"""Goodwill administrator Reports UI and export; live DB values, no placeholders."""
from __future__ import annotations

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo
from decimal import Decimal
from html import escape
from io import BytesIO
import logging
from typing import Mapping

import streamlit as st

from dashboard_data import DashboardPermissionError
from reports_data import (get_report_snapshot, get_report_filter_options,
                          PERIODS, ADJUSTMENTS)
from style_loader import load_styles

LOG = logging.getLogger(__name__)
NAVY = "#18364f"
TEAL = "#315E7D"
CHART_COLORS = ("#18364F", "#315E7D", "#6287A1", "#B5CAD8")


def _h(value: object) -> str:
    return escape(str(value if value is not None else ""), quote=True)


def _html(markup: str) -> None:
    st.html(markup)


def _cash(value: object) -> str:
    return f"${Decimal(str(value or 0)):,.2f}"


def _metric(title: str, value: str, note: str, *, accent: bool = False) -> None:
    _html('<div class="gw-report-metric">'
          f'<div class="gw-report-metric-label">{_h(title)}</div>'
          f'<div class="gw-report-metric-value{ " is-accent" if accent else "" }">{_h(value)}</div>'
          f'<div class="gw-report-metric-note">{_h(note)}</div></div>')


def _category_chart(categories: list[dict]) -> None:
    _html('<div class="gw-report-panel-title">Top Priced Categories</div>'
          '<div class="gw-report-panel-sub">Average final price by category</div>')
    if not categories:
        st.caption("No priced items for this period and branch.")
        return
    biggest = max(float(r.get("average_final_price") or 0) for r in categories) or 1
    blocks = []
    for row in categories:
        name = _h(row.get("category_name") or "Unnamed")
        value = float(row.get("average_final_price") or 0)
        width = max(0, min(100, 100 * value / biggest))
        blocks.append('<div class="gw-report-bar-row">'
                      f'<span title="{name}" class="gw-report-bar-name">{name}</span>'
                      '<span class="gw-report-bar-track">'
                      f'<span class="gw-report-bar-fill" style="width:{width:.2f}%"></span></span>'
                      f'<span class="gw-report-bar-value">{_h(_cash(value))}</span></div>')
    _html(''.join(blocks))
    _html('<div class="gw-report-axis-caption">USD · Average final price</div>')


def _reason_chart(reasons: list[dict], total: int) -> None:
    _html('<div class="gw-report-panel-title">Most Common Override Reasons</div>'
          '<div class="gw-report-panel-sub">Why final prices differ from suggested prices</div>')
    if not reasons or total == 0:
        st.caption("No price overrides for the selected period.")
        return
    gradients = []
    pos = 0.0
    legend = []
    for i, row in enumerate(reasons):
        pct = 100 * int(row["count"]) / total
        end = pos + pct
        color = CHART_COLORS[min(i, len(CHART_COLORS) - 1)]
        gradients.append(f"{color} {pos:.4f}% {end:.4f}%")
        pos = end
        legend.append('<div class="gw-report-reason-line">'
                      f'<span class="gw-report-color-dot" style="background:{color}"></span>'
                      f'<span class="gw-report-reason-name">{_h(row["reason"])}</span>'
                      f'<strong>{pct:.0f}%</strong></div>')
    # Avoid a tiny blank sliver due to floating point rounding.
    if pos < 100:
        gradients.append(f"{CHART_COLORS[min(len(reasons)-1, len(CHART_COLORS)-1)]} {pos:.4f}% 100%")
    bg = ','.join(gradients)
    _html('<div class="gw-report-donut-layout">'
          f'<div class="gw-report-donut" style="background:conic-gradient({bg})">'
          '<div class="gw-report-donut-hole">'
          f'<strong>{total:,}</strong><span>overrides</span></div></div>'
          f'<div class="gw-report-reason-legend">{"".join(legend)}</div></div>')
    _html('<div class="gw-report-reason-caption">Share of price overrides in the selected period</div>')


