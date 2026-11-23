"""Goodwill employee pricing workspace: screenshot-inspired two-screen design.

Add to your existing Streamlit app:

    from item_search_page import render_item_search_page
    render_item_search_page(show_header=True)  # False if app.py draws the header

Session contract: app.py sets st.session_state['user_id'] upon authentication.
Existing database.py provides get_connection() (MySQL Connector/Python).
"""
from __future__ import annotations

from datetime import datetime
import base64
from decimal import Decimal
from html import escape
import logging
from zoneinfo import ZoneInfo

import streamlit as st
from style_loader import load_styles

from item_search import (
    ItemEntryError,
    get_active_price_guideline,
    get_authenticated_store_staff,
    get_lookup_options,
    validate_item_entry,
)
from pricing_decision import (
    PricingValidationError,
    PricingSaveError,
    barcode_svg,
    money,
    printable_label_pdf,
    save_pricing_decision,
)

LOG = logging.getLogger(__name__)
DRAFT_KEY = 'module3_pricing_draft'
RESULT_KEY = 'module3_match_result'
RECEIPT_KEY = 'module3_receipt'
SEARCH_ERROR_KEY = 'module3_search_error'
P = 'module3_'
TZ = ZoneInfo('America/Detroit')



def _safe(value: object) -> str:
    return escape(str(value), quote=True)


def _clear_search() -> None:
    for key in (DRAFT_KEY, RESULT_KEY, SEARCH_ERROR_KEY):
        st.session_state.pop(key, None)


def _start_next_item() -> None:
    keys = [key for key in list(st.session_state) if key.startswith(P)]
    for key in keys:
        st.session_state.pop(key, None)


def _logout() -> None:
    _start_next_item()
    for key in ('user_id', 'username', 'user_type', 'role', 'authenticated', 'logged_in', 'user'):
        st.session_state.pop(key, None)
    st.rerun()


def _type_changed() -> None:
    st.session_state.pop(f'{P}Category_Id', None)
    _clear_search()


def _select(field: str, label: str, options: dict[str, dict[int, str]],
            item_type_id: int | None = None) -> int | None:
    dictionary = options[field]
    if field == 'Category_Id':
        parents = options.get('Category_Item_Type', {})
        dictionary = {key: value for key, value in dictionary.items()
                      if item_type_id is not None and parents.get(key) == item_type_id}
    widget_key = f'{P}{field}'
    current_id = st.session_state.get(widget_key)
    if current_id is not None and current_id not in dictionary:
        st.session_state.pop(widget_key, None)
    return st.selectbox(
        label,
        options=[None, *dictionary],
        format_func=lambda val: 'Select...' if val is None else dictionary[val],
        key=widget_key,
        disabled=field == 'Category_Id' and item_type_id is None,
        on_change=_type_changed if field == 'Item_Type_Id' else _clear_search,
    )


def _header(employee: dict, show_header: bool, on_logout=None) -> None:
    if not show_header:
        return
    with st.container(key=f'{P}topbar'):
        a, b, c, d = st.columns([5, 6.85, 2.5, 1], gap='small')
        with a:
            st.markdown(
                '<div class="gw-brand"><span class="gw-logo">g</span><div>'
                '<div class="gw-name">goodwill</div>'
                '<div class="gw-tagline">Pricing Decision Support System</div>'
                '</div></div>', unsafe_allow_html=True,
            )
        with b:
            st.markdown('<div class="gw-active-tab">Item pricing</div>', unsafe_allow_html=True)
        with c:
            st.markdown(
                '<div class="gw-workspace">Pricing workspace'
                f'<strong>Store staff&nbsp; · &nbsp;Store {int(employee["Branch_Id"]):03d}</strong>'
                '</div>', unsafe_allow_html=True,
            )
        with d:
            if st.button('Logout', key=f'{P}logout', use_container_width=True):
                if on_logout is not None:
                    on_logout()
                else:
                    _logout()


