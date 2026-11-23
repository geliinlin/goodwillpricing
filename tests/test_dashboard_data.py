"""Dashboard SQL security, aggregation, history scoping and reprint queries."""
import unittest
from decimal import Decimal
from _goodwill_test_helpers import ScriptedConnection
import dashboard_data as data

ADMIN = {"user_id": 1, "user_type": "Administrator"}
STAFF = {"user_id": 5, "user_type": "Store Staff"}


def verified(role="Store Staff", *, active=1, subtype=True):
    return {
        "User_Type": "Administrator" if role == "Administrator" else role,
        "User_Is_Active": active,
        "Admin_Id": 1 if role == "Administrator" and subtype else None,
        "Staff_Id": 5 if role != "Administrator" and subtype else None,
    }


class DashboardDataTests(unittest.TestCase):
    def test_staff_subtype_and_active_account_verified(self):
        con = ScriptedConnection([[verified()]])
        self.assertEqual(data._verify_user(con, STAFF), ("Store Staff", 5))
        self.assertEqual(con.statements[0][1], (5,))
        self.assertTrue(con.cursors[0].options["buffered"])
        self.assertTrue(con.cursors[0].closed)
        self.assertIn("b.Branch_Id = s.Branch_Id AND b.Branch_Is_Active = 1",
                      con.statements[0][0])

    def test_employee_in_database_accepted_for_staff_session(self):
        con = ScriptedConnection([[verified("Employee")]])
        self.assertEqual(data._verify_user(con, STAFF), ("Store Staff", 5))

    def test_reject_inactive_or_missing_subtype(self):
        for row in (verified(active=0), verified(subtype=False), None):
            with self.subTest(row=row):
                con = ScriptedConnection([[row] if row else []])
                with self.assertRaises(data.DashboardPermissionError):
                    data._verify_user(con, STAFF)

    def test_reject_session_role_mismatch(self):
        con = ScriptedConnection([[verified("Administrator")]])
        with self.assertRaises(data.DashboardPermissionError):
            data._verify_user(con, STAFF)

    def test_reject_unrecognized_roles_before_sql(self):
        with self.assertRaises(data.DashboardPermissionError):
            data._verify_user(ScriptedConnection([]), {"user_type": "Visitor", "user_id": 5})

    def test_admin_kpis_and_recent_global_query(self):
        metrics = {"active_guidelines": 336, "active_users": 6, "decisions": 8, "overrides": 2}
        con = ScriptedConnection([[verified("Administrator")], [metrics], [{"decision_id": 10}]])
        output = data.get_admin_snapshot(ADMIN, lambda: con)
        self.assertEqual(output["metrics"], metrics)
        self.assertEqual(output["recent"], [{"decision_id": 10}])
        self.assertIn("Guideline_Is_Active = 1", con.statements[1][0])
        self.assertNotIn("WHERE d.Staff_Id", con.statements[2][0])
        self.assertTrue(con.closed)
        self.assertTrue(all(c.closed for c in con.cursors))

    def test_staff_kpis_are_scoped_to_authenticated_user(self):
        con = ScriptedConnection([[verified("Employee")], [
            {"my_decisions": 3, "today_decisions": None, "my_overrides": 1}
        ], [{"decision_id": 8}]])
        output = data.get_staff_snapshot(STAFF, lambda: con)
        self.assertEqual(output["metrics"], {"my_decisions": 3, "today_decisions": 0, "my_overrides": 1})
        self.assertIn("WHERE d.Staff_Id = %s", con.statements[1][0])
        self.assertEqual(con.statements[1][1], (5,))
        self.assertEqual(con.statements[2][1], (5,))
        self.assertTrue(con.closed)

    def test_admin_cannot_request_staff_snapshot(self):
        con = ScriptedConnection([[verified("Administrator")]])
        with self.assertRaises(data.DashboardPermissionError):
            data.get_staff_snapshot(ADMIN, lambda: con)
        self.assertEqual(len(con.statements), 1)
        self.assertTrue(con.closed)

    def test_history_limits_are_clamped_without_sql_injection(self):
        con = ScriptedConnection([[verified()], []])
        data.get_history(STAFF, limit=999999, factory=lambda: con)
        self.assertIn("LIMIT 500", con.statements[-1][0])
        self.assertEqual(con.statements[-1][1], (5,))
        self.assertNotIn("LIMIT 999999", con.statements[-1][0])

    def test_admin_history_allows_all_stores(self):
        con = ScriptedConnection([[verified("Administrator")], []])
        data.get_history(ADMIN, limit=2, factory=lambda: con)
        self.assertNotIn("WHERE d.Staff_Id", con.statements[-1][0])
        self.assertEqual(con.statements[-1][1], ())

    def test_reprint_scoped_to_staff_and_does_not_write(self):
        receipt = {"tag_number": "MP26-100012", "final_price": Decimal("12.00")}
        con = ScriptedConnection([[verified()], [receipt]])
        self.assertEqual(data.get_reprint_receipt(STAFF, " MP26-100012 ", lambda: con), receipt)
        self.assertEqual(con.statements[-1][1], ("MP26-100012", 5))
        self.assertTrue(all("INSERT" not in sql.upper() and "UPDATE" not in sql.upper() for sql, _ in con.statements))
        self.assertTrue(con.closed)

    def test_administrator_cannot_reprint_through_staff_endpoint(self):
        con = ScriptedConnection([[verified("Administrator")]])
        with self.assertRaises(data.DashboardPermissionError):
            data.get_reprint_receipt(ADMIN, "MP26-100012", lambda: con)
        self.assertEqual(len(con.statements), 1)

    def test_invalid_tag_short_circuits_before_database(self):
        called = []
        self.assertIsNone(data.get_reprint_receipt(STAFF, " ", lambda: called.append(1)))
        self.assertIsNone(data.get_reprint_receipt(STAFF, "X" * 21, lambda: called.append(1)))
        self.assertEqual(called, [])

    def test_query_exception_closes_cursor_and_connection(self):
        con = ScriptedConnection([[]], fail_sql="SELECT")
        with self.assertRaises(RuntimeError):
            data.get_history(STAFF, factory=lambda: con)
        self.assertTrue(con.closed)
        self.assertTrue(all(c.closed for c in con.cursors))

    def test_currency_format(self):
        self.assertEqual(data.as_money(Decimal("1234.5")), "$1,234.50")


if __name__ == "__main__":
    unittest.main()
