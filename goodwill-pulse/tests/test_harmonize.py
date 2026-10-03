"""Harmonizer tests: tiny hand-built source DBs (sql/sources/*.sql DDL + tests/fixtures/harmonize/*.sql rows),
each row exercising one native quirk, then exact assertions on the canonical output."""
from datetime import date, datetime, timezone
from decimal import Decimal as D
from pathlib import Path

import duckdb
import pytest
import yaml

from goodwill_pulse import harmonize

ROOT = Path(__file__).resolve().parent.parent
FIX = Path(__file__).resolve().parent / "fixtures" / "harmonize"


def make_sources(dirpath: Path) -> Path:
    dirpath.mkdir(parents=True, exist_ok=True)
    for name in harmonize.SOURCES:
        con = duckdb.connect(str(dirpath / f"{name}.duckdb"))
        con.execute((ROOT / "sql" / "sources" / f"{name}.sql").read_text())
        con.execute((FIX / f"{name}.sql").read_text())
        con.close()
    return dirpath


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("harm")
    out = tmp / "harmonized.duckdb"
    res = harmonize.build(make_sources(tmp / "sources"), out)
    con = duckdb.connect(str(out), read_only=True)
    con.execute("SET TimeZone='UTC'")
    yield res, con
    con.close()


def q(con, sql, *params):
    return con.execute(sql, list(params)).fetchall()


def order(con, key):
    cur = con.execute("SELECT * FROM fct_orders WHERE order_key = ?", [key])
    row = cur.fetchone()
    assert row is not None, f"missing order {key}"
    return dict(zip([d[0] for d in cur.description], row))


def utc(*a):
    return datetime(*a, tzinfo=timezone.utc)


def test_build_result_and_atomic_output(built, tmp_path):
    res, con = built
    assert Path(res["out_path"]).exists() and not Path(res["out_path"] + ".tmp").exists()
    assert res["output_row_counts"]["fct_orders"] == 13
    assert res["source_row_counts"]["amazon.order_items"] == 7
    assert res["source_row_counts"]["excluded.test_orders"] == 5
    assert res["source_row_counts"]["excluded.canceled_orders"] == 1
    assert q(con, "SELECT count(*) FROM harmonize_runs")[0][0] == 1
    # no temp objects / macros leak into the output file
    tables = {r[0] for r in q(con, "SELECT table_name FROM duckdb_tables() WHERE database_name='harmonized'")}
    assert not any(t.startswith("stg_") for t in tables)


def test_order_counts_per_channel_and_exclusions(built):
    _, con = built
    assert dict(q(con, "SELECT channel, count(*) FROM fct_orders GROUP BY 1")) == {
        "shopgoodwill": 5, "ebay": 2, "amazon": 2, "goodwillfinds": 2, "goodwillbooks": 2}
    keys = {r[0] for r in q(con, "SELECT order_key FROM fct_orders")}
    # TEST buyers on every channel, the canceled Amazon order and the duplicated GF order id are gone
    for k in ("shopgoodwill:9004", "ebay:12-00003-00003", "amazon:113-0000009-0000009",
              "amazon:113-0000008-0000008", "goodwillfinds:3003", "goodwillfinds:3009", "goodwillbooks:GWB000002"):
        assert k not in keys
    # nothing hangs off an excluded order
    assert q(con, """SELECT count(*) FROM (SELECT order_key FROM fct_order_lines UNION ALL SELECT order_key FROM fct_refunds
                     UNION ALL SELECT order_key FROM fct_fees WHERE order_key IS NOT NULL)
                     WHERE order_key NOT IN (SELECT order_key FROM fct_orders)""")[0][0] == 0


