"""Offline regression checks for master-data descriptions and staff reference."""
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import configuration_service as config
import configuration_page as config_page
import staff_guidelines_page as staff_page
import staff_guidelines_service as staff_service
from dashboard_data import DashboardPermissionError
from _goodwill_test_helpers import ScriptedConnection

ROOT = Path(__file__).resolve().parents[1]
STAFF = {'user_id': 11, 'user_type': 'Store Staff'}
ADMIN = {'user_id': 4, 'user_type': 'Administrator'}
ACTIVE_STAFF = {'User_Type': 'Store Staff', 'User_Is_Active': 1,
                'Admin_Id': None, 'Staff_Id': 11}


class AdminDescriptionTests(unittest.TestCase):
    def test_all_five_attributes_have_description_field(self):
        self.assertEqual(config.SPECS['item_types'].detail, ('Item_Type_Description',))
        self.assertIn('Category_Description', config.SPECS['categories'].detail)
        self.assertIn('Brand_Description', config.SPECS['brands'].detail)
        self.assertIn('Condition_Description', config.SPECS['conditions'].detail)
        self.assertIn('Branch_Description', config.SPECS['branches'].detail)

    def test_item_type_description_stored_and_snapshotted(self):
        spec = config.SPECS['item_types']
        fields = config._validated(spec, {'Item_Type_Name': ' Furniture ',
                                          'Item_Type_Description': ' Home furniture  '})
        self.assertEqual(fields['Item_Type_Description'], 'Home furniture')
        old = {'Item_Type_Id': 1, 'Item_Type_Name': 'Furniture',
               'Item_Type_Is_Active': 1, 'Item_Type_Description': 'Old'}
        self.assertNotEqual(config.snapshot_for_row('item_types', old),
                            config.snapshot_for_row('item_types', dict(old, Item_Type_Description='New')))

    def test_editing_description_is_written_and_audited(self):
        cur = MagicMock()
        conn = MagicMock()
        conn.cursor.return_value = cur
        old = {'Brand_Id': 3, 'Brand_Name': 'Generic',
               'Brand_Description': 'Old meaning', 'Brand_Is_Active': 1}
        cur.fetchone.side_effect = [{'acquired': 1}, old, {'released': 1}]
        cur.fetchall.return_value = []
        actor = {'user_id': 7, 'user_type': 'Administrator',
                 'can_manage_users_config': True}
        with patch.object(config, '_read_actor', return_value='Administrator'):
            config.update_master_data(actor, 'brands', 3,
                {'Brand_Name': 'Generic', 'Brand_Description': 'Updated meaning'},
                config.snapshot_for_row('brands', old),
                connection_factory=lambda: conn)
        stmts = [call.args[0] for call in cur.execute.call_args_list]
        self.assertTrue(any('UPDATE BRAND SET' in q and 'Brand_Description=%s' in q
                            for q in stmts))
        self.assertTrue(any('INSERT INTO MASTER_DATA_AUDIT' in q for q in stmts))
        conn.commit.assert_called_once()

    def test_description_limit_and_optional_blank(self):
        spec = config.SPECS['brands']
        self.assertEqual(config._validated(spec, {'Brand_Name': 'A'})['Brand_Description'], '')
        with self.assertRaisesRegex(config.ConfigurationError, 'at most 255'):
            config._validated(spec, {'Brand_Name': 'A', 'Brand_Description': 'a'*256})

    def test_branch_duplicate_check_does_not_depend_on_description(self):
        spec = config.SPECS['branches']
        cur = MagicMock()
        cur.fetchall.return_value = [
            {'Branch_Id': 4, 'Branch_Name': 'Old', 'Branch_Street': '100 Main St',
             'Branch_City': 'Lansing', 'Branch_State': 'MI', 'Branch_Zip': '48901',
             'Branch_Description': 'Old description'}
        ]
        fields = config._validated(spec, {'Branch_Name': 'New', 'Branch_Street': '100 Main St',
                                          'Branch_City': 'Lansing', 'Branch_State': 'MI',
                                          'Branch_Zip': '48901',
                                          'Branch_Description': 'Different description'})
        with self.assertRaisesRegex(config.ConfigurationError, 'address'):
            config._duplicates(cur, spec, fields)

    def test_admin_editor_supports_branch_description(self):
        spec = config.SPECS['branches']
        with patch.object(config_page, 'st') as st:
            st.text_input.return_value = 'x'
            st.text_area.return_value = 'Training branch'
            fields = config_page._editing_fields(spec, {}, 'branch_test')
        self.assertEqual(fields['Branch_Description'], 'Training branch')
        st.text_area.assert_called_once()

    def test_admin_config_table_includes_description(self):
        source = (ROOT / 'configuration_page.py').read_text()
        self.assertIn("entry['Description']", source)
        self.assertIn("'Branch_Description'", source)

    def test_migration_is_additive_idempotent(self):
        sql = (ROOT/'migrations/006_master_descriptions.sql').read_text()
        self.assertIn('Item_Type_Description VARCHAR(255)', sql)
        self.assertIn('Branch_Description VARCHAR(255)', sql)
        self.assertIn('information_schema.COLUMNS', sql)
        self.assertNotIn('DROP TABLE', sql)
        self.assertNotIn('DELETE FROM', sql)


