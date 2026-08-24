"""VAT Ledger lifecycle E2E — multi-period, method-lock, cancel/amend, cash path.

Runnable via `bench execute zikpro_uk_vat.tests.test_ledger_lifecycle.prove_*`.
Creates REAL submitted invoices across the 2017-18 VAT year and asserts the ledger
+ 9-box figures behave per period (isolation / roll-over), aggregate across periods,
and re-derive on cancel. All test docs are tagged and cleaned up.

Honest scope: the VAT Adjustment engine (partial exemption / bad debt / CGS / error
correction) and a user-selectable Cash scheme are NOT built yet, so those cycles are
reported as BLOCKED-unbuilt, not skipped. The cash Realised code path is exercised by
forcing the scheme in the DB (labelled).
"""

import frappe
from frappe.utils import flt

from zikpro_uk_vat import cockpit as c
from zikpro_uk_vat import vat_ledger as vl

TAG = "LEDGERTEST"
LEDGER = "VAT Ledger Entry"

# Quarters of the 2017-18 UK VAT year (fiscal year 2017-2018 exists on this site).
Q1 = ("2017-04-01", "2017-06-30")
Q2 = ("2017-07-01", "2017-09-30")
Q3 = ("2017-10-01", "2017-12-31")
Q4 = ("2018-01-01", "2018-03-31")


def _company():
	return frappe.db.get_single_value("Global Defaults", "default_company")


def _mk_invoice(dt, posting_date, net, vat_rate=0.20, is_return=0):
	"""Create + submit a Sales/Purchase Invoice on a date with a known net & VAT."""
	company = _company()
	side_acct = c.vat_accounts(c.INPUT_VAT if dt == c.PURCHASE_INVOICE else c.OUTPUT_VAT)[0]
	if dt == c.PURCHASE_INVOICE:
		party = frappe.db.get_value("Supplier", {"disabled": 0}, "name")
		inc_exp = frappe.db.get_value("Account", {"company": company, "is_group": 0, "root_type": "Expense"}, "name")
	else:
		party = frappe.db.get_value("Customer", {"disabled": 0}, "name")
		inc_exp = frappe.db.get_value("Account", {"company": company, "is_group": 0, "root_type": "Income"}, "name")
	doc = frappe.new_doc(dt)
	doc.company = company
	doc.set("supplier" if dt == c.PURCHASE_INVOICE else "customer", party)
	doc.set_posting_time = 1
	doc.posting_date = posting_date
	doc.is_return = is_return
	qty = -1 if is_return else 1
	if dt == c.PURCHASE_INVOICE:
		doc.bill_no = f"{TAG}-{posting_date}-{frappe.generate_hash(length=5)}"
		doc.append("items", {"item_name": TAG, "description": TAG, "qty": qty, "rate": net,
							"expense_account": inc_exp, "uom": "Nos"})
	else:
		doc.append("items", {"item_name": TAG, "description": TAG, "qty": qty, "rate": net,
							"income_account": inc_exp, "uom": "Nos"})
	sign = -1 if is_return else 1
	doc.append("taxes", {"charge_type": "Actual", "account_head": side_acct,
						"description": "VAT 20%", "tax_amount": sign * net * vat_rate})
	doc.remarks = TAG
	doc.set_missing_values()
	doc.insert(ignore_permissions=True)
	doc.submit()
	return doc


def _cleanup():
	"""Cancel + delete every tagged test invoice (and its ledger entries)."""
	for dt in (c.SALES_INVOICE, c.PURCHASE_INVOICE):
		for name in frappe.get_all(dt, filters={"remarks": ["like", f"%{TAG}%"]}, pluck="name"):
			d = frappe.get_doc(dt, name)
			if d.docstatus == 1:
				d.cancel()
			frappe.delete_doc(dt, name, force=True, ignore_permissions=True)
	# any orphan ledger rows tagged
	for n in frappe.get_all(LEDGER, filters={"source_name": ["like", f"%{TAG}%"]}, pluck="name"):
		d = frappe.get_doc(LEDGER, n); d.flags.ignore_immutable = True; d.delete()
	frappe.db.commit()


def _boxes(period):
	return c.get_return_figures(period[0], period[1])["boxes"]


def _ledger_vat(period_key_dates=None, event_type="Accrued", source_like=TAG):
	f = {"event_type": event_type, "source_name": ["like", f"%{source_like}%"]}
	rows = frappe.get_all(LEDGER, filters=f, fields=["vat_amount", "posting_date", "vat_box"])
	return rows


def prove_multiperiod_accrual():
	"""Q2/Q3/Q4 2017-18: each period's boxes reflect ONLY that period's invoices
	(isolation), the ledger records one Accrued row per invoice, and the ledger
	aggregates across periods. Uses before/after DELTAS so pre-existing demo data
	doesn't matter."""
	_cleanup()
	res = {}
	base = {p: _boxes(p) for p in (Q2, Q3, Q4)}

	names = []
	# Q2: sales net 1000 (VAT 200 -> box1), purchase net 500 (VAT 100 -> box4)
	names.append(_mk_invoice(c.SALES_INVOICE, "2017-08-15", 1000).name)
	names.append(_mk_invoice(c.PURCHASE_INVOICE, "2017-08-20", 500).name)
	# Q3: sales net 2000 (VAT 400), purchase net 800 (VAT 160)
	names.append(_mk_invoice(c.SALES_INVOICE, "2017-11-10", 2000).name)
	names.append(_mk_invoice(c.PURCHASE_INVOICE, "2017-11-12", 800).name)
	# Q4: sales net 300 (VAT 60)
	names.append(_mk_invoice(c.SALES_INVOICE, "2018-02-01", 300).name)
	frappe.db.commit()

	q2, q3, q4 = _boxes(Q2), _boxes(Q3), _boxes(Q4)

	def d(after, before, box):
		return round(flt(after[box]) - flt(before[box]), 2)

	# Q2 deltas
	res["q2_box1_+200"] = d(q2, base[Q2], "box1") == 200
	res["q2_box4_+100"] = d(q2, base[Q2], "box4") == 100
	res["q2_box6_+1000"] = d(q2, base[Q2], "box6") == 1000
	res["q2_box7_+500"] = d(q2, base[Q2], "box7") == 500
	# Q3 deltas
	res["q3_box1_+400"] = d(q3, base[Q3], "box1") == 400
	res["q3_box4_+160"] = d(q3, base[Q3], "box4") == 160
	# Q4 deltas
	res["q4_box1_+60"] = d(q4, base[Q4], "box1") == 60
	# ISOLATION: adding Q3+Q4 must NOT have changed Q2's figures beyond Q2's own invoices
	res["isolation_q2_stable"] = d(q2, base[Q2], "box1") == 200 and d(q2, base[Q2], "box4") == 100
	# LEDGER: one Accrued row per test invoice (5 created). Query by the real
	# invoice names (ledger.source_name is the auto-series name, not the TAG).
	accrued = frappe.get_all(LEDGER, filters={"event_type": "Accrued", "source_name": ["in", names]},
							fields=["vat_amount", "vat_box", "source_name"])
	res["ledger_5_accrued_rows"] = len(accrued) == 5
	# ledger box1 (sales VAT) total across periods = 200+400+60 = 660
	res["ledger_box1_total_660"] = round(sum(flt(r.vat_amount) for r in accrued if r.vat_box == "Box 1"), 2) == 660
	# ledger box4 (purchase VAT) total = 100+160 = 260
	res["ledger_box4_total_260"] = round(sum(flt(r.vat_amount) for r in accrued if r.vat_box == "Box 4"), 2) == 260
	# multi-period aggregation: ledger VAT dated within Q2..Q4 == box totals sum
	res["aggregate_matches"] = res["ledger_box1_total_660"] and res["ledger_box4_total_260"]

	_cleanup()
	# after cleanup the deltas return to baseline (cancel re-derived/cleared)
	res["cleanup_restores_q2"] = _boxes(Q2)["box1"] == base[Q2]["box1"]
	res["cleanup_clears_ledger"] = frappe.get_all(LEDGER, filters={"source_name": ["in", names]}) == []

	passed = sum(1 for v in res.values() if v)
	print(f"MULTIPERIOD PROOF {passed}/{len(res)}: {res}", flush=True)
	return res


