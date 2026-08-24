"""VAT Ledger — writes the immutable per-event VAT sub-ledger from document hooks.

Design: see [[project-uk-mtd-vat-ledger-design]]. The ledger is the single
system-of-record; the source invoice/payment DISPLAYS it (never carries it as an
editable child table). Events written here:

  Accrued  — Sales/Purchase Invoice on_submit: the VAT the invoice RECORDS
             (accrual view), VAT-only + outside-scope net excluded.
  Realised — Payment Entry allocation on_submit, CASH basis only: the paid
             fraction of an allocated invoice's VAT/net (cash view).

Cancel/amend of a source clears its events (flags.ignore_immutable) so they are
re-derived, never silently doubled. Correcting an amount is a NEW entry, never an
edit — the doctype enforces immutability.
"""

import frappe
from frappe.utils import flt

from zikpro_uk_vat import cockpit as c

LEDGER = "VAT Ledger Entry"


# ---- derivation -----------------------------------------------------------

def _invoice_vat_net(doc):
	"""(vat, net_in_scope) for a submitted Sales/Purchase Invoice — VAT-only (posted
	to the configured VAT accounts) with outside-scope net excluded. Reuses the exact
	extraction the 9-box engine uses, so ledger and return can never disagree."""
	if doc.doctype == c.SALES_INVOICE:
		child, item_child, vtype = c.SALES_TAXES, f"{c.SALES_INVOICE} Item", c.OUTPUT_VAT
	else:
		child, item_child, vtype = c.PURCHASE_TAXES, f"{c.PURCHASE_INVOICE} Item", c.INPUT_VAT
	accts = c.vat_accounts(vtype)
	vat = c._vat_by_parent(child, doc.doctype, [doc.name], accts).get(doc.name, 0.0)
	excluded = c._excluded_net_by_parent(
		item_child, doc.doctype, [doc.name], c._outside_scope_templates()
	).get(doc.name, 0.0)
	net = flt(doc.get("base_net_total") or 0) - flt(excluded)
	return flt(vat), flt(net)


def _vat_box(doctype):
	return "Box 1" if doctype == c.SALES_INVOICE else "Box 4"


# ---- writers (hooked from doc_events) -------------------------------------

def record_invoice_accrual(doc, method=None):
	"""Sales/Purchase Invoice on_submit -> one Accrued entry (idempotent)."""
	if doc.doctype not in (c.SALES_INVOICE, c.PURCHASE_INVOICE):
		return
	_clear(doc.doctype, doc.name, event_type="Accrued")
	vat, net = _invoice_vat_net(doc)
	if not vat and not net:
		return
	_write(
		doc, event_type="Accrued", basis="Accrual", vat_box=_vat_box(doc.doctype),
		net=net, vat=vat, tax_point=doc.get("posting_date"),
	)
	_refresh_status(doc.doctype, doc.name)


# Boxes whose amount is VAT (goes in vat_amount) vs net turnover (goes in net_amount).
_VAT_BOXES = ("Box 1", "Box 2", "Box 4")


def record_adjustment(doc, method=None):
	"""VAT Adjustment on_submit -> one Adjusted ledger event (idempotent). The
	adjustment amount lands in vat_amount for VAT boxes (1/2/4) or net_amount for
	turnover boxes (6/7/8/9); the figure fold-in (_apply_adjustments) reads whichever
	is set. origin_ref/origin_period carry provenance for the double-count guard."""
	if doc.doctype != "VAT Adjustment":
		return
	_clear(doc.doctype, doc.name)
	amount = flt(doc.amount)
	is_vat_box = doc.vat_box in _VAT_BOXES
	_write(
		doc, event_type="Adjusted", basis=(c._scheme() == c.CASH and "Cash" or "Accrual"),
		vat_box=doc.vat_box,
		net=0 if is_vat_box else amount,
		vat=amount if is_vat_box else 0,
		tax_point=doc.get("posting_date"),
		origin_ref=doc.get("origin_name"),
		origin_period=doc.get("origin_period"),
	)


