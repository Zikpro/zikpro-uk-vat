"""Base app smoke — the app installs and its module is registered. Grows into the
real Basic proofs (accrual/cash/flat-rate figures, connect, file) as code is extracted.
"""

import frappe


def prove_installed():
	res = {}
	res["app_installed"] = "zikpro_uk_vat" in frappe.get_installed_apps()
	res["module_registered"] = frappe.db.exists("Module Def", "UK VAT") is not None
	return res
