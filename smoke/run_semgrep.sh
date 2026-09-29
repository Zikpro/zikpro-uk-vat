#!/usr/bin/env bash
# Marketplace semgrep gate — runs the SAME ruleset Frappe Cloud's app review uses
# (github.com/frappe/semgrep-rules) and FAILS on any ERROR-severity finding, so a
# blocking issue is caught here instead of at marketplace submission. WARNING-level
# findings (type hints, manual commits in tests) are advisory and do not fail.
#
# Usage:
#   smoke/run_semgrep.sh [APP_PACKAGE_DIR]      # default: this app's package
#   FRAPPE_SEMGREP_RULES=/path/to/rules smoke/run_semgrep.sh   # reuse a local checkout
#
# Covers all three UK MTD VAT apps — point it at each package dir:
#   base:      .../zikpro-uk-vat/zikpro_uk_vat
#   pro:       .../zikpro-uk-vat-pro/zikpro_uk_vat_pro
#   connector: .../hmrc_broker_repo/hmrc_broker   (the HMRC OAuth broker)
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR="${1:-$(cd "$HERE/.." && pwd)/zikpro_uk_vat}"
CACHE="${FRAPPE_SEMGREP_RULES:-$HOME/.cache/frappe-semgrep-rules}"

if ! command -v semgrep >/dev/null 2>&1; then
	echo "FAIL: semgrep not installed (pip install semgrep). The gate fails rather than skip." >&2
	exit 2
fi
if [ ! -d "$CACHE/rules" ]; then
	echo "Fetching frappe/semgrep-rules -> $CACHE"
	rm -rf "$CACHE"
	git clone --depth 1 https://github.com/frappe/semgrep-rules.git "$CACHE" >/dev/null 2>&1 || {
		echo "FAIL: could not fetch frappe/semgrep-rules and none cached at $CACHE." >&2
		exit 2
	}
fi

OUT="$(mktemp)"
trap 'rm -f "$OUT"' EXIT
semgrep scan --config "$CACHE/rules" --severity ERROR --metrics=off --timeout 90 --json "$APP_DIR" >"$OUT" 2>/dev/null || true

# Allowlist: (rule, file) pairs that are known-benign and cannot be changed here.
#   frappe-single-value-type-safety @ api.py — get_value("System Settings","System Settings",..)
#     is correct for a Single doctype (its docname IS the doctype name); api.py is the
#     HMRC-approved, byte-FROZEN FPH file and must not be edited. If marketplace ever blocks
#     on it, the behaviour-identical fix is get_single_value(), which needs an api.py unfreeze.
python3 - "$OUT" <<'PY'
import json, sys
ALLOW = {("frappe-single-value-type-safety", "api.py")}
res = json.load(open(sys.argv[1])).get("results", [])
blocking = []
for r in res:
	rule = r["check_id"].split(".")[-1]
	base = r["path"].split("/")[-1]
	if (rule, base) in ALLOW:
		continue
	blocking.append((rule, r["path"], r["start"]["line"]))
if blocking:
	print(f"SEMGREP GATE: {len(blocking)} blocking ERROR finding(s):")
	for rule, path, line in blocking:
		print(f"  [{rule}] {path}:{line}")
	sys.exit(1)
print(f"SEMGREP GATE PASS: 0 blocking ERROR findings ({len(res)} allowlisted).")
PY
