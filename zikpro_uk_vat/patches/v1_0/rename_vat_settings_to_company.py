"""Rename existing VAT Settings records from their hash name to their company.

VAT Settings shipped with no `autoname`, so every record got a random hash name
(seen live: ziktax `vub6ivdqno`, test101 `316j6euvju`). This is a multi-company
app — an accountant with several companies then sees several settings all named
like `vub6ivdqno`, unable to tell which belongs to which. Worse, nothing enforced
one-settings-per-company, so a stray duplicate would leave
`get_value("VAT Settings", {"company": ...})` returning an arbitrary one — an
undefined "which settings files this company's return" hazard.

The doctype now autonames `field:company`, which fixes both going forward (legible
names + name-uniqueness = one settings per company). This patch brings EXISTING
records in line: it renames each hash-named record to its company. All lookups are
by the `company` filter and no Link field anywhere targets VAT Settings, so the
rename is safe. Idempotent; runs on Frappe Cloud too (B101: after_migrate doesn't,
patches do). If a company somehow already has two settings, the first keeps the
company name and the rest are left untouched and logged rather than clobbered.
"""

import frappe


def execute():
	if not frappe.db.exists("DocType", "VAT Settings"):
		return
	seen = set()
	for name, company in frappe.get_all(
		"VAT Settings", fields=["name", "company"], as_list=True
	):
		if not company or name == company:
			continue  # already named by company, or no company to name it by
		if company in seen or frappe.db.exists("VAT Settings", company):
			# A record already holds this company's name — don't collide. Leave the
			# duplicate as-is (its hash name) so an admin can reconcile it by hand.
			frappe.log_error(
				f"VAT Settings {name}: company {company!r} already named; left as hash",
				"uk_vat_rename_settings",
			)
			continue
		try:
			frappe.rename_doc("VAT Settings", name, company, force=True, show_alert=False)
			seen.add(company)
		except Exception:
			frappe.log_error(
				f"VAT Settings rename {name} -> {company} failed", "uk_vat_rename_settings"
			)
	frappe.db.commit()