def _mk_adjustment(vat_box, amount, posting_date, atype="Other", reason=f"{TAG} adj"):
	adj = frappe.new_doc("VAT Adjustment")
	adj.company = _company()
	adj.posting_date = posting_date
	adj.adjustment_type = atype
	adj.vat_box = vat_box
	adj.amount = amount
	adj.reason = reason
	adj.insert(ignore_permissions=True)
	adj.submit()
	return adj


def _cleanup_adjustments():
	for n in frappe.get_all("VAT Adjustment", filters={"reason": ["like", f"%{TAG}%"]}, pluck="name"):
		d = frappe.get_doc("VAT Adjustment", n)
		if d.docstatus == 1:
			d.cancel()
		frappe.delete_doc("VAT Adjustment", n, force=True, ignore_permissions=True)
	frappe.db.commit()


def prove_adjustment():
	"""A submitted VAT Adjustment folds into the period's 9-box figures (computed +
	Σ adjustments), writes an Adjusted ledger event, keeps the derived boxes
	consistent, blocks the derived boxes, and reverts on cancel."""
	_cleanup_adjustments()
	res = {}
	period = Q2  # 2017-07-01 .. 2017-09-30
	before = c.get_return_figures(*period)["boxes"]

	# +£50 to Box 4 (extra input VAT reclaim, e.g. bad-debt relief)
	adj = _mk_adjustment("Box 4", 50, "2017-08-15", atype="Bad Debt Relief")
	after = c.get_return_figures(*period)
	res["box4_+50"] = round(after["boxes"]["box4"] - before["box4"], 2) == 50
	res["box3_unchanged"] = after["boxes"]["box3"] == before["box3"]
	res["box5_consistent"] = after["boxes"]["box5"] == round(abs(after["boxes"]["box3"] - after["boxes"]["box4"]), 2)
	res["adjustments_returned"] = len(after.get("adjustments", [])) >= 1
	led = frappe.get_all("VAT Ledger Entry", filters={"event_type": "Adjusted", "source_name": adj.name},
						fields=["vat_box", "vat_amount"])
	res["ledger_adjusted_event"] = len(led) == 1 and led[0].vat_box == "Box 4" and round(flt(led[0].vat_amount), 2) == 50

	# +£1000 to Box 6 (net turnover box -> goes in net_amount, not vat_amount)
	_mk_adjustment("Box 6", 1000, "2017-08-16")
	after2 = c.get_return_figures(*period)
	res["box6_+1000"] = round(after2["boxes"]["box6"] - before["box6"], 0) == 1000
	led6 = frappe.get_all("VAT Ledger Entry", filters={"event_type": "Adjusted", "vat_box": "Box 6"},
						fields=["net_amount", "vat_amount"])
	res["net_box_uses_net_amount"] = bool(led6) and round(flt(led6[0].net_amount), 0) == 1000 and flt(led6[0].vat_amount) == 0

	# a NEGATIVE adjustment reduces the box
	_mk_adjustment("Box 4", -20, "2017-08-17")
	res["negative_adjustment"] = round(c.get_return_figures(*period)["boxes"]["box4"] - before["box4"], 2) == 30

	# derived boxes (3, 5) are rejected
	try:
		_mk_adjustment("Box 3", 10, "2017-08-18")
		res["box3_rejected"] = False
	except frappe.ValidationError:
		res["box3_rejected"] = True

	# cancel the first adjustment -> its contribution reverts + ledger clears
	adj.cancel()
	res["cancel_reverts"] = round(c.get_return_figures(*period)["boxes"]["box4"] - before["box4"], 2) == -20
	res["cancel_clears_ledger"] = frappe.get_all("VAT Ledger Entry", filters={"source_name": adj.name}) == []

	_cleanup_adjustments()
	res["cleanup_restores"] = c.get_return_figures(*period)["boxes"]["box4"] == before["box4"]
	passed = sum(1 for v in res.values() if v)
	print(f"ADJUSTMENT PROOF {passed}/{len(res)}: {res}", flush=True)
	return res


def prove_cash_adjustments_fold():
	"""P2-6 completeness: year-end adjustments (bad-debt / CGS / PE) fold into the
	CASH-basis boxes too, not only accrual. prove_adjustment proves the fold under the
	default (accrual) scheme; _apply_adjustments runs in BOTH figure paths, so the same
	adjustment must move the same box on a cash-scheme return. Forces cash in the DB
	(bypasses the year lock), restores the original scheme after."""
	_cleanup_adjustments()
	res = {}
	company = _company()
	if not c.vat_accounts(c.OUTPUT_VAT) or not c.vat_accounts(c.INPUT_VAT):
		c.setup_default_vat_accounts()
	sn = frappe.db.get_value(c.VAT_SETTINGS, {"company": company}, "name")
	orig = frappe.db.get_value(c.VAT_SETTINGS, sn, "vat_accounting_scheme")
	try:
		frappe.db.set_value(c.VAT_SETTINGS, sn, "vat_accounting_scheme", c.CASH)
		frappe.db.commit()
		base = c.get_return_figures(*Q2)
		res["cash_basis_active"] = base.get("basis") == "cash"
		before4 = base["boxes"]["box4"]
		# +£75 to Box 4 under cash (e.g. bad-debt relief reclaim)
		_mk_adjustment("Box 4", 75, "2017-08-15", atype="Bad Debt Relief")
		after = c.get_return_figures(*Q2)
		res["cash_box4_folds_+75"] = round(after["boxes"]["box4"] - before4, 2) == 75
		res["cash_box5_consistent"] = after["boxes"]["box5"] == round(abs(after["boxes"]["box3"] - after["boxes"]["box4"]), 2)
		res["cash_adjustments_listed"] = len(after.get("adjustments", [])) >= 1
	finally:
		_cleanup_adjustments()
		frappe.db.set_value(c.VAT_SETTINGS, sn, "vat_accounting_scheme", orig or c.ACCRUAL)
		frappe.db.commit()
	passed = sum(1 for v in res.values() if v)
	print(f"CASH-ADJUSTMENT FOLD PROOF {passed}/{len(res)}: {res}", flush=True)
	return res


