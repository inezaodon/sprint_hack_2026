"""Grounded narratives (PLAN C3): 2-4 sentences written only from a facts JSON, then number-checked.

    pulse_narrative(build_pulse(con, d))      -> {"text", "engine", "checked_numbers", "unmatched_numbers", ...}
    month_narrative(kpi.scorecard(con, m))    -> same shape

Flow: compact facts (+ derived percentages, so the writer never has to do arithmetic) -> Claude -> number check
-> one regeneration with the failures spelled out -> otherwise the deterministic template (which passes the checker
by construction; tests prove it). Narratives describe; they never recommend inventory or pricing actions.
"""
from __future__ import annotations

import json
import re
from datetime import date, datetime
from typing import Any

from . import client as ai
from .numbers import check_numbers

# Debie's boundary: show the data, don't prescribe what to list, price or stock.
PRESCRIPTIVE_RE = re.compile(
    r"\b(should|recommend\w*|suggest\w*|consider\w*|must|need(?:s)? to|ought|advis\w+|restock\w*|re-?price\w*|"
    r"mark ?down\w*|cut prices|raise prices|list more|stop listing|pull (?:back|inventory)|"
    r"(?:increase|reduce|boost|shift) (?:inventory|listings|stock))\b", re.I)

NARRATIVE_SYSTEM = """You write the short summary that sits above Goodwill's e-commerce numbers.

Rules, all mandatory:
- Write 2 to 4 plain sentences for a busy operations leader. No headings, bullets, or markdown.
- Use ONLY numbers that appear in the facts JSON you are given, rounded the way a person would say them \
($10,505 or $10.5K; 16.2% or 16%). Percent changes and shares are already computed under "derived"; never \
compute new numbers, never add counts, ranks, or dates that are not in the facts.
- Do not write digits that are not facts (no "2 of 4", no "#1"); use words instead ("two", "the top").
- Describe what happened; do not recommend actions or decisions (no inventory, pricing, listing, or staffing \
advice, no "should"/"consider"). The reader decides what to do.
- If the facts say data is incomplete or a report is missing, say so plainly in one clause.

Return JSON: {"narrative": "<the sentences>"}."""

NARRATIVE_SCHEMA = {
    "type": "object",
    "properties": {"narrative": {"type": "string"}},
    "required": ["narrative"],
    "additionalProperties": False,
}


# ---------------------------------------------------------------------------------------------------------------
# formatting helpers shared with the fallback answers in ask.py
def money(v: float) -> str:
    return f"${v:,.0f}" if abs(v) >= 1000 else f"${v:,.2f}"


def pct_change(new: float | None, old: float | None) -> float | None:
    if new is None or old in (None, 0):
        return None
    return round((new - old) / abs(old) * 100, 1)


def _clock(iso: str) -> str:
    t = datetime.fromisoformat(iso)
    h = t.hour % 12 or 12
    return f"{h}{'' if t.minute == 0 else f':{t.minute:02d}'} {'AM' if t.hour < 12 else 'PM'}"


