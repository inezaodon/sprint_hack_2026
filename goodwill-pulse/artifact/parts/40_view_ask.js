/* ---------------- Ask: question -> spec -> chart ---------------- */
const AM = {
  item_sales: {label: "Item sales", unit: "usd", src: "daily"}, orders: {label: "Orders", unit: "count", src: "daily"},
  shipping: {label: "Shipping charged", unit: "usd", src: "daily"}, fees: {label: "Marketplace fees", unit: "usd", src: "daily"},
  refunds: {label: "Refunds", unit: "usd", src: "daily"}, avg_order: {label: "Average order", unit: "usd", src: "daily"},
  store_revenue: {label: "Sales credited to store", unit: "usd", src: "pipe", col: "revenue"}, units_sold: {label: "Units sold", unit: "count", src: "pipe", col: "sold"},
  items_identified: {label: "Items identified", unit: "count", src: "pipe", col: "identified"}, items_sent: {label: "Items sent to e-commerce", unit: "count", src: "pipe", col: "sent"},
  items_listed: {label: "Items listed online", unit: "count", src: "pipe", col: "listed"},
};
const BYS = ["time", "channel", "store", "category", "none"], CHARTS = ["line", "bar", "stat", "table"];
const addMonths = (s, k) => { const d = new Date(D(s)); d.setUTCMonth(d.getUTCMonth() + k, 1); return iso(+d); };
const monthEnd = s => addDays(addMonths(s, 1), -1);
const isIso = s => typeof s === "string" && /^\d{4}-\d{2}-\d{2}$/.test(s) && !isNaN(D(s));

async function askContext() {
  const [rows, sc, opt] = await Promise.all([dailyRows(), read("storecat/all"), read("site/options")]);
  const ix = Object.fromEntries(sc.cols.map((c, i) => [c, i]));
  const pipe = sc.rows.map(r => ({month: r[ix.month], store: r[ix.store], category: r[ix.category], identified: r[ix.identified], sent: r[ix.sent], listed: r[ix.listed], sold: r[ix.sold], revenue: r[ix.revenue]}));
  const stores = {...sc.stores, "": "Unattributed"};
  const categories = [...new Set(pipe.map(r => r.category).filter(Boolean))].sort();
  return {rows, pipe, stores, categories, last: rows.reduce((a, r) => r.date > a ? r.date : a, ""), first: rows[0].date, months: opt.months};
}