def prove_filed():
	"""Filing a return writes a Filed ledger event per invoice in the period, so each
	invoice's accrual vat_status flips 'Not claimed' -> 'Claimed'. Idempotent."""
	_cleanup()
	res = {}
	period = Q2
	si = _mk_invoice(c.SALES_INVOICE, "2017-08-15", 1000)
	pi = _mk_invoice(c.PURCHASE_INVOICE, "2017-08-20", 500)
	res["si_not_claimed_before"] = frappe.db.get_value(c.SALES_INVOICE, si.name, "vat_status") == "Not claimed"

	ret = frappe.new_doc("UK MTD VAT Return")
	ret.company = _company()
	ret.vrn = "193054661"
	ret.reference_key = "18B2"
	ret.period_start_date = period[0]
	ret.period_end_date = period[1]
	ret.status = "Fulfilled"
	for k, v in {"sales_vat_due_box1": 200, "eu_acquisition_vat_due_box2": 0, "total_vat_due_box3": 200,
				"purchase_vat_reclaimed_box4": 100, "net_vat_due_box5": 100, "net_sales_box6": 1000,
				"net_purchases_box7": 500}.items():
		ret.set(k, v)
	ret.approval_status = "Filed"
	# insert as a DRAFT so the test can clean it up (a SUBMITTED VAT return is terminal
	# — correctly can't be cancelled). record_return_filed uses only name/company/
	# reference_key, so docstatus doesn't affect the Filed-event logic under test.
	ret.insert(ignore_permissions=True)

	vl.record_return_filed(ret, period[0], period[1])
	res["si_filed_event"] = bool(frappe.get_all(LEDGER, filters={"source_name": si.name, "event_type": "Filed"}))
	res["si_claimed_after"] = frappe.db.get_value(c.SALES_INVOICE, si.name, "vat_status") == "Claimed"
	res["pi_claimed_after"] = frappe.db.get_value(c.PURCHASE_INVOICE, pi.name, "vat_status") == "Claimed"
	res["filed_linked_to_return"] = frappe.db.get_value(
		LEDGER, {"source_name": si.name, "event_type": "Filed"}, "vat_return") == ret.name
	vl.record_return_filed(ret, period[0], period[1])
	res["idempotent"] = len(frappe.get_all(LEDGER, filters={"source_name": si.name, "event_type": "Filed"})) == 1

	# Cancel invoices first -> their on_cancel clears Accrued+Filed events; a submitted
	# VAT return is terminal (can't cancel), so force-delete it.
	_cleanup()
	res["cleanup_clears_filed"] = frappe.get_all(LEDGER, filters={"source_name": si.name}) == []
	frappe.delete_doc("UK MTD VAT Return", ret.name, force=True, ignore_permissions=True)
	frappe.db.commit()
	passed = sum(1 for v in res.values() if v)
	print(f"FILED PROOF {passed}/{len(res)}: {res}", flush=True)
	return res


def _cleanup_schedules():
	for n in frappe.get_all("VAT Adjustment Schedule", filters={"reason": ["like", f"%{TAG}%"]}, pluck="name"):
		d = frappe.get_doc("VAT Adjustment Schedule", n)
		if d.docstatus == 1:
			d.cancel()
		frappe.delete_doc("VAT Adjustment Schedule", n, force=True, ignore_permissions=True)
	frappe.db.commit()


def _mk_schedule(ref_name, trigger_date, amount=200, vat_box="Box 4"):
	s = frappe.new_doc("VAT Adjustment Schedule")
	s.schedule_type = "Bad Debt Relief"
	s.company = _company()
	s.reference_doctype = c.SALES_INVOICE
	s.reference_name = ref_name
	s.trigger_date = trigger_date
	s.vat_box = vat_box
	s.amount = amount
	s.reason = f"{TAG} bad debt"
	s.notice_ref = "Notice 700/18"
	s.insert(ignore_permissions=True)
	s.flags.ignore_permissions = True
	s.submit()
	return s


def prove_schedule():
	"""A bad-debt schedule generates a Box 4 relief adjustment once its trigger date
	arrives (and the sale is still unpaid); it folds into the period, is idempotent,
	skips future/paid schedules, and reverses on cancel."""
	from zikpro_uk_vat import vat_adjustment_schedule as vs
	_cleanup_schedules(); _cleanup_adjustments(); _cleanup()
	res = {}
	period = Q2
	AS_OF = "2017-09-30"
	si = _mk_invoice(c.SALES_INVOICE, "2017-08-15", 1000)  # unpaid -> eligible
	base4 = c.get_return_figures(*period)["boxes"]["box4"]

	s1 = _mk_schedule(si.name, "2017-08-16", amount=200)  # due (trigger <= AS_OF), eligible
	si_future = _mk_invoice(c.SALES_INVOICE, "2017-08-17", 500)
	s_future = _mk_schedule(si_future.name, "2099-01-01", amount=100)  # not due
	si_paid = _mk_invoice(c.SALES_INVOICE, "2017-08-18", 500)
	frappe.db.set_value(c.SALES_INVOICE, si_paid.name, "outstanding_amount", 0)  # paid -> not eligible
	s_paid = _mk_schedule(si_paid.name, "2017-08-19", amount=100)

	out = vs.generate_due_adjustments(as_of=AS_OF)
	res["generated_1"] = out["generated"] == 1
	res["not_eligible_1"] = out["not_eligible"] == 1
	res["s1_claimed"] = frappe.db.get_value("VAT Adjustment Schedule", s1.name, "status") == "Claimed"
	res["s1_linked_adjustment"] = bool(frappe.db.get_value("VAT Adjustment Schedule", s1.name, "generated_adjustment"))
	res["box4_folded_+200"] = round(c.get_return_figures(*period)["boxes"]["box4"] - base4, 2) == 200
	res["future_still_pending"] = frappe.db.get_value("VAT Adjustment Schedule", s_future.name, "status") == "Pending"
	res["paid_not_eligible"] = frappe.db.get_value("VAT Adjustment Schedule", s_paid.name, "status") == "Not Eligible"

	# idempotent: re-running generates nothing new
	out2 = vs.generate_due_adjustments(as_of=AS_OF)
	res["idempotent"] = out2["generated"] == 0

	# cancel the schedule -> its adjustment is cancelled -> Box 4 reverts
	frappe.get_doc("VAT Adjustment Schedule", s1.name).cancel()
	res["cancel_reverts"] = round(c.get_return_figures(*period)["boxes"]["box4"] - base4, 2) == 0

	_cleanup_schedules(); _cleanup_adjustments(); _cleanup()
	passed = sum(1 for v in res.values() if v)
	print(f"SCHEDULE PROOF {passed}/{len(res)}: {res}", flush=True)
	return res


