"""Vercel entry point for the whole repo: the Goodwill E-Com Pulse app (goodwill-pulse/) as one Python function.

`/` always serves the full app (all tabs, web/app.html, built from goodwill-pulse/artifact/). The API and the
older pages (/pulse, /dashboard, /close) read prebuilt data from goodwill-pulse/data. Vercel's code directory is
read-only, so on a cold start that data is copied to /tmp; writes (uploads, demo reset, close approval) land there
and are per-instance.
"""
import os
import shutil
import sys
from pathlib import Path

APP = Path(__file__).resolve().parent.parent / "goodwill-pulse"
BUNDLED = APP / "data"
LIVE = Path("/tmp/goodwill-data")
RUNTIME_DATA = ["harmonized.duckdb", "warehouse.duckdb", "warehouse.duckdb.wal", "sources/finance.duckdb",
                "close_inputs", "samples"]

if not (LIVE / "harmonized.duckdb").exists():
    for rel in RUNTIME_DATA:
        src, dst = BUNDLED / rel, LIVE / rel
        if src.is_dir():
            shutil.copytree(src, dst, dirs_exist_ok=True)
        elif src.exists():
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
os.environ.setdefault("GOODWILL_DATA_DIR", str(LIVE))
sys.path.insert(0, str(APP))

from goodwill_pulse.api import app  # noqa: E402  (after GOODWILL_DATA_DIR is set)
