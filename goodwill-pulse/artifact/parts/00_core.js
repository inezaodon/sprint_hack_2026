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
const state = {tab: "pulse", date: null, month: null, kpi: "total_revenue", catBy: "revenue", closeMonth: null};

/* ---------------- tab registry: every view file registers itself ---------------- */
const VIEWS = {}, TABS = [];
function registerTab(id, label, view, order) { VIEWS[id] = view; TABS.push({id, label, order: order ?? 50}); TABS.sort((a, b) => a.order - b.order); }
