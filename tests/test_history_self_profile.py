"""Tests for History SQL filtering and privilege-scoped administrator self edits.

Offline only: ScriptedConnection never contacts the real MariaDB instance.
"""
import unittest
from datetime import date
from unittest.mock import patch, Mock
from _goodwill_test_helpers import ScriptedConnection, FakeStreamlit, ensure_streamlit_importable

ensure_streamlit_importable()
import dashboard_data as data
import dashboard_page as dashboard
import users_service as users
import users_page as user_page

ADMIN = {'user_id': 1, 'user_type': 'Administrator', 'can_manage_users_config': True}
STAFF = {'user_id': 5, 'user_type': 'Store Staff'}


def verified(role='Store Staff'):
    return {'User_Type': role, 'User_Is_Active': 1,
            'Admin_Id': 1 if role == 'Administrator' else None,
            'Staff_Id': 5 if role == 'Store Staff' else None}


class HistoryFilterSecurityTests(unittest.TestCase):
    def test_all_admin_filters_are_in_sql_before_limit(self):
        conn = ScriptedConnection([[verified('Administrator')], [{'decision_id': 12}]])
        result = data.get_filtered_history(ADMIN, keyword='jacket', days=30,
            category_id=2, branch_id=3, staff_id=5,
            price_change='Override', limit=200, factory=lambda: conn)
        self.assertEqual(result, [{'decision_id': 12}])
        sql, params = conn.statements[-1]
        for expected in ('ss.Branch_Id = %s', 'd.Staff_Id = %s',
                         'i.Category_Id = %s', 'Decision_Tag_Number LIKE %s',
                         'd.Decision_Reason AS override_reason',
                         'Decision_Final_Price <> g.Guideline_Suggested_Price',
                         'ORDER BY', 'LIMIT 200'):
            self.assertIn(expected, sql)
        self.assertIn('INTERVAL 29 DAY', sql)
        self.assertEqual(params, (5, 3, 2, '%jacket%', '%jacket%', '%jacket%'))
        self.assertTrue(conn.closed)
        self.assertFalse(any('UPDATE ' in st or 'INSERT ' in st for st, _ in conn.statements))

    def test_staff_scoped_by_verified_user_id(self):
        conn = ScriptedConnection([[verified()], []])
        data.get_filtered_history(STAFF, category_id=2,
                                  price_change='No Override', factory=lambda: conn)
        sql, params = conn.statements[-1]
        self.assertIn('d.Staff_Id = %s', sql)
        self.assertIn('Decision_Final_Price = g.Guideline_Suggested_Price', sql)
        self.assertEqual(params, (5, 2))

    def test_staff_cannot_supply_foreign_staff_or_branch(self):
        for values in ({'staff_id': 1}, {'branch_id': 2}):
            with self.subTest(values=values):
                conn = ScriptedConnection([[verified()]])
                with self.assertRaises(data.DashboardPermissionError):
                    data.get_filtered_history(STAFF, factory=lambda: conn, **values)
                self.assertEqual(len(conn.statements), 1)

    def test_custom_date_range_is_inclusive(self):
        conn = ScriptedConnection([[verified()], []])
        data.get_filtered_history(STAFF, start_date=date(2026,11,1),
                                  end_date=date(2026,11,23), factory=lambda: conn)
        sql, params = conn.statements[-1]
        self.assertIn('Decision_Datetime < %s', sql)
        self.assertEqual(params, (5, date(2026,11,1), date(2026,11,24)))

    def test_bad_filters_are_rejected_before_connecting(self):
        checks = ({'branch_id': True}, {'staff_id': -1}, {'days': 365},
                  {'price_change': 'anything'},
                  {'start_date': date(2026,11,20), 'end_date': date(2026,11,1)},
                  {'keyword': 'x' * 101}, {'limit': 0})
        for values in checks:
            with self.subTest(values=values):
                factory = Mock()
                with self.assertRaises(ValueError):
                    data.get_filtered_history(ADMIN, factory=factory, **values)
                factory.assert_not_called()

    def test_staff_category_options_only_own_history(self):
        conn = ScriptedConnection([[verified()], [{'id':2,'name':'Shirts'}]])
        options = data.get_history_filter_options(STAFF, factory=lambda:conn)
        self.assertEqual(options['categories'], [{'id':2,'name':'Shirts'}])
        self.assertEqual(options['branches'], [])
        self.assertEqual(options['staff'], [])
        self.assertEqual(conn.statements[-1][1], (5,))

    def test_admin_can_filter_inactive_staff_historical_rows(self):
        conn = ScriptedConnection([[verified('Administrator')], [], [],
                                   [{'id':11,'first_name':'Old','last_name':'Staff'}]])
        options = data.get_history_filter_options(ADMIN, factory=lambda:conn)
        self.assertEqual(options['staff'][0]['id'], 11)
        self.assertTrue(all('UPDATE ' not in sql for sql, _ in conn.statements))


