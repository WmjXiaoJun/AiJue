# -*- coding: utf-8 -*-
"""V21「可视化创建」后端 API：新建 / 删除 角色与模板。

端点（前缀 /api/v5）：
  POST   /characters                新建角色（写 contract/v5/custom/characters/<id>.json）
  DELETE /characters/{character}    删除 custom 角色（被模板 cast 引用 → 400）
  POST   /templates                 新建模板（写 custom 模板 + 生成 TTS/episode + semantic brief）
  DELETE /templates/{template}      删除 custom 模板（有 final → 400）

TTS 复用现有 pipeline 模块：`pipeline.tts_breeze`（服务在跑才用）/ `pipeline.tts_engine`（edge 兜底），
统一输出 24k mono wav；speech 时长用 `pipeline.utils.probe_duration`。
Semantic brief 复用 `pipeline.templates3d_semantic`（LLM）；失败时写通用兜底 brief，不阻塞创建。
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from fastapi import APIRouter, Body, HTTPException

_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from pipeline import v5_catalog  # noqa: E402

router = APIRouter(prefix="/api/v5", tags=["v5-create"])

_CONTRACT = _REPO_ROOT / "contract" / "v5"
_CUSTOM_DIR = _CONTRACT / "custom"
_BUILTIN_TEMPLATES = _CONTRACT / "templates"
_SEMANTIC_DIR = _CONTRACT / "cinema3d" / "templates3d"
_V5_OUT = _REPO_ROOT / "outputs" / "templates_v5"
_OUT3D = _REPO_ROOT / "outputs" / "templates_3d"
_FFMPEG = _REPO_ROOT / "tools" / "node_modules" / "ffmpeg-static" / "ffmpeg.exe"

_ID_RE = re.compile(r"^[a-z][a-z0-9_]{1,60}$")
_ROLE_RE = re.compile(r"^[a-z][a-z0-9_]{0,30}$")
_HEX_RE = re.compile(r"^#[0-9a-fA-F]{6}$")

CUSTOM_PREFIX = getattr(v5_catalog, "CUSTOM_PREFIX", "custom_")
EDGE_DEFAULT_VOICE = "zh-CN-YunxiNeural"
SILENCE_RATE = 24000
DEFAULT_BROLL_DURATION = 3.0
DEFAULT_MOUTH = ["closed", "a", "o", "e", "i", "u", "m"]
DEFAULT_POSES = ["front", "point", "think"]
DEFAULT_APPEARANCE = {
    "skin": "#F2D6C3",
    "brow": "thick",
    "beard": "none",
    "face_ratio": 1.0,
    "head_ratio": 1.1,
    "chibi": 0.2,
    "eye_style": "sharp",
    "accessories": [],
}


def _catalog() -> dict:
    try:
        return v5_catalog.load_catalog()
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"目录读取失败: {exc}") from exc


def _err(message: str, errors: Optional[List[str]] = None) -> HTTPException:
    return HTTPException(status_code=400, detail={"message": message,
                                                  "errors": errors or [message]})


def _now_text() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def _write_json(path: Path, data: Any) -> Path:
    return v5_catalog.write_json_atomic(path, data)


def _custom_path(kind: str, item_id: str) -> Path:
    return v5_catalog.custom_path(kind, item_id)


def _normalize_custom_id(raw: Any, *, default_prefix: str) -> str:
    text = str(raw or "").strip().lower()
    if not text:
        return default_prefix + uuid.uuid4().hex[:8]
    if not text.startswith(CUSTOM_PREFIX):
        text = CUSTOM_PREFIX + text
    return text


def _to_float(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _as_bool(value: Any, default: bool = False) -> bool:
    """兼容 JSON bool / 0-1 / "true"/"false" 字符串。"""
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    text = str(value or "").strip().lower()
    if text in ("1", "true", "yes", "on"):
        return True
    if text in ("0", "false", "no", "off", ""):
        return False
    return default


def _probe_duration(path: Path) -> float:
    from pipeline import utils  # noqa: PLC0415

    return round(float(utils.probe_duration(path) or 0.0), 3)


# --------------------------------------------------------------------------- #
# 声音 / TTS（复用 pipeline 模块，输出 24k mono）
# --------------------------------------------------------------------------- #
def _resolve_voice_spec(character: dict) -> dict:
    from pipeline import v5_voices  # noqa: PLC0415

    raw = character.get("voice") if isinstance(character.get("voice"), dict) else {}
    provider = str(raw.get("provider") or "").strip().lower()
    voice_id = str(raw.get("voice_id") or "").strip()
    if provider == "edge":
        return {
            "character": character.get("id"), "voice_id": voice_id or EDGE_DEFAULT_VOICE,
            "provider": "edge", "label": str(raw.get("label") or character.get("label") or ""),
            "instruction": str(raw.get("instruction") or ""), "seed": raw.get("seed"),
            "cfg_scale": raw.get("cfg_scale"), "rate": str(raw.get("rate") or "+0%"),
            "ref_audio": None, "ref_text": None, "key": "edge",
        }
    try:
        return v5_voices.resolve_voice(character, v5_voices.load_voice_library())
    except Exception:  # noqa: BLE001 - 兼容未标 edge 但使用 zh-CN 音色的角色
        if voice_id.lower().startswith("zh-cn-"):
            return {
                "character": character.get("id"), "voice_id": voice_id,
                "provider": "edge", "label": str(character.get("label") or ""),
                "instruction": str(raw.get("instruction") or ""), "seed": raw.get("seed"),
                "cfg_scale": raw.get("cfg_scale"), "rate": str(raw.get("rate") or "+0%"),
                "ref_audio": None, "ref_text": None, "key": "edge",
            }
        raise


def _edge_voice_for(spec: dict) -> str:
    voice_id = str(spec.get("voice_id") or "")
    if voice_id.lower().startswith("zh-cn-") and voice_id.lower().endswith("neural"):
        return voice_id
    text = f"{spec.get('instruction') or ''} {spec.get('label') or ''}"
    female = any(key in text for key in ("女", "她", "妃", "后", "娘", "妹", "姐", "甜", "公主"))
    low = any(key in text for key in ("低沉", "浑厚", "厚重", "沙哑", "威压", "低音", "冷峻", "压迫", "沉"))
    if female:
        return "zh-CN-XiaoxiaoNeural"
    if low:
        return "zh-CN-YunjianNeural"
    return EDGE_DEFAULT_VOICE


def _to_24k_mono(src: Path, dst: Path) -> None:
    from pipeline import utils  # noqa: PLC0415

    utils.ffmpeg(["-y", "-hide_banner", "-loglevel", "error", "-i", str(src),
                  "-ar", str(SILENCE_RATE), "-ac", "1", "-c:a", "pcm_s16le", str(dst)])


def _synth_edge(text: str, spec: dict, out_wav: Path) -> None:
    from pipeline import tts_engine  # noqa: PLC0415

    mp3 = out_wav.with_name(f"{out_wav.stem}.edge.mp3")
    try:
        # 用 pipeline 自带重试（config.TTS_RETRIES），降低 edge 网络抖动导致创建失败/静音
        tts_engine.synth_with_retry(text, _edge_voice_for(spec), mp3,
                                    rate=str(spec.get("rate") or "+0%"))
        _to_24k_mono(mp3, out_wav)
    finally:
        try:
            mp3.unlink(missing_ok=True)
        except OSError:
            pass


def _synth_breeze(text: str, spec: dict, out_wav: Path) -> None:
    from pipeline import tts_breeze, utils  # noqa: PLC0415

    if not tts_breeze.server_alive():
        raise RuntimeError("Breeze TTS 服务未运行")
    results = tts_breeze.synthesize_batch(
        [text], str(spec.get("character") or "character"), out_dir=out_wav.parent,
        voice_spec={"instruction": spec.get("instruction"), "seed": spec.get("seed"),
                    "cfg_scale": spec.get("cfg_scale")},
        ref_audio=spec.get("ref_audio"), ref_text=spec.get("ref_text") or "",
    )
    if not results:
        raise RuntimeError("Breeze 合成结果为空")
    utils.ffmpeg(["-y", "-hide_banner", "-loglevel", "error", "-i", str(results[0]["path"]),
                  "-ar", str(SILENCE_RATE), "-ac", "1", "-c:a", "pcm_s16le", str(out_wav)])


def _synth_silence(duration: float, out_wav: Path) -> None:
    from pipeline import utils  # noqa: PLC0415

    utils.ffmpeg(["-y", "-hide_banner", "-loglevel", "error", "-f", "lavfi",
                  "-i", f"anullsrc=r={SILENCE_RATE}:cl=mono", "-t", f"{duration:.3f}",
                  "-c:a", "pcm_s16le", str(out_wav)])


def _synthesize_line(character: dict, text: str, out_wav: Path,
                     warnings: List[str]) -> Dict[str, Any]:
    """对白合成：breeze 服务在跑则 breeze，否则 edge；都失败退回静音（warning）。"""
    from pipeline import tts_breeze  # noqa: PLC0415

    spec = _resolve_voice_spec(character)
    prefer_breeze = str(spec.get("provider") or "").lower() != "edge"
    breeze_ready = False
    if prefer_breeze:
        try:
            breeze_ready = bool(tts_breeze.server_alive())
        except Exception:  # noqa: BLE001
            breeze_ready = False
    engines: List[str] = []
    if breeze_ready:
        engines.append("breeze")
    engines.append("edge")
    for engine in engines:
        try:
            if engine == "breeze":
                _synth_breeze(text, spec, out_wav)
            else:
                _synth_edge(text, spec, out_wav)
            return {"engine": engine, "voice_id": spec.get("voice_id"),
                    "provider": engine, "key": spec.get("key"), "spec": spec}
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"角色 {character.get('id')} {engine} TTS 失败：{exc}")
    warnings.append(f"角色 {character.get('id')} 所有 TTS 失败，使用静音占位")
    _synth_silence(2.0, out_wav)
    return {"engine": "silent", "voice_id": spec.get("voice_id"), "provider": "silent",
            "key": spec.get("key"), "spec": spec}


# --------------------------------------------------------------------------- #
# 角色
# --------------------------------------------------------------------------- #
def _character_defaults(body: dict) -> dict:
    appearance = body.get("appearance") if isinstance(body.get("appearance"), dict) else {}
    outfit = appearance.get("outfit") if isinstance(appearance.get("outfit"), dict) else {}
    hair = str(appearance.get("hair") or "#2B2B33")
    return {
        "gender": str(appearance.get("gender") or "male"),
        "age": int(_to_float(appearance.get("age"), 30)),
        "skin": str(appearance.get("skin") or DEFAULT_APPEARANCE["skin"]),
        "hair": hair,
        "hair_style": str(appearance.get("hair_style") or "short_swept"),
        "eye": str(appearance.get("eye") or hair),
        "eye_style": str(appearance.get("eye_style") or DEFAULT_APPEARANCE["eye_style"]),
        "brow": str(appearance.get("brow") or DEFAULT_APPEARANCE["brow"]),
        "beard": str(appearance.get("beard") or DEFAULT_APPEARANCE["beard"]),
        "face_ratio": _to_float(appearance.get("face_ratio"), DEFAULT_APPEARANCE["face_ratio"]),
        "head_ratio": _to_float(appearance.get("head_ratio"), DEFAULT_APPEARANCE["head_ratio"]),
        "chibi": _to_float(appearance.get("chibi"), DEFAULT_APPEARANCE["chibi"]),
        "outfit": {
            "type": str(outfit.get("type") or "jacket"),
            "main": str(outfit.get("main") or "#2F3A4F"),
            "accent": str(outfit.get("accent") or "#F5C518"),
        },
        "accessories": [str(item) for item in (appearance.get("accessories") or []) if str(item).strip()],
    }


@router.post("/characters", summary="新建角色（写 contract/v5/custom/characters）")
def create_character(body: Dict[str, Any] = Body(default={})) -> dict:
    data = body if isinstance(body, dict) else {}
    label = str(data.get("label") or "").strip()
    if not label:
        raise _err("缺少 label（角色名）")
    catalog = _catalog()
    cid = _normalize_custom_id(data.get("id"), default_prefix="custom_")
    if not _ID_RE.fullmatch(cid):
        raise _err(f"id 不合法：{cid}（需 ^[a-z][a-z0-9_]{{1,60}}$）")
    if cid in (catalog.get("characters") or {}):
        raise _err(f"角色已存在：{cid}")
    if _custom_path("characters", cid).is_file():
        raise _err(f"角色已存在：{cid}")

    voice_id = str(data.get("voice_id") or "").strip()
    from pipeline import v5_voices  # noqa: PLC0415

    library = v5_voices.load_voice_library()
    if voice_id:
        try:
            preset = v5_voices.preset(library, voice_id)
        except Exception as exc:  # noqa: BLE001
            raise _err(f"未知 voice_id: {voice_id}（{exc}）") from exc
        voice: Dict[str, Any] = {"provider": str(preset.get("provider") or "breeze"),
                                 "voice_id": voice_id}
        if preset.get("instruction"):
            voice["instruction"] = preset["instruction"]
    else:
        # 默认 edge 音色：本环境 breeze 可能未启动，保证创建后仍可试听/生成
        voice = {"provider": "edge", "voice_id": EDGE_DEFAULT_VOICE,
                 "instruction": "中性、清晰的中文叙述音色", "rate": "+0%"}

    appearance = _character_defaults(data)
    errors: List[str] = []
    for key in ("gender", "hair_style"):
        if not str(appearance.get(key) or "").strip():
            errors.append(f"appearance.{key} 不能为空")
    for key in ("skin", "hair", "eye"):
        if not _HEX_RE.fullmatch(str(appearance.get(key) or "")):
            errors.append(f"appearance.{key} 需为 #RRGGBB：{appearance.get(key)!r}")
    for key in ("main", "accent"):
        if not _HEX_RE.fullmatch(str(appearance["outfit"].get(key) or "")):
            errors.append(f"appearance.outfit.{key} 需为 #RRGGBB：{appearance['outfit'].get(key)!r}")
    if not (1 <= int(appearance.get("age") or 0) <= 150):
        errors.append("appearance.age 需在 1-150")
    role_pool = data.get("role_pool")
    if not isinstance(role_pool, list) or not role_pool:
        role_pool = ["host", "guest"]
    role_pool = [str(item).strip() for item in role_pool if str(item).strip()]
    if not role_pool or not all(_ROLE_RE.fullmatch(item) for item in role_pool):
        errors.append("role_pool 必须是合法 role 列表（^[a-z][a-z0-9_]{0,30}$）")
    if errors:
        raise _err("角色字段校验失败", errors)

    item = {
        "id": cid,
        "label": label,
        "era": str(data.get("era") or "modern").strip() or "modern",
        "persona": str(data.get("persona") or "").strip(),
        "tags": [str(tag) for tag in (data.get("tags") or []) if str(tag).strip()],
        "role_pool": role_pool,
        "voice": voice,
        "appearance": appearance,
        "poses": DEFAULT_POSES,
        "mouth": DEFAULT_MOUTH,
        "style_overrides": {},
    }
    result = v5_catalog.validate_item("characters", item, catalog, as_custom=True)
    if not result.get("ok"):
        raise _err("角色校验失败", result.get("errors") or [])
    path = _custom_path("characters", cid)
    _write_json(path, item)

    merged = (_catalog().get("characters") or {}).get(cid) or item
    return {
        "ok": True,
        "id": cid,
        "item": merged,
        "preview_url": f"/api/v5/preview/characters/{cid}",
        "path": str(path),
        "warnings": result.get("warnings") or [],
    }


@router.delete("/characters/{character}", summary="删除 custom 角色（被模板引用则 400）")
def delete_character(character: str) -> dict:
    cid = str(character or "").strip().lower()
    if not _ID_RE.fullmatch(cid):
        raise HTTPException(status_code=404, detail=f"角色不存在: {character}")
    custom = _custom_path("characters", cid)
    builtin = _CONTRACT / "characters" / f"{cid}.json"
    if builtin.is_file() and not custom.is_file():
        raise _err(f"只能删除 custom 角色：{cid} 是内建角色（contract/v5/characters）")
    if not custom.is_file():
        raise HTTPException(status_code=404, detail=f"custom 角色不存在: {cid}")
    if builtin.is_file():
        raise _err(f"只能删除 custom-only 角色：{cid} 有内建定义（contract/v5/characters）")
    catalog = _catalog()
    refs = []
    for tid, tpl in sorted((catalog.get("templates") or {}).items()):
        cast = tpl.get("cast") if isinstance(tpl, dict) else None
        if isinstance(cast, list) and any(
                isinstance(entry, dict) and str(entry.get("character") or "") == cid
                for entry in cast):
            refs.append(tid)
    if refs:
        raise _err(f"角色 {cid} 被模板引用，不能删除", [f"引用模板：{', '.join(refs)}",
                                                       *refs])
    custom.unlink()
    return {"ok": True, "id": cid, "deleted": True, "path": str(custom)}


# --------------------------------------------------------------------------- #
# 模板：TTS + episode + semantic
# --------------------------------------------------------------------------- #
def _derive_style3d(style_entry: dict, scene_ids: List[str], catalog: dict) -> dict:
    h3 = style_entry.get("h3") if isinstance(style_entry.get("h3"), dict) else {}
    look = str(h3.get("style_prompt") or style_entry.get("description")
               or "a cinematic 3D animated scene")
    scene_map: Dict[str, str] = {}
    for sid in scene_ids:
        scene_map[sid] = f"a cinematic set inspired by {str(sid).replace('_', ' ')}"
    ambience_raw = str((style_entry.get("audio") or {}).get("ambience") or "quiet room tone")
    tags = [str(tag).lower() for tag in (style_entry.get("tags") or [])]
    realistic = ("photoreal" in look.lower()) or ("film" in str(style_entry.get("id") or "")) \
        or any("real" in tag for tag in tags)
    return {
        "look": look,
        "scenes": scene_map,
        "ambience": f"soft {ambience_raw.replace('_', ' ')} ambience, quiet room tone",
        "realistic": bool(realistic),
    }


def _fallback_semantic_brief(tid: str, catalog: dict, episode: dict, template: dict,
                             style_entry: dict) -> dict:
    """LLM 语义增强失败时的通用 brief（仍保证模板可进 3D 生成流程）。"""
    from pipeline import templates3d as base  # noqa: PLC0415

    style3d = _derive_style3d(style_entry, [s.get("scene") for s in episode.get("shots") or []
                                            if s.get("scene")], catalog)
    look = style3d["look"]
    shots = []
    for shot in episode.get("shots") or []:
        sid = shot.get("id")
        scene = str(shot.get("scene") or "")
        scene_desc = style3d["scenes"].get(scene, f"a cinematic set inspired by {scene.replace('_', ' ')}")
        chars = []
        for role in shot.get("characters") or []:
            cid = ((episode.get("meta") or {}).get("cast") or {}).get(role, {}).get("character")
            if cid:
                desc = base.character_desc(catalog, cid,
                                           realistic=bool(style3d.get("realistic")))
                if desc not in chars:
                    chars.append(desc)
        char_part = (" Featuring " + "; ".join(chars) + ".") if chars else ""
        if shot.get("line"):
            action = "The character speaks naturally to the camera, gesturing with subtle body language."
        else:
            action = "The camera surveys the space, atmospheric and cinematic."
        prompt = (f"integrated_multimodal_description: [Shot 1] {look}. Set in {scene_desc}."
                  f"{char_part} {action} Camera: slow cinematic camera move. "
                  f"3D animated feature film quality, volumetric light, shallow depth of field. "
                  f"no text, no subtitles, no watermark, no logo, no distorted faces.\n\n"
                  f"overall_soundscape: {style3d['ambience']}. No dialogue, no human voices, no music.\n\n"
                  f"non_diegetic_music: N/A")
        shots.append({
            "id": sid,
            "kind": "dialogue" if shot.get("line") else "broll",
            "scene": scene,
            "duration": shot.get("duration"),
            "line": shot.get("line") or "",
            "semantic_visual": "",
            "framing": "",
            "prompt": prompt,
        })
    out = {"template": tid, "style": template.get("style"), "look": look,
           "mode": "semantic_fallback", "shots": shots}
    _SEMANTIC_DIR.mkdir(parents=True, exist_ok=True)
    _write_json(_SEMANTIC_DIR / f"{tid}_semantic.json", out)
    return out


def _generate_semantic_brief(tid: str, catalog: dict, episode: dict, template: dict,
                             style_entry: dict, warnings: List[str]) -> bool:
    try:
        from pipeline import templates3d as base  # noqa: PLC0415
        from pipeline import templates3d_semantic as sem  # noqa: PLC0415

        base.STYLE3D[tid] = _derive_style3d(
            style_entry, [s.get("scene") for s in episode.get("shots") or [] if s.get("scene")], catalog)
        beats: Dict[str, Any] = {"shots": []}
        try:
            beats = sem.semantic_beats(tid, episode, template)
        except Exception as exc:  # noqa: BLE001 - LLM 不可用时用通用画面描述继续
            warnings.append(f"台词语义翻译失败（{exc}），已使用通用画面描述")
        sem.build_semantic_briefs(tid, catalog, episode, template, beats)
        return True
    except Exception as exc:  # noqa: BLE001
        warnings.append(f"semantic brief 生成失败（{exc}），已写入通用兜底 brief")
        try:
            _fallback_semantic_brief(tid, catalog, episode, template, style_entry)
            return True
        except Exception as fallback_exc:  # noqa: BLE001
            warnings.append(f"通用兜底 brief 也失败：{fallback_exc}")
            return False


def _normalize_aspect(raw: Any) -> List[int]:
    if isinstance(raw, str) and ":" in raw:
        try:
            left, right = raw.split(":", 1)
            return [int(left), int(right)]
        except ValueError:
            raise _err(f"aspect 不合法：{raw}（示例 16:9 或 [16,9]）")
    if isinstance(raw, (list, tuple)) and len(raw) == 2:
        try:
            return [int(raw[0]), int(raw[1])]
        except (TypeError, ValueError):
            raise _err(f"aspect 不合法：{raw}")
    return [16, 9]


def _maybe_autostart(tid: str, *, auto_generate: bool, dry_run: bool,
                     warnings: List[str]) -> Optional[Dict[str, Any]]:
    """V23：创建成功后按 auto_generate 提交 pipeline job。

    - auto_generate=false 或 dry_run=true → 返回 None（pipeline: null，向后兼容）；
    - 单并发 busy（409）→ started=false / reason="busy" / running_job_id，不报错；
    - 其它启动异常 → started=false / reason="error" / error + warning，不影响创建成功。
    """
    if not auto_generate or dry_run:
        return None
    try:
        from .pipeline_ui import start_pipeline_job  # noqa: PLC0415 - 延迟 import 避免循环依赖

        started = start_pipeline_job(tid, reuse_shots=False, dry_run=False)
        return {
            "started": True,
            "job_id": started.get("job_id"),
            "status": started.get("status") or "queued",
        }
    except HTTPException as exc:
        detail = exc.detail if isinstance(exc.detail, dict) else {"message": str(exc.detail)}
        if int(exc.status_code or 0) == 409:
            return {"started": False, "reason": "busy",
                    "running_job_id": detail.get("job_id")}
        message = str(detail.get("message") or detail)
        warnings.append(f"auto_generate 启动失败：{message}")
        return {"started": False, "reason": "error", "error": message}
    except Exception as exc:  # noqa: BLE001
        message = f"{type(exc).__name__}: {exc}"
        warnings.append(f"auto_generate 启动异常：{message}")
        return {"started": False, "reason": "error", "error": message}


@router.post("/templates", summary="新建模板（TTS + episode + semantic brief）")
def create_template(body: Dict[str, Any] = Body(default={})) -> dict:
    data = body if isinstance(body, dict) else {}
    catalog = _catalog()
    warnings: List[str] = []
    errors: List[str] = []
    # V23：创建后立即生成 3D；dry_run=true 时不启动（pipeline=null，向后兼容）
    auto_generate = _as_bool(data.get("auto_generate"))
    dry_run = _as_bool(data.get("dry_run"))

    label = str(data.get("label") or "").strip()
    if not label:
        errors.append("缺少 label（模板中文名）")
    style_id = str(data.get("style") or "").strip()
    styles = catalog.get("styles") or {}
    if not style_id or style_id not in styles:
        errors.append(f"style 不存在：{style_id or '（空）'}")
    scene_table = catalog.get("scenes") or {}
    characters = catalog.get("characters") or {}
    tid = _normalize_custom_id(data.get("id"), default_prefix="custom_tpl_")
    if not _ID_RE.fullmatch(tid):
        errors.append(f"id 不合法：{tid}（需 ^[a-z][a-z0-9_]{{1,60}}$）")
    if tid in (catalog.get("templates") or {}):
        errors.append(f"模板 id 已存在：{tid}")
    aspect = _normalize_aspect(data.get("aspect"))

    cast_in = data.get("cast")
    if not isinstance(cast_in, list) or not cast_in:
        errors.append("cast 至少 1 个角色")
        cast_in = []
    cast: List[Dict[str, str]] = []
    roles: List[str] = []
    for index, entry in enumerate(cast_in):
        if not isinstance(entry, dict):
            errors.append(f"cast[{index}] 必须是 object")
            continue
        role = str(entry.get("role") or "").strip()
        cid = str(entry.get("character") or "").strip()
        if not _ROLE_RE.fullmatch(role):
            errors.append(f"cast[{index}].role 不合法：{role!r}")
            continue
        if role in roles:
            errors.append(f"cast role 重复：{role}")
            continue
        if cid not in characters:
            errors.append(f"cast[{index}] 角色不存在：{cid}")
            continue
        name = str(entry.get("name") or characters[cid].get("label") or cid).strip()
        cast.append({"role": role, "character": cid, "name": name})
        roles.append(role)
    if len(cast) > 6:
        errors.append("cast 最多 6 个角色")

    acts_in = data.get("acts")
    if not isinstance(acts_in, list) or not acts_in:
        errors.append("acts 至少 1 幕")
        acts_in = []
    if isinstance(acts_in, list) and not (4 <= len(acts_in) <= 10):
        errors.append(f"acts 需 4-10 幕（当前 {len(acts_in)}）")
    acts: List[Dict[str, Any]] = []
    for index, entry in enumerate(acts_in):
        if not isinstance(entry, dict):
            errors.append(f"acts[{index}] 必须是 object")
            continue
        scene = str(entry.get("scene") or "").strip()
        if scene not in scene_table:
            errors.append(f"acts[{index}] 场景不存在：{scene}")
        text = str(entry.get("text") or "").strip()
        speaker = str(entry.get("speaker") or entry.get("role") or "").strip()
        if text and speaker not in roles:
            errors.append(f"acts[{index}] 有台词但 speaker 不在 cast：{speaker or '（空）'}")
        camera = str(entry.get("camera") or ("static" if not text else "dolly")).strip() or "static"
        duration = _to_float(entry.get("duration"), DEFAULT_BROLL_DURATION if not text else 0.0)
        acts.append({
            "index": index + 1,
            "scene": scene,
            "camera": camera,
            "speaker": speaker if text else "",
            "text": text,
            "requested_duration": duration,
        })
    if errors:
        raise _err("模板字段校验失败", errors)

    style_entry = styles[style_id]
    workdir = _V5_OUT / tid / "work"
    audio_dir = workdir / "audio"
    try:
        if workdir.exists():
            raise _err(f"模板工作目录已存在：{workdir}（请换 id 或先删除旧产物）")
        audio_dir.mkdir(parents=True, exist_ok=True)
        cast_map = {entry["role"]: entry for entry in cast}
        shots: List[Dict[str, Any]] = []
        audio_files: List[str] = []
        used_characters: Dict[str, Dict[str, Any]] = {}
        synth_engines: List[str] = []
        cursor = 0.0
        for act in acts:
            idx = act["index"]
            sid = f"v5_{idx:03d}"
            speaker = act["speaker"]
            text = act["text"]
            out_wav = audio_dir / f"{sid}.wav"
            if text:
                role_entry = cast_map[speaker]
                cid = role_entry["character"]
                character = characters[cid]
                synth = _synthesize_line(character, text, out_wav, warnings)
                synth_engines.append(str(synth.get("engine") or "edge"))
                speech = _probe_duration(out_wav)
                duration = round(max(1.4, speech + 0.6), 2)
                voice_meta = {
                    "provider": synth["provider"], "character": cid,
                    "voice_id": synth.get("voice_id"), "key": synth.get("key"),
                    "seed": (synth.get("spec") or {}).get("seed"),
                    "text": text, "speech_duration": speech,
                }
                used_characters.setdefault(cid, synth.get("spec") or {})
            else:
                duration = round(max(1.5, min(act["requested_duration"] or DEFAULT_BROLL_DURATION, 8.0)), 2)
                speech = 0.0
                _synth_silence(duration, out_wav)
                voice_meta = {}
            audio_files.append(str(out_wav))
            characters_roles = [speaker] if text else []
            shots.append({
                "id": sid, "no": idx, "type": "dialogue" if text else "broll",
                "act_id": f"act{idx:02d}", "act_name": f"第 {idx} 幕",
                "role": speaker or None,
                "character": cast_map[speaker]["character"] if text else None,
                "speaker_name": cast_map[speaker]["name"] if text else "",
                "scene": act["scene"],
                "camera": {"type": act["camera"], "strength": 0.06, "ease": "in_out",
                           "zone": [0.0, 0.0, 1.0, 1.0]},
                "characters": characters_roles,
                "duration": duration, "line": text, "speech_duration": speech,
                "voice": voice_meta, "audio": str(out_wav),
                "edit": {"shot": sid, "index": idx - 1, "scene": act["scene"],
                         "has_line": bool(text), "shot_size": "MS",
                         "camera": act["camera"], "zone": "wide",
                         "zone_rect": [0.0, 0.0, 1.0, 1.0],
                         "j_cut": 0.0, "l_cut": 0.0, "insert": False,
                         "transition_out": {"mode": "cut", "duration": 0.0}},
                "_edit_start": round(cursor, 3),
            })
            cursor += duration
        total_duration = round(sum(float(shot["duration"]) for shot in shots), 2)

        # 回写 acts 时长（模板合同与 episode 保持一致）
        template_acts = []
        for act, shot in zip(acts, shots):
            template_acts.append({
                "id": f"act{act['index']:02d}", "name": f"第 {act['index']} 幕",
                "scene": act["scene"], "camera": act["camera"],
                "camera_strength": 0.06, "duration": shot["duration"],
                "line": act["speaker"] if act["text"] else "",
                "text": act["text"],
                "characters": shot["characters"],
                "broll": not bool(act["text"]),
            })

        template_item = {
            "id": tid, "label": label,
            "description": str(data.get("description") or f"{label}（V21 可视化创建）").strip(),
            "tags": ["custom", "v21"],
            "style": style_id, "target_duration": total_duration, "aspect": aspect,
            "cast": cast, "acts": template_acts,
            "packaging": {"subtitle": "default", "transition": "fade", "title": label},
            "lipsync": {"mode": "musetalk"},
            "audio": {},
            "meta": {"create_source": "v21", "three_d": True,
                     "created_at": _now_text(), "created_by": "api"},
        }
        result = v5_catalog.validate_item("templates", template_item, catalog, as_custom=True)
        if not result.get("ok"):
            shutil.rmtree(workdir.parent, ignore_errors=True)
            raise _err("模板校验失败", result.get("errors") or [])
        template_path = _custom_path("templates", tid)
        _write_json(template_path, template_item)

        voices_meta = []
        for cid, spec in used_characters.items():
            voices_meta.append({"character": cid, "voice_id": spec.get("voice_id"),
                                "key": spec.get("key"), "seed": spec.get("seed"),
                                "provider": spec.get("provider")})
        episode = {
            "episode": {
                "id": tid, "label": label, "style": style_id,
                "resolution": "1280x720", "fps": 25,
                "target_duration": total_duration, "total_duration": total_duration,
                "acts": template_acts,
            },
            "shots": shots,
            "meta": {
                "template": tid, "style": style_id, "target_duration": total_duration,
                "seed": 20261121, "cast": cast_map,
                "script_source": "template", "h3_shot_count": 0,
                "speech_driven": True,
                "voice_provider": ("breeze" if synth_engines and all(e == "breeze" for e in synth_engines)
                                   else "edge"),
                "voices": voices_meta,
                "create_source": "v21",
            },
        }
        _write_json(workdir / "episode.json", episode)

        brief_ok = _generate_semantic_brief(tid, catalog, episode, template_item,
                                            style_entry, warnings)
        response = {
            "ok": True, "id": tid, "label": label, "style": style_id,
            "shots": len(shots),
            "dialogue_shots": sum(1 for shot in shots if shot["line"]),
            "broll_shots": sum(1 for shot in shots if not shot["line"]),
            "total_duration": total_duration,
            "audio": len(audio_files),
            "brief": bool(brief_ok),
            "target_duration": total_duration,
            "warnings": warnings,
            "template_path": str(template_path),
            "episode_path": str(workdir / "episode.json"),
            "semantic_path": str(_SEMANTIC_DIR / f"{tid}_semantic.json") if brief_ok else None,
        }
        # V23：所有产物就绪后再尝试启动 pipeline job；busy/异常不影响创建成功
        response["pipeline"] = _maybe_autostart(tid, auto_generate=auto_generate,
                                               dry_run=dry_run, warnings=warnings)
        return response
    except HTTPException:
        shutil.rmtree(_V5_OUT / tid, ignore_errors=True)
        raise
    except Exception as exc:  # noqa: BLE001
        shutil.rmtree(_V5_OUT / tid, ignore_errors=True)
        raise HTTPException(status_code=500, detail=f"创建模板失败: {exc}") from exc


@router.delete("/templates/{template}", summary="删除 custom 模板（有 final 则 400）")
def delete_template(template: str) -> dict:
    tid = str(template or "").strip().lower()
    if not _ID_RE.fullmatch(tid):
        raise HTTPException(status_code=404, detail=f"模板不存在: {template}")
    custom = _custom_path("templates", tid)
    builtin = _BUILTIN_TEMPLATES / f"{tid}.json"
    if builtin.is_file() and not custom.is_file():
        raise _err(f"只能删除 custom 模板：{tid} 是内建模板（{builtin.name}）")
    if not custom.is_file():
        raise HTTPException(status_code=404, detail=f"custom 模板不存在: {tid}")
    if builtin.is_file():
        raise _err(f"只能删除 custom 模板：{tid} 有内建定义")
    finals = [_OUT3D / tid / "final.mp4", _V5_OUT / tid / "final.mp4"]
    if any(path.is_file() and path.stat().st_size > 0 for path in finals):
        raise _err(f"模板 {tid} 已有成片（final.mp4），不能删除")
    custom.unlink()
    semantic = _SEMANTIC_DIR / f"{tid}_semantic.json"
    if semantic.is_file():
        semantic.unlink()
    workdir = _V5_OUT / tid
    if workdir.is_dir():
        shutil.rmtree(workdir, ignore_errors=True)
    return {"ok": True, "id": tid, "deleted": True,
            "removed": [str(custom), str(semantic), str(workdir)]}
