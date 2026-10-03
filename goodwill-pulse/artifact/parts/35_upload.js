/* ---------------- Upload tab: add an Excel / CSV order report to the database and use it for the daily numbers ----------------
   Contract 3 (AGENTS.md): collection `uploads`
     uploads/index  = {items:[{id,name,uploaded_at,rows,from,to,channels,status,hash,...}]}
     uploads/<id>   = {meta:{...,parts}, rows:[[date,channel,order_id,item_sales,shipping,fees,refund]...]}, more rows in <id>_2, <id>_3
   A row value of null means "the file has no such column"; the warehouse value is kept for that measure. */

const UPL_SHEETJS = "https://cdnjs.cloudflare.com/ajax/libs/xlsx/0.18.5/xlsx.full.min.js";
const UPL_MAXDOC = 150000;                 // characters of row JSON per data doc (the db limit is 200 KB)
const UPL_CHANNELS = ["amazon", "ebay", "goodwillbooks", "goodwillfinds", "shopgoodwill"];
const UPL_FIELDS = [
  {id: "order_id", label: "Order id", hint: "Used to count distinct orders. Without it every row counts as one order."},
  {id: "paid_date", label: "Paid date", req: true, hint: "Date (and time) the order was paid."},
  {id: "channel", label: "Marketplace", hint: "Or choose one marketplace for the whole file."},
  {id: "item_sales", label: "Item sales (subtotal)", req: true, hint: "Item subtotal. Not the order total, which includes shipping and tax."},
  {id: "shipping", label: "Shipping charged", hint: "What the buyer paid for shipping. Not your shipping cost."},
  {id: "shipping_2", label: "Plus handling (optional)", hint: "Added to shipping charged."},
  {id: "fees", label: "Marketplace fees", hint: "Final value fee or market fees."},
  {id: "fees_2", label: "Plus payment fee (optional)", hint: "Added to fees."},
  {id: "refund", label: "Refunds", hint: "Leave empty if the file has no refunds. The warehouse refunds are then kept."},
];
const UPL_ALIAS = {
  order_id: ["uprightorderid", "orderid", "channelorderid", "ordernumber", "orderno", "ordernum", "order"],
  paid_date: ["paidat", "paiddate", "datepaid", "orderdate", "dateordered", "saledate", "solddate", "orderdatetime", "transactiondate", "createdat", "date"],
  channel: ["channel", "marketplace", "saleschannel", "platform", "site"],
  item_sales: ["subtotal", "itemprice", "itemsubtotal", "itemsales", "itemtotal", "productsales", "merchandisetotal", "sales", "price"],
  shipping: ["shippingtotal", "shippingcredit", "shippingcharged", "shippingrevenue", "shippingpaid", "shipping"],
  shipping_2: ["handling", "handlingfee", "handlingtotal"],
  fees: ["finalvaluefee", "marketfees", "marketplacefees", "marketplacefee", "sellingfees", "fees", "fee", "commission"],
  fees_2: ["paymentfee", "processingfee", "paymentprocessingfee"],
  refund: ["refund", "refunds", "refundamount", "refunded", "refundtotal", "returns"],
};
const UPL_TZ = {"America/New_York": "Eastern (already business dates)", "America/Los_Angeles": "Los Angeles (Upright, ShopGoodwill)", "UTC": "UTC (Cash Monkey)"};
const UPL_SETASIDE_WHY = "eBay in the warehouse combines Upright and Cash Monkey orders. One file covers only part of it, so it is set aside by default. Tick it to add it anyway.";

const UPL = {root: null, f: null, memo: new Map(), notice: null, sampleOK: null, sjs: null, claudeMsg: ""};

const uplNorm = s => String(s ?? "").toLowerCase().replace(/[^a-z0-9]/g, "");
const uplR2 = n => Math.round((n + Number.EPSILON) * 100) / 100;
const uplCell = v => v == null ? "" : v instanceof Date ? v.toISOString() : String(v);

/* ---- file reading ---- */
function uplLoadSheetJS() {
  if (window.XLSX) return Promise.resolve(window.XLSX);
  if (UPL.sjs) return UPL.sjs;
  UPL.sjs = new Promise((ok, no) => {
    const s = document.createElement("script"); s.src = UPL_SHEETJS; s.async = true;
    s.onload = () => window.XLSX ? ok(window.XLSX) : no(new Error("The Excel reader loaded but did not start."));
    s.onerror = () => { UPL.sjs = null; no(new Error("Could not load the Excel reader from cdnjs.cloudflare.com. Check the connection, or save the file as CSV and add it again.")); };
    document.head.appendChild(s);
  });
  return UPL.sjs;
}
function uplParseCSV(text) {
  text = text.replace(/^﻿/, "");
  const first = text.split(/\r?\n/, 1)[0] || "";
  const delim = [",", ";", "\t"].map(d => [d, first.split(d).length]).sort((a, b) => b[1] - a[1])[0][0];
  const rows = []; let row = [], cur = "", q = false;
  for (let i = 0; i < text.length; i++) {
    const c = text[i];
    if (q) { if (c === '"') { if (text[i + 1] === '"') { cur += '"'; i++; } else q = false; } else cur += c; }
    else if (c === '"') q = true;
    else if (c === delim) { row.push(cur); cur = ""; }
    else if (c === "\n" || c === "\r") { if (c === "\r" && text[i + 1] === "\n") i++; row.push(cur); rows.push(row); row = []; cur = ""; }
    else cur += c;
  }
  if (cur !== "" || row.length) { row.push(cur); rows.push(row); }
  return rows.map(r => r.map(v => v === "" ? null : v));
}
async function uplHash(buf) {
  try {
    if (crypto?.subtle) { const h = await crypto.subtle.digest("SHA-256", buf); return [...new Uint8Array(h)].map(b => b.toString(16).padStart(2, "0")).join(""); }
  } catch (e) { /* fall through */ }
  let h1 = 0xdeadbeef, h2 = 0x41c6ce57; const u = new Uint8Array(buf);
  for (let i = 0; i < u.length; i++) { h1 = Math.imul(h1 ^ u[i], 2654435761); h2 = Math.imul(h2 ^ u[i], 1597334677); }
  h1 = Math.imul(h1 ^ (h1 >>> 16), 2246822507) ^ Math.imul(h2 ^ (h2 >>> 13), 3266489909);
  h2 = Math.imul(h2 ^ (h2 >>> 16), 2246822507) ^ Math.imul(h1 ^ (h1 >>> 13), 3266489909);
  const x = n => (n >>> 0).toString(16).padStart(8, "0");
  return (x(h2) + x(h1)).repeat(4);
}
async function uplSheetMatrix(f, name) {
  if (f.isCsv) return f.csv;
  const X = window.XLSX, ws = f.wb.Sheets[name];
  return X.utils.sheet_to_json(ws, {header: 1, raw: true, defval: null, blankrows: true});
}

