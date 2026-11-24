"""Offline Reports data tests. No real database connection or writes."""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
import unittest

from _goodwill_test_helpers import ScriptedConnection
import reports_data as data

ADMIN = {'user_id': 7, 'user_type': 'Administrator'}
STAFF = {'user_id': 8, 'user_type': 'Store Staff'}


def fixture(reasons=None, staff=None, **metric_changes):
    metrics = {
        'items_priced': 5,
        'average_final_price': Decimal('12.50'),
        'override_count': 2,
        'active_staff_with_activity': 2,
    }
    metrics.update(metric_changes)
    return [
        [{'User_Id': 7}],
        [{'branch_id': 1, 'branch_name': 'Mount Pleasant'},
         {'branch_id': 2, 'branch_name': 'Lansing'}],
        [metrics],
        [{'category_name': 'Jeans', 'items_priced': 3, 'average_final_price': Decimal('15.5')}],
        reasons if reasons is not None else [{'reason': 'Stain', 'reason_count': 2}],
        staff if staff is not None else [{'staff_id': 8, 'staff_name': 'Ashley Brennan',
                                         'branch_name': 'Mount Pleasant',
                                         'is_active': 1, 'items_priced': 5,
                                         'average_final_price': Decimal('12.50'),
                                         'override_count': 2}],
    ]


