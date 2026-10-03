/* ---------------- shell ---------------- */
let seq = 0;
async function render() {
  const my = ++seq, main = $("#main");
  document.querySelectorAll("#tabs button").forEach(b => b.setAttribute("aria-selected", b.dataset.tab === state.tab));
  if (!db) return;
  const keep = window.scrollY;
  try {
    const box = document.createElement("div"); box.style.display = "contents";
    await VIEWS[state.tab](box);
    if (my !== seq) return;
    main.replaceChildren(box); window.scrollTo(0, keep);
    if (typeof decorateExplain === "function") decorateExplain(main);
  } catch (e) {
    if (my === seq) main.innerHTML = `<div class="banner bad">${esc(e.message)}</div>`;
  }
}
function setTab(t) {
  state.tab = VIEWS[t] ? t : "pulse";
  try { history.replaceState(null, "", "#" + state.tab); } catch (e) {}
  render();
}
function buildNav() {
  const nav = $("#tabs"); nav.innerHTML = TABS.map(t => `<button role="tab" data-tab="${esc(t.id)}">${esc(t.label)}</button>`).join("");
  nav.querySelectorAll("button").forEach(b => b.addEventListener("click", () => { setTab(b.dataset.tab); window.scrollTo(0, 0); }));
  state.tab = VIEWS[location.hash.slice(1)] ? location.hash.slice(1) : "pulse";
  nav.querySelectorAll("button").forEach(b => b.setAttribute("aria-selected", b.dataset.tab === state.tab));
}
buildNav();
$("#foot").textContent = "All figures are synthetic and modeled on the report layouts in Goodwill Michiana's kickoff deck. No real Goodwill data is used. Numbers are read from this page's database, which was built from the harmonized SQL warehouse (58,962 orders across five marketplaces, September 2025 to October 2026).";

(async () => {
  db = await (window.claude?.use ? window.claude.use("db") : null);
  if (!db) { $("#main").innerHTML = `<div class="banner">Sign in to claude.ai to load the data. This page reads its figures from the artifact database.</div>`; return; }
  render();
})();
