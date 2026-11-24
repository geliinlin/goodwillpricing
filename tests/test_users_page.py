"""Users page smoke tests under FakeStreamlit: never accesses real MySQL."""
import unittest
from unittest.mock import patch

from _goodwill_test_helpers import FakeStreamlit
import users_page as page

ADMIN = {'user_id':1,'user_type':'Administrator','full_name':'Karen Whitfield',
         'can_manage_users_config':True}
STAFF = {'user_id':2,'user_type':'Store Staff','full_name':'Ashley Brennan',
         'can_manage_users_config':True}

ROWS = [{'User_Id':10,'User_First_Name':'Alex','User_Last_Name':'Brown',
         'User_Username':'alex','User_Email':'alex@example.org','User_Type':'Store Staff',
         'User_Is_Active':1,'Branch_Id':3,'Branch_Name':'Mount Pleasant',
         'Can_Manage_Users_Config':0}]
BRANCHES = [{'Branch_Id':3,'Branch_Name':'Mount Pleasant'}]

class UserPageTests(unittest.TestCase):
    def setUp(self):
        self.ui = FakeStreamlit()
        self.patch_ui = patch.object(page,'st',self.ui)
        self.patch_ui.start()
        self.addCleanup(self.patch_ui.stop)
        self.patch_styles = patch.object(page,'load_styles')
        self.styles = self.patch_styles.start()
        self.addCleanup(self.patch_styles.stop)

    def test_privileged_user_sees_directory(self):
        with patch.object(page,'load_user_directory',return_value={
            'users':ROWS,'branches':BRANCHES,'can_grant_access':True}) as read:
            page.show_user_management(ADMIN)
        self.assertEqual(len(self.ui.grid_events),1)
        self.assertEqual(list(self.ui.grid_events[0][0]['Username']),['alex'])
        self.assertTrue(self.ui.markdowns)
        self.styles.assert_called_once_with('users.css')
        read.assert_called_once_with(ADMIN)

    def test_filtered_out_account_cannot_still_be_managed(self):
        self.ui.session_state['gw_users_selected_id'] = ROWS[0]['User_Id']
        self.ui.session_state['gw_users_search'] = 'missing account'
        page._directory(ADMIN, dict(users=ROWS, branches=BRANCHES, can_grant_access=True))
        self.assertNotIn('gw_users_selected_id', self.ui.session_state)
        self.assertFalse(any(label == 'Save account status' for label, _ in self.ui.button_calls))

    def test_grid_selection_is_reset_when_account_positions_change(self):
        directory = dict(users=ROWS, branches=BRANCHES, can_grant_access=True)
        page._directory(ADMIN, directory)
        first = self.ui.grid_events[-1][1]['key']
        page._directory(ADMIN, dict(directory, users=[dict(ROWS[0], User_Id=11), *ROWS]))
        self.assertNotEqual(first, self.ui.grid_events[-1][1]['key'])

    def test_unprivileged_staff_cannot_even_load_directory(self):
        with patch.object(page,'load_user_directory') as read:
            page.show_user_management(dict(STAFF,can_manage_users_config=False))
        read.assert_not_called()
        self.assertTrue(self.ui.errors)

    def test_staff_with_forged_management_flag_cannot_see_directory(self):
        with patch.object(page,'load_user_directory') as read:
            page.show_user_management(STAFF)
        read.assert_not_called()
        self.assertTrue(self.ui.errors)

    def test_data_failure_is_caught_without_exposing_credentials(self):
        with patch.object(page,'load_user_directory',side_effect=RuntimeError('secret host')):
            page.show_user_management(ADMIN)
        self.assertTrue(self.ui.errors)
        self.assertNotIn('secret host', ''.join(self.ui.errors))

    def test_add_form_describes_email_setup(self):
        self.ui.session_state['gw_users_mode'] = 'add'
        with patch.object(page,'load_user_directory',return_value={
            'users':ROWS,'branches':BRANCHES,'can_grant_access':True}):
            page.show_user_management(ADMIN)
        self.assertIn('An invitation email includes', ''.join(self.ui.markdowns))
        self.assertFalse(self.ui.errors)

    def test_add_form_management_checkbox_only_for_admin_target(self):
        self.ui.session_state['gw_users_mode'] = 'add'
        with patch.object(page,'load_user_directory',return_value={
            'users':ROWS,'branches':BRANCHES,'can_grant_access':True}), \
             patch.object(self.ui, 'checkbox', wraps=self.ui.checkbox) as boxes:
            page.show_user_management(ADMIN)
        self.assertFalse(any('Grant Users + Configuration access' == call.args[0]
                             for call in boxes.call_args_list))

        second = FakeStreamlit()
        second.session_state['gw_users_mode'] = 'add'
        second.session_state['gw_users_new_type'] = 'Administrator'
        with patch.object(page, 'st', second), patch.object(page, 'load_user_directory', return_value={
            'users':ROWS,'branches':BRANCHES,'can_grant_access':True}), \
             patch.object(second, 'checkbox', wraps=second.checkbox) as boxes:
            page.show_user_management(ADMIN)
        self.assertTrue(any('Grant Users + Configuration access' == call.args[0]
                            for call in boxes.call_args_list))


if __name__ == '__main__':
    unittest.main()
