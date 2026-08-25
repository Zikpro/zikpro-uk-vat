"""Backend API for the UK VAT SPA cockpit.

Kept separate from api.py (which holds the frozen HMRC/FPH + approved production
OAuth code) so the cockpit can evolve without touching HMRC-facing logic. All
methods here are read-only or build a redirect URL; none change what HMRC receives.
"""

import re
import time
import urllib.parse
from decimal import ROUND_HALF_UP, Decimal

import frappe
import requests
from requests.auth import HTTPBasicAuth

# Sandbox endpoints for the cockpit's dev/test Connect flow. (api.py holds the
# approved PRODUCTION endpoints — untouched.)
HMRC_SANDBOX_BASE = "https://test-api.service.hmrc.gov.uk"
HMRC_SANDBOX_AUTH_URL = f"{HMRC_SANDBOX_BASE}/oauth/authorize"
HMRC_SANDBOX_TOKEN_URL = f"{HMRC_SANDBOX_BASE}/oauth/token"
# Production HMRC base. Environment is a per-site switch AND a branch default (see
# _hmrc_production): main defaults to production, develop to sandbox. Force either with:
#   bench --site <site> set-config hmrc_sandbox 1      (force sandbox)
#   bench --site <site> set-config hmrc_production 1   (force production)
# In broker mode the broker's own sandbox_mode must match (the token is issued for that env).
HMRC_PROD_BASE = "https://api.service.hmrc.gov.uk"


def _hmrc_production():
	"""True when this deployment files against LIVE HMRC.

	Branch default (BRANCH-DIVERGENT — the only intentional difference between develop and
	main): on **develop** the default is the HMRC SANDBOX, so a staging/test deploy can never
	accidentally file a real return; on **main** (marketplace/production) the default is
	PRODUCTION. Either branch can override per-site: `hmrc_sandbox=1` forces sandbox and
	`hmrc_production=1` forces production. Keep this line in sync-by-intent on promotion:
	develop returns False, main returns True.
	"""
	conf = frappe.conf
	if conf.get("hmrc_sandbox"):
		return False
	if conf.get("hmrc_production") is not None:
		return bool(conf.get("hmrc_production"))
	return True  # main default: PRODUCTION (develop default: False)


def _hmrc_base():
	"""The HMRC API base for live calls (obligations/submit/liabilities/payments) — production
	or sandbox per the deployment switch. (The FPH validator is a sandbox-only test endpoint and
	always uses the sandbox base.)"""
	return HMRC_PROD_BASE if _hmrc_production() else HMRC_SANDBOX_BASE


def _hmrc_authorize_url():
	"""OAuth authorize endpoint — production or sandbox per the deployment switch."""
	return f"{_hmrc_base()}/oauth/authorize"


def _hmrc_token_url():
	"""OAuth token endpoint — production or sandbox per the deployment switch."""
	return f"{_hmrc_base()}/oauth/token"


def _hmrc_environment():
	return "Production" if _hmrc_production() else "Sandbox"


def _two_factor_active(user):
	"""True if two-factor auth is enabled for this user (System Settings + role rules). A live
	filing requires it so the frozen Gov-Client-Multi-Factor header reflects a genuine MFA event
	(every login is then a real 2FA event). Frappe still has no queryable 2FA timestamp, so this
	uses Frappe's own enablement check rather than trusting the per-login timestamp store."""
	try:
		from frappe.twofactor import two_factor_is_enabled
		return bool(two_factor_is_enabled(user=user))
	except Exception:
		return False

VAT_SETTINGS = "VAT Settings"
GLOBAL_DEFAULTS = "Global Defaults"
SALES_INVOICE = "Sales Invoice"
PURCHASE_INVOICE = "Purchase Invoice"
SALES_TAXES = "Sales Taxes and Charges"
PURCHASE_TAXES = "Purchase Taxes and Charges"
MTD_VAT_ACCOUNT = "MTD VAT Account"
MTD_VAT_TREATMENT = "MTD VAT Treatment"
ITEM_TAX_TEMPLATE = "Item Tax Template"
UK_MTD_VAT_RETURN = "UK MTD VAT Return"
VAT_ADJUSTMENT = "VAT Adjustment"
AWAITING_APPROVAL = "Awaiting Approval"
_MSG_NO_SETTINGS = "No VAT Settings found for the default company."
# The VAT treatments a line can carry (kept in sync with the MTD VAT Treatment
# DocType's vat_treatment Select). Zero/Exempt/Outside-scope all carry 0% VAT —
# only the treatment distinguishes them for Box 6/7.
VAT_TREATMENTS = ("Standard rated", "Reduced rated", "Zero rated", "Exempt", "Outside scope")
_OAUTH_STATE_PREFIX = "vat_oauth_state:"
_LOG_DASHBOARD = "UK VAT Dashboard"
# The product's central OAuth broker. A marketplace install with no HMRC client
# credentials of its own self-registers here on first Connect and files through it.
DEFAULT_BROKER_URL = "https://zikops.frappe.cloud"
_MSG_NOT_CONNECTED = "Not connected to HMRC."
_MSG_NO_VRN = "No VAT registration number set on the Company."
_MSG_NO_HMRC = "Could not reach HMRC. Please try again."
_MSG_EXPIRED = "Your HMRC connection has expired. Please reconnect on the Connect tab."


def _require(ptype="read"):
	"""Server-side permission gate for every whitelisted cockpit endpoint.

	@frappe.whitelist() alone only proves the caller is logged in — it does NOT
	authorise them. VAT config and filing a legal return must be gated, so we check
	the VAT Settings DocType permission (read for views, write for mutations).
	"""
	if not frappe.has_permission(VAT_SETTINGS, ptype):
		frappe.throw(
			frappe._("You are not permitted to access UK VAT."), frappe.PermissionError
		)


PREPARER_ROLE = "UK VAT Preparer"
APPROVER_ROLE = "UK VAT Approver"


def _has_role(role):
	"""System Manager is a superuser for the ROLE gate (can do either job); the
	segregation-of-duties (not-self) check is enforced separately and applies to
	everyone, including System Manager."""
	roles = set(frappe.get_roles())
	return role in roles or "System Manager" in roles


def _require_role(role, action):
	if not _has_role(role):
		frappe.throw(
			frappe._("You need the '{0}' role to {1}.").format(role, action),
			frappe.PermissionError,
		)


@frappe.whitelist()
def user_vat_roles():
	"""Which VAT roles the current user holds — lets the UI show the right actions."""
	roles = set(frappe.get_roles())
	is_sysmgr = "System Manager" in roles
	return {
		# A dedicated preparer/approver holds ONLY that role. System Manager is a
		# superuser (admin direct-file) and is reported separately so the UI can
		# offer the one-shot path without forcing a two-person flow on a solo admin.
		"is_preparer": PREPARER_ROLE in roles,
		"is_approver": APPROVER_ROLE in roles,
		"is_sysmgr": is_sysmgr,
		"user": frappe.session.user,
	}


# HMRC error codes worth translating — the raw code is accurate but means nothing
# to an accountant. Anything unmapped falls through to HMRC's own message text.
_HMRC_CODE_HELP = {
	"CLIENT_OR_AGENT_NOT_AUTHORISED": (
		"HMRC does not recognise this authorisation for VAT number {vrn}. "
		"Either the VAT number is wrong, or the HMRC account you authorised is not "
		"enrolled for MTD VAT for it. Re-authorise on the Connect tab."
	),
	"VRN_INVALID": "VAT number {vrn} is not a valid format. Check it on the Company record.",
	"NOT_FOUND": "HMRC has no VAT record for {vrn} for this period.",
	"INVALID_DATE_RANGE": "The date range is not one HMRC accepts for this VAT number.",
	"MESSAGE_THROTTLED_OUT": "HMRC is rate-limiting us. Please try again shortly.",
}


# HMRC codes that mean "this authorisation will not work", as opposed to a
# transient or data-specific failure. Seeing one is proof the connection is not
# usable, whatever the stored tokens look like.
_HMRC_AUTH_ERRORS = {"CLIENT_OR_AGENT_NOT_AUTHORISED", "VRN_INVALID"}


def _hmrc_code(resp):
	"""The HMRC error code from a failed response, flat or nested under errors[]."""
	try:
		body = resp.json()
	except Exception:
		return None
	for err in body.get("errors") or []:
		if err.get("code"):
			return err["code"]
	return body.get("code")


def _hmrc_error(resp, vrn=None):
	"""User-facing message for a failed HMRC call.

	HMRC returns a JSON body carrying `code` and `message` (sometimes nested under
	`errors[]`). Collapsing that to "HMRC returned 403." throws away the only
	information that tells the user what to actually DO, so parse it out and, for
	the codes that have a real-world cause, say what that cause is.
	"""
	if resp.status_code == 401:
		return _MSG_EXPIRED

	code = _hmrc_code(resp)
	message = None
	try:
		body = resp.json()
		message = body.get("message")
		for err in body.get("errors") or []:
			message = err.get("message") or message
			break
	except Exception:
		pass

	# Raw detail is already captured by frappe.log_error at each call site.
	if code and code in _HMRC_CODE_HELP:
		return _HMRC_CODE_HELP[code].format(vrn=vrn or "this VAT number")
	if message:
		return f"HMRC returned {resp.status_code}: {message}"
	return f"HMRC returned {resp.status_code}."


def _connection(company=None):
	"""Single source of truth for connection state — column-safe.

	NOTE: VAT Settings has no `status`/`token_expiry` column, so we derive state
	from what actually exists (client credentials + access token presence). Do not
	query a `status` field here — it does not exist and would raise Unknown column.
	"""
	company = company or frappe.db.get_single_value(GLOBAL_DEFAULTS, "default_company")
	vrn = settings_name = redirect_url = authorised_vrn = None
	client_configured = has_token = False
	if company:
		vrn = frappe.db.get_value("Company", company, "uk_vat_registration_number")
		settings_name = frappe.db.get_value(VAT_SETTINGS, {"company": company}, "name")
		if settings_name:
			row = frappe.db.get_value(
				VAT_SETTINGS,
				settings_name,
				["client_id", "access_token", "redirect_url", "authorised_vrn"],
				as_dict=True,
			)
			client_configured = bool(row.get("client_id"))
			has_token = bool(row.get("access_token"))
			redirect_url = row.get("redirect_url")
			authorised_vrn = row.get("authorised_vrn")

	# A token is not the same thing as a working authorisation. HMRC grants are tied
	# to the VAT number the user was enrolled for at Connect; if the Company's VRN has
	# since changed, the tokens stay valid but every call 403s. Saying "Connected"
	# there is simply untrue, so treat a known mismatch as its own state.
	# authorised_vrn is empty on connections made before it was recorded — that is
	# unknown, not wrong, so those keep the previous behaviour.
	vrn_mismatch = bool(has_token and authorised_vrn and vrn and authorised_vrn != vrn)

	connected = has_token and not vrn_mismatch
	if vrn_mismatch:
		state = "Re-authorisation needed"
	elif has_token:
		state = "Connected"
	elif not settings_name or not client_configured:
		state = "Not configured"
	else:
		state = "Not connected"

	return {
		"company": company or None,
		"vrn": vrn or None,
		"settings": settings_name,
		"client_configured": client_configured,
		"has_token": has_token,
		"connected": connected,
		"state": state,
		"authorised_vrn": authorised_vrn or None,
		"vrn_mismatch": vrn_mismatch,
		"redirect_url": redirect_url or None,
	}


@frappe.whitelist()
def get_dashboard_data():
	"""Connection status + company/VRN for the cockpit Dashboard.

	Data-truthful: reflects the real VAT Settings/Company state; never fabricates
	figures. If not authorised, the UI shows a 'connect' state.
	"""
	_require()
	c = _connection()
	# Broker-only build: readiness to connect = a broker signup token is stored (not client creds).
	signup_set = bool(c["settings"]) and bool(frappe.db.get_value(VAT_SETTINGS, c["settings"], "broker_signup_token"))
	already_tenant = bool(c["settings"]) and bool(frappe.db.get_value(VAT_SETTINGS, c["settings"], "broker_tenant_id"))
	return {
		"company": c["company"],
		"vrn": c["vrn"],
		"vat_settings": c["settings"],
		"status": c["state"],
		"connected": c["connected"],
		"authorised_vrn": c["authorised_vrn"],
		"vrn_mismatch": c["vrn_mismatch"],
		"client_configured": c["client_configured"],
		"broker_only": True,
		"signup_token_set": signup_set,
		# Ready to press Connect: broker mode needs a signup token (or an existing tenant).
		"can_connect": signup_set or already_tenant,
		# Sandbox vs Production — driven by the deployment switch (site config hmrc_production).
		"environment": _hmrc_environment(),
		# Gate premium UI (PE-annual / CGS engines) — Base shows an upsell, Pro shows the tools.
		"pro_installed": _pro_installed(),
	}


@frappe.whitelist()
def get_connection_status():
	"""Richer connection detail for the Connect screen (read-only, truthful).

	Broker-only build: the Connect panel branches on `broker_only`/`signup_token_set`/
	`can_connect` (identical to get_dashboard_data). Without these the Vue template falls
	back to the direct-mode branch and shows a "add client ID/secret" dead end — Settings
	has no client-credential fields in a broker build. Keep this in lockstep with
	get_dashboard_data().
	"""
	_require()
	c = _connection()
	signup_set = bool(c["settings"]) and bool(
		frappe.db.get_value(VAT_SETTINGS, c["settings"], "broker_signup_token")
	)
	already_tenant = bool(c["settings"]) and bool(
		frappe.db.get_value(VAT_SETTINGS, c["settings"], "broker_tenant_id")
	)
	# In a broker build there is no client_id, so _connection() reports "Not configured".
	# That's a direct-mode label; for broker mode, readiness is a signup token / tenant.
	if not c["connected"] and not c["vrn_mismatch"]:
		c["state"] = "Not connected" if (signup_set or already_tenant) else "Not configured"
	c["broker_only"] = True
	c["signup_token_set"] = signup_set
	c["can_connect"] = signup_set or already_tenant
	# The cockpit header badge reads conn.environment — without it the badge falls back to
	# 'Sandbox' even on a production deployment. Keep in lockstep with get_dashboard_data.
	c["environment"] = _hmrc_environment()
	return c


ACCRUAL = "Standard (Accrual)"
CASH = "Cash Accounting"
FLAT_RATE = "Flat Rate Scheme"

# VAT Notice 733 — Flat Rate Scheme turnover thresholds (GBP). Join if taxable
# turnover (ex-VAT) for the next year is expected <= £150k; must leave once total
# business income INCLUDING VAT in a year exceeds £230k.
FRS_JOIN_THRESHOLD = 150_000.0
FRS_LEAVE_THRESHOLD = 230_000.0

# VAT Notice 731 — Cash Accounting Scheme turnover thresholds (GBP, taxable turnover
# excluding VAT). Join only if estimated next-12-months <= JOIN; must leave once the
# past year exceeds LEAVE. We proxy "estimated" with trailing-12-month sales.
CASH_JOIN_THRESHOLD = 1_350_000.0
CASH_LEAVE_THRESHOLD = 1_600_000.0
# Notice 731 §6.2: an invoice whose payment falls due 6+ months after the invoice
# date is excluded from cash accounting (accrual-based).
CASH_EXCLUSION_DUE_MONTHS = 6


def _current_vat_year():
	"""The VAT year (ERPNext Fiscal Year) that today falls in — used for the
	scheme mid-year lock."""
	from frappe.utils import nowdate

	fy = frappe.db.get_value(
		"Fiscal Year",
		{"year_start_date": ["<=", nowdate()], "year_end_date": [">=", nowdate()]},
		"name",
	)
	return fy or nowdate()[:4]


def _scheme(settings_name=None):
	settings_name = settings_name or frappe.db.get_value(
		VAT_SETTINGS, {"company": frappe.db.get_single_value(GLOBAL_DEFAULTS, "default_company")}, "name"
	)
	if not settings_name:
		return ACCRUAL
	return frappe.db.get_value(VAT_SETTINGS, settings_name, "vat_accounting_scheme") or ACCRUAL


@frappe.whitelist()
def get_settings():
	"""VAT settings for the in-cockpit Settings screen (no desk form needed)."""
	_require()
	c = _connection()
	row = {}
	if c["settings"]:
		row = (
			frappe.db.get_value(
				VAT_SETTINGS,
				c["settings"],
				["billing_contact", "vat_accounting_scheme", "scheme_effective_year", "flat_rate_percentage", "client_id", "redirect_url"],
				as_dict=True,
			)
			or {}
		)
	cur = _current_vat_year()
	eff = row.get("scheme_effective_year")
	# UX-1: the account + treatment mappings are what turn invoices into box
	# figures. Without them every box reads a silent £0. Surface them (and whether
	# they are configured) in the Settings screen so a fresh site can map them
	# in-cockpit — the desk form is off-limits per the locked UX rule.
	accounts, treatments = [], []
	if c["settings"]:
		accounts = frappe.get_all(
			MTD_VAT_ACCOUNT,
			filters={"parent": c["settings"], "parenttype": VAT_SETTINGS},
			fields=["vat_type", "account", "description"],
			order_by="idx",
		)
		treatments = frappe.get_all(
			MTD_VAT_TREATMENT,
			filters={"parent": c["settings"], "parenttype": VAT_SETTINGS},
			fields=["item_tax_template", "vat_treatment", "in_box_6_7"],
			order_by="idx",
		)
	# NOTE: client_secret / tokens are Password fields — never returned to the UI.
	return {
		"settings": c["settings"],
		"company": c["company"],
		"vrn": c["vrn"],
		"billing_contact": row.get("billing_contact"),
		"vat_accounting_scheme": row.get("vat_accounting_scheme") or ACCRUAL,
		"scheme_effective_year": eff,
		"current_vat_year": cur,
		"scheme_locked": bool(eff) and eff == cur,
		# A1-1: Notice 731 turnover eligibility, so the cockpit can show whether Cash
		# Accounting is available before the user tries to switch.
		"cash_turnover_12m": round(_cash_taxable_turnover(c["company"]), 2) if c["company"] else 0,
		"cash_eligible": c["company"] is not None and _cash_eligibility_error(c["company"]) is None,
		"cash_join_threshold": CASH_JOIN_THRESHOLD,
		"client_id": row.get("client_id"),
		"redirect_url": row.get("redirect_url"),
		# A8: broker-only product hides the direct-credential inputs when in broker mode.
		"use_broker": bool(frappe.db.get_value(VAT_SETTINGS, c["settings"], "use_broker")) if c["settings"] else False,
		# Whether a broker signup token is stored (never expose the value).
		"signup_token_set": bool(c["settings"]) and bool(frappe.db.get_value(VAT_SETTINGS, c["settings"], "broker_signup_token")),
		# Broker-only marketplace build: never show the direct HMRC-credential inputs.
		"broker_only": True,
		"secret_set": bool(c["settings"]) and bool(frappe.db.get_value(VAT_SETTINGS, c["settings"], "client_secret")),
		"client_configured": c["client_configured"],
		"connected": c["connected"],
		"schemes": [ACCRUAL, CASH, FLAT_RATE],
		"vat_accounts": accounts,
		"vat_treatments": treatments,
		"accounts_configured": bool(accounts),
		"treatments_configured": bool(treatments),
		# Candidate ledger accounts the user can map to (so the mapping is editable
		# in-cockpit, not only auto-configured). Tax accounts are where VAT posts;
		# offer them for the company. UX-fix: auto-configure can pick the wrong
		# account, and there was previously no in-cockpit way to correct it.
		"account_options": (
			frappe.get_all(
				"Account",
				filters={"company": c["company"], "account_type": "Tax", "is_group": 0},
				pluck="name",
				order_by="name",
			)
			if c["company"]
			else []
		),
		"account_types": [OUTPUT_VAT, INPUT_VAT],
		# Editable treatments: the Item Tax Templates that can be classified, and the
		# valid treatment values (kept in sync with the DocType Select).
		"template_options": (
			frappe.get_all(
				ITEM_TAX_TEMPLATE,
				filters={"company": c["company"]},
				pluck="name",
				order_by="name",
			)
			if c["company"]
			else []
		),
		"treatment_options": list(VAT_TREATMENTS),
	}


