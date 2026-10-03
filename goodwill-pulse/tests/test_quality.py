"""Data-quality checks: a tiny, internally consistent world (harmonized + 6 source DBs) passes every check; each
seeded defect is caught by its own check (and only by the checks it necessarily also breaks, pinned below)."""
from __future__ import annotations

from datetime import date
from decimal import Decimal as D
from pathlib import Path

import duckdb
import pytest

from goodwill_pulse import quality
from goodwill_pulse.config import ROOT
from goodwill_pulse.db import SCHEMA as WAREHOUSE_SCHEMA

SCHEMA = (ROOT / "sql" / "harmonized" / "schema.sql").read_text()
SRC_SQL = ROOT / "sql" / "sources"
DIRTY_DDL = "CREATE TABLE _dirty_data (table_name VARCHAR, native_key VARCHAR, kind VARCHAR, note VARCHAR)"
DAYS = [date(2026, 9, 1), date(2026, 9, 2)]
PRICES, FEES = [D("10.00"), D("15.50")], [D("1.00"), D("1.55")]
CHANNELS = ["amazon", "ebay", "shopgoodwill", "goodwillfinds", "goodwillbooks"]


def _orders():
    """Two orders per channel (one per day), two lines each. Same ids in native and harmonized form."""
    out, k = [], 0
    for i, d in enumerate(DAYS, start=1):
        for ch in CHANNELS:
            books = ch in ("amazon", "goodwillbooks") or (ch == "ebay" and i == 2)
            lines = []
            for n in (1, 2):
                k += 1
                sku = f"GWM-02-{k:06d}" if books else f"UP-01-{k:06d}"
                lines.append({"n": n, "k": k, "sku": sku, "price": PRICES[n - 1], "fee": FEES[n - 1],
                              "store": "Store02" if books else "Store01", "cat": "Books" if books else "Jewelry"})
            mid = {"amazon": f"113-000000{i}-0000001", "ebay": f"12-00000-0000{i}", "shopgoodwill": str(1000 + i),
                   "goodwillfinds": str(2000 + i), "goodwillbooks": f"GWB10000{i}"}[ch]
            out.append({"ch": ch, "mid": mid, "day": d, "paid": f"{d} 15:00:00+00", "lines": lines,
                        "lob": "books" if books else "general_merch"})
    return out