class HistoryPageTests(unittest.TestCase):
    def test_history_displays_saved_prices_and_escaped_override_reason(self):
        ui = FakeStreamlit()
        row = dict(description="Test Item", category="Books", final_price=6,
                   first_name="Test", last_name="Staff", tag_number="GW26-100001",
                   decision_time="2026-11-22", suggested_price=7,
                   override_reason="Cover <damage>")
        with patch.object(dashboard, 'st', ui):
            dashboard._activity([row], admin=True, history=True)
        markup = ''.join(ui.markdowns)
        for value in ("Test Item", "Books", "$6.00", "Suggested $7.00", "Test Staff",
                      "GW26-100001", "2026-11-22", "Override reason: Cover &lt;damage&gt;"):
            self.assertIn(value, markup)

    def test_failed_reprint_keeps_saved_receipt_and_hides_internal_error(self):
        tag = "GW26-100012"
        ui = FakeStreamlit(session={"gw_reprint_search": tag})
        receipt = dict(tag_number=tag, description="QA", category="Books",
                       final_price="6.00", brand="Basic", condition="Good",
                       branch_name="QA Store")
        with patch.object(dashboard, "st", ui), \
             patch.object(dashboard, "get_reprint_receipt", return_value=receipt) as lookup, \
             patch("pricing_decision.printable_label_pdf", side_effect=ValueError("private")), \
             self.assertLogs(dashboard.LOG, level="ERROR"):
            dashboard.show_reprint_tag(STAFF, Mock())
        lookup.assert_called_once_with(STAFF, tag)
        self.assertEqual(ui.session_state["gw_reprint_search"], tag)
        self.assertEqual(ui.errors, ["Unable to generate the printable PDF from this saved tag."])
        self.assertFalse(ui.downloads)

    def test_admin_passes_all_selected_filters_to_database(self):
        ui = FakeStreamlit(selects={
            'gw_history_admin_date': 'Last 7 days',
            'gw_history_admin_change': 'Override',
            'gw_history_admin_category': 2,
            'gw_history_admin_branch': 3,
            'gw_history_admin_staff': 5,
        }, inputs={'gw_history_admin_search':'MP26'})
        opts={'categories':[{'id':2,'name':'Clothing'}],
              'branches':[{'id':3,'name':'Mount Pleasant'}],
              'staff':[{'id':5,'first_name':'Ashley','last_name':'Brennan'}]}
        with patch.object(dashboard, 'st', ui), \
             patch.object(dashboard, 'get_history_filter_options', return_value=opts), \
             patch.object(dashboard, 'get_filtered_history', return_value=[]) as query, \
             patch.object(dashboard, '_activity'):
            dashboard.show_pricing_history(ADMIN, Mock())
        self.assertEqual(query.call_args.kwargs['keyword'], 'MP26')
        self.assertEqual(query.call_args.kwargs['days'], 7)
        self.assertEqual(query.call_args.kwargs['category_id'], 2)
        self.assertEqual(query.call_args.kwargs['branch_id'], 3)
        self.assertEqual(query.call_args.kwargs['staff_id'], 5)
        self.assertEqual(query.call_args.kwargs['price_change'], 'Override')

    def test_staff_does_not_see_branch_or_staff_filters(self):
        ui = FakeStreamlit()
        seen_labels = []
        original = ui.selectbox
        def capture(label, options, *args, **kwargs):
            seen_labels.append(label)
            return original(label, options, *args, **kwargs)
        ui.selectbox = capture
        with patch.object(dashboard,'st',ui), \
             patch.object(dashboard,'get_history_filter_options',
                          return_value={'categories': [], 'branches': [], 'staff': []}), \
             patch.object(dashboard,'get_filtered_history',return_value=[]), \
             patch.object(dashboard,'_activity'):
            dashboard.show_pricing_history(STAFF, Mock())
        self.assertNotIn('Branch', seen_labels)
        self.assertNotIn('Staff', seen_labels)
        self.assertIn('Category', seen_labels)
        self.assertEqual(ui.column_specs[0][0], [2,1,1])


