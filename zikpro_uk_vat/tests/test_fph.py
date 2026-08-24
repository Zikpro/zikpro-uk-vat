"""FPH-1 regression — the HMRC Gov-Client-Screens / Window-Size fraud-prevention
headers must carry the browser's REAL values, not the hardcoded 1920x1080 fallback.

Root cause (fixed): the frozen api.py get_fraud_prevention_headers() reads client_info
under keys screen_width/screen_height/pixel_ratio/color_depth (Screens) and width/
height (Window-Size); update_client_info used to store the real values under
width/height/scaling, so Screens never found screen_width and always sent 1920x1080 —
an inaccurate FPH header HMRC penalises. Fix is entirely in non-frozen utils.py + the
capture JS; api.py stays byte-frozen. Run:
  bench execute zikpro_uk_vat.tests.test_fph.prove_fph
"""

import frappe

from zikpro_uk_vat import api, utils


def _screens():
	h = api.get_fraud_prevention_headers()
	return h.get("Gov-Client-Screens"), h.get("Gov-Client-Window-Size")


def prove_fph():
	res = {}
	user = frappe.session.user
	frappe.cache().hdel(utils.CLIENT_INFO_CACHE, user)

	# REALISTIC two-request flow: the browser sets client_info in request A; the submit
	# is a SEPARATE request B. frappe.session.data does NOT persist across requests on
	# Frappe Cloud, so request B must re-hydrate from the per-user cache (before_request).
	frappe.session.data.pop("client_info", None)
	utils.update_client_info(2560, 1440, 30, 2, -5, window_width=1900, window_height=1200)  # request A
	frappe.session.data.pop("client_info", None)  # simulate a fresh request B
	utils.hydrate_client_info()  # before_request
	sc, ws = _screens()
	res["persists_across_requests"] = utils.get_client_info().get("screen_width") == 2560
	res["screens_real"] = sc == "width=2560&height=1440&scaling-factor=2&colour-depth=30"
	res["window_real"] = ws == "width=1900&height=1200"
	res["not_the_1920_default"] = "1920" not in (sc or "")

	# fractional pixel ratio preserved
	frappe.session.data.pop("client_info", None)
	utils.update_client_info(1440, 900, 24, 1.5, 0)
	res["fractional_ratio"] = _screens()[0] == "width=1440&height=900&scaling-factor=1.5&colour-depth=24"

	# fallback is DETECTABLE (1921x1081), never indistinguishable from a real 1920x1080
	frappe.cache().hdel(utils.CLIENT_INFO_CACHE, user)
	frappe.session.data.pop("client_info", None)
	utils.set_default_client_info(None, None)
	res["fallback_detectable"] = _screens()[0] == "width=1921&height=1081&scaling-factor=1&colour-depth=25"

	frappe.cache().hdel(utils.CLIENT_INFO_CACHE, user)
	frappe.session.data.pop("client_info", None)
	passed = sum(1 for v in res.values() if v)
	print(f"FPH PROOF {passed}/{len(res)}: {res}", flush=True)
	return res
