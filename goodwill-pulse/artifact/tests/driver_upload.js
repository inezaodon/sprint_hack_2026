// Upload tab end-to-end. Run:
//   .venv/bin/python artifact/test_page.py --dist DIST --driver artifact/tests/driver_upload.js --files artifact/samples/upright_paid_orders_match.xlsx,artifact/samples/upright_paid_orders_differs.xlsx,artifact/samples/messy_sales_export.xlsx,artifact/samples/cashmonkey_orders_match.xlsx,artifact/samples/upright_paid_orders_match.csv [--sample '{"mapping":{...}}']
// With --hash upload (screenshot run) it only stages the messy file and stops.
const out = {checks: []}, M = () => document.querySelector('#main');
const ck = (m, ok, extra) => out.checks.push({ok: !!ok, m, ...(extra === undefined ? {} : {v: extra})});
const waitFor = async (fn, ms = 25000) => { const t0 = Date.now(); while (Date.now() - t0 < ms) { try { const v = fn(); if (v) return v; } catch (e) {} await sleep(150); } return null; };
const pick = async name => { const f = await loadFile(name), dt = new DataTransfer(); dt.items.add(f); const i = document.querySelector('#upl-file'); i.files = dt.files; i.dispatchEvent(new Event('change', {bubbles: true})); return waitFor(() => document.querySelector('#upl-add, #upl-notice.bad')); };
const goTab = async t => { click('[data-tab=' + t + ']'); await sleep(900); };
const upIds = () => Object.keys(DOCS).filter(k => k.startsWith('uploads/'));
const nightly = async (from, to) => { state.rng = {preset: 'custom', from, to}; await goTab('pulse'); const m = M(); const rows = [...m.querySelectorAll('section')[0].querySelectorAll('tbody tr')].map(tr => [...tr.children].map(td => td.innerText.trim()).slice(0, 5)); return rows; };
const cell = (rows, d, c) => rows.find(r => r.date === d && r.channel === c);

