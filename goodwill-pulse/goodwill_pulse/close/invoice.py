"""AR sales invoices, one per marketplace customer (replaces workbook step 06).

Close v1.4 (docs/CONTRACT.md): revenue is booked ONLY by the AR invoice (Dr marketplace customer / Cr revenue by
store). The channel journal documents carry only fees, refunds and payout settlement against that customer.
So the invoice is built from the CloseRun's per-channel revenue table (`revenue_frame(run)`): one invoice per channel
customer, one line per (revenue account, store).

Fallback (pre-v1.4 runs with no revenue table): every journal document with a Customer debit is the receivable and its
G/L lines become the invoice lines (`build_invoices_from_journal`).

Customer numbers come from the close rules and are PLACEHOLDERS until Goodwill names the AR customers
(PLAN question 7), so every output labels them.

Outputs:
  * BC API v2.0 payloads (`POST .../companies({id})/salesInvoices` with nested `salesInvoiceLines`)
  * an Excel workbook: one 'Invoices' header sheet and one 'Invoice lines' sheet
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill
from openpyxl.utils import get_column_letter

from .bc_excel import DATE_FMT, FIXED_TS, MONEY_FMT
from .journal import Journal, JournalLine

PLACEHOLDER_NOTE = ("Placeholder customer: Goodwill has not yet confirmed who the AR invoice is issued to "
                    "or the customer number in Business Central.")
CHANNEL_NAMES = {"SGW": "ShopGoodwill", "SHOPGOODWILL": "ShopGoodwill", "EBAY": "eBay", "AMZ": "Amazon",
                 "AMAZON": "Amazon", "GWB": "Goodwillbooks", "GOODWILLBOOKS": "Goodwillbooks",
                 "GF": "GoodwillFinds", "GOODWILLFINDS": "GoodwillFinds"}


@dataclass
class InvoiceLine:
    sequence: int
    account_no: str
    description: str
    quantity: Decimal
    unit_price: Decimal
    department_code: str = ""
    store_code: str = ""
    document_no: str = ""

    @property
    def amount(self) -> Decimal:
        return self.quantity * self.unit_price


@dataclass
class SalesInvoice:
    customer_number: str
    customer_name: str
    invoice_date: object
    posting_date: object
    external_document_number: str
    placeholder: bool
    expected_total: Decimal
    lines: list[InvoiceLine] = field(default_factory=list)
    channel: str = ""

    @property
    def total(self) -> Decimal:
        return sum((l.amount for l in self.lines), Decimal("0.00"))

    @property
    def ties_out(self) -> bool:
        return self.total == self.expected_total

    def bc_payload(self) -> dict:
        """BC API v2.0 salesInvoice with deep-inserted salesInvoiceLines (field names per the v2.0 entity)."""
        return {
            "customerNumber": self.customer_number,
            "invoiceDate": self.invoice_date.isoformat(),
            "postingDate": self.posting_date.isoformat(),
            "externalDocumentNumber": self.external_document_number[:35],
            "salesInvoiceLines": [{
                "sequence": l.sequence,
                "lineType": "Account",
                "lineObjectNumber": l.account_no,
                "description": l.description[:100],
                "quantity": float(l.quantity),
                "unitPrice": float(l.unit_price),
                "dimensionSetLines": [d for d in (
                    {"code": "DEPARTMENT", "valueCode": l.department_code} if l.department_code else None,
                    {"code": "STORE", "valueCode": l.store_code} if l.store_code else None) if d],
            } for l in self.lines],
        }

    def to_dict(self) -> dict:
        return {"channel": self.channel, "customer_number": self.customer_number, "customer_name": self.customer_name,
                "placeholder": self.placeholder, "note": PLACEHOLDER_NOTE if self.placeholder else "",
                "invoice_date": self.invoice_date.isoformat(), "external_document_number":
                    self.external_document_number, "lines": len(self.lines), "total": float(self.total),
                "expected_total": float(self.expected_total), "ties_out": self.ties_out}


def _is_placeholder(line: JournalLine) -> bool:
    n = line.account_no.upper()
    return line.placeholder_account or "PLACEHOLDER" in n or "XXXX" in n


def _customer_name(line: JournalLine) -> str:
    n = line.account_no.upper()
    for key, name in CHANNEL_NAMES.items():
        if key in n.replace("-", " ").split() or n.endswith(key) or f"-{key}" in n:
            return name
    return line.description.split(" ")[0] if line.description else line.account_no


# --- v1.4: invoices from the CloseRun revenue table ------------------------------------------------------------------
REVENUE_ATTRS = ("revenue", "revenue_lines", "channel_revenue", "invoice_lines", "ar_revenue", "revenue_by_channel",
                 "ar_invoice_lines", "invoices")
COL_ALIASES = {
    "channel": ["channel"],
    "customer_no": ["customer_no", "customer_number", "customer_account", "customer", "account_customer"],
    "account_no": ["account_no", "revenue_account", "gl_account", "revenue_account_no"],
    "store_id": ["store_id", "store", "store_code"],
    "amount": ["amount", "revenue", "net_sales", "sales", "line_amount"],
    "description": ["description"],
    "placeholder_account": ["placeholder_account", "placeholder"],
    "source_ref": ["source_ref"],
    "doc_no": ["doc_no", "document_no"],
    "posting_date": ["posting_date", "invoice_date"],
    "department_code": ["department_code", "department"],
}


def revenue_frame(run):
    """The run's per-channel revenue table as a DataFrame with canonical columns, or None if the run has none."""
    import pandas as pd
    src = None
    for name in REVENUE_ATTRS:
        v = getattr(run, name, None)
        if v is not None and not callable(v):
            src = v
            break
    if src is None:
        return None
    df = src.copy() if isinstance(src, pd.DataFrame) else pd.DataFrame(list(src))
    if df.empty:
        return None                              # an empty table means "no revenue table": use the journal fallback
    out = pd.DataFrame(index=df.index)
    for canon, names in COL_ALIASES.items():
        col = next((c for c in df.columns if str(c).lower() in names), None)
        out[canon] = df[col] if col is not None else None
    if out["amount"].isna().all():
        raise ValueError(f"revenue table has no amount column (columns: {list(df.columns)})")
    out["amount"] = out["amount"].astype(float)
    if len(out) and (out["amount"] <= 0).all() and (out["amount"] < 0).any():
        out["amount"] = -out["amount"]          # credit-signed revenue -> positive invoice amounts
    return out


