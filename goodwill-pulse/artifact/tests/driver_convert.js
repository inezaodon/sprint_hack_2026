// Upload tab, other formats (35_upload.js, routes/intake.py). The server is faked with a fetch stub.
//   .venv/bin/python artifact/test_page.py --dist DIST --budget 120000 --driver artifact/tests/driver_convert.js --files artifact/samples/paid_orders_09-07-2026_09-10-2026_match.csv
const out = {checks: []}, M = () => document.querySelector('#main');
const ck = (m, ok, extra) => out.checks.push({ok: !!ok, m, ...(extra === undefined ? {} : {v: extra})});
const waitFor = async (fn, ms = 8000) => { const t0 = Date.now(); while (Date.now() - t0 < ms) { try { const v = fn(); if (v) return v; } catch (e) {} await sleep(100); } return null; };
const goTab = async t => { setTab(t); await sleep(700); };
const txt = sel => (document.querySelector(sel) || {innerText: ''}).innerText;
const drop = async (name, type) => { const i = document.querySelector('#upl-file'); const dt = new DataTransfer(); dt.items.add(new File([new Uint8Array([1, 2, 3])], name, {type: type || ''})); i.files = dt.files; i.dispatchEvent(new Event('change', {bubbles: true})); await sleep(300); };

const csvBytes = new Uint8Array(await (await loadFile('paid_orders_09-07-2026_09-10-2026_match.csv')).arrayBuffer());
const calls = [];
let previewReply = null;
const preview = {header: ['Line', 'Date', 'Type', 'Order No', 'SKU', 'Description', 'Amount'], rows: [['1', '09/01/2026', 'SALE', 'GWB1', 'GWM-1', 'Item sale <b>x</b>', '10.38'], ['2', '09/01/2026', 'SHIPPING', 'GWB1', '', 'Shipping', '3.99']]};
const finOut = (id, over) => ({output_id: id, name: 'GWB_Statement_2026-09.xlsx', origin: 'statement.eml > attachment > GWB_Statement_2026-09.pdf, pages 1-11', method: 'parsed', target: 'finance', layout: 'gwb_statement', label: 'Goodwill Books payment statement', row_count: 642, loaded_rows: 0, status: 'ready', message: 'Recognized as Goodwill Books payment statement. Not loaded yet. 642 rows for 2026-09.', months: ['2026-09'], pending: true, exceptions: [], stated: {'Net payment': '$3,603.07'}, control: {rows_total: 3603.07, stated_total: 3603.07, label: 'net', ok: true}, preview, excel_url: '/api/intake/outputs/' + id + '/file', ...(over || {})});
window.fetch = async (url, opts) => {
  const u = String(url), m = (opts && opts.method) || 'GET'; calls.push(m + ' ' + u);
  const json = (body, status = 200) => new Response(JSON.stringify(body), {status, headers: {'Content-Type': 'application/json'}});
  if (u.startsWith('/api/intake/formats')) return json({accept: ['.eml', '.pdf'], spreadsheets: ['.csv'], ai: false});
  if (u === '/api/intake/preview') return json(previewReply);
  if (u.startsWith('/api/intake?')) return json([]);
  if (u.endsWith('/file')) return new Response(new Blob([csvBytes], {type: 'text/csv'}), {status: 200});
  if (u.endsWith('/confirm')) {
    const fd = opts.body, who = fd.get('confirmed_by'), over = fd.get('override_control') === 'true';
    if (!who && /bad/.test(u)) return json({detail: 'Enter your name to release a file that was held for review.'}, 422);
    return json({...finOut(u.split('/')[4], {status: over ? 'partial' : 'loaded', loaded_rows: 642, pending: false, message: 'Loaded 642 rows into Goodwill Books payment statement for 2026-09.'}), close_months_rebuilt: ['2026-09']});
  }
  return json({detail: 'unexpected ' + u}, 404);
};