def _cash_taxable_turnover(company):
	"""Trailing-12-month taxable (standard/reduced/zero-rated) turnover ex-VAT — a
	proxy for Notice 731's 'estimated taxable turnover'. Uses submitted, non-return
	Sales Invoice net; exempt/outside-scope net is not counted."""
	from frappe.utils import add_days, nowdate

	filters = {"docstatus": 1, "is_return": 0,
			   "posting_date": [">=", add_days(nowdate(), -365)]}
	if company:
		filters["company"] = company
	rows = frappe.get_all(SALES_INVOICE, filters=filters, fields=["name", "base_net_total"])
	if not rows:
		return 0.0
	names = [r.name for r in rows]
	item_child = f"{SALES_INVOICE} Item"
	# Outside-scope net is not taxable turnover; exempt supplies aren't separately
	# modelled yet, so this proxy is slightly conservative (counts exempt as taxable).
	exempt = _excluded_net_by_parent(item_child, SALES_INVOICE, names, _outside_scope_templates())
	return sum(float(r.base_net_total or 0) - exempt.get(r.name, 0.0) for r in rows)


def _cash_eligibility_error(company):
	"""Notice 731 §2: you may JOIN Cash Accounting only if estimated taxable turnover
	for the next 12 months won't exceed £1.35m. We proxy that with trailing-12-month
	turnover and refuse joining above it (and note the £1.6m mandatory-leave line).
	Returns an error dict or None."""
	turnover = _cash_taxable_turnover(company)
	if turnover > CASH_JOIN_THRESHOLD:
		return {
			"ok": False,
			"message": (
				f"Cash Accounting is not available: your taxable turnover in the last 12 "
				f"months (£{turnover:,.0f}) exceeds the £{CASH_JOIN_THRESHOLD:,.0f} limit for "
				f"joining the scheme (VAT Notice 731; you must leave once it passes "
				f"£{CASH_LEAVE_THRESHOLD:,.0f})."
			),
		}
	return None


def _frs_eligibility_error(company):
	"""Notice 733 §3: join the Flat Rate Scheme only if taxable turnover (ex-VAT) for
	the next 12 months is expected <= £150k. Proxy with trailing-12-month turnover and
	refuse joining above it (£230k VAT-inclusive is the mandatory-leave line)."""
	turnover = _cash_taxable_turnover(company)
	if turnover > FRS_JOIN_THRESHOLD:
		return {
			"ok": False,
			"message": (
				f"The Flat Rate Scheme is not available: your taxable turnover in the last "
				f"12 months (£{turnover:,.0f}) exceeds the £{FRS_JOIN_THRESHOLD:,.0f} join "
				f"limit (VAT Notice 733; you must leave once total income including VAT "
				f"passes £{FRS_LEAVE_THRESHOLD:,.0f})."
			),
		}
	return None


def _switch_date(effective_year):
	"""Start date of the VAT year the new scheme takes effect from (the switch date).
	Invoices dated before this belong to the OLD scheme."""
	return frappe.db.get_value("Fiscal Year", effective_year, "year_start_date")


def _outstanding_vat_net(doctype, switch, company):
	"""Notice 731 §6.4: VAT + net still UNPAID on invoices dated before the switch —
	the amount that would otherwise fall between the two schemes. Apportioned by the
	outstanding fraction, same VAT/net rules as the cash engine."""
	rows = frappe.get_all(
		doctype,
		filters={"docstatus": 1, "company": company, "posting_date": ["<", switch],
				 "outstanding_amount": [">", 0]},
		fields=["name", "base_net_total", "base_grand_total", "outstanding_amount"],
	)
	if not rows:
		return 0.0, 0.0
	names = [r.name for r in rows]
	child = SALES_TAXES if doctype == SALES_INVOICE else PURCHASE_TAXES
	item_child = f"{doctype} Item"
	accts = vat_accounts(OUTPUT_VAT if doctype == SALES_INVOICE else INPUT_VAT)
	vat_map = _vat_by_parent(child, doctype, names, accts)
	excluded = _excluded_net_by_parent(item_child, doctype, names, _outside_scope_templates())
	tot_vat = tot_net = 0.0
	for r in rows:
		grand = float(r.base_grand_total or 0)
		if not grand:
			continue
		frac = float(r.outstanding_amount or 0) / grand
		tot_vat += vat_map.get(r.name, 0.0) * frac
		tot_net += (float(r.base_net_total or 0) - excluded.get(r.name, 0.0)) * frac
	return _r2(tot_vat), _r2(tot_net)


def _post_transition_adjustment(company, vat_box, amount, switch, reason):
	"""Create + submit a Scheme-Change VAT Adjustment (a box delta). Skips £0."""
	if not amount:
		return
	doc = frappe.new_doc(VAT_ADJUSTMENT)
	doc.company = company
	doc.posting_date = switch
	doc.adjustment_type = SCHEME_CHANGE_TYPE
	doc.vat_box = vat_box
	doc.amount = amount
	doc.reason = reason
	doc.notice_ref = "VAT Notice 731 §6.4"
	doc.flags.ignore_permissions = True
	doc.insert(ignore_permissions=True)
	doc.submit()


def _apply_scheme_transition(company, old_scheme, new_scheme, effective_year):
	"""HMRC Notice 731 §6.4 transitional handling on a Cash<->non-Cash switch.

	LEAVING cash: post the outstanding VAT (on invoices unpaid at the switch) as a
	Scheme-Change adjustment so it isn't lost between schemes. JOINING cash: flag
	pre-switch invoices as cash-excluded so their later payments aren't double-counted
	(they were already accounted on the accrual basis). Returns a human note or None.
	"""
	if not company:
		return None
	switch = _switch_date(effective_year)
	if not switch:
		return None
	leaving = old_scheme == CASH and new_scheme in (ACCRUAL, FLAT_RATE)
	joining = new_scheme == CASH and old_scheme in (ACCRUAL, FLAT_RATE)

	if leaving:
		s_vat, s_net = _outstanding_vat_net(SALES_INVOICE, switch, company)
		p_vat, p_net = _outstanding_vat_net(PURCHASE_INVOICE, switch, company)
		reason = f"Leaving Cash Accounting from {effective_year}: output tax due on supplies not yet paid (Notice 731 §6.4)."
		_post_transition_adjustment(company, "Box 1", s_vat, switch, reason)
		_post_transition_adjustment(company, "Box 6", s_net, switch, reason)
		_post_transition_adjustment(company, "Box 4", p_vat, switch, reason)
		_post_transition_adjustment(company, "Box 7", p_net, switch, reason)
		if any((s_vat, p_vat, s_net, p_net)):
			return (f"Leaving Cash Accounting: a transitional adjustment for outstanding VAT "
					f"(Box 1 £{s_vat:,.2f}, Box 4 £{p_vat:,.2f}) was posted dated {switch} "
					"so unpaid supplies are still accounted (Notice 731 §6.4). Review it under Reports.")
		return None

	if joining:
		n = 0
		for dt in (SALES_INVOICE, PURCHASE_INVOICE):
			for name in frappe.get_all(dt, filters={"docstatus": 1, "company": company,
													"posting_date": ["<", switch], "vat_cash_excluded": 0}, pluck="name"):
				frappe.db.set_value(dt, name, "vat_cash_excluded", 1, update_modified=False)
				n += 1
		if n:
			return (f"Joining Cash Accounting: {n} invoice(s) dated before {switch} were flagged "
					"as already accounted on the accrual basis, so their later payments won't be "
					"double-counted (Notice 731 §6.3).")
		return None
	return None


def _apply_scheme_change(doc, vat_accounting_scheme, cur):
	"""Validate + apply a VAT-accounting-scheme change on the settings doc.

	Returns an error dict if the change is rejected (invalid scheme, Cash turnover
	ineligibility, or the mid-year lock), else applies it and returns None.
	"""
	if vat_accounting_scheme not in (ACCRUAL, CASH, FLAT_RATE):
		return {"ok": False, "message": "Invalid accounting scheme."}
	# A1-1: Cash Accounting is now supported. Notice 731 §6 exclusions are honoured
	# per-invoice via the vat_cash_excluded flag (accrual-based in the cash engine),
	# and §2/§4 turnover eligibility is enforced here before allowing the scheme.
	if vat_accounting_scheme == CASH:
		err = _cash_eligibility_error(doc.company)
		if err:
			return err
	# A1-2: Flat Rate Scheme (Notice 733) — join only under the £150k turnover limit.
	if vat_accounting_scheme == FLAT_RATE:
		err = _frs_eligibility_error(doc.company)
		if err:
			return err
	if doc.scheme_effective_year and doc.scheme_effective_year == cur:
		return {
			"ok": False,
			"message": f"The VAT accounting scheme is locked for VAT year {cur} and cannot be changed mid-year. You can change it from the start of the next VAT year.",
		}
	doc.vat_accounting_scheme = vat_accounting_scheme
	doc.scheme_effective_year = cur
	return None


def _get_or_create_settings(c, billing_contact):
	"""Return (VAT Settings doc, None) or (None, error-dict). Creates the record on
	first save — nothing creates VAT Settings on install and the cockpit must not
	fall back to the desk form, so a fresh site configures itself here. Extracted
	from save_settings (CH-1)."""
	if c["settings"]:
		return frappe.get_doc(VAT_SETTINGS, c["settings"]), None
	if not c["company"]:
		return None, {
			"ok": False,
			"message": "Set a default company in Global Defaults before configuring UK VAT.",
		}
	doc = frappe.new_doc(VAT_SETTINGS)
	doc.company = c["company"]
	# Required by the DocType; real values arrive from the fields below or are edited
	# later. Defaulting the scheme to Standard matches HMRC's default and does NOT
	# engage the mid-year lock (scheme_effective_year stays unset).
	doc.billing_contact = billing_contact or frappe.session.user
	doc.vat_accounting_scheme = doc.vat_accounting_scheme or ACCRUAL
	doc.payment_method = doc.payment_method or "Bank"
	doc.insert(ignore_permissions=True)
	return doc, None


def _apply_flat_rate_pct(doc, flat_rate_percentage, changed):
	"""Diff-and-set the Flat Rate percentage on the settings doc. Returns an error
	dict if the value isn't numeric, else None (appending to `changed` when it moves)."""
	if flat_rate_percentage is None or str(flat_rate_percentage) == "":
		return None
	try:
		pct = float(flat_rate_percentage)
	except (TypeError, ValueError):
		return {"ok": False, "message": "Flat Rate Percentage must be a number."}
	if float(doc.get("flat_rate_percentage") or 0) != pct:
		doc.flat_rate_percentage = pct
		changed.append("flat rate percentage")
	return None


@frappe.whitelist()
def save_settings(
	billing_contact=None, vat_accounting_scheme=None, client_id=None, redirect_url=None,
	client_secret=None, flat_rate_percentage=None, broker_signup_token=None
):
	"""Save the cockpit-editable VAT settings. Enforces the mid-year scheme lock:
	once a scheme is chosen for a VAT year it cannot be changed until the next year."""
	_require("write")
	c = _connection()
	doc, err = _get_or_create_settings(c, billing_contact)
	if err:
		return err
	cur = _current_vat_year()
	changed = []

	# Plain scalar fields that just diff-and-set. (field, incoming, human label)
	for field, value, label in (
		("billing_contact", billing_contact, "billing contact"),
		("client_id", client_id, "client ID"),
		("redirect_url", redirect_url, "redirect URL"),
	):
		if value is not None and value != (doc.get(field) or ""):
			doc.set(field, value)
			changed.append(label)

	# client_secret is write-only: set only when a non-empty value is provided.
	if client_secret:
		doc.client_secret = client_secret
		changed.append("client secret")

	# Broker signup token (write-only): the per-subscriber token ZikPro issued. Stored so the
	# next Connect can self-register this site with the broker (A1 gate).
	if broker_signup_token:
		doc.broker_signup_token = broker_signup_token
		changed.append("broker signup token")

	old_scheme, scheme_changed = doc.vat_accounting_scheme, False
	if vat_accounting_scheme and vat_accounting_scheme != doc.vat_accounting_scheme:
		err = _apply_scheme_change(doc, vat_accounting_scheme, cur)
		if err:
			return err
		changed.append("accounting scheme")
		scheme_changed = True

	# A1-2: the Flat Rate percentage (Notice 733) — only meaningful under FRS.
	err = _apply_flat_rate_pct(doc, flat_rate_percentage, changed)
	if err:
		return err

	if not changed:
		return {"ok": True, "message": "No changes to save.", "settings": get_settings()}

	doc.save(ignore_permissions=True)
	frappe.db.commit()

	# A1-4: Notice 731 §6.4 transitional adjustment on a Cash<->non-Cash switch.
	note = _apply_scheme_transition(doc.company, old_scheme, doc.vat_accounting_scheme, cur) if scheme_changed else None
	msg = "Saved: " + ", ".join(changed) + "."
	if note:
		msg += " " + note
	return {"ok": True, "message": msg, "settings": get_settings()}


# --- Phase 2: OAuth via the central hmrc_broker (multi-tenant). When use_broker is set,
# connect + refresh route through the broker (which holds the single HMRC secret and the
# one registered redirect URI); tokens still live here. Direct mode is untouched. ---
def _ensure_broker_registration(settings_name, company=None, vrn=None):
	"""Marketplace onboarding (A1-gated). If this site is not yet a broker tenant, self-register
	with the product broker and flip to broker mode — BUT ONLY when a Broker Signup Token is
	present. A bare/anonymous install with no token does NOT auto-register (so it can never file
	under ZikPro's shared HMRC credential). Idempotent; a site already registered is left as-is.
	Returns True if broker mode is (now) active."""
	if not settings_name:
		return False
	row = frappe.db.get_value(
		VAT_SETTINGS, settings_name, ["use_broker", "broker_tenant_id"], as_dict=True)
	# Already a broker tenant → nothing to do.
	if row and row.use_broker and row.broker_tenant_id:
		return True
	# A1 GATE: no signup token → do NOT register. Free ≠ anonymous.
	token = _safe_password(frappe.get_doc(VAT_SETTINGS, settings_name), "broker_signup_token")
	if not token:
		return False
	payload = {"company_name": company or frappe.local.site, "site_url": frappe.utils.get_url(),
			   "signup_token": token}
	if vrn:
		payload["vrn"] = vrn
	r = _broker_call(DEFAULT_BROKER_URL, "self_register", payload)
	if not r.get("ok") or not r.get("tenant_id"):
		return False
	if r.get("already_registered"):
		# The broker knows this site but we have no secret locally and can't recover it openly;
		# fall back to the invite path (support). Do not claim broker mode.
		return False
	doc = frappe.get_doc(VAT_SETTINGS, settings_name)
	doc.use_broker = 1
	doc.broker_url = (r.get("broker_url") or DEFAULT_BROKER_URL).rstrip("/")
	doc.broker_tenant_id = r["tenant_id"]
	doc.broker_shared_secret = r.get("shared_secret")
	doc.save(ignore_permissions=True)
	frappe.db.commit()
	return True


def _broker_settings(settings_name):
	"""(broker_url, tenant_id, shared_secret) if this tenant is in broker mode, else None."""
	if not settings_name:
		return None
	row = frappe.db.get_value(
		VAT_SETTINGS, settings_name, ["use_broker", "broker_url", "broker_tenant_id"], as_dict=True)
	if not row or not row.use_broker or not row.broker_url or not row.broker_tenant_id:
		return None
	secret = _safe_password(frappe.get_doc(VAT_SETTINGS, settings_name), "broker_shared_secret")
	if not secret:
		return None
	return row.broker_url.rstrip("/"), row.broker_tenant_id, secret


def _broker_sig(secret, payload):
	import hashlib
	import hmac
	return hmac.new((secret or "").encode(), (payload or "").encode(), hashlib.sha256).hexdigest()


def _broker_call(broker_url, endpoint, payload):
	"""POST to the broker; return its `message` dict (or {} on failure). Never carries
	the HMRC client secret — only the tenant's HMAC signature."""
	try:
		resp = requests.post(f"{broker_url}/api/method/hmrc_broker.api.{endpoint}", data=payload, timeout=30)
	except requests.RequestException as e:
		frappe.log_error(f"[cockpit] broker {endpoint} failed: {e}", _LOG_DASHBOARD)
		return {}
	if resp.status_code != 200:
		frappe.log_error(f"[cockpit] broker {endpoint} {resp.status_code}: {resp.text[:300]}", _LOG_DASHBOARD)
		return {}
	return resp.json().get("message") or {}


@frappe.whitelist()
def get_authorize_url():
	"""Build the HMRC **sandbox** OAuth authorize URL for the Connect button.

	Read-only prerequisites check; returns an actionable error instead of a broken
	redirect when credentials aren't set. Token exchange/callback is a separate step.
	In broker mode, the broker builds the URL (it owns the client_id + redirect + state).
	"""
	_require("write")
	c = _connection()
	if not c["settings"]:
		return {"ok": False, "message": "No VAT Settings found for the default company. Create it first."}

	# Marketplace one-click: if we have no HMRC creds of our own and aren't a tenant yet,
	# self-register with the product broker so the very first Connect just works.
	_ensure_broker_registration(c["settings"], company=c["company"], vrn=c["vrn"])

	broker = _broker_settings(c["settings"])
	if broker:
		broker_url, tenant_id, secret = broker
		nonce = frappe.generate_hash(length=24)
		frappe.cache().set_value(f"{_OAUTH_STATE_PREFIX}{nonce}", c["settings"], expires_in_sec=600)
		r = _broker_call(broker_url, "authorize", {
			"tenant_id": tenant_id, "nonce": nonce, "sig": _broker_sig(secret, f"{tenant_id}|{nonce}")})
		if not r.get("ok") or not r.get("authorize_url"):
			return {"ok": False, "message": "The OAuth broker did not return an authorize URL. Check the broker settings."}
		return {"ok": True, "url": r["authorize_url"]}

	# Broker-only product: with no broker registration, the site needs a ZikPro signup token.
	if not c["client_configured"] or not c["redirect_url"]:
		return {
			"ok": False,
			"message": "Enter your ZikPro Broker Signup Token in VAT Settings, then click Connect. "
					   "You receive this token when you subscribe — it links this site to HMRC "
					   "through ZikPro's broker.",
		}

	row = frappe.db.get_value(
		VAT_SETTINGS, c["settings"], ["client_id", "redirect_url"], as_dict=True
	)
	# CSRF/replay guard for the OAuth round-trip (validated in complete_oauth on callback).
	state = frappe.generate_hash(length=24)
	frappe.cache().set_value(f"{_OAUTH_STATE_PREFIX}{state}", c["settings"], expires_in_sec=600)

	params = {
		"response_type": "code",
		"client_id": row["client_id"],
		"scope": "read:vat write:vat",
		"redirect_uri": row["redirect_url"],
		"state": state,
	}
	url = f"{_hmrc_authorize_url()}?{urllib.parse.urlencode(params, quote_via=urllib.parse.quote)}"
	return {"ok": True, "url": url}


