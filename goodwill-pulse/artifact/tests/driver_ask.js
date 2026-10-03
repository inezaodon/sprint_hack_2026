// Ask tab: exercises the Claude path with the canned --sample JSON (or the keyword path when none). SAMPLE.__q overrides the question.
const q = (typeof SAMPLE !== "undefined" && SAMPLE && SAMPLE.__q) || "Which stores listed the fewest books last month?";
click('[data-tab=ask]'); await sleep(800);
setVal('#q', q, 'input'); click('#askbtn'); await sleep(1500);
const a = document.querySelector('#answer'), txt = s => (a.querySelector(s) || {innerText: ''}).innerText.replace(/\s+/g, ' ');
const out = {q, status: txt('.ask-status'), interp: txt('.ask-interp'), notes: [...a.querySelectorAll('.ask-note')].map(x => x.innerText.slice(0, 120)),
  alts: [...a.querySelectorAll('[data-alt]')].map(b => b.innerText), tiles: [...a.querySelectorAll('#askdash .kpi')].map(e => e.dataset.kpi), explain: [...a.querySelectorAll('#askdash [data-explain]')].map(e => e.dataset.explain).slice(0, 2),
  dashLead: txt('.ask-dash .lead'), result: txt('.card.pad').slice(0, 140), banners: [...a.querySelectorAll('.banner')].map(x => x.innerText)};
if (out.alts.length) { click('[data-alt="1"]'); await sleep(1200); out.afterAlt = {status: txt('.ask-status'), interp: txt('.ask-interp'), tiles: [...a.querySelectorAll('#askdash .kpi')].map(e => e.dataset.kpi)}; }
const ob = document.querySelector('#askopen');
if (ob) {
  out.openPillar = ob.dataset.pillar; ob.click(); await sleep(1200);
  const m = document.querySelector('#main');
  out.dash = {tab: state.tab, focusCleared: state.focus == null, openDetails: [...m.querySelectorAll('details[open]')].map(d => d.dataset.pillar), highlighted: [...m.querySelectorAll('.kpi.focus')].map(e => e.dataset.kpi), rows: [...m.querySelectorAll('tr.focus')].length};
}
done(out);
