"""Payment Tracking System — Weekly Receivables Report

Page 1 — Summary cards, payments due this week, status distribution pie chart
Page 2 — Top 10 overdue receivables table, overdue distribution bar + profile distribution pie

Usage:
    from weekly_report import generate_weekly_report
    path = generate_weekly_report(clients, receivables)
"""

import io
import os
import sys
import datetime
from collections import defaultdict

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
import matplotlib.ticker as mticker

from reportlab.lib import colors
from reportlab.lib.units import cm
from reportlab.lib.pagesizes import A4
from reportlab.lib.enums import TA_CENTER, TA_RIGHT
from reportlab.lib.styles import ParagraphStyle
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer,
    Table, TableStyle, Image, PageBreak,
)

sys.path.insert(0, os.path.dirname(__file__))
from base import (
    W, H, MARGIN, CONTENT_W,
    R, PROFILES, PROFILE_COLORS,
    S_TH, S_TD, S_TD_C, S_TD_R, S_NOTE,
    section_heading, create_card, draw_header,
    fmt_amount, fmt_date, days_overdue, parse_date,
    status_color, profile_info, today_str,
    calculate_profile, PageNumberCanvas,
)

# ── COLOR CONSTANTS (hex, for charts) ─────────────────────────────────────────
_HEX = {
    'paid':        '#27AE60',
    'partial':     '#E67E22',
    'unpaid':      '#E74C3C',
    'perfect':     '#27AE60',
    'reliable':    '#2ECC71',
    'uncertain':   '#F39C12',
    'risky':       '#E67E22',
    'problematic': '#E74C3C',
}

# Overdue categories (bar order)
OVERDUE_CATS  = ['1-7 days', '8-15 days', '16-30 days', '31-60 days', '60+ days']
OVERDUE_COLORS = ['#F39C12', '#E67E22', '#E74C3C', '#C0392B', '#7B241C']


def _week_range(week_end_str: str = None):
    """
    Return Monday–Sunday range.
    If week_end_str is not provided, uses the Sunday of the current week.
    Returns: (start: date, end: date)
    """
    if week_end_str:
        end = parse_date(week_end_str)
    else:
        today = datetime.date.today()
        end = today + datetime.timedelta(days=6 - today.weekday())
    start = end - datetime.timedelta(days=6)
    return start, end


# ── DATA FILTERING ────────────────────────────────────────────────────────────
def _clients_map(clients: list) -> dict:
    """client_id → client dict mapping."""
    return {k.get('client_id', ''): k for k in clients}


def _client_name(client_id: str, clients_map: dict) -> str:
    k = clients_map.get(client_id)
    if k:
        return k.get('full_name', client_id)
    return client_id


def _due_this_week(receivables: list, clients_map: dict,
                   start: datetime.date,
                   end: datetime.date) -> list:
    """Receivables whose due date falls within this week (including paid — for reference)."""
    result = []
    for a in receivables:
        t = parse_date(a.get('due_date'))
        if t and start <= t <= end:
            result.append({**a, '_client_name': _client_name(a.get('client_id', ''), clients_map)})
    return sorted(result, key=lambda a: (parse_date(a.get('due_date')) or datetime.date.min))


def _overdue_receivables(receivables: list, clients_map: dict, limit: int = 10) -> list:
    """Overdue and unpaid receivables, sorted from most to least overdue."""
    result = []
    for a in receivables:
        status = str(a.get('status', '')).lower()
        if 'paid' in status:
            continue
        days_late = days_overdue(a.get('due_date', ''))
        if days_late <= 0:
            continue
        result.append({**a, '_days_late': days_late,
                       '_client_name': _client_name(a.get('client_id', ''), clients_map)})
    result.sort(key=lambda a: a['_days_late'], reverse=True)
    return result[:limit], len(result)  # (list, total_overdue_count)


