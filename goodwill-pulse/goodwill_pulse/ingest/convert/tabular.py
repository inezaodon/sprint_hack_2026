"""Text formats that already hold a table: delimited text, HTML, JSON, XML, OFX/QFX bank downloads.

Every reader returns plain tables: a header (list of str) and rows (lists of str, padded to the header width).
Values are copied as text exactly as written; nothing is parsed into numbers or dates here.
"""
from __future__ import annotations

import csv
import io
import json
import re
import xml.etree.ElementTree as ET
from collections import Counter
from html.parser import HTMLParser

Table = tuple[list[str], list[list[str]]]

DELIMITERS = (",", "\t", "|", ";")


def decode(data: bytes) -> str:
    for enc in ("utf-8-sig", "utf-16"):
        try:
            text = data.decode(enc)
            if "\x00" not in text:
                return text
        except UnicodeDecodeError:
            continue
    return data.decode("cp1252", errors="replace")


def clean_header(header: list[str]) -> list[str]:
    """Blank or repeated column names get unique names, so every column survives into the spreadsheet."""
    out, seen = [], Counter()
    for i, h in enumerate(header, 1):
        h = re.sub(r"\s+", " ", str(h or "")).strip() or f"Column {i}"
        seen[h] += 1
        out.append(h if seen[h] == 1 else f"{h} ({seen[h]})")
    return out


def _fit(row: list[str], width: int) -> list[str]:
    row = [str(c).strip() for c in row]
    return (row + [""] * width)[:width]


# ------------------------------------------------------------------------------------------------ delimited text
def read_delimited(text: str, delimiters: tuple[str, ...] = DELIMITERS, min_rows: int = 1) -> tuple[Table, list[str]] | None:
    """The delimiter that gives the most rows of one consistent width wins. Lines above the first full-width row
    (report titles, "All amounts in USD", Amazon's description block) are returned separately as preamble."""
    best = None
    for d in delimiters:
        rows = list(csv.reader(io.StringIO(text), delimiter=d))
        widths = [len(r) for r in rows if len(r) > 1 and any(c.strip() for c in r)]
        if not widths:
            continue
        width, n = Counter(widths).most_common(1)[0]
        if best is None or (n, width) > (best[2], best[1]):
            best = (rows, width, n)
    if best is None or best[2] < min_rows + 1:
        return None
    rows, width, _ = best
    start = next(i for i, r in enumerate(rows) if len(r) == width)
    preamble = [" ".join(c.strip() for c in r if c.strip()) for r in rows[:start]]
    body = [_fit(r, width) for r in rows[start + 1:] if any(c.strip() for c in r)]
    return (clean_header(rows[start]), body), [p for p in preamble if p]


