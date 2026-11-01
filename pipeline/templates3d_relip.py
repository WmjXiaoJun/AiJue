# -*- coding: utf-8 -*-
"""V15：把「口型」放进生成阶段 —— 对话镜改用 H3 ref2va（首帧 + 该镜 TTS 音频）原生音驱动生成。

替代上一版 MuseTalk 后处理（嘴部贴片会出现马赛克/色块）。
流程：
1. 对每个模板的每个有台词镜头：取当前镜头首帧 + 该镜 TTS wav → run_h3_ref.py --mode audio_ref
   → 输出替换 shots/<sid>.mp4（原生嘴型，无贴片）。
2. 全部模板处理完 → 重新剪辑 + 刷新海报。
3. 旧 MuseTalk 版成片先备份到 outputs/_archive_v14_musetalk/。
"""
from __future__ import annotations

import json
import pathlib
import shutil
import subprocess
import sys
import time

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from pipeline import templates3d as t3  # noqa: E402

FFMPEG = REPO / "tools/node_modules/ffmpeg-static/ffmpeg.exe"
H3_REF = REPO / "integrations/comfyui/run_h3_ref.py"
LOG = REPO / "tmp/v15/relip_run.log"
STATUS = REPO / "tmp/v15/relip_status.json"
ARCHIVE = REPO / "outputs/_archive_v14_musetalk"
WORK = REPO / "tmp/v15/relip"

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


def archive_final(tid: str) -> None:
    final = t3.OUT3D / tid / "final.mp4"
    if not final.is_file():
        return
    ARCHIVE.mkdir(parents=True, exist_ok=True)
    target = ARCHIVE / f"{tid}.mp4"
    if not target.is_file():
        shutil.copy2(final, target)


def relip_shot(tid: str, shot: dict, episode: dict, seed_base: int) -> bool:
    sid = shot["id"]
    line = str(shot.get("line") or "").strip()
    if not line:
        return False
    duration = float(shot.get("duration") or 5.0)
    clip = t3.OUT3D / tid / "shots" / f"{sid}.mp4"
    audio = t3.V5_OUT / tid / "work" / "audio" / f"{sid}.wav"
    if not clip.is_file() or not audio.is_file():
        log(f"  skip {tid}/{sid}: clip/audio missing")
        return False
    WORK.mkdir(parents=True, exist_ok=True)
    first = WORK / f"{tid}_{sid}_first.png"
    out = WORK / f"{tid}_{sid}_ref2va.mp4"
    subprocess.run([str(FFMPEG), "-y", "-hide_banner", "-loglevel", "error",
                    "-i", str(clip), "-frames:v", "1", str(first)], check=False)
    if not first.is_file():
        log(f"  skip {tid}/{sid}: first frame failed")
        return False
    t0 = time.time()
    proc = subprocess.run([
        sys.executable, str(H3_REF),
        "--mode", "audio_ref",
        "--image", str(first),
        "--audio", str(audio),
        "--out", str(out),
        "--duration", f"{duration:.3f}",
        "--width", "768", "--height", "432",
        "--fps", "24",
        "--seed", str(seed_base + abs(hash(tid + sid)) % 100000),
        "--timeout", "900",
    ], capture_output=True, text=True, encoding="utf-8", errors="replace")
    elapsed = round(time.time() - t0, 1)
    if proc.returncode != 0 or not out.is_file() or out.stat().st_size < 50_000:
        tail = (proc.stderr or proc.stdout or "")[-400:]
        log(f"  ref2va {tid}/{sid}: FAIL {elapsed}s | {tail}")
        return False
    shutil.copy2(out, clip)
    log(f"  ref2va {tid}/{sid}: ok {elapsed}s")
    return True


def main() -> int:
    status = read_json(STATUS, {}) or {}
    seed_base = 20261102
    started = time.time()

    # Phase 1：逐模板 relip（ComfyUI 常驻，不卸载）
    for tid in ORDER:
        if status.get(tid, {}).get("status") == "done":
            log(f"skip {tid} (done)")
            continue
        episode = t3.load_episode(tid)
        shots = [s for s in episode["shots"] if str(s.get("line") or "").strip()]
        archive_final(tid)
        log(f"=== relip {tid}: {len(shots)} 个对话镜 ===")
        status[tid] = {"status": "relip", "total": len(shots), "done": 0}
        write_status(status)
        ok = 0
        for shot in shots:
            if relip_shot(tid, shot, episode, seed_base):
                ok += 1
            status[tid]["done"] = ok
            write_status(status)
        status[tid] = {"status": "relip_done", "done": ok, "total": len(shots)}
        write_status(status)

    # Phase 2：剪辑 + 海报
    for tid in ORDER:
        st = status.get(tid, {})
        if st.get("status") == "done":
            continue
        log(f"--- assemble {tid} ---")
        try:
            report = t3.assemble(tid)
            final = t3.OUT3D / tid / "final.mp4"
            subprocess.run([str(FFMPEG), "-y", "-hide_banner", "-loglevel", "error",
                            "-ss", "12", "-i", str(final), "-frames:v", "1", "-q:v", "3",
                            "-vf", "scale=960:-1", str(t3.OUT3D / tid / "poster.jpg")], check=False)
            status[tid] = {"status": "done",
                           "size": report.get("probe", {}).get("format", {}).get("size")}
            log(f"  {tid} 完成")
        except Exception as exc:  # noqa: BLE001
            status[tid] = {"status": "assemble_failed", "error": str(exc)[:300]}
            log(f"  {tid} 失败: {exc}")
        write_status(status)

    log(f"relip 全量结束，总耗时 {round(time.time() - started, 1)}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
