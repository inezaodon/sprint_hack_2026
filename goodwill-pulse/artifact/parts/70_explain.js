/* ---------------- Explain: the "How we got this" audit card (contracts 1 and 2) ----------------
   Everything here is computed by code from stored documents. Nothing in this file asks a model for a number. */
const EXM_KEY = "gp_explain_mode";
function exMode() { try { return localStorage.getItem(EXM_KEY) === "full" ? "full" : "quick"; } catch (e) { return "quick"; } }
function exSetMode(m) {
  try { localStorage.setItem(EXM_KEY, m); } catch (e) {}
  document.querySelectorAll(".ex-card").forEach(c => { c.dataset.mode = m; c.querySelectorAll("[data-exmode]").forEach(b => b.setAttribute("aria-pressed", b.dataset.exmode === m)); });
}
async function exLin() {
  const out = {};
  await Promise.all(["overview", "metrics", "ledger", "drill"].map(async k => { try { out[k] = await read("lineage/" + k); } catch (e) { out[k] = null; } }));
  return out;
}
const exMeasure = (lin, id) => lin.metrics && (lin.metrics.measures || []).find(m => m.id === id) || null;
const exUnit = (lin, id, fallback) => { const m = exMeasure(lin, id); return (m && m.unit) || fallback || (["orders", "units_sold", "items_identified", "items_sent", "items_listed"].includes(id) ? "count" : "usd"); };
const exFmt = (unit, v) => v == null ? "n/a" : unit === "usd" ? usd(v, 2) : fmt(unit, v);
const exTable = (heads, rows, rightFrom = 1) => `<div class="scroll ex-tbl"><table><thead><tr>${heads.map((h, i) => `<th class="${i >= rightFrom ? "r" : ""}">${esc(h)}</th>`).join("")}</tr></thead><tbody>${rows.map(r => `<tr class="${r.cls || ""}">${r.cells.map((c, i) => `<td class="${i >= rightFrom ? "r num" : ""}">${c}</td>`).join("")}</tr>`).join("")}</tbody></table></div>`;
const exStatusPill = (s, text) => pill(s === "match" ? "p-ok" : s === "diff" ? "p-warn" : "p-info", text || (s === "match" ? "Match" : s === "diff" ? "Differs" : "Not checked"));
const exPeriod = (a, b) => a === b ? longDay(a) : `${shortDay(a)}${a.slice(0, 4) !== b.slice(0, 4) ? ", " + a.slice(0, 4) : ""} to ${shortDay(b)}, ${b.slice(0, 4)}`;
const exMonthEnd = m => addDays(addDays(m + "-01", 31).slice(0, 8) + "01", -1).slice(0, 10) >= m + "-28" ? iso(Date.UTC(+m.slice(0, 4), +m.slice(5, 7), 0)) : m + "-28";

/* Bind dates and filters into the stored SQL text, so the reader sees the exact query for THIS number. */
function exBind(sql, b) {
  if (!sql) return "";
  let used = false;
  const val = {from: b.from, to: b.to, start: b.from, end: b.to, date: b.from, month: b.month || (b.from || "").slice(0, 7), channel: b.channel};
  let s = String(sql).replace(/[\w.]*channel\s*(?:=|in)\s*\(?\s*'?(?:\{\{|\{|:|@|\$)channel(?:\}\}|\})?'?\s*\)?/gi, m => { used = true; return b.channel ? `channel = '${b.channel}'` : "1 = 1 /* all marketplaces */"; });
  s = s.replace(/'?(?:\{\{|\{|:|@|\$)(from|to|start|end|date|month|channel)(?:\}\}|\})?'?/g, (m, k) => { used = true; return val[k] != null ? `'${val[k]}'` : "NULL"; });
  const bound = Object.entries({from: b.from, to: b.to, month: b.month, channel: b.channel}).filter(([, v]) => v).map(([k, v]) => `${k} = ${v}`).join(", ");
  return used || !bound ? s : s + `\n-- bound for this number: ${bound}`;
}
const exSqlBlock = (sql, label) => `<div class="ex-sql">${label ? `<div class="ex-sqlh"><span class="muted">${esc(label)}</span>` : `<div class="ex-sqlh"><span></span>`}<button type="button" class="ex-copy" data-excopy>Copy</button></div><pre class="mono">${esc(sql)}</pre></div>`;

