# -*- coding: utf-8 -*-
"""ComfyUI 客户端：轻量复用 integrations/comfyui 的「启动/提交/轮询」逻辑，
只补充 SDXL 图片场景需要的下载与落盘。

复用来源（同一仓库，稳定接口）：
  integrations/comfyui/run_h3.py      -> ensure_server / http_json / http_post_json / wait_prompt / fmt_sec
  integrations/comfyui/workflow_to_api.py -> fetch_object_info
"""
from __future__ import annotations

import os
import shutil
import sys
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(HERE)
COMFY_SCRIPTS = os.path.join(REPO_ROOT, "integrations", "comfyui")
if COMFY_SCRIPTS not in sys.path:
    sys.path.insert(0, COMFY_SCRIPTS)

from run_h3 import (  # noqa: E402  复用服务生命周期与轮询
    ensure_server,
    fmt_sec,
    http_json,
    http_post_json,
    wait_prompt,
)
from workflow_to_api import fetch_object_info  # noqa: E402

DEFAULT_SERVER = os.environ.get("COMFYUI_URL", "http://127.0.0.1:8188")
DEFAULT_COMFY_ROOT = os.environ.get(
    "COMFYUI_ROOT", r"D:\BaiduSyncdisk\wmj_work\XJ\ComfyUI")


# --------------------------------------------------------------------------- #
# 服务 / 模型
# --------------------------------------------------------------------------- #
def ensure_comfy(server: str = DEFAULT_SERVER, comfy_root: str = DEFAULT_COMFY_ROOT,
                 do_start: bool = True, log=print) -> dict:
    return ensure_server(server, comfy_root, do_start, log)


def get_checkpoints(server: str = DEFAULT_SERVER, object_info: dict = None) -> list:
    """CheckpointLoaderSimple 可选的 ckpt 列表（来自 /object_info）。"""
    info = object_info or fetch_object_info(server)
    node = info.get("CheckpointLoaderSimple") or {}
    defn = ((node.get("input") or {}).get("required") or {}).get("ckpt_name") or []
    return list(defn[0]) if defn and isinstance(defn[0], list) else []


def wait_checkpoint(server: str = DEFAULT_SERVER, ckpt: str = None,
                    timeout: float = 0.0, poll: float = 10.0, log=print) -> bool:
    """等待 checkpoint 出现在列表里（下载完成后可能不需要重启）。"""
    import time
    t0 = time.time()
    while True:
        ckpts = get_checkpoints(server)
        if ckpt in ckpts:
            return True
        if timeout and time.time() - t0 > timeout:
            return False
        if not timeout:
            return False
        log("[model] 等待 checkpoint 出现: %s（已等 %s）" % (ckpt, fmt_sec(time.time() - t0)))
        time.sleep(poll)


# --------------------------------------------------------------------------- #
# 提交 / 轮询 / 取图
# --------------------------------------------------------------------------- #
def submit(server: str, prompt: dict, log=print) -> str:
    """POST /prompt，返回 prompt_id；失败抛 RuntimeError 并打印 node_errors。"""
    import uuid
    payload = {"prompt": prompt, "client_id": uuid.uuid4().hex}
    status, body = http_post_json(server.rstrip("/") + "/prompt", payload, timeout=120)
    if status != 200 or "prompt_id" not in body:
        log("[submit] 失败 HTTP %s" % status)
        log(str(body)[:4000])
        raise RuntimeError("ComfyUI 拒绝 prompt (HTTP %s): %s"
                           % (status, str(body)[:1500]))
    pid = body["prompt_id"]
    log("[submit] 已入队 prompt_id=%s" % pid)
    return pid


def fetch_images(rec: dict) -> list:
    """从 history record 里取出所有产出图片项 [{filename, subfolder, type, node, kind}]。"""
    out = []
    for nid, node_out in (rec.get("outputs") or {}).items():
        if not isinstance(node_out, dict):
            continue
        for key, items in node_out.items():
            if not isinstance(items, list):
                continue
            for it in items:
                if isinstance(it, dict) and it.get("filename"):
                    out.append({
                        "filename": it["filename"],
                        "subfolder": it.get("subfolder") or "",
                        "type": it.get("type") or "output",
                        "node": nid,
                        "kind": key,
                    })
    return out


def save_image(server: str, comfy_root: str, item: dict, dest: str, log=print) -> str:
    """把 ComfyUI 产出图落到 dest（优先直接读 output 目录，其次 /view 下载）。"""
    os.makedirs(os.path.dirname(os.path.abspath(dest)), exist_ok=True)
    local = os.path.join(comfy_root, "output", item["subfolder"], item["filename"])
    if item["type"] == "output" and os.path.isfile(local):
        shutil.copyfile(local, dest)
    else:
        url = server.rstrip("/") + "/view?" + urllib.parse.urlencode({
            "filename": item["filename"], "subfolder": item["subfolder"],
            "type": item["type"]})
        with urllib.request.urlopen(url, timeout=300) as r, open(dest, "wb") as f:
            shutil.copyfileobj(r, f)
    log("[image] %s <- %s" % (dest, item["filename"]))
    return dest
