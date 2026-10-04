/* ---------------- Dashboard (Hub top tab "dashboard"): anchors, 15-KPI scorecard, trends, stores, categories, pillars ----------------
   Every number is read from stored documents (month/*, series/all, daily/all, storecat/all, storeday/*) and added up here. */
const DASH_PN = {growth: "Growth", profitability: "Profitability", productivity: "Productivity", inventory: "Inventory", engagement: "Engagement"};
let DASH_CHARTS = [];
function dashBuckets(from, to, grain) { const out = []; for (let d = from; d <= to; d = addDays(d, 1)) { const k = GRAINS[grain].key(d); if (out[out.length - 1] !== k) out.push(k); } return out; }

/* change vs a base value, as a Hub delta pill (computation is core's delta()) */
const dashDelta = (c, key) => delta(c, key).replace('<span class="', '<span class="delta ');
/* 13-month spark: green when the move since the first point is in the better direction */
function dashSpark(points, better) {
  const pts = (points || []).filter(p => p.value != null);
  if (pts.length < 2) return "";
  const w = 200, h = 44, lo = Math.min(...pts.map(p => p.value)), hi = Math.max(...pts.map(p => p.value)), rng = hi - lo || 1;
  const xy = pts.map((p, i) => `${(i / (pts.length - 1) * w).toFixed(1)},${(h - 5 - (p.value - lo) / rng * (h - 10)).toFixed(1)}`).join(" ");
  const up = pts[pts.length - 1].value >= pts[0].value, col = better === "up" ? (up ? "var(--good)" : "var(--bad)") : better === "down" ? (up ? "var(--bad)" : "var(--good)") : "var(--blue)";
  return `<svg class="db-spark" viewBox="0 0 ${w} ${h}" preserveAspectRatio="none" aria-hidden="true"><polyline points="${xy}" fill="none" stroke="${col}" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round" vector-effect="non-scaling-stroke"/></svg>`;
}
const dashAvail = a => a && a !== "built" ? `<span class="db-av ${a === "input" ? "in" : "na"}">${a === "input" ? "Manual input" : "Not yet available"}</span>` : "";

/* Shared KPI tile (also used by the Ask tab). card = a month doc card; month = "YYYY-MM" for the explain hook. */
function dashKpiTile(c, anchor, month) {
  if (!c) return "";
  return `<div class="card kpi db-tile ${anchor ? "anchor" : ""}" data-kpi="${esc(c.id)}"><span class="l">${esc(c.label)}${c.provisional ? ` <span class="db-av in">Provisional</span>` : ""}</span><span class="v num">${fmt(c.unit, c.value)}</span>
    <span class="d" data-explain="kpi:${esc(c.id)}:${esc(month || state.month)}"><span class="db-dp">${dashDelta(c, "prior_month")}<small>vs last month</small></span><span class="db-dp">${dashDelta(c, "prior_year")}<small>vs last year</small></span></span>${dashSpark(c.trend, c.better)}</div>`;
}