function exDefinition(lin, id) {
  const m = exMeasure(lin, id);
  if (!m) return `<p class="muted">No definition for <span class="mono">${esc(id)}</span> is stored in lineage/metrics${lin.metrics ? "" : " (the lineage documents are not loaded yet)"}.</p>`;
  return `<div class="ex-def"><b>${esc(m.label)}</b> ${pill("p-info", m.kind === "kpi" ? "KPI" : m.kind === "daily" ? "Daily measure" : "Pipeline measure")}
    <p class="ex-syn"><span class="muted">Also called:</span> ${(m.synonyms || []).length ? m.synonyms.map(x => `<span class="ex-chip">${esc(x)}</span>`).join(" ") : `<span class="muted">no other names stored</span>`}</p>
    <p class="ex-formula"><span class="muted">Formula:</span> ${esc(m.formula_text || "")}</p>${m.notes ? `<p class="muted ex-note">${esc(m.notes)}</p>` : ""}</div>`;
}
function exSources(lin, id) {
  const m = exMeasure(lin, id), ov = lin.overview;
  if (!m) return `<p class="muted">Source tables are not listed for this measure yet.</p>`;
  const all = ov ? [...(ov.sources || []), ...(ov.harmonized || [])] : [];
  const rows = (m.source_tables || []).map(t => { const hit = all.find(x => x.table === t || `${x.db}.${x.table}` === t); return {cells: [`<span class="mono">${esc(t)}</span>`, hit ? nf(hit.rows) : "n/a", esc(hit ? hit.grain || "" : "")]}; });
  const ex = ov && ov.exclusions ? ov.exclusions : null;
  return (rows.length ? exTable(["Table", "Rows", "One row is"], rows.map(r => ({cells: r.cells})), 1) : `<p class="muted">No source tables listed.</p>`) +
    (m.columns_used && m.columns_used.length ? `<p class="ex-note muted">Columns read: ${m.columns_used.map(c => `<span class="mono">${esc(c)}</span>`).join(", ")}</p>` : "") +
    (ex ? `<p class="ex-note"><b>Left out on purpose:</b> ${nf(ex.test_orders)} test orders, ${nf(ex.test_skus)} test SKUs and ${nf(ex.canceled_orders)} canceled orders are not counted.</p>` : "");
}
function exLedger(lin, pred, shownId) {
  if (!lin.ledger) return `<p class="muted">The verification ledger is not loaded yet, so this number has not been independently recomputed.</p>`;
  const hits = (lin.ledger.checks || []).filter(pred);
  if (!hits.length) return `<p class="muted">Not independently recomputed yet for this period.</p>`;
  return hits.map(c => `<div class="ex-check ${c.id === shownId ? "ex-hit" : ""}"><div class="ex-checkh"><b>${esc(c.title)}</b> ${exStatusPill(c.status)} <span class="muted">difference ${esc(nf(c.diff, 4))}, tolerance ${esc(c.tolerance)}</span></div>` +
    exTable(["Path", "Value", "Rows scanned"], [...c.paths.map(p => ({cells: [esc(p.label), exFmt(exUnit(lin, c.measure), p.value), nf(p.rows_scanned)]})), {cls: "total", cells: [`Shown on this page (${esc(c.page_source || "")})`, exFmt(exUnit(lin, c.measure), c.page_value), ""]}], 1) + `</div>`).join("");
}
function exOverall(lin, pred) {
  const hits = lin.ledger ? (lin.ledger.checks || []).filter(pred) : [];
  if (!hits.length) return {s: "none", html: pill("p-info", "Not independently recomputed yet")};
  const bad = hits.filter(c => c.status !== "match").length;
  return {s: bad ? "diff" : "match", html: bad ? pill("p-warn", `${bad} of ${hits.length} independent check${hits.length > 1 ? "s" : ""} differ`) : pill("p-ok", `${hits.length} independent check${hits.length > 1 ? "s" : ""} match`)};
}

