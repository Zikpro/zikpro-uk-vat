"""P0-2 / BSW-2 (B58/B93) proof: a UK MTD VAT Return cannot reach docstatus=1 by a
direct submit() — bypassing the cockpit's HMRC filing + preparer/approver SoD. Only a
return carrying an HMRC receipt (form_bundle_number) may submit.

    bench --site erpnext.zikpro.local execute zikpro_uk_vat.smoke.verify_submit_guard.run
"""

import frappe

REF = "24A1-GUARDTEST"


def _clean():
	for n in frappe.get_all("UK MTD VAT Return", filters={"reference_key": REF}, pluck="name"):
		# test artifact may be submitted; drop docstatus in the DB so on_cancel (which
		# refuses to un-file a real return) doesn't block deletion of the fixture.
		frappe.db.set_value("UK MTD VAT Return", n, "docstatus", 0, update_modified=False)
		frappe.delete_doc("UK MTD VAT Return", n, force=True, ignore_permissions=True)
	frappe.db.commit()


def _draft(with_receipt):
	doc = frappe.new_doc("UK MTD VAT Return")
	doc.vrn = "193054661"
	doc.reference_key = REF
	doc.period_start_date = "2026-01-01"
	doc.period_end_date = "2026-03-31"
	doc.due_date = "2026-05-07"
	doc.company = "Demo Company"
	doc.sales_vat_due_box1 = 100
	doc.total_vat_due_box3 = 100
	doc.net_vat_due_box5 = 100
	if with_receipt:
		doc.form_bundle_number = "TEST-BUNDLE-123"
	doc.insert(ignore_permissions=True)
	frappe.db.commit()
	return doc


def run():
	results = []

	def check(label, cond):
		results.append(bool(cond))
		print(f"[{'PASS' if cond else 'FAIL'}] {label}", flush=True)

	# CASE 1: a bare draft (no HMRC receipt) must NOT submit, even with ignore_permissions
	# (before_submit is a business-rule guard, not a permission check).
	_clean()
	d = _draft(with_receipt=False)
	blocked = False
	try:
		d.flags.ignore_permissions = True
		d.submit()  # NB: no ignore_permissions arg — that TypeErrors on v16.28 (B45)
	except frappe.PermissionError:
		blocked = True
	except Exception as e:  # accept any refusal that names the guard
		blocked = "form bundle" in str(e).lower() or "cockpit" in str(e).lower()
	d.reload()
	check("bare draft (no form_bundle_number) submit REFUSED", blocked)
	check("  ...and it stayed a draft (docstatus 0)", d.docstatus == 0)

	# CASE 2: a return that carries an HMRC receipt submits normally (legit cockpit path).
	_clean()
	d2 = _draft(with_receipt=True)
	ok = False
	try:
		d2.flags.ignore_permissions = True
		d2.submit()
		d2.reload()
		ok = d2.docstatus == 1
	except Exception as e:
		print("  unexpected on receipted submit:", str(e)[:160], flush=True)
	check("return WITH form_bundle_number submits (docstatus 1)", ok)

	_clean()
	print(f"\n=== SUBMIT-GUARD (P0-2/BSW-2) PROOF: {sum(results)}/{len(results)} passed ===", flush=True)
	return sum(results) == len(results)
