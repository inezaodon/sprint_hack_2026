"""The synthetic "truth world": one consistent Goodwill Michiana e-commerce history.

    .venv/bin/python -m goodwill_pulse.gen.truth            # -> data/sources/_truth.duckdb

Every marketplace / ops / finance source DB is derived from this file (docs/CONTRACT.md section 1).
Everything is invented. Item-first lifecycle:

    identified at a store -> manifested to e-com -> posted (first listed) by a lister
      -> listing on one channel -> sold (grouped into an order) | unsold (maybe relisted, ~15% of unsold GM) | active
    unposted items at the cutoff are the backlog (it grows over the year)

Orders are built only from sold items, so sell-through, revenue, fees and payouts always agree.

Conventions (read by engineers 2, 3, 8):
  * all timestamps TIMESTAMPTZ in UTC; "business date" = America/New_York date.
  * money DECIMAL(12,2); order.subtotal = sum(order_lines.sale_price); total = subtotal + shipping_charged + handling + tax.
  * order_lines.fee_alloc = (marketplace_fee + payment_fee) allocated to lines by sale price (sums exactly per order).
  * refunds.amount excludes tax (full refund = subtotal + shipping_charged + handling).
  * payouts: gross = sum(subtotal + shipping_charged + handling) of orders whose business date is in the period;
    fees = sum(marketplace_fee + payment_fee) of those orders (eBay: plus the cost of labels bought on eBay,
    shipping_charges.carrier = 'ebay', by the label's business date); refunds = sum(refund amounts) whose business date
    is in the period; net = gross - fees - refunds. Only periods that are complete and paid by the cutoff exist.
    eBay daily (paid next day), Amazon 14-day periods from 2025-09-01 (paid end+2), ShopGoodwill periods
    1 = days 1-10 / 2 = 11-20 / 3 = 21-EOM (paid end+2), GoodwillFinds Mon-Sun weeks (paid end+3),
    Goodwillbooks calendar month (paid 2nd business day of next month).
  * shipping_charges: carrier fedex | osm | pitney_bowes | easypost | ebay (~60% of eBay orders buy the label
    on eBay; eBay deducts it from the payout). One label charge per shipped order (amount = orders.shipping_label_cost) plus FedEx
    refunds (negative) / adjustments (positive). Planted: a FedEx refund of -42.10 dated 2026-10-01 for a label
    charged in late September. Orders paid shortly before the cutoff may not be shipped (no charge) yet.
  * native ids: GoodwillFinds marketplace_order_id is the numeric Shopify-style id; its display name is
    '#GF' + (10000 + rank of the order by paid_at within goodwillfinds), 1-based.
  * pre-window history is simulated (warm-up) but only items still "alive" on the start date are kept, so the
    world starts in steady state: every kept item's listings are kept, orders before the start are dropped.
"""
from __future__ import annotations

import argparse
import os
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import duckdb
import numpy as np
import pandas as pd

from ..config import DATA_DIR
from .world import BOOK_TITLES, GM_CATEGORIES, STORE_WEIGHTS

ET_NAME = "America/New_York"
ET = ZoneInfo(ET_NAME)
DEFAULT_OUT = DATA_DIR / "sources" / "_truth.duckdb"
DEFAULT_START = date(2025, 9, 1)
DEFAULT_END = datetime(2026, 10, 3, 22, 0, tzinfo=ET)       # demo cutoff: tonight 10 PM Eastern
GROWTH_ANCHOR = date(2025, 9, 1)
WARMUP_DAYS = 220                                              # books can sit listed 180 days

TRUTH_DDL = """
CREATE TABLE stores (
    store_id     VARCHAR PRIMARY KEY,      -- 'Store01'..'Store24'
    store_name   VARCHAR,
    city         VARCHAR,
    ai_flagging  BOOLEAN,                  -- 9 stores
    ecom_eye     BOOLEAN                   -- 2 stores
);
CREATE TABLE employees (
    employee_id  VARCHAR PRIMARY KEY,      -- 'E001'..
    role         VARCHAR,                  -- lister | photographer | shipper | manager
    hourly_rate  DECIMAL(8,2),
    hired_on     DATE
);
CREATE TABLE items (
    item_id          VARCHAR PRIMARY KEY,  -- SKU: 'UP-03-000123' (general merch) | 'GWM-03-123456' (books)
    store_id         VARCHAR,
    line_of_business VARCHAR,              -- general_merch | books
    category         VARCHAR,              -- canonical category (Books for books)
    title            VARCHAR,
    isbn             VARCHAR,              -- books only
    identified_at    TIMESTAMPTZ,
    flagged_by       VARCHAR,              -- ai | person
    manifest_id      VARCHAR,
    manifested_at    TIMESTAMPTZ,
    posted_at        TIMESTAMPTZ,          -- first listed; NULL = backlog
    poster_id        VARCHAR,
    list_minutes     DOUBLE
);
CREATE TABLE listings (
    listing_id         VARCHAR PRIMARY KEY,
    item_id            VARCHAR,
    channel            VARCHAR,            -- shopgoodwill | ebay | amazon | goodwillfinds | goodwillbooks
    tool               VARCHAR,            -- upright | cashmonkey
    native_listing_id  VARCHAR,            -- SGW ItemID / eBay legacyItemId / GF product id / Amazon & GWB = sku
    listed_at          TIMESTAMPTZ,
    ended_at           TIMESTAMPTZ,        -- NULL = active
    status             VARCHAR,            -- active | sold | unsold
    price              DECIMAL(12,2),
    relist_of          VARCHAR
);
CREATE TABLE buyers (
    buyer_id          VARCHAR PRIMARY KEY,
    channel           VARCHAR,
    native_buyer_ref  VARCHAR,
    state             VARCHAR
);
CREATE TABLE orders (
    order_id              VARCHAR PRIMARY KEY,
    channel               VARCHAR,
    tool                  VARCHAR,
    marketplace_order_id  VARCHAR,
    upright_order_id      VARCHAR,         -- Upright orders only
    buyer_id              VARCHAR,
    paid_at               TIMESTAMPTZ,
    item_count            INTEGER,
    subtotal              DECIMAL(12,2),
    shipping_charged      DECIMAL(12,2),
    handling              DECIMAL(12,2),   -- Upright: $3/item; else 0
    tax                   DECIMAL(12,2),
    marketplace_fee       DECIMAL(12,2),
    payment_fee           DECIMAL(12,2),
    shipping_label_cost   DECIMAL(12,2),
    total                 DECIMAL(12,2),
    payment_type          VARCHAR
);
CREATE TABLE order_lines (
    order_id    VARCHAR,
    line_no     INTEGER,
    item_id     VARCHAR,
    listing_id  VARCHAR,
    quantity    INTEGER,
    sale_price  DECIMAL(12,2),
    fee_alloc   DECIMAL(12,2),
    PRIMARY KEY (order_id, line_no)
);
CREATE TABLE refunds (
    refund_id    VARCHAR PRIMARY KEY,
    order_id     VARCHAR,
    refunded_at  TIMESTAMPTZ,
    amount       DECIMAL(12,2),
    reason       VARCHAR
);
CREATE TABLE payouts (
    payout_id     VARCHAR PRIMARY KEY,
    channel       VARCHAR,
    period_start  DATE,
    period_end    DATE,
    paid_on       DATE,
    gross         DECIMAL(12,2),
    fees          DECIMAL(12,2),
    refunds       DECIMAL(12,2),
    net           DECIMAL(12,2)
);
CREATE TABLE shipping_charges (
    charge_id   VARCHAR PRIMARY KEY,
    carrier     VARCHAR,                   -- fedex | osm | pitney_bowes | easypost
    order_id    VARCHAR,
    charged_at  TIMESTAMPTZ,
    amount      DECIMAL(12,2),             -- positive charge, negative refund/adjustment
    tracking    VARCHAR
);
CREATE TABLE labor (
    employee_id  VARCHAR,
    work_date    DATE,
    hours        DECIMAL(5,2),
    PRIMARY KEY (employee_id, work_date)
);
CREATE TABLE productivity (
    employee_id   VARCHAR,
    work_date     DATE,
    accepted      INTEGER,
    rejected      INTEGER,
    photographed  INTEGER,
    posted        INTEGER,
    PRIMARY KEY (employee_id, work_date)
);
CREATE TABLE budget (
    month           DATE,
    channel         VARCHAR,
    revenue_budget  DECIMAL(12,2),
    PRIMARY KEY (month, channel)
);
CREATE TABLE monthly_inputs (
    month                 DATE PRIMARY KEY,
    overhead_allocation   DECIMAL(12,2),
    store_retail_revenue  DECIMAL(12,2)
);
"""

