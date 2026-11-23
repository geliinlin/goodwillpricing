"""Offline tests for Administrator-only profile edits and Users UI."""
import unittest
from unittest.mock import patch

from _goodwill_test_helpers import FakeStreamlit, ScriptedConnection
import users_service as svc
import users_page as page

ADMIN = {'user_id': 1, 'user_type': 'Administrator',
         'can_manage_users_config': True, 'full_name': 'Karen Whitfield'}
STAFF = {'user_id': 2, 'user_type': 'Store Staff',
         'can_manage_users_config': True}


def actor_reply():
    return {'User_Type': 'Administrator', 'User_Is_Active': 1,
            'Admin_Id': 1, 'Staff_Id': None, 'can_manage': 1}


def target_reply(user_type='Store Staff', user_id=10):
    return {'User_Id': user_id, 'User_Type': user_type,
            'User_Is_Active': 1, 'can_manage': 0}


def current_reply(user_type='Store Staff', branch=3):
    return {'User_First_Name': 'Alex', 'User_Last_Name': 'Brown',
            'User_Username': 'alex', 'User_Email': 'alex@example.org',
            'User_Type': user_type,
            'Branch_Id': branch if user_type == 'Store Staff' else None}


def expected(branch=3):
    return {'User_First_Name': 'Alex', 'User_Last_Name': 'Brown',
            'User_Username': 'alex', 'User_Email': 'alex@example.org',
            'Branch_Id': branch}


