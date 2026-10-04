"""Upload ingest: files that aren't spreadsheets become spreadsheets, then load (or wait for a person)."""
import io
import json
import shutil
import zipfile
from datetime import date, datetime
from email.message import EmailMessage
from pathlib import Path
from types import SimpleNamespace

import duckdb
import pandas as pd
import pytest

from goodwill_pulse import db
from goodwill_pulse.ai import client as ai
from goodwill_pulse.config import FINANCE_DB_PATH, ROOT
from goodwill_pulse.gen import other_formats as gen
from goodwill_pulse.ingest import finance, intake as intake_mod
from goodwill_pulse.ingest.convert import convert, sniff, tabular
from goodwill_pulse.sources.finance import FINANCE_DDL

UPRIGHT_CSV = ROOT / "demo_data" / "02_friday_2026-10-02" / "paid_orders_10-02-2026_10-02-2026.csv"


@pytest.fixture(autouse=True)
def no_ai(monkeypatch):
    monkeypatch.setenv("GOODWILL_AI", "off")
    ai.set_client(None)
    yield
    ai.set_client(None)


@pytest.fixture
def env(tmp_path, monkeypatch):
    """A temp warehouse + finance DB; archives and converted spreadsheets go under tmp_path."""
    monkeypatch.setattr("goodwill_pulse.ingest.pipeline.ARCHIVE_DIR", tmp_path / "archive")
    monkeypatch.setattr(intake_mod, "CONVERTED_DIR", tmp_path / "converted")
    fin = tmp_path / "finance.duckdb"
    monkeypatch.setattr(intake_mod, "FINANCE_DB_PATH", fin)
    con = db.connect(tmp_path / "w.duckdb")
    yield SimpleNamespace(con=con, fin=fin, tmp=tmp_path)
    con.close()


def _email(subject="Report", msg_id="<a@example.test>", text="See attached.", html=None) -> EmailMessage:
    m = EmailMessage()
    m["From"], m["To"], m["Subject"] = "sender@example.test", "acct@example.test", subject
    m["Date"], m["Message-ID"] = "Thu, 01 Oct 2026 08:00:00 -0400", msg_id
    m.set_content(text)
    if html:
        m.add_alternative(html, subtype="html")
    return m


def _statement_db(path: Path, month="2026-09", n=130) -> tuple[list, tuple]:
    """A finance DB holding one Goodwill Books statement: n lines across several PDF pages, long descriptions,
    refunds and fees negative, printed totals = the lines."""
    con = duckdb.connect(str(path))
    con.execute(FINANCE_DDL)
    lines, d0 = [], date(2026, 9, 1)
    for i in range(1, n + 1):
        typ = ["SALE", "SHIPPING", "FEE", "REFUND"][i % 4]
        amt = {"SALE": 10 + i / 100, "SHIPPING": 3.99, "FEE": -(1 + i / 1000), "REFUND": -(2 + i / 100)}[typ]
        desc = {"SALE": "Item sale The Complete Works of Shakespeare Variations"[: 30 + i % 18].strip(),
                "SHIPPING": "Shipping & handling", "FEE": "Platform fee", "REFUND": f"Refund RF-{i:06d}"}[typ]
        sku = f"GWM-{i % 24 + 1:02d}-{100000 + i}" if typ in ("SALE", "REFUND") else None
        lines.append((month, i, typ, f"GWB{120000 + i // 3}", date(2026, 9, 1 + i % 28), sku, desc, round(amt, 2)))
    con.executemany("INSERT INTO gwb_statement VALUES (?,?,?,?,?,?,?,?)", lines)
    gross = round(sum(l[7] for l in lines if l[2] in ("SALE", "SHIPPING")), 2)
    fees = round(-sum(l[7] for l in lines if l[2] == "FEE"), 2)
    refunds = round(-sum(l[7] for l in lines if l[2] == "REFUND"), 2)
    net = round(sum(l[7] for l in lines), 2)
    hdr = (month, date(2026, 10, 1), gross, fees, refunds, net, date(2026, 10, 2))
    con.execute("INSERT INTO gwb_statement_header VALUES (?,?,?,?,?,?,?)", hdr)
    con.close()
    return lines, hdr


