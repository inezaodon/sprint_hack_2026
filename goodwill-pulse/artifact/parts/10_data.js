/* ---------------- date + period helpers (ISO strings, UTC arithmetic) ---------------- */
const MS = 864e5;
const D = iso => Date.UTC(+iso.slice(0, 4), +iso.slice(5, 7) - 1, +iso.slice(8, 10));
const iso = t => new Date(t).toISOString().slice(0, 10);
const addDays = (s, n) => iso(D(s) + n * MS);
const dow = s => (new Date(D(s)).getUTCDay() + 6) % 7;            // Monday = 0
const weekStart = s => addDays(s, -dow(s));
const monthStart = s => s.slice(0, 8) + "01";
const quarterStart = s => `${s.slice(0, 4)}-${String(Math.floor((+s.slice(5, 7) - 1) / 3) * 3 + 1).padStart(2, "0")}-01`;
const shortDay = s => new Date(D(s)).toLocaleDateString("en-US", {month: "short", day: "numeric", timeZone: "UTC"});
const longDay = s => new Date(D(s)).toLocaleDateString("en-US", {weekday: "short", month: "short", day: "numeric", year: "numeric", timeZone: "UTC"});
const GRAINS = {
  day: {key: s => s, label: s => shortDay(s)},
  week: {key: weekStart, label: s => "Wk " + shortDay(s)},
  month: {key: monthStart, label: s => monthShort(s)},
  quarter: {key: quarterStart, label: s => `Q${Math.floor((+s.slice(5, 7) - 1) / 3) + 1} ${s.slice(2, 4)}`},
};
const CHN = {amazon: "Amazon", ebay: "eBay", goodwillbooks: "Goodwillbooks", goodwillfinds: "GoodwillFinds", shopgoodwill: "ShopGoodwill"};
const CHORDER = Object.keys(CHN);
const MEAS = ["orders", "item_sales", "shipping", "fees", "refunds"];

/* Overlays let other parts change the daily rows before any number is built from them (e.g. an uploaded Excel file).
   An overlay is async (rows) => rows. Rows are {date, channel, orders, item_sales, shipping, fees, refunds}; an overlay may
   add a `src` field ("warehouse" | "upload:<id>") so the explain views can say where a cell came from. */
const OVERLAYS = [];
function addRowsOverlay(fn) { OVERLAYS.push(fn); }
async function dailyRows() {
  const d = await read("daily/all"), ix = Object.fromEntries(d.cols.map((c, i) => [c, i]));
  let rows = d.rows.map(r => ({date: r[ix.date], channel: r[ix.channel], orders: r[ix.orders], item_sales: r[ix.item_sales], shipping: r[ix.shipping], fees: r[ix.fees], refunds: r[ix.refunds], src: "warehouse"}));
  for (const fn of OVERLAYS) rows = (await fn(rows)) || rows;
  return rows;
}
async function dailyRowsBase() {   // the warehouse rows only, never overlaid
  const d = await read("daily/all"), ix = Object.fromEntries(d.cols.map((c, i) => [c, i]));
  return d.rows.map(r => ({date: r[ix.date], channel: r[ix.channel], orders: r[ix.orders], item_sales: r[ix.item_sales], shipping: r[ix.shipping], fees: r[ix.fees], refunds: r[ix.refunds], src: "warehouse"}));
}
function totalsFor(rows, from, to, channel) {
  const out = Object.fromEntries(CHORDER.map(c => [c, Object.fromEntries(MEAS.map(m => [m, 0]))]));
  for (const r of rows) if (r.date >= from && r.date <= to && (!channel || r.channel === channel)) for (const m of MEAS) out[r.channel][m] += r[m];
  return out;
}
function presets(last, first) {   // report periods ending on the latest business day; "catchup" = the last Fri-Sun
  const clamp = s => first && s < first ? first : s;
  const sun = dow(last) === 6 ? last : addDays(last, -(dow(last) + 1));
  const prevMonthEnd = addDays(monthStart(last), -1);
  return {
    latest: {label: "Last night", from: last, to: last},
    last7: {label: "7 days", from: clamp(addDays(last, -6)), to: last},
    last30: {label: "30 days", from: clamp(addDays(last, -29)), to: last},
    mtd: {label: "This month", from: clamp(monthStart(last)), to: last},
    ytd: {label: "Year to date", from: clamp(last.slice(0, 4) + "-01-01"), to: last},
    catchup: {label: "Monday catch-up (Fri–Sun)", from: clamp(addDays(sun, -2)), to: sun},
    lastmonth: {label: "Last month", from: clamp(monthStart(prevMonthEnd)), to: prevMonthEnd},
  };
}
function reportText(cur, prior, from, to, trust) {
  const sum = o => Object.fromEntries(MEAS.map(m => [m, CHORDER.reduce((a, c) => a + o[c][m], 0)]));
  const t = sum(cur), p = sum(prior), pc = (a, b) => b ? `${a >= b ? "+" : "−"}${nf(Math.abs(a - b) / b * 100, 1)}%` : "n/a";
  const pad = (s, n, left) => left ? String(s).padEnd(n) : String(s).padStart(n);
  const title = from === to ? `E-commerce report for ${longDay(from)}` : `E-commerce report for ${shortDay(from)} through ${shortDay(to)}, ${to.slice(0, 4)}`;
  const lines = [title, "", `Item sales ${usd(t.item_sales, 2)} (${pc(t.item_sales, p.item_sales)} vs ${from === to ? "same weekday last week" : "the " + ((D(to) - D(from)) / MS + 1) + " days before"}), ${nf(t.orders)} orders, shipping charged ${usd(t.shipping, 2)}`, "",
    pad("Marketplace", 15, true) + pad("Orders", 8) + pad("Item sales", 14) + pad("Shipping", 12) + pad("Fees", 12) + pad("Refunds", 12)];
  for (const c of CHORDER) lines.push(pad(CHN[c], 15, true) + pad(nf(cur[c].orders), 8) + pad(usd(cur[c].item_sales, 2), 14) + pad(usd(cur[c].shipping, 2), 12) + pad(usd(cur[c].fees, 2), 12) + pad(usd(cur[c].refunds, 2), 12));
  lines.push(pad("Total", 15, true) + pad(nf(t.orders), 8) + pad(usd(t.item_sales, 2), 14) + pad(usd(t.shipping, 2), 12) + pad(usd(t.fees, 2), 12) + pad(usd(t.refunds, 2), 12), "", trust, "Business days are Eastern time. Item sales exclude shipping and tax.");
  return lines.join("\n");
}
const trustLine = q => { const e = q.checks.filter(c => c.status !== "pass" && c.severity === "error").length, w = q.checks.filter(c => c.status !== "pass" && c.severity !== "error").length;
  return e ? `${e} data check(s) failed. Do not rely on these numbers until finance reviews them.` : `All ${q.total} data checks passed their blocking rules${w ? `, ${w} warning(s) to review` : ""}.`; };
