"""data/sources/finance.duckdb : the month-end finance inputs that are NOT marketplace exports (slide 38 #3, #4, #5, #7).

Derived from data/sources/_truth.duckdb (shipping_charges, payouts, orders, order_lines, items, refunds), so the bank,
FedEx, jewelry and Goodwill Books numbers agree with every marketplace DB.

Tables (DDL below is the contract for close/rules.py):

  bank_0101            1st Source operating account 0101 activity export. One row per bank line.
                       amount > 0 = deposit, < 0 = withdrawal. Postage vendors are paid weekly by ACH
                       ('OSM WORLDWIDE ACH DEBIT', 'PITNEY BOWES POSTAGE BY PHONE', 'EASYPOST WALLET RELOAD');
                       FedEx invoices are paid by ACH ('FEDEX ACH PAYMENT', reference = invoice no) and FedEx refunds
                       arrive as deposits ('BNKDEPOSIT FEDEX REFUND', type BNKDEPOSIT, reference = refund_ref);
                       marketplace payouts land here ('EBAY PAYOUT', 'AMAZON SETTLEMENT', 'SHOPGOODWILL REMIT',
                       'GOODWILLFINDS PAYOUT', 'GOODWILLBOOKS PAYMENT'; reference = payout_id); plus noise lines
                       (bank service charge, a non-FedEx BNKDEPOSIT) the rules must ignore.
  fedex_invoices       FedEx billing export. Weekly invoices (Sun-Sat, dated the Saturday) with one CHARGE row per
                       tracking number; refunds are credit-memo REFUND rows (amount < 0) dated the credit date,
                       pointing to the original invoice (orig_invoice_no) and to the bank deposit (refund_ref).
  jewelry_report       Upright Jewelry Report (category Jewelry, Upright channels), one row per item sold, by
                       report_month (sale date in America/New_York). Supplier (= originating store_id) is BLANK on
                       ~25% of rows; the close's enrich step fills it by SKU lookup ("Co-Pivot populates Supplier").
  gwb_statement        Goodwill Books monthly payment statement lines (the emailed PDF, as structured data):
                       SALE (+, per item, with sku), SHIPPING (+, per order), FEE (-, per order), REFUND (-).
  gwb_statement_header One row per statement: totals; net = sum of the lines (the control total).
  _dirty_data          Planted cases for the close: table_name, native_key, kind, note.

Planted cases (seeded): one FedEx refund credited 2026-09-29 against a September invoice but deposited 2026-10-01
(cross-month), and one September jewelry row whose SKU was mis-keyed (truncated) with a blank Supplier (unmatchable).

    build(truth_path, out_path, seed=7) -> dict[str, int]
    python -m goodwill_pulse.sources.finance [--truth PATH] [--out PATH] [--seed 7]
"""
from __future__ import annotations

import argparse
import os
from datetime import date, timedelta
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

from goodwill_pulse.config import DATA_DIR

TZ = "America/New_York"
WINDOW_END = date(2026, 10, 3)
CROSS_MONTH_CREDIT = date(2026, 9, 29)
CROSS_MONTH_DEPOSIT = date(2026, 10, 1)
UNMATCHED_JEWELRY_MONTH = "2026-09"

