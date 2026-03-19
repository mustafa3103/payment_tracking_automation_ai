"""Payment Tracking System — PDF Report Base Module

Shared: fonts, colors, utility functions, page-number canvas.
"""

import os
import datetime

import matplotlib
matplotlib.use('Agg')

from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.lib.units import cm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfbase.pdfmetrics import registerFontFamily
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.enums import TA_LEFT, TA_CENTER, TA_RIGHT
from reportlab.pdfgen import canvas as rl_canvas
from reportlab.platypus import Paragraph, Spacer, Table, TableStyle

# ── PAGE SIZE ─────────────────────────────────────────────────────────────────
W, H = A4          # 595.27pt × 841.89pt
MARGIN = 1.8 * cm
CONTENT_W = W - 2 * MARGIN  # ≈ 17.4 cm

# ── FONT SETUP ────────────────────────────────────────────────────────────────
def _register_fonts():
    """Register DejaVu Sans fonts with ReportLab (full Unicode character support)."""
    import matplotlib as mpl
    font_dir = os.path.join(os.path.dirname(mpl.__file__), 'mpl-data', 'fonts', 'ttf')

    for name, filename in [
        ('DejaVu',            'DejaVuSans.ttf'),
        ('DejaVu-Bold',       'DejaVuSans-Bold.ttf'),
        ('DejaVu-Italic',     'DejaVuSans-Oblique.ttf'),
        ('DejaVu-BoldItalic', 'DejaVuSans-BoldOblique.ttf'),
    ]:
        path = os.path.join(font_dir, filename)
        if os.path.exists(path):
            pdfmetrics.registerFont(TTFont(name, path))

    registerFontFamily(
        'DejaVu',
        normal='DejaVu',
        bold='DejaVu-Bold',
        italic='DejaVu-Italic',
        boldItalic='DejaVu-BoldItalic',
    )

_register_fonts()

# ── COLORS ────────────────────────────────────────────────────────────────────
class R:
    HEADER      = colors.HexColor('#2C3E50')   # dark header background
    HEADER_TEXT = colors.white

    PAID        = colors.HexColor('#27AE60')   # green
    PARTIAL     = colors.HexColor('#E67E22')   # orange
    UNPAID      = colors.HexColor('#E74C3C')   # red

    PERFECT     = colors.HexColor('#27AE60')
    RELIABLE    = colors.HexColor('#2ECC71')
    UNCERTAIN   = colors.HexColor('#F39C12')
    RISKY       = colors.HexColor('#E67E22')
    PROBLEMATIC = colors.HexColor('#E74C3C')

    TH_BG       = colors.HexColor('#34495E')   # table header background
    TH_FG       = colors.white
    ROW_ALT     = colors.HexColor('#F5F7FA')   # zebra row
    ROW_NRM     = colors.white
    BORDER      = colors.HexColor('#DEE2E6')

    CARD_BG     = colors.HexColor('#F8F9FA')
    CARD_BORD   = colors.HexColor('#D5D8DC')

    TEXT        = colors.HexColor('#2C3E50')
    TEXT_MUTED  = colors.HexColor('#7F8C8D')
    FOOTER      = colors.HexColor('#95A5A6')
    LINE        = colors.HexColor('#BDC3C7')


PROFILES      = ['Perfect', 'Reliable', 'Uncertain', 'Risky', 'Problematic']
PROFILE_COLORS = [R.PERFECT, R.RELIABLE, R.UNCERTAIN, R.RISKY, R.PROBLEMATIC]
# Sort index: lower = perfect, higher = problematic
PROFILE_ORDER  = {'Perfect': 0, 'Reliable': 1, 'Uncertain': 2, 'Risky': 3, 'Problematic': 4}

# ── PARAGRAPH STYLE FACTORY ───────────────────────────────────────────────────
def style(name='_', **kwargs):
    defaults = dict(fontName='DejaVu', fontSize=10, textColor=R.TEXT, leading=14)
    defaults.update(kwargs)
    return ParagraphStyle(name, **defaults)

