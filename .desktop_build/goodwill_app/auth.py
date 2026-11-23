
"""Authentication and authorization using the existing G3 MySQL schema."""

from typing import Any
from datetime import datetime, timezone

import bcrypt

from database import get_connection


_USER_FIELDS = """
    u.User_Id, u.User_First_Name, u.User_Last_Name,
    u.User_Username, u.User_Is_Active, u.User_Type,
    a.Admin_Id, s.Staff_Id, b.Branch_Id, b.Branch_Name,
    COALESCE(p.Can_Manage_Users_Config, 0) AS Can_Manage_Users_Config,
    COALESCE(ps.Must_Change_Password, 0) AS Must_Change_Password,
    ps.Temp_Password_Expires_At,
    COALESCE(ps.Session_Version, 0) AS Password_Version
"""

_USER_JOINS = """
    FROM USER_ACCOUNT AS u
    LEFT JOIN ADMINISTRATOR AS a ON a.Admin_Id = u.User_Id
    LEFT JOIN STORE_STAFF AS s ON s.Staff_Id = u.User_Id
    LEFT JOIN BRANCH AS b ON b.Branch_Id = s.Branch_Id AND b.Branch_Is_Active = 1
    LEFT JOIN USER_MODULE_ACCESS AS p ON p.User_Id = a.Admin_Id
    LEFT JOIN USER_PASSWORD_STATE AS ps ON ps.User_Id = u.User_Id
"""


def _to_session_user(row: dict[str, Any] | None) -> dict[str, Any] | None:
    """Accept active users with matching user-type records."""

    if not row or not bool(row["User_Is_Active"]):
        return None

    user_type = row["User_Type"]
    must_change = bool(row.get('Must_Change_Password', 0))
    expires = row.get('Temp_Password_Expires_At')
    if must_change and isinstance(expires, datetime):
        expiration = (expires.replace(tzinfo=timezone.utc) if expires.tzinfo is None
                      else expires.astimezone(timezone.utc))
        if expiration <= datetime.now(timezone.utc):
            return None

    if user_type == "Administrator":
        if row["Admin_Id"] is None:
            return None

    elif user_type == "Store Staff":
        if row["Staff_Id"] is None or row["Branch_Id"] is None:
            return None

    else:
        return None

    return {
        "user_id": int(row["User_Id"]),
        "username": row["User_Username"],
        "full_name": (
            f"{row['User_First_Name']} {row['User_Last_Name']}"
        ),
        "user_type": user_type,
        "must_change_password": must_change,
        "password_version": int(row.get("Password_Version") or 0),
        "can_manage_users_config": (user_type == "Administrator" and bool(row.get("Can_Manage_Users_Config", 0))),
        "branch_id": (
            row["Branch_Id"]
            if user_type == "Store Staff"
            else None
        ),
        "branch_name": (
            row["Branch_Name"]
            if user_type == "Store Staff"
            else None
        ),
    }


def _read_one(statement: str, parameters: tuple[Any, ...]):
    """Execute one parameterized read and close the connection."""

    connection = get_connection()
    cursor = None

    try:
        cursor = connection.cursor(dictionary=True)
        cursor.execute(statement, parameters)
        return cursor.fetchone()

    finally:
        if cursor is not None:
            cursor.close()
        connection.close()



def hash_password(password: str) -> str:
    """Generate a bcrypt password hash."""
    if not isinstance(password, str) or not password:
        raise ValueError("Password cannot be empty.")

    password_bytes = password.encode("utf-8")

    if len(password_bytes) > 72:
        raise ValueError("Password exceeds 72 bytes.")

    return bcrypt.hashpw(
        password_bytes,
        bcrypt.gensalt(rounds=12)
    ).decode("utf-8")


def authenticate(login_identifier: str, password: str) -> dict[str, Any] | None:
    """Authenticate using either email or username."""

    login_identifier = (
        login_identifier.strip()
        if isinstance(login_identifier, str)
        else ""
    )

    if not login_identifier or not isinstance(password, str) or not password:
        return None
    password_bytes = password.encode("utf-8")
    if len(password_bytes) > 72:
        return None

    sql = (
        "SELECT " + _USER_FIELDS + ", u.User_Password_Hash "
        + _USER_JOINS
        + " WHERE u.User_Email = %s OR u.User_Username = %s "
        + "LIMIT 2"
    )

    rows = []
    connection = get_connection()
    cursor = None

    try:
        cursor = connection.cursor(dictionary=True)
        cursor.execute(
            sql,
            (login_identifier, login_identifier)
        )
        rows = cursor.fetchall()
    finally:
        if cursor is not None:
            cursor.close()
        connection.close()

    # Reject ambiguous identifiers.
    if len(rows) != 1:
        return None

    row = rows[0]

    try:
        matches = bcrypt.checkpw(
            password_bytes,
            row["User_Password_Hash"].encode("utf-8")
        )
    except (ValueError, TypeError, AttributeError):
        return None

    return _to_session_user(row) if matches else None




def get_active_user(user_id: int) -> dict[str, Any] | None:
    """Recheck user status and type on every Streamlit rerun."""

    sql = (
        "SELECT " + _USER_FIELDS
        + _USER_JOINS
        + " WHERE u.User_Id = %s LIMIT 1"
    )

    return _to_session_user(_read_one(sql, (user_id,)))


def require_user_type(
    user: dict[str, Any] | None,
    allowed_types: set[str]
) -> None:
    """Ensure the user is authorized for the requested operation."""

    if user is None or user.get("user_type") not in allowed_types:
        raise PermissionError(
            "You are not authorized to access this function."
        )
