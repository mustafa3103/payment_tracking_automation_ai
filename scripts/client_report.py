"""Payment Tracking System — Client Report

Usage:
    from client_report import generate_client_report
    path = generate_client_report(client_dict, receivables_list, profil='Sorunlu')

Page 1 — Client info, statistics cards, payment status pie chart, profile scale
Page 2 — Receivables detail table, installment summaries
"""

import io
import os
import sys

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches

from reportlab.lib import colors
from reportlab.lib.units import cm
from reportlab.lib.pagesizes import A4
from reportlab.lib.enums import TA_CENTER, TA_RIGHT
from reportlab.lib.styles import ParagraphStyle
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer,
    Table, TableStyle, Image, PageBreak,
)
from reportlab.platypus.flowables import Flowable

sys.path.insert(0, os.path.dirname(__file__))
from base import (
    W, H, MARGIN, CONTENT_W,
    R, PROFILES, PROFILE_COLORS,
    S_TH, S_TD, S_TD_C, S_TD_R, S_NOTE,
    section_heading, create_card, draw_header,
    fmt_amount, fmt_date, days_overdue,
    status_color, profile_info, today_str,
    calculate_profile, PageNumberCanvas,
)

# ── CONSTANTS ─────────────────────────────────────────────────────────────────
PROFILE_DESCRIPTION = {
    'Sorunsuz':  'Client who makes all payments on time with no delays.',
    'Güvenilir': 'Client who generally pays on time with only occasional delays.',
    'Belirsiz':  'Definitive profile cannot be determined due to insufficient payment history.',
    'Riskli':    'Client with frequent delays and some unpaid receivables.',
    'Sorunlu':   'Client with significant overdue or unpaid receivables.',
}

# Receivables table column widths (total = CONTENT_W ≈ 17.4 cm)
TABLE_COL_W = [1.2, 2.6, 2.3, 2.3, 2.5, 2.2, 2.3, 2.0]  # cm  (Due date 2.5 — fits "01.03.2026")

# ── PROFILE SCALE (custom Flowable) ──────────────────────────────────────────
class ProfileScale(Flowable):
    """5-segment colour-coded profile bar; arrow indicator below the active segment."""

    SEG_H   = 0.75 * cm
    ARROW_H = 0.38 * cm
    PAD     = 0.12 * cm

    def __init__(self, profile_name: str, width: float):
        Flowable.__init__(self)
        self.profile_name = profile_name
        self.width = width
        _, self.active_idx = profile_info(profile_name)
        self.height = self.PAD + self.ARROW_H + self.SEG_H + self.PAD

    def draw(self):
        c = self.canv
        seg_w = self.width / len(PROFILES)
        seg_y = self.PAD + self.ARROW_H  # bottom y of segment

        for i, (label, color) in enumerate(zip(PROFILES, PROFILE_COLORS)):
            x  = i * seg_w
            cx = x + seg_w / 2

            # Coloured segment
            c.setFillColor(color)
            c.setStrokeColor(colors.white)
            c.setLineWidth(1.5)
            c.rect(x, seg_y, seg_w, self.SEG_H, fill=1, stroke=1)

            # Label (white, vertically centred within segment)
            font = 'DejaVu-Bold' if i == self.active_idx else 'DejaVu'
            c.setFont(font, 8.5)
            c.setFillColor(colors.white)
            label_y = seg_y + (self.SEG_H - 8.5 * 0.75) / 2
            c.drawCentredString(cx, label_y, label)

            # Arrow indicator (upward-pointing triangle below active segment)
            if i == self.active_idx:
                c.setFillColor(color)
                half_w = 0.32 * cm
                p = c.beginPath()
                p.moveTo(cx,          seg_y)               # top (tip — touches segment)
                p.lineTo(cx - half_w, self.PAD)            # bottom left
                p.lineTo(cx + half_w, self.PAD)            # bottom right
                p.close()
                c.drawPath(p, fill=1, stroke=0)


