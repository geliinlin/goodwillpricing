"""Deterministic Tag ID, actual Code128 rendering and printable PDF content."""
import unittest
from datetime import datetime
from decimal import Decimal
import importlib.util
from unittest.mock import patch
import pricing_decision as mod

RECEIPT = {
    "tag_number": "MP26-100012", "branch_id": 3,
    "category": "Clothing", "brand": "Designer", "condition": "Good",
    "description": "Blue jeans", "final_price": "12.00",
}


class BarcodeTests(unittest.TestCase):
    def test_city_prefixes_and_unique_decision_serial(self):
        when = datetime(2026, 11, 22, 14, 30)
        a = mod.generate_tag_number("Mount Pleasant", when, 12)
        b = mod.generate_tag_number("Mount Pleasant", when, 13)
        self.assertEqual(a, "MP26-100012")
        self.assertNotEqual(a, b)
        self.assertEqual(mod.generate_tag_number("Lansing", when, 1), "LA26-100001")

    def test_tag_id_is_repeatable_and_has_no_database_side_effect(self):
        when = datetime(2026, 11, 22, 0, 0)
        self.assertEqual(mod.generate_tag_number("Mount Pleasant", when, 100),
                         mod.generate_tag_number("Mount Pleasant", when, 100))

    def test_tag_rejects_overlong_id(self):
        with self.assertRaises(mod.PricingValidationError):
            mod.generate_tag_number("Mount Pleasant", datetime(2026, 11, 22), 12345678901234567890)

    @unittest.skipUnless(importlib.util.find_spec("reportlab"), "reportlab is not installed")
    def test_svg_is_code128_with_bar_shapes(self):
        graphic = mod.barcode_svg(RECEIPT["tag_number"])
        self.assertIsInstance(graphic, bytes)
        self.assertIn(b"<svg", graphic)
        self.assertIn(b"<rect", graphic)

    @unittest.skipUnless(importlib.util.find_spec("reportlab"), "reportlab is not installed")
    def test_pdf_is_nonempty_printable_document(self):
        pdf = mod.printable_label_pdf(RECEIPT)
        self.assertTrue(pdf.startswith(b"%PDF-"))
        self.assertGreater(len(pdf), 1000)
        self.assertIn(b"%%EOF", pdf[-32:])

    @unittest.skipUnless(importlib.util.find_spec("reportlab"), "reportlab is not installed")
    def test_pdf_allows_empty_optional_description(self):
        data = dict(RECEIPT, description="")
        pdf = mod.printable_label_pdf(data)
        self.assertTrue(pdf.startswith(b"%PDF-"))

    @unittest.skipUnless(importlib.util.find_spec("reportlab"), "reportlab is not installed")
    def test_long_description_and_large_price_fit_without_crash(self):
        data = dict(RECEIPT, description="This is a very long item description " * 8,
                    final_price="999999.99")
        pdf = mod.printable_label_pdf(data)
        self.assertTrue(pdf.startswith(b"%PDF-"))

    @unittest.skipUnless(importlib.util.find_spec("reportlab"), "reportlab is not installed")
    def test_long_reprint_barcode_and_quiet_zones_fit_printable_width(self):
        from reportlab.graphics.barcode.code128 import Code128

        draw = Code128.drawOn
        bounds = []

        def capture(code, canvas, x, y):
            bounds.append((canvas.absolutePosition(x, y)[0],
                           canvas.absolutePosition(x + code.width, y)[0]))
            return draw(code, canvas, x, y)

        with patch.object(Code128, "drawOn", capture):
            mod.printable_label_pdf(dict(RECEIPT, tag_number="TEST-LEGACY-CODE-123"))
        self.assertEqual(len(bounds), 1)
        self.assertGreaterEqual(bounds[0][0], 16 - 0.001)
        self.assertLessEqual(bounds[0][1], 288 - 16 + 0.001)

    @unittest.skipUnless(importlib.util.find_spec("reportlab"), "reportlab is not installed")
    def test_unencodable_existing_tag_is_not_silently_changed(self):
        with self.assertRaises(mod.PricingValidationError):
            mod.printable_label_pdf(dict(RECEIPT, tag_number="TEST-🧦"))


if __name__ == "__main__":
    unittest.main()
