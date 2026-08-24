"""P3-2: secrets never reach a log — free-text redaction AND structured masking.

Both derive from security.SENSITIVE_KEYS (single source of truth). redact_secrets
guards the free-text Error Log written by the frozen api.py; mask_mapping guards any
dict/list payload masked at defined key locations before serialisation.
"""

from zikpro_uk_vat import security as s


def prove_log_masking():
	res = {}

	# --- free-text redaction ---
	res["bearer_masked"] = "Bearer [REDACTED]" in s.redact_secrets("Bearer abc.def-123~")
	res["bearer_value_gone"] = "abc.def" not in s.redact_secrets("Bearer abc.def-123~")
	res["access_token_masked"] = "tok123" not in s.redact_secrets('{"access_token": "tok123"}')
	res["client_secret_masked"] = "sh!secret" not in s.redact_secrets("client_secret=sh!secret")
	res["shared_secret_masked"] = "hmac999" not in s.redact_secrets("broker_shared_secret=hmac999&x=1")
	res["oauth_code_masked"] = "authcode" not in s.redact_secrets("grant=x&code=authcode&y=2")
	res["key_name_kept"] = "access_token" in s.redact_secrets("access_token=zzz")
	# NO false positive on compound keys ending in a sensitive substring
	fp = s.redact_secrets("error_code=500 status_code=200")
	res["no_false_positive"] = "500" in fp and "200" in fp

	# --- structured masking (headers / output / data) ---
	payload = {
		"Authorization": "Bearer xyz",
		"access_token": "AAA",
		"Gov-Client-Public-IP": "1.2.3.4",
		"Gov-Vendor-Product-Name": "ERPNext",
		"nested": {"refresh_token": "RRR", "note": "ok"},
		"list": [{"sig": "SIG"}, "plain code=deadbeef&z=1"],
		"innocent": "hello",
	}
	m = s.mask_mapping(payload)
	res["struct_authorization"] = m["Authorization"] == "[REDACTED]"
	res["struct_access_token"] = m["access_token"] == "[REDACTED]"
	res["struct_govclient_pii"] = m["Gov-Client-Public-IP"] == "[REDACTED]"
	res["struct_govvendor_kept"] = m["Gov-Vendor-Product-Name"] == "ERPNext"
	res["struct_nested"] = m["nested"]["refresh_token"] == "[REDACTED]" and m["nested"]["note"] == "ok"
	res["struct_list_sig"] = m["list"][0]["sig"] == "[REDACTED]"
	res["struct_list_str_code"] = "deadbeef" not in m["list"][1]
	res["struct_innocent_kept"] = m["innocent"] == "hello"
	res["struct_no_mutate"] = payload["access_token"] == "AAA"

	passed = sum(1 for v in res.values() if v)
	print(f"LOG-MASKING (P3-2) PROOF {passed}/{len(res)}: {res}", flush=True)
	return res