def _overdue_categories(receivables: list) -> list:
    """Return [count_1_7, count_8_15, count_16_30, count_31_60, count_60p]."""
    counts = [0, 0, 0, 0, 0]
    for a in receivables:
        status = str(a.get('status', '')).lower()
        if 'paid' in status:
            continue
        days_late = days_overdue(a.get('due_date', ''))
        if days_late <= 0:
            continue
        if   days_late <=  7: counts[0] += 1
        elif days_late <= 15: counts[1] += 1
        elif days_late <= 30: counts[2] += 1
        elif days_late <= 60: counts[3] += 1
        else:                 counts[4] += 1
    return counts


def _profile_distribution(clients: list, receivables: list) -> dict:
    """Calculate each client's profile and return {profile_name: count}."""
    # client_id → receivable list
    client_receivables = defaultdict(list)
    for a in receivables:
        client_receivables[a.get('client_id', '')].append(a)

    distribution = defaultdict(int)
    for k in clients:
        active = str(k.get('active', 'Yes')).lower()
        if active == 'no':
            continue
        kid = k.get('client_id', '')
        p = calculate_profile(client_receivables.get(kid, []))
        distribution[p] += 1
    return dict(distribution)


# ── CHARTS ────────────────────────────────────────────────────────────────────
def _status_pie(n_paid: int, n_partial: int, n_unpaid: int):
    """
    Payment status distribution pie chart.
    Returns: (BytesIO, [(label, hex)] legend)  or (None, [])
    """
    data, labels, chart_colors = [], [], []
    if n_paid    > 0: data.append(n_paid);    labels.append(f'Paid ({n_paid})');       chart_colors.append(_HEX['paid'])
    if n_partial > 0: data.append(n_partial); labels.append(f'Partial ({n_partial})'); chart_colors.append(_HEX['partial'])
    if n_unpaid  > 0: data.append(n_unpaid);  labels.append(f'Unpaid ({n_unpaid})');   chart_colors.append(_HEX['unpaid'])
    if not data:
        return None, []

    fig, ax = plt.subplots(figsize=(4.2, 4.2), facecolor='none')
    ax.pie(
        data, colors=chart_colors,
        autopct=lambda p: f'{round(p)}%' if p >= 5 else '',
        pctdistance=0.70, startangle=90,
        wedgeprops=dict(linewidth=2.5, edgecolor='white'),
        textprops=dict(fontsize=12, fontweight='bold', color='white'),
    )
    ax.set_aspect('equal')
    plt.tight_layout(pad=0)
    buf = io.BytesIO()
    fig.savefig(buf, format='png', dpi=200, bbox_inches='tight',
                transparent=True, facecolor='none')
    buf.seek(0)
    plt.close(fig)
    return buf, list(zip(labels, chart_colors))


def _profile_pie(profile_distribution: dict):
    """
    Profile distribution pie chart (ordered: Perfect→Problematic).
    Returns: (BytesIO, [(label, hex)] legend)  or (None, [])
    """
    data, labels, chart_colors = [], [], []
    color_map = dict(zip(
        ['Perfect', 'Reliable', 'Uncertain', 'Risky', 'Problematic'],
        [_HEX['perfect'], _HEX['reliable'], _HEX['uncertain'],
         _HEX['risky'], _HEX['problematic']],
    ))
    for p in ['Perfect', 'Reliable', 'Uncertain', 'Risky', 'Problematic']:
        n = profile_distribution.get(p, 0)
        if n > 0:
            data.append(n)
            labels.append(f'{p} ({n})')
            chart_colors.append(color_map[p])
    if not data:
        return None, []

    fig, ax = plt.subplots(figsize=(4.2, 4.2), facecolor='none')
    ax.pie(
        data, colors=chart_colors,
        autopct=lambda p: f'{round(p)}%' if p >= 5 else '',
        pctdistance=0.70, startangle=90,
        wedgeprops=dict(linewidth=2.5, edgecolor='white'),
        textprops=dict(fontsize=12, fontweight='bold', color='white'),
    )
    ax.set_aspect('equal')
    plt.tight_layout(pad=0)
    buf = io.BytesIO()
    fig.savefig(buf, format='png', dpi=200, bbox_inches='tight',
                transparent=True, facecolor='none')
    buf.seek(0)
    plt.close(fig)
    return buf, list(zip(labels, chart_colors))


