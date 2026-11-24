"""Goodwill role-based dashboards and lightweight read-only history pages.

Usage:
    from dashboard_page import render_nav_header, show_admin_dashboard, show_staff_dashboard

Do not call st.set_page_config() here.  The existing app.py remains the entrypoint.
"""
from __future__ import annotations

from datetime import datetime
from html import escape
import logging
from typing import Callable, Mapping

import streamlit as st

from style_loader import load_styles

from dashboard_data import (
    DashboardPermissionError, UNKNOWN_BRANCH,
    as_money,
    get_admin_snapshot,
    get_history,
    get_history_filter_options,
    get_filtered_history,
    get_reprint_receipt,
    get_staff_snapshot,
)

LOG = logging.getLogger(__name__)
NAVY = "#18364f"
TEAL = "#315E7D"



def _html(markup: str) -> None:
    st.markdown(markup, unsafe_allow_html=True)


def _safe(value: object) -> str:
    return escape(str(value if value is not None else ""), quote=True)


def _time_label(value: object) -> str:
    if isinstance(value, datetime):
        return value.strftime("%b %d, %Y · %I:%M %p")
    return _safe(value)


def _go(on_navigate: Callable[[str], None], page: str) -> None:
    on_navigate(page)


def render_nav_header(user: Mapping, on_navigate: Callable[[str], None],
                      on_logout: Callable[[], None], current_page: str) -> None:
    load_styles("navigation.css", "dashboard.css")
    admin = user.get("user_type") == "Administrator"
    home = "admin_dashboard" if admin else "staff_dashboard"
    with st.container(key="gw_dashboard_nav"):
        # Equal-width tab columns keep label centers and gaps consistent.
        # The shorter staff menu has three tabs instead of an empty fourth slot.
        nav_tabs = (
            [
                ("Admin Dashboard", "admin_dashboard", "gw_nav_admin"),
                ("Guidelines", "guidelines", "gw_nav_guidelines"),
                ("Reports", "reports", "gw_nav_reports"),
                ("Users", "users", "gw_nav_users"),
                ("Configuration", "configuration", "gw_nav_configuration"),
            ] if admin else [
                ("Dashboard", "staff_dashboard", "gw_nav_staff"),
                ("Price an Item", "item_search", "gw_nav_pricing"),
                ("Guidelines", "staff_guidelines", "gw_nav_staff_guidelines"),
                ("History", "history", "gw_nav_history"),
            ]
        )
        if admin and user.get("can_manage_users_config") is not True:
            nav_tabs = [t for t in nav_tabs if t[1] not in {"users", "configuration"}]
        # Goodwill unified navigation spacing V1: fixed shell + equal tab slots.
        # Changing role / permission does not shift the brand, identity or logout.
        shell = st.columns([2.4, 5.9, 1.9, .78], gap="small",
                           vertical_alignment="center")
        with shell[0]:
            if st.button("goodwill", type="tertiary", key="gw_brand_home"):
                _go(on_navigate, home)
            _html('<div class="gw-nav-brand-subtitle">PRICING DECISION SUPPORT SYSTEM</div>')
        with shell[1]:
            with st.container(key="gw_nav_tabs"):
                # Equal-width columns / equal gaps for BOTH admin and staff.
                tab_columns = st.columns(len(nav_tabs), gap="small",
                                         vertical_alignment="center")
                for column, (label, destination, key) in zip(tab_columns, nav_tabs):
                    with column:
                        wrapper_key = ("gw_nav_selected" if destination == current_page
                                       else f"gw_nav_tab_{destination}")
                        with st.container(key=wrapper_key):
                            if st.button(label, type="tertiary", key=key,
                                         width="stretch"):
                                _go(on_navigate, destination)
        with shell[2]:
            role_label = "Administrator" if admin else "Store Staff"
            branch_label = "" if admin else f" · {_safe(user.get('branch_name') or 'Assigned Branch')}"
            _html(f'<div class="gw-nav-account"><strong>{_safe(user.get("full_name", ""))}'
                  f'</strong><span>{role_label}{branch_label}</span></div>')
        with shell[3]:
            if st.button("Logout", type="tertiary", key="gw_nav_logout"):
                on_logout()


