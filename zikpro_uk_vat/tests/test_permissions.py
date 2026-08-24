"""Access-control tests for the VAT cockpit — run as a NON-Administrator user.

Broadcast B29: the framework disables role checks, permission-query conditions,
workflow self-approval and field masking for Administrator. Every other test in
this app (and 267/270 across the portfolio) runs as Administrator, so no
assertion about who-can-do-what has ever actually been evaluated — the controls
are switched off for the only user the tests use.

These tests de-escalate with frappe.set_user(<non-admin>) and prove that the
_require() gate and the SEC-12 legacy-OAuth neutralisation actually block an
ordinary user. Without this, "the cockpit is permission-gated" is an unverified
claim. Run: bench --site <site> run-tests --module \
  zikpro_uk_vat.tests.test_permissions
"""

import frappe
from frappe.tests.utils import FrappeTestCase

from zikpro_uk_vat import cockpit, security

_TEST_USER = "vat_nonadmin@example.com"


class TestCockpitPermissions(FrappeTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		# A plain enabled user with NO VAT-relevant role. VAT Settings + the VAT
		# Return grant read/write/submit to System Manager only, so this user must
		# be refused by _require() on every gated endpoint.
		if not frappe.db.exists("User", _TEST_USER):
			user = frappe.get_doc(
				{
					"doctype": "User",
					"email": _TEST_USER,
					"first_name": "VAT",
					"last_name": "NonAdmin",
					"send_welcome_email": 0,
				}
			)
			user.insert(ignore_permissions=True)
		# Ensure they are NOT a System Manager (the only role that can use the cockpit).
		roles = {r.role for r in frappe.get_doc("User", _TEST_USER).roles}
		cls.assertNotInAdmin = "System Manager" not in roles

	def tearDown(self):
		frappe.set_user("Administrator")

	def test_nonadmin_user_is_not_system_manager(self):
		# Guard: if this user somehow had System Manager, every assertion below
		# would pass for the wrong reason (B29's exact trap).
		frappe.set_user(_TEST_USER)
		self.assertNotIn("System Manager", frappe.get_roles())

	def test_read_endpoint_blocked_for_nonadmin(self):
		frappe.set_user(_TEST_USER)
		with self.assertRaises(frappe.PermissionError):
			cockpit.get_dashboard_data()

	def test_write_endpoint_blocked_for_nonadmin(self):
		frappe.set_user(_TEST_USER)
		with self.assertRaises(frappe.PermissionError):
			cockpit.save_settings(billing_contact="hijack@example.com")

	def test_submit_return_blocked_for_nonadmin(self):
		frappe.set_user(_TEST_USER)
		with self.assertRaises(frappe.PermissionError):
			cockpit.submit_return(
				period_key="18A2", from_date="2017-04-01", to_date="2017-06-30"
			)

	def test_require_write_is_stricter_than_read(self):
		# The read/write split must be real: _require('write') has to fail for a
		# user even in contexts where a bare read gate might be looser.
		frappe.set_user(_TEST_USER)
		with self.assertRaises(frappe.PermissionError):
			cockpit._require("write")
		with self.assertRaises(frappe.PermissionError):
			cockpit._require("read")

	def test_sec12_legacy_oauth_refuses_everyone(self):
		# The neutralised legacy endpoints must refuse regardless of who calls —
		# they carried the tokens-in-URL / guest-writable P0s.
		frappe.set_user(_TEST_USER)
		with self.assertRaises(frappe.PermissionError):
			security.disabled_legacy_oauth()

	def test_administrator_can_still_read(self):
		# Sanity/oracle check: the gate is not simply "always throw". A privileged
		# user must pass — otherwise the block tests above prove nothing.
		frappe.set_user("Administrator")
		# Should not raise.
		cockpit._require("read")
		cockpit._require("write")


def prove_b29():
	"""Runnable proof (bench execute) that the gates block a non-admin, for use on
	a dev site where the full test-runner bootstrap collides with demo data.
	Returns a dict of check -> bool; True means the gate behaved correctly."""
	import frappe as _f
	if not _f.db.exists("User", _TEST_USER):
		_f.get_doc({"doctype": "User", "email": _TEST_USER, "first_name": "VAT",
					"last_name": "NonAdmin", "send_welcome_email": 0}).insert(ignore_permissions=True)
		_f.db.commit()
	res = {}

	def _blocked(fn):
		try:
			fn()
			return False
		except _f.PermissionError:
			return True

	_f.set_user(_TEST_USER)
	res["nonadmin_is_not_sysmgr"] = "System Manager" not in _f.get_roles()
	res["read_blocked"] = _blocked(lambda: cockpit.get_dashboard_data())
	res["write_blocked"] = _blocked(lambda: cockpit.save_settings(billing_contact="hijack@x.com"))
	res["submit_blocked"] = _blocked(lambda: cockpit.submit_return("18A2", "2017-04-01", "2017-06-30"))
	res["require_read_blocked"] = _blocked(lambda: cockpit._require("read"))
	res["require_write_blocked"] = _blocked(lambda: cockpit._require("write"))
	res["sec12_refuses"] = _blocked(lambda: security.disabled_legacy_oauth())
	_f.set_user("Administrator")
	res["oracle_admin_allowed"] = not _blocked(lambda: cockpit._require("write"))
	_f.set_user("Administrator")
	passed = sum(1 for v in res.values() if v)
	print(f"B29 PROOF {passed}/{len(res)}: {res}", flush=True)
	return res


def prove_ux2b():
	"""Proof (bench execute) that submit_return REFUSES a return with incomplete
	VAT figures (unmapped accounts) even with a valid declaration, and does not
	file. Restores the mapping afterwards. Returns a dict of check -> bool."""
	import frappe as _f
	from zikpro_uk_vat import cockpit as _c

	sn = _c._connection()["settings"]
	doc = _f.get_doc("VAT Settings", sn)
	saved = [(a.vat_type, a.account, a.description) for a in doc.vat_accounts]
	# Determinism: this proves submit refuses on INCOMPLETE FIGURES, not the live HMRC
	# already-filed guard. Stub get_return so 18A2 reads OPEN and submit_return reaches
	# the blocking-figures check regardless of the sandbox's state for this VRN.
	_orig_get_return = _c.get_return
	_c.get_return = lambda period_key: {"ok": True, "filed": False, "boxes": {}}
	_orig_connection = _c._connection
	_stub_conn = {**_orig_connection(), "connected": True, "vrn_mismatch": False}
	_stub_conn["vrn"] = _stub_conn.get("vrn") or "567643603"
	_c._connection = lambda *a, **k: _stub_conn
	res = {}
	try:
		# 1. clear the account mapping -> figures become blocking
		doc.vat_accounts = []
		doc.save(ignore_permissions=True)
		_f.db.commit()
		fig = _c.get_return_figures("2017-04-01", "2017-06-30")
		res["figures_blocking_when_unmapped"] = fig.get("blocking") is True

		# 2. try to FILE it with a valid declaration -> must refuse, must not file
		out = _c.submit_return("18A2", "2017-04-01", "2017-06-30", finalised=True)
		res["submit_refused"] = out.get("ok") is False
		res["submit_not_filed"] = not out.get("filed") and not out.get("already_filed")
		res["submit_flags_blocking_or_guard"] = bool(out.get("blocking")) or "incomplete" in (out.get("message") or "").lower()
	finally:
		# 3. restore the mapping + the stubbed HMRC check
		_c.get_return = _orig_get_return
		_c._connection = _orig_connection
		doc = _f.get_doc("VAT Settings", sn)
		doc.vat_accounts = []
		for vt, acc, desc in saved:
			doc.append("vat_accounts", {"vat_type": vt, "account": acc, "description": desc})
		doc.save(ignore_permissions=True)
		_f.db.commit()
		res["restored_not_blocking"] = _c.get_return_figures("2017-04-01", "2017-06-30").get("blocking") is False

	passed = sum(1 for v in res.values() if v)
	print(f"UX-2B PROOF {passed}/{len(res)}: {res}", flush=True)
	return res


def prove_sysmgr_access():
	"""Regression proof for the SEC-11 Custom-DocPerm lockout (found on cloud).

	Frappe uses Custom DocPerm EXCLUSIVELY once any exists for a DocType, so adding
	the VAT roles silently stripped System Manager (which lived only in the standard
	.json DocPerm) of all access — every System Manager got 403 from _require. This
	must be proven as a PLAIN System Manager, never Administrator (who bypasses
	permissions and hid the bug — B29). Returns check -> bool."""
	import frappe as _f
	from zikpro_uk_vat import cockpit as _c, install as _i

	SM = "vat_sysmgr@example.com"
	if not _f.db.exists("User", SM):
		_f.get_doc({"doctype": "User", "email": SM, "first_name": "VAT", "last_name": "SysMgr",
					"send_welcome_email": 0, "user_type": "System User"}).insert(ignore_permissions=True)
	u = _f.get_doc("User", SM)
	have = {r.role for r in u.roles}
	for r in ("System Manager",):
		if r not in have:
			u.append("roles", {"role": r})
	# explicitly NOT a VAT role — access must come purely from System Manager.
	u.save(ignore_permissions=True)
	_i.ensure_vat_roles_and_perms()  # self-heal must be idempotent + include SysMgr
	_f.clear_cache()
	_f.db.commit()

	res = {}
	_f.set_user(SM)
	res["sysmgr_not_a_vat_role"] = not {"UK VAT Preparer", "UK VAT Approver"} & set(_f.get_roles())
	res["sysmgr_reads_settings"] = _f.has_permission("VAT Settings", "read")
	res["sysmgr_writes_settings"] = _f.has_permission("VAT Settings", "write")
	res["sysmgr_submits_return"] = _f.has_permission("UK MTD VAT Return", "submit")

	def _passes(fn):
		try:
			fn(); return True
		except _f.PermissionError:
			return False
	res["sysmgr_require_read_passes"] = _passes(lambda: _c._require("read"))
	res["sysmgr_require_write_passes"] = _passes(lambda: _c._require("write"))
	_f.set_user("Administrator")

	passed = sum(1 for v in res.values() if v)
	print(f"SYSMGR-ACCESS PROOF {passed}/{len(res)}: {res}", flush=True)
	return res


def prove_sec11():
	"""Proof (bench execute) of the preparer/approver segregation of duties:
	role gates block the wrong role, and an approver cannot file a return they
	prepared. Does NOT file to HMRC — points the draft at an already-filed period
	so the double-submit guard stops it before any POST. Returns check -> bool."""
	import frappe as _f
	from zikpro_uk_vat import cockpit as _c

	PREP, APPR, BOTH = "vat_prep@example.com", "vat_appr@example.com", "vat_both@example.com"

	def _ensure(email, roles):
		if not _f.db.exists("User", email):
			_f.get_doc({"doctype": "User", "email": email, "first_name": email.split("@")[0],
						"send_welcome_email": 0}).insert(ignore_permissions=True)
		u = _f.get_doc("User", email)
		have = {r.role for r in u.roles}
		for r in roles:
			if r not in have:
				u.append("roles", {"role": r})
		u.save(ignore_permissions=True)

	_ensure(PREP, [_c.PREPARER_ROLE])
	_ensure(APPR, [_c.APPROVER_ROLE])
	_ensure(BOTH, [_c.PREPARER_ROLE, _c.APPROVER_ROLE])
	_f.db.commit()

	res = {}

	def _blocked(fn):
		try:
			out = fn()
			# a returned {"ok": False, ...} is a soft refusal, not a hard perm block
			return isinstance(out, dict) and out.get("ok") is False and "permitted" in str(out).lower()
		except _f.PermissionError:
			return True

	# clean any stale draft for the test period
	for n in _f.get_all("UK MTD VAT Return", filters={"reference_key": ["in", ["18A2", "18A1"]], "docstatus": 0}, pluck="name"):
		_f.delete_doc("UK MTD VAT Return", n, force=True, ignore_permissions=True)
	_f.db.commit()

	# Determinism: this proves the SoD/role gates, NOT live HMRC obligation state.
	# Stub cockpit.get_return so 18A2 is treated as OPEN (prepare succeeds) and 18A1
	# as FILED (step 7's double-submit guard fires) regardless of the sandbox's
	# current state for this VRN — otherwise a period filed in a prior session (e.g.
	# by the smoke harness) makes this test flap.
	_orig_get_return = _c.get_return
	_c.get_return = lambda period_key: {"ok": True, "filed": period_key == "18A1", "boxes": {}}
	# ...and a connected state, so the SoD gates are what's under test — not whether
	# this dev site currently holds a valid HMRC token (the smoke harness deliberately
	# leaves the connection pointing at a throwaway VRN).
	_orig_connection = _c._connection
	_stub_conn = {**_orig_connection(), "connected": True, "vrn_mismatch": False}
	_stub_conn["vrn"] = _stub_conn.get("vrn") or "567643603"
	_c._connection = lambda *a, **k: _stub_conn

	# 1. non-preparer cannot prepare
	_f.set_user(APPR)
	res["nonpreparer_cannot_prepare"] = _blocked(lambda: _c.prepare_return("18A2", "2017-04-01", "2017-06-30"))

	# 2. preparer CAN prepare -> draft awaiting approval, prepared_by = preparer
	_f.set_user(PREP)
	out = _c.prepare_return("18A2", "2017-04-01", "2017-06-30")
	res["preparer_can_prepare"] = out.get("ok") is True
	draft = out.get("return_name")
	res["draft_prepared_by_is_preparer"] = _f.db.get_value("UK MTD VAT Return", draft, "prepared_by") == PREP
	res["draft_awaiting_approval"] = _f.db.get_value("UK MTD VAT Return", draft, "approval_status") == "Awaiting Approval"

	# 3. preparer cannot approve (no approver role)
	res["preparer_cannot_approve"] = _blocked(lambda: _c.approve_and_submit(draft, finalised=True))

	# 4. non-approver (fresh nonadmin) cannot even list pending approvals
	_f.set_user("vat_nonadmin@example.com")
	res["nonapprover_cannot_list"] = _blocked(lambda: _c.list_pending_approvals())

	# 5. approver CAN see the pending draft
	_f.set_user(APPR)
	pend = _c.list_pending_approvals()
	res["approver_sees_pending"] = any(r["name"] == draft for r in pend.get("rows", []))

	# 6. SoD: a user with BOTH roles who PREPARED it cannot approve it
	_f.set_user(BOTH)
	out2 = _c.prepare_return("18A2", "2017-04-01", "2017-06-30")  # re-prepare -> prepared_by = BOTH
	draft2 = out2.get("return_name")
	sod_blocked = False
	try:
		_c.approve_and_submit(draft2, finalised=True)
	except _f.PermissionError as e:
		sod_blocked = "segregation of duties" in str(e).lower()
	res["self_approval_blocked_SoD"] = sod_blocked

	# 7. a DIFFERENT approver passes role+SoD (proven by reaching the double-submit
	#    guard on an already-filed period, NOT by actually filing). Point the draft
	#    at 18A1 (filed on sandbox) so no HMRC POST happens.
	_f.db.set_value("UK MTD VAT Return", draft2, "reference_key", "18A1")
	_f.db.set_value("UK MTD VAT Return", draft2, "prepared_by", PREP)  # prepared by someone else
	_f.db.commit()
	_f.set_user(APPR)
	passed_gates = False
	try:
		out3 = _c.approve_and_submit(draft2, finalised=True)
		# reached beyond role+SoD: either already_filed guard or a connection/HMRC result
		passed_gates = isinstance(out3, dict)
	except _f.PermissionError:
		passed_gates = False
	res["different_approver_passes_gates"] = passed_gates

	# cleanup drafts + restore the stubbed HMRC check
	_c.get_return = _orig_get_return
	_c._connection = _orig_connection
	_f.set_user("Administrator")
	for n in _f.get_all("UK MTD VAT Return", filters={"reference_key": ["in", ["18A2", "18A1"]], "docstatus": 0}, pluck="name"):
		_f.delete_doc("UK MTD VAT Return", n, force=True, ignore_permissions=True)
	_f.db.commit()

	passed = sum(1 for v in res.values() if v)
	print(f"SEC-11 PROOF {passed}/{len(res)}: {res}", flush=True)
	return res


def prove_whitelist_gate():
	"""Every endpoint the Vue cockpit calls via frappe.call MUST be registered in
	frappe.whitelisted, and internal helpers must NOT be. The in-process prove_*
	tests call these functions directly, so they never exercise the whitelist gate —
	a stray/misplaced @frappe.whitelist() (decorator orphaned onto an adjacent helper)
	silently unexposes an endpoint and breaks the cockpit only in a real browser.
	This caught cockpit.get_return_figures losing its decorator during A1-2."""
	import frappe
	from zikpro_uk_vat import cockpit as _c

	res = {}
	endpoints = [
		"get_return_figures", "get_settings", "get_connection_status", "save_settings",
		"get_authorize_url", "get_obligations", "submit_return", "get_return",
		"prepare_return", "calculation_notes", "validate_fraud_headers", "reconcile_period",
		"create_adjustment", "list_adjustments", "run_schedule_generation",
	]
	for name in endpoints:
		fn = getattr(_c, name, None)
		res[f"exposed:{name}"] = bool(fn) and fn in frappe.whitelisted

	# internal helpers must stay private
	helpers = ["_flat_rate_pct", "_flat_rate_figures", "_cash_basis_figures",
			   "_apply_scheme_change", "_cash_excluded_side", "_require"]
	for name in helpers:
		fn = getattr(_c, name, None)
		res[f"private:{name}"] = bool(fn) and fn not in frappe.whitelisted

	passed = sum(1 for v in res.values() if v)
	bad = [k for k, v in res.items() if not v]
	print(f"WHITELIST-GATE PROOF {passed}/{len(res)}" + (f" FAILED: {bad}" if bad else ""), flush=True)
	return res


def prove_hmrc_audit_log():
	"""A2-1: every HMRC call writes a MASKED audit record — the bearer token is never
	stored, fraud-prevention headers are recorded by NAME only, and receipt/correlation
	IDs + status are captured. Uses a fake response so no live HMRC call is needed."""
	import time as _t
	import frappe as _f
	from zikpro_uk_vat import cockpit as _c

	LOG = "HMRC Request Log"

	class _Req:
		headers = {"Authorization": "Bearer SUPERSECRETTOKEN123",
				   "Gov-Client-Screens": "width=1600&height=900",
				   "Gov-Client-Timezone": "UTC+00:00", "Gov-Vendor-Version": "x"}

	class _Resp:
		def __init__(self, status, text=""):
			self.status_code = status
			self.headers = {"Receipt-ID": "rcpt-abc", "X-CorrelationId": "corr-123"}
			self.text = text
			self.request = _Req()

	sn = _c._connection()["settings"]
	# clean any prior test rows
	for n in _f.get_all(LOG, filters={"vrn": "123456789"}, pluck="name"):
		_f.delete_doc(LOG, n, force=True, ignore_permissions=True)

	res = {}
	# 1. a successful POST
	_c._log_hmrc_call("POST", "/organisations/vat/123456789/returns", sn, _Resp(201), _t.monotonic())
	# 2. an error GET whose body contains a token-like string -> must be masked
	_c._log_hmrc_call("GET", "/organisations/vat/123456789/obligations", sn,
					  _Resp(403, '{"error":"denied","leak":"Bearer ABCDEF.tok.99"}'), _t.monotonic())
	_f.db.commit()

	rows = _f.get_all(LOG, filters={"vrn": "123456789"},
					  fields=["method", "outcome", "http_status", "fph_headers", "error_summary",
							  "receipt_id", "correlation_id", "endpoint"])
	blob = frappe.as_json(rows)
	res["two_records_written"] = len(rows) == 2
	res["no_token_stored"] = "SUPERSECRETTOKEN123" not in blob
	res["no_authorization_header_logged"] = "Authorization" not in blob
	res["fph_by_name_only"] = any("Gov-Client-Screens" in (r.fph_headers or "") for r in rows)
	res["fph_values_not_stored"] = "width=1600" not in blob
	res["receipt_and_correlation_captured"] = any(r.receipt_id == "rcpt-abc" and r.correlation_id == "corr-123" for r in rows)
	res["success_and_error_outcomes"] = {r.outcome for r in rows} == {"Success", "Error"}
	# error_summary is masked by security.redact_secrets (P3-2): the bearer is replaced by a
	# [REDACTED] marker (not the old literal 'Bearer ***') AND the token value is gone.
	res["error_body_bearer_masked"] = any("[REDACTED]" in (r.error_summary or "") for r in rows) and "Bearer ABCDEF" not in blob

	# 3. immutability: an existing log row cannot be edited
	imm_ok = False
	if rows:
		name = _f.get_all(LOG, filters={"vrn": "123456789"}, pluck="name")[0]
		d = _f.get_doc(LOG, name)
		d.method = "TAMPER"
		try:
			d.save(ignore_permissions=True)
		except _f.ValidationError:
			imm_ok = True
	res["log_is_immutable"] = imm_ok

	for n in _f.get_all(LOG, filters={"vrn": "123456789"}, pluck="name"):
		_f.delete_doc(LOG, n, force=True, ignore_permissions=True)
	_f.db.commit()

	passed = sum(1 for v in res.values() if v)
	print(f"HMRC-AUDIT-LOG PROOF {passed}/{len(res)}: {res}", flush=True)
	return res


def prove_broker_tenant():
	"""Phase 2: the tenant-side broker wiring. With use_broker set, get_authorize_url /
	complete_oauth / _refresh_token route through the broker (MOCKED) — signing each call
	with the tenant's shared_secret — and store the returned tokens locally. Direct mode
	is unaffected. No network / no real broker needed."""
	import hashlib
	import hmac
	import frappe as _f
	from zikpro_uk_vat import cockpit as _c

	sn = _c._connection()["settings"]
	doc = _f.get_doc("VAT Settings", sn)
	saved = {k: doc.get(k) for k in ("use_broker", "broker_url", "broker_tenant_id", "access_token", "refresh_token")}
	SECRET, TID = "tenant-secret-xyz", "tenantX"
	res, calls = {}, []
	orig = _c._broker_call

	def fake_call(broker_url, endpoint, payload):
		calls.append((endpoint, payload))
		base = {"authorize": f"{TID}|{payload.get('nonce')}",
				"exchange": f"{TID}|{payload.get('broker_code')}",
				"refresh": f"{TID}|{payload.get('refresh_token')}"}[endpoint]
		# the fake only succeeds if the tenant signed correctly with SECRET
		if hmac.new(SECRET.encode(), base.encode(), hashlib.sha256).hexdigest() != payload.get("sig"):
			return {"ok": False}
		return {
			"authorize": {"ok": True, "authorize_url": "https://test-api.service.hmrc.gov.uk/oauth/authorize?state=abc"},
			"exchange": {"ok": True, "access_token": "AT-broker", "refresh_token": "RT-broker"},
			"refresh": {"ok": True, "access_token": "AT-refreshed", "refresh_token": "RT-2"},
		}[endpoint]

	try:
		_c._broker_call = fake_call
		_f.db.set_value("VAT Settings", sn, {"use_broker": 1, "broker_url": "https://oauth.zikpro.com",
											 "broker_tenant_id": TID, "access_token": None, "refresh_token": None})
		d = _f.get_doc("VAT Settings", sn); d.broker_shared_secret = SECRET; d.save(ignore_permissions=True)
		_f.db.commit(); _f.clear_cache()

		a = _c.get_authorize_url()
		res["authorize_via_broker"] = bool(a.get("ok")) and "oauth/authorize" in a.get("url", "")
		res["authorize_hmac_signed_ok"] = bool(calls) and calls[-1][0] == "authorize"  # fake returned ok only if sig matched
		nonce = calls[-1][1]["nonce"]

		s2, err = _c.complete_oauth(broker_code="BC-123", nonce=nonce)
		res["complete_via_broker"] = err is None and s2 == sn
		res["tokens_stored_locally"] = _f.get_doc("VAT Settings", sn).get_password("access_token") == "AT-broker"

		res["refresh_via_broker"] = _c._refresh_token(sn) and \
			_f.get_doc("VAT Settings", sn).get_password("access_token") == "AT-refreshed"

		s3, err3 = _c.complete_oauth(broker_code="BC-x", nonce="wrong-nonce")
		res["bad_nonce_rejected"] = s3 is None and bool(err3)

		_f.db.set_value("VAT Settings", sn, "use_broker", 0); _f.clear_cache()
		res["direct_mode_when_off"] = _c._broker_settings(sn) is None
	finally:
		_c._broker_call = orig
		d = _f.get_doc("VAT Settings", sn)
		for k, v in saved.items():
			d.set(k, v)
		d.save(ignore_permissions=True); _f.db.commit(); _f.clear_cache()

	passed = sum(1 for v in res.values() if v)
	print(f"BROKER-TENANT PROOF {passed}/{len(res)}: {res}", flush=True)
	return res
