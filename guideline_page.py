"""Goodwill administrator guideline page: Figma-style UI with live MySQL data.

All database mutations delegate to guidelines.py. The listing and editor share
one workspace: select a guideline at left, update it at right.
"""

from collections import Counter
import hashlib
import json

import pandas as pd
from html import escape
import logging

import mysql.connector
import streamlit as st

from style_loader import load_styles
from guideline_table import COLUMNS, display_value, filter_options, filter_rows, next_sort, sort_rows
from auth import require_user_type
from guidelines import (
    ATTRIBUTE_FIELDS, LOOKUPS, GuidelineError, load_catalog,
    save_guideline, set_guideline_active, snapshot_token,
)

logger = logging.getLogger(__name__)


def _html(value):
    return escape(str(value), quote=True)


def _notice(message):
    st.session_state["gw_guideline_notice"] = message
    st.session_state.pop("gw_guideline_editor", None)
    st.session_state.pop("gw_guideline_snapshot", None)
    st.session_state["gw_guideline_epoch"] = st.session_state.get("gw_guideline_epoch", 0) + 1
    st.rerun()


def _error(exc):
    if isinstance(exc, (GuidelineError, PermissionError)):
        st.error(str(exc))
    else:
        logger.exception("Guideline database operation failed")
        st.error("Could not complete the database request. Please try again or contact an administrator.")


def _brand_header():
    with st.container(key="gw_nav"):
        brand, guidelines, users, reports, identity, signout = st.columns(
            [3.1, 1.0, 0.8, 0.9, 2.4, 0.9], vertical_alignment="center"
        )
        with brand:
            st.html('''
            <div class="gw-brand">
                <span class="gw-mark">g</span>
                <span><span class="gw-brand-name">goodwill</span>
                <span class="gw-brand-sub">PRICING DECISION SUPPORT SYSTEM</span></span>
            </div>''')
        with guidelines:
            st.html('<div class="gw-nav-active">Guidelines</div>')
        with users:
            st.button("Users", key="gw_users_soon", disabled=True, help="Coming in a later module")
        with reports:
            st.button("Reports", key="gw_reports_soon", disabled=True, help="Full reporting comes in Module 6")
        with identity:
            st.html('<div class="gw-identity">Corporate workspace<br><b>Administrator</b></div>')
        with signout:
            if st.button("Logout", key="gw_top_logout"):
                st.session_state.clear()
                st.rerun()


def _fields(lookups, prefix, row=None, *, compact=False):
    row = row or {}
    values = {}
    cols = None if compact else st.columns(2)

    display_order = {
        "Item_Type_Id": 0,
        "Category_Id": 1,
        "Brand_Id": 2,
        "Condition_Id": 3,
    }

    ordered_lookups = sorted(
        LOOKUPS,
        key=lambda entry: display_order[entry[1]]
    )

    for index, (_, field, _, label) in enumerate(ordered_lookups):
        choices = lookups[field]
        if field == 'Category_Id' and values.get('Item_Type_Id') is not None:
            parents = lookups.get('Category_Item_Type', {})
            if 'Category_Item_Type' in lookups:
                choices = {k: v for k, v in choices.items()
                           if parents.get(k) == values['Item_Type_Id']}
        ids = list(choices)
        selected = ids.index(row[field]) if row.get(field) in ids else 0
        widget_key = (f"{prefix}_{field}_{values['Item_Type_Id']}"
                      if field == "Category_Id" and values.get("Item_Type_Id") is not None
                      else f"{prefix}_{field}")
        if compact:
            values[field] = st.selectbox(
                label, ids, index=selected, format_func=choices.get,
                key=widget_key,
            )
        else:
            with cols[index % 2]:
                values[field] = st.selectbox(
                    label, ids, index=selected, format_func=choices.get,
                    key=widget_key,
                )
    price = st.text_input(
        "Suggested price ($)",
        value=str(row.get("Guideline_Suggested_Price", "")),
        placeholder="12.99", key=f"{prefix}_price",
    )
    active = st.checkbox(
        "Active", value=bool(row.get("Guideline_Is_Active", True)),
        key=f"{prefix}_active",
    )
    return values, price, active


