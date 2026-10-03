/* ---------------- Daily Pulse ---------------- */
async function viewPulse(root) {
  const [all, rows, q] = await Promise.all([read("pulse/all"), dailyRows(), read("quality/latest")]);
  const dates = Object.keys(all).sort().reverse();
  if (!state.date || !all[state.date]) state.date = dates[0];
  const p = all[state.date];
  const last = rows.reduce((a, r) => r.date > a ? r.date : a, "");
  const PR = presets(last);
  if (!state.rng) state.rng = {preset: "latest", from: last, to: last};
  if (state.rng.preset !== "custom") Object.assign(state.rng, {from: PR[state.rng.preset].from, to: PR[state.rng.preset].to});
  const {from, to} = state.rng, span = (D(to) - D(from)) / MS + 1;
  const cur = totalsFor(rows, from, to), prior = totalsFor(rows, addDays(from, -7), addDays(to, -7));
  const tot = o => Object.fromEntries(MEAS.map(m => [m, CHORDER.reduce((a, c) => a + o[c][m], 0)]));
  const T = tot(cur), TP = tot(prior);
  const wow = (a, b) => b ? (a - b) / b : null;
  const dcell = (a, b) => { const r = wow(a, b); return r == null ? `<span class="flat">n/a</span>` : `<span class="${Math.abs(r) < .005 ? "flat" : r > 0 ? "up" : "down"}">${r >= 0 ? "▲" : "▼"} ${nf(Math.abs(r) * 100, 1)}%</span>`; };
  const row = (r, cls = "") => `<tr class="${cls}"><td>${esc(r.label ?? "Total e-commerce")}</td><td class="r num">${usd(r.revenue)}</td><td class="r num">${dcell(r.revenue, r.revenue_last_week)}</td><td class="r num">${nf(r.customers)}</td><td class="r num">${dcell(r.customers, r.customers_last_week)}</td><td class="r num">${usd(r.revenue_mtd)}</td></tr>`;
  const srcs = (p.sources || []).map(s => `<div class="card step"><b>${esc(s.label)}</b>${pill(s.status === "complete" && !s.has_errors ? "p-ok" : "p-warn", s.status === "complete" ? "Complete" : s.status)}<small>${s.files.length} file${s.files.length === 1 ? "" : "s"}${s.latest_order_at ? " · latest order " + new Date(s.latest_order_at).toLocaleString("en-US", {month: "short", day: "numeric", hour: "numeric", minute: "2-digit"}) : ""}</small></div>`).join("");
  const text = reportText(cur, prior, from, to, trustLine(q));
  root.innerHTML = `
  <section><h2>Nightly report</h2><p class="lead">The totals staff add up by hand from the Upright and Cash Monkey downloads: item sales, shipping and order count, by marketplace. Pick a period, including the Monday catch-up for Friday through Sunday. The same report is emailed each night by <span class="mono">python -m goodwill_pulse.digest --send</span>.</p>
    <div class="bar"><div class="seg" role="group" aria-label="Report period">${Object.entries(PR).map(([k, v]) => `<button data-preset="${k}" aria-pressed="${state.rng.preset === k}">${esc(v.label)}</button>`).join("")}</div>
      <label class="f">From<input type="date" id="rfrom" value="${from}" min="${rows[0].date}" max="${last}"></label>
      <label class="f">To<input type="date" id="rto" value="${to}" min="${rows[0].date}" max="${last}"></label></div>
    <div class="card pad" data-explain="report:${from}:${to}" style="margin-top:12px;display:flex;flex-wrap:wrap;gap:6px 28px;align-items:baseline"><span class="kpi"><span class="l">Item sales</span><span class="v">${usd(T.item_sales)}</span></span>
      <span class="kpi"><span class="l">Orders</span><span class="v">${nf(T.orders)}</span></span><span class="kpi"><span class="l">Shipping charged</span><span class="v">${usd(T.shipping)}</span></span>
      <span class="muted" style="font-size:13px">${from === to ? longDay(from) : `${longDay(from)} to ${longDay(to)} (${span} days)`} · ${dcell(T.item_sales, TP.item_sales)} vs ${from === to ? "same weekday last week" : "a week earlier"}</span></div>
    <div class="card scroll" style="margin-top:12px"><table><thead><tr><th>Marketplace</th><th class="r">Orders</th><th class="r">Item sales</th><th class="r">Shipping</th><th class="r">Fees</th><th class="r">Refunds</th><th class="r">vs a week earlier</th></tr></thead><tbody>
      ${CHORDER.map(c => `<tr><td>${CHN[c]}</td><td class="r num">${nf(cur[c].orders)}</td><td class="r num">${usd(cur[c].item_sales, 2)}</td><td class="r num">${usd(cur[c].shipping, 2)}</td><td class="r num">${usd(cur[c].fees, 2)}</td><td class="r num">${usd(cur[c].refunds, 2)}</td><td class="r num">${dcell(cur[c].item_sales, prior[c].item_sales)}</td></tr>`).join("")}
      <tr class="total"><td>Total</td><td class="r num">${nf(T.orders)}</td><td class="r num">${usd(T.item_sales, 2)}</td><td class="r num">${usd(T.shipping, 2)}</td><td class="r num">${usd(T.fees, 2)}</td><td class="r num">${usd(T.refunds, 2)}</td><td class="r num">${dcell(T.item_sales, TP.item_sales)}</td></tr></tbody></table></div>
    <p style="margin:10px 0 0">${pill(q.blocking ? "p-bad" : "p-ok", q.blocking ? "Data checks failing" : "Data checks passing")} <span class="muted" style="font-size:13px">${esc(trustLine(q))} <a href="#quality" data-goto="quality">See the checks</a></span></p>
    <details style="border:1px solid var(--line);border-radius:8px;margin-top:12px;background:var(--surface)"><summary><b>Email text</b><span class="muted">Paste into Outlook or Teams</span></summary><div style="padding:0 12px 12px">
      <textarea id="rtext" readonly rows="14" style="width:100%;font:12.5px/1.5 var(--mono);background:var(--bg);color:var(--ink);border:1px solid var(--line);border-radius:6px;padding:10px;resize:vertical">${esc(text)}</textarea>
      <div class="bar" style="margin-top:8px"><button class="seg-btn" id="rcopy">Copy report text</button><span class="muted" id="rcopied" style="font-size:13px"></span></div></div></details></section>
  <section><div class="bar"><div style="flex:1 1 280px"><h2>Daily Pulse</h2><p class="lead" style="margin:0">Revenue and customers by marketplace for one business day (Eastern time), as built from the two report files. It agrees with the warehouse totals above.</p></div>
    <label class="f">Business day<select id="date">${dates.map(d => `<option value="${d}">${longDay(d)}</option>`).join("")}</select></label></div></section>
  <section><div class="card scroll"><table><thead><tr><th>Marketplace</th><th class="r">Revenue</th><th class="r">vs last week</th><th class="r">Customers</th><th class="r">vs last week</th><th class="r">Month to date</th></tr></thead>
    <tbody>${p.rows.map(r => row(r)).join("")}${row({...p.total, label: "Total e-commerce"}, "total")}</tbody></table></div>
    <p class="muted" style="margin:8px 0 0;font-size:12.5px">Revenue is item subtotal on paid orders. Customers are paid orders. ${p.complete ? "All source reports for the day are in." : "Some source reports for the day are still missing."}${p.open_exceptions ? ` ${p.open_exceptions} open exception${p.open_exceptions > 1 ? "s" : ""}.` : ""}</p></section>
  <section><h2>Where the numbers came from</h2><p class="lead">Each report is checked for coverage before the day is called complete.</p><div class="steps">${srcs}</div></section>`;
  $("#date", root).value = state.date;
  $("#date", root).onchange = e => { state.date = e.target.value; render(); };
  root.querySelectorAll("[data-preset]").forEach(b => b.onclick = () => { state.rng = {preset: b.dataset.preset}; render(); });
  const custom = () => { const f = $("#rfrom", root).value, t = $("#rto", root).value; if (f && t && f <= t) { state.rng = {preset: "custom", from: f, to: t}; render(); } };
  $("#rfrom", root).onchange = custom; $("#rto", root).onchange = custom;
  $("#rcopy", root).onclick = async () => { const ta = $("#rtext", root), msg = $("#rcopied", root);
    try { await navigator.clipboard.writeText(ta.value); msg.textContent = "Copied."; } catch (e) { ta.select(); msg.textContent = "Select the text and copy it."; } };
  const g = root.querySelector("[data-goto]"); if (g) g.onclick = e => { e.preventDefault(); setTab("quality"); };
}
registerTab("pulse", "Daily Pulse", viewPulse, 10);
