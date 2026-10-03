"""Work out which report a file is, and how its columns map onto the report's known layout."""
from __future__ import annotations

import csv
import difflib
import re
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

from ..config import mappings


def read_header(path: Path) -> list[str]:
    if path.suffix.lower() in (".xlsx", ".xls"):
        return [str(c) for c in pd.read_excel(path, nrows=0).columns]
    with path.open(newline="", encoding="utf-8-sig") as f:
        return [h.strip() for h in next(csv.reader(f), [])]


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower())


@dataclass
class Recognition:
    report_type: str | None
    score: float
    column_map: dict[str, str] = field(default_factory=dict)       # file column -> expected column
    missing_required: list[str] = field(default_factory=list)
    suggestions: dict[str, str] = field(default_factory=dict)       # file column -> expected column (unconfirmed)
    unknown_columns: list[str] = field(default_factory=list)

    @property
    def ready(self) -> bool:
        return self.report_type is not None and not self.missing_required


def recognize(path: Path, aliases: dict[str, dict[str, str]] | None = None) -> Recognition:
    """Fingerprint by filename pattern and header columns. Deterministic; no AI.

    `aliases` holds confirmed renames per report type: {report_type: {file column: expected column}}.
    """
    header = read_header(path)
    aliases = aliases or {}
    best = Recognition(None, 0.0, unknown_columns=header)
    for rtype, spec in mappings().items():
        expected = list(spec["columns"])
        confirmed = aliases.get(rtype, {})
        col_map = {}
        for h in header:
            if h in spec["columns"]:
                col_map[h] = h
            elif h in confirmed:
                col_map[h] = confirmed[h]
        found = set(col_map.values())
        required = [c for c, v in spec["columns"].items() if v.get("required")]
        missing = [c for c in required if c not in found]
        score = len(found & set(expected)) / len(expected)
        if re.match(spec["filename_pattern"], path.name):
            score += 0.5
        if score > best.score:
            unknown = [h for h in header if h not in col_map]
            suggestions = {}
            for m in missing:
                close = difflib.get_close_matches(_norm(m), [_norm(u) for u in unknown], n=1, cutoff=0.7)
                if close:
                    suggestions[next(u for u in unknown if _norm(u) == close[0])] = m
            best = Recognition(rtype, score, col_map, missing, suggestions, unknown)
    if best.score < 0.5:
        return Recognition(None, best.score, unknown_columns=header)
    return best
