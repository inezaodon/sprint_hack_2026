/* ---------------- Data Quality ---------------- */
async function viewQuality(root) {
  const q = await read("quality/latest");
  const P = {error: ["p-bad", "Error"], warning: ["p-warn", "Warning"], info: ["p-info", "Info"]};
  const ex = c => { const rows = c.examples || []; if (!rows.length) return ""; const cols = Object.keys(rows[0]);
    return `<div class="scroll"><table><thead><tr>${cols.map(k => `<th>${esc(k)}</th>`).join("")}</tr></thead><tbody>${rows.map(r => `<tr>${cols.map(k => `<td class="mono">${esc(typeof r[k] === "object" ? JSON.stringify(r[k]) : r[k])}</td>`).join("")}</tr>`).join("")}</tbody></table></div>`; };
  root.innerHTML = `
  <section><h2>Data Quality</h2><p class="lead">${q.passed} of ${q.total} checks pass${q.blocking ? `, ${q.blocking} blocking` : ", none blocking"}. Each check is a SQL query that returns the failing rows; zero rows means it passes. Last run ${new Date(q.run_at).toLocaleString("en-US", {dateStyle: "medium", timeStyle: "short"})}.</p></section>
  <section><div class="card">${q.checks.map(c => { const [cl, t] = c.status === "pass" ? ["p-ok", "Pass"] : P[c.severity] || ["p-info", c.severity];
    return `<details><summary>${pill(cl, t)}<b class="mono">${esc(c.check_id)}</b><span class="muted">${c.status === "pass" ? "" : nf(c.failing_rows) + " failing rows"}</span></summary><div style="padding:0 12px 12px"><p class="muted" style="margin:0 0 8px">${esc(c.description)}</p>${ex(c)}</div></details>`; }).join("")}</div></section>
  <section><p class="muted" style="max-width:75ch">The two warnings are expected on this data set. Two SKUs carry a planted typo, so their store credit is held as an exception instead of being guessed. Thirty-three channel-days have no orders, almost all on GoodwillFinds, which sells only a few items a day.</p></section>`;
}
registerTab("quality", "Data Quality", viewQuality, 80);
