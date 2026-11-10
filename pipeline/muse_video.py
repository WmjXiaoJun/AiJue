# -*- coding: utf-8 -*-
"""V50 · Muse 视频生成模型接入层（provider 适配器）。

支持三类「Muse」来源，同一套签名：
1. musev_local    —— 本地开源 MuseV（TME Lyralab），文本/图片 → 视频
2. musepose_local —— 本地开源 MusePose（姿态驱动），参考图 + 姿态序列 → 视频
3. muse_cloud     —— 任意云端 Muse 视频接口（HTTP，环境变量配置）
4. stub           —— 只用于接线自测（生成纯色片，产物带 stub=true 标记）

CLI:
    python -m pipeline.muse_video --check
    python -m pipeline.muse_video --prompt "..." --duration 4 --out tmp/muse/test.mp4
    $env:MUSE_API_URL="https://..."; $env:MUSE_API_KEY="..."; python -m pipeline.muse_video --check

Python:
    available() -> dict
    generate_video(spec: dict, prog=None) -> dict
"""
from __future__ import annotations

import base64
import json
import os
import re
import shlex
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
from . import config  # noqa: E402
MUSE_ROOT = REPO / "local_models" / "muse"
OUT_ROOT = config.OUTPUTS_ROOT / "muse_video"
FFMPEG = REPO / "tools" / "node_modules" / "ffmpeg-static" / "ffmpeg.exe"
FFPROBE = REPO / "tools" / "node_modules" / "ffprobe-static" / "bin" / "win32" / "x64" / "ffprobe.exe"
DOCS = "docs/MUSE_VIDEO_INTEGRATION.md"


def _env(name: str, default: str = "") -> str:
    return str(os.environ.get(name, default) or "").strip()


def minimax_config() -> dict:
    return {
        "key": _env("MINIMAX_API_KEY"),
        "group": _env("MINIMAX_GROUP_ID"),
        "model": _env("MINIMAX_MODEL", "MiniMax-Hailuo-02"),
        "base": _env("MINIMAX_BASE", "https://api.minimaxi.com"),
        "poll": float(_env("MINIMAX_POLL", "8") or 8),
        "timeout": float(_env("MINIMAX_TIMEOUT", "1800") or 1800),
    }


def cloud_config() -> dict:
    return {
        "url": _env("MUSE_API_URL"),
        "key": _env("MUSE_API_KEY"),
        "model": _env("MUSE_MODEL", "muse"),
        "poll_url": _env("MUSE_POLL_URL"),
        "timeout": float(_env("MUSE_TIMEOUT", "900") or 900),
    }


def _local_cmd(provider: str) -> str:
    key = "MUSEV_CMD" if provider == "musev_local" else "MUSEPOSE_CMD"
    return _env(key)