def build_world(tmp_path: Path) -> tuple[Path, Path]:
    src = tmp_path / "sources"
    src.mkdir()
    orders = _orders()
    hp = tmp_path / "harmonized.duckdb"
    h = duckdb.connect(str(hp))
    h.execute(SCHEMA)
    h.execute("INSERT INTO dim_store VALUES ('Store01','S1','A',true,false), ('Store02','S2','B',false,true)")
    for ch in CHANNELS:
        h.execute("INSERT INTO dim_channel VALUES (?, ?, ?, 'both')", [ch, ch, ch])
        h.execute("INSERT INTO fct_budget VALUES (DATE '2026-09-01', ?, 1000)", [ch])
    for o in orders:
        key = f"{o['ch']}:{o['mid']}"
        refund = D("10.00") if o["ch"] == "shopgoodwill" and o["day"] == DAYS[1] else D("0")
        h.execute("INSERT INTO fct_orders VALUES (?,?,?,?,?,'b',?,?,2,25.50,5.00,0,1.00,2.55,?,31.50)",
                  [key, o["ch"], o["mid"], o["ch"], o["lob"], o["paid"], o["day"], refund])
        for ln in o["lines"]:
            h.execute("INSERT INTO fct_order_lines VALUES (?,?,?,?,?,?,?,'t',1,?,?,?)",
                      [key, ln["n"], o["ch"], ln["sku"], ln["store"], ln["cat"], o["lob"], ln["price"], ln["fee"], o["day"]])
            h.execute("INSERT INTO fct_listings VALUES (?,?,?,?,?,?,?,?,'sold',?,NULL)",
                      [f"{o['ch']}:L{ln['k']}", o["ch"], ln["sku"], ln["store"], ln["cat"], o["lob"],
                       f"{DAYS[0]} 00:00:00+00", o["paid"], ln["price"]])
            h.execute("INSERT INTO fct_items (item_id, store_id, line_of_business, category) VALUES (?,?,?,?)",
                      [ln["sku"], ln["store"], o["lob"], ln["cat"]])
        if refund:
            h.execute("INSERT INTO fct_refunds VALUES (?, ?, ?, ?, ?, ?)",
                      [f"shopgoodwill:{o['mid']}", key, "shopgoodwill", f"{o['day']} 18:00:00+00", o["day"], refund])
    for ch in CHANNELS:
        refunds = D("10.00") if ch == "shopgoodwill" else D("0")
        h.execute("INSERT INTO fct_payouts VALUES (?, ?, DATE '2026-09-05', ?, ?, 63.00, 5.10, ?, ?)",
                  [f"{ch}:P1", ch, DAYS[0], DAYS[1], refunds, D("63.00") - D("5.10") - refunds])
    # the GWB lowercase-SKU dirt was FIXED by the harmonizer (upper-cased), as expected
    h.close()

    cons = {}
    for name in quality.SOURCE_DBS:
        c = duckdb.connect(str(src / f"{name}.duckdb"))
        c.execute((SRC_SQL / f"{name}.sql").read_text())
        if name != "ops":
            c.execute(DIRTY_DDL)
        cons[name] = c
    for o in orders:
        m, L, c = o["mid"], o["lines"], cons[o["ch"]]
        if o["ch"] == "amazon":
            c.execute("INSERT INTO orders (amazon_order_id, purchase_date) VALUES (?, ?)", [m, f"{o['day']}T15:00:00Z"])
            for ln in L:
                c.execute("INSERT INTO order_items (amazon_order_id, order_item_id, seller_sku, quantity_ordered, item_price) "
                          "VALUES (?, ?, ?, 1, ?)", [m, f"OI{ln['k']}", ln["sku"], ln["price"]])
        elif o["ch"] == "ebay":
            c.execute("INSERT INTO orders (orderId, creationDate, pricingSummary_priceSubtotal) VALUES (?, ?, '25.50')",
                      [m, f"{o['day']}T15:00:00.000Z"])
            for ln in L:
                c.execute("INSERT INTO line_items (lineItemId, orderId, sku, quantity, lineItemCost) VALUES (?,?,?,1,?)",
                          [f"LI{ln['k']}", m, ln["sku"], str(ln["price"])])
        elif o["ch"] == "shopgoodwill":
            for ln in L:
                c.execute("INSERT INTO auctions (ItemID, SellerItemCode, Status) VALUES (?, ?, 'Sold')", [5000 + ln["k"], ln["sku"]])
                c.execute("INSERT INTO sales (OrderID, ItemID, PaidDate, HammerPrice) VALUES (?, ?, ?, ?)",
                          [int(m), 5000 + ln["k"], f"{o['day']} 08:00:00", ln["price"]])
        elif o["ch"] == "goodwillfinds":
            c.execute("INSERT INTO orders (id, name, created_at, subtotal_price) VALUES (?, ?, ?, 25.50)",
                      [int(m), f"#GF{m}", f"{o['day']}T11:00:00-04:00"])
            for ln in L:
                c.execute("INSERT INTO line_items (id, order_id, sku, price, quantity) VALUES (?, ?, ?, ?, 1)",
                          [3000 + ln["k"], int(m), ln["sku"], ln["price"]])
        else:
            c.execute("INSERT INTO sales_orders (order_no, order_date, items_cents) VALUES (?, ?, 2550)",
                      [m, o["day"].strftime("%m/%d/%Y 11:00")])
            for ln in L:
                sku = ln["sku"].lower() if m == "GWB100001" and ln["n"] == 1 else ln["sku"]
                c.execute("INSERT INTO sales_order_lines (order_no, line, sku, qty, unit_price_cents) VALUES (?,?,?,1,?)",
                          [m, ln["n"], sku, int(ln["price"] * 100)])
    # documented dirt: an eBay TEST order (correctly excluded from harmonized) and a lowercase GWB SKU (fixed)
    e = cons["ebay"]
    e.execute("INSERT INTO orders (orderId, creationDate, buyer_username, pricingSummary_priceSubtotal) "
              "VALUES ('12-99999-99999', '2026-09-02T16:00:00.000Z', 'TEST', '9.99')")
    e.execute("INSERT INTO line_items (lineItemId, orderId, sku, quantity, lineItemCost) "
              "VALUES ('LIT', '12-99999-99999', 'UP-01-999999', 1, '9.99')")
    e.execute("INSERT INTO _dirty_data VALUES ('orders', '12-99999-99999', 'test_order', 'TEST buyer'), "
              "('line_items', 'LIT', 'test_order', 'line of test order')")
    cons["goodwillbooks"].execute("INSERT INTO _dirty_data VALUES ('sales_order_lines', 'GWB100001/1', 'lowercase_sku', 'x')")
    for c in cons.values():
        c.close()
    return hp, src


