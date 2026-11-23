"""Offline regression tests for scripts/ tools; real MySQL is never contacted."""

import importlib.util
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"


class FakeDatabaseError(Exception):
    pass


def load_script(filename):
    mysql = types.ModuleType("mysql")
    mysql_connector = types.ModuleType("mysql.connector")
    mysql_connector.Error = FakeDatabaseError
    mysql.connector = mysql_connector
    auth = types.ModuleType("auth")

    def fake_hash(password):
        if not password or len(password.encode("utf-8")) > 72:
            raise ValueError("Invalid password length")
        return "bcrypt-hash"

    auth.hash_password = fake_hash
    database = types.ModuleType("database")
    database.get_connection = lambda: None
    guidelines = types.ModuleType("guidelines")
    guidelines.LOOKUPS = (("CATEGORY", "Category_Id", "Category_Name", "Category"),)
    guidelines.VERSION_FIELDS = ("Guideline_Id", "Guideline_Suggested_Price")

    modules = {
        "mysql": mysql, "mysql.connector": mysql_connector,
        "auth": auth, "database": database, "guidelines": guidelines,
    }
    spec = importlib.util.spec_from_file_location("gw_test_script_" + filename[:-3], SCRIPTS / filename)
    module = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, modules):
        spec.loader.exec_module(module)
    return module


class FakeCursor:
    def __init__(self, *, schema="demo_goodwill", has_branch=True, found_user=True):
        self.schema = schema
        self.has_branch = has_branch
        self.found_user = found_user
        self.queries = []
        self.rowcount = 0
        self.last_sql = ""
        self.closed = False

    def execute(self, sql, params=None):
        self.queries.append((sql, params))
        self.last_sql = sql
        if "UPDATE USER_ACCOUNT" in sql:
            self.rowcount = 1 if self.found_user else 0

    def fetchone(self):
        if "SELECT DATABASE(), CURRENT_USER()" in self.last_sql:
            return (self.schema, "demo-user")
        if "SELECT DATABASE()" in self.last_sql:
            return (self.schema,)
        if "SELECT 1 FROM BRANCH" in self.last_sql:
            return (1,) if self.has_branch else None
        if "COUNT(*)" in self.last_sql:
            return (1,)
        return None

    def fetchall(self):
        if "information_schema.TABLES" in self.last_sql:
            return [
                {"TABLE_NAME": t, "ENGINE": "InnoDB"}
                for t in ("USER_ACCOUNT", "ADMINISTRATOR", "PRICING_GUIDELINE", "CATEGORY")
            ]
        return []

    def close(self):
        self.closed = True


class FakeConnection:
    def __init__(self, **kwargs):
        self.cur = FakeCursor(**kwargs)
        self.commits = 0
        self.rollbacks = 0
        self.closed = False

    def cursor(self, **kwargs):
        return self.cur

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1

    def close(self):
        self.closed = True


STAFF_ARGS = [
    "--user-id", "100", "--username", "alex", "--first-name", "Alex",
    "--last-name", "Lee", "--email", "alex@example.org",
    "--role", "Store Staff", "--branch-id", "1",
]
ADMIN_ARGS = [
    "--user-id", "101", "--username", "admin", "--first-name", "Jane",
    "--last-name", "Doe", "--email", "jane@example.org", "--role", "Administrator",
]


