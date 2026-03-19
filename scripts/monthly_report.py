"""Payment Tracking System — Monthly Receivables Report

Page 1 — Summary overview, weekly collection trend (bar) + status distribution (pie)
Page 2 — Profile distribution (pie) + overdue distribution (bar), top 10 most overdue
Page 3 — All clients summary table (50+ clients, automatic page overflow)

Usage:
    from monthly_report import generate_monthly_report
    path = generate_monthly_report(clients, receivables, month=3, year=2026)
"""

import io
import os
import sys
import datetime
import calendar
from collections import defaultdict

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.ticker as mticker

from reportlab.lib import colors
from reportlab.lib.units import cm
from reportlab.lib.pagesizes import A4
from reportlab.lib.enums import TA_CENTER, TA_RIGHT, TA_LEFT
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
    calculate_profile, PageNumberCanvas, PROFILE_ORDER,
)

# ── CONSTANTS ─────────────────────────────────────────────────────────────────
_MONTHS = [
    'January', 'February', 'March', 'April', 'May', 'June',
    'July', 'August', 'September', 'October', 'November', 'December',
]

_HEX = {
    'paid':         '#27AE60',
    'partial':      '#E67E22',
    'unpaid':       '#E74C3C',
    'perfect':      '#27AE60',
    'reliable':     '#2ECC71',
    'uncertain':    '#F39C12',
    'risky':        '#E67E22',
    'problematic':  '#E74C3C',
}

OVERDUE_CATS  = ['1-7 days', '8-15 days', '16-30 days', '31-60 days', '60+ days']
OVERDUE_COLORS = ['#F39C12', '#E67E22', '#E74C3C', '#C0392B', '#7B241C']


# ── HELPERS ───────────────────────────────────────────────────────────────────
def _month_name(month: int) -> str:
    return _MONTHS[month - 1] if 1 <= month <= 12 else str(month)


def _fl(a: dict, key: str) -> float:
    try:
        return float(str(a.get(key, 0)).replace(',', '.') or 0)
    except ValueError:
        return 0.0


def _clients_map(clients: list) -> dict:
    return {k.get('client_id', ''): k for k in clients}


# ── DATA CALCULATIONS ─────────────────────────────────────────────────────────
def _weekly_trend(receivables: list, month: int, year: int) -> tuple:
    """
    Weekly paid amounts for receivables due in the given month.
    Returns: (amounts [w1,w2,w3,w4], labels [str,str,str,str])
    """
    last_day = calendar.monthrange(year, month)[1]
    intervals = [(1, 7), (8, 14), (15, 21), (22, last_day)]
    amounts = [0.0, 0.0, 0.0, 0.0]

    for a in receivables:
        due = parse_date(a.get('due_date', ''))
        if not due or due.month != month or due.year != year:
            continue
        paid = _fl(a, 'paid_amount')
        day = due.day
        for i, (start, end) in enumerate(intervals):
            if start <= day <= end:
                amounts[i] += paid
                break

    labels = [
        f'Week 1\n(01-07)',
        f'Week 2\n(08-14)',
        f'Week 3\n(15-21)',
        f'Week 4\n(22-{last_day:02d})',
    ]
    return amounts, labels


def _overdue_categories(receivables: list) -> list:
    counts = [0, 0, 0, 0, 0]
    for a in receivables:
        status = str(a.get('status', '')).lower()
        if 'paid' in status or 'odendi' in status or 'ödendi' in status:
            continue
        days_late = days_overdue(a.get('due_date', ''))
        if   days_late <=  0: continue
        elif days_late <=  7: counts[0] += 1
        elif days_late <= 15: counts[1] += 1
        elif days_late <= 30: counts[2] += 1
        elif days_late <= 60: counts[3] += 1
        else:                 counts[4] += 1
    return counts


def _profile_distribution(clients: list, receivables: list) -> dict:
    client_receivables = defaultdict(list)
    for a in receivables:
        client_receivables[a.get('client_id', '')].append(a)
    distribution = defaultdict(int)
    for k in clients:
        if str(k.get('active', 'Yes')).lower() == 'no':
            continue
        kid = k.get('client_id', '')
        p = calculate_profile(client_receivables.get(kid, []))
        distribution[p] += 1
    return dict(distribution)