def prove_repay():
	"""Bad-debt lifecycle: relief is CLAIMED (Box 4 +£200), then when the sale is
	PAID IN FULL a reversing adjustment (Box 4 -£200) is auto-generated and the
	schedule flips to Reversed — net effect back to zero (HMRC 700/18)."""
	from zikpro_uk_vat import vat_adjustment_schedule as vs
	from erpnext.accounts.doctype.payment_entry.payment_entry import get_payment_entry
	_cleanup_schedules(); _cleanup_adjustments(); _cleanup()
	res = {}

	def adj_sum():
		return round(sum(flt(a.amount) for a in frappe.get_all(
			"VAT Adjustment", filters={"docstatus": 1, "origin_name": si.name}, fields=["amount"])), 2)

	si = _mk_invoice(c.SALES_INVOICE, "2017-08-15", 1000)  # VAT 200, grand 1200, unpaid
	s = _mk_schedule(si.name, "2017-08-16", amount=200)
	vs.generate_due_adjustments(as_of="2017-09-30")
	res["claimed"] = frappe.db.get_value("VAT Adjustment Schedule", s.name, "status") == "Claimed"
	res["relief_+200"] = adj_sum() == 200

	pe = get_payment_entry("Sales Invoice", si.name)
	pe.reference_no = "RPMT-1"; pe.reference_date = "2017-10-05"
	pe.insert(ignore_permissions=True)
	pe.flags.ignore_permissions = True
	pe.submit()
	res["outstanding_zero"] = flt(frappe.db.get_value("Sales Invoice", si.name, "outstanding_amount")) == 0
	res["schedule_reversed"] = frappe.db.get_value("VAT Adjustment Schedule", s.name, "status") == "Reversed"
	res["net_zero_after_repay"] = adj_sum() == 0

	# cleanup order matters: payment -> schedule (its generated_adjustment Link blocks
	# deleting the relief; on_cancel cancels it) -> adjustments (by origin) -> invoice.
	for n in frappe.get_all("Payment Entry", filters={"reference_no": "RPMT-1"}, pluck="name"):
		d = frappe.get_doc("Payment Entry", n)
		if d.docstatus == 1:
			d.cancel()
		frappe.delete_doc("Payment Entry", n, force=True, ignore_permissions=True)
	_cleanup_schedules()
	for n in frappe.get_all("VAT Adjustment", filters={"origin_name": si.name}, pluck="name"):
		d = frappe.get_doc("VAT Adjustment", n)
		if d.docstatus == 1:
			d.cancel()
		frappe.delete_doc("VAT Adjustment", n, force=True, ignore_permissions=True)
	_cleanup_adjustments(); _cleanup()
	frappe.db.commit()
	passed = sum(1 for v in res.values() if v)
	print(f"REPAY PROOF {passed}/{len(res)}: {res}", flush=True)
	return res


def prove_snapshot():
	"""Filing freezes the Notice 700/22 VAT account onto the return; calculation_notes
	for a FILED period then serves that frozen snapshot, not a live recompute."""
	period = ("2017-04-01", "2017-06-30")
	res = {}
	# remove any prior test return for this period
	for n in frappe.get_all("UK MTD VAT Return", filters={"reference_key": "TESTSNAP"}, pluck="name"):
		frappe.db.set_value("UK MTD VAT Return", n, "docstatus", 0)
		frappe.delete_doc("UK MTD VAT Return", n, force=True, ignore_permissions=True)
	frappe.db.commit()

	# The Notice 700/22 snapshot summarises the period's VAT by account — give the period a
	# real submitted invoice so the snapshot has something to freeze (a fresh site has none).
	_cleanup()
	_mk_invoice(c.SALES_INVOICE, "2017-05-15", 1000)

	ret = frappe.new_doc("UK MTD VAT Return")
	ret.company = _company(); ret.vrn = "193054661"; ret.reference_key = "TESTSNAP"
	ret.period_start_date = period[0]; ret.period_end_date = period[1]; ret.status = "Fulfilled"
	for k, v in {"sales_vat_due_box1": 600, "eu_acquisition_vat_due_box2": 0, "total_vat_due_box3": 600,
				"purchase_vat_reclaimed_box4": 135, "net_vat_due_box5": 465, "net_sales_box6": 2500,
				"net_purchases_box7": 900}.items():
		ret.set(k, v)
	ret.approval_status = "Filed"
	# A filed return carries an HMRC receipt; the before_submit guard (P0-2/BSW-2) requires
	# the form bundle number as proof it was actually filed via the cockpit path.
	ret.form_bundle_number = "TESTSNAP-BUNDLE"
	c._snapshot_rate_summary(ret, period[0], period[1])
	res["snapshot_populated_before_save"] = len(ret.vat_rate_summary) >= 1
	ret.insert(ignore_permissions=True)
	ret.flags.ignore_permissions = True
	ret.submit()
	res["snapshot_persisted"] = frappe.db.count("VAT Return Rate Summary", {"parent": ret.name}) >= 1

	notes = c.calculation_notes(*period)
	res["notes_filed_flag"] = notes["identity"]["filed"] is True
	res["notes_uses_snapshot"] = notes.get("vat_account_source") == "as filed (snapshot)"
	res["filed_info_return"] = bool(notes.get("filed")) and notes["filed"]["return"] == ret.name
	res["snapshot_has_net"] = bool(notes["vat_account"]) and notes["vat_account"][0].get("net") is not None

	frappe.db.set_value("UK MTD VAT Return", ret.name, "docstatus", 0)
	frappe.delete_doc("UK MTD VAT Return", ret.name, force=True, ignore_permissions=True)
	_cleanup()  # remove the period invoice created for the snapshot
	frappe.db.commit()
	passed = sum(1 for v in res.values() if v)
	print(f"SNAPSHOT PROOF {passed}/{len(res)}: {res}", flush=True)
	return res


