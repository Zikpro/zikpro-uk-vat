# Copyright (c) 2025, Zikpro and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document


class UKMTDVATReturn(Document):
	"""A VAT return as filed with HMRC — a legal declaration, hence submittable.

	docstatus=1 makes the filed figures immutable and gives the 6-year audit
	artifact (9 boxes + scheme + HMRC receipt). HMRC remains the source of truth;
	this is our record of what we sent and what HMRC acknowledged.
	"""

	def validate(self):
		# One LIVE return per VRN+period. Deliberately enforced here rather than with
		# a `unique` field: a unique field on a submittable DocType always breaks
		# Amend, because the amended draft copies the value verbatim (broadcast B15).
		if not (self.vrn and self.reference_key):
			return
		existing = frappe.get_all(
			"UK MTD VAT Return",
			filters={
				"vrn": self.vrn,
				"reference_key": self.reference_key,
				"docstatus": 1,
				"name": ["!=", self.name],
			},
			pluck="name",
		)
		# An amendment supersedes the document it came from, so ignore that one.
		existing = [n for n in existing if n != self.amended_from]
		if existing:
			frappe.throw(
				_("A VAT return for period {0} has already been filed ({1}).").format(
					self.reference_key, existing[0]
				),
				title=_("Already Filed"),
			)

	def before_submit(self):
		# SoD / direct-submit guard (B58/B93). A VAT return is a legal declaration that
		# only legitimately reaches docstatus=1 AFTER HMRC has accepted it via the cockpit
		# filing path (submit_return / approve_and_submit), each of which records the HMRC
		# receipt BEFORE submitting. A raw frappe.client.submit / run_doc_method on a bare
		# draft would otherwise mark a return "filed" with no HMRC submission — and, on the
		# approver flow, walk straight past the preparer/approver segregation of duties.
		# The HMRC form bundle number is the un-fakeable proof of a real filing, so require it.
		if not self.form_bundle_number:
			frappe.throw(
				_(
					"A VAT return can only be submitted through the VAT cockpit, which files it "
					"to HMRC first. This return carries no HMRC receipt (form bundle number), so it "
					"has not been filed — submit it from the cockpit (prepare → approve & file)."
				),
				title=_("File Through the Cockpit"),
				exc=frappe.PermissionError,
			)

	def on_cancel(self):
		# You cannot un-file a return with HMRC. Cancelling would assert "this never
		# happened", which is false. Corrections go through HMRC's error-correction
		# route (next-return adjustment, or VAT652 above the threshold).
		frappe.throw(
			_(
				"A submitted VAT return cannot be cancelled — HMRC already holds it. "
				"Use HMRC's error-correction process (adjust the next return, or notify "
				"on form VAT652) instead."
			),
			title=_("Cannot Cancel a Filed Return"),
		)
