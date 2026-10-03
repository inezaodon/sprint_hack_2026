"""Synthetic Goodwill Michiana e-commerce activity.

Everything here is invented. It is shaped after what Goodwill showed us:
- ~128 Upright paid orders on a weekday (deck slide 26: 9/30/2026, rows 2-129, $13,247 subtotal)
- handling of $3 per item, tax on most orders, ShopGoodwill as the main channel
- books sold through Cash Monkey on Amazon (merchant fulfilled), eBay and Goodwillbooks, one row per unit
- 24 stores; the originating store gets the revenue credit
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

UTC = ZoneInfo("UTC")
ET = ZoneInfo("America/New_York")

STORES = [f"Store{n:02d}" for n in range(1, 25)]
# uneven volume: a few large stores send most items online
STORE_WEIGHTS = [random.Random(n).uniform(0.3, 1.0) * (2.2 if n in (3, 9, 11, 13) else 1) for n in range(1, 25)]

GM_CATEGORIES = [  # (category, weight, median price, spread)
    ("Jewelry", 0.16, 95.0, 1.1),
    ("Collectibles", 0.14, 55.0, 0.8),
    ("Electronics", 0.12, 70.0, 0.7),
    ("Clothing", 0.18, 30.0, 0.5),
    ("Shoes", 0.08, 42.0, 0.5),
    ("Home Decor", 0.12, 45.0, 0.6),
    ("Toys & Games", 0.08, 35.0, 0.6),
    ("Art", 0.05, 70.0, 0.9),
    ("Watches", 0.07, 85.0, 0.9),
]
UPRIGHT_CHANNELS = [("Shopgoodwill", 0.84), ("eBay", 0.11), ("GoodwillFinds", 0.05)]
CM_CHANNELS = [("Amazon-MF", 0.70), ("eBay", 0.20), ("Goodwillbooks", 0.10)]
BOOK_TITLES = [
    "The Midnight Library", "Educated", "Where the Crawdads Sing", "Atomic Habits", "Becoming",
    "The Body Keeps the Score", "Sapiens", "Calculus: Early Transcendentals", "The Pragmatic Programmer",
    "Little Fires Everywhere", "The Very Hungry Caterpillar", "Dune", "Campbell Biology", "Lessons in Chemistry",
    "The Silent Patient", "Born a Crime", "Pride and Prejudice", "Organic Chemistry", "The Hobbit", "Rich Dad Poor Dad",
]
PAYMENT_TYPES = [("CreditCard", 0.45), ("PayPal", 0.25), ("ApplePay", 0.30)]


def _pick(rng: random.Random, pairs):
    return rng.choices([p[0] for p in pairs], weights=[p[1] for p in pairs])[0]


@dataclass
class Order:
    system: str                 # upright | cashmonkey
    channel_raw: str
    order_id: str               # channel order id
    source_order_id: str        # Upright order id (blank for Cash Monkey)
    buyer: str
    paid_at: datetime           # aware, UTC
    lines: list[dict] = field(default_factory=list)  # one dict per unit: store, category/title, price
    shipping: float = 0.0
    tax_rate: float = 0.0
    payment_type: str = ""
    # set when the order comes from the truth world (gen/truth.py); writers then use them as-is
    tax_amount: float | None = None
    handling: float | None = None
    final_value_fee: float | None = None
    payment_fee: float | None = None

    @property
    def item_count(self) -> int:
        return len(self.lines)

    @property
    def subtotal(self) -> float:
        return round(sum(l["price"] for l in self.lines), 2)


class World:
    def __init__(self, seed: int = 7):
        self.rng = random.Random(seed)
        self._upright_id = 24_100_000
        self._channel_id = 65_700_000
        self._amazon_seq = 0
        self.gm_buyers = [self._username() for _ in range(5200)]
        self.book_buyers = [f"B{self.rng.randint(10**9, 10**10 - 1)}" for _ in range(3800)]

    def _username(self) -> str:
        stems = ["thrift", "treasure", "vintage", "resell", "deal", "find", "collect", "shop", "hunt", "gem"]
        return f"{self.rng.choice(stems)}{self.rng.choice(['_', '', '.'])}{self.rng.randint(1, 9999)}"

    def _buyer(self, pool: list[str]) -> str:
        # a fifth of the pool buys repeatedly
        if self.rng.random() < 0.35:
            return pool[self.rng.randrange(len(pool) // 5)]
        return self.rng.choice(pool)

    def _paid_at(self, day: date, tz: ZoneInfo, peak_hour: float) -> datetime:
        hour = min(23.99, max(0.0, self.rng.gauss(peak_hour, 4.0)))
        local = datetime(day.year, day.month, day.day, tzinfo=tz) + timedelta(hours=hour)
        return local.astimezone(UTC)

    def _paid_at_wrapped(self, day: date, peak_utc_hour: float) -> datetime:
        """A time on this UTC day; US evening orders wrap to the early UTC hours of the same UTC day."""
        hour = self.rng.gauss(peak_utc_hour, 4.5) % 24
        return datetime(day.year, day.month, day.day, tzinfo=UTC) + timedelta(hours=hour)

    def _volume(self, day: date, base: float) -> int:
        weekday = [1.0, 1.08, 1.05, 1.02, 0.97, 0.92, 0.95][day.weekday()]
        season = 1 + 0.18 * (day.month in (11, 12)) - 0.08 * (day.month in (1, 2))
        growth = 1 + 0.0004 * (day - date(2025, 9, 1)).days     # slow year-over-year growth
        return max(0, int(self.rng.gauss(base * weekday * season * growth, base * 0.08)))

    def upright_orders(self, day: date) -> list[Order]:
        """Orders paid on `day` (Pacific calendar day, how ShopGoodwill runs)."""
        orders = []
        for _ in range(self._volume(day, 104)):
            channel = _pick(self.rng, UPRIGHT_CHANNELS)
            n_items = 1 if self.rng.random() < 0.9 else self.rng.choice([2, 2, 3, 4, 10])
            lines = []
            for _ in range(n_items):
                cat, _, median, spread = self.rng.choices(GM_CATEGORIES, weights=[c[1] for c in GM_CATEGORIES])[0]
                price = round(min(4000.0, max(3.0, self.rng.lognormvariate(0, spread) * median)), 2)
                store = self.rng.choices(STORES, weights=STORE_WEIGHTS)[0]
                lines.append({"store": store, "category": cat, "price": price})
            self._upright_id += self.rng.randint(1, 40)
            self._channel_id += self.rng.randint(1, 30)
            orders.append(Order(
                system="upright",
                channel_raw=channel,
                order_id=str(self._channel_id) if channel != "eBay" else f"{self.rng.randint(10,27)}-{self.rng.randint(10000,99999)}-{self.rng.randint(10000,99999)}",
                source_order_id=str(self._upright_id),
                buyer=self._buyer(self.gm_buyers),
                paid_at=self._paid_at(day, ZoneInfo("America/Los_Angeles"), 15.5),
                lines=lines,
                shipping=0.0 if self.rng.random() < 0.06 else round(self.rng.uniform(7.5, 15.5), 2),
                tax_rate=0.0 if self.rng.random() < 0.12 else round(self.rng.uniform(0.05, 0.08), 4),
                payment_type=_pick(self.rng, PAYMENT_TYPES),
            ))
        return orders

    def cashmonkey_orders(self, day: date) -> list[Order]:
        """Book orders on `day` (UTC calendar day, how Cash Monkey reports)."""
        orders = []
        for _ in range(self._volume(day, 58)):
            channel = _pick(self.rng, CM_CHANNELS)
            n_units = self.rng.choices([1, 2, 3], weights=[0.82, 0.13, 0.05])[0]
            store = self.rng.choices(STORES, weights=STORE_WEIGHTS)[0]
            lines = [{
                "store": store,
                "title": self.rng.choice(BOOK_TITLES),
                "sku": f"GWM-{store[-2:]}-{self.rng.randint(100000, 999999)}",
                "price": round(min(120.0, max(3.99, self.rng.lognormvariate(0, 0.6) * 9.5)), 2),
            } for _ in range(n_units)]
            if channel == "Amazon-MF":
                self._amazon_seq += 1
                oid = f"11{self.rng.randint(1,4)}-{self.rng.randint(1000000,9999999)}-{self.rng.randint(1000000,9999999)}"
            elif channel == "eBay":
                oid = f"{self.rng.randint(10,27)}-{self.rng.randint(10000,99999)}-{self.rng.randint(10000,99999)}"
            else:
                oid = f"GWB{self.rng.randint(100000, 999999)}"
            orders.append(Order(
                system="cashmonkey",
                channel_raw=channel,
                order_id=oid,
                source_order_id="",
                buyer=self._buyer(self.book_buyers),
                paid_at=self._paid_at_wrapped(day, peak_utc_hour=19.0),   # ~3 PM Eastern
                lines=lines,
                shipping=3.99 * n_units,
            ))
        return orders
