"""L4 / module-standard reachability for the cockpit SPA.

The proof suite is otherwise Python-level: it calls cockpit methods directly, so a
button whose method was renamed, moved, or lost its @frappe.whitelist() decorator
would still pass every proof while the real page is dead on click (the B100/B81
"page no test can open" class). A full browser test needs Playwright in CI; this
gives the same high-signal guarantee without a browser:

  for every `zikpro_uk_vat.cockpit.<x>` the built bundle actually calls,
  the Python function must (a) import and (b) be whitelisted (frappe.call-able).

Uses the same `fn in frappe.whitelisted` signal as tests/test_permissions.py.
"""

import re

import frappe

APP = "zikpro_uk_vat"
PAGE = "vat-cockpit"
# All cockpit entrypoints the SPA invokes are `...cockpit.<name>` (api.py is the
# frozen HMRC boundary and is not called straight from the bundle).
_METHOD_RE = re.compile(r"zikpro_uk_vat\.cockpit\.[a-z_]+")


def _bundle_methods():
	path = frappe.get_app_path(APP, "public", "js", "vat_cockpit.bundle.js")
	with open(path, encoding="utf-8") as f:
		return sorted(set(_METHOD_RE.findall(f.read())))


def prove_cockpit_reachable():
	"""Every method the cockpit bundle calls resolves and is whitelisted."""
	res = {"page_exists": bool(frappe.db.exists("Page", PAGE))}

	try:
		methods = _bundle_methods()
	except Exception as e:  # bundle missing/unreadable is itself a failure
		res["bundle_readable"] = False
		res["_error"] = False
		frappe.log_error(f"cockpit bundle unreadable: {e}", "prove_cockpit_reachable")
		return res

	res["bundle_readable"] = True
	# Sanity: the regex actually matched the SPA's call surface (guards a silent
	# rename of the module path making this proof vacuously green).
	res["bundle_call_surface_found"] = len(methods) >= 25

	for m in methods:
		short = m.rsplit(".", 1)[-1]
		try:
			fn = frappe.get_attr(m)
		except Exception:
			fn = None
		res[f"resolves:{short}"] = callable(fn)
		res[f"whitelisted:{short}"] = bool(fn) and fn in frappe.whitelisted

	return res
