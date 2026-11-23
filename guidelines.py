"""Administrator guideline services for the unchanged G3 schema.

All application writes share one database-scoped advisory lock. Price/attribute
edits insert a replacement and retire the old row in the same transaction, so
PRICING_DECISION continues to reference its original price and attributes.
"""

from contextlib import contextmanager
from decimal import Decimal, InvalidOperation
import hashlib
import json
import logging

import mysql.connector

from auth import require_user_type
from database import get_connection

logger = logging.getLogger(__name__)

LOOKUPS = (
    ("CATEGORY", "Category_Id", "Category_Name", "Category"),
    ("BRAND", "Brand_Id", "Brand_Name", "Brand"),
    ("ITEM_CONDITION", "Condition_Id", "Condition_Name", "Condition"),
    ("ITEM_TYPE", "Item_Type_Id", "Item_Type_Name", "Item type"),
)
ATTRIBUTE_FIELDS = tuple(entry[1] for entry in LOOKUPS)
VERSION_FIELDS = (
    "Guideline_Id", "Guideline_Suggested_Price", "Guideline_Last_Updated",
    "Guideline_Is_Active", "Admin_Id", *ATTRIBUTE_FIELDS,
)


class GuidelineError(ValueError):
    """A validation or concurrency problem safe to display to the user."""


def parse_price(value):
    try:
        price = Decimal(str(value).strip())
    except (InvalidOperation, ValueError):
        raise GuidelineError("Enter a valid price, such as 12.99.") from None
    if not price.is_finite() or not Decimal("0.01") <= price <= Decimal("999999.99"):
        raise GuidelineError("Price must be between $0.01 and $999,999.99.")
    if price != price.quantize(Decimal("0.01")):
        raise GuidelineError("Use no more than two decimal places.")
    return price.quantize(Decimal("0.01"))


def snapshot_token(row):
    """Compare the displayed record, including price, attributes and status."""
    values = [str(row[field]) for field in VERSION_FIELDS]
    return hashlib.sha256(json.dumps(values).encode()).hexdigest()


def _authorize(cursor, user, writing=False):
    # Never trust a stale session role alone.
    cursor.execute(
        "SELECT u.User_Id FROM USER_ACCOUNT u "
        "JOIN ADMINISTRATOR a ON a.Admin_Id = u.User_Id "
        "WHERE u.User_Id = %s AND u.User_Is_Active = 1 "
        "AND u.User_Type = 'Administrator'" + (" FOR UPDATE" if writing else ""),
        (user["user_id"],),
    )
    if cursor.fetchone() is None:
        raise PermissionError("Your administrator access is no longer available. Log in again.")


@contextmanager
def _session(user, writing=False):
    require_user_type(user, {"Administrator"})
    connection = get_connection()
    cursor = None
    lock_name = None
    try:
        cursor = connection.cursor(dictionary=True)
        if writing:
            cursor.execute("SELECT DATABASE() AS db_name")
            db_name = cursor.fetchone()["db_name"]
            name = "goodwill.guidelines." + hashlib.sha256(db_name.encode()).hexdigest()[:40]
            cursor.execute("SELECT GET_LOCK(%s, 5) AS acquired", (name,))
            if cursor.fetchone()["acquired"] != 1:
                raise GuidelineError("Another administrator is saving. Please try again shortly.")
            lock_name = name
            connection.rollback()
            connection.start_transaction(isolation_level="READ COMMITTED")
        _authorize(cursor, user, writing)
        yield cursor
        if writing:
            connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        # GET_LOCK is connection-scoped, not released by commit/rollback.
        if cursor is not None:
            try:
                if lock_name:
                    cursor.execute("SELECT RELEASE_LOCK(%s) AS released", (lock_name,))
                    cursor.fetchone()
            except mysql.connector.Error:
                logger.exception("Could not explicitly release guideline lock; closing connection")
            finally:
                cursor.close()
        connection.close()


