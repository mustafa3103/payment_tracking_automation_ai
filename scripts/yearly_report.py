"""Payment Tracking System — Yearly Receivables Report

Page 1 — Yearly summary: KPI cards + statistics row + status pie + profile pie
Page 2 — Monthly collection trend: grouped bar (expected vs collected) + monthly comparison table
Page 3 — Risk analysis: overdue duration bar + top 15 most overdue receivables
Page 4+ — Client-by-client summary (profile-ordered: Perfect → Problematic; same profile sorted by remaining ↓)

Usage:
    from yearly_report import generate_yearly_report
    path = generate_yearly_report(clients, receivables, year=2026, company_name='ABC Construction Ltd.')
"""

import io
import os
import sys
import datetime
from collections import defaultdict

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.ticker as mticker
import matplotlib.patches as mpatches

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
    R, PROFILES, PROFILE_COLORS, PROFILE_ORDER,
    S_TH, S_TD, S_TD_C, S_TD_R, S_NOTE,
    section_heading, create_card, draw_header,
    fmt_amount, fmt_date, days_overdue, parse_date,
    status_color, profile_info, today_str,
    calculate_profile, PageNumberCanvas,
)

# ── CONSTANTS ──────────────────────────────────────────────────────────────────
_MONTHS = ['January', 'February', 'March', 'April', 'May', 'June',
           'July', 'August', 'September', 'October', 'November', 'December']
_MONTHS_SHORT = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
                 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']

_ACCENT     = '#1ABC9C'   # teal — corporate accent color
_BEKLENEN_C = '#3498DB'   # blue — expected amount
_TAHSIL_C   = '#27AE60'   # green — collected amount

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

OVERDUE_CATS = ['1-7 days', '8-15 days', '16-30 days', '31-60 days', '60+ days']
OVERDUE_RENK = ['#F39C12', '#E67E22', '#E74C3C', '#C0392B', '#7B241C']


# ── HELPERS ────────────────────────────────────────────────────────────────────
def _month_name(month: int) -> str:
    return _MONTHS[month - 1] if 1 <= month <= 12 else str(month)


def _fl(a: dict, key: str) -> float:
    try:
        return float(str(a.get(key, 0) or 0).replace(',', '.'))
    except (ValueError, TypeError):
        return 0.0


def _clients_map(clients: list) -> dict:
    return {k.get('client_id', ''): k for k in clients}


# ── DATA CALCULATIONS ──────────────────────────────────────────────────────────
def _monthly_trend(receivables: list, year: int) -> tuple:
    """
    For each month: (expected_total, collected) — filtered to year, 12-element lists.
    expected: sum of total_amount for receivables due in that month
    collected: sum of paid_amount for receivables due in that month
    """
    expected  = [0.0] * 12
    collected = [0.0] * 12
    for a in receivables:
        due = parse_date(a.get('due_date', ''))
        if not due or due.year != year:
            continue
        m = due.month - 1
        expected[m]  += _fl(a, 'total_amount')
        collected[m] += _fl(a, 'paid_amount')
    return expected, collected


def _monthly_summary_data(receivables: list, year: int) -> list:
    """Summary row data for all 12 months — months with no data show zeros."""
    months = {m: {'total': 0.0, 'paid': 0.0,
                  'remaining': 0.0, 'count': 0, 'overdue': 0} for m in range(1, 13)}
    for a in receivables:
        due = parse_date(a.get('due_date', ''))
        if not due or due.year != year:
            continue
        m = due.month
        months[m]['total']     += _fl(a, 'total_amount')
        months[m]['paid']      += _fl(a, 'paid_amount')
        months[m]['remaining'] += _fl(a, 'remaining_amount')
        months[m]['count']     += 1
        d = str(a.get('status', '')).lower()
        if 'odendi' not in d and days_overdue(a.get('due_date', '')) > 0:
            months[m]['overdue'] += 1

    rows = []
    for m in range(1, 13):
        d = months[m]
        # rate: calculate if data exists, otherwise None (shown as '—' in table cell)
        rate = (d['paid'] / d['total'] * 100) if d['total'] > 0 else None
        rows.append([_month_name(m), d['count'], d['total'],
                     d['paid'], d['remaining'], rate, d['overdue']])
    return rows


def _overdue_categories(receivables: list) -> list:
    counts = [0, 0, 0, 0, 0]
    for a in receivables:
        status = str(a.get('status', '')).lower()
        if 'odendi' in status or 'ödendi' in status:
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
    dist = defaultdict(int)
    for k in clients:
        active = str(k.get('active', 'Yes')).lower()
        if active == 'no':
            continue
        p = calculate_profile(client_receivables.get(k.get('client_id', ''), []))
        dist[p] += 1
    return dict(dist)


def _overdue_receivables(receivables: list, clients_map: dict, limit: int = 15):
    result = []
    for a in receivables:
        status = str(a.get('status', '')).lower()
        if 'odendi' in status or 'ödendi' in status:
            continue
        days_late = days_overdue(a.get('due_date', ''))
        if days_late <= 0:
            continue
        k = clients_map.get(a.get('client_id', ''))
        client_name = k.get('full_name', a.get('client_id', '')) if k else a.get('client_id', '')
        result.append({**a, '_days_late': days_late, '_client_name': client_name})
    result.sort(key=lambda x: x['_days_late'], reverse=True)
    return result[:limit], len(result)


def _sorted_client_profiles(clients: list, receivables: list, year: int = None) -> list:
    """
    Returns active clients ordered by profile:
    Perfect → Reliable → Uncertain → Risky → Problematic
    Same profile: remaining_amount high to low.
    If year is given, only clients with receivables in that year are included.
    Returns: [(client_dict, total, paid, remaining, profile_str), ...]
    """
    client_receivables = defaultdict(list)
    for a in receivables:
        if year is not None:
            due = parse_date(a.get('due_date', ''))
            if not due or due.year != year:
                continue
        client_receivables[a.get('client_id', '')].append(a)

    result = []
    for k in clients:
        if str(k.get('active', 'Yes')).lower() == 'no':
            continue
        kid       = k.get('client_id', '')
        recs      = client_receivables.get(kid, [])
        if not recs:
            continue  # Skip clients with no receivables in this year
        total     = sum(_fl(a, 'total_amount')     for a in recs)
        paid      = sum(_fl(a, 'paid_amount')      for a in recs)
        remaining = sum(_fl(a, 'remaining_amount') for a in recs)
        profile   = calculate_profile(recs)
        result.append((k, total, paid, remaining, profile))

    result.sort(key=lambda x: (PROFILE_ORDER.get(x[4], 99), -x[3]))
    return result


