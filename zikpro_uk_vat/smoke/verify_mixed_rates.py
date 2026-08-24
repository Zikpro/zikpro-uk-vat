"""Hand-checked verification of mixed-rate / exempt / partial-payment VAT.

    bench --site <site> execute zikpro_uk_vat.smoke.verify_mixed_rates.run

Builds a deliberately awful invoice — standard 20%, reduced 5%, zero-rated and
exempt lines on ONE invoice — then part-pays it, and compares Boxes 1/6 against
figures computed by hand (VAT Notice 731: part payments apportion proportionally;
Box 6 includes zero-rated and exempt supplies). Re-run after touching the VAT
extraction or cash-basis apportionment.
"""

import frappe

from zikpro_uk_vat import cockpit as ck

COMPANY = "Demo Company"
VAT_ACCOUNT = "VAT - DC"
PERIOD = ("2026-09-01", "2026-09-30")
INV_DATE = "2026-09-15"
PAY_DATE = "2026-09-20"


def _tax_template(name, rate):
	full = f"{name} - DC"
	if frappe.db.exists("Item Tax Template", full):
		return full
	t = frappe.new_doc("Item Tax Template")
	t.title = name
	t.company = COMPANY
	t.append("taxes", {"tax_type": VAT_ACCOUNT, "tax_rate": rate})
	t.insert(ignore_permissions=True)
	return t.name


def _clean_period():
	"""Remove any prior run's invoices/payments in this test period, so repeated
	runs don't accumulate (the harness was previously non-idempotent)."""
	for inv in frappe.get_all("Sales Invoice", filters={"posting_date": INV_DATE, "docstatus": 1}, pluck="name"):
		for pe in frappe.get_all(
			"Payment Entry Reference", filters={"reference_name": inv}, fields=["parent"], pluck="parent"
		):
			if frappe.db.get_value("Payment Entry", pe, "docstatus") == 1:
				frappe.get_doc("Payment Entry", pe).cancel()
			frappe.delete_doc("Payment Entry", pe, force=True, ignore_permissions=True)
		frappe.get_doc("Sales Invoice", inv).cancel()
		frappe.delete_doc("Sales Invoice", inv, force=True, ignore_permissions=True)
	frappe.db.commit()


def run():
	results = []
	_clean_period()

	def check(label, got, expected):
		ok = abs(float(got) - float(expected)) < 0.01
		results.append(ok)
		print(f"[{'PASS' if ok else 'FAIL'}] {label}: got {got}, expected {expected}", flush=True)

	std = _tax_template("VAT 20", 20)
	red = _tax_template("VAT 5", 5)
	zero = _tax_template("VAT 0", 0)
	exempt = _tax_template("VAT Exempt", 0)
	frappe.db.commit()

	# One invoice, four VAT treatments:
	#   1000 @20% = 200 VAT | 1000 @5% = 50 VAT | 500 zero-rated | 500 exempt
	#   net 3000, VAT 250, gross 3250
	si = frappe.new_doc("Sales Invoice")
	si.company = COMPANY
	si.customer = "Harbour Retail Ltd"
	si.set_posting_time = 1
	si.posting_date = INV_DATE
	si.due_date = INV_DATE
	for item_tax, rate_amount in ((std, 1000), (red, 1000), (zero, 500), (exempt, 500)):
		si.append("items", {"item_code": "SVC-CONSULT", "qty": 1, "rate": rate_amount,
		                    "item_tax_template": item_tax})
	si.append("taxes", {"charge_type": "On Net Total", "account_head": VAT_ACCOUNT,
	                    "description": "VAT", "rate": 20})
	si.insert(ignore_permissions=True)
	si.submit()
	frappe.db.commit()
	print(f"INVOICE {si.name} net={si.base_net_total} vat={si.base_total_taxes_and_charges} gross={si.base_grand_total}", flush=True)

	check("invoice VAT is 20%+5% only (exempt/zero add none)", si.base_total_taxes_and_charges, 250)

	# --- ACCRUAL: whole invoice counts in the period it was issued
	frappe.db.set_value("VAT Settings", ck._connection()["settings"], "vat_accounting_scheme", ck.ACCRUAL)
	frappe.db.commit()
	acc = ck.get_return_figures(*PERIOD)["boxes"]
	check("ACCRUAL Box 1 (VAT due)", acc["box1"], 250)
	check("ACCRUAL Box 6 (net sales incl. zero-rated + exempt)", acc["box6"], 3000)

	# --- CASH: only what was actually paid, apportioned proportionally (Notice 731)
	from erpnext.accounts.doctype.payment_entry.payment_entry import get_payment_entry

	pe = get_payment_entry("Sales Invoice", si.name)
	pe.posting_date = PAY_DATE
	pe.reference_no = "MIXED-TEST"
	pe.reference_date = PAY_DATE
	half = si.base_grand_total / 2.0
	pe.paid_amount = half
	pe.received_amount = half
	pe.references[0].allocated_amount = half
	pe.insert(ignore_permissions=True)
	pe.submit()
	frappe.db.commit()
	print(f"PAYMENT {pe.name} allocated={half} (50% of {si.base_grand_total})", flush=True)

	frappe.db.set_value("VAT Settings", ck._connection()["settings"], "vat_accounting_scheme", ck.CASH)
	frappe.db.commit()
	cash = ck.get_return_figures(*PERIOD)["boxes"]
	check("CASH Box 1 = 50% of VAT", cash["box1"], 125)
	check("CASH Box 6 = 50% of net", cash["box6"], 1500)

	# restore the demo baseline
	frappe.db.set_value("VAT Settings", ck._connection()["settings"], "vat_accounting_scheme", ck.ACCRUAL)
	frappe.db.commit()
	print(f"\n=== MIXED-RATE VERIFY: {sum(results)}/{len(results)} passed ===", flush=True)
