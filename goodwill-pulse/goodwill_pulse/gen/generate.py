"""Generate synthetic report files.

    python -m goodwill_pulse.gen.generate --start 2026-08-01 --demo-day 2026-10-03

Writes:
  data/samples/history/  one Upright paid-orders file per Pacific day, one Cash Monkey file per UTC day
  data/samples/demo/     the demo day's reports, pulled at 10 PM Eastern (what staff would download tonight)
  data/samples/messy/    the same demo day with problems: duplicate rows, a renamed column, Cash Monkey missing
"""
from __future__ import annotations

import argparse
import random
import shutil
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from ..config import SAMPLES_DIR
from .world import World
from .writers import write_cashmonkey_orders, write_upright_paid_orders

ET = ZoneInfo("America/New_York")
UTC = ZoneInfo("UTC")


def generate(start: date, demo_day: date, cutoff_hour: int = 22, seed: int = 7) -> dict:
    world = World(seed)
    hist, demo, messy = (SAMPLES_DIR / d for d in ("history", "demo", "messy"))
    for d in (hist, demo, messy):
        shutil.rmtree(d, ignore_errors=True)
        d.mkdir(parents=True)

    cutoff = datetime.combine(demo_day, time(cutoff_hour), ET).astimezone(UTC)
    counts = {"history": 0, "demo": 0, "messy": 0}

    day = start
    while day <= demo_day + timedelta(days=1):
        up = [o for o in world.upright_orders(day) if o.paid_at < cutoff]
        cm = [o for o in world.cashmonkey_orders(day) if o.paid_at < cutoff]

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