def failing(results) -> set[str]:
    return {r["check_id"] for r in results if r["status"] != "pass"}


@pytest.fixture
def world(tmp_path):
    return build_world(tmp_path)


def test_clean_world_passes_every_check(world):
    hp, src = world
    results = quality.run_checks(hp, src)
    assert not [r for r in results if r["crashed"]], [r["detail"] for r in results if r["crashed"]]
    assert failing(results) == set(), {r["check_id"]: r["detail"] for r in results if r["status"] != "pass"}
    assert len(results) >= 17 and all(r["check_id"] != "_prelude" for r in results)
    con = duckdb.connect(str(hp), read_only=True)
    assert con.execute("SELECT count(*), count(*) FILTER (WHERE status = 'pass') FROM dq_results").fetchone() == (len(results),) * 2
    con.close()


AMZ = "amazon:113-0000001-0000001"
DEFECTS = {
    # check_id: (db, [sql], also-failing checks that the same defect necessarily breaks)
    "orders_duplicate_key": ("h", [
        "CREATE TABLE fo2 AS SELECT * FROM fct_orders", "DROP TABLE fct_orders", "ALTER TABLE fo2 RENAME TO fct_orders",
        f"INSERT INTO fct_orders SELECT * FROM fct_orders WHERE order_key = '{AMZ}'"],
        {"source_reconciliation", "payouts_vs_orders"}),
    "order_lines_orphan": ("h", [
        "INSERT INTO fct_order_lines VALUES ('amazon:NOPE',1,'amazon','GWM-02-777777','Store02','Books','books','t',1,5,0,'2026-09-01')",
        "INSERT INTO fct_listings VALUES ('amazon:L777','amazon','GWM-02-777777','Store02','Books','books',NULL,NULL,'sold',5,NULL)"],
        set()),
    "order_subtotal_vs_lines": ("h", [f"UPDATE fct_order_lines SET sale_price = sale_price + 1 WHERE order_key = '{AMZ}' AND line_no = 1"], set()),
    "fee_alloc_vs_order_fees": ("h", [f"UPDATE fct_order_lines SET fee_alloc = fee_alloc + 0.5 WHERE order_key = '{AMZ}' AND line_no = 1"], set()),
    "order_total_formula": ("h", [f"UPDATE fct_orders SET total = total + 0.5 WHERE order_key = '{AMZ}'"], set()),
    "store_id_missing": ("h", [f"UPDATE fct_order_lines SET store_id = NULL WHERE order_key = '{AMZ}' AND line_no = 1"], set()),
    "unknown_category_channel": ("h", [f"UPDATE fct_order_lines SET category = 'books ' WHERE order_key = '{AMZ}' AND line_no = 1"], set()),
    "business_date_et": ("h", [f"UPDATE fct_orders SET paid_at_utc = '2026-09-01 02:00:00+00' WHERE order_key = '{AMZ}'"], set()),
    "refunds_vs_orders": ("h", ["INSERT INTO fct_refunds VALUES ('amazon:R9','amazon:NOPE','amazon','2026-09-01 15:00:00+00','2026-09-01',0.5)"], set()),
    "payouts_vs_orders": ("h", ["UPDATE fct_payouts SET net = net + 5 WHERE channel = 'ebay'"], set()),
    "listing_order_link": ("h", ["UPDATE fct_listings SET status = 'unsold' WHERE listing_key = 'amazon:L1'"], set()),
    "item_sold_twice": ("h", ["INSERT INTO fct_listings SELECT 'amazon:L1-relist', channel, sku, store_id, category, line_of_business, "
                              "listed_at_utc, ended_at_utc, status, price, 'amazon:L1' FROM fct_listings WHERE listing_key = 'amazon:L1'"], set()),
    "source_reconciliation": ("amazon", ["UPDATE order_items SET item_price = item_price + 1 WHERE order_item_id = 'OI1'"], set()),
    "dirty_data_handled": ("h", [   # the eBay TEST order leaked into harmonized
        "INSERT INTO fct_orders VALUES ('ebay:12-99999-99999','ebay','12-99999-99999','ebay','general_merch','b',"
        "'2026-09-02 16:00:00+00','2026-09-02',1,0.5,0,0,0,0,0,0.5)",
        "INSERT INTO fct_order_lines VALUES ('ebay:12-99999-99999',1,'ebay','UP-01-999999','Store01','Jewelry','general_merch','t',1,0.5,0,'2026-09-02')",
        "INSERT INTO fct_listings VALUES ('ebay:LT','ebay','UP-01-999999','Store01','Jewelry','general_merch',NULL,NULL,'sold',0.5,NULL)"],
        set()),
    "ebay_double_counting": ("h", ["UPDATE fct_orders SET line_of_business = 'general_merch' WHERE order_key = 'ebay:12-00000-00002'"], set()),
    "month_completeness": ("h", [   # an Amazon-only order on Sep 4 => Sep 3 empty for everyone, Sep 4 empty for 4 channels
        "INSERT INTO fct_orders VALUES ('amazon:113-0000004-0000001','amazon','113-0000004-0000001','amazon','books','b',"
        "'2026-09-04 15:00:00+00','2026-09-04',1,5,0,0,0,0,0,5)",
        "INSERT INTO fct_order_lines VALUES ('amazon:113-0000004-0000001',1,'amazon','GWM-02-888888','Store02','Books','books','t',1,5,0,'2026-09-04')",
        "INSERT INTO fct_listings VALUES ('amazon:L888','amazon','GWM-02-888888','Store02','Books','books',NULL,NULL,'sold',5,NULL)",
        ("amazon", "INSERT INTO orders (amazon_order_id, purchase_date) VALUES ('113-0000004-0000001', '2026-09-04T15:00:00Z')"),
        ("amazon", "INSERT INTO order_items (amazon_order_id, order_item_id, seller_sku, quantity_ordered, item_price) "
                   "VALUES ('113-0000004-0000001', 'OI888', 'GWM-02-888888', 1, 5)")],
        set()),
    "budget_coverage": ("h", ["DELETE FROM fct_budget WHERE channel = 'goodwillfinds'"], set()),
}