function keywordSpec(q, ctx) {
  const t = q.toLowerCase(), last = ctx.last, sp = {filters: {}};
  sp.metric = /refund/.test(t) ? "refunds" : /\bfees?\b/.test(t) ? "fees" : /ship/.test(t) ? "shipping" : /average (order|sale)|avg (order|sale)|order value/.test(t) ? "avg_order"
    : /identif|donat/.test(t) ? "items_identified" : /\bsent\b|\bsend|manifest/.test(t) ? "items_sent" : /list|scan|post/.test(t) ? "items_listed"
    : /\bunits?\b|pieces|items sold/.test(t) ? "units_sold" : /\borders?\b|transaction|customer|buyer/.test(t) ? "orders" : "item_sales";
  const timeWord = /\b(daily|weekly|monthly|quarterly|per (day|week|month|quarter)|each (day|week|month|quarter)|over time|trend|by (day|week|month|quarter)|last \d+ (days|weeks|months))\b/.test(t);
  sp.by = /\bstores?\b/.test(t) ? "store" : /categor/.test(t) ? "category" : timeWord ? "time" : /marketplace|channel|platform/.test(t) ? "channel" : "none";
  if (sp.by === "time" && /marketplace|channel|platform/.test(t)) sp.split = "channel";
  sp.grain = /quarterly|per quarter|by quarter/.test(t) ? "quarter" : /monthly|per month|each month|by month/.test(t) ? "month" : /weekly|per week|each week|by week/.test(t) ? "week" : "day";
  for (const c of ctx.categories) { const l = c.toLowerCase(), w = [l, l.replace(/s$/, ""), l.split(/[ &]+/)[0]]; if (sp.by !== "category" && w.some(x => x.length > 2 && new RegExp(`\\b${x}s?\\b`).test(t))) sp.filters.category = c; }
  const chans = {amazon: /amazon/, ebay: /ebay/, shopgoodwill: /shop ?goodwill/, goodwillfinds: /goodwill ?finds/, goodwillbooks: /goodwill ?books/};
  if (sp.by !== "channel" && sp.split !== "channel") for (const [c, re] of Object.entries(chans)) if (re.test(t)) sp.filters.channel = c;
  for (const [id, name] of Object.entries(ctx.stores)) { const city = name.replace(/^Goodwill /, "").toLowerCase(); if (id && sp.by !== "store" && city.length > 3 && t.includes(city)) sp.filters.store = id; }
  const pipe = AM[sp.metric].src === "pipe" || sp.by === "store" || sp.by === "category";
  const n = t.match(/last (\d+) (day|week|month)s?/), prevM = addMonths(monthStart(last), -1);
  let from, to = last, matched = true;
  if (n) { const k = +n[1]; if (n[2] === "day") from = addDays(last, -(k - 1)); else if (n[2] === "week") from = addDays(weekStart(last), -(k - 1) * 7); else { from = addMonths(monthStart(last), -k); to = monthEnd(addMonths(monthStart(last), -1)); } }
  else if (/yesterday|latest day|today|tonight/.test(t)) from = last;
  else if (/last week/.test(t)) { from = addDays(weekStart(last), -7); to = addDays(weekStart(last), -1); }
  else if (/this week/.test(t)) from = weekStart(last);
  else if (/last month/.test(t)) { from = prevM; to = monthEnd(prevM); }
  else if (/this month|month to date|mtd/.test(t)) from = monthStart(last);
  else if (/last quarter/.test(t)) { const q0 = quarterStart(last); from = addMonths(q0, -3); to = addDays(q0, -1); }
  else if (/this quarter/.test(t)) from = quarterStart(last);
  else if (/year to date|ytd|this year/.test(t)) from = `${last.slice(0, 4)}-01-01`;
  else if (pipe) { from = prevM; to = monthEnd(prevM); matched = false; }
  else if (sp.by === "time" && (sp.grain === "month" || sp.grain === "quarter")) { from = ctx.first; matched = false; }
  else { from = addDays(last, -29); matched = false; }
  sp.period = {from: from < ctx.first ? ctx.first : from, to};
  sp.order = /fewest|least|lowest|zero|worst|bottom|smallest|\bno\b/.test(t) ? "asc" : "desc";
  const top = t.match(/(?:top|bottom|first)\s+(\d+)/) || t.match(/(\d+)\s+(?:stores|categories|marketplaces)/); sp.limit = top ? +top[1] : 10;
  sp.chart = /\btable\b/.test(t) ? "table" : sp.by === "time" ? "line" : sp.by === "none" ? "stat" : "bar";
  sp._unmatched = !matched && sp.by === "none" && !Object.keys(sp.filters).length && sp.metric === "item_sales" && !/sales|revenue|sold/.test(t);
  return sp;
}

function cleanSpec(s, ctx) {
  if (!s || typeof s !== "object") throw new Error("No spec returned.");
  const f = s.filters || {}, p = s.period || {};
  const sp = {metric: AM[s.metric] ? s.metric : null, by: BYS.includes(s.by) ? s.by : null, grain: GRAINS[s.grain] ? s.grain : "day", split: s.split === "channel" ? "channel" : null,
    filters: {}, order: s.order === "asc" ? "asc" : "desc", limit: Math.min(30, Math.max(1, parseInt(s.limit) || 10)), chart: CHARTS.includes(s.chart) ? s.chart : null};
  if (!sp.metric || !sp.by) throw new Error("The spec used a measure or grouping this page does not have.");
  if (CHN[f.channel]) sp.filters.channel = f.channel;
  const cat = ctx.categories.find(c => c.toLowerCase() === String(f.category || "").toLowerCase()); if (cat) sp.filters.category = cat;
  const st = Object.entries(ctx.stores).find(([id, n]) => id && (id === f.store || n.toLowerCase() === String(f.store || "").toLowerCase())); if (st) sp.filters.store = st[0];
  if (!isIso(p.from) || !isIso(p.to) || p.from > p.to) throw new Error("The spec's period was not a valid date range.");
  sp.period = {from: p.from < ctx.first ? ctx.first : p.from, to: p.to > ctx.last ? ctx.last : p.to};
  sp.chart = sp.chart || (sp.by === "time" ? "line" : sp.by === "none" ? "stat" : "bar");
  return sp;
}

