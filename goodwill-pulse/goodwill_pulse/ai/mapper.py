"""Report recognizer + column mapper (PLAN C1). Output is a SUGGESTION for a person to confirm, never applied.

    suggest_mapping(header, rows, filename) -> {
        report_type, label, confidence (0-1), engine: claude|fallback,
        column_mapping {source_column: canonical_field | "ignore"},
        aliases {source_column: expected_column}      # renamed columns, ready for POST /api/files/{id}/confirm-mapping
        columns [{source_column, canonical_field, expected_column, method, score}],
        missing_required [expected columns], sample_checks [...], notes [...]}

Known layouts come from config/mappings/*.yaml. The fallback is deterministic fuzzy matching on normalized names
('Sub Total' -> 'Subtotal'); both paths are validated against the sample rows (money parses as money, dates as
dates) before anything is shown.
"""
from __future__ import annotations

import csv
import difflib
import io
import json
import re
from pathlib import Path
from typing import Any

import pandas as pd

from ..config import mappings
from . import client as ai

SYNONYMS = {   # extra spellings seen in marketplace exports -> canonical field
    "subtotal": ["sub total", "item subtotal", "items total", "merchandise total", "item total"],
    "paid_at_local": ["paid date", "payment date", "date paid", "paid on", "order paid at", "paid time"],
    "channel_order_id": ["marketplace order id", "order number", "order no", "order #"],
    "buyer": ["customer", "buyer name", "buyer username", "username", "buyer id"],
    "item_count": ["items", "item count", "qty items", "number of items"],
    "tax": ["tax", "sales tax", "tax amount"],
    "shipping_charged": ["shipping", "shipping paid", "shipping charged", "shipping income"],
    "quantity": ["qty", "units"],
}


def norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", str(s).lower())


def _catalog() -> dict[str, dict]:
    """{report_type: {label, filename_pattern, columns: {expected: {field, type, required}}}}"""
    return {rt: {"label": spec["label"], "filename_pattern": spec["filename_pattern"], "grain": spec.get("grain"),
                 "columns": {c: {"field": m["field"], "type": m.get("type", "text"), "required": bool(m.get("required"))}
                             for c, m in spec["columns"].items()}}
            for rt, spec in sorted(mappings().items())}


def _fields(spec: dict) -> dict[str, str]:
    """canonical field -> expected column (first one; 'ignore' excluded)."""
    out: dict[str, str] = {}
    for col, m in spec["columns"].items():
        if m["field"] != "ignore":
            out.setdefault(m["field"], col)
    return out


# ---------------------------------------------------------------------------------------------------------------
# sample validation
def _money_ok(v: str) -> bool:
    return bool(re.fullmatch(r"\(?-?\$?\s*-?[\d,]*\.?\d+\)?", v.strip()))


def _check_samples(spec: dict, header: list[str], rows: list[list[str]], mapping: dict[str, str]) -> list[dict]:
    """Do the sample values look like the type the mapping claims?"""
    fields = _fields(spec)
    out = []
    for src, fld in mapping.items():
        if fld == "ignore" or fld not in fields or src not in header:
            continue
        typ = spec["columns"][fields[fld]]["type"]
        if typ == "text":
            continue
        i = header.index(src)
        vals = [str(r[i]).strip() for r in rows if i < len(r) and str(r[i]).strip()]
        if not vals:
            continue
        if typ == "money":
            bad = [v for v in vals if not _money_ok(v)]
        elif typ == "int":
            bad = [v for v in vals if not re.fullmatch(r"-?\d+(\.0+)?", v.replace(",", ""))]
        elif typ == "datetime":
            bad = [v for v in vals if pd.isna(pd.to_datetime(v, errors="coerce", format="mixed"))]
        else:
            bad = []
        out.append({"source_column": src, "canonical_field": fld, "expected_type": typ, "ok": not bad,
                    "bad_examples": bad[:3]})
    return out


