"""/api/close: month-end close (Feature 3). Owned by Engineer 9; see docs/CONTRACT.md sections 5 and 7.

    GET  /api/close/months                       months with data, newest first, and which one to show by default
    GET  /api/close/run?month=2026-09            the whole close screen as JSON (stages, sources, control totals,
                                                 journal by document, invoices, workbook comparison, exceptions)
    POST /api/close/approve?month=2026-09        body {approver, note, override}; 409 while error exceptions are open
                                                 unless override=true with a note
    GET  /api/close/export?month=&kind=journal|invoice|invoice_json|reconciliation   the file (journal/invoice only
                                                 after approval)

The CloseRun from `close.rules.run_close` is cached per month in memory; `?refresh=1` rebuilds it.
Outputs are written to data/out/close/<yyyy-mm>/.
"""
from __future__ import annotations

import json
import threading
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path

import duckdb
from fastapi import APIRouter, Body, HTTPException, Query
from fastapi.responses import FileResponse

from ..close import bc_excel, invoice as inv_mod, reconcile as rec_mod, workbook_compare as wc_mod
from ..close.journal import Journal, build_journal
from ..config import CONFIG_DIR, DATA_DIR, OUT_DIR

router = APIRouter(prefix="/api/close", tags=["close"])

# Paths (module globals so tests can monkeypatch them).
HARMONIZED_PATH = DATA_DIR / "harmonized.duckdb"
FINANCE_PATH = DATA_DIR / "sources" / "finance.duckdb"
RULES_PATH = CONFIG_DIR / "close_rules.yaml"
CLOSE_INPUTS_DIR = DATA_DIR / "close_inputs"
OUT_ROOT = OUT_DIR / "close"

STAGES = [("acquire", "Acquire"), ("archive", "Archive"), ("enrich", "Enrich"), ("rules", "Apply rules"),
          ("output", "BC output"), ("reconcile", "Post + reconcile")]

# The nine source workflows on slide 38, with the CloseRun `source` names (and journal doc suffixes) that feed them.
SOURCES = [
    ("cashmonkey", "Cash Monkey", "Orders, full month", ["cashmonkey", "cash_monkey", "harmonized.fct_orders:amazon",
                                                         "harmonized.fct_orders:goodwillbooks"]),
    ("upright", "Upright", "Paid order items, full month", ["upright", "paid_order_items",
                                                           "harmonized.fct_orders:goodwillfinds"]),
    ("jewelry", "Jewelry", "Jewelry Report; Co-Pivot fills Supplier", ["jewelry_report", "jewelry"]),
    ("postage", "OSM / Pitney Bowes / EasyPost", "Bank 0101 → GL 10009", ["bank_0101", "shipping", "postage"]),
    ("fedex", "FedEx", "GL 40356 · Dept 180 · V00122, net BNKDEPOSIT refunds", ["fedex_invoices", "fedex"]),
    ("shopgoodwill", "ShopGoodwill", "Period 1 periodic only; Period 3 all reports",
     ["shopgoodwill", "sgw", "harmonized.fct_payouts+fct_orders"]),
    ("goodwillbooks", "Goodwill Books", "Prior-month payment statement (PDF)", ["gwb_statement", "goodwillbooks",
                                                                              "gwb"]),
    ("ebay", "eBay", "Listing sales report", ["ebay"]),
    ("amazon", "Amazon", "Payments summary", ["amazon"]),
]

_lock = threading.Lock()
_cache: dict[str, "CloseView"] = {}


class CloseNotReady(Exception):
    pass


@dataclass
class CloseView:
    month: date
    run: object
    journal: Journal
    invoices: list
    recon: list
    comparison: dict
    exceptions: list[dict]
    out_dir: Path
    generated_at: str
    approval: rec_mod.Approval | None = None
    files: dict[str, Path] = field(default_factory=dict)


# --- helpers ----------------------------------------------------------------------------------------------------
def _parse_month(month: str | None) -> date:
    if not month:
        months = _months()
        if not months:
            raise HTTPException(503, "No months available yet: build data/harmonized.duckdb first "
                                     "(.venv/bin/python -m goodwill_pulse.build).")
        return date.fromisoformat(next((m["month"] for m in months if m["default"]), months[0]["month"]) + "-01")
    try:
        return date.fromisoformat(month[:7] + "-01")
    except ValueError:
        raise HTTPException(422, f"month must look like 2026-09, got {month!r}")


