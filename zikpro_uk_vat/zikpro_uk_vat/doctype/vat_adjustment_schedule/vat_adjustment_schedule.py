"""VAT Adjustment Schedule — a forward plan that GENERATES VAT Adjustments.

Models the recurring/time-triggered UK adjustments (bad-debt relief 6-month
trigger, capital goods scheme intervals, partial-exemption annual true-up) as
ERPNext's Asset Depreciation Schedule does: a submitted plan whose entries are
materialised into actual VAT Adjustments when their trigger date arrives (see
zikpro_uk_vat.vat_adjustment_schedule.generate_due_adjustments, run daily).

See [[project-uk-mtd-vat-ledger-design]].
"""

import frappe
from frappe.model.document import Document

_DERIVED_BOXES = ("Box 3", "Box 5")


class VATAdjustmentSchedule(Document):
	def validate(self):
		if self.vat_box in _DERIVED_BOXES:
			frappe.throw(frappe._("Box 3 and Box 5 are calculated automatically — schedule Box 1, 2, 4, 6 or 7."))
		if not self.amount:
			frappe.throw(frappe._("Amount cannot be zero."))

	def on_cancel(self):
		# Cancelling the plan reverses the adjustment it generated (which unwinds its
		# effect on the boxes + clears its ledger event).
		if self.generated_adjustment and frappe.db.exists("VAT Adjustment", self.generated_adjustment):
			adj = frappe.get_doc("VAT Adjustment", self.generated_adjustment)
			if adj.docstatus == 1:
				adj.cancel()
