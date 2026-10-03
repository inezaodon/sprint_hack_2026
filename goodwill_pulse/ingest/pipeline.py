"""Intake -> archive -> recognize -> parse -> validate -> load.

Good rows always load. Every problem becomes a row in `exceptions` with a reason and a suggested fix,
so a bad file never silently changes the totals.
"""
from __future__ import annotations

import hashlib
import re
import shutil
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import duckdb
import pandas as pd

from ..config import ARCHIVE_DIR, business_tz, mappings
from .recognize import Recognition, recognize

UTC = ZoneInfo("UTC")


@dataclass
class FileResult:
    file_id: str
    original_name: str
    report_type: str | None
    status: str
    row_count: int = 0
    loaded_rows: int = 0
    skipped_rows: int = 0
    exceptions: list[dict] = field(default_factory=list)
    message: str = ""

    def add(self, rule: str, severity: str, message: str, row: int | None = None, fix: str = "") -> None:
        self.exceptions.append({"rule": rule, "severity": severity, "message": message,
                                "source_row": row, "suggested_fix": fix})


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_aliases(con: duckdb.DuckDBPyConnection) -> dict[str, dict[str, str]]:
    out: dict[str, dict[str, str]] = {}
    for rtype, src, dst in con.execute("SELECT report_type, source_column, mapped_to FROM column_aliases").fetchall():
        out.setdefault(rtype, {})[src] = dst
    return out


def archive(path: Path, report_type: str | None, file_id: str) -> Path:
    now = datetime.now()
    dest_dir = ARCHIVE_DIR / f"{now:%Y}" / f"{now:%m}" / (report_type or "unrecognized")
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / f"{file_id[:10]}__{path.name}"
    if not dest.exists():
        shutil.copy2(path, dest)
    return dest


def _read_frame(path: Path) -> pd.DataFrame:
    if path.suffix.lower() in (".xlsx", ".xls"):
        df = pd.read_excel(path, dtype=str)
    else:
        df = pd.read_csv(path, dtype=str, keep_default_na=False, encoding="utf-8-sig")
    df.columns = [str(c).strip() for c in df.columns]
    df = df.fillna("")
    df.insert(0, "__row", range(2, len(df) + 2))   # spreadsheet row numbers: header is row 1
    return df


def _money(s: pd.Series) -> pd.Series:
    return pd.to_numeric(s.astype(str).str.replace(r"[$,\s]", "", regex=True).replace("", "0"), errors="coerce")


def _coverage(spec: dict, path: Path, paid_utc: pd.Series) -> tuple[datetime | None, datetime | None]:
    """The time window the report claims to cover, in UTC."""
    tz = ZoneInfo(spec["source_timezone"])
    m = re.match(spec["filename_pattern"], path.name)
    if spec["report_type"] == "upright_paid_orders" and m:
        fmt = spec["filename_date_format"]
        start = datetime.strptime(m.group(1), fmt).replace(tzinfo=tz)
        end = datetime.strptime(m.group(2), fmt).replace(tzinfo=tz) + timedelta(days=1)
        return start.astimezone(UTC), end.astimezone(UTC)
    if paid_utc.notna().any():
        # Cash Monkey: the requested range isn't in the file. Start = first order's UTC day;
        # end = when the report was generated (in the filename), else the end of the last order's day.
        first = paid_utc.min().tz_convert(tz).normalize().to_pydatetime().astimezone(UTC)
        last = (paid_utc.max().tz_convert(tz).normalize() + pd.Timedelta(days=1)).to_pydatetime().astimezone(UTC)
        if m:
            generated = datetime.strptime(m.group(1), spec["filename_date_format"]).replace(tzinfo=UTC)
            # a report pulled the next morning covers only through its last day; one pulled tonight, through now
            last = max(min(generated, last), paid_utc.max().to_pydatetime())
        return first, last
    return None, None


