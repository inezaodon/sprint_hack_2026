"""The rules that make the numbers trustworthy."""
from datetime import date

import pytest

from goodwill_pulse import db
from goodwill_pulse.gen.writers import CASHMONKEY_HEADER, UPRIGHT_HEADER
from goodwill_pulse.ingest.pipeline import confirm_aliases, process_file
from goodwill_pulse.pulse import build_pulse


@pytest.fixture
def con(tmp_path, monkeypatch):
    monkeypatch.setattr("goodwill_pulse.ingest.pipeline.ARCHIVE_DIR", tmp_path / "archive")
    c = db.connect(tmp_path / "w.duckdb")
    yield c
    c.close()


def upright_file(tmp_path, rows, name="paid_orders_10-03-2026_10-03-2026.csv", header=UPRIGHT_HEADER):
    """rows: (order id, channel, channel order id, buyer, subtotal, paid at Pacific)."""
    lines = [",".join(header)]
    for uid, ch, cid, buyer, sub, paid in rows:
        lines.append(f"{uid},{ch},{cid},,{buyer},1,,PayPal,{sub + 13},{sub},10,,3,0,0,USD,,0,{paid}")
    total = sum(r[4] for r in rows)
    lines.append(",,,,,,,,," + f"{total},{10 * len(rows)}" + ",,,,,,,,")
    p = tmp_path / name
    p.write_text("\n".join(lines) + "\n")
    return p


def cashmonkey_file(tmp_path, units, name="orders2023-20261004-020000-12345.csv"):
    """units: (order id, channel, price, paid at UTC). One row per unit, like the real report."""
    lines = [",".join(CASHMONKEY_HEADER)]
    for oid, ch, price, paid in units:
        lines.append(f"{paid},{oid},276 - Goodwill Michiana,{ch},SKU1,0123,Title,Used - Good,Store03,1,{price},3.99,1.5,3.5,{price}")
    p = tmp_path / name
    p.write_text("\n".join(lines) + "\n")
    return p


def test_upright_totals_row_is_not_a_customer(con, tmp_path):
    f = upright_file(tmp_path, [("1", "Shopgoodwill", "100", "a", 50.0, "10/03/2026 10:00:00"),
                                ("2", "Shopgoodwill", "101", "b", 25.0, "10/03/2026 11:00:00")])
    res = process_file(con, f)
    assert res.status == "loaded" and res.row_count == 2 and res.loaded_rows == 2
    p = build_pulse(con, date(2026, 10, 3))
    sg = next(r for r in p["rows"] if r["label"] == "ShopGoodwill")
    assert sg["customers"] == 2 and sg["revenue"] == 75.0


def test_cashmonkey_units_group_into_orders(con, tmp_path):
    f = cashmonkey_file(tmp_path, [("111-1", "Amazon-MF", 10.0, "2026-10-03 15:00:00"),
                                   ("111-1", "Amazon-MF", 12.0, "2026-10-03 15:00:00"),
                                   ("111-2", "Amazon-MF", 8.0, "2026-10-03 16:00:00")])
    process_file(con, f)
    amazon = next(r for r in build_pulse(con, date(2026, 10, 3))["rows"] if r["label"] == "Amazon")
    assert amazon["customers"] == 2           # 3 rows, 2 orders
    assert amazon["revenue"] == 30.0


def test_business_day_is_eastern(con, tmp_path):
    # 10:30 PM Pacific on Oct 3 is 1:30 AM Eastern on Oct 4
    f = upright_file(tmp_path, [("1", "Shopgoodwill", "100", "a", 40.0, "10/03/2026 22:30:00")])
    process_file(con, f)
    assert build_pulse(con, date(2026, 10, 3))["total"]["customers"] == 0
    assert build_pulse(con, date(2026, 10, 4))["total"]["customers"] == 1


def test_duplicate_rows_flagged_and_counted_once(con, tmp_path):
    row = ("1", "Shopgoodwill", "100", "a", 50.0, "10/03/2026 10:00:00")
    res = process_file(con, upright_file(tmp_path, [row, row]))
    assert res.loaded_rows == 1
    assert [e["rule"] for e in res.exceptions].count("duplicate_order") == 1


def test_same_order_in_two_reports_counted_once(con, tmp_path):
    row = ("1", "Shopgoodwill", "100", "a", 50.0, "10/03/2026 10:00:00")
    process_file(con, upright_file(tmp_path, [row]))
    res = process_file(con, upright_file(tmp_path, [row, ("2", "eBay", "9-9", "b", 5.0, "10/03/2026 12:00:00")],
                                         name="paid_orders_10-03-2026_10-03-2026 (2).csv"))
    assert res.loaded_rows == 1
    assert build_pulse(con, date(2026, 10, 3))["total"]["customers"] == 2


def test_renamed_column_waits_for_confirmation(con, tmp_path):
    header = ["Sub Total" if h == "Subtotal" else h for h in UPRIGHT_HEADER]
    f = upright_file(tmp_path, [("1", "Shopgoodwill", "100", "a", 50.0, "10/03/2026 10:00:00")], header=header)
    res = process_file(con, f)
    assert res.status == "needs_mapping" and res.loaded_rows == 0
    assert res.exceptions[0]["rule"] == "renamed_column"
    res2 = confirm_aliases(con, res.file_id)
    assert res2.status == "loaded" and res2.loaded_rows == 1


def test_unknown_channel_is_an_exception_not_a_silent_drop(con, tmp_path):
    f = upright_file(tmp_path, [("1", "Mercari", "100", "a", 50.0, "10/03/2026 10:00:00"),
                                ("2", "Shopgoodwill", "101", "b", 20.0, "10/03/2026 10:00:00")])
    res = process_file(con, f)
    assert res.status == "partial" and res.loaded_rows == 1
    assert any(e["rule"] == "unknown_channel" for e in res.exceptions)


def test_missing_report_is_flagged(con, tmp_path):
    process_file(con, upright_file(tmp_path, [("1", "Shopgoodwill", "100", "a", 50.0, "10/03/2026 10:00:00")]))
    sources = {s["report_type"]: s["status"] for s in build_pulse(con, date(2026, 10, 3))["sources"]}
    assert sources["cashmonkey_orders"] == "missing"
