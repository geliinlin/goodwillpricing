"""Grant/revoke Goodwill Users + Configuration access for Administrators only.

Run from the *project root* with ``python -m scripts.manage_user_access``.

First-time trusted administrator bootstrap (only if no admin has the grant):
    python -m scripts.manage_user_access --username kwhitfield --grant --bootstrap

Normal grant/revoke by an already authorized administrator:
    python -m scripts.manage_user_access --username anotheradmin --grant --actor kwhitfield
    python -m scripts.manage_user_access --username anotheradmin --revoke --actor kwhitfield

Read-only status check:
    python -m scripts.manage_user_access --username kwhitfield --check

This is a privileged LOCAL maintenance tool, not a public web endpoint.
Bootstrap requires access to the configured MySQL account and typed confirmations.
Never bundle database secrets with this file or expose this command in Streamlit.
"""
from __future__ import annotations

import argparse
from getpass import getpass


LOCK_NAME = "goodwill_user_management_v1"  # Same named lock as users_service.py


class AccessScriptError(RuntimeError):
    """An expected, safely printable error."""


def _args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--username", required=True, help="Existing Administrator username to update")
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--grant", action="store_true", help="Allow Users and Configuration")
    action.add_argument("--revoke", action="store_true", help="Remove Users and Configuration permission")
    action.add_argument("--check", action="store_true", help="Read the current permission only")
    parser.add_argument("--actor", help="Authorized Administrator login for normal grant/revoke")
    parser.add_argument("--bootstrap", action="store_true", help="Initial one-time grant when no admin has access")
    parser.add_argument("--expected-database", help="Optional exact database name safeguard")
    args = parser.parse_args(argv)
    args.username = args.username.strip()
    if not args.username:
        parser.error("--username cannot be blank")
    if args.bootstrap and not args.grant:
        parser.error("--bootstrap can only be used with --grant")
    if args.bootstrap and args.actor:
        parser.error("--bootstrap must not specify --actor")
    if not args.bootstrap and not args.check and not args.actor:
        parser.error("Normal grants/revocations require --actor")
    if args.check and args.actor:
        parser.error("--check does not require --actor")
    return args


def _database_info(connection):
    cur = connection.cursor(dictionary=True, buffered=True)
    try:
        cur.execute("SELECT DATABASE() AS database_name, CURRENT_USER() AS database_user")
        row = cur.fetchone() or {}
        name = row.get("database_name")
        if not name:
            raise AccessScriptError("No MySQL database/schema selected.")
        return name, row.get("database_user", "unknown")
    finally:
        cur.close()


def _verify_admin_only_schema(connection):
    """Refuse a leftover V1 schema whose permission FK still points to USER_ACCOUNT."""
    cur = connection.cursor(dictionary=True, buffered=True)
    try:
        cur.execute("""
            SELECT COUNT(*) AS valid_fk
            FROM information_schema.KEY_COLUMN_USAGE
            WHERE TABLE_SCHEMA = DATABASE()
              AND TABLE_NAME = 'USER_MODULE_ACCESS'
              AND COLUMN_NAME = 'User_Id'
              AND REFERENCED_TABLE_NAME = 'ADMINISTRATOR'
              AND REFERENCED_COLUMN_NAME = 'Admin_Id'
        """)
        if int((cur.fetchone() or {}).get("valid_fk") or 0) != 1:
            raise AccessScriptError(
                "USER_MODULE_ACCESS missing or not using the Users V2 Administrator-only FK. "
                "Apply migrations/001_user_management_access.sql first (do not overwrite a V1 schema blindly)."
            )
    finally:
        cur.close()


def _find_admin(connection, username, *, for_update=False):
    cur = connection.cursor(dictionary=True, buffered=True)
    try:
        sql = """
            SELECT u.User_Id, u.User_Username, u.User_Type, u.User_Is_Active,
                   a.Admin_Id, COALESCE(p.Can_Manage_Users_Config, 0) AS can_manage
            FROM USER_ACCOUNT AS u
            LEFT JOIN ADMINISTRATOR AS a ON a.Admin_Id = u.User_Id
            LEFT JOIN USER_MODULE_ACCESS AS p ON p.User_Id = a.Admin_Id
            WHERE u.User_Username = %s
            LIMIT 2
        """
        if for_update:
            sql += " FOR UPDATE"
        cur.execute(sql, (username,))
        rows = cur.fetchall()
        if len(rows) != 1:
            raise AccessScriptError("Username not found or not unique. No changes made.")
        row = rows[0]
        if row.get("User_Type") != "Administrator" or row.get("Admin_Id") is None:
            raise AccessScriptError("Only Administrator accounts can hold Users + Configuration access.")
        return row
    finally:
        cur.close()


