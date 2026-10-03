"""Build data/harmonized.duckdb from the native marketplace + ops DBs.

All transformation logic lives in sql/harmonize/NN_*.sql (pure DuckDB SQL). This module only:
  1. creates a fresh DB at <out_path>.tmp,
  2. ATTACHes each source DB READ_ONLY under its name (amazon, ebay, shopgoodwill, goodwillfinds, goodwillbooks, ops),
  3. runs sql/harmonized/schema.sql, then every sql/harmonize/*.sql in numeric order, statement by statement,
  4. records the run in harmonize_runs (90_lineage.sql) and os.replace()s the file onto out_path,
so readers never see a half-built DB.

    python -m goodwill_pulse.harmonize [--sources data/sources] [--out data/harmonized.duckdb]
"""
from __future__ import annotations

import argparse
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path

import duckdb

from .config import DATA_DIR, ROOT

SOURCES = ("amazon", "ebay", "shopgoodwill", "goodwillfinds", "goodwillbooks", "ops")
SCHEMA_SQL = ROOT / "sql" / "harmonized" / "schema.sql"
HARMONIZE_DIR = ROOT / "sql" / "harmonize"
DEFAULT_SOURCES_DIR = DATA_DIR / "sources"
DEFAULT_OUT = DATA_DIR / "harmonized.duckdb"


class HarmonizeError(RuntimeError):
    """A SQL file failed; the message names the file, statement number, line and SQL."""


def sql_files(directory: Path = HARMONIZE_DIR) -> list[Path]:
    # numeric prefix order ('05_' < '10_' < '20_' ...), name as tie-breaker
    return sorted(directory.glob("[0-9]*_*.sql"), key=lambda p: (int(p.name.split("_", 1)[0]), p.name))


def _run_file(con: duckdb.DuckDBPyConnection, path: Path) -> None:
    text = path.read_text()
    try:
        statements = [s.query for s in con.extract_statements(text)]
    except duckdb.Error as e:
        raise HarmonizeError(f"{path.relative_to(ROOT)}: could not parse SQL: {e}") from e
    cursor = 0
    for i, stmt in enumerate(statements, 1):
        body = stmt.strip()
        # line number of the statement in the file (for the error message)
        first_code = next((ln for ln in body.splitlines() if ln.strip() and not ln.strip().startswith("--")), body)
        pos = text.find(first_code.strip(), cursor)
        line = text.count("\n", 0, pos) + 1 if pos >= 0 else "?"
        if pos >= 0:
            cursor = pos
        try:
            con.execute(body)
        except duckdb.Error as e:
            snippet = first_code.strip()[:160]
            raise HarmonizeError(
                f"{path.relative_to(ROOT) if path.is_relative_to(ROOT) else path}: statement {i} "
                f"(line {line}) failed: {type(e).__name__}: {e}\n  SQL: {snippet} ..."
            ) from e


def build(sources_dir: Path = DEFAULT_SOURCES_DIR, out_path: Path = DEFAULT_OUT) -> dict:
    """Build the harmonized DB. Returns {'run_id', 'seconds', 'source_row_counts', 'output_row_counts'}."""
    sources_dir, out_path = Path(sources_dir), Path(out_path)
    missing = [f"{n}.duckdb" for n in SOURCES if not (sources_dir / f"{n}.duckdb").exists()]
    if missing:
        raise FileNotFoundError(f"harmonize: missing source DB(s) in {sources_dir}: {', '.join(missing)}")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = out_path.with_name(out_path.name + ".tmp")
    for p in (tmp, tmp.with_name(tmp.name + ".wal")):
        if p.exists():
            p.unlink()

    t0 = time.perf_counter()
    started = datetime.now(timezone.utc)
    run_id = "hr-" + started.strftime("%Y%m%dT%H%M%S.%fZ")
    con = duckdb.connect(str(tmp))
    try:
        con.execute("SET TimeZone = 'UTC'")  # no implicit cast may depend on the build machine's zone
        for name in SOURCES:
            path = str((sources_dir / f"{name}.duckdb").resolve()).replace("'", "''")
            con.execute(f"ATTACH '{path}' AS {name} (READ_ONLY)")
        _run_file(con, SCHEMA_SQL)
        con.execute("CREATE TEMP TABLE _run_ctx AS SELECT ?::VARCHAR AS run_id, ?::TIMESTAMPTZ AS started_at",
                    [run_id, started])
        for f in sql_files():
            _run_file(con, f)
        src, out = con.execute(
            "SELECT source_row_counts, output_row_counts FROM harmonize_runs WHERE run_id = ?", [run_id]
        ).fetchone()
        for name in SOURCES:
            con.execute(f"DETACH {name}")
        con.execute("CHECKPOINT")
    except Exception:
        con.close()
        tmp.unlink(missing_ok=True)
        raise
    con.close()
    os.replace(tmp, out_path)
    return {
        "run_id": run_id,
        "seconds": round(time.perf_counter() - t0, 2),
        "out_path": str(out_path),
        "source_row_counts": json.loads(src),
        "output_row_counts": json.loads(out),
    }


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description="Harmonize marketplace source DBs into data/harmonized.duckdb")
    ap.add_argument("--sources", type=Path, default=DEFAULT_SOURCES_DIR)
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    a = ap.parse_args(argv)
    try:
        res = build(a.sources, a.out)
    except (HarmonizeError, FileNotFoundError) as e:
        raise SystemExit(f"harmonize failed: {e}")
    print(f"harmonized -> {res['out_path']} in {res['seconds']}s (run {res['run_id']})")
    for k, v in res["output_row_counts"].items():
        print(f"  {k:<20} {v:>9,}")


if __name__ == "__main__":
    main()
