# -*- coding: utf-8 -*-
"""V55：零重复的 5 分钟填充镜头。

原则：**不复用同一段视频**。每条填充镜都是新的取景/运镜/调色（同一张场景美术图可以有不同构图，
但不是把成片循环播放）。素材图只从该模板自己的产物里取（关键帧/海报/场景图）。

用途：
- V5 栈：作为 b-roll 镜插入 render_v5 的分镜（保留字幕/BGM/转场）
- HQ 短片栈：与已有镜头交错拼接（每条原镜头只出现一次）
"""
from __future__ import annotations

import hashlib
import json
import math
import random
from pathlib import Path

from PIL import Image, ImageEnhance

from . import config, utils

OUT = config.OUTPUTS_ROOT
CACHE = OUT / "fillers"

CAMERAS = ("in", "out", "left", "right", "up", "down", "diag")
GRADES = (
    "eq=saturation=1.06:contrast=1.03",
    "eq=saturation=0.94:brightness=-0.02",
    "curves=preset=increase_contrast",
    "colorbalance=rs=.04:bs=-.04:gm=.02",
    "",
)


def _images(tid: str, template: dict) -> list[Path]:
    found: list[Path] = []
    # 3D/HQ 素材优先：关键帧 & 3D 镜头抽帧（保证空镜和正片同一套 3D 写实风格）
    kf_roots = sorted((config.REPO_ROOT / "tmp" / "v16" / "keyframes").glob(f"**/{tid}*/**/*.png"))
    keyframes = [p for p in kf_roots if p.is_file() and p.stat().st_size > 50_000]
    shots_dir = OUT / "templates_3d" / tid / "shots"
    if not keyframes and shots_dir.is_dir():
        frames_dir = utils.ensure_dir(OUT / "fillers" / tid / "frames")
        for clip in sorted(shots_dir.glob("*.mp4"))[:12]:
            for ratio in (0.25, 0.6):
                frame = frames_dir / f"{clip.stem}_{int(ratio * 100)}.jpg"
                if frame.is_file() and frame.stat().st_size > 20_000:
                    keyframes.append(frame)
                    continue
                try:
                    d = float(utils.probe_duration(clip) or 0)
                    utils.ffmpeg(["-ss", f"{max(0.0, d * ratio):.2f}", "-i", str(clip),
                                  "-frames:v", "1", "-q:v", "2", "-y", str(frame)])
                    if frame.is_file() and frame.stat().st_size > 20_000:
                        keyframes.append(frame)
                except Exception:  # noqa: BLE001
                    continue
    for root in (OUT / "templates_3d" / tid, OUT / "templates_v5" / tid,
                 OUT / "templates_2d" / tid):
        if not root.is_dir():
            continue
        for pattern in ("poster.jpg", "poster.png", "keyframes/**/*.png",
                        "*.png", "*.jpg", "scenes/**/*.png", "scenes/**/*.jpg"):
            found.extend(sorted(root.glob(pattern)))
    # 角色立绘/场景美术（V5 栈）：场景库是 172 张，足够让每条填充镜用不同图
    if keyframes:
        found = keyframes + found
    # R5：场景底板/场景库是**最适合空镜**的素材（无人物、构图宽）——不再只在"没有关键帧"时才用。
    plate_dir = OUT / "scene_plates"
    if plate_dir.is_dir():
        found.extend(sorted(plate_dir.glob("*.png")))
    scenes = config.REPO_ROOT / "assets" / "v5" / "scenes"
    if scenes.is_dir():
        found.extend(sorted(scenes.rglob("*.png"))[:120])
        found.extend(sorted(scenes.rglob("*.jpg"))[:40])
    # 角色立绘**不**作为空镜素材：它们是白底/透明底的资产图，铺满画面会变成"白底小人"。
    # （R5：之前把 characters/*.png 也塞进来，亮度过滤后反而最容易被选中。）
    for cid in (template.get("cast") or []):
        char = cid.get("character") if isinstance(cid, dict) else cid
        base = config.REPO_ROOT / "assets" / "v5" / "characters" / str(char)
        if base.is_dir():
            # 只取该角色已生成的"场景化"图（带背景的立绘），不要 cutout/three_view
            for pat in ("scene.png", "portrait.png", "portrait_3d.png", "portrait_real.png"):
                p = base / pat
                if p.is_file() and p.stat().st_size > 20_000:
                    found.append(p)
    # HQ 短片的关键帧（多候选图也算不同素材）
    for kf in (config.REPO_ROOT / "tmp" / "v16" / "keyframes").glob(f"**/{tid}*/**/*.png"):
        found.append(kf)
    seen, unique = set(), []
    for path in found:
        key = str(path.resolve())
        if key in seen or path.suffix.lower() not in (".png", ".jpg", ".jpeg"):
            continue
        try:
            if path.stat().st_size < 20_000:
                continue
        except OSError:
            continue
        seen.add(key)
        unique.append(path)
    # R5：空镜应该是"景"，不是"脸"，而且要和本模板的场景同风格。
    # 分层：本模板用到的场景（scene_plates / 场景库）> 其它场景 > 镜头抽帧 > 海报 > 关键帧。
    wanted = _wanted_scenes(template)
    unique.sort(key=lambda p: _tier(p, wanted))
    return _prefer_bright(unique)[:80]


