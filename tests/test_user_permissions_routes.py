"""UI navigation denies privileged pages by default and allows both roles if granted."""
import unittest
import types
import sys
from unittest.mock import Mock, patch

from _goodwill_test_helpers import FakeStreamlit
import dashboard_router as router
import dashboard_page as nav

ADMIN = {"user_id": 1, "user_type": "Administrator", "full_name": "Karen Whitfield"}
STAFF = {"user_id": 2, "user_type": "Store Staff", "full_name": "Ashley Brennan"}

class PermissionRoutingTests(unittest.TestCase):
    def setUp(self):
        self.fake = FakeStreamlit()
        self.patch_st = patch.object(router, 'st', self.fake)
        self.patch_st.start()
        self.addCleanup(self.patch_st.stop)
        self.patch_nav = patch.object(router, 'render_nav_header')
        self.patch_nav.start()
        self.addCleanup(self.patch_nav.stop)

    def test_regular_administrator_cannot_open_users_or_configuration(self):
        for destination in ('users', 'configuration'):
            self.fake.session_state.update(gw_page=destination, gw_nav_owner_id=1)
            with patch.object(router, 'show_admin_dashboard') as home:
                router.render_authenticated_app(ADMIN)
            self.assertEqual(self.fake.session_state['gw_page'], 'admin_dashboard')
            home.assert_called_once()

    def test_regular_staff_cannot_open_users_or_configuration(self):
        for destination in ('users', 'configuration'):
            self.fake.session_state.update(gw_page=destination, gw_nav_owner_id=2)
            with patch.object(router, 'show_staff_dashboard') as home:
                router.render_authenticated_app(STAFF)
            self.assertEqual(self.fake.session_state['gw_page'], 'staff_dashboard')
            home.assert_called_once()

    def test_staff_with_forged_permission_cannot_access_users_or_configuration(self):
        forged = dict(STAFF, can_manage_users_config=True)
        for destination in ('users', 'configuration', 'reports'):
            self.fake.session_state.update(gw_page=destination, gw_nav_owner_id=2)
            with patch.object(router, 'show_staff_dashboard') as home:
                router.render_authenticated_app(forged)
            self.assertEqual(self.fake.session_state['gw_page'], 'staff_dashboard')
            home.assert_called_once()

    def test_admin_without_flag_still_gets_guidelines_reports(self):
        self.fake.session_state.update(gw_page='guidelines', gw_nav_owner_id=1)
        fake_module = types.ModuleType('guideline_page')
        fake_module.show_guideline_management = Mock()
        with patch.dict(sys.modules, {'guideline_page': fake_module}):
            router.render_authenticated_app(ADMIN)
        fake_module.show_guideline_management.assert_called_once()

    def test_navigation_reacts_to_permission_flag(self):
        ui = FakeStreamlit()
        with patch.object(nav, 'st', ui), patch.object(nav, 'load_styles'):
            nav.render_nav_header(ADMIN, Mock(), Mock(), 'admin_dashboard')
        self.assertEqual([label for label,_ in ui.button_calls],
            ['goodwill','Admin Dashboard','Guidelines','Reports','Logout'])
        ui = FakeStreamlit()
        with patch.object(nav,'st',ui), patch.object(nav,'load_styles'):
            nav.render_nav_header(dict(STAFF,can_manage_users_config=True), Mock(), Mock(), 'staff_dashboard')
        self.assertEqual([label for label,_ in ui.button_calls],
            ['goodwill','Dashboard','Price an Item','Guidelines','History','Logout'])

    def test_permission_revocation_closes_stale_page(self):
        self.fake.session_state.update(gw_page='users', gw_nav_owner_id=2)
        with patch.object(router,'show_staff_dashboard') as home:
            router.render_authenticated_app(STAFF)
        home.assert_called_once()
        self.assertEqual(self.fake.session_state['gw_page'],'staff_dashboard')

if __name__ == '__main__':
    unittest.main()
