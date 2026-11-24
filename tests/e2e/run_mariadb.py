"""Run from project root: .venv/bin/python tests/e2e/run_mariadb.py

Creates and discards its own loopback-only MariaDB, applies migrations to the
reconstructed workflow baseline, then exercises the production SQL directly.
"""
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from decimal import Decimal
from pathlib import Path
import sys
from threading import Barrier
import unittest
from unittest.mock import patch

import mysql.connector
from mariadb_fixture import fixture

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import auth
import dashboard_data as dashboard
from item_search import get_active_price_guideline, get_lookup_options
from pricing_decision import PricingSaveError, PricingValidationError, save_pricing_decision
from staff_guidelines_service import get_staff_attribute_descriptions, get_staff_guidelines


class MariaDBWorkflow(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.config, path = cls.enterClassContext(fixture())
        cls.password = (path / 'login-password').read_text()

    def connect(self):
        return mysql.connector.connect(**self.config)

    def sql(self, query, params=()):
        with self.connect() as conn, conn.cursor() as cur:
            cur.execute(query, params)
            rows = cur.fetchall() if cur.with_rows else []
            conn.commit()
            return rows

    def setUp(self):
        self.staff = {'user_id': 2, 'user_type': 'Store Staff'}
        self.admin = {'user_id': 1, 'user_type': 'Administrator'}
        self.draft = dict(staff_id=2, branch_id=1, item_type_id=1, category_id=1,
                          brand_id=1, condition_id=1, guideline_id=1,
                          suggested_price='12.50', item_received_date=date(2020, 1, 1))

    def counts(self):
        return self.sql('SELECT (SELECT COUNT(*) FROM ITEM),(SELECT COUNT(*) FROM PRICING_DECISION)')[0]

    def save(self, description, price='12.50', reason=None, **changes):
        draft = {**self.draft, 'item_description': description, **changes}
        return save_pricing_decision(2, draft, price, reason, self.connect)

    def history(self, user, **filters):
        return dashboard.get_filtered_history(user, factory=self.connect, **filters)

    def test_authenticate_and_refresh(self):
        with patch.object(auth, 'get_connection', self.connect):
            for identifier in ('staff_e2e', 'staff_e2e@example.invalid'):
                result = auth.authenticate(identifier, self.password)
                self.assertEqual((result['user_id'], result['branch_id']), (2, 1))
            self.assertIsNone(auth.authenticate('staff_e2e', 'incorrect'))
            self.assertIsNone(auth.authenticate('inactive_e2e', self.password))
            self.sql('UPDATE BRANCH SET Branch_Is_Active=0 WHERE Branch_Id=1')
            self.addCleanup(self.sql, 'UPDATE BRANCH SET Branch_Is_Active=1 WHERE Branch_Id=1')
            self.assertIsNone(auth.get_active_user(2))
            self.assertIsNone(auth.authenticate('staff_e2e', self.password))
            self.assertIsNotNone(auth.get_active_user(1))
            with self.assertRaises(dashboard.DashboardPermissionError):
                self.history(self.staff)

    def test_guidelines_and_lookup(self):
        rows = get_staff_guidelines(self.staff, self.connect)
        self.assertEqual([(r['guideline_id'], r['category'], r['suggested_price']) for r in rows],
                         [(1, 'Shirts', Decimal('12.50'))])
        descriptions = get_staff_attribute_descriptions(self.staff, self.connect)
        self.assertTrue(any(r['attribute_name'] == 'Shirts' and r['item_type_name'] == 'Clothing'
                            and r['description'] for r in descriptions))
        options = get_lookup_options(self.connect)
        self.assertEqual(options['Category_Item_Type'][1], 1)
        other_type = next(k for k, v in options['Item_Type_Id'].items() if v == 'Furniture')
        match = get_active_price_guideline(1, 1, 1, 1, self.connect)
        self.assertEqual((match.guideline_id, match.suggested_price), (1, Decimal('12.50')))
        self.assertEqual(get_active_price_guideline(other_type, 1, 1, 1, self.connect).status,
                         'not_found')

    def test_invalidated_and_ambiguous_quote(self):
        before = self.counts()
        self.addCleanup(self.sql, 'UPDATE PRICING_GUIDELINE SET Guideline_Is_Active=0 WHERE Guideline_Id=2')
        self.addCleanup(self.sql, 'UPDATE PRICING_GUIDELINE SET Guideline_Is_Active=1 WHERE Guideline_Id=1')
        self.sql('UPDATE PRICING_GUIDELINE SET Guideline_Is_Active=1 WHERE Guideline_Id=2')
        self.assertEqual(get_active_price_guideline(1, 1, 1, 1, self.connect).status, 'conflict')
        with self.assertRaises(PricingValidationError):
            self.save('conflicting quote')
        self.sql('UPDATE PRICING_GUIDELINE SET Guideline_Is_Active=0 WHERE Guideline_Id IN (1,2)')
        self.assertEqual(get_active_price_guideline(1, 1, 1, 1, self.connect).status, 'not_found')
        with self.assertRaises(PricingValidationError):
            self.save('withdrawn quote')
        self.assertEqual(self.counts(), before)

    def test_deactivated_attribute_and_changed_staff_after_quote(self):
        before = self.counts()
        self.sql('UPDATE BRAND SET Brand_Is_Active=0 WHERE Brand_Id=1')
        self.addCleanup(self.sql, 'UPDATE BRAND SET Brand_Is_Active=1 WHERE Brand_Id=1')
        self.assertNotIn(1, get_lookup_options(self.connect)['Brand_Id'])
        self.assertEqual(get_staff_guidelines(self.staff, self.connect), [])
        with self.assertRaises(PricingValidationError):
            self.save('inactive brand')
        self.sql('UPDATE BRAND SET Brand_Is_Active=1 WHERE Brand_Id=1')
        self.sql('UPDATE STORE_STAFF SET Branch_Id=2 WHERE Staff_Id=2')
        self.addCleanup(self.sql, 'UPDATE STORE_STAFF SET Branch_Id=1 WHERE Staff_Id=2')
        with self.assertRaisesRegex(PricingValidationError, 'store has changed'):
            self.save('old branch')
        self.sql('UPDATE USER_ACCOUNT SET User_Is_Active=0 WHERE User_Id=2')
        self.addCleanup(self.sql, 'UPDATE USER_ACCOUNT SET User_Is_Active=1 WHERE User_Id=2')
        with self.assertRaisesRegex(PricingValidationError, 'no longer authorized'):
            self.save('inactive account', branch_id=2)
        self.assertEqual(self.counts(), before)

    def test_history_and_price_links(self):
        before = self.counts()
        with self.assertRaises(PricingValidationError):
            self.save('invalid override', '8.50')
        self.assertEqual(self.counts(), before)
        older = self.save('Historical blue shirt')
        self.sql('UPDATE PRICING_DECISION SET Decision_Datetime=%s WHERE Decision_Id=%s',
                 ('2020-01-01 12:00:00', older.decision_id))
        newer = self.save('Recent yellow shirt', '8.50', 'Visible wear')
        self.assertNotEqual(newer.tag_number, older.tag_number)
        self.assertEqual(self.counts(), (before[0] + 2, before[1] + 2))
        links = self.sql('''SELECT i.Item_Id,d.Guideline_Id,d.Staff_Id,i.Item_Description,
                                   d.Decision_Final_Price,d.Decision_Reason
                            FROM ITEM i JOIN PRICING_DECISION d ON i.Item_Id=d.Item_Id
                            WHERE d.Decision_Id IN (%s,%s) ORDER BY d.Decision_Id''',
                         (older.decision_id, newer.decision_id))
        self.assertEqual(links, [(older.item_id, 1, 2, 'Historical blue shirt', Decimal('12.50'), None),
                                 (newer.item_id, 1, 2, 'Recent yellow shirt', Decimal('8.50'), 'Visible wear')])
        rows = self.history(self.staff, keyword='Historical blue', category_id=1, limit=1,
                            start_date=date(2020, 1, 1), end_date=date(2020, 1, 1),
                            price_change='No Override')
        self.assertEqual([r['tag_number'] for r in rows], [older.tag_number])
        self.assertEqual(rows[0]['decision_time'].date(), date(2020, 1, 1))
        self.assertEqual(rows[0]['suggested_price'], Decimal('12.50'))
        self.assertEqual(self.history(self.staff, keyword=newer.tag_number, price_change='Override')[0]['override_reason'],
                         'Visible wear')
        self.assertEqual(self.history(self.staff, keyword='Other fixture item'), [])
        self.assertEqual(len(self.history(self.admin, keyword='Other fixture item', staff_id=3, branch_id=2)), 1)
        self.assertEqual(self.history(self.admin, keyword='Other fixture item', branch_id=1), [])
        with self.assertRaises(dashboard.DashboardPermissionError):
            self.history(self.staff, staff_id=3)
        for _ in range(2):
            self.assertEqual(dashboard.get_reprint_receipt(self.staff, newer.tag_number, self.connect)['decision_id'],
                             newer.decision_id)
        self.assertIsNone(dashboard.get_reprint_receipt(self.staff, 'TS26-100001', self.connect))
        self.assertEqual(self.counts(), (before[0] + 2, before[1] + 2))

    def test_real_insert_failure_rolls_back_item_and_releases_lock(self):
        before = self.counts()
        self.sql("""CREATE TRIGGER fixture_reject_decision BEFORE INSERT ON PRICING_DECISION
                    FOR EACH ROW SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='fixture insertion failure'""")
        self.addCleanup(self.sql, 'DROP TRIGGER IF EXISTS fixture_reject_decision')
        with self.assertRaises(PricingSaveError) as err:
            self.save('rollback fixture')
        self.assertEqual(err.exception.stage, 'inserting PRICING_DECISION')
        self.assertEqual(self.counts(), before)
        self.assertNotIn('fixture insertion failure', str(err.exception))
        self.sql('DROP TRIGGER fixture_reject_decision')
        self.save('retry after rollback')
        self.assertEqual(self.counts(), (before[0] + 1, before[1] + 1))

    def test_concurrent_distinct_items_get_unique_ids_and_tags(self):
        before = self.counts()
        gate = Barrier(2)
        def submit(i):
            gate.wait(timeout=15)
            return self.save(f'Distinct concurrent item {i}')
        with ThreadPoolExecutor(max_workers=2) as pool:
            first, second = pool.map(submit, (1, 2))
        self.assertNotEqual(first.item_id, second.item_id)
        self.assertNotEqual(first.decision_id, second.decision_id)
        self.assertNotEqual(first.tag_number, second.tag_number)
        self.assertEqual(self.sql(
            'SELECT Decision_Branch_Id FROM PRICING_DECISION WHERE Decision_Id IN (%s,%s)',
            (first.decision_id, second.decision_id)), [(1,), (1,)])
        self.assertEqual(self.counts(), (before[0] + 2, before[1] + 2))


if __name__ == '__main__':
    unittest.main(verbosity=2)
