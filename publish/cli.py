"""一键发布 CLI（contract/PUBLISHING.md）。

用法（dry-run 不联网）：
  python -m publish.cli --video out.mp4 --title "标题" --platforms youtube,tiktok,instagram \
      --username myprofile --dry-run
  python -m publish.cli --video out.mp4 --title "标题" --platforms youtube --username myprofile

API Key 只从环境变量 UPLOAD_POST_API_KEY 读取（不写进命令行/日志）。末行输出 JSON：
{ok, dry_run, video, duration, size_mb, platforms, configured, warnings, errors,
 request_id?, results?, error?}
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PLATFORMS = ["youtube", "tiktok", "instagram"]
TIKTOK_MAX_DURATION_SECONDS = 600
MAX_UPLOAD_MB = 500.0
DEFAULT_HASHTAGS = ["#造片局", "#AI微综艺", "#Zaopianju", "#AIVariety", "#AIGenerated"]


class PublishError(Exception):
    pass


def _emit(payload: Dict[str, Any]) -> None:
    print(json.dumps(payload, ensure_ascii=False, default=str))


def norm_platforms(raw: str | List[str]) -> List[str]:
    items: List[str] = []
    if isinstance(raw, str):
        raw = raw.replace(" ", ",")
        items = [part.strip().lower() for part in raw.split(",") if part.strip()]
    else:
        items = [str(part).strip().lower() for part in raw]
    alias = {"instagram": "instagram", "reels": "instagram", "ig": "instagram", "youtube": "youtube", "yt": "youtube", "tiktok": "tiktok"}
    platforms: List[str] = []
    for item in items:
        key = alias.get(item, item)
        if key not in DEFAULT_PLATFORMS:
            raise PublishError(f"不支持的平台: {item}（可选 youtube/tiktok/instagram）")
        if key not in platforms:
            platforms.append(key)
    return platforms


def norm_hashtags(raw: str | List[str]) -> List[str]:
    if isinstance(raw, str):
        parts = [part.strip() for part in raw.replace(",", " ").split() if part.strip()]
    else:
        parts = [str(part).strip() for part in raw if str(part).strip()]
    tags: List[str] = []
    for tag in parts:
        if not tag.startswith("#"):
            tag = "#" + tag
        if tag not in tags:
            tags.append(tag)
    return tags


def _ffprobe_binary() -> Optional[str]:
    candidates = [
        shutil.which("ffprobe"),
        str(REPO_ROOT / "tools" / "node_modules" / "ffprobe-static" / "bin" / "win32" / "x64" / "ffprobe.exe"),
    ]
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return candidate
    return None


def probe_media(path: str | Path) -> Dict[str, Any]:
    """探测时长/分辨率；失败抛 PublishError。"""
    binary = _ffprobe_binary()
    if binary is None:
        raise PublishError("找不到 ffprobe，无法预检视频时长/分辨率")
    try:
        proc = subprocess.run(
            [
                binary,
                "-v",
                "error",
                "-show_entries",
                "format=duration:stream=width,height",
                "-of",
                "json",
                str(path),
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=60,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise PublishError(f"ffprobe 执行失败: {exc}") from exc
    if proc.returncode != 0:
        raise PublishError(f"ffprobe 读取失败: {(proc.stderr or '').strip()[:300]}")
    try:
        data = json.loads(proc.stdout or "{}")
    except json.JSONDecodeError as exc:
        raise PublishError("ffprobe 输出无法解析") from exc
    streams = data.get("streams") or []
    video_stream = next((item for item in streams if item.get("width")), {})
    try:
        duration = float((data.get("format") or {}).get("duration") or 0.0)
    except (TypeError, ValueError):
        duration = 0.0
    return {
        "duration": round(duration, 3),
        "width": int(video_stream.get("width") or 0),
        "height": int(video_stream.get("height") or 0),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Upload-Post 一键发布（YouTube/TikTok/Instagram）")
    parser.add_argument("--video", required=True, help="视频文件路径")
    parser.add_argument("--title", default="造片局 · AI微综艺", help="标题（YouTube ≤100 字符）")
    parser.add_argument("--description", default="", help="描述（YouTube ≤5000）")
    parser.add_argument("--hashtags", default="", help="标签，逗号/空格分隔，可带 #")
    parser.add_argument("--platforms", default=",".join(DEFAULT_PLATFORMS), help="平台，逗号分隔")
    parser.add_argument("--username", default="", help="Upload-Post profile 用户名")
    parser.add_argument("--privacy", default="unlisted", choices=["public", "unlisted", "private"], help="YouTube 可见性")
    parser.add_argument("--youtube-made-for-kids", action="store_true", default=False)
    parser.add_argument("--lang", default="", help="语言版本（元数据展示用）")
    parser.add_argument("--dry-run", action="store_true", default=False)
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    payload: Dict[str, Any] = {
        "ok": False,
        "dry_run": bool(args.dry_run),
        "video": str(Path(args.video).resolve()),
        "duration": 0.0,
        "size_mb": 0.0,
        "platforms": [],
        "configured": False,
        "warnings": [],
        "errors": [],
        "lang": args.lang,
    }
    warnings: List[str] = payload["warnings"]
    errors: List[str] = payload["errors"]
    try:
        platforms = norm_platforms(args.platforms)
        hashtags = norm_hashtags(args.hashtags)
        video_path = Path(args.video)
        if not video_path.is_file():
            raise PublishError(f"视频文件不存在: {video_path}")
        size_bytes = video_path.stat().st_size
        if size_bytes <= 0:
            raise PublishError("视频文件为空")
        payload["size_mb"] = round(size_bytes / (1024 * 1024), 2)
        media = probe_media(video_path)
        payload.update({"duration": media["duration"], "width": media["width"], "height": media["height"]})
        payload["platforms"] = platforms
        if not platforms:
            errors.append("至少选择一个发布平台")
        if media["duration"] <= 0:
            errors.append("无法读取视频时长")
        if media["duration"] > TIKTOK_MAX_DURATION_SECONDS and "tiktok" in platforms:
            errors.append(
                f"TikTok 单视频上限 10 分钟（当前 {media['duration']:.1f}s），请剪短或去掉 TikTok"
            )
        if payload["size_mb"] > MAX_UPLOAD_MB:
            errors.append(f"文件 {payload['size_mb']:.1f}MB 超过上传上限 {MAX_UPLOAD_MB:.0f}MB")
        if "instagram" in platforms and media["width"] > media["height"]:
            warnings.append("横屏 16:9 发 Instagram Reels 会加黑边，建议竖屏或裁切")
        if len(args.title or "") > 100:
            warnings.append("标题超过 100 字符，YouTube 会自动截断")

        api_key = (os.environ.get("UPLOAD_POST_API_KEY") or "").strip()
        payload["configured"] = bool(api_key and (args.username or "").strip())
        if not payload["configured"]:
            message = "未配置 Upload-Post：请在设置中填写 API Key 与 username（upload-post.com）"
            if args.dry_run:
                warnings.append(message)
            else:
                errors.append(message)
        if args.dry_run:
            payload["ok"] = not errors
            payload["hashtags"] = hashtags
            _emit(payload)
            return 0 if payload["ok"] else 2
        if errors:
            payload["error"] = "；".join(errors)
            _emit(payload)
            return 2

        from . import upload_post

        result = upload_post.upload_video(
            video_path,
            args.title,
            platforms,
            args.username,
            api_key,
            description=args.description,
            hashtags=hashtags,
            privacy=args.privacy,
            made_for_kids=bool(args.youtube_made_for_kids),
        )
        payload["ok"] = bool(result.get("success"))
        payload["request_id"] = result.get("request_id")
        payload["results"] = result.get("results")
        if result.get("error"):
            payload["error"] = str(result.get("error"))
        payload["hashtags"] = hashtags
        _emit(payload)
        return 0 if payload["ok"] else 1
    except PublishError as exc:
        payload["ok"] = False
        payload["error"] = str(exc)
        _emit(payload)
        return 2
    except Exception as exc:  # noqa: BLE001 - CLI 必须给末行 JSON
        payload["ok"] = False
        payload["error"] = f"发布失败: {exc}"
        _emit(payload)
        return 1


if __name__ == "__main__":
    sys.exit(main())
