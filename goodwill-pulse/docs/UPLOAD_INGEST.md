# Upload ingest (branch `upload-ingest`)

Any file in, spreadsheet out, database loaded. The existing pipeline only reads CSV/Excel; slide 38's other sources
arrive as emails, PDFs, bank downloads, portal pages and zips. `ingest/convert` changes the format only (every value
copied as text, nothing computed), then the same recognizers and checks as an uploaded spreadsheet take over.

| Input | How it is read | AI? |
|---|---|---|
| csv, xlsx, xls | unchanged | no |
| txt / tsv / psv | delimiter sniffed, title lines kept as notes | no |
| html | data tables (layout tables skipped) | no |
| json, xml | the list of records | no |
| ofx, qfx | one row per transaction, bank layout | no |
| eml | attachments + body tables; sender, date, subject, Message-ID kept | no |
| zip | every file inside | no |
| pdf | tables under known column headers (multi-page), else ruled tables; printed totals kept | only if scanned |
| png / jpg | Claude transcribes | yes, held for review |

Targets: Upright / Cash Monkey reports -> warehouse.duckdb (`pipeline.process_file`); bank 0101, FedEx, Jewelry Report,
Goodwill Books statement -> finance.duckdb (`ingest/finance.py`, layouts in `config/finance_layouts/*.yaml`), the
tables the month-end close reads. All column names for the month-end layouts are ASSUMED until Goodwill shares real files.

Controls: a statement whose lines don't add up to its printed net is held (`needs_review`), never loaded short;
anything Claude read is held until a person confirms; the same email twice is caught by Message-ID; every converted
spreadsheet records its source, SHA-256, email headers and how it was read; loading month-end inputs drops the cached close.

API: `POST /api/upload` (any format; spreadsheet shape unchanged), `POST /api/intake`, `GET /api/intake`,
`GET /api/intake/outputs/{id}/file`, `POST /api/intake/outputs/{id}/confirm` (optional corrected file).
CLI: `python -m goodwill_pulse.ingest.convert FILE` (convert only), `python -m goodwill_pulse.ingest.intake FILE`.
Tests: `tests/test_upload_ingest.py` (round trip: finance.duckdb -> files -> ingest -> identical rows and an identical September close).

Demo files (emails, statement PDF, OFX, FedEx HTML email, Jewelry zip, one statement that does not add up): `demo_other_formats/`, regenerate with `python -m goodwill_pulse.gen.other_formats`. They live outside `demo_data/` because `gen/demo_pack.py` deletes that folder.