def available() -> dict:
    """探测各类 provider：**按真实配置判定** ok/provider，并给出 checks 明细。

    P1-8（AUDIT_render.md）：旧实现 `order` 里 apimart_muse/metaso_h3/minimax_cloud/stub
    都落在 `else: provider = name; break`，所以**第一次循环就命中 apimart_muse**，
    清空所有配置后依然 `ok=True, provider="apimart_muse"`；`musev_local` 装好也永不被选中，
    `stub` 分支不可达。现在：
    - 每个 provider 的 `configured` 由必需 env/文件决定（`missing` 列出缺什么）；
    - `ok = any(configured)`，`provider` = 按优先级第一个 configured 的；都没有 → ""；
    - 调用方需要 stub 自测时显式传 `provider="stub"`（`stub_available` 单独标出）。
    """
    cloud = cloud_config()
    rep = replicate_config()
    uapi = useapi_config()
    apimart = apimart_config()
    metaso = metaso_config()
    minimax = minimax_config()
    musev_cmd = _local_cmd("musev_local")
    musepose_cmd = _local_cmd("musepose_local")
    musev_installed = (MUSE_ROOT / "MuseV").is_dir()
    musev_weights = (MUSE_ROOT / "MuseV" / "pretrained_weights").is_dir()
    musepose_installed = (MUSE_ROOT / "MusePose").is_dir()
    musepose_weights = (MUSE_ROOT / "MusePose" / "pretrained_weights").is_dir()
    musepose_dwpose = (REPO / "local_models" / "lipsync_models" / "dwpose" /
                       "dw-ll_ucoco_384.pth").is_file()

    def _local(installed: bool, weights: bool, cmd: str, extra_ok: bool = True,
               extra: str = "") -> dict:
        missing = []
        if not installed:
            missing.append("代码目录")
        if not weights and not cmd:
            missing.append("权重或 *_CMD")
        if not extra_ok:
            missing.append(extra)
        return {"configured": bool(installed and (weights or cmd) and extra_ok),
                "installed": installed, "weights": weights,
                "cmd_env": bool(cmd), "missing": missing}

    checks = {
        "musev_local": {**_local(musev_installed, musev_weights, musev_cmd),
                        "vram_hint_gb": 20},
        "musepose_local": {**_local(musepose_installed, musepose_weights, musepose_cmd,
                                    musepose_dwpose, "dwpose 权重"),
                           "dwpose": musepose_dwpose, "vram_hint_gb": 16},
        "muse_cloud": {"configured": bool(cloud["url"]), "url": cloud["url"],
                       "has_key": bool(cloud["key"]), "model": cloud["model"],
                       "missing": [] if cloud["url"] else ["MUSE_API_URL"]},
        "replicate": {"configured": bool(rep["token"]), "model": rep["model"],
                      "base": rep["base"],
                      "missing": [] if rep["token"] else ["REPLICATE_API_TOKEN"],
                      "note": "Replicate 托管 MuseV/MuseTalk；注册 https://replicate.com/signin"},
        "useapi_muse": {"configured": bool(uapi["token"] and uapi["account"]),
                        "account": uapi["account"], "url": uapi["url"],
                        "missing": [n for n, v in (("USEAPI_TOKEN", uapi["token"]),
                                                   ("USEAPI_ACCOUNT", uapi["account"]))
                                    if not v],
                        "note": "Meta Muse 官方无公开 API，第三方代理 useapi.net（需 Muse 订阅）"},
        "apimart_muse": {"configured": bool(apimart["key"]), "model": apimart["model"],
                         "base": apimart["base"],
                         "missing": [] if apimart["key"] else ["APIMART_API_KEY"],
                         "note": "APIMart 网关（Muse Video Ext）；注册 https://apimart.ai/zh/keys"},
        "metaso_h3": {"configured": bool(metaso["key"]), "has_key": bool(metaso["key"]),
                      "base": metaso["base"], "model": metaso["model"],
                      "missing": [] if metaso["key"] else ["METASO_API_KEY"],
                      "note": "秘塔 H3（OpenAI 兼容 /v1/videos，model=MiniMax-H3 或 sora-2）"},
        "minimax_cloud": {"configured": bool(minimax["key"]),
                          "has_key": bool(minimax["key"]), "model": minimax["model"],
                          "base": minimax["base"],
                          "missing": [] if minimax["key"] else ["MINIMAX_API_KEY"],
                          "note": "MiniMax 海螺（Hailuo）在线视频：platform.minimaxi.com 开通取 key"},
        # stub 永远「可用」但不是「已配置的模型」：不计入 ok，只有显式请求才用它
        "stub": {"configured": False, "installed": True, "available": True,
                 "missing": [],
                 "note": "仅接线自测（纯色占位片），产物标记 stub=true，不可当交付"},
    }
    order = ["musev_local", "musepose_local", "replicate", "useapi_muse",
             "apimart_muse", "metaso_h3", "minimax_cloud", "muse_cloud"]
    configured = [name for name in order if checks[name].get("configured")]
    # provider 回落 stub（让调用方走 skipped/400 分支），但 ok=False 明说这不是真能力
    provider = configured[0] if configured else "stub"
    ok = bool(configured)
    hint = ""
    if not ok:
        hint = ("本地未安装 MuseV/MusePose，也没有任何可用的云端 provider；"
                f"接线见 {DOCS}（自测可显式传 provider=stub / allow_stub=true）")
    return {"ok": ok, "provider": provider, "stub_provider": "stub",
            "configured_providers": configured, "checks": checks,
            "docs": DOCS, "root": str(MUSE_ROOT), "hint": hint}


def _probe(path: Path) -> dict:
    try:
        raw = subprocess.run([str(FFPROBE), "-v", "error", "-show_entries",
                              "format=duration,size", "-show_entries",
                              "stream=codec_type,width,height,r_frame_rate",
                              "-of", "json", str(path)],
                             capture_output=True, text=True, timeout=60).stdout
        return json.loads(raw or "{}")
    except Exception:  # noqa: BLE001
        return {}


def _stub_video(spec: dict, out: Path, prog=None) -> dict:
    duration = float(spec.get("duration") or 3.0)
    aspect = str(spec.get("aspect") or "16:9")
    size = {"16:9": "1280x720", "9:16": "720x1280", "1:1": "720x720"}.get(aspect, "1280x720")
    color = str(spec.get("color") or "0x101820")
    if prog:
        prog("stub", f"生成占位片 {size} {duration}s（仅用于接线自测）")
    subprocess.run([str(FFMPEG), "-y", "-hide_banner", "-loglevel", "error",
                    "-f", "lavfi", "-i", f"color=c={color}:s={size}:d={duration}:r=25",
                    "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo",
                    "-shortest", "-c:v", "libx264", "-pix_fmt", "yuv420p",
                    "-c:a", "aac", str(out)], check=True, timeout=300)
    return {"ok": True, "provider": "stub", "stub": True, "out": str(out),
            "bytes": out.stat().st_size, "probe": _probe(out)}


