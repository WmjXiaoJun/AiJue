# -*- coding: utf-8 -*-
"""render_v3：模板 → storyboard → stage 首帧 → H3 每镜 → 剪辑/包装/混音 → QC。

docs/V3_INTERFACES.md §6 主流程：
  1) load+validate 模板（comfy 模式失败即退出，不渲染；previs 只警告）
  2) storyboard_v3.build_plan → work/episode.json
  3) TTS：复用 pipeline.render.prepare_tts / build_shot_audio /
     export_shot_tts_audio / ensure_sfx
  4) 每镜（H3 GPU 串行）：非卡片 → stage.compose_first_frame（fl2v 再 compose_last_frame）
     → h3_comfy.generate（audio_ref 带该镜 TTS wav）→ h3_comfy.normalize_clip；
     卡片 → stage.compose_card_frame + ffmpeg 静帧 clip
  5) 剪辑：render.concat_video(_xfade) / build_transition_sfx / ass_builder.build_ass /
     concat_audio / ensure_bgm / style_v2.grade_filter+packaging_filters / final_encode
  6) QC：qc_v3.evaluate → qc_report.json
  7) 产物：outputs/templates_v3/{id}/{final.mp4,shots/,frames/,h3_meta.json,
     qc_report.json,work/}；摘要 JSON 落 stdout 最后一行 + work/manifest.json

模式：
  --h3 comfy   每镜真跑 ComfyUI H3（GPU 串行；每镜 meta 必须 workflow/prompt_id/seed/ref_image）
  --h3 previs  只用 stage 首帧做静态 clip（QC 标 previs、deliverable=false，不算交付）
  --fallback-static  H3 失败镜回退静态并写 fallback 标记（QC 报告列出，验收不接受）
"""
from __future__ import annotations

import importlib
import json
import logging
import math
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

from . import (ass_builder, config, fonts, qc_v3, storyboard_v3, style_v2,
               template_v3, utils)

LOG = logging.getLogger("pipeline")

REPO_ROOT = Path(__file__).resolve().parents[1]
OUT_ROOT = REPO_ROOT / "outputs" / "templates_v3"
DEFAULT_H3_MODE = "comfy"
DEFAULT_SEED = storyboard_v3.DEFAULT_SEED
H3_NATIVE_FPS = 24
FINAL_W, FINAL_H, FINAL_FPS = 1280, 720, 25
AUDIO_WORKERS = 8
FRAME_SAMPLES = 8
CONTACT_COLS = 4
TRANSITION_DURATION = 0.30


class RenderV3Error(RuntimeError):
    """v3 渲染不可继续（模板/依赖/产物错误）。"""


# ---------------------------------------------------------------------------
# 依赖模块（stage / action_library / h3_comfy 由并行任务实现，按文档签名调用）
# ---------------------------------------------------------------------------
def _try_module(name: str):
    try:
        return importlib.import_module(f".{name}", __package__)
    except ImportError as exc:  # pragma: no cover - 依赖未就绪时
        LOG.debug("模块 pipeline.%s 未就绪: %s", name, exc)
        return None


def _require_module(name: str):
    mod = _try_module(name)
    if mod is None:
        raise RenderV3Error(f"pipeline/{name}.py 未就绪（v3 渲染依赖该模块）")
    return mod


# ---------------------------------------------------------------------------
# 参数 / 路径
# ---------------------------------------------------------------------------
def _arg(args, name: str, default=None):
    value = getattr(args, name, None)
    return default if value is None else value


def render_mode(args) -> str:
    mode = str(_arg(args, "h3", DEFAULT_H3_MODE)).strip().lower()
    return "previs" if mode in ("previs", "static", "none") else "comfy"


def resolve_paths(args, template_id: str) -> dict:
    """返回 {out, out_dir, workdir, shots_dir, frames_dir, h3_dir}。"""
    out_arg = _arg(args, "out")
    if out_arg:
        out = Path(out_arg)
        if out.suffix.lower() != ".mp4":
            out = out / "final.mp4"
        out_dir = out.parent
    else:
        out_dir = OUT_ROOT / template_id
        out = out_dir / "final.mp4"
    out = out.resolve()
    out_dir = out.parent
    work_arg = _arg(args, "workdir")
    workdir = (Path(work_arg).resolve() if work_arg else out_dir / "work")
    return {
        "out": out,
        "out_dir": out_dir,
        "workdir": workdir,
        "shots_dir": out_dir / "shots",
        "frames_dir": out_dir / "frames",
        "h3_dir": workdir / "h3",
    }


def _ensure_dirs(paths: dict) -> None:
    for key in ("out_dir", "workdir", "shots_dir", "frames_dir", "h3_dir"):
        utils.ensure_dir(paths[key])


# ---------------------------------------------------------------------------
# 并发保护：同一输出目录同时只允许一个 render_v3（GPU/ffmpeg 会互相覆盖成片）
# ---------------------------------------------------------------------------
LOCK_NAME = ".render_v3.lock"
LOCK_TTL_SEC = 2 * 3600
STILL_ACTIVE = 259


def pid_alive(pid) -> bool:
    """进程是否存活（Windows 用 OpenProcess，其他平台 kill(0)）。"""
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return False
    if pid <= 0:
        return False
    try:
        if os.name == "nt":
            import ctypes
            kernel32 = ctypes.windll.kernel32
            handle = kernel32.OpenProcess(0x1000, False, pid)  # QUERY_LIMITED_INFORMATION
            if not handle:
                return False
            try:
                code = ctypes.c_ulong()
                if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
                    return False
                return code.value == STILL_ACTIVE
            finally:
                kernel32.CloseHandle(handle)
        os.kill(pid, 0)
        return True
    except Exception:  # noqa: BLE001
        return False


def lock_target(args) -> Path:
    """在解析模板/路径前，按 --workdir / --out / 默认约定定位锁目录。"""
    work_arg = _arg(args, "workdir")
    if work_arg:
        return Path(work_arg).resolve()
    tid = str(_arg(args, "template", "") or "").strip() or "_unknown"
    out_arg = _arg(args, "out")
    if out_arg:
        out = Path(out_arg)
        out_dir = out.parent if out.suffix.lower() == ".mp4" else out
        return (out_dir / "work").resolve()
    return (OUT_ROOT / tid / "work").resolve()


