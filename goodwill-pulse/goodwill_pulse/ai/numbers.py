"""Number checker: every number in generated text must come from the facts JSON (within rounding).

    check_numbers("Revenue was $13.2K, up 18%.", facts) -> NumberCheck(ok=..., checked=[...], unmatched=[...])

Rules
- Numbers are read with their displayed precision: "$13.2K" means 13,200 ± 50, "18%" means 18 ± 0.5,
  "13,247.50" means 13,247.50 ± 0.005. A fact matches if it rounds to what the text shows.
- Percent numbers also match fractional facts (0.183 -> "18%"). Signs are ignored ("down 16%" vs -16.2).
- Dates and times inside the facts (ISO strings) contribute their year/month/day/hour/minute, so
  "Saturday, October 3, 2026" or "as of 10 PM" pass when the facts say so.
- Digits glued to letters (Store07, Q3, ISBNs inside words) are identifiers, not numbers, and are skipped.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Iterator

_MULT = {"k": 1e3, "thousand": 1e3, "m": 1e6, "million": 1e6, "b": 1e9, "bn": 1e9, "billion": 1e9}

NUM_RE = re.compile(r"""
    (?<![\w.])                                         # not glued to a word: Store07, v2, 1.2.3
    (?P<cur>\$\s?)?
    (?P<num>\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?|\.\d+)
    (?P<suf>\s?(?:[KkMB]|bn|thousand|million|billion)(?![A-Za-z]))?
    (?P<pct>\s?(?:%|percent(?:age\ points?)?|pp|pts?\b|points?\b))?
    (?![\w])                                           # ...nor followed by letters: 3rd, 2x, 10am
""", re.VERBOSE)
_ORDINAL_RE = re.compile(r"(?<![\w.])(\d+)(?:st|nd|rd|th)\b")


@dataclass
class Found:
    text: str
    value: float
    unit: float          # resolution implied by the displayed precision
    percent: bool
    start: int


@dataclass
class NumberCheck:
    ok: bool
    checked: list[dict] = field(default_factory=list)     # {text, value, matched_path, fact_value}
    unmatched: list[dict] = field(default_factory=list)   # {text, value}

    def as_dict(self) -> dict:
        return {"ok": self.ok, "checked_numbers": self.checked, "unmatched_numbers": self.unmatched}


def extract_numbers(text: str) -> list[Found]:
    out = []
    for m in NUM_RE.finditer(text):
        raw = m.group("num").replace(",", "")
        decimals = len(raw.split(".")[1]) if "." in raw else 0
        mult = _MULT[m.group("suf").strip().lower()] if m.group("suf") else 1.0
        value = float(raw) * mult
        out.append(Found(m.group(0).strip(), value, (10 ** -decimals) * mult, bool(m.group("pct")), m.start()))
    for m in _ORDINAL_RE.finditer(text):   # "the 3rd" -> 3 (dates written as ordinals)
        out.append(Found(m.group(0), float(m.group(1)), 1.0, False, m.start()))
    return sorted(out, key=lambda f: f.start)


def _date_parts(s: str) -> list[float]:
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        try:
            d = date.fromisoformat(s)
        except ValueError:
            return []
        return [d.year, d.month, d.day]
    if len(s) <= 10:
        return [dt.year, dt.month, dt.day]
    hour12 = dt.hour % 12 or 12
    return [dt.year, dt.month, dt.day, dt.hour, hour12, dt.minute]


def fact_values(facts: Any, path: str = "") -> Iterator[tuple[str, float]]:
    """Every number in a facts structure, with its JSON path."""
    if isinstance(facts, bool) or facts is None:
        return
    if isinstance(facts, (int, float, Decimal)):
        v = float(facts)
        if math.isfinite(v):
            yield path or "$", v
    elif isinstance(facts, (date, datetime)):
        for v in _date_parts(facts.isoformat()):
            yield f"{path}(date)", float(v)
    elif isinstance(facts, str):
        s = facts.strip()
        if re.fullmatch(r"-?\d+(?:\.\d+)?", s):
            yield path, float(s)
        elif re.match(r"\d{4}-\d{2}-\d{2}", s):
            for v in _date_parts(s):
                yield f"{path}(date)", float(v)
    elif isinstance(facts, dict):
        for k, v in facts.items():
            yield from fact_values(v, f"{path}.{k}" if path else str(k))
    elif isinstance(facts, (list, tuple)):
        for i, v in enumerate(facts):
            yield from fact_values(v, f"{path}[{i}]")


def _matches(found: Found, fact: float) -> bool:
    tol = found.unit / 2 + 1e-9 * max(1.0, abs(fact))
    candidates = [abs(fact)]
    if found.percent:
        candidates.append(abs(fact) * 100)
    return any(abs(found.value - c) <= tol for c in candidates)


def check_numbers(text: str, facts: Any) -> NumberCheck:
    values = list(fact_values(facts))
    res = NumberCheck(ok=True)
    for f in extract_numbers(text):
        hit = next(((p, v) for p, v in values if _matches(f, v)), None)
        if hit:
            res.checked.append({"text": f.text, "value": f.value, "matched_path": hit[0], "fact_value": hit[1]})
        else:
            res.unmatched.append({"text": f.text, "value": f.value})
    res.ok = not res.unmatched
    return res