def test_shopgoodwill_pacific_first_line_shipping_and_late_night(built):
    _, con = built
    o = order(con, "shopgoodwill:9001")
    # 8:30 PM PDT Sep 30 -> 03:30 UTC Oct 1 -> 11:30 PM ET Sep 30
    assert o["paid_at_utc"] == utc(2026, 10, 1, 3, 30)
    assert o["business_date"] == date(2026, 9, 30)
    assert (o["subtotal"], o["shipping_charged"], o["handling"], o["tax"]) == (D("150.00"), D("12.50"), D("6.00"), D("9.00"))
    assert o["total"] == D("177.50")
    assert o["item_count"] == 2 and o["line_of_business"] == "general_merch"
    assert o["marketplace_fees"] == D("22.50")         # 12 + 6 + 4.50; duplicate fee row ignored
    assert o["refund_amount"] == D("50.00")
    assert o["buyer_key"] == duckdb.sql("SELECT md5('shopgoodwill|B100')").fetchone()[0]


def test_dst_fall_back_takes_first_occurrence_and_gap_shifts_forward(built):
    _, con = built
    # Pacific: 2025-11-02 01:07:51 happens twice -> first (PDT, UTC-7)
    assert order(con, "shopgoodwill:9005")["paid_at_utc"] == utc(2025, 11, 2, 8, 7, 51)
    # Pacific: 2026-03-08 02:30 does not exist -> shifted forward to 03:30 PDT
    assert order(con, "shopgoodwill:9006")["paid_at_utc"] == utc(2026, 3, 8, 10, 30)
    # Eastern (Goodwillbooks text): 11/02/2025 01:30 twice -> first (EDT, UTC-4)
    g = order(con, "goodwillbooks:GWB000003")
    assert g["paid_at_utc"] == utc(2025, 11, 2, 5, 30) and g["business_date"] == date(2025, 11, 2)
    # macro directly, both zones, both sides of each transition
    con2 = duckdb.connect()
    con2.execute("SET TimeZone='UTC'")
    for f in ("05_macros.sql",):
        harmonize._run_file(con2, harmonize.HARMONIZE_DIR / f)
    got = con2.execute("""SELECT pacific_naive(TIMESTAMP '2025-11-02 00:59:59'), pacific_naive(TIMESTAMP '2025-11-02 01:59:59'),
                                  pacific_naive(TIMESTAMP '2025-11-02 02:00:00'), eastern_naive(TIMESTAMP '2026-03-08 02:15:00'),
                                  eastern_naive(TIMESTAMP '2026-03-08 01:59:00')""").fetchone()
    assert got == (utc(2025, 11, 2, 7, 59, 59), utc(2025, 11, 2, 8, 59, 59), utc(2025, 11, 2, 10),
                   utc(2026, 3, 8, 7, 15), utc(2026, 3, 8, 6, 59))
    con2.close()


def test_test_items_excluded_from_listings(built):
    _, con = built
    skus = {r[0] for r in q(con, "SELECT sku FROM fct_listings")}
    assert "UP-08-000030" not in skus and "GWM-04-100011" not in skus   # SGW auction + GWB inventory of TEST orders
    assert "UP-13-000020" in skus and "GWM-04-100005" in skus           # real items stay
    assert q(con, "SELECT count(*) FROM fct_listings WHERE channel='shopgoodwill'")[0][0] == 8   # 9 auctions - 1 test


def test_dst_spring_forward(built):
    _, con = built
    a, b = order(con, "shopgoodwill:9002"), order(con, "shopgoodwill:9003")
    assert a["paid_at_utc"] == utc(2026, 3, 8, 9, 30)   # 01:30 PST (UTC-8)
    assert b["paid_at_utc"] == utc(2026, 3, 8, 10, 30)  # 03:30 PDT (UTC-7): one real hour later
    assert a["business_date"] == b["business_date"] == date(2026, 3, 8)
    gf = order(con, "goodwillfinds:3002")               # -05:00 offset honoured
    assert gf["paid_at_utc"] == utc(2026, 3, 8, 6, 30) and gf["business_date"] == date(2026, 3, 8)


