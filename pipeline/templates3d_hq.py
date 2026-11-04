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
import re
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

STYLE_2D_PREFIX = ("2D anime, cel-shaded, flat colors, clean line art, "
                   "hand-drawn animation, anime screenshot")
STYLE_2D_QUALITY = ("2D anime film quality, clean line art, flat cel-shaded colors, "
                    "hand-drawn animation, anime screenshot")
STYLE_2D_BROLL = ("anime key visual, Japanese TV anime screenshot, clean lineart, "
                  "flat cel-shaded colors, detailed background, no 3D, no cgi")
STYLE_2D_CACHE = "_2d_v43"


def apply_2d_style(prompt: str, broll: bool = False) -> str:
    """V42/V43：把 3D/T2VA 提示词改写为 2D 动漫风格（cel-shaded/flat/line art）。"""
    text = str(prompt or "")
    text = re.sub(r"3D animated feature film quality[^.]*\.",
                  STYLE_2D_QUALITY + ".", text, flags=re.IGNORECASE)
    text = re.sub(r"(?:3D|3d)\s+toon", "2D anime", text)
    text = re.sub(r"stylized\s+(?:3D|3d)\s+animated", "stylized 2D anime", text)
    text = re.sub(r"(?:3D|3d)\s+animated", "2D anime", text)
    text = re.sub(r"(?:3D|3d)\s+animated\s+art\s+style", "2D anime art style", text)
    text = re.sub(r"\b(?:3D|3d)\b", "2D", text)
    text = re.sub(r"\brich physical materials,\s*", "", text, flags=re.IGNORECASE)
    text = re.sub(r"volumetric light", "flat cel-shaded lighting", text, flags=re.IGNORECASE)
    text = STYLE_2D_PREFIX + ". " + text
    if broll:
        text += " " + STYLE_2D_BROLL + "."
    return text


# --------------------------------------------------------------------------- #
# V43：2D 对话镜头关键帧（SDXL anime + IP-Adapter 锁脸）
# --------------------------------------------------------------------------- #
KEYFRAMES = WORK / "keyframes"


def _character_contract(cid: str) -> dict:
    for path in (
        REPO / "contract" / "characters" / f"{cid}.json",
        REPO / "contract" / "v5" / "characters" / f"{cid}.json",
        REPO / "contract" / "v5" / "custom" / "characters" / f"{cid}.json",
    ):
        if path.is_file():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    return data
            except (OSError, ValueError):
                continue
    return {}


def _shot_character_id(tid: str, shot: dict) -> str:
    """用 episode.json 的 shots 映射 shot id → 角色 id（brief 只保留 prompt）。"""
    try:
        episode = t3.load_episode(tid)
    except SystemExit:
        return ""
    for item in episode.get("shots") or []:
        if str(item.get("id")) == str(shot.get("id")):
            return str(item.get("character") or "")
    return ""