TABLES = ["stores", "employees", "items", "listings", "buyers", "orders", "order_lines", "refunds", "payouts",
          "shipping_charges", "labor", "productivity", "budget", "monthly_inputs"]

CHANNELS = ["shopgoodwill", "ebay", "goodwillfinds", "amazon", "goodwillbooks"]
SGW, EBAY, GF, AMZ, GWB = range(5)
TOOLS = ["upright", "cashmonkey"]

# ---- calibration knobs -------------------------------------------------------------------------------------------
GM_IDENTIFIED_PER_DAY = 232.0      # at growth 1.0 (2025-09-01)
BOOK_IDENTIFIED_PER_DAY = 138.0
GROWTH_PER_DAY = 0.0005            # supply growth; backlog growth absorbs part of it
Q_GM = 0.48                        # chance a GM listing sells (scaled by the day's demand factor)
Q_BOOK = 0.455
RELIST_RATE = 0.15
PRICE_SCALE = 1.06
GM_MIN_PRICE_C = 1000              # minimum online price threshold: $10 general merch
BOOK_MIN_PRICE_C = 399
WEEKDAY_DEMAND = np.array([1.0, 1.08, 1.05, 1.02, 0.97, 0.92, 0.95])
GM_CHANNEL_P = [0.84, 0.11, 0.05]  # shopgoodwill, ebay, goodwillfinds
BOOK_CHANNEL_P = [0.70, 0.20, 0.10]  # amazon, ebay, goodwillbooks
REFUND_RATE = 0.025
EBAY_LABEL_SHARE = 0.60            # eBay orders whose label is bought on eBay (carrier 'ebay', deducted in payouts)
OVERHEAD_SHARE = 0.20
# Wed 2026-09-30 is the deck's reference day (slide 26: ~128 Upright paid orders, ~$13.2K subtotal)
PINNED_DEMAND = {date(2026, 9, 30): 1.09}

# "jewelry high price / low volume": reuse world.py price shapes, item-volume weights lower for jewelry
GM_VOLUME_WEIGHTS = {"Jewelry": 0.09, "Collectibles": 0.15, "Electronics": 0.13, "Clothing": 0.22, "Shoes": 0.09,
                     "Home Decor": 0.14, "Toys & Games": 0.09, "Art": 0.04, "Watches": 0.05}
GM_NOUNS = {
    "Jewelry": ["Sterling Silver Ring", "14K Gold Chain Necklace", "Cameo Brooch", "Pearl Earrings",
                "Costume Jewelry Lot", "Turquoise Cuff Bracelet", "Charm Bracelet"],
    "Collectibles": ["Pyrex Mixing Bowl", "Hummel Figurine", "Sports Card Lot", "Coca-Cola Tray", "Coin Set",
                     "Depression Glass Plate", "Die-Cast Car"],
    "Electronics": ["Nintendo DS Console", "Bose Speaker", "Canon DSLR Body", "iPod Classic", "Turntable",
                    "Bluetooth Headphones", "Film Camera"],
    "Clothing": ["Patagonia Fleece Jacket", "Levi's 501 Jeans", "Pendleton Wool Shirt", "Silk Blouse",
                 "Carhartt Coat", "Band T-Shirt", "Cashmere Sweater"],
    "Shoes": ["Nike Air Max Sneakers", "Dr. Martens Boots", "Cowboy Boots", "Leather Loafers", "Birkenstock Sandals"],
    "Home Decor": ["Mid-Century Lamp", "Brass Candlesticks", "Framed Mirror", "Le Creuset Dutch Oven",
                   "Crystal Vase", "Quilt"],
    "Toys & Games": ["LEGO Set", "Vintage Board Game", "Barbie Doll", "Hot Wheels Lot", "Puzzle Lot", "Plush Bear"],
    "Art": ["Oil Painting on Canvas", "Signed Lithograph", "Watercolor Landscape", "Pottery Vase", "Framed Print"],
    "Watches": ["Seiko Automatic Watch", "Fossil Watch", "Timex Watch Lot", "Citizen Eco-Drive", "Pocket Watch"],
}
GM_ADJ = ["Vintage ", "", "", "", "Antique ", "Lot of 2 ", "NWT ", "Retro "]
MORE_BOOKS = [
    "The Great Gatsby", "To Kill a Mockingbird", "1984", "The Alchemist", "The Kite Runner", "Gone Girl",
    "The Road", "A Man Called Ove", "The Nightingale", "Circe", "Project Hail Mary", "The Martian",
    "Thinking, Fast and Slow", "Outliers", "Quiet", "The Four Agreements", "Harry Potter and the Sorcerer's Stone",
    "Goodnight Moon", "Introduction to Algorithms", "Principles of Economics", "Psychology: Themes and Variations",
    "The Joy of Cooking", "Moosewood Cookbook", "The Old Man and the Sea", "Of Mice and Men", "Beloved",
    "The Handmaid's Tale", "Fahrenheit 451", "Brave New World", "The Catcher in the Rye", "Anatomy & Physiology",
    "Microeconomics", "The 7 Habits of Highly Effective People", "Man's Search for Meaning", "Wonder",
    "Charlotte's Web", "The Giver", "Holes", "Matilda", "The Outsiders",
]
STORE_CITIES = ["South Bend", "Mishawaka", "Elkhart", "Goshen", "Niles", "Plymouth", "Warsaw", "La Porte",
                "Michigan City", "Benton Harbor", "St. Joseph", "Granger", "Nappanee", "Bremen", "Dowagiac",
                "Buchanan", "Sturgis", "Three Rivers", "Rochester", "Knox", "Culver", "Syracuse", "Middlebury",
                "Edwardsburg"]
AI_STORES = [3, 9, 11, 13, 2, 5, 16, 19, 22]
ECOM_EYE_STORES = [3, 11]
STATES = (["IN", "MI", "OH", "IL", "CA", "TX", "FL", "NY", "PA", "GA", "NC", "WA", "WI", "MN", "AZ", "NJ", "VA",
           "MA", "CO", "TN"],
          [8, 7, 5, 6, 11, 9, 8, 7, 5, 4, 4, 3, 3, 3, 3, 3, 3, 3, 2, 3])
REFUND_REASONS = ["Item not as described", "Damaged in transit", "Lost in transit", "Buyer cancelled",
                  "Missing parts"]
UPRIGHT_PAYMENT = (["CreditCard", "PayPal", "ApplePay"], [0.45, 0.25, 0.30])
EMPLOYEE_PLAN = (  # role, count, rate range
    [("manager", 3, (24.0, 31.0)), ("lister", 16, (15.5, 19.0)), ("photographer", 6, (15.0, 17.5)),
     ("shipper", 8, (14.5, 16.5))])
LATE_HIRES = {"lister": [date(2026, 1, 12), date(2026, 3, 2), date(2026, 5, 18), date(2026, 8, 3)]}


# ---- helpers -----------------------------------------------------------------------------------------------------
def _holidays(y0: int, y1: int) -> set[date]:
    out = set()
    for y in range(y0, y1 + 1):
        nov1 = date(y, 11, 1)
        thanksgiving = nov1 + timedelta(days=(3 - nov1.weekday()) % 7 + 21)
        out |= {date(y, 1, 1), date(y, 7, 4), date(y, 12, 25), thanksgiving}
    return out


def _isbn13(stem12: str) -> str:
    s = sum(int(c) * (1 if i % 2 == 0 else 3) for i, c in enumerate(stem12))
    return stem12 + str((10 - s % 10) % 10)


def _money(c) -> np.ndarray:
    return np.round(np.asarray(c, dtype="float64") / 100.0, 2)