def _overdue_bar(counts: list):
    """
    Overdue period distribution bar chart.
    counts: [count_1_7, count_8_15, count_16_30, count_31_60, count_60p]
    Returns: BytesIO  or None
    """
    if sum(counts) == 0:
        return None

    fig, ax = plt.subplots(figsize=(5.8, 3.6), facecolor='none')

    x = range(len(OVERDUE_CATS))
    bars = ax.bar(x, counts, color=OVERDUE_COLORS,
                  edgecolor='white', linewidth=1.5, width=0.6)

    # Value label above each bar
    for bar, count in zip(bars, counts):
        if count > 0:
            ax.text(
                bar.get_x() + bar.get_width() / 2,
                bar.get_height() + 0.15,
                str(count),
                ha='center', va='bottom',
                fontsize=11, fontweight='bold', color='#2C3E50',
            )

    ax.set_xticks(list(x))
    ax.set_xticklabels(OVERDUE_CATS, fontsize=9.5)   # set individually → no overlap
    ax.set_ylabel('Count', fontsize=9.5)
    ax.yaxis.set_major_locator(mticker.MaxNLocator(integer=True))
    ax.set_ylim(0, max(counts) * 1.3 + 1)

    ax.spines['top'].set_visible(False)
    ax.spines['right'].set_visible(False)
    ax.tick_params(axis='both', labelsize=9)

    plt.tight_layout(pad=0.4)
    buf = io.BytesIO()
    fig.savefig(buf, format='png', dpi=200, bbox_inches='tight',
                transparent=True, facecolor='none')
    buf.seek(0)
    plt.close(fig)
    return buf


# ── LEGEND (same method as client report) ─────────────────────────────────────
def _legend_table(legend_data: list, font_size: int = 10) -> Table:
    s = ParagraphStyle('leg', fontName='DejaVu', fontSize=font_size,
                        textColor=R.TEXT, leading=14)
    rows = []
    for label, hex_color in legend_data:
        box = Table([['']], colWidths=[0.42 * cm], rowHeights=[0.42 * cm])
        box.setStyle(TableStyle([
            ('BACKGROUND',    (0, 0), (0, 0), colors.HexColor(hex_color)),
            ('TOPPADDING',    (0, 0), (0, 0), 0),
            ('BOTTOMPADDING', (0, 0), (0, 0), 0),
            ('LEFTPADDING',   (0, 0), (0, 0), 0),
            ('RIGHTPADDING',  (0, 0), (0, 0), 0),
        ]))
        rows.append([box, Paragraph(label, s)])

    t = Table(rows, colWidths=[0.65 * cm, 5 * cm])
    t.setStyle(TableStyle([
        ('VALIGN',        (0, 0), (-1, -1), 'MIDDLE'),
        ('TOPPADDING',    (0, 0), (-1, -1), 5),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 5),
        ('LEFTPADDING',   (0, 0), (-1, -1), 0),
        ('RIGHTPADDING',  (0, 0), (-1, -1), 0),
    ]))
    return t


# ── TABLE COMPONENTS ──────────────────────────────────────────────────────────
def _stats_row(total: float, paid: float, remaining: float,
               collection_rate: float) -> Table:
    """4 summary stat cards."""
    def card(value_str, label, color):
        return create_card(value_str, label, color, CONTENT_W / 4, value_fs=15)

    remaining_color = R.UNPAID if remaining > 0 else R.PAID
    rate_color      = R.UNPAID if collection_rate < 50 else (
                      R.PARTIAL    if collection_rate < 80 else R.PAID)

    outer = Table([[
        card(fmt_amount(total),              'Total Receivable',    R.TEXT),
        card(fmt_amount(paid),               'Collected',           R.PAID),
        card(fmt_amount(remaining),          'Remaining',           remaining_color),
        card(f'%{collection_rate:.1f}',      'Collection Rate',     rate_color),
    ]], colWidths=[CONTENT_W / 4] * 4)
    outer.setStyle(TableStyle([
        ('LEFTPADDING',   (0, 0), (-1, -1), 0),
        ('RIGHTPADDING',  (0, 0), (-1, -1), 0),
        ('TOPPADDING',    (0, 0), (-1, -1), 0),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 0),
        ('VALIGN',        (0, 0), (-1, -1), 'TOP'),
    ]))
    return outer


