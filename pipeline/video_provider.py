# -*- coding: utf-8 -*-
"""视频模型优先级策略：**Muse → 本地 ComfyUI → 其它云端**。

用户口径（2026-11）：视频模型第一优先 Muse，其次本地 ComfyUI，最后才是其它。

这里的"Muse"指 Muse 家族（MuseTalk 口型 / MuseV / MusePose / 云端 Muse 代理），
"本地 ComfyUI"指本机 8188 的 H3 T2VA/ref2va（`templates3d_hq` 的镜头渲染路径），
"其它"指 metaso / minimax 等第三方托管。

职责划分（这是本模块唯一要说清的事）：
- **口型 / Muse 视频生成**（`muse`、`musev` 阶段）：三条来源都可选 → 本模块按优先级挑；
- **逐镜渲染**（`shots` 阶段）：目前只有本地 ComfyUI 这条路是通的（MuseV 需要另外的
  图生视频 API 与权重），所以它只上报"将用 comfyui_local"，不改变既有渲染路径 ——
  想真正换引擎要改 `templates3d_hq`（另立任务，别在这里偷偷切）。

对外：
    provider_order()   -> ["muse", "comfyui_local", "other"]
    resolve()          -> 选中的一类 + 原因 + 每类的可用性明细
    status()           -> 给 API/UI 用的扁平状态
"""
from __future__ import annotations

import os
from pathlib import Path

from . import config

#: 用户指定的默认优先级；可用 `ZAOPIANJU_VIDEO_PROVIDER_ORDER=muse,comfyui_local,other` 覆盖
DEFAULT_ORDER = ("muse", "comfyui_local", "other")
KNOWN = set(DEFAULT_ORDER)


def provider_order() -> list[str]:
    raw = str(os.environ.get("ZAOPIANJU_VIDEO_PROVIDER_ORDER") or "").strip()
    if not raw:
        return list(DEFAULT_ORDER)
    picked = [p.strip().lower() for p in raw.split(",") if p.strip()]
    order = [p for p in picked if p in KNOWN]
    # 没点到的类别排在后面（保证 resolve 永远能兜底到 other）
    order += [p for p in DEFAULT_ORDER if p not in order]
    return order


#: 属于 Muse 家族的 provider（其余如 metaso_h3 / minimax 归 "other"）
MUSE_FAMILY = ("musev_local", "musepose_local", "muse_cloud", "replicate",
               "useapi_muse", "apimart_muse")


def _muse_status() -> dict:
    """Muse 家族：口型（MuseTalk）+ 视频生成（MuseV/MusePose/云端代理）。"""
    from . import lipsync, muse_video

    lip = {}
    try:
        lip = lipsync.available()
    except Exception as exc:  # noqa: BLE001
        lip = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    vid = {}
    try:
        vid = muse_video.available()
    except Exception as exc:  # noqa: BLE001
        vid = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    lip_ok = bool(lip.get("ok")) and bool(lip.get("musetalk"))
    # 只认 Muse 家族：metaso/minimax 这些"其它云端"不能把 Muse 顶成可用（否则优先级撒谎）
    configured = [n for n in (vid.get("configured_providers") or []) if n in MUSE_FAMILY]
    checks = vid.get("checks") or {}
    vid_ok = bool(configured)
    provider = configured[0] if configured else ""
    return {
        "available": bool(lip_ok or vid_ok),
        "lipsync": {"ok": lip_ok, "provider": "musetalk" if lip_ok else "",
                    "detail": {k: lip.get(k) for k in ("musetalk", "wav2lip", "device") if k in lip}},
        "video": {"ok": vid_ok, "provider": provider,
                  "configured": configured,
                  "checks": {k: v for k, v in checks.items() if k in MUSE_FAMILY}},
        "what": "MuseTalk 口型 + MuseV/MusePose 视频生成",
    }


def _comfyui_status() -> dict:
    """本地 ComfyUI：探 /system_stats，并报 H3 工作流是否就位。"""
    from . import h3_comfy

    info: dict = {"available": False, "server": "", "error": ""}
    try:
        server = h3_comfy.server_url() if hasattr(h3_comfy, "server_url") else None
    except Exception:  # noqa: BLE001
        server = None
    try:
        health = h3_comfy.health(server) if callable(getattr(h3_comfy, "health", None)) else {}
    except Exception as exc:  # noqa: BLE001
        health = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    info["server"] = str(health.get("server") or server or h3_comfy.DEFAULT_SERVER)
    info["available"] = bool(health.get("ok"))
    info["error"] = "" if info["available"] else str(health.get("error") or "")[:300]
    wf = Path(config.REPO_ROOT) / "integrations" / "comfyui"
    info["workflow_dir"] = str(wf)
    info["workflow_present"] = (wf / "run_h3.py").is_file()
    if "system" in health:
        info["comfyui_version"] = (health.get("system") or {}).get("comfyui_version")
    if "devices" in health:
        info["devices"] = health.get("devices")
    return info


def _other_status() -> dict:
    """其它云端视频（metaso / minimax）：只看 env 是否配置，不发网络请求。"""
    from . import muse_video

    out = {}
    for name, fn in (("metaso_h3", getattr(muse_video, "metaso_config", None)),
                     ("minimax_cloud", getattr(muse_video, "minimax_config", None))):
        if not callable(fn):
            continue
        try:
            cfg = fn() or {}
        except Exception:  # noqa: BLE001
            cfg = {}
        key = str(cfg.get("key") or "")
        out[name] = {"configured": bool(key), "base": str(cfg.get("base") or "")}
    return {"available": any(v.get("configured") for v in out.values()), "providers": out}


def resolve() -> dict:
    """按优先级挑一类；返回选中项、原因、以及每类的可用性明细。"""
    order = provider_order()
    status = {"muse": _muse_status(), "comfyui_local": _comfyui_status(),
              "other": _other_status()}
    chosen = next((name for name in order if status[name]["available"]), "")
    reason = {
        "muse": "Muse 可用（口型/视频生成）",
        "comfyui_local": f"本地 ComfyUI 可用（{status['comfyui_local'].get('server')}）",
        "other": "仅第三方云端可用",
        "": "没有任何可用的视频来源：检查 Muse 权重/env、ComfyUI 是否启动（127.0.0.1:8188）",
    }[chosen]
    return {"order": order, "chosen": chosen, "reason": reason, "providers": status}


def status() -> dict:
    """给 API/UI：扁平、可缓存、可直接渲染成"视频模型：Muse · 本地 ComfyUI"。"""
    res = resolve()
    providers = res["providers"]
    return {
        "ok": bool(res["chosen"]),
        "order": res["order"],
        "chosen": res["chosen"],
        "chosen_label": {"muse": "Muse", "comfyui_local": "本地 ComfyUI",
                         "other": "第三方云端", "": "不可用"}[res["chosen"]],
        "reason": res["reason"],
        "muse": {"ok": providers["muse"]["available"],
                 "lipsync": providers["muse"]["lipsync"],
                 "video": {k: v for k, v in providers["muse"]["video"].items() if k != "checks"}},
        "comfyui_local": providers["comfyui_local"],
        "other": providers["other"],
        "note": "逐镜渲染（shots 阶段）当前固定走本地 ComfyUI；Muse 用于口型与 Muse 视频生成。",
    }


if __name__ == "__main__":
    import json

    print(json.dumps(status(), ensure_ascii=False, indent=1))
