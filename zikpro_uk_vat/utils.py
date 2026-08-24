import frappe
import time
from datetime import datetime
from frappe.utils import now_datetime,add_to_date,get_traceback
from frappe.twofactor import confirm_otp_token as original_confirm_otp_token
from frappe import publish_realtime

CLIENT_INFO_CACHE = "uk_vat_client_info"


def _store_client_info(ci):
    """Persist client_info for the current user. frappe.session.data does NOT reliably
    persist across requests on Frappe Cloud, so the LATER submit request would never see
    it — we keep a per-user copy in the cache and re-hydrate it into session.data at the
    start of every request (see hydrate_client_info / before_request). Without this the
    frozen get_fraud_prevention_headers always reads empty client_info -> 1920x1080."""
    frappe.session.data['client_info'] = ci
    user = getattr(frappe.session, "user", None)
    if user and user != "Guest":
        frappe.cache().hset(CLIENT_INFO_CACHE, user, frappe.as_json(ci))


def hydrate_client_info(*args, **kwargs):
    """before_request hook: load the user's stored client_info into session.data BEFORE
    the request is processed, so the frozen get_fraud_prevention_headers() (which reads
    frappe.session.data['client_info']) sees the real device values during a submit."""
    try:
        user = getattr(getattr(frappe.local, "session", None), "user", None)
        if not user or user == "Guest" or frappe.session.data.get('client_info'):
            return
        raw = frappe.cache().hget(CLIENT_INFO_CACHE, user)
        if raw:
            frappe.session.data['client_info'] = frappe.parse_json(raw)
    except Exception:
        pass


@frappe.whitelist(allow_guest=False)
def update_client_info(screen_width, screen_height, color_depth, pixel_ratio, timezone_offset,
                       window_width=None, window_height=None):
    """Store the browser's REAL client info in the session for the HMRC fraud-prevention
    headers (FPH-1). CRITICAL: the frozen api.py get_fraud_prevention_headers() reads
    Gov-Client-Screens from client_info['screen_width'/'screen_height'/'pixel_ratio'/
    'color_depth'] and Gov-Client-Window-Size from ['width'/'height']. This setter MUST
    store under those exact keys — the previous version filed the real values under
    'width'/'height'/'scaling', so the header never found screen_width and always sent
    the 1920x1080 fallback (an inaccurate FPH header HMRC penalises). See CTO review A0-1.
    """
    try:
        tz_offset = float(timezone_offset)
        sw, sh = max(1, int(screen_width)), max(1, int(screen_height))
        ww = max(1, int(window_width)) if window_width else sw
        wh = max(1, int(window_height)) if window_height else sh
        ratio = max(0.5, float(pixel_ratio))
        ratio = int(ratio) if ratio == int(ratio) else round(ratio, 2)  # 2.0 -> 2, keep 1.5
        depth = max(8, int(color_depth))
        _store_client_info({
            # Gov-Client-Screens (physical screen) — exact keys the frozen header reads:
            'screen_width': sw, 'screen_height': sh, 'pixel_ratio': ratio, 'color_depth': depth,
            # Gov-Client-Window-Size (browser window):
            'width': ww, 'height': wh,
            'scaling': ratio,  # legacy alias
            'timezone_offset': tz_offset,
            'timezone_offset_rounded': round(tz_offset),
            'last_updated': datetime.now().isoformat(),
        })
        return {"success": True}
    except Exception as e:
        frappe.log_error("Client Info Update Failed", str(e))
        return {"success": False}

# def on_login_handler(login_manager):
#     """Handles login-related tasks: set default client info + store last 2FA login."""
#     try:
#         # 1️⃣ Set default client info
#         set_default_client_info()

#         frappe.log_error("DEBUG", f"on_login_handler executed for {frappe.session.user}")

#     except Exception:
#         frappe.log_error("on_login_handler Error", frappe.get_traceback())

def set_default_client_info(doc, method):
    """Fallback client info on login until the browser reports real values. Uses the
    same keys the frozen header reads, with deliberately off-by-one values (1921x1081,
    depth 25) so a fallback is DETECTABLE in the Gov-Client-Screens header rather than
    indistinguishable from a real 1920x1080 device."""
    if not frappe.session.data.get('client_info') and not frappe.cache().hget(CLIENT_INFO_CACHE, frappe.session.user):
        _store_client_info({
            'screen_width': 1921, 'screen_height': 1081, 'pixel_ratio': 1, 'color_depth': 25,
            'width': 1921, 'height': 1081, 'scaling': 1,
            'timezone_offset': -time.timezone // 3600,
            'is_fallback': True,
        })