# --------------------------------------------------------------------------------------------------------- HTML
class _Tables(HTMLParser):
    """Collects <table>s. Tables that contain other tables are page layout (common in emails) and are dropped."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack: list[dict] = []
        self.done: list[dict] = []

    def handle_starttag(self, tag, attrs):
        if tag == "table":
            if self.stack:
                self.stack[-1]["nested"] = True
            self.stack.append({"rows": [], "nested": False, "row": None, "cell": None, "th": []})
        elif not self.stack:
            return
        elif tag == "tr":
            self.stack[-1]["row"] = []
        elif tag in ("td", "th"):
            t = self.stack[-1]
            if t["row"] is None:
                t["row"] = []
            t["cell"] = []
            t["is_th"] = tag == "th"
        elif tag == "br" and self.stack[-1]["cell"] is not None:
            self.stack[-1]["cell"].append(" ")

    def handle_endtag(self, tag):
        if not self.stack:
            return
        t = self.stack[-1]
        if tag in ("td", "th") and t["cell"] is not None:
            t["row"].append(re.sub(r"\s+", " ", "".join(t["cell"])).strip())
            if t.get("is_th"):
                t["th"].append(len(t["rows"]))
            t["cell"] = None
        elif tag == "tr" and t["row"] is not None:
            if t["cell"] is not None:
                self.handle_endtag("td")
            t["rows"].append(t["row"])
            t["row"] = None
        elif tag == "table":
            self.done.append(self.stack.pop())

    def handle_data(self, data):
        if self.stack and self.stack[-1]["cell"] is not None:
            self.stack[-1]["cell"].append(data)


def _looks_numeric(s: str) -> bool:
    return bool(re.fullmatch(r"[-+($]*[\d,]+(\.\d+)?\)?%?", s.replace(" ", ""))) if s else False


def read_html(text: str) -> list[Table]:
    p = _Tables()
    p.feed(text)
    p.close()
    out = []
    for t in p.done:
        rows = [r for r in t["rows"] if any(c for c in r)]
        if t["nested"] or len(rows) < 2:
            continue
        width = Counter(len(r) for r in rows).most_common(1)[0][0]
        if width < 2:
            continue
        first = rows[0]
        has_header = 0 in t["th"] or not any(_looks_numeric(c) for c in first)
        header = clean_header(first if has_header else [f"Column {i}" for i in range(1, len(first) + 1)])
        width = len(header)
        out.append((header, [_fit(r, width) for r in rows[1 if has_header else 0:]]))
    return out


def html_text(text: str) -> str:
    """Visible text of an HTML body (for reading 'Label: value' lines and for Claude)."""
    text = re.sub(r"(?is)<(script|style).*?</\1>", " ", text)
    text = re.sub(r"(?i)<br\s*/?>|</(p|div|tr|li|h\d)>", "\n", text)
    text = re.sub(r"(?i)</t[dh]>", "\t", text)
    text = re.sub(r"<[^>]+>", "", text)
    from html import unescape
    return "\n".join(re.sub(r"[ \t]+", " ", unescape(line)).strip() for line in text.splitlines() if line.strip())


# --------------------------------------------------------------------------------------------------------- JSON
def _records(obj) -> list | None:
    """A list of records: the value itself, or the longest list of objects nested inside it."""
    if isinstance(obj, list) and obj and all(isinstance(x, dict) for x in obj):
        return obj
    best = None
    for v in (obj.values() if isinstance(obj, dict) else obj if isinstance(obj, list) else []):
        r = _records(v)
        if r and (best is None or len(r) > len(best)):
            best = r
    return best


def _flatten(d: dict, prefix: str = "") -> dict[str, str]:
    out = {}
    for k, v in d.items():
        key = f"{prefix}{k}"
        if isinstance(v, dict):
            out.update(_flatten(v, key + "."))
        elif isinstance(v, list):
            out[key] = json.dumps(v, ensure_ascii=False)
        else:
            out[key] = "" if v is None else str(v)
    return out


def read_json(text: str) -> Table | None:
    try:
        obj = json.loads(text)
    except json.JSONDecodeError:   # JSON Lines: one object per line
        try:
            obj = [json.loads(line) for line in text.splitlines() if line.strip()]
        except json.JSONDecodeError:
            return None
    recs = _records(obj)
    if not recs:
        return None
    flat = [_flatten(r) for r in recs]
    cols = list(dict.fromkeys(k for r in flat for k in r))
    return clean_header(cols), [[r.get(c, "") for c in cols] for r in flat]


# ---------------------------------------------------------------------------------------------------------- XML
def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def read_xml(text: str) -> Table | None:
    """The element with the most same-named children is the record list; each child is one row."""
    try:
        root = ET.fromstring(text.encode("utf-8") if isinstance(text, str) else text)
    except ET.ParseError:
        return None
    best, best_n = None, 1
    for el in root.iter():
        counts = Counter(_local(c.tag) for c in el)
        if counts:
            tag, n = counts.most_common(1)[0]
            if n > best_n:
                best, best_n = [c for c in el if _local(c.tag) == tag], n
    if not best:
        return None
    rows = []
    for rec in best:
        r = {_local(k): v for k, v in rec.attrib.items()}
        for c in rec.iter():
            if c is not rec and len(c) == 0:
                r[_local(c.tag)] = (c.text or "").strip()
        rows.append(r)
    cols = list(dict.fromkeys(k for r in rows for k in r))
    return clean_header(cols), [[r.get(c, "") for c in cols] for r in rows]


# ---------------------------------------------------------------------------------------------------------- OFX
OFX_COLUMNS = ["Post Date", "Description", "Memo", "Amount", "Type", "Reference", "Account"]


def _ofx_date(s: str) -> str:
    m = re.match(r"(\d{4})(\d{2})(\d{2})", s or "")
    return f"{m.group(1)}-{m.group(2)}-{m.group(3)}" if m else (s or "")


def read_ofx(text: str) -> Table | None:
    """OFX / QFX bank download (SGML or XML flavour): one row per <STMTTRN>, in the bank layout's column names."""
    account = (re.search(r"<ACCTID>\s*([^<\r\n]+)", text, re.I) or [None, ""])[1].strip()
    rows = []
    for block in re.findall(r"<STMTTRN>(.*?)</STMTTRN>", text, re.S | re.I):
        f = {k.upper(): v.strip() for k, v in re.findall(r"<(\w+)>\s*([^<\r\n]*)", block)}
        rows.append([_ofx_date(f.get("DTPOSTED", "")), f.get("NAME", ""), f.get("MEMO", ""), f.get("TRNAMT", ""),
                     f.get("TRNTYPE", ""), f.get("REFNUM") or f.get("CHECKNUM") or f.get("FITID", ""), account])
    return (OFX_COLUMNS, rows) if rows else None