def _s(v) -> str:
    if v is None:
        return ""
    try:
        if v != v:
            return ""
    except TypeError:
        pass
    return str(v)


def build_invoices_from_revenue(rev, journal: Journal, channels: dict | None = None
                                ) -> tuple[list[SalesInvoice], list[dict]]:
    """One invoice per channel: lines by (revenue account, store). Customer number from the table, else the rules'
    channel config, else the Customer account on the channel's journal document."""
    channels = channels or {}
    issues: list[dict] = []
    invoices: list[SalesInvoice] = []
    posting = journal.documents[0].posting_date if journal.documents else None
    if posting is None:
        from .journal import _month_end
        posting = _month_end(journal.month)
    for ch, g in rev.groupby(rev["channel"].fillna("").astype(str), sort=True):
        cfg = channels.get(ch, {})
        cust = next((_s(v) for v in g["customer_no"] if _s(v)), "") or _s(cfg.get("customer"))
        if not cust:
            suffix = _s(cfg.get("doc_suffix")).upper()
            doc = next((d for d in journal.documents if suffix and d.document_no.upper().endswith("-" + suffix)), None)
            cust = next((l.account_no for l in (doc.lines if doc else []) if l.account_type == "Customer"), "")
        if not cust:
            issues.append({"source": "ar_invoice", "rule_id": "invoice_customer", "severity": "error",
                           "message": f"No customer number for channel '{ch}'; cannot build its AR invoice.",
                           "amount": round(float(g["amount"].sum()), 2), "owner": "Accounting"})
            continue
        label = _s(cfg.get("label")) or CHANNEL_NAMES.get(ch.upper(), ch)
        placeholder = ("PLACEHOLDER" in cust.upper() or cust.upper().startswith("CUST-")
                       or bool(g["placeholder_account"].fillna(False).astype(bool).any()))
        inv = SalesInvoice(customer_number=cust, customer_name=label, invoice_date=posting, posting_date=posting,
                           external_document_number=f"AR-{journal.month:%Y-%m}-{_s(cfg.get('doc_suffix')) or ch.upper()}",
                           placeholder=placeholder, expected_total=Decimal("0.00"), channel=ch)
        keys = ["account_no", "store_id", "department_code"]
        gg = g.assign(**{k: g[k].map(_s) for k in keys})
        seq = 0
        for (acct, store, dept), lines in gg.groupby(keys, sort=True):
            amt = sum((Decimal(str(a)).quantize(Decimal("0.01")) for a in lines["amount"]), Decimal("0.00"))
            if amt == 0:
                continue
            if not acct:
                issues.append({"source": "ar_invoice", "rule_id": "invoice_line_account", "severity": "error",
                               "message": f"{label}: {amt:,.2f} of revenue has no revenue account.",
                               "amount": float(amt), "owner": "Accounting"})
                continue
            seq += 10000
            desc = next((_s(d) for d in lines["description"] if _s(d)), "") if len(lines) == 1 else ""
            desc = desc or f"{label} sales {journal.month:%B %Y}" + (f" - {store}" if store else "")
            inv.lines.append(InvoiceLine(seq, acct, desc, Decimal("1"), amt, dept, store,
                                         _s(lines["doc_no"].iloc[0]) or inv.external_document_number))
        inv.expected_total = inv.total           # the control is the reconciliation invariant (reconcile.py)
        if not inv.lines:
            continue
        invoices.append(inv)
    return sorted(invoices, key=lambda i: i.customer_number), issues


