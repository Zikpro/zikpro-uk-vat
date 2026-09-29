"""Backfill `broker_environment` on already-registered broker tenants.

Env-refactor step 2 makes the HMRC environment TOKEN-DERIVED: `self_register`
stamps `broker_environment` ('production' | 'sandbox') on VAT Settings, and
`_hmrc_production` reads it instead of the branch default. Tenants that
registered BEFORE this change have a `broker_tenant_id` but a blank
`broker_environment`, so they currently fall through to the branch default.

That fallback is correct today (develop→sandbox, main→production, matching how
they filed before). But step 4 equalises the branch constants — after that,
a blank `broker_environment` with no site config would lose its anchor. Stamp
each registered tenant NOW with its current effective environment, so its
behaviour is preserved explicitly and can never drift on a develop→main merge.

Idempotent: only fills a BLANK field, and only for tenants that actually hold a
broker tenant id. Ships in the same commit as the field (standing rule: a new
field's backfill lands with the field).
"""

import frappe

from zikpro_uk_vat.cockpit import _hmrc_environment


def execute():
	if not frappe.db.has_column("VAT Settings", "broker_environment"):
		return

	# Only broker-registered tenants have an environment to record; a non-broker
	# site files with its own HMRC client and this field stays blank by design.
	rows = frappe.get_all(
		"VAT Settings",
		filters={"broker_environment": ["in", [None, ""]]},
		fields=["name", "broker_tenant_id"],
	)
	stamped = 0
	for row in rows:
		# Frappe's "is set" filter counts an empty string as set, so gate on a
		# truthy tenant id here instead — a site that never registered stays blank.
		if not row.broker_tenant_id:
			continue
		# The resolver falls through to config/branch default while the field is
		# blank — i.e. exactly the environment this tenant files against today.
		env = _hmrc_environment(row.name).lower()  # 'production' | 'sandbox'
		frappe.db.set_value("VAT Settings", row.name, "broker_environment", env, update_modified=False)
		stamped += 1

	if stamped:
		frappe.db.commit()