class _Clock:
    """Times are float days since `base` midnight, Eastern wall clock."""

    def __init__(self, base: date):
        self.base = base
        self.base64 = np.datetime64(base, "s")

    def utc(self, t) -> pd.Series:
        t = np.asarray(t, dtype="float64")
        nan = np.isnan(t)
        secs = np.round(np.where(nan, 0.0, t) * 86400.0).astype("int64")
        local = pd.DatetimeIndex(self.base64 + secs.astype("timedelta64[s]"))
        idx = local.tz_localize(ET_NAME, ambiguous=np.zeros(len(t), dtype=bool),
                                nonexistent="shift_forward").tz_convert("UTC")
        s = pd.Series(idx)
        s[nan] = pd.NaT
        return s

    def day_dates(self, d) -> pd.Series:
        d = np.asarray(d, dtype="int64")
        return pd.Series(np.datetime64(self.base, "D") + d.astype("timedelta64[D]"))


def _resolve_end(end) -> datetime:
    if isinstance(end, datetime):
        return end if end.tzinfo else end.replace(tzinfo=ET)
    return datetime(end.year, end.month, end.day, 22, 0, tzinfo=ET)


def _seq(n: int, rng, lo: int, hi: int, start: int) -> np.ndarray:
    """Strictly increasing ids with random strides (unique, look non-sequential)."""
    return start + np.cumsum(rng.integers(lo, hi, size=n, dtype="int64"))


# ---- build -------------------------------------------------------------------------------------------------------
def build(out_path: Path = DEFAULT_OUT, start: date = DEFAULT_START, end=DEFAULT_END, seed: int = 7) -> dict[str, int]:
    out_path = Path(out_path)
    tables = generate(start, end, seed)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = out_path.with_name(out_path.name + ".tmp")
    for p in (tmp, Path(str(tmp) + ".wal")):
        if p.exists():
            p.unlink()
    con = duckdb.connect(str(tmp))
    try:
        con.execute("SET TimeZone = 'UTC'")
        con.execute(TRUTH_DDL)
        counts = {}
        for name in TABLES:
            df = tables[name]
            con.register("_df", df)
            cols = ", ".join(f'"{c}"' for c in df.columns)
            con.execute(f"INSERT INTO {name} ({cols}) SELECT {cols} FROM _df")
            con.unregister("_df")
            counts[name] = len(df)
        con.execute("CHECKPOINT")
    finally:
        con.close()
    os.replace(tmp, out_path)
    return counts


