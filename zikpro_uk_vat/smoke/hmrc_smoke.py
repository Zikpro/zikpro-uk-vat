"""End-to-end HMRC sandbox smoke gate.

WHY THIS EXISTS: unit tests and seeded records cannot catch integration defects.
A real submission once failed to persist its audit record for weeks because HMRC
returns ISO8601 timestamps with 'Z'+milliseconds that MariaDB DATETIME rejects —
invisible to every unit test and to hand-seeded data. This harness walks the WHOLE
chain against the real sandbox and asserts each link, so that class of bug fails
loudly instead of silently.

    bench --site <site> execute zikpro_uk_vat.smoke.hmrc_smoke.run

It creates a throwaway HMRC test user, connects, files a return, then verifies
what HMRC holds AND what we stored. Sandbox only — never point it at production.
"""

import json
import time
import os
import subprocess
import sys

import frappe
import requests

from zikpro_uk_vat import cockpit

SANDBOX = "https://test-api.service.hmrc.gov.uk"
RETURN_DOCTYPE = "UK MTD VAT Return"


class _Results:
	def __init__(self):
		self.rows = []

	def check(self, step, ok, detail=""):
		self.rows.append({"step": step, "ok": bool(ok), "detail": str(detail)[:180]})
		print(f"[{'PASS' if ok else 'FAIL'}] {step}" + (f" — {detail}" if detail else ""), flush=True)
		return bool(ok)

	@property
	def failed(self):
		return [r for r in self.rows if not r["ok"]]


def _create_test_user(client_id, client_secret):
	"""Create a throwaway organisation test user enrolled for MTD VAT."""
	tok = requests.post(
		f"{SANDBOX}/oauth/token",
		data={
			"grant_type": "client_credentials",
			"client_id": client_id,
			"client_secret": client_secret,
			"scope": "read:vat write:vat",
		},
		timeout=30,
	)
	tok.raise_for_status()
	server_token = tok.json()["access_token"]
	resp = requests.post(
		f"{SANDBOX}/create-test-user/organisations",
		headers={
			"Authorization": f"Bearer {server_token}",
			"Accept": "application/vnd.hmrc.1.0+json",
			"Content-Type": "application/json",
		},
		data=json.dumps({"serviceNames": ["mtd-vat"]}),
		timeout=30,
	)
	resp.raise_for_status()
	return resp.json()


def mint_test_user():
	"""Generate a throwaway HMRC sandbox organisation (VRN) via the Create Test User
	API, using the sandbox client credentials in VAT Settings. Standalone — no browser
	or web server needed. Prints the VRN (and userId; password is withheld from logs).

	    bench --site <site> execute zikpro_uk_vat.smoke.hmrc_smoke.mint_test_user
	"""
	settings_name = cockpit._connection()["settings"]
	if not settings_name:
		print("No VAT Settings for the default company.", flush=True)
		return {"ok": False}
	client_id = frappe.db.get_value(cockpit.VAT_SETTINGS, settings_name, "client_id")
	client_secret = frappe.get_doc(cockpit.VAT_SETTINGS, settings_name).get_password("client_secret")
	if not (client_id and client_secret):
		print("HMRC client_id/secret not configured.", flush=True)
		return {"ok": False}
	user = _create_test_user(client_id, client_secret)
	vrn = user.get("vrn")
	print(f"SANDBOX TEST USER CREATED · vrn={vrn} · userId={user.get('userId')} "
		  f"· mtd-vat enrolled (password withheld)", flush=True)
	return {"ok": bool(vrn), "vrn": vrn, "userId": user.get("userId")}


def _grant(authorize_url, user_id, password):
	"""Drive the browser OAuth grant in a subprocess (Playwright needs its own process).

	The bench venv does not necessarily have Playwright installed — set
	HMRC_SMOKE_PYTHON to an interpreter that does (else we fall back to ours).
	"""
	script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_oauth_grant.py")
	python_bin = os.environ.get("HMRC_SMOKE_PYTHON") or sys.executable
	# nosemgrep: frappe-subprocess-exec -- dev-only smoke harness (never shipped in a
	# request path); argv is a static list, python_bin/script are code-controlled, and
	# the three interpolated values are a sandbox authorize URL + HMRC test-user creds.
	out = subprocess.run(  # noqa
		[python_bin, script, authorize_url, user_id, password],
		capture_output=True,
		text=True,
		timeout=300,
	)
	ok = "GRANT_OK" in (out.stdout or "")
	detail = (out.stdout or "").strip() if ok else (out.stderr or out.stdout or "").strip()[-160:]
	return ok, detail


