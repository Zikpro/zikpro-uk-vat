"""A2 proof: the FPH pre-submit gate verdict logic (valid / invalid / unavailable).

Unit-tests _fph_gate by stubbing the app-token fetch and HMRC validator response, and
confirms approve_and_submit wires the gate (blocks on 'invalid').

    bench --site erpnext.zikpro.local execute zikpro_uk_vat.smoke.verify_fph_gate.run
"""

import types
import frappe

from zikpro_uk_vat import cockpit as ck


class _Resp:
	def __init__(self, status, payload):
		self.status_code = status
		self._p = payload

	def json(self):
		return self._p


def run():
	results = []

	def check(label, cond):
		results.append(bool(cond))
		print(f"[{'PASS' if cond else 'FAIL'}] {label}", flush=True)

	orig_token = ck._fph_app_token
	orig_get = ck.requests.get
	# neutralise the real FPH build so the header dict is deterministic
	orig_api = __import__("zikpro_uk_vat.api", fromlist=["get_fraud_prevention_headers"])
	orig_build = orig_api.get_fraud_prevention_headers
	orig_api.get_fraud_prevention_headers = lambda: {"Gov-Client-Connection-Method": "WEB_APP_VIA_SERVER"}
	try:
		# 1. no app token -> unavailable (fail-open)
		ck._fph_app_token = lambda sn: None
		v, _ = ck._fph_gate("x")
		check("no app token -> unavailable", v == "unavailable")

		ck._fph_app_token = lambda sn: "app-token"

		# 2. validator says VALID_HEADERS -> valid
		ck.requests.get = lambda *a, **k: _Resp(200, {"code": "VALID_HEADERS", "errors": []})
		v, _ = ck._fph_gate("x")
		check("VALID_HEADERS -> valid", v == "valid")

		# 3. validator reports errors -> invalid (BLOCK)
		ck.requests.get = lambda *a, **k: _Resp(200, {"code": "INVALID_HEADERS",
													  "errors": [{"code": "GOV_CLIENT_PUBLIC_IP_MISSING"}]})
		v, d = ck._fph_gate("x")
		check("errors -> invalid", v == "invalid")

		# 4. validator unreachable -> unavailable (fail-open)
		def _boom(*a, **k):
			raise ck.requests.RequestException("down")
		ck.requests.get = _boom
		v, _ = ck._fph_gate("x")
		check("validator unreachable -> unavailable", v == "unavailable")

		# 5. approve_and_submit references the gate (integration wiring)
		import inspect
		src = inspect.getsource(ck.approve_and_submit)
		check("approve_and_submit calls _fph_gate", "_fph_gate(" in src and "fph_blocked" in src)
	finally:
		ck._fph_app_token = orig_token
		ck.requests.get = orig_get
		orig_api.get_fraud_prevention_headers = orig_build

	passed = sum(1 for r in results if r)
	print(f"FPH GATE {passed}/{len(results)}", flush=True)
	return {"passed": passed, "total": len(results)}
