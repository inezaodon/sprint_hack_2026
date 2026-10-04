/* ---------------- Month-End Close (Business Central > Month-end close) ----------------
   Same documents and numbers as before (close/<YYYY-MM>): stages, sources, control totals, BC journal by document,
   parallel run vs the manual workbook, AR invoices, customer balances, exceptions. Hub look, fewer words. */
async function viewClose(root) {
  const opt = await read("site/options");
  const months = opt.close_months;
  if (!state.closeMonth) state.closeMonth = opt.close_default;
  const d = await read("close/" + state.closeMonth);
  const E = typeof bcEsc === "function" ? bcEsc : esc;
  const money = v => v == null ? "n/a" : (v < 0 ? "−" : "") + "$" + Math.abs(v).toLocaleString("en-US", {minimumFractionDigits: 2, maximumFractionDigits: 2});
  const n0 = v => v == null ? "n/a" : Number(v).toLocaleString("en-US");
  const B = (k, t) => `<span class="bc-badge ${k}">${E(t)}</span>`;
  const st = s => ({ok: B("ok", "OK"), warn: B("warn", "Check"), warning: B("warn", "Check"), bad: B("bad", "Blocked"), error: B("bad", "Error")}[s] || B("info", s));
  const zero = v => Math.abs(v) < .005;
  const diffCell = v => `<td class="num ${zero(v) ? "muted" : "neg"}">${zero(v) ? "0.00" : money(v)}</td>`;
  const tbl = (head, body, id) => `<div class="bc-tbl"><table${id ? ` id="${id}"` : ""}><thead><tr>${head.map((h, i) => `<th${i && !h.startsWith("~") ? "" : ' class="l"'}>${E(h.replace(/^~/, ""))}</th>`).join("")}</tr></thead><tbody>${body}</tbody></table></div>`;
  const sec = (id, title, hint, inner, extra = "") => `<section class="bc-card cl-sec" data-close-section="${id}"><div class="cl-h"><h2>${E(title)}</h2>${extra}</div>${hint ? `<p class="bc-hint">${hint}</p>` : ""}${inner}</section>`;
  const blocking = d.blocking_count, J = d.journal, C = d.comparison;
  const sevRank = s => ({error: 0, warning: 1, info: 2}[s] ?? 3);
  const breaks = d.control_totals.filter(c => c.status !== "ok").length, balBreaks = d.customer_balances.filter(b => b.status !== "ok").length;
  const noWb = C.status === "no_workbook" || !C.workbook;

  root.innerHTML = `<div class="bcv cl">
  <div class="bc-head"><div><h1>Month-end close</h1><p class="bc-sub">Nine inputs, allocation rules, a balanced BC journal and AR invoices. Rules ${E(d.rules_version)}.</p></div>
    <div class="bc-ctl"><label>Month<select id="cm-sel">${months.map(x => `<option value="${E(x.month)}">${E(x.label)}${x.complete ? "" : " (in progress)"}</option>`).join("")}</select></label></div></div>
  <div class="cl-banner ${blocking ? "bad" : "ok"}" data-close-section="status">${blocking ? `${blocking} error${blocking > 1 ? "s" : ""} block approval. Resolve, or approve with an override note in the working app.` : "No blocking errors. Ready to approve in the working app."}${d.approval ? ` Approved${d.approval.by ? " by " + E(d.approval.by) : ""}.` : ""}</div>
  <div class="cl-kpis">
    <div class="bc-card"><span>Journal debits</span><b class="num">${money(J.total_debit)}</b><small>${J.balanced ? B("ok", "Balanced") : B("bad", J.unbalanced_documents + " unbalanced")}</small></div>
    <div class="bc-card"><span>Documents</span><b class="num">${J.documents.length}</b><small>${n0(J.documents.reduce((a, x) => a + x.line_count, 0))} lines</small></div>
    <div class="bc-card"><span>AR invoices</span><b class="num">${money(d.invoices.reduce((a, i) => a + i.total, 0))}</b><small>${d.invoices.length} customers</small></div>
    <div class="bc-card"><span>Exceptions</span><b class="num">${d.exceptions.length}</b><small>${blocking} blocking</small></div>
  </div>
  <div class="cl-steps" data-close-section="stages">${d.stages.map(s => `<div class="bc-card cl-step ${E(s.status)}"><div class="cl-step-h"><b>${E(s.label)}</b>${st(s.status)}</div><small>${E(s.detail || "")}</small></div>`).join("")}</div>

  ${sec("sources", "Sources", "What arrived, and what the rules did with it.", tbl(["Source", "Status", "Rows", "File total", "Journal total", "~Detail"],
    d.sources.map(s => `<tr><td class="l"><b>${E(s.label)}</b><small class="cl-sub">${E(s.workflow)}</small></td><td class="l">${st(s.status)}</td><td class="num">${n0(s.rows)}</td><td class="num">${s.file_total == null ? "n/a" : money(s.file_total)}</td><td class="num">${s.journal_total == null ? "n/a" : money(s.journal_total)}</td><td class="l muted wrap">${E(s.detail)}${s.exceptions ? ` · ${s.exceptions} exception${s.exceptions > 1 ? "s" : ""}` : ""}</td></tr>`).join("")))}

  ${sec("control", "Control totals", "File → loaded → rules → journal. Differences must be zero or explained.", tbl(["Source", "~Measure", "File", "Loaded", "Rules", "Journal", "Diff", "~Status"],
    d.control_totals.map(c => `<tr><td class="l mono">${E(c.source)}</td><td class="l">${E(c.measure)}</td><td class="num">${money(c.file_total)}</td><td class="num">${money(c.loaded_total)}</td><td class="num">${money(c.rules_total)}</td><td class="num">${money(c.journal_total)}</td>${diffCell(c.diff_file_loaded + c.diff_loaded_rules + c.diff_rules_journal)}<td class="l">${c.status === "ok" ? B("ok", "OK") : B("bad", "Break")}</td></tr>`).join("")),
    breaks ? B("bad", breaks + " break" + (breaks > 1 ? "s" : "")) : B("ok", "All tie"))}

  ${sec("journal", "Business Central journal", `Batch GENERAL / ECOM. ${J.balanced ? "Every document balances." : E(J.unbalanced_documents + " document(s) do not balance.")} Debits ${money(J.total_debit)}, credits ${money(J.total_credit)}.`,
    `<div class="cl-docs">${J.documents.map(doc => `<details class="cl-doc"><summary><b class="mono">${E(doc.document_no)}</b>${doc.balanced ? B("ok", "Balanced") : B("bad", "Unbalanced")}${doc.has_placeholder_accounts ? B("warn", "Placeholder accounts") : ""}<span class="muted">${doc.line_count} lines · ${money(doc.total_debit)}</span></summary>
      ${tbl(["Account", "~Description", "~Dept", "Debit", "Credit"], doc.lines.map(l => `<tr><td class="l mono">${E(l.account_no)}${l.placeholder_account ? " *" : ""}</td><td class="l">${E(l.description)}</td><td class="l mono">${E(l.department_code)}</td><td class="num">${l.debit ? money(l.debit) : ""}</td><td class="num">${l.credit ? money(l.credit) : ""}</td></tr>`).join(""))}</details>`).join("")}</div>
    <p class="bc-foot" style="margin-top:8px">* Placeholder account: chart of accounts not yet confirmed by Goodwill.</p>`)}

  ${sec("comparison", "Parallel run vs manual workbook", noWb ? "No manual workbook for this month." : `${n0(C.matches)} lines match. ${C.differences.length} differ.`,
    C.differences.length ? `<details class="cl-more" open><summary>${C.differences.length} difference${C.differences.length > 1 ? "s" : ""}</summary>${tbl(["Document", "Account", "~Description", "System", "Workbook", "Difference"],
      C.differences.map(x => `<tr><td class="l mono">${E(x.doc_no)}</td><td class="l mono">${E(x.account_no)}</td><td class="l wrap">${E(x.description)}${x.explanation ? `<small class="cl-sub">${E(x.explanation)}</small>` : ""}</td><td class="num">${x.system_amount == null ? "—" : money(x.system_amount)}</td><td class="num">${x.workbook_amount == null ? "—" : money(x.workbook_amount)}</td><td class="num neg">${x.difference == null ? "" : money(x.difference)}</td></tr>`).join(""))}</details>` : "",
    noWb ? B("info", "No workbook") : C.differences.length ? B("warn", C.differences.length + " differ") : B("ok", "Matches"))}

  ${sec("invoices", "AR invoices", "", tbl(["Customer", "~Document", "Lines", "Total", "~Ties out"],
    d.invoices.map(i => `<tr><td class="l">${E(i.customer_name)} ${i.placeholder ? B("warn", "Placeholder customer") : ""}</td><td class="l mono">${E(i.external_document_number)}</td><td class="num">${i.lines}</td><td class="num">${money(i.total)}</td><td class="l">${i.ties_out ? B("ok", "Yes") : B("bad", "No")}</td></tr>`).join("")))}

  ${sec("balances", "Customer balances", "Invoice − fees − refunds − payouts = open balance.", tbl(["Marketplace", "Invoice", "Fees", "Refunds", "Payouts", "Open balance", "~Status"],
    d.customer_balances.map(b => `<tr><td class="l">${E(typeof BC_CH_LABEL !== "undefined" && BC_CH_LABEL[b.channel] || b.channel)}</td><td class="num">${money(b.invoice_total)}</td><td class="num">${money(b.fees)}</td><td class="num">${money(b.refunds)}</td><td class="num">${money(b.payouts)}</td><td class="num">${money(b.open_balance)}</td><td class="l">${b.status === "ok" ? B("ok", "OK") : B("bad", "Break")}</td></tr>`).join("")),
    balBreaks ? B("bad", balBreaks + " break" + (balBreaks > 1 ? "s" : "")) : B("ok", "All tie"))}

  ${sec("exceptions", "Exceptions", "Each has an owner. Errors block approval.", (() => {
    const rows = [...d.exceptions].sort((a, b) => sevRank(a.severity) - sevRank(b.severity));
    const tr = e => `<tr><td class="l">${B(e.severity === "error" ? "bad" : e.severity === "warning" ? "warn" : "info", e.severity)}</td><td class="l mono">${E(e.source)}</td><td class="l wrap">${E(e.message)}</td><td class="num">${e.amount == null ? "" : money(e.amount)}</td><td class="l">${E(e.owner)}</td></tr>`;
    const head = ["Severity", "~Source", "~What happened", "Amount", "~Owner"];
    if (!rows.length) return tbl(head, `<tr><td colspan="5" class="l muted">No exceptions.</td></tr>`);
    if (rows.length <= 5) return tbl(head, rows.map(tr).join(""));
    return tbl(head, rows.slice(0, 5).map(tr).join("")) + `<details class="cl-more"><summary>${rows.length - 5} more</summary>${tbl(head, rows.slice(5).map(tr).join(""))}</details>`;
  })())}
  </div>`;
  const sel = root.querySelector("#cm-sel");
  sel.value = state.closeMonth;
  sel.onchange = e => { state.closeMonth = e.target.value; render(); };
}
registerTab("close", "Month-end close", viewClose, 70, {group: "bc", sub: "Month-end close"});