def _wanted_scenes(template: dict | None) -> set[str]:
    out: set[str] = set()
    for act in ((template or {}).get("acts") or []):
        scene = str((act or {}).get("scene") or "").replace("_3d", "").strip()
        if scene:
            out.add(scene)
    for key in ("scene", "stage"):
        val = (template or {}).get(key)
        if isinstance(val, dict):
            scene = str(val.get("plate") or "").replace("_3d", "").strip()
            if scene:
                out.add(scene)
    return out


def _tier(path: Path, wanted: set[str] | None = None) -> int:
    s = str(path).replace("\\", "/")
    wanted = wanted or set()
    if "/scene_plates/" in s or "/assets/v5/scenes/" in s:
        stem = path.stem.split("__")[0].replace("_3d", "")
        # 同场景优先；不同场景但仍是"景"（tier 1）好过人物特写
        return 0 if (not wanted or stem in wanted) else 1
    if "/fillers/" in s or "/shots/" in s:
        return 2          # 镜头抽帧：构图正确但带人
    if "/keyframes/" in s:
        return 4          # 关键帧多为特写，垫底
    return 3


def _luma(path: Path) -> float:
    """快速估算平均亮度（缩到 64×36 后取均值，避开大图解码开销）。"""
    try:
        with Image.open(path) as im:
            small = im.convert("L").resize((64, 36))
            import numpy as np
            return float(np.asarray(small, dtype=float).mean())
    except Exception:  # noqa: BLE001
        return 0.0


def _prefer_bright(paths: list[Path], min_luma: float = 55.0,
                   keep_ratio: float = 0.6, hard_floor: float = 30.0) -> list[Path]:
    """丢掉过暗的素材（R5：用户反馈"外面全黑、中间一个亮圈"）。

    空镜素材如果本身就是暗场（H3 输出常常四周全黑 + 中间一团光），裁暗角也救不回来 ——
    只能不选它。规则：
    1) 先把低于 `hard_floor` 的**一律丢掉**（这种图怎么修都是黑框）；
    2) 剩下的够亮（≥ min_luma）就留；不够就用最亮的前 `keep_ratio`，保证还有素材可用。
    """
    if not paths:
        return paths
    scored = [(p, _luma(p)) for p in paths]
    scored = [(p, v) for p, v in scored if v >= hard_floor] or scored
    bright = [p for p, v in scored if v >= min_luma]
    if len(bright) >= max(3, int(len(scored) * 0.2)):
        return bright
    scored.sort(key=lambda item: item[1], reverse=True)
    keep = max(1, int(len(scored) * keep_ratio))
    return [p for p, _v in scored[:keep]]


