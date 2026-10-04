"""Build web/app.html: the full single-page app (artifact/) wired to this server instead of the claude.ai runtime.

    .venv/bin/python -m goodwill_pulse.webapp            # writes web/app.html (served at /)

The page calls window.claude.use("db") and window.claude.use("sample"). The shim below provides:
- db: the exported documents (artifact/data/<collection>__<doc>.json), copied to web/docs/ and fetched from
  /static/docs/ the first time each is read (the page embeds only their index, so it stays small: Vercel caps a
  response at 4.5 MB). The viewer's own writes (uploads) are kept in their browser's localStorage on top.
  Nothing is shared between viewers.
- sample: Claude through POST /api/runtime/sample (null when the server has no Claude access, which the page handles).
"""
from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from .config import ROOT, WEB_DIR

ARTIFACT = ROOT / "artifact"

SHIM = """<script>
(() => {
  const INDEX = %s, KEY = "gwp:doc:", GONE = "__deleted__", fetched = {};
  const ls = {
    get(k) { try { return localStorage.getItem(k); } catch (e) { return null; } },
    set(k, v) { try { localStorage.setItem(k, v); } catch (e) { /* storage unavailable: write is lost on reload */ } },
  };
  const clone = o => o === undefined ? undefined : JSON.parse(JSON.stringify(o));
  const local = {};                                   // fallback when localStorage is blocked
  const seed = p => {                                 // shipped document, fetched once per page load
    if (!(p in INDEX)) return Promise.resolve(undefined);
    return fetched[p] || (fetched[p] = fetch("/static/docs/" + INDEX[p]).then(r => r.ok ? r.json() : undefined)
      .catch(() => { delete fetched[p]; return undefined; }));
  };
  const read = async p => {
    const s = ls.get(KEY + p) ?? local[p];
    if (s === GONE) return undefined;
    if (s != null) { try { return JSON.parse(s); } catch (e) { /* fall through to the seed */ } }
    return seed(p);
  };
  const write = (p, v) => { local[p] = v; ls.set(KEY + p, v); };
  const paths = () => {
    const out = new Set([...Object.keys(INDEX), ...Object.keys(local)]);
    try { for (let i = 0; i < localStorage.length; i++) { const k = localStorage.key(i); if (k.startsWith(KEY)) out.add(k.slice(KEY.length)); } } catch (e) {}
    return [...out];
  };
  const doc = p => ({
    id: p.split("/").pop(),
    get: async () => { const d = clone(await read(p)); return {exists: d !== undefined, id: p.split("/").pop(), data: () => clone(d)}; },
    set: async d => write(p, JSON.stringify(d)),
    update: async d => write(p, JSON.stringify({...((await read(p)) || {}), ...clone(d)})),
    delete: async () => write(p, GONE),
  });
  const collection = c => ({
    doc: id => doc(c + "/" + id),
    get: async () => {
      const ks = paths().filter(k => k.startsWith(c + "/") && !k.slice(c.length + 1).includes("/"));
      const ds = await Promise.all(ks.map(read));
      return {docs: ks.map((k, i) => ({id: k.slice(c.length + 1), d: ds[i]})).filter(x => x.d !== undefined)
        .map(x => ({id: x.id, data: () => clone(x.d)}))};
    },
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


def publish_docs(data_dir: Path = ARTIFACT / "data", docs_dir: Path = WEB_DIR / "docs") -> dict:
    """Copy the documents to web/docs/ (served at /static/docs/) and return {path: file name?v=content hash}."""
    shutil.rmtree(docs_dir, ignore_errors=True)
    docs_dir.mkdir(parents=True)
    index = {}
    for f in sorted(data_dir.glob("*.json")):
        coll, doc_id = f.stem.split("__", 1)
        body = f.read_bytes()
        (docs_dir / f.name).write_bytes(body)
        index[f"{coll}/{doc_id}"] = f"{f.name}?v={hashlib.sha256(body).hexdigest()[:10]}"
    return index


def build(out: Path = WEB_DIR / "app.html", docs_dir: Path | None = None) -> Path:
    with tempfile.TemporaryDirectory(dir=ROOT / "data") as tmp:   # stay inside the project
        page_path = Path(tmp) / "page.html"
        r = subprocess.run([sys.executable, str(ARTIFACT / "build.py"), str(page_path)], capture_output=True, text=True)
        if r.returncode:
            raise SystemExit(f"artifact build failed:\n{r.stdout}{r.stderr}")
        page = page_path.read_text()
    index = publish_docs(docs_dir=docs_dir or out.parent / "docs")
    shim = SHIM % json.dumps(index, separators=(",", ":")).replace("</", "<\\/")
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
