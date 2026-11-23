"""Goodwill master-data Configuration workspace, accessible only to authorized Administrators."""
from __future__ import annotations

from html import escape
from hashlib import sha256
import logging

import pandas as pd
import streamlit as st

from configuration_service import (
    SPECS, ConfigurationError, create_master_data, load_audit, load_master_data,
    set_master_active, snapshot_for_row, update_master_data,
)
from style_loader import load_styles
from users_service import has_management_access, UserManagementPermissionError

LOG = logging.getLogger(__name__)
SINGULAR = {
    'item_types': 'Item Type', 'categories': 'Category', 'brands': 'Brand Tier',
    'conditions': 'Item Condition', 'branches': 'Branch',
}


def _notice(text):
    st.session_state['gw_config_notice'] = text
    st.rerun()


def _report_error(exc):
    if isinstance(exc, (ConfigurationError, UserManagementPermissionError, PermissionError)):
        st.error(str(exc))
    else:
        LOG.exception('Configuration operation failed')
        st.error('Unable to complete the configuration request. Check migration 003 and server logs.')


def _editing_fields(spec, row: dict, prefix: str, item_types: list[dict] | None = None) -> dict:
    values = {spec.name: st.text_input('Name', value=str(row.get(spec.name) or ''),
                                      max_chars=100 if spec.kind == 'branches' else 50,
                                      key=f'{prefix}_name')}
    if spec.kind == 'categories':
        parent_rows = item_types or []
        candidates = [r for r in parent_rows if r.get('Item_Type_Is_Active')]
        current = row.get('Item_Type_Id')
        if current is not None and all(r['Item_Type_Id'] != current for r in candidates):
            candidates = [r for r in parent_rows if r['Item_Type_Id'] == current] + candidates
        ids = [r['Item_Type_Id'] for r in candidates]
        display = {r['Item_Type_Id']: r['Item_Type_Name'] for r in candidates}
        if not ids:
            raise ConfigurationError('No active Item Types exist. Create one first.')
        values['Item_Type_Id'] = st.selectbox(
            'Item Type', ids, index=ids.index(current) if current in ids else 0,
            format_func=display.get, key=f'{prefix}_item_type')
    if spec.kind == 'branches':
        values.update({
            'Branch_Street': st.text_input('Street address', value=str(row.get('Branch_Street') or ''),
                                           key=f'{prefix}_street', max_chars=100),
            'Branch_City': st.text_input('City', value=str(row.get('Branch_City') or ''),
                                         key=f'{prefix}_city', max_chars=50),
            'Branch_State': st.text_input('State (2 letters)', value=str(row.get('Branch_State') or ''),
                                          key=f'{prefix}_state', max_chars=2),
            'Branch_Zip': st.text_input('ZIP code', value=str(row.get('Branch_Zip') or ''),
                                        key=f'{prefix}_zip', max_chars=10),
            'Branch_Description': st.text_area('Description (optional)',
                value=str(row.get('Branch_Description') or ''),
                max_chars=255, key=f'{prefix}_description'),
        })
    else:
        for col in spec.detail:
            values[col] = st.text_area('Description (optional)', value=str(row.get(col) or ''),
                                       max_chars=255, key=f'{prefix}_description')
    return values


