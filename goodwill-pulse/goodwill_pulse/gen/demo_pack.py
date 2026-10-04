"""Synthetic demo folders in the layouts Goodwill actually uses (photographed Oct 2026).

    .venv/bin/python -m goodwill_pulse.gen.demo_pack            # writes goodwill-pulse/demo_data/

Each folder is one test scenario. Files are derived from the same simulated world as the warehouse (the history and demo
CSVs under data/samples), so totals tie to the warehouse; each scenario ships an expected.json with the file's own sums.

  01_tonight_saturday_2026-10-03     Upright paid orders (real layout, csv + xlsx) and Cash Monkey for tonight's pull
  02_friday_2026-10-02               the day in the photographed file name (paid_orders_10-02-2026_10-02-2026)
  03_monday_catchup_fri_sat_sun      Friday, Saturday, Sunday as three daily files plus one range file
  04_messy_reports                   duplicates, renamed headers, a test order, a bad date, title rows above the header
  05_store_weekly_sales_supro        'Weekly Sales 2026 Week 40.xlsx': store sales by weekday with last-year comparison
  06_daily_summary_manual            the hand-entered Daily Summary Spreadsheet, filled from the warehouse

Real Upright columns A to R are read from the photo (some names are cut off in it; see config/mappings/upright_paid_orders.yaml).
Columns after R in the photo are cut off, so Shipping City/Country/Address/State and Paid At are our guesses.
"""
from __future__ import annotations

import csv
import hashlib
import json
import random
import shutil
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path

import duckdb
from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from ..config import DATA_DIR, ROOT, SAMPLES_DIR

OUT = ROOT / "demo_data"

REAL_HEADER = [
    "Upright Order ID", "Channel", "Channel Order ID", "Secondary Channel", "Channel Buyer ID", "Order Item Count",
    "Payment ID", "Payment Type", "Total", "Subtotal", "Shipping Total", "Shipping Label Cost", "Handling Total",
    "Tax Total", "Donation Total", "Currency", "Final Value", "Payment Processing Fee",
    "Shipping City", "Shipping Country", "Shipping Address 1", "Shipping Address 2", "Shipping State", "Paid At",
]
MONEY_COLS = {"Total", "Subtotal", "Shipping Total", "Shipping Label Cost", "Handling Total", "Tax Total", "Donation Total",
              "Final Value", "Payment Processing Fee"}
CITIES = [("Fort Wayne", "IN"), ("Indianapolis", "IN"), ("Chicago", "IL"), ("Kalamazoo", "MI"), ("Columbus", "OH"),
          ("Grand Rapids", "MI"), ("Lansing", "MI"), ("Toledo", "OH"), ("Madison", "WI"), ("Louisville", "KY")]
CASHMONKEY_HEADER = ["Order Date", "Order ID", "Account", "Channel", "SKU", "ASIN", "Title", "Condition", "Source",
                     "Quantity", "Item Price", "Shipping Credit", "Market Fees", "Shipping Cost", "Net Revenue", "Buyer"]


# --------------------------------------------------------------------------------------------- Upright (real layout)
def _h(key: str) -> int:
    return int(hashlib.sha1(key.encode()).hexdigest()[:8], 16)


def _num(x) -> float:
    try:
        return float(str(x).replace(",", "").replace("$", "")) if str(x).strip() != "" else 0.0
    except ValueError:
        return 0.0


def read_legacy_upright(path: Path) -> list[dict]:
    """Rows of one of our older paid_orders files (slide-26 layout), without its SUM row."""
    with path.open(newline="") as f:
        return [r for r in csv.DictReader(f) if (r.get("Upright Order ID") or "").strip()]