def _acquire_lock(workdir: Path, template_id: str = "") -> Path:
    workdir = utils.ensure_dir(workdir)
    lock = workdir / LOCK_NAME
    now = time.time()
    for _ in range(3):
        try:
            fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            info = utils.read_json(lock) or {}
            try:
                age = now - float(info.get("time") or 0)
            except (TypeError, ValueError):
                age = LOCK_TTL_SEC + 1
            owner_pid = info.get("pid")
            stale = age > LOCK_TTL_SEC
            if not stale and owner_pid is not None and not pid_alive(owner_pid):
                LOG.warning("清理已退出进程的渲染锁（pid=%s）: %s", owner_pid, lock)
                stale = True
            if not stale:
                raise RenderV3Error(
                    f"另一个 render_v3 正在渲染该输出目录（lock={lock}，"
                    f"pid={owner_pid}，已 {age:.0f}s）；请等它结束，"
                    f"或用 --workdir/--out 换独立目录（禁止同目录并发）")
            try:
                lock.unlink()
            except OSError:
                pass
            continue
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump({"pid": os.getpid(), "time": now,
                           "template": template_id}, fh, ensure_ascii=False)
        except OSError:
            pass
        return lock
    raise RenderV3Error(f"无法获取渲染锁: {lock}")


def _release_lock(lock: Path | None) -> None:
    if lock is None:
        return
    try:
        if lock.is_file():
            lock.unlink()
    except OSError:
        pass


def _parse_resolution(text, default=(1024, 576)) -> tuple[int, int]:
    try:
        w, h = str(text or "").lower().split("x", 1)
        return int(w), int(h)
    except (ValueError, TypeError):
        return default


# ---------------------------------------------------------------------------
# ffmpeg 小工具
# ---------------------------------------------------------------------------
def _probe(path: Path) -> dict:
    try:
        return utils.probe_video(path)
    except Exception:  # noqa: BLE001
        return {"width": 0, "height": 0, "fps": 0.0, "duration": 0.0,
                "codec": "", "has_audio": False}


def make_static_clip(image: Path | str, out: Path | str, duration: float,
                     width: int, height: int, fps: int = FINAL_FPS) -> dict:
    """静帧 → 精确时长 clip（卡片 / previs / 兜底统一走这里）。"""
    image, out = Path(image), Path(out)
    utils.ensure_dir(out.parent)
    vf = (f"scale={width}:{height}:force_original_aspect_ratio=increase,"
          f"crop={width}:{height},setsar=1")
    utils.ffmpeg([
        "-loop", "1", "-framerate", str(fps), "-i", str(image),
        "-t", f"{max(0.1, float(duration)):.3f}", "-vf", vf,
        "-r", str(fps), "-c:v", "libx264", "-preset", config.X264_PRESET,
        "-crf", config.X264_CRF, "-pix_fmt", "yuv420p", "-an", str(out),
    ])
    return {"path": str(out), "duration": round(float(duration), 3),
            "width": width, "height": height, "fps": fps}


def normalize_clip_fallback(src: Path | str, dst: Path | str, *, duration: float,
                            width: int = FINAL_W, height: int = FINAL_H,
                            fps: int = FINAL_FPS) -> dict:
    """h3_comfy.normalize_clip 未就绪时的等价实现（缩放/补边/trim/tpad）。"""
    src, dst = Path(src), Path(dst)
    utils.ensure_dir(dst.parent)
    vf = (f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
          f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps={fps},"
          f"tpad=stop_mode=clone:stop_duration={max(1.0, duration):.2f}")
    utils.ffmpeg([
        "-i", str(src), "-vf", vf, "-t", f"{duration:.3f}", "-an",
        "-c:v", "libx264", "-preset", config.X264_PRESET,
        "-crf", config.X264_CRF, "-pix_fmt", "yuv420p",
        "-movflags", "+faststart", str(dst),
    ])
    return {"path": str(dst), "duration": utils.probe_duration(dst),
            "width": width, "height": height, "fps": fps, "fallback": True}


def _normalize_clip(h3_mod, src: Path, dst: Path, duration: float,
                    width: int, height: int, fps: int) -> dict:
    fn = getattr(h3_mod, "normalize_clip", None) if h3_mod is not None else None
    if callable(fn):
        return fn(src, dst, duration=duration, width=width, height=height, fps=fps)
    LOG.warning("h3_comfy.normalize_clip 缺失，使用内置归一化")
    return normalize_clip_fallback(src, dst, duration=duration,
                                   width=width, height=height, fps=fps)


# ---------------------------------------------------------------------------
# 首帧 / 卡片帧（stage 优先；previs 缺失时用 plate 兜底并标记）
# ---------------------------------------------------------------------------
def _plate_image(template: dict, profile: dict) -> Path | None:
    found = template_v3.plate_path(template)
    if found is not None:
        return found
    plate = str((template.get("stage") or {}).get("plate") or "")
    path, _level, fallback = style_v2.plate_path(profile or {}, plate)
    return Path(path) if Path(path).is_file() else None


def _cover_image(size, src: Path, width: int, height: int):
    """PIL：等比裁切填满 width×height。"""
    from PIL import Image
    img = Image.open(src).convert("RGB")
    sw, sh = img.size
    scale = max(width / sw, height / sh)
    new = (max(1, int(round(sw * scale))), max(1, int(round(sh * scale))))
    img = img.resize(new, Image.LANCZOS)
    left = (new[0] - width) // 2
    top = (new[1] - height) // 2
    return img.crop((left, top, left + width, top + height))


# blackdetect 的 pix_th/pic_th 口径：98% 像素低于 ~0.10 亮度即判黑帧段
BLACK_PIX_TH = 26
BLACK_PIC_TH = 0.98


def frame_black_fraction(png: Path | str) -> float:
    """近黑像素占比（对齐 ffmpeg blackdetect pic_th=0.98 的口径）。"""
    try:
        from PIL import Image
        with Image.open(png) as img:
            gray = img.convert("L")
            hist = gray.histogram()
            total = sum(hist) or 1
            dark = sum(hist[:BLACK_PIX_TH])
            return round(dark / total, 4)
    except Exception as exc:  # noqa: BLE001
        LOG.debug("读取帧亮度失败 %s: %s", png, exc)
        return 0.0


