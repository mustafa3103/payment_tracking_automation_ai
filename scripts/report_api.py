#!/usr/bin/env python3
"""
Report API — Flask-based PDF generation service.

n8n sends data fetched from Google Sheets as JSON.
This service generates a PDF and returns base64 + a short summary.

Endpoints:
  POST /report/monthly  → {clients, receivables, year, month, company_name}
  POST /report/yearly   → {clients, receivables, year, company_name}
  POST /report/weekly   → {clients, receivables, company_name}
  POST /report/client   → {client, receivables}
  GET  /health          → {"status": "ok"}
"""

import base64
import datetime
import os
import sys
import tempfile

from flask import Flask, jsonify, request

sys.path.insert(0, os.path.dirname(__file__))

from monthly_report  import generate_monthly_report
from weekly_report   import generate_weekly_report
from client_report   import generate_client_report
from yearly_report   import generate_yearly_report, generate_general_report
from base import parse_date, fmt_amount

app = Flask(__name__)

COMPANY_NAME = os.environ.get('COMPANY_NAME', 'Payment Tracking System')


# ── Helpers ────────────────────────────────────────────────────────────────────

def _pdf_to_base64(file: str) -> str:
    with open(file, 'rb') as f:
        return base64.b64encode(f.read()).decode('utf-8')


def _tmp_file(suffix: str) -> str:
    fd, path = tempfile.mkstemp(suffix=suffix)
    os.close(fd)
    return path


def _fl(val) -> float:
    try:
        return float(str(val or 0).replace(',', '.').replace(' ', ''))
    except (ValueError, TypeError):
        return 0.0


def _prepare_receivables(raw: list) -> list:
    """
    Normalise receivables coming from n8n Google Sheets.
    XLSX formula fields (status, remaining_amount) are computed manually.
    """
    result = []
    for a in raw:
        paid_amount  = _fl(a.get('paid_amount', 0))
        total_amount = _fl(a.get('total_amount', 0))
        a['status']           = ('Paid' if paid_amount >= total_amount
                                 else ('Unpaid' if paid_amount <= 0 else 'Partial'))
        a['remaining_amount'] = max(0.0, total_amount - paid_amount)
        result.append(a)
    return result


def _fmt_currency(n: float) -> str:
    """Currency format: 1,129,000 USD"""
    return f"${int(round(n)):,}"


def _make_summary(receivables: list, label: str) -> str:
    total     = sum(_fl(a.get('total_amount', 0))     for a in receivables)
    paid      = sum(_fl(a.get('paid_amount', 0))      for a in receivables)
    remaining = sum(a.get('remaining_amount', 0)      for a in receivables)
    n_clients = len({a.get('client_id') for a in receivables if a.get('client_id')})
    return (f"📊 *{label}*\n"
            f"👥 {n_clients} clients | {len(receivables)} receivables\n"
            f"💰 Total: {_fmt_currency(total)}\n"
            f"✅ Paid: {_fmt_currency(paid)}\n"
            f"🔴 Remaining: {_fmt_currency(remaining)}")


def _json_error(message: str, code: int = 400):
    return jsonify({'error': message}), code


def _safe_int(val, default: int) -> int:
    try:
        return int(val)
    except (TypeError, ValueError):
        return default


def _filter_monthly(receivables: list, year: int, month: int) -> list:
    """Return receivables whose due_date falls in the given month/year."""
    return [a for a in receivables
            if (d := parse_date(a.get('due_date'))) and d.year == year and d.month == month]


def _filter_yearly(receivables: list, year: int) -> list:
    """Return receivables whose due_date falls in the given year."""
    return [a for a in receivables
            if (d := parse_date(a.get('due_date'))) and d.year == year]


# ── Endpoints ──────────────────────────────────────────────────────────────────

@app.get('/health')
def health():
    return jsonify({'status': 'ok'})


@app.post('/report/monthly')
def monthly():
    data        = request.get_json(force=True)
    clients     = data.get('clients', [])
    receivables = _prepare_receivables(data.get('receivables', []))
    year        = _safe_int(data.get('year'), datetime.date.today().year)
    month       = _safe_int(data.get('month'), datetime.date.today().month)
    company     = data.get('company_name', COMPANY_NAME)

    if not clients or not receivables:
        return _json_error('clients and receivables are required')
    if not (1 <= month <= 12):
        return _json_error('month must be between 1 and 12')
    if not (2000 <= year <= 2100):
        return _json_error('year must be between 2000 and 2100')

    # Filter to receivables due in the requested month (due_date filter)
    receivables_month = _filter_monthly(receivables, year, month)

    tmp = _tmp_file('.pdf')
    try:
        generate_monthly_report(clients, receivables_month, year=year, month=month,
                                company_name=company, output_file=tmp)
        b64 = _pdf_to_base64(tmp)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)

    month_names = ['', 'January', 'February', 'March', 'April', 'May', 'June',
                   'July', 'August', 'September', 'October', 'November', 'December']
    label    = f"{month_names[month]} {year} Monthly Report"
    filename = f"monthly_{year}_{month:02d}.pdf"

    return jsonify({
        'base64':   b64,
        'filename': filename,
        'summary':  _make_summary(receivables_month, label),
    })