class ReportDataTests(unittest.TestCase):
    def test_window_inclusive_30_calendar_days_and_exclusive_end(self):
        start, end = data.report_window(30, today=date(2026, 11, 22))
        self.assertEqual(start, datetime(2026, 10, 24))
        self.assertEqual(end, datetime(2026, 11, 23))
        self.assertEqual((end - start).days, 30)

    def test_invalid_period_and_branch_fail_before_connection(self):
        for d in (0, 31, '30', True, -1):
            with self.subTest(d=d), self.assertRaises(ValueError):
                data.report_window(d, today=date(2026, 11, 22))
        with self.assertRaises(ValueError):
            data.get_report_snapshot(ADMIN, branch_id='1', connection_factory=lambda: None)

    def test_staff_rejected_before_first_sql(self):
        con = ScriptedConnection([])
        with self.assertRaises(data.DashboardPermissionError):
            data.get_report_snapshot(STAFF, connection_factory=lambda: con)
        self.assertFalse(con.statements)
        self.assertTrue(con.closed)

    def test_admin_rechecked_in_db_and_denied_if_inactive(self):
        con = ScriptedConnection([[]])
        with self.assertRaises(data.DashboardPermissionError):
            data.get_report_snapshot(ADMIN, connection_factory=lambda: con)
        self.assertEqual(len(con.statements), 1)
        self.assertIn('User_Is_Active = 1', con.statements[0][0])
        self.assertTrue(con.closed)

    def test_report_all_branches_uses_real_groupings(self):
        con = ScriptedConnection(fixture())
        snap = data.get_report_snapshot(ADMIN, connection_factory=lambda: con,
                                        today=date(2026, 11, 22))
        self.assertEqual(snap['branch_name'], 'All branches')
        self.assertEqual(snap['metrics']['items_priced'], 5)
        self.assertEqual(snap['metrics']['override_rate'], .4)
        self.assertEqual(snap['metrics']['average_final_price'], Decimal('12.50'))
        self.assertEqual(snap['reasons'][0]['reason'], 'Stain')
        self.assertEqual(snap['reasons'][0]['share'], 1)
        self.assertEqual(len(snap['staff']), 1)
        self.assertEqual(len(con.statements), 6)
        self.assertEqual(con.statements[2][1], (datetime(2026, 10, 24), datetime(2026, 11, 23)))
        self.assertIn('Guideline_Suggested_Price', con.statements[2][0])
        self.assertIn('GROUP BY c.Category_Id', con.statements[3][0])
        self.assertIn('GROUP BY reason', con.statements[4][0])
        self.assertNotIn('AND d.Decision_Branch_Id = %s', con.statements[2][0])
        self.assertTrue(all(c.options['buffered'] for c in con.cursors))
        self.assertTrue(all(c.closed for c in con.cursors))
        self.assertTrue(con.closed)

    def test_branch_filter_uses_bound_parameters_in_all_queries(self):
        con = ScriptedConnection(fixture())
        snap = data.get_report_snapshot(ADMIN, branch_id=2,
                                        connection_factory=lambda: con,
                                        today=date(2026, 11, 22))
        self.assertEqual(snap['branch_name'], 'Lansing')
        for sql, params in con.statements[2:]:
            self.assertIn('AND d.Decision_Branch_Id = %s', sql)
            self.assertEqual(params[-1], 2)
            self.assertNotIn('Branch_Id = 2', sql)

    def test_unknown_branch_does_not_run_analytics_queries(self):
        con = ScriptedConnection(fixture())
        with self.assertRaisesRegex(ValueError, 'no longer exists'):
            data.get_report_snapshot(ADMIN, branch_id=123,
                                     connection_factory=lambda: con)
        self.assertEqual(len(con.statements), 2)
        self.assertTrue(con.closed)

    def test_empty_period_returns_zero_metrics_without_division(self):
        con = ScriptedConnection(fixture(reasons=[], staff=[], items_priced=0,
                    average_final_price=None, override_count=None,
                    active_staff_with_activity=None))
        snap = data.get_report_snapshot(ADMIN, connection_factory=lambda: con)
        self.assertEqual(snap['metrics']['override_rate'], 0)
        self.assertEqual(snap['metrics']['average_final_price'], Decimal('0'))
        self.assertEqual(snap['metrics']['active_staff_with_activity'], 0)
        self.assertEqual(snap['reasons'], [])

    def test_reason_bucket_top_three_and_other_share(self):
        rows = [{'reason':n,'reason_count':v} for n,v in
                [('A', 5), ('B',3), ('C',2), ('D',2), ('Other',1)]]
        result = data._reason_buckets(rows, 13)
        self.assertEqual([r['reason'] for r in result], ['A','B','C','Other'])
        self.assertEqual([r['count'] for r in result], [5,3,2,3])
        self.assertAlmostEqual(sum(r['share'] for r in result), 1)

    def test_duplicate_reason_names_are_aggregated(self):
        rows = [{'reason':'Stain','reason_count':1},{'reason':'Stain','reason_count':2}]
        result = data._reason_buckets(rows, 3)
        self.assertEqual(result, [{'reason':'Stain','count':3,'share':1}])

    def test_queries_are_read_only_and_historical_staff_kept(self):
        con = ScriptedConnection(fixture())
        data.get_report_snapshot(ADMIN, connection_factory=lambda: con)
        for sql, _ in con.statements:
            self.assertTrue(sql.strip().upper().startswith('SELECT'))
            self.assertNotIn('u.User_Is_Active = 1\n            GROUP BY', sql)
        self.assertIn('u.User_Is_Active AS is_active', con.statements[-1][0])
        self.assertEqual(con.commits, 0)

    def test_query_failure_still_closes_cursor_and_connection(self):
        con = ScriptedConnection([ [{'User_Id':7}], [] ], fail_sql='FROM BRANCH')
        with self.assertRaises(RuntimeError):
            data.get_report_snapshot(ADMIN, connection_factory=lambda: con)
        self.assertTrue(con.closed)
        self.assertTrue(all(c.closed for c in con.cursors))


