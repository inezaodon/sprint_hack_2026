"""AR sales invoices, one per marketplace customer, from the journal (replaces workbook step 06).

Which lines become an invoice: every journal document that has a `Customer` line is the receivable from that
marketplace. The document's G/L Account lines become the invoice lines (credit = positive unit price, so revenue is
positive and fees the marketplace withholds are negative). The invoice total must equal the customer line's debit;
otherwise the invoice is flagged.

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
        return {"customer_number": self.customer_number, "customer_name": self.customer_name,
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


def build_invoices(journal: Journal) -> tuple[list[SalesInvoice], list[dict]]:
    """Return (invoices, issues). One invoice per customer number, lines from every document carrying it."""
    by_customer: dict[str, SalesInvoice] = {}
    issues: list[dict] = []
    seq: dict[str, int] = {}
    for doc in journal.documents:
        customers = [l for l in doc.lines if l.account_type == "Customer"]
        if not customers:
            continue
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
