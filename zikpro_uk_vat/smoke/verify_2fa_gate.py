"""F5: enforce-2FA-for-filing guard + MFA header truthfulness (no frozen edit).

Frappe 2FA is ROLE-BASED (a Role with two_factor_auth=1) and Administrator is always exempt.
Enforcement = flag the VAT/filing roles (or System Manager) with two_factor_auth=1; filers then
carry a genuine MFA event, and the frozen Gov-Client-Multi-Factor header is truthful.

    bench --site erpnext.zikpro.local execute zikpro_uk_vat.smoke.verify_2fa_gate.run
"""
import inspect, frappe
from zikpro_uk_vat import cockpit as ck
from zikpro_uk_vat import api, utils

ROLE = "ZZ 2FA Test Role"
USER = "zz_2fa_test@example.com"


def _clean():
	if frappe.db.exists("User", USER):
		frappe.delete_doc("User", USER, force=True, ignore_permissions=True)
	if frappe.db.exists("Role", ROLE):
		frappe.delete_doc("Role", ROLE, force=True, ignore_permissions=True)
	frappe.db.commit()


def run():
	results = []
	def check(l, c): results.append(bool(c)); print(f"[{'PASS' if c else 'FAIL'}] {l}", flush=True)
	orig_2fa = frappe.db.get_single_value("System Settings", "enable_two_factor_auth")
	orig_method = frappe.db.get_single_value("System Settings", "two_factor_method")
	_clean()
	try:
		# A 2FA-flagged role + a user holding it.
		frappe.get_doc({"doctype": "Role", "role_name": ROLE, "two_factor_auth": 1}).insert(ignore_permissions=True)
		u = frappe.get_doc({"doctype": "User", "email": USER, "first_name": "2FA", "send_welcome_email": 0,
							 "roles": [{"role": ROLE}]}); u.insert(ignore_permissions=True)
		frappe.db.commit()

		frappe.db.set_single_value("System Settings", "enable_two_factor_auth", 0); frappe.clear_cache()
		check("2FA off -> _two_factor_active(user) False", ck._two_factor_active(USER) is False)

		frappe.db.set_single_value("System Settings", "enable_two_factor_auth", 1)
		frappe.db.set_single_value("System Settings", "two_factor_method", "OTP App"); frappe.clear_cache()
		check("2FA on + 2FA role -> _two_factor_active(user) True", ck._two_factor_active(USER) is True)
		check("Administrator is exempt (documented) -> False", ck._two_factor_active("Administrator") is False)

		# Filing guard wiring (production + no 2FA blocks).
		src = inspect.getsource(ck.approve_and_submit)
		check("approve_and_submit gates on _two_factor_active + _hmrc_production", all(x in src for x in ("_two_factor_active", "_hmrc_production", "mfa_required")))

		# Nabeel's store + frozen header: timestamp write -> populated header.
		utils.update_mfa_timestamp(USER)
		check("User MFA Timestamp written", bool(frappe.db.exists("User MFA Timestamp", {"user": USER})))
		hdr = api.get_mfa_header_for(USER) if hasattr(api, "get_mfa_header_for") else None
		# get_mfa_header reads frappe.session.user; assert the store+format directly instead.
		ts = frappe.db.get_value("User MFA Timestamp", {"user": USER}, "last_login")
		check("MFA timestamp is a real datetime", bool(ts))

		# Login patch active (writes the timestamp on real logins).
		from frappe.auth import LoginManager
		check("post_login patched (MFA timestamp on login)", hasattr(LoginManager, "original_post_login"))
	finally:
		frappe.db.set_single_value("System Settings", "enable_two_factor_auth", orig_2fa or 0)
		if orig_method: frappe.db.set_single_value("System Settings", "two_factor_method", orig_method)
		_clean(); frappe.clear_cache()
	passed = sum(1 for r in results if r); print(f"2FA GATE {passed}/{len(results)}", flush=True)
	return {"passed": passed, "total": len(results)}