function bucketKeys(from, to, grain) { const out = []; for (let d = from; d <= to; d = addDays(d, 1)) { const k = GRAINS[grain].key(d); if (out[out.length - 1] !== k) out.push(k); } return out; }

function execSpec(sp, ctx) {
  const notes = []; let metric = sp.metric, m = AM[metric], {from, to} = sp.period, f = sp.filters;
  const wantsPipe = sp.by === "store" || sp.by === "category" || ((f.store || f.category) && m.src === "daily" && (metric === "item_sales" || metric === "orders"));
  if (wantsPipe && m.src === "daily") {
    if (metric === "item_sales") { metric = "store_revenue"; notes.push("Sales are credited to the store that sent the item."); }
    else if (metric === "orders") { metric = "units_sold"; notes.push("Orders cannot be split by store or category, so units sold are shown."); }
    else return {error: `${m.label} cannot be split by ${sp.by}. Try item sales, units sold or items listed.`};
    m = AM[metric];
  }
  const res = {metric, m, from, to, notes, items: null, series: null, total: null};
  if (m.src === "daily") {
    if (f.store || f.category) notes.push("Store and category filters apply to item measures, so they were ignored.");
    const rows = ctx.rows.filter(r => r.date >= from && r.date <= to && (!f.channel || r.channel === f.channel));
    const val = rs => metric === "avg_order" ? (rs.reduce((a, r) => a + r.orders, 0) ? rs.reduce((a, r) => a + r.item_sales, 0) / rs.reduce((a, r) => a + r.orders, 0) : 0) : rs.reduce((a, r) => a + r[metric], 0);
    if (sp.by === "time") {
      const keys = bucketKeys(from, to, sp.grain), mk = (rs, key) => keys.map(k => ({label: GRAINS[sp.grain].label(k), value: val(rs.filter(r => GRAINS[sp.grain].key(r.date) === k))}));
      res.series = sp.split === "channel" ? CHORDER.map(c => ({label: CHN[c], color: CH[c], points: mk(rows.filter(r => r.channel === c))})) : [{label: m.label, color: "var(--accent)", points: mk(rows)}];
      res.total = val(rows);
    } else if (sp.by === "channel") res.items = CHORDER.map(c => ({label: CHN[c], value: val(rows.filter(r => r.channel === c))}));
    else res.total = val(rows);
  } else {
    if (f.channel) notes.push("Item pipeline data is not split by marketplace, so the marketplace filter was ignored.");
    const m0 = from.slice(0, 7), m1 = to.slice(0, 7), rows = ctx.pipe.filter(r => r.month >= m0 && r.month <= m1 && (!f.store || r.store === f.store) && (!f.category || r.category === f.category));
    if (from.slice(8) !== "01" || to !== monthEnd(monthStart(to))) notes.push("Item data is monthly, so whole months are used.");
    const val = rs => rs.reduce((a, r) => a + r[m.col], 0);
    if (sp.by === "store") res.items = Object.entries(ctx.stores).filter(([id]) => !f.store || id === f.store).map(([id, n]) => ({label: n, value: val(rows.filter(r => r.store === id))})).filter(x => x.label !== "Unattributed" || x.value);
    else if (sp.by === "category") res.items = ctx.categories.filter(c => !f.category || c === f.category).map(c => ({label: c, value: val(rows.filter(r => r.category === c))}));
    else if (sp.by === "channel") return {error: "Item pipeline data is not split by marketplace. Group by store or category instead."};
    else if (sp.by === "time") {
      const g = sp.grain === "quarter" ? "quarter" : "month"; if (sp.grain === "day" || sp.grain === "week") notes.push("Item data is monthly, so months are shown.");
      const keys = bucketKeys(monthStart(from), to, g);
      res.series = [{label: m.label, color: "var(--accent)", points: keys.map(k => ({label: GRAINS[g].label(k), value: val(rows.filter(r => GRAINS[g].key(r.month + "-01") === k))}))}];
    }
    res.total = val(rows);
  }
  if (res.items) { res.items.sort((a, b) => sp.order === "asc" ? a.value - b.value || a.label.localeCompare(b.label) : b.value - a.value || a.label.localeCompare(b.label)); res.all = res.items; res.items = res.items.slice(0, sp.limit); }
  return res;
}

