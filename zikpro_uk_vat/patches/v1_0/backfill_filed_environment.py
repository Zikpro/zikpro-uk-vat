"""Backfill `filed_environment` on already-filed returns (BS2 / #19).

The environment tag is stamped going forward at filing (_apply_receipt). Returns
filed BEFORE the field existed have no tag, and their environment is not reliably
knowable after the fact — a sandbox test filing and a live one look the same in
our record. Mark those 'Unknown' rather than guessing, so the History filter
shows them honestly instead of mislabelling a test return as live (or vice versa).

Ships in the same commit as the field (standing rule: a new field's backfill lands
with the field). Idempotent: only fills a blank tag on a return that actually
carries a form bundle number (i.e. was filed).
"""

import frappe


def execute():
	if not frappe.db.has_column("UK MTD VAT Return", "filed_environment"):
		return
	rows = frappe.get_all(
		"UK MTD VAT Return",
		filters={
			"form_bundle_number": ["is", "set"],
			"filed_environment": ["in", [None, ""]],
		},
		pluck="name",
	)
	for name in rows:
		frappe.db.set_value("UK MTD VAT Return", name, "filed_environment", "Unknown", update_modified=False)
	if rows:
		frappe.db.commit()