def is_black_frame(png: Path | str) -> bool:
    return frame_black_fraction(png) >= BLACK_PIC_TH


def fallback_plate_frame(template: dict, shot: dict, out_png: Path, *,
                         width: int, height: int, profile: dict,
                         workdir: Path | None = None, darken: bool = False) -> dict:
    """stage 未就绪时的 previs 兜底帧：场景 plate 等比裁切（可压暗/叠字）。"""
    from PIL import Image, ImageDraw, ImageFont
    utils.ensure_dir(Path(out_png).parent)
    plate = _plate_image(template, profile)
    if plate is None:
        img = Image.new("RGB", (width, height), (18, 18, 24))
    else:
        img = _cover_image(None, plate, width, height)
    if darken:
        overlay = Image.new("RGBA", img.size, (0, 0, 0, 150))
        img = Image.alpha_composite(img.convert("RGBA"), overlay).convert("RGB")
    draw = ImageDraw.Draw(img)
    is_card = bool(shot.get("static_card"))
    if is_card:
        title = str(shot.get("title") or (template.get("label") or ""))
        font = None
        if workdir:
            try:
                info = fonts.ensure_fonts(workdir)
                font_path = Path(workdir) / str((info.get("body") or {}).get("file") or "")
                if font_path.is_file():
                    font = ImageFont.truetype(str(font_path), max(22, int(height * 0.075)))
            except Exception as exc:  # noqa: BLE001
                LOG.debug("兜底卡片字体失败: %s", exc)
        if font is not None:
            bbox = draw.textbbox((0, 0), title, font=font)
            tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
            draw.text(((width - tw) / 2, (height - th) / 2),
                      title, font=font, fill=(245, 197, 24))
    else:
        label = str(shot.get("visual") or (shot.get("v3") or {}).get("intent") or "")
        if label:
            try:
                font = ImageFont.load_default()
                draw.text((24, height - 46), f"[previs] {label}", font=font,
                          fill=(230, 230, 230))
            except Exception:  # noqa: BLE001
                pass
    img.save(out_png)
    return {"path": str(out_png), "width": width, "height": height,
            "plate": str(plate) if plate else "",
            "fallback": True, "stage_fallback": True, "h3_input": True,
            "cell": "", "camera_zone": str((shot.get("v3") or {}).get("camera_zone") or ""),
            "characters": [], "props": []}


def compose_first_frame(stage_mod, template: dict, shot: dict, out_png: Path, *,
                        width: int, height: int, workdir: Path, index: int,
                        profile: dict) -> dict:
    if stage_mod is not None:
        return stage_mod.compose_first_frame(
            template, shot, out_png, width=width, height=height,
            workdir=workdir, index=index)
    return fallback_plate_frame(template, shot, out_png, width=width,
                                height=height, profile=profile, workdir=workdir)


def compose_card_frame(stage_mod, template: dict, shot: dict, out_png: Path, *,
                       width: int, height: int, workdir: Path,
                       profile: dict) -> dict:
    if stage_mod is not None:
        try:
            return stage_mod.compose_card_frame(
                template, shot, out_png, width=width, height=height)
        except Exception as exc:  # noqa: BLE001
            LOG.warning("stage.compose_card_frame 失败，用 plate 兜底卡片: %s", exc)
    return fallback_plate_frame(template, shot, out_png, width=width, height=height,
                                profile=profile, workdir=workdir, darken=True)


def compose_last_frame(stage_mod, template: dict, shot: dict, first_meta: dict,
                       out_png: Path, *, width: int, height: int) -> dict | None:
    if stage_mod is None:
        return None
    fn = getattr(stage_mod, "compose_last_frame", None)
    if not callable(fn):
        return None
    try:
        meta = fn(template, shot, first_meta, out_png, width=width, height=height)
    except Exception as exc:  # noqa: BLE001
        LOG.warning("compose_last_frame 失败（fl2v 用首帧兜底）: %s", exc)
        return None
    if meta and meta.get("path"):
        return meta
    return None


# ---------------------------------------------------------------------------
# TTS / 镜头音轨（复用 render.py）
# ---------------------------------------------------------------------------
def prepare_audio(plan: dict, workdir: Path, prog, force: bool) -> dict:
    from . import render as render_mod
    shots = plan.get("shots") or []
    assets = plan.get("assets") or []
    prog(6, f"旁白合成：{sum(1 for s in shots if str(s.get('line') or '').strip())} 条台词")
    tts_results = render_mod.prepare_tts(shots, assets, workdir, prog, 6, 16, force=force)
    tts_fail = sum(1 for r in tts_results.values() if not r.get("ok"))
    LOG.info("TTS: %d 条（失败 %d）", len(tts_results), tts_fail)

    voices = render_mod._voice_map(assets)
    sfx = render_mod.ensure_sfx(workdir)
    audio_results: dict[str, dict] = {}
    prog(17, "构建镜头音轨")
    with ThreadPoolExecutor(max_workers=AUDIO_WORKERS) as pool:
        futures = {}
        for shot in shots:
            sid = render_mod._shot_id(shot)
            info = render_mod.shot_tts_info(shot, tts_results, voices)
            sfx_path = None
            sfx_name = str(shot.get("sfx") or "").lower()
            if sfx_name in ("whoosh", "ding") and sfx.get(sfx_name):
                sfx_path = sfx[sfx_name]
            futures[pool.submit(render_mod.build_shot_audio, shot, info,
                                render_mod._shot_duration(shot), workdir,
                                sfx_path, force)] = sid
        done = 0
        for fut in as_completed(futures):
            sid = futures[fut]
            audio_results[sid] = fut.result()
            done += 1
            prog(17 + 5.0 * done / max(1, len(shots)), f"镜头音轨 {done}/{len(shots)}")
    shot_tts_map = render_mod.export_shot_tts_audio(shots, workdir)
    LOG.info("每镜 TTS wav（H3 audio_ref 输入）: %d 条", len(shot_tts_map))
    return {"tts": tts_results, "tts_fail": tts_fail, "sfx": sfx,
            "shot_tts_map": shot_tts_map, "audio": audio_results}


