"""Supro store sales for the Revenue Hub: one row per store x day, deterministic, real weekly sheets win over estimates."""
from __future__ import annotations

from datetime import date, datetime, timedelta

import duckdb
import pytest
from openpyxl import Workbook

from goodwill_pulse.gen import supro

FIRST, LAST = date(2025, 11, 20), date(2026, 1, 10)
STORES = ["Store01", "Store07", "Store24"]


@pytest.fixture
def con(tmp_path, monkeypatch):
    # a tiny week-sheet: Store01 and Store07 report 2025-12-07..13, Store24 is missing from it
    wb = Workbook()
    ws = wb.active
    ws.title = "Data Sheet"
    ws.append(["Store ID", "Store Code", "Store", "Date", "Weekday", "Net Sales", "Customers", "Avg Sale", "LY Net Sales", "LY Customers"])
    for sid in STORES[:2]:
        for i in range(7):
            d = datetime(2025, 12, 7) + timedelta(days=i)
            ws.append([sid, "X", "Goodwill X", d, d.strftime("%A").upper(), 1000.0 + i, 50 + i, 20.0, 900.0, 45])
    wb.save(tmp_path / "Weekly Sales 2025 Week 50.xlsx")
    monkeypatch.setattr(supro, "SUPRO_DIR", tmp_path)
    c = duckdb.connect()
    c.execute("CREATE TABLE dim_store (store_id VARCHAR, store_name VARCHAR)")
    c.executemany("INSERT INTO dim_store VALUES (?, ?)", [(s, s) for s in STORES])
    yield c
    c.close()


def test_shape_and_every_store_day(con):
    assert supro.COLS == ["date", "store", "sales", "customers", "returns", "units", "est"]
    rows, info = supro.supro_days(con, FIRST, LAST)
    days = (LAST - FIRST).days + 1
    assert len(rows) == days * len(STORES)
    assert {(r[0], r[1]) for r in rows} == {((FIRST + timedelta(days=i)).isoformat(), s) for i in range(days) for s in STORES}
    for r in rows:
        assert len(r) == len(supro.COLS)
        assert isinstance(r[3], int) and isinstance(r[5], int) and r[6] in (0, 1)
        assert r[2] == round(r[2], 2) and r[4] == round(r[4], 2)
    assert set(info) == {"2025-11", "2025-12", "2026-01"}
    assert set(supro.DEFINITIONS) >= {"sales", "customers", "returns", "units", "est", "dates"}


def test_deterministic(con):
    assert supro.supro_days(con, FIRST, LAST) == supro.supro_days(con, FIRST, LAST)


def test_real_sheet_used_and_est_matches_info(con):
    rows, info = supro.supro_days(con, FIRST, LAST)
    by = {(r[0], r[1]): r for r in rows}
    assert by[("2025-12-09", "Store07")][2:4] == [1002.0, 52] and by[("2025-12-09", "Store07")][6] == 0
    assert by[("2025-12-09", "Store24")][6] == 1                     # missing from the sheet -> estimated
    for m, mi in info.items():
        assert mi["estimated"] == any(r[6] for r in rows if r[0][:7] == m)
        assert isinstance(mi["note"], str) and mi["note"]
    assert info["2025-12"]["reported_days"] == 7 and info["2025-12"]["reports"] == ["Weekly Sales 2025 Week 50.xlsx"]
    assert info["2026-01"]["reports"] == []


def test_totals_sane(con):
    rows, _ = supro.supro_days(con, FIRST, LAST)
    est = [r for r in rows if r[6] and r[2] > 0]
    assert all(200 < r[2] < 15000 for r in est)
    s = sum(r[2] for r in est)
    assert 0.005 <= sum(r[4] for r in est) / s <= 0.015
    assert 15 <= s / sum(r[3] for r in est) <= 25                    # average basket
    closed = [r for r in rows if r[0] in ("2025-11-27", "2025-12-25")]
    assert closed and all(r[2] == 0 and r[3] == 0 and r[6] == 1 for r in closed)
    day = lambda d: sum(r[2] for r in rows if r[0] == d.isoformat() and r[6])  # noqa: E731
    sat, tue = date(2026, 1, 10), date(2026, 1, 6)
    assert day(sat) > day(tue)