def _steps(complete: bool = False) -> None:
    """Show the same three-step workflow on entry and generated-tag pages.

    On the receipt screen the tag exists, but printing has not yet happened.
    """
    if complete:
        msg = (
            '<span class="gw-step-done">01&nbsp; Item details</span>'
            '<span class="sep">—</span>'
            '<span class="gw-step-done">02&nbsp; Generate tag</span>'
            '<span class="sep">—</span>'
            '<b class="gw-step-current">03&nbsp; Print</b>'
        )
    else:
        msg = (
            '<b class="gw-step-current">01&nbsp; Item details</b>'
            '<span class="sep">—</span>'
            '02&nbsp; Generate tag'
            '<span class="sep">—</span>'
            '03&nbsp; Print'
        )
    st.markdown(f'<div class="gw-stepper">{msg}</div>', unsafe_allow_html=True)


def _search_guidelines(
    employee: dict,
    selected: tuple[int | None, int | None, int | None, int | None],
    description: str,
) -> None:
    _clear_search()
    item_type_id, category_id, brand_id, condition_id = selected
    if any(v is None for v in selected):
        st.session_state[SEARCH_ERROR_KEY] = ('warning', 'Select Item Type, Category, Brand Tier and Condition.')
        return
    today = datetime.now(TZ).date()
    try:
        validate_item_entry(description, today, today)
        match = get_active_price_guideline(item_type_id, category_id, brand_id, condition_id)
    except ItemEntryError as exc:
        st.session_state[SEARCH_ERROR_KEY] = ('warning', str(exc))
        return
    except Exception:
        LOG.error('Price matching failed')
        st.session_state[SEARCH_ERROR_KEY] = ('error', 'Could not search MySQL. Please retry or contact an administrator.')
        return

    if match.status == 'not_found':
        st.session_state[SEARCH_ERROR_KEY] = (
            'warning', 'No active guideline matches these four attributes. Ask an administrator to add one.'
        )
        return
    if match.status == 'conflict':
        st.session_state[SEARCH_ERROR_KEY] = (
            'error', f'Multiple active guidelines match (IDs: {", ".join(map(str, match.conflicting_ids))}). '
                     'Ask an administrator to resolve the conflict.'
        )
        return
    st.session_state[DRAFT_KEY] = {
        'staff_id': int(employee['Staff_Id']),
        'branch_id': int(employee['Branch_Id']),
        'item_type_id': int(item_type_id),
        'category_id': int(category_id),
        'brand_id': int(brand_id),
        'condition_id': int(condition_id),
        'item_description': description.strip(),
        'item_received_date': today.isoformat(),
        'guideline_id': int(match.guideline_id),
        'suggested_price': str(match.suggested_price),
    }
    st.session_state[RESULT_KEY] = {
        'guideline_id': int(match.guideline_id),
        'suggested_price': str(match.suggested_price),
    }
    st.session_state[f'{P}final_price'] = f'${match.suggested_price:.2f}'
    st.session_state[f'{P}reason_choice'] = 'Select a reason...'
    st.session_state.pop(f'{P}reason_other', None)


