"""ComfyUI API 适配器（MiniMax H3 workflow，API 格式）。

demo 阶段不联网实测；实现真实 HTTP 协议，便于用 mock server 做单元测试：
  1) POST {base}/prompt  {"prompt": <api workflow>, "client_id": ...}
  2) 轮询 GET {base}/history/{prompt_id} 直到 outputs 出现
  3) 下载 GET {base}/view?filename=... 得到视频
  4) ffmpeg 统一到 1280x720 / 25fps / 镜头时长
"""
from __future__ import annotations

import json
import logging
import time
import uuid
from pathlib import Path
from typing import Any

from . import config, utils

LOG = logging.getLogger("pipeline")

POLL_INTERVAL = 1.5
MAX_WAIT = 900


def _find_video_output(outputs: dict) -> dict | None:
    for node in (outputs or {}).values():
        for key in ("videos", "gifs", "images"):
            for item in node.get(key) or []:
                name = str(item.get("filename") or "")
                if name.lower().endswith((".mp4", ".webm", ".mov", ".gif", ".webp")):
                    return item
    return None


def _inject_prompt(workflow: Any, positive: str, negative: str = "") -> Any:
    """把台词/画面描述注入 API workflow 的文本节点（尽力而为）。"""
    data = json.loads(json.dumps(workflow))
    nodes = data if isinstance(data, dict) else {}
    used = 0
    for node in nodes.values():
        if not isinstance(node, dict):
            continue
        if node.get("class_type") in ("CLIPTextEncode", "TextEncodeQwenImageEdit",
                                      "CLIPTextEncodeSDXL"):
            inputs = node.setdefault("inputs", {})
            if isinstance(inputs.get("text"), str):
                if used == 0:
                    inputs["text"] = positive
                elif used == 1 and negative:
                    inputs["text"] = negative
                used += 1
    return data


def generate_comfyui_clip(spec: dict, out_path) -> dict:
    """按 contract shot_spec.json 的 comfyui 模式生成片段。"""
    try:
        import httpx
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("comfyui 模式需要 httpx") from exc

    spec = spec or {}
    settings = spec.get("settings") or {}
    base = str(settings.get("comfyui_url") or spec.get("comfyui_url") or "").rstrip("/")
    workflow = settings.get("workflow_template") or spec.get("workflow_template")
    if not base or not workflow:
        raise RuntimeError("comfyui 模式缺少 settings.comfyui_url / workflow_template")
    if isinstance(workflow, str):
        workflow = json.loads(workflow)

    shot = spec.get("shot") or {}
    positive = str(shot.get("line") or shot.get("visual") or "AI 微综艺 演播厅 暖黄灯")
    negative = str(settings.get("negative_prompt") or "")
    prompt = _inject_prompt(workflow, positive, negative)
    client_id = uuid.uuid4().hex

    timeout = httpx.Timeout(60.0, connect=15.0)
    with httpx.Client(timeout=timeout, follow_redirects=True) as client:
        resp = client.post(f"{base}/prompt", json={"prompt": prompt, "client_id": client_id})
        resp.raise_for_status()
        prompt_id = resp.json().get("prompt_id")
        if not prompt_id:
            raise RuntimeError("ComfyUI 未返回 prompt_id")
        deadline = time.time() + MAX_WAIT
        item = None
        while time.time() < deadline:
            hist = client.get(f"{base}/history/{prompt_id}")
            if hist.status_code == 200:
                data = hist.json().get(prompt_id) or {}
                status = data.get("status") or {}
                if status.get("status_str") == "error" or status.get("completed") is False:
                    if status.get("status_str") == "error":
                        raise RuntimeError(f"ComfyUI 任务失败: {status}")
                item = _find_video_output(data.get("outputs") or {})
                if item:
                    break
            time.sleep(POLL_INTERVAL)
        if not item:
            raise RuntimeError("ComfyUI 等待超时或没有视频输出")
        params = {
            "filename": item.get("filename"),
            "subfolder": item.get("subfolder") or "",
            "type": item.get("type") or "output",
        }
        video = client.get(f"{base}/view", params=params)
        video.raise_for_status()

    out = Path(out_path)
    utils.ensure_dir(out.parent)
    raw = out.with_suffix(".comfy_raw.bin")
    raw.write_bytes(video.content)
    duration = float(shot.get("duration") or 5.0)
    w, h = utils.parse_resolution((spec.get("episode") or {}).get("resolution"))
    fps = int((spec.get("episode") or {}).get("fps") or config.FPS)
    utils.ffmpeg([
        "-i", str(raw),
        "-vf", f"scale={w}:{h}:force_original_aspect_ratio=decrease,"
               f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps={fps}",
        "-t", f"{duration:.3f}", "-an",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
        "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(out),
    ])
    try:
        raw.unlink()
    except OSError:
        pass
    return {
        "ok": True,
        "path": str(out.resolve()),
        "duration": round(utils.probe_duration(out), 3),
        "provider": "comfyui",
        "prompt_id": prompt_id,
    }
