"""Upload-Post API 客户端（contract/PUBLISHING.md）。

参考 MoneyPrinterTurbo 的 Upload-Post 集成：
- POST /api/upload（multipart），headers: Authorization: Apikey <key>
- 成功可能立即返回 results，也可能转后台；后台需 GET /api/uploadposts/status?request_id= 轮询
- 网络异常/超时/5xx 时结果不确定：返回 request_id 让用户人工确认，**不得自动重试**
"""

from __future__ import annotations

import os
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import httpx

API_BASE = os.environ.get("UPLOAD_POST_API_BASE", "https://api.upload-post.com").rstrip("/")
UPLOAD_TIMEOUT_SECONDS = 300.0
STATUS_POLL_INTERVAL_SECONDS = 10.0
STATUS_TIMEOUT_SECONDS = 3600.0
MAX_CONSECUTIVE_STATUS_ERRORS = 3

SUPPORTED_PLATFORMS = ("youtube", "tiktok", "instagram")


def _post_upload(url: str, headers: Dict[str, str], data: Any, files: Any, timeout: float):
    return httpx.post(url, headers=headers, data=data, files=files, timeout=timeout)


def _get_status(url: str, headers: Dict[str, str], params: Dict[str, str], timeout: float):
    return httpx.get(url, headers=headers, params=params, timeout=timeout)


def _with_platform_outcome(result: Dict[str, Any], expected_platforms: Optional[List[str]] = None) -> Dict[str, Any]:
    """一次成功的 HTTP 请求也可能包含某些平台失败/跳过。"""
    platform_results = result.get("results")
    if isinstance(platform_results, dict):
        entries = list(platform_results.items())
    elif isinstance(platform_results, list):
        entries = [
            (entry.get("platform", "unknown") if isinstance(entry, dict) else "unknown", entry)
            for entry in platform_results
        ]
    else:
        if "results" in result:
            return {**result, "success": False, "error": "Upload-Post returned invalid platform results"}
        return result

    failures = [
        str(platform)
        for platform, entry in entries
        if not isinstance(entry, dict)
        or entry.get("success") is not True
        or entry.get("skipped") is True
    ]
    reported = {platform for platform, _ in entries if isinstance(platform, str)}
    failures.extend(
        f"{platform} (missing result)"
        for platform in dict.fromkeys(expected_platforms or [])
        if platform not in reported
    )
    if not platform_results:
        failures.append("no platform results")
    if failures:
        return {
            **result,
            "success": False,
            "error": "Upload-Post failed or skipped platforms: " + ", ".join(failures),
        }
    return result


def check_status(request_id: str, api_key: str, timeout: float = 30.0) -> Dict[str, Any]:
    """GET /api/uploadposts/status。"""
    headers = {"Authorization": f"Apikey {api_key}"}
    try:
        response = _get_status(
            f"{API_BASE}/api/uploadposts/status",
            headers=headers,
            params={"request_id": request_id},
            timeout=timeout,
        )
        response.raise_for_status()
        try:
            result = response.json()
        except ValueError:
            return {"success": False, "error": "Upload-Post returned invalid status JSON"}
        if not isinstance(result, dict):
            return {"success": False, "error": "Upload-Post returned an invalid status response"}
        return result
    except httpx.HTTPError as exc:
        return {"success": False, "error": str(exc)}


