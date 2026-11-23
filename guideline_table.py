"""Pure display/filter/sort logic for the admin Guidelines table.

No Streamlit, database access, or data writes. Column sort order is the order
headers are activated; same header cycles ascending -> descending -> off.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Mapping, Sequence

# (label, database field, key); presented in the new six-column layout.
COLUMNS = (
    ('No.', 'Guideline_Id', 'id'),
    ('Item Type', 'Item_Type_Name', 'type'),
    ('Category', 'Category_Name', 'category'),
    ('Brand Tier', 'Brand_Name', 'brand'),
    ('Condition', 'Condition_Name', 'condition'),
    ('Suggested Price', 'Guideline_Suggested_Price', 'price'),
)
FILTER_FIELDS = {key: field for _, field, key in COLUMNS}


def display_value(row: Mapping, key: str) -> str:
    field = FILTER_FIELDS[key]
    raw = row.get(field)
    if raw is None:
        return ''
    if key == 'price':
        return f'${Decimal(str(raw)):,.2f}'
    if key == 'id':
        return str(int(raw))
    return str(raw)


def filter_options(rows: Sequence[Mapping], key: str) -> list[str]:
    if key not in FILTER_FIELDS:
        raise ValueError('Unknown column')
    values = {display_value(row, key) for row in rows}
    values.discard('')
    if key in {'id', 'price'}:
        return sorted(values, key=lambda v: Decimal(v.replace('$', '').replace(',', '')))
    return sorted(values, key=str.casefold)


def filter_rows(
    rows: Sequence[Mapping],
    selected: Mapping[str, Sequence[str]],
    *,
    status: str = "All",
) -> list[dict]:
    """Filter AND across columns, OR inside a column, and apply status.

    Status is a filter control only; it is deliberately not a table column.
    """
    for key in selected:
        if key not in FILTER_FIELDS:
            raise ValueError("Unknown filter column")
    if status not in {"All", "Active", "Inactive"}:
        raise ValueError("Unknown guideline status")
    result = []
    for row in rows:
        if status != "All" and bool(row["Guideline_Is_Active"]) != (status == "Active"):
            continue
        if all(not chosen or display_value(row, key) in chosen
               for key, chosen in selected.items()):
            result.append(dict(row))
    return result


def next_sort(sorts: Sequence[tuple[str, str]], key: str) -> list[tuple[str, str]]:
    """Preserve click-order priority. A column cycles ASC -> DESC -> off."""
    if key not in FILTER_FIELDS:
        raise ValueError('Unknown sort column')
    out = list(sorts)
    for index, (field, direction) in enumerate(out):
        if field == key:
            if direction == 'asc':
                out[index] = (key, 'desc')
            else:
                out.pop(index)
            return out
    out.append((key, 'asc'))
    return out


def sort_rows(rows: Sequence[Mapping], sorts: Sequence[tuple[str, str]]) -> list[dict]:
    for key, direction in sorts:
        if key not in FILTER_FIELDS or direction not in {'asc', 'desc'}:
            raise ValueError('Invalid sort instruction')
    ordered = [dict(row) for row in rows]
    # A deterministic tie-breaker, overridden by later stable sorts.
    ordered.sort(key=lambda row: int(row['Guideline_Id']), reverse=True)
    for key, direction in reversed(sorts):
        field = FILTER_FIELDS[key]
        def value(row):
            data = row.get(field)
            if data is None:
                return (1, '')
            if key in {'id', 'price'}:
                return (0, Decimal(str(data)))
            return (0, str(data).casefold())
        ordered.sort(key=value, reverse=direction == 'desc')
    return ordered