def _this_week_table(records: list) -> Table:
    """Table of payments due this week."""
    col_widths = [2.2, 3.5, 1.5, 3.0, 2.3, 2.0, 2.9]  # cm
    col_widths = [w * cm for w in col_widths]
    headers    = ['Due Date', 'Client', 'Ref No', 'Project', 'Amount', 'Status', 'Remaining']
    rows = [[Paragraph(h, S_TH) for h in headers]]

    for a in records:
        status  = str(a.get('status', ''))
        s_color = status_color(status)
        s_d     = ParagraphStyle('ds', fontName='DejaVu-Bold', fontSize=8,
                                  textColor=s_color, alignment=TA_CENTER, leading=11)
        rows.append([
            Paragraph(fmt_date(a.get('due_date', '')),        S_TD_C),
            Paragraph(a.get('_client_name', ''),              S_TD),
            Paragraph(str(a.get('receivable_no', '')),        S_TD_C),
            Paragraph(str(a.get('project_name', '')),         S_TD),
            Paragraph(fmt_amount(a.get('total_amount', 0)),   S_TD_R),
            Paragraph(status,                                  s_d),
            Paragraph(fmt_amount(a.get('remaining_amount', 0)), S_TD_R),
        ])

    return _apply_table_style(rows, col_widths)


def _overdue_table(records: list, total_overdue: int) -> tuple:
    """
    Top 10 overdue receivables table.
    Returns: (Table, total_overdue_remaining float)
    """
    col_widths = [1.5, 3.5, 3.5, 2.8, 3.1, 3.0]  # cm
    col_widths = [w * cm for w in col_widths]
    headers    = ['Ref No', 'Client', 'Project', 'Description', 'Remaining', 'Days Late']
    rows = [[Paragraph(h, S_TH) for h in headers]]

    total_remaining = 0.0
    for a in records:
        try:
            remaining = float(str(a.get('remaining_amount', 0)).replace(',', '.') or 0)
        except ValueError:
            remaining = 0.0
        total_remaining += remaining

        days_late    = a.get('_days_late', days_overdue(a.get('due_date', '')))
        installment  = a.get('installment_no', '')
        description  = str(a.get('work_description', ''))
        if installment:
            description += f' T{installment}'

        # Days late color
        if   days_late > 60: days_late_color = '#7B241C'
        elif days_late > 30: days_late_color = '#C0392B'
        elif days_late > 15: days_late_color = '#E74C3C'
        else:                days_late_color = '#E67E22'

        s_g = ParagraphStyle('gf', fontName='DejaVu-Bold', fontSize=9,
                              textColor=colors.HexColor(days_late_color),
                              alignment=TA_CENTER, leading=12)
        rows.append([
            Paragraph(str(a.get('receivable_no', '')),  S_TD_C),
            Paragraph(a.get('_client_name', ''),        S_TD),
            Paragraph(str(a.get('project_name', '')),   S_TD),
            Paragraph(description,                       S_TD),
            Paragraph(fmt_amount(remaining),             S_TD_R),
            Paragraph(f'{days_late} days',               s_g),
        ])

    return _apply_table_style(rows, col_widths), total_remaining


