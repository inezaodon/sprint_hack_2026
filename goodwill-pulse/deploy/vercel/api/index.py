"""Vercel entry point: the whole FastAPI app as one Python serverless function.

Vercel's code directory is read-only, so on a cold start the bundled data (prebuilt warehouse, harmonized model,
finance DB, close inputs, sample reports) is copied to /tmp and the app is pointed at it. Writes (uploads, demo
reset, close exports) then land in /tmp: they work, but are per-instance and disappear when the instance is recycled.
"""
import os
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
BUNDLED = HERE / "data"
LIVE = Path("/tmp/goodwill-data")

if not (LIVE / "harmonized.duckdb").exists():
    shutil.copytree(BUNDLED, LIVE, dirs_exist_ok=True)
os.environ.setdefault("GOODWILL_DATA_DIR", str(LIVE))
sys.path.insert(0, str(HERE))

from goodwill_pulse.api import app  # noqa: E402  (must come after GOODWILL_DATA_DIR is set)
