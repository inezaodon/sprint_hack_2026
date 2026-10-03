"""Nightly e-commerce report: the email that replaces the morning Upright pull and the manual SUM formulas.

    .venv/bin/python -m goodwill_pulse.digest                      # latest business day, written to data/out/digest/
    .venv/bin/python -m goodwill_pulse.digest --date 2026-10-02
    .venv/bin/python -m goodwill_pulse.digest --catchup            # Monday morning: Friday through Sunday in one report
    .venv/bin/python -m goodwill_pulse.digest --send               # also email it (SMTP_HOST, SMTP_USER, SMTP_PASSWORD,
                                                                   # DIGEST_FROM, DIGEST_TO in the environment)

Totals are the three numbers staff sum by hand today (sales, shipping, order count), by marketplace, plus fees and
refunds. Business days are Eastern, like the 10 PM store report. A single day is compared with the same weekday last
week. The trust line comes from the data-quality checks, so a reader sees whether the numbers can be relied on.
"""
from __future__ import annotations

import argparse
import html
import os
import smtplib
from datetime import date, timedelta
from email.message import EmailMessage
from pathlib import Path

import duckdb

from .config import DATA_DIR, OUT_DIR

HARMONIZED_PATH = DATA_DIR / "harmonized.duckdb"
LABEL = {"shopgoodwill": "ShopGoodwill", "amazon": "Amazon", "ebay": "eBay", "goodwillfinds": "GoodwillFinds",
         "goodwillbooks": "Goodwillbooks"}
ORDER = list(LABEL)
MEASURES = ("orders", "item_sales", "shipping", "fees", "refunds")


def catchup_range(today: date) -> tuple[date, date]:
    """The days nobody pulled over the weekend, as seen on `today` (a Monday): Friday through Sunday."""
    return today - timedelta(days=3), today - timedelta(days=1)


def _totals(con, start: date, end: date) -> dict[str, dict]:
    rows = con.execute("""
        SELECT channel, count(*), sum(subtotal), sum(coalesce(shipping_charged, 0)), sum(coalesce(marketplace_fees, 0)),
               sum(coalesce(refund_amount, 0))
        FROM fct_orders WHERE business_date BETWEEN ? AND ? GROUP BY channel""", [start, end]).fetchall()
    out = {c: dict.fromkeys(MEASURES, 0.0) for c in ORDER}
    for ch, n, sub, ship, fees, ref in rows:
        out[ch] = {"orders": int(n), "item_sales": float(sub or 0), "shipping": float(ship or 0),
                   "fees": float(fees or 0), "refunds": float(ref or 0)}
    return out


def _trust(path: Path) -> dict:
    try:
        con = duckdb.connect(str(path), read_only=True)
        try:
            rows = con.execute("SELECT severity, status, count(*) FROM dq_results GROUP BY 1, 2").fetchall()
        finally:
            con.close()
    except duckdb.Error:
        return {"checks": 0, "failed_errors": 0, "failed_warnings": 0}
    total = sum(n for _, _, n in rows)
    return {"checks": total,
            "failed_errors": sum(n for s, st, n in rows if st != "pass" and s == "error"),
            "failed_warnings": sum(n for s, st, n in rows if st != "pass" and s != "error")}


def build_digest(start: date, end: date | None = None, harmonized_path: Path = HARMONIZED_PATH) -> dict:
    end = end or start
    con = duckdb.connect(str(harmonized_path), read_only=True)
    try:
        cur = _totals(con, start, end)
        prior = _totals(con, start - timedelta(days=7), end - timedelta(days=7))
        last_day = con.execute("SELECT max(business_date) FROM fct_orders").fetchone()[0]
    finally:
        con.close()
    rows = [{"channel": c, "label": LABEL[c], **cur[c], "prior_item_sales": prior[c]["item_sales"],
             "prior_orders": prior[c]["orders"]} for c in ORDER]
    total = {m: sum(r[m] for r in rows) for m in MEASURES}
    total["prior_item_sales"] = sum(r["prior_item_sales"] for r in rows)
    total["prior_orders"] = sum(r["prior_orders"] for r in rows)
    return {"start": start, "end": end, "rows": rows, "total": total, "trust": _trust(harmonized_path),
            "complete": last_day is not None and last_day >= end,
            "compare_label": "same weekday last week" if start == end else "the same days a week earlier"}


def _usd(v: float) -> str:
    return f"-${abs(v):,.2f}" if v < 0 else f"${v:,.2f}"


def _chg(now: float, before: float) -> str:
    return "n/a" if not before else f"{(now - before) / before * 100:+.1f}%"


def _title(d: dict) -> str:
    s, e = d["start"], d["end"]
    return (f"E-commerce report for {s:%A, %B %-d, %Y}" if s == e
            else f"E-commerce report for {s:%a %b %-d} through {e:%a %b %-d, %Y}")


def _trust_line(d: dict) -> str:
    t = d["trust"]
    if not t["checks"]:
        return "Data checks have not been run."
    if t["failed_errors"]:
        return f"{t['failed_errors']} data check(s) failed. Do not rely on these numbers until finance reviews them."
    note = f", {t['failed_warnings']} warning(s) to review" if t["failed_warnings"] else ""
    return f"All {t['checks']} data checks passed their blocking rules{note}."


