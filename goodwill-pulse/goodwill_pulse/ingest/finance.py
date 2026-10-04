"""Load a month-end input spreadsheet into finance.duckdb: bank activity, FedEx invoices, the Jewelry Report and the
Goodwill Books statement (slide 38). These are the tables close/rules.py reads; until now only the synthetic
generator (sources/finance.py) wrote them.

    recognize(header)                    -> FinanceMatch (which layout, column map, what's missing)
    load(path, finance_path, ...)        -> FinanceResult

Layouts live in config/finance_layouts/*.yaml (columns, aliases, value maps, how a period is replaced), so a real
export's column names are a config change. Same rules as the sales pipeline: good rows load, every problem becomes an
exception with a suggested fix. One extra control: when the source printed a total (a statement's net payment), the
rows must add up to it, or NOTHING loads until a person confirms. A short read must never look like a small month.
"""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path

import duckdb
import pandas as pd

from ..config import FINANCE_DB_PATH, finance_layouts

STATED_SHEET = "Stated values"
TOLERANCE = Decimal("0.005")


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9#&]", "", str(s).lower())


@dataclass
class FinanceMatch:
    layout: str | None
    score: float = 0.0
    column_map: dict[str, str] = field(default_factory=dict)     # file column -> layout column
    split: dict[str, str] = field(default_factory=dict)          # 'credit'/'debit' -> file column
    missing_required: list[str] = field(default_factory=list)

    @property
    def ready(self) -> bool:
        return self.layout is not None and not self.missing_required


@dataclass
class FinanceResult:
    layout: str | None
    label: str
    status: str                       # loaded | partial | needs_review | needs_mapping | rejected
    rows: int = 0
    loaded: int = 0
    period: str | None = None
    message: str = ""
    exceptions: list[dict] = field(default_factory=list)
    control: dict | None = None       # {rows_total, stated_total, label, ok}
    months: list[str] = field(default_factory=list)   # 'YYYY-MM' the loaded rows touch (closes to rebuild)

    def add(self, rule: str, severity: str, message: str, row: int | None = None, fix: str = "") -> None:
        self.exceptions.append({"rule": rule, "severity": severity, "message": message, "source_row": row,
                                "suggested_fix": fix})


# ---------------------------------------------------------------------------------------------------- recognize
def recognize(header: list[str], hint: str | None = None) -> FinanceMatch:
    """Best finance layout for these column names. `hint` (a layout the converter already identified) wins ties."""
    best = FinanceMatch(None)
    for lid, spec in finance_layouts().items():
        names = {_norm(c): c for c in spec["columns"]}
        names.update({_norm(a): c for a, c in (spec.get("aliases") or {}).items()})
        col_map: dict[str, str] = {}
        for h in header:
            c = names.get(_norm(h))
            if c and c not in col_map.values():
                col_map[h] = c
        split = {}
        sa = spec.get("split_amount")
        if sa:
            for side in ("credit", "debit"):
                wanted = {_norm(x) for x in sa[side]}
                split.update({side: h for h in header if _norm(h) in wanted and h not in col_map})
        found = set(col_map.values())
        amount_col = next((c for c, m in spec["columns"].items() if sa and m["field"] == sa["field"]), None)
        if amount_col and amount_col not in found and len(split) == 2:
            found.add(amount_col)
        missing = [c for c, m in spec["columns"].items() if m.get("required") and c not in found]
        score = len(found) / len(spec["columns"]) + (0.25 if lid == hint else 0)
        cand = FinanceMatch(lid, score, col_map, split if len(split) == 2 else {}, missing)
        if (cand.ready, cand.score) > (best.ready, best.score):
            best = cand
    if best.score < 0.5:
        return FinanceMatch(None, best.score)
    return best


# -------------------------------------------------------------------------------------------------------- parse
def money(s) -> Decimal | None:
    """'$1,234.56', '(12.00)', '12.00-', '-$5' -> Decimal. Blank -> None. Raises ValueError if unreadable."""
    t = str(s or "").strip().replace("$", "").replace(",", "").replace(" ", "")
    if not t:
        return None
    neg = t.startswith("(") and t.endswith(")") or t.endswith("-")
    t = t.strip("()").rstrip("-")
    try:
        v = Decimal(t)
    except InvalidOperation:
        raise ValueError(s)
    return -abs(v) if neg else v


def to_date(s) -> date | None:
    t = str(s or "").strip()
    if not t:
        return None
    d = pd.to_datetime(t, errors="coerce", format="mixed")
    if pd.isna(d):
        raise ValueError(s)
    return d.date()