/* Daily rows: per-marketplace derivation, plus order rows from lineage/drill where covered. */
async function exDailyBlock(lin, a) {   // a: {from, to, channel, measure, label}
  const [rows, base] = await Promise.all([dailyRows(), dailyRowsBase()]);
  const col = a.measure === "avg_order" ? "item_sales" : a.measure;
  const inR = r => r.date >= a.from && r.date <= a.to && (!a.channel || r.channel === a.channel);
  const sel = rows.filter(inR), unit = exUnit(lin, col);
  const chans = CHORDER.filter(c => !a.channel || c === a.channel);
  const per = chans.map(c => { const rs = sel.filter(r => r.channel === c); return {c, days: rs.length, v: Object.fromEntries(MEAS.map(m => [m, rs.reduce((x, r) => x + r[m], 0)]))}; });
  const tot = Object.fromEntries(MEAS.map(m => [m, per.reduce((x, p) => x + p.v[m], 0)]));
  const heads = ["Marketplace", "Days", "Orders", "Item sales", "Shipping", "Fees", "Refunds"];
  let html = `<p class="ex-note muted">Each marketplace figure is the sum of its daily rows (one row per business day) between ${esc(shortDay(a.from))} and ${esc(shortDay(a.to))}. Total below is added up in your browser from those rows.</p>` +
    exTable(heads, [...per.map(p => ({cells: [esc(CHN[p.c]), nf(p.days), nf(p.v.orders), usd(p.v.item_sales, 2), usd(p.v.shipping, 2), usd(p.v.fees, 2), usd(p.v.refunds, 2)]})),
      {cls: "total", cells: ["Total, recomputed here", nf(per.reduce((x, p) => x + p.days, 0)), nf(tot.orders), `<span data-ex-recomputed="item_sales">${usd(tot.item_sales, 2)}</span>`, usd(tot.shipping, 2), usd(tot.fees, 2), usd(tot.refunds, 2)]}], 1);
  const dr = lin.drill;
  const days = [...new Set(sel.map(r => r.date))].sort();
  if (!dr) return html + `<p class="muted">Order rows are not loaded yet (lineage/drill).</p>`;
  const keys = Object.keys(dr), covered = [...new Set(keys.map(k => k.split("|")[0]))].sort();
  const cells = [];
  for (const d of days) for (const c of chans) { const k = d + "|" + c, cur = sel.find(r => r.date === d && r.channel === c); if (dr[k] && cur) cells.push({k, d, c, cur, bw: base.find(r => r.date === d && r.channel === c), dd: dr[k]}); }
  if (!cells.length) return html + `<p class="ex-note muted">Order rows are kept for ${covered.length ? `${esc(longDay(covered[0]))} through ${esc(longDay(covered[covered.length - 1]))}` : "no days"} only. This period is outside them, so it cannot be walked down to individual orders here.</p>`;
  const shown = cells.slice(-12), colKey = {item_sales: "item_sales", shipping: "shipping", fees: "fees", refunds: "refund"}[col];
  const sumOf = x => (x.rows || []).reduce((s, r) => s + (r[colKey] || 0), 0);
  html += `<h5 class="ex-h5">Order rows behind the ${esc(a.label || col)} cells</h5><p class="ex-note muted">Order rows are kept for the last ${covered.length} business days (${esc(shortDay(covered[0]))} to ${esc(shortDay(covered[covered.length - 1]))}), up to 60 per marketplace-day, largest first.${cells.length > shown.length ? ` Showing the latest ${shown.length} of ${cells.length} covered cells.` : ""}</p>`;
  html += shown.map(x => {
    const complete = x.dd.rows.length >= x.dd.total_rows, rec = sumOf(x.dd), shownV = x.cur[col], upl = x.cur.src && x.cur.src !== "warehouse";
    const ref = upl ? x.bw[col] : shownV, ok = complete && Math.abs(rec - ref) < 0.005;
    const fullSum = {item_sales: x.dd.sum_item_sales, shipping: x.dd.sum_shipping, fees: x.dd.sum_fees}[col];
    return `<details class="ex-cell"><summary><b>${esc(shortDay(x.d))} · ${esc(CHN[x.c])}</b><span class="muted">${nf(x.dd.total_rows)} orders</span>
      <span>Page ${esc(exFmt(unit, shownV))}</span><span data-ex-drill="${esc(x.k)}">Order rows add to ${esc(exFmt(unit, rec))}</span>${complete ? exStatusPill(ok ? "match" : "diff", ok ? "Match" : "Differs") : pill("p-info", `largest ${x.dd.rows.length} of ${nf(x.dd.total_rows)}`)}${upl ? pill("p-warn", "replaced by upload") : ""}</summary>
      <div class="ex-cellb">${complete ? "" : `<p class="ex-note muted">Only the largest ${x.dd.rows.length} orders are stored. The export's full total for all ${nf(x.dd.total_rows)} orders is ${esc(exFmt(unit, fullSum))}.</p>`}${upl ? `<p class="ex-note">This cell was replaced by an uploaded file (${esc(x.cur.src)}). The rows below are the warehouse's, which add to ${esc(exFmt(unit, x.bw[col]))}.</p>` : ""}
      ${exTable(["Order", "Paid (ET)", "Item sales", "Shipping", "Fees", "Refund", "From"], x.dd.rows.map(r => ({cells: [`<span class="mono">${esc(r.order_key)}</span>`, esc(String(r.paid_at_et || "").replace("T", " ").slice(0, 16)), usd(r.item_sales, 2), usd(r.shipping, 2), usd(r.fees, 2), usd(r.refund, 2), `<span class="muted">${esc(r.source_db)}</span>`]})), 2)}</div></details>`;
  }).join("");
  return html;
}

