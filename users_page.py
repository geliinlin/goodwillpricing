"""Goodwill Users page for authorized Administrators only."""
from __future__ import annotations

from html import escape
import logging
from hashlib import sha256

import pandas as pd
import streamlit as st

from style_loader import load_styles
from users_service import (
    UserManagementError, UserManagementPermissionError, has_management_access,
    load_user_directory, create_and_invite_user, set_account_active,
    set_management_access, update_user_profile,
    find_account_duplicates,
    ROLE_ADMIN, ROLE_STAFF,
)

from password_recovery import send_admin_reset_link

LOG = logging.getLogger(__name__)


def _html(text):
    st.markdown(text, unsafe_allow_html=True)


def _safe(text):
    return escape(str(text if text is not None else ''), quote=True)


def _issue(exc):
    if isinstance(exc, (UserManagementError, UserManagementPermissionError)):
        st.error(str(exc))
    else:
        LOG.exception('User management failed')
        st.error('Unable to complete the account request. Check database migration and server logs.')


def _intro(title, desc):
    _html('<div class="gw-users-eyebrow">CORPORATE ADMIN · ACCESS MANAGEMENT</div>'
          f'<div class="gw-users-title">{_safe(title)}</div>'
          f'<div class="gw-users-desc">{_safe(desc)}</div>')


def _access_overview(user_type):
    with st.container(key='gw_users_access_overview'):
        _html('<div class="gw-users-card-title">Access overview</div>'
              f'<div class="gw-users-access-label">{_safe(user_type)} permissions</div>'
              '<div class="gw-users-rule"></div>')
        if user_type == ROLE_STAFF:
            entries = [
                ('01', 'Price donated items', 'Use current pricing guidelines.'),
                ('02', 'Record price overrides', 'Choose a reason when changing the price.'),
                ('03', 'Generate item tags', 'Create printable barcodes for inventory.'),
            ]
        else:
            entries = [
                ('01', 'Manage pricing guidelines', 'Create and maintain pricing versions.'),
                ('02', 'View reports', 'Review pricing activity and trends.'),
                ('03', 'Review system dashboard', 'Monitor current operations.'),
            ]
        _html(''.join(
            '<div class="gw-users-access-row">'
            f'<div class="gw-users-access-number">{number}</div>'
            f'<div><strong>{_safe(title)}</strong><small>{_safe(desc)}</small></div>'
            '</div>' for number, title, desc in entries
        ))
        _html('<div class="gw-users-access-note">Users + Configuration access can be granted '
              'only to Administrator accounts, never to Store Staff.</div>')
    with st.container(key='gw_users_security_box'):
        _html('<div class="gw-users-card-title">Secure account setup</div>'
              '<p>An invitation email includes a temporary password and a one-time '
              'password setup link. The temporary password must be changed before '
              'the user can enter the system.</p>')



def _show_duplicate_status(matches: dict, *, key_prefix: str,
                           first_name: str, last_name: str,
                           username: str, email: str) -> bool:
    """Show collision info, require explicit review only for namesakes.

    Returns True only if user can submit (all hard collisions absent and any
    namesake warning acknowledged). Keys change with input to avoid reusing
    a checkbox confirmation after someone edits identity fields.
    """
    for item in matches['blocking']:
        label = ' / '.join(item['fields'])
        status = 'Active' if item['active'] else 'Inactive'
        st.error(f"Duplicate account: {label} already belongs to User #{item['user_id']} "
                 f"({item['username']}, {status}). Select that account instead.")
    if matches['blocking']:
        return False
    if not matches['similar']:
        return True
    examples = ', '.join(
        f"#{r['user_id']} ({r['username']}, {'Active' if r['active'] else 'Inactive'})"
        for r in matches['similar'][:3])
    st.warning('Potential duplicate account: the same first and last name appears '
               f'in {examples}. Verify that this is not the same person.')
    signature = '\x1f'.join(str(x).strip().casefold() for x in
                           (first_name, last_name, username, email))
    fingerprint = sha256(signature.encode('utf-8')).hexdigest()[:16]
    return st.checkbox('I checked the existing account(s) and confirm this is a different person',
                       key=f'{key_prefix}_{fingerprint}', value=False)


