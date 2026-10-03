"""Generate synthetic report files, derived from the truth world (gen/truth.py).

    python -m goodwill_pulse.gen.generate --start 2026-08-01 --demo-day 2026-10-03

Writes:
  data/samples/history/  one Upright paid-orders file per Pacific day, one Cash Monkey file per UTC day
  data/samples/demo/     the demo day's reports, pulled at 10 PM Eastern (what staff would download tonight)
  data/samples/messy/    the same demo day with problems: duplicate rows, a renamed column, Cash Monkey missing

The orders are exactly the truth world's Upright (tool = upright) and Cash Monkey (tool = cashmonkey) orders, so the
Daily Pulse agrees with the marketplace DBs. The truth world is generated in memory (same seed -> same world as
data/sources/_truth.duckdb); no DuckDB file is read or written here.
"""
from __future__ import annotations

import argparse
import random
import shutil
from collections import defaultdict
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pandas as pd

from ..config import SAMPLES_DIR
from . import truth
from .world import Order
from .writers import write_cashmonkey_orders, write_upright_paid_orders

ET = ZoneInfo("America/New_York")
PT = ZoneInfo("America/Los_Angeles")
UTC = ZoneInfo("UTC")

UPRIGHT_CHANNEL_TEXT = {"shopgoodwill": "Shopgoodwill", "ebay": "eBay", "goodwillfinds": "GoodwillFinds"}
CM_CHANNEL_TEXT = {"amazon": "Amazon-MF", "ebay": "eBay", "goodwillbooks": "Goodwillbooks"}


def truth_orders(seed: int = 7, start: date | None = None) -> tuple[dict[date, list[Order]], dict[date, list[Order]]]:
    """Upright orders keyed by Pacific paid date, Cash Monkey orders keyed by UTC paid date."""
    w = truth.generate(seed=seed)
    o = w["orders"]
    if start is not None:
        o = o[o["paid_at"] >= pd.Timestamp(start - timedelta(days=2), tz="UTC")]
    lines = (w["order_lines"].merge(w["items"][["item_id", "store_id", "category", "title"]], on="item_id")
             .sort_values(["order_id", "line_no"]))
    lines = lines[lines["order_id"].isin(o["order_id"])]
    o = o.merge(w["buyers"][["buyer_id", "native_buyer_ref"]], on="buyer_id", how="left")
    by_order: dict[str, list[dict]] = defaultdict(list)
    for r in lines.itertuples(index=False):
        by_order[r.order_id].append({"store": r.store_id, "category": r.category, "title": r.title, "sku": r.item_id,
                                     "price": float(r.sale_price), "fees": float(r.fee_alloc)})
    upright: dict[date, list[Order]] = defaultdict(list)
    cashmonkey: dict[date, list[Order]] = defaultdict(list)
    for r in o.itertuples(index=False):
        paid = r.paid_at.to_pydatetime().astimezone(UTC)
        ls = by_order[r.order_id]
        if r.tool == "upright":
            upright[paid.astimezone(PT).date()].append(Order(
                system="upright", channel_raw=UPRIGHT_CHANNEL_TEXT[r.channel], order_id=r.marketplace_order_id,
                source_order_id=r.upright_order_id, buyer=r.native_buyer_ref, paid_at=paid, lines=ls,
                shipping=float(r.shipping_charged), payment_type=r.payment_type, tax_amount=float(r.tax),
                handling=float(r.handling), final_value_fee=float(r.marketplace_fee),
                payment_fee=float(r.payment_fee)))
        else:
            per_unit_label = round(float(r.shipping_label_cost) / max(1, len(ls)), 2)
            for line in ls:
                line["ship_cost"] = per_unit_label
            cashmonkey[paid.date()].append(Order(
                system="cashmonkey", channel_raw=CM_CHANNEL_TEXT[r.channel], order_id=r.marketplace_order_id,
                source_order_id="", buyer=r.native_buyer_ref, paid_at=paid, lines=ls,
                shipping=float(r.shipping_charged), payment_type=r.payment_type))
    return upright, cashmonkey


def generate(start: date, demo_day: date, cutoff_hour: int = 22, seed: int = 7) -> dict:
    upright, cashmonkey = truth_orders(seed, start)
    hist, demo, messy = (SAMPLES_DIR / d for d in ("history", "demo", "messy"))
    for d in (hist, demo, messy):
        shutil.rmtree(d, ignore_errors=True)
        d.mkdir(parents=True)

    cutoff = datetime.combine(demo_day, time(cutoff_hour), ET).astimezone(UTC)
    counts = {"history": 0, "demo": 0, "messy": 0}

    day = start
    while day <= demo_day + timedelta(days=1):
        up = [o for o in upright.get(day, []) if o.paid_at < cutoff]
        cm = [o for o in cashmonkey.get(day, []) if o.paid_at < cutoff]

        if day < demo_day:
            write_upright_paid_orders(hist, day, day, up)
            counts["history"] += 1
        elif day == demo_day:
            write_upright_paid_orders(demo, day, day, up)
            write_upright_paid_orders(messy, day, day, up, copy_no=2, duplicate_rows=2,
                                      rename={"Subtotal": "Sub Total"}, rng=random.Random(seed))
            counts["demo"] += 1
            counts["messy"] += 1

        # Cash Monkey works in UTC days. Tonight's pull covers yesterday-UTC through now.
        if day < demo_day:
            write_cashmonkey_orders(hist, cm, datetime.combine(day + timedelta(days=1), time(13, 22, 56)))
            counts["history"] += 1
        else:
            if day == demo_day:
                tonight = cm
            else:
                tonight += cm
                write_cashmonkey_orders(demo, tonight, cutoff.replace(tzinfo=None))
                counts["demo"] += 1
        day += timedelta(days=1)
    return counts


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--start", type=date.fromisoformat, default=date(2026, 8, 1))
    p.add_argument("--demo-day", type=date.fromisoformat, default=date(2026, 10, 3))
    p.add_argument("--seed", type=int, default=7)
    a = p.parse_args()
    print(generate(a.start, a.demo_day, seed=a.seed))


if __name__ == "__main__":
    main()