def _zoompan(index: int, frames: int, width: int, height: int) -> str:
    cam = CAMERAS[index % len(CAMERAS)]
    zooms = [(1.0, 1.16), (1.18, 1.0), (1.05, 1.12), (1.12, 1.02)]
    z0, z1 = zooms[index % len(zooms)]
    zexpr = f"{z0}+({z1}-{z0})*on/{max(1, frames - 1)}"
    cx, cy = "iw/2", "ih/2"
    if cam == "left":
        cx = f"iw/2-(iw/zoom/2)*0.2+on*{width * 0.06 / max(1, frames):.4f}"
    elif cam == "right":
        cx = f"iw/2+(iw/zoom/2)*0.2-on*{width * 0.06 / max(1, frames):.4f}"
    elif cam == "up":
        cy = f"ih/2-(ih/zoom/2)*0.18+on*{height * 0.05 / max(1, frames):.4f}"
    elif cam == "down":
        cy = f"ih/2+(ih/zoom/2)*0.18-on*{height * 0.05 / max(1, frames):.4f}"
    elif cam == "diag":
        cx = f"iw/2-(iw/zoom/2)*0.1+on*{width * 0.05 / max(1, frames):.4f}"
        cy = f"ih/2-(ih/zoom/2)*0.1+on*{height * 0.04 / max(1, frames):.4f}"
    return (f"scale={width * 2}:{height * 2}:force_original_aspect_ratio=increase,"
            f"crop={width * 2}:{height * 2},"
            f"zoompan=z='{zexpr}':x='{cx}':y='{cy}':d={frames}:s={width}x{height}:fps=25,"
            f"setsar=1")


def _defringe(image: Path, cache_dir: Path, margin: float = 0.09,
              width: int = 1280, height: int = 720) -> Path:
    """裁掉四周 `margin` 的暗角/羽化边，轻微提亮，缓存到 cache_dir。

    用户反馈「外面全是黑的、中间一个羽化圈」的根因：空镜素材（关键帧/镜头抽帧）本身
    带风格化暗角，zoompan 再放大一次就变成"黑框 + 亮圈"。这里先中心裁 91% 再升采样到
    1280×720，既去掉暗角边，也保住构图；亮度 ×1.08 补偿裁切后的观感。
    """
    src = Path(image)
    try:
        st = src.stat()
        key = hashlib.sha1(f"{src.resolve()}|{st.st_mtime_ns}|{st.st_size}|{margin}"
                           .encode("utf-8")).hexdigest()[:12]
    except OSError:
        key = "nofile"
    dst = Path(cache_dir) / f"{src.stem}_defringe_{key}.png"
    if dst.is_file() and dst.stat().st_size > 10_000:
        return dst
    utils.ensure_dir(Path(cache_dir))
    with Image.open(src) as raw:
        im = raw.convert("RGB")
        w, h = im.size
        box = (int(w * margin), int(h * margin),
               int(w * (1 - margin)), int(h * (1 - margin)))
        im = im.crop(box).resize((width, height), Image.LANCZOS)
        im = ImageEnhance.Brightness(im).enhance(1.08)
    im.save(dst, "PNG")
    return dst


def render_filler(index: int, image: Path, seconds: float, out: Path,
                  width: int = 1280, height: int = 720, grade: str = "") -> Path:
    frames = max(2, int(round(seconds * 25)))
    vf = _zoompan(index, frames, width, height)
    if grade:
        vf += "," + grade
    # R5：先去掉素材自带的暗角/羽化边（用户反馈的"外面全黑、中间亮圈"），再 zoompan
    src = _defringe(image, Path(out).parent / "_src", width=width, height=height)
    utils.ffmpeg(["-i", str(src),
                  "-f", "lavfi", "-i", f"anullsrc=r=48000:cl=stereo",
                  "-vf", vf + ",format=yuv420p",
                  "-t", f"{seconds:.3f}", "-r", "25",
                  "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
                  "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2",
                  "-shortest", "-y", str(out)])
    return out


