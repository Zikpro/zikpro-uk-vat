# UK VAT for ERPNext

File your UK **Making Tax Digital (MTD) VAT** return to HMRC directly from ERPNext —
no spreadsheets, no bridging tools. The nine-box return is built from the Sales and
Purchase invoices already in your books and submitted straight to HMRC.

> Free/base edition. A commercial **Pro** edition adds Cash Accounting & Flat Rate
> schemes, year-end adjustments (Partial Exemption, Capital Goods Scheme), multi-company,
> and accountant/multi-client filing. See https://zikpro.com.

## What it does
- **Standard (accrual) VAT scheme** — the full nine-box return from your invoices
- **Connect to HMRC** securely (Making Tax Digital for VAT API)
- **Obligations** — see what's due and when
- **Submit** the return with HMRC's legal declaration, and keep the receipt
- **Fraud-Prevention Headers** as HMRC requires
- **View** your VAT liabilities and payments as HMRC has them
- A single **VAT cockpit** — prepare, drill from each box down to the exact invoice, file

## Requirements
- Frappe Framework v15+ and ERPNext v15+
- A UK VAT registration and a Government Gateway login enrolled for MTD for VAT

## Install
```bash
bench get-app https://github.com/Zikpro/zikpro-uk-vat
bench --site <your-site> install-app zikpro_uk_vat
```

## HMRC Making Tax Digital
This software connects to HMRC's MTD for VAT API. You remain responsible for the
accuracy of every return you submit. Figures are computed from the invoices in your
ERPNext company; review them before you file.

## Licence
MIT — see [LICENSE](LICENSE).

---
Built by [ZikPro](https://zikpro.com) · a Frappe Certified Partner in the UK · info@zikpro.com
