"""Close stage 03 "Enrich" (slide 40): supplier/store assignment, source labels, period metadata.

* ``store_from_sku``      - 'UP-03-000123' / 'GWM-03-123456' -> 'Store03' (fallback when a harmonized line has no store)
* ``sku_store_lookup``    - SKU -> store_id from harmonized fct_items + fct_order_lines (the "Co-Pivot" lookup table)
* ``fill_supplier``       - Jewelry Report: fill a blank Supplier by exact SKU lookup (slide 38: "Co-Pivot populates
                            Supplier"). Unmatched SKUs stay blank and are flagged ``supplier_source='unmatched'``.
* ``label``               - stamp each row with its close source and period label ('2026-09')
* ``sgw_period``          - ShopGoodwill period (1/2/3) for a day of month, from the rules config
"""
from __future__ import annotations

import calendar
import re
from datetime import date

import pandas as pd

_SKU_RE = re.compile(r"^\s*(UP|GWM)-(\d{2})-\d{6}\s*$", re.IGNORECASE)


def month_bounds(month: date) -> tuple[date, date]:
    """First and last calendar day of ``month`` (any day in the month is accepted)."""
    start = date(month.year, month.month, 1)
    end = date(month.year, month.month, calendar.monthrange(month.year, month.month)[1])
    return start, end


def add_months(month: date, n: int) -> date:
    y, m = divmod(month.month - 1 + n, 12)
    return date(month.year + y, m + 1, 1)


def period_label(month: date) -> str:
    return f"{month.year:04d}-{month.month:02d}"


def store_from_sku(sku: str | None) -> str | None:
    """Store code embedded in a well-formed Upright/Cash Monkey SKU, else None."""
    if not sku:
        return None
    m = _SKU_RE.match(str(sku))
    if not m:
        return None
    n = int(m.group(2))
    return f"Store{n:02d}" if 1 <= n <= 24 else None


def sku_store_lookup(hcon) -> dict[str, str]:
    """SKU -> store_id from the harmonized model (fct_items first, then fct_order_lines). Exact SKU match only."""
    rows = hcon.execute(
        """
        SELECT sku, store_id FROM (
            SELECT item_id AS sku, store_id, 1 AS pri FROM fct_items WHERE store_id IS NOT NULL
            UNION ALL
            SELECT sku, store_id, 2 AS pri FROM fct_order_lines WHERE store_id IS NOT NULL AND sku IS NOT NULL
        ) QUALIFY row_number() OVER (PARTITION BY sku ORDER BY pri, store_id) = 1
        """
    ).fetchall()
    return {sku: store for sku, store in rows}


def fill_supplier(df: pd.DataFrame, lookup: dict[str, str], sku_col: str = "sku",
                  supplier_col: str = "supplier") -> pd.DataFrame:
    """Co-Pivot step: fill blank Supplier from the SKU lookup.

    Adds ``store_id`` (final store) and ``supplier_source`` in {'report', 'co-pivot', 'unmatched'}.
    A supplier already on the report wins; no fuzzy matching (an unmatched SKU is an exception for a person)."""
    out = df.copy()
    stores, how = [], []
    for sku, sup in zip(out[sku_col], out[supplier_col]):
        if isinstance(sup, str) and sup.strip():
            stores.append(sup.strip())
            how.append("report")
        elif sku in lookup:
            stores.append(lookup[sku])
            how.append("co-pivot")
        else:
            stores.append(None)
            how.append("unmatched")
    out["store_id"] = pd.Series(stores, index=out.index, dtype=object)
    out["supplier_source"] = how
    return out


def fill_store(df: pd.DataFrame, sku_col: str = "sku", store_col: str = "store_id") -> pd.DataFrame:
    """Fill a NULL store on harmonized rows from the SKU prefix. Adds ``store_filled`` (bool)."""
    out = df.copy()
    missing = out[store_col].isna()
    out.loc[missing, store_col] = out.loc[missing, sku_col].map(store_from_sku)
    out["store_filled"] = missing & out[store_col].notna()
    return out


def label(df: pd.DataFrame, source: str, month: date) -> pd.DataFrame:
    out = df.copy()
    out["close_source"] = source
    out["close_period"] = period_label(month)
    return out


def sgw_period(day: int, periods: dict) -> int:
    """ShopGoodwill period number for a day of month, from ``rules.periods`` ({1: {days: [1, 10]}, ...})."""
    for p, spec in sorted(((int(k), v) for k, v in periods.items())):
        lo, hi = spec["days"]
        if lo <= day <= hi:
            return p
    raise ValueError(f"day {day} is in no ShopGoodwill period")
