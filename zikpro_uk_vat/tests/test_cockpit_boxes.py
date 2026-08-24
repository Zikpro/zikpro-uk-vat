"""Regression tests for the 9-box calculation keystone (cockpit.compute_boxes).

Guards the four inherited bugs the cockpit calc fixes (see BUILD_TRACKER):
credit notes dropped, non-VAT counted as VAT, Box 5 sign, Data-type/rounding.
compute_boxes is a pure function, so these run without HMRC or a DB.
"""

import unittest

from zikpro_uk_vat.cockpit import compute_boxes


class TestComputeBoxes(unittest.TestCase):
	def test_credit_notes_included(self):
		# A sale (1000 net / 200 VAT) and a credit note (negative) net off.
		b = compute_boxes([{"net": 1000, "vat": 200}, {"net": -100, "vat": -20}], [])
		self.assertEqual(b["box1"], 180.0)  # 200 - 20
		self.assertEqual(b["box6"], 900)    # 1000 - 100

	def test_box5_is_absolute(self):
		# Box4 > Box3 must yield a POSITIVE Box5 (HMRC rejects negatives).
		b = compute_boxes([{"net": 500, "vat": 100}], [{"net": 900, "vat": 300}])
		self.assertEqual(b["box3"], 100.0)
		self.assertEqual(b["box4"], 300.0)
		self.assertEqual(b["box5"], 200.0)

	def test_rounding(self):
		# Boxes 1-5 to 2dp; boxes 6-9 to whole pounds.
		b = compute_boxes([{"net": 339.90, "vat": 67.98}], [])
		self.assertEqual(b["box1"], 67.98)
		self.assertEqual(b["box6"], 340)

	def test_rounding_is_half_up_not_bankers(self):
		# Broadcast B36: Python round() does banker's rounding + float-repr error,
		# diverging from the arithmetic rounding HMRC expects. Each input rounds the
		# WRONG way under round(); assert the statutory HALF_UP result instead.
		# 0.125 -> round()=0.12, must be 0.13
		self.assertEqual(compute_boxes([{"net": 1, "vat": 0.125}], [])["box1"], 0.13)
		# 1.005 -> round()=1.00 (float repr), must be 1.01
		self.assertEqual(compute_boxes([{"net": 1, "vat": 1.005}], [])["box1"], 1.01)
		# whole-pound HALF_UP: 340.50 -> 341 (round() banker's gives 340)
		self.assertEqual(compute_boxes([{"net": 340.50, "vat": 0}], [])["box6"], 341)

	def test_empty(self):
		b = compute_boxes([], [])
		self.assertEqual(b["box5"], 0.0)
		self.assertEqual(b["box6"], 0)

	def test_box3_is_box1_plus_box2(self):
		b = compute_boxes([{"net": 1000, "vat": 200}], [], eu={"box2": 50})
		self.assertEqual(b["box3"], 250.0)

	def test_returns_numbers_not_strings(self):
		b = compute_boxes([{"net": 10, "vat": 2}], [])
		self.assertIsInstance(b["box1"], float)
		self.assertIsInstance(b["box6"], int)


if __name__ == "__main__":
	unittest.main()