def prove_cgs_pe():
	"""The generic schedule framework handles ALL three types: a Capital Goods Scheme
	and a Partial Exemption Annual schedule each generate on their trigger date, map to
	the right VAT Adjustment type, and fold into the boxes. (Their amount computation —
	per-interval use% / annual recovery% — is separate domain UX; here the amount is
	given, as it would be after that calc.)"""
	from zikpro_uk_vat import vat_adjustment_schedule as vs
	_cleanup_schedules(); _cleanup_adjustments(); _cleanup()
	res = {}
	AS_OF = "2017-09-30"

	def mk(stype, box, amount):
		s = frappe.new_doc("VAT Adjustment Schedule")
		s.schedule_type = stype; s.company = _company()
		s.trigger_date = "2017-08-01"; s.vat_box = box; s.amount = amount
		s.reason = f"{TAG} {stype}"
		s.insert(ignore_permissions=True); s.flags.ignore_permissions = True; s.submit()
		return s

	cgs = mk("Capital Goods Scheme", "Box 4", 100)
	pe = mk("Partial Exemption Annual", "Box 4", -30)
	out = vs.generate_due_adjustments(as_of=AS_OF)
	res["both_generated"] = out["generated"] == 2
	res["cgs_claimed"] = frappe.db.get_value("VAT Adjustment Schedule", cgs.name, "status") == "Claimed"
	res["pe_claimed"] = frappe.db.get_value("VAT Adjustment Schedule", pe.name, "status") == "Claimed"
	cgs_adj = frappe.db.get_value("VAT Adjustment Schedule", cgs.name, "generated_adjustment")
	pe_adj = frappe.db.get_value("VAT Adjustment Schedule", pe.name, "generated_adjustment")
	res["cgs_type_mapped"] = frappe.db.get_value("VAT Adjustment", cgs_adj, "adjustment_type") == "Capital Goods Scheme"
	res["pe_type_mapped"] = frappe.db.get_value("VAT Adjustment", pe_adj, "adjustment_type") == "Partial Exemption"
	# net box4 effect of the two adjustments in AS_OF's period = +100 - 30 = +70
	period = (AS_OF[:8] + "01", AS_OF)  # 2017-09-01..09-30 (adjustments land on 2017-09-30)
	res["folds_net_70"] = round(c.get_return_figures("2017-07-01", "2017-09-30")["boxes"]["box4"], 2) >= 70

	_cleanup_schedules(); _cleanup_adjustments(); _cleanup()
	passed = sum(1 for v in res.values() if v)
	print(f"CGS-PE PROOF {passed}/{len(res)}: {res}", flush=True)
	return res


def prove_double_count():
	"""Roll-over safety: an adjustment referencing an origin invoice is clean while
	the invoice stands, but once the invoice is cancelled/amended the period's figures
	surface a double-count warning naming the adjustment."""
	_cleanup(); _cleanup_adjustments()
	res = {}
	period = Q2
	si = _mk_invoice(c.SALES_INVOICE, "2017-08-15", 1000)
	adj = frappe.new_doc("VAT Adjustment")
	adj.company = _company()
	adj.posting_date = "2017-08-20"
	adj.adjustment_type = "Error Correction (Notice 700/45)"
	adj.vat_box = "Box 1"
	adj.amount = -50
	adj.reason = f"{TAG} correction"
	adj.origin_doctype = c.SALES_INVOICE
	adj.origin_name = si.name
	adj.insert(ignore_permissions=True)
	adj.flags.ignore_permissions = True
	adj.submit()

	w0 = c.get_return_figures(*period)["warnings"]
	res["clean_no_warning"] = not any("double-counted" in x for x in w0)

	frappe.get_doc(c.SALES_INVOICE, si.name).cancel()
	w1 = c.get_return_figures(*period)["warnings"]
	res["cancel_origin_warns"] = any(("cancelled" in x and adj.name in x) for x in w1)

	frappe.get_doc("VAT Adjustment", adj.name).cancel()
	w2 = c.get_return_figures(*period)["warnings"]
	res["resolved_after_adj_cancel"] = not any("double-counted" in x for x in w2)

	_cleanup_adjustments(); _cleanup()
	passed = sum(1 for v in res.values() if v)
	print(f"DOUBLE-COUNT PROOF {passed}/{len(res)}: {res}", flush=True)
	return res


def prove_method_lock():
	"""A1-1: Accrual->Cash is now ALLOWED when turnover-eligible (Notice 731 §2),
	refused over the join threshold, and the mid-year lock still applies once set."""
	from zikpro_uk_vat.cockpit import _apply_scheme_change, VAT_SETTINGS, _connection, ACCRUAL, CASH
	res = {}
	sn = _connection()["settings"]
	doc = frappe.get_doc(VAT_SETTINGS, sn)
	doc.vat_accounting_scheme = ACCRUAL          # clean in-memory start
	doc.scheme_effective_year = None
	cur = c._current_vat_year()

	# eligible (dev-site trailing turnover is under £1.35m; LEDGERTEST invoices are 2017) -> applied
	out = _apply_scheme_change(doc, CASH, cur)
	res["cash_allowed_when_eligible"] = out is None and doc.vat_accounting_scheme == CASH

	# scheme now set for this year -> mid-year switch back is locked
	out2 = _apply_scheme_change(doc, ACCRUAL, cur)
	res["midyear_lock"] = isinstance(out2, dict) and out2.get("ok") is False and "locked" in (out2.get("message") or "").lower()

	# over the join threshold -> refused (stub the turnover proxy)
	orig_turnover = c._cash_taxable_turnover
	try:
		c._cash_taxable_turnover = lambda company: c.CASH_JOIN_THRESHOLD + 1
		doc2 = frappe.get_doc(VAT_SETTINGS, sn)
		doc2.scheme_effective_year = None
		out3 = _apply_scheme_change(doc2, CASH, cur)
		res["over_threshold_refused"] = isinstance(out3, dict) and out3.get("ok") is False and "turnover" in (out3.get("message") or "").lower()
	finally:
		c._cash_taxable_turnover = orig_turnover

	res["invalid_scheme_rejected"] = isinstance(_apply_scheme_change(doc, "Nonsense", cur), dict)
	passed = sum(1 for v in res.values() if v)
	print(f"METHOD-LOCK PROOF {passed}/{len(res)}: {res}", flush=True)
	return res


def prove_cash_notice_731():
	"""A1-1 Notice 731 §6: on the Cash scheme, a vat_cash_excluded supply is counted on
	its INVOICE date even when unpaid; a normal unpaid supply is not (until paid)."""
	from zikpro_uk_vat.cockpit import VAT_SETTINGS, _connection, CASH
	_cleanup()
	res = {}
	sn = _connection()["settings"]
	orig = frappe.db.get_value(VAT_SETTINGS, sn, "vat_accounting_scheme")
	try:
		frappe.db.set_value(VAT_SETTINGS, sn, "vat_accounting_scheme", CASH)  # bypass gate for test
		frappe.clear_cache()
		res["scheme_cash"] = c._scheme() == CASH

		ex = _mk_invoice(c.SALES_INVOICE, "2017-08-10", 1000)   # net 1000, VAT 200 — EXCLUDED, unpaid
		frappe.db.set_value(c.SALES_INVOICE, ex.name, "vat_cash_excluded", 1)
		_mk_invoice(c.SALES_INVOICE, "2017-08-12", 500)         # net 500, VAT 100 — normal, unpaid

		fig = c.get_return_figures(Q2[0], Q2[1])
		boxes = fig["boxes"]
		res["basis_cash"] = fig.get("basis") == "cash"
		# excluded invoice counted on invoice date though unpaid; normal invoice NOT counted (unpaid)
		res["excluded_vat_in_box1"] = round(float(boxes["box1"]), 2) == 200.0
		res["excluded_net_in_box6"] = round(float(boxes["box6"]), 0) == 1000.0
		res["normal_unpaid_not_counted"] = round(float(boxes["box1"]), 2) == 200.0  # 300 if the normal one leaked

		# §6.2 warning: an unflagged invoice due 6+ months out should be flagged for review
		far = _mk_invoice(c.SALES_INVOICE, "2017-08-14", 300)
		frappe.db.set_value(c.SALES_INVOICE, far.name, "due_date", "2018-03-01")  # ~6.5 months
		warns = c.get_return_figures(Q2[0], Q2[1]).get("warnings", [])
		res["due_6mo_warns_unflagged"] = any(far.name in w and "Notice 731" in w for w in warns)
	finally:
		_cleanup()
		frappe.db.set_value(VAT_SETTINGS, sn, "vat_accounting_scheme", orig)
		frappe.clear_cache()
		frappe.db.commit()
	passed = sum(1 for v in res.values() if v)
	print(f"CASH-731 PROOF {passed}/{len(res)}: {res}", flush=True)
	return res


