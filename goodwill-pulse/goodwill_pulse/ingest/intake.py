"""Upload ingest: any file in -> spreadsheets -> the database.

    intake(con, path)                       -> dict   convert, route, load (or hold for review)
    intake(con, path, autoload=False)       -> dict   convert and check only: every spreadsheet is "ready" (the UI shows
                                                      what was found, a person chooses what to load)
    confirm(con, output_id, corrected=None) -> dict   load a held / ready spreadsheet, optionally a corrected copy
    python -m goodwill_pulse.ingest.intake FILE [FILE ...]

1. Convert (ingest/convert): the upload becomes one spreadsheet per table found. CSV / Excel pass through unchanged.
2. Route each spreadsheet:
     sales report (Upright, Cash Monkey: config/mappings)       -> pipeline.process_file -> warehouse.duckdb
     month-end input (bank, FedEx, Jewelry, Goodwill Books)     -> finance.load          -> finance.duckdb
     neither                                                    -> needs_mapping (the spreadsheet is kept)
3. Hold instead of load when a person has to look first:
     - Claude read it (scanned PDF, photo, free-text email): needs_review until confirmed
     - the rows don't add up to a total printed in the source: needs_review (finance.load's control)
4. Record: the original is archived; intake_files / intake_outputs say what came from where; every problem is a row
   in `exceptions` (the same list the Upload page shows), so nothing a converter skipped is silent.
"""
from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

import duckdb

from ..config import CONVERTED_DIR, FINANCE_DB_PATH, finance_layouts, mappings
from . import finance
from .convert import Conversion, Output, convert
from .pipeline import archive, load_aliases, process_file, sha256
from .recognize import read_header, recognize

HELD = ("needs_review", "needs_mapping", "ready")
PREVIEW_ROWS = 8


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _exception(con, file_id: str, name: str, e: dict) -> None:
    con.execute("""INSERT INTO exceptions (file_id, original_name, source_row, rule, severity, message, suggested_fix)
                   VALUES (?,?,?,?,?,?,?)""",
                [file_id, name, e.get("source_row"), e["rule"], e["severity"], e["message"], e.get("suggested_fix", "")])


def _label(layout: str | None) -> str:
    if layout in finance_layouts():
        return finance_layouts()[layout]["label"]
    if layout in mappings():
        return mappings()[layout]["label"]
    return ""


def _preview(o: Output) -> dict:
    """First rows of a spreadsheet, as text, for the UI to show before anything is loaded."""
    if o.rows is not None:
        return {"header": o.header, "rows": o.rows[:PREVIEW_ROWS]}
    try:
        df, _ = finance.read_sheet(o.path)
        return {"header": list(df.columns), "rows": df.head(PREVIEW_ROWS).astype(str).values.tolist()}
    except Exception:  # noqa: BLE001  (a preview must never fail an upload)
        return {"header": [], "rows": []}


def _target_of(con, path: Path, hint: str | None) -> tuple[str, str | None]:
    """('warehouse' | 'finance' | 'none', layout). A complete sales match wins, then a complete finance match, then
    a partial sales match (so the existing 'confirm the renamed column' flow handles it)."""
    rec = recognize(path, load_aliases(con))
    fm = finance.recognize(read_header(path), hint=hint)
    if rec.ready:
        return "warehouse", rec.report_type
    if fm.ready:
        return "finance", fm.layout
    if rec.report_type:
        return "warehouse", rec.report_type
    if fm.layout:
        return "finance", fm.layout
    return "none", None