def generate(start: date = DEFAULT_START, end=DEFAULT_END, seed: int = 7) -> dict[str, pd.DataFrame]:
    rng = np.random.default_rng(seed)
    end_dt = _resolve_end(end).astimezone(ET)
    end_date = end_dt.date()
    base = start - timedelta(days=WARMUP_DAYS)
    clock = _Clock(base)
    nd = (end_date - base).days + 1
    start_t = float((start - base).days)
    cut_t = (end_date - base).days + (end_dt.hour + end_dt.minute / 60 + end_dt.second / 3600) / 24
    PAD = 400

    # calendar --------------------------------------------------------------------------------------------------
    dates = pd.DatetimeIndex(np.datetime64(base, "D") + np.arange(nd + PAD).astype("timedelta64[D]"))
    wd = dates.weekday.values
    month = dates.month.values
    hol = _holidays(base.year, end_date.year + 2)
    closed = np.array([d.date() in hol for d in dates])
    age = (dates.values - np.datetime64(GROWTH_ANCHOR, "D")).astype("timedelta64[D]").astype(int)
    growth = 1 + GROWTH_PER_DAY * age
    season = np.where(np.isin(month, [11, 12]), 1.18, np.where(np.isin(month, [1, 2]), 0.92, 1.0))
    demand = WEEKDAY_DEMAND[wd] * season * np.exp(rng.normal(0, 0.06, nd + PAD) - 0.0018)
    demand[nd:] = 0.0
    for pin_day, pin_val in PINNED_DEMAND.items():      # deck slide 26 calibration day
        k = (pin_day - base).days
        if 0 <= k < nd:
            demand[k] = pin_val
    workday = (wd < 6) & ~closed                    # back office works Mon-Sat
    next_work = np.empty(nd + PAD, dtype="int64")
    nxt = nd + PAD
    for d in range(nd + PAD - 1, -1, -1):
        if workday[d]:
            nxt = d
        next_work[d] = nxt
    frac_year = np.clip(age / 397.0, 0, 1)
    work_list = np.nonzero(workday)[0]

    # stores ----------------------------------------------------------------------------------------------------
    sw = np.array(STORE_WEIGHTS) * np.array([1.35 if n in AI_STORES else 1.0 for n in range(1, 25)])
    p_store = sw / sw.sum()
    stores = pd.DataFrame({
        "store_id": [f"Store{n:02d}" for n in range(1, 25)],
        "store_name": [f"Goodwill {STORE_CITIES[n - 1]}" for n in range(1, 25)],
        "city": STORE_CITIES,
        "ai_flagging": [n in AI_STORES for n in range(1, 25)],
        "ecom_eye": [n in ECOM_EYE_STORES for n in range(1, 25)],
    })

    # employees + labor ---------------------------------------------------------------------------------------
    roles, rates, hired = [], [], []
    for role, n, (lo, hi) in EMPLOYEE_PLAN:
        late = LATE_HIRES.get(role, [])
        for i in range(n + len(late)):
            roles.append(role)
            rates.append(round(float(rng.uniform(lo, hi)), 2))
            if i < n:
                hired.append(date(2018, 1, 1) + timedelta(days=int(rng.integers(0, 2500))))
            else:
                hired.append(late[i - n])
    ne = len(roles)
    roles_a = np.array(roles)
    emp_ids = np.array([f"E{i + 1:03d}" for i in range(ne)])
    employees = pd.DataFrame({"employee_id": emp_ids, "role": roles_a, "hourly_rate": rates,
                              "hired_on": pd.to_datetime(hired).date})
    hired64 = np.array([np.datetime64(h, "D") for h in hired])
    sat_p = {"manager": 0.1, "lister": 0.5, "photographer": 0.35, "shipper": 0.6}
    p_week = np.array([0.97 if r == "manager" else 0.93 for r in roles])
    p_sat = np.array([sat_p[r] for r in roles])
    u = rng.random((nd, ne))
    dd = dates.values[:nd]
    p = np.where(wd[:nd, None] < 5, p_week[None, :], np.where(wd[:nd, None] == 5, p_sat[None, :], 0.0))
    works = (u < p) & (dd[:, None] >= hired64[None, :]) & ~closed[:nd, None]
    for role in ("lister", "shipper"):
        cols = np.nonzero(roles_a == role)[0]
        none = workday[:nd] & ~works[:, cols].any(axis=1)
        works[none, cols[0]] = True
    hours = np.where(wd[:nd, None] < 5, rng.normal(7.6, 0.5, (nd, ne)), rng.normal(5.5, 1.0, (nd, ne)))
    hours = np.where(roles_a[None, :] == "manager", hours + 0.8, hours)
    hours = np.round(np.clip(hours, 3.0, 9.75) * 4) / 4

    # items -----------------------------------------------------------------------------------------------------
    id_week = np.array([1.0, 1.0, 1.0, 1.0, 1.0, 1.15, 0.6])
    id_week = id_week / id_week.mean()
    gm_cats = [c for c in GM_CATEGORIES]
    gm_w = np.array([GM_VOLUME_WEIGHTS[c[0]] for c in gm_cats])
    gm_w = gm_w / gm_w.sum()

    def make_items(lam_base, books: bool):
        lam = lam_base * growth[:nd] * id_week[wd[:nd]] * ~closed[:nd]
        cnt = rng.poisson(lam)
        day = np.repeat(np.arange(nd), cnt)
        n = len(day)
        store = rng.choice(24, size=n, p=p_store)
        ident = day + rng.uniform(9 / 24, 19 / 24, n)
        man_day = day + rng.geometric(0.55, n)
        man_day = np.where(wd[man_day] == 6, man_day + 1, man_day)
        man_t = man_day + (14 + (store % 4) * 0.5) / 24
        wait = (5 + 7 * frac_year[man_day]) if books else (10 + 15 * frac_year[man_day])
        # wait counted in working days so every working day gets an even share of posting
        pos = np.searchsorted(work_list, man_day + 1) + np.floor(rng.exponential(wait * 6 / 7)).astype("int64")
        post_day = work_list[np.minimum(pos, len(work_list) - 1)]
        post_t = post_day + rng.uniform(8.5 / 24, 16.5 / 24, n)
        post_t = np.where(post_t < cut_t, post_t, np.nan)
        ai = np.isin(store + 1, AI_STORES)
        flag_ai = np.where(ai, rng.random(n) < 0.72, rng.random(n) < 0.03)
        if books:
            cat = np.full(n, len(gm_cats))          # Books
            mins = np.round(rng.lognormal(np.log(3.0), 0.35, n), 1)
        else:
            cat = rng.choice(len(gm_cats), size=n, p=gm_w)
            mins = np.round(rng.lognormal(np.log(9.0), 0.4, n), 1)
        return dict(day=day, store=store, ident=ident, man_day=man_day, man_t=man_t, post_t=post_t,
                    flag_ai=flag_ai, cat=cat, mins=mins, books=np.full(n, books))

    gm = make_items(GM_IDENTIFIED_PER_DAY, False)
    bk = make_items(BOOK_IDENTIFIED_PER_DAY, True)
    it = {k: np.concatenate([gm[k], bk[k]]) for k in gm}
    n_items = len(it["day"])
    is_book = it["books"]

    # first listing per posted item -------------------------------------------------------------------------
    posted = ~np.isnan(it["post_t"])
    first_items = np.nonzero(posted)[0]
    fb = is_book[first_items]
    ch = np.where(fb, rng.choice([AMZ, EBAY, GWB], size=len(first_items), p=BOOK_CHANNEL_P),
                  rng.choice([SGW, EBAY, GF], size=len(first_items), p=GM_CHANNEL_P))
    med = np.array([c[2] for c in gm_cats] + [9.5]) * np.array([PRICE_SCALE] * len(gm_cats) + [1.0])
    spr = np.array([c[3] for c in gm_cats] + [0.6])
    cat_f = it["cat"][first_items]
    price = med[cat_f] * rng.lognormal(0, spr[cat_f])
    price = np.where(fb, np.clip(price, BOOK_MIN_PRICE_C / 100, 120.0), np.clip(price, GM_MIN_PRICE_C / 100, 4000.0))
    price_c = np.round(price * 100).astype("int64")

    L_item, L_ch, L_t, L_price, L_parent = [first_items], [ch], [it["post_t"][first_items]], [price_c], \
        [np.full(len(first_items), -1, dtype="int64")]
    L_end, L_status, L_sale = [], [], []

    def simulate(item_idx, chan, lt):
        n = len(item_idx)
        books = is_book[item_idx]
        lday = np.floor(lt).astype("int64")
        sale_day = np.empty(n, dtype="int64")
        end_t = np.empty(n)
        ok = np.ones(n, dtype=bool)
        sgw = chan == SGW
        dur = rng.integers(5, 11, n)
        lag = rng.choice([0, 1, 2], size=n, p=[0.5, 0.35, 0.15])
        off_gm = np.floor(rng.exponential(9.0, n)).astype("int64")
        off_bk = np.floor(rng.exponential(40.0, n)).astype("int64")
        fixed_gm = ~sgw & ~books
        sale_day[sgw] = (lday + dur + lag)[sgw]
        end_t[sgw] = (lday + dur + rng.uniform(19 / 24, 23 / 24, n))[sgw]
        sale_day[fixed_gm] = (lday + 1 + off_gm)[fixed_gm]
        ok[fixed_gm] = (off_gm < 30)[fixed_gm]
        end_t[fixed_gm] = (lt + 30)[fixed_gm]
        sale_day[books] = (lday + 1 + off_bk)[books]
        ok[books] = (off_bk < 180)[books]
        end_t[books] = (lt + 180)[books]
        sale_day = np.minimum(sale_day, nd + PAD - 1)
        q = np.where(books, Q_BOOK, Q_GM)
        hit = ok & (rng.random(n) < q * demand[sale_day])
        tod = np.where(books, rng.normal(15.0, 4.5, n) % 24, rng.normal(18.5, 4.0, n) % 24)
        sale_t = sale_day + tod / 24
        sold = hit & (sale_t < cut_t)
        status = np.where(sold, 1, np.where(hit | (end_t >= cut_t), 0, 2))   # 0 active, 1 sold, 2 unsold
        end_out = np.where(status == 2, end_t, np.nan)
        return status, end_out, np.where(sold, sale_t, np.nan)

    status, end_t, sale_t = simulate(first_items, ch, L_t[0])
    L_status.append(status); L_end.append(end_t); L_sale.append(sale_t)
    offset = 0
    cur_items, cur_ch, cur_price, cur_status, cur_end = first_items, ch, price_c, status, end_t
    while True:
        cand = np.nonzero((cur_status == 2) & ~is_book[cur_items])[0]
        cand = cand[rng.random(len(cand)) < RELIST_RATE]
        new_t = np.floor(cur_end[cand]) + rng.integers(1, 5, len(cand)) + rng.uniform(8.5 / 24, 16.5 / 24, len(cand))
        cand, new_t = cand[new_t < cut_t], new_t[new_t < cut_t]
        if len(cand) == 0:
            break
        parent = offset + cand
        offset += len(cur_items)
        n_items_r = cur_items[cand]
        n_ch = cur_ch[cand]
        n_price = np.maximum(np.round(cur_price[cand] * 0.85).astype("int64"), GM_MIN_PRICE_C)
        st, en, sa = simulate(n_items_r, n_ch, new_t)
        L_item.append(n_items_r); L_ch.append(n_ch); L_t.append(new_t); L_price.append(n_price)
        L_parent.append(parent); L_status.append(st); L_end.append(en); L_sale.append(sa)
        cur_items, cur_ch, cur_price, cur_status, cur_end = n_items_r, n_ch, n_price, st, en

    li = {"item": np.concatenate(L_item), "ch": np.concatenate(L_ch), "t": np.concatenate(L_t),
          "price": np.concatenate(L_price), "parent": np.concatenate(L_parent),
          "status": np.concatenate(L_status), "end": np.concatenate(L_end), "sale": np.concatenate(L_sale)}
    li["tool"] = is_book[li["item"]].astype("int64")
    # sale price: SGW auctions hammer above the starting bid; fixed price elsewhere
    bump = np.where(li["ch"] == SGW, 1 + rng.exponential(0.12, len(li["item"])), 1.0)
    li["sale_price"] = np.round(li["price"] * bump).astype("int64")

    # group sold items into orders ------------------------------------------------------------------------
    sold_idx = np.nonzero(li["status"] == 1)[0]
    key = li["ch"][sold_idx] * 2 + li["tool"][sold_idx]
    order_ = np.lexsort((li["sale"][sold_idx], key))
    s = sold_idx[order_]
    key_s = key[order_]
    gid = np.empty(len(s), dtype="int64")
    g0 = 0
    for k in np.unique(key_s):
        seg = np.nonzero(key_s == k)[0]
        m = len(seg)
        if k % 2 == 0:   # upright
            sizes = np.where(rng.random(m) < 0.9, 1, rng.choice([2, 2, 3, 4, 10], size=m))
        else:
            sizes = rng.choice([1, 2, 3], size=m, p=[0.82, 0.13, 0.05])
        cs = np.cumsum(sizes)
        local = np.searchsorted(cs, np.arange(m), side="right")
        gid[seg] = g0 + local
        g0 += local[-1] + 1
    ng = g0
    paid_t = np.full(ng, -np.inf)
    np.maximum.at(paid_t, gid, li["sale"][s])
    li["end"][s] = paid_t[gid]

    # keep only items alive on the start date ----------------------------------------------------------------
    last = np.full(n_items, -np.inf)
    last[~posted] = np.inf
    ev = np.where(li["status"] == 0, np.inf, li["end"])
    np.maximum.at(last, li["item"], ev)
    keep_item = last >= start_t
    item_map = np.full(n_items, -1, dtype="int64")
    # sort kept items by identified time so SKUs follow identification order
    kept = np.nonzero(keep_item)[0]
    kept = kept[np.argsort(it["ident"][kept], kind="stable")]
    item_map[kept] = np.arange(len(kept))
    keep_l = keep_item[li["item"]]
    # listings ordered by listed time
    lk = np.nonzero(keep_l)[0]
    lk = lk[np.argsort(li["t"][lk], kind="stable")]
    lmap = np.full(len(li["item"]), -1, dtype="int64")
    lmap[lk] = np.arange(len(lk))
    keep_g = paid_t >= start_t
    gk = np.nonzero(keep_g)[0]
    gk = gk[np.argsort(paid_t[gk], kind="stable")]
    gmap = np.full(ng, -1, dtype="int64")
    gmap[gk] = np.arange(len(gk))
    s_keep = keep_g[gid]
    s, gid = s[s_keep], gmap[gid[s_keep]]
    assert np.all(keep_l[s])

    # ---- items table ------------------------------------------------------------------------------------------
    I = {k: v[kept] for k, v in it.items()}
    nI = len(kept)
    st_code = np.array([f"{x + 1:02d}" for x in range(24)])
    book_k = I["books"]
    store_ids = np.array([f"Store{x + 1:02d}" for x in range(24)])
    # per-store sequence
    seqn = np.zeros(nI, dtype="int64")
    for b in (False, True):
        for sx in range(24):
            ix = np.nonzero((I["store"] == sx) & (book_k == b))[0]
            if b:
                seqn[ix] = 100000 + np.cumsum(rng.integers(1, 9, len(ix)))
            else:
                seqn[ix] = np.arange(1, len(ix) + 1) + int(rng.integers(0, 3000))
    item_id = np.where(book_k, "GWM-", "UP-").astype(object) + st_code[I["store"]].astype(object) + "-" + \
        pd.Series(seqn).astype(str).str.zfill(6).values.astype(object)
    cat_names = np.array([c[0] for c in gm_cats] + ["Books"])
    adj = np.array(GM_ADJ)[rng.integers(0, len(GM_ADJ), nI)]
    noun_pick = rng.integers(0, 1000, nI)
    titles_all = BOOK_TITLES + MORE_BOOKS
    isbn_tab = np.array([_isbn13("978" + f"{int(x):09d}") for x in rng.integers(0, 10**9, len(titles_all))])
    tpick = rng.integers(0, len(titles_all), nI)
    title = np.empty(nI, dtype=object)
    isbn = np.full(nI, None, dtype=object)
    for i in range(nI):
        c = I["cat"][i]
        if book_k[i]:
            title[i] = titles_all[tpick[i]]
            isbn[i] = isbn_tab[tpick[i]]
        else:
            nouns = GM_NOUNS[cat_names[c]]
            title[i] = adj[i] + nouns[noun_pick[i] % len(nouns)]
    man_dates = clock.day_dates(I["man_day"]).dt.strftime("%Y%m%d").values.astype(object)
    manifest_id = "MF-" + st_code[I["store"]].astype(object) + "-" + man_dates + \
        np.where(book_k, "-B", "-G").astype(object)

    # posters / photographers
    post_t_k = I["post_t"]
    posted_k = ~np.isnan(post_t_k)
    post_day = np.where(posted_k, np.floor(np.nan_to_num(post_t_k, nan=0)), 0).astype("int64")
    lister_cols = np.nonzero(roles_a == "lister")[0]
    photo_cols = np.nonzero(roles_a == "photographer")[0]

    def assign(cols, days, fallback=None):
        w = works[:, cols]
        cnt = w.sum(axis=1)
        order = np.argsort(~w, axis=1, kind="stable")       # working ones first
        c = cnt[days]
        r = np.floor(rng.random(len(days)) * np.maximum(c, 1)).astype("int64")
        pick = cols[order[days, r]]
        if fallback is not None:
            pick = np.where(c > 0, pick, fallback)
        return pick, c

    poster = np.full(nI, -1, dtype="int64")
    pk = np.nonzero(posted_k)[0]
    poster[pk], _ = assign(lister_cols, post_day[pk])
    photog = np.full(nI, -1, dtype="int64")
    pg = pk[~book_k[pk]]
    photog[pg], _ = assign(photo_cols, post_day[pg], fallback=poster[pg])

    items = pd.DataFrame({
        "item_id": item_id,
        "store_id": store_ids[I["store"]],
        "line_of_business": np.where(book_k, "books", "general_merch"),
        "category": cat_names[I["cat"]],
        "title": title,
        "isbn": isbn,
        "identified_at": clock.utc(I["ident"]),
        "flagged_by": np.where(I["flag_ai"], "ai", "person"),
        "manifest_id": manifest_id,
        "manifested_at": clock.utc(I["man_t"]),
        "posted_at": clock.utc(post_t_k),
        "poster_id": np.where(poster >= 0, emp_ids[np.maximum(poster, 0)], None),
        "list_minutes": np.where(posted_k, I["mins"], np.nan),
    })

    # ---- listings table ---------------------------------------------------------------------------------------
    L = {k: v[lk] for k, v in li.items()}
    nL = len(lk)
    L_item_k = item_map[L["item"]]
    assert np.all(L_item_k >= 0)
    listing_id = "LST-" + pd.Series(np.arange(1, nL + 1)).astype(str).str.zfill(8).values.astype(object)
    native = np.empty(nL, dtype=object)
    for c, (lo, hi, base0) in {SGW: (1, 4, 210_000_000), EBAY: (1, 9000, 356_000_000_000),
                               GF: (1, 40, 8_100_000_000)}.items():
        ix = np.nonzero(L["ch"] == c)[0]
        native[ix] = pd.Series(_seq(len(ix), rng, lo, hi, base0)).astype(str).values
    ix = np.isin(L["ch"], [AMZ, GWB])
    native[ix] = item_id[L_item_k[ix]]
    parent_k = np.where(L["parent"] >= 0, lmap[np.maximum(L["parent"], 0)], -1)
    status_names = np.array(["active", "sold", "unsold"])
    listings = pd.DataFrame({
        "listing_id": listing_id,
        "item_id": item_id[L_item_k],
        "channel": np.array(CHANNELS)[L["ch"]],
        "tool": np.array(TOOLS)[L["tool"]],
        "native_listing_id": native,
        "listed_at": clock.utc(L["t"]),
        "ended_at": clock.utc(L["end"]),
        "status": status_names[L["status"]],
        "price": _money(L["price"]),
        "relist_of": np.where(parent_k >= 0, listing_id[np.maximum(parent_k, 0)], None),
    })

    # ---- orders + lines ---------------------------------------------------------------------------------------
    nO = len(gk)
    o_paid = paid_t[gk]
    line_l = lmap[s]                      # kept listing index per line
    o_ch = np.zeros(nO, dtype="int64")
    o_ch[gid] = L["ch"][line_l]
    o_tool = np.zeros(nO, dtype="int64")
    o_tool[gid] = L["tool"][line_l]
    line_price = L["sale_price"][line_l]
    o_items = np.bincount(gid, minlength=nO)
    o_sub = np.bincount(gid, weights=line_price, minlength=nO).astype("int64")
    up = o_tool == 0
    ship = np.where(up, np.where(rng.random(nO) < 0.06, 0,
                                 np.round(rng.uniform(750, 1550, nO)) + 150 * (o_items - 1)), 399 * o_items)
    ship = ship.astype("int64")
    handling = np.where(up, 300 * o_items, 0).astype("int64")
    tax_rate = np.where(rng.random(nO) < 0.12, 0.0, rng.uniform(0.05, 0.08, nO))
    tax = np.round(o_sub * tax_rate).astype("int64")
    total = o_sub + ship + handling + tax
    mf = np.select([o_ch == SGW, o_ch == EBAY, o_ch == GF, o_ch == AMZ, o_ch == GWB],
                   [np.round(o_sub * 0.09), np.round((o_sub + ship) * 0.1325) + 30, np.round(o_sub * 0.15),
                    np.round(o_sub * 0.15) + 180 * o_items, np.round(o_sub * 0.05) + 30 * o_items]).astype("int64")
    pf = np.where(np.isin(o_ch, [SGW, GF]), np.round(total * 0.029) + 30, 0).astype("int64")
    carrier_names = np.array(["fedex", "osm", "pitney_bowes", "easypost", "ebay"])
    carrier = np.where(up, rng.choice(4, nO, p=[0.35, 0.20, 0.30, 0.15]), rng.choice([1, 2, 3], nO, p=[0.35, 0.45, 0.20]))
    carrier = np.where((o_ch == EBAY) & (rng.random(nO) < EBAY_LABEL_SHARE), 4, carrier)   # bought on eBay
    label = np.where(up, np.round(rng.uniform(650, 1350, nO)) + 120 * (o_items - 1) + np.where(carrier == 0, 150, 0),
                     np.round(rng.uniform(320, 440, nO)) * o_items).astype("int64")
    pay_type = np.where(up, np.array(UPRIGHT_PAYMENT[0])[rng.choice(3, nO, p=UPRIGHT_PAYMENT[1])],
                        np.select([o_ch == AMZ, o_ch == EBAY], ["Amazon", "eBay Managed Payments"], "CreditCard"))

    # lines sorted by (order, sale time) -> line_no; fee allocation
    lo = np.lexsort((L["sale"][line_l], gid))
    gid, line_l, line_price = gid[lo], line_l[lo], line_price[lo]
    first_of = np.r_[True, gid[1:] != gid[:-1]]
    starts = np.nonzero(first_of)[0]
    line_no = np.arange(len(gid)) - np.repeat(starts, np.diff(np.r_[starts, len(gid)])) + 1
    fee = mf + pf
    alloc = np.floor(fee[gid] * line_price / np.maximum(o_sub[gid], 1)).astype("int64")
    rem = fee - np.bincount(gid, weights=alloc, minlength=nO).astype("int64")
    last_line = np.r_[gid[1:] != gid[:-1], True]
    alloc[last_line] += rem[gid[last_line]]

    # ids
    order_id = "ORD-" + pd.Series(np.arange(1, nO + 1)).astype(str).str.zfill(7).values.astype(object)
    mkt = np.empty(nO, dtype=object)
    ix = np.nonzero(o_ch == SGW)[0]
    mkt[ix] = pd.Series(_seq(len(ix), rng, 1, 30, 65_700_000)).astype(str).values
    ix = np.nonzero(o_ch == EBAY)[0]
    v = pd.Series(_seq(len(ix), rng, 1, 2_000_000, 121_000_000_000)).astype(str)
    mkt[ix] = (v.str[:2] + "-" + v.str[2:7] + "-" + v.str[7:12]).values
    ix = np.nonzero(o_ch == AMZ)[0]
    v = pd.Series(_seq(len(ix), rng, 1, 10**9, 11_300_000_000_000_000)).astype(str)
    mkt[ix] = (v.str[:3] + "-" + v.str[3:10] + "-" + v.str[10:17]).values
    ix = np.nonzero(o_ch == GF)[0]
    mkt[ix] = pd.Series(_seq(len(ix), rng, 1000, 90000, 5_500_000_000)).astype(str).values
    ix = np.nonzero(o_ch == GWB)[0]
    mkt[ix] = ("GWB" + pd.Series(_seq(len(ix), rng, 1, 20, 100_000)).astype(str)).values
    upright_id = np.full(nO, None, dtype=object)
    ix = np.nonzero(up)[0]
    upright_id[ix] = pd.Series(_seq(len(ix), rng, 1, 40, 24_100_000)).astype(str).values

    # buyers: per channel, a loyal core + a long tail
    buyer_of = np.empty(nO, dtype=object)
    months_n = max(1.0, (cut_t - start_t) / 30.4)
    brows = []
    stems = np.array(["thrift", "treasure", "vintage", "resell", "deal", "find", "collect", "shop", "hunt", "gem"])
    seps = np.array(["_", "", "."])
    code = {SGW: "SGW", EBAY: "EBY", GF: "GFD", AMZ: "AMZ", GWB: "GWB"}
    for c in range(5):
        ix = np.nonzero(o_ch == c)[0]
        if len(ix) == 0:
            continue
        per_m = len(ix) / months_n
        Lc, Pc = max(5, int(0.3 * per_m)), max(50, int(55 * per_m))
        loyal = rng.random(len(ix)) < 0.25
        b = np.where(loyal, rng.integers(0, Lc, len(ix)), Lc + rng.integers(0, Pc, len(ix)))
        ub, inv = np.unique(b, return_inverse=True)
        bid = np.array([f"BUY-{code[c]}-{x:06d}" for x in ub], dtype=object)
        buyer_of[ix] = bid[inv]
        n = len(ub)
        if c == EBAY:
            ref = [f"{stems[x % 10]}{seps[x % 3]}{x:05d}" for x in ub]
        elif c == SGW:
            ref = [str(1_000_000 + x * 13 + x % 7) for x in ub]
        elif c == AMZ:
            r = rng.integers(0, 2**36, n)
            ref = [f"{(int(rr) << 20 | int(x)):014x}@marketplace.amazon.com" for rr, x in zip(r, ub)]
        elif c == GF:
            ref = [str(7_000_000_000 + x * 37 + x % 11) for x in ub]
        else:
            ref = [f"C{100000 + x * 3 + x % 2}" for x in ub]
        sp = np.array(STATES[1], dtype=float)
        brows.append(pd.DataFrame({"buyer_id": bid, "channel": CHANNELS[c], "native_buyer_ref": ref,
                                   "state": np.array(STATES[0])[rng.choice(len(sp), n, p=sp / sp.sum())]}))
    buyers = pd.concat(brows, ignore_index=True)

    # planted: a big FedEx label late in September that gets refunded Oct 1 (crosses the month end)
    plant_fedex = None
    sep28 = (date(2026, 9, 28) - base).days
    oct1 = (date(2026, 10, 1) - base).days
    if 0 <= sep28 and oct1 + 10.25 / 24 < cut_t and sep28 >= start_t:
        cand = np.nonzero(up & (carrier == 0) & (np.floor(o_paid) == sep28))[0]
        if len(cand):
            plant_fedex = cand[0]
            label[plant_fedex] = 4210

    orders = pd.DataFrame({
        "order_id": order_id, "channel": np.array(CHANNELS)[o_ch], "tool": np.array(TOOLS)[o_tool],
        "marketplace_order_id": mkt, "upright_order_id": upright_id, "buyer_id": buyer_of,
        "paid_at": clock.utc(o_paid), "item_count": o_items.astype("int32"),
        "subtotal": _money(o_sub), "shipping_charged": _money(ship), "handling": _money(handling),
        "tax": _money(tax), "marketplace_fee": _money(mf), "payment_fee": _money(pf),
        "shipping_label_cost": _money(label), "total": _money(total), "payment_type": pay_type,
    })
    order_lines = pd.DataFrame({
        "order_id": order_id[gid], "line_no": line_no.astype("int32"),
        "item_id": item_id[L_item_k[line_l]], "listing_id": listing_id[line_l],
        "quantity": np.ones(len(gid), dtype="int32"), "sale_price": _money(line_price), "fee_alloc": _money(alloc),
    })

    # ---- refunds ------------------------------------------------------------------------------------------------
    rmask = rng.random(nO) < REFUND_RATE
    r_t = o_paid + rng.lognormal(np.log(5.0), 0.8, nO)
    full = rng.random(nO) < 0.6
    r_amt = np.where(full, o_sub + ship + handling, np.round(o_sub * rng.uniform(0.2, 0.5, nO))).astype("int64")
    # planted: paid late on Sep 30, refunded just after midnight Oct 1 (crosses midnight and month end)
    sep30 = (date(2026, 9, 30) - base).days
    if sep30 >= start_t and oct1 + 0.5 / 24 < cut_t:
        cand = np.nonzero(up & ~rmask & (o_paid >= sep30 + 20 / 24) & (o_paid < sep30 + 23.9 / 24))[0]
        if len(cand):
            rmask[cand[0]] = True
            r_t[cand[0]] = oct1 + 0.35 / 24
            full[cand[0]] = True
            r_amt[cand[0]] = o_sub[cand[0]] + ship[cand[0]] + handling[cand[0]]
    rmask &= r_t < cut_t
    ri = np.nonzero(rmask)[0]
    ri = ri[np.argsort(r_t[ri], kind="stable")]
    refunds = pd.DataFrame({
        "refund_id": ["RF-" + f"{k + 1:06d}" for k in range(len(ri))],
        "order_id": order_id[ri], "refunded_at": clock.utc(r_t[ri]), "amount": _money(r_amt[ri]),
        "reason": np.array(REFUND_REASONS)[rng.integers(0, len(REFUND_REASONS), len(ri))],
    })

    # ---- shipping charges ----------------------------------------------------------------------------------------
    ship_day = np.floor(o_paid).astype("int64") + 1
    ship_day = next_work[np.minimum(ship_day, nd + PAD - 1)]
    c_t = ship_day + rng.uniform(9 / 24, 16 / 24, nO)
    shipped = c_t < cut_t
    if plant_fedex is not None:
        c_t[plant_fedex] = sep28 + 1 + 11.0 / 24
        shipped[plant_fedex] = True
    si = np.nonzero(shipped)[0]
    trk = np.empty(nO, dtype=object)
    fx = carrier == 0
    trk[fx] = pd.Series(rng.integers(10**11, 10**12, fx.sum())).astype(str).values
    trk[~fx] = ("94001" + pd.Series(rng.integers(10**16, 10**17, (~fx).sum())).astype(str)).values
    adj_mask = shipped & fx & (rng.random(nO) < 0.02)
    adj_t = c_t + rng.uniform(5, 20, nO)
    adj_amt = np.where(rng.random(nO) < 0.7, -label, np.round(rng.uniform(200, 1500, nO))).astype("int64")
    if plant_fedex is not None:
        adj_mask[plant_fedex] = True
        adj_t[plant_fedex] = oct1 + 10.25 / 24
        adj_amt[plant_fedex] = -4210
    adj_mask &= adj_t < cut_t
    ai_ = np.nonzero(adj_mask)[0]
    ch_t = np.r_[c_t[si], adj_t[ai_]]
    ch_o = np.r_[si, ai_]
    ch_amt = np.r_[label[si], adj_amt[ai_]]
    so = np.lexsort((ch_o, ch_t))
    shipping_charges = pd.DataFrame({
        "charge_id": [f"SC-{k + 1:07d}" for k in range(len(so))],
        "carrier": carrier_names[carrier[ch_o[so]]],
        "order_id": order_id[ch_o[so]],
        "charged_at": clock.utc(ch_t[so]),
        "amount": _money(ch_amt[so]),
        "tracking": trk[ch_o[so]],
    })

    # ---- payouts (eBay deducts the labels bought on eBay, by label date) ------------------------------------------
    ebay_lbl = si[carrier[si] == 4]
    payouts = _payouts(clock, o_ch, np.floor(o_paid).astype("int64"), o_sub + ship + handling, mf + pf,
                       o_ch[ri], np.floor(r_t[ri]).astype("int64"), r_amt[ri], end_date,
                       np.floor(c_t[ebay_lbl]).astype("int64"), label[ebay_lbl])

    # ---- labor + productivity -----------------------------------------------------------------------------------
    d0 = int(start_t)
    wd_idx, we_idx = np.nonzero(works[d0:nd])
    labor = pd.DataFrame({"employee_id": emp_ids[we_idx], "work_date": clock.day_dates(wd_idx + d0).dt.date,
                          "hours": hours[wd_idx + d0, we_idx]})
    in_win = posted_k & (post_day >= d0)
    ppost = np.zeros((nd, ne), dtype="int64")
    np.add.at(ppost, (post_day[in_win], poster[in_win]), 1)
    pphoto = np.zeros((nd, ne), dtype="int64")
    gmw = in_win & ~book_k
    np.add.at(pphoto, (post_day[gmw], photog[gmw]), 1)
    pd_, pe_ = np.nonzero((ppost[d0:] + pphoto[d0:]) > 0)
    pd_ += d0
    posted_n = ppost[pd_, pe_]
    accepted = posted_n + rng.binomial(posted_n, 0.02)
    productivity = pd.DataFrame({
        "employee_id": emp_ids[pe_], "work_date": clock.day_dates(pd_).dt.date,
        "accepted": accepted.astype("int32"), "rejected": rng.binomial(accepted, 0.07).astype("int32"),
        "photographed": pphoto[pd_, pe_].astype("int32"), "posted": posted_n.astype("int32"),
    })

    # ---- budget + monthly inputs ------------------------------------------------------------------------------
    budget, monthly_inputs = _budget_inputs(rng, clock, o_ch, np.floor(o_paid).astype("int64"), o_sub, start, end_date,
                                            int(cut_t))

    return {"stores": stores, "employees": employees, "items": items, "listings": listings, "buyers": buyers,
            "orders": orders, "order_lines": order_lines, "refunds": refunds, "payouts": payouts,
            "shipping_charges": shipping_charges, "labor": labor, "productivity": productivity,
            "budget": budget, "monthly_inputs": monthly_inputs}


