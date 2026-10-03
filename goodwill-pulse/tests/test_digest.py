"""The nightly report must agree with the warehouse and with the Monday catch-up rule."""
from datetime import date

import duckdb
import pytest

from goodwill_pulse import digest


def test_catchup_is_friday_through_sunday():
    assert digest.catchup_range(date(2026, 9, 28)) == (date(2026, 9, 25), date(2026, 9, 27))


@pytest.fixture()
def harmonized(tmp_path):
    p = tmp_path / "h.duckdb"
    con = duckdb.connect(str(p))
    con.execute("""CREATE TABLE fct_orders (order_key VARCHAR, channel VARCHAR, business_date DATE, subtotal DECIMAL(12,2),
                   shipping_charged DECIMAL(12,2), marketplace_fees DECIMAL(12,2), refund_amount DECIMAL(12,2))""")
    con.execute("""INSERT INTO fct_orders VALUES
        ('a1', 'ebay', '2026-09-26', 100.00, 10.00, 12.00, 0), ('a2', 'ebay', '2026-09-26', 50.00, 5.00, 6.00, 20.00),
        ('b1', 'amazon', '2026-09-27', 30.00, 4.00, 5.00, 0), ('c1', 'ebay', '2026-09-19', 80.00, 8.00, 9.00, 0)""")
    con.close()
    return p


def test_totals_sum_the_three_manual_numbers(harmonized):
    d = digest.build_digest(date(2026, 9, 25), date(2026, 9, 27), harmonized)
    t = d["total"]
    assert (t["orders"], t["item_sales"], t["shipping"], t["fees"], t["refunds"]) == (3, 180.0, 19.0, 23.0, 20.0)
    assert {r["channel"]: r["orders"] for r in d["rows"]}["ebay"] == 2
    assert t["prior_item_sales"] == 80.0          # the same days one week earlier
    assert d["trust"]["checks"] == 0               # no dq_results table: reported as not run, never as passing


def test_render_mentions_totals_and_trust(harmonized):
    d = digest.build_digest(date(2026, 9, 25), date(2026, 9, 27), harmonized)
    text, html = digest.render_text(d), digest.render_html(d)
    assert "$180.00" in text and "3 orders" in text and "Data checks have not been run" in text
    assert "$180.00" in html and "Fri Sep 25 through Sun Sep 27" in html
