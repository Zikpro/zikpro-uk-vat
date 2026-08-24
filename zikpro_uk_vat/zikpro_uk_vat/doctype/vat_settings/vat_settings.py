# Copyright (c) 2025, Zikpro and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document


class VATSettings(Document):
	def validate(self):
		self._validate_broker_config()

	def _validate_broker_config(self):
		"""BSW-5 / B49: when broker mode is on, the broker connection must be complete.

		The broker_* fields use mandatory_depends_on (use_broker), which Frappe enforces
		ONLY in the browser — a save via the REST API / script / a portal could otherwise
		turn broker mode on with a null broker_url / tenant_id / shared_secret, and then
		every OAuth call silently no-ops. Enforce it server-side too.

		broker_shared_secret is a Password: check self.get() (truthy for a just-typed value
		OR the loaded '****' placeholder) rather than get_password(), so this never tries to
		DECRYPT a value — decryption can throw on a cloned/restored site (B47/P0-5).
		"""
		if not self.use_broker:
			return
		missing = []
		if not self.broker_url:
			missing.append("Broker URL")
		if not self.broker_tenant_id:
			missing.append("Broker Tenant ID")
		if not self.get("broker_shared_secret"):
			missing.append("Broker Shared Secret")
		if missing:
			frappe.throw(
				_(
					"Broker mode is enabled but the broker connection is incomplete: {0} not set. "
					"Fill the broker fields (or turn off 'Use broker') before saving."
				).format(", ".join(missing)),
				title=_("Broker Connection Incomplete"),
			)