def get_client_info():
    """Retrieve stored client information"""
    return frappe.session.data.get('client_info', {})

def log_mfa_error(user, title, error_message):
    try:
        frappe.get_doc({
            "doctype": "Error Log",
            "title": title,
            "error": error_message,
            "method": "MFA Update",
            "reference_doctype": "User MFA Timestamp",
            "reference_name": user or "Unknown"
        }).insert(ignore_permissions=True)
        # frappe.db.commit() - v16 handles transaction automatically
    except Exception as e:
        frappe.logger().error(f"Failed to log MFA error: {e} | Original Error: {error_message}")

def update_mfa_timestamp(user):
    try:
        if not user or user == "Guest":
            return

        timestamp = now_datetime()

        exists = frappe.db.exists("User MFA Timestamp", {"user": user})

        if exists:
            frappe.db.sql("""
                UPDATE `tabUser MFA Timestamp`
                SET last_login = %s, modified = %s
                WHERE user = %s
            """, (timestamp, timestamp, user))
        else:
            frappe.db.sql("""
                INSERT INTO `tabUser MFA Timestamp`
                (name, user, last_login, creation, modified)
                VALUES (%s, %s, %s, %s, %s)
            """, (frappe.generate_hash(10), user, timestamp, timestamp, timestamp))

        # frappe.db.commit() - v16 handles transaction automatically
        frappe.clear_cache(doctype="User MFA Timestamp")
        frappe.clear_cache(user=user)

        frappe.publish_realtime('mfa_updated', {'user': user, 'timestamp': timestamp})

    except Exception:
        msg = f"User: {user}\nError: {frappe.get_traceback()}\nSQL: {frappe.db.last_query}"
        frappe.log_error("MFA Update Failed", msg)
        log_mfa_error(user, "MFA Update Failed", msg)

def custom_post_login(login_manager,*args, **kwargs):
    try:
        # Call original method
        from frappe.auth import LoginManager
        LoginManager.original_post_login(login_manager,*args, **kwargs)

        # Always update MFA timestamp after successful login
        update_mfa_timestamp(login_manager.user)

        frappe.log_error("DEBUG", f"✅ MFA timestamp updated for {login_manager.user}")

    except Exception:
        msg = f"Failed MFA update in post_login:\n{frappe.get_traceback()}"
        frappe.log_error("MFA Post Login Error", msg)
        log_mfa_error(login_manager.user, "MFA Post Login Error", msg)


def patch_login_manager():
    try:
        from frappe.auth import LoginManager
        if not hasattr(LoginManager, "original_post_login"):
            LoginManager.original_post_login = LoginManager.post_login
            LoginManager.post_login = custom_post_login
            print("✅ Patched LoginManager.post_login")
        else:
            print("⚠️ LoginManager already patched")
    except Exception:
        print("❌ Error patching LoginManager:", frappe.get_traceback())


# def patched_confirm_otp_token(login_manager):
#     try:
#         result = frappe.twofactor._original_confirm_otp_token(login_manager)

#         if result and login_manager.user:
#             user = login_manager.user
#             update_mfa_timestamp(user)

#             frappe.enqueue(
#                 "zikpro_uk_vat.utils.verify_mfa_update",
#                 user=user,
#                 enqueue_after_commit=True,
#                 now=True,
#                 at_front=True
#             )

#         return result

#     except Exception:
#         error_msg = get_traceback()
#         frappe.log_error("OTP Patch Error", error_msg)
#         log_mfa_error(login_manager.user if login_manager else "Unknown", "OTP Patch Error", error_msg)  # ✅ Always log
#         raise


# def verify_mfa_update(user):
#     try:
#         frappe.log_error("DEBUG", f"verify_mfa_update executed for user {user}")
#         timestamp = now_datetime()
#         current = frappe.get_value("User MFA Timestamp", {"user": user}, "last_login")

#         if not current or current < add_to_date(None, minutes=-1):
#             update_mfa_timestamp(user)

#         frappe.db.commit()
#         frappe.clear_cache(doctype="User MFA Timestamp")
#         frappe.clear_cache(user=user)

