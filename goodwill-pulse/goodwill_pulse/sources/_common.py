"""Shared helpers for building native marketplace source DBs from the truth world.

Owned by Engineer 2. Other source builders may import from here; the public names below are kept stable:

    ROOT, SQL_SOURCES_DIR, SOURCES_DIR, TRUTH_PATH, PACIFIC, EASTERN, UTC
    atomic_duckdb(out_path)                -> context manager yielding a writable connection on <out>.tmp
    create_from_ddl(con, ddl_path)         -> executes a DDL file
    open_truth(truth_path)                 -> read-only connection to _truth.duckdb (TimeZone = UTC)
    fetch_dicts(con, sql, params=None)     -> list[dict]
    insert_rows(con, table, rows)          -> bulk insert list[dict] (values are cast by DuckDB to the column types)
    record_dirty(con, table, key, kind, note)
    table_counts(con)                      -> {table: rows}
    money(x) / money_text(x)               -> Decimal quantized to cents / "45.00"
    allocate(total, weights)               -> list[Decimal] summing exactly to total
    iso_utc(dt, ms=False)                  -> '2026-09-30T23:41:07Z' / '2026-09-30T23:41:07.000Z'
    pacific_amazon(dt)                     -> 'Sep 30, 2026 4:41:07 PM PDT'
    pacific_text(dt)                       -> '2026-09-30 16:41:07 PDT'
    pacific_naive(dt)                      -> naive datetime in America/Los_Angeles wall time
    to_utc(dt)                             -> tz-aware UTC datetime (naive input is treated as UTC)
    PeriodIndex(periods)                   -> .find(day) -> key of the [start, end] date period containing day
    IdMaker(rng)                           -> .digits(n, prefix='') unique random digit strings

Truth timestamps are TIMESTAMPTZ. The venv has no pytz, so never fetch TIMESTAMPTZ into Python directly:
select `timezone('UTC', col)` (a naive UTC TIMESTAMP) and pass that to the formatters here.
"""
from __future__ import annotations

import os
from contextlib import contextmanager
from datetime import datetime, timezone
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
import bisect
import random
from datetime import date
from typing import Iterable, Iterator, Sequence
from zoneinfo import ZoneInfo

import duckdb
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
SQL_SOURCES_DIR = ROOT / "sql" / "sources"
SOURCES_DIR = ROOT / "data" / "sources"
TRUTH_PATH = SOURCES_DIR / "_truth.duckdb"

UTC = timezone.utc
PACIFIC = ZoneInfo("America/Los_Angeles")
EASTERN = ZoneInfo("America/New_York")
CENT = Decimal("0.01")

DIRTY_DDL = """
CREATE TABLE IF NOT EXISTS _dirty_data (
    table_name VARCHAR NOT NULL,
    native_key VARCHAR NOT NULL,
    kind       VARCHAR NOT NULL,
    note       VARCHAR
)"""


# ---------------------------------------------------------------- DB plumbing
@contextmanager
def atomic_duckdb(out_path: Path | str) -> Iterator[duckdb.DuckDBPyConnection]:
    """Build a DuckDB file at <out>.tmp and os.replace it onto <out> only if the block succeeds."""
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = out_path.with_name(out_path.name + ".tmp")
    for p in (tmp, Path(str(tmp) + ".wal")):
        if p.exists():
            p.unlink()
    con = duckdb.connect(str(tmp))
    try:
        yield con
        con.execute("CHECKPOINT")
        con.close()
    except BaseException:
        con.close()
        for p in (tmp, Path(str(tmp) + ".wal")):
            if p.exists():
                p.unlink()
        raise
    os.replace(tmp, out_path)


def create_from_ddl(con: duckdb.DuckDBPyConnection, ddl_path: Path | str) -> None:
    con.execute(Path(ddl_path).read_text())


def open_truth(truth_path: Path | str) -> duckdb.DuckDBPyConnection:
    truth_path = Path(truth_path)
    if not truth_path.exists():
        raise FileNotFoundError(f"truth DB not found: {truth_path} (run python -m goodwill_pulse.gen.truth)")
    con = duckdb.connect(str(truth_path), read_only=True)
    con.execute("SET TimeZone = 'UTC'")
    return con


def fetch_dicts(con: duckdb.DuckDBPyConnection, sql: str, params: Sequence | None = None) -> list[dict]:
    cur = con.execute(sql, params or [])
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


