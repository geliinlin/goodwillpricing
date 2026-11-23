"""Master-data Configuration V1 offline tests; no real MySQL connection."""
import unittest
from unittest.mock import MagicMock, patch

import configuration_service as service

ADMIN = {'user_id': 7, 'user_type': 'Administrator', 'can_manage_users_config': True}
STAFF = {'user_id': 8, 'user_type': 'Store Staff', 'can_manage_users_config': True}
PLAIN_ADMIN = {'user_id': 9, 'user_type': 'Administrator', 'can_manage_users_config': False}


class ConfigurationValidationTests(unittest.TestCase):
    def test_five_sections_exist(self):
        self.assertEqual(set(service.SPECS), {'item_types', 'categories', 'brands', 'conditions', 'branches'})

    def test_dynamic_table_identifiers_only_from_allowlist(self):
        with self.assertRaises(service.ConfigurationError):
            service.create_master_data(ADMIN, 'ITEM_TYPE; DROP TABLE USER_ACCOUNT', {'Item_Type_Name': 'Furniture'})

    def test_master_names_normalized_and_validated(self):
        spec = service.SPECS['item_types']
        self.assertEqual(service._validated(spec, {'Item_Type_Name': ' Home  Furniture '})['Item_Type_Name'], 'Home Furniture')
        for value in (None, '', '  ', 'a' * 51):
            with self.subTest(value=value), self.assertRaises(service.ConfigurationError):
                service._validated(spec, {'Item_Type_Name': value})

    def test_branch_address_and_state_validation(self):
        good = {'Branch_Name': 'Goodwill Lansing', 'Branch_Street': '12 Main St',
                'Branch_City': 'Lansing', 'Branch_State': 'mi', 'Branch_Zip': '48901'}
        self.assertEqual(service._validated(service.SPECS['branches'], good)['Branch_State'], 'MI')
        for field, value in (('Branch_State', 'Michigan'), ('Branch_Zip', 'ABCDE'), ('Branch_Street', '')):
            bad = {**good, field: value}
            with self.subTest(field=field), self.assertRaises(service.ConfigurationError):
                service._validated(service.SPECS['branches'], bad)

    def test_snapshot_changes_when_name_or_status_changes(self):
        row = {'Category_Id': 1, 'Category_Name': 'Shoes', 'Category_Description': 'Fine',
               'Category_Is_Active': 1}
        a = service.snapshot_for_row('categories', row)
        b = service.snapshot_for_row('categories', {**row, 'Category_Is_Active': 0})
        self.assertNotEqual(a, b)
        self.assertEqual(len(a), 64)

    def test_staff_and_ordinary_admin_cannot_open_database(self):
        factory = MagicMock()
        for actor in (STAFF, PLAIN_ADMIN):
            with self.subTest(actor=actor), self.assertRaises(PermissionError):
                service.load_master_data(actor, 'branches', connection_factory=factory)
            with self.assertRaises(PermissionError):
                service.create_master_data(actor, 'item_types', {'Item_Type_Name': 'Furniture'},
                                           connection_factory=factory)
        factory.assert_not_called()