# ---------------------------------------------------------------------------
# 每镜：stage → H3 → normalize
# ---------------------------------------------------------------------------
def _reusable_h3(meta_path: Path, clip_path: Path, h3_mod) -> dict | None:
    if not (meta_path.is_file() and clip_path.is_file()):
        return None
    meta = utils.read_json(meta_path) or {}
    if not isinstance(meta, dict):
        return None
    checker = getattr(h3_mod, "meta_ok", None) if h3_mod is not None else None
    try:
        ok = bool(checker(meta)) if callable(checker) else bool(
            meta.get("h3") and meta.get("prompt_id") and meta.get("workflow"))
    except Exception:  # noqa: BLE001
        ok = False
    return meta if ok else None


def render_shot_clips(plan: dict, template: dict, paths: dict, *, mode: str,
                      seed: int, profile: dict, audio_info: dict, prog,
                      force: bool = False, fallback_static: bool = False,
                      timed: bool = False) -> dict:
    """逐镜生成 clip（H3 严格串行）→ {shot_metas, fallbacks, stats}。"""
    shots = plan.get("shots") or []
    previs = mode == "previs"
    stage_mod = _try_module("stage")
    action_mod = _try_module("action_library")
    h3_mod = _try_module("h3_comfy") if not previs else None
    if not previs:
        if stage_mod is None:
            raise RenderV3Error("pipeline/stage.py 未就绪：comfy 模式必须用舞台首帧")
        if action_mod is None:
            raise RenderV3Error("pipeline/action_library.py 未就绪：comfy 模式需要 H3 prompt")
        if h3_mod is None:
            raise RenderV3Error("pipeline/h3_comfy.py 未就绪：comfy 模式需要 ComfyUI 客户端")

    h3_cfg = template.get("h3") if isinstance(template.get("h3"), dict) else {}
    h3_w, h3_h = _parse_resolution(h3_cfg.get("resolution"), (1024, 576))
    style_prefix = style_v2.prompt_prefix(profile)
    tts_map = audio_info.get("shot_tts_map") or {}

    shot_metas: dict[str, dict] = {}
    fallbacks: list[dict] = []
    generation = {"h3": 0, "reused": 0, "cards": 0, "fallback": 0}
    bodies = [s for s in shots if not qc_v3.is_card(s)]
    total_bodies = max(1, len(bodies))
    body_no = 0

    for index, shot in enumerate(shots):
        sid = str(shot.get("id"))
        dur = float(shot.get("duration") or storyboard_v3.H3_SHOT_DURATION)
        clip_path = paths["shots_dir"] / f"{sid}.mp4"
        # ---- 卡片：静态帧 + 静帧 clip（允许静态，QC 标 static_card）----
        if qc_v3.is_card(shot):
            png = paths["frames_dir"] / f"{sid}.png"
            meta_frame = compose_card_frame(stage_mod, template, shot, png,
                                            width=h3_w, height=h3_h,
                                            workdir=paths["workdir"], profile=profile)
            if is_black_frame(png):
                # 纯黑卡片会让 blackdetect 命中硬门；改用 plate 兜底卡片并留证据
                LOG.warning("%s 卡片帧近乎纯黑（blackdetect 会命中），改用 plate 兜底卡片", sid)
                meta_frame = fallback_plate_frame(
                    template, shot, png, width=h3_w, height=h3_h,
                    profile=profile, workdir=paths["workdir"], darken=True)
                meta_frame.update({"card_fallback": True, "dark_card": True})
            make_static_clip(png, clip_path, dur, FINAL_W, FINAL_H, FINAL_FPS)
            shot_metas[sid] = {
                "id": sid, "no": shot.get("no"), "type": shot.get("type"),
                "h3": False, "static_card": True, "mode": "static",
                "duration": dur, "path": str(clip_path),
                "ref_image": str(png), "frame": meta_frame,
                "black_fraction": frame_black_fraction(png),
                "stage_fallback": bool((meta_frame or {}).get("stage_fallback")),
            }
            generation["cards"] += 1
            prog(22 + 55.0 * (index + 1) / max(1, len(shots)),
                 f"卡片 {sid}（{shot.get('type')}）")
            continue

        body_no += 1
        # ---- 非卡片：stage 首帧（+fl2v 尾帧）----
        first_png = paths["frames_dir"] / f"{sid}_first.png"
        try:
            first_meta = compose_first_frame(
                stage_mod, template, shot, first_png, width=h3_w, height=h3_h,
                workdir=paths["workdir"], index=index, profile=profile)
        except Exception as exc:  # noqa: BLE001
            if not (previs or fallback_static):
                raise RenderV3Error(f"{sid} 首帧合成失败: {exc}") from exc
            LOG.warning("%s 首帧合成失败，plate 兜底: %s", sid, exc)
            first_meta = fallback_plate_frame(template, shot, first_png,
                                              width=h3_w, height=h3_h,
                                              profile=profile,
                                              workdir=paths["workdir"])
        if is_black_frame(first_png):
            # 首帧纯黑会让钳制住的 previs/静态镜命中 blackdetect；用 plate 兜底
            LOG.warning("%s 首帧近乎纯黑，改用 plate 兜底（stage_fallback）", sid)
            first_meta = fallback_plate_frame(template, shot, first_png,
                                              width=h3_w, height=h3_h,
                                              profile=profile,
                                              workdir=paths["workdir"])
            first_meta["dark_frame"] = True

        # ---- previs：静态 clip（QC 标 previs，不算交付）----
        if previs:
            make_static_clip(first_png, clip_path, dur, FINAL_W, FINAL_H, FINAL_FPS)
            shot_metas[sid] = {
                "id": sid, "no": shot.get("no"), "type": shot.get("type"),
                "action": shot.get("action"), "h3": False, "previs": True,
                "mode": "previs", "duration": dur, "path": str(clip_path),
                "ref_image": str(first_png), "frame": first_meta,
                "black_fraction": frame_black_fraction(first_png),
                "stage_fallback": bool((first_meta or {}).get("stage_fallback")),
            }
            generation["cards"] += 1
            prog(22 + 55.0 * (index + 1) / max(1, len(shots)),
                 f"previs 镜 {body_no}/{len(bodies)} {sid}")
            continue

        # ---- comfy：断点复用 ----
        h3_meta_path = paths["h3_dir"] / f"{sid}.meta.json"
        if not force:
            reused = _reusable_h3(h3_meta_path, clip_path, h3_mod)
            if reused:
                reused.update({"id": sid, "no": shot.get("no"),
                               "type": shot.get("type"),
                               "ref_image": reused.get("ref_image") or str(first_png),
                               "path": str(clip_path), "reused": True})
                shot_metas[sid] = reused
                generation["reused"] += 1
                LOG.info("%s 复用已有 H3 片段（prompt_id=%s）", sid,
                         reused.get("prompt_id"))
                prog(22 + 55.0 * (index + 1) / max(1, len(shots)),
                     f"H3 复用 {body_no}/{len(bodies)} {sid}")
                continue

        shot_mode = str(shot.get("h3_mode") or "fl2v")
        last_png = None
        last_meta = None
        if shot_mode == "fl2v":
            last_png = paths["frames_dir"] / f"{sid}_last.png"
            last_meta = compose_last_frame(stage_mod, template, shot, first_meta,
                                           last_png, width=h3_w, height=h3_h)
            if last_meta is None:
                last_png = None
        prompt = ""
        try:
            prompt = action_mod.build_h3_prompt(
                template, shot, mode=shot_mode, style_prefix=style_prefix)
        except Exception as exc:  # noqa: BLE001
            LOG.warning("%s H3 prompt 构建失败: %s", sid, exc)
            prompt = ""
        raw_out = paths["h3_dir"] / f"{sid}.mp4"
        has_line = bool(str(shot.get("line") or "").strip())
        # 只有真正有台词的镜才传 TTS wav；无台词镜传 None → h3_comfy 用静音参考
        # 并标 audio_silent（export_shot_tts_audio 会给所有镜导出静音 wav，不能直接全用）
        audio_wav = tts_map.get(sid) if (shot_mode == "audio_ref" and has_line) else None
        # Lead 实测决策：无台词镜也走 audio_ref（静音参考 audio_wav=None），不再降 fl2v
        audio_silent = shot_mode == "audio_ref" and not audio_wav
        if audio_silent:
            LOG.info("%s 无台词 → audio_ref 静音参考（H3 重绘空镜/反应镜）", sid)
        shot_seed = int(shot.get("seed") or (seed + index))
        prog(22 + 55.0 * index / max(1, len(shots)),
             f"H3 生成 {body_no}/{len(bodies)} {sid}（{shot_mode}）")
        try:
            gen = h3_mod.generate(
                shot, first_png, raw_out, mode=shot_mode, prompt=prompt,
                duration=dur, audio_wav=audio_wav, last_image=last_png,
                seed=shot_seed, width=h3_w, height=h3_h, fps=H3_NATIVE_FPS,
                workdir=paths["h3_dir"], timeout=1800)
        except Exception as exc:  # noqa: BLE001
            if not fallback_static:
                raise RenderV3Error(f"{sid} H3 生成失败（未开 --fallback-static）: {exc}") from exc
            LOG.warning("%s H3 失败，静态兜底（QC 会列为 fallback）: %s", sid, exc)
            make_static_clip(first_png, clip_path, dur, FINAL_W, FINAL_H, FINAL_FPS)
            shot_metas[sid] = {
                "id": sid, "no": shot.get("no"), "type": shot.get("type"),
                "action": shot.get("action"), "h3": False, "fallback": True,
                "fallback_static": True, "fallback_reason": str(exc)[:400],
                "mode": "static_fallback", "duration": dur,
                "path": str(clip_path), "ref_image": str(first_png),
                "frame": first_meta, "seed": shot_seed,
            }
            fallbacks.append({"id": sid, "reason": str(exc)[:400]})
            generation["fallback"] += 1
            continue

        meta = dict(gen or {})
        if not meta.get("prompt_id"):
            on_disk = utils.read_json(raw_out.with_suffix(".meta.json")) or {}
            if isinstance(on_disk, dict):
                meta = {**on_disk, **{k: v for k, v in meta.items() if v}}
        _normalize_clip(h3_mod, raw_out, clip_path, dur, FINAL_W, FINAL_H, FINAL_FPS)
        meta.update({
            "id": sid, "no": shot.get("no"), "type": shot.get("type"),
            "action": shot.get("action"),
            "mode": meta.get("mode") or shot_mode,
            "duration": dur,
            "path": str(clip_path),
            "raw_path": str(raw_out),
            "ref_image": meta.get("ref_image") or str(first_png),
            "last_image": meta.get("last_image") or (str(last_png) if last_png else ""),
            "audio": meta.get("audio") or (str(audio_wav) if audio_wav else ""),
            "audio_silent": bool(meta.get("audio_silent", audio_silent)),
            "black_fraction": frame_black_fraction(first_png),
            "prompt": prompt,
            "gen_sec": meta.get("gen_sec"),
            "normalized": True,
        })
        shot_metas[sid] = meta
        generation["h3"] += 1
        LOG.info("%s H3 ok（%s / %s / prompt_id=%s）", sid, meta.get("mode"),
                 meta.get("workflow"), meta.get("prompt_id"))
        prog(22 + 55.0 * (index + 1) / max(1, len(shots)),
             f"H3 {body_no}/{len(bodies)} 完成 {sid}")

    stats = {
        "mode": mode, "h3": generation["h3"], "reused": generation["reused"],
        "cards": generation["cards"], "fallback": generation["fallback"],
        "shots": len(shots), "bodies": len(bodies),
    }
    return {"shot_metas": shot_metas, "fallbacks": fallbacks, "stats": stats,
            "stage_module": bool(stage_mod), "action_module": bool(action_mod),
            "h3_resolution": [h3_w, h3_h]}


