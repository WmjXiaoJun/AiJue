# -*- coding: utf-8 -*-
"""V8「16 个 V5 模板 3D 化」后端 API（前缀 `/api/cinema/templates`）。

看板 / 批量操作 / 成片播放需要的全部真实文件都在这里读，不造假：
- 进度     outputs/templates_3d/progress.json（pipeline/templates3d.py 写）
- 逐镜片   outputs/templates_3d/<id>/shots/<sid>.mp4（+ <sid>.meta.json）
- 成片     outputs/templates_3d/<id>/final.mp4（ffprobe 读真实时长/大小，缓存 5s）
- 报告     outputs/templates_3d/<id>/report.json
- 海报     outputs/templates_3d/<id>/poster.jpg（不存在时从 final.mp4 抽首帧生成并缓存；
           成片还没出来时回落到旧 2D 产物 outputs/templates_v5/<id>/frames/frame_01.png 等）
- 逐镜 brief contract/v5/cinema3d/templates3d/<id>.json
- 模板元数据 contract/v5/templates/<id>.json（label 中文 / style）

状态判定「产物为准」：`progress.json` 由后台生成进程与 assemble-watch 同时读写（生成进程用内存
副本整份覆盖，会把已剪辑完成的 done 冲回 generated），因此：
- `final.mp4` 存在且 ffprobe 时长>0（或 final+report.json 都在）-> `status="done"`（覆盖 progress）；
- `final.mp4` 缺失 -> 仍按 progress（generated / assemble_failed），report.json 不单独回填 done；
- summary 里同时给 `done`（状态统计）与 `assembled`（final 真实存在的模板数）。
轻量 `/status` 用同样的产物优先规则（只 stat，不 ffprobe）。

端点：
  GET  /api/cinema/templates                 16 个模板总览（含 summary / jobs）
  GET  /api/cinema/templates/status          轻量轮询版（不 ffprobe、不生成海报，3s 轮询用）
  GET  /api/cinema/templates/{tid}           单模板详情（+ shots 逐镜列表，抽屉用）
  POST /api/cinema/templates/generate        body {templates: [id...]|"all", force?, dry_run?} -> {job_id}
  POST /api/cinema/templates/assemble        body {templates: [id...]|"all", force?, dry_run?} -> {job_id}

任务复用 `cinema3d.py` 的线程任务队列（同一份 `_JOBS` / `_PROCS`），因此：
  GET  /api/cinema/jobs              能看到这里提交的任务
  GET  /api/cinema/jobs/{job_id}     查询进度 / 日志
  POST /api/cinema/jobs/{job_id}/cancel  取消（会 taskkill 子进程树）
无需重复实现任务查询。

安全护栏（GPU 正被后台 `--generate` / `--assemble-watch` 占用时）：
- 检测到**外部** `python -m pipeline.templates3d --generate` 进程 -> POST generate 返回 409，
  避免两个进程同时写 progress.json / 抢 ComfyUI 队列；
- 检测到外部 `--assemble` 进程 -> POST assemble 返回 409；检测到 `--assemble-watch` 守护时，
  非 force 合并不拦截（只处理尚未剪辑的模板）并在响应里带 warning，force 合并不允许（避免同一成片互相覆盖）。
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import zipfile
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple, Union

from fastapi import APIRouter, Body, HTTPException, Response
from pydantic import BaseModel

from .. import config, jobs
from .cinema3d import (
    ACTIVE_STATUSES,
    JOB_KIND_LABEL,
    _JOBS,
    _JOBS_LOCK,
    _PROCS,
    _append_log,
    _failure_message,
    _finish_failed,
    _kill_tree,
    _now_text,
    _persist_job,
    _reserve_job,
    _start_process,
    _timeout_watchdog,
    _update_job,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/cinema/templates", tags=["cinema-templates"])

# --------------------------------------------------------------------------- #
# 常量
# --------------------------------------------------------------------------- #
#: 历史 16 个模板（仅当 pipeline / contract 都读不到时兜底；正常路径见 _discover_template_ids）
_LEGACY_TEMPLATE_IDS: List[str] = [
    "tonight_show",
    "classics",
    "history_talk",
    "masked_singer",
    "roundtable",
    "national_treasure",
    "hot_ones",
    "mystery",
    "qipashuo",
    "shark_tank",
    "slow_life",
    "snl_sketch",
    "standup",
    "taskmaster",
    "traitors",
    "history_mystery",
]

#: V17 新增「写实人物场景」模板的桥接种子：
#: 优先从 pipeline.templates3d.TEMPLATES / contract/v5/templates 动态发现；
#: 若两者都尚未落地新 id，用这份种子保证看板先显示 19 项（真实来源出现后按 id 去重，种子自动失效）。
_V17_REAL_TEMPLATE_BRIDGE: Tuple[str, ...] = ("real_street", "real_cafe", "real_kitchen")
_REAL_TEMPLATE_PREFIX = "real_"
_TEMPLATE_ID_RE = re.compile(r"^[a-z][a-z0-9_]{1,60}$")
#: contract/v5/templates/<id>.json 还没落地时的临时中文名（有 contract 时以 contract 为准）
_FALLBACK_TEMPLATE_LABELS: Dict[str, str] = {
    "real_street": "城市夜访（写实）",
    "real_cafe": "咖啡对谈（写实）",
    "real_kitchen": "深夜食堂（写实）",
}

#: 模板级状态（progress.json 的 status 字段）
STATUS_PENDING = "pending"
STATUS_GENERATING = "generating"
STATUS_GENERATED = "generated"
STATUS_DONE = "done"
STATUS_ASSEMBLE_FAILED = "assemble_failed"
ALLOWED_STATUSES = {
    STATUS_PENDING,
    STATUS_GENERATING,
    STATUS_GENERATED,
    STATUS_DONE,
    STATUS_ASSEMBLE_FAILED,
}
#: 逐镜 clip 在 progress.json 里算「已生成」的状态
CLIP_OK_STATUSES = ("ok", "cached", "card-skip")

T3D_ROOT = config.OUTPUTS_DIR / "templates_3d"
PROGRESS_PATH = T3D_ROOT / "progress.json"
V5_ROOT = config.OUTPUTS_DIR / "templates_v5"
BRIEFS_DIR = config.CONTRACT_DIR / "v5" / "cinema3d" / "templates3d"
V5_TEMPLATE_CONTRACTS = config.CONTRACT_DIR / "v5" / "templates"
#: V21 可视化创建的 custom 模板（contract/v5/custom/templates/<id>.json）
CUSTOM_TEMPLATE_DIR = config.CONTRACT_DIR / "v5" / "custom" / "templates"


def _pipeline_template_ids() -> List[str]:
    """读 `pipeline.templates3d.TEMPLATES`（Lead 新增模板会追加在这里；失败返回 []）。"""
    repo_root = Path(__file__).resolve().parents[3]
    try:
        if str(repo_root) not in sys.path:
            sys.path.insert(0, str(repo_root))
        from pipeline import templates3d  # noqa: PLC0415 - 延迟 import，避免影响服务启动

        raw = getattr(templates3d, "TEMPLATES", None) or []
        ids = [str(item).strip() for item in raw if str(item).strip()]
        if ids:
            logger.info("模板 3D 看板：从 pipeline.templates3d 读到 %d 个模板", len(ids))
        return ids
    except Exception as exc:  # noqa: BLE001 - pipeline 不可用时回落 contract/兜底
        logger.warning("读取 pipeline.templates3d.TEMPLATES 失败（回落 contract/兜底）：%s", exc)
        return []


def _contract_template_ids() -> List[str]:
    """从 contract 发现 3D 看板模板。

    - `contract/v5/templates/*.json`：已有 3D 产物/3D brief（outputs/templates_3d/<id>、
      <id>.json 或 <id>_semantic.json）的模板；`real_*` 前缀即使 3D 产物尚未生成也纳入。
    - `contract/v5/custom/templates/*.json`（V21 可视化创建）：有 semantic brief 或
      `meta.three_d` 标记的 custom 模板纳入（pending，可走 3D 生成）。
    """
    found: List[str] = []
    if V5_TEMPLATE_CONTRACTS.is_dir():
        for path in sorted(V5_TEMPLATE_CONTRACTS.glob("*.json")):
            tid = path.stem
            if not _TEMPLATE_ID_RE.fullmatch(tid):
                continue
            if tid.startswith(_REAL_TEMPLATE_PREFIX):
                found.append(tid)
                continue
            has_3d = (
                (T3D_ROOT / tid).is_dir()
                or (BRIEFS_DIR / f"{tid}.json").is_file()
                or (BRIEFS_DIR / f"{tid}_semantic.json").is_file()
            )
            if has_3d:
                found.append(tid)
    if CUSTOM_TEMPLATE_DIR.is_dir():
        for path in sorted(CUSTOM_TEMPLATE_DIR.glob("*.json")):
            tid = path.stem
            if not _TEMPLATE_ID_RE.fullmatch(tid) or tid in found:
                continue
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            meta = data.get("meta") if isinstance(data, dict) and isinstance(data.get("meta"), dict) else {}
            if (BRIEFS_DIR / f"{tid}_semantic.json").is_file() or meta.get("three_d") \
                    or meta.get("create_source") == "v21":
                found.append(tid)
    return found


def _discover_template_ids() -> List[str]:
    """动态模板列表：pipeline.templates3d 优先 → contract/v5/templates → 历史 16 兜底。

    顺序稳定（pipeline 顺序优先，contract 发现的新模板追加），并去重/校验 id 合法性。
    """
    ids: List[str] = list(_pipeline_template_ids())
    for tid in _contract_template_ids():
        if tid not in ids:
            ids.append(tid)
    if not ids:
        ids = list(_LEGACY_TEMPLATE_IDS)
    # V17：pipeline 与 contract 都尚未落地新模板时，桥接种子保证 19 项可先展示
    for tid in _V17_REAL_TEMPLATE_BRIDGE:
        if tid not in ids:
            ids.append(tid)
    seen = set()
    result: List[str] = []
    for tid in ids:
        if tid in seen or not _TEMPLATE_ID_RE.fullmatch(tid):
            continue
        seen.add(tid)
        result.append(tid)
    logger.info("模板 3D 看板：%d 个模板（%s）", len(result), ",".join(result))
    return result


TEMPLATES: List[str] = _discover_template_ids()
TEMPLATE_SET = frozenset(TEMPLATES)

#: V21：custom 模板创建/删除后，列表需即时反映（按 contract 目录 mtime 失效并重算）
_TEMPLATE_IDS_LOCK = threading.RLock()
_TEMPLATE_IDS_SIG: Optional[tuple] = None


def _template_dirs_signature() -> tuple:
    parts = []
    for directory in (V5_TEMPLATE_CONTRACTS, CUSTOM_TEMPLATE_DIR, BRIEFS_DIR):
        try:
            parts.append((str(directory), directory.stat().st_mtime_ns if directory.is_dir() else 0))
        except OSError:
            parts.append((str(directory), -1))
    return tuple(parts)


def _refresh_template_ids() -> None:
    """contract/custom 模板目录变化时刷新 TEMPLATES/TEMPLATE_SET（创建新模板后列表即时可见）。"""
    global TEMPLATES, TEMPLATE_SET, _TEMPLATE_IDS_SIG
    signature = _template_dirs_signature()
    with _TEMPLATE_IDS_LOCK:
        if signature == _TEMPLATE_IDS_SIG:
            return
        _TEMPLATE_IDS_SIG = signature
        ids = _discover_template_ids()
        if ids != TEMPLATES:
            TEMPLATES = ids
            TEMPLATE_SET = frozenset(ids)
            logger.info("模板 3D 看板已刷新：%d 个模板（%s）", len(ids), ",".join(ids))

POSTER_FILENAME = "poster.jpg"
MIN_SHOT_BYTES = 100 * 1024  # 有效镜头文件最小体积（与 pipeline/templates3d.py 一致）
FINAL_CACHE_TTL = 5.0  # 成片 ffprobe 缓存秒数
DEFAULT_RESOLUTION = "768x432"

#: 任务 kind（与 cinema3d.py 共用 _JOBS；这里补上中文标签）
KIND_TEMPLATES_GENERATE = "templates_generate"
KIND_TEMPLATES_ASSEMBLE = "templates_assemble"
JOB_KIND_LABEL.setdefault(KIND_TEMPLATES_GENERATE, "模板 3D 生成")
JOB_KIND_LABEL.setdefault(KIND_TEMPLATES_ASSEMBLE, "模板 3D 合成")

#: 生成任务互斥范围：本模板生成 + 原 relic 3D 的本地/云端生成（同一块 GPU）
GENERATE_CONFLICT_KINDS = ("generate_local", "generate_cloud", KIND_TEMPLATES_GENERATE)
#: 合成任务互斥范围：本模板合成 + 原 relic 3D 合成
ASSEMBLE_CONFLICT_KINDS = ("assemble", KIND_TEMPLATES_ASSEMBLE)

GENERATE_TIMEOUT_SECONDS = 12 * 3600  # 120 镜 × 最长 40 分钟，兜底 12h
ASSEMBLE_TIMEOUT_SECONDS = 3 * 3600

#: 轻量轮询判定 final「已合成」前，要求文件至少这么多秒没被写过（避免读到 assemble 写一半的 mp4）
FINAL_SETTLE_SECONDS = 20.0

#: 旧 2D 产物里可复用的海报/剧照（按优先级）
OLD_POSTER_CANDIDATES = (
    "poster.png",
    "poster.jpg",
    "cover.png",
    "cover.jpg",
    "frames/frame_01.png",
    "storyboard.png",
)


def _media_url(rel_path: str) -> str:
    text = str(rel_path).replace(os.sep, "/").lstrip("/")
    return f"{config.MEDIA_URL_PREFIX}/{text}"


def _t3d_media(tid: str, rel: str) -> str:
    return _media_url(f"templates_3d/{tid}/{rel}")


# --------------------------------------------------------------------------- #
# 容错 JSON 读缓存（progress.json / briefs 正被 pipeline 非原子写入）
# --------------------------------------------------------------------------- #
class _JsonFileCache:
    """按 (mtime_ns, size) 缓存的 JSON 读取；文件被改写/替换自动失效。

    pipeline/templates3d.py 用 `Path.write_text` 直接覆盖 progress.json，
    读这边可能读到半截 JSON：这里重试 3 次，失败返回 None（调用方回落旧值/空值）。
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._keys: Dict[str, Tuple[int, int]] = {}
        self._values: Dict[str, Any] = {}

    def get(self, path: Path, attempts: int = 3) -> Any:
        try:
            stat = path.stat()
        except OSError:
            return None
        key = (stat.st_mtime_ns, stat.st_size)
        name = str(path)
        with self._lock:
            if self._keys.get(name) == key:
                return self._values.get(name)
        value: Any = None
        for attempt in range(max(1, attempts)):
            try:
                text = path.read_text(encoding="utf-8-sig")
            except OSError:
                return None
            if not text.strip():
                value = None
                break
            try:
                value = json.loads(text)
                break
            except json.JSONDecodeError:
                if attempt + 1 < attempts:
                    time.sleep(0.04)
                else:
                    value = None
        with self._lock:
            self._keys[name] = key
            self._values[name] = value
        return value


