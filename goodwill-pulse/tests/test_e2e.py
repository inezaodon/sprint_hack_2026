"""PM acceptance tests: the built platform must agree with the truth world it was generated from.

Runs against the real build in data/ (`python -m goodwill_pulse.build`); skips if it hasn't been built.
These are the checks that decide whether the team's work is "done".
"""
from datetime import date
from pathlib import Path

import duckdb
import pytest

from goodwill_pulse.config import DATA_DIR

SOURCES = DATA_DIR / "sources"
TRUTH = SOURCES / "_truth.duckdb"
HARMONIZED = DATA_DIR / "harmonized.duckdb"
SEPT = date(2026, 9, 1)

pytestmark = pytest.mark.skipif(not (TRUTH.exists() and HARMONIZED.exists()), reason="platform not built")


@pytest.fixture(scope="module")
def con():
    c = duckdb.connect()
    c.execute(f"ATTACH '{TRUTH}' AS truth (READ_ONLY)")
    c.execute(f"ATTACH '{HARMONIZED}' AS h (READ_ONLY)")
    c.execute("SET TimeZone = 'UTC'")
    yield c
    c.close()


def test_every_truth_order_is_harmonized_exactly_once(con):
    missing, extra = con.execute("""
        WITH t AS (SELECT channel || ':' || marketplace_order_id AS k FROM truth.orders),
             f AS (SELECT order_key AS k FROM h.fct_orders)
        SELECT (SELECT count(*) FROM t WHERE k NOT IN (SELECT k FROM f)),
               (SELECT count(*) FROM f WHERE k NOT IN (SELECT k FROM t))""").fetchone()
    assert missing == 0, f"{missing} truth orders missing from fct_orders"
    assert extra == 0, f"{extra} fct_orders rows are not real orders (dirty data leaked)"


def test_monthly_revenue_by_channel_matches_truth(con):
    diffs = con.execute("""
        WITH t AS (SELECT channel, date_trunc('month', timezone('America/New_York', paid_at))::DATE AS m,
                          sum(subtotal) AS rev, count(*) AS n FROM truth.orders GROUP BY ALL),
             f AS (SELECT channel, date_trunc('month', business_date)::DATE AS m, sum(subtotal) AS rev, count(*) AS n
                   FROM h.fct_orders GROUP BY ALL)
        SELECT t.channel, t.m, t.rev, f.rev, t.n, f.n FROM t FULL JOIN f USING (channel, m)
        WHERE abs(coalesce(t.rev, 0) - coalesce(f.rev, 0)) > 0.01 OR coalesce(t.n, 0) <> coalesce(f.n, 0)
        ORDER BY 1, 2""").fetchall()
    assert not diffs, f"channel-month mismatches (channel, month, truth rev, harmonized rev, truth n, harmonized n): {diffs[:10]}"


def test_business_date_is_eastern(con):
    bad = con.execute("""
        SELECT count(*) FROM truth.orders t JOIN h.fct_orders f
          ON f.order_key = t.channel || ':' || t.marketplace_order_id
        WHERE f.business_date <> timezone('America/New_York', t.paid_at)::DATE
           OR abs(epoch(f.paid_at_utc) - epoch(t.paid_at)) > 60""").fetchone()[0]
    assert bad == 0


def test_store_credit_matches_truth(con):
    bad = con.execute("""
        SELECT count(*) FROM truth.order_lines l JOIN truth.items i USING (item_id)
        JOIN truth.orders o USING (order_id)
        JOIN h.fct_order_lines f ON f.order_key = o.channel || ':' || o.marketplace_order_id AND f.sku = l.item_id
        WHERE f.store_id IS DISTINCT FROM i.store_id""").fetchone()[0]
    assert bad == 0


def test_refunds_and_fees_match_truth(con):
    t_ref, t_fee = con.execute("SELECT (SELECT sum(amount) FROM truth.refunds), "
                               "(SELECT sum(marketplace_fee + payment_fee) FROM truth.orders)").fetchone()
    h_ref, h_fee = con.execute("SELECT (SELECT sum(amount) FROM h.fct_refunds), "
                               "(SELECT sum(marketplace_fees) FROM h.fct_orders)").fetchone()
    assert abs(t_ref - h_ref) < 1, (t_ref, h_ref)
    assert abs(t_fee - h_fee) < 1, (t_fee, h_fee)


def test_no_error_level_quality_failures():
    from goodwill_pulse import quality
    results = quality.run_checks(HARMONIZED, SOURCES)
    errors = [r for r in results if r["severity"] == "error" and r["status"] == "fail"]
    assert not errors, [(r["check_id"], r["failing_rows"]) for r in errors]


def test_september_scorecard_is_complete():
    from goodwill_pulse import kpi
    c = kpi.connect_harmonized(HARMONIZED)
    sc = kpi.scorecard(c, SEPT)
    assert len(sc["anchors"]) == 3 and len(sc["scorecard"]) == 15
    for card in sc["anchors"] + sc["scorecard"]:
        if card["availability"] != "n/a":
            assert card["value"] is not None, card["id"]
    vals = {c["id"]: c["value"] for c in sc["anchors"]}
    assert all(v is not None for v in vals.values()), vals


def test_september_close_balances_and_catches_planted_error():
    from goodwill_pulse.close import rules
    from goodwill_pulse.close import workbook_compare
    run = rules.run_close(SEPT, HARMONIZED, SOURCES / "finance.duckdb", Path("config/close_rules.yaml"))
    by_doc = run.allocations.groupby("doc_no")["amount"].sum().round(2)
    assert (by_doc == 0).all(), by_doc[by_doc != 0]
    assert run.allocations["source_ref"].notna().all()
    wb = DATA_DIR / "close_inputs" / "2026-09" / "E-Commerce Allocation 2026-09.xlsx"
    assert wb.exists()
    diffs = workbook_compare.compare(run, wb)
    found = [d for d in (diffs["differences"] if isinstance(diffs, dict) else diffs)]
    assert len(found) == 1, found
