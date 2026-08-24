"""P0-4: the shared construction reverse-charge classifier is the one source of truth.

Locks the invariants of construction_reverse_charge_templates / is_construction_reverse_charge
so the CIS product and this app can rely on one definition. Data-independent: every
assertion holds whether or not any reverse-charge treatment is configured, so it never
flakes on a bare vs seeded site.
"""

from zikpro_uk_vat import cockpit as c


def prove_rc_classifier():
	res = {}
	res["none_is_false"] = c.is_construction_reverse_charge(None) is False
	res["blank_is_false"] = c.is_construction_reverse_charge("") is False

	all_rc = c.construction_reverse_charge_templates()
	purchase = c.construction_reverse_charge_templates("purchase")
	sale = c.construction_reverse_charge_templates("sale")

	# side sets are subsets of the general set, and the two sides don't overlap
	res["purchase_subset_all"] = purchase <= all_rc
	res["sale_subset_all"] = sale <= all_rc
	res["sides_disjoint"] = purchase.isdisjoint(sale)

	# the set-builder and the predicate agree, on every side
	res["predicate_agrees_all"] = all(c.is_construction_reverse_charge(t) for t in all_rc)
	res["predicate_agrees_purchase"] = all(c.is_construction_reverse_charge(t, "purchase") for t in purchase)
	res["predicate_agrees_sale"] = all(c.is_construction_reverse_charge(t, "sale") for t in sale)

	return res