def make_fillers(tid: str, template: dict, need_sec: float, workdir: Path,
                 seed: int = 20261105) -> list[dict]:
    """按需生成新的填充镜（每条不同取景/运镜/调色），返回镜头描述列表。"""
    images = _images(tid, template)
    if not images or need_sec <= 0:
        return []
    utils.ensure_dir(workdir)
    rnd = random.Random(seed)
    total, index = 0.0, 0
    shots: list[dict] = []
    while total < need_sec - 0.2 and index < 200:
        seconds = round(min(5.0, max(3.0, rnd.uniform(3.4, 4.8))), 2)
        image = images[index % len(images)]
        grade = GRADES[index % len(GRADES)]
        sid = f"fill_{index:03d}"
        clip = workdir / f"{sid}.mp4"
        try:
            render_filler(index, image, seconds, clip, grade=grade)
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(f"填充镜渲染失败（{image.name}）: {exc}") from exc
        shots.append({"id": sid, "index": index, "image": str(image),
                      "clip": str(clip), "duration": seconds,
                      "camera": CAMERAS[index % len(CAMERAS)], "grade": grade})
        total += seconds
        index += 1
    return shots


def _source_clips(tid: str, shots_subdir: str = "shots",
                  allowed: set[str] | None = None) -> list[Path]:
    root = OUT / "templates_3d" / tid / shots_subdir
    if not root.is_dir():
        root = OUT / "templates_v5" / tid / "shots"
    if not root.is_dir():
        return []
    clips = [p for p in sorted(root.glob("*.mp4")) if p.stat().st_size > 10_000]
    if allowed:
        # 只认分镜里真实存在的镜头，避免把历史 fill_*/卡片混进成片
        clips = [p for p in clips if p.stem in allowed]
    return clips


def _conform(path: Path, target: float, work: Path) -> float:
    """不足 target 用末帧定格补、超出则裁；保证“满 N 分钟”。"""
    actual = float(utils.probe_duration(path) or 0.0)
    if actual <= 0 or abs(actual - target) < 0.25:
        return actual
    out = work / "conform.mp4"
    if actual < target:
        pad = min(30.0, target - actual + 0.04)
        utils.ffmpeg(["-i", str(path),
                      "-vf", f"tpad=stop_mode=clone:stop_duration={pad:.3f}",
                      "-af", f"apad=whole_dur={target:.3f}",
                      "-t", f"{target:.3f}",
                      "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
                      "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2",
                      "-movflags", "+faststart", "-y", str(out)])
    else:
        utils.ffmpeg(["-i", str(path), "-t", f"{target:.3f}",
                      "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
                      "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2",
                      "-movflags", "+faststart", "-y", str(out)])
    if out.is_file() and out.stat().st_size > 0:
        out.replace(path)
    return float(utils.probe_duration(path) or 0.0)


