// Copyright (c) 2025, Zikpro and contributors
// For license information, please see license.txt

// SEC-12 (M0-A): the old "Authorize with HMRC" button here launched
// api.start_oauth_flow — the legacy insecure OAuth path (tokens returned in the
// URL, unvalidated state, guest-callable token injection). The cockpit's
// Connect tab is the one sanctioned flow (server-held state, one-time use,
// tokens never in the browser). This form now only points there.
frappe.ui.form.on("VAT Settings", {
	refresh: function (frm) {
		frm.add_custom_button(__("Open VAT Cockpit"), function () {
			window.location.href = "/app/vat-cockpit";
		}).addClass("btn-primary");
		frm.dashboard.add_comment(
			__("Connect to HMRC and manage VAT from the UK VAT cockpit — the desk OAuth flow is retired."),
			"blue",
			true
		);
	},
});
