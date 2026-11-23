"""Duplicate-account detection for Users V4; no live MySQL or SMTP."""
from hashlib import sha256
import unittest
from unittest.mock import patch

from _goodwill_test_helpers import FakeStreamlit, ScriptedConnection
import users_service as svc
import users_page as page

ADMIN = {'user_id': 1, 'user_type': 'Administrator', 'can_manage_users_config': True}
EXISTING = {
    'User_Id': 20, 'User_First_Name': 'Jane', 'User_Last_Name': 'Smith',
    'User_Username': 'jsmith', 'User_Email': 'jane@example.org',
    'User_Is_Active': 0, 'User_Type': 'Store Staff',
    'Branch_Id': 3, 'Branch_Name': 'Mount Pleasant',
    'Can_Manage_Users_Config': 0,
}
ACTOR = {'User_Type': 'Administrator', 'User_Is_Active': 1,
         'Admin_Id': 1, 'Staff_Id': None, 'can_manage': 1}


def match(**overrides):
    fields = dict(first_name='Jane', last_name='Smith',
                  username='different', email='different@example.org')
    fields.update(overrides)
    return svc.find_account_duplicates([EXISTING], **fields)


class DuplicatePreviewTests(unittest.TestCase):
    def test_email_match_is_blocking_regardless_of_case_and_status(self):
        result = match(email='  JANE@EXAMPLE.ORG  ')
        self.assertEqual(result['similar'], [])
        self.assertEqual(result['blocking'][0]['fields'], ('Email',))
        self.assertFalse(result['blocking'][0]['active'])
        self.assertEqual(result['blocking'][0]['user_id'], 20)

    def test_username_match_is_blocking_even_if_name_differs(self):
        result = match(first_name='Other', last_name='Person', username='  JSMITH  ')
        self.assertEqual(result['blocking'][0]['fields'], ('Username',))

    def test_name_match_only_is_warning_not_hard_duplicate(self):
        result = match(first_name='  JANE ', last_name='SMITH  ')
        self.assertEqual(result['blocking'], [])
        self.assertEqual(result['similar'][0]['user_id'], 20)

    def test_editing_self_does_not_flag_own_row(self):
        result = svc.find_account_duplicates([EXISTING],
            first_name='Jane', last_name='Smith', username='jsmith',
            email='jane@example.org', exclude_user_id=20)
        self.assertEqual(result, {'blocking': [], 'similar': []})

    def test_blank_form_does_not_show_duplicates(self):
        result = match(first_name='', last_name='', username='', email='')
        self.assertEqual(result, {'blocking': [], 'similar': []})

    def test_whitespace_normalization_in_names(self):
        row = dict(EXISTING, User_First_Name='  Mary   Jane ', User_Last_Name='Smith')
        result = svc.find_account_duplicates([row], first_name='MARY JANE', last_name='Smith',
                                              username='a', email='a@example.org')
        self.assertEqual(len(result['similar']), 1)


class DuplicateDatabaseTests(unittest.TestCase):
    @patch.object(svc, 'hash_password', return_value='test-hash')
    def test_create_blocks_same_name_without_confirmation(self, hashed):
        con = ScriptedConnection([
            [{'acquired': 1}], [ACTOR], [{'Branch_Id': 3}], [],
            [{'User_Id': 20, 'User_Username': 'jsmith'}], [{'released': 1}],
        ])
        with self.assertRaisesRegex(svc.UserManagementError, 'User #20'):
            svc.create_user_account(ADMIN, first_name='Jane', last_name='Smith',
                username='jane2', email='jane2@example.org', user_type='Store Staff',
                branch_id=3, connection_factory=lambda: con,
                password_factory=lambda: 'temporary-password')
        self.assertEqual(con.rollbacks, 1)
        self.assertEqual(con.commits, 0)
        self.assertFalse(any('INSERT INTO USER_ACCOUNT' in sql for sql, _ in con.statements))
        self.assertTrue(con.closed)

    @patch.object(svc, 'hash_password', return_value='test-hash')
    def test_create_same_name_allowed_after_explicit_confirmation(self, hashed):
        con = ScriptedConnection([
            [{'acquired': 1}], [ACTOR], [{'Branch_Id': 3}], [],
            [{'User_Id': 20, 'User_Username': 'jsmith'}],
            [{'next_id': 55}], [], [], [{'released': 1}],
        ])
        result = svc.create_user_account(ADMIN, first_name='Jane', last_name='Smith',
            username='jane2', email='jane2@example.org', user_type='Store Staff',
            branch_id=3, confirm_similar_name=True,
            connection_factory=lambda: con, password_factory=lambda: 'temporary-password')
        self.assertEqual(result['user_id'], 55)
        self.assertEqual(con.commits, 1)
        self.assertEqual(con.rollbacks, 0)

    @patch.object(svc, 'hash_password', return_value='test-hash')
    def test_identical_email_cannot_be_overridden_by_confirmation(self, hashed):
        con = ScriptedConnection([
            [{'acquired': 1}], [ACTOR], [{'Branch_Id': 3}],
            [{'User_Id': 20, 'User_Username': 'jsmith', 'User_Email': 'jane@example.org'}],
            [{'released': 1}],
        ])
        with self.assertRaisesRegex(svc.UserManagementError, 'Email already belongs.*User #20'):
            svc.create_user_account(ADMIN, first_name='Jane', last_name='Smith',
                username='jane2', email='JANE@example.org', user_type='Store Staff',
                branch_id=3, confirm_similar_name=True,
                connection_factory=lambda: con, password_factory=lambda: 'temporary-password')
        self.assertEqual(con.rollbacks, 1)
        self.assertFalse(any(sql.startswith('INSERT INTO USER_ACCOUNT') for sql, _ in con.statements))

    def test_edit_blocks_name_match_even_if_username_and_email_unique(self):
        current = {'User_First_Name': 'Alex', 'User_Last_Name': 'Brown',
            'User_Username': 'alex', 'User_Email': 'alex@example.org',
            'User_Type': 'Store Staff', 'Branch_Id': 3}
        con = ScriptedConnection([
            [{'acquired':1}], [ACTOR],
            [{'User_Id':10, 'User_Type':'Store Staff', 'User_Is_Active':1, 'can_manage':0}],
            [current], [{'Branch_Id':3}], [],
            [{'User_Id':20, 'User_Username':'jsmith'}], [{'released':1}],
        ])
        with self.assertRaisesRegex(svc.UserManagementError, 'User #20'):
            svc.update_user_profile(ADMIN, 10, first_name='Jane', last_name='Smith',
                username='alex', email='alex@example.org', branch_id=3,
                expected_profile={'User_First_Name':'Alex','User_Last_Name':'Brown',
                    'User_Username':'alex','User_Email':'alex@example.org','Branch_Id':3},
                connection_factory=lambda: con)
        self.assertEqual(con.rollbacks, 1)
        self.assertFalse(any(sql.strip().startswith('UPDATE USER_ACCOUNT') for sql, _ in con.statements))


