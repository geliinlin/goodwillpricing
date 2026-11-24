"""Compare the downloaded one-page report with the independent browser fixture SQL."""
from decimal import Decimal
import json
from pathlib import Path
import unittest

from pypdf import PdfReader

OUTPUT = Path(__file__).resolve().parents[2] / 'test-results' / 'admin-workflow'


class AdminExportTest(unittest.TestCase):
    def test_report_matches_browser_database(self):
        evidence = json.loads((OUTPUT / 'browser-sql.json').read_text())
        pages = PdfReader(OUTPUT / 'report-export.pdf').pages
        self.assertEqual(len(pages), 1)
        text = pages[0].extract_text()
        decisions = evidence['decisions']
        count = len(decisions)
        average = sum(Decimal(final) for _, _, _, final, _ in decisions) / count
        overrides = sum(Decimal(final) != Decimal(suggested)
                        for _, _, suggested, final, _ in decisions)
        staff = len({staff_id for _, staff_id, _, _, _ in decisions})
        self.assertIn(f'\n{count}\n${average:.2f}\n{overrides/count:.1%} ({overrides})\n{staff}\n', text)
        self.assertIn('No overrides\n0', text)
        for category, name, branch, price in evidence['export_rows']:
            # This fixture has one decision per category and per active employee.
            self.assertIn(f'{category}\n1\n${price}\n', text)
            self.assertIn(f'{name}\n{branch}\n1\n${price}\n0\n', text)


if __name__ == '__main__':
    unittest.main(verbosity=2)