FINANCE_DDL = """
CREATE TABLE IF NOT EXISTS bank_0101 (
    line_id      VARCHAR PRIMARY KEY,      -- 'B0101-000001', in posting order
    account_no   VARCHAR NOT NULL,         -- '0101' (1st Source operating account)
    post_date    DATE NOT NULL,            -- bank posting date
    description  VARCHAR NOT NULL,         -- 'OSM WORLDWIDE ACH DEBIT', 'BNKDEPOSIT FEDEX REFUND', 'EBAY PAYOUT', ...
    amount       DECIMAL(12,2) NOT NULL,   -- > 0 deposit, < 0 withdrawal
    type         VARCHAR NOT NULL,         -- ACH_DEBIT | ACH_CREDIT | BNKDEPOSIT | SERVICE_CHARGE
    reference    VARCHAR                   -- payout_id / FedEx invoice no / FedEx refund_ref / vendor batch id
);
CREATE TABLE IF NOT EXISTS fedex_invoices (
    line_id         VARCHAR PRIMARY KEY,   -- 'FX-0000001'
    invoice_no      VARCHAR NOT NULL,      -- weekly invoice '7-123-45678'; credit memo 'CM-7-123-45678'
    invoice_date    DATE NOT NULL,         -- invoice: Saturday ending the ship week; credit memo: credit date
    line_type       VARCHAR NOT NULL,      -- CHARGE | REFUND
    tracking        VARCHAR,
    ship_date       DATE,
    amount          DECIMAL(12,2) NOT NULL,-- CHARGE > 0, REFUND < 0
    orig_invoice_no VARCHAR,               -- REFUND: invoice that carried the original charge
    refund_ref      VARCHAR                -- REFUND: reference on the BNKDEPOSIT line in bank_0101
);
CREATE TABLE IF NOT EXISTS jewelry_report (
    report_month VARCHAR NOT NULL,         -- 'YYYY-MM'
    line_no      INTEGER NOT NULL,
    channel      VARCHAR,                  -- shopgoodwill | ebay | goodwillfinds
    order_ref    VARCHAR,                  -- marketplace order id
    sale_date    DATE,                     -- America/New_York
    sku          VARCHAR,
    title        VARCHAR,
    sale_price   DECIMAL(12,2),
    supplier     VARCHAR,                  -- originating store 'Store03'; NULL/blank = to be filled (Co-Pivot)
    PRIMARY KEY (report_month, line_no)
);
CREATE TABLE IF NOT EXISTS gwb_statement (
    statement_month VARCHAR NOT NULL,      -- 'YYYY-MM' (month the statement covers)
    line_no         INTEGER NOT NULL,
    line_type       VARCHAR NOT NULL,      -- SALE | SHIPPING | FEE | REFUND
    order_no        VARCHAR,               -- 'GWB123456'
    line_date       DATE,
    sku             VARCHAR,               -- SALE / REFUND
    description     VARCHAR,
    amount          DECIMAL(12,2) NOT NULL,-- SALE / SHIPPING > 0, FEE / REFUND < 0
    PRIMARY KEY (statement_month, line_no)
);
CREATE TABLE IF NOT EXISTS gwb_statement_header (
    statement_month VARCHAR PRIMARY KEY,
    statement_date  DATE,                  -- first day of the following month (emailed PDF)
    gross           DECIMAL(12,2),         -- SALE + SHIPPING
    fees            DECIMAL(12,2),         -- positive
    refunds         DECIMAL(12,2),         -- positive
    net             DECIMAL(12,2),         -- = sum of statement lines (control total)
    paid_on         DATE
);
CREATE TABLE IF NOT EXISTS _dirty_data (
    table_name VARCHAR, native_key VARCHAR, kind VARCHAR, note VARCHAR
);
"""

POSTAGE = {  # carrier -> (bank description, days after the Saturday week end, reference prefix)
    "osm": ("OSM WORLDWIDE ACH DEBIT", 3, "OSM"),
    "pitney_bowes": ("PITNEY BOWES POSTAGE BY PHONE", 2, "PB"),
    "easypost": ("EASYPOST WALLET RELOAD", 4, "EP"),
}
PAYOUT_DESC = {
    "ebay": "EBAY PAYOUT", "amazon": "AMAZON SETTLEMENT", "shopgoodwill": "SHOPGOODWILL REMIT",
    "goodwillfinds": "GOODWILLFINDS PAYOUT", "goodwillbooks": "GOODWILLBOOKS PAYMENT",
}


def _week_end(d: date) -> date:
    """Saturday ending the Sun-Sat week containing d."""
    return d + timedelta(days=(5 - d.weekday()) % 7)


