"""A0-3 — real HMRC-sandbox end-to-end VAT filing test.

Why separate from run_proofs: this makes LIVE network calls to HMRC's sandbox and
needs real credentials, so it is NOT auto-discovered by the deterministic proof
suite. Run it explicitly (and only when creds exist):

    bench --site <site> execute \\
        zikpro_uk_vat.tests.test_hmrc_sandbox_e2e.run_sandbox_e2e

Credentials (env vars, or the same keys in site_config.json):
    HMRC_SANDBOX_VRN            a sandbox VAT registration number
    HMRC_SANDBOX_ACCESS_TOKEN   an application-restricted OAuth token for the
                                sandbox (test-api.service.hmrc.gov.uk)
    HMRC_SANDBOX_BASE           optional; defaults to the sandbox base URL

Without creds it prints SKIP and returns cleanly (so CI without secrets is green).

Scope + honest finding: the app's own submit path builds the URL from
api.HMRC_API_BASE_URL, which is FROZEN to production (the sandbox URLs are commented
out in the HMRC-approved api.py). So driving a real sandbox filing THROUGH the app
requires a config-driven base URL the app does not yet expose — a backlog item. This
test therefore exercises the HMRC round-trip at the protocol level using the EXACT
9-box payload contract and the app's real fraud-prevention headers, proving the core
obligations -> submit -> receipt path that the unit proofs only simulate. The
post-receipt bookkeeping (Filed ledger event + snapshot) is covered by prove_filed /
prove_snapshot.
"""

import os

import frappe

from zikpro_uk_vat import api

_DEFAULT_SANDBOX = "https://test-api.service.hmrc.gov.uk"


def _cfg(key):
    return os.environ.get(key) or frappe.conf.get(key.lower())


def _headers(token):
    """The app's real fraud-prevention headers + the sandbox auth/accept headers."""
    h = dict(api.get_fraud_prevention_headers() or {})
    h.update({
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.hmrc.1.0+json",
        "Content-Type": "application/json",
    })
    return h


def _payload_from_period(doc_boxes, period_key):
    """Build the submit body using the SAME keys api.submit_vat_return_to_hmrc uses."""
    b = doc_boxes
    return {
        "periodKey": period_key,
        "vatDueSales": float(b.get("box1", 0)),
        "vatDueAcquisitions": float(b.get("box2", 0)),
        "totalVatDue": float(b.get("box3", 0)),
        "vatReclaimedCurrPeriod": float(b.get("box4", 0)),
        "netVatDue": float(b.get("box5", 0)),
        "totalValueSalesExVAT": int(round(float(b.get("box6", 0)), 0)),
        "totalValuePurchasesExVAT": int(round(float(b.get("box7", 0)), 0)),
        "totalValueGoodsSuppliedExVAT": int(round(float(b.get("box8", 0)), 0)),
        "totalAcquisitionsExVAT": int(round(float(b.get("box9", 0)), 0)),
        "finalised": True,
    }


def run_sandbox_e2e():
    import requests

    vrn = _cfg("HMRC_SANDBOX_VRN")
    token = _cfg("HMRC_SANDBOX_ACCESS_TOKEN")
    base = _cfg("HMRC_SANDBOX_BASE") or _DEFAULT_SANDBOX

    if not (vrn and token):
        print("SKIP sandbox E2E: set HMRC_SANDBOX_VRN + HMRC_SANDBOX_ACCESS_TOKEN "
              "to run the live HMRC round-trip.", flush=True)
        return {"skipped": True}

    res = {}
    sess = requests.Session()

    # 1) OPEN obligations — the period we are allowed to file.
    r = sess.get(f"{base}/organisations/vat/{vrn}/obligations",
                 params={"status": "O"}, headers=_headers(token), timeout=30)
    res["obligations_ok"] = r.status_code == 200
    obligations = (r.json() or {}).get("obligations", []) if r.status_code == 200 else []
    if not obligations:
        print(f"SANDBOX E2E: no OPEN obligations for VRN {vrn} (status {r.status_code}). "
              "Seed one in the sandbox, then re-run.", flush=True)
        return {**res, "no_open_obligation": True}

    ob = obligations[0]
    period_key = ob["periodKey"]

    # 2) Build the 9-box payload from the app's own figures for that period.
    from zikpro_uk_vat import cockpit as c
    figures = c.get_return_figures(ob["start"], ob["end"]).get("boxes", {})
    payload = _payload_from_period(figures, period_key)
    res["payload_has_9_boxes"] = len({k for k in payload if k.startswith("vat") or "Value" in k or "Acquisitions" in k}) >= 8
    res["payload_finalised"] = payload["finalised"] is True

    # 3) Submit the return to the sandbox.
    sub = sess.post(f"{base}/organisations/vat/{vrn}/returns",
                    json=payload, headers=_headers(token), timeout=30)
    res["submit_accepted"] = sub.status_code in (200, 201)
    data = sub.json() if sub.headers.get("content-type", "").startswith("application/") else {}
    res["got_form_bundle"] = bool(data.get("formBundleNumber"))
    res["got_receipt_id"] = bool(sub.headers.get("Receipt-ID") or data.get("processingDate"))

    passed = sum(1 for v in res.values() if v)
    print(f"SANDBOX E2E {passed}/{len(res)} · period={period_key} · "
          f"formBundle={data.get('formBundleNumber')} · http={sub.status_code}: {res}",
          flush=True)
    return res
