"""Single-command proof runner for the base app (mirrors the Pro app's harness).

Auto-discovers every `prove_*` across the tests package, runs each (a dict of
{check: bool}), prints a summary, and exits NON-ZERO on any failure so CI turns red.

    bench --site <site> execute zikpro_uk_vat.tests.run_proofs.run
"""

import importlib
import pkgutil
import traceback

import frappe

_PKG = "zikpro_uk_vat.tests"


def _discover():
	pkg = importlib.import_module(_PKG)
	for mod in pkgutil.iter_modules(pkg.__path__):
		if mod.name in ("run_proofs",) or mod.name.startswith("_"):
			continue
		m = importlib.import_module(f"{_PKG}.{mod.name}")
		for attr in sorted(dir(m)):
			if attr.startswith("prove_") and callable(getattr(m, attr)):
				yield mod.name, attr, getattr(m, attr)


def _run_one(fn):
	try:
		res = fn() or {}
		if not isinstance(res, dict):
			res = {"returned_truthy": bool(res)}
		passed = sum(1 for v in res.values() if v)
		failed = [k for k, v in res.items() if not v]
		return passed, len(res), (f"failed: {failed}" if failed else None)
	except Exception as e:
		traceback.print_exc()
		return 0, 0, f"EXCEPTION: {e}"
	finally:
		frappe.db.rollback()


def run():
	results, total_checks, failed_checks = [], 0, 0
	for mod_name, fn_name, fn in _discover():
		passed, total, err = _run_one(fn)
		results.append((f"{mod_name}.{fn_name}", passed, total, err))
		total_checks += total
		failed_checks += (total - passed) + (1 if err and total == 0 else 0)

	print("\n" + "=" * 60)
	print("  UK VAT (BASE) — PROOF SUITE")
	print("=" * 60)
	suites_failed = 0
	for label, passed, total, err in results:
		mark = "PASS" if not err else "FAIL"
		if err:
			suites_failed += 1
		print(f"  [{mark}] {label:<40} {passed}/{total}" + (f"  {err}" if err else ""))
	print("-" * 60)
	print(f"  {len(results)} proofs · {total_checks - failed_checks}/{total_checks} checks green")
	print("=" * 60 + "\n")

	if suites_failed or failed_checks:
		raise SystemExit(1)
	return {"suites": len(results), "checks": total_checks, "green": True}