# ------------------------------------------------------------------------------------------------- converters
@pytest.mark.parametrize("data,name,kind", [
    (b"%PDF-1.4 ...", "x.bin", "pdf"),
    (b"\x89PNG\r\n\x1a\n...", "shot", "png"),
    (b"\xff\xd8\xff\xe0...", "photo.jpg", "jpeg"),
    (b"From: a@b.c\nSubject: hi\nDate: x\n\nbody", "mail", "eml"),
    (b"OFXHEADER:100\nDATA:OFXSGML\n<OFX>", "bank.qfx", "ofx"),
    (b"<html><body><table><tr><td>a</td></tr></table>", "page.htm", "html"),
    (b'<?xml version="1.0"?><rows><r/></rows>', "x.xml", "xml"),
    (b'[{"a": 1}]', "x.json", "json"),
    (b"a\tb\n1\t2\n", "x.txt", "text"),
    (b"a,b\n1,2\n", "x.csv", "csv"),
])
def test_sniff_reads_the_bytes_not_just_the_extension(data, name, kind):
    assert sniff(data, name) == kind


def test_excel_renamed_csv_is_still_excel(tmp_path):
    p = tmp_path / "x.xlsx"
    pd.DataFrame({"a": [1]}).to_excel(p, index=False)
    assert sniff(p.read_bytes(), "report.csv") == "xlsx"


def test_delimited_text_skips_title_lines_and_keeps_them_as_notes():
    text = ('"Includes Amazon Marketplace and FBA transactions"\n"All amounts in USD"\n'
            "date/time\ttype\torder id\ttotal\n"
            "Sep 1, 2026 1:02:03 AM PDT\tOrder\t113-1\t12.50\n"
            "Sep 2, 2026 4:05:06 AM PDT\tRefund\t113-2\t-3.00\n")
    (header, rows), preamble = tabular.read_delimited(text)
    assert header == ["date/time", "type", "order id", "total"]
    assert rows[1] == ["Sep 2, 2026 4:05:06 AM PDT", "Refund", "113-2", "-3.00"]
    assert preamble == ["Includes Amazon Marketplace and FBA transactions", "All amounts in USD"]


def test_html_layout_tables_are_dropped_data_tables_kept():
    html = """<table><tr><td><img src="logo.png"></td></tr><tr><td>
      <table><tr><th>Invoice Number</th><th>Net Charge</th></tr><tr><td>7-1</td><td>1,234.50</td></tr>
      <tr><td>7-2</td><td>(5.00)</td></tr></table></td></tr></table>"""
    tables = tabular.read_html(html)
    assert tables == [(["Invoice Number", "Net Charge"], [["7-1", "1,234.50"], ["7-2", "(5.00)"]])]


def test_json_and_xml_records_become_rows():
    h, rows = tabular.read_json('{"meta": {"n": 2}, "data": {"orders": [{"id": "A", "amt": {"v": 1.5}}, '
                                '{"id": "B", "amt": {"v": 2}, "note": null}]}}')
    assert h == ["id", "amt.v", "note"] and rows == [["A", "1.5", ""], ["B", "2", ""]]
    h, rows = tabular.read_xml('<r xmlns="urn:x"><head><t>x</t></head><line id="1"><amt>3.10</amt></line>'
                               '<line id="2"><amt>4</amt></line></r>')
    assert h == ["id", "amt"] and rows == [["1", "3.10"], ["2", "4"]]


def test_ofx_becomes_the_bank_layout():
    ofx = ("OFXHEADER:100\n<OFX><BANKACCTFROM><ACCTID>4410000101</BANKACCTFROM><BANKTRANLIST>"
           "<STMTTRN><TRNTYPE>DEP<DTPOSTED>20260929120000[-5:EST]<TRNAMT>12.34<FITID>1<NAME>BNKDEPOSIT FEDEX REFUND"
           "<REFNUM>FXR-1</STMTTRN></BANKTRANLIST></OFX>")
    conv = convert(data=ofx.encode(), name="bank.ofx")
    o = conv.outputs[0]
    assert o.layout == "bank_0101" and o.rows == [["2026-09-29", "BNKDEPOSIT FEDEX REFUND", "", "12.34", "DEP",
                                                   "FXR-1", "4410000101"]]


