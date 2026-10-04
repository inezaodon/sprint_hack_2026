// Revenue Hub checks: BC nightly view + journal, home and report buttons, dialogs, routing.
// .venv/bin/python artifact/test_page.py --dist DIST --driver artifact/tests/driver_hub.js
const out = {checks: []}, M = () => document.querySelector('#main');
const ck = (m, ok, v) => out.checks.push({ok: !!ok, m, ...(v === undefined ? {} : {v})});
const goTab = async t => { setTab(t); await sleep(1000); };
const waitFor = async (fn, ms = 8000) => { const t0 = Date.now(); while (Date.now() - t0 < ms) { try { const v = fn(); if (v) return v; } catch (e) {} await sleep(100); } return null; };

// journal: balanced and revenue credits equal hub/days item sales, last 10 nights
const days = await hubDays(), last = days.reduce((a, r) => r.date > a ? r.date : a, '');
for (let i = 0; i < 10; i++) {
  const d = addDays(last, -i), j = await bcJournal(d);
  const rev = Math.round(-j.lines.filter(l => / item sales/.test(l.description)).reduce((a, l) => a + l.amount, 0) * 100);
  const hub = Math.round(days.filter(r => r.date === d).reduce((a, r) => a + r.item_sales, 0) * 100);
  const sum = Math.round(j.lines.reduce((a, l) => a + l.amount, 0) * 100);
  ck('journal ' + d + ' balanced', j.balanced && sum === 0, [j.debit, j.credit]);
  ck('journal ' + d + ' revenue = hub item sales', rev === hub, [rev, hub]);
}

// BC view
await goTab('bc');
ck('bc view renders lines', document.querySelectorAll('#bc-lines tbody tr').length > 5);
ck('bc balanced text', /Balanced/.test((document.querySelector('#bc-bal') || {}).innerText || ''));
ck('bc JSON parses', (() => { const p = JSON.parse(document.querySelector('#bc-json').textContent); return p.lines.length && p.lines.every(l => l.accountNumber && l.postingDate); })());
ck('bc post button disabled', [...M().querySelectorAll('button')].some(b => /Post to Business Central/.test(b.innerText) && b.disabled));
ck('bc sub-tabs: Nightly first, Month-end close second', [...document.querySelectorAll('#subtabs button')].map(b => b.innerText).join('|') === 'Nightly|Month-end close');
const prev = state.bcDate; setVal('#bc-day', addDays(last, -3)); await sleep(900);
ck('bc date picker changes the journal', state.bcDate === addDays(last, -3) && new RegExp(shortDay(addDays(last, -3)).replace(' ', ' ')).test(M().innerText));
setVal('#bc-day', '2001-01-01'); await sleep(900);
ck('bc date clamped to data', state.bcDate >= '2024-01-01' && /Data runs/.test(document.querySelector('#bc-msg').innerText), state.bcDate);
state.bcDate = prev;

// home buttons + dialogs
await goTab('home');
for (const s of ['[data-bc]', '[data-up]', '[data-raw]', '[data-send]']) ck('home has ' + s, M().querySelector(s));
click('#main [data-send]'); const sd = await waitFor(() => document.querySelector('#hub-send[open]'));
ck('send dialog opens', sd);
ck('send dialog: To CEO, COO, CFO, enterprise total', sd && /CEO, COO, CFO/.test(sd.innerText) && /Enterprise sales/.test(sd.innerText));
if (sd) sd.close();
click('#main [data-raw]'); const rd = await waitFor(() => document.querySelector('#hub-raw[open]'));
ck('raw dialog opens with four sources', rd && rd.querySelectorAll('.srcpick input').length === 4);
if (rd) { rd.querySelector('[data-per="month"]').click(); await sleep(500); ck('raw dialog monthly period', /Monthly/.test(rd.innerText) && rd.querySelector('input[type=month]')); rd.close(); }
click('#main [data-up]'); await sleep(900);
ck('upload button opens Upload', state.tab === 'upload' && document.querySelector('#upl-file'));

// report: buttons stamped with range and source; drill to a store; presets
await goTab('report/supro');
const rb = M().querySelector('[data-raw]');
ck('report raw button has range + source', rb && rb.dataset.source === 'supro' && rb.dataset.from && rb.dataset.to, rb && {...rb.dataset});
click('#main [data-raw]'); const rd2 = await waitFor(() => document.querySelector('#hub-raw[open]'));
ck('raw dialog from report preselects the source', rd2 && [...rd2.querySelectorAll('.srcpick input:checked')].map(i => i.value).join() === 'supro');
if (rd2) rd2.close();
await goTab('report/upright');
click('#main [data-preset="last7"]'); await sleep(900);
ck('7 days preset', /7 days|By night/.test(M().innerText) && state.rng.preset === 'last7');
const tr = M().querySelector('#rp-stores tr[data-store]'); tr.click(); await sleep(1000);
ck('store drill-in', /report\/upright\/all\//.test(location.hash) && M().querySelector('#rp-title'), location.hash);
await goTab('home');
document.querySelector('#rp-askq').value = 'How did we do last night?'; document.querySelector('#rp-askform').requestSubmit(); await sleep(1500);
ck('home ask bar routes to Ask', state.tab === 'ask' && document.querySelectorAll('.ask-q').length >= 1);

done({ok: out.checks.every(c => c.ok) && !__errs.length, failed: out.checks.filter(c => !c.ok), n: out.checks.length});