def build_invoices(journal: Journal, run=None, channels: dict | None = None) -> tuple[list[SalesInvoice], list[dict]]:
    """v1.4: from the run's revenue table when it has one; otherwise from the journal (pre-v1.4 runs)."""
    rev = revenue_frame(run) if run is not None else None
    if rev is not None:
        return build_invoices_from_revenue(rev, journal, channels)
    return build_invoices_from_journal(journal)


def build_invoices_from_journal(journal: Journal) -> tuple[list[SalesInvoice], list[dict]]:
    """Pre-v1.4: one invoice per customer number, lines from every document carrying a Customer debit."""
    by_customer: dict[str, SalesInvoice] = {}
    issues: list[dict] = []
    seq: dict[str, int] = {}
    for doc in journal.documents:
        customers = [l for l in doc.lines if l.account_type == "Customer"]
        if not customers or sum((c.amount for c in customers), Decimal("0.00")) <= 0:
            continue                    # v1.4 channel docs only credit the customer: no revenue to invoice here
        if len({c.account_no for c in customers}) > 1:
            issues.append({"source": doc.source, "rule_id": "invoice_customer", "severity": "error",
                           "message": f"{doc.document_no} has more than one customer; cannot build one invoice.",
                           "amount": None, "owner": "Accounting"})
            continue
        cust = customers[0]
        inv = by_customer.get(cust.account_no)
        if inv is None:
            inv = by_customer[cust.account_no] = SalesInvoice(
                customer_number=cust.account_no, customer_name=_customer_name(cust),
                invoice_date=doc.posting_date, posting_date=doc.posting_date,
                external_document_number=doc.document_no, placeholder=_is_placeholder(cust),
                expected_total=Decimal("0.00"))
            seq[cust.account_no] = 0
        else:
            inv.external_document_number = f"{inv.external_document_number}+"[:35]
        inv.expected_total += sum((c.amount for c in customers), Decimal("0.00"))
        for l in doc.lines:
            if l.account_type == "Customer":
                continue
            if l.account_type != "G/L Account":
                issues.append({"source": doc.source, "rule_id": "invoice_line_type", "severity": "warning",
                               "message": (f"{doc.document_no}: {l.account_type} {l.account_no} line "
                                           f"({l.amount:,.2f}) cannot be an invoice line; left on the journal."),
                               "amount": float(l.amount), "owner": "Accounting"})
                inv.expected_total += l.amount     # keep the tie-out about G/L lines only
                continue
            seq[cust.account_no] += 10000
            inv.lines.append(InvoiceLine(seq[cust.account_no], l.account_no, l.description, Decimal("1"),
                                         -l.amount, l.department_code, l.store_code, doc.document_no))
    invoices = sorted(by_customer.values(), key=lambda i: i.customer_number)
    for inv in invoices:
        if not inv.ties_out:
            issues.append({"source": "ar_invoice", "rule_id": "invoice_total", "severity": "error",
                           "message": (f"Invoice to {inv.customer_number} totals {inv.total:,.2f} but the "
                                       f"receivable is {inv.expected_total:,.2f}."),
                           "amount": float(inv.total - inv.expected_total), "owner": "Accounting"})
    return invoices, issues


