"""Build the single-file artifact page from parts.

    .venv/bin/python artifact/build.py [out.html]      (default: artifact/dist/pulse.html)

shell.html holds the page skeleton; parts/css/*.css and parts/*.js are concatenated in file-name order into it.
Numbered names set the order: 00 core, 10 data, 20-80 views, 90 shell (always last). Add a feature by adding a
parts/NN_name.js that calls registerTab(id, label, viewFn, order), and parts/css/NN_name.css for its styles.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent


def build(out: Path | None = None) -> Path:
    out = out or HERE / "dist" / "pulse.html"
    css = "\n".join(p.read_text() for p in sorted((HERE / "parts" / "css").glob("*.css")))
    js = "\n".join(p.read_text() for p in sorted((HERE / "parts").glob("*.js")))
    html = (HERE / "shell.html").read_text().replace("/*__CSS__*/", css).replace("/*__JS__*/", js)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html)
    check = out.with_suffix(".check.js")
    check.write_text(js)
    r = subprocess.run(["node", "--check", str(check)], capture_output=True, text=True)
    check.unlink()
    if r.returncode:
        raise SystemExit("JS syntax error:\n" + r.stderr)
    print(f"built {out} ({len(html):,} bytes), syntax ok")
    return out


if __name__ == "__main__":
    build(Path(sys.argv[1]) if len(sys.argv) > 1 else None)