def parse(path: Path, rec: Recognition, res: FileResult) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    """Return (orders, order_lines, file facts) in canonical form."""
    spec = mappings()[rec.report_type]
    raw = _read_frame(path)
    inverse = {v: k for k, v in rec.column_map.items()}   # expected column -> column name in this file

    def col(expected: str) -> pd.Series:
        return raw[inverse[expected]] if expected in inverse else pd.Series([""] * len(raw), index=raw.index)

    fields = {meta["field"]: expected for expected, meta in spec["columns"].items() if meta["field"] != "ignore"}
    id_col = fields["channel_order_id"]

    # Totals row: no order id but numbers present (Upright export ends with =SUM rows)
    facts: dict = {"control_total": None}
    if spec.get("has_totals_row"):
        is_total = (col(id_col).str.strip() == "") & (col(fields["subtotal"]).str.strip() != "")
        if is_total.any():
            facts["control_total"] = float(_money(col(fields["subtotal"])[is_total]).sum())
            raw = raw[~is_total]
    blank = raw.drop(columns="__row").apply(lambda r: "".join(r).strip() == "", axis=1)
    raw = raw[~blank]
    res.row_count = len(raw)

    df = pd.DataFrame({"source_row": raw["__row"]})
    for fld, expected in fields.items():
        df[fld] = col(expected).loc[raw.index].astype(str).str.strip()
    money_fields = [f for f, e in fields.items() if spec["columns"][e].get("type") == "money"]
    for f in money_fields:
        df[f] = _money(df[f])
    for f in [f for f, e in fields.items() if spec["columns"][e].get("type") == "int"]:
        df[f] = pd.to_numeric(df[f], errors="coerce")

    tz = ZoneInfo(spec["source_timezone"])
    local = pd.to_datetime(df["paid_at_local"], errors="coerce", format="mixed")
    df["paid_at_utc"] = local.dt.tz_localize(tz, ambiguous="NaT", nonexistent="NaT").dt.tz_convert(UTC)
    df["channel"] = df["channel_raw"].map(spec["channel_values"])

    # --- row validation --------------------------------------------------------------------------
    bad = pd.Series(False, index=df.index)
    for fld, expected in fields.items():
        if spec["columns"][expected].get("required"):
            miss = df[fld].isna() | (df[fld].astype(str).isin(["", "nan"]))
            for r in df.loc[miss & ~bad, "source_row"]:
                res.add("missing_value", "error", f"'{expected}' is empty", int(r),
                        "Check this row in the source report; it was not loaded.")
            bad |= miss
    for f in money_fields:
        neg = df[f] < 0
        for r in df.loc[neg & ~bad, "source_row"]:
            res.add("negative_amount", "warning", f"Negative {f} loaded as-is", int(r), "Confirm this is a refund or adjustment.")
    unknown = df["channel"].isna() & ~bad
    for raw_val, rows in df[unknown].groupby("channel_raw")["source_row"]:
        res.add("unknown_channel", "error", f"Channel '{raw_val}' is not mapped ({len(rows)} rows)", int(rows.iloc[0]),
                f"Add '{raw_val}' to channel_values in config/mappings/{spec['report_type']}.yaml")
    bad |= unknown
    bad_time = df["paid_at_utc"].isna() & ~bad
    for r in df.loc[bad_time, "source_row"]:
        res.add("bad_timestamp", "error", "Payment time could not be read", int(r), "Check the date format in this row.")
    bad |= bad_time
    df = df[~bad].copy()

    df["order_key"] = spec["source_system"] + ":" + df["channel"] + ":" + df["channel_order_id"]

    lines = pd.DataFrame()
    if spec["grain"] == "unit":
        # one row per unit -> group into orders (counting rows would overcount customers)
        df["quantity"] = df["quantity"].fillna(1)
        lines = df.assign(line_no=df.groupby("order_key").cumcount() + 1)
        agg = {
            "channel": "first", "channel_order_id": "first", "buyer": "first", "paid_at_utc": "min",
            "quantity": "sum", "subtotal": "sum", "shipping_charged": "sum", "marketplace_fees": "sum",
            "shipping_cost": "sum", "source_row": lambda s: ",".join(map(str, s)),
        }
        orders = df.groupby("order_key", as_index=False).agg(agg).rename(columns={"quantity": "item_count"})
        for c in ("handling", "tax"):
            orders[c] = 0.0
        orders["total"] = orders["subtotal"] + orders["shipping_charged"]
    else:
        dup = df.duplicated("order_key", keep="first")
        exact = df.duplicated([c for c in df.columns if c != "source_row"], keep="first")
        for r, k in df.loc[dup, ["source_row", "order_key"]].itertuples(index=False):
            same = exact.loc[df["source_row"] == r].iloc[0]
            res.add("duplicate_order", "warning" if same else "error",
                    f"Order {k.split(':')[-1]} appears more than once in this file" + ("" if same else " with different amounts"),
                    int(r), "Duplicate row skipped; the first copy was loaded." if same
                    else "Review both rows in the source report; only the first was loaded.")
        df = df[~dup].copy()
        orders = df.rename(columns={"source_row": "source_rows"})
        orders["source_rows"] = orders["source_rows"].astype(str)
        orders["marketplace_fees"] = orders.get("fee_final_value", 0).fillna(0) + orders.get("fee_payment", 0).fillna(0)
        orders["shipping_cost"] = None
    if "source_row" in orders:
        orders = orders.rename(columns={"source_row": "source_rows"})

    orders["source_system"] = spec["source_system"]
    orders["report_type"] = spec["report_type"]
    orders["line_of_business"] = spec["line_of_business"]
    orders["buyer_key"] = orders["channel"] + ":" + orders["buyer"].astype(str).map(
        lambda b: hashlib.sha1(b.lower().encode()).hexdigest()[:12])
    orders["business_date"] = orders["paid_at_utc"].dt.tz_convert(business_tz()).dt.date
    res.skipped_rows = int(bad.sum())

    paid = df["paid_at_utc"]
    facts["coverage_start"], facts["coverage_end"] = _coverage(spec, path, paid)
    facts["latest_order_at"] = paid.max().to_pydatetime() if len(paid) else None
    if facts["control_total"] is not None:
        parsed_total = float(_money(col(fields["subtotal"]).loc[raw.index]).sum())
        if abs(parsed_total - facts["control_total"]) > 0.01:
            res.add("control_total", "error",
                    f"Rows add up to ${parsed_total:,.2f} but the report's total row says ${facts['control_total']:,.2f}",
                    None, "The export may be truncated; download the report again.")
    return orders, lines, facts


