"""Demo files for upload ingest: the slide 38 sources that DON'T arrive as a spreadsheet.

    python -m goodwill_pulse.gen.other_formats [--out demo_other_formats] [--month 2026-09]

Built from data/sources/finance.duckdb (and one Upright CSV from demo_data), so loading them back must reproduce
the same finance rows: the round-trip test in tests/test_upload_ingest.py proves the converters lose nothing.

  01_upright_email_delivery.eml                 Upright "email delivery": the paid-orders CSV attached to an email
  02_goodwill_books_statement_<month>.eml       Goodwill Books: monthly email with the payment statement PDF attached
  03_goodwill_books_statement_<prev>_misread.pdf the previous statement with one line missing from the table: the lines
                                                no longer add up to the printed net, so it must be HELD, not loaded
  04_bank_0101_<month>.ofx                      1st Source account 0101 download (OFX / Quicken format)
  05_fedex_billing_<month>.eml                  FedEx billing notification with the invoice lines as an HTML table
  06_jewelry_report_<month>.zip                 Jewelry Report as a tab-delimited .txt (with title lines) in a zip

Every layout here is ASSUMED (no real samples yet); the readers are generic, so real files mostly need aliases in
config/finance_layouts, not code.
"""
from __future__ import annotations

import argparse
import io
import zipfile
from datetime import date, datetime
from email.message import EmailMessage
from email.utils import format_datetime
from pathlib import Path

import duckdb

from ..config import FINANCE_DB_PATH, ROOT

OUT = ROOT / "demo_other_formats"   # not under demo_data/: gen/demo_pack.py wipes that folder
UPRIGHT_CSV = ROOT / "demo_data" / "02_friday_2026-10-02" / "paid_orders_10-02-2026_10-02-2026.csv"


# --------------------------------------------------------------------------------------------- minimal PDF writer
_W = {**{c: 556 for c in "0123456789$"}, ".": 278, ",": 278, "-": 333, "(": 333, ")": 333, " ": 278,
      "A": 667, "m": 833, "o": 556, "u": 556, "n": 556, "t": 278}     # Helvetica widths (1/1000 em)


def text_width(s: str, size: float) -> float:
    return sum(_W.get(c, 556) for c in s) * size / 1000


def _esc(s: str) -> str:
    return s.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def write_pdf(pages: list[list[tuple]], width: int = 612, height: int = 792) -> bytes:
    """pages: [[(x, y, text, size, bold), ...], ...] -> a PDF with a real text layer (like a system-made statement)."""
    objs: list[bytes] = [b"", b"", b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>",
                         b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold /Encoding /WinAnsiEncoding >>"]
    kids = []
    for items in pages:
        ops = []
        for x, y, text, size, bold in items:
            ops.append(f"BT /{'F2' if bold else 'F1'} {size} Tf {x:.2f} {y:.2f} Td ({_esc(text)}) Tj ET")
        stream = "\n".join(ops).encode("cp1252", "replace")
        objs.append(b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream")
        content_no = len(objs)
        objs.append(f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {width} {height}] /Contents {content_no} 0 R "
                    f"/Resources << /Font << /F1 3 0 R /F2 4 0 R >> >> >>".encode())
        kids.append(len(objs))
    objs[0] = b"<< /Type /Catalog /Pages 2 0 R >>"
    objs[1] = f"<< /Type /Pages /Kids [{' '.join(f'{k} 0 R' for k in kids)}] /Count {len(kids)} >>".encode()
    out = io.BytesIO()
    out.write(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = []
    for i, body in enumerate(objs, 1):
        offsets.append(out.tell())
        out.write(b"%d 0 obj\n" % i + body + b"\nendobj\n")
    xref = out.tell()
    out.write(b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1))
    for off in offsets:
        out.write(b"%010d 00000 n \n" % off)
    out.write(b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objs) + 1, xref))
    return out.getvalue()


# ------------------------------------------------------------------------------------------- Goodwill Books PDF
def _money(v) -> str:
    v = float(v)
    return f"({abs(v):,.2f})" if v < 0 else f"{v:,.2f}"


