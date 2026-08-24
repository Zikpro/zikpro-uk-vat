"""VAT Adjustment — a period-scoped correction that lands in one VAT return's boxes.

Covers the manual adjustment types UK VAT requires (error correction Notice 700/45,
partial exemption, bad-debt relief, capital goods scheme, fuel scale charge). On
submit it writes an `Adjusted` event to the VAT Ledger and the return figures for
its period fold it in (computed boxes + Σ adjustments = final). Submittable so it is
an auditable, amendable record; the preparer/approver split mirrors the return SoD.

See [[project-uk-mtd-vat-ledger-design]].
"""

import frappe
from frappe.model.document import Document

# Boxes 3 and 5 are DERIVED (box3 = box1+box2, box5 = |box3-box4|) — never adjusted
# directly. The Select options already exclude them; this is the server-side guard.
_DERIVED_BOXES = ("Box 3", "Box 5")


class VATAdjustment(Document):
	def validate(self):
		if self.vat_box in _DERIVED_BOXES:
			frappe.throw(frappe._("Box 3 and Box 5 are calculated automatically — adjust Box 1, 2, 4, 6 or 7 instead."))
		if not self.amount:
			frappe.throw(frappe._("Adjustment amount cannot be zero."))

	def on_submit(self):
		from zikpro_uk_vat import vat_ledger
		vat_ledger.record_adjustment(self)

	def on_cancel(self):
		from zikpro_uk_vat import vat_ledger
		vat_ledger.clear_source_entries(self)
