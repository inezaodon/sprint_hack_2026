"""Daily Pulse: revenue and customer count by marketplace, then enterprise totals (Goodwill deck, slide 31)."""
from __future__ import annotations

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import duckdb

from .config import business_tz, channels_config, mappings


def _day_window(d: date) -> tuple[datetime, datetime]:
    tz = ZoneInfo(business_tz())
    start = datetime.combine(d, time(0), tz)
    return start, start + timedelta(days=1)


def _rollup(con: duckdb.DuckDBPyConnection, start: date, end: date, before: datetime | None = None) -> dict[str, dict]:
    """Revenue, orders and distinct buyers per channel for business dates in [start, end], optionally only
    orders paid before `before` (so a 10 PM pulse is compared with last week up to 10 PM, not the full day)."""
    basis = channels_config()["revenue_basis"]
    cutoff = "AND paid_at_utc < ?" if before else ""
    rows = con.execute(f"""
        SELECT channel, sum({basis})::DOUBLE, count(*), count(DISTINCT buyer_key)
        FROM orders WHERE business_date BETWEEN ? AND ? {cutoff} GROUP BY channel""",
        [start, end] + ([before] if before else [])).fetchall()
    return {ch: {"revenue": rev or 0.0, "customers": n, "buyers": b} for ch, rev, n, b in rows}


def _rows(by_channel: dict[str, dict]) -> list[dict]:
    out = []
    for row in channels_config()["pulse_rows"]:
        vals = [by_channel.get(c, {"revenue": 0.0, "customers": 0, "buyers": 0}) for c in row["channels"]]
        out.append({"label": row["label"], "channels": row["channels"],
                    "revenue": round(sum(v["revenue"] for v in vals), 2),
                    "customers": sum(v["customers"] for v in vals),
                    "buyers": sum(v["buyers"] for v in vals)})
    return out


def freshness(con: duckdb.DuckDBPyConnection, d: date) -> list[dict]:
    """For each report we expect daily: did it arrive, and does it cover the whole business day?"""
    start, end = _day_window(d)
    out = []
    for rtype in channels_config()["expected_daily_sources"]:
        files = con.execute("""
            SELECT original_name, coverage_start, coverage_end, latest_order_at, status, received_at, loaded_rows
            FROM report_files
            WHERE report_type = ? AND status IN ('loaded','partial','needs_mapping')
              AND coverage_start < ? AND coverage_end > ?
            ORDER BY coverage_start""", [rtype, end, start]).fetchall()
        usable = [f for f in files if f[4] in ("loaded", "partial")]
        covered_from = min((f[1] for f in usable), default=None)
        covered_to = max((f[2] for f in usable), default=None)
        latest = max((f[3] for f in usable if f[3]), default=None)
        if not usable:
            status = "needs_mapping" if files else "missing"
        elif covered_from > start or covered_to < end:
            status = "partial"
        else:
            status = "complete"
        out.append({
            "report_type": rtype, "label": mappings()[rtype]["label"], "status": status,
            "files": [f[0] for f in files],
            "latest_order_at": latest.isoformat() if latest else None,
            "covered_through": min(covered_to, end).astimezone(start.tzinfo).isoformat() if covered_to else None,
            "has_errors": any(f[4] == "partial" for f in files),
        })
    return out


def build_pulse(con: duckdb.DuckDBPyConnection, d: date) -> dict:
    fresh = freshness(con, d)
    _, day_end = _day_window(d)
    # The pulse is "as of" the earliest point every received report reaches
    reached = [datetime.fromisoformat(s["covered_through"]) for s in fresh if s["covered_through"]]
    as_of = min(reached + [day_end])
    today = _rows(_rollup(con, d, d))
    last_week = _rows(_rollup(con, d - timedelta(days=7), d - timedelta(days=7),
                              before=as_of - timedelta(days=7) if as_of < day_end else None))
    mtd = _rows(_rollup(con, d.replace(day=1), d))
    for r, lw, m in zip(today, last_week, mtd):
        r["revenue_last_week"], r["customers_last_week"] = lw["revenue"], lw["customers"]
        r["revenue_mtd"] = m["revenue"]

    def total(rows):
        return {k: round(sum(r[k] for r in rows), 2) for k in ("revenue", "customers", "buyers")}

    tot = total(today)
    tot.update(revenue_last_week=round(sum(r["revenue"] for r in last_week), 2),
               customers_last_week=sum(r["customers"] for r in last_week),
               revenue_mtd=round(sum(r["revenue"] for r in mtd), 2))
    open_exc = con.execute("""
        SELECT count(*) FROM exceptions e JOIN report_files f USING (file_id)
        WHERE e.status = 'open' AND e.severity IN ('error','warning')
          AND f.coverage_start < ? AND f.coverage_end > ?""", list(reversed(_day_window(d)))).fetchone()[0]
    return {
        "business_date": d.isoformat(),
        "weekday": d.strftime("%A"),
        "timezone": business_tz(),
        "revenue_basis": channels_config()["revenue_basis"],
        "rows": today,
        "total": tot,
        "sources": fresh,
        "open_exceptions": open_exc,
        "complete": all(s["status"] == "complete" for s in fresh),
        "as_of": as_of.isoformat() if as_of < day_end else None,
        "generated_at": datetime.now(ZoneInfo(business_tz())).isoformat(timespec="seconds"),
    }


def business_dates(con: duckdb.DuckDBPyConnection) -> list[str]:
    return [r[0].isoformat() for r in con.execute(
        "SELECT DISTINCT business_date FROM orders ORDER BY 1 DESC").fetchall()]