# Shared styles
S_CLIENT_NAME = style('S_CLIENT_NAME', fontName='DejaVu-Bold', fontSize=13, leading=18)
S_CLIENT_INFO = style('S_CLIENT_INFO', fontSize=10, textColor=R.TEXT_MUTED, leading=15)
S_SECTION     = style('S_SECTION',     fontName='DejaVu-Bold', fontSize=12,
                       leading=16, spaceBefore=6, spaceAfter=4)
S_TH          = style('S_TH',          fontName='DejaVu-Bold', fontSize=9,
                       textColor=R.TH_FG, alignment=TA_CENTER, leading=12)
S_TD          = style('S_TD',          fontSize=9, leading=12)
S_TD_C        = style('S_TD_C',        fontSize=9, alignment=TA_CENTER, leading=12)
S_TD_R        = style('S_TD_R',        fontSize=9, alignment=TA_RIGHT,  leading=12)
S_NOTE        = style('S_NOTE',        fontSize=9, textColor=R.TEXT_MUTED,
                       fontName='DejaVu-Italic', leading=13)

# ── UTILITY FUNCTIONS ─────────────────────────────────────────────────────────
def fmt_amount(n) -> str:
    """Format a number as 150.000 TL (integer) or 150.000,50 TL."""
    try:
        v = float(str(n).replace(' ', '').replace('TL', '').replace(',', '.'))
    except (ValueError, TypeError):
        return '0 TL'
    whole = int(v)
    cents = round((v - whole) * 100)
    thousands = f"{whole:,}".replace(',', '.')
    if cents == 0:
        return f"{thousands} TL"
    return f"{thousands},{cents:02d} TL"


def parse_date(s) -> datetime.date | None:
    """Parse a DD.MM.YYYY or YYYY-MM-DD string into a date object."""
    if not s or s == '-':
        return None
    s = str(s).strip()
    try:
        if '.' in s and len(s) == 10:
            return datetime.datetime.strptime(s, '%d.%m.%Y').date()
        if '-' in s:
            return datetime.datetime.strptime(s[:10], '%Y-%m-%d').date()
    except ValueError:
        pass
    return None


def fmt_date(s) -> str:
    """YYYY-MM-DD or DD.MM.YYYY → DD.MM.YYYY."""
    if not s:
        return '-'
    s = str(s).strip()
    if len(s) == 10 and s[2] == '.' and s[5] == '.':
        return s
    try:
        if '-' in s:
            d = datetime.datetime.strptime(s[:10], '%Y-%m-%d')
            return d.strftime('%d.%m.%Y')
    except ValueError:
        pass
    return s


def days_overdue(due_date) -> int:
    """Days between the due date and today (positive = overdue)."""
    if not due_date or due_date == '-':
        return 0
    try:
        s = str(due_date).strip()
        if '.' in s and len(s) == 10:
            d = datetime.datetime.strptime(s, '%d.%m.%Y').date()
        elif '-' in s:
            d = datetime.datetime.strptime(s[:10], '%Y-%m-%d').date()
        else:
            return 0
        return max(0, (datetime.date.today() - d).days)
    except ValueError:
        return 0


def _normalize(s: str) -> str:
    """Fold Unicode letters to ASCII for comparison."""
    return (s.lower().strip()
            .replace('ş', 's').replace('ğ', 'g').replace('ü', 'u')
            .replace('ö', 'o').replace('ı', 'i').replace('ç', 'c')
            .replace('İ', 'i').replace('Ş', 's').replace('Ğ', 'g'))


def status_color(status) -> colors.Color:
    d = _normalize(str(status))
    if 'paid' in d or 'odendi' in d:
        return R.PAID
    if 'partial' in d or 'kismi' in d:
        return R.PARTIAL
    return R.UNPAID


