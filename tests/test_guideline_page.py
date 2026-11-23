"""Guideline page behavior for the CURRENT V2 UI and its dynamic widgets.

No AppTest or real browser is needed: Streamlit widgets and database services are
mocked. This verifies Python interactions, not CSS rendering or pixels.
"""
from copy import deepcopy
from datetime import datetime
from decimal import Decimal
import unittest
from unittest.mock import patch

from _goodwill_test_helpers import ensure_streamlit_importable, FakeStreamlit

ensure_streamlit_importable()
import guideline_page as page
from guidelines import snapshot_token

ADMIN = {"user_id": 7, "user_type": "Administrator", "full_name": "Morgan Lee"}
STAFF = {"user_id": 8, "user_type": "Store Staff", "branch_id": 1}
LOOKUPS = {
    "Category_Id": {1: "Clothing"}, "Brand_Id": {2: "Levi's"},
    "Condition_Id": {3: "Good"}, "Item_Type_Id": {4: "Jeans"},
}
ROW = {
    "Guideline_Id": 8, "Guideline_Suggested_Price": Decimal("12.99"),
    "Guideline_Last_Updated": datetime(2026, 11, 17, 12),
    "Guideline_Is_Active": 1, "Admin_Id": 7,
    "Category_Id": 1, "Brand_Id": 2, "Condition_Id": 3, "Item_Type_Id": 4,
    "Category_Name": "Clothing", "Brand_Name": "Levi's",
    "Condition_Name": "Good", "Item_Type_Name": "Jeans", "Admin_Name": "Morgan Lee",
}