def _apply(hp: Path, src: Path, db: str, stmts) -> None:
    for s in stmts:
        target, sql = (s if isinstance(s, tuple) else (db, s))
        con = duckdb.connect(str(hp if target == "h" else src / f"{target}.duckdb"))
        con.execute(sql)
        con.close()


@pytest.mark.parametrize("check_id", sorted(DEFECTS))
def test_each_defect_caught_by_its_check(world, check_id):
    hp, src = world
    db, stmts, also = DEFECTS[check_id]
    _apply(hp, src, db, stmts)
    results = quality.run_checks(hp, src, write=False)
    assert failing(results) == {check_id} | also, {r["check_id"]: r["detail"][:3] for r in results if r["status"] != "pass"}


def test_every_check_has_a_seeded_defect():
    assert {c["check_id"] for c in quality.load_checks()} == set(DEFECTS)


def test_dirty_value_leak_and_month_detail(world):
    hp, src = world
    # harmonizer forgot to upper-case the documented lowercase GWB SKU (store still set => not flagged)
    _apply(hp, src, "h", ["UPDATE fct_order_lines SET sku = lower(sku) WHERE order_key = 'goodwillbooks:GWB100001' AND line_no = 1"])
    r = {x["check_id"]: x for x in quality.run_checks(hp, src, write=False)}
    assert r["dirty_data_handled"]["status"] == "fail"
    assert r["dirty_data_handled"]["detail"][0]["reason"] == "dirty_value_loaded_on_order_line"
    # flagging it instead (store_id NULL) is an accepted outcome for the dirty check
    _apply(hp, src, "h", ["UPDATE fct_order_lines SET store_id = NULL WHERE order_key = 'goodwillbooks:GWB100001' AND line_no = 1"])
    r = {x["check_id"]: x for x in quality.run_checks(hp, src, write=False)}
    assert r["dirty_data_handled"]["status"] == "pass" and r["store_id_missing"]["status"] == "fail"


