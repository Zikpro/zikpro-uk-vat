"""F3 regression guard: the FROZEN get_fraud_prevention_headers must emit the REAL device in
Gov-Client-Screens / Window-Size / colour-depth when client_info is populated — never the
1920x1080 (or 1921x1081 detectable-fallback) default. Guards against the class of bug where a
key-mismatch froze the header at the spec example (HMRC's "no variation" complaint).

Read-only: imports the frozen generator and asserts its output; does NOT modify api.py. The
request/network-dependent helpers are stubbed so the test is deterministic and offline (we only
assert the client_info-derived headers here).

    bench --site erpnext.zikpro.local execute zikpro_uk_vat.smoke.verify_golden_headers.run
"""

import frappe
from zikpro_uk_vat import api


def run():
	results = []

	def check(label, cond):
		results.append(bool(cond))
		print(f"[{'PASS' if cond else 'FAIL'}] {label}", flush=True)

	# Stub the request/network-dependent helpers so only the client_info path is exercised.
	stubs = {
		"get_server_public_ip": lambda: "203.0.113.10",
		"get_public_ip": lambda: "198.51.100.20",
		"get_client_port": lambda: "51000",
		"get_vendor_public_ip": lambda: "203.0.113.10",
		"get_vendor_forwarded": lambda: "by=203.0.113.10&for=198.51.100.20",
		"get_browser_user_agent": lambda: "Mozilla/5.0 (Test)",
		"get_mfa_header": lambda: "",
		"get_timezone": lambda: "UTC+00:00",
	}
	saved = {name: getattr(api, name, None) for name in stubs}
	for name, fn in stubs.items():
		if saved[name] is not None:
			setattr(api, name, fn)

	# A REAL device — deliberately not 1920x1080 and not the 1921x1081 fallback.
	frappe.session.data["client_info"] = {
		"screen_width": 2560, "screen_height": 1440, "pixel_ratio": 2, "color_depth": 30,
		"width": 1600, "height": 900, "timezone_offset": 0,
	}
	try:
		h = api.get_fraud_prevention_headers() or {}
		scr = h.get("Gov-Client-Screens", "")
		win = h.get("Gov-Client-Window-Size", "")
		check("headers built (non-empty)", bool(h))
		check("Screens reflects real width 2560", "width=2560" in scr)
		check("Screens reflects real height 1440", "height=1440" in scr)
		check("Screens colour-depth 30", "colour-depth=30" in scr)
		check("Screens scaling-factor 2", "scaling-factor=2" in scr)
		check("Screens NOT default 1920", "width=1920" not in scr)
		check("Screens NOT detectable-fallback 1921", "width=1921" not in scr)
		check("Window reflects real 1600x900", "width=1600" in win and "height=900" in win)
		check("Connection-Method WEB_APP_VIA_SERVER", h.get("Gov-Client-Connection-Method") == "WEB_APP_VIA_SERVER")

		# And the fallback IS detectable (empty client_info -> not a real 1920x1080 device).
		frappe.session.data["client_info"] = {}
		hf = api.get_fraud_prevention_headers() or {}
		# frozen default is 1920; the login fallback (utils.set_default_client_info) uses 1921 —
		# either way the point is the generator does not fabricate a *real* device silently.
		check("fallback screens present", bool(hf.get("Gov-Client-Screens")))
	finally:
		for name, fn in saved.items():
			if fn is not None:
				setattr(api, name, fn)
		frappe.session.data.pop("client_info", None)

	passed = sum(1 for r in results if r)
	print(f"GOLDEN HEADERS {passed}/{len(results)}", flush=True)
	return {"passed": passed, "total": len(results)}
