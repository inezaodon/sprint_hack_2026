"""Run artifact/export_lineage.py against the real data and check the contract-5 shapes (artifact/AGENTS.md)."""
import importlib.util
import json
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("export_lineage", ROOT / "artifact" / "export_lineage.py")
mod = importlib.util.module_from_spec(SPEC)
sys.modules["export_lineage"] = mod
SPEC.loader.exec_module(mod)

pytestmark = pytest.mark.skipif(not (ROOT / "data" / "harmonized.duckdb").exists(), reason="needs built data")


@pytest.fixture(scope="module")
def docs(tmp_path_factory):
    out = tmp_path_factory.mktemp("lineage")
    sizes = mod.main(out)
    d = {n: json.loads((out / f"lineage__{n}.json").read_text()) for n in ("overview", "metrics", "ledger", "drill")}
    return d, sizes


def test_size_limits(docs):
    _, sizes = docs
    assert set(sizes) == {f"lineage__{n}.json" for n in ("overview", "metrics", "ledger", "drill")}
    for name, size in sizes.items():
        assert 0 < size < 250_000, (name, size)


def test_overview_shape(docs):
    ov = docs[0]["overview"]
    for k in ("generated_at", "pipeline", "sources", "harmonized", "artifact_docs", "builds", "exclusions"):
        assert k in ov
    assert len(ov["pipeline"]) >= 7 and all({"id", "title", "detail", "tables"} <= set(p) for p in ov["pipeline"])
    for t in ov["sources"] + ov["harmonized"]:
        assert {"db", "table", "rows", "columns", "grain", "description"} <= set(t)
        assert t["columns"] and all({"name", "type"} == set(c) for c in t["columns"])
        assert t["grain"] != "n/a", (t["db"], t["table"])
    assert {"fct_orders", "dim_store"} <= {t["table"] for t in ov["harmonized"]}
    assert {"amazon", "ebay", "shopgoodwill", "goodwillfinds", "goodwillbooks", "ops"} <= {t["db"] for t in ov["sources"]}
    assert all({"path", "bytes", "what"} <= set(d) for d in ov["artifact_docs"]) and ov["artifact_docs"]
    assert {"run_id", "started_at", "seconds"} <= set(ov["builds"]["harmonize"])
    assert {"run_at", "total", "passed"} <= set(ov["builds"]["quality"])
    assert {"test_orders", "test_skus", "canceled_orders"} <= set(ov["exclusions"])


def test_metrics_shape(docs):
    from goodwill_pulse.kpi import KPIS
    ms = docs[0]["metrics"]["measures"]
    ids = {m["id"] for m in ms}
    assert {"item_sales", "orders", "shipping", "fees", "refunds", "avg_order", "store_revenue", "units_sold",
            "items_identified", "items_sent", "items_listed"} <= ids
    assert set(KPIS) <= ids
    for m in ms:
        assert {"id", "label", "synonyms", "unit", "kind", "formula_text", "sql", "source_tables", "columns_used", "notes"} <= set(m)
        assert m["kind"] in ("daily", "pipeline", "kpi") and m["synonyms"] and m["formula_text"]
        if m["kind"] != "kpi" or KPIS[m["id"]].availability != "n/a":
            assert "SELECT" in m["sql"].upper() and m["source_tables"]
    by = {m["id"]: m for m in ms}
    assert "revenue" in by["item_sales"]["synonyms"] and "AOV" in by["avg_order"]["synonyms"]


def test_ledger_shape(docs):
    led = docs[0]["ledger"]
    assert "as_of" in led and led["checks"]
    ids = [c["id"] for c in led["checks"]]
    assert len(ids) == len(set(ids))
    for c in led["checks"]:
        assert {"id", "title", "scope", "measure", "paths", "page_value", "page_source", "status", "tolerance", "diff"} <= set(c)
        assert c["status"] in ("match", "diff")
        assert len(c["paths"]) >= 2, c["id"]
        assert c["page_source"].startswith("artifact/data/")
        assert c["page_value"] is not None
        for p in c["paths"]:
            assert {"label", "sql", "value", "rows_scanned", "source_tables"} <= set(p)
            assert isinstance(p["value"], (int, float)) and p["rows_scanned"] >= 0
        if c["status"] == "diff":
            assert c["explanation"]
    measures = {c["measure"] for c in led["checks"]}
    assert {"item_sales", "orders", "shipping", "fees", "refunds", "total_revenue", "store_revenue", "payout_net"} <= measures
    assert led["summary"]["checks"] == len(led["checks"])


def test_ledger_status_follows_numbers(docs):
    for c in docs[0]["ledger"]["checks"]:
        worst = max(abs(p["value"] - c["page_value"]) for p in c["paths"] if not p.get("component"))
        assert (c["status"] == "match") == (worst <= c["tolerance"] + 1e-9), c["id"]


def test_drill_shape_and_sums(docs):
    drill = docs[0]["drill"]
    assert len(drill) == 7 * 5
    for key, cell in drill.items():
        assert re.match(r"^\d{4}-\d{2}-\d{2}\|[a-z]+$", key)
        assert {"total_rows", "sum_item_sales", "sum_shipping", "sum_fees", "rows"} <= set(cell)
        assert len(cell["rows"]) <= 60 and len(cell["rows"]) <= cell["total_rows"]
        for r in cell["rows"]:
            assert {"order_key", "paid_at_et", "item_sales", "shipping", "fees", "refund", "source_db", "native_key"} <= set(r)
        amounts = [r["item_sales"] for r in cell["rows"]]
        assert amounts == sorted(amounts, reverse=True)
        if not cell["truncated"]:
            assert round(sum(amounts), 2) == cell["sum_item_sales"]


def test_drill_matches_daily_doc(docs):
    daily = json.loads((ROOT / "artifact" / "data" / "daily__all.json").read_text())
    ix = {c: i for i, c in enumerate(daily["cols"])}
    for r in daily["rows"]:
        key = f"{r[ix['date']]}|{r[ix['channel']]}"
        if key in docs[0]["drill"]:
            assert docs[0]["drill"][key]["total_rows"] == r[ix["orders"]]
            assert docs[0]["drill"][key]["sum_item_sales"] == pytest.approx(r[ix["item_sales"]], abs=0.005)