def complete_oauth(code=None, state=None, broker_code=None, nonce=None):
	"""Exchange an HMRC **sandbox** authorization code for tokens and store them.

	Called by the `/oauth-callback` www handler. In broker mode the broker returns a
	one-time `broker_code` (+ our `nonce`) instead of a raw HMRC code; we pull the
	tokens from the broker. In direct mode we validate our own `state` and exchange the
	code ourselves. Saves access/refresh tokens on VAT Settings.

	Returns (settings_name, error_message). error_message is None on success.
	Does NOT touch the frozen production api.py path.
	"""
	if broker_code:
		return _complete_oauth_broker(broker_code, nonce)
	if not code or not state:
		return None, "Missing authorization code or state."

	settings_name = frappe.cache().get_value(f"{_OAUTH_STATE_PREFIX}{state}")
	if not settings_name:
		return None, "Authorization state is invalid or expired. Please start Connect again."
	# one-time use
	frappe.cache().delete_value(f"{_OAUTH_STATE_PREFIX}{state}")

	if not frappe.db.exists(VAT_SETTINGS, settings_name):
		return None, "VAT Settings no longer exists."

	doc = frappe.get_doc(VAT_SETTINGS, settings_name)
	client_id = doc.client_id
	client_secret = _safe_password(doc, "client_secret")
	redirect_uri = doc.redirect_url

	payload = {
		"grant_type": "authorization_code",
		"code": code,
		"redirect_uri": redirect_uri,
		"client_id": client_id,
		"client_secret": client_secret,
	}
	try:
		resp = requests.post(
			_hmrc_token_url(),
			data=payload,
			headers={"Content-Type": "application/x-www-form-urlencoded"},
			auth=HTTPBasicAuth(client_id, client_secret),
			timeout=30,
		)
	except requests.RequestException as e:
		frappe.log_error(f"[cockpit] token request failed: {e}", "UK VAT Connect")
		return None, "Could not reach HMRC to exchange the code. Please try again."

	if resp.status_code != 200:
		frappe.log_error(
			f"[cockpit] token exchange {resp.status_code}: {resp.text[:500]}", "UK VAT Connect"
		)
		return None, f"HMRC rejected the token exchange ({resp.status_code})."

	token = resp.json()
	# VAT Settings has only access_token / refresh_token columns (no token_expiry —
	# see BUILD_TRACKER; that gap lives in the deferred token-lifecycle work).
	doc.access_token = token.get("access_token")
	doc.refresh_token = token.get("refresh_token")
	# Record WHICH VAT number this grant is for. HMRC ties the authorisation to the
	# MTD enrolment of the account the user signed in with; if the Company's VRN is
	# later changed, every call 403s with CLIENT_OR_AGENT_NOT_AUTHORISED while the
	# tokens themselves stay perfectly valid. Without this we cannot tell the two
	# situations apart and the UI shows a green "Connected" over a broken link.
	doc.authorised_vrn = frappe.db.get_value(
		"Company", doc.company, "uk_vat_registration_number"
	)
	doc.save(ignore_permissions=True)
	frappe.db.commit()
	return settings_name, None


def _complete_oauth_broker(broker_code, nonce):
	"""Broker mode: verify our nonce, PULL the tokens from the broker (one-time,
	HMAC-signed), and store them. The broker did the code->token exchange."""
	if not broker_code or not nonce:
		return None, "Missing broker code or nonce."
	settings_name = frappe.cache().get_value(f"{_OAUTH_STATE_PREFIX}{nonce}")
	if not settings_name:
		return None, "Authorization state is invalid or expired. Please start Connect again."
	frappe.cache().delete_value(f"{_OAUTH_STATE_PREFIX}{nonce}")  # one-time
	broker = _broker_settings(settings_name)
	if not broker:
		return None, "The OAuth broker is not configured."
	broker_url, tenant_id, secret = broker
	r = _broker_call(broker_url, "exchange", {
		"tenant_id": tenant_id, "broker_code": broker_code,
		"sig": _broker_sig(secret, f"{tenant_id}|{broker_code}")})
	if not r.get("ok") or not r.get("access_token"):
		return None, "The broker could not complete the token exchange."
	doc = frappe.get_doc(VAT_SETTINGS, settings_name)
	doc.access_token = r["access_token"]
	doc.refresh_token = r.get("refresh_token")
	doc.authorised_vrn = frappe.db.get_value("Company", doc.company, "uk_vat_registration_number")
	doc.save(ignore_permissions=True)
	frappe.db.commit()
	return settings_name, None


def _safe_password(doc, fieldname):
	"""Read a Password field, tolerating an UNDECRYPTABLE value (B47). A cloned/restored
	site rotates the encryption_key, so a previously-stored HMRC secret/token can no longer
	be decrypted and get_password throws 'Encryption key is invalid' — even with
	raise_exception=False, which only suppresses the missing-value case. These are
	user-supplied credentials we cannot regenerate, so the honest degrade is to treat them
	as ABSENT (→ 'not connected / reconnect'), never a 500 for the person using the cockpit.
	See [[broadcast-encryption-key-heal-password-secret]].
	"""
	try:
		return doc.get_password(fieldname, raise_exception=False)
	except Exception:
		frappe.log_error(
			f"[cockpit] {fieldname} could not be decrypted (encryption_key changed after a "
			"restore/clone?) — treating as absent; the user must reconnect / re-enter credentials",
			_LOG_DASHBOARD,
		)
		return None


def _refresh_token(settings_name):
	"""Exchange the stored refresh token for a fresh access token (sandbox).

	HMRC access tokens last ~4h; refresh tokens rotate on use, so the NEW pair is
	persisted immediately. Returns True on success. In broker mode this routes through
	the broker (the HMRC secret stays there). The frozen api.py refresh path is untouched.
	"""
	doc = frappe.get_doc(VAT_SETTINGS, settings_name)
	broker = _broker_settings(settings_name)
	if broker:
		broker_url, tenant_id, secret = broker
		refresh = _safe_password(doc, "refresh_token") if doc.refresh_token else None
		if not refresh:
			return False
		r = _broker_call(broker_url, "refresh", {
			"tenant_id": tenant_id, "refresh_token": refresh,
			"sig": _broker_sig(secret, f"{tenant_id}|{refresh}")})
		if not r.get("ok") or not r.get("access_token"):
			return False
		doc.access_token = r["access_token"]
		if r.get("refresh_token"):
			doc.refresh_token = r["refresh_token"]
		doc.save(ignore_permissions=True)
		frappe.db.commit()
		return True

	refresh = _safe_password(doc, "refresh_token") if doc.refresh_token else None
	if not refresh or not doc.client_id:
		return False
	try:
		resp = requests.post(
			_hmrc_token_url(),
			data={
				"grant_type": "refresh_token",
				"refresh_token": refresh,
				"client_id": doc.client_id,
				"client_secret": _safe_password(doc, "client_secret"),
			},
			headers={"Content-Type": "application/x-www-form-urlencoded"},
			timeout=30,
		)
	except requests.RequestException as e:
		frappe.log_error(f"[cockpit] refresh request failed: {e}", _LOG_DASHBOARD)
		return False
	if resp.status_code != 200:
		frappe.log_error(f"[cockpit] refresh {resp.status_code}: {resp.text[:300]}", _LOG_DASHBOARD)
		return False
	tok = resp.json()
	doc.access_token = tok.get("access_token")
	if tok.get("refresh_token"):
		doc.refresh_token = tok["refresh_token"]  # rotating — persist the new one
	doc.save(ignore_permissions=True)
	frappe.db.commit()
	return True


def _auth_headers(settings_name, extra=None):
	"""Bearer + FROZEN fraud-prevention headers (imported read-only from api.py).

	NOTE: never log these — they contain the Bearer token.
	"""
	# Imported lazily + read-only; FPH logic stays frozen (never edited here).
	from zikpro_uk_vat.api import get_fraud_prevention_headers

	doc = frappe.get_doc(VAT_SETTINGS, settings_name)
	headers = {
		"Authorization": f"Bearer {_safe_password(doc, 'access_token')}",
		"Accept": "application/vnd.hmrc.1.0+json",
	}
	try:
		headers.update(get_fraud_prevention_headers() or {})
	except Exception as e:  # FPH must never break a call; log and continue without it
		frappe.log_error(f"[cockpit] FPH build failed: {e}", _LOG_DASHBOARD)
	if extra:
		headers.update(extra)
	return headers


_MAX_REQ_PER_SEC = 3  # HMRC hard limit, PER APPLICATION, shared across all tenants
_MAX_429_RETRIES = 3


def _throttle():
	"""Best-effort client-side rate limit so we stay under HMRC's 3 req/s.

	The limit is per-application (all tenants/workers share it), so the counter
	lives in the shared (redis-backed) site cache, not process memory. Fixed
	one-second window; if it's full we wait for the next second.
	"""
	cache = frappe.cache()
	for _ in range(15):  # bounded wait (~5s worst case) — never block a worker forever
		key = f"hmrc_reqs:{int(time.time())}"
		count = (cache.get_value(key) or 0) + 1
		cache.set_value(key, count, expires_in_sec=2)
		if count <= _MAX_REQ_PER_SEC:
			return
		time.sleep(1.0 / _MAX_REQ_PER_SEC)


def _sandbox_request(method, settings_name, endpoint, *, params=None, json_data=None, test_scenario=None):
	"""One authenticated call to the HMRC **sandbox** VAT API.

	Single place that knows: sandbox base + Bearer + FROZEN fraud-prevention
	headers + throttling + the 401-refresh and 429-backoff retries. Access tokens
	last ~4h (401 → refresh once). HMRC allows 3 req/s per application (429
	MESSAGE_THROTTLED_OUT → honour Retry-After / exponential backoff). The frozen
	production api.make_hmrc_request path is not used (it targets the production base).
	"""
	extra = {}
	if test_scenario:
		extra["Gov-Test-Scenario"] = test_scenario
	if json_data is not None:
		extra["Content-Type"] = "application/json"
	url = f"{_hmrc_base()}{endpoint}"

	def _send():
		_throttle()
		return requests.request(
			method,
			url,
			headers=_auth_headers(settings_name, extra or None),
			params=params,
			json=json_data,
			timeout=30,
		)

	t0 = time.monotonic()
	resp = _send()
	if resp.status_code == 401 and _refresh_token(settings_name):
		resp = _send()
	retries = 0
	while resp.status_code == 429 and retries < _MAX_429_RETRIES:
		try:
			wait = float(resp.headers.get("Retry-After") or 0)
		except (TypeError, ValueError):
			wait = 0
		time.sleep(min(wait or (2 ** retries), 10))  # honour Retry-After, else backoff
		retries += 1
		resp = _send()
	_log_hmrc_call(method, endpoint, settings_name, resp, t0)  # A2-1 masked audit trail
	return resp


_BEARER_RE = re.compile(r"Bearer\s+[A-Za-z0-9._\-]+")


def _log_hmrc_call(method, endpoint, settings_name, resp, t0):
	"""A2-1: write a MASKED audit record of one HMRC call to the HMRC Request Log.

	Never stores the bearer token; fraud-prevention headers are recorded by NAME only
	(read from the ACTUAL sent headers on resp.request). Best-effort — a logging error
	must never break the HMRC call."""
	try:
		sent = getattr(getattr(resp, "request", None), "headers", None) or {}
		fph = ", ".join(sorted(k for k in sent if str(k).startswith("Gov-")))
		hdr = resp.headers or {}
		ok = 200 <= resp.status_code < 300
		parts = endpoint.strip("/").split("/")
		vrn = parts[2] if len(parts) >= 3 and parts[0] == "organisations" and parts[1] == "vat" else None
		err = None
		if not ok:
			# P3-2: mask the FULL sensitive-key set (not just Bearer) in the error body,
			# using the single source of truth in security.py.
			from zikpro_uk_vat.security import redact_secrets
			err = redact_secrets((resp.text or "")[:500])
		doc = frappe.new_doc("HMRC Request Log")
		doc.request_time = frappe.utils.now_datetime()
		doc.method = method
		doc.outcome = "Success" if ok else "Error"
		doc.http_status = resp.status_code
		doc.vrn = vrn
		doc.company = frappe.db.get_value(VAT_SETTINGS, settings_name, "company") if settings_name else None
		doc.duration_ms = int((time.monotonic() - t0) * 1000)
		doc.endpoint = f"{method} {endpoint}"[:490]
		doc.correlation_id = hdr.get("X-CorrelationId") or hdr.get("CorrelationId")
		doc.receipt_id = hdr.get("Receipt-ID")
		# P3-3 dispute defensibility: on a successful VAT return submission HMRC returns
		# the receipt (formBundleNumber) in the BODY, not a header. Capture it so this
		# audit row carries HMRC's own reference — the join key to the filed Return
		# (UK MTD VAT Return.form_bundle_number holds the same value).
		if ok and not doc.receipt_id:
			try:
				doc.receipt_id = (resp.json() or {}).get("formBundleNumber")
			except Exception:
				pass
		doc.fph_headers = fph
		doc.error_summary = err
		doc.flags.ignore_permissions = True
		doc.insert(ignore_permissions=True)
	except Exception:
		pass


def _sandbox_get(settings_name, endpoint, params=None, test_scenario=None):
	"""Authenticated GET (see _sandbox_request)."""
	return _sandbox_request(
		"GET", settings_name, endpoint, params=params, test_scenario=test_scenario
	)


def _sandbox_post(settings_name, endpoint, json_data):
	"""Authenticated POST (see _sandbox_request)."""
	return _sandbox_request("POST", settings_name, endpoint, json_data=json_data)


@frappe.whitelist()
def get_obligations(status=None):
	"""Real HMRC VAT obligations for the connected VRN (Dashboard 'where you stand').

	Data-truthful: returns exactly what HMRC returns; never fabricates periods.
	"""
	_require()
	from frappe.utils import add_to_date, nowdate

	c = _connection()
	if not c["connected"]:
		return {"ok": False, "reason": "not_connected", "message": _MSG_NOT_CONNECTED}
	if not c["vrn"]:
		return {"ok": False, "reason": "no_vrn", "message": _MSG_NO_VRN}

	# Obligations require a date window; HMRC caps it at ~366 days (400 INVALID_DATE_RANGE
	# beyond that), so look back 12 months to today.
	params = {"from": add_to_date(nowdate(), months=-12), "to": nowdate()}
	if status in ("O", "F"):
		params["status"] = status

	try:
		resp = _sandbox_get(
			c["settings"], f"/organisations/vat/{c['vrn']}/obligations", params=params
		)
	except requests.RequestException as e:
		frappe.log_error(f"[cockpit] obligations request failed: {e}", _LOG_DASHBOARD)
		return {"ok": False, "reason": "network", "message": _MSG_NO_HMRC}

	if resp.status_code == 200:
		obligations = (resp.json() or {}).get("obligations", [])
		return {"ok": True, "vrn": c["vrn"], "obligations": obligations}
	if resp.status_code == 404:
		# HMRC returns 404 NOT_FOUND when there are no obligations in the window.
		return {"ok": True, "vrn": c["vrn"], "obligations": []}

	frappe.log_error(
		f"[cockpit] obligations {resp.status_code}: {resp.text[:400]}", _LOG_DASHBOARD
	)
	return {
		"ok": False,
		"reason": "hmrc_error",
		"message": _hmrc_error(resp, c["vrn"]),
		"auth_error": _hmrc_code(resp) in _HMRC_AUTH_ERRORS,
	}


def _dated_list(endpoint_suffix, list_key, log_label):
	"""Shared fetch for liabilities/payments (both are {vrn}/<x>?from&to, 12-mo cap)."""
	from frappe.utils import add_to_date, nowdate

	c = _connection()
	if not c["connected"]:
		return {"ok": False, "reason": "not_connected", "message": _MSG_NOT_CONNECTED}
	if not c["vrn"]:
		return {"ok": False, "reason": "no_vrn", "message": _MSG_NO_VRN}

	# Unlike obligations, the liabilities/payments endpoints reject `to == today`
	# (400 DATE_RANGE_INVALID) — `to` must be strictly in the past. Use yesterday.
	params = {"from": add_to_date(nowdate(), months=-12), "to": add_to_date(nowdate(), days=-1)}
	try:
		resp = _sandbox_get(
			c["settings"], f"/organisations/vat/{c['vrn']}/{endpoint_suffix}", params=params
		)
	except requests.RequestException as e:
		frappe.log_error(f"[cockpit] {log_label} request failed: {e}", _LOG_DASHBOARD)
		return {"ok": False, "reason": "network", "message": _MSG_NO_HMRC}

	if resp.status_code == 200:
		return {"ok": True, "vrn": c["vrn"], "items": (resp.json() or {}).get(list_key, [])}
	if resp.status_code == 404:
		return {"ok": True, "vrn": c["vrn"], "items": []}
	frappe.log_error(f"[cockpit] {log_label} {resp.status_code}: {resp.text[:400]}", _LOG_DASHBOARD)
	return {
		"ok": False,
		"reason": "hmrc_error",
		"message": _hmrc_error(resp, c["vrn"]),
		"auth_error": _hmrc_code(resp) in _HMRC_AUTH_ERRORS,
	}


@frappe.whitelist()
def submit_return(period_key, from_date, to_date, finalised=False):
	"""Submit a VAT return for an obligation period to the HMRC sandbox.

	SECURITY: the 9-box figures are RECOMPUTED server-side from the period — the
	client's numbers are never trusted. `finalised` must be true (the legal
	declaration). Payload uses HMRC's field names mapped from our boxes.
	"""
	_require("write")
	if isinstance(finalised, str):
		finalised = finalised.lower() in ("1", "true", "yes")
	if not finalised:
		return {"ok": False, "message": "You must confirm the declaration before submitting."}

	c = _connection()
	if not c["connected"]:
		return {"ok": False, "message": _MSG_NOT_CONNECTED}
	if not c["vrn"]:
		return {"ok": False, "message": _MSG_NO_VRN}
	if not period_key:
		return {"ok": False, "message": "No obligation period selected."}

	# DOUBLE-SUBMISSION GUARD. A period can only be filed once; HMRC rejects a repeat
	# with 403 DUPLICATE_SUBMISSION. We check the ACTUAL return (not the obligation
	# status) because the obligation can still read "Open" after a successful filing.
	already = get_return(period_key)
	if already.get("ok") and already.get("filed"):
		return {
			"ok": False,
			"already_filed": True,
			"boxes": already.get("boxes"),
			"message": "This period has already been submitted to HMRC. You can view the filed return under History.",
		}

	fig = get_return_figures(from_date, to_date)
	# UX-2b: refuse to FILE a return the app knows is incomplete. `blocking` is set
	# when the VAT accounts aren't mapped (Box 1/4 read £0 despite real VAT) or VAT
	# is posted to unmapped accounts (Box 1/4 understated) — filing that is a
	# materially wrong legal return. Enforced server-side so it can't be bypassed by
	# calling the endpoint directly; the UI also disables Submit. Advisory warnings
	# (partial exemption, unclassified templates) do NOT block.
	if fig.get("blocking"):
		return {
			"ok": False,
			"blocking": True,
			"message": (
				"This return cannot be filed yet — its VAT figures are incomplete: "
				+ " ".join(fig.get("warnings", []))
				+ " Fix the VAT account mapping in Settings, then recalculate."
			),
		}
	b = fig["boxes"]
	res = _file_to_hmrc(c, period_key, b)
	if not res.get("ok"):
		return res
	receipt = res["receipt"]
	record = _record_submission(c, period_key, from_date, to_date, b, receipt)
	return {"ok": True, "receipt": receipt, "boxes": b, "record": record}


def _hmrc_return_payload(period_key, b):
	"""The exact 9-box body submitted to HMRC (their field names). Single source of truth
	for both the live POST and the immutable snapshot frozen onto the filed return (P3-1),
	so what we declared is recorded verbatim, not reconstructed — HMRC-dispute defensible."""
	return {
		"periodKey": period_key,
		"vatDueSales": b["box1"],
		"vatDueAcquisitions": b["box2"],
		"totalVatDue": b["box3"],
		"vatReclaimedCurrPeriod": b["box4"],
		"netVatDue": b["box5"],
		"totalValueSalesExVAT": b["box6"],
		"totalValuePurchasesExVAT": b["box7"],
		"totalValueGoodsSuppliedExVAT": b["box8"],
		"totalAcquisitionsExVAT": b["box9"],
		"finalised": True,
	}


