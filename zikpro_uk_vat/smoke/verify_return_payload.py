"""P3-1 proof: the filed VAT Return carries an IMMUTABLE snapshot of the exact 9-box body
sent to HMRC (submitted_payload) — recorded at filing, frozen on submit. HMRC-dispute
defensible (India Compliance pattern).

    bench --site erpnext.zikpro.local execute zikpro_uk_vat.smoke.verify_return_payload.run
"""

import json

import frappe

from zikpro_uk_vat import cockpit as ck

REF = "24A1-PAYLOADTEST"


def _clean():
	for n in frappe.get_all("UK MTD VAT Return", filters={"reference_key": REF}, pluck="name"):
		frappe.db.set_value("UK MTD VAT Return", n, "docstatus", 0, update_modified=False)
		frappe.delete_doc("UK MTD VAT Return", n, force=True, ignore_permissions=True)
	frappe.db.commit()


def run():
	results = []

	def check(label, cond, got=None):
		results.append(bool(cond))
		extra = "" if cond else f"  (got {got})"
		print(f"[{'PASS' if cond else 'FAIL'}] {label}{extra}", flush=True)

	boxes = {"box1": 600.0, "box2": 0.0, "box3": 600.0, "box4": 135.0, "box5": 465.0,
			 "box6": 2500.0, "box7": 900.0, "box8": 0.0, "box9": 0.0}

	# 1. The payload helper maps our boxes to HMRC's field names, single source of truth.
	pl = ck._hmrc_return_payload("24A1", boxes)
	check("payload uses HMRC field names + periodKey + finalised",
		  pl.get("periodKey") == "24A1" and pl.get("vatDueSales") == 600.0
		  and pl.get("vatReclaimedCurrPeriod") == 135.0 and pl.get("finalised") is True)

	# 2. Build a filed return carrying the snapshot; submit; confirm it persists and round-trips.
	_clean()
	ret = frappe.new_doc("UK MTD VAT Return")
	ret.vrn = "193054661"; ret.reference_key = REF
	ret.period_start_date = "2026-01-01"; ret.period_end_date = "2026-03-31"; ret.due_date = "2026-05-07"
	ret.company = "Demo Company"
	ck._apply_boxes(ret, boxes)
	ret.form_bundle_number = "PAYLOAD-BUNDLE-1"  # P0-2 guard needs the HMRC receipt
	ret.submitted_payload = frappe.as_json(ck._hmrc_return_payload(ret.reference_key, boxes))
	ret.insert(ignore_permissions=True)
	ret.flags.ignore_permissions = True
	ret.submit()
	frappe.db.commit()

	fresh = frappe.get_doc("UK MTD VAT Return", ret.name)
	check("submitted return is docstatus 1 (immutable)", fresh.docstatus == 1)
	check("submitted_payload persisted", bool(fresh.submitted_payload))
	try:
		parsed = json.loads(fresh.submitted_payload)
	except Exception:
		parsed = {}
	check("snapshot is valid JSON matching the filed boxes",
		  parsed.get("vatDueSales") == 600.0 and parsed.get("totalValueSalesExVAT") == 2500.0
		  and parsed.get("periodKey") == REF)

	# 3. Immutable: a submitted document cannot be edited (docstatus=1 blocks db_set/save).
	blocked = False
	try:
		fresh.submitted_payload = "{\"tampered\": true}"
		fresh.save(ignore_permissions=True)
	except Exception:
		blocked = True
	check("snapshot is immutable after submit (edit refused)", blocked)

	_clean()
	print(f"\n=== RETURN-PAYLOAD SNAPSHOT (P3-1) PROOF: {sum(results)}/{len(results)} passed ===", flush=True)
	return sum(results) == len(results)
