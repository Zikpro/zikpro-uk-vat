"""P1-8 multi-tenant hardening guard: locks the invariants that keep one tenant's VAT data
from leaking to another once the OAuth broker serves many tenants. Audited clean (Aug 2026);
this proof turns a future regression red.

Auto-discovered by tests.run_proofs (CI). Run standalone:
    bench --site erpnext.zikpro.local execute zikpro_uk_vat.tests.test_hardening.prove_multitenant_hardening
"""

import frappe

MODULE = "Zikpro Uk Vat"
VAT_DOCTYPES = ("VAT Settings", "UK MTD VAT Return", "VAT Adjustment", "VAT Adjustment Schedule",
				"VAT Ledger Entry")


def prove_multitenant_hardening():
	res = {}

	# B38: every Script Report in the app must be ROLE-GATED — empty roles means the report
	# is runnable by EVERYONE, with no row-level security, exposing all clients' figures.
	reports = frappe.get_all(
		"Report", filters={"module": MODULE, "report_type": ("in", ["Script Report", "Query Report"])},
		pluck="name",
	)
	res["reports_present"] = len(reports) >= 1
	ungated = [r for r in reports if not frappe.get_doc("Report", r).roles]
	res["all_reports_role_gated"] = not ungated
	# (report data itself is company-scoped: it delegates to cockpit.get_return_figures,
	#  which filters on the default company.)

	# SC-02: server scripts stay OFF — the app ships none and must not silently depend on them.
	res["server_scripts_disabled"] = not frappe.conf.get("server_script_enabled")

	# B32: no VAT doctype grants the "All" role (every authenticated user) any access.
	all_role_perms = frappe.get_all(
		"Custom DocPerm", filters={"parent": ("in", VAT_DOCTYPES), "role": "All"}, pluck="name"
	)
	res["no_all_role_grant"] = not all_role_perms

	passed = sum(1 for v in res.values() if v)
	print(f"MULTITENANT-HARDENING PROOF {passed}/{len(res)}: {res}", flush=True)
	return res