const periodText = (a, b) => a === b ? longDay(a) : `${shortDay(a)}${a.slice(0, 4) !== b.slice(0, 4) ? ", " + a.slice(0, 4) : ""} to ${shortDay(b)}, ${b.slice(0, 4)}`;
function treeHTML(q, sp, res, engine, ctx) {
  const f = sp.filters, fl = [f.channel && "marketplace = " + CHN[f.channel], f.store && "store = " + ctx.stores[f.store], f.category && "category = " + f.category].filter(Boolean);
  const m = res.m || AM[sp.metric];
  const li = (k, v) => `<li><span class="muted">${k}</span> ${esc(v)}</li>`;
  return `<div class="tree"><div class="tq">“${esc(q)}”</div><ul>${li("Measure", m.label)}${li("Grouped by", sp.by === "none" ? "nothing (one total)" : sp.by === "time" ? `time, by ${sp.grain}${sp.split ? ", split by marketplace" : ""}` : sp.by)}${li("Filters", fl.length ? fl.join(", ") : "none")}${li("Period", periodText(res.from || sp.period.from, res.to || sp.period.to))}${li("Chart", sp.chart + (sp.by !== "time" && sp.by !== "none" ? (sp.order === "asc" ? ", lowest first" : ", highest first") : ""))}</ul>
  <div class="muted" style="font-size:12px">Parsed by ${engine === "claude" ? "Claude" : "built-in keyword rules"}. Numbers always come from the stored data, never from the model.</div></div>`;
}
function resultHTML(sp, res) {
  const m = res.m, F = v => fmt(m.unit, v);
  const head = `<h3 style="font:700 16px var(--display);margin:0 0 4px">${esc(m.label)}${sp.by !== "none" && sp.by !== "time" ? ` by ${esc(sp.by)}` : ""}</h3><p class="muted" style="margin:0 0 12px;font-size:13px">${esc(periodText(res.from, res.to))}</p>`;
  const notes = res.notes.map(n => `<p class="muted" style="margin:8px 0 0;font-size:12.5px">${esc(n)}</p>`).join("");
  if (res.series && sp.chart !== "table") return head + `<div class="chartwrap">${lineChart(res.series, m.unit === "usd" ? "usd" : "n")}</div>` + notes;
  if (res.series) return head + `<div class="scroll"><table><thead><tr><th>Period</th>${res.series.map(s => `<th class="r">${esc(s.label)}</th>`).join("")}</tr></thead><tbody>${res.series[0].points.map((p, i) => `<tr><td>${esc(p.label)}</td>${res.series.map(s => `<td class="r num">${F(s.points[i].value)}</td>`).join("")}</tr>`).join("")}</tbody></table></div>` + notes;
  if (res.items) {
    const max = Math.max(...res.items.map(i => i.value), 1), zeros = res.all.filter(i => i.value === 0).length;
    const sum = zeros ? `<p style="margin:0 0 10px">${pill("p-warn", `${zeros} of ${res.all.length} ${zeros === 1 ? "has" : "have"} none`)}</p>` : "";
    if (sp.chart === "table") return head + sum + `<div class="scroll"><table><thead><tr><th>${esc(sp.by)}</th><th class="r">${esc(m.label)}</th></tr></thead><tbody>${res.items.map(i => `<tr><td>${esc(i.label)}</td><td class="r num">${F(i.value)}</td></tr>`).join("")}</tbody></table></div>` + notes;
    return head + sum + `<div class="bars">${res.items.map(i => `<div class="brow"><span class="bl">${esc(i.label)}</span><div class="bartrack"><i style="width:${(i.value / max * 100).toFixed(1)}%"></i></div><span class="bv num ${i.value === 0 ? "down" : ""}">${F(i.value)}</span></div>`).join("")}</div>` + notes;
  }
  return head + `<div class="kpi" style="padding:0"><span class="v" style="font-size:40px">${F(res.total)}</span></div>` + notes;
}

