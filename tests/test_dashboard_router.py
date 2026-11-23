# Goodwill unified navigation spacing V1
"""Route-level role isolation and single shared header; no database needed."""
import unittest
import sys
import types
from unittest.mock import patch, Mock
from _goodwill_test_helpers import ensure_streamlit_importable, FakeStreamlit

ensure_streamlit_importable()
import dashboard_router as router

ADMIN = {"user_id": 1, "user_type": "Administrator", "full_name": "Karen Whitfield", "can_manage_users_config": True}
STAFF = {"user_id": 2, "user_type": "Store Staff", "full_name": "Ashley Brennan"}


class DashboardRouterTests(unittest.TestCase):
    def setUp(self):
        self.ui = FakeStreamlit()
        self.p = patch.object(router, "st", self.ui)
        self.p.start()
        self.addCleanup(self.p.stop)
        self.nav_patch = patch.object(router, "render_nav_header")
        self.nav = self.nav_patch.start()
        self.addCleanup(self.nav_patch.stop)

    def test_homepages_and_unknown_role_fail_closed(self):
        self.assertEqual(router._homepage(ADMIN), "admin_dashboard")
        self.assertEqual(router._homepage(STAFF), "staff_dashboard")
        with self.assertRaises(PermissionError):
            router._homepage({"user_type": "Visitor"})

    def test_admin_login_opens_home_with_one_header(self):
        with patch.object(router, "show_admin_dashboard") as show:
            router.render_authenticated_app(ADMIN)
        self.assertEqual(self.ui.session_state["gw_page"], "admin_dashboard")
        self.nav.assert_called_once()
        self.assertIn("gw_page_body", self.ui.containers)
        self.assertEqual(self.nav.call_args.args[-1], "admin_dashboard")
        show.assert_called_once()

    def test_staff_login_opens_home(self):
        with patch.object(router, "show_staff_dashboard") as show:
            router.render_authenticated_app(STAFF)
        self.assertEqual(self.ui.session_state["gw_page"], "staff_dashboard")
        show.assert_called_once()

    def test_staff_cannot_route_to_guidelines_even_from_stale_page(self):
        self.ui.session_state.update(gw_nav_owner_id=2, gw_page="guidelines")
        with patch.object(router, "show_staff_dashboard") as show:
            router.render_authenticated_app(STAFF)
        self.assertTrue(self.ui.errors)
        self.assertEqual(self.ui.session_state["gw_page"], "staff_dashboard")
        show.assert_called_once()

    def test_admin_cannot_route_to_staff_pricing(self):
        self.ui.session_state.update(gw_nav_owner_id=1, gw_page="item_search")
        with patch.object(router, "show_admin_dashboard") as show:
            router.render_authenticated_app(ADMIN)
        self.assertTrue(self.ui.errors)
        show.assert_called_once()

    def test_switching_account_resets_old_route_and_tag_search(self):
        self.ui.session_state.update(gw_nav_owner_id=1, gw_page="reports",
                                     gw_reprint_search="oldtag", gw_reprint_tag_input="oldtag",
                                     gw_reports_staff=42, gw_reports_start_date="old-date",
                                     gw_reports_adjustment="decreased")
        with patch.object(router, "show_staff_dashboard"):
            router.render_authenticated_app(STAFF)
        self.assertEqual(self.ui.session_state["gw_nav_owner_id"], 2)
        self.assertEqual(self.ui.session_state["gw_page"], "staff_dashboard")
        self.assertNotIn("gw_reprint_search", self.ui.session_state)
        self.assertNotIn("gw_reprint_tag_input", self.ui.session_state)
        self.assertNotIn("gw_reports_staff", self.ui.session_state)
        self.assertNotIn("gw_reports_start_date", self.ui.session_state)
        self.assertNotIn("gw_reports_adjustment", self.ui.session_state)

    def test_guidelines_use_router_header_only(self):
        self.ui.session_state.update(gw_nav_owner_id=1, gw_page="guidelines")
        fake_module = types.ModuleType("guideline_page")
        fake_module.show_guideline_management = Mock()
        with patch.dict(sys.modules, {"guideline_page": fake_module}):
            router.render_authenticated_app(ADMIN)
        fake_module.show_guideline_management.assert_called_once_with(ADMIN, show_header=False)

    def test_pricing_uses_router_header_only(self):
        self.ui.session_state.update(gw_nav_owner_id=2, gw_page="item_search")
        with patch("item_search_page.render_item_search_page") as show:
            router.render_authenticated_app(STAFF)
        self.assertFalse(show.call_args.kwargs["show_header"])
        self.assertTrue(callable(show.call_args.kwargs["on_logout"]))

    def test_reports_and_configuration_route_to_real_pages(self):
        self.ui.session_state.update(gw_nav_owner_id=1, gw_page="reports")
        fake_module = types.ModuleType("reports_page")
        fake_module.show_admin_reports = Mock()
        with patch.dict(sys.modules, {"reports_page": fake_module}), \
             patch.object(router, "show_unavailable_page") as placeholder:
            router.render_authenticated_app(ADMIN)
        fake_module.show_admin_reports.assert_called_once_with(ADMIN)
        placeholder.assert_not_called()
        self.ui.session_state.update(gw_nav_owner_id=1, gw_page="configuration")
        fake_config = types.ModuleType("configuration_page")
        fake_config.show_configuration = Mock()
        with patch.dict(sys.modules, {"configuration_page": fake_config}), \
             patch.object(router, "show_unavailable_page") as placeholder:
            router.render_authenticated_app(ADMIN)
        fake_config.show_configuration.assert_called_once_with(ADMIN)
        placeholder.assert_not_called()
        fake_users = types.ModuleType("users_page")
        fake_users.show_user_management = Mock()
        self.ui.session_state.update(gw_nav_owner_id=1, gw_page="users")
        with patch.dict(sys.modules, {"users_page": fake_users}):
            router.render_authenticated_app(ADMIN)
        fake_users.show_user_management.assert_called_once_with(ADMIN, router._navigate)

    def test_staff_cannot_access_reports_or_configuration(self):
        for route in ("reports", "configuration"):
            self.ui.session_state.update(gw_nav_owner_id=2, gw_page=route)
            with patch.object(router, "show_staff_dashboard"):
                router.render_authenticated_app(STAFF)
            self.assertEqual(self.ui.session_state["gw_page"], "staff_dashboard")

    def test_navigation_and_logout(self):
        router._navigate("history")
        self.assertEqual(self.ui.session_state["gw_page"], "history")
        self.assertEqual(self.ui.reruns, 1)
        router._logout()
        self.assertEqual(self.ui.session_state, {})
        self.assertEqual(self.ui.reruns, 2)

    def test_unknown_role_has_no_header(self):
        router.render_authenticated_app({"user_id": 3, "user_type": "Unknown"})
        self.nav.assert_not_called()
        self.assertTrue(self.ui.errors)


if __name__ == "__main__":
    unittest.main()