class GuidelinePageTests(unittest.TestCase):
    def setUp(self):
        self.ui = FakeStreamlit()
        self.patch_st = patch.object(page, 'st', self.ui)
        self.patch_st.start()
        self.addCleanup(self.patch_st.stop)
        self.load_styles = patch.object(page, 'load_styles')
        self.styles = self.load_styles.start()
        self.addCleanup(self.load_styles.stop)
        self.rows = [deepcopy(ROW)]
        self.lookups = deepcopy(LOOKUPS)

    def test_admin_renders_page_with_sole_shared_header(self):
        with patch.object(page, '_brand_header') as legacy, \
             patch.object(page, 'load_catalog', return_value=(self.lookups, self.rows)):
            page.show_guideline_management(ADMIN, show_header=False)
        legacy.assert_not_called()
        self.styles.assert_called_once_with('guidelines.css')
        self.assertIn('Guideline management', ''.join(self.ui.styles))
        self.assertIn('gw_guideline_content', self.ui.containers)
        self.assertIn('gw_guideline_editor_slot', self.ui.containers)
        self.assertNotIn('gw_report_panel', self.ui.containers)
        self.assertFalse(any('Add Item Type' in label for label, _ in self.ui.button_calls))

    def test_explicit_standalone_view_can_request_legacy_header(self):
        with patch.object(page, '_brand_header') as legacy, \
             patch.object(page, 'load_catalog', return_value=(self.lookups, self.rows)):
            page.show_guideline_management(ADMIN, show_header=True)
        legacy.assert_called_once_with()

    def test_staff_is_rejected_before_db_or_styling(self):
        with patch.object(page, 'load_catalog') as catalog:
            with self.assertRaises(PermissionError):
                page.show_guideline_management(STAFF, show_header=False)
        catalog.assert_not_called()
        self.styles.assert_not_called()

    def test_add_guideline_button_sets_editor_mode(self):
        self.ui.buttons['gw_add_guideline'] = True
        with patch.object(page, 'load_catalog', return_value=(self.lookups, self.rows)), \
             patch.object(page, '_editor') as editor:
            page.show_guideline_management(ADMIN, show_header=False)
            self.assertEqual(editor.call_args.args[0], ADMIN)
        self.assertEqual(self.ui.session_state['gw_guideline_editor'], 'new')
        self.assertEqual(self.ui.reruns, 1)

    def test_row_selection_opens_the_original_versioned_editor(self):
        self.ui.grid_rows = [0]
        page._table(self.rows)
        self.assertEqual(self.ui.session_state['gw_guideline_editor'], 8)
        self.assertEqual(self.ui.session_state['gw_guideline_snapshot'], ROW)
        self.assertEqual(self.ui.reruns, 0)  # data-frame selection already reran the page
        grid, options = self.ui.grid_events[0]
        self.assertEqual(options['selection_mode'], 'single-row')
        self.assertEqual(options['on_select'], 'rerun')
        self.assertEqual(grid.data.columns.tolist(),
                         ['No.', 'Item Type', 'Category', 'Brand Tier', 'Condition', 'Suggested Price'])

    def test_column_value_filter_removes_nonmatching_rows(self):
        self.ui.selects['gw_filter_type'] = ['Furniture']
        page._table(self.rows)
        self.assertEqual(len(self.ui.grid_events), 0)
        self.assertTrue(self.ui.infos)

    def test_header_click_sets_explicit_first_priority(self):
        self.ui.buttons['gw_sort_category'] = True
        page._table(self.rows)
        self.assertEqual(self.ui.session_state['gw_guideline_sorts'], [('category','asc')])
        self.assertTrue(self.ui.reruns)

    def test_new_grid_key_on_filter_change_prevents_stale_selection(self):
        page._table(self.rows)
        key_a = self.ui.grid_events[-1][1]['key']
        self.ui.selects['gw_filter_brand'] = ["Levi's"]
        page._table(self.rows)
        key_b = self.ui.grid_events[-1][1]['key']
        self.assertNotEqual(key_a, key_b)

    def test_row_colors_active_green_and_inactive_gray(self):
        inactive = deepcopy(ROW)
        inactive['Guideline_Id'] = 9
        inactive['Guideline_Is_Active'] = 0
        page._table([self.rows[0], inactive])
        styler, _ = self.ui.grid_events[-1]
        styles = styler.to_html()
        self.assertIn('#e5f4ec', styles)
        self.assertIn('#edf0f3', styles)

    def test_saved_fields_are_not_injected_into_raw_html(self):
        malicious = deepcopy(ROW)
        malicious['Category_Name'] = '<img src=x onerror=alert(1)>'
        page._table([malicious])
        markup = ''.join(self.ui.styles)
        self.assertNotIn('<img', markup)
        self.assertEqual(self.ui.grid_events[-1][0].data['Category'].iloc[0],
                         '<img src=x onerror=alert(1)>')

    def test_new_guideline_form_saves_selected_ids_and_price(self):
        self.ui.session_state['gw_guideline_editor'] = 'new'
        self.ui.inputs['gw_form_new_0_price'] = '8.99'
        self.ui.buttons['Save Guideline'] = True
        with patch.object(page, 'save_guideline', return_value=9) as save:
            page._editor(ADMIN, self.lookups, self.rows)
        self.assertEqual(save.call_args.args[0], ADMIN)
        self.assertEqual(save.call_args.args[1], {
            'Item_Type_Id': 4, 'Category_Id': 1, 'Brand_Id': 2, 'Condition_Id': 3,
        })
        self.assertEqual(save.call_args.args[2], '8.99')
        self.assertTrue(save.call_args.args[3])
        self.assertIsNone(save.call_args.kwargs['guideline_id'])
        self.assertNotIn('gw_guideline_editor', self.ui.session_state)
        self.assertTrue(self.ui.reruns)

    def test_duplicate_save_displays_existing_guideline_id_in_right_editor(self):
        self.ui.session_state['gw_guideline_editor'] = 'new'
        self.ui.inputs['gw_form_new_0_price'] = '12.99'
        self.ui.buttons['Save Guideline'] = True
        message = ('Guideline #337 already exists (Active) with identical attributes '
                   'and Suggested Price ($12.99). No new guideline was created.')
        with patch.object(page, 'save_guideline', side_effect=page.GuidelineError(message)) as save:
            page._editor(ADMIN, self.lookups, self.rows)
        save.assert_called_once()
        self.assertTrue(self.ui.errors)
        self.assertIn('Guideline #337', self.ui.errors[-1])
        self.assertIn('No new guideline was created', self.ui.errors[-1])
        self.assertEqual(self.ui.session_state['gw_guideline_editor'], 'new')
        self.assertEqual(self.ui.reruns, 0)

    def test_edit_uses_expected_snapshot_token_for_versioning(self):
        self.ui.session_state['gw_guideline_editor'] = 8
        self.ui.inputs['gw_form_8_0_price'] = '19.99'
        self.ui.buttons['Save Guideline'] = True
        with patch.object(page, 'save_guideline', return_value=9) as save:
            page._editor(ADMIN, self.lookups, self.rows)
        self.assertEqual(save.call_args.kwargs['guideline_id'], 8)
        self.assertEqual(save.call_args.kwargs['expected_token'], snapshot_token(ROW))

    def test_stale_snapshot_blocks_save_until_reloaded(self):
        self.ui.session_state['gw_guideline_editor'] = 8
        self.ui.session_state['gw_guideline_snapshot'] = {
            **ROW, 'Guideline_Suggested_Price': Decimal('10.00')
        }
        self.ui.buttons['Save Guideline'] = True
        self.ui.buttons['gw_reload_selected'] = True
        with patch.object(page, 'save_guideline') as save:
            page._editor(ADMIN, self.lookups, self.rows)
        save.assert_not_called()
        self.assertEqual(self.ui.session_state['gw_guideline_snapshot'], ROW)
        self.assertTrue(self.ui.reruns)

    def test_status_button_deactivates_existing_guideline(self):
        self.ui.session_state['gw_guideline_editor'] = 8
        self.ui.buttons['gw_toggle_8'] = True
        with patch.object(page, 'set_guideline_active') as status:
            page._editor(ADMIN, self.lookups, self.rows)
        status.assert_called_once_with(ADMIN, 8, False, snapshot_token(ROW))
        self.assertTrue(self.ui.reruns)

    def test_missing_lookup_blocks_creation_before_form(self):
        self.ui.session_state['gw_guideline_editor'] = 'new'
        self.lookups['Brand_Id'] = {}
        with patch.object(page, 'save_guideline') as save:
            page._editor(ADMIN, self.lookups, self.rows)
        save.assert_not_called()
        self.assertTrue(any('Brand' in warning for warning in self.ui.warnings))

    def test_editor_is_beside_table_and_report_section_is_removed(self):
        with patch.object(page, 'load_catalog', return_value=(self.lookups, self.rows)):
            page.show_guideline_management(ADMIN, show_header=False)
        self.assertTrue(any(spec == [3.1, 1.2] for spec, _ in self.ui.column_specs))
        self.assertIn('gw_guideline_editor_slot', self.ui.containers)
        self.assertNotIn('gw_report_panel', self.ui.containers)
        self.assertNotIn('Most Common Override Reasons', ''.join(self.ui.styles))

    def test_compact_counts_inside_table_header_and_no_overview_card(self):
        inactive = deepcopy(ROW)
        inactive['Guideline_Id'] = 9
        inactive['Guideline_Is_Active'] = 0
        self.rows.append(inactive)
        with patch.object(page, 'load_catalog', return_value=(self.lookups, self.rows)):
            page.show_guideline_management(ADMIN, show_header=False)
        keys = self.ui.containers
        self.assertNotIn('gw_guideline_summary', keys)
        self.assertLess(keys.index('gw_guideline_table'), keys.index('gw_guideline_editor_slot'))
        add_calls = [label for label, kwargs in self.ui.button_calls
                     if kwargs.get('key') == 'gw_add_guideline']
        self.assertEqual(add_calls, ['＋  Add New Guideline'])
        combined = ''.join(str(v) for v in self.ui.styles)
        self.assertNotIn('Guideline Overview', combined)
        self.assertIn('1 categories covered', combined)
        self.assertIn('Active Guidelines <strong>1</strong>', combined)
        self.assertIn('Inactive Guidelines <strong>1</strong>', combined)

    def test_active_counts_and_categories_reflect_unfiltered_source_rows(self):
        second = deepcopy(ROW)
        second['Guideline_Id'] = 10
        second['Category_Id'] = 5
        second['Guideline_Is_Active'] = 1
        inactive = deepcopy(ROW)
        inactive['Guideline_Id'] = 11
        inactive['Guideline_Is_Active'] = 0
        self.rows.extend([second, inactive])
        # Counts stay global when a field filter hides rows in the dataframe.
        self.ui.selects['gw_filter_status'] = 'Inactive'
        with patch.object(page, 'load_catalog', return_value=(self.lookups, self.rows)):
            page.show_guideline_management(ADMIN, show_header=False)
        combined = ''.join(str(v) for v in self.ui.styles)
        self.assertIn('2 categories covered', combined)
        self.assertIn('Active Guidelines <strong>2</strong>', combined)
        self.assertIn('Inactive Guidelines <strong>1</strong>', combined)

    def test_status_dropdown_only_shows_active_or_inactive(self):
        inactive = deepcopy(ROW)
        inactive['Guideline_Id'] = 9
        inactive['Guideline_Is_Active'] = 0
        for status, expected in [('All', [9, 8]), ('Active', [8]), ('Inactive', [9])]:
            with self.subTest(status=status):
                self.ui.selects['gw_filter_status'] = status
                self.ui.grid_events.clear()
                page._table([self.rows[0], inactive])
                grid, _ = self.ui.grid_events[-1]
                self.assertEqual(grid.data['No.'].tolist(), expected)
                self.assertNotIn('Status', grid.data.columns)

    def test_status_filter_change_resets_grid_key(self):
        page._table(self.rows)
        first = self.ui.grid_events[-1][1]['key']
        self.ui.selects['gw_filter_status'] = 'Active'
        page._table(self.rows)
        second = self.ui.grid_events[-1][1]['key']
        self.assertNotEqual(first, second)


if __name__ == '__main__':
    unittest.main()
