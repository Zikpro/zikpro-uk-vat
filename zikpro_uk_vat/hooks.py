app_name = "zikpro_uk_vat"
app_title = "Zikpro UK VAT"
app_publisher = "Zikpro"
app_description = "UK VAT — file Making Tax Digital returns to HMRC (free base edition)"
app_email = "info@zikpro.com"
app_license = "mit"
# This app extends ERPNext (doc_events + custom fields + doctype_js on Sales/Purchase
# Invoice, VAT item-tax-templates). Declare it so a bench deploy pulls/refuses without it —
# else customer deploys fail with "required app not found" (flagged by Frappe marketplace).
required_apps = ["frappe/erpnext"]

app_version = "2.1.3"

# Show the UK VAT app on the desk Apps launcher (sprint-1: app was unreachable — v16 dropped this)
add_to_apps_screen = [
    {
        "name": "zikpro_uk_vat",
        "logo": "/assets/zikpro_uk_vat/uk_vat.svg",
        "title": "UK VAT",
        "route": "/app/vat-cockpit",
        "has_permission": "zikpro_uk_vat.permissions.check_app_permission",
    }
]

# Includes in <head>
# ------------------

# include js, css files in header of desk.html
# app_include_css = "/assets/zikpro_uk_vat/css/zikpro_uk_vat.css"
# app_include_js = "/assets/zikpro_uk_vat/js/zikpro_uk_vat.js"
app_include_js = [
    "/assets/zikpro_uk_vat/js/hmrc_fraud_prevention.js"
]

whitelisted_methods = [
    "zikpro_uk_vat.utils.update_client_info"
]

# NOTE: the Workspace + Desktop Icon are intentionally NOT fixtures. A fixture
# only puts the record in the database; frappe.model.sync.remove_orphan_entities
# runs on every `bench migrate` and deletes any public Workspace / standard
# Desktop Icon that has no matching on-disk file under a `workspace/`/
# `desktop_icon/` folder in an installed app — it does not know fixtures exist.
# That is why "UK VAT" never survived a migrate: fixture sync created it, then
# orphan-cleanup deleted it, every single run, on both localhost and Cloud.
# Fixed by using Frappe's actual native mechanism instead:
#   zikpro_uk_vat/workspace/uk_vat/uk_vat.json
#   zikpro_uk_vat/desktop_icon/uk_vat.json
# — the same files-on-disk convention every ERPNext/HRMS workspace uses, which
# is why theirs never disappear.

# M0-A SEC-12: the legacy desk OAuth flow (tokens in the URL, unvalidated
# `state`, guest-writable save_tokens — code-review P0-2/P0-3) is superseded by
# the cockpit's complete_oauth. api.py is FROZEN, so instead of deleting the
# functions the endpoints are neutralised here: every call routes to a stub
# that refuses and points at the cockpit Connect tab. api.py stays byte-frozen.
_DISABLED_LEGACY_OAUTH = "zikpro_uk_vat.security.disabled_legacy_oauth"
override_whitelisted_methods = {
    "zikpro_uk_vat.api.start_oauth_flow": _DISABLED_LEGACY_OAUTH,
    "zikpro_uk_vat.api.oauth_callback": _DISABLED_LEGACY_OAUTH,
    "zikpro_uk_vat.api.save_tokens": _DISABLED_LEGACY_OAUTH,
    # SEC-13 (broadcast B27 — endpoint twins): `validate_fraud_headers` exists in
    # BOTH api.py and cockpit.py, so both are separately reachable. The cockpit
    # one is _require()-gated and returns only HMRC's verdict; the frozen api.py
    # twin returns `"headers": headers` — which contains
    # `Authorization: Bearer <access_token>` — to ANY authenticated caller, and
    # msgprints them in developer_mode. Route the leaky twin to the safe one.
    "zikpro_uk_vat.api.validate_fraud_headers": "zikpro_uk_vat.cockpit.validate_fraud_headers",
}

# override_whitelisted_methods = {
#     "frappe.twofactor.confirm_otp_token": "zikpro_uk_vat.utils.patched_confirm_otp_token"
# }
# sprint-1: REMOVED broken login override — target `custom_login` is not implemented
# (commented out in utils.py), which broke the /login path (CSRF/400s). MFA timestamp
# is handled by custom_post_login (patch_login_manager) which stays FROZEN per FPH rule.
# override_whitelisted_methods = {
#     "login": "zikpro_uk_vat.utils.custom_login"
# }

