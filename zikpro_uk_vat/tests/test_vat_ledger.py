"""VAT Ledger tests — the immutable per-event sub-ledger.

Runnable via `bench execute zikpro_uk_vat.tests.test_vat_ledger.prove_*`
(the full FrappeTestCase bootstrap collides with this dev site's demo Fiscal Year,
same as the other prove_* suites here). Proves:
  - the writer derives the right VAT/net/box from a real submitted invoice,
  - it is idempotent (re-run keeps exactly one Accrued row),
  - the derived status rolls up correctly,
  - cancel clears the events,
  - immutability is enforced (no edit / no user delete),
  - and a REAL submit -> cancel end-to-end actually fires the hooks.
"""

import frappe
from frappe.utils import flt

from zikpro_uk_vat import cockpit as c
from zikpro_uk_vat import vat_ledger as vl

LEDGER = "VAT Ledger Entry"


def _entries(dt, name, event_type=None):
	f = {"source_doctype": dt, "source_name": name}
	if event_type:
		f["event_type"] = event_type
	return frappe.get_all(LEDGER, filters=f, fields=["name", "event_type", "vat_box", "net_amount", "vat_amount"])


def prove_ledger():
	# Create our OWN submitted purchase invoice carrying VAT (Box 4 side) rather than
	# scavenging one from the site — a fresh CI site has none (they get cleaned up).
	from zikpro_uk_vat.tests.test_ledger_lifecycle import _mk_invoice, _cleanup

	res = {}
	_cleanup()
	pi = _mk_invoice(c.PURCHASE_INVOICE, "2017-08-20", 500)  # net 500, VAT 100 -> Box 4
	pi_name = pi.name
	doc = frappe.get_doc(c.PURCHASE_INVOICE, pi_name)
	exp_vat, exp_net = vl._invoice_vat_net(doc)

	vl._clear(c.PURCHASE_INVOICE, pi_name)  # clean slate
	# 1. writer produces one Accrued row with the right figures + box
	vl.record_invoice_accrual(doc)
	rows = _entries(c.PURCHASE_INVOICE, pi_name)
	res["one_accrued_row"] = len(rows) == 1 and rows[0]["event_type"] == "Accrued"
	res["vat_correct"] = rows and abs(flt(rows[0]["vat_amount"]) - exp_vat) < 0.01 and exp_vat > 0
	res["net_correct"] = rows and abs(flt(rows[0]["net_amount"]) - exp_net) < 0.01
	res["box_is_4"] = rows and rows[0]["vat_box"] == "Box 4"
	# 2. idempotent
	vl.record_invoice_accrual(doc)
	res["idempotent_single_row"] = len(_entries(c.PURCHASE_INVOICE, pi_name)) == 1
	# 3. status rollup (accrual + unfiled => Not claimed)
	res["status_not_claimed"] = vl.compute_vat_status(c.PURCHASE_INVOICE, pi_name) == "Not claimed"
	res["status_written_to_invoice"] = frappe.db.get_value(c.PURCHASE_INVOICE, pi_name, "vat_status") == "Not claimed"
	# 4. immutability: cannot edit, cannot user-delete
	entry = frappe.get_doc(LEDGER, _entries(c.PURCHASE_INVOICE, pi_name)[0]["name"])
	try:
		entry.vat_amount = 999
		entry.save(ignore_permissions=True)
		res["immutable_no_edit"] = False
	except frappe.PermissionError:
		res["immutable_no_edit"] = True
	try:
		frappe.get_doc(LEDGER, entry.name).delete(ignore_permissions=True)
		res["immutable_no_delete"] = False
	except frappe.PermissionError:
		res["immutable_no_delete"] = True
	# 5. clear (cancel path) removes events + resets status
	vl.clear_source_entries(doc)
	res["cleared_on_cancel"] = _entries(c.PURCHASE_INVOICE, pi_name) == []
	res["status_reset"] = (frappe.db.get_value(c.PURCHASE_INVOICE, pi_name, "vat_status") or "") == ""

	_cleanup()  # remove our test invoice
	frappe.db.commit()
	passed = sum(1 for v in res.values() if v)
	print(f"LEDGER PROOF {passed}/{len(res)}: {res}", flush=True)
	return res


def prove_ledger_e2e():
	"""Real submit -> cancel of a fresh Purchase Invoice; proves the HOOK fires
	(not just the function). Best-effort: needs a company/supplier/expense+VAT
	account on the site. Rolls everything back."""
	res = {}
	company = frappe.db.get_single_value("Global Defaults", "default_company")
	vat_acct = (c.vat_accounts(c.INPUT_VAT) or [None])[0]
	supplier = frappe.db.get_value("Supplier", {}, "name")
	exp_acct = frappe.db.get_value(
		"Account", {"company": company, "is_group": 0, "root_type": "Expense"}, "name"
	)
	if not all([company, vat_acct, supplier, exp_acct]):
		print(f"LEDGER E2E SKIPPED (missing setup): company={bool(company)} vat={bool(vat_acct)} "
			f"supplier={bool(supplier)} exp={bool(exp_acct)}", flush=True)
		return res
	pi = frappe.new_doc("Purchase Invoice")
	pi.company = company
	pi.supplier = supplier
	pi.append("items", {"item_name": "VAT Ledger Test", "description": "test", "qty": 1,
						"rate": 1000, "expense_account": exp_acct, "uom": "Nos"})
	pi.append("taxes", {"charge_type": "Actual", "account_head": vat_acct,
						"description": "VAT 20%", "tax_amount": 200})
	pi.set_missing_values()
	pi.insert(ignore_permissions=True)
	pi.submit()
	rows = _entries(c.PURCHASE_INVOICE, pi.name, "Accrued")
	res["hook_fired_on_submit"] = len(rows) == 1
	res["e2e_vat_200"] = bool(rows) and abs(flt(rows[0]["vat_amount"]) - 200) < 0.01
	res["e2e_box_4"] = bool(rows) and rows[0]["vat_box"] == "Box 4"
	pi.cancel()
	res["hook_cleared_on_cancel"] = _entries(c.PURCHASE_INVOICE, pi.name) == []
	# cleanup
	frappe.delete_doc("Purchase Invoice", pi.name, force=True, ignore_permissions=True)
	frappe.db.commit()
	passed = sum(1 for v in res.values() if v)
	print(f"LEDGER E2E PROOF {passed}/{len(res)}: {res}", flush=True)
	return res
