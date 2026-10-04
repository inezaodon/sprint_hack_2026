/* ---------------- Reports (Engineer 3): Home + Report drill-down, absorbing the Daily Pulse ----------------
   Routes: #home, #report/<source>[/<channel|all>[/<store id, "-" = unassigned>]]. state.rng = {preset, from, to} is the
   report period (shared by all sources; presets are re-anchored to each source's latest day). */
const rpDay = s => new Date(D(s)).toLocaleDateString("en-US", {weekday: "short", month: "short", day: "numeric", timeZone: "UTC"});
const rpRange = (f, t) => f === t ? rpDay(f) : `${shortDay(f)}${f.slice(0, 4) !== t.slice(0, 4) ? ", " + f.slice(0, 4) : ""} – ${shortDay(t)}, ${t.slice(0, 4)}`;
const rpSpan = (f, t) => Math.round((D(t) - D(f)) / MS) + 1;
const rpPrior = (f, t) => f === t ? {from: addDays(f, -7), to: addDays(t, -7)} : {from: addDays(f, -rpSpan(f, t)), to: addDays(f, -1)};
const rpVs = (f, t) => f === t ? "vs last " + new Date(D(f)).toLocaleDateString("en-US", {weekday: "short", timeZone: "UTC"}) : `vs prior ${rpSpan(f, t)} days`;
const rpWord = n => n <= 31 ? "night" : n <= 180 ? "week" : "month";
const rpBad = v => v == null || !isFinite(v);
const rp$ = v => rpBad(v) ? "n/a" : usd(v);
const rp$c = v => rpBad(v) ? "n/a" : usd(v, 2);
const rpN = v => rpBad(v) ? "n/a" : nf(v);
const rpRatio = (a, b) => b ? a / b : null;
const rpStoreName = (meta, id) => id === "" || id == null ? "Unassigned" : (meta.stores && meta.stores[id]) || id;
const rpColor = v => { const m = /--[\w-]+/.exec(v || ""); return (m && hubTheme(m[0])) || "#0053A0"; };
const rpFade = c => /^#[0-9a-f]{6}$/i.test(c) ? c + "88" : c;
function rpDelta(a, b, inv) {
  if (rpBad(a) || rpBad(b) || !b) return "";
  const p = (a - b) / Math.abs(b) * 100, good = inv ? p <= 0 : p >= 0;
  return `<span class="delta ${good ? "up" : "down"}">${p >= 0 ? "▲" : "▼"} ${nf(Math.abs(p), 0)}%</span>`;
}
function rpSum(rows, from, to, keys) {   // sums keys over rows with from <= date <= to; a key with any null value becomes null
  const o = Object.fromEntries(keys.map(k => [k, 0]));
  for (const r of rows) if (r.date >= from && r.date <= to) for (const k of keys) { if (o[k] == null) continue; if (r[k] == null) o[k] = null; else o[k] += r[k]; }
  return o;
}
function rpBuckets(from, to) {
  const n = rpSpan(from, to), days = [...Array(n)].map((_, i) => addDays(from, i));
  if (n <= 31) return days.map(d => ({label: rpDay(d), short: shortDay(d), from: d, to: d}));
  const byMonth = n > 180, g = new Map();
  for (const d of days) {
    const k = byMonth ? d.slice(0, 7) : weekStart(d);
    if (!g.has(k)) g.set(k, {label: byMonth ? monthLabel(k) : "Week of " + shortDay(k), short: byMonth ? monthShort(k) : shortDay(k), from: d, to: d});
    g.get(k).to = d;
  }
  return [...g.values()];
}
function rpAfterAttach(el, fn, n = 0) {   // charts need the canvas in the page; the view renders detached
  if (el.isConnected) { try { fn(); } catch (e) { console.error(e); } return; }
  if (n < 200) setTimeout(() => rpAfterAttach(el, fn, n + 1), 16);
}
function rpChecksBadge(c) {
  if (!c) return "";
  const bad = (c.blocking || 0) + (c.errors || 0);
  const t = bad ? `${bad} data check${bad > 1 ? "s" : ""} failing` : `Data checks pass${c.warnings ? ` · ${c.warnings} warning${c.warnings > 1 ? "s" : ""}` : ""}`;
  return `<button type="button" class="badge rp-link ${bad ? "rp-bad" : c.warnings ? "wait" : "ok"}" data-goto="quality" title="${esc(`${c.passed ?? "?"} of ${c.total ?? "?"} checks passed`)}">${esc(t)}</button>`;
}
const rpAsk = q => { if (typeof hubAsk === "function") hubAsk(q); else setTab("ask"); };
const rpBC = range => { if (typeof hubDownloadBC === "function") hubDownloadBC(range); else hubToast("The Business Central file is not ready yet."); };
const rpSend = (scope, range, src) => { if (typeof hubSend === "function") hubSend(scope, range, src); else hubToast("Sending is not ready yet."); };
function rpWireCommon(root, range, src) {
  root.querySelectorAll("[data-goto]").forEach(b => b.onclick = e => { e.preventDefault(); hubGo(b.dataset.goto); });
  // Engineer 5's document-level handler reads data-from/data-to/data-source; only wire our own when it is missing.
  state.hubRange = range ? {...range} : null;
  root.querySelectorAll("[data-bc],[data-send],[data-raw]").forEach(b => { if (range) { b.dataset.from = range.from; b.dataset.to = range.to; } if (src) b.dataset.source = src; });
  if (typeof hubSend !== "function") root.querySelectorAll("[data-send]").forEach(b => b.onclick = () => rpSend(b.dataset.send, range, src));
  if (typeof hubDownloadBC !== "function") root.querySelectorAll("[data-bc]").forEach(b => b.onclick = () => rpBC(range));
}
function rpSpark(vals, up) {   // up: true/false colors the line by the insight direction; else last vs first
  const v = vals.filter(x => !rpBad(x));
  if (v.length < 2) return "";
  const mx = Math.max(...v), mn = Math.min(...v), w = 200, h = 44;
  const pts = vals.map((x, i) => rpBad(x) ? null : `${(i / (vals.length - 1) * w).toFixed(1)},${(h - 5 - (x - mn) / (mx - mn || 1) * (h - 10)).toFixed(1)}`).filter(Boolean).join(" ");
  return `<svg viewBox="0 0 ${w} ${h}" preserveAspectRatio="none" aria-hidden="true"><polyline points="${pts}" fill="none" stroke="var(${(up ?? v[v.length - 1] >= v[0]) ? "--good" : "--bad"})" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round" vector-effect="non-scaling-stroke"/></svg>`;
}
const rpMaxDate = rows => rows.reduce((a, r) => r.date > a ? r.date : a, "");
const rpMinDate = rows => rows.reduce((a, r) => !a || r.date < a ? r.date : a, "");