def _months() -> list[dict]:
    if not HARMONIZED_PATH.exists():
        return []
    con = duckdb.connect(str(HARMONIZED_PATH), read_only=True)
    try:
        rows = con.execute("SELECT DISTINCT date_trunc('month', business_date)::DATE AS m, max(business_date) "
                           "OVER (PARTITION BY date_trunc('month', business_date)) FROM fct_orders "
                           "ORDER BY 1 DESC").fetchall()
    except duckdb.Error:
        return []
    finally:
        con.close()
    out, seen, default_set = [], set(), False
    for m, last in rows:
        if m in seen:
            continue
        seen.add(m)
        complete = last >= bc_excel_month_end(m)
        has_wb = wc_mod.workbook_path(CLOSE_INPUTS_DIR, m).exists()
        is_default = complete and not default_set
        default_set |= is_default
        out.append({"month": f"{m:%Y-%m}", "label": f"{m:%B %Y}", "complete": complete, "has_workbook": has_wb,
                    "approved": bool(_cache.get(f"{m:%Y-%m}") and _cache[f"{m:%Y-%m}"].approval
                                     and _cache[f"{m:%Y-%m}"].approval.approved),
                    "default": is_default})
    return out


def bc_excel_month_end(m: date) -> date:
    nxt = date(m.year + (m.month == 12), m.month % 12 + 1, 1)
    return date.fromordinal(nxt.toordinal() - 1)


def _run_close(month: date):
    """Call Engineer 8's rules engine. Raises CloseNotReady with a human message if it can't run yet."""
    try:
        from ..close.rules import run_close
    except ImportError as e:
        raise CloseNotReady(f"The close rules engine (goodwill_pulse/close/rules.py) is not available yet: {e}")
    missing = [p for p in (HARMONIZED_PATH, FINANCE_PATH, RULES_PATH) if not p.exists()]
    if missing:
        raise CloseNotReady("Close inputs not built yet: missing " + ", ".join(str(p.relative_to(DATA_DIR.parent))
                                                                           for p in missing)
                            + ". Run .venv/bin/python -m goodwill_pulse.build.")
    return run_close(month, HARMONIZED_PATH, FINANCE_PATH, RULES_PATH)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def build_view(month: date, run) -> CloseView:
    """Everything after the rules engine: journal, invoices, reconciliation, workbook compare, files."""
    journal = build_journal(run)
    invoices, inv_issues = inv_mod.build_invoices(journal)
    base_exc = [dict(e) for e in (run.exceptions or [])]
    recon = rec_mod.reconcile(list(run.source_totals or []), journal, base_exc)
    comparison = wc_mod.compare_with_file(journal, wc_mod.workbook_path(CLOSE_INPUTS_DIR, month))
    exceptions = (base_exc + journal.issues + inv_issues + rec_mod.recon_exceptions(recon)
                  + wc_mod.comparison_exceptions(comparison))
    seen, uniq = set(), []
    for e in exceptions:
        e.setdefault("status", "open")
        e["id"] = rec_mod.exception_id(e)
        if e["id"] not in seen:
            seen.add(e["id"])
            uniq.append(e)
    out_dir = OUT_ROOT / f"{month:%Y-%m}"
    view = CloseView(month, run, journal, invoices, recon, comparison, uniq, out_dir, _now())
    _write_outputs(view)
    return view


def _write_outputs(v: CloseView) -> None:
    v.files["journal"] = bc_excel.write_journal_xlsx(v.journal, v.out_dir)
    v.files["invoice"] = inv_mod.write_invoice_xlsx(v.journal, v.invoices, v.out_dir)
    v.files["invoice_json"] = inv_mod.write_invoice_json(v.journal, v.invoices, v.out_dir)
    v.files["reconciliation"] = rec_mod.write_reconciliation_xlsx(v.month, v.recon, v.journal, v.exceptions,
                                                                  v.comparison, v.approval, v.out_dir)
    record = {"month": f"{v.month:%Y-%m}", "generated_at": v.generated_at,
              "rules_version": v.journal.rules_version, "journal": v.journal.summary(),
              "invoices": [i.to_dict() for i in v.invoices],
              "reconciliation": [r.to_dict() for r in v.recon],
              "workbook_comparison": {k: v.comparison.get(k) for k in ("status", "matches", "workbook")}
              | {"differences": len(v.comparison.get("differences", []))},
              "exceptions": v.exceptions, "approval": v.approval.to_dict() if v.approval else None,
              "outputs": {k: p.name for k, p in v.files.items()}}
    (v.out_dir / "close_run.json").write_text(json.dumps(record, indent=2, default=str) + "\n")