async function exUploadStep(from, to, channel) {
  if (typeof uploadProvenance !== "function") return null;
  let list = []; try { list = (await uploadProvenance()) || []; } catch (e) { return `<p class="muted">Upload provenance could not be read: ${esc(e.message)}</p>`; }
  list = list.filter(p => (!from || p.date >= from) && (!to || p.date <= to) && (!channel || p.channel === channel));
  if (!list.length) return `<p class="muted">No cell in this period was replaced by an uploaded file. All figures are the warehouse's.</p>`;
  return `<p class="ex-note">These marketplace-days use an uploaded Excel file instead of the warehouse total. The right-hand columns show how far the file differs from the warehouse.</p>` +
    exTable(["Day", "Marketplace", "File", "Warehouse item sales", "File item sales", "Difference"], list.map(p => ({cells: [esc(shortDay(p.date)), esc(CHN[p.channel] || p.channel), esc(p.name || p.upload_id), usd(p.warehouse && p.warehouse.item_sales, 2), usd(p.upload && p.upload.item_sales, 2), usd(p.diff && p.diff.item_sales, 2)]})), 3);
}

const exStep = (n, title, body, full) => `<li class="ex-step${full ? " ex-full" : ""}"><h4><span class="ex-n">${n}</span>${esc(title)}</h4><div class="ex-sb">${body}</div></li>`;
function exCard(title, status, steps) {
  const m = exMode(); let n = 0;
  return `<div class="ex-card" data-mode="${m}"><div class="ex-head"><b>How we got this</b><span class="ex-title">${esc(title)}</span>${status}
    <div class="seg ex-seg" role="group" aria-label="Verification depth"><button type="button" data-exmode="quick" aria-pressed="${m === "quick"}">Quick check</button><button type="button" data-exmode="full" aria-pressed="${m === "full"}">Full audit trail</button></div></div>
    <ol class="ex-steps">${steps.filter(Boolean).map(s => exStep(++n, s.t, s.b, s.full)).join("")}</ol>
    <p class="ex-foot muted">Numbers are always computed by code from stored data. A model never produces a displayed number.</p></div>`;
}
const exRule = (label, v) => `<li><span class="muted">${esc(label)}</span> ${v}</li>`;