def _file_to_hmrc(c, period_key, b):
	"""POST the 9 boxes to HMRC and normalise the response. Shared by the one-shot
	submit_return and the approver flow. Returns {ok, receipt} or {ok:False, ...}."""
	payload = _hmrc_return_payload(period_key, b)
	try:
		resp = _sandbox_post(c["settings"], f"/organisations/vat/{c['vrn']}/returns", payload)
	except requests.RequestException as e:
		frappe.log_error(f"[cockpit] submit failed: {e}", _LOG_DASHBOARD)
		return {"ok": False, "message": "Could not reach HMRC to submit. Please try again."}

	if resp.status_code in (200, 201):
		receipt = resp.json() or {}
		# The 201 also returns the non-repudiation receipt as HEADERS (legal proof of
		# submission — Notice 700/22). Capture them alongside the JSON body.
		receipt["_headers"] = {
			"receipt_id": resp.headers.get("Receipt-ID"),
			"receipt_timestamp": resp.headers.get("Receipt-Timestamp"),
			# Receipt-Signature is DEPRECATED by HMRC ("DO NOT USE") — not stored.
			"correlation_id": resp.headers.get("X-CorrelationId"),
		}
		return {"ok": True, "receipt": receipt}
	frappe.log_error(f"[cockpit] submit {resp.status_code}: {resp.text[:500]}", _LOG_DASHBOARD)
	# HMRC wraps the useful detail in errors[] — the top-level message is just
	# "Business validation error", which tells the user nothing.
	try:
		body = resp.json() or {}
	except ValueError:
		body = {}
	nested = body.get("errors") or []
	detail = "; ".join(e.get("message") for e in nested if e.get("message")) if nested else ""
	codes = {e.get("code") for e in nested}
	if "DUPLICATE_SUBMISSION" in codes:
		return {
			"ok": False,
			"already_filed": True,
			"status": resp.status_code,
			"message": "This period has already been submitted to HMRC. You can view the filed return under History.",
		}
	msg = detail or body.get("message") or resp.text[:200]
	return {"ok": False, "status": resp.status_code, "message": f"HMRC rejected the submission: {msg}"}



def _hmrc_datetime(value):
	"""Normalise an HMRC ISO8601 timestamp (e.g. '2026-07-19T15:10:38.776Z') to a
	MariaDB-safe 'YYYY-MM-DD HH:MM:SS'. HMRC sends the 'T', fractional seconds and a
	trailing 'Z' that MariaDB DATETIME rejects."""
	if not value:
		return None
	return value.replace("T", " ").replace("Z", "").split(".")[0].strip() or None


def _record_submission(conn, period_key, from_date, to_date, boxes, receipt):
	"""Persist the filed return as a SUBMITTED UK MTD VAT Return (audit trail).

	HMRC stays the source of truth; this is our immutable record of what we sent
	and what HMRC acknowledged (6-year VAT record-keeping). Written only AFTER
	HMRC accepts, and never allowed to break the user's successful filing.
	"""
	try:
		doc = frappe.new_doc(UK_MTD_VAT_RETURN)
		doc.company = conn.get("company")
		doc.vrn = conn.get("vrn")
		doc.reference_key = period_key
		doc.period_start_date = from_date
		doc.period_end_date = to_date
		doc.status = "Fulfilled"
		doc.accounting_scheme = _scheme(conn.get("settings"))
		_apply_boxes(doc, boxes)
		doc.submitted_payload = frappe.as_json(_hmrc_return_payload(period_key, boxes))
		_snapshot_rate_summary(doc, from_date, to_date)
		_apply_receipt(doc, receipt)
		# One-shot path has no separate approval step; mark it filed for consistency
		# with the preparer/approver flow's records.
		doc.approval_status = "Filed"
		doc.insert(ignore_permissions=True)
		# ignore_permissions on submit too: this is internal bookkeeping written
		# AFTER HMRC has accepted the filing, and the real authorisation gate is
		# _require("write") at the top of submit_return. Only System Manager holds
		# the DocType submit permission, so without this a finance user would file
		# successfully at HMRC and then have their audit record silently stuck as a
		# draft (the except below swallows it) — defeating the audit trail. (B10.)
		doc.flags.ignore_permissions = True
		doc.submit()
		frappe.db.commit()
		# VAT Ledger: record a Filed event per invoice in the period so their accrual
		# vat_status becomes "Claimed". Best-effort — a ledger hiccup must not turn a
		# successful HMRC filing into a reported failure (caught by the except below).
		from zikpro_uk_vat import vat_ledger
		vat_ledger.record_return_filed(doc, from_date, to_date)
		return doc.name
	except Exception as e:
		# The return IS filed at HMRC — a bookkeeping failure must never be
		# reported to the user as a failed submission. Log loudly instead.
		frappe.log_error(
			f"[cockpit] filed at HMRC but local record failed for {period_key}: {e}",
			_LOG_DASHBOARD,
		)
		return None


def _apply_boxes(doc, b):
	doc.sales_vat_due_box1 = b["box1"]
	doc.eu_acquisition_vat_due_box2 = b["box2"]
	doc.total_vat_due_box3 = b["box3"]
	doc.purchase_vat_reclaimed_box4 = b["box4"]
	doc.net_vat_due_box5 = b["box5"]
	doc.net_sales_box6 = b["box6"]
	doc.net_purchases_box7 = b["box7"]
	doc.net_eu_supplies_box_8 = b["box8"]
	doc.net_eu_acquisitions_box_9 = b["box9"]


def _apply_receipt(doc, receipt):
	doc.form_bundle_number = receipt.get("formBundleNumber")
	doc.charge_ref_number = receipt.get("chargeRefNumber")
	doc.payment_indicator = receipt.get("paymentIndicator")
	doc.processing_date = _hmrc_datetime(receipt.get("processingDate"))
	doc.submitted_on = frappe.utils.now_datetime()
	hdr = receipt.get("_headers") or {}
	doc.receipt_id = hdr.get("receipt_id")
	doc.receipt_timestamp = _hmrc_datetime(hdr.get("receipt_timestamp"))
	doc.correlation_id = hdr.get("correlation_id")


@frappe.whitelist()
def prepare_return(period_key, from_date, to_date):
	"""PREPARER step (SEC-11): build/refresh a DRAFT return awaiting approval.

	Does NOT contact HMRC — a DIFFERENT user files it via approve_and_submit.
	Re-preparing the same period updates the existing draft.
	"""
	_require("write")
	_require_role(PREPARER_ROLE, "prepare a VAT return")
	c = _connection()
	if not c["connected"]:
		return {"ok": False, "message": _MSG_NOT_CONNECTED}
	if not c["vrn"]:
		return {"ok": False, "message": _MSG_NO_VRN}
	if not period_key:
		return {"ok": False, "message": "No obligation period selected."}
	already = get_return(period_key)
	if already.get("ok") and already.get("filed"):
		return {"ok": False, "already_filed": True, "message": "This period has already been submitted to HMRC."}
	fig = get_return_figures(from_date, to_date)
	if fig.get("blocking"):
		return {
			"ok": False,
			"blocking": True,
			"message": "This return can't be prepared — its VAT figures are incomplete: "
			+ " ".join(fig.get("warnings", [])),
		}
	b = fig["boxes"]
	draft_name = frappe.db.get_value(UK_MTD_VAT_RETURN, {"reference_key": period_key, "docstatus": 0}, "name")
	doc = frappe.get_doc(UK_MTD_VAT_RETURN, draft_name) if draft_name else frappe.new_doc(UK_MTD_VAT_RETURN)
	doc.company = c["company"]
	doc.vrn = c["vrn"]
	doc.reference_key = period_key
	doc.period_start_date = from_date
	doc.period_end_date = to_date
	# `status` (the HMRC obligation status) only allows Overdue/Fulfilled; leave it
	# unset for a draft and let approve_and_submit set Fulfilled once filed.
	doc.accounting_scheme = _scheme(c["settings"])
	_apply_boxes(doc, b)
	doc.approval_status = AWAITING_APPROVAL
	doc.prepared_by = frappe.session.user
	doc.prepared_on = frappe.utils.now_datetime()
	doc.approved_by = None
	doc.save(ignore_permissions=True)
	frappe.db.commit()
	return {"ok": True, "return_name": doc.name, "boxes": b, "prepared_by": doc.prepared_by}


@frappe.whitelist()
def list_pending_approvals():
	"""APPROVER view: draft returns awaiting approval, with who prepared each."""
	_require("read")
	_require_role(APPROVER_ROLE, "review VAT returns for approval")
	rows = frappe.get_all(
		UK_MTD_VAT_RETURN,
		filters={"docstatus": 0, "approval_status": AWAITING_APPROVAL},
		fields=[
			"name", "reference_key", "period_start_date", "period_end_date",
			"prepared_by", "prepared_on", "sales_vat_due_box1", "total_vat_due_box3",
			"purchase_vat_reclaimed_box4", "net_vat_due_box5", "net_sales_box6", "net_purchases_box7",
		],
		order_by="prepared_on desc",
	)
	return {"ok": True, "rows": rows, "current_user": frappe.session.user}


def _fph_app_token(settings_name):
	"""An HMRC application (client_credentials) token for the FPH validator. Broker mode → minted
	by the broker (the tenant holds no secret); direct mode → from the site's own creds."""
	broker = _broker_settings(settings_name)
	if broker:
		broker_url, tenant_id, secret = broker
		r = _broker_call(broker_url, "app_token",
						 {"tenant_id": tenant_id, "sig": _broker_sig(secret, f"{tenant_id}|apptoken")})
		return r.get("access_token") if r.get("ok") else None
	doc = frappe.get_doc(VAT_SETTINGS, settings_name)
	cid, csec = doc.get("client_id"), _safe_password(doc, "client_secret")
	if not (cid and csec):
		return None
	try:
		tok = requests.post(_hmrc_token_url(), data={
			"grant_type": "client_credentials", "client_id": cid, "client_secret": csec,
			"scope": "read:vat write:vat"}, timeout=30)
		return tok.json().get("access_token") if tok.status_code == 200 else None
	except requests.RequestException:
		return None


def _fph_gate(settings_name):
	"""A2 pre-submit gate: validate the fraud-prevention headers this site would send against
	HMRC's validator. Returns (verdict, detail) where verdict is:
	  'valid'       → headers pass, filing allowed;
	  'invalid'     → HMRC reported header ERRORS → BLOCK the filing;
	  'unavailable' → could not validate (no app token / validator unreachable) → fail-OPEN with
	                  a logged warning (a validator/HMRC outage must not halt all filing; HMRC
	                  still monitors FPH on the real submission)."""
	at = _fph_app_token(settings_name)
	if not at:
		return "unavailable", "no application token to validate headers"
	from zikpro_uk_vat.api import get_fraud_prevention_headers
	try:
		fph = get_fraud_prevention_headers() or {}
	except Exception as e:
		return "unavailable", f"FPH build failed: {e}"
	headers = {"Authorization": f"Bearer {at}", "Accept": "application/vnd.hmrc.1.0+json"}
	headers.update(fph)
	try:
		resp = requests.get(f"{HMRC_SANDBOX_BASE}/test/fraud-prevention-headers/validate",
							headers=headers, timeout=30)
	except requests.RequestException as e:
		return "unavailable", f"validator unreachable: {e}"
	try:
		body = resp.json() or {}
	except ValueError:
		body = {}
	errors = body.get("errors") or []
	if resp.status_code == 200 and (body.get("code") == "VALID_HEADERS" or not errors):
		return "valid", "VALID_HEADERS"
	return "invalid", f"{body.get('code') or resp.status_code}: {errors or body}"


@frappe.whitelist()
def approve_and_submit(return_name, finalised=False):
	"""APPROVER step (SEC-11 / B28): file a prepared draft to HMRC.

	Segregation of duties — the approver must NOT be the preparer. Enforced in
	code for EVERYONE, including System Manager / Administrator.
	"""
	# Gate on READ of VAT Settings, not write: the Approver role has settings
	# read-only by design (they review + file, they don't edit VAT config — that's
	# the preparer's job). The write authority here is the APPROVER role + the VAT
	# Return submit permission, enforced next.
	_require("read")
	_require_role(APPROVER_ROLE, "approve and file a VAT return")
	if isinstance(finalised, str):
		finalised = finalised.lower() in ("1", "true", "yes")
	if not finalised:
		return {"ok": False, "message": "You must confirm the declaration before filing."}
	if not frappe.db.exists(UK_MTD_VAT_RETURN, return_name):
		return {"ok": False, "message": "Return not found."}
	doc = frappe.get_doc(UK_MTD_VAT_RETURN, return_name)
	if doc.docstatus != 0 or doc.approval_status != AWAITING_APPROVAL:
		return {"ok": False, "message": "This return is not awaiting approval."}
	if doc.prepared_by and doc.prepared_by == frappe.session.user:
		frappe.throw(
			frappe._(
				"Segregation of duties: you cannot approve a return you prepared. "
				"A different user must approve and file it."
			),
			frappe.PermissionError,
		)
	c = _connection()
	if not c["connected"]:
		return {"ok": False, "message": _MSG_NOT_CONNECTED}
	# 2FA gate (PRODUCTION filings only): HMRC's Gov-Client-Multi-Factor header must reflect a
	# genuine MFA event. Requiring 2FA at the point of filing makes that header truthful by
	# construction (every login is a real MFA event) — without editing the frozen FPH code.
	# Sandbox is left open so testing/pilots don't need 2FA configured.
	if _hmrc_production() and not _two_factor_active(frappe.session.user):
		return {
			"ok": False,
			"mfa_required": True,
			"message": "Enable Two-Factor Authentication before filing. HMRC's fraud-prevention "
			"headers require a genuine multi-factor authentication record — turn on 2FA in System "
			"Settings, sign in again, then retry.",
		}
	period_key = doc.reference_key
	from_date, to_date = str(doc.period_start_date), str(doc.period_end_date)
	already = get_return(period_key)
	if already.get("ok") and already.get("filed"):
		return {"ok": False, "already_filed": True, "message": "This period has already been submitted to HMRC."}
	# RECOMPUTE server-side — the figures filed are never the draft's stored numbers
	# (invoices may have changed between preparation and approval).
	fig = get_return_figures(from_date, to_date)
	if fig.get("blocking"):
		return {
			"ok": False,
			"blocking": True,
			"message": "This return can't be filed — its VAT figures are incomplete: "
			+ " ".join(fig.get("warnings", [])),
		}
	b = fig["boxes"]
	# A2 GATE: validate fraud-prevention headers before filing. Block on HMRC-reported header
	# errors; fail-open (with a logged warning) if the validator itself is unavailable.
	verdict, detail = _fph_gate(c["settings"])
	if verdict == "invalid":
		return {
			"ok": False,
			"fph_blocked": True,
			"message": "Filing blocked: the fraud-prevention headers failed HMRC validation. "
			+ "Open the return on the live, public-facing site in a browser and retry. " + detail,
		}
	if verdict == "unavailable":
		frappe.log_error(f"[cockpit] FPH gate could not validate (filing allowed): {detail}", _LOG_DASHBOARD)
	res = _file_to_hmrc(c, period_key, b)
	if not res.get("ok"):
		return res
	receipt = res["receipt"]
	try:
		_apply_boxes(doc, b)
		doc.submitted_payload = frappe.as_json(_hmrc_return_payload(period_key, b))
		_apply_receipt(doc, receipt)
		doc.status = "Fulfilled"
		doc.approval_status = "Filed"
		doc.approved_by = frappe.session.user
		doc.save(ignore_permissions=True)
		doc.flags.ignore_permissions = True
		doc.submit()
		frappe.db.commit()
	except Exception as e:
		frappe.log_error(
			f"[cockpit] filed at HMRC but finalising draft failed for {return_name}: {e}",
			_LOG_DASHBOARD,
		)
	return {"ok": True, "receipt": receipt, "boxes": b, "record": doc.name}


@frappe.whitelist()
def validate_fraud_headers():
	"""Ask HMRC's Test Fraud Prevention Headers API to VALIDATE the FPH we send.

	READ-ONLY DIAGNOSTIC — this does NOT modify the frozen FPH; it imports
	`get_fraud_prevention_headers` unchanged and asks HMRC whether those headers
	are valid. Zero re-approval risk (validation only, no HMRC-facing behaviour
	change). Caveat: called server-side the browser-sourced headers (screens,
	client IP, user-agent) differ from a real desk request, so treat this as a
	server-context check, not a full production verdict.
	"""
	_require()
	c = _connection()
	if not c["settings"]:
		return {"ok": False, "message": "No VAT Settings found for the default company."}

	# application-restricted endpoint → app token. Broker mode: minted by the broker (the tenant
	# holds no secret); direct mode: from the site's own creds. Same helper the A2 gate uses.
	app_token = _fph_app_token(c["settings"])
	if not app_token:
		return {"ok": False, "message": "Could not obtain an application token to validate headers "
				"(check the broker signup token / connection)."}
	try:
		from zikpro_uk_vat.api import get_fraud_prevention_headers

		fph = {}
		try:
			fph = get_fraud_prevention_headers() or {}
		except Exception as e:
			frappe.log_error(f"[cockpit] FPH build failed: {e}", _LOG_DASHBOARD)
		headers = {"Authorization": f"Bearer {app_token}", "Accept": "application/vnd.hmrc.1.0+json"}
		headers.update(fph)
		resp = requests.get(
			f"{HMRC_SANDBOX_BASE}/test/fraud-prevention-headers/validate", headers=headers, timeout=30
		)
	except requests.RequestException as e:
		frappe.log_error(f"[cockpit] FPH validate failed: {e}", _LOG_DASHBOARD)
		return {"ok": False, "message": _MSG_NO_HMRC}

	body = {}
	try:
		body = resp.json() or {}
	except ValueError:
		pass
	return {
		"ok": resp.status_code in (200, 201),
		"status": resp.status_code,
		"spec_version": body.get("specVersion"),
		"code": body.get("code"),
		"message": body.get("message"),
		"errors": body.get("errors") or [],
		"warnings": body.get("warnings") or [],
		"headers_sent": sorted(fph.keys()),
	}


@frappe.whitelist()
def get_penalties():
	"""HMRC points-based penalty position (late submission + late payment).

	Serves the 'never miss the window' job: penalty points, the regime threshold
	(a £200 penalty triggers when points reach it), when a clean-compliance reset
	is achieved, and any late-payment charges outstanding/estimated. Read-only.
	"""
	_require()
	c = _connection()
	if not c["connected"]:
		return {"ok": False, "message": _MSG_NOT_CONNECTED}
	if not c["vrn"]:
		return {"ok": False, "message": _MSG_NO_VRN}
	try:
		resp = _sandbox_get(c["settings"], f"/organisations/vat/{c['vrn']}/penalties")
	except requests.RequestException as e:
		frappe.log_error(f"[cockpit] penalties request failed: {e}", _LOG_DASHBOARD)
		return {"ok": False, "message": _MSG_NO_HMRC}
	if resp.status_code == 404:
		return {"ok": True, "has_data": False}
	if resp.status_code != 200:
		frappe.log_error(f"[cockpit] penalties {resp.status_code}: {resp.text[:300]}", _LOG_DASHBOARD)
		return {"ok": False, "message": _hmrc_error(resp, c["vrn"])}

	return _parse_penalties(resp.json() or {})