# ── CHARTS ────────────────────────────────────────────────────────────────────
def _pie_chart(values: list, labels: list, chart_colors: list):
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
    v, e, r = [], [], []
    if n_paid    > 0: v.append(n_paid);    e.append(f'Paid ({n_paid})');     r.append(_HEX['paid'])
    if n_partial > 0: v.append(n_partial); e.append(f'Partial ({n_partial})'); r.append(_HEX['partial'])
    if n_unpaid  > 0: v.append(n_unpaid);  e.append(f'Unpaid ({n_unpaid})');  r.append(_HEX['unpaid'])
    return _pie_chart(v, e, r), list(zip(e, r))


def _profile_pie(profile_dist: dict):
    color_map = {
        'Perfect':     _HEX['perfect'],
        'Reliable':    _HEX['reliable'],
        'Uncertain':   _HEX['uncertain'],
        'Risky':       _HEX['risky'],
        'Problematic': _HEX['problematic'],
    }
    v, e, r = [], [], []
    for p in ['Perfect', 'Reliable', 'Uncertain', 'Risky', 'Problematic']:
        n = profile_dist.get(p, 0)
        if n > 0:
            v.append(n); e.append(f'{p} ({n})'); r.append(color_map[p])
    return _pie_chart(v, e, r), list(zip(e, r))


def _monthly_grouped_bar(expected: list, collected: list) -> io.BytesIO | None:
    """
    12-month grouped bar: blue = Expected (total_amount), green = Collected.
    Returns None if both lists are empty; months with partial data show 0.
    """
    if sum(expected) == 0 and sum(collected) == 0:
        return None

    exp_k   = [t / 1000 for t in expected]
    col_k   = [t / 1000 for t in collected]
    max_val = max(max(exp_k), max(col_k)) if any(exp_k + col_k) else 1

    bar_width = 0.38
    x = list(range(12))

    fig, ax = plt.subplots(figsize=(10, 4.0), facecolor='none')

    b1 = ax.bar([i - bar_width / 2 for i in x], exp_k, width=bar_width,
                color=_BEKLENEN_C, edgecolor='white', linewidth=0.8, alpha=0.80, zorder=2)
    b2 = ax.bar([i + bar_width / 2 for i in x], col_k, width=bar_width,
                color=_TAHSIL_C,   edgecolor='white', linewidth=0.8, alpha=0.95, zorder=2)

    # Value labels (only for bars > 0)
    for bar, amount in zip(b1, exp_k):
        if amount > 0:
            ax.text(bar.get_x() + bar.get_width() / 2,
                    bar.get_height() + max_val * 0.025,
                    f"{amount:,.0f}".replace(',', '.'),
                    ha='center', va='bottom', fontsize=6.5,
                    color='#2C3E50', fontweight='bold')
    for bar, amount in zip(b2, col_k):
        if amount > 0:
            ax.text(bar.get_x() + bar.get_width() / 2,
                    bar.get_height() + max_val * 0.025,
                    f"{amount:,.0f}".replace(',', '.'),
                    ha='center', va='bottom', fontsize=6.5,
                    color='#1a7a40', fontweight='bold')

    ax.set_xticks(x)
    ax.set_xticklabels(_MONTHS_SHORT, fontsize=9)
    ax.set_ylabel('Thousand', fontsize=9)
    ax.yaxis.set_major_formatter(
        mticker.FuncFormatter(lambda v, _: f"{v:,.0f}".replace(',', '.'))
    )
    ax.set_ylim(0, max_val * 1.30)
    ax.spines['top'].set_visible(False)
    ax.spines['right'].set_visible(False)
    ax.tick_params(axis='both', labelsize=8.5)
    ax.grid(axis='y', linestyle='--', alpha=0.3, zorder=0)

    leg = [
        mpatches.Patch(color=_BEKLENEN_C, alpha=0.80, label='Expected'),
        mpatches.Patch(color=_TAHSIL_C,   alpha=0.95, label='Collected'),
    ]
    ax.legend(handles=leg, fontsize=9, framealpha=0, loc='upper right')

    plt.tight_layout(pad=0.4)
    buf = io.BytesIO()
    fig.savefig(buf, format='png', dpi=200, bbox_inches='tight',
                transparent=True, facecolor='none')
    buf.seek(0)
    plt.close(fig)
    return buf


def _overdue_bar(overdue_counts: list) -> io.BytesIO | None:
    if sum(overdue_counts) == 0:
        return None
    fig, ax = plt.subplots(figsize=(5.5, 3.4), facecolor='none')
    bars = ax.bar(range(len(OVERDUE_CATS)), overdue_counts,
                  color=OVERDUE_RENK, edgecolor='white', linewidth=1.5, width=0.6, zorder=2)
    max_val = max(overdue_counts) if max(overdue_counts) > 0 else 1
    for bar, count in zip(bars, overdue_counts):
        if count > 0:
            ax.text(bar.get_x() + bar.get_width() / 2,
                    bar.get_height() + max_val * 0.03,
                    str(count), ha='center', va='bottom',
                    fontsize=10, fontweight='bold', color='#2C3E50')
    ax.set_xticks(range(len(OVERDUE_CATS)))
    ax.set_xticklabels(OVERDUE_CATS, fontsize=9)
    ax.set_ylabel('Count', fontsize=9)
    ax.yaxis.set_major_locator(mticker.MaxNLocator(integer=True))
    ax.set_ylim(0, max_val * 1.35)
    ax.spines['top'].set_visible(False)
    ax.spines['right'].set_visible(False)
    ax.grid(axis='y', linestyle='--', alpha=0.3, zorder=0)
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