def to_real_row(r: dict) -> dict:
    """One legacy order row -> the real export's columns. Money fields keep their values; the real Total also holds the
    Donation, and the Shipping Label Cost (what the label cost Goodwill) is blank on about 40% of orders as in the photo."""
    oid = r["Upright Order ID"]
    ship, sub, hand, tax = _num(r["Shipping Total"]), _num(r["Subtotal"]), _num(r["Handling"]), _num(r["Tax Total"])
    hk = _h(oid)
    donation = round(0.05 + (hk % 86) / 100, 2) if hk % 100 < 14 else 0.0
    label = round(ship * (0.86 + (hk % 5) / 100), 2) if ship > 0 and hk % 10 < 6 else None
    city, state = CITIES[hk % len(CITIES)]
    return {
        "Upright Order ID": oid, "Channel": r["Channel"], "Channel Order ID": r["Channel Order ID"], "Secondary Channel": "",
        "Channel Buyer ID": r["Channel Buyer"], "Order Item Count": int(_num(r["Order Item Count"])), "Payment ID": "",
        "Payment Type": r["Payment Type"], "Total": round(sub + ship + hand + tax + donation, 2), "Subtotal": sub,
        "Shipping Total": ship, "Shipping Label Cost": label, "Handling Total": hand, "Tax Total": tax,
        "Donation Total": donation, "Currency": "USD", "Final Value": _num(r["Final Value Fee"]) or None,
        "Payment Processing Fee": _num(r["Payment Fee"]), "Shipping City": city, "Shipping Country": "US",
        "Shipping Address 1": "", "Shipping Address 2": "", "Shipping State": state, "Paid At": r["Paid At"],
    }


def _fmt(v, col):
    if v is None or v == "":
        return ""
    if col in MONEY_COLS:
        s = f"{float(v):.2f}".rstrip("0").rstrip(".")
        return s or "0"
    return str(v)


def totals_row(rows: list[dict]) -> dict:
    t = {k: "" for k in REAL_HEADER}
    for c in ("Total", "Subtotal", "Shipping Total", "Handling Total", "Tax Total", "Donation Total"):
        t[c] = round(sum(float(r[c] or 0) for r in rows), 2)
    return t


def write_real_csv(path: Path, rows: list[dict], header: list[str] | None = None, totals: bool = True) -> Path:
    header = header or REAL_HEADER
    with path.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(header)
        body = rows + ([totals_row(rows)] if totals and rows else [])
        for r in body:
            w.writerow([_fmt(r.get(k), k) for k in REAL_HEADER])
    return path


def write_real_xlsx(path: Path, rows: list[dict], title_rows: int = 0, totals: bool = True) -> Path:
    wb = Workbook()
    ws = wb.active
    ws.title = path.stem[:31]
    for i in range(title_rows):
        ws.cell(row=i + 1, column=1, value=["Upright Labs - Paid orders export", "Goodwill of Michiana (synthetic test file)", ""][i % 3])
    hr = title_rows + 1
    for j, h in enumerate(REAL_HEADER, 1):
        c = ws.cell(row=hr, column=j, value=h)
        c.font = Font(bold=True)
    body = rows + ([totals_row(rows)] if totals and rows else [])
    for i, r in enumerate(body, hr + 1):
        for j, k in enumerate(REAL_HEADER, 1):
            v = r.get(k)
            if v is None or v == "":
                continue
            if k in MONEY_COLS or k == "Order Item Count":
                ws.cell(row=i, column=j, value=float(v) if k in MONEY_COLS else int(v))
            else:
                ws.cell(row=i, column=j, value=int(v) if k in ("Upright Order ID", "Channel Order ID") and str(v).isdigit() else v)
    for j, h in enumerate(REAL_HEADER, 1):
        ws.column_dimensions[get_column_letter(j)].width = max(11, min(22, len(h) + 2))
    wb.save(path)
    return path


def upright_expected(rows: list[dict]) -> dict:
    out = {}
    for r in rows:
        ch = r["Channel"]
        d = out.setdefault(ch, {"orders": 0, "item_sales": 0.0, "shipping_total": 0.0, "handling": 0.0, "units": 0})
        d["orders"] += 1
        d["item_sales"] += float(r["Subtotal"])
        d["shipping_total"] += float(r["Shipping Total"])
        d["handling"] += float(r["Handling Total"])
        d["units"] += int(r["Order Item Count"])
    return {k: {kk: (round(vv, 2) if isinstance(vv, float) else vv) for kk, vv in v.items()} for k, v in sorted(out.items())}


def day_rows(day: date) -> list[dict]:
    """Real-layout rows for one Pacific paid date, from the legacy sample files (history or demo)."""
    name = f"paid_orders_{day:%m-%d-%Y}_{day:%m-%d-%Y}.csv"
    for d in ("history", "demo"):
        p = SAMPLES_DIR / d / name
        if p.exists():
            return [to_real_row(r) for r in read_legacy_upright(p)]
    raise FileNotFoundError(f"{name} not in data/samples (run python -m goodwill_pulse.gen.generate first)")