after_migrate = [
    # Self-heal custom fields on every deploy. A one-shot patch cannot repair a
    # site once Patch Log marks it done — ziktax.frappe.cloud was missing
    # Company.uk_vat_registration_number and every cockpit load threw 1054.
    "zikpro_uk_vat.install.ensure_custom_fields",
    # B37: pin the timezone on every deploy too — a fresh cloud site inherits
    # Frappe's Asia/Kolkata default and mis-dates transactions across VAT periods.
    "zikpro_uk_vat.install.enforce_uk_timezone",
    # Fill blank System Settings language/number_format so ERPNext tax calc doesn't
    # crash (UnboundLocalError in frappe core's get_locale_value) on the first invoice
    # — bites sites provisioned without the setup wizard. Only fills blanks.
    "zikpro_uk_vat.install.ensure_locale_defaults",
    # SEC-11: self-heal the preparer/approver roles + DocPerms on every deploy
    # (fresh installs skip seed patches, and cloud migrate skips DocPerm JSON).
    "zikpro_uk_vat.install.ensure_vat_roles_and_perms",
    # Force the standard report + workspace back in line with disk. import_file
    # skips re-importing when the DB copy isn't older, so a diverged site keeps a
    # stale ref_doctype / phantom workspace links forever (seen on ziktax.frappe.cloud).
    "zikpro_uk_vat.install.resync_standard_ui_records",
]

# after_install = "zikpro_uk_vat.utils.create_initial_records"

after_install = "zikpro_uk_vat.install.after_install"

# boot_session = "zikpro_uk_vat.utils.patch_twofactor"

socketio_handlers = [
    {
        "event": "mfa_updated",
        "handler": "zikpro_uk_vat.utils.clear_user_cache"
    }
]


# include js, css files in header of web template
# web_include_css = "/assets/zikpro_uk_vat/css/zikpro_uk_vat.css"
# web_include_js = "/assets/zikpro_uk_vat/js/zikpro_uk_vat.js"

# include custom scss in every website theme (without file extension ".scss")
# website_theme_scss = "zikpro_uk_vat/public/scss/website"

# include js, css files in header of web form
# webform_include_js = {"doctype": "public/js/doctype.js"}
# webform_include_css = {"doctype": "public/css/doctype.css"}

# include js in page
# page_js = {"page" : "public/js/file.js"}

# include js in doctype views
# doctype_js = {"doctype" : "public/js/doctype.js"}
# VAT Activity panel — renders the invoice's VAT Ledger events read-only.
doctype_js = {
    "Sales Invoice": "public/js/vat_activity.js",
    "Purchase Invoice": "public/js/vat_activity.js",
}
# doctype_list_js = {"doctype" : "public/js/doctype_list.js"}
# doctype_tree_js = {"doctype" : "public/js/doctype_tree.js"}
# doctype_calendar_js = {"doctype" : "public/js/doctype_calendar.js"}

# Svg Icons
# ------------------
# include app icons in desk
# app_include_icons = "zikpro_uk_vat/public/icons.svg"

# Home Pages
# ----------

# application home page (will override Website Settings)
# home_page = "login"

# website user home page (by Role)
# role_home_page = {
# 	"Role": "home_page"
# }

# Generators
# ----------

# automatically create page for each record of this doctype
# website_generators = ["Web Page"]

# Jinja
# ----------

# add methods and filters to jinja environment
# jinja = {
# 	"methods": "zikpro_uk_vat.utils.jinja_methods",
# 	"filters": "zikpro_uk_vat.utils.jinja_filters"
# }

# Installation
# ------------

# before_install = "zikpro_uk_vat.install.before_install"
# after_install = "zikpro_uk_vat.install.after_install"
# This ensures patches run on install/update
# after_install = "zikpro_uk_vat.setup.after_install"
# after_migrate = "zikpro_uk_vat.setup.after_migrate"

# Uninstallation
# ------------

# before_uninstall = "zikpro_uk_vat.uninstall.before_uninstall"
# after_uninstall = "zikpro_uk_vat.uninstall.after_uninstall"

# Integration Setup
# ------------------
# To set up dependencies/integrations with other apps
# Name of the app being installed is passed as an argument

# before_app_install = "zikpro_uk_vat.utils.before_app_install"
# after_app_install = "zikpro_uk_vat.utils.after_app_install"

# Integration Cleanup
# -------------------
# To clean up dependencies/integrations with other apps
# Name of the app being uninstalled is passed as an argument

# before_app_uninstall = "zikpro_uk_vat.utils.before_app_uninstall"
# after_app_uninstall = "zikpro_uk_vat.utils.after_app_uninstall"

# Desk Notifications
# ------------------
# See frappe.core.notifications.get_notification_config

# notification_config = "zikpro_uk_vat.notifications.get_notification_config"

# Permissions
# -----------
# Permissions evaluated in scripted ways

# permission_query_conditions = {
# 	"Event": "frappe.desk.doctype.event.event.get_permission_query_conditions",
# }
#
# has_permission = {
# 	"Event": "frappe.desk.doctype.event.event.has_permission",
# }

# DocType Class
# ---------------
# Override standard doctype classes

# override_doctype_class = {
# 	"ToDo": "custom_app.overrides.CustomToDo"
# }