def _pie_block(buf, legend: list, col_w: float, pie_cm: float = 6.5) -> Table:
    """Pie chart centered at top, legend below — vertical layout."""
    img = Image(buf, width=pie_cm * cm, height=pie_cm * cm)
    img_row = Table([[img]], colWidths=[col_w])
    img_row.setStyle(TableStyle([
        ('ALIGN',         (0, 0), (0, 0), 'CENTER'),
        ('VALIGN',        (0, 0), (0, 0), 'MIDDLE'),
        ('TOPPADDING',    (0, 0), (0, 0), 0),
        ('BOTTOMPADDING', (0, 0), (0, 0), 6),
        ('LEFTPADDING',   (0, 0), (0, 0), 0),
        ('RIGHTPADDING',  (0, 0), (0, 0), 0),
    ]))
    s_leg = ParagraphStyle('_pl', fontName='DejaVu', fontSize=9,
                            textColor=R.TEXT, leading=13)
    leg_rows = []
    for label, hex_color in legend:
        box = Table([['']], colWidths=[0.38 * cm], rowHeights=[0.38 * cm])
        box.setStyle(TableStyle([
            ('BACKGROUND',    (0, 0), (0, 0), colors.HexColor(hex_color)),
            ('TOPPADDING',    (0, 0), (0, 0), 0),
            ('BOTTOMPADDING', (0, 0), (0, 0), 0),
            ('LEFTPADDING',   (0, 0), (0, 0), 0),
            ('RIGHTPADDING',  (0, 0), (0, 0), 0),
        ]))
        leg_rows.append([box, Paragraph(label, s_leg)])
    leg_t = Table(leg_rows, colWidths=[0.58 * cm, col_w - 0.58 * cm])
    leg_t.setStyle(TableStyle([
        ('VALIGN',        (0, 0), (-1, -1), 'MIDDLE'),
        ('TOPPADDING',    (0, 0), (-1, -1), 4),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
        ('LEFTPADDING',   (0, 0), (-1, -1), 0),
        ('RIGHTPADDING',  (0, 0), (-1, -1), 0),
        ('ALIGN',         (0, 0), (0, -1), 'CENTER'),
    ]))
    block = Table([[img_row], [leg_t]], colWidths=[col_w])
    block.setStyle(TableStyle([
        ('TOPPADDING',    (0, 0), (-1, -1), 0),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 0),
        ('LEFTPADDING',   (0, 0), (-1, -1), 0),
        ('RIGHTPADDING',  (0, 0), (-1, -1), 0),
    ]))
    return block


def _pie_and_legend(buf, legend: list, pie_cm: float = 5.0,
                    legend_cm: float = None) -> Table:
    if legend_cm is None:
        legend_cm = CONTENT_W / cm - pie_cm - 1
    img = Image(buf, width=pie_cm * cm, height=pie_cm * cm)
    t = Table([[img, _legend_table(legend)]],
              colWidths=[(pie_cm + 0.8) * cm, legend_cm * cm])
    t.setStyle(TableStyle([
        ('VALIGN',        (0, 0), (-1, -1), 'MIDDLE'),
        ('LEFTPADDING',   (0, 0), (-1, -1), 0),
        ('RIGHTPADDING',  (0, 0), (-1, -1), 0),
        ('TOPPADDING',    (0, 0), (-1, -1), 0),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 0),
    ]))
    return t


# ── TABLE COMPONENTS ──────────────────────────────────────────────────────────
def _styled_table(rows: list, col_widths: list,
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


def _kpi_row(total_receivables: float, total_paid: float, total_remaining: float,
             collection_rate: float) -> Table:
    """4 large KPI cards."""
    remaining_color = R.UNPAID if total_remaining > 0 else R.PAID
    rate_color      = (R.UNPAID if collection_rate < 50 else
                       R.PARTIAL    if collection_rate < 80 else R.PAID)

    def card(value_str, label, color):
        return create_card(value_str, label, color, CONTENT_W / 4, value_fs=14)

    outer = Table([[
        card(fmt_amount(total_receivables), 'Total Receivables', R.TEXT),
        card(fmt_amount(total_paid),        'Collected',         R.PAID),
        card(fmt_amount(total_remaining),   'Remaining',         remaining_color),
        card(f'%{collection_rate:.1f}',     'Collection Rate',   rate_color),
    ]], colWidths=[CONTENT_W / 4] * 4)
    outer.setStyle(TableStyle([
        ('LEFTPADDING',   (0, 0), (-1, -1), 0),
        ('RIGHTPADDING',  (0, 0), (-1, -1), 0),
        ('TOPPADDING',    (0, 0), (-1, -1), 0),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 0),
        ('VALIGN',        (0, 0), (-1, -1), 'TOP'),
    ]))
    return outer


def _statistics_row(n_clients: int, n_records: int,
                    n_paid: int, n_partial: int,
                    n_unpaid: int, n_overdue: int) -> Table:
    """6 small count cards."""
    def card(value_str, label, color):
        return create_card(value_str, label, color, CONTENT_W / 6,
                           value_fs=16, label_fs=8, padding_v=8)

    outer = Table([[
        card(str(n_clients), 'Clients',         R.TEXT),
        card(str(n_records), 'Total Records',   R.TEXT),
        card(str(n_paid),    'Paid',            R.PAID),
        card(str(n_partial), 'Partial',         R.PARTIAL),
        card(str(n_unpaid),  'Unpaid',          R.UNPAID),
        card(str(n_overdue), 'Overdue',         R.UNPAID),
    ]], colWidths=[CONTENT_W / 6] * 6)
    outer.setStyle(TableStyle([
        ('LEFTPADDING',   (0, 0), (-1, -1), 0),
        ('RIGHTPADDING',  (0, 0), (-1, -1), 0),
        ('TOPPADDING',    (0, 0), (-1, -1), 0),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 0),
        ('VALIGN',        (0, 0), (-1, -1), 'TOP'),
    ]))
    return outer


def _overdue_table(top_overdue: list) -> Table:
    col_widths = [w * cm for w in [1.5, 3.5, 3.2, 3.0, 2.8, 3.0]]
    rows = [[Paragraph(b, S_TH) for b in
             ['Code', 'Client', 'Project', 'Description', 'Remaining', 'Overdue']]]

    for a in top_overdue:
        days_late = a.get('_days_late', days_overdue(a.get('due_date', '')))
        grc       = ('#7B241C' if days_late > 60 else '#C0392B' if days_late > 30
                     else '#E74C3C' if days_late > 15 else '#E67E22')
        s_g       = ParagraphStyle('gf', fontName='DejaVu-Bold', fontSize=9,
                                   textColor=colors.HexColor(grc),
                                   alignment=TA_CENTER, leading=12)
        installment  = a.get('installment_no', '')
        description  = str(a.get('work_description', ''))
        if installment:
            description += f' T{installment}'
        rows.append([
            Paragraph(str(a.get('receivable_no', '')),         S_TD_C),
            Paragraph(a.get('_client_name', ''),               S_TD),
            Paragraph(str(a.get('project_name', '')),          S_TD),
            Paragraph(description,                             S_TD),
            Paragraph(fmt_amount(_fl(a, 'remaining_amount')),  S_TD_R),
            Paragraph(f'{days_late} days',                     s_g),
        ])
    return _styled_table(rows, col_widths)


