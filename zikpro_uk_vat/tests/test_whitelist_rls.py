"""P3-5: whitelisted-surface security invariants (RLS + v16 type-hint metric).

Static guardrail over cockpit.py: every @frappe.whitelist method that accepts user
input must carry a permission/scope guard (_require / has_permission / _connection),
so a future method can't ship taking user params with no check (the RLS regression
class). Also reports v16 whitelist type-hint coverage (informational — untyped params
still work, but the number should trend up as methods are hardened for v16).
"""

import ast

import frappe

APP = "zikpro_uk_vat"
_GUARDS = ("_require(", "has_permission", "_connection(")


def _is_whitelisted(fn):
	return any("frappe.whitelist" in ast.unparse(d) for d in fn.decorator_list)


def prove_whitelist_rls():
	with open(frappe.get_app_path(APP, "cockpit.py"), encoding="utf-8") as f:
		tree = ast.parse(f.read())

	total = all_params = typed_params = 0
	unguarded = []
	for node in ast.walk(tree):
		if not (isinstance(node, ast.FunctionDef) and _is_whitelisted(node)):
			continue
		total += 1
		uparams = [a for a in node.args.args if a.arg != "self"]
		body = ast.unparse(node)
		if uparams and not any(g in body for g in _GUARDS):
			unguarded.append(node.name)
		for a in uparams:
			all_params += 1
			typed_params += a.annotation is not None

	if unguarded:
		print(f"UNGUARDED whitelisted methods with user params: {unguarded}", flush=True)
	print(f"v16 whitelist type-hint coverage: {typed_params}/{all_params} params typed", flush=True)

	return {
		"scanned_enough_methods": total >= 30,
		"every_param_method_guarded": len(unguarded) == 0,
	}