def insert_rows(con: duckdb.DuckDBPyConnection, table: str, rows: Iterable[dict]) -> int:
    """Bulk-insert dicts. Values go in as text/ints and DuckDB casts them to the column types (exact for DECIMAL)."""
    rows = list(rows)
    if not rows:
        return 0
    cols = list(rows[0].keys())
    data = {c: [_to_insertable(r[c]) for r in rows] for c in cols}
    df = pd.DataFrame(data, columns=cols).astype(object)
    df = df.where(pd.notna(df), None)
    con.register("_ins_df", df)
    try:
        collist = ", ".join(f'"{c}"' for c in cols)
        con.execute(f'INSERT INTO "{table}" ({collist}) SELECT {collist} FROM _ins_df')
    finally:
        con.unregister("_ins_df")
    return len(rows)


def _to_insertable(v):
    if isinstance(v, Decimal):
        return str(v)
    if isinstance(v, datetime):
        return v.isoformat(sep=" ")
    if v is None or isinstance(v, (str, int)):
        return v
    return str(v)


def record_dirty(con: duckdb.DuckDBPyConnection, table: str, key: str, kind: str, note: str) -> None:
    con.execute(DIRTY_DDL)
    con.execute("INSERT INTO _dirty_data VALUES (?, ?, ?, ?)", [table, str(key), kind, note])


def table_counts(con: duckdb.DuckDBPyConnection) -> dict[str, int]:
    names = [r[0] for r in con.execute(
        "SELECT table_name FROM duckdb_tables() WHERE database_name = current_database() ORDER BY table_name"
    ).fetchall()]
    return {n: con.execute(f'SELECT count(*) FROM "{n}"').fetchone()[0] for n in names}


# ---------------------------------------------------------------- money
def money(x) -> Decimal:
    if x is None:
        return Decimal("0.00")
    return Decimal(str(x)).quantize(CENT, rounding=ROUND_HALF_UP)


def money_text(x) -> str | None:
    """eBay-style money string: "45.00" (None stays None)."""
    if x is None:
        return None
    return f"{money(x):.2f}"


def allocate(total, weights: Sequence) -> list[Decimal]:
    """Split `total` across weights in cents; the parts sum exactly to total (last part takes the remainder)."""
    total = money(total)
    n = len(weights)
    if n == 0:
        return []
    w = [Decimal(str(x or 0)) for x in weights]
    s = sum(w)
    if s == 0:
        w, s = [Decimal(1)] * n, Decimal(n)
    parts = [money(total * wi / s) for wi in w[:-1]]
    parts.append(total - sum(parts, Decimal("0.00")))
    return parts


# ---------------------------------------------------------------- time
def to_utc(dt: datetime) -> datetime:
    return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt.astimezone(UTC)


def iso_utc(dt: datetime | None, ms: bool = False) -> str | None:
    if dt is None:
        return None
    u = to_utc(dt)
    if ms:
        return u.strftime("%Y-%m-%dT%H:%M:%S.") + f"{u.microsecond // 1000:03d}Z"
    return u.strftime("%Y-%m-%dT%H:%M:%SZ")


def pacific_naive(dt: datetime) -> datetime:
    return to_utc(dt).astimezone(PACIFIC).replace(tzinfo=None)


def pacific_amazon(dt: datetime | None) -> str | None:
    """Amazon Date Range report time: 'Sep 30, 2026 4:41:07 PM PDT' (no zero padding on day/hour)."""
    if dt is None:
        return None
    p = to_utc(dt).astimezone(PACIFIC)
    hour12 = p.hour % 12 or 12
    ampm = "AM" if p.hour < 12 else "PM"
    return f"{p:%b} {p.day}, {p.year} {hour12}:{p:%M:%S} {ampm} {p.tzname()}"


def pacific_text(dt: datetime | None) -> str | None:
    """Amazon listings open_date: '2026-09-30 16:41:07 PDT'."""
    if dt is None:
        return None
    p = to_utc(dt).astimezone(PACIFIC)
    return f"{p:%Y-%m-%d %H:%M:%S} {p.tzname()}"


def local_date(dt: datetime, tz=EASTERN) -> date:
    return to_utc(dt).astimezone(tz).date()


