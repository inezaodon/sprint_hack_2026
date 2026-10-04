"""Paths and YAML config loading."""
import os
from functools import lru_cache
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = ROOT / "config"
MAPPINGS_DIR = CONFIG_DIR / "mappings"
FINANCE_LAYOUTS_DIR = CONFIG_DIR / "finance_layouts"
# GOODWILL_DATA_DIR relocates all data (e.g. to /tmp on Vercel, whose code directory is read-only)
DATA_DIR = Path(os.environ.get("GOODWILL_DATA_DIR") or ROOT / "data")
INBOX_DIR = DATA_DIR / "inbox"
ARCHIVE_DIR = DATA_DIR / "archive"
SAMPLES_DIR = DATA_DIR / "samples"
OUT_DIR = DATA_DIR / "out"
DB_PATH = DATA_DIR / "warehouse.duckdb"
FINANCE_DB_PATH = DATA_DIR / "sources" / "finance.duckdb"   # month-end inputs the close reads (sources/finance.py DDL)
CONVERTED_DIR = DATA_DIR / "converted"                     # spreadsheets made from emails, PDFs, OFX... (ingest/convert)
WEB_DIR = ROOT / "web"


@lru_cache
def channels_config() -> dict:
    return yaml.safe_load((CONFIG_DIR / "channels.yaml").read_text())


@lru_cache
def mappings() -> dict[str, dict]:
    """All report mappings, keyed by report_type."""
    out = {}
    for path in sorted(MAPPINGS_DIR.glob("*.yaml")):
        spec = yaml.safe_load(path.read_text())
        out[spec["report_type"]] = spec
    return out


def business_tz() -> str:
    return channels_config()["business_timezone"]


@lru_cache
def finance_layouts() -> dict[str, dict]:
    """Month-end input layouts (bank, FedEx, Jewelry Report, Goodwill Books statement), keyed by layout id."""
    out = {}
    for path in sorted(FINANCE_LAYOUTS_DIR.glob("*.yaml")):
        spec = yaml.safe_load(path.read_text())
        out[spec["layout"]] = spec
    return out