def test_email_attachment_passes_through_with_its_name_and_the_envelope_is_kept():
    m = _email("Your Upright report is ready", "<u-1@example.test>")
    m.add_attachment(UPRIGHT_CSV.read_bytes(), maintype="text", subtype="csv", filename=UPRIGHT_CSV.name)
    m.add_attachment(b"\x89PNG" + b"0" * 100, maintype="image", subtype="png", filename="logo.png")   # tiny = logo
    conv = convert(data=bytes(m), name="mail.eml")
    assert [o.name for o in conv.outputs] == [UPRIGHT_CSV.name]
    assert conv.outputs[0].method == "passthrough" and conv.outputs[0].raw == UPRIGHT_CSV.read_bytes()
    assert conv.envelope["subject"] == "Your Upright report is ready"
    assert conv.envelope["message_id"] == "<u-1@example.test>"


def test_forwarded_email_attachments_are_read_once():
    inner = _email("FW: statement", "<inner@example.test>")
    inner.add_attachment(b"a,b\n1,2\n", maintype="text", subtype="csv", filename="inner.csv")
    outer = _email("Fwd", "<outer@example.test>")
    outer.add_attachment(inner)
    conv = convert(data=bytes(outer), name="fwd.eml")
    assert [o.name for o in conv.outputs] == ["inner.csv"]
    assert conv.envelope["message_id"] == "<outer@example.test>"


def test_zip_members_are_converted_and_mac_junk_skipped():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("r/report.txt", "A|B\n1|2\n3|4\n")
        z.writestr("__MACOSX/r/._report.txt", b"\x00")
    conv = convert(data=buf.getvalue(), name="r.zip")
    assert [(o.name, o.header, o.rows) for o in conv.outputs] == [("report.xlsx", ["A", "B"], [["1", "2"], ["3", "4"]])]


def test_unreadable_parts_are_reported_not_dropped():
    m = _email("No data", "<n@example.test>", text="Hi team, numbers coming tomorrow.")
    conv = convert(data=bytes(m), name="note.eml")
    assert conv.outputs == []
    assert "AI is not configured" in conv.notes[0]


def test_converted_workbook_is_text_and_records_where_it_came_from(tmp_path):
    conv = convert(data=b"SKU\tAmount\n00123\t5.00\n", name="x.txt")
    [path] = conv.write(tmp_path)
    df = pd.read_excel(path, dtype=str)
    assert df.to_dict("records") == [{"SKU": "00123", "Amount": "5.00"}]      # leading zeros survive
    about = pd.read_excel(path, sheet_name="About this file", header=None)
    facts = dict(zip(about[0], about[1]))
    assert facts["Source SHA-256"] == conv.sha256 and facts["Read by"].startswith("Deterministic")


# ------------------------------------------------------------------------------------------------------- PDF
def test_statement_pdf_is_read_exactly(tmp_path):
    lines, hdr = _statement_db(tmp_path / "f.duckdb")
    con = duckdb.connect(str(tmp_path / "f.duckdb"), read_only=True)
    pdf = gen.gwb_statement_pdf(con, "2026-09")
    con.close()
    conv = convert(data=pdf, name="GWB_Statement_2026-09.pdf")
    [o] = conv.outputs
    assert o.layout == "gwb_statement" and o.method == "parsed"
    assert "pages 1-" in o.origin                                      # spans several pages
    got = [(int(r[0]), r[2], r[3], r[4] or None, r[5], finance.money(r[6])) for r in o.rows]
    want = [(l[1], l[2], l[3], l[5], l[6], finance.money(f"{l[7]:.2f}")) for l in lines]
    assert got == want
    assert o.stated["Net payment"] == f"${hdr[5]:,.2f}" and o.stated["Statement period"] == "September 2026"