_JSON_CACHE = _JsonFileCache()


def _load_progress() -> Dict[str, Any]:
    data = _JSON_CACHE.get(PROGRESS_PATH, attempts=4)
    return data if isinstance(data, dict) else {}


def _template_contract(tid: str) -> Dict[str, Any]:
    """内建 contract/v5/templates + V21 custom 覆盖合并后的模板合同。"""
    merged: Dict[str, Any] = {}
    for path in (V5_TEMPLATE_CONTRACTS / f"{tid}.json", CUSTOM_TEMPLATE_DIR / f"{tid}.json"):
        data = _JSON_CACHE.get(path)
        if isinstance(data, dict):
            merged.update(data)
    return merged


def _template_label(tid: str) -> str:
    label = str(_template_contract(tid).get("label") or "").strip()
    return label or _FALLBACK_TEMPLATE_LABELS.get(tid, tid)


def _template_style(tid: str) -> Optional[str]:
    style = _template_contract(tid).get("style")
    if style:
        return str(style)
    # 新模板 contract 尚未落地时，语义 brief 里的 style 也可用
    semantic_style = _load_semantic(tid).get("style")
    return str(semantic_style) if semantic_style else None


def _load_briefs(tid: str) -> Dict[str, Any]:
    data = _JSON_CACHE.get(BRIEFS_DIR / f"{tid}.json")
    return data if isinstance(data, dict) else {}


def _load_semantic(tid: str) -> Dict[str, Any]:
    """逐镜语义 brief（V17 写实模板/语义重做版）：`contract/v5/cinema3d/templates3d/<tid>_semantic.json`。"""
    data = _JSON_CACHE.get(BRIEFS_DIR / f"{tid}_semantic.json")
    return data if isinstance(data, dict) else {}


def _expected_clip_total(tid: str) -> int:
    """期望生成镜头数（不含 card）：优先 briefs，其次 semantic briefs，最后旧 v5 episode.json。"""
    briefs = _load_briefs(tid)
    shots = briefs.get("shots")
    if isinstance(shots, list):
        total = sum(1 for item in shots if isinstance(item, dict) and item.get("prompt"))
        if total:
            return total
    semantic = _load_semantic(tid)
    semantic_shots = semantic.get("shots")
    if isinstance(semantic_shots, list):
        total = sum(
            1
            for item in semantic_shots
            if isinstance(item, dict) and str(item.get("kind") or "").lower() != "card"
        )
        if total:
            return total
    episode = _JSON_CACHE.get(V5_ROOT / tid / "work" / "episode.json")
    if isinstance(episode, dict) and isinstance(episode.get("shots"), list):
        return sum(
            1
            for item in episode["shots"]
            if isinstance(item, dict) and str(item.get("type") or "").lower() != "card"
        )
    return 0


# --------------------------------------------------------------------------- #
# 模板状态（progress.json -> 规范状态 / 百分比 / clips）
# --------------------------------------------------------------------------- #
def _normalize_status(raw: Any) -> str:
    text = str(raw or "").strip().lower()
    return text if text in ALLOWED_STATUSES else STATUS_PENDING


def _path_nonempty(path: Path) -> bool:
    try:
        return path.is_file() and path.stat().st_size > 0
    except OSError:
        return False


def _artifact_done_full(final_info: Dict[str, Any], report_exists: bool) -> bool:
    """产物为准（列表/详情，可用 ffprobe 真实时长）。

    progress.json 有双写竞争 —— 后台生成进程会用启动时的内存副本整份覆盖，
    把 assemble-watch 写入的 done 冲回 generated。所以「已合成」以真实产物判定：
    - final.mp4 存在且 ffprobe 时长 > 0 -> done；
    - final.mp4 存在 + report.json 存在（ffprobe 暂时读不出时长的兜底）-> done；
    - final.mp4 缺失 -> 不看 report，仍按 progress（generated / assemble_failed）。
    """
    if not final_info.get("exists"):
        return False
    if float(final_info.get("duration") or 0.0) > 0.0:
        return True
    return bool(report_exists)


def _artifact_done_light(final_path: Path) -> bool:
    """产物为准（轻量轮询，只 stat 不 ffprobe）。

    与完整版同样的规则：final.mp4 存在且非空才算已合成；report.json 存在但 final 缺失不回填。
    额外加 20s settle：assemble 正把 final.mp4 写到一半时（文件已存在、mtime 很新、report 未写），
    不要提前显示「已完成」，否则前端会去播一个没写完的 mp4。
    """
    if not _path_nonempty(final_path):
        return False
    if _path_nonempty(final_path.parent / "report.json"):
        return True
    try:
        age = time.time() - final_path.stat().st_mtime
    except OSError:
        return False
    return age >= FINAL_SETTLE_SECONDS


def _clip_counts(tid: str, state: Dict[str, Any]) -> Tuple[int, int]:
    clips = state.get("clips") if isinstance(state.get("clips"), dict) else {}
    raw_done = state.get("done")
    done = int(raw_done) if isinstance(raw_done, (int, float)) else sum(
        1
        for item in clips.values()
        if isinstance(item, dict) and str(item.get("status") or "") in CLIP_OK_STATUSES
    )
    raw_total = state.get("total")
    total = int(raw_total) if isinstance(raw_total, (int, float)) else 0
    if total <= 0:
        total = _expected_clip_total(tid)
    done = max(0, done)
    total = max(done, total)
    return done, total


def _progress_percent(status: str, done: int, total: int) -> float:
    if status == STATUS_DONE:
        return 100.0
    if total <= 0:
        return 100.0 if status in (STATUS_GENERATED, STATUS_ASSEMBLE_FAILED) else 0.0
    pct = done * 100.0 / total
    return round(max(0.0, min(100.0, pct)), 1)


def _template_state(
    tid: str,
    progress: Dict[str, Any],
    artifact_done: bool = False,
) -> Dict[str, Any]:
    raw = progress.get(tid) if isinstance(progress, dict) else None
    state = raw if isinstance(raw, dict) else {}
    status = _normalize_status(state.get("status"))
    done, total = _clip_counts(tid, state)
    # 兜底：progress 里没登记但逐镜片已经在盘上（例如写盘失败/历史产物）
    if status == STATUS_PENDING and done <= 0 and not artifact_done:
        shots_dir = T3D_ROOT / tid / "shots"
        if shots_dir.is_dir():
            existing = sum(
                1
                for path in shots_dir.glob("*.mp4")
                if path.stat().st_size >= MIN_SHOT_BYTES
            )
            if existing > 0:
                done = min(existing, total or existing)
                if total <= 0:
                    total = existing
                status = STATUS_GENERATING
    if status == STATUS_PENDING and done > 0:
        status = STATUS_GENERATING
    # 产物优先：final.mp4 可播放（或 final+report 都在）即视为已合成完成，
    # 覆盖 progress 里被并发写回旧的 generated / assemble_failed。
    if artifact_done:
        status = STATUS_DONE
        if total > 0:
            done = total  # 成片存在 => 逐镜必然都已入库；progress 的 done 可能被旧副本覆盖
        elif done > 0:
            total = done
    updated = state.get("updated")
    error = state.get("error")
    if artifact_done:
        error = None  # 产物已合成时不再展示旧的剪辑失败信息
    return {
        "status": status,
        "progress": _progress_percent(status, done, total),
        "clips": {"done": done, "total": total},
        "updated": str(updated) if updated else None,
        "error": str(error)[:800] if error else None,
        "assembled": bool(artifact_done),
    }


def _summary(states: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    counts = Counter(item["status"] for item in states)
    total = len(states)
    done = counts.get(STATUS_DONE, 0)
    generating = counts.get(STATUS_GENERATING, 0)
    generated = counts.get(STATUS_GENERATED, 0)
    assemble_failed = counts.get(STATUS_ASSEMBLE_FAILED, 0)
    pending = total - done - generating - generated - assemble_failed
    return {
        "total": total,
        "done": done,
        # 已合成（产物为准）：final.mp4 真实存在的模板数（含 progress 尚未回写 done 的）
        "assembled": sum(1 for item in states if item.get("assembled")),
        "generating": generating,
        "generated": generated,
        "assemble_failed": assemble_failed,
        "pending": max(0, pending),
        "clips_done": sum(int(item["clips"]["done"]) for item in states),
        "clips_total": sum(int(item["clips"]["total"]) for item in states),
    }


# --------------------------------------------------------------------------- #
# 成片信息（ffprobe 缓存 5s，列表接口并行探测）
# --------------------------------------------------------------------------- #
_FINAL_CACHE: Dict[str, Tuple[Tuple[int, int], float, Dict[str, Any]]] = {}
_FINAL_LOCK = threading.Lock()


def _final_path(tid: str) -> Path:
    return T3D_ROOT / tid / "final.mp4"


def _final_info(tid: str) -> Dict[str, Any]:
    path = _final_path(tid)
    url = _t3d_media(tid, "final.mp4")
    try:
        stat = path.stat()
    except OSError:
        return {"exists": False, "duration": 0.0, "size": 0, "mtime": None, "url": url}
    key = (stat.st_mtime_ns, stat.st_size)
    now = time.monotonic()
    with _FINAL_LOCK:
        hit = _FINAL_CACHE.get(tid)
        if hit is not None and hit[0] == key and (now - hit[1]) < FINAL_CACHE_TTL:
            return dict(hit[2])
    duration = float(jobs.probe_video_duration(path) or 0.0)
    value = {
        "exists": bool(stat.st_size > 0),
        "duration": round(duration, 2),
        "size": int(stat.st_size),
        "mtime": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(stat.st_mtime)),
        "url": url,
    }
    with _FINAL_LOCK:
        _FINAL_CACHE[tid] = (key, now, value)
    return dict(value)


