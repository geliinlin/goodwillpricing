"""Module 3 data access for Goodwill Pricing Management System.

Read-only: no ITEM or PRICING_DECISION rows are inserted by this module.
Compatible with the existing database.get_connection() connection factory.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, Callable, Dict, List, Mapping, Optional

ConnectionFactory = Callable[[], Any]

LOOKUP_TABLES = (
    ("Item_Type_Id", "ITEM_TYPE", "Item_Type_Id", "Item_Type_Name"),
    ("Category_Id", "CATEGORY", "Category_Id", "Category_Name"),
    ("Brand_Id", "BRAND", "Brand_Id", "Brand_Name"),
    ("Condition_Id", "ITEM_CONDITION", "Condition_Id", "Condition_Name"),
)

# Use the same eligibility checks when quoting a price and locking it for sale.
ACTIVE_GUIDELINE_SQL = """
    SELECT g.Guideline_Id, g.Guideline_Suggested_Price, g.Guideline_Last_Updated,
           c.Category_Name, br.Brand_Name, co.Condition_Name, t.Item_Type_Name
    FROM PRICING_GUIDELINE g
    JOIN ITEM_TYPE t ON t.Item_Type_Id = g.Item_Type_Id
    JOIN CATEGORY c ON c.Category_Id = g.Category_Id AND c.Item_Type_Id = g.Item_Type_Id
    JOIN BRAND br ON br.Brand_Id = g.Brand_Id
    JOIN ITEM_CONDITION co ON co.Condition_Id = g.Condition_Id
    WHERE g.Item_Type_Id = %s AND g.Category_Id = %s
      AND g.Brand_Id = %s AND g.Condition_Id = %s
      AND g.Guideline_Is_Active = 1 AND t.Item_Type_Is_Active = 1
      AND c.Category_Is_Active = 1 AND br.Brand_Is_Active = 1 AND co.Condition_Is_Active = 1
    ORDER BY g.Guideline_Last_Updated DESC, g.Guideline_Id DESC
    LIMIT 2
"""


class ItemEntryError(ValueError):
    """Validation message that can be displayed to staff."""


@dataclass(frozen=True)
class GuidelineMatch:
    """status is 'matched', 'not_found', or 'conflict'."""
    status: str
    guideline_id: Optional[int] = None
    suggested_price: Optional[Decimal] = None
    last_updated: Any = None
    conflicting_ids: tuple[int, ...] = ()


def _connection(factory: Optional[ConnectionFactory]) -> Any:
    if factory is None:
        from database import get_connection  # Reuse Module 1 connection settings.
        factory = get_connection
    return factory()


def get_lookup_options(
    connection_factory: Optional[ConnectionFactory] = None,
) -> Dict[str, Dict[int, str]]:
    """Get dropdown options; every label and id comes from MySQL."""
    conn = _connection(connection_factory)
    try:
        cursor = conn.cursor(dictionary=True, buffered=True)
        try:
            options: Dict[str, Dict[int, str]] = {}
            for key, table, id_col, name_col in LOOKUP_TABLES:
                # Table/column names are constants above, not user inputs.
                active_col = {
                    "ITEM_TYPE": "Item_Type_Is_Active",
                    "CATEGORY": "Category_Is_Active",
                    "BRAND": "Brand_Is_Active",
                    "ITEM_CONDITION": "Condition_Is_Active",
                }[table]
                extra = ', `Item_Type_Id` AS parent_type' if table == 'CATEGORY' else ''
                cursor.execute(
                    f"SELECT `{id_col}` AS id, `{name_col}` AS name{extra} "
                    f"FROM `{table}` WHERE `{active_col}`=1 ORDER BY `{name_col}`, `{id_col}`"
                )
                rows = cursor.fetchall()
                options[key] = {int(row['id']): str(row['name']) for row in rows}
                if table == 'CATEGORY':
                    options['Category_Item_Type'] = {
                        int(row['id']): int(row['parent_type'])
                        for row in rows if row.get('parent_type') is not None
                    }
            return options
        finally:
            cursor.close()
    finally:
        conn.close()


def get_active_price_guideline(
    item_type_id: int,
    category_id: int,
    brand_id: int,
    condition_id: int,
    connection_factory: Optional[ConnectionFactory] = None,
) -> GuidelineMatch:
    """Exact-match an active guideline; fail closed on ambiguous matches."""
    ids = (item_type_id, category_id, brand_id, condition_id)
    if any(not isinstance(x, int) or isinstance(x, bool) or x <= 0 for x in ids):
        raise ItemEntryError("All four lookup IDs must be positive integers.")

    conn = _connection(connection_factory)
    try:
        cursor = conn.cursor(dictionary=True, buffered=True)
        try:
            cursor.execute(ACTIVE_GUIDELINE_SQL, ids)
            rows = cursor.fetchall()
        finally:
            cursor.close()
    finally:
        conn.close()

    if not rows:
        return GuidelineMatch(status="not_found")
    if len(rows) > 1:
        return GuidelineMatch(
            status="conflict",
            conflicting_ids=tuple(int(r["Guideline_Id"]) for r in rows),
        )

    row = rows[0]
    try:
        price = Decimal(str(row["Guideline_Suggested_Price"]))
    except InvalidOperation:
        raise ItemEntryError("Pricing guideline contains an invalid price.") from None
    if (not price.is_finite() or not Decimal('0.01') <= price <= Decimal('999999.99')
            or price != price.quantize(Decimal('0.01'))):
        raise ItemEntryError("Pricing guideline contains an invalid price.")
    return GuidelineMatch(
        status="matched",
        guideline_id=int(row["Guideline_Id"]),
        suggested_price=price,
        last_updated=row["Guideline_Last_Updated"],
    )


def get_authenticated_store_staff(
    user_id: int,
    connection_factory: Optional[ConnectionFactory] = None,
) -> Optional[Dict[str, Any]]:
    """Validate the session's user_id using active account and STAFF membership.

    Accept both 'Employee' (uploaded G3 script) and 'Store Staff' (older
    implementations), but never authorize a user based on a client-side role.
    """
    if not isinstance(user_id, int) or isinstance(user_id, bool) or user_id <= 0:
        return None

    conn = _connection(connection_factory)
    try:
        cursor = conn.cursor(dictionary=True, buffered=True)
        try:
            cursor.execute(
                """
                SELECT u.User_Id, u.User_First_Name, u.User_Last_Name,
                       s.Staff_Id, b.Branch_Id, b.Branch_Name, b.Branch_City
                FROM USER_ACCOUNT AS u
                INNER JOIN STORE_STAFF AS s ON s.Staff_Id = u.User_Id
                INNER JOIN BRANCH AS b ON b.Branch_Id = s.Branch_Id
                WHERE u.User_Id = %s
                  AND u.User_Is_Active = 1
                  AND b.Branch_Is_Active = 1
                  AND u.User_Type IN ('Employee', 'Store Staff')
                LIMIT 1
                """,
                (user_id,),
            )
            result = cursor.fetchone()
            return dict(result) if result else None
        finally:
            cursor.close()
    finally:
        conn.close()


def validate_item_entry(description: str | None, received_date: Any, today: Any) -> None:
    """Validate optional description (up to 255 chars) and received date."""
    if description is not None and not isinstance(description, str):
        raise ItemEntryError("Item description must be text when provided.")
    if len((description or '').strip()) > 255:
        raise ItemEntryError("Item description cannot exceed 255 characters.")
    if received_date is None or received_date > today:
        raise ItemEntryError("Received date cannot be in the future.")