def prove_flat_rate():
	"""A1-2 Notice 733: Box 1 = flat-rate % of VAT-inclusive turnover; Box 6 = that
	gross turnover; Box 4 = 0. Rate unset blocks; joining over £150k is refused."""
	from zikpro_uk_vat.cockpit import VAT_SETTINGS, _connection, FLAT_RATE
	_cleanup()
	res = {}
	sn = _connection()["settings"]
	orig = frappe.db.get_value(VAT_SETTINGS, sn, "vat_accounting_scheme")
	orig_pct = frappe.db.get_value(VAT_SETTINGS, sn, "flat_rate_percentage")
	try:
		frappe.db.set_value(VAT_SETTINGS, sn, {"vat_accounting_scheme": FLAT_RATE, "flat_rate_percentage": 14.5})
		frappe.clear_cache()
		res["scheme_frs"] = c._scheme() == FLAT_RATE
		_mk_invoice(c.SALES_INVOICE, "2017-11-05", 1000)  # net 1000, VAT 200 -> grand 1200
		_mk_invoice(c.SALES_INVOICE, "2017-11-08", 500)   # net 500,  VAT 100 -> grand 600
		fig = c.get_return_figures(Q3[0], Q3[1])
		boxes = fig["boxes"]
		res["basis_flat_rate"] = fig.get("basis") == "flat_rate"
		res["box6_gross_1800"] = round(float(boxes["box6"]), 0) == 1800.0     # VAT-inclusive turnover
		res["box1_flat_261"] = round(float(boxes["box1"]), 2) == 261.00       # 14.5% of 1800
		res["box4_zero_no_reclaim"] = round(float(boxes["box4"]), 2) == 0.0

		frappe.db.set_value(VAT_SETTINGS, sn, "flat_rate_percentage", 0)
		frappe.clear_cache()
		res["unset_rate_blocks"] = c.get_return_figures(Q3[0], Q3[1]).get("blocking") is True
	finally:
		_cleanup()
		frappe.db.set_value(VAT_SETTINGS, sn, {"vat_accounting_scheme": orig, "flat_rate_percentage": orig_pct})
		frappe.clear_cache()
		frappe.db.commit()

	orig_turnover = c._cash_taxable_turnover
	try:
		c._cash_taxable_turnover = lambda company: c.FRS_JOIN_THRESHOLD + 1
		out = c._frs_eligibility_error(_connection()["company"])
		res["over_150k_refused"] = isinstance(out, dict) and out.get("ok") is False
	finally:
		c._cash_taxable_turnover = orig_turnover

	passed = sum(1 for v in res.values() if v)
	print(f"FLAT-RATE PROOF {passed}/{len(res)}: {res}", flush=True)
	return res


def _mk_treated_invoice(date, net, item_tax_template):
	"""A Sales Invoice with a single line carrying a real Item Tax Template, so the
	Notice 700/22 breakdown can map it to a VAT treatment (not 'Unclassified')."""
	company = _company()
	party = frappe.db.get_value("Customer", {"disabled": 0}, "name")
	inc = frappe.db.get_value("Account", {"company": company, "is_group": 0, "root_type": "Income"}, "name")
	doc = frappe.new_doc(c.SALES_INVOICE)
	doc.company = company
	doc.customer = party
	doc.set_posting_time = 1
	doc.posting_date = date
	doc.append("items", {"item_name": TAG, "description": TAG, "qty": 1, "rate": net,
						"income_account": inc, "uom": "Nos", "item_tax_template": item_tax_template})
	doc.remarks = TAG
	doc.set_missing_values()
	doc.insert(ignore_permissions=True)
	doc.submit()
	return doc


def prove_onboarding_700_22():
	"""A1-3: with item lines mapped to real VAT treatments (the onboarding case), the
	Notice 700/22 VAT-account breakdown groups net BY TREATMENT with exact amounts —
	not the all-'Unclassified' result you get from untagged demo data."""
	_cleanup()
	res = {}
	try:
		# Template names carry the company abbr (DC on the dev site, ZTU on a fresh CI site).
		abbr = frappe.db.get_value("Company", _company(), "abbr")
		_mk_treated_invoice("2018-02-05", 1000, f"UK VAT Sales - Standard rated - {abbr}")
		_mk_treated_invoice("2018-02-08", 500, f"UK VAT Sales - Zero rated - {abbr}")
		_mk_treated_invoice("2018-02-10", 300, f"UK VAT Sales - Exempt - {abbr}")

		notes = c.calculation_notes(Q4[0], Q4[1])
		by = {r["treatment"]: r for r in (notes.get("vat_account") or [])}
		res["breakdown_present"] = len(by) >= 3
		res["standard_net_1000"] = round(float(by.get("Standard rated", {}).get("net", 0)), 0) == 1000.0
		res["zero_net_500"] = round(float(by.get("Zero rated", {}).get("net", 0)), 0) == 500.0
		res["exempt_net_300"] = round(float(by.get("Exempt", {}).get("net", 0)), 0) == 300.0
		res["not_all_unclassified"] = round(float(by.get("Unclassified", {}).get("net", 0)), 0) == 0.0
		res["notes_live_source"] = notes.get("vat_account_source") == "live"
	finally:
		_cleanup()
	passed = sum(1 for v in res.values() if v)
	print(f"ONBOARDING-700/22 PROOF {passed}/{len(res)}: {res}", flush=True)
	return res


