"""/api/ai routes: grounded narratives, column-mapping suggestions, ask-the-data (docs/CONTRACT.md section 6/7).

Every response carries `engine: "claude" | "fallback"`; narratives also carry `checked_numbers` (each number in
the text with the fact it matched) so the UI can prove nothing was invented.
"""
from __future__ import annotations

from datetime import date
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel

from ..ai import ask as ask_mod
from ..ai import client as ai
from ..ai.mapper import read_sample, suggest_mapping
from ..ai.narrator import month_narrative, pulse_narrative
from ..config import DATA_DIR

router = APIRouter(prefix="/api/ai", tags=["ai"])
HARMONIZED = DATA_DIR / "harmonized.duckdb"


def _harmonized_path() -> Path:
    if not HARMONIZED.exists():
        raise HTTPException(503, "data/harmonized.duckdb is not built yet (run python -m goodwill_pulse.build)")
    return HARMONIZED


@router.get("/status")
def status() -> dict:
    return {"claude_available": ai.ai_available(), "model": ai.MODEL}


@router.post("/narrative/pulse")
def narrative_pulse(d: date | None = None, body: dict | None = None) -> dict:
    """Narrative for the Daily Pulse of business date `d` (default: latest). A pulse JSON may be POSTed instead."""
    if body and "total" in body and "rows" in body:
        return pulse_narrative(body)
    from .. import api as _api          # lazy: api.py imports this module
    from ..pulse import build_pulse, business_dates
    with _api._lock:
        con = _api._db() if hasattr(_api, "_db") else _api._con
        if d is None:
            dates = business_dates(con)
            if not dates:
                raise HTTPException(404, "No orders loaded yet.")
            d = date.fromisoformat(dates[0])
        pulse = build_pulse(con, d)
    return pulse_narrative(pulse)


def _parse_month(month: str | None) -> date | None:
    if not month:
        return None
    try:
        return ask_mod._month(month)
    except ValueError as e:
        raise HTTPException(422, str(e))


@router.post("/narrative/month")
def narrative_month(month: str | None = None, store: str | None = None, channel: str | None = None,
                    body: dict | None = None) -> dict:
    """Narrative for the monthly scorecard (kpi.scorecard). A scorecard JSON may be POSTed instead."""
    if body and ("anchors" in body or "kpis" in body):
        return month_narrative(body, _parse_month(month))
    try:
        from .. import kpi
    except Exception as e:  # noqa: BLE001
        raise HTTPException(503, f"KPI module unavailable: {e}")
    con = kpi.connect_harmonized(_harmonized_path())
    try:
        m = _parse_month(month) or kpi.default_month(con)
        if m is None:
            raise HTTPException(404, "No orders in the harmonized model.")
        sc = kpi.scorecard(con, m, store or None, channel or None)
    finally:
        con.close()
    return month_narrative(sc, m)


class MapBody(BaseModel):
    header: list[str]
    rows: list[list[str | int | float | None]] | list[dict] = []
    filename: str = ""


@router.post("/map-columns")
async def map_columns(request: Request) -> dict:
    """Suggest report type + column mapping. Multipart `file` (CSV/XLSX) or JSON {header, rows, filename}.
    Nothing is applied; confirm renames with POST /api/files/{file_id}/confirm-mapping {aliases}."""
    ctype = request.headers.get("content-type", "")
    if ctype.startswith("multipart/"):
        form = await request.form()
        f = form.get("file")
        if f is None or not hasattr(f, "read"):
            raise HTTPException(422, "multipart upload needs a 'file' field")
        name = getattr(f, "filename", None) or "upload.csv"
        try:
            header, rows = read_sample(await f.read(), name)
        except Exception as e:  # noqa: BLE001
            raise HTTPException(422, f"could not read file: {e}")
    else:
        try:
            body = MapBody(**(await request.json()))
        except Exception as e:  # noqa: BLE001
            raise HTTPException(422, f"expected JSON {{header, rows, filename}}: {e}")
        header, rows, name = body.header, body.rows, body.filename
    if not header:
        raise HTTPException(422, "empty header")
    return await run_in_threadpool(suggest_mapping, header, rows, name)


class AskBody(BaseModel):
    question: str
    month: str | None = None


@router.post("/ask")
def ask(body: AskBody) -> dict:
    if not body.question.strip():
        raise HTTPException(422, "question is empty")
    try:
        return ask_mod.ask(body.question, body.month, db_path=_harmonized_path())
    except ValueError as e:
        raise HTTPException(422, str(e))
