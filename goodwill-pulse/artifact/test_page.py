"""Run the built page in headless Chrome against the exported documents, with an in-memory stand-in for the artifact db.

    .venv/bin/python artifact/test_page.py --driver artifact/tests/driver_basic.js
    .venv/bin/python artifact/test_page.py --driver my_driver.js --sample '{"metric":"item_sales",...}' --shot out.png --hash ask

The driver is JS that runs after the page loads. It may use `await sleep(ms)`, `click(sel)`, `setVal(sel, value, event)` and
must call `done(obj)` with a JSON-able result (or throw). The harness prints that object plus any uncaught page errors.
Stand-in db: db.doc(path).get/set/update/delete, db.collection(path).get()/doc(id), seeded from artifact/data/*.json
(file `<collection>__<doc>.json` -> doc `<collection>/<doc>`). `--sample` makes claude.use("sample") return that JSON from
sample.json(); without it, use("sample") resolves null. `--files a.xlsx,b.csv` lets a driver call `await loadFile(name)` to get a File.
"""
from __future__ import annotations

import argparse, base64, html as H, json, re, subprocess, sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--driver"); ap.add_argument("--sample"); ap.add_argument("--files", default="")
    ap.add_argument("--data", default=str(HERE / "data")); ap.add_argument("--hash", default="")
    ap.add_argument("--shot"); ap.add_argument("--size", default="1300,2400"); ap.add_argument("--budget", type=int, default=60000)
    ap.add_argument("--no-build", action="store_true"); ap.add_argument("--dist", default=str(HERE / "dist"), help="output dir for the built page + harness (use your own to avoid colliding with other agents)")
    a = ap.parse_args()
    if not a.no_build:
        r0 = subprocess.run([sys.executable, str(HERE / "build.py"), str(Path(a.dist) / "pulse.html")], capture_output=True, text=True)
        if r0.returncode: print("BUILD FAILED (maybe another agent is mid-edit; retry in 30s):\n" + r0.stdout + r0.stderr); return 2
    page = (Path(a.dist) / "pulse.html").read_text()
    docs = {}
    for f in Path(a.data).glob("*.json"):
        c, i = f.stem.split("__", 1); docs[f"{c}/{i}"] = json.loads(f.read_text())
    files = {}
    for p in filter(None, a.files.split(",")):
        files[Path(p).name] = base64.b64encode(Path(p).read_bytes()).decode()
    sample = json.loads(a.sample) if a.sample else None
    stub = """<script>
const DOCS = %s, FILES = %s, SAMPLE = %s; window.__errs = []; window.__sampleCalls = [];
addEventListener('error', e => __errs.push(String(e.message))); addEventListener('unhandledrejection', e => __errs.push('rejection: ' + String(e.reason && e.reason.message || e.reason)));
const mkdoc = p => ({get: async () => ({exists: p in DOCS, id: p.split('/').pop(), data: () => DOCS[p] && JSON.parse(JSON.stringify(DOCS[p]))}),
  set: async d => { DOCS[p] = JSON.parse(JSON.stringify(d)); }, update: async d => { DOCS[p] = {...(DOCS[p] || {}), ...JSON.parse(JSON.stringify(d))}; }, delete: async () => { delete DOCS[p]; }});
const mkcoll = c => ({doc: id => mkdoc(c + '/' + id), get: async () => ({docs: Object.keys(DOCS).filter(k => k.startsWith(c + '/') && !k.slice(c.length + 1).includes('/')).map(k => ({id: k.slice(c.length + 1), data: () => JSON.parse(JSON.stringify(DOCS[k]))}))})});
window.claude = {use: async n => {
  if (n === 'db') return {doc: mkdoc, collection: mkcoll};
  if (n === 'sample' && SAMPLE !== null) { const f = async (input) => ({text: JSON.stringify(SAMPLE), truncated: false}); f.json = async (input, o) => { __sampleCalls.push(String(input).slice(0, 200)); return JSON.parse(JSON.stringify(SAMPLE)); }; f.limits = async () => ({images: false}); return f; }
  return null; }};
</script>""" % (json.dumps(docs, separators=(",", ":")), json.dumps(files), json.dumps(sample))
    page = page.replace("<script>\n", stub + "\n<script>\n", 1)
    drv = ""
    if a.driver:
        drv = """<script>
const sleep = ms => new Promise(r => setTimeout(r, ms));
const click = s => { const e = document.querySelector(s); if (!e) throw new Error('no element ' + s); e.click(); };
const setVal = (s, v, ev = 'change') => { const e = document.querySelector(s); if (!e) throw new Error('no element ' + s); e.value = v; e.dispatchEvent(new Event(ev, {bubbles: true})); };
const loadFile = async n => { const b = Uint8Array.from(atob(FILES[n]), c => c.charCodeAt(0)); return new File([b], n); };
const done = o => { const pre = document.createElement('pre'); pre.id = 'RESULT'; pre.textContent = JSON.stringify({result: o, errors: __errs, sampleCalls: __sampleCalls}, null, 1); document.body.appendChild(pre); };
(async () => { try { await sleep(1200);\n""" + Path(a.driver).read_text() + """\n} catch (e) { done({driverError: String(e && e.stack || e)}); } })();
</script>"""
    out = Path(a.dist) / "harness.html"
    out.write_text("<!doctype html><meta charset=utf8><body>" + page.replace("</body>", "") + drv)
    cmd = [CHROME, "--headless=new", "--disable-gpu", "--hide-scrollbars", f"--window-size={a.size}", f"--virtual-time-budget={a.budget}"]
    url = f"file://{out}" + (f"#{a.hash}" if a.hash else "")
    if a.shot:
        subprocess.run(cmd + [f"--screenshot={a.shot}", url], capture_output=True, timeout=180); print("screenshot:", a.shot)
    if a.driver:
        r = subprocess.run(cmd + ["--dump-dom", url], capture_output=True, text=True, timeout=240)
        m = re.search(r'<pre id="RESULT">(.*?)</pre>', r.stdout, re.S)
        print(H.unescape(m.group(1)) if m else "NO RESULT (driver never called done())\n" + r.stdout[-600:])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
