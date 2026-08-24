import frappe


@frappe.whitelist()
def check_app_permission():
	"""Whether to show the UK VAT app on the desk Apps launcher.

	Shown to any logged-in user. (The desk launcher filters apps by their
	`has_permission`; apps without one are not rendered — which is why the
	UK VAT icon was missing after v16 dropped this function.)
	"""
	return frappe.session.user and frappe.session.user != "Guest"