def _overdue_receivables(receivables: list, clients_map: dict, limit: int = 10):
    result = []
    for a in receivables:
        status = str(a.get('status', '')).lower()
        if 'paid' in status or 'odendi' in status or 'ödendi' in status:
            continue
        days_late = days_overdue(a.get('due_date', ''))
        if days_late <= 0:
            continue
        k = clients_map.get(a.get('client_id', ''))
        client_name = k.get('full_name', a.get('client_id', '')) if k else a.get('client_id', '')
        result.append({**a, '_days_late': days_late, '_client_name': client_name})
    result.sort(key=lambda x: x['_days_late'], reverse=True)
    return result[:limit], len(result)


# ── CHARTS ────────────────────────────────────────────────────────────────────
def _pie_chart(values: list, labels: list, chart_colors: list):
    """Generic pie chart generator. Returns: BytesIO or None."""
    if not values or sum(values) == 0:
        return None
    fig, ax = plt.subplots(figsize=(4.2, 4.2), facecolor='none')
    ax.pie(
        values, colors=chart_colors,
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
    return buf


def _status_pie(n_paid: int, n_partial: int, n_unpaid: int):
    """Returns: (buf, legend_data)"""
    v, e, r = [], [], []
    if n_paid    > 0: v.append(n_paid);    e.append(f'Paid ({n_paid})');     r.append(_HEX['paid'])
    if n_partial > 0: v.append(n_partial); e.append(f'Partial ({n_partial})'); r.append(_HEX['partial'])
    if n_unpaid  > 0: v.append(n_unpaid);  e.append(f'Unpaid ({n_unpaid})');  r.append(_HEX['unpaid'])
    buf = _pie_chart(v, e, r)
    return buf, list(zip(e, r))


def _profile_pie(profile_dist: dict):
    """Returns: (buf, legend_data)"""
    color_map = dict(zip(
        ['Perfect', 'Reliable', 'Uncertain', 'Risky', 'Problematic'],
        [_HEX['perfect'], _HEX['reliable'], _HEX['uncertain'],
         _HEX['risky'], _HEX['problematic']],
    ))
    v, e, r = [], [], []
    for p in ['Perfect', 'Reliable', 'Uncertain', 'Risky', 'Problematic']:
        n = profile_dist.get(p, 0)
        if n > 0:
            v.append(n); e.append(f'{p} ({n})'); r.append(color_map[p])
    buf = _pie_chart(v, e, r)
    return buf, list(zip(e, r))


def _trend_bar(amounts: list, labels: list) -> io.BytesIO | None:
    """Weekly collection trend bar chart. Y axis: Thousands."""
    if sum(amounts) == 0:
        return None

    amounts_k = [t / 1000 for t in amounts]
    bar_colors = ['#3498DB' if t > 0 else '#BDC3C7' for t in amounts_k]

    fig, ax = plt.subplots(figsize=(5.5, 3.8), facecolor='none')
    bars = ax.bar(range(len(labels)), amounts_k,
                  color=bar_colors, edgecolor='white', linewidth=1.5, width=0.6)

    max_val = max(amounts_k) if max(amounts_k) > 0 else 1
    for bar, amount in zip(bars, amounts_k):
        if amount > 0:
            ax.text(
                bar.get_x() + bar.get_width() / 2,
                bar.get_height() + max_val * 0.03,
                f"{amount:,.0f}".replace(',', '.'),
                ha='center', va='bottom',
                fontsize=9, fontweight='bold', color='#2C3E50',
            )

    ax.set_xticks(range(len(labels)))
    ax.set_xticklabels(labels, fontsize=8.5)
    ax.set_ylabel('Thousands', fontsize=9)
    ax.yaxis.set_major_formatter(
        mticker.FuncFormatter(lambda x, _: f"{x:,.0f}".replace(',', '.'))
    )
    ax.set_ylim(0, max_val * 1.3)
    ax.spines['top'].set_visible(False)
    ax.spines['right'].set_visible(False)
    ax.tick_params(axis='both', labelsize=8.5)

    plt.tight_layout(pad=0.4)
    buf = io.BytesIO()
    fig.savefig(buf, format='png', dpi=200, bbox_inches='tight',
                transparent=True, facecolor='none')
    buf.seek(0)
    plt.close(fig)
    return buf


def _overdue_bar(counts: list) -> io.BytesIO | None:
    if sum(counts) == 0:
        return None
    fig, ax = plt.subplots(figsize=(5.5, 3.4), facecolor='none')
    bars = ax.bar(range(len(OVERDUE_CATS)), counts,
                  color=OVERDUE_COLORS, edgecolor='white', linewidth=1.5, width=0.6)
    max_val = max(counts) if max(counts) > 0 else 1
    for bar, count in zip(bars, counts):
        if count > 0:
            ax.text(
                bar.get_x() + bar.get_width() / 2,
                bar.get_height() + max_val * 0.03,
                str(count),
                ha='center', va='bottom',
                fontsize=10, fontweight='bold', color='#2C3E50',
            )
    ax.set_xticks(range(len(OVERDUE_CATS)))
    ax.set_xticklabels(OVERDUE_CATS, fontsize=9)
    ax.set_ylabel('Count', fontsize=9)
    ax.yaxis.set_major_locator(mticker.MaxNLocator(integer=True))
    ax.set_ylim(0, max_val * 1.35)
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


# ── LEGEND ────────────────────────────────────────────────────────────────────
def _legend_table(legend_data: list, font_size: int = 9) -> Table:
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
    t = Table(rows, colWidths=[0.65 * cm, 5.5 * cm])
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
    remaining_color = R.UNPAID if remaining > 0 else R.PAID
    rate_color = (R.UNPAID if collection_rate < 50 else
                  R.PARTIAL    if collection_rate < 80 else R.PAID)

    def card(value_str, label, color):
        return create_card(value_str, label, color, CONTENT_W / 4, value_fs=15)

    outer = Table([[
        card(fmt_amount(total),              'Total Receivable',   R.TEXT),
        card(fmt_amount(paid),               'Collected',          R.PAID),
        card(fmt_amount(remaining),          'Remaining',          remaining_color),
        card(f'%{collection_rate:.1f}',      'Collection Rate',    rate_color),
    ]], colWidths=[CONTENT_W / 4] * 4)
    outer.setStyle(TableStyle([
        ('LEFTPADDING',   (0, 0), (-1, -1), 0),
        ('RIGHTPADDING',  (0, 0), (-1, -1), 0),
        ('TOPPADDING',    (0, 0), (-1, -1), 0),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 0),
        ('VALIGN',        (0, 0), (-1, -1), 'TOP'),
    ]))
    return outer


