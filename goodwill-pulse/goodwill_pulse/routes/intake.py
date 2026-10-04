"""Upload ingest API: any file format in, spreadsheets out, loaded into the warehouse or the close's finance inputs.

    GET  /api/intake/formats                         {accept: [".csv", ".eml", ...], ai: bool, spreadsheets: [...]}
    POST /api/intake/preview                         same upload as POST /api/intake, but nothing is loaded: each
                                                     spreadsheet comes back "ready" (or needs_review / needs_mapping)
                                                     with `preview` rows, `control` (rows vs printed total) and
                                                     `target` ("finance" = the close's month-end inputs; anything
                                                     else = daily sales numbers). Load one with .../confirm.
    POST /api/intake                                 multipart `files`: emails, PDFs, OFX, HTML, JSON, XML, zips,
                                                     images, CSV, Excel. -> [{intake_id, status, message, envelope,
                                                     notes, outputs: [{output_id, name, origin, method, target,
                                                     layout, label, status, row_count, loaded_rows, message,
                                                     exceptions, stated, excel_url, months}]}]
    GET  /api/intake                                 recent uploads with their spreadsheets
    GET  /api/intake/outputs/{output_id}/file        download a spreadsheet made from an upload (to review it)
    GET  /api/intake/outputs/{output_id}/preview     {header, rows}: its first rows
    POST /api/intake/outputs/{output_id}/confirm     load a held spreadsheet. Optional multipart `file` = corrected
                                                     copy; form fields `confirmed_by`, `override_control` (true loads
                                                     even if the rows don't add up to the printed total)

Shares the warehouse connection and lock with api.py. Loading month-end inputs drops the cached close for each month
they touch, so the Close page rebuilds from the new data (an approved month must be approved again).
"""
from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse

from ..config import INBOX_DIR
from ..ingest import intake as intake_mod

router = APIRouter(prefix="/api/intake", tags=["intake"])

XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _warehouse():
    from .. import api            # imported late: api.py includes this router
    return api._db(), api._lock


def _refresh_close(results: list[dict]) -> list[str]:
    from . import close as close_routes
    months = sorted({m for r in results for o in r.get("outputs", [r]) for m in o.get("months", [])
                     if o.get("target") == "finance" and o.get("loaded_rows")})
    with close_routes._lock:
        for m in months:
            close_routes._cache.pop(m, None)
    return months


def ingest_upload(files: list[UploadFile], contents: list[bytes], autoload: bool = True) -> list[dict]:
    """Write each upload to the inbox, run intake, clean up. Used by POST /api/intake and api.py's /api/upload."""
    con, lock = _warehouse()
    INBOX_DIR.mkdir(parents=True, exist_ok=True)
    out = []
    for f, data in zip(files, contents):
        dest = INBOX_DIR / Path(f.filename or "upload").name
        dest.write_bytes(data)
        try:
            with lock:
                out.append(intake_mod.intake(con, dest, autoload=autoload))
        finally:
            dest.unlink(missing_ok=True)
    rebuilt = _refresh_close(out)
    for r in out:
        r["close_months_rebuilt"] = rebuilt
    return out


ACCEPT = [".csv", ".xlsx", ".xls", ".eml", ".msg", ".pdf", ".ofx", ".qfx", ".html", ".htm", ".json", ".xml", ".txt",
          ".tsv", ".zip", ".png", ".jpg", ".jpeg", ".gif", ".webp"]


@router.get("/formats")
def formats() -> dict:
    from ..ai import client as ai
    return {"accept": ACCEPT, "spreadsheets": [".csv", ".xlsx", ".xls"], "ai": ai.ai_available()}


@router.post("/preview")
async def preview(files: list[UploadFile] = File(...)) -> list[dict]:
    return ingest_upload(files, [await f.read() for f in files], autoload=False)


@router.post("")
async def upload_any(files: list[UploadFile] = File(...)) -> list[dict]:
    return ingest_upload(files, [await f.read() for f in files])


@router.get("")
def recent(limit: int = 50) -> list[dict]:
    con, lock = _warehouse()
    with lock:
        return intake_mod.recent(con, limit)


@router.get("/outputs/{output_id}/file")
def download(output_id: str) -> FileResponse:
    con, lock = _warehouse()
    with lock:
        try:
            path = intake_mod.output_path(con, output_id)
        except KeyError:
            raise HTTPException(404, "Unknown spreadsheet")
    media = XLSX if path.suffix.lower() == ".xlsx" else "text/csv" if path.suffix.lower() == ".csv" else None
    return FileResponse(path, filename=path.name, media_type=media)


@router.get("/outputs/{output_id}/preview")
def output_preview(output_id: str) -> dict:
    con, lock = _warehouse()
    with lock:
        try:
            return intake_mod.output_preview(con, output_id)
        except KeyError:
            raise HTTPException(404, "Unknown spreadsheet")


@router.post("/outputs/{output_id}/confirm")
async def confirm(output_id: str, file: UploadFile | None = File(None), confirmed_by: str = Form(""),
                  override_control: bool = Form(False)) -> dict:
    con, lock = _warehouse()
    tmp = None
    try:
        if file is not None:
            suffix = Path(file.filename or "corrected.xlsx").suffix or ".xlsx"
            if suffix.lower() not in (".xlsx", ".xls", ".csv"):
                raise HTTPException(422, "Upload the corrected copy as Excel or CSV.")
            with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as t:
                shutil.copyfileobj(file.file, t)
                tmp = Path(t.name)
        with lock:
            try:
                held = con.execute("SELECT status FROM intake_outputs WHERE output_id = ?", [output_id]).fetchone()
                if held and held[0] == "needs_review" and not confirmed_by.strip():
                    raise HTTPException(422, "Enter your name to release a file that was held for review.")
                res = intake_mod.confirm(con, output_id, tmp, confirmed_by.strip(), override_control)
            except KeyError:
                raise HTTPException(404, "Unknown spreadsheet")
            except ValueError as e:
                raise HTTPException(409, str(e))
    finally:
        if tmp:
            tmp.unlink(missing_ok=True)
    res["close_months_rebuilt"] = _refresh_close([{"outputs": [res]}])
    return res
