"""Proof: _ensure_broker_registration guard branches (no network).

Marketplace one-click self-register must NOT fire when it shouldn't:
  - a site already in broker mode (has tenant) -> left alone, returns True
  - a site running its own direct HMRC creds (client_id set) -> respected, returns False
  - no settings -> False
The actual open-registration network path is validated by the cloud E2E, not here.

    bench --site erpnext.zikpro.local execute zikpro_uk_vat.smoke.verify_self_register_guard.run
"""

import frappe

from zikpro_uk_vat import cockpit as ck

_SN = "__selfreg_guard_settings__"


def _mk_settings(**vals):
	if frappe.db.exists("VAT Settings", _SN):
		frappe.delete_doc("VAT Settings", _SN, force=True, ignore_permissions=True)
	d = frappe.new_doc("VAT Settings")
	d.name = _SN
	d.billing_contact = "ci@example.com"  # mandatory; a later doc.save() must not trip on it
	for k, v in vals.items():
		setattr(d, k, v)
	d.flags.ignore_mandatory = True
	d.flags.ignore_validate = True
	d.insert(ignore_permissions=True, ignore_if_duplicate=True)
	frappe.db.commit()
	return d.name


def run():
	results = []

	def check(label, cond):
		results.append(bool(cond))
		print(f"[{'PASS' if cond else 'FAIL'}] {label}", flush=True)

	# 0. no settings -> False, no call
	check("no settings -> False", ck._ensure_broker_registration(None) is False)

	# 1. already a broker tenant -> True, and never calls the broker
	called = {"n": 0}
	orig = ck._broker_call
	ck._broker_call = lambda *a, **k: called.__setitem__("n", called["n"] + 1) or {}
	try:
		sn = _mk_settings(use_broker=1, broker_tenant_id="existing-tid", broker_url="https://x")
		r = ck._ensure_broker_registration(sn)
		check("already-registered -> True", r is True)
		check("already-registered -> no broker call", called["n"] == 0)

		# 2. A1 GATE: no signup token -> False, and NEVER calls the broker (free ≠ anonymous)
		called["n"] = 0
		sn = _mk_settings()
		r = ck._ensure_broker_registration(sn)
		check("no signup token -> False (A1 gate)", r is False)
		check("no signup token -> no broker call", called["n"] == 0)

		# 3. with a signup token, broker returning already_registered -> False (can't recover secret)
		called["n"] = 0
		ck._broker_call = lambda *a, **k: {"ok": True, "already_registered": True, "tenant_id": "t1"}
		sn = _mk_settings(broker_signup_token="tok-123")
		r = ck._ensure_broker_registration(sn, company="C", vrn="123")
		check("token + already_registered -> False", r is False)

		# 4. with a signup token, broker returns a fresh tenant -> flips to broker mode
		called["n"] = 0
		def _fresh(*a, **k):
			called["n"] += 1
			return {"ok": True, "already_registered": False, "tenant_id": "t9",
					"shared_secret": "sec9", "broker_url": "https://oauth.ziktax.com"}
		ck._broker_call = _fresh
		sn = _mk_settings(broker_signup_token="tok-123")
		r = ck._ensure_broker_registration(sn, company="C", vrn="123")
		check("token + fresh tenant -> True (broker mode)", r is True)
		check("token path -> broker WAS called", called["n"] >= 1)
	finally:
		ck._broker_call = orig
		if frappe.db.exists("VAT Settings", _SN):
			frappe.delete_doc("VAT Settings", _SN, force=True, ignore_permissions=True)
			frappe.db.commit()

	passed = sum(1 for r in results if r)
	print(f"SELF-REGISTER GUARD {passed}/{len(results)}", flush=True)
	return {"passed": passed, "total": len(results)}
