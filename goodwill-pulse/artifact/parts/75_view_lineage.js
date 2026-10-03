/* ---------------- Data & Lineage: where the numbers come from and how to verify them ---------------- */
async function viewLineage(root) {
  const lin = typeof exLin === "function" ? await exLin() : {};
  if (!lin.overview && !lin.metrics && !lin.ledger) throw new Error("The lineage documents are not stored yet. Run artifact/export_lineage.py and load its output into the database.");
  const ov = lin.overview || {}, measures = (lin.metrics && lin.metrics.measures) || [], checks = (lin.ledger && lin.ledger.checks) || [];
  const tblRows = t => { const all = [...(ov.sources || []), ...(ov.harmonized || [])]; const h = all.find(x => x.table === t || `${x.db}.${x.table}` === t); return h ? h.rows : null; };
  const ex = ov.exclusions || {};

  const steps = (ov.pipeline || []).map((s, i) => {
    const counts = (s.tables || []).map(t => ({t, n: tblRows(t)})).filter(x => x.n != null);
    return `<li class="ln-box"><span class="ln-n">${i + 1}</span><b>${esc(s.title)}</b><span class="ln-d">${esc(s.detail || "")}</span>${counts.length ? `<span class="ln-c">${counts.map(c => `<span class="mono">${esc(c.t)}</span> ${nf(c.n)} rows`).join("<br>")}</span>` : ""}</li>`;
  }).join("");
  const pipe = `<ol class="ln-pipe" aria-label="Data pipeline">${steps}<li class="ln-box ln-end"><span class="ln-n">${(ov.pipeline || []).length + 1}</span><b>This page</b><span class="ln-d">Reads ${nf((ov.artifact_docs || []).length)} stored documents. Numbers are summed in your browser.</span></li></ol>
    <p class="ln-ex"><b>Left out on purpose:</b> ${nf(ex.test_orders)} test orders, ${nf(ex.test_skus)} test SKUs and ${nf(ex.canceled_orders)} canceled orders never reach a total.${ov.builds && ov.builds.quality ? ` Data checks on the last build: ${nf(ov.builds.quality.passed)} of ${nf(ov.builds.quality.total)} passed.` : ""}</p>`;

  const tableCard = t => `<details class="ln-tbl"><summary><span class="mono">${esc(t.db ? t.db + "." : "")}${esc(t.table)}</span><span class="num">${nf(t.rows)} rows</span><span class="muted">${esc(t.grain || "")}</span></summary>
    <div class="ln-tb"><p class="muted">${esc(t.description || "")}</p><p class="mono ln-cols">${(t.columns || []).map(c => `${esc(c.name)} <span class="muted">${esc(c.type)}</span>`).join(" · ")}</p></div></details>`;
  const docs = (ov.artifact_docs || []).slice().sort((a, b) => a.path.localeCompare(b.path));
  const db = `<div class="grid g2" style="align-items:start"><div class="card"><div class="rowlab" style="padding:12px 12px 0">Online artifact database (what this page reads)</div><div class="scroll"><table><thead><tr><th>Document</th><th class="r">Size</th><th>What it holds</th></tr></thead><tbody>${docs.map(d => `<tr><td class="mono">${esc(d.path)}</td><td class="r num">${nf(d.bytes / 1024, 1)} KB</td><td>${esc(d.what)}</td></tr>`).join("") || `<tr><td colspan="3" class="muted">Not listed yet.</td></tr>`}</tbody></table></div></div>
    <div class="card"><div class="rowlab" style="padding:12px 12px 0">Warehouse (source databases and harmonized tables)</div>${(ov.sources || []).map(tableCard).join("")}${(ov.harmonized || []).map(tableCard).join("")}${!(ov.sources || []).length && !(ov.harmonized || []).length ? `<div class="empty">Not listed yet.</div>` : ""}</div></div>`;

  const nMatch = checks.filter(c => c.status === "match").length, maxP = Math.max(2, ...checks.map(c => c.paths.length));
  const fv = (c, v) => exFmt(exUnit(lin, c.measure), v);
  const ledger = checks.length ? `<p class="ln-head"><b id="ln-count">${nMatch} of ${checks.length} independent checks match</b> ${pill(nMatch === checks.length ? "p-ok" : "p-warn", nMatch === checks.length ? "All agree" : `${checks.length - nMatch} differ`)}</p>
    <div class="card scroll"><table class="ln-ledger"><thead><tr><th>Check</th>${Array.from({length: maxP}, (_, i) => `<th class="r">Path ${i + 1}</th>`).join("")}<th class="r">On this page</th><th>Status</th><th></th></tr></thead><tbody>
    ${checks.map(c => `<tr><td>${esc(c.title)}<div class="muted ln-sm">${esc(c.measure)}</div></td>${Array.from({length: maxP}, (_, i) => { const p = c.paths[i]; return `<td class="r num">${p ? `${esc(fv(c, p.value))}<div class="muted ln-sm">${esc(p.label)}</div>` : ""}</td>`; }).join("")}<td class="r num">${esc(fv(c, c.page_value))}<div class="muted ln-sm">${esc(c.page_source || "")}</div></td><td>${exStatusPill(c.status)}${c.status === "diff" ? `<div class="muted ln-sm">off by ${esc(nf(c.diff, 4))}</div>` : ""}</td><td><button type="button" class="ex-btn" data-lncheck="${esc(c.id)}">Open audit card</button></td></tr>`).join("")}</tbody></table></div><div id="ln-card" class="ln-cardslot" aria-live="polite"></div>`
    : `<div class="card empty">No independent checks are stored yet.</div>`;

  const defCard = m => `<details class="ln-def card" data-mid="${esc(m.id)}"><summary><b>${esc(m.label)}</b>${pill("p-info", m.kind === "kpi" ? "KPI" : m.kind === "daily" ? "Daily" : "Pipeline")}<span class="muted">${(m.synonyms || []).map(esc).join(", ")}</span></summary>
    <div class="ln-db"><p><span class="muted">Formula:</span> ${esc(m.formula_text || "")}</p>${m.sql ? exSqlBlock(m.sql) : ""}<p class="muted ln-sm">Source tables: ${(m.source_tables || []).map(t => `<span class="mono">${esc(t)}</span>`).join(", ") || "none listed"}${m.columns_used && m.columns_used.length ? ` · columns: ${m.columns_used.map(esc).join(", ")}` : ""}</p></div></details>`;

  let ups = [], prov = [];
  try { ups = (await read("uploads/index")).items || []; } catch (e) {}
  if (typeof uploadProvenance === "function") { try { prov = await uploadProvenance(); } catch (e) {} }
  const changed = typeof uploadProvenance === "function" || ups.length ? `<section><h2>What changed the numbers</h2><p class="lead">An uploaded Excel file replaces the warehouse total for each day and marketplace it covers. Each replaced cell is listed with the difference.</p>
    ${ups.length ? `<div class="card scroll"><table><thead><tr><th>File</th><th>Covers</th><th class="r">Rows</th><th>Status</th><th></th></tr></thead><tbody>${ups.map(u => `<tr><td>${esc(u.name)}</td><td>${esc(u.from || "")} to ${esc(u.to || "")}</td><td class="r num">${nf(u.rows)}</td><td>${esc(u.status || "")}</td><td><button type="button" class="ex-btn" data-lnupload="${esc(u.id)}">Open audit card</button></td></tr>`).join("")}</tbody></table></div><div id="ln-upcard" class="ln-cardslot"></div>` : `<div class="card empty">No file has been uploaded. Every number comes from the warehouse.</div>`}
    ${prov.length ? `<div class="card scroll" style="margin-top:12px"><table><thead><tr><th>Day</th><th>Marketplace</th><th>File</th><th class="r">Warehouse item sales</th><th class="r">File item sales</th><th class="r">Difference</th></tr></thead><tbody>${prov.slice(0, 60).map(p => `<tr><td>${esc(shortDay(p.date))}</td><td>${esc(CHN[p.channel] || p.channel)}</td><td>${esc(p.name || p.upload_id)}</td><td class="r num">${usd(p.warehouse && p.warehouse.item_sales, 2)}</td><td class="r num">${usd(p.upload && p.upload.item_sales, 2)}</td><td class="r num">${usd(p.diff && p.diff.item_sales, 2)}</td></tr>`).join("")}</tbody></table></div>${prov.length > 60 ? `<p class="muted ln-sm">Showing 60 of ${prov.length} replaced cells.</p>` : ""}` : ""}</section>` : "";

  root.innerHTML = `
  <section><h2>Where the numbers come from</h2><p class="lead">Source systems on the left, this page on the right. Row counts are shown at each step.</p>${pipe}</section>
  <section><h2>The database</h2><p class="lead">What this page reads, and the warehouse it was built from. Open a table to see its columns.</p>${db}</section>
  <section><h2>Verification ledger</h2><p class="lead">Each headline number is recomputed a second way, straight from the native source databases. Open a row to see the queries, rows and both values.</p>${ledger}</section>
  <section><h2>Measure definitions</h2><p class="lead">One definition per measure. Type a name you use, such as top line or postage, to find the canonical measure.</p>
    <label class="f" style="max-width:420px">Search measures<input type="search" id="ln-q" autocomplete="off" placeholder="top line, postage, margin" style="font:500 15px var(--body);padding:10px 12px;border:1px solid var(--line);border-radius:6px;background:var(--surface);color:var(--ink)"></label>
    <p class="muted ln-sm" id="ln-n" aria-live="polite"></p><div id="ln-defs" class="ln-defs"></div></section>
  ${changed}
  <section><h2>How to read this</h2><div class="card pad ln-how"><p><b>No number on this page is produced by a model.</b> When you ask a question, Claude may read it and pick a measure, grouping and period. Code then adds up stored rows and shows the result. A model never writes, rounds or estimates a figure.</p><p>Every figure can be walked down: the displayed value, the formula, the query, the tables and rows behind it, and a second calculation from the native source databases. A match means the two agree within the stated tolerance. A difference is shown, not hidden.</p></div></section>`;

  const list = $("#ln-defs", root), cnt = $("#ln-n", root);
  const paint = () => {
    const q = $("#ln-q", root).value.trim().toLowerCase(), words = q.split(/\s+/).filter(Boolean);
    const hit = measures.filter(m => { const hay = [m.id, m.label, ...(m.synonyms || []), m.formula_text, ...(m.source_tables || [])].join(" ").toLowerCase(); return words.every(w => hay.includes(w)); });
    list.innerHTML = hit.length ? hit.map(defCard).join("") : `<div class="card empty">No measure matches “${esc(q)}”.</div>`;
    cnt.textContent = `${hit.length} of ${measures.length} measures`;
  };
  paint(); $("#ln-q", root).addEventListener("input", paint);
  const open = async (slot, kind, pay) => { slot.innerHTML = `<div class="muted">Loading…</div>`; slot.innerHTML = await explainHTML(kind, pay); try { slot.scrollIntoView({block: "nearest"}); } catch (e) {} };
  root.addEventListener("click", e => {
    const c = e.target.closest("[data-lncheck]"), u = e.target.closest("[data-lnupload]");
    if (c) open($("#ln-card", root), "check", {id: c.dataset.lncheck});
    if (u) open($("#ln-upcard", root), "upload", {id: u.dataset.lnupload});
  });
}
registerTab("lineage", "Data & Lineage", viewLineage, 60);
