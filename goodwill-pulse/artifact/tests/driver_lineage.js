// Lineage tab + audit card checks. Run: test_page.py --driver artifact/tests/driver_lineage.js [--shot x.png --hash lineage]
const out = {}, M = () => document.querySelector('#main'), vis = e => !!e && e.offsetParent !== null;
const num = s => parseFloat(String(s).replace(/[^0-9.\-]/g, ''));
const hold = (c, m) => { out.checks = out.checks || []; out.checks.push({ok: !!c, m}); };
try { localStorage.setItem('gp_explain_mode', 'quick'); } catch (e) {}
click('[data-tab=lineage]'); await sleep(1200);
out.sections = [...M().querySelectorAll('h2')].map(h => h.innerText);
hold(out.sections.length >= 5, 'five or more sections');
out.pipeBoxes = M().querySelectorAll('.ln-box').length; hold(out.pipeBoxes >= 2, 'pipeline boxes');
out.ledgerHead = (M().querySelector('#ln-count') || {}).innerText; hold(/\d+ of \d+ independent checks match/.test(out.ledgerHead || ''), 'ledger headline');
out.docRows = M().querySelectorAll('.ln-tbl').length; hold(out.docRows >= 1, 'warehouse tables');
// open audit card from the first ledger row
const first = M().querySelector('[data-lncheck]'); out.firstCheck = first && first.dataset.lncheck; first.click(); await sleep(900);
const card = M().querySelector('#ln-card .ex-card'); hold(!!card, 'audit card opened from ledger');
out.cardMode0 = card.dataset.mode;
const steps = () => [...card.querySelectorAll('.ex-step')].filter(vis).length;
const len = () => card.innerText.length;
out.quick = {steps: steps(), len: len(), sqlVisible: vis(card.querySelector('.ex-sql'))};
click('[data-exmode=full]'); await sleep(300);
out.full = {steps: steps(), len: len(), sqlVisible: vis(card.querySelector('.ex-sql'))};
hold(out.full.steps > out.quick.steps && out.full.len > out.quick.len && out.full.sqlVisible && !out.quick.sqlVisible, 'quick vs full differ');
out.persisted = (() => { try { return localStorage.getItem('gp_explain_mode'); } catch (e) { return 'n/a'; } })(); hold(out.persisted === 'full', 'mode remembered');
// drill total equals displayed
const cell = card.querySelector('.ex-cell');
if (cell) { const sp = [...cell.querySelectorAll('summary span')].map(s => s.innerText); out.cellSummary = sp; const page = num(sp.find(s => /^Page/.test(s))), rec = num(sp.find(s => /add to/.test(s))); hold(Math.abs(page - rec) <= 0.01, 'full-day sum equals displayed (' + page + ' vs ' + rec + ')'); }
else hold(false, 'drill cell present');
// every drill cell on the report card: full-day sum equals page figure; truncated cells carry the label
// search
setVal('#ln-q', 'top line', 'input'); await sleep(200);
out.searchTop = [...M().querySelectorAll('.ln-def summary b')].map(b => b.innerText); hold(out.searchTop.some(t => /item sales/i.test(t)), 'top line finds item sales');
setVal('#ln-q', 'postage', 'input'); await sleep(200);
out.searchPost = [...M().querySelectorAll('.ln-def summary b')].map(b => b.innerText); hold(out.searchPost.some(t => /shipping/i.test(t)), 'postage finds shipping');
setVal('#ln-q', 'zzzz', 'input'); await sleep(200); hold(/No measure matches/.test(M().innerText), 'no-match message');
setVal('#ln-q', '', 'input');
// report card on Pulse
click('[data-tab=pulse]'); await sleep(1000);
const btns = M().querySelectorAll('.ex-btn'); hold(btns.length === 1, 'exactly one How button on pulse (' + btns.length + ')');
btns[0].click(); await sleep(900);
const rc = M().querySelector('.ex-host .ex-card'); hold(!!rc, 'report card opens');
click('[data-exmode=quick]'); await sleep(200);
out.reportQuickSteps = [...rc.querySelectorAll('.ex-step')].filter(vis).length;
click('[data-exmode=full]'); await sleep(200);
out.reportFullSteps = [...rc.querySelectorAll('.ex-step')].filter(vis).length; hold(out.reportFullSteps > out.reportQuickSteps, 'report quick<full');
const recomp = num(rc.querySelector('[data-ex-recomputed="item_sales"]').innerText), shown = num(M().querySelector('.kpi .v').innerText);
out.reportTotals = {recomp, shown}; hold(Math.abs(recomp - shown) <= 0.5, 'recomputed total ~ displayed (displayed is rounded to dollars)');
const cl = [...rc.querySelectorAll('.ex-cell')]; out.cellChecks = cl.map(c => { const sp = [...c.querySelectorAll('summary span')].map(s => s.innerText); const tr = /^(\d+) orders/.exec(sp[0]); return {page: num(sp.find(s => /^Page/.test(s))), all: num(sp.find(s => /add to/.test(s))), rows: c.querySelectorAll('tbody tr').length, trunc: !!c.querySelector('[data-ex-trunc]'), n: tr && +tr[1]}; });
hold(out.cellChecks.every(c => Math.abs(c.page - c.all) <= 0.01), 'all report cells: full-day sum equals page');
hold(out.cellChecks.every(c => (c.n > c.rows) === c.trunc), 'truncated label present exactly when truncated');
const cs = cl.length; out.reportCells = cs; hold(cs > 0, 'drill cells under report');
// no duplicate buttons after re-render
render(); await sleep(900); hold(M().querySelectorAll('.ex-btn').length === 1, 'no duplicate buttons after re-render');
decorateExplain(M()); hold(M().querySelectorAll('.ex-btn').length === 1, 'decorate twice stays at one');
// ask + kpi cards through the contract function
const box = document.createElement('div'); document.body.appendChild(box);
const askRes = {metric: 'item_sales', m: {label: 'Item sales', unit: 'usd', src: 'daily'}, from: '2026-10-01', to: '2026-10-03', total: null, notes: []};
const rows = await dailyRows(); askRes.total = rows.filter(r => r.date >= '2026-10-01' && r.date <= '2026-10-03').reduce((a, r) => a + r.item_sales, 0);
box.innerHTML = await explainHTML('ask', {question: 'item sales this month', spec: {metric: 'item_sales', by: 'none', filters: {}, period: {from: '2026-10-01', to: '2026-10-03'}}, res: askRes, engine: 'keywords'});
out.askCard = {has: !!box.querySelector('.ex-card'), says: /model only reads the question/.test(box.innerText), cmp: (box.querySelector('.ex-cmp') || {}).innerText};
hold(out.askCard.has && out.askCard.says && /Match/.test(out.askCard.cmp || ''), 'ask card');
box.innerHTML = await explainHTML('kpi', {id: 'net_margin', month: '2026-09'});
out.kpiCard = {has: !!box.querySelector('.ex-card'), text: box.innerText.slice(0, 200).replace(/\s+/g, ' ')};
hold(out.kpiCard.has && /Prior month/.test(box.innerText) && /SELECT/.test(box.innerText), 'kpi card');
box.innerHTML = await explainHTML('upload', {id: 'nope'}); hold(!!box.querySelector('.ex-card') || !!box.querySelector('.banner'), 'upload card does not crash');
box.remove();
out.bad = out.checks.filter(c => !c.ok).map(c => c.m);
if (new URLSearchParams(location.search).get('shotcard') !== null || true) {}
done(out);
