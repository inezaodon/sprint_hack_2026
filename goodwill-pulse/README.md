# Goodwill Michiana E-Commerce Daily Pulse (SprintHack@ND prototype)

Turns the reports Goodwill staff already download (Upright paid orders, Cash Monkey orders) into one clean
data model, and from it a nightly Daily Pulse: revenue and customers by marketplace, then enterprise totals.

**All data is synthetic.** Report layouts follow Goodwill's kickoff deck (Upright columns from the slide 26
screenshot; Cash Monkey behavior from slides 28–30). Volumes are calibrated to the one real figure shown there
(9/30/2026: 128 paid orders, $13,247 subtotal). No real Goodwill data is used.

## Run
```bash
/opt/homebrew/bin/python3.12 -m venv .venv && .venv/bin/pip install pandas duckdb fastapi "uvicorn[standard]" python-multipart openpyxl pyyaml anthropic pytest httpx pytz
.venv/bin/python -m goodwill_pulse.gen.generate          # writes data/samples/{history,demo,messy}
.venv/bin/uvicorn goodwill_pulse.api:app --port 8000      # open http://localhost:8000
curl -X POST localhost:8000/api/demo/reset                 # load history (everything before Saturday)
```
Then click **Load Saturday's reports** and **Load a messy report** on the page.

## Layout
- `config/` channels (pulse rows) and one mapping per report type; changing a channel or column is config, not code
- `goodwill_pulse/gen/` synthetic world + writers that reproduce each report's native format and quirks
- `goodwill_pulse/ingest/` recognize → archive → parse → validate → load; problems go to `exceptions`
- `goodwill_pulse/pulse.py` business-day (Eastern) rollup, freshness per source, like-for-like comparisons
- `goodwill_pulse/api.py` FastAPI + serves `web/index.html` (also publishable as a claude.ai artifact)
- `tests/` rules that keep the numbers trustworthy

## Deploy (Vercel)
Live: https://goodwill-pulse.vercel.app (FastAPI as one Python function; synthetic data only).
```bash
.venv/bin/python -m goodwill_pulse.build              # if data/harmonized.duckdb is stale
.venv/bin/python deploy/build_vercel_bundle.py        # assembles data/_tmp/vercel (code + prebuilt data, gitignored)
cd data/_tmp/vercel && npx vercel deploy --prod       # first time: npx vercel login; npx vercel link --project goodwill-pulse
```
The bundle ships prebuilt read-only data and copies it to /tmp on cold start (`GOODWILL_DATA_DIR`). Uploads, demo
resets and close approvals work but live in that instance's /tmp only. Set `ANTHROPIC_API_KEY` in the Vercel project
to switch the AI features from the deterministic fallback to Claude.