def wait_for_completion(
    request_id: str,
    api_key: str,
    expected_platforms: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """轮询后台任务直到 completed/failed/超时；超时返回 request_id 供人工确认。"""
    consecutive_errors = 0
    deadline = time.monotonic() + STATUS_TIMEOUT_SECONDS
    while True:
        status_result = check_status(request_id, api_key)
        status = status_result.get("status")
        if status == "completed":
            if not isinstance(status_result.get("results"), (dict, list)):
                return {
                    **status_result,
                    "request_id": request_id,
                    "success": False,
                    "error": "Upload-Post completed without platform results",
                }
            return _with_platform_outcome(
                {**status_result, "request_id": request_id, "success": True},
                expected_platforms,
            )
        if status == "failed":
            return {
                **status_result,
                "request_id": request_id,
                "success": False,
                "error": status_result.get("message") or "Upload-Post background upload failed",
            }
        if status in {"pending", "queued", "processing", "in_progress"}:
            consecutive_errors = 0
        else:
            consecutive_errors += 1
            if consecutive_errors >= MAX_CONSECUTIVE_STATUS_ERRORS:
                return {
                    "success": False,
                    "request_id": request_id,
                    "error": "Upload-Post status could not be confirmed; "
                    f"check request_id {request_id}",
                }
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        time.sleep(min(STATUS_POLL_INTERVAL_SECONDS, remaining))
    return {
        "success": False,
        "request_id": request_id,
        "error": "Upload-Post status did not complete within 1 hour; "
        f"check request_id {request_id}",
    }


def upload_video(
    video_path: str | Path,
    title: str,
    platforms: List[str],
    username: str,
    api_key: str,
    description: str = "",
    hashtags: Optional[List[str]] = None,
    privacy: str = "unlisted",
    made_for_kids: bool = False,
    on_background_start: Optional[Callable[[str], None]] = None,
) -> Dict[str, Any]:
    """上传视频到 Upload-Post；失败返回 request_id + error（不确定时提示人工核对）。"""
    video_path = Path(video_path)
    if not (username or "").strip() or not (api_key or "").strip():
        return {"success": False, "error": "Upload-Post not configured (api key / username missing)"}
    if not video_path.is_file():
        return {"success": False, "error": f"Video file not found: {video_path}"}

    request_id = str(uuid.uuid4())
    tags = [str(tag).strip() for tag in (hashtags or []) if str(tag).strip()]

    def unconfirmed(message: str) -> Dict[str, Any]:
        return {
            "success": False,
            "request_id": request_id,
            "error": f"{message}; upload outcome is unconfirmed; "
            f"check request_id {request_id} before submitting again",
        }

    has_youtube = any(platform.startswith("youtube") for platform in platforms)
    data: Dict[str, Any] = {
        "user": username,
        "request_id": request_id,
        "title": (title or "")[:2200],
        "privacy_level": "PUBLIC_TO_EVERYONE",
        "platform[]": list(platforms),
    }
    if has_youtube:
        data["selfDeclaredMadeForKids"] = str(bool(made_for_kids)).lower()
        data["privacyStatus"] = privacy if privacy in ("public", "unlisted", "private") else "unlisted"
        data["youtube_title"] = (title or "")[:100]
        if description:
            data["youtube_description"] = description
        if tags:
            data["tags[]"] = tags
        data["containsSyntheticMedia"] = "true"

    headers = {"Authorization": f"Apikey {api_key}"}
    try:
        with video_path.open("rb") as video_file:
            response = _post_upload(
                f"{API_BASE}/api/upload",
                headers=headers,
                data=data,
                files={"video": (video_path.name, video_file, "video/mp4")},
                timeout=UPLOAD_TIMEOUT_SECONDS,
            )
        if 300 <= response.status_code < 400:
            return unconfirmed("Upload-Post upload returned an unexpected redirect")
        response.raise_for_status()
        try:
            result = response.json()
        except ValueError:
            return unconfirmed("Upload-Post returned invalid JSON")
    except httpx.HTTPStatusError as exc:
        status_code = exc.response.status_code if exc.response is not None else 0
        if status_code >= 500:
            return unconfirmed(f"Upload-Post upload failed: HTTP {status_code}")
        return {"success": False, "error": f"Upload-Post upload rejected: HTTP {status_code}"}
    except (httpx.TimeoutException, httpx.ConnectError) as exc:
        return unconfirmed(f"Upload-Post network error: {exc}")
    except httpx.HTTPError as exc:
        return {"success": False, "request_id": request_id, "error": f"Upload-Post upload failed: {exc}"}

    if not isinstance(result, dict) or not isinstance(result.get("success"), bool):
        return unconfirmed("Upload-Post returned an invalid response")
    result.setdefault("request_id", request_id)
    if result["success"]:
        if "results" not in result:
            remote_request_id = str(result.get("request_id") or "").strip()
            if not remote_request_id:
                return unconfirmed("Upload-Post started a background upload without a request_id")
            if on_background_start is not None:
                try:
                    on_background_start(remote_request_id)
                except Exception:  # noqa: BLE001 - 远端已开始，本地记录失败不能重传
                    pass
            result = wait_for_completion(remote_request_id, api_key, platforms)
        else:
            result = _with_platform_outcome(result, platforms)
    result.setdefault("request_id", request_id)
    return result
