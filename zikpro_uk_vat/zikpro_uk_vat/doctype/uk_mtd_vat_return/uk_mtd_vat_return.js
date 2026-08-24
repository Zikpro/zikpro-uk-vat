// Copyright (c) 2025, Zikpro and contributors
// For license information, please see license.txt

frappe.ui.form.on('UK MTD VAT Return', {
    refresh(frm) {
        if (!frm.is_new()) {
            frm.add_custom_button(__('View VAT Report'), () => {
                frappe.set_route('query-report', 'VAT Return', {
                    vat_return: frm.doc.name,
                    company: frm.doc.company,
                    from_date: frm.doc.period_start_date,
                    to_date: frm.doc.period_end_date
                });
            }, __('Reports'));
        }
    }
});

