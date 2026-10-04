// Ask tab (thread layout): exercises the model path with the canned --sample JSON (or the keyword path when none). SAMPLE.__q overrides the question.
const q = (typeof SAMPLE !== "undefined" && SAMPLE && SAMPLE.__q) || "Which stores listed the fewest books last month?";
click('[data-tab=ask]'); await sleep(800);
setVal('#q', q, 'input'); document.querySelector('#askform').requestSubmit(); await sleep(2000);
const cards = [...document.querySelectorAll('.ask-a')], a = cards[cards.length - 1], txt = s => (a && a.querySelector(s) || {innerText: ''}).innerText.replace(/\s+/g, ' ');
const out = {q, questions: document.querySelectorAll('.ask-q').length, headline: txt('.headline'), body: txt('.ask-body'),
  math: a ? [...a.querySelectorAll('.ask-math li')].map(li => li.innerText.replace(/\s+/g, ' ').slice(0, 120)) : [],
  chart: !!(a && a.querySelector('.ask-chartbox canvas, .ask-chartbox svg, .ask-chartbox .bars')), alts: a ? [...a.querySelectorAll('[data-alt]')].map(b => b.innerText) : [],
  follow: a ? [...a.querySelectorAll('[data-follow]')].map(b => b.innerText) : [], banners: a ? [...a.querySelectorAll('.banner')].map(x => x.innerText) : []};
const dd = a && a.querySelector('.ask-dashd');
if (dd) { dd.open = true; dd.dispatchEvent(new Event('toggle')); await sleep(1200); out.tiles = [...dd.querySelectorAll('.kpi')].map(e => e.dataset.kpi);
  const ob = dd.querySelector('[data-askopen]'); if (ob) { ob.click(); await sleep(1200); const m = document.querySelector('#main');
    out.dash = {tab: state.tab, focusCleared: state.focus == null, openDetails: [...m.querySelectorAll('details[open]')].map(d => d.dataset.pillar), highlighted: [...m.querySelectorAll('.kpi.focus')].map(e => e.dataset.kpi)}; } }
out.ok = !!(a && out.headline && !out.banners.length && out.math.length);
done(out);
