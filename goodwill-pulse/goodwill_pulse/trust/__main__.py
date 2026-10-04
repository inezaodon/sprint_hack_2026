"""Trust engine test runner: prints a pass/fail table (the screenshot for the slide). Exit 1 on any failure.

    python -m goodwill_pulse.trust                          # built-in fixture and its planted answers
    python -m goodwill_pulse.trust --break total_sales      # sabotage one formula: the table must go red
    python -m goodwill_pulse.trust --odin                   # Odin's harmonized.duckdb: his answer key + kpi.py cross-check
    python -m goodwill_pulse.trust --db data/x.duckdb --answers answers.json [--as-of 2026-10-04]
    python -m goodwill_pulse.trust --catalog                # print the metric catalog as JSON
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from decimal import Decimal

import duckdb

from . import engine, fixture, odin
from .catalog import catalog_json
from .engine import COUNTED, PAID, REFUND, TrustError, _close, compute, dataset_checks

# Realistic bugs for --break: each fails at least one check and one planted answer.
BUGS = {
    "total_sales": ("forgets to subtract refunds",
                    lambda rows: sum((Decimal(r.subtotal) for r in rows if r.status == PAID), Decimal(0))),
    "orders": ("counts lines instead of orders",
               lambda rows: Decimal(sum(1 for r in rows if r.status == PAID))),
    "average_sale": ("divides by lines instead of orders",
                     lambda rows: engine.total_sales(rows) / max(1, sum(1 for r in rows if r.status == PAID))),
    "shipping_charged": ("counts unpaid orders",
                         lambda rows: sum((Decimal(r.shipping_charged) for r in rows), Decimal(0))),
    "items_sold": ("counts unpaid orders", lambda rows: sum((Decimal(r.items) for r in rows), Decimal(0))),
    "customers": ("ignores the platform when matching buyers",
                  lambda rows: Decimal(len({r.buyer_id for r in rows if r.status == PAID}))),
}

GREEN, RED, DIM, BOLD, RESET = "\033[32m", "\033[31m", "\033[2m", "\033[1m", "\033[0m"


def _matches(result: dict, expect) -> tuple[bool, str]:
    if isinstance(expect, dict):
        got = {str(r["key"]): r["value"] for r in result["rows"]}
        bad = [k for k, v in expect.items() if not _close(got.get(k, 0 if v == 0 else None), v)]
        shown = ", ".join(f"{k}={got.get(k, 0)}" for k in expect)
        return not bad, shown if not bad else f"expected {expect}, got {shown}"
    return _close(result["value"], expect), f"{result['value']}" + ("" if _close(result["value"], expect)
                                                                   else f" (expected {expect})")


def run(con: duckdb.DuckDBPyConnection, answers: list[dict], as_of: date | None) -> list[tuple]:
    """[(section, name, ok, detail)] for every planted answer, every check on it, and the dataset checks."""
    lines = []
    for c in dataset_checks(con):
        lines.append(("Data", c["label"], c["ok"], c["detail"]))
    for a in answers:
        q = a.get("question", a["metric"])
        try:
            res = compute(con, a["metric"], a.get("filters"), a.get("breakdown"), a.get("period", "yesterday"), as_of)
        except TrustError as e:
            lines.append((q, "answer", False, str(e)))
            continue
        ok, detail = _matches(res, a["expect"])
        lines.append((q, "planted answer", ok, detail))
        for c in res["checks"]:
            lines.append((q, c["label"], c["ok"], c["detail"]))
    return lines


def print_table(lines: list[tuple], color: bool) -> None:
    c = (lambda code, s: f"{code}{s}{RESET}") if color else (lambda code, s: s)
    width = max(len(n) for _, n, _, _ in lines) + 2
    section = None
    for sec, name, ok, detail in lines:
        if sec != section:
            print("\n" + c(BOLD, sec))
            section = sec
        mark = c(GREEN, "PASS") if ok else c(RED, "FAIL")
        print(f"  {mark}  {name:<{width}} {c(DIM, detail)}")
    failed = sum(1 for l in lines if not l[2])
    total = len(lines)
    summary = f"{total - failed}/{total} passed" + (f", {failed} FAILED" if failed else "")
    print("\n" + c(RED if failed else GREEN, c(BOLD, summary)))


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m goodwill_pulse.trust", description=__doc__.splitlines()[0])
    p.add_argument("--db", help="DuckDB file with sales_line and books (default: built-in fixture)")
    p.add_argument("--odin", nargs="?", const=str(odin.HARMONIZED), metavar="PATH",
                   help="run on Odin's harmonized DB (default data/harmonized.duckdb) via the trust views")
    p.add_argument("--answers", help="JSON list of planted answers (default: the fixture's)")
    p.add_argument("--as-of", help="'today' for named periods, YYYY-MM-DD (default: day after the latest data)")
    p.add_argument("--break", dest="broken", choices=sorted(BUGS), help="sabotage one metric's formula")
    p.add_argument("--catalog", action="store_true", help="print the metric catalog as JSON and exit")
    args = p.parse_args(argv)

    if args.catalog:
        print(json.dumps(catalog_json(), indent=2))
        return 0

    if args.odin:
        con, default_answers, default_as_of = odin.connect(args.odin), odin.answers_from_demo_data(), None
    elif args.db:
        con, default_answers, default_as_of = duckdb.connect(args.db, read_only=True), [], None
    else:
        con, default_answers, default_as_of = fixture.connect(), fixture.ANSWERS, fixture.AS_OF
    answers = json.load(open(args.answers)) if args.answers else default_answers
    as_of = date.fromisoformat(args.as_of) if args.as_of else default_as_of

    original = dict(engine.FUNCS)
    if args.broken:
        desc, bug = BUGS[args.broken]
        engine.FUNCS[args.broken] = bug
        print(f"Sabotaged {args.broken}: {desc}")
    try:
        lines = run(con, answers, as_of)
        if args.odin:
            lines += odin.cross_check(con)
    finally:
        engine.FUNCS.update(original)
    print_table(lines, color=sys.stdout.isatty())
    return 0 if all(ok for _, _, ok, _ in lines) else 1


if __name__ == "__main__":
    sys.exit(main())