def _final_info_many(tids: Sequence[str]) -> Dict[str, Dict[str, Any]]:
    """并行 ffprobe（首次 16 个成片约 0.3-0.8s；缓存命中则纯 stat）。"""
    if not tids:
        return {}
    workers = max(1, min(8, len(tids)))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        values = list(pool.map(_final_info, tids))
    return dict(zip(tids, values))


# --------------------------------------------------------------------------- #
# 海报：poster.jpg（3D 成片抽帧）> 旧 2D 产物（frame_01.png 等）
# --------------------------------------------------------------------------- #
_POSTER_LOCK = threading.RLock()
_POSTER_QUEUE: List[str] = []
_POSTER_WORKER: Optional[threading.Thread] = None


def _poster_path(tid: str) -> Path:
    return T3D_ROOT / tid / POSTER_FILENAME


def _poster_exists(tid: str) -> bool:
    path = _poster_path(tid)
    try:
        return path.is_file() and path.stat().st_size > 0
    except OSError:
        return False


def _old_poster_url(tid: str) -> Optional[str]:
    base = V5_ROOT / tid
    for rel in OLD_POSTER_CANDIDATES:
        path = base / rel
        try:
            if path.is_file() and path.stat().st_size > 0:
                return _media_url(f"templates_v5/{tid}/{rel}")
        except OSError:
            continue
    return None


#: contract 模板里可能声明的海报字段（V17 新模板/后续模板可直接在 JSON 里挂图）
_CONTRACT_POSTER_KEYS = (
    "poster",
    "poster_url",
    "cover",
    "cover_url",
    "thumbnail",
    "thumbnail_url",
    "image",
    "image_url",
    "preview",
    "preview_url",
)


def _resolve_served_media_url(raw: Any) -> Optional[str]:
    """把 contract 里的图片路径/URL 解析成 `/media/...`（只有 outputs/ 内文件可被静态服务）。"""
    text = str(raw or "").strip()
    if not text:
        return None
    if text.startswith(f"{config.MEDIA_URL_PREFIX}/"):
        return text
    path = Path(text)
    candidates = [path] if path.is_absolute() else [config.REPO_ROOT / text, config.OUTPUTS_DIR / text]
    for candidate in candidates:
        try:
            if not candidate.is_file() or candidate.stat().st_size <= 0:
                continue
            relative = candidate.resolve().relative_to(config.OUTPUTS_DIR.resolve())
            return _media_url(relative.as_posix())
        except (OSError, ValueError):
            continue
    return None


def _contract_poster_url(tid: str) -> Optional[str]:
    """contract/v5/templates/<id>.json 内声明的海报（无 3D 海报时的回落之一）。"""
    contract = _template_contract(tid)
    for key in _CONTRACT_POSTER_KEYS:
        url = _resolve_served_media_url(contract.get(key))
        if url:
            return url
    for parent_key in ("packaging", "meta", "assets"):
        parent = contract.get(parent_key)
        if not isinstance(parent, dict):
            continue
        for key in _CONTRACT_POSTER_KEYS:
            url = _resolve_served_media_url(parent.get(key))
            if url:
                return url
    return None


def _generate_poster(tid: str) -> bool:
    """用 ffmpeg 从 final.mp4 抽一帧做海报（同目录临时文件 + os.replace 原子落盘）。"""
    final = _final_path(tid)
    if not final.is_file():
        return False
    target = _poster_path(tid)
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
    except OSError:
        return False
    tmp = target.with_name(f"poster.tmp-{os.getpid()}-{threading.get_ident()}.jpg")
    cmd = [
        str(jobs.ffmpeg_bin()),
        "-y",
        "-ss",
        "0.8",
        "-i",
        str(final),
        "-frames:v",
        "1",
        "-q:v",
        "3",
        str(tmp),
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, timeout=120)
    except (OSError, subprocess.SubprocessError) as exc:
        logger.warning("生成模板海报失败（%s）：%s", tid, exc)
        return False
    try:
        ok = proc.returncode == 0 and tmp.is_file() and tmp.stat().st_size > 0
    except OSError:
        ok = False
    if not ok:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        return False
    try:
        os.replace(tmp, target)
    except OSError as exc:
        logger.warning("写入模板海报失败（%s）：%s", tid, exc)
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        return False
    return True


def _poster_worker() -> None:
    global _POSTER_WORKER
    while True:
        with _POSTER_LOCK:
            if not _POSTER_QUEUE:
                _POSTER_WORKER = None
                return
            tid = _POSTER_QUEUE.pop(0)
        try:
            if _poster_exists(tid) or not _final_path(tid).is_file():
                continue
            _generate_poster(tid)
        except Exception:  # noqa: BLE001 - 后台线程必须兜底
            logger.exception("后台生成模板海报异常：%s", tid)


def _enqueue_poster(tid: str) -> None:
    global _POSTER_WORKER
    with _POSTER_LOCK:
        if tid in _POSTER_QUEUE or _poster_exists(tid):
            return
        _POSTER_QUEUE.append(tid)
        if _POSTER_WORKER is not None and _POSTER_WORKER.is_alive():
            return
        _POSTER_WORKER = threading.Thread(
            target=_poster_worker, daemon=True, name="cinema-templates-poster"
        )
        _POSTER_WORKER.start()


def _poster_url(tid: str, final_exists: bool, allow_generate: bool) -> Optional[str]:
    """海报 URL：优先 3D poster.jpg；成片已出但海报未生成时抽帧（列表异步/详情同步）；
    成片还没出来时回落到 contract 声明的海报 / 旧 2D 产物；都没有返回 None（前端显示占位，不 500）。"""
    if _poster_exists(tid):
        return _t3d_media(tid, POSTER_FILENAME)
    if final_exists:
        if allow_generate:
            if _generate_poster(tid):
                return _t3d_media(tid, POSTER_FILENAME)
        else:
            _enqueue_poster(tid)
    return _contract_poster_url(tid) or _old_poster_url(tid)


def _report_url(tid: str) -> Optional[str]:
    path = T3D_ROOT / tid / "report.json"
    try:
        if path.is_file() and path.stat().st_size > 0:
            return _t3d_media(tid, "report.json")
    except OSError:
        pass
    return None


# --------------------------------------------------------------------------- #
# 单模板视图
# --------------------------------------------------------------------------- #
def _template_view(
    tid: str,
    progress: Dict[str, Any],
    final_info: Optional[Dict[str, Any]] = None,
    allow_poster_generate: bool = False,
) -> Dict[str, Any]:
    final = final_info if final_info is not None else _final_info(tid)
    report_url = _report_url(tid)
    # 产物为准：progress.json 有双写竞争，final 可播放（或 final+report 都在）即视为 done
    artifact_done = _artifact_done_full(final, report_url is not None)
    state = _template_state(tid, progress, artifact_done=artifact_done)
    return {
        "id": tid,
        "label": _template_label(tid),
        "style": _template_style(tid),
        "status": state["status"],
        "assembled": state["assembled"],
        "progress": state["progress"],
        "clips": state["clips"],
        "final": final,
        "poster_url": _poster_url(tid, bool(final.get("exists")), allow_poster_generate),
        "report_url": report_url,
        "error": state["error"],
        "updated": state["updated"],
    }


def _jobs_info() -> Dict[str, Any]:
    with _JOBS_LOCK:
        active_generate = sum(
            1
            for job in _JOBS.values()
            if job.get("kind") == KIND_TEMPLATES_GENERATE and job.get("status") in ACTIVE_STATUSES
        )
        active_assemble = sum(
            1
            for job in _JOBS.values()
            if job.get("kind") == KIND_TEMPLATES_ASSEMBLE and job.get("status") in ACTIVE_STATUSES
        )
    return {
        "active": active_generate + active_assemble,
        "generate": active_generate,
        "assemble": active_assemble,
    }


# --------------------------------------------------------------------------- #
# 轻量轮询：progress + 文件存在性，不 ffprobe（3s 轮询专用）
# --------------------------------------------------------------------------- #
def _status_rows(progress: Dict[str, Any]) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for tid in TEMPLATES:
        final_exists = _path_nonempty(_final_path(tid))
        report_exists = _path_nonempty(T3D_ROOT / tid / "report.json")
        # 轻量版同样以产物为准（只 stat）：final 写完即 done，覆盖 progress 的双写旧状态
        state = _template_state(
            tid, progress, artifact_done=_artifact_done_light(_final_path(tid))
        )
        rows.append(
            {
                "id": tid,
                "label": _template_label(tid),
                "status": state["status"],
                "assembled": state["assembled"],
                "progress": state["progress"],
                "clips": state["clips"],
                "final_exists": final_exists,
                "report_exists": report_exists,
                "updated": state["updated"],
            }
        )
    return rows


# --------------------------------------------------------------------------- #
# 逐镜详情（briefs + progress.clips + shots 目录）
# --------------------------------------------------------------------------- #
_SHOT_PROBE_CACHE: Dict[str, Tuple[Tuple[int, int], bool, float]] = {}
_SHOT_PROBE_LOCK = threading.Lock()


def _probe_shot_file(path: Path) -> Tuple[bool, float]:
    try:
        stat = path.stat()
    except OSError:
        return (False, 0.0)
    if stat.st_size < MIN_SHOT_BYTES:
        return (False, 0.0)
    key = (stat.st_mtime_ns, stat.st_size)
    name = str(path)
    with _SHOT_PROBE_LOCK:
        hit = _SHOT_PROBE_CACHE.get(name)
    if hit is not None and hit[0] == key:
        return (hit[1], hit[2])
    duration = float(jobs.probe_video_duration(path) or 0.0)
    valid = duration > 0.0
    with _SHOT_PROBE_LOCK:
        _SHOT_PROBE_CACHE[name] = (key, valid, duration)
    return (valid, duration)


def _probe_shots(paths: Dict[str, Path]) -> Dict[str, Tuple[bool, float]]:
    if not paths:
        return {}
    workers = max(1, min(8, len(paths)))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        values = list(pool.map(_probe_shot_file, paths.values()))
    return dict(zip(paths.keys(), values))


def _shot_meta_view(tid: str, sid: str, clip: Dict[str, Any]) -> Dict[str, Any]:
    raw = _JSON_CACHE.get(T3D_ROOT / tid / "shots" / f"{sid}.meta.json")
    meta: Dict[str, Any] = {}
    if isinstance(raw, dict):
        # prompt 很长且前端抽屉不展示，剔除以减小响应体积
        meta = {key: value for key, value in raw.items() if key != "prompt"}
    for key in ("status", "seconds", "prompt_id", "error"):
        if key in clip:
            meta.setdefault(key, clip[key])
    return meta


def _shot_status(kind: str, clip: Dict[str, Any], valid: bool) -> str:
    if kind == "card":
        return "card"  # 片头/片尾卡片沿用旧 v5 卡片镜，不参与 3D 生成
    clip_status = str(clip.get("status") or "").strip().lower()
    if clip_status in CLIP_OK_STATUSES:
        return "done"
    if clip_status == "failed":
        return "failed"
    if valid:
        return "done"
    return "pending"