def _open_selected_guideline(selected_id: int, source_rows: list[dict]) -> None:
    """Enter the existing, version-safe editor; selection never writes to MySQL."""
    record = next((row for row in source_rows if int(row["Guideline_Id"]) == selected_id), None)
    if record is None:
        return
    st.session_state["gw_guideline_editor"] = selected_id
    st.session_state["gw_guideline_snapshot"] = dict(record)
    # The native dataframe selection already triggers a rerun. The editor
    # renders after the table in the *same pass*, avoiding a second jump.


def _table(rows):
    with st.container(key="gw_guideline_table"):
        # Keep the three overview counts on the existing table header:
        # category coverage is a subtle hint near the title; active/inactive
        # totals sit directly inside the legend pills. No overview card.
        active_total = sum(bool(row["Guideline_Is_Active"]) for row in rows)
        inactive_total = len(rows) - active_total
        category_total = len({row["Category_Id"] for row in rows if row["Guideline_Is_Active"]})
        heading_col, legend_col = st.columns([2.2, 2.0], gap="small", vertical_alignment="center")
        with heading_col:
            st.html(
                '<div class="gw-panel-heading-row">'
                '<div class="gw-panel-heading">Pricing Guidelines</div>'
                f'<span class="gw-covered-count">{category_total:,} categories covered</span>'
                '</div>'
            )
        with legend_col:
            st.html(
                '<div class="gw-status-legend">'
                f'<span class="gw-legend gw-legend-active">● Active Guidelines <strong>{active_total:,}</strong></span>'
                f'<span class="gw-legend gw-legend-inactive">● Inactive Guidelines <strong>{inactive_total:,}</strong></span>'
                '</div>'
            )
        st.html('''<div class="gw-panel-sub">Select a row to edit. Click the column headings
                  below to build prioritized sorts. Choose values to filter each column.</div>''')

        sorts = list(st.session_state.get('gw_guideline_sorts', []))
        headers = st.columns([.73, 1.25, 1.25, 1.25, 1.1, 1.2], gap="small")
        for column, (label, _, key) in zip(headers, COLUMNS):
            chosen = next(((index, direction) for index, (name, direction)
                           in enumerate(sorts, start=1) if name == key), None)
            suffix = '' if chosen is None else f"  {chosen[0]}{'↑' if chosen[1] == 'asc' else '↓'}"
            with column:
                if st.button(label + suffix, key=f'gw_sort_{key}',
                             help=f'Sort by {label}. Click again for descending; a third time removes it.',
                             use_container_width=True):
                    st.session_state['gw_guideline_sorts'] = next_sort(sorts, key)
                    st.rerun()

        # OR within one field; AND across different fields.
        # First row: Status and No. each occupy HALF the space that
        # No. previously occupied, followed by Item Type and Category.
        # Second row: Brand Tier, Condition and Suggested Price use the
        # same full-width three-column layout as before.
        selected = {}
        fields = {key: label for label, _, key in COLUMNS}

        def pick_values(key):
            return st.multiselect(
                fields[key], options=filter_options(rows, key),
                key=f'gw_filter_{key}',
                label_visibility='collapsed', placeholder=fields[key],
            )

        top_filters = st.columns([0.5, 0.5, 1, 1], gap='small')
        with top_filters[0]:
            status_filter = st.selectbox(
                'Status', ['All', 'Active', 'Inactive'],
                key='gw_filter_status', label_visibility='collapsed',
                format_func=lambda value: 'Status' if value == 'All' else value,
            )
        with top_filters[1]:
            selected['id'] = pick_values('id')
        with top_filters[2]:
            selected['type'] = pick_values('type')
        with top_filters[3]:
            selected['category'] = pick_values('category')

        bottom_filters = st.columns(3, gap='small')
        with bottom_filters[0]:
            selected['brand'] = pick_values('brand')
        with bottom_filters[1]:
            selected['condition'] = pick_values('condition')
        with bottom_filters[2]:
            selected['price'] = pick_values('price')

        visible = sort_rows(filter_rows(rows, selected, status=status_filter), sorts)
        count_active = sum(bool(row['Guideline_Is_Active']) for row in visible)
        st.caption(f'{len(visible)} of {len(rows)} guidelines · '
                   f'{count_active} active · {len(visible)-count_active} inactive. '
                   'Sort priority: first clicked heading = 1.')
        if not visible:
            st.info('No guidelines match the selected column values.')
            return

        # Native Streamlit row selection preserves the authenticated session.
        # No Status or Action column is displayed.
        frame = pd.DataFrame([
            {
                'No.': int(row['Guideline_Id']),
                'Item Type': display_value(row, 'type'),
                'Category': display_value(row, 'category'),
                'Brand Tier': display_value(row, 'brand'),
                'Condition': display_value(row, 'condition'),
                'Suggested Price': float(row['Guideline_Suggested_Price']),
            }
            for row in visible
        ])
        statuses = [bool(row['Guideline_Is_Active']) for row in visible]
        def color_row(table_row):
            # Preserve the previous green active palette; grey is for inactive.
            shade = '#e5f4ec' if statuses[table_row.name] else '#edf0f3'
            return [f'background-color: {shade}' for _ in table_row]

        styler = frame.style.apply(color_row, axis=1).format({'Suggested Price': '${:,.2f}'})
        # A filter/sort change creates a fresh grid so a previous position
        # can never select a different guideline after a rerun.
        grid_state = json.dumps({'filters': selected, 'status': status_filter, 'sorts': sorts}, sort_keys=True)
        grid_key = 'gw_guideline_grid_' + hashlib.sha256(grid_state.encode()).hexdigest()[:12]
        event = st.dataframe(
            styler, key=grid_key, hide_index=True,
            on_select='rerun', selection_mode='single-row',
            use_container_width=True,
            height=min(650, 42 * len(visible) + 43),
            column_config={
                'No.': st.column_config.NumberColumn('No.', format='%d', width='small'),
                'Item Type': st.column_config.TextColumn('Item Type'),
                'Category': st.column_config.TextColumn('Category'),
                'Brand Tier': st.column_config.TextColumn('Brand Tier'),
                'Condition': st.column_config.TextColumn('Condition'),
                'Suggested Price': st.column_config.NumberColumn(
                    'Suggested Price', format='$%.2f'),
            },
        )
        selection = event.selection.rows
        if not selection:
            st.session_state.pop('gw_guideline_last_selected', None)
            return
        index = int(selection[0])
        if not 0 <= index < len(visible):
            return
        selected_id = int(visible[index]['Guideline_Id'])
        selection_signature = (grid_key, selected_id)
        if st.session_state.get('gw_guideline_last_selected') != selection_signature:
            st.session_state['gw_guideline_last_selected'] = selection_signature
            _open_selected_guideline(selected_id, list(rows))