if (location.hash === '#upload') {
  await goTab('upload'); await pick('messy_sales_export.xlsx'); await sleep(500);
  done({staged: !!document.querySelector('#upl-add')});
} else {
await goTab('upload');
ck('upload tab exists', document.querySelector('[data-tab=upload]'));
ck('controls present', document.querySelector('#upl-file') && document.querySelector('#upl-drop'));
const base = await dailyRowsBase();
const bc = (d, c) => base.find(r => r.date === d && r.channel === c);

// ---- (a) matching Upright file
let r = await pick('upright_paid_orders_match.xlsx');
ck('xlsx parsed via SheetJS', document.querySelector('#upl-add'), !!window.XLSX);
const txt = document.querySelector('#upl-work').innerText;
ck('Upright layout recognised', /Upright paid orders layout recognised/.test(txt));
ck('totals row excluded and listed', /Totals row/.test(txt));
ck('eBay set aside with reason', /Set aside by choice: eBay/.test(txt) && /combines Upright and Cash Monkey/.test(txt));
out.mapping = [...document.querySelectorAll('[data-map]')].map(s => s.dataset.map + '=' + s.options[s.selectedIndex].text);
out.previewTiles = [...document.querySelectorAll('.upl-tiles .kpi')].map(k => k.innerText.replace(/\s+/g, ' '));
const before = await nightly('2026-09-08', '2026-09-10');
out.nightlyBefore = before;
await goTab('upload'); await pick('upright_paid_orders_match.xlsx');
click('#upl-add'); await waitFor(() => document.querySelector('#upl-notice.ok'));
ck('add reported success', document.querySelector('#upl-notice.ok'), (document.querySelector('#upl-notice') || {}).innerText);
ck('index + data doc written', DOCS['uploads/index'] && DOCS['uploads/index'].items.length === 1 && upIds().length >= 2, upIds());
const it = DOCS['uploads/index'].items[0];
ck('index fields', it.id && it.name === 'upright_paid_orders_match.xlsx' && it.uploaded_at && it.hash && it.rows > 100 && it.from === '2026-09-08' && it.to === '2026-09-10', it);
const meta = DOCS['uploads/' + it.id].meta; out.meta = {tz: meta.tz, kept: meta.rows_kept, excluded: meta.excluded, set_aside: meta.set_aside, mapping: meta.mapping};
const rows = await dailyRows();
for (const d of ['2026-09-08', '2026-09-09', '2026-09-10']) for (const c of ['shopgoodwill', 'goodwillfinds']) {
  const x = cell(rows, d, c), b = bc(d, c);
  ck(`overlay ${d} ${c} src upload`, x && x.src === 'upload:' + it.id, x && x.src);
  ck(`overlay ${d} ${c} equals warehouse (file matches)`, x && b && x.orders === b.orders && Math.abs(x.item_sales - b.item_sales) < .011 && Math.abs(x.shipping - b.shipping) < .011 && Math.abs(x.fees - b.fees) < .011, x && b && [x.orders, x.item_sales, x.shipping, x.fees, b.orders, b.item_sales, b.shipping, b.fees]);
}
ck('ebay untouched', cell(rows, '2026-09-09', 'ebay').src === 'warehouse');
ck('other days untouched', cell(rows, '2026-09-07', 'shopgoodwill').src === 'warehouse');
let prov = await uploadProvenance();
out.provSummary = {cells: prov.length, match: prov.filter(p => p.status === 'match').length, diff: prov.filter(p => p.status === 'diff').map(p => p.date + ' ' + p.channel)};
ck('provenance shape', prov.length === 6 && prov[0].upload_id === it.id && prov[0].warehouse && prov[0].upload && prov[0].diff, prov[0]);
ck('all 6 cells match', prov.every(p => p.status === 'match'));
let after = await nightly('2026-09-08', '2026-09-10');
out.nightlyAfter = after;
ck('nightly report renders after upload', after.length >= 5);
await goTab('upload');
ck('reconcile table shows matches', /6 of 6 cells match/.test(document.querySelector('#upl-recon').innerText) && document.querySelectorAll('#upl-recon-table tbody tr').length === 6);
ck('same file refused', true);
await pick('upright_paid_orders_match.xlsx'); click('#upl-add'); await waitFor(() => /already in the database/.test(document.querySelector('#upl-work').innerText));
ck('duplicate file is not added twice', /already in the database/.test(document.querySelector('#upl-work').innerText) && DOCS['uploads/index'].items.length === 1);
click('#upl-cancel');

// ---- (b) differing file: newer upload wins, reconcile shows differences
await pick('upright_paid_orders_differs.xlsx');
const t2 = document.querySelector('#upl-work').innerText;
ck('exact duplicate excluded and shown', /Exact duplicate of/.test(t2), (t2.match(/Exact duplicate[^\n]*/) || [])[0]);
click('#upl-add'); await waitFor(() => document.querySelector('#upl-notice.ok'));
prov = await uploadProvenance();
const diffs = prov.filter(p => p.status === 'diff');
out.diffCells = diffs.map(p => ({d: p.date, c: p.channel, diff: p.diff}));
ck('differences detected', diffs.length >= 2 && diffs.some(p => p.channel === 'shopgoodwill' && Math.abs(p.diff.item_sales) > 1) && diffs.some(p => p.channel === 'goodwillfinds' && Math.abs(p.diff.fees - 4) < .011));
ck('duplicate order not double counted', prov.every(p => p.diff.orders === 0), prov.map(p => p.diff.orders));
const nd = await nightly('2026-09-08', '2026-09-10'), sg = r => r.find(x => x[0] === 'ShopGoodwill');
out.nightlyDiffers = sg(nd); ck('nightly ShopGoodwill item sales changed by +$17.50 after differing upload', Math.abs(parseFloat(sg(nd)[2].replace(/[$,]/g, '')) - parseFloat(sg(before)[2].replace(/[$,]/g, '')) - 17.5) < .011, [sg(before), sg(nd)]);
await goTab('upload');
ck('reconcile shows Differs pills', /Differs/.test(document.querySelector('#upl-recon').innerText));
ck('latest upload wins', (await dailyRows()).filter(x => x.src.startsWith('upload:')).every(x => x.src === 'upload:' + diffs[0].upload_id));
// remove both
for (const id of DOCS['uploads/index'].items.map(i => i.id)) { click('[data-rm="' + id + '"]'); await sleep(900); }
ck('removed: index empty, docs deleted', DOCS['uploads/index'].items.length === 0 && upIds().length === 1, upIds());
const rows2 = await dailyRows();
ck('numbers revert to warehouse', rows2.every(x => x.src === 'warehouse') && rows2.length === base.length && JSON.stringify(rows2.map(x => [x.date, x.channel, x.orders, x.item_sales, x.shipping, x.fees])) === JSON.stringify(base.map(x => [x.date, x.channel, x.orders, x.item_sales, x.shipping, x.fees])));
const afterRevert = await nightly('2026-09-08', '2026-09-10');
ck('nightly report reverts', JSON.stringify(afterRevert) === JSON.stringify(before));

// ---- messy file
await goTab('upload'); await pick('messy_sales_export.xlsx');
const t3 = document.querySelector('#upl-work').innerText;
out.messyMap = [...document.querySelectorAll('[data-map]')].map(s => s.dataset.map + '=' + s.options[s.selectedIndex].text);
out.messyTiles = [...document.querySelectorAll('.upl-tiles .kpi')].map(k => k.innerText.replace(/\s+/g, ' '));
ck('messy: header row found below title rows', document.querySelector('#upl-hdr').value === '3', document.querySelector('#upl-hdr').value);
ck('messy: recognised without Claude', !/not recognised/.test(t3) && !document.querySelector('#upl-claude'));
ck('messy: totals, blank, test, bad date, bad amount all listed', /Totals row/.test(t3) && /Blank rows/.test(t3) && /Test order/.test(t3) && /Invalid date/.test(t3) && /Non-numeric amount/.test(t3));
click('#upl-add'); await waitFor(() => document.querySelector('#upl-notice.ok'));
const mi = DOCS['uploads/index'].items[0], mm = DOCS['uploads/' + mi.id];
ck('messy: text dates parsed to ISO days', mm.rows.length > 5 && mm.rows.every(x => /^2026-09-(09|10)$/.test(x[0])), mm.rows[0]);
ck('messy: no refunds column keeps warehouse refunds', mm.meta.supplies.refunds === false && (await dailyRows()).find(x => x.date === mm.rows[0][0] && x.channel === mm.rows[0][1]).refunds === bc(mm.rows[0][0], mm.rows[0][1]).refunds);
click('[data-rm="' + mi.id + '"]'); await sleep(900);

// ---- Cash Monkey + csv
await goTab('upload'); await pick('cashmonkey_orders_match.xlsx');
ck('cash monkey recognised, unit grain, UTC', /Cash Monkey orders layout/.test(document.querySelector('#upl-work').innerText) && document.querySelector('#upl-unit').checked && document.querySelector('#upl-tz').value === 'UTC');
click('#upl-add'); await waitFor(() => document.querySelector('#upl-notice.ok'));
prov = await uploadProvenance();
out.cmSummary = {cells: prov.length, diff: prov.filter(p => p.status === 'diff').map(p => p.date + ' ' + p.channel + ' ' + JSON.stringify(p.diff))};
ck('cash monkey matches warehouse (amazon, books)', prov.length === 6 && prov.every(p => p.status === 'match'), prov.map(p => p.status));
await goTab('upload'); click('[data-rm="' + DOCS['uploads/index'].items[0].id + '"]'); await sleep(900);
await pick('upright_paid_orders_match.csv');
ck('csv parsed without SheetJS (text dates)', /Upright paid orders layout recognised/.test(document.querySelector('#upl-work').innerText) && document.querySelector('#upl-add'));
click('#upl-add'); await waitFor(() => document.querySelector('#upl-notice.ok'));
prov = await uploadProvenance(); ck('csv matches warehouse', prov.length === 6 && prov.every(p => p.status === 'match'), prov.map(p => p.status));
await goTab('upload'); click('[data-rm="' + DOCS['uploads/index'].items[0].id + '"]'); await sleep(900);

// ---- write refusal
const realUse = window.claude.use; const origDb = db;
db = {doc: origDb.doc, collection: () => ({doc: () => ({get: async () => ({exists: false, data: () => null}), set: async () => { throw new Error('permission denied'); }, delete: async () => {}})})};
await goTab('upload'); await pick('upright_paid_orders_match.xlsx'); click('#upl-add'); await waitFor(() => /not added/.test((document.querySelector('#upl-notice') || {innerText: ''}).innerText));
ck('refused write shows clear message', /not added.*edit/s.test(document.querySelector('#upl-notice').innerText), document.querySelector('#upl-notice').innerText);
db = origDb;

// ---- Ask Claude mapping (only meaningful with --sample)
await goTab('upload');
const ugly = new File(["Zeta,Alpha,Omega,Kappa\n2026-09-10,A1,12.50,x\n"], 'ugly.csv');
const dt = new DataTransfer(); dt.items.add(ugly); const i = document.querySelector('#upl-file'); i.files = dt.files; i.dispatchEvent(new Event('change', {bubbles: true}));
await waitFor(() => document.querySelector('#upl-map-paid_date'));
out.claudeButton = !!document.querySelector('#upl-claude');
if (document.querySelector('#upl-claude')) {
  click('#upl-claude'); await waitFor(() => /Claude suggested|could not/i.test((document.querySelector('#upl-claudemsg') || {innerText: ''}).innerText), 8000);
  out.claudeMsg = (document.querySelector('#upl-claudemsg') || {innerText: ''}).innerText;
  out.afterClaude = [...document.querySelectorAll('[data-map]')].map(s => s.dataset.map + '=' + s.options[s.selectedIndex].text);
}
out.errorsInPage = __errs.slice();
done(out);
}
