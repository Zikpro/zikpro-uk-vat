"""P3-4: UK MTD VAT Return doctype hygiene.

A filed VAT return is an immutable legal record submitted to HMRC. Guard its
doctype-level invariants so a future edit can't quietly weaken them:
  - track_changes on (a versioned audit trail of every field change),
  - NOT renameable (a filed return's id must not move under it),
  - submittable with an amended_from lifecycle,
  - a proper naming series (no Prompt/hash reuse).
"""

import frappe

DT = "UK MTD VAT Return"


def prove_return_hygiene():
	m = frappe.get_meta(DT)
	return {
		"track_changes_on": bool(m.track_changes),
		"not_renameable": not m.allow_rename,
		"is_submittable": bool(m.is_submittable),
		"has_amended_from": bool(m.get_field("amended_from")),
		"autoname_naming_series": (m.autoname or "").startswith("naming_series"),
	}


def prove_return_report_ptype():
	"""The standard 'VAT Return' report needs the `report` ptype on its ref_doctype
	(UK MTD VAT Return): frappe.desk.query_report.get_script checks
	has_permission(ref_doctype, 'report'), so without it the report 403s for every role.
	Guards the fix where the all-zeros DocPerm baseline had omitted it."""
	res = {}
	for role in ("System Manager", "UK VAT Preparer", "UK VAT Approver"):
		name = frappe.db.get_value("Custom DocPerm", {"parent": DT, "permlevel": 0, "role": role})
		res[f"{role}: report ptype granted"] = bool(name) and int(frappe.db.get_value("Custom DocPerm", name, "report") or 0) == 1
	return res