def month_of(s: str) -> str | None:
    """'September 2026', '2026-09', '09/01/2026 - 09/30/2026' -> '2026-09'."""
    t = str(s or "").strip()
    if re.fullmatch(r"\d{4}-\d{2}", t):
        return t
    for cand in [t] + re.findall(r"\d{1,2}/\d{1,2}/\d{2,4}|\d{4}-\d{2}-\d{2}|[A-Za-z]{3,9}\.? \d{1,2},? \d{4}", t):
        d = pd.to_datetime(cand, errors="coerce", format="mixed")
        if not pd.isna(d):
            return f"{d:%Y-%m}"
    return None


def _convert(value: str, typ: str):
    if typ == "money":
        return money(value)
    if typ == "date":
        return to_date(value)
    if typ == "int":
        v = str(value).strip()
        return int(Decimal(v)) if v else None
    v = str(value).strip()
    if typ == "upper":
        return v.upper() or None
    if typ == "lower":
        return v.lower() or None
    if typ == "last4":
        digits = re.sub(r"\D", "", v)
        return (digits[-4:] if len(digits) >= 4 else v) or None
    return v or None


def read_sheet(path: Path) -> tuple[pd.DataFrame, dict[str, str]]:
    """(table as text, stated values). Converted workbooks carry printed totals on a 'Stated values' sheet."""
    path = Path(path)
    stated: dict[str, str] = {}
    if path.suffix.lower() in (".xlsx", ".xls"):
        df = pd.read_excel(path, sheet_name=0, dtype=str)
        try:
            st = pd.read_excel(path, sheet_name=STATED_SHEET, dtype=str).fillna("")
            stated = {r["Label"]: r["Value"] for _, r in st.iterrows() if r["Label"]}
        except (ValueError, KeyError):
            pass
    else:
        df = pd.read_csv(path, dtype=str, keep_default_na=False, encoding="utf-8-sig")
    df.columns = [str(c).strip() for c in df.columns]
    return df.fillna(""), stated


def _stated_value(stated: dict[str, str], labels: list[str]) -> tuple[str, str] | None:
    norm = {_norm(k): (k, v) for k, v in stated.items()}
    for label in labels:
        if _norm(label) in norm:
            return norm[_norm(label)]
    return None


# --------------------------------------------------------------------------------------------------------- load
def _ensure_tables(con: duckdb.DuckDBPyConnection) -> None:
    from ..sources.finance import FINANCE_DDL
    con.execute(FINANCE_DDL)


def _table_columns(con, table: str) -> list[str]:
    return [r[0] for r in con.execute("SELECT column_name FROM information_schema.columns WHERE table_name = ? "
                                      "ORDER BY ordinal_position", [table]).fetchall()]