/* ---------------- Home ---------------- */
async function rpHome(root) {
  const [meta, days, thr, sr] = await Promise.all([hubMeta(), hubDays(), hubThriftly(), hubSuproRange()]);
  const last = rpMaxDate(days), wk = addDays(last, -7);
  const sup = sr.last ? await hubSupro(addDays(sr.last, -13), sr.last) : [];
  const h = new Date().getHours();
  const srcDay = (src, d) => rpSum(days.filter(r => r.source === src), d, d, ["item_sales", "orders"]);
  const upCells = src => days.some(r => r.source === src && r.date === last && String(r.src).startsWith("upload:"));
  const fresh = src => {
    if (upCells(src)) return ["wait", "From upload"];
    const f = meta.freshness && meta.freshness[src];
    if (!f) return ["wait", "No report yet"];
    if (f.business_date && f.business_date !== last) return ["wait", "Through " + shortDay(f.business_date)];
    return [f.badge === "Complete" ? "ok" : "wait", f.badge || hubFreshBadge(f)];
  };
  const card = (key, big, lbl, dl, chs, badge, when) => `<button class="src" type="button" data-src="${key}">
      <span class="top"><span class="name">${esc(HUB_SOURCES[key].label)}</span><span class="badge ${badge[0]}">${esc(badge[1])}</span></span>
      <span class="what">${esc(HUB_SOURCES[key].what)}</span>
      <span class="big num">${big}</span>
      <span class="lbl">${esc(lbl)} ${dl}</span>${when ? `<span class="lbl">${esc(when)}</span>` : ""}
      <span class="chs">${chs.map(c => `<span>${esc(c)}</span>`).join("")}</span>
      <span class="open">Open report</span></button>`;
  const ecomCard = src => { const a = srcDay(src, last), b = srcDay(src, wk);
    return card(src, rp$(a.item_sales), `item sales · ${rpN(a.orders)} customers`, rpDelta(a.item_sales, b.item_sales), HUB_SOURCES[src].channels.map(c => hubChLabel(src, c)), fresh(src)); };
  let supCard, thrCard;
  if (sr.last) {
    const a = rpSum(sup, sr.last, sr.last, ["sales", "customers"]), b = rpSum(sup, addDays(sr.last, -7), addDays(sr.last, -7), ["sales", "customers"]);
    const est = sup.some(r => r.date === sr.last && r.est);
    supCard = card("supro", rp$(a.sales), `store sales · ${rpN(a.customers)} customers`, rpDelta(a.sales, b.sales), [Object.keys(meta.stores || {}).length + " stores"],
      [est ? "wait" : "ok", (est ? "Estimated · " : "Through ") + shortDay(sr.last)], sr.last !== last ? rpDay(sr.last) : "");
  } else supCard = card("supro", "n/a", "store sales", "", ["Stores"], ["wait", "No report yet"]);
  const tl = rpMaxDate(thr);
  if (tl) {
    const a = rpSum(thr, tl, tl, ["pieces", "labor_hours"]), b = rpSum(thr, addDays(tl, -7), addDays(tl, -7), ["pieces", "labor_hours"]);
    thrCard = card("thriftly", rpBad(rpRatio(a.pieces, a.labor_hours)) ? "n/a" : nf(a.pieces / a.labor_hours, 1), "pieces per labor hour", rpDelta(rpRatio(a.pieces, a.labor_hours), rpRatio(b.pieces, b.labor_hours)),
      ["Production", "Sell-through"], ["ok", "Through " + shortDay(tl)], tl !== last ? rpDay(tl) : "");
  } else thrCard = card("thriftly", "n/a", "pieces per labor hour", "", ["Production"], ["wait", "No report yet"]);

  // Worth a look: computed from the documents, never from a model.
  const ins = [];
  const w7 = [...Array(7)].map((_, i) => addDays(last, i - 6));
  const moves = [];
  for (const src of ["upright", "cashmonkey"]) for (const ch of HUB_SOURCES[src].channels) {
    const rs = days.filter(r => r.source === src && r.channel === ch);
    const a = rpSum(rs, w7[0], last, ["item_sales"]).item_sales, b = rpSum(rs, addDays(w7[0], -7), addDays(last, -7), ["item_sales"]).item_sales;
    if (b > 0) moves.push({src, ch, label: hubChLabel(src, ch), a, b, p: (a - b) / b * 100, s: w7.map(d => rpSum(rs, d, d, ["item_sales"]).item_sales)});
  }
  moves.sort((x, y) => Math.abs(y.p) - Math.abs(x.p));
  if (moves[0]) { const m = moves[0];
    ins.push({t: `${m.label} sales are ${m.p >= 0 ? "up" : "down"} ${nf(Math.abs(m.p), 0)}% this week`, d: `${rp$(m.a)} vs ${rp$(m.b)} the 7 days before. Biggest change of any online channel.`, s: m.s, up: m.p >= 0,
      q: `How did ${m.label} sales trend over the last 7 days?`}); }
  if (sr.last) {
    const s7 = [...Array(7)].map((_, i) => addDays(sr.last, i - 6)), by = {};
    for (const r of sup) if (r.date >= s7[0]) by[r.store] = (by[r.store] || 0) + r.sales;
    const st = Object.entries(by).sort((a, b) => b[1] - a[1]);
    if (st.length) ins.push({t: `${rpStoreName(meta, st[0][0])} led the stores with ${rp$(st[0][1])} this week`, d: `${rpStoreName(meta, st[st.length - 1][0])} was lowest at ${rp$(st[st.length - 1][1])}. From Supro.`,
      s: s7.map(d => sup.filter(r => r.date === d && r.store === st[0][0]).reduce((a, r) => a + r.sales, 0)), q: "Which store sold the most in retail this week?"});
  }
  if (tl) {
    const t30 = thr.filter(r => r.date > addDays(tl, -30) && r.date <= tl), pph = t30.map(r => rpRatio(r.pieces, r.labor_hours));
    const avg = rpRatio(t30.reduce((a, r) => a + r.pieces, 0), t30.reduce((a, r) => a + r.labor_hours, 0));
    ins.push({t: `Pieces per labor hour: ${nf(pph[pph.length - 1], 1)} on ${shortDay(tl)}`, d: `30-night average is ${nf(avg, 1)}. From Thriftly.`, s: pph, q: "How is Thriftly production trending this month?"});
  }
  rpHome.ins = ins;
  const range = {from: last, to: last};
  root.innerHTML = `<div class="rp">
    <div class="greet">
      <div><h1>${h < 12 ? "Good morning" : h < 17 ? "Good afternoon" : "Good evening"}, Amanda</h1>
        <p class="sub">Reports for ${esc(rpDay(last))} are ready. Open a source to check the math.</p>
        <p class="status rp-status">${rpChecksBadge(meta.checks)}</p></div>
      <div class="homeactions"><button class="pill" type="button" data-bc>Download Business Central file</button><button class="pill" type="button" data-up>Upload file</button><button class="pill" type="button" data-raw>Download raw data</button><button class="pill primary" type="button" data-send="all">Send to leadership</button></div>
    </div>
    <div class="sources" id="rp-sources">${ecomCard("upright")}${ecomCard("cashmonkey")}${supCard}${thrCard}</div>
    <div class="homeask"><form class="askbar" id="rp-askform"><input id="rp-askq" placeholder="Ask a question, e.g. which store sold the most on eBay this week?" aria-label="Ask a question about the reports" autocomplete="off"><button type="submit">Ask</button></form></div>
    <div class="sec-h"><h2>Worth a look</h2><span class="hint" style="margin:0">Spotted in this week's data</span></div>
    <div class="insights" id="rp-insights">${ins.map((x, i) => `<button class="ins" type="button" data-i="${i}"><b>${esc(x.t)}</b><p>${esc(x.d)}</p>${rpSpark(x.s, x.up)}</button>`).join("")}</div>
    <div class="card later">
      <svg width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true" style="flex:none;color:var(--muted)"><rect x="3" y="4" width="18" height="16" rx="3"/><path d="M3 9h18M8 14h4"/></svg>
      <p><b>Business Central today: an import file.</b> <button class="back" type="button" data-goto="bc" style="margin:0;display:inline">See the API plan</button><br>The download holds every verified total, store detail, production numbers and the questions asked with their sources.</p>
    </div></div>`;
  root.querySelectorAll(".src").forEach(b => b.onclick = () => hubGo("report/" + b.dataset.src));
  root.querySelectorAll(".ins").forEach(b => b.onclick = () => rpAsk(ins[+b.dataset.i].q));
  $("#rp-askform", root).onsubmit = e => { e.preventDefault(); const q = $("#rp-askq", root).value.trim(); if (q) rpAsk(q); };
  rpWireCommon(root, range, null);
}

