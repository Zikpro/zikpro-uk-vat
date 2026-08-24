"""HMRC OAuth redirect landing (sandbox Connect flow).

Registered redirect URI is http://localhost:8002/oauth-callback, so this www page
handles the round-trip without needing a new URI registered. It exchanges the code
for tokens (via cockpit.complete_oauth) and bounces back to the cockpit. The frozen
production api.py callback is left untouched.
"""

import frappe

from zikpro_uk_vat import cockpit

no_cache = 1


def _back(status):
	frappe.local.flags.redirect_location = f"/app/vat-cockpit?connect={status}"
	raise frappe.Redirect


def get_context(context):
	# HMRC returns ?error=access_denied when the user declines authorisation.
	if frappe.form_dict.get("error"):
		_back("denied")

	# Direct mode returns ?code&state; broker mode returns ?broker_code&nonce.
	_settings, err = cockpit.complete_oauth(
		frappe.form_dict.get("code"), frappe.form_dict.get("state"),
		broker_code=frappe.form_dict.get("broker_code"), nonce=frappe.form_dict.get("nonce"))
	_back("error" if err else "success")