/* ---------- builders per kind ---------- */
async function exAsk(p, lin) {
  const sp = p.spec || {}, res = p.res || {}, f = sp.filters || {}, m = res.m || (typeof AM !== "undefined" && AM[sp.metric]) || {label: sp.metric, unit: "count"};
  const metric = res.metric || sp.metric, from = res.from || (sp.period || {}).from, to = res.to || (sp.period || {}).to, pipe = m.src === "pipe";
  const engine = p.engine === "claude" ? "Claude" : "built-in keyword rules";
  const fl = [f.channel && "marketplace = " + (CHN[f.channel] || f.channel), f.store && "store = " + f.store, f.category && "category = " + f.category].filter(Boolean);
  const by = sp.by === "none" ? "nothing (one total)" : sp.by === "time" ? `time, by ${sp.grain || "day"}${sp.split ? ", split by marketplace" : ""}` : sp.by;
  const unit = m.unit === "usd" ? "usd" : "count";
  const bind = {from, to, channel: f.channel};
  let rowsHtml = "", recomputed = null;
  if (!pipe) {
    rowsHtml = await exDailyBlock(lin, {from, to, channel: f.channel, measure: metric, label: m.label});
    const rows = (await dailyRows()).filter(r => r.date >= from && r.date <= to && (!f.channel || r.channel === f.channel));
    recomputed = metric === "avg_order" ? (rows.reduce((a, r) => a + r.orders, 0) ? rows.reduce((a, r) => a + r.item_sales, 0) / rows.reduce((a, r) => a + r.orders, 0) : 0) : rows.reduce((a, r) => a + r[metric], 0);
  } else {
    try {
      const sc = await read("storecat/all"), ix = Object.fromEntries(sc.cols.map((c, i) => [c, i])), m0 = from.slice(0, 7), m1 = to.slice(0, 7);
      const rs = sc.rows.filter(r => r[ix.month] >= m0 && r[ix.month] <= m1 && (!f.store || r[ix.store] === f.store) && (!f.category || r[ix.category] === f.category));
      const byM = {}; rs.forEach(r => { byM[r[ix.month]] = (byM[r[ix.month]] || 0) + r[ix[m.col]]; });
      recomputed = rs.reduce((a, r) => a + r[ix[m.col]], 0);
      rowsHtml = `<p class="ex-note muted">Summed from the month by store by category pipeline rows (storecat/all), ${nf(rs.length)} rows.</p>` + exTable(["Month", m.label], [...Object.entries(byM).sort().map(([k, v]) => ({cells: [esc(monthLabel(k)), exFmt(unit, v)]})), {cls: "total", cells: ["Total, recomputed here", `<span data-ex-recomputed="total">${exFmt(unit, recomputed)}</span>`]}], 1);
    } catch (e) { rowsHtml = `<p class="muted">${esc(e.message)}</p>`; }
  }
  const displayed = res.total != null ? res.total : (res.all ? (metric === "avg_order" ? null : res.all.reduce((a, i) => a + i.value, 0)) : null);
  const cmp = displayed == null ? "" : `<p class="ex-cmp">Displayed ${esc(exFmt(unit, displayed))} · recomputed from the rows ${esc(exFmt(unit, recomputed))} ${exStatusPill(Math.abs(displayed - recomputed) < 0.005 ? "match" : "diff")}</p>`;
  const pred = c => c.measure === metric && (!c.scope.date || (c.scope.date >= from && c.scope.date <= to)) && (!c.scope.month || (c.scope.month >= from.slice(0, 7) && c.scope.month <= to.slice(0, 7))) && (!c.scope.channel || !f.channel || c.scope.channel === f.channel);
  const up = await exUploadStep(pipe ? null : from, to, f.channel);
  return {title: p.question || m.label, status: exOverall(lin, pred).html, steps: [
    {t: "What you asked", b: `<p class="ex-q">“${esc(p.question || "")}”</p><p>${p.interpretation ? esc(p.interpretation) : `Read as: ${esc(m.label)} for ${esc(exPeriod(from, to))}.`}</p><p>Read by: <b>${esc(engine)}</b>. The model only reads the question; the numbers below were computed by code.</p>`},
    {t: "The spec", b: `<ul class="ex-spec">${exRule("Measure", esc(m.label))}${exRule("Grouped by", esc(by))}${exRule("Filters", esc(fl.length ? fl.join(", ") : "none"))}${exRule("Period", esc(exPeriod(from, to)))}${(res.notes || []).map(n => `<li class="muted">${esc(n)}</li>`).join("")}</ul>`},
    {t: "The definition", b: exDefinition(lin, metric)},
    {t: "The query", full: 1, b: exMeasure(lin, metric) ? exSqlBlock(exBind(exMeasure(lin, metric).sql, bind)) : `<p class="muted">No SQL is stored for this measure yet.</p>`},
    {t: "The sources", full: 1, b: exSources(lin, metric)},
    {t: "The rows behind it", full: 1, b: cmp + rowsHtml},
    {t: "The independent check", full: 1, b: exLedger(lin, pred)},
    up != null ? {t: "Upload provenance", full: 1, b: up} : null]};
}

async function exReport(p, lin) {
  const {from, to} = p, ids = ["item_sales", "orders", "shipping", "fees", "refunds"];
  const pred = c => ids.includes(c.measure) && ((c.scope.date && c.scope.date >= from && c.scope.date <= to) || (c.scope.month && c.scope.month >= from.slice(0, 7) && c.scope.month <= to.slice(0, 7)));
  const block = await exDailyBlock(lin, {from, to, measure: "item_sales", label: "item sales"});
  const up = await exUploadStep(from, to, null);
  const defs = ids.map(i => exDefinition(lin, i)).join("");
  const sqls = ids.map((i, k) => { const m = exMeasure(lin, i); return m ? `<details class="ex-sqld" ${k === 0 ? "open" : ""}><summary><b>${esc(m.label)}</b></summary>${exSqlBlock(exBind(m.sql, {from, to}))}</details>` : ""; }).join("");
  const tbl = [...new Set(ids.flatMap(i => (exMeasure(lin, i) || {}).source_tables || []))];
  return {title: `Nightly report, ${exPeriod(from, to)}`, status: exOverall(lin, pred).html, steps: [
    {t: "Which number", b: `<p>The nightly report totals for <b>${esc(exPeriod(from, to))}</b>: item sales, orders, shipping, fees and refunds for each marketplace, and the total across marketplaces.</p><p>No model is involved. The page adds up stored daily rows.</p>`},
    {t: "The spec", b: `<ul class="ex-spec">${exRule("Measures", "item sales, orders, shipping charged, fees, refunds")}${exRule("Grouped by", "marketplace")}${exRule("Filters", "none (paid, non-test, non-canceled orders only)")}${exRule("Period", esc(exPeriod(from, to)) + ", Eastern business days")}</ul>`},
    {t: "The definition", b: defs},
    {t: "The query", full: 1, b: sqls || `<p class="muted">No SQL is stored yet.</p>`},
    {t: "The sources", full: 1, b: exSources(lin, "item_sales")},
    {t: "The rows behind it", full: 1, b: block},
    {t: "The independent check", full: 1, b: exLedger(lin, pred)},
    up != null ? {t: "Upload provenance", full: 1, b: up} : null].filter(Boolean).map(s => s), tbl};
}