/* ---------------- Report: source -> channel -> store ---------------- */
const RP_COLS = {
  ecom: [["Item sales", "item_sales", "$"], ["Shipping", "shipping", "$"], ["Fees", "fees", "$"], ["Refunds", "refunds", "$"], ["Net", "net", "$"], ["Customers", "orders", "n"], ["Units", "units", "n"]],
  store: [["Item sales", "item_sales", "$"], ["Shipping", "shipping", "$"], ["Customers", "orders", "n"], ["Units", "units", "n"], ["Avg order", "aov", "$"]],
  retail: [["Store sales", "sales", "$"], ["Returns", "returns", "$"], ["Customers", "customers", "n"], ["Units", "units", "n"], ["Avg order", "aov", "$"]],
  prod: [["Pieces", "pieces", "n"], ["Labor hours", "labor_hours", "n"], ["Pieces / hour", "pph", "1"], ["Sent to e-com", "sent_to_ecom", "n"], ["Listed", "listed", "n"], ["Sold", "sold", "n"], ["Sell-through", "st", "%"]],
};
const RP_KEYS = {ecom: ["item_sales", "shipping", "fees", "refunds", "orders", "units"], store: ["item_sales", "shipping", "orders", "units"],
  retail: ["sales", "returns", "customers", "units"], prod: ["pieces", "labor_hours", "sent_to_ecom", "listed", "sold"]};