#         publish_realtime('mfa_updated', {'user': user, 'timestamp': timestamp})

#     except Exception:
#         error_msg = f"User: {user}\nError: {get_traceback()}\nSQL: {frappe.db.last_query}"
#         frappe.log_error(title="MFA Verify Update Failed", message=error_msg, reference_doctype="User MFA Timestamp")
#         log_mfa_error(user, "MFA Verify Update Failed", error_msg)
#         raise


# def patch_twofactor():
#     from frappe import twofactor
#     frappe.log_error("DEBUG", "patch_twofactor called!")
#     if not hasattr(twofactor, "_original_confirm_otp_token"):
#         frappe.log_error("DEBUG", "patch_twofactor applied successfully!")
#         twofactor._original_confirm_otp_token = twofactor.confirm_otp_token
#         twofactor.confirm_otp_token = patched_confirm_otp_token
#     else:
#         frappe.log_error("DEBUG", "patch_twofactor already applied!")

# def patch_twofactor():
#     try:
#         from frappe import twofactor

#         if not hasattr(twofactor, "_original_confirm_otp_token"):
#             twofactor._original_confirm_otp_token = twofactor.confirm_otp_token
#             twofactor.confirm_otp_token = twofactor._original_confirm_otp_token
#             print("✅ MFA Patch Applied")
#         else:
#             print("⚠️ MFA Patch Already Applied")

#     except Exception:
#         print("❌ MFA Patch Error:", get_traceback())

# def create_initial_records():
#     users = frappe.get_all("User", filters={"enabled": 1}, pluck="name")
#     for user in users:
#         update_mfa_timestamp(user)

def create_initial_records():
    """Create MFA timestamp records only for users that don't already exist."""
    try:
        # Get all enabled users
        users = frappe.get_all("User", filters={"enabled": 1}, pluck="name")

        for user in users:
            exists = frappe.db.exists("User MFA Timestamp", {"user": user})
            if not exists:  # Only create if record does not exist
                update_mfa_timestamp(user)

        # frappe.db.commit() - v16 handles transaction automatically

    except Exception:
        error_msg = f"Error creating initial MFA records:\n{frappe.get_traceback()}"
        frappe.log_error(title="MFA Initial Record Creation Failed", message=error_msg)
        log_mfa_error("SYSTEM", "MFA Initial Record Creation Failed", error_msg)


def clear_user_cache(data):
    frappe.clear_cache(doctype="User MFA Timestamp")
    frappe.clear_cache(user=data["user"])
    # frappe.db.commit() - v16 handles transaction automatically



# def on_login_handler(login_manager):
#     """Handles login-related tasks: store client info + MFA timestamp."""
#     try:
#         # ✅ 1) Set default client info
#         set_default_client_info()

#     except Exception:
#         frappe.log_error("on_login_handler Error", frappe.get_traceback())

# def on_login_handler(login_manager):
#     try:
#         # Existing logic
#         set_default_client_info()

#         # Ensure patch is applied (safe call)
#         patch_twofactor()

#         # ✅ NEW: Update MFA timestamp if MFA is enabled
#         is_mfa_enabled = frappe.db.get_value("User", login_manager.user, "mfa_enabled")
#         frappe.log_error("DEBUG", f"Login for {login_manager.user}, MFA Enabled={is_mfa_enabled}")

#         if is_mfa_enabled:
#             update_mfa_timestamp(login_manager.user)

#     except Exception:
#         frappe.log_error("on_login_handler Error", get_traceback())


# def clear_cache_handler(data):
#     """Forces cache clearance across workers"""
#     frappe.clear_cache(doctype="User MFA Timestamp")
#     frappe.db.commit()

# @frappe.whitelist(allow_guest=True)
# def custom_login():
#     from frappe.auth import LoginManager

#     login_manager = LoginManager()
#     login_manager.authenticate()
#     login_manager.post_login()

#     frappe.log_error("DEBUG", f"Custom Login executed for {login_manager.user}")

#     if login_manager.user and frappe.db.get_value("User", login_manager.user, "mfa_enabled"):
#         frappe.log_error("DEBUG", f"✅ MFA Enabled for {login_manager.user}, updating timestamp")
#         update_mfa_timestamp(login_manager.user)
#     else:
#         frappe.log_error("DEBUG", f"❌ MFA Disabled for {login_manager.user}, skipping update")

#     frappe.local.response["message"] = "Logged In"