def _route(con, o: Output, output_id: str, source_name: str, finance_path: Path, *, confirmed: bool = False,
           control_override: bool = False, autoload: bool = True) -> dict:
    """Load one spreadsheet (or hold it). Returns the intake_outputs row as a dict, plus its exceptions."""
    row = {"output_id": output_id, "name": o.name, "path": str(o.path), "origin": o.origin, "method": o.method,
           "target": "none", "layout": o.layout, "row_count": o.row_count, "loaded_rows": 0, "status": "rejected",
           "message": "", "file_id": None, "stated": json.dumps(o.stated) if o.stated else None, "exceptions": [],
           "control": None, "preview": _preview(o)}
    target, layout = _target_of(con, o.path, o.layout)
    row["target"], row["layout"] = target, layout or o.layout
    shown = f"{source_name} > {o.name}" if o.name != source_name else source_name

    if o.method == "ai" and not confirmed:
        row["status"] = "needs_review"
        kind = _label(row["layout"]) or "an unrecognized table"
        row["message"] = f"Read by Claude as {kind}; check the spreadsheet against the source, then confirm to load."
        if target == "finance":   # show what the checks will say once confirmed, without loading
            pre = finance.load(o.path, finance_path, layout=layout, stated=o.stated, dry_run=True)
            row["exceptions"] += pre.exceptions
            row["control"] = pre.control
            if pre.control:
                c = pre.control
                row["message"] += (f" Lines total ${c['rows_total']:,.2f} vs printed {c['label']} "
                                   f"${c['stated_total']:,.2f}: {'match' if c['ok'] else 'MISMATCH'}.")
        row["exceptions"].insert(0, {"rule": "needs_review", "severity": "warning", "source_row": None,
                                     "message": f"{o.name} was read by Claude from {o.origin}. Nothing is loaded "
                                                "until a person checks it.",
                                     "suggested_fix": "Download the spreadsheet, compare it with the source, fix any "
                                                      "cell, then confirm (or upload the corrected copy)."})
        for e in row["exceptions"]:
            _exception(con, output_id, shown, e)
        return row

    if not autoload and not confirmed:
        label = _label(row["layout"])
        row["status"] = "ready"
        row["message"] = f"Recognized as {label}. Not loaded yet." if label else "Converted. Not loaded yet."
        if target == "finance":
            pre = finance.load(o.path, finance_path, layout=layout, stated=o.stated, file_id=output_id, dry_run=True)
            row["exceptions"], row["control"] = pre.exceptions, pre.control
            row["months"] = pre.months
            if pre.status == "needs_review":
                row["status"], row["message"] = "needs_review", f"Recognized as {label}, but {pre.message.lower()}"
                for e in pre.exceptions:
                    _exception(con, output_id, shown, e)
            elif pre.status in ("needs_mapping", "rejected"):
                row["status"], row["message"] = pre.status, pre.message
                for e in pre.exceptions:
                    _exception(con, output_id, shown, e)
            else:
                row["message"] += (f" {pre.rows} rows" + (f" for {pre.period}" if pre.period else "") + ".")
        return row if target != "none" else _unmapped(con, o, row, shown)

    if target == "warehouse":
        fr = process_file(con, o.path)            # records its own report_files row and exceptions
        row.update(file_id=fr.file_id, layout=fr.report_type, row_count=fr.row_count, loaded_rows=fr.loaded_rows,
                   status=fr.status, message=fr.message, exceptions=fr.exceptions)
        return row

    if target == "finance":
        fres = finance.load(o.path, finance_path, layout=layout, stated=o.stated, file_id=output_id,
                            control_override=control_override)
        row.update(layout=fres.layout, row_count=fres.rows, loaded_rows=fres.loaded, status=fres.status,
                   message=fres.message, exceptions=fres.exceptions, months=fres.months, control=fres.control)
        if control_override and fres.control and not fres.control["ok"]:
            row["message"] += " Loaded despite the control-total difference (confirmed by a person)."
        for e in fres.exceptions:
            _exception(con, output_id, shown, e)
        return row

    return _unmapped(con, o, row, shown)


def _unmapped(con, o: Output, row: dict, shown: str) -> dict:
    row["status"] = "needs_mapping"
    row["message"] = "Converted, but the columns don't match any known report."
    e = {"rule": "unrecognized_report", "severity": "error", "source_row": None,
         "message": f"{o.name} (from {o.origin}) doesn't match any known report layout.",
         "suggested_fix": "Download the spreadsheet and check its columns. Add the column names as aliases in "
                          "config/mappings or config/finance_layouts, then confirm it again."}
    row["exceptions"] = [e]
    _exception(con, row["output_id"], shown, e)
    return row


