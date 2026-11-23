"""Role-specific page router for the EXISTING Goodwill Streamlit app.

Integration: after app.py calls get_active_user(...), replace its sidebar-based
navigation block with:

    from dashboard_router import render_authenticated_app
    render_authenticated_app(user)

Leave the existing show_login, show_password_reset, authentication, and
st.set_page_config logic in app.py unchanged.
"""
from __future__ import annotations

import streamlit as st

from dashboard_page import (
    render_nav_header,
    show_admin_dashboard,
    show_pricing_history,
    show_reprint_tag,
    show_staff_dashboard,
    show_unavailable_page,
)

ADMIN_PAGES = frozenset({"admin_dashboard", "guidelines", "reports", "history"})
MANAGEMENT_PAGES = frozenset({"users", "configuration"})
STAFF_PAGES = frozenset({"staff_dashboard", "item_search", "staff_guidelines", "history", "reprint"})


def _homepage(user: dict) -> str:
    role = user.get("user_type")
    if role == "Administrator":
        return "admin_dashboard"
    if role == "Store Staff":
        return "staff_dashboard"
    raise PermissionError("Unsupported user role")


def _navigate(page: str) -> None:
    st.session_state["gw_page"] = page
    st.rerun()


def _logout() -> None:
    st.session_state.clear()
    st.rerun()


def render_authenticated_app(user: dict) -> None:
    """Render only routes permitted by auth.get_active_user's DB-verified role."""
    try:
        home = _homepage(user)
    except PermissionError:
        st.error("Unknown account type. Please contact your administrator.")
        return

    # A new login, even within the same browser session, starts at its own home.
    owner = st.session_state.get("gw_nav_owner_id")
    if owner != user.get("user_id"):
        st.session_state["gw_nav_owner_id"] = user["user_id"]
        st.session_state["gw_page"] = home
        st.session_state.pop("gw_reprint_search", None)
        st.session_state.pop("gw_reprint_tag_input", None)
        # Clear every report filter between accounts, including custom dates.
        for key in tuple(st.session_state):
            if key.startswith("gw_reports_"):
                st.session_state.pop(key, None)

    page = st.session_state.get("gw_page", home)
    allowed = (ADMIN_PAGES if user.get("user_type") == "Administrator" else STAFF_PAGES)
    if user.get("user_type") == "Administrator" and user.get("can_manage_users_config") is True:
        allowed = allowed | MANAGEMENT_PAGES
    # Deliberate deny-by-default route policy, including direct URL/query-string
    # attempts and stale links from a previously logged-in role.
    if page not in allowed:
        st.error("Access denied: this page is not available to your account.")
        st.session_state["gw_page"] = home
        page = home

    render_nav_header(user, _navigate, _logout, page)

    # Goodwill unified navigation spacing V1: one body frame for every route.
    with st.container(key="gw_page_body"):
        if page == "admin_dashboard":
            show_admin_dashboard(user, _navigate)
        elif page == "staff_dashboard":
            show_staff_dashboard(user, _navigate)
        elif page == "guidelines":
            from guideline_page import show_guideline_management
            # Router owns the sole authenticated header. The optional flag is
            # installed by apply_guideline_nav_fix.py; fail explicitly if missing,
            # rather than silently drawing two nav bars again.
            show_guideline_management(user, show_header=False)
        elif page == "staff_guidelines":
            from staff_guidelines_page import show_staff_guidelines
            show_staff_guidelines(user, _navigate)
        elif page == "item_search":
            from item_search_page import render_item_search_page
            # Keep the 4-stage pricing flow (including Print Tag page) unchanged;
            # avoid duplicate global header since dashboard_router owns the nav.
            render_item_search_page(show_header=False, on_logout=_logout)
        elif page == "history":
            show_pricing_history(user, _navigate)
        elif page == "reprint":
            show_reprint_tag(user, _navigate)
        elif page == "reports":
            from reports_page import show_admin_reports
            show_admin_reports(user)
        elif page == "users":
            from users_page import show_user_management
            show_user_management(user, _navigate)
        elif page == "configuration":
            from configuration_page import show_configuration
            show_configuration(user)
        else:
            st.error("This page is not available.")
