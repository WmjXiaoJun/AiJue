"""剪映专业版草稿导出（pipeline.capcut）。

使用 pyJianYingDraft (>=0.3.1) 生成剪映/CapCut 草稿，并在其基础上补齐
draft_meta_info.json、draft_settings、SRT、README 等交付物。

    python -m pipeline.capcut export --manifest <export_manifest.json> \
        --outdir <dir> [--name 造片局_EP001] [--copy-media]

    # 从 pipeline 渲染工作目录自动生成 manifest（后端可直接调函数）
    python -m pipeline.capcut build-manifest --episode <episode.json> \
        --workdir <render_workdir> --out <manifest.json>

产物（{outdir}/{name}/）：
    draft_content.json / draft_meta_info.json      # 剪映草稿（库生成 + 字段补全）
    draft_settings / draft_virtual_store.json / timeline_layout.json
    materials/（--copy-media 时复制媒体）
    subtitles.srt / export_manifest.json / export_audit.json / README.txt

说明：本机剪映 10.3.0.13901 会把草稿 JSON 加密保存，pyJianYingDraft 面向
明文草稿（v5 风格）；导出草稿可直接供支持明文草稿的剪映版本使用，10.x
的导入限制见 README.txt。
"""
from __future__ import annotations

import argparse
import json
import logging
import re
import shutil
import sys
import time
import uuid
from pathlib import Path

from . import __version__, config, utils

LOG = logging.getLogger("pipeline")

SEC = 1_000_000                      # 剪映内部时间单位：微秒
DEFAULT_WIDTH = 1920
DEFAULT_HEIGHT = 1080
DEFAULT_FPS = 30
PYJIANYING_HINT = "pip install pyJianYingDraft==0.3.1"


# --------------------------------------------------------------------------
# 基础工具
# --------------------------------------------------------------------------
def _new_id() -> str:
    return str(uuid.uuid4()).upper()


def _now_us() -> int:
    return int(time.time() * SEC)


def _safe_name(name: str) -> str:
    text = re.sub(r'[\\/:*?"<>|\r\n\t]', "_", str(name or "draft")).strip(" .")
    return text or "draft"


def _load_json(path: Path | str):
    data = utils.read_json(Path(path))
    if data is None:
        raise RuntimeError(f"无法读取/解析 JSON: {path}")
    return data


def _media_info(path: Path | str) -> dict:
    try:
        info = utils.probe_video(path)
        return {"width": int(info.get("width") or 0),
                "height": int(info.get("height") or 0),
                "duration": float(info.get("duration") or 0.0)}
    except Exception as exc:  # noqa: BLE001
        LOG.debug("读取媒体信息失败 %s: %s", path, exc)
        return {"width": 0, "height": 0, "duration": 0.0}


# --------------------------------------------------------------------------
# Manifest
# --------------------------------------------------------------------------
def normalize_manifest(raw: dict, outdir: Path | str | None = None) -> dict:
    """把 contract/EXPORT.md 的 manifest 规整成内部结构（路径转绝对路径）。"""
    if not isinstance(raw, dict):
        raise RuntimeError("manifest 必须是 JSON 对象")
    name = _safe_name(raw.get("name") or "造片局_EP001")
    width = int(raw.get("width") or DEFAULT_WIDTH)
    height = int(raw.get("height") or DEFAULT_HEIGHT)
    fps = float(raw.get("fps") or DEFAULT_FPS)

    def abs_path(text: str) -> str:
        p = Path(str(text))
        return str(p if p.is_absolute() else p.resolve())

    videos = []
    for item in raw.get("video") or []:
        path = str(item.get("path") or "").strip()
        if not path:
            continue
        videos.append({
            "path": abs_path(path),
            "start": float(item.get("start") or 0.0),
            "duration": float(item.get("duration") or 0.0),
            "type": str(item.get("type") or "main"),
        })
    audios = []
    for item in raw.get("audio") or []:
        path = str(item.get("path") or "").strip()
        if not path:
            continue
        audios.append({
            "path": abs_path(path),
            "start": float(item.get("start") or 0.0),
            "duration": float(item.get("duration") or 0.0),
            "volume": float(item.get("volume") if item.get("volume") is not None else 1.0),
        })
    subtitles = []
    for item in raw.get("subtitles") or []:
        text = str(item.get("text") or "").strip()
        if not text:
            continue
        start = max(0.0, float(item.get("start") or 0.0))
        end = max(start + 0.1, float(item.get("end") or 0.0))
        subtitles.append({"start": round(start, 3), "end": round(end, 3),
                          "text": text})
    subtitles.sort(key=lambda x: (x["start"], x["end"]))

    total = float(raw.get("duration") or 0.0)
    if total <= 0:
        ends = [v["start"] + v["duration"] for v in videos] + \
               [a["start"] + a["duration"] for a in audios] + \
               [s["end"] for s in subtitles]
        total = max(ends) if ends else 0.0

    return {
        "name": name,
        "width": width,
        "height": height,
        "fps": fps,
        "duration": round(total, 3),
        "video": videos,
        "audio": audios,
        "subtitles": subtitles,
        "outdir": str(Path(outdir).resolve()) if outdir else "",
    }