# ---------------------------------------------------------------------------------------------------- intake
def test_statement_email_loads_into_the_close_tables(env, tmp_path):
    _, hdr = _statement_db(tmp_path / "src.duckdb")
    src = duckdb.connect(str(tmp_path / "src.duckdb"), read_only=True)
    m = _email("Your Goodwill Books payment statement", "<gwb@example.test>")
    m.add_attachment(gen.gwb_statement_pdf(src, "2026-09"), maintype="application", subtype="pdf",
                     filename="GWB_Statement_2026-09.pdf")
    src.close()
    p = tmp_path / "statement.eml"
    p.write_bytes(bytes(m))
    r = intake_mod.intake(env.con, p)
    assert r["status"] == "loaded", r
    [o] = r["outputs"]
    assert (o["target"], o["layout"], o["loaded_rows"], o["months"]) == ("finance", "gwb_statement", 130, ["2026-09"])
    f = duckdb.connect(str(env.fin), read_only=True)
    assert f.execute("SELECT count(*), sum(amount) FROM gwb_statement WHERE statement_month = '2026-09'").fetchone() \
        == (130, pytest.approx(__import__("decimal").Decimal(str(hdr[5]))))
    assert [float(x) for x in f.execute("SELECT gross, fees, refunds, net FROM gwb_statement_header").fetchone()] \
        == [hdr[2], hdr[3], hdr[4], hdr[5]]
    f.close()
    row = env.con.execute("SELECT email_subject, message_id, status FROM intake_files").fetchone()
    assert row == ("Your Goodwill Books payment statement", "<gwb@example.test>", "loaded")


def test_rows_that_dont_add_up_to_the_printed_total_are_held(env, tmp_path):
    _statement_db(tmp_path / "src.duckdb")
    src = duckdb.connect(str(tmp_path / "src.duckdb"), read_only=True)
    pdf = gen.gwb_statement_pdf(src, "2026-09", drop_line=40)              # one line lost, totals unchanged
    src.close()
    p = tmp_path / "statement.pdf"
    p.write_bytes(pdf)
    r = intake_mod.intake(env.con, p)
    [o] = r["outputs"]
    assert r["status"] == "needs_review" and o["loaded_rows"] == 0
    assert any(e["rule"] == "control_total" for e in o["exceptions"])
    assert not env.fin.exists() or duckdb.connect(str(env.fin), read_only=True).execute(
        "SELECT count(*) FROM gwb_statement").fetchone()[0] == 0
    # a person checks, then loads it anyway: the difference stays on the exceptions list
    res = intake_mod.confirm(env.con, o["output_id"], confirmed_by="Amanda", override_control=True)
    assert res["status"] == "partial" and res["loaded_rows"] == 129
    assert env.con.execute("SELECT count(*) FROM exceptions WHERE rule = 'control_total' AND status = 'open'"
                           ).fetchone()[0] == 1
    assert env.con.execute("SELECT confirmed_by FROM intake_outputs").fetchone()[0] == "Amanda"


def test_a_corrected_spreadsheet_can_replace_the_converted_one(env, tmp_path):
    lines, _ = _statement_db(tmp_path / "src.duckdb")
    src = duckdb.connect(str(tmp_path / "src.duckdb"), read_only=True)
    p = tmp_path / "statement.pdf"
    p.write_bytes(gen.gwb_statement_pdf(src, "2026-09", drop_line=40))
    src.close()
    o = intake_mod.intake(env.con, p)["outputs"][0]
    path = intake_mod.output_path(env.con, o["output_id"])
    df = pd.read_excel(path, dtype=str).fillna("")
    l = lines[39]
    fixed = pd.concat([df.iloc[:39], pd.DataFrame([[str(l[1]), f"{l[4]:%m/%d/%Y}", l[2], l[3], l[5] or "", l[6],
                                                    f"{l[7]:.2f}"]], columns=df.columns), df.iloc[39:]])
    corrected = tmp_path / "fixed.xlsx"
    with pd.ExcelWriter(corrected) as w:
        fixed.to_excel(w, index=False)
        pd.read_excel(path, sheet_name="Stated values", dtype=str).to_excel(w, sheet_name="Stated values", index=False)
    res = intake_mod.confirm(env.con, o["output_id"], corrected=corrected, confirmed_by="Amanda")
    assert res["status"] == "loaded" and res["loaded_rows"] == 130 and "(corrected)" in res["name"]