def _overdue_table(overdue: list) -> Table:
    """Top 10 overdue receivables table."""
    col_widths = [w * cm for w in [1.5, 3.5, 3.2, 3.2, 3.0, 3.0]]
    headers    = ['Ref No', 'Client', 'Project', 'Description', 'Remaining', 'Days Late']
    rows = [[Paragraph(h, S_TH) for h in headers]]

    for a in overdue:
        days_late = a.get('_days_late', days_overdue(a.get('due_date', '')))
        if   days_late > 60: grc = '#7B241C'
        elif days_late > 30: grc = '#C0392B'
        elif days_late > 15: grc = '#E74C3C'
        else:                grc = '#E67E22'

        s_g = ParagraphStyle('gf', fontName='DejaVu-Bold', fontSize=9,
                              textColor=colors.HexColor(grc),
                              alignment=TA_CENTER, leading=12)

        installment  = a.get('installment_no', '')
        description = str(a.get('work_description', ''))
        if installment:
            description += f' T{installment}'

        rows.append([
            Paragraph(str(a.get('receivable_no', '')),        S_TD_C),
            Paragraph(a.get('_client_name', ''),              S_TD),
            Paragraph(str(a.get('project_name', '')),         S_TD),
            Paragraph(description,                            S_TD),
            Paragraph(fmt_amount(_fl(a, 'remaining_amount')), S_TD_R),
            Paragraph(f'{days_late} days',                    s_g),
        ])

    return _style_table(rows, col_widths)