class StaffDescriptionTests(unittest.TestCase):
    def test_all_five_groups_return_active_rows_read_only(self):
        ref = [{'attribute_group': 'Item Types', 'attribute_id': 1,
                'attribute_name': 'Furniture', 'description': 'Furniture items',
                'item_type_name': None}]
        conn = ScriptedConnection([[ACTIVE_STAFF], ref])
        result = staff_service.get_staff_attribute_descriptions(STAFF, lambda: conn)
        self.assertEqual(result, ref)
        self.assertTrue(conn.closed)
        self.assertEqual(conn.commits, 0)
        sql = conn.statements[1][0]
        for table in ['ITEM_TYPE','CATEGORY','BRAND','ITEM_CONDITION','BRANCH']:
            self.assertIn(table, sql)
        self.assertIn('Category_Description', sql)
        self.assertIn('Branch_Description', sql)
        self.assertIn('Is_Active = 1', sql)
        self.assertNotIn('UPDATE ', sql)
        self.assertNotIn('DELETE ', sql)
        self.assertNotIn('INSERT ', sql)

    def test_admin_is_rejected_before_db(self):
        factory = MagicMock()
        with self.assertRaises(DashboardPermissionError):
            staff_service.get_staff_attribute_descriptions(ADMIN, factory)
        factory.assert_not_called()

    def test_forged_staff_flag_denied_by_db(self):
        conn = ScriptedConnection([[{'User_Type': 'Administrator', 'User_Is_Active': 1,
                                    'Admin_Id': 11, 'Staff_Id': None}]])
        with self.assertRaises(DashboardPermissionError):
            staff_service.get_staff_attribute_descriptions(STAFF, lambda: conn)
        self.assertEqual(len(conn.statements), 1)

    def test_category_reference_has_parent_and_filter(self):
        source = [
            {'attribute_group': 'Categories', 'attribute_name': 'Chairs',
             'description': 'Seats', 'item_type_name': 'Furniture'},
            {'attribute_group': 'Categories', 'attribute_name': 'Sneakers',
             'description': 'Shoes', 'item_type_name': 'Clothing'},
            {'attribute_group': 'Branches', 'attribute_name': 'Lansing',
             'description': 'Store', 'item_type_name': None},
        ]
        self.assertEqual(len(staff_service.filter_attribute_descriptions(source, group='Categories')), 2)
        self.assertEqual(staff_service.filter_attribute_descriptions(source, group='Categories',
                             search='furniture')[0]['attribute_name'], 'Chairs')
        self.assertEqual(staff_service.filter_attribute_descriptions(source, group='Branches',
                             search='store')[0]['attribute_name'], 'Lansing')
        self.assertEqual(len(source), 3)

    def test_staff_reference_is_lazy_and_read_only(self):
        with patch.object(staff_page, 'st') as st, \
             patch.object(staff_page, 'get_staff_attribute_descriptions') as load:
            st.session_state = {}
            st.button.return_value = False
            staff_page._attribute_reference(STAFF)
        load.assert_not_called()

    def test_staff_reference_open_renders_descriptions_without_edit(self):
        example = [{'attribute_group': 'Categories', 'attribute_name': 'Chairs',
                    'description': 'A seat', 'item_type_name': 'Furniture'}]
        with patch.object(staff_page, 'st') as st, \
             patch.object(staff_page, 'get_staff_attribute_descriptions', return_value=example):
            st.session_state = {}
            staff_page._attribute_reference(STAFF)
            self.assertEqual(st.button.call_args.args[0], 'View Attribute Descriptions')
            st.button.call_args.kwargs['on_click']()
            st.selectbox.return_value = 'Categories'
            st.text_input.return_value = ''
            staff_page._attribute_reference(STAFF)
            self.assertEqual(st.button.call_args.args[0], 'Hide Attribute Descriptions')
            st.button.call_args.kwargs['on_click']()
            staff_page._attribute_reference(STAFF)
            self.assertEqual(st.button.call_args.args[0], 'View Attribute Descriptions')
        self.assertEqual(st.dataframe.call_args.args[0][0]['Description'], 'A seat')
        self.assertEqual(st.dataframe.call_args.args[0][0]['Item Type'], 'Furniture')
