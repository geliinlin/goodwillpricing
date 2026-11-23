"""Regression checks for one-to-many Item Type -> Category ownership (no DB writes)."""
from pathlib import Path
import unittest
from unittest.mock import MagicMock, patch

import configuration_service as config
import configuration_page as page
import guidelines
import item_search
from _goodwill_test_helpers import ScriptedConnection

ROOT = Path(__file__).resolve().parents[1]
ADMIN = {'user_id': 7, 'user_type': 'Administrator', 'can_manage_users_config': True}


class ItemTypeCategoryChecks(unittest.TestCase):
    def test_category_requires_positive_parent_type_id(self):
        spec = config.SPECS['categories']
        for value in (None, 0, True, -3, '4'):
            with self.subTest(value=value), self.assertRaises(config.ConfigurationError):
                config._validated(spec, {'Category_Name': 'Chairs', 'Item_Type_Id': value})
        result = config._validated(spec, {'Category_Name': ' Chairs ', 'Item_Type_Id': 4})
        self.assertEqual(result['Item_Type_Id'], 4)
        self.assertEqual(result['Category_Name'], 'Chairs')

    def test_category_snapshot_includes_item_type(self):
        row = {'Category_Id': 1, 'Category_Name': 'Tables', 'Category_Is_Active': 1,
               'Category_Description': '', 'Item_Type_Id': 3}
        self.assertNotEqual(config.snapshot_for_row('categories', row),
                            config.snapshot_for_row('categories', {**row, 'Item_Type_Id': 9}))

    def test_duplicate_category_only_within_the_same_item_type(self):
        cursor = MagicMock()
        cursor.fetchall.return_value = [
            {'Category_Id': 11, 'Category_Name': 'Accessories', 'Item_Type_Id': 1},
        ]
        fields = {'Category_Name': ' accessories ', 'Item_Type_Id': 2,
                  'Category_Description': ''}
        config._duplicates(cursor, config.SPECS['categories'], fields)
        with self.assertRaisesRegex(config.ConfigurationError, '#11'):
            config._duplicates(cursor, config.SPECS['categories'], {**fields, 'Item_Type_Id': 1})

    def test_moving_used_category_to_different_item_type_blocked(self):
        cursor = MagicMock()
        cursor.fetchone.return_value = {'n': 2}
        with self.assertRaisesRegex(config.ConfigurationError, 'Cannot change Item Type'):
            config._ensure_category_unreferenced(cursor, 2)
        self.assertIn('PRICING_GUIDELINE', cursor.execute.call_args.args[0])

    def test_parent_type_must_be_active(self):
        cursor = MagicMock()
        cursor.fetchone.return_value = None
        with self.assertRaisesRegex(config.ConfigurationError, 'active Item Type'):
            config._require_active_type(cursor, 4)
        self.assertIn('Item_Type_Is_Active=1', cursor.execute.call_args.args[0])

    def test_configuration_page_has_category_parent_dropdown_and_column(self):
        spec = config.SPECS['categories']
        types = [{'Item_Type_Id': 4, 'Item_Type_Name': 'Furniture', 'Item_Type_Is_Active': 1}]
        with patch.object(page.st, 'text_input', return_value='Tables'), \
             patch.object(page.st, 'selectbox', return_value=4), \
             patch.object(page.st, 'text_area', return_value='', create=True):
            fields = page._editing_fields(spec, {}, 'test', types)
        self.assertEqual(fields['Item_Type_Id'], 4)
        content = (ROOT / 'configuration_page.py').read_text()
        self.assertIn("entry['Item Type']", content)
        self.assertIn("load_master_data(user, 'item_types')", content)

    def test_staff_lookup_has_association_without_extra_roundtrip(self):
        connection = ScriptedConnection([
            [{'id': 1, 'name': 'Clothing'}],
            [{'id': 21, 'name': 'Accessories', 'parent_type': 1}],
            [{'id': 2, 'name': 'Designer'}],
            [{'id': 3, 'name': 'Good'}],
        ])
        out = item_search.get_lookup_options(lambda: connection)
        self.assertEqual(out['Category_Item_Type'], {21: 1})
        self.assertEqual(len(connection.statements), 4)

    def test_backend_guide_query_verifies_category_item_type_pair(self):
        cursor = MagicMock()
        cursor.fetchone.return_value = {'Category_Id': 2}
        attrs = {'Category_Id': 2, 'Brand_Id': 3, 'Condition_Id': 4, 'Item_Type_Id': 5}
        guidelines._validate_attributes(cursor, attrs)
        stmt = cursor.execute.call_args_list[0]
        self.assertIn('AND Item_Type_Id = %s', stmt.args[0])
        self.assertIn('LOCK IN SHARE MODE', stmt.args[0])
        self.assertEqual(stmt.args[1], (2, 5))

    def test_mariadb_share_locks_are_supported(self):
        self.assertNotIn('FOR SHARE', (ROOT/'guidelines.py').read_text())
        self.assertNotIn('FOR SHARE', (ROOT/'configuration_service.py').read_text())

    def test_migration_preserves_old_data_and_enforces_consistency(self):
        sql = (ROOT / 'migrations/004_category_item_type.sql').read_text()
        self.assertIn('COUNT(DISTINCT Item_Type_Id) > 1', sql)
        self.assertIn('fk_guideline_type_category_pair', sql)
        self.assertIn('fk_item_type_category_pair', sql)
        self.assertIn('uq_category_type_name', sql)
        self.assertNotIn('DROP TABLE CATEGORY', sql)
        self.assertNotIn('DELETE FROM CATEGORY', sql)

    def test_seed_is_repeatable_and_uses_expected_categories(self):
        sql = (ROOT / 'migrations/005_seed_item_types_categories.sql').read_text()
        self.assertIn('IF NOT EXISTS (SELECT 1 FROM CATEGORY', sql)
        self.assertIn('GET_LOCK', sql)
        self.assertIn('Furniture', sql)
        self.assertIn('Home & Kitchen', sql)
        self.assertIn('Electronics', sql)
        self.assertIn('Outdoor Gear', sql)


if __name__ == '__main__':
    unittest.main()