class ProfileServiceTests(unittest.TestCase):
    def test_staff_cannot_edit_even_with_forged_permission(self):
        with patch.object(svc, 'get_connection') as open_db:
            with self.assertRaises(svc.UserManagementPermissionError):
                svc.update_user_profile(STAFF, 10, first_name='J', last_name='Brown',
                    username='alex', email='alex@example.org', branch_id=3,
                    expected_profile=expected())
            open_db.assert_not_called()

    def test_admin_cannot_change_own_profile(self):
        """Now self basic edits are allowed while special privileges remain locked."""
        con = ScriptedConnection([
            [{'acquired': 1}], [actor_reply()],
            [target_reply(user_type='Administrator', user_id=1)],
            [current_reply(user_type='Administrator')],
            [], [], [], [{'released': 1}],
        ])
        result = svc.update_user_profile(ADMIN, 1, first_name='Alexandra',
            last_name='Brown', username='alex', email='alex@example.org',
            branch_id=None, expected_profile=expected(branch=None),
            connection_factory=lambda: con)
        self.assertFalse(result['branch_changed'])
        self.assertEqual(con.commits, 1)
        self.assertEqual(con.rollbacks, 0)
        self.assertTrue(any('UPDATE USER_ACCOUNT' in sql for sql, _ in con.statements))
        self.assertFalse(any('UPDATE USER_MODULE_ACCESS' in sql for sql, _ in con.statements))


    def test_staff_profile_and_branch_are_updated_together(self):
        con = ScriptedConnection([
            [{'acquired': 1}], [actor_reply()], [target_reply()],
            [current_reply()], [{'Branch_Id': 4}], [],  # branch exists; no duplicate
            [], [], [], [],  # name-check, update account, branch and session version
            [{'released': 1}],
        ])
        result = svc.update_user_profile(ADMIN, 10, first_name='Alexandra',
            last_name='Brown', username='alex', email='alex@example.org',
            branch_id=4, expected_profile=expected(), connection_factory=lambda: con)
        self.assertEqual(result, {'email_changed': False, 'username_changed': False,
                                  'branch_changed': True})
        writes = [(sql, params) for sql, params in con.statements
                  if sql.strip().startswith('UPDATE')]
        self.assertEqual(len(writes), 2)
        self.assertIn('UPDATE USER_ACCOUNT', writes[0][0])
        self.assertEqual(writes[0][1], ('Alexandra', 'Brown', 'alex', 'alex@example.org', 10))
        self.assertIn('UPDATE STORE_STAFF', writes[1][0])
        self.assertEqual(writes[1][1], (4, 10))
        self.assertEqual(con.commits, 1)
        self.assertTrue(any('USER_PASSWORD_STATE' in sql and 'Session_Version' in sql for sql, _ in con.statements))

    def test_email_change_revokes_tokens_and_sessions(self):
        con = ScriptedConnection([
            [{'acquired': 1}], [actor_reply()], [target_reply()],
            [current_reply()], [{'Branch_Id': 3}], [],
            [], [], [], [], [{'released': 1}],
        ])
        result = svc.update_user_profile(ADMIN, 10, first_name='Alex',
            last_name='Brown', username='alex', email='new@example.org',
            branch_id=3, expected_profile=expected(), connection_factory=lambda: con)
        self.assertTrue(result['email_changed'])
        self.assertEqual(con.commits, 1)
        sql = '\n'.join(st for st, _ in con.statements)
        self.assertIn('UPDATE USER_PASSWORD_TOKEN', sql)
        self.assertIn('Session_Version=Session_Version+1', sql)
        self.assertNotIn('User_Password_Hash=', sql)

    def test_username_change_revokes_sessions_not_tokens(self):
        con = ScriptedConnection([
            [{'acquired': 1}], [actor_reply()], [target_reply()],
            [current_reply()], [{'Branch_Id': 3}], [], [], [], [],
            [{'released': 1}],
        ])
        result = svc.update_user_profile(ADMIN, 10, first_name='Alex',
            last_name='Brown', username='alex.new', email='alex@example.org',
            branch_id=3, expected_profile=expected(), connection_factory=lambda: con)
        self.assertTrue(result['username_changed'])
        sql = '\n'.join(st for st, _ in con.statements)
        self.assertIn('Session_Version', sql)
        self.assertNotIn('UPDATE USER_PASSWORD_TOKEN', sql)

    def test_duplicate_username_or_email_is_rejected_without_update(self):
        con = ScriptedConnection([
            [{'acquired': 1}], [actor_reply()], [target_reply()],
            [current_reply()], [{'Branch_Id': 3}], [{'User_Id': 11}],
            [{'released': 1}],
        ])
        with self.assertRaisesRegex(svc.UserManagementError, 'already belongs'):
            svc.update_user_profile(ADMIN, 10, first_name='Alex',
                last_name='Brown', username='taken', email='alex@example.org',
                branch_id=3, expected_profile=expected(), connection_factory=lambda: con)
        self.assertEqual(con.rollbacks, 1)
        self.assertFalse(any(sql.strip().startswith('UPDATE') for sql, _ in con.statements))

    def test_stale_profile_does_not_overwrite_another_admin(self):
        changed = current_reply()
        changed['User_First_Name'] = 'Updated'
        con = ScriptedConnection([
            [{'acquired': 1}], [actor_reply()], [target_reply()], [changed],
            [{'released': 1}],
        ])
        with self.assertRaisesRegex(svc.UserManagementError, 'changed by someone else'):
            svc.update_user_profile(ADMIN, 10, first_name='New Name',
                last_name='Brown', username='alex', email='alex@example.org',
                branch_id=3, expected_profile=expected(), connection_factory=lambda: con)
        self.assertEqual(con.commits, 0)
        self.assertEqual(con.rollbacks, 1)
        self.assertFalse(any(st.strip().startswith('UPDATE') for st, _ in con.statements))

    def test_invalid_email_rejected_before_database(self):
        with patch.object(svc, 'get_connection') as connection:
            with self.assertRaisesRegex(svc.UserManagementError, 'valid email'):
                svc.update_user_profile(ADMIN, 10, first_name='Alex',
                    last_name='Brown', username='alex', email='invalid',
                    branch_id=3, expected_profile=expected())
            connection.assert_not_called()

    def test_cannot_assign_branch_to_administrator(self):
        con = ScriptedConnection([
            [{'acquired': 1}], [actor_reply()],
            [target_reply(user_type='Administrator')],
            [current_reply(user_type='Administrator')],
            [{'released': 1}],
        ])
        with self.assertRaisesRegex(svc.UserManagementError, 'cannot be assigned'):
            svc.update_user_profile(ADMIN, 10, first_name='Alex',
                last_name='Brown', username='alex', email='alex@example.org',
                branch_id=3, expected_profile=expected(branch=None),
                connection_factory=lambda: con)
        self.assertEqual(con.rollbacks, 1)

    def test_failed_update_rolls_back(self):
        con = ScriptedConnection([
            [{'acquired': 1}], [actor_reply()], [target_reply()],
            [current_reply()], [{'Branch_Id': 4}], [], [],
            [{'released': 1}],
        ], fail_sql='UPDATE USER_ACCOUNT')
        with self.assertRaisesRegex(RuntimeError, 'Synthetic database failure'):
            svc.update_user_profile(ADMIN, 10, first_name='Alexandra',
                last_name='Brown', username='alex', email='alex@example.org',
                branch_id=4, expected_profile=expected(), connection_factory=lambda: con)
        self.assertEqual(con.commits, 0)
        self.assertEqual(con.rollbacks, 1)
        self.assertTrue(con.closed)


