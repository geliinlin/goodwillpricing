"""Decode the actual historical reprint and compare its contents with the saved SQL row."""
import json
from pathlib import Path
import unittest

import pypdfium2 as pdfium
import zxingcpp

OUT = Path(__file__).resolve().parents[2] / 'test-results/cross-role-integration'


class ReprintTest(unittest.TestCase):
    def test_reprint_after_replacement_and_deactivation(self):
        history = json.loads((OUT / 'sql-checkpoints.json').read_text())
        original = history['accepted']['decisions'][-1]
        self.assertIn(original, history['reauthenticated']['decisions'])
        with pdfium.PdfDocument(OUT / 'historical-tag.pdf') as doc:
            self.assertEqual(len(doc), 1)
            page = doc[0]
            self.assertEqual(page.get_size(), (288, 180))
            content = page.get_textpage().get_text_bounded()
            for value in (original['tag'], '$' + original['final'], original['description']):
                self.assertIn(value, content)
            codes = zxingcpp.read_barcodes(page.render(scale=203/72).to_pil(),
                                           formats=zxingcpp.BarcodeFormat.Code128)
            self.assertEqual([code.text for code in codes], [original['tag']])


if __name__ == '__main__':
    unittest.main(verbosity=2)
