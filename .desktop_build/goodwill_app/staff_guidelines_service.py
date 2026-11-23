"""Read-only active pricing guideline catalog for authenticated Store Staff.

Unlike guideline_page / guidelines.py, this module exposes no write operations.
Every request re-verifies account status and STORE_STAFF membership in MySQL.
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Callable

from dashboard_data import (
    DashboardPermissionError,
    _connection,
    _fetchall,
    _verify_user,
)


def get_staff_guidelines(user: Mapping[str, Any],
                         connection_factory: Callable[[], Any] | None = None) -> list[dict]:
    """Return only ACTIVE guidelines and ACTIVE attributes for store staff.

    Dataset is read-only and intentionally independent of a staff member's
    branch: corporate prices are intended as learning/reference material.
    No administrative fields (user IDs, cost data, inactive versions) leak.
    """
    if user.get('user_type') != 'Store Staff':
        raise DashboardPermissionError('Store Staff access required')

    with _connection(connection_factory) as conn:
        role, _ = _verify_user(conn, user)
        if role != 'Store Staff':
            raise DashboardPermissionError('Store Staff access required')
        return _fetchall(conn, """
            SELECT
                g.Guideline_Id AS guideline_id,
                it.Item_Type_Name AS item_type,
                c.Category_Name AS category,
                b.Brand_Name AS brand_tier,
                ic.Condition_Name AS item_condition,
                g.Guideline_Suggested_Price AS suggested_price
            FROM PRICING_GUIDELINE AS g
            JOIN ITEM_TYPE AS it ON it.Item_Type_Id = g.Item_Type_Id
            JOIN CATEGORY AS c ON c.Category_Id = g.Category_Id
                              AND c.Item_Type_Id = g.Item_Type_Id
            JOIN BRAND AS b ON b.Brand_Id = g.Brand_Id
            JOIN ITEM_CONDITION AS ic ON ic.Condition_Id = g.Condition_Id
            WHERE g.Guideline_Is_Active = 1
              AND it.Item_Type_Is_Active = 1
              AND c.Category_Is_Active = 1
              AND b.Brand_Is_Active = 1
              AND ic.Condition_Is_Active = 1
            ORDER BY it.Item_Type_Name, c.Category_Name,
                     b.Brand_Name, ic.Condition_Name, g.Guideline_Id
        """)


def filter_guidelines(rows: list[dict], *, item_type: str = 'All',
                      category: str = 'All', brand_tier: str = 'All',
                      item_condition: str = 'All', search: str = '') -> list[dict]:
    """UI-only filtering of the already authorized active catalog."""
    term = search.casefold().strip()
    selected = {
        'item_type': item_type, 'category': category,
        'brand_tier': brand_tier, 'item_condition': item_condition,
    }
    result = []
    for row in rows:
        if any(value != 'All' and str(row.get(key, '')) != value
               for key, value in selected.items()):
            continue
        haystack = ' '.join(str(row.get(key, '')) for key in
                            ('guideline_id', 'item_type', 'category',
                             'brand_tier', 'item_condition'))
        if term and term not in haystack.casefold():
            continue
        result.append(row)
    return result


def get_staff_attribute_descriptions(
    user: Mapping[str, Any], connection_factory: Callable[[], Any] | None = None
) -> list[dict]:
    """Read-only reference for all ACTIVE master-data values.

    Never trusts client-side role flags: the DB validates the active Store Staff
    subtype before any master-data rows are returned. Descriptions are optional.
    """
    if user.get('user_type') != 'Store Staff':
        raise DashboardPermissionError('Store Staff access required')

    with _connection(connection_factory) as conn:
        role, _ = _verify_user(conn, user)
        if role != 'Store Staff':
            raise DashboardPermissionError('Store Staff access required')
        return _fetchall(conn, """
            SELECT 'Item Types' AS attribute_group, Item_Type_Id AS attribute_id,
                   Item_Type_Name AS attribute_name,
                   COALESCE(Item_Type_Description, '') AS description,
                   NULL AS item_type_name
              FROM ITEM_TYPE WHERE Item_Type_Is_Active = 1
            UNION ALL
            SELECT 'Categories', c.Category_Id, c.Category_Name,
                   COALESCE(c.Category_Description, ''), it.Item_Type_Name
              FROM CATEGORY c
              JOIN ITEM_TYPE it ON it.Item_Type_Id = c.Item_Type_Id
             WHERE c.Category_Is_Active = 1 AND it.Item_Type_Is_Active = 1
            UNION ALL
            SELECT 'Brand Tiers', Brand_Id, Brand_Name,
                   COALESCE(Brand_Description, ''), NULL
              FROM BRAND WHERE Brand_Is_Active = 1
            UNION ALL
            SELECT 'Item Conditions', Condition_Id, Condition_Name,
                   COALESCE(Condition_Description, ''), NULL
              FROM ITEM_CONDITION WHERE Condition_Is_Active = 1
            UNION ALL
            SELECT 'Branches', Branch_Id, Branch_Name,
                   COALESCE(Branch_Description, ''), NULL
              FROM BRANCH WHERE Branch_Is_Active = 1
            ORDER BY attribute_group, item_type_name, attribute_name, attribute_id
        """)


def filter_attribute_descriptions(rows: list[dict], *, group: str,
                                  search: str = '') -> list[dict]:
    """Pure filter; never modifies the source rows or broadens DB permissions."""
    term = search.strip().casefold()
    return [r for r in rows if r.get('attribute_group') == group and
            (not term or term in ' '.join(str(r.get(k) or '') for k in
             ('attribute_name', 'description', 'item_type_name')).casefold())]