def _pick_url(obj: dict) -> str:
    for path in (("video_url",), ("url",), ("output",), ("data", "video_url"),
                 ("data", "url"), ("result", "video_url"), ("result", "url")):
        cur = obj
        ok = True
        for key in path:
            if isinstance(cur, dict) and key in cur:
                cur = cur[key]
            else:
                ok = False
                break
        if ok and isinstance(cur, str) and cur.startswith("http"):
            return cur
    return ""


def _cloud_generate(spec: dict, out: Path, cfg: dict, prog=None) -> dict:
    if not cfg["url"]:
        raise RuntimeError(f"未配置 MUSE_API_URL（接法见 {DOCS}）")
    body = {
        "model": spec.get("model") or cfg["model"],
        "prompt": str(spec.get("prompt") or ""),
        "duration": float(spec.get("duration") or 4.0),
        "aspect": str(spec.get("aspect") or "16:9"),
        "fps": int(spec.get("fps") or 25),
        "seed": int(spec.get("seed") or 20261105),
    }
    image = spec.get("image")
    if image and Path(str(image)).is_file():
        body["image_b64"] = base64.b64encode(Path(str(image)).read_bytes()).decode("ascii")
    if spec.get("pose"):
        body["pose"] = spec["pose"]
    if prog:
        prog("cloud", f"POST {cfg['url']}（provider=muse_cloud, model={body['model']}）")
    resp = _http_json(cfg["url"], body, cfg["key"], timeout=min(cfg["timeout"], 120))
    url = _pick_url(resp)
    task_id = resp.get("task_id") or resp.get("id") or (resp.get("data") or {}).get("task_id")
    waited = 0.0
    while not url and (task_id or resp.get("status") in ("queued", "running", "processing")):
        poll = cfg["poll_url"] or (cfg["url"].rstrip("/") + f"/{task_id}")
        time.sleep(3)
        waited += 3
        if prog:
            prog("cloud", f"轮询 {poll}（{waited:.0f}s）")
        if waited > cfg["timeout"]:
            raise RuntimeError(f"Muse 云端任务超时（>{cfg['timeout']:.0f}s）")
        resp = _http_json(poll, None, cfg["key"], timeout=60)
        url = _pick_url(resp)
        task_id = task_id or resp.get("task_id") or resp.get("id")
    if not url:
        raise RuntimeError(f"Muse 云端未返回视频地址: {json.dumps(resp, ensure_ascii=False)[:300]}")
    if prog:
        prog("cloud", "下载产物")
    urllib.request.urlretrieve(url, out)
    return {"ok": True, "provider": "muse_cloud", "out": str(out),
            "bytes": out.stat().st_size, "remote_url": url,
            "response": {k: resp.get(k) for k in ("status", "task_id", "id", "model")},
            "probe": _probe(out)}


def _http_json(url: str, body, key: str, timeout: float = 120, method: str = "") -> dict:
    data = None if body is None else json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, method=method or ("POST" if data else "GET"))
    req.add_header("Content-Type", "application/json")
    if key:
        req.add_header("Authorization", f"Bearer {key}")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            detail = exc.read().decode("utf-8", "replace")[:400]
        except Exception:  # noqa: BLE001
            pass
        raise RuntimeError(f"HTTP {exc.code}: {detail}") from exc


def _minimax_generate(spec: dict, out: Path, cfg: dict, prog=None) -> dict:
    """MiniMax 海螺（Hailuo）在线视频：创建任务 → 轮询 → 下载。"""
    if not cfg["key"]:
        raise RuntimeError("未配置 MINIMAX_API_KEY；到 https://platform.minimaxi.com 开通海螺并生成 Key")
    prompt = str(spec.get("prompt") or "").strip()
    if not prompt:
        raise RuntimeError("MiniMax 生成需要 prompt")
    body = {
        "model": cfg["model"],
        "prompt": prompt,
        "duration": int(float(spec.get("duration") or 6)),
        "resolution": str(spec.get("resolution") or "1080P"),
    }
    if spec.get("image"):
        body["first_frame_image"] = str(spec["image"])
    if prog:
        prog("minimax", f"创建任务 model={cfg['model']} duration={body['duration']}s")
    created = _http_json(f"{cfg['base'].rstrip('/')}/v1/video_generation", body, cfg["key"])
    task_id = str(created.get("task_id") or (created.get("data") or {}).get("task_id") or "")
    if not task_id:
        raise RuntimeError(f"MiniMax 未返回 task_id: {json.dumps(created, ensure_ascii=False)[:300]}")
    waited = 0.0
    file_id = ""
    while waited <= cfg["timeout"]:
        time.sleep(cfg["poll"])
        waited += cfg["poll"]
        info = _http_json(f"{cfg['base'].rstrip('/')}/v1/query/video_generation?task_id={task_id}",
                          None, cfg["key"], timeout=60)
        status = str(info.get("status") or (info.get("data") or {}).get("status") or "")
        file_id = str(info.get("file_id") or (info.get("data") or {}).get("file_id") or "")
        if prog:
            prog("minimax", f"{status or 'running'}（{waited:.0f}s）")
        if status in ("Success", "success", "SUCCESS") and file_id:
            break
        if status in ("Fail", "failed", "Failed", "FAIL"):
            raise RuntimeError(f"MiniMax 任务失败: {json.dumps(info, ensure_ascii=False)[:300]}")
    if not file_id:
        raise RuntimeError(f"MiniMax 超时（>{cfg['timeout']:.0f}s）")
    files = _http_json(f"{cfg['base'].rstrip('/')}/v1/files/retrieve?file_id={file_id}",
                       None, cfg["key"], timeout=60)
    url = ((files.get("file") or {}).get("download_url")
           or (files.get("data") or {}).get("download_url") or "")
    if not url:
        raise RuntimeError(f"MiniMax 未返回下载地址: {json.dumps(files, ensure_ascii=False)[:300]}")
    if prog:
        prog("minimax", "下载产物")
    urllib.request.urlretrieve(url, out)
    return {"ok": True, "provider": "minimax_cloud", "out": str(out),
            "bytes": out.stat().st_size, "task_id": task_id, "remote_url": url,
            "probe": _probe(out)}