def _monthly_summary_table(summary_rows: list) -> Table:
    col_widths = [w * cm for w in [2.2, 1.4, 3.5, 3.5, 3.5, 1.8, 1.5]]
    rows = [[Paragraph(b, S_TH) for b in
             ['Month', 'Records', 'Expected', 'Paid', 'Remaining', 'Rate', 'Overdue']]]

    s_empty = ParagraphStyle('bos', fontName='DejaVu', fontSize=9,
                              textColor=R.TEXT_MUTED, alignment=TA_CENTER, leading=13)
    for r in summary_rows:
        month_name, count, total, paid, remaining, rate, overdue = r
        is_empty = (count == 0)

        if is_empty:
            rows.append([
                Paragraph(month_name, S_TD),
                Paragraph('—', s_empty),
                Paragraph('—', s_empty),
                Paragraph('—', s_empty),
                Paragraph('—', s_empty),
                Paragraph('—', s_empty),
                Paragraph('—', s_empty),
            ])
        else:
            rate_color      = (R.UNPAID if rate < 50 else
                               R.PARTIAL    if rate < 80 else R.PAID)
            remaining_color = R.UNPAID if remaining > 0 else R.PAID
            s_rate      = ParagraphStyle('or', fontName='DejaVu-Bold', fontSize=9,
                                          textColor=rate_color, alignment=TA_CENTER, leading=13)
            s_remaining = ParagraphStyle('ka', fontName='DejaVu', fontSize=9,
                                          textColor=remaining_color, alignment=TA_RIGHT, leading=13)
            rows.append([
                Paragraph(month_name,          S_TD),
                Paragraph(str(count),          S_TD_C),
                Paragraph(fmt_amount(total),   S_TD_R),
                Paragraph(fmt_amount(paid),    S_TD_R),
                Paragraph(fmt_amount(remaining), s_remaining),
                Paragraph(f'%{rate:.0f}',      s_rate),
                Paragraph(str(overdue) if overdue > 0 else '—', S_TD_C),
            ])
    return _styled_table(rows, col_widths, font_size=8.5, row_pad=5)


def _yearly_trend_data(receivables: list) -> tuple:
    """Group by year: (expected, collected) per year."""
    year_data: dict = defaultdict(lambda: {'expected': 0.0, 'collected': 0.0})
    for a in receivables:
        due = parse_date(a.get('due_date', ''))
        if not due:
            continue
        year_data[due.year]['expected']  += _fl(a, 'total_amount')
        year_data[due.year]['collected'] += _fl(a, 'paid_amount')
    if not year_data:
        return [], [], []
    years = sorted(year_data.keys())
    return years, [year_data[y]['expected'] for y in years], [year_data[y]['collected'] for y in years]


def _yearly_grouped_bar(years: list, expected: list, collected: list) -> 'io.BytesIO | None':
    """Yearly grouped bar: blue = Expected, green = Collected."""
    if not years or (sum(expected) == 0 and sum(collected) == 0):
        return None

    exp_k   = [t / 1000 for t in expected]
    col_k   = [t / 1000 for t in collected]
    max_val = max(max(exp_k), max(col_k)) if any(exp_k + col_k) else 1

    bar_width = 0.35
    x = list(range(len(years)))

    fig, ax = plt.subplots(figsize=(10, 4.0), facecolor='none')

    b1 = ax.bar([i - bar_width / 2 for i in x], exp_k, width=bar_width,
                color=_BEKLENEN_C, edgecolor='white', linewidth=0.8, alpha=0.80, zorder=2)
    b2 = ax.bar([i + bar_width / 2 for i in x], col_k, width=bar_width,
                color=_TAHSIL_C,   edgecolor='white', linewidth=0.8, alpha=0.95, zorder=2)

    for bar, amount in zip(b1, exp_k):
        if amount > 0:
            ax.text(bar.get_x() + bar.get_width() / 2,
                    bar.get_height() + max_val * 0.025,
                    f"{amount:,.0f}".replace(',', '.'),
                    ha='center', va='bottom', fontsize=7.5,
                    color='#2C3E50', fontweight='bold')
    for bar, amount in zip(b2, col_k):
        if amount > 0:
            ax.text(bar.get_x() + bar.get_width() / 2,
                    bar.get_height() + max_val * 0.025,
                    f"{amount:,.0f}".replace(',', '.'),
                    ha='center', va='bottom', fontsize=7.5,
                    color='#1a7a40', fontweight='bold')

    ax.set_xticks(x)
    ax.set_xticklabels([str(y) for y in years], fontsize=10)
    ax.set_ylabel('Thousand', fontsize=9)
    ax.yaxis.set_major_formatter(
        mticker.FuncFormatter(lambda v, _: f"{v:,.0f}".replace(',', '.'))
    )
    ax.set_ylim(0, max_val * 1.30)
    ax.spines['top'].set_visible(False)
    ax.spines['right'].set_visible(False)
    ax.tick_params(axis='both', labelsize=9)
    ax.grid(axis='y', linestyle='--', alpha=0.3, zorder=0)

    leg = [
        mpatches.Patch(color=_BEKLENEN_C, alpha=0.80, label='Expected'),
        mpatches.Patch(color=_TAHSIL_C,   alpha=0.95, label='Collected'),
    ]
    ax.legend(handles=leg, fontsize=9, framealpha=0, loc='upper right')

    plt.tight_layout(pad=0.4)
    buf = io.BytesIO()
    fig.savefig(buf, format='png', dpi=200, bbox_inches='tight',
                transparent=True, facecolor='none')
    plt.close(fig)
    buf.seek(0)
    return buf


def _yearly_summary_data(receivables: list) -> list:
    """Summary row data grouped by year."""
    year_data: dict = defaultdict(lambda: {'total': 0.0, 'paid': 0.0,
                                            'remaining': 0.0, 'count': 0, 'overdue': 0})
    for a in receivables:
        due = parse_date(a.get('due_date', ''))
        if not due:
            continue
        y = due.year
        year_data[y]['total']     += _fl(a, 'total_amount')
        year_data[y]['paid']      += _fl(a, 'paid_amount')
        year_data[y]['remaining'] += _fl(a, 'remaining_amount')
        year_data[y]['count']     += 1
        d = str(a.get('status', '')).lower()
        if 'odendi' not in d and days_overdue(a.get('due_date', '')) > 0:
            year_data[y]['overdue'] += 1

    rows = []
    for y in sorted(year_data.keys()):
        d    = year_data[y]
        rate = (d['paid'] / d['total'] * 100) if d['total'] > 0 else None
        rows.append([str(y), d['count'], d['total'], d['paid'], d['remaining'], rate, d['overdue']])
    return rows