def _show_section(user: dict, kind: str) -> None:
    spec = SPECS[kind]
    try:
        rows = load_master_data(user, kind)
    except Exception as exc:
        _report_error(exc)
        return

    parent_rows = []
    if kind == 'categories':
        try:
            parent_rows = load_master_data(user, 'item_types')
        except Exception as exc:
            _report_error(exc)
            return
    all_count = len(rows)
    active_count = sum(bool(r[spec.active]) for r in rows)
    st.markdown(
        f'<div class="gw-config-heading">{escape(spec.label)}'
        f' <span class="gw-config-count">{active_count} active · {all_count - active_count} inactive</span></div>',
        unsafe_allow_html=True,
    )
    st.caption('Master data controls available choices in pricing and account management. '
               'Old IDs and historical references are never deleted.')
    if kind == 'categories':
        search, type_filter, status = st.columns([1.8, 1.25, .85])
    else:
        search, status = st.columns([2.3, 1])
    chosen_type = None
    if kind == 'categories':
        with type_filter:
            ids = [r['Item_Type_Id'] for r in parent_rows]
            names = {r['Item_Type_Id']: r['Item_Type_Name'] for r in parent_rows}
            chosen_type = st.selectbox('Item Type', [None, *ids],
                                       format_func=lambda k: 'All Item Types' if k is None else names.get(k, str(k)),
                                       key='gw_config_category_type_filter')
    with search:
        query = st.text_input('Search', key=f'gw_config_find_{kind}',
                              placeholder=f'Search {spec.label.lower()} by name or ID')
    with status:
        chosen = st.selectbox('Status', ['All', 'Active', 'Inactive'],
                              key=f'gw_config_status_{kind}')
    needle = query.strip().casefold()
    visible = [r for r in rows if
               (not needle or needle in str(r.get(spec.name, '')).casefold() or
                needle in str(r.get('Item_Type_Name', '')).casefold() or
                needle in str(r.get({'item_types': 'Item_Type_Description',
                                    'categories': 'Category_Description',
                                    'brands': 'Brand_Description',
                                    'conditions': 'Condition_Description',
                                    'branches': 'Branch_Description'}[kind], '')).casefold() or
                needle == str(r[spec.key])) and
               (chosen == 'All' or bool(r[spec.active]) == (chosen == 'Active')) and
               (kind != 'categories' or chosen_type is None or r['Item_Type_Id'] == chosen_type)]

    left, right = st.columns([1.85, 1], gap='large')
    with left:
        with st.container(key=f'gw_config_directory_{kind}'):
            if visible:
                records = []
                for row in visible:
                    entry = {'No.': row[spec.key], 'Name': row[spec.name]}
                    if kind == 'categories':
                        entry['Item Type'] = row.get('Item_Type_Name', '')
                    description_col = {
                        'item_types': 'Item_Type_Description',
                        'categories': 'Category_Description',
                        'brands': 'Brand_Description',
                        'conditions': 'Condition_Description',
                        'branches': 'Branch_Description',
                    }[kind]
                    entry['Description'] = str(row.get(description_col) or '')
                    entry['Status'] = 'Active' if row[spec.active] else 'Inactive'
                    if kind == 'item_types':
                        entry['Categories'] = row.get('category_count', 0)
                    if kind == 'branches':
                        entry.update({'City': row['Branch_City'], 'State': row['Branch_State'],
                                      'Staff': row.get('staff_count', 0)})
                    else:
                        entry.update({'Guidelines': row.get('guideline_count', 0),
                                      'Items': row.get('item_count', 0)})
                    records.append(entry)
                frame = pd.DataFrame(records)
                # A new selection key prevents stale row indices being applied
                # to a different result after filters change.
                fingerprint = sha256(','.join(str(r[spec.key]) for r in visible).encode()).hexdigest()[:12]
                selection = st.dataframe(frame, width='stretch', hide_index=True,
                                         on_select='rerun', selection_mode='single-row',
                                         key=f'gw_config_grid_{kind}_{fingerprint}', height=360)
                indices = getattr(getattr(selection, 'selection', None), 'rows', ())
                selected_id = visible[indices[0]][spec.key] if indices and indices[0] < len(visible) else None
            else:
                st.info('No matching master-data entries.')
                selected_id = None
            st.caption('Select a row to edit. Status changes and updates are logged.')

    with right:
        with st.container(key=f'gw_config_editor_{kind}'):
            st.markdown('<div class="gw-config-editor-title">Master Data Editor</div>',
                        unsafe_allow_html=True)
            if st.button(f'+ Add {SINGULAR[kind]}', key=f'gw_config_add_{kind}',
                         width='stretch'):
                st.session_state[f'gw_config_add_mode_{kind}'] = True
            add_mode = bool(st.session_state.get(f'gw_config_add_mode_{kind}', False))
            selected = next((r for r in rows if r[spec.key] == selected_id), None)
            if not add_mode and selected is None:
                st.info('Select a row or click Add to create a new entry.')
                return
            row = {} if add_mode else selected
            revision = 'new' if add_mode else f'{row[spec.key]}_{snapshot_for_row(kind, row)[:12]}'
            prefix = f'gw_config_{kind}_{revision}'
            st.markdown(f'**{"Add New" if add_mode else "Edit"} {escape(SINGULAR[kind])}**')
            if not add_mode:
                st.caption(f'ID #{row[spec.key]} · {"Active" if row[spec.active] else "Inactive"}. '
                           'Changing a name may also change how historical records are labeled.')
            with st.form(key=f'{prefix}_form'):
                fields = _editing_fields(spec, row, prefix, parent_rows)
                submit = st.form_submit_button('Create' if add_mode else 'Save Changes',
                                               type='primary', width='stretch')
            if submit:
                try:
                    if add_mode:
                        new_id = create_master_data(user, kind, fields)
                        st.session_state[f'gw_config_add_mode_{kind}'] = False
                        _notice(f'{spec.label} entry #{new_id} created.')
                    else:
                        update_master_data(user, kind, row[spec.key], fields,
                                           snapshot_for_row(kind, row))
                        _notice(f'Entry #{row[spec.key]} updated.')
                except Exception as exc:
                    _report_error(exc)
            if add_mode:
                if st.button('Cancel', key=f'{prefix}_cancel'):
                    st.session_state[f'gw_config_add_mode_{kind}'] = False
                    st.rerun()
            else:
                current_active = bool(row[spec.active])
                label = 'Deactivate entry' if current_active else 'Reactivate entry'
                with st.expander('Status management · preserve history'):
                    st.warning('Historical Items and Guidelines remain unchanged. '
                               'A value used by active guidelines cannot be deactivated. '
                               'A branch with assigned staff cannot be deactivated.')
                    confirmed = st.checkbox(f'Confirm {label.lower()}',
                                            key=f'{prefix}_confirm_status')
                    if st.button(label, disabled=not confirmed,
                                 key=f'{prefix}_toggle_status'):
                        try:
                            set_master_active(user, kind, row[spec.key],
                                              not current_active, snapshot_for_row(kind, row))
                            _notice(f'Entry #{row[spec.key]} status updated.')
                        except Exception as exc:
                            _report_error(exc)


def show_configuration(user: dict) -> None:
    """Router entrypoint; server queries recheck role and privilege every time."""
    if not has_management_access(user):
        st.error('Access denied: Configuration is for authorized Administrators only.')
        return
    load_styles('configuration.css')
    st.markdown('<div class="gw-config-eyebrow">CORPORATE ADMIN · MASTER DATA</div>'
                '<div class="gw-config-title">System Configuration</div>'
                '<div class="gw-config-subtitle">Manage product attributes and store branches.</div>',
                unsafe_allow_html=True)
    notice = st.session_state.pop('gw_config_notice', None)
    if notice:
        st.success(notice)
    labels = {spec.label: kind for kind, spec in SPECS.items()}
    choice = st.segmented_control('Configuration section',
                                  options=[*labels, 'Change History'],
                                  default='Item Types', key='gw_config_section')
    if choice in labels:
        _show_section(user, labels[choice])
    elif choice == 'Change History':
        st.markdown('#### Recent configuration changes')
        try:
            history = load_audit(user)
            if history:
                st.dataframe(pd.DataFrame(history), width='stretch', hide_index=True)
            else:
                st.info('No configuration changes have been recorded yet.')
        except Exception as exc:
            _report_error(exc)