def _staff_table(staff: list[dict], show_all: bool, limit: int) -> None:
    visible = staff if show_all else staff[:5]
    parts = ['<div class="gw-report-table-scroll"><table class="gw-report-table">'
             '<thead><tr><th>Staff Name</th><th>Branch</th>'
             '<th class="numeric">Items Priced ↓</th><th class="numeric">Avg. Price</th>'
             '<th class="numeric">Overrides</th></tr></thead><tbody>']
    for row in visible:
        name = _h(row.get("staff_name") or "Unknown")
        state = '' if bool(row.get('is_active', True)) else ' <span class="gw-report-inactive">Inactive</span>'
        parts.append('<tr>'
                     f'<td>{name}{state}</td>'
                     f'<td>{_h(row.get("branch_name") or "")}</td>'
                     f'<td class="numeric">{int(row.get("items_priced") or 0):,}</td>'
                     f'<td class="numeric">{_h(_cash(row.get("average_final_price")))}</td>'
                     f'<td class="numeric">{int(row.get("override_count") or 0):,}</td>'
                     '</tr>')
    parts.append('</tbody></table></div>')
    _html(''.join(parts))
    if not staff:
        st.caption("No staff pricing activity for the selected filters.")
    elif len(staff) >= limit:
        st.caption(f"Showing up to {limit:,} staff. The main totals include all staff.")


def build_reports_pdf(report: dict) -> bytes:
    """Build a self-contained, multi-page PDF from the exact displayed report."""
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_LEFT
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import inch
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, LongTable

    navy = colors.HexColor(NAVY)
    pale = colors.HexColor("#f1f6f8")
    teal = colors.HexColor(TEAL)
    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle(name="GWTitle", parent=styles['Title'], fontName="Helvetica-Bold",
                              textColor=navy, fontSize=19, leading=23, alignment=TA_LEFT))
    styles.add(ParagraphStyle(name="GWSection", parent=styles['Heading2'],
                              fontName="Helvetica-Bold", textColor=navy, fontSize=12, spaceBefore=14))
    styles.add(ParagraphStyle(name="GWBody", parent=styles['Normal'], textColor=navy, fontSize=9, leading=12))
    buf = BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=letter, rightMargin=42, leftMargin=42,
                            topMargin=45, bottomMargin=48)
    start = report['start'].date().isoformat()
    through = (report['end'] - timedelta(days=1)).date().isoformat()
    branch = str(report['branch_name'])
    m = report['metrics']
    description = str(report.get('filter_description') or 'All staff, categories and item types')
    story = [Paragraph("goodwill | Pricing Reports &amp; Analytics", styles['GWTitle']),
             Spacer(1, 6),
             Paragraph(f"{_h(branch)} | {start} through {through} (America/Detroit)", styles['GWBody']),
             Spacer(1, 4),
             Paragraph(_h(description), styles['GWBody']),
             Spacer(1, 16)]
    totals = [
        ["Items priced", "Avg. final price", "Override rate", "Active staff with activity"],
        [f"{m['items_priced']:,}", _cash(m['average_final_price']),
         f"{m['override_rate']:.1%} ({m['override_count']:,})",
         f"{m['active_staff_with_activity']:,}"],
    ]
    t = Table(totals, colWidths=[132] * 4, rowHeights=[23, 27])
    t.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,-1),pale),
                           ('TEXTCOLOR',(0,0),(-1,-1),navy),
                           ('FONTNAME',(0,0),(-1,0),'Helvetica-Bold'),
                           ('FONTNAME',(0,1),(-1,1),'Helvetica-Bold'),
                           ('FONTSIZE',(0,0),(-1,0),8),('FONTSIZE',(0,1),(-1,1),11),
                           ('VALIGN',(0,0),(-1,-1),'MIDDLE'),
                           ('LEFTPADDING',(0,0),(-1,-1),9),
                           ('LINEBELOW',(0,-1),(-1,-1),1,teal)]))
    story.append(t)
    story.append(Paragraph('Top Priced Categories', styles['GWSection']))
    categories = [["Category", "Items priced", "Average final price"]]
    categories.extend([str(r.get('category_name') or ''),
                       f"{int(r.get('items_priced') or 0):,}",
                       _cash(r.get('average_final_price'))] for r in report['categories'])
    if len(categories) == 1:
        categories.append(["No activity", "—", "—"])
    story.append(_pdf_table(categories, [248, 126, 154]))
    story.append(Paragraph('Override Reasons', styles['GWSection']))
    reasons = [["Reason", "Count", "Share of overrides"]]
    reasons.extend([str(r['reason']), f"{int(r['count']):,}", f"{r['share']:.1%}"] for r in report['reasons'])
    if len(reasons) == 1:
        reasons.append(["No overrides", "0", "—"])
    story.append(_pdf_table(reasons, [248, 126, 154]))
    story.append(Paragraph('Staff Pricing Activity', styles['GWSection']))
    activity = [["Staff", "Branch", "Priced", "Avg. price", "Overrides"]]
    for row in report['staff']:
        name = str(row.get('staff_name') or 'Unknown')
        if not bool(row.get('is_active', True)):
            name += ' (inactive)'
        activity.append([name, str(row.get('branch_name') or ''),
                         f"{int(row.get('items_priced') or 0):,}",
                         _cash(row.get('average_final_price')),
                         f"{int(row.get('override_count') or 0):,}"])
    if len(activity) == 1:
        activity.append(["No activity", "", "0", "$0.00", "0"])
    story.append(_pdf_table(activity, [157, 153, 60, 86, 72], long=True))
    story.append(Spacer(1, 12))
    story.append(Paragraph("Source: live MySQL PRICING_DECISION records. "
                           "Overrides compare each decision with its linked historical guideline price. "
                           "Counts reflect pricing decisions; inactive staff's historic activity remains in the table.",
                           styles['GWBody']))

    def footer(canvas, document):
        canvas.saveState()
        canvas.setStrokeColor(colors.HexColor("#dce5ec"))
        canvas.line(42, 35, letter[0] - 42, 35)
        canvas.setFont('Helvetica', 8)
        canvas.setFillColor(navy)
        canvas.drawString(42, 23, "Goodwill Pricing Decision Support System")
        canvas.drawRightString(letter[0] - 42, 23, f"Page {document.page}")
        canvas.restoreState()

    doc.build(story, onFirstPage=footer, onLaterPages=footer)
    return buf.getvalue()


