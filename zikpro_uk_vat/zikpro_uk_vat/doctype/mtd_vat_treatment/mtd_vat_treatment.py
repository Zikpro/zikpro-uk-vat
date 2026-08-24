# Copyright (c) 2026, Zikpro and contributors
# For license information, please see license.txt

from frappe.model.document import Document


class MTDVATTreatment(Document):
	"""Maps an Item Tax Template to its VAT treatment.

	Zero-rated, exempt and outside-scope items all carry 0% VAT, so the tax
	amounts alone cannot distinguish them — only the template can. Box 6/7 must
	include zero-rated and exempt supplies but exclude outside-scope amounts.
	"""

	pass
