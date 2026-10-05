# UK VAT for ERPNext

File your UK **Making Tax Digital (MTD) VAT** return to HMRC directly from ERPNext —
no spreadsheets, no bridging tools. The nine-box return is built from the Sales and
Purchase invoices already in your books and submitted straight to HMRC.

> Free/base edition — a complete filer including all three VAT schemes. A commercial
> **Pro** edition adds the automatic year-end and sector adjustment engines: Partial
> Exemption (Notice 706), Capital Goods Scheme (Notice 706/2), and Domestic Reverse
> Charge for construction/CIS (Notice 735).

## What it does
- **All three VAT schemes** — Standard (accrual), Cash Accounting (Notice 731) and Flat
  Rate (Notice 733) — the full nine-box return from your invoices
- **Connect to HMRC** securely (Making Tax Digital for VAT API)
- **Obligations** — see what's due and when
- **Submit** the return with HMRC's legal declaration, and keep the receipt
- **Fraud-Prevention Headers** as HMRC requires
- **View** your VAT liabilities and payments as HMRC has them
- A single **VAT cockpit** — prepare, drill from each box down to the exact invoice, file

## Requirements
- Frappe Framework v15+ and ERPNext v15+
- A UK VAT registration and a Government Gateway login enrolled for MTD for VAT

## Filing to HMRC
Filing goes through ZikPro's OAuth broker. For a hosted, always-current build, install
from the Frappe Cloud Marketplace — that is the copy ZikPro supports for live filing.
**Sandbox is self-serve and free:** open the VAT cockpit, choose Connect, then Get
sandbox access to provision a test VAT number and try a full filing (nothing is sent to
HMRC). **Production** files real returns and needs a vetted token. A production site must
be a public HTTPS host so the fraud-prevention headers carry a real IP.

## HMRC Making Tax Digital
This software connects to HMRC's MTD for VAT API. You remain responsible for the
accuracy of every return you submit. Figures are computed from the invoices in your
ERPNext company; review them before you file.

## Licence
MIT. See the LICENSE file in this app.

---
Built by ZikPro, a Frappe Certified Partner in the UK.