# ---------------------------------------------------------------------------------------------------------------
# deterministic fuzzy matcher
def _match_column(h: str, spec: dict) -> tuple[str, str | None, str, float] | None:
    """(canonical_field, expected_column, method, score) for one header, or None."""
    nh = norm(h)
    if h in spec["columns"]:
        return spec["columns"][h]["field"], h, "exact", 1.0
    for col, m in spec["columns"].items():
        if norm(col) == nh:
            return m["field"], col, "normalized", 0.95
    fields = _fields(spec)
    for fld, syns in SYNONYMS.items():
        if fld in fields and nh in {norm(s) for s in syns}:
            return fld, fields[fld], "synonym", 0.85
    names = {norm(c): c for c in spec["columns"]}
    names.update({norm(f): fields[f] for f in fields if norm(f) not in names})
    close = difflib.get_close_matches(nh, list(names), n=1, cutoff=0.8)
    if close:
        col = names[close[0]]
        return spec["columns"][col]["field"], col, "fuzzy", round(difflib.SequenceMatcher(None, nh, close[0]).ratio(), 2)
    return None


def _assemble(rt: str, spec: dict, header: list[str], rows: list[list[str]], filename: str,
              picks: dict[str, tuple[str, str | None, str, float]], notes: list[str]) -> dict:
    used: dict[str, str] = {}      # expected column -> source column (first wins)
    columns, mapping, aliases = [], {}, {}
    for h in header:
        p = picks.get(h)
        if p and p[1] and p[1] in used and p[0] != "ignore":
            notes.append(f"'{h}' also looks like '{p[1]}' (already matched to '{used[p[1]]}'); left unmapped.")
            p = None
        if p is None:
            mapping[h] = "ignore"
            columns.append({"source_column": h, "canonical_field": "ignore", "expected_column": None,
                            "method": "unmatched", "score": 0.0})
            continue
        fld, exp, method, score = p
        mapping[h] = fld
        if exp:
            used[exp] = h
            if exp != h:
                aliases[h] = exp
        columns.append({"source_column": h, "canonical_field": fld, "expected_column": exp, "method": method,
                        "score": score})
    required = [c for c, m in spec["columns"].items() if m["required"]]
    missing = [c for c in required if c not in used]
    checks = _check_samples(spec, header, rows, mapping)
    bad_checks = [c for c in checks if not c["ok"]]
    for c in bad_checks:
        notes.append(f"'{c['source_column']}' should be {c['expected_type']} but has values like "
                     f"{', '.join(repr(v) for v in c['bad_examples'])}.")
    for exp_col in missing:
        notes.append(f"Required column '{exp_col}' was not found.")
    for src, exp in aliases.items():
        notes.append(f"'{src}' looks like a renamed '{exp}'.")
    req_frac = (len(required) - len(missing)) / len(required) if required else 1.0
    col_frac = len(used) / len(spec["columns"])
    name_hit = bool(re.match(spec["filename_pattern"], Path(filename or "").name))
    conf = (0.6 * req_frac + 0.3 * col_frac + 0.1 * name_hit) * (0.85 ** len(bad_checks))
    return {"report_type": rt, "label": spec["label"], "confidence": round(min(conf, 1.0), 2),
            "column_mapping": mapping, "aliases": aliases, "columns": columns, "missing_required": missing,
            "sample_checks": checks, "filename_matches": name_hit, "notes": notes}


def fuzzy_mapping(header: list[str], rows: list[list[str]], filename: str = "") -> dict:
    best = None
    for rt, spec in _catalog().items():
        picks = {h: m for h in header if (m := _match_column(h, spec))}
        cand = _assemble(rt, spec, header, rows, filename, picks, [])
        if best is None or cand["confidence"] > best["confidence"]:
            best = cand
    if best is None or best["confidence"] < 0.4:
        return {"report_type": None, "label": None, "confidence": best["confidence"] if best else 0.0,
                "column_mapping": {h: "ignore" for h in header}, "aliases": {}, "columns": [],
                "missing_required": [], "sample_checks": [], "filename_matches": False,
                "notes": ["This file doesn't look like any known report layout."]}
    return best


# ---------------------------------------------------------------------------------------------------------------
# Claude path
MAPPER_SYSTEM = """You map the columns of a marketplace/listing-tool export onto Goodwill's known report layouts.

You get a filename, a header row and up to 5 sample rows. Decide which known report type the file is (or "unknown"),
then map EVERY header column to one canonical field of that report type, or "ignore" if it carries nothing the
layout needs. Columns are often renamed between exports ("Sub Total" vs "Subtotal", "Paid Date" vs "Paid At"):
use the names AND the sample values (money, dates, ids, counts) to decide. Map at most one source column to each
canonical field. `transform` briefly states any conversion a loader would need (date format, time zone, sign,
cents->dollars) or "" if none. Confidence is your 0-1 belief that the whole mapping is right. Notes: one or two
short sentences a non-technical person can act on.

Known report types (canonical field per expected column, type, required):
"""