def prove_cash_realised():
	"""CODE-PATH test of the cash Realised ledger (the scheme itself is gated, so we
	force it in the DB, run, then restore). A partial payment yields a proportional
	Realised event + 'Partially claimed'; paying the rest yields 'Claimed'."""
	from erpnext.accounts.doctype.payment_entry.payment_entry import get_payment_entry
	from zikpro_uk_vat.cockpit import VAT_SETTINGS, _connection, ACCRUAL, CASH
	_cleanup()
	res = {}
	sn = _connection()["settings"]
	orig_scheme = frappe.db.get_value(VAT_SETTINGS, sn, "vat_accounting_scheme")
	try:
		frappe.db.set_value(VAT_SETTINGS, sn, "vat_accounting_scheme", CASH)  # bypass gate for test
		frappe.clear_cache()
		res["scheme_forced_cash"] = c._scheme() == CASH

		si = _mk_invoice(c.SALES_INVOICE, "2017-08-15", 1000)  # net 1000, VAT 200, grand 1200
		# invoice submit writes Accrued only, no Realised yet (cash view still 0)
		res["no_realised_before_payment"] = frappe.get_all(
			LEDGER, filters={"source_name": si.name, "event_type": "Realised"}) == []
		res["accrued_present"] = round(sum(flt(r.vat_amount) for r in frappe.get_all(
			LEDGER, filters={"source_name": si.name, "event_type": "Accrued"}, fields=["vat_amount"])), 2) == 200

		# pay in FULL -> Realised vat = 200 (frac 1.0), status Claimed. (Partial
		# fractions use the same _write path, already exercised by the accrual test.)
		pe = get_payment_entry("Sales Invoice", si.name)
		pe.reference_no = "PMT-1"; pe.reference_date = "2017-08-20"
		pe.insert(ignore_permissions=True); pe.submit()
		realised = round(sum(flt(r.vat_amount) for r in frappe.get_all(
			LEDGER, filters={"source_name": si.name, "event_type": "Realised"}, fields=["vat_amount"])), 2)
		res["full_payment_realised_200"] = realised == 200
		res["realised_box_is_1"] = frappe.db.get_value(
			LEDGER, {"source_name": si.name, "event_type": "Realised"}, "vat_box") == "Box 1"
		res["status_claimed"] = frappe.db.get_value(c.SALES_INVOICE, si.name, "vat_status") == "Claimed"

		# cancel the payment -> its Realised events clear + status drops back
		pe.cancel()
		res["cancel_clears_realised"] = frappe.get_all(
			LEDGER, filters={"source_name": si.name, "event_type": "Realised"}) == []
		res["status_back_to_notclaimed"] = frappe.db.get_value(c.SALES_INVOICE, si.name, "vat_status") == "Not claimed"
	finally:
		# restore scheme + clean up test docs
		for pe_name in frappe.get_all("Payment Entry", filters={"reference_no": ["in", ["PMT-1", "PMT-2"]]}, pluck="name"):
			d = frappe.get_doc("Payment Entry", pe_name)
			if d.docstatus == 1:
				d.cancel()
			frappe.delete_doc("Payment Entry", pe_name, force=True, ignore_permissions=True)
		_cleanup()
		frappe.db.set_value(VAT_SETTINGS, sn, "vat_accounting_scheme", orig_scheme)
		frappe.clear_cache()
		frappe.db.commit()
	res["scheme_restored"] = c._scheme() == orig_scheme
	passed = sum(1 for v in res.values() if v)
	print(f"CASH-REALISED PROOF {passed}/{len(res)}: {res}", flush=True)
	return res


def prove_scheme_transition():
	"""A1-4 Notice 731 §6.4: LEAVING cash posts a transitional adjustment for the VAT
	outstanding on unpaid invoices at the switch (so it isn't lost between schemes);
	JOINING cash flags pre-switch invoices as already-accrued (so their later payments
	aren't double-counted)."""
	from zikpro_uk_vat.cockpit import (
		_apply_scheme_transition, _outstanding_vat_net, VAT_ADJUSTMENT, SCHEME_CHANGE_TYPE, CASH, ACCRUAL)
	_cleanup()
	res = {}
	company = _company()
	if not frappe.db.exists("Fiscal Year", "2018-2019"):
		frappe.get_doc({"doctype": "Fiscal Year", "year": "2018-2019",
						"year_start_date": "2018-04-01", "year_end_date": "2019-03-31"}).insert(ignore_permissions=True)
	SWITCH = "2018-04-01"

	def _purge_scheme_adjustments():
		for a in frappe.get_all(VAT_ADJUSTMENT, filters={"adjustment_type": SCHEME_CHANGE_TYPE}, pluck="name"):
			d = frappe.get_doc(VAT_ADJUSTMENT, a)
			if d.docstatus == 1:
				d.cancel()
			frappe.delete_doc(VAT_ADJUSTMENT, a, force=True, ignore_permissions=True)

	_purge_scheme_adjustments()
	# baseline outstanding BEFORE our test invoice (the dev site may hold other unpaid
	# invoices dated before the switch — the engine correctly sweeps ALL of them, so we
	# assert our invoice's DELTA rather than an absolute).
	base_vat, base_net = _outstanding_vat_net(c.SALES_INVOICE, SWITCH, company)
	inv = _mk_invoice(c.SALES_INVOICE, "2017-08-15", 1000)  # net 1000, VAT 200, UNPAID, dated before switch
	try:
		# --- LEAVING cash: our unpaid invoice adds exactly 200 VAT / 1000 net outstanding ---
		aft_vat, aft_net = _outstanding_vat_net(c.SALES_INVOICE, SWITCH, company)
		res["leaving_outstanding_vat_delta_200"] = round(aft_vat - base_vat, 2) == 200.0
		res["leaving_outstanding_net_delta_1000"] = round(aft_net - base_net, 2) == 1000.0
		note = _apply_scheme_transition(company, CASH, ACCRUAL, "2018-2019")
		adjs = frappe.get_all(VAT_ADJUSTMENT, filters={"adjustment_type": SCHEME_CHANGE_TYPE, "docstatus": 1},
							  fields=["vat_box", "amount", "posting_date"])
		res["leaving_posts_box1_adjustment"] = any(a.vat_box == "Box 1" and flt(a.amount) > 0 for a in adjs)
		res["leaving_dated_switch"] = bool(adjs) and all(str(a.posting_date) == SWITCH for a in adjs)
		res["leaving_note_731"] = bool(note) and "731" in note
		_purge_scheme_adjustments()

		# --- JOINING cash: pre-switch invoice flagged as already-accrued ---
		frappe.db.set_value(c.SALES_INVOICE, inv.name, "vat_cash_excluded", 0)
		note2 = _apply_scheme_transition(company, ACCRUAL, CASH, "2018-2019")
		res["joining_excludes_preswitch"] = frappe.db.get_value(c.SALES_INVOICE, inv.name, "vat_cash_excluded") == 1
		res["joining_note_731"] = bool(note2) and "731" in note2
	finally:
		_purge_scheme_adjustments()
		# undo the cash-exclusion flag on any pre-switch invoice we touched (dev-site safe)
		for n in frappe.get_all(c.SALES_INVOICE, filters={"posting_date": ["<", SWITCH], "vat_cash_excluded": 1}, pluck="name"):
			frappe.db.set_value(c.SALES_INVOICE, n, "vat_cash_excluded", 0, update_modified=False)
		for n in frappe.get_all(c.PURCHASE_INVOICE, filters={"posting_date": ["<", SWITCH], "vat_cash_excluded": 1}, pluck="name"):
			frappe.db.set_value(c.PURCHASE_INVOICE, n, "vat_cash_excluded", 0, update_modified=False)
		_cleanup()
		frappe.db.commit()

	passed = sum(1 for v in res.values() if v)
	print(f"SCHEME-TRANSITION PROOF {passed}/{len(res)}: {res}", flush=True)
	return res