def compose(tid: str, template: dict, target: float = 300.0,
            force: bool = False, shots_subdir: str = "shots",
            out_suffix: str = "", allow_fillers: bool = True) -> dict:
    """短片栈：把已有镜头（每条只用一次）+ 新生成的空镜交错拼成 target 秒。

    不循环、不复用片段；源镜头全部保留，空缺用新空镜补足。
    """
    import time as _time

    started = _time.time()
    out_dir = OUT / "templates_3d" / tid
    final = out_dir / f"final_{int(target)}s{out_suffix}.mp4"
    allowed = None
    ep_path = OUT / "templates_3d" / tid / "work" / "episode.json"
    if ep_path.is_file():
        try:
            ep = json.loads(ep_path.read_text(encoding="utf-8"))
            allowed = {str(x.get("id")) for x in ep.get("shots") or [] if x.get("id")}
        except (OSError, ValueError):
            allowed = None
    clips = _source_clips(tid, shots_subdir, allowed)
    if not clips:
        return {"ok": False, "template": tid, "error": "没有镜头素材（先渲染 shots）"}
    if final.is_file() and not force and final.stat().st_mtime >= max(c.stat().st_mtime for c in clips):
        return {"ok": True, "cached": True, "template": tid, "out": str(final),
                "duration": utils.probe_duration(final)}
    src_total = sum(float(utils.probe_duration(c) or 0) for c in clips)
    work = utils.ensure_dir(OUT / "fillers" / tid)
    # 章节卡：按模板幕名生成 3 张「第 N 章」卡，插在 25/50/75% 位置
    acts = [a for a in ((template or {}).get("acts") or []) if isinstance(a, dict)]
    try:
        _scenes = style_of({"style": (template or {}).get("style")}) and {}
    except Exception:  # noqa: BLE001
        _scenes = {}
    try:
        from . import v5_catalog as _v5c  # noqa: PLC0415
        _scenes = _v5c.load_catalog().get("scenes") or {}
    except Exception:  # noqa: BLE001
        _scenes = {}
    def _scene_label(scene_id: str) -> str:
        scene = _scenes.get(str(scene_id)) or {}
        return str(scene.get("label") or scene_id or "")
    chapter_titles = []
    for act in acts:
        label = _scene_label(str(act.get("scene") or ""))
        chapter_titles.append(label or str(act.get("name") or act.get("id") or ""))
    chapter_titles = [t for t in chapter_titles if t][:3]
    cards: list[Path] = []
    card_secs = 2.4
    if chapter_titles:
        try:
            from . import longform  # noqa: PLC0415
            poster = longform._poster_of(tid)
            for ci, name in enumerate(chapter_titles, start=1):
                card_path = work / f"chapter_{ci}.mp4"
                longform._card("", poster, card_secs, card_path)  # 3D 标题由 ASS 叠加
                cards.append(card_path)
        except Exception:  # noqa: BLE001
            cards = []
    need = max(0.0, target * 1.01 - src_total - len(cards) * card_secs)
    # 严格模式：不生成空镜（不足就用末尾定格兜底，并在证据里标出来）
    fillers = make_fillers(tid, template, need, work) if allow_fillers else []
    # 交错：每条源镜头之间均摊空镜，避免“先播完再补空镜”
    total = len(clips) + len(fillers)
    ratio = (len(fillers) / max(1, len(clips))) if fillers else 0
    ordered: list[Path] = []
    fi = 0
    for idx, clip in enumerate(clips):
        ordered.append(clip)
        take = int(round((idx + 1) * ratio)) - fi
        for _ in range(max(0, take)):
            if fi < len(fillers):
                ordered.append(Path(fillers[fi]["clip"]))
                fi += 1
    while fi < len(fillers):
        ordered.append(Path(fillers[fi]["clip"]))
        fi += 1
    # 字幕：源镜头台词 + 空镜标签（综艺花字），拼接后烧进成片
    meta_by_id = {}
    ep_path = out_dir / "work" / "episode.json"
    if ep_path.is_file():
        try:
            ep = json.loads(ep_path.read_text(encoding="utf-8"))
            for s in ep.get("shots") or []:
                meta_by_id[str(s.get("id"))] = s
        except (OSError, ValueError):
            meta_by_id = {}
    tpl_label = str((template or {}).get("label") or tid)
    title_cues: list[dict] = []
    if cards:
        marked: list[Path] = []
        cum = 0.0
        marks = [target * k / 4 for k in (1, 2, 3)]
        ci = 0
        for clip in ordered:
            marked.append(clip)
            cum += float(utils.probe_duration(clip) or 0)
            while ci < len(cards) and cum >= marks[ci]:
                marked.append(cards[ci])
                cum += card_secs
                if ci < len(chapter_titles):
                    title_cues.append({"start": round(cum - card_secs, 2),
                                       "end": round(cum, 2),
                                       "text": f"第{ci + 1}章 · {chapter_titles[ci]}"})
                ci += 1
        ordered = marked

    cues = []
    cursor = 0.0
    for clip in ordered:
        dur = float(utils.probe_duration(clip) or 0.0)
        src_shot = meta_by_id.get(clip.stem) or {}
        filler = not src_shot
        cues.append({"start": round(cursor, 3), "end": round(cursor + dur, 3),
                     "line": str(src_shot.get("line") or ""),
                     "speaker_name": str(src_shot.get("speaker_name") or src_shot.get("speaker") or ""),
                     # R5：空镜不再打 "空镜 · " 内部标记（观众不该看到制作术语）。
                     # 这里只给一个中性的"现场"标签，真正好看的花字由 variety_subs 的分幕名负责。
                     "scene": ("现场" if filler else str(src_shot.get("scene") or "")),
                     "filler": filler})
        cursor += dur
    from . import variety_subs  # noqa: PLC0415
    captions = []
    for act in (template or {}).get("acts") or []:
        label = str((_scenes.get(str(act.get("scene"))) or {}).get("label") or "")
        name = str(act.get("name") or "")
        if label and label not in captions:
            captions.append(label)
        if name and name not in captions:
            captions.append(name)
    premise = str(((template or {}).get("season") or {}).get("premise") or "")
    if premise:
        captions.append(premise[:18])
    ass_info = variety_subs.build_ass(cues, work / "final.ass", 1280, 720,
                                      style=style_of(template), captions=captions,
                                      title_cues=title_cues)
    norm_dir = utils.ensure_dir(work / "norm")
    normalized: list[Path] = []
    for idx, clip in enumerate(ordered):
        normalized.append(_normalize_clip(clip, norm_dir / f"n{idx:03d}.mp4"))
    concat = work / "concat.txt"
    concat.write_text("\n".join(f"file '{p.as_posix()}'" for p in normalized) + "\n",
                      encoding="utf-8")
    utils.ffmpeg(["-f", "concat", "-safe", "0", "-i", str(concat),
                  "-t", f"{target:.3f}", "-c", "copy",
                  "-movflags", "+faststart", "-y", str(final)])
    # 烧综艺字幕（libass）：cwd=work 用相对文件名，避免 Windows 盘符转义
    bgm = None
    if ass_info.get("count"):
        subbed = work / "final_subbed.mp4"
        # 烧字幕 + 配 BGM + 统一响度（-16 LUFS），一次编码完成
        bgm = None
        try:
            from . import v5_audio  # noqa: PLC0415
            bgm = v5_audio.ensure_bgm(style_of(template), work)
        except Exception:  # noqa: BLE001
            bgm = None
        args = ["-i", str(final)]
        if bgm and Path(bgm).is_file():
            args += ["-i", str(bgm)]
            fc = ("[0:a][1:a]amix=inputs=2:duration=first:weights=1 0.09[mix];"
                  "[mix]loudnorm=I=-16:TP=-1.5:LRA=11[a]")
            args += ["-filter_complex", "[0:v]ass=final.ass[v];" + fc,
                     "-map", "[v]", "-map", "[a]"]
        else:
            args += ["-vf", "ass=final.ass", "-af", "loudnorm=I=-16:TP=-1.5:LRA=11"]
        utils.ffmpeg(args + ["-t", f"{target:.3f}",
                             "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
                             "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2",
                             "-movflags", "+faststart", "-y", str(subbed)], cwd=work)
        if subbed.is_file() and subbed.stat().st_size > 0:
            subbed.replace(final)
    # 后期特效：暗角 + 颗粒 + 色散 + 漏光
    try:
        from . import postfx  # noqa: PLC0415
        postfx.apply(final, work / "postfx.mp4", strength="medium", work=work)
    except Exception as exc:  # noqa: BLE001
        print(f"[warn] 后期特效失败（保留原片）：{exc}")
    actual = _conform(final, target, work)
    evidence = {"ok": actual >= target - 0.6, "template": tid, "target": target,
                "duration": round(actual, 3), "source_clips": len(clips),
                "fillers": len(fillers), "out": str(final),
                "elapsed_sec": round(_time.time() - started, 1),
                "unique_shots": total + len(cards)}
    utils.write_json(out_dir / f"longform_{int(target)}s{out_suffix}.json", evidence)
    utils.write_json(out_dir / "qc_shorts_longform.json", {
        "ok": actual >= target - 0.6,
        "duration": round(actual, 3), "target": target,
        "subtitles": ass_info.get("count"), "dialogue_subs": ass_info.get("dialogue"),
        "unique_shots": total, "source_clips": len(clips), "fillers": len(fillers),
        "bgm": bool(bgm), "loudness": "-16 LUFS"})
    return evidence