def _payouts(clock, o_ch, o_day, o_gross, o_fees, r_ch, r_day, r_amt, end_date: date,
             lbl_day=None, lbl_amt=None) -> pd.DataFrame:
    a = pd.DataFrame({"ch": o_ch, "date": clock.day_dates(o_day), "gross": o_gross, "fees": o_fees, "refunds": 0})
    b = pd.DataFrame({"ch": r_ch, "date": clock.day_dates(r_day), "gross": 0, "fees": 0, "refunds": r_amt})
    parts = [a, b]
    if lbl_day is not None and len(lbl_day):
        parts.append(pd.DataFrame({"ch": EBAY, "date": clock.day_dates(lbl_day), "gross": 0, "fees": lbl_amt,
                                   "refunds": 0}))
    ev = pd.concat(parts, ignore_index=True)
    d = ev["date"]
    ms = d.dt.to_period("M").dt.start_time
    me = d.dt.to_period("M").dt.end_time.dt.normalize()
    anchor = pd.Timestamp(DEFAULT_START)
    one = pd.Timedelta(days=1)
    ps = pd.Series(pd.NaT, index=ev.index, dtype="datetime64[s]")
    pe = ps.copy()
    paid = ps.copy()
    for c in range(5):
        m = ev["ch"] == c
        dc = d[m]
        if c == EBAY:
            s_, e_, p_ = dc, dc, dc + one
        elif c == AMZ:
            k = (dc - anchor).dt.days // 14
            s_ = anchor + pd.to_timedelta(k * 14, unit="D")
            e_ = s_ + pd.Timedelta(days=13)
            p_ = e_ + 2 * one
        elif c == SGW:
            day = dc.dt.day
            s_ = ms[m] + pd.to_timedelta(np.where(day <= 10, 0, np.where(day <= 20, 10, 20)), unit="D")
            e_ = pd.Series(np.where(day <= 10, ms[m] + pd.Timedelta(days=9),
                                    np.where(day <= 20, ms[m] + pd.Timedelta(days=19), me[m])), index=dc.index)
            p_ = e_ + 2 * one
        elif c == GF:
            s_ = dc - pd.to_timedelta(dc.dt.weekday, unit="D")
            e_ = s_ + pd.Timedelta(days=6)
            p_ = e_ + 3 * one
        else:
            s_, e_ = ms[m], me[m]
            nxt = (e_ + one).values.astype("datetime64[D]")
            p_ = pd.Series(np.busday_offset(nxt, 1, roll="forward"), index=dc.index)
        ps[m], pe[m], paid[m] = s_.values, e_.values, pd.to_datetime(p_).values
    ev["ps"], ev["pe"], ev["paid"] = ps, pe, paid
    g = ev.groupby(["ch", "ps", "pe", "paid"], as_index=False)[["gross", "fees", "refunds"]].sum()
    lim = pd.Timestamp(end_date)
    g = g[(g["pe"] < lim) & (g["paid"] <= lim)].sort_values(["paid", "ch"]).reset_index(drop=True)
    g["net"] = g["gross"] - g["fees"] - g["refunds"]
    return pd.DataFrame({
        "payout_id": [f"PO-{CHANNELS[c][:3].upper()}-{p:%Y%m%d}-{e:%Y%m%d}" for c, p, e in zip(g["ch"], g["ps"], g["pe"])],
        "channel": np.array(CHANNELS)[g["ch"].values], "period_start": g["ps"].dt.date, "period_end": g["pe"].dt.date,
        "paid_on": g["paid"].dt.date, "gross": _money(g["gross"]), "fees": _money(g["fees"]),
        "refunds": _money(g["refunds"]), "net": _money(g["net"]),
    })


