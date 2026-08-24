"""P0-7 proof: the pre-filing reconciliation gate surfaces (advisory) when the return's
figures don't match the VAT posted to the mapped VAT accounts in the GL.

    bench --site erpnext.zikpro.local execute zikpro_uk_vat.smoke.verify_reconciliation.run
"""

import frappe

from zikpro_uk_vat import cockpit as ck

COMPANY = "Demo Company"
VAT_ACCOUNT = "VAT - DC"
EMPTY = ("2027-01-01", "2027-01-31")  # a period with no invoices -> GL VAT movement = 0


def run():
	results = []

	def check(label, cond):
		results.append(bool(cond))
		print(f"[{'PASS' if cond else 'FAIL'}] {label}", flush=True)

	# Ensure a VAT account is mapped so the reconciliation runs (else it self-skips).
	s = frappe.get_doc("VAT Settings", ck._connection()["settings"])
	if not ck.vat_accounts(ck.OUTPUT_VAT):
		s.append("vat_accounts", {"vat_type": ck.OUTPUT_VAT, "account": VAT_ACCOUNT})
		s.save(ignore_permissions=True)
		frappe.db.commit()

	def _recon(boxes, basis):
		return ck._reconciliation_warning(boxes, basis, *EMPTY)

	# 1. Return matches GL (both zero in an empty period) -> no reconciliation warning.
	check("reconciled period (0 vs 0) raises NO warning", len(_recon({"box1": 0, "box4": 0}, "accrual")) == 0)

	# 2. Return shows VAT the GL doesn't (empty period, box1=100) -> mismatch surfaces.
	w = _recon({"box1": 100, "box4": 0}, "accrual")
	check("mismatch (return 100 vs GL 0) surfaces a warning", len(w) == 1 and "Reconciliation" in w[0])
	if w:
		print("  WARN:", w[0][:150], flush=True)

	# 3. Cash basis: a return-vs-GL difference is EXPECTED, so it must NOT warn.
	check("cash basis does not raise a reconciliation warning", len(_recon({"box1": 100, "box4": 0}, "cash")) == 0)

	# 4. Guard: with NO VAT accounts mapped the check self-skips (mapping warning covers it).
	s2 = frappe.get_doc("VAT Settings", s.name)
	saved = list(s2.vat_accounts)
	s2.vat_accounts = []
	s2.save(ignore_permissions=True)
	frappe.db.commit()
	skipped = len(ck._reconciliation_warning({"box1": 100, "box4": 0}, "accrual", *EMPTY)) == 0
	# restore mapping
	s3 = frappe.get_doc("VAT Settings", s.name)
	for r in saved:
		s3.append("vat_accounts", {"vat_type": r.vat_type, "account": r.account})
	s3.save(ignore_permissions=True)
	frappe.db.commit()
	check("no VAT accounts mapped -> reconciliation self-skips", skipped)

	# 5. reconcile_period (refactored) still returns its diagnostic shape.
	rp = ck.reconcile_period(*EMPTY)
	check("reconcile_period still returns rows + reconciled", isinstance(rp, dict) and "rows" in rp and "reconciled" in rp)

	print(f"\n=== RECONCILIATION (P0-7) PROOF: {sum(results)}/{len(results)} passed ===", flush=True)
	return sum(results) == len(results)