def _intro(eyebrow: str, title: str, description: str) -> None:
    _html(f'<div class="gw-eyebrow">{_safe(eyebrow)}</div>'
          f'<div class="gw-page-title">{_safe(title)}</div>'
          f'<div class="gw-page-description">{_safe(description)}</div>')


def _section(title: str) -> None:
    _html(f'<div class="gw-section-title">{_safe(title)}</div>')


def _metric(title: str, number: int | str, symbol: str) -> None:
    _html(f'<div class="gw-kpi"><div class="gw-kpi-marker">{_safe(symbol)}</div>'
          f'<div class="gw-kpi-value">{_safe(number)}</div>'
          f'<div class="gw-kpi-label">{_safe(title)}</div></div>')


def _action(title: str, description: str, symbol: str, route: str,
            key: str, on_navigate: Callable[[str], None]) -> None:
    with st.container(key=f"gw_action_{key}"):
        _html(f'<div class="gw-action-head"><div class="gw-action-symbol">'
              f'{_safe(symbol)}</div><div class="gw-action-title">{_safe(title)}</div></div>'
              f'<div class="gw-action-desc">{_safe(description)}</div>')
        if st.button(f"Open {title}  →", key=f"gw_open_{key}",
                     width="stretch"):
            _go(on_navigate, route)


def _activity(rows: list[dict], *, admin: bool, history: bool = False) -> None:
    if not rows:
        message = "No pricing decisions match these filters." if history else "No pricing decisions have been saved yet."
        _html(f'<div class="gw-empty">{message}</div>')
        return
    head = ('<div class="gw-activity-row gw-activity-head">'
            '<div>Item / Tag</div><div>' + ('Store / Staff' if admin else 'Category') +
            '</div><div>Date</div><div>Final Price</div></div>')
    body = []
    for row in rows:
        description = _safe(row.get("description") or row.get("category") or "Priced item")
        tag = _safe(row.get("tag_number"))
        secondary = (
            _safe(row.get("branch_name") or "") + " · " +
            _safe((str(row.get("first_name") or "") + " " + str(row.get("last_name") or "")).strip())
            if admin else _safe(row.get("category") or "")
        )
        item_details = f'<div class="gw-activity-sub">Tag {tag}</div>'
        price_details = ''
        if history:
            if admin:
                item_details += f'<div class="gw-activity-sub">{_safe(row.get("category"))}</div>'
            else:
                item_details += f'<div class="gw-activity-sub">Store: {_safe(row.get("branch_name") or "Unknown")}</div>'
            if row.get("override_reason"):
                item_details += (
                    f'<div class="gw-activity-sub">Override reason: {_safe(row["override_reason"])}</div>'
                )
            price_details = (
                f'<div class="gw-activity-sub">Suggested {as_money(row["suggested_price"])}</div>'
            )
        body.append(
            '<div class="gw-activity-row">'
            f'<div><div class="gw-activity-main">{description}</div>'
            f'{item_details}</div>'
            f'<div class="gw-activity-cell">{secondary}</div>'
            f'<div class="gw-activity-cell">{_time_label(row.get("decision_time"))}</div>'
            f'<div class="gw-activity-price">{as_money(row.get("final_price"))}{price_details}</div>'
            '</div>'
        )
    _html('<div class="gw-activity">' + head + ''.join(body) + '</div>')


def _footer() -> None:
    _html('<div class="gw-footer"><span>© 2026 Goodwill. All rights reserved.</span>'
          '<span>Goodwill Pricing Decision Support System</span></div>')


def _fetch_or_error(fetch, user):
    try:
        return fetch(user)
    except DashboardPermissionError as exc:
        LOG.warning("Dashboard permission check failed: %s", exc)
        st.error("You do not have permission to access this page.")
        return None
    except Exception:
        LOG.exception("Error loading Goodwill dashboard data")
        st.error("Unable to load dashboard data from MySQL. Please check the server logs.")
        return None