def _shots_detail(tid: str, progress: Dict[str, Any]) -> List[Dict[str, Any]]:
    briefs = _load_briefs(tid)
    brief_shots = briefs.get("shots") if isinstance(briefs.get("shots"), list) else []
    semantic = _load_semantic(tid)
    semantic_shots = semantic.get("shots") if isinstance(semantic.get("shots"), list) else []
    episode = _JSON_CACHE.get(V5_ROOT / tid / "work" / "episode.json")
    episode_index: Dict[str, Dict[str, Any]] = {}
    if isinstance(episode, dict) and isinstance(episode.get("shots"), list):
        for item in episode["shots"]:
            if isinstance(item, dict) and item.get("id") is not None:
                episode_index[str(item["id"])] = item
    source: List[Dict[str, Any]] = []
    source_kind = "episode"
    if brief_shots:
        source = [item for item in brief_shots if isinstance(item, dict)]
        source_kind = "briefs"
    elif semantic_shots:
        # V17 新模板/语义版：shots 来自 <tid>_semantic.json（可能只有语义字段，无落盘镜头）
        source = [item for item in semantic_shots if isinstance(item, dict)]
        source_kind = "semantic"
    else:
        source = list(episode_index.values())

    raw = progress.get(tid) if isinstance(progress, dict) else None
    state = raw if isinstance(raw, dict) else {}
    clip_map = state.get("clips") if isinstance(state.get("clips"), dict) else {}

    shots_dir = T3D_ROOT / tid / "shots"
    probe_paths: Dict[str, Path] = {}
    for item in source:
        sid = str(item.get("id") or "")
        if sid:
            probe_paths[sid] = shots_dir / f"{sid}.mp4"
    probes = _probe_shots(probe_paths)

    shots: List[Dict[str, Any]] = []
    for item in source:
        sid = str(item.get("id") or "").strip()
        if not sid:
            continue
        # briefs 里的 card 镜没有 duration/scene，回落到旧 v5 episode.json
        fallback = episode_index.get(sid) or {}
        raw_kind = str(item.get("kind") or "").strip().lower()
        if not raw_kind:
            raw_type = str(item.get("type") or fallback.get("type") or "").lower()
            raw_kind = "card" if raw_type == "card" else ("broll" if raw_type == "broll" else "dialogue")
        clip = clip_map.get(sid)
        clip = clip if isinstance(clip, dict) else {}
        valid, probed = probes.get(sid, (False, 0.0))
        status = _shot_status(raw_kind, clip, valid)
        planned = item.get("duration")
        if planned is None:
            planned = fallback.get("duration")
        if planned is None and raw_kind == "card":
            # 卡片镜在 briefs/semantic 里没有 duration：按 v5 惯例 1.4/1.6s
            planned = 1.4 if "title" in sid else 1.6
        try:
            planned_duration = round(float(planned), 3) if planned is not None else 0.0
        except (TypeError, ValueError):
            planned_duration = 0.0
        scene = item.get("scene") or fallback.get("scene")
        entry: Dict[str, Any] = {
            "id": sid,
            "kind": raw_kind,
            "scene": str(scene) if scene else None,
            "duration": planned_duration,
            "status": status,
            "url": _t3d_media(tid, f"shots/{sid}.mp4"),
            "valid": bool(valid),
            "seconds": round(float(probed), 2) if probed else None,
            "prompt_id": clip.get("prompt_id"),
            "error": str(clip.get("error"))[:400] if clip.get("error") else None,
            "meta": _shot_meta_view(tid, sid, clip),
        }
        if source_kind == "semantic":
            # 语义 brief 的额外字段：前端抽屉可直接展示「台词 / 语义画面 / 景别」
            for extra in ("line", "semantic_visual", "framing"):
                if item.get(extra):
                    entry[extra] = item[extra]
        shots.append(entry)
    return shots


# --------------------------------------------------------------------------- #
# 后台进程探测（避免和正在跑的 pipeline.templates3d 抢 GPU / 抢 progress.json）
# --------------------------------------------------------------------------- #
_PIPE_SCAN_CACHE: Dict[str, Any] = {"ts": 0.0, "modes": None, "ok": False}
_PIPE_SCAN_LOCK = threading.Lock()
_PIPE_SCAN_TTL = 10.0

_POWER_SHELL_SCAN = (
    "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | "
    "Select-Object ProcessId,CommandLine | ConvertTo-Json -Compress"
)


def _own_job_pids() -> set:
    with _JOBS_LOCK:
        procs = list(_PROCS.values())
    pids = set()
    for proc in procs:
        if proc is None:
            continue
        try:
            if proc.poll() is None:
                pids.add(int(proc.pid))
        except (OSError, ValueError):
            continue
    return pids


def _scan_pipeline_modes() -> Tuple[Dict[str, List[int]], bool]:
    """扫描系统 python 进程里的 pipeline.templates3d 模式（10s 缓存）。

    返回 (modes, ok)：ok=False 表示扫描失败（调用方回落到 progress.json 判断）。
    """
    now = time.monotonic()
    with _PIPE_SCAN_LOCK:
        cached = _PIPE_SCAN_CACHE.get("modes")
        if cached is not None and (now - float(_PIPE_SCAN_CACHE.get("ts") or 0.0)) < _PIPE_SCAN_TTL:
            return cached, bool(_PIPE_SCAN_CACHE.get("ok"))

    modes: Dict[str, List[int]] = {"generate": [], "assemble": [], "watch": []}
    ok = False
    try:
        proc = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", _POWER_SHELL_SCAN],
            capture_output=True,
            text=True,
            timeout=20,
        )
        if proc.returncode == 0:
            ok = True
            raw = (proc.stdout or "").strip()
            data = json.loads(raw) if raw else []
            if isinstance(data, dict):
                data = [data]
            for item in data if isinstance(data, list) else []:
                if not isinstance(item, dict):
                    continue
                command = str(item.get("CommandLine") or "")
                if "pipeline.templates3d" not in command:
                    continue
                try:
                    pid = int(item.get("ProcessId") or 0)
                except (TypeError, ValueError):
                    continue
                if pid <= 0:
                    continue
                # `--assemble-watch` 也包含子串 "--assemble"，先判 watch
                if "--generate" in command:
                    modes["generate"].append(pid)
                if "--assemble-watch" in command:
                    modes["watch"].append(pid)
                elif "--assemble" in command:
                    modes["assemble"].append(pid)
    except (OSError, subprocess.SubprocessError, ValueError):
        logger.warning("扫描 pipeline.templates3d 后台进程失败，回落 progress.json 判断")

    with _PIPE_SCAN_LOCK:
        _PIPE_SCAN_CACHE["ts"] = now
        _PIPE_SCAN_CACHE["modes"] = modes
        _PIPE_SCAN_CACHE["ok"] = ok
    return modes, ok


def _external_pipeline_modes() -> Tuple[Dict[str, List[int]], bool]:
    modes, ok = _scan_pipeline_modes()
    own = _own_job_pids()
    if own:
        modes = {key: [pid for pid in pids if pid not in own] for key, pids in modes.items()}
    return modes, ok


def _progress_has_fresh_generating(max_age_seconds: float = 300.0) -> bool:
    progress = _load_progress()
    generating = any(
        isinstance(state, dict) and state.get("status") == STATUS_GENERATING
        for state in progress.values()
    )
    if not generating:
        return False
    try:
        age = time.time() - PROGRESS_PATH.stat().st_mtime
    except OSError:
        return False
    return age <= max_age_seconds


def _external_generation_pid() -> Optional[int]:
    """外部 `--generate` 进程 PID；-1 表示扫描失败但进度显示正在生成。"""
    modes, ok = _external_pipeline_modes()
    if ok:
        pids = modes.get("generate") or []
        return int(pids[0]) if pids else None
    return -1 if _progress_has_fresh_generating() else None


def _external_watcher_pids() -> List[int]:
    modes, ok = _external_pipeline_modes()
    if not ok:
        return []
    return list(modes.get("watch") or [])


def _external_assemble_pids() -> List[int]:
    modes, ok = _external_pipeline_modes()
    if not ok:
        return []
    return list(modes.get("assemble") or [])


# --------------------------------------------------------------------------- #
# 请求体 / 参数校验
# --------------------------------------------------------------------------- #
class TemplateGenerateRequest(BaseModel):
    """POST /generate。

    templates: 模板 id 数组 或 "all"（也接受逗号分隔字符串，容错前端）
    force:     true 时给 pipeline 加 --force（重生成/重剪辑已有产物）
    resolution: 可选，默认 768x432（与后台生成任务一致）
    dry_run:   可选，只建任务并立即完成（不启动子进程/不占 GPU），用于前端预览将执行的命令
    """

    templates: Optional[Union[List[str], str]] = "all"
    force: bool = False
    resolution: Optional[str] = None
    dry_run: bool = False


class TemplateAssembleRequest(BaseModel):
    """POST /assemble。"""

    templates: Optional[Union[List[str], str]] = "all"
    force: bool = False
    dry_run: bool = False


def _bad_request(detail: str) -> HTTPException:
    return HTTPException(status_code=400, detail=detail)


def _parse_templates(raw: Any, field: str = "templates") -> List[str]:
    if raw is None:
        raw = "all"
    requested: List[str]
    if isinstance(raw, str):
        text = raw.strip()
        if not text:
            raise _bad_request(f"{field} 不能为空：传模板 id 数组或 \"all\"")
        if text.lower() == "all":
            return list(TEMPLATES)
        requested = [item.strip() for item in text.split(",") if item.strip()]
    elif isinstance(raw, (list, tuple)):
        requested = [str(item).strip() for item in raw if str(item).strip()]
    else:
        raise _bad_request(f"{field} 必须是模板 id 数组或 \"all\"")
    if not requested:
        raise _bad_request(f"{field} 不能为空：至少选择一个模板，或传 \"all\"")
    unknown = [item for item in requested if item not in TEMPLATE_SET]
    if unknown:
        raise _bad_request(
            f"模板不存在：{', '.join(unknown[:8])}；可选模板：{', '.join(TEMPLATES)}"
        )
    return list(dict.fromkeys(requested))


def _parse_resolution(raw: Optional[str]) -> str:
    text = str(raw or DEFAULT_RESOLUTION).strip().lower()
    if not re.fullmatch(r"\d{2,5}x\d{2,5}", text):
        raise _bad_request("resolution 格式应为 宽x高（如 768x432）")
    return text


def _generate_cmd(tids: Sequence[str], force: bool, resolution: str) -> List[str]:
    cmd = [
        sys.executable,
        "-m",
        "pipeline.templates3d",
        "--generate",
        "--templates",
        ",".join(tids),
        "--resolution",
        resolution,
    ]
    if force:
        cmd.append("--force")
    return cmd


def _assemble_cmd(tids: Sequence[str], force: bool) -> List[str]:
    cmd = [
        sys.executable,
        "-m",
        "pipeline.templates3d",
        "--assemble",
        "--templates",
        ",".join(tids),
    ]
    if force:
        cmd.append("--force")
    return cmd


# --------------------------------------------------------------------------- #
# 任务执行：复刻 cinema3d._run_job，但用 templates3d 的日志格式解析进度
# --------------------------------------------------------------------------- #
_GEN_START_RE = re.compile(r"^===\s*([a-z0-9_]+)\s*[:：]\s*(\d+)\s*镜\s*===")
_GEN_DONE_RE = re.compile(r"^===\s*([a-z0-9_]+)\s*完成\s*(\d+)\s*/\s*(\d+)")
_GEN_CLIP_RE = re.compile(r"^\[([a-z0-9_]+)\s+(\d+)\s*/\s*(\d+)\]")
_ASM_START_RE = re.compile(r"^---\s*剪辑\s+([a-z0-9_]+)\s*---")
_ASM_DONE_RE = re.compile(r"^---\s*([a-z0-9_]+)\s*剪辑完成")
_ASM_FAIL_RE = re.compile(r"^---\s*([a-z0-9_]+)\s*剪辑失败\s*[:：]?\s*(.*)$")