def test_missing_source_is_reported_not_silent(world):
    hp, src = world
    (src / "goodwillfinds.duckdb").unlink()
    results = quality.run_checks(hp, src, write=False)
    assert results[0]["missing_sources"] == ["goodwillfinds"]
    rec = next(r for r in results if r["check_id"] == "source_reconciliation")
    assert rec["status"] == "fail" and rec["detail"][0]["source_db"] == "goodwillfinds"


def test_cli_exit_codes(world, capsys):
    hp, src = world
    assert quality.main(["--harmonized", str(hp), "--sources", str(src)]) == 0
    _apply(hp, src, "h", ["DELETE FROM fct_budget WHERE channel = 'ebay'"])            # warning only
    assert quality.main(["--harmonized", str(hp), "--sources", str(src)]) == 0
    _apply(hp, src, "h", [f"UPDATE fct_orders SET total = total + 0.5 WHERE order_key = '{AMZ}'"])   # error
    assert quality.main(["--harmonized", str(hp), "--sources", str(src)]) == 1
    out = capsys.readouterr().out
    assert "order_total_formula" in out and "FAIL" in out


def test_reconcile_reports(world, tmp_path):
    hp, _ = world
    wp = tmp_path / "warehouse.duckdb"
    w = duckdb.connect(str(wp))
    w.execute(WAREHOUSE_SCHEMA)
    ins = "INSERT INTO orders (order_key, source_system, channel, channel_order_id, business_date, subtotal) VALUES (?,?,?,?,?,?)"
    for o in _orders():
        tool = "cashmonkey" if o["lob"] == "books" else "upright"
        w.execute(ins, [f"{tool}:{o['ch']}:{o['mid']}", tool, o["ch"], o["mid"], o["day"], 25.50])
    # eBay order of Sep 2 also (wrongly) in the Upright report => double counted
    w.execute(ins, ["upright:ebay:12-00000-00002", "upright", "ebay", "12-00000-00002", DAYS[1], 25.50])
    w.close()
    rec = quality.reconcile_reports(hp, wp)
    by = {(r["business_date"], r["pulse_row"]): r for r in rec}
    assert len(rec) == 8   # 2 days x 4 pulse rows
    assert by[("2026-09-01", "eBay")]["status"] == "match"
    assert by[("2026-09-01", "Other e-commerce channels")]["harmonized_orders"] == 2
    bad = by[("2026-09-02", "eBay")]
    assert bad["status"] == "mismatch" and bad["double_counted_orders"] == 1 and bad["revenue_diff"] == 25.5
    assert {r["status"] for r in rec} == {"match", "mismatch"}


def test_flagged_missing_store_line_reported_once(world):
    """A SKU that lost its store code ('GWM--000001') loaded with store NULL and no category: flagged, so only
    store_id_missing (warning) reports it, not listing_order_link / unknown_category_channel (errors)."""
    hp, src = world
    _apply(hp, src, "h", [f"UPDATE fct_order_lines SET sku = 'GWM--000001', store_id = NULL, category = NULL "
                          f"WHERE order_key = '{AMZ}' AND line_no = 1"])
    assert failing(quality.run_checks(hp, src, write=False)) == {"store_id_missing"}
    # ...but the same broken SKU loaded WITH a store is an error
    _apply(hp, src, "h", [f"UPDATE fct_order_lines SET store_id = 'Store02', category = 'Books' WHERE order_key = '{AMZ}' AND line_no = 1"])
    assert "listing_order_link" in failing(quality.run_checks(hp, src, write=False))