def _parse_penalties(d):
	"""Shape HMRC's penalties payload into the cockpit's flat view (CH-1)."""
	tot = d.get("totalisations") or {}
	lsp = (d.get("lateSubmissionPenalty") or {}).get("summary") or {}
	lpp_details = (d.get("latePaymentPenalty") or {}).get("details") or []
	active = lsp.get("activePenaltyPoints") or 0
	threshold = lsp.get("regimeThreshold") or 0
	return {
		"ok": True,
		"has_data": True,
		"active_points": active,
		"inactive_points": lsp.get("inactivePenaltyPoints") or 0,
		"threshold": threshold,
		"at_threshold": bool(threshold) and active >= threshold,
		"compliance_achievement": lsp.get("periodOfComplianceAchievement"),
		"late_submission_charge": lsp.get("penaltyChargeAmount") or 0,
		"late_payment_posted": tot.get("latePaymentPenaltyPostedTotal") or 0,
		"late_payment_estimate": tot.get("latePaymentPenaltyEstimateTotal") or 0,
		"penalised_principal": tot.get("penalisedPrincipalTotal") or 0,
		"late_payment_details": [
			{
				"category": x.get("penaltyCategory"),
				"outstanding": x.get("penaltyAmountOutstanding"),
				"status": x.get("penaltyStatus"),
				"due": x.get("principalChargeDueDate"),
				"from": x.get("principalChargeBillingFrom"),
				"to": x.get("principalChargeBillingTo"),
			}
			for x in lpp_details
		],
	}


@frappe.whitelist()
def get_filed_returns():
	"""Local audit trail — the UK MTD VAT Return records we filed and HMRC accepted.

	Distinct from get_return (which live-queries HMRC's View Return): this is OUR
	immutable record incl. the receipt (formBundleNumber) and the scheme used.
	Keyed by reference_key so the History screen can pair it with the obligation.
	"""
	_require()
	c = _connection()
	filters = {"docstatus": 1}
	if c["vrn"]:
		filters["vrn"] = c["vrn"]
	rows = frappe.get_all(
		UK_MTD_VAT_RETURN,
		filters=filters,
		fields=[
			"name", "reference_key", "period_start_date", "period_end_date",
			"accounting_scheme", "net_vat_due_box5", "form_bundle_number",
			"charge_ref_number", "submitted_on",
		],
		order_by="period_start_date desc",
	)
	by_key = {}
	for r in rows:
		if r.reference_key and r.reference_key not in by_key:
			by_key[r.reference_key] = {
				"name": r.name,
				"scheme": r.accounting_scheme,
				"net_vat": r.net_vat_due_box5,
				"form_bundle_number": r.form_bundle_number,
				"charge_ref_number": r.charge_ref_number,
				"submitted_on": str(r.submitted_on) if r.submitted_on else None,
			}
	return {"ok": True, "by_key": by_key}


@frappe.whitelist()
def get_return(period_key):
	"""View a previously submitted return from HMRC (View Return endpoint).

	Truthful: returns exactly what HMRC has on file for the period. 404 => not filed.
	"""
	_require()
	c = _connection()
	if not c["connected"]:
		return {"ok": False, "message": _MSG_NOT_CONNECTED}
	if not c["vrn"]:
		return {"ok": False, "message": _MSG_NO_VRN}
	if not period_key:
		return {"ok": False, "message": "No period selected."}

	try:
		resp = _sandbox_get(
			c["settings"], f"/organisations/vat/{c['vrn']}/returns/{urllib.parse.quote(period_key)}"
		)
	except requests.RequestException as e:
		frappe.log_error(f"[cockpit] view-return request failed: {e}", _LOG_DASHBOARD)
		return {"ok": False, "message": _MSG_NO_HMRC}

	if resp.status_code == 404:
		return {"ok": True, "filed": False}
	if resp.status_code == 200:
		d = resp.json() or {}
		# Map HMRC field names back to our box keys.
		boxes = {
			"box1": d.get("vatDueSales"),
			"box2": d.get("vatDueAcquisitions"),
			"box3": d.get("totalVatDue"),
			"box4": d.get("vatReclaimedCurrPeriod"),
			"box5": d.get("netVatDue"),
			"box6": d.get("totalValueSalesExVAT"),
			"box7": d.get("totalValuePurchasesExVAT"),
			"box8": d.get("totalValueGoodsSuppliedExVAT"),
			"box9": d.get("totalAcquisitionsExVAT"),
		}
		return {"ok": True, "filed": True, "period_key": period_key, "boxes": boxes}
	frappe.log_error(f"[cockpit] view-return {resp.status_code}: {resp.text[:400]}", _LOG_DASHBOARD)
	return {"ok": False, "message": _hmrc_error(resp, c["vrn"])}


