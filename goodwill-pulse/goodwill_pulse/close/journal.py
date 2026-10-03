"""Build Business Central General Journal lines from a CloseRun's allocations.

Input: any object shaped like `close.rules.CloseRun` (docs/CONTRACT.md section 5): `.month`, `.rules_version`,
`.allocations` (DataFrame, one row per journal line, amount > 0 = debit, < 0 = credit), `.exceptions`, `.source_totals`.

Output: a `Journal` with one `JournalDocument` per `doc_no`. Every document must balance (sum of debits equals sum of
credits, to the cent). An unbalanced document is kept and flagged (`balanced=False`, plus an error-severity issue),
or raises `UnbalancedJournalError` when `strict=True`.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal

import pandas as pd

CENT = Decimal("0.01")

# Column headers as Business Central shows them on the General Journal page ("Edit in Excel").
# The last two columns are traceability only; BC ignores columns it does not know.
BC_COLUMNS = [
    "Journal Template Name", "Journal Batch Name", "Line No.", "Posting Date", "Document Type", "Document No.",
    "Account Type", "Account No.", "Description", "Department Code", "Store Code",
    "Debit Amount", "Credit Amount", "Bal. Account Type", "Bal. Account No.",
    "Source", "Source Ref",
]
TEMPLATE = "GENERAL"
BATCH = "ECOM"
LINE_STEP = 10000          # BC numbers journal lines 10000, 20000, ...


class UnbalancedJournalError(ValueError):
    """Raised by build_journal(strict=True) when any document's debits differ from its credits."""


def money(v) -> Decimal:
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return Decimal("0.00")
    return Decimal(str(v)).quantize(CENT, rounding=ROUND_HALF_UP)


def _as_date(v) -> date | None:
    if v is None or (not isinstance(v, (date, datetime, str)) and pd.isna(v)):
        return None
    if isinstance(v, pd.Timestamp):
        return v.date()
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    return date.fromisoformat(str(v)[:10])


def _txt(v) -> str:
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return ""
    return str(v)


@dataclass
class JournalLine:
    line_no: int
    posting_date: date | None
    document_type: str
    document_no: str
    account_type: str
    account_no: str
    description: str
    department_code: str
    store_code: str
    debit: Decimal
    credit: Decimal
    bal_account_type: str = ""
    bal_account_no: str = ""
    source: str = ""
    source_ref: str = ""
    rule_id: str = ""
    placeholder_account: bool = False

    @property
    def amount(self) -> Decimal:
        """Signed amount, BC convention: debit positive, credit negative."""
        return self.debit - self.credit

    def bc_row(self) -> list:
        return [TEMPLATE, BATCH, self.line_no, self.posting_date, self.document_type, self.document_no,
                self.account_type, self.account_no, self.description, self.department_code, self.store_code,
                float(self.debit) if self.debit else None, float(self.credit) if self.credit else None,
                self.bal_account_type, self.bal_account_no, self.source, self.source_ref]

    def to_dict(self) -> dict:
        return {"line_no": self.line_no, "posting_date": self.posting_date.isoformat() if self.posting_date else None,
                "document_type": self.document_type, "document_no": self.document_no,
                "account_type": self.account_type, "account_no": self.account_no, "description": self.description,
                "department_code": self.department_code, "store_code": self.store_code,
                "debit": float(self.debit), "credit": float(self.credit),
                "bal_account_type": self.bal_account_type, "bal_account_no": self.bal_account_no,
                "source": self.source, "source_ref": self.source_ref, "rule_id": self.rule_id,
                "placeholder_account": self.placeholder_account}


@dataclass
class JournalDocument:
    document_no: str
    source: str
    posting_date: date | None
    lines: list[JournalLine] = field(default_factory=list)

    @property
    def total_debit(self) -> Decimal:
        return sum((l.debit for l in self.lines), Decimal("0.00"))

    @property
    def total_credit(self) -> Decimal:
        return sum((l.credit for l in self.lines), Decimal("0.00"))

    @property
    def difference(self) -> Decimal:
        return self.total_debit - self.total_credit

    @property
    def balanced(self) -> bool:
        return self.difference == 0

    @property
    def has_placeholder(self) -> bool:
        return any(l.placeholder_account for l in self.lines)

    def to_dict(self, with_lines: bool = True) -> dict:
        d = {"document_no": self.document_no, "source": self.source,
             "posting_date": self.posting_date.isoformat() if self.posting_date else None,
             "line_count": len(self.lines), "total_debit": float(self.total_debit),
             "total_credit": float(self.total_credit), "difference": float(self.difference),
             "balanced": self.balanced, "has_placeholder_accounts": self.has_placeholder}
        if with_lines:
            d["lines"] = [l.to_dict() for l in self.lines]
        return d


