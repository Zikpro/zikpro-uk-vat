"""VAT Return report — native Frappe report surface (export/print/summary cards).

The box math is NOT reimplemented here: it delegates to the ONE correct
implementation, cockpit.get_return_figures (credit notes included, VAT-only
extraction, Box 5 absolute, scheme-aware accrual/cash). This keeps a single
source of truth for the 9 boxes and gives users an exportable/printable view.
"""

import frappe

from zikpro_uk_vat import cockpit


def execute(filters=None):
	filters = filters or {}
	if not filters.get("vat_return"):
		frappe.throw("Please select a VAT Return")

	doc = frappe.get_doc("UK MTD VAT Return", filters["vat_return"])
	if not doc.period_start_date or not doc.period_end_date:
		frappe.throw("Please set Period Start and End Dates in the VAT Return")

	fig = cockpit.get_return_figures(str(doc.period_start_date), str(doc.period_end_date))
	boxes = fig["boxes"]

	columns = [
		{"label": "Type", "fieldname": "invoice_type", "fieldtype": "Data", "width": 110},
		{"label": "Invoice", "fieldname": "invoice", "fieldtype": "Dynamic Link", "options": "invoice_type", "width": 170},
		{"label": "Party", "fieldname": "party", "fieldtype": "Data", "width": 170},
		{"label": "Date", "fieldname": "posting_date", "fieldtype": "Date", "width": 110},
		{"label": "Net (ex VAT)", "fieldname": "net", "fieldtype": "Currency", "width": 130},
		{"label": "VAT", "fieldname": "vat", "fieldtype": "Currency", "width": 120},
	]

	data = []
	for src in (fig["sales"], fig["purchases"]):
		for t in src:
			data.append(
				{
					"invoice_type": t["doctype"],
					"invoice": t["name"],
					"party": t["party"],
					"posting_date": t["date"],
					"net": t["net"],
					"vat": t["vat"],
				}
			)

	basis = "Cash accounting" if fig.get("basis") == "cash" else "Standard (accrual)"
	message = f"Basis: {basis}. Boxes 2, 8 and 9 apply to Northern Ireland trade only."
	return columns, data, message, None, get_summary(boxes)


# Official VAT Notice 700/12 labels. Boxes 2/8/9 are Northern Ireland only (from 1 Jan 2021).
_BOX_LABELS = {
	"box1": "Box 1 — VAT due on sales and other outputs",
	"box2": "Box 2 — VAT due on acquisitions (NI from EU)",
	"box3": "Box 3 — Total VAT due",
	"box4": "Box 4 — VAT reclaimed on purchases",
	"box5": "Box 5 — Net VAT to pay or reclaim",
	"box6": "Box 6 — Total value of sales ex VAT",
	"box7": "Box 7 — Total value of purchases ex VAT",
	"box8": "Box 8 — Dispatches from NI to EU ex VAT",
	"box9": "Box 9 — Acquisitions in NI from EU ex VAT",
}


def get_summary(boxes):
	"""9-box summary cards, sourced from the one correct calculation."""
	return [
		{"label": _BOX_LABELS[k], "value": boxes[k], "indicator": "blue", "datatype": "Currency"}
		for k in ("box1", "box2", "box3", "box4", "box5", "box6", "box7", "box8", "box9")
	]
