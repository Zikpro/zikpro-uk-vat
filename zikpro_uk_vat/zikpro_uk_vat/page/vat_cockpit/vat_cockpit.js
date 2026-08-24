frappe.pages["vat-cockpit"].on_page_load = function (wrapper) {
	const page = frappe.ui.make_app_page({
		parent: wrapper,
		title: "UK VAT",
		single_column: true,
	});

	const mount = document.createElement("div");
	mount.id = "vat-cockpit-app";
	page.main.append(mount);

	// vat_cockpit.bundle.js is built by esbuild (resolves Vue) and exposes window.mount_vat_cockpit
	frappe.require("vat_cockpit.bundle.js", () => {
		window.mount_vat_cockpit(mount);
	});
};

// All-in-one, single-window VAT experience: suppress the desk chrome (left sidebar +
// breadcrumb head) *only* while on the cockpit route. The class is removed the moment
// we route away (e.g. a report row's click-through to an invoice opens in the normal desk).
frappe.pages["vat-cockpit"].on_page_show = function () {
	document.body.classList.add("vat-cockpit-fullscreen");
};

frappe.router.on("change", () => {
	const route = frappe.get_route();
	if (!(route && route[0] === "vat-cockpit")) {
		document.body.classList.remove("vat-cockpit-fullscreen");
	}
});