def record_return_filed(return_doc, from_date, to_date):
	"""On a successful HMRC filing, write a `Filed` ledger event (under each invoice
	in the period) so the invoice's accrual vat_status becomes 'Claimed'. Idempotent
	per return. Called from cockpit._record_submission AFTER HMRC accepts — inside its
	try/except, so a ledger hiccup never breaks the user's filing."""
	if not frappe.db.exists("DocType", LEDGER):
		return
	company = return_doc.get("company")
	period_key = return_doc.get("reference_key")
	# clear any prior Filed rows for this return (idempotent re-file)
	for name in frappe.get_all(LEDGER, filters={"event_type": "Filed", "vat_return": return_doc.name}, pluck="name"):
		d = frappe.get_doc(LEDGER, name)
		d.flags.ignore_immutable = True
		d.delete(ignore_permissions=True)
	touched = []
	for dt in (c.SALES_INVOICE, c.PURCHASE_INVOICE):
		filters = {"docstatus": 1, "posting_date": ["between", [from_date, to_date]]}
		if company:
			filters["company"] = company
		for inv in frappe.get_all(dt, filters=filters, fields=["name", "posting_date"]):
			vat, net = _invoice_vat_net(frappe.get_doc(dt, inv.name))
			if not vat and not net:
				continue
			_write(
				return_doc, event_type="Filed", basis="Accrual", vat_box=_vat_box(dt),
				net=net, vat=vat, tax_point=inv.posting_date, period_key=period_key,
				vat_return=return_doc.name, source_dt=dt, source_dn=inv.name,
			)
			touched.append((dt, inv.name))
	for dt, name in touched:
		_refresh_status(dt, name)
	frappe.db.commit()


def record_payment_realisation(doc, method=None):
	"""Payment Entry on_submit -> Realised entries (CASH basis only): the paid
	fraction of each allocated invoice's VAT/net. Idempotent for this payment."""
	if doc.doctype != "Payment Entry" or c._scheme() != c.CASH:
		return
	_clear_realised_for_payment(doc.name)  # idempotent for this payment
	touched = set()
	for ref in doc.get("references") or []:
		inv_dt = ref.get("reference_doctype")
		if inv_dt not in (c.SALES_INVOICE, c.PURCHASE_INVOICE) or not ref.get("reference_name"):
			continue
		inv = frappe.get_doc(inv_dt, ref.reference_name)
		grand = flt(inv.get("base_grand_total") or 0)
		if not grand:
			continue
		frac = flt(ref.get("allocated_amount") or 0) / grand
		vat, net = _invoice_vat_net(inv)
		if not vat and not net:
			continue
		# The Realised event is INVOICE-centric: its source is the invoice (so the
		# invoice's status rollup + VAT Activity panel can see its own realisation),
		# and origin_ref records the Payment Entry that realised it (used to clear on
		# payment cancel). posting/tax_point come from the payment (the realisation).
		_write(
			doc, event_type="Realised", basis="Cash", vat_box=_vat_box(inv_dt),
			net=net * frac, vat=vat * frac, tax_point=doc.get("posting_date"),
			origin_ref=doc.name, source_dt=inv_dt, source_dn=inv.name,
		)
		touched.add((inv_dt, inv.name))
	for dt, name in touched:
		_refresh_status(dt, name)


def clear_source_entries(doc, method=None):
	"""On cancel/amend of any source doc, drop its ledger events so they can be
	re-derived (never silently doubled). For a cancelled Payment Entry this also
	drops the invoice-centric Realised rows that reference it via origin_ref, and
	refreshes the status of the invoices they were realising."""
	if not frappe.db.exists("DocType", LEDGER):
		return
	# rows directly sourced from this doc (e.g. an invoice's Accrued rows)
	affected = {
		(e.source_doctype, e.source_name)
		for e in frappe.get_all(
			LEDGER,
			filters={"source_doctype": doc.doctype, "source_name": doc.name},
			fields=["source_doctype", "source_name"],
		)
	}
	_clear(doc.doctype, doc.name)
	# a cancelled payment: its Realised rows live under the INVOICE (source) and point
	# back to the payment via origin_ref — clear those and refresh those invoices.
	if doc.doctype == "Payment Entry":
		for r in frappe.get_all(
			LEDGER,
			filters={"event_type": "Realised", "origin_ref": doc.name},
			fields=["name", "source_doctype", "source_name"],
		):
			affected.add((r.source_doctype, r.source_name))
			d = frappe.get_doc(LEDGER, r.name)
			d.flags.ignore_immutable = True
			d.delete(ignore_permissions=True)
	for dt, name in affected:
		if dt and name and frappe.db.exists(dt, name):
			_refresh_status(dt, name)