MAPPER_SCHEMA_BASE = {
    "type": "object",
    "properties": {
        "report_type": {"type": "string"},
        "confidence": {"type": "number"},
        "columns": {"type": "array", "items": {
            "type": "object",
            "properties": {"source_column": {"type": "string"}, "canonical_field": {"type": "string"},
                           "transform": {"type": "string"}},
            "required": ["source_column", "canonical_field", "transform"], "additionalProperties": False}},
        "notes": {"type": "string"},
    },
    "required": ["report_type", "confidence", "columns", "notes"],
    "additionalProperties": False,
}


def _claude_mapping(header: list[str], rows: list[list[str]], filename: str) -> dict | None:
    cat = _catalog()
    schema = json.loads(json.dumps(MAPPER_SCHEMA_BASE))
    schema["properties"]["report_type"]["enum"] = sorted(cat) + ["unknown"]
    system = MAPPER_SYSTEM + json.dumps(cat, sort_keys=True, indent=1)
    user = json.dumps({"filename": filename, "header": header, "sample_rows": rows[:5]})
    try:
        resp = ai.create("mapper", system=ai.cached_system(system), messages=[{"role": "user", "content": user}],
                         max_tokens=4096, output_config={"effort": "low", **ai.json_schema(schema)})
        out = ai.json_of(resp)
    except ai.AIUnavailable:
        return None
    rt = out.get("report_type")
    if rt not in cat:
        return None
    spec = cat[rt]
    valid_fields = set(_fields(spec)) | {"ignore"}
    by_field = _fields(spec)
    picks: dict[str, tuple[str, str | None, str, float]] = {}
    notes = [str(out.get("notes") or "").strip()] if out.get("notes") else []
    for c in out.get("columns", []):
        src, fld = c.get("source_column"), c.get("canonical_field")
        if src not in header or fld not in valid_fields:
            if src in header:
                notes.append(f"Model proposed unknown field '{fld}' for '{src}'; ignored.")
            continue
        if fld == "ignore":
            exp = src if src in spec["columns"] else None
            picks[src] = ("ignore", exp, "claude", 1.0)
        else:
            picks[src] = (fld, by_field[fld], "claude", 1.0)
        if c.get("transform"):
            notes.append(f"{src}: {c['transform']}")
    for h in header:                       # anything the model skipped: deterministic match
        if h not in picks and (m := _match_column(h, spec)):
            picks[h] = m
    res = _assemble(rt, spec, header, rows, filename, picks, notes)
    res["confidence"] = round(min(float(out.get("confidence") or 0), 1.0) * (0.85 ** sum(
        1 for c in res["sample_checks"] if not c["ok"])) * (1.0 if not res["missing_required"] else 0.7), 2)
    return res


# ---------------------------------------------------------------------------------------------------------------
def suggest_mapping(header: list[str], rows: list[list[Any]] | list[dict] | None = None, filename: str = "") -> dict:
    header = [str(h).strip() for h in header]
    rows = [[str(r.get(h, "")) for h in header] if isinstance(r, dict) else ["" if v is None else str(v) for v in r]
            for r in (rows or [])][:5]
    if ai.ai_available():
        res = _claude_mapping(header, rows, filename)
        if res is not None:
            ai.log_engine("mapper", "claude", f"{res['report_type']} conf={res['confidence']}")
            return {**res, "engine": "claude", "applied": False}
    res = fuzzy_mapping(header, rows, filename)
    ai.log_engine("mapper", "fallback", f"{res['report_type']} conf={res['confidence']}")
    return {**res, "engine": "fallback", "applied": False}


def read_sample(data: bytes, filename: str, n: int = 5) -> tuple[list[str], list[list[str]]]:
    """Header + first n data rows from an uploaded CSV or Excel file."""
    if Path(filename).suffix.lower() in (".xlsx", ".xls"):
        df = pd.read_excel(io.BytesIO(data), dtype=str, nrows=n).fillna("")
        return [str(c).strip() for c in df.columns], df.astype(str).values.tolist()
    text = data.decode("utf-8-sig", errors="replace")
    reader = csv.reader(io.StringIO(text))
    header = [h.strip() for h in next(reader, [])]
    rows = []
    for r in reader:
        if len(rows) >= n:
            break
        if any(c.strip() for c in r):
            rows.append(r)
    return header, rows