# ---------------------------------------------------------------- periods / ids
class PeriodIndex:
    """Find which payout/settlement period (inclusive start..end dates) a day falls in."""

    def __init__(self, periods: Iterable[tuple[date, date, object]]):
        self._p = sorted(periods, key=lambda p: (p[0], p[1]))
        self._starts = [p[0] for p in self._p]

    def find(self, day: date):
        i = bisect.bisect_right(self._starts, day) - 1
        while i >= 0:
            start, end, key = self._p[i]
            if start <= day <= end:
                return key
            if end < day:
                return None
            i -= 1
        return None


class IdMaker:
    """Deterministic, collision-free random digit strings (one shared namespace per instance)."""

    def __init__(self, rng: random.Random):
        self.rng = rng
        self.seen: set[str] = set()

    def digits(self, n: int, prefix: str = "") -> str:
        while True:
            s = prefix + "".join(str(self.rng.randrange(10)) for _ in range(n))
            if s not in self.seen:
                self.seen.add(s)
                return s


# ---------------------------------------------------------------- truth reader
def load_truth_channel(con: duckdb.DuckDBPyConnection, channel: str) -> dict[str, list[dict]]:
    """Everything a marketplace builder needs for one channel, timestamps as naive UTC, sorted deterministically.

    Keys: orders, lines, refunds, payouts, listings, labels_billed_by_carrier (set of order_ids),
    shipping_charges (rows for this channel's orders, any carrier), now (max paid_at, naive UTC).
    `lines` rows carry item fields (category, title, isbn) and the listing's native_listing_id.
    """
    q = lambda sql: fetch_dicts(con, sql, [channel])  # noqa: E731
    orders = q("""
        SELECT o.order_id, o.channel, o.tool, o.marketplace_order_id, o.upright_order_id, o.buyer_id,
               timezone('UTC', o.paid_at) AS paid_at, o.item_count, o.subtotal, o.shipping_charged, o.handling,
               o.tax, o.marketplace_fee, o.payment_fee, o.shipping_label_cost, o.total, o.payment_type,
               b.native_buyer_ref, b.state AS buyer_state
        FROM orders o LEFT JOIN buyers b USING (buyer_id)
        WHERE o.channel = ? ORDER BY o.paid_at, o.order_id""")
    lines = q("""
        SELECT l.order_id, l.line_no, l.item_id, l.listing_id, l.quantity, l.sale_price, l.fee_alloc,
               i.category, i.title, i.isbn, i.store_id, li.native_listing_id
        FROM order_lines l JOIN orders o USING (order_id)
        LEFT JOIN items i USING (item_id) LEFT JOIN listings li ON li.listing_id = l.listing_id
        WHERE o.channel = ? ORDER BY l.order_id, l.line_no""")
    refunds = q("""
        SELECT r.refund_id, r.order_id, timezone('UTC', r.refunded_at) AS refunded_at, r.amount, r.reason
        FROM refunds r JOIN orders o USING (order_id)
        WHERE o.channel = ? ORDER BY r.refunded_at, r.refund_id""")
    payouts = q("""
        SELECT payout_id, period_start::DATE AS period_start, period_end::DATE AS period_end, paid_on::DATE AS paid_on,
               gross, fees, refunds, net
        FROM payouts WHERE channel = ? ORDER BY period_start, payout_id""")
    listings = q("""
        SELECT li.listing_id, li.item_id, li.tool, li.native_listing_id, timezone('UTC', li.listed_at) AS listed_at,
               timezone('UTC', li.ended_at) AS ended_at, li.status, li.price, li.relist_of,
               p.native_listing_id AS relist_of_native, i.category, i.title, i.isbn
        FROM listings li LEFT JOIN listings p ON p.listing_id = li.relist_of
        LEFT JOIN items i ON i.item_id = li.item_id
        WHERE li.channel = ? ORDER BY li.listed_at, li.listing_id""")
    carrier = {r["order_id"] for r in q("""
        SELECT DISTINCT s.order_id FROM shipping_charges s JOIN orders o USING (order_id) WHERE o.channel = ?""")}
    charges = q("""
        SELECT s.charge_id, s.order_id, s.carrier, timezone('UTC', s.charged_at) AS charged_at, s.amount, s.tracking
        FROM shipping_charges s JOIN orders o USING (order_id) WHERE o.channel = ? ORDER BY s.charged_at, s.charge_id""")
    now = con.execute("SELECT timezone('UTC', max(paid_at)) FROM orders").fetchone()[0]
    return {"orders": orders, "lines": lines, "refunds": refunds, "payouts": payouts, "listings": listings,
            "labels_billed_by_carrier": carrier, "shipping_charges": charges, "now": now}