def test_same_file_or_same_email_twice_is_a_duplicate(env, tmp_path):
    m = _email("Your Upright report is ready", "<dup@example.test>")
    m.add_attachment(UPRIGHT_CSV.read_bytes(), maintype="text", subtype="csv", filename=UPRIGHT_CSV.name)
    a = tmp_path / "a.eml"
    a.write_bytes(bytes(m))
    first = intake_mod.intake(env.con, a)
    assert first["status"] == "loaded" and first["outputs"][0]["target"] == "warehouse"
    assert intake_mod.intake(env.con, a)["status"] == "duplicate_file"
    m.replace_header("Subject", "Fwd: Your Upright report is ready")          # different bytes, same Message-ID
    b = tmp_path / "b.eml"
    b.write_bytes(bytes(m))
    again = intake_mod.intake(env.con, b)
    assert again["status"] == "duplicate_file" and "Message-ID" in again["message"]


def test_unknown_table_is_kept_and_flagged(env, tmp_path):
    p = tmp_path / "misc.json"
    p.write_text(json.dumps([{"color": "red", "size": 3}]))
    r = intake_mod.intake(env.con, p)
    [o] = r["outputs"]
    assert o["status"] == "needs_mapping" and intake_mod.output_path(env.con, o["output_id"]).exists()
    assert env.con.execute("SELECT count(*) FROM exceptions WHERE rule = 'unrecognized_report'").fetchone()[0] == 1


def test_bank_csv_with_debit_and_credit_columns(env, tmp_path):
    p = tmp_path / "activity.csv"
    p.write_text("Posting Date,Payee,Debit,Credit,Ref\n09/02/2026,OSM WORLDWIDE ACH DEBIT,812.40,,OSM-1\n"
                 "09/03/2026,EBAY PAYOUT,,1792.12,PO-1\n")
    r = intake_mod.intake(env.con, p)
    assert r["outputs"][0]["layout"] == "bank_0101" and r["status"] == "loaded"
    rows = duckdb.connect(str(env.fin), read_only=True).execute(
        "SELECT post_date, amount, account_no FROM bank_0101 ORDER BY post_date").fetchall()
    assert [(d.isoformat(), float(a), acct) for d, a, acct in rows] == [("2026-09-02", -812.4, "0101"),
                                                                       ("2026-09-03", 1792.12, "0101")]


# --------------------------------------------------------------------------------------------------- Claude
def _ai_response(payload: dict):
    return SimpleNamespace(content=[SimpleNamespace(type="text", text=json.dumps(payload))], stop_reason="end_turn",
                           usage=SimpleNamespace(input_tokens=1, output_tokens=1, cache_read_input_tokens=0))


class FakeClient:
    def __init__(self, payload):
        self.payload, self.calls = payload, []
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self._create))

    def _create(self, **kw):
        self.calls.append(kw)
        return _ai_response(self.payload)


def test_photo_of_a_report_is_read_by_claude_and_held_until_confirmed(env, tmp_path):
    fake = FakeClient({"tables": [{"layout": "jewelry_report", "title": "Jewelry Report", "columns":
                                   ["Channel", "Order", "Sale Date", "SKU", "Title", "Sale Price", "Supplier"],
                                   "rows": [["eBay", "12-1", "09/04/2026", "UP-09-1", "Ring", "45.00", "Store09"]]}],
                       "stated": [], "notes": ""})
    ai.set_client(fake)
    p = tmp_path / "photo.png"
    p.write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 30_000)
    r = intake_mod.intake(env.con, p)
    [o] = r["outputs"]
    assert (o["method"], o["status"], o["loaded_rows"]) == ("ai", "needs_review", 0)
    assert fake.calls[0]["messages"][0]["content"][0]["type"] == "image"
    assert not env.fin.exists()                                              # nothing written before a person looks
    res = intake_mod.confirm(env.con, o["output_id"], confirmed_by="Stardess")
    assert (res["status"], res["loaded_rows"], res["layout"]) == ("loaded", 1, "jewelry_report")


