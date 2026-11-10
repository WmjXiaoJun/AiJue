# -*- coding: utf-8 -*-
"""V60：全部模板重制成「3D 写实 + 5 分钟」（HQ 3D 栈，不用 V5 平面 2D）。

流程（每个模板）：
1) v5_story.build_plan → 写 work/episode.json（台词/场景/时长占位）
2) templates3d_hq --style-mode 3d --force → SDXL 3D 关键帧 + H3 T2VA 镜头
3) 用真实镜头时长回写 episode.json
4) unique_fill.compose(300)：3D 镜头各用一次 + 3D 关键帧空镜 + 章节卡 + 花字字幕 + BGM

用法：
  python scripts/rebuild_all_3d_5min.py --only custom_tpl_v46_cyber
  python scripts/rebuild_all_3d_5min.py            # 全量（已达标跳过）
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

from pipeline import config, unique_fill, utils, v5_catalog, v5_story  # noqa: E402

TARGET = 300.0
OUT3D = config.OUTPUTS_ROOT / "templates_3d"
REPORT = config.OUTPUTS_ROOT / "longform" / "report_3d_5min.json"


def _final(tid: str) -> Path:
    return OUT3D / tid / f"final_{int(TARGET)}s.mp4"


def _done(tid: str) -> bool:
    path = _final(tid)
    if not path.is_file():
        return False
    try:
        return float(utils.probe_duration(path) or 0) >= TARGET - 1.0
    except Exception:
        return False


def _write_episode(tid: str, template: dict) -> dict:
    plan = v5_story.build_plan(template, TARGET)
    plan["meta"]["template"] = tid
    plan["meta"]["template_contract"] = template
    work = utils.ensure_dir(OUT3D / tid / "work")
    utils.write_json(work / "episode.json", plan)
    return plan


def _sync_durations(tid: str, plan: dict) -> dict:
    shots_dir = OUT3D / tid / "shots"
    for shot in plan.get("shots") or []:
        clip = shots_dir / f"{shot.get('id')}.mp4"
        if clip.is_file():
            dur = float(utils.probe_duration(clip) or 0)
            if dur > 0.2:
                shot["duration"] = round(dur, 3)
    utils.write_json(OUT3D / tid / "work" / "episode.json", plan)
    return plan


def _render_3d(tid: str, force: bool, log: Path) -> int:
    cmd = [sys.executable, "-m", "pipeline.templates3d_hq",
           "--template", tid, "--style-mode", "3d"]
    if force:
        cmd.append("--force")
    else:
        cmd.append("--reuse-shots")
    with log.open("a", encoding="utf-8") as fh:
        fh.write("\n$ " + " ".join(cmd) + "\n")
        fh.flush()
        return subprocess.call(cmd, cwd=str(REPO), stdout=fh, stderr=subprocess.STDOUT)


def build_one(tid: str, template: dict, force: bool = False) -> dict:
    started = time.time()
    log = utils.ensure_dir(config.OUTPUTS_ROOT / "longform" / "logs3d") / f"{tid}.log"
    plan = _write_episode(tid, template)
    code = _render_3d(tid, force or not (OUT3D / tid / "shots").is_dir(), log)
    shots = sorted((OUT3D / tid / "shots").glob("*.mp4")) if (OUT3D / tid / "shots").is_dir() else []
    if not shots:
        return {"template": tid, "ok": False, "stage": "render3d", "exit": code,
                "elapsed_sec": round(time.time() - started, 1)}
    if code != 0:
        # hq 的收尾 assemble 对 custom 模板会因内置路径缺失报错；镜头已出就算成功
        print(f"[warn] {tid} hq 退出码 {code}，但已产出 {len(shots)} 个镜头，继续合成", flush=True)
    _sync_durations(tid, plan)
    res = unique_fill.compose(tid, template, TARGET, force=True)
    return {"template": tid, **res, "stack": "hq_3d",
            "elapsed_sec": round(time.time() - started, 1)}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="3D 写实 5 分钟重制")
    ap.add_argument("--only", default="")
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args(argv)
    catalog = v5_catalog.load_catalog()
    templates = catalog.get("templates") or {}
    ids = ([t.strip() for t in args.only.split(",") if t.strip()] if args.only
           else sorted(templates))
    results = []
    if REPORT.is_file():
        try:
            results = json.loads(REPORT.read_text(encoding="utf-8")).get("items") or []
        except (OSError, ValueError):
            results = []
    done = {r.get("template") for r in results if r.get("ok")}
    for idx, tid in enumerate(ids, start=1):
        if tid in done and not args.force and _done(tid):
            print(f"[{idx}/{len(ids)}] {tid} 已达标，跳过", flush=True)
            continue
        print(f"[{idx}/{len(ids)}] {tid} 开始（3D）", flush=True)
        res = build_one(tid, templates.get(tid) or {}, force=args.force)
        print(f"[{idx}/{len(ids)}] {tid} -> ok={res.get('ok')} "
              f"dur={res.get('duration')} {res.get('elapsed_sec')}s", flush=True)
        results = [r for r in results if r.get("template") != tid] + [res]
        utils.write_json(REPORT, {"target": TARGET, "mode": "3d",
                                  "updated": time.strftime("%Y-%m-%d %H:%M:%S"),
                                  "items": results})
    ok = len([r for r in results if r.get("ok")])
    print(json.dumps({"ok": ok, "total": len(results)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
