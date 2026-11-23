"""Users/Configuration rights are tested without any live database."""
import unittest
from unittest.mock import patch

from _goodwill_test_helpers import ScriptedConnection
import users_service as svc
import auth

ADMIN = {'user_id': 1, 'user_type': 'Administrator', 'can_manage_users_config': True}
STAFF = {'user_id': 2, 'user_type': 'Store Staff', 'can_manage_users_config': True}


def actor_reply(role='Administrator', allowed=1):
    return {'User_Type': role, 'User_Is_Active': 1,
            'Admin_Id': 1 if role == 'Administrator' else None,
            'Staff_Id': 2 if role == 'Store Staff' else None,
            'can_manage': allowed}


def create_reply(role='Administrator', grant=False, has_branch=True):
    queries = [[{'acquired': 1}], [actor_reply(role)]]
    queries.extend([[{'Branch_Id': 3}], []] if has_branch else [[]])
    queries.extend([[], [{'next_id': 55}], [], []])
    if grant:
        queries.append([])
    queries.append([{'released': 1}])
    return queries


class UsersServiceTests(unittest.TestCase):
    def test_session_flag_is_navigation_hint_not_authority(self):
        self.assertFalse(svc.has_management_access({'user_type': 'Administrator'}))
        self.assertTrue(svc.has_management_access(ADMIN))
        self.assertFalse(svc.has_management_access(STAFF))
        self.assertFalse(svc.has_management_access(None))

    def test_staff_directory_rejected_before_database_even_with_flag(self):
        with patch.object(svc, 'get_connection') as connect:
            with self.assertRaises(svc.UserManagementPermissionError):
                svc.load_user_directory(STAFF)
            connect.assert_not_called()

    def test_admin_directory_lists_all_roles_and_rechecks_db(self):
        con = ScriptedConnection([
            [actor_reply()],
            [{'User_Id': 2, 'User_Type': 'Store Staff'}],
            [{'Branch_Id': 3, 'Branch_Name': 'Mount Pleasant'}],
        ])
        directory = svc.load_user_directory(ADMIN, connection_factory=lambda: con)
        self.assertTrue(directory['can_grant_access'])
        self.assertNotIn("WHERE u.User_Type='Store Staff'", con.statements[1][0])
        self.assertIn('LEFT JOIN ADMINISTRATOR', con.statements[1][0])
        self.assertTrue(con.closed)
        self.assertTrue(all(c.closed for c in con.cursors))

    def test_revoked_actor_cannot_read_directory(self):
        con = ScriptedConnection([[actor_reply(allowed=0)]])
        with self.assertRaises(svc.UserManagementPermissionError):
            svc.load_user_directory(ADMIN, connection_factory=lambda: con)
        self.assertEqual(len(con.statements), 1)

    @patch.object(svc, 'hash_password', return_value='bcrypt-hash')
    def test_admin_creates_manager_with_subtype_transaction_and_temp_password(self, hashed):
        con = ScriptedConnection(create_reply(grant=True, has_branch=False))
        result = svc.create_user_account(ADMIN, first_name='Jane', last_name='Smith',
            username='jsmith', email='Jane@example.org', user_type='Administrator',
            branch_id=None, management_access=True, connection_factory=lambda: con,
            password_factory=lambda: 'one-time-password')
        self.assertEqual(result['user_id'], 55)
        self.assertEqual(result['temporary_password'], 'one-time-password')
        self.assertEqual(con.commits, 1)
        self.assertEqual(con.rollbacks, 0)
        self.assertTrue(con.closed)
        self.assertTrue(any('INSERT INTO USER_ACCOUNT' in s for s, _ in con.statements))
        self.assertTrue(any('INSERT INTO ADMINISTRATOR' in s for s, _ in con.statements))
        self.assertTrue(any('INSERT INTO USER_MODULE_ACCESS' in s for s, _ in con.statements))
        self.assertEqual(con.statements[2][1], ('jsmith', 'jane@example.org'))
        self.assertTrue(any('RELEASE_LOCK' in s for s, _ in con.statements))
        hashed.assert_called_once_with('one-time-password')

    @patch.object(svc, 'hash_password', return_value='bcrypt-hash')
    def test_staff_cannot_create_any_user_even_if_session_flag_true(self, hashed):
        with patch.object(svc, 'get_connection') as opening:
            with self.assertRaises(svc.UserManagementPermissionError):
                svc.create_user_account(STAFF, first_name='Sam', last_name='Lee',
                    username='sam', email='sam@example.org', user_type='Store Staff', branch_id=3,
                    password_factory=lambda: 'pw')
            opening.assert_not_called()

    @patch.object(svc, 'hash_password', return_value='bcrypt-hash')
    def test_admin_cannot_grant_management_rights_to_staff(self, hashed):
        with patch.object(svc, 'get_connection') as opening:
            with self.assertRaisesRegex(svc.UserManagementError, 'cannot receive'):
                svc.create_user_account(ADMIN, first_name='Sam', last_name='Lee',
                    username='sam', email='sam@example.org', user_type='Store Staff',
                    branch_id=3, management_access=True, password_factory=lambda:'pw')
            opening.assert_not_called()

    @patch.object(svc, 'hash_password', return_value='bcrypt-hash')
    def test_duplicate_user_prevents_insert_and_rolls_back(self, hashed):
        con = ScriptedConnection([
            [{'acquired': 1}], [actor_reply()], [{'Branch_Id': 3}],
            [{'User_Id': 9}], [{'released': 1}]
        ])
        with self.assertRaisesRegex(svc.UserManagementError, 'already belongs'):
            svc.create_user_account(ADMIN, first_name='Sam', last_name='Lee',
                username='sam', email='sam@example.org', user_type='Store Staff',
                branch_id=3, connection_factory=lambda: con, password_factory=lambda:'pw')
        self.assertEqual(con.commits, 0)
        self.assertEqual(con.rollbacks, 1)
        self.assertFalse(any('INSERT' in sql for sql, _ in con.statements))

    def test_invalid_email_and_ids_fail_before_connection(self):
        with patch.object(svc, 'get_connection') as opening:
            with self.assertRaises(svc.UserManagementError):
                svc.create_user_account(ADMIN, first_name='A', last_name='B',
                    username='abc', email='bad-email', user_type='Store Staff', branch_id=3)
            with self.assertRaises(svc.UserManagementError):
                svc.set_account_active(ADMIN, -1, True)
            opening.assert_not_called()

    def test_staff_cannot_change_any_permission(self):
        with patch.object(svc, 'get_connection') as opening:
            with self.assertRaises(svc.UserManagementPermissionError):
                svc.set_management_access(STAFF, 9, True)
            opening.assert_not_called()

    def test_admin_can_grant_permission_to_another_admin(self):
        con = ScriptedConnection([
            [{'acquired':1}], [actor_reply()],
            [{'User_Id':9,'User_Type':'Administrator','User_Is_Active':1,'can_manage':0}],
            [], [{'released':1}]
        ])
        svc.set_management_access(ADMIN, 9, True, connection_factory=lambda: con)
        self.assertEqual(con.commits, 1)
        writes = [(sql,params) for sql,params in con.statements if 'INSERT INTO USER_MODULE_ACCESS' in sql]
        self.assertEqual(len(writes), 1)
        self.assertEqual(writes[0][1], (9, 1, 1))

    def test_admin_cannot_grant_permission_to_staff_even_for_false_bit(self):
        con = ScriptedConnection([
            [{'acquired':1}], [actor_reply()],
            [{'User_Id':9,'User_Type':'Store Staff','User_Is_Active':1,'can_manage':0}],
            [{'released':1}]
        ])
        with self.assertRaisesRegex(svc.UserManagementError, 'only available to Administrators'):
            svc.set_management_access(ADMIN, 9, True, connection_factory=lambda: con)
        self.assertFalse(any('INSERT INTO USER_MODULE_ACCESS' in sql for sql, _ in con.statements))
        self.assertEqual(con.rollbacks, 1)

    def test_actor_cannot_deactivate_own_account(self):
        con = ScriptedConnection([
            [{'acquired':1}], [actor_reply()],
            [{'User_Id':1,'User_Type':'Administrator','User_Is_Active':1,'can_manage':1}],
            [{'released':1}]
        ])
        with self.assertRaisesRegex(svc.UserManagementError, 'own account'):
            svc.set_account_active(ADMIN, 1, False, connection_factory=lambda: con)
        self.assertEqual(con.rollbacks, 1)

    @patch.object(svc, 'hash_password', return_value='bcrypt-hash')
    def test_staff_cannot_reset_any_password(self, hashed):
        with patch.object(svc, 'get_connection') as opening:
            with self.assertRaises(svc.UserManagementPermissionError):
                svc.reset_account_password(STAFF, 12, password_factory=lambda:'once')
            opening.assert_not_called()

    def test_session_actor_role_mismatch_is_denied(self):
        con = ScriptedConnection([[actor_reply('Store Staff')]])
        with self.assertRaises(svc.UserManagementPermissionError):
            svc.load_user_directory(ADMIN, connection_factory=lambda: con)

    def test_read_only_directory_never_writes(self):
        con = ScriptedConnection([[actor_reply()], [], []])
        svc.load_user_directory(ADMIN, connection_factory=lambda: con)
        self.assertFalse(any(sql.strip().upper().startswith(('INSERT','UPDATE','DELETE'))
                             for sql, _ in con.statements))
        self.assertEqual(con.commits, 0)


