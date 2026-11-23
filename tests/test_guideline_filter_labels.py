"""Guidelines V4.4: compact filters and legend position; no DB needed."""
import ast
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]


class GuidelineFilterLayoutTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = (ROOT / 'guideline_page.py').read_text(encoding='utf-8')
        cls.tree = ast.parse(cls.source)
        cls.table = next(n for n in ast.walk(cls.tree)
                         if isinstance(n, ast.FunctionDef) and n.name == '_table')

    def test_multi_select_placeholder_is_its_field_name(self):
        widgets = [n for n in ast.walk(self.table)
                   if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                   and n.func.attr == 'multiselect']
        self.assertEqual(len(widgets), 1)
        kw = {k.arg: k.value for k in widgets[0].keywords}
        self.assertEqual(ast.literal_eval(kw['label_visibility']), 'collapsed')
        self.assertIsInstance(kw['placeholder'], ast.Subscript)
        self.assertEqual(kw['placeholder'].value.id, 'fields')

    def test_status_placeholder_preserves_all_option(self):
        widgets = [n for n in ast.walk(self.table)
                   if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                   and n.func.attr == 'selectbox']
        self.assertEqual(len(widgets), 1)
        kw = {k.arg: k.value for k in widgets[0].keywords}
        self.assertEqual(ast.literal_eval(kw['label_visibility']), 'collapsed')
        self.assertEqual(ast.literal_eval(widgets[0].args[1]), ['All', 'Active', 'Inactive'])
        fmt = eval(compile(ast.Expression(kw['format_func']), '<test>', 'eval'), {})
        self.assertEqual([fmt(v) for v in ['All', 'Active', 'Inactive']],
                         ['Status', 'Active', 'Inactive'])

    def test_status_and_number_share_first_filter_slot(self):
        self.assertIn("top_filters = st.columns([0.5, 0.5, 1, 1], gap='small')", self.source)
        self.assertLess(self.source.index('with top_filters[0]:'), self.source.index('with top_filters[1]:'))
        self.assertIn("selected['id'] = pick_values('id')", self.source)

    def test_legend_is_right_of_title_not_separate_row(self):
        start = self.source.index('heading_col, legend_col = st.columns(')
        end = self.source.index('sorts = list(', start)
        segment = self.source[start:end]
        self.assertIn('with heading_col:', segment)
        self.assertIn('with legend_col:', segment)
        self.assertLess(segment.index('Pricing Guidelines'), segment.index('gw-status-legend'))
        css = (ROOT / 'styles/guidelines.css').read_text(encoding='utf-8')
        self.assertIn('justify-content:flex-end', css)
        self.assertIn('gw-legend-active', css)
        self.assertIn('gw-legend-inactive', css)

    def test_sorting_selection_and_status_color_are_preserved(self):
        self.assertIn('next_sort(sorts, key)', self.source)
        self.assertIn("selection_mode='single-row'", self.source)
        self.assertIn('_open_selected_guideline', self.source)
        self.assertIn("shade = '#e5f4ec' if statuses[table_row.name] else '#edf0f3'", self.source)


if __name__ == '__main__':
    unittest.main()