class ConfigurationWriteTests(unittest.TestCase):
    def setUp(self):
        self.conn = MagicMock()
        self.cur = self.conn.cursor.return_value
        self.factory = MagicMock(return_value=self.conn)
        auth = patch.object(service, '_read_actor', return_value='Administrator')
        auth.start()
        self.addCleanup(auth.stop)

    def _calls(self, fragment):
        return [c for c in self.cur.execute.call_args_list if fragment in c.args[0]]

    def test_new_item_type_inserts_and_audits(self):
        self.cur.fetchone.side_effect = [{'acquired': 1}, {'next_id': 19}, {'released': 1}]
        self.cur.fetchall.return_value = []
        out = service.create_master_data(ADMIN, 'item_types', {'Item_Type_Name': ' Furniture '},
                                         connection_factory=self.factory)
        self.assertEqual(out, 19)
        inserted = self._calls('INSERT INTO ITEM_TYPE')[0].args[1]
        self.assertEqual(inserted, (19, 'Furniture', '', 1))
        self.assertEqual(len(self._calls('INSERT INTO MASTER_DATA_AUDIT')), 1)
        self.conn.commit.assert_called_once()
        self.assertEqual(len(self._calls('RELEASE_LOCK')), 1)

    def test_inactive_name_duplicate_is_rejected_transactionally(self):
        self.cur.fetchone.side_effect = [{'acquired': 1}, {'released': 1}]
        self.cur.fetchall.return_value = [{'Item_Type_Id': 6, 'Item_Type_Name': 'fUrNiTuRe'}]
        with self.assertRaisesRegex(service.ConfigurationError, '#6'):
            service.create_master_data(ADMIN, 'item_types', {'Item_Type_Name': ' Furniture '},
                                       connection_factory=self.factory)
        self.assertFalse(self._calls('INSERT INTO ITEM_TYPE'))
        self.conn.rollback.assert_called_once()

    def test_branch_duplicate_address_is_rejected(self):
        self.cur.fetchone.side_effect = [{'acquired': 1}, {'released': 1}]
        self.cur.fetchall.return_value = [{
            'Branch_Id': 3, 'Branch_Name': 'Old branch', 'Branch_Street': '12 MAIN ST',
            'Branch_City': 'Lansing', 'Branch_State': 'MI', 'Branch_Zip': '48901'
        }]
        payload = {'Branch_Name': 'New branch', 'Branch_Street': '12 Main St',
                   'Branch_City': 'Lansing', 'Branch_State': 'MI', 'Branch_Zip': '48901'}
        with self.assertRaisesRegex(service.ConfigurationError, 'address'):
            service.create_master_data(ADMIN, 'branches', payload, connection_factory=self.factory)

    def test_deactivate_referenced_active_guideline_denied(self):
        row = {'Category_Id': 2, 'Category_Name': 'Clothing',
               'Category_Description': '', 'Category_Is_Active': 1}
        self.cur.fetchone.side_effect = [{'acquired': 1}, row, {'n': 3}, {'released': 1}]
        with self.assertRaisesRegex(service.ConfigurationError, 'active pricing guidelines'):
            service.set_master_active(ADMIN, 'categories', 2, False,
                                      service.snapshot_for_row('categories', row),
                                      connection_factory=self.factory)
        self.assertFalse(self._calls('UPDATE CATEGORY SET'))
        self.conn.commit.assert_not_called()

    def test_deactivate_branch_with_any_assigned_staff_denied(self):
        row = {'Branch_Id': 2, 'Branch_Name': 'Goodwill South', 'Branch_Street': '12 Main St',
               'Branch_City': 'Lansing', 'Branch_State': 'MI', 'Branch_Zip': '48901',
               'Branch_Is_Active': 1}
        self.cur.fetchone.side_effect = [{'acquired': 1}, row, {'n': 1}, {'released': 1}]
        with self.assertRaisesRegex(service.ConfigurationError, 'assigned staff'):
            service.set_master_active(ADMIN, 'branches', 2, False,
                                     service.snapshot_for_row('branches', row),
                                     connection_factory=self.factory)
        self.assertFalse(self._calls('UPDATE BRANCH SET'))

    def test_deactivation_with_no_active_dependents_logs_audit(self):
        row = {'Item_Type_Id': 9, 'Item_Type_Name': 'Rugs', 'Item_Type_Is_Active': 1}
        self.cur.fetchone.side_effect = [{'acquired': 1}, row, {'n': 0}, {'released': 1}]
        service.set_master_active(ADMIN, 'item_types', 9, False,
                                  service.snapshot_for_row('item_types', row),
                                  connection_factory=self.factory)
        self.assertEqual(self._calls('UPDATE ITEM_TYPE SET')[0].args[1], (0, 9))
        self.assertEqual(self._calls('INSERT INTO MASTER_DATA_AUDIT')[0].args[1][3], 'DEACTIVATE')
        self.conn.commit.assert_called_once()

    def test_stale_snapshot_blocks_update(self):
        row = {'Brand_Id': 3, 'Brand_Name': 'Generic', 'Brand_Description': '',
               'Brand_Is_Active': 1}
        self.cur.fetchone.side_effect = [{'acquired': 1}, row, {'released': 1}]
        with self.assertRaisesRegex(service.ConfigurationError, 'changed since'):
            service.update_master_data(ADMIN, 'brands', 3,
                                       {'Brand_Name': 'New Brand', 'Brand_Description': ''},
                                       'a' * 64, connection_factory=self.factory)
        self.assertFalse(self._calls('UPDATE BRAND SET'))

    def test_db_revalidation_cannot_be_bypassed_with_session_flag(self):
        with patch.object(service, '_read_actor', side_effect=PermissionError('Access revoked')):
            self.cur.fetchone.side_effect = [{'acquired': 1}, {'released': 1}]
            with self.assertRaises(PermissionError):
                service.create_master_data(ADMIN, 'item_types', {'Item_Type_Name': 'Furniture'},
                                           connection_factory=self.factory)
            self.conn.rollback.assert_called_once()
            self.assertFalse(self._calls('INSERT INTO ITEM_TYPE'))


class ConfigurationWiringTests(unittest.TestCase):
    def test_router_loader_and_migration_presence(self):
        from pathlib import Path
        root = Path(__file__).resolve().parents[1]
        self.assertIn('show_configuration(user)', (root / 'dashboard_router.py').read_text())
        self.assertIn('"configuration.css"', (root / 'style_loader.py').read_text())
        script = (root / 'migrations/003_master_data_management.sql').read_text()
        self.assertIn('MASTER_DATA_AUDIT', script)
        for name in ('Item_Type_Is_Active', 'Category_Is_Active', 'Brand_Is_Active',
                     'Condition_Is_Active', 'Branch_Is_Active'):
            self.assertIn(name, script)

    def test_pricing_looks_up_active_only(self):
        from pathlib import Path
        root = Path(__file__).resolve().parents[1]
        self.assertIn('WHERE `{active_col}`=1', (root / 'item_search.py').read_text())
        self.assertIn('AND {active_field} = 1', (root / 'guidelines.py').read_text())
        self.assertIn('Branch_Is_Active=1', (root / 'users_service.py').read_text())


if __name__ == '__main__':
    unittest.main()
