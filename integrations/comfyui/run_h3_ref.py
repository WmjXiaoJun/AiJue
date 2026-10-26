#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""H3 动态镜头 CLI：音频驱动口型（ref2va）与首尾帧（fl2v）。

供 pipeline/dynamic.py 调用：
  python integrations/comfyui/run_h3_ref.py --mode audio_ref --image first.png \
      --audio shot.wav --out clips/dynamic_s001.mp4 --duration 5.49 \
      --width 768 --height 768 --fps 25 --seed 42
  python integrations/comfyui/run_h3_ref.py --mode fl2v --image first.png \
      --last-frame last.png --out out.mp4 --duration 4.0
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
TOOLS = HERE / "tools"
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(TOOLS))

from h3_dynamic_probe import (  # noqa: E402
    PROMPT_CN, PROMPT_FL2V, prepare_prompt, run_and_fetch, synth_audio,
)

COMFY = os.environ.get("COMFYUI_ROOT", r"D:\BaiduSyncdisk\wmj_work\XJ\ComfyUI")


NEUTRAL_PROMPT = (
    "subject_definitions: Subject 1 is the character shown in the reference image, "
    "preserving their exact face, hairstyle, hair color, outfit, age and art style.\n\n"
    "summary: One continuous medium close-up of Subject 1 speaking directly to the camera in a dark studio "
    "with warm golden rim light. His or her lips move naturally in sync with the reference speech audio, "
    "with subtle blinking, eyebrow movement and small head nods while talking. Keep the face, hairstyle, "
    "outfit and composition of the reference image stable; only the mouth, eyes and head move subtly. "
    "Anime cel-shaded style, clean line art, no text, no watermark.\n\n"
    "overall_soundscape: The same spoken sentence, close-mic voice, quiet studio room tone.\n\n"
    "non_diegetic_music: N/A")

def frame_length(duration: float) -> int:
    frames = max(124.0, float(duration or 5.0) * 24.0)
    k = max(7, int(round((frames - 5.0) / 17.0)))
    return 17 * k + 5


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", required=True, choices=["audio_ref", "fl2v"])
    ap.add_argument("--image", required=True)
    ap.add_argument("--last-frame", default=None)
    ap.add_argument("--audio", default=None)
    ap.add_argument("--audio-text", default=None)
    ap.add_argument("--voice", default="zh-CN-YunxiNeural")
    ap.add_argument("--prompt", default=None)
    ap.add_argument("--prompt-file", default=None)
    ap.add_argument("--out", required=True)
    ap.add_argument("--duration", type=float, default=5.0)
    ap.add_argument("--length", type=int, default=None,
                    help="显式帧数（17k+5，如 124/141/158）；给定时覆盖 duration 换算")
    ap.add_argument("--width", type=int, default=768)
    ap.add_argument("--height", type=int, default=768)
    ap.add_argument("--fps", type=int, default=24)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--timeout", type=int, default=1800)
    args = ap.parse_args(argv)

    if not Path(args.image).is_file():
        raise SystemExit(f"首帧不存在: {args.image}")
    last = Path(args.last_frame) if args.last_frame else None
    if last is not None and not last.is_file():
        raise SystemExit(f"尾帧不存在: {last}")

    prompt = args.prompt
    if args.prompt_file:
        prompt = Path(args.prompt_file).read_text(encoding="utf-8").strip()
    if not prompt:
        prompt = PROMPT_FL2V if args.mode == "fl2v" else NEUTRAL_PROMPT

    if args.length:
        length = int(args.length)
        if length < 5 or (length - 5) % 17 != 0:
            raise SystemExit(f"--length 必须是 17k+5（如 124/141/158），当前 {length}")
        length = min(3600, max(5, length))
    else:
        length = frame_length(args.duration)
    audio_rel = None
    if args.mode == "audio_ref":
        wav = args.audio
        if not wav:
            if not args.audio_text:
                raise SystemExit("audio_ref 模式需要 --audio 或 --audio-text")
            wav = str(REPO / "tmp" / "h3_ref_audio.wav")
            audio_rel = synth_audio(args.audio_text, args.voice, wav)
        else:
            relay_dir = Path(COMFY) / "input" / "zaopianju_dynamic"
            relay_dir.mkdir(parents=True, exist_ok=True)
            dst = relay_dir / Path(wav).name
            shutil.copyfile(wav, dst)
            audio_rel = "zaopianju_dynamic/%s" % Path(wav).name

    # 04/05 均支持任意 32 倍数分辨率（实测 16:9 1024x576 首尾帧正常、无拉伸）；
    # 统一对齐到 32 倍数，保留调用方给定宽高（成片再归一化到 1280×720）
    args.width = max(256, (args.width // 32) * 32)
    args.height = max(256, (args.height // 32) * 32)

    template = "05_ref2va_4step_sla.json" if args.mode == "audio_ref" else "04_i2v_fl2v_4step_sla.json"
    api, _ = prepare_prompt(
        template,
        str(Path(args.image).resolve()),
        audio_rel=audio_rel,
        last_image=str(last.resolve()) if last else None,
        prompt=prompt,
        width=args.width,
        height=args.height,
        length=length,
        seed=args.seed,
        filename_prefix="zaopianju_dynamic/%s" % Path(args.out).stem,
    )
    out = str(Path(args.out).resolve())
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    res = run_and_fetch(Path(args.out).stem, api, out)
    info = res.get("ffprobe") or {}
    print(json.dumps({
        "ok": True, "mode": args.mode, "out": out, "duration": args.duration,
        "length": length, "seed": args.seed, "gen_sec": res.get("gen_sec"),
        "workflow": template, "prompt_id": res.get("prompt_id"),
        "width": args.width, "height": args.height, "fps": args.fps,
        "has_audio": info.get("has_audio"), "probe": info, "sheet": res.get("sheet"),
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
