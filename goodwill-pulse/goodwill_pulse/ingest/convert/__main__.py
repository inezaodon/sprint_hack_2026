"""python -m goodwill_pulse.ingest.convert FILE [FILE ...] [--out DIR]: convert only (nothing is loaded)."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from . import convert


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m goodwill_pulse.ingest.convert", description=__doc__)
    p.add_argument("files", nargs="+")
    p.add_argument("--out", default="converted", help="folder for the spreadsheets (default ./converted)")
    args = p.parse_args(argv)
    for f in args.files:
        conv = convert(Path(f))
        conv.write(Path(args.out) / conv.sha256[:10])
        print(json.dumps(conv.summary(), indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
