# -*- coding: utf-8 -*-
"""V29：成片库「批量导出发布包」API（前缀 `/api/cinema/publish-packages`）。

- POST /api/cinema/publish-packages/batch
- GET  /api/cinema/publish-packages/batch/{batch_id}
- POST /api/cinema/publish-packages/batch/{batch_id}/cancel
- GET  /api/cinema/publish-packages/batches?limit=10

逐项复用 `cinema_templates._build_publish_package`（同 `_PUBLISH_LOCK`，避免并发写同一个 zip）；
无 final / 构建失败的 item 标 failed，不整体失败；持久化 outputs/publish_batches/<id>.json。
"""
from __future__ import annotations

import json
import os
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from .. import config
from . import cinema_templates as cinema

router = APIRouter(prefix="/api/cinema", tags=["publish-batches"])

_BATCH_DIR = Path(config.OUTPUTS_DIR) / "publish_batches"
_BATCHES: Dict[str, Dict[str, Any]] = {}
_BATCH_LOCK = threading.RLock()
_BATCH_LIMIT = 10


class PublishBatchBody(BaseModel):
    template_ids: List[str]
    platform: str = "generic"
    vertical: Optional[bool] = None
    vertical_mode: str = "blur_pad"
    force: bool = False


def _now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def _persist(batch: Dict[str, Any]) -> None:
    try:
        _BATCH_DIR.mkdir(parents=True, exist_ok=True)
        target = _BATCH_DIR / f"{batch['id']}.json"
        tmp = target.with_name(f"{target.name}.tmp-{os.getpid()}-{threading.get_ident()}")
        tmp.write_text(json.dumps(batch, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, target)
    except OSError:
        pass


def _elapsed(batch: Dict[str, Any]) -> float:
    created = batch.get("created_ts")
    if not isinstance(created, (int, float)):
        return 0.0
    finished = batch.get("finished_ts")
    end = float(finished) if isinstance(finished, (int, float)) else time.time()
    return round(max(0.0, end - float(created)), 1)


def _summary(batch: Dict[str, Any]) -> Dict[str, int]:
    counts = {key: 0 for key in ("queued", "building", "done", "failed", "canceled")}
    for item in batch.get("items") or []:
        status = str(item.get("status") or "queued")
        counts[status] = counts.get(status, 0) + 1
    counts["total"] = len(batch.get("items") or [])
    return counts


def _item_view(item: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "index": item.get("index"),
        "template_id": item.get("template_id"),
        "label": item.get("label"),
        "status": item.get("status"),
        "package_url": item.get("package_url"),
        "size_bytes": item.get("size_bytes"),
        "files_count": item.get("files_count"),
        "error": item.get("error"),
        "started_at": item.get("started_at"),
        "finished_at": item.get("finished_at"),
        "elapsed_sec": item.get("elapsed_sec"),
    }


def _batch_view(batch: Dict[str, Any], *, full: bool = True) -> Dict[str, Any]:
    items = [_item_view(item) for item in batch.get("items") or []]
    if not full:
        items = [{key: item.get(key) for key in
                  ("index", "template_id", "label", "status", "package_url",
                   "size_bytes", "files_count", "error")} for item in items]
    return {
        "id": batch.get("id"),
        "status": batch.get("status"),
        "current_index": batch.get("current_index", -1),
        "total": len(items),
        "items": items,
        "summary": _summary(batch),
        "elapsed_sec": _elapsed(batch),
        "platform": batch.get("platform"),
        "vertical": batch.get("vertical"),
        "vertical_mode": batch.get("vertical_mode"),
        "force": batch.get("force"),
        "created_at": batch.get("created_at"),
        "finished_at": batch.get("finished_at"),
        "error": batch.get("error"),
    }


def _run_batch(batch_id: str) -> None:
    batch = _BATCHES.get(batch_id)
    if not batch:
        return
    with _BATCH_LOCK:
        if batch.get("status") != "queued":
            return
        batch["status"] = "running"
        batch["started_at"] = _now()
        batch["started_ts"] = time.time()
        _persist(batch)
    try:
        while True:
            with _BATCH_LOCK:
                if batch.get("status") == "canceled":
                    break
                item = next((row for row in batch.get("items") or []
                             if row.get("status") == "queued"), None)
                if item is None:
                    break
                item["status"] = "building"
                item["started_at"] = _now()
                item["started_ts"] = time.time()
                item["error"] = None
                batch["current_index"] = item.get("index")
                _persist(batch)
            started = time.time()
            try:
                result = cinema._build_publish_package(
                    str(item.get("template_id") or ""),
                    str(batch.get("platform") or "generic"),
                    bool(batch.get("force")),
                    vertical=bool(batch.get("vertical")),
                    vertical_mode=str(batch.get("vertical_mode") or "blur_pad"),
                )
                with _BATCH_LOCK:
                    item["status"] = "done"
                    item["package_url"] = result.get("package_url")
                    item["size_bytes"] = int(result.get("size_bytes") or 0)
                    item["files_count"] = len(result.get("files") or [])
                    item["cached"] = bool(result.get("cached"))
                    item["finished_at"] = _now()
                    item["finished_ts"] = time.time()
                    item["elapsed_sec"] = round(time.time() - started, 1)
                    batch["current_index"] = -1
                    _persist(batch)
            except Exception as exc:  # noqa: BLE001 - 单项失败不整体失败
                if isinstance(exc, HTTPException):
                    detail = exc.detail
                    message = detail.get("message") if isinstance(detail, dict) else str(detail)
                else:
                    message = f"{type(exc).__name__}: {exc}"
                with _BATCH_LOCK:
                    item["status"] = "failed"
                    item["error"] = str(message or exc)[:800]
                    item["finished_at"] = _now()
                    item["finished_ts"] = time.time()
                    item["elapsed_sec"] = round(time.time() - started, 1)
                    batch["current_index"] = -1
                    _persist(batch)
            with _BATCH_LOCK:
                if batch.get("status") == "canceled":
                    break
        with _BATCH_LOCK:
            if batch.get("status") == "canceled":
                for item in batch.get("items") or []:
                    if item.get("status") in ("queued", "building"):
                        item["status"] = "canceled"
                        item["error"] = "用户取消"
                        item["finished_at"] = _now()
                        item["finished_ts"] = time.time()
            else:
                statuses = [item.get("status") for item in batch.get("items") or []]
                failed = statuses.count("failed")
                total = len(statuses)
                if failed == 0:
                    batch["status"] = "done"
                elif failed >= total:
                    batch["status"] = "failed"
                else:
                    batch["status"] = "partial_failed"
            batch["current_index"] = -1
            batch["finished_at"] = _now()
            batch["finished_ts"] = time.time()
            _persist(batch)
    except Exception as exc:  # noqa: BLE001 - 批次级异常兜底
        with _BATCH_LOCK:
            batch["status"] = "failed"
            batch["error"] = f"批量导出异常: {type(exc).__name__}: {exc}"
            for item in batch.get("items") or []:
                if item.get("status") in ("queued", "building"):
                    item["status"] = "failed"
                    item["error"] = batch["error"]
                    item["finished_at"] = _now()
            batch["current_index"] = -1
            batch["finished_at"] = _now()
            batch["finished_ts"] = time.time()
            _persist(batch)


@router.post("/publish-packages/batch", status_code=202,
             summary="批量导出发布包（多模板串行构建）")
def start_publish_batch(payload: PublishBatchBody):
    cinema._refresh_template_ids()
    ids: List[str] = []
    for raw in payload.template_ids or []:
        tid = str(raw or "").strip()
        if not tid:
            continue
        if tid not in ids:
            ids.append(tid)
    if not ids:
        raise HTTPException(status_code=400, detail="template_ids 不能为空")
    if len(ids) > 50:
        raise HTTPException(status_code=400, detail="一次批量最多 50 个模板")
    platform = str(payload.platform or "generic").strip().lower()
    if platform not in cinema.PUBLISH_PLATFORMS:
        raise HTTPException(status_code=400,
                            detail=f"platform 只支持 {', '.join(cinema.PUBLISH_PLATFORMS)}（当前 {platform!r}）")
    vertical_mode = str(payload.vertical_mode or "blur_pad").strip().lower()
    if vertical_mode not in cinema.PUBLISH_VERTICAL_MODES:
        raise HTTPException(status_code=400,
                            detail=f"vertical_mode 只支持 {', '.join(cinema.PUBLISH_VERTICAL_MODES)}"
                                   f"（当前 {vertical_mode!r}）")
    vertical = (bool(payload.vertical) if payload.vertical is not None
                else platform in cinema.PUBLISH_VERTICAL_PLATFORMS)
    batch_id = "pub_" + uuid.uuid4().hex[:12]
    items = [{
        "index": index,
        "template_id": tid,
        "label": cinema._template_label(tid) or tid,
        "status": "queued",
        "package_url": None,
        "size_bytes": None,
        "files_count": None,
        "error": None,
        "started_at": None,
        "finished_at": None,
        "elapsed_sec": None,
    } for index, tid in enumerate(ids)]
    batch: Dict[str, Any] = {
        "id": batch_id,
        "status": "queued",
        "current_index": -1,
        "items": items,
        "platform": platform,
        "vertical": bool(vertical),
        "vertical_mode": vertical_mode,
        "force": bool(payload.force),
        "created_at": _now(),
        "created_ts": time.time(),
        "started_at": None,
        "finished_at": None,
        "finished_ts": None,
        "error": None,
    }
    with _BATCH_LOCK:
        _BATCHES[batch_id] = batch
    _persist(batch)
    try:
        threading.Thread(target=_run_batch, args=(batch_id,), daemon=True,
                         name=f"publish-batch-{batch_id}").start()
    except Exception as exc:  # noqa: BLE001
        with _BATCH_LOCK:
            batch["status"] = "failed"
            batch["error"] = f"启动批量线程失败: {exc}"
            batch["finished_at"] = _now()
            batch["finished_ts"] = time.time()
        _persist(batch)
        raise HTTPException(status_code=500, detail=f"启动批量导出失败: {exc}") from exc
    return {
        "ok": True,
        "batch_id": batch_id,
        "status": "queued",
        "total": len(items),
        "items": [{"template_id": item["template_id"], "label": item["label"],
                   "status": "queued"} for item in items],
        "platform": platform,
        "vertical": bool(vertical),
        "vertical_mode": vertical_mode,
    }


@router.get("/publish-packages/batch/{batch_id}", summary="批量发布包状态")
def get_publish_batch(batch_id: str):
    with _BATCH_LOCK:
        batch = _BATCHES.get(str(batch_id or "").strip())
        if not batch:
            raise HTTPException(status_code=404, detail=f"批量导出任务不存在: {batch_id}")
        return {"ok": True, **_batch_view(batch, full=True)}


@router.get("/publish-packages/batches", summary="最近批量发布包任务（默认 10 条）")
def list_publish_batches(limit: int = 10):
    limit = max(1, min(int(limit or 10), 50))
    with _BATCH_LOCK:
        rows = sorted(_BATCHES.values(), key=lambda item: item.get("created_ts") or 0,
                      reverse=True)[:limit]
        views = [_batch_view(batch, full=False) for batch in rows]
    return {"ok": True, "count": len(views), "batches": views}


@router.post("/publish-packages/batch/{batch_id}/cancel",
             summary="取消批量导出（当前项完成后停止，未开始项取消）")
def cancel_publish_batch(batch_id: str):
    key = str(batch_id or "").strip()
    with _BATCH_LOCK:
        batch = _BATCHES.get(key)
        if not batch:
            raise HTTPException(status_code=404, detail=f"批量导出任务不存在: {batch_id}")
        if batch.get("status") not in ("queued", "running"):
            return {"ok": True, "batch_id": key, "status": batch.get("status"),
                    "message": "批量任务已结束，无需取消"}
        batch["status"] = "canceled"
        batch["finished_at"] = _now()
        batch["finished_ts"] = time.time()
        for item in batch.get("items") or []:
            if item.get("status") == "queued":
                item["status"] = "canceled"
                item["error"] = "用户取消"
                item["finished_at"] = _now()
                item["finished_ts"] = time.time()
        _persist(batch)
        view = _batch_view(batch, full=True)
    return {"ok": True, "batch_id": key, "status": "canceled", **view}


def _load_persisted_batches() -> None:
    """启动时加载；中断（queued/running）的 batch 与 items 标 failed。"""
    if not _BATCH_DIR.is_dir():
        return
    paths = sorted(_BATCH_DIR.glob("*.json"), key=lambda path: path.stat().st_mtime)
    for path in paths[-100:]:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(data, dict) or not data.get("id"):
            continue
        if data.get("status") in ("queued", "running"):
            data["status"] = "failed"
            data["error"] = "后端服务重启，批量导出中断（可重新发起）"
            data["finished_at"] = _now()
            data["finished_ts"] = time.time()
            data["current_index"] = -1
            for item in data.get("items") or []:
                if item.get("status") in ("queued", "building"):
                    item["status"] = "failed"
                    item["error"] = data["error"]
                    item["finished_at"] = _now()
                    item["finished_ts"] = time.time()
            _persist(data)
        _BATCHES[str(data["id"])] = data


_load_persisted_batches()
