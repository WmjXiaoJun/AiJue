# -*- coding: utf-8 -*-
"""V50：Muse（MuseTalk 1.5）口型生成接口。

提供三件事：
- GET  /api/v6/muse/status         provider 可用性（代码/权重/venv/GPU）+ 最近产物 + 任务
- POST /api/v6/muse/sync           对「镜头 + 音频」跑口型（单条或按模板批量，后台串行）
- GET  /api/v6/muse/jobs/{job_id}  进度/日志/结果

说明：
- 本模块不 import torch；真正的推理由 pipeline.lipsync.sync_clip 转发到
  local_models/lipsync-venv（torch cu128）。
- 同一时间只允许一个 Muse 任务（GPU 显存 + 串行约束）。
- 产物落 outputs/muse/<template>/<shot>_muse.mp4，通过 /media/muse/... 访问。
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Body, HTTPException

from .. import config, ids

_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from pipeline import lipsync, muse_video, v5_catalog  # noqa: E402

router = APIRouter(prefix="/api/v6/muse", tags=["v6-muse"])
#: 独立 router：视频模型优先级不属于 muse 前缀（路径是 /api/v6/video/provider）
video_router = APIRouter(tags=["v6-video"])
ID_RE = re.compile(r"^[a-z][a-z0-9_]{1,60}$")
OUT_ROOT = Path(config.OUTPUTS_DIR) / "muse"


@video_router.get("/api/v6/video/provider")
def video_provider_status() -> dict:
    """视频模型优先级（用户口径：Muse → 本地 ComfyUI → 其它云端）。"""
    from pipeline import video_provider

    return {"ok": True, **video_provider.status()}
SHOTS_ROOTS = (Path(config.OUTPUTS_DIR) / "templates_3d",
               Path(config.OUTPUTS_DIR) / "templates_2d")
AUDIO_ROOTS = ("work/audio",)

_jobs: Dict[str, Dict[str, Any]] = {}
_lock = threading.Lock()
_current: Optional[str] = None
_CUDA: Optional[Dict[str, Any]] = None


def _cuda_probe() -> Dict[str, Any]:
    """用 venv 解释器探测 CUDA（首次 ~2s，之后缓存）。"""
    global _CUDA
    if _CUDA is not None:
        return _CUDA
    try:
        proc = subprocess.run(
            [str(lipsync.VENV_PY), "-c",
             "import torch;print(int(torch.cuda.is_available()));"
             "print(torch.cuda.get_device_name(0) if torch.cuda.is_available() else '')"],
            capture_output=True, text=True, timeout=90)
        lines = [x.strip() for x in (proc.stdout or "").splitlines() if x.strip()]
        _CUDA = {"available": bool(lines and lines[0] == "1"),
                 "device": lines[1] if len(lines) > 1 else "",
                 "progress": lines[2] if len(lines) > 2 else ""}
    except Exception as exc:  # noqa: BLE001
        _CUDA = {"available": False, "device": "", "error": str(exc)[:200]}
    return _CUDA


def _template(template_id: str) -> dict:
    if not ID_RE.match(template_id or ""):
        raise HTTPException(status_code=400, detail="非法模板 id")
    cat = v5_catalog.load_catalog()
    tpl = (cat.get("templates") or {}).get(template_id)
    if not tpl:
        raise HTTPException(status_code=404, detail=f"模板不存在: {template_id}")
    return tpl


def _safe_input_path(raw: str, label: str) -> Path:
    """只允许仓库 outputs/ 或 tmp/ 下的文件，防止 ../ 逃逸读任意文件。"""
    if not str(raw).strip():
        raise HTTPException(status_code=400, detail=f"{label} 不能为空")
    path = (_REPO_ROOT / str(raw)).resolve()
    roots = [Path(config.OUTPUTS_DIR).resolve(), (_REPO_ROOT / "tmp").resolve()]
    if not any(path == r or r in path.parents for r in roots):
        raise HTTPException(status_code=400,
                            detail=f"{label} 必须在 outputs/ 或 tmp/ 下: {raw}")
    return path


def _media_url(path: Path) -> str:
    try:
        rel = path.resolve().relative_to(Path(config.OUTPUTS_DIR).resolve())
    except ValueError:
        return ""
    return "/media/" + rel.as_posix()


def _dialogue_pairs(template_id: str, limit: int = 2) -> List[Dict[str, Any]]:
    """对白镜头 + 音频（与 agent 工具共用 pipeline.lipsync.dialogue_pairs 的选择规则）。"""
    pairs = lipsync.dialogue_pairs(template_id, limit)
    for item in pairs:
        item["bytes"] = item["clip"].stat().st_size if item["clip"].is_file() else 0
    return pairs


def _recent(limit: int = 12) -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    if not OUT_ROOT.is_dir():
        return items
    for path in sorted(OUT_ROOT.rglob("*_muse.mp4"), key=lambda p: p.stat().st_mtime,
                       reverse=True)[:limit]:
        items.append({
            "template": path.parent.name,
            "shot": path.name.replace("_muse.mp4", ""),
            "url": _media_url(path),
            "bytes": path.stat().st_size,
            "mtime": time.strftime("%Y-%m-%d %H:%M", time.localtime(path.stat().st_mtime)),
        })
    return items


@router.get("/status", summary="Muse(MuseTalk) 可用性 + 最近产物")
def muse_status() -> dict:
    avail = lipsync.available()
    with _lock:
        jobs = [{k: v for k, v in j.items() if k != "logs"} for j in _jobs.values()]
    jobs.sort(key=lambda j: j.get("created") or "", reverse=True)
    return {"ok": True, "available": {**avail, "cuda": _cuda_probe()},
            "recent": _recent(),
            "jobs": jobs[:10], "running": _current}


def _worker(job_id: str, items: List[Dict[str, Any]], out_dir: Path,
            start: Optional[float], duration: Optional[float]) -> None:
    global _current
    with _lock:
        job = _jobs[job_id]
        job["status"] = "running"
        job["started"] = time.strftime("%Y-%m-%d %H:%M:%S")
        _current = job_id

    def log(msg: str) -> None:
        with _lock:
            j = _jobs.get(job_id)
            if j is not None:
                j["logs"].append({"t": time.strftime("%H:%M:%S"), "msg": str(msg)[:400]})
                if len(j["logs"]) > 400:
                    j["logs"] = j["logs"][-400:]

    results: List[Dict[str, Any]] = []
    try:
        for idx, item in enumerate(items, 1):
            out = out_dir / f"{item['shot']}_muse.mp4"
            log(f"[{idx}/{len(items)}] {item['shot']} 开始（{item['clip'].name}）")
            t0 = time.time()
            try:
                res = lipsync.sync_clip(item["clip"], item["audio"], out,
                                        start=start, duration=duration)
                elapsed = round(time.time() - t0, 1)
                try:
                    sync = lipsync.measure_sync(out, item["audio"], start=float(start or 0.0))
                except Exception as exc:  # noqa: BLE001
                    sync = {"ok": False, "reason": str(exc)[:200]}
                results.append({"shot": item["shot"], "out": str(out),
                                "out_url": _media_url(out), "elapsed": elapsed,
                                "sync": {k: sync.get(k) for k in
                                         ("ok", "provider", "corr_best", "lag_frames",
                                          "face_ratio")},
                                "result": res if isinstance(res, dict) else None})
                log(f"[{idx}/{len(items)}] {item['shot']} 完成 {elapsed}s")
            except BaseException as exc:  # noqa: BLE001
                results.append({"shot": item["shot"],
                                "error": f"{type(exc).__name__}: {exc}",
                                "elapsed": round(time.time() - t0, 1)})
                log(f"[{idx}/{len(items)}] {item['shot']} 失败：{exc}")
        failed = [r for r in results if r.get("error")]
        with _lock:
            job = _jobs[job_id]
            job["status"] = "failed" if failed and len(failed) == len(results) else "ok"
            job["ended"] = time.strftime("%Y-%m-%d %H:%M:%S")
            job["result"] = {"items": results, "out_dir": str(out_dir),
                             "ok_count": len(results) - len(failed),
                             "failed_count": len(failed)}
    except BaseException as exc:  # noqa: BLE001 - SystemExit 也要落 failed
        with _lock:
            job = _jobs[job_id]
            job["status"] = "failed"
            job["error"] = f"{type(exc).__name__}: {exc}"
            job["ended"] = time.strftime("%Y-%m-%d %H:%M:%S")
    finally:
        with _lock:
            if _current == job_id:  # 只清自己占的位
                _current = None


@router.post("/sync", summary="跑口型（单条 clip 或按模板批量）")
def muse_sync(payload: dict = Body(default=None)) -> dict:
    global _current
    payload = dict(payload or {})
    try:
        limit = max(1, min(int(payload.get("limit") or 2), 8))
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="limit 必须是整数 1-8")
    if payload.get("clip"):
        clip = _safe_input_path(payload["clip"], "clip")
        audio = _safe_input_path(payload.get("audio") or "", "audio")
        if not clip.is_file():
            raise HTTPException(status_code=400, detail=f"clip 不存在: {clip}")
        if not audio.is_file():
            raise HTTPException(status_code=400, detail=f"audio 不存在: {audio}")
        guessed = clip.parent.parent.name
        # 审查 2026-11-08：clip 分支曾直接采信 payload["template"]，再拼 OUT_ROOT / template，
        # ../../ 可把 mp4 写到 outputs/muse 之外。这里与视频分支统一走 ids 白名单。
        raw_template = payload.get("template") or (guessed if ID_RE.match(guessed) else "muse")
        template_id = ids.check_id(raw_template, "template")
        items = [{"shot": clip.stem, "clip": clip, "audio": audio}]
    else:
        template_id = str(payload.get("template") or "")
        _template(template_id)
        items = _dialogue_pairs(template_id, limit)
        if not items:
            raise HTTPException(status_code=400,
                                detail="该模板没有「镜头 + 音频」对（先跑 shots 阶段）")
    avail = lipsync.available()
    if not avail.get("ok"):
        raise HTTPException(status_code=400,
                            detail={"message": "MuseTalk 不可用", "reason": avail.get("reason")})
    out_dir = ids.resolve_under(OUT_ROOT, template_id, "Muse 输出目录")
    out_dir.mkdir(parents=True, exist_ok=True)
    with _lock:
        if _current:
            raise HTTPException(status_code=409, detail=f"已有 Muse 任务在跑: {_current}")
        job_id = "muse_" + uuid.uuid4().hex[:10]
        _current = job_id  # 请求内先占位，避免 check-then-act 竞态
        _jobs[job_id] = {
            "job_id": job_id, "status": "queued", "logs": [],
            "created": time.strftime("%Y-%m-%d %H:%M:%S"),
            "template": template_id, "shots": [i["shot"] for i in items],
            "provider": "musetalk",
        }
    threading.Thread(target=_worker, args=(job_id, items, out_dir,
                                           payload.get("start"), payload.get("duration")),
                     daemon=True).start()
    return {"ok": True, "job_id": job_id, "template": template_id,
            "shots": [i["shot"] for i in items], "provider": "musetalk"}


@router.get("/jobs/{job_id}", summary="Muse 任务状态/日志/结果")
def muse_job(job_id: str) -> dict:
    with _lock:
        job = _jobs.get(job_id)
        if not job:
            raise HTTPException(status_code=404, detail="job 不存在")
        return {"ok": True, **json.loads(json.dumps(job, ensure_ascii=False))}

# --------------------------------------------------------------------------- #
# Muse 视频生成（MuseV / MusePose / 云端）—— 与口型共用任务模型
# --------------------------------------------------------------------------- #
_vjobs: Dict[str, Dict[str, Any]] = {}
_vlock = threading.Lock()
_vcurrent: Optional[str] = None
VIDEO_ROOT = Path(config.OUTPUTS_DIR) / "muse_video"


def _video_recent(limit: int = 8) -> list:
    items = []
    if VIDEO_ROOT.is_dir():
        for path in sorted(VIDEO_ROOT.rglob("*.mp4"), key=lambda p: p.stat().st_mtime,
                           reverse=True)[:limit]:
            items.append({"name": path.stem, "url": _media_url(path),
                          "bytes": path.stat().st_size,
                          "mtime": time.strftime("%Y-%m-%d %H:%M",
                                                 time.localtime(path.stat().st_mtime))})
    return items


@router.get("/video/status", summary="Muse 视频模型 provider 探测")
def muse_video_status() -> dict:
    state = muse_video.available()
    with _vlock:
        jobs = [{k: v for k, v in j.items() if k != "logs"} for j in _vjobs.values()]
    jobs.sort(key=lambda j: j.get("created") or "", reverse=True)
    return {"ok": True, "state": state, "recent": _video_recent(),
            "jobs": jobs[:10], "running": _vcurrent}


def _video_worker(job_id: str, spec: dict) -> None:
    global _vcurrent
    with _vlock:
        job = _vjobs[job_id]
        job["status"] = "running"
        job["started"] = time.strftime("%Y-%m-%d %H:%M:%S")
        _vcurrent = job_id

    def log(stage: str, msg: str) -> None:
        with _vlock:
            j = _vjobs.get(job_id)
            if j is not None:
                j["logs"].append({"t": time.strftime("%H:%M:%S"), "stage": stage,
                                  "msg": str(msg)[:500]})
                if len(j["logs"]) > 400:
                    j["logs"] = j["logs"][-400:]

    try:
        result = muse_video.generate_video(spec, prog=log)
        with _vlock:
            job = _vjobs[job_id]
            job["status"] = "ok"
            job["ended"] = time.strftime("%Y-%m-%d %H:%M:%S")
            path = Path(str(result.get("out") or ""))
            job["result"] = {**{k: result.get(k) for k in
                                ("provider", "bytes", "elapsed_sec", "probe", "stub")},
                             "out": str(path), "out_url": _media_url(path)}
    except BaseException as exc:  # noqa: BLE001
        with _vlock:
            job = _vjobs[job_id]
            job["status"] = "failed"
            job["error"] = f"{type(exc).__name__}: {exc}"
            job["ended"] = time.strftime("%Y-%m-%d %H:%M:%S")
    finally:
        with _vlock:
            if _vcurrent == job_id:
                _vcurrent = None


@router.post("/video", summary="Muse 生成一段视频（后台任务）")
def muse_video_generate(payload: dict = Body(default=None)) -> dict:
    global _vcurrent
    payload = dict(payload or {})
    state = muse_video.available()
    provider = str(payload.get("provider") or state.get("provider") or "")
    if provider == "stub" and not payload.get("allow_stub"):
        raise HTTPException(status_code=400, detail={
            "message": "Muse 视频模型未接入（本地未安装、云端未配置）",
            "state": state, "docs": state.get("docs"),
            "hint": "见 docs/MUSE_VIDEO_INTEGRATION.md；接线自测可传 allow_stub=true"})
    template = str(payload.get("template") or "muse")
    if template != "muse" and not ID_RE.match(template):
        raise HTTPException(status_code=400, detail=f"非法 template: {template}")
    raw_name = str(payload.get("name") or f"muse_{time.strftime('%Y%m%d_%H%M%S')}")
    name = re.sub(r"[^A-Za-z0-9_-]", "", raw_name)[:60] or "muse"
    out = (VIDEO_ROOT / (template if template != "muse" else "") / f"{name}.mp4").resolve()
    if VIDEO_ROOT.resolve() not in out.parents:
        raise HTTPException(status_code=400, detail="输出路径越界")
    spec = {**payload, "provider": provider, "out": str(out)}
    spec.pop("allow_stub", None)
    with _vlock:
        if _vcurrent:
            raise HTTPException(status_code=409, detail=f"已有 Muse 视频任务在跑: {_vcurrent}")
        job_id = "musev_" + uuid.uuid4().hex[:10]
        _vcurrent = job_id  # 请求内先占位
        _vjobs[job_id] = {
            "job_id": job_id, "status": "queued", "logs": [],
            "created": time.strftime("%Y-%m-%d %H:%M:%S"),
            "provider": provider, "prompt": str(spec.get("prompt") or "")[:200],
            "out": str(out),
        }
    threading.Thread(target=_video_worker, args=(job_id, spec), daemon=True).start()
    return {"ok": True, "job_id": job_id, "provider": provider, "out": str(out)}


@router.get("/video/jobs/{job_id}", summary="Muse 视频任务状态")
def muse_video_job(job_id: str) -> dict:
    with _vlock:
        job = _vjobs.get(job_id)
        if not job:
            raise HTTPException(status_code=404, detail="job 不存在")
        return {"ok": True, **json.loads(json.dumps(job, ensure_ascii=False))}


# ---------------------------------------------------------------- 模板级：云端 Muse 出整集
#: 与"单条视频"共用 `_vjobs`，用 kind 区分；进度由 muse_shots.render 的 prog 回调写入。
@video_router.post("/api/v6/muse/video/template", summary="用云端 Muse 按模板生成整集")
def muse_video_template(payload: dict = Body(...)) -> dict:
    """按模板/brief 跑云端 Muse（10s/段），可选直接合成。

    body: {template, segments?, limit?, chain?, force?, assemble?, dry_run?}
    - chain=true：用上一段最后一帧当下一段首帧（长场景/同角色连续）
    - 每段参考图 = 说话角色的立绘（实测可过内容安全，2D 关键帧会被拒）
    """
    from pipeline import muse_shots

    tid = str(payload.get("template") or "").strip()
    _template(tid)  # 校验 id + 存在性
    segments = payload.get("segments")
    limit = payload.get("limit")
    chain = bool(payload.get("chain", True))
    force = bool(payload.get("force", False))
    assemble = bool(payload.get("assemble", True))
    dry = bool(payload.get("dry_run", False))
    if dry:
        res = muse_shots.render(tid, limit=limit, segments=segments, chain=chain,
                                dry_run=True)
        return {"ok": True, "dry_run": True, **res}
    with _vlock:
        if _vcurrent:
            raise HTTPException(status_code=409, detail=f"已有 Muse 视频任务在跑: {_vcurrent}")
        job_id = "muset_" + uuid.uuid4().hex[:10]
        _vcurrent = job_id
        _vjobs[job_id] = {
            "job_id": job_id, "kind": "template", "status": "running",
            "template": tid, "segments": segments, "limit": limit,
            "chain": chain, "assemble": assemble,
            "created": time.strftime("%Y-%m-%d %H:%M:%S"),
            "logs": [], "ok_count": 0, "failed": [], "final": None,
        }

    def _run() -> None:
        global _vcurrent
        try:
            def prog(who: str, msg: str) -> None:
                with _vlock:
                    job = _vjobs.get(job_id)
                    if job is not None:
                        job["logs"].append(f"[{who}] {msg}")
                        if len(job["logs"]) > 400:
                            job["logs"] = job["logs"][-400:]
                        job["stage"] = msg

            res = muse_shots.render(tid, limit=limit, segments=segments, chain=chain,
                                    force=force, assemble=assemble, prog=prog)
            with _vlock:
                job = _vjobs.get(job_id) or {}
                job.update({"status": "done" if res.get("ok") else "failed",
                            "ok_count": res.get("ok_count"), "failed": res.get("failed"),
                            "final": (res.get("assemble") or {}).get("final"),
                            "episode": res.get("episode"),
                            "result": {k: res.get(k) for k in ("segments", "ok_count")}})
        except Exception as exc:  # noqa: BLE001
            with _vlock:
                job = _vjobs.get(job_id) or {}
                job.update({"status": "failed", "error": f"{type(exc).__name__}: {exc}"[:400]})
        finally:
            with _vlock:
                _vcurrent = None

    threading.Thread(target=_run, daemon=True).start()
    return {"ok": True, "job_id": job_id, "template": tid, "chain": chain}