def _editor(user, lookups, rows):
    selection = st.session_state.get("gw_guideline_editor")
    if selection is None:
        with st.container(key="gw_guideline_editor_panel"):
            st.html('<div class="gw-panel-heading">Guideline Editor</div>'
                    '<div class="gw-panel-sub">Select a guideline on the left to update '
                    'its attributes, price or active status.</div>')
            st.info("Select a guideline row or click Add New Guideline to start.")
        return
    by_id = {int(r["Guideline_Id"]): r for r in rows}
    editing = selection != "new"
    if editing and selection not in by_id:
        st.warning("This guideline is no longer available.")
        st.session_state.pop("gw_guideline_editor", None)
        return
    missing = [label for _, field, _, label in LOOKUPS if not lookups[field]]
    if missing:
        st.warning("Missing lookup records: " + ", ".join(missing))
        return
    row = by_id[selection] if editing else None
    snapshot = st.session_state.get("gw_guideline_snapshot") if editing else None
    if editing and (not snapshot or snapshot["Guideline_Id"] != selection):
        snapshot = dict(row)
        st.session_state["gw_guideline_snapshot"] = snapshot
    stale = editing and snapshot_token(snapshot) != snapshot_token(row)
    with st.container(key="gw_guideline_editor_panel"):
        st.subheader(f"Update Guideline #{selection}" if editing else "Add New Guideline")
        if editing:
            st.caption("Price or attribute changes create a new version and deactivate the old guideline.")
        if stale:
            st.warning("This guideline changed. Reload it before saving.")
            if st.button("Reload selected guideline", key="gw_reload_selected"):
                st.session_state["gw_guideline_snapshot"] = dict(row)
                st.session_state["gw_guideline_epoch"] = st.session_state.get("gw_guideline_epoch", 0) + 1
                st.rerun()
        else:
            epoch = st.session_state.get("gw_guideline_epoch", 0)
            prefix = f"gw_form_{selection}_{epoch}"
            initial_row = snapshot if editing else None
            with st.form(prefix):
                attributes, price, active = _fields(lookups, prefix, initial_row, compact=True)
                save = st.form_submit_button("Save Guideline", type="primary")
            if save:
                try:
                    saved_id = save_guideline(
                        user, attributes, price, active,
                        guideline_id=selection if editing else None,
                        expected_token=snapshot_token(snapshot) if editing else None,
                    )
                except (GuidelineError, PermissionError, mysql.connector.Error, RuntimeError) as exc:
                    _error(exc)
                else:
                    _notice(f"Guideline #{saved_id} saved successfully.")

            if editing:
                target = not bool(snapshot["Guideline_Is_Active"])
                label = "Activate Guideline" if target else "Deactivate Guideline"
                if st.button(label, key=f"gw_toggle_{selection}"):
                    try:
                        set_guideline_active(user, selection, target, snapshot_token(snapshot))
                    except (GuidelineError, PermissionError, mysql.connector.Error, RuntimeError) as exc:
                        _error(exc)
                    else:
                        _notice(f"Guideline #{selection} is now {'active' if target else 'inactive'}.")
        if st.button("Cancel", key="gw_close_editor"):
            st.session_state.pop("gw_guideline_editor", None)
            st.session_state.pop("gw_guideline_snapshot", None)
            st.rerun()