class SelfProfileProtectionTests(unittest.TestCase):
    def test_self_email_change_revokes_tokens_and_sessions(self):
        actor = {'user_id': 1, 'user_type': 'Administrator',
                 'can_manage_users_config': True}
        actor_row = {'User_Type': 'Administrator', 'User_Is_Active': 1,
                     'Admin_Id': 1, 'Staff_Id': None, 'can_manage': 1}
        target_row = {'User_Id': 1, 'User_Type': 'Administrator',
                      'User_Is_Active': 1, 'can_manage': 1}
        current = {'User_First_Name':'Karen', 'User_Last_Name':'Whitfield',
                   'User_Username':'kwhitfield', 'User_Email':'old@example.org',
                   'User_Type':'Administrator', 'Branch_Id':None}
        conn = ScriptedConnection([[{'acquired':1}], [actor_row], [target_row],
                                   [current], [], [], [], [], [], [{'released':1}]])
        result = users.update_user_profile(actor, 1,
            first_name='Karen', last_name='Whitfield', username='kwhitfield',
            email='new@example.org', branch_id=None,
            expected_profile={'User_First_Name':'Karen','User_Last_Name':'Whitfield',
                              'User_Username':'kwhitfield','User_Email':'old@example.org',
                              'Branch_Id': None},
            connection_factory=lambda:conn)
        self.assertTrue(result['email_changed'])
        sql='\n'.join(q for q,_ in conn.statements)
        self.assertIn('UPDATE USER_PASSWORD_TOKEN',sql)
        self.assertIn('Session_Version=Session_Version+1',sql)
        self.assertNotIn('UPDATE USER_MODULE_ACCESS',sql)
        self.assertEqual(conn.commits,1)

    def test_unprivileged_admin_cannot_self_edit(self):
        actor={'user_id':1, 'user_type':'Administrator',
               'can_manage_users_config':False}
        with patch.object(users, 'get_connection') as open_db:
            with self.assertRaises(users.UserManagementPermissionError):
                users.update_user_profile(actor,1,first_name='Karen',
                    last_name='Whitfield',username='kwhitfield',
                    email='valid@example.org',branch_id=None,
                    expected_profile={'User_First_Name':'Karen',
                        'User_Last_Name':'Whitfield','User_Username':'kwhitfield',
                        'User_Email':'valid@example.org','Branch_Id':None})
            open_db.assert_not_called()

    def test_status_and_management_still_disallow_self(self):
        # Existing _can_modify remains the gate for these services.
        self.assertTrue(any('own account' in str(s) for s in users._can_modify.__code__.co_consts))
        self.assertIn('_can_modify(actor, role, t)',
                      __import__('inspect').getsource(users.set_account_active))
        self.assertIn('_can_modify(actor, role, t)',
                      __import__('inspect').getsource(users.set_management_access))
