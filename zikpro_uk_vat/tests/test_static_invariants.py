"""Static-invariant proofs — self-guard the Books Research findings the portfolio
scanners cannot see.

The central BKF scanners derive their target list from the my-bench apps directory,
and this repo lives OUTSIDE my-bench (`~/zikpro-uk-vat`), so BKF-1 and BKF-6 are never
re-checked centrally here. Per the standing rule "every QA check becomes a routine test
in the repo", these run in-suite via run_proofs — a regression fails THIS app's CI.

BKF-1 (Release It!, blocked threads): an outbound `requests.*` call with no `timeout=`
parks a worker indefinitely and, on the OAuth callback path, freezes the user's browser
mid-redirect. House style is `timeout=30`. The HMRC payload is unchanged by a client-side
timeout.

BKF-6 (Continuous Delivery, reproducible from declared inputs): `hooks.required_apps` and
`pyproject [tool.bench.frappe-dependencies]` are read by DIFFERENT things — `bench
install-app` enforces hooks, Frappe Cloud resolves a bench from pyproject — so a
disagreement is invisible on a running site and only bites a FRESH deploy. This app hard-
depends on ERPNext (doc_events + custom fields on Sales/Purchase Invoice), so ERPNext must
be declared in BOTH.
"""

import ast
import glob
import os
import tomllib

_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
_APP_DIR = os.path.dirname(_TESTS_DIR)          # .../zikpro_uk_vat
_REPO_ROOT = os.path.dirname(_APP_DIR)          # repo root (holds pyproject.toml)
_HTTP_VERBS = {"post", "get", "put", "patch", "delete", "request", "head", "options"}


def _requests_without_timeout():
    """Every `requests.<verb>(...)` in the shipped app code must pass `timeout=`.
    Excludes tests/ and scans/ (not shipped) — comments are already skipped by AST."""
    offenders = []
    for path in glob.glob(os.path.join(_APP_DIR, "**", "*.py"), recursive=True):
        if f"{os.sep}tests{os.sep}" in path or f"{os.sep}scans{os.sep}" in path:
            continue
        try:
            tree = ast.parse(open(path, encoding="utf-8").read())
        except (SyntaxError, UnicodeDecodeError):
            continue
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr in _HTTP_VERBS
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "requests"
                and "timeout" not in {kw.arg for kw in node.keywords}
            ):
                offenders.append(f"{os.path.relpath(path, _REPO_ROOT)}:{node.lineno}")
    return offenders


def _required_apps():
    """The `required_apps` list literal from hooks.py (owner/repo strings)."""
    tree = ast.parse(open(os.path.join(_APP_DIR, "hooks.py"), encoding="utf-8").read())
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "required_apps":
                    try:
                        return list(ast.literal_eval(node.value))
                    except (ValueError, SyntaxError):
                        return []
    return []


def _frappe_dependencies():
    """The `[tool.bench.frappe-dependencies]` table keys from pyproject.toml."""
    with open(os.path.join(_REPO_ROOT, "pyproject.toml"), "rb") as fh:
        data = tomllib.load(fh)
    return set(data.get("tool", {}).get("bench", {}).get("frappe-dependencies", {}))


def _fph_gate_at_chokepoint():
    """`_file_to_hmrc` is the single function that POSTs a VAT return to HMRC (shared by
    submit_return and approve_and_submit). It MUST call `_fph_gate`, or a filing path reaches
    HMRC with unvalidated fraud-prevention headers under the shared broker application — the
    submit_return bypass fixed 29 Sep. Guards against that reopening."""
    tree = ast.parse(open(os.path.join(_APP_DIR, "cockpit.py"), encoding="utf-8").read())
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_file_to_hmrc":
            called = {n.func.id for n in ast.walk(node)
                      if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
            return "_fph_gate" in called
    return False


def prove_static_invariants():
    """BKF-1 + BKF-6 + the FPH-gate chokepoint, as routine checks. Returns {check: bool}."""
    results = {}

    missing = _requests_without_timeout()
    results["bkf1_no_requests_without_timeout"] = len(missing) == 0
    if missing:
        print("  BKF-1 offenders (requests.* without timeout=):", missing, flush=True)

    required = _required_apps()
    deps = _frappe_dependencies()
    needs_erpnext = any("erpnext" in entry for entry in required)
    declares_erpnext = "erpnext" in deps
    # This app genuinely needs ERPNext; assert it is declared in BOTH read paths, and
    # that the two never disagree (the fresh-deploy trap).
    results["bkf6_erpnext_in_required_apps"] = needs_erpnext
    results["bkf6_erpnext_in_frappe_dependencies"] = declares_erpnext
    results["bkf6_required_apps_and_pyproject_agree"] = needs_erpnext == declares_erpnext

    # FPH must be validated at the chokepoint so no filing path can bypass it.
    results["fph_gate_enforced_in_file_to_hmrc"] = _fph_gate_at_chokepoint()

    for check, ok in results.items():
        print(f"[{'PASS' if ok else 'FAIL'}] {check}", flush=True)
    return results