class NewReportFilterTests(unittest.TestCase):
    def test_custom_dates_are_inclusive_with_exclusive_sql_end(self):
        start, end = data.custom_report_window(
            date(2026, 11, 1), date(2026, 11, 22), today=date(2026, 11, 22))
        self.assertEqual(start, datetime(2026, 11, 1))
        self.assertEqual(end, datetime(2026, 11, 23))

    def test_custom_dates_reject_invalid_reverse_future_or_excessive(self):
        for first, last in (
            (date(2026,11,20), date(2026,11,19)),
            (date(2026,11,21), date(2026,11,23)),
            (datetime(2026,11,1), date(2026,11,22)),
            (date(2011,11,1), date(2026,11,22)),
        ):
            with self.subTest(start=first,end=last), self.assertRaises(ValueError):
                data.custom_report_window(first, last, today=date(2026,11,22))

    def test_invalid_extra_filters_fail_before_opening_connection(self):
        bad = (
            {'staff_id': '5'}, {'category_id': True}, {'item_type_id': -1},
            {'adjustment': 'INVALID'}, {'adjustment': []},
            {'start_date': date(2026,11,1)},
            {'start_date': date(2026,11,1), 'end_date': date(2026,11,25)},
        )
        for kwargs in bad:
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                data.get_report_snapshot(ADMIN, connection_factory=lambda: self.fail('connected'),
                                         today=date(2026,11,22), **kwargs)

    def test_all_kpi_chart_and_staff_queries_share_combined_filters(self):
        con = ScriptedConnection(fixture())
        snap = data.get_report_snapshot(
            ADMIN, connection_factory=lambda: con,
            branch_id=2, staff_id=9, category_id=4, item_type_id=3,
            adjustment='decreased',
            start_date=date(2026,11,1), end_date=date(2026,11,22),
            today=date(2026,11,22))
        expected = (datetime(2026,11,1), datetime(2026,11,23), 2, 9, 4, 3)
        for sql, params in con.statements[2:]:
            self.assertEqual(params, expected)
            self.assertIn('AND d.Decision_Branch_Id = %s',sql)
            self.assertIn('AND d.Staff_Id = %s',sql)
            self.assertIn('AND i.Category_Id = %s',sql)
            self.assertIn('AND i.Item_Type_Id = %s',sql)
            self.assertIn('d.Decision_Final_Price < g.Guideline_Suggested_Price',sql)
            self.assertIn('JOIN ITEM AS i', sql)
            self.assertIn('JOIN PRICING_GUIDELINE AS g',sql)
        self.assertEqual(snap['staff_id'], 9)
        self.assertIsNone(snap['days'])
        self.assertEqual(len(con.statements),6)

    def test_price_change_filters_emit_correct_condition(self):
        expected = {'overrides':'<>', 'unchanged':'=', 'increased':'>', 'decreased':'<'}
        for label, operator in expected.items():
            with self.subTest(label=label):
                where, values = data._conditions(datetime(2026,1,1),
                                                  datetime(2026,1,2),None,
                                                  adjustment=label)
                self.assertIn(f'd.Decision_Final_Price {operator} g.Guideline_Suggested_Price', where)
                self.assertEqual(len(values), 2)

    def test_filter_options_include_inactive_staff_and_are_read_only(self):
        con = ScriptedConnection([
            [{'User_Id':7}],
            [{'branch_id':1,'branch_name':'Mount Pleasant'}],
            [{'staff_id':9,'staff_name':'Former Staff','branch_id':1,'is_active':0}],
            [{'category_id':4,'category_name':'Jeans'}],
            [{'item_type_id':3,'item_type_name':'Clothing'}],
        ])
        result = data.get_report_filter_options(ADMIN, connection_factory=lambda:con)
        self.assertEqual(result['staff'][0]['is_active'],0)
        self.assertEqual(result['categories'][0]['category_name'],'Jeans')
        self.assertEqual(len(con.statements),5)
        self.assertIn('FROM ITEM_TYPE',con.statements[-1][0])
        self.assertTrue(all(sql.strip().upper().startswith('SELECT') for sql,_ in con.statements))
        self.assertTrue(all(x.closed for x in con.cursors))
        self.assertTrue(con.closed)

    def test_staff_cannot_load_filter_options(self):
        con=ScriptedConnection([])
        with self.assertRaises(data.DashboardPermissionError):
            data.get_report_filter_options(STAFF, connection_factory=lambda:con)
        self.assertEqual(con.statements,[])
        self.assertTrue(con.closed)


if __name__ == '__main__':
    unittest.main()