def show_admin_dashboard(user: Mapping, on_navigate: Callable[[str], None]) -> None:
    if user.get("user_type") != "Administrator":
        st.error("Administrator access required.")
        return
    data = _fetch_or_error(get_admin_snapshot, user)
    if data is None:
        return
    _intro("Corporate Admin", "Admin Dashboard",
           "Manage pricing guidelines, users, and store operations.")
    metrics = data["metrics"]
    cols = st.columns(4, gap="medium")
    for col, title, key, symbol in zip(
        cols,
        ["Active Guidelines", "Active Users", "Pricing Decisions", "Price Overrides"],
        ["active_guidelines", "active_users", "decisions", "overrides"],
        ["GUIDELINES", "USERS", "DECISIONS", "OVERRIDES"],
    ):
        with col:
            _metric(title, int(metrics.get(key) or 0), symbol)
    _section("Quick Actions")
    actions = [
        ("Manage Guidelines", "Create, update, and deactivate pricing guidelines.", "▤", "guidelines"),
        ("View Reports", "Explore pricing trends and overrides across stores.", "▥", "reports"),
        ("Pricing History", "Review saved decisions, stores, and tag numbers.", "◷", "history"),
    ]
    if user.get("can_manage_users_config") is True:
        actions.insert(2, ("Manage Users", "View team members and manage account access.", "♙", "users"))
    cols = st.columns(len(actions), gap="medium")
    for i, (title, desc, sym, page) in enumerate(actions):
        with cols[i]:
            _action(title, desc, sym, page, f"admin_{i+1}", on_navigate)
    _section("Recent Pricing Activity")
    _activity(data["recent"], admin=True)
    _footer()


def show_staff_dashboard(user: Mapping, on_navigate: Callable[[str], None]) -> None:
    if user.get("user_type") != "Store Staff":
        st.error("Store staff access required.")
        return
    data = _fetch_or_error(get_staff_snapshot, user)
    if data is None:
        return
    first = str(user.get("full_name") or "").split(" ")[0]
    branch = str(user.get("branch_name") or "your store")
    _intro("Store Operations", f"Welcome back{', ' + first if first else ''}!",
           f"{branch} · Your pricing workspace")
    with st.container(key="gw_staff_hero"):
        _html('<div class="gw-pricing-hero"><div class="gw-eyebrow">PRIMARY ACTION</div>'
              '<div class="gw-hero-title">Start New Pricing</div>'
              '<div class="gw-hero-desc">Enter item details, check the recommended price, '
              'make the final decision, then generate a printable tag.</div>'
              '<div class="gw-steps">' + ''.join(
                  f'<div class="gw-step"><div class="gw-step-dot">{i}</div>'
                  f'<div class="gw-step-label">{label}</div></div>'
                  for i, label in enumerate(
                      ["Item Details", "Recommendation", "Final Decision", "Print Tag"], start=1)
              ) + '</div></div>')
        if st.button("Start Pricing  →", key="gw_staff_start", type="primary",
                     width="stretch"):
            _go(on_navigate, "item_search")
    _section("Your Activity")
    metrics = data["metrics"]
    cols = st.columns(3, gap="medium")
    for col, title, key in zip(
        cols,
        ["Items Priced", "Priced Today", "Price Overrides"],
        ["my_decisions", "today_decisions", "my_overrides"],
    ):
        with col:
            _metric(title, int(metrics.get(key, 0)), "MY ACTIVITY")
    _section("Quick Access")
    guide_col, history_col, reprint_col = st.columns(3, gap="medium")
    with guide_col:
        _action("Browse Guidelines", "Learn active suggested prices before pricing.",
                "▤", "staff_guidelines", "staff_guidelines", on_navigate)
    with history_col:
        _action("Pricing History", "Review items and tags you previously submitted.",
                "◷", "history", "staff_history", on_navigate)
    with reprint_col:
        _action("Reprint Tag", "Find an existing Tag ID and print the same label again.",
                "▤", "reprint", "staff_reprint", on_navigate)
    if user.get("can_manage_users_config") is True:
        _section("Management Access")
        with st.container(key="gw_action_staff_users"):
            _action("Manage Users", "Manage authorized accounts and branch assignments.",
                    "♙", "users", "staff_users", on_navigate)
    _section("Recent Pricing Decisions")
    _activity(data["recent"], admin=False)
    _footer()


