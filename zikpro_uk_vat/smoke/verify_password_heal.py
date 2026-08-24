"""P0-5 / B47 proof: after a site restore/clone the encryption_key rotates and a stored
HMRC token/secret can no longer be decrypted. The cockpit must degrade to 'not connected /
reconnect' — NEVER a 500. Simulate an undecryptable access_token and confirm the read path
tolerates it.

    bench --site erpnext.zikpro.local execute zikpro_uk_vat.smoke.verify_password_heal.run
"""

import frappe

from zikpro_uk_vat import cockpit as ck

COMPANY = "Demo Company"


def _settings():
	name = frappe.db.get_value(ck.VAT_SETTINGS, {"company": COMPANY}, "name")
	if not name:
		d = frappe.get_doc({"doctype": ck.VAT_SETTINGS, "company": COMPANY})
		d.insert(ignore_permissions=True)
		frappe.db.commit()
		name = d.name
	return name


def run():
	results = []

	def check(label, cond):
		results.append(bool(cond))
		print(f"[{'PASS' if cond else 'FAIL'}] {label}", flush=True)

	name = _settings()
	doc = frappe.get_doc(ck.VAT_SETTINGS, name)
	doc.access_token = "AT-heal-test"
	doc.save(ignore_permissions=True)
	frappe.db.commit()

	# Corrupt the stored ciphertext to simulate an encryption_key change.
	frappe.db.sql(
		"update `__Auth` set `password`=%s where doctype=%s and name=%s and fieldname='access_token'",
		("garbage-not-a-fernet-token", ck.VAT_SETTINGS, name),
	)
	frappe.db.commit()
	frappe.clear_cache()
	doc = frappe.get_doc(ck.VAT_SETTINGS, name)

	# 1. It is genuinely undecryptable now (raw get_password throws).
	try:
		doc.get_password("access_token")
		check("stored token is undecryptable (precondition)", False)
	except Exception:
		check("stored token is undecryptable (precondition)", True)

	# 2. _safe_password swallows it -> None (no throw).
	try:
		check("_safe_password returns None, no throw", ck._safe_password(doc, "access_token") is None)
	except Exception:
		check("_safe_password returns None, no throw", False)

	# 3. _auth_headers (Bearer builder) does NOT 500 on the undecryptable token.
	try:
		h = ck._auth_headers(name)
		check("_auth_headers degrades without raising", isinstance(h, dict) and "Authorization" in h)
	except Exception as e:
		print("  auth_headers raised:", str(e)[:120], flush=True)
		check("_auth_headers degrades without raising", False)

	# 4. _connection still reports state without raising (UI gate stays usable).
	try:
		c = ck._connection(COMPANY)
		check("_connection returns state without raising", isinstance(c, dict) and "state" in c)
	except Exception:
		check("_connection returns state without raising", False)

	# cleanup: drop the corrupt secret + the token field so the site is left clean
	frappe.db.sql(
		"delete from `__Auth` where doctype=%s and name=%s and fieldname='access_token'",
		(ck.VAT_SETTINGS, name),
	)
	frappe.db.set_value(ck.VAT_SETTINGS, name, "access_token", None, update_modified=False)
	frappe.db.commit()

	print(f"\n=== PASSWORD-HEAL (P0-5/B47) PROOF: {sum(results)}/{len(results)} passed ===", flush=True)
	return sum(results) == len(results)