async function exKpi(p, lin) {
  const doc = await read("month/" + p.month), c = (doc.cards || {})[p.id];
  if (!c) throw new Error(`No KPI ${p.id} stored for ${p.month}.`);
  const m = exMeasure(lin, p.id), unit = c.unit;
  const pm = addDays(p.month + "-01", -1).slice(0, 7), py = (+p.month.slice(0, 4) - 1) + p.month.slice(4);
  const rd = async mo => { try { const d = await read("month/" + mo); return (d.cards[p.id] || {}).value; } catch (e) { return undefined; } };
  const [vpm, vpy] = await Promise.all([rd(pm), rd(py)]);
  const row = (lab, stored, doc2, other) => ({cells: [esc(lab), fmt(unit, stored), `<span class="mono">${esc(doc2)}</span>`, other === undefined ? `<span class="muted">not stored</span>` : other == null && stored == null ? exStatusPill("match", "Match") : exStatusPill(Math.abs((other ?? 0) - (stored ?? 0)) < 1e-9 ? "match" : "diff", Math.abs((other ?? 0) - (stored ?? 0)) < 1e-9 ? "Same in its own month" : "Differs")]});
  const pred = x => x.measure === p.id && (!x.scope.month || x.scope.month === p.month);
  const rows = `<p class="ex-note muted">The value, prior month and prior year are stored on this KPI card in <span class="mono">month/${esc(p.month)}</span>. The prior figures are also checked against the KPI's own month documents.</p>` +
    exTable(["Reading", "Value", "Read from", "Cross-check"], [{cells: [esc(monthLabel(p.month)), fmt(unit, c.value), `<span class="mono">month/${esc(p.month)}</span>`, `<span class="muted">this card</span>`]}, row("Prior month " + monthShort(pm), c.prior_month, `month/${pm}`, vpm), row("Prior year " + monthShort(py), c.prior_year, `month/${py}`, vpy)], 1) +
    `<p class="ex-cmp">Change vs prior month, recomputed here: ${delta(c, "prior_month")} · vs prior year: ${delta(c, "prior_year")}</p>` + (c.notes && c.notes.length ? `<p class="ex-note muted">${c.notes.map(esc).join(" ")}</p>` : "");
  return {title: `${c.label}, ${monthLabel(p.month)}`, status: exOverall(lin, pred).html, steps: [
    {t: "Which number", b: `<p><b>${esc(c.label)}</b> for ${esc(monthLabel(p.month))}: <b class="ex-val" data-ex-displayed>${esc(fmt(unit, c.value))}</b>${c.provisional ? " " + pill("p-warn", "Provisional") : ""}.</p><p class="muted">Stored as of ${esc(c.as_of || "n/a")}. No model is involved in this number.</p>`},
    {t: "The spec", b: `<ul class="ex-spec">${exRule("Measure", esc(c.label))}${exRule("Grouped by", "nothing (one value for the month)")}${exRule("Period", esc(monthLabel(p.month)))}${exRule("Inputs", esc(c.source || (m && (m.columns_used || []).join(", ")) || "n/a"))}</ul>`},
    {t: "The definition", b: `${exDefinition(lin, p.id)}${m ? "" : `<p class="ex-formula"><span class="muted">Formula:</span> ${esc(c.formula || "")}</p>`}`},
    {t: "The query", full: 1, b: m ? exSqlBlock(exBind(m.sql, {from: p.month + "-01", to: exMonthEnd(p.month), month: p.month})) : `<p class="muted">No SQL is stored for this KPI yet.</p>`},
    {t: "The sources", full: 1, b: exSources(lin, p.id) + `<p class="ex-note muted">KPI inputs: ${esc(c.source || "n/a")}. Availability: ${esc(c.availability || "n/a")}.</p>`},
    {t: "The rows behind it", full: 1, b: rows},
    {t: "The independent check", full: 1, b: exLedger(lin, pred)}]};
}