def _create_form(user: dict, branches: list[dict], on_navigate, users: list[dict]):
    top, back = st.columns([4, 1], vertical_alignment='center')
    with top:
        _intro('Add New User', 'Create an account and assign access to the pricing system.')
    with back:
        if st.button('← Back to users', key='gw_users_back'):
            st.session_state['gw_users_mode'] = 'directory'
            st.rerun()

    left, right = st.columns([2.05, 1], gap='large')
    with left:
        with st.container(key='gw_users_form_card'):
            _html('<div class="gw-users-card-title">User details</div>'
                  '<div class="gw-users-card-hint">All fields required unless marked optional.</div>')
            with st.container(key='gw_users_create_fields'):
                first_col, last_col = st.columns(2)
                with first_col:
                    first = st.text_input('First Name', max_chars=50, key='gw_users_new_first')
                with last_col:
                    last = st.text_input('Last Name', max_chars=50, key='gw_users_new_last')
                username_col, email_col = st.columns([1, 1.4])
                with username_col:
                    username = st.text_input('Username', max_chars=50, key='gw_users_new_username')
                with email_col:
                    email = st.text_input('Email address', max_chars=100,
                                          key='gw_users_new_email')
                types = [ROLE_STAFF, ROLE_ADMIN]
                role = st.radio('Role', types, horizontal=True, key='gw_users_new_type')
                branch_id = None
                if role == ROLE_STAFF:
                    branch_map = {int(row['Branch_Id']): str(row['Branch_Name']) for row in branches}
                    if branch_map:
                        branch_id = st.selectbox('Branch', list(branch_map),
                                    format_func=lambda x: branch_map[x], key='gw_users_new_branch')
                    else:
                        st.warning('No branches exist. Create a Branch before adding Store Staff.')
                st.caption('Accounts are active by default. You can deactivate them later.')
                active = st.checkbox('Active on creation', value=True, key='gw_users_new_active')
                elevated = False
                if role == ROLE_ADMIN:
                    elevated = st.checkbox('Grant Users + Configuration access', value=False,
                                           key='gw_users_new_manage',
                                           help='Only Administrators may receive this permission.')
                matches = find_account_duplicates(users, first_name=first,
                    last_name=last, username=username, email=email)
                safe_to_save = _show_duplicate_status(matches,
                    key_prefix='gw_users_duplicate_create', first_name=first,
                    last_name=last, username=username, email=email)
                submitted = st.button('Create User', type='primary',
                    disabled=(not safe_to_save or
                              (role == ROLE_STAFF and branch_id is None)))
            if submitted and safe_to_save:
                try:
                    receipt = create_and_invite_user(user, first_name=first, last_name=last,
                        username=username, email=email, user_type=role,
                        branch_id=branch_id, active=active, management_access=elevated,
                        confirm_similar_name=bool(matches['similar']) and safe_to_save)
                except Exception as exc:
                    _issue(exc)
                else:
                    st.success(f"User #{receipt['user_id']} ({receipt['username']}) created successfully.")
                    if receipt['email_sent']:
                        st.success('Invitation email sent with a temporary password and one-time setup link.')
                    else:
                        st.warning('Account created, but invitation not sent. Check email configuration or account status; use Send Password Reset Email once available.')
    with right:
        _access_overview(st.session_state.get('gw_users_new_type', ROLE_STAFF))



def _profile_snapshot(account: dict) -> dict:
    """Original visible values used for optimistic concurrency."""
    return {key: account.get(key) for key in (
        'User_First_Name', 'User_Last_Name', 'User_Username', 'User_Email',
        'Branch_Id')}


