"""Provision a Goodwill account in the configured MySQL schema.

Run from the project root, for example:
    python -m scripts.create_user_account --user-id 1001 --username staff1 \
        --first-name Alex --last-name Lee --email alex@example.org \
        --role 'Store Staff' --branch-id 1

The current auth.py expects User_Type='Store Staff' for staff accounts. The
original G3 SQL draft allowed only 'Employee'; if that older CHECK constraint
is still deployed, align the schema/auth configuration before creating staff.
"""

import argparse
import getpass

import mysql.connector

from auth import hash_password
from database import get_connection


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="Create an active Goodwill account")
    parser.add_argument("--user-id", type=int, required=True)
    parser.add_argument("--username", required=True)
    parser.add_argument("--first-name", required=True)
    parser.add_argument("--last-name", required=True)
    parser.add_argument("--email", required=True)
    parser.add_argument(
        "--role", choices=("Administrator", "Store Staff", "Employee"), required=True,
        help="Use 'Store Staff' for employees; 'Employee' is a legacy CLI alias.",
    )
    parser.add_argument("--branch-id", type=int)
    args = parser.parse_args(argv)

    if args.user_id <= 0:
        parser.error("--user-id must be a positive integer")
    args.role = "Store Staff" if args.role == "Employee" else args.role
    if args.role == "Store Staff" and (args.branch_id is None or args.branch_id <= 0):
        parser.error("Store Staff must have a valid --branch-id")
    if args.role == "Administrator" and args.branch_id is not None:
        parser.error("Administrators must not supply --branch-id")

    for arg_name, attr_name, max_length in (
        ("username", "username", 50),
        ("first-name", "first_name", 50),
        ("last-name", "last_name", 50),
        ("email", "email", 100),
    ):
        value = getattr(args, attr_name).strip()
        if not value or len(value) > max_length:
            parser.error(f"--{arg_name} must contain 1–{max_length} characters")
        setattr(args, attr_name, value)
    if "@" not in args.email or any(ch.isspace() for ch in args.email):
        parser.error("--email must be a valid email address")
    return args


def main(argv=None) -> None:
    args = parse_args(argv)
    password = getpass.getpass("New password: ")
    confirm = getpass.getpass("Confirm password: ")
    if password != confirm:
        raise SystemExit("Passwords do not match. Account was not created.")
    try:
        # Reuse the same bcrypt implementation and 72-byte bound as auth.py.
        password_hash = hash_password(password)
    except ValueError as exc:
        raise SystemExit(f"Invalid password: {exc}") from None

    connection = None
    cursor = None
    try:
        connection = get_connection()
        cursor = connection.cursor()
        cursor.execute("SELECT DATABASE()")
        selected = cursor.fetchone()
        database_name = selected[0] if selected else None
        if not database_name:
            raise RuntimeError("No database is selected. Check your connection settings.")
        print(f"Selected database: {database_name}")
        answer = input(f"Type '{database_name}' to confirm account creation: ").strip()
        if answer != database_name:
            print("Cancelled; no account was created.")
            return

        if args.role == "Store Staff":
            cursor.execute("SELECT 1 FROM BRANCH WHERE Branch_Id = %s", (args.branch_id,))
            if cursor.fetchone() is None:
                raise ValueError("Branch ID not found in BRANCH.")

        cursor.execute(
            """INSERT INTO USER_ACCOUNT
               (User_Id, User_First_Name, User_Last_Name, User_Username,
                User_Password_Hash, User_Email, User_Is_Active, User_Type)
               VALUES (%s, %s, %s, %s, %s, %s, 1, %s)""",
            (args.user_id, args.first_name, args.last_name,
             args.username, password_hash, args.email, args.role),
        )
        if args.role == "Administrator":
            cursor.execute("INSERT INTO ADMINISTRATOR (Admin_Id) VALUES (%s)",
                           (args.user_id,))
        else:
            cursor.execute(
                "INSERT INTO STORE_STAFF (Staff_Id, Branch_Id) VALUES (%s, %s)",
                (args.user_id, args.branch_id),
            )
        connection.commit()
        print(f"Created {args.role} account: {args.username} (User_Id={args.user_id})")
    except (mysql.connector.Error, RuntimeError, ValueError) as exc:
        if connection is not None:
            connection.rollback()
        raise SystemExit(
            f"Account creation failed; changes rolled back: {exc}. "
            "If your schema still restricts User_Type to 'Employee', "
            "align that constraint with the current 'Store Staff' login role first."
        ) from None
    finally:
        if cursor is not None:
            cursor.close()
        if connection is not None:
            connection.close()


if __name__ == "__main__":
    main()