ORDER_COLS = ["order_key", "source_system", "report_type", "channel", "line_of_business", "channel_order_id",
              "buyer_key", "paid_at_utc", "business_date", "item_count", "subtotal", "shipping_charged", "handling",
              "tax", "marketplace_fees", "shipping_cost", "total", "file_id", "source_rows"]


def process_file(con: duckdb.DuckDBPyConnection, path: Path) -> FileResult:
    path = Path(path)
    file_id = sha256(path)
    prev = con.execute("SELECT status FROM report_files WHERE file_id = ?", [file_id]).fetchone()
    if prev and prev[0] in ("loaded", "partial"):
        return FileResult(file_id, path.name, None, "duplicate_file", message="This exact file was already processed.")
    con.execute("DELETE FROM report_files WHERE file_id = ?", [file_id])
    con.execute("DELETE FROM exceptions WHERE file_id = ?", [file_id])

    rec = recognize(path, load_aliases(con))
    res = FileResult(file_id, path.name, rec.report_type, "loaded")
    archived = archive(path, rec.report_type, file_id)
    facts: dict = {}

    if rec.report_type is None:
        res.status = "rejected"
        res.message = "Unrecognized report."
        res.add("unrecognized_report", "error", "This file doesn't match any known report layout.", None,
                "Use 'Map columns' to tell the system what each column is.")
    elif not rec.ready:
        res.status = "needs_mapping"
        label = mappings()[rec.report_type]["label"]
        res.message = f"Looks like {label}, but required columns are missing: {', '.join(rec.missing_required)}"
        for src, dst in rec.suggestions.items():
            res.add("renamed_column", "error", f"Column '{dst}' is missing; the file has '{src}' instead.", 1,
                    f"Confirm '{src}' means '{dst}' to load this file.")
        for m in [m for m in rec.missing_required if m not in rec.suggestions.values()]:
            res.add("missing_column", "error", f"Required column '{m}' was not found.", 1,
                    "Download the report again with all columns, or map a column to it.")
    else:
        orders, lines, facts = parse(path, rec, res)
        before = con.execute("SELECT count(*) FROM orders").fetchone()[0]
        if len(orders):
            orders["file_id"] = file_id
            for c in ORDER_COLS:
                if c not in orders:
                    orders[c] = None
            con.register("new_orders", orders[ORDER_COLS])
            # Same order arriving in an overlapping report: keep the first copy (order_key is the identity)
            con.execute(f"INSERT INTO orders SELECT {', '.join(ORDER_COLS)} FROM new_orders ON CONFLICT (order_key) DO NOTHING")
            con.unregister("new_orders")
        if len(lines):
            lines = lines.assign(file_id=file_id, sale_price=lines["subtotal"], fee_alloc=lines["marketplace_fees"])
            con.register("new_lines", lines)
            con.execute("""INSERT INTO order_lines SELECT order_key, line_no, sku, title, store_id, quantity,
                           sale_price, fee_alloc, file_id, source_row FROM new_lines""")
            con.unregister("new_lines")
        res.loaded_rows = con.execute("SELECT count(*) FROM orders").fetchone()[0] - before
        overlap = len(orders) - res.loaded_rows
        if overlap:
            res.add("already_loaded", "info", f"{overlap} orders were already loaded from an earlier report.", None, "")
        if any(e["severity"] == "error" for e in res.exceptions):
            res.status = "partial"
        res.message = f"Loaded {res.loaded_rows} orders from {res.row_count} rows."

    con.execute(
        "INSERT INTO report_files VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        [file_id, res.report_type, path.name, str(archived), datetime.now(timezone.utc),
         facts.get("coverage_start"), facts.get("coverage_end"), res.row_count, res.loaded_rows,
         facts.get("control_total"), facts.get("latest_order_at"), res.status, res.message])
    for e in res.exceptions:
        con.execute("""INSERT INTO exceptions (file_id, original_name, source_row, rule, severity, message, suggested_fix)
                       VALUES (?,?,?,?,?,?,?)""",
                    [file_id, path.name, e["source_row"], e["rule"], e["severity"], e["message"], e["suggested_fix"]])
    return res


