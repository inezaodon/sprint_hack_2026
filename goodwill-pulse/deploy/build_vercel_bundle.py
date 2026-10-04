"""Assemble a self-contained Vercel bundle in data/_tmp/vercel (gitignored), then deploy it with the Vercel CLI.

    .venv/bin/python deploy/build_vercel_bundle.py          # needs data/harmonized.duckdb (python -m goodwill_pulse.build)
    cd data/_tmp/vercel && npx vercel deploy --prod

The bundle ships prebuilt read-only data (harmonized model, finance DB, close inputs, sample reports) plus a fresh
Daily Pulse warehouse loaded with the sample history and Saturday's reports. The truth world and raw marketplace DBs
stay behind: they are only needed to rebuild, and Vercel functions are capped at 250 MB.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "_tmp" / "vercel"
IGNORE = shutil.ignore_patterns("__pycache__", "*.pyc", ".pytest_cache")

LOAD_WAREHOUSE = """
from goodwill_pulse import db
from goodwill_pulse.config import SAMPLES_DIR
from goodwill_pulse.ingest.pipeline import process_dir
con = db.connect()
for s in ("history", "demo"):
    process_dir(con, SAMPLES_DIR / s)
print(con.execute("SELECT count(*), max(business_date) FROM orders").fetchone())
con.execute("CHECKPOINT")
con.close()
"""


def build() -> Path:
    harmonized = ROOT / "data" / "harmonized.duckdb"
    if not harmonized.exists():
        raise SystemExit("data/harmonized.duckdb is missing: run .venv/bin/python -m goodwill_pulse.build first")
    shutil.rmtree(OUT, ignore_errors=True)
    OUT.mkdir(parents=True)

    for d in ("goodwill_pulse", "config", "sql", "web"):
        shutil.copytree(ROOT / d, OUT / d, ignore=IGNORE)
    shutil.copy2(ROOT / "requirements.txt", OUT / "requirements.txt")
    shutil.copytree(ROOT / "deploy" / "vercel", OUT, dirs_exist_ok=True, ignore=IGNORE)

    data = OUT / "data"
    (data / "sources").mkdir(parents=True)
    shutil.copy2(harmonized, data / "harmonized.duckdb")
    shutil.copy2(ROOT / "data" / "sources" / "finance.duckdb", data / "sources" / "finance.duckdb")
    for d in ("close_inputs", "samples"):
        if (ROOT / "data" / d).exists():
            shutil.copytree(ROOT / "data" / d, data / d)

    # Fresh warehouse in the bundle (the live dev server holds a lock on data/warehouse.duckdb)
    env = {**os.environ, "GOODWILL_DATA_DIR": str(data)}
    r = subprocess.run([sys.executable, "-c", LOAD_WAREHOUSE], cwd=OUT, env=env, capture_output=True, text=True)
    if r.returncode:
        raise SystemExit(f"warehouse load failed:\n{r.stderr}")
    print("warehouse orders, latest business date:", r.stdout.strip())
    shutil.rmtree(data / "archive", ignore_errors=True)   # raw-file archive from the load; not needed at runtime

    for p in OUT.rglob("*"):                     # the function may run as another user: make everything readable
        p.chmod(0o755 if p.is_dir() else 0o644)
    size = sum(p.stat().st_size for p in OUT.rglob("*") if p.is_file())
    print(f"bundle: {OUT} ({size / 1e6:.0f} MB before dependencies)")
    return OUT


if __name__ == "__main__":
    build()