/* ---- header matching ---- */
function uplDetectHeader(m) {
  const all = new Set(Object.values(UPL_ALIAS).flat());
  let best = 0, bestScore = -1;
  for (let i = 0; i < Math.min(12, m.length); i++) {
    const cells = (m[i] || []).filter(v => v != null && v !== "");
    if (cells.length < 2) continue;
    const score = cells.filter(v => all.has(uplNorm(v))).length;
    if (score > bestScore) { bestScore = score; best = i; }
  }
  return best;
}
function uplAutoMap(headers) {
  const map = {}, used = new Set(), hn = headers.map(uplNorm);
  for (const f of UPL_FIELDS) {
    map[f.id] = -1;
    for (const a of UPL_ALIAS[f.id]) { const i = hn.findIndex((h, k) => h === a && !used.has(k)); if (i >= 0) { map[f.id] = i; used.add(i); break; } }
  }
  return map;
}
function uplKind(headers) {
  const h = new Set(headers.map(uplNorm));
  if (h.has("uprightorderid") || h.has("channelbuyer")) return "upright";
  if (h.has("itemprice") && (h.has("shippingcredit") || h.has("marketfees"))) return "cashmonkey";
  return "generic";
}
const UPL_KIND_LABEL = {upright: "Upright paid orders", cashmonkey: "Cash Monkey orders", generic: "Other order report"};

