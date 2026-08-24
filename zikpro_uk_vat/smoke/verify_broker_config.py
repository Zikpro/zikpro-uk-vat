"""P1-7 / BSW-5 proof: broker mode requires a complete broker connection server-side
(mandatory_depends_on is browser-only). Also a B20 parity check on the broker_* fields.

    bench --site erpnext.zikpro.local execute zikpro_uk_vat.smoke.verify_broker_config.run
"""

import frappe


def run():
	results = []

	def check(label, cond):
		results.append(bool(cond))
		print(f"[{'PASS' if cond else 'FAIL'}] {label}", flush=True)

	# 1. broker on + fields missing -> refused (in-memory, non-destructive)
	d = frappe.new_doc("VAT Settings")
	d.use_broker = 1
	try:
		d._validate_broker_config()
		check("broker on + fields missing -> REFUSED", False)
	except frappe.exceptions.ValidationError:
		check("broker on + fields missing -> REFUSED", True)

	# 2. broker on + all fields set -> ok
	d.broker_url = "https://zikops.frappe.cloud"
	d.broker_tenant_id = "zikprotest"
	d.broker_shared_secret = "shared-secret-value"
	try:
		d._validate_broker_config()
		check("broker on + all fields -> allowed", True)
	except Exception as e:
		print("  unexpected:", str(e)[:120], flush=True)
		check("broker on + all fields -> allowed", False)

	# 3. broker OFF -> always ok
	d2 = frappe.new_doc("VAT Settings")
	d2.use_broker = 0
	try:
		d2._validate_broker_config()
		check("broker off -> allowed regardless", True)
	except Exception:
		check("broker off -> allowed regardless", False)

	# 4. B20 parity: every broker_* field actually exists in the schema
	meta = frappe.get_meta("VAT Settings")
	for f in ("use_broker", "broker_url", "broker_tenant_id", "broker_shared_secret"):
		check(f"B20: field '{f}' exists in VAT Settings schema", bool(meta.get_field(f)))

	print(f"\n=== BROKER-CONFIG (P1-7/BSW-5) PROOF: {sum(results)}/{len(results)} passed ===", flush=True)
	return sum(results) == len(results)
