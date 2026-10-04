# Artifact build guide (read this first)

The published page "Goodwill E-Com Pulse" is ONE html file built from parts in `goodwill-pulse/artifact/`.
Everything runs in the browser. Data comes from a small online database (the Claude artifact `db` capability);
Claude is reached through the `sample` capability. All data is synthetic.

## Layout
- `shell.html` page skeleton (header, `<nav id="tabs">`, `<main id="main">`). Do not edit unless told.
- `parts/css/NN_name.css`, `parts/NN_name.js`: concatenated in file-name order by `build.py` into one `<script>`.
  Parts are NOT modules: they share one global scope, so name your top-level functions/consts with a prefix unique to your part.
  Existing: 00_core (helpers, charts, `read(path)`, `state`, tab registry), 10_data (date helpers, `dailyRows()`, `addRowsOverlay(fn)`),
  20_view_pulse, 30_view_dashboard, 40_view_ask, 50_view_close, 60_view_quality, 90_shell (always last).
- A view is `async function viewX(root)` that sets `root.innerHTML` and wires events on `root` (use `$("#id", root)`; the root
  is detached while it renders). Register it at the bottom of its file: `registerTab("id", "Label", viewX, order)`.
  Tab order: pulse 10, dashboard 20, ask 30, (free 35-65), close 70, quality 80.
- Data: `await read("collection/doc")` returns the doc's data (cached). `invalidate(prefix)` clears the cache. `render()` re-renders the tab.
  Docs live in `artifact/data/<collection>__<doc>.json` for tests (exported by `tools/export_artifact_docs.py`): site/options,
  month/YYYY-MM (KPI cards, stores, categories), series/all, pulse/all, daily/all (day x marketplace totals), storecat/all
  (month x store x category pipeline), close/YYYY-MM, quality/latest.
- After every render the shell calls `decorateExplain(main)` if that function exists (owned by the lineage agent).
- Page-wide rule: numbers are ALWAYS computed by code from stored data. A model never produces a displayed number.

## Build and test (use your OWN dist dir; other agents build at the same time)
    cd goodwill-pulse
    .venv/bin/python artifact/test_page.py --dist /tmp/dist_YOURNAME --driver artifact/tests/YOUR_driver.js [--sample 'JSON'] [--files a.xlsx] [--shot out.png --hash tabid]
`artifact/test_page.py --help` text (top of the file) explains the driver API (`sleep, click, setVal, loadFile, done`). `tests/driver_basic.js`
is the regression driver: it must still pass (all tabs render, no page errors) when you are done. If the build fails because of someone
else's half-written file, wait 30 s and retry. Run `node --check parts/YOUR_FILE.js` after every edit.
Use the scratchpad dir for temp files, not /tmp: /private/tmp/claude-501/-Users-odon-Desktop-Hackathon/71900710-0efb-40ed-9c54-c154a0f53fe3/scratchpad

## Hard rules
- Edit only the files your task owns. Do NOT publish artifacts, do NOT run git commit/stash/reset/checkout (an autosave commits for the team).
- Design: reuse the CSS tokens in `parts/css/base.css` (`--bg --surface --ink --muted --line --accent --good --warn --bad`, `--mono`, `--display`);
  never use literal colors; works in light and dark and at 400px width (tables inside `.scroll`); no emojis; plain direct copy, no em-dash asides.
- Anything you render from data must go through `esc()`. Wrap clipboard/storage calls in try/catch. No alert/confirm/prompt.
- Allowed external scripts: https://cdnjs.cloudflare.com only (pin exact versions). No other network access from the page.
- Finish with a short report: what you built, files touched, how you tested, what does not work or you could not verify.

## Verification model (what Kepler does, and what we copy)
Kepler (kepler.ai) separates what the model does from what code does: the LLM only interprets the question; deterministic code retrieves
data and computes; a concept map ("ontology") maps synonyms (revenue, top line, total sales) to ONE canonical definition; every number
is traceable to source document, page and line item; formulas are explicit and reproducible; users pick quick-check or full audit trail.
Our equivalents: question -> spec (Claude) -> code computes from stored rows -> the number carries a "How we got this" card:
spec, formula, SQL, source tables and row counts, the order rows behind it, an independent recompute from the native source DBs,
and a match/diff status. Nothing the model says is shown as a number unless the code produced the same number.