def get_view(month: date, refresh: bool = False) -> CloseView:
    key = f"{month:%Y-%m}"
    with _lock:
        if refresh or key not in _cache:
            try:
                run = _run_close(month)
            except CloseNotReady as e:
                raise HTTPException(503, str(e))
            except FileNotFoundError as e:
                raise HTTPException(503, f"Close data not ready: {e}")
            prior = _cache.get(key)
            view = build_view(month, run)
            if prior and prior.approval and prior.approval.approved and not refresh:
                view.approval = prior.approval
            _cache[key] = view
        return _cache[key]


# --- presentation -----------------------------------------------------------------------------------------------
def _match_source(aliases: list[str], recon: list, journal: Journal) -> tuple[list, list]:
    def hit(name: str) -> bool:
        n = name.lower()
        return any(a == n or n.endswith(":" + a) or a in n.split(".") or n.startswith(a + ":") or n == a
                   for a in aliases) or n in aliases
    return [r for r in recon if hit(r.source)], [d for d in journal.documents if hit(d.source)]


def _sources(v: CloseView) -> list[dict]:
    out, used = [], set()
    for sid, label, workflow, aliases in SOURCES:
        rows, docs = _match_source(aliases, v.recon, v.journal)
        rows = [r for r in rows if r.source not in used] or rows
        for r in rows:
            used.add(r.source)
        exc = [e for e in v.exceptions if str(e.get("source", "")).lower() in
               {a for a in aliases} | {r.source.lower() for r in rows}]
        if any(r.status == "missing" for r in rows) or any(e.get("rule_id") == "missing_source" for e in exc):
            status, detail = "missing", "Not received for this month"
        elif not rows and not docs:
            status, detail = "not_tracked", "Covered by the harmonized orders; no separate file in this run"
        elif any(r.status == "break" for r in rows) or any(e.get("severity") == "error" for e in exc):
            status, detail = "error", "Received; a rule or total needs attention"
        elif any(r.status == "explained" for r in rows) or exc:
            status, detail = "warning", "Received; see exceptions"
        else:
            status, detail = "ok", "Received and reconciled"
        out.append({"id": sid, "label": label, "workflow": workflow, "status": status, "detail": detail,
                    "rows": sum(r.rows or 0 for r in rows) if rows else None,
                    "file_total": _sum([r.file_total for r in rows]),
                    "journal_total": _sum([r.journal_total for r in rows]),
                    "documents": sorted({d.document_no for d in docs}),
                    "run_sources": [r.source for r in rows], "exceptions": len(exc)})
    return out


def _sum(vals):
    vals = [v for v in vals if v is not None]
    return float(sum(vals)) if vals else None


def _stages(v: CloseView, sources: list[dict]) -> list[dict]:
    exc = v.exceptions
    def worst(items):
        sev = {str(e.get("severity")) for e in items if e.get("status", "open") == "open"}
        return "bad" if "error" in sev else "warn" if "warning" in sev else "ok"
    missing = [s["label"] for s in sources if s["status"] == "missing"]
    enrich_exc = [e for e in exc if any(k in str(e.get("rule_id", "")) for k in ("enrich", "supplier", "copivot"))]
    rule_exc = [e for e in exc if e.get("rule_id") not in ("journal_balance", "journal_posting_date",
                                                            "placeholder_accounts", "invoice_total",
                                                            "invoice_customer", "invoice_line_type",
                                                            "reconciliation", "workbook_compare")
                and e not in enrich_exc]
    out_exc = [e for e in exc if str(e.get("rule_id", "")).startswith(("journal_", "invoice_", "placeholder"))]
    rec_breaks = [r for r in v.recon if r.status == "break"]
    approved = bool(v.approval and v.approval.approved)
    stages = {
        "acquire": ("bad" if missing else "ok",
                    f"Missing: {', '.join(missing)}" if missing else
                    f"{sum(1 for s in sources if s['status'] != 'not_tracked')} source files in, "
                    f"{len(v.recon)} source totals"),
        "archive": ("ok", f"Run stored with inputs, rule version {v.journal.rules_version or 'n/a'} and outputs "
                          f"in data/out/close/{v.month:%Y-%m}"),
        "enrich": (worst(enrich_exc), f"{len(enrich_exc)} supplier/store exceptions" if enrich_exc
                   else "Store and source on every line"),
        "rules": (worst(rule_exc), f"{len(rule_exc)} rule exceptions" if rule_exc else "All rules applied"),
        "output": ("bad" if not v.journal.balanced or any(e["severity"] == "error" for e in out_exc)
                   else "warn" if out_exc else "ok",
                   f"{len(v.journal.documents)} documents, {len(v.journal.lines)} lines, "
                   + ("all balanced" if v.journal.balanced else "UNBALANCED")
                   + f"; {len(v.invoices)} AR invoices"),
        "reconcile": ("bad" if rec_breaks else "ok" if approved else "warn",
                      f"{len(rec_breaks)} reconciliation breaks" if rec_breaks else
                      ("Approved" + (" with override" if v.approval.override else "")) if approved
                      else "Reconciled; awaiting approval"),
    }
    return [{"id": sid, "label": label, "status": stages[sid][0], "detail": stages[sid][1]} for sid, label in STAGES]


