# -*- coding: utf-8 -*-
"""V14 三阶段全量流水线：A 全部生成（ComfyUI 常驻）→ B 统一口型（释放显存一次）→ C 剪辑+海报。

比逐模板「生成→释放→口型→剪辑」快，避免每个模板都重载 H3 模型。
"""
from __future__ import annotations

import json
import pathlib
import shutil
import subprocess
import sys
import time

import httpx

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from pipeline import templates3d as t3  # noqa: E402
from pipeline import templates3d_semantic as sem  # noqa: E402

LOG = REPO / "tmp/v14/pipeline_run.log"
STATUS = REPO / "tmp/v14/pipeline_status.json"
FFMPEG = REPO / "tools/node_modules/ffmpeg-static/ffmpeg.exe"

ORDER = [
    "tonight_show", "classics", "history_talk", "masked_singer", "roundtable",
    "national_treasure", "hot_ones", "mystery", "qipashuo", "shark_tank",
    "slow_life", "snl_sketch", "standup", "taskmaster", "traitors", "history_mystery",
]


def log(message: str) -> None:
    line = f"[{time.strftime('%H:%M:%S')}] {message}"
    print(line, flush=True)
    with LOG.open("a", encoding="utf-8") as handle:
        handle.write(line + "\n")


def read_json(path: pathlib.Path, default=None):
    if path.is_file():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            return default
    return default


def write_status(data: dict) -> None:
    STATUS.parent.mkdir(parents=True, exist_ok=True)
    STATUS.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def free_comfy() -> None:
    try:
        httpx.post("http://127.0.0.1:8188/free",
                   json={"unload_models": True, "free_memory": True}, timeout=20)
        log("ComfyUI 显存已释放")
    except Exception as exc:  # noqa: BLE001
        log(f"释放显存失败: {exc}")


def shots_and_briefs(tid: str):
    catalog = t3.load_catalog()
    episode = t3.load_episode(tid)
    template = t3.load_template(tid)
    cached = read_json(REPO / f"contract/v5/cinema3d/templates3d/{tid}_semantic.json")
    if cached and cached.get("shots"):
        briefs = cached
    else:
        beats = sem.semantic_beats(tid, episode, template)
        briefs = sem.build_semantic_briefs(tid, catalog, episode, template, beats)
    todo = [s for s in briefs["shots"] if s.get("prompt")]
    return episode, briefs, todo


def lipsync_shot(tid: str, shot: dict) -> None:
    if not str(shot.get("line") or "").strip():
        return
    clip = t3.OUT3D / tid / "shots" / f"{shot['id']}.mp4"
    audio = t3.V5_OUT / tid / "work" / "audio" / f"{shot['id']}.wav"
    if not clip.is_file() or not audio.is_file():
        log(f"  lipsync skip {tid}/{shot['id']}: clip/audio missing")
        return
    tmp_out = t3.OUT3D / tid / "shots_lip" / f"{shot['id']}.mp4"
    tmp_out.parent.mkdir(parents=True, exist_ok=True)
    try:
        from pipeline import lipsync
        t0 = time.time()
        res = lipsync.sync_clip(clip, audio, tmp_out)
        ok = bool(res.get("ok"))
        detail = "" if ok else f" | {res.get('reason') or res.get('error')}"
        log(f"  lipsync {tid}/{shot['id']}: {'ok' if ok else 'fail'} {round(time.time() - t0, 1)}s{detail}")
        if ok and tmp_out.is_file():
            shutil.copy2(tmp_out, clip)
    except Exception as exc:  # noqa: BLE001
        log(f"  lipsync {tid}/{shot['id']} error: {type(exc).__name__}: {exc}")


def refresh_poster(tid: str) -> None:
    final = t3.OUT3D / tid / "final.mp4"
    if final.is_file():
        subprocess.run([
            str(FFMPEG), "-y", "-hide_banner", "-loglevel", "error",
            "-ss", "12", "-i", str(final), "-frames:v", "1", "-q:v", "3",
            "-vf", "scale=960:-1", str(t3.OUT3D / tid / "poster.jpg"),
        ], check=False)


def main() -> int:
    legacy = read_json(REPO / "tmp/v14/full_run_status.json", {}) or {}
    done = {tid for tid in ORDER if legacy.get(tid, {}).get("status") == "done"}
    pending = [tid for tid in ORDER if tid not in done]
    status = read_json(STATUS, {}) or {}
    for tid in done:
        status[tid] = {"status": "done", "note": "from previous run"}
    write_status(status)
    log(f"待处理 {len(pending)} 个模板：{', '.join(pending)}")

    # ---------- Phase A：全部生成（ComfyUI 常驻，不释放） ----------
    prepared = []
    for tid in pending:
        try:
            episode, briefs, todo = shots_and_briefs(tid)
        except Exception as exc:  # noqa: BLE001
            log(f"=== {tid} 准备失败: {exc}")
            status[tid] = {"status": "failed", "error": str(exc)[:300]}
            write_status(status)
            continue
        log(f"=== phaseA {tid}: {len(todo)} 镜 ===")
        status[tid] = {"status": "generating", "shots": len(todo)}
        write_status(status)
        for i, shot in enumerate(todo, 1):
            res = t3.generate_one(tid, shot, (768, 432), force=True)
            log(f"  [{i}/{len(todo)}] {shot['id']} {res.get('status')} {res.get('seconds', 0)}s"
                + ("" if res.get("status") != "failed" else f" | {res.get('error')}"))
        prepared.append((tid, episode, briefs, todo))
        status[tid] = {"status": "generated"}
        write_status(status)

    # ---------- Phase B：统一口型 ----------
    log(f"=== phaseB 口型同步（{len(prepared)} 个模板）===")
    free_comfy()
    for tid, episode, briefs, todo in prepared:
        log(f"--- lipsync {tid} ---")
        for shot in todo:
            lipsync_shot(tid, shot)
        status[tid] = {"status": "lipsynced"}
        write_status(status)

    # ---------- Phase C：剪辑 + 海报 ----------
    log(f"=== phaseC 剪辑（{len(prepared)} 个模板）===")
    for tid, episode, briefs, todo in prepared:
        try:
            report = t3.assemble(tid)
            refresh_poster(tid)
            size = report.get("probe", {}).get("format", {}).get("size")
            status[tid] = {"status": "done", "size": size}
            log(f"  {tid} 剪辑完成 size={size}")
        except Exception as exc:  # noqa: BLE001
            status[tid] = {"status": "assemble_failed", "error": str(exc)[:300]}
            log(f"  {tid} 剪辑失败: {exc}")
        write_status(status)

    log("三阶段流水线结束")
    return 0


if __name__ == "__main__":
    sys.exit(main())
