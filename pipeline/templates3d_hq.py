# -*- coding: utf-8 -*-
"""V16 高清样片：1024×576 语义 T2VA（保留运镜/远景）+ 1024×576 ref2va 原生口型（带动镜头提示）。

用法：
    python -m pipeline.templates3d_hq --template tonight_show [--skip-t2va] [--skip-ref2va]

流程：
1. 备份当前 768 版镜头到 outputs/_archive_v15_ref2va_768/<tid>/shots/
2. 每镜先用语义 prompt 做 T2VA 1024×576（画面 + 运镜 + 远景）
3. 有台词镜头：取 T2VA 首帧 + 该镜 TTS 音频 + 「带运镜/中景」提示词 → ref2va 1024×576 原生口型
4. 无台词镜头：直接用 T2VA 1024×576
5. 剪辑 + 海报
"""
from __future__ import annotations

import argparse
import json
import pathlib
import random
import shutil
import subprocess
import sys
import time

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from pipeline import cinema3d_local as local  # noqa: E402
from pipeline import templates3d as t3  # noqa: E402

FFMPEG = REPO / "tools/node_modules/ffmpeg-static/ffmpeg.exe"
H3_REF = REPO / "integrations/comfyui/run_h3_ref.py"
WORK = REPO / "tmp/v16"
LOG = WORK / "hq_run.log"

MOTION_WORDS = {
    "push_in": "the camera slowly pushes in",
    "pull_out": "the camera slowly pulls back revealing the space",
    "pan_right": "the camera pans smoothly across the scene",
    "pan_left": "the camera pans smoothly across the scene",
    "dolly": "the camera dollies slowly through the space",
    "crane_up": "the camera rises on a slow crane move",
    "crane_down": "the camera descends on a slow crane move",
    "rack_focus": "the camera racks focus between foreground and background",
    "orbit": "the camera orbits slowly around the subject",
    "handheld": "the camera drifts with subtle handheld movement",
}


def log(message: str) -> None:
    line = f"[{time.strftime('%H:%M:%S')}] {message}"
    print(line, flush=True)
    WORK.mkdir(parents=True, exist_ok=True)
    with LOG.open("a", encoding="utf-8") as handle:
        handle.write(line + "\n")


def briefs_for(tid: str) -> dict:
    cached = REPO / f"contract/v5/cinema3d/templates3d/{tid}_semantic.json"
    if cached.is_file():
        return json.loads(cached.read_text(encoding="utf-8"))
    raise SystemExit(f"缺少语义 brief: {cached}")


def frame_length(duration: float) -> int:
    frames = max(124.0, float(duration or 5.0) * 24.0)
    k = max(7, int(round((frames - 5.0) / 17.0)))
    return 17 * k + 5


def gen_t2va(tid: str, shot: dict, seed: int) -> pathlib.Path:
    out = WORK / "clips" / f"{tid}_{shot['id']}_t2va1024.mp4"
    if out.is_file() and out.stat().st_size > 200_000:
        return out
    duration = float(shot.get("duration") or 5.0)
    length = frame_length(duration)
    wf = local.build_workflow(shot["prompt"], 1024, 576, length, seed,
                              f"v16/{tid}/{shot['id']}_t2va")
    pid = local.submit(wf)
    res = local.wait_and_fetch(pid, out, timeout=1800)
    if not out.is_file():
        raise RuntimeError(f"T2VA 失败: {res}")
    return out


def motion_prompt(shot: dict) -> str:
    visual = str(shot.get("semantic_visual") or "").strip()
    camera_text = ""
    prompt = str(shot.get("prompt") or "")
    if "Camera:" in prompt:
        camera_text = prompt.split("Camera:", 1)[1].split(".", 1)[0].strip()
    move = ""
    for key, text in MOTION_WORDS.items():
        if key in camera_text:
            move = text
            break
    if not move:
        move = "the camera drifts gently across the scene"
    return (
        "subject_definitions: Subject 1 is the character shown in the reference image, preserving their exact face, "
        "hairstyle, hair color, outfit, age and 3D animated art style.\n\n"
        f"summary: {visual or 'A cinematic 3D animated scene.'} The shot is a medium or medium-wide cinematic composition "
        f"that keeps the surrounding set visible; {move}. The character speaks naturally with lips moving in sync with the "
        "reference speech audio, blinking and gesturing, while the camera and body have gentle real motion. Keep the "
        "character identity consistent with the reference image. 3D animated feature film quality, rich physical "
        "materials, volumetric light, shallow depth of field. no text, no subtitles, no watermark.\n\n"
        "overall_soundscape: the same spoken sentence, close-mic voice, quiet room tone.\n\n"
        "non_diegetic_music: N/A"
    )