// 1. no server reachable (file:// page): only spreadsheets, and a clear message for anything else
await goTab('upload');
ck('no server: drop zone offers spreadsheets only', /Drop an Excel or CSV file here/.test(txt('#upl-drop')) && document.querySelector('#upl-file').accept === '.xlsx,.xls,.csv', document.querySelector('#upl-file').accept);
await drop('statement.eml');
ck('no server: an email is refused with the reason', /not reachable from this page/.test(txt('#upl-notice')), txt('#upl-notice').slice(0, 120));
ck('no server: no converted-files section', !document.querySelector('#upl-recent-card'));

// 2. server present
UPL.server = {accept: ['.eml', '.pdf'], spreadsheets: ['.csv'], ai: false}; UPL.notice = null;
await goTab('home'); await goTab('upload');
ck('server: drop zone accepts other formats', /Drop a file here/.test(txt('#upl-drop')) && document.querySelector('#upl-file').accept.includes('.eml'));
ck('server: converted-files section shows', !!document.querySelector('#upl-recent-card'));

// 2a. a month-end statement whose lines match its printed total
previewReply = [{intake_id: 'i1', name: 'statement.eml', format: 'eml', status: 'ready', message: '', envelope: {from: 'statements@goodwillbooks.example', subject: 'Your Goodwill Books payment statement', date: 'Thu, 01 Oct 2026'}, notes: [], outputs: [finOut('o-good')]}];
await drop('statement.eml');
await waitFor(() => document.querySelector('#upl-conv'));
ck('2a: found-card shows file, email and spreadsheet', /What was found in statement\.eml/.test(txt('#upl-conv')) && /statements@goodwillbooks\.example/.test(txt('#upl-conv')) && /GWB_Statement_2026-09\.xlsx/.test(txt('#upl-conv')));
ck('2a: says it was read by code', /Read by code, no AI/.test(txt('#upl-conv')));
ck('2a: shows the control match', /Matches the file's own total/.test(txt('#upl-conv')));
ck('2a: preview rows are escaped', document.querySelectorAll('#upl-conv .upl-peek tbody tr').length === 2 && !document.querySelector('#upl-conv .upl-peek b'));
ck('2a: has a download link', !!document.querySelector('#upl-conv a[download]'));
ck('2a: previewed only, nothing loaded', calls.filter(c => c.includes('/confirm')).length === 0 && calls.includes('POST /api/intake/preview'));
click('[data-load="o-good"]'); await waitFor(() => /Loaded 642 rows/.test(txt('#upl-notice')));
ck('2a: load calls confirm and reports the rebuilt month', calls.some(c => c === 'POST /api/intake/outputs/o-good/confirm') && /rebuild 2026-09/.test(txt('#upl-notice')), txt('#upl-notice'));
ck('2a: link to the Close page appears', !!document.querySelector('#upl-conv a[href="/close"]'));

// 2b. statement that does not add up: held, needs a name
previewReply = [{intake_id: 'i2', name: 'bad.pdf', format: 'pdf', status: 'needs_review', message: '', envelope: {}, notes: [], outputs: [finOut('o-bad', {status: 'needs_review', message: 'Held for review', control: {rows_total: 3602.4, stated_total: 3603.07, label: 'net', ok: false}, exceptions: [{rule: 'control_total', severity: 'error', message: 'Lines add up to $3,602.40'}]})]}];
await goTab('home'); await goTab('upload'); await drop('bad.pdf');
await waitFor(() => document.querySelector('#upl-conv [data-out="o-bad"]'));
ck('2b: mismatch is explained with the difference', /do not add up/.test(txt('#upl-conv')) && /\$0\.67/.test(txt('#upl-conv')) || /−\$0\.67/.test(txt('#upl-conv')), txt('#upl-conv .upl-ctl'));
ck('2b: load-anyway button and corrected-copy picker present', !!document.querySelector('[data-load="o-bad"][data-override]') && !!document.querySelector('input[data-fix="o-bad"]'));
click('[data-load="o-bad"]'); await sleep(300);
ck('2b: no name, no request', /Enter your name/.test(txt('#upl-notice')) && !calls.some(c => c.includes('o-bad/confirm')), txt('#upl-notice'));
setVal('#upl-who-o-bad', 'Amanda', 'input'); click('[data-load="o-bad"]'); await waitFor(() => /Loaded with problems/.test(txt('#upl-conv')));
ck('2b: with a name it loads and says loaded with problems', calls.some(c => c.includes('o-bad/confirm')) && /Loaded with problems/.test(txt('#upl-conv')), txt('#upl-conv').slice(0, 200));

// 2c. a converted sales report continues into the normal column check and preview
previewReply = [{intake_id: 'i3', name: 'upright.eml', format: 'eml', status: 'ready', message: '', envelope: {subject: 'Your Upright report'}, notes: [], outputs: [{output_id: 'o-sales', name: 'paid_orders_09-07-2026_09-10-2026_match.csv', origin: 'upright.eml > attachment', method: 'passthrough', target: 'warehouse', layout: 'upright_paid_orders', label: 'Upright paid orders', row_count: 92, loaded_rows: 0, status: 'ready', message: 'Recognized as Upright paid orders. Not loaded yet.', pending: true, exceptions: [], stated: {}, control: null, preview, months: [], excel_url: '/api/intake/outputs/o-sales/file'}]}];
await goTab('home'); await goTab('upload'); await drop('upright.eml');
await waitFor(() => document.querySelector('[data-use="o-sales"]'));
ck('2c: sales report offers "Use in the daily numbers"', !!document.querySelector('[data-use="o-sales"]') && !document.querySelector('[data-load="o-sales"]'));
click('[data-use="o-sales"]'); await waitFor(() => document.querySelector('#upl-add') || document.querySelector('#upl-notice.bad'), 15000);
ck('2c: continues into the existing steps (layout recognised, Add button)', /Upright paid orders layout recognised/.test(txt('#upl-work')) && !!document.querySelector('#upl-add'), txt('#upl-work').slice(0, 200));
ck('2c: nothing was sent to confirm for a sales report', !calls.some(c => c.includes('o-sales/confirm')));

// 2d. a file that cannot be read at all and a duplicate
previewReply = [{intake_id: 'i4', name: 'note.eml', format: 'eml', status: 'rejected', message: '', envelope: {}, notes: ['note.eml: the email has no attachment and no table.'], outputs: []}];
await goTab('home'); await goTab('upload'); await drop('note.eml');
await waitFor(() => document.querySelector('#upl-conv'));
ck('2d: unreadable file explains why', /no attachment and no table/.test(txt('#upl-conv')) && /No table could be read/.test(txt('#upl-conv')));
previewReply = [{intake_id: 'i1', name: 'statement.eml', format: 'eml', status: 'duplicate_file', previous_status: 'loaded', message: 'This exact file was already uploaded (statement.eml, loaded).', envelope: {}, notes: [], outputs: [finOut('o-good', {status: 'loaded', pending: false, loaded_rows: 642})]}];
await goTab('home'); await goTab('upload'); await drop('statement.eml');
await waitFor(() => document.querySelector('#upl-dup'));
ck('2d: duplicate says so and still shows its spreadsheet', /already uploaded/.test(txt('#upl-dup')) && !!document.querySelector('[data-out="o-good"]'));

// 3. server errors are shown, not swallowed
window.fetch = async () => new Response(JSON.stringify({detail: 'boom'}), {status: 500, headers: {'Content-Type': 'application/json'}});
await goTab('home'); await goTab('upload'); await drop('x.pdf');
await waitFor(() => document.querySelector('#upl-notice.bad'));
ck('3: a server error becomes a visible message', /boom/.test(txt('#upl-notice')), txt('#upl-notice'));
out.errorsInPage = __errs.slice();
ck('no page errors', __errs.length === 0, __errs);
done(out);