class _TemplateJobTracker:
    """把 pipeline.templates3d 的 stdout 翻译成任务进度 / 终态结果。"""

    def __init__(self, mode: str, tids: Sequence[str]) -> None:
        self.mode = mode  # "generate" | "assemble"
        self.tids = list(tids)
        self.expected: Dict[str, int] = {tid: _expected_clip_total(tid) for tid in self.tids}
        self.template_total: Dict[str, int] = {
            tid: self.expected.get(tid, 0) for tid in self.tids
        }
        self.done_by_tid: Dict[str, int] = {tid: 0 for tid in self.tids}
        self.assembled: List[str] = []
        self.assemble_failed: List[str] = []
        self.assemble_errors: Dict[str, str] = {}

    # -- 生成 -------------------------------------------------------------
    def _generate_update(self, line: str) -> Optional[Dict[str, Any]]:
        match = _GEN_DONE_RE.match(line)
        if match:
            tid = match.group(1)
            if tid in self.done_by_tid:
                done = int(match.group(2))
                total = int(match.group(3))
                self.done_by_tid[tid] = done
                self.template_total[tid] = max(self.template_total.get(tid, 0), total)
            return self._generate_progress(f"模板 {tid} 生成完成")
        match = _GEN_CLIP_RE.match(line)
        if match:
            tid = match.group(1)
            index, total = int(match.group(2)), int(match.group(3))
            if tid in self.done_by_tid:
                self.done_by_tid[tid] = max(self.done_by_tid[tid], index)
                self.template_total[tid] = max(self.template_total.get(tid, 0), total)
            return self._generate_progress(f"{tid} 逐镜生成中 {index}/{total}")
        match = _GEN_START_RE.match(line)
        if match:
            tid = match.group(1)
            total = int(match.group(2))
            if tid in self.done_by_tid:
                self.template_total[tid] = max(self.template_total.get(tid, 0), total)
            return self._generate_progress(f"开始生成模板 {tid}（{total} 镜）")
        if "达到本次时间预算" in line:
            return {"message": line[:200]}
        return None

    def _generate_progress(self, message: str) -> Dict[str, Any]:
        total = sum(self.template_total.values())
        done = sum(self.done_by_tid.values())
        percent = (done * 100.0 / total) if total > 0 else 1.0
        return {"progress": round(min(99.0, max(1.0, percent)), 1), "message": message[:200]}

    # -- 合成 -------------------------------------------------------------
    def _assemble_update(self, line: str) -> Optional[Dict[str, Any]]:
        match = _ASM_FAIL_RE.match(line)
        if match:
            tid = match.group(1)
            if tid not in self.assemble_failed:
                self.assemble_failed.append(tid)
            detail = (match.group(2) or "").strip()
            if detail.endswith("---"):  # pipeline 输出形如 `--- <tid> 剪辑失败: <exc> ---`
                detail = detail[:-3].strip()
            self.assemble_errors[tid] = detail[:400]
            return self._assemble_progress(f"模板 {tid} 剪辑失败")
        match = _ASM_DONE_RE.match(line)
        if match:
            tid = match.group(1)
            if tid not in self.assembled:
                self.assembled.append(tid)
            return self._assemble_progress(f"模板 {tid} 剪辑完成")
        match = _ASM_START_RE.match(line)
        if match:
            return self._assemble_progress(f"剪辑模板 {match.group(1)} 中…")
        return None

    def _assemble_progress(self, message: str) -> Dict[str, Any]:
        total = max(1, len(self.tids))
        finished = len(self.assembled) + len(self.assemble_failed)
        percent = (finished * 100.0 / total) if total else 1.0
        return {"progress": round(min(99.0, max(1.0, percent)), 1), "message": message[:200]}

    # -- 入口 -------------------------------------------------------------
    def on_line(self, line: str) -> Optional[Dict[str, Any]]:
        if self.mode == "assemble":
            return self._assemble_update(line)
        return self._generate_update(line)

    # -- 终态 -------------------------------------------------------------
    def generate_result(self) -> Dict[str, Any]:
        progress = _load_progress()
        templates_status: Dict[str, str] = {}
        clips_done = 0
        clips_total = 0
        failed_clips: List[str] = []
        for tid in self.tids:
            state = progress.get(tid) if isinstance(progress, dict) else None
            state = state if isinstance(state, dict) else {}
            done, total = _clip_counts(tid, state)
            status = _normalize_status(state.get("status"))
            templates_status[tid] = status
            clips_done += done
            clips_total += total
            clips = state.get("clips") if isinstance(state.get("clips"), dict) else {}
            for sid, clip in clips.items():
                if isinstance(clip, dict) and str(clip.get("status") or "") == "failed":
                    failed_clips.append(f"{tid}/{sid}")
        return {
            "mode": "generate",
            "templates": list(self.tids),
            "clips_done": clips_done,
            "clips_total": clips_total,
            "templates_status": templates_status,
            "failed_clips": failed_clips[:40],
        }

    def assemble_result(self) -> Dict[str, Any]:
        return {
            "mode": "assemble",
            "templates": list(self.tids),
            "assembled": list(self.assembled),
            "failed": list(self.assemble_failed),
            "errors": dict(self.assemble_errors),
        }


def _generate_done_message(result: Dict[str, Any]) -> str:
    tids = result.get("templates") or []
    clips_done = int(result.get("clips_done") or 0)
    clips_total = int(result.get("clips_total") or 0)
    statuses = result.get("templates_status") or {}
    finished = sum(1 for value in statuses.values() if value in (STATUS_GENERATED, STATUS_DONE))
    message = f"3D 生成完成：{clips_done}/{clips_total} 镜，{finished}/{len(tids)} 模板"
    failed = result.get("failed_clips") or []
    if failed:
        message += f"；失败 {len(failed)} 镜（{', '.join(str(item) for item in failed[:3])}）"
    return message


def _assemble_done_message(result: Dict[str, Any]) -> str:
    assembled = result.get("assembled") or []
    failed = result.get("failed") or []
    if assembled:
        message = f"3D 合成完成：成功 {len(assembled)} 个模板"
        if failed:
            message += f"，失败 {len(failed)} 个（{', '.join(str(item) for item in failed[:3])}）"
        return message
    if failed:
        return f"3D 合成失败：{len(failed)} 个模板（{', '.join(str(item) for item in failed[:3])}）"
    return "3D 合成任务结束：没有可剪辑的模板（需先完成逐镜生成，或等 assemble-watch 自动处理）"


def _finish_template_job(job_id: str, message: str, result: Dict[str, Any]) -> None:
    with _JOBS_LOCK:
        job = _JOBS.get(job_id)
        if job is None or job.get("status") == "cancelled":
            return
        job.update(
            status="done",
            progress=100.0,
            finished_at=_now_text(),
            message=message[:300],
            result=result,
        )
        snapshot = dict(job)
    _persist_job(snapshot)


def _run_template_job(job_id: str) -> None:
    """任务线程：起子进程 -> 逐行入日志/解析进度 -> 终态回写（复用 cinema3d 的任务状态函数）。"""
    with _JOBS_LOCK:
        job = _JOBS.get(job_id)
        if job is None:
            return
        cmd = list(job.get("_cmd") or [])
        mode = str(job.get("_mode") or "")
        timeout = job.get("_timeout")
        tids = list(job.get("_templates") or [])
    if not cmd:
        _finish_failed(job_id, "内部错误：任务缺少可执行命令")
        return
    tracker = _TemplateJobTracker(mode, tids)
    label = JOB_KIND_LABEL.get(KIND_TEMPLATES_GENERATE if mode == "generate" else KIND_TEMPLATES_ASSEMBLE, "任务")

    proc: Optional[subprocess.Popen] = None
    try:
        with _JOBS_LOCK:
            current = _JOBS.get(job_id)
            if current is None:
                return
            if current.get("status") == "cancelled":
                return
            current.update(
                status="running",
                started_at=_now_text(),
                _started_ts=time.time(),
                progress=1.0,
                message=f"{label}运行中",
            )
            start_snapshot = dict(current)
        _persist_job(start_snapshot)
        _append_log(job_id, "$ " + " ".join(cmd))
        proc = _start_process(cmd)
        with _JOBS_LOCK:
            _PROCS[job_id] = proc
            cancelled_before_register = (_JOBS.get(job_id) or {}).get("status") == "cancelled"
        if cancelled_before_register:
            _kill_tree(proc)
            return
        if timeout:
            threading.Thread(
                target=_timeout_watchdog,
                args=(job_id, proc, float(timeout)),
                daemon=True,
                name=f"cinema-templates-watchdog-{job_id}",
            ).start()
        if proc.stdout is not None:
            for raw in proc.stdout:
                line = str(raw).rstrip("\r\n")
                if not line.strip():
                    continue
                _append_log(job_id, line)
                with _JOBS_LOCK:
                    current = _JOBS.get(job_id)
                    if current is None or current.get("status") != "running":
                        continue
                update = tracker.on_line(line)
                if update:
                    _update_job(job_id, **update)
        proc.wait()
        returncode = proc.returncode
    except Exception as exc:  # noqa: BLE001 - 任务线程必须兜底
        logger.exception("templates3d 任务执行异常：%s", job_id)
        _finish_failed(job_id, f"任务执行异常：{exc}")
        return
    finally:
        with _JOBS_LOCK:
            _PROCS.pop(job_id, None)
        if proc is not None and proc.poll() is None:
            _kill_tree(proc)

    with _JOBS_LOCK:
        current = _JOBS.get(job_id)
        if current is None:
            return
        status = current.get("status")
        logs = list(current.get("logs") or [])
        timed_out = bool(current.get("_timed_out"))
    if status == "cancelled":
        return
    if timed_out:
        _finish_failed(job_id, f"任务超时（>{int(float(timeout or 0))}s），已终止")
        return

    if mode == "assemble":
        result = tracker.assemble_result()
        message = _assemble_done_message(result)
    else:
        result = tracker.generate_result()
        message = _generate_done_message(result)
    if returncode == 0:
        _finish_template_job(job_id, message, result)
    else:
        fail_mode = "assemble" if mode == "assemble" else "generate"
        _finish_failed(job_id, _failure_message(int(returncode or -1), logs, fail_mode))


def _spawn_template_job(job_id: str) -> None:
    threading.Thread(
        target=_run_template_job,
        args=(job_id,),
        daemon=True,
        name=f"cinema-templates-job-{job_id}",
    ).start()


# --------------------------------------------------------------------------- #
# GET /api/cinema/templates
# --------------------------------------------------------------------------- #
@router.get("")
@router.get("/")
def list_templates(response: Response) -> Dict[str, Any]:
    response.headers["Cache-Control"] = "no-store, max-age=0"
    _refresh_template_ids()  # V21：新建 custom 模板后列表即时可见
    progress = _load_progress()
    finals = _final_info_many(TEMPLATES)
    views = [
        _template_view(tid, progress, finals.get(tid), allow_poster_generate=False)
        for tid in TEMPLATES
    ]
    return {
        "templates": views,
        "summary": _summary(views),
        "jobs": _jobs_info(),
    }


# --------------------------------------------------------------------------- #
# GET /api/cinema/templates/status（轻量轮询，注册在 /{tid} 之前）
# --------------------------------------------------------------------------- #
@router.get("/status")
def get_templates_status(response: Response) -> Dict[str, Any]:
    response.headers["Cache-Control"] = "no-store, max-age=0"
    _refresh_template_ids()  # V21
    progress = _load_progress()
    rows = _status_rows(progress)
    return {
        "templates": rows,
        "summary": _summary(rows),
        "jobs": _jobs_info(),
    }


# --------------------------------------------------------------------------- #
# POST /api/cinema/templates/generate
# --------------------------------------------------------------------------- #
def _conflict_message_external_generate(pid: int) -> str:
    if pid > 0:
        return (
            f"已有后台 3D 生成进程在运行（PID {pid}），GPU 正在串行生成，请等它完成后再触发；"
            "可先用 GET /api/cinema/templates 或 GET /api/cinema/templates/status 查看进度"
        )
    return (
        "progress.json 显示正在生成（后台生成的进程未能扫描到），请等当前生成结束后再触发；"
        "可先用 GET /api/cinema/templates/status 查看进度"
    )


def _reserve_or_409(
    conflict_kinds: Sequence[str],
    kind: str,
    tids: Sequence[str],
    **fields: Any,
) -> Dict[str, Any]:
    try:
        return _reserve_job(tuple(conflict_kinds), kind, [], **fields)
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001 - 队列异常不能变成 500 静默
        raise HTTPException(status_code=500, detail=f"任务入队失败：{exc}") from exc


@router.post("/generate")
def start_generate(payload: TemplateGenerateRequest) -> Dict[str, Any]:
    _refresh_template_ids()  # V21：允许 custom 模板
    tids = _parse_templates(payload.templates)
    resolution = _parse_resolution(payload.resolution)
    force = bool(payload.force)
    dry_run = bool(payload.dry_run)

    if not dry_run:  # dry-run 不启动子进程，只预览命令，不受后台 GPU 任务影响
        external_pid = _external_generation_pid()
        if external_pid is not None:
            raise HTTPException(
                status_code=409, detail=_conflict_message_external_generate(external_pid)
            )

    cmd = _generate_cmd(tids, force, resolution)
    job = _reserve_or_409(
        GENERATE_CONFLICT_KINDS,
        KIND_TEMPLATES_GENERATE,
        tids,
        _cmd=cmd,
        _mode="generate",
        _timeout=GENERATE_TIMEOUT_SECONDS,
        _templates=list(tids),
        message=f"模板 3D 生成排队中（{len(tids)} 个模板）",
        templates=list(tids),
        force=force,
        resolution=resolution,
        dry_run=dry_run,
        result={"mode": "generate", "templates": list(tids), "force": force, "dry_run": dry_run},
    )
    if dry_run:
        _update_job(
            job["id"],
            status="done",
            progress=100.0,
            finished_at=_now_text(),
            message="dry-run：未执行（命令见 logs/result）",
            result={
                "mode": "generate",
                "dry_run": True,
                "templates": list(tids),
                "force": force,
                "command": cmd,
            },
        )
        _append_log(job["id"], "[dry-run] " + " ".join(cmd))
    else:
        _spawn_template_job(job["id"])
    logger.info("templates3d 生成任务入队：%s %s", job["id"], ",".join(tids))
    return {"job_id": job["id"], "templates": list(tids), "dry_run": bool(payload.dry_run)}


