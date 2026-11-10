# -*- coding: utf-8 -*-
"""V54：把全部模板重制成「满 5 分钟」版本。

- V5 渲染栈（内建 16 + custom_holo_talk）：`render_v5 --duration 300`（真扩写分镜并重渲）
- HQ 短片栈（custom_tpl_*）：重合成 → `pipeline.longform` 循环变体扩到 300s（不重渲 H3 镜头）

用法：
  python scripts/rebuild_all_5min.py            # 全量（已达标会跳过）
  python scripts/rebuild_all_5min.py --only tonight_show,classics
  python scripts/rebuild_all_5min.py --force
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from pipeline import config, longform, utils, v5_catalog  # noqa: E402
from pipeline.agents.workflow import is_shorts  # noqa: E402

TARGET = 300.0
REPORT = config.OUTPUTS_ROOT / "longform" / "report_5min.json"


def _final(tid: str) -> Path | None:
    for directory in (config.OUTPUTS_ROOT / "templates_v5",
                      config.OUTPUTS_ROOT / "templates_3d"):
        for name in (f"final_{int(TARGET)}s.mp4", "final.mp4"):
            candidate = directory / tid / name
            if candidate.is_file():
                return candidate
    return None


def _done(tid: str) -> bool:
    final = _final(tid)
    if final is None:
        return False
    try:
        return float(utils.probe_duration(final) or 0) >= TARGET - 1.0
    except Exception:  # noqa: BLE001
        return False


def _run(cmd: list[str], log: Path) -> int:
    with log.open("a", encoding="utf-8") as fh:
        fh.write("\n$ " + " ".join(cmd) + "\n")
        fh.flush()
        return subprocess.call(cmd, cwd=str(REPO), stdout=fh, stderr=subprocess.STDOUT)


def build_one(tid: str, template: dict, force: bool = False) -> dict:
    started = time.time()
    log = utils.ensure_dir(config.OUTPUTS_ROOT / "longform" / "logs") / f"{tid}.log"
    if template and is_shorts(tid, template):
        # 短片栈：已有镜头各用一次 + 新生成空镜交错（不循环、不复用片段）
        from pipeline import unique_fill  # noqa: PLC0415

        res = unique_fill.compose(tid, template, TARGET, force=force)
        return {"template": tid, "stack": "hq_shorts", **res,
                "elapsed_sec": round(time.time() - started, 1)}
    out = config.OUTPUTS_ROOT / "templates_v5" / tid / f"final_{int(TARGET)}s.mp4"
    if out.is_file() and not force:
        return {"template": tid, "stack": "v5_render", "ok": True, "cached": True,
                "duration": utils.probe_duration(out)}
    code = _run([sys.executable, "-m", "pipeline.cli", "render_v5",
                 "--template", tid, "--duration", str(TARGET), "--force",
                 "--out", str(out)], log)
    ok = code == 0 and out.is_file()
    return {"template": tid, "stack": "v5_render", "ok": ok, "exit": code,
            "out": str(out) if ok else None,
            "duration": (utils.probe_duration(out) if ok else None),
            "elapsed_sec": round(time.time() - started, 1)}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="重制全部模板为 5 分钟")
    ap.add_argument("--only", default="")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args(argv)
    catalog = v5_catalog.load_catalog()
    templates = catalog.get("templates") or {}
    ids = ([t.strip() for t in args.only.split(",") if t.strip()] if args.only
           else sorted(templates))
    if args.limit:
        ids = ids[: args.limit]
    results = []
    if REPORT.is_file():
        try:
            results = json.loads(REPORT.read_text(encoding="utf-8")).get("items") or []
        except (OSError, ValueError):
            results = []
    done = {r.get("template") for r in results if r.get("ok")}
    for idx, tid in enumerate(ids, start=1):
        if tid in done and not args.force and _done(tid):
            print(f"[{idx}/{len(ids)}] {tid} 已达标，跳过")
            continue
        print(f"[{idx}/{len(ids)}] {tid} 开始", flush=True)
        res = build_one(tid, templates.get(tid) or {}, force=args.force)
        print(f"[{idx}/{len(ids)}] {tid} -> ok={res.get('ok')} "
              f"dur={res.get('duration')} {res.get('elapsed_sec')}s", flush=True)
        results = [r for r in results if r.get("template") != tid] + [res]
        utils.write_json(REPORT, {"target": TARGET, "updated": time.strftime("%Y-%m-%d %H:%M:%S"),
                                  "items": results})
    ok_count = len([r for r in results if r.get("ok")])
    print(json.dumps({"ok": ok_count, "total": len(results), "report": str(REPORT)},
                     ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())