def eastern_day_rows(day: date) -> list[dict]:
    """Rows whose paid time falls on Eastern business date `day`, with Paid At rewritten in Eastern time. Upright exports by
    Pacific day, so an Eastern day spans two of those files (Oct 1 from 9 PM PT and Oct 2 until 9 PM PT)."""
    from datetime import datetime as _dt
    from zoneinfo import ZoneInfo
    pt, et = ZoneInfo("America/Los_Angeles"), ZoneInfo("America/New_York")
    out = []
    for d in (day - timedelta(days=1), day, day + timedelta(days=1)):
        try:
            rows = day_rows(d)
        except FileNotFoundError:
            continue
        for r in rows:
            t = _dt.strptime(r["Paid At"], "%m/%d/%Y %H:%M:%S").replace(tzinfo=pt).astimezone(et)
            if t.date() == day:
                out.append({**r, "Paid At": t.strftime("%m/%d/%Y %H:%M:%S")})
    return sorted(out, key=lambda r: r["Paid At"])

def cashmonkey_files_for(day: date) -> list[Path]:
    """The Cash Monkey download generated the morning after `day` (UTC day), or tonight's pull for the demo day."""
    out = []
    for d in ("history", "demo"):
        base = SAMPLES_DIR / d
        out += sorted(base.glob(f"orders2023-{day + timedelta(days=1):%Y%m%d}-*.csv"))
    return out


def cm_expected(paths: list[Path]) -> dict:
    out = {}
    for p in paths:
        with p.open(newline="") as f:
            for r in csv.DictReader(f):
                d = out.setdefault(r["Channel"], {"orders": set(), "units": 0, "item_sales": 0.0, "shipping_credit": 0.0, "fees": 0.0})
                d["orders"].add(r["Order ID"])
                d["units"] += int(_num(r["Quantity"]))
                d["item_sales"] += _num(r["Item Price"])
                d["shipping_credit"] += _num(r["Shipping Credit"])
                d["fees"] += _num(r["Market Fees"])
    return {k: {"orders": len(v["orders"]), "units": v["units"], "item_sales": round(v["item_sales"], 2),
                "shipping_credit": round(v["shipping_credit"], 2), "fees": round(v["fees"], 2)} for k, v in sorted(out.items())}


def _write_expected(folder: Path, obj: dict) -> None:
    (folder / "expected.json").write_text(json.dumps(obj, indent=2, default=str) + "\n")


def _copy_cm(folder: Path, day: date) -> list[Path]:
    paths = cashmonkey_files_for(day)
    for p in paths:
        shutil.copy2(p, folder / p.name)
    return [folder / p.name for p in paths]


# ----------------------------------------------------------------------------------------------------- scenarios
def scenario_single_day(folder: Path, day: date, note: str) -> dict:
    folder.mkdir(parents=True, exist_ok=True)
    rows = day_rows(day)
    base = f"paid_orders_{day:%m-%d-%Y}_{day:%m-%d-%Y}"
    write_real_csv(folder / f"{base}.csv", rows)
    write_real_xlsx(folder / f"{base}.xlsx", rows)
    cm = _copy_cm(folder, day)
    exp = {"scenario": note, "business_day_pacific": str(day), "upright": upright_expected(rows),
           "upright_total_item_sales": round(sum(float(r["Subtotal"]) for r in rows), 2),
           "upright_orders": len(rows), "cashmonkey_files": [p.name for p in cm], "cashmonkey": cm_expected(cm)}
    if day == date(2026, 10, 2):
        et = folder / "eastern_time_export"
        et.mkdir(exist_ok=True)
        erows = eastern_day_rows(day)
        write_real_csv(et / f"{base}.csv", erows)
        (et / "README.txt").write_text(
            "Same Friday, exported with Upright's time zone set to Eastern: Paid At is Eastern time and the rows are exactly\n"
            "Eastern business day 10/02. In the Upload tab choose 'Eastern' for the dates, and this file fully covers the day, so it\n"
            "replaces the warehouse totals and reconciles. The local FastAPI importer assumes Los Angeles time; do not drop this one there.\n")
        exp["eastern_time_export"] = {"orders": len(erows), "item_sales": round(sum(float(r["Subtotal"]) for r in erows), 2),
                                      "by_channel": upright_expected(erows)}
    _write_expected(folder, exp)
    return exp


