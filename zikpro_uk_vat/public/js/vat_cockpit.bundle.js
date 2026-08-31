// Full build (esm-bundler) includes the runtime template compiler, so the
// `template:` string below compiles at runtime. Plain "vue" resolves to the
// runtime-only build (no compiler) and silently renders nothing.
import { createApp } from "vue/dist/vue.esm-bundler.js";

// Exposed to the (non-bundled) page JS, which calls this after frappe.require loads the bundle.
window.mount_vat_cockpit = function (el) {
	createApp({
		data() {
			return {
				active: "dashboard",
				loading: true,
				dash: {},
				conn: {},
				connLoading: true,
				connBusy: false,
				connMsg: "",
				connResult: "",
				obligations: [],
				oblLoading: false,
				oblMsg: "",
				authError: false,
				penalties: null,
				penLoading: false,
				penMsg: "",
				liabilities: [],
				liabLoading: false,
				liabMsg: "",
				payments: [],
				payLoading: false,
				payMsg: "",
				txns: [],
				txnLoading: false,
				txnMsg: "",
				schedules: [],
				schedBusy: false,
				schedMsg: "",
				schedDue: 0,
				showSchedForm: false,
				schedFormMsg: "",
				schedForm: { schedule_type: "Bad Debt Relief", trigger_date: "", vat_box: "Box 4", amount: null, reason: "", notice_ref: "" },
				schedTypes: ["Bad Debt Relief", "Capital Goods Scheme", "Partial Exemption Annual"],
				calcForm: { total_vat: null, intervals: null, baseline_pct: null, interval_pct: null, residual_vat: null, annual_pct: null, provisional_pct: null },
				calcNote: "",
				txnFrom: "",
				txnTo: "",
				txnLoaded: false,
				prepObls: [],
				prepLoading: false,
				prepMsg: "",
				prepLoaded: false,
				selectedObl: null,
				figures: null,
				figLoading: false,
				figMsg: "",
				drillBox: null,
				showCalcNotes: false,
				calcNotes: null,
				calcBusy: false,
				showAdjForm: false,
				adjBusy: false,
				adjMsg: "",
				adjForm: { adjustment_type: "Bad Debt Relief", vat_box: "Box 4", amount: null, reason: "", notice_ref: "" },
				adjTypes: ["Error Correction (Notice 700/45)", "Partial Exemption", "Bad Debt Relief", "Capital Goods Scheme", "Fuel Scale Charge", "Other"],
				adjBoxes: ["Box 1", "Box 2", "Box 4", "Box 6", "Box 7", "Box 8", "Box 9"],
				proInstalled: false,
				declared: false,
				submitting: false,
				submitResult: null,
				submitMsg: "",
				userRoles: {},
				pendingApprovals: [],
				pendBusy: false,
				prepBusy: false,
				sentForApproval: false,
				prepActionMsg: "",
				approveBusyFor: null,
				approvedReceipt: null,
				histObls: [],
				histLoading: false,
				histMsg: "",
				histLoaded: false,
				histReturns: {},
				histViewing: null,
				histBusy: null,
				histFiled: {},
				boxDefs: [
					// Wording per VAT Notice 700/12. Boxes 2/8/9 are NORTHERN IRELAND only
					// since 1 Jan 2021 (NI Protocol) — they are not general "EU" boxes.
					{ k: "box1", n: 1, label: "VAT due on sales and other outputs", drill: "sales" },
					{ k: "box2", n: 2, label: "VAT due on acquisitions of goods made in Northern Ireland from EU Member States", ni: true },
					{ k: "box3", n: 3, label: "Total VAT due (Box 1 + Box 2)" },
					{ k: "box4", n: 4, label: "VAT reclaimed on purchases and other inputs", drill: "purchases" },
					{ k: "box5", n: 5, label: "Net VAT to pay to HMRC or reclaim" },
					{ k: "box6", n: 6, label: "Total value of sales and all other outputs excluding VAT", drill: "sales" },
					{ k: "box7", n: 7, label: "Total value of purchases and all other inputs excluding VAT", drill: "purchases" },
					{ k: "box8", n: 8, label: "Total value of dispatches of goods from Northern Ireland to EU Member States, excluding VAT", ni: true },
					{ k: "box9", n: 9, label: "Total value of acquisitions of goods made in Northern Ireland from EU Member States, excluding VAT", ni: true },
				],
				tabs: [
					{ key: "dashboard", label: "Dashboard", hint: "Where you stand with HMRC" },
					{ key: "prepare", label: "Prepare Return", hint: "Calculate → review → declare → submit" },
					{ key: "reports", label: "Reports", hint: "VAT reports, drill through to the invoice" },
					{ key: "history", label: "History", hint: "Submitted returns & receipts" },
					{ key: "adjustments", label: "Adjustments", hint: "Partial exemption & capital goods" },
					// Connect + Settings are a family (configuration) — keep them adjacent at the end.
					{ key: "connect", label: "Connect", hint: "HMRC authorisation & status" },
					{ key: "settings", label: "Settings", hint: "Company, HMRC credentials & VAT scheme" },
				],
				setInfo: {},
				adjForm: { pe_from: "", pe_to: "", cgs_total: null, cgs_intervals: 5, cgs_baseline: null, cgs_interval_use: null },
				peResult: null, cgsResult: null, adjBusy: false, cgsBusy: false,
				fph: null,
				fphBusy: false,
				setForm: { billing_contact: "", vat_accounting_scheme: "", client_id: "", redirect_url: "", client_secret: "", broker_signup_token: "" },
				setLoaded: false,
				setLoading: false,
				setBusy: false,
				vatSetupBusy: false,
				acctForm: {},
				acctBusy: false,
				treatForm: [],
				showAdvanced: false,
				treatBusy: false,
				setMsg: "",
				setErr: "",
			};
		},
		computed: {
			current() {
				return this.tabs.find((t) => t.key === this.active) || this.tabs[0];
			},
		},
		watch: {
			active(tab) {
				// Lazy-load each screen's data the first time its tab is opened.
				if (tab === "reports" && !this.txnLoaded) this.loadReports();
				if (tab === "prepare" && !this.prepLoaded) this.loadPrepObligations();
				if (tab === "prepare" && this.userRoles.is_approver) this.loadPendingApprovals();
				if (tab === "history" && !this.histLoaded) this.loadHistory();
				if (tab === "settings" && !this.setLoaded) this.loadSettings();
			},
		},
		mounted() {
			// Returning from the HMRC OAuth round-trip: /oauth-callback bounces here with ?connect=...
			const params = new URLSearchParams(window.location.search);
			const connect = params.get("connect");
			if (connect) {
				this.active = "connect";
				this.connResult = connect;
			}
			this.loadDashboard();
			this.loadConnection();
			this.loadUserRoles();
			this.loadSchedules();
		},
		methods: {
			loadUserRoles() {
				frappe.call({
					method: "zikpro_uk_vat.cockpit.user_vat_roles",
					callback: (r) => { this.userRoles = r.message || {}; },
				});
			},
			loadPendingApprovals() {
				if (!this.userRoles.is_approver) return;
				this.pendBusy = true;
				frappe.call({
					method: "zikpro_uk_vat.cockpit.list_pending_approvals",
					callback: (r) => {
						this.pendBusy = false;
						this.pendingApprovals = (r.message && r.message.rows) || [];
					},
					error: () => { this.pendBusy = false; },
				});
			},
			sendForApproval() {
				if (!this.selectedObl) return;
				this.prepBusy = true;
				this.prepActionMsg = "";
				frappe.call({
					method: "zikpro_uk_vat.cockpit.prepare_return",
					args: { period_key: this.selectedObl.periodKey, from_date: this.selectedObl.start, to_date: this.selectedObl.end },
					callback: (r) => {
						this.prepBusy = false;
						const res = r.message || {};
						if (res.ok) { this.sentForApproval = true; this.prepActionMsg = ""; }
						else { this.prepActionMsg = res.message || "Could not send for approval."; }
					},
					error: () => { this.prepBusy = false; this.prepActionMsg = "Could not send for approval."; },
				});
			},
			approveReturn(row) {
				// HMRC's mandated legal declaration, shown verbatim as a
				// confirmation before the irreversible submission (matches the
				// wording HMRC requires and the previously-recognised app).
				// finalised:true is only sent once the filer explicitly proceeds.
				const proceed = () => {
					this.approveBusyFor = row.name;
					this.prepActionMsg = "";
					frappe.call({
						method: "zikpro_uk_vat.cockpit.approve_and_submit",
						args: { return_name: row.name, finalised: true },
						callback: (r) => {
							this.approveBusyFor = null;
							const res = r.message || {};
							if (res.ok) { this.approvedReceipt = res.receipt || {}; this.loadPendingApprovals(); }
							else { this.prepActionMsg = res.message || "Could not approve and file."; }
						},
						error: (e) => {
							this.approveBusyFor = null;
							this.prepActionMsg = "Could not approve and file (you may have prepared this return — a different user must approve it).";
						},
					});
				};
				frappe.confirm(
					__('<strong>UK HMRC Legal Declaration</strong><br><br>When you submit this VAT information, you are making a legal declaration that the information is true and complete. A false declaration can result in prosecution.<br><br>Would you like to proceed?'),
					proceed
				);
			},
			loadDashboard() {
				this.loading = true;
				frappe.call({
					method: "zikpro_uk_vat.cockpit.get_dashboard_data",
					callback: (r) => {
						this.dash = r.message || {};
						this.proInstalled = !!(r.message && r.message.pro_installed);
						this.loading = false;
						// get_period_status returns periods + liabilities + payments in one call;
						// penalties is a separate HMRC resource (2nd call, still under 3 req/s).
						if (this.dash.connected) {
							this.loadObligations();
							this.loadPenalties();
						}
					},
					error: () => {
						this.loading = false;
					},
				});
			},
			loadObligations() {
				this.oblLoading = true;
				this.oblMsg = "";
				// Period status = filing AND payment (two separate duties, two deadlines).
				this.liabLoading = true;
				this.payLoading = true;
				frappe.call({
					method: "zikpro_uk_vat.cockpit.get_period_status",
					callback: (r) => {
						this.oblLoading = false;
						this.liabLoading = false;
						this.payLoading = false;
						const res = r.message || {};
						if (res.ok) {
							this.obligations = res.periods || [];
							this.liabilities = res.liabilities || [];
							this.payments = res.payments || [];
							this.liabMsg = res.liabilities_message || "";
							this.payMsg = res.payments_message || "";
						} else {
							this.oblMsg = res.message || "Could not load obligations.";
							// HMRC has just told us this authorisation does not work. That is
							// better evidence than the presence of a stored token, so stop
							// claiming "Connected" on the card above.
							this.authError = !!res.auth_error;
						}
					},
					error: () => {
						this.oblLoading = false;
						this.liabLoading = false;
						this.payLoading = false;
						this.oblMsg = "Could not load obligations.";
					},
				});
			},
			fmtDate(d) {
				if (!d) return "—";
				const dt = new Date(d + "T00:00:00");
				return Number.isNaN(dt.getTime())
					? d
					: dt.toLocaleDateString("en-GB", { day: "2-digit", month: "short", year: "numeric" });
			},
			fmtMoney(v) {
				if (v === null || v === undefined || v === "") return "—";
				const n = Number(v);
				return Number.isNaN(n)
					? v
					: n.toLocaleString("en-GB", { style: "currency", currency: "GBP" });
			},
			loadPenalties() {
				this.penLoading = true;
				this.penMsg = "";
				frappe.call({
					method: "zikpro_uk_vat.cockpit.get_penalties",
					callback: (r) => {
						this.penLoading = false;
						const res = r.message || {};
						if (res.ok) this.penalties = res;
						else this.penMsg = res.message || "Could not load penalties.";
					},
					error: () => {
						this.penLoading = false;
						this.penMsg = "Could not load penalties.";
					},
				});
			},
			loadLiabilities() {
				this.liabLoading = true;
				this.liabMsg = "";
				frappe.call({
					method: "zikpro_uk_vat.cockpit.get_liabilities",
					callback: (r) => {
						this.liabLoading = false;
						const res = r.message || {};
						if (res.ok) this.liabilities = res.items || [];
						else this.liabMsg = res.message || "Could not load liabilities.";
					},
					error: () => {
						this.liabLoading = false;
						this.liabMsg = "Could not load liabilities.";
					},
				});
			},
			loadPayments() {
				this.payLoading = true;
				this.payMsg = "";
				frappe.call({
					method: "zikpro_uk_vat.cockpit.get_payments",
					callback: (r) => {
						this.payLoading = false;
						const res = r.message || {};
						if (res.ok) this.payments = res.items || [];
						else this.payMsg = res.message || "Could not load payments.";
					},
					error: () => {
						this.payLoading = false;
						this.payMsg = "Could not load payments.";
					},
				});
			},
			loadReports() {
				this.txnLoading = true;
				this.txnMsg = "";
				frappe.call({
					method: "zikpro_uk_vat.cockpit.get_vat_transactions",
					args: { from_date: this.txnFrom || undefined, to_date: this.txnTo || undefined },
					callback: (r) => {
						this.txnLoading = false;
						this.txnLoaded = true;
						const res = r.message || {};
						if (res.ok) {
							this.txns = res.rows || [];
							this.txnFrom = res.from;
							this.txnTo = res.to;
						} else {
							this.txnMsg = res.message || "Could not load transactions.";
						}
					},
					error: () => {
						this.txnLoading = false;
						this.txnLoaded = true;
						this.txnMsg = "Could not load transactions.";
					},
				});
			},
			rowRoute(t) {
				// Drill-through to the exact invoice — the only exit from the cockpit.
				return `/app/${t.doctype.toLowerCase().replaceAll(" ", "-")}/${encodeURIComponent(t.name)}`;
			},
			linkifyWarning(w) {
				// P2-7: make invoice/adjustment references in a warning clickable (open the desk form).
				const esc = String(w).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
				return esc.replace(/\b(Sales Invoice|Purchase Invoice|VAT Adjustment)\s+([A-Za-z0-9][A-Za-z0-9\-\/]*)/g,
					(m, dt, name) => `${dt} <a href="/app/${dt.toLowerCase().replaceAll(" ", "-")}/${encodeURIComponent(name)}" target="_blank" rel="noopener">${name}</a>`);
			},
			loadSchedules() {
				this.schedBusy = true;
				frappe.call({
					method: "zikpro_uk_vat.cockpit.list_schedules",
					callback: (r) => {
						this.schedBusy = false;
						const res = r.message || {};
						if (res.ok) { this.schedules = res.rows || []; this.schedDue = res.due_now || 0; }
					},
					error: () => { this.schedBusy = false; },
				});
			},
			runSchedules() {
				this.schedBusy = true; this.schedMsg = "";
				frappe.call({
					method: "zikpro_uk_vat.cockpit.run_schedule_generation",
					callback: (r) => {
						this.schedBusy = false;
						this.schedMsg = (r.message || {}).message || "";
						this.loadSchedules();
					},
					error: () => { this.schedBusy = false; },
				});
			},
			cancelSchedule(name) {
				this.schedBusy = true;
				frappe.call({
					method: "zikpro_uk_vat.cockpit.cancel_schedule",
					args: { name },
					callback: () => { this.schedBusy = false; this.loadSchedules(); },
					error: () => { this.schedBusy = false; },
				});
			},
			addSchedule() {
				const f = this.schedForm;
				if (!f.trigger_date) { this.schedFormMsg = "Pick a trigger date."; return; }
				if (!f.amount || Number(f.amount) === 0) { this.schedFormMsg = "Enter a non-zero amount."; return; }
				if (!f.reason || !f.reason.trim()) { this.schedFormMsg = "A reason is required."; return; }
				this.schedBusy = true; this.schedFormMsg = "";
				frappe.call({
					method: "zikpro_uk_vat.cockpit.create_schedule",
					args: {
						schedule_type: f.schedule_type, trigger_date: f.trigger_date,
						vat_box: f.vat_box, amount: f.amount, reason: f.reason, notice_ref: f.notice_ref,
					},
					callback: (r) => {
						this.schedBusy = false;
						const res = r.message || {};
						if (res.ok) {
							this.showSchedForm = false;
							this.schedForm = { schedule_type: "Bad Debt Relief", trigger_date: "", vat_box: "Box 4", amount: null, reason: "", notice_ref: "" };
							this.loadSchedules();
						} else { this.schedFormMsg = res.message || "Could not create the schedule."; }
					},
					error: () => { this.schedBusy = false; this.schedFormMsg = "Could not create the schedule."; },
				});
			},
			computeSchedAmount() {
				this.schedBusy = true; this.schedFormMsg = "";
				frappe.call({
					method: "zikpro_uk_vat.cockpit.compute_schedule_amount",
					args: Object.assign({ schedule_type: this.schedForm.schedule_type }, this.calcForm),
					callback: (r) => {
						this.schedBusy = false;
						const res = r.message || {};
						if (res.ok) {
							this.schedForm.amount = res.amount;
							if (res.vat_box) this.schedForm.vat_box = res.vat_box;
							this.calcNote = res.note;
						} else { this.schedFormMsg = res.message || "Could not compute the amount."; }
					},
					error: () => { this.schedBusy = false; },
				});
			},
			loadPrepObligations() {
				this.prepLoading = true;
				this.prepMsg = "";
				frappe.call({
					method: "zikpro_uk_vat.cockpit.get_period_status",
					callback: (r) => {
						this.prepLoading = false;
						this.prepLoaded = true;
						const res = r.message || {};
						if (res.ok) {
							// Unfiled periods only, OLDEST FIRST — late filers can have several
							// open at once and the overdue one must be dealt with first.
							this.prepObls = (res.periods || [])
								.filter((p) => !p.filed)
								.map((p) => ({ ...p, status: "O" }));
							// Default to the oldest outstanding period.
							if (this.prepObls.length && !this.selectedObl) this.selectObligation(this.prepObls[0]);
						} else {
							this.prepMsg = res.message || "Could not load open periods.";
						}
					},
					error: () => {
						this.prepLoading = false;
						this.prepLoaded = true;
						this.prepMsg = "Could not load open periods.";
					},
				});
			},
			dueLabel(p) {
				if (p.days_to_due === null || p.days_to_due === undefined) return "";
				if (p.days_to_due < 0) return `${Math.abs(p.days_to_due)} days overdue`;
				if (p.days_to_due === 0) return "due today";
				return `${p.days_to_due} days left`;
			},
			selectObligation(o) {
				this.selectedObl = o;
				this.figures = null;
				this.drillBox = null;
				this.declared = false;
				this.submitResult = null;
				this.submitMsg = "";
				this.calculate();
			},
			calculate() {
				if (!this.selectedObl) return;
				this.figLoading = true;
				this.figMsg = "";
				frappe.call({
					method: "zikpro_uk_vat.cockpit.get_return_figures",
					args: { from_date: this.selectedObl.start, to_date: this.selectedObl.end },
					callback: (r) => {
						this.figLoading = false;
						const res = r.message || {};
						if (res.ok) this.figures = res;
						else this.figMsg = res.message || "Could not calculate figures.";
					},
					error: () => {
						this.figLoading = false;
						this.figMsg = "Could not calculate figures.";
					},
				});
			},
			addAdjustment() {
				if (!this.selectedObl) return;
				const f = this.adjForm;
				if (!f.amount || Number(f.amount) === 0) { this.adjMsg = "Enter a non-zero amount."; return; }
				if (!f.reason || !f.reason.trim()) { this.adjMsg = "A reason is required (audit trail)."; return; }
				this.adjBusy = true; this.adjMsg = "";
				frappe.call({
					method: "zikpro_uk_vat.cockpit.create_adjustment",
					args: {
						posting_date: this.selectedObl.end, adjustment_type: f.adjustment_type,
						vat_box: f.vat_box, amount: f.amount, reason: f.reason, notice_ref: f.notice_ref,
					},
					callback: (r) => {
						this.adjBusy = false;
						const res = r.message || {};
						if (res.ok) {
							this.showAdjForm = false;
							this.adjForm = { adjustment_type: "Bad Debt Relief", vat_box: "Box 4", amount: null, reason: "", notice_ref: "" };
							this.calculate();
						} else { this.adjMsg = res.message || "Could not save the adjustment."; }
					},
					error: () => { this.adjBusy = false; this.adjMsg = "Could not save the adjustment."; },
				});
			},
			cancelAdj(name) {
				this.adjBusy = true;
				frappe.call({
					method: "zikpro_uk_vat.cockpit.cancel_adjustment",
					args: { name },
					callback: () => { this.adjBusy = false; this.calculate(); },
					error: () => { this.adjBusy = false; },
				});
			},
			boxValue(k) {
				return this.figures ? this.figures.boxes[k] : 0;
			},
			loadCalcNotes() {
				if (this.showCalcNotes) { this.showCalcNotes = false; return; }
				if (!this.selectedObl) return;
				this.calcBusy = true;
				frappe.call({
					method: "zikpro_uk_vat.cockpit.calculation_notes",
					args: { from_date: this.selectedObl.start, to_date: this.selectedObl.end },
					callback: (r) => {
						this.calcBusy = false;
						const res = r.message || {};
						if (res.ok) { this.calcNotes = res; this.showCalcNotes = true; }
					},
					error: () => { this.calcBusy = false; },
				});
			},
			isVatBox(k) {
				return ["box1", "box2", "box3", "box4", "box5"].includes(k);
			},
			toggleDrill(def) {
				if (!def.drill) return;
				this.drillBox = this.drillBox === def.k ? null : def.k;
			},
			drillRows(def) {
				if (!this.figures || !def.drill) return [];
				return this.figures[def.drill] || [];
			},
			validateFph() {
				this.fphBusy = true;
				this.fph = null;
				frappe.call({
					method: "zikpro_uk_vat.cockpit.validate_fraud_headers",
					callback: (r) => {
						this.fphBusy = false;
						this.fph = r.message || { ok: false, message: "No response." };
					},
					error: () => {
						this.fphBusy = false;
						this.fph = { ok: false, message: "Could not run the check." };
					},
				});
			},
			loadSettings() {
				this.setLoading = true;
				this.setMsg = "";
				this.setErr = "";
				frappe.call({
					method: "zikpro_uk_vat.cockpit.get_settings",
					callback: (r) => {
						this.setLoading = false;
						this.setLoaded = true;
						const s = r.message || {};
						this.setInfo = s;
						this.setForm = {
							billing_contact: s.billing_contact || "",
							vat_accounting_scheme: s.vat_accounting_scheme || "Standard (Accrual)",
							flat_rate_percentage: s.flat_rate_percentage || 0,
							client_id: s.client_id || "",
							redirect_url: s.redirect_url || "",
							client_secret: "",
							broker_signup_token: "",
						};
						// Editable account mapping: one dropdown per VAT type, pre-filled.
						const byType = {};
						(s.vat_accounts || []).forEach((a) => { byType[a.vat_type] = a.account; });
						this.acctForm = {};
						(s.account_types || []).forEach((t) => { this.acctForm[t] = byType[t] || ""; });
						// Editable treatments: a row per Item Tax Template, pre-filled from
						// the saved mapping (unclassified templates start blank).
						const byTmpl = {};
						(s.vat_treatments || []).forEach((t) => { byTmpl[t.item_tax_template] = t; });
						this.treatForm = (s.template_options || []).map((tmpl) => {
							const cur = byTmpl[tmpl];
							return {
								item_tax_template: tmpl,
								vat_treatment: cur ? cur.vat_treatment : "",
								in_box_6_7: cur ? !!cur.in_box_6_7 : true,
							};
						});
					},
					error: () => {
						this.setLoading = false;
						this.setLoaded = true;
						this.setErr = "Could not load settings.";
					},
				});
			},
			treatmentMismatch(row) {
				// Warn when a template NAME implies a treatment that differs from the one selected.
				const n = String(row.item_tax_template || "").toLowerCase();
				const map = [["outside scope","Outside scope"],["exempt","Exempt"],["zero","Zero rated"],["reduced","Reduced rated"],["standard","Standard rated"]];
				for (const [kw, treat] of map) { if (n.includes(kw)) return (row.vat_treatment && row.vat_treatment !== treat) ? treat : null; }
				return null;
			},
			saveVatTreatments() {
				this.treatBusy = true;
				this.setMsg = "";
				this.setErr = "";
				frappe.call({
					method: "zikpro_uk_vat.cockpit.save_vat_treatments",
					args: { treatments: this.treatForm },
					callback: (r) => {
						this.treatBusy = false;
						const res = r.message || {};
						if (res.ok) {
							this.setMsg = res.message || "VAT treatments saved.";
							this.loadSettings();
						} else {
							this.setErr = res.message || "Could not save VAT treatments.";
						}
					},
					error: () => {
						this.treatBusy = false;
						this.setErr = "Could not save VAT treatments.";
					},
				});
			},
			saveVatAccounts() {
				this.acctBusy = true;
				this.setMsg = "";
				this.setErr = "";
				const accounts = Object.keys(this.acctForm).map((vat_type) => ({
					vat_type,
					account: this.acctForm[vat_type],
				}));
				frappe.call({
					method: "zikpro_uk_vat.cockpit.save_vat_accounts",
					args: { accounts },
					callback: (r) => {
						this.acctBusy = false;
						const res = r.message || {};
						if (res.ok) {
							this.setMsg = res.message || "VAT account mapping saved.";
							this.loadSettings();
						} else {
							this.setErr = res.message || "Could not save VAT accounts.";
						}
					},
					error: () => {
						this.acctBusy = false;
						this.setErr = "Could not save VAT accounts.";
					},
				});
			},
			autoConfigureVat() {
				this.vatSetupBusy = true;
				this.setMsg = "";
				this.setErr = "";
				frappe.call({
					method: "zikpro_uk_vat.cockpit.setup_vat_defaults",
					callback: (r) => {
						this.vatSetupBusy = false;
						const res = r.message || {};
						if (res.ok) {
							this.setMsg = res.message || "VAT accounts and treatments configured.";
							this.loadSettings(); // reload so the mappings + status refresh
						} else {
							this.setErr = res.message || "Could not configure VAT defaults.";
						}
					},
					error: () => {
						this.vatSetupBusy = false;
						this.setErr = "Could not configure VAT defaults.";
					},
				});
			},
			calcPeAnnual() {
				this.adjBusy = true; this.peResult = null;
				frappe.call({ method: "zikpro_uk_vat.cockpit.pe_annual_adjustment",
					args: { from_date: this.adjForm.pe_from, to_date: this.adjForm.pe_to },
					callback: (r) => { this.peResult = r.message || null; },
					always: () => { this.adjBusy = false; } });
			},
			previewCgs() {
				this.cgsBusy = true; this.cgsResult = null;
				frappe.call({ method: "zikpro_uk_vat.cockpit.cgs_preview",
					args: { total_input_vat: this.adjForm.cgs_total, intervals: this.adjForm.cgs_intervals,
						baseline_use_pct: this.adjForm.cgs_baseline, interval_use_pct: this.adjForm.cgs_interval_use },
					callback: (r) => { this.cgsResult = r.message || null; },
					always: () => { this.cgsBusy = false; } });
			},
			saveSettings() {
				this.setBusy = true;
				this.setMsg = "";
				this.setErr = "";
				frappe.call({
					method: "zikpro_uk_vat.cockpit.save_settings",
					args: {
						billing_contact: this.setForm.billing_contact,
						vat_accounting_scheme: this.setForm.vat_accounting_scheme,
						flat_rate_percentage: this.setForm.flat_rate_percentage,
						client_id: this.setForm.client_id,
						redirect_url: this.setForm.redirect_url,
						client_secret: this.setForm.client_secret || undefined,
						broker_signup_token: this.setForm.broker_signup_token || undefined,
					},
					callback: (r) => {
						this.setBusy = false;
						const res = r.message || {};
						if (res.ok) {
							this.setMsg = res.message || "Saved.";
							if (res.settings) {
								this.setInfo = res.settings;
								this.setForm.vat_accounting_scheme = res.settings.vat_accounting_scheme;
								this.setForm.flat_rate_percentage = res.settings.flat_rate_percentage || 0;
								this.setForm.client_secret = "";
							}
							// keep connection state fresh elsewhere
							this.loadConnection();
						} else {
							this.setErr = res.message || "Could not save settings.";
						}
					},
					error: () => {
						this.setBusy = false;
						this.setErr = "Could not save settings.";
					},
				});
			},
			loadHistory() {
				this.histLoading = true;
				this.histMsg = "";
				// Our local audit records (with the HMRC receipt) — pair them with the
				// obligations so History shows both "HMRC's view" and "what we filed".
				frappe.call({
					method: "zikpro_uk_vat.cockpit.get_filed_returns",
					callback: (r) => {
						this.histFiled = r.message?.by_key || {};
					},
				});
				frappe.call({
					method: "zikpro_uk_vat.cockpit.get_obligations",
					callback: (r) => {
						this.histLoading = false;
						this.histLoaded = true;
						const res = r.message || {};
						if (res.ok) this.histObls = res.obligations || [];
						else this.histMsg = res.message || "Could not load history.";
					},
					error: () => {
						this.histLoading = false;
						this.histLoaded = true;
						this.histMsg = "Could not load history.";
					},
				});
			},
			viewReturn(o) {
				const key = o.periodKey;
				if (this.histViewing === key) {
					this.histViewing = null;
					return;
				}
				this.histViewing = key;
				if (this.histReturns[key]) return; // cached
				this.histBusy = key;
				frappe.call({
					method: "zikpro_uk_vat.cockpit.get_return",
					args: { period_key: key },
					callback: (r) => {
						this.histBusy = null;
						this.histReturns[key] = r.message || {};
					},
					error: () => {
						this.histBusy = null;
						this.histReturns[key] = { ok: false, message: "Could not load the return." };
					},
				});
			},
			submitReturn() {
				if (!this.selectedObl || !this.declared) return;
				// HMRC's mandated legal declaration, shown verbatim before the
				// irreversible submission. finalised:true is only sent once the
				// filer explicitly proceeds past this declaration.
				frappe.confirm(
					__('<strong>UK HMRC Legal Declaration</strong><br><br>When you submit this VAT information, you are making a legal declaration that the information is true and complete. A false declaration can result in prosecution.<br><br>Would you like to proceed?'),
					() => this._doSubmitReturn()
				);
			},
			_doSubmitReturn() {
				if (!this.selectedObl || !this.declared) return;
				this.submitting = true;
				this.submitMsg = "";
				this.submitResult = null;
				frappe.call({
					method: "zikpro_uk_vat.cockpit.submit_return",
					args: {
						period_key: this.selectedObl.periodKey,
						from_date: this.selectedObl.start,
						to_date: this.selectedObl.end,
						finalised: true,
					},
					callback: (r) => {
						this.submitting = false;
						const res = r.message || {};
						if (res.ok) {
							this.submitResult = res.receipt || {};
							this.loadDashboard();
							this.loadPrepObligations();
						} else {
							this.submitMsg = res.message || "Submission failed.";
						}
					},
					error: () => {
						this.submitting = false;
						this.submitMsg = "Submission failed. Please try again.";
					},
				});
			},
			loadConnection() {
				this.connLoading = true;
				this.connMsg = "";
				frappe.call({
					method: "zikpro_uk_vat.cockpit.get_connection_status",
					callback: (r) => {
						this.conn = r.message || {};
						this.connLoading = false;
					},
					error: () => {
						this.connLoading = false;
					},
				});
			},
			startConnect() {
				this.connBusy = true;
				this.connMsg = "";
				frappe.call({
					method: "zikpro_uk_vat.cockpit.get_authorize_url",
					callback: (r) => {
						this.connBusy = false;
						const res = r.message || {};
						if (res.ok && res.url) {
							window.location.href = res.url;
						} else {
							this.connMsg = res.message || "Unable to start the connection.";
						}
					},
					error: () => {
						this.connBusy = false;
						this.connMsg = "Unable to start the connection. Please try again.";
					},
				});
			},
		},
		template: `
			<div class="vat-cockpit">
				<div class="vc-head">
					<div class="vc-head-right">
						<a class="vc-exit" href="/app" title="Back to the Desk">☰ Desk</a>
						<span class="vc-badge" :class="{ prod: conn.environment === 'Production' }">{{ conn.environment || 'Sandbox' }}</span>
					</div>
					<div class="vc-title">UK VAT <span class="vc-sub">Making Tax Digital</span></div>
				</div>
				<div class="vc-nav">
					<button v-for="t in tabs" :key="t.key"
						:class="['vc-tab', { active: active === t.key }]"
						@click="active = t.key">{{ t.label }}</button>
				</div>

				<div class="vc-body" v-if="active === 'connect'">
					<h3>Connect</h3>
					<p class="text-muted">Authorise this app with HMRC ({{ (conn.environment || 'Sandbox').toLowerCase() }})</p>
					<div v-if="connResult === 'success'" class="vc-note ok-note">✓ Connected to HMRC successfully.</div>
					<div v-else-if="connResult === 'denied'" class="vc-note warn">Authorisation was declined at HMRC. You can try connecting again.</div>
					<div v-else-if="connResult === 'error'" class="vc-note warn">The connection could not be completed. Please try again.</div>
					<div v-if="connLoading" class="text-muted">Loading…</div>
					<div v-else>
						<div class="vc-card">
							<div class="vc-row">
								<span class="vc-k">HMRC connection</span>
								<span :class="['vc-status', conn.connected ? 'ok' : (conn.vrn_mismatch ? 'warn-pill' : 'off')]">{{ conn.state }}</span>
							</div>
							<div class="vc-row"><span class="vc-k">Company</span><span>{{ conn.company || '—' }}</span></div>
							<div class="vc-row"><span class="vc-k">VAT registration no.</span><span>{{ conn.vrn || 'Not set' }}</span></div>
							<div v-if="conn.vrn_mismatch" class="vc-row">
								<span class="vc-k">Authorised for</span><span>{{ conn.authorised_vrn }}</span>
							</div>
							<div class="vc-row" v-if="conn.broker_only">
								<span class="vc-k">Broker signup token</span>
								<span :class="['vc-status', conn.signup_token_set ? 'ok' : 'off']">
									{{ conn.signup_token_set ? 'Set' : 'Not set' }}
								</span>
							</div>
							<div class="vc-row" v-else>
								<span class="vc-k">Client credentials</span>
								<span :class="['vc-status', conn.client_configured ? 'ok' : 'off']">
									{{ conn.client_configured ? 'Configured' : 'Not set' }}
								</span>
							</div>
						</div>
						<div v-if="conn.vrn_mismatch" class="vc-note warn">
							The stored authorisation is for VAT number <b>{{ conn.authorised_vrn }}</b>, but this
							Company's VAT number is <b>{{ conn.vrn }}</b>. HMRC rejects every request until they
							match. Connecting again will re-authorise for {{ conn.vrn }}; if that number is the
							one that is wrong, correct it on the Company first.
						</div>

						<div class="vc-actions">
							<button v-if="!conn.connected" class="vc-btn primary"
								:disabled="connBusy || !conn.can_connect"
								@click="startConnect">
								{{ connBusy ? 'Starting…' : 'Connect to HMRC' }}
							</button>
							<button class="vc-btn" :disabled="connLoading" @click="loadConnection">Refresh status</button>
							<button class="vc-btn" @click="active = 'settings'">VAT Settings</button>
						</div>

						<div v-if="connMsg" class="vc-note warn">{{ connMsg }}</div>
						<div v-else-if="conn.broker_only && !conn.can_connect" class="vc-note">
							Add your <b>Broker signup token</b> in
							<a href="#" @click.prevent="active = 'settings'">VAT Settings</a>, then return here to connect.
							Don't have a token? Request a free one from
							<a href="mailto:info@zikpro.com?subject=UK%20MTD%20VAT%20broker%20signup%20token">info@zikpro.com</a> —
							it links this site to HMRC through ZikPro's broker.
						</div>
						<div v-else-if="!conn.broker_only && !conn.client_configured" class="vc-note">
							Add your HMRC <b>client ID</b>, <b>client secret</b> and <b>redirect URL</b> in
							<a href="#" @click.prevent="active = 'settings'">VAT Settings</a>, then return here to connect.
						</div>
						<div v-else-if="conn.connected" class="vc-note">
							Connected to HMRC. Your obligations, liabilities and payments are available in the Dashboard.
						</div>
					</div>
				</div>

				<div class="vc-body" v-else-if="active === 'dashboard'">
					<h3>Dashboard</h3>
					<p class="text-muted">Where you stand with HMRC</p>
					<div v-if="!proInstalled && dash.pro_schedules_orphaned && dash.pro_schedules_orphaned.length" class="vc-note warn" style="margin-bottom:12px">
						<b>{{ dash.pro_schedules_orphaned.length }} open Capital Goods / Partial Exemption schedule(s) need attention.</b>
						These are year-end adjustment schedules whose future intervals are calculated by <b>UK VAT Pro</b>, which isn't installed. They won't auto-generate — re-subscribe to Pro, or post each due interval as a <b>manual adjustment</b> (Prepare Return) so nothing is missed:
						<ul style="margin:6px 0 0 18px">
							<li v-for="s in dash.pro_schedules_orphaned" :key="s.name">{{ s.schedule_type }} — due {{ s.trigger_date }}<span v-if="s.notice_ref"> ({{ s.notice_ref }})</span> · <span class="text-muted">{{ s.name }}</span></li>
						</ul>
					</div>
					<div v-if="loading" class="text-muted">Loading…</div>
					<div v-else>
						<div class="vc-grid"><div><div class="vc-card">
							<div class="vc-row">
								<span class="vc-k">HMRC connection</span>
								<span :class="['vc-status', (dash.connected && !authError) ? 'ok' : ((dash.vrn_mismatch || authError) ? 'warn-pill' : 'off')]">
									{{ (dash.connected && !authError) ? 'Connected' : (authError ? 'Re-authorisation needed' : dash.status) }}
								</span>
							</div>
							<div class="vc-row"><span class="vc-k">Company</span><span>{{ dash.company || '—' }}</span></div>
							<div class="vc-row"><span class="vc-k">VAT registration no.</span><span>{{ dash.vrn || 'Not set' }}</span></div>
							<div v-if="dash.vrn_mismatch" class="vc-row">
								<span class="vc-k">Authorised for</span><span>{{ dash.authorised_vrn }}</span>
							</div>
						</div>
						<div v-if="dash.vrn_mismatch" class="vc-note warn">
							This HMRC authorisation was granted for VAT number <b>{{ dash.authorised_vrn }}</b>,
							but the Company is now set to <b>{{ dash.vrn }}</b>. HMRC rejects every request
							until the two match. Either correct the VAT number on the Company, or use
							<b>Connect</b> to re-authorise for {{ dash.vrn }}.
						</div>
						<div v-else-if="dash.broker_only && !dash.signup_token_set" class="vc-note">
							<b>One step before you can file.</b> This edition submits to HMRC securely through
							ZikPro's Making Tax Digital broker, which needs a free <b>signup token</b> to link
							this site. If you don't have one yet, request it from
							<a href="mailto:info@zikpro.com?subject=UK%20MTD%20VAT%20broker%20signup%20token">info@zikpro.com</a>
							— then paste it into <a href="#" @click.prevent="active = 'settings'">VAT Settings</a>
							and click <b>Connect</b>. (Your invoices and returns already work; only submission needs the token.)
						</div>
						<div v-else-if="!dash.connected" class="vc-note">
							Not connected to HMRC. Use <b>Connect</b> to authorise — then your obligations,
							liabilities and payments appear here.
						</div>
						</div><div v-if="dash.connected && penalties && penalties.has_data" class="vc-section">
							<div class="vc-section-head">Compliance <span class="text-muted">— penalty points &amp; charges</span></div>
							<div class="vc-card">
								<div class="vc-row">
									<span class="vc-k">Late-submission penalty points</span>
									<span :class="['vc-status', penalties.at_threshold ? 'warn-pill' : (penalties.active_points ? 'warn-pill' : 'ok')]">
										{{ penalties.active_points }} of {{ penalties.threshold }}{{ penalties.at_threshold ? ' · £200 penalty' : '' }}
									</span>
								</div>
								<div class="vc-row" v-if="penalties.late_payment_posted">
									<span class="vc-k">Late-payment penalties charged</span><span>{{ fmtMoney(penalties.late_payment_posted) }}</span>
								</div>
								<div class="vc-row" v-if="penalties.late_payment_estimate">
									<span class="vc-k">Late-payment penalties estimated</span><span>{{ fmtMoney(penalties.late_payment_estimate) }}</span>
								</div>
							</div>
							<div v-if="penalties.at_threshold" class="vc-note warn">
								You have reached the penalty-points threshold — a £200 penalty applies, and each further late return adds another. File on time to start reducing points.
							</div>
						</div>
						</div><div v-if="dash.connected" class="vc-section">
							<div class="vc-section-head">
								VAT periods <span class="text-muted">— filing and payment are two separate duties</span>
							</div>
							<div v-if="oblLoading" class="text-muted">Loading periods…</div>
							<div v-else-if="oblMsg" class="vc-note warn">{{ oblMsg }}</div>
							<div v-else-if="!obligations.length" class="vc-note">No VAT periods in the last 12 months.</div>
							<table v-else class="vc-table vc-table-wide">
								<thead>
									<tr><th>Period</th><th>Due</th><th>Filed</th><th>Paid</th><th class="num">Outstanding</th></tr>
								</thead>
								<tbody>
									<tr v-for="o in obligations" :key="o.periodKey">
										<td>{{ fmtDate(o.start) }} – {{ fmtDate(o.end) }}</td>
										<td>
											{{ fmtDate(o.due) }}
											<span v-if="o.filing_overdue || o.payment_overdue" class="vc-status warn-pill" style="margin-left:6px">{{ dueLabel(o) }}</span>
										</td>
										<td>
											<span :class="['vc-status', o.filed ? 'ok' : 'warn-pill']">{{ o.filed ? 'Filed' : 'Not filed' }}</span>
										</td>
										<td>
											<span v-if="o.paid === true" class="vc-status ok">Paid</span>
											<span v-else-if="o.paid === false" class="vc-status warn-pill">Unpaid</span>
											<span v-else class="text-muted">—</span>
										</td>
										<td class="num">{{ o.outstanding === null || o.outstanding === undefined ? '—' : fmtMoney(o.outstanding) }}</td>
									</tr>
								</tbody>
							</table>
							<div class="vc-hint" style="margin-top:8px">
								VAT is paid to HMRC directly (bank/card/Direct Debit) — not from here. This shows what HMRC has recorded.
							</div>

							<div class="vc-grid"><div><div class="vc-section-head">What you owe <span class="text-muted">(liabilities)</span></div>
							<div v-if="liabLoading" class="text-muted">Loading liabilities…</div>
							<div v-else-if="liabMsg" class="vc-note warn">{{ liabMsg }}</div>
							<div v-else-if="!liabilities.length" class="vc-note">No outstanding VAT liabilities.</div>
							<table v-else class="vc-table">
								<thead><tr><th>Period</th><th>Type</th><th>Outstanding</th><th>Due</th></tr></thead>
								<tbody>
									<tr v-for="(l, i) in liabilities" :key="i">
										<td>{{ fmtDate(l.taxPeriod && l.taxPeriod.from) }} – {{ fmtDate(l.taxPeriod && l.taxPeriod.to) }}</td>
										<td>{{ l.type || '—' }}</td>
										<td>{{ fmtMoney(l.outstandingAmount !== undefined ? l.outstandingAmount : l.originalAmount) }}</td>
										<td>{{ fmtDate(l.due) }}</td>
									</tr>
								</tbody>
							</table>

							</div><div><div class="vc-section-head">Payments received <span class="text-muted">by HMRC</span></div>
							<div v-if="payLoading" class="text-muted">Loading payments…</div>
							<div v-else-if="payMsg" class="vc-note warn">{{ payMsg }}</div>
							<div v-else-if="!payments.length" class="vc-note">No VAT payments recorded.</div>
							<table v-else class="vc-table">
								<thead><tr><th>Received</th><th>Amount</th></tr></thead>
								<tbody>
									<tr v-for="(pmt, i) in payments" :key="i">
										<td>{{ fmtDate(pmt.received) }}</td>
										<td>{{ fmtMoney(pmt.amount) }}</td>
									</tr>
								</tbody>
							</table></div></div>
						</div>
					</div>
				</div>

				<div class="vc-body" v-else-if="active === 'reports'">
					<h3>Reports</h3>
					<p class="text-muted">VAT transactions — click a reference to open the exact invoice</p>
					<div class="vc-filter">
						<label>From <input type="date" v-model="txnFrom" /></label>
						<label>To <input type="date" v-model="txnTo" /></label>
						<button class="vc-btn" :disabled="txnLoading" @click="loadReports">Apply</button>
					</div>
					<div v-if="txnLoading" class="text-muted">Loading transactions…</div>
					<div v-else-if="txnMsg" class="vc-note warn">{{ txnMsg }}</div>
					<div v-else-if="!txns.length" class="vc-note">No VAT transactions in this period.</div>
					<table v-else class="vc-table vc-table-wide">
						<thead>
							<tr><th>Date</th><th>Type</th><th>Reference</th><th>Party</th>
								<th class="num">Net</th><th class="num">VAT</th><th class="num">Total</th></tr>
						</thead>
						<tbody>
							<tr v-for="t in txns" :key="t.doctype + t.name">
								<td>{{ fmtDate(t.date) }}</td>
								<td>{{ t.type }}</td>
								<td><a :href="rowRoute(t)">{{ t.name }}</a></td>
								<td>{{ t.party || '—' }}</td>
								<td class="num">{{ fmtMoney(t.net) }}</td>
								<td class="num">{{ fmtMoney(t.tax) }}</td>
								<td class="num">{{ fmtMoney(t.total) }}</td>
							</tr>
						</tbody>
					</table>
									<div class="vc-section" style="margin-top:24px">
						<div class="vc-section-head">Adjustment schedules <span class="text-muted">— <template v-if="proInstalled">bad-debt / capital-goods / partial-exemption, generated when due</template><template v-else>bad-debt schedules, generated when due (capital-goods &amp; partial-exemption schedules need UK VAT Pro)</template></span></div>
						<div class="vc-actions" style="margin-bottom:10px">
							<button class="vc-btn" :disabled="schedBusy" @click="loadSchedules">Refresh</button>
							<button class="vc-btn primary" :disabled="schedBusy" @click="runSchedules">{{ schedBusy ? 'Working…' : 'Generate due adjustments' }}<span v-if="schedDue"> ({{ schedDue }} due)</span></button>
							<button class="vc-btn" @click="showSchedForm = !showSchedForm">{{ showSchedForm ? 'Close' : '+ Add schedule' }}</button>
						</div>
						<div v-if="showSchedForm" class="vc-card" style="max-width:none; padding:14px 16px; margin-bottom:12px">
							<div class="vc-filter">
								<label>Type <select v-model="schedForm.schedule_type"><option v-for="t in (proInstalled ? schedTypes : schedTypes.filter(x => x === 'Bad Debt Relief'))" :key="t">{{ t }}</option></select></label>
								<label>Box <select v-model="schedForm.vat_box"><option v-for="b in adjBoxes" :key="b">{{ b }}</option></select></label>
								<label>Trigger date <input type="date" v-model="schedForm.trigger_date" /></label>
								<label>Amount <input type="number" step="0.01" v-model="schedForm.amount" placeholder="e.g. 200.00" /></label>
							</div>
								<div v-if="schedForm.schedule_type === 'Capital Goods Scheme'" class="vc-filter" style="margin-top:2px">
									<label>Total input VAT <input type="number" step="0.01" v-model="calcForm.total_vat" /></label>
									<label>Intervals <input type="number" v-model="calcForm.intervals" placeholder="5 or 10" /></label>
									<label>Baseline % <input type="number" step="0.01" v-model="calcForm.baseline_pct" /></label>
									<label>This interval % <input type="number" step="0.01" v-model="calcForm.interval_pct" /></label>
									<button class="vc-btn" :disabled="schedBusy" @click="computeSchedAmount">Compute amount</button>
								</div>
								<div v-else-if="schedForm.schedule_type === 'Partial Exemption Annual'" class="vc-filter" style="margin-top:2px">
									<label>Residual input VAT <input type="number" step="0.01" v-model="calcForm.residual_vat" /></label>
									<label>Annual recovery % <input type="number" step="0.01" v-model="calcForm.annual_pct" /></label>
									<label>Provisional % applied <input type="number" step="0.01" v-model="calcForm.provisional_pct" /></label>
									<button class="vc-btn" :disabled="schedBusy" @click="computeSchedAmount">Compute amount</button>
								</div>
								<div v-if="calcNote" class="vc-hint" style="margin-bottom:8px">Computed: {{ calcNote }} → Box 4</div>
							<div class="vc-field"><label>Reason (required)</label>
								<input type="text" v-model="schedForm.reason" placeholder="Why — kept for the audit trail" /></div>
							<div class="vc-field"><label>Notice reference (optional)</label>
								<input type="text" v-model="schedForm.notice_ref" placeholder="e.g. Notice 700/18" /></div>
							<div v-if="schedFormMsg" class="vc-note warn">{{ schedFormMsg }}</div>
							<div class="vc-actions">
								<button class="vc-btn primary" :disabled="schedBusy" @click="addSchedule">{{ schedBusy ? 'Saving…' : 'Create schedule' }}</button>
								<button class="vc-btn" :disabled="schedBusy" @click="showSchedForm = false; schedFormMsg = ''">Cancel</button>
							</div>
							<div class="vc-hint" style="margin-top:6px">The adjustment is generated on/after the trigger date (daily, or via “Generate due adjustments”). Bad-debt relief also checks the sale is still unpaid.</div>
						</div>
						<div v-if="schedMsg" class="vc-note ok-note">{{ schedMsg }}</div>
						<table v-if="schedules.length" class="vc-table vc-table-wide">
							<thead><tr><th>Type</th><th>Reference</th><th>Trigger</th><th>Box</th><th class="num">Amount</th><th>Status</th><th></th></tr></thead>
							<tbody>
								<tr v-for="s in schedules" :key="s.name">
									<td>{{ s.schedule_type }}</td>
									<td>{{ s.reference_name || '—' }}</td>
									<td>{{ fmtDate(s.trigger_date) }}</td>
									<td>{{ s.vat_box }}</td>
									<td class="num">{{ fmtMoney(s.amount) }}</td>
									<td><span :class="['vc-status', s.status==='Claimed' ? 'ok' : (s.status==='Pending' ? 'warn-pill' : 'off')]">{{ s.status }}</span></td>
									<td><button v-if="s.status==='Pending'" class="vc-btn" :disabled="schedBusy" @click="cancelSchedule(s.name)">Cancel</button></td>
								</tr>
							</tbody>
						</table>
						<div v-else class="text-muted">No adjustment schedules yet — create one from the “VAT Adjustment Schedule” list in the desk.</div>
					</div>
				</div>

				<div class="vc-body" v-else-if="active === 'prepare'">
					<h3>Prepare Return</h3>
					<p class="text-muted">{{ userRoles.is_preparer && !userRoles.is_sysmgr ? 'Pick an open period, review the 9 boxes, then send it for approval' : 'Pick an open period, review the 9 boxes, declare and submit' }}</p>

					<!-- APPROVER queue: returns a preparer sent for approval (SEC-11 SoD) -->
					<div v-if="userRoles.is_approver && (pendingApprovals.length || approvedReceipt)" class="vc-section" style="margin-bottom:22px">
						<div class="vc-section-head">Awaiting your approval</div>
						<div v-if="approvedReceipt" class="vc-note ok-note">
							✓ Approved and filed to HMRC.
							<span v-if="approvedReceipt.formBundleNumber">Receipt: {{ approvedReceipt.formBundleNumber }}.</span>
						</div>
						<table v-if="pendingApprovals.length" class="vc-table vc-table-wide">
							<thead><tr><th>Period</th><th>Prepared by</th><th class="num">Box 5 (net VAT)</th><th></th></tr></thead>
							<tbody>
								<tr v-for="row in pendingApprovals" :key="row.name">
									<td>{{ fmtDate(row.period_start_date) }} – {{ fmtDate(row.period_end_date) }}</td>
									<td>{{ row.prepared_by }}</td>
									<td class="num">{{ fmtMoney(row.net_vat_due_box5) }}</td>
									<td class="num">
										<span v-if="row.prepared_by === userRoles.user" class="vc-status warn-pill" title="Segregation of duties">You prepared this</span>
										<button v-else class="vc-btn primary" :disabled="approveBusyFor === row.name" @click="approveReturn(row)">
											{{ approveBusyFor === row.name ? 'Filing…' : 'Approve & file to HMRC' }}
										</button>
									</td>
								</tr>
							</tbody>
						</table>
						<div v-if="prepActionMsg" class="vc-note warn" style="margin-top:8px">{{ prepActionMsg }}</div>
						<div class="vc-hint" style="margin-top:6px">
							Approving is the legal declaration and files the return to HMRC. You cannot approve a return you prepared — a different user must.
						</div>
					</div>

					<div v-if="prepLoading" class="text-muted">Loading open periods…</div>
					<div v-else-if="prepMsg" class="vc-note warn">{{ prepMsg }}</div>
					<div v-else-if="!prepObls.length" class="vc-note">No open VAT periods to file right now.</div>
					<div v-else>
						<div class="vc-section-head">
							Open periods
							<span class="text-muted" v-if="prepObls.length > 1">— oldest first; file the overdue period first</span>
						</div>
						<div class="vc-period-list">
							<button v-for="o in prepObls" :key="o.periodKey"
								:class="['vc-period', { active: selectedObl && selectedObl.periodKey === o.periodKey }]"
								@click="selectObligation(o)">
								{{ fmtDate(o.start) }} – {{ fmtDate(o.end) }}
								<span class="text-muted">· due {{ fmtDate(o.due) }}</span>
								<span v-if="o.filing_overdue" class="vc-status warn-pill" style="margin-left:8px">Overdue · {{ dueLabel(o) }}</span>
								<span v-else-if="o.days_to_due !== null && o.days_to_due !== undefined" class="text-muted"> · {{ dueLabel(o) }}</span>
							</button>
						</div>

						<div v-if="selectedObl">
							<div v-if="figLoading" class="text-muted" style="margin-top:16px">Calculating…</div>
							<div v-else-if="figMsg" class="vc-note warn">{{ figMsg }}</div>
							<div v-else-if="figures">
								<div v-if="(figures.warnings || []).length && !submitResult" class="vc-note warn" style="margin-top:16px">
									<b>Check these before filing — the figures below may be incomplete:</b>
									<ul style="margin:8px 0 0; padding-left:18px">
										<li v-for="(w, i) in figures.warnings" :key="'fw'+i" v-html="linkifyWarning(w)"></li>
									</ul>
								</div>
								<div class="vc-section-head" style="margin-top:20px">9-box return
										<button class="vc-btn" style="float:right; font-size:12px; padding:3px 10px" :disabled="calcBusy" @click="loadCalcNotes">{{ calcBusy ? 'Loading…' : (showCalcNotes ? 'Hide notes' : 'Calculation notes') }}</button>
									</div>
									<div v-if="showCalcNotes && calcNotes" class="vc-card" style="max-width:none; margin-bottom:14px; padding:14px 18px">
										<div class="vc-section-head">Calculation notes <span class="text-muted">— review before filing (VAT Notice 700/22)</span></div>
										<div class="vc-row"><span class="vc-k">Company / VRN</span><span>{{ calcNotes.identity.company || '—' }} · {{ calcNotes.identity.vrn || 'Not set' }}</span></div>
										<div class="vc-row"><span class="vc-k">Period · basis</span><span>{{ fmtDate(calcNotes.identity.period_from) }} – {{ fmtDate(calcNotes.identity.period_to) }} · {{ calcNotes.identity.basis }}</span></div>
										<div class="vc-row"><span class="vc-k">Prepared</span><span>{{ calcNotes.identity.prepared_on }}</span></div>
										<div v-if="calcNotes.filed" class="vc-row"><span class="vc-k">Filed</span><span><span class="vc-status ok" style="margin-right:6px">As filed</span>{{ calcNotes.filed.filed_on || '' }}<template v-if="calcNotes.filed.form_bundle_number"> · receipt {{ calcNotes.filed.form_bundle_number }}</template></span></div>
										<div class="vc-section-head" style="margin-top:14px">How each box was derived</div>
										<table class="vc-table"><thead><tr><th>Box</th><th class="num">Amount</th><th>How derived</th></tr></thead>
											<tbody><tr v-for="b in calcNotes.boxes" :key="b.box"><td>{{ b.box }}</td><td class="num">{{ fmtMoney(b.amount) }}</td><td>{{ b.how }}</td></tr></tbody>
										</table>
										<template v-if="calcNotes.vat_account && calcNotes.vat_account.length">
											<div class="vc-section-head" style="margin-top:14px">VAT account by treatment <span class="text-muted">(Notice 700/22 — {{ calcNotes.vat_account_source === 'live' ? 'live · indicative' : calcNotes.vat_account_source }})</span></div>
											<table class="vc-table">
												<thead><tr><th>Treatment</th><th class="num">Rate</th><th class="num">Net</th><th class="num">Sales</th><th class="num">Purchases</th><th class="num">Lines</th></tr></thead>
												<tbody>
													<tr v-for="t in calcNotes.vat_account" :key="t.treatment">
														<td>{{ t.treatment }}</td>
														<td class="num">{{ t.rate === null ? '—' : t.rate + '%' }}</td>
														<td class="num">{{ fmtMoney(t.net) }}</td>
														<td class="num">{{ fmtMoney(t.sales_net) }}</td>
														<td class="num">{{ fmtMoney(t.purchase_net) }}</td>
														<td class="num">{{ t.lines }}</td>
													</tr>
												</tbody>
											</table>
										</template>
										<div class="vc-section-head" style="margin-top:14px">Counts</div>
										<div class="vc-row"><span class="vc-k">Sales invoices · credit notes</span><span>{{ calcNotes.counts.sales_invoices }} · {{ calcNotes.counts.sales_credit_notes }}</span></div>
										<div class="vc-row"><span class="vc-k">Purchase invoices · credit notes</span><span>{{ calcNotes.counts.purchase_invoices }} · {{ calcNotes.counts.purchase_credit_notes }}</span></div>
										<div class="vc-row"><span class="vc-k">Adjustments</span><span>{{ calcNotes.counts.adjustments }}</span></div>
										<template v-if="calcNotes.ledger.length">
											<div class="vc-section-head" style="margin-top:14px">VAT ledger activity</div>
											<table class="vc-table"><thead><tr><th>Event</th><th class="num">Count</th><th class="num">VAT</th></tr></thead>
												<tbody><tr v-for="l in calcNotes.ledger" :key="l.event_type"><td>{{ l.event_type }}</td><td class="num">{{ l.count }}</td><td class="num">{{ fmtMoney(l.vat) }}</td></tr></tbody>
											</table>
										</template>
										<div class="vc-section-head" style="margin-top:14px">Assumptions</div>
										<ul style="margin:0; padding-left:18px">
											<li v-for="(a, i) in calcNotes.assumptions" :key="'as'+i" class="text-muted" style="margin-bottom:3px">{{ a }}</li>
										</ul>
									</div>
								<table class="vc-table vc-table-wide">
									<thead><tr><th>Box</th><th>Description</th><th class="num">Amount</th><th></th></tr></thead>
									<tbody>
										<template v-for="d in boxDefs" :key="d.k">
											<tr :class="{ clickable: d.drill }" @click="toggleDrill(d)">
												<td>{{ d.n }}</td>
												<td>{{ d.label }}
													<span v-if="d.ni" class="vc-status off" style="margin-left:6px">NI only</span>
												</td>
												<td class="num">{{ fmtMoney(boxValue(d.k)) }}</td>
												<td class="drill-cell">{{ d.drill ? (drillBox === d.k ? '▾' : '▸') : '' }}</td>
											</tr>
											<tr v-if="drillBox === d.k" :key="d.k + '-drill'">
												<td colspan="4" class="drill-panel">
													<div v-if="!drillRows(d).length" class="text-muted">No transactions.</div>
													<table v-else class="vc-table">
														<thead><tr><th>Date</th><th>Reference</th><th>Party</th><th class="num">Net</th><th class="num">VAT</th></tr></thead>
														<tbody>
															<tr v-for="t in drillRows(d)" :key="t.name">
																<td>{{ fmtDate(t.date) }}</td>
																<td><a :href="rowRoute(t)">{{ t.name }}</a>
																	<span v-if="t.is_return" class="vc-status warn-pill" style="margin-left:6px">Credit note</span></td>
																<td>{{ t.party || '—' }}</td>
																<td class="num">{{ fmtMoney(t.net) }}</td>
																<td class="num">{{ fmtMoney(t.vat) }}</td>
															</tr>
														</tbody>
													</table>
												</td>
											</tr>
										</template>
									</tbody>
								</table>

								<div v-if="!submitResult" class="vc-section" style="margin-top:20px">
									<div class="vc-section-head">Adjustments <span class="text-muted">— corrections that fold into the boxes above</span></div>
									<table v-if="(figures.adjustments || []).length" class="vc-table" style="margin-bottom:10px">
										<thead><tr><th>Date</th><th>Type</th><th>Box</th><th class="num">Amount</th><th>Reason</th><th></th></tr></thead>
										<tbody>
											<tr v-for="a in figures.adjustments" :key="a.name">
												<td>{{ fmtDate(a.posting_date) }}</td>
												<td>{{ a.adjustment_type }}</td>
												<td>{{ a.vat_box }}</td>
												<td class="num">{{ fmtMoney(a.amount) }}</td>
												<td>{{ a.reason }}</td>
												<td><button class="vc-btn" :disabled="adjBusy" @click="cancelAdj(a.name)">Remove</button></td>
											</tr>
										</tbody>
									</table>
									<div v-else class="text-muted" style="margin-bottom:10px">No adjustments for this period.</div>
									<button v-if="!showAdjForm" class="vc-btn" @click="showAdjForm = true">+ Add adjustment</button>
									<div v-else class="vc-card" style="max-width:none; padding:14px 16px">
										<div class="vc-filter">
											<label>Type
												<select v-model="adjForm.adjustment_type"><option v-for="t in adjTypes" :key="t">{{ t }}</option></select>
											</label>
											<label>Box
												<select v-model="adjForm.vat_box"><option v-for="b in adjBoxes" :key="b">{{ b }}</option></select>
											</label>
											<label>Amount
												<input type="number" step="0.01" v-model="adjForm.amount" placeholder="e.g. -50.00" />
											</label>
										</div>
										<div class="vc-field"><label>Reason (required)</label>
											<input type="text" v-model="adjForm.reason" placeholder="Why this adjustment — kept for the audit trail" /></div>
										<div class="vc-field"><label>Notice reference (optional)</label>
											<input type="text" v-model="adjForm.notice_ref" placeholder="e.g. VAT Notice 706" /></div>
										<div v-if="adjMsg" class="vc-note warn">{{ adjMsg }}</div>
										<div class="vc-actions">
											<button class="vc-btn primary" :disabled="adjBusy" @click="addAdjustment">{{ adjBusy ? 'Saving…' : 'Save adjustment' }}</button>
											<button class="vc-btn" :disabled="adjBusy" @click="showAdjForm = false; adjMsg = ''">Cancel</button>
										</div>
										<div class="vc-hint" style="margin-top:6px">Adjustment lands in this period and folds into the boxes above. Box 3 and Box 5 are calculated automatically.</div>
									</div>
								</div>

								<div v-if="submitResult" class="vc-note ok-note">
									✓ Return submitted to HMRC.
									<span v-if="submitResult.formBundleNumber">Receipt: {{ submitResult.formBundleNumber }}.</span>
									<span v-if="submitResult.processingDate">Processed {{ fmtDate((submitResult.processingDate || '').slice(0,10)) }}.</span>
								</div>
								<div v-else-if="sentForApproval" class="vc-note ok-note">
									✓ Sent for approval. A different user with the Approver role must review and file it
									(under “Awaiting your approval”). You cannot approve a return you prepared.
								</div>
								<template v-else>
									<div v-if="figures.blocking" class="vc-note warn">
										This return can't be submitted until the VAT figures are complete —
										fix the VAT account mapping in Settings (see the warnings above), then
										recalculate. Filing now would send an incorrect return to HMRC.
									</div>
									<!-- PREPARER (not admin): send for a separate approver to file -->
									<template v-else-if="userRoles.is_preparer && !userRoles.is_sysmgr">
										<div v-if="prepActionMsg" class="vc-note warn">{{ prepActionMsg }}</div>
										<div class="vc-actions">
											<button class="vc-btn primary" :disabled="prepBusy" @click="sendForApproval">
												{{ prepBusy ? 'Sending…' : 'Prepare &amp; send for approval' }}
											</button>
										</div>
										<div class="vc-hint" style="margin-top:6px">Preparing records these figures for a separate approver to review and file — it does not contact HMRC.</div>
									</template>
									<!-- APPROVER-only: nothing to file directly here; use the queue above -->
									<div v-else-if="userRoles.is_approver && !userRoles.is_sysmgr" class="vc-note">
										You have the Approver role. Prepared returns awaiting your approval appear under
										“Awaiting your approval” above. Preparing a return is done by a user with the Preparer role.
									</div>
									<!-- SYSTEM MANAGER (admin direct file) -->
									<template v-else>
										<label class="vc-declare">
											<input type="checkbox" v-model="declared" />
											I confirm the information is true and complete. Submitting a false declaration is a legal offence and can result in prosecution.
										</label>
										<div v-if="submitMsg" class="vc-note warn">{{ submitMsg }}</div>
										<div class="vc-actions">
											<button class="vc-btn primary" :disabled="!declared || submitting || figures.blocking" @click="submitReturn">
												{{ submitting ? 'Submitting…' : 'Submit to HMRC' }}
											</button>
										</div>
									</template>
								</template>
							</div>
						</div>
					</div>
				</div>

				<div class="vc-body" v-else-if="active === 'history'">
					<h3>History</h3>
					<p class="text-muted">Your VAT periods — view a submitted return as HMRC holds it</p>
					<div v-if="histLoading" class="text-muted">Loading…</div>
					<div v-else-if="histMsg" class="vc-note warn">{{ histMsg }}</div>
					<div v-else-if="!histObls.length" class="vc-note">No VAT periods found.</div>
					<table v-else class="vc-table vc-table-wide">
						<thead>
							<tr><th>Period</th><th>Due</th><th>HMRC status</th><th>Filed here</th><th></th></tr>
						</thead>
						<tbody>
							<template v-for="o in histObls" :key="o.periodKey">
								<tr>
									<td>{{ fmtDate(o.start) }} – {{ fmtDate(o.end) }}</td>
									<td>{{ fmtDate(o.due) }}</td>
									<td><span :class="['vc-status', o.status === 'F' ? 'ok' : 'warn-pill']">{{ o.status === 'F' ? 'Fulfilled' : 'Open' }}</span></td>
									<td>
										<span v-if="histFiled[o.periodKey]" class="vc-status ok" :title="'Receipt ' + (histFiled[o.periodKey].form_bundle_number || '')">✓ {{ histFiled[o.periodKey].name }}</span>
										<span v-else class="text-muted">—</span>
									</td>
									<td class="num"><button class="vc-btn" @click="viewReturn(o)">{{ histViewing === o.periodKey ? 'Hide' : 'View return' }}</button></td>
								</tr>
								<tr v-if="histViewing === o.periodKey" :key="o.periodKey + '-r'">
									<td colspan="5" class="drill-panel">
										<div v-if="histFiled[o.periodKey]" class="vc-hint" style="margin-bottom:10px">
											Filed from this software as <b>{{ histFiled[o.periodKey].name }}</b>
											<span v-if="histFiled[o.periodKey].form_bundle_number"> · HMRC receipt {{ histFiled[o.periodKey].form_bundle_number }}</span>
											<span v-if="histFiled[o.periodKey].scheme"> · {{ histFiled[o.periodKey].scheme }} basis</span>
											<span v-if="histFiled[o.periodKey].submitted_on"> · {{ fmtDate((histFiled[o.periodKey].submitted_on || '').slice(0,10)) }}</span>
										</div>
										<div v-if="histBusy === o.periodKey" class="text-muted">Loading return…</div>
										<div v-else-if="histReturns[o.periodKey] && !histReturns[o.periodKey].ok" class="vc-note warn">{{ histReturns[o.periodKey].message }}</div>
										<div v-else-if="histReturns[o.periodKey] && !histReturns[o.periodKey].filed" class="vc-note">
											<template v-if="o.status === 'F'">
												HMRC records this period as <b>fulfilled</b>, but holds no return details for it.
												This can happen when the return was filed outside this software (or, on sandbox, where
												the test obligation has no stored return).
											</template>
											<template v-else>No return has been submitted for this period yet.</template>
										</div>
										<table v-else-if="histReturns[o.periodKey]" class="vc-table">
											<thead><tr><th>Box</th><th>Description</th><th class="num">Amount</th></tr></thead>
											<tbody>
												<tr v-for="d in boxDefs" :key="d.k">
													<td>{{ d.n }}</td><td>{{ d.label }}</td>
													<td class="num">{{ fmtMoney(histReturns[o.periodKey].boxes[d.k]) }}</td>
												</tr>
											</tbody>
										</table>
									</td>
								</tr>
							</template>
						</tbody>
					</table>
				</div>

				<div class="vc-body" v-else-if="active === 'adjustments'">
					<h3>Adjustments</h3>
					<p class="text-muted"><template v-if="proInstalled">Year-end VAT adjustments — Partial Exemption (Notice 706) and the Capital Goods Scheme (Notice 706/2). Preview here, then post via a VAT Adjustment.</template><template v-else>Automatic year-end adjustments (Partial Exemption, Capital Goods Scheme) are part of UK VAT Pro. Basic supports manual adjustments on the Prepare Return screen.</template></p>
					<div v-if="!proInstalled" class="vc-card">
						<div class="vc-form-head">Year-end adjustment engines — UK VAT Pro</div>
						<div class="vc-note">Automatic <b>Partial Exemption</b> annual adjustment (Notice 706) and <b>Capital Goods Scheme</b> interval calculations (Notice 706/2) are part of <b>UK VAT Pro</b>. Your Basic edition still supports <b>manual</b> adjustments on the Prepare Return screen — enter the figure yourself and it folds into the boxes. Upgrade at <b>zikpro.com</b> to compute them automatically.</div>
					</div>
					<template v-if="proInstalled">
					<div class="vc-card">
						<div class="vc-form-head">Partial Exemption — annual adjustment</div>
						<div class="vc-field"><label>VAT year from</label><input type="date" v-model="adjForm.pe_from" /></div>
						<div class="vc-field"><label>VAT year to</label><input type="date" v-model="adjForm.pe_to" /></div>
						<button class="vc-btn primary" :disabled="adjBusy || !adjForm.pe_from || !adjForm.pe_to" @click="calcPeAnnual">{{ adjBusy ? 'Calculating…' : 'Calculate PE adjustment' }}</button>
						<div v-if="peResult" style="margin-top:12px">
							<div class="vc-row"><span class="vc-k">Recovery %</span><span>{{ peResult.recovery_pct }}%</span></div>
							<div class="vc-row"><span class="vc-k">Taxable supplies</span><span>{{ fmtMoney(peResult.taxable_supplies) }}</span></div>
							<div class="vc-row"><span class="vc-k">Exempt supplies</span><span>{{ fmtMoney(peResult.exempt_supplies) }}</span></div>
							<div class="vc-row"><span class="vc-k">Input VAT</span><span>{{ fmtMoney(peResult.input_vat) }}</span></div>
							<div class="vc-row"><span class="vc-k">De minimis</span><span>{{ peResult.de_minimis ? 'Yes — fully recoverable' : 'No' }}</span></div>
							<div class="vc-row"><span class="vc-k">Box 4 adjustment</span><span>{{ fmtMoney(peResult.adjustment) }}</span></div>
							<div v-if="peResult.applicable === false" class="vc-note">Not applicable — no exempt supplies / no input VAT recovered this year.</div>
						</div>
					</div>
					<div class="vc-card">
						<div class="vc-form-head">Capital Goods Scheme — interval adjustment</div>
						<div class="vc-field"><label>Total input VAT on the asset</label><input type="number" v-model.number="adjForm.cgs_total" /></div>
						<div class="vc-field"><label>Intervals</label><select v-model.number="adjForm.cgs_intervals"><option :value="5">5 (computers, ships, aircraft)</option><option :value="10">10 (land &amp; buildings)</option></select></div>
						<div class="vc-field"><label>Baseline taxable-use %</label><input type="number" v-model.number="adjForm.cgs_baseline" /></div>
						<div class="vc-field"><label>This interval's taxable-use %</label><input type="number" v-model.number="adjForm.cgs_interval_use" /></div>
						<button class="vc-btn primary" :disabled="cgsBusy" @click="previewCgs">{{ cgsBusy ? 'Calculating…' : 'Preview interval adjustment' }}</button>
						<div v-if="cgsResult" style="margin-top:12px">
							<div class="vc-row"><span class="vc-k">Interval adjustment</span><span>{{ fmtMoney(cgsResult.amount) }}</span></div>
							<div class="vc-hint">Positive = extra recovery; negative = claw-back. Post via a VAT Adjustment (Box 4) dated in this interval.</div>
						</div>
					</div>
					</template>
				</div>

				<div class="vc-body" v-else-if="active === 'settings'">
					<h3>Settings</h3>
					<p class="text-muted">Everything VAT in one place — no need to leave the cockpit</p>
					<div v-if="setLoading" class="text-muted">Loading…</div>
					<div v-else-if="setErr && !setInfo.settings" class="vc-note warn">{{ setErr }}</div>
					<div v-else class="vc-form">
						<div class="vc-field">
							<label>Company</label>
							<div class="vc-ro">{{ setInfo.company || '—' }}</div>
						</div>
						<div class="vc-field">
							<label>VAT registration no.</label>
							<div class="vc-ro">{{ setInfo.vrn || 'Not set' }}</div>
						</div>
						<div class="vc-field">
							<label>Billing contact</label>
							<input type="text" v-model="setForm.billing_contact" />
						</div>

						<div class="vc-field">
							<label>VAT accounting scheme</label>
							<select v-model="setForm.vat_accounting_scheme" :disabled="setInfo.scheme_locked">
								<option v-for="s in (setInfo.schemes || [])" :key="s" :value="s">
									{{ s }}
								</option>
							</select>
							<div v-if="setInfo.scheme_locked" class="vc-hint">
								Locked for VAT year {{ setInfo.current_vat_year }} — the basis can't change mid-year.
								You can change it from the start of the next VAT year.
							</div>
							<div v-else class="vc-hint">
								Standard = by invoice date. Cash Accounting = by payment date (turnover ≤ £1.35M;
								Notice 731 exclusions are handled per invoice). Flat Rate = a fixed % of gross turnover
								(turnover ≤ £150K; Notice 733). Once chosen, the scheme locks for VAT year
								{{ setInfo.current_vat_year }}.
							</div>
							<div v-if="setInfo.cash_turnover_12m !== undefined" class="vc-hint">
								Taxable turnover (last 12 months): £{{ Number(setInfo.cash_turnover_12m).toLocaleString() }}.
								<span v-if="!setInfo.cash_eligible">Cash Accounting is unavailable above £1.35M.</span>
							</div>
						</div>

						<div class="vc-field" v-if="setForm.vat_accounting_scheme === 'Flat Rate Scheme'">
							<label>Flat rate percentage</label>
							<input type="number" step="0.1" min="0" max="20"
								v-model.number="setForm.flat_rate_percentage" :disabled="setInfo.scheme_locked" />
							<div class="vc-hint">
								Your HMRC flat rate for your sector (Notice 733). Include the 1% first-year discount
								while it applies; use 16.5% if you are a limited cost trader. Box 1 = this % of your
								VAT-inclusive turnover; input VAT is not reclaimed.
							</div>
						</div>

						<div class="vc-form-head">VAT accounts &amp; treatments</div>
						<div v-if="!setInfo.accounts_configured || !setInfo.treatments_configured" class="vc-note warn">
							<b>Not configured — your VAT boxes will show £0.</b>
							The app needs to know which ledger accounts hold Output (Sales) and Input
							(Purchases) VAT, and how each Item Tax Template is treated. Map them
							automatically from your chart of accounts:
							<div style="margin-top:10px">
								<button class="vc-btn primary" :disabled="vatSetupBusy" @click="autoConfigureVat()">
									{{ vatSetupBusy ? "Configuring…" : "Auto-configure defaults" }}
								</button>
							</div>
						</div>
						<div v-else class="vc-note ok-note">
							VAT accounts and treatments are configured — invoices will flow into the boxes.
							Re-run if your chart of accounts changed:
							<div style="margin-top:8px">
								<button class="vc-btn" :disabled="vatSetupBusy" @click="autoConfigureVat()">
									{{ vatSetupBusy ? "Configuring…" : "Re-configure defaults" }}
								</button>
							</div>
						</div>
						<div class="vc-field" style="margin-top:12px">
							<label>VAT accounts</label>
							<div class="vc-hint" style="margin-bottom:8px">
								Which ledger account each type of VAT posts to. If a box reads £0 when it
								shouldn't, the mapping here probably doesn't match the account your invoices
								actually use — correct it and save.
							</div>
							<table class="vc-table">
								<thead><tr><th>Type</th><th>Account</th></tr></thead>
								<tbody>
									<tr v-for="t in (setInfo.account_types || [])" :key="t">
										<td>{{ t }}</td>
										<td>
											<select v-model="acctForm[t]" style="width:100%">
												<option value="">— not mapped —</option>
												<option v-for="acc in (setInfo.account_options || [])" :key="acc" :value="acc">{{ acc }}</option>
											</select>
										</td>
									</tr>
								</tbody>
							</table>
							<div class="vc-actions" style="margin-top:8px">
								<button class="vc-btn" :disabled="acctBusy" @click="saveVatAccounts()">
									{{ acctBusy ? "Saving…" : "Save VAT accounts" }}
								</button>
							</div>
						</div>
						<div v-if="(treatForm || []).length" class="vc-field">
							<label class="vc-adv-toggle" @click="showAdvanced = !showAdvanced" style="cursor:pointer; user-select:none">{{ showAdvanced ? '▾' : '▸' }} Advanced — Item Tax Template treatments</label>
							<template v-if="showAdvanced">
								<div class="vc-hint" style="margin:6px 0 8px">How each Item Tax Template is treated. Zero-rated and exempt stay in Boxes 6/7; outside-scope (e.g. wages, disbursements) is excluded — untick "In Box 6/7". Defaults are set for you; only change these for custom templates.</div>
								<table class="vc-table vc-treat-table">
									<thead><tr><th>Item Tax Template</th><th>Treatment</th><th class="tc-center">In Box 6/7</th></tr></thead>
									<tbody>
										<tr v-for="row in treatForm" :key="row.item_tax_template">
											<td>{{ row.item_tax_template }}</td>
											<td>
												<select v-model="row.vat_treatment" style="width:100%">
													<option value="">— not classified —</option>
													<option v-for="opt in (setInfo.treatment_options || [])" :key="opt" :value="opt">{{ opt }}</option>
												</select>
												<div v-if="treatmentMismatch(row)" class="vc-hint" style="color:#a01c1c; margin-top:4px">⚠ Name suggests “{{ treatmentMismatch(row) }}”</div>
											</td>
											<td class="tc-center"><input type="checkbox" v-model="row.in_box_6_7" /></td>
										</tr>
									</tbody>
								</table>
								<div class="vc-actions" style="margin-top:8px"><button class="vc-btn" :disabled="treatBusy" @click="saveVatTreatments()">{{ treatBusy ? "Saving…" : "Save treatments" }}</button></div>
							</template>
						</div>

						<div class="vc-form-head">HMRC connection</div>
							<div v-if="setInfo.use_broker" class="vc-hint" style="margin-bottom:8px">This site connects to HMRC securely through ZikPro's broker — no HMRC credentials are stored here.</div>
							<div v-if="setInfo.broker_only" class="vc-field">
								<label>Broker signup token <span class="text-muted" v-if="setInfo.signup_token_set">(saved — leave blank to keep)</span></label>
								<input type="password" v-model="setForm.broker_signup_token" placeholder="paste the token ZikPro sent you" autocomplete="new-password" />
								<div class="vc-hint">Paste your ZikPro signup token, click <b>Save settings</b>, then go to <b>Connect</b>. Required to link this site to HMRC through the broker. Don't have one? Request a free token from <a href="mailto:info@zikpro.com?subject=UK%20MTD%20VAT%20broker%20signup%20token">info@zikpro.com</a>.</div>
							</div>
						<div class="vc-field" v-if="!setInfo.broker_only">
							<label>Client ID</label>
							<input type="text" v-model="setForm.client_id" />
						</div>
						<div class="vc-field" v-if="!setInfo.broker_only">
							<label>Client secret <span class="text-muted" v-if="setInfo.secret_set">(set — leave blank to keep)</span></label>
							<input type="password" v-model="setForm.client_secret" placeholder="••••••••" autocomplete="new-password" />
						</div>
						<div class="vc-field" v-if="!setInfo.broker_only">
							<label>Redirect URL</label>
							<input type="text" v-model="setForm.redirect_url" />
						</div>
						<div class="vc-field">
							<label>HMRC connection</label>
							<div class="vc-ro">
								<span :class="['vc-status', setInfo.connected ? 'ok' : 'off']">{{ setInfo.connected ? 'Connected' : 'Not connected' }}</span>
								<button class="vc-btn" style="margin-left:10px" @click="active = 'connect'">Manage connection</button>
							</div>
						</div>

						<div class="vc-form-head">Fraud-prevention headers</div>
						<div class="vc-field">
							<div class="vc-hint" style="margin-bottom:8px">
								Checks the anti-fraud headers this software sends, using HMRC's validator (read-only — it does not change what is sent).
								Run it on the live, public-facing site from your browser: on localhost/dev, headers that need a public IP will always show as invalid.
							</div>
							<button class="vc-btn" :disabled="fphBusy" @click="validateFph">{{ fphBusy ? 'Checking…' : 'Check fraud-prevention headers' }}</button>
							<div v-if="fph" style="margin-top:12px">
								<div v-if="!fph.ok && fph.message" class="vc-note warn">{{ fph.message }}</div>
								<template v-else>
									<div :class="['vc-note', fph.code === 'VALID_HEADERS' ? 'ok-note' : 'warn']">
										{{ fph.code }} — {{ fph.message }} <span class="text-muted" v-if="fph.spec_version">(spec {{ fph.spec_version }})</span>
									</div>
									<ul class="vc-hint" v-if="fph.errors && fph.errors.length" style="margin-top:8px">
										<li v-for="(e, i) in fph.errors" :key="'e'+i"><b>{{ e.code }}</b>: {{ (e.headers || []).join(', ') }}</li>
									</ul>
									<ul class="vc-hint" v-if="fph.warnings && fph.warnings.length" style="margin-top:6px">
										<li v-for="(w, i) in fph.warnings" :key="'w'+i">{{ w.code }}: {{ (w.headers || []).join(', ') }}</li>
									</ul>
								</template>
							</div>
						</div>

						<div v-if="setMsg" class="vc-note ok-note">{{ setMsg }}</div>
						<div v-if="setErr" class="vc-note warn">{{ setErr }}</div>
						<div class="vc-actions">
							<button class="vc-btn primary" :disabled="setBusy" @click="saveSettings">{{ setBusy ? 'Saving…' : 'Save settings' }}</button>
						</div>
					</div>
				</div>

				<div class="vc-body" v-else>
					<h3>{{ current.label }}</h3>
					<p class="text-muted">{{ current.hint }}</p>
					<div class="vc-placeholder">Screen coming next.</div>
				</div>
			</div>
		`,
	}).mount(el);
};
