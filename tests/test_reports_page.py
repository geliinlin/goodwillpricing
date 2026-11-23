"""Offline Reports page tests including export and escaping. No MySQL connection."""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
import unittest
from unittest.mock import patch

from _goodwill_test_helpers import ensure_streamlit_importable, FakeStreamlit
ensure_streamlit_importable()
import reports_page as page

ADMIN = {'user_id': 7, 'user_type': 'Administrator'}
STAFF = {'user_id': 4, 'user_type': 'Store Staff'}


def sample_report():
    return {'start':datetime(2026, 10, 24), 'end':datetime(2026, 11, 23),
            'branch_name':'All branches', 'days':30, 'branch_id':None,
            'branches':[{'branch_id':1, 'branch_name':'Mount Pleasant'}],
            'metrics':{'items_priced':5, 'average_final_price':Decimal('12.50'),
                       'override_count':2, 'override_rate':.4,
                       'active_staff_with_activity':1},
            'categories':[{'category_name':'Jeans','average_final_price':Decimal('15.00'),
                           'items_priced':5}],
            'reasons':[{'reason':'Stain','count':2,'share':1.0}],
            'staff':[{'staff_name':'Ashley Brennan','branch_name':'Mount Pleasant',
                      'is_active':1,'items_priced':5,
                      'average_final_price':Decimal('12.50'),'override_count':2}],
            'staff_limit':500}


