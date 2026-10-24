# -*- coding: utf-8 -*-
"""翻译 CLI：units.json（[{key,text}]）→ translations.json。"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from .translator import translate_units


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="infile", required=True)
    ap.add_argument("--out", dest="outfile", required=True)
    ap.add_argument("--lang", required=True)
    ap.add_argument("--provider", default="ollama", choices=["ollama", "nllb", "mock"])
    args = ap.parse_args(argv)

    units = json.loads(Path(args.infile).read_text(encoding="utf-8"))
    if isinstance(units, dict):
        units = units.get("units") or []
    result = translate_units(units, args.lang, provider=args.provider)
    Path(args.outfile).parent.mkdir(parents=True, exist_ok=True)
    Path(args.outfile).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: result[k] for k in ("ok", "lang", "provider", "model", "count", "elapsed_sec")}, ensure_ascii=False))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