def _r2(v):
	"""Round to 2 dp (boxes 1-5: VAT amounts in pounds and pence).

	Uses Decimal ROUND_HALF_UP, NOT Python round(). round() does banker's
	rounding (round-half-to-even) on top of float representation error —
	round(0.125,2)=0.12 and round(1.005,2)=1.00 — which diverges from the
	statutory arithmetic rounding HMRC expects and can put a box figure 1p off
	per affected line, accumulating across the return (broadcast B36). Decimal
	via str() sidesteps the float-repr error too.
	"""
	return float(Decimal(str(v or 0)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def _r0(v):
	"""Round to whole pounds (boxes 6-9: HMRC wants no pence), HALF_UP not banker's."""
	return int(Decimal(str(v or 0)).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def compute_boxes(sales, purchases, eu=None, reverse_charge=None):
	"""Pure 9-box calculation — the keystone. `sales`/`purchases` are lists of
	{"net": float, "vat": float} (credit notes already carry NEGATIVE net & vat).

	`reverse_charge={"vat": x}` is the Domestic Reverse Charge notional VAT (Notice 735):
	the CUSTOMER of a reverse-charge service self-accounts the output VAT in Box 1 AND
	reclaims it in Box 4 (net already sits in Box 7 as a normal purchase; NOT in Box 6).
	Defaults to 0 → today's behaviour when there are no reverse-charge lines.

	Fixes the four inherited bugs (see BUILD_TRACKER / api.calculate_vat_boxes):
	  - credit notes INCLUDED (caller must not filter is_return out; their negatives net off),
	  - `vat` is VAT-only (caller extracts VAT tax lines, not all taxes/charges),
	  - Box 5 = |Box3 - Box4| (absolute; never negative — HMRC rejects negatives),
	  - returns NUMBERS (not Data strings), with correct rounding (1-5: 2dp, 6-9: whole £).
	"""
	eu = eu or {}
	rc = float((reverse_charge or {}).get("vat", 0))  # DRC notional VAT (customer self-accounts)
	box1 = sum(s["vat"] for s in sales) + rc     # VAT due on sales (+ reverse-charge output)
	box2 = float(eu.get("box2", 0))              # VAT due on EU acquisitions
	box4 = sum(p["vat"] for p in purchases) + rc # VAT reclaimed on purchases (+ reverse-charge input)
	box6 = sum(s["net"] for s in sales)          # total value of sales ex-VAT
	box7 = sum(p["net"] for p in purchases)      # total value of purchases ex-VAT
	box3 = box1 + box2                           # total VAT due
	box5 = abs(box3 - box4)                      # net VAT — ABSOLUTE
	return {
		"box1": _r2(box1),
		"box2": _r2(box2),
		"box3": _r2(box3),
		"box4": _r2(box4),
		"box5": _r2(box5),
		"box6": _r0(box6),
		"box7": _r0(box7),
		"box8": _r0(eu.get("box8", 0)),
		"box9": _r0(eu.get("box9", 0)),
	}


_VAT_BOX_KEYS = ("box1", "box2", "box3", "box4", "box5")


def _apply_adjustments(boxes, from_date, to_date):
	"""Fold submitted VAT Adjustments landing in [from_date, to_date] into the boxes,
	then recompute the derived boxes (3 = 1+2, 5 = |3-4|). Returns (boxes, adjustments)
	where adjustments are the source VAT Adjustment records (name/date/type/amount/
	reason) so the cockpit can list + remove them. (The matching `Adjusted` ledger
	events are the ledger-centric mirror for multi-period reporting.)
	"""
	if not frappe.db.exists("DocType", VAT_ADJUSTMENT):
		return boxes, []
	company = frappe.db.get_single_value(GLOBAL_DEFAULTS, "default_company")
	filters = {"docstatus": 1, "posting_date": ["between", [from_date, to_date]]}
	if company:
		filters["company"] = company
	adjs = frappe.get_all(
		VAT_ADJUSTMENT, filters=filters,
		fields=["name", "posting_date", "adjustment_type", "vat_box", "amount", "reason", "notice_ref"],
		order_by="posting_date asc, creation asc",
	)
	if not adjs:
		return boxes, []
	b = {k: float(boxes[k]) for k in boxes}
	for a in adjs:
		if not a.vat_box:
			continue
		key = "box" + a.vat_box.split()[1]  # "Box 4" -> "box4"; amount is the box delta
		b[key] = b.get(key, 0.0) + float(a.amount or 0)
	b["box3"] = b["box1"] + b["box2"]
	b["box5"] = abs(b["box3"] - b["box4"])
	out = {k: (_r2(v) if k in _VAT_BOX_KEYS else _r0(v)) for k, v in b.items()}
	return out, adjs


def _origin_state(origin_doctype, origin_name):
	"""'missing' / 'cancelled' / 'amended' / None for an adjustment's origin doc —
	the states that mean a manual correction may now be double-counted."""
	if not origin_doctype or not origin_name or not frappe.db.exists("DocType", origin_doctype):
		return None
	if not frappe.db.exists(origin_doctype, origin_name):
		return "missing"
	# amended before cancelled: an amended doc IS cancelled (docstatus 2) but has a
	# successor — report the more accurate "amended".
	meta = frappe.get_meta(origin_doctype)
	if meta.get_field("amended_from") and frappe.db.exists(origin_doctype, {"amended_from": origin_name}):
		return "amended"
	if frappe.db.get_value(origin_doctype, origin_name, "docstatus") == 2:
		return "cancelled"
	return None


def _double_count_warnings(from_date, to_date):
	"""Roll-over safety: flag submitted VAT Adjustments in the period whose ORIGIN
	document has since been amended/cancelled/deleted — the correction may now be
	applied twice (via this adjustment AND via the corrected source flowing into its
	own period). Provenance-driven; surfaced with the 'Check before filing' warnings.
	"""
	if not frappe.db.exists("DocType", VAT_ADJUSTMENT):
		return []
	company = frappe.db.get_single_value(GLOBAL_DEFAULTS, "default_company")
	filters = {"docstatus": 1, "posting_date": ["between", [from_date, to_date]], "origin_name": ["is", "set"]}
	if company:
		filters["company"] = company
	adjs = frappe.get_all(
		VAT_ADJUSTMENT, filters=filters, fields=["name", "origin_doctype", "origin_name"]
	)
	warnings = []
	for a in adjs:
		state = _origin_state(a.origin_doctype, a.origin_name)
		if not state:
			continue
		if state == "missing":
			warnings.append(
				f"Adjustment {a.name} references {a.origin_doctype} {a.origin_name}, which no "
				"longer exists — verify the correction is still valid and not double-counted."
			)
		else:
			warnings.append(
				f"Adjustment {a.name} references {a.origin_doctype} {a.origin_name}, which has "
				f"since been {state} — check the correction is not double-counted (once via this "
				f"adjustment and again via the {state} source)."
			)
	return warnings


OUTPUT_VAT = "Output (Sales)"
INPUT_VAT = "Input (Purchases)"


def vat_treatments(settings_name=None):
	"""Item Tax Template → VAT treatment mapping from VAT Settings (child table)."""
	settings_name = settings_name or _connection()["settings"]
	if not settings_name:
		return []
	return frappe.get_all(
		MTD_VAT_TREATMENT,
		filters={"parent": settings_name, "parenttype": VAT_SETTINGS},
		fields=["item_tax_template", "vat_treatment", "in_box_6_7", "notional_rate"],
	)


def _outside_scope_templates(settings_name=None):
	"""Templates whose amounts are EXCLUDED from Boxes 6/7 (outside scope of VAT)."""
	return {t.item_tax_template for t in vat_treatments(settings_name) if not t.in_box_6_7}


def _excluded_net_by_parent(item_doctype, parent_doctype, names, excluded_templates):
	"""Per-invoice net of lines to EXCLUDE from Box 6/7 (outside-scope items).

	Box 6/7 include zero-rated and exempt supplies but exclude outside-scope
	amounts (wages, dividends, disbursements). Those are identified by the line's
	item_tax_template, since the tax amount is 0 for all three treatments.
	"""
	if not names or not excluded_templates:
		return {}
	rows = frappe.get_all(
		item_doctype,
		filters={
			"parenttype": parent_doctype,
			"parent": ["in", names],
			"item_tax_template": ["in", list(excluded_templates)],
		},
		fields=["parent", "base_net_amount"],
	)
	out = {}
	for r in rows:
		out[r.parent] = out.get(r.parent, 0.0) + float(r.base_net_amount or 0)
	return out


def _unclassified_templates(item_doctype, parent_doctype, names, settings_name=None):
	"""Item Tax Templates used on these invoices but NOT classified in VAT Settings.

	Same 'configured ≠ configured correctly' guard as the accounts: an unclassified
	template could be outside-scope wrongly counted in Box 6/7 — warn, don't guess.
	"""
	if not names:
		return set()
	used = {
		r.item_tax_template
		for r in frappe.get_all(
			item_doctype,
			filters={"parenttype": parent_doctype, "parent": ["in", names], "item_tax_template": ["is", "set"]},
			fields=["item_tax_template"],
		)
		if r.item_tax_template
	}
	known = {t.item_tax_template for t in vat_treatments(settings_name)}
	return used - known


def vat_accounts(vat_type=None, settings_name=None):
	"""Configured VAT ledger accounts from VAT Settings (child table).

	Explicit configuration replaces the old '%VAT%' account-name heuristic, which
	silently produced £0 boxes for any client whose VAT account was named
	differently. Returns [] when unconfigured — callers must warn, never guess.
	"""
	settings_name = settings_name or _connection()["settings"]
	if not settings_name:
		return []
	filters = {"parent": settings_name, "parenttype": VAT_SETTINGS}
	if vat_type:
		filters["vat_type"] = vat_type
	return [
		r.account
		for r in frappe.get_all(MTD_VAT_ACCOUNT, filters=filters, fields=["account"])
		if r.account
	]


def _find_group(company, abbr, candidates, root_type):
	"""Pick a sensible parent group for a new VAT account, else any matching group."""
	for name in candidates:
		full = f"{name} - {abbr}"
		if frappe.db.exists("Account", full):
			return full
	return frappe.db.get_value(
		"Account", {"company": company, "is_group": 1, "root_type": root_type}, "name"
	)


def _resolve_or_create_vat_account(company, abbr, base_name, root_type, groups):
	"""Find (preferably) or create the VAT tax account for one side. Returns
	(account_name | None, created: bool). None means no parent group existed to
	create under, so the caller must skip this side. Extracted from
	setup_default_vat_accounts (CH-1)."""
	account = f"{base_name} - {abbr}"
	if not frappe.db.exists("Account", account):
		# Prefer an EXISTING VAT tax account of the right side before creating a new
		# one — most clients already post VAT somewhere, and inventing a second
		# account would leave their real VAT unmapped (Boxes 1/4 stuck at zero).
		existing_account = frappe.db.get_value(
			"Account",
			{
				"company": company,
				"is_group": 0,
				"account_type": "Tax",
				"root_type": root_type,
				"account_name": ["like", "%VAT%"],
			},
			"name",
		)
		if existing_account:
			account = existing_account
	if not frappe.db.exists("Account", account):
		parent = _find_group(company, abbr, groups, root_type)
		if not parent:
			return None, False
		acc = frappe.new_doc("Account")
		acc.account_name = base_name
		acc.company = company
		acc.parent_account = parent
		acc.account_type = "Tax"
		acc.root_type = root_type
		acc.insert(ignore_permissions=True)
		return acc.name, True
	return account, False


@frappe.whitelist()
def setup_default_vat_accounts():
	"""Create/point the two default VAT accounts and map them in VAT Settings.

	MTD VAT Output = sales VAT (liability); MTD VAT Input = purchase VAT (asset).
	Existing accounts are reused, and a client can always change the mapping in the
	VAT Accounts table — this only seeds sensible defaults.
	"""
	_require("write")
	c = _connection()
	if not c["settings"]:
		return {"ok": False, "message": _MSG_NO_SETTINGS}
	company = c["company"]
	abbr = frappe.db.get_value("Company", company, "abbr")

	wanted = [
		(OUTPUT_VAT, "MTD VAT Output", "Liability", ["Duties and Taxes", "Current Liabilities"]),
		(INPUT_VAT, "MTD VAT Input", "Asset", ["Duties and Taxes", "Current Assets"]),
	]
	doc = frappe.get_doc(VAT_SETTINGS, c["settings"])
	existing = {(r.vat_type, r.account) for r in (doc.vat_accounts or [])}
	created, mapped = [], []

	for vat_type, base_name, root_type, groups in wanted:
		account, was_created = _resolve_or_create_vat_account(company, abbr, base_name, root_type, groups)
		if account is None:
			continue
		if was_created:
			created.append(account)
		if not any(t == vat_type for t, _ in existing):
			doc.append("vat_accounts", {"vat_type": vat_type, "account": account,
			                            "description": "Default"})
			mapped.append(f"{vat_type} → {account}")

	if mapped:
		doc.save(ignore_permissions=True)
		frappe.db.commit()
	return {
		"ok": True,
		"created": created,
		"mapped": mapped,
		"accounts": get_vat_accounts()["rows"],
		"message": ("Mapped: " + "; ".join(mapped)) if mapped else "VAT accounts already configured.",
	}


# Salvaged from the approved baseline's create_vat_accounts_and_templates patch
# (branch `main` @1a2e7e2, dropped by UK_VAT_Version16) and adapted to the
# accounts + treatments model. (treatment, rate, counts in Box 6/7)
_DEFAULT_TREATMENTS = (
	("Standard rated", 20, 1),
	("Reduced rated", 5, 1),
	("Zero rated", 0, 1),
	("Exempt", 0, 1),
	("Outside scope", 0, 0),
)


def _ensure_item_tax_template(side, treatment, rate, account, company):
	"""Find or create one UK VAT Item Tax Template. Returns (name, created: bool).
	Extracted from setup_default_vat_templates (CH-1)."""
	title = f"UK VAT {side} - {treatment}"
	name = frappe.db.get_value(ITEM_TAX_TEMPLATE, {"title": title, "company": company}, "name")
	if name:
		return name, False
	t = frappe.new_doc(ITEM_TAX_TEMPLATE)
	t.title = title
	t.company = company
	t.append("taxes", {"tax_type": account, "tax_rate": rate})
	t.insert(ignore_permissions=True)
	return t.name, True


@frappe.whitelist()
def setup_default_vat_templates():
	"""Create the UK VAT Item Tax Templates and classify each one.

	Zero-rated / Exempt / Outside-scope are all 0% — only the template tells them
	apart — so creating them AND mapping their treatment is one job. Idempotent:
	existing templates and existing mappings are left alone (a client may already
	have their own).
	"""
	_require("write")
	c = _connection()
	if not c["settings"]:
		return {"ok": False, "message": _MSG_NO_SETTINGS}
	company = c["company"]
	doc = frappe.get_doc(VAT_SETTINGS, c["settings"])
	mapped_already = {t.item_tax_template for t in (doc.vat_treatments or [])}
	created, mapped = [], []

	for side, vat_type in (("Sales", OUTPUT_VAT), ("Purchases", INPUT_VAT)):
		accounts = vat_accounts(vat_type, c["settings"])
		if not accounts:
			continue  # accounts must be mapped first (setup_default_vat_accounts)
		account = accounts[0]
		for treatment, rate, in_box in _DEFAULT_TREATMENTS:
			name, was_created = _ensure_item_tax_template(side, treatment, rate, account, company)
			if was_created:
				created.append(name)
			if name not in mapped_already:
				doc.append(
					"vat_treatments",
					{"item_tax_template": name, "vat_treatment": treatment, "in_box_6_7": in_box},
				)
				mapped_already.add(name)
				mapped.append(f"{name} → {treatment}")

	if mapped:
		doc.save(ignore_permissions=True)
		frappe.db.commit()
	return {
		"ok": True,
		"created": created,
		"mapped": mapped,
		"message": (f"{len(created)} template(s) created, {len(mapped)} classified.")
		if (created or mapped)
		else "VAT templates already set up.",
	}


@frappe.whitelist()
def setup_vat_defaults():
	"""One-click: VAT accounts, then the Item Tax Templates + their treatments."""
	_require("write")
	acc = setup_default_vat_accounts()
	if not acc.get("ok"):
		return acc
	tpl = setup_default_vat_templates()
	return {
		"ok": tpl.get("ok"),
		"accounts": acc.get("mapped"),
		"templates_created": tpl.get("created"),
		"treatments_mapped": tpl.get("mapped"),
		"message": f"{acc.get('message', '')} {tpl.get('message', '')}".strip(),
	}


@frappe.whitelist()
def save_vat_accounts(accounts):
	"""Manually set the Output/Input VAT account mapping from the cockpit.

	Auto-configure picks a VAT account by heuristic and can choose one the
	business's invoices don't actually post to (Box 1/4 then read £0). This lets
	the user correct the mapping in-cockpit instead of the forbidden desk form.
	`accounts` is a JSON list of {vat_type, account}.
	"""
	_require("write")
	c = _connection()
	if not c["settings"]:
		return {"ok": False, "message": _MSG_NO_SETTINGS}
	if isinstance(accounts, str):
		accounts = frappe.parse_json(accounts)

	valid_types = {OUTPUT_VAT, INPUT_VAT}
	rows = []
	for a in accounts or []:
		vat_type = (a.get("vat_type") or "").strip()
		account = (a.get("account") or "").strip()
		if not account:
			continue  # allow clearing a mapping by leaving it blank
		if vat_type not in valid_types:
			return {"ok": False, "message": f"Unknown VAT type: {vat_type}"}
		# Guard against a mis-scoped or non-existent account being written.
		if not frappe.db.exists("Account", {"name": account, "company": c["company"]}):
			return {"ok": False, "message": f"Account '{account}' is not an account of {c['company']}."}
		rows.append({"vat_type": vat_type, "account": account})

	doc = frappe.get_doc(VAT_SETTINGS, c["settings"])
	doc.vat_accounts = []
	for r in rows:
		doc.append("vat_accounts", r)
	doc.save(ignore_permissions=True)
	frappe.db.commit()
	return {"ok": True, "message": "VAT account mapping saved.", "count": len(rows)}


_TREATMENTS = VAT_TREATMENTS


def _validate_treatment_row(t):
	"""Validate one incoming treatment row. Returns (row | None, error | None):
	row=None with error=None means 'skip' (unclassified); a non-None error is a
	hard validation failure. Extracted from save_vat_treatments (CH-1)."""
	tmpl = (t.get("item_tax_template") or "").strip()
	treatment = (t.get("vat_treatment") or "").strip()
	if not tmpl or not treatment:
		return None, None  # a row left unclassified is simply not stored
	if treatment not in _TREATMENTS:
		return None, f"Unknown VAT treatment: {treatment}"
	if not frappe.db.exists(ITEM_TAX_TEMPLATE, tmpl):
		return None, f"Item Tax Template '{tmpl}' does not exist."
	in_box = t.get("in_box_6_7")
	in_box = 1 if (in_box in (1, "1", True, "true", "yes")) else 0
	return {"item_tax_template": tmpl, "vat_treatment": treatment, "in_box_6_7": in_box}, None


@frappe.whitelist()
def save_vat_treatments(treatments):
	"""Manually set the Item Tax Template → VAT treatment mapping from the cockpit.

	The treatment decides how each line lands in the boxes (zero/exempt stay in
	Box 6/7, outside-scope is excluded), which the tax amount alone can't tell
	apart. Editable here so a user can correct classifications without the desk
	form. `treatments` is a JSON list of {item_tax_template, vat_treatment,
	in_box_6_7}.
	"""
	_require("write")
	c = _connection()
	if not c["settings"]:
		return {"ok": False, "message": _MSG_NO_SETTINGS}
	if isinstance(treatments, str):
		treatments = frappe.parse_json(treatments)

	rows = []
	for t in treatments or []:
		row, err = _validate_treatment_row(t)
		if err:
			return {"ok": False, "message": err}
		if row:
			rows.append(row)

	doc = frappe.get_doc(VAT_SETTINGS, c["settings"])
	doc.vat_treatments = []
	for r in rows:
		doc.append("vat_treatments", r)
	doc.save(ignore_permissions=True)
	frappe.db.commit()
	return {"ok": True, "message": "VAT treatments saved.", "count": len(rows)}


@frappe.whitelist()
def get_vat_accounts():
	"""The configured VAT account mapping (for the cockpit Settings screen)."""
	_require()
	c = _connection()
	rows = []
	if c["settings"]:
		rows = frappe.get_all(
			MTD_VAT_ACCOUNT,
			filters={"parent": c["settings"], "parenttype": VAT_SETTINGS},
			fields=["name", "vat_type", "account", "description"],
			order_by="idx",
		)
	return {
		"ok": True,
		"rows": rows,
		"configured": bool(rows),
		"types": [OUTPUT_VAT, INPUT_VAT],
	}


def _unmapped_tax(child_doctype, parent_doctype, names, accounts):
	"""Tax sitting on accounts that are NOT mapped as VAT, for these invoices.

	Being 'configured' is not the same as being configured CORRECTLY: mapping the
	wrong ledger leaves real VAT unclaimed and the boxes silently at zero. If a
	period has tax posted somewhere we don't recognise, the user must be told.
	"""
	if not names:
		return {}
	rows = frappe.get_all(
		child_doctype,
		filters={
			"parenttype": parent_doctype,
			"parent": ["in", names],
			"account_head": ["not in", accounts or [""]],
		},
		fields=["account_head", "base_tax_amount"],
	)
	out = {}
	for r in rows:
		amount = float(r.base_tax_amount or 0)
		if amount:
			out[r.account_head] = out.get(r.account_head, 0.0) + amount
	return out


def _vat_by_parent(child_doctype, parent_doctype, names, accounts=None):
	"""Sum VAT-only tax per invoice from its taxes child table.

	VAT-only = tax rows posted to the CONFIGURED VAT accounts. This is what fixes
	the 'non-VAT charges counted as VAT' bug: we never use the invoice's
	total_taxes_and_charges (which also holds freight and other charges).

	`accounts` empty => unconfigured; we return {} rather than guess, and the
	caller surfaces a warning so the user is never shown a silent £0.
	"""
	if not names or not accounts:
		return {}
	rows = frappe.get_all(
		child_doctype,
		filters={
			"parenttype": parent_doctype,
			"parent": ["in", names],
			"account_head": ["in", accounts],
		},
		fields=["parent", "base_tax_amount"],
	)
	out = {}
	for r in rows:
		out[r.parent] = out.get(r.parent, 0.0) + float(r.base_tax_amount or 0)
	return out


def _flat_rate_pct(settings_name):
	pct = frappe.db.get_value(VAT_SETTINGS, settings_name, "flat_rate_percentage") if settings_name else None
	return float(pct or 0)


def _frs_gross_turnover(from_date, to_date, company):
	"""FRS 'basic turnover': total sales INCLUDING VAT in the period (invoice date),
	credit notes netting off. This is both the base the flat rate applies to and Box 6
	under Notice 733."""
	filters = {"docstatus": 1, "posting_date": ["between", [from_date, to_date]]}
	if company:
		filters["company"] = company
	rows = frappe.get_all(SALES_INVOICE, filters=filters, fields=["base_grand_total"])
	return sum(float(r.base_grand_total or 0) for r in rows)


def _flat_rate_figures(from_date, to_date):
	"""VAT Notice 733 — Flat Rate Scheme. VAT due (Box 1) = flat-rate % of the
	VAT-inclusive turnover; input VAT is NOT reclaimed (Box 4 = 0 — the capital-goods
	reclaim >= £2,000 is a follow-up). Box 6 = the flat-rate (VAT-inclusive) turnover;
	Boxes 2/7/8/9 = 0."""
	conn = _connection()
	company = conn["company"]
	pct = _flat_rate_pct(conn["settings"])
	gross = _frs_gross_turnover(from_date, to_date, company)
	box1 = gross * pct / 100.0
	boxes = {
		"box1": _r2(box1), "box2": _r2(0), "box3": _r2(box1), "box4": _r2(0),
		"box5": _r2(box1), "box6": _r0(gross), "box7": _r0(0), "box8": _r0(0), "box9": _r0(0),
	}
	boxes, adjustments = _apply_adjustments(boxes, from_date, to_date)
	warnings, blocking = [], False
	if pct <= 0:
		warnings.append(
			"Set your Flat Rate Percentage in VAT Settings — Box 1 cannot be calculated "
			"without it (VAT Notice 733)."
		)
		blocking = True
	warnings.extend(_double_count_warnings(from_date, to_date))
	return {
		"ok": True, "warnings": warnings, "blocking": blocking, "basis": "flat_rate",
		"flat_rate_percentage": pct, "from": from_date, "to": to_date,
		"boxes": boxes, "adjustments": adjustments, "sales": [], "purchases": [],
	}


@frappe.whitelist()
def get_return_figures(from_date, to_date):
	"""Correct 9-box figures for a period — respects the VAT accounting scheme.

	Standard (Accrual): by invoice date. Cash Accounting: by payment date. Flat Rate:
	a fixed % of VAT-inclusive turnover (Notice 733). Data-truthful; never fabricates.
	"""
	_require()
	scheme = _scheme()
	if scheme == CASH:
		return _cash_basis_figures(from_date, to_date)
	if scheme == FLAT_RATE:
		return _flat_rate_figures(from_date, to_date)
	return _accrual_figures(from_date, to_date)


def _gl_vat_movement(accounts, from_date, to_date, company):
	"""(sum credit, sum debit) posted to the given VAT account(s) in the period, from
	the General Ledger — the accounting truth, independent of the return engine."""
	if not accounts:
		return 0.0, 0.0
	row = frappe.db.sql(
		"""SELECT COALESCE(SUM(credit), 0), COALESCE(SUM(debit), 0)
		   FROM `tabGL Entry`
		   WHERE account IN %(accounts)s AND company = %(company)s
			 AND is_cancelled = 0 AND posting_date BETWEEN %(from_date)s AND %(to_date)s""",
		{"accounts": tuple(accounts), "company": company, "from_date": from_date, "to_date": to_date},
	)
	return float(row[0][0] or 0), float(row[0][1] or 0)


def _reconcile_rows(boxes, basis, from_date, to_date, company):
	"""Pure GL-reconciliation core (shared by reconcile_period and the pre-filing warning):
	compare the return's Box 1/4 (and Net) against the VAT actually posted to the mapped VAT
	accounts. Returns (rows, reconciled, note, combined). No perms / no figure recompute."""
	box1 = float(boxes.get("box1", 0) or 0)
	box4 = float(boxes.get("box4", 0) or 0)
	out_accts, in_accts = vat_accounts(OUTPUT_VAT), vat_accounts(INPUT_VAT)
	# When output and input VAT post to the SAME ledger account, only the NET is
	# separable from the GL — reconcile that; otherwise reconcile Box 1 and Box 4.
	combined = bool(set(out_accts) & set(in_accts))
	rows = []
	if not combined:
		ocr, odr = _gl_vat_movement(out_accts, from_date, to_date, company)
		icr, idr = _gl_vat_movement(in_accts, from_date, to_date, company)
		rows.append({"box": "Box 1", "label": "Output VAT due (sales)",
					 "return_val": _r2(box1), "gl_val": _r2(ocr - odr), "diff": _r2(box1 - (ocr - odr))})
		rows.append({"box": "Box 4", "label": "Input VAT reclaimed (purchases)",
					 "return_val": _r2(box4), "gl_val": _r2(idr - icr), "diff": _r2(box4 - (idr - icr))})
	acr, adr = _gl_vat_movement(list(set(out_accts) | set(in_accts)), from_date, to_date, company)
	net_gl = _r2(acr - adr)  # net credit on the VAT accounts = output − input = net VAT due
	rows.append({"box": "Net VAT", "label": "Net VAT due (Box 1 − Box 4)",
				 "return_val": _r2(box1 - box4), "gl_val": net_gl, "diff": _r2((box1 - box4) - net_gl)})

	is_cash = basis == "cash"
	reconciled = is_cash or all(abs(r["diff"]) < 0.01 for r in rows)
	if is_cash:
		note = ("Cash basis: the return counts VAT on PAYMENTS while the GL reflects invoice "
				"postings, so a difference equal to the VAT on unpaid invoices is expected.")
	elif combined:
		note = ("Output and input VAT share one ledger account, so only the NET VAT is "
				"reconcilable per period — map separate Output/Input VAT accounts to reconcile Box 1/4.")
	else:
		note = "Accrual basis: the return should match the VAT posted to the mapped VAT accounts."
	return rows, reconciled, note, combined


def _reconciliation_warning(boxes, basis, from_date, to_date):
	"""P0-7 pre-submit reconciliation gate (advisory): surface — in the 'Check before filing'
	panel, BEFORE the declaration — when the return's Box 1/4 do not match the VAT posted to
	the mapped VAT accounts in the GL (VAT posted outside the return: manual journals,
	mis-mapped accounts). Non-blocking: legitimate divergence exists (adjustments, timing, cash
	basis), so the honest move is to show the signer, not to refuse. India Compliance pattern —
	reconcile before it reaches HMRC. Skipped when no VAT accounts are mapped (the blocking
	account-mapping warning already covers that)."""
	if not (vat_accounts(OUTPUT_VAT) or vat_accounts(INPUT_VAT)):
		return []
	company = frappe.db.get_single_value(GLOBAL_DEFAULTS, "default_company")
	if not company:
		return []
	rows, reconciled, _note, _combined = _reconcile_rows(boxes, basis, from_date, to_date, company)
	if reconciled:
		return []
	worst = max(rows, key=lambda r: abs(r["diff"]))
	return [
		f"Reconciliation: {worst['label']} on the return (£{worst['return_val']:,.2f}) does not match "
		f"the VAT posted to the mapped VAT accounts in the GL (£{worst['gl_val']:,.2f}) — a difference "
		f"of £{abs(worst['diff']):,.2f}. VAT may have been posted outside the return (manual journals, "
		"mis-mapped accounts) or an adjustment isn't reflected in the ledger. Review before filing."
	]


@frappe.whitelist()
def reconcile_period(from_date, to_date):
	"""A2-3: reconcile the return's Box 1/4 against the VAT actually posted to the
	mapped VAT accounts in the GL. A non-zero diff flags VAT posted outside the return
	engine (manual journals, mis-mapped accounts). On the cash basis a difference equal
	to the VAT on unpaid invoices is EXPECTED (return = payments, GL = invoices)."""
	_require()
	company = frappe.db.get_single_value(GLOBAL_DEFAULTS, "default_company")
	fig = get_return_figures(from_date, to_date)
	rows, reconciled, note, combined = _reconcile_rows(
		fig.get("boxes", {}), fig.get("basis"), from_date, to_date, company
	)
	return {"ok": True, "basis": fig.get("basis"), "combined_vat_account": combined,
			"from": from_date, "to": to_date, "rows": rows, "reconciled": reconciled, "note": note}


_ADJUSTMENT_BOXES = ("Box 1", "Box 2", "Box 4", "Box 6", "Box 7", "Box 8", "Box 9")
SCHEME_CHANGE_TYPE = "Scheme Change (Notice 731)"
_ADJUSTMENT_TYPES = (
	"Error Correction (Notice 700/45)", "Partial Exemption", "Bad Debt Relief",
	"Capital Goods Scheme", "Fuel Scale Charge", SCHEME_CHANGE_TYPE, "Other",
)


@frappe.whitelist()
def adjustment_options():
	"""Select options for the cockpit's Add-Adjustment form."""
	_require()
	return {"ok": True, "boxes": list(_ADJUSTMENT_BOXES), "types": list(_ADJUSTMENT_TYPES)}


@frappe.whitelist()
def list_adjustments(from_date, to_date):
	"""Submitted VAT Adjustments landing in the period (shown under the 9 boxes)."""
	_require()
	company = frappe.db.get_single_value(GLOBAL_DEFAULTS, "default_company")
	filters = {"docstatus": 1, "posting_date": ["between", [from_date, to_date]]}
	if company:
		filters["company"] = company
	rows = frappe.get_all(
		VAT_ADJUSTMENT, filters=filters,
		fields=["name", "posting_date", "adjustment_type", "vat_box", "amount", "reason", "notice_ref"],
		order_by="posting_date asc, creation asc",
	)
	return {"ok": True, "rows": rows}


def _validate_adjustment(company, adjustment_type, vat_box, amount, reason):
	"""Return (amount|None, error-dict|None) for a proposed adjustment (CH-lean)."""
	if not company:
		return None, {"ok": False, "message": "Set a default company in Global Defaults first."}
	if vat_box not in _ADJUSTMENT_BOXES:
		return None, {"ok": False, "message": "Box 3 and Box 5 are calculated — choose Box 1, 2, 4, 6 or 7."}
	if adjustment_type not in _ADJUSTMENT_TYPES:
		return None, {"ok": False, "message": "Unknown adjustment type."}
	try:
		amount = float(amount)
	except (TypeError, ValueError):
		return None, {"ok": False, "message": "Amount must be a number."}
	if not amount:
		return None, {"ok": False, "message": "Adjustment amount cannot be zero."}
	if not (reason or "").strip():
		return None, {"ok": False, "message": "A reason is required for the audit trail."}
	return amount, None


@frappe.whitelist()
def create_adjustment(posting_date, adjustment_type, vat_box, amount, reason,
                      notice_ref=None, origin_period=None):
	"""Create + submit a VAT Adjustment from the cockpit (e.g. from a 'Check before
	filing' warning). It folds into the period's figures immediately."""
	_require("write")
	company = frappe.db.get_single_value(GLOBAL_DEFAULTS, "default_company")
	amount, err = _validate_adjustment(company, adjustment_type, vat_box, amount, reason)
	if err:
		return err
	doc = frappe.new_doc(VAT_ADJUSTMENT)
	doc.company = company
	doc.posting_date = posting_date
	doc.adjustment_type = adjustment_type
	doc.vat_box = vat_box
	doc.amount = amount
	doc.reason = reason
	doc.notice_ref = notice_ref
	doc.origin_period = origin_period
	doc.insert(ignore_permissions=True)
	doc.submit()
	frappe.db.commit()
	return {"ok": True, "name": doc.name, "message": f"Adjustment {doc.name} recorded."}


@frappe.whitelist()
def cancel_adjustment(name):
	"""Cancel a VAT Adjustment (reverses its effect on the figures)."""
	_require("write")
	doc = frappe.get_doc(VAT_ADJUSTMENT, name)
	if doc.docstatus == 1:
		doc.cancel()
	frappe.db.commit()
	return {"ok": True, "message": f"Adjustment {name} cancelled."}


VAT_ADJUSTMENT_SCHEDULE = "VAT Adjustment Schedule"


@frappe.whitelist()
def list_schedules():
	"""Forward VAT-adjustment schedules (bad-debt / CGS / PE-annual) for the cockpit."""
	_require()
	company = frappe.db.get_single_value(GLOBAL_DEFAULTS, "default_company")
	filters = {"docstatus": 1}
	if company:
		filters["company"] = company
	rows = frappe.get_all(
		VAT_ADJUSTMENT_SCHEDULE, filters=filters,
		fields=["name", "schedule_type", "reference_doctype", "reference_name", "trigger_date",
				"vat_box", "amount", "status", "generated_adjustment", "generated_on"],
		order_by="trigger_date asc, creation asc",
	)
	from frappe.utils import nowdate
	today = nowdate()
	due = sum(1 for r in rows if r.status == "Pending" and str(r.trigger_date) <= today)
	return {"ok": True, "rows": rows, "due_now": due}


@frappe.whitelist()
def run_schedule_generation():
	"""Materialise every due schedule into a VAT Adjustment now (same as the daily
	scheduler). Lets a preparer pull forward due adjustments on demand."""
	_require("write")
	from zikpro_uk_vat import vat_adjustment_schedule
	counts = vat_adjustment_schedule.generate_due_adjustments()
	msg = f"{counts['generated']} adjustment(s) generated"
	if counts.get("not_eligible"):
		msg += f", {counts['not_eligible']} no longer eligible"
	return {"ok": True, "message": msg + ".", **counts}


@frappe.whitelist()
def cancel_schedule(name):
	"""Cancel a VAT Adjustment Schedule (and any adjustment it generated)."""
	_require("write")
	doc = frappe.get_doc(VAT_ADJUSTMENT_SCHEDULE, name)
	if doc.docstatus == 1:
		doc.cancel()
	frappe.db.commit()
	return {"ok": True, "message": f"Schedule {name} cancelled."}


CAPITAL_GOODS_SCHEME = "Capital Goods Scheme"
PARTIAL_EXEMPTION_ANNUAL = "Partial Exemption Annual"
_SCHEDULE_TYPES = ("Bad Debt Relief", CAPITAL_GOODS_SCHEME, PARTIAL_EXEMPTION_ANNUAL)


@frappe.whitelist()
def schedule_options():
	"""Select options for the cockpit's Add-Schedule form."""
	_require()
	return {"ok": True, "types": list(_SCHEDULE_TYPES), "boxes": list(_ADJUSTMENT_BOXES)}


@frappe.whitelist()
def create_schedule(schedule_type, trigger_date, vat_box, amount, reason,
                    reference_doctype=None, reference_name=None, notice_ref=None):
	"""Create + submit a forward VAT Adjustment Schedule from the cockpit. It is
	materialised into a VAT Adjustment on/after its trigger date (daily, or via
	'Generate due adjustments')."""
	_require("write")
	company = frappe.db.get_single_value(GLOBAL_DEFAULTS, "default_company")
	if not company:
		return {"ok": False, "message": "Set a default company in Global Defaults first."}
	if schedule_type not in _SCHEDULE_TYPES:
		return {"ok": False, "message": "Unknown schedule type."}
	amount, err = _validate_adjustment(company, "Other", vat_box, amount, reason)
	if err:
		return err
	if not trigger_date:
		return {"ok": False, "message": "A trigger date is required."}
	doc = frappe.new_doc(VAT_ADJUSTMENT_SCHEDULE)
	doc.schedule_type = schedule_type
	doc.company = company
	doc.trigger_date = trigger_date
	doc.vat_box = vat_box
	doc.amount = amount
	doc.reason = reason
	doc.notice_ref = notice_ref
	doc.reference_doctype = reference_doctype
	doc.reference_name = reference_name
	doc.insert(ignore_permissions=True)
	doc.flags.ignore_permissions = True
	doc.submit()
	frappe.db.commit()
	return {"ok": True, "name": doc.name, "message": f"Schedule {doc.name} created."}


@frappe.whitelist()
def compute_schedule_amount(schedule_type, total_vat=None, intervals=None, baseline_pct=None,
                            interval_pct=None, residual_vat=None, annual_pct=None, provisional_pct=None):
	"""Pro (Capital Goods Scheme / Partial Exemption Annual calculator, Box 4)."""
	_require()
	return _pro("compute_schedule_amount", _upsell("the adjustment calculator"), schedule_type,
				total_vat, intervals, baseline_pct, interval_pct, residual_vat, annual_pct, provisional_pct)


_BOX_DERIVATION = {
	"box1": ("VAT due on sales and other outputs",
			"Output VAT on submitted sales invoices — VAT-only tax lines posted to the configured Output VAT account; credit notes net off. Plus any Box 1 adjustments."),
	"box2": ("VAT due on acquisitions (NI ↔ EU)", "NI-only post-Brexit; 0 unless NI acquisitions are recorded."),
	"box3": ("Total VAT due", "Box 1 + Box 2 (calculated)."),
	"box4": ("VAT reclaimed on purchases and other inputs",
			"Input VAT on submitted purchase invoices — VAT-only tax lines to the configured Input VAT account; credit notes net off. Plus any Box 4 adjustments (e.g. bad-debt relief)."),
	"box5": ("Net VAT to pay / reclaim", "|Box 3 − Box 4| (absolute; calculated)."),
	"box6": ("Total value of sales ex-VAT", "Net (ex-VAT) of sales, outside-scope supplies excluded. Plus any Box 6 adjustments."),
	"box7": ("Total value of purchases ex-VAT", "Net (ex-VAT) of purchases, outside-scope excluded. Plus any Box 7 adjustments."),
	"box8": ("Value of NI→EU goods dispatches", "NI-only; 0 unless recorded."),
	"box9": ("Value of NI←EU goods acquisitions", "NI-only; 0 unless recorded."),
}

# Indicative VAT rate per treatment name (for the 700/22 VAT-account breakdown).
_TREATMENT_RATE = (("standard", 20), ("reduced", 5), ("zero", 0), ("exempt", 0), ("outside", 0))


def _treatment_rate(treatment):
	t = (treatment or "").lower()
	for key, rate in _TREATMENT_RATE:
		if key in t:
			return rate
	return None


def _treatment_breakdown(from_date, to_date):
	"""Notice 700/22 VAT-account view: net turnover grouped by VAT treatment (rate
	category) across the period's sales + purchases, with line/doc counts. Net is
	accurate (from item lines); VAT is indicative (net × the treatment's rate). Lines
	whose item tax template is not mapped to a treatment fall under 'Unclassified'.
	"""
	tmap = {t.item_tax_template: t.vat_treatment for t in vat_treatments()}
	if not tmap:
		return []
	company = frappe.db.get_single_value(GLOBAL_DEFAULTS, "default_company")
	agg = {}
	for parent_dt, side in ((SALES_INVOICE, "sales"), (PURCHASE_INVOICE, "purchase")):
		inv_filter = {"docstatus": 1, "posting_date": ["between", [from_date, to_date]]}
		if company:
			inv_filter["company"] = company
		names = frappe.get_all(parent_dt, filters=inv_filter, pluck="name")
		if not names:
			continue
		rows = frappe.get_all(
			f"{parent_dt} Item",
			filters={"parenttype": parent_dt, "parent": ["in", names]},
			fields=["parent", "item_tax_template", "base_net_amount"],
		)
		for r in rows:
			tr = tmap.get(r.item_tax_template) or "Unclassified"
			a = agg.setdefault(tr, {"net": 0.0, "sales_net": 0.0, "purchase_net": 0.0, "lines": 0, "docs": set()})
			net = float(r.base_net_amount or 0)
			a["net"] += net
			a[f"{side}_net"] += net
			a["lines"] += 1
			a["docs"].add((parent_dt, r.parent))
	out = []
	for tr, a in sorted(agg.items()):
		rate = _treatment_rate(tr)
		out.append({
			"treatment": tr, "rate": rate,
			"net": _r2(a["net"]), "sales_net": _r2(a["sales_net"]), "purchase_net": _r2(a["purchase_net"]),
			"vat_indicative": _r2(a["net"] * rate / 100) if rate else 0,
			"lines": a["lines"], "docs": len(a["docs"]),
		})
	return out


def _snapshot_rate_summary(doc, from_date, to_date):
	"""Freeze the Notice 700/22 VAT-account (net-by-treatment) onto the return at
	filing, so the audit record is what was FILED, not a later live recompute that
	could drift as invoices are amended. Best-effort — never break a filing."""
	try:
		doc.set("vat_rate_summary", [])
		for r in _treatment_breakdown(from_date, to_date):
			doc.append("vat_rate_summary", {
				"treatment": r["treatment"], "rate": r["rate"], "net": r["net"],
				"sales_net": r["sales_net"], "purchase_net": r["purchase_net"],
				"vat_indicative": r["vat_indicative"], "line_count": r["lines"], "doc_count": r["docs"],
			})
	except Exception:
		frappe.log_error("VAT rate-summary snapshot failed", "uk_vat_snapshot")


def _period_vat_account(from_date, to_date, company):
	"""(vat_account, source, filed_info) for a period. If the period has been FILED,
	prefer the frozen snapshot — the audit record must be what was FILED, not a live
	recompute that could drift as invoices are later amended. Extracted (CH-lean)."""
	filed_filter = {"docstatus": 1, "period_start_date": from_date, "period_end_date": to_date}
	if company:
		filed_filter["company"] = company
	filed = frappe.get_all(UK_MTD_VAT_RETURN, filters=filed_filter,
						fields=["name", "form_bundle_number", "submitted_on"], limit=1)
	if not filed:
		return _treatment_breakdown(from_date, to_date), "live", None
	ret = filed[0]
	snap = frappe.get_all(
		"VAT Return Rate Summary", filters={"parent": ret.name},
		fields=["treatment", "rate", "net", "sales_net", "purchase_net", "vat_indicative",
				"line_count", "doc_count"], order_by="idx",
	)
	# Map to the same keys the live _treatment_breakdown() emits so both sources are
	# drop-in equal. (Aliasing to `lines` in SQL trips MariaDB's reserved word on v15.)
	for r in snap:
		r["lines"] = r.pop("line_count")
		r["docs"] = r.pop("doc_count")
	filed_info = {
		"return": ret.name, "form_bundle_number": ret.form_bundle_number,
		"filed_on": str(ret.submitted_on) if ret.submitted_on else None,
	}
	if snap:
		return snap, "as filed (snapshot)", filed_info
	return _treatment_breakdown(from_date, to_date), "live", filed_info


@frappe.whitelist()
def calculation_notes(from_date, to_date):
	"""The 'Calculation Notes' one-pager an account manager reviews before filing —
	the human face of the Notice 700/22 VAT account. Assembles identity, the 9 boxes
	with how each was derived, transaction counts, the VAT Ledger event summary for
	the period, the adjustments, the pre-filing warnings, and the standing assumptions.
	"""
	_require()
	conn = _connection()
	fig = get_return_figures(from_date, to_date)
	boxes = fig.get("boxes", {})
	box_rows = [
		{"box": k.replace("box", "Box "), "label": _BOX_DERIVATION[k][0],
		 "amount": boxes.get(k, 0), "how": _BOX_DERIVATION[k][1]}
		for k in ("box1", "box2", "box3", "box4", "box5", "box6", "box7", "box8", "box9")
	]

	company = conn.get("company")
	inv_filter = {"docstatus": 1, "posting_date": ["between", [from_date, to_date]]}
	if company:
		inv_filter["company"] = company
	sales = frappe.get_all(SALES_INVOICE, filters=inv_filter, fields=["name", "is_return"])
	purchases = frappe.get_all(PURCHASE_INVOICE, filters=inv_filter, fields=["name", "is_return"])
	counts = {
		"sales_invoices": sum(1 for s in sales if not s.is_return),
		"sales_credit_notes": sum(1 for s in sales if s.is_return),
		"purchase_invoices": sum(1 for p in purchases if not p.is_return),
		"purchase_credit_notes": sum(1 for p in purchases if p.is_return),
		"adjustments": len(fig.get("adjustments") or []),
	}

	ledger = []
	if frappe.db.exists("DocType", "VAT Ledger Entry"):
		lf = {"posting_date": ["between", [from_date, to_date]]}
		if company:
			lf["company"] = company
		rows = frappe.get_all("VAT Ledger Entry", filters=lf, fields=["event_type", "vat_amount"])
		summ = {}
		for r in rows:
			e = summ.setdefault(r.event_type, {"count": 0, "vat": 0.0})
			e["count"] += 1
			e["vat"] += float(r.vat_amount or 0)
		ledger = [{"event_type": k, "count": v["count"], "vat": _r2(v["vat"])} for k, v in sorted(summ.items())]

	assumptions = [
		f"Accounting basis: {fig.get('basis', 'accrual')} ({'payment date' if fig.get('basis') == 'cash' else 'invoice date'}).",
		"VAT extracted from tax lines posted to the configured Output/Input VAT accounts only (not total taxes & charges).",
		"Rounding: boxes 1–5 to 2dp (HALF_UP), boxes 6–9 to whole £.",
		"Box 5 is absolute |Box 3 − Box 4|; credit notes are included and net off.",
		"Boxes 2/8/9 are Northern Ireland only (post-Brexit).",
	]

	vat_account, account_source, filed_info = _period_vat_account(from_date, to_date, company)

	return {
		"ok": True,
		"identity": {
			"company": company, "vrn": conn.get("vrn"),
			"period_from": from_date, "period_to": to_date,
			"basis": fig.get("basis"), "scheme": _scheme(conn.get("settings")),
			"prepared_on": frappe.utils.now_datetime().strftime("%Y-%m-%d %H:%M"),
			"blocking": fig.get("blocking", False), "filed": bool(filed_info),
		},
		"boxes": box_rows,
		"vat_account": vat_account,
		"vat_account_source": account_source,
		"filed": filed_info,
		"counts": counts,
		"ledger": ledger,
		"adjustments": fig.get("adjustments") or [],
		"warnings": fig.get("warnings") or [],
		"assumptions": assumptions,
	}


def _account_mapping_warnings(stray=None, unclassified=None):
	"""Shared warning + `blocking` builder for the accrual and cash figure paths.

	BLOCKING (UX-2b) = the VAT figures are provably wrong/incomplete, so the return
	must not be filed: an unmapped Output/Input VAT account (Box 1/4 read £0 when
	real VAT exists) or `stray` VAT posted to accounts we don't count (Box 1/4
	understated). Advisory warnings (unclassified templates, partial exemption)
	are review prompts, not incorrect figures — they do not block. Returns
	(warnings, blocking). The accrual path passes stray/unclassified; cash passes
	neither (it has no per-line treatment step).
	"""
	warnings = []
	no_output = not vat_accounts(OUTPUT_VAT)
	no_input = not vat_accounts(INPUT_VAT)
	if no_output:
		warnings.append("No Output (Sales) VAT account configured — Box 1 cannot be calculated.")
	if no_input:
		warnings.append("No Input (Purchases) VAT account configured — Box 4 cannot be calculated.")
	if stray:
		warnings.append(
			"Tax found on accounts that are NOT mapped as VAT — these amounts are excluded "
			"from Boxes 1/4. Map them in Settings if they are VAT: " + "; ".join(sorted(set(stray)))
		)
	blocking = bool(no_output or no_input or stray)
	if unclassified:
		warnings.append(
			"Item Tax Templates used but not classified as a VAT treatment — currently "
			"counted in Boxes 6/7. Classify them in Settings (e.g. mark outside-scope items): "
			+ "; ".join(sorted(unclassified))
		)
	return warnings, blocking


def _pro(fn_name, default, *args, **kwargs):
	"""Freemium boundary: delegate a PREMIUM calculation to the Pro add-on
	(zikpro_uk_vat_pro.pro_engines) when it is installed; otherwise return `default`
	so the free base edition degrades gracefully (no reverse charge, no year-end
	adjustments). The premium engine code lives ONLY in the private Pro app — never
	in this public tree. Call sites keep the same function names, so nothing else changes.
	"""
	try:
		from zikpro_uk_vat_pro import pro_engines
	except Exception:
		return default
	fn = getattr(pro_engines, fn_name, None)
	return fn(*args, **kwargs) if fn else default


def _upsell(feature):
	"""Standard 'needs Pro' response for a premium cockpit action in the free base edition."""
	return {"ok": False, "pro_required": True,
			"message": f"{feature} is available in UK VAT Pro — upgrade at zikpro.com."}


def _pro_installed():
	"""Whether the Pro add-on is present, so the cockpit can GATE premium UI (year-end
	adjustment engines) behind an upsell instead of showing forms that only fail on use."""
	import importlib.util

	return importlib.util.find_spec("zikpro_uk_vat_pro") is not None


def construction_reverse_charge_templates(side=None):
	"""Pro (Domestic Reverse Charge / CIS, VAT Notice 735). Empty in the base edition;
	the Pro add-on's pro_engines provides the classifier."""
	return _pro("construction_reverse_charge_templates", set(), side)


def is_construction_reverse_charge(item_tax_template, side=None):
	"""True if an item tax template is classified as a construction reverse-charge supply.
	The shared predicate for both this app and CIS (see construction_reverse_charge_templates)."""
	return bool(item_tax_template) and item_tax_template in construction_reverse_charge_templates(side)


def _reverse_charge_invoice_names(ref_doctype, from_date, to_date, company):
	"""Pro (DRC, Notice 731 §6.2): period invoices carrying a reverse-charge line.
	Empty in the base edition (no reverse-charge supplies without the Pro add-on)."""
	return _pro("reverse_charge_invoice_names", set(), ref_doctype, from_date, to_date, company)


def _reverse_charge_notional(purchase_invoice_names):
	"""Pro (DRC, Notice 735): customer-side notional VAT self-accounted into Box 1 + Box 4.
	0 in the base edition."""
	return _pro("reverse_charge_notional", 0.0, purchase_invoice_names)


def _accrual_figures(from_date, to_date):
	"""Invoice-basis: boxes from invoices (incl. credit notes) with tax point in period."""
	company = frappe.db.get_single_value(GLOBAL_DEFAULTS, "default_company")
	base = {"docstatus": 1, "posting_date": ["between", [from_date, to_date]]}
	if company:
		base["company"] = company

	stray = []
	unclassified = set()
	os_templates = _outside_scope_templates()

	def _load(doctype, party_field):
		invs = frappe.get_all(
			doctype,
			filters=base,
			fields=["name", "posting_date", f"{party_field} as party", "base_net_total", "is_return"],
			order_by="posting_date asc",
		)
		child = SALES_TAXES if doctype == SALES_INVOICE else PURCHASE_TAXES
		item_child = f"{doctype} Item"
		accts = vat_accounts(OUTPUT_VAT if doctype == SALES_INVOICE else INPUT_VAT)
		names = [i.name for i in invs]
		vat_map = _vat_by_parent(child, doctype, names, accts)
		# Outside-scope line amounts must be kept OUT of Box 6/7.
		excluded = _excluded_net_by_parent(item_child, doctype, names, os_templates)
		unclassified.update(_unclassified_templates(item_child, doctype, names))
		for acc, amt in _unmapped_tax(child, doctype, names, accts).items():
			stray.append(f"{acc} ({_r2(amt)}) on {doctype}")
		items = []
		for i in invs:
			items.append(
				{
					"doctype": doctype,
					"name": i.name,
					"date": str(i.posting_date) if i.posting_date else None,
					"party": i.party,
					"net": float(i.base_net_total or 0) - excluded.get(i.name, 0.0),
					"vat": vat_map.get(i.name, 0.0),
					"is_return": bool(i.is_return),
				}
			)
		return items

	sales = _load(SALES_INVOICE, "customer")
	purchases = _load(PURCHASE_INVOICE, "supplier")
	# NEVER show a silent £0: an unmapped VAT account means we can't identify VAT on
	# any invoice, so warn loudly (and block filing) instead of returning fake zeros.
	warnings, blocking = _account_mapping_warnings(stray, unclassified)
	rc_vat = _reverse_charge_notional([p["name"] for p in purchases])
	boxes = compute_boxes(sales, purchases, reverse_charge={"vat": rc_vat})
	boxes, adjustments = _apply_adjustments(boxes, from_date, to_date)
	warnings.extend(_double_count_warnings(from_date, to_date))
	warnings.extend(_partial_exemption_warning(boxes, [s["name"] for s in sales]))
	warnings.extend(_null_classification_warning([s["name"] for s in sales]))
	warnings.extend(_reconciliation_warning(boxes, "accrual", from_date, to_date))
	return {
		"ok": True,
		"warnings": warnings,
		"blocking": blocking,
		"basis": "accrual",
		"from": from_date,
		"to": to_date,
		"boxes": boxes,
		"sales": sales,
		"purchases": purchases,
		"adjustments": adjustments,
	}


def _partial_exemption_warning(boxes, sales_invoice_names):
	"""M0-A PE-1: honesty guard for partially exempt businesses.

	A business making exempt supplies usually may NOT reclaim all its input VAT
	(partial exemption, VAT Notice 706) — but this app currently reclaims the
	full amount in Box 4. Full apportionment (standard method, de minimis,
	annual adjustment) is a roadmap item; until it ships, the honest minimum is
	to DETECT the situation and warn, instead of silently overstating the
	reclaim on a legal return.
	"""
	if not boxes.get("box4") or not sales_invoice_names:
		return []
	exempt_templates = {
		t.item_tax_template for t in vat_treatments() if t.vat_treatment == "Exempt"
	}
	if not exempt_templates:
		return []
	exempt_net = sum(
		_excluded_net_by_parent(
			f"{SALES_INVOICE} Item", SALES_INVOICE, sales_invoice_names, exempt_templates
		).values()
	)
	if not exempt_net:
		return []
	return [
		f"Exempt supplies of £{abs(exempt_net):,.2f} found in this period. Businesses making "
		"exempt supplies usually cannot reclaim ALL input VAT (partial exemption, VAT Notice 706) "
		"— Box 4 currently reclaims the full amount and may be overstated. Review the input tax "
		"attributable to exempt supplies before filing."
	]


def _pe_annual_adjustment(from_date, to_date):
	"""Pro (Partial Exemption annual, VAT Notice 706) — the engine lives in the Pro
	add-on's pro_engines; the base edition returns an upsell."""
	return _pro("pe_annual_adjustment", _upsell("Partial Exemption annual adjustment"), from_date, to_date)


def _pe_annual_from_figures(box6, box4, exempt_net, months):
	"""Pro (PE annual pure calc) — engine in the Pro add-on; base returns 'not applicable'."""
	return _pro("pe_annual_from_figures", {"applicable": False, "recovery_pct": 100, "adjustment": 0.0},
				box6, box4, exempt_net, months)


@frappe.whitelist()
def pe_annual_adjustment(from_date, to_date):
	"""Compute the standard-method Partial Exemption annual adjustment for a VAT year."""
	_require()
	return _pe_annual_adjustment(from_date, to_date)


@frappe.whitelist()
def cgs_preview(total_input_vat, intervals, baseline_use_pct, interval_use_pct):
	"""Pro (Capital Goods Scheme interval preview, Notice 706/2)."""
	_require()
	return _pro("cgs_preview", _upsell("Capital Goods Scheme"),
				total_input_vat, intervals, baseline_use_pct, interval_use_pct)


@frappe.whitelist()
def create_cgs_schedule(company, total_input_vat, intervals, acquisition_date, baseline_use_pct,
						reference_doctype=None, reference_name=None):
	"""Pro (Capital Goods Scheme 5/10-interval schedule, Notice 706/2)."""
	_require("write")
	return _pro("create_cgs_schedule", _upsell("Capital Goods Scheme"), company, total_input_vat,
				intervals, acquisition_date, baseline_use_pct, reference_doctype, reference_name)


def _null_classification_warning(sales_invoice_names):
	"""B92 backstop: sales lines carrying NO item_tax_template are unclassified by
	OMISSION. They are invisible to _partial_exemption_warning / _unclassified_templates
	(there is no template to be unclassified), so an exempt/outside-scope supply raised
	with a bare line leaves Box 4 silently overstated with no other warning. Detect the
	null lines themselves, and do NOT trust any upstream app (e.g. Block Management) to
	classify supplies for us. See [[broadcast-vat-treatment-expressed-by-omission-is-invisible]].
	"""
	if not sales_invoice_names:
		return []
	null_lines = frappe.db.count(
		f"{SALES_INVOICE} Item",
		{
			"parenttype": SALES_INVOICE,
			"parent": ["in", sales_invoice_names],
			"item_tax_template": ["is", "not set"],
		},
	)
	if not null_lines:
		return []
	# Elevate when the registration is KNOWN to make non-standard supplies: an unclassified
	# line on a partially-exempt/outside-scope registration is a Box 4 overstatement risk.
	mixed = any(t.vat_treatment in ("Exempt", "Outside scope") for t in vat_treatments())
	if mixed:
		return [
			f"{null_lines} sales line(s) carry NO VAT liability classification "
			"(item_tax_template unset). This registration is configured for exempt or "
			"outside-scope supplies, so leaving lines unclassified risks overstating Box 4 "
			"input VAT (partial exemption, VAT Notice 706). Classify every sales line before filing."
		]
	return [
		f"{null_lines} sales line(s) carry no VAT liability classification (item_tax_template "
		"unset) — their treatment is undetermined. If any relate to exempt or outside-scope "
		"supplies, Box 4 may be overstated. Set an Item Tax Template on every sales line."
	]


def _cash_load_side(ref_doctype, payment_type, from_date, to_date, company):
	"""One cash-basis side (sales via Receive / purchases via Pay): the paid
	fraction of each allocated invoice's net+VAT. Lifted out of _cash_basis_figures
	(CH-1) — pure move, arithmetic unchanged."""
	pe_filters = {
		"docstatus": 1,
		"posting_date": ["between", [from_date, to_date]],
		"payment_type": payment_type,
	}
	if company:
		pe_filters["company"] = company
	pes = {
		p.name: p
		for p in frappe.get_all(
			"Payment Entry", filters=pe_filters, fields=["name", "posting_date", "party"]
		)
	}
	if not pes:
		return []
	refs = frappe.get_all(
		"Payment Entry Reference",
		filters={"parent": ["in", list(pes)], "reference_doctype": ref_doctype},
		fields=["parent", "reference_name", "allocated_amount"],
	)
	inv_names = list({r.reference_name for r in refs if r.reference_name})
	if not inv_names:
		return []
	info = {
		i.name: i
		for i in frappe.get_all(
			ref_doctype,
			filters={"name": ["in", inv_names]},
			fields=["name", "base_net_total", "base_grand_total", "is_return", "vat_cash_excluded"],
		)
	}
	child = SALES_TAXES if ref_doctype == SALES_INVOICE else PURCHASE_TAXES
	item_child = f"{ref_doctype} Item"
	accts = vat_accounts(OUTPUT_VAT if ref_doctype == SALES_INVOICE else INPUT_VAT)
	vat_map = _vat_by_parent(child, ref_doctype, inv_names, accts)
	# Outside-scope net is excluded before apportioning the paid fraction.
	excluded = _excluded_net_by_parent(item_child, ref_doctype, inv_names, _outside_scope_templates())
	items = []
	for r in refs:
		row = _cash_paid_row(r, ref_doctype, info, pes, vat_map, excluded)
		if row:
			items.append(row)
	return items


def _cash_paid_row(r, ref_doctype, info, pes, vat_map, excluded):
	"""Build one cash-basis row for a payment allocation, or None to skip it.
	Skips unknown invoices, Notice 731-excluded supplies (accrual-based elsewhere),
	and zero-total invoices."""
	inv = info.get(r.reference_name)
	if not inv or inv.get("vat_cash_excluded"):
		return None
	grand = float(inv.base_grand_total or 0)
	if not grand:
		return None
	frac = float(r.allocated_amount or 0) / grand
	pe = pes[r.parent]
	net_in_scope = float(inv.base_net_total or 0) - excluded.get(r.reference_name, 0.0)
	return {
		"doctype": ref_doctype,
		"name": r.reference_name,
		"date": str(pe.posting_date) if pe.posting_date else None,
		"party": pe.party,
		"net": net_in_scope * frac,
		"vat": vat_map.get(r.reference_name, 0.0) * frac,
		"is_return": bool(inv.is_return),
	}


def _cash_excluded_side(ref_doctype, from_date, to_date, company):
	"""Notice 731 §6 exclusions on the CASH scheme: supplies flagged
	vat_cash_excluded are accounted on the INVOICE date (accrual basis), regardless
	of payment, so the return is correct for HP/lease, reverse charge, imports and
	6-month+ terms. Returns items shaped like _cash_load_side (full net+VAT, not a
	paid fraction)."""
	base = {"docstatus": 1, "posting_date": ["between", [from_date, to_date]]}
	if company:
		base["company"] = company
	# §6 flagged (HP/lease, imports, >6mo, deposits) OR reverse charge (§6.2, always excluded).
	names = {i.name for i in frappe.get_all(ref_doctype, filters={**base, "vat_cash_excluded": 1}, fields=["name"])}
	names |= _reverse_charge_invoice_names(ref_doctype, from_date, to_date, company)
	if not names:
		return []
	invs = frappe.get_all(
		ref_doctype, filters={"name": ["in", list(names)]},
		fields=["name", "base_net_total", "is_return", "posting_date"],
	)
	names = [i.name for i in invs]
	child = SALES_TAXES if ref_doctype == SALES_INVOICE else PURCHASE_TAXES
	item_child = f"{ref_doctype} Item"
	accts = vat_accounts(OUTPUT_VAT if ref_doctype == SALES_INVOICE else INPUT_VAT)
	vat_map = _vat_by_parent(child, ref_doctype, names, accts)
	excluded_net = _excluded_net_by_parent(item_child, ref_doctype, names, _outside_scope_templates())
	items = []
	for inv in invs:
		items.append({
			"doctype": ref_doctype,
			"name": inv.name,
			"date": str(inv.posting_date) if inv.posting_date else None,
			"party": None,
			"net": float(inv.base_net_total or 0) - excluded_net.get(inv.name, 0.0),
			"vat": vat_map.get(inv.name, 0.0),
			"is_return": bool(inv.is_return),
			"notice_731_excluded": True,
		})
	return items


def _cash_exclusion_warnings(from_date, to_date, company):
	"""Notice 731 §6.2 guard: on the cash scheme, an invoice whose payment falls due
	6+ months after its date should be accounted on the invoice date (flagged
	vat_cash_excluded). Warn — don't silently mis-account — when one isn't flagged, so
	the preparer can tick it. Non-blocking (advisory; the preparer confirms)."""
	from frappe.utils import add_months, getdate

	warnings = []
	for dt in (SALES_INVOICE, PURCHASE_INVOICE):
		filters = {
			"docstatus": 1,
			"posting_date": ["between", [from_date, to_date]],
			"vat_cash_excluded": 0,
		}
		if company:
			filters["company"] = company
		for inv in frappe.get_all(dt, filters=filters,
								  fields=["name", "posting_date", "due_date"]):
			if not inv.due_date or not inv.posting_date:
				continue
			cutoff = add_months(getdate(inv.posting_date), CASH_EXCLUSION_DUE_MONTHS)
			if getdate(inv.due_date) >= cutoff:
				warnings.append(
					f"{dt} {inv.name}: payment is due 6+ months after the invoice date. "
					"Under VAT Notice 731 §6 this is excluded from cash accounting — tick "
					"'Excluded from Cash Accounting' so it's counted on the invoice date."
				)

	# §6 also excludes imported goods / acquisitions: import VAT is accounted at the
	# border (or via postponed VAT accounting), NOT on payment to the supplier. We can't
	# reliably tell goods from services per line, so ADVISE (never auto-exclude — a wrong
	# exclusion mis-states a legal return). Signal: a non-UK supplier. Skip reverse-charge
	# invoices (services already invoice-accounted) so we don't double-flag.
	_UK = {"united kingdom", "uk", "gb", "great britain", "england", "scotland", "wales", "northern ireland"}
	pi_filters = {"docstatus": 1, "posting_date": ["between", [from_date, to_date]], "vat_cash_excluded": 0}
	if company:
		pi_filters["company"] = company
	pis = frappe.get_all(PURCHASE_INVOICE, filters=pi_filters, fields=["name", "supplier"])
	rc_names = set(_reverse_charge_invoice_names(PURCHASE_INVOICE, from_date, to_date, company))
	suppliers = {p.supplier for p in pis if p.supplier}
	country_by_supplier = {}
	if suppliers:
		for s in frappe.get_all("Supplier", filters={"name": ["in", list(suppliers)]}, fields=["name", "country"]):
			country_by_supplier[s.name] = s.country
	for p in pis:
		if p.name in rc_names:
			continue
		country = (country_by_supplier.get(p.supplier) or "").strip()
		if country and country.lower() not in _UK:
			warnings.append(
				f"Purchase Invoice {p.name}: supplier is in {country}. Imported goods and "
				"acquisitions are excluded from cash accounting under VAT Notice 731 §6 "
				"(import VAT is accounted at the border / via postponed VAT accounting, not on "
				"payment). If this invoice is for imported goods, tick 'Excluded from Cash Accounting'."
			)
	return warnings


def _cash_basis_figures(from_date, to_date):
	"""Cash-basis: boxes from PAYMENTS allocated in the period (Payment Entry).

	For each payment allocation to an invoice, the VAT/net counted is the paid
	fraction (allocated / invoice grand total) of that invoice's VAT/net — so
	partial payments and credit notes are handled proportionally. Invoices flagged
	under Notice 731 §6 are instead accounted on the invoice date (_cash_excluded_side).
	"""
	company = frappe.db.get_single_value(GLOBAL_DEFAULTS, "default_company")
	sales = _cash_load_side(SALES_INVOICE, "Receive", from_date, to_date, company)
	sales += _cash_excluded_side(SALES_INVOICE, from_date, to_date, company)
	purchases = _cash_load_side(PURCHASE_INVOICE, "Pay", from_date, to_date, company)
	purchases += _cash_excluded_side(PURCHASE_INVOICE, from_date, to_date, company)
	# NEVER show a silent £0: an unmapped VAT account means we can't identify VAT on
	# any invoice, so warn loudly (and block filing) instead of returning fake zeros.
	# Cash has no per-line *treatment* step, but the B92 backstop (sales lines with
	# item_tax_template unset) is scheme-independent, so it still applies below.
	warnings, blocking = _account_mapping_warnings()
	# DRC (Notice 735 + 731 §6.2): reverse charge is invoice-accounted even on the cash
	# scheme, so the customer's notional VAT still self-accounts into Box 1 and Box 4.
	rc_vat = _reverse_charge_notional(list(_reverse_charge_invoice_names(PURCHASE_INVOICE, from_date, to_date, company)))
	boxes = compute_boxes(sales, purchases, reverse_charge={"vat": rc_vat})
	boxes, adjustments = _apply_adjustments(boxes, from_date, to_date)
	warnings.extend(_double_count_warnings(from_date, to_date))
	warnings.extend(_partial_exemption_warning(boxes, [s["name"] for s in sales]))
	# B92 backstop on the cash path too (P0-1): an exempt/outside-scope sales line
	# raised with no item_tax_template overstates Box 4 the same way regardless of
	# scheme. Dedupe names — one invoice can appear across several payment allocations.
	warnings.extend(_null_classification_warning(list({s["name"] for s in sales})))
	warnings.extend(_cash_exclusion_warnings(from_date, to_date, company))
	return {
		"ok": True,
		"warnings": warnings,
		"blocking": blocking,
		"basis": "cash",
		"from": from_date,
		"to": to_date,
		"boxes": boxes,
		"adjustments": adjustments,
		"sales": sales,
		"purchases": purchases,
	}


@frappe.whitelist()
def get_vat_transactions(from_date=None, to_date=None):
	"""VAT-relevant transactions (sales + purchase invoices) for the Reports screen.

	All-in-one principle: the report renders INSIDE the cockpit; each row drills
	through to the exact invoice (the only exit to the standard desk form). Reads
	real submitted invoices — data-truthful, no fabricated figures.

	`tax` is VAT-ONLY (same `_vat_by_parent` extraction the 9-box math uses), so this
	screen and Prepare Return can never disagree about the same invoice's VAT.
	"""
	_require()
	from frappe.utils import add_to_date, nowdate

	company = frappe.db.get_single_value(GLOBAL_DEFAULTS, "default_company")
	to_date = to_date or nowdate()
	from_date = from_date or add_to_date(to_date, months=-12)

	filters = {"docstatus": 1, "posting_date": ["between", [from_date, to_date]]}
	if company:
		filters["company"] = company

	rows = []
	for doctype, party_field, sign in (
		(SALES_INVOICE, "customer", "Sales"),
		(PURCHASE_INVOICE, "supplier", "Purchase"),
	):
		invs = frappe.get_all(
			doctype,
			filters=filters,
			fields=[
				"name",
				"posting_date",
				f"{party_field} as party",
				"base_net_total",
				"base_grand_total",
			],
			order_by="posting_date desc",
		)
		child = SALES_TAXES if doctype == SALES_INVOICE else PURCHASE_TAXES
		accts = vat_accounts(OUTPUT_VAT if doctype == SALES_INVOICE else INPUT_VAT)
		names = [i.name for i in invs]
		vat_map = _vat_by_parent(child, doctype, names, accts)
		for d in invs:
			rows.append(
				{
					"doctype": doctype,
					"type": sign,
					"name": d.name,
					"date": str(d.posting_date) if d.posting_date else None,
					"party": d.party,
					"net": d.base_net_total,
					"tax": vat_map.get(d.name, 0.0),
					"total": d.base_grand_total,
				}
			)

	rows.sort(key=lambda r: r["date"] or "", reverse=True)
	return {"ok": True, "from": from_date, "to": to_date, "rows": rows}


@frappe.whitelist()
def get_liabilities():
	"""Real outstanding/settled VAT liabilities for the connected VRN."""
	_require()
	return _dated_list("liabilities", "liabilities", "liabilities")


def _period_status_row(o, by_period, today):
	"""One filing+payment status row for a single obligation. Extracted so
	get_period_status stays a thin orchestration loop (CH-1)."""
	from frappe.utils import date_diff

	due = o.get("due")
	lia = by_period.get((o.get("start"), o.get("end")))
	outstanding = float(lia.get("outstandingAmount") or 0) if lia else None
	days = date_diff(due, today) if due else None
	return {
		"periodKey": o.get("periodKey"),
		"start": o.get("start"),
		"end": o.get("end"),
		"due": due,
		"filed": o.get("status") == "F",
		"received": o.get("received"),
		"amount_due": float(lia.get("originalAmount") or 0) if lia else None,
		"outstanding": outstanding,
		"paid": (outstanding == 0) if lia else None,
		"days_to_due": days,
		"filing_overdue": bool(due and o.get("status") != "F" and days is not None and days < 0),
		"payment_overdue": bool(lia and outstanding and days is not None and days < 0),
	}


@frappe.whitelist()
def get_period_status():
	"""Filing AND payment status per VAT period — these are two separate duties.

	Filing (obligations) and paying each carry their own deadline; a period is only
	truly 'done' when BOTH are complete. Payment happens on the HMRC portal/bank —
	never from ERPNext — so we can only READ its state, which is what the
	liabilities + payments endpoints provide. Returns periods OLDEST-FIRST with
	overdue flags (late filers can have several open periods at once).
	"""
	_require()
	from frappe.utils import nowdate

	obl = get_obligations()
	if not obl.get("ok"):
		return obl
	liab = get_liabilities()
	pay = get_payments()

	# Index liabilities by tax period so a filing period can be matched to money owed.
	by_period = {}
	for item in liab.get("items", []) if liab.get("ok") else []:
		tp = item.get("taxPeriod") or {}
		by_period[(tp.get("from"), tp.get("to"))] = item

	today = nowdate()
	periods = [_period_status_row(o, by_period, today) for o in obl.get("obligations", [])]

	# Oldest first — overdue periods must be dealt with before current ones.
	periods.sort(key=lambda p: p["start"] or "")
	# Return the raw lists too so the Dashboard needs ONE round trip, not three
	# (HMRC allows only 3 req/s, and each call also rebuilds the FPH headers).
	return {
		"ok": True,
		"periods": periods,
		"liabilities": liab.get("items", []) if liab.get("ok") else [],
		"payments": pay.get("items", []) if pay.get("ok") else [],
		"liabilities_ok": bool(liab.get("ok")),
		"payments_ok": bool(pay.get("ok")),
		"liabilities_message": None if liab.get("ok") else liab.get("message"),
		"payments_message": None if pay.get("ok") else pay.get("message"),
	}


@frappe.whitelist()
def get_payments():
	"""Real VAT payments HMRC has received for the connected VRN."""
	_require()
	return _dated_list("payments", "payments", "payments")