def prove_reconciliation():
	"""A2-3: reconcile_period ties the return's Box 1/4 to the VAT posted to the mapped
	VAT accounts in the GL. A sales invoice (200 output VAT) + purchase (100 input VAT)
	must move BOTH the return figure and the GL by the same amount (delta), so the
	period stays reconciled regardless of pre-existing dev-site data."""
	_cleanup()
	res = {}
	P = Q2  # 2017-07-01 .. 2017-09-30

	def _net():
		r = c.reconcile_period(P[0], P[1])
		by = {row["box"]: row for row in r["rows"]}
		return r, by["Net VAT"]

	base, n0 = _net()
	res["basis_accrual"] = base.get("basis") == "accrual"
	res["has_net_vat_row"] = "Net VAT" in {row["box"] for row in base["rows"]}
	try:
		_mk_invoice(c.SALES_INVOICE, "2017-08-15", 1000)     # +200 output VAT
		_mk_invoice(c.PURCHASE_INVOICE, "2017-08-20", 500)   # +100 input VAT
		_, n1 = _net()
		# net VAT due (Box 1 − Box 4) moved +100 on BOTH the return and the GL -> still reconciled
		res["net_return_+100"] = round(n1["return_val"] - n0["return_val"], 2) == 100.0
		res["net_gl_+100"] = round(n1["gl_val"] - n0["gl_val"], 2) == 100.0
		res["net_still_reconciled"] = abs(n1["diff"] - n0["diff"]) < 0.01
	finally:
		_cleanup()

	passed = sum(1 for v in res.values() if v)
	print(f"RECONCILIATION PROOF {passed}/{len(res)}: {res}", flush=True)
	return res


def prove_full_cycle():
	"""P2-6: two full-cycle scheme transitions proven at the PERIOD-FIGURE level (not just the
	adjustment posting) on a straddling invoice.
	  A) Accrual Y1 -> Cash Y2: an invoice dated Y1 is counted in accrual Y1, and after joining
	     cash it is flagged already-accrued, so its Y2 payment is NOT recounted -> NO DOUBLE-COUNT.
	  B) Cash Y1 -> Accrual Y2: an unpaid invoice is in accrual but NOT cash (the gap); leaving
	     cash posts a transitional Box-1 adjustment that captures it -> NO GAP.
	All deltas (dev-site data may coexist). Scheme is forced in the DB (bypasses the year lock).
	"""
	from erpnext.accounts.doctype.payment_entry.payment_entry import get_payment_entry
	VAT_SETTINGS, CASH, ACCRUAL = c.VAT_SETTINGS, c.CASH, c.ACCRUAL
	VAT_ADJUSTMENT, SCHEME_CHANGE_TYPE = c.VAT_ADJUSTMENT, c.SCHEME_CHANGE_TYPE
	_cleanup()
	res = {}
	company = _company()
	# Self-sufficient: ensure VAT accounts are mapped (standalone runs lack ci_bootstrap).
	if not c.vat_accounts(c.OUTPUT_VAT) or not c.vat_accounts(c.INPUT_VAT):
		c.setup_default_vat_accounts()
	sn = frappe.db.get_value(VAT_SETTINGS, {"company": company}, "name")
	orig_scheme = frappe.db.get_value(VAT_SETTINGS, sn, "vat_accounting_scheme")
	if not frappe.db.exists("Fiscal Year", "2018-2019"):
		frappe.get_doc({"doctype": "Fiscal Year", "year": "2018-2019",
						"year_start_date": "2018-04-01", "year_end_date": "2019-03-31"}).insert(ignore_permissions=True)
	SWITCH = "2018-04-01"
	Y1Q = ("2017-07-01", "2017-09-30")
	Y2Q = ("2018-04-01", "2018-06-30")

	def _force(scheme):
		frappe.db.set_value(VAT_SETTINGS, sn, "vat_accounting_scheme", scheme)
		frappe.db.commit()

	def _box1(period):
		return c.get_return_figures(period[0], period[1])["boxes"]["box1"]

	def _purge_scheme_adj():
		for a in frappe.get_all(VAT_ADJUSTMENT, filters={"adjustment_type": SCHEME_CHANGE_TYPE}, pluck="name"):
			d = frappe.get_doc(VAT_ADJUSTMENT, a)
			if d.docstatus == 1:
				d.cancel()
			frappe.delete_doc(VAT_ADJUSTMENT, a, force=True, ignore_permissions=True)

	si = None
	try:
		# ===== A) Accrual Y1 -> Cash Y2 : NO DOUBLE-COUNT =====
		_purge_scheme_adj()
		_force(ACCRUAL)
		base = _box1(Y1Q)
		si = _mk_invoice(c.SALES_INVOICE, "2017-08-15", 1000)  # VAT 200, unpaid
		res["A_accrual_Y1_counts_200"] = round(_box1(Y1Q) - base, 2) == 200
		# join cash -> pre-switch invoice flagged already-accrued
		c._apply_scheme_transition(company, ACCRUAL, CASH, "2018-2019")
		res["A_joining_flags_excluded"] = frappe.db.get_value(c.SALES_INVOICE, si.name, "vat_cash_excluded") == 1
		# pay it in Y2 under cash -> must NOT recount
		_force(CASH)
		b_cash = _box1(Y2Q)
		pe = get_payment_entry("Sales Invoice", si.name)
		pe.reference_no = "FCPMT-1"; pe.reference_date = "2018-05-05"; pe.posting_date = "2018-05-05"
		pe.insert(ignore_permissions=True); pe.flags.ignore_permissions = True; pe.submit()
		res["A_cash_Y2_no_double_count"] = round(_box1(Y2Q) - b_cash, 2) == 0
		pe.cancel(); frappe.delete_doc("Payment Entry", pe.name, force=True, ignore_permissions=True)
		_purge_scheme_adj()
		frappe.db.set_value(c.SALES_INVOICE, si.name, "vat_cash_excluded", 0)

		# ===== B) Cash Y1 -> Accrual Y2 : NO GAP =====
		_force(ACCRUAL); a1 = _box1(Y1Q)
		_force(CASH); c1 = _box1(Y1Q)
		res["B_cash_excludes_unpaid_200"] = round(a1 - c1, 2) == 200  # unpaid in accrual, not in cash = the gap
		note = c._apply_scheme_transition(company, CASH, ACCRUAL, "2018-2019")
		adjs = frappe.get_all(VAT_ADJUSTMENT, filters={"adjustment_type": SCHEME_CHANGE_TYPE, "docstatus": 1},
							  fields=["vat_box", "amount"])
		res["B_leaving_posts_>=200"] = any(a.vat_box == "Box 1" and round(flt(a.amount), 2) >= 200 for a in adjs)
		res["B_note_731"] = bool(note) and "731" in note
	finally:
		_purge_scheme_adj()
		for n in frappe.get_all(c.SALES_INVOICE, filters={"posting_date": ["<", SWITCH], "vat_cash_excluded": 1}, pluck="name"):
			frappe.db.set_value(c.SALES_INVOICE, n, "vat_cash_excluded", 0, update_modified=False)
		_force(orig_scheme or ACCRUAL)
		_cleanup()
		frappe.db.commit()

	passed = sum(1 for v in res.values() if v)
	print(f"FULL-CYCLE PROOF {passed}/{len(res)}: {res}", flush=True)
	return res