@dataclass
class Journal:
    month: date
    rules_version: str
    documents: list[JournalDocument]
    issues: list[dict]                       # same shape as CloseRun.exceptions

    @property
    def lines(self) -> list[JournalLine]:
        return [l for d in self.documents for l in d.lines]

    @property
    def total_debit(self) -> Decimal:
        return sum((d.total_debit for d in self.documents), Decimal("0.00"))

    @property
    def total_credit(self) -> Decimal:
        return sum((d.total_credit for d in self.documents), Decimal("0.00"))

    @property
    def balanced(self) -> bool:
        return all(d.balanced for d in self.documents)

    def document(self, doc_no: str) -> JournalDocument | None:
        return next((d for d in self.documents if d.document_no == doc_no), None)

    def by_source(self) -> dict[str, dict]:
        """Per source: lines, debits, credits (used by reconciliation)."""
        out: dict[str, dict] = {}
        for l in self.lines:
            s = out.setdefault(l.source, {"lines": 0, "debit": Decimal("0.00"), "credit": Decimal("0.00")})
            s["lines"] += 1
            s["debit"] += l.debit
            s["credit"] += l.credit
        return out

    def to_frame(self) -> pd.DataFrame:
        return pd.DataFrame([l.bc_row() for l in self.lines], columns=BC_COLUMNS)

    def summary(self) -> dict:
        return {"documents": len(self.documents), "lines": len(self.lines),
                "total_debit": float(self.total_debit), "total_credit": float(self.total_credit),
                "balanced": self.balanced,
                "unbalanced_documents": [d.document_no for d in self.documents if not d.balanced]}


REQUIRED = ["doc_no", "account_type", "account_no", "amount"]


def build_journal(run, *, strict: bool = False) -> Journal:
    """Group the run's allocations into BC journal documents and check each balances."""
    alloc: pd.DataFrame = run.allocations
    missing = [c for c in REQUIRED if c not in alloc.columns]
    if missing:
        raise ValueError(f"allocations are missing columns {missing}")
    month = _as_date(run.month)
    issues: list[dict] = []
    docs: dict[str, JournalDocument] = {}
    line_no = 0
    for row in alloc.to_dict("records"):
        amt = money(row.get("amount"))
        if amt == 0:
            continue                                          # BC rejects zero lines; nothing to post
        doc_no = _txt(row["doc_no"])
        posting = _as_date(row.get("posting_date"))
        if posting is None and month is not None:
            posting = _month_end(month)
        doc = docs.get(doc_no)
        if doc is None:
            doc = docs[doc_no] = JournalDocument(doc_no, _txt(row.get("source")), posting)
            line_no = 0
        line_no += LINE_STEP
        doc.lines.append(JournalLine(
            line_no=line_no, posting_date=posting, document_type=_txt(row.get("document_type")),
            document_no=doc_no, account_type=_txt(row["account_type"]), account_no=_txt(row["account_no"]),
            description=_txt(row.get("description"))[:100], department_code=_txt(row.get("department_code")),
            store_code=_txt(row.get("store_id")), debit=amt if amt > 0 else Decimal("0.00"),
            credit=-amt if amt < 0 else Decimal("0.00"), source=_txt(row.get("source")),
            source_ref=_txt(row.get("source_ref")), rule_id=_txt(row.get("rule_id")),
            placeholder_account=bool(row.get("placeholder_account") or False)))
    documents = sorted(docs.values(), key=lambda d: d.document_no)
    for d in documents:
        if not d.balanced:
            issues.append({"source": d.source, "rule_id": "journal_balance", "severity": "error",
                           "message": (f"{d.document_no} does not balance: debits {d.total_debit:,.2f}, "
                                       f"credits {d.total_credit:,.2f}, difference {d.difference:,.2f}."),
                           "amount": float(d.difference), "owner": "Accounting"})
        dates = {l.posting_date for l in d.lines}
        if len(dates) > 1:
            issues.append({"source": d.source, "rule_id": "journal_posting_date", "severity": "warning",
                           "message": f"{d.document_no} has lines on {len(dates)} different posting dates.",
                           "amount": None, "owner": "Accounting"})
    placeholders = sorted({l.account_no for l in (l for d in documents for l in d.lines) if l.placeholder_account})
    if placeholders:
        issues.append({"source": "journal", "rule_id": "placeholder_accounts", "severity": "info",
                       "message": ("Placeholder accounts until Goodwill confirms the chart of accounts: "
                                   + ", ".join(placeholders)),
                       "amount": None, "owner": "Accounting"})
    journal = Journal(month, _txt(getattr(run, "rules_version", "")), documents, issues)
    if strict and not journal.balanced:
        raise UnbalancedJournalError("; ".join(i["message"] for i in issues if i["rule_id"] == "journal_balance"))
    return journal


def _month_end(m: date) -> date:
    nxt = date(m.year + (m.month == 12), m.month % 12 + 1, 1)
    return date.fromordinal(nxt.toordinal() - 1)