def load(path: Path, finance_path: Path | str = FINANCE_DB_PATH, *, layout: str | None = None,
         stated: dict[str, str] | None = None, file_id: str = "", control_override: bool = False,
         dry_run: bool = False) -> FinanceResult:
    """Parse, check and load one spreadsheet. `stated` adds printed values found by the converter; `dry_run` checks
    without writing (used to preview a file held for review)."""
    df, sheet_stated = read_sheet(path)
    stated = {**sheet_stated, **(stated or {})}
    match = recognize(list(df.columns), hint=layout)
    if match.layout is None:
        res = FinanceResult(None, "", "rejected", message="Not a known month-end layout.")
        res.add("unrecognized_report", "error", "This file doesn't match any known report layout.", None,
                "Add its column names as aliases in config/finance_layouts/*.yaml.")
        return res
    spec = finance_layouts()[match.layout]
    res = FinanceResult(match.layout, spec["label"], "loaded")
    if not match.ready:
        res.status = "needs_mapping"
        res.message = f"Looks like {spec['label']}, but required columns are missing: {', '.join(match.missing_required)}"
        for m in match.missing_required:
            res.add("missing_column", "error", f"Required column '{m}' was not found.", 1,
                    f"Add the file's name for '{m}' under aliases in config/finance_layouts/{match.layout}.yaml.")
        return res

    # --- rows -> fields -------------------------------------------------------------------------------------
    cols = spec["columns"]
    inverse = {v: k for k, v in match.column_map.items()}            # layout column -> file column
    values = {f: {str(k).upper(): v for k, v in m.items()} for f, m in (spec.get("values") or {}).items()}
    defaults = spec.get("defaults") or {}
    records, totals_rows = [], []
    for i, raw in df.iterrows():   # rows not loaded are counted from their exceptions (one row number each)
        row_no = int(i) + 2
        if not "".join(raw.values).strip():
            continue
        rec, errors = {}, []
        for col, meta in cols.items():
            v = raw[inverse[col]] if col in inverse else ""
            try:
                rec[meta["field"]] = _convert(v, meta.get("type", "text"))
            except (ValueError, InvalidOperation):
                errors.append(f"'{col}' value '{v}' can't be read as {meta.get('type')}")
                rec[meta["field"]] = None
        if match.split:
            fld = spec["split_amount"]["field"]
            try:
                cr, dr = money(raw[match.split["credit"]]), money(raw[match.split["debit"]])
                rec[fld] = (cr or 0) - abs(dr or 0) if (cr is not None or dr is not None) else None
            except ValueError:
                errors.append("Debit / Credit can't be read as money")
        for fld, m in values.items():
            if isinstance(rec.get(fld), str):
                rec[fld] = m.get(rec[fld].upper(), rec[fld])
        for fld, v in defaults.items():
            if rec.get(fld) in (None, ""):
                rec[fld] = v
        sd = spec.get("sign_default")
        if sd and rec.get(sd["field"]) in (None, "") and rec.get("amount") is not None:
            rec[sd["field"]] = sd["negative"] if rec["amount"] < 0 else sd["positive"]
        missing = [c for c, m in cols.items() if m.get("required") and rec.get(m["field"]) in (None, "")]
        if missing and any(re.search(r"\btotal", str(x), re.I) for x in raw.values):
            totals_rows.append((row_no, raw))                            # a printed 'Total' row: a control, not data
            continue
        if errors or missing:
            for e in errors:
                res.add("bad_value", "error", e, row_no, "Fix this cell in the source and upload again.")
            for c in missing:
                res.add("missing_value", "error", f"'{c}' is empty", row_no, "This row was not loaded.")
            continue
        records.append(rec)
    res.rows = len(records) + len({e["source_row"] for e in res.exceptions if e["source_row"]})
    if not records:
        res.status = "rejected"
        res.message = "No rows could be read."
        return res

    # --- period -----------------------------------------------------------------------------------------------
    per = spec.get("period")
    if per:
        found = _stated_value(stated, per.get("stated") or [])
        period = month_of(found[1]) if found else None
        months = Counter(f"{r[per['from']]:%Y-%m}" for r in records if r.get(per["from"]))
        if period is None and months:
            period = months.most_common(1)[0][0]
        if period is None:
            res.status = "rejected"
            res.message = f"Can't tell which month this {spec['label']} is for."
            res.add("no_period", "error", res.message, None, "Add a statement period, or dates on the lines.")
            return res
        outside = sum(n for m, n in months.items() if m != period)
        if outside:
            res.add("outside_period", "info", f"{outside} lines are dated outside {period} (kept in {period}).", None, "")
        for r in records:
            r[per["field"]] = period
        res.period = period

    # --- generated keys ---------------------------------------------------------------------------------------
    for fld, how in (spec.get("generate") or {}).items():
        if how == "sequence":
            if any(r.get(fld) is None for r in records) or len({r.get(fld) for r in records}) < len(records):
                for n, r in enumerate(records, 1):
                    r[fld] = n
        else:
            for n, r in enumerate(records, 1):
                r[fld] = f"{how}-{(file_id or 'manual')[:8]}-{n:05d}"

    # --- header (printed totals) and control ------------------------------------------------------------------
    header_row = None
    hspec = spec.get("header")
    if hspec:
        header_row = {}
        for fld, meta in hspec["fields"].items():
            got = _stated_value(stated, meta["labels"])
            if got:
                try:
                    v = _convert(got[1], meta.get("type", "text"))
                except (ValueError, InvalidOperation):
                    res.add("bad_value", "warning", f"Printed '{got[0]}' value '{got[1]}' can't be read", None, "")
                    continue
                header_row[fld] = abs(v) if meta.get("abs") and v is not None else v
        if not any(v is not None for v in header_row.values()):
            header_row = None
            res.add("no_stated_total", "warning", f"No printed totals found on the {spec['label']}; the lines can't "
                    "be checked against it.", None, "Check the statement's totals by hand before approving the close.")
    ctl = spec.get("control")
    if ctl:
        rows_total = sum((r.get(ctl["sum"]) or Decimal(0) for r in records), Decimal(0))
        stated_total, label = None, None
        if header_row and header_row.get(ctl["equals"]) is not None:
            stated_total, label = Decimal(str(header_row[ctl["equals"]])), ctl["equals"]
        elif totals_rows:
            amount_col = next(c for c, m in cols.items() if m["field"] == ctl["sum"])
            try:
                stated_total, label = money(totals_rows[-1][1][inverse[amount_col]]), f"Total row {totals_rows[-1][0]}"
            except (ValueError, KeyError):
                pass
        if stated_total is not None:
            ok = abs(rows_total - stated_total) <= TOLERANCE
            res.control = {"rows_total": float(rows_total), "stated_total": float(stated_total), "label": label, "ok": ok}
            if not ok:
                res.add("control_total", "error",
                        f"Lines add up to ${rows_total:,.2f} but the {spec['label']} says {label} ${stated_total:,.2f} "
                        f"(difference ${rows_total - stated_total:,.2f}).", None,
                        "Open the spreadsheet and compare it with the source: a line was probably missed or misread. "
                        "Fix it and upload the corrected spreadsheet, or confirm to load anyway.")
                if not control_override:
                    res.status = "needs_review"
                    res.message = "Held for review: lines don't add up to the printed total."
                    return res

    date_fields = [m["field"] for m in cols.values() if m.get("type") == "date"]
    res.months = sorted({f"{r[f]:%Y-%m}" for r in records for f in date_fields if r.get(f)} | ({res.period} if res.period else set()))
    if dry_run:
        res.status = "partial" if any(e["severity"] == "error" for e in res.exceptions) else "loaded"
        res.message = f"Ready to load {len(records)} rows into {spec['table']}" + (f" for {res.period}." if res.period else ".")
        return res

    # --- write ------------------------------------------------------------------------------------------------
    Path(finance_path).parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(finance_path))
    try:
        _ensure_tables(con)
        table = spec["table"]
        tcols = _table_columns(con, table)
        out = pd.DataFrame([{c: r.get(c) for c in tcols} for r in records])
        for c in out.columns:
            if out[c].map(lambda x: isinstance(x, Decimal)).any():
                out[c] = out[c].map(lambda x: None if x is None else float(x))
        rep = spec.get("replace") or {}
        con.execute("BEGIN TRANSACTION")
        if "by" in rep:            # one field or several: the rows sharing a loaded key combination are replaced
            by = [rep["by"]] if isinstance(rep["by"], str) else list(rep["by"])
            keys = pd.DataFrame(sorted({tuple(r.get(f) for f in by) for r in records}, key=str), columns=by)
            con.register("_keys", keys)
            con.execute(f"DELETE FROM {table} USING _keys k WHERE " + " AND ".join(f"{table}.{f} = k.{f}" for f in by))
            con.unregister("_keys")
        elif "range" in rep:
            f = rep["range"]
            lo, hi = min(r[f] for r in records), max(r[f] for r in records)
            within = rep.get("within")
            extra, params = "", [lo, hi]
            if within:
                ws = sorted({str(r[within]) for r in records})
                extra = f" AND {within} IN ({', '.join('?' * len(ws))})"
                params += ws
            con.execute(f"DELETE FROM {table} WHERE {f} BETWEEN ? AND ?{extra}", params)
        con.register("_up", out)
        con.execute(f"INSERT INTO {table} ({', '.join(tcols)}) SELECT {', '.join(tcols)} FROM _up")
        con.unregister("_up")
        if hspec and header_row is not None and res.period:
            htable, key = hspec["table"], per["field"]
            hcols = _table_columns(con, htable)
            hrow = {key: res.period, **{k: (float(v) if isinstance(v, Decimal) else v) for k, v in header_row.items()}}
            hrow = {k: v for k, v in hrow.items() if k in hcols}
            con.execute(f"DELETE FROM {htable} WHERE {key} = ?", [res.period])
            con.execute(f"INSERT INTO {htable} ({', '.join(hrow)}) VALUES ({', '.join('?' * len(hrow))})", list(hrow.values()))
        con.execute("COMMIT")
        con.execute("CHECKPOINT")
    except Exception:
        try:
            con.execute("ROLLBACK")
        except duckdb.Error:
            pass
        raise
    finally:
        con.close()
    res.loaded = len(records)
    if any(e["severity"] == "error" for e in res.exceptions):
        res.status = "partial"
    res.message = f"Loaded {res.loaded} rows into {spec['label']}" + (f" for {res.period}." if res.period else ".")
    return res