def profile_info(profile_name: str):
    """Return (color, index). Falls back to Uncertain if unknown."""
    p = _normalize(profile_name)
    mapping = {
        'perfect':     (R.PERFECT,     0),
        'sorunsuz':    (R.PERFECT,     0),
        'reliable':    (R.RELIABLE,    1),
        'guvenilir':   (R.RELIABLE,    1),
        'uncertain':   (R.UNCERTAIN,   2),
        'belirsiz':    (R.UNCERTAIN,   2),
        'risky':       (R.RISKY,       3),
        'riskli':      (R.RISKY,       3),
        'problematic': (R.PROBLEMATIC, 4),
        'sorunlu':     (R.PROBLEMATIC, 4),
    }
    return mapping.get(p, (R.UNCERTAIN, 2))


def today_str() -> str:
    return datetime.date.today().strftime('%d.%m.%Y')


def calculate_profile(receivables: list) -> str:
    """Calculate a simple profile from a list of receivables."""
    if not receivables:
        return 'Uncertain'
    total    = len(receivables)
    paid     = sum(1 for a in receivables
                   if 'paid' in _normalize(str(a.get('status', ''))))
    overdue  = sum(1 for a in receivables
                   if days_overdue(a.get('due_date', '')) > 0
                   and 'paid' not in _normalize(str(a.get('status', ''))))
    ratio     = paid    / total
    over_ratio = overdue / total

    if ratio >= 0.95 and over_ratio == 0:
        return 'Perfect'
    if ratio >= 0.80 and over_ratio < 0.20:
        return 'Reliable'
    if over_ratio < 0.50:
        return 'Uncertain'
    if over_ratio < 0.75:
        return 'Risky'
    return 'Problematic'


# ── DESIGN COMPONENTS ─────────────────────────────────────────────────────────
_ACCENT = colors.HexColor('#1ABC9C')  # teal — consistent accent color across all reports


def draw_header(canvas, company_name: str, report_type: str, subtitle: str = ''):
    """Common header for all reports.

    Layout (2.8 cm height):
      top-left  : company name  — 22pt bold white (LARGEST)
      bottom-left: report type  — 11pt teal
      top-right : subtitle      —  9pt grey (period etc.)
      bottom strip: 0.3 cm teal accent
    """
    HEADER_H = 2.8 * cm
    # Dark background
    canvas.setFillColor(R.HEADER)
    canvas.rect(0, H - HEADER_H, W, HEADER_H, fill=1, stroke=0)
    # Bottom teal strip
    canvas.setFillColor(_ACCENT)
    canvas.rect(0, H - HEADER_H, W, 0.3 * cm, fill=1, stroke=0)
    # Company name — primary element (22pt bold white)
    canvas.setFont('DejaVu-Bold', 22)
    canvas.setFillColor(colors.white)
    canvas.drawString(MARGIN, H - 1.3 * cm, company_name)
    # Report type — secondary (11pt teal)
    canvas.setFont('DejaVu', 11)
    canvas.setFillColor(_ACCENT)
    canvas.drawString(MARGIN, H - 2.1 * cm, report_type)
    # Subtitle — top right (9pt grey)
    if subtitle:
        canvas.setFont('DejaVu', 9)
        canvas.setFillColor(colors.HexColor('#95A5A6'))
        canvas.drawRightString(W - MARGIN, H - 1.3 * cm, subtitle)