def _clients_table(clients: list, receivables: list) -> Table:
    """
    All clients summary table (50+ clients, repeatRows=1).
    Profile column is shown with colored text.
    Sort order: Perfect → Reliable → Uncertain → Risky → Problematic;
    within the same profile, remaining_amount descending.
    """
    client_receivables = defaultdict(list)
    for a in receivables:
        client_receivables[a.get('client_id', '')].append(a)

    # Collect data first, then sort
    sorted_rows = []
    for k in clients:
        if str(k.get('active', 'Yes')).lower() == 'no':
            continue
        kid    = k.get('client_id', '')
        recs   = client_receivables.get(kid, [])
        if not recs:
            continue  # Skip clients with no receivables this month
        total     = sum(_fl(a, 'total_amount')     for a in recs)
        paid      = sum(_fl(a, 'paid_amount')      for a in recs)
        remaining = sum(_fl(a, 'remaining_amount') for a in recs)
        profile   = calculate_profile(recs)
        sorted_rows.append((k, total, paid, remaining, profile))
    sorted_rows.sort(key=lambda x: (PROFILE_ORDER.get(x[4], 99), -x[3]))

    col_widths = [w * cm for w in [3.6, 3.6, 2.7, 2.5, 2.5, 2.3]]
    headers    = ['Full Name', 'Company', 'Total', 'Paid', 'Remaining', 'Profile']
    rows = [[Paragraph(h, S_TH) for h in headers]]

    for k, total, paid, remaining, profile in sorted_rows:
        p_color, _ = profile_info(profile)

        # Remaining amount color
        remaining_color = R.UNPAID if remaining > 0 else R.PAID
        s_remaining = ParagraphStyle('sk', fontName='DejaVu', fontSize=8.5,
                                      textColor=remaining_color, alignment=TA_RIGHT, leading=12)
        # Profile color
        s_profile = ParagraphStyle('sp', fontName='DejaVu-Bold', fontSize=8.5,
                                    textColor=p_color, alignment=TA_CENTER, leading=12)

        s_td_sm = ParagraphStyle('sm', fontName='DejaVu', fontSize=8.5,
                                  textColor=R.TEXT, leading=12)
        s_td_r_sm = ParagraphStyle('sr', fontName='DejaVu', fontSize=8.5,
                                    textColor=R.TEXT, alignment=TA_RIGHT, leading=12)

        rows.append([
            Paragraph(k.get('full_name', ''), s_td_sm),
            Paragraph(k.get('company', '—'),  s_td_sm),
            Paragraph(fmt_amount(total),       s_td_r_sm),
            Paragraph(fmt_amount(paid),        s_td_r_sm),
            Paragraph(fmt_amount(remaining),   s_remaining),
            Paragraph(profile,                 s_profile),
        ])

    return _style_table(rows, col_widths, font_size=8.5, row_pad=5)


def _style_table(rows: list, col_widths: list,
                 font_size: float = 9, row_pad: int = 6) -> Table:
    t = Table(rows, colWidths=col_widths, repeatRows=1)
    style = [
        ('BACKGROUND',    (0,  0), (-1,  0), R.TH_BG),
        ('TOPPADDING',    (0,  0), (-1,  0), 8),
        ('BOTTOMPADDING', (0,  0), (-1,  0), 8),
        ('FONTNAME',      (0,  1), (-1, -1), 'DejaVu'),
        ('FONTSIZE',      (0,  1), (-1, -1), font_size),
        ('TOPPADDING',    (0,  1), (-1, -1), row_pad),
        ('BOTTOMPADDING', (0,  1), (-1, -1), row_pad),
        ('LEFTPADDING',   (0,  0), (-1, -1), 5),
        ('RIGHTPADDING',  (0,  0), (-1, -1), 5),
        ('VALIGN',        (0,  0), (-1, -1), 'MIDDLE'),
        ('LINEBELOW',     (0,  0), (-1, -1), 0.3, R.BORDER),
        ('BOX',           (0,  0), (-1, -1), 0.5, R.TH_BG),
    ]
    for i in range(1, len(rows)):
        if i % 2 == 0:
            style.append(('BACKGROUND', (0, i), (-1, i), R.ROW_ALT))
    t.setStyle(TableStyle(style))
    return t