## Contracts between parts
1. `async function explainHTML(kind, payload)` (owned by 70_explain.js) returns an HTML string for a collapsible audit card.
   kinds: "ask" {question, spec, res, engine, interpretation}; "report" {from, to}; "kpi" {id, month}; "upload" {id}.
   Callers must guard: `typeof explainHTML === "function"`.
2. Any element with `data-explain="kind:arg1:arg2"` (e.g. `kpi:net_margin:2026-09`, `report:2026-09-25:2026-09-27`) gets a small
   "How is this calculated?" button from `decorateExplain(root)`, which opens the audit card in place.
3. Uploads (35_upload.js): collection `uploads`. `uploads/index` = {items:[{id,name,uploaded_at,rows,from,to,channels,status}]};
   `uploads/<id>` = {meta:{...}, rows:[[date,channel,order_id,item_sales,shipping,fees,refund]...]} (split as `<id>_2`, `<id>_3`
   when a doc would exceed 200 KB). It registers an overlay with `addRowsOverlay`: for each (date, channel) cell present in the latest
   upload covering it, the warehouse totals are REPLACED by the upload's sums and the row gets `src: "upload:<id>"`.
   `async function uploadProvenance()` returns [{date, channel, upload_id, name, warehouse:{orders,item_sales,shipping,fees,refunds}, upload:{...}, diff:{...}}].
   Other formats: when `/api/intake/formats` answers, the Upload tab also accepts emails, PDFs, OFX, HTML, JSON, XML, zips and images,
   converts them on the server (`/api/intake/preview`), and either loads a month-end input (`/api/intake/outputs/<id>/confirm`) or hands the
   converted sales spreadsheet to the same steps as a file picked from disk. See docs/UPLOAD_INGEST.md.
4. Related dashboards (41_related.js): `relatedKpis(spec, claudeHint) -> {pillar, ids:[kpi ids], reason}`; the dashboard accepts
   `state.focus = {pillar, ids}` to open that pillar and highlight those tiles.
5. Lineage docs (written by `artifact/export_lineage.py`, one collection `lineage`, each doc < 250 KB):
   - `lineage/overview`: {generated_at, pipeline:[{id,title,detail,tables:[names]}], sources:[{db,table,rows,columns:[{name,type}],grain,description}],
     harmonized:[same shape], artifact_docs:[{path,bytes,what}], builds:{harmonize:{run_id,started_at,seconds}, quality:{run_at,total,passed}},
     exclusions:{test_orders,test_skus,canceled_orders}}
   - `lineage/metrics`: {measures:[{id,label,synonyms:[...],unit,kind:"daily"|"pipeline"|"kpi",formula_text,sql,source_tables:[...],columns_used:[...],notes}]}
     covering the daily measures (item_sales, orders, shipping, fees, refunds, avg_order), the pipeline measures (store_revenue, units_sold,
     items_identified, items_sent, items_listed) and every KPI in goodwill_pulse/kpi.py (definitions from docs/KPI_DEFINITIONS.md).
   - `lineage/ledger`: {as_of, checks:[{id,title,scope:{month?,date?,channel?},measure,paths:[{label,sql,value,rows_scanned,source_tables}],
     page_value,page_source,status:"match"|"diff",tolerance,diff}]}: each headline number recomputed independently two or more ways
     (native source DBs vs harmonized vs the value stored in the artifact docs).
   - `lineage/drill`: {"YYYY-MM-DD|channel": {total_rows, sum_item_sales, sum_shipping, sum_fees, rows:[{order_key,paid_at_et,item_sales,shipping,fees,refund,source_db,native_key}]}}
     for the last 7 business days, up to 60 rows per cell (largest first), so a total can be walked down to orders.
