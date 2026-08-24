"""P1-4 / FL-02 proof: a File attached to a VAT document is forced private, even if
created with is_private=0; files attached to unrelated doctypes are untouched.

    bench --site erpnext.zikpro.local execute zikpro_uk_vat.smoke.verify_file_privacy.run
"""

import frappe

TAG = "p14-file-privacy-test"


def _clean():
	for n in frappe.get_all("File", filters={"file_name": ["like", f"{TAG}%"]}, pluck="name"):
		frappe.delete_doc("File", n, force=True, ignore_permissions=True)
	frappe.db.commit()


def _mk(attached_to_doctype, attached_to_name, is_private):
	from frappe.utils.file_manager import save_file
	f = save_file(
		f"{TAG}-{attached_to_doctype or 'none'}.txt", "x",
		attached_to_doctype, attached_to_name, is_private=is_private,
	)
	frappe.db.commit()
	return f


def run():
	results = []

	def check(label, cond):
		results.append(bool(cond))
		print(f"[{'PASS' if cond else 'FAIL'}] {label}", flush=True)

	_clean()
	settings = frappe.db.get_value("VAT Settings", {}, "name")

	# 1. A PUBLIC file attached to a VAT doctype is REFUSED.
	blocked = False
	try:
		_mk("VAT Settings", settings, is_private=0)
	except frappe.PermissionError:
		blocked = True
	except Exception as e:
		blocked = "must be PRIVATE" in str(e) or "private" in str(e).lower()
	check("public file on VAT Settings is REFUSED", blocked)

	# 2. A PRIVATE file attached to a VAT doctype is allowed.
	f2 = _mk("VAT Settings", settings, is_private=1)
	check("private file on VAT Settings is allowed", int(f2.is_private) == 1)

	# 3. A public file on an UNRELATED doctype is untouched (no over-reach).
	f3 = _mk("ToDo", None, is_private=0)
	check("public file on unrelated doctype allowed (is_private=0)", int(f3.is_private) == 0)

	_clean()
	print(f"\n=== FILE-PRIVACY (P1-4/FL-02) PROOF: {sum(results)}/{len(results)} passed ===", flush=True)
	return sum(results) == len(results)
