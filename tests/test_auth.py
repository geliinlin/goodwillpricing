"""Current auth.py contract: user_type, email/username, bcrypt and DB re-check.

All connections and password operations are simulated; no real MySQL is used.
"""
import unittest
from unittest.mock import Mock, patch

from _goodwill_test_helpers import ScriptedConnection
import auth


def sample_user(**changes):
    row = {
        "User_Id": 7, "User_First_Name": "Test", "User_Last_Name": "User",
        "User_Username": "testuser", "User_Is_Active": 1,
        "User_Type": "Administrator", "Admin_Id": 7, "Staff_Id": None,
        "Branch_Id": None, "Branch_Name": None,
    }
    row.update(changes)
    return row


class AuthenticationTests(unittest.TestCase):
    def test_staff_login_checks_real_bcrypt_password_by_username_and_email(self):
        correct = "s" * 72
        hashed = auth.bcrypt.hashpw(correct.encode(), auth.bcrypt.gensalt(4)).decode()
        row = sample_user(User_Type="Store Staff", Admin_Id=None, Staff_Id=7,
                          Branch_Id=2, Branch_Name="Test branch", User_Password_Hash=hashed)
        for identifier in ("testuser", "test@example.org"):
            with self.subTest(identifier=identifier):
                conn = ScriptedConnection([[row]])
                with patch.object(auth, "get_connection", return_value=conn):
                    self.assertEqual(auth.authenticate(identifier, correct)["user_type"], "Store Staff")
                self.assertEqual(conn.statements[0][1], (identifier, identifier))
        for password, active in (("incorrect", 1), (correct + "x", 1), (correct, 0)):
            with self.subTest(active=active, length=len(password)):
                conn = ScriptedConnection([[dict(row, User_Is_Active=active)]])
                with patch.object(auth, "get_connection", return_value=conn):
                    self.assertIsNone(auth.authenticate("testuser", password))

    def test_recheck_requires_matched_active_branch_for_staff(self):
        conn = ScriptedConnection([[sample_user(User_Type="Store Staff", Admin_Id=None,
                                                Staff_Id=7, Branch_Id=None)]])
        with patch.object(auth, "get_connection", return_value=conn):
            self.assertIsNone(auth.get_active_user(7))
        sql = conn.statements[0][0]
        self.assertIn("s.Staff_Id, b.Branch_Id", sql)
        self.assertIn("b.Branch_Id = s.Branch_Id AND b.Branch_Is_Active = 1", sql)

    def test_active_admin_requires_administrator_subtype(self):
        user = auth._to_session_user(sample_user())
        self.assertEqual(user["user_type"], "Administrator")
        self.assertEqual(user["full_name"], "Test User")
        self.assertNotIn("role", user)
        self.assertIsNone(auth._to_session_user(sample_user(Admin_Id=None)))

    def test_staff_requires_active_staff_row_and_branch(self):
        row = sample_user(User_Type="Store Staff", Admin_Id=None, Staff_Id=7,
                          Branch_Id=2, Branch_Name="Mount Pleasant")
        user = auth._to_session_user(row)
        self.assertEqual(user["user_type"], "Store Staff")
        self.assertEqual(user["branch_id"], 2)
        self.assertEqual(user["branch_name"], "Mount Pleasant")
        for missing in (dict(Staff_Id=None), dict(Branch_Id=None), dict(User_Is_Active=0)):
            with self.subTest(missing=missing):
                self.assertIsNone(auth._to_session_user({**row, **missing}))

    def test_unrecognized_or_legacy_role_does_not_bypass_login_rules(self):
        # Current auth only permits the DB values Administrator / Store Staff.
        for role in ("Employee", "Visitor", None):
            with self.subTest(role=role):
                self.assertIsNone(auth._to_session_user(sample_user(User_Type=role)))
        self.assertIsNone(auth._to_session_user(None))

    def test_require_user_type_uses_current_session_field(self):
        admin = auth._to_session_user(sample_user())
        auth.require_user_type(admin, {"Administrator"})
        for user in (admin, None):
            with self.subTest(user=user), self.assertRaises(PermissionError):
                auth.require_user_type(user, {"Store Staff"})

    def test_authenticate_by_email_or_username_does_not_return_hash(self):
        for login in ("testuser", "test@example.org"):
            with self.subTest(login=login):
                row = sample_user(User_Password_Hash="test-hash")
                conn = ScriptedConnection([[row]])
                with patch.object(auth, "get_connection", return_value=conn), \
                     patch.object(auth.bcrypt, "checkpw", return_value=True) as verify:
                    user = auth.authenticate(f"  {login}  ", "correct-password")
                self.assertEqual(user["user_id"], 7)
                self.assertNotIn("User_Password_Hash", user)
                self.assertEqual(conn.statements[0][1], (login, login))
                self.assertIn("User_Email = %s", conn.statements[0][0])
                self.assertIn("User_Username = %s", conn.statements[0][0])
                verify.assert_called_once_with(b"correct-password", b"test-hash")
                self.assertTrue(conn.closed)
                self.assertTrue(all(c.closed for c in conn.cursors))

    def test_wrong_password_rejected_even_if_account_active(self):
        conn = ScriptedConnection([[sample_user(User_Password_Hash="test-hash")]])
        with patch.object(auth, "get_connection", return_value=conn), \
             patch.object(auth.bcrypt, "checkpw", return_value=False):
            self.assertIsNone(auth.authenticate("testuser", "wrong"))

    def test_ambiguous_identifier_and_no_match_are_rejected(self):
        for rows in ([], [sample_user(), sample_user(User_Id=8)]):
            conn = ScriptedConnection([rows])
            with patch.object(auth, "get_connection", return_value=conn), \
                 patch.object(auth.bcrypt, "checkpw") as check:
                self.assertIsNone(auth.authenticate("ambiguous", "pass"))
                check.assert_not_called()
            self.assertTrue(conn.closed)

    def test_empty_credentials_do_not_touch_database(self):
        with patch.object(auth, "get_connection") as conn:
            for login, password in (("", "x"), ("testuser", ""), (None, "x")):
                self.assertIsNone(auth.authenticate(login, password))
            conn.assert_not_called()

    def test_get_active_user_rechecks_database_and_closes_resources(self):
        conn = ScriptedConnection([[sample_user()]])
        with patch.object(auth, "get_connection", return_value=conn):
            user = auth.get_active_user(7)
        self.assertEqual(user["user_type"], "Administrator")
        self.assertEqual(conn.statements[0][1], (7,))
        self.assertIn("User_Id = %s", conn.statements[0][0])
        self.assertTrue(conn.closed)
        self.assertTrue(conn.cursors[0].closed)

    def test_inactive_account_recheck_returns_none(self):
        conn = ScriptedConnection([[sample_user(User_Is_Active=0)]])
        with patch.object(auth, "get_connection", return_value=conn):
            self.assertIsNone(auth.get_active_user(7))

    def test_hash_password_rejects_blank_and_bcrypt_byte_limit(self):
        for password in ("", "界" * 25):  # 25 CJK chars = 75 UTF-8 bytes
            with self.subTest(password=password), self.assertRaises(ValueError):
                auth.hash_password(password)

    def test_hash_password_uses_bcrypt_rounds_12(self):
        with patch.object(auth.bcrypt, "gensalt", return_value=b"salt") as salt, \
             patch.object(auth.bcrypt, "hashpw", return_value=b"stored-hash") as hashing:
            result = auth.hash_password("short-password")
        self.assertEqual(result, "stored-hash")
        salt.assert_called_once_with(rounds=12)
        hashing.assert_called_once_with(b"short-password", b"salt")


