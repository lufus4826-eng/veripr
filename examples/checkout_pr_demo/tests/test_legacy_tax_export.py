"""Pre-existing broken test on Base branch (unrelated to checkout PR)."""

import unittest

from checkout_app.legacy_tax_export import export_tax_csv_row


class TestLegacyTaxExport(unittest.TestCase):
    def test_legacy_tax_rounding(self):
        row = export_tax_csv_row("ORD-LEGACY", 100.0, tax_rate=0.075)
        self.assertEqual(row, "ORD-LEGACY,100.00,7.50")


if __name__ == "__main__":
    unittest.main(verbosity=2)
