# ANSWER KEY - planted re-keying error in `E-Commerce Allocation 2026-09.xlsx`

Synthetic manual allocation workbook (what the team builds by hand today, slide 39), generated from the automated
close run (rules 2026.10.03-2+a2d0fd2d, seed 7). It matches the automated journal line for line, **except one
orange input cell where two adjacent digits were transposed** while re-keying a source report.

| | |
|---|---|
| Document | `ECOM-2026-09-FEDEX` |
| Line | FedEx invoice 7-197-15332 (09/05/2026, 232 shipments) |
| Orange input cell | `Inputs!E17` |
| Journal Entries cell | `'Journal Entries'!I19` |
| Correct amount (source report) | 2,758.47 |
| Keyed amount (workbook) | 2,785.47 |
| Difference (keyed - correct) | 27.00 |

Because the workbook's formulas derive the balancing line from the inputs, the balancing line of the same document is
also off by the same amount and the document **still balances** (which is why nobody notices):

| | |
|---|---|
| Balancing line | FedEx 2026-09: charges 10,740.43 net of BNKDEPOSIT refunds 112.61 (`'Journal Entries'!I33`) |
| Correct | -10,627.82 |
| Workbook | -10,654.82 |

Expected result of the workbook comparison: every document and line matches except these two lines in
`ECOM-2026-09-FEDEX`; the root cause is the single orange cell `Inputs!E17` (digits transposed).