class StaffSessionTests(unittest.TestCase):
    def setUp(self):
        import app
        self.app = app
        self.ui = Mock(query_params={}, session_state={
            "user_id": 7, "gw_password_version": 3, "gw_page": "item_search",
        })
        self.enterContext(patch.object(app, "st", self.ui))
        self.enterContext(patch.object(app, "load_styles"))
        self.login = self.enterContext(patch.object(app, "show_login"))
        self.route = self.enterContext(patch.object(app, "render_authenticated_app"))
        self.reload = self.enterContext(patch.object(app, "get_active_user"))

    def test_active_session_rechecks_account_and_keeps_selected_page(self):
        self.reload.return_value = {"user_id": 7, "user_type": "Store Staff",
                                    "password_version": 3}
        self.app.main()
        self.reload.assert_called_once_with(7)
        self.route.assert_called_once_with(self.reload.return_value)
        self.assertEqual(self.ui.session_state["gw_page"], "item_search")

    def test_deactivation_and_branch_change_revoke_session_before_routing(self):
        # A reassignment by users_service increments Session_Version.
        for user in (None, {"user_id": 7, "user_type": "Store Staff", "password_version": 4}):
            with self.subTest(user=user):
                self.ui.session_state.update(user_id=7, gw_password_version=3, gw_page="item_search")
                self.reload.return_value = user
                self.app.main()
                self.assertEqual(self.ui.session_state, {})
                self.login.assert_called_once()
                self.login.reset_mock()
                self.route.assert_not_called()

    def test_session_check_db_failure_shows_generic_error_and_stops(self):
        self.reload.side_effect = RuntimeError("private fixture detail")
        with patch.object(self.app.logger, "exception"):
            self.app.main()
        self.route.assert_not_called()
        self.ui.error.assert_called_once_with("Cannot verify your session. Please try again later.")


if __name__ == "__main__":
    unittest.main()
