# Copyright (c) 2026, Zikpro and contributors
# For license information, please see license.txt

from frappe.model.document import Document


class MTDVATAccount(Document):
	"""Maps a chart-of-accounts head to VAT output (sales) or input (purchases).

	Replaces the old '%VAT%' account-name heuristic, which silently produced £0
	boxes whenever a client named their VAT account anything else.
	"""

	pass