def scenario_catchup(folder: Path, days: list[date]) -> dict:
    folder.mkdir(parents=True, exist_ok=True)
    allrows, per_day = [], {}
    for d in days:
        rows = day_rows(d)
        allrows += rows
        write_real_csv(folder / f"paid_orders_{d:%m-%d-%Y}_{d:%m-%d-%Y}.csv", rows)
        per_day[str(d)] = {"orders": len(rows), "item_sales": round(sum(float(r["Subtotal"]) for r in rows), 2)}
        _copy_cm(folder, d)
    rng = f"paid_orders_{days[0]:%m-%d-%Y}_{days[-1]:%m-%d-%Y}"
    (folder / "one_range_file").mkdir(exist_ok=True)
    write_real_csv(folder / "one_range_file" / f"{rng}.csv", allrows)
    write_real_xlsx(folder / "one_range_file" / f"{rng}.xlsx", allrows)
    _write_expected(folder, {"scenario": "Monday morning: Friday, Saturday and Sunday pulled together",
                             "days": per_day, "range_file_total_item_sales": round(sum(float(r["Subtotal"]) for r in allrows), 2),
                             "range_file_orders": len(allrows), "upright": upright_expected(allrows)})
    return per_day


def scenario_messy(folder: Path, day: date) -> dict:
    folder.mkdir(parents=True, exist_ok=True)
    rows = day_rows(day)
    rng = random.Random(11)
    n_clean = len(rows)
    exp = {"scenario": "Messy Upright exports. Each file lists what the system should catch", "clean_orders": n_clean,
           "clean_item_sales": round(sum(float(r["Subtotal"]) for r in rows), 2), "files": {}}

    # 1) duplicates, a renamed header, a test order, a blank buyer
    m = [dict(r) for r in rows]
    dups = rng.sample(m, 2)
    for r in dups:
        m.insert(rng.randrange(len(m)), dict(r))
    test = dict(m[3]); test.update({"Upright Order ID": "24999001", "Channel Buyer ID": "TEST_BUYER", "Subtotal": 1.0, "Total": 1.0,
                                    "Shipping Total": 0.0, "Handling Total": 0.0, "Tax Total": 0.0, "Donation Total": 0.0})
    m.insert(5, test)
    m[8] = dict(m[8]); m[8]["Channel Buyer ID"] = ""
    hdr = ["Sub Total" if h == "Subtotal" else h for h in REAL_HEADER]
    p = folder / f"paid_orders_{day:%m-%d-%Y}_{day:%m-%d-%Y} (2).csv"
    with p.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(hdr)
        for r in m:
            w.writerow([_fmt(r.get(k), k) for k in REAL_HEADER])
        w.writerow([_fmt(totals_row(m).get(k), k) for k in REAL_HEADER])
    exp["files"][p.name] = {"should_catch": ["renamed column 'Sub Total' (suggest Subtotal)", "2 duplicate order rows",
                                             "1 test order (TEST_BUYER)", "1 blank buyer"],
                            "orders_after_cleanup": n_clean, "item_sales_after_cleanup": exp["clean_item_sales"]}

    # 2) title rows above the header, text and bad dates, amounts with $ and commas
    m2 = [dict(r) for r in rows[:40]]
    for i, r in enumerate(m2):
        if i == 4:
            r["Paid At"] = "Oct 2, 2026 9:15 AM"
        if i == 9:
            r["Paid At"] = "N/A"
    p2 = write_real_xlsx(folder / f"paid_orders_title_rows_{day:%m-%d-%Y}.xlsx", m2, title_rows=3)
    exp["files"][p2.name] = {"should_catch": ["3 title rows before the header", "one text date, one invalid date", "totals row"],
                             "orders_in_file": len(m2)}

    # 3) a file for the wrong period: filename says one day, rows are another
    wrong = day_rows(day - timedelta(days=1))[:25]
    p3 = write_real_csv(folder / f"paid_orders_{day:%m-%d-%Y}_{day:%m-%d-%Y} (3).csv", wrong)
    exp["files"][p3.name] = {"should_catch": [f"file name says {day} but the paid dates are {day - timedelta(days=1)}"], "orders_in_file": len(wrong)}

    # 4) the exact same file twice
    shutil.copy2(folder / f"paid_orders_{day:%m-%d-%Y}_{day:%m-%d-%Y} (3).csv", folder / f"paid_orders_{day:%m-%d-%Y}_{day:%m-%d-%Y} (4).csv")
    exp["files"][f"paid_orders_{day:%m-%d-%Y}_{day:%m-%d-%Y} (4).csv"] = {"should_catch": ["identical to file (3): already loaded"]}

    # 5) no Cash Monkey file for the day: nothing to copy, state it
    (folder / "NO_CASH_MONKEY_FILE.txt").write_text(
        f"Cash Monkey's download for {day} is deliberately missing from this folder.\n"
        "The pulse should show Cash Monkey as 'not received' and the day as incomplete, not as zero sales.\n")
    exp["files"]["NO_CASH_MONKEY_FILE.txt"] = {"should_catch": ["Cash Monkey source missing: day marked incomplete"]}
    _write_expected(folder, exp)
    return exp


