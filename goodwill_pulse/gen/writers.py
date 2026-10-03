"""Write synthetic orders out in each tool's native export shape."""
from __future__ import annotations

import csv
import random
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from .world import Order

PT = ZoneInfo("America/Los_Angeles")

UPRIGHT_HEADER = [
    "Upright Order ID", "Channel", "Channel Order ID", "Secondary Channel", "Channel Buyer",
    "Order Item Count", "Payment Method", "Payment Type", "Total", "Subtotal", "Shipping Total",
    "Shipping Discount", "Handling", "Tax Total", "Donation", "Currency", "Final Value Fee",
    "Payment Fee", "Paid At",
]

CASHMONKEY_HEADER = [
    "Order Date", "Order ID", "Account", "Channel", "SKU", "ASIN", "Title", "Condition", "Source",
    "Quantity", "Item Price", "Shipping Credit", "Market Fees", "Shipping Cost", "Net Revenue", "Buyer",
]


def _money(x: float) -> str:
    return f"{x:.2f}".rstrip("0").rstrip(".") if x else "0"


def upright_rows(orders: list[Order]) -> list[list[str]]:
    rows = []
    for o in sorted(orders, key=lambda o: o.paid_at):
        handling = 3.0 * o.item_count
        tax = round(o.subtotal * o.tax_rate, 2)
        total = round(o.subtotal + o.shipping + handling + tax, 2)
        fvf = round(o.subtotal * 0.1325, 2) if o.channel_raw == "eBay" else 0.0
        pay_fee = round(total * 0.029 + 0.30, 2) if o.payment_type == "PayPal" else 0.0
        rows.append([
            o.source_order_id, o.channel_raw, o.order_id, "", o.buyer, str(o.item_count), "", o.payment_type,
            _money(total), _money(o.subtotal), _money(o.shipping), "", _money(handling), _money(tax), "0", "USD",
            _money(fvf) if fvf else "", _money(pay_fee), o.paid_at.astimezone(PT).strftime("%m/%d/%Y %H:%M:%S"),
        ])
    return rows


def write_upright_paid_orders(path_dir: Path, start: date, end: date, orders: list[Order],
                              copy_no: int | None = None, rename: dict | None = None,
                              duplicate_rows: int = 0, rng: random.Random | None = None) -> Path:
    """`paid_orders_MM-DD-YYYY_MM-DD-YYYY.csv`, ends with a SUM totals row like the real export."""
    header = [rename.get(h, h) if rename else h for h in UPRIGHT_HEADER]
    rows = upright_rows(orders)
    if duplicate_rows and rows:
        rng = rng or random.Random(1)
        for r in rng.sample(rows, min(duplicate_rows, len(rows))):
            rows.insert(rng.randrange(len(rows)), list(r))
    subtotal = round(sum(float(r[9]) for r in rows), 2)
    shipping = round(sum(float(r[10]) for r in rows), 2)
    totals = [""] * len(header)
    totals[9], totals[10] = f"{subtotal:.2f}", f"{shipping:.2f}"
    suffix = f" ({copy_no})" if copy_no else ""
    name = f"paid_orders_{start:%m-%d-%Y}_{end:%m-%d-%Y}{suffix}.csv"
    path = path_dir / name
    with path.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)
        w.writerow(totals)
    return path


def write_cashmonkey_orders(path_dir: Path, orders: list[Order], generated_at: datetime,
                            rng: random.Random | None = None) -> Path:
    """`orders2023-YYYYMMDD-HHMMSS-NNNNN.csv`: one line per unit, UTC timestamps, fees pro-rated per unit."""
    rng = rng or random.Random(generated_at.toordinal())
    rows = []
    for o in sorted(orders, key=lambda o: o.paid_at):
        fee_rate = {"Amazon-MF": 0.15, "eBay": 0.1325, "Goodwillbooks": 0.05}[o.channel_raw]
        per_unit_ship_credit = round(o.shipping / o.item_count, 2)
        for line in o.lines:
            fees = round(line["price"] * fee_rate + (1.80 if o.channel_raw == "Amazon-MF" else 0.30), 2)
            ship_cost = round(rng.uniform(3.2, 4.4), 2)
            net = round(line["price"] + per_unit_ship_credit - fees - ship_cost, 2)
            rows.append([
                o.paid_at.strftime("%Y-%m-%d %H:%M:%S"), o.order_id,
                "276 - Goodwill Michiana", o.channel_raw, line["sku"],
                f"0{rng.randint(100000000, 999999999)}", line["title"],
                rng.choice(["Used - Good", "Used - Very Good", "Used - Acceptable", "Used - Like New"]),
                line["store"], "1", f"{line['price']:.2f}", f"{per_unit_ship_credit:.2f}",
                f"{fees:.2f}", f"{ship_cost:.2f}", f"{net:.2f}", o.buyer,
            ])
    name = f"orders2023-{generated_at:%Y%m%d-%H%M%S}-{rng.randint(10000, 99999)}.csv"
    path = path_dir / name
    with path.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(CASHMONKEY_HEADER)
        w.writerows(rows)
    return path


def next_day(d: date) -> date:
    return d + timedelta(days=1)