/* ---- charts: Chart.js through hubChart when it exists, else the SVG line chart ---- */
const dashCss = v => { const m = /var\((--[\w-]+)\)/.exec(v || ""); if (!m) return v; try { return getComputedStyle(document.documentElement).getPropertyValue(m[1]).trim() || "#0053A0"; } catch (e) { return "#0053A0"; } };
const dashTick = unit => v => unit === "usd" ? (Math.abs(v) >= 1000 ? "$" + nf(v / 1000, Math.abs(v) % 1000 ? 1 : 0) + "k" : "$" + nf(v)) : unit === "pct" ? nf(v * 100) + "%" : nf(v, Math.abs(v) < 10 && v % 1 ? 1 : 0);
const dashVal = unit => v => unit === "usd" ? usd(v, Math.abs(v) < 1000 ? 2 : 0) : unit === "pct" ? pct(v) : unit === "n" ? nf(v) : fmt(unit, v);
function dashChartBox(id, series, unit, kind) {
  if (typeof hubChart === "function") return `<div class="chartbox db-chart" data-chart="${esc(id)}"><canvas id="${esc(id)}" role="img" aria-label="Chart"></canvas></div>`;
  return `<div class="chartwrap db-chart">${lineChart(series, unit)}</div>`;
}
function dashDraw(root, specs) {
  for (const c of DASH_CHARTS) { try { c && c.destroy && c.destroy(); } catch (e) {} }
  DASH_CHARTS = [];
  if (typeof hubChart !== "function") return;
  let tries = 0;
  const go = () => {
    const first = specs.length && root.querySelector("#" + specs[0].id);
    if (!first) return;
    if (!first.isConnected) { if (++tries < 200) setTimeout(go, 25); return; }
    for (const s of specs) {
      const cv = root.querySelector("#" + s.id); if (!cv) continue;
      const n = s.series[0] ? s.series[0].points.length : 0, bar = s.kind === "bar";
      const cfg = () => ({type: bar ? "bar" : "line",
        data: {labels: (s.series[0] ? s.series[0].points : []).map(p => p.label ?? monthShort(p.month)),
          datasets: s.series.map(x => { const col = dashCss(x.color); return {label: x.label, data: x.points.map(p => p.value), borderColor: col, backgroundColor: bar ? col : col, tension: .3,
            pointRadius: n <= 31 ? 2 : 0, borderWidth: bar ? 0 : 2, spanGaps: true, stack: bar ? "s" : undefined, borderRadius: bar ? 4 : 0}; })},
        options: {responsive: true, maintainAspectRatio: false, interaction: {mode: "index", intersect: false},
          plugins: {legend: {display: s.series.length > 1, position: "bottom", labels: {boxWidth: 10, boxHeight: 10}},
            tooltip: {callbacks: {label: ctx => `${ctx.dataset.label}: ${dashVal(s.unit)(ctx.parsed.y)}`,
              footer: items => bar && items.length > 1 ? "Total: " + dashVal(s.unit)(items.reduce((a, i) => a + (i.parsed.y || 0), 0)) : ""}}},
          scales: {x: {stacked: bar, grid: {display: false}, ticks: {maxTicksLimit: 8}}, y: {stacked: bar, grid: {color: dashCss("var(--line)")}, ticks: {callback: dashTick(s.unit)}}}}});
      let ch = null; try { ch = hubChart(cv, cfg); } catch (e) { ch = null; }
      if (ch) DASH_CHARTS.push(ch);
      else { const box = cv.closest(".db-chart"); if (box) { box.className = "chartwrap db-chart"; box.innerHTML = lineChart(s.series, s.unit); } }
    }
  };
  setTimeout(go, 0);
}

/* store-level daily rows (storeday docs via hubStoreDays), or null when not available */
async function dashStoreRows(from, to, store) {
  if (typeof hubStoreDays !== "function") return null;
  try { const rs = await hubStoreDays(from, to); return Array.isArray(rs) ? rs.filter(r => r.store === store.store_id || r.store === store.store_name) : null; } catch (e) { return null; }
}
const dashMath = items => `<details class="db-math"><summary>Show the math</summary><ul class="math">${items.filter(Boolean).map(([b, s]) => `<li><b>${esc(b)}</b><span>${s}</span></li>`).join("")}</ul></details>`;

