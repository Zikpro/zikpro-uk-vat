"""Custom field provisioning — self-healing.

These fields were previously created only by a one-shot patch
(patches/v1_0/add_eu_vat_fields). That is not repairable: once the patch is
recorded in Patch Log it never runs again, so a site that ended up missing a
field stays broken forever through any number of `bench migrate` runs.

That is not hypothetical — ziktax.frappe.cloud had `is_eu_supplier` and
`is_eu_customer` but NOT `Company.uk_vat_registration_number`, while Patch Log
showed the patch as completed. Every cockpit page load threw
`OperationalError (1054) Unknown column 'uk_vat_registration_number'`.

So field creation runs from `after_migrate` (every deploy) as well as
`after_install`, and uses the framework helper, which is idempotent and also
repairs drifted properties on existing fields.
"""

import frappe
from frappe.custom.doctype.custom_field.custom_field import (
	create_custom_fields as _create_custom_fields,
)

# "VAT Activity" — a read-only view of this invoice's VAT Ledger events, shown
# BELOW the tax breakup. The log itself lives in the VAT Ledger Entry doctype (a
# separate immutable ledger, GL-Entry-style), NOT as an editable child table on the
# submittable invoice — see project-uk-mtd-vat-ledger-design. These fields only
# DISPLAY it: a derived status + an HTML panel rendered by doctype_js.
_VAT_ACTIVITY_FIELDS = [
	{
		"fieldname": "vat_activity_section",
		"label": "VAT Activity",
		"fieldtype": "Section Break",
		"insert_after": "total_taxes_and_charges",
		"collapsible": 1,
	},
	{
		"fieldname": "vat_status",
		"label": "VAT Status",
		"fieldtype": "Data",
		"insert_after": "vat_activity_section",
		"read_only": 1,
		"allow_on_submit": 1,
		"description": "Derived from the VAT Ledger — not editable.",
	},
	{
		"fieldname": "vat_activity_html",
		"label": "VAT Activity",
		"fieldtype": "HTML",
		"insert_after": "vat_status",
	},
	# Notice 731 §6: certain supplies (hire purchase / lease, reverse charge, imports,
	# payment due 6+ months out, advance invoices) are EXCLUDED from the Cash Accounting
	# scheme and must be accounted on the invoice date even when the business is on cash.
	# The cash engine reads this flag and accrual-bases these invoices. See cockpit.py
	# _cash_excluded_side / _cash_load_side.
	{
		"fieldname": "vat_cash_excluded",
		"label": "Excluded from Cash Accounting (Notice 731)",
		"fieldtype": "Check",
		"insert_after": "vat_activity_html",
		"default": 0,
		"allow_on_submit": 1,
		"description": (
			"Tick for hire purchase / lease, reverse-charge supplies, imports, or payment "
			"due 6+ months out. On the Cash Accounting scheme this supply is still counted "
			"on its invoice date, not when paid (VAT Notice 731 §6)."
		),
	},
]

CUSTOM_FIELDS = {
	"Company": [
		{
			"fieldname": "uk_vat_registration_number",
			"label": "UK VAT Registration Number",
			"fieldtype": "Data",
			"insert_after": "parent_company",
			"unique": 1,
		}
	],
	"Purchase Invoice": [
		{
			"fieldname": "is_eu_supplier",
			"label": "Is EU Supplier",
			"fieldtype": "Check",
			"insert_after": "supplier",
			"default": 0,
		},
		*_VAT_ACTIVITY_FIELDS,
	],
	"Sales Invoice": [
		{
			"fieldname": "is_eu_customer",
			"label": "Is EU Customer",
			"fieldtype": "Check",
			"insert_after": "customer",
			"default": 0,
		},
		*_VAT_ACTIVITY_FIELDS,
	],
	# Preparer/approver segregation-of-duties audit fields on the return (SEC-11).
	"UK MTD VAT Return": [
		{
			"fieldname": "approval_status",
			"label": "Approval Status",
			"fieldtype": "Select",
			"options": "Awaiting Approval\nFiled",
			"insert_after": "status",
			"read_only": 1,
		},
		{
			"fieldname": "prepared_by",
			"label": "Prepared By",
			"fieldtype": "Link",
			"options": "User",
			"insert_after": "approval_status",
			"read_only": 1,
		},
		{
			"fieldname": "prepared_on",
			"label": "Prepared On",
			"fieldtype": "Datetime",
			"insert_after": "prepared_by",
			"read_only": 1,
		},
		{
			"fieldname": "approved_by",
			"label": "Approved By",
			"fieldtype": "Link",
			"options": "User",
			"insert_after": "prepared_on",
			"read_only": 1,
		},
	],
}

# SEC-11: two roles giving a real preparer/approver segregation of duties for
# filing a legal VAT return. Preparer builds the draft; a DIFFERENT approver
# files it (self-approval blocked in code — see cockpit.approve_and_submit).
PREPARER_ROLE = "UK VAT Preparer"
APPROVER_ROLE = "UK VAT Approver"

