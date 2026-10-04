"""HTTP API + the live dashboard.

    .venv/bin/uvicorn goodwill_pulse.api:app --reload --port 8000
"""
from __future__ import annotations

import shutil
import threading
from datetime import date
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import db
from .config import INBOX_DIR, SAMPLES_DIR, WEB_DIR
from .ingest.pipeline import FileResult, confirm_aliases, process_dir, process_file
from .pulse import build_pulse, business_dates
from .routes import ai as ai_routes, close as close_routes, dashboard as dashboard_routes, intake as intake_routes, \
    runtime as runtime_routes

app = FastAPI(title="Goodwill Pulse")
_lock = threading.Lock()
_con = None  # opened on first use, so importing the app (e.g. in tests) doesn't take the warehouse lock


def _db():
    global _con
    if _con is None:
        _con = db.connect()
    return _con

for _r in (dashboard_routes, close_routes, ai_routes, runtime_routes, intake_routes):
    app.include_router(_r.router)


def _result(r: FileResult) -> dict:
    return {"file_id": r.file_id, "name": r.original_name, "report_type": r.report_type, "status": r.status,
            "rows": r.row_count, "loaded": r.loaded_rows, "message": r.message, "exceptions": r.exceptions}


def _rows(sql: str, params: list | None = None) -> list[dict]:
    with _lock:
        cur = _db().execute(sql, params or [])
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]


@app.get("/api/health")
def health() -> dict:
    return {"ok": True}


@app.get("/api/pulse/dates")
def pulse_dates() -> list[str]:
    with _lock:
        return business_dates(_db())


@app.get("/api/pulse")
def pulse(d: date | None = None) -> dict:
    with _lock:
        if d is None:
            dates = business_dates(_db())
            if not dates:
                raise HTTPException(404, "No orders loaded yet.")
            d = date.fromisoformat(dates[0])
        return build_pulse(_db(), d)


SPREADSHEET = (".csv", ".xlsx", ".xls")


def _intake_results(r: dict) -> list[dict]:
    """An intake result in /api/upload's shape: one entry per spreadsheet made from the upload."""
    if not r.get("outputs"):
        return [{"file_id": r["intake_id"], "name": r["name"], "report_type": None, "status": r["status"],
                 "rows": 0, "loaded": 0, "message": r["message"], "exceptions": [], "notes": r.get("notes", [])}]
    return [{"file_id": o.get("file_id") or o["output_id"], "name": f"{r['name']} > {o['name']}",
             "report_type": o["layout"], "status": o["status"], "rows": o["row_count"], "loaded": o["loaded_rows"],
             "message": o["message"], "exceptions": o["exceptions"], "intake_id": r["intake_id"],
             "output_id": o["output_id"], "method": o["method"], "origin": o["origin"], "excel_url": o["excel_url"]}
            for o in r["outputs"]]


@app.post("/api/upload")
async def upload(files: list[UploadFile] = File(...)) -> list[dict]:
    """Spreadsheets load exactly as before. Any other format (email, PDF, OFX, ...) is converted first by upload
    ingest (routes/intake.py) and reported here in the same shape, one entry per spreadsheet made from it."""
    INBOX_DIR.mkdir(parents=True, exist_ok=True)
    out = []
    for f in files:
        name = Path(f.filename or "upload.csv").name
        data = await f.read()
        if Path(name).suffix.lower() not in SPREADSHEET:
            for r in intake_routes.ingest_upload([f], [data]):
                out += _intake_results(r)
            continue
        dest = INBOX_DIR / name
        dest.write_bytes(data)
        with _lock:
            out.append(_result(process_file(_db(), dest)))
        dest.unlink(missing_ok=True)
    return out


@app.get("/api/files")
def files() -> list[dict]:
    return _rows("""SELECT file_id, report_type, original_name, received_at, coverage_start, coverage_end,
                           row_count, loaded_rows, control_total, latest_order_at, status, message
                    FROM report_files ORDER BY received_at DESC LIMIT 200""")


@app.get("/api/exceptions")
def exceptions(status: str = "open") -> list[dict]:
    return _rows("""SELECT exception_id, created_at, file_id, original_name, source_row, rule, severity, message,
                           suggested_fix, status
                    FROM exceptions WHERE status = ? AND severity <> 'info'
                    ORDER BY created_at DESC, exception_id""", [status])


class AliasBody(BaseModel):
    aliases: dict[str, str] | None = None


@app.post("/api/files/{file_id}/confirm-mapping")
def confirm_mapping(file_id: str, body: AliasBody | None = None) -> dict:
    with _lock:
        try:
            return _result(confirm_aliases(_db(), file_id, body.aliases if body else None))
        except KeyError:
            raise HTTPException(404, "Unknown file")


@app.post("/api/exceptions/{exception_id}/resolve")
def resolve(exception_id: int) -> dict:
    with _lock:
        _db().execute("UPDATE exceptions SET status = 'resolved' WHERE exception_id = ?", [exception_id])
    return {"ok": True}


# --- demo helpers: make the recording reproducible -----------------------------------------------
@app.post("/api/demo/reset")
def demo_reset() -> dict:
    """Wipe the warehouse and load the synthetic history (everything before the demo day)."""
    global _con
    with _lock:
        if _con is not None:
            _con.close()
        db.reset()
        _con = db.connect()
        results = process_dir(_db(), SAMPLES_DIR / "history")
    return {"files": len(results), "orders": _rows("SELECT count(*) AS n FROM orders")[0]["n"]}


@app.post("/api/demo/drop/{sample_set}")
def demo_drop(sample_set: str) -> list[dict]:
    """Drop tonight's reports into the inbox, as Upright's email and Cash Monkey's download would."""
    src = SAMPLES_DIR / sample_set
    if sample_set not in ("demo", "messy") or not src.exists():
        raise HTTPException(404, "Unknown sample set")
    INBOX_DIR.mkdir(parents=True, exist_ok=True)
    for p in src.iterdir():
        shutil.copy2(p, INBOX_DIR / p.name)
    with _lock:
        return [_result(r) for r in process_dir(_db(), INBOX_DIR, move_done=True)]


SHELL = ('<!doctype html><html lang="en"><head><meta charset="utf-8">'
         '<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover"></head><body>{}</body></html>')


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    # Always the full app (all tabs). web/app.html is committed; rebuild it with `python -m goodwill_pulse.webapp`
    # after changing artifact/ (tests/test_app_page.py fails while it is stale).
    app_page = WEB_DIR / "app.html"
    if not app_page.exists():
        raise HTTPException(500, "web/app.html is missing: run .venv/bin/python -m goodwill_pulse.webapp")
    return app_page.read_text()


@app.get("/pulse", response_class=HTMLResponse)
def pulse_page() -> str:
    # web/index.html is written as an artifact page (no <html>/<head>); add the document shell here
    return SHELL.format((WEB_DIR / "index.html").read_text())


def _page(name: str) -> HTMLResponse:
    path = WEB_DIR / f"{name}.html"
    if not path.exists():
        raise HTTPException(404, f"{name} page not built yet")
    return HTMLResponse(path.read_text())


@app.get("/dashboard", response_class=HTMLResponse)
def dashboard_page() -> HTMLResponse:
    return _page("dashboard")


@app.get("/close", response_class=HTMLResponse)
def close_page() -> HTMLResponse:
    return _page("close")


app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")
