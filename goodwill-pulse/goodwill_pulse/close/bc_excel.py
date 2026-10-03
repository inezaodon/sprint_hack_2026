"""Write a Journal as a Business Central "Edit in Excel" General Journal workbook (openpyxl).

Sheets, in order:
  All lines        every line, BC General Journal columns, ready to paste / import into batch GENERAL / ECOM
  Control totals   per document: lines, debits, credits, difference, balanced; then grand totals
  <doc sheets>     one sheet per document (same columns), with a totals row two rows under the last line

Cells are typed: dates are Excel dates, money is numeric with a 2-decimal format, line numbers are integers.
The header row is frozen and filtered on every sheet.
"""
from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from .journal import BATCH, BC_COLUMNS, TEMPLATE, Journal, JournalDocument

MONEY_FMT = "#,##0.00"
DATE_FMT = "yyyy-mm-dd"
HEADER_FILL = PatternFill("solid", fgColor="DCEFEB")
FLAG_FILL = PatternFill("solid", fgColor="FAE3E0")
BOLD = Font(bold=True)
FIXED_TS = datetime(2026, 1, 1)          # deterministic workbook metadata

WIDTHS = {"Description": 48, "Source Ref": 34, "Document No.": 24, "Account No.": 14, "Account Type": 14,
          "Posting Date": 12, "Debit Amount": 14, "Credit Amount": 14, "Journal Template Name": 10,
          "Journal Batch Name": 10, "Bal. Account Type": 12, "Bal. Account No.": 12, "Source": 16}


def journal_filename(journal: Journal) -> str:
    return f"BC General Journal ECOM {journal.month:%Y-%m}.xlsx"


def _sheet_title(name: str, used: set[str]) -> str:
    t = re.sub(r"[\[\]:*?/\\]", "-", name)[:31] or "Document"
    base, i = t, 2
    while t.lower() in used:
        suffix = f" ({i})"
        t = base[:31 - len(suffix)] + suffix
        i += 1
    used.add(t.lower())
    return t


def _header(ws, columns: list[str]) -> None:
    ws.append(columns)
    for c in ws[1]:
        c.font = BOLD
        c.fill = HEADER_FILL
        c.alignment = Alignment(vertical="center")
    ws.freeze_panes = "A2"
    for i, name in enumerate(columns, 1):
        ws.column_dimensions[get_column_letter(i)].width = WIDTHS.get(name, 12)


def _format_lines(ws, first_row: int, last_row: int) -> None:
    di, ci, pi = BC_COLUMNS.index("Debit Amount") + 1, BC_COLUMNS.index("Credit Amount") + 1, \
        BC_COLUMNS.index("Posting Date") + 1
    for r in range(first_row, last_row + 1):
        ws.cell(r, di).number_format = MONEY_FMT
        ws.cell(r, ci).number_format = MONEY_FMT
        ws.cell(r, pi).number_format = DATE_FMT


def _write_lines(ws, lines) -> None:
    _header(ws, BC_COLUMNS)
    for l in lines:
        ws.append(l.bc_row())
    if lines:
        _format_lines(ws, 2, len(lines) + 1)
        ws.auto_filter.ref = f"A1:{get_column_letter(len(BC_COLUMNS))}{len(lines) + 1}"


def _doc_sheet(ws, doc: JournalDocument) -> None:
    _write_lines(ws, doc.lines)
    r = len(doc.lines) + 3
    di, ci = BC_COLUMNS.index("Debit Amount") + 1, BC_COLUMNS.index("Credit Amount") + 1
    ws.cell(r, di - 1, "Document total").font = BOLD
    for col, v in ((di, doc.total_debit), (ci, doc.total_credit)):
        c = ws.cell(r, col, float(v))
        c.number_format, c.font = MONEY_FMT, BOLD
    c = ws.cell(r + 1, di - 1, "Balanced" if doc.balanced else f"OUT OF BALANCE by {doc.difference:,.2f}")
    c.font = BOLD
    if not doc.balanced:
        c.fill = FLAG_FILL


CONTROL_COLUMNS = ["Document No.", "Source", "Posting Date", "Lines", "Total Debit", "Total Credit", "Difference",
                   "Balanced", "Placeholder accounts"]


def _control_sheet(ws, journal: Journal) -> None:
    _header(ws, CONTROL_COLUMNS)
    for i, w in enumerate([24, 16, 12, 8, 16, 16, 12, 10, 20], 1):
        ws.column_dimensions[get_column_letter(i)].width = w
    for d in journal.documents:
        ws.append([d.document_no, d.source, d.posting_date, len(d.lines), float(d.total_debit),
                   float(d.total_credit), float(d.difference), "Yes" if d.balanced else "NO",
                   "Yes" if d.has_placeholder else ""])
        if not d.balanced:
            for c in ws[ws.max_row]:
                c.fill = FLAG_FILL
    ws.append([])
    ws.append(["TOTAL", "", None, len(journal.lines), float(journal.total_debit), float(journal.total_credit),
               float(journal.total_debit - journal.total_credit), "Yes" if journal.balanced else "NO", ""])
    for c in ws[ws.max_row]:
        c.font = BOLD
    for r in range(2, ws.max_row + 1):
        ws.cell(r, 3).number_format = DATE_FMT
        for col in (5, 6, 7):
            ws.cell(r, col).number_format = MONEY_FMT
    ws.append([])
    ws.append([f"Month {journal.month:%Y-%m} · rules version {journal.rules_version or 'n/a'} · "
               f"journal template {TEMPLATE} / batch {BATCH} · synthetic data"])


def write_journal_xlsx(journal: Journal, out_dir: Path, filename: str | None = None) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / (filename or journal_filename(journal))
    wb = Workbook()
    used: set[str] = set()
    ws = wb.active
    ws.title = _sheet_title("All lines", used)
    _write_lines(ws, journal.lines)
    _control_sheet(wb.create_sheet(_sheet_title("Control totals", used)), journal)
    for d in journal.documents:
        _doc_sheet(wb.create_sheet(_sheet_title(d.document_no, used)), d)
    wb.properties.creator = "Goodwill Pulse close"
    wb.properties.created = FIXED_TS
    wb.properties.modified = FIXED_TS
    tmp = path.with_suffix(".tmp.xlsx")
    wb.save(tmp)
    tmp.replace(path)
    return path
