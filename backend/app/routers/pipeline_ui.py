# -*- coding: utf-8 -*-
"""V18 pipeline 编排 API（`/api/pipeline/*`）+ 项目切换 3D 模板。

端点：
  POST /api/pipeline/run                启动一条 3D 模板全流程任务（单并发）
  GET  /api/pipeline/run/{job_id}       任务状态（stage/逐镜进度/日志尾/成片 URL）
  POST /api/pipeline/run/{job_id}/cancel 取消任务（杀子进程树）
  GET  /api/pipeline/runs               最近任务（默认 20 条）
  POST /api/projects/{pid}/apply-template 把项目切到 3D 模板对应的节目格式

实现：子进程 `python -m pipeline.templates3d_hq --template <tid> [--reuse-shots]`，
stdout 逐行解析阶段：
  `=== <tid>: N 镜 ...`      -> prepare
  `[i/N] <sid> ok (ref2va)`  -> generate / lipsync（原生口型）
  `=== 剪辑 ===`             -> assemble
  `完成: ...`                -> poster（进程退出后 done）
任务状态落 `outputs/pipeline_jobs/<job_id>.json`（刷新/重启后可查）。
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from .. import config, models
from ..db import get_db

_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

router = APIRouter(prefix="/api", tags=["pipeline-v18"])

#: 阶段表：id -> 中文标签（顺序即 stage_index）
STAGES: List[Dict[str, str]] = [
    {"id": "prepare", "label": "准备"},
    {"id": "generate", "label": "生成镜头"},
    {"id": "lipsync", "label": "原生口型"},
    {"id": "assemble", "label": "剪辑合成"},
    {"id": "poster", "label": "海报"},
    {"id": "done", "label": "完成"},
]
_STAGE_INDEX = {item["id"]: idx for idx, item in enumerate(STAGES)}
_STAGE_LABEL = {item["id"]: item["label"] for item in STAGES}

_TEMPLATE_ID_RE = re.compile(r"^[a-z][a-z0-9_]{1,60}$")
_SHOT_PROGRESS_RE = re.compile(r"\[(\d+)/(\d+)\]")
_PREPARE_RE = re.compile(r"===\s*[a-z0-9_]+:\s*(\d+)\s*镜")
_LOG_LIMIT = 400
_TAIL_LINES = 80
_RUNS_LIMIT = 20

_SEMANTIC_DIR = config.CONTRACT_DIR / "v5" / "cinema3d" / "templates3d"
_TEMPLATE_CONTRACT_DIR = config.CONTRACT_DIR / "v5" / "templates"
_CUSTOM_TEMPLATE_DIR = config.CONTRACT_DIR / "v5" / "custom" / "templates"
_CUSTOM_HQ_RUNNER = Path(__file__).resolve().parent / "custom_hq_runner.py"
_JOB_DIR = config.OUTPUTS_DIR / "pipeline_jobs"

_LOCK = threading.RLock()
_JOBS: Dict[str, Dict[str, Any]] = {}
_ACTIVE: Optional[str] = None

#: V25 批量队列：batch 内 items 串行复用 start_pipeline_job；与单发 run 互斥
_BATCH_DIR = config.OUTPUTS_DIR / "pipeline_batches"
_BATCHES: Dict[str, Dict[str, Any]] = {}
_BATCH_LOCK = threading.RLock()
_ACTIVE_BATCH: Optional[str] = None
_BATCH_LIMIT = 10


# --------------------------------------------------------------------------- #
# 模板 / 路径
# --------------------------------------------------------------------------- #
def _custom_template_ids() -> List[str]:
    """V21 可视化创建的 custom 模板（有 semantic brief 或 meta.three_d 标记）。"""
    if not _CUSTOM_TEMPLATE_DIR.is_dir():
        return []
    ids: List[str] = []
    for path in sorted(_CUSTOM_TEMPLATE_DIR.glob("*.json")):
        tid = path.stem
        if not _TEMPLATE_ID_RE.fullmatch(tid):
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        meta = data.get("meta") if isinstance(data, dict) and isinstance(data.get("meta"), dict) else {}
        if (_SEMANTIC_DIR / f"{tid}_semantic.json").is_file() or meta.get("three_d") \
                or meta.get("create_source") == "v21" \
                or str(data.get("render_type") or "").lower() in ("2d", "3d", "realistic"):
            ids.append(tid)
    return ids


def _template_contract(tid: str) -> Dict[str, Any]:
    merged: Dict[str, Any] = {}
    for path in (_TEMPLATE_CONTRACT_DIR / f"{tid}.json",
                 _CUSTOM_TEMPLATE_DIR / f"{tid}.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(data, dict):
            merged.update(data)
    return merged


_RENDER_TYPE_LABELS = {"2d": "2D 动画", "3d": "3D 电影", "realistic": "写实实拍"}
_RENDER_STYLE_LABELS = {"jp_anime": "日漫电影感", "guoman": "国漫国风",
                        "retro_cel": "复古赛璐璐", "soft_watercolor": "吉卜力治愈系"}


def _template_render_type(tid: str) -> str:
    """V41：模板节目类型；显式优先，缺省 real_* → realistic，否则 3d。"""
    raw = str(_template_contract(tid).get("render_type") or "").strip().lower()
    if raw in _RENDER_TYPE_LABELS:
        return raw
    return "realistic" if str(tid).startswith("real_") else "3d"


def _template_render_type_label(tid: str) -> str:
    render_type = _template_render_type(tid)
    return _RENDER_TYPE_LABELS.get(render_type, render_type)


def _template_render_style(tid: str) -> str | None:
    """V44：2D 风格子类型；非 2D 返回 None，2D 缺省 jp_anime。"""
    if _template_render_type(tid) != "2d":
        return None
    raw = str(_template_contract(tid).get("render_style") or "").strip().lower()
    return raw if raw in _RENDER_STYLE_LABELS else "jp_anime"


def _template_render_style_label(tid: str) -> str | None:
    style = _template_render_style(tid)
    return _RENDER_STYLE_LABELS.get(style) if style else None


def _is_custom_template(tid: str) -> bool:
    """custom 模板（不在内建 contract/v5/templates 中）需要 hq 启动包装来兼容 load_template。"""
    custom = _CUSTOM_TEMPLATE_DIR / f"{tid}.json"
    builtin = _TEMPLATE_CONTRACT_DIR / f"{tid}.json"
    return custom.is_file() and not builtin.is_file()


def _template_ids() -> List[str]:
    """3D 模板 id：pipeline.templates3d 优先 + contract semantic + V21 custom 模板。"""
    ids: List[str] = []
    try:
        from pipeline import templates3d  # noqa: PLC0415

        ids = [str(item).strip() for item in (getattr(templates3d, "TEMPLATES", None) or [])
               if str(item).strip()]
    except Exception:  # noqa: BLE001 - pipeline 不可用则只靠 contract
        ids = []
    if not ids and _SEMANTIC_DIR.is_dir():
        ids = sorted(path.name[: -len("_semantic.json")] for path in _SEMANTIC_DIR.glob("*_semantic.json"))
    for tid in _custom_template_ids():
        if tid not in ids:
            ids.append(tid)
    return [tid for tid in ids if _TEMPLATE_ID_RE.fullmatch(tid)]


def _semantic_path(tid: str) -> Path:
    return _SEMANTIC_DIR / f"{tid}_semantic.json"


def _semantic_shot_total(tid: str) -> int:
    try:
        data = json.loads(_semantic_path(tid).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return 0
    shots = data.get("shots") if isinstance(data, dict) else None
    if not isinstance(shots, list):
        return 0
    return sum(1 for item in shots if isinstance(item, dict) and item.get("prompt"))


def _existing_shots(tid: str) -> int:
    shots_dir = config.OUTPUTS_DIR / "templates_3d" / tid / "shots"
    if not shots_dir.is_dir():
        return 0
    return sum(1 for path in shots_dir.glob("*.mp4") if path.stat().st_size > 100_000)


def _final_url(tid: str) -> Optional[str]:
    render_type = _template_render_type(tid)
    candidates = [
        config.OUTPUTS_DIR / "templates_2d" / tid / "final.mp4",
        config.OUTPUTS_DIR / "templates_3d" / tid / "final.mp4",
    ]
    if render_type != "2d":
        candidates.reverse()
    for path in candidates:
        try:
            if path.is_file() and path.stat().st_size > 0:
                return config.media_url(path)
        except OSError:
            continue
    return None


def _template_2d_out(tid: str) -> Path:
    return config.OUTPUTS_DIR / "templates_2d" / tid / "final.mp4"


def _template_3d_out(tid: str) -> Path:
    return config.OUTPUTS_DIR / "templates_3d" / tid / "final.mp4"


def _sync_2d_outputs(tid: str) -> None:
    """V42：hq 输出在 templates_3d，成功后复制 final/poster 到 templates_2d 兼容目录。"""
    src_final = _template_3d_out(tid)
    if not src_final.is_file() or src_final.stat().st_size <= 0:
        return
    dst_dir = config.OUTPUTS_DIR / "templates_2d" / tid
    dst_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src_final, dst_dir / "final.mp4")
    src_poster = config.OUTPUTS_DIR / "templates_3d" / tid / "poster.jpg"
    if src_poster.is_file() and src_poster.stat().st_size > 0:
        shutil.copy2(src_poster, dst_dir / "poster.jpg")


def _generate_hq_command(tid: str, style_mode: str = "3d",
                         reuse: bool = False) -> List[str]:
    if _is_custom_template(tid):
        cmd = [sys.executable, str(_CUSTOM_HQ_RUNNER), "--template", tid]
    else:
        cmd = [sys.executable, "-m", "pipeline.templates3d_hq", "--template", tid]
    cmd.extend(["--style-mode", style_mode])
    if style_mode == "2d":
        cmd.extend(["--2d-style", _template_render_style(tid) or "jp_anime"])
    if reuse:
        cmd.append("--reuse-shots")
    return cmd


def _template_label(tid: str) -> Optional[str]:
    for path in (_TEMPLATE_CONTRACT_DIR / f"{tid}.json", _CUSTOM_TEMPLATE_DIR / f"{tid}.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        label = data.get("label") if isinstance(data, dict) else None
        if label:
            return str(label)
    return None


# --------------------------------------------------------------------------- #
# job 存储（内存 + outputs/pipeline_jobs/*.json）
# --------------------------------------------------------------------------- #
def _now_text() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def _persist(job: Dict[str, Any]) -> None:
    try:
        _JOB_DIR.mkdir(parents=True, exist_ok=True)
        target = _JOB_DIR / f"{job['id']}.json"
        tmp = target.with_name(f"{target.name}.tmp-{os.getpid()}-{threading.get_ident()}")
        tmp.write_text(json.dumps(job, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, target)
    except OSError:
        pass


def _elapsed_sec(job: Dict[str, Any]) -> float:
    started = job.get("started_ts")
    if not isinstance(started, (int, float)):
        return 0.0
    finished = job.get("finished_ts")
    end = float(finished) if isinstance(finished, (int, float)) else time.time()
    return round(max(0.0, end - float(started)), 1)


def _job_view(job: Dict[str, Any], *, tail: int = _TAIL_LINES) -> Dict[str, Any]:
    stages = STAGES
    stage_label = _STAGE_LABEL
    stage_index = _STAGE_INDEX
    stage = str(job.get("stage") or "prepare")
    total = int(job.get("total") or 0)
    done = int(job.get("done") or 0)
    percent = job.get("percent")
    if not isinstance(percent, (int, float)):
        percent = (done * 100.0 / total) if total > 0 else (100.0 if job.get("status") == "done" else 0.0)
    return {
        "id": job.get("id"),
        "template_id": job.get("template_id"),
        "template_label": job.get("template_label"),
        "render_type": job.get("render_type") or _template_render_type(str(job.get("template_id") or "")),
        "render_type_label": _template_render_type_label(str(job.get("template_id") or "")),
        "render_style": _template_render_style(str(job.get("template_id") or "")),
        "render_style_label": _template_render_style_label(str(job.get("template_id") or "")),
        "status": job.get("status"),
        "stage": stage,
        "stage_index": int(stage_index.get(stage, 0)),
        "stage_label": stage_label.get(stage, stage),
        "progress": {"done": max(0, done), "total": max(0, total)},
        "percent": round(max(0.0, min(100.0, float(percent))), 1),
        "log_tail": (job.get("log") or [])[-tail:],
        "elapsed_sec": _elapsed_sec(job),
        "final_url": _final_url(str(job.get("template_id") or "")) if job.get("status") == "done" else None,
        "error": job.get("error"),
        "reuse_shots": bool(job.get("reuse_shots")),
        "created_at": job.get("created_at"),
        "finished_at": job.get("finished_at"),
        "stages": [item["id"] for item in stages],
        "stage_labels": dict(stage_label),
    }


def _append_log(job: Dict[str, Any], raw: str) -> None:
    """追加一行日志并解析阶段/逐镜进度（解析的是去掉 `[HH:MM:SS]` 前缀后的正文）。"""
    line = raw.rstrip("\r\n")
    if not line:
        return
    log: List[str] = job.setdefault("log", [])
    log.append(line[:800])
    if len(log) > _LOG_LIMIT:
        del log[: len(log) - _LOG_LIMIT]

    body = line
    stamp = re.match(r"^\[\d{2}:\d{2}:\d{2}\]\s*", body)
    if stamp:
        body = body[stamp.end():]
    changed = False
    shot = _SHOT_PROGRESS_RE.search(body)
    if shot:
        done, total = int(shot.group(1)), int(shot.group(2))
        if job.get("done") != done or job.get("total") != total:
            job["done"], job["total"] = done, total
            changed = True
        new_stage = "lipsync" if "ref2va" in body else "generate"
        if job.get("stage") != new_stage:
            job["stage"] = new_stage
            changed = True
    elif "=== 剪辑 ===" in body:
        if job.get("stage") != "assemble":
            job["stage"] = "assemble"
            changed = True
    elif _PREPARE_RE.search(body):
        match = _PREPARE_RE.search(body)
        if job.get("stage") != "prepare":
            job["stage"] = "prepare"
            changed = True
        if match and not job.get("total"):
            job["total"] = int(match.group(1))
            changed = True
    elif body.startswith("完成"):
        if job.get("stage") != "poster":
            job["stage"] = "poster"
            changed = True
    if changed and job.get("status") == "running":
        _persist(job)


def _run_job(job_id: str) -> None:
    global _ACTIVE
    job = _JOBS.get(job_id)
    if not job:
        return
    if job.get("cancel_requested") or job.get("status") == "canceled":
        with _LOCK:
            job["status"] = "canceled"
            job["error"] = job.get("error") or "用户取消"
            job["finished_at"] = _now_text()
            job["finished_ts"] = time.time()
            _persist(job)
            if _ACTIVE == job_id:
                _ACTIVE = None
        return
    tid = str(job.get("template_id") or "")
    is_2d = str(job.get("render_type") or "") == "2d"
    # V42：2D 也用 H3 hq，仅追加 --style-mode 2d；custom 走 wrapper 透传
    cmd = _generate_hq_command(tid, "2d" if is_2d else "3d",
                               bool(job.get("reuse_shots")))
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUNBUFFERED"] = "1"
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
    try:
        proc = subprocess.Popen(
            cmd,
            cwd=str(_REPO_ROOT),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            env=env,
            creationflags=creationflags,
        )
    except Exception as exc:  # noqa: BLE001
        with _LOCK:
            job["status"] = "failed"
            job["error"] = f"启动子进程失败: {exc}"
            job["finished_at"] = _now_text()
            job["finished_ts"] = time.time()
            _persist(job)
            if _ACTIVE == job_id:
                _ACTIVE = None
        return
    with _LOCK:
        job["pid"] = proc.pid
        if job.get("cancel_requested") or job.get("status") == "canceled":
            # 启动与取消竞态：进程刚起来就被取消 → 立刻杀掉，不进入 running
            job["status"] = "canceled"
            job["error"] = job.get("error") or "用户取消"
            job["finished_at"] = _now_text()
            job["finished_ts"] = time.time()
            _persist(job)
            if _ACTIVE == job_id:
                _ACTIVE = None
            kill_now = True
        else:
            job["status"] = "running"
            job["started_ts"] = time.time()
            job["started_at"] = _now_text()
            job["stage"] = job.get("stage") or ("render" if is_2d else "prepare")
            kill_now = False
            _persist(job)
    if kill_now:
        try:
            if os.name == "nt":
                subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                               capture_output=True, timeout=30)
            else:
                proc.kill()
        except Exception:  # noqa: BLE001
            pass
        try:
            proc.wait(timeout=10)
        except Exception:  # noqa: BLE001
            pass
        return
    try:
        if proc.stdout is not None:
            for line in proc.stdout:
                with _LOCK:
                    _append_log(job, line)
                    if job.get("status") == "canceled":
                        continue
        code = proc.wait()
    except Exception as exc:  # noqa: BLE001 - 读流失败也要收尾
        with _LOCK:
            job["error"] = f"读取任务日志失败: {exc}"
        code = proc.poll() if proc.poll() is not None else -1
    with _LOCK:
        job["returncode"] = code
        job["finished_at"] = _now_text()
        job["finished_ts"] = time.time()
        if job.get("status") == "canceled" or job.get("cancel_requested"):
            job["status"] = "canceled"
            job["error"] = job.get("error") or "任务已取消"
        elif code == 0:
            if is_2d:
                _sync_2d_outputs(tid)
            if is_2d and (not _template_2d_out(tid).is_file()
                          or _template_2d_out(tid).stat().st_size <= 0):
                job["status"] = "failed"
                job["stage"] = "failed"
                job["error"] = "2D 渲染结束但未找到有效成片 outputs/templates_2d/<tid>/final.mp4"
            else:
                job["status"] = "done"
                job["stage"] = "done"
                job["percent"] = 100.0
                total = int(job.get("total") or 0)
                job["done"] = total
        else:
            job["status"] = "failed"
            if is_2d:
                job["stage"] = "failed"
            tail = " / ".join((job.get("log") or [])[-5:])
            job["error"] = job.get("error") or f"进程退出码 {code}" + (f"：{tail[:600]}" if tail else "")
        _persist(job)
        if _ACTIVE == job_id:
            _ACTIVE = None


# --------------------------------------------------------------------------- #
# 启动时恢复（重启后把中断任务标 failed，避免永远 running）
# --------------------------------------------------------------------------- #
def _load_persisted_jobs() -> None:
    if not _JOB_DIR.is_dir():
        return
    paths = sorted(_JOB_DIR.glob("*.json"), key=lambda p: p.stat().st_mtime)
    for path in paths[-50:]:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(data, dict) or not data.get("id"):
            continue
        if data.get("status") in ("queued", "running"):
            data["status"] = "failed"
            data["error"] = "后端服务重启，任务中断（可重新发起）"
            data["finished_at"] = _now_text()
            data["finished_ts"] = time.time()
            _persist(data)
        _JOBS[str(data["id"])] = data


_load_persisted_jobs()


# --------------------------------------------------------------------------- #
# 请求模型 / 端点
# --------------------------------------------------------------------------- #
def active_pipeline_job() -> Optional[Dict[str, Any]]:
    """当前 queued/running 的 pipeline job（供概念图端点判断 GPU/3D 互斥）。"""
    with _LOCK:
        if not _ACTIVE:
            return None
        job = _JOBS.get(_ACTIVE) or {}
        if job.get("status") in ("queued", "running"):
            return _job_view(job, tail=0)
    return None


class PipelineRunBody(BaseModel):
    template_id: str
    reuse_shots: bool = False
    dry_run: bool = False  # V21：只做校验（不启动子进程/不占 GPU），供自测/预检使用


class ApplyTemplateBody(BaseModel):
    template_id: str


def start_pipeline_job(template_id: str, *, reuse_shots: bool = False,
                       dry_run: bool = False,
                       batch_id: Optional[str] = None) -> Dict[str, Any]:
    """启动一条 pipeline 任务（供 POST /api/pipeline/run、创建模板 auto_generate、批量队列复用）。

    行为与 POST /api/pipeline/run 一致：参数错误 400；已有 running 任务 409；
    dry_run=True 只校验、不创建/不启动子进程。batch_id 非空表示由批量队列调用
    （batch 运行中单发 run 会被 409 拦截，反之亦然）。
    """
    global _ACTIVE
    tid = str(template_id or "").strip()
    if not _TEMPLATE_ID_RE.fullmatch(tid):
        raise HTTPException(status_code=400, detail="template_id 必须是合法模板 id")
    if tid not in _template_ids():
        raise HTTPException(status_code=400, detail=f"未知模板: {tid}")
    render_type = _template_render_type(tid)
    is_2d = render_type == "2d"
    if not _semantic_path(tid).is_file():
        raise HTTPException(status_code=400, detail=f"模板语义 brief 不存在: {tid}_semantic.json")
    reuse = bool(reuse_shots)
    if reuse and _existing_shots(tid) <= 0:
        raise HTTPException(status_code=400,
                            detail=f"模板 {tid} 没有可复用的镜头（shots/ 为空），不能 reuse_shots")
    if dry_run:
        # 只做校验：返回 202 语义的 dry-run 结果，不创建/不启动任何子进程（不占 GPU）
        command = _generate_hq_command(tid, "2d" if is_2d else "3d", reuse)
        return {
            "ok": True,
            "job_id": None,
            "template_id": tid,
            "template_label": _template_label(tid),
            "render_type": render_type,
            "render_type_label": _template_render_type_label(tid),
            "render_style": _template_render_style(tid),
            "render_style_label": _template_render_style_label(tid),
            "reuse_shots": reuse,
            "status": "dry_run",
            "dry_run": True,
            "stages": [item["id"] for item in STAGES],
            "stage_labels": dict(_STAGE_LABEL),
            "command": command,
        }
    # V25：批量队列与单发 run 互斥
    with _BATCH_LOCK:
        active_batch = _ACTIVE_BATCH
        if batch_id is not None:
            if active_batch != batch_id:
                raise HTTPException(status_code=409, detail={
                    "message": "批量任务已不在运行（可能被取消/结束）",
                    "reason": "batch_mismatch", "batch_id": active_batch,
                })
        elif active_batch:
            batch_meta = _BATCHES.get(active_batch) or {}
            if batch_meta.get("status") in ("queued", "running"):
                raise HTTPException(status_code=409, detail={
                    "message": "批量任务运行中，暂不能单发 pipeline 任务",
                    "reason": "batch_running", "batch_id": active_batch,
                })
    with _LOCK:
        if _ACTIVE:
            active = _JOBS.get(_ACTIVE) or {}
            if active.get("status") in ("queued", "running"):
                raise HTTPException(status_code=409, detail={
                    "message": "已有 pipeline 任务在运行（单并发）",
                    "job_id": _ACTIVE,
                    "template_id": active.get("template_id"),
                })
            _ACTIVE = None
        job_id = "pl_" + uuid.uuid4().hex[:12]
        job: Dict[str, Any] = {
            "id": job_id,
            "template_id": tid,
            "template_label": _template_label(tid),
            "render_type": render_type,
            "reuse_shots": reuse,
            "status": "queued",
            "stage": "prepare",
            "percent": 0.0,
            "total": _semantic_shot_total(tid) or _existing_shots(tid),
            "done": 0,
            "log": [],
            "error": None,
            "created_at": _now_text(),
            "created_ts": time.time(),
            "finished_at": None,
            "finished_ts": None,
        }
        _JOBS[job_id] = job
        _ACTIVE = job_id
        _persist(job)
    # 竞态兜底：注册 job 后若批量已抢到活动权，则回滚本 job（避免 batch/单发同时运行）
    if batch_id is None:
        with _BATCH_LOCK:
            race_batch = _ACTIVE_BATCH
            race_meta = (_BATCHES.get(race_batch) or {}) if race_batch else {}
        if race_batch and race_meta.get("status") in ("queued", "running"):
            with _LOCK:
                job["status"] = "failed"
                job["error"] = "批量任务已启动，本次单发任务取消"
                job["finished_at"] = _now_text()
                job["finished_ts"] = time.time()
                _persist(job)
                if _ACTIVE == job_id:
                    _ACTIVE = None
            raise HTTPException(status_code=409, detail={
                "message": "批量任务运行中，暂不能单发 pipeline 任务",
                "reason": "batch_running", "batch_id": race_batch,
            })
    try:
        threading.Thread(target=_run_job, args=(job_id,), daemon=True,
                         name=f"pipeline-{job_id}").start()
    except Exception as exc:  # noqa: BLE001 - 线程启动失败不能留下卡死的 queued job
        with _LOCK:
            job["status"] = "failed"
            job["error"] = f"启动任务线程失败: {exc}"
            job["finished_at"] = _now_text()
            job["finished_ts"] = time.time()
            _persist(job)
            if _ACTIVE == job_id:
                _ACTIVE = None
        raise HTTPException(status_code=500, detail=f"启动任务失败: {exc}") from exc
    return {
        "ok": True,
        "job_id": job_id,
        "template_id": tid,
        "template_label": _template_label(tid),
        "render_type": render_type,
        "render_type_label": _template_render_type_label(tid),
        "render_style": _template_render_style(tid),
        "render_style_label": _template_render_style_label(tid),
        "reuse_shots": reuse,
        "status": "queued",
        "stages": [item["id"] for item in STAGES],
        "stage_labels": dict(_STAGE_LABEL),
    }


@router.post("/pipeline/run", status_code=202, summary="启动 3D 模板全流程任务（单并发）")
def start_pipeline_run(payload: PipelineRunBody):
    return start_pipeline_job(payload.template_id, reuse_shots=payload.reuse_shots,
                              dry_run=payload.dry_run)


@router.get("/pipeline/run/{job_id}", summary="pipeline 任务状态（阶段/进度/日志尾/成片）")
def get_pipeline_run(job_id: str):
    with _LOCK:
        job = _JOBS.get(str(job_id or "").strip())
        if not job:
            raise HTTPException(status_code=404, detail=f"任务不存在: {job_id}")
        return _job_view(job)


@router.post("/pipeline/run/{job_id}/cancel", summary="取消 pipeline 任务")
def cancel_pipeline_run(job_id: str):
    global _ACTIVE
    key = str(job_id or "").strip()
    with _LOCK:
        job = _JOBS.get(key)
        if not job:
            raise HTTPException(status_code=404, detail=f"任务不存在: {job_id}")
        if job.get("status") not in ("queued", "running"):
            return {"ok": True, "job_id": key, "status": job.get("status"),
                    "message": "任务已结束，无需取消"}
        job["cancel_requested"] = True
        job["status"] = "canceled"
        job["stage"] = job.get("stage") or "prepare"
        job["finished_at"] = _now_text()
        job["finished_ts"] = time.time()
        job["error"] = job.get("error") or "用户取消"
        pid = job.get("pid")
        _persist(job)
    if isinstance(pid, int) and pid > 0:
        try:
            if os.name == "nt":
                subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)],
                               capture_output=True, timeout=30)
            else:
                os.kill(pid, 15)
        except Exception:  # noqa: BLE001 - 取消失败不阻塞响应，进程收尾线程会兜底
            pass
    return {"ok": True, "job_id": key, "status": "canceled"}


@router.get("/pipeline/runs", summary="最近 pipeline 任务（默认 20 条）")
def list_pipeline_runs(limit: int = 20):
    limit = max(1, min(int(limit or 20), 50))
    with _LOCK:
        jobs = sorted(_JOBS.values(), key=lambda item: item.get("created_ts") or 0, reverse=True)
        rows = [_job_view(job, tail=20) for job in jobs[:limit]]
    return {"ok": True, "count": len(rows), "runs": rows}


# --------------------------------------------------------------------------- #
# V25 批量队列（多模板串行排队 / 进度 / 取消 / 持久化）
# --------------------------------------------------------------------------- #
class BatchCreateBody(BaseModel):
    template_ids: List[str]
    reuse_shots: bool = False
    stop_on_error: bool = False


def _batch_persist(batch: Dict[str, Any]) -> None:
    try:
        _BATCH_DIR.mkdir(parents=True, exist_ok=True)
        target = _BATCH_DIR / f"{batch['id']}.json"
        tmp = target.with_name(f"{target.name}.tmp-{os.getpid()}-{threading.get_ident()}")
        tmp.write_text(json.dumps(batch, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, target)
    except OSError:
        pass


def _batch_elapsed(batch: Dict[str, Any]) -> float:
    created = batch.get("created_ts")
    if not isinstance(created, (int, float)):
        return 0.0
    finished = batch.get("finished_ts")
    end = float(finished) if isinstance(finished, (int, float)) else time.time()
    return round(max(0.0, end - float(created)), 1)


def _batch_summary(batch: Dict[str, Any]) -> Dict[str, int]:
    counts: Dict[str, int] = {key: 0 for key in
                              ("queued", "running", "done", "failed", "canceled", "skipped")}
    for item in batch.get("items") or []:
        status = str(item.get("status") or "queued")
        counts[status] = counts.get(status, 0) + 1
    counts["total"] = len(batch.get("items") or [])
    return counts


def _batch_item_view(item: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "index": item.get("index"),
        "template_id": item.get("template_id"),
        "label": item.get("label"),
        "status": item.get("status"),
        "job_id": item.get("job_id"),
        "stage": item.get("stage"),
        "stage_index": item.get("stage_index"),
        "progress": item.get("progress") or {"done": 0, "total": 0},
        "final_url": item.get("final_url"),
        "error": item.get("error"),
        "started_at": item.get("started_at"),
        "finished_at": item.get("finished_at"),
    }


def _batch_view(batch: Dict[str, Any], *, full: bool = True) -> Dict[str, Any]:
    items = [_batch_item_view(item) for item in batch.get("items") or []]
    if not full:
        items = [{key: item.get(key) for key in
                  ("index", "template_id", "label", "status", "job_id")} for item in items]
    return {
        "id": batch.get("id"),
        "status": batch.get("status"),
        "current_index": batch.get("current_index", -1),
        "total": len(items),
        "created_at": batch.get("created_at"),
        "finished_at": batch.get("finished_at"),
        "elapsed_sec": _batch_elapsed(batch),
        "reuse_shots": bool(batch.get("reuse_shots")),
        "stop_on_error": bool(batch.get("stop_on_error")),
        "error": batch.get("error"),
        "summary": _batch_summary(batch),
        "template_ids": [item.get("template_id") for item in batch.get("items") or []],
        "items": items,
    }


def _cancel_pipeline_job_safe(job_id: Optional[str]) -> Optional[Dict[str, Any]]:
    if not job_id:
        return None
    try:
        return cancel_pipeline_run(str(job_id))
    except HTTPException:
        return None
    except Exception:  # noqa: BLE001
        return None


def _fail_batch_remaining(batch: Dict[str, Any], reason: str) -> None:
    with _BATCH_LOCK:
        for other in batch.get("items") or []:
            if other.get("status") == "queued":
                other["status"] = "skipped"
                other["error"] = reason
            elif other.get("status") == "running":
                other["status"] = "failed"
                other["error"] = reason
        batch["status"] = "failed"
        batch["error"] = reason
        batch["finished_at"] = _now_text()
        batch["finished_ts"] = time.time()
        batch["current_index"] = -1
        _batch_persist(batch)


def _run_batch(batch_id: str) -> None:
    """批量执行线程：串行等待 pipeline 空闲 → start_pipeline_job → 轮询到 done/failed/canceled。"""
    global _ACTIVE_BATCH
    batch = _BATCHES.get(batch_id)
    if not batch:
        return
    with _BATCH_LOCK:
        if batch.get("status") != "queued":
            return
        batch["status"] = "running"
        batch["started_at"] = _now_text()
        batch["started_ts"] = time.time()
        _batch_persist(batch)
    try:
        while True:
            with _BATCH_LOCK:
                if batch.get("status") != "running":
                    break
                item = next((it for it in batch.get("items") or []
                             if it.get("status") == "queued"), None)
                if item is None:
                    break
                item_index = item.get("index")
            # 等待 pipeline 空闲（同时响应 cancel）
            while True:
                with _BATCH_LOCK:
                    if batch.get("status") != "running":
                        break
                with _LOCK:
                    active = (_JOBS.get(_ACTIVE) or {}) if _ACTIVE else {}
                    busy = active.get("status") in ("queued", "running")
                if not busy:
                    break
                time.sleep(0.5)
            with _BATCH_LOCK:
                if batch.get("status") != "running":
                    break
                item["status"] = "running"
                item["started_at"] = _now_text()
                item["started_ts"] = time.time()
                item["error"] = None
                batch["current_index"] = item_index
                _batch_persist(batch)
            # 启动 job（若单发刚抢占则等待重试；取消则立即停）
            result: Optional[Dict[str, Any]] = None
            start_error: Optional[str] = None
            attempts = 0
            canceled_before_start = False
            while result is None:
                with _BATCH_LOCK:
                    if batch.get("status") != "running":
                        canceled_before_start = True
                        break
                attempts += 1
                try:
                    result = start_pipeline_job(
                        str(item.get("template_id") or ""),
                        reuse_shots=bool(batch.get("reuse_shots")),
                        dry_run=False, batch_id=batch_id)
                except HTTPException as exc:
                    detail = exc.detail if isinstance(exc.detail, dict) else {"message": str(exc.detail)}
                    reason = str(detail.get("reason") or "")
                    if exc.status_code == 409 and reason in ("pipeline_running", "batch_mismatch"):
                        if reason == "batch_mismatch":
                            start_error = "批量任务已结束"
                            canceled_before_start = True
                            break
                        if attempts > 240:
                            start_error = "等待 pipeline 空闲超时"
                            break
                        time.sleep(0.5)
                        continue
                    start_error = str(detail.get("message") or detail)
                    break
                except Exception as exc:  # noqa: BLE001
                    start_error = f"{type(exc).__name__}: {exc}"
                    break
            if canceled_before_start:
                with _BATCH_LOCK:
                    item["status"] = "canceled"
                    item["error"] = "用户取消"
                    item["finished_at"] = _now_text()
                    item["finished_ts"] = time.time()
                    batch["current_index"] = -1
                    _batch_persist(batch)
                break
            if result is None:
                with _BATCH_LOCK:
                    item["status"] = "failed"
                    item["error"] = start_error or "启动失败"
                    item["finished_at"] = _now_text()
                    item["finished_ts"] = time.time()
                    batch["current_index"] = -1
                    _batch_persist(batch)
                if bool(batch.get("stop_on_error")):
                    _fail_batch_remaining(batch, f"{item.get('template_id')} 启动失败: {start_error}")
                    return
                continue
            job_id = result.get("job_id")
            with _BATCH_LOCK:
                canceled_now = batch.get("status") == "canceled"
                item["job_id"] = job_id
                if canceled_now:
                    item["status"] = "canceled"
                    item["error"] = "用户取消"
                    item["finished_at"] = _now_text()
                    item["finished_ts"] = time.time()
                    batch["current_index"] = -1
                else:
                    item["status"] = "running"
                _batch_persist(batch)
            if canceled_now:
                _cancel_pipeline_job_safe(job_id)
                break
            poll_count = 0
            while True:
                with _BATCH_LOCK:
                    canceled = batch.get("status") == "canceled"
                if canceled:
                    _cancel_pipeline_job_safe(job_id)
                    with _BATCH_LOCK:
                        if item.get("status") == "running":
                            item["status"] = "canceled"
                            item["error"] = "用户取消"
                            item["finished_at"] = _now_text()
                            item["finished_ts"] = time.time()
                        batch["current_index"] = -1
                        _batch_persist(batch)
                    break
                view: Optional[Dict[str, Any]] = None
                with _LOCK:
                    job = _JOBS.get(str(job_id)) if job_id else None
                    if isinstance(job, dict):
                        view = _job_view(job, tail=0)
                if view is not None:
                    status = str(view.get("status") or "")
                    with _BATCH_LOCK:
                        item["stage"] = view.get("stage")
                        item["stage_index"] = view.get("stage_index")
                        item["progress"] = view.get("progress") or item.get("progress")
                        if status in ("done", "failed", "canceled"):
                            item["status"] = status
                            item["final_url"] = view.get("final_url")
                            item["error"] = view.get("error")
                            item["finished_at"] = _now_text()
                            item["finished_ts"] = time.time()
                            batch["current_index"] = -1
                            _batch_persist(batch)
                    if status in ("done", "failed", "canceled"):
                        break
                poll_count += 1
                if poll_count % 10 == 0:
                    _batch_persist(batch)
                time.sleep(0.5)
            with _BATCH_LOCK:
                item_status = item.get("status")
                batch_status = batch.get("status")
            if batch_status == "canceled":
                break
            if item_status == "failed" and bool(batch.get("stop_on_error")):
                _fail_batch_remaining(batch, f"{item.get('template_id')} 失败")
                return
        # 收尾
        with _BATCH_LOCK:
            if batch.get("status") not in ("canceled", "failed"):
                items = batch.get("items") or []
                if any(it.get("status") in ("failed", "skipped") for it in items):
                    batch["status"] = "failed"
                    batch["error"] = batch.get("error") or "部分模板失败"
                else:
                    batch["status"] = "done"
                batch["finished_at"] = _now_text()
                batch["finished_ts"] = time.time()
                batch["current_index"] = -1
                _batch_persist(batch)
    except Exception as exc:  # noqa: BLE001 - 批量线程异常也要收尾
        with _BATCH_LOCK:
            batch["status"] = "failed"
            batch["error"] = f"批量执行异常: {type(exc).__name__}: {exc}"
            for item in batch.get("items") or []:
                if item.get("status") in ("queued", "running"):
                    item["status"] = "failed"
                    item["error"] = batch["error"]
                    item["finished_at"] = _now_text()
            batch["finished_at"] = _now_text()
            batch["finished_ts"] = time.time()
            batch["current_index"] = -1
            _batch_persist(batch)
    finally:
        with _BATCH_LOCK:
            if _ACTIVE_BATCH == batch_id:
                _ACTIVE_BATCH = None


@router.post("/pipeline/batch", status_code=202, summary="启动批量 pipeline 队列（多模板串行）")
def start_pipeline_batch(payload: BatchCreateBody):
    global _ACTIVE_BATCH
    raw_ids = payload.template_ids if isinstance(payload.template_ids, list) else []
    ids: List[str] = []
    for raw in raw_ids:
        tid = str(raw or "").strip()
        if not tid:
            continue
        if not _TEMPLATE_ID_RE.fullmatch(tid):
            raise HTTPException(status_code=400, detail=f"非法模板 id: {tid}")
        if tid not in _template_ids():
            raise HTTPException(status_code=400, detail=f"未知模板: {tid}")
        if tid not in ids:
            ids.append(tid)
    if not ids:
        raise HTTPException(status_code=400, detail="template_ids 不能为空")
    if len(ids) > 20:
        raise HTTPException(status_code=400, detail="一次批量最多 20 个模板")
    # 单发 run 运行中 → 409
    with _LOCK:
        active_job_id: Optional[str] = None
        if _ACTIVE:
            active = _JOBS.get(_ACTIVE) or {}
            if active.get("status") in ("queued", "running"):
                active_job_id = _ACTIVE
    if active_job_id:
        raise HTTPException(status_code=409, detail={
            "message": "已有 pipeline 任务运行中，不能启动批量任务",
            "reason": "pipeline_running", "job_id": active_job_id,
        })
    with _BATCH_LOCK:
        if _ACTIVE_BATCH:
            current = _BATCHES.get(_ACTIVE_BATCH) or {}
            if current.get("status") in ("queued", "running"):
                raise HTTPException(status_code=409, detail={
                    "message": "已有批量任务在运行",
                    "reason": "batch_running", "batch_id": _ACTIVE_BATCH,
                })
            _ACTIVE_BATCH = None
    batch_id = "pb_" + uuid.uuid4().hex[:12]
    items = [{
        "index": index,
        "template_id": tid,
        "label": _template_label(tid) or tid,
        "status": "queued",
        "job_id": None,
        "stage": None,
        "stage_index": 0,
        "progress": {"done": 0, "total": 0},
        "final_url": None,
        "error": None,
        "started_at": None,
        "finished_at": None,
    } for index, tid in enumerate(ids)]
    batch: Dict[str, Any] = {
        "id": batch_id,
        "status": "queued",
        "current_index": -1,
        "items": items,
        "total": len(items),
        "reuse_shots": bool(payload.reuse_shots),
        "stop_on_error": bool(payload.stop_on_error),
        "error": None,
        "created_at": _now_text(),
        "created_ts": time.time(),
        "started_at": None,
        "finished_at": None,
        "finished_ts": None,
    }
    with _BATCH_LOCK:
        _BATCHES[batch_id] = batch
        _ACTIVE_BATCH = batch_id
    _batch_persist(batch)
    # 竞态兜底：设置 active batch 后若单发已注册 job，则回滚 batch 并 409
    with _LOCK:
        race_job = (_JOBS.get(_ACTIVE) or {}) if _ACTIVE else {}
        race_job_id = _ACTIVE if race_job.get("status") in ("queued", "running") else None
    if race_job_id:
        with _BATCH_LOCK:
            batch["status"] = "canceled"
            batch["error"] = "检测到单发任务，批量任务取消"
            batch["finished_at"] = _now_text()
            batch["finished_ts"] = time.time()
            _BATCHES.pop(batch_id, None)
            if _ACTIVE_BATCH == batch_id:
                _ACTIVE_BATCH = None
        _batch_persist(batch)
        raise HTTPException(status_code=409, detail={
            "message": "已有 pipeline 任务运行中，不能启动批量任务",
            "reason": "pipeline_running", "job_id": race_job_id,
        })
    try:
        threading.Thread(target=_run_batch, args=(batch_id,), daemon=True,
                         name=f"batch-{batch_id}").start()
    except Exception as exc:  # noqa: BLE001
        with _BATCH_LOCK:
            batch["status"] = "failed"
            batch["error"] = f"启动批量线程失败: {exc}"
            batch["finished_at"] = _now_text()
            batch["finished_ts"] = time.time()
            if _ACTIVE_BATCH == batch_id:
                _ACTIVE_BATCH = None
        _batch_persist(batch)
        raise HTTPException(status_code=500, detail=f"启动批量任务失败: {exc}") from exc
    return {
        "ok": True,
        "batch_id": batch_id,
        "status": "queued",
        "total": len(items),
        "reuse_shots": bool(payload.reuse_shots),
        "stop_on_error": bool(payload.stop_on_error),
        "created_at": batch["created_at"],
        "items": [{"template_id": item["template_id"], "label": item["label"],
                   "status": "queued"} for item in items],
    }


@router.get("/pipeline/batch/{batch_id}", summary="批量任务状态（items + summary + 当前进度）")
def get_pipeline_batch(batch_id: str):
    with _BATCH_LOCK:
        batch = _BATCHES.get(str(batch_id or "").strip())
        if not batch:
            raise HTTPException(status_code=404, detail=f"批量任务不存在: {batch_id}")
        return {"ok": True, **_batch_view(batch, full=True)}


@router.get("/pipeline/batches", summary="最近批量任务（默认 10 条）")
def list_pipeline_batches(limit: int = 10):
    limit = max(1, min(int(limit or 10), 50))
    with _BATCH_LOCK:
        rows = sorted(_BATCHES.values(), key=lambda item: item.get("created_ts") or 0,
                      reverse=True)[:limit]
        views = [_batch_view(batch, full=False) for batch in rows]
    return {"ok": True, "count": len(views), "batches": views}


@router.post("/pipeline/batch/{batch_id}/cancel", summary="取消批量任务（当前 job + 未开始项）")
def cancel_pipeline_batch(batch_id: str):
    global _ACTIVE_BATCH
    key = str(batch_id or "").strip()
    with _BATCH_LOCK:
        batch = _BATCHES.get(key)
        if not batch:
            raise HTTPException(status_code=404, detail=f"批量任务不存在: {batch_id}")
        if batch.get("status") not in ("queued", "running"):
            return {"ok": True, "batch_id": key, "status": batch.get("status"),
                    "message": "批量任务已结束，无需取消"}
        batch["status"] = "canceled"
        batch["error"] = "用户取消"
        batch["finished_at"] = _now_text()
        batch["finished_ts"] = time.time()
        batch["current_index"] = -1
        running_job_id: Optional[str] = None
        for item in batch.get("items") or []:
            if item.get("status") == "queued":
                item["status"] = "canceled"
                item["error"] = "用户取消"
                item["finished_at"] = _now_text()
                item["finished_ts"] = time.time()
            elif item.get("status") == "running":
                running_job_id = item.get("job_id")
                item["error"] = "用户取消"
        _batch_persist(batch)
    if running_job_id:
        _cancel_pipeline_job_safe(running_job_id)
    with _BATCH_LOCK:
        for item in batch.get("items") or []:
            if item.get("status") == "running":
                item["status"] = "canceled"
                item["error"] = "用户取消"
                item["finished_at"] = _now_text()
                item["finished_ts"] = time.time()
        _batch_persist(batch)
        if _ACTIVE_BATCH == key:
            _ACTIVE_BATCH = None
    return {"ok": True, "batch_id": key, "status": "canceled",
            "canceled_job_id": running_job_id, **_batch_view(batch, full=False)}


def _load_persisted_batches() -> None:
    """启动时加载 batch；把中断（queued/running）的 batch/items 标 failed。"""
    if not _BATCH_DIR.is_dir():
        return
    paths = sorted(_BATCH_DIR.glob("*.json"), key=lambda path: path.stat().st_mtime)
    for path in paths[-50:]:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(data, dict) or not data.get("id"):
            continue
        if data.get("status") in ("queued", "running"):
            data["status"] = "failed"
            data["error"] = "后端服务重启，批量任务中断（可重新发起）"
            data["finished_at"] = _now_text()
            data["finished_ts"] = time.time()
            data["current_index"] = -1
            for item in data.get("items") or []:
                if item.get("status") in ("queued", "running"):
                    item["status"] = "failed"
                    item["error"] = data["error"]
                    item["finished_at"] = _now_text()
                    item["finished_ts"] = time.time()
            _batch_persist(data)
        _BATCHES[str(data["id"])] = data


@router.post("/projects/{pid}/apply-template", summary="把项目切到 3D 模板对应的节目格式")
def apply_template(pid: str, payload: ApplyTemplateBody, db: Session = Depends(get_db)):
    project = db.get(models.Project, pid)
    if project is None:
        raise HTTPException(status_code=404, detail=f"项目不存在: {pid}")
    tid = str(payload.template_id or "").strip()
    if not _TEMPLATE_ID_RE.fullmatch(tid):
        raise HTTPException(status_code=400, detail="template_id 必须是合法模板 id")
    if tid not in _template_ids():
        raise HTTPException(status_code=400, detail=f"未知模板: {tid}")

    direct = config.FORMATS_DIR / f"{tid}_5min.json"
    mapped = "direct"
    if direct.is_file():
        format_id = direct.stem
        note = "已切换到与模板同名的节目格式"
    else:
        candidates = sorted(config.FORMATS_DIR.glob("*.json"))
        if not candidates:
            raise HTTPException(status_code=400, detail="contract/formats 下没有可用的节目格式")
        format_id = candidates[0].stem
        mapped = "fallback_first"
        note = ("contract/formats 中没有该模板的同名格式，已回落到首个可用格式；"
                "项目表没有 template_id 字段，模板选择请由前端/上层保存")
    project.format_id = format_id
    db.commit()
    db.refresh(project)
    return {
        "ok": True,
        "project_id": pid,
        "format_id": format_id,
        "template_id": tid,
        "template_label": _template_label(tid),
        "mapped": mapped,
        "note": note,
    }


# 启动时恢复 batch 队列（中断的 batch 标 failed；须在模块加载末尾执行）
_load_persisted_batches()
