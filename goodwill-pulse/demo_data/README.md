# Demo data: what to drop where

Everything here is synthetic. The files copy the layouts in the photos Goodwill staff shared (Upright "Paid orders" export,
the hand-entered Daily Summary Spreadsheet, and the weekly store Daily Sales Sheet). Totals come from the same simulated world as
the warehouse, so a correct import reproduces the numbers in each folder's `expected.json`.
Regenerate with `.venv/bin/python -m goodwill_pulse.gen.demo_pack`.

| Folder | Use it to show | Where to drop it |
|---|---|---|
| `01_tonight_saturday_2026-10-03` | Tonight's two reports become the Daily Pulse | Local app: `POST /api/demo/reset`, then drop both files on the page. Published app: Upload tab |
| `02_friday_2026-10-02` | The file in the photo (`paid_orders_10-02-2026_10-02-2026`) | Upload tab: it reconciles against the warehouse for 10/2 |
| `03_monday_catchup_fri_sat_sun` | Friday, Saturday and Sunday pulled together on Monday | Three daily files, or the single range file in `one_range_file/` |
| `04_messy_reports` | Duplicates, a renamed header, a test order, bad dates, title rows, a wrong-period file, a missing Cash Monkey file | Each file lists what must be caught in `expected.json` |
| `05_store_weekly_sales_supro` | In-store Daily Sales Sheet, 24 stores, with last-year comparison | Reference input for the store side; not part of the e-commerce pulse |
| `06_daily_summary_manual` | The spreadsheet staff fill by hand, filled from the warehouse | Compare with the nightly report: same sales, orders and shipping |

## Real layout notes (from the photo)
- Upright columns: Upright Order ID, Channel, Channel Order ID, Secondary Channel, Channel Buyer ID, Order Item Count, Payment ID,
  Payment Type, Total, Subtotal, Shipping Total, Shipping Label Cost, Handling Total, Tax Total, Donation Total, Currency, Final Value,
  Payment Processing Fee. Names cut off in the photo (`Channel Bu`, `Shipping La`, `Payment P`) are our best reading.
- Total = Subtotal + Shipping Total + Handling Total + Tax Total + Donation Total. ShopGoodwill handling is $3 per item.
- Shipping City/Country/Address/State and Paid At sit past the visible columns, so they are assumed. Confirm with Goodwill.
- Cash Monkey files keep the layout from the kickoff deck (no photo of it yet).
