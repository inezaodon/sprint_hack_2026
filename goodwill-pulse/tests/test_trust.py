"""Trust engine tests on the hand-built fixture (goodwill_pulse/trust/fixture.py has the worked answers)."""
import pytest

from goodwill_pulse.trust import (CATALOG, BadRequest, NotConnected, UnknownMetric, catalog_json, compute,
                                  dataset_checks, fixture)
from goodwill_pulse.trust.__main__ import BUGS, main

AS_OF = fixture.AS_OF


@pytest.fixture
def con():
    c = fixture.connect()
    yield c
    c.close()


def _c(con, *args, **kw):
    return compute(con, *args, as_of=AS_OF, **kw)


def test_headline_numbers(con):
    assert _c(con, "total_sales")["value"] == 260.00          # refund netted, unpaid excluded
    assert _c(con, "gross_sales")["value"] == 280.00          # before the $20 refund
    assert _c(con, "refunds")["value"] == 20.00
    assert _c(con, "orders")["value"] == 5
    assert _c(con, "average_sale")["value"] == 52.00
    assert _c(con, "shipping_charged")["value"] == 35.00
    assert _c(con, "items_sold")["value"] == 8
    assert _c(con, "customers")["value"] == 4                 # B1 on two platforms counts twice


def test_demo_question_jewelry_on_shopgoodwill(con):
    r = _c(con, "items_sold", {"channel": "shopgoodwill", "department": "JEWELRY"})   # case-insensitive filters
    assert r["value"] == 3 and r["verified"]


def test_breakdown_sorted_and_verified(con):
    r = _c(con, "total_sales", breakdown="channel")
    assert [(x["key"], x["value"]) for x in r["rows"]] == [("eBay", 165.0), ("ShopGoodwill", 95.0), ("Amazon", 0.0)]
    assert r["verified"]
    assert {c["id"] for c in r["checks"]} >= {"recount", "parts_equal_whole", "unpaid_excluded", "refunds_netted",
                                              "departments_platforms_file"}


def test_show_the_math(con):
    r = _c(con, "average_sale")
    assert r["math"] == "$260.00 ÷ 5 orders = $52.00"
    assert r["source_files"] == ["paid_orders_10-03-2026_10-03-2026.csv"]
    assert r["row_count"] == 7 and r["rows_excluded"] == 1    # the unpaid order


def test_books_zero_scanners(con):
    r = _c(con, "books_scanned", breakdown="store", period="last_3_weeks")
    zero = sorted(x["key"] for x in r["rows"] if x["value"] == 0)
    assert zero == ["Store11", "Store12", "Store15"] and r["verified"]


def test_refusals(con):
    with pytest.raises(UnknownMetric, match="We don't track that yet"):
        _c(con, "profit_margin")
    with pytest.raises(NotConnected, match="Thriftly"):
        _c(con, "pieces_per_hour")
    with pytest.raises(BadRequest):
        _c(con, "books_sold", breakdown="day")                # books are weekly
    with pytest.raises(BadRequest):
        _c(con, "books_sold", {"channel": "eBay"})


def test_positive_refund_is_caught(con):
    con.execute("UPDATE sales_line SET subtotal = 20.00 WHERE status = 'refund'")
    r = _c(con, "total_sales")
    assert not r["verified"]
    assert not next(c for c in r["checks"] if c["id"] == "refunds_netted")["ok"]
    assert not next(c for c in dataset_checks(con) if c["id"] == "refunds_negative")["ok"]


def test_duplicate_line_is_caught(con):
    con.execute("INSERT INTO sales_line SELECT * FROM sales_line WHERE order_id = 'O1'")
    assert not next(c for c in dataset_checks(con) if c["id"] == "no_duplicate_lines")["ok"]


def test_runner_all_green(capsys):
    assert main([]) == 0
    assert "FAIL" not in capsys.readouterr().out


@pytest.mark.parametrize("metric", sorted(BUGS))
def test_broken_formula_fails_loudly(metric, capsys):
    assert main(["--break", metric]) == 1
    assert "FAIL" in capsys.readouterr().out


def test_catalog_complete():
    from goodwill_pulse.trust.engine import FUNCS, RECOUNT_SQL
    assert set(CATALOG) == set(FUNCS) == set(RECOUNT_SQL)
    assert len(catalog_json()["metrics"]) == 11


# ---- Odin's harmonized.duckdb (skipped when it hasn't been built) ----
from goodwill_pulse.trust import odin

needs_odin = pytest.mark.skipif(not odin.HARMONIZED.exists(), reason="data/harmonized.duckdb not built")


@needs_odin
def test_odin_views_match_his_tables():
    con = odin.connect()
    mine = compute(con, "gross_sales", breakdown="channel", period={"start": "2026-10-03", "end": "2026-10-03"})
    theirs = dict(con.execute("SELECT channel, SUM(subtotal) FROM fct_orders WHERE business_date = '2026-10-03' "
                              "GROUP BY 1").fetchall())
    assert {r["key"]: r["value"] for r in mine["rows"]} == {odin.CHANNEL_NAMES[k]: float(v) for k, v in theirs.items()}
    assert mine["verified"]


@needs_odin
def test_odin_refunds_add_up():
    con = odin.connect()
    view, table = con.execute("SELECT (SELECT -SUM(subtotal) FROM sales_line WHERE status = 'refund'), "
                              "(SELECT SUM(amount) FROM fct_refunds)").fetchone()
    assert view == table                                     # pro-rata split loses no cents


@needs_odin
def test_odin_kpi_cross_check_agrees():
    lines = odin.cross_check(odin.connect())
    assert lines and all(ok for _, _, ok, _ in lines), [l for l in lines if not l[2]]