def _bizday(d: date) -> date:
    while d.weekday() >= 5:
        d += timedelta(days=1)
    return d


def _r2(x) -> float:
    return round(float(x) + 0.0, 2)


def _bank(shipping: pd.DataFrame, fedex_inv: pd.DataFrame, fedex_cm: pd.DataFrame, payouts: pd.DataFrame,
          months: list[date], rng) -> pd.DataFrame:
    rows = []
    post = shipping[shipping.carrier.isin(list(POSTAGE))].copy()
    post["week_end"] = post["d"].map(_week_end)
    for (carrier, we), g in post.groupby(["carrier", "week_end"], sort=True):
        desc, lag, pre = POSTAGE[carrier]
        pd_ = _bizday(we + timedelta(days=lag))
        amt = _r2(g["amount"].sum())
        if pd_ <= WINDOW_END and amt > 0:
            rows.append((pd_, desc, -amt, "ACH_DEBIT", f"{pre}-{we:%Y%m%d}"))
    for inv, g in fedex_inv.groupby("invoice_no", sort=False):
        pd_ = _bizday(g["invoice_date"].iloc[0] + timedelta(days=7))
        if pd_ <= WINDOW_END:
            rows.append((pd_, "FEDEX ACH PAYMENT", -_r2(g["amount"].sum()), "ACH_DEBIT", inv))
    for r in fedex_cm.itertuples():
        if r.deposit_date <= WINDOW_END:
            rows.append((r.deposit_date, "BNKDEPOSIT FEDEX REFUND", _r2(-r.amount), "BNKDEPOSIT", r.refund_ref))
    for r in payouts.itertuples():
        if r.paid_on <= WINDOW_END:
            rows.append((r.paid_on, PAYOUT_DESC.get(r.channel, f"{r.channel.upper()} PAYOUT"), _r2(r.net),
                         "ACH_CREDIT", str(r.payout_id)))
    for m in months:  # noise the rules must ignore
        nxt = (m.replace(day=28) + timedelta(days=4)).replace(day=1)
        last = nxt - timedelta(days=1)
        while last.weekday() >= 5:
            last -= timedelta(days=1)
        if last <= WINDOW_END:
            rows.append((last, "SERVICE CHARGE 1ST SOURCE", -45.00, "SERVICE_CHARGE", f"SC-{m:%Y%m}"))
        dep = _bizday(m + timedelta(days=int(rng.integers(3, 20))))
        if dep <= WINDOW_END:
            rows.append((dep, "BNKDEPOSIT STORE CASH ECOM", _r2(rng.uniform(40, 400)), "BNKDEPOSIT", f"DEP-{m:%Y%m}"))
    df = pd.DataFrame(rows, columns=["post_date", "description", "amount", "type", "reference"])
    df = df.sort_values(["post_date", "description", "reference"], kind="mergesort").reset_index(drop=True)
    df.insert(0, "line_id", [f"B0101-{i + 1:06d}" for i in range(len(df))])
    df.insert(1, "account_no", "0101")
    return df