def _imagegen_spec_for(cid: str, spec_dir: pathlib.Path) -> pathlib.Path:
    """写 imagegen spec（优先精选 spec，否则用角色合同构造），返回 spec 路径。"""
    contract = _character_contract(cid)
    curated = REPO / "tmp" / "v52_art" / "specs" / f"{cid}.json"
    spec: dict = {}
    if curated.is_file():
        try:
            loaded = json.loads(curated.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                spec = loaded
        except (OSError, ValueError):
            spec = {}
    if not spec:
        spec = {
            "id": cid,
            "name": str(contract.get("name") or contract.get("label") or cid),
            "title": str(contract.get("title") or ""),
            "persona": str(contract.get("persona") or ""),
            "accent_color": str(contract.get("accent_color") or "#F5C518"),
            "style": contract.get("style") or {"primary": "2d_cel", "variants": ["2d_cel"]},
            "appearance": contract.get("appearance") or {},
            "extra_tags": ["anime", "cel-shaded"],
        }
    spec["id"] = cid
    if contract.get("name") or contract.get("label"):
        spec["name"] = str(contract.get("name") or contract.get("label"))
    spec_dir.mkdir(parents=True, exist_ok=True)
    path = spec_dir / f"{cid}.json"
    path.write_text(json.dumps(spec, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def _generate_keyframe(tid: str, shot: dict, visual: str, seed: int) -> pathlib.Path:
    """SDXL anime 关键帧（scene 1280x720，portrait IP-Adapter 0.75）→ 1024×576。"""
    shot_id = str(shot.get("id"))
    cid = _shot_character_id(tid, shot)
    if not cid:
        raise RuntimeError(f"{shot_id}: 找不到说话角色")
    portrait = REPO / "assets" / "v5" / "characters" / cid / "portrait.png"
    if not portrait.is_file():
        raise RuntimeError(f"{shot_id}: 缺少 anime 立绘 {portrait}")
    key_dir = KEYFRAMES / tid
    key_dir.mkdir(parents=True, exist_ok=True)
    key_png = key_dir / f"{shot_id}.png"
    if key_png.is_file() and key_png.stat().st_size > 100_000:
        return key_png
    spec_path = _imagegen_spec_for(cid, KEYFRAMES / "specs")
    raw_dir = KEYFRAMES / tid / f"raw_{shot_id}"
    prompt_extra = (
        f"{visual or 'anime key visual scene'}; anime key visual, cel-shaded, clean lineart, "
        "detailed eyes, official anime art, no text; detailed environment, background scenery, "
        "wide shot, cinematic composition"
    )
    cmd = [
        sys.executable, "-m", "imagegen.cli", "generate",
        "--spec", str(spec_path), "--outdir", str(raw_dir),
        "--mode", "scene", "--style", "anime", "--size", "1280x720",
        "--ref", str(portrait), "--ip-adapter-strength", "0.75",
        "--prompt-extra", prompt_extra,
        "--negative-extra",
        "abstract background, solid color background, plain background, close-up portrait, flames",
        "--seed", str(seed), "--timeout", "600",
    ]
    proc = subprocess.run(cmd, cwd=str(REPO), capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=660)
    raw_png = raw_dir / "scene.png"
    if proc.returncode != 0 or not raw_png.is_file():
        tail = "\n".join(((proc.stdout or "") + "\n" + (proc.stderr or "")).strip().splitlines()[-8:])
        raise RuntimeError(f"{shot_id}: imagegen 关键帧失败：{tail[-700:]}")
    subprocess.run([
        str(FFMPEG), "-y", "-hide_banner", "-loglevel", "error",
        "-i", str(raw_png),
        "-vf", "scale=1024:576:force_original_aspect_ratio=increase,crop=1024:576",
        str(key_png),
    ], check=True, capture_output=True, timeout=120)
    log(f"[2D] 关键帧 {shot_id} ← {cid} ({raw_png.stat().st_size} B)")
    return key_png


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


def gen_t2va(tid: str, shot: dict, seed: int, style_mode: str = "3d",
             broll: bool = False) -> pathlib.Path:
    suffix = STYLE_2D_CACHE if style_mode == "2d" else ""
    out = WORK / "clips" / f"{tid}_{shot['id']}_t2va1024{suffix}.mp4"
    if out.is_file() and out.stat().st_size > 200_000:
        return out
    duration = float(shot.get("duration") or 5.0)
    length = frame_length(duration)
    prompt = apply_2d_style(str(shot["prompt"]), broll=broll) \
        if style_mode == "2d" else str(shot["prompt"])
    wf = local.build_workflow(prompt, 1024, 576, length, seed,
                              f"v16/{tid}/{shot['id']}_t2va{suffix}")
    pid = local.submit(wf)
    res = local.wait_and_fetch(pid, out, timeout=1800)
    if not out.is_file():
        raise RuntimeError(f"T2VA 失败: {res}")
    return out


def motion_prompt(shot: dict, style_mode: str = "3d") -> str:
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
    if style_mode == "2d":
        return (
            "subject_definitions: Subject 1 is the character shown in the reference image, preserving their exact face, "
            "hairstyle, hair color, outfit, age and 2D anime art style; cel-shaded, clean line art, flat colors.\n\n"
            f"summary: {STYLE_2D_PREFIX}. {visual or 'A cinematic 2D anime scene.'} The shot is a medium or medium-wide "
            f"2D anime composition that keeps the surrounding set visible; {move}. The character speaks naturally with "
            "lips moving in sync with the reference speech audio, blinking and gesturing, while the camera and body have "
            "gentle hand-drawn motion. Keep the character identity consistent with the reference image. "
            f"{STYLE_2D_QUALITY}. no text, no subtitles, no watermark.\n\n"
            "overall_soundscape: the same spoken sentence, close-mic voice, quiet room tone.\n\n"
            "non_diegetic_music: N/A"
        )
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


def gen_ref2va(tid: str, shot: dict, t2va: pathlib.Path, seed: int,
               style_mode: str = "3d", keyframe: pathlib.Path | None = None) -> pathlib.Path:
    suffix = STYLE_2D_CACHE if style_mode == "2d" else ""
    out = WORK / "clips" / f"{tid}_{shot['id']}_ref2va1024{suffix}.mp4"
    if keyframe is not None:
        # 关键帧模式：旧 ref2va 缓存（V42 T2VA 首帧）不能复用，强制重生成
        try:
            if out.is_file():
                out.unlink()
        except OSError:
            pass
    elif out.is_file() and out.stat().st_size > 200_000:
        return out
    if keyframe is not None and keyframe.is_file():
        first = keyframe
    else:
        first = WORK / "clips" / f"{tid}_{shot['id']}_first{suffix}.png"
        subprocess.run([str(FFMPEG), "-y", "-hide_banner", "-loglevel", "error",
                        "-i", str(t2va), "-frames:v", "1", str(first)], check=False)
    audio = t3.V5_OUT / tid / "work" / "audio" / f"{shot['id']}.wav"
    prompt_file = WORK / "clips" / f"{tid}_{shot['id']}_prompt{suffix}.txt"
    prompt_file.write_text(motion_prompt(shot, style_mode), encoding="utf-8")
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


def _ensure_card_shots(tid: str) -> None:
    """V42：custom 模板首次生成时没有旧 V5 卡片 clip；按 episode 补生成片头/片尾卡。"""
    from pipeline import render_v5, v5_catalog

    episode = t3.load_episode(tid)
    template = t3.load_template(tid)
    catalog = v5_catalog.load_catalog()
    style = (catalog.get("styles") or {}).get(str(template.get("style") or ""))
    if not isinstance(style, dict):
        raise RuntimeError(f"卡片生成缺少 style: {template.get('style')}")
    out_dir = t3.OUT3D / tid
    workdir = out_dir / "work"
    shots_dir = out_dir / "shots"
    shots_dir.mkdir(parents=True, exist_ok=True)
    workdir.mkdir(parents=True, exist_ok=True)
    paths = {
        "out": out_dir / "final.mp4", "out_dir": out_dir,
        "workdir": workdir, "shots_dir": shots_dir,
        "frames_dir": out_dir / "frames", "assets_dir": workdir / "assets",
    }
    for shot in episode.get("shots") or []:
        if str(shot.get("type")) != "card":
            continue
        dest = shots_dir / f"{shot['id']}.mp4"
        if dest.is_file() and dest.stat().st_size > 100_000:
            continue
        old = t3.V5_OUT / tid / "shots" / f"{shot['id']}.mp4"
        if old.is_file() and old.stat().st_size > 100_000:
            shutil.copy2(old, dest)
            continue
        try:
            render_v5._render_card_clip(shot, style, paths, dest)
            log(f"补生成卡片 {shot['id']}")
        except Exception as exc:  # noqa: BLE001 - 卡片失败不阻塞正片
            log(f"卡片 {shot['id']} 生成失败（跳过）：{exc}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--template", required=True)
    ap.add_argument("--style-mode", choices=("3d", "2d"), default="3d",
                    help="V42：2d 使用 AI 动漫(cel-shaded/线稿)提示词，3d 保持原 hq")
    ap.add_argument("--skip-t2va", action="store_true")
    ap.add_argument("--skip-ref2va", action="store_true")
    ap.add_argument("--reuse-shots", action="store_true",
                    help="已有 shots/<sid>.mp4 时跳过生成，直接复用（用于快速重剪辑/UI「复用已有镜头」）")
    args = ap.parse_args(argv)
    tid = args.template
    style_mode = str(args.style_mode or "3d")
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
    log(f"=== {tid}: {len(shots)} 镜，1024×576 T2VA + ref2va（style_mode={style_mode}）===")
    for i, shot in enumerate(shots, 1):
        existing = out_shots / f"{shot['id']}.mp4"
        if args.reuse_shots and existing.is_file() and existing.stat().st_size > 100_000:
            log(f"[{i}/{len(shots)}] {shot['id']} ok (reuse)")
            continue
        seed = 20261102 + abs(hash(tid + shot["id"])) % 100000
        has_line = bool(str(shot.get("line") or "").strip())
        final_src = None
        if style_mode == "2d" and has_line and not args.skip_ref2va:
            visual = str(shot.get("semantic_visual") or "").strip()
            try:
                keyframe = _generate_keyframe(tid, shot, visual, seed)
                final_src = gen_ref2va(tid, shot, keyframe, seed, style_mode,
                                       keyframe=keyframe)
                log(f"[{i}/{len(shots)}] {shot['id']} ok (keyframe+ref2va)")
            except Exception as exc:  # noqa: BLE001 - 关键帧失败回退 T2VA
                log(f"[{i}/{len(shots)}] {shot['id']} 关键帧失败，回退 T2VA：{exc}")
        if final_src is None:
            t2va = gen_t2va(tid, shot, seed, style_mode,
                            broll=(style_mode == "2d" and not has_line))
            final_src = t2va
            if has_line and not args.skip_ref2va:
                final_src = gen_ref2va(tid, shot, t2va, seed, style_mode)
            log(f"[{i}/{len(shots)}] {shot['id']} ok "
                f"({'ref2va' if final_src != t2va else 't2va'})")
        shutil.copy2(final_src, out_shots / f"{shot['id']}.mp4")
    log("=== 剪辑 ===")
    _ensure_card_shots(tid)
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
