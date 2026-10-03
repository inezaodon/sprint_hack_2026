"""data/sources/ops.duckdb: internal operations (Upright back office, timekeeping, finance inputs).

    .venv/bin/python -m goodwill_pulse.sources.ops

Created by executing sql/sources/ops.sql, then filled straight from the truth world:
  stores <- stores; upright_items <- items; operational_productivity <- productivity; employees <- employees;
  timeclock <- labor; budget <- budget; monthly_inputs <- monthly_inputs.
No dirty data here (internal system of record).
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path

import duckdb

from ..config import DATA_DIR, ROOT

DDL_PATH = ROOT / "sql" / "sources" / "ops.sql"
DEFAULT_TRUTH = DATA_DIR / "sources" / "_truth.duckdb"
DEFAULT_OUT = DATA_DIR / "sources" / "ops.duckdb"

COPIES = {   # ops table -> SELECT over the attached truth DB
    "stores": "SELECT store_id, store_name, city, ai_flagging, ecom_eye FROM truth.stores ORDER BY store_id",
    "upright_items": """SELECT item_id, store_id, line_of_business, category, title, identified_at, flagged_by,
                               manifest_id, manifested_at, posted_at, poster_id, list_minutes
                        FROM truth.items ORDER BY identified_at, item_id""",
    "operational_productivity": """SELECT employee_id, work_date, accepted, rejected, photographed, posted
                                   FROM truth.productivity ORDER BY work_date, employee_id""",
    "employees": "SELECT employee_id, role, hourly_rate, hired_on FROM truth.employees ORDER BY employee_id",
    "timeclock": "SELECT employee_id, work_date, hours FROM truth.labor ORDER BY work_date, employee_id",
    "budget": "SELECT month, channel, revenue_budget FROM truth.budget ORDER BY month, channel",
    "monthly_inputs": """SELECT month, overhead_allocation, store_retail_revenue
                         FROM truth.monthly_inputs ORDER BY month""",
}


def build(truth_path: Path = DEFAULT_TRUTH, out_path: Path = DEFAULT_OUT, seed: int = 7) -> dict[str, int]:
    truth_path, out_path = Path(truth_path), Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = out_path.with_name(out_path.name + ".tmp")
    for p in (tmp, Path(str(tmp) + ".wal")):
        if p.exists():
            p.unlink()
    con = duckdb.connect(str(tmp))
    counts: dict[str, int] = {}
    try:
        con.execute("SET TimeZone = 'UTC'")
        con.execute(DDL_PATH.read_text())
        con.execute(f"ATTACH '{truth_path}' AS truth (READ_ONLY)")
        for table, sql in COPIES.items():
            con.execute(f"INSERT INTO {table} {sql}")
            counts[table] = con.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
        con.execute("DETACH truth")
        con.execute("CHECKPOINT")
    finally:
        con.close()
    os.replace(tmp, out_path)
    return counts


def main() -> None:
    p = argparse.ArgumentParser(description="Build data/sources/ops.duckdb from the truth world")
    p.add_argument("--truth", type=Path, default=DEFAULT_TRUTH)
    p.add_argument("--out", type=Path, default=DEFAULT_OUT)
    p.add_argument("--seed", type=int, default=7)
    a = p.parse_args()
    for k, v in build(a.truth, a.out, a.seed).items():
        print(f"{k:26s} {v:>9,d}")


if __name__ == "__main__":
    main()