# ── CHART + LEGEND SIDE BY SIDE ───────────────────────────────────────────────
def _pie_with_legend(buf, legend: list, pie_cm: float = 5.0,
                     legend_cm: float = None) -> Table:
    if legend_cm is None:
        legend_cm = CONTENT_W / cm - pie_cm - 1
    img = Image(buf, width=pie_cm * cm, height=pie_cm * cm)
    t = Table(
        [[img, _legend_table(legend)]],
        colWidths=[(pie_cm + 0.8) * cm, legend_cm * cm],
    )
    t.setStyle(TableStyle([
        ('VALIGN',        (0, 0), (-1, -1), 'MIDDLE'),
        ('LEFTPADDING',   (0, 0), (-1, -1), 0),
        ('RIGHTPADDING',  (0, 0), (-1, -1), 0),
        ('TOPPADDING',    (0, 0), (-1, -1), 0),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 0),
    ]))
    return t


def _trend_and_pie(trend_buf, trend_title: str,
                   pie_buf, pie_title: str, pie_legend: list) -> list:
    """Bar (left) + Pie (right) side by side."""
    half_w = CONTENT_W / 2

    trend_img = Image(trend_buf,
                      width=half_w - 0.5 * cm,
                      height=(half_w - 0.5 * cm) * (3.8 / 5.5))
    pie_img = Image(pie_buf,
                    width=half_w - 1.0 * cm,
                    height=half_w - 1.0 * cm)

    # Pie + legend stacked (inside right column)
    right = Table(
        [[pie_img], [_legend_table(pie_legend, font_size=8)]],
        colWidths=[half_w - 0.5 * cm],
    )
    right.setStyle(TableStyle([
        ('ALIGN',         (0, 0), (-1, -1), 'CENTER'),
        ('VALIGN',        (0, 0), (-1, -1), 'MIDDLE'),
        ('LEFTPADDING',   (0, 0), (-1, -1), 0),
        ('RIGHTPADDING',  (0, 0), (-1, -1), 0),
        ('TOPPADDING',    (0, 0), (-1, -1), 0),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 3),
    ]))

    headings = Table(
        [[section_heading(trend_title, half_w)[1],
          section_heading(pie_title, half_w)[1]]],
        colWidths=[half_w, half_w],
    )
    headings.setStyle(TableStyle([
        ('LEFTPADDING',   (0, 0), (-1, -1), 0),
        ('RIGHTPADDING',  (0, 0), (-1, -1), 0),
        ('TOPPADDING',    (0, 0), (-1, -1), 0),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
    ]))

    content = Table(
        [[trend_img, right]],
        colWidths=[half_w, half_w],
    )
    content.setStyle(TableStyle([
        ('VALIGN',        (0, 0), (-1, -1), 'TOP'),
        ('LEFTPADDING',   (0, 0), (-1, -1), 0),
        ('RIGHTPADDING',  (0, 0), (-1, -1), 0),
        ('TOPPADDING',    (0, 0), (-1, -1), 0),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 0),
    ]))

    return [Spacer(1, 0.25 * cm), headings, content]


def _overdue_and_profile(overdue_buf, profile_buf, profile_legend: list) -> list:
    """Overdue bar (left) + Profile pie (right) side by side."""
    half_w = CONTENT_W / 2

    overdue_img = Image(overdue_buf,
                        width=half_w - 0.5 * cm,
                        height=(half_w - 0.5 * cm) * (3.4 / 5.5))
    pie_img = Image(profile_buf,
                    width=half_w - 1.0 * cm,
                    height=half_w - 1.0 * cm)

    right = Table(
        [[pie_img], [_legend_table(profile_legend, font_size=8)]],
        colWidths=[half_w - 0.5 * cm],
    )
    right.setStyle(TableStyle([
        ('ALIGN',         (0, 0), (-1, -1), 'CENTER'),
        ('VALIGN',        (0, 0), (-1, -1), 'MIDDLE'),
        ('LEFTPADDING',   (0, 0), (-1, -1), 0),
        ('RIGHTPADDING',  (0, 0), (-1, -1), 0),
        ('TOPPADDING',    (0, 0), (-1, -1), 0),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 3),
    ]))

    headings = Table(
        [[section_heading('Overdue Period Distribution', half_w)[1],
          section_heading('Client Profile Distribution', half_w)[1]]],
        colWidths=[half_w, half_w],
    )
    headings.setStyle(TableStyle([
        ('LEFTPADDING',   (0, 0), (-1, -1), 0),
        ('RIGHTPADDING',  (0, 0), (-1, -1), 0),
        ('TOPPADDING',    (0, 0), (-1, -1), 0),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
    ]))

    content = Table(
        [[overdue_img, right]],
        colWidths=[half_w, half_w],
    )
    content.setStyle(TableStyle([
        ('VALIGN',        (0, 0), (-1, -1), 'TOP'),
        ('LEFTPADDING',   (0, 0), (-1, -1), 0),
        ('RIGHTPADDING',  (0, 0), (-1, -1), 0),
        ('TOPPADDING',    (0, 0), (-1, -1), 0),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 0),
    ]))

    return [Spacer(1, 0.25 * cm), headings, content]