function rpCell(x, k, f) {
  const v = k === "net" ? (x.fees == null || x.refunds == null ? null : x.item_sales - x.fees - x.refunds)
    : k === "aov" ? rpRatio(x.sales ?? x.item_sales, x.customers ?? x.orders) : k === "pph" ? rpRatio(x.pieces, x.labor_hours) : k === "st" ? rpRatio(x.sold, x.listed) : x[k];
  return f === "$" ? rp$c(v) : f === "1" ? (rpBad(v) ? "n/a" : nf(v, 1)) : f === "%" ? (rpBad(v) ? "n/a" : nf(v * 100, 0) + "%") : rpN(v);
}

async function rpReport(root, route) {
  const src = route.source, S = HUB_SOURCES[src];
  if (!S) { root.innerHTML = `<div class="rp"><div class="banner">No report called "${esc(src)}". <a href="#home" data-goto="home">All reports</a></div></div>`; rpWireCommon(root, null, null); return; }
  const meta = await hubMeta();
  const path = route.path || [];
  const ch = S.channels.includes(path[1]) ? path[1] : "all";
  const store = path[2] == null || S.kind === "prod" ? null : path[2] === "-" ? "" : path[2];
  const kind = S.kind === "ecom" ? (store != null ? "store" : "ecom") : S.kind;

  // Data and the source's own date bounds.
  let base, first, last;
  if (S.kind === "ecom") { base = (await hubDays()).filter(r => r.source === src); first = rpMinDate(base); last = rpMaxDate(base); }
  else if (S.kind === "retail") { const b = await hubSuproRange(); first = b.first; last = b.last; }
  else { base = await hubThriftly(); first = rpMinDate(base); last = rpMaxDate(base); }
  if (!last) { root.innerHTML = `<div class="rp"><nav class="crumbs"><button type="button" data-goto="home">All reports</button></nav><div class="card"><h2>${esc(S.label)}</h2><p class="hint">No ${esc(S.label)} data is stored yet.</p></div></div>`; rpWireCommon(root, null, src); return; }
  const PR = presets(last, first);
  if (!state.rng || !(state.rng.preset in PR || state.rng.preset === "custom")) state.rng = {preset: "latest"};
  let from, to;
  if (state.rng.preset === "custom") { from = state.rng.from < first ? first : state.rng.from > last ? last : state.rng.from; to = state.rng.to > last ? last : state.rng.to < first ? first : state.rng.to; if (from > to) [from, to] = [to, from]; }
  else ({from, to} = PR[state.rng.preset]);
  const single = from === to, RL = rpRange(from, to), VS = rpVs(from, to), pr = rpPrior(from, to), n = rpSpan(from, to);
  const lo = [pr.from, addDays(to, -13), from].sort()[0];

  // Scope rows: one object per date (and channel) with the measures of this kind.
  let rows, storeRows = null, supro = null;
  const ids = ch === "all" ? S.channels : [ch];
  if (S.kind === "ecom") {
    storeRows = (await hubStoreDays(lo < first ? first : lo, to)).filter(r => r.source === src && ids.includes(r.channel));
    rows = store != null ? storeRows.filter(r => (r.store || "") === store) : base.filter(r => ids.includes(r.channel));
  } else if (S.kind === "retail") {
    supro = await hubSupro(lo < first ? first : lo, to);
    rows = store != null ? supro.filter(r => r.store === store) : supro;
  } else rows = base;
  const keys = RP_KEYS[kind], cols = RP_COLS[kind];
  const v = rpSum(rows, from, to, keys);
  const hasPrior = pr.from >= first, pv = hasPrior ? rpSum(rows, pr.from, pr.to, keys) : null;
  const scopeName = (ch === "all" ? S.label : hubChLabel(src, ch)) + (store != null ? " at " + rpStoreName(meta, store) : "");
  const title = store != null ? rpStoreName(meta, store) : ch === "all" ? S.label + " report" : hubChLabel(src, ch);

  // Metrics.
  const D_ = (a, b, inv) => pv ? rpDelta(a, b, inv) : "";
  const M = kind === "retail" ? [["Store sales", rp$(v.sales), D_(v.sales, pv && pv.sales)], ["Customers", rpN(v.customers), D_(v.customers, pv && pv.customers)],
      ["Avg order", rp$c(rpRatio(v.sales, v.customers)), D_(rpRatio(v.sales, v.customers), pv && rpRatio(pv.sales, pv.customers))], ["Returns", rp$(v.returns), D_(v.returns, pv && pv.returns, true)], ["Units sold", rpN(v.units), D_(v.units, pv && pv.units)]]
    : kind === "prod" ? [["Pieces processed", rpN(v.pieces), D_(v.pieces, pv && pv.pieces)], ["Labor hours", rpN(v.labor_hours), D_(v.labor_hours, pv && pv.labor_hours, true)],
      ["Pieces per hour", rpCell(v, "pph", "1"), D_(rpRatio(v.pieces, v.labor_hours), pv && rpRatio(pv.pieces, pv.labor_hours))], ["Sent to e-commerce", rpN(v.sent_to_ecom), D_(v.sent_to_ecom, pv && pv.sent_to_ecom)],
      ["Sell-through", rpCell(v, "st", "%"), D_(rpRatio(v.sold, v.listed), pv && rpRatio(pv.sold, pv.listed))]]
    : [["Item sales", rp$(v.item_sales), D_(v.item_sales, pv && pv.item_sales)], ["Customers", rpN(v.orders), D_(v.orders, pv && pv.orders)],
      ["Avg order", rp$c(rpRatio(v.item_sales, v.orders)), D_(rpRatio(v.item_sales, v.orders), pv && rpRatio(pv.item_sales, pv.orders))], ["Shipping", rp$(v.shipping), D_(v.shipping, pv && pv.shipping)], ["Units sold", rpN(v.units), D_(v.units, pv && pv.units)]];
  const metrics = M.map(([l, x, d]) => `<div class="metric"><div class="l">${esc(l)}</div><div class="v num">${x}</div>${d ? d + ` <span class="rp-vs">${esc(VS)}</span>` : `<span class="rp-vs">${pv ? "" : "No earlier data to compare"}</span>`}</div>`).join("");

  // Table.
  const byChannel = kind === "ecom" && ch === "all";
  const line = (lbl, x, cls = "") => `<tr class="${cls}"><td>${esc(lbl)}</td>${cols.map(c => `<td class="num">${rpCell(x, c[1], c[2])}</td>`).join("")}</tr>`;
  const Wd = rpWord(n);
  let tH, tHint, tb = "";
  if (byChannel) {
    tH = single ? "Daily summary" : "Summary for the range";
    const mtd = rpSum(rows, monthStart(to) < first ? first : monthStart(to), to, ["item_sales"]).item_sales;
    tHint = (single ? "One row per channel, as on the Daily Summary Spreadsheet." : `One row per channel, ${RL}.`) + ` Month to date ${rp$(mtd)}.`;
    for (const c of S.channels) tb += line(hubChLabel(src, c), rpSum(rows.filter(r => r.channel === c), from, to, keys));
    tb += line(S.label + " total", v, "sum");
  } else if (single) {
    const f7 = addDays(to, -6) < first ? first : addDays(to, -6);
    tH = "Last 7 nights"; tHint = "Selected night is highlighted.";
    for (let d = f7; d <= to; d = addDays(d, 1)) tb += line(rpDay(d), rpSum(rows, d, d, keys), d === to ? "hl" : "");
    tb += line(`${rpSpan(f7, to)}-night total`, rpSum(rows, f7, to, keys), "sum");
  } else {
    tH = "By " + Wd; tHint = `${RL}, ${n} nights.`;
    for (const b of rpBuckets(from, to)) tb += line(b.label, rpSum(rows, b.from, b.to, keys));
    tb += line("Range total", v, "sum");
  }
  const table = `<thead><tr><th>${byChannel ? "Channel" : single ? "Night" : Wd[0].toUpperCase() + Wd.slice(1)}</th>${cols.map(c => `<th>${esc(c[0])}</th>`).join("")}</tr></thead><tbody>${tb}</tbody>`;

  // Show the math (definitions, not numbers a model made).
  const inRange = rows.filter(r => r.date >= from && r.date <= to);
  const uploaded = [...new Set(inRange.filter(r => String(r.src || "").startsWith("upload:")).map(r => r.date + " " + hubChLabel(src, r.channel)))];
  const flag = t => `<span class="flag">${esc(t)}</span>`;
  let math;
  if (kind === "retail") {
    const df = (supro && supro.definitions) || {};
    math = [["Source file", esc(`Supro end-of-day store reports, ${RL} (${n} report${n > 1 ? "s" : ""}).`)],
      ["Store sales", esc(df.sales || "Net register sales per store, after returns, tax excluded.")], ["Customers", esc(df.customers || "Transactions per store.")],
      ["Returns", esc(df.returns || "Dollars refunded at the register.") + " Already taken out of store sales."], ["Avg order", "Store sales ÷ customers."]];
    if (supro && supro.estimated.length) math.push(["Estimated", flag(`${supro.estimated.map(monthLabel).join(", ")}: no store revenue input yet, so these days are estimated.`)]);
  } else if (kind === "prod") {
    const df = base.definitions || {};
    math = [["Source", esc(`Thriftly production log, ${RL}.`)], ...["pieces", "labor_hours", "sent_to_ecom", "listed", "sold"].filter(k => df[k]).map(k => [cols.find(c => c[1] === k)[0], esc(df[k])]),
      ["Pieces per hour", "Pieces ÷ labor hours, each summed over the range first."], ["Sell-through", "Sold ÷ listed. " + flag("Window and definition to confirm with Amanda.")]];
  } else {
    math = [["Source file", esc(`${S.label} ${src === "upright" ? "paid-orders" : "orders"} reports, ${RL} (${n} nightly pull${n > 1 ? "s" : ""}).`)],
      ["Item sales", "Sum of item subtotals on paid orders. Sales tax and shipping are left out."], ["Shipping", "Shipping charged to buyers."],
      ["Customers", esc(`Paid orders: ${rpN(v.orders)} in this ${single ? "night" : "range"}. Test and canceled orders are removed.`)], ["Units sold", "Sum of item quantities."]];
    if (kind === "ecom") math.push(["Fees and refunds", "Marketplace fees and refunds on the order's business date. " + flag("Net = item sales − fees − refunds is our assumption; confirm with Amanda.")]);
    if (ids.includes("ebay")) math.push(["eBay", "eBay orders are split by line of business: general merchandise is Upright, books are Cash Monkey."]);
    if (uploaded.length) math.push(["Uploaded files", esc(`${uploaded.length} night-channel cell${uploaded.length > 1 ? "s" : ""} come from the Upload tab (${uploaded.slice(0, 4).join(", ")}${uploaded.length > 4 ? "…" : ""}). Uploaded eBay totals are split by the warehouse source mix; units keep the warehouse value.`)]);
  }
  math.push(["Comparison", single ? "Same weekday one week earlier." : `The ${n} days just before the range.`]);
  if (S.kind === "ecom") math.push(["Store", "The store that sent the item. An order with items from two stores counts as a customer at both. Shipping is shared across stores by item-sales share." + (uploaded.length ? " " + flag("Uploaded files have no store, so store rows cover warehouse data only.") : "")]);
  math.push(["Business day", "Eastern time, midnight to midnight."]);

  // By store.
  let storeHtml = "";
  if (store == null && S.kind !== "prod") {
    const sk = S.kind === "retail" ? ["sales", "customers"] : ["item_sales", "orders"], srows = S.kind === "retail" ? supro : storeRows;
    const by = new Map();
    for (const r of srows) { const k = r.store || ""; if (!by.has(k)) by.set(k, []); by.get(k).push(r); }
    const list = [...by.entries()].map(([k, rs]) => ({k, v: rpSum(rs, from, to, sk), p: hasPrior ? rpSum(rs, pr.from, pr.to, sk) : null})).filter(x => x.v[sk[0]] || x.v[sk[1]]).sort((a, b) => b.v[sk[0]] - a.v[sk[0]]);
    const tot = list.reduce((a, x) => a + x.v[sk[0]], 0), mx = list.length ? list[0].v[sk[0]] || 1 : 1;
    const allCust = S.kind === "retail" ? list.reduce((a, x) => a + x.v.customers, 0) : v.orders;
    storeHtml = `<div class="card storecard" id="rp-storecard"><h2>By store</h2><p class="hint">${esc(`${scopeName}, ${RL}. Click a store to open it.`)}</p>
      <div class="tbl-wrap"><table id="rp-stores"><thead><tr><th>Store</th><th style="text-align:left">Share of sales</th><th>Sales</th><th>Customers</th><th>Avg order</th><th>Change</th><th></th></tr></thead><tbody>
      ${list.map(x => `<tr data-store="${esc(x.k === "" ? "-" : x.k)}" tabindex="0"><td><b>${esc(rpStoreName(meta, x.k))}</b></td><td style="text-align:left"><span class="share" style="width:${Math.max(0, x.v[sk[0]] / mx * 90).toFixed(0)}px"></span>${tot ? nf(x.v[sk[0]] / tot * 100, 0) : 0}%</td><td class="num">${rp$c(x.v[sk[0]])}</td><td class="num">${rpN(x.v[sk[1]])}</td><td class="num">${rp$c(rpRatio(x.v[sk[0]], x.v[sk[1]]))}</td><td>${x.p ? rpDelta(x.v[sk[0]], x.p[sk[0]]) : ""}</td><td class="go">Open</td></tr>`).join("")}
      <tr class="sum"><td>All stores</td><td></td><td class="num">${rp$c(tot)}</td><td class="num">${rpN(allCust)}</td><td class="num">${rp$c(rpRatio(tot, allCust))}</td><td></td><td></td></tr></tbody></table></div></div>`;
  }

  // Report files and freshness (from the Daily Pulse): which source files back the selected night.
  let filesHtml = "";
  if (S.kind === "ecom") {
    const pulse = await hubTry("pulse/all"), rt = src === "upright" ? "upright_paid_orders" : "cashmonkey_orders";
    const f = (meta.freshness && meta.freshness[src] && meta.freshness[src].business_date === to) ? meta.freshness[src]
      : pulse && pulse[to] ? hubFreshFromPulse((pulse[to].sources || []).find(s => s.report_type === rt)) : null;
    const up = rows.some(r => r.date === to && String(r.src || "").startsWith("upload:"));
    filesHtml = `<details class="rp-files"><summary>Report files for ${esc(rpDay(to))}</summary>${f ? `<p class="rp-fresh"><span class="badge ${f.status === "complete" ? "ok" : "wait"}">${esc(hubFreshBadge(f))}</span> ${esc((f.files || []).join(", "))}${f.latest_order_at ? esc(" · latest order " + new Date(f.latest_order_at).toLocaleString("en-US", {month: "short", day: "numeric", hour: "numeric", minute: "2-digit", timeZone: "America/New_York"})) : ""}</p>` : `<p class="hint">No file record for this night.</p>`}${up ? `<p class="rp-fresh"><span class="badge wait">From upload</span> <a href="#upload" data-goto="upload">See uploads</a></p>` : ""}</details>`;
  }

  // Nightly email text (the Daily Pulse report), all five marketplaces, for the same period.
  let emailHtml = "", emailText = "";
  if (S.kind === "ecom") {
    const [drows, q] = await Promise.all([dailyRows(), hubTry("quality/latest")]);
    emailText = reportText(totalsFor(drows, from, to), totalsFor(drows, pr.from, pr.to), from, to, q ? trustLine(q) : "");
    emailHtml = `<details class="rp-email"><summary>Nightly email text</summary><textarea id="rp-text" readonly rows="13">${esc(emailText)}</textarea></details>`;
  }

  // Crumbs and channel tabs.
  const cr = [`<button type="button" data-nav="home">All reports</button>`];
  cr.push(ch === "all" && store == null ? `<b>${esc(S.label)}</b>` : `<button type="button" data-nav="report/${src}">${esc(S.label)}</button>`);
  if (ch !== "all") cr.push(store == null ? `<b>${esc(hubChLabel(src, ch))}</b>` : `<button type="button" data-nav="report/${src}/${ch}">${esc(hubChLabel(src, ch))}</button>`);
  if (store != null) cr.push(`<b>${esc(rpStoreName(meta, store))}</b>`);
  const tabs = S.channels.length > 1 && store == null ? [`<button class="chtab" type="button" data-ch="all" aria-pressed="${ch === "all"}">All channels</button>`]
    .concat(S.channels.map(c => `<button class="chtab" type="button" data-ch="${c}" aria-pressed="${ch === c}"><span class="sw" style="background:${HUB_CHANNELS[c].color}"></span>${esc(hubChLabel(src, c))}</button>`)).join("") : "";
  const estTag = supro && rows.some(r => r.est && r.date >= from && r.date <= to) ? ` <span class="badge wait">Estimated</span>` : "";
  const chartH = kind === "prod" ? "Pieces per labor hour" : single ? "Last 14 nights" : "By " + Wd;
  const range = {from, to};

  root.innerHTML = `<div class="rp" data-src="${src}">
    <nav class="crumbs" aria-label="Breadcrumb">${cr.join('<span class="sep">›</span>')}</nav>
    <div class="rhead">
      <div><h1 id="rp-title">${esc(title)}${estTag}</h1><p class="sub">${esc(store != null ? `${scopeName}, ${RL}.` : `${S.what}, ${RL}.`)}</p><p class="status rp-status">${rpChecksBadge(meta.checks)}</p></div>
      <div class="controls rangectl">
        <div class="rangebox"><label>From<input type="date" class="pill" id="rp-from" min="${first}" max="${last}" value="${from}"></label><label>To<input type="date" class="pill" id="rp-to" min="${first}" max="${last}" value="${to}"></label></div>
        <div class="presets" role="group" aria-label="Report period">${Object.entries(PR).map(([k, p]) => `<button class="chip" type="button" data-preset="${k}" aria-pressed="${state.rng.preset === k}">${esc(p.label)}</button>`).join("")}</div>
        <p class="rangemsg" id="rp-msg" aria-live="polite">${esc(state.rngMsg || "")}</p>
      </div>
    </div>
    ${tabs ? `<div class="chtabs" role="group" aria-label="Channel">${tabs}</div>` : ""}
    <div class="metrics" id="rp-metrics"${S.kind === "ecom" && store == null && ch === "all" ? ` data-explain="report:${from}:${to}"` : ""}>${metrics}</div>
    <div class="rgrid">
      <div class="card"><h2>${esc(tH)}</h2><p class="hint">${esc(tHint)}</p><div class="tbl-wrap"><table id="rp-tbl">${table}</table></div>
        <details><summary>Show the math</summary><ul class="math" id="rp-math">${math.map(([a, b]) => `<li><b>${esc(a)}</b><span>${b}</span></li>`).join("")}</ul></details>${filesHtml}${emailHtml}</div>
      <div class="card"><h2>${esc(chartH)}</h2><p class="hint">${esc(kind === "prod" ? (single ? "Last 14 nights." : `By ${Wd}, ${RL}.`) : (kind === "retail" ? "Store sales" : ids.length > 1 ? "Item sales by channel" : "Item sales") + (store != null ? " at " + rpStoreName(meta, store) : "") + (single ? "." : `, ${RL}.`))}</p>
        <div class="chartbox"><canvas id="rp-chart" aria-label="${esc(chartH)}"></canvas></div></div>
    </div>
    ${storeHtml}
    <div class="sendrow">
      <button class="pill" type="button" id="rp-ask">Ask about this report</button>
      ${emailHtml ? `<button class="pill" type="button" id="rp-copy">Copy summary</button>` : ""}
      <button class="pill" type="button" data-bc>Download Business Central file</button>
      <button class="pill" type="button" data-up>Upload file</button>
      <button class="pill" type="button" data-raw>Download raw data</button>
      <button class="pill primary" type="button" data-send="one">Send to leadership</button>
    </div></div>`;
  state.rngMsg = "";

  // Events.
  const base_ = `report/${src}`, chPath = c => c === "all" ? base_ : `${base_}/${c}`;
  root.querySelectorAll("[data-nav]").forEach(b => b.onclick = () => hubGo(b.dataset.nav));
  root.querySelectorAll(".chtab").forEach(b => b.onclick = () => setTab(chPath(b.dataset.ch)));
  root.querySelectorAll("#rp-stores tr[data-store]").forEach(tr => { const open = () => hubGo(`${base_}/${ch}/${tr.dataset.store}`); tr.onclick = open; tr.onkeydown = e => { if (e.key === "Enter") open(); }; });
  root.querySelectorAll("[data-preset]").forEach(b => b.onclick = () => { state.rng = {preset: b.dataset.preset}; render(); });
  const apply = () => {
    let f = $("#rp-from", root).value, t = $("#rp-to", root).value, msg = "";
    if (!f || !t) return;
    if (f > t) { [f, t] = [t, f]; msg = "Dates swapped so From comes first. "; }
    if (f < first || t > last) msg += `Data runs ${rpDay(first)} to ${rpDay(last)}; dates outside that were adjusted.`;
    state.rng = {preset: "custom", from: f < first ? first : f > last ? last : f, to: t > last ? last : t < first ? first : t}; state.rngMsg = msg; render();
  };
  $("#rp-from", root).onchange = apply; $("#rp-to", root).onchange = apply;
  $("#rp-ask", root).onclick = () => rpAsk(`How did ${scopeName} do ${single ? "on " + rpDay(from) : "from " + from + " to " + to}?`);
  const cp = $("#rp-copy", root);
  if (cp) cp.onclick = async () => { try { await navigator.clipboard.writeText(emailText); hubToast("Summary copied"); } catch (e) { const d = $(".rp-email", root); if (d) d.open = true; const ta = $("#rp-text", root); if (ta) ta.select(); hubToast("Select the text and copy it"); } };
  rpWireCommon(root, range, src);

  // Chart: one night shows the 14 nights ending there; a range shows the range itself.
  const B = single ? [...Array(14)].map((_, i) => addDays(to, i - 13)).filter(d => d >= first).map(d => ({short: shortDay(d), from: d, to: d})) : rpBuckets(from, to);
  const canvas = $("#rp-chart", root);
  rpAfterAttach(canvas, () => hubChart(canvas, () => {
    if (kind === "prod") return {type: "line", data: {labels: B.map(b => b.short), datasets: [{label: "Pieces per hour", data: B.map(b => { const q = rpSum(rows, b.from, b.to, ["pieces", "labor_hours"]); return rpBad(rpRatio(q.pieces, q.labor_hours)) ? null : +(q.pieces / q.labor_hours).toFixed(1); }),
      borderColor: rpColor("--c1"), backgroundColor: rpColor("--c1") + "22", fill: true, tension: .35, pointRadius: B.length <= 31 ? 2 : 0}]}, options: {plugins: {legend: {display: false}}}};
    const series = kind === "retail" ? [{label: "Store sales", color: rpColor("--c3"), rs: rows, k: "sales"}]
      : (byChannel ? S.channels : ids).map(c => ({label: hubChLabel(src, c), color: rpColor(HUB_CHANNELS[c].color), rs: rows.filter(r => r.channel === c), k: "item_sales"}));
    return {type: "bar", data: {labels: B.map(b => b.short), datasets: series.map(s => ({label: s.label, stack: "s", borderRadius: 4,
      data: B.map(b => Math.round(rpSum(s.rs, b.from, b.to, [s.k])[s.k] || 0)),
      backgroundColor: B.map((_, j) => single && series.length === 1 && j !== B.length - 1 ? rpFade(s.color) : s.color)}))},
      options: {plugins: {legend: {display: series.length > 1}}, scales: {x: {stacked: true}, y: {stacked: true, ticks: {callback: hubMoney}}}}};
  }));
}

/* ---------------- entry ---------------- */
async function viewHome(root) {
  const r = state.route || {};
  if (r.view === "report" && r.source) return rpReport(root, r);
  if (r.view === "report") { hubGo("home"); return; }
  return rpHome(root);
}
registerTab("home", "Reports", viewHome, 10);
