/* ---------------- Hub data layer: sources, channels, hub docs, Send to leadership, BC import file, raw data ----------------
   Docs (exported by tools/export_artifact_docs.py, export_hub): hub/meta, hub/days, storeday/<YYYY-MM>, supro/<YYYY-MM>, thriftly/all.
   Every number below is added up here from stored rows. Uploaded nights (35_upload.js overlay) replace warehouse rows in hubDays(). */
const HUB_SOURCES = {
  upright: {label: "Upright", what: "ShopGoodwill, eBay and GoodwillFinds paid orders", kind: "ecom", channels: ["shopgoodwill", "ebay", "goodwillfinds"],
    report: "Paid orders export", pulled: "9:00 pm"},
  cashmonkey: {label: "Cash Monkey", what: "Amazon, eBay books and Goodwillbooks orders", kind: "ecom", channels: ["amazon", "ebay", "goodwillbooks"],
    report: "Orders report", pulled: "9:00 pm"},
  supro: {label: "Supro", what: "Retail store sales and customer counts", kind: "retail", channels: ["stores"], report: "End-of-day store report", pulled: "10:00 pm"},
  thriftly: {label: "Thriftly", what: "Back-room production and sell-through", kind: "prod", channels: [], report: "Production scans", pulled: "nightly"},
};
const HUB_CHANNELS = {
  amazon: {label: "Amazon", color: "var(--c1)"}, ebay: {label: "eBay", color: "var(--c2)"}, goodwillbooks: {label: "Goodwillbooks", color: "var(--c3)"},
  goodwillfinds: {label: "GoodwillFinds", color: "var(--c4)"}, shopgoodwill: {label: "ShopGoodwill", color: "var(--c5)"}, stores: {label: "Stores", color: "var(--c3)"},
};
const HUB_ECOM = ["upright", "cashmonkey"];
function hubChLabel(src, ch) {
  if (ch === "ebay" && src === "cashmonkey") return "eBay books";
  return (HUB_CHANNELS[ch] && HUB_CHANNELS[ch].label) || CHN[ch] || String(ch || "");
}
async function hubTry(path) { try { return await read(path); } catch (e) { return null; } }
const hubObjs = d => !d || !Array.isArray(d.cols) ? [] : d.rows.map(r => { const o = {}; d.cols.forEach((c, i) => { o[c] = r[i]; }); return o; });
const hubMonths = (from, to) => { const out = []; let m = from.slice(0, 7); const end = to.slice(0, 7);
  while (m <= end) { out.push(m); const y = +m.slice(0, 4), mo = +m.slice(5, 7); m = mo === 12 ? `${y + 1}-01` : `${y}-${String(mo + 1).padStart(2, "0")}`; } return out; };
const hubR2 = v => Math.round((+v || 0) * 100) / 100;

/* ---- freshness ---- */
function hubFreshBadge(f) {
  if (!f) return "No report yet";
  const s = String(f.status || "").toLowerCase();
  if (s === "complete") return "Complete";
  if (s === "partial") {
    let t = "";
    try { if (f.covered_through) t = new Date(f.covered_through).toLocaleTimeString("en-US", {hour: "numeric", minute: "2-digit", timeZone: "America/New_York"}); } catch (e) {}
    return t ? "Partial, through " + t : "Partial";
  }
  if (s === "missing" || !s) return "Missing";
  return s[0].toUpperCase() + s.slice(1);
}
function hubFreshFromPulse(s) {
  if (!s) return null;
  return {status: s.status, latest_order_at: s.latest_order_at || null, label: s.label || "", covered_through: s.covered_through || null, files: s.files || [], has_errors: !!s.has_errors};
}
let HUB_META_MEMO = null;
async function hubMeta() {
  const m = await read("hub/meta");
  if (HUB_META_MEMO && HUB_META_MEMO.src === m) return HUB_META_MEMO.out;
  const fresh = {};
  for (const [k, f] of Object.entries(m.freshness || {})) fresh[k] = {...f, badge: hubFreshBadge(f)};
  const out = {...m, stores: m.stores || {}, freshness: fresh};
  HUB_META_MEMO = {src: m, out};
  return out;
}

/* ---- online days (date x source x channel), with uploaded nights applied ---- */
let HUB_DAYS_MEMO = null;
async function hubDays() {
  const d = await read("hub/days");
  const base = hubObjs(d).map(r => ({...r, src: "warehouse"}));
  let ups = [];
  if (OVERLAYS.length) { try { ups = (await dailyRows()).filter(r => String(r.src || "").startsWith("upload:")); } catch (e) { console.warn(e); ups = []; } }
  const sig = ups.map(u => [u.date, u.channel, u.src, u.orders, u.item_sales, u.shipping, u.fees, u.refunds].join("|")).join(";");
  if (HUB_DAYS_MEMO && HUB_DAYS_MEMO.doc === d && HUB_DAYS_MEMO.sig === sig) return HUB_DAYS_MEMO.rows;
  let rows = base;
  if (ups.length) {
    const byCell = new Map();
    for (const r of base) { const k = r.date + "|" + r.channel; if (!byCell.has(k)) byCell.set(k, []); byCell.get(k).push(r); }
    const replaced = new Set(), added = [];
    for (const u of ups) {
      const k = u.date + "|" + u.channel, ws = byCell.get(k) || [];
      // Sources for this channel; eBay is split by the warehouse source mix (item sales share), all to Upright when there is none.
      let parts;
      if (ws.length) {
        const tot = ws.reduce((a, r) => a + (+r.item_sales || 0), 0);
        parts = ws.map(r => ({w: r, share: tot ? (+r.item_sales || 0) / tot : 1 / ws.length}));
      } else {
        const src = HUB_SOURCES.cashmonkey.channels.includes(u.channel) && u.channel !== "ebay" ? "cashmonkey" : "upright";
        parts = [{w: {date: u.date, source: src, channel: u.channel, units: null}, share: 1}];
      }
      let leftOrders = +u.orders || 0;
      parts.forEach((p, i) => {
        const last = i === parts.length - 1, n = {...p.w, src: u.src};
        n.orders = last ? leftOrders : Math.round((+u.orders || 0) * p.share); leftOrders -= n.orders;
        for (const m of ["item_sales", "shipping", "fees", "refunds"]) n[m] = u[m] == null ? (p.w[m] ?? 0) : hubR2(u[m] * p.share);
        if (last && parts.length > 1) for (const m of ["item_sales", "shipping", "fees", "refunds"]) if (u[m] != null) n[m] = hubR2(u[m] - parts.slice(0, -1).reduce((a, q) => a + hubR2(u[m] * q.share), 0));
        added.push(n);
      });
      replaced.add(k);
    }
    rows = base.filter(r => !replaced.has(r.date + "|" + r.channel)).concat(added)
      .sort((a, b) => a.date.localeCompare(b.date) || a.source.localeCompare(b.source) || a.channel.localeCompare(b.channel));
  }
  HUB_DAYS_MEMO = {doc: d, sig, rows};
  return rows;
}

