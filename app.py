
"""Goodwill Pricing Management System — authentication, admin & staff pricing."""

import logging
import mysql.connector
import streamlit as st

from auth import authenticate, get_active_user
from dashboard_router import render_authenticated_app
from style_loader import load_styles
from password_recovery import (
    request_password_reset, complete_password_reset,
    change_temporary_password, PasswordRecoveryError,
)


st.set_page_config(
    page_title="Goodwill Pricing",
    page_icon="🏷️",
    layout="wide",
    initial_sidebar_state="collapsed"
)

logger = logging.getLogger(__name__)

# The Dashboard Router owns authenticated navigation. CSS loads by page.

def show_login():
    # Load login-page styling
    load_styles("login.css")

    # Top navigation
    st.html("""
    <div class="gw-navbar">
        <div class="gw-brand">
            <div class="gw-logo">g</div>
            <div>
                <div class="gw-brand-name">goodwill</div>
                <div class="gw-brand-subtitle">
                    PRICING DECISION SUPPORT SYSTEM
                </div>
            </div>
        </div>
        <div class="gw-portal">STAFF & ADMIN PORTAL</div>
    </div>
    """)

    # Page content
    with st.container(horizontal_alignment="center"):

        st.html("""
        <div class="gw-tagline">
            A LITTLE CLARITY. A LOT OF GOOD.
        </div>
        """)

        # Centered login card
        with st.container(
            key="goodwill_login_card",
            width=490,
            border=False
        ):

            st.html("""
            <div class="gw-card-logo">
                <span class="gw-small-logo">g</span>
                goodwill
            </div>

            <div class="gw-welcome">
                Welcome back
            </div>

            <div class="gw-description">
                Goodwill Pricing Decision<br>
                Support System
            </div>
            """)

            # Functional login form
            with st.form(
                "login_form",
                clear_on_submit=False,
                border=False
            ):
                login_identifier = st.text_input(
                    "Email or Username",
                    placeholder="Enter your email or username",
                    max_chars=100
                )

                password = st.text_input(
                    "Password",
                    type="password"
                )

                submitted = st.form_submit_button(
                    "Login",
                    type="primary",
                    width="stretch"
                )

            # Password recovery information
            if st.button(
                "Forgot Password?",
                key="gw_go_reset",
                type="tertiary"
            ):
                st.session_state["auth_view"] = "reset"
                st.rerun()

            st.html("""
            <div class="gw-card-divider"></div>

            <div class="gw-card-note">
                For authorized Goodwill team members only.<br>
                Price with confidence. Create more opportunity.
            </div>
            """)

            # Existing authentication logic
            if submitted:
                try:
                    user = authenticate(login_identifier, password)

                except (mysql.connector.Error, RuntimeError):
                    logger.error(
                        "Login failed due to a database/configuration error"
                    )
                    st.error(
                        "Cannot connect to the database. "
                        "Please contact the system administrator."
                    )
                    return

                if user is None:
                    st.error(
                        "Invalid email, username or password, "
                        "or account is unavailable."
                    )
                    
                else:
                    # Store user ID only
                    st.session_state["user_id"] = user["user_id"]
                    st.session_state["gw_password_version"] = user.get("password_version", 0)
                    st.rerun()

        st.html("""
        <div class="gw-mission">
            Supporting our mission, one thoughtfully
            priced item at a time.
        </div>
        """)

    # Footer
    st.html("""
    <div class="gw-login-footer">
        <span>© 2026 Goodwill. All rights reserved.</span>
        <span>Goodwill Pricing Decision Support System</span>
    </div>
    """)


def show_password_reset():
    """Display the password recovery page."""

    # Load existing login page styles
    load_styles("login.css")

    # Top navigation
    st.html("""
    <div class="gw-navbar">
        <div class="gw-brand">
            <div class="gw-logo">g</div>
            <div>
                <div class="gw-brand-name">goodwill</div>
                <div class="gw-brand-subtitle">
                    PRICING DECISION SUPPORT SYSTEM
                </div>
            </div>
        </div>
        <div class="gw-portal">
            STAFF & ADMIN PORTAL
        </div>
    </div>
    """)

    with st.container(horizontal_alignment="center"):

        # Page tagline
        st.html("""
        <div class="gw-tagline">
            ACCOUNT RECOVERY
        </div>
        """)

        # Password reset card
        with st.container(
            key="goodwill_login_card",
            width=490,
            border=False
        ):

            st.html("""
            <div class="gw-card-logo">
                <span class="gw-small-logo">g</span>
                goodwill
            </div>

            <div class="gw-welcome">
                Reset Password
            </div>

            <div class="gw-description">
                Enter your registered email to receive a one-time
                password reset link (valid for 30 minutes).
            </div>
            """)

            # Email form
            with st.form(
                "password_reset_form",
                clear_on_submit=False,
                border=False
            ):

                email = st.text_input(
                    "Email",
                    placeholder="you@goodwill.org",
                    max_chars=100
                )

                submitted = st.form_submit_button(
                    "Send Reset Link",
                    type="primary",
                    width="stretch"
                )

            # Handle reset request
            if submitted:
                email = email.strip()

                if not email or "@" not in email:
                    st.error(
                        "Please enter a valid email address."
                    )
                else:
                    try:
                        request_password_reset(email)
                    except Exception:
                        logger.exception('Password recovery request could not be processed')
                    # Same response for known/unknown/inactive accounts.
                    st.success('If an active account uses that email, a one-time password reset link will be sent. Check your inbox and spam folder.')

            # Back to login navigation
            if st.button(
                "← Back to Login",
                key="gw_reset_back",
                type="tertiary",
                width="stretch"
            ):
                st.session_state["auth_view"] = "login"
                st.rerun()

            st.html("""
            <div class="gw-reset-note">
                For password recovery assistance,
                please contact your system administrator.
            </div>
            """)

        # Mission statement
        st.html("""
        <div class="gw-mission">
            Supporting our mission, one thoughtfully
            priced item at a time.
        </div>
        """)

    # Footer
    st.html("""
    <div class="gw-login-footer">
        <span>© 2026 Goodwill. All rights reserved.</span>
        <span>Goodwill Pricing Decision Support System</span>
    </div>
    """)






