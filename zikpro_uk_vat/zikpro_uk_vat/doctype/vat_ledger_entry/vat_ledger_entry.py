"""VAT Ledger Entry — the immutable per-event VAT sub-ledger.

One row per VAT event (Accrued on invoice submit, Realised on payment allocation
under cash basis, Filed at return submission, Adjusted/Bad Debt Relief/Repaid).
This is the single system-of-record that unifies per-invoice VAT activity, period
roll-over adjustments and multi-period reconciliation reports — modelled on
ERPNext's GL Entry / Payment Ledger Entry (a separate ledger the source document
displays, never an editable child table on the submittable invoice).

Append-only: values never change after creation. The only sanctioned removal is by
the system when a source document is cancelled/amended, which sets
flags.ignore_immutable so the stale events can be cleared and re-derived.
"""

import frappe
from frappe.model.document import Document


class VATLedgerEntry(Document):
	def validate(self):
		# Immutable values: once written, an entry is never edited. A correction is a
		# NEW entry (Adjusted/Repaid), never an in-place change — that is what keeps
		# the audit trail and the double-count guard honest.
		if not self.is_new() and not self.flags.ignore_immutable:
			frappe.throw(
				frappe._("VAT Ledger Entry is immutable — post a correcting entry instead of editing."),
				frappe.PermissionError,
			)

	def on_trash(self):
		# Deletion is only allowed to the system when it is re-deriving a source
		# document's events (cancel/amend). A user cannot delete history.
		if not self.flags.ignore_immutable:
			frappe.throw(
				frappe._("VAT Ledger Entry cannot be deleted — it is an audit record."),
				frappe.PermissionError,
			)