def load_catalog(user):
    """Load dynamic dropdowns and all guidelines for this small course project."""
    with _session(user) as cursor:
        lookups = {}
        for table, id_field, name_field, _ in LOOKUPS:
            # Identifiers come only from the fixed LOOKUPS allowlist.
            extra = ', Item_Type_Id' if table == 'CATEGORY' else ''
            cursor.execute(f"SELECT {id_field}, {name_field}{extra} FROM {table} ORDER BY {name_field}")
            lookup_rows = cursor.fetchall()
            lookups[id_field] = {row[id_field]: row[name_field] for row in lookup_rows}
            if table == 'CATEGORY':
                lookups['Category_Item_Type'] = {
                    row['Category_Id']: row.get('Item_Type_Id') for row in lookup_rows
                }
        cursor.execute("""
            SELECT g.*, c.Category_Name, b.Brand_Name, ic.Condition_Name,
                   it.Item_Type_Name,
                   CONCAT(u.User_First_Name, ' ', u.User_Last_Name) AS Admin_Name
            FROM PRICING_GUIDELINE g
            JOIN CATEGORY c ON c.Category_Id = g.Category_Id
            JOIN BRAND b ON b.Brand_Id = g.Brand_Id
            JOIN ITEM_CONDITION ic ON ic.Condition_Id = g.Condition_Id
            JOIN ITEM_TYPE it ON it.Item_Type_Id = g.Item_Type_Id
            JOIN USER_ACCOUNT u ON u.User_Id = g.Admin_Id
            ORDER BY g.Guideline_Is_Active DESC, g.Guideline_Id DESC
        """)
        return lookups, cursor.fetchall()


def _validate_attributes(cursor, attributes):
    values = []
    for table, id_field, _, label in LOOKUPS:
        value = attributes.get(id_field)
        if type(value) is not int:
            raise GuidelineError(f"Select a valid {label.lower()}.")
        active_field = {
            "CATEGORY": "Category_Is_Active", "BRAND": "Brand_Is_Active",
            "ITEM_CONDITION": "Condition_Is_Active", "ITEM_TYPE": "Item_Type_Is_Active",
        }[table]
        parent_check = ' AND Item_Type_Id = %s' if table == 'CATEGORY' else ''
        params = (value, attributes.get('Item_Type_Id')) if table == 'CATEGORY' else (value,)
        cursor.execute(f"SELECT {id_field} FROM {table} WHERE {id_field} = %s "
                       f"AND {active_field} = 1{parent_check} LOCK IN SHARE MODE", params)
        if cursor.fetchone() is None:
            raise GuidelineError(f"The selected {label.lower()} is no longer available. Refresh the page.")
        values.append(value)
    return tuple(values)


def _ensure_unique_active(cursor, values, excluding=None):
    cursor.execute(
        "SELECT Guideline_Id FROM PRICING_GUIDELINE "
        "WHERE Category_Id = %s AND Brand_Id = %s AND Condition_Id = %s "
        "AND Item_Type_Id = %s AND Guideline_Is_Active = 1 "
        "AND (%s IS NULL OR Guideline_Id <> %s) LIMIT 1 FOR UPDATE",
        (*values, excluding, excluding),
    )
    duplicate = cursor.fetchone()
    if duplicate:
        raise GuidelineError(
            f"Active guideline #{duplicate['Guideline_Id']} already uses these four attributes. "
            "Edit that guideline or deactivate it first."
        )


def _ensure_no_identical_guideline(cursor, values, price):
    """Reject a duplicate four-attribute + suggested-price record.

    Check Active AND Inactive rows: archived guidelines remain real records.
    The advisory lock in _session makes this consistent with normal app writes.
    This is intentionally a *creation* rule; versioned edits keep their existing
    historical behavior.
    """
    cursor.execute(
        "SELECT Guideline_Id, Guideline_Is_Active "
        "FROM PRICING_GUIDELINE "
        "WHERE Category_Id = %s AND Brand_Id = %s AND Condition_Id = %s "
        "AND Item_Type_Id = %s AND Guideline_Suggested_Price = %s "
        "ORDER BY Guideline_Is_Active DESC, Guideline_Id DESC "
        "LIMIT 1 FOR UPDATE",
        (*values, price),
    )
    existing = cursor.fetchone()
    if existing is not None:
        existing_id = int(existing["Guideline_Id"])
        existing_status = "Active" if bool(existing["Guideline_Is_Active"]) else "Inactive"
        raise GuidelineError(
            f"Guideline #{existing_id} already exists ({existing_status}) with "
            "the same Item Type, Category, Brand Tier, Condition, and "
            f"Suggested Price (${price:,.2f}). No new guideline was created. "
            f"Select Guideline #{existing_id} in the table to review or edit it."
        )


