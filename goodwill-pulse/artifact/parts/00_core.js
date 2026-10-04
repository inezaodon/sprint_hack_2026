const $ = (s, r = document) => r.querySelector(s);
const esc = s => String(s ?? "").replace(/[&<>"]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
const nf = (v, d = 0) => v == null || isNaN(v) ? "n/a" : Number(v).toLocaleString("en-US", {minimumFractionDigits: d, maximumFractionDigits: d});
const usd = (v, d = 0) => v == null ? "n/a" : (v < 0 ? "−" : "") + "$" + nf(Math.abs(v), d);
const pct = (v, d = 1) => v == null ? "n/a" : nf(v * 100, d) + "%";
const CH = {amazon: "var(--c1)", ebay: "var(--c2)", goodwillbooks: "var(--c3)", goodwillfinds: "var(--c4)", shopgoodwill: "var(--c5)"};
const monthLabel = m => new Date(m + "-01T12:00:00").toLocaleDateString("en-US", {month: "long", year: "numeric"});
const monthShort = m => new Date(m.slice(0, 7) + "-01T12:00:00").toLocaleDateString("en-US", {month: "short", year: "2-digit"});

function fmt(unit, v) {
  if (v == null) return "n/a";
  switch (unit) {
    case "usd": return usd(v, Math.abs(v) < 1000 ? 2 : 0);
    case "pct": return pct(v);
    case "usd_per_hour": return usd(v, 2) + "/hr";
    case "days": return nf(v, 1) + " d";
    default: return nf(v, Math.abs(v) < 100 && v % 1 ? 1 : 0);
  }
}
function delta(card, key) {
  const base = card[key];
  if (card.value == null || base == null || base === 0) return `<span class="flat">n/a</span>`;
  const rel = card.unit === "pct" ? card.value - base : (card.value - base) / Math.abs(base);
  const txt = card.unit === "pct" ? (rel >= 0 ? "+" : "−") + nf(Math.abs(rel) * 100, 1) + " pts" : (rel >= 0 ? "+" : "−") + nf(Math.abs(rel) * 100, 1) + "%";
  const good = card.better === "up" ? rel > 0 : card.better === "down" ? rel < 0 : null;
  const cls = Math.abs(rel) < 0.0005 || good == null ? "flat" : good ? "up" : "down";
  return `<span class="${cls}">${rel >= 0 ? "▲" : "▼"} ${txt}</span>`;
}
function spark(points, w = 240, h = 44) {
  const vs = points.map(p => p.value).filter(v => v != null);
  if (vs.length < 2) return "";
  const lo = Math.min(...vs), hi = Math.max(...vs), rng = hi - lo || 1, pad = 4;
  const xy = points.map((p, i) => p.value == null ? null : [pad + i * (w - 2 * pad) / (points.length - 1), h - pad - (p.value - lo) / rng * (h - 2 * pad)]).filter(Boolean);
  const line = xy.map(p => p.map(n => n.toFixed(1)).join(",")).join(" ");
  const last = xy[xy.length - 1];
  return `<svg viewBox="0 0 ${w} ${h}" preserveAspectRatio="none" aria-hidden="true"><polygon points="${pad},${h} ${line} ${last[0]},${h}" fill="var(--accent-soft)"/><polyline points="${line}" fill="none" stroke="var(--accent)" stroke-width="1.8" stroke-linejoin="round" vector-effect="non-scaling-stroke"/><circle cx="${last[0]}" cy="${last[1]}" r="3" fill="var(--accent)"/></svg>`;
}
function lineChart(series, unit) {
  const W = 760, H = 280, L = 62, R = 14, T = 12, B = 30;
  const all = series.flatMap(s => s.points.map(p => p.value)).filter(v => v != null);
  if (!all.length) return `<div class="empty">No data for this measure.</div>`;
  let lo = Math.min(0, ...all), hi = Math.max(...all); if (hi === lo) hi = lo + 1;
  const step = (() => { const raw = (hi - lo) / 4, p = 10 ** Math.floor(Math.log10(raw)), n = raw / p; return (n <= 1 ? 1 : n <= 2 ? 2 : n <= 5 ? 5 : 10) * p; })();
  const top = Math.ceil(hi / step) * step, bot = Math.floor(lo / step) * step;
  const n = series[0].points.length, x = i => L + i * (W - L - R) / (n - 1), y = v => T + (top - v) / (top - bot) * (H - T - B);
  let g = "";
  for (let v = bot; v <= top + 1e-9; v += step) g += `<line x1="${L}" x2="${W - R}" y1="${y(v)}" y2="${y(v)}" stroke="var(--line)"/><text x="${L - 8}" y="${y(v) + 4}" text-anchor="end">${esc(unit === "usd" ? (v >= 1000 ? "$" + nf(v / 1000) + "k" : "$" + nf(v)) : unit === "pct" ? nf(v * 100) + "%" : nf(v, step < 1 ? 1 : 0))}</text>`;
  const every = Math.max(1, Math.ceil(n / 7));
  series[0].points.forEach((p, i) => { if ((n - 1 - i) % every === 0) g += `<text x="${x(i)}" y="${H - 8}" text-anchor="${i === n - 1 ? "end" : i === 0 ? "start" : "middle"}">${esc(p.label ?? monthShort(p.month))}</text>`; });
  const lines = series.map(s => {
    const pts = s.points.map((p, i) => p.value == null ? null : [x(i), y(p.value)]).filter(Boolean);
    const d = pts.map(p => p.map(v => v.toFixed(1)).join(",")).join(" "), e = pts[pts.length - 1];
    return `<polyline points="${d}" fill="none" stroke="${s.color}" stroke-width="2" stroke-linejoin="round"/><circle cx="${e[0]}" cy="${e[1]}" r="3.5" fill="${s.color}"/>`;
  }).join("");
  return `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Monthly trend by marketplace">${g}${lines}</svg>` +
    `<div class="legend">${series.map(s => `<span><i style="background:${s.color}"></i>${esc(s.label)}</span>`).join("")}</div>`;
}
const pill = (cls, t) => `<span class="pill ${cls}">${esc(t)}</span>`;
const STATUS = {ok: ["p-ok", "OK"], warn: ["p-warn", "Check"], bad: ["p-bad", "Blocked"]};

/* ---------------- data access ---------------- */
let db = null;
const cache = {};
function invalidate(prefix) { for (const k of Object.keys(cache)) if (!prefix || k.startsWith(prefix)) delete cache[k]; }
async function read(path) {
  if (cache[path]) return cache[path];
  const snap = await db.doc(path).get();
  if (!snap.exists) throw new Error(`Nothing is stored at ${path} yet.`);
  return (cache[path] = snap.data());
}
const state = {tab: "home", top: "home", route: {tab: "home", path: [], source: null}, date: null, month: null, kpi: "total_revenue", catBy: "revenue", closeMonth: null};

/* ---------------- tab registry: every view file registers itself ----------------
   registerTab(id, label, view, order, opts)   opts = {group: "<top tab id>", sub: "<sub-tab label>"}
   Top tabs are fixed by the shell (HUB_TOP). A view whose id is a top tab id, or that names a group, lands there.
   Old ids keep working: "pulse" maps to "home" (the Daily Pulse view fills Reports until a "home" view registers). */
const VIEWS = {}, TABS = [], TAB_META = {};
const HUB_TOP = [
  {id: "home", label: "Reports"}, {id: "dashboard", label: "Dashboard"}, {id: "ask", label: "Ask"},
  {id: "bc", label: "Business Central", short: "BC"}, {id: "data", label: "Data"}];
const HUB_GROUP_ALIAS = {upload: "data", data: "data", close: "bc"};          /* group names accepted in opts.group */
const HUB_SUB_DEFAULT = {                                                      /* where each view lives until it says otherwise */
  bc: {group: "bc", sub: "Nightly", order: 1}, close: {group: "bc", sub: "Month-end close", order: 2},
  upload: {group: "data", sub: "Upload", order: 1}, quality: {group: "data", sub: "Quality", order: 2},
  lineage: {group: "data", sub: "Lineage", order: 3}};
const HUB_ID_ALIAS = {pulse: "home", report: "home", data: "upload", reports: "home", businesscentral: "bc", nightly: "bc"};
function registerTab(id, label, view, order, opts) {
  VIEWS[id] = view;
  const i = TABS.findIndex(t => t.id === id); if (i >= 0) TABS.splice(i, 1);
  TABS.push({id, label, order: order ?? 50}); TABS.sort((a, b) => a.order - b.order);
  const d = HUB_SUB_DEFAULT[id] || {}, o = opts || {};
  const group = HUB_GROUP_ALIAS[o.group] || o.group || d.group || null;
  TAB_META[id] = {id, label, order: order ?? 50, group: group && group !== id ? group : (d.group || null), sub: o.sub || d.sub || label, subOrder: d.order ?? order ?? 50};
  if (typeof hubNavRefresh === "function") hubNavRefresh();
}

/* ---------------- Hub helpers: theme, toast, charts ---------------- */
function hubTheme(varName) { try { return getComputedStyle(document.documentElement).getPropertyValue(varName).trim(); } catch (e) { return ""; } }
function hubToast(text) {
  const el = document.getElementById("toast"); if (!el) return;
  el.textContent = String(text ?? ""); el.classList.add("on");
  clearTimeout(el._t); el._t = setTimeout(() => el.classList.remove("on"), 2800);
}
/* hubChart(canvas | "id", config): Chart.js with the reference defaults (Figtree, muted ticks, --line grid, legend at bottom).
   Destroys any chart already on that canvas and re-renders on theme change. config.options merges over the defaults;
   a function config (() => cfg) is re-evaluated on theme change so colors read via hubTheme() update too. */
const HUB_CHARTS = new Set();
const hubMoney = v => (v < 0 ? "−" : "") + "$" + (Math.abs(v) >= 1000 ? (Math.abs(v) / 1000).toFixed(Math.abs(v) % 1000 ? 1 : 0) + "k" : Math.abs(v));
function hubMerge(a, b) {
  if (!b || typeof b !== "object" || Array.isArray(b)) return b === undefined ? a : b;
  const o = Array.isArray(a) || !a || typeof a !== "object" ? {} : {...a};
  for (const k of Object.keys(b)) o[k] = hubMerge(o[k], b[k]);
  return o;
}
function hubChartDefaults() {
  const line = hubTheme("--line"), muted = hubTheme("--muted");
  return {responsive: true, maintainAspectRatio: false, interaction: {mode: "index", intersect: false},
    plugins: {legend: {position: "bottom", labels: {boxWidth: 10, boxHeight: 10, useBorderRadius: true, borderRadius: 3, color: muted}}},
    scales: {x: {grid: {display: false}, ticks: {maxTicksLimit: 8, color: muted}, border: {color: line}},
             y: {grid: {color: line}, ticks: {color: muted}, border: {display: false}}}};
}
function hubChart(canvas, config) {
  const el = typeof canvas === "string" ? (document.getElementById(canvas) || document.querySelector(canvas)) : canvas;
  if (!el || typeof Chart === "undefined") return null;
  const old = Chart.getChart ? Chart.getChart(el) : null; if (old) { HUB_CHARTS.delete(old); old.destroy(); }
  Chart.defaults.font.family = "Figtree, system-ui, sans-serif";
  Chart.defaults.color = hubTheme("--muted");
  const cfg = typeof config === "function" ? config() : config;
  const type = cfg.type, def = hubChartDefaults();
  if (type === "doughnut" || type === "pie" || type === "radar" || type === "polarArea") delete def.scales;
  else if (cfg.options && cfg.options.indexAxis === "y") def.scales = {x: def.scales.y, y: {...def.scales.x, ticks: {color: def.scales.x.ticks.color}}};
  const ch = new Chart(el, {...cfg, options: hubMerge(def, cfg.options || {})});
  ch.$hub = {canvas: el, config};
  HUB_CHARTS.add(ch);
  return ch;
}
function hubChartsRefresh() {
  for (const ch of [...HUB_CHARTS]) {
    if (!ch.canvas || !ch.canvas.isConnected) { HUB_CHARTS.delete(ch); try { ch.destroy(); } catch (e) {} continue; }
    try { hubChart(ch.$hub.canvas, ch.$hub.config); } catch (e) {}
  }
}
try {
  matchMedia("(prefers-color-scheme: dark)").addEventListener("change", hubChartsRefresh);
  new MutationObserver(hubChartsRefresh).observe(document.documentElement, {attributes: true, attributeFilter: ["data-theme"]});
} catch (e) {}