def build_manifest_from_workdir(episode_path: Path | str, workdir: Path | str,
                                name: str | None = None,
                                subtitles_path: Path | str | None = None) -> dict:
    """从 episode.json + pipeline 渲染工作目录自动生成导出 manifest。

    - 视频：workdir/clips/{shot_id}.mp4 全部存在时按镜头铺时间线，否则用 workdir/final.mp4 单段；
    - 音频：workdir/audio/narration.wav；
    - 字幕：显式 subtitles_path > workdir/final.ass 解析 > 空。
    """
    episode_path = Path(episode_path).resolve()
    workdir = Path(workdir).resolve()
    data = _load_json(episode_path)
    episode = data.get("episode") or {}
    shots = [s for s in data.get("shots") or [] if isinstance(s, dict)]
    width, height = utils.parse_resolution(episode.get("resolution"))
    fps = int(episode.get("fps") or config.FPS)
    ep_tag = str(episode.get("id") or "ep_001")
    if ep_tag.lower().startswith("ep_"):
        ep_tag = "EP" + ep_tag[3:]
    draft_name = _safe_name(name or f"造片局_{ep_tag}")

    videos: list[dict] = []
    cursor = 0.0
    clips_dir = workdir / "clips"
    if shots and all((clips_dir / f"{s.get('id')}.mp4").is_file() for s in shots):
        for shot in shots:
            dur = float(shot.get("duration") or 0.0)
            videos.append({"path": str(clips_dir / f"{shot.get('id')}.mp4"),
                           "start": round(cursor, 3), "duration": dur,
                           "type": "shot"})
            cursor += dur
    else:
        final = workdir / "final.mp4"
        if not final.is_file():
            raise RuntimeError(f"工作目录里既没有完整 clips/ 也没有 {final}")
        info = _media_info(final)
        total = float(episode.get("target_duration") or info.get("duration") or 0.0)
        videos.append({"path": str(final), "start": 0.0,
                       "duration": round(total, 3), "type": "main"})
        cursor = total

    audios: list[dict] = []
    narration = workdir / "audio" / "narration.wav"
    if narration.is_file():
        audios.append({"path": str(narration), "start": 0.0,
                       "duration": round(cursor, 3), "volume": 0.9})

    if subtitles_path:
        subtitles = load_subtitles(subtitles_path)
    elif (workdir / "final.ass").is_file():
        subtitles = subtitles_from_ass(workdir / "final.ass")
    else:
        subtitles = []

    return normalize_manifest({
        "name": draft_name,
        "width": width,
        "height": height,
        "fps": fps,
        "duration": round(cursor, 3),
        "video": videos,
        "audio": audios,
        "subtitles": subtitles,
    })


# --------------------------------------------------------------------------
# 字幕：ASS 解析 / SRT 读写
# --------------------------------------------------------------------------
_ASS_TAG_RE = re.compile(r"\{[^}]*\}")
_ASS_TIME_RE = re.compile(r"^(\d+):(\d{2}):(\d{2})[.:](\d{1,3})$")


def _ass_seconds(text: str) -> float:
    m = _ASS_TIME_RE.match(str(text).strip())
    if not m:
        return 0.0
    h, mnt, sec, frac = m.groups()
    return int(h) * 3600 + int(mnt) * 60 + int(sec) + int(frac.ljust(3, "0")) / 1000.0


