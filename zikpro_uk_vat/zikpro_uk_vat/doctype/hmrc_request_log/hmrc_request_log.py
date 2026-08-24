"""HMRC Request Log — a masked, append-only audit trail of calls to HMRC.

Written server-side by cockpit._log_hmrc_call. NEVER stores the bearer token; the
fraud-prevention headers are recorded by NAME only. Treated as immutable: once
written, edits/deletes are blocked for everyone except a purge by System Manager.
"""

import frappe
from frappe.model.document import Document


class HMRCRequestLog(Document):
	def on_update(self):
		# Immutable audit record: block EDITS after creation, but never the initial insert.
		# Use get_doc_before_save() (None on insert, a doc on a genuine update) rather than
		# is_new(): on some Frappe versions is_new() is already False during the insert's
		# post-save on_update, so the old check fired on EVERY write — and although the caller
		# catches the exception, frappe.throw had already queued the message, leaking a popup
		# ("...cannot be edited") to the browser on every HMRC call.
		if self.get_doc_before_save() is not None and not self.flags.ignore_immutable:
			frappe.throw(frappe._("HMRC Request Log entries are an immutable audit trail and cannot be edited."))
