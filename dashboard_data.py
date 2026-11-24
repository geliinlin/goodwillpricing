"""Read-only dashboard data access for the Goodwill Pricing MySQL schema.

Designed for the schema in 'G3 SQL script'.  All statements use parameters,
all cursors are buffered (avoids mysql.connector 'Unread result found'), and
all connection/cursor resources close in finally blocks.

The app's auth.get_active_user() is still the primary login check.  We validate
role membership here a second time to enforce the dashboard's data boundary.
"""
from __future__ import annotations

from contextlib import contextmanager
from decimal import Decimal
from typing import Any, Callable, Iterator, Mapping


class DashboardPermissionError(PermissionError):
    """The current user cannot access the requested dashboard data."""


def _connect():
    from database import get_connection
    return get_connection()


@contextmanager
def _connection(factory: Callable[[], Any] | None = None) -> Iterator[Any]:
    conn = (factory or _connect)()
    try:
        yield conn
    finally:
        conn.close()


def _fetchall(conn: Any, sql: str, params: tuple = ()) -> list[dict]:
    # Buffered cursors are important with MySQL Connector/Python C extensions.
    cursor = conn.cursor(dictionary=True, buffered=True)
    try:
        cursor.execute(sql, params)
        return cursor.fetchall()
    finally:
        cursor.close()


def _fetchone(conn: Any, sql: str, params: tuple = ()) -> dict | None:
    rows = _fetchall(conn, sql, params)
    return rows[0] if rows else None


def _verify_user(conn: Any, user: Mapping[str, Any]) -> tuple[str, int]:
    """Return (role, id) only if DB confirms an active role/subtype record."""
    role = user.get("user_type")
    if role not in ("Administrator", "Store Staff"):
        raise DashboardPermissionError("Unrecognized user role")
    try:
        user_id = int(user["user_id"])
    except (KeyError, TypeError, ValueError) as exc:
        raise DashboardPermissionError("Missing authenticated user ID") from exc

    row = _fetchone(
        conn,
        """SELECT u.User_Type, u.User_Is_Active, a.Admin_Id, s.Staff_Id
           FROM USER_ACCOUNT AS u
           LEFT JOIN ADMINISTRATOR AS a ON a.Admin_Id = u.User_Id
           LEFT JOIN STORE_STAFF AS s ON s.Staff_Id = u.User_Id
           LEFT JOIN USER_PASSWORD_STATE AS ps ON ps.User_Id = u.User_Id
           WHERE u.User_Id = %s AND COALESCE(ps.Must_Change_Password, 0) = 0 AND (
               u.User_Type = 'Administrator' OR EXISTS (
                   SELECT 1 FROM BRANCH AS b
                   WHERE b.Branch_Id = s.Branch_Id AND b.Branch_Is_Active = 1
               )
           )""",
        (user_id,),
    )
    if not row or not int(row["User_Is_Active"]):
        raise DashboardPermissionError("User account is inactive or missing")
    if role == "Administrator":
        if row["User_Type"] != "Administrator" or row["Admin_Id"] is None:
            raise DashboardPermissionError("Administrator role mismatch")
    elif row["User_Type"] not in ("Employee", "Store Staff") or row["Staff_Id"] is None:
        # Existing item_search.get_authenticated_store_staff() accepts both
        # values. Keep the dashboard role check consistent with login/item
        # pricing without relaxing active-account or subtype checks.
        raise DashboardPermissionError("Store staff role mismatch")
    return role, user_id


def get_admin_snapshot(user: Mapping[str, Any], factory=None) -> dict:
    """System-wide counts and latest decisions, for administrators only."""
    with _connection(factory) as conn:
        role, _ = _verify_user(conn, user)
        if role != "Administrator":
            raise DashboardPermissionError("Administrator access required")
        metrics = _fetchone(
            conn,
            """SELECT
                  (SELECT COUNT(*) FROM PRICING_GUIDELINE
                   WHERE Guideline_Is_Active = 1) AS active_guidelines,
                  (SELECT COUNT(*) FROM USER_ACCOUNT
                   WHERE User_Is_Active = 1) AS active_users,
                  (SELECT COUNT(*) FROM PRICING_DECISION) AS decisions,
                  (SELECT COUNT(*)
                   FROM PRICING_DECISION AS d
                   JOIN PRICING_GUIDELINE AS g ON g.Guideline_Id = d.Guideline_Id
                   WHERE d.Decision_Final_Price <> g.Guideline_Suggested_Price
                  ) AS overrides""",
        ) or {}
        return {
            "metrics": metrics,
            "recent": _recent_rows(conn, staff_id=None, limit=7),
        }


