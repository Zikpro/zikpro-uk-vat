"""Idempotent CI fixtures — makes a freshly-installed site fixture-ready for the
proof suite. On a dev site that already has these, every step is a no-op.

    bench --site <site> execute zikpro_uk_vat.tests.ci_bootstrap.ensure_fixtures

The proofs need: a default Company, the 2017-2018 UK VAT Fiscal Year, at least one
enabled Customer and Supplier, and income/expense + VAT accounts (the app's own
install creates the MTD VAT account; the Company's chart of accounts supplies the
rest).
"""

import frappe

COMPANY = "Zikpro Test UK"
ABBR = "ZTU"


def _ensure_company():
    existing = frappe.db.get_single_value("Global Defaults", "default_company")
    if existing:
        return existing
    name = frappe.db.get_value("Company", {"name": COMPANY})
    if not name:
        frappe.get_doc({
            "doctype": "Company",
            "company_name": COMPANY,
            "abbr": ABBR,
            "default_currency": "GBP",
            "country": "United Kingdom",
            "create_chart_of_accounts_based_on": "Standard Template",
        }).insert(ignore_permissions=True)
        name = COMPANY
    frappe.db.set_single_value("Global Defaults", "default_company", name)
    # New invoices default to the SYSTEM currency (INR out of the box), which clashes with
    # the GBP company accounts ("Debtors currency GBP vs document currency INR"). Pin GBP.
    frappe.db.set_single_value("Global Defaults", "default_currency", "GBP")
    frappe.db.set_default("currency", "GBP")
    frappe.db.commit()
    frappe.clear_cache()
    return name


def _ensure_fiscal_year():
    # The ledger proofs use the 2017-18 VAT year; the e2e/schedule proofs post on TODAY, so
    # the current UK VAT year must exist too (a fresh site only ships the year of install).
    from frappe.utils import getdate, nowdate

    y = getdate(nowdate()).year
    cur_start = y if getdate(nowdate()).month >= 4 else y - 1  # UK fiscal year starts 1 Apr
    years = [("2017-2018", "2017-04-01", "2018-03-31"),
             (f"{cur_start}-{cur_start + 1}", f"{cur_start}-04-01", f"{cur_start + 1}-03-31")]
    for name, start, end in years:
        if not frappe.db.exists("Fiscal Year", name):
            frappe.get_doc({"doctype": "Fiscal Year", "year": name,
                            "year_start_date": start, "year_end_date": end}).insert(ignore_permissions=True)
    frappe.db.commit()


def _ensure_party(doctype, name, extra):
    if frappe.db.get_value(doctype, {"disabled": 0}):
        return
    doc = {"doctype": doctype, "disabled": 0}
    doc.update(extra)
    frappe.get_doc(doc).insert(ignore_permissions=True)
    frappe.db.commit()


def _ensure_base_fixtures():
    """ERPNext base records that a HEADLESS install (no setup wizard) can lack, but which
    Company creation + the proofs need. Company insert fails with 'Could not find Warehouse
    Type: Transit' otherwise, taking the whole suite down before a single proof runs."""
    if not frappe.db.exists("Warehouse Type", "Transit"):
        frappe.get_doc({"doctype": "Warehouse Type", "name": "Transit"}).insert(ignore_permissions=True)
    if not frappe.db.exists("UOM", "Nos"):
        frappe.get_doc({"doctype": "UOM", "uom_name": "Nos"}).insert(ignore_permissions=True)
    frappe.db.commit()


def ensure_fixtures():
    _ensure_base_fixtures()
    company = _ensure_company()
    _ensure_fiscal_year()
    _ensure_party("Customer", "Zikpro Test Customer", {
        "customer_name": "Zikpro Test Customer", "customer_type": "Company",
        "default_currency": "GBP",  # else invoices default to INR and clash with the GBP company accounts
        "customer_group": frappe.db.get_value("Customer Group", {"is_group": 0}, "name"),
        "territory": frappe.db.get_value("Territory", {"is_group": 0}, "name"),
    })
    _ensure_party("Supplier", "Zikpro Test Supplier", {
        "supplier_name": "Zikpro Test Supplier", "supplier_type": "Company",
        "default_currency": "GBP",
        "supplier_group": frappe.db.get_value("Supplier Group", {"is_group": 0}, "name"),
    })
    _ensure_price_lists()
    _ensure_vat_config()
    print(f"CI fixtures ready · company={company} · fiscal 2017-2018 · customer+supplier+VAT seeded",
          flush=True)
    return {"company": company}


def _ensure_price_lists():
    """A fresh site's invoices need a selling/buying Price List in the company currency
    (else 'selling_price_list ... mandatory' / a currency clash). Pin GBP + set as defaults."""
    for pl, buying, settings, field in (
        ("Standard Selling", 0, "Selling Settings", "selling_price_list"),
        ("Standard Buying", 1, "Buying Settings", "buying_price_list"),
    ):
        if frappe.db.exists("Price List", pl):
            frappe.db.set_value("Price List", pl, "currency", "GBP")
        else:
            frappe.get_doc({"doctype": "Price List", "price_list_name": pl, "currency": "GBP",
                            "selling": 0 if buying else 1, "buying": buying}).insert(ignore_permissions=True)
        try:
            frappe.db.set_single_value(settings, field, pl)
        except Exception:
            pass
    frappe.db.commit()


def _ensure_vat_config():
    """Map the default VAT accounts + treatment templates. The ledger/onboarding proofs
    assume these exist (e.g. vat_accounts(...)[0]), but a fresh site has none and only
    test_permissions configures accounts — which may run AFTER the ledger proofs in the
    discovery order. Configure here so every proof has them regardless of order. Idempotent."""
    from zikpro_uk_vat import cockpit

    company = frappe.db.get_single_value("Global Defaults", "default_company")
    # setup_default_vat_accounts maps accounts ONTO a VAT Settings doc — a fresh site has
    # none (hence "VAT Settings None not found"), so create it for the company first.
    if company and not frappe.db.get_value("VAT Settings", {"company": company}):
        frappe.get_doc({
            "doctype": "VAT Settings", "company": company,
            "billing_contact": "ci@example.com",
        }).insert(ignore_permissions=True, ignore_mandatory=True)
        frappe.db.commit()
    try:
        cockpit.setup_default_vat_accounts()
        cockpit.setup_default_vat_templates()
        frappe.db.commit()
    except Exception as e:  # never let optional setup abort the whole bootstrap
        print(f"[ci_bootstrap] VAT default setup skipped: {e}", flush=True)