def _edit_profile_panel(actor: dict, account: dict, branches: list[dict], users: list[dict]):
    uid = int(account['User_Id'])
    editing = st.session_state.get('gw_users_edit_profile_id') == uid
    if not editing:
        if st.button('Edit User Profile', key=f'gw_users_edit_start_{uid}',
                     width='stretch'):
            st.session_state['gw_users_edit_profile_id'] = uid
            st.session_state['gw_users_edit_profile_original'] = _profile_snapshot(account)
            st.session_state['gw_users_edit_profile_nonce'] = (
                st.session_state.get('gw_users_edit_profile_nonce', 0) + 1)
            st.rerun()
        return

    original = st.session_state.get('gw_users_edit_profile_original')
    if not isinstance(original, dict):
        st.warning('Select Edit User Profile again to load the current account details.')
        st.session_state.pop('gw_users_edit_profile_id', None)
        return
    nonce = st.session_state.get('gw_users_edit_profile_nonce', 0)
    with st.container(key='gw_users_profile_editor'):
        _html('<div class="gw-users-profile-heading">Edit User Profile</div>')
        st.caption('Role is read-only. Account status and management access are controlled separately.')
        first = st.text_input('First Name', value=original['User_First_Name'],
                              max_chars=50, key=f'gw_users_edit_first_{uid}_{nonce}')
        last = st.text_input('Last Name', value=original['User_Last_Name'],
                             max_chars=50, key=f'gw_users_edit_last_{uid}_{nonce}')
        username = st.text_input('Username', value=original['User_Username'],
                                 max_chars=50, key=f'gw_users_edit_username_{uid}_{nonce}')
        email = st.text_input('Email address', value=original['User_Email'],
                              max_chars=100, key=f'gw_users_edit_email_{uid}_{nonce}')
        branch_id = None
        if account['User_Type'] == ROLE_STAFF:
            branch_map = {int(b['Branch_Id']): str(b['Branch_Name']) for b in branches}
            old_branch = original.get('Branch_Id')
            options = list(branch_map)
            if old_branch not in branch_map:
                st.warning('The assigned branch is inactive or unavailable. Select an active branch.')
                options = [None, *options]
            if options and any(option is not None for option in options):
                branch_id = st.selectbox('Assigned Branch', options,
                    index=options.index(old_branch) if old_branch in options else 0,
                    format_func=lambda key: branch_map.get(key, 'Choose an active branch'),
                    key=f'gw_users_edit_branch_{uid}_{nonce}')
            else:
                st.error('No active branches available. Cannot save a Store Staff profile.')
        st.caption('Changing an email address invalidates old password-reset links and signs out existing sessions. New addresses are not automatically verified.')
        if account['User_Type'] == ROLE_ADMIN:
            st.caption('Assigned Branch: Corporate / All Branches (not editable for Administrators).')
        if account['User_Type'] == ROLE_STAFF:
            st.caption('The new branch applies to future pricing decisions.')
        matches = find_account_duplicates(users, first_name=first,
            last_name=last, username=username, email=email, exclude_user_id=uid)
        safe_to_save = _show_duplicate_status(matches,
            key_prefix=f'gw_users_duplicate_edit_{uid}', first_name=first,
            last_name=last, username=username, email=email)
        save, cancel = st.columns([1.4, 1])
        with save:
            submitted = st.button('Save Profile Changes', type='primary',
                disabled=(not safe_to_save or
                          (account['User_Type'] == ROLE_STAFF and branch_id is None)),
                key=f'gw_users_edit_save_{uid}', width='stretch')
        with cancel:
            cancelled = st.button('Cancel', key=f'gw_users_edit_cancel_{uid}',
                                  width='stretch')
        if cancelled:
            st.session_state.pop('gw_users_edit_profile_id', None)
            st.session_state.pop('gw_users_edit_profile_original', None)
            st.rerun()
        if submitted and safe_to_save:
            try:
                outcome = update_user_profile(actor, uid, first_name=first,
                    last_name=last, username=username, email=email,
                    branch_id=branch_id, expected_profile=original,
                    confirm_similar_name=bool(matches['similar']) and safe_to_save)
            except Exception as exc:
                _issue(exc)
            else:
                st.session_state.pop('gw_users_edit_profile_id', None)
                st.session_state.pop('gw_users_edit_profile_original', None)
                if outcome['email_changed'] or outcome['username_changed']:
                    st.success('Profile updated. Existing login sessions have been revoked.')
                elif outcome.get('branch_changed'):
                    st.success('Branch updated. The employee must sign in again.')
                else:
                    st.success('User profile updated.')
                st.rerun()