class ScriptToolTests(unittest.TestCase):
    def test_create_user_accepts_current_staff_role(self):
        mod = load_script("create_user_account.py")
        args = mod.parse_args(STAFF_ARGS)
        self.assertEqual(args.role, "Store Staff")
        self.assertEqual(args.branch_id, 1)

    def test_employee_legacy_alias_maps_to_current_role(self):
        mod = load_script("create_user_account.py")
        args = mod.parse_args(STAFF_ARGS[:-4] + ["--role", "Employee", "--branch-id", "1"])
        self.assertEqual(args.role, "Store Staff")

    def test_staff_role_requires_branch(self):
        mod = load_script("create_user_account.py")
        with patch("sys.stderr"):
            with self.assertRaises(SystemExit):
                mod.parse_args(STAFF_ARGS[:-2])

    def test_short_nonempty_password_is_accepted(self):
        mod = load_script("create_user_account.py")
        conn = FakeConnection()
        mod.get_connection = lambda: conn
        with patch.object(mod, "getpass") as pw, patch("builtins.input", return_value="demo_goodwill"):
            pw.getpass.side_effect = ["abc", "abc"]
            mod.main(STAFF_ARGS)
        self.assertEqual(conn.commits, 1)
        self.assertEqual(conn.rollbacks, 0)
        insert = next(params for sql, params in conn.cur.queries if "INSERT INTO USER_ACCOUNT" in sql)
        self.assertEqual(insert[-1], "Store Staff")
        self.assertEqual(insert[-2], "alex@example.org")
        self.assertEqual(insert[-3], "bcrypt-hash")
        self.assertTrue(conn.closed and conn.cur.closed)

    def test_create_wrong_database_confirmation_no_insert(self):
        mod = load_script("create_user_account.py")
        conn = FakeConnection()
        mod.get_connection = lambda: conn
        with patch.object(mod, "getpass") as pw, patch("builtins.input", return_value="wrong_db"):
            pw.getpass.side_effect = ["abc", "abc"]
            mod.main(ADMIN_ARGS)
        self.assertFalse(any("INSERT" in sql for sql, _ in conn.cur.queries))
        self.assertEqual(conn.commits, 0)
        self.assertTrue(conn.closed)

    def test_invalid_staff_branch_fails_and_rolls_back(self):
        mod = load_script("create_user_account.py")
        conn = FakeConnection(has_branch=False)
        mod.get_connection = lambda: conn
        with patch.object(mod, "getpass") as pw, patch("builtins.input", return_value="demo_goodwill"):
            pw.getpass.side_effect = ["abc", "abc"]
            with self.assertRaises(SystemExit):
                mod.main(STAFF_ARGS)
        self.assertEqual(conn.rollbacks, 1)
        self.assertEqual(conn.commits, 0)
        self.assertFalse(any("INSERT" in sql for sql, _ in conn.cur.queries))

    def test_reset_blank_password_rejected_before_database(self):
        mod = load_script("reset_user_password.py")
        mod.get_connection = lambda: self.fail("Database should not be touched")
        with patch("builtins.input", return_value="alex"), patch.object(mod, "getpass", side_effect=["", ""]):
            mod.reset_password()

    def test_reset_username_and_database_confirmation(self):
        mod = load_script("reset_user_password.py")
        conn = FakeConnection()
        mod.get_connection = lambda: conn
        with patch("builtins.input", side_effect=["alex", "demo_goodwill"]), patch.object(
            mod, "getpass", side_effect=["short", "short"]
        ):
            mod.reset_password()
        self.assertEqual(conn.commits, 1)
        updated = next(params for sql, params in conn.cur.queries if "UPDATE USER_ACCOUNT" in sql)
        self.assertEqual(updated, ("bcrypt-hash", "alex"))
        self.assertTrue(conn.closed)

    def test_reset_wrong_schema_does_not_update(self):
        mod = load_script("reset_user_password.py")
        conn = FakeConnection()
        mod.get_connection = lambda: conn
        with patch("builtins.input", side_effect=["alex", "other"]), patch.object(
            mod, "getpass", side_effect=["short", "short"]
        ):
            mod.reset_password()
        self.assertFalse(any("UPDATE" in sql for sql, _ in conn.cur.queries))
        self.assertEqual(conn.commits, 0)

    def test_connection_check_queries_core_tables_only(self):
        mod = load_script("check_database_connection.py")
        conn = FakeConnection()
        mod.get_connection = lambda: conn
        mod.main()
        self.assertEqual(sum("SELECT COUNT(*)" in sql for sql, _ in conn.cur.queries), 11)
        self.assertFalse(any("INSERT" in sql or "UPDATE" in sql for sql, _ in conn.cur.queries))
        self.assertTrue(conn.closed)


if __name__ == "__main__":
    unittest.main(verbosity=2)
