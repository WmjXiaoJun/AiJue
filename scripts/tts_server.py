# -*- coding: utf-8 -*-
"""Breeze TTS 2 本地服务管理 CLI。

用法：
  python scripts/tts_server.py status           # 健康检查/模型/音色
  python scripts/tts_server.py start [--wait 300]
  python scripts/tts_server.py stop
  python scripts/tts_server.py say --role laozhou --text "..." --out tmp/demo.wav
  python scripts/tts_server.py say --role ali --text "第一句" --text "第二句" --out-dir tmp/ali
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main(argv=None) -> int:
    from pipeline import tts_breeze, utils

    ap = argparse.ArgumentParser(prog="python scripts/tts_server.py")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status")
    p_start = sub.add_parser("start")
    p_start.add_argument("--wait", type=float, default=300.0)
    sub.add_parser("stop")
    p_say = sub.add_parser("say")
    p_say.add_argument("--role", default="laozhou")
    p_say.add_argument("--text", action="append", required=True)
    p_say.add_argument("--out", default=None, help="单条输出 wav")
    p_say.add_argument("--out-dir", default="tmp/tts_demo")
    p_say.add_argument("--no-start", action="store_true")
    args = ap.parse_args(argv)

    if args.cmd == "status":
        print(json.dumps(tts_breeze.server_status(), ensure_ascii=False, indent=2))
        return 0
    if args.cmd == "start":
        ok = tts_breeze.start_server(wait_seconds=args.wait)
        print(json.dumps({"ok": ok, **tts_breeze.server_status()}, ensure_ascii=False))
        return 0 if ok else 1
    if args.cmd == "stop":
        ok = tts_breeze.stop_server()
        print(json.dumps({"ok": ok}, ensure_ascii=False))
        return 0 if ok else 1
    if args.cmd == "say":
        try:
            results = tts_breeze.synthesize(
                {args.role: args.text}, Path(args.out_dir),
                auto_start=not args.no_start)
        except Exception as exc:  # noqa: BLE001
            print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
            return 1
        if args.out and len(results) == 1:
            dst = Path(args.out)
            utils.ensure_dir(dst.parent)
            utils.ffmpeg(["-y", "-hide_banner", "-loglevel", "error",
                          "-i", results[0]["path"], "-ar", "48000", "-ac", "2", str(dst)])
            results[0]["out"] = str(dst)
        print(json.dumps({"ok": True, "role": args.role, "results": results},
                         ensure_ascii=False, indent=2))
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