# Document Events
# ---------------
# Hook on document methods and events
doc_events = {
    "User": {
        "on_login": "zikpro_uk_vat.utils.set_default_client_info"
    },
    # M0-A SEC-3: the frozen api.py logs the full HMRC request headers (Bearer
    # token + Gov-Client PII) to Error Log on every call (api.py:279). Redact
    # every Error Log at insert time instead of editing the frozen line — this
    # also covers any OTHER leaky logger, present or future.
    "Error Log": {
        "before_insert": "zikpro_uk_vat.security.redact_error_log"
    },
    # FL-02 (P1-4): a file attached to a VAT document must never be public — a public
    # file is served with no permission check and would leak cross-tenant once the
    # broker serves many tenants. Force is_private on insert and on save.
    "File": {
        # before_validate so is_private is set BEFORE the physical write + URL check
        # (flipping it later would leave the bytes in the public path — "File URL incorrect").
        "before_validate": "zikpro_uk_vat.security.enforce_private_vat_files",
    },
    # VAT Ledger (project-uk-mtd-vat-ledger-design): record the immutable per-event
    # sub-ledger from the source documents. Accrued on invoice submit; Realised on
    # payment allocation (cash basis); clear+re-derive on cancel/amend so nothing
    # is silently doubled.
    "Sales Invoice": {
        "on_submit": "zikpro_uk_vat.vat_ledger.record_invoice_accrual",
        "on_cancel": "zikpro_uk_vat.vat_ledger.clear_source_entries",
    },
    "Purchase Invoice": {
        "on_submit": "zikpro_uk_vat.vat_ledger.record_invoice_accrual",
        "on_cancel": "zikpro_uk_vat.vat_ledger.clear_source_entries",
    },
    "Payment Entry": {
        "on_submit": [
            "zikpro_uk_vat.vat_ledger.record_payment_realisation",
            # HMRC 700/18: repay bad-debt relief when the recovered sale is fully paid.
            "zikpro_uk_vat.vat_adjustment_schedule.reverse_bad_debt_on_recovery",
        ],
        "on_cancel": "zikpro_uk_vat.vat_ledger.clear_source_entries",
    },
}

# doc_events = {
# 	"*": {
# 		"on_update": "method",
# 		"on_cancel": "method",
# 		"on_trash": "method"
# 	}
# }

# Scheduled Tasks
# ---------------

# Materialise due VAT Adjustment Schedules (bad-debt 6mo trigger / CGS intervals /
# PE annual) into real VAT Adjustments once their trigger date arrives.
scheduler_events = {
    "daily": [
        "zikpro_uk_vat.vat_adjustment_schedule.generate_due_adjustments",
    ],
}

# scheduler_events = {
# 	"all": [
# 		"zikpro_uk_vat.tasks.all"
# 	],
# 	"daily": [
# 		"zikpro_uk_vat.tasks.daily"
# 	],
# 	"hourly": [
# 		"zikpro_uk_vat.tasks.hourly"
# 	],
# 	"weekly": [
# 		"zikpro_uk_vat.tasks.weekly"
# 	],
# 	"monthly": [
# 		"zikpro_uk_vat.tasks.monthly"
# 	],
# }

# Testing
# -------

# before_tests = "zikpro_uk_vat.install.before_tests"

# Overriding Methods
# ------------------------------
#
# override_whitelisted_methods = {
# 	"frappe.desk.doctype.event.event.get_events": "zikpro_uk_vat.event.get_events"
# }
#
# each overriding function accepts a `data` argument;
# generated from the base implementation of the doctype dashboard,
# along with any modifications made in other Frappe apps
# override_doctype_dashboards = {
# 	"Task": "zikpro_uk_vat.task.get_dashboard_data"
# }

# exempt linked doctypes from being automatically cancelled
#
# auto_cancel_exempted_doctypes = ["Auto Repeat"]

# Ignore links to specified DocTypes when deleting documents
# -----------------------------------------------------------

# ignore_links_on_delete = ["Communication", "ToDo"]

# Request Events
# ----------------
# FPH-1: re-hydrate the user's real browser client_info into session.data on every
# request, because frappe.session.data does not persist across requests on Frappe Cloud
# — without this the frozen get_fraud_prevention_headers() never sees the real device
# values and Gov-Client-Screens is stuck at the 1920x1080 fallback.
before_request = ["zikpro_uk_vat.utils.hydrate_client_info"]
# after_request = ["zikpro_uk_vat.utils.after_request"]

# Job Events
# ----------
# before_job = ["zikpro_uk_vat.utils.before_job"]
# after_job = ["zikpro_uk_vat.utils.after_job"]

# User Data Protection
# --------------------

# user_data_fields = [
# 	{
# 		"doctype": "{doctype_1}",
# 		"filter_by": "{filter_by}",
# 		"redact_fields": ["{field_1}", "{field_2}"],
# 		"partial": 1,
# 	},
# 	{
# 		"doctype": "{doctype_2}",
# 		"filter_by": "{filter_by}",
# 		"partial": 1,
# 	},
# 	{
# 		"doctype": "{doctype_3}",
# 		"strict": False,
# 	},
# 	{
# 		"doctype": "{doctype_4}"
# 	}
# ]

# Authentication and authorization
# --------------------------------

# auth_hooks = [
# 	"zikpro_uk_vat.auth.validate"
# ]

# Automatically update python controller files with type annotations for this app.
# export_python_type_annotations = True

# default_log_clearing_doctypes = {
# 	"Logging DocType Name": 30  # days to retain logs
# }