class AuthCapabilityTests(unittest.TestCase):
    def test_account_role_and_permission_are_distinct(self):
        base = {'User_Id': 10, 'User_First_Name':'A','User_Last_Name':'B',
            'User_Username':'a','User_Is_Active':1,'User_Type':'Store Staff',
            'Admin_Id':None,'Staff_Id':10,'Branch_Id':2,'Branch_Name':'Mount Pleasant'}
        denied = auth._to_session_user(dict(base, Can_Manage_Users_Config=0))
        allowed = auth._to_session_user(dict(base, Can_Manage_Users_Config=1))
        self.assertFalse(denied['can_manage_users_config'])
        self.assertFalse(allowed['can_manage_users_config'])
        self.assertEqual(allowed['user_type'], 'Store Staff')
        admin = dict(base, User_Type='Administrator', Admin_Id=10, Staff_Id=None,
                     Branch_Id=None, Can_Manage_Users_Config=1)
        self.assertTrue(auth._to_session_user(admin)['can_manage_users_config'])

    def test_login_rechecks_capability_from_mysql(self):
        self.assertIn('USER_MODULE_ACCESS', auth._USER_JOINS)
        self.assertIn('Can_Manage_Users_Config', auth._USER_FIELDS)
        self.assertIn('p.User_Id = a.Admin_Id', auth._USER_JOINS)


if __name__ == '__main__':
    unittest.main()