def _apply_table_style(rows: list, col_widths: list) -> Table:
    """Apply common table style (zebra stripes + header)."""
    t = Table(rows, colWidths=col_widths, repeatRows=1)
    style = [
        ('BACKGROUND',    (0, 0), (-1,  0), R.TH_BG),
        ('TOPPADDING',    (0, 0), (-1,  0), 8),
        ('BOTTOMPADDING', (0, 0), (-1,  0), 8),
        ('FONTNAME',      (0, 1), (-1, -1), 'DejaVu'),
        ('FONTSIZE',      (0, 1), (-1, -1), 9),
        ('TOPPADDING',    (0, 1), (-1, -1), 6),
        ('BOTTOMPADDING', (0, 1), (-1, -1), 6),
        ('LEFTPADDING',   (0, 0), (-1, -1), 5),
        ('RIGHTPADDING',  (0, 0), (-1, -1), 5),
        ('VALIGN',        (0, 0), (-1, -1), 'MIDDLE'),
        ('LINEBELOW',     (0, 0), (-1, -1), 0.3, R.BORDER),
        ('BOX',           (0, 0), (-1, -1), 0.5, R.TH_BG),
    ]
    for i in range(1, len(rows)):
        if i % 2 == 0:
            style.append(('BACKGROUND', (0, i), (-1, i), R.ROW_ALT))
    t.setStyle(TableStyle(style))
    return t


# ── HEADER / FOOTER ───────────────────────────────────────────────────────────
def _draw_footer(canvas, company_name: str):
    canvas.setStrokeColor(R.LINE)
    canvas.setLineWidth(0.5)
    canvas.line(MARGIN, 1.25 * cm, W - MARGIN, 1.25 * cm)
    canvas.setFont('DejaVu', 8)
    canvas.setFillColor(R.FOOTER)
    canvas.drawString(MARGIN, 0.6 * cm, f'{company_name}  |  Weekly Report')
    canvas.drawCentredString(W / 2, 0.6 * cm, today_str())


# ── CHART + LEGEND SIDE BY SIDE ───────────────────────────────────────────────
def _chart_legend_row(chart_buf, legend_data: list,
                      chart_cm: float = 5.5) -> Table:
    """Return pie chart + legend side by side as a Table."""
    img    = Image(chart_buf, width=chart_cm * cm, height=chart_cm * cm)
    legend = _legend_table(legend_data, font_size=9)
    t = Table([[img, legend]],
              colWidths=[(chart_cm + 1) * cm, CONTENT_W - (chart_cm + 1) * cm])
    t.setStyle(TableStyle([
        ('VALIGN',        (0, 0), (-1, -1), 'MIDDLE'),
        ('LEFTPADDING',   (0, 0), (-1, -1), 0),
        ('RIGHTPADDING',  (0, 0), (-1, -1), 0),
        ('TOPPADDING',    (0, 0), (-1, -1), 0),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 0),
    ]))
    return t


def _two_chart_row(left_buf, left_title: str,
                   right_buf, right_title: str,
                   right_legend: list) -> list:
    """
    Return two charts (bar on left, pie on right) side by side.
    Returns: list of story elements
    """
    half_w = CONTENT_W / 2

    left_img = Image(left_buf, width=half_w - 0.5 * cm,
                     height=(half_w - 0.5 * cm) * (3.6 / 5.8))

    right_img_h = half_w - 0.5 * cm
    right_img   = Image(right_buf, width=right_img_h, height=right_img_h)
    right_leg   = _legend_table(right_legend, font_size=8)

    right_inner = Table(
        [[right_img], [right_leg]],
        colWidths=[half_w - 0.5 * cm],
    )
    right_inner.setStyle(TableStyle([
        ('VALIGN',        (0, 0), (-1, -1), 'MIDDLE'),
        ('ALIGN',         (0, 0), (-1, -1), 'CENTER'),
        ('LEFTPADDING',   (0, 0), (-1, -1), 0),
        ('RIGHTPADDING',  (0, 0), (-1, -1), 0),
        ('TOPPADDING',    (0, 0), (-1, -1), 0),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
    ]))

    top_row = Table(
        [[section_heading(left_title,  half_w)[1],
          section_heading(right_title, half_w)[1]]],
        colWidths=[half_w, half_w],
    )
    top_row.setStyle(TableStyle([
        ('LEFTPADDING',   (0, 0), (-1, -1), 0),
        ('RIGHTPADDING',  (0, 0), (-1, -1), 0),
        ('TOPPADDING',    (0, 0), (-1, -1), 0),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
    ]))

    bottom_row = Table(
        [[left_img, right_inner]],
        colWidths=[half_w, half_w],
    )
    bottom_row.setStyle(TableStyle([
        ('VALIGN',        (0, 0), (-1, -1), 'TOP'),
        ('LEFTPADDING',   (0, 0), (-1, -1), 0),
        ('RIGHTPADDING',  (0, 0), (-1, -1), 0),
        ('TOPPADDING',    (0, 0), (-1, -1), 0),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 0),
    ]))

    return [Spacer(1, 0.25 * cm), top_row, bottom_row]


