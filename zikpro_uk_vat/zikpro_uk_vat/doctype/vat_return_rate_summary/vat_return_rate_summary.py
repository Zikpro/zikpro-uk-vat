"""VAT Return Rate Summary — child of UK MTD VAT Return.

The Notice 700/22 VAT account (net turnover by VAT treatment) frozen at filing, so
the return's audit record reflects what was filed rather than a live recompute.
"""

from frappe.model.document import Document


class VATReturnRateSummary(Document):
	pass