# ------------------------------------------------------------------------------- Supro-style weekly store sales workbook
STORES = [("Store01", "South Bend", "SB", 4900), ("Store02", "Mishawaka", "MISH", 4300), ("Store03", "Elkhart", "ELK", 3800),
          ("Store04", "Goshen", "GOSH", 3200), ("Store05", "Niles", "NIL", 3500), ("Store06", "Plymouth", "PLY", 2400),
          ("Store07", "Warsaw", "WAR", 3000), ("Store08", "La Porte", "LP", 3300), ("Store09", "Michigan City", "MC", 3900),
          ("Store10", "Benton Harbor", "BH", 2600), ("Store11", "St. Joseph", "STJ", 2300), ("Store12", "Granger", "GRAN", 3600),
          ("Store13", "Nappanee", "NAP", 1900), ("Store14", "Bremen", "BRE", 1500), ("Store15", "Dowagiac", "DOW", 1700),
          ("Store16", "Buchanan", "BUC", 1600), ("Store17", "Sturgis", "STU", 1800), ("Store18", "Three Rivers", "TR", 1700),
          ("Store19", "Rochester", "ROCH", 1500), ("Store20", "Knox", "KNOX", 1300), ("Store21", "Culver", "CUL", 1100),
          ("Store22", "Syracuse", "SYR", 1400), ("Store23", "Middlebury", "MID", 1600), ("Store24", "Edwardsburg", "EDW", 1200)]
DOW_MULT = [0.85, 0.90, 0.95, 1.00, 1.05, 1.15, 1.30]       # Sunday first, as on the sheet
DAYS = ["SUNDAY", "MONDAY", "TUESDAY", "WEDNESDAY", "THURSDAY", "FRIDAY", "SATURDAY"]
GREEN = PatternFill("solid", fgColor="C6E0B4")
BLUE = PatternFill("solid", fgColor="BDD7EE")


def store_week(start: date, seed: int = 40) -> dict[str, list[dict]]:
    """In-store sales for a Sunday-to-Saturday week: net sales, customers and last year's same weekday."""
    out = {}
    for sid, city, code, base in STORES:
        rng = random.Random(f"{seed}-{sid}-{start}")
        rows = []
        for i in range(7):
            avg = rng.uniform(17.5, 24.5)
            sales = base * DOW_MULT[i] * rng.uniform(0.82, 1.18)
            cust = max(1, round(sales / avg))
            ly_sales = sales * rng.uniform(0.68, 1.30)
            ly_cust = max(1, round(cust * rng.uniform(0.75, 1.25)))
            rows.append({"date": start + timedelta(days=i), "sales": round(sales, 2), "customers": cust,
                         "ly_sales": round(ly_sales, 2), "ly_customers": ly_cust})
        out[sid] = rows
    return out


def _pct(a, b):
    return (a / b - 1) if b else None


