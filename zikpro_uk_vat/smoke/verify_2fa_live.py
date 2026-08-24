"""Live 2FA login scaffold (LOCAL served site). setup_live configures a test user with a 2FA
role + pre-seeded TOTP secret (skips first-login email); a browser then logs in through 2FA with
a pyotp code; check_wrote confirms the REAL 2FA login wrote the User MFA Timestamp.

    bench --site erpnext.zikpro.local execute zikpro_uk_vat.smoke.verify_2fa_live.setup_live
    ... browser login ...
    bench --site erpnext.zikpro.local execute zikpro_uk_vat.smoke.verify_2fa_live.check_wrote
    bench --site erpnext.zikpro.local execute zikpro_uk_vat.smoke.verify_2fa_live.teardown_live
"""
import frappe
from frappe import twofactor
from frappe.utils.password import update_password

ROLE = "ZZ 2FA Live"
USER = "zz_2fa_live@example.com"
PWD = "Zz2fa-live-2026!"


def _del():
	if frappe.db.exists("User", USER):
		frappe.delete_doc("User", USER, force=True, ignore_permissions=True)
	if frappe.db.exists("Role", ROLE):
		frappe.delete_doc("Role", ROLE, force=True, ignore_permissions=True)
	for n in frappe.get_all("User MFA Timestamp", filters={"user": USER}, pluck="name"):
		frappe.delete_doc("User MFA Timestamp", n, force=True, ignore_permissions=True)
	frappe.db.commit()


def setup_live():
	_del()
	frappe.db.set_single_value("System Settings", "enable_two_factor_auth", 1)
	frappe.db.set_single_value("System Settings", "two_factor_method", "OTP App")
	frappe.get_doc({"doctype": "Role", "role_name": ROLE, "two_factor_auth": 1}).insert(ignore_permissions=True)
	frappe.get_doc({"doctype": "User", "email": USER, "first_name": "2FALive", "send_welcome_email": 0,
					"roles": [{"role": ROLE}]}).insert(ignore_permissions=True)
	update_password(USER, PWD)
	secret = twofactor.get_otpsecret_for_(USER)          # base32 TOTP seed (test user, ephemeral)
	twofactor.set_default(USER + "_otplogin", 1)                    # go straight to authenticator, skip setup email
	frappe.db.commit()
	print(f"SETUP user={USER} pwd={PWD} secret={secret}", flush=True)
	return {"user": USER, "password": PWD, "secret": secret}


def check_wrote():
	ts = frappe.db.get_value("User MFA Timestamp", {"user": USER}, "last_login")
	enabled = twofactor.two_factor_is_enabled(user=USER)
	print(f"CHECK mfa_timestamp={ts} two_factor_active={enabled}", flush=True)
	return {"timestamp": str(ts), "active": bool(enabled)}


def teardown_live():
	frappe.db.set_single_value("System Settings", "enable_two_factor_auth", 0)
	_del()
	print("TEARDOWN done", flush=True)
	return {"ok": True}