async function viewDashboard(root) {
  const fc = state.focus; state.focus = null;
  if (fc && fc.month) state.month = fc.month;
  const opt = await read("site/options");
  if (!state.month || !opt.months.includes(state.month)) state.month = opt.default_month;
  const [m, ser, drows] = await Promise.all([read("month/" + state.month), read("series/all"), dailyRows()]);
  state.tg = state.tg || "week"; state.tm = state.tm || "item_sales";
  if (state.dChan && !CHN[state.dChan]) state.dChan = "";
  const stores = m.stores.stores, storeOpts = (opt.stores && opt.stores.length ? opt.stores : stores);
  const store = state.dStore ? storeOpts.find(s => s.store_id === state.dStore) || null : null;
  if (!store) state.dStore = "";
  const chans = state.dChan ? [state.dChan] : CHORDER;

  /* sales over time: daily rows (uploads applied), or the selected store's order lines */
  const dlast = drows.reduce((a, r) => a > r.date ? a : r.date, ""), dfirst = drows.reduce((a, r) => !a || r.date < a ? r.date : a, "");
  const win0 = {day: addDays(dlast, -44), week: addDays(weekStart(dlast), -25 * 7), month: dfirst, quarter: dfirst}[state.tg];
  const win = win0 < dfirst ? dfirst : win0;
  const tkeys = dashBuckets(win, dlast, state.tg);
  let srows = drows, storeNote = "";
  if (store) { const rs = await dashStoreRows(win, dlast, store); if (rs) srows = rs; else storeNote = "Store detail by day is not stored yet, so this chart shows all stores."; }
  const tval = rs => state.tm === "avg_order" ? (rs.reduce((a, r) => a + r.orders, 0) ? rs.reduce((a, r) => a + r.item_sales, 0) / rs.reduce((a, r) => a + r.orders, 0) : 0) : rs.reduce((a, r) => a + (r[state.tm] || 0), 0);
  const tseries = chans.map(c => { const rs = srows.filter(r => r.channel === c && r.date >= win); return {label: CHN[c], color: CH[c], points: tkeys.map(k => ({label: GRAINS[state.tg].label(k), value: tval(rs.filter(r => GRAINS[state.tg].key(r.date) === k))}))}; });
  const tunit = state.tm === "orders" ? "n" : "usd";

  /* trend by marketplace from series/all */
  if (!(state.kpi in ser) || !ser[state.kpi]) state.kpi = "total_revenue";
  const sk = ser[state.kpi];
  const kseries = sk.series.filter(s => !state.dChan || s.key === state.dChan).map(s => ({label: s.label, color: CH[s.key] || "var(--c1)", points: s.points}));
  const kpiOpts = Object.keys(ser).filter(k => ser[k]).map(k => `<option value="${esc(k)}">${esc(ser[k].label)}</option>`).join("");

  const C = m.cards, kpi = (id, anchor) => dashKpiTile(C[id], anchor, state.month);
  const optKpi = Object.fromEntries(opt.kpis.map(k => [k.id, k]));
  const av = opt.availability || {};
  const pillars = Object.entries(m.pillars).map(([p, ids]) => `<details data-pillar="${esc(p)}"><summary><b>${esc(DASH_PN[p] || p)}</b><span class="muted db-n">${ids.length} measures</span></summary>
    <div class="tbl-wrap"><table><thead><tr><th>Measure</th><th>${esc(monthShort(state.month))}</th><th>vs last month</th><th>vs last year</th><th></th></tr></thead><tbody>${ids.map(id => { const c = C[id]; if (!c) return ""; return `<tr data-kpi="${esc(id)}"><td>${esc(c.label)}</td><td class="num">${fmt(c.unit, c.value)}</td><td class="num">${dashDelta(c, "prior_month")}</td><td class="num">${dashDelta(c, "prior_year")}</td><td>${dashAvail(optKpi[id] && optKpi[id].availability)}</td></tr>`; }).join("")}</tbody></table></div></details>`).join("");

  const maxRev = Math.max(...stores.map(s => s.revenue), 1), totRev = stores.reduce((a, s) => a + s.revenue, 0);
  /* categories: the month doc (all stores), or the store's pipeline rows from storecat/all */
  let catHTML, catHint;
  if (!store) {
    const cats = m.categories[state.catBy].categories;
    catHint = `Top ${cats.length} by ${state.catBy}, ${monthLabel(state.month)}.`;
    catHTML = `<table id="db-cats"><thead><tr><th>Category</th><th>Revenue</th><th>Units</th><th>Avg price</th><th>Fees</th><th>Gross profit</th><th>Margin</th></tr></thead><tbody>
    ${cats.map(c => `<tr><td>${esc(c.category)}</td><td class="num">${usd(c.revenue)}</td><td class="num">${nf(c.units)}</td><td class="num">${usd(c.asp, 2)}</td><td class="num">${usd(c.fees)}</td><td class="num">${usd(c.gross_profit)}</td><td class="num">${pct(c.margin_pct)}</td></tr>`).join("")}</tbody></table>`;
  } else {
    const sc = await read("storecat/all"), ix = Object.fromEntries(sc.cols.map((c, i) => [c, i])), by = {};
    for (const r of sc.rows) if (r[ix.month] === state.month && r[ix.store] === store.store_id) { const k = r[ix.category], o = by[k] || (by[k] = {revenue: 0, sold: 0, identified: 0, sent: 0, listed: 0}); for (const f of ["revenue", "sold", "identified", "sent", "listed"]) o[f] += r[ix[f]] || 0; }
    const rows = Object.entries(by).sort((a, b) => b[1].revenue - a[1].revenue), t = rows.reduce((a, [, v]) => { for (const k in v) a[k] = (a[k] || 0) + v[k]; return a; }, {});
    catHint = `${store.store_name}, ${monthLabel(state.month)}. Fees and margin are kept for all stores only.`;
    catHTML = rows.length ? `<table id="db-cats"><thead><tr><th>Category</th><th>Revenue</th><th>Units sold</th><th>Avg price</th><th>Identified</th><th>Sent</th><th>Listed</th></tr></thead><tbody>
    ${rows.map(([k, v]) => `<tr><td>${esc(k)}</td><td class="num">${usd(v.revenue)}</td><td class="num">${nf(v.sold)}</td><td class="num">${v.sold ? usd(v.revenue / v.sold, 2) : "n/a"}</td><td class="num">${nf(v.identified)}</td><td class="num">${nf(v.sent)}</td><td class="num">${nf(v.listed)}</td></tr>`).join("")}
    <tr class="sum"><td>All categories</td><td class="num">${usd(t.revenue)}</td><td class="num">${nf(t.sold)}</td><td class="num">${t.sold ? usd(t.revenue / t.sold, 2) : "n/a"}</td><td class="num">${nf(t.identified)}</td><td class="num">${nf(t.sent)}</td><td class="num">${nf(t.listed)}</td></tr></tbody></table>`
      : `<div class="empty">No category activity for ${esc(store.store_name)} in ${esc(monthLabel(state.month))}.</div>`;
  }
  const chanChips = `<div class="chtabs db-chans" role="group" aria-label="Marketplace"><button class="chtab" data-dchan="" aria-pressed="${!state.dChan}">All marketplaces</button>${CHORDER.map(c => `<button class="chtab" data-dchan="${c}" aria-pressed="${state.dChan === c}"><span class="sw" style="background:${CH[c]}"></span>${esc(CHN[c])}</button>`).join("")}</div>`;
  const grainL = {day: "day", week: "week", month: "month", quarter: "quarter"}[state.tg];

  root.innerHTML = `<div class="db">
  <div class="rhead"><div><h1>Dashboard</h1><p class="sub">${esc(monthLabel(state.month))}${state.month === opt.months[0] ? " (in progress)" : ""}. Data through ${esc(shortDay(opt.data_through))}.</p></div>
    <div class="controls"><label class="db-lab">Month<select id="month" class="pill">${opt.months.map(x => `<option value="${esc(x)}">${monthLabel(x)}${x === opt.months[0] ? " (in progress)" : ""}</option>`).join("")}</select></label>
    <label class="db-lab">Store<select id="db-store" class="pill"><option value="">All stores</option>${storeOpts.map(s => `<option value="${esc(s.store_id)}">${esc(s.store_name)}</option>`).join("")}</select></label></div></div>

  <div class="db-anchors">${m.anchors.map(id => kpi(id, true)).join("")}</div>

  <section class="card db-card" id="db-scorecard"><h2>Scorecard</h2><p class="hint">15 measures, company-wide. Lines show 13 months.</p>
    ${m.scorecard_rows.map(r => `<div class="rowlab">${esc(r.label)}</div><div class="db-grid">${r.ids.map(id => kpi(id)).join("")}</div>`).join("")}
    ${dashMath([["Arrows", "This month against last month and the same month last year. Percent change, or points for rates."], ["Color", "Green when the move is in the better direction for that measure, red when not."], ["Line", "Thirteen monthly values ending " + esc(monthLabel(state.month)) + ". Green or red by the same rule."],
      av.input && ["Manual input", esc(av.input.detail)], av["n/a"] && ["Not yet available", esc(av["n/a"].detail)], ["Check a tile", "Open How we got this under any tile for the formula, query and an independent recompute."]])}</section>

  <section class="db-sec">${chanChips}
  <div class="card db-card"><div class="db-head"><div><h2>Sales over time</h2><p class="hint">${store ? esc(store.store_name) + ", " : ""}by ${grainL}${state.dChan ? ", " + esc(CHN[state.dChan]) : ""}.${storeNote ? " " + esc(storeNote) : ""}</p></div>
    <div class="controls"><div class="seg" role="group" aria-label="Measure">${[["item_sales", "Item sales"], ["orders", "Orders"], ["shipping", "Shipping"], ["avg_order", "Avg order"]].map(([k, l]) => `<button data-tm="${k}" aria-pressed="${state.tm === k}">${l}</button>`).join("")}</div>
    <div class="seg" role="group" aria-label="Period">${["day", "week", "month", "quarter"].map(g => `<button data-tg="${g}" aria-pressed="${state.tg === g}">${g[0].toUpperCase() + g.slice(1)}</button>`).join("")}</div></div></div>
    ${dashChartBox("db-c-sales", tseries, tunit)}
    ${dashMath([["Source", store && !storeNote ? `Order lines credited to ${esc(store.store_name)}. An order with items from two stores counts in both. Uploaded files are not split by store.` : "Daily warehouse rows, one per Eastern business day and marketplace. A file from the Upload tab replaces the days it fully covers."],
      ["Window", "Day: last 45 days. Week: last 26 weeks. Month and quarter: all data. The first and last period can be partial."], ["Avg order", "Item sales divided by orders in the period."], ["Range", `${esc(shortDay(win))} to ${esc(shortDay(dlast))}, ${esc(dlast.slice(0, 4))}.`]])}</div>

  <div class="card db-card"><div class="db-head"><div><h2>Trend by marketplace</h2><p class="hint">13 months to ${esc(monthLabel(sk.filters.month))}.</p></div>
    <div class="controls"><label class="db-lab">Measure<select id="kpi" class="pill">${kpiOpts}</select></label></div></div>
    ${dashChartBox("db-c-trend", kseries, sk.unit)}
    ${dashMath([["Measure", esc(sk.label) + (C[state.kpi] && C[state.kpi].formula ? ". " + esc(C[state.kpi].formula) : "")], ["Split", "Each line is one marketplace, computed the same way as the scorecard."]])}</div></section>

  <section class="card db-card" id="db-stores"><h2>Stores</h2><p class="hint">${esc(stores.length)} stores, ${esc(monthLabel(state.month))}. Click a store to filter.</p>
    <div class="tbl-wrap"><table><thead><tr><th>Store</th><th>Revenue</th><th>Share</th><th>Units</th><th>Avg price</th><th>Sent</th><th>Listed</th><th>Backlog</th></tr></thead><tbody>
    ${stores.map(s => `<tr data-dstore="${esc(s.store_id)}" tabindex="0" class="${state.dStore === s.store_id ? "hl" : ""}"><td><b>${esc(s.store_name)}</b>${s.ai_flagging ? ` <span class="db-av ai" title="Uses AI flagging">AI</span>` : ""}</td><td class="num"><span class="share" style="width:${(s.revenue / maxRev * 70).toFixed(0)}px"></span>${usd(s.revenue)}</td>
    <td class="num">${pct(s.share)}</td><td class="num">${nf(s.units)}</td><td class="num">${s.asp == null ? "n/a" : usd(s.asp, 2)}</td><td class="num">${nf(s.items_sent)}</td><td class="num">${nf(s.items_listed)}</td><td class="num">${nf(s.backlog_end)}</td></tr>`).join("")}
    <tr class="sum"><td>All stores</td><td class="num">${usd(totRev)}</td><td></td><td class="num">${nf(stores.reduce((a, s) => a + s.units, 0))}</td><td></td><td class="num">${nf(stores.reduce((a, s) => a + s.items_sent, 0))}</td><td class="num">${nf(stores.reduce((a, s) => a + s.items_listed, 0))}</td><td class="num">${nf(stores.reduce((a, s) => a + s.backlog_end, 0))}</td></tr></tbody></table></div>
    ${dashMath([["Revenue", "Item sales credited to the store that sent the item."], ["Share", "Store revenue divided by all stores' revenue for the month."], ["Avg price", "Revenue divided by units sold."], ["Sent, Listed", "Items sent to e-commerce and items listed in the month."], ["Backlog", "Items received but not listed at month end."]])}</section>

  <section class="card db-card" id="db-catcard"><div class="db-head"><div><h2>Categories</h2><p class="hint">${esc(catHint)}</p></div>
    <div class="seg" role="group" aria-label="Rank categories by"><button id="cr" aria-pressed="${state.catBy === "revenue"}">By revenue</button><button id="cm" aria-pressed="${state.catBy === "margin"}" ${store ? "disabled title=\"Margin is kept for all stores only\"" : ""}>By margin</button></div></div>
    <div class="tbl-wrap">${catHTML}</div>
    ${dashMath([["Gross profit", "Revenue minus marketplace fees and refunds."], ["Margin", "Gross profit divided by revenue."], store && ["Store view", "Summed from the month by store by category pipeline rows (storecat/all)."]])}</section>

  <section class="card db-card" id="db-pillars"><h2>All measures by pillar</h2><p class="hint">Every KPI for ${esc(monthLabel(state.month))}.</p>${pillars}</section></div>`;

  if (fc && Array.isArray(fc.ids)) {
    const want = new Set(fc.ids);
    root.querySelectorAll("[data-kpi]").forEach(e => { if (want.has(e.dataset.kpi)) e.classList.add("focus"); });
    const det = [...root.querySelectorAll("details[data-pillar]")].find(d => d.dataset.pillar === fc.pillar);
    if (det) { det.open = true; requestAnimationFrame(() => { try { det.scrollIntoView({block: "start", behavior: "smooth"}); } catch (e) {} }); }
  }
  $("#month", root).value = state.month; $("#kpi", root).value = state.kpi; $("#db-store", root).value = state.dStore || "";
  $("#month", root).onchange = e => { state.month = e.target.value; render(); };
  $("#kpi", root).onchange = e => { state.kpi = e.target.value; render(); };
  $("#db-store", root).onchange = e => { state.dStore = e.target.value; render(); };
  root.querySelectorAll("[data-dchan]").forEach(b => b.onclick = () => { state.dChan = b.dataset.dchan; render(); });
  root.querySelectorAll("[data-tm]").forEach(b => b.onclick = () => { state.tm = b.dataset.tm; render(); });
  root.querySelectorAll("[data-tg]").forEach(b => b.onclick = () => { state.tg = b.dataset.tg; render(); });
  root.querySelectorAll("tr[data-dstore]").forEach(tr => { const go = () => { state.dStore = state.dStore === tr.dataset.dstore ? "" : tr.dataset.dstore; render(); }; tr.onclick = e => { if (!e.target.closest("button,a,.ex-host")) go(); }; tr.onkeydown = e => { if (e.key === "Enter") go(); }; });
  $("#cr", root).onclick = () => { state.catBy = "revenue"; render(); };
  $("#cm", root).onclick = () => { if (!state.dStore) { state.catBy = "margin"; render(); } };
  dashDraw(root, [{id: "db-c-sales", series: tseries, unit: tunit, kind: state.tm === "avg_order" ? "line" : "bar"}, {id: "db-c-trend", series: kseries, unit: sk.unit, kind: "line"}]);
}
registerTab("dashboard", "Dashboard", viewDashboard, 20);