def show_pricing_history(user: Mapping, on_navigate: Callable[[str], None]) -> None:
    """Shared read-only history. All filter predicates execute before SQL LIMIT."""
    from datetime import date

    role = user.get('user_type')
    admin = role == 'Administrator'
    _intro('Pricing Records', 'Pricing History',
           'Search all saved decisions.' if admin else
           'Search pricing decisions submitted by your account.')
    if role not in ('Administrator', 'Store Staff'):
        st.error('You do not have permission to view these records.')
        return
    try:
        options = get_history_filter_options(user)
    except DashboardPermissionError:
        st.error('You do not have permission to view these records.')
        return
    except Exception:
        LOG.exception('History filter options failed')
        st.error('Unable to load history filters from MySQL.')
        return

    prefix = 'gw_history_admin' if admin else 'gw_history_staff'
    search_col, period_col, price_col = st.columns([2, 1, 1], gap='small')
    with search_col:
        term = st.text_input('Tag ID / item description',
                             key=f'{prefix}_search', max_chars=100,
                             placeholder='Search pricing history')
    with period_col:
        period = st.selectbox('Date',
            ['All time', 'Last 7 days', 'Last 30 days', 'Last 90 days', 'Custom range'],
            key=f'{prefix}_date')
    with price_col:
        change = st.selectbox('Price Change',
            ['All', 'Override', 'No Override'], key=f'{prefix}_change')

    category_options = [None] + [int(item['id']) for item in options['categories']]
    category_names = {int(item['id']): str(item['name']) for item in options['categories']}
    if admin:
        cat_col, branch_col, staff_col = st.columns(3, gap='small')
    else:
        cat_col = st.container()
    with cat_col:
        category_id = st.selectbox('Category', category_options,
            format_func=lambda x: 'All categories' if x is None else
                f'{category_names[x]} (#{x})', key=f'{prefix}_category')
    branch_id = staff_id = None
    if admin:
        branch_options = [None] + [int(item['id']) for item in options['branches']]
        branch_names = {int(item['id']): str(item['name']) for item in options['branches']}
        branch_options.append(UNKNOWN_BRANCH)
        with branch_col:
            branch_id = st.selectbox('Branch', branch_options,
                format_func=lambda x: 'All branches' if x is None else
                    'Unknown' if x == UNKNOWN_BRANCH else f'{branch_names[x]} (#{x})', key=f'{prefix}_branch')
        staff_options = [None] + [int(item['id']) for item in options['staff']]
        staff_names = {int(item['id']):
                       f"{item.get('first_name', '')} {item.get('last_name', '')}".strip()
                       for item in options['staff']}
        with staff_col:
            staff_id = st.selectbox('Staff', staff_options,
                format_func=lambda x: 'All staff' if x is None else
                    f'{staff_names[x]} (#{x})', key=f'{prefix}_staff')
    start_date = end_date = None
    if period == 'Custom range':
        start_col, end_col = st.columns(2, gap='small')
        with start_col:
            start_date = st.date_input('From', value=date.today().replace(day=1),
                                       key=f'{prefix}_start')
        with end_col:
            end_date = st.date_input('Through', value=date.today(),
                                     key=f'{prefix}_end')
    preset = {'Last 7 days': 7, 'Last 30 days': 30, 'Last 90 days': 90}
    def _reset_history_filters():
        # Streamlit callbacks run before the next render; clearing widget keys
        # after widget creation in the same run can raise an API exception.
        for field in ('search', 'date', 'change', 'category', 'branch', 'staff', 'start', 'end'):
            st.session_state.pop(f'{prefix}_{field}', None)

    st.button('Reset history filters', key=f'{prefix}_reset',
              on_click=_reset_history_filters)

    try:
        rows = get_filtered_history(
            user, keyword=term, days=preset.get(period),
            start_date=start_date, end_date=end_date,
            category_id=category_id, branch_id=branch_id,
            staff_id=staff_id, price_change=change, limit=200)
    except DashboardPermissionError:
        st.error('You do not have permission to view these records.')
        return
    except ValueError as exc:
        st.warning(str(exc))
        return
    except Exception:
        LOG.exception('History query failed')
        st.error('Unable to load pricing history from MySQL.')
        return
    st.caption(f'{len(rows)} matching decisions shown (maximum 200 per query). '
               'Filters search the full database history before limiting results.')
    if admin:
        st.caption('Branch is recorded at pricing time. Older records may show Unknown.')
    else:
        st.caption('Only pricing decisions submitted by your account are accessible.')
    _activity(rows, admin=admin, history=True)
    if not admin and st.button('Reprint an Existing Tag', key='gw_history_reprint'):
        _go(on_navigate, 'reprint')
    _footer()



