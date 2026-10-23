"""ComfyUI（MiniMax H3）视频生成适配器。

流程（与 ComfyUI HTTP API 一致）：
1. POST {base}/prompt             提交 API-format 工作流，拿 prompt_id
2. GET  {base}/history/{prompt_id} 轮询直到 outputs 就绪
3. GET  {base}/view?filename=...  下载生成的视频字节

网络访问全部通过 httpx，可注入 httpx.Client（单元测试用 MockTransport），
因此本模块不需要真实 ComfyUI 也可验证。demo 默认走 mock 模式，不实际调用。
"""

from __future__ import annotations

import json
import logging
import re
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import httpx

logger = logging.getLogger(__name__)

VIDEO_EXTENSIONS = (".mp4", ".webm", ".mov", ".mkv", ".gif")
_PLACEHOLDER = re.compile(r"\{\{\s*([A-Za-z0-9_.-]+)\s*\}\}")


class ComfyUIError(RuntimeError):
    """ComfyUI 交互失败。"""


def _substitute(value: Any, values: Dict[str, Any]) -> Any:
    if isinstance(value, dict):
        return {key: _substitute(item, values) for key, item in value.items()}
    if isinstance(value, list):
        return [_substitute(item, values) for item in value]
    if isinstance(value, str):
        exact = _PLACEHOLDER.fullmatch(value)
        if exact:
            key = exact.group(1)
            return values.get(key, value)
        return _PLACEHOLDER.sub(
            lambda match: str(values.get(match.group(1), match.group(0))),
            value,
        )
    return value


def build_workflow(template: Any, values: Optional[Dict[str, Any]] = None) -> Any:
    """把 {{key}} 占位符替换为 values 中的值（保持 JSON 类型）。"""
    return _substitute(template, values or {})


def load_workflow_template(template: str | Path, base_dir: str | Path | None = None) -> Any:
    """加载工作流模板：支持 JSON 字符串、绝对路径或相对仓库根/指定目录的 .json。"""
    if isinstance(template, Path):
        return json.loads(template.read_text(encoding="utf-8"))
    text = str(template or "").strip()
    if not text:
        raise ComfyUIError("workflow_template 为空")
    if text.startswith("{") or text.startswith("["):
        return json.loads(text)
    candidates: List[Path] = []
    raw = Path(text)
    if raw.is_absolute():
        candidates.append(raw)
    else:
        if base_dir is not None:
            candidates.append(Path(base_dir) / raw)
        from . import config

        candidates.extend(
            [
                config.REPO_ROOT / raw,
                config.CONTRACT_DIR / raw,
                config.DATA_DIR / raw,
                Path.cwd() / raw,
            ]
        )
    for path in candidates:
        if path.is_file():
            return json.loads(path.read_text(encoding="utf-8"))
    raise ComfyUIError(f"找不到工作流模板文件: {text}")


def pick_video_file(outputs: Dict[str, Any]) -> Tuple[str, str, str]:
    """从 history outputs 中挑选视频文件，返回 (filename, subfolder, type)。"""
    candidates: List[Dict[str, Any]] = []
    for node_output in (outputs or {}).values():
        if not isinstance(node_output, dict):
            continue
        for key in ("videos", "gifs", "video", "images", "output"):
            files = node_output.get(key)
            if isinstance(files, dict):
                files = [files]
            if isinstance(files, list):
                candidates.extend(item for item in files if isinstance(item, dict))
    if not candidates:
        raise ComfyUIError("ComfyUI history 中没有输出文件")
    videos = [
        item
        for item in candidates
        if str(item.get("filename", "")).lower().endswith(VIDEO_EXTENSIONS)
    ]
    chosen = (videos or candidates)[0]
    return (
        str(chosen.get("filename") or ""),
        str(chosen.get("subfolder") or ""),
        str(chosen.get("type") or "output"),
    )


class ComfyUIVideoClient:
    """提交工作流并取回视频字节。"""

    def __init__(
        self,
        base_url: str,
        client: Optional[httpx.Client] = None,
        poll_interval: float = 1.0,
        timeout: float = 600.0,
        http_timeout: float = 30.0,
    ) -> None:
        self.base_url = (base_url or "").rstrip("/")
        self.poll_interval = max(0.01, float(poll_interval))
        self.timeout = float(timeout)
        self._client = client
        self._owns_client = client is None
        self._http_timeout = http_timeout

    # ---- httpx ----
    @property
    def client(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(timeout=self._http_timeout)
        return self._client

    def close(self) -> None:
        if self._client is not None and self._owns_client:
            self._client.close()

    def __enter__(self) -> "ComfyUIVideoClient":
        return self

    def __exit__(self, *exc_info: Any) -> None:
        self.close()

    def _url(self, path: str) -> str:
        return f"{self.base_url}/{path.lstrip('/')}"

    # ---- 单步 API ----
    def submit_prompt(self, workflow: Dict[str, Any], client_id: Optional[str] = None) -> str:
        payload = {"prompt": workflow, "client_id": client_id or uuid.uuid4().hex}
        response = self.client.post(self._url("/prompt"), json=payload)
        response.raise_for_status()
        data = response.json() or {}
        if data.get("error"):
            raise ComfyUIError(f"提交工作流失败: {data.get('error')}")
        prompt_id = data.get("prompt_id") or data.get("promptId")
        if not prompt_id:
            raise ComfyUIError(f"提交工作流未返回 prompt_id: {data}")
        return str(prompt_id)

    def get_history(self, prompt_id: str) -> Dict[str, Any]:
        response = self.client.get(self._url(f"/history/{prompt_id}"))
        response.raise_for_status()
        return response.json() or {}

    def wait_for_history(self, prompt_id: str) -> Dict[str, Any]:
        """轮询 history 直到该 prompt 出现；超时抛 ComfyUIError。"""
        deadline = time.monotonic() + self.timeout
        while True:
            history = self.get_history(prompt_id)
            entry = history.get(prompt_id)
            if entry:
                status = entry.get("status") or {}
                if status.get("status_str") == "error":
                    raise ComfyUIError(f"ComfyUI 执行失败: {status}")
                if entry.get("outputs"):
                    return entry
            if time.monotonic() >= deadline:
                raise ComfyUIError(f"等待 ComfyUI 结果超时（{self.timeout:.0f}s）: {prompt_id}")
            time.sleep(min(self.poll_interval, max(0.0, deadline - time.monotonic())))

    def download_file(
        self,
        filename: str,
        subfolder: str = "",
        folder_type: str = "output",
    ) -> bytes:
        response = self.client.get(
            self._url("/view"),
            params={"filename": filename, "subfolder": subfolder, "type": folder_type},
        )
        response.raise_for_status()
        return response.content

    # ---- 组合调用 ----
    def generate_video(
        self,
        workflow: Dict[str, Any],
        values: Optional[Dict[str, Any]] = None,
    ) -> bytes:
        prompt_id = self.submit_prompt(build_workflow(workflow, values))
        entry = self.wait_for_history(prompt_id)
        filename, subfolder, folder_type = pick_video_file(entry.get("outputs") or {})
        logger.info("ComfyUI 生成完成 prompt_id=%s file=%s", prompt_id, filename)
        return self.download_file(filename, subfolder, folder_type)

    def generate_from_template(self, template: str | Path, values: Dict[str, Any]) -> bytes:
        workflow = load_workflow_template(template)
        return self.generate_video(workflow, values)