def _budget_inputs(rng, clock, o_ch, o_day, o_sub, start: date, end_date: date, cut_day: int):
    df = pd.DataFrame({"ch": o_ch, "date": clock.day_dates(o_day), "sub": o_sub})
    df["month"] = df["date"].dt.to_period("M")
    act = df.groupby(["month", "ch"])["sub"].sum()
    first = pd.Period(start, "M")
    last_complete = pd.Period(end_date, "M") if (end_date + timedelta(days=1)).day == 1 else pd.Period(end_date, "M") - 1
    months = pd.period_range(first, pd.Period(f"{end_date.year}-12", "M"), freq="M")
    complete = [m for m in months if m <= last_complete]
    daily_rate = {}
    for c in range(5):
        tot = df[df["ch"] == c]["sub"].sum()
        daily_rate[c] = tot / max(1, (end_date - start).days + 1)
    rows = []
    for m in months:
        for c in range(5):
            py = m - 12
            if py >= first and py <= last_complete:
                base_v = act.get((py, c), 0) * 1.08
            elif m <= last_complete:
                base_v = act.get((m, c), 0) / (1 + GROWTH_PER_DAY * 365) * 1.08
            else:
                base_v = daily_rate[c] * m.days_in_month
            v = round(base_v * rng.uniform(0.95, 1.05) / 10000) * 100   # cents -> whole $100s
            rows.append((m.start_time.date(), CHANNELS[c], float(v)))
    budget = pd.DataFrame(rows, columns=["month", "channel", "revenue_budget"])
    mrows = []
    mon_tot = df.groupby("month")["sub"].sum()
    for m in (complete or [first]):
        rev = mon_tot.get(m, 0) / 100.0
        overhead = round(rev * OVERHEAD_SHARE * rng.uniform(0.95, 1.05), -2)
        retail = round(rev / 0.05 * rng.uniform(0.95, 1.05), -2)
        mrows.append((m.start_time.date(), float(overhead), float(retail)))
    return budget, pd.DataFrame(mrows, columns=["month", "overhead_allocation", "store_retail_revenue"])


