"""DuckDB warehouse: the canonical data model every output reads from."""
from pathlib import Path

import duckdb

from .config import DB_PATH

SCHEMA = """
CREATE TABLE IF NOT EXISTS report_files (
    file_id        VARCHAR PRIMARY KEY,      -- sha256 of the raw bytes
    report_type    VARCHAR,
    original_name  VARCHAR,
    archived_path  VARCHAR,
    received_at    TIMESTAMPTZ,
    coverage_start TIMESTAMPTZ,              -- earliest moment the report covers
    coverage_end   TIMESTAMPTZ,              -- moment the report covers up to (exclusive)
    row_count      INTEGER,                  -- data rows (header and totals row excluded)
    loaded_rows    INTEGER,
    control_total  DECIMAL(14,2),            -- sum of revenue column as found in the file
    latest_order_at TIMESTAMPTZ,             -- newest order in the file ("data through")
    status         VARCHAR,                  -- loaded | partial | needs_mapping | rejected | duplicate_file
    message        VARCHAR
);

-- Column renames a person has confirmed (e.g. 'Sub Total' -> 'Subtotal'), applied on every later file.
CREATE TABLE IF NOT EXISTS column_aliases (
    report_type    VARCHAR,
    source_column  VARCHAR,
    mapped_to      VARCHAR,
    confirmed_at   TIMESTAMPTZ DEFAULT now(),
    PRIMARY KEY (report_type, source_column)
);

CREATE TABLE IF NOT EXISTS orders (
    order_key         VARCHAR PRIMARY KEY,   -- source:channel:channel_order_id
    source_system     VARCHAR,
    report_type       VARCHAR,
    channel           VARCHAR,
    line_of_business  VARCHAR,
    channel_order_id  VARCHAR,
    buyer_key         VARCHAR,
    paid_at_utc       TIMESTAMPTZ,
    business_date     DATE,
    item_count        INTEGER,
    subtotal          DECIMAL(12,2),
    shipping_charged  DECIMAL(12,2),
    handling          DECIMAL(12,2),
    tax               DECIMAL(12,2),
    marketplace_fees  DECIMAL(12,2),
    shipping_cost     DECIMAL(12,2),
    total             DECIMAL(12,2),
    file_id           VARCHAR,
    source_rows       VARCHAR                -- row numbers in the raw file, for traceability
);

CREATE TABLE IF NOT EXISTS order_lines (
    order_key   VARCHAR,
    line_no     INTEGER,
    sku         VARCHAR,
    title       VARCHAR,
    store_id    VARCHAR,
    quantity    INTEGER,
    sale_price  DECIMAL(12,2),
    fee_alloc   DECIMAL(12,2),
    file_id     VARCHAR,
    source_row  INTEGER
);

-- Uploads that needed converting first (ingest/intake.py: emails, PDFs, OFX, HTML, zips ... and month-end
-- spreadsheets). One row per original upload, one row per spreadsheet made from it.
CREATE TABLE IF NOT EXISTS intake_files (
    intake_id      VARCHAR PRIMARY KEY,      -- sha256 of the original bytes
    original_name  VARCHAR,
    format         VARCHAR,                  -- eml | pdf | ofx | html | zip | text | json | xml | png | csv | xlsx ...
    archived_path  VARCHAR,
    received_at    TIMESTAMPTZ,
    email_from     VARCHAR,
    email_subject  VARCHAR,
    email_date     VARCHAR,
    message_id     VARCHAR,                  -- the same email forwarded twice is a duplicate
    status         VARCHAR,                  -- loaded | partial | needs_review | needs_mapping | rejected
    notes          VARCHAR                   -- JSON list: parts that couldn't be read, and why
);

CREATE TABLE IF NOT EXISTS intake_outputs (
    output_id      VARCHAR PRIMARY KEY,      -- '<intake_id[:12]>-<n>'
    intake_id      VARCHAR,
    name           VARCHAR,                  -- spreadsheet file name
    path           VARCHAR,                  -- the spreadsheet on disk (download it to review)
    origin         VARCHAR,                  -- where in the upload it came from ('x.eml > attachment > s.pdf, pages 1-3')
    method         VARCHAR,                  -- passthrough | parsed | ai
    target         VARCHAR,                  -- warehouse | finance | none
    layout         VARCHAR,                  -- report_type / finance layout id
    row_count      INTEGER,
    loaded_rows    INTEGER,
    status         VARCHAR,                  -- loaded | partial | needs_review | needs_mapping | rejected | duplicate_file
    message        VARCHAR,
    file_id        VARCHAR,                  -- report_files.file_id when it went through the sales pipeline
    stated         VARCHAR,                  -- JSON: totals printed in the source, outside the table
    confirmed_by   VARCHAR,
    confirmed_at   TIMESTAMPTZ
);

CREATE SEQUENCE IF NOT EXISTS exception_seq;
CREATE TABLE IF NOT EXISTS exceptions (
    exception_id  INTEGER DEFAULT nextval('exception_seq') PRIMARY KEY,
    created_at    TIMESTAMPTZ DEFAULT now(),
    file_id       VARCHAR,
    original_name VARCHAR,
    source_row    INTEGER,
    rule          VARCHAR,
    severity      VARCHAR,                    -- error | warning | info
    message       VARCHAR,
    suggested_fix VARCHAR,
    status        VARCHAR DEFAULT 'open'
);
"""


def connect(path: Path | str = DB_PATH) -> duckdb.DuckDBPyConnection:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(path))
    con.execute(SCHEMA)
    return con


def reset(path: Path | str = DB_PATH) -> None:
    for p in [Path(path), Path(str(path) + ".wal")]:
        if p.exists():
            p.unlink()