def write_store_weekly(path: Path, start: date, week_no: int) -> dict:
    data = store_week(start)
    wb = Workbook()
    ds = wb.active
    ds.title = "Data Sheet"
    ds.append(["Store ID", "Store Code", "Store", "Date", "Weekday", "Net Sales", "Customers", "Avg Sale", "LY Net Sales", "LY Customers"])
    for sid, city, code, _ in STORES:
        for r in data[sid]:
            ds.append([sid, code, f"Goodwill {city}", r["date"], r["date"].strftime("%A").upper(), r["sales"], r["customers"],
                       round(r["sales"] / r["customers"], 2), r["ly_sales"], r["ly_customers"]])
    for c in ds[1]:
        c.font = Font(bold=True)
    for row in ds.iter_rows(min_row=2, min_col=4, max_col=4):
        row[0].number_format = "m/d/yyyy"

    rp = wb.create_sheet("Report")
    end = start + timedelta(days=6)
    rp["B1"], rp["F1"], rp["I1"], rp["J1"], rp["K1"], rp["L1"] = ("GOODWILL INDUSTRIES OF MICHIANA, INC", "DAILY SALES SHEET",
                                                                  "Week Start", start, "to", end)
    rp["J1"].number_format = rp["L1"].number_format = "m/d/yyyy"
    for j, d in enumerate(DAYS):
        c = rp.cell(row=3, column=4 + j, value=d)
        c.font = Font(bold=True)
        c.alignment = Alignment(horizontal="right")
    rp.cell(row=3, column=12, value="TOTALS").font = Font(bold=True)
    labels = ["", "", "LY Totals", "% increase/decrease", "$ increase/decrease", "Customers", "Last Year", "% increase/decrease", "",
              "Avg Sale", "Last Year", "% Increase", "Avg Sale Increase"]
    r0, totals = 4, {}
    for sid, city, code, _ in STORES:
        d = data[sid]
        sales, ly = [x["sales"] for x in d], [x["ly_sales"] for x in d]
        cu, lcu = [x["customers"] for x in d], [x["ly_customers"] for x in d]
        avg, lavg = [s / c for s, c in zip(sales, cu)], [s / c for s, c in zip(ly, lcu)]
        ts, tly, tcu, tlcu = sum(sales), sum(ly), sum(cu), sum(lcu)
        block = [
            (city.upper(), sales, ts, "$#,##0"), ("", None, None, None),
            (labels[2], ly, tly, "#,##0"), (labels[3], [_pct(a, b) for a, b in zip(sales, ly)], _pct(ts, tly), "0%"),
            (labels[4], [a - b for a, b in zip(sales, ly)], ts - tly, "#,##0"),
            (labels[5], cu, tcu, "#,##0"), (labels[6], lcu, tlcu, "#,##0"), (labels[7], [_pct(a, b) for a, b in zip(cu, lcu)], _pct(tcu, tlcu), "0%"),
            ("", None, None, None),
            (labels[9], avg, ts / tcu, "0.00"), (labels[10], lavg, tly / tlcu, "0.00"),
            (labels[11], [_pct(a, b) for a, b in zip(avg, lavg)], _pct(ts / tcu, tly / tlcu), "0%"),
            (labels[12], [a - b for a, b in zip(avg, lavg)], ts / tcu - tly / tlcu, "0.00"),
        ]
        for k, (lab, vals, tot, fmt) in enumerate(block):
            row = r0 + k
            rp.cell(row=row, column=2, value=lab)
            if vals is not None:
                for j, v in enumerate(vals):
                    c = rp.cell(row=row, column=4 + j, value=None if v is None else round(v, 4))
                    c.number_format = fmt
                c = rp.cell(row=row, column=12, value=None if tot is None else round(tot, 4))
                c.number_format = fmt
        for col in range(2, 14):
            rp.cell(row=r0, column=col).fill = GREEN
            rp.cell(row=r0 + 1, column=col).fill = BLUE
        rp.cell(row=r0, column=2).font = Font(bold=True)
        rp.cell(row=r0, column=13, value=code)
        totals[code] = {"week_sales": round(ts, 2), "week_customers": tcu, "daily_sales": [round(x, 2) for x in sales]}
        r0 += len(block) + 1
    rp.column_dimensions["B"].width = 22
    for col in "DEFGHIJL":
        rp.column_dimensions[col].width = 12
    wb.move_sheet("Report", offset=0)
    wb.active = 1
    wb.save(path)
    return {"week_start": str(start), "week_end": str(end), "stores": len(STORES), "enterprise_week_sales": round(sum(v["week_sales"] for v in totals.values()), 2),
            "enterprise_week_customers": sum(v["week_customers"] for v in totals.values()), "by_store": totals}


