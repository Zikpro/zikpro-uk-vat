"""P0-3 / BSW-1 (B40/B51) proof: the SEC-11 Custom DocPerms grant EXACTLY the intended
ptypes — no inherited over-grant (e.g. export=1 on a read-only Approver) — and reconcile
drift on every run. System Manager access is preserved (B40).

    bench --site erpnext.zikpro.local execute zikpro_uk_vat.smoke.verify_docperm_baseline.run
"""

import frappe

from zikpro_uk_vat.install import ensure_vat_roles_and_perms

SETTINGS = "VAT Settings"
RETURN = "UK MTD VAT Return"


def _perm(doctype, role):
	name = frappe.db.get_value("Custom DocPerm", {"parent": doctype, "permlevel": 0, "role": role})
	if not name:
		return None
	fields = ["read", "write", "create", "delete", "submit", "cancel", "export", "import", "print", "email", "share"]
	return {f: int(frappe.db.get_value("Custom DocPerm", name, f) or 0) for f in fields}


def run():
	results = []

	def check(label, cond, got=None):
		results.append(bool(cond))
		extra = "" if cond else f"  (got {got})"
		print(f"[{'PASS' if cond else 'FAIL'}] {label}{extra}", flush=True)

	# Apply the (now-reconciling) perm materialiser — corrects any drifted rows.
	ensure_vat_roles_and_perms()
	frappe.db.commit()

	appr = _perm(SETTINGS, "UK VAT Approver")
	check("B40: Approver row on VAT Settings exists", appr is not None)
	if appr:
		check("B51: Approver read-only on Settings — write=0", appr["write"] == 0, appr)
		check("B51: Approver read-only on Settings — create=0", appr["create"] == 0, appr)
		check("B51: Approver NO inherited export on Settings", appr["export"] == 0, appr)
		check("Approver read=1 on Settings", appr["read"] == 1, appr)

	prep = _perm(RETURN, "UK VAT Preparer")
	check("Preparer on Return exists", prep is not None)
	if prep:
		check("Preparer can create/write the Return", prep["create"] == 1 and prep["write"] == 1, prep)
		check("SoD: Preparer has NO submit on Return", prep["submit"] == 0, prep)
		check("B51: Preparer NO inherited export on Return", prep["export"] == 0, prep)
		check("B51: Preparer NO inherited delete on Return", prep["delete"] == 0, prep)

	sm_s = _perm(SETTINGS, "System Manager")
	sm_r = _perm(RETURN, "System Manager")
	check("B40: System Manager present on Settings (read/write/create)",
		  sm_s and sm_s["read"] and sm_s["write"] and sm_s["create"], sm_s)
	check("B40: System Manager present on Return (incl submit)",
		  sm_r and sm_r["read"] and sm_r["write"] and sm_r["create"] and sm_r["submit"], sm_r)

	appr_r = _perm(RETURN, "UK VAT Approver")
	check("Approver can submit the Return (the filing role)", appr_r and appr_r["submit"] == 1, appr_r)
	check("B51: Approver NO inherited export on Return", appr_r and appr_r["export"] == 0, appr_r)

	print(f"\n=== DOCPERM-BASELINE (P0-3/BSW-1) PROOF: {sum(results)}/{len(results)} passed ===", flush=True)
	return sum(results) == len(results)
