/* ---------------- Business Central > Nightly: import file, journal-lines API preview, account mapping, go-live steps ----------------
   Journal lines for one posting date are built here from hub/days (uploads applied) and supro/<month>. Nothing is posted.
   Account map mirrors goodwill-pulse/config/close_rules.yaml (accounts + channels). Revenue, customer, shipping income, fees and
   refunds accounts are placeholders there too; retail sales and store clearing have no account in the close rules yet. */
const BC_ACCT = {
  shipping_income: {no: "4xxxx-SHIPINC", name: "Shipping and handling income", ph: true},
  refunds: {no: "4xxxx-REFUNDS", name: "Sales returns and refunds", ph: true},
  fees: {no: "6xxxx-FEES", name: "Marketplace and payment fees", ph: true},
  retail: {no: "4xxxx-RETAIL", name: "Retail store sales", ph: true},
  store_clearing: {no: "1xxxx-STORECLR", name: "Store deposits clearing", ph: true},
};
const BC_CH_ACCT = {
  shopgoodwill: {revenue: "4xxxx-SGW", customer: "CUST-SGW"}, ebay: {revenue: "4xxxx-EBAY", customer: "CUST-EBAY"}, amazon: {revenue: "4xxxx-AMZ", customer: "CUST-AMZ"},
  goodwillfinds: {revenue: "4xxxx-GWF", customer: "CUST-GWF"}, goodwillbooks: {revenue: "4xxxx-GWB", customer: "CUST-GWB"}};
const BC_ACCT_CONFIRMED = [["10009", "Shipping postage (OSM, Pitney Bowes, EasyPost) paid from 1st Source"], ["0101", "1st Source operating account (payouts land here)"],
  ["40356", "FedEx shipping charges and refunds, Dept 180"], ["V00122", "FedEx vendor"]];
const bcR2 = v => Math.round((+v || 0) * 100) / 100;
const bcMoney = v => (v < 0 ? "−" : "") + "$" + Math.abs(v).toLocaleString("en-US", {minimumFractionDigits: 2, maximumFractionDigits: 2});

async function bcJournal(date) {
  const days = (await hubDays()).filter(r => r.date === date), sup = (await hubSupro(date, date)).filter(r => r.date === date);
  const doc = "ECOM-" + date.replace(/-/g, ""), L = [];
  const push = (acct, accountType, amt, desc, ch) => { const a = bcR2(amt); if (!a) return;
    L.push({lineNumber: (L.length + 1) * 10000, accountType, accountNumber: acct.no, postingDate: date, documentNumber: doc, amount: a, description: desc, _acct: acct, _ch: ch}); };
  for (const ch of CHORDER) {
    const rs = days.filter(r => r.channel === ch); if (!rs.length) continue;
    const v = {item_sales: 0, shipping: 0, fees: 0, refunds: 0}; for (const r of rs) for (const k in v) v[k] += +r[k] || 0;
    const name = CHN[ch], A = BC_CH_ACCT[ch], up = rs.some(r => String(r.src || "").startsWith("upload:")) ? " (uploaded file)" : "", start = L.length;
    push({no: A.revenue, name: name + " sales", ph: true}, "G/L Account", -v.item_sales, `${name} item sales${up}`, ch);
    push(BC_ACCT.shipping_income, "G/L Account", -v.shipping, `${name} shipping charged`, ch);
    push(BC_ACCT.fees, "G/L Account", v.fees, `${name} marketplace fees`, ch);
    push(BC_ACCT.refunds, "G/L Account", v.refunds, `${name} refunds`, ch);
    push({no: A.customer, name: name + " receivable", ph: true}, "Customer", -L.slice(start).reduce((a, l) => a + l.amount, 0), `${name} payout due`, ch);
  }
  if (sup.length) {
    const sales = sup.reduce((a, r) => a + (+r.sales || 0), 0), est = sup.some(r => r.est);
    push(BC_ACCT.retail, "G/L Account", -sales, `Retail store sales, ${sup.filter(r => +r.sales).length} stores${est ? " (estimated)" : ""}`, "stores");
    push(BC_ACCT.store_clearing, "G/L Account", sales, "Store deposits due", "stores");
  }
  const debit = bcR2(L.filter(l => l.amount > 0).reduce((a, l) => a + l.amount, 0)), credit = bcR2(-L.filter(l => l.amount < 0).reduce((a, l) => a + l.amount, 0));
  return {date, doc, lines: L, debit, credit, balanced: Math.abs(debit - credit) < 0.005, placeholders: L.filter(l => l._acct.ph).length, estimated: sup.some(r => r.est), stores: sup.length > 0};
}

