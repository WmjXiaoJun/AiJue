# -*- coding: utf-8 -*-
"""V68：跑完整一期（TTS 配音 → 立绘锁脸关键帧 → ref2va 镜头 → 合成/字幕/特效）。

用法：python scripts/run_episode.py --template classics --look 3d_anim [--force]
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

from pipeline import (config, render_v5, unique_fill, utils, v5_catalog,  # noqa: E402
                      v5_story)

OUT3D = config.OUTPUTS_ROOT / "templates_3d"


def _paths(tid: str) -> dict:
    from pipeline import templates3d as t3
    out_dir = OUT3D / tid
    return {
        # 音频必须落在 hq/ref2va 约定的 templates_v5/<tid>/work/audio
        "workdir": str(t3.V5_OUT / tid / "work"),
        "shots_dir": str(out_dir / "shots"),
        "out_dir": str(out_dir),
        "out": str(out_dir / "final_tmp.mp4"),
        "assets_dir": str(out_dir / "work" / "assets"),
        "frames_dir": str(out_dir / "frames"),
    }


def _write_episode(paths: dict, tid: str, plan: dict) -> list[str]:
    """写 episode.json（先备份已有文件，避免把共享证据无条件覆盖掉）。"""
    written = []
    for target in (Path(paths["workdir"]) / "episode.json",
                   OUT3D / tid / "work" / "episode.json"):
        if target.is_file():
            try:
                backup = target.with_name(target.name + ".bak")
                utils.ensure_dir(backup.parent)
                backup.write_bytes(target.read_bytes())  # 每次覆盖写：保留最近一次原文
            except OSError as exc:
                print(f"[episode] 备份失败（继续）: {target} {exc}", flush=True)
        utils.write_json(target, plan)
        written.append(str(target))
    return written


def build(tid: str, look: str, target: float = 300.0, force: bool = False) -> dict:
    catalog = v5_catalog.load_catalog()
    template = (catalog.get("templates") or {}).get(tid)
    if not template:
        return {"ok": False, "error": f"模板不存在: {tid}"}
    started = time.time()
    plan = v5_story.build_plan(template, target)
    plan["meta"]["template"] = tid
    plan["meta"]["template_contract"] = template
    paths = _paths(tid)
    utils.ensure_dir(paths["workdir"])
    utils.ensure_dir(paths["shots_dir"])
    utils.ensure_dir(Path(paths["workdir"]) / "audio")
    utils.ensure_dir(OUT3D / tid / "work")

    def prog(pct, msg=""):
        print(f"[tts {pct}] {msg}", flush=True)

    # 1) 配音（有台词的镜头拿到真实语音；无台词镜头后面补静音）
    try:
        info = render_v5.tts_and_pace(plan, catalog, paths, prog, force=False)
        print("[tts] done:", json.dumps({k: info.get(k) for k in ("shots", "duration")},
                                        ensure_ascii=False), flush=True)
    except Exception as exc:  # noqa: BLE001
        print("[tts] 失败，继续静音：", exc, flush=True)

    # 2) 写回真实镜头时长（若有旧镜头）
    for shot in plan["shots"]:
        clip = Path(paths["shots_dir"]) / f"{shot['id']}.mp4"
        if clip.is_file():
            d = float(utils.probe_duration(clip) or 0)
            if d > 0.2:
                shot["duration"] = round(d, 3)
    episodes = _write_episode(paths, tid, plan)

    # 3) 立绘锁脸渲染（hq --identity keyframe）；--force 由参数决定，不再写死
    cmd = [sys.executable, "-m", "pipeline.templates3d_hq", "--template", tid,
           "--style-mode", "3d" if look != "2d_anim" else "2d",
           "--2d-style", "arcane_cinematic", "--look", look,
           "--identity", "keyframe"]
    if force:
        cmd.append("--force")
    log = utils.ensure_dir(config.OUTPUTS_ROOT / "longform" / "logs_episode") / f"{tid}_{look}.log"
    with log.open("a", encoding="utf-8") as fh:
        fh.write("\n$ " + " ".join(cmd) + "\n")
        fh.flush()
        code = subprocess.call(cmd, cwd=str(REPO), stdout=fh, stderr=subprocess.STDOUT)
    shots = sorted(Path(paths["shots_dir"]).glob("*.mp4"))
    print(f"[render] exit={code} shots={len(shots)} (log: {log})", flush=True)
    if code != 0:
        # 渲染失败还去 compose = 用「已存在的旧镜头 + 缺镜」合出一版"看起来成功"的成片
        return {"ok": False, "template": tid, "look": look, "stage": "render",
                "exit": code, "shots": len(shots), "log": str(log),
                "episodes": episodes,
                "elapsed_sec": round(time.time() - started, 1)}
    if not shots:
        return {"ok": False, "template": tid, "look": look, "stage": "render",
                "exit": code, "shots": 0, "log": str(log), "episodes": episodes,
                "error": "渲染成功但 shots/ 没有 mp4",
                "elapsed_sec": round(time.time() - started, 1)}

    # 4) 合成（字幕/3D 标题/特效/精确 target 秒）
    res = unique_fill.compose(tid, template, target, force=True,
                              shots_subdir="shots", out_suffix=f"_{look}")
    return {"ok": bool(res.get("ok")), "template": tid, "look": look, **res,
            "exit": code, "shots": len(shots), "log": str(log),
            "episodes": episodes,
            "elapsed_sec": round(time.time() - started, 1)}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--template", required=True)
    ap.add_argument("--look", default="3d_anim",
                    choices=("3d_anim", "2d_anim", "realistic"))
    ap.add_argument("--target", type=float, default=300.0)
    ap.add_argument("--force", action="store_true",
                    help="忽略关键帧/镜头缓存重渲（默认增量）")
    args = ap.parse_args(argv)
    res = build(args.template, args.look, args.target, force=bool(args.force))
    print(json.dumps(res, ensure_ascii=False))
    return 0 if res.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
