"""Administrator-only read-only pricing reports for the current Goodwill MySQL schema.

The app writes Decision_Datetime as naive America/Detroit wall time. Reports use
local calendar-day boundaries in that same timezone. All queries are parameterized,
all cursors buffered, and there are no INSERT/UPDATE/DELETE statements.
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta
from decimal import Decimal
from typing import Any, Callable, Mapping
from zoneinfo import ZoneInfo

from dashboard_data import DashboardPermissionError

PERIODS = frozenset({7, 30, 90, 365})
ADJUSTMENTS = frozenset({"all", "overrides", "unchanged", "increased", "decreased"})
MAX_STAFF_ROWS = 500
LOCAL_TZ = ZoneInfo("America/Detroit")


def _connection():
    from database import get_connection
    return get_connection()


def _rows(conn: Any, statement: str, parameters: tuple[Any, ...] = ()) -> list[dict]:
    cursor = conn.cursor(dictionary=True, buffered=True)
    try:
        cursor.execute(statement, parameters)
        return cursor.fetchall()
    finally:
        cursor.close()


def _one(conn: Any, statement: str, parameters: tuple[Any, ...] = ()) -> dict | None:
    result = _rows(conn, statement, parameters)
    return result[0] if result else None


def _assert_admin(conn: Any, user: Mapping[str, Any]) -> None:
    """Validate role against MySQL, not just session state or hidden nav links."""
    if not user or user.get("user_type") != "Administrator":
        raise DashboardPermissionError("Administrator access required")
    try:
        user_id = int(user["user_id"])
    except (KeyError, ValueError, TypeError) as exc:
        raise DashboardPermissionError("Missing administrator ID") from exc
    if user_id <= 0:
        raise DashboardPermissionError("Invalid administrator ID")
    approved = _one(conn, """
        SELECT u.User_Id
        FROM USER_ACCOUNT AS u
        JOIN ADMINISTRATOR AS a ON a.Admin_Id = u.User_Id
        WHERE u.User_Id = %s AND u.User_Is_Active = 1
              AND u.User_Type = 'Administrator'
        LIMIT 1
    """, (user_id,))
    if approved is None:
        raise DashboardPermissionError("Administrator account is not active")


def report_window(days: int, *, today: date | None = None) -> tuple[datetime, datetime]:
    """Inclusive local start and exclusive local end for N calendar days."""
    if type(days) is not int or days not in PERIODS:
        raise ValueError("Invalid reporting period")
    if today is None:
        today = datetime.now(LOCAL_TZ).date()
    if not isinstance(today, date) or isinstance(today, datetime):
        raise ValueError("today must be a date")
    return (datetime.combine(today - timedelta(days=days - 1), time.min),
            datetime.combine(today + timedelta(days=1), time.min))


def custom_report_window(start_date: date, end_date: date, *,
                         today: date | None = None) -> tuple[datetime, datetime]:
    """Inclusive start/end dates, converted to an exclusive SQL upper bound.

    Reporting follows the same local wall-clock dates used when decisions are saved.
    """
    if type(start_date) is not date or type(end_date) is not date:
        raise ValueError("Choose a valid start and end date")
    if today is None:
        today = datetime.now(LOCAL_TZ).date()
    if end_date < start_date:
        raise ValueError("Start date cannot be after end date")
    if end_date > today:
        raise ValueError("End date cannot be in the future")
    if (end_date - start_date).days >= 3660:
        raise ValueError("Choose a range of 10 years or less")
    return (datetime.combine(start_date, time.min),
            datetime.combine(end_date + timedelta(days=1), time.min))


def _optional_id(name: str, value: int | None) -> None:
    if value is not None and (type(value) is not int or value <= 0):
        raise ValueError(f"Invalid {name} selection")


def _conditions(start: datetime, end: datetime, branch_id: int | None,
                staff_id: int | None = None, category_id: int | None = None,
                item_type_id: int | None = None, adjustment: str = "all"):
    """One parameterized WHERE clause shared by every chart and KPI query."""
    where = "d.Decision_Datetime >= %s AND d.Decision_Datetime < %s"
    params: tuple[Any, ...] = (start, end)
    for column, value in (("ss.Branch_Id", branch_id), ("d.Staff_Id", staff_id),
                          ("i.Category_Id", category_id), ("i.Item_Type_Id", item_type_id)):
        if value is not None:
            # SQL identifiers are constants above, never user-supplied strings.
            where += f" AND {column} = %s"
            params += (value,)
    if adjustment == "overrides":
        where += " AND d.Decision_Final_Price <> g.Guideline_Suggested_Price"
    elif adjustment == "unchanged":
        where += " AND d.Decision_Final_Price = g.Guideline_Suggested_Price"
    elif adjustment == "increased":
        where += " AND d.Decision_Final_Price > g.Guideline_Suggested_Price"
    elif adjustment == "decreased":
        where += " AND d.Decision_Final_Price < g.Guideline_Suggested_Price"
    return where, params


def get_report_filter_options(
    user: Mapping[str, Any], *,
    connection_factory: Callable[[], Any] | None = None,
) -> dict[str, list[dict]]:
    """Load current branch, staff, category and item-type dropdowns (read-only).

    Include inactive staff so administrators can inspect their historical pricing.
    """
    conn = (connection_factory or _connection)()
    try:
        _assert_admin(conn, user)
        return {
            "branches": _rows(conn, """
                SELECT Branch_Id AS branch_id, Branch_Name AS branch_name
                FROM BRANCH ORDER BY Branch_Name, Branch_Id
            """),
            "staff": _rows(conn, """
                SELECT ss.Staff_Id AS staff_id,
                       CONCAT(u.User_First_Name, ' ', u.User_Last_Name) AS staff_name,
                       ss.Branch_Id AS branch_id, u.User_Is_Active AS is_active
                FROM STORE_STAFF AS ss
                JOIN USER_ACCOUNT AS u ON u.User_Id = ss.Staff_Id
                ORDER BY u.User_Last_Name, u.User_First_Name, ss.Staff_Id
            """),
            "categories": _rows(conn, """
                SELECT Category_Id AS category_id, Category_Name AS category_name
                FROM CATEGORY ORDER BY Category_Name, Category_Id
            """),
            "item_types": _rows(conn, """
                SELECT Item_Type_Id AS item_type_id, Item_Type_Name AS item_type_name
                FROM ITEM_TYPE ORDER BY Item_Type_Name, Item_Type_Id
            """),
        }
    finally:
        conn.close()


def _reason_buckets(rows: list[dict], override_count: int) -> list[dict]:
    """Top three explicit reasons plus a consolidated Other bucket."""
    if override_count == 0:
        return []
    counts: dict[str, int] = {}
    for row in rows:
        name = str(row.get("reason") or "Not specified").strip() or "Not specified"
        count = int(row.get("reason_count") or 0)
        counts[name] = counts.get(name, 0) + count
    # Treat literal 'Other' as part of the aggregate Other bucket.
    leading = sorted(((n, c) for n, c in counts.items() if n.casefold() != "other"),
                     key=lambda pair: (-pair[1], pair[0].casefold()))[:3]
    used = sum(count for _, count in leading)
    other = max(0, override_count - used)
    output = [{"reason": name, "count": count,
               "share": count / override_count} for name, count in leading]
    if other:
        output.append({"reason": "Other", "count": other,
                       "share": other / override_count})
    return output


def get_report_snapshot(
    user: Mapping[str, Any], *, days: int = 30, branch_id: int | None = None,
    connection_factory: Callable[[], Any] | None = None,
    today: date | None = None,
    start_date: date | None = None, end_date: date | None = None,
    staff_id: int | None = None, category_id: int | None = None,
    item_type_id: int | None = None, adjustment: str = "all",
) -> dict:
    """Return one filtered, internally consistent report dataset.

    `branch_id=None` means all branches. Count of items priced is count of pricing
    decisions, consistent with the current 1-item-per-submission application flow.
    Only administrators can access data, and authorization is rechecked in MySQL.
    """
    if (start_date is None) != (end_date is None):
        raise ValueError("Both start and end dates are required for a custom range")
    if start_date is None:
        start, end = report_window(days, today=today)
    else:
        start, end = custom_report_window(start_date, end_date, today=today)
    for name, value in (("branch", branch_id), ("staff", staff_id),
                        ("category", category_id), ("item type", item_type_id)):
        _optional_id(name, value)
    if type(adjustment) is not str or adjustment not in ADJUSTMENTS:
        raise ValueError("Invalid price adjustment filter")
    conn = (connection_factory or _connection)()
    try:
        _assert_admin(conn, user)
        branches = _rows(conn, """
            SELECT Branch_Id AS branch_id, Branch_Name AS branch_name
            FROM BRANCH ORDER BY Branch_Name, Branch_Id
        """)
        branch_lookup = {int(b["branch_id"]): str(b["branch_name"]) for b in branches}
        if branch_id is not None and branch_id not in branch_lookup:
            raise ValueError("Selected branch no longer exists")
        where, params = _conditions(start, end, branch_id, staff_id, category_id,
                                    item_type_id, adjustment)
        metrics = _one(conn, f"""
            SELECT COUNT(*) AS items_priced,
                   COALESCE(AVG(d.Decision_Final_Price), 0) AS average_final_price,
                   COALESCE(SUM(CASE WHEN d.Decision_Final_Price <>
                                    g.Guideline_Suggested_Price THEN 1 ELSE 0 END), 0)
                       AS override_count,
                   COUNT(DISTINCT CASE WHEN u.User_Is_Active = 1 THEN
                                            d.Staff_Id ELSE NULL END)
                       AS active_staff_with_activity
            FROM PRICING_DECISION AS d
            JOIN PRICING_GUIDELINE AS g ON g.Guideline_Id = d.Guideline_Id
            JOIN STORE_STAFF AS ss ON ss.Staff_Id = d.Staff_Id
            JOIN USER_ACCOUNT AS u ON u.User_Id = ss.Staff_Id
            JOIN ITEM AS i ON i.Item_Id = d.Item_Id
            WHERE {where}
        """, params) or {}
        item_count = int(metrics.get("items_priced") or 0)
        override_count = int(metrics.get("override_count") or 0)
        metric_values = {
            "items_priced": item_count,
            "average_final_price": Decimal(str(metrics.get("average_final_price") or 0)),
            "override_count": override_count,
            "override_rate": override_count / item_count if item_count else 0.0,
            "active_staff_with_activity": int(metrics.get("active_staff_with_activity") or 0),
        }
        categories = _rows(conn, f"""
            SELECT c.Category_Name AS category_name,
                   COUNT(*) AS items_priced,
                   AVG(d.Decision_Final_Price) AS average_final_price
            FROM PRICING_DECISION AS d
            JOIN ITEM AS i ON i.Item_Id = d.Item_Id
            JOIN CATEGORY AS c ON c.Category_Id = i.Category_Id
            JOIN PRICING_GUIDELINE AS g ON g.Guideline_Id = d.Guideline_Id
            JOIN STORE_STAFF AS ss ON ss.Staff_Id = d.Staff_Id
            WHERE {where}
            GROUP BY c.Category_Id, c.Category_Name
            ORDER BY average_final_price DESC, items_priced DESC, c.Category_Name
            LIMIT 5
        """, params)
        reason_rows = _rows(conn, f"""
            SELECT COALESCE(NULLIF(TRIM(d.Decision_Reason), ''), 'Not specified')
                       AS reason, COUNT(*) AS reason_count
            FROM PRICING_DECISION AS d
            JOIN PRICING_GUIDELINE AS g ON g.Guideline_Id = d.Guideline_Id
            JOIN STORE_STAFF AS ss ON ss.Staff_Id = d.Staff_Id
            JOIN ITEM AS i ON i.Item_Id = d.Item_Id
            WHERE {where}
              AND d.Decision_Final_Price <> g.Guideline_Suggested_Price
            GROUP BY reason
            ORDER BY reason_count DESC, reason
        """, params)
        staff = _rows(conn, f"""
            SELECT u.User_Id AS staff_id,
                   CONCAT(u.User_First_Name, ' ', u.User_Last_Name) AS staff_name,
                   b.Branch_Name AS branch_name,
                   u.User_Is_Active AS is_active,
                   COUNT(*) AS items_priced,
                   AVG(d.Decision_Final_Price) AS average_final_price,
                   SUM(CASE WHEN d.Decision_Final_Price <>
                                 g.Guideline_Suggested_Price THEN 1 ELSE 0 END)
                       AS override_count
            FROM PRICING_DECISION AS d
            JOIN PRICING_GUIDELINE AS g ON g.Guideline_Id = d.Guideline_Id
            JOIN STORE_STAFF AS ss ON ss.Staff_Id = d.Staff_Id
            JOIN USER_ACCOUNT AS u ON u.User_Id = d.Staff_Id
            JOIN BRANCH AS b ON b.Branch_Id = ss.Branch_Id
            JOIN ITEM AS i ON i.Item_Id = d.Item_Id
            WHERE {where}
            GROUP BY u.User_Id, u.User_First_Name, u.User_Last_Name,
                     u.User_Is_Active, b.Branch_Id, b.Branch_Name
            ORDER BY items_priced DESC, u.User_Last_Name, u.User_First_Name
            LIMIT {MAX_STAFF_ROWS}
        """, params)
        # Staff rows include historic decisions by now-inactive staff (otherwise
        # tabular activity would not reconcile with overall report totals).
        return {
            "days": days if start_date is None else None,
            "start": start, "end": end, "branch_id": branch_id,
            "staff_id": staff_id, "category_id": category_id,
            "item_type_id": item_type_id, "adjustment": adjustment,
            "branch_name": branch_lookup.get(branch_id, "All branches"),
            "branches": branches,
            "metrics": metric_values,
            "categories": categories,
            "reasons": _reason_buckets(reason_rows, override_count),
            "staff": staff,
            "staff_limit": MAX_STAFF_ROWS,
        }
    finally:
        conn.close()
