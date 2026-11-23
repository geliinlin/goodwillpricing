"""Self-contained tests for dashboard_data.py (no MySQL server required)."""
import unittest

from dashboard_data import (
    DashboardPermissionError,
    get_admin_snapshot,
    get_staff_snapshot,
    get_history,
    get_reprint_receipt,
)


class FakeCursor:
    def __init__(self, conn):
        self.conn = conn
        self.result = []
        self.closed = False

    def execute(self, sql, params=()):
        self.conn.queries.append((sql, tuple(params)))
        if 'FROM USER_ACCOUNT AS u' in sql:
            self.result = [self.conn.identity]
        elif 'SELECT COUNT(*) AS my_decisions' in sql:
            self.result = [{'my_decisions': 8, 'today_decisions': 2, 'my_overrides': 3}]
        elif 'AS active_guidelines' in sql:
            self.result = [{'active_guidelines': 4, 'active_users': 5,
                            'decisions': 11, 'overrides': 3}]
        elif 'WHERE d.Decision_Tag_Number = %s' in sql:
            self.result = ([{'tag_number': params[0], 'final_price': 4.50}]
                           if params[0] == 'TAG1' else [])
        else:
            self.result = [{'decision_id': 1, 'tag_number': 'TAG1',
                            'description': 'T-shirt', 'final_price': 4.50}]

    def fetchall(self):
        return list(self.result)

    def close(self):
        self.closed = True
        self.conn.closed_cursors += 1


class FakeConnection:
    def __init__(self, identity):
        self.identity = identity
        self.closed = False
        self.queries = []
        self.closed_cursors = 0

    def cursor(self, *, dictionary=False, buffered=False):
        assert dictionary is True and buffered is True
        return FakeCursor(self)

    def close(self):
        self.closed = True


class DashboardDataTests(unittest.TestCase):
    def mk(self, role):
        identity = {'User_Is_Active': 1,
                    'User_Type': 'Administrator' if role == 'Administrator' else 'Employee',
                    'Admin_Id': 3 if role == 'Administrator' else None,
                    'Staff_Id': None if role == 'Administrator' else 7}
        self.conn = FakeConnection(identity)
        return self.conn

    def test_admin_counts_and_all_activities(self):
        user = {'user_id': 3, 'user_type': 'Administrator'}
        data = get_admin_snapshot(user, factory=lambda: self.mk('Administrator'))
        self.assertEqual(data['metrics']['active_guidelines'], 4)
        self.assertEqual(len(data['recent']), 1)
        self.assertTrue(self.conn.closed)
        self.assertEqual(len(self.conn.queries), self.conn.closed_cursors)
        recent_query = self.conn.queries[-1]
        self.assertNotIn('WHERE d.Staff_Id = %s', recent_query[0])

    def test_staff_queries_are_scoped_to_own_id(self):
        user = {'user_id': 7, 'user_type': 'Store Staff'}
        data = get_staff_snapshot(user, factory=lambda: self.mk('Store Staff'))
        self.assertEqual(data['metrics']['today_decisions'], 2)
        self.assertTrue(self.conn.closed)
        self.assertIn('WHERE d.Staff_Id = %s', self.conn.queries[-1][0])
        self.assertEqual(self.conn.queries[-1][1], (7,))

    def test_staff_cannot_access_admin_stats(self):
        user = {'user_id': 7, 'user_type': 'Store Staff'}
        with self.assertRaises(DashboardPermissionError):
            get_admin_snapshot(user, factory=lambda: self.mk('Store Staff'))
        self.assertTrue(self.conn.closed)

    def test_inactive_user_is_denied(self):
        user = {'user_id': 7, 'user_type': 'Store Staff'}
        conn = self.mk('Store Staff')
        conn.identity['User_Is_Active'] = 0
        with self.assertRaises(DashboardPermissionError):
            get_staff_snapshot(user, factory=lambda: conn)

    def test_staff_history_limited_to_staff_records(self):
        user = {'user_id': 7, 'user_type': 'Store Staff'}
        get_history(user, factory=lambda: self.mk('Store Staff'))
        self.assertEqual(self.conn.queries[-1][1], (7,))

    def test_reprint_uses_existing_tag_and_staff_scope(self):
        user = {'user_id': 7, 'user_type': 'Store Staff'}
        receipt = get_reprint_receipt(user, 'TAG1', factory=lambda: self.mk('Store Staff'))
        self.assertEqual(receipt['tag_number'], 'TAG1')
        self.assertIn('WHERE d.Decision_Tag_Number = %s AND d.Staff_Id = %s',
                      self.conn.queries[-1][0])
        self.assertEqual(self.conn.queries[-1][1], ('TAG1', 7))
        self.assertFalse(any('INSERT INTO' in q[0] for q in self.conn.queries))

    def test_admin_cannot_use_staff_reprint(self):
        user = {'user_id': 3, 'user_type': 'Administrator'}
        with self.assertRaises(DashboardPermissionError):
            get_reprint_receipt(user, 'TAG1', factory=lambda: self.mk('Administrator'))


if __name__ == '__main__':
    unittest.main()