def _env_file(path: Path) -> dict:
    data = {}
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, val = line.split("=", 1)
            data[key.strip()] = val.strip().strip('"').strip("'")
    except OSError:
        pass
    return data


def metaso_config() -> dict:
    """秘塔（Metaso）OpenAI 兼容视频：https://metaso.cn/api/openai/v1/videos。"""
    file_env = _env_file(REPO / "backend" / "data" / "metaso.env")
    return {
        "base": _env("METASO_OPENAI_BASE", "https://metaso.cn/api/openai").rstrip("/"),
        "key": (_env("METASO_H3_KEY") or _env("METASO_API_KEY")
                or file_env.get("METASO_H3_KEY", "") or file_env.get("METASO_API_KEY", "")),
        "model": _env("METASO_H3_MODEL", "MiniMax-H3"),
        "seconds": int(float(_env("METASO_H3_SECONDS", "4") or 4)),
        "poll": float(_env("METASO_H3_POLL", "6") or 6),
        "timeout": float(_env("METASO_H3_TIMEOUT", "1800") or 1800),
    }


def _metaso_generate(spec: dict, out: Path, cfg: dict, prog=None) -> dict:
    if not cfg["key"]:
        raise RuntimeError("未配置 METASO_API_KEY（Metaso → API 密钥）")
    prompt = str(spec.get("prompt") or "").strip()
    if not prompt:
        raise RuntimeError("Metaso 生成需要 prompt")
    seconds = int(float(spec.get("seconds") or cfg["seconds"]))
    size = str(spec.get("size") or spec.get("resolution") or "1280x720")
    if size.upper().startswith("1080"):
        size = "1920x1080"
    body = {"model": str(spec.get("model") or cfg["model"]), "prompt": prompt,
            "seconds": seconds, "size": size}
    if spec.get("image"):
        body["input_reference"] = {"image_url": str(spec["image"])}
    if prog:
        prog("metaso", f"POST {cfg['base']}/v1/videos model={body['model']} {seconds}s {size}")
    created = _http_json(f"{cfg['base']}/v1/videos", body, cfg["key"], timeout=120)
    vid = str(created.get("id") or "")
    if not vid:
        raise RuntimeError(f"Metaso 未返回任务 id: {json.dumps(created, ensure_ascii=False)[:300]}")
    waited = 0.0
    info = created
    while waited <= cfg["timeout"]:
        status = str(info.get("status") or "")
        if status in ("completed", "succeeded", "success", "Succeeded"):
            break
        if status in ("failed", "error", "cancelled"):
            raise RuntimeError(f"Metaso 任务失败: {json.dumps(info, ensure_ascii=False)[:300]}")
        time.sleep(cfg["poll"])
        waited += cfg["poll"]
        info = _http_json(f"{cfg['base']}/v1/videos/{vid}", None, cfg["key"], timeout=60)
        if prog:
            prog("metaso", f"{info.get('status') or 'running'}（{waited:.0f}s）")
    url = ""
    for key in ("url", "video_url", "output"):
        val = info.get(key)
        if isinstance(val, str) and val.startswith("http"):
            url = val
            break
    if not url:
        url = f"{cfg['base']}/v1/videos/{vid}/content"
    if prog:
        prog("metaso", "下载产物")
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {cfg['key']}"})
    with urllib.request.urlopen(req, timeout=300) as resp:
        out.write_bytes(resp.read())
    return {"ok": True, "provider": "metaso_h3", "out": str(out),
            "bytes": out.stat().st_size, "task_id": vid, "probe": _probe(out)}


