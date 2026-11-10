"""Upload-Post API 客户端（contract/PUBLISHING.md）。

参考 MoneyPrinterTurbo 的 Upload-Post 集成：
- POST /api/upload（multipart），headers: Authorization: Apikey <key>
- 成功可能立即返回 results，也可能转后台；后台需 GET /api/uploadposts/status?request_id= 轮询
- 网络异常/超时/5xx 时结果不确定：返回 request_id 让用户人工确认，**不得自动重试**
"""

from __future__ import annotations

import json
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

# Upload-Post 的 `privacy_level` 只认这几种枚举值（TikTok / Instagram 用）。
# 审查 P1-18：这里曾硬编码 `PUBLIC_TO_EVERYONE`，等于 **TikTok/IG 永远公开**，
# 而 `--privacy unlisted` 只写进了 youtube 的 privacyStatus —— 全仓唯一不可撤回的外部副作用。
PRIVACY_LEVELS: Dict[str, str] = {
    "public": "PUBLIC_TO_EVERYONE",       # 公开
    "private": "SELF_ONLY",               # 仅自己可见
    "unlisted": "MUTUAL_FOLLOW_FRIENDS",  # 仅互关可见（与 YouTube unlisted 不等价）
}
DEFAULT_PRIVACY = "unlisted"
# 只有显式打开（参数或环境变量）才允许把 unlisted 映射到 MUTUAL_FOLLOW_FRIENDS 发出去。
# 用环境变量是为了不改 publish/cli.py（子进程继承 env 即可透传）。
ALLOW_PUBLIC_FALLBACK_ENV = "UPLOAD_POST_ALLOW_PUBLIC_FALLBACK"


class PrivacyRejected(ValueError):
    """privacy 无法安全映射到某些平台时拒发；``platforms`` 是拒绝涉及的平台。"""

    def __init__(self, message: str, platforms: List[str]):
        super().__init__(message)
        self.platforms = list(platforms)


def _normalize_privacy(privacy: Optional[str]) -> str:
    """未知/空值一律按 unlisted（最保守），与 cli/API 的白名单保持一致。"""
    key = str(privacy or "").strip().lower()
    return key if key in PRIVACY_LEVELS else DEFAULT_PRIVACY


def _allow_public_fallback(flag: Optional[bool] = None) -> bool:
    """显式参数优先；None 时读 ``UPLOAD_POST_ALLOW_PUBLIC_FALLBACK``（backend/app/jobs.py 透传）。"""
    if isinstance(flag, bool):
        return flag
    raw = str(os.environ.get(ALLOW_PUBLIC_FALLBACK_ENV) or "").strip().lower()
    return raw in ("1", "true", "yes", "y", "on")


def _privacy_levels(
    platforms: List[str],
    privacy: str,
    allow_public_fallback: Optional[bool] = None,
) -> Dict[str, str]:
    """每个平台**实际会发送**的隐私档位；需要拒发时抛 :class:`PrivacyRejected`。

    - ``youtube`` 走 YouTube 自己的 ``privacyStatus``（public/unlisted/private），
      所以回显的也是这三个值本身，便于台账核对"记录写 unlisted，实际发的是什么"；
    - 其它平台（tiktok/instagram）走 ``privacy_level``，映射见 :data:`PRIVACY_LEVELS`；
    - ``privacy="unlisted"`` + 非 youtube 平台：默认**拒发**（MUTUAL_FOLLOW_FRIENDS 是
      "仅互关可见"，不是"不公开"）。调用方显式传 ``allow_public_fallback=True``
      （或置 ``UPLOAD_POST_ALLOW_PUBLIC_FALLBACK=1``）才放行。
    """
    key = _normalize_privacy(privacy)
    level = PRIVACY_LEVELS[key]
    names = [str(p or "").strip().lower() for p in (platforms or [])]
    names = [p for p in names if p]
    if not names:
        raise PrivacyRejected("没有可发布的平台（platforms 为空）", [])
    risky = [p for p in names if not p.startswith("youtube")]
    if key == "unlisted" and risky and not _allow_public_fallback(allow_public_fallback):
        raise PrivacyRejected(
            "privacy=unlisted 无法安全映射到 "
            + ", ".join(risky)
            + "：Upload-Post 对 TikTok/Instagram 只有 MUTUAL_FOLLOW_FRIENDS（仅互关可见），"
            "与「不公开」不等价；默认拒发以免内容被公开。请改 privacy=private，"
            "或只发 youtube，或显式传 allow_public_fallback=True 接受该可见范围",
            risky,
        )
    return {p: (key if p.startswith("youtube") else level) for p in names}


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