class DuplicatePageTests(unittest.TestCase):
    def setUp(self):
        self.ui = FakeStreamlit()
        self.patch_ui = patch.object(page, 'st', self.ui)
        self.patch_ui.start()
        self.addCleanup(self.patch_ui.stop)
        self.patch_css = patch.object(page, 'load_styles')
        self.patch_css.start()
        self.addCleanup(self.patch_css.stop)
        self.directory = {'users':[EXISTING],
                          'branches':[{'Branch_Id':3, 'Branch_Name':'Mount Pleasant'}],
                          'can_grant_access': True}

    def run_page(self):
        with patch.object(page, 'load_user_directory', return_value=self.directory):
            page.show_user_management(ADMIN)

    def test_add_duplicate_email_displays_existing_id_and_disables_save(self):
        self.ui.session_state['gw_users_mode'] = 'add'
        self.ui.inputs.update(gw_users_new_first='Other', gw_users_new_last='Person',
                              gw_users_new_username='newperson',
                              gw_users_new_email='JANE@example.org')
        self.ui.buttons['Create User'] = True
        with patch.object(page, 'create_and_invite_user') as send:
            self.run_page()
        send.assert_not_called()
        self.assertTrue(any('User #20' in msg and 'Email' in msg for msg in self.ui.errors))
        buttons = [args for label, args in self.ui.button_calls if label == 'Create User']
        self.assertTrue(buttons[0]['disabled'])

    def test_create_same_name_requires_explicit_checkbox(self):
        self.ui.session_state['gw_users_mode'] = 'add'
        self.ui.inputs.update(gw_users_new_first='Jane', gw_users_new_last='Smith',
                              gw_users_new_username='another',
                              gw_users_new_email='another@example.org')
        self.ui.buttons['Create User'] = True
        with patch.object(page, 'create_and_invite_user') as send:
            self.run_page()
        send.assert_not_called()
        self.assertTrue(any('Potential duplicate account' in msg for msg in self.ui.warnings))
        self.assertTrue(next(args['disabled'] for label,args in self.ui.button_calls
                             if label == 'Create User'))
        signature = '\x1f'.join(x.casefold() for x in
                      ('Jane','Smith','another','another@example.org'))
        key = 'gw_users_duplicate_create_' + sha256(signature.encode()).hexdigest()[:16]
        self.ui.checks[key] = True
        self.ui.button_calls.clear()
        with patch.object(page, 'create_and_invite_user',
                          return_value={'user_id':55, 'username':'another', 'email_sent':False}) as send:
            self.run_page()
        self.assertFalse(next(args['disabled'] for label,args in self.ui.button_calls
                              if label == 'Create User'))
        self.assertTrue(send.call_args.kwargs['confirm_similar_name'])

    def test_edit_excludes_current_id_from_preview(self):
        self.ui.session_state['gw_users_selected_id'] = 20
        self.ui.session_state['gw_users_edit_profile_id'] = 20
        self.ui.session_state['gw_users_edit_profile_original'] = page._profile_snapshot(EXISTING)
        self.ui.session_state['gw_users_edit_profile_nonce'] = 1
        self.run_page()
        self.assertFalse(any('Duplicate account' in msg for msg in self.ui.errors))
        self.assertFalse(any('Potential duplicate account' in msg for msg in self.ui.warnings))

if __name__ == '__main__':
    unittest.main()
