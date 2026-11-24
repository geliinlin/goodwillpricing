"""Verify downloaded branch-transfer PDFs against persistent independent SQL evidence."""
import json
import os
from pathlib import Path
import unittest

from pypdf import PdfReader
import pypdfium2 as pdfium
import zxingcpp

OUT = Path(os.environ.get('GW_TEST_RESULTS', Path(__file__).resolve().parents[2] / 'test-results/final-integrity'))


class TransferExports(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = json.loads((OUT/'transfer-sql.json').read_text())['after_reprints']

    def verify_label(self, file, row, store):
        with pdfium.PdfDocument(OUT/file) as doc:
            self.assertEqual(len(doc), 1)
            page = doc[0]
            text = page.get_textpage().get_text_bounded()
            for value in (row['tag'], '$'+row['price'], 'STORE '+store):
                self.assertIn(value, text)
            codes = zxingcpp.read_barcodes(page.render(scale=203/72).to_pil(),
                                           formats=zxingcpp.BarcodeFormat.Code128)
            self.assertEqual([code.text for code in codes], [row['tag']])

    def test_reprint_after_transfer(self):
        first = self.data['decisions'][1]
        self.assertNotEqual(first['branch_id'], self.data['current_branch'])
        self.verify_label('04-original-reprint.pdf',first,f"{first['branch_id']:03d}")

    def test_reprint_with_unknown_store(self):
        legacy = self.data['decisions'][0]
        self.assertIsNone(legacy['branch_id'])
        self.verify_label('08-legacy-reprint.pdf', legacy, 'Unknown')

    def test_report_has_separate_staff_and_historical_branch_rows(self):
        pages = PdfReader(OUT/'transfer-report.pdf').pages
        self.assertEqual(len(pages), 1)
        text = pages[0].extract_text()
        overall = next(row for row in self.data['reports'] if row['branch']=='All branches')
        self.assertIn(f"\n{overall['items']}\n${overall['avg']}\n{overall['rate']} (1)\n{overall['staff']}\n", text)
        for row in self.data['decisions']:
            staff = 'Fixture staff_e2e' if row['staff']==2 else 'Fixture other_e2e'
            self.assertIn(f"{staff}\n{row['branch']}\n1\n${row['price']}\n",text)


if __name__ == '__main__':
    unittest.main(verbosity=2)