/* ---- monthly docs: only the months a range touches ---- */
async function hubStoreDays(from, to) {
  if (!from || !to) return [];
  const out = [];
  for (const m of hubMonths(from, to)) { const d = await hubTry("storeday/" + m); if (d) for (const r of hubObjs(d)) if (r.date >= from && r.date <= to) out.push(r); }
  return out;
}
async function hubSupro(from, to) {
  const out = []; out.definitions = {}; out.estimated = []; out.notes = []; out.reports = [];
  if (!from || !to) return out;
  for (const m of hubMonths(from, to)) {
    const d = await hubTry("supro/" + m); if (!d) continue;
    Object.assign(out.definitions, d.definitions || {});
    if (d.estimated) out.estimated.push(m);
    if (d.note) out.notes.push(d.note);
    for (const f of d.reports || []) if (!out.reports.includes(f)) out.reports.push(f);
    for (const r of hubObjs(d)) if (r.date >= from && r.date <= to) out.push(r);
  }
  return out;
}
async function hubSuproRange() {
  const meta = await hubTry("hub/meta");
  const first = (meta && meta.first) || "2024-01-01", last = (meta && meta.last) || iso(Date.now());
  const ms = hubMonths(first, addDays(last, 31));
  let lo = null, hi = null;
  for (const m of ms) { const d = await hubTry("supro/" + m); if (d && d.rows && d.rows.length) { lo = d.rows.reduce((a, r) => !a || r[0] < a ? r[0] : a, ""); break; } }
  for (const m of ms.slice().reverse()) { const d = await hubTry("supro/" + m); if (d && d.rows && d.rows.length) { hi = d.rows.reduce((a, r) => r[0] > a ? r[0] : a, ""); break; } }
  return {first: lo, last: hi};
}
let HUB_THR_MEMO = null;
async function hubThriftly() {
  const d = await hubTry("thriftly/all");
  if (!d) { const e = []; e.definitions = {}; return e; }
  if (HUB_THR_MEMO && HUB_THR_MEMO.doc === d) return HUB_THR_MEMO.rows;
  const rows = hubObjs(d); rows.definitions = d.definitions || {};
  HUB_THR_MEMO = {doc: d, rows};
  return rows;
}

/* ---- shared math for the dialogs and files ---- */
const hubSpan = (f, t) => Math.round((D(t) - D(f)) / MS) + 1;
const hubPrior = (f, t) => f === t ? {from: addDays(f, -7), to: addDays(t, -7)} : {from: addDays(f, -hubSpan(f, t)), to: addDays(f, -1)};
const hubRangeText = (f, t) => f === t ? longDay(f) : `${shortDay(f)} to ${shortDay(t)}, ${t.slice(0, 4)}`;
function hubAdd(rows, from, to, keys, pred) {
  const o = Object.fromEntries(keys.map(k => [k, 0])); let n = 0;
  for (const r of rows) if (r.date >= from && r.date <= to && (!pred || pred(r))) { n++; for (const k of keys) o[k] += +r[k] || 0; }
  o._n = n; return o;
}
function hubDeltaHtml(a, b, inv) {
  if (a == null || b == null || !b || !isFinite(a) || !isFinite(b)) return "";
  const p = (a - b) / Math.abs(b) * 100, good = inv ? p <= 0 : p >= 0;
  return `<span class="delta ${good ? "up" : "down"}">${p >= 0 ? "▲" : "▼"} ${nf(Math.abs(p), 0)}%</span>`;
}
async function hubDefaultRange() {
  if (state.hubRange && state.hubRange.from && state.hubRange.to) return {...state.hubRange};
  const days = await hubDays(), last = days.reduce((a, r) => r.date > a ? r.date : a, "");
  return {from: last, to: last};
}
function hubFileStamp(r) { return r.from === r.to ? r.from : `${r.from}-to-${r.to}`; }
async function hubSave(wb, name) {
  if (!window.XLSX) { hubToast("The spreadsheet library did not load. Check your connection."); return false; }
  let dl = null;
  try { dl = window.claude && window.claude.use ? await window.claude.use("downloads") : null; } catch (e) { dl = null; }
  if (dl && typeof dl.save === "function") {
    try { await dl.save({filename: name, data: new Blob([XLSX.write(wb, {bookType: "xlsx", type: "array"})])}); hubToast("Saved " + name); return true; }
    catch (err) { if (err && err.code === "declined") return false; hubToast(err && err.code === "rate_limited" ? "A save prompt is already open" : "Download is not available in this view"); return false; }
  }
  try { XLSX.writeFile(wb, name); hubToast("Downloaded " + name); return true; } catch (err) { hubToast("Download is not available in this view"); return false; }
}

/* ---- one dialog element, reused ---- */
function hubDialog(id, label) {
  let dlg = document.getElementById(id);
  if (!dlg) { dlg = document.createElement("dialog"); dlg.id = id; dlg.className = "hubdlg"; dlg.setAttribute("aria-label", label); document.body.appendChild(dlg);
    dlg.addEventListener("click", e => { if (e.target === dlg) dlg.close(); }); }
  return dlg;
}
function hubShow(dlg) { try { if (!dlg.open) dlg.showModal(); } catch (e) { dlg.setAttribute("open", ""); } }