def test_ebay_text_money_books_vs_gm_and_midnight(built):
    _, con = built
    gm, bk = order(con, "ebay:12-00001-00001"), order(con, "ebay:12-00002-00002")
    assert (gm["subtotal"], gm["shipping_charged"], gm["tax"], gm["marketplace_fees"]) == (
        D("45.00"), D("8.95"), D("3.15"), D("6.88"))
    assert gm["line_of_business"] == "general_merch" and bk["line_of_business"] == "books"
    assert gm["item_count"] == 1                        # duplicate line row collapsed
    assert gm["refund_amount"] == D("10.00")
    assert bk["paid_at_utc"] == utc(2026, 10, 1, 3, 59, 59) and bk["business_date"] == date(2026, 9, 30)
    lines = q(con, "SELECT sku, store_id, category, line_of_business FROM fct_order_lines WHERE channel='ebay' ORDER BY sku")
    assert lines == [("GWM-09-100001", "Store09", "Books", "books"),
                     ("UP-07-000010", "Store07", "Electronics", "general_merch")]   # lowercase SKU normalized


def test_amazon_fees_dates_and_refund(built):
    _, con = built
    o = order(con, "amazon:113-0000001-0000001")
    assert o["paid_at_utc"] == utc(2026, 9, 30, 23, 41, 7) and o["business_date"] == date(2026, 9, 30)
    assert (o["subtotal"], o["shipping_charged"], o["tax"]) == (D("12.00"), D("11.97"), D("0.90"))
    assert o["item_count"] == 3                          # duplicate order_items row collapsed
    assert o["marketplace_fees"] == D("7.21")            # negative fees flipped; duplicate event ignored
    assert o["refund_amount"] == D("7.99")
    r = q(con, "SELECT refunded_at_utc, business_date, amount FROM fct_refunds WHERE refund_key='amazon:E4'")
    assert r == [(utc(2026, 10, 2, 16, 15), date(2026, 10, 2), D("7.99"))]   # 'Oct 2, 2026 9:15:00 AM PDT'
    # PST event parsed with -08:00: 'Jan 5, 2026 11:30:00 PM PST' = 07:30 UTC Jan 6
    assert q(con, "SELECT business_date FROM fct_fees WHERE fee_key='amazon:E6:referral'") == [(date(2026, 1, 6),)]
    fees = dict(q(con, "SELECT fee_type, sum(amount) FROM fct_fees WHERE channel='amazon' GROUP BY 1"))
    assert fees == {"referral": D("2.46"), "other": D("47.19")}   # 0.61+0.60+0.60-0.48+1.13 ; 1.80*4 + 39.99
    assert q(con, "SELECT order_key FROM fct_fees WHERE fee_key='amazon:E5:service'") == [(None,)]
    # lowercase SKU not in the item master: store from the SKU, books from the prefix
    assert q(con, "SELECT sku, store_id, category FROM fct_order_lines WHERE order_key='amazon:113-0000002-0000002'") == [
        ("GWM-12-100099", "Store12", "Books")]


def test_fee_allocation_sums_to_order_fees(built):
    _, con = built
    assert q(con, """SELECT count(*) FROM fct_orders o JOIN (SELECT order_key, sum(fee_alloc) s FROM fct_order_lines GROUP BY 1) l
                     USING (order_key) WHERE l.s <> o.marketplace_fees""")[0][0] == 0
    # three equal lines, $7.21: the leftover cent goes to line 1
    assert q(con, "SELECT line_no, fee_alloc FROM fct_order_lines WHERE order_key='amazon:113-0000001-0000001' ORDER BY 1") == [
        (1, D("2.41")), (2, D("2.40")), (3, D("2.40"))]
    assert q(con, "SELECT sku, fee_alloc FROM fct_order_lines WHERE order_key='shopgoodwill:9001' ORDER BY 1") == [
        ("UP-03-000001", D("15.00")), ("UP-03-000002", D("7.50"))]
    assert q(con, "SELECT sku, fee_alloc FROM fct_order_lines WHERE order_key='goodwillfinds:3001' ORDER BY 1") == [
        ("UP-13-000020", D("3.75")), ("UP-13-000021", D("5.25"))]


