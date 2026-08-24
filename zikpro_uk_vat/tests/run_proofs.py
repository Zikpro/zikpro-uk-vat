"""Single-command proof runner — the standardized CI harness for this app.

Why this exists: the stock `FrappeTestCase` runner collides with the site's demo
2017-2018 Fiscal Year fixtures, so the app's coverage is written as executable
`prove_*` functions (each returns a dict of {check: bool}) invoked with
`bench execute`. This runner auto-discovers every `prove_*` across the tests
package, runs them, prints a summary, and exits NON-ZERO if any check fails — so
GitHub Actions turns red on a regression.

    bench --site <site> execute zikpro_uk_vat.tests.run_proofs.run

CI calls `run()`. It raises SystemExit(1) on any failure.
"""

import importlib
import pkgutil
import traceback

import frappe

# prove_* functions must run in a fixture-ready site (company, fiscal year
# 2017-2018, an enabled Customer + Supplier, VAT accounts). CI seeds these via
# ci_bootstrap.ensure_fixtures() before calling run().
_PKG = "zikpro_uk_vat.tests"


_TAG = "LEDGERTEST"


def _purge_residue():
    """Clear leftover tagged test records in FK order so a prior partial run can't
    make a proof's own teardown fail (schedules reference adjustments; both must go
    before the invoices/ledger). No-op on a fresh CI site."""
    order = [
        ("VAT Adjustment Schedule", {"reason": ["like", f"%{_TAG}%"]}),
        ("VAT Adjustment", {"reason": ["like", f"%{_TAG}%"]}),
        ("Sales Invoice", {"remarks": ["like", f"%{_TAG}%"]}),
        ("Purchase Invoice", {"remarks": ["like", f"%{_TAG}%"]}),
        ("VAT Ledger Entry", {"source_name": ["like", f"%{_TAG}%"]}),
    ]
    for dt, flt in order:
        for name in frappe.get_all(dt, filters=flt, pluck="name"):
            try:
                d = frappe.get_doc(dt, name)
                if getattr(d, "docstatus", 0) == 1:
                    d.cancel()
                d.flags.ignore_immutable = True
                frappe.delete_doc(dt, name, force=True, ignore_permissions=True)
            except Exception:
                pass
    # Test/smoke VAT Return records (sandbox period keys) collide with the SEC-11 /
    # UX-2B preparer-flow tests via the double-submit guard — force-remove them.
    for name in frappe.get_all("UK MTD VAT Return",
                               filters={"reference_key": ["in", ["18A1", "18A2", "18B1", "18B2"]]},
                               pluck="name"):
        try:
            frappe.db.set_value("UK MTD VAT Return", name, "docstatus", 2)
            frappe.delete_doc("UK MTD VAT Return", name, force=True, ignore_permissions=True)
        except Exception:
            pass
    frappe.db.commit()


def _discover():
    """Yield (module_name, fn_name, callable) for every prove_* in the package."""
    pkg = importlib.import_module(_PKG)
    for mod in pkgutil.iter_modules(pkg.__path__):
        if mod.name in ("run_proofs", "ci_bootstrap") or mod.name.startswith("_"):
            continue
        m = importlib.import_module(f"{_PKG}.{mod.name}")
        for attr in sorted(dir(m)):
            if attr.startswith("prove_") and callable(getattr(m, attr)):
                yield mod.name, attr, getattr(m, attr)


def _run_one(fn):
    """Run one proof; return (passed, total, error_or_None). A crash is a failure."""
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
    # NB: do NOT set frappe.flags.in_test — it makes global-search enqueue assert
    # hard instead of logging, which is unrelated to what these proofs verify.
    _purge_residue()
    results = []          # (suite, passed, total, error)
    total_checks = failed_checks = 0

    for mod_name, fn_name, fn in _discover():
        passed, total, err = _run_one(fn)
        results.append((f"{mod_name}.{fn_name}", passed, total, err))
        total_checks += total
        failed_checks += (total - passed) + (1 if err and total == 0 else 0)

    print("\n" + "=" * 66)
    print("  UK MTD VAT — PROOF SUITE")
    print("=" * 66)
    suites_failed = 0
    for label, passed, total, err in results:
        mark = "PASS" if not err else "FAIL"
        if err:
            suites_failed += 1
        print(f"  [{mark}] {label:<44} {passed}/{total}"
              + (f"  {err}" if err else ""))
    print("-" * 66)
    print(f"  {len(results)} proofs · {total_checks - failed_checks}/{total_checks} checks green"
          f" · {suites_failed} suite(s) failed")
    print("=" * 66 + "\n")

    if suites_failed or failed_checks:
        raise SystemExit(1)
    return {"suites": len(results), "checks": total_checks, "green": True}