def _directory(user: dict, directory: dict):
    users = directory['users']
    branches = directory['branches']
    heading, add = st.columns([4, 1], vertical_alignment='center')
    with heading:
        _intro('User Management', 'Manage accounts, branch assignments, and system access.')
    with add:
        if st.button('+ Add New User', type='primary', key='gw_users_add_open',
                     width='stretch'):
            st.session_state['gw_users_mode'] = 'add'
            st.rerun()
    active_users = sum(bool(r['User_Is_Active']) for r in users)
    managers = sum(bool(r['Can_Manage_Users_Config']) for r in users)
    cols = st.columns(3, gap='medium')
    for col, number, desc in zip(cols,
            [len(users), active_users, managers],
            ['Users in your scope', 'Active accounts', 'Users + Configuration access']):
        with col:
            _html(f'<div class="gw-users-metric"><strong>{number}</strong><span>{_safe(desc)}</span></div>')
    term_col, role_col, status_col = st.columns([2, 1, 1], gap='small')
    with term_col:
        term = st.text_input('Search users', placeholder='Name, username, or email',
                              key='gw_users_search')
    with role_col:
        roles = ['All roles', ROLE_ADMIN, ROLE_STAFF]
        role_filter = st.selectbox('Role', roles, key='gw_users_filter_role')
    with status_col:
        state_filter = st.selectbox('Status', ['All statuses', 'Active', 'Inactive'],
                                    key='gw_users_filter_status')
    filtered = []
    for row in users:
        value = f"{row['User_First_Name']} {row['User_Last_Name']} {row['User_Username']} {row['User_Email']}".casefold()
        if term.strip().casefold() not in value:
            continue
        if role_filter != 'All roles' and row['User_Type'] != role_filter:
            continue
        if state_filter == 'Active' and not row['User_Is_Active']:
            continue
        if state_filter == 'Inactive' and row['User_Is_Active']:
            continue
        filtered.append(row)
    left, right = st.columns([2.05, 1], gap='large')
    with left:
        with st.container(key='gw_users_directory_card'):
            _html('<div class="gw-users-card-title">Users</div>'
                  '<div class="gw-users-card-hint">Select a row to manage the selected account.</div>')
            records = [{
                'ID': int(r['User_Id']),
                'Name': f"{r['User_First_Name']} {r['User_Last_Name']}",
                'Username': r['User_Username'],
                'Role': r['User_Type'],
                'Branch': r.get('Branch_Name') or '—',
                'Status': 'Active' if r['User_Is_Active'] else 'Inactive',
                'Management': 'Yes' if r['Can_Manage_Users_Config'] else 'No',
            } for r in filtered]
            visible_ids = ','.join(str(row['ID']) for row in records)
            grid_key = 'gw_users_table_' + sha256(visible_ids.encode()).hexdigest()[:12]
            event = st.dataframe(pd.DataFrame(records, columns=[
                'ID', 'Name', 'Username', 'Role', 'Branch', 'Status', 'Management']),
                width='stretch', hide_index=True, height=460,
                on_select='rerun', selection_mode='single-row', key=grid_key)
            selection = event.selection.rows if hasattr(event, 'selection') else []
            if selection and 0 <= selection[0] < len(filtered):
                new_selection = int(filtered[selection[0]]['User_Id'])
                if st.session_state.get('gw_users_selected_id') != new_selection:
                    st.session_state.pop('gw_users_edit_profile_id', None)
                    st.session_state.pop('gw_users_edit_profile_original', None)
                st.session_state['gw_users_selected_id'] = new_selection
            st.caption(f'{len(filtered)} of {len(users)} accounts shown')
    with right:
        selected_id = st.session_state.get('gw_users_selected_id')
        selected = next((r for r in filtered if r['User_Id'] == selected_id), None)
        if not selected:
            for key in ('gw_users_selected_id', 'gw_users_edit_profile_id', 'gw_users_edit_profile_original'):
                st.session_state.pop(key, None)
            with st.container(key='gw_users_detail_card'):
                _html('<div class="gw-users-card-title">Account details</div>')
                st.info('Select a row on the left to review or modify an account.')
            return
        with st.container(key='gw_users_detail_card'):
            _html('<div class="gw-users-card-title">Account details</div>'
                  f'<div class="gw-users-person">{_safe(selected["User_First_Name"])} '
                  f'{_safe(selected["User_Last_Name"])}</div>'
                  f'<div class="gw-users-card-hint">{_safe(selected["User_Username"])} · '
                  f'{_safe(selected["User_Email"])}</div>')
            st.write(f"**Role:** {selected['User_Type']}")
            if selected['User_Type'] == ROLE_ADMIN:
                st.write('**Branch:** Corporate / All Branches')
            else:
                st.write(f"**Branch:** {selected.get('Branch_Name') or 'Unassigned'}")
            is_self = selected['User_Id'] == user['user_id']
            _edit_profile_panel(user, selected, branches, users)
            if is_self:
                st.info('You can update your own basic profile (name, username and email). '
                        'Account status, role and management permissions cannot be changed here.')
                return
            active = st.checkbox('Active account', value=bool(selected['User_Is_Active']),
                                 key=f"gw_users_active_{selected['User_Id']}")
            if st.button('Save account status', key=f"gw_users_save_status_{selected['User_Id']}"):
                try:
                    set_account_active(user, int(selected['User_Id']), active)
                except Exception as exc:
                    _issue(exc)
                else:
                    st.success('Account status updated.')
                    st.rerun()
            if directory['can_grant_access'] and selected['User_Type'] == ROLE_ADMIN:
                permission = st.checkbox('Users + Configuration access',
                    value=bool(selected['Can_Manage_Users_Config']),
                    key=f"gw_users_perm_{selected['User_Id']}")
                if st.button('Save management access',
                        key=f"gw_users_save_perm_{selected['User_Id']}"):
                    try:
                        set_management_access(user, int(selected['User_Id']), permission)
                    except Exception as exc:
                        _issue(exc)
                    else:
                        st.success('Management access updated.')
                        st.rerun()
            if st.button('Send Password Reset Email',
                    key=f"gw_users_reset_password_{selected['User_Id']}"):
                try:
                    sent = send_admin_reset_link(user, int(selected['User_Id']))
                except Exception as exc:
                    _issue(exc)
                else:
                    if sent:
                        st.success('One-time password reset link sent to the registered email.')
                    else:
                        st.warning('Email was not sent (recipient unavailable, recent request, or SMTP error). Check server logs.')


def show_user_management(user: dict, on_navigate=None):
    """Users page. Server rechecks access via load_user_directory + each write."""
    if not has_management_access(user):
        st.error('You do not have permission to access Users.')
        return
    try:
        directory = load_user_directory(user)
    except Exception as exc:
        _issue(exc)
        return
    load_styles('users.css')
    if st.session_state.get('gw_users_mode') == 'add':
        _create_form(user, directory['branches'], on_navigate,
                     directory['users'])
    else:
        _directory(user, directory)