def test_goodwillfinds_vendor_typo_and_category_map(built):
    _, con = built
    o = order(con, "goodwillfinds:3001")
    assert o["paid_at_utc"] == utc(2026, 9, 30, 23, 41, 7) and o["business_date"] == date(2026, 9, 30)
    assert o["refund_amount"] == D("25.00") and o["marketplace_fees"] == D("9.00")
    assert q(con, "SELECT sku, store_id, category FROM fct_order_lines WHERE channel='goodwillfinds' ORDER BY sku") == [
        ("UP-13-000020", "Store13", "Home Decor"),   # 'Goodwil Michiana #13' typo still -> Store13
        ("UP-13-000021", "Store13", "Jewelry"),      # 'JEWELRY' mapped (not in item master)
        ("UP-13-000022", "Store13", "Clothing")]     # '# 13'
    assert q(con, "SELECT business_date FROM fct_refunds WHERE refund_key='goodwillfinds:6001'") == [(date(2026, 10, 1),)]


def test_goodwillbooks_cents_eastern_and_missing_store_code(built):
    _, con = built
    o = order(con, "goodwillbooks:GWB000001")
    assert o["paid_at_utc"] == utc(2026, 10, 1, 3, 30) and o["business_date"] == date(2026, 9, 30)
    assert (o["subtotal"], o["shipping_charged"], o["tax"], o["marketplace_fees"], o["refund_amount"], o["total"]) == (
        D("15.00"), D("3.99"), D("1.05"), D("2.25"), D("5.00"), D("20.04"))
    assert q(con, "SELECT sku, store_id, sale_price, fee_alloc FROM fct_order_lines WHERE order_key='goodwillbooks:GWB000001' ORDER BY 1") == [
        ("GWM-02-100004", "Store02", D("5.00"), D("0.75")),   # store_code NULL -> from SKU
        ("GWM-04-100005", "Store04", D("10.00"), D("1.50"))]


def test_refunds_cross_midnight(built):
    _, con = built
    assert q(con, "SELECT refunded_at_utc, business_date, amount FROM fct_refunds WHERE refund_key='shopgoodwill:9001:5002'") == [
        (utc(2026, 10, 2, 5, 0), date(2026, 10, 2), D("50.00"))]   # 10 PM PDT Oct 1 = 1 AM ET Oct 2
    assert q(con, "SELECT count(*), sum(amount) FROM fct_refunds") == [(5, D("97.99"))]


def test_no_duplicates_and_store_never_dropped(built):
    _, con = built
    assert q(con, "SELECT count(*) - count(DISTINCT (order_key, sku)) FROM fct_order_lines")[0][0] == 0
    assert q(con, "SELECT count(*) FROM fct_order_lines")[0][0] == 18   # SGW 6, eBay 2, Amazon 4, GF 3, GWB 3
    assert q(con, "SELECT count(*) FROM fct_order_lines WHERE store_id IS NULL OR category IS NULL")[0][0] == 0
    assert q(con, "SELECT count(*) FROM fct_fees WHERE channel='ebay'")[0][0] == 4   # T1, T2, T4, T7 (T5 dup, T6 test)


def test_ebay_shipping_label_is_a_fee_row_not_a_marketplace_fee(built):
    _, con = built
    assert q(con, "SELECT order_key, fee_type, amount, business_date FROM fct_fees WHERE fee_key='ebay:T7'") == [
        ("ebay:12-00001-00001", "shipping_label", D("7.45"), date(2026, 9, 30))]
    assert order(con, "ebay:12-00001-00001")["marketplace_fees"] == D("6.88")   # label NOT included
    assert q(con, "SELECT sum(fee_alloc) FROM fct_order_lines WHERE order_key='ebay:12-00001-00001'") == [(D("6.88"),)]


