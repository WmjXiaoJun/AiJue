# -*- coding: utf-8 -*-
"""V17：生成新增的写实模板（real_street / real_cafe / real_kitchen）。

流程：校验数据 → 语义 brief（写实 look）→ 静音补全 → 1024×576 T2VA + ref2va → 剪辑 + 海报 → 写 progress。

用法：python -m pipeline.templates3d_new_templates [--templates real_street,real_cafe,real_kitchen]
"""
from __future__ import annotations

import argparse
import json
import pathlib
import subprocess
import sys
import time

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from pipeline import templates3d as t3  # noqa: E402
from pipeline import templates3d_hq as hq  # noqa: E402
from pipeline import templates3d_semantic as sem  # noqa: E402

FFMPEG = REPO / "tools/node_modules/ffmpeg-static/ffmpeg.exe"
LOG = REPO / "tmp/v17/new_templates_run.log"
STATUS = REPO / "tmp/v17/new_templates_status.json"
PROGRESS = REPO / "outputs/templates_3d/progress.json"


def log(message: str) -> None:
    line = f"[{time.strftime('%H:%M:%S')}] {message}"
    print(line, flush=True)
    LOG.parent.mkdir(parents=True, exist_ok=True)
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


def ensure_data(tid: str) -> dict:
    template = REPO / f"contract/v5/templates/{tid}.json"
    episode = REPO / f"outputs/templates_v5/{tid}/work/episode.json"
    if not template.is_file() or not episode.is_file():
        raise RuntimeError(f"数据未就绪：{template} / {episode}")
    return json.loads(episode.read_text(encoding="utf-8"))


def ensure_silent_audio(tid: str, episode: dict) -> None:
    audio_dir = REPO / f"outputs/templates_v5/{tid}/work/audio"
    audio_dir.mkdir(parents=True, exist_ok=True)
    for shot in episode.get("shots", []):
        sid = shot["id"]
        wav = audio_dir / f"{sid}.wav"
        if wav.is_file() and wav.stat().st_size > 1000:
            continue
        duration = max(1.0, float(shot.get("duration") or 3.0))
        subprocess.run([
            str(FFMPEG), "-y", "-hide_banner", "-loglevel", "error",
            "-f", "lavfi", "-i", f"anullsrc=r=24000:cl=mono:d={duration:.3f}",
            "-t", f"{duration:.3f}", str(wav),
        ], check=False)
        log(f"  静音补全 {tid}/{sid} {duration:.2f}s")


def ensure_brief(tid: str, episode: dict, template: dict) -> dict:
    path = REPO / f"contract/v5/cinema3d/templates3d/{tid}_semantic.json"
    cached = read_json(path)
    if cached and cached.get("shots"):
        log(f"  brief 已存在 {path.name}")
        return cached
    catalog = t3.load_catalog()
    log(f"  LLM 生成语义画面（{len(episode.get('shots', []))} 镜）")
    beats = sem.semantic_beats(tid, episode, template)
    briefs = sem.build_semantic_briefs(tid, catalog, episode, template, beats)
    return briefs


def update_progress(tid: str, report: dict, episode: dict, briefs: dict) -> None:
    progress = read_json(PROGRESS, {}) or {}
    shots = [s for s in briefs.get("shots", []) if s.get("prompt")]
    clips = {s["id"]: {"id": s["id"], "status": "ok"} for s in shots}
    progress[tid] = {
        "status": "done",
        "done": len(shots),
        "total": len(shots),
        "clips": clips,
        "updated": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "final": str(t3.OUT3D / tid / "final.mp4"),
        "probe": report.get("probe"),
        "mode": "semantic_hq_realistic",
    }
    PROGRESS.parent.mkdir(parents=True, exist_ok=True)
    PROGRESS.write_text(json.dumps(progress, ensure_ascii=False, indent=1), encoding="utf-8")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--templates", default="real_street,real_cafe,real_kitchen")
    args = ap.parse_args(argv)
    tids = [x.strip() for x in args.templates.split(",") if x.strip()]
    status = read_json(STATUS, {}) or {}
    for tid in tids:
        if status.get(tid, {}).get("status") == "done":
            log(f"skip {tid}")
            continue
        log(f"=== 写实模板 {tid} 开始 ===")
        t0 = time.time()
        try:
            episode = ensure_data(tid)
            template = json.loads((REPO / f"contract/v5/templates/{tid}.json").read_text(encoding="utf-8"))
            ensure_silent_audio(tid, episode)
            briefs = ensure_brief(tid, episode, template)
            code = hq.main(["--template", tid])
            if code != 0:
                raise RuntimeError(f"hq exit {code}")
            report = read_json(t3.OUT3D / tid / "report.json", {}) or {}
            update_progress(tid, report, episode, briefs)
            status[tid] = {"status": "done", "elapsed_sec": round(time.time() - t0, 1)}
            log(f"=== {tid} 完成 {status[tid]['elapsed_sec']}s ===")
        except Exception as exc:  # noqa: BLE001
            status[tid] = {"status": "failed", "error": f"{type(exc).__name__}: {exc}"[-500:]}
            log(f"=== {tid} 失败: {exc} ===")
        write_status(status)
    return 0


if __name__ == "__main__":
    sys.exit(main())