# ------------------------------------------------------------------------------ hand-entered Daily Summary Spreadsheet
def write_daily_summary(path: Path, start: date, week_label: str) -> dict:
    con = duckdb.connect(str(DATA_DIR / "harmonized.duckdb"), read_only=True)
    ET = "America/New_York"
    end = start + timedelta(days=6)
    sales = defaultdict(dict)
    for d, ch, n, sub, ship in con.execute("""SELECT business_date, channel, count(*), sum(subtotal), sum(coalesce(shipping_charged, 0))
                                              FROM fct_orders WHERE business_date BETWEEN ? AND ? GROUP BY 1, 2""", [start, end]).fetchall():
        sales[d][ch] = (n, float(sub), float(ship))
    sold = defaultdict(lambda: defaultdict(int))
    for d, cat, q in con.execute("""SELECT business_date, category, sum(quantity) FROM fct_order_lines
                                    WHERE business_date BETWEEN ? AND ? GROUP BY 1, 2""", [start, end]).fetchall():
        sold[d][cat or "Unassigned"] += int(q)
    listed = defaultdict(lambda: defaultdict(int))
    for d, cat, n in con.execute(f"""SELECT CAST(timezone('{ET}', first_listed_at_utc) AS DATE), category, count(*) FROM fct_items
                                     WHERE first_listed_at_utc IS NOT NULL
                                       AND CAST(timezone('{ET}', first_listed_at_utc) AS DATE) BETWEEN ? AND ? GROUP BY 1, 2""", [start, end]).fetchall():
        listed[d][cat or "Unassigned"] += int(n)
    con.close()
    wb = Workbook()
    ws = wb.active
    ws.title = week_label
    fills = [PatternFill("solid", fgColor=c) for c in ("FFF2CC", "DDEBF7", "FCE4D6", "E2EFDA", "DDEBF7", "FFF2CC", "FCE4D6")]
    cats = ["Jewelry", "Books", "Clothing", "Collectibles", "Electronics", "Home Decor"]
    exp = {}
    row = 1
    ws.cell(row=row, column=1, value="Daily Summary: e-commerce (entered by hand from the Upright and Cash Monkey downloads)").font = Font(bold=True, size=12)
    row += 2
    for i in range(7):
        d = start + timedelta(days=i)
        s = sales.get(d, {})
        sgw, ebay, amz, gf, gb = (s.get(c, (0, 0.0, 0.0)) for c in ("shopgoodwill", "ebay", "amazon", "goodwillfinds", "goodwillbooks"))
        rng = random.Random(f"pickup-{d}")
        pickups = round(sgw[0] * rng.uniform(0.04, 0.09))
        top = row
        ws.cell(row=row, column=1, value=d.strftime("%A")).font = Font(bold=True)
        ws.cell(row=row, column=2, value=d).number_format = "m/d/yyyy"
        ws.cell(row=row, column=4, value="Orders Shipped").font = Font(bold=True)
        ws.cell(row=row, column=7, value="Shipping").font = Font(bold=True)
        ws.cell(row=row, column=9, value="Sold items").font = Font(bold=True)
        ws.cell(row=row, column=12, value="Listings").font = Font(bold=True)
        lines = [("SALES", None), ("SGW", sgw[1]), ("eBay", ebay[1]), ("Amazon", amz[1]), ("GoodwillFinds", gf[1]), ("Books", gb[1])]
        for k, (lab, v) in enumerate(lines, 1):
            ws.cell(row=row + k, column=1, value=lab)
            if v is not None:
                ws.cell(row=row + k, column=2, value=round(v, 2)).number_format = "$#,##0.00"
        ship_lines = [("SGW", sgw[0]), ("eBay", ebay[0]), ("Amazon", amz[0]), ("GoodwillFinds", gf[0]), ("Pick-Ups", pickups)]
        for k, (lab, v) in enumerate(ship_lines, 1):
            ws.cell(row=row + k, column=4, value=lab)
            ws.cell(row=row + k, column=5, value=v)
        total_ship = sum(x[2] for x in (sgw, ebay, amz, gf, gb))
        ws.cell(row=row + 1, column=7, value="Shipping charged")
        ws.cell(row=row + 1, column=8, value=round(total_ship, 2)).number_format = "$#,##0.00"
        for k, c in enumerate(cats, 1):
            ws.cell(row=row + k, column=9, value=c)
            ws.cell(row=row + k, column=10, value=sold[d].get(c, 0))
            ws.cell(row=row + k, column=12, value=c)
            ws.cell(row=row + k, column=13, value=listed[d].get(c, 0))
        for r_ in range(top, top + 7):
            for c_ in range(1, 14):
                ws.cell(row=r_, column=c_).fill = fills[i]
        exp[str(d)] = {"sgw_sales": round(sgw[1], 2), "ebay_sales": round(ebay[1], 2), "amazon_sales": round(amz[1], 2),
                       "goodwillfinds_sales": round(gf[1], 2), "books_sales": round(gb[1], 2), "orders": sgw[0] + ebay[0] + amz[0] + gf[0] + gb[0],
                       "shipping_charged": round(total_ship, 2)}
        row += 9
    for col, w in zip("ABCDEFGHIJKLM", (14, 12, 3, 14, 8, 3, 18, 12, 14, 8, 3, 14, 8)):
        ws.column_dimensions[col].width = w
    wb.save(path)
    return exp