const BC_STEPS = [
  ["done", "Build the totals and journal lines", "The hub adds up every line nightly and checks that it balances.", "Team"],
  ["done", "Preview the API payload", "This page shows tonight's lines in the journal lines API shape.", "Team"],
  ["", "Register an app in Microsoft Entra", "Goodwill IT creates the app and shares the tenant ID, client ID and secret.", "Goodwill IT"],
  ["", "Allow the app in Business Central", "Add it on the Microsoft Entra Applications page with journal posting permissions only.", "Goodwill IT"],
  ["", "Fill the account map", "Replace placeholder accounts with the ones from the E-Commerce Allocation workbook.", "Amanda"],
  ["", "Test in a sandbox company", "Post one night, compare with the workbook, then switch from preview to post.", "Team + Amanda"]];

async function viewBC(root) {
  const days = await hubDays();
  const sr = await hubSuproRange();
  const first = [days.reduce((a, r) => !a || r.date < a ? r.date : a, ""), sr.first].filter(Boolean).sort()[0];
  const last = [days.reduce((a, r) => r.date > a ? r.date : a, ""), sr.last].filter(Boolean).sort().reverse()[0];
  if (!last) { root.innerHTML = `<div class="card"><h2>Business Central</h2><p class="hint">No data is stored yet.</p></div>`; return; }
  let msg = state.bcMsg || ""; state.bcMsg = "";
  if (!state.bcDate) state.bcDate = days.reduce((a, r) => r.date > a ? r.date : a, "") || last;
  if (state.bcDate < first || state.bcDate > last) { msg = `Data runs ${shortDay(first)}, ${first.slice(0, 4)} to ${shortDay(last)}, ${last.slice(0, 4)}.`; state.bcDate = state.bcDate < first ? first : last; }
  const d = state.bcDate, j = await bcJournal(d);
  const payload = {environment: "<goodwill-environment>", company: "<goodwill-company-id>", journal: "<journal-id>",
    endpoint: "POST https://api.businesscentral.dynamics.com/v2.0/{tenant}/{environment}/api/v2.0/companies({companyId})/journals({journalId})/journalLines",
    lines: j.lines.map(({_acct, _ch, ...x}) => x)};
  const json = JSON.stringify(payload, null, 2);
  const rows = j.lines.map(l => `<tr><td class="num">${l.lineNumber}</td><td class="l"><span class="${l._acct.ph ? "ph" : "cf"}">${esc(l.accountNumber)}</span> ${esc(l._acct.name)}</td><td class="l">${esc(l.description)}</td><td class="num">${l.amount > 0 ? bcMoney(l.amount) : ""}</td><td class="num">${l.amount < 0 ? bcMoney(-l.amount) : ""}</td></tr>`).join("");
  const map = [["Confirmed", BC_ACCT_CONFIRMED.map(([n, t]) => `<span class="cf">${esc(n)}</span> ${esc(t)}`).join(". ") + ". These post at month-end in the close, not nightly."],
    ["Placeholders", `<span class="ph">${esc(Object.values(BC_CH_ACCT).map(a => a.revenue + ", " + a.customer).join(", "))}, ${esc(Object.values(BC_ACCT).map(a => a.no).join(", "))}</span>. Fill these from the E-Commerce Allocation workbook.`],
    ["Per channel", "Credit sales and shipping income, debit fees and refunds, and debit the marketplace customer for the payout due. Each channel nets to zero."],
    ["Stores", "Supro sales are already net of returns: credit retail sales, debit store deposits clearing." + (j.estimated ? ` <span class="ph">This night's store sales are estimated.</span>` : "")],
    ["Dimensions", "Department and channel dimensions get attached per line once the account map is confirmed."]];
  root.innerHTML = `<div class="bcn">
    <div class="rhead"><div><h1>Business Central</h1><p class="sub">An import file today. A direct API push once Goodwill IT registers the app.</p></div>
      <div class="controls rangectl"><div class="rangebox"><label>Posting date<input type="date" class="pill" id="bc-day" min="${first}" max="${last}" value="${d}"></label></div>
        <p class="rangemsg" id="bc-msg" aria-live="polite">${esc(msg)}</p></div></div>
    <div class="bcgrid">
      <div class="card conn"><div class="ic ok" aria-hidden="true">✓</div><div><h2>Import file</h2><p>Working now. Download the .xlsx and import it through a configuration package.</p>
        <button type="button" class="pill" data-bc data-from="${d}" data-to="${d}" id="bc-dl" style="margin-top:10px">Download Business Central file</button></div></div>
      <div class="card conn"><div class="ic wait" aria-hidden="true">…</div><div><h2>Direct API push</h2><p>Previewed below. Posting waits on an app registration in Microsoft Entra.</p>
        <button type="button" class="pill primary" disabled title="Needs the Entra app registration first" style="margin-top:10px">Post to Business Central</button></div></div>
    </div>
    <div class="card" id="bc-card">
      <h2>What the hub would post for ${esc(longDay(d))}</h2>
      <p class="hint">General journal lines, one journal per night, in the shape of the journal lines API. Nothing is sent.</p>
      <div class="endpoint"><span class="verb">POST</span><span>/companies({companyId})/journals({journalId})/journalLines</span></div>
      ${j.lines.length ? `<div class="tbl-wrap"><table id="bc-lines"><thead><tr><th>Line</th><th class="l">Account</th><th class="l">Description</th><th>Debit</th><th>Credit</th></tr></thead><tbody>${rows}
        <tr class="sum"><td></td><td class="l">Totals</td><td></td><td class="num">${bcMoney(j.debit)}</td><td class="num">${bcMoney(j.credit)}</td></tr></tbody></table></div>
      <div class="bal" id="bc-bal"><span>${j.lines.length} lines</span><span>Document <b>${esc(j.doc)}</b></span><span class="${j.balanced ? "cf" : "ph"}">${j.balanced ? "Balanced: debits equal credits" : "Out of balance by " + bcMoney(Math.abs(j.debit - j.credit))}</span>${j.placeholders ? `<span class="ph">${j.placeholders} lines use placeholder accounts</span>` : ""}${j.stores ? "" : `<span class="ph">No Supro store report for this night</span>`}</div>`
        : `<p class="empty">No sales stored for this night.</p>`}
      <details><summary>Show the JSON payload</summary><pre class="json" id="bc-json">${esc(json)}</pre><button type="button" class="pill" id="bc-copy" style="margin-top:10px">Copy JSON</button></details>
      <details><summary>Account mapping</summary><ul class="math" id="bc-map">${map.map(([a, b]) => `<li><b>${esc(a)}</b><span>${b}</span></li>`).join("")}</ul></details>
    </div>
    <div class="card" style="margin-top:16px"><h2>Steps to go live</h2><p class="hint">Most of what is left is access, not code.</p>
      <ol class="csteps" id="bc-steps">${BC_STEPS.map(([st, b, t, w], i) => `<li class="${st}"><div class="no">${st ? "✓" : i + 1}</div><div><b>${esc(b)}</b><span class="d">${esc(t)}</span></div><span class="who-tag">${esc(w)}</span></li>`).join("")}</ol></div>
  </div>`;
  const inp = $("#bc-day", root);
  inp.onchange = () => { const v = inp.value; if (!v) return; if (v < first || v > last) state.bcMsg = `Data runs ${shortDay(first)}, ${first.slice(0, 4)} to ${shortDay(last)}, ${last.slice(0, 4)}; the date was adjusted.`; state.bcDate = v < first ? first : v > last ? last : v; render(); };
  $("#bc-copy", root).onclick = async () => {
    try { await navigator.clipboard.writeText(json); hubToast("Payload copied"); }
    catch (e) { try { const r = document.createRange(); r.selectNodeContents($("#bc-json")); const s = getSelection(); s.removeAllRanges(); s.addRange(r); } catch (e2) {} hubToast("Selected. Press Ctrl+C to copy."); }
  };
  // The page-wide data-bc handler (15_hub.js) reads data-from/data-to on #bc-dl.
  state.hubRange = {from: d, to: d};
}
registerTab("bc", "Business Central", viewBC, 65, {group: "bc", sub: "Nightly"});
