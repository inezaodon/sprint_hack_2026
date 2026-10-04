// Regression: every top tab and sub-tab renders, routes work, no page errors. Add your own checks in a separate driver file.
// .venv/bin/python artifact/run_driver.py --name me --driver artifact/tests/driver_basic.js
const out = [], M = () => document.querySelector('#main');
const snap = (l, extra) => { const m = M(); out.push({step: l, len: m.innerText.length, banners: [...m.querySelectorAll('.banner.bad, .hub-err, .hub-pending')].map(x => x.innerText.slice(0, 120)), tables: m.querySelectorAll('table').length, charts: m.querySelectorAll('svg, canvas').length, ...(extra ? extra(m) : {})}); };
const tops = [...document.querySelectorAll('#tabs button')].map(b => b.dataset.tab);
out.push({tops});
for (const t of tops) {
  click('#tabs [data-tab="' + t + '"]'); await sleep(900); snap('top ' + t);
  for (const s of [...document.querySelectorAll('#subtabs button')].map(b => b.dataset.tab)) { click('#subtabs [data-tab="' + s + '"]'); await sleep(900); snap('sub ' + t + '/' + s); }
}
for (const r of ['report/upright', 'report/cashmonkey', 'report/supro', 'report/thriftly', 'pulse', 'close', 'lineage']) { setTab(r); await sleep(900); snap('route ' + r, () => ({hash: location.hash})); }
setTab('dashboard'); await sleep(900);
for (const g of ['day', 'month', 'quarter', 'week']) { const b = document.querySelector('[data-tg=' + g + ']'); if (b) { b.click(); await sleep(300); snap('grain ' + g); } else out.push({step: 'grain ' + g, missing: true}); }
const bad = out.filter(o => o.step && (o.missing || o.len < 40 || (o.banners && o.banners.length)));
done({ok: !bad.length && !__errs.length, bad, steps: out});
