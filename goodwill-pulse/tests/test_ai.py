"""AI layer: number checker, narratives (fallback + fake Claude), column mapper, ask-the-data. No network."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import duckdb
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from goodwill_pulse.ai import ask as ask_mod
from goodwill_pulse.ai import client as ai
from goodwill_pulse.ai.mapper import suggest_mapping
from goodwill_pulse.ai.narrator import (month_narrative, pulse_facts, pulse_narrative, template_month,
                                        template_pulse)
from goodwill_pulse.ai.numbers import check_numbers, extract_numbers
from goodwill_pulse.config import mappings

ROOT = Path(__file__).resolve().parent.parent
SCHEMA = ROOT / "sql" / "harmonized" / "schema.sql"


@pytest.fixture(autouse=True)
def no_claude(monkeypatch):
    """Default: no key, no injected client -> fallback path."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    ai.set_client(None)
    yield
    ai.set_client(None)


# --------------------------------------------------------------------------------------------- fake Claude
def _text(s: str):
    return SimpleNamespace(type="text", text=s)


def _resp(*blocks, stop="end_turn"):
    return SimpleNamespace(content=list(blocks), stop_reason=stop,
                           usage=SimpleNamespace(input_tokens=1, output_tokens=1, cache_read_input_tokens=0))


def _tool_use(name: str, inp: dict, id_: str = "tu_1"):
    return SimpleNamespace(type="tool_use", name=name, input=inp, id=id_)


