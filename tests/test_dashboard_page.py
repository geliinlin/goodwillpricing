# Goodwill unified navigation spacing V1
"""Dashboard UI logic, navigation ordering, visibility, and HTML safety."""
import unittest
from datetime import datetime
from unittest.mock import patch, Mock
from _goodwill_test_helpers import ensure_streamlit_importable, FakeStreamlit

ensure_streamlit_importable()
import dashboard_page as page

ADMIN = {"user_id": 1, "user_type": "Administrator", "full_name": "Karen Whitfield", "can_manage_users_config": True}
STAFF = {"user_id": 2, "user_type": "Store Staff", "full_name": "Ashley Brennan", "branch_name": "Mount Pleasant"}


class DashboardPageTests(unittest.TestCase):
    def setUp(self):
        self.ui = FakeStreamlit()
        self.stub = patch.object(page, "st", self.ui)
        self.stub.start()
        self.addCleanup(self.stub.stop)
        self.styles = patch.object(page, "load_styles")
        self.styles.start()
        self.addCleanup(self.styles.stop)

    def test_admin_navigation_has_expected_order_and_equal_tab_weights(self):
        page.render_nav_header(ADMIN, Mock(), Mock(), "guidelines")
        labels = [label for label, _ in self.ui.button_calls]
        self.assertEqual(labels, ["goodwill", "Admin Dashboard", "Guidelines", "Reports", "Users", "Configuration", "Logout"])
        shell = self.ui.column_specs[0][0]
        self.assertEqual(shell, [2.4, 5.9, 1.9, .78])
        self.assertEqual(self.ui.column_specs[1][0], 5)
        self.assertIn("gw_nav_tabs", self.ui.containers)
        self.assertEqual(self.ui.containers.count("gw_nav_selected"), 1)
        self.assertIn("gw_nav_tab_reports", self.ui.containers)

    def test_staff_menu_shows_no_admin_only_buttons(self):
        page.render_nav_header(STAFF, Mock(), Mock(), "staff_dashboard")
        labels = [label for label, _ in self.ui.button_calls]
        self.assertEqual(labels, ["goodwill", "Dashboard", "Price an Item", "Guidelines", "History", "Logout"])
        self.assertEqual(self.ui.column_specs[0][0], [2.4, 5.9, 1.9, .78])
        self.assertEqual(self.ui.column_specs[1][0], 4)
        self.assertIn("gw_nav_tabs", self.ui.containers)

    def test_clicking_logo_returns_user_home(self):
        self.ui.buttons["gw_brand_home"] = True
        navigate = Mock()
        page.render_nav_header(STAFF, navigate, Mock(), "history")
        navigate.assert_called_once_with("staff_dashboard")

    def test_clicking_reports_navigates_and_logout_invokes_callback(self):
        self.ui.buttons["gw_nav_reports"] = True
        self.ui.buttons["gw_nav_logout"] = True
        navigate, logout = Mock(), Mock()
        page.render_nav_header(ADMIN, navigate, logout, "admin_dashboard")
        navigate.assert_called_once_with("reports")
        logout.assert_called_once_with()

    def test_user_name_is_html_escaped(self):
        u = dict(STAFF, full_name='<img src=x onerror=alert(1)>')
        page.render_nav_header(u, Mock(), Mock(), "staff_dashboard")
        rendered = ''.join(self.ui.markdowns)
        self.assertIn('&lt;img', rendered)
        self.assertNotIn('<img', rendered)

    def test_admin_page_metrics_and_links(self):
        with patch.object(page, "get_admin_snapshot", return_value={
            "metrics": {"active_guidelines": 3, "active_users": 7, "decisions": 2, "overrides": 1},
            "recent": []
        }):
            page.show_admin_dashboard(ADMIN, Mock())
        rendered = ''.join(self.ui.markdowns)
        for label in ("Admin Dashboard", "Active Guidelines", "Manage Guidelines", "Manage Users", "View Reports"):
            self.assertIn(label, rendered)
        self.assertEqual(len([c for c in self.ui.button_calls if c[0].startswith("Open ")]), 4)

    def test_staff_home_has_pricing_entry_and_4_step_guide(self):
        with patch.object(page, "get_staff_snapshot", return_value={
            "metrics": {"my_decisions": 2, "today_decisions": 1, "my_overrides": 0}, "recent": []
        }):
            page.show_staff_dashboard(STAFF, Mock())
        rendered = ''.join(self.ui.markdowns)
        for label in ("Start New Pricing", "Item Details", "Recommendation", "Final Decision", "Print Tag"):
            self.assertIn(label, rendered)
        self.assertIn("gw_staff_start", [c[1].get("key") for c in self.ui.button_calls])

    def test_staff_cannot_view_admin_dashboard(self):
        with patch.object(page, "get_admin_snapshot") as lookup:
            page.show_admin_dashboard(STAFF, Mock())
        self.assertTrue(self.ui.errors)
        lookup.assert_not_called()

    def test_admin_cannot_view_staff_dashboard(self):
        with patch.object(page, "get_staff_snapshot") as lookup:
            page.show_staff_dashboard(ADMIN, Mock())
        self.assertTrue(self.ui.errors)
        lookup.assert_not_called()

    def test_history_search_filters_loaded_rows(self):
        """Search is now SQL-side, not applied to the first 100 loaded rows."""
        self.ui.inputs['gw_history_staff_search'] = 'MP26'
        rows = [{'tag_number':'MP26-1001','description':'jacket','category':'Clothes'},
                {'tag_number':'LA26-1002','description':'lamp','category':'Home'}]
        options = {'categories': [], 'branches': [], 'staff': []}
        with patch.object(page, 'get_history_filter_options', return_value=options), \
             patch.object(page, 'get_filtered_history', return_value=rows) as query, \
             patch.object(page, '_activity') as activity:
            page.show_pricing_history(STAFF, Mock())
        self.assertEqual(query.call_args.kwargs['keyword'], 'MP26')
        self.assertEqual(activity.call_args.args[0], rows)
        self.assertFalse(activity.call_args.kwargs['admin'])


    def test_time_label_formats_datetime_and_escapes_other_values(self):
        self.assertIn("Nov 22, 2026", page._time_label(datetime(2026, 11, 22, 15, 0)))
        self.assertEqual(page._time_label("<b>"), "&lt;b&gt;")

    def test_access_failure_shows_user_message_not_traceback(self):
        def blocked(user):
            raise page.DashboardPermissionError("private detail")
        self.assertIsNone(page._fetch_or_error(blocked, ADMIN))
        self.assertEqual(len(self.ui.errors), 1)
        self.assertNotIn("private detail", self.ui.errors[0])


if __name__ == "__main__":
    unittest.main()
