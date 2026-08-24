"""P1-5 / B31 guard: a fresh install must be fully provisioned by after_install /
after_migrate (roles, DocPerms, custom fields, timezone) — install_app marks seed patches
done without running them, so anything not self-healed on every deploy starts EMPTY on a new
tenant site. This proof asserts the seed landed AND that re-running it is idempotent.

Auto-discovered by tests.run_proofs (CI). Run standalone:
    bench --site erpnext.zikpro.local execute zikpro_uk_vat.tests.test_fresh_install.prove_fresh_install
"""

import frappe

from zikpro_uk_vat import install as _i


def prove_fresh_install():
	res = {}

	# Idempotency: re-running the installer must not error or duplicate (B17/B31).
	try:
		_i.after_install()
		frappe.db.commit()
		res["after_install_idempotent"] = True
	except Exception as e:
		print("  after_install raised:", str(e)[:140], flush=True)
		res["after_install_idempotent"] = False

	# 1. Roles seeded.
	res["preparer_role_exists"] = bool(frappe.db.exists("Role", "UK VAT Preparer"))
	res["approver_role_exists"] = bool(frappe.db.exists("Role", "UK VAT Approver"))

	# 2. Custom fields seeded (a fresh cloud site was missing these — 1054 on every load).
	res["company_vrn_field"] = bool(frappe.get_meta("Company").get_field("uk_vat_registration_number"))
	ret_meta = frappe.get_meta("UK MTD VAT Return")
	res["return_sod_fields"] = all(
		bool(ret_meta.get_field(f)) for f in ("prepared_by", "approved_by", "approval_status", "form_bundle_number")
	)
	set_meta = frappe.get_meta("VAT Settings")
	res["settings_broker_fields"] = all(
		bool(set_meta.get_field(f)) for f in ("use_broker", "broker_url", "broker_tenant_id", "broker_shared_secret")
	)

	# 3. Timezone pinned to Europe/London (else a fresh site inherits Asia/Kolkata and
	#    mis-dates transactions across VAT quarter boundaries — B37).
	res["timezone_london"] = frappe.db.get_single_value("System Settings", "time_zone") == "Europe/London"

	# 4. DocPerms materialised as Custom DocPerm (System Manager + both roles) on both
	#    doctypes — once any Custom DocPerm exists the standard JSON perms go inert (B40).
	for dt in ("VAT Settings", "UK MTD VAT Return"):
		roles = {
			p.role for p in frappe.get_all(
				"Custom DocPerm", filters={"parent": dt, "permlevel": 0}, fields=["role"]
			)
		}
		res[f"docperms_{dt.replace(' ', '_')}"] = {
			"System Manager", "UK VAT Preparer", "UK VAT Approver"
		}.issubset(roles)

	passed = sum(1 for v in res.values() if v)
	print(f"FRESH-INSTALL PROOF {passed}/{len(res)}: {res}", flush=True)
	return res