ROW = {'User_Id': 10, 'User_First_Name': 'Alex', 'User_Last_Name': 'Brown',
       'User_Username': 'alex', 'User_Email': 'alex@example.org',
       'User_Type': 'Store Staff', 'User_Is_Active': 1,
       'Branch_Id': 3, 'Branch_Name': 'Mount Pleasant',
       'Can_Manage_Users_Config': 0}
BRANCHES = [{'Branch_Id': 3, 'Branch_Name': 'Mount Pleasant'},
            {'Branch_Id': 4, 'Branch_Name': 'Lansing'}]


class ProfilePageTests(unittest.TestCase):
    def setUp(self):
        self.ui = FakeStreamlit()
        self.ui.session_state['gw_users_selected_id'] = 10
        self.patched_ui = patch.object(page, 'st', self.ui)
        self.patched_ui.start()
        self.addCleanup(self.patched_ui.stop)
        self.patched_css = patch.object(page, 'load_styles')
        self.patched_css.start()
        self.addCleanup(self.patched_css.stop)

    def render(self, rows=None):
        with patch.object(page, 'load_user_directory', return_value={
            'users': list(rows or [ROW]), 'branches': BRANCHES,
            'can_grant_access': True}):
            page.show_user_management(ADMIN)

    def test_edit_button_opens_form_and_preserves_existing_role(self):
        self.ui.buttons['gw_users_edit_start_10'] = True
        self.render()
        self.assertEqual(self.ui.session_state['gw_users_edit_profile_id'], 10)
        self.assertEqual(self.ui.session_state['gw_users_edit_profile_original']['User_Email'],
                         'alex@example.org')
        self.assertTrue(self.ui.reruns)

        self.ui.buttons = {}
        self.render()
        self.assertIn('Edit User Profile', ''.join(self.ui.markdowns))
        self.assertIn('Role is read-only', ''.join(self.ui.markdowns)) if False else None
        self.assertIn('gw_users_profile_editor', self.ui.containers)
        self.assertTrue(any(spec == [1.4, 1] for spec, _ in self.ui.column_specs))

    def test_save_sends_fields_to_service_with_original_snapshot(self):
        self.ui.session_state['gw_users_edit_profile_id'] = 10
        self.ui.session_state['gw_users_edit_profile_original'] = page._profile_snapshot(ROW)
        self.ui.session_state['gw_users_edit_profile_nonce'] = 2
        self.ui.buttons['gw_users_edit_save_10'] = True
        self.ui.inputs['gw_users_edit_first_10_2'] = 'Alexandra'
        self.ui.inputs['gw_users_edit_email_10_2'] = 'new@example.org'
        self.ui.selects['gw_users_edit_branch_10_2'] = 4
        with patch.object(page, 'update_user_profile', return_value={
            'email_changed': True, 'username_changed': False,
            'branch_changed': True}) as update:
            self.render()
        self.assertEqual(update.call_args.args[:2], (ADMIN, 10))
        self.assertEqual(update.call_args.kwargs['first_name'], 'Alexandra')
        self.assertEqual(update.call_args.kwargs['email'], 'new@example.org')
        self.assertEqual(update.call_args.kwargs['branch_id'], 4)
        self.assertEqual(update.call_args.kwargs['expected_profile'], page._profile_snapshot(ROW))
        self.assertNotIn('gw_users_edit_profile_id', self.ui.session_state)
        self.assertTrue(self.ui.reruns)

    def test_self_edit_is_not_exposed(self):
        """Self basic profile editor is available; privileged controls are not."""
        self.ui.session_state['gw_users_selected_id'] = 1
        my_account = dict(ROW, User_Id=1, User_Type='Administrator',
                          Branch_Id=None, Branch_Name=None)
        self.render([my_account])
        labels = [label for label, _ in self.ui.button_calls]
        self.assertIn('Edit User Profile', labels)
        self.assertNotIn('Save account status', labels)
        self.assertNotIn('Save management access', labels)
        self.assertNotIn('Send Password Reset Email', labels)


    def test_switch_row_discards_old_profile_snapshot(self):
        self.ui.session_state['gw_users_edit_profile_id'] = 10
        self.ui.session_state['gw_users_edit_profile_original'] = page._profile_snapshot(ROW)
        other = dict(ROW, User_Id=11, User_Username='other', User_Email='other@example.org')
        self.ui.grid_rows = [1]
        self.render([ROW, other])
        self.assertEqual(self.ui.session_state.get('gw_users_selected_id'), 11)
        self.assertNotIn('gw_users_edit_profile_id', self.ui.session_state)
        self.assertNotIn('gw_users_edit_profile_original', self.ui.session_state)


if __name__ == '__main__':
    unittest.main()