def _response_error_text(response: Any) -> str:
    """从 Upload-Post 错误响应里提取可读原因（绝不包含请求头/Key）。

    该接口 4xx 时会返回可操作 JSON，例如：
    {"success": false, "message": "None of the requested platforms are valid for profile ...",
     "invalid_platforms": {"youtube": "Profile default has no Youtube account configured"}}
    优先取 message，再拼 invalid_platforms 的简要说明，让用户知道去 upload-post 后台连接账号。
    """
    if response is None:
        return ""
    try:
        payload = response.json()
    except ValueError:
        text = str(getattr(response, "text", "") or "").strip()
        return text[:400]
    if not isinstance(payload, dict):
        return ""
    parts: List[str] = []
    message = str(payload.get("message") or payload.get("error") or "").strip()
    if message:
        parts.append(message)
    invalid = payload.get("invalid_platforms")
    if isinstance(invalid, dict) and invalid:
        bits: List[str] = []
        for platform, reason in list(invalid.items())[:5]:
            reason_text = str(reason).strip()
            bits.append(f"{platform}: {reason_text}" if reason_text else str(platform))
        parts.append("invalid_platforms: " + "; ".join(bits))
    elif isinstance(invalid, list) and invalid:
        parts.append("invalid_platforms: " + ", ".join(str(item) for item in invalid[:5]))
    if parts:
        return "；".join(parts)[:600]
    try:
        return json.dumps(payload, ensure_ascii=False)[:400]
    except (TypeError, ValueError):
        return ""


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
    allow_public_fallback: Optional[bool] = None,
) -> Dict[str, Any]:
    """上传视频到 Upload-Post；失败返回 request_id + error（不确定时提示人工核对）。"""
    video_path = Path(video_path)
    if not (username or "").strip() or not (api_key or "").strip():
        return {"success": False, "error": "Upload-Post not configured (api key / username missing)"}
    if not video_path.is_file():
        return {"success": False, "error": f"Video file not found: {video_path}"}

    # P1-18：先把"每个平台实际用哪个隐私档位"算清楚；算不出来（unlisted + TikTok/IG
    # 且未显式放行）就在这里直接拒发，一张字节都不上传 —— 这是不可撤回的外部副作用。
    try:
        privacy_levels = _privacy_levels(platforms, privacy, allow_public_fallback)
    except PrivacyRejected as exc:
        return {
            "success": False,
            "error": str(exc),
            "privacy_levels": {},
            "blocked_platforms": list(exc.platforms),
            "privacy": _normalize_privacy(privacy),
        }

    request_id = str(uuid.uuid4())
    tags = [str(tag).strip() for tag in (hashtags or []) if str(tag).strip()]

    def unconfirmed(message: str) -> Dict[str, Any]:
        return {
            "success": False,
            "request_id": request_id,
            "error": f"{message}; upload outcome is unconfirmed; "
            f"check request_id {request_id} before submitting again",
            "privacy_levels": privacy_levels,
        }

    has_youtube = any(platform.startswith("youtube") for platform in platforms)
    privacy_key = _normalize_privacy(privacy)
    data: Dict[str, Any] = {
        "user": username,
        "request_id": request_id,
        "title": (title or "")[:2200],
        # 只有 TikTok/Instagram 认这个字段；值按 privacy 映射，绝不硬编码 PUBLIC_TO_EVERYONE
        "privacy_level": PRIVACY_LEVELS[privacy_key],
        "platform[]": list(platforms),
    }
    if has_youtube:
        data["selfDeclaredMadeForKids"] = str(bool(made_for_kids)).lower()
        data["privacyStatus"] = privacy_key if privacy_key in ("public", "unlisted", "private") else "unlisted"
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
        response = exc.response
        status_code = response.status_code if response is not None else 0
        detail = _response_error_text(response)
        if status_code >= 500:
            suffix = f"：{detail}" if detail else ""
            return unconfirmed(f"Upload-Post upload failed: HTTP {status_code}{suffix}")
        if detail:
            # 4xx：把服务端可操作原因带出来（如某平台账号未连接），仍是「已明确拒绝」，不发 request_id
            return {
                "success": False,
                "error": f"Upload-Post 拒绝（HTTP {status_code}）：{detail}",
            }
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
    # 台账回显：每个平台**实际发送**的隐私档位（youtube 是 privacyStatus 的取值）。
    # 记录里写的是用户意图（privacy=unlisted/public/private），这里才是实际生效值。
    result["privacy"] = privacy_key
    result["privacy_levels"] = privacy_levels
    return result
