// Today's test reports (goodwill_pulse.gen.today_reports) through the Upload tab: the day must become the latest day.
// .venv/bin/python artifact/test_page.py --dist DIST --driver artifact/tests/driver_today.js --files demo_data/07_today_2026-10-04/<the 4 xlsx>
const out = {checks: [], steps: []}, DAY = '2026-10-04', STEM = 'paid_orders_10-03-2026_10-04-2026';
const ck = (m, ok, v) => out.checks.push({ok: !!ok, m, ...(v === undefined ? {} : {v})});
const waitFor = async (fn, ms = 25000) => { const t0 = Date.now(); while (Date.now() - t0 < ms) { try { const v = fn(); if (v) return v; } catch (e) {} await sleep(150); } return null; };
const goTab = async t => { click('[data-tab=' + t + ']'); await sleep(900); };
const pick = async name => { const f = await loadFile(name), dt = new DataTransfer(); dt.items.add(f); const i = document.querySelector('#upl-file'); i.files = dt.files; i.dispatchEvent(new Event('change', {bubbles: true})); await waitFor(() => document.querySelector('#upl-add')); await sleep(300); };
const today = async () => Object.fromEntries((await dailyRows()).filter(r => r.date === DAY).map(r => [r.channel, {orders: r.orders, item_sales: Math.round(r.item_sales * 100) / 100, src: r.src}]));
const add = async name => {
  await goTab('upload'); await pick(name);
  const txt = document.querySelector('#upl-work').innerText;
  const cover = (txt.match(/Fully covers[^\n]*/) || [''])[0];
  click('#upl-add'); const ok = await waitFor(() => document.querySelector('#upl-notice.ok'));
  out.steps.push({file: name, cover, added: !!ok, today: await today()});
  ck(name + ': fully covers Oct 4', /Oct 4|Sun 4|10-04|2026-10-04/.test(cover), cover);
  ck(name + ': added', ok, (document.querySelector('#upl-notice') || {}).innerText);
};
const before = await dailyRows();
out.latestBefore = before.reduce((a, r) => r.date > a ? r.date : a, '');
await add(STEM + '.xlsx');
await add('cashmonkey_orders_' + DAY + '.xlsx');
await add('messy_sales_export_' + DAY + '.xlsx');
const after = await dailyRows();
out.latestAfter = after.reduce((a, r) => r.date > a ? r.date : a, '');
ck('latest day is now ' + DAY, out.latestAfter === DAY, out.latestAfter);
state.rng = null; await goTab('pulse');
out.pulseKpis = [...document.querySelectorAll('#main .card.pad .v')].map(x => x.innerText).join(' / ');
out.pulseHead = (document.querySelector('#main h1, #main h2') || {innerText: ''}).innerText;
const t1 = await today();
await add(STEM + '_differs.xlsx');
const t2 = await today();
out.sgwChange = {before: t1.shopgoodwill, after: t2.shopgoodwill};
ck('differs file changes ShopGoodwill today', t1.shopgoodwill && t2.shopgoodwill && t2.shopgoodwill.item_sales !== t1.shopgoodwill.item_sales, out.sgwChange);
done(out);
