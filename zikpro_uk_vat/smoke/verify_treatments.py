"""Hand-checked verification of VAT-treatment classification via Item Tax Templates.

    bench --site <site> execute zikpro_uk_vat.smoke.verify_treatments.run

Zero-rated, exempt and outside-scope items all carry 0% VAT — only the
item_tax_template distinguishes them. Box 6/7 must INCLUDE zero-rated and exempt
but EXCLUDE outside-scope. Builds one invoice with all five treatments and
compares Boxes 1/6 to figures worked out by hand. Idempotent-ish; cleans its own
Sales Invoice each run.
"""

import frappe

from zikpro_uk_vat import cockpit as ck

COMPANY = "Demo Company"
VAT_ACCOUNT = "VAT - DC"
PERIOD = ("2026-10-01", "2026-10-31")
INV_DATE = "2026-10-15"


def _template(title, rate):
	full = f"{title} - DC"
	if frappe.db.exists("Item Tax Template", full):
		return full
	t = frappe.new_doc("Item Tax Template")
	t.title = title
	t.company = COMPANY
	t.append("taxes", {"tax_type": VAT_ACCOUNT, "tax_rate": rate})
	t.insert(ignore_permissions=True)
	return t.name


def run():
	results = []

	def check(label, got, expected):
		ok = abs(float(got) - float(expected)) < 0.01
		results.append(ok)
		print(f"[{'PASS' if ok else 'FAIL'}] {label}: got {got}, expected {expected}", flush=True)

	std = _template("VAT 20", 20)
	red = _template("VAT 5", 5)
	zero = _template("VAT 0", 0)
	exempt = _template("VAT Exempt", 0)
	outside = _template("VAT Outside Scope", 0)
	frappe.db.commit()

	# classify them in VAT Settings (outside-scope excluded from Box 6/7)
	s = frappe.get_doc("VAT Settings", ck._connection()["settings"])
	s.vat_treatments = []
	for tmpl, treatment, in67 in (
		(std, "Standard rated", 1), (red, "Reduced rated", 1), (zero, "Zero rated", 1),
		(exempt, "Exempt", 1), (outside, "Outside scope", 0),
	):
		s.append("vat_treatments", {"item_tax_template": tmpl, "vat_treatment": treatment, "in_box_6_7": in67})
	# ensure the VAT account is mapped so Box 1 computes
	if not ck.vat_accounts(ck.OUTPUT_VAT):
		s.append("vat_accounts", {"vat_type": ck.OUTPUT_VAT, "account": VAT_ACCOUNT})
	s.save(ignore_permissions=True)
	frappe.db.set_value("VAT Settings", s.name, "vat_accounting_scheme", ck.ACCRUAL)
	frappe.db.commit()

	# clean any prior run
	for n in frappe.get_all("Sales Invoice", filters={"posting_date": INV_DATE, "docstatus": 1}, pluck="name"):
		frappe.get_doc("Sales Invoice", n).cancel()
		frappe.delete_doc("Sales Invoice", n, force=True, ignore_permissions=True)
	frappe.db.commit()

	# One invoice: 1000@20 + 1000@5 + 500 zero + 500 exempt + 700 OUTSIDE SCOPE
	#   VAT = 200 + 50 = 250
	#   Box 6 (in-scope net) = 1000+1000+500+500 = 3000  (the 700 is excluded)
	si = frappe.new_doc("Sales Invoice")
	si.company = COMPANY
	si.customer = "Harbour Retail Ltd"
	si.set_posting_time = 1
	si.posting_date = INV_DATE
	si.due_date = INV_DATE
	for tmpl, amount in ((std, 1000), (red, 1000), (zero, 500), (exempt, 500), (outside, 700)):
		si.append("items", {"item_code": "SVC-CONSULT", "qty": 1, "rate": amount, "item_tax_template": tmpl})
	si.append("taxes", {"charge_type": "On Net Total", "account_head": VAT_ACCOUNT, "description": "VAT", "rate": 20})
	si.insert(ignore_permissions=True)
	si.submit()
	frappe.db.commit()
	print(f"INVOICE {si.name} net={si.base_net_total} vat={si.base_total_taxes_and_charges}", flush=True)

	fig = ck.get_return_figures(*PERIOD)
	boxes = fig["boxes"]
	check("Box 1 (VAT) = 20% + 5% only", boxes["box1"], 250)
	check("Box 6 EXCLUDES outside-scope (3700 net -> 3000)", boxes["box6"], 3000)
	check("no warnings (all templates classified)", len(fig.get("warnings", [])), 0)

	# now REMOVE the outside-scope classification -> it should warn + count in Box 6
	s = frappe.get_doc("VAT Settings", s.name)
	s.vat_treatments = [t for t in s.vat_treatments if t.item_tax_template != outside]
	s.save(ignore_permissions=True)
	frappe.db.commit()
	fig2 = ck.get_return_figures(*PERIOD)
	check("unclassified -> Box 6 back to full 3700", fig2["boxes"]["box6"], 3700)
	check("unclassified -> a warning is raised", 1 if fig2.get("warnings") else 0, 1)
	if fig2.get("warnings"):
		print("  WARN:", fig2["warnings"][-1][:120], flush=True)

	# cleanup this invoice so the demo period stays clean
	frappe.get_doc("Sales Invoice", si.name).cancel()
	frappe.delete_doc("Sales Invoice", si.name, force=True, ignore_permissions=True)
	frappe.db.commit()
	print(f"\n=== TREATMENTS VERIFY: {sum(results)}/{len(results)} passed ===", flush=True)
