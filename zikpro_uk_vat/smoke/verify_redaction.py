"""P3-2 proof: log redaction masks tokens AND the broker/signup secrets at every location.

    bench --site erpnext.zikpro.local execute zikpro_uk_vat.smoke.verify_redaction.run
"""

from zikpro_uk_vat.security import redact_secrets


def run():
	results = []

	def check(label, secret, text):
		masked = redact_secrets(text)
		ok = secret not in masked and "[REDACTED]" in masked
		results.append(ok)
		print(f"[{'PASS' if ok else 'FAIL'}] {label}", flush=True)

	check("Bearer token", "eyJhbGc.tok.enV", "Authorization: Bearer eyJhbGc.tok.enV")
	check("access_token", "AT-999", '{"access_token": "AT-999", "x": 1}')
	check("client_secret", "CS-abc", "client_secret=CS-abc&grant_type=x")
	check("shared_secret", "SS-secret48", '{"shared_secret": "SS-secret48"}')
	check("broker_shared_secret", "BSS-1", "broker_shared_secret: BSS-1")
	check("signup_token", "SIGNUP-xyz", '{"signup_token":"SIGNUP-xyz","site":"a"}')
	check("state_signing_secret", "STATE-1", "state_signing_secret=STATE-1")
	check("hmac sig", "deadbeefsig", '{"sig": "deadbeefsig", "tenant_id": "t1"}')

	passed = sum(1 for r in results if r)
	print(f"REDACTION {passed}/{len(results)}", flush=True)
	return {"passed": passed, "total": len(results)}