async function exUpload(p, lin) {
  let idx = {items: []}, doc = null;
  try { idx = await read("uploads/index"); } catch (e) {}
  try { doc = await read("uploads/" + p.id); } catch (e) {}
  const it = (idx.items || []).find(x => x.id === p.id) || {}, meta = (doc && doc.meta) || {}, rows = (doc && doc.rows) || [];
  const byCell = {}; rows.forEach(r => { const k = r[0] + "|" + r[1]; (byCell[k] = byCell[k] || {orders: new Set(), item_sales: 0}); byCell[k].orders.add(r[2]); byCell[k].item_sales += +r[3] || 0; });
  const pretty = v => v == null ? "n/a" : typeof v === "object" ? `<span class="mono">${esc(JSON.stringify(v)).slice(0, 600)}</span>` : esc(v);
  const kv = pick => { const es = Object.entries(meta).filter(([k]) => pick.test(k)); return es.length ? exTable(["Field", "Value"], es.map(([k, v]) => ({cells: [`<span class="mono">${esc(k)}</span>`, pretty(v)]})), 1).replace(/class="r num"/g, 'class=""') : `<p class="muted">Nothing stored for this.</p>`; };
  let prov = []; if (typeof uploadProvenance === "function") { try { prov = (await uploadProvenance()).filter(x => x.upload_id === p.id); } catch (e) {} }
  const recomputed = rows.reduce((a, r) => a + (+r[3] || 0), 0);
  return {title: `Upload ${it.name || p.id}`, status: pill("p-info", it.status || "uploaded"), steps: [
    {t: "Which file", b: `<p><b>${esc(it.name || meta.name || p.id)}</b>, uploaded ${esc(it.uploaded_at || meta.uploaded_at || "n/a")}. ${nf(it.rows ?? rows.length)} rows covering ${esc(it.from || "n/a")} to ${esc(it.to || "n/a")}.</p><p class="muted">The file is read by code. No model produces its numbers.</p>`},
    {t: "The spec", b: `<p>Column mapping and exclusions applied when the file was read.</p>${kv(/map|column|header|sheet/i)}`},
    {t: "The definition", b: `<p>Each (day, marketplace) cell present in this file replaces the warehouse totals for that cell when this is the latest file covering it. Item sales are the sum of the file's item sales column.</p>${kv(/exclu|skip|reject|ignored/i)}`},
    {t: "The query", full: 1, b: `<p class="muted">An uploaded file is not queried from the warehouse. The page sums its rows by day and marketplace in the browser.</p>`},
    {t: "The sources", full: 1, b: `<p>Source: the uploaded file only (${nf(rows.length)} stored rows). Reconcile result against the warehouse:</p>${kv(/reconcil|match|warehouse|diff/i)}`},
    {t: "The rows behind it", full: 1, b: `<p class="ex-cmp">File item sales, recomputed here from its rows: <b data-ex-recomputed="total">${usd(recomputed, 2)}</b></p>` + exTable(["Day", "Marketplace", "Orders", "Item sales"], Object.entries(byCell).sort().slice(0, 40).map(([k, v]) => ({cells: [esc(shortDay(k.split("|")[0])), esc(CHN[k.split("|")[1]] || k.split("|")[1]), nf(v.orders.size), usd(v.item_sales, 2)]})), 2)},
    {t: "The independent check", full: 1, b: prov.length ? exTable(["Day", "Marketplace", "Warehouse item sales", "File item sales", "Difference"], prov.map(x => ({cells: [esc(shortDay(x.date)), esc(CHN[x.channel] || x.channel), usd(x.warehouse.item_sales, 2), usd(x.upload.item_sales, 2), usd(x.diff.item_sales, 2)]})), 2) : `<p class="muted">No warehouse comparison is available for this file yet.</p>`}]};
}