def _jsonable(o: Any) -> Any:
    if isinstance(o, dict):
        return {k: _jsonable(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_jsonable(v) for v in o]
    if isinstance(o, (date, datetime)):
        return o.isoformat()
    if hasattr(o, "__float__") and not isinstance(o, (int, float, bool)):
        return float(o)
    return o


# ---------------------------------------------------------------------------------------------------------------
# facts
def pulse_facts(pulse: dict) -> dict:
    """The subset of build_pulse() output a narrative may use, plus derived percentages."""
    tot = pulse["total"]
    rows = [{k: r.get(k) for k in ("label", "revenue", "customers", "revenue_last_week", "customers_last_week",
                                   "revenue_mtd")} for r in pulse["rows"]]
    shares = {r["label"]: round(r["revenue"] / tot["revenue"] * 100, 1) for r in rows if tot["revenue"]}
    derived = {
        "total_revenue_change_vs_same_day_last_week_pct": pct_change(tot["revenue"], tot.get("revenue_last_week")),
        "total_orders_change_vs_same_day_last_week_pct": pct_change(tot["customers"], tot.get("customers_last_week")),
        "share_of_day_revenue_pct": shares,
        "revenue_change_vs_same_day_last_week_pct": {
            r["label"]: pct_change(r["revenue"], r["revenue_last_week"]) for r in rows},
    }
    return _jsonable({
        "business_date": pulse["business_date"],
        "weekday": pulse.get("weekday"),
        "note": "'customers' = orders paid that day; revenue basis = " + str(pulse.get("revenue_basis", "subtotal")),
        "total": {k: tot.get(k) for k in ("revenue", "customers", "revenue_last_week", "customers_last_week",
                                          "revenue_mtd")},
        "rows": rows,
        "derived": derived,
        "complete": pulse.get("complete"),
        "as_of": pulse.get("as_of"),
        "sources": [{"label": s.get("label"), "status": s.get("status"), "covered_through": s.get("covered_through")}
                    for s in pulse.get("sources", [])],
        "open_exceptions": pulse.get("open_exceptions"),
    })


def _card_facts(card: dict) -> dict:
    unit, v = card.get("unit"), card.get("value")
    pm, py = card.get("prior_month"), card.get("prior_year")
    out = {k: card.get(k) for k in ("id", "label", "pillar", "unit", "value", "prior_month", "prior_year", "status",
                                    "availability", "provisional")}
    if card.get("breakdown"):
        out["top_3"] = [{"key": b.get("key"), "value": b.get("value")} for b in card["breakdown"][:3]]
    if unit == "pct":
        out["change_vs_prior_month_pts"] = None if v is None or pm is None else round((v - pm) * _pct_scale(v, pm), 1)
        out["change_vs_prior_year_pts"] = None if v is None or py is None else round((v - py) * _pct_scale(v, py), 1)
    else:
        out["change_vs_prior_month_pct"] = pct_change(v, pm)
        out["change_vs_prior_year_pct"] = pct_change(v, py)
    return out


def _pct_scale(*vals: float | None) -> float:
    """KPIs may store percentages as fractions (0.52) or as numbers (52); points are always in percent units."""
    return 100.0 if all(v is None or abs(v) <= 1.5 for v in vals) else 1.0


def scorecard_facts(scorecard: dict, month: date | str | None = None) -> dict:
    """Compact facts from kpi.scorecard(): anchor + scorecard cards without trend arrays (13 months of trend
    numbers would let almost any invented number 'match')."""
    seen, cards = set(), []
    for c in list(scorecard.get("anchors", [])) + list(scorecard.get("scorecard", [])):
        if c.get("id") in seen:
            continue
        seen.add(c.get("id"))
        cards.append(_card_facts(c))
    anchors = {c.get("id") for c in scorecard.get("anchors", [])}
    m = month or scorecard.get("month")
    return _jsonable({
        "month": m.isoformat() if isinstance(m, date) else m,
        "as_of": scorecard.get("as_of"),
        "filters": {k: v for k, v in (scorecard.get("filters") or {}).items() if v},
        "anchor_ids": [c["id"] for c in cards if c["id"] in anchors],
        "kpis": cards,
        "derived": {"kpis_not_available_count": sum(1 for c in cards if c.get("value") is None)},
    })


# ---------------------------------------------------------------------------------------------------------------
# deterministic templates (must pass the checker; tests assert it)
def _fmt_kpi(c: dict) -> str:
    v, unit = c["value"], c.get("unit")
    if unit == "pct":
        return f"{v * _pct_scale(v):.1f}%"
    if unit in ("usd", "usd_per_hour"):
        return money(v) + (" per labor hour" if unit == "usd_per_hour" else "")
    if unit == "days":
        return f"{v:,.1f} days"
    return f"{v:,.0f}"


def _change_clause(c: dict, key: str, versus: str) -> str | None:
    pts = c.get(f"{key}_pts")
    pct = c.get(f"{key}_pct")
    if pts is not None:
        return f"{'up' if pts >= 0 else 'down'} {abs(pts):.1f} points {versus}"
    if pct is not None:
        return f"{'up' if pct >= 0 else 'down'} {abs(pct):.1f}% {versus}"
    return None


def template_pulse(f: dict) -> str:
    d = date.fromisoformat(f["business_date"])
    tot, der = f["total"], f["derived"]
    s = [f"On {f.get('weekday') or d.strftime('%A')}, {d:%B} {d.day}, e-commerce revenue was {money(tot['revenue'])} "
         f"from {tot['customers']:,} orders"]
    ch = der.get("total_revenue_change_vs_same_day_last_week_pct")
    if ch is not None:
        s[0] += (f", {'up' if ch >= 0 else 'down'} {abs(ch):.1f}% from {money(tot['revenue_last_week'])} "
                 f"on the same day last week")
    s[0] += "."
    rows = sorted([r for r in f["rows"] if r["revenue"]], key=lambda r: -r["revenue"])
    if rows:
        top = rows[0]
        sent = f"{top['label']} led with {money(top['revenue'])}"
        share = der["share_of_day_revenue_pct"].get(top["label"])
        if share is not None:
            sent += f" ({share:.1f}% of the day)"
        if len(rows) > 1:
            sent += f", followed by {rows[1]['label']} at {money(rows[1]['revenue'])}"
        s.append(sent + ".")
    tail = f"Month-to-date revenue is {money(tot['revenue_mtd'])}"
    if not f.get("complete"):
        gaps = []
        for src in f.get("sources", []):
            if src["status"] == "missing":
                gaps.append(f"the {src['label']} report has not arrived")
            elif src["status"] == "needs_mapping":
                gaps.append(f"the {src['label']} report is waiting for a column mapping")
            elif src["status"] == "partial" and src.get("covered_through"):
                gaps.append(f"{src['label']} only covers through {_clock(src['covered_through'])}")
        if gaps:
            tail += ", but the day is not complete yet: " + "; ".join(gaps)
    s.append(tail + ".")
    return " ".join(s)


def template_month(f: dict) -> str:
    m = date.fromisoformat(f["month"][:10]) if f.get("month") else None
    when = f"In {m:%B %Y}" if m else "This month"
    kpis = [c for c in f["kpis"] if c.get("value") is not None]
    anchors = [c for c in kpis if c["id"] in f.get("anchor_ids", [])]
    s = []
    parts = []
    for c in anchors[:3]:
        clause = f"{c['label']} was {_fmt_kpi(c)}"
        yoy = _change_clause(c, "change_vs_prior_year", "year over year")
        parts.append(clause + (f" ({yoy})" if yoy else ""))
    if parts:
        s.append(f"{when}, " + ("; ".join(parts[:-1]) + ", and " + parts[-1] if len(parts) > 1 else parts[0]) + ".")
    movers = [c for c in kpis if c["id"] not in f.get("anchor_ids", []) and not c.get("top_3")]

    def size(c: dict) -> float:
        return abs(c.get("change_vs_prior_month_pct") or c.get("change_vs_prior_month_pts") or 0)
    movers = sorted([c for c in movers if size(c)], key=size, reverse=True)[:2]
    for c in movers:
        mom = _change_clause(c, "change_vs_prior_month", "from the prior month")
        s.append(f"{c['label']} was {_fmt_kpi(c)}, {mom}.")
    if not s:
        s.append(f"{when}, no scorecard KPIs could be computed from the data yet.")
    top = next((c for c in kpis if c["id"] == "top_categories_by_revenue" and c.get("top_3")), None)
    if top and top["top_3"][0].get("value") is not None and len(s) < 3:
        s.append(f"{top['top_3'][0]['key']} was the top category by revenue at {money(top['top_3'][0]['value'])}.")
    if f.get("as_of") and any(c.get("provisional") for c in anchors) and len(s) < 4:
        a = date.fromisoformat(f["as_of"][:10])
        s.append(f"Some figures are provisional because data runs through {a:%B} {a.day}.")
    n = f["derived"].get("kpis_not_available_count") or 0
    if n:
        s.append(f"{n} scorecard {'KPI is' if n == 1 else 'KPIs are'} not yet available from the data.")
    return " ".join(s[:4])


# ---------------------------------------------------------------------------------------------------------------
def _problems(text: str, facts: dict) -> tuple[Any, list[str]]:
    chk = check_numbers(text, facts)
    probs = []
    if chk.unmatched:
        probs.append("These numbers are not in the facts: " + ", ".join(u["text"] for u in chk.unmatched) + ".")
    bad = sorted({m.group(0) for m in PRESCRIPTIVE_RE.finditer(text)})
    if bad:
        probs.append("These words recommend actions, which is not allowed: " + ", ".join(bad) + ".")
    n = len(re.findall(r"[.!?](?:\s|$)", text.strip() + " "))
    if not 1 <= n <= 4:
        probs.append(f"Write 2 to 4 sentences (you wrote {n}).")
    return chk, probs


def _claude_narrative(kind: str, facts: dict) -> tuple[str | None, Any, list[dict]]:
    """Claude path with one regeneration. Returns (text or None, number check, rejected attempts)."""
    messages: list[dict] = [{"role": "user", "content":
                             f"Facts JSON ({kind}):\n{json.dumps(facts, sort_keys=True)}\n\nWrite the summary."}]
    rejected = []
    for attempt in range(2):
        try:
            resp = ai.create(f"narrative.{kind}", system=ai.cached_system(NARRATIVE_SYSTEM), messages=messages,
                             max_tokens=2048, output_config={"effort": "low", **ai.json_schema(NARRATIVE_SCHEMA)})
            text = str(ai.json_of(resp).get("narrative", "")).strip()
        except ai.AIUnavailable:
            return None, None, rejected
        chk, probs = _problems(text, facts)
        if text and not probs:
            return text, chk, rejected
        rejected.append({"text": text, "problems": probs, "unmatched_numbers": chk.unmatched})
        messages += [{"role": "assistant", "content": resp.content},
                     {"role": "user", "content": " ".join(probs) + " Rewrite the summary following every rule."}]
    return None, None, rejected


def _narrate(kind: str, facts: dict, template) -> dict:
    rejected: list[dict] = []
    reason = "no ANTHROPIC_API_KEY" if not ai.ai_available() else ""
    if ai.ai_available():
        text, chk, rejected = _claude_narrative(kind, facts)
        if text:
            ai.log_engine(f"narrative.{kind}", "claude", f"{len(rejected)} regeneration(s)")
            return {"text": text, "engine": "claude", "checked_numbers": chk.checked, "unmatched_numbers": [],
                    "rejected_attempts": rejected, "facts": facts}
        reason = "claude output failed the number check twice" if rejected else "claude call failed"
    text = template(facts)
    chk = check_numbers(text, facts)
    if not chk.ok:   # a template bug; never publish an unchecked number silently
        ai.log.error("ai[narrative.%s] template failed number check: %s", kind, chk.unmatched)
    ai.log_engine(f"narrative.{kind}", "fallback", reason)
    return {"text": text, "engine": "fallback", "fallback_reason": reason, "checked_numbers": chk.checked,
            "unmatched_numbers": chk.unmatched, "rejected_attempts": rejected, "facts": facts}


def pulse_narrative(pulse: dict) -> dict:
    """`pulse` is build_pulse() output (or already-compacted pulse_facts())."""
    facts = pulse if "derived" in pulse else pulse_facts(pulse)
    return _narrate("pulse", facts, template_pulse)


def month_narrative(scorecard: dict, month: date | str | None = None) -> dict:
    """`scorecard` is kpi.scorecard() output (or already-compacted scorecard_facts())."""
    facts = scorecard if "kpis" in scorecard else scorecard_facts(scorecard, month)
    return _narrate("month", facts, template_month)