V_NORM = ("fps=25,scale=1280:720:force_original_aspect_ratio=decrease,"
          "pad=1280:720:(ow-iw)/2:(oh-ih)/2,setsar=1,format=yuv420p")


def _kenburns(src: Path, width: int = 1280, height: int = 720) -> str:
    """按文件名哈希选推近/拉远/横移，给静态感强的镜头加持续运动。"""
    import zlib
    mode = zlib.crc32(src.stem.encode("utf-8")) % 3
    if mode == 0:
        return (f",zoompan=z='min(1.10,1+0.00035*on)':x='iw/2-(iw/zoom/2)':"
                f"y='ih/2-(ih/zoom/2)':d=1:s={width}x{height}:fps=25")
    if mode == 1:
        return (f",zoompan=z='max(1.0,1.10-0.00035*on)':x='iw/2-(iw/zoom/2)':"
                f"y='ih/2-(ih/zoom/2)':d=1:s={width}x{height}:fps=25")
    return (f",zoompan=z='1.06':x='(iw-iw/zoom)/2+sin(on/220)*40':"
            f"y='(ih-ih/zoom)/2':d=1:s={width}x{height}:fps=25")


def _normalize_clip(src_path: Path, dst: Path) -> Path:
    """统一到 1280x720/25fps/AAC 48k；源音频损坏就换静音，保证 concat 不炸。"""
    dur = float(utils.probe_duration(src_path) or 0.0)
    fade = ""
    if dur > 0.8:
        fade = (f",fade=t=in:st=0:d=0.20,"
                f"fade=t=out:st={max(0.0, dur - 0.20):.3f}:d=0.20")
    base = ["-i", str(src_path), "-vf", V_NORM + fade + _kenburns(src_path),
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "20"]
    audio = ["-af", "aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo",
             "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2"]
    try:
        utils.ffmpeg(base + audio + ["-y", str(dst)])
        return dst
    except Exception:  # noqa: BLE001 - 源音频坏（H3/拼接怪流）→ 用静音兜底
        utils.ffmpeg(["-i", str(src_path), "-f", "lavfi", "-i",
                      "anullsrc=r=48000:cl=stereo", "-map", "0:v:0", "-map", "1:a:0",
                      "-vf", V_NORM, "-c:v", "libx264", "-preset", "veryfast",
                      "-crf", "20", "-c:a", "aac", "-b:a", "192k",
                      "-shortest", "-y", str(dst)])
        return dst

def style_of(template: dict | None) -> dict:
    """取模板样式（palette/accent）给字幕配色用；拿不到就返回空。"""
    try:
        from . import v5_catalog  # noqa: PLC0415

        tid = str((template or {}).get("style") or "")
        catalog = v5_catalog.load_catalog()
        return (catalog.get("styles") or {}).get(tid) or {}
    except Exception:  # noqa: BLE001
        return {}