def gen_ref2va(tid: str, shot: dict, t2va: pathlib.Path, seed: int) -> pathlib.Path:
    out = WORK / "clips" / f"{tid}_{shot['id']}_ref2va1024.mp4"
    if out.is_file() and out.stat().st_size > 200_000:
        return out
    first = WORK / "clips" / f"{tid}_{shot['id']}_first.png"
    subprocess.run([str(FFMPEG), "-y", "-hide_banner", "-loglevel", "error",
                    "-i", str(t2va), "-frames:v", "1", str(first)], check=False)
    audio = t3.V5_OUT / tid / "work" / "audio" / f"{shot['id']}.wav"
    prompt_file = WORK / "clips" / f"{tid}_{shot['id']}_prompt.txt"
    prompt_file.write_text(motion_prompt(shot), encoding="utf-8")
    duration = float(shot.get("duration") or 5.0)
    proc = subprocess.run([
        sys.executable, str(H3_REF), "--mode", "audio_ref",
        "--image", str(first), "--audio", str(audio), "--out", str(out),
        "--duration", f"{duration:.3f}", "--width", "1024", "--height", "576",
        "--fps", "24", "--seed", str(seed), "--prompt-file", str(prompt_file),
        "--timeout", "1200",
    ], capture_output=True, text=True, encoding="utf-8", errors="replace")
    if proc.returncode != 0 or not out.is_file():
        raise RuntimeError(f"ref2va 失败: {(proc.stderr or '')[-300:]}")
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--template", required=True)
    ap.add_argument("--skip-t2va", action="store_true")
    ap.add_argument("--skip-ref2va", action="store_true")
    args = ap.parse_args(argv)
    tid = args.template
    briefs = briefs_for(tid)
    shots = [s for s in briefs["shots"] if s.get("prompt")]
    archive = REPO / f"outputs/_archive_v15_ref2va_768/{tid}/shots"
    archive.mkdir(parents=True, exist_ok=True)
    out_shots = t3.OUT3D / tid / "shots"
    out_shots.mkdir(parents=True, exist_ok=True)  # 新模板首次生成时目录不存在
    for shot in shots:
        src = out_shots / f"{shot['id']}.mp4"
        dst = archive / f"{shot['id']}.mp4"
        if src.is_file() and not dst.is_file():
            shutil.copy2(src, dst)
    log(f"=== {tid}: {len(shots)} 镜，1024×576 T2VA + ref2va ===")
    for i, shot in enumerate(shots, 1):
        seed = 20261102 + abs(hash(tid + shot["id"])) % 100000
        t2va = gen_t2va(tid, shot, seed)
        final_src = t2va
        if str(shot.get("line") or "").strip() and not args.skip_ref2va:
            final_src = gen_ref2va(tid, shot, t2va, seed)
        shutil.copy2(final_src, out_shots / f"{shot['id']}.mp4")
        log(f"[{i}/{len(shots)}] {shot['id']} ok ({'ref2va' if final_src != t2va else 't2va'})")
    log("=== 剪辑 ===")
    report = t3.assemble(tid)
    final = t3.OUT3D / tid / "final.mp4"
    # 海报取无台词镜头
    broll = next((s for s in shots if not str(s.get("line") or "").strip()), shots[0])
    clip = out_shots / f"{broll['id']}.mp4"
    if clip.is_file():
        subprocess.run([str(FFMPEG), "-y", "-hide_banner", "-loglevel", "error",
                        "-ss", "2.2", "-i", str(clip), "-frames:v", "1", "-q:v", "3",
                        "-vf", "scale=960:-1", str(t3.OUT3D / tid / "poster.jpg")], check=False)
    log(f"完成: {json.dumps(report.get('probe', {}).get('format', {}), ensure_ascii=False)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