def _bootstrap(connection, username):
    """Bootstrap a *single* first administrator grant; serialized with app writes."""
    cur = connection.cursor(dictionary=True, buffered=True)
    locked = False
    in_transaction = False
    try:
        cur.execute("SELECT GET_LOCK(%s, 10) AS acquired", (LOCK_NAME,))
        locked = (cur.fetchone() or {}).get("acquired") == 1
        if not locked:
            raise AccessScriptError("Could not acquire the user-management lock; retry later.")
        # Read-only preflight (or GET_LOCK) may have opened an implicit transaction
        # when autocommit=False. Advisory locks survive ROLLBACK.
        connection.rollback()
        connection.start_transaction()
        in_transaction = True
        target = _find_admin(connection, username, for_update=True)
        if not bool(target["User_Is_Active"]):
            raise AccessScriptError("Cannot grant management access to an inactive Administrator.")
        cur.execute("""
            SELECT COUNT(*) AS n
            FROM USER_MODULE_ACCESS
            WHERE Can_Manage_Users_Config = 1
        """)
        existing = int((cur.fetchone() or {}).get("n") or 0)
        if existing:
            raise AccessScriptError(
                "Bootstrap blocked: one or more Administrators already have access. "
                "Use an authorized Administrator with --actor instead."
            )
        cur.execute("""
            INSERT INTO USER_MODULE_ACCESS
                (User_Id, Can_Manage_Users_Config, Granted_By_User_Id)
            VALUES (%s, 1, NULL)
            ON DUPLICATE KEY UPDATE
                Can_Manage_Users_Config = 1,
                Granted_By_User_Id = NULL
        """, (target["User_Id"],))
        connection.commit()
        in_transaction = False
    except Exception:
        if in_transaction:
            connection.rollback()
        raise
    finally:
        if locked:
            try:
                cur.execute("SELECT RELEASE_LOCK(%s) AS released", (LOCK_NAME,))
                cur.fetchone()
            except Exception:
                pass  # Closing connection also releases named locks.
        cur.close()


def _confirm(database_name, target_username, action):
    print(f"\nDatabase: {database_name}")
    if input(f"Type database name '{database_name}' to confirm: ").strip() != database_name:
        raise AccessScriptError("Cancelled: database name did not match.")
    expected = f"{action.upper()} {target_username}"
    if input(f"Type '{expected}' to confirm the operation: ").strip() != expected:
        raise AccessScriptError("Cancelled: target confirmation did not match.")


def run(argv=None):
    args = _args(argv)
    # Lazy imports: no DB or Streamlit startup just to parse CLI arguments.
    from database import get_connection

    # Preflight is read-only, on its own connection. Never verify writes against
    # the previous REPEATABLE READ snapshot; start a fresh connection instead.
    connection = get_connection()
    try:
        database_name, database_user = _database_info(connection)
        if args.expected_database and database_name != args.expected_database:
            raise AccessScriptError(
                f"Connected to '{database_name}', not requested '{args.expected_database}'. No changes made."
            )
        _verify_admin_only_schema(connection)
        target = _find_admin(connection, args.username)
    finally:
        connection.close()

    print(f"Database: {database_name} | Database account: {database_user}")
    print(
        f"Target: {target['User_Username']} (User_Id={target['User_Id']}); "
        f"Active={bool(target['User_Is_Active'])}; "
        f"Users + Configuration={bool(target['can_manage'])}"
    )
    if args.check:
        return
    if args.grant and not bool(target["User_Is_Active"]):
        raise AccessScriptError("Inactive Administrators cannot receive access.")
    action = "grant" if args.grant else "revoke"
    _confirm(database_name, target["User_Username"], action)

    if args.bootstrap:
        connection = get_connection()
        try:
            # Guard against switching to a different Schema after preflight.
            name, _ = _database_info(connection)
            if name != database_name:
                raise AccessScriptError("Connected database changed. No changes made.")
            _verify_admin_only_schema(connection)
            _bootstrap(connection, args.username)
        finally:
            connection.close()
    else:
        # This branch requires a real login, not merely an --actor username.
        # users_service then rechecks the actor's DB permission in its own transaction.
        from auth import authenticate
        from users_service import set_management_access
        password = getpass(f"Password for Administrator '{args.actor}': ")
        actor = authenticate(args.actor, password)
        if not (actor and actor.get("user_type") == "Administrator"
                and actor.get("can_manage_users_config") is True):
            raise AccessScriptError("Administrator authentication or authorization failed.")
        if actor["user_id"] == target["User_Id"]:
            raise AccessScriptError("Administrators cannot change their own access with this mode.")
        set_management_access(actor, target["User_Id"], args.grant)

    connection = get_connection()
    try:
        name, _ = _database_info(connection)
        if name != database_name:
            raise AccessScriptError("Connected database changed during verification.")
        current = _find_admin(connection, args.username)
    finally:
        connection.close()
    if bool(current["can_manage"]) != args.grant:
        raise AccessScriptError("Update could not be verified; check the database before continuing.")
    print(f"SUCCESS: Users + Configuration access {'GRANTED' if args.grant else 'REVOKED'} "
          f"for {args.username} in {database_name}.")
    print("Log out and log back in to refresh the Goodwill navigation.")


def main():
    try:
        run()
    except AccessScriptError as exc:
        raise SystemExit(f"Access update failed: {exc}") from None
    except (ValueError, PermissionError) as exc:
        # Business-rule errors from Users V2 are designed for user display.
        raise SystemExit(f"Access update failed: {exc}") from None
    except Exception as exc:
        # Do not print connector exceptions that could include credentials/host details.
        raise SystemExit(f"Access update failed ({type(exc).__name__}). No verified change reported.") from None


if __name__ == "__main__":
    main()