/* ---------------- Send to leadership (email preview; sending is simulated) ---------------- */
async function hubSourceSummary(key, from, to, P, days, meta) {
  const S = HUB_SOURCES[key];
  if (S.kind === "ecom") {
    const rs = days.filter(r => r.source === key), k = ["item_sales", "orders"];
    const rows = S.channels.map(c => { const a = hubAdd(rs, from, to, k, r => r.channel === c), b = hubAdd(rs, P.from, P.to, k, r => r.channel === c);
      return [esc(hubChLabel(key, c)), usd(a.item_sales), b._n ? hubDeltaHtml(a.item_sales, b.item_sales) : "", nf(a.orders)]; });
    const a = hubAdd(rs, from, to, k), b = hubAdd(rs, P.from, P.to, k);
    rows.push([`<b>${esc(S.label)} total</b>`, `<b>${usd(a.item_sales)}</b>`, b._n ? hubDeltaHtml(a.item_sales, b.item_sales) : "", `<b>${nf(a.orders)}</b>`]);
    return {head: ["Channel", "Sales", "Change", "Customers"], rows, sales: a.item_sales, cust: a.orders, has: a._n > 0, prior: b._n ? b.item_sales : null, priorCust: b._n ? b.orders : null};
  }
  if (S.kind === "retail") {
    const all = await hubSupro(P.from < from ? P.from : from, to), k = ["sales", "customers", "returns"];
    const a = hubAdd(all, from, to, k), b = hubAdd(all, P.from, P.to, k);
    const est = all.some(r => r.est && r.date >= from && r.date <= to);
    return {head: ["Measure", "Value", "Change"], rows: [["Store sales" + (est ? " (some days estimated)" : ""), usd(a.sales), b._n ? hubDeltaHtml(a.sales, b.sales) : ""], ["Customers", nf(a.customers), b._n ? hubDeltaHtml(a.customers, b.customers) : ""], ["Returns", usd(a.returns), b._n ? hubDeltaHtml(a.returns, b.returns, true) : ""]],
      sales: a.sales, cust: a.customers, has: a._n > 0, prior: b._n ? b.sales : null, priorCust: b._n ? b.customers : null};
  }
  const th = await hubThriftly(), k = ["pieces", "labor_hours", "listed", "sold"], a = hubAdd(th, from, to, k), b = hubAdd(th, P.from, P.to, k);
  const pph = x => x.labor_hours ? x.pieces / x.labor_hours : null;
  return {head: ["Measure", "Value", "Change"], rows: [["Pieces processed", nf(a.pieces), b._n ? hubDeltaHtml(a.pieces, b.pieces) : ""], ["Pieces per labor hour", pph(a) == null ? "n/a" : nf(pph(a), 1), b._n ? hubDeltaHtml(pph(a), pph(b)) : ""],
    ["Sell-through", a.listed ? nf(a.sold / a.listed * 100, 0) + "%" : "n/a", ""]], has: a._n > 0};
}
async function hubSend(scope, range, src) {
  const r = range && range.from ? {from: range.from, to: range.to || range.from} : await hubDefaultRange();
  const one = src && HUB_SOURCES[src] ? src : null;
  const st = {scope: scope === "one" && one ? "one" : "all"};
  const dlg = hubDialog("hub-send", "Send to leadership");
  const [days, meta] = await Promise.all([hubDays(), hubMeta()]);
  const P = hubPrior(r.from, r.to), RL = hubRangeText(r.from, r.to);
  const paint = async () => {
    const keys = st.scope === "all" ? Object.keys(HUB_SOURCES) : [one];
    const subj = (st.scope === "all" ? (r.from === r.to ? "Nightly report" : "Revenue report") : `${HUB_SOURCES[one].label} report`) + ", " + RL;
    const att = `goodwill-${st.scope === "all" ? "all-sources" : one}-${hubFileStamp(r)}.xlsx`;
    const sums = {}; for (const k of Object.keys(HUB_SOURCES)) sums[k] = await hubSourceSummary(k, r.from, r.to, P, days, meta);
    let b = `<p>Good evening,</p><p>Here are the totals for <b>${esc(RL)}</b>, calculated from the source reports. Change is ${r.from === r.to ? "against the same night last week" : `against the ${hubSpan(r.from, r.to)} days before`}.</p>`;
    if (st.scope === "all") {
      const ent = ["upright", "cashmonkey", "supro"].reduce((a, k) => a + (sums[k].sales || 0), 0), cust = ["upright", "cashmonkey", "supro"].reduce((a, k) => a + (sums[k].cust || 0), 0);
      const ps = ["upright", "cashmonkey", "supro"].map(k => sums[k].prior), prior = ps.every(x => x != null) ? ps.reduce((a, x) => a + x, 0) : null;
      b += `<p class="em-big"><b>Enterprise sales ${usd(ent)}</b> ${hubDeltaHtml(ent, prior)} · ${nf(cust)} customers</p>`;
    }
    for (const k of keys) { const s = sums[k];
      b += `<h4>${esc(HUB_SOURCES[k].label)}</h4>` + (s.has ? `<table><thead><tr>${s.head.map(h => `<th>${esc(h)}</th>`).join("")}</tr></thead><tbody>${s.rows.map(row => `<tr>${row.map(c => `<td>${c}</td>`).join("")}</tr>`).join("")}</tbody></table>` : `<p class="muted">No ${esc(HUB_SOURCES[k].label)} data for this period.</p>`); }
    b += `<p class="muted">Detail by channel, store and night is in the attached file, which also imports into Business Central.</p>`;
    dlg.innerHTML = `<div class="dlg"><h2>Send to leadership</h2><p class="hint">What lands in their inbox.</p>
      <div class="seg dseg" role="group" aria-label="What to send">
        <button type="button" data-scope="one" aria-pressed="${st.scope === "one"}"${one ? "" : " disabled"}>${esc(one ? HUB_SOURCES[one].label + " only" : "This report")}<small>${one ? "The report you have open" : "Open a report first"}</small></button>
        <button type="button" data-scope="all" aria-pressed="${st.scope === "all"}">All four reports<small>Upright, Cash Monkey, Supro, Thriftly</small></button></div>
      <div class="email"><div class="eh"><div><span>To:</span> CEO, COO, CFO</div><div><span>Subject:</span> <b id="em-subj">${esc(subj)}</b></div><div><span>Attachment:</span> ${esc(att)}</div></div>
        <div class="eb" id="em-body">${b}</div></div>
      <div class="acts"><button type="button" class="pill" id="send-cancel">Cancel</button><button type="button" class="pill" id="send-att">Download attachment</button><button type="button" class="pill primary" id="send-go">Send email</button></div>
      <p class="note">Sending is simulated here. Nothing leaves this page.</p></div>`;
    dlg.querySelectorAll("[data-scope]").forEach(x => x.onclick = () => { if (x.disabled) return; st.scope = x.dataset.scope; paint(); });
    dlg.querySelector("#send-cancel").onclick = () => dlg.close();
    dlg.querySelector("#send-att").onclick = () => hubDownloadBC(r, st.scope === "one" ? one : null, att);
    dlg.querySelector("#send-go").onclick = () => { dlg.close(); hubToast(`${st.scope === "all" ? "All four reports" : HUB_SOURCES[one].label + " report"} for ${RL} sent to CEO, COO and CFO`); };
  };
  await paint();
  hubShow(dlg);
}

