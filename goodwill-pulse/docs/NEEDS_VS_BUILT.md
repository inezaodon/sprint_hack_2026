# What the client asked for, and what we built

Sources: the operations-manager interview, the kickoff-day team notes, the Upright pain-point interview, the
channel-scoping call, and the problem-statement call.

| Need (who said it) | Built | Where |
|---|---|---|
| Stop the daily manual Upright pull and hand-written SUMs of sales, shipping and order count | Nightly report with exactly those totals by marketplace, plus fees and refunds | `goodwill_pulse/digest.py`, the Nightly report section of the Daily Pulse tab |
| E-commerce numbers alongside the 10 PM store email | Digest as email text and HTML; `--send` over SMTP | `python -m goodwill_pulse.digest --send` |
| Monday needs Friday, Saturday and Sunday | Monday catch-up period and `--catchup` | `digest.catchup_range` |
| Day, week, month and quarter breakdowns | Period toggle on the dashboard and in Ask | Monthly Dashboard, Ask |
| Metrics for growth, profitability, productivity, inventory, engagement | 3 anchors, 15 scorecard measures, all measures by pillar | `goodwill_pulse/kpi.py`, Monthly Dashboard |
| Doubt about data reliability | 17 SQL checks, report-versus-warehouse reconciliation, trust line on every report | `sql/checks`, `quality.py`, Data Quality tab |
| Month-end close into Business Central | Rules engine, balanced journal, AR invoices, control totals | `goodwill_pulse/close`, Month-End Close tab |
| Ad hoc questions, such as the books scanning example | Ask: question to spec to chart, with the parse shown | Ask tab, `goodwill_pulse/ai/ask.py` |
| Role-based access (admins, read-only store managers) | Not built in the app. In the published artifact, sharing levels map to it: Editors administer, Viewers read | Artifact Share menu |

## Not built, and why
- Live intraday sales: Upright only delivers reports, so the nightly report is the honest cadence until an API exists.
- Replacing Supro, Thriftly or Business Central: the notes ask for consolidation, not another platform.
- A planted "stores scanning zero books" case: the synthetic world has none, so the Ask example shows the lowest
  stores, and flags any store with none when it exists.

## Known data caveat
The Daily Pulse (built from the report files) and the harmonized warehouse agree on 254 of 260 day-by-row cells.
The 6 differences are 2026-07-31 and the first day of the report history. Reload the warehouse after regenerating
samples (`POST /api/demo/reset`, then `POST /api/demo/drop/demo`), or the two will drift apart.
