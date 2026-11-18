"""Unit tests targeting discount_service directly."""

import unittest

from checkout_app.discount_service import calculate_discount


class TestDiscountService(unittest.TestCase):
    def test_valid_coupon_discount(self):
        self.assertEqual(calculate_discount(100.0, coupon_code="SAVE10"), 10.0)

    def test_unknown_coupon_zero(self):
        self.assertEqual(calculate_discount(100.0, coupon_code="NOPE"), 0.0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
