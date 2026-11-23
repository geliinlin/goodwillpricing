"""Staff privilege is denied at database schema, service, session, router and navigation."""
from pathlib import Path
import unittest
from unittest.mock import patch, Mock
from _goodwill_test_helpers import FakeStreamlit
import auth
import users_service as service
import dashboard_page
import dashboard_router

ROOT = Path(__file__).resolve().parents[1]
STAFF = {'user_id':2,'user_type':'Store Staff','full_name':'Ashley Brennan',
         'can_manage_users_config':True}
ADMIN = {'user_id':1,'user_type':'Administrator','full_name':'Karen Whitfield',
         'can_manage_users_config':True}

class AdminOnlyAccessTests(unittest.TestCase):
    def test_migration_can_only_store_admin_grants(self):
        sql = (ROOT/'migrations/001_user_management_access.sql').read_text()
        self.assertIn('REFERENCES ADMINISTRATOR (Admin_Id)', sql)
        self.assertNotIn('REFERENCES USER_ACCOUNT (User_Id)', sql)

    def test_staff_flag_is_never_a_grant(self):
        self.assertFalse(service.has_management_access(STAFF))
        self.assertTrue(service.has_management_access(ADMIN))

    def test_staff_with_forged_flag_cannot_load_data(self):
        with patch.object(service,'get_connection') as connect:
            with self.assertRaises(service.UserManagementPermissionError):
                service.load_user_directory(STAFF)
            connect.assert_not_called()

    def test_staff_with_forged_flag_never_sees_tabs(self):
        fake=FakeStreamlit()
        with patch.object(dashboard_page,'st',fake),patch.object(dashboard_page,'load_styles'):
            dashboard_page.render_nav_header(STAFF,Mock(),Mock(),'staff_dashboard')
        labels=[label for label,_ in fake.button_calls]
        self.assertEqual(labels,['goodwill','Dashboard','Price an Item','Guidelines','History','Logout'])

    def test_admin_tabs_show_only_if_granted(self):
        for granted,expected in [(False,False),(True,True)]:
            fake=FakeStreamlit()
            user=dict(ADMIN,can_manage_users_config=granted)
            with patch.object(dashboard_page,'st',fake),patch.object(dashboard_page,'load_styles'):
                dashboard_page.render_nav_header(user,Mock(),Mock(),'admin_dashboard')
            labels=[label for label,_ in fake.button_calls]
            self.assertEqual('Users' in labels,expected)
            self.assertEqual('Configuration' in labels,expected)

if __name__=='__main__': unittest.main()
