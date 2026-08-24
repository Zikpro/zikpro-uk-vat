frappe.listview_settings['UK MTD VAT Return'] = {
    default_sort: "creation desc", 
    get_indicator: function (doc) {
        if (doc.status === "Fulfilled") {
            return [__("Fulfilled"), "green", "status,=,Fulfilled"];
        } else if (doc.status === "Overdue") {
            return [__("Overdue"), "red", "status,=,Overdue"];
        } else {
            return [__(doc.status), "gray", "status,=," + doc.status];
        }
    },
    onload: function(listview) {
        listview.page.add_button(__("Fetch Obligations"), function() {
            let d = new frappe.ui.Dialog({
                title: __("Fetch VAT Obligations"),
                fields: [
                    {
                        label: "Frequency",
                        fieldname: "frequency",
                        fieldtype: "Select",
                        options: ["Monthly", "Quarterly"],
                        default: "Quarterly",
                        reqd: 1
                    },
                    {
                        label: "From Date",
                        fieldname: "from_date",
                        fieldtype: "Date",
                        default: frappe.datetime.add_months(frappe.datetime.nowdate(), -12),
                        reqd: 1
                    },
                    {
                        label: "To Date",
                        fieldname: "to_date",
                        fieldtype: "Date",
                        default: frappe.datetime.nowdate(),
                        reqd: 1
                    }
                ],
                primary_action_label: __("Fetch"),
                primary_action(values) {
                    d.hide();
                    frappe.call({
                        method: 'zikpro_uk_vat.api.fetch_all_obligations',
                        args: {
                            //docname: "VAT Settings",
                            frequency: values.frequency,
                            from_date: values.from_date,
                            to_date: values.to_date
                        },
                        callback: function(r) {
                            if (r.message) {
                                frappe.show_alert({
                                    message: __("Processed {0} obligations", [r.message.count]),
                                    indicator: 'green'
                                });
                                listview.refresh();
                            }
                        },
                        freeze: true,
                        freeze_message: __("Fetching {0} obligations...", [values.frequency])
                    });
                }
            });
            d.show();
        }).addClass("btn-primary");
    }
};

// Filing happens in the UK VAT cockpit (the single correct path: scheme-aware
// 9-box calc, declaration, double-submission guard, HMRC receipt). The legacy
// desk "Calculate VAT Boxes" / "Submit to HMRC" buttons are retired — they used
// the old buggy box math (credit notes dropped, non-VAT counted, Box 5 negative)
// and bypassed the cockpit's guards. This form now only VIEWS the filed record.
frappe.ui.form.on('UK MTD VAT Return', {
    refresh: function(frm) {
        if (frm.doc.docstatus === 0) {
            frm.add_custom_button(__('Open VAT Cockpit'), function() {
                window.location.href = '/app/vat-cockpit';
            }).addClass('btn-primary');
            frm.dashboard.add_comment(
                __('Prepare and submit VAT returns in the UK VAT cockpit — this form only stores the filed record.'),
                'blue', true
            );
        }
    }
});