def _read_expected(cursor, guideline_id, expected_token):
    cursor.execute("SELECT * FROM PRICING_GUIDELINE WHERE Guideline_Id = %s FOR UPDATE", (guideline_id,))
    row = cursor.fetchone()
    if row is None:
        raise GuidelineError("This guideline no longer exists. Reload the list.")
    if not expected_token or snapshot_token(row) != expected_token:
        raise GuidelineError("This guideline changed after you opened it. Click Reload selected guideline before saving.")
    return row


def _insert(cursor, admin_id, values, price, active):
    # Safe across cooperating app writers only, because _session holds GET_LOCK.
    cursor.execute("SELECT COALESCE(MAX(Guideline_Id), 0) + 1 AS next_id FROM PRICING_GUIDELINE")
    guideline_id = int(cursor.fetchone()["next_id"])
    if guideline_id > 2147483647:
        raise GuidelineError("Guideline IDs have reached the database INT limit.")
    cursor.execute("""
        INSERT INTO PRICING_GUIDELINE
            (Guideline_Id, Guideline_Suggested_Price, Guideline_Last_Updated,
             Guideline_Is_Active, Category_Id, Brand_Id, Condition_Id, Item_Type_Id, Admin_Id)
        VALUES (%s, %s, NOW(), %s, %s, %s, %s, %s, %s)
    """, (guideline_id, price, int(active), *values, admin_id))
    return guideline_id


def save_guideline(user, attributes, price, active=True, *, guideline_id=None, expected_token=None):
    """Create or edit. An edited price/attribute combination gets a new ID."""
    require_user_type(user, {"Administrator"})
    price = parse_price(price)
    if type(active) is not bool:
        raise GuidelineError("Status must be Active or Inactive.")
    with _session(user, writing=True) as cursor:
        values = _validate_attributes(cursor, attributes)
        old = _read_expected(cursor, guideline_id, expected_token) if guideline_id is not None else None
        if old is None:
            # Check full duplicates BEFORE the active-combination rule, so the
            # user gets the precise existing Guideline ID and its status.
            _ensure_no_identical_guideline(cursor, values, price)
        if active:
            _ensure_unique_active(cursor, values, guideline_id)
        if old is None:
            return _insert(cursor, user["user_id"], values, price, active)
        changed = price != old["Guideline_Suggested_Price"] or values != tuple(old[f] for f in ATTRIBUTE_FIELDS)
        if changed:
            # Preserve old price, attributes and owner for existing decisions.
            cursor.execute(
                "UPDATE PRICING_GUIDELINE SET Guideline_Is_Active = 0, "
                "Guideline_Last_Updated = NOW() WHERE Guideline_Id = %s", (guideline_id,),
            )
            return _insert(cursor, user["user_id"], values, price, active)
        if bool(old["Guideline_Is_Active"]) != active:
            cursor.execute(
                "UPDATE PRICING_GUIDELINE SET Guideline_Is_Active = %s, "
                "Guideline_Last_Updated = NOW() WHERE Guideline_Id = %s", (int(active), guideline_id),
            )
        return guideline_id



def create_item_type(user, name: str) -> int:
    """Compatibility wrapper; all master-data writes now require Configuration access.

    This old service entry point must not bypass the stricter Administrator
    permission checks, duplicate rules, audit trail, or new active flag.
    """
    from configuration_service import ConfigurationError, create_master_data
    try:
        return create_master_data(user, 'item_types', {'Item_Type_Name': name})
    except ConfigurationError as exc:
        raise GuidelineError(str(exc)) from exc


def set_guideline_active(user, guideline_id, active, expected_token):
    """Separate status action; does not rewrite prices or delete linked rows."""
    if type(active) is not bool:
        raise GuidelineError("Status must be Active or Inactive.")
    with _session(user, writing=True) as cursor:
        row = _read_expected(cursor, guideline_id, expected_token)
        if active:
            _validate_attributes(cursor, {field: row[field] for field in ATTRIBUTE_FIELDS})
            _ensure_unique_active(cursor, tuple(row[f] for f in ATTRIBUTE_FIELDS), guideline_id)
        if bool(row["Guideline_Is_Active"]) != active:
            cursor.execute(
                "UPDATE PRICING_GUIDELINE SET Guideline_Is_Active = %s, "
                "Guideline_Last_Updated = NOW() WHERE Guideline_Id = %s", (int(active), guideline_id),
            )