# ---------------------------------------------------------------------------
# 剪辑 / 包装 / 混音
# ---------------------------------------------------------------------------
def assemble_video(plan: dict, template: dict, paths: dict, template_id: str,
                   prog, *, profile: dict, mode: str) -> dict:
    from . import render as render_mod
    shots = plan.get("shots") or []
    total = storyboard_v3.plan_duration(plan)
    fps = FINAL_FPS
    packaging = template.get("packaging") if isinstance(template.get("packaging"), dict) else {}
    transition_key = str(packaging.get("transition") or "fade")
    t_mode, t_style = style_v2.TRANSITION_ARGS.get(transition_key, ("xfade", "fade"))
    clip_paths = {str(s.get("id")): paths["shots_dir"] / f"{s.get('id')}.mp4" for s in shots}

    prog(80, f"拼接视频（{t_mode}/{t_style}，{len(shots)} 镜 / {total:.2f}s）")
    transition_sfx = None
    if t_mode == "xfade" and len(shots) > 1:
        times: list[float] = []
        cursor = 0.0
        for shot in shots[:-1]:
            cursor += float(shot.get("duration") or 0.0)
            times.append(round(cursor, 3))
        sfx = render_mod.ensure_sfx(paths["workdir"])
        transition_sfx = render_mod.build_transition_sfx(
            paths["workdir"], times, sfx.get("whoosh"), total)
        video_raw = render_mod.concat_video_xfade(
            shots, clip_paths, paths["workdir"], fps, FINAL_W, FINAL_H, total,
            transition=t_style, transition_duration=TRANSITION_DURATION, prog=prog)
    else:
        video_raw = render_mod.concat_video(shots, paths["workdir"], fps, total,
                                            clip_paths=clip_paths)
    LOG.info("video_raw: %s (%.2fs)", video_raw, utils.probe_duration(video_raw))

    # ---- 字幕 / 人名条 / 章节 ----
    prog(86, "生成字幕/花字轨")
    timings = []
    cursor = 0.0
    for shot in shots:
        dur = float(shot.get("duration") or 0.0)
        timings.append({"shot": shot, "start": cursor, "end": cursor + dur,
                        "duration": dur})
        cursor += dur
    episode = dict(plan.get("episode") or {})
    episode["segments"] = plan.get("episode", {}).get("segments") or []
    body_sids = {str(s.get("id")) for s in shots if not qc_v3.is_card(s)}
    ass_info = ass_builder.build_ass(
        episode, timings, plan.get("assets") or [], fonts.ensure_fonts(paths["workdir"]),
        paths["workdir"] / "final.ass", width=FINAL_W, height=FINAL_H,
        char_shot_ids=body_sids, dynamic_shot_ids=body_sids)

    # ---- 音轨 / BGM ----
    prog(88, "拼接旁白音轨")
    narration = render_mod.concat_audio(shots, paths["workdir"], total)
    LOG.info("narration: %.2fs", utils.probe_duration(narration))
    bgm = render_mod.ensure_bgm(paths["workdir"])

    # ---- 编码成片（调色 + 字幕 + 包装 + 混音）----
    prog(90, "编码成片（烧字幕 + 调色/包装 + 混音）")
    font_info = fonts.ensure_fonts(paths["workdir"])
    grade = style_v2.grade_filter(profile)
    packaging_filter = style_v2.packaging_filters(
        profile, paths["workdir"], FINAL_W, FINAL_H, font_info["body"]["file"])
    render_mod.final_encode(
        paths["workdir"], paths["out"], total, fps, FINAL_W, FINAL_H, bgm,
        has_ass=ass_info["events"] > 0, prog=prog, pct0=90, pct1=99,
        transition_sfx=transition_sfx, grade_filter=grade or None,
        packaging_filter=packaging_filter or None)
    return {"total_duration": total, "video_raw": str(video_raw),
            "ass": ass_info, "narration": str(narration),
            "bgm": bool(bgm), "transition_sfx": bool(transition_sfx),
            "transition": {"mode": t_mode, "style": t_style,
                           "duration": TRANSITION_DURATION,
                           "key": transition_key},
            "grade_filter": grade, "packaging_filter": bool(packaging_filter)}


