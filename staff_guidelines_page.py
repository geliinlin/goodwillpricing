"""Training/reference-only Guidelines page for Store Staff.

Does not import administrator editing pages or call database write endpoints.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
import logging
from typing import Any, Callable, Mapping

import streamlit as st

from dashboard_data import DashboardPermissionError
from dashboard_page import _intro
from staff_guidelines_service import (
    get_staff_guidelines, filter_guidelines,
    get_staff_attribute_descriptions, filter_attribute_descriptions,
)

LOG = logging.getLogger(__name__)


def _options(rows: list[dict], key: str) -> list[str]:
    return ['All'] + sorted({str(r[key]) for r in rows if r.get(key) is not None},
                            key=str.casefold)



ATTRIBUTE_GROUPS = ('Item Types', 'Categories', 'Brand Tiers',
                    'Item Conditions', 'Branches')


def _attribute_reference(user: Mapping[str, Any]) -> None:
    """Accessible, read-only explanations of master-data attributes."""
    state_key = f"gw_attribute_reference_open_{user['user_id']}"
    is_open = bool(st.session_state.get(state_key, False))

    def toggle_reference():
        st.session_state[state_key] = not st.session_state.get(state_key, False)

    st.button('Hide Attribute Descriptions' if is_open else 'View Attribute Descriptions',
              key=f'gw_attr_reference_toggle_{user["user_id"]}',
              type='secondary', on_click=toggle_reference)
    if not is_open:
        return

    try:
        descriptions = get_staff_attribute_descriptions(user)
    except DashboardPermissionError:
        st.error('You do not have permission to view attribute descriptions.')
        return
    except Exception:
        LOG.exception('Failed loading attribute reference')
        st.error('Unable to load attribute descriptions. Check server logs.')
        return

    st.subheader('Attribute Descriptions')
    st.caption('Read-only definitions of active master data. '
               'Descriptions are maintained by authorized Administrators.')
    chosen = st.selectbox('Attribute group', ATTRIBUTE_GROUPS,
                          key='gw_attribute_reference_group')
    search = st.text_input('Search descriptions',
                           key='gw_attribute_reference_search',
                           placeholder='Search by attribute name or definition')
    filtered = filter_attribute_descriptions(descriptions, group=chosen, search=search)
    records = []
    for record in filtered:
        display = {'Name': str(record.get('attribute_name') or ''),
                   'Description': str(record.get('description') or '').strip()
                                  or 'No description provided yet.'}
        if chosen == 'Categories':
            display = {'Item Type': str(record.get('item_type_name') or ''), **display}
        records.append(display)
    if records:
        st.dataframe(records, width='stretch', hide_index=True,
                     height=min(460, max(160, 42 * (len(records) + 1))))
    else:
        st.info('No attributes match this search.')


def show_staff_guidelines(user: Mapping[str, Any],
                          on_navigate: Callable[[str], None] | None = None) -> None:
    # Do not rely on navigation visibility as an authorization boundary.
    if user.get('user_type') != 'Store Staff':
        st.error('This reference page is available to Store Staff only.')
        return
    try:
        rows = get_staff_guidelines(user)
    except DashboardPermissionError:
        st.error('You do not have permission to view pricing guidelines.')
        return
    except Exception:
        LOG.exception('Failed loading staff guideline reference')
        st.error('Unable to load pricing guidelines. Please check server logs.')
        return

    _intro('Store Operations', 'Pricing Guidelines',
           'Browse current suggested prices by item type, category, brand tier and condition.')
    st.caption('Read-only reference · Active guidelines only · Corporate suggested prices')

    # Loading the reference is opt-in so a collapsed section does not run
    # five-table SQL on every visit to Guidelines.
    _attribute_reference(user)

    # Dropdowns are derived exclusively from verified, active guideline rows.
    type_col, category_col, brand_col, condition_col = st.columns(4, gap='small')
    with type_col:
        item_type = st.selectbox('Item Type', _options(rows, 'item_type'),
                                 key='gw_learn_item_type')
    in_type = [r for r in rows if item_type == 'All' or r['item_type'] == item_type]
    with category_col:
        category = st.selectbox('Category', _options(in_type, 'category'),
                                key='gw_learn_category')
    with brand_col:
        brand = st.selectbox('Brand Tier', _options(rows, 'brand_tier'),
                             key='gw_learn_brand')
    with condition_col:
        condition = st.selectbox('Condition', _options(rows, 'item_condition'),
                                 key='gw_learn_condition')
    search = st.text_input('Search guideline ID or attribute',
                           key='gw_learn_search',
                           placeholder='e.g., Sneakers, Furniture, 120')
    matching = filter_guidelines(rows, item_type=item_type, category=category,
                                 brand_tier=brand, item_condition=condition,
                                 search=search)
    st.caption(f'{len(matching):,} matching guidelines · {len(rows):,} currently active')

    if not matching:
        st.info('No active guidelines match these filters. Try a different selection.')
        return

    table_rows = []
    for row in matching:
        try:
            price = Decimal(str(row['suggested_price']))
            if not price.is_finite() or price < 0:
                raise InvalidOperation
            price_label = f'${price:,.2f}'
        except (InvalidOperation, TypeError, ValueError):
            LOG.warning('Invalid suggested price for guideline %s', row.get('guideline_id'))
            price_label = 'Unavailable'
        table_rows.append({
            'No.': row['guideline_id'],
            'Item Type': row['item_type'],
            'Category': row['category'],
            'Brand Tier': row['brand_tier'],
            'Condition': row['item_condition'],
            'Suggested Price': price_label,
        })
    st.dataframe(table_rows, hide_index=True, width='stretch',
                 height=min(640, max(180, 44 * (len(table_rows) + 1))))
    st.caption('Suggested prices are for reference. To record a pricing decision, '
               'use Price an Item and follow the standard submission workflow.')
    if on_navigate and st.button('Price an Item', key='gw_learn_start_pricing'):
        on_navigate('item_search')