def show_link_password_reset(raw_token: str):
    """Password setup/change screen opened from an emailed one-time URL."""
    load_styles('login.css')
    st.html('''<div class="gw-navbar"><div class="gw-brand"><div class="gw-logo">g</div><div>
      <div class="gw-brand-name">goodwill</div><div class="gw-brand-subtitle">PRICING DECISION SUPPORT SYSTEM</div>
    </div></div><div class="gw-portal">SECURE ACCOUNT SETUP</div></div>''')
    with st.container(horizontal_alignment='center'):
        with st.container(key='goodwill_login_card', width=490, border=False):
            st.subheader('Choose Your Password')
            st.caption('Your link expires after one use. For your protection, do not forward it.')
            with st.form('gw_one_time_password_form'):
                p1 = st.text_input('New Password', type='password', key='gw_link_new_password')
                p2 = st.text_input('Confirm New Password', type='password', key='gw_link_confirm_password')
                submitted = st.form_submit_button('Save New Password', type='primary', width="stretch")
            if submitted:
                if p1 != p2:
                    st.error('Passwords do not match.')
                else:
                    try:
                        complete_password_reset(raw_token, p1)
                    except PasswordRecoveryError as exc:
                        st.error(str(exc))
                    except Exception:
                        logger.exception('One-time password change failed')
                        st.error('Unable to update password. Try again or request a new link.')
                    else:
                        st.query_params.clear()
                        st.session_state.clear()
                        st.session_state['gw_password_reset_success'] = True
                        st.rerun()
            if st.button('Back to Login', key='gw_link_back_login'):
                st.query_params.clear()
                st.session_state.clear()
                st.rerun()


def show_forced_password_change(user: dict):
    """Gate every application route for temporary-password login."""
    load_styles('login.css')
    st.html('''<div class="gw-navbar"><div class="gw-brand"><div class="gw-logo">g</div><div>
      <div class="gw-brand-name">goodwill</div><div class="gw-brand-subtitle">PRICING DECISION SUPPORT SYSTEM</div>
    </div></div><div class="gw-portal">FIRST LOGIN SECURITY</div></div>''')
    with st.container(horizontal_alignment='center'):
        with st.container(key='goodwill_login_card', width=490, border=False):
            st.subheader('Change Your Temporary Password')
            st.caption('You must choose a personal password before using Goodwill Pricing.')
            with st.form('gw_temporary_password_change_form'):
                old = st.text_input('Temporary Password', type='password', key='gw_temporary_current')
                new = st.text_input('New Password', type='password', key='gw_temporary_new')
                confirm = st.text_input('Confirm New Password', type='password', key='gw_temporary_confirm')
                submitted = st.form_submit_button('Change Password', type='primary', width="stretch")
            if submitted:
                if new != confirm:
                    st.error('Passwords do not match.')
                else:
                    try:
                        change_temporary_password(user['user_id'], old, new)
                    except PasswordRecoveryError as exc:
                        st.error(str(exc))
                    except Exception:
                        logger.exception('First-login password update failed')
                        st.error('Unable to update password. Please try again.')
                    else:
                        st.session_state.clear()
                        st.session_state['gw_password_reset_success'] = True
                        st.rerun()
            if st.button('Logout', key='gw_first_login_logout'):
                st.session_state.clear()
                st.rerun()



def main():
    load_styles("global.css")
    # The emailed token is a browser URL parameter, never a session secret.
    raw_token = st.query_params.get('reset_token')
    if raw_token:
        show_link_password_reset(raw_token)
        return
    if st.session_state.pop('gw_password_reset_success', False):
        st.success('Password updated. Please sign in with your new password.')
    if "user_id" not in st.session_state:

        auth_view = st.session_state.get(
            "auth_view", "login"
        )

        if auth_view == "reset":
            show_password_reset()
        else:
            show_login()

        return
        
    try:
        # Recheck account status and user type on every rerun.
        user = get_active_user(
            int(st.session_state["user_id"])
        )

    except (
        mysql.connector.Error,
        RuntimeError,
        ValueError,
        TypeError
    ):
        logger.error("Could not refresh authenticated user")
        st.error(
            "Cannot verify your session. Please try again later."
        )
        return

    if (user is not None and
            st.session_state.get('gw_password_version') != user.get('password_version', 0)):
        st.session_state.clear()
        st.warning('Your account was updated. Please log in again.')
        show_login()
        return

    if user is None:
        st.session_state.clear()
        st.warning(
            "Your session is no longer valid. Please log in again."
        )
        show_login()
        return
    if user.get('must_change_password'):
        show_forced_password_change(user)
        return
    render_authenticated_app(user)


if __name__ == "__main__":
    main()
