/* ---------------- Data Quality (Hub Data > Quality) ---------------- */
async function viewQuality(root) {
  const q = await read("quality/latest");
  const P = {error: ["p-bad", "Error"], warning: ["p-warn", "Warning"], info: ["p-info", "Info"]};
  const failing = q.checks.filter(c => c.status !== "pass"), nErr = failing.filter(c => c.severity === "error").length, nWarn = failing.length - nErr;
  const ex = c => { const rows = c.examples || []; if (!rows.length) return ""; const cols = Object.keys(rows[0]);
    return `<div class="scroll qy-ex"><table><thead><tr>${cols.map(k => `<th>${esc(k)}</th>`).join("")}</tr></thead><tbody>${rows.map(r => `<tr>${cols.map(k => `<td class="mono">${esc(typeof r[k] === "object" ? JSON.stringify(r[k]) : r[k])}</td>`).join("")}</tr>`).join("")}</tbody></table></div>`; };
  const ord = c => c.status === "pass" ? 3 : c.severity === "error" ? 0 : c.severity === "warning" ? 1 : 2;
  const checks = q.checks.slice().sort((a, b) => ord(a) - ord(b));
  const when = new Date(q.run_at).toLocaleString("en-US", {dateStyle: "medium", timeStyle: "short"});
  const tile = (l, v, cls, sub) => `<div class="metric"><div class="l">${esc(l)}</div><div class="v num ${cls || ""}">${v}</div>${sub ? `<div class="muted qy-sm">${esc(sub)}</div>` : ""}</div>`;
  root.innerHTML = `<div class="qy">
  <div class="rhead"><div><h1>Data quality</h1><p class="sub">${q.passed} of ${q.total} checks pass${q.blocking ? `, ${q.blocking} blocking` : ", none blocking"}. Last run ${esc(when)}.</p></div></div>
  <div class="metrics qy-tiles">${tile("Checks passed", `${nf(q.passed)} of ${nf(q.total)}`)}${tile("Blocking", nf(q.blocking || 0), q.blocking ? "qy-bad" : "qy-good", q.blocking ? "do not rely on totals" : "numbers can be used")}${tile("Errors", nf(nErr), nErr ? "qy-bad" : "")}${tile("Warnings", nf(nWarn), nWarn ? "qy-warn" : "")}</div>
  <section class="card"><h2>Checks</h2><p class="hint">Each check is a SQL query for failing rows. Zero rows means pass.</p>
  ${checks.map(c => { const [cl, t] = c.status === "pass" ? ["p-ok", "Pass"] : P[c.severity] || ["p-info", c.severity];
    return `<details class="qy-check" data-check="${esc(c.check_id)}"><summary>${pill(cl, t)}<b class="mono">${esc(c.check_id)}</b><span class="muted qy-sm">${c.status === "pass" ? "" : nf(c.failing_rows) + " failing rows"}</span></summary><div class="qy-body"><p class="muted">${esc(c.description)}</p>${ex(c)}</div></details>`; }).join("")}
  <details class="qy-why"><summary>Why the warnings are expected</summary><ul class="math"><li><b>Planted SKU typos</b><span>Two SKUs carry a typo, so their store credit is held as an exception instead of being guessed.</span></li><li><b>Empty channel-days</b><span>Thirty-three channel-days have no orders, almost all on GoodwillFinds, which sells only a few items a day.</span></li></ul></details></section></div>`;
}
registerTab("quality", "Quality", viewQuality, 80, {group: "data", sub: "Quality"});