def _clear_realised_for_payment(payment_name):
	"""Drop the Realised rows a payment produced (keyed to the invoice as source,
	the payment as origin_ref). Makes re-running a payment's realisation idempotent."""
	if not frappe.db.exists("DocType", LEDGER):
		return
	for name in frappe.get_all(
		LEDGER, filters={"event_type": "Realised", "origin_ref": payment_name}, pluck="name"
	):
		d = frappe.get_doc(LEDGER, name)
		d.flags.ignore_immutable = True
		d.delete(ignore_permissions=True)


# ---- helpers --------------------------------------------------------------

def _write(doc, event_type, basis, vat_box, net, vat, tax_point=None,
           period_key=None, vat_return=None, origin_ref=None, origin_period=None,
           source_dt=None, source_dn=None):
	# posting/company come from `doc` (the document driving the event — the invoice
	# for Accrued, the payment for Realised); source_dt/source_dn override which
	# document the event is FILED UNDER (always the invoice, so invoice-centric views
	# see it) when that differs from `doc`.
	e = frappe.new_doc(LEDGER)
	e.posting_date = doc.get("posting_date") or frappe.utils.nowdate()
	e.company = doc.get("company")
	e.event_type = event_type
	e.basis = basis
	e.vat_box = vat_box
	e.net_amount = net
	e.vat_amount = vat
	e.source_doctype = source_dt or doc.doctype
	e.source_name = source_dn or doc.name
	e.tax_point_date = tax_point
	e.period_key = period_key
	e.vat_return = vat_return
	e.origin_ref = origin_ref
	e.origin_period = origin_period
	e.insert(ignore_permissions=True)
	return e


def _clear(source_doctype, source_name, event_type=None):
	if not frappe.db.exists("DocType", LEDGER):
		return
	filters = {"source_doctype": source_doctype, "source_name": source_name}
	if event_type:
		filters["event_type"] = event_type
	for name in frappe.get_all(LEDGER, filters=filters, pluck="name"):
		d = frappe.get_doc(LEDGER, name)
		d.flags.ignore_immutable = True
		d.delete(ignore_permissions=True)


# ---- derived status (rollup shown on the source invoice) ------------------

def compute_vat_status(source_doctype, source_name):
	"""Roll the ledger up into a glanceable status for one source document.
	Never hand-set — always derived from the events."""
	rows = frappe.get_all(
		LEDGER,
		filters={"source_doctype": source_doctype, "source_name": source_name},
		fields=["event_type", "vat_amount"],
	)
	if not rows:
		return ""
	types = {r.event_type for r in rows}
	if "Repaid" in types:
		return "Repaid"
	if "Adjusted" in types:
		return "Adjusted"
	accrued = sum(flt(r.vat_amount) for r in rows if r.event_type == "Accrued")
	realised = sum(flt(r.vat_amount) for r in rows if r.event_type == "Realised")
	filed = "Filed" in types
	if c._scheme() == c.CASH:
		if not realised:
			return "Not claimed"
		# realised within a small tolerance of accrued => fully claimed
		if accrued and realised >= accrued - 0.01:
			return "Claimed"
		return "Partially claimed"
	# accrual basis: recorded on submit; "claimed" once its period is filed
	return "Claimed" if filed else "Not claimed"


def _refresh_status(source_doctype, source_name):
	"""Write the derived status to the source's vat_status custom field. Uses
	db.set_value so it does not fight the submit lock (direct SQL, no validation)."""
	if not frappe.db.has_column(source_doctype, "vat_status"):
		return
	status = compute_vat_status(source_doctype, source_name)
	frappe.db.set_value(source_doctype, source_name, "vat_status", status, update_modified=False)
