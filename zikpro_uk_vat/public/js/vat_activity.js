// VAT Activity — read-only view of an invoice's VAT Ledger events, shown in the
// collapsible "VAT Activity" section below the tax breakup. The log lives in the
// VAT Ledger Entry doctype (immutable ledger); this only DISPLAYS it. See
// project-uk-mtd-vat-ledger-design.

frappe.ui.form.on("Sales Invoice", { refresh: render_vat_activity });
frappe.ui.form.on("Purchase Invoice", { refresh: render_vat_activity });

function render_vat_activity(frm) {
	const field = frm.get_field("vat_activity_html");
	if (!field) return;
	if (frm.is_new() || frm.doc.docstatus === 0) {
		field.$wrapper.html(
			`<div class="text-muted small">VAT activity appears here once the invoice is submitted.</div>`
		);
		return;
	}
	frappe.db
		.get_list("VAT Ledger Entry", {
			filters: { source_doctype: frm.doctype, source_name: frm.doc.name },
			fields: [
				"posting_date",
				"event_type",
				"vat_box",
				"basis",
				"net_amount",
				"vat_amount",
				"period_key",
				"vat_return",
			],
			order_by: "posting_date asc, creation asc",
			limit: 0,
		})
		.then((rows) => {
			if (!rows || !rows.length) {
				field.$wrapper.html(
					`<div class="text-muted small">No VAT ledger activity recorded for this invoice yet.</div>`
				);
				return;
			}
			const cur = frm.doc.currency || frappe.defaults.get_default("currency");
			const fmt = (v) => format_currency(v, cur);
			const body = rows
				.map(
					(r) => `<tr>
						<td>${frappe.datetime.str_to_user(r.posting_date) || ""}</td>
						<td>${frappe.utils.escape_html(r.event_type || "")}</td>
						<td>${frappe.utils.escape_html(r.vat_box || "")}</td>
						<td>${frappe.utils.escape_html(r.basis || "")}</td>
						<td class="text-right">${fmt(r.net_amount)}</td>
						<td class="text-right">${fmt(r.vat_amount)}</td>
						<td>${frappe.utils.escape_html(r.period_key || (r.vat_return ? r.vat_return : "—"))}</td>
					</tr>`
				)
				.join("");
			field.$wrapper.html(
				`<table class="table table-bordered" style="margin-bottom:0">
					<thead><tr>
						<th>Date</th><th>Event</th><th>Box</th><th>Basis</th>
						<th class="text-right">Net</th><th class="text-right">VAT</th><th>Period</th>
					</tr></thead>
					<tbody>${body}</tbody>
				</table>`
			);
		});
}