# ---------------------------------------------------------------------------
# 抽帧 + contact sheet
# ---------------------------------------------------------------------------
def extract_frames(video: Path, paths: dict, total: float, count: int = FRAME_SAMPLES) -> dict:
    frames_dir = utils.ensure_dir(paths["frames_dir"])
    count = max(1, int(count))
    samples: list[Path] = []
    times = [round((i + 0.5) * max(0.1, total) / count, 3) for i in range(count)]
    for i, t in enumerate(times, start=1):
        out = frames_dir / f"frame_{i:02d}.png"
        try:
            utils.ffmpeg(["-ss", f"{t:.3f}", "-i", str(video), "-frames:v", "1",
                          "-q:v", "2", "-y", str(out)])
            if out.is_file():
                samples.append(out)
        except Exception as exc:  # noqa: BLE001
            LOG.warning("抽帧失败 t=%.2fs: %s", t, exc)
    sheet = paths["out_dir"] / "frames" / "contact_sheet.png"
    cols = min(CONTACT_COLS, max(1, len(samples)))
    rows = max(1, math.ceil(len(samples) / cols)) if samples else 1
    try:
        utils.ffmpeg([
            "-framerate", "1", "-start_number", "1",
            "-i", str(frames_dir / "frame_%02d.png"),
            "-frames:v", "1",
            "-vf", f"scale=320:180,tile={cols}x{rows}:padding=6:margin=6",
            "-y", str(sheet),
        ])
    except Exception as exc:  # noqa: BLE001
        LOG.warning("contact sheet 生成失败: %s", exc)
        sheet = None
    return {"frames": [str(p) for p in samples],
            "contact_sheet": str(sheet) if sheet and Path(sheet).is_file() else "",
            "times": times}