class ReportPageTests(unittest.TestCase):
    def setUp(self):
        self.ui = FakeStreamlit()
        self.spy = patch.object(page, 'st', self.ui).start()
        self.addCleanup(patch.stopall)
        self.sty = patch.object(page, 'load_styles').start()
        self.options = patch.object(page, 'get_report_filter_options', return_value={
            'branches':[{'branch_id':1,'branch_name':'Mount Pleasant'},
                        {'branch_id':2,'branch_name':'Lansing'}],
            'staff':[{'staff_id':8,'staff_name':'Ashley Brennan',
                      'branch_id':1,'is_active':1},
                     {'staff_id':9,'staff_name':'Former Staff',
                      'branch_id':2,'is_active':0}],
            'categories':[{'category_id':4,'category_name':'Jeans'}],
            'item_types':[{'item_type_id':3,'item_type_name':'Clothing'}],
        }).start()
        self.query = patch.object(page, 'get_report_snapshot', return_value=sample_report()).start()
        self.pdf_patch = patch.object(page, 'build_reports_pdf', return_value=b'%PDF-sample')
        self.pdf = self.pdf_patch.start()

    def test_admin_page_has_real_metrics_sections_and_pdf(self):
        page.show_admin_reports(ADMIN)
        html = ''.join(self.ui.styles)
        self.assertIn('Pricing Reports &amp; Analytics', html)
        self.assertIn('Total Items Priced', html)
        self.assertIn('Average Final Price', html)
        self.assertIn('40.0%', html)
        self.assertIn('Most Common Override Reasons', html)
        self.assertIn('Staff Pricing Activity', html)
        self.assertEqual(len(self.ui.downloads), 1)
        self.assertTrue(self.ui.downloads[0][1]['data'].startswith(b'%PDF'))
        self.sty.assert_called_once_with('reports.css')

    def test_staff_does_not_load_report_data_or_pdf(self):
        page.show_admin_reports(STAFF)
        self.assertTrue(self.ui.errors)
        self.query.assert_not_called()
        self.pdf.assert_not_called()

    def test_existing_filter_state_passes_into_query(self):
        self.ui.session_state.update(gw_reports_days=90, gw_reports_branch=1)
        page.show_admin_reports(ADMIN)
        params = self.query.call_args.kwargs
        self.assertEqual(params['days'], 90)
        self.assertEqual(params['branch_id'], 1)
        self.assertIsNone(params['staff_id'])

    def test_html_escapes_db_strings(self):
        report = sample_report()
        report['staff'][0]['staff_name'] = '<img src=x onerror=alert(1)>'
        report['categories'][0]['category_name'] = '" onmouseover="bad'
        report['reasons'][0]['reason'] = '<script>alert(1)</script>'
        self.query.return_value = report
        page.show_admin_reports(ADMIN)
        html = ''.join(self.ui.styles)
        self.assertNotIn('<script>', html)
        self.assertNotIn('<img src=x', html)
        self.assertIn('&lt;script&gt;', html)
        self.assertIn('&lt;img', html)
        self.assertIn('&quot; onmouseover=&quot;', html)

    def test_empty_report_does_not_crash(self):
        report = sample_report()
        report['metrics'].update(items_priced=0, override_rate=0, override_count=0)
        report['categories'], report['reasons'], report['staff'] = [], [], []
        self.query.return_value = report
        page.show_admin_reports(ADMIN)
        self.assertFalse(self.ui.errors)
        self.assertEqual(len(self.ui.downloads), 1)

    def test_permission_error_is_safe_for_user(self):
        self.query.side_effect = page.DashboardPermissionError('secret internal message')
        page.show_admin_reports(ADMIN)
        self.assertTrue(self.ui.errors)
        self.assertNotIn('secret internal message', self.ui.errors[0])

    def test_pdf_builder_returns_real_pdf_bytes(self):
        self.pdf_patch.stop()  # exercise real reportlab implementation
        value = page.build_reports_pdf(sample_report())
        self.assertTrue(value.startswith(b'%PDF-'))
        self.assertGreater(len(value), 1800)

    def test_staff_table_limits_initial_view(self):
        report = sample_report()
        report['staff'] = [dict(report['staff'][0], staff_name=f'Staff {i}') for i in range(12)]
        self.query.return_value = report
        page.show_admin_reports(ADMIN)
        rendered = ''.join(self.ui.styles)
        self.assertIn('Staff 4', rendered)
        self.assertNotIn('Staff 10', rendered)
        self.assertTrue(any(x[0] == 'Show all staff' for x in self.ui.button_calls))

    def test_reasons_percentages_dont_hardcode_example_numbers(self):
        report = sample_report()
        report['metrics'].update(override_count=4, items_priced=5, override_rate=.8)
        report['reasons'] = [{'reason':'Broken', 'count':3, 'share':.75},
                             {'reason':'Missing', 'count':1, 'share':.25}]
        self.query.return_value = report
        page.show_admin_reports(ADMIN)
        html = ''.join(self.ui.styles)
        self.assertIn('75%', html)
        self.assertIn('25%', html)
        self.assertNotIn('38%', html)

    def test_custom_date_and_advanced_fields_reach_single_report_query(self):
        from datetime import date
        self.ui.session_state.update(gw_reports_days='custom',
                                     gw_reports_branch=1, gw_reports_staff=8,
                                     gw_reports_category=4, gw_reports_item_type=3,
                                     gw_reports_adjustment='increased',
                                     gw_reports_start_date=date(2026,11,1),
                                     gw_reports_end_date=date(2026,11,22))
        page.show_admin_reports(ADMIN)
        params=self.query.call_args.kwargs
        self.assertEqual(params['start_date'],date(2026,11,1))
        self.assertEqual(params['end_date'],date(2026,11,22))
        self.assertEqual(params['staff_id'],8)
        self.assertEqual(params['category_id'],4)
        self.assertEqual(params['item_type_id'],3)
        self.assertEqual(params['adjustment'],'increased')
        self.assertIn('Ashley Brennan',''.join(self.ui.styles))
        self.assertIn('gw_reports_export',self.ui.downloads[0][1]['key'])

    def test_branch_change_clears_stale_staff_before_report(self):
        self.ui.session_state.update(gw_reports_branch=1, gw_reports_staff=9)
        page.show_admin_reports(ADMIN)
        self.assertNotIn('gw_reports_staff', self.ui.session_state)
        self.assertIsNone(self.query.call_args.kwargs['staff_id'])

    def test_reset_clears_all_report_filters_and_reruns(self):
        self.ui.session_state.update(gw_reports_days='custom',gw_reports_branch=1,
                                     gw_reports_staff=8, gw_reports_adjustment='overrides')
        self.ui.buttons['gw_reports_reset']=True
        page.show_admin_reports(ADMIN)
        self.assertEqual(self.ui.reruns,1)
        self.query.assert_not_called()
        for key in page.FILTER_KEYS:
            self.assertNotIn(key,self.ui.session_state)

    def test_permissions_block_staff_before_filter_options_query(self):
        page.show_admin_reports(STAFF)
        self.options.assert_not_called()
        self.query.assert_not_called()

    def test_filter_options_failure_is_safe_and_stops_report(self):
        self.options.side_effect=RuntimeError('private db hostname')
        page.show_admin_reports(ADMIN)
        self.query.assert_not_called()
        self.assertNotIn('private db hostname',''.join(self.ui.errors))

    def test_escaped_selected_filter_name_does_not_inject_html(self):
        self.options.return_value['staff'][0]['staff_name']='<img onerror=1>'
        self.ui.session_state['gw_reports_staff']=8
        page.show_admin_reports(ADMIN)
        html=''.join(self.ui.styles)
        self.assertNotIn('<img onerror=1>',html)
        self.assertIn('&lt;img onerror=1&gt;',html)
        self.assertIn('All branches',html)


if __name__ == '__main__':
    unittest.main()