# ------------------------------------------------------------------------------------------------------------ main
README = """# Demo data: what to drop where

Everything here is synthetic. The files copy the layouts in the photos Goodwill staff shared (Upright "Paid orders" export,
the hand-entered Daily Summary Spreadsheet, and the weekly store Daily Sales Sheet). Totals come from the same simulated world as
the warehouse, so a correct import reproduces the numbers in each folder's `expected.json`.
Regenerate with `.venv/bin/python -m goodwill_pulse.gen.demo_pack`.

| Folder | Use it to show | Where to drop it |
|---|---|---|
| `01_tonight_saturday_2026-10-03` | Tonight's two reports become the Daily Pulse | Local app: `POST /api/demo/reset`, then drop both files on the page. Published app: Upload tab |
| `02_friday_2026-10-02` | The file in the photo (`paid_orders_10-02-2026_10-02-2026`). It is a Pacific-day export, so it only partly covers Eastern business days. `eastern_time_export/` holds the same Friday exported in Eastern time | Upload tab. Use the Eastern file to see a full replace and an exact reconcile; use the Pacific file to see the partial-day warning |
| `03_monday_catchup_fri_sat_sun` | Friday, Saturday and Sunday pulled together on Monday | Three daily files, or the single range file in `one_range_file/` |
| `04_messy_reports` | Duplicates, a renamed header, a test order, bad dates, title rows, a wrong-period file, a missing Cash Monkey file | Each file lists what must be caught in `expected.json` |
| `05_store_weekly_sales_supro` | In-store Daily Sales Sheet, 24 stores, with last-year comparison | Reference input for the store side; not part of the e-commerce pulse |
| `06_daily_summary_manual` | The spreadsheet staff fill by hand, filled from the warehouse | Compare with the nightly report: same sales, orders and shipping |

## Real layout notes (from the photo)
- Upright columns: Upright Order ID, Channel, Channel Order ID, Secondary Channel, Channel Buyer ID, Order Item Count, Payment ID,
  Payment Type, Total, Subtotal, Shipping Total, Shipping Label Cost, Handling Total, Tax Total, Donation Total, Currency, Final Value,
  Payment Processing Fee. Names cut off in the photo (`Channel Bu`, `Shipping La`, `Payment P`) are our best reading.
- Total = Subtotal + Shipping Total + Handling Total + Tax Total + Donation Total. ShopGoodwill handling is $3 per item.
- Shipping City/Country/Address/State and Paid At sit past the visible columns, so they are assumed. Confirm with Goodwill.
- Cash Monkey files keep the layout from the kickoff deck (no photo of it yet).
"""


def main() -> None:
    shutil.rmtree(OUT, ignore_errors=True)
    OUT.mkdir(parents=True)
    scenario_single_day(OUT / "01_tonight_saturday_2026-10-03", date(2026, 10, 3), "Tonight's reports (demo day)")
    scenario_single_day(OUT / "02_friday_2026-10-02", date(2026, 10, 2), "The day in the photographed file name")
    scenario_catchup(OUT / "03_monday_catchup_fri_sat_sun", [date(2026, 9, 25), date(2026, 9, 26), date(2026, 9, 27)])
    scenario_messy(OUT / "04_messy_reports", date(2026, 10, 2))
    f5 = OUT / "05_store_weekly_sales_supro"
    f5.mkdir()
    e5 = {}
    for wk, start in ((40, date(2026, 9, 27)), (39, date(2026, 9, 20))):
        e5[f"week_{wk}"] = write_store_weekly(f5 / f"Weekly Sales 2026 Week {wk}.xlsx", start, wk)
    _write_expected(f5, {"scenario": "Brick-and-mortar Daily Sales Sheet, as in the photo", **{k: {kk: vv for kk, vv in v.items() if kk != "by_store"} for k, v in e5.items()},
                         "by_store_week_40": e5["week_40"]["by_store"]})
    f6 = OUT / "06_daily_summary_manual"
    f6.mkdir()
    e6 = write_daily_summary(f6 / "Daily Summary Spreadsheet Wk 40.xlsx", date(2026, 9, 27), "Wk 40")
    _write_expected(f6, {"scenario": "Hand-entered daily summary, filled from the warehouse (layout approximated from a blurry photo)", "days": e6})
    (OUT / "README.md").write_text(README)
    print("wrote", OUT)
    for p in sorted(OUT.rglob("*")):
        if p.is_file():
            print(f"  {p.relative_to(OUT)}  {p.stat().st_size:,} B")


if __name__ == "__main__":
    main()