# --------------------------------------------------------------------------- #
# POST /api/cinema/templates/assemble
# --------------------------------------------------------------------------- #
@router.post("/assemble")
def start_assemble(payload: TemplateAssembleRequest) -> Dict[str, Any]:
    _refresh_template_ids()  # V21：允许 custom 模板
    tids = _parse_templates(payload.templates)
    force = bool(payload.force)
    dry_run = bool(payload.dry_run)

    warning = None
    if not dry_run:  # dry-run 不启动子进程，只预览命令
        watcher_pids = _external_watcher_pids()
        external_assemble = _external_assemble_pids()
        if external_assemble:
            raise HTTPException(
                status_code=409,
                detail=(
                    f"已有后台 3D 合成进程在运行（PID {external_assemble[0]}），"
                    "请等它结束后再触发，避免同一个 final.mp4 被并发覆盖"
                ),
            )
        if force and watcher_pids:
            raise HTTPException(
                status_code=409,
                detail=(
                    f"后台 assemble-watch 守护正在运行（PID {watcher_pids[0]}），"
                    "force 合成会和守护抢同一个成片；请等守护结束（全部模板 done 后自动退出）再 force 合成"
                ),
            )
        if watcher_pids:
            warning = (
                f"后台 assemble-watch 守护正在运行（PID {watcher_pids[0]}），会自动剪辑已生成完的模板；"
                "本任务只会处理尚未剪辑的模板（非 force）"
            )

    cmd = _assemble_cmd(tids, force)
    job = _reserve_or_409(
        ASSEMBLE_CONFLICT_KINDS,
        KIND_TEMPLATES_ASSEMBLE,
        tids,
        _cmd=cmd,
        _mode="assemble",
        _timeout=ASSEMBLE_TIMEOUT_SECONDS,
        _templates=list(tids),
        message=f"模板 3D 合成排队中（{len(tids)} 个模板）",
        templates=list(tids),
        force=force,
        dry_run=dry_run,
        result={"mode": "assemble", "templates": list(tids), "force": force, "dry_run": dry_run},
    )
    if dry_run:
        _update_job(
            job["id"],
            status="done",
            progress=100.0,
            finished_at=_now_text(),
            message="dry-run：未执行（命令见 logs/result）",
            result={
                "mode": "assemble",
                "dry_run": True,
                "templates": list(tids),
                "force": force,
                "command": cmd,
            },
        )
        _append_log(job["id"], "[dry-run] " + " ".join(cmd))
    else:
        _spawn_template_job(job["id"])
    logger.info("templates3d 合成任务入队：%s %s", job["id"], ",".join(tids))
    response: Dict[str, Any] = {"job_id": job["id"], "templates": list(tids), "dry_run": bool(payload.dry_run)}
    if warning:
        response["warning"] = warning
    return response


# --------------------------------------------------------------------------- #
# V26 发布包导出（zip：成片/封面/中文字幕/元数据/时间线/报告/README）
# --------------------------------------------------------------------------- #
PUBLISH_DIRNAME = "publish"
PUBLISH_PLATFORMS = ("youtube", "tiktok", "instagram", "generic")
PUBLISH_VERTICAL_PLATFORMS = ("tiktok", "instagram")
PUBLISH_VERTICAL_MODES = ("blur_pad", "crop")
PUBLISH_VERTICAL_SIZE = (1080, 1920)
PUBLISH_VERTICAL_FG = (1080, 608)
PUBLISH_KEEP = 5
PUBLISH_PLATFORM_LABEL = {
    "youtube": "YouTube",
    "tiktok": "TikTok",
    "instagram": "Instagram Reels",
    "generic": "通用",
}
_PUBLISH_LOCK = threading.Lock()
_ASS_TIME_RE = re.compile(r"^(?:(\d+):)?(\d{1,2}):(\d{1,2}(?:\.\d+)?)$")


def _publish_dir(tid: str) -> Path:
    return T3D_ROOT / tid / PUBLISH_DIRNAME


def _publish_zip_path(tid: str, platform: str) -> Path:
    return _publish_dir(tid) / f"{tid}_{platform}.zip"


def _publish_prune(tid: str) -> None:
    """只保留最近 5 个发布包（按修改时间倒序）。"""
    try:
        zips = sorted(_publish_dir(tid).glob("*.zip"),
                      key=lambda path: path.stat().st_mtime, reverse=True)
    except OSError:
        return
    for old in zips[PUBLISH_KEEP:]:
        try:
            old.unlink()
        except OSError:
            pass


def _publish_zip_metadata(zip_path: Path) -> Optional[Dict[str, Any]]:
    """读取 zip 内的 metadata.json（用于缓存命中校验/返回竖版信息）。"""
    try:
        with zipfile.ZipFile(zip_path) as zf:
            if "metadata.json" not in zf.namelist():
                return None
            data = json.loads(zf.read("metadata.json").decode("utf-8"))
            return data if isinstance(data, dict) else None
    except (OSError, ValueError, UnicodeDecodeError, zipfile.BadZipFile):
        return None


