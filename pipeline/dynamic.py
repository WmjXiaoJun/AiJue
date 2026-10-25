"""动态镜头（H3 音驱动口型 / 首尾帧）生成与缓存。

    pipeline 动态模式：每镜先用静态合成 clip 的首帧作为首帧图；
    有台词的镜头 → H3 ref2va 音驱动（首帧 + 该镜 TTS wav）；
    无台词镜头 → H3 i2v/fl2v（首帧 + 下一镜首帧作为尾帧，match cut 连续性）；
    输出 clips/dynamic_{sid}.mp4（静音 H3 原声，成片使用 TTS 音轨）。

H3 CLI 约定（h3-comfy 提供，可用环境变量 H3_DYNAMIC_CLI 覆盖）：

    python integrations/comfyui/run_h3_ref.py \
        --mode audio_ref|fl2v --image first.png [--last-frame last.png] \
        [--audio shot.wav] --out out.mp4 --duration 5.0 \
        --width 768 --height 768 --fps 24 --seed 42

cache：workdir/dynamic/{sid}.mp4 + {sid}.meta.json（hash 含 shot/首尾帧/音频/参数）。
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from . import config, shot_grammar, style as style_mod, utils

LOG = logging.getLogger("pipeline")

DYNAMIC_VERSION = "dyn-v2"
CARD_TYPES = ("title_card", "end_card", "meme_card")
# H3 原生生成尺寸：audio_ref 用 16:9（1024x576，像素≈768²，比 1280x720 快）；
# fl2v 工作流要求 768²（CLI 内部会强制）。
H3_GEN_W = max(256, (int(os.environ.get("H3_DYNAMIC_W", "1024")) // 32) * 32)
H3_GEN_H = max(256, (int(os.environ.get("H3_DYNAMIC_H", "576")) // 32) * 32)
DEFAULT_CLI = config.REPO_ROOT / "integrations" / "comfyui" / "run_h3_ref.py"


def _valid(path, min_size: int = 512) -> bool:
    try:
        p = Path(path)
        return p.is_file() and p.stat().st_size >= min_size
    except (TypeError, OSError):
        return False


def _sid(shot: dict) -> str:
    return str(shot.get("id") or "shot")


def _dur(shot: dict) -> float:
    try:
        return max(0.5, float(shot.get("duration") or 0.0))
    except (TypeError, ValueError):
        return 5.0


def resolve_cli(cli: str | Path | None = None) -> Path | None:
    """定位 H3 ref CLI：显式参数 > H3_DYNAMIC_CLI > 默认路径。

    显式传入的路径不存在时返回 None（视为“不可用”），不静默回退默认 CLI；
    这样 --dynamic-fallback-static / 单测才能可靠地走回退路径。
    """
    if cli:
        p = Path(cli)
        return p.resolve() if p.is_file() else None
    for candidate in (os.environ.get("H3_DYNAMIC_CLI"), DEFAULT_CLI):
        if not candidate:
            continue
        p = Path(candidate)
        if p.is_file():
            return p.resolve()
    return None


def _extract_frame(video: Path, out: Path, last: bool = False) -> bool:
    utils.ensure_dir(out.parent)
    args = ["-y", "-hide_banner", "-loglevel", "error"]
    if last:
        # 取最后一帧：先探测时长再 -ss（取倒数第二帧更稳）
        dur = utils.probe_duration(video)
        args += ["-ss", f"{max(0.0, dur - 0.10):.3f}"]
    args += ["-i", str(video), "-frames:v", "1", str(out)]
    try:
        utils.ffmpeg(args)
        return _valid(out, 1000)
    except Exception as exc:  # noqa: BLE001
        LOG.warning("抽帧失败 %s: %s", video, exc)
        return False


def _compose_static_clips(shots: list, assets: list, episode: dict,
                         workdir: Path, width: int, height: int, fps: int,
                         char_map: dict | None = None) -> dict[str, Path]:
    """用当前角色素材/合成函数重新生成「参考构图」静态 clip（不复用平台 shots）。

    平台生成台的 shots 可能是旧素材/旧风格（例如 chibi 版），H3 动态需以
    当前概念角色构图为参考，才能保证 3 个角色身份正确。
    """
    from . import fonts, mock_shot
    # 先串行准备好字体，避免 6 个合成线程并发复制字体文件竞态
    try:
        fonts.ensure_fonts(workdir)
    except Exception as exc:  # noqa: BLE001
        LOG.warning("参考构图字体准备失败: %s", exc)
    comp_dir = utils.ensure_dir(workdir / "dynamic" / "_static")
    out: dict[str, Path] = {}

    def _one(shot: dict):
        sid = _sid(shot)
        dst = comp_dir / f"{sid}.mp4"
        if _valid(dst, 2000):
            return sid, dst
        spec = {
            "shot": shot,
            "assets": assets,
            "episode": {
                "aspect": episode.get("aspect") or "16:9",
                "resolution": f"{width}x{height}",
                "fps": fps,
                "no": episode.get("no"),
                "title": episode.get("title"),
            },
            "mode": "mock",
            "params": {},
        }
        try:
            mock_shot.generate_mock_clip(spec, dst, workdir,
                                         resolution=f"{width}x{height}",
                                         fps=fps, char_map=char_map)
        except Exception as exc:  # noqa: BLE001
            LOG.warning("参考构图生成失败 %s: %s", sid, exc)
        return sid, dst

    with ThreadPoolExecutor(max_workers=6) as pool:
        for sid, dst in pool.map(_one, shots):
            if _valid(dst, 2000):
                out[sid] = dst
    return out


def _first_frames(shots: list, comp_clips: dict, workdir: Path) -> dict[str, Path]:
    """从参考构图 clip 抽每镜首帧（供 H3 首帧/尾帧输入）。"""
    frames: dict[str, Path] = {}
    frames_dir = utils.ensure_dir(workdir / "dynamic" / "frames")
    for shot in shots:
        sid = _sid(shot)
        clip = comp_clips.get(sid) or (workdir / "clips" / f"{sid}.mp4")
        out = frames_dir / f"{sid}_first.png"
        if not _valid(clip, 2000):
            continue
        # 每轮都从参考构图重新抽帧，避免复用旧的平台/chibi 首帧
        if _extract_frame(clip, out):
            frames[sid] = out
    return frames


def _char_refs(shots: list, frames: dict, workdir: Path, width: int,
               height: int, plans: dict | None = None) -> tuple[dict, dict]:
    """按 ShotPlan 景别裁 H3 参考图（768²），返回 ({sid: ref}, {sid: 策略})。

    - close-up / reaction：从角色画窗再裁脸部特写（更近的构图）；
    - medium：角色画窗（现有构图）；
    - wide/full：H3 ref2va 不支持拉远，记录限制并回退中景画窗；
    - b_roll：画面中心方形。
    """
    refs: dict[str, Path] = {}
    strategies: dict[str, str] = {}
    plans = plans or {}
    ref_dir = utils.ensure_dir(workdir / "dynamic" / "refs")
    # 场景模式：直接使用「2D 绘景 + 角色」整帧作为参考，保留环境
    if os.environ.get("SCENE_DYNAMIC_FULL_REF") == "1":
        for shot in shots:
            sid = _sid(shot)
            full = frames.get(sid)
            if full is None:
                continue
            refs[sid] = full
            strategies[sid] = "scene-full"
        return refs, strategies
    for shot in shots:
        sid = _sid(shot)
        full = frames.get(sid)
        if full is None:
            continue
        out = ref_dir / f"{sid}_ref.png"
        typ = str(shot.get("type") or "")
        plan = plans.get(sid) or {}
        size = str(plan.get("size") or "")
        purpose = str(plan.get("purpose") or "")
        fg = max(256, int(height * 0.91))
        win_x = max(0, width - fg - 50)
        win_y = max(0, height - fg)
        if typ == "b_roll":
            size_px = max(256, min(width, height))
            x = max(0, (width - size_px) // 2)
            y = max(0, (height - size_px) // 2)
            vf = f"crop={size_px}:{size_px}:{x}:{y},scale=768:768"
            strategies[sid] = "center-square"
        elif ("Close" in size) or (purpose in ("emotion", "reaction")
                                  and size not in ("Medium", "Medium Close-up",
                                                   "Full", "Wide")):
            # 景别以 ShotPlan 为准；reaction 的变体若排到 Medium 则用中景画窗，
            # 避免 ref 特写把画面拉得比 plan 更近（QC 复核 plan/画面一致）
            face = max(256, int(fg * 0.78))
            x = win_x + max(0, (fg - face) // 2)
            y = win_y + max(0, int(fg * 0.02))
            vf = f"crop={face}:{face}:{x}:{y},scale=768:768"
            strategies[sid] = "face-closeup"
        elif size in ("Wide", "Wide / Establishing", "Full"):
            vf = f"crop={fg}:{fg}:{win_x}:{win_y},scale=768:768"
            strategies[sid] = "wide-limitation(window-fallback)"
        else:
            vf = f"crop={fg}:{fg}:{win_x}:{win_y},scale=768:768"
            strategies[sid] = "window-medium"
        try:
            utils.ffmpeg(["-y", "-hide_banner", "-loglevel", "error",
                          "-i", str(full), "-vf", vf, str(out)])
            if _valid(out, 1000):
                refs[sid] = out
            else:
                refs[sid] = full
        except Exception as exc:  # noqa: BLE001
            LOG.warning("角色参考图裁剪失败 %s（用整帧）: %s", sid, exc)
            refs[sid] = full
        strategies.setdefault(sid, "full-frame")
    return refs, strategies


def _call_h3(cli: Path, mode: str, first: Path, last: Path | None,
             audio: Path | None, out: Path, duration: float,
             width: int, height: int, fps: int, seed: int = 42,
             prompt_file: Path | None = None, timeout: int = 1800) -> None:
    if cli.suffix.lower() == ".py":
        cmd = [sys.executable, str(cli)]
    else:
        cmd = [str(cli)]
    cmd += ["--mode", mode, "--image", str(first), "--out", str(out)]
    if last is not None:
        cmd += ["--last-frame", str(last)]
    if audio is not None:
        cmd += ["--audio", str(audio)]
    if prompt_file is not None:
        cmd += ["--prompt-file", str(prompt_file)]
    # H3 原生生成尺寸（audio_ref 16:9；fl2v CLI 会强制 768²）
    cmd += ["--duration", f"{duration:.3f}", "--width", str(H3_GEN_W),
            "--height", str(H3_GEN_H), "--fps", str(fps), "--seed", str(seed)]
    LOG.info("H3 动态生成: %s mode=%s %.2fs", _sid({"id": out.stem}), mode, duration)
    proc = subprocess.run(cmd, cwd=str(config.REPO_ROOT), capture_output=True,
                          text=True, encoding="utf-8", errors="replace",
                          timeout=timeout)
    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout or "")[-1500:]
        raise RuntimeError(f"H3 动态生成失败 (exit {proc.returncode}): {tail}")


def _normalize_dynamic(src: Path, dst: Path, duration: float, width: int,
                       height: int, fps: int) -> None:
    """统一规格并保证时长：H3 输出可能是 768²/24fps/5.17s。"""
    tmp = dst.with_suffix(".tmp.mp4")
    utils.ensure_dir(dst.parent)
    utils.ffmpeg([
        "-y", "-hide_banner", "-loglevel", "error", "-i", str(src),
        "-vf", (f"fps={fps},scale={width}:{height}:force_original_aspect_ratio=increase,"
                f"crop={width}:{height},setsar=1,"
                f"tpad=stop_mode=clone:stop_duration=8,trim=0:{duration:.3f},"
                f"setpts=PTS-STARTPTS"),
        "-an", "-t", f"{duration:.3f}",
        "-c:v", "libx264", "-preset", config.X264_PRESET,
        "-crf", config.X264_CRF, "-pix_fmt", "yuv420p", "-r", str(fps),
        str(tmp),
    ])
    tmp.replace(dst)


def _digest(shot: dict, mode: str, first: Path, last: Path | None,
            audio: Path | None, cli: Path, width: int, height: int,
            fps: int, seed: int) -> str:
    parts = [DYNAMIC_VERSION, json.dumps(shot, sort_keys=True, ensure_ascii=False),
             mode, str(cli), str(cli.stat().st_mtime) if cli.exists() else "",
             str(width), str(height), f"{H3_GEN_W}x{H3_GEN_H}", str(fps), str(seed)]
    # 参考图/首尾帧用内容尺寸即可（每次渲染会重抽/重裁，mtime 不稳定）；
    # 音频/台词变化用 size+mtime 判断。
    for p in (first, last):
        if p is None:
            parts.append("-")
            continue
        try:
            parts.append(f"{Path(p).name}:{Path(p).stat().st_size}")
        except OSError:
            parts.append(f"{p}:missing")
    if audio is None:
        parts.append("-")
    else:
        try:
            st = Path(audio).stat()
            parts.append(f"{Path(audio).name}:{st.st_size}:{st.st_mtime_ns}")
        except OSError:
            parts.append(f"{audio}:missing")
    return utils.sha1_text(*parts)


def ensure_dynamic_clips(episode: dict, shots: list, assets: list, workdir: Path,
                         width: int, height: int, fps: int,
                         char_map: dict | None = None,
                         tts_map: dict | None = None,
                         force: bool = False, cli: str | Path | None = None,
                         fallback_static: bool = False,
                         progress=None, seed: int = 42,
                         fill_missing_static: bool = True,
                         style_name: str | None = None,
                         template_id: str | None = None,
                         ref_clips: dict | None = None,
                         scene_desc: str | None = None) -> dict:
    """为每个镜头准备动态 clip；返回 {sid: {path, mode, first_frame, last_frame, reused}}。

    style_name/template_id: 风格系统（3d_anime 默认）；写 H3 prompt style prefix。
    ref_clips: 场景合成整帧（{sid: mp4}）；提供时参考图用整帧（保留玄关/家具/光线），
    不再裁角色窗口，并把场景描述注入 prompt。
    scene_desc: H3 prompt 里的场景英文描述。
    """
    workdir = Path(workdir)
    tts_map = tts_map or {}
    cli_path = resolve_cli(cli)
    if cli_path is None and not fallback_static:
        raise RuntimeError(
            "H3 动态 CLI 未就绪：等待 h3-comfy 提供 "
            "integrations/comfyui/run_h3_ref.py，或用 H3_DYNAMIC_CLI 指定；"
            "也可加 --dynamic-fallback-static 先走静态回退")

    ep = episode.get("episode") if isinstance(episode.get("episode"), dict) else episode
    template_id = template_id or episode.get("format_id") or ep.get("id")
    style_name, style_dict = style_mod.resolve(template_id or "", override=style_name)
    style_prefix = str((style_dict or {}).get("prompt_prefix") or "")
    scene_text = scene_desc or ""
    if char_map is None:
        try:
            from . import characters
            char_map = characters.resolve_for_assets(assets)
        except Exception as exc:  # noqa: BLE001
            LOG.warning("角色映射失败（参考构图回退纯文字卡）: %s", exc)
    ref_strategies: dict[str, str] = {}
    if ref_clips:
        # 场景模式：参考图 = 场景合成整帧（角色已在场景里），不做角色窗口裁剪
        frames: dict[str, Path] = {}
        frames_dir = utils.ensure_dir(workdir / "dynamic" / "frames")
        for shot in shots:
            sid = _sid(shot)
            clip = Path(ref_clips.get(sid) or "")
            if not clip.is_file():
                continue
            out = frames_dir / f"{sid}_scene.png"
            if _extract_frame(clip, out):
                frames[sid] = out
                ref_strategies[sid] = "scene-full-frame"
        comp_clips = {sid: Path(p) for sid, p in (ref_clips or {}).items()}
        refs = dict(frames)
        plans = {_sid(shot): shot_grammar.plan_for_shot(shot, i)
                 for i, shot in enumerate(shots)}
    else:
        # 无场景合成时：按当前概念角色重新合成参考构图 + 角色窗口裁剪
        comp_clips = _compose_static_clips(shots, assets, ep, workdir, width, height,
                                           fps, char_map=char_map)
        frames = _first_frames(shots, comp_clips, workdir)
        plans = {_sid(shot): shot_grammar.plan_for_shot(shot, i)
                 for i, shot in enumerate(shots)}
        refs, ref_strategies = _char_refs(shots, frames, workdir, width, height,
                                          plans=plans)
    prompts_dir = utils.ensure_dir(workdir / "dynamic" / "prompts")
    ids = [_sid(s) for s in shots]
    dynamic_dir = utils.ensure_dir(workdir / "dynamic")
    meta_dir = utils.ensure_dir(dynamic_dir / "meta")
    results: dict[str, dict] = {}
    total = max(1, len(shots))

    for i, shot in enumerate(shots):
        sid = _sid(shot)
        typ = str(shot.get("type") or "")
        static_clip = comp_clips.get(sid) or (workdir / "clips" / f"{sid}.mp4")
        if typ in CARD_TYPES:
            results[sid] = {"id": sid, "path": str(static_clip), "mode": "static-card",
                            "first_frame": "", "last_frame": "", "reused": True,
                            "plan": plans.get(sid)}
            continue
        first = frames.get(sid)
        if first is None:
            if fallback_static:
                results[sid] = {"id": sid, "path": str(static_clip),
                                "mode": "static-fallback", "first_frame": "",
                                "last_frame": "", "reused": True}
                continue
            raise RuntimeError(f"缺少静态首帧（先渲染静态版）: {sid}")
        # H3 参考图：角色特写裁剪（避免整帧背景干扰身份）；尾帧=下一镜参考图（match cut）
        first_ref = refs.get(sid) or first
        next_ref = None
        for nxt in shots[i + 1:]:
            nxt_sid = _sid(nxt)
            if nxt_sid in refs:
                next_ref = refs[nxt_sid]
                break
        last = next_ref or first_ref
        line = str(shot.get("line") or "").strip()
        audio = Path(tts_map[sid]) if tts_map.get(sid) else \
            workdir / "audio" / f"shot_{sid}.wav"
        audio = audio if _valid(audio, 500) else None
        mode = "audio_ref" if (line and audio is not None) else "fl2v"
        plan = plans.get(sid) or shot_grammar.plan_for_shot(shot, i)
        prompt = shot_grammar.build_h3_prompt(
            plan, mode,
            scene_description=scene_text or os.environ.get("SCENE_DESC") or None,
            style_prefix=style_prefix or None)
        prompt_path = prompts_dir / f"{sid}.txt"
        utils.write_text(prompt_path, prompt + "\n")
        out = dynamic_dir / f"{sid}.mp4"
        meta_path = meta_dir / f"{sid}.json"
        if cli_path is None:  # fallback_static
            results[sid] = {"id": sid, "path": str(static_clip),
                            "mode": "static-fallback", "first_frame": str(first),
                            "last_frame": str(last), "reused": True}
            continue
        digest = _digest(shot, mode, first, last, audio, cli_path,
                         width, height, fps, seed)
        digest = utils.sha1_text(digest, prompt, "shot-plan-v1")
        meta = utils.read_json(meta_path) or {}
        if not force and _valid(out, 5000) and meta.get("hash") == digest:
            results[sid] = {"id": sid, "path": str(out), "mode": meta.get("mode", mode),
                            "first_frame": str(first), "last_frame": str(last),
                            "ref_image": str(first_ref), "plan": meta.get("plan") or plan,
                            "reused": True}
        else:
            tmp = dynamic_dir / f"{sid}.raw.mp4"
            try:
                _call_h3(cli_path, mode, first_ref, last, audio, tmp,
                         _dur(shot), width, height, fps, seed=seed,
                         prompt_file=prompt_path)
                _normalize_dynamic(tmp, out, _dur(shot), width, height, fps)
                utils.write_json(meta_path, {
                    "hash": digest, "mode": mode, "first_frame": str(first),
                    "ref_image": str(first_ref), "last_frame": str(last),
                    "ref_strategy": ref_strategies.get(sid, "full-frame"),
                    "style": style_name, "style_prefix": style_prefix,
                    "scene_desc": scene_text,
                    "plan": plan, "plan_source": plan.get("source"),
                    "prompt_file": str(prompt_path),
                    "prompt": prompt,
                    "audio": str(audio) if audio else "",
                    "duration": _dur(shot), "width": width, "height": height,
                    "h3_gen": [H3_GEN_W, H3_GEN_H],
                    "fps": fps, "seed": seed, "cli": str(cli_path),
                    "version": DYNAMIC_VERSION,
                    "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                })
                results[sid] = {"id": sid, "path": str(out), "mode": mode,
                                "first_frame": str(first), "last_frame": str(last),
                                "plan": plan, "prompt_file": str(prompt_path),
                                "reused": False}
            except Exception as exc:  # noqa: BLE001
                LOG.warning("动态镜头失败 %s（%s）", sid, exc)
                if fallback_static:
                    results[sid] = {"id": sid, "path": str(static_clip),
                                    "mode": "static-fallback", "first_frame": str(first),
                                    "last_frame": str(last), "reused": True,
                                    "error": str(exc)[:300]}
                else:
                    raise
            finally:
                try:
                    tmp.unlink()
                except OSError:
                    pass
        if progress is not None:
            progress(66 + 2.0 * (i + 1) / total, f"动态镜头 {i + 1}/{total}")
    return results


def dynamic_summary(results: dict) -> dict:
    modes: dict[str, int] = {}
    reused = 0
    for info in (results or {}).values():
        modes[str(info.get("mode"))] = modes.get(str(info.get("mode")), 0) + 1
        if info.get("reused"):
            reused += 1
    dynamic = sum(count for mode, count in modes.items()
                  if mode in ("audio_ref", "fl2v"))
    return {"shots": len(results or {}), "modes": modes, "reused": reused,
            "dynamic": dynamic}