# ---------------------------------------------------------------------------
# h3_meta / manifest
# ---------------------------------------------------------------------------
def build_h3_meta(plan: dict, template: dict, shot_metas: dict, mode: str,
                  seed: int, stats: dict) -> dict:
    shots_meta = []
    for shot in plan.get("shots") or []:
        sid = str(shot.get("id"))
        meta = dict(shot_metas.get(sid) or {})
        # 契约 §0/§4：每镜 H3 证据必须能对回计划的 shot / segment / role / action
        meta.update({
            "id": meta.get("id") or sid,
            "shot_id": sid,
            "no": meta.get("no") or shot.get("no"),
            "type": meta.get("type") or shot.get("type"),
            "segment_id": shot.get("segment_id"),
            "segment_name": shot.get("segment_name"),
            "role": shot.get("role"),
            "speaker": shot.get("speaker"),
            "speaker_name": shot.get("speaker_name"),
            "h3_mode": shot.get("h3_mode"),
            "action": meta.get("action") or shot.get("action"),
            "duration": meta.get("duration") or shot.get("duration"),
            "line": bool(str(shot.get("line") or "").strip()),
        })
        shots_meta.append(meta)
    bodies = [m for m in shots_meta if not m.get("static_card") and
              not m.get("previs")]
    h3_ok = [m for m in bodies if m.get("h3") is True]
    prompt_ids = [str(m.get("prompt_id")) for m in h3_ok if m.get("prompt_id")]
    return {
        "template": str(template.get("id")),
        "label": str(template.get("label") or ""),
        "h3_mode": mode,
        "previs": mode == "previs",
        "seed": int(seed),
        "workflow": str((template.get("h3") or {}).get("workflow") or ""),
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "shots_count": len(shots_meta),
        "h3_shots": len(h3_ok),
        "cards": len(shots_meta) - len(bodies),
        "prompt_ids": len(set(prompt_ids)),
        "prompt_ids_unique": len(set(prompt_ids)) == len(prompt_ids),
        "fallbacks": [{"id": m.get("id"), "reason": m.get("fallback_reason") or
                       m.get("error") or ""}
                      for m in bodies if m.get("h3") is not True
                      and not m.get("previs")],
        "stats": stats,
        "shots": shots_meta,
    }


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------
def run_render(args) -> int:
    """带输出目录互斥锁的入口（同一目录禁止并发渲染，避免互相覆盖成片）。"""
    template_id = str(_arg(args, "template", "") or "").strip()
    lock = None
    try:
        lock = _acquire_lock(lock_target(args), template_id)
        return _run_render(args)
    except RenderV3Error as exc:
        LOG.error("render_v3 拒绝执行: %s", exc)
        utils.emit_json_line({"ok": False, "error": str(exc),
                              "template": template_id})
        return 1
    finally:
        _release_lock(lock)