def render_text(d: dict) -> str:
    t = d["total"]
    lines = [_title(d), "", f"Item sales {_usd(t['item_sales'])} ({_chg(t['item_sales'], t['prior_item_sales'])} vs "
             f"{d['compare_label']}), {t['orders']:,} orders, shipping charged {_usd(t['shipping'])}", "",
             f"{'Marketplace':<15}{'Orders':>8}{'Item sales':>14}{'Shipping':>12}{'Fees':>12}{'Refunds':>12}"]
    for r in d["rows"] + [{**t, "label": "Total"}]:
        lines.append(f"{r['label']:<15}{r['orders']:>8,}{_usd(r['item_sales']):>14}{_usd(r['shipping']):>12}"
                     f"{_usd(r['fees']):>12}{_usd(r['refunds']):>12}")
    lines += ["", _trust_line(d)]
    if not d["complete"]:
        lines.append("Some source reports for this period have not arrived yet; totals may rise.")
    lines.append("Business days are Eastern time. Item sales exclude shipping and tax.")
    return "\n".join(lines)


def render_html(d: dict) -> str:
    t = d["total"]
    td = "padding:6px 10px;border-bottom:1px solid #d8dfdd;text-align:right"
    def row(r, bold=False):
        w = "font-weight:600;" if bold else ""
        cells = "".join(f'<td style="{td};{w}">{v}</td>' for v in (f"{r['orders']:,}", _usd(r['item_sales']),
                        _usd(r['shipping']), _usd(r['fees']), _usd(r['refunds'])))
        return f'<tr><td style="{td};text-align:left;{w}">{html.escape(r["label"])}</td>{cells}</tr>'
    head = "".join(f'<th style="{td};font-weight:600">{h}</th>' for h in ("Orders", "Item sales", "Shipping", "Fees", "Refunds"))
    body = "".join(row(r) for r in d["rows"]) + row({**t, "label": "Total"}, True)
    warn = "" if d["complete"] else '<p style="color:#9a5b00">Some source reports for this period have not arrived yet; totals may rise.</p>'
    return (f'<div style="font:14px/1.5 system-ui,sans-serif;color:#14201e;max-width:640px">'
            f'<h2 style="margin:0 0 6px">{html.escape(_title(d))}</h2>'
            f'<p style="font-size:20px;margin:0 0 12px"><b>{_usd(t["item_sales"])}</b> item sales '
            f'<span style="color:#566663;font-size:13px">({_chg(t["item_sales"], t["prior_item_sales"])} vs {d["compare_label"]})</span>'
            f' &middot; {t["orders"]:,} orders &middot; {_usd(t["shipping"])} shipping</p>'
            f'<table style="border-collapse:collapse;width:100%"><tr><th style="{td};text-align:left;font-weight:600">Marketplace</th>{head}</tr>{body}</table>'
            f'{warn}<p style="color:#566663;font-size:12.5px">{html.escape(_trust_line(d))} Business days are Eastern time. '
            f'Item sales exclude shipping and tax.</p></div>')


def write(d: dict, out_dir: Path | None = None) -> dict[str, Path]:
    out_dir = out_dir or OUT_DIR / "digest"
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = f"ecom-report-{d['start']:%Y%m%d}" + ("" if d["start"] == d["end"] else f"-{d['end']:%Y%m%d}")
    paths = {"txt": out_dir / f"{stem}.txt", "html": out_dir / f"{stem}.html"}
    paths["txt"].write_text(render_text(d))
    paths["html"].write_text(render_html(d))
    return paths


def send(d: dict) -> None:
    host, to = os.environ.get("SMTP_HOST"), os.environ.get("DIGEST_TO")
    if not host or not to:
        raise SystemExit("Set SMTP_HOST and DIGEST_TO (and SMTP_USER, SMTP_PASSWORD, DIGEST_FROM) to send.")
    msg = EmailMessage()
    msg["Subject"], msg["To"] = _title(d), to
    msg["From"] = os.environ.get("DIGEST_FROM", os.environ.get("SMTP_USER", "pulse@localhost"))
    msg.set_content(render_text(d))
    msg.add_alternative(render_html(d), subtype="html")
    with smtplib.SMTP(host, int(os.environ.get("SMTP_PORT", "587"))) as s:
        s.starttls()
        if os.environ.get("SMTP_USER"):
            s.login(os.environ["SMTP_USER"], os.environ.get("SMTP_PASSWORD", ""))
        s.send_message(msg)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--date", type=date.fromisoformat, help="business day (default: latest with orders)")
    p.add_argument("--catchup", action="store_true", help="Friday through Sunday, for Monday morning")
    p.add_argument("--send", action="store_true")
    a = p.parse_args(argv)
    con = duckdb.connect(str(HARMONIZED_PATH), read_only=True)
    latest = con.execute("SELECT max(business_date) FROM fct_orders").fetchone()[0]
    con.close()
    if a.catchup:
        monday = a.date or date.today()
        start, end = catchup_range(monday)
    else:
        start = end = a.date or latest
    d = build_digest(start, end)
    paths = write(d)
    print(render_text(d), "\n\nwritten:", *(str(v) for v in paths.values()), sep="\n")
    if a.send:
        send(d)
        print("sent to", os.environ["DIGEST_TO"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