def section_heading(text: str, content_w: float = None) -> list:
    """Section heading with a teal accent bar on the left edge.
    Returns [Spacer, Table, Spacer] — use story.extend() to add.
    To get only the Table: section_heading(...)[1]
    """
    if content_w is None:
        content_w = CONTENT_W
    BAR_W = 0.4 * cm
    s = ParagraphStyle('_bb', fontName='DejaVu-Bold', fontSize=11,
                       textColor=R.TEXT, leading=15)
    bar_t = Table([['']], colWidths=[BAR_W], rowHeights=[0.75 * cm])
    bar_t.setStyle(TableStyle([
        ('BACKGROUND',    (0, 0), (0, 0), _ACCENT),
        ('TOPPADDING',    (0, 0), (0, 0), 0),
        ('BOTTOMPADDING', (0, 0), (0, 0), 0),
        ('LEFTPADDING',   (0, 0), (0, 0), 0),
        ('RIGHTPADDING',  (0, 0), (0, 0), 0),
    ]))
    outer = Table([[bar_t, Paragraph(text, s)]],
                  colWidths=[BAR_W + 0.2 * cm, content_w - BAR_W - 0.2 * cm])
    outer.setStyle(TableStyle([
        ('VALIGN',        (0, 0), (-1, -1), 'MIDDLE'),
        ('LEFTPADDING',   (0, 0), (-1, -1), 0),
        ('RIGHTPADDING',  (0, 0), (-1, -1), 0),
        ('TOPPADDING',    (0, 0), (-1, -1), 4),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
        ('BACKGROUND',    (0, 0), (-1, -1), colors.HexColor('#EBF9F7')),
    ]))
    return [Spacer(1, 0.28 * cm), outer, Spacer(1, 0.12 * cm)]


def create_card(value_str: str, label: str, color, width: float,
                value_fs: int = 14, label_fs: int = 9,
                padding_v: int = 10) -> Table:
    """KPI card with a colored accent strip at the top."""
    w = width - 0.2 * cm
    s_d = ParagraphStyle('_kd', fontName='DejaVu-Bold', fontSize=value_fs,
                          textColor=color, alignment=TA_CENTER,
                          leading=int(value_fs * 1.4))
    s_l = ParagraphStyle('_kl', fontName='DejaVu', fontSize=label_fs,
                          textColor=R.TEXT_MUTED, alignment=TA_CENTER,
                          leading=int(label_fs * 1.45))
    t = Table(
        [[''], [Paragraph(value_str, s_d)], [Paragraph(label, s_l)]],
        colWidths=[w],
        rowHeights=[10, None, None],
    )
    t.setStyle(TableStyle([
        ('BACKGROUND',    (0, 0), (0, 0), color),
        ('BACKGROUND',    (0, 1), (0, 2), R.CARD_BG),
        ('BOX',           (0, 0), (-1, -1), 0.5, R.CARD_BORD),
        ('TOPPADDING',    (0, 0), (0, 0), 0),
        ('BOTTOMPADDING', (0, 0), (0, 0), 0),
        ('LEFTPADDING',   (0, 0), (0, 0), 0),
        ('RIGHTPADDING',  (0, 0), (0, 0), 0),
        ('TOPPADDING',    (0, 1), (0, 1), padding_v),
        ('BOTTOMPADDING', (0, 1), (0, 1), 3),
        ('TOPPADDING',    (0, 2), (0, 2), 2),
        ('BOTTOMPADDING', (0, 2), (0, 2), padding_v),
        ('LEFTPADDING',   (0, 1), (0, 2), 4),
        ('RIGHTPADDING',  (0, 1), (0, 2), 4),
        ('VALIGN',        (0, 0), (-1, -1), 'MIDDLE'),
    ]))
    return t


# ── PAGE NUMBER CANVAS ────────────────────────────────────────────────────────
class PageNumberCanvas(rl_canvas.Canvas):
    """Canvas that writes 'Page X / Y' using a two-pass approach."""

    def __init__(self, *args, **kwargs):
        rl_canvas.Canvas.__init__(self, *args, **kwargs)
        self._page_states = []

    def showPage(self):
        self._page_states.append(dict(self.__dict__))
        self._startPage()

    def save(self):
        total = len(self._page_states)
        for status in self._page_states:
            self.__dict__.update(status)
            self._write_page_number(total)
            rl_canvas.Canvas.showPage(self)
        rl_canvas.Canvas.save(self)

    def _write_page_number(self, total: int):
        self.setFont('DejaVu', 8)
        self.setFillColor(R.FOOTER)
        self.drawRightString(
            W - MARGIN,
            0.6 * cm,
            f'Page {self._pageNumber} / {total}',
        )