@app.post('/report/yearly')
def yearly():
    data        = request.get_json(force=True)
    clients     = data.get('clients', [])
    receivables = _prepare_receivables(data.get('receivables', []))
    year        = _safe_int(data.get('year'), datetime.date.today().year)
    company     = data.get('company_name', COMPANY_NAME)
    if not (2000 <= year <= 2100):
        return _json_error('year must be between 2000 and 2100')

    if not clients or not receivables:
        return _json_error('clients and receivables are required')

    # Filter to receivables due in the requested year (due_date filter)
    receivables_year = _filter_yearly(receivables, year)

    if not receivables_year:
        return _json_error(f'No receivables found for year {year}', 404)

    tmp = _tmp_file('.pdf')
    try:
        generate_yearly_report(clients, receivables_year, year=year,
                               company_name=company, output_file=tmp)
        b64 = _pdf_to_base64(tmp)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)

    return jsonify({
        'base64':   b64,
        'filename': f"yearly_{year}.pdf",
        'summary':  _make_summary(receivables_year, f"{year} Yearly Report"),
    })


@app.post('/report/weekly')
def weekly():
    data        = request.get_json(force=True)
    clients     = data.get('clients', [])
    receivables = _prepare_receivables(data.get('receivables', []))
    company     = data.get('company_name', COMPANY_NAME)

    if not clients or not receivables:
        return _json_error('clients and receivables are required')

    # Check for receivables due this week
    today      = datetime.date.today()
    week_start = today - datetime.timedelta(days=today.weekday())
    week_end   = week_start + datetime.timedelta(days=6)
    this_week  = [a for a in receivables
                  if parse_date(a.get('due_date'))
                  and week_start <= parse_date(a.get('due_date')) <= week_end]

    # No dues this week — skip PDF generation
    if not this_week:
        period = f"{week_start.strftime('%d.%m.%Y')} – {week_end.strftime('%d.%m.%Y')}"
        return jsonify({
            'base64':   '',
            'filename': '',
            'summary':  f"📅 No payments due this week ({period}).",
            'no_pdf':   True,
        })

    tmp = _tmp_file('.pdf')
    try:
        generate_weekly_report(clients, receivables, company_name=company, output_file=tmp)
        b64 = _pdf_to_base64(tmp)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)

    return jsonify({
        'base64':   b64,
        'filename': 'weekly_report.pdf',
        'summary':  _make_summary(this_week, 'Due This Week'),
    })


@app.post('/report/client')
def client_report():
    data        = request.get_json(force=True)
    client_dict = data.get('client')
    receivables = _prepare_receivables(data.get('receivables', []))
    company     = data.get('company_name', COMPANY_NAME)

    if not client_dict:
        return _json_error('client is required')

    tmp = _tmp_file('.pdf')
    try:
        generate_client_report(client_dict, receivables, company_name=company, output_file=tmp)
        b64 = _pdf_to_base64(tmp)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)

    name = client_dict.get('full_name', 'Client')
    return jsonify({
        'base64':   b64,
        'filename': f"client_{client_dict.get('client_id', 'report')}.pdf",
        'summary':  _make_summary(receivables, f"{name} Client Report"),
    })


@app.post('/report/general')
def general():
    data        = request.get_json(force=True)
    clients     = data.get('clients', [])
    receivables = _prepare_receivables(data.get('receivables', []))
    company     = data.get('company_name', COMPANY_NAME)

    if not clients or not receivables:
        return _json_error('clients and receivables are required')

    tmp = _tmp_file('.pdf')
    try:
        generate_general_report(clients, receivables,
                                company_name=company, output_file=tmp)
        b64 = _pdf_to_base64(tmp)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)

    return jsonify({
        'base64':   b64,
        'filename': 'general_report.pdf',
        'summary':  _make_summary(receivables, 'All Periods General Report'),
    })


# ── Start ──────────────────────────────────────────────────────────────────────

if __name__ == '__main__':
    port = int(os.environ.get('REPORT_API_PORT', 5050))
    app.run(host='0.0.0.0', port=port)