def _pdf_table(data: list[list[str]], widths: list[int], *, long: bool = False):
    from reportlab.lib import colors
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.platypus import Table, LongTable, TableStyle, Paragraph

    cell_style = ParagraphStyle(name='GWCellWrap', fontName='Helvetica',
                                fontSize=8, leading=10, splitLongWords=1,
                                textColor=colors.HexColor(NAVY))
    # Long names or branch labels must wrap rather than overprint columns.
    safe_data = [data[0]]
    for row in data[1:]:
        safe_data.append([Paragraph(_h(value), cell_style)
                          if len(str(value)) > 28 else value
                          for value in row])
    obj = (LongTable if long else Table)(safe_data, colWidths=widths, repeatRows=1,
                                        hAlign='LEFT', splitByRow=1)
    obj.setStyle(TableStyle([
        ('BACKGROUND', (0,0),(-1,0),colors.HexColor('#e6eff2')),
        ('FONTNAME', (0,0),(-1,0),'Helvetica-Bold'),
        ('FONTSIZE', (0,0),(-1,-1),8),
        ('TEXTCOLOR',(0,0),(-1,-1),colors.HexColor(NAVY)),
        ('VALIGN',(0,0),(-1,-1),'MIDDLE'),
        ('TOPPADDING',(0,0),(-1,-1),6),
        ('BOTTOMPADDING',(0,0),(-1,-1),6),
        ('ROWBACKGROUNDS',(0,1),(-1,-1),[colors.white,colors.HexColor('#f8fafb')]),
        ('LINEBELOW',(0,-1),(-1,-1),.6,colors.HexColor('#d8e4ec')),
    ]))
    return obj


FILTER_KEYS = (
    'gw_reports_branch', 'gw_reports_staff', 'gw_reports_days',
    'gw_reports_start_date', 'gw_reports_end_date',
    'gw_reports_category', 'gw_reports_item_type',
    'gw_reports_adjustment', 'gw_reports_show_all_staff',
)
ADJUSTMENT_LABELS = {
    'all': 'All pricing decisions',
    'overrides': 'Overrides only',
    'unchanged': 'No price adjustment',
    'increased': 'Price increased',
    'decreased': 'Price decreased',
}


def _valid_selection(key: str, allowed: list) -> None:
    """Clear stale dropdown IDs before Streamlit creates keyed widgets."""
    if key in st.session_state and st.session_state[key] not in allowed:
        st.session_state.pop(key, None)


def _reset_filters() -> None:
    for key in FILTER_KEYS:
        st.session_state.pop(key, None)
    st.rerun()