def show_reprint_tag(user: Mapping, on_navigate: Callable[[str], None]) -> None:
    if user.get("user_type") != "Store Staff":
        st.error("Store staff access required.")
        return
    _intro("Store Operations", "Reprint Tag",
           "Find a previously saved tag. Reprinting will not create a new pricing decision.")
    tag = st.text_input("Existing Tag ID", max_chars=20, key="gw_reprint_tag_input",
                        placeholder="Enter the Tag ID from your pricing history")
    if st.button("Find Tag", key="gw_lookup_tag", type="primary"):
        st.session_state["gw_reprint_search"] = tag.strip()
    selected_tag = st.session_state.get("gw_reprint_search", "")
    if not selected_tag:
        return
    try:
        receipt = get_reprint_receipt(user, selected_tag)
    except DashboardPermissionError:
        st.error("You do not have permission to reprint this tag.")
        return
    except Exception:
        LOG.exception("Error looking up existing tag")
        st.error("Unable to look up the tag in MySQL.")
        return
    if receipt is None:
        st.warning("No matching Tag ID was found in your pricing decisions.")
        return
    st.success("Saved tag found. No new record has been created.")
    c1, c2 = st.columns(2)
    with c1:
        st.write(f"**Tag ID:** {receipt['tag_number']}")
        st.write(f"**Item:** {receipt['description']}")
        st.write(f"**Category:** {receipt['category']}")
    with c2:
        st.write(f"**Final price:** {as_money(receipt['final_price'])}")
        st.write(f"**Brand / Condition:** {receipt['brand']} / {receipt['condition']}")
        st.write(f"**Store:** {receipt['branch_name']}")
    try:
        from pricing_decision import printable_label_pdf
        pdf = printable_label_pdf(receipt)
    except Exception:
        LOG.exception("Unable to generate PDF from saved receipt")
        st.error("Unable to generate the printable PDF from this saved tag.")
        return
    st.download_button("Download Printable Tag PDF", data=pdf,
                       file_name=f"goodwill_{receipt['tag_number']}.pdf",
                       mime="application/pdf", type="primary",
                       key="gw_reprint_pdf")
    st.caption("Open the downloaded PDF and choose Print.")
    _footer()


def show_unavailable_page(page: str, on_navigate: Callable[[str], None]) -> None:
    labels = {
        "users": ("Corporate Admin", "User Management", "The user list and Add New User backend are not connected in this dashboard package yet."),
        "configuration": ("Corporate Admin", "Configuration", "Master data management will be developed in the Configuration module."),
    }
    eyebrow, title, description = labels.get(page, ("Goodwill", "Not Available", "This page has not been connected."))
    _intro(eyebrow, title, description)
    st.info("This page is not yet implemented. The dashboard link is ready for the corresponding module.")
    if st.button("← Back to Admin Dashboard", key=f"gw_placeholder_back_{page}"):
        _go(on_navigate, "admin_dashboard")
    _footer()