# ------------------------------------------------------------------------------------------------------- API
def test_upload_endpoint_takes_an_email(env, tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from goodwill_pulse import api
    monkeypatch.setattr(api, "_con", env.con)
    monkeypatch.setattr(api, "INBOX_DIR", tmp_path / "inbox")
    monkeypatch.setattr("goodwill_pulse.routes.intake.INBOX_DIR", tmp_path / "inbox")
    c = TestClient(api.app)
    m = _email("Your Upright report is ready", "<api@example.test>")
    m.add_attachment(UPRIGHT_CSV.read_bytes(), maintype="text", subtype="csv", filename=UPRIGHT_CSV.name)
    r = c.post("/api/upload", files={"files": ("upright.eml", bytes(m), "message/rfc822")})
    assert r.status_code == 200, r.text
    [row] = r.json()
    assert row["status"] == "loaded" and row["report_type"] == "upright_paid_orders" and row["loaded"] > 0
    listed = c.get("/api/intake").json()
    assert listed[0]["envelope"]["subject"] == "Your Upright report is ready"
    f = c.get(listed[0]["outputs"][0]["excel_url"])
    assert f.status_code == 200 and f.content == UPRIGHT_CSV.read_bytes()


# ------------------------------------------------------------------------------------------------ round trip
@pytest.mark.skipif(not FINANCE_DB_PATH.exists(), reason="needs data/sources/finance.duckdb")
def test_round_trip_reproduces_the_finance_inputs_and_the_close(env, tmp_path):
    """finance.duckdb -> email / PDF / OFX / HTML / zip -> upload ingest -> the same rows, and the same close."""
    from goodwill_pulse.close.rules import DEFAULT_HARMONIZED, DEFAULT_RULES, run_close
    shutil.copy2(FINANCE_DB_PATH, env.fin)
    files = gen.build(tmp_path / "demo", FINANCE_DB_PATH, "2026-09")
    statuses = {f.name: intake_mod.intake(env.con, f)["status"] for f in files}
    assert statuses.pop("03_goodwill_books_statement_2026-08_misread.pdf") == "needs_review"
    assert set(statuses.values()) == {"loaded"}, statuses
    a, b = duckdb.connect(str(FINANCE_DB_PATH), read_only=True), duckdb.connect(str(env.fin), read_only=True)
    for q in ("SELECT statement_month, line_no, line_type, order_no, line_date, sku, description, amount FROM gwb_statement",
              "SELECT * FROM gwb_statement_header",
              "SELECT post_date, description, amount, type, reference, account_no FROM bank_0101",
              "SELECT invoice_no, invoice_date, line_type, tracking, ship_date, amount, orig_invoice_no, refund_ref "
              "FROM fedex_invoices",
              "SELECT report_month, line_no, channel, order_ref, sale_date, sku, title, sale_price, "
              "coalesce(supplier, '') FROM jewelry_report"):
        assert sorted(a.execute(q).fetchall(), key=str) == sorted(b.execute(q).fetchall(), key=str), q
    a.close()
    b.close()
    if not DEFAULT_HARMONIZED.exists():
        return
    x = run_close(date(2026, 9, 1), DEFAULT_HARMONIZED, FINANCE_DB_PATH, DEFAULT_RULES)
    y = run_close(date(2026, 9, 1), DEFAULT_HARMONIZED, env.fin, DEFAULT_RULES)
    sig = lambda r: (r.allocations.groupby(["doc_no", "account_no"])["amount"].sum().round(2).to_dict(),
                     r.revenue.groupby(["channel", "account_no"])["amount"].sum().round(2).to_dict(),
                     sorted((e.get("severity"), e.get("message")) for e in r.exceptions))
    assert sig(x) == sig(y)
