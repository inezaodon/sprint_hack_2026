"""Data quality checks and reconciliation (Engineer 5).

    python -m goodwill_pulse.quality                     # run sql/checks/*.sql, write dq_results, exit 1 on error failures
    python -m goodwill_pulse.quality --reconcile-reports # also compare the Daily Pulse warehouse with harmonized truth

Each check is one file `sql/checks/<check_id>.sql`: a header (`-- severity: error|warning|info`,
`-- description: ...`) and a single SELECT returning the FAILING rows (zero rows = pass). Files starting with `_`
are helpers, not checks: `_prelude.sql` defines TEMP views (dq_native_orders, dq_native_lines, dq_dirty) that the
checks use. Checks run on one connection where harmonized.duckdb is the default catalog (attached READ_ONLY) and
the source DBs are ATTACHed READ_ONLY as amazon, ebay, shopgoodwill, goodwillfinds, goodwillbooks, ops.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
import tempfile
from datetime import date, datetime, timezone
from pathlib import Path

import duckdb

from .config import DATA_DIR, ROOT, channels_config

CHECKS_DIR = ROOT / "sql" / "checks"
SOURCES_SQL_DIR = ROOT / "sql" / "sources"
HARMONIZED_PATH = DATA_DIR / "harmonized.duckdb"
SOURCES_DIR = DATA_DIR / "sources"
SOURCE_DBS = ("amazon", "ebay", "shopgoodwill", "goodwillfinds", "goodwillbooks", "ops")
MAX_DETAIL_ROWS = 20


# ---------------------------------------------------------------------------------------------------------- checks
def load_checks(checks_dir: Path = CHECKS_DIR) -> list[dict]:
    """[{check_id, severity, description, sql, path}] for every sql/checks/*.sql not starting with '_'."""
    out = []
    for path in sorted(checks_dir.glob("*.sql")):
        if path.name.startswith("_"):
            continue
        text = path.read_text()
        sev = re.search(r"^--\s*severity:\s*(\w+)", text, re.M)
        desc = re.search(r"^--\s*description:\s*(.+)$", text, re.M)
        severity = sev.group(1).lower() if sev else "error"
        if severity not in ("error", "warning", "info"):
            raise ValueError(f"{path.name}: bad severity {severity!r}")
        out.append({"check_id": path.stem, "severity": severity,
                    "description": desc.group(1).strip() if desc else "", "sql": text, "path": path})
    return out


def _attach_sources(con: duckdb.DuckDBPyConnection, sources_dir: Path) -> list[str]:
    """ATTACH each source DB read-only; a missing one becomes an empty in-memory stand-in built from its DDL
    (so checks still run and reconciliation shows the gap). Returns the names of missing sources."""
    missing = []
    main = con.execute("SELECT current_database()").fetchone()[0]
    for name in SOURCE_DBS:
        path = Path(sources_dir) / f"{name}.duckdb"
        if path.exists():
            con.execute(f"ATTACH '{path}' AS {name} (READ_ONLY)")
        else:
            missing.append(name)
            con.execute(f"ATTACH ':memory:' AS {name}")
            con.execute(f"USE {name}")
            con.execute((SOURCES_SQL_DIR / f"{name}.sql").read_text())
            con.execute(f"USE {main}")
    # one view over every source's _dirty_data (sources without one contribute nothing)
    parts = []
    for name in SOURCE_DBS:
        has = con.execute("SELECT count(*) FROM duckdb_tables() WHERE database_name = ? AND table_name = '_dirty_data'",
                          [name]).fetchone()[0]
        if has:
            parts.append(f"SELECT '{name}' AS source_db, CAST(table_name AS VARCHAR) AS table_name, "
                         f"CAST(native_key AS VARCHAR) AS native_key, CAST(kind AS VARCHAR) AS kind, "
                         f"CAST(note AS VARCHAR) AS note FROM {name}._dirty_data")
    if not parts:
        parts.append("SELECT NULL::VARCHAR AS source_db, NULL::VARCHAR AS table_name, NULL::VARCHAR AS native_key, "
                     "NULL::VARCHAR AS kind, NULL::VARCHAR AS note WHERE false")
    con.execute("CREATE OR REPLACE TEMP VIEW dq_dirty_raw AS " + " UNION ALL ".join(parts))
    return missing


def check_connection(harmonized_path: Path, sources_dir: Path) -> tuple[duckdb.DuckDBPyConnection, list[str]]:
    """In-memory connection with harmonized (READ_ONLY, default catalog) + sources attached and the prelude run."""
    con = duckdb.connect()
    con.execute("SET TimeZone = 'UTC'")
    con.execute(f"ATTACH '{Path(harmonized_path)}' AS harmonized (READ_ONLY)")
    con.execute("USE harmonized")
    missing = _attach_sources(con, Path(sources_dir))
    for helper in sorted(CHECKS_DIR.glob("_*.sql")):
        con.execute(helper.read_text())
    return con, missing


def _jsonable(v):
    if isinstance(v, (datetime, date)):
        return v.isoformat()
    if isinstance(v, (int, float, str, bool)) or v is None:
        return v
    return str(v)   # Decimal, UUID, ...


def _run_one(con: duckdb.DuckDBPyConnection, check: dict) -> dict:
    sql = re.sub(r";\s*$", "", check["sql"].strip())
    try:
        cur = con.execute(f"SELECT * FROM ({sql}) AS failing")
        cols = [d[0] for d in cur.description]
        rows = cur.fetchall()
        status, n = ("pass" if not rows else "fail"), len(rows)
        detail = [{c: _jsonable(v) for c, v in zip(cols, r)} for r in rows[:MAX_DETAIL_ROWS]]
        crashed = False
    except duckdb.Error as e:   # a check that cannot run is a FAIL (never a silent pass); detail carries the error
        status, n, detail, crashed = "fail", None, [{"error": str(e).splitlines()[0][:500]}], True
    return {"check_id": check["check_id"], "severity": check["severity"], "status": status,
            "failing_rows": n, "detail": detail, "description": check["description"], "crashed": crashed}


def _write_results(harmonized_path: Path, results: list[dict], run_at: datetime) -> None:
    """Replace dq_results in harmonized.duckdb without ever holding the live file open read-write:
    copy -> write into the copy -> os.replace (readers never see half a DB)."""
    harmonized_path = Path(harmonized_path)
    fd, tmp = tempfile.mkstemp(prefix=harmonized_path.name + ".", suffix=".dq.tmp", dir=harmonized_path.parent)
    os.close(fd)
    try:
        shutil.copyfile(harmonized_path, tmp)
        con = duckdb.connect(tmp)
        try:
            con.execute("""CREATE TABLE IF NOT EXISTS dq_results (check_id VARCHAR, run_at TIMESTAMPTZ, severity VARCHAR,
                           status VARCHAR, failing_rows BIGINT, detail JSON, description VARCHAR)""")
            con.execute("DELETE FROM dq_results")
            con.executemany("INSERT INTO dq_results VALUES (?, ?, ?, ?, ?, ?, ?)", [
                [r["check_id"], run_at, r["severity"], r["status"], r["failing_rows"],
                 json.dumps(r["detail"]), r["description"]] for r in results])
            con.execute("CHECKPOINT")
        finally:
            con.close()
        os.replace(tmp, harmonized_path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def run_checks(harmonized_path: Path | str = HARMONIZED_PATH, sources_dir: Path | str = SOURCES_DIR,
               write: bool = True) -> list[dict]:
    """Run every check; returns [{check_id, severity, status ('pass'|'fail'), failing_rows (None if the check SQL
    itself crashed; then crashed=True and detail=[{error}]), detail (<= 20 example rows), description, run_at}] and (if write) replaces the dq_results table in harmonized.duckdb."""
    harmonized_path = Path(harmonized_path)
    if not harmonized_path.exists():
        raise FileNotFoundError(f"{harmonized_path} not found; run `python -m goodwill_pulse.harmonize` first")
    run_at = datetime.now(timezone.utc)
    con, missing = check_connection(harmonized_path, Path(sources_dir))
    try:
        results = [_run_one(con, c) for c in load_checks()]
    finally:
        con.close()
    for r in results:
        r["run_at"] = run_at.isoformat()
        if missing:
            r["missing_sources"] = missing
    if write:
        _write_results(harmonized_path, results, run_at)
    return results


def has_blocking_failure(results: list[dict]) -> bool:
    return any(r["severity"] == "error" and r["status"] != "pass" for r in results)


# --------------------------------------------------------------------------------------- report reconciliation
def _open_warehouse(warehouse_path: Path) -> tuple[duckdb.DuckDBPyConnection, str | None]:
    """Open warehouse.duckdb READ_ONLY; if the live server's write lock blocks that, read a temp copy."""
    try:
        return duckdb.connect(str(warehouse_path), read_only=True), None
    except duckdb.Error:
        fd, tmp = tempfile.mkstemp(prefix=warehouse_path.name + ".", suffix=".recon.tmp", dir=warehouse_path.parent)
        os.close(fd)
        shutil.copyfile(warehouse_path, tmp)
        wal = Path(str(warehouse_path) + ".wal")
        if wal.exists():
            shutil.copyfile(wal, tmp + ".wal")
        return duckdb.connect(tmp, read_only=True), tmp


def reconcile_reports(harmonized_path: Path | str = HARMONIZED_PATH,
                      warehouse_path: Path | str = DATA_DIR / "warehouse.duckdb",
                      tolerance: float = 0.01) -> list[dict]:
    """Daily Pulse (Upright/Cash Monkey report files) vs harmonized marketplace truth, per business_date x pulse row,
    over the business dates the warehouse has data for. Revenue basis = channels.yaml revenue_basis (subtotal).

    Returns [{business_date, pulse_row, report_orders, harmonized_orders, orders_diff, report_revenue,
              harmonized_revenue, revenue_diff, status ('match'|'mismatch'|'missing_report'|'missing_harmonized'),
              double_counted_orders}]
    double_counted_orders = marketplace orders present in BOTH the Upright and the Cash Monkey report for that row/day
    (the eBay shared-account risk)."""
    cfg = channels_config()
    basis = cfg["revenue_basis"]
    row_of = {ch: r["label"] for r in cfg["pulse_rows"] for ch in r["channels"]}
    wcon, tmp = _open_warehouse(Path(warehouse_path))
    try:
        rep = wcon.execute(f"""
            SELECT business_date, channel, count(*), sum({basis})::DOUBLE FROM orders GROUP BY ALL""").fetchall()
        dbl = wcon.execute("""
            SELECT business_date, channel, count(*) FROM (
                SELECT business_date, channel, channel_order_id FROM orders GROUP BY ALL
                HAVING count(DISTINCT source_system) > 1) GROUP BY ALL""").fetchall()
    finally:
        wcon.close()
        if tmp:
            for p in (tmp, tmp + ".wal"):
                if os.path.exists(p):
                    os.unlink(p)
    if not rep:
        return []
    lo, hi = min(r[0] for r in rep), max(r[0] for r in rep)
    hcon = duckdb.connect(str(harmonized_path), read_only=True)
    try:
        harm = hcon.execute(f"""
            SELECT business_date, channel, count(*), sum({basis})::DOUBLE FROM fct_orders
            WHERE business_date BETWEEN ? AND ? GROUP BY ALL""", [lo, hi]).fetchall()
    finally:
        hcon.close()

    agg: dict[tuple, dict] = {}

    def add(rows, prefix):
        for d, ch, n, rev in rows:
            k = (d, row_of.get(ch, row_of.get("other", ch)))
            a = agg.setdefault(k, {"report_orders": 0, "report_revenue": 0.0, "harmonized_orders": 0,
                                   "harmonized_revenue": 0.0, "double_counted_orders": 0})
            a[f"{prefix}_orders"] += n
            a[f"{prefix}_revenue"] += rev or 0.0

    add(rep, "report")
    add(harm, "harmonized")
    for d, ch, n in dbl:
        k = (d, row_of.get(ch, ch))
        if k in agg:
            agg[k]["double_counted_orders"] += n
    report_dates = {r[0] for r in rep}
    out = []
    for (d, label), a in sorted(agg.items()):
        if d not in report_dates:
            continue
        rd = round(a["report_revenue"] - a["harmonized_revenue"], 2)
        od = a["report_orders"] - a["harmonized_orders"]
        if a["report_orders"] == 0:
            status = "missing_report"
        elif a["harmonized_orders"] == 0:
            status = "missing_harmonized"
        elif od == 0 and abs(rd) <= tolerance and a["double_counted_orders"] == 0:
            status = "match"
        else:
            status = "mismatch"
        out.append({"business_date": d.isoformat(), "pulse_row": label,
                    "report_orders": a["report_orders"], "harmonized_orders": a["harmonized_orders"], "orders_diff": od,
                    "report_revenue": round(a["report_revenue"], 2), "harmonized_revenue": round(a["harmonized_revenue"], 2),
                    "revenue_diff": rd, "status": status, "double_counted_orders": a["double_counted_orders"]})
    return out


# ------------------------------------------------------------------------------------------------------------ CLI
def format_table(results: list[dict]) -> str:
    lines = [f"{'check_id':<28} {'severity':<8} {'status':<6} {'failing':>8}  description",
             "-" * 110]
    order = {"error": 0, "warning": 1, "info": 2}
    for r in sorted(results, key=lambda r: (r["status"] == "pass", order[r["severity"]], r["check_id"])):
        n = "-" if r["failing_rows"] is None else str(r["failing_rows"])
        lines.append(f"{r['check_id']:<28} {r['severity']:<8} {r['status'].upper():<6} {n:>8}  {r['description'][:70]}")
        if r.get("crashed"):
            lines.append(f"{'':<46}  ! {r['detail'][0]['error'][:120]}")
    fails = [r for r in results if r["status"] != "pass"]
    lines.append("-" * 110)
    lines.append(f"{len(results)} checks: {len(results) - len(fails)} pass, {len(fails)} fail/error "
                 f"({sum(1 for r in fails if r['severity'] == 'error')} blocking)")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--harmonized", type=Path, default=HARMONIZED_PATH)
    ap.add_argument("--sources", type=Path, default=SOURCES_DIR)
    ap.add_argument("--no-write", action="store_true", help="don't write dq_results")
    ap.add_argument("--details", action="store_true", help="print example failing rows")
    ap.add_argument("--reconcile-reports", action="store_true")
    ap.add_argument("--warehouse", type=Path, default=DATA_DIR / "warehouse.duckdb")
    args = ap.parse_args(argv)

    results = run_checks(args.harmonized, args.sources, write=not args.no_write)
    if results and results[0].get("missing_sources"):
        print(f"WARNING: source DBs missing (empty stand-ins used): {', '.join(results[0]['missing_sources'])}")
    print(format_table(results))
    if args.details:
        for r in results:
            if r["status"] != "pass":
                print(f"\n== {r['check_id']} ({r['failing_rows']} rows, showing {len(r['detail'])})")
                for row in r["detail"][:5]:
                    print("  ", json.dumps(row, default=str))
    if args.reconcile_reports:
        rec = reconcile_reports(args.harmonized, args.warehouse)
        bad = [r for r in rec if r["status"] != "match"]
        print(f"\nDaily Pulse reports vs harmonized: {len(rec)} date x row cells, {len(rec) - len(bad)} match, {len(bad)} differ")
        for r in bad[:25]:
            print(f"  {r['business_date']} {r['pulse_row']:<26} {r['status']:<18} orders {r['report_orders']:>4} vs "
                  f"{r['harmonized_orders']:>4}  revenue {r['report_revenue']:>10,.2f} vs {r['harmonized_revenue']:>10,.2f}"
                  f"  double-counted {r['double_counted_orders']}")
    return 1 if has_blocking_failure(results) else 0


if __name__ == "__main__":
    sys.exit(main())