def _run_render(args) -> int:
    t_start = time.time()
    prog = utils.Progress()
    template_id = str(_arg(args, "template", "") or "").strip()
    if not template_id:
        utils.emit_json_line({"ok": False, "error": "缺少 --template"})
        return 1
    mode = render_mode(args)
    previs = mode == "previs"
    try:
        duration = float(_arg(args, "duration", storyboard_v3.DEFAULT_TARGET_DURATION))
    except (TypeError, ValueError):
        duration = storyboard_v3.DEFAULT_TARGET_DURATION
    seed = int(_arg(args, "seed", DEFAULT_SEED) or DEFAULT_SEED)
    force = bool(_arg(args, "force", False))
    fallback_static = bool(_arg(args, "fallback_static", False))
    limit = _arg(args, "limit")

    # ---- 1) load + validate ----
    try:
        template = template_v3.load(template_id)
    except template_v3.TemplateError as exc:
        utils.emit_json_line({"ok": False, "error": str(exc)})
        return 1
    validation = template_v3.validate(template, strict_plates=not previs)
    if not validation["ok"]:
        if not previs:
            LOG.error("模板校验失败，拒绝渲染（comfy 模式不产半成品）:")
            for err in validation["errors"][:20]:
                LOG.error("  ERR %s", err)
            utils.emit_json_line({"ok": False, "template": template_id,
                                  "error": "模板校验失败",
                                  "validation": validation})
            return 1
        LOG.warning("previs 模式容忍模板校验错误（仅看构图，不作交付）: %d 项",
                    len(validation["errors"]))
    prog(1, f"模板 {template_id} / {mode} / 目标 {duration:.0f}s / seed {seed}")

    profile = style_v2.profile_for(template_id,
                                   override=str(template.get("style") or "") or None)
    paths = resolve_paths(args, template_id)
    if (previs and not force and not _arg(args, "out") and not _arg(args, "workdir")):
        # 已有 comfy 交付产物时，previs 改写到 previs/ 子目录，避免把交付成片覆盖成预览
        delivery = utils.read_json(paths["out_dir"] / "qc_report.json")
        if paths["out"].is_file() and isinstance(delivery, dict) \
                and delivery.get("ok") and not delivery.get("previs"):
            preview_dir = paths["out_dir"] / "previs"
            LOG.warning("检测到已有 comfy 交付产物（QC PASS），previs 改写到 %s 避免覆盖",
                        preview_dir)
            paths.update({
                "out": preview_dir / "final.mp4",
                "out_dir": preview_dir,
                "workdir": preview_dir / "work",
                "shots_dir": preview_dir / "shots",
                "frames_dir": preview_dir / "frames",
                "h3_dir": preview_dir / "work" / "h3",
            })
    _ensure_dirs(paths)
    LOG.info("产物目录: %s（work=%s，style_preset=%s）",
             paths["out_dir"], paths["workdir"], profile.get("preset"))

    # ---- 2) storyboard ----
    plan = storyboard_v3.build_plan(template, duration, include_cards=True, seed=seed)
    if limit:
        try:
            keep = max(1, int(limit))
        except (TypeError, ValueError):
            keep = 0
        if keep:
            plan["shots"] = plan["shots"][:keep]
            ids = {str(s.get("id")) for s in plan["shots"]}
            plan["cards"] = {k: v for k, v in (plan.get("cards") or {}).items()
                             if str(v.get("id")) in ids}
            plan["episode"]["total_duration"] = storyboard_v3.plan_duration(plan)
            plan["episode"]["duration"] = plan["episode"]["total_duration"]
            plan["episode"]["shots_total"] = len(plan["shots"])
            for seg in plan["episode"].get("segments") or []:
                seg["duration"] = round(sum(
                    float(s.get("duration") or 0) for s in plan["shots"]
                    if s.get("segment_id") == seg.get("id")), 3)
    total = storyboard_v3.plan_duration(plan)
    utils.write_json(paths["workdir"] / "episode.json", plan)
    LOG.info("storyboard: %d 镜 / %.2fs（H3 镜 %d、卡片 %d、台词 %d）",
             len(plan["shots"]), total, plan["meta"]["h3_shot_count"],
             plan["meta"]["card_count"],
             sum(1 for s in plan["shots"] if str(s.get("line") or "").strip()))
    prog(4, f"storyboard：{len(plan['shots'])} 镜 / {total:.2f}s")

    # ---- 3) TTS / 镜头音轨 ----
    audio_info = prepare_audio(plan, paths["workdir"], prog, force)

    # ---- 4) 每镜 stage → H3（GPU 串行）----
    prog(22, f"逐镜生成（{mode}）：stage 首帧 → {'静态 clip' if previs else 'H3'}")
    clips = render_shot_clips(
        plan, template, paths, mode=mode, seed=seed, profile=profile,
        audio_info=audio_info, prog=prog, force=force,
        fallback_static=fallback_static)
    shot_metas = clips["shot_metas"]
    LOG.info("镜头生成: %s", clips["stats"])
    if clips["fallbacks"]:
        LOG.warning("静态兜底镜（QC 会标红，验收不接受）: %s",
                    [f["id"] for f in clips["fallbacks"]])

    # ---- 5) 剪辑 / 字幕 / 混音 / 编码 ----
    assemble = assemble_video(plan, template, paths, template_id, prog,
                              profile=profile, mode=mode)
    if not paths["out"].is_file():
        raise RenderV3Error(f"成片未生成: {paths['out']}")
    probe = _probe(paths["out"])
    LOG.info("成片: %s（%.2fs / %dx%d / %.2ffps）", paths["out"],
             probe["duration"], probe["width"], probe["height"], probe["fps"])

    # ---- 6) 抽帧 + contact sheet ----
    prog(99, "抽帧 + contact sheet")
    frames = extract_frames(paths["out"], paths, total)

    # ---- 7) QC ----
    subtitles = paths["workdir"] / "final.ass"
    qc = qc_v3.evaluate(video=paths["out"], plan=plan, shot_metas=shot_metas,
                        template=template, subtitles=subtitles,
                        h3_require=not previs)
    qc_v3.write_report(qc, paths["out_dir"] / "qc_report.json")

    # ---- 8) h3_meta / manifest / 摘要 ----
    h3_meta = build_h3_meta(plan, template, shot_metas, mode, seed, clips["stats"])
    utils.write_json(paths["out_dir"] / "h3_meta.json", h3_meta)

    elapsed = round(time.time() - t_start, 1)
    summary = {
        "ok": bool(qc["ok"]),
        "template": template_id,
        "label": str(template.get("label") or ""),
        "h3_mode": mode,
        "previs": previs,
        "deliverable": bool(qc.get("deliverable")),
        "path": str(paths["out"]),
        "out_dir": str(paths["out_dir"]),
        "workdir": str(paths["workdir"]),
        "duration": probe["duration"],
        "expected_duration": total,
        "resolution": f"{probe['width']}x{probe['height']}",
        "fps": probe["fps"],
        "size_bytes": paths["out"].stat().st_size if paths["out"].is_file() else 0,
        "shots": len(plan["shots"]),
        "h3_shots": sum(1 for s in plan["shots"] if not qc_v3.is_card(s)),
        "cards": sum(1 for s in plan["shots"] if qc_v3.is_card(s)),
        "h3_generated": clips["stats"]["h3"],
        "h3_reused": clips["stats"]["reused"],
        "fallbacks": len(clips["fallbacks"]),
        "dialogue_shots": qc["stats"].get("dialogue_shots", 0),
        "audio_silent_shots": sum(1 for m in shot_metas.values()
                                  if m.get("audio_silent")),
        "h3_mode_policy": storyboard_v3.H3_MODE_POLICY,
        "subtitle_coverage": qc["stats"].get("subtitle_coverage", 0.0),
        "h3_coverage": qc["stats"].get("h3_coverage", 0.0),
        "black_segments": qc["stats"].get("black_segments", -1),
        "qc_ok": bool(qc["ok"]),
        "qc_failed_checks": [k for k, v in (qc.get("checks") or {}).items()
                             if not v.get("ok")],
        "qc_report": str(paths["out_dir"] / "qc_report.json"),
        "h3_meta": str(paths["out_dir"] / "h3_meta.json"),
        "contact_sheet": frames.get("contact_sheet") or "",
        "frames": len(frames.get("frames") or []),
        "seed": seed,
        "elapsed_sec": elapsed,
        "episode": str(paths["workdir"] / "episode.json"),
        "manifest": str(paths["workdir"] / "manifest.json"),
        "validation": {"ok": validation["ok"],
                       "errors": len(validation.get("errors") or []),
                       "warnings": len(validation.get("warnings") or [])},
        "transition": assemble.get("transition"),
        "style": {"preset": profile.get("preset"),
                  "palette": profile.get("palette"),
                  "character_style": profile.get("character_style")},
        "stage": {"module": clips["stage_module"],
                  "fallback": bool(any(m.get("stage_fallback")
                                       for m in shot_metas.values()))},
        "h3_resolution": clips.get("h3_resolution"),
        "tts_failures": audio_info.get("tts_fail"),
    }
    manifest = dict(summary)
    manifest["plan_meta"] = plan.get("meta")
    manifest["segments"] = plan["episode"].get("segments")
    manifest["qc"] = {"ok": qc["ok"], "checks": qc["checks"],
                      "fallbacks": qc["fallbacks"], "stats": qc["stats"]}
    utils.write_json(paths["workdir"] / "manifest.json", manifest)
    prog(100, f"完成：{paths['out']} / QC {'PASS' if qc['ok'] else 'FAIL'}")
    utils.emit_json_line(summary)
    return 0 if qc["ok"] else 1


def main(args) -> int:
    try:
        return run_render(args)
    except Exception as exc:  # noqa: BLE001
        LOG.error("render_v3 失败: %s", exc)
        LOG.debug("%s", __import__("traceback").format_exc())
        utils.emit_json_line({"ok": False, "error": str(exc)})
        return 1
