"""Guidelines V4.6: inline statistics in table heading, no overview card."""
import ast
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]

class GuidelineOverviewLayoutTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = (ROOT / 'guideline_page.py').read_text(encoding='utf-8')
        cls.css = (ROOT / 'styles/guidelines.css').read_text(encoding='utf-8')

    def test_page_compiles_and_listing_still_precedes_editor(self):
        tree = ast.parse(self.source)
        page = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)
                    and n.name == 'show_guideline_management')
        segment = ast.get_source_segment(self.source, page)
        self.assertIn('st.columns([3.1, 1.2]', segment)
        self.assertLess(segment.index('_table(rows)'), segment.index('key="gw_add_guideline"'))
        self.assertNotIn('_summary_panel', self.source)
        self.assertNotIn('gw_guideline_summary', self.source)
        self.assertNotIn('Guideline Overview', self.source)
        self.assertNotIn('.st-key-gw_guideline_summary', self.css)

    def test_metrics_are_in_header_and_editor_stays_sticky(self):
        table = next(n for n in ast.walk(ast.parse(self.source))
                     if isinstance(n, ast.FunctionDef) and n.name == '_table')
        segment = ast.get_source_segment(self.source, table)
        for token in ('category_total', 'active_total', 'inactive_total',
                      'categories covered', 'Active Guidelines', 'Inactive Guidelines'):
            self.assertIn(token, segment)
        self.assertIn('gw-covered-count', self.css)
        self.assertIn('gw-legend strong', self.css)
        self.assertIn('position: sticky', self.css)
        self.assertIn('top: calc(var(--gw-nav-height) + 14px)', self.css)

    def test_editing_sorting_and_status_filter_unchanged(self):
        for token in ('save_guideline(', 'set_guideline_active(',
                      "on_select='rerun'", 'gw_filter_status', 'next_sort(sorts, key)'):
            self.assertIn(token, self.source)

if __name__ == '__main__':
    unittest.main()