def _yearly_summary_table(summary_rows: list) -> Table:
    col_widths = [w * cm for w in [2.2, 1.4, 3.5, 3.5, 3.5, 1.8, 1.5]]
    rows = [[Paragraph(b, S_TH) for b in
             ['Year', 'Records', 'Expected', 'Paid', 'Remaining', 'Rate', 'Overdue']]]

    s_empty = ParagraphStyle('bos2', fontName='DejaVu', fontSize=9,
                              textColor=R.TEXT_MUTED, alignment=TA_CENTER, leading=13)
    for r in summary_rows:
        year_str, count, total, paid, remaining, rate, overdue = r
        if count == 0:
            rows.append([Paragraph(year_str, S_TD)] + [Paragraph('—', s_empty)] * 6)
        else:
            rate_color      = (R.UNPAID if rate < 50 else R.PARTIAL if rate < 80 else R.PAID)
            remaining_color = R.UNPAID if remaining > 0 else R.PAID
            s_rate      = ParagraphStyle('or2', fontName='DejaVu-Bold', fontSize=9,
                                          textColor=rate_color, alignment=TA_CENTER, leading=13)
            s_remaining = ParagraphStyle('ka2', fontName='DejaVu', fontSize=9,
                                          textColor=remaining_color, alignment=TA_RIGHT, leading=13)
            rows.append([
                Paragraph(year_str,            S_TD),
                Paragraph(str(count),          S_TD_C),
                Paragraph(fmt_amount(total),   S_TD_R),
                Paragraph(fmt_amount(paid),    S_TD_R),
                Paragraph(fmt_amount(remaining), s_remaining),
                Paragraph(f'%{rate:.0f}',      s_rate),
                Paragraph(str(overdue) if overdue > 0 else '—', S_TD_C),
            ])
    return _styled_table(rows, col_widths, font_size=8.5, row_pad=5)


def _client_list(sorted_clients: list) -> Table:
    """
    Profile-sorted client table.
    sorted_clients: [(client_dict, total, paid, remaining, profile), ...]
    """
    col_widths = [w * cm for w in [3.6, 3.4, 2.8, 2.6, 2.8, 2.2]]
    rows = [[Paragraph(b, S_TH) for b in
             ['Full Name', 'Company', 'Total', 'Paid', 'Remaining', 'Profile']]]

    for k, total, paid, remaining, profile in sorted_clients:
        p_color, _     = profile_info(profile)
        remaining_color = R.UNPAID if remaining > 0 else R.PAID

        s_remaining = ParagraphStyle('sk', fontName='DejaVu', fontSize=8,
                                      textColor=remaining_color, alignment=TA_RIGHT, leading=14)
        s_profile   = ParagraphStyle('sp', fontName='DejaVu-Bold', fontSize=8,
                                      textColor=p_color, alignment=TA_CENTER, leading=14)
        s_sm        = ParagraphStyle('sm', fontName='DejaVu', fontSize=8,
                                      textColor=R.TEXT, leading=14)
        s_r_sm      = ParagraphStyle('sr', fontName='DejaVu', fontSize=8,
                                      textColor=R.TEXT, alignment=TA_RIGHT, leading=14)

        rows.append([
            Paragraph(k.get('full_name', ''), s_sm),
            Paragraph(k.get('company', '—'),  s_sm),
            Paragraph(fmt_amount(total),      s_r_sm),
            Paragraph(fmt_amount(paid),       s_r_sm),
            Paragraph(fmt_amount(remaining),  s_remaining),
            Paragraph(profile,                s_profile),
        ])

    return _styled_table(rows, col_widths, font_size=8, row_pad=9)


# ── HEADER / FOOTER ───────────────────────────────────────────────────────────

def _draw_footer(canvas, company_name: str, year_str: str, report_type: str = 'Yearly Receivables Report'):
    canvas.setStrokeColor(R.LINE)
    canvas.setLineWidth(0.5)
    canvas.line(MARGIN, 1.25 * cm, W - MARGIN, 1.25 * cm)
    canvas.setFont('DejaVu', 8)
    canvas.setFillColor(R.FOOTER)
    canvas.drawString(MARGIN, 0.6 * cm,
                      f'{company_name}  |  {report_type}  |  {year_str}')
    canvas.drawCentredString(W / 2, 0.6 * cm, today_str())
    # Right side is written by PageNumberCanvas — not placed here (would conflict)