# DocPerms are defence-in-depth; the cockpit endpoints enforce the roles in code
# too (frappe.get_all / Administrator bypass DocPerms — broadcasts B26/B29).
# System Manager MUST be listed here. Frappe uses Custom DocPerm *exclusively* once
# any Custom DocPerm row exists for a DocType — the standard .json DocPerms (which
# grant System Manager) become inert. So the moment we add the VAT roles below, a
# System Manager loses ALL access unless we also re-grant it here, matching the
# doctype's original standard perms. (Regression found on cloud: every System
# Manager got 403 from the cockpit's _require gate — B29 class: local tests run as
# Administrator, who bypasses permissions, so it was invisible.)
SYSTEM_MANAGER = "System Manager"
# B51: a Custom DocPerm created with only the *wanted* ptypes inherits Frappe's field
# defaults for the rest (observed live: UK VAT Approver silently had export=1 on VAT
# Settings, a read-only role). Every managed row is materialised from this full baseline
# of 0s so the granted set is EXACTLY the intent — nothing inherited.
_ALL_PTYPES = (
	"select", "read", "write", "create", "delete", "submit", "cancel", "amend",
	"report", "export", "import", "share", "print", "email",
)
_VAT_DOCPERMS = {
	"VAT Settings": {
		SYSTEM_MANAGER: {"read": 1, "write": 1, "create": 1},
		PREPARER_ROLE: {"read": 1, "write": 1},
		APPROVER_ROLE: {"read": 1},
	},
	"UK MTD VAT Return": {
		# `report` + `export` are REQUIRED for the standard "VAT Return" report: Frappe's
		# get_script checks has_permission(ref_doctype, "report"), so without it the report
		# 403s for EVERY role (the all-zeros baseline never granted it). export lets the
		# roles that can read the return keep a copy for records.
		SYSTEM_MANAGER: {"read": 1, "write": 1, "create": 1, "submit": 1, "report": 1, "export": 1},
		PREPARER_ROLE: {"read": 1, "write": 1, "create": 1, "report": 1, "export": 1},
		APPROVER_ROLE: {"read": 1, "write": 1, "submit": 1, "cancel": 1, "report": 1, "export": 1},
	},
}


def ensure_vat_roles_and_perms():
	"""Create the two VAT roles and their DocPerms, idempotently, on every deploy.

	Done in code (after_migrate) not as fixtures/JSON: a fresh install skips seed
	patches (B31) and Frappe Cloud migrate does not re-apply DocPerm JSON to an
	existing DocType (the DocPerm-skip gotcha). Running it every migrate self-heals.
	"""
	for role in (PREPARER_ROLE, APPROVER_ROLE):
		if not frappe.db.exists("Role", role):
			frappe.get_doc(
				{"doctype": "Role", "role_name": role, "desk_access": 1}
			).insert(ignore_permissions=True)

	for doctype, role_perms in _VAT_DOCPERMS.items():
		if not frappe.db.exists("DocType", doctype):
			continue
		# Only Custom DocPerm counts as "present": once ANY Custom DocPerm row exists
		# for this doctype, Frappe ignores the standard DocPerm table entirely, so a
		# role that lives only in standard DocPerm (e.g. System Manager) must still be
		# (re-)materialised here as a Custom DocPerm or it silently loses access.
		changed = False
		for role, perms in role_perms.items():
			# Exact target: baseline 0 for every ptype, then overlay the intended grants.
			target = {pt: 0 for pt in _ALL_PTYPES}
			target.update(perms)
			name = frappe.db.get_value(
				"Custom DocPerm", {"parent": doctype, "permlevel": 0, "role": role}
			)
			if name:
				# RECONCILE an existing row — don't just skip it (B51): a drifted row with
				# an inherited over-grant (e.g. export=1 on a read-only role) must be corrected.
				if any(
					int(frappe.db.get_value("Custom DocPerm", name, pt) or 0) != v
					for pt, v in target.items()
				):
					frappe.db.set_value("Custom DocPerm", name, target, update_modified=False)
					changed = True
			else:
				frappe.get_doc(
					{
						"doctype": "Custom DocPerm",
						"parent": doctype,
						"parenttype": "DocType",
						"parentfield": "permissions",
						"role": role,
						"permlevel": 0,
						**target,
					}
				).insert(ignore_permissions=True)
				changed = True
		if changed:
			frappe.clear_cache(doctype=doctype)
	frappe.db.commit()


def ensure_custom_fields():
	"""Create any missing custom fields. Safe to run on every migrate."""
	_create_custom_fields(CUSTOM_FIELDS, ignore_validate=True)

	# The Custom Field row existing does not guarantee the DB column does — a
	# failed ALTER leaves the doc behind. Reconcile the actual schema.
	for doctype in CUSTOM_FIELDS:
		missing = [
			f["fieldname"]
			for f in CUSTOM_FIELDS[doctype]
			if not frappe.db.has_column(doctype, f["fieldname"])
		]
		if missing:
			frappe.db.commit()  # DDL must not run inside the open transaction
			frappe.db.updatedb(doctype)
			frappe.logger().info(
				f"UK VAT: rebuilt schema for {doctype}, missing columns {missing}"
			)


