"""P0-1 / B92 backstop proof: a sales line raised with NO item_tax_template must be
detected and warned about, so Box 4 cannot be silently overstated even when an upstream
app omits classification.

    bench --site erpnext.zikpro.local execute zikpro_uk_vat.smoke.verify_null_classification.run
"""

import frappe

from zikpro_uk_vat import cockpit as ck

COMPANY = "Demo Company"
VAT_ACCOUNT = "VAT - DC"
PERIOD = ("2026-11-01", "2026-11-30")
INV_DATE = "2026-11-15"
CUSTOMER = "Harbour Retail Ltd"
ITEM = "SVC-CONSULT"


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


def _si(lines):
	si = frappe.new_doc("Sales Invoice")
	si.company = COMPANY
	si.customer = CUSTOMER
	si.set_posting_time = 1
	si.posting_date = INV_DATE
	si.due_date = INV_DATE
	for tmpl, amount in lines:
		row = {"item_code": ITEM, "qty": 1, "rate": amount}
		if tmpl:  # None => a bare line, no VAT classification (the B92 case)
			row["item_tax_template"] = tmpl
		si.append("items", row)
	si.append("taxes", {"charge_type": "On Net Total", "account_head": VAT_ACCOUNT, "description": "VAT", "rate": 20})
	si.insert(ignore_permissions=True)
	si.submit()
	frappe.db.commit()
	return si.name


def _clean():
	for n in frappe.get_all("Sales Invoice", filters={"posting_date": INV_DATE, "docstatus": 1}, pluck="name"):
		frappe.get_doc("Sales Invoice", n).cancel()
		frappe.delete_doc("Sales Invoice", n, force=True, ignore_permissions=True)
	frappe.db.commit()


def run():
	results = []

	def check(label, cond):
		results.append(bool(cond))
		print(f"[{'PASS' if cond else 'FAIL'}] {label}", flush=True)

	std = _template("VAT 20", 20)
	exempt = _template("VAT Exempt", 0)

	# Configure VAT Settings: standard + exempt classified, VAT account mapped, accrual.
	s = frappe.get_doc("VAT Settings", ck._connection()["settings"])
	s.vat_treatments = []
	s.append("vat_treatments", {"item_tax_template": std, "vat_treatment": "Standard rated", "in_box_6_7": 1})
	s.append("vat_treatments", {"item_tax_template": exempt, "vat_treatment": "Exempt", "in_box_6_7": 1})
	if not ck.vat_accounts(ck.OUTPUT_VAT):
		s.append("vat_accounts", {"vat_type": ck.OUTPUT_VAT, "account": VAT_ACCOUNT})
	s.save(ignore_permissions=True)
	frappe.db.set_value("VAT Settings", s.name, "vat_accounting_scheme", ck.ACCRUAL)
	frappe.db.commit()

	def _null_warn(fig):
		return [w for w in fig.get("warnings", []) if "no VAT liability classification" in w or "NO VAT liability classification" in w]

	# CASE 1: all lines classified -> NO null-classification warning.
	_clean()
	_si([(std, 1000), (exempt, 500)])
	fig = ck.get_return_figures(*PERIOD)
	check("all-classified quarter raises NO null warning", len(_null_warn(fig)) == 0)

	# CASE 2 (the B92 case): a bare line (no item_tax_template) present -> warning fires,
	# and because the registration is known-mixed (Exempt configured) it is the elevated form.
	_clean()
	_si([(std, 1000), (None, 750)])  # 750 raised with NO classification
	fig = ck.get_return_figures(*PERIOD)
	warns = _null_warn(fig)
	check("bare line -> null-classification warning FIRES", len(warns) == 1)
	check("warning is the ELEVATED (known-mixed) form", bool(warns) and "risks overstating Box 4" in warns[0])
	check("warning names the line count (1)", bool(warns) and "1 sales line" in warns[0])
	if warns:
		print("  WARN:", warns[0][:160], flush=True)

	# CASE 3: the backstop fires EVEN IF Box4 exists only via purchases and the upstream
	# app never classifies — i.e. it does not depend on _partial_exemption_warning firing.
	# (Covered by CASE 2 already: exempt_net there is 0 because the bare line has no exempt
	# template, so _partial_exemption_warning stays silent yet the backstop still fires.)
	pe = [w for w in fig.get("warnings", []) if "Notice 706" in w and "Exempt supplies of" in w]
	check("backstop fires where _partial_exemption_warning is SILENT (B92 core)", len(pe) == 0 and len(warns) == 1)

	# CASE 4 (P0-1 cash path): the SAME backstop must fire on the Cash scheme. Route a
	# bare-line invoice into the cash figures via the Notice 731 §6 excluded flag (invoice-
	# accounted, so no Payment Entry needed), switch the scheme to Cash, and assert it fires.
	_clean()
	inv = _si([(std, 1000), (None, 750)])
	frappe.db.set_value("Sales Invoice", inv, "vat_cash_excluded", 1, update_modified=False)
	frappe.db.set_value("VAT Settings", s.name, "vat_accounting_scheme", ck.CASH)
	frappe.db.commit()
	cash_fig = ck.get_return_figures(*PERIOD)
	cash_warns = _null_warn(cash_fig)
	check("cash basis is actually in use for CASE 4", cash_fig.get("basis") == "cash")
	check("bare line on CASH scheme -> null-classification warning FIRES (P0-1)", len(cash_warns) == 1)
	# restore accrual so later proofs don't inherit a cash scheme
	frappe.db.set_value("VAT Settings", s.name, "vat_accounting_scheme", ck.ACCRUAL)
	frappe.db.commit()

	_clean()
	print(f"\n=== NULL-CLASSIFICATION (P0-1/B92) PROOF: {sum(results)}/{len(results)} passed ===", flush=True)
	return sum(results) == len(results)