def _publish_vertical_payload(metadata: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    metadata = metadata or {}
    info = metadata.get("vertical") if isinstance(metadata.get("vertical"), dict) else {}
    return {
        "enabled": bool(metadata.get("vertical_requested")),
        "generated": bool(info.get("file")),
        "file": info.get("file"),
        "cover_file": info.get("cover_file"),
        "resolution": info.get("resolution") or f"{PUBLISH_VERTICAL_SIZE[0]}x{PUBLISH_VERTICAL_SIZE[1]}"
        if metadata.get("vertical_requested") else None,
        "mode": info.get("mode") or metadata.get("vertical_mode") or "blur_pad",
        "duration_sec": info.get("duration_sec"),
        "size_bytes": info.get("size_bytes"),
        "error": info.get("error"),
    }


def _publish_cache_compatible(tid: str, platform: str, vertical: bool,
                              vertical_mode: str) -> bool:
    """缓存是否满足本次请求（含竖版开关/模式）；不满足则重建。"""
    zip_path = _publish_zip_path(tid, platform)
    try:
        if not zip_path.is_file() or zip_path.stat().st_size <= 0:
            return False
    except OSError:
        return False
    metadata = _publish_zip_metadata(zip_path)
    if not isinstance(metadata, dict):
        return False
    if str(metadata.get("platform") or "") != platform:
        return False
    if bool(metadata.get("vertical_requested")) != bool(vertical):
        return False
    if vertical and str(metadata.get("vertical_mode") or "") != vertical_mode:
        return False
    return True


def _publish_build_vertical(tid: str, cover: Optional[Path], workdir: Path, mode: str,
                            warnings: List[str]) -> Tuple[Optional[Path], Optional[Path],
                                                          Dict[str, Any]]:
    """生成 1080×1920 竖版视频 + 竖版封面（blur_pad 模糊填充 / crop 中心裁切）。

    返回 (video_path, cover_path, vertical_info)；失败只记 warning，不阻塞 16:9 包。
    """
    if mode not in PUBLISH_VERTICAL_MODES:
        message = f"不支持的 vertical_mode: {mode}（仅 {', '.join(PUBLISH_VERTICAL_MODES)}）"
        warnings.append(message)
        return None, None, {"mode": mode, "error": message}
    final = _final_path(tid)
    width, height = PUBLISH_VERTICAL_SIZE
    fg_width, fg_height = PUBLISH_VERTICAL_FG
    video_out = workdir / "final_vertical.mp4"
    if mode == "crop":
        # 中心裁切 9:16：16:9 源片取中心 405×720，再 lanczos 放大到 1080×1920
        # （表达式兼容 poster 等非 1280×720 输入）
        crop_expr = "crop='min(iw,ih*9/16)':'ih':'(iw-ow)/2':'(ih-oh)/2'"
        video_filter = (
            f"[0:v]{crop_expr},"
            f"scale={width}:{height}:flags=lanczos,format=yuv420p,fps=25[v]"
        )
    else:
        video_filter = (
            f"[0:v]split=2[src_bg][src_fg];"
            f"[src_bg]scale={width}:{height}:force_original_aspect_ratio=increase,"
            f"crop={width}:{height},gblur=sigma=30[bg];"
            f"[src_fg]scale={fg_width}:{fg_height}[fg];"
            f"[bg][fg]overlay=(W-w)/2:(H-h)/2,format=yuv420p,fps=25[v]"
        )
    started = time.time()
    try:
        proc = subprocess.run(
            [str(jobs.ffmpeg_bin()), "-y", "-i", str(final),
             "-filter_complex", video_filter,
             "-map", "[v]", "-map", "0:a?",
             "-c:v", "libx264", "-preset", "medium", "-crf", "20",
             "-c:a", "aac", "-b:a", "128k", "-shortest",
             "-movflags", "+faststart", str(video_out)],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=300,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        message = f"竖版视频生成失败: {type(exc).__name__}: {exc}"
        warnings.append(message)
        return None, None, {"mode": mode, "error": message[:500]}
    if proc.returncode != 0 or not video_out.is_file() or video_out.stat().st_size <= 0:
        tail = "\n".join((proc.stderr or "").strip().splitlines()[-8:])
        message = f"竖版视频 ffmpeg 失败（returncode={proc.returncode}）：{tail[-600:]}"
        warnings.append(message)
        return None, None, {"mode": mode, "error": message[:800]}
    probe = _publish_probe(video_out)
    video_info: Dict[str, Any] = {
        "file": "video/final_vertical.mp4",
        "resolution": probe.get("resolution") or f"{width}x{height}",
        "width": probe.get("width") or width,
        "height": probe.get("height") or height,
        "fps": probe.get("fps"),
        "mode": mode,
        "duration_sec": probe.get("duration_sec"),
        "size_bytes": int(video_out.stat().st_size),
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "elapsed_sec": round(time.time() - started, 2),
    }
    cover_out: Optional[Path] = None
    if cover is not None:
        cover_out = workdir / "cover_vertical.jpg"
        if mode == "crop":
            crop_expr = "crop='min(iw,ih*9/16)':'ih':'(iw-ow)/2':'(ih-oh)/2'"
            cover_filter = (
                f"[0:v]{crop_expr},"
                f"scale={width}:{height}:flags=lanczos[out]"
            )
        else:
            cover_filter = (
                f"[0:v]split=2[src_bg][src_fg];"
                f"[src_bg]scale={width}:{height}:force_original_aspect_ratio=increase,"
                f"crop={width}:{height},gblur=sigma=30[bg];"
                f"[src_fg]scale={fg_width}:{fg_height}[fg];"
                f"[bg][fg]overlay=(W-w)/2:(H-h)/2[out]"
            )
        try:
            cover_proc = subprocess.run(
                [str(jobs.ffmpeg_bin()), "-y", "-loop", "1", "-i", str(cover),
                 "-filter_complex", cover_filter, "-map", "[out]",
                 "-frames:v", "1", "-q:v", "3", str(cover_out)],
                capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120,
            )
            if (cover_proc.returncode == 0 and cover_out.is_file()
                    and cover_out.stat().st_size > 0):
                cover_probe = _publish_probe(cover_out)
                video_info["cover_file"] = "cover/cover_vertical.jpg"
                video_info["cover_resolution"] = (
                    cover_probe.get("resolution") or f"{width}x{height}")
            else:
                warnings.append("竖版封面生成失败（ffmpeg 返回非 0），仅保留 16:9 封面")
                cover_out = None
        except (OSError, subprocess.TimeoutExpired) as exc:
            warnings.append(f"竖版封面生成失败: {type(exc).__name__}: {exc}")
            cover_out = None
    else:
        warnings.append("竖版封面未生成（无可用 16:9 封面）")
    return video_out, cover_out, video_info


def _publish_view(tid: str, platform: str, *, cached: bool) -> Dict[str, Any]:
    zip_path = _publish_zip_path(tid, platform)
    stat = zip_path.stat()
    files: List[Dict[str, Any]] = []
    try:
        with zipfile.ZipFile(zip_path) as zf:
            for info in zf.infolist():
                if info.is_dir():
                    continue
                files.append({"name": info.filename, "size": int(info.file_size)})
    except (OSError, zipfile.BadZipFile):
        files = []
    metadata = _publish_zip_metadata(zip_path)
    return {
        "ok": True,
        "template_id": tid,
        "package_url": _t3d_media(tid, f"{PUBLISH_DIRNAME}/{zip_path.name}"),
        "filename": zip_path.name,
        "size_bytes": int(stat.st_size),
        "files": files,
        "cached": cached,
        "platform": platform,
        "platform_label": PUBLISH_PLATFORM_LABEL.get(platform, platform),
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(stat.st_mtime)),
        "vertical": _publish_vertical_payload(metadata),
        "path": str(zip_path),
    }


def _ass_time_seconds(value: str) -> Optional[float]:
    match = _ASS_TIME_RE.match(str(value or "").strip())
    if not match:
        return None
    hours = int(match.group(1) or 0)
    minutes = int(match.group(2))
    seconds = float(match.group(3))
    return hours * 3600 + minutes * 60 + seconds


def _format_srt_time(seconds: float) -> str:
    total_ms = max(0, int(round(float(seconds) * 1000)))
    hours, rest = divmod(total_ms, 3600_000)
    minutes, rest = divmod(rest, 60_000)
    secs, millis = divmod(rest, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


def _clean_subtitle_text(value: str) -> str:
    text = re.sub(r"\{[^}]*\}", "", str(value or ""))
    text = text.replace("\\N", "\n").replace("\\n", "\n").replace("\\h", " ")
    return text.strip()


def _srt_entries_from_ass(path: Path) -> List[Tuple[float, float, str]]:
    entries: List[Tuple[float, float, str]] = []
    try:
        text = path.read_text(encoding="utf-8-sig", errors="replace")
    except OSError:
        return entries
    for line in text.splitlines():
        if not line.startswith("Dialogue:"):
            continue
        parts = line[len("Dialogue:"):].strip().split(",", 9)
        if len(parts) < 10:
            continue
        start = _ass_time_seconds(parts[1])
        end = _ass_time_seconds(parts[2])
        content = _clean_subtitle_text(parts[9])
        if start is None or end is None or end <= start or not content:
            continue
        entries.append((start, end, content))
    entries.sort(key=lambda item: item[0])
    return entries


def _srt_entries_from_timeline(timeline: Any) -> List[Tuple[float, float, str]]:
    entries: List[Tuple[float, float, str]] = []
    if not isinstance(timeline, dict):
        return entries
    for track in timeline.get("tracks") or []:
        if not isinstance(track, dict):
            continue
        kind = str(track.get("kind") or track.get("type") or "").lower()
        if kind not in ("subtitle", "subtitles", "sub", "text", "caption"):
            continue
        for clip in track.get("clips") or []:
            if not isinstance(clip, dict):
                continue
            text = _clean_subtitle_text(clip.get("text") or clip.get("line") or "")
            if not text:
                continue
            try:
                start = float(clip.get("at") if clip.get("at") is not None else clip.get("start"))
                end = float(clip.get("out") if clip.get("out") is not None else clip.get("end"))
            except (TypeError, ValueError):
                continue
            if end <= start:
                end = start + 2.0
            entries.append((start, end, text))
    entries.sort(key=lambda item: item[0])
    return entries


def _srt_entries_from_episode(tid: str) -> List[Tuple[float, float, str]]:
    episode = None
    for path in (T3D_ROOT / tid / "work" / "episode.json",
                 V5_ROOT / tid / "work" / "episode.json"):
        data = _JSON_CACHE.get(path)
        if isinstance(data, dict):
            episode = data
            break
    if not isinstance(episode, dict):
        return []
    entries: List[Tuple[float, float, str]] = []
    cursor = 0.0
    for shot in episode.get("shots") or []:
        if not isinstance(shot, dict):
            continue
        duration = float(shot.get("duration") or 0.0)
        start = shot.get("_edit_start")
        start = float(start) if isinstance(start, (int, float)) else cursor
        cursor = max(cursor, start + max(0.0, duration))
        text = _clean_subtitle_text(shot.get("line") or "")
        if not text or duration <= 0:
            continue
        speech = float(shot.get("speech_duration") or 0.0)
        end = start + max(1.2, min(duration - 0.05, (speech + 0.5) if speech > 0 else duration - 0.3))
        if end <= start + 0.2:
            end = start + 1.2
        entries.append((start + 0.1, end, text))
    entries.sort(key=lambda item: item[0])
    return entries


def _publish_subtitles(tid: str, warnings: List[str]) -> Tuple[bytes, int, str]:
    """优先 work/final.ass → timeline → episode；返回 (srt_bytes, 条数, 来源)。"""
    ass_path = T3D_ROOT / tid / "work" / "final.ass"
    if ass_path.is_file() and ass_path.stat().st_size > 0:
        entries = _srt_entries_from_ass(ass_path)
        if entries:
            return _srt_text(entries).encode("utf-8"), len(entries), "work/final.ass"
        warnings.append("work/final.ass 存在但没有可解析的字幕 Dialogue，尝试 timeline/episode")
    for path in (T3D_ROOT / tid / "timeline.json",
                 T3D_ROOT / tid / "work" / "timeline.json"):
        data = _JSON_CACHE.get(path)
        entries = _srt_entries_from_timeline(data)
        if entries:
            warnings.append("字幕从 timeline.json 合成（final.ass 缺失/为空）")
            return _srt_text(entries).encode("utf-8"), len(entries), str(path.relative_to(T3D_ROOT))
    entries = _srt_entries_from_episode(tid)
    if entries:
        warnings.append("字幕从 work/episode.json 台词合成（final.ass/timeline 缺失）")
        return _srt_text(entries).encode("utf-8"), len(entries), "work/episode.json"
    warnings.append("未找到字幕台词（final.ass/timeline/episode 均为空），subtitles/zh.srt 为空文件")
    return b"", 0, "empty"


def _srt_text(entries: Sequence[Tuple[float, float, str]]) -> str:
    blocks = []
    for index, (start, end, text) in enumerate(entries, 1):
        blocks.append(f"{index}\n{_format_srt_time(start)} --> {_format_srt_time(end)}\n{text}\n")
    return "\n".join(blocks)


def _publish_cover(tid: str, workdir: Path, warnings: List[str]) -> Optional[Path]:
    poster = _poster_path(tid)
    if poster.is_file() and poster.stat().st_size > 0:
        return poster
    final = _final_path(tid)
    cover = workdir / "cover.jpg"
    try:
        proc = subprocess.run(
            [str(jobs.ffmpeg_bin()), "-y", "-ss", "1.0", "-i", str(final),
             "-frames:v", "1", "-q:v", "3", str(cover)],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60,
        )
        if proc.returncode == 0 and cover.is_file() and cover.stat().st_size > 0:
            warnings.append("封面从 final.mp4 第 1s 抽帧生成（poster.jpg 不存在）")
            return cover
    except (OSError, subprocess.TimeoutExpired) as exc:
        warnings.append(f"封面抽帧失败: {exc}")
    warnings.append("未生成 cover/cover.jpg（poster 与抽帧均失败）")
    return None


def _publish_probe(path: Path) -> Dict[str, Any]:
    try:
        proc = subprocess.run(
            [str(jobs.ffprobe_bin()), "-v", "error", "-show_streams",
             "-show_format", "-of", "json", str(path)],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60,
        )
        data = json.loads(proc.stdout or "{}")
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return {}
    video = next((stream for stream in data.get("streams") or []
                  if stream.get("codec_type") == "video"), {})
    fmt = data.get("format") if isinstance(data.get("format"), dict) else {}
    fps = 0.0
    raw_rate = str(video.get("r_frame_rate") or video.get("avg_frame_rate") or "0/1")
    try:
        num, _, den = raw_rate.partition("/")
        fps = round(float(num) / float(den or 1), 3)
    except (TypeError, ValueError, ZeroDivisionError):
        fps = 0.0
    width = int(video.get("width") or 0)
    height = int(video.get("height") or 0)
    return {
        "codec": video.get("codec_name"),
        "width": width,
        "height": height,
        "resolution": f"{width}x{height}" if width and height else None,
        "fps": fps,
        "duration_sec": round(float(fmt.get("duration") or 0.0), 2),
        "size_bytes": int(fmt.get("size") or 0),
    }


def _publish_cast(contract: Dict[str, Any]) -> List[Dict[str, str]]:
    rows: List[Dict[str, str]] = []
    base_dirs = (config.CONTRACT_DIR / "v5" / "characters",
                 config.CONTRACT_DIR / "v5" / "custom" / "characters")
    for entry in contract.get("cast") or []:
        if not isinstance(entry, dict):
            continue
        cid = str(entry.get("character") or "").strip()
        role = str(entry.get("role") or "").strip()
        name = str(entry.get("name") or "").strip()
        if not name and cid:
            for base in base_dirs:
                data = _JSON_CACHE.get(base / f"{cid}.json")
                if isinstance(data, dict):
                    name = str(data.get("label") or data.get("name") or "").strip()
                    if name:
                        break
        rows.append({"role": role, "character": cid, "name": name or cid})
    return rows


def _publish_metadata(tid: str, contract: Dict[str, Any], final_info: Dict[str, Any],
                      platform: str, srt_count: int) -> Dict[str, Any]:
    final = _final_path(tid)
    probe = _publish_probe(final)
    cast = _publish_cast(contract)
    label = str(contract.get("label") or _template_label(tid) or tid)
    description = str(contract.get("description") or "").strip()
    if not description:
        description = f"{label} · AI 生成的原创短视频"
    cast_text = "、".join(f"{row['name']}（{row['role']}）" for row in cast if row.get("name"))
    if cast_text:
        description += f"｜阵容：{cast_text}"
    style = str(contract.get("style") or "")
    if style:
        description += f"｜风格：{style}"
    tags: List[str] = []
    for tag in list(contract.get("tags") or []) + [style, "AI视频",
                                                   PUBLISH_PLATFORM_LABEL.get(platform, platform)]:
        value = str(tag or "").strip()
        if value and value not in tags:
            tags.append(value)
    report = _JSON_CACHE.get(T3D_ROOT / tid / "report.json")
    report = report if isinstance(report, dict) else {}
    report_qc = report.get("qc") if isinstance(report.get("qc"), dict) else {}
    checks = report_qc.get("checks") or report.get("checks") or []
    try:
        stat = final.stat()
    except OSError:
        stat = None
    metadata = {
        "title": label,
        "description": description,
        "tags": tags,
        "template_id": tid,
        "template_label": label,
        "style": style,
        "cast": cast,
        "duration_sec": float(final_info.get("duration") or probe.get("duration_sec") or 0.0),
        "resolution": probe.get("resolution"),
        "fps": probe.get("fps"),
        "size_bytes": int(stat.st_size) if stat else int(final_info.get("size") or 0),
        "generated_at": (time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(stat.st_mtime))
                         if stat else time.strftime("%Y-%m-%dT%H:%M:%S")),
        "platform": platform,
        "platform_label": PUBLISH_PLATFORM_LABEL.get(platform, platform),
        "qc": {
            "ok": bool(final_info.get("exists")),
            "report_present": bool(report),
            "probe": probe,
            "checks": checks,
            "subtitle_lines": srt_count,
            "final_url": _t3d_media(tid, "final.mp4"),
        },
    }
    return metadata


def _publish_readme(tid: str, contract: Dict[str, Any], metadata: Dict[str, Any],
                    platform: str, srt_count: int, srt_source: str,
                    has_cover: bool, warnings: Sequence[str]) -> str:
    label = metadata.get("title") or tid
    cast_text = "、".join(f"{row['name']}（{row['role']}）"
                          for row in metadata.get("cast") or [] if row.get("name")) or "—"
    notes = {
        "youtube": [
            "YouTube：可直接上传 16:9 成片（当前 {res}，{fps}fps）；",
            "标题/简介/标签见 metadata.json；字幕上传 subtitles/zh.srt；",
            "封面建议 1280×720，可将 cover/cover.jpg 上传为自定义缩略图；",
            "若走 Shorts，请使用竖版 9:16 另行导出。",
        ],
        "tiktok": [
            "TikTok：优先 9:16 竖版，当前成片为 {res}，上传前建议裁切/加边框；",
            "可直接上传 video/final.mp4，平台会自动转码；",
            "字幕可用 subtitles/zh.srt（TikTok 也支持自动字幕）；",
            "标题/文案参考 metadata.json 的 description。",
        ],
        "instagram": [
            "Instagram Reels：建议 9:16 竖版（当前 {res}），可裁切/加边框；",
            "时长与分辨率建议 ≤ 90s / 1080p；封面可用 cover/cover.jpg；",
            "字幕可烧录或参考 subtitles/zh.srt 文案。",
        ],
        "generic": [
            "通用包：video/final.mp4 为成片，cover/cover.jpg 为封面；",
            "subtitles/zh.srt 为 SRT 中文字幕，metadata.json 为标题/简介/标签；",
            "按目标平台要求调整画幅（TikTok/IG 建议 9:16）后上传。",
        ],
    }.get(platform, [])
    lines = [
        f"发布包：{label}（模板 {tid}）",
        f"平台：{PUBLISH_PLATFORM_LABEL.get(platform, platform)}（platform={platform}）",
        f"生成时间：{metadata.get('generated_at')}",
        "",
        "一、包内文件",
        "  video/final.mp4       成片（{:.2f}s，{}，{}fps）".format(
            float(metadata.get("duration_sec") or 0), metadata.get("resolution"),
            metadata.get("fps")),
        "  cover/cover.jpg       封面（{}）".format("poster.jpg" if has_cover else "未生成"),
        "  subtitles/zh.srt      中文字幕（{} 条，来源 {}）".format(srt_count, srt_source),
        "  metadata.json         标题/简介/标签/角色/画质信息",
        "  timeline.json         时间线（若模板有）",
        "  report.json           合成报告/探测信息（若模板有）",
        "  README.txt            本说明",
    ]
    if metadata.get("vertical_requested"):
        vertical = metadata.get("vertical") if isinstance(metadata.get("vertical"), dict) else {}
        if vertical.get("file"):
            lines.extend([
                "  video/final_vertical.mp4  竖版 9:16 成片（1080×1920，blur_pad 模糊背景填充）",
                "  cover/cover_vertical.jpg  竖版封面（1080×1920）",
            ])
        else:
            lines.append(
                "  （竖版 9:16 生成失败：{}；16:9 包仍可正常上传）".format(
                    vertical.get("error") or "未知错误"))
    lines.extend([
        "",
        "二、上传步骤",
        "  1. 登录目标平台创作中心，选择上传视频；",
        "  2. 上传 video/final.mp4（TikTok/IG 建议改用 video/final_vertical.mp4）；",
        "  3. 复制 metadata.json 的 title / description / tags 填写；",
        "  4. 上传 cover/cover.jpg 作为封面（竖版平台可用 cover/cover_vertical.jpg）；",
        "  5. 字幕：上传 subtitles/zh.srt 或在平台自动生成；",
        "  6. 发布前检查画幅/时长是否符合平台要求。",
        "",
        "三、平台注意",
        *[f"  - {note.format(res=metadata.get('resolution'), fps=metadata.get('fps'))}"
          for note in notes],
        "",
        "四、生成信息",
        f"  模板：{tid}（{contract.get('label') or label}）",
        f"  风格：{metadata.get('style') or '—'}",
        f"  阵容：{cast_text}",
        f"  画质 QC：{'ok' if (metadata.get('qc') or {}).get('ok') else 'unknown'}",
        "  账号尚未连接：本包为本地导出，需手动上传。",
        "",
    ])
    if metadata.get("vertical_requested"):
        vertical = metadata.get("vertical") if isinstance(metadata.get("vertical"), dict) else {}
        if vertical.get("file"):
            mode = str(vertical.get("mode") or "blur_pad")
            if mode == "crop":
                mode_lines = [
                    "五、竖版 9:16（中心裁切 crop）",
                    "  - 画面：原片中心裁切 405×720（9:16）后 lanczos 放大到 1080×1920；",
                    "  - 编码：H.264 + AAC，yuv420p，25fps；时长与原片一致；",
                    "  - 上传 TikTok / Instagram Reels 时优先使用 video/final_vertical.mp4。",
                    "",
                ]
            else:
                mode_lines = [
                    "五、竖版 9:16（模糊背景填充 blur_pad）",
                    "  - 背景：原片放大填满 1080×1920 后 gblur(sigma=30)；前景：原片缩放到 1080×608 居中；",
                    "  - 编码：H.264 + AAC，yuv420p，25fps；时长与原片一致；",
                    "  - 上传 TikTok / Instagram Reels 时优先使用 video/final_vertical.mp4。",
                    "",
                ]
            lines.extend(mode_lines)
    if warnings:
        lines.append("六、构建警告")
        lines.extend(f"  - {item}" for item in warnings)
        lines.append("")
    return "\n".join(lines)


def _build_publish_package(tid: str, platform: str, force: bool,
                           vertical: bool = False,
                           vertical_mode: str = "blur_pad") -> Dict[str, Any]:
    final = _final_path(tid)
    try:
        final_ok = final.is_file() and final.stat().st_size > 0
    except OSError:
        final_ok = False
    if not final_ok:
        raise HTTPException(status_code=400,
                            detail=f"模板 {tid} 没有成片 final.mp4，请先生成/合成后再导出发布包")
    zip_path = _publish_zip_path(tid, platform)
    with _PUBLISH_LOCK:
        try:
            if (zip_path.is_file() and zip_path.stat().st_size > 0 and not force
                    and _publish_cache_compatible(tid, platform, vertical, vertical_mode)):
                return _publish_view(tid, platform, cached=True)
        except OSError:
            pass
        publish_dir = _publish_dir(tid)
        publish_dir.mkdir(parents=True, exist_ok=True)
        workdir = Path(tempfile.mkdtemp(prefix=f".{tid}-publish-", dir=str(T3D_ROOT / tid)))
        warnings: List[str] = []
        metadata: Dict[str, Any] = {}
        try:
            contract = _template_contract(tid) or {"id": tid, "label": _template_label(tid)}
            final_info = _final_info(tid)
            srt_bytes, srt_count, srt_source = _publish_subtitles(tid, warnings)
            cover = _publish_cover(tid, workdir, warnings)
            metadata = _publish_metadata(tid, contract, final_info, platform, srt_count)
            entries: Dict[str, Any] = {"video/final.mp4": final}
            if cover is not None:
                entries["cover/cover.jpg"] = cover
            entries["subtitles/zh.srt"] = srt_bytes
            # V27：竖版 9:16（TikTok/IG 默认开启）；失败只 warning，不阻塞 16:9 包
            metadata["vertical_requested"] = bool(vertical)
            metadata["vertical_mode"] = vertical_mode
            if vertical:
                vertical_video, vertical_cover, vertical_info = _publish_build_vertical(
                    tid, cover, workdir, vertical_mode, warnings)
                metadata["vertical"] = vertical_info
                if vertical_video is not None:
                    entries["video/final_vertical.mp4"] = vertical_video
                if vertical_cover is not None:
                    entries["cover/cover_vertical.jpg"] = vertical_cover
            else:
                metadata["vertical"] = None
            readme = _publish_readme(tid, contract, metadata, platform, srt_count,
                                     srt_source, bool(cover), warnings)
            entries["metadata.json"] = json.dumps(metadata, ensure_ascii=False, indent=2).encode("utf-8")
            entries["README.txt"] = readme.encode("utf-8")
            timeline = T3D_ROOT / tid / "timeline.json"
            if not timeline.is_file():
                timeline = T3D_ROOT / tid / "work" / "timeline.json"
            if timeline.is_file():
                entries["timeline.json"] = timeline
            else:
                warnings.append("timeline.json 不存在，包内未包含")
            report = T3D_ROOT / tid / "report.json"
            if report.is_file():
                entries["report.json"] = report
            else:
                warnings.append("report.json 不存在，包内未包含")
            tmp_zip = publish_dir / f".{zip_path.name}.tmp-{os.getpid()}-{threading.get_ident()}"
            try:
                with zipfile.ZipFile(tmp_zip, "w", compression=zipfile.ZIP_DEFLATED,
                                     compresslevel=6) as zf:
                    for arcname, source in entries.items():
                        if isinstance(source, (bytes, bytearray)):
                            zf.writestr(arcname, bytes(source))
                        else:
                            zf.write(source, arcname=arcname)
                os.replace(tmp_zip, zip_path)
            finally:
                try:
                    tmp_zip.unlink(missing_ok=True)
                except OSError:
                    pass
        finally:
            shutil.rmtree(workdir, ignore_errors=True)
        _publish_prune(tid)
        view = _publish_view(tid, platform, cached=False)
        view["warnings"] = warnings
        view["vertical"] = _publish_vertical_payload(metadata)
        view["metadata_summary"] = {
            "title": metadata.get("title"),
            "duration_sec": metadata.get("duration_sec"),
            "resolution": metadata.get("resolution"),
            "fps": metadata.get("fps"),
            "size_bytes": metadata.get("size_bytes"),
            "tags": metadata.get("tags"),
            "cast": [row.get("name") for row in metadata.get("cast") or []],
            "subtitle_lines": (metadata.get("qc") or {}).get("subtitle_lines"),
            "subtitle_source": srt_source,
            "vertical_generated": bool((metadata.get("vertical") or {}).get("file")),
            "vertical_resolution": (metadata.get("vertical") or {}).get("resolution"),
        }
        return view


class PublishPackageBody(BaseModel):
    platform: str = "generic"
    force: bool = False
    # V27：竖版 9:16；None 时按平台默认（tiktok/instagram=true，其它 false）
    vertical: Optional[bool] = None
    vertical_mode: str = "blur_pad"


@router.post("/{tid}/publish-package", summary="导出发布包 zip（成片/封面/中文字幕/元数据/README）")
def build_publish_package(tid: str,
                          payload: PublishPackageBody = Body(default=PublishPackageBody())) -> Dict[str, Any]:
    _refresh_template_ids()
    key = str(tid or "").strip()
    if not _TEMPLATE_ID_RE.fullmatch(key):
        raise HTTPException(status_code=400,
                            detail="template_id 不合法（仅允许 [a-z][a-z0-9_]{1,60}）")
    if key not in TEMPLATE_SET:
        raise HTTPException(status_code=404, detail=f"模板不存在：{tid}")
    platform = str(payload.platform or "generic").strip().lower()
    if platform not in PUBLISH_PLATFORMS:
        raise HTTPException(status_code=400,
                            detail=f"platform 只支持 {', '.join(PUBLISH_PLATFORMS)}（当前 {platform!r}）")
    vertical_mode = str(payload.vertical_mode or "blur_pad").strip().lower()
    if vertical_mode not in PUBLISH_VERTICAL_MODES:
        raise HTTPException(status_code=400,
                            detail=f"vertical_mode 只支持 {', '.join(PUBLISH_VERTICAL_MODES)}（当前 {vertical_mode!r}）")
    vertical = (bool(payload.vertical) if payload.vertical is not None
                else platform in PUBLISH_VERTICAL_PLATFORMS)
    return _build_publish_package(key, platform, bool(payload.force),
                                  vertical=vertical, vertical_mode=vertical_mode)


@router.get("/{tid}/publish-package", summary="列出该模板已生成的发布包（最近 5 个）")
def list_publish_packages(tid: str) -> Dict[str, Any]:
    _refresh_template_ids()
    key = str(tid or "").strip()
    if not _TEMPLATE_ID_RE.fullmatch(key):
        raise HTTPException(status_code=400,
                            detail="template_id 不合法（仅允许 [a-z][a-z0-9_]{1,60}）")
    if key not in TEMPLATE_SET:
        raise HTTPException(status_code=404, detail=f"模板不存在：{tid}")
    _publish_prune(key)
    rows: List[Dict[str, Any]] = []
    try:
        zips = sorted(_publish_dir(key).glob("*.zip"),
                      key=lambda path: path.stat().st_mtime, reverse=True)
    except OSError:
        zips = []
    for path in zips:
        platform = path.stem.rsplit("_", 1)[-1]
        try:
            stat = path.stat()
        except OSError:
            continue
        rows.append({
            "filename": path.name,
            "package_url": _t3d_media(key, f"{PUBLISH_DIRNAME}/{path.name}"),
            "size_bytes": int(stat.st_size),
            "platform": platform if platform in PUBLISH_PLATFORMS else "unknown",
            "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(stat.st_mtime)),
            "mtime_ts": stat.st_mtime,
        })
    return {"ok": True, "template_id": key, "count": len(rows),
            "packages": rows, "keep_limit": PUBLISH_KEEP}


# --------------------------------------------------------------------------- #
# GET /api/cinema/templates/{tid}（必须最后注册，避免吃掉 /status 等固定路径）
# --------------------------------------------------------------------------- #
@router.get("/{tid}")
def get_template(tid: str, response: Response) -> Dict[str, Any]:
    response.headers["Cache-Control"] = "no-store, max-age=0"
    _refresh_template_ids()  # V21：新建 custom 模板后详情即时可见
    key = str(tid or "").strip()
    if key not in TEMPLATE_SET:
        raise HTTPException(
            status_code=404,
            detail=f"模板不存在：{tid}；可选模板：{', '.join(TEMPLATES)}",
        )
    progress = _load_progress()
    view = _template_view(key, progress, allow_poster_generate=True)
    view["shots"] = _shots_detail(key, progress)
    view["look"] = _load_briefs(key).get("look") or _load_semantic(key).get("look")
    return view
