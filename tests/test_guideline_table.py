"""Regression tests for the pure six-column filter and priority-sort rules."""
from decimal import Decimal
import unittest
from guideline_table import COLUMNS, display_value, filter_options, filter_rows, next_sort, sort_rows

ROWS = [
    dict(Guideline_Id=1, Item_Type_Name='Furniture', Category_Name='Tables',
         Brand_Name='Generic', Condition_Name='Good', Guideline_Suggested_Price=Decimal('24.99'), Guideline_Is_Active=1),
    dict(Guideline_Id=2, Item_Type_Name='Clothing', Category_Name='Accessories',
         Brand_Name='Designer', Condition_Name='Fair', Guideline_Suggested_Price=Decimal('7.32'), Guideline_Is_Active=0),
    dict(Guideline_Id=3, Item_Type_Name='Clothing', Category_Name='Accessories',
         Brand_Name='Generic', Condition_Name='Good', Guideline_Suggested_Price=Decimal('19.99'), Guideline_Is_Active=1),
    dict(Guideline_Id=4, Item_Type_Name='Clothing', Category_Name='Accessories',
         Brand_Name='Generic', Condition_Name='Fair', Guideline_Suggested_Price=Decimal('17.00'), Guideline_Is_Active=0),
]

class GuidelineTablePureTests(unittest.TestCase):
    def test_exact_six_column_order_no_status_or_update(self):
        self.assertEqual([col[0] for col in COLUMNS],
                         ['No.', 'Item Type', 'Category', 'Brand Tier', 'Condition', 'Suggested Price'])
    def test_numeric_id_and_price_options_are_numerically_sorted(self):
        self.assertEqual(filter_options(ROWS, 'id'), ['1', '2', '3', '4'])
        self.assertEqual(filter_options(ROWS, 'price'), ['$7.32', '$17.00', '$19.99', '$24.99'])
    def test_all_values_per_column_are_selectable(self):
        self.assertEqual(filter_options(ROWS, 'type'), ['Clothing', 'Furniture'])
        self.assertEqual(filter_options(ROWS, 'brand'), ['Designer', 'Generic'])
    def test_multiple_values_or_within_column(self):
        result = filter_rows(ROWS, {'brand': ['Designer', 'Generic']})
        self.assertEqual(len(result), 4)
    def test_multiple_columns_and(self):
        result = filter_rows(ROWS, {'type': ['Clothing'], 'condition': ['Good']})
        self.assertEqual([r['Guideline_Id'] for r in result], [3])
    def test_blank_selection_means_no_filter(self):
        self.assertEqual(len(filter_rows(ROWS, {'type': []})), 4)
    def test_id_filter_matches_exact_id(self):
        self.assertEqual([r['Guideline_Id'] for r in filter_rows(ROWS, {'id': ['2']})], [2])
    def test_price_filter_matches_price_format(self):
        self.assertEqual([r['Guideline_Id'] for r in filter_rows(ROWS, {'price': ['$24.99']})], [1])
    def test_sort_click_adds_priority_and_cycles(self):
        a = next_sort([], 'category')
        self.assertEqual(a, [('category', 'asc')])
        a = next_sort(a, 'brand')
        self.assertEqual(a, [('category', 'asc'), ('brand', 'asc')])
        a = next_sort(a, 'category')
        self.assertEqual(a, [('category', 'desc'), ('brand', 'asc')])
        a = next_sort(a, 'category')
        self.assertEqual(a, [('brand', 'asc')])
    def test_primary_sort_priority_precedes_secondary(self):
        ordered = sort_rows(ROWS, [('category', 'asc'), ('price', 'desc')])
        self.assertEqual([r['Guideline_Id'] for r in ordered], [3, 4, 2, 1])
    def test_numeric_price_sort(self):
        self.assertEqual([r['Guideline_Id'] for r in sort_rows(ROWS, [('price','asc')])], [2,4,3,1])
    def test_stable_id_fallback_descending(self):
        self.assertEqual([r['Guideline_Id'] for r in sort_rows(ROWS, [])], [4,3,2,1])
    def test_invalid_column_rejected(self):
        with self.assertRaises(ValueError): filter_options(ROWS, 'sql-injection')
        with self.assertRaises(ValueError): next_sort([], 'unknown')
        with self.assertRaises(ValueError): filter_rows(ROWS, {'unknown': ['x']})
        with self.assertRaises(ValueError): sort_rows(ROWS, [('category','hack')])
    def test_status_filter_active_and_inactive(self):
        self.assertEqual(
            [r['Guideline_Id'] for r in filter_rows(ROWS, {}, status='Active')], [1, 3])
        self.assertEqual(
            [r['Guideline_Id'] for r in filter_rows(ROWS, {}, status='Inactive')], [2, 4])
        self.assertEqual(len(filter_rows(ROWS, {}, status='All')), 4)

    def test_status_filter_combines_with_column_values(self):
        filtered = filter_rows(ROWS, {'type': ['Clothing'], 'condition': ['Fair']}, status='Inactive')
        self.assertEqual([r['Guideline_Id'] for r in filtered], [2, 4])

    def test_unknown_status_rejected(self):
        with self.assertRaises(ValueError):
            filter_rows(ROWS, {}, status='archived')

    def test_source_data_not_mutated(self):
        before = list(ROWS)
        sort_rows(ROWS, [('category','asc')])
        self.assertEqual(before, ROWS)

if __name__ == '__main__':
    unittest.main()