# ── MAIN FUNCTION ─────────────────────────────────────────────────────────────
def generate_yearly_report(
    clients: list,
    receivables: list,
    year: int = None,
    company_name: str = 'Payment Tracking System',
    output_file: str = None,
) -> str:
    """
    Generates a yearly receivables report PDF.

    Args:
        clients:      [{client_id, full_name, company, active, ...}]
        receivables:  [{client_id, receivable_no, project_name, work_description, installment_no,
                        total_amount, due_date, status, paid_amount, remaining_amount, ...}]
        year:         Report year — monthly trend is filtered to this year (default: last year)
        company_name: Company/brand name shown in header and footer
        output_file:  PDF output path (auto-generated if not provided)

    Returns:
        Full path of the generated PDF.
    """
    today = datetime.date.today()
    if year is None:
        year = today.year - 1
    year_str = str(year)

    if not output_file:
        os.makedirs(
            os.path.join(os.path.dirname(os.path.dirname(__file__)), 'reports'),
            exist_ok=True,
        )
        output_file = os.path.join(
            os.path.dirname(os.path.dirname(__file__)),
            'reports',
            f'yearly_report_{year}.pdf',
        )

    # ── Data calculations ─────────────────────────────────────────────────────
    clients_map = _clients_map(clients)

    total_receivables = sum(_fl(a, 'total_amount')     for a in receivables)
    total_paid        = sum(_fl(a, 'paid_amount')      for a in receivables)
    total_remaining   = sum(_fl(a, 'remaining_amount') for a in receivables)
    collection_rate   = (total_paid / total_receivables * 100) if total_receivables > 0 else 0.0

    n_partial = sum(1 for a in receivables
                    if 'kismi'  in a.get('status', '').lower()
                    or 'kısmi'  in a.get('status', '').lower())
    n_paid    = sum(1 for a in receivables
                    if ('odendi' in a.get('status', '').lower() or
                        'ödendi' in a.get('status', '').lower())
                    and 'kismi' not in a.get('status', '').lower()
                    and 'kısmi' not in a.get('status', '').lower())
    n_unpaid  = sum(1 for a in receivables
                    if 'odenmedi' in a.get('status', '').lower()
                    or 'ödenmedi' in a.get('status', '').lower())
    n_overdue = sum(1 for a in receivables
                    if 'odendi' not in a.get('status', '').lower()
                    and days_overdue(a.get('due_date', '')) > 0)

    monthly_expected, monthly_collected = _monthly_trend(receivables, year)
    monthly_summary                     = _monthly_summary_data(receivables, year)
    overdue_counts                      = _overdue_categories(receivables)
    profile_dist                        = _profile_distribution(clients, receivables)
    top_overdue, total_overdue_n        = _overdue_receivables(receivables, clients_map, limit=15)
    sorted_clients                      = _sorted_client_profiles(clients, receivables, year)
    n_active_clients                    = len(sorted_clients)  # Clients with receivables in this year

    # ── Charts ────────────────────────────────────────────────────────────────
    status_buf,  status_leg  = _status_pie(n_paid, n_partial, n_unpaid)
    profile_buf, profile_leg = _profile_pie(profile_dist)
    monthly_buf              = _monthly_grouped_bar(monthly_expected, monthly_collected)
    overdue_buf              = _overdue_bar(overdue_counts)

    # ── Page callbacks ────────────────────────────────────────────────────────
    PAGE_TITLES = {
        1: 'Yearly Summary',
        2: 'Monthly Collection Trend',
        3: 'Risk and Overdue Analysis',
    }

    def _on_page(canvas, doc):
        canvas.saveState()
        title = PAGE_TITLES.get(doc.page, 'Client-by-Client Yearly Summary')
        draw_header(canvas, company_name, title, year_str)
        _draw_footer(canvas, company_name, year_str)
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

    s_note = ParagraphStyle('sn', fontName='DejaVu', fontSize=9,
                             textColor=R.TEXT_MUTED, leading=13, spaceAfter=6)

    # ─────────────────────────────────────────────────────────────────────────
    # PAGE 1 — Yearly Summary
    # ─────────────────────────────────────────────────────────────────────────
    story.append(_kpi_row(total_receivables, total_paid, total_remaining, collection_rate))
    story.append(Spacer(1, 0.25 * cm))
    story.append(_statistics_row(
        n_active_clients, len(receivables),
        n_paid, n_partial, n_unpaid, n_overdue,
    ))
    story.append(Spacer(1, 0.5 * cm))

    # Status pie (left) + Profile pie (right) — vertical layout, large charts
    half_w = CONTENT_W / 2
    if status_buf and profile_buf:
        headings_t = Table(
            [[section_heading('Payment Status Distribution',  half_w)[1],
              section_heading('Client Profile Distribution',  half_w)[1]]],
            colWidths=[half_w, half_w],
        )
        headings_t.setStyle(TableStyle([
            ('LEFTPADDING',   (0, 0), (-1, -1), 0),
            ('RIGHTPADDING',  (0, 0), (-1, -1), 0),
            ('TOPPADDING',    (0, 0), (-1, -1), 0),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
        ]))
        story.append(headings_t)

        content_t = Table(
            [[_pie_block(status_buf,  status_leg,  half_w, pie_cm=6.5),
              _pie_block(profile_buf, profile_leg, half_w, pie_cm=6.5)]],
            colWidths=[half_w, half_w],
        )
        content_t.setStyle(TableStyle([
            ('VALIGN',        (0, 0), (-1, -1), 'TOP'),
            ('LEFTPADDING',   (0, 0), (-1, -1), 0),
            ('RIGHTPADDING',  (0, 0), (-1, -1), 0),
            ('TOPPADDING',    (0, 0), (-1, -1), 0),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 0),
        ]))
        story.append(content_t)
    elif status_buf:
        story.extend(section_heading('Payment Status Distribution'))
        story.append(_pie_and_legend(status_buf, status_leg))

    # ─────────────────────────────────────────────────────────────────────────
    # PAGE 2 — Monthly Collection Trend
    # ─────────────────────────────────────────────────────────────────────────
    story.append(PageBreak())

    if monthly_buf:
        story.extend(section_heading(f'Monthly Collection Trend — {year_str}  (Thousands)'))
        story.append(Image(monthly_buf, width=CONTENT_W,
                           height=CONTENT_W * (4.0 / 10)))
        story.append(Spacer(1, 0.35 * cm))
    else:
        story.append(Paragraph(
            f'No collection data found for {year_str}.', S_NOTE))
        story.append(Spacer(1, 0.25 * cm))

    if monthly_summary:
        story.extend(section_heading('Monthly Comparison'))
        story.append(_monthly_summary_table(monthly_summary))
    else:
        story.append(Paragraph(
            f'No monthly data found for {year_str}.', S_NOTE))

    # ─────────────────────────────────────────────────────────────────────────
    # PAGE 3 — Risk and Overdue Analysis
    # ─────────────────────────────────────────────────────────────────────────
    story.append(PageBreak())

    if overdue_buf:
        story.extend(section_heading('Overdue Duration Distribution'))
        story.append(Image(overdue_buf, width=CONTENT_W * 0.55,
                           height=CONTENT_W * 0.55 * (3.4 / 5.5)))
        story.append(Spacer(1, 0.35 * cm))

    summary_text = (
        f'Showing the top 15 records with the longest overdue out of '
        f'{total_overdue_n} total overdue receivables.'
        if total_overdue_n > 15
        else f'There are {total_overdue_n} overdue receivables in total.'
    )
    story.extend(section_heading('Most Overdue Receivables'))
    story.append(Paragraph(summary_text, s_note))

    if top_overdue:
        story.append(_overdue_table(top_overdue))
        overdue_total = sum(_fl(a, 'remaining_amount') for a in top_overdue)
        label         = 'the listed 15 records' if total_overdue_n > 15 else 'overdue receivables'
        s_total = ParagraphStyle('gt', fontName='DejaVu-Bold', fontSize=9.5,
                                  textColor=R.UNPAID, alignment=TA_RIGHT, leading=14)
        story.append(Spacer(1, 0.2 * cm))
        story.append(Paragraph(
            f'Total overdue remaining ({label}): {fmt_amount(overdue_total)}', s_total))
    else:
        story.append(Paragraph('No overdue receivables found.', S_NOTE))

    # ─────────────────────────────────────────────────────────────────────────
    # PAGE 4+ — Client-by-Client Yearly Summary (profile-sorted, auto-overflow)
    # ─────────────────────────────────────────────────────────────────────────
    story.append(PageBreak())

    s_info = ParagraphStyle('ki', fontName='DejaVu', fontSize=9,
                             textColor=R.TEXT_MUTED, leading=13, spaceAfter=4)
    story.append(Paragraph(
        f'Number of clients with receivables in {year_str}: {n_active_clients}  |  '
        f'Order: Profile (Perfect \u2192 Problematic), then remaining amount (\u2193)',
        s_info,
    ))
    story.append(Spacer(1, 0.15 * cm))
    story.append(_client_list(sorted_clients))

    # ── Build ─────────────────────────────────────────────────────────────────
    doc.build(
        story,
        onFirstPage=_on_page,
        onLaterPages=_on_page,
        canvasmaker=PageNumberCanvas,
    )
    return output_file