def show_guideline_management(user, show_header: bool = True):
    require_user_type(user, {"Administrator"})
    load_styles("guidelines.css")
    if show_header:
        _brand_header()

    if message := st.session_state.pop("gw_guideline_notice", None):
        st.success(message)

    try:
        lookups, rows = load_catalog(user)
    except (mysql.connector.Error, RuntimeError, PermissionError, GuidelineError) as exc:
        _error(exc)
        return

    with st.container(key="gw_guideline_content"):
        st.html('''
        <div class="gw-eyebrow">CORPORATE ADMIN</div>
        <div class="gw-page-title">Guideline management</div>
        <div class="gw-page-subtitle">Consistent pricing starts with clear, up-to-date guidelines.</div>
        ''')

        active = [r for r in rows if r["Guideline_Is_Active"]]
        counts = Counter(tuple(r[f] for f in ATTRIBUTE_FIELDS) for r in active)
        duplicates = sum(n > 1 for n in counts.values())

        # The listing remains the primary work area; its title now contains
        # the compact summary, without a separate overview card.
        left, right = st.columns([3.1, 1.2], gap="medium", vertical_alignment="top")
        with left:
            if duplicates:
                st.warning(
                    f"{duplicates} attribute combination(s) have multiple active guidelines. "
                    "Resolve them before item matching."
                )
            _table(rows)
        with right:
            if st.button("＋  Add New Guideline", key="gw_add_guideline", type="primary", use_container_width=True):
                st.session_state["gw_guideline_editor"] = "new"
                st.session_state.pop("gw_guideline_snapshot", None)
                st.rerun()
            with st.container(key="gw_guideline_editor_slot"):
                _editor(user, lookups, rows)

        st.html('''<div class="gw-page-footer"><span>© 2026 Goodwill. All rights reserved.</span>
        <span>Goodwill Pricing Decision Support System</span></div>''')