def _item_form(employee: dict, lookups: dict[str, dict[int, str]], user_id: int) -> None:
    with st.container(key=f'{P}intro'):
        headline, progress = st.columns([2, 1.2], gap='medium')
        with headline:
            st.markdown(
                '<div class="gw-eyebrow">Store operations</div>'
                '<div class="gw-page-title">Price an item</div>'
                '<div class="gw-desc">Turn donated goods into new opportunities. '
                'Start with the item details below.</div>', unsafe_allow_html=True,
            )
        with progress:
            _steps()

    with st.container(key=f'{P}cards'):
        left, right = st.columns([760, 555], gap='medium')
        with left:
            with st.container(border=True, key=f'{P}item_card'):
                st.markdown(
                    '<div class="gw-card-title">Item details</div>'
                    '<div class="gw-card-subtitle">Select item attributes to find the right guideline. Item Description is optional.</div>',
                    unsafe_allow_html=True,
                )
                description = st.text_input(
                    'Item Description (Optional)', placeholder="e.g., Levi's Jeans, size 32",
                    max_chars=255, key=f'{P}description', on_change=_clear_search,
                )
                a, b = st.columns([1, 1.9], gap='medium')
                with a:
                    item_type_id = _select('Item_Type_Id', 'Item Type', lookups)
                with b:
                    category_id = _select('Category_Id', 'Category', lookups, item_type_id)
                brand_id = _select('Brand_Id', 'Brand Tier', lookups)
                st.caption('No-label, Generic, Name brand or Designer')
                condition_id = _select('Condition_Id', 'Condition', lookups)
                st.caption('Assess wear, damage and overall quality before pricing.')
                selected = (item_type_id, category_id, brand_id, condition_id)
                draft = st.session_state.get(DRAFT_KEY)
                if draft and (
                    selected != tuple(draft[key] for key in ('item_type_id', 'category_id', 'brand_id', 'condition_id'))
                    or description.strip() != draft['item_description']
                    or employee['Staff_Id'] != draft['staff_id']
                    or employee['Branch_Id'] != draft['branch_id']
                ):
                    _clear_search()
                if st.button('Search Guidelines', type='secondary',
                             use_container_width=True, key=f'{P}search'):
                    _search_guidelines(employee, selected, description)
                    st.rerun()
                search_error = st.session_state.get(SEARCH_ERROR_KEY)
                if search_error:
                    level, message = search_error
                    (st.error if level == 'error' else st.warning)(message)
                draft = st.session_state.get(DRAFT_KEY)
                if draft and draft.get('staff_id') == int(employee['Staff_Id']):
                    match_text = ' / '.join([
                        _safe(lookups['Category_Id'].get(draft['category_id'], '')),
                        _safe(lookups['Brand_Id'].get(draft['brand_id'], '')),
                        _safe(lookups['Condition_Id'].get(draft['condition_id'], '')),
                    ])
                    st.markdown(
                        '<div class="gw-guideline-row"><span>Guideline match</span>'
                        f'<b>{match_text}</b></div>', unsafe_allow_html=True,
                    )
                else:
                    st.markdown(
                        '<div class="gw-guideline-row"><span>Guideline match</span><b>—</b></div>',
                        unsafe_allow_html=True,
                    )

        with right:
            with st.container(border=True, key=f'{P}pricing_card'):
                st.markdown('<div class="gw-card-title">Pricing decision</div>', unsafe_allow_html=True)
                draft = st.session_state.get(DRAFT_KEY)
                valid_draft = bool(draft and draft.get('staff_id') == int(employee['Staff_Id']))
                displayed_suggested = f"${money(draft['suggested_price']):,.2f}" if valid_draft else '—'
                footer = (
                    'From active pricing guideline · Read-only' if valid_draft
                    else 'Search guidelines to view a suggested price'
                )
                st.markdown(
                    '<div class="gw-suggestion">'
                    '<div class="label">Suggested Price</div>'
                    f'<div class="price">{_safe(displayed_suggested)}</div>'
                    f'<div class="note">{_safe(footer)}</div>'
                    '</div>', unsafe_allow_html=True,
                )

                # Compute difference BEFORE drawing the price row, so the override
                # badge appears alongside the label, as in the supplied screenshot.
                raw_final_price = st.session_state.get(f'{P}final_price', '') if valid_draft else ''
                price_error = None
                delta = None
                if valid_draft:
                    try:
                        delta = money(raw_final_price) - money(draft['suggested_price'])
                    except PricingValidationError as exc:
                        price_error = str(exc)
                badge = '<span class="gw-override-placeholder"></span>'
                if delta is not None and delta != 0:
                    prefix = '+' if delta > 0 else '-'
                    badge = (
                        '<span class="gw-override">'
                        f'Override · {prefix}${abs(delta):.2f}</span>'
                    )
                st.markdown(
                    '<div class="gw-price-label-row">'
                    f'<span class="gw-field-label">Final Price</span>{badge}</div>',
                    unsafe_allow_html=True,
                )
                if valid_draft:
                    raw_final_price = st.text_input(
                        'Final Price', label_visibility='collapsed',
                        key=f'{P}final_price',
                    )
                else:
                    raw_final_price = st.text_input(
                        'Final Price', value='', label_visibility='collapsed',
                        disabled=True, key=f'{P}final_price_disabled',
                    )

                st.markdown('<div class="gw-field-label">Reason for Override</div>', unsafe_allow_html=True)
                choices = [
                    'Select a reason...', 'Missing Button', 'Visible Wear',
                    'Minor Damage', 'Stains / Discoloration', 'Other',
                ]
                choice = st.selectbox(
                    'Reason for Override', choices, label_visibility='collapsed',
                    key=f'{P}reason_choice' if valid_draft else f'{P}reason_disabled',
                    disabled=not valid_draft or delta is None or delta == 0,
                )
                reason = None
                if delta is not None and delta != 0:
                    if choice == 'Other':
                        reason = st.text_input(
                            'Describe the override', max_chars=255,
                            key=f'{P}reason_other',
                        )
                    elif choice != 'Select a reason...':
                        reason = choice
                st.markdown(
                    '<div class="gw-reason-note">'
                    'Required when Final Price differs from Suggested Price.</div>'
                    '<div class="gw-submit-gap"></div>',
                    unsafe_allow_html=True,
                )
                if st.button('Submit & Generate Tag', type='primary',
                             use_container_width=True, key=f'{P}submit', disabled=not valid_draft) and valid_draft:
                    if price_error:
                        st.error(price_error)
                    elif delta is not None and delta != 0 and not reason:
                        st.warning('Please select a Reason for Override before generating a tag.')
                    elif st.session_state.pop(DRAFT_KEY, None) is not None:
                        # Consume the draft before I/O so a second click during
                        # a slow save cannot submit the same item again.
                        try:
                            receipt = save_pricing_decision(user_id, draft, raw_final_price, reason)
                        except (PricingValidationError, ItemEntryError) as exc:
                            st.session_state[DRAFT_KEY] = draft
                            st.warning(str(exc))
                        except PricingSaveError as exc:
                            if exc.uncertain_tag:
                                _clear_search()
                                st.session_state[SEARCH_ERROR_KEY] = (
                                    'warning', f'Save confirmation was interrupted. Check Pricing History for tag '
                                               f'{exc.uncertain_tag} before pricing this item again.'
                                )
                                st.rerun()
                            else:
                                st.session_state[DRAFT_KEY] = draft
                                st.error('Unable to save the item. Please retry or contact an administrator.')
                        except Exception:
                            LOG.error('Unexpected pricing submission failure')
                            st.error('Unable to confirm the save. Check Pricing History before trying again.')
                        else:
                            st.session_state[RECEIPT_KEY] = receipt.as_dict()
                            st.session_state.pop(RESULT_KEY, None)
                            st.rerun()
                st.markdown(
                    '<div class="gw-caption-note">A unique tag number will be created for this item.</div>',
                    unsafe_allow_html=True,
                )

    st.markdown(
        '<div class="gw-pricing-footer"><span>© 2026 Goodwill. All rights reserved.</span>'
        '<span>Goodwill Pricing Decision Support System</span></div>',
        unsafe_allow_html=True,
    )