async function exCheck(p, lin) {
  const c = lin.ledger && (lin.ledger.checks || []).find(x => x.id === p.id);
  if (!c) throw new Error("That check is not in the ledger.");
  const m = exMeasure(lin, c.measure), s = c.scope || {}, shown = x => x.id === c.id;
  const from = s.date || (s.month ? s.month + "-01" : null), to = s.date || (s.month ? exMonthEnd(s.month) : null);
  let rows = "";
  if (m && m.kind === "daily" && from) rows = await exDailyBlock(lin, {from, to, channel: s.channel, measure: c.measure, label: m.label});
  else rows = `<p class="muted">Value stored on the page: ${esc(exFmt(exUnit(lin, c.measure), c.page_value))} from <span class="mono">${esc(c.page_source || "")}</span>.</p>`;
  const paths = c.paths.map(x => exSqlBlock(exBind(x.sql, {from, to, channel: s.channel, month: s.month}), x.label)).join("");
  const srcs = [...new Set(c.paths.flatMap(x => x.source_tables || []))];
  return {title: c.title, status: exStatusPill(c.status), steps: [
    {t: "Which number", b: `<p><b>${esc(c.title)}</b>. The page shows <b>${esc(exFmt(exUnit(lin, c.measure), c.page_value))}</b> from <span class="mono">${esc(c.page_source || "")}</span>.</p><p class="muted">No model is involved.</p>`},
    {t: "The spec", b: `<ul class="ex-spec">${exRule("Measure", esc(m ? m.label : c.measure))}${s.channel ? exRule("Marketplace", esc(CHN[s.channel] || s.channel)) : ""}${s.date ? exRule("Day", esc(longDay(s.date))) : ""}${s.month ? exRule("Month", esc(monthLabel(s.month))) : ""}</ul>`},
    {t: "The definition", b: exDefinition(lin, c.measure)},
    {t: "The query", full: 1, b: paths || `<p class="muted">No SQL stored.</p>`},
    {t: "The sources", full: 1, b: `<p>Tables read: ${srcs.map(t => `<span class="mono">${esc(t)}</span>`).join(", ") || "none listed"}.</p>` + exSources(lin, c.measure)},
    {t: "The rows behind it", full: 1, b: rows},
    {t: "The independent check", full: 1, b: exLedger(lin, shown, c.id)}]};
}

async function explainHTML(kind, payload) {
  try {
    const lin = await exLin(), p = payload || {};
    const b = kind === "ask" ? await exAsk(p, lin) : kind === "report" ? await exReport(p, lin) : kind === "kpi" ? await exKpi(p, lin) : kind === "upload" ? await exUpload(p, lin) : kind === "check" ? await exCheck(p, lin) : null;
    if (!b) return `<div class="banner">No explanation is available for “${esc(kind)}”.</div>`;
    return exCard(b.title, b.status, b.steps);
  } catch (e) { return `<div class="banner bad">${esc(e.message)}</div>`; }
}

/* contract 2: a button next to every [data-explain] element; the card fills in place on click */
function decorateExplain(root) {
  (root || document).querySelectorAll("[data-explain]").forEach(el => {
    const nx = el.nextElementSibling;
    if (nx && nx.classList.contains("ex-host") && nx.dataset.for === el.dataset.explain) return;
    const host = document.createElement("div"); host.className = "ex-host"; host.dataset.for = el.dataset.explain;
    host.innerHTML = `<button type="button" class="ex-btn" aria-expanded="false" data-exopen="${esc(el.dataset.explain)}">How is this calculated?</button><div class="ex-slot"></div>`;
    el.insertAdjacentElement("afterend", host);
  });
}
async function exOpen(btn) {
  const host = btn.closest(".ex-host"), slot = host.querySelector(".ex-slot");
  if (btn.getAttribute("aria-expanded") === "true") { slot.innerHTML = ""; btn.setAttribute("aria-expanded", "false"); return; }
  btn.setAttribute("aria-expanded", "true"); slot.innerHTML = `<div class="muted">Loading…</div>`;
  const [kind, a, b] = btn.dataset.exopen.split(":");
  const pay = kind === "report" ? {from: a, to: b} : kind === "kpi" ? {id: a, month: b} : {id: a};
  slot.innerHTML = await explainHTML(kind, pay);
}
if (!window.__exWired) {
  window.__exWired = true;
  document.addEventListener("click", async e => {
    const t = e.target.closest && e.target.closest("[data-exopen],[data-exmode],[data-excopy]"); if (!t) return;
    if (t.dataset.exmode) return exSetMode(t.dataset.exmode);
    if (t.hasAttribute("data-excopy")) { const pre = t.closest(".ex-sql").querySelector("pre"); try { await navigator.clipboard.writeText(pre.textContent); t.textContent = "Copied"; } catch (er) { try { const r = document.createRange(); r.selectNodeContents(pre); const s = getSelection(); s.removeAllRanges(); s.addRange(r); t.textContent = "Selected, press copy"; } catch (e2) {} } return; }
    if (t.dataset.exopen) exOpen(t);
  });
}