def _save_output(con, intake_id: str, row: dict) -> None:
    con.execute("DELETE FROM intake_outputs WHERE output_id = ?", [row["output_id"]])
    con.execute("""INSERT INTO intake_outputs (output_id, intake_id, name, path, origin, method, target, layout,
                   row_count, loaded_rows, status, message, file_id, stated, control) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                [row["output_id"], intake_id, row["name"], row["path"], row["origin"], row["method"], row["target"],
                 row["layout"], row["row_count"], row["loaded_rows"], row["status"], row["message"], row["file_id"],
                 row["stated"], json.dumps(row["control"]) if row.get("control") else None])


def _overall(statuses: list[str]) -> str:
    if not statuses:
        return "rejected"
    if any(s == "needs_review" for s in statuses):
        return "needs_review"
    if any(s == "ready" for s in statuses):
        return "ready"
    if all(s in ("loaded", "duplicate_file") for s in statuses):
        return "loaded"
    if any(s in ("loaded", "partial") for s in statuses):
        return "partial"
    return "needs_mapping" if any(s == "needs_mapping" for s in statuses) else "rejected"


def _public(row: dict) -> dict:
    out = {k: v for k, v in row.items() if k not in ("path", "stated")}
    out.setdefault("months", [])
    out.setdefault("exceptions", [])
    out.setdefault("preview", None)
    if isinstance(out.get("control"), str):
        out["control"] = json.loads(out["control"])
    out["pending"] = out.get("status") in HELD
    out["stated"] = json.loads(row["stated"]) if row.get("stated") else {}
    out["label"] = _label(row.get("layout"))
    out["excel_url"] = f"/api/intake/outputs/{row['output_id']}/file"
    return out


def _existing(con, intake_id: str) -> list[dict]:
    cur = con.execute("SELECT * FROM intake_outputs WHERE intake_id = ? ORDER BY output_id", [intake_id])
    cols = [d[0] for d in cur.description]
    return [_public(dict(zip(cols, r))) for r in cur.fetchall()]


def intake(con: duckdb.DuckDBPyConnection, path: Path | str, finance_path: Path | str | None = None,
           converted_dir: Path | str | None = None, autoload: bool = True) -> dict:
    path, finance_path = Path(path), Path(finance_path or FINANCE_DB_PATH)
    converted_dir = converted_dir or CONVERTED_DIR
    intake_id = sha256(path)
    prev = con.execute("SELECT status, original_name FROM intake_files WHERE intake_id = ?", [intake_id]).fetchone()
    if prev and prev[0] not in ("rejected", "ready"):
        return {"intake_id": intake_id, "name": path.name, "status": "duplicate_file", "previous_status": prev[0],
                "message": f"This exact file was already uploaded ({prev[1]}, {prev[0]}).",
                "outputs": _existing(con, intake_id), "notes": []}

    conv: Conversion = convert(path)
    mid = conv.envelope.get("message_id")
    if mid:
        dup = con.execute("SELECT original_name FROM intake_files WHERE message_id = ? AND intake_id <> ? "
                          "AND status NOT IN ('rejected', 'ready')", [mid, intake_id]).fetchone()
        if dup:
            return {"intake_id": intake_id, "name": path.name, "status": "duplicate_file", "outputs": [], "notes": [],
                    "message": f"This email was already uploaded as {dup[0]} (same Message-ID)."}

    archived = archive(path, "intake", intake_id)
    when = _now()
    out_dir = Path(converted_dir) / f"{when:%Y}" / f"{when:%m}" / intake_id[:12]
    conv.write(out_dir)

    con.execute("DELETE FROM intake_outputs WHERE intake_id = ?", [intake_id])
    con.execute("DELETE FROM exceptions WHERE file_id = ? OR file_id LIKE ?", [intake_id, f"{intake_id[:12]}-%"])
    rows = []
    for n, o in enumerate(conv.outputs, 1):
        row = _route(con, o, f"{intake_id[:12]}-{n}", conv.source_name, finance_path, autoload=autoload)
        _save_output(con, intake_id, row)
        rows.append(row)
    for note in conv.notes:
        _exception(con, intake_id, path.name, {"rule": "unreadable_part", "severity": "warning", "message": note,
                                                "suggested_fix": "Upload that part separately as CSV, Excel or PDF."})

    status = _overall([r["status"] for r in rows])
    env = conv.envelope
    con.execute("DELETE FROM intake_files WHERE intake_id = ?", [intake_id])
    con.execute("INSERT INTO intake_files VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                [intake_id, path.name, conv.fmt, str(archived), when, env.get("from"), env.get("subject"),
                 env.get("date"), mid, status, json.dumps(conv.notes)])
    loaded = sum(r["loaded_rows"] or 0 for r in rows)
    held = sum(1 for r in rows if r["status"] in ("needs_review", "needs_mapping"))
    msg = f"{len(rows)} spreadsheet{'s' if len(rows) != 1 else ''} from {path.name}: {loaded} rows loaded"
    if not autoload and not held:
        msg = f"{len(rows)} spreadsheet{'s' if len(rows) != 1 else ''} found in {path.name}, ready to load"
    msg += f", {held} waiting for review" if held else ""
    msg += f", {len(conv.notes)} part{'s' if len(conv.notes) != 1 else ''} not readable" if conv.notes else ""
    return {"intake_id": intake_id, "name": path.name, "format": conv.fmt, "status": status, "message": msg + ".",
            "envelope": env, "notes": conv.notes, "outputs": [_public(r) for r in rows]}


def confirm(con: duckdb.DuckDBPyConnection, output_id: str, corrected: Path | None = None,
            confirmed_by: str = "", override_control: bool = False,
            finance_path: Path | str | None = None) -> dict:
    """A person checked a held spreadsheet (and maybe fixed it): load it. `override_control` loads even when the rows
    don't add up to the printed total; the difference stays on the exceptions list."""
    r = con.execute("SELECT intake_id, name, path, origin, method, layout, stated, status FROM intake_outputs "
                    "WHERE output_id = ?", [output_id]).fetchone()
    if r is None:
        raise KeyError(output_id)
    intake_id, name, path, origin, method, layout, stated, status = r
    if status not in HELD + ("partial", "rejected"):
        raise ValueError(f"{name} is already {status}.")
    path = Path(path)
    if corrected is not None:
        fixed = path.with_name(f"{path.stem} (corrected){Path(corrected).suffix or path.suffix}")
        shutil.copy2(corrected, fixed)
        path = fixed
    o = Output(path.name, origin + (" (corrected by a person)" if corrected is not None else ""), method,
               layout=layout, stated=json.loads(stated) if stated else {}, path=path)
    src = con.execute("SELECT original_name FROM intake_files WHERE intake_id = ?", [intake_id]).fetchone()
    con.execute("UPDATE exceptions SET status = 'resolved' WHERE file_id = ? AND rule IN "
                "('needs_review', 'unrecognized_report', 'control_total', 'missing_column') AND status = 'open'",
                [output_id])
    row = _route(con, o, output_id, src[0] if src else name, Path(finance_path or FINANCE_DB_PATH), confirmed=True,
                 control_override=override_control)
    row["row_count"] = row["row_count"] or 0
    _save_output(con, intake_id, row)
    con.execute("UPDATE intake_outputs SET confirmed_by = ?, confirmed_at = ? WHERE output_id = ?",
                [confirmed_by or None, _now(), output_id])
    statuses = [s for (s,) in con.execute("SELECT status FROM intake_outputs WHERE intake_id = ?", [intake_id]).fetchall()]
    con.execute("UPDATE intake_files SET status = ? WHERE intake_id = ?", [_overall(statuses), intake_id])
    return _public(row)


def output_path(con: duckdb.DuckDBPyConnection, output_id: str) -> Path:
    r = con.execute("SELECT path FROM intake_outputs WHERE output_id = ?", [output_id]).fetchone()
    if r is None or not Path(r[0]).exists():
        raise KeyError(output_id)
    return Path(r[0])


def output_preview(con: duckdb.DuckDBPyConnection, output_id: str) -> dict:
    """First rows of a stored spreadsheet (to reopen a file that is waiting for review)."""
    return _preview(Output("", "", "", path=output_path(con, output_id)))


def recent(con: duckdb.DuckDBPyConnection, limit: int = 50) -> list[dict]:
    files = con.execute("""SELECT intake_id, original_name, format, received_at, email_from, email_subject, email_date,
                                  message_id, status, notes FROM intake_files ORDER BY received_at DESC LIMIT ?""",
                        [limit]).fetchall()
    out = []
    for f in files:
        cur = con.execute("SELECT * FROM intake_outputs WHERE intake_id = ? ORDER BY output_id", [f[0]])
        cols = [d[0] for d in cur.description]
        outs = [_public(dict(zip(cols, r))) for r in cur.fetchall()]
        out.append({"intake_id": f[0], "name": f[1], "format": f[2], "received_at": f[3],
                    "envelope": {k: v for k, v in zip(("from", "subject", "date", "message_id"), f[4:8]) if v},
                    "status": f[8], "notes": json.loads(f[9] or "[]"), "outputs": outs})
    return out


def main(argv: list[str] | None = None) -> int:
    import argparse

    from .. import db
    p = argparse.ArgumentParser(prog="python -m goodwill_pulse.ingest.intake",
                                description="Convert and load any file (email, PDF, OFX, HTML, zip, CSV, Excel ...).")
    p.add_argument("files", nargs="+")
    p.add_argument("--finance", default=str(FINANCE_DB_PATH), help="finance.duckdb to load month-end inputs into")
    args = p.parse_args(argv)
    con = db.connect()
    try:
        for f in args.files:
            print(json.dumps(intake(con, Path(f), args.finance), indent=2, default=str))
    finally:
        con.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