def _render_receipt(receipt: dict, employee: dict) -> None:
    # Keep the workflow header visible on the generated-tag page as well.
    with st.container(key=f'{P}intro'):
        headline, progress = st.columns([2, 1.2], gap='medium')
        with headline:
            st.markdown(
                '<div class="gw-eyebrow">Store operations</div>'
                '<div class="gw-page-title">Price an item</div>'
                '<div class="gw-desc">Your tag has been generated. '
                'Review the details and print it below.</div>',
                unsafe_allow_html=True,
            )
        with progress:
            _steps(complete=True)

    _, center, _ = st.columns([1, 1.55, 1], gap='small')
    with center:
        with st.container(border=True, key=f'{P}receipt'):
            st.markdown(
                '<div class="gw-confirm">✓</div>'
                '<div class="gw-confirm-title">Tag generated successfully</div>'
                '<div class="gw-confirm-sub">Your item is priced and ready for the sales floor.</div>',
                unsafe_allow_html=True,
            )
            category = _safe(receipt['category'])
            brand = _safe(receipt['brand'])
            condition = _safe(receipt['condition'])
            desc = _safe(receipt.get('description') or '')
            description_html = f'<div class="gw-tag-desc">{desc}</div>' if desc else ''
            tag = _safe(receipt['tag_number'])
            barcode_html = ''
            try:
                svg = barcode_svg(str(receipt['tag_number']))
                src = base64.b64encode(svg).decode('ascii')
                barcode_html = (
                    '<img alt="Code 128 barcode" '
                    'style="display:block;width:92%;height:57px;object-fit:contain;margin:8px auto 5px;" '
                    f'src="data:image/svg+xml;base64,{src}"/>'
                )
            except ImportError:
                st.error('Install reportlab to preview the barcode.')
            except Exception:
                LOG.exception('Barcode SVG rendering failure')
                st.error('Unable to render barcode. Saved decision is unaffected.')
            st.markdown(
                '<div class="gw-tag-preview">'
                '<div class="gw-tag-top"><span class="gw-tag-brand">goodwill</span>'
                f'<span class="gw-tag-store">STORE {int(receipt["branch_id"]):03d}</span></div>'
                f'<div class="gw-tag-category">{category}&nbsp; · &nbsp;{brand}&nbsp; · &nbsp;{condition}</div>'
                f'{description_html}'
                '<div class="gw-tag-price-row">'
                f'<div class="gw-tag-price">${_safe(receipt["final_price"])}</div>'
                f'<div class="gw-tag-id">TAG NUMBER<b>{tag}</b></div>'
                '</div>'
                f'{barcode_html}'
                '<div style="text-align:center;color:#17334d;font-size:13px;font-weight:600;">'
                f'{tag}</div><div style="text-align:center;color:#71879b;font-size:10px;margin-bottom:0;">'
                'Code 128 barcode</div>'
                '</div>', unsafe_allow_html=True,
            )
            try:
                pdf_bytes = printable_label_pdf(receipt)
            except Exception:
                LOG.exception('PDF tag generation failed')
                st.error('PDF generation unavailable. Check reportlab installation.')
            else:
                st.download_button(
                    'Print Tag', data=pdf_bytes,
                    file_name=f"goodwill_{receipt['tag_number']}.pdf",
                    mime='application/pdf', type='primary',
                    use_container_width=True, key=f'{P}print',
                )
            if st.button('Start Next Item', use_container_width=True, key=f'{P}next'):
                _start_next_item()
                st.rerun()
            st.markdown(
                '<div style="text-align:center;padding:20px 0 4px;color:#70869a;font-size:13px;">'
                f'Saved to Store {int(receipt["branch_id"]):03d} · '
                f'Final price ${_safe(receipt["final_price"])}</div>',
                unsafe_allow_html=True,
            )
    st.caption('Print Tag downloads a printable PDF label. Open it and choose Print.')
    st.markdown(
        '<div class="gw-pricing-footer"><span>© 2026 Goodwill. All rights reserved.</span>'
        '<span>Goodwill Pricing Decision Support System</span></div>', unsafe_allow_html=True,
    )


