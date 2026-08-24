"""Security hardening that must not touch the FROZEN api.py (M0-A).

Two of the code-review P0s live in api.py, which is frozen byte-for-byte until
the FPH unfreeze is loudly approved. Both are closed here from OUTSIDE the
frozen file, via framework hooks:

1. SEC-12 — the legacy desk OAuth flow (`api.start_oauth_flow` →
   `api.oauth_callback` → `api.save_tokens`) passes tokens in the URL, never
   validates `state`, and `save_tokens` is guest-writable (P0-2 + P0-3). The
   cockpit's `complete_oauth` replaced it long ago, but the endpoints remained
   reachable. `override_whitelisted_methods` in hooks.py now routes all three
   to :func:`disabled_legacy_oauth`, which refuses with a pointer to Connect.

2. SEC-3 — api.py logs `Headers: {headers}` on EVERY HMRC request ("HMRC API
   Debug", api.py:279), which includes the Bearer access token and the
   Gov-Client PII headers. `frappe.log_error` inserts an Error Log document,
   so a `before_insert` doc_event (:func:`redact_error_log`) strips secrets
   from every Error Log written by ANY code path — stronger than editing the
   single line, and freeze-safe.
"""

import re

import frappe

_LEGACY_OAUTH_MSG = (
	"This legacy HMRC authorisation endpoint has been disabled for security "
	"reasons (it exposed tokens in the URL). Use Connect inside the VAT "
	"cockpit (/app/vat-cockpit) instead."
)

# Single source of truth for "what is a secret" (P3-2). Both the free-text regex
# redactor (below, for Error Logs written by the FROZEN api.py) and the structured
# mask_mapping() (for dict/list payloads) derive from this — so a new secret is
# added in ONE place. Ordered longest-first where names nest (broker_shared_secret
# before shared_secret) so the alternation prefers the longer key.
SENSITIVE_KEYS = (
	"access_token", "refresh_token", "client_secret", "authorization",
	"broker_shared_secret", "shared_secret", "signup_token",
	"state_signing_secret", "sig",
)
_KEY_ALT = "|".join(re.escape(k) for k in SENSITIVE_KEYS)

# Order matters. "Bearer <token>" MUST be redacted BEFORE the key/value pass:
# the header value is `Bearer <token>` (a space separates them), and the
# key/value regex stops its capture at that space, so on its own it would strip
# only the word "Bearer" and leave the token. Bearer-first guarantees the token
# is gone whichever form it appears in.
_REDACTIONS = [
	# Bearer <jwt-or-opaque-token> — run first (see note above).
	(re.compile(r"(?i)\bBearer\s+[\w.~+/=-]+"), "Bearer [REDACTED]"),
	# <sensitive_key> : value / = value — keep the key, drop the value. Masks at
	# EVERY log location, not just the HMRC Request Log.
	(re.compile(r"(?i)(['\"]?(?:" + _KEY_ALT + r")['\"]?\s*[:=]\s*['\"]?)[^'\"&\s,}]+"), r"\1[REDACTED]"),
	# oauth token-exchange bodies: code=...&  — \b so it never matches inside
	# innocuous compound keys like error_code=500.
	(re.compile(r"(?i)\b(code=)[^&\s'\"]+"), r"\1[REDACTED]"),
]

# Structured keys: exact (lower-cased) dict keys whose VALUE is a secret. Adds the
# one-time OAuth `code` (safe as an exact key, unlike the substring regex case).
_STRUCT_KEYS = frozenset(k.lower() for k in SENSITIVE_KEYS) | {"code"}


@frappe.whitelist(allow_guest=True)
def disabled_legacy_oauth(*args, **kwargs):
	"""Refuse the legacy OAuth endpoints (P0-2/P0-3) without editing api.py."""
	frappe.throw(frappe._(_LEGACY_OAUTH_MSG), frappe.PermissionError)


def redact_secrets(text):
	"""Strip Bearer tokens / OAuth secrets from arbitrary log text."""
	if not text:
		return text
	for pattern, replacement in _REDACTIONS:
		text = pattern.sub(replacement, text)
	return text


def mask_mapping(obj):
	"""P3-2 mask-at-defined-locations for STRUCTURED payloads (headers / output / data).

	Recursively redacts the VALUE of any dict key that names a secret (case-insensitive)
	or is a Gov-Client-* fraud-prevention header (device/PII), and runs the free-text
	redactor over plain strings. Prefer this over redact_secrets when you hold the dict
	BEFORE serialising it into a log — masking by exact key is more reliable than regex
	over already-serialised text. Returns a new structure; never mutates the input.
	"""
	if isinstance(obj, dict):
		out = {}
		for k, v in obj.items():
			lk = str(k).lower()
			out[k] = "[REDACTED]" if (lk in _STRUCT_KEYS or lk.startswith("gov-client-")) else mask_mapping(v)
		return out
	if isinstance(obj, (list, tuple)):
		return [mask_mapping(v) for v in obj]
	if isinstance(obj, str):
		return redact_secrets(obj)
	return obj


def redact_error_log(doc, method=None):
	"""before_insert hook on Error Log — no secret ever reaches the table."""
	doc.error = redact_secrets(doc.error)
	# title ("method") is occasionally used as the message by callers
	doc.method = redact_secrets(doc.method)


# ---------------------------------------------------------------------------
# FL-02 (P1-4): keep files attached to VAT documents PRIVATE.
#
# Frappe's File defaults to is_private=0, and a public file is served by nginx
# with NO permission check and bypasses the parent document's permissions. The
# app attaches no files today, but once the OAuth broker serves many tenants,
# ANY receipt/return/settings attachment left public would be fetchable by URL
# across tenants. Force is_private=1 for files attached to our doctypes at
# A File's physical path is chosen at write time from is_private, so we cannot safely FLIP
# it after the fact (the bytes would sit in the public folder → "File URL incorrect"). Refuse
# a public file instead — a VAT attachment must be created private from the start.
_PRIVATE_FILE_PARENTS = {
	"VAT Settings",
	"UK MTD VAT Return",
	"VAT Adjustment",
	"VAT Adjustment Schedule",
	"VAT Ledger Entry",
}


def enforce_private_vat_files(doc, method=None):
	if getattr(doc, "attached_to_doctype", None) in _PRIVATE_FILE_PARENTS and not doc.is_private:
		frappe.throw(
			frappe._(
				"Files attached to {0} must be PRIVATE — a public file is served with no "
				"permission check and could be read across tenants. Attach it as private."
			).format(doc.attached_to_doctype),
			title=frappe._("Private File Required"),
			exc=frappe.PermissionError,
		)
