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

API: `POST /api/upload` (any format; spreadsheet shape unchanged), `POST /api/intake/preview` (convert and check, load nothing),
`POST /api/intake` (convert and load; hold what needs a person), `GET /api/intake`, `GET /api/intake/formats`,
`GET /api/intake/outputs/{id}/file`, `GET /api/intake/outputs/{id}/preview`, `POST /api/intake/outputs/{id}/confirm`
(optional corrected file; `confirmed_by` is required to release a held file).
CLI: `python -m goodwill_pulse.ingest.convert FILE` (convert only), `python -m goodwill_pulse.ingest.intake FILE`.
Tests: `tests/test_upload_ingest.py` (round trip: finance.duckdb -> files -> ingest -> identical rows and an identical September close).

Demo files (emails, statement PDF, OFX, FedEx HTML email, Jewelry zip, one statement that does not add up): `demo_other_formats/`, regenerate with `python -m goodwill_pulse.gen.other_formats`. They live outside `demo_data/` because `gen/demo_pack.py` deletes that folder.

## Where the UI uses it

- **Upload tab (`/`, `artifact/parts/35_upload.js`)**: spreadsheets still read in the browser. Any other file goes to
  `/api/intake/preview`; the page shows what was found (email headers, how each table was read, a preview of the rows,
  the lines-vs-printed-total check). A month-end input is loaded with one click, or, if held, after the reviewer's name
  (and optionally a corrected spreadsheet). A converted sales report continues into the existing column check and preview.
  A "Converted files" list shows earlier uploads with a Review button for anything still waiting. With no server
  reachable (the claude.ai artifact), none of this shows and the tab behaves as before.
- **Close page (`/close`)**: "Add a source file" next to the Sources grid sends a file to `/api/intake`, then rebuilds the close.
- **`/pulse` page**: the file picker accepts every format and goes through `/api/upload`.
- Tests: `artifact/tests/driver_convert.js` (Upload tab with a faked server; run it with `artifact/test_page.py`, see its header),
  `tests/test_upload_ingest.py` (backend, API and preview/confirm).