def invoices_json(journal: Journal, invoices: list[SalesInvoice]) -> dict:
    return {
        "_about": (f"Business Central API v2.0 salesInvoices for {journal.month:%Y-%m}. POST each item to "
                   "/api/v2.0/companies({companyId})/salesInvoices (lines deep-insert). Synthetic data."),
        "_placeholders": {i.customer_number: f"{i.customer_name}: {PLACEHOLDER_NOTE}"
                          for i in invoices if i.placeholder},
        "salesInvoices": [i.bc_payload() for i in invoices],
    }


def write_invoice_json(journal: Journal, invoices: list[SalesInvoice], out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"BC AR Invoices {journal.month:%Y-%m}.json"
    path.write_text(json.dumps(invoices_json(journal, invoices), indent=2) + "\n")
    return path


INV_COLUMNS = ["Customer No.", "Customer", "Placeholder", "Invoice Date", "Posting Date", "External Document No.",
               "Lines", "Amount", "Receivable per journal", "Ties out", "Note"]
LINE_COLUMNS = ["Customer No.", "Line No.", "Type", "No.", "Description", "Quantity", "Unit Price", "Line Amount",
                "Department Code", "Store Code", "Journal Document No."]


def write_invoice_xlsx(journal: Journal, invoices: list[SalesInvoice], out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"BC AR Invoices {journal.month:%Y-%m}.xlsx"
    wb = Workbook()
    ws = wb.active
    ws.title = "Invoices"
    ws.append(INV_COLUMNS)
    for i in invoices:
        ws.append([i.customer_number, i.customer_name, "PLACEHOLDER" if i.placeholder else "", i.invoice_date,
                   i.posting_date, i.external_document_number, len(i.lines), float(i.total),
                   float(i.expected_total), "Yes" if i.ties_out else "NO",
                   PLACEHOLDER_NOTE if i.placeholder else ""])
    ws2 = wb.create_sheet("Invoice lines")
    ws2.append(LINE_COLUMNS)
    for i in invoices:
        for l in i.lines:
            ws2.append([i.customer_number, l.sequence, "G/L Account", l.account_no, l.description,
                        float(l.quantity), float(l.unit_price), float(l.amount), l.department_code, l.store_code,
                        l.document_no])
    for sheet, cols, money_cols, date_cols in ((ws, INV_COLUMNS, (8, 9), (4, 5)), (ws2, LINE_COLUMNS, (7, 8), ())):
        for c in sheet[1]:
            c.font = Font(bold=True)
            c.fill = PatternFill("solid", fgColor="DCEFEB")
        sheet.freeze_panes = "A2"
        for idx, name in enumerate(cols, 1):
            sheet.column_dimensions[get_column_letter(idx)].width = 44 if name in ("Description", "Note") else 16
        for r in range(2, sheet.max_row + 1):
            for col in money_cols:
                sheet.cell(r, col).number_format = MONEY_FMT
            for col in date_cols:
                sheet.cell(r, col).number_format = DATE_FMT
    wb.properties.created = wb.properties.modified = FIXED_TS
    tmp = path.with_suffix(".tmp.xlsx")
    wb.save(tmp)
    tmp.replace(path)
    return path
