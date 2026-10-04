"""Claude reads what no deterministic reader can: scanned PDFs, photos / screenshots of a report, free-text emails.

Claude only TRANSCRIBES: it copies the table cells and the printed totals as they appear and names which known layout
the table is (if any). It does no arithmetic and fills in nothing. Every table it returns is marked method="ai" and
held for a person to confirm before it loads; the loader's control totals still run on it.
"""
from __future__ import annotations

import base64

from ...ai import client as ai

SYSTEM = """You transcribe business reports into tables for an accounting system at Goodwill Michiana.

Rules:
- Copy every table row that appears in the document, in order. Copy each cell exactly as printed: keep signs,
  parentheses, $ and commas, dates as written. Never compute, round, total, reformat, translate or guess a value.
  A cell that is blank or unreadable is "" (and say which in notes).
- If a table matches one of the known layouts, set `layout` to its id and use that layout's column names for the
  columns that are printed (leave out layout columns the document does not have). Otherwise use the printed
  column headers and layout "unknown".
- Put every printed total, subtotal, period, statement date or payment date that is NOT a table row into
  `stated` as {label, value}, label and value exactly as printed.
- Do not include page numbers, addresses, logos or marketing text."""

SCHEMA = {
    "type": "object",
    "properties": {
        "tables": {"type": "array", "items": {
            "type": "object",
            "properties": {
                "layout": {"type": "string"},
                "title": {"type": "string"},
                "columns": {"type": "array", "items": {"type": "string"}},
                "rows": {"type": "array", "items": {"type": "array", "items": {"type": "string"}}},
            },
            "required": ["layout", "title", "columns", "rows"], "additionalProperties": False}},
        "stated": {"type": "array", "items": {
            "type": "object", "properties": {"label": {"type": "string"}, "value": {"type": "string"}},
            "required": ["label", "value"], "additionalProperties": False}},
        "notes": {"type": "string"},
    },
    "required": ["tables", "stated", "notes"], "additionalProperties": False,
}

READ_TIMEOUT = 180      # seconds: transcribing a multi-page statement takes longer than the app's 30 s default
IMAGE_TYPES = {"png": "image/png", "jpeg": "image/jpeg", "gif": "image/gif", "webp": "image/webp"}


def available() -> bool:
    return ai.ai_available()


def _layout_text(layouts: list[dict]) -> str:
    return "\n".join(f"- {l['id']} ({l['label']}): {', '.join(l['columns'])}" for l in layouts)


def read(layouts: list[dict], *, pdf: bytes | None = None, image: tuple[bytes, str] | None = None,
         text: str | None = None, hint: str = "") -> dict:
    """{tables: [{layout, title, columns, rows}], stated: {label: value}, notes}. Raises ai.AIUnavailable."""
    content: list[dict] = []
    if pdf is not None:
        content.append({"type": "document", "source": {"type": "base64", "media_type": "application/pdf",
                                                       "data": base64.b64encode(pdf).decode()}})
    if image is not None:
        data, kind = image
        content.append({"type": "image", "source": {"type": "base64", "media_type": IMAGE_TYPES[kind],
                                                    "data": base64.b64encode(data).decode()}})
    if text is not None:
        content.append({"type": "text", "text": f"<document>\n{text[:100_000]}\n</document>"})
    content.append({"type": "text", "text": f"Known layouts:\n{_layout_text(layouts)}\n\n"
                                            f"Source: {hint or 'uploaded file'}\nTranscribe the tables."})
    resp = ai.create("convert", system=ai.cached_system(SYSTEM), messages=[{"role": "user", "content": content}],
                     max_tokens=16000, timeout=READ_TIMEOUT, output_config={"effort": "low", **ai.json_schema(SCHEMA)})
    out = ai.json_of(resp)
    ids = {l["id"] for l in layouts}
    tables = []
    for t in out.get("tables") or []:
        cols = [str(c) for c in t.get("columns") or []]
        if not cols:
            continue
        tables.append({"layout": t.get("layout") if t.get("layout") in ids else None, "title": t.get("title") or "",
                       "header": cols, "rows": [[str(c) for c in r] for r in t.get("rows") or []]})
    stated = {}
    for s in out.get("stated") or []:
        stated.setdefault(str(s.get("label", "")).strip(), str(s.get("value", "")).strip())
    ai.log_engine("convert", "claude", f"{len(tables)} tables")
    return {"tables": tables, "stated": {k: v for k, v in stated.items() if k}, "notes": out.get("notes") or ""}
