frappe.query_reports["VAT Return"] = {
	filters: [
		{
			fieldname: "company",
			label: __("Company"),
			fieldtype: "Link",
			options: "Company",
			reqd: 1
		},
		{
			fieldname: "from_date",
			label: __("From Date"),
			fieldtype: "Date",
			reqd: 1
		},
		{
			fieldname: "to_date",
			label: __("To Date"),
			fieldtype: "Date",
			reqd: 1
		},
		{
			fieldname: "vat_return",
			label: __("VAT Return"),
			fieldtype: "Link",
			options: "UK MTD VAT Return",
			reqd: 1
		}
	],

	onload: function (report) {
		report.page.add_inner_button(__('Refresh VAT Boxes'), function () {
			report.refresh();
		});
	},

	// after_datatable_render: function (report) {
	// 	// Remove existing cards
	// 	$('.vat-box-summary').remove();

	// 	if (!report.data || report.data.length === 0) return;

	// 	// Extract VAT box rows from data
	// 	let vatRows = report.data.filter(row => row.invoice_type && row.invoice_type.startsWith("Box"));
	// 	if (vatRows.length === 0) return;

	// 	// Create grey ERPNext-style section
	// 	let container = $(`
	// 		<div class="vat-box-summary"
	// 			style="background-color:#f4f5f6; 
	// 				   border-radius:8px; 
	// 				   padding:1.5rem;
	// 				   margin:1rem 0;
	// 				   border:1px solid #d1d5db;">
	// 			<h4 style="font-weight:600; font-size:15px; color:#111827; margin-bottom:1rem;">
	// 				VAT Box Summary
	// 			</h4>
	// 			<div style="display:grid; grid-template-columns:repeat(auto-fit,minmax(230px,1fr)); gap:1rem;">
	// 			</div>
	// 		</div>
	// 	`);

	// 	let grid = container.find("div:last");

	// 	vatRows.forEach(row => {
	// 		let card = $(`
	// 			<div style="background:white;
	// 						border:1px solid #e5e7eb;
	// 						border-radius:8px;
	// 						padding:0.8rem 1rem;
	// 						box-shadow:0 1px 2px rgba(0,0,0,0.03);">
	// 				<h5 style="font-size:13px; font-weight:500; color:#111827; margin-bottom:0.4rem;">
	// 					${row.invoice_type}
	// 				</h5>
	// 				<p style="font-size:17px; font-weight:700; color:#000; margin:0;">
	// 					£ ${frappe.format(row.grand_total || 0, {fieldtype: 'Currency'})}
	// 				</p>
	// 			</div>
	// 		`);
	// 		grid.append(card);
	// 	});

	// 	// Insert above the datatable
	// 	report.$report_wrapper.find(".dt-scrollable").before(container);
	// }
};
