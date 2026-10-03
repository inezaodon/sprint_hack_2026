"""One command for the whole data platform.

    .venv/bin/python -m goodwill_pulse.build            # truth -> 7 source DBs -> harmonized -> quality checks
    .venv/bin/python -m goodwill_pulse.build --from harmonize

Each stage also runs alone (see docs/CONTRACT.md). Stages run in order; a failure stops the build and names the stage.
"""
from __future__ import annotations

import argparse
import importlib
import time
from pathlib import Path

from .config import DATA_DIR

SOURCES_DIR = DATA_DIR / "sources"
TRUTH = SOURCES_DIR / "_truth.duckdb"
HARMONIZED = DATA_DIR / "harmonized.duckdb"
SOURCE_DBS = ["ops", "amazon", "ebay", "shopgoodwill", "goodwillfinds", "goodwillbooks", "finance"]


def _stage_truth(seed: int) -> dict:
    return importlib.import_module("goodwill_pulse.gen.truth").build(TRUTH, seed=seed)


def _stage_source(name: str, seed: int) -> dict:
    return importlib.import_module(f"goodwill_pulse.sources.{name}").build(TRUTH, SOURCES_DIR / f"{name}.duckdb", seed=seed)


def _stage_harmonize(seed: int) -> dict:
    return importlib.import_module("goodwill_pulse.harmonize").build(SOURCES_DIR, HARMONIZED)


def _stage_quality(seed: int) -> dict:
    results = importlib.import_module("goodwill_pulse.quality").run_checks(HARMONIZED, SOURCES_DIR)
    failed = [r for r in results if r.get("status") == "fail"]
    return {"checks": len(results), "failed": len(failed),
            "errors": [r["check_id"] for r in failed if r.get("severity") == "error"]}


def stages() -> list[tuple[str, callable]]:
    out = [("truth", _stage_truth)]
    out += [(f"source:{n}", (lambda n: lambda seed: _stage_source(n, seed))(n)) for n in SOURCE_DBS]
    out += [("harmonize", _stage_harmonize), ("quality", _stage_quality)]
    return out


def run(start_at: str | None = None, seed: int = 7) -> list[dict]:
    SOURCES_DIR.mkdir(parents=True, exist_ok=True)
    plan = stages()
    if start_at:
        names = [n for n, _ in plan]
        match = [i for i, n in enumerate(names) if n == start_at or n.startswith(start_at)]
        if not match:
            raise SystemExit(f"unknown stage {start_at!r}; stages: {', '.join(names)}")
        plan = plan[match[0]:]
    log = []
    for name, fn in plan:
        t0 = time.perf_counter()
        try:
            result = fn(seed)
        except Exception as e:  # name the stage, keep the traceback
            raise RuntimeError(f"build stage '{name}' failed: {e}") from e
        log.append({"stage": name, "seconds": round(time.perf_counter() - t0, 1), "result": result})
        print(f"{name:<22} {log[-1]['seconds']:>6.1f}s  {result}")
    return log


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--from", dest="start_at")
    p.add_argument("--seed", type=int, default=7)
    a = p.parse_args()
    run(a.start_at, a.seed)


if __name__ == "__main__":
    main()