/* ---------------- Business Central import file ---------------- */
async function hubBCWorkbook(range, only) {
  const {from, to} = range, wb = XLSX.utils.book_new();
  const [days, meta, th] = await Promise.all([hubDays(), hubMeta(), hubThriftly()]);
  const sup = await hubSupro(from, to), stores = meta.stores || {}, storeName = id => id ? stores[id] || id : "Unassigned";
  const want = k => !only || only === k;
  const dates = []; for (let d = from; d <= to; d = addDays(d, 1)) dates.push(d);
  const kpi = [["Posting date", "Source", "Channel", "Item sales", "Shipping", "Fees", "Refunds / returns", "Net", "Customers", "Units", "Avg order", "Data from"]];
  const krow = (d, s, c, v, from_) => [d, s, c, hubR2(v.item_sales), hubR2(v.shipping), hubR2(v.fees), hubR2(v.refunds), hubR2(v.net), Math.round(v.orders), v.units == null ? "" : Math.round(v.units), v.orders ? hubR2(v.item_sales / v.orders) : "", from_];
  const tot = {item_sales: 0, shipping: 0, fees: 0, refunds: 0, net: 0, orders: 0, units: 0};
  const addT = (t, v) => { for (const k in t) t[k] += (+v[k] || 0); };
  for (const d of dates) {
    const dayT = {item_sales: 0, shipping: 0, fees: 0, refunds: 0, net: 0, orders: 0, units: 0}; let any = false;
    for (const s of HUB_ECOM) if (want(s)) for (const r of days.filter(x => x.date === d && x.source === s)) {
      const v = {...r, net: r.item_sales - r.fees - r.refunds}; kpi.push(krow(d, HUB_SOURCES[s].label, hubChLabel(s, r.channel), v, r.src === "warehouse" ? "Nightly pull" : "Uploaded file")); addT(dayT, v); any = true; }
    if (want("supro")) { const rs = sup.filter(x => x.date === d); if (rs.length) { const a = hubAdd(rs, d, d, ["sales", "customers", "returns", "units"]);
      const v = {item_sales: a.sales, shipping: 0, fees: 0, refunds: a.returns, net: a.sales, orders: a.customers, units: a.units};
      kpi.push(krow(d, "Supro", "Retail stores", v, rs.some(x => x.est) ? "Estimated" : "Supro report")); addT(dayT, v); any = true; } }
    if (any && !only) kpi.push(krow(d, "All", "Enterprise total", dayT, "")); addT(tot, dayT);
  }
  if (dates.length > 1) kpi.push(krow(`${from} to ${to}`, only ? HUB_SOURCES[only].label : "All", "Range total", tot, ""));
  kpi.push([], ["Net = item sales − fees − refunds for online channels (assumption to confirm). Supro sales are already net of returns."]);
  if (want("upright") || want("cashmonkey") || want("supro") || !only) XLSX.utils.book_append_sheet(wb, XLSX.utils.aoa_to_sheet(kpi), "KPI totals");
  if (!only || HUB_SOURCES[only].kind !== "prod") {
    const sd = [["Posting date", "Source", "Channel", "Store", "Item sales", "Shipping", "Customers", "Units"]];
    for (const r of await hubStoreDays(from, to)) if (want(r.source)) sd.push([r.date, HUB_SOURCES[r.source] ? HUB_SOURCES[r.source].label : r.source, hubChLabel(r.source, r.channel), storeName(r.store), hubR2(r.item_sales), hubR2(r.shipping), r.orders, r.units]);
    if (want("supro")) for (const r of sup) sd.push([r.date, "Supro", "Retail stores", storeName(r.store), hubR2(r.sales), 0, r.customers, r.units]);
    sd.push([], ["Online store rows come from warehouse order lines; uploaded files have no store. An order with items from two stores counts at both."]);
    XLSX.utils.book_append_sheet(wb, XLSX.utils.aoa_to_sheet(sd), "Store detail");
  }
  if (want("thriftly")) {
    const pr = [["Posting date", "Pieces processed", "Labor hours", "Pieces per hour", "Sent to e-commerce", "Listed", "Sold", "Sell-through"]];
    for (const r of th) if (r.date >= from && r.date <= to) pr.push([r.date, r.pieces, r.labor_hours, r.labor_hours ? hubR2(r.pieces / r.labor_hours) : "", r.sent_to_ecom, r.listed, r.sold, r.listed ? Math.round(r.sold / r.listed * 10000) / 10000 : ""]);
    XLSX.utils.book_append_sheet(wb, XLSX.utils.aoa_to_sheet(pr), "Production");
  }
  if (!only && typeof bcJournal === "function") {   // journal builder + account map live in 55_view_bc.js
    const jl = [["Posting date", "Document", "Line", "Account type", "Account", "Description", "Debit", "Credit", "Placeholder account"]];
    for (const d of dates.slice(-62)) { const j = await bcJournal(d); if (j) for (const l of j.lines) jl.push([d, l.documentNumber, l.lineNumber, l.accountType, l.accountNumber, l.description, l.amount > 0 ? l.amount : "", l.amount < 0 ? -l.amount : "", l._acct.ph ? "Yes" : ""]); }
    if (dates.length > 62) jl.push([], ["Journal lines cover the last 62 nights of the range."]);
    XLSX.utils.book_append_sheet(wb, XLSX.utils.aoa_to_sheet(jl), "Journal lines");
  }
  const log = typeof HUB_QLOG !== "undefined" ? HUB_QLOG : [];
  XLSX.utils.book_append_sheet(wb, XLSX.utils.aoa_to_sheet([["Asked at", "Question", "Answer", "Formula", "Sources"]].concat(log.length ? log.map(q => [q.at, q.q, q.a, q.formula || "", q.sources || ""]) : [["", "No questions asked this session", "", "", ""]])), "Questions asked");
  const so = [["Posting date", "Source", "Report", "Status", "Files"]], pulse = await hubTry("pulse/all");
  const RT = {upright_paid_orders: "upright", cashmonkey_orders: "cashmonkey"};
  for (const d of dates) {
    for (const s of (pulse && pulse[d] && pulse[d].sources) || []) if (RT[s.report_type] && want(RT[s.report_type])) so.push([d, HUB_SOURCES[RT[s.report_type]].label, s.label, hubFreshBadge(s), (s.files || []).join(", ")]);
  }
  if (want("supro")) so.push([`${from} to ${to}`, "Supro", "End-of-day store report", sup.estimated.length ? "Some days estimated" : "Reported", sup.reports.join(", ")]);
  if (want("thriftly")) so.push([`${from} to ${to}`, "Thriftly", "Production scans", "", "thriftly/all"]);
  if (typeof uplIndex === "function") for (const u of (await uplIndex()).filter(i => i.status !== "removed" && i.to >= from && i.from <= to)) so.push([`${u.from} to ${u.to}`, "Uploaded file", u.name, "Replaces the nightly pull for the nights it covers", u.rows + " rows"]);
  XLSX.utils.book_append_sheet(wb, XLSX.utils.aoa_to_sheet(so), "Sources");
  return wb;
}
async function hubDownloadBC(range, only, name) {
  if (!window.XLSX) { hubToast("The spreadsheet library did not load. Check your connection."); return; }
  const r = range && range.from ? {from: range.from, to: range.to || range.from} : await hubDefaultRange();
  try {
    const wb = await hubBCWorkbook(r, only && HUB_SOURCES[only] ? only : null);
    await hubSave(wb, name || `business-central-import-${hubFileStamp(r)}.xlsx`);
  } catch (e) { console.error(e); hubToast("Could not build the file: " + (e && e.message || e)); }
}