def gwb_statement_pdf(con: duckdb.DuckDBPyConnection, month: str, drop_line: int | None = None) -> bytes:
    lines = con.execute("SELECT line_no, line_date, line_type, order_no, sku, description, amount FROM gwb_statement "
                        "WHERE statement_month = ? ORDER BY line_no", [month]).fetchall()
    hdr = con.execute("SELECT statement_date, gross, fees, refunds, net, paid_on FROM gwb_statement_header "
                      "WHERE statement_month = ?", [month]).fetchone()
    if drop_line is not None:
        lines = [l for l in lines if l[0] != drop_line]
    size, lead, top, bottom = 8, 11, 742, 70
    cols = [("Line", 40), ("Date", 72), ("Type", 124), ("Order No", 176), ("SKU", 236), ("Description", 316)]
    amount_right = 572
    period = datetime.strptime(month, "%Y-%m").strftime("%B %Y")

    def header_row(y):
        row = [(x, y, name, size, True) for name, x in cols]
        row.append((amount_right - text_width("Amount", size), y, "Amount", size, True))
        return row

    pages, items, y = [], [], top
    items += [(40, y, "Goodwill Books", 14, True), (40, y - 18, "Seller payment statement", 10, False),
              (40, y - 40, "Seller: Goodwill Industries of Michiana", size, False),
              (40, y - 52, f"Statement period: {period}", size, False),
              (40, y - 64, f"Statement date: {hdr[0]:%m/%d/%Y}", size, False)]
    y -= 90
    items += header_row(y)
    y -= lead
    for ln in lines:
        if y < bottom:
            pages.append(items)
            items, y = header_row(top), top - lead
        no, d, typ, order, sku, desc, amt = ln
        cells = [str(no), f"{d:%m/%d/%Y}", typ, order or "", sku or "", desc or ""]
        items += [(x, y, c, size, False) for (_, x), c in zip(cols, cells) if c]
        a = _money(amt)
        items.append((amount_right - text_width(a, size), y, a, size, False))
        y -= lead
    if y < bottom + 6 * lead:
        pages.append(items)
        items, y = [], top
    y -= lead
    for label, val in (("Gross sales", f"${float(hdr[1]):,.2f}"), ("Total fees", _money(-hdr[2])),
                       ("Total refunds", _money(-hdr[3])), ("Net payment", f"${float(hdr[4]):,.2f}")):
        items += [(316, y, label, size, label == "Net payment"),
                  (amount_right - text_width(val, size), y, val, size, label == "Net payment")]
        y -= lead
    items.append((316, y - lead, f"Paid on: {hdr[5]:%m/%d/%Y}", size, False))
    pages.append(items)
    for i, p in enumerate(pages, 1):
        p.append((280, 36, f"Page {i} of {len(pages)}", 7, False))
    return write_pdf(pages)


# ----------------------------------------------------------------------------------------------------- the rest
def _email(sender: str, subject: str, sent: datetime, message_id: str, text: str, html: str | None = None) -> EmailMessage:
    m = EmailMessage()
    m["From"], m["To"] = sender, "ecommerce-accounting@goodwillmichiana.example"
    m["Subject"], m["Date"], m["Message-ID"] = subject, format_datetime(sent), message_id
    m.set_content(text)
    if html:
        m.add_alternative(html, subtype="html")
    return m


def bank_ofx(con, month: str) -> bytes:
    rows = con.execute("SELECT post_date, description, amount, type, reference FROM bank_0101 WHERE account_no = '0101' "
                       "AND strftime(post_date, '%Y-%m') = ? ORDER BY post_date, line_id", [month]).fetchall()
    trntype = {"ACH_DEBIT": "DIRECTDEBIT", "ACH_CREDIT": "DIRECTDEP", "BNKDEPOSIT": "DEP", "SERVICE_CHARGE": "SRVCHG"}
    tx = []
    for i, (d, desc, amt, typ, ref) in enumerate(rows, 1):
        tx.append(f"<STMTTRN>\n<TRNTYPE>{trntype.get(typ, 'OTHER')}\n<DTPOSTED>{d:%Y%m%d}120000[-5:EST]\n"
                  f"<TRNAMT>{float(amt):.2f}\n<FITID>{d:%Y%m%d}{i:05d}\n<REFNUM>{ref or ''}\n<NAME>{desc}\n</STMTTRN>")
    start, end = rows[0][0], rows[-1][0]
    body = "\n".join(tx)
    return f"""OFXHEADER:100
DATA:OFXSGML
VERSION:102
SECURITY:NONE
ENCODING:USASCII
CHARSET:1252
COMPRESSION:NONE
OLDFILEUID:NONE
NEWFILEUID:NONE

<OFX>
<SIGNONMSGSRSV1><SONRS><STATUS><CODE>0<SEVERITY>INFO</STATUS><DTSERVER>{end:%Y%m%d}235959<LANGUAGE>ENG</SONRS></SIGNONMSGSRSV1>
<BANKMSGSRSV1><STMTTRNRS><TRNUID>1<STATUS><CODE>0<SEVERITY>INFO</STATUS>
<STMTRS><CURDEF>USD<BANKACCTFROM><BANKID>071000301<ACCTID>4410000101<ACCTTYPE>CHECKING</BANKACCTFROM>
<BANKTRANLIST><DTSTART>{start:%Y%m%d}<DTEND>{end:%Y%m%d}
{body}
</BANKTRANLIST>
</STMTRS></STMTTRNRS></BANKMSGSRSV1>
</OFX>
""".encode("ascii", "replace")