def view_json(v: CloseView) -> dict:
    sources = _sources(v)
    blocking = rec_mod.blocking_exceptions(v.exceptions)
    return {
        "month": f"{v.month:%Y-%m}", "label": f"{v.month:%B %Y}", "generated_at": v.generated_at,
        "rules_version": v.journal.rules_version,
        "stages": _stages(v, sources),
        "sources": sources,
        "control_totals": [r.to_dict() for r in v.recon],
        "journal": v.journal.summary() | {"documents": [d.to_dict() for d in v.journal.documents]},
        "invoices": [i.to_dict() for i in v.invoices],
        "comparison": v.comparison,
        "exceptions": [e | {"blocking": e in blocking} for e in v.exceptions],
        "blocking_count": len(blocking),
        "approval": v.approval.to_dict() if v.approval else None,
        "can_approve": not blocking,
        "outputs": {k: p.name for k, p in v.files.items()},
    }


# --- routes -----------------------------------------------------------------------------------------------------
@router.get("/months")
def months() -> dict:
    ms = _months()
    if not ms:
        raise HTTPException(503, "No months available yet: data/harmonized.duckdb is missing or empty. "
                                 "Run .venv/bin/python -m goodwill_pulse.build.")
    return {"months": ms, "default": next((m["month"] for m in ms if m["default"]), ms[0]["month"])}


@router.get("/run")
def run(month: str | None = Query(None, description="yyyy-mm"), refresh: bool = False) -> dict:
    return view_json(get_view(_parse_month(month), refresh))


@router.post("/approve")
def approve(month: str = Query(..., description="yyyy-mm"), body: dict | None = Body(None)) -> dict:
    v = get_view(_parse_month(month))
    body = body or {}
    with _lock:
        a = rec_mod.approve(v.exceptions, override=bool(body.get("override")), note=str(body.get("note") or ""),
                            approver=str(body.get("approver") or "Close owner"), now=_now())
        if not a.approved:
            raise HTTPException(409, {"message": f"Approval blocked: {len(a.blocking)} error exception(s) are open. "
                                                 "Resolve them, or override with a note.",
                                      "blocking": a.blocking})
        v.approval = a
        _write_outputs(v)
    return {"approval": a.to_dict(), "outputs": {k: p.name for k, p in v.files.items()}}


KINDS = {"journal": ("journal", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
         "invoice": ("invoice", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
         "invoice_json": ("invoice_json", "application/json"),
         "reconciliation": ("reconciliation", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")}


@router.get("/export")
def export(month: str = Query(..., description="yyyy-mm"), kind: str = "journal") -> FileResponse:
    if kind not in KINDS:
        raise HTTPException(422, f"kind must be one of {', '.join(KINDS)}")
    v = get_view(_parse_month(month))
    if kind in ("journal", "invoice", "invoice_json") and not (v.approval and v.approval.approved):
        raise HTTPException(409, "Approve the close before exporting the journal or invoice. "
                                 "The reconciliation report can be exported any time.")
    key, media = KINDS[kind]
    path = v.files[key]
    return FileResponse(path, media_type=media, filename=path.name)