# ── GENERAL REPORT (ALL PERIODS) ──────────────────────────────────────────────
def generate_general_report(
    clients: list,
    receivables: list,
    company_name: str = 'Payment Tracking System',
    output_file: str = None,
) -> str:
    """
    Generates a general receivables report PDF covering all periods.

    Differences from the yearly report:
      - No filtering to a specific year — all data included
      - Page 2 shows a yearly comparison bar chart instead of monthly trend
      - Page 2 has a year-by-year summary table
    """
    # Determine date range label
    years_set = sorted({
        parse_date(a.get('due_date', '')).year
        for a in receivables
        if parse_date(a.get('due_date', ''))
    })
    if len(years_set) >= 2:
        year_str = f'{years_set[0]}\u2013{years_set[-1]}'
    elif years_set:
        year_str = str(years_set[0])
    else:
        year_str = 'All Periods'

    if not output_file:
        os.makedirs(
            os.path.join(os.path.dirname(os.path.dirname(__file__)), 'reports'),
            exist_ok=True,
        )
        output_file = os.path.join(
            os.path.dirname(os.path.dirname(__file__)),
            'reports',
            'general_report.pdf',
        )

    # ── Data calculations ─────────────────────────────────────────────────────
    clients_map = _clients_map(clients)

    total_receivables = sum(_fl(a, 'total_amount')     for a in receivables)
    total_paid        = sum(_fl(a, 'paid_amount')      for a in receivables)
    total_remaining   = sum(_fl(a, 'remaining_amount') for a in receivables)
    collection_rate   = (total_paid / total_receivables * 100) if total_receivables > 0 else 0.0

    n_partial = sum(1 for a in receivables
                    if 'kismi'  in a.get('status', '').lower()
                    or 'kısmi'  in a.get('status', '').lower())
    n_paid    = sum(1 for a in receivables
                    if ('odendi' in a.get('status', '').lower() or
                        'ödendi' in a.get('status', '').lower())
                    and 'kismi' not in a.get('status', '').lower()
                    and 'kısmi' not in a.get('status', '').lower())
    n_unpaid  = sum(1 for a in receivables
                    if 'odenmedi' in a.get('status', '').lower()
                    or 'ödenmedi' in a.get('status', '').lower())
    n_overdue = sum(1 for a in receivables
                    if 'odendi' not in a.get('status', '').lower()
                    and days_overdue(a.get('due_date', '')) > 0)

    year_list, yearly_expected, yearly_collected = _yearly_trend_data(receivables)
    yearly_summary                = _yearly_summary_data(receivables)
    overdue_counts                = _overdue_categories(receivables)
    profile_dist                  = _profile_distribution(clients, receivables)
    top_overdue, total_overdue_n  = _overdue_receivables(receivables, clients_map, limit=15)
    sorted_clients                = _sorted_client_profiles(clients, receivables, year=None)
    n_active_clients              = len(sorted_clients)

    # ── Charts ────────────────────────────────────────────────────────────────
    status_buf,  status_leg  = _status_pie(n_paid, n_partial, n_unpaid)
    profile_buf, profile_leg = _profile_pie(profile_dist)
    yearly_buf               = _yearly_grouped_bar(year_list, yearly_expected, yearly_collected)
    overdue_buf              = _overdue_bar(overdue_counts)

    # ── Page callbacks ────────────────────────────────────────────────────────
    PAGE_TITLES = {
        1: 'General Summary',
        2: 'Yearly Collection Trend',
        3: 'Risk and Overdue Analysis',
    }

    def _on_page(canvas, doc):
        canvas.saveState()
        title = PAGE_TITLES.get(doc.page, 'Client-by-Client General Summary')
        draw_header(canvas, company_name, title, year_str)
        _draw_footer(canvas, company_name, year_str, 'General Receivables Report')
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

    s_note = ParagraphStyle('sng', fontName='DejaVu', fontSize=9,
                             textColor=R.TEXT_MUTED, leading=13, spaceAfter=6)
    s_info = ParagraphStyle('kig', fontName='DejaVu', fontSize=9,
                             textColor=R.TEXT_MUTED, leading=13, spaceAfter=4)

    # ─────────────────────────────────────────────────────────────────────────
    # PAGE 1 — General Summary
    # ─────────────────────────────────────────────────────────────────────────
    story.append(_kpi_row(total_receivables, total_paid, total_remaining, collection_rate))
    story.append(Spacer(1, 0.25 * cm))
    story.append(_statistics_row(
        n_active_clients, len(receivables),
        n_paid, n_partial, n_unpaid, n_overdue,
    ))
    story.append(Spacer(1, 0.5 * cm))

    half_w = CONTENT_W / 2
    if status_buf and profile_buf:
        headings_t = Table(
            [[section_heading('Payment Status Distribution', half_w)[1],
              section_heading('Client Profile Distribution', half_w)[1]]],
            colWidths=[half_w, half_w],
        )
        headings_t.setStyle(TableStyle([
            ('LEFTPADDING',   (0, 0), (-1, -1), 0),
            ('RIGHTPADDING',  (0, 0), (-1, -1), 0),
            ('TOPPADDING',    (0, 0), (-1, -1), 0),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
        ]))
        story.append(headings_t)

        content_t = Table(
            [[_pie_block(status_buf,  status_leg,  half_w, pie_cm=9.0),
              _pie_block(profile_buf, profile_leg, half_w, pie_cm=9.0)]],
            colWidths=[half_w, half_w],
        )
        content_t.setStyle(TableStyle([
            ('VALIGN',        (0, 0), (-1, -1), 'TOP'),
            ('LEFTPADDING',   (0, 0), (-1, -1), 0),
            ('RIGHTPADDING',  (0, 0), (-1, -1), 0),
            ('TOPPADDING',    (0, 0), (-1, -1), 0),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 0),
        ]))
        story.append(content_t)
    elif status_buf:
        story.extend(section_heading('Payment Status Distribution'))
        story.append(_pie_and_legend(status_buf, status_leg))

    # Key findings — fills bottom of page 1
    story.append(Spacer(1, 0.4 * cm))
    story.extend(section_heading('Key Findings'))
    s_finding   = ParagraphStyle('bul', fontName='DejaVu', fontSize=9.5,
                                  textColor=R.TEXT, leading=16, spaceAfter=5,
                                  leftIndent=8)
    s_finding_b = ParagraphStyle('bulb', fontName='DejaVu-Bold', fontSize=9.5,
                                  textColor=R.TEXT, leading=16, spaceAfter=5,
                                  leftIndent=8)
    overdue_balance = sum(_fl(a, 'remaining_amount') for a in receivables
                          if 'odendi' not in str(a.get('status', '')).lower()
                          and days_overdue(a.get('due_date', '')) > 0)
    # sorted_clients: [(client_dict, total, paid, remaining, profile_str), ...]
    problematic_clients       = [k for k in sorted_clients if k[4] == 'Problematic']
    risky_and_problematic     = [k for k in sorted_clients if k[4] in ('Problematic', 'Risky')]
    top_client = risky_and_problematic[0] if risky_and_problematic else (sorted_clients[0] if sorted_clients else None)
    rate_comment = ('critically low' if collection_rate < 40 else
                    ('needs monitoring' if collection_rate < 60 else
                     ('good' if collection_rate < 80 else 'very good')))
    findings = [
        f'Overdue balance from <b>{n_overdue}</b> past-due receivables: <b>{fmt_amount(overdue_balance)}</b>.',
        f'Overall collection rate is <b>%{round(collection_rate)}</b> — {rate_comment}.',
        f'<b>{len(problematic_clients)}</b> client(s) in "Problematic" profile; highest risky balance: <b>{fmt_amount(top_client[3] if top_client else 0)}</b> ({top_client[0].get("full_name", "-") if top_client else "-"}).',
        f'<b>{n_paid}</b> out of <b>{n_active_clients}</b> active clients have paid all their receivables.',
    ]
    for finding in findings:
        story.append(Paragraph(f'\u2022 {finding}', s_finding))

    # ─────────────────────────────────────────────────────────────────────────
    # PAGE 2 — Yearly Collection Trend
    # ─────────────────────────────────────────────────────────────────────────
    story.append(PageBreak())

    if yearly_buf:
        story.extend(section_heading(f'Yearly Collection Trend — {year_str}  (Thousands)'))
        story.append(Image(yearly_buf, width=CONTENT_W,
                           height=CONTENT_W * (6.5 / 10)))
        story.append(Spacer(1, 0.35 * cm))
    else:
        story.append(Paragraph('No collection data found.', s_note))
        story.append(Spacer(1, 0.25 * cm))

    if yearly_summary:
        story.extend(section_heading('Yearly Comparison'))
        story.append(_yearly_summary_table(yearly_summary))
        story.append(Spacer(1, 0.5 * cm))
        s_analysis = ParagraphStyle('anz', fontName='DejaVu', fontSize=9.5,
                                    textColor=R.TEXT, leading=16, spaceAfter=6)
        # rows: [year_str, count, total, paid, remaining, rate_float|None, overdue]
        if len(yearly_summary) >= 2:
            last    = yearly_summary[-1]   # [year, count, expected, paid, remaining, rate, overdue]
            prev    = yearly_summary[-2]
            yr_last, exp_last,  rate_last,  ov_last  = last[0], last[2], last[5] or 0, last[6]
            yr_prev, exp_prev,  rate_prev,  ov_prev  = prev[0], prev[2], prev[5] or 0, prev[6]
            volume_ratio = round(exp_last / exp_prev, 1) if exp_prev > 0 else 0
            rate_diff    = round(rate_last - rate_prev, 1)
            ov_diff      = ov_last - ov_prev
            rate_comment = ('increased \u25b2' if rate_diff > 0.5 else
                            ('declined \u25bc' if rate_diff < -0.5 else 'remained stable \u2014'))
            ov_comment   = 'reached' if ov_diff >= 0 else 'declined'
            total_remaining_all  = sum(r[4] for r in yearly_summary)
            total_expected_all   = sum(r[2] for r in yearly_summary)
            remaining_rate = round(total_remaining_all / total_expected_all * 100) if total_expected_all > 0 else 0
            for sentence in [
                f'Receivable volume in <b>{yr_last}</b> grew <b>{volume_ratio}x</b> compared to {yr_prev} ({fmt_amount(exp_prev)} \u2192 {fmt_amount(exp_last)}).',
                f'Collection rate {rate_comment} (%{round(rate_prev)} \u2192 %{round(rate_last)}).',
                f'Overdue receivable count in {yr_last} {ov_comment} to {ov_last} records ({yr_prev}: {ov_prev} records).',
                f'Cumulative total receivable across all periods: <b>{fmt_amount(total_expected_all)}</b> — uncollected: <b>{fmt_amount(total_remaining_all)}</b> (%{remaining_rate}).',
            ]:
                story.append(Paragraph(sentence, s_analysis))
        elif len(yearly_summary) == 1:
            r = yearly_summary[0]
            story.append(Paragraph(
                f'<b>{r[0]}</b> is reported as the only period. '
                f'Expected receivable: {fmt_amount(r[2])}, Collection rate: %{round(r[5] or 0)}.',
                s_analysis,
            ))

    # ─────────────────────────────────────────────────────────────────────────
    # PAGE 3 — Risk and Overdue Analysis
    # ─────────────────────────────────────────────────────────────────────────
    story.append(PageBreak())

    if overdue_buf:
        story.extend(section_heading('Overdue Duration Distribution'))
        story.append(Image(overdue_buf, width=CONTENT_W * 0.55,
                           height=CONTENT_W * 0.55 * (3.4 / 5.5)))
        story.append(Spacer(1, 0.35 * cm))

    summary_text = (
        f'Showing the top 15 records with the longest overdue out of '
        f'{total_overdue_n} total overdue receivables.'
        if total_overdue_n > 15
        else f'There are {total_overdue_n} overdue receivables in total.'
    )
    story.extend(section_heading('Most Overdue Receivables'))
    story.append(Paragraph(summary_text, s_note))

    if top_overdue:
        story.append(_overdue_table(top_overdue))
        overdue_total = sum(_fl(a, 'remaining_amount') for a in top_overdue)
        label         = 'the listed 15 records' if total_overdue_n > 15 else 'overdue receivables'
        s_total = ParagraphStyle('gtg', fontName='DejaVu-Bold', fontSize=9.5,
                                  textColor=R.UNPAID, alignment=TA_RIGHT, leading=14)
        story.append(Spacer(1, 0.2 * cm))
        story.append(Paragraph(
            f'Total overdue remaining ({label}): {fmt_amount(overdue_total)}', s_total))
    else:
        story.append(Paragraph('No overdue receivables found.', s_note))

    # ─────────────────────────────────────────────────────────────────────────
    # PAGE 4+ — Client-by-Client General Summary
    # ─────────────────────────────────────────────────────────────────────────
    story.append(PageBreak())
    story.append(Paragraph(
        f'Number of clients with receivables across all periods: {n_active_clients}  |  '
        f'Order: Profile (Perfect \u2192 Problematic), then remaining amount (\u2193)',
        s_info,
    ))
    story.append(Spacer(1, 0.15 * cm))
    story.append(_client_list(sorted_clients))

    # ── Build ─────────────────────────────────────────────────────────────────
    doc.build(
        story,
        onFirstPage=_on_page,
        onLaterPages=_on_page,
        canvasmaker=PageNumberCanvas,
    )
    return output_file
