"""Verify the output of run_browser.py using an independent raster decoder.

Requires Pillow, pypdfium2, zxing-cpp and the project's ReportLab dependency.
Run after the browser suite: python tests/e2e/check_printed_tags.py
"""
import json
from pathlib import Path
import sys
import unittest

import pypdfium2 as pdfium
from PIL import Image
import zxingcpp

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from pricing_decision import printable_label_pdf

OUTPUT = ROOT / 'test-results' / 'staff-workflow'


class PrintedTagTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tags = json.loads((OUTPUT / 'browser-tags.json').read_text())

    def assert_code128(self, pixels, expected):
        results = zxingcpp.read_barcodes(pixels, formats=zxingcpp.BarcodeFormat.Code128)
        self.assertEqual([result.text for result in results], [expected])

    def assert_pdf(self, name, tag, price, description):
        with pdfium.PdfDocument(OUTPUT / name) as document:
            self.assertEqual(len(document), 1)
            page = document[0]
            self.assertEqual(page.get_size(), (288, 180))
            text = page.get_textpage().get_text_bounded()
            for value in (tag, price, description):
                self.assertIn(value, text)
            bitmap = page.render(scale=203 / 72)
            self.assert_code128(bitmap.to_pil(), tag)
            if name == 'stress.pdf':
                bitmap.to_pil().save(OUTPUT / 'stress-203dpi.png')

    def test_accepted_download(self):
        self.assert_pdf('accepted.pdf', self.tags['acceptedTag'], '$12.50', self.tags['run'] + '-accept')

    def test_override_download(self):
        self.assert_pdf('override.pdf', self.tags['overriddenTag'], '$9.00', self.tags['run'] + '-override')

    def test_history_reprint(self):
        self.assert_pdf('reprint.pdf', self.tags['overriddenTag'], '$9.00', self.tags['run'] + '-override')

    def test_receipt_screenshot(self):
        with Image.open(OUTPUT / '07-tag.png') as image:
            self.assert_code128(image, self.tags['overriddenTag'])

    def test_long_legacy_tag_with_maximum_price(self):
        tag = 'TEST-LEGACY-CODE-123'
        receipt = dict(tag_number=tag, branch_id=1, final_price='999999.99',
                       category='Shirts', brand='Generic', condition='Good',
                       description=('Café cotton shirt with stitching. ' * 9)[:255])
        (OUTPUT / 'stress.pdf').write_bytes(printable_label_pdf(receipt))
        self.assert_pdf('stress.pdf', tag, '$999999.99', 'Café cotton shirt')


if __name__ == '__main__':
    unittest.main(verbosity=2)