# ── MAIN FUNCTION ─────────────────────────────────────────────────────────────
def generate_weekly_report(
    clients: list,
    receivables: list,
    week_end_str: str = None,
    company_name: str = 'Payment Tracking System',
    output_file: str = None,
) -> str:
    """
    Generate weekly receivables report PDF.

    Args:
        clients:      [{client_id, full_name, active, ...}]
        receivables:  [{client_id, receivable_no, project_name, work_description,
                        installment_no, total_amount, due_date, status,
                        paid_amount, remaining_amount, ...}]
        week_end_str: Last day of the week DD.MM.YYYY (defaults to Sunday of current week)
        output_file:  PDF output path (auto-generated if not provided)

    Returns:
        Full path to the generated PDF.
    """
    start, end = _week_range(week_end_str)
    period_str = f'{start.strftime("%d.%m.%Y")} – {end.strftime("%d.%m.%Y")}'

    if not output_file:
        date = start.strftime('%d_%m')
        os.makedirs(
            os.path.join(os.path.dirname(os.path.dirname(__file__)), 'reports'),
            exist_ok=True,
        )
        output_file = os.path.join(
            os.path.dirname(os.path.dirname(__file__)),
            'reports',
            f'weekly_report_{date}.pdf',
        )

    # ── Data calculation ──────────────────────────────────────────────────────
    clients_map = _clients_map(clients)

    def _fl(a, key):
        try:
            return float(str(a.get(key, 0)).replace(',', '.') or 0)
        except ValueError:
            return 0.0

    total_receivable = sum(_fl(a, 'total_amount')     for a in receivables)
    total_paid       = sum(_fl(a, 'paid_amount')      for a in receivables)
    total_remaining  = sum(_fl(a, 'remaining_amount') for a in receivables)
    collection_rate  = (total_paid / total_receivable * 100) if total_receivable > 0 else 0.0

    n_partial = sum(1 for a in receivables if 'partial' in a.get('status', '').lower()
                                           or 'kismi'   in a.get('status', '').lower()
                                           or 'kısmi'   in a.get('status', '').lower())
    n_paid    = sum(1 for a in receivables
                    if ('paid'    in a.get('status', '').lower() or
                        'odendi'  in a.get('status', '').lower() or
                        'ödendi'  in a.get('status', '').lower())
                    and 'partial' not in a.get('status', '').lower()
                    and 'kismi'   not in a.get('status', '').lower()
                    and 'kısmi'   not in a.get('status', '').lower())
    n_unpaid  = sum(1 for a in receivables if 'unpaid'   in a.get('status', '').lower()
                                           or 'odenmedi' in a.get('status', '').lower()
                                           or 'ödenmedi' in a.get('status', '').lower())

    due_week       = _due_this_week(receivables, clients_map, start, end)
    overdue_10, total_overdue_n = _overdue_receivables(receivables, clients_map, limit=10)
    overdue_counts = _overdue_categories(receivables)
    profile_dist   = _profile_distribution(clients, receivables)

    # ── Charts ────────────────────────────────────────────────────────────────
    status_buf, status_legend   = _status_pie(n_paid, n_partial, n_unpaid)
    profile_buf, profile_legend = _profile_pie(profile_dist)
    overdue_buf                 = _overdue_bar(overdue_counts)

    # ── Page callbacks ────────────────────────────────────────────────────────
    def _page1(canvas, doc):
        canvas.saveState()
        draw_header(canvas, company_name, 'Weekly Receivables Report', period_str)
        _draw_footer(canvas, company_name)
        canvas.restoreState()

    def _page2(canvas, doc):
        canvas.saveState()
        draw_header(canvas, company_name, 'Overdue Payments & Analysis', period_str)
        _draw_footer(canvas, company_name)
        canvas.restoreState()

    # ── Document ──────────────────────────────────────────────────────────────
    doc = SimpleDocTemplate(
        output_file,
        pagesize=A4,
        leftMargin=MARGIN, rightMargin=MARGIN,
        topMargin=3.1 * cm,
        bottomMargin=1.9 * cm,
    )
    story = []

    # ─────────────────────────────────────────────────────────────────────────
    # PAGE 1
    # ─────────────────────────────────────────────────────────────────────────

    # --- Summary stat cards ---
    story.append(_stats_row(total_receivable, total_paid,
                            total_remaining, collection_rate))
    story.append(Spacer(1, 0.5 * cm))

    # --- Payments due this week ---
    story.extend(section_heading('Payments Due This Week'))

    if due_week:
        story.append(_this_week_table(due_week))
    else:
        story.append(Paragraph(
            f'No payments are due within the period {period_str}.', S_NOTE
        ))

    story.append(Spacer(1, 0.5 * cm))

    # --- Status distribution ---
    story.extend(section_heading('Overall Status Distribution'))
    if status_buf:
        story.append(_chart_legend_row(status_buf, status_legend, chart_cm=5.0))
    else:
        story.append(Paragraph('No data available to display.', S_NOTE))

    # ─────────────────────────────────────────────────────────────────────────
    # PAGE 2
    # ─────────────────────────────────────────────────────────────────────────
    story.append(PageBreak())

    # --- Overdue payments table ---
    s_summary = ParagraphStyle('oz', fontName='DejaVu', fontSize=9,
                                textColor=R.TEXT_MUTED, leading=13, spaceAfter=6)
    if total_overdue_n > 10:
        summary_text = (f'Showing the top 10 most overdue records '
                        f'out of {total_overdue_n} total overdue receivables.')
    else:
        summary_text = f'There are {total_overdue_n} overdue receivable(s) in total.'

    story.extend(section_heading('Overdue Payments'))
    story.append(Paragraph(summary_text, s_summary))

    if overdue_10:
        overdue_tbl, overdue_remaining_total = _overdue_table(overdue_10, total_overdue_n)
        story.append(overdue_tbl)

        # Total remaining (for listed overdue records)
        s_total = ParagraphStyle('gt', fontName='DejaVu-Bold', fontSize=9.5,
                                  textColor=R.UNPAID, alignment=TA_RIGHT, leading=14)
        label = 'the listed 10 records' if total_overdue_n > 10 else 'overdue receivables'
        story.append(Spacer(1, 0.2 * cm))
        story.append(Paragraph(
            f'Total overdue remaining ({label}): {fmt_amount(overdue_remaining_total)}',
            s_total,
        ))
    else:
        story.append(Paragraph('There are no overdue receivables.', S_NOTE))

    story.append(Spacer(1, 0.5 * cm))

    # --- Two charts side by side ---
    if overdue_buf and profile_buf:
        for item in _two_chart_row(
            overdue_buf, 'Overdue Period Distribution',
            profile_buf, 'Client Profile Distribution',
            profile_legend,
        ):
            story.append(item)
    elif overdue_buf:
        story.extend(section_heading('Overdue Period Distribution'))
        story.append(Image(overdue_buf,
                           width=CONTENT_W * 0.6,
                           height=CONTENT_W * 0.6 * (3.6 / 5.8)))
    elif profile_buf:
        story.extend(section_heading('Client Profile Distribution'))
        story.append(_chart_legend_row(profile_buf, profile_legend))

    # ── Build ─────────────────────────────────────────────────────────────────
    doc.build(
        story,
        onFirstPage=_page1,
        onLaterPages=_page2,
        canvasmaker=PageNumberCanvas,
    )
    return output_file