def test_listings_payouts_and_ops(built):
    _, con = built
    assert q(con, "SELECT relist_of, status, sku, price FROM fct_listings WHERE listing_key='ebay:7001'") == [
        ("ebay:7000", "sold", "UP-07-000010", D("45.00"))]
    assert q(con, "SELECT relist_of, status, ended_at_utc FROM fct_listings WHERE listing_key='shopgoodwill:5007'") == [
        ("shopgoodwill:5006", "active", None)]
    assert q(con, "SELECT listed_at_utc, ended_at_utc FROM fct_listings WHERE listing_key='amazon:GWM-11-100002'") == [
        (utc(2026, 9, 1, 17), utc(2026, 9, 30, 23, 41, 7))]
    assert q(con, "SELECT listed_at_utc FROM fct_listings WHERE listing_key='amazon:GWM-11-100010'") == [(utc(2026, 1, 15, 17),)]
    assert q(con, "SELECT count(*) FROM fct_payouts")[0][0] == 9    # amazon 3, ebay 2, sgw 1, gf 2, gwb 1
    assert q(con, "SELECT paid_on, period_start, period_end, net FROM fct_payouts WHERE payout_key='goodwillbooks:2026-09'") == [
        (date(2026, 10, 15), date(2026, 9, 1), date(2026, 9, 30), D("12.75"))]
    assert q(con, "SELECT period_start, period_end FROM fct_payouts WHERE payout_key='goodwillfinds:7002'") == [
        (date(2026, 10, 5), date(2026, 10, 11))]   # paid Wed 10/14 for the Mon-Sun week ending 3 days earlier
    assert q(con, "SELECT gross, fees, refunds, net FROM fct_payouts WHERE payout_key='ebay:PAY1'") == [
        (D("81.60"), D("18.68"), D("0.00"), D("57.47"))]   # incl. TEST order's sale + label; net is the bank amount
    assert q(con, "SELECT first_sold_at_utc FROM fct_items WHERE item_id='UP-03-000001'") == [(utc(2026, 10, 1, 3, 30),)]
    assert q(con, "SELECT first_listed_at_utc FROM fct_items WHERE item_id='GWM-04-100005'") == [(utc(2026, 9, 1, 4),)]
    assert q(con, "SELECT employee_id, hours, cost, posted FROM fct_labor ORDER BY 1") == [
        ("E001", D("8.00"), D("160.00"), 40), ("E002", D("7.50"), D("138.75"), None), ("E003", None, None, 28)]
    assert q(con, "SELECT channel, revenue_budget FROM fct_budget ORDER BY 1") == [
        ("amazon", D("20000.00")), ("shopgoodwill", D("100000.00"))]
    assert q(con, "SELECT count(*) FROM dim_store")[0][0] == 24


def test_dim_channel_matches_channels_yaml(built):
    _, con = built
    cfg = yaml.safe_load((ROOT / "config" / "channels.yaml").read_text())
    expected = {ch: row["label"] for row in cfg["pulse_rows"] for ch in row["channels"] if ch != "other"}
    assert dict(q(con, "SELECT channel, pulse_row FROM dim_channel")) == expected


def test_sept_revenue_by_channel(built):
    _, con = built
    rev = dict(q(con, """SELECT channel, sum(subtotal) FROM fct_orders
                         WHERE business_date BETWEEN '2026-09-01' AND '2026-09-30' GROUP BY 1"""))
    assert rev == {"shopgoodwill": D("150.00"), "ebay": D("57.50"), "amazon": D("12.00"),
                   "goodwillfinds": D("60.00"), "goodwillbooks": D("15.00")}


def test_clear_error_names_file_and_statement(tmp_path, monkeypatch):
    src = make_sources(tmp_path / "sources")
    bad = tmp_path / "sqlh"
    bad.mkdir()
    (bad / "10_ok.sql").write_text("CREATE TEMP TABLE x AS SELECT 1 AS a;\n")
    (bad / "20_bad.sql").write_text("SELECT 1;\n\n-- second\nSELECT nope FROM missing_table;\n")
    monkeypatch.setattr(harmonize, "HARMONIZE_DIR", bad)
    monkeypatch.setattr(harmonize, "sql_files", lambda d=bad: sorted(bad.glob("*.sql")))
    out = tmp_path / "h.duckdb"
    with pytest.raises(harmonize.HarmonizeError, match=r"20_bad\.sql: statement 2 \(line 4\)"):
        harmonize.build(src, out)
    assert not out.exists() and not (tmp_path / "h.duckdb.tmp").exists()


def test_missing_source_is_clear(tmp_path):
    with pytest.raises(FileNotFoundError, match="amazon.duckdb"):
        harmonize.build(tmp_path, tmp_path / "h.duckdb")
