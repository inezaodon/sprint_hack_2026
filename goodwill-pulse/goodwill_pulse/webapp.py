"""Build web/app.html: the full single-page app (artifact/) wired to this server instead of the claude.ai runtime.

    .venv/bin/python -m goodwill_pulse.webapp            # writes web/app.html (served at /)

The page calls window.claude.use("db") and window.claude.use("sample"). The shim below provides:
- db: the exported documents (artifact/data/<collection>__<doc>.json) embedded in the page, with the viewer's own
  writes (uploads) kept in their browser's localStorage on top. Nothing is shared between viewers.
- sample: Claude through POST /api/runtime/sample (null when the server has no Claude access, which the page handles).
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

from .config import ROOT, WEB_DIR

ARTIFACT = ROOT / "artifact"

SHIM = """<script>
(() => {
  const SEEDS = %s, KEY = "gwp:doc:", GONE = "__deleted__";
  const ls = {
    get(k) { try { return localStorage.getItem(k); } catch (e) { return null; } },
    set(k, v) { try { localStorage.setItem(k, v); } catch (e) { /* storage unavailable: write is lost on reload */ } },
  };
  const clone = o => o === undefined ? undefined : JSON.parse(JSON.stringify(o));
  const local = {};                                   // fallback when localStorage is blocked
  const read = p => {
    const s = ls.get(KEY + p) ?? local[p];
    if (s === GONE) return undefined;
    if (s != null) { try { return JSON.parse(s); } catch (e) { /* fall through to the seed */ } }
    return SEEDS[p];
  };
  const write = (p, v) => { local[p] = v; ls.set(KEY + p, v); };
  const paths = () => {
    const out = new Set([...Object.keys(SEEDS), ...Object.keys(local)]);
    try { for (let i = 0; i < localStorage.length; i++) { const k = localStorage.key(i); if (k.startsWith(KEY)) out.add(k.slice(KEY.length)); } } catch (e) {}
    return [...out];
  };
  const doc = p => ({
    id: p.split("/").pop(),
    get: async () => { const d = read(p); return {exists: d !== undefined, id: p.split("/").pop(), data: () => clone(d)}; },
    set: async d => write(p, JSON.stringify(d)),
    update: async d => write(p, JSON.stringify({...(read(p) || {}), ...clone(d)})),
    delete: async () => write(p, GONE),
  });
  const collection = c => ({
    doc: id => doc(c + "/" + id),
    get: async () => ({docs: paths()
      .filter(k => k.startsWith(c + "/") && !k.slice(c.length + 1).includes("/") && read(k) !== undefined)
      .map(k => ({id: k.slice(c.length + 1), data: () => clone(read(k))}))}),
  });

  let sampler;                                         // undefined = not checked yet, null = no Claude
  async function getSampler() {
    if (sampler !== undefined) return sampler;
    try { const r = await fetch("/api/runtime/available"); sampler = r.ok && (await r.json()).claude ? makeSampler() : null; }
    catch (e) { sampler = null; }
    return sampler;
  }
  function makeSampler() {
    const call = async (prompt, o = {}, json = false) => {
      const r = await fetch("/api/runtime/sample", {method: "POST", headers: {"content-type": "application/json"},
        body: JSON.stringify({prompt: String(prompt), json, tier: o.modelTier || null}), signal: o.signal});
      if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || "Claude request failed");
      return r.json();
    };
    const f = async (prompt, o) => { const r = await call(prompt, o, false); return {text: r.text, truncated: !!r.truncated}; };
    f.json = async (prompt, o) => (await call(prompt, o, true)).json;
    f.limits = async () => ({images: false});
    return f;
  }
  window.claude = {use: async name => name === "db" ? {doc, collection} : name === "sample" ? await getSampler() : null};
})();
</script>
"""

HEAD = ('<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">\n')


def seeds(data_dir: Path = ARTIFACT / "data") -> dict:
    out = {}
    for f in sorted(data_dir.glob("*.json")):
        coll, doc_id = f.stem.split("__", 1)
        out[f"{coll}/{doc_id}"] = json.loads(f.read_text())
    return out


def build(out: Path = WEB_DIR / "app.html") -> Path:
    with tempfile.TemporaryDirectory(dir=ROOT / "data") as tmp:   # stay inside the project
        page_path = Path(tmp) / "page.html"
        r = subprocess.run([sys.executable, str(ARTIFACT / "build.py"), str(page_path)], capture_output=True, text=True)
        if r.returncode:
            raise SystemExit(f"artifact build failed:\n{r.stdout}{r.stderr}")
        page = page_path.read_text()
    shim = SHIM % json.dumps(seeds(), separators=(",", ":")).replace("</", "<\\/")
    if "<script>" not in page:
        raise SystemExit("artifact page has no <script> to put the runtime shim in front of")
    # artifact pages omit the document shell; the parser infers <head>/<body>, we only add doctype + metas
    html = page if page.lstrip().lower().startswith("<!doctype") else HEAD + page
    i = html.index("<script>")
    out.write_text(html[:i] + shim + html[i:])
    return out


if __name__ == "__main__":
    p = build()
    print(f"{p} ({p.stat().st_size / 1e6:.1f} MB)")
