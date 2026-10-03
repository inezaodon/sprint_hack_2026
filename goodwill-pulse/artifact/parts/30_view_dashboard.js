/* ---------------- Monthly Dashboard ---------------- */
async function viewDashboard(root) {
  const opt = await read("site/options");
  if (!state.month) state.month = opt.default_month;
  const [m, ser, drows] = await Promise.all([read("month/" + state.month), read("series/all"), dailyRows()]);
  state.tg = state.tg || "week"; state.tm = state.tm || "item_sales";
  const dlast = drows.reduce((a, r) => a > r.date ? a : r.date, "");
  const win = {day: addDays(dlast, -44), week: addDays(weekStart(dlast), -25 * 7), month: drows[0].date, quarter: drows[0].date}[state.tg];
  const tkeys = bucketKeys(win < drows[0].date ? drows[0].date : win, dlast, state.tg);
  const tval = rs => state.tm === "avg_order" ? (rs.reduce((a, r) => a + r.orders, 0) ? rs.reduce((a, r) => a + r.item_sales, 0) / rs.reduce((a, r) => a + r.orders, 0) : 0) : rs.reduce((a, r) => a + r[state.tm], 0);
  const tseries = CHORDER.map(c => { const rs = drows.filter(r => r.channel === c); return {label: CHN[c], color: CH[c], points: tkeys.map(k => ({label: GRAINS[state.tg].label(k), value: tval(rs.filter(r => GRAINS[state.tg].key(r.date) === k))}))}; });
  const C = m.cards;
  const kpi = (id, anchor) => { const c = C[id]; if (!c) return ""; return `<div class="card kpi ${anchor ? "anchor" : ""}"><span class="l">${esc(c.label)}</span><span class="v">${fmt(c.unit, c.value)}</span>
    <span class="d"><span class="muted">vs last month</span> ${delta(c, "prior_month")}<span class="muted">vs last year</span> ${delta(c, "prior_year")}</span>${spark(c.trend || [])}</div>`; };
  const sk = ser[state.kpi] || ser.total_revenue;
  const kpiOpts = Object.keys(ser).filter(k => ser[k]).map(k => `<option value="${k}">${esc(ser[k].label)}</option>`).join("");
  const PN = {growth: "Growth", profitability: "Profitability", productivity: "Productivity", inventory: "Inventory", engagement: "Engagement"};
  const avail = c => c.availability && c.availability !== "built" ? pill(c.availability === "input" ? "p-info" : "p-warn", c.availability === "input" ? "Manual input" : "Not yet available") : "";
  const optKpi = Object.fromEntries(opt.kpis.map(k => [k.id, k]));
  const pillars = Object.entries(m.pillars).map(([p, ids]) => `<details><summary><b>${PN[p] || esc(p)}</b><span class="muted">${ids.length} measures</span></summary>
    <div class="scroll"><table><thead><tr><th>Measure</th><th class="r">${monthShort(state.month)}</th><th class="r">vs last month</th><th class="r">vs last year</th><th></th></tr></thead><tbody>${ids.map(id => { const c = C[id]; return `<tr><td>${esc(c.label)}</td><td class="r num">${fmt(c.unit, c.value)}</td><td class="r num">${delta(c, "prior_month")}</td><td class="r num">${delta(c, "prior_year")}</td><td>${avail({availability: optKpi[id]?.availability})}</td></tr>`; }).join("")}</tbody></table></div></details>`).join("");
  const stores = m.stores.stores, maxRev = Math.max(...stores.map(s => s.revenue), 1);
  const cats = m.categories[state.catBy].categories;
  root.innerHTML = `
  <section><div class="bar"><div style="flex:1 1 280px"><h2>Monthly Dashboard</h2><p class="lead" style="margin:0">Three headline measures, the fifteen on the scorecard, then the detail behind them. Data through ${esc(opt.data_through)}.</p></div>
    <label class="f">Month<select id="month">${opt.months.map(x => `<option value="${x}">${monthLabel(x)}${x === opt.months[0] ? " (in progress)" : ""}</option>`).join("")}</select></label></div></section>
  <section><div class="grid g3">${m.anchors.map(id => kpi(id, true)).join("")}</div></section>
  <section><h2>Scorecard</h2><p class="lead">Each tile shows 13 months. Green means the move is in the better direction for that measure.</p>
    ${m.scorecard_rows.map(r => `<div class="rowlab">${esc(r.label)}</div><div class="grid g3">${r.ids.map(id => kpi(id)).join("")}</div>`).join("")}</section>
  <section><div class="bar"><div style="flex:1 1 260px"><h2>Trend by marketplace</h2><p class="lead" style="margin:0">Thirteen months ending ${monthLabel(ser.total_revenue.filters.month)}.</p></div>
    <label class="f">Measure<select id="kpi">${kpiOpts}</select></label></div>
    <div class="card pad chartwrap" style="margin-top:12px">${lineChart(sk.series.map(s => ({label: s.label, color: CH[s.key] || "var(--c1)", points: s.points})), sk.unit)}</div></section>
  <section><div class="bar"><div style="flex:1 1 260px"><h2>Sales over time</h2><p class="lead" style="margin:0">The same warehouse totals by day, week, month or quarter, from the daily orders. The first and last period can be partial.</p></div>
    <div class="seg" role="group" aria-label="Measure">${[["item_sales", "Item sales"], ["orders", "Orders"], ["shipping", "Shipping"], ["avg_order", "Average order"]].map(([k, l]) => `<button data-tm="${k}" aria-pressed="${state.tm === k}">${l}</button>`).join("")}</div>
    <div class="seg" role="group" aria-label="Period">${["day", "week", "month", "quarter"].map(g => `<button data-tg="${g}" aria-pressed="${state.tg === g}">${g[0].toUpperCase() + g.slice(1)}</button>`).join("")}</div></div>
    <div class="card pad chartwrap" style="margin-top:12px">${lineChart(tseries, state.tm === "orders" ? "n" : "usd")}</div></section>
  <section><h2>Stores</h2><p class="lead">Sales are credited to the store that sent the item. ${esc(stores.length)} stores with activity in ${monthLabel(state.month)}.</p>
    <div class="card scroll"><table><thead><tr><th>Store</th><th>Revenue</th><th class="r">Share</th><th class="r">Units</th><th class="r">Avg price</th><th class="r">Sent</th><th class="r">Listed</th><th class="r">Backlog</th></tr></thead><tbody>
    ${stores.map(s => `<tr><td>${esc(s.store_name)}${s.ai_flagging ? ` <span class="pill p-info" title="Uses AI flagging">AI</span>` : ""}</td><td><div style="display:flex;gap:10px;align-items:center"><div class="bartrack"><i style="width:${(s.revenue / maxRev * 100).toFixed(1)}%"></i></div><span class="num">${usd(s.revenue)}</span></div></td>
    <td class="r num">${pct(s.share)}</td><td class="r num">${nf(s.units)}</td><td class="r num">${s.asp == null ? "n/a" : usd(s.asp, 2)}</td><td class="r num">${nf(s.items_sent)}</td><td class="r num">${nf(s.items_listed)}</td><td class="r num">${nf(s.backlog_end)}</td></tr>`).join("")}</tbody></table></div></section>
  <section><div class="bar"><div style="flex:1 1 260px"><h2>Categories</h2><p class="lead" style="margin:0">Margin is sale price minus marketplace fees and refunds.</p></div>
    <div class="seg" role="group" aria-label="Rank categories by"><button id="cr" aria-pressed="${state.catBy === "revenue"}">By revenue</button><button id="cm" aria-pressed="${state.catBy === "margin"}">By margin</button></div></div>
    <div class="card scroll" style="margin-top:12px"><table><thead><tr><th>Category</th><th class="r">Revenue</th><th class="r">Units</th><th class="r">Avg price</th><th class="r">Fees</th><th class="r">Gross profit</th><th class="r">Margin</th></tr></thead><tbody>
    ${cats.map(c => `<tr><td>${esc(c.category)}</td><td class="r num">${usd(c.revenue)}</td><td class="r num">${nf(c.units)}</td><td class="r num">${usd(c.asp, 2)}</td><td class="r num">${usd(c.fees)}</td><td class="r num">${usd(c.gross_profit)}</td><td class="r num">${pct(c.margin_pct)}</td></tr>`).join("")}</tbody></table></div></section>
  <section><h2>All measures by pillar</h2><div class="card">${pillars}</div></section>`;
  $("#month", root).value = state.month; $("#kpi", root).value = state.kpi in ser ? state.kpi : "total_revenue";
  $("#month", root).onchange = e => { state.month = e.target.value; render(); };
  $("#kpi", root).onchange = e => { state.kpi = e.target.value; render(); };
  root.querySelectorAll("[data-tm]").forEach(b => b.onclick = () => { state.tm = b.dataset.tm; render(); });
  root.querySelectorAll("[data-tg]").forEach(b => b.onclick = () => { state.tg = b.dataset.tg; render(); });
  $("#cr", root).onclick = () => { state.catBy = "revenue"; render(); };
  $("#cm", root).onclick = () => { state.catBy = "margin"; render(); };
}
registerTab("dashboard", "Monthly Dashboard", viewDashboard, 20);
