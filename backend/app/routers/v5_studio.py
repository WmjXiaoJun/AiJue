# -*- coding: utf-8 -*-
"""V5 Studio API：成片画廊 / 静态媒体 / 角色声音修改 / 后台渲染任务。

挂载在既有 V5 契约 API 之外，路径前缀独立：
- `GET  /api/v5/outputs`                    扫描 outputs/templates_v5/*/ → 成片画廊数据
  （返回的媒体 URL 为真实路径 `/media/templates_v5/<id>/<file>`，由主应用 `/media` 静态挂载）
- `GET  /media/v5/{template}/{file}`        安全返回 final.mp4 / timeline.html / qc_report.json / style_report.json / contact_sheet.png（兼容旧前端，保留）
- `GET  /media/v5/characters/{id}/{file}`   角色 AI 立绘（assets/v5/characters/<id>/portrait.png 等，缺失 404）
- `GET  /media/v5/scenes/{file}`            场景 AI plate（assets/v5/scenes/<id>.png，缺失 404）
- `GET  /media/v5/voice/{id}.wav`           角色声音试听产物（tmp/v5_voice/<id>.wav）
- `GET/PUT /api/v5/voice/{character}`       读/写角色固定声音（写 contract/v5/custom/characters/<id>.json）
- `POST /api/v5/render`                     启动后台渲染 job（单并发）
- `GET  /api/v5/render`                     最近 job 列表 + 当前运行 job
- `GET  /api/v5/render/{job_id}`            单 job 状态/进度/日志尾
- `DELETE /api/v5/render/{job_id}`          取消渲染 job

说明：
- 本模块只读 outputs/ contract/（渲染子进程除外），不修改 pipeline/；
- 媒体路由必须防路径穿越（白名单 + resolve 后 is_relative_to 校验）；
- 渲染 job 用内存字典 + 后台线程，单并发；进程结束回填 status/progress/result。
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
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

from fastapi import APIRouter, Body, HTTPException, Query
from fastapi.responses import FileResponse

# 允许以 `uvicorn app.main:app --app-dir backend` 启动时 import 仓库根 pipeline.*
_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from .. import config  # noqa: E402
from pipeline import v5_catalog  # noqa: E402

router = APIRouter(tags=["v5-studio"])

ID_RE = re.compile(r"^[a-z][a-z0-9_]{1,60}$")
TEMPLATE_ID_RE = re.compile(r"^[a-z][a-z0-9_]{1,60}$")
# 允许通过 /media/v5/{template}/... 下载的文件（相对模板产物目录）
TEMPLATE_MEDIA_RE = re.compile(
    r"^(final\.mp4|timeline\.html|qc_report\.json|style_report\.json|"
    r"timeline\.json|contact_sheet\.png|storyboard\.png|storyboard\.html|"
    r"frames/frame_\d{1,3}\.png|shots/[a-z0-9_]{1,80}\.png|"
    r"work/storyboard/[a-z0-9_]{1,80}\.(jpg|png))$")
CHAR_MEDIA_RE = re.compile(r"^(portrait|portrait_real|portrait_25d|front|cutout|thumb)\.(png|jpg|jpeg|webp)$")
SCENE_MEDIA_RE = re.compile(r"^[a-z][a-z0-9_]{1,60}(_\d{1,2})?\.(png|jpg|jpeg|webp)$")
VOICE_RE = re.compile(r"^[a-z][a-z0-9_]{1,60}$")
MEDIA_TYPES = {
    ".mp4": "video/mp4",
    ".html": "text/html; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".wav": "audio/wav",
}


# ---------------------------------------------------------------------------
# 路径 / 通用
# ---------------------------------------------------------------------------
def _repo() -> Path:
    return Path(config.REPO_ROOT)


def _outputs_root() -> Path:
    """成片产物根目录（优先 config.OUTPUTS_DIR，回退仓库 outputs/）。"""
    configured = Path(config.OUTPUTS_DIR) / "templates_v5"
    fallback = _repo() / "outputs" / "templates_v5"
    if configured.is_dir():
        return configured
    return fallback


def _read_json_file(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _safe_file(base: Path, relative: str) -> Path:
    """把 relative 拼到 base 下并校验没有路径穿越；不存在 → 404。"""
    if not relative or ".." in Path(relative).parts:
        raise HTTPException(status_code=404, detail="文件不存在")
    base_resolved = base.resolve()
    candidate = (base_resolved / relative).resolve()
    try:
        candidate.relative_to(base_resolved)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="文件不存在") from exc
    if not candidate.is_file():
        raise HTTPException(status_code=404, detail="文件不存在")
    return candidate


def _media_response(path: Path) -> FileResponse:
    media_type = MEDIA_TYPES.get(path.suffix.lower(), "application/octet-stream")
    return FileResponse(str(path), media_type=media_type,
                        headers={"Cache-Control": "no-store"})


def _catalog() -> dict:
    try:
        return v5_catalog.load_catalog()
    except v5_catalog.V5Error:
        return {"styles": {}, "scenes": {}, "characters": {}, "templates": {},
                "props": {}, "packs": {}}


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# ---------------------------------------------------------------------------
# 成片画廊
# ---------------------------------------------------------------------------
def _template_media_url(template: str, relative: str) -> str:
    """模板产物的真实媒体地址。

    outputs/ 被主应用挂载在 `/media`，所以模板产物真实地址是
    `/media/templates_v5/<template>/<relative>`（旧 `/media/v5/<template>/...`
    路由仍保留兼容，但 API 返回值统一给真实路径，前端/浏览器直接可用、HEAD 也可用）。
    """
    rel = str(relative).replace(os.sep, "/").lstrip("/")
    return f"{config.MEDIA_URL_PREFIX}/templates_v5/{template}/{rel}"


def _template_file_url(template: str, path: Path | None, relative: str) -> str | None:
    """文件存在且非空时返回真实 /media 地址，否则 None。"""
    if path is None:
        return None
    try:
        if not path.is_file() or path.stat().st_size <= 0:
            return None
    except OSError:
        return None
    return _template_media_url(template, relative)


def _summarize_output(template_id: str, out_dir: Path, catalog: dict) -> dict:
    templates = catalog.get("templates") or {}
    styles = catalog.get("styles") or {}
    timeline = _read_json_file(out_dir / "timeline.json")
    qc = _read_json_file(out_dir / "qc_report.json")
    style_report = _read_json_file(out_dir / "style_report.json")
    plan = _read_json_file(out_dir / "work" / "episode.json")

    style_block = timeline.get("style") if isinstance(timeline.get("style"), dict) else {}
    style_id = str(style_block.get("id") or style_report.get("style")
                   or (templates.get(template_id) or {}).get("style") or "")
    style_label = (styles.get(style_id) or {}).get("label") \
        or style_block.get("label") or style_report.get("label") or style_id
    tpl = templates.get(template_id) or {}
    label = timeline.get("label") or tpl.get("label") or style_report.get("template") or template_id

    meta = timeline.get("meta") if isinstance(timeline.get("meta"), dict) else {}
    scenes = [str(s) for s in (meta.get("scenes") or []) if s and str(s) != "None"]
    if not scenes:
        scenes = [str(s) for s in (style_report.get("scenes") or []) if s and str(s) != "None"]
    characters = [str(c) for c in (meta.get("characters") or []) if c]
    if not characters:
        characters = [str(c) for c in (style_report.get("characters") or []) if c]

    shots = 0
    for track in timeline.get("tracks") or []:
        if isinstance(track, dict) and track.get("kind") == "video":
            shots = len(track.get("clips") or [])
            break
    if not shots:
        shots = len((plan.get("shots") or []) if isinstance(plan, dict) else [])

    resolution = timeline.get("resolution")
    if isinstance(resolution, list) and len(resolution) == 2:
        resolution_text = f"{int(resolution[0])}x{int(resolution[1])}"
    else:
        resolution_text = None
    duration = timeline.get("duration")
    if not isinstance(duration, (int, float)):
        duration = None

    final_path = out_dir / "final.mp4"
    poster_path = out_dir / "contact_sheet.png"
    poster_rel = "contact_sheet.png"
    if not poster_path.is_file():
        poster_path = out_dir / "frames" / "frame_01.png"
        poster_rel = "frames/frame_01.png"
    qc_ok = bool(qc.get("ok")) if qc else None
    checks = qc.get("checks") if isinstance(qc.get("checks"), dict) else {}
    checks_ok = sum(1 for item in checks.values()
                    if isinstance(item, dict) and item.get("ok"))
    try:
        mtime = datetime.fromtimestamp(out_dir.stat().st_mtime).strftime("%Y-%m-%d %H:%M")
    except OSError:
        mtime = ""

    return {
        "id": template_id,
        "label": label,
        "style": style_id,
        "style_label": style_label,
        "style_tags": (styles.get(style_id) or {}).get("tags") or [],
        "duration": round(float(duration), 2) if duration is not None else None,
        "resolution": resolution_text,
        "fps": timeline.get("fps"),
        "qc_ok": qc_ok,
        "qc_checks": checks_ok,
        "qc_total": len(checks),
        "shots": shots,
        "scenes": scenes,
        "characters": characters,
        "final_url": _template_file_url(template_id, final_path, "final.mp4"),
        "timeline_url": _template_file_url(
            template_id, out_dir / "timeline.html", "timeline.html"
        ),
        "qc_url": _template_file_url(
            template_id, out_dir / "qc_report.json", "qc_report.json"
        ),
        "style_report_url": _template_file_url(
            template_id, out_dir / "style_report.json", "style_report.json"
        ),
        "poster_url": _template_file_url(template_id, poster_path, poster_rel),
        "mtime": mtime,
        "dir": str(out_dir),
    }


@router.get("/api/v5/outputs", summary="V5 成片画廊（扫描 outputs/templates_v5）")
def list_outputs(include_custom: bool = Query(default=False),
                 include_missing: bool = Query(default=True)):
    catalog = _catalog()
    root = _outputs_root()
    catalog_templates = catalog.get("templates") or {}
    order = [tid for tid in sorted(catalog_templates)
             if include_custom or not str(tid).startswith("custom_")]
    outputs: list[dict] = []
    seen: set[str] = set()
    if root.is_dir():
        dirs = {p.name: p for p in root.iterdir() if p.is_dir()}
    else:
        dirs = {}
    for template_id in order:
        out_dir = dirs.get(template_id)
        if out_dir is None:
            continue
        outputs.append(_summarize_output(template_id, out_dir, catalog))
        seen.add(template_id)
    # 未在 catalog 里的产物目录（include_custom / 历史遗留）
    for template_id in sorted(dirs):
        if template_id in seen or not TEMPLATE_ID_RE.match(template_id):
            continue
        if not include_custom and template_id.startswith("custom_"):
            continue
        outputs.append(_summarize_output(template_id, dirs[template_id], catalog))
    missing = [tid for tid in order if tid not in seen] if include_missing else []
    return {
        "ok": True,
        "root": str(root),
        "count": len(outputs),
        "expected": len(order),
        "outputs": outputs,
        "missing": missing,
        "generated_at": _now(),
    }


# ---------------------------------------------------------------------------
# V5.4 剪辑 / 分镜：editorial 档案 + storyboard 产物
# ---------------------------------------------------------------------------
def _editorial_root() -> Path:
    return _repo() / "contract" / "v5" / "editorial"


def _storyboard_path(template: str) -> Path:
    return _outputs_root() / template / "storyboard.json"


def _enrich_storyboard(template: str, data: dict) -> dict:
    """给每镜补 thumbnail_url（若缩略图文件存在），并补 storyboard 图片/HTML 链接。"""
    out_dir = _outputs_root() / template
    out_resolved = out_dir.resolve()
    shots: list[dict] = []
    raw_shots = data.get("shots") if isinstance(data.get("shots"), list) else []
    for index, shot in enumerate(raw_shots):
        if not isinstance(shot, dict):
            continue
        item = dict(shot)
        shot_id = str(item.get("id") or item.get("shot_id") or f"shot{index + 1}")
        candidates: list[str] = []
        for key in ("thumbnail", "thumbnail_path", "image", "preview"):
            value = item.get(key)
            if not isinstance(value, str) or not value.strip():
                continue
            raw_value = value.strip()
            path = Path(raw_value)
            if path.is_absolute():
                # storyboard.json 里 thumbnail 可能是渲染机绝对路径：只接受落在模板产物目录内的
                try:
                    relative = path.resolve().relative_to(out_resolved)
                    candidates.append(relative.as_posix())
                except ValueError:
                    continue
            else:
                candidates.append(raw_value.replace("\\", "/"))
        candidates.append(f"shots/{shot_id}.png")
        candidates.append(f"frames/frame_{index + 1:02d}.png")
        thumbnail_url = None
        thumbnail_rel: str | None = None
        for relative in candidates:
            if not relative or ".." in Path(relative).parts:
                continue
            if not re.match(r"^[A-Za-z0-9_./-]+\.(png|jpg|jpeg|webp)$", relative):
                continue
            try:
                target = _safe_file(out_dir, relative)
            except HTTPException:
                continue
            if target.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp"):
                if TEMPLATE_MEDIA_RE.match(relative):
                    thumbnail_url = _template_media_url(template, relative)
                thumbnail_rel = relative
                break
        if thumbnail_rel:
            item["thumbnail"] = thumbnail_rel
        if thumbnail_url:
            item["thumbnail_url"] = thumbnail_url
        shots.append(item)
    data = dict(data)
    data["shots"] = shots
    png = out_dir / "storyboard.png"
    html = out_dir / "storyboard.html"
    data["storyboard_png_url"] = _template_file_url(
        template, png, "storyboard.png"
    )
    data["storyboard_html_url"] = _template_file_url(
        template, html, "storyboard.html"
    )
    # timeline.json 的 edit 轨（若渲染已接入 V5.4）
    timeline = _read_json_file(out_dir / "timeline.json")
    edit_track = None
    for track in timeline.get("tracks") or []:
        if isinstance(track, dict) and track.get("kind") in ("edit", "edl"):
            edit_track = track
            break
    data["timeline_edit"] = edit_track
    return data


@router.get("/api/v5/editorial/{template}", summary="模板剪辑档案（contract/v5/editorial）")
def get_editorial(template: str):
    if not TEMPLATE_ID_RE.match(template):
        raise HTTPException(status_code=404, detail="模板不存在")
    path = _editorial_root() / f"{template}.json"
    if not path.is_file():
        raise HTTPException(status_code=404, detail={
            "message": "该模板尚未生成剪辑档案",
            "template": template,
            "path": str(path),
        })
    editorial = _read_json_file(path)
    if not editorial:
        raise HTTPException(status_code=404, detail={
            "message": "剪辑档案为空或不是合法 JSON",
            "template": template,
        })
    return {"ok": True, "template": template, "editorial": editorial, "path": str(path)}


@router.get("/api/v5/storyboard/{template}", summary="模板分镜产物（outputs/templates_v5/<id>/storyboard.json）")
def get_storyboard(template: str):
    if not TEMPLATE_ID_RE.match(template):
        raise HTTPException(status_code=404, detail="模板不存在")
    path = _storyboard_path(template)
    if not path.is_file():
        raise HTTPException(status_code=404, detail={
            "message": "该模板尚未生成分镜数据（重渲后可见）",
            "template": template,
            "path": str(path),
        })
    data = _read_json_file(path)
    if not data:
        raise HTTPException(status_code=404, detail={
            "message": "分镜数据为空或不是合法 JSON",
            "template": template,
        })
    storyboard = _enrich_storyboard(template, data)
    return {"ok": True, "template": template, "storyboard": storyboard,
            "shots": len(storyboard.get("shots") or []),
            "storyboard_png_url": storyboard.get("storyboard_png_url"),
            "storyboard_html_url": storyboard.get("storyboard_html_url"),
            "path": str(path)}


# ---------------------------------------------------------------------------
# 静态媒体（防路径穿越）
# ---------------------------------------------------------------------------
@router.get("/media/v5/characters/{character}/{filename}", summary="角色 AI 立绘")
def get_character_art(character: str, filename: str):
    if not ID_RE.match(character) or not CHAR_MEDIA_RE.match(filename):
        raise HTTPException(status_code=404, detail="文件不存在")
    base = _repo() / "assets" / "v5" / "characters" / character
    return _media_response(_safe_file(base, filename))


@router.get("/media/v5/scenes/{filename}", summary="场景 AI plate")
def get_scene_art(filename: str):
    if not SCENE_MEDIA_RE.match(filename):
        raise HTTPException(status_code=404, detail="文件不存在")
    base = _repo() / "assets" / "v5" / "scenes"
    return _media_response(_safe_file(base, filename))


@router.get("/media/v5/voice/{filename}", summary="角色声音试听 wav")
def get_voice_wav(filename: str):
    stem = filename[:-4] if filename.lower().endswith(".wav") else ""
    if not stem or not VOICE_RE.match(stem):
        raise HTTPException(status_code=404, detail="文件不存在")
    base = _repo() / "tmp" / "v5_voice"
    return _media_response(_safe_file(base, f"{stem}.wav"))


@router.get("/media/v5/{template}/{file_path:path}", summary="V5 成片产物媒体")
def get_template_media(template: str, file_path: str):
    if not TEMPLATE_ID_RE.match(template) or not TEMPLATE_MEDIA_RE.match(file_path):
        raise HTTPException(status_code=404, detail="文件不存在")
    base = _outputs_root() / template
    return _media_response(_safe_file(base, file_path))


# ---------------------------------------------------------------------------
# 角色固定声音：读 / 改
# ---------------------------------------------------------------------------
def _voice_module():
    from pipeline import v5_voices

    return v5_voices


def _resolved_voice(catalog: dict, character: str) -> dict:
    v5_voices = _voice_module()
    char = (catalog.get("characters") or {}).get(character)
    if not isinstance(char, dict):
        raise HTTPException(status_code=404, detail=f"角色不存在: {character}")
    library = v5_voices.load_voice_library()
    spec = v5_voices.resolve_voice(char, library)
    return {"spec": spec, "char": char, "library": library}


@router.get("/api/v5/voice/{character}", summary="读取角色固定声音（resolved + 锁状态）")
def get_voice(character: str):
    if not ID_RE.match(character):
        raise HTTPException(status_code=404, detail=f"角色不存在: {character}")
    catalog = _catalog()
    resolved = _resolved_voice(catalog, character)
    v5_voices = _voice_module()
    spec = resolved["spec"]
    lock = v5_voices.lock_info(character, spec)
    lock_dir = v5_voices.lock_dir(character)
    stale = sorted(p.name for p in lock_dir.glob("*.wav") if p.name != f"{spec['key']}.wav")
    return {"ok": True, "character": character,
            "voice_id": spec["voice_id"], "seed": spec["seed"],
            "cfg_scale": spec["cfg_scale"], "instruction": spec.get("instruction"),
            "key": spec["key"], "ref_text": spec.get("ref_text") or "",
            "lock": lock, "needs_relock": bool(stale) or not lock.get("exists"),
            "stale_locks": stale}


@router.put("/api/v5/voice/{character}", summary="保存角色固定声音（写 custom 覆盖）")
def put_voice(character: str, body: Dict[str, Any] = Body(default={})):
    if not ID_RE.match(character):
        raise HTTPException(status_code=404, detail=f"角色不存在: {character}")
    catalog = _catalog()
    resolved_before = _resolved_voice(catalog, character)
    previous_key = resolved_before["spec"]["key"]
    body = body if isinstance(body, dict) else {}
    updates: dict = {}
    for key in ("voice_id", "seed", "cfg_scale", "instruction", "ref_text", "provider"):
        if key in body and body[key] is not None:
            updates[key] = body[key]
    if updates.get("voice_id"):
        v5_voices = _voice_module()
        try:
            v5_voices.preset(v5_voices.load_voice_library(), str(updates["voice_id"]))
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=400,
                                detail=f"未知 voice_id: {updates['voice_id']}（{exc}）") from exc
    if not updates:
        raise HTTPException(status_code=400, detail="没有可更新的声音字段（voice_id/seed/cfg_scale/instruction）")
    payload = {"id": character, "voice": updates}
    ok = v5_catalog.validate_item("characters", payload, catalog, as_custom=True)
    if not ok.get("ok"):
        raise HTTPException(status_code=400, detail={
            "message": "声音修改校验失败", "errors": ok.get("errors") or []})
    path = v5_catalog.custom_path("characters", character,
                                  root=catalog.get("root") or None)
    v5_catalog.write_json_atomic(path, payload)
    # 重新加载目录 + 解析（确保落盘后声音可用）
    catalog_after = _catalog()
    resolved = _resolved_voice(catalog_after, character)
    spec = resolved["spec"]
    v5_voices = _voice_module()
    lock = v5_voices.lock_info(character, spec)
    lock_dir = v5_voices.lock_dir(character)
    stale = sorted(p.name for p in lock_dir.glob("*.wav") if p.name != f"{spec['key']}.wav")
    return {
        "ok": True,
        "character": character,
        "written": str(path),
        "voice": {"voice_id": spec["voice_id"], "seed": spec["seed"],
                  "cfg_scale": spec["cfg_scale"],
                  "instruction": spec.get("instruction"), "key": spec["key"]},
        "previous_key": previous_key,
        "needs_relock": bool(stale) or not lock.get("exists"),
        "lock": lock,
        "hint": "改完声音后点「重建声音锁」再重渲，成片音色才会更新",
    }


# ---------------------------------------------------------------------------
# 后台渲染 job
# ---------------------------------------------------------------------------
_RENDER_LOCK = threading.Lock()
_RENDER_JOBS: dict[str, dict] = {}
_RENDER_ACTIVE: Optional[str] = None
_LOG_LIMIT = 240
_PROGRESS_RE = re.compile(r"^PROGRESS\s+(\d+(?:\.\d+)?)\s*(.*)$")


def _job_public(job: dict, *, log_lines: int = 60) -> dict:
    return {
        "id": job.get("id"),
        "template": job.get("template"),
        "duration": job.get("duration"),
        "force": job.get("force"),
        "status": job.get("status"),
        "progress": job.get("progress", 0),
        "message": job.get("message", ""),
        "created_at": job.get("created_at"),
        "started_at": job.get("started_at"),
        "finished_at": job.get("finished_at"),
        "elapsed": round(job.get("elapsed") or 0, 1),
        "returncode": job.get("returncode"),
        "error": job.get("error"),
        "result": job.get("result"),
        "log_tail": (job.get("log") or [])[-log_lines:],
        "out_dir": job.get("out_dir"),
    }


def _append_log(job: dict, line: str) -> None:
    line = line.rstrip("\r\n")
    if not line:
        return
    log: list[str] = job.setdefault("log", [])
    log.append(line[:600])
    if len(log) > _LOG_LIMIT:
        del log[: len(log) - _LOG_LIMIT]
    match = _PROGRESS_RE.match(line.strip())
    if match:
        try:
            job["progress"] = max(0.0, min(100.0, float(match.group(1))))
        except ValueError:
            pass
        if match.group(2):
            job["message"] = match.group(2)[:200]


def _reader(job_id: str, proc: subprocess.Popen) -> None:
    job = _RENDER_JOBS.get(job_id)
    if not job:
        return
    try:
        if proc.stdout is not None:
            for line in proc.stdout:
                _append_log(job, line)
                if line.strip().startswith("{") and "ok" in line:
                    try:
                        job["result"] = json.loads(line.strip())
                    except ValueError:
                        pass
        code = proc.wait()
    except Exception as exc:  # noqa: BLE001 - 子进程读失败也要收尾
        job["error"] = f"日志读取失败: {exc}"
        code = proc.poll() if proc.poll() is not None else -1
    job["returncode"] = code
    job["finished_at"] = _now()
    try:
        started = float(job.get("started_ts") or time.time())
        job["elapsed"] = round(time.time() - started, 1)
    except (TypeError, ValueError):
        pass
    if job.get("status") == "canceled":
        job["message"] = job.get("message") or "已取消"
    elif code == 0:
        job["status"] = "done"
        job["progress"] = 100.0
        job["message"] = job.get("message") or "渲染完成"
        final_path = Path(job.get("out_dir") or "") / "final.mp4"
        if final_path.is_file():
            job.setdefault("result", {})
            if isinstance(job["result"], dict):
                job["result"].setdefault(
                    "final_url",
                    _template_media_url(str(job.get("template") or ""), "final.mp4"),
                )
    else:
        job["status"] = "failed"
        job["error"] = job.get("error") or f"渲染进程退出码 {code}"
        job["message"] = job.get("message") or "渲染失败"
    global _RENDER_ACTIVE
    with _RENDER_LOCK:
        if _RENDER_ACTIVE == job_id:
            _RENDER_ACTIVE = None


@router.post("/api/v5/render", status_code=202, summary="启动后台渲染（单并发）")
def start_render(payload: Dict[str, Any] = Body(default={})):
    payload = payload if isinstance(payload, dict) else {}
    template = str(payload.get("template") or "").strip()
    if not TEMPLATE_ID_RE.match(template):
        raise HTTPException(status_code=400, detail="template 必须是合法模板 id")
    catalog = _catalog()
    if template not in (catalog.get("templates") or {}):
        raise HTTPException(status_code=400,
                            detail=f"模板不存在: {template}（contract/v5/templates）")
    try:
        duration = float(payload.get("duration") or 40.0)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail="duration 必须是数字") from exc
    if duration < 5 or duration > 600:
        raise HTTPException(status_code=400, detail="duration 需在 5-600 秒之间")
    force = bool(payload.get("force"))
    global _RENDER_ACTIVE
    with _RENDER_LOCK:
        if _RENDER_ACTIVE and _RENDER_JOBS.get(_RENDER_ACTIVE, {}).get("status") in (
                "queued", "running"):
            raise HTTPException(status_code=409, detail={
                "message": "已有渲染任务在运行（单并发）",
                "job_id": _RENDER_ACTIVE,
                "job": _job_public(_RENDER_JOBS[_RENDER_ACTIVE], log_lines=5),
            })
        job_id = "r" + uuid.uuid4().hex[:10]
        out_dir = _repo() / "outputs" / "templates_v5" / template
        job: dict = {
            "id": job_id, "template": template, "duration": duration, "force": force,
            "status": "queued", "progress": 0.0, "message": "排队中",
            "created_at": _now(), "started_at": None, "finished_at": None,
            "elapsed": 0.0, "returncode": None, "error": None, "result": None,
            "log": [], "out_dir": str(out_dir),
        }
        _RENDER_JOBS[job_id] = job
        _RENDER_ACTIVE = job_id
        cmd = [sys.executable, "-m", "pipeline.cli", "render_v5",
               "--template", template, "--duration", f"{duration:g}"]
        if force:
            cmd.append("--force")
        env = dict(os.environ)
        env["PYTHONIOENCODING"] = "utf-8"
        env.setdefault("PYTHONUNBUFFERED", "1")
        creationflags = 0x08000000 if os.name == "nt" else 0  # CREATE_NO_WINDOW
        try:
            proc = subprocess.Popen(  # noqa: S603 - 固定 argv，无 shell
                cmd, cwd=str(_repo()), env=env, stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT, text=True, encoding="utf-8",
                errors="replace", bufsize=1, creationflags=creationflags,
            )
        except OSError as exc:
            job["status"] = "failed"
            job["error"] = f"启动渲染进程失败: {exc}"
            _RENDER_ACTIVE = None
            raise HTTPException(status_code=500, detail=job["error"]) from exc
        job["status"] = "running"
        job["started_at"] = _now()
        job["started_ts"] = time.time()
        job["message"] = "渲染中"
        job["_proc"] = proc
        thread = threading.Thread(target=_reader, args=(job_id, proc),
                                  name=f"v5-render-{job_id}", daemon=True)
        thread.start()
    return {"ok": True, "job": _job_public(_RENDER_JOBS[job_id], log_lines=10),
            "cmd": cmd}


@router.get("/api/v5/render", summary="渲染 job 列表（含当前运行）")
def list_render_jobs(limit: int = Query(default=20, ge=1, le=100)):
    with _RENDER_LOCK:
        jobs = sorted(_RENDER_JOBS.values(), key=lambda item: item.get("created_at") or "",
                      reverse=True)[:limit]
        active = _RENDER_JOBS.get(_RENDER_ACTIVE) if _RENDER_ACTIVE else None
        return {"ok": True,
                "running": _job_public(active, log_lines=20) if active else None,
                "jobs": [_job_public(job, log_lines=10) for job in jobs],
                "count": len(_RENDER_JOBS)}


@router.get("/api/v5/render/{job_id}", summary="渲染 job 状态/进度/日志尾")
def get_render_job(job_id: str):
    job = _RENDER_JOBS.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail=f"渲染任务不存在: {job_id}")
    return {"ok": True, "job": _job_public(job, log_lines=80)}


@router.delete("/api/v5/render/{job_id}", summary="取消渲染 job")
def cancel_render_job(job_id: str):
    job = _RENDER_JOBS.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail=f"渲染任务不存在: {job_id}")
    proc = job.get("_proc")
    if job.get("status") in ("running", "queued") and proc is not None:
        try:
            proc.terminate()
        except OSError:
            pass
        job["status"] = "canceled"
        job["message"] = "已取消"
        global _RENDER_ACTIVE
        with _RENDER_LOCK:
            if _RENDER_ACTIVE == job_id:
                _RENDER_ACTIVE = None
    return {"ok": True, "job": _job_public(job, log_lines=20)}