def apimart_config() -> dict:
    """APIMart（Muse Video Ext 等）OpenAI 兼容视频网关。"""
    file_env = _env_file(REPO / "backend" / "data" / "muse.env")
    return {
        "base": _env("APIMART_BASE", "https://api.apimart.ai").rstrip("/"),
        "key": (_env("APIMART_API_KEY") or file_env.get("APIMART_API_KEY", "")),
        "model": _env("APIMART_MUSE_MODEL", "muse-video-ext"),
        "path": _env("APIMART_VIDEO_PATH", "/v1/videos/generations"),
        "poll": float(_env("APIMART_POLL", "6") or 6),
        "timeout": float(_env("APIMART_TIMEOUT", "1800") or 1800),
    }


def _as_image_ref(value: str) -> str:
    """把图片入参规范成 APIMart 能吃的形态：http(s) URL / data URI 原样返回，本地文件转 base64 data URI。"""
    v = str(value or "").strip()
    if v.lower().startswith(("http://", "https://", "data:")):
        return v
    p = Path(v)
    if not p.is_file():
        raise RuntimeError(f"参考图不存在: {v}")
    import base64
    mime = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
            ".webp": "image/webp"}.get(p.suffix.lower(), "image/png")
    data = base64.b64encode(p.read_bytes()).decode("ascii")
    return f"data:{mime};base64,{data}"


def _apimart_generate(spec: dict, out: Path, cfg: dict, prog=None) -> dict:
    """APIMart（Muse Video Ext）：POST /v1/videos/generations → GET /v1/tasks/{id} → 下载。

    响应形态：{"code":200,"data":[{"status":"submitted","task_id":"task_..."}]}
    查询：   {"code":200,"data":{"status":"completed","result":{"videos":[{"url":["..."]}]}}}
    """
    if not cfg["key"]:
        raise RuntimeError("未配置 APIMART_API_KEY（https://apimart.ai/zh/keys 注册取 key）")
    prompt = str(spec.get("prompt") or "").strip()
    if not prompt:
        raise RuntimeError("APIMart 生成需要 prompt")
    # R6：APIMart 只接受 HTTP(S) URL / data URI / base64 —— 本地路径必须先转 data URI，
    # 否则一定 400（"reference image must be an HTTP(S) URL, image data URI, or base64"）。
    model = str(spec.get("model") or cfg["model"])
    dur = int(float(spec.get("duration") or spec.get("seconds") or 10))
    if "muse-video-ext" in model and dur != 10:
        # 该模型只支持 10s（实测 400: "duration must be 10 seconds"）
        if prog:
            prog("apimart", f"{model} 只支持 10s，{dur}s 已改为 10s")
        dur = 10
    body = {"model": model, "prompt": prompt, "duration": dur}
    # R8：首尾帧 —— spec["images"] 传 [首帧, 尾帧]（APIMart 的 image_urls 是数组）。
    imgs = [str(x) for x in (spec.get("images") or []) if str(x).strip()]
    if imgs:
        body["image_urls"] = [_as_image_ref(x) for x in imgs[:2]]
    elif spec.get("image"):
        body["image_urls"] = [_as_image_ref(str(spec["image"]))]
    if spec.get("aspect"):
        body["aspect_ratio"] = str(spec["aspect"])
    url = cfg["base"] + cfg["path"]
    if prog:
        prog("apimart", f"POST {url} model={body['model']} {body['duration']}s")
    created = _http_json(url, body, cfg["key"], timeout=180)
    data = created.get("data")
    item = data[0] if isinstance(data, list) and data else (data if isinstance(data, dict) else {})
    task_id = str(item.get("task_id") or item.get("id") or "")
    if not task_id:
        raise RuntimeError(f"APIMart 未返回 task_id: {json.dumps(created, ensure_ascii=False)[:300]}")
    waited = 0.0
    info = item
    net_errors = 0
    while waited <= cfg["timeout"]:
        status = str(info.get("status") or "")
        if status in ("completed", "succeeded", "success"):
            break
        if status in ("failed", "error", "cancelled"):
            raise RuntimeError(f"APIMart 任务失败: {json.dumps(info, ensure_ascii=False)[:300]}")
        time.sleep(cfg["poll"])
        waited += cfg["poll"]
        # R7：轮询要能扛住偶发网络重置（实测 276s 时 URLError 直接把整个付费任务丢掉）。
        try:
            resp = _http_json(f"{cfg['base']}/v1/tasks/{task_id}", None, cfg["key"], timeout=60)
            net_errors = 0
        except Exception as exc:  # noqa: BLE001
            net_errors += 1
            if net_errors >= 6:
                raise RuntimeError(f"APIMart 轮询连续 {net_errors} 次失败（task_id={task_id}）："
                                   f"{type(exc).__name__}: {exc}") from exc
            if prog:
                prog("apimart", f"轮询网络抖动（{net_errors}/6），{cfg['poll']:.0f}s 后重试")
            continue
        info = resp.get("data") if isinstance(resp.get("data"), dict) else info
        if prog:
            prog("apimart", f"{info.get('status') or 'running'} {info.get('progress') or ''}%（{waited:.0f}s）")
    remote = ""
    result = info.get("result") or {}
    videos = result.get("videos") or []
    if videos:
        urls = videos[0].get("url") or []
        if isinstance(urls, list) and urls:
            remote = str(urls[0])
        elif isinstance(urls, str):
            remote = urls
    if not remote:
        remote = str(info.get("url") or info.get("video_url") or "")
    if not remote:
        raise RuntimeError(f"APIMart 未返回视频地址: {json.dumps(info, ensure_ascii=False)[:300]}")
    if prog:
        prog("apimart", "下载产物")
    out.parent.mkdir(parents=True, exist_ok=True)
    last_exc = None
    for attempt in range(3):
        try:
            req = urllib.request.Request(remote, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=600) as resp:
                data = resp.read()
            if data:
                out.write_bytes(data)
                break
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            time.sleep(3 + attempt * 5)
    else:
        raise RuntimeError(f"下载失败（重试 3 次）: {last_exc}")
    return {"ok": True, "provider": "apimart_muse", "out": str(out),
            "bytes": out.stat().st_size, "task_id": task_id,
            "cost_usd": info.get("cost"), "credits": info.get("credits_cost"),
            "probe": _probe(out)}


