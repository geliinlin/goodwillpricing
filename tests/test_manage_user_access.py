"""Offline verification of the maintenance CLI; never reaches a real MySQL server."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "manage_user_access.py"
spec = importlib.util.spec_from_file_location("manage_user_access_under_test", SCRIPT)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def row(username="kwhitfield", active=True, admin=True, can_manage=False):
    return {"User_Id": 7, "User_Username": username,
            "User_Type": "Administrator" if admin else "Store Staff",
            "User_Is_Active": int(active), "Admin_Id": 7 if admin else None,
            "can_manage": int(can_manage)}


class FakeConnection:
    """Intentionally tiny fake with statement-dependent replies."""
    def __init__(self, *, username="kwhitfield", authorized=False, schema_ok=True,
                 already_granted=0, target_active=True, target_admin=True):
        self.username = username
        self.authorized = authorized
        self.schema_ok = schema_ok
        self.already_granted = already_granted
        self.target_active = target_active
        self.target_admin = target_admin
        self.queries = []
        self.commit_count = 0
        self.rollback_count = 0
        self.start_count = 0
        self.closed = False
        self.released = False
        self.inserted = False

    def cursor(self, **kwargs):
        return FakeCursor(self)

    def start_transaction(self):
        self.start_count += 1

    def commit(self):
        self.commit_count += 1

    def rollback(self):
        self.rollback_count += 1

    def close(self):
        self.closed = True


class FakeCursor:
    def __init__(self, connection):
        self.connection = connection
        self.rows = []
        self.closed = False

    def execute(self, sql, params=None):
        q = " ".join(sql.split())
        c = self.connection
        c.queries.append((q, params))
        if "DATABASE() AS database_name" in q:
            self.rows = [{"database_name": "Fall2026BIS698TueG3s", "database_user": "tester@%"}]
        elif "information_schema.KEY_COLUMN_USAGE" in q:
            self.rows = [{"valid_fk": 1 if c.schema_ok else 0}]
        elif "FROM USER_ACCOUNT AS u" in q:
            self.rows = [row(c.username, c.target_active, c.target_admin, c.authorized)]
        elif "SELECT GET_LOCK(" in q:
            self.rows = [{"acquired": 1}]
        elif "SELECT RELEASE_LOCK(" in q:
            c.released = True
            self.rows = [{"released": 1}]
        elif "COUNT(*) AS n" in q:
            self.rows = [{"n": c.already_granted}]
        elif "INSERT INTO USER_MODULE_ACCESS" in q:
            c.inserted = True
            c.authorized = True
            self.rows = []
        else:
            raise AssertionError(f"Unexpected SQL: {q}")

    def fetchone(self):
        return self.rows[0] if self.rows else None

    def fetchall(self):
        return list(self.rows)

    def close(self):
        self.closed = True


class ManageAccessTests(unittest.TestCase):
    def test_cli_requires_actor_for_normal_grant(self):
        with self.assertRaises(SystemExit):
            module._args(["--username", "kwhitfield", "--grant"])

    def test_cli_rejects_bootstrap_revoke(self):
        with self.assertRaises(SystemExit):
            module._args(["--username", "kwhitfield", "--revoke", "--bootstrap"])

    def test_cli_supports_check_and_dynamic_database(self):
        args = module._args(["--username", "kwhitfield", "--check", "--expected-database", "current_schema"])
        self.assertEqual(args.expected_database, "current_schema")

    def test_refuse_staff_even_with_permission_flag(self):
        conn = FakeConnection(target_admin=False, authorized=True)
        with self.assertRaisesRegex(module.AccessScriptError, "Administrator"):
            module._find_admin(conn, "kwhitfield")

    def test_refuse_outdated_permissions_table(self):
        conn = FakeConnection(schema_ok=False)
        with self.assertRaisesRegex(module.AccessScriptError, "Users V2"):
            module._verify_admin_only_schema(conn)

    def test_bootstrap_grants_once_and_releases_named_lock(self):
        conn = FakeConnection()
        module._bootstrap(conn, "kwhitfield")
        self.assertTrue(conn.inserted)
        self.assertEqual(conn.commit_count, 1)
        self.assertTrue(conn.released)
        self.assertTrue(any('FOR UPDATE' in sql for sql,_ in conn.queries))

    def test_bootstrap_fails_if_an_admin_already_granted(self):
        conn = FakeConnection(already_granted=1)
        with self.assertRaisesRegex(module.AccessScriptError, "Bootstrap blocked"):
            module._bootstrap(conn, "kwhitfield")
        self.assertFalse(conn.inserted)
        self.assertEqual(conn.commit_count, 0)
        self.assertTrue(conn.released)

    def test_bootstrap_cannot_grant_inactive_admin(self):
        conn = FakeConnection(target_active=False)
        with self.assertRaisesRegex(module.AccessScriptError, "inactive"):
            module._bootstrap(conn, "kwhitfield")
        self.assertEqual(conn.commit_count, 0)

    def test_normal_grant_uses_authenticated_service(self):
        before = FakeConnection()
        after = FakeConnection(authorized=True)
        db = types.ModuleType("database")
        connections = iter([before, after])
        db.get_connection = lambda: next(connections)
        auth = types.ModuleType("auth")
        auth.authenticate = lambda name, password: {
            "user_id": 9, "user_type": "Administrator", "can_manage_users_config": True
        } if (name, password) == ("existing_admin", "correct") else None
        service = types.ModuleType("users_service")
        calls = []
        service.set_management_access = lambda actor, uid, allowed: calls.append((actor, uid, allowed))
        with patch.dict(sys.modules, {"database": db, "auth": auth, "users_service": service}), \
             patch.object(module, "_confirm"), patch.object(module, "getpass", return_value="correct"):
            module.run(["--username", "kwhitfield", "--grant", "--actor", "existing_admin"])
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][1:], (7, True))
        self.assertTrue(before.closed and after.closed)

    def test_bad_actor_password_prevents_write(self):
        before = FakeConnection()
        db = types.ModuleType("database")
        db.get_connection = lambda: before
        auth = types.ModuleType("auth")
        auth.authenticate = lambda *args: None
        service = types.ModuleType("users_service")
        service.set_management_access = lambda *args: self.fail("must not mutate")
        with patch.dict(sys.modules, {"database": db, "auth": auth, "users_service": service}), \
             patch.object(module, "_confirm"), patch.object(module, "getpass", return_value="wrong"):
            with self.assertRaisesRegex(module.AccessScriptError, "authentication"):
                module.run(["--username", "kwhitfield", "--grant", "--actor", "existing_admin"])


if __name__ == "__main__":
    unittest.main()