# ── PIE CHART ─────────────────────────────────────────────────────────────────
def _pie_chart(n_paid: int, n_partial: int, n_unpaid: int):
    """
    Payment distribution pie chart.
    Zero-value slices are omitted.
    Returns: (BytesIO PNG buffer, [(label, hex_color), ...] legend data)
             If no data: (None, [])
    """
    values, labels, chart_colors = [], [], []

    if n_paid > 0:
        values.append(n_paid)
        labels.append(f'Paid ({n_paid})')
        chart_colors.append('#27AE60')
    if n_partial > 0:
        values.append(n_partial)
        labels.append(f'Partial ({n_partial})')
        chart_colors.append('#E67E22')
    if n_unpaid > 0:
        values.append(n_unpaid)
        labels.append(f'Unpaid ({n_unpaid})')
        chart_colors.append('#E74C3C')

    if not values:
        return None, []

    # Square figure — prevents oval shape
    fig, ax = plt.subplots(figsize=(4.2, 4.2), facecolor='none')

    wedge_props = dict(linewidth=2.5, edgecolor='white')
    _, _, autotexts = ax.pie(
        values,
        colors=chart_colors,
        autopct=lambda p: f'{round(p)}%' if p >= 5 else '',
        pctdistance=0.70,
        startangle=90,
        wedgeprops=wedge_props,
        textprops=dict(fontsize=12, fontweight='bold', color='white'),
    )
    ax.set_aspect('equal')   # CRITICAL — definitively prevents oval shape

    plt.tight_layout(pad=0)
    buf = io.BytesIO()
    fig.savefig(buf, format='png', dpi=200,
                bbox_inches='tight', transparent=True, facecolor='none')
    buf.seek(0)
    plt.close(fig)

    legend = list(zip(labels, chart_colors))
    return buf, legend


# ── COMPONENTS ────────────────────────────────────────────────────────────────
def _stats_row(total: float, paid: float, remaining: float) -> Table:
    """3 statistics cards side by side."""
    remaining_color = R.UNPAID if remaining > 0 else R.PAID

    def card(value, label, color):
        return create_card(fmt_amount(value), label, color, CONTENT_W / 3,
                           value_fs=16, padding_v=11)

    outer = Table(
        [[card(total,     'Total Receivable', R.TEXT),
          card(paid,      'Collected',        R.PAID),
          card(remaining, 'Outstanding',      remaining_color)]],
        colWidths=[CONTENT_W / 3] * 3,
    )
    outer.setStyle(TableStyle([
        ('LEFTPADDING',   (0, 0), (-1, -1), 0),
        ('RIGHTPADDING',  (0, 0), (-1, -1), 0),
        ('TOPPADDING',    (0, 0), (-1, -1), 0),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 0),
        ('VALIGN',        (0, 0), (-1, -1), 'TOP'),
    ]))
    return outer