def render_item_search_page(*, show_header: bool = True, on_logout=None) -> None:
    """Render the staff pricing flow. Optional on_logout is an existing app callback."""
    load_styles("pricing.css")
    raw_user_id = st.session_state.get('user_id')
    try:
        user_id = int(raw_user_id)
    except (TypeError, ValueError):
        st.warning('Please log in to access item pricing.')
        return
    try:
        employee = get_authenticated_store_staff(user_id)
    except Exception:
        LOG.error('Failed to verify staff account')
        st.error('Could not verify your login with MySQL. Check the database connection.')
        return
    if employee is None:
        st.error('Only active store staff accounts can access Item Pricing.')
        return
    _header(employee, show_header, on_logout=on_logout)

    receipt = st.session_state.get(RECEIPT_KEY)
    if receipt:
        # Never show the previous employee's tag after another login.
        if int(receipt.get('staff_id', -1)) != int(employee['Staff_Id']):
            _start_next_item()
        else:
            _render_receipt(receipt, employee)
            return
    try:
        lookups = get_lookup_options()
    except Exception:
        LOG.error('Unable to load dropdown options from MySQL')
        st.error('Unable to load item options. Please retry or contact an administrator.')
        return
    if any(not lookups.get(field) for field in ('Item_Type_Id','Category_Id','Brand_Id','Condition_Id')):
        st.warning('One or more lookup tables are empty. Have an administrator add master data first.')
        return
    _item_form(employee, lookups, user_id)
