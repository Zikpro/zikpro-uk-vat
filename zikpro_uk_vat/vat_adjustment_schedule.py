"""Generation engine for VAT Adjustment Schedules.

Runs daily (scheduler_events) — and on demand — to materialise due schedule entries
into real VAT Adjustments, which then flow through the existing adjustment engine
(Adjusted ledger event + fold into the period's 9 boxes). Idempotent: one adjustment
per schedule, status-gated.

Bad-debt relief is the fully-modelled type (eligibility = the referenced sale is
still unpaid at the trigger date). Capital Goods Scheme and Partial Exemption Annual
share the same trigger-date generation; their type-specific eligibility/interval
logic plugs into `_eligible`.
"""

import frappe
from frappe.utils import flt, nowdate

SCHEDULE = "VAT Adjustment Schedule"
ADJUSTMENT = "VAT Adjustment"
SALES_INVOICE = "Sales Invoice"
BAD_DEBT_RELIEF = "Bad Debt Relief"

# Schedule type -> VAT Adjustment.adjustment_type (the Select on the adjustment doc).
_TYPE_MAP = {
	BAD_DEBT_RELIEF: BAD_DEBT_RELIEF,
	"Capital Goods Scheme": "Capital Goods Scheme",
	"Partial Exemption Annual": "Partial Exemption",
}


def _bad_debt_still_eligible(sched):
	"""Bad-debt relief requires the referenced sale to be STILL UNPAID at trigger.
	If it has since been paid, the relief no longer applies (and any prior relief
	would be repaid — a separate reversal, not handled here)."""
	if sched.reference_doctype != SALES_INVOICE or not sched.reference_name:
		return True  # nothing to check (manual schedule) — allow
	if not frappe.db.exists(SALES_INVOICE, sched.reference_name):
		return False
	outstanding = frappe.db.get_value(SALES_INVOICE, sched.reference_name, "outstanding_amount")
	return flt(outstanding) > 0


def _eligible(sched):
	if sched.schedule_type == BAD_DEBT_RELIEF:
		return _bad_debt_still_eligible(sched)
	return True  # CGS / PE annual: trigger-date driven, no extra gate yet


def _create_adjustment(sched, as_of):
	adj = frappe.new_doc(ADJUSTMENT)
	adj.company = sched.company
	adj.posting_date = as_of
	adj.adjustment_type = _TYPE_MAP.get(sched.schedule_type, "Other")
	adj.vat_box = sched.vat_box
	adj.amount = sched.amount
	adj.reason = sched.reason or f"Generated from {sched.name}"
	adj.notice_ref = sched.notice_ref
	adj.origin_doctype = sched.reference_doctype
	adj.origin_name = sched.reference_name
	adj.insert(ignore_permissions=True)
	adj.flags.ignore_permissions = True
	adj.submit()
	return adj.name


def reverse_bad_debt_on_recovery(payment_doc, method=None):
	"""When a payment fully clears a sale that had bad-debt relief CLAIMED, repay the
	reclaimed VAT (HMRC Notice 700/18): generate a reversing VAT Adjustment (opposite
	sign, same box, in the recovery period) and mark the schedule Reversed. Hooked on
	Payment Entry on_submit. v1 handles FULL recovery (outstanding -> 0); proportional
	partial recovery is a follow-up."""
	if payment_doc.doctype != "Payment Entry" or not frappe.db.exists("DocType", SCHEDULE):
		return
	for ref in payment_doc.get("references") or []:
		if ref.get("reference_doctype") != SALES_INVOICE or not ref.get("reference_name"):
			continue
		inv = ref.reference_name
		if flt(frappe.db.get_value(SALES_INVOICE, inv, "outstanding_amount")) > 0:
			continue  # not fully recovered yet
		for s in frappe.get_all(
			SCHEDULE,
			filters={"docstatus": 1, "status": "Claimed", "schedule_type": BAD_DEBT_RELIEF, "reference_name": inv},
			fields=["name", "vat_box", "amount", "company"],
		):
			adj = frappe.new_doc(ADJUSTMENT)
			adj.company = s.company
			adj.posting_date = nowdate()
			adj.adjustment_type = BAD_DEBT_RELIEF
			adj.vat_box = s.vat_box
			adj.amount = -flt(s.amount)  # repay the relief taken
			adj.reason = f"Bad-debt relief repaid on recovery of {inv} (schedule {s.name})"
			adj.notice_ref = "Notice 700/18"
			adj.origin_doctype = SALES_INVOICE
			adj.origin_name = inv
			adj.insert(ignore_permissions=True)
			adj.flags.ignore_permissions = True
			adj.submit()
			frappe.db.set_value(SCHEDULE, s.name, "status", "Reversed", update_modified=False)
	frappe.db.commit()


# Capital Goods Scheme engine (_cgs_interval_adjustment / generate_cgs_schedule /
# set_cgs_interval_use) is PREMIUM — it lives in the Pro add-on (zikpro_uk_vat_pro.pro_engines)
# and is absent from this free base tree. Base's cockpit CGS methods delegate to it via _pro().


def generate_due_adjustments(as_of=None):
	"""Materialise every Pending, submitted schedule whose trigger_date has arrived
	and which is still eligible. Idempotent (status-gated: a schedule generates once).
	Safe to run daily. Returns {generated, not_eligible}."""
	as_of = as_of or nowdate()
	counts = {"generated": 0, "not_eligible": 0}
	if not frappe.db.exists("DocType", SCHEDULE):
		return counts
	due = frappe.get_all(
		SCHEDULE,
		filters={"docstatus": 1, "status": "Pending", "trigger_date": ["<=", as_of]},
		pluck="name",
	)
	for name in due:
		sched = frappe.get_doc(SCHEDULE, name)
		if not _eligible(sched):
			sched.db_set("status", "Not Eligible", update_modified=False)
			counts["not_eligible"] += 1
			continue
		adj_name = _create_adjustment(sched, as_of)
		sched.db_set("generated_adjustment", adj_name, update_modified=False)
		sched.db_set("generated_on", as_of, update_modified=False)
		sched.db_set("status", "Claimed", update_modified=False)
		counts["generated"] += 1
	frappe.db.commit()
	frappe.logger().info(f"VAT adjustment schedule generation ({as_of}): {counts}")
	return counts