def _local_generate(provider: str, spec: dict, out: Path, prog=None) -> dict:
    """本地 MuseV/MusePose：模板 → argv 列表直接 exec（不再过 shell）。

    P1-8：旧实现 `cmd_tpl.format(**mapping)` + `shell=True` 且无 timeout ——
    含空格/引号/`&`/`|` 的中文 prompt 或 Windows 路径会被 cmd.exe 二次解释，
    MuseV 卡死还会永久挂住 agent 阶段。现在：
    - 模板语法**不变**（仍是 `MUSEV_CMD` 里的 `{prompt}` / `{out}` 等占位符）；
    - `shlex.split` 按 shell 词法切成 argv 后再逐 token 替换占位符（值不再被解析）；
    - 列表参数 + `subprocess.run(..., timeout=...)`；需要命令行回显时用 `shlex.quote`。
    """
    cmd_tpl = _local_cmd(provider)
    if not cmd_tpl:
        raise RuntimeError(
            f"未配置 {provider} 的推理命令；设置环境变量 "
            f"{'MUSEV_CMD' if provider == 'musev_local' else 'MUSEPOSE_CMD'}"
            f"（模板示例见 {DOCS}）")
    mapping = {
        "prompt": str(spec.get("prompt") or ""),
        "image": str(spec.get("image") or ""),
        "pose": str(spec.get("pose") or ""),
        "duration": str(spec.get("duration") or 4),
        "out": str(out),
        "seed": str(spec.get("seed") or 20261105),
        "width": str(spec.get("width") or 512),
        "height": str(spec.get("height") or 768),
    }
    argv = _argv_from_template(cmd_tpl, mapping)
    if prog:
        prog(provider, "执行: " + " ".join(shlex.quote(a) for a in argv)[:300])
    timeout = float(_env(f"{'MUSEV' if provider == 'musev_local' else 'MUSEPOSE'}_TIMEOUT",
                         "3600") or 3600)
    try:
        proc = subprocess.run(argv, cwd=str(REPO), capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(
            f"{provider} 推理超时（>{timeout:.0f}s，可调 "
            f"{'MUSEV' if provider == 'musev_local' else 'MUSEPOSE'}_TIMEOUT）"
            f"：{type(exc).__name__}") from exc
    except OSError as exc:  # 可执行文件不存在 / 无权限
        raise RuntimeError(f"{provider} 无法启动：{exc}（检查模板第一段可执行文件路径）") from exc
    if proc.returncode != 0:
        raise RuntimeError(f"{provider} 执行失败 rc={proc.returncode}: "
                           f"{(proc.stderr or proc.stdout or '')[-800:]}")
    if not out.is_file() or out.stat().st_size <= 0:
        raise RuntimeError(f"{provider} 未产出文件: {out}")
    return {"ok": True, "provider": provider, "out": str(out),
            "bytes": out.stat().st_size, "probe": _probe(out),
            "cmd": " ".join(shlex.quote(a) for a in argv)}


_PLACEHOLDER_RE = re.compile(r"\{([a-zA-Z_][a-zA-Z0-9_]*)\}")


def _argv_from_template(template: str, mapping: dict) -> list[str]:
    """把 `MUSEV_CMD` 模板切成 argv 并替换占位符（不经 shell，值不被二次解析）。

    - `posix=False` 保留 Windows 反斜杠路径；成对引号由本函数去掉；
    - 只替换 mapping 里认识的占位符，其它 `{...}` 原样保留（不会 KeyError/崩溃）；
    - 引号不配对时给可读错误（而不是 shlex 的 ValueError）。
    """
    try:
        tokens = shlex.split(str(template or ""), posix=False)
    except ValueError as exc:
        raise RuntimeError(f"本地 Muse 命令模板引号不配对：{exc}｜模板={template!r}") from exc
    argv: list[str] = []
    for token in tokens:
        if len(token) >= 2 and token[0] == token[-1] and token[0] in ("'", '"'):
            token = token[1:-1]
        argv.append(_PLACEHOLDER_RE.sub(
            lambda m: str(mapping[m.group(1)]) if m.group(1) in mapping else m.group(0),
            token))
    if not argv:
        raise RuntimeError("本地 Muse 命令模板为空")
    return argv



# --------------------------------------------------------------------------- #
# 云端托管：Replicate（MuseV / MuseTalk 官方托管）· useapi.net（Meta Muse 代理）
# --------------------------------------------------------------------------- #
def replicate_config() -> dict:
    return {
        "token": _env("REPLICATE_API_TOKEN"),
        "model": _env("REPLICATE_MUSEV_MODEL", "douwantech/musev"),
        "version": _env("REPLICATE_MUSEV_VERSION"),
        "base": _env("REPLICATE_API_BASE", "https://api.replicate.com").rstrip("/"),
        "input_map": _env("REPLICATE_MUSEV_INPUT_MAP"),
        "timeout": float(_env("REPLICATE_TIMEOUT", "900") or 900),
    }


def useapi_config() -> dict:
    return {
        "token": _env("USEAPI_TOKEN"),
        "account": _env("USEAPI_ACCOUNT"),
        "url": _env("USEAPI_MUSE_URL", "https://api.useapi.net/v1/muse/videos"),
        "poll": _env("USEAPI_MUSE_POLL"),
        "timeout": float(_env("USEAPI_TIMEOUT", "900") or 900),
    }


def _http_json_headers(url: str, payload: dict | None, headers: dict,
                       timeout: float = 120) -> dict:
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(url, data=data, method="POST" if data else "GET")
    req.add_header("Content-Type", "application/json")
    for key, value in (headers or {}).items():
        if value:
            req.add_header(key, value)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8") or "{}")


def _replicate_input(spec: dict, cfg: dict) -> dict:
    default = {
        "prompt": spec.get("prompt") or "",
        "image": spec.get("image"),
        "seed": int(spec.get("seed") or 20261105),
        "aspect_ratio": spec.get("aspect") or "16:9",
    }
    if cfg.get("input_map"):
        try:
            mapping = json.loads(cfg["input_map"])
            out = {}
            for src, dst in mapping.items():
                value = spec.get(src)
                if value in (None, ""):
                    continue
                out[dst] = value
            if out:
                return out
        except ValueError:
            pass
    return {k: v for k, v in default.items() if v not in (None, "")}


def _replicate_generate(spec: dict, out: Path, cfg: dict, prog=None) -> dict:
    if not cfg["token"]:
        raise RuntimeError("未配置 REPLICATE_API_TOKEN（注册：https://replicate.com/signin → "
                           "Account → API tokens）")
    if cfg["version"]:
        url = f"{cfg['base']}/v1/predictions"
        body = {"version": cfg["version"], "input": _replicate_input(spec, cfg)}
    else:
        url = f"{cfg['base']}/v1/models/{cfg['model']}/predictions"
        body = {"input": _replicate_input(spec, cfg)}
    headers = {"Authorization": f"Bearer {cfg['token']}", "Prefer": "wait=60"}
    if prog:
        prog("replicate", f"POST {url}（model={cfg['model']}）")
    resp = _http_json_headers(url, body, headers, timeout=120)
    get_url = (resp.get("urls") or {}).get("get") or f"{cfg['base']}/v1/predictions/{resp.get('id')}"
    waited = 0.0
    while str(resp.get("status") or "") in ("starting", "processing", "queued", ""):
        time.sleep(3)
        waited += 3
        if waited > cfg["timeout"]:
            raise RuntimeError(f"Replicate 任务超时（>{cfg['timeout']:.0f}s）: {get_url}")
        if prog:
            prog("replicate", f"轮询 {resp.get('status')}（{waited:.0f}s）")
        resp = _http_json_headers(get_url, None, headers, timeout=60)
    if str(resp.get("status")) != "succeeded":
        raise RuntimeError(f"Replicate 任务失败: {json.dumps(resp, ensure_ascii=False)[:300]}")
    output = resp.get("output")
    remote = ""
    if isinstance(output, str):
        remote = output
    elif isinstance(output, list) and output:
        remote = next((x for x in output if isinstance(x, str)), "")
    elif isinstance(output, dict):
        remote = str(output.get("video") or output.get("url") or "")
    if not remote:
        raise RuntimeError(f"Replicate 未返回视频地址: {json.dumps(output, ensure_ascii=False)[:300]}")
    if prog:
        prog("replicate", "下载产物")
    urllib.request.urlretrieve(remote, out)
    return {"ok": True, "provider": "replicate", "out": str(out), "bytes": out.stat().st_size,
            "remote_url": remote, "prediction": resp.get("id"), "probe": _probe(out)}


def _useapi_generate(spec: dict, out: Path, cfg: dict, prog=None) -> dict:
    if not cfg["token"]:
        raise RuntimeError("未配置 USEAPI_TOKEN（注册：https://useapi.net/docs/start-here/setup-muse）")
    headers = {"Authorization": f"Bearer {cfg['token']}"}
    body = {
        "account": cfg["account"],
        "prompt": str(spec.get("prompt") or ""),
        "duration": float(spec.get("duration") or 4.0),
    }
    if spec.get("image"):
        body["image"] = str(spec["image"])
    if prog:
        prog("useapi", f"POST {cfg['url']}（account={cfg['account'] or '未设置'}）")
    resp = _http_json_headers(cfg["url"], body, headers, timeout=120)
    remote = _pick_url(resp) or str(resp.get("video") or "")
    task_id = resp.get("id") or resp.get("task_id") or (resp.get("data") or {}).get("id")
    waited = 0.0
    while not remote and (task_id or str(resp.get("status")) in ("running", "queued", "processing")):
        time.sleep(3)
        waited += 3
        if waited > cfg["timeout"]:
            raise RuntimeError(f"useapi Muse 任务超时（>{cfg['timeout']:.0f}s）")
        poll = (cfg["poll"].format(id=task_id) if cfg["poll"] and task_id
                else f"{cfg['url'].rstrip('/')}/{task_id}")
        if prog:
            prog("useapi", f"轮询 {poll}（{waited:.0f}s）")
        resp = _http_json_headers(poll, None, headers, timeout=60)
        remote = _pick_url(resp) or str(resp.get("video") or "")
    if not remote:
        raise RuntimeError(f"useapi 未返回视频地址: {json.dumps(resp, ensure_ascii=False)[:300]}")
    if prog:
        prog("useapi", "下载产物")
    urllib.request.urlretrieve(remote, out)
    return {"ok": True, "provider": "useapi_muse", "out": str(out),
            "bytes": out.stat().st_size, "remote_url": remote,
            "task": task_id, "probe": _probe(out)}


def generate_video(spec: dict, prog=None) -> dict:
    """统一入口：按 provider 分发；返回 {ok, provider, out, bytes, probe, elapsed_sec}。"""
    spec = dict(spec or {})
    state = available()
    provider = str(spec.get("provider") or state["provider"] or "")
    if not provider:
        raise RuntimeError(f"没有可用的 Muse provider；接法见 {DOCS}")
    out = Path(spec.get("out") or (OUT_ROOT / f"muse_{int(time.time())}.mp4"))
    out.parent.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    if provider == "apimart_muse":
        return _apimart_generate(spec, out, apimart_config(), prog)
    if provider == "metaso_h3":
        return _metaso_generate(spec, out, metaso_config(), prog)
    if provider == "minimax_cloud":
        return _minimax_generate(spec, out, minimax_config(), prog)
    if provider == "stub":
        result = _stub_video(spec, out, prog)
    elif provider == "muse_cloud":
        result = _cloud_generate(spec, out, cloud_config(), prog)
    elif provider == "replicate":
        result = _replicate_generate(spec, out, replicate_config(), prog)
    elif provider == "useapi_muse":
        result = _useapi_generate(spec, out, useapi_config(), prog)
    elif provider in ("musev_local", "musepose_local"):
        result = _local_generate(provider, spec, out, prog)
    else:
        raise RuntimeError(f"未知 provider: {provider}")
    result["elapsed_sec"] = round(time.time() - t0, 2)
    result["spec"] = {k: v for k, v in spec.items() if k != "image_b64"}
    return result


def main(argv=None) -> int:
    import argparse

    ap = argparse.ArgumentParser(description="Muse 视频生成（provider 适配器）")
    ap.add_argument("--check", action="store_true", help="打印 provider 探测结果")
    ap.add_argument("--provider", default=None)
    ap.add_argument("--prompt", default="")
    ap.add_argument("--image", default=None)
    ap.add_argument("--pose", default=None)
    ap.add_argument("--duration", type=float, default=4.0)
    ap.add_argument("--aspect", default="16:9")
    ap.add_argument("--seed", type=int, default=20261105)
    ap.add_argument("--out", default=None)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    if args.check:
        print(json.dumps(available(), ensure_ascii=False, indent=2))
        return 0
    spec = {k: v for k, v in vars(args).items() if k not in ("check", "json")}
    try:
        result = generate_video(spec, prog=lambda stage, msg: print(f"[{stage}] {msg}", flush=True))
    except Exception as exc:  # noqa: BLE001
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2 if not args.json else None))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
