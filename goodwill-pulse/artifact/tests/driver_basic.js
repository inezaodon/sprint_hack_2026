// Regression: every tab renders, key controls work. Add your own checks in a separate driver file.
const out = [], M = () => document.querySelector('#main');
const snap = (l, extra) => { const m = M(); out.push({step: l, len: m.innerText.length, banners: [...m.querySelectorAll('.banner')].map(x => x.innerText.slice(0, 120)), tables: m.querySelectorAll('table').length, svgs: m.querySelectorAll('svg').length, ...(extra ? extra(m) : {})}); };
const tabs = [...document.querySelectorAll('#tabs button')].map(b => b.dataset.tab);
out.push({tabs});
for (const t of tabs) { click('[data-tab=' + t + ']'); await sleep(900); snap('tab ' + t); }
click('[data-tab=pulse]'); await sleep(600);
for (const p of ['catchup', 'last7', 'mtd', 'lastmonth', 'latest']) { click('[data-preset=' + p + ']'); await sleep(400); snap('preset ' + p, m => ({kpis: [...m.querySelectorAll('.card.pad .v')].map(x => x.innerText).join(' / ')})); }
click('[data-tab=dashboard]'); await sleep(700);
for (const g of ['day', 'month', 'quarter', 'week']) { click('[data-tg=' + g + ']'); await sleep(300); snap('grain ' + g); }
click('[data-tab=ask]'); await sleep(700);
for (let i = 0; i < 5; i++) { click('[data-ex="' + i + '"]'); await sleep(600); snap('ask ' + i, m => ({res: (m.querySelector('#answer .card') || {innerText: ''}).innerText.replace(/\s+/g, ' ').slice(0, 160)})); }
done(out);