def confirm_aliases(con: duckdb.DuckDBPyConnection, file_id: str, aliases: dict[str, str] | None = None) -> FileResult:
    """Accept suggested column renames for a file, remember them, and reprocess the archived file."""
    row = con.execute("SELECT archived_path, report_type FROM report_files WHERE file_id = ?", [file_id]).fetchone()
    if not row:
        raise KeyError(file_id)
    path, rtype = Path(row[0]), row[1]
    if aliases is None:
        aliases = recognize(path, load_aliases(con)).suggestions
    for src, dst in aliases.items():
        con.execute("INSERT OR REPLACE INTO column_aliases (report_type, source_column, mapped_to) VALUES (?,?,?)",
                    [rtype, src, dst])
    con.execute("UPDATE exceptions SET status = 'resolved' WHERE file_id = ? AND rule IN ('renamed_column','missing_column')", [file_id])
    con.execute("UPDATE report_files SET status = 'superseded' WHERE file_id = ?", [file_id])
    original = path.name.split("__", 1)[-1]
    tmp = path.parent / original
    shutil.copy2(path, tmp)
    try:
        con.execute("DELETE FROM report_files WHERE file_id = ?", [file_id])
        return process_file(con, tmp)
    finally:
        tmp.unlink(missing_ok=True)


def process_dir(con: duckdb.DuckDBPyConnection, directory: Path, move_done: bool = False) -> list[FileResult]:
    results = []
    for path in sorted(p for p in Path(directory).iterdir() if p.suffix.lower() in (".csv", ".xlsx", ".xls")):
        results.append(process_file(con, path))
        if move_done:
            path.unlink()
    return results