def enforce_uk_timezone():
	"""Pin the site timezone to Europe/London (broadcast B37).

	Frappe's get_system_timezone defaults an unset time_zone to Asia/Kolkata
	(UTC+5:30). For a UK VAT product that is a correctness/compliance bug, not a
	cosmetic one: now_datetime()/nowdate() and creation/modified become naive IST
	timestamps, so any UK transaction after ~18:30 is dated to the NEXT DAY — a
	31-Mar 20:00 sale becomes 1-Apr and lands in the WRONG VAT period, and HMRC
	deadline maths shifts too. Dev sites happen to be set correctly by hand, but a
	fresh cloud/CI install inherits the default (and B31 means seed patches don't
	run on a new site), so this must be enforced in code. Idempotent.
	"""
	if frappe.db.get_single_value("System Settings", "time_zone") != "Europe/London":
		frappe.db.set_single_value("System Settings", "time_zone", "Europe/London")
		frappe.db.commit()
		frappe.logger().info("UK VAT: set System Settings.time_zone = Europe/London")


def ensure_locale_defaults():
	"""Guarantee System Settings has a language + number_format so invoices can post.

	Frappe core's get_locale_value() (frappe/locale.py) does `if lang: value = ...;
	return value or ...` — when System Settings.language is EMPTY, `frappe.local.lang`
	is falsy, `value` is never bound, and the function raises
	`UnboundLocalError: local variable 'value'` the moment ERPNext calculates taxes
	(calculate_item_values -> get_number_format). The result: submitting ANY Sales/
	Purchase Invoice throws, so no VAT is ever posted to the ledger. A normal ERPNext
	install runs the setup wizard, which fills these in — but a site provisioned
	WITHOUT the wizard (bare `bench new-site`, some cloud/CI provisioning, partial
	restores) has them blank and hits the crash on the very first invoice. We only
	fill blanks (never override an admin's choice). Idempotent; must never abort migrate.
	"""
	try:
		defaults = {"language": "en", "number_format": "#,###.##", "float_precision": "2"}
		changed = False
		for key, val in defaults.items():
			if not frappe.db.get_single_value("System Settings", key):
				frappe.db.set_single_value("System Settings", key, val)
				changed = True
		if changed:
			frappe.db.commit()
			frappe.logger().info("UK VAT: seeded blank System Settings locale defaults")
	except Exception:
		frappe.log_error("UK VAT: ensure_locale_defaults failed", "uk_vat_locale")


# Standard UI records (module reports + the public workspace) that MUST match the
# on-disk definition. Frappe's import_file skips re-importing a standard record when
# the DB copy's `modified` is not older than the file's — so once these diverge in a
# site's DB they stay stale through every `bench migrate` (B17/B19 family).
# ziktax.frappe.cloud proved it: the "VAT Return" report kept a stale
# ref_doctype="VAT MTD Return" (real doctype is "UK MTD VAT Return") and the "UK VAT"
# workspace kept phantom links to long-removed doctypes (UK MTD VAT Liability/Payment)
# — both throwing "DocType ... not found" for users. force=True re-imports regardless.
_RESYNC_DOCS = (
	("report", "vat_return"),
	("workspace", "uk_vat"),
)


def resync_standard_ui_records():
	"""Force the module's standard report + workspace back in line with disk, every
	deploy. Defensive: a failure here must never abort migrate."""
	for dt, dn in _RESYNC_DOCS:
		try:
			frappe.reload_doc("zikpro_uk_vat", dt, dn, force=True)
		except Exception:
			frappe.log_error(f"UK VAT: resync {dt}/{dn} failed", "uk_vat_resync")
	frappe.db.commit()


def ensure_vat_setup():
	"""Out-of-the-box setup: if a default company exists, create its VAT Settings and
	seed the VAT accounts + Item Tax Templates (Standard/Reduced/Zero/Exempt/Outside-scope
	for sales and purchases) with their treatments, so a fresh install has a working VAT
	cockpit immediately instead of an empty one. Idempotent; skips silently when no company
	exists yet (a bare ERPNext before the setup wizard) — the cockpit's one-click still works."""
	company = frappe.db.get_single_value("Global Defaults", "default_company")
	if not company:
		return
	try:
		if not frappe.db.exists("VAT Settings", {"company": company}):
			admin_email = frappe.db.get_value("User", "Administrator", "email") or "admin@example.com"
			frappe.get_doc({
				"doctype": "VAT Settings",
				"company": company,
				"billing_contact": admin_email,
				"vat_accounting_scheme": "Standard (Accrual)",
			}).insert(ignore_permissions=True)
			frappe.db.commit()
		# reuse the cockpit's idempotent seeders (accounts, then templates + treatments)
		from zikpro_uk_vat import cockpit
		cockpit.setup_vat_defaults()
	except Exception:
		# best-effort — never fail the install; the user can run the cockpit one-click.
		frappe.log_error(frappe.get_traceback(), "zikpro_uk_vat: ensure_vat_setup")


def after_install():
	ensure_custom_fields()
	enforce_uk_timezone()
	ensure_vat_roles_and_perms()
	resync_standard_ui_records()
	ensure_vat_setup()
