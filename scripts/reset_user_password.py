"""Interactively reset a Goodwill password after confirming the selected MySQL schema.

Run from project root: python -m scripts.reset_user_password
Uses auth.hash_password() (bcrypt); no minimum character count, but empty
passwords and input exceeding 72 UTF-8 bytes are rejected.
"""

from getpass import getpass

import mysql.connector

from auth import hash_password
from database import get_connection


def reset_password() -> None:
    username = input("Enter username: ").strip()
    if not username:
        print("Username is required. No changes made.")
        return

    password = getpass("Enter new password: ")
    confirmation = getpass("Confirm new password: ")
    if password != confirmation:
        print("Passwords do not match. No changes made.")
        return
    try:
        password_hash = hash_password(password)
    except ValueError as exc:
        print(f"Invalid password: {exc}. No changes made.")
        return

    connection = None
    cursor = None
    try:
        connection = get_connection()
        cursor = connection.cursor()
        cursor.execute("SELECT DATABASE(), CURRENT_USER()")
        selected = cursor.fetchone()
        database_name, database_user = selected if selected else (None, None)
        if not database_name:
            raise RuntimeError("No MySQL schema is selected.")

        print(f"Connected database: {database_name}")
        print(f"Database user: {database_user}")
        answer = input(f"Type '{database_name}' to confirm password reset: ").strip()
        if answer != database_name:
            print("Password reset cancelled; no changes made.")
            return

        cursor.execute(
            """UPDATE USER_ACCOUNT
               SET User_Password_Hash = %s
               WHERE User_Username = %s""",
            (password_hash, username),
        )
        if cursor.rowcount != 1:
            connection.rollback()
            print("User not found; no changes made.")
            return
        connection.commit()
        print("Password updated successfully.")
    except (mysql.connector.Error, RuntimeError, ValueError) as exc:
        if connection is not None:
            connection.rollback()
        raise SystemExit(f"Password reset failed; changes rolled back: {exc}") from None
    finally:
        if cursor is not None:
            cursor.close()
        if connection is not None:
            connection.close()


if __name__ == "__main__":
    reset_password()