def get_staff_snapshot(user: Mapping[str, Any], factory=None) -> dict:
    """Return metrics for this staff member only (not other employees)."""
    with _connection(factory) as conn:
        role, user_id = _verify_user(conn, user)
        if role != "Store Staff":
            raise DashboardPermissionError("Store staff access required")
        metrics = _fetchone(
            conn,
            """SELECT COUNT(*) AS my_decisions,
                      SUM(CASE WHEN DATE(d.Decision_Datetime) = CURDATE()
                               THEN 1 ELSE 0 END) AS today_decisions,
                      SUM(CASE WHEN d.Decision_Final_Price <>
                                    g.Guideline_Suggested_Price
                               THEN 1 ELSE 0 END) AS my_overrides
               FROM PRICING_DECISION AS d
               JOIN PRICING_GUIDELINE AS g ON g.Guideline_Id = d.Guideline_Id
               WHERE d.Staff_Id = %s""",
            (user_id,),
        ) or {}
        return {
            "metrics": {k: int(v or 0) for k, v in metrics.items()},
            "recent": _recent_rows(conn, staff_id=user_id, limit=5),
        }


def _recent_rows(conn: Any, *, staff_id: int | None, limit: int) -> list[dict]:
    where = "" if staff_id is None else "WHERE d.Staff_Id = %s"
    params = () if staff_id is None else (staff_id,)
    return _fetchall(
        conn,
        f"""SELECT d.Decision_Id AS decision_id,
                   d.Decision_Datetime AS decision_time,
                   d.Decision_Tag_Number AS tag_number,
                   d.Decision_Final_Price AS final_price,
                   g.Guideline_Suggested_Price AS suggested_price,
                   i.Item_Description AS description,
                   c.Category_Name AS category,
                   u.User_First_Name AS first_name,
                   u.User_Last_Name AS last_name,
                   COALESCE(b.Branch_Name, 'Unknown') AS branch_name
            FROM PRICING_DECISION AS d
            JOIN ITEM AS i ON i.Item_Id = d.Item_Id
            JOIN CATEGORY AS c ON c.Category_Id = i.Category_Id
            JOIN PRICING_GUIDELINE AS g ON g.Guideline_Id = d.Guideline_Id
            JOIN STORE_STAFF AS ss ON ss.Staff_Id = d.Staff_Id
            LEFT JOIN BRANCH AS b ON b.Branch_Id = d.Decision_Branch_Id
            JOIN USER_ACCOUNT AS u ON u.User_Id = d.Staff_Id
            {where}
            ORDER BY d.Decision_Datetime DESC, d.Decision_Id DESC
            LIMIT {int(limit)}""",
        params,
    )


def get_history(user: Mapping[str, Any], *, limit: int = 100, factory=None) -> list[dict]:
    """Admins: all decisions; staff: their own decisions, only."""
    limit = max(1, min(int(limit), 500))
    with _connection(factory) as conn:
        role, user_id = _verify_user(conn, user)
        return _recent_rows(conn, staff_id=None if role == "Administrator" else user_id,
                            limit=limit)