class FakeClient:
    """Mimics client.beta.messages.create; returns scripted responses and records every call."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls: list[dict] = []
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self._create))

    def _create(self, **kw):
        self.calls.append(json.loads(json.dumps(kw, default=lambda o: o.__dict__)))
        if not self.responses:
            raise RuntimeError("fake client: no more scripted responses")
        r = self.responses.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


# --------------------------------------------------------------------------------------------- facts
PULSE = {
    "business_date": "2026-09-30", "weekday": "Wednesday", "timezone": "America/New_York", "revenue_basis": "subtotal",
    "rows": [
        {"label": "ShopGoodwill", "channels": ["shopgoodwill"], "revenue": 11120.4, "customers": 108, "buyers": 105,
         "revenue_last_week": 9800.0, "customers_last_week": 99, "revenue_mtd": 301234.55},
        {"label": "Amazon", "channels": ["amazon"], "revenue": 612.3, "customers": 41, "buyers": 41,
         "revenue_last_week": 650.0, "customers_last_week": 44, "revenue_mtd": 18000.0},
        {"label": "eBay", "channels": ["ebay"], "revenue": 1500.0, "customers": 20, "buyers": 20,
         "revenue_last_week": 1400.0, "customers_last_week": 18, "revenue_mtd": 40000.0},
        {"label": "Other e-commerce channels", "channels": ["goodwillfinds"], "revenue": 0.0, "customers": 0,
         "buyers": 0, "revenue_last_week": 100.0, "customers_last_week": 2, "revenue_mtd": 5000.0},
    ],
    "total": {"revenue": 13232.7, "customers": 169, "buyers": 166, "revenue_last_week": 11950.0,
              "customers_last_week": 163, "revenue_mtd": 364234.55},
    "sources": [{"report_type": "upright_paid_orders", "label": "Upright paid orders", "status": "complete",
                 "covered_through": "2026-10-01T00:00:00-04:00"},
                {"report_type": "cashmonkey_orders", "label": "Cash Monkey orders", "status": "partial",
                 "covered_through": "2026-09-30T22:00:00-04:00"}],
    "open_exceptions": 0, "complete": False, "as_of": "2026-09-30T22:00:00-04:00",
    "generated_at": "2026-10-03T15:51:31-04:00",
}


def _card(id_, label, unit, value, pm, py, anchor=False, **kw):
    return {"id": id_, "label": label, "pillar": "growth", "unit": unit, "value": value, "prior_month": pm,
            "prior_year": py, "status": None, "availability": "built", "anchor": anchor,
            "trend": [{"month": "2026-08-01", "value": 123456.0}], **kw}


SCORECARD = {
    "month": "2026-09-01", "as_of": "2026-10-03", "filters": {"store": None, "channel": None, "category": None},
    "anchors": [_card("net_margin", "E-com net margin", "pct", 0.1234, 0.11, 0.105, True),
                _card("revenue_per_labor_hour", "Revenue per labor hour", "usd_per_hour", 61.37, 58.2, 55.0, True),
                _card("sell_through", "Sell-through rate", "pct", 0.512, 0.53, None, True, provisional=True)],
    "scorecard": [_card("total_revenue", "Total e-com revenue", "usd", 412345.67, 398000.0, 371000.0),
                  _card("listings_created", "Listings created", "count", 15234.0, 14100.0, 13000.0),
                  _card("repeat_buyer_rate", "Repeat buyer rate", "pct", 0.31, 0.30, 0.28),
                  _card("customer_satisfaction", "Customer satisfaction", "count", None, None, None),
                  _card("top_categories_by_revenue", "Top 10 categories by revenue", "usd", 90000.0, 85000.0, 80000.0,
                        breakdown=[{"key": "Jewelry", "value": 90000.0}, {"key": "Books", "value": 60000.0}])],
}


# --------------------------------------------------------------------------------------------- number checker
def test_extracts_common_formats():
    got = {f.text: f.value for f in extract_numbers("Revenue $13.2K, up 18%, 1,204 orders, $13,247.50 total, "
                                                     "Store07 and Q3 are ids, $1.5 million.")}
    assert got["$13.2K"] == 13200 and got["18%"] == 18 and got["1,204"] == 1204 and got["$13,247.50"] == 13247.5
    assert got["$1.5 million"] == 1_500_000
    assert not any("07" in t or t == "3" for t in got)


def test_checker_rounding_tolerance_and_percent_fraction():
    facts = {"rev": 13247.5, "growth": 0.1834, "orders": 1204, "date": "2026-09-30"}
    ok = check_numbers("On September 30, 2026 revenue was $13.2K ($13,248), up 18%, from 1,204 orders.", facts)
    assert ok.ok, ok.unmatched
    assert {c["matched_path"] for c in ok.checked} >= {"rev", "growth", "orders"}


def test_checker_rejects_invented_number():
    facts = {"rev": 13247.5, "orders": 1204}
    res = check_numbers("Revenue was $13.2K from 1,204 orders, the best day in 14 weeks.", facts)
    assert not res.ok and [u["text"] for u in res.unmatched] == ["14"]
    # wrong rounding is also caught: 13,247.50 is $13.2K, not $13.3K
    assert not check_numbers("Revenue was $13.3K.", facts).ok


# --------------------------------------------------------------------------------------------- narratives
def test_fallback_pulse_narrative_passes_checker():
    r = pulse_narrative(PULSE)
    assert r["engine"] == "fallback" and r["unmatched_numbers"] == []
    assert check_numbers(r["text"], r["facts"]).ok
    assert "$13,233" in r["text"] and "10 PM" in r["text"]
    assert 2 <= r["text"].count(". ") + 1 <= 4
    assert "generated_at" not in r["facts"]


def test_fallback_month_narrative_passes_checker():
    r = month_narrative(SCORECARD)
    assert r["engine"] == "fallback" and r["unmatched_numbers"] == [], r
    assert "12.3%" in r["text"] and "September 2026" in r["text"]
    assert "123,456" not in r["text"]                       # trend arrays never reach the facts


def test_templates_never_prescribe():
    from goodwill_pulse.ai.narrator import PRESCRIPTIVE_RE
    f = pulse_facts(PULSE)
    assert not PRESCRIPTIVE_RE.search(template_pulse(f))
    assert not PRESCRIPTIVE_RE.search(template_month(month_narrative(SCORECARD)["facts"]))


def test_claude_narrative_regenerates_once_on_bad_number():
    good = "Wednesday revenue was $13,233 from 169 orders, up 10.7% on the same day last week. ShopGoodwill brought in $11,120."
    fake = FakeClient([_resp(_text(json.dumps({"narrative": "Revenue was $14.1K, a record for 9 weeks."}))),
                       _resp(_text(json.dumps({"narrative": good})))])
    ai.set_client(fake)
    r = pulse_narrative(PULSE)
    assert r["engine"] == "claude" and r["text"] == good
    assert len(r["rejected_attempts"]) == 1
    assert {u["text"] for u in r["rejected_attempts"][0]["unmatched_numbers"]} == {"$14.1K", "9"}
    assert len(fake.calls) == 2
    call = fake.calls[0]
    assert call["model"] == "claude-opus-5-5" and call["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert call["output_config"]["format"]["type"] == "json_schema"
    assert "$14.1K" in fake.calls[1]["messages"][-1]["content"]      # the retry names the bad number


def test_claude_narrative_falls_back_after_two_failures_and_on_errors():
    ai.set_client(FakeClient([_resp(_text(json.dumps({"narrative": "Revenue was $99,999."}))),
                              _resp(_text(json.dumps({"narrative": "You should restock jewelry; revenue $13,233."})))]))
    r = pulse_narrative(PULSE)
    assert r["engine"] == "fallback" and len(r["rejected_attempts"]) == 2 and r["unmatched_numbers"] == []
    ai.set_client(FakeClient([TimeoutError("boom")]))
    r = month_narrative(SCORECARD)
    assert r["engine"] == "fallback" and r["fallback_reason"] == "claude call failed"


# --------------------------------------------------------------------------------------------- mapper
UPRIGHT_HEADER = list(mappings()["upright_paid_orders"]["columns"])
UPRIGHT_ROW = ["24255501", "Shopgoodwill", "65816275", "", "resell.7694", "1", "", "CreditCard", "37.49", "20.74",
               "12.38", "", "3", "1.37", "0", "USD", "", "0", "10/03/2026 04:46:58"]
CM_HEADER = list(mappings()["cashmonkey_orders"]["columns"])
CM_ROW = ["2026-10-03 13:01:02", "113-1234567-1234567", "276", "Amazon-MF", "GWM-07-000123", "B00X", "A Book", "Good",
          "Store07", "1", "7.99", "3.99", "1.20", "3.50", "7.28", "buyer@relay"]


def test_mapper_recognizes_both_report_types_from_headers():
    up = suggest_mapping(UPRIGHT_HEADER, [UPRIGHT_ROW], "export.csv")
    cm = suggest_mapping(CM_HEADER, [CM_ROW], "download.csv")
    assert up["report_type"] == "upright_paid_orders" and up["engine"] == "fallback" and not up["missing_required"]
    assert cm["report_type"] == "cashmonkey_orders" and not cm["missing_required"]
    assert up["confidence"] >= 0.85 and cm["confidence"] >= 0.85
    assert up["applied"] is False and all(c["ok"] for c in up["sample_checks"])


def test_mapper_maps_renamed_sub_total():
    header = ["Sub Total" if h == "Subtotal" else h for h in UPRIGHT_HEADER]
    r = suggest_mapping(header, [UPRIGHT_ROW], "paid_orders_10-03-2026_10-03-2026 (2).csv")
    assert r["report_type"] == "upright_paid_orders"
    assert r["column_mapping"]["Sub Total"] == "subtotal"
    assert r["aliases"] == {"Sub Total": "Subtotal"}
    assert r["column_mapping"]["Secondary Channel"] == "ignore"
    assert not r["missing_required"] and r["filename_matches"]


def test_mapper_flags_type_mismatch_and_unknown_file():
    bad = list(UPRIGHT_ROW)
    bad[9] = "lots"            # Subtotal not money
    r = suggest_mapping(UPRIGHT_HEADER, [bad], "x.csv")
    assert any(not c["ok"] and c["source_column"] == "Subtotal" for c in r["sample_checks"])
    assert suggest_mapping(["foo", "bar"], [["1", "2"]], "x.csv")["report_type"] is None


def test_mapper_claude_path_validated(monkeypatch):
    header = ["Sub Total" if h == "Subtotal" else h for h in UPRIGHT_HEADER]
    cols = [{"source_column": "Sub Total", "canonical_field": "subtotal", "transform": ""},
            {"source_column": "Paid At", "canonical_field": "made_up_field", "transform": ""}]
    ai.set_client(FakeClient([_resp(_text(json.dumps({"report_type": "upright_paid_orders", "confidence": 0.9,
                                                       "columns": cols, "notes": "Renamed subtotal."})))]))
    r = suggest_mapping(header, [UPRIGHT_ROW], "x.csv")
    assert r["engine"] == "claude" and r["column_mapping"]["Sub Total"] == "subtotal"
    assert r["column_mapping"]["Paid At"] == "paid_at_local"           # invalid field replaced by fuzzy match
    assert r["column_mapping"]["Upright Order ID"] == "source_order_id"  # columns the model skipped are filled


# --------------------------------------------------------------------------------------------- ask the data
def _utc(s: str) -> datetime:
    return datetime.fromisoformat(s).replace(tzinfo=timezone.utc)


@pytest.fixture
def hdb(tmp_path) -> Path:
    path = tmp_path / "harmonized.duckdb"
    con = duckdb.connect(str(path))
    con.execute(SCHEMA.read_text())
    con.executemany("INSERT INTO dim_store VALUES (?,?,?,?,?)",
                    [(f"Store0{i}", f"Store {i}", "Town", i == 1, False) for i in (1, 2, 3)])
    orders = [  # key, channel, paid_at, business_date, subtotal
        ("ebay:1", "ebay", "2026-09-05 15:00:00", "2026-09-05", 100.00),
        ("ebay:2", "ebay", "2026-09-20 15:00:00", "2026-09-20", 50.25),
        ("shopgoodwill:1", "shopgoodwill", "2026-09-10 15:00:00", "2026-09-10", 300.00),
        ("amazon:1", "amazon", "2026-09-12 15:00:00", "2026-09-12", 12.00),
        ("ebay:3", "ebay", "2026-08-15 15:00:00", "2026-08-15", 70.00),
        ("shopgoodwill:2", "shopgoodwill", "2026-10-02 15:00:00", "2026-10-02", 40.00),
    ]
    for k, ch, paid, bd, sub in orders:
        con.execute("""INSERT INTO fct_orders (order_key, channel, marketplace_order_id, source_db, line_of_business,
                       buyer_key, paid_at_utc, business_date, item_count, subtotal, shipping_charged, handling, tax,
                       marketplace_fees, refund_amount, total) VALUES (?,?,?,?, 'general_merch', ?, ?, ?, 1, ?, 0,0,0,
                       0,0, ?)""", [k, ch, k.split(":")[1], ch, "b" + k, _utc(paid), bd, sub, sub])
    lines = [  # order_key, sku, store, category, price
        ("ebay:1", "S1", "Store01", "Jewelry", 100.00), ("ebay:2", "S2", "Store02", "Books", 50.25),
        ("shopgoodwill:1", "S3", "Store02", "Jewelry", 300.00), ("amazon:1", "S9", "Store03", "Books", 12.00),
        ("ebay:3", "S0", "Store01", "Toys & Games", 70.00), ("shopgoodwill:2", "S8", "Store02", "Art", 40.00)]
    for k, sku, st, cat, price in lines:
        bd = next(o[3] for o in orders if o[0] == k)
        con.execute("INSERT INTO fct_order_lines VALUES (?,1,?,?,?,?, 'general_merch', 't', 1, ?, 0, ?)",
                    [k, k.split(":")[0], sku, st, cat, price, bd])
    listings = [("S1", "2026-09-01 14:00:00"), ("S2", "2026-09-02 14:00:00"), ("S4", "2026-09-03 14:00:00"),
                ("S5", "2026-09-04 14:00:00"), ("S0", "2026-08-01 14:00:00")]
    for i, (sku, at) in enumerate(listings):
        con.execute("INSERT INTO fct_listings VALUES (?, 'ebay', ?, 'Store01', 'Books', 'general_merch', ?, NULL,"
                    " 'active', 10, NULL)", [f"ebay:L{i}", sku, _utc(at)])
    items = [("S1", "Store01"), ("S2", "Store02"), ("S3", "Store02"), ("S4", "Store02"), ("S5", "Store03")]
    for sku, st in items:
        con.execute("INSERT INTO fct_items (item_id, store_id, line_of_business, category, identified_at_utc, "
                    "manifested_at_utc) VALUES (?, ?, 'general_merch', 'Books', ?, ?)",
                    [sku, st, _utc("2026-08-28 12:00:00"), _utc("2026-09-01 12:00:00")])
    con.executemany("INSERT INTO dq_results VALUES (?, ?, ?, ?, ?, '[]', ?)", [
        ("store_id_missing", _utc("2026-10-03 01:00:00"), "warning", "fail", 3, "Lines without a store"),
        ("orders_duplicate_key", _utc("2026-10-03 01:00:00"), "error", "pass", 0, "Duplicate order keys")])
    con.close()
    return path


def test_ask_router_answers_three_canned_questions(hdb):
    r = ask_mod.ask("Which stores sent the most items to e-commerce last month?", db_path=hdb)
    assert r["engine"] == "fallback" and r["citations"][0] == {
        "tool": "items_by_store", "filters": {"month": "2026-09", "stage": "sent", "limit": 10}}
    assert r["answer"].startswith("Stores with the most items sent to e-commerce in September 2026: Store02 (3)")
    assert r["unmatched_numbers"] == []

    r = ask_mod.ask("What was sell-through in September?", db_path=hdb)
    assert r["citations"][0]["tool"] == "kpi_value"
    assert r["citations"][0]["filters"] == {"kpi_id": "sell_through", "month": "2026-09"}
    assert r["results"][0]["data"]["value"] == pytest.approx(0.5)
    assert "50.0%" in r["answer"] and r["unmatched_numbers"] == []

    r = ask_mod.ask("What was revenue on eBay last month?", db_path=hdb)
    assert r["citations"][0] == {"tool": "revenue_by", "filters": {"month": "2026-09", "by": "channel",
                                                                   "channel": "ebay"}}
    assert "$150.25 from 2 orders" in r["answer"] and r["unmatched_numbers"] == []


def test_ask_dq_and_categories_and_unknown(hdb):
    assert "store_id_missing (warning, 3 rows)" in ask_mod.ask("Any data quality checks failing?", db_path=hdb)["answer"]
    r = ask_mod.ask("top categories in September 2026", db_path=hdb)
    assert r["results"][0]["data"]["rows"][0] == {"key": "Jewelry", "revenue": 400.0, "units": 2}
    r = ask_mod.ask("what's the weather", db_path=hdb)
    assert r["citations"] == [] and "Try:" in r["answer"]


def test_tools_reject_bad_arguments(hdb):
    con = duckdb.connect(str(hdb), read_only=True)
    assert "error" in ask_mod.run_tool(con, "revenue_by", {"month": "Sept", "by": "store"})
    assert "error" in ask_mod.run_tool(con, "revenue_by", {"month": "2026-09", "by": "store", "channel": "etsy"})
    assert "error" in ask_mod.run_tool(con, "drop_table", {})
    con.close()


def test_ask_claude_tool_use_loop(hdb):
    fake = FakeClient([
        _resp(_tool_use("revenue_by", {"month": "2026-09", "by": "channel", "channel": "ebay"}), stop="tool_use"),
        _resp(_text("eBay revenue in September 2026 was $150.25 from 2 orders.\n"
                    "Source: revenue_by(month=2026-09, by=channel, channel=ebay)")),
    ])
    ai.set_client(fake)
    r = ask_mod.ask("How much did we make on eBay last month?", db_path=hdb)
    assert r["engine"] == "claude" and r["citations"][0]["tool"] == "revenue_by"
    assert r["results"][0]["data"]["rows"][0]["revenue"] == 150.25
    second = fake.calls[1]["messages"]
    assert second[-1]["content"][0]["type"] == "tool_result" and second[-1]["content"][0]["tool_use_id"] == "tu_1"
    assert all(t.get("strict") for t in fake.calls[0]["tools"])
    assert {t["name"] for t in fake.calls[0]["tools"]} >= {"revenue_by", "kpi_value", "open_dq_failures"}


def test_ask_claude_invented_number_regenerates_then_falls_back(hdb):
    ai.set_client(FakeClient([
        _resp(_tool_use("revenue_by", {"month": "2026-09", "by": "channel", "channel": "ebay"}), stop="tool_use"),
        _resp(_text("eBay made $175 in September.")),
        _resp(_text("eBay made $180 in September.")),
    ]))
    r = ask_mod.ask("What was revenue on eBay last month?", db_path=hdb)
    assert r["engine"] == "fallback" and "failed the number check" in r["fallback_reason"]
    assert "$150.25" in r["answer"]


# --------------------------------------------------------------------------------------------- routes
@pytest.fixture
def api(hdb, monkeypatch):
    from goodwill_pulse.routes import ai as routes
    monkeypatch.setattr(routes, "HARMONIZED", hdb)
    app = FastAPI()
    app.include_router(routes.router)
    return TestClient(app)


def test_routes(api):
    r = api.post("/api/ai/narrative/pulse", json=PULSE).json()
    assert r["engine"] == "fallback" and r["checked_numbers"] and r["unmatched_numbers"] == []
    r = api.post("/api/ai/narrative/month", json=SCORECARD).json()
    assert r["engine"] == "fallback" and "checked_numbers" in r
    r = api.post("/api/ai/narrative/month?month=2026-09").json()       # real kpi.scorecard on the tiny DB
    assert r["engine"] == "fallback" and r["unmatched_numbers"] == [], r
    header = ",".join("Sub Total" if h == "Subtotal" else h for h in UPRIGHT_HEADER)
    csv_bytes = (header + "\n" + ",".join(UPRIGHT_ROW) + "\n").encode()
    r = api.post("/api/ai/map-columns", files={"file": ("export.csv", csv_bytes, "text/csv")}).json()
    assert r["engine"] == "fallback" and r["aliases"] == {"Sub Total": "Subtotal"}
    r = api.post("/api/ai/map-columns", json={"header": CM_HEADER, "rows": [CM_ROW], "filename": "a.csv"}).json()
    assert r["report_type"] == "cashmonkey_orders"
    r = api.post("/api/ai/ask", json={"question": "revenue by channel", "month": "2026-09"}).json()
    assert r["engine"] == "fallback" and r["citations"][0]["tool"] == "revenue_by"
    assert api.post("/api/ai/ask", json={"question": " "}).status_code == 422
    assert api.get("/api/ai/status").json() == {"claude_available": False, "model": "claude-opus-5-5"}