def _ass_clean(text: str) -> str:
    text = _ASS_TAG_RE.sub("", str(text))
    text = text.replace("\\N", " ").replace("\\n", " ")
    return re.sub(r"\s+", " ", text).strip()


def subtitles_from_ass(path: Path | str) -> list[dict]:
    """解析 ASS Dialogue 行 -> [{start,end,text}]。"""
    out: list[dict] = []
    p = Path(path)
    try:
        lines = p.read_text(encoding="utf-8-sig", errors="replace").splitlines()
    except OSError:
        return out
    in_events = False
    for line in lines:
        stripped = line.strip()
        if stripped.lower() == "[events]":
            in_events = True
            continue
        if not in_events or not stripped.lower().startswith("dialogue:"):
            continue
        parts = stripped.split(",", 9)
        if len(parts) < 10:
            continue
        # 只取字幕事件；跳过花字/人名条/台标/章节标等包装文本
        style = parts[3].strip()
        if style in ("Punch", "Lower", "Brand", "Chapter"):
            continue
        start = _ass_seconds(parts[1])
        end = _ass_seconds(parts[2])
        text = _ass_clean(parts[9])
        if text and end > start:
            out.append({"start": round(start, 3), "end": round(end, 3), "text": text})
    return out


def _srt_time(seconds: float) -> str:
    ms = max(0, int(round(float(seconds) * 1000)))
    h, rem = divmod(ms, 3600000)
    m, rem = divmod(rem, 60000)
    s, ms = divmod(rem, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def build_srt(subtitles: list[dict]) -> str:
    lines: list[str] = []
    for idx, item in enumerate(subtitles or [], 1):
        lines.append(str(idx))
        lines.append(f"{_srt_time(item['start'])} --> {_srt_time(item['end'])}")
        lines.append(str(item.get("text") or ""))
        lines.append("")
    return "\n".join(lines).rstrip() + "\n" if lines else ""


def load_subtitles(path: Path | str) -> list[dict]:
    """读取 SRT（也接受 JSON 数组）。"""
    p = Path(path)
    if p.suffix.lower() == ".json":
        data = _load_json(p)
        return [{"start": float(i.get("start", 0)), "end": float(i.get("end", 0)),
                 "text": str(i.get("text") or "")} for i in data if isinstance(i, dict)]
    out: list[dict] = []
    text = p.read_text(encoding="utf-8-sig", errors="replace")
    for block in re.split(r"\r?\n\r?\n", text):
        lines = [ln for ln in block.splitlines() if ln.strip() != ""]
        if len(lines) < 2:
            continue
        timing = next((ln for ln in lines if "-->" in ln), None)
        if not timing:
            continue
        start_txt, _, end_txt = timing.partition("-->")
        body = "\n".join(lines[lines.index(timing) + 1:]).strip()

        def to_sec(value: str) -> float:
            value = value.strip().replace(",", ".")
            try:
                h, m, s = value.split(":")
                return int(h) * 3600 + int(m) * 60 + float(s)
            except ValueError:
                return 0.0
        out.append({"start": round(to_sec(start_txt), 3),
                    "end": round(to_sec(end_txt), 3), "text": body})
    return out


# --------------------------------------------------------------------------
# 剪映草稿（pyJianYingDraft）
# --------------------------------------------------------------------------
def _require_pyjianying():
    try:
        import pyJianYingDraft  # noqa: F401
    except ImportError as exc:
        raise RuntimeError(
            "缺少依赖 pyJianYingDraft，请先安装：" + PYJIANYING_HINT
        ) from exc


def _copy_material(src: Path, draft_dir: Path, prefix: str) -> Path:
    materials_dir = utils.ensure_dir(draft_dir / "materials")
    dst = materials_dir / f"{prefix}_{src.name}"
    if not dst.exists() or dst.stat().st_size != src.stat().st_size:
        shutil.copyfile(src, dst)
    return dst


def _patch_meta_info(draft_dir: Path, manifest: dict, materials_size: int) -> dict:
    """补全库生成的 draft_meta_info.json（模板字段为空）。"""
    meta_path = draft_dir / "draft_meta_info.json"
    meta = utils.read_json(meta_path) or {}
    now = _now_us()
    meta.update({
        "draft_name": manifest["name"],
        "draft_fold_path": str(draft_dir).replace("\\", "/"),
        "draft_root_path": str(draft_dir.parent).replace("\\", "/"),
        "draft_json_file": str((draft_dir / "draft_content.json")).replace("\\", "/"),
        "draft_id": _new_id(),
        "tm_draft_create": now,
        "tm_draft_modified": now,
        "tm_draft_removed": 0,
        "tm_duration": int(round(float(manifest["duration"]) * SEC)),
        "draft_type": "",
        "draft_timeline_materials_size": int(materials_size),
    })
    utils.write_json(meta_path, meta)
    return meta


def build_draft_settings() -> str:
    now = int(time.time())
    return ("[General]\r\n"
            f"draft_create_time={now}\r\n"
            f"draft_last_edit_time={now}\r\n"
            "real_edit_seconds=0\r\n"
            "real_edit_keys=1\r\n")


def build_virtual_store() -> dict:
    return {
        "draft_materials": [],
        "draft_virtual_store": [
            {"type": 0, "value": []},
            {"type": 1, "value": []},
            {"type": 2, "value": []},
        ],
    }


def build_timeline_layout(timeline_id: str) -> dict:
    return {
        "dockItems": [{"dockIndex": 0, "ratio": 1,
                       "timelineIds": [timeline_id],
                       "timelineNames": ["时间线01"]}],
        "layoutOrientation": 1,
    }


def build_readme(draft_name: str, draft_dir: Path, manifest: dict,
                 audit: dict) -> str:
    lines = [
        f"剪映专业版草稿：{draft_name}",
        "=" * 48,
        "",
        f"草稿目录：{draft_dir}",
        f"画布：{manifest['width']}x{manifest['height']} @ {manifest['fps']}fps",
        f"时长：{manifest['duration']:.3f}s",
        f"轨道：视频 {len(manifest['video'])} / 音频 {len(manifest['audio'])} / 字幕 {len(manifest['subtitles'])}",
        "",
        "导入方法：",
        "1）把整个草稿文件夹复制到剪映草稿目录（通常为",
        "   %LOCALAPPDATA%\\JianyingPro\\User Data\\Projects\\com.lveditor.draft\\ ），",
        "   重启剪映专业版后在「草稿」列表查看；",
        "2）或用剪映的「导入草稿」功能选择该文件夹/对应压缩包（视版本支持）；",
        "3）若导出时未加 --copy-media，请保持素材原路径可用（草稿里是绝对路径）。",
        "",
        "兼容性说明：",
        "- 本导出器基于 pyJianYingDraft 0.3.1 生成明文草稿（draft_content.json /",
        "  draft_meta_info.json），可直接被支持明文草稿的剪映版本读取；",
        "- 本机剪映 10.3.0.13901 会把草稿 JSON 加密保存（本机现有样例不是明文 JSON），",
        "  因此 10.x 可能无法直接识别明文草稿；建议用 ≤5.9 版本导入，或参考",
        "  materials/ 与 subtitles.srt 手动重建。",
        "- subtitles.srt 也可在剪映「文本 - 识别字幕/导入字幕」或其它剪辑软件直接使用。",
        "",
        f"自检结果：{'通过' if audit.get('ok') else '失败'}",
        f"  画布：{audit.get('canvas')}",
        f"  轨道：{audit.get('tracks')}",
        f"  素材：{audit.get('materials')}",
    ]
    if audit.get("errors"):
        lines.append("  错误：" + "; ".join(audit["errors"]))
    lines.append("")
    return "\n".join(lines)


def validate_draft(draft_dir: Path | str) -> dict:
    """自检：JSON 可解析 + 关键字段/轨道/素材路径齐全。"""
    root = Path(draft_dir)
    errors: list[str] = []
    content = utils.read_json(root / "draft_content.json")
    meta = utils.read_json(root / "draft_meta_info.json")
    if not isinstance(content, dict):
        errors.append("draft_content.json 不可解析")
        content = {}
    if not isinstance(meta, dict):
        errors.append("draft_meta_info.json 不可解析")
        meta = {}
    canvas = content.get("canvas_config") or {}
    for key in ("width", "height", "ratio"):
        if key not in canvas:
            errors.append(f"canvas_config 缺字段 {key}")
    if not canvas.get("width") or not canvas.get("height"):
        errors.append("canvas_config 宽高为 0")
    if int(content.get("duration") or 0) <= 0:
        errors.append("duration <= 0")
    tracks = content.get("tracks") or []
    track_types = [str(t.get("type")) for t in tracks]
    if "video" not in track_types:
        errors.append("缺少视频轨")
    materials = content.get("materials") or {}
    # 视频必须有；音频 / 字幕按 manifest 可选（只有 manifest 里给了才要求有）
    if not materials.get("videos"):
        errors.append("materials.videos 为空")
    warnings: list[str] = []
    for key in ("audios", "texts"):
        if not materials.get(key):
            warnings.append(f"materials.{key} 为空（manifest 未提供对应素材）")
    for seg in [s for t in tracks for s in (t.get("segments") or [])]:
        tr = seg.get("target_timerange") or {}
        if int(tr.get("duration") or 0) <= 0:
            errors.append("segment target_timerange.duration <= 0")
    # 媒体路径存在性
    for key in ("videos", "audios"):
        for item in materials.get(key) or []:
            raw = str(item.get("path") or "")
            if not raw:
                errors.append(f"materials.{key} 条目缺少 path")
                continue
            path = Path(raw)
            if not path.is_absolute():
                path = root / path
            if not path.is_file():
                errors.append(f"{key} 素材缺失: {raw}")
    for key in ("draft_name", "draft_fold_path", "tm_duration"):
        if not meta.get(key):
            errors.append(f"draft_meta_info 缺字段 {key}")
    for name in ("draft_content.json", "draft_meta_info.json", "draft_settings",
                 "draft_virtual_store.json", "timeline_layout.json",
                 "subtitles.srt", "export_manifest.json", "README.txt"):
        if not (root / name).is_file():
            errors.append(f"缺少产物 {name}")
    return {
        "ok": not errors,
        "draft_dir": str(root),
        "errors": errors,
        "warnings": warnings,
        "canvas": {"width": canvas.get("width"), "height": canvas.get("height"),
                   "ratio": canvas.get("ratio")},
        "duration": content.get("duration"),
        "fps": content.get("fps"),
        "tracks": track_types,
        "materials": {key: len(materials.get(key) or [])
                      for key in ("videos", "audios", "texts")},
        "meta_name": meta.get("draft_name"),
        "meta_duration": meta.get("tm_duration"),
    }


def export_draft(manifest: dict, outdir: Path | str, name: str | None = None,
                 copy_media: bool = False) -> dict:
    """把 manifest 导出为剪映草稿目录，返回摘要 dict。"""
    _require_pyjianying()
    from pyJianYingDraft import (DraftFolder, TrackSpec, TrackType,
                                 VideoSegment, AudioSegment, trange)

    manifest = normalize_manifest(manifest, outdir)
    if name:
        manifest["name"] = _safe_name(name)
    draft_name = manifest["name"]
    t0 = time.time()
    outroot = utils.ensure_dir(Path(outdir).resolve())

    # ---- 先用 pyJianYingDraft 创建草稿 ----
    # 注意：create_draft(allow_replace=True) 会创建/清空草稿目录，
    # 因此必须先建草稿、拿到最终 draft_dir，再复制媒体，否则 materials/ 会被清掉。
    folder = DraftFolder(str(outroot))
    script = folder.create_draft(draft_name, manifest["width"],
                                 manifest["height"],
                                 int(round(manifest["fps"])),
                                 allow_replace=True)
    draft_dir = Path(script.save_path).parent
    utils.ensure_dir(draft_dir / "materials")

    # ---- 媒体准备（--copy-media 时复制进最终草稿目录 materials/）----
    prepared_video = []
    for item in manifest["video"]:
        src = Path(item["path"])
        if not src.is_file():
            raise RuntimeError(f"视频素材不存在: {src}")
        final_path = _copy_material(src, draft_dir, "video") if copy_media else src
        prepared_video.append({"path": str(final_path),
                               "start": item["start"],
                               "duration": item["duration"],
                               "type": item["type"]})
    prepared_audio = []
    for item in manifest["audio"]:
        src = Path(item["path"])
        if not src.is_file():
            raise RuntimeError(f"音频素材不存在: {src}")
        final_path = _copy_material(src, draft_dir, "audio") if copy_media else src
        prepared_audio.append({"path": str(final_path),
                               "start": item["start"],
                               "duration": item["duration"],
                               "volume": item["volume"]})
    manifest = dict(manifest, video=prepared_video, audio=prepared_audio)

    if manifest["video"]:
        script.append_track(TrackSpec(TrackType.video, "主轨"))
    if manifest["audio"]:
        script.append_track(TrackSpec(TrackType.audio, "旁白"))

    for item in manifest["video"]:
        meta = _media_info(item["path"])
        dur = float(item["duration"] or meta.get("duration") or 0.0)
        if dur <= 0:
            raise RuntimeError(f"视频素材时长为 0: {item['path']}")
        segment = VideoSegment(item["path"],
                               trange(int(round(item["start"] * SEC)),
                                      int(round(dur * SEC))))
        script.add_segment(segment, "主轨")
    for item in manifest["audio"]:
        meta = _media_info(item["path"])
        dur = float(item["duration"] or meta.get("duration") or 0.0)
        if dur <= 0:
            raise RuntimeError(f"音频素材时长为 0: {item['path']}")
        segment = AudioSegment(item["path"],
                               trange(int(round(item["start"] * SEC)),
                                      int(round(dur * SEC))),
                               volume=float(item.get("volume") or 1.0))
        script.add_segment(segment, "旁白")

    # ---- 字幕：写 SRT 后由库导入为文本轨 ----
    srt_text = build_srt(manifest["subtitles"])
    srt_path = draft_dir / "subtitles.srt"
    utils.write_text(srt_path, srt_text or "WEBVTT\n".replace("WEBVTT\n", ""))
    if manifest["subtitles"]:
        script.import_srt(str(srt_path), "字幕")

    script.save()
    LOG.info("pyJianYingDraft 草稿已保存: %s", script.save_path)
    content = utils.read_json(draft_dir / "draft_content.json") or {}
    # 画布比例按 16:9 写入（库默认 original），便于剪映识别画布尺寸
    canvas = content.get("canvas_config")
    if isinstance(canvas, dict):
        canvas["ratio"] = "16:9"
        units = {"name": manifest["name"], "duration": content.get("duration")}
        utils.write_json(draft_dir / "draft_content.json", content)

    # ---- 补全 meta + 额外产物 ----
    materials_size = 0
    for item in manifest["video"] + manifest["audio"]:
        try:
            materials_size += Path(item["path"]).stat().st_size
        except OSError:
            pass
    meta = _patch_meta_info(draft_dir, manifest, materials_size)

    timeline_id = str(content.get("id") or "")
    utils.write_json(draft_dir / "draft_virtual_store.json", build_virtual_store())
    utils.write_json(draft_dir / "timeline_layout.json",
                     build_timeline_layout(timeline_id))
    utils.write_text(draft_dir / "draft_settings", build_draft_settings())

    export_manifest = dict(manifest)
    export_manifest["outdir"] = str(draft_dir)
    utils.write_json(draft_dir / "export_manifest.json", export_manifest)

    # README 先写，再整体自检（自检会检查 README 等产物是否齐全）
    draft_readme = build_readme(draft_name, draft_dir, manifest,
                                {"ok": True, "canvas": {}, "tracks": [],
                                 "materials": {}})
    utils.write_text(draft_dir / "README.txt", draft_readme)
    audit = validate_draft(draft_dir)
    utils.write_json(draft_dir / "export_audit.json", audit)
    utils.write_text(draft_dir / "README.txt",
                     build_readme(draft_name, draft_dir, manifest, audit))

    size_bytes = sum(p.stat().st_size for p in draft_dir.rglob("*") if p.is_file())
    summary = {
        "ok": bool(audit.get("ok")),
        "provider": "jianying-capcut",
        "engine": "pyJianYingDraft",
        "draft_dir": str(draft_dir),
        "draft_content": str(draft_dir / "draft_content.json"),
        "name": draft_name,
        "duration": manifest["duration"],
        "resolution": f"{manifest['width']}x{manifest['height']}",
        "fps": manifest["fps"],
        "tracks": {"video": len(manifest["video"]),
                   "audio": len(manifest["audio"]),
                   "text": len(manifest["subtitles"])},
        "copy_media": bool(copy_media),
        "materials_size": materials_size,
        "size_bytes": size_bytes,
        "elapsed_sec": round(time.time() - t0, 2),
        "audit": audit,
        "version": __version__,
    }
    LOG.info("剪映草稿导出完成: %s（%d 视频 / %d 音频 / %d 字幕）",
             draft_dir, len(manifest["video"]), len(manifest["audio"]),
             len(manifest["subtitles"]))
    return summary


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------
def _force_utf8_stdio() -> None:
    for name in ("stdout", "stderr"):
        stream = getattr(sys, name, None)
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass


def cmd_export(args: argparse.Namespace) -> int:
    manifest_path = Path(args.manifest).resolve()
    raw = utils.read_json(manifest_path)
    if not isinstance(raw, dict):
        utils.emit_json_line({"ok": False, "error": f"无法读取 manifest: {manifest_path}"})
        return 1
    try:
        summary = export_draft(raw, args.outdir, name=args.name,
                               copy_media=bool(args.copy_media))
        utils.emit_json_line(summary)
        return 0 if summary.get("ok") else 1
    except Exception as exc:  # noqa: BLE001
        LOG.error("导出失败: %s", exc)
        LOG.debug("%s", __import__("traceback").format_exc())
        utils.emit_json_line({"ok": False, "error": str(exc)})
        return 1


def cmd_build_manifest(args: argparse.Namespace) -> int:
    try:
        manifest = build_manifest_from_workdir(args.episode, args.workdir,
                                               name=args.name,
                                               subtitles_path=args.subtitles)
        if args.out:
            utils.write_json(args.out, manifest)
        utils.emit_json_line({"ok": True, "manifest": manifest,
                              "path": str(Path(args.out).resolve()) if args.out else ""})
        return 0
    except Exception as exc:  # noqa: BLE001
        LOG.error("生成 manifest 失败: %s", exc)
        utils.emit_json_line({"ok": False, "error": str(exc)})
        return 1


def cmd_validate(args: argparse.Namespace) -> int:
    audit = validate_draft(args.draft_dir)
    utils.emit_json_line(audit)
    return 0 if audit.get("ok") else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m pipeline.capcut",
        description="造片局 · 剪映专业版草稿导出（pipeline.capcut / pyJianYingDraft）",
    )
    parser.add_argument("--version", action="version", version=__version__)
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("export", help="把 manifest 导出为剪映草稿")
    p.add_argument("--manifest", required=True, help="export_manifest.json")
    p.add_argument("--outdir", required=True, help="输出根目录")
    p.add_argument("--name", default=None, help="草稿名（默认用 manifest.name）")
    p.add_argument("--copy-media", action="store_true", help="复制媒体到 materials/")
    p.set_defaults(func=cmd_export)

    p2 = sub.add_parser("build-manifest", help="从 episode + 渲染工作目录生成 manifest")
    p2.add_argument("--episode", required=True, help="episode.json")
    p2.add_argument("--workdir", required=True, help="pipeline render 的 workdir")
    p2.add_argument("--out", default=None, help="manifest 输出路径")
    p2.add_argument("--name", default=None)
    p2.add_argument("--subtitles", default=None, help="可选 SRT/JSON 字幕文件")
    p2.set_defaults(func=cmd_build_manifest)

    p3 = sub.add_parser("validate", help="校验已导出的草稿目录")
    p3.add_argument("--draft-dir", required=True)
    p3.set_defaults(func=cmd_validate)
    return parser


def main(argv: list[str] | None = None) -> int:
    _force_utf8_stdio()
    parser = build_parser()
    args = parser.parse_args(argv)
    utils.setup_logging(bool(getattr(args, "verbose", False)))
    try:
        return int(args.func(args))
    except KeyboardInterrupt:
        return 130
    except Exception as exc:  # noqa: BLE001
        LOG.error("执行失败: %s", exc)
        utils.emit_json_line({"ok": False, "error": str(exc)})
        return 1


if __name__ == "__main__":
    sys.exit(main())