CALIBRATION_SQL = {
    "upright_2026_09_30_pacific": """
        SELECT count(*) AS orders, sum(subtotal) AS subtotal FROM orders
        WHERE tool = 'upright' AND (paid_at AT TIME ZONE 'America/Los_Angeles')::DATE = DATE '2026-09-30'""",
    "upright_2026_09_30_eastern": """
        SELECT count(*) AS orders, sum(subtotal) AS subtotal FROM orders
        WHERE tool = 'upright' AND (paid_at AT TIME ZONE 'America/New_York')::DATE = DATE '2026-09-30'""",
    "orders_per_day_by_month": """
        WITH o AS (SELECT *, (paid_at AT TIME ZONE 'America/New_York')::DATE AS d FROM orders)
        SELECT date_trunc('month', d)::DATE AS month, round(count(*) / count(DISTINCT d), 1) AS orders_per_day,
               round(count(*) FILTER (WHERE tool = 'upright') / count(DISTINCT d), 1) AS upright_per_day,
               round(count(*) FILTER (WHERE tool = 'cashmonkey') / count(DISTINCT d), 1) AS cashmonkey_per_day
        FROM o GROUP BY 1 ORDER BY 1""",
    "revenue_by_channel_month": """
        PIVOT (SELECT date_trunc('month', paid_at AT TIME ZONE 'America/New_York')::DATE AS month, channel,
                      subtotal FROM orders)
        ON channel IN ('shopgoodwill', 'ebay', 'goodwillfinds', 'amazon', 'goodwillbooks')
        USING round(sum(subtotal)) ORDER BY month""",
    "sell_through_30d_by_cohort": """
        WITH s AS (SELECT ol.item_id, o.paid_at FROM order_lines ol JOIN orders o USING (order_id))
        SELECT date_trunc('month', i.posted_at AT TIME ZONE 'America/New_York')::DATE AS cohort,
               round(avg(CASE WHEN i.line_of_business = 'general_merch'
                              THEN coalesce(s.paid_at < i.posted_at + INTERVAL 30 DAY, false)::INT END), 3) AS gm,
               round(avg(CASE WHEN i.line_of_business = 'books'
                              THEN coalesce(s.paid_at < i.posted_at + INTERVAL 30 DAY, false)::INT END), 3) AS books
        FROM items i LEFT JOIN s USING (item_id)
        WHERE i.posted_at >= DATE '2025-09-01' GROUP BY 1 ORDER BY 1""",
    "relist_share_of_unsold_gm": """
        SELECT round(count(r.listing_id) / count(*), 3) FROM listings l
        LEFT JOIN listings r ON r.relist_of = l.listing_id
        WHERE l.status = 'unsold' AND l.tool = 'upright'""",
    "backlog_month_end": """
        WITH m AS (SELECT DISTINCT date_trunc('month', paid_at AT TIME ZONE 'America/New_York')::DATE AS month
                   FROM orders),
             e AS (SELECT month, ((month + INTERVAL 1 MONTH)::TIMESTAMP AT TIME ZONE 'America/New_York') AS t FROM m)
        SELECT month, (SELECT count(*) FROM items i WHERE i.manifested_at < e.t
                       AND (i.posted_at IS NULL OR i.posted_at >= e.t)) AS backlog
        FROM e ORDER BY month""",
    "kpis_by_month": """
        WITH o AS (SELECT date_trunc('month', paid_at AT TIME ZONE 'America/New_York')::DATE AS month, * FROM orders),
        rev AS (SELECT month, sum(subtotal) AS revenue, sum(marketplace_fee + payment_fee) AS fees,
                       sum(shipping_label_cost) AS labels, count(*) AS n FROM o GROUP BY 1),
        rf AS (SELECT date_trunc('month', refunded_at AT TIME ZONE 'America/New_York')::DATE AS month,
                      sum(amount) AS refunds, count(*) AS n_ref FROM refunds GROUP BY 1),
        lab AS (SELECT date_trunc('month', work_date)::DATE AS month, sum(l.hours) AS hours,
                       sum(l.hours * e.hourly_rate) AS cost
                FROM labor l JOIN employees e USING (employee_id) GROUP BY 1),
        fb AS (SELECT buyer_id, min(month) AS first_month FROM o GROUP BY 1),
        rep AS (SELECT o.month, count(DISTINCT o.buyer_id) AS buyers,
                       count(DISTINCT o.buyer_id) FILTER (WHERE fb.first_month < o.month) AS repeat_buyers
                FROM o JOIN fb USING (buyer_id) GROUP BY 1)
        SELECT rev.month, round(rev.revenue) AS revenue, round(rev.revenue / lab.hours, 1) AS rev_per_labor_hour,
               round((rev.revenue - rev.fees - rev.labels - coalesce(rf.refunds, 0) - lab.cost
                      - mi.overhead_allocation) / rev.revenue, 3) AS net_margin,
               round(rf.n_ref / rev.n, 4) AS refund_rate,
               round(rep.repeat_buyers / rep.buyers, 3) AS repeat_buyer_rate,
               round(rev.revenue / mi.store_retail_revenue, 3) AS ecom_share_of_retail
        FROM rev JOIN lab USING (month) JOIN rep USING (month) LEFT JOIN rf USING (month)
        LEFT JOIN monthly_inputs mi USING (month) ORDER BY 1""",
}


