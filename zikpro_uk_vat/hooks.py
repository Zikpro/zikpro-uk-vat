app_name = "zikpro_uk_vat"
app_title = "UK VAT for ERPNext"
app_publisher = "Zikpro"
app_description = "File UK Making Tax Digital VAT returns to HMRC from ERPNext (free/base edition)."
app_email = "info@zikpro.com"
app_license = "mit"
app_version = "0.0.1"

# Base edition needs ERPNext (the return is built from Sales/Purchase invoices).
required_apps = ["frappe/erpnext"]

# ---------------------------------------------------------------------------
# Freemium boundary (see private split plan):
#   This PUBLIC base app ships the Standard (accrual) scheme + HMRC connect/file.
#   The premium calculations (Cash, Flat Rate, adjustments, DRC, ledger, …) live
#   ONLY in the private `zikpro_uk_vat_pro` add-on and register themselves into the
#   scheme/feature registry on install. With Pro absent, those paths render a clean
#   upsell — never premium code in this public tree.
# ---------------------------------------------------------------------------

# doc_events / scheduler_events / app_include_js and the base doctypes are added
# during the extraction from the current full app (staged, CI-gated).