const EXAMPLES = ["Which stores listed the fewest books last month?", "Weekly item sales by marketplace for the last 12 weeks", "Top 10 stores by sales last month", "How much shipping did we charge on the latest day?", "Refunds by marketplace this month"];
async function viewAsk(root) {
  const ctx = await askContext();
  if (!state.q) state.q = EXAMPLES[0];
  root.innerHTML = `
  <section><h2>Ask</h2><p class="lead">Type a question about sales, orders, shipping, or what stores sent and listed. The question is turned into a plain spec (measure, grouping, filters, period, chart), and the chart is drawn from the stored data. Try the first example: it is the books question from the ops manager interview.</p>
    <form id="askform" class="bar" style="align-items:stretch"><label class="f" style="flex:1 1 320px">Your question<input type="text" id="q" value="${esc(state.q)}" autocomplete="off" style="font:500 15px var(--body);padding:10px 12px;border:1px solid var(--line);border-radius:6px;background:var(--surface);color:var(--ink)"></label>
      <button class="seg-btn" type="submit" id="askbtn" style="align-self:end;padding:11px 18px">Ask</button></form>
    <div class="legend" style="margin-top:10px">${EXAMPLES.map((e, i) => `<button type="button" class="chip" data-ex="${i}">${esc(e)}</button>`).join("")}</div></section>
  <section id="answer"></section>`;
  const run = async () => {
    const q = $("#q", root).value.trim(); if (!q) return; state.q = q;
    const box = $("#answer", root); box.innerHTML = `<div class="empty">Reading your question…</div>`;
    let sp, engine = "keywords", why = "";
    try {
      const sample = window.claude?.use ? await window.claude.use("sample") : null;
      if (sample) {
        const prompt = `Translate a question about Goodwill e-commerce data into one JSON spec. Reply with JSON only.
Latest data day: ${ctx.last} (a ${["Mon","Tue","Wed","Thu","Fri","Sat","Sun"][dow(ctx.last)]}). Earliest: ${ctx.first}. Weeks start Monday.
metric: ${Object.entries(AM).map(([k, v]) => `${k} (${v.label}${v.src === "pipe" ? ", only by store/category/time" : ""})`).join("; ")}.
by: time | channel | store | category | none. grain (only when by=time): day | week | month | quarter. split: "channel" or null (only when by=time).
filters: channel in ${Object.keys(CHN).join("|")}; category in ${ctx.categories.join("|")}; store is one of ${Object.entries(ctx.stores).filter(([id]) => id).map(([, n]) => n).join("; ")}. Use null for none.
period: {"from":"YYYY-MM-DD","to":"YYYY-MM-DD"} inclusive. "last month" means the previous calendar month; item measures (store_revenue, units_sold, items_*) use whole months.
order: "desc" or "asc" (asc for fewest/lowest/zero). limit: number of rows. chart: line | bar | stat | table.
Shape: {"metric":"","by":"","grain":"day","split":null,"filters":{"channel":null,"store":null,"category":null},"period":{"from":"","to":""},"order":"desc","limit":10,"chart":"bar"}
Question: ${q}`;
        sp = cleanSpec(await sample.json(prompt, {modelTier: "quick"}), ctx); engine = "claude";
      }
    } catch (e) { why = e && e.message ? e.message : ""; sp = null; }
    try {
      let unmatched = false;
      if (!sp) { const ks = keywordSpec(q, ctx); unmatched = !!ks._unmatched; sp = cleanSpec(ks, ctx); }
      const res = execSpec(sp, ctx);
      if (unmatched && !res.error) res.notes.unshift("I did not recognise a measure, grouping or period in that question, so this is item sales for the last 30 days. Try one of the examples above.");
      if (res.error) { box.innerHTML = `<div class="banner">${esc(res.error)}</div>`; return; }
      box.innerHTML = `<div class="grid g2" style="align-items:start"><div class="card pad" style="min-width:0">${resultHTML(sp, res)}</div><div class="card pad" style="min-width:0">${treeHTML(q, sp, res, engine, ctx)}</div></div>`;
    } catch (e) { box.innerHTML = `<div class="banner bad">${esc(e.message)}</div>`; }
  };
  $("#askform", root).onsubmit = e => { e.preventDefault(); run(); };
  root.querySelectorAll("[data-ex]").forEach(b => b.onclick = () => { $("#q", root).value = EXAMPLES[+b.dataset.ex]; run(); });
  run();
}
registerTab("ask", "Ask", viewAsk, 30);