def show_admin_reports(user: Mapping) -> None:
    """Render live Reports with a shared set of filters for all charts and PDF."""
    if user.get('user_type') != 'Administrator':
        st.error('Administrator access required.')
        return
    load_styles('reports.css')
    try:
        options = get_report_filter_options(user)
    except DashboardPermissionError:
        st.error('You do not have permission to access pricing reports.')
        return
    except Exception:
        LOG.exception('Unable to load Reports filter options')
        st.error('Unable to load reporting filters from MySQL. Check the server logs.')
        return

    heading, export_col = st.columns([4.7, 1.2], gap='medium', vertical_alignment='bottom')
    with heading:
        _html('<div class="gw-eyebrow">CORPORATE ADMIN</div>'
              '<div class="gw-report-title">Pricing Reports &amp; Analytics</div>'
              '<div class="gw-report-subtitle">Analyze pricing performance and staff consistency.</div>')

    branch_names = {int(x['branch_id']): str(x['branch_name']) for x in options['branches']}
    category_names = {int(x['category_id']): str(x['category_name']) for x in options['categories']}
    item_type_names = {int(x['item_type_id']): str(x['item_type_name']) for x in options['item_types']}
    _valid_selection('gw_reports_branch', [None, *branch_names])
    _valid_selection('gw_reports_days', [7, 30, 90, 365, 'custom'])
    _valid_selection('gw_reports_category', [None, *category_names])
    _valid_selection('gw_reports_item_type', [None, *item_type_names])
    _valid_selection('gw_reports_adjustment', list(ADJUSTMENTS))

    with st.container(key='gw_reports_filters'):
        caption_col, reset_col = st.columns([5.6, 1], gap='small', vertical_alignment='center')
        with caption_col:
            _html('<div class="gw-report-filter-heading">Report filters</div>')
        with reset_col:
            if st.button('Reset filters', key='gw_reports_reset', use_container_width=True):
                _reset_filters()
                return
        col_branch, col_staff, col_period = st.columns([1.15, 1.5, 1.15], gap='medium')
        with col_branch:
            branch_id = st.selectbox(
                'Branch', [None, *branch_names],
                format_func=lambda x: 'All branches' if x is None else branch_names[x],
                key='gw_reports_branch')
        possible_staff = [s for s in options['staff'] if branch_id is None
                          or int(s['branch_id']) == branch_id]
        staff_names = {
            int(s['staff_id']): str(s['staff_name']) +
            (' (inactive)' if not bool(s['is_active']) else '')
            for s in possible_staff
        }
        _valid_selection('gw_reports_staff', [None, *staff_names])
        with col_staff:
            staff_id = st.selectbox(
                'Store Staff', [None, *staff_names],
                format_func=lambda x: 'All staff' if x is None else staff_names[x],
                key='gw_reports_staff')
        with col_period:
            period = st.selectbox(
                'Time range', [7, 30, 90, 365, 'custom'],
                format_func=lambda x: ('Custom date range' if x == 'custom'
                                       else f'Last {x} Days'),
                key='gw_reports_days')
        start_date = end_date = None
        if period == 'custom':
            local_today = datetime.now(ZoneInfo('America/Detroit')).date()
            start_col, end_col = st.columns(2, gap='medium')
            with start_col:
                start_date = st.date_input(
                    'Start date', value=local_today - timedelta(days=29),
                    key='gw_reports_start_date')
            with end_col:
                end_date = st.date_input(
                    'End date (inclusive)', value=local_today,
                    key='gw_reports_end_date')
        with st.expander('Advanced filters · Category, Item Type & price adjustment',
                         expanded=False):
            col_category, col_type, col_adjustment = st.columns(3, gap='medium')
            with col_category:
                category_id = st.selectbox(
                    'Category', [None, *category_names],
                    format_func=lambda x: 'All categories' if x is None else category_names[x],
                    key='gw_reports_category')
            with col_type:
                item_type_id = st.selectbox(
                    'Item Type', [None, *item_type_names],
                    format_func=lambda x: 'All item types' if x is None else item_type_names[x],
                    key='gw_reports_item_type')
            with col_adjustment:
                adjustment = st.selectbox(
                    'Price adjustment', list(ADJUSTMENT_LABELS),
                    format_func=lambda x: ADJUSTMENT_LABELS[x],
                    key='gw_reports_adjustment')

    try:
        # One data call uses the same WHERE parameters in every report metric.
        kwargs = {'days': period if isinstance(period, int) else 30,
                  'branch_id': branch_id, 'staff_id': staff_id,
                  'category_id': category_id, 'item_type_id': item_type_id,
                  'adjustment': adjustment}
        if period == 'custom':
            kwargs.update(start_date=start_date, end_date=end_date)
        report = get_report_snapshot(user, **kwargs)
    except DashboardPermissionError:
        st.error('You do not have permission to access pricing reports.')
        return
    except ValueError as exc:
        st.warning(str(exc))
        return
    except Exception:
        LOG.exception('Unable to load Goodwill pricing report')
        st.error('Unable to load reporting data from MySQL. Check the server logs.')
        return

    report_start = report['start'].date().isoformat()
    report_end = (report['end'] - timedelta(days=1)).date().isoformat()
    selections = [f'{report_start} to {report_end}',
                  branch_names.get(branch_id, 'All branches'),
                  staff_names.get(staff_id, 'All staff'),
                  category_names.get(category_id, 'All categories'),
                  item_type_names.get(item_type_id, 'All item types'),
                  ADJUSTMENT_LABELS[adjustment]]
    report['filter_description'] = ' | '.join(selections[2:])
    _html('<div class="gw-report-applied-filters"><b>Showing:</b> '
          + ' <span class="gw-report-filter-separator"> · </span> '.join(
              _h(value) for value in selections) + '</div>')
    st.caption('Dates use America/Detroit. Historical records are attributed to staff members’ '
               'currently assigned branches.')
    with export_col:
        try:
            pdf = build_reports_pdf(report)
        except ImportError:
            st.caption('Install reportlab to enable PDF export.')
        except Exception:
            LOG.exception('Unable to build Goodwill Reports PDF')
            st.caption('PDF export is temporarily unavailable.')
        else:
            st.download_button('Export to PDF', data=pdf,
                               file_name=f'goodwill_report_{report_start}_{report_end}.pdf',
                               mime='application/pdf', use_container_width=True,
                               key='gw_reports_export', type='primary')

    m = report['metrics']
    cols = st.columns(4, gap='medium')
    cards = (
        ('Total Items Priced', f'{m["items_priced"]:,}', 'Pricing decisions matching filters', False),
        ('Average Final Price', _cash(m['average_final_price']), 'After staff adjustments', False),
        ('Override Rate', f'{m["override_rate"]:.1%}',
         f'{m["override_count"]:,} of {m["items_priced"]:,} filtered decisions', True),
        ('Active Store Staff', f'{m["active_staff_with_activity"]:,}',
         'Active accounts with pricing activity', False),
    )
    for col, (title, value, note, accent) in zip(cols, cards):
        with col:
            _metric(title, value, note, accent=accent)

    chart_left, chart_right = st.columns(2, gap='medium')
    with chart_left:
        with st.container(key='gw_reports_category_panel'):
            _category_chart(report['categories'])
    with chart_right:
        with st.container(key='gw_reports_reason_panel'):
            _reason_chart(report['reasons'], m['override_count'])

    with st.container(key='gw_reports_staff_panel'):
        _html('<div class="gw-report-panel-title">Staff Pricing Activity</div>'
              '<div class="gw-report-panel-sub">Compare staff volume, average price, and overrides</div>')
        showing_all = bool(st.session_state.get('gw_reports_show_all_staff', False))
        _staff_table(report['staff'], showing_all, report['staff_limit'])
        _html(f'<div class="gw-report-table-foot">Showing '
              f'{len(report["staff"]) if showing_all else min(5, len(report["staff"])):,} '
              f'of {len(report["staff"]):,} staff · Ranked by items priced'
              f'<span>{_h(report["branch_name"])} · {report_start} to {report_end}</span></div>')
        if len(report['staff']) > 5:
            if st.button('Show top 5 only' if showing_all else 'Show all staff',
                         key='gw_reports_more_staff'):
                st.session_state['gw_reports_show_all_staff'] = not showing_all
                st.rerun()
    _html('<div class="gw-report-footer"><span>© 2026 Goodwill. All rights reserved.</span>'
          '<span>Live data from pricing decisions</span>'
          '<span>Goodwill Pricing Decision Support System</span></div>')