# ── HEADER / FOOTER ───────────────────────────────────────────────────────────
def _draw_footer(canvas, company_name: str, month_str: str):
    canvas.setStrokeColor(R.LINE)
    canvas.setLineWidth(0.5)
    canvas.line(MARGIN, 1.25 * cm, W - MARGIN, 1.25 * cm)
    canvas.setFont('DejaVu', 8)
    canvas.setFillColor(R.FOOTER)
    canvas.drawString(MARGIN, 0.6 * cm,
                      f'{company_name}  |  Monthly Report  |  {month_str}')
    canvas.drawCentredString(W / 2, 0.6 * cm, today_str())


# ── MAIN FUNCTION ─────────────────────────────────────────────────────────────
def generate_monthly_report(
    clients: list,
    receivables: list,
    month: int = None,
    year: int = None,
    company_name: str = 'Payment Tracking System',
    output_file: str = None,
) -> str:
    """
    Generates a monthly receivables report PDF.

    Args:
        clients:     [{client_id, full_name, company, active, ...}]
        receivables: [{client_id, receivable_no, project_name, work_description,
                       installment_no, total_amount, due_date, status, paid_amount,
                       remaining_amount, ...}]
        month:       Report month 1-12 (defaults to previous month if not given)
        year:        Report year (defaults to current year if not given)
        output_file: PDF output path (auto-generated if not given)

    Returns:
        Full path of the generated PDF.
    """
    today = datetime.date.today()
    if month is None or year is None:
        prev = today.replace(day=1) - datetime.timedelta(days=1)
        month = month or prev.month
        year  = year  or prev.year

    month_str = f'{_month_name(month)} {year}'

    if not output_file:
        os.makedirs(
            os.path.join(os.path.dirname(os.path.dirname(__file__)), 'raporlar'),
            exist_ok=True,
        )
        output_file = os.path.join(
            os.path.dirname(os.path.dirname(__file__)),
            'raporlar',
            f'monthly_report_{month:02d}_{year}.pdf',
        )

    # ── Data ──────────────────────────────────────────────────────────────────
    clients_map = _clients_map(clients)

    total_receivable = sum(_fl(a, 'total_amount')     for a in receivables)
    total_paid       = sum(_fl(a, 'paid_amount')      for a in receivables)
    total_remaining  = sum(_fl(a, 'remaining_amount') for a in receivables)
    collection_rate  = (total_paid / total_receivable * 100) if total_receivable > 0 else 0.0

    n_partial = sum(1 for a in receivables if 'kismi'    in a.get('status', '').lower()
                                           or 'kısmi'    in a.get('status', '').lower())
    n_paid    = sum(1 for a in receivables
                   if ('odendi'  in a.get('status', '').lower() or
                       'ödendi'  in a.get('status', '').lower())
                   and 'kismi'  not in a.get('status', '').lower()
                   and 'kısmi'  not in a.get('status', '').lower())
    n_unpaid  = sum(1 for a in receivables if 'odenmedi' in a.get('status', '').lower()
                                           or 'ödenmedi' in a.get('status', '').lower())

    trend_amounts, trend_labels = _weekly_trend(receivables, month, year)
    overdue_counts  = _overdue_categories(receivables)
    profile_dist    = _profile_distribution(clients, receivables)
    top10_overdue, total_overdue_n = _overdue_receivables(receivables, clients_map, limit=10)

    # ── Charts ────────────────────────────────────────────────────────────────
    status_buf,  status_leg  = _status_pie(n_paid, n_partial, n_unpaid)
    profile_buf, profile_leg = _profile_pie(profile_dist)
    trend_buf  = _trend_bar(trend_amounts, trend_labels)
    overdue_buf = _overdue_bar(overdue_counts)

    # ── Page Callbacks ────────────────────────────────────────────────────────
    def _page1(canvas, doc):
        canvas.saveState()
        draw_header(canvas, company_name, 'Monthly Receivables Report', month_str)
        _draw_footer(canvas, company_name, month_str)
        canvas.restoreState()

    def _page_later(canvas, doc):
        canvas.saveState()
        if doc.page == 2:
            title = 'Detailed Analysis'
        else:
            title = 'Client Summary'
        draw_header(canvas, company_name, title, month_str)
        _draw_footer(canvas, company_name, month_str)
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
    # PAGE 1 — Monthly Summary
    # ─────────────────────────────────────────────────────────────────────────
    story.append(_stats_row(total_receivable, total_paid,
                            total_remaining, collection_rate))
    story.append(Spacer(1, 0.5 * cm))

    # Trend (left) + Status Distribution (right)
    if trend_buf and status_buf:
        for item in _trend_and_pie(
            trend_buf, 'Weekly Collection Trend',
            status_buf, 'Status Distribution', status_leg,
        ):
            story.append(item)
    elif trend_buf:
        story.extend(section_heading('Weekly Collection Trend'))
        story.append(Image(trend_buf, width=CONTENT_W * 0.6,
                           height=CONTENT_W * 0.6 * (3.8 / 5.5)))
    elif status_buf:
        story.extend(section_heading('Status Distribution'))
        story.append(_pie_with_legend(status_buf, status_leg))
    else:
        story.append(Paragraph('No chart data available for this month.', S_NOTE))

    # ─────────────────────────────────────────────────────────────────────────
    # PAGE 2 — Detailed Analysis
    # ─────────────────────────────────────────────────────────────────────────
    story.append(PageBreak())

    # Overdue bar (left) + Profile pie (right)
    if overdue_buf and profile_buf:
        for item in _overdue_and_profile(overdue_buf, profile_buf, profile_leg):
            story.append(item)
    elif profile_buf:
        story.extend(section_heading('Client Profile Distribution'))
        story.append(_pie_with_legend(profile_buf, profile_leg))

    story.append(Spacer(1, 0.4 * cm))

    # Top 10 most overdue
    s_summary = ParagraphStyle('oz', fontName='DejaVu', fontSize=9,
                                textColor=R.TEXT_MUTED, leading=13, spaceAfter=6)
    summary_text = (
        f'Showing the top 10 most overdue records out of {total_overdue_n} total overdue receivables.'
        if total_overdue_n > 10
        else f'There are {total_overdue_n} overdue receivable(s) in total.'
    )

    story.extend(section_heading('Most Overdue Receivables'))
    story.append(Paragraph(summary_text, s_summary))

    if top10_overdue:
        story.append(_overdue_table(top10_overdue))
        # Total overdue remaining
        overdue_total = sum(_fl(a, 'remaining_amount') for a in top10_overdue)
        s_total = ParagraphStyle('gt', fontName='DejaVu-Bold', fontSize=9.5,
                                  textColor=R.UNPAID, alignment=TA_RIGHT, leading=14)
        label = 'the listed 10 records' if total_overdue_n > 10 else 'all overdue receivables'
        story.append(Spacer(1, 0.2 * cm))
        story.append(Paragraph(
            f'Total overdue remaining ({label}): {fmt_amount(overdue_total)}', s_total
        ))
    else:
        story.append(Paragraph('No past-due receivables found.', S_NOTE))

    # ─────────────────────────────────────────────────────────────────────────
    # PAGE 3 — Client Summary (50+ clients, automatic overflow)
    # ─────────────────────────────────────────────────────────────────────────
    story.append(PageBreak())

    month_client_ids = {a.get('client_id') for a in receivables if a.get('client_id')}
    active_count = len(month_client_ids)
    s_info = ParagraphStyle('ki', fontName='DejaVu', fontSize=9,
                             textColor=R.TEXT_MUTED, leading=13, spaceAfter=6)
    story.append(Paragraph(f'Number of clients with receivables this month: {active_count}', s_info))
    story.append(Spacer(1, 0.15 * cm))
    story.append(_clients_table(clients, receivables))

    # ── Build ─────────────────────────────────────────────────────────────────
    doc.build(
        story,
        onFirstPage=_page1,
        onLaterPages=_page_later,
        canvasmaker=PageNumberCanvas,
    )
    return output_file