def run():
	"""Walk the full chain against the HMRC sandbox and assert every link."""
	r = _Results()
	settings_name = cockpit._connection()["settings"]
	if not settings_name:
		r.check("VAT Settings exist", False, "no VAT Settings for the default company")
		return _finish(r)

	creds = frappe.db.get_value(cockpit.VAT_SETTINGS, settings_name, ["client_id"], as_dict=True)
	client_secret = frappe.get_doc(cockpit.VAT_SETTINGS, settings_name).get_password("client_secret")
	if not (creds.client_id and client_secret):
		r.check("HMRC client credentials configured", False, "client_id/secret missing")
		return _finish(r)

	original_vrn = frappe.db.get_value("Company", cockpit._connection()["company"], "uk_vat_registration_number")

	# 1. throwaway test user
	try:
		user = _create_test_user(creds.client_id, client_secret)
	except Exception as e:
		r.check("Create HMRC test user", False, e)
		return _finish(r)
	vrn = user.get("vrn")
	r.check("Create HMRC test user", bool(vrn), f"vrn={vrn}")

	# 2. connect that VRN
	frappe.db.set_value("Company", cockpit._connection()["company"], "uk_vat_registration_number", vrn)
	frappe.db.commit()
	auth = cockpit.get_authorize_url()
	if not r.check("Build authorize URL", auth.get("ok"), auth.get("message", "")):
		return _finish(r, original_vrn)
	granted, detail = _grant(auth["url"], user["userId"], user["password"])
	if not r.check("OAuth grant + token exchange", granted, detail):
		# Without a token for THIS user every later call is meaningless (stale tokens
		# from a previous user would make "connected" look true — a false pass).
		return _finish(r, original_vrn)
	# The callback saved the tokens in the WEB WORKER process, AFTER this process's
	# transaction began. Under MariaDB's REPEATABLE READ we'd keep reading our old
	# snapshot — i.e. the previous user's access token — and every subsequent call
	# would 403 CLIENT_OR_AGENT_NOT_AUTHORISED. Commit to start a fresh snapshot,
	# and drop the cached document so the new token is actually read.
	frappe.db.commit()
	frappe.clear_document_cache(cockpit.VAT_SETTINGS, settings_name)
	r.check("Access token stored", cockpit._connection()["connected"])

	# 3. obligations -> an open period to file.
	# A freshly created test user's MTD-VAT enrolment takes a little while to become
	# active in the sandbox; until it does HMRC answers 403
	# CLIENT_OR_AGENT_NOT_AUTHORISED. Retry briefly rather than fail a good build.
	obl, opens = {}, []
	for _ in range(8):
		obl = cockpit.get_obligations()
		opens = [o for o in obl.get("obligations", []) if o.get("status") == "O"]
		if obl.get("ok") and opens:
			break
		time.sleep(5)
	if not r.check(
		"Retrieve obligations (open period found)",
		obl.get("ok") and opens,
		obl.get("message", "") or "no open period",
	):
		return _finish(r, original_vrn)
	period = opens[0]

	# 4. submit — the real filing
	sub = cockpit.submit_return(period["periodKey"], period["start"], period["end"], finalised=True)
	if not r.check("Submit VAT return", sub.get("ok"), sub.get("message", "")):
		return _finish(r, original_vrn)
	receipt = sub.get("receipt") or {}
	headers = receipt.get("_headers") or {}
	r.check("Receipt body has formBundleNumber", bool(receipt.get("formBundleNumber")))
	# the non-repudiation receipt arrives as HEADERS, not body
	r.check("Receipt-ID header captured", bool(headers.get("receipt_id")), headers.get("receipt_id"))
	r.check("Receipt-Timestamp header captured", bool(headers.get("receipt_timestamp")), headers.get("receipt_timestamp"))

	# 5. HMRC's own record must match what we sent (cross-system reconciliation)
	view = cockpit.get_return(period["periodKey"])
	sent, held = sub.get("boxes") or {}, (view.get("boxes") or {})
	mismatched = [k for k in sent if float(held.get(k) or 0) != float(sent.get(k) or 0)]
	r.check("View Return matches submitted figures", view.get("ok") and view.get("filed") and not mismatched,
	        f"mismatched={mismatched}" if mismatched else "all 9 boxes match")

	# 6. THE REGRESSION THIS HARNESS EXISTS FOR: the audit record must persist,
	#    with HMRC's ISO8601 timestamps actually stored (not silently dropped).
	names = frappe.get_all(
		RETURN_DOCTYPE,
		filters={"vrn": vrn, "reference_key": period["periodKey"], "docstatus": 1},
		pluck="name",
	)
	if r.check("Audit record persisted (submitted)", bool(names), names[:1]):
		doc = frappe.get_doc(RETURN_DOCTYPE, names[0])
		r.check("Audit record: processing_date stored", bool(doc.processing_date), str(doc.processing_date))
		r.check("Audit record: receipt_id stored", bool(doc.receipt_id), doc.receipt_id)
		r.check("Audit record: receipt_timestamp stored", bool(doc.receipt_timestamp), str(doc.receipt_timestamp))
		r.check("Audit record: box5 matches submission", float(doc.net_vat_due_box5 or 0) == float(sent.get("box5") or 0),
		        f"{doc.net_vat_due_box5} vs {sent.get('box5')}")

	# 7. double-submission guard must refuse a repeat
	again = cockpit.submit_return(period["periodKey"], period["start"], period["end"], finalised=True)
	r.check("Double-submission blocked", (not again.get("ok")) and again.get("already_filed"), again.get("message", ""))

	# 8. remaining read endpoints answer
	for label, fn in (
		("Retrieve liabilities", cockpit.get_liabilities),
		("Retrieve payments", cockpit.get_payments),
		("Retrieve penalties", cockpit.get_penalties),
	):
		res = fn()
		r.check(label, res.get("ok"), res.get("message", ""))

	return _finish(r, original_vrn)


def _finish(r, original_vrn=None):
	if original_vrn:
		# leave the site's VRN as we found it; tokens now belong to the throwaway
		# user, so re-authorise before using the cockpit for demos again.
		frappe.db.set_value("Company", cockpit._connection()["company"], "uk_vat_registration_number", original_vrn)
		frappe.db.commit()
	passed = len(r.rows) - len(r.failed)
	print(f"\n=== HMRC SMOKE: {passed}/{len(r.rows)} passed ===", flush=True)
	for f in r.failed:
		print(f"  FAILED: {f['step']} — {f['detail']}", flush=True)
	return {"ok": not r.failed, "passed": passed, "total": len(r.rows), "results": r.rows}