/* ---------------- Download raw data (daily / weekly / monthly / yearly, per source, with an Equations sheet) ---------------- */
const HUB_RAW_FROM = {
  upright: "Upright paid orders export (ShopGoodwill, eBay, GoodwillFinds), as loaded into the warehouse.",
  cashmonkey: "Cash Monkey orders report (Amazon, eBay books, Goodwillbooks), as loaded into the warehouse.",
  supro: "Supro end-of-day store reports, one row per store per day.",
  thriftly: "Thriftly production scans and timeclock hours, one row per day."};
const HUB_RAW = {per: "day", anchor: null, srcs: null};
async function hubRawBounds() {
  const days = await hubDays(), sr = await hubSuproRange(), th = await hubThriftly();
  const ds = [days.reduce((a, r) => !a || r.date < a ? r.date : a, ""), sr.first, th.length ? th[0].date : null].filter(Boolean);
  const de = [days.reduce((a, r) => r.date > a ? r.date : a, ""), sr.last, th.length ? th[th.length - 1].date : null].filter(Boolean);
  return {first: ds.sort()[0], last: de.sort().reverse()[0], ecomLast: de[0]};
}
function hubRawSpan(b) {
  const a = HUB_RAW.anchor; let s, e, label, file;
  if (HUB_RAW.per === "day") { s = e = a; label = longDay(a); file = a; }
  else if (HUB_RAW.per === "week") { s = weekStart(a); e = addDays(s, 6); label = "Week of " + shortDay(s) + ", " + s.slice(0, 4); file = "week-of-" + s; }
  else if (HUB_RAW.per === "month") { s = monthStart(a); const n = new Date(Date.UTC(+a.slice(0, 4), +a.slice(5, 7), 0)).getUTCDate(); e = a.slice(0, 8) + String(n).padStart(2, "0"); label = monthLabel(a.slice(0, 7)); file = a.slice(0, 7); }
  else { s = a.slice(0, 4) + "-01-01"; e = a.slice(0, 4) + "-12-31"; label = a.slice(0, 4); file = a.slice(0, 4); }
  const cs = s < b.first ? b.first : s, ce = e > b.last ? b.last : e;
  return {s: cs, e: ce, label, file, partial: cs !== s || ce !== e};
}
const hubRawSheet = k => HUB_SOURCES[k].kind === "ecom" ? HUB_SOURCES[k].label + " by channel" : HUB_SOURCES[k].kind === "retail" ? "Supro by store" : "Thriftly";
async function hubRaw(src, range) {
  const b = await hubRawBounds();
  HUB_RAW.anchor = range && range.to ? range.to : b.ecomLast;
  if (HUB_RAW.anchor > b.last) HUB_RAW.anchor = b.last;
  HUB_RAW.srcs = new Set(src && HUB_SOURCES[src] ? [src] : Object.keys(HUB_SOURCES));
  const dlg = hubDialog("hub-raw", "Download raw data");
  const counts = async sp => {
    const days = await hubDays(), sup = await hubSupro(sp.s, sp.e), th = await hubThriftly(), sd = await hubStoreDays(sp.s, sp.e);
    const o = {};
    for (const k of HUB_ECOM) o[k] = [days.filter(r => r.source === k && r.date >= sp.s && r.date <= sp.e).length, sd.filter(r => r.source === k).length];
    o.supro = [sup.length]; o.thriftly = [th.filter(r => r.date >= sp.s && r.date <= sp.e).length];
    return o;
  };
  const paint = async () => {
    const sp = hubRawSpan(b), c = await counts(sp), P = HUB_RAW.per;
    const input = P === "year" ? (() => { const ys = []; for (let y = +b.first.slice(0, 4); y <= +b.last.slice(0, 4); y++) ys.push(y);
        return `<select class="pill" id="raw-in">${ys.map(y => `<option${String(y) === HUB_RAW.anchor.slice(0, 4) ? " selected" : ""}>${y}</option>`).join("")}</select>`; })()
      : P === "month" ? `<input type="month" class="pill" id="raw-in" min="${b.first.slice(0, 7)}" max="${b.last.slice(0, 7)}" value="${HUB_RAW.anchor.slice(0, 7)}">`
      : `<input type="date" class="pill" id="raw-in" min="${b.first}" max="${b.last}" value="${HUB_RAW.anchor}">`;
    const unit = k => k === "thriftly" ? `${nf(c[k][0])} nightly rows` : k === "supro" ? `${nf(c[k][0])} store rows` : `${nf(c[k][0])} channel rows, ${nf(c[k][1])} store rows`;
    const sheets = ["About this file", "Equations"].concat([...HUB_RAW.srcs].flatMap(k => HUB_SOURCES[k].kind === "ecom" ? [hubRawSheet(k), HUB_SOURCES[k].label + " by store"] : [hubRawSheet(k)])).concat(HUB_RAW.srcs.size ? [P === "year" ? "Totals by month" : "Totals by night"] : []);
    dlg.innerHTML = `<form class="dlg" onsubmit="return false"><h2>Download raw data</h2><p class="hint">The stored rows behind every total, before any math. Every row says where it came from.</p>
      <div class="periods" role="group" aria-label="Period">${[["day", "Daily", "One night"], ["week", "Weekly", "Mon to Sun"], ["month", "Monthly", "Calendar month"], ["year", "Yearly", "Jan to Dec"]].map(([k, l, s]) => `<button type="button" data-per="${k}" aria-pressed="${P === k}">${l}<small>${s}</small></button>`).join("")}</div>
      <div class="rawpick"><label>${P === "year" ? "Year" : P === "month" ? "Month" : P === "week" ? "Any day in the week" : "Night"}${input}</label><div class="rawrange">${sp.s === sp.e ? "" : esc(hubRangeText(sp.s, sp.e)) + ` · ${hubSpan(sp.s, sp.e)} nights`}</div></div>
      <p class="rangemsg" id="raw-msg" aria-live="polite">${sp.partial ? esc(`Only part of this ${P} is in the data (${shortDay(b.first)}, ${b.first.slice(0, 4)} to ${shortDay(b.last)}, ${b.last.slice(0, 4)}), so the file covers ${hubRangeText(sp.s, sp.e)}.`) : ""}</p>
      <div class="srcpick">${Object.keys(HUB_SOURCES).map(k => `<label><input type="checkbox" value="${k}"${HUB_RAW.srcs.has(k) ? " checked" : ""}><span><b>${esc(HUB_SOURCES[k].label)}</b><span class="from">${esc(HUB_RAW_FROM[k])}</span></span><span class="rows">${esc(unit(k))}</span></label>`).join("")}</div>
      <p class="sheets">${HUB_RAW.srcs.size ? "Sheets: " + esc(sheets.join(", ")) + "." : "Pick at least one source."}</p>
      <div class="acts"><button type="button" class="pill" id="raw-cancel">Cancel</button><button type="button" class="pill primary" id="raw-go"${HUB_RAW.srcs.size ? "" : " disabled"}>Download .xlsx</button></div></form>`;
    dlg.querySelectorAll("[data-per]").forEach(x => x.onclick = () => { HUB_RAW.per = x.dataset.per; paint(); });
    const inp = dlg.querySelector("#raw-in");
    inp.onchange = () => { let v = inp.value; if (!v) return; if (P === "year") v = v + (HUB_RAW.anchor.slice(4)); else if (P === "month") v = v + "-01"; HUB_RAW.anchor = v < b.first ? b.first : v > b.last ? b.last : v; paint(); };
    dlg.querySelectorAll(".srcpick input").forEach(i => i.onchange = () => { i.checked ? HUB_RAW.srcs.add(i.value) : HUB_RAW.srcs.delete(i.value); paint(); });
    dlg.querySelector("#raw-cancel").onclick = () => dlg.close();
    dlg.querySelector("#raw-go").onclick = async () => {
      if (!window.XLSX) { hubToast("The spreadsheet library did not load. Check your connection."); return; }
      try { const wb = await hubRawWorkbook(sp, HUB_RAW.per, [...HUB_RAW.srcs]); dlg.close(); await hubSave(wb, `goodwill-raw-${{day: "daily", week: "weekly", month: "monthly", year: "yearly"}[HUB_RAW.per]}-${sp.file}.xlsx`); }
      catch (e) { console.error(e); hubToast("Could not build the file: " + (e && e.message || e)); }
    };
  };
  await paint();
  hubShow(dlg);
}
async function hubRawWorkbook(sp, per, keys) {
  const wb = XLSX.utils.book_new(), {s, e} = sp, has = k => keys.includes(k);
  const [days, meta, th] = await Promise.all([hubDays(), hubMeta(), hubThriftly()]);
  const sup = has("supro") ? await hubSupro(s, e) : [], sd = keys.some(k => HUB_ECOM.includes(k)) ? await hubStoreDays(s, e) : [];
  const stores = meta.stores || {}, storeName = id => id ? stores[id] || id : "Unassigned";
  const inR = r => r.date >= s && r.date <= e;
  const about = [["Goodwill Michiana Revenue Hub, raw data export"], [], ["Period", {day: "Daily", week: "Weekly", month: "Monthly", year: "Yearly"}[per]], ["Covers", hubRangeText(s, e) + (sp.partial ? " (partial: the rest is outside the data)" : "")],
    ["Nights", hubSpan(s, e)], ["Generated", new Date().toLocaleString("en-US")], [], ["Sheet", "Where it comes from"]];
  for (const k of keys) { about.push([hubRawSheet(k), HUB_RAW_FROM[k]]); if (HUB_SOURCES[k].kind === "ecom") about.push([HUB_SOURCES[k].label + " by store", "Warehouse order lines credited to the store that sent the item."]); }
  about.push([], ["Raw means as stored: no net, fee or ratio math applied. The Equations sheet redoes the math as live Excel formulas on these sheets and checks each result against the hub."]);
  XLSX.utils.book_append_sheet(wb, XLSX.utils.aoa_to_sheet(about), "About this file");

  // Equations: formulas over the raw sheets, next to the hub's own number.
  const rows = [["Equations, as live formulas on the raw sheets"], [`Covers ${hubRangeText(s, e)}. Column E recalculates from the raw sheets. Column F is the hub's number. Column G checks they match.`], [], ["Group", "Measure", "By hand today", "Excel formula", "Result", "Hub value", "Match"]];
  const ref = {}, q = n => `'${n}'`;
  const add = (key, group, measure, manual, f, hub, z) => { const r = rows.length + 1; ref[key] = "E" + r; const F = f.replace(/\{(\w+)\}/g, (_, k) => ref[k]);
    const v = hub == null || !isFinite(hub) ? 0 : hub;
    rows.push([group, measure, manual, "=" + F, {t: "n", f: F, v, z: z || "#,##0.00"}, {t: "n", v, z: z || "#,##0.00"}, {t: "s", f: `IF(ABS(E${r}-F${r})<0.01,"Yes","Check")`, v: "Yes"}]); };
  const ecomSel = HUB_ECOM.filter(has), ecIds = [];
  for (const k of ecomSel) {
    const S = HUB_SOURCES[k], sh = q(hubRawSheet(k)), rs = days.filter(r => r.source === k && inR(r));
    for (const c of S.channels) { const m = hubChLabel(k, c), v = hubAdd(rs, s, e, ["orders", "units", "item_sales", "shipping", "fees", "refunds"], r => r.channel === c), id = k + c, g = S.label + " · " + m;
      add(id + "_s", g, "Item sales", `Filter the report to ${m}, sum item price`, `SUMIF(${sh}!B:B,"${m}",${sh}!E:E)`, hubR2(v.item_sales));
      add(id + "_sh", g, "Shipping", `Sum shipping charged for ${m}`, `SUMIF(${sh}!B:B,"${m}",${sh}!F:F)`, hubR2(v.shipping));
      add(id + "_f", g, "Marketplace fees", `Sum fees for ${m}`, `SUMIF(${sh}!B:B,"${m}",${sh}!G:G)`, hubR2(v.fees));
      add(id + "_r", g, "Refunds", `Sum refunds for ${m}`, `SUMIF(${sh}!B:B,"${m}",${sh}!H:H)`, hubR2(v.refunds));
      add(id + "_c", g, "Customers (paid orders)", "Count paid orders", `SUMIF(${sh}!B:B,"${m}",${sh}!C:C)`, v.orders, "#,##0");
      add(id + "_a", g, "Average order", "Item sales ÷ customers", `IF({${id}_c}=0,0,{${id}_s}/{${id}_c})`, v.orders ? v.item_sales / v.orders : 0);
      add(id + "_n", g, "Net (assumed: sales − fees − refunds)", "Confirm with Amanda", `{${id}_s}-{${id}_f}-{${id}_r}`, hubR2(v.item_sales - v.fees - v.refunds));
    }
    const v = hubAdd(rs, s, e, ["orders", "item_sales"]);
    add(k + "_s", S.label + " total", "Item sales", "Add the marketplace totals", S.channels.map(c => `{${k + c}_s}`).join("+"), hubR2(v.item_sales));
    add(k + "_c", S.label + " total", "Customers", "Add the marketplace customer counts", S.channels.map(c => `{${k + c}_c}`).join("+"), v.orders, "#,##0");
    ecIds.push(k);
  }
  if (has("supro")) { const g = "Supro · Retail stores", sh = q("Supro by store"), v = hubAdd(sup, s, e, ["sales", "customers", "returns"]);
    add("st_s", g, "Store sales", "Add net sales across stores", `SUM(${sh}!D:D)`, hubR2(v.sales));
    add("st_r", g, "Returns", "Add returns across stores", `SUM(${sh}!E:E)`, hubR2(v.returns));
    add("st_c", g, "Customers (transactions)", "Add transactions across stores", `SUM(${sh}!C:C)`, v.customers, "#,##0");
    add("st_a", g, "Average order", "Store sales ÷ transactions", `IF({st_c}=0,0,{st_s}/{st_c})`, v.customers ? v.sales / v.customers : 0); }
  if (ecIds.length) { const v = hubAdd(days.filter(r => ecIds.includes(r.source)), s, e, ["item_sales", "orders"]);
    add("e_s", "Daily Summary Spreadsheet", "Total e-commerce sales", "Add Upright and Cash Monkey totals", ecIds.map(k => `{${k}_s}`).join("+"), hubR2(v.item_sales));
    add("e_c", "Daily Summary Spreadsheet", "Total e-commerce customers", "Add Upright and Cash Monkey customers", ecIds.map(k => `{${k}_c}`).join("+"), v.orders, "#,##0");
    if (has("supro")) { const st = hubAdd(sup, s, e, ["sales", "customers"]), ent = v.item_sales + st.sales;
      add("x_s", "Enterprise", "Enterprise sales", "E-commerce total + store sales", "{e_s}+{st_s}", hubR2(ent));
      add("x_m", "Enterprise", "E-commerce share of sales", "E-commerce ÷ enterprise", "IF({x_s}=0,0,{e_s}/{x_s})", ent ? v.item_sales / ent : 0, "0.0%"); } }
  if (has("thriftly")) { const g = "Thriftly · Production", sh = q("Thriftly"), v = hubAdd(th, s, e, ["pieces", "labor_hours", "listed", "sold"]);
    add("t_p", g, "Pieces processed", "Add pieces from the production view", `SUM(${sh}!B:B)`, v.pieces, "#,##0");
    add("t_h", g, "Labor hours", "Add labor hours", `SUM(${sh}!C:C)`, hubR2(v.labor_hours));
    add("t_pph", g, "Pieces per labor hour", "Pieces ÷ labor hours", "IF({t_h}=0,0,{t_p}/{t_h})", v.labor_hours ? v.pieces / v.labor_hours : 0);
    add("t_st", g, "Sell-through", "Sold ÷ listed (definition to confirm)", `IF(SUM(${sh}!E:E)=0,0,SUM(${sh}!F:F)/SUM(${sh}!E:E))`, v.listed ? v.sold / v.listed : 0, "0.0%"); }
  rows.push([], ["Yes means the formula on the raw rows matches the hub. Check means they differ; look at that source's sheet."]);
  const ws = XLSX.utils.aoa_to_sheet(rows); ws["!cols"] = [{wch: 28}, {wch: 34}, {wch: 40}, {wch: 52}, {wch: 14}, {wch: 14}, {wch: 8}];
  XLSX.utils.book_append_sheet(wb, ws, "Equations");

  for (const k of ecomSel) {
    const S = HUB_SOURCES[k], a = [["Business date", "Channel", "Orders", "Units", "Item sales", "Shipping", "Fees", "Refunds", "Data from", "Source report"]];
    for (const r of days.filter(r => r.source === k && inR(r))) a.push([r.date, hubChLabel(k, r.channel), r.orders, r.units == null ? "" : r.units, r.item_sales, r.shipping, r.fees, r.refunds, r.src === "warehouse" ? "Nightly pull" : "Uploaded file (" + r.src.slice(7) + ")", S.label + " " + S.report]);
    XLSX.utils.book_append_sheet(wb, XLSX.utils.aoa_to_sheet(a), hubRawSheet(k));
    const b = [["Business date", "Channel", "Store", "Orders", "Units", "Item sales", "Shipping (shared by item share)", "Source report"]];
    for (const r of sd.filter(r => r.source === k)) b.push([r.date, hubChLabel(k, r.channel), storeName(r.store), r.orders, r.units, r.item_sales, r.shipping, S.label + " " + S.report]);
    XLSX.utils.book_append_sheet(wb, XLSX.utils.aoa_to_sheet(b), S.label + " by store");
  }
  if (has("supro")) { const a = [["Business date", "Store", "Transactions", "Net sales", "Returns", "Units", "Estimated", "Source report"]];
    for (const r of sup) a.push([r.date, storeName(r.store), r.customers, r.sales, r.returns, r.units, r.est ? "Yes" : "", "Supro end-of-day store report"]);
    XLSX.utils.book_append_sheet(wb, XLSX.utils.aoa_to_sheet(a), "Supro by store"); }
  if (has("thriftly")) { const a = [["Production date", "Pieces processed", "Labor hours", "Sent to e-commerce", "Listed", "Sold", "Source report"]];
    for (const r of th.filter(inR)) a.push([r.date, r.pieces, r.labor_hours, r.sent_to_ecom, r.listed, r.sold, "Thriftly production scans"]);
    XLSX.utils.book_append_sheet(wb, XLSX.utils.aoa_to_sheet(a), "Thriftly"); }

  // Totals by night (or by month for a year).
  const groups = []; const byMonth = per === "year";
  for (let d = s; d <= e; d = addDays(d, 1)) { const k = byMonth ? d.slice(0, 7) : d; if (!groups.length || groups[groups.length - 1].k !== k) groups.push({k, label: byMonth ? monthLabel(k) : d, from: d, to: d}); groups[groups.length - 1].to = d; }
  const head = [byMonth ? "Month" : "Night"];
  for (const k of ecomSel) for (const c of HUB_SOURCES[k].channels) head.push(hubChLabel(k, c) + " sales", hubChLabel(k, c) + " customers");
  if (has("supro")) head.push("Store sales", "Store customers");
  if (has("thriftly")) head.push("Thriftly pieces", "Thriftly labor hours");
  const line = (label, f, t) => { const r = [label];
    for (const k of ecomSel) for (const c of HUB_SOURCES[k].channels) { const v = hubAdd(days, f, t, ["item_sales", "orders"], x => x.source === k && x.channel === c); r.push(hubR2(v.item_sales), v.orders); }
    if (has("supro")) { const v = hubAdd(sup, f, t, ["sales", "customers"]); r.push(hubR2(v.sales), v.customers); }
    if (has("thriftly")) { const v = hubAdd(th, f, t, ["pieces", "labor_hours"]); r.push(v.pieces, hubR2(v.labor_hours)); }
    return r; };
  const tot = [head, ...groups.map(g => line(g.label, g.from, g.to))];
  if (groups.length > 1) tot.push(line("Total", s, e));
  XLSX.utils.book_append_sheet(wb, XLSX.utils.aoa_to_sheet(tot), byMonth ? "Totals by month" : "Totals by night");
  return wb;
}

/* ---------------- page-wide buttons: data-send, data-bc, data-raw, data-up (any view) ---------------- */
document.addEventListener("click", e => {
  const b = e.target.closest && e.target.closest("[data-send],[data-bc],[data-raw],[data-up]");
  if (!b || b.disabled || !document.getElementById("main").contains(b)) return;
  e.preventDefault();
  const range = b.dataset.from && b.dataset.to ? {from: b.dataset.from, to: b.dataset.to} : (state.hubRange || null), src = b.dataset.source || null;
  const fail = err => { console.error(err); hubToast(String(err && err.message || err)); };
  if (b.hasAttribute("data-send")) hubSend(b.dataset.send || "all", range, src).catch(fail);
  else if (b.hasAttribute("data-bc")) hubDownloadBC(range).catch(fail);
  else if (b.hasAttribute("data-raw")) hubRaw(src, range).catch(fail);
  else hubGo("upload");
});