/* ---- value parsing ---- */
const uplFmtCache = {};
function uplParts(t, tz) {
  const f = uplFmtCache[tz] || (uplFmtCache[tz] = new Intl.DateTimeFormat("en-US", {timeZone: tz, hourCycle: "h23", year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit"}));
  const o = {}; for (const p of f.formatToParts(new Date(t))) o[p.type] = +p.value;
  return o;
}
function uplZoneOffset(t, tz) { const p = uplParts(t, tz); return Date.UTC(p.year, p.month - 1, p.day, p.hour, p.minute, p.second) - Math.floor(t / 1000) * 1000; }
function uplToBusinessDate(c, tz) {
  const pad = n => String(n).padStart(2, "0");
  if (!c.hasTime || tz === "America/New_York") return `${c.y}-${pad(c.m)}-${pad(c.d)}`;
  const guess = Date.UTC(c.y, c.m - 1, c.d, c.h, c.mi, c.s);
  let inst = guess - uplZoneOffset(guess, tz); inst = guess - uplZoneOffset(inst, tz);
  const p = uplParts(inst, "America/New_York");
  return `${p.year}-${pad(p.month)}-${pad(p.day)}`;
}
function uplDateParts(v, dmy) {
  const ok = c => c && c.y >= 2000 && c.y <= 2100 && c.m >= 1 && c.m <= 12 && c.d >= 1 && c.d <= 31 && c.h < 24 && c.mi < 60 && c.s < 60 &&
    new Date(Date.UTC(c.y, c.m - 1, c.d)).getUTCDate() === c.d ? c : null;
  if (v instanceof Date) return ok({y: v.getFullYear(), m: v.getMonth() + 1, d: v.getDate(), h: v.getHours(), mi: v.getMinutes(), s: v.getSeconds(), hasTime: true});
  let n = typeof v === "number" ? v : (/^\d{5}(\.\d+)?$/.test(String(v).trim()) ? +v : null);
  if (n != null) {
    if (n < 20000 || n > 80000) return null;
    const dt = new Date(Math.round((n - 25569) * 864e5 / 1000) * 1000);
    return ok({y: dt.getUTCFullYear(), m: dt.getUTCMonth() + 1, d: dt.getUTCDate(), h: dt.getUTCHours(), mi: dt.getUTCMinutes(), s: dt.getUTCSeconds(), hasTime: Math.abs(n - Math.floor(n)) > 1e-6});
  }
  const s = String(v ?? "").trim(); let m;
  if ((m = s.match(/^(\d{4})-(\d{1,2})-(\d{1,2})(?:[T ](\d{1,2}):(\d{2})(?::(\d{2}))?)?/))) return ok({y: +m[1], m: +m[2], d: +m[3], h: +(m[4] || 0), mi: +(m[5] || 0), s: +(m[6] || 0), hasTime: m[4] != null});
  if ((m = s.match(/^(\d{1,2})[\/.-](\d{1,2})[\/.-](\d{2,4})(?:[ T,]+(\d{1,2}):(\d{2})(?::(\d{2}))?\s*([AaPp][Mm])?)?$/))) {
    let a = +m[1], b = +m[2], y = +m[3], h = +(m[4] || 0);
    if (y < 100) y += 2000;
    if (m[7]) { const pm = /p/i.test(m[7]); if (pm && h < 12) h += 12; if (!pm && h === 12) h = 0; }
    return ok({y, m: dmy ? b : a, d: dmy ? a : b, h, mi: +(m[5] || 0), s: +(m[6] || 0), hasTime: m[4] != null});
  }
  return null;
}
function uplDetectDmy(m, col, from) {
  if (col < 0) return false;
  for (let i = from; i < m.length; i++) { const v = m[i]?.[col]; if (typeof v !== "string") continue; const x = v.trim().match(/^(\d{1,2})[\/.-](\d{1,2})[\/.-]\d{2,4}/); if (x && +x[1] > 12) return true; }
  return false;
}
function uplMoney(v) {
  if (v == null) return {blank: true};
  if (typeof v === "number") return isFinite(v) ? {val: v} : {bad: true};
  let s = String(v).trim();
  if (s === "" || s === "-") return {blank: true};
  let neg = false;
  if (/^\(.*\)$/.test(s)) { neg = true; s = s.slice(1, -1); }
  s = s.replace(/[$,\s]/g, "");
  if (/-$/.test(s)) { neg = !neg; s = s.slice(0, -1); }
  if (!/^-?\d*\.?\d+$/.test(s)) return {bad: true};
  const n = Number(s); return isFinite(n) ? {val: neg ? -n : n} : {bad: true};
}
function uplChannel(v) {
  const s = uplNorm(v);
  if (!s) return null;
  const direct = {shopgoodwill: "shopgoodwill", sgw: "shopgoodwill", ebay: "ebay", goodwillfinds: "goodwillfinds", finds: "goodwillfinds", amazon: "amazon", amazonmf: "amazon", amazonfba: "amazon", amazoncom: "amazon", goodwillbooks: "goodwillbooks", books: "goodwillbooks"};
  if (direct[s]) return direct[s];
  for (const [k, c] of [["shopgoodwill", "shopgoodwill"], ["goodwillfinds", "goodwillfinds"], ["goodwillbooks", "goodwillbooks"], ["ebay", "ebay"], ["amazon", "amazon"]]) if (s.includes(k)) return c;
  return null;
}
const uplId = v => v == null ? "" : typeof v === "number" ? (Number.isInteger(v) ? String(v) : String(v)) : String(v).trim();

/* ---- build the validated row set from the staged sheet ---- */
function uplBuild(U) {
  const m = U.matrix, map = U.map, h0 = U.hdr, out = {kept: [], excluded: [], problems: [], read: 0, supplies: {}};
  const has = id => map[id] >= 0;
  out.supplies = {item_sales: has("item_sales"), shipping: has("shipping") || has("shipping_2"), fees: has("fees") || has("fees_2"), refunds: has("refund")};
  const buyerCol = U.headers.findIndex(h => uplNorm(h).includes("buyer"));
  const dmy = uplDetectDmy(m, map.paid_date, h0 + 1);
  out.dmy = dmy;
  const seen = new Map(), ids = new Map();
  const excl = (line, reason, kind) => out.excluded.push({line, reason, kind});
  const prob = (line, text) => out.problems.push({line, text});
  for (let i = h0 + 1; i < m.length; i++) {
    const r = m[i] || [], line = i + 1;
    if (!r.some(v => v != null && String(v).trim() !== "")) { out.blank = (out.blank || 0) + 1; excl(line, "Blank row", "blank"); continue; }
    out.read++;
    const get = id => has(id) ? r[map[id]] : null;
    const isTot = r.some(v => typeof v === "string" && /^\s*(grand\s+)?(totals?|sum|subtotals?|grand total)\s*:?\s*$/i.test(v)) ||
      ((has("order_id") || has("paid_date")) && (!has("order_id") || uplId(get("order_id")) === "") && (!has("paid_date") || get("paid_date") == null || String(get("paid_date")).trim() === "") &&
        (!has("channel") || get("channel") == null) && r.some(v => typeof v === "number" || (typeof v === "string" && uplMoney(v).val != null)));
    if (isTot) { excl(line, "Totals row (not an order)", "totals"); continue; }
    const oid = uplId(get("order_id")), buyer = buyerCol >= 0 ? String(r[buyerCol] ?? "").trim() : "";
    if (/^test$/i.test(buyer) || /^test@/i.test(buyer) || /\btest\b/i.test(oid)) { excl(line, "Test order (buyer TEST), not revenue", "test"); continue; }
    const dv = get("paid_date");
    if (dv == null || String(dv).trim() === "") { prob(line, "Blank paid date. Row excluded."); excl(line, "Blank paid date", "date"); continue; }
    const dp = uplDateParts(dv, dmy);
    if (!dp) { prob(line, `Date not understood: "${uplCell(dv).slice(0, 30)}". Row excluded.`); excl(line, `Invalid date "${uplCell(dv).slice(0, 30)}"`, "date"); continue; }
    const date = uplToBusinessDate(dp, U.tz);
    let ch;
    if (has("channel")) { ch = uplChannel(get("channel")); if (!ch) { prob(line, `Marketplace not recognised: "${uplCell(get("channel")).slice(0, 30)}". Row excluded.`); excl(line, `Unrecognised marketplace "${uplCell(get("channel")).slice(0, 30)}"`, "channel"); continue; } }
    else ch = U.fixed;
    if (!ch) { excl(line, "No marketplace chosen", "channel"); continue; }
    if (U.aside.has(ch)) { excl(line, `Set aside by choice: ${CHN[ch]} not added`, "aside"); continue; }
    const amt = {}; let bad = null;
    for (const id of ["item_sales", "shipping", "shipping_2", "fees", "fees_2", "refund"]) {
      if (!has(id)) continue;
      const a = uplMoney(r[map[id]]);
      if (a.bad) { bad = `${U.headers[map[id]] || id}: "${uplCell(r[map[id]]).slice(0, 24)}"`; break; }
      amt[id] = a.blank ? 0 : a.val;
      if (a.blank && id === "item_sales") prob(line, "Blank item sales, counted as 0.");
    }
    if (bad) { prob(line, `Amount is not a number (${bad}). Row excluded.`); excl(line, `Non-numeric amount (${bad})`, "amount"); continue; }
    if (!has("order_id")) out.noIds = true;
    if (has("order_id") && oid === "") prob(line, "Blank order id. Counted as its own order.");
    const key = r.map(uplCell).join("␟");
    if (!U.unit) {
      if (seen.has(key)) { excl(line, `Exact duplicate of line ${seen.get(key)}`, "dup"); continue; }
      seen.set(key, line);
      if (oid && ids.has(oid)) prob(line, `Order id ${oid} also appears on line ${ids.get(oid)} with different values. Both kept; check which is right.`);
      if (oid && !ids.has(oid)) ids.set(oid, line);
    }
    const sum2 = (a, b) => (has(a) || has(b)) ? uplR2((amt[a] || 0) + (amt[b] || 0)) : null;
    out.kept.push([date, ch, has("order_id") ? oid : "r" + line, has("item_sales") ? uplR2(amt.item_sales || 0) : null, sum2("shipping", "shipping_2"), sum2("fees", "fees_2"), has("refund") ? uplR2(amt.refund || 0) : null]);
  }
  if (out.noIds) prob(0, "No order id column: every row counts as one order.");
  return out;
}

/* ---- aggregation shared by preview, overlay and reconcile ---- */
function uplAggregate(rows, sup) {
  const cells = new Map();
  for (const r of rows) {
    const k = r[0] + "|" + r[1];
    let c = cells.get(k); if (!c) cells.set(k, c = {date: r[0], channel: r[1], ids: new Set(), blanks: 0, item_sales: 0, shipping: 0, fees: 0, refunds: 0});
    if (r[2]) c.ids.add(r[2]); else c.blanks++;
    c.item_sales += r[3] || 0; c.shipping += r[4] || 0; c.fees += r[5] || 0; c.refunds += r[6] || 0;
  }
  const out = new Map();
  for (const [k, c] of cells) out.set(k, {date: c.date, channel: c.channel, orders: c.ids.size + c.blanks,
    item_sales: sup.item_sales === false ? null : uplR2(c.item_sales), shipping: sup.shipping === false ? null : uplR2(c.shipping),
    fees: sup.fees === false ? null : uplR2(c.fees), refunds: sup.refunds === false ? null : uplR2(c.refunds)});
  return out;
}

/* ---- database access ---- */
async function uplIndex() { try { const x = await read("uploads/index"); return x && Array.isArray(x.items) ? x.items : []; } catch (e) { return []; } }
async function uplLoad(item) {
  const d = await read("uploads/" + item.id), parts = (d.meta && d.meta.parts) || 1; let rows = d.rows || [];
  for (let p = 2; p <= parts; p++) rows = rows.concat((await read(`uploads/${item.id}_${p}`)).rows || []);
  return {meta: d.meta || {}, rows};
}
async function uplEffective() {   // Map "date|channel" -> {item, agg} using the latest upload that covers each cell
  const items = (await uplIndex()).filter(i => i.status !== "removed").sort((a, b) => String(b.uploaded_at).localeCompare(String(a.uploaded_at)) || String(b.id).localeCompare(String(a.id)));
  const sig = items.map(i => i.id + i.uploaded_at + i.rows).join(","), hit = UPL.memo.get("eff");
  if (hit && hit.sig === sig) return hit.map;
  const map = new Map();
  for (const it of items) {
    let aggs = UPL.memo.get("agg:" + it.id + it.uploaded_at);
    if (!aggs) {
      try { const u = await uplLoad(it); aggs = uplAggregate(u.rows, u.meta.supplies || {}); UPL.memo.set("agg:" + it.id + it.uploaded_at, aggs); }
      catch (e) { console.warn("upload " + it.id + " could not be read", e); continue; }
    }
    for (const [k, agg] of aggs) if (!map.has(k)) map.set(k, {item: it, agg});
  }
  UPL.memo.set("eff", {sig, map});
  return map;
}
addRowsOverlay(async rows => {
  const eff = await uplEffective();
  if (!eff.size) return rows;
  const byKey = new Map(rows.map(r => [r.date + "|" + r.channel, r])), out = rows.slice();
  for (const [k, {item, agg}] of eff) {
    let r = byKey.get(k);
    if (!r) { r = {date: agg.date, channel: agg.channel, orders: 0, item_sales: 0, shipping: 0, fees: 0, refunds: 0, src: "warehouse"}; byKey.set(k, r); out.push(r); }
    const idx = out.indexOf(r), n = {...r, orders: agg.orders, src: "upload:" + item.id};
    for (const m of ["item_sales", "shipping", "fees", "refunds"]) if (agg[m] != null) n[m] = agg[m];
    out[idx] = n; byKey.set(k, n);
  }
  return out.sort((a, b) => a.date.localeCompare(b.date) || CHORDER.indexOf(a.channel) - CHORDER.indexOf(b.channel));
});
function uplDiff(w, u) {
  const diff = {}; let same = true;
  for (const m of MEAS) {
    if (u[m] == null) { diff[m] = null; continue; }
    diff[m] = uplR2(u[m] - w[m]);
    if (m === "orders" ? diff[m] !== 0 : Math.abs(diff[m]) > 0.0101) same = false;
  }
  return {diff, same};
}
async function uploadProvenance() {
  const eff = await uplEffective(); if (!eff.size) return [];
  const base = await dailyRowsBase(), wk = new Map(base.map(r => [r.date + "|" + r.channel, r])), out = [];
  for (const [k, {item, agg}] of eff) {
    const w0 = wk.get(k), warehouse = w0 ? {orders: w0.orders, item_sales: w0.item_sales, shipping: w0.shipping, fees: w0.fees, refunds: w0.refunds} : {orders: 0, item_sales: 0, shipping: 0, fees: 0, refunds: 0};
    const upload = {orders: agg.orders, item_sales: agg.item_sales, shipping: agg.shipping, fees: agg.fees, refunds: agg.refunds}, d = uplDiff(warehouse, upload);
    out.push({date: agg.date, channel: agg.channel, upload_id: item.id, name: item.name, warehouse, upload, diff: d.diff, status: d.same ? "match" : "diff", in_warehouse: !!w0});
  }
  return out.sort((a, b) => a.date.localeCompare(b.date) || CHORDER.indexOf(a.channel) - CHORDER.indexOf(b.channel));
}

/* ---- staging a file ---- */
async function uplHandleFile(file) {
  const U = UPL;
  U.notice = null; U.claudeMsg = "";
  if (!file) return;
  if (!/\.(xlsx|xls|csv)$/i.test(file.name)) { U.f = null; U.notice = {cls: "bad", text: `"${file.name}" is not an .xlsx, .xls or .csv file.`}; return uplPaint(); }
  U.f = {name: file.name, size: file.size, loading: true}; uplPaint();
  try {
    const buf = await file.arrayBuffer(), f = {name: file.name, size: file.size, hash: await uplHash(buf), isCsv: /\.csv$/i.test(file.name)};
    if (f.isCsv) { f.csv = uplParseCSV(new TextDecoder("utf-8").decode(buf)); f.sheets = ["CSV"]; }
    else { const X = await uplLoadSheetJS(); f.wb = X.read(new Uint8Array(buf), {type: "array", cellDates: false}); f.sheets = f.wb.SheetNames.slice(); }
    if (!f.sheets.length) throw new Error("The file has no sheets.");
    U.f = f; await uplSetSheet(f.sheets[0]);
  } catch (e) { U.f = null; U.notice = {cls: "bad", text: `Could not read "${file.name}": ${e.message || e}`}; }
  uplPaint();
}
async function uplSetSheet(name) {
  const f = UPL.f; f.sheet = name; f.matrix = await uplSheetMatrix(f, name);
  f.hdr = uplDetectHeader(f.matrix); uplRemap(true);
}
function uplRemap(reset) {
  const f = UPL.f, hrow = f.matrix[f.hdr] || [];
  f.headers = hrow.map(v => v == null ? "" : String(v).trim());
  f.map = uplAutoMap(f.headers); f.kind = uplKind(f.headers);
  if (reset) {
    f.tz = f.kind === "upright" ? "America/Los_Angeles" : f.kind === "cashmonkey" ? "UTC" : "America/New_York";
    f.unit = f.kind === "cashmonkey"; f.aside = new Set(f.kind === "generic" ? [] : ["ebay"]); f.fixed = "";
  }
  f.claudeMsg = "";
}
function uplRecognised(f) { return f.map.paid_date >= 0 && f.map.item_sales >= 0 && (f.map.channel >= 0 || f.fixed); }

async function uplAskClaude() {
  const U = UPL, f = U.f; if (!f) return;
  U.claudeMsg = "Asking Claude...";  uplPaint();
  try {
    const sample = window.claude?.use ? await window.claude.use("sample") : null;
    if (!sample) { U.claudeMsg = "Claude is not available here, so map the columns by hand."; return uplPaint(); }
    const rows = f.matrix.slice(f.hdr + 1).filter(r => r && r.some(v => v != null) && !r.some(v => typeof v === "string" && /^\s*(grand\s+)?(totals?|sum)\s*:?\s*$/i.test(v))).slice(0, 3);
    const fields = UPL_FIELDS.map(x => x.id + ": " + x.label + (x.hint ? " (" + x.hint + ")" : "")).join("\n");
    const prompt = `You map the columns of an e-commerce order report to canonical fields. Reply with JSON only: {"mapping": {"<field>": "<exact header text or null>"}, "channel_fixed": "<amazon|ebay|goodwillbooks|goodwillfinds|shopgoodwill or null>"}.\nUse each header text exactly as given. Use null when no header fits. Item sales is the item subtotal, never the order total.\nFields:\n${fields}\nHeader row: ${JSON.stringify(f.headers)}\nSample rows: ${JSON.stringify(rows.map(r => r.map(v => v == null ? null : String(v).slice(0, 40))))}`;
    let raw = await sample.json(prompt, {modelTier: "quick"});
    if (raw && typeof raw.text === "string") { const t = raw.text, a = t.indexOf("{"), b = t.lastIndexOf("}"); raw = JSON.parse(t.slice(a, b + 1)); }
    const mp = raw && typeof raw.mapping === "object" && raw.mapping ? raw.mapping : null;
    if (!mp) throw new Error("no mapping in the answer");
    const allowed = new Set(UPL_FIELDS.map(x => x.id)), used = new Set(), next = {}; let n = 0;
    for (const [k, v] of Object.entries(mp)) {
      if (!allowed.has(k) || v == null) continue;
      const i = f.headers.findIndex((h, j) => h === String(v) && !used.has(j));
      if (i >= 0) { next[k] = i; used.add(i); n++; }
    }
    if (!n) throw new Error("none of the suggested headers exist in the file");
    for (const k of allowed) f.map[k] = k in next ? next[k] : -1;
    if (UPL_CHANNELS.includes(raw.channel_fixed) && f.map.channel < 0) f.fixed = raw.channel_fixed;
    U.claudeMsg = `Claude suggested ${n} column${n === 1 ? "" : "s"}. Check them below before adding. Claude only matched headers; every number is still computed from the file by this page.`;
  } catch (e) { U.claudeMsg = "Claude could not map the columns (" + String(e.message || e).slice(0, 80) + "). Map them by hand."; }
  uplPaint();
}

/* ---- writing ---- */
async function uplAdd() {
  const U = UPL, f = U.f; if (!f || f.busy) return;
  const res = uplBuild(f);
  if (!res.kept.length) { U.notice = {cls: "bad", text: "There are no valid rows to add."}; return uplPaint(); }
  f.busy = true; uplPaint();
  const id = "u" + f.hash.slice(0, 12), written = [];
  try {
    const coll = db.collection("uploads"), ixSnap = await coll.doc("index").get(), items = ixSnap.exists ? (ixSnap.data().items || []) : [];
    const dup = items.find(i => i.hash === f.hash || i.id === id);
    if (dup) { U.notice = {cls: "", text: `This exact file is already in the database as "${dup.name}". Remove that one first if you want to add it again.`}; f.busy = false; return uplPaint(); }
    const dates = res.kept.map(r => r[0]).sort(), chans = [...new Set(res.kept.map(r => r[1]))].sort();
    const meta = {id, name: f.name, uploaded_at: new Date().toISOString(), hash: f.hash, kind: f.kind, sheet: f.sheet, tz: f.tz, unit_grain: !!f.unit, supplies: res.supplies,
      rows_read: res.read, rows_kept: res.kept.length, rows_excluded: res.excluded.filter(x => x.kind !== "blank").length, from: dates[0], to: dates[dates.length - 1], channels: chans,
      mapping: Object.fromEntries(Object.entries(f.map).filter(([, i]) => i >= 0).map(([k, i]) => [k, f.headers[i]])), fixed_channel: f.fixed || null,
      excluded: res.excluded.filter(x => x.kind !== "blank").slice(0, 150).map(x => [x.line, x.reason]), set_aside: [...f.aside]};
    const chunks = []; let cur = [], len = 0, limit = Math.max(40000, UPL_MAXDOC - JSON.stringify(meta).length);
    for (const r of res.kept) { const l = JSON.stringify(r).length + 1; if (len + l > limit && cur.length) { chunks.push(cur); cur = []; len = 0; limit = UPL_MAXDOC; } cur.push(r); len += l; }
    chunks.push(cur); meta.parts = chunks.length;
    for (let p = 0; p < chunks.length; p++) { const did = p === 0 ? id : `${id}_${p + 1}`; await coll.doc(did).set(p === 0 ? {meta, rows: chunks[p]} : {rows: chunks[p]}); written.push(did); }
    const item = {id, name: f.name, uploaded_at: meta.uploaded_at, rows: res.kept.length, from: meta.from, to: meta.to, channels: chans, status: "active", hash: f.hash, parts: chunks.length, kind: f.kind};
    await coll.doc("index").set({items: [...items, item]});
    UPL.memo.clear(); invalidate("uploads");
    U.notice = {cls: "ok", text: `Added "${f.name}": ${nf(res.kept.length)} rows, ${shortDay(meta.from)} to ${shortDay(meta.to)}. Numbers built from daily rows now use it.`};
    U.f = null;
  } catch (e) {
    for (const did of written) { try { await db.collection("uploads").doc(did).delete(); } catch (x) { /* best effort */ } }
    f.busy = false;
    U.notice = {cls: "bad", text: "The file was not added. The database refused the write, which usually means you can view this page but not edit it. Ask the page owner for edit access. (" + String(e && e.message || e).slice(0, 120) + ")"};
  }
  render();
}
async function uplRemove(id) {
  try {
    const coll = db.collection("uploads"), snap = await coll.doc("index").get(), items = snap.exists ? (snap.data().items || []) : [], it = items.find(i => i.id === id);
    await coll.doc("index").set({items: items.filter(i => i.id !== id)});
    const parts = (it && it.parts) || 1; for (let p = 1; p <= parts; p++) { try { await coll.doc(p === 1 ? id : `${id}_${p}`).delete(); } catch (e) { /* index already updated */ } }
    UPL.memo.clear(); invalidate("uploads");
    UPL.notice = {cls: "ok", text: `Removed "${it ? it.name : id}". Numbers are back to the warehouse values for those days.`};
  } catch (e) { UPL.notice = {cls: "bad", text: "Could not remove it. " + String(e && e.message || e).slice(0, 140)}; }
  render();
}

/* ---- rendering ---- */
const uplOpt = (v, label, sel) => `<option value="${esc(v)}"${sel ? " selected" : ""}>${esc(label)}</option>`;
function uplMapHTML(f) {
  const hopt = id => uplOpt("-1", id === "channel" ? "One marketplace for all rows" : "(not in this file)", f.map[id] < 0) + f.headers.map((h, i) => h === "" ? "" : uplOpt(i, h, f.map[id] === i)).join("");
  const rows = UPL_FIELDS.map(x => `<label class="f upl-fld">${esc(x.label)}${x.req ? " *" : ""}<select id="upl-map-${x.id}" data-map="${x.id}">${hopt(x.id)}</select><small class="muted">${esc(x.hint)}</small></label>` +
    (x.id === "channel" && f.map.channel < 0 ? `<label class="f upl-fld">Marketplace for all rows<select id="upl-fixed">${uplOpt("", "Choose...", !f.fixed)}${UPL_CHANNELS.map(c => uplOpt(c, CHN[c], f.fixed === c)).join("")}</select></label>` : "")).join("");
  return rows;
}
function uplPreviewHTML(f) {
  const res = uplBuild(f), kept = res.kept, aggs = [...uplAggregate(kept, res.supplies).values()].sort((a, b) => a.date.localeCompare(b.date) || CHORDER.indexOf(a.channel) - CHORDER.indexOf(b.channel));
  const dates = kept.map(r => r[0]).sort(), chans = [...new Set(kept.map(r => r[1]))].sort((a, b) => CHORDER.indexOf(a) - CHORDER.indexOf(b));
  const orders = aggs.reduce((a, c) => a + c.orders, 0), tot = m => aggs.reduce((a, c) => a + (c[m] || 0), 0);
  const ex = res.excluded.filter(x => x.kind !== "blank"), blanks = res.excluded.length - ex.length;
  const reasons = {}; for (const x of ex) { const k = x.reason.replace(/line \d+/, "an earlier line").replace(/"[^"]*"/, '"..."').replace(/\(.*\)/, ""); (reasons[k] = reasons[k] || {n: 0, lines: []}).n++; if (reasons[k].lines.length < 8) reasons[k].lines.push(x.line); }
  const tile = (l, v, sub) => `<div class="card kpi"><span class="l">${esc(l)}</span><span class="v">${esc(v)}</span>${sub ? `<span class="muted">${esc(sub)}</span>` : ""}</div>`;
  const chBox = [...new Set([...chans, ...f.aside])].filter(c => UPL_CHANNELS.includes(c)).map(c => `<label class="upl-chk"><input type="checkbox" data-aside="${c}" ${f.aside.has(c) ? "" : "checked"}> ${esc(CHN[c])}</label>`).join("");
  const wide = f.map.paid_date >= 0 && f.map.item_sales >= 0;
  let html = `<div class="grid g3 upl-tiles">${tile("Rows kept", nf(kept.length), `of ${nf(res.read)} data rows read`)}${tile("Rows excluded", nf(ex.length), blanks ? `plus ${blanks} blank` : "")}${tile("Orders", nf(orders), "distinct order ids")}${tile("Business days", kept.length ? `${shortDay(dates[0])} to ${shortDay(dates[dates.length - 1])}` : "none", `Dates read as ${res.dmy ? "day/month" : "month/day"}, ${UPL_TZ[f.tz].split(" (")[0]} time converted to Eastern business dates`)}${tile("Item sales", usd(tot("item_sales"), 2), res.supplies.shipping ? `Shipping ${usd(tot("shipping"), 2)}` : "")}${tile("Marketplaces", chans.map(c => CHN[c]).join(", ") || "none")}</div>`;
  if (chBox) html += `<div class="card pad"><div class="rowlab">Marketplaces to add</div><div class="upl-chks">${chBox}</div>${f.aside.has("ebay") ? `<p class="muted upl-note">${esc(UPL_SETASIDE_WHY)}</p>` : ""}</div>`;
  html += `<div class="grid g2"><div class="card pad"><div class="rowlab">Problems found (${res.problems.length})</div>${res.problems.length ? `<ul class="upl-list">${res.problems.slice(0, 40).map(p => `<li>${p.line ? `<span class="mono">Line ${p.line}</span> ` : ""}${esc(p.text)}</li>`).join("")}${res.problems.length > 40 ? `<li class="muted">and ${res.problems.length - 40} more</li>` : ""}</ul>` : `<p class="muted">None. Every kept row has a date, a marketplace and numeric amounts.</p>`}</div>
    <div class="card pad"><div class="rowlab">Excluded rows (${ex.length + blanks}). Nothing is dropped without being listed here.</div>${ex.length + blanks ? `<table><thead><tr><th>Why</th><th class="r">Rows</th><th>Lines</th></tr></thead><tbody>${Object.entries(reasons).map(([k, v]) => `<tr><td>${esc(k)}</td><td class="r num">${v.n}</td><td class="mono">${esc(v.lines.join(", "))}${v.n > v.lines.length ? ", ..." : ""}</td></tr>`).join("")}${blanks ? `<tr><td>Blank rows</td><td class="r num">${blanks}</td><td class="mono">${esc(res.excluded.filter(x => x.kind === "blank").slice(0, 8).map(x => x.line).join(", "))}</td></tr>` : ""}</tbody></table>` : `<p class="muted">No rows were excluded.</p>`}</div></div>`;
  html += `<div class="card"><div class="rowlab" style="padding:14px 16px 0">Per-day totals as they will be stored (${aggs.length} day and marketplace cells)</div><div class="scroll upl-days"><table><thead><tr><th>Day</th><th>Marketplace</th><th class="r">Orders</th><th class="r">Item sales</th><th class="r">Shipping</th><th class="r">Fees</th><th class="r">Refunds</th></tr></thead><tbody>${aggs.slice(0, 200).map(a => `<tr><td>${esc(a.date)}</td><td>${esc(CHN[a.channel])}</td><td class="r num">${nf(a.orders)}</td><td class="r num">${a.item_sales == null ? "n/a" : usd(a.item_sales, 2)}</td><td class="r num">${a.shipping == null ? "n/a" : usd(a.shipping, 2)}</td><td class="r num">${a.fees == null ? "n/a" : usd(a.fees, 2)}</td><td class="r num">${a.refunds == null ? "n/a" : usd(a.refunds, 2)}</td></tr>`).join("")}${aggs.length > 200 ? `<tr><td colspan="7" class="muted">${aggs.length - 200} more cells are stored but not shown.</td></tr>` : ""}</tbody>${aggs.length ? `<tfoot><tr class="total"><td colspan="2">Total</td><td class="r num">${nf(orders)}</td><td class="r num">${usd(tot("item_sales"), 2)}</td><td class="r num">${res.supplies.shipping ? usd(tot("shipping"), 2) : "n/a"}</td><td class="r num">${res.supplies.fees ? usd(tot("fees"), 2) : "n/a"}</td><td class="r num">${res.supplies.refunds ? usd(tot("refunds"), 2) : "n/a"}</td></tr></tfoot>` : ""}</table></div></div>`;
  const notSupplied = ["shipping", "fees", "refunds"].filter(m => !res.supplies[m]);
  if (notSupplied.length) html += `<p class="muted upl-note">This file has no ${notSupplied.join(", ")} column, so the warehouse ${notSupplied.join(", ")} stay in place for those days.</p>`;
  html += `<div class="bar"><button class="seg-btn" id="upl-add" ${kept.length && wide && !f.busy ? "" : "disabled"}>${f.busy ? "Adding..." : "Add to database"}</button><button class="upl-ghost" id="upl-cancel">Cancel</button><span class="muted">Replaces the warehouse totals for ${aggs.length} day and marketplace cell${aggs.length === 1 ? "" : "s"}. Other days stay as they are.</span></div>`;
  return html;
}
function uplWorkHTML() {
  const f = UPL.f; if (!f) return "";
  if (f.loading) return `<div class="card pad" id="upl-loading">Reading ${esc(f.name)}...</div>`;
  const preview = f.matrix.slice(f.hdr, f.hdr + 4), ok = uplRecognised(f), canAsk = !ok && UPL.sampleOK !== false && window.claude?.use;
  const kindPill = pill(f.kind === "generic" ? "p-warn" : "p-ok", UPL_KIND_LABEL[f.kind] + (f.kind === "generic" ? ": check the mapping" : " layout recognised"));
  return `<section id="upl-step"><h2>2. Check the columns</h2>
  <div class="card pad upl-file"><div class="upl-meta"><b>${esc(f.name)}</b> <span class="muted">${nf(f.size / 1024, 1)} KB, hash ${esc(f.hash.slice(0, 10))}</span> ${kindPill}</div>
   <div class="bar upl-bar">
    ${f.sheets.length > 1 ? `<label class="f">Sheet<select id="upl-sheet">${f.sheets.map(s => uplOpt(s, s, s === f.sheet)).join("")}</select></label>` : ""}
    <label class="f">Header row<input type="number" id="upl-hdr" min="1" max="50" value="${f.hdr + 1}" class="upl-num"></label>
    <label class="f">Dates in the file are<select id="upl-tz">${Object.entries(UPL_TZ).map(([k, v]) => uplOpt(k, v, f.tz === k)).join("")}</select></label>
    <label class="upl-chk"><input type="checkbox" id="upl-unit" ${f.unit ? "checked" : ""}> One line per unit (an order can repeat)</label></div>
   <div class="scroll"><table class="upl-peek"><tbody>${preview.map((r, i) => `<tr class="${i ? "" : "total"}">${f.headers.map((_, c) => `<td class="${i ? "" : "mono"}">${esc(uplCell(r[c]).slice(0, 28))}</td>`).join("")}</tr>`).join("")}</tbody></table></div></div>
  <div class="card pad upl-map"><div class="rowlab">Which column is which</div>${ok ? "" : `<div class="banner">Some required columns were not recognised. Choose them below${canAsk ? " or ask Claude to suggest them" : ""}.</div>`}
   ${canAsk ? `<div class="bar upl-bar"><button class="seg-btn" id="upl-claude">Ask Claude to map the columns</button><span class="muted">Claude sees only the header row and 3 sample rows, never totals.</span></div>` : ""}
   ${UPL.claudeMsg ? `<p class="muted" id="upl-claudemsg">${esc(UPL.claudeMsg)}</p>` : ""}
   <div class="upl-grid">${uplMapHTML(f)}</div></div></section>
  <section id="upl-prev"><h2>3. Preview</h2>${uplPreviewHTML(f)}</section>`;
}
async function uplListHTML() {
  const items = (await uplIndex()).slice().sort((a, b) => String(b.uploaded_at).localeCompare(String(a.uploaded_at)));
  if (!items.length) return `<div class="empty">Nothing has been uploaded yet. The numbers come from the warehouse build.</div>`;
  return `<div class="card scroll"><table><thead><tr><th>File</th><th>Added</th><th class="r">Rows</th><th>Days</th><th>Marketplaces</th><th></th></tr></thead><tbody>${items.map(i => `<tr data-explain="upload:${esc(i.id)}"><td><b>${esc(i.name)}</b><br><span class="mono muted">${esc(i.id)}</span></td><td class="num">${esc(new Date(i.uploaded_at).toLocaleString("en-US", {month: "short", day: "numeric", hour: "numeric", minute: "2-digit"}))}</td><td class="r num">${nf(i.rows)}</td><td class="num">${esc(i.from)} to ${esc(i.to)}</td><td>${esc((i.channels || []).map(c => CHN[c] || c).join(", "))}</td><td><button class="upl-ghost" data-rm="${esc(i.id)}" aria-label="Remove ${esc(i.name)}">Remove</button></td></tr>`).join("")}</tbody></table></div>`;
}
const uplCmp = (w, u, money) => u == null ? `<span class="muted">not in file</span>` : `<span class="num">${money ? usd(w, 2) : nf(w)}</span> <span class="muted">vs</span> <span class="num">${money ? usd(u, 2) : nf(u)}</span>`;
async function uplReconHTML() {
  const prov = await uploadProvenance();
  if (!prov.length) return `<div class="empty">Reconcile appears here once a file is added. It compares the file with the warehouse for every day and marketplace the file covers.</div>`;
  const nOk = prov.filter(p => p.status === "match").length, only = UPL.onlyDiff;
  const show = prov.filter(p => !only || p.status !== "match").slice(0, 300);
  return `<div class="bar upl-bar"><span>${pill(nOk === prov.length ? "p-ok" : "p-warn", `${nOk} of ${prov.length} cells match`)}</span><label class="upl-chk"><input type="checkbox" id="upl-onlydiff" ${only ? "checked" : ""}> Only show differences</label></div>
  <div class="card scroll"><table id="upl-recon-table"><thead><tr><th>Day</th><th>Marketplace</th><th>Warehouse vs file: orders</th><th>Item sales</th><th>Shipping</th><th>Fees</th><th>Status</th></tr></thead><tbody>${show.map(p => {
    const dd = m => p.diff[m] != null && (m === "orders" ? p.diff[m] !== 0 : Math.abs(p.diff[m]) > 0.0101) ? `<div class="down num">${p.diff[m] > 0 ? "+" : "−"}${m === "orders" ? nf(Math.abs(p.diff[m])) : usd(Math.abs(p.diff[m]), 2)}</div>` : "";
    return `<tr data-status="${p.status}"><td class="num">${esc(p.date)}</td><td>${esc(CHN[p.channel])}${p.in_warehouse ? "" : ` <span class="muted">(not in warehouse)</span>`}</td><td>${uplCmp(p.warehouse.orders, p.upload.orders)}${dd("orders")}</td><td>${uplCmp(p.warehouse.item_sales, p.upload.item_sales, 1)}${dd("item_sales")}</td><td>${uplCmp(p.warehouse.shipping, p.upload.shipping, 1)}${dd("shipping")}</td><td>${uplCmp(p.warehouse.fees, p.upload.fees, 1)}${dd("fees")}</td><td>${p.status === "match" ? pill("p-ok", "Matches within $0.01") : pill("p-warn", "Differs")}</td></tr>`;
  }).join("")}</tbody></table></div><p class="muted upl-note">A difference is not automatically an error. The file may be partial or the warehouse may hold orders the file does not. Cells in the warehouse that the file does not cover keep their warehouse values.</p>`;
}
function uplPaint() {
  const root = UPL.root; if (!root) return;
  const w = $("#upl-work", root); if (!w) return;
  const y = window.scrollY;
  w.innerHTML = (UPL.notice ? `<div class="banner ${UPL.notice.cls}" id="upl-notice">${esc(UPL.notice.text)}</div>` : "") + uplWorkHTML();
  window.scrollTo(0, y);
}

async function viewUpload(root) {
  UPL.root = root;
  root.innerHTML = `
  <section><h2>Upload an order report</h2>
   <p class="lead">Drop the Upright "Paid orders" report or the Cash Monkey orders report here instead of adding it up by hand. The page reads it, shows what it found, and adds it to the database. Sales, shipping, fees and order counts for those days are then rebuilt from your file.</p>
   <div class="grid g2"><div class="card pad upl-what"><div class="rowlab">What changes</div><ul class="upl-list"><li>The nightly report, Ask answers about sales, orders, shipping, fees and refunds, and Sales over time, for every day and marketplace the file covers.</li><li>Days and marketplaces the file does not cover keep the warehouse values.</li></ul></div>
   <div class="card pad upl-what"><div class="rowlab">What does not change</div><ul class="upl-list"><li>The KPI scorecard (Dashboard and Close tabs). Those values come from the SQL warehouse build and are not recomputed from uploads.</li><li>The original warehouse data. Removing an upload puts every number back.</li></ul></div></div></section>
  <section><h2>1. Choose a file</h2>
   <label class="card upl-drop" id="upl-drop" for="upl-file"><span><b>Drop an Excel or CSV file here</b></span><span class="muted">or click to choose. .xlsx, .xls and .csv. The file is read in your browser; only the validated rows are saved.</span>
    <input type="file" id="upl-file" accept=".xlsx,.xls,.csv" class="upl-input"></label></section>
  <div id="upl-work"></div>
  <section><h2>Uploaded files</h2><p class="lead">The latest upload that covers a day and marketplace wins. Removing a file brings the warehouse numbers back.</p><div id="upl-list">${await uplListHTML()}</div></section>
  <section><h2>Reconcile with the warehouse</h2><p class="lead">A parallel run: every day and marketplace in an uploaded file next to the same cell in the warehouse, so you can see whether the two agree.</p><div id="upl-recon">${await uplReconHTML()}</div></section>`;
  uplPaint();
  const drop = $("#upl-drop", root), inp = $("#upl-file", root);
  inp.addEventListener("change", () => { const f = inp.files && inp.files[0]; if (f) uplHandleFile(f); inp.value = ""; });
  ["dragenter", "dragover"].forEach(ev => drop.addEventListener(ev, e => { e.preventDefault(); drop.classList.add("over"); }));
  ["dragleave", "drop"].forEach(ev => drop.addEventListener(ev, e => { e.preventDefault(); drop.classList.remove("over"); }));
  drop.addEventListener("drop", e => { const f = e.dataTransfer && e.dataTransfer.files && e.dataTransfer.files[0]; if (f) uplHandleFile(f); });
  root.addEventListener("click", e => {
    const t = e.target.closest("button"); if (!t) return;
    if (t.id === "upl-add") uplAdd();
    else if (t.id === "upl-cancel") { UPL.f = null; UPL.notice = null; uplPaint(); }
    else if (t.id === "upl-claude") uplAskClaude();
    else if (t.dataset.rm) uplRemove(t.dataset.rm);
  });
  root.addEventListener("change", async e => {
    const t = e.target, f = UPL.f;
    if (t.id === "upl-onlydiff") { UPL.onlyDiff = t.checked; $("#upl-recon", root).innerHTML = await uplReconHTML(); return; }
    if (!f || f.loading) return;
    if (t.id === "upl-sheet") await uplSetSheet(t.value);
    else if (t.id === "upl-hdr") { f.hdr = Math.max(0, Math.min(f.matrix.length - 1, (+t.value || 1) - 1)); uplRemap(false); }
    else if (t.id === "upl-tz") f.tz = t.value;
    else if (t.id === "upl-unit") f.unit = t.checked;
    else if (t.id === "upl-fixed") f.fixed = t.value;
    else if (t.dataset.map) { f.map[t.dataset.map] = +t.value; if (t.dataset.map === "channel" && +t.value >= 0) f.fixed = ""; }
    else if (t.dataset.aside) { t.checked ? f.aside.delete(t.dataset.aside) : f.aside.add(t.dataset.aside); }
    else return;
    UPL.notice = null; uplPaint();
  });
  if (UPL.sampleOK === null && window.claude?.use) { UPL.sampleOK = undefined; try { UPL.sampleOK = !!(await window.claude.use("sample")); } catch (e) { UPL.sampleOK = false; } if (UPL.f) uplPaint(); }
  UPL.notice = UPL.f ? UPL.notice : null;
}
registerTab("upload", "Upload", viewUpload, 35);