def get_reprint_receipt(user: Mapping[str, Any], tag_number: str,
                        factory=None) -> dict | None:
    """Return an existing saved tag; staff can only retrieve their own tags.

    This function NEVER inserts a new ITEM or PRICING_DECISION row.
    """
    tag_number = tag_number.strip()
    if not tag_number or len(tag_number) > 20:
        return None
    with _connection(factory) as conn:
        role, user_id = _verify_user(conn, user)
        if role != "Store Staff":
            raise DashboardPermissionError("Only store staff may reprint their tags")
        row = _fetchone(
            conn,
            """SELECT d.Decision_Id AS decision_id,
                      d.Decision_Tag_Number AS tag_number,
                      d.Decision_Final_Price AS final_price,
                      d.Decision_Reason AS reason,
                      i.Item_Description AS description,
                      c.Category_Name AS category,
                      b.Brand_Name AS brand,
                      ic.Condition_Name AS `condition`,
                      it.Item_Type_Name AS item_type,
                      d.Decision_Branch_Id AS branch_id,
                      COALESCE(br.Branch_Name, 'Unknown') AS branch_name
               FROM PRICING_DECISION AS d
               JOIN ITEM AS i ON i.Item_Id = d.Item_Id
               JOIN CATEGORY AS c ON c.Category_Id = i.Category_Id
               JOIN BRAND AS b ON b.Brand_Id = i.Brand_Id
               JOIN ITEM_CONDITION AS ic ON ic.Condition_Id = i.Condition_Id
               JOIN ITEM_TYPE AS it ON it.Item_Type_Id = i.Item_Type_Id
               JOIN STORE_STAFF AS ss ON ss.Staff_Id = d.Staff_Id
               LEFT JOIN BRANCH AS br ON br.Branch_Id = d.Decision_Branch_Id
               WHERE d.Decision_Tag_Number = %s AND d.Staff_Id = %s
               LIMIT 1""",
            (tag_number, user_id),
        )
        return row


def as_money(value: Any) -> str:
    return f"${Decimal(str(value or 0)):,.2f}"

# Added to dashboard_data.py by safe installer; parameterized SQL, role-scoped.


def _history_positive_id(value, field: str) -> int | None:
    if value is None:
        return None
    if type(value) is not int or value < 1:
        raise ValueError(f'Invalid {field} filter')
    return value


UNKNOWN_BRANCH = "unknown"


def get_history_filter_options(user: Mapping[str, Any], factory=None) -> dict:
    """Actual history values only, with server-side role/employee verification.

    Historical branch comes from the decision snapshot. Inactive staff/categories are included
    if they appear in pricing history.
    """
    with _connection(factory) as conn:
        role, user_id = _verify_user(conn, user)
        where = '' if role == 'Administrator' else 'WHERE d.Staff_Id = %s'
        params = () if role == 'Administrator' else (user_id,)
        joins = '''FROM PRICING_DECISION AS d
                   JOIN ITEM AS i ON i.Item_Id = d.Item_Id
                   JOIN CATEGORY AS c ON c.Category_Id = i.Category_Id
                   JOIN STORE_STAFF AS ss ON ss.Staff_Id = d.Staff_Id
                   LEFT JOIN BRANCH AS b ON b.Branch_Id = d.Decision_Branch_Id
                   JOIN USER_ACCOUNT AS u ON u.User_Id = d.Staff_Id'''
        categories = _fetchall(conn,
            f'''SELECT DISTINCT c.Category_Id AS id,
                       c.Category_Name AS name
                {joins} {where}
                ORDER BY c.Category_Name, c.Category_Id''', params)
        result = {'categories': categories, 'branches': [], 'staff': []}
        if role == 'Administrator':
            result['branches'] = _fetchall(conn,
                f'''SELECT DISTINCT b.Branch_Id AS id,
                           b.Branch_Name AS name
                    {joins} WHERE d.Decision_Branch_Id IS NOT NULL ORDER BY b.Branch_Name, b.Branch_Id''')
            result['staff'] = _fetchall(conn,
                f'''SELECT DISTINCT d.Staff_Id AS id,
                           u.User_First_Name AS first_name,
                           u.User_Last_Name AS last_name
                    {joins} ORDER BY u.User_Last_Name, u.User_First_Name, d.Staff_Id''')
        return result