def build(truth_path: Path = DATA_DIR / "sources" / "_truth.duckdb",
          out_path: Path = DATA_DIR / "sources" / "finance.duckdb", seed: int = 7) -> dict[str, int]:
    truth_path, out_path = Path(truth_path), Path(out_path)
    rng = np.random.default_rng(seed)
    t = duckdb.connect(str(truth_path), read_only=True)
    try:
        shipping = t.execute(f"""
            SELECT charge_id, carrier, order_id, (charged_at AT TIME ZONE '{TZ}')::DATE AS d, amount::DOUBLE AS amount,
                   tracking FROM shipping_charges ORDER BY charged_at, charge_id""").df()
        payouts = t.execute("SELECT payout_id, channel, period_start, period_end, paid_on, net::DOUBLE AS net "
                            "FROM payouts ORDER BY paid_on, payout_id").df()
        jewelry = t.execute(f"""
            SELECT o.channel, o.marketplace_order_id AS order_ref, (o.paid_at AT TIME ZONE '{TZ}')::DATE AS sale_date,
                   ol.item_id AS sku, i.title, ol.sale_price::DOUBLE AS sale_price, i.store_id
            FROM order_lines ol JOIN orders o USING (order_id) JOIN items i USING (item_id)
            WHERE i.category = 'Jewelry' AND o.channel IN ('shopgoodwill','ebay','goodwillfinds')
            ORDER BY o.paid_at, o.order_id, ol.line_no""").df()
        gwb_lines = t.execute(f"""
            SELECT o.order_id, o.marketplace_order_id AS order_no, (o.paid_at AT TIME ZONE '{TZ}')::DATE AS d,
                   ol.line_no, ol.item_id AS sku, i.title, ol.sale_price::DOUBLE AS sale_price,
                   (coalesce(o.shipping_charged,0) + coalesce(o.handling,0))::DOUBLE AS ship,
                   (coalesce(o.marketplace_fee,0) + coalesce(o.payment_fee,0))::DOUBLE AS fees
            FROM orders o JOIN order_lines ol USING (order_id) JOIN items i USING (item_id)
            WHERE o.channel = 'goodwillbooks' ORDER BY o.paid_at, o.order_id, ol.line_no""").df()
        gwb_refunds = t.execute(f"""
            SELECT r.refund_id, o.marketplace_order_id AS order_no, (r.refunded_at AT TIME ZONE '{TZ}')::DATE AS d,
                   r.amount::DOUBLE AS amount,
                   (SELECT item_id FROM order_lines x WHERE x.order_id = o.order_id ORDER BY line_no LIMIT 1) AS sku
            FROM refunds r JOIN orders o USING (order_id) WHERE o.channel = 'goodwillbooks'
            ORDER BY r.refunded_at, r.refund_id""").df()
    finally:
        t.close()
    for df, cols in ((shipping, ["d"]), (payouts, ["period_start", "period_end", "paid_on"]), (jewelry, ["sale_date"]),
                     (gwb_lines, ["d"]), (gwb_refunds, ["d"])):
        for c in cols:
            df[c] = pd.to_datetime(df[c]).dt.date
    dirty = []

    # ---- FedEx: weekly invoices of charges, credit memos for refunds
    fx = shipping[shipping.carrier == "fedex"].copy()
    charges = fx[fx.amount > 0].copy()
    charges["invoice_date"] = charges["d"].map(_week_end)
    charges = charges[charges.invoice_date <= WINDOW_END]
    inv_no = {}
    for we in sorted(charges.invoice_date.unique()):
        inv_no[we] = f"7-{int(rng.integers(100, 999))}-{int(rng.integers(10000, 99999))}"
    charges["invoice_no"] = charges.invoice_date.map(inv_no)
    inv_by_tracking = dict(zip(charges.tracking, charges.invoice_no))
    cms = fx[fx.amount < 0].copy()
    cms["invoice_date"] = cms["d"]
    cms["orig_invoice_no"] = [inv_by_tracking.get(tr, inv_no.get(_week_end(d))) for tr, d in zip(cms.tracking, cms.d)]
    cms["deposit_date"] = cms["d"].map(_bizday)
    # natural refunds deposit the next business day; keep them inside their own month so the only cross-month case
    # is the planted one
    cms["deposit_date"] = [dd if (dd.year, dd.month) == (d.year, d.month) else d for dd, d in zip(cms.deposit_date, cms.d)]
    # planted cross-month refund: credit 2026-09-29 for a September-invoiced shipment, deposited 2026-10-01
    sept = charges[(charges.invoice_date >= date(2026, 9, 1)) & (charges.invoice_date <= date(2026, 9, 30))
                   & ~charges.tracking.isin(cms.tracking)]
    if len(sept):
        pick = sept.iloc[int(rng.integers(len(sept)))]
        amt = -round(float(pick.amount) * float(rng.uniform(0.35, 0.6)), 2)
        cms = pd.concat([cms, pd.DataFrame([{
            "charge_id": f"{pick.charge_id}-ADJ", "carrier": "fedex", "order_id": pick.order_id,
            "d": CROSS_MONTH_CREDIT, "amount": amt, "tracking": pick.tracking, "invoice_date": CROSS_MONTH_CREDIT,
            "orig_invoice_no": pick.invoice_no, "deposit_date": CROSS_MONTH_DEPOSIT, "planted": True}])],
            ignore_index=True)
    cms = cms.sort_values(["invoice_date", "charge_id"], kind="mergesort").reset_index(drop=True)
    cms["refund_ref"] = [f"FXR-{100001 + i}" for i in range(len(cms))]
    cms["invoice_no"] = ["CM-" + str(o) for o in cms.orig_invoice_no]
    if len(sept):
        p = cms[cms.get("planted", False) == True].iloc[0]  # noqa: E712
        dirty.append(("fedex_invoices", p.refund_ref, "cross_month_refund",
                      f"FedEx address-correction credit {p.refund_ref} ${-p.amount:,.2f} dated {CROSS_MONTH_CREDIT} for "
                      f"September invoice {p.orig_invoice_no} (tracking {p.tracking}); BNKDEPOSIT lands "
                      f"{CROSS_MONTH_DEPOSIT} in bank 0101, after the September cut-off"))
    fedex = pd.concat([
        charges.assign(line_type="CHARGE", orig_invoice_no=None, refund_ref=None)[
            ["invoice_no", "invoice_date", "line_type", "tracking", "d", "amount", "orig_invoice_no", "refund_ref"]],
        cms.assign(line_type="REFUND")[
            ["invoice_no", "invoice_date", "line_type", "tracking", "d", "amount", "orig_invoice_no", "refund_ref"]],
    ], ignore_index=True).rename(columns={"d": "ship_date"})
    fedex = fedex.sort_values(["invoice_date", "line_type", "invoice_no", "tracking"], kind="mergesort")
    fedex = fedex.reset_index(drop=True)
    fedex.insert(0, "line_id", [f"FX-{i + 1:07d}" for i in range(len(fedex))])
    fedex["amount"] = fedex["amount"].round(2)

    months = sorted({date(d.year, d.month, 1) for d in shipping["d"]} | {date(2026, 9, 1)})
    bank = _bank(shipping, charges, cms, payouts, months, rng)

    # ---- Jewelry report: blank Supplier on ~25% of rows, one unmatchable SKU in Sept 2026
    j = jewelry.copy()
    j["report_month"] = [f"{d.year:04d}-{d.month:02d}" for d in j.sale_date]
    j["line_no"] = j.groupby("report_month").cumcount() + 1
    j["supplier"] = [None if rng.random() < 0.25 else s for s in j.store_id]
    sep = j.index[j.report_month == UNMATCHED_JEWELRY_MONTH]
    if len(sep):
        k = sep[int(rng.integers(len(sep)))]
        good = j.at[k, "sku"]
        j.at[k, "sku"] = good[:-1]          # last digit dropped while keying
        j.at[k, "supplier"] = None
        dirty.append(("jewelry_report", f"{UNMATCHED_JEWELRY_MONTH}/{j.at[k, 'line_no']}", "unmatched_sku",
                      f"SKU keyed as '{good[:-1]}' (should be '{good}', {j.at[k, 'store_id']}); Supplier blank; "
                      f"${j.at[k, 'sale_price']:,.2f}"))
    jr = j[["report_month", "line_no", "channel", "order_ref", "sale_date", "sku", "title", "sale_price", "supplier"]]

    # ---- Goodwill Books statements (complete months only: statement emailed the 1st of the next month)
    st_rows = []
    gwb_lines["m"] = [date(d.year, d.month, 1) for d in gwb_lines.d]
    gwb_refunds["m"] = [date(d.year, d.month, 1) for d in gwb_refunds.d]
    gwb_pay = payouts[payouts.channel == "goodwillbooks"]
    for m in sorted(set(gwb_lines.m) | set(gwb_refunds.m)):
        nxt = (m.replace(day=28) + timedelta(days=4)).replace(day=1)
        if nxt > WINDOW_END:
            continue
        ym = f"{m.year:04d}-{m.month:02d}"
        lines = []
        g = gwb_lines[gwb_lines.m == m]
        for oid, og in g.groupby("order_id", sort=False):
            o = og.iloc[0]
            for r in og.itertuples():
                lines.append(("SALE", r.order_no, r.d, r.sku, f"Item sale {r.title}"[:80], _r2(r.sale_price)))
            if o.ship:
                lines.append(("SHIPPING", o.order_no, o.d, None, "Shipping & handling", _r2(o.ship)))
            if o.fees:
                lines.append(("FEE", o.order_no, o.d, None, "Platform fee", -_r2(o.fees)))
        for r in gwb_refunds[gwb_refunds.m == m].itertuples():
            lines.append(("REFUND", r.order_no, r.d, r.sku, f"Refund {r.refund_id}", -_r2(r.amount)))
        for i, (lt, on, d, sku, desc, amt) in enumerate(lines, 1):
            st_rows.append((ym, i, lt, on, d, sku, desc, amt))
        gross = sum(a for lt, *_, a in lines if lt in ("SALE", "SHIPPING"))
        fees = -sum(a for lt, *_, a in lines if lt == "FEE")
        refs = -sum(a for lt, *_, a in lines if lt == "REFUND")
        pay = gwb_pay[(gwb_pay.period_start <= m) & (gwb_pay.period_end >= m)]
        paid_on = pay.paid_on.iloc[0] if len(pay) else nxt + timedelta(days=14)
        st_rows_h = (ym, nxt, round(gross, 2), round(fees, 2), round(refs, 2),
                     round(sum(l[-1] for l in lines), 2), paid_on)
        st_rows.append(("__H__",) + st_rows_h)
    hdr = pd.DataFrame([r[1:] for r in st_rows if r[0] == "__H__"],
                       columns=["statement_month", "statement_date", "gross", "fees", "refunds", "net", "paid_on"])
    stl = pd.DataFrame([r for r in st_rows if r[0] != "__H__"],
                       columns=["statement_month", "line_no", "line_type", "order_no", "line_date", "sku",
                                "description", "amount"])
    dd = pd.DataFrame(dirty, columns=["table_name", "native_key", "kind", "note"])

    out_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = out_path.with_suffix(".duckdb.tmp")
    if tmp.exists():
        tmp.unlink()
    con = duckdb.connect(str(tmp))
    try:
        con.execute(FINANCE_DDL)
        tables = {"bank_0101": bank, "fedex_invoices": fedex.drop(columns=[]), "jewelry_report": jr,
                  "gwb_statement": stl, "gwb_statement_header": hdr, "_dirty_data": dd}
        counts = {}
        for name, df in tables.items():
            if len(df):
                con.register("_df", df)
                cols = ", ".join(df.columns)
                con.execute(f"INSERT INTO {name} ({cols}) SELECT {cols} FROM _df")
                con.unregister("_df")
            counts[name] = con.execute(f"SELECT count(*) FROM {name}").fetchone()[0]
        con.execute("CHECKPOINT")
    finally:
        con.close()
    os.replace(tmp, out_path)
    return counts


def main(argv=None):
    ap = argparse.ArgumentParser(description="Build data/sources/finance.duckdb from the truth world")
    ap.add_argument("--truth", default=str(DATA_DIR / "sources" / "_truth.duckdb"))
    ap.add_argument("--out", default=str(DATA_DIR / "sources" / "finance.duckdb"))
    ap.add_argument("--seed", type=int, default=7)
    a = ap.parse_args(argv)
    for k, v in build(Path(a.truth), Path(a.out), a.seed).items():
        print(f"{k:22s} {v:>8,}")


if __name__ == "__main__":
    main()