def fedex_email(con, month: str) -> bytes:
    rows = con.execute("SELECT invoice_no, invoice_date, line_type, tracking, ship_date, amount, orig_invoice_no, "
                       "refund_ref FROM fedex_invoices WHERE strftime(invoice_date, '%Y-%m') = ? "
                       "ORDER BY invoice_date, line_id", [month]).fetchall()
    head = ["Invoice Number", "Invoice Date", "Line Type", "Tracking ID", "Ship Date", "Net Charge",
            "Original Invoice", "Refund Reference"]
    cell = lambda v: "" if v is None else (f"{v:%m/%d/%Y}" if isinstance(v, date) else
                                          f"{float(v):.2f}" if not isinstance(v, str) else v)
    trs = "\n".join("<tr>" + "".join(f"<td>{cell(v)}</td>" for v in r) + "</tr>" for r in rows)
    html = f"""<html><body>
<table width="100%"><tr><td><img src="cid:logo" alt="FedEx Billing Online"></td></tr>
<tr><td><p>Your invoices for {month} are ready. The line detail is below.</p>
<table border="1" cellpadding="3"><tr>{''.join(f'<th>{h}</th>' for h in head)}</tr>
{trs}
</table>
<p>Questions? Reply to this email.</p></td></tr></table></body></html>"""
    m = _email("FedEx Billing Online <billing@fedex-billing.example>", f"FedEx invoices ready - {month}",
               datetime(2026, 10, 1, 7, 30), f"<fedex-{month}@fedex-billing.example>",
               "Your invoices are ready. View this email in HTML to see the line detail.", html)
    return bytes(m)


def jewelry_zip(con, month: str) -> bytes:
    rows = con.execute("SELECT channel, order_ref, sale_date, sku, title, sale_price, supplier FROM jewelry_report "
                       "WHERE report_month = ? ORDER BY line_no", [month]).fetchall()
    names = {"shopgoodwill": "ShopGoodwill", "ebay": "eBay", "goodwillfinds": "GoodwillFinds"}
    lines = ["Upright Labs - Jewelry Report", f"Report month: {month}\tGenerated: 10/01/2026 06:00", "",
             "\t".join(["Channel", "Order", "Sale Date", "SKU", "Title", "Sale Price", "Supplier"])]
    for ch, order, d, sku, title, price, sup in rows:
        lines.append("\t".join([names.get(ch, ch), order or "", f"{d:%m/%d/%Y}", sku or "", title or "",
                                f"{float(price):.2f}", sup or ""]))
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(f"jewelry_report_{month}.txt", "\n".join(lines) + "\n")
        z.writestr("__MACOSX/._jewelry_report.txt", b"\x00")
    return buf.getvalue()


def build(out_dir: Path = OUT, finance_path: Path = FINANCE_DB_PATH, month: str = "2026-09") -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    y, m = map(int, month.split("-"))
    prev = f"{y - (m == 1):04d}-{(m - 2) % 12 + 1:02d}"
    con = duckdb.connect(str(finance_path), read_only=True)
    written = []

    def put(name: str, data: bytes) -> None:
        (out_dir / name).write_bytes(data)
        written.append(out_dir / name)

    try:
        if UPRIGHT_CSV.exists():
            e = _email("Upright Labs <reports@uprightlabs.example>", "Your Upright report is ready: Paid orders",
                       datetime(2026, 10, 3, 6, 5), "<upright-paid-orders-20261002@uprightlabs.example>",
                       "Your requested report is attached.\n\nUpright Labs")
            e.add_attachment(UPRIGHT_CSV.read_bytes(), maintype="text", subtype="csv", filename=UPRIGHT_CSV.name)
            put("01_upright_email_delivery.eml", bytes(e))

        e = _email("Goodwill Books Seller Payments <statements@goodwillbooks.example>",
                   f"Your Goodwill Books payment statement - {datetime.strptime(month, '%Y-%m'):%B %Y}",
                   datetime(2026, 10, 1, 8, 0), f"<gwb-statement-{month}@goodwillbooks.example>",
                   "Hello,\n\nYour monthly payment statement is attached.\n\nGoodwill Books Seller Support")
        e.add_attachment(gwb_statement_pdf(con, month), maintype="application", subtype="pdf",
                         filename=f"GWB_Statement_{month}.pdf")
        put(f"02_goodwill_books_statement_{month}.eml", bytes(e))

        n = con.execute("SELECT max(line_no) FROM gwb_statement WHERE statement_month = ?", [prev]).fetchone()[0]
        if n:
            put(f"03_goodwill_books_statement_{prev}_misread.pdf", gwb_statement_pdf(con, prev, drop_line=n // 2))

        put(f"04_bank_0101_{month}.ofx", bank_ofx(con, month))
        put(f"05_fedex_billing_{month}.eml", fedex_email(con, month))
        put(f"06_jewelry_report_{month}.zip", jewelry_zip(con, month))
    finally:
        con.close()
    return written


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--out", default=str(OUT))
    p.add_argument("--finance", default=str(FINANCE_DB_PATH))
    p.add_argument("--month", default="2026-09")
    a = p.parse_args(argv)
    for path in build(Path(a.out), Path(a.finance), a.month):
        print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