def _legend_table(legend_data: list) -> Table:
    """Pie chart legend: coloured square + text."""
    s = ParagraphStyle('leg', fontName='DejaVu', fontSize=10,
                        textColor=R.TEXT, leading=14)
    rows = []
    for label, hex_color in legend_data:
        color = colors.HexColor(hex_color)
        box = Table([['']], colWidths=[0.42 * cm], rowHeights=[0.42 * cm])
        box.setStyle(TableStyle([
            ('BACKGROUND',    (0, 0), (0, 0), color),
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


def _receivables_table(receivables: list) -> Table:
    """Receivables detail table (Platypus Table)."""
    col_widths = [w * cm for w in TABLE_COL_W]
    headers    = ['Code', 'Project', 'Description', 'Amount', 'Due Date', 'Status', 'Paid', 'Remaining']

    rows = [[Paragraph(h, S_TH) for h in headers]]

    for a in receivables:
        status      = str(a.get('status', ''))
        s_color     = status_color(status)
        days_late   = days_overdue(a.get('due_date', ''))

        # Status cell: text + how many days overdue
        status_text = status
        if days_late > 0 and 'paid' not in status.lower():
            status_text = f'{status_text}\n({days_late} days)'

        s_status = ParagraphStyle('ds', fontName='DejaVu-Bold', fontSize=8,
                                   textColor=s_color, alignment=TA_CENTER, leading=11)

        # Description: work_description + installment number if present
        installment  = a.get('installment_no', '')
        description  = str(a.get('work_description', ''))
        if installment:
            description += f'\nInstallment {installment}'

        rows.append([
            Paragraph(str(a.get('receivable_no', '')),          S_TD_C),
            Paragraph(str(a.get('project_name', '')),            S_TD),
            Paragraph(description,                               S_TD),
            Paragraph(fmt_amount(a.get('total_amount', 0)),     S_TD_R),
            Paragraph(fmt_date(a.get('due_date', '')),          S_TD_C),
            Paragraph(status_text,                               s_status),
            Paragraph(fmt_amount(a.get('paid_amount', 0)),      S_TD_R),
            Paragraph(fmt_amount(a.get('remaining_amount', 0)), S_TD_R),
        ])

    t = Table(rows, colWidths=col_widths, repeatRows=1)

    style = [
        # ── Header row
        ('BACKGROUND',    (0, 0), (-1, 0), R.TH_BG),
        ('TOPPADDING',    (0, 0), (-1, 0), 8),
        ('BOTTOMPADDING', (0, 0), (-1, 0), 8),
        # ── Data rows
        ('FONTNAME',      (0, 1), (-1, -1), 'DejaVu'),
        ('FONTSIZE',      (0, 1), (-1, -1), 9),
        ('TOPPADDING',    (0, 1), (-1, -1), 6),
        ('BOTTOMPADDING', (0, 1), (-1, -1), 6),
        # ── General
        ('LEFTPADDING',   (0, 0), (-1, -1), 5),
        ('RIGHTPADDING',  (0, 0), (-1, -1), 5),
        ('VALIGN',        (0, 0), (-1, -1), 'MIDDLE'),
        ('LINEBELOW',     (0, 0), (-1, -1), 0.3, R.BORDER),
        ('BOX',           (0, 0), (-1, -1), 0.5, R.TH_BG),
    ]

    # Zebra striping
    for i in range(1, len(rows)):
        if i % 2 == 0:
            style.append(('BACKGROUND', (0, i), (-1, i), R.ROW_ALT))

    t.setStyle(TableStyle(style))
    return t


def _installment_summary_boxes(receivables: list) -> list:
    """Summary info boxes for project+work groups with 2+ installments."""
    from collections import defaultdict

    groups = defaultdict(list)
    for a in receivables:
        key = f"{a.get('project_name', '')}||{a.get('work_description', '')}"
        groups[key].append(a)

    boxes = []
    for key, records in groups.items():
        if len(records) < 2:
            continue
        project, work = key.split('||', 1)

        def _fl(a, k):
            try:
                return float(str(a.get(k, 0)).replace(',', '.') or 0)
            except ValueError:
                return 0.0

        g_total     = sum(_fl(a, 'total_amount')     for a in records)
        g_paid      = sum(_fl(a, 'paid_amount')      for a in records)
        g_remaining = sum(_fl(a, 'remaining_amount') for a in records)

        n = len(records)
        text = (
            f'{work} — {project}  ({n} installments):  '
            f'Total {fmt_amount(g_total)}  |  '
            f'Paid {fmt_amount(g_paid)}  |  '
            f'Remaining {fmt_amount(g_remaining)}'
        )
        s = ParagraphStyle('oz', fontName='DejaVu-Bold', fontSize=9.5,
                            textColor=R.TEXT, leading=14)
        box = Table([[Paragraph(text, s)]], colWidths=[CONTENT_W])
        box.setStyle(TableStyle([
            ('BACKGROUND',    (0, 0), (0, 0), R.CARD_BG),
            ('BOX',           (0, 0), (0, 0), 0.7, R.CARD_BORD),
            ('LEFTPADDING',   (0, 0), (0, 0), 12),
            ('RIGHTPADDING',  (0, 0), (0, 0), 12),
            ('TOPPADDING',    (0, 0), (0, 0),  9),
            ('BOTTOMPADDING', (0, 0), (0, 0),  9),
        ]))
        boxes.append(box)

    return boxes


# ── HEADER / FOOTER ───────────────────────────────────────────────────────────
def _draw_footer(canvas, company_name: str):
    """Bottom line + left info + centre date (page number added by PageNumberCanvas on right)."""
    canvas.setStrokeColor(R.LINE)
    canvas.setLineWidth(0.5)
    canvas.line(MARGIN, 1.25 * cm, W - MARGIN, 1.25 * cm)

    canvas.setFont('DejaVu', 8)
    canvas.setFillColor(R.FOOTER)
    canvas.drawString(MARGIN, 0.6 * cm, f'{company_name}  |  Client Report')
    canvas.drawCentredString(W / 2, 0.6 * cm, today_str())


# ── MAIN FUNCTION ─────────────────────────────────────────────────────────────
def generate_client_report(
    client: dict,
    receivables: list,
    profil: str = None,
    company_name: str = 'Payment Tracking System',
    output_file: str = None,
) -> str:
    """
    Generates a client report PDF.

    Args:
        client: {client_id, telefon, full_name, company, email, notes, active}
        receivables: [{client_id, receivable_no, project_name, work_description,
                       installment_no, total_amount, due_date, status,
                       paid_amount, remaining_amount, ...}]
        profil: Profile name (calculated from receivables if not provided)
        output_file: PDF file path (auto-generated if not provided)

    Returns:
        Full path of the generated PDF.
    """
    # Profile
    if not profil:
        profil = calculate_profile(receivables)
    profile_color_obj, _ = profile_info(profil)

    # Output path
    if not output_file:
        name = ''.join(
            c for c in client.get('full_name', 'client').lower().replace(' ', '_')
            if c.isalnum() or c == '_'
        )
        date = today_str().replace('.', '')
        os.makedirs(
            os.path.join(os.path.dirname(os.path.dirname(__file__)), 'reports'),
            exist_ok=True,
        )
        output_file = os.path.join(
            os.path.dirname(os.path.dirname(__file__)),
            'reports',
            f'client_report_{name}_{date}.pdf',
        )

    # Statistics
    def _fl(a, key):
        try:
            return float(str(a.get(key, 0)).replace(',', '.') or 0)
        except ValueError:
            return 0.0

    total_receivable = sum(_fl(a, 'total_amount')     for a in receivables)
    total_paid       = sum(_fl(a, 'paid_amount')      for a in receivables)
    total_remaining  = sum(_fl(a, 'remaining_amount') for a in receivables)

    n_partial  = sum(1 for a in receivables if 'kismi'    in a.get('status', '').lower()
                                            or 'partial'  in a.get('status', '').lower())
    n_paid     = sum(1 for a in receivables
                     if ('odendi'  in a.get('status', '').lower() or
                         'paid'    in a.get('status', '').lower())
                     and 'kismi'   not in a.get('status', '').lower()
                     and 'partial' not in a.get('status', '').lower())
    n_unpaid   = sum(1 for a in receivables if 'odenmedi' in a.get('status', '').lower()
                                            or 'unpaid'   in a.get('status', '').lower())

    client_name = client.get('full_name', '')

    # ── Callbacks ────────────────────────────────────────────────────────────
    def _page1(canvas, doc):
        canvas.saveState()
        draw_header(canvas, company_name, 'Client Report', client_name)
        _draw_footer(canvas, company_name)
        canvas.restoreState()

    def _page2(canvas, doc):
        canvas.saveState()
        draw_header(canvas, company_name, 'Receivables Detail', client_name)
        _draw_footer(canvas, company_name)
        canvas.restoreState()

    # ── Document ─────────────────────────────────────────────────────────────
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

    # --- Client info card + Profile badge ---
    s_client_name = ParagraphStyle('ka', fontName='DejaVu-Bold', fontSize=13,
                                    textColor=R.TEXT, leading=18)
    s_client_info = ParagraphStyle('kb', fontName='DejaVu', fontSize=10,
                                    textColor=R.TEXT_MUTED, leading=15)

    client_rows = [
        [Paragraph(client_name, s_client_name)],
        [Paragraph(client.get('company', '—'),  s_client_info)],
        [Paragraph(client.get('telefon', ''),   s_client_info)],
        [Paragraph(client.get('email', ''),     s_client_info)],
    ]
    if client.get('notes'):
        client_rows.append([
            Paragraph(f'Note: {client["notes"]}',
                      ParagraphStyle('kn', fontName='DejaVu-Italic', fontSize=9,
                                      textColor=R.TEXT_MUTED, leading=13))
        ])

    client_card = Table(client_rows, colWidths=[CONTENT_W * 0.60])
    client_card.setStyle(TableStyle([
        ('BOX',           (0, 0), (-1, -1), 0.5, R.CARD_BORD),
        ('BACKGROUND',    (0, 0), (-1, -1), R.CARD_BG),
        ('LEFTPADDING',   (0, 0), (-1, -1), 12),
        ('RIGHTPADDING',  (0, 0), (-1, -1), 12),
        ('TOPPADDING',    (0, 0), ( 0,  0), 12),
        ('BOTTOMPADDING', (0,-1), (-1, -1), 12),
        ('TOPPADDING',    (0, 1), (-1, -1),  3),
        ('BOTTOMPADDING', (0, 0), (-1, -2),  3),
    ]))

    s_badge = ParagraphStyle('bg', fontName='DejaVu-Bold', fontSize=17,
                              textColor=colors.white, alignment=TA_CENTER, leading=22)
    badge = Table(
        [[Paragraph(profil.upper(), s_badge)]],
        colWidths=[CONTENT_W * 0.33],
    )
    badge.setStyle(TableStyle([
        ('BACKGROUND',    (0, 0), (0, 0), profile_color_obj),
        ('ALIGN',         (0, 0), (0, 0), 'CENTER'),
        ('VALIGN',        (0, 0), (0, 0), 'MIDDLE'),
        ('TOPPADDING',    (0, 0), (0, 0), 20),
        ('BOTTOMPADDING', (0, 0), (0, 0), 20),
    ]))

    top_row = Table(
        [[client_card, badge]],
        colWidths=[CONTENT_W * 0.63, CONTENT_W * 0.37],
    )
    top_row.setStyle(TableStyle([
        ('VALIGN',        (0, 0), (-1, -1), 'MIDDLE'),
        ('LEFTPADDING',   (0, 0), (-1, -1), 0),
        ('RIGHTPADDING',  (0, 0), (-1, -1), 0),
        ('TOPPADDING',    (0, 0), (-1, -1), 0),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 0),
    ]))
    story.append(top_row)
    story.append(Spacer(1, 0.45 * cm))

    # --- Statistics cards ---
    story.append(_stats_row(total_receivable, total_paid, total_remaining))
    story.append(Spacer(1, 0.5 * cm))

    # --- Payment Status (pie chart + legend) ---
    story.extend(section_heading('Payment Status'))

    chart_buf, legend_data = _pie_chart(n_paid, n_partial, n_unpaid)
    if chart_buf:
        # Square size — prevents oval shape in ReportLab
        pie_img = Image(chart_buf, width=5.5 * cm, height=5.5 * cm)
        legend  = _legend_table(legend_data)

        chart_row = Table(
            [[pie_img, legend]],
            colWidths=[6.5 * cm, CONTENT_W - 6.5 * cm],
        )
        chart_row.setStyle(TableStyle([
            ('VALIGN',        (0, 0), (-1, -1), 'MIDDLE'),
            ('LEFTPADDING',   (0, 0), (-1, -1), 0),
            ('RIGHTPADDING',  (0, 0), (-1, -1), 0),
            ('TOPPADDING',    (0, 0), (-1, -1), 0),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 0),
        ]))
        story.append(chart_row)
    else:
        story.append(Paragraph('No payment data available to display.', S_NOTE))

    story.append(Spacer(1, 0.4 * cm))

    # --- Profile scale ---
    story.extend(section_heading('Payment Profile'))
    story.append(ProfileScale(profil, CONTENT_W))

    description = PROFILE_DESCRIPTION.get(profil, '')
    if description:
        story.append(Spacer(1, 0.25 * cm))
        story.append(Paragraph(description, S_NOTE))

    # ─────────────────────────────────────────────────────────────────────────
    # PAGE 2
    # ─────────────────────────────────────────────────────────────────────────
    story.append(PageBreak())
    story.extend(section_heading('Receivables Detail'))

    if receivables:
        sorted_receivables = sorted(
            receivables,
            key=lambda a: (
                str(a.get('project_name', '')),
                str(a.get('work_description', '')),
                int(str(a.get('installment_no') or 0)),
            ),
        )
        story.append(_receivables_table(sorted_receivables))
        story.append(Spacer(1, 0.5 * cm))

        for box in _installment_summary_boxes(sorted_receivables):
            story.append(box)
            story.append(Spacer(1, 0.25 * cm))
    else:
        story.append(Paragraph(
            'No receivable records found for this client.', S_NOTE
        ))

    # ── Build ─────────────────────────────────────────────────────────────────
    doc.build(
        story,
        onFirstPage=_page1,
        onLaterPages=_page2,
        canvasmaker=PageNumberCanvas,
    )
    return output_file
