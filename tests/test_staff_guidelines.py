"""Offline coverage for read-only Store Staff Guidelines reference page."""
import sys
from contextlib import nullcontext
import types
import unittest
from unittest.mock import Mock, patch

from _goodwill_test_helpers import ScriptedConnection, ensure_streamlit_importable

ensure_streamlit_importable()
import dashboard_router as router
import staff_guidelines_page as page
from staff_guidelines_service import get_staff_guidelines, filter_guidelines
from dashboard_data import DashboardPermissionError

STAFF = {'user_id': 9, 'user_type': 'Store Staff'}
ADMIN = {'user_id': 1, 'user_type': 'Administrator'}
ACTIVE_STAFF = {'User_Type': 'Store Staff', 'User_Is_Active': 1,
                'Admin_Id': None, 'Staff_Id': 9}
GUIDELINE = {'guideline_id': 10, 'item_type': 'Clothing', 'category': 'Sneakers',
             'brand_tier': 'Premium', 'item_condition': 'Good',
             'suggested_price': '12.50'}


class StaffGuidelineServiceTests(unittest.TestCase):
    def test_staff_can_read_active_catalog_with_verified_database_role(self):
        conn = ScriptedConnection([[ACTIVE_STAFF], [GUIDELINE]])
        rows = get_staff_guidelines(STAFF, lambda: conn)
        self.assertEqual(rows, [GUIDELINE])
        self.assertTrue(conn.closed)
        self.assertTrue(all(c.closed for c in conn.cursors))
        self.assertEqual(conn.commits, 0)
        self.assertEqual(len(conn.statements), 2)
        sql = conn.statements[1][0]
        self.assertIn('Guideline_Is_Active = 1', sql)
        self.assertIn('c.Item_Type_Id = g.Item_Type_Id', sql)
        for column in ('it.Item_Type', 'c.Category', 'b.Brand', 'ic.Condition'):
            self.assertIn(column + '_Is_Active = 1', sql)
        for table in ('ITEM_TYPE', 'CATEGORY', 'BRAND', 'ITEM_CONDITION'):
            self.assertIn(table, sql)
        for word in ('UPDATE ', 'DELETE ', 'INSERT ', 'FOR SHARE'):
            self.assertNotIn(word, sql)

    def test_administrator_refused_before_opening_database(self):
        factory = Mock()
        with self.assertRaises(DashboardPermissionError):
            get_staff_guidelines(ADMIN, factory)
        factory.assert_not_called()

    def test_forged_staff_session_refused_at_database_role_check(self):
        conn = ScriptedConnection([[{'User_Type': 'Administrator',
                                    'User_Is_Active': 1, 'Admin_Id': 9,
                                    'Staff_Id': None}]])
        with self.assertRaises(DashboardPermissionError):
            get_staff_guidelines(STAFF, lambda: conn)
        self.assertEqual(len(conn.statements), 1)
        self.assertTrue(conn.closed)

    def test_inactive_staff_account_refused(self):
        conn = ScriptedConnection([[dict(ACTIVE_STAFF, User_Is_Active=0)]])
        with self.assertRaises(DashboardPermissionError):
            get_staff_guidelines(STAFF, lambda: conn)

    def test_local_filters_do_not_change_source_rows(self):
        second = dict(GUIDELINE, guideline_id=11, item_type='Furniture',
                      category='Desks')
        original = [GUIDELINE, second]
        self.assertEqual(filter_guidelines(original, item_type='Clothing'), [GUIDELINE])
        self.assertEqual(filter_guidelines(original, category='Desks'), [second])
        self.assertEqual(filter_guidelines(original, search='sneakers'), [GUIDELINE])
        self.assertEqual(filter_guidelines(original, search='11'), [second])
        self.assertEqual(len(original), 2)


class StaffGuidelinePageTests(unittest.TestCase):
    def test_no_admin_ui_or_database_for_administrator(self):
        with patch.object(page, 'get_staff_guidelines') as service, \
             patch.object(page, 'st') as st:
            page.show_staff_guidelines(ADMIN)
        service.assert_not_called()
        st.error.assert_called_once()

    def test_staff_filters_show_active_prices_and_no_write_buttons(self):
        with patch.object(page, 'get_staff_guidelines', return_value=[GUIDELINE]), \
             patch.object(page, 'st') as st:
            st.session_state = {}
            st.columns.return_value = [nullcontext() for _ in range(4)]
            st.selectbox.side_effect = lambda label, options, **kwargs: options[0]
            st.text_input.return_value = ''
            st.button.return_value = False
            page.show_staff_guidelines(STAFF)
        table = st.dataframe.call_args.args[0]
        self.assertEqual(table[0]['Suggested Price'], '$12.50')
        self.assertEqual(table[0]['Brand Tier'], 'Premium')
        self.assertNotIn('Admin_Id', str(table))
        self.assertEqual(st.dataframe.call_args.kwargs['width'], 'stretch')
        labels = [call.args[0] for call in st.button.call_args_list]
        self.assertNotIn('Edit', labels)
        self.assertNotIn('Deactivate', labels)

    def test_staff_and_admin_routes_remain_separate(self):
        self.assertIn('staff_guidelines', router.STAFF_PAGES)
        self.assertNotIn('staff_guidelines', router.ADMIN_PAGES)
        self.assertNotIn('guidelines', router.STAFF_PAGES)
        self.assertIn('guidelines', router.ADMIN_PAGES)

    def test_staff_route_invokes_read_only_page(self):
        from _goodwill_test_helpers import FakeStreamlit
        fake_st = FakeStreamlit(session={'gw_nav_owner_id': 9,
                                         'gw_page': 'staff_guidelines'})
        mod = types.ModuleType('staff_guidelines_page')
        mod.show_staff_guidelines = Mock()
        with patch.object(router, 'st', fake_st), \
             patch.object(router, 'render_nav_header'), \
             patch.dict(sys.modules, {'staff_guidelines_page': mod}):
            router.render_authenticated_app(STAFF)
        mod.show_staff_guidelines.assert_called_once()
        self.assertEqual(mod.show_staff_guidelines.call_args.args[0], STAFF)


if __name__ == '__main__':
    unittest.main()