def get_filtered_history(
    user: Mapping[str, Any], *, keyword: str = '',
    days: int | None = None, start_date=None, end_date=None,
    category_id: int | None = None, branch_id: int | str | None = None,
    staff_id: int | None = None, price_change: str = 'All',
    limit: int = 200, factory=None,
) -> list[dict]:
    """Filter ALL decisions in SQL, then limit; no client-side 100-row scan.

    Store Staff is always scoped to its verified Staff_Id, and cannot filter by
    another staff member or by branch. Everything here is read-only SELECT.
    """
    from datetime import date, datetime, timedelta
    if not isinstance(keyword, str) or len(keyword) > 100:
        raise ValueError('Search term must be at most 100 characters')
    if type(limit) is not int or limit < 1:
        raise ValueError('Invalid history limit')
    limit = min(limit, 500)
    category_id = _history_positive_id(category_id, 'category')
    if branch_id != UNKNOWN_BRANCH:
        branch_id = _history_positive_id(branch_id, 'branch')
    staff_id = _history_positive_id(staff_id, 'staff')
    if days is not None and (type(days) is not int or days not in (7, 30, 90)):
        raise ValueError('Invalid date period')
    if days is not None and (start_date is not None or end_date is not None):
        raise ValueError('Choose either a preset period or custom dates')
    if (start_date is None) != (end_date is None):
        raise ValueError('Both custom dates are required')
    if start_date is not None:
        if (type(start_date) is not date or type(end_date) is not date
                or start_date > end_date
                or (end_date - start_date).days > 3660):
            raise ValueError('Invalid custom date range')
    if price_change not in ('All', 'Override', 'No Override'):
        raise ValueError('Invalid price change filter')
    term = keyword.strip()

    with _connection(factory) as conn:
        role, user_id = _verify_user(conn, user)
        if role == 'Store Staff' and (branch_id is not None or staff_id is not None):
            raise DashboardPermissionError('Store Staff can view only their own history')
        conditions, params = [], []
        if role == 'Store Staff':
            conditions.append('d.Staff_Id = %s')
            params.append(user_id)
        elif staff_id is not None:
            conditions.append('d.Staff_Id = %s')
            params.append(staff_id)
        if branch_id == UNKNOWN_BRANCH:
            conditions.append('d.Decision_Branch_Id IS NULL')
        elif branch_id is not None:
            conditions.append('d.Decision_Branch_Id = %s')
            params.append(branch_id)
        if category_id is not None:
            conditions.append('i.Category_Id = %s')
            params.append(category_id)
        if term:
            conditions.append('''(d.Decision_Tag_Number LIKE %s
                              OR COALESCE(i.Item_Description, '') LIKE %s
                              OR c.Category_Name LIKE %s)''')
            wildcard = '%' + term + '%'
            params.extend((wildcard, wildcard, wildcard))
        if days is not None:
            # Server-side rolling calendar period, including today.
            # days has already been restricted to 7, 30 or 90, so this
            # numeric SQL literal cannot be influenced by arbitrary input.
            conditions.append(f'd.Decision_Datetime >= DATE_SUB(CURDATE(), INTERVAL {days - 1} DAY)')
        elif start_date is not None:
            conditions.append('d.Decision_Datetime >= %s')
            conditions.append('d.Decision_Datetime < %s')
            params.extend((start_date, end_date + timedelta(days=1)))
        if price_change == 'Override':
            conditions.append('d.Decision_Final_Price <> g.Guideline_Suggested_Price')
        elif price_change == 'No Override':
            conditions.append('d.Decision_Final_Price = g.Guideline_Suggested_Price')
        where = ('WHERE ' + ' AND '.join(conditions)) if conditions else ''
        sql = f'''SELECT d.Decision_Id AS decision_id,
                       d.Decision_Datetime AS decision_time,
                       d.Decision_Tag_Number AS tag_number,
                       d.Decision_Final_Price AS final_price,
                       d.Decision_Reason AS override_reason,
                       g.Guideline_Suggested_Price AS suggested_price,
                       i.Item_Description AS description,
                       c.Category_Name AS category,
                       u.User_First_Name AS first_name,
                       u.User_Last_Name AS last_name,
                       COALESCE(b.Branch_Name, 'Unknown') AS branch_name
                FROM PRICING_DECISION AS d
                JOIN ITEM AS i ON i.Item_Id = d.Item_Id
                JOIN CATEGORY AS c ON c.Category_Id = i.Category_Id
                JOIN PRICING_GUIDELINE AS g ON g.Guideline_Id = d.Guideline_Id
                JOIN STORE_STAFF AS ss ON ss.Staff_Id = d.Staff_Id
                LEFT JOIN BRANCH AS b ON b.Branch_Id = d.Decision_Branch_Id
                JOIN USER_ACCOUNT AS u ON u.User_Id = d.Staff_Id
                {where}
                ORDER BY d.Decision_Datetime DESC, d.Decision_Id DESC
                LIMIT {limit}'''
        return _fetchall(conn, sql, tuple(params))

