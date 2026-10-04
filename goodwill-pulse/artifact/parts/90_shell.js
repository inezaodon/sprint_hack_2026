/* ---------------- shell (Engineer 2): top tabs, sub-tabs, hash routing, render loop ----------------
   Routes: #home, #report/<source>[/...], #dashboard, #ask, #bc, #close, #upload, #quality, #lineage (+ any registered id).
   setTab(id) accepts any of those (with or without "#"), plus old ids (pulse -> home).
   The view reads state.tab (view id), state.top (top tab id) and state.route = {tab, view, source, path}. */
var hubShellReady = false;            /* var: registerTab may call hubNavRefresh before this file's consts exist */
let seq = 0;

function hubResolve(raw) {
  const parts = String(raw || "").replace(/^#/, "").split("/").filter(Boolean).map(s => { try { return decodeURIComponent(s); } catch (e) { return s; } });
  let head = (parts[0] || "home").toLowerCase();
  const isReport = head === "report";
  let tab = HUB_ID_ALIAS[head] || head;
  if (!isReport && !VIEWS[tab] && !TAB_META[tab] && !HUB_SUB_DEFAULT[tab] && !HUB_TOP.some(t => t.id === tab)) tab = "home";
  if (tab === "data" || (HUB_TOP.some(t => t.id === tab) && hubSubs(tab).length && !hubSubs(tab).some(s => s.id === tab))) tab = (hubSubs(tab)[0] || {id: tab}).id;
  const meta = TAB_META[tab] || HUB_SUB_DEFAULT[tab];
  const top = meta && meta.group ? meta.group : tab;
  const rest = parts.slice(1);
  return {tab, top, route: {tab, view: isReport ? "report" : tab, source: isReport ? (rest[0] || null) : null, path: rest},
          hash: isReport ? "report" + (rest.length ? "/" + rest.map(encodeURIComponent).join("/") : "") : tab + (rest.length ? "/" + rest.map(encodeURIComponent).join("/") : "")};
}
function hubSubs(top) {
  const ids = new Set([...Object.keys(HUB_SUB_DEFAULT).filter(k => HUB_SUB_DEFAULT[k].group === top), ...Object.keys(TAB_META).filter(k => TAB_META[k].group === top)]);
  return [...ids].map(id => {
    const m = TAB_META[id] || {}, d = HUB_SUB_DEFAULT[id] || {};
    return {id, label: m.sub || d.sub || m.label || id, order: d.order ?? m.subOrder ?? 50};
  }).sort((a, b) => a.order - b.order);
}
function hubViewFor(tab) {
  if (VIEWS[tab]) return VIEWS[tab];
  if (tab === "home" && VIEWS.pulse) return VIEWS.pulse;          /* Daily Pulse fills Reports until the Home view lands */
  return null;
}
async function hubPending(root, tab) {
  const t = HUB_TOP.find(x => x.id === tab), s = HUB_SUB_DEFAULT[tab];
  root.innerHTML = `<div class="card hub-pending"><h2>${esc((s && s.sub) || (t && t.label) || tab)}</h2><p style="margin:0">This page is being built. Check back shortly.</p></div>`;
}

function hubNavRefresh() {
  if (!hubShellReady) return;
  const nav = $("#tabs");
  nav.innerHTML = HUB_TOP.map(t => `<button class="tab" type="button" data-tab="${esc(t.id)}"${t.short ? ` aria-label="${esc(t.label)}"` : ""}>${t.short ? `<span class="tl">${esc(t.label)}</span><span class="ts" aria-hidden="true">${esc(t.short)}</span>` : esc(t.label)}</button>`).join("");
  nav.querySelectorAll("button").forEach(b => b.addEventListener("click", () => {
    const top = b.dataset.tab, subs = hubSubs(top);
    const last = state.lastSub && state.lastSub[top];
    setTab(subs.length ? (subs.some(s => s.id === last) ? last : subs[0].id) : top);
    window.scrollTo(0, 0);
  }));
  hubNavMark();
}
function hubNavMark() {
  if (!hubShellReady) return;
  document.querySelectorAll("#tabs .tab").forEach(b => { if (b.dataset.tab === state.top) b.setAttribute("aria-current", "page"); else b.removeAttribute("aria-current"); });
  const subs = hubSubs(state.top), bar = $("#subbar"), sn = $("#subtabs");
  if (subs.length < 2) { bar.hidden = true; sn.innerHTML = ""; return; }
  bar.hidden = false;
  sn.innerHTML = subs.map(s => `<button class="subtab" type="button" data-tab="${esc(s.id)}"${s.id === state.tab ? ' aria-current="page"' : ""}>${esc(s.label)}</button>`).join("");
  sn.querySelectorAll("button").forEach(b => b.addEventListener("click", () => { setTab(b.dataset.tab); window.scrollTo(0, 0); }));
}

async function render() {
  const my = ++seq, main = $("#main");
  hubNavMark();
  if (!db) return;
  const keep = window.scrollY;
  try {
    const box = document.createElement("div"); box.style.display = "contents";
    const view = hubViewFor(state.tab);
    await (view ? view(box) : hubPending(box, state.tab));
    if (my !== seq) return;
    main.replaceChildren(box); window.scrollTo(0, keep);
    if (typeof decorateExplain === "function") { try { decorateExplain(main); } catch (e) { console.error(e); } }
  } catch (e) {
    console.error(e);
    if (my === seq) main.innerHTML = `<div class="banner bad hub-err">${esc(e && e.message || e)}</div>`;
  }
}
function hubApply(r) {
  const prevTab = state.tab;
  state.tab = r.tab; state.top = r.top; state.route = r.route;
  state.lastSub = state.lastSub || {}; if (r.top !== r.tab) state.lastSub[r.top] = r.tab;
  return prevTab !== r.tab;
}
function setTab(t) {
  const r = hubResolve(t);
  hubApply(r);
  try { if (location.hash.slice(1) !== r.hash) history.pushState(null, "", "#" + r.hash); } catch (e) {}
  render();
}
function hubGo(path) { setTab(path); window.scrollTo(0, 0); }
function hubOnHash() {
  const r = hubResolve(location.hash);
  if (r.hash === location.hash.slice(1) && r.tab === state.tab && JSON.stringify(r.route) === JSON.stringify(state.route)) return;
  const changed = hubApply(r);
  try { if (location.hash.slice(1) !== r.hash) history.replaceState(null, "", "#" + r.hash); } catch (e) {}
  render(); if (changed) window.scrollTo(0, 0);
}

hubShellReady = true;
hubApply(hubResolve(location.hash));
hubNavRefresh();
$("#homebtn").addEventListener("click", () => hubGo("home"));
addEventListener("hashchange", hubOnHash);
addEventListener("popstate", hubOnHash);

(async () => {
  db = await (window.claude?.use ? window.claude.use("db") : null);
  if (!db) { $("#main").innerHTML = `<div class="banner">Sign in to claude.ai to load the data. This page reads its figures from the artifact database.</div>`; return; }
  render();
})();