def calibration_report(path: Path = DEFAULT_OUT) -> dict[str, pd.DataFrame]:
    """The figures to eyeball after a build (deck calibration day, volumes, KPIs)."""
    con = duckdb.connect(str(path), read_only=True)
    try:
        return {k: con.execute(q).df() for k, q in CALIBRATION_SQL.items()}
    finally:
        con.close()


def main() -> None:
    p = argparse.ArgumentParser(description="Build the synthetic truth world")
    p.add_argument("--out", type=Path, default=DEFAULT_OUT)
    p.add_argument("--start", type=date.fromisoformat, default=DEFAULT_START)
    p.add_argument("--end", type=date.fromisoformat, default=None, help="end date (cutoff 22:00 ET that day)")
    p.add_argument("--seed", type=int, default=7)
    p.add_argument("--report", action="store_true", help="only print the calibration report of --out")
    a = p.parse_args()
    if a.report:
        with pd.option_context("display.width", 200, "display.max_rows", 50, "display.max_columns", 20):
            for k, df in calibration_report(a.out).items():
                print(f"\n== {k}\n{df.to_string(index=False)}")
        return
    t0 = time.time()
    counts = build(a.out, a.start, a.end or DEFAULT_END, a.seed)
    for k, v in counts.items():
        print(f"{k:18s} {v:>9,d}")
    print(f"wrote {a.out} in {time.time() - t0:.1f}s")


if __name__ == "__main__":
    main()
