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
import hashlib
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from fastapi import APIRouter, Body, HTTPException

_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from pipeline import v5_catalog  # noqa: E402

from .. import config  # noqa: E402

router = APIRouter(prefix="/api/v5", tags=["v5-create"])

_CONTRACT = _REPO_ROOT / "contract" / "v5"
_CUSTOM_DIR = _CONTRACT / "custom"
_BUILTIN_TEMPLATES = _CONTRACT / "templates"
_SEMANTIC_DIR = _CONTRACT / "cinema3d" / "templates3d"
_V5_OUT = Path(config.OUTPUTS_DIR) / "templates_v5"
_OUT3D = Path(config.OUTPUTS_DIR) / "templates_3d"
_FFMPEG = _REPO_ROOT / "tools" / "node_modules" / "ffmpeg-static" / "ffmpeg.exe"

_ID_RE = re.compile(r"^[a-z][a-z0-9_]{1,60}$")
_ROLE_RE = re.compile(r"^[a-z][a-z0-9_]{0,30}$")
_HEX_RE = re.compile(r"^#[0-9a-fA-F]{6}$")
# V41：节目类型
_RENDER_TYPES = ("2d", "3d", "realistic")
_RENDER_TYPE_LABELS = {"2d": "2D 动画", "3d": "3D 电影", "realistic": "写实实拍"}
# V44：2D 风格子类型
_RENDER_STYLES = {
    "jp_anime": "日漫电影感",
    "guoman": "国漫国风",
    "retro_cel": "复古赛璐璐",
    "soft_watercolor": "吉卜力治愈系",
    "arcane_cinematic": "赛博电影感",
}

#: V24 概念图（imagegen/SDXL）：单张 portrait；V28 扩展三视图/表情/设定图
_ART_DIR = _REPO_ROOT / "assets" / "v5" / "characters"
_ART_SPEC_DIR = _REPO_ROOT / "tmp" / "v28" / "specs"
_ART_STYLES = ("anime", "illustrious", "guoman", "realistic", "stylized_25d")
_ART_DEFAULT_STYLE = "anime"
_ART_TIMEOUT_SECONDS = 900.0
_ART_INFLIGHT: set = set()
_ART_STATE_LOCK = threading.Lock()
_ART_MODES = ("portrait", "three_view", "expressions", "sheet")
_ART_ALL_MODES = ("portrait", "three_view", "expressions", "sheet")
_ART_VIEWS = ("front", "side", "three_quarter")
_ART_EXPRESSIONS = ("neutral", "happy", "surprised", "thinking")
#: V30 立绘版本：style → 落盘文件名（anime 系列统一 portrait.png）
_ART_PORTRAIT_BY_STYLE = {
    "anime": "portrait.png",
    "illustrious": "portrait.png",
    "guoman": "portrait.png",
    "stylized_25d": "portrait_25d.png",
    "realistic": "portrait_real.png",
}
_ART_PORTRAIT_VARIANT_BY_STYLE = {
    "anime": "anime",
    "illustrious": "anime",
    "guoman": "anime",
    "stylized_25d": "stylized_25d",
    "realistic": "realistic",
}

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
# V24 角色概念图（imagegen / SDXL，单张 portrait）
# --------------------------------------------------------------------------- #
_IMG_OUTFIT_TAGS = {
    "hanfu": "hanfu, chinese clothes", "suit": "suit, necktie", "jacket": "jacket",
    "shirt": "shirt", "robe": "robe", "armor": "chinese armor",
    "tangzhuang": "tangzhuang", "qipao": "qipao", "apron": "apron",
    "labcoat": "lab coat", "sportswear": "sportswear", "hoodie": "hoodie",
    "gown": "gown", "dress": "dress", "uniform": "uniform", "coat": "coat",
}
_IMG_HAIR_TAGS = {
    "ancient_guan": "chinese hair bun, guan hat, long black hair",
    "lun_jin": "lun jin headwear", "short_swept": "short swept hair",
    "bob": "bob cut", "long_straight": "long straight hair",
    "ponytail": "ponytail", "bun": "hair bun", "messy": "messy hair",
    "curly": "curly hair", "buzz": "buzz cut", "long": "long hair",
}
_IMG_ACCESSORY_TAGS = {
    "feather_fan": "holding feather fan", "glasses": "glasses",
    "watch": "wristwatch", "earring": "earrings", "hat": "hat",
    "sword": "sword", "scroll": "holding scroll",
    "microphone": "microphone", "mic": "microphone", "headset": "headset",
    "headphones": "headphones", "crown": "crown", "helmet": "helmet",
    "necklace": "necklace", "mask": "mask",
}
_IMG_ERA_TAGS = {"history": "chinese ancient setting, historical",
                 "modern": "modern setting"}
_IMG_MARK_TAGS = {
    "smile_lines": "smile", "warm_smile": "warm smile", "scar": "scar",
    "freckles": "freckles", "beauty_mark": "beauty mark",
    "wrinkles": "slight wrinkles", "tired_eyes": "tired eyes",
}
_IMG_BEARD_TAGS = {
    "long_goatee": "long beard, goatee", "stubble": "stubble",
    "short_beard": "short beard", "full": "full beard", "none": "",
}


class _ArtBusyError(RuntimeError):
    """概念图并发/GPU 互斥（映射为 HTTP 409）。"""

    def __init__(self, reason: str, message: str, *, job_id: Optional[str] = None):
        super().__init__(message)
        self.reason = reason
        self.job_id = job_id


def _default_art_seed(character_id: str) -> int:
    return 42 + int(hashlib.sha1(character_id.encode("utf-8")).hexdigest()[:8], 16) % 10000


def _adapt_imagegen_spec(character: dict) -> dict:
    """contract 角色 appearance → imagegen spec（字段映射参考 tmp/gen_v52_char_specs.py）。"""
    ap = character.get("appearance") if isinstance(character.get("appearance"), dict) else {}
    outfit = ap.get("outfit") if isinstance(ap.get("outfit"), dict) else {}
    accessories = [str(item) for item in (ap.get("accessories") or []) if str(item).strip()]
    marks = [str(item) for item in (ap.get("marks") or []) if str(item).strip()]
    tags: List[str] = []
    for item in accessories:
        tag = _IMG_ACCESSORY_TAGS.get(item)
        if tag and tag not in tags:
            tags.append(tag)
    for item in marks:
        tag = _IMG_MARK_TAGS.get(item)
        if tag and tag not in tags:
            tags.append(tag)
    if ap.get("hat"):
        hat = str(ap["hat"]).strip()
        if hat and hat not in tags:
            tags.append(hat)
    outfit_type = str(outfit.get("type") or "")
    if _IMG_OUTFIT_TAGS.get(outfit_type):
        tags.append(_IMG_OUTFIT_TAGS[outfit_type])
    hair_style = str(ap.get("hair_style") or "")
    if _IMG_HAIR_TAGS.get(hair_style):
        tags.append(_IMG_HAIR_TAGS[hair_style])
    beard = str(ap.get("beard") or ap.get("facial_hair") or "")
    if _IMG_BEARD_TAGS.get(beard):
        tags.append(_IMG_BEARD_TAGS[beard])
    era = str(character.get("era") or "")
    if _IMG_ERA_TAGS.get(era):
        tags.append(_IMG_ERA_TAGS[era])
    character_id = str(character.get("id") or "")
    return {
        "id": character_id,
        "name": str(character.get("label") or character.get("name") or character_id),
        "title": str(character.get("persona") or ""),
        "persona": str(character.get("persona") or ""),
        "accent_color": str(outfit.get("accent") or ap.get("accent") or "#F5C518"),
        "style": {"primary": "3d_toon", "variants": ["3d_toon", "2d_cel"]},
        "appearance": {
            "gender": str(ap.get("gender") or "male"),
            "age": int(_to_float(ap.get("age"), 30)),
            "height_ratio": 1,
            "head_scale": float(_to_float(ap.get("head_ratio"), 1.0)),
            "chibi": float(_to_float(ap.get("chibi"), 0.18)),
            "skin": str(ap.get("skin") or "#F2D6C3"),
            "hair_color": str(ap.get("hair") or "#2B2B33"),
            "hair_style": hair_style or "short_swept",
            "eye_color": str(ap.get("eye") or "#2B2B33"),
            "eye_size": 1,
            "brow": str(ap.get("brow") or "normal"),
            "facial_hair": beard,
            "outfit": {
                "type": outfit_type or "suit",
                "main": str(outfit.get("main") or "#2F3A4F"),
                "accent": str(outfit.get("accent") or "#F5C518"),
                "shirt": str(outfit.get("shirt") or "#F4F1E8"),
            },
            "accessories": accessories,
            "marks": marks,
        },
        "extra_tags": tags,
    }


def _imagegen_spec_path(character: dict) -> Path:
    """构造并落盘 tmp/v24/specs/<id>.json。

    已有 `tmp/v52_art/specs/<id>.json`（24 个内建角色的精选 spec）优先复用其 appearance/style，
    但用 contract 的最新 label/persona/appearance 覆盖关键字段；custom/新角色走 appearance 映射。
    """
    character_id = str(character.get("id") or "")
    curated_path = _REPO_ROOT / "tmp" / "v52_art" / "specs" / f"{character_id}.json"
    spec: Optional[dict] = None
    if curated_path.is_file():
        try:
            loaded = json.loads(curated_path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                spec = loaded
        except (OSError, ValueError):
            spec = None
    if not isinstance(spec, dict):
        spec = _adapt_imagegen_spec(character)

    ap = character.get("appearance") if isinstance(character.get("appearance"), dict) else {}
    appearance = spec.get("appearance") if isinstance(spec.get("appearance"), dict) else {}
    appearance.update({
        "gender": str(ap.get("gender") or appearance.get("gender") or "male"),
        "age": int(_to_float(ap.get("age"), appearance.get("age") or 30)),
        "skin": str(ap.get("skin") or appearance.get("skin") or "#F2D6C3"),
        "hair_color": str(ap.get("hair") or appearance.get("hair_color") or "#2B2B33"),
        "hair_style": str(ap.get("hair_style") or appearance.get("hair_style") or "short_swept"),
        "eye_color": str(ap.get("eye") or appearance.get("eye_color") or "#2B2B33"),
        "brow": str(ap.get("brow") or appearance.get("brow") or "normal"),
        "facial_hair": str(ap.get("beard") or appearance.get("facial_hair") or ""),
    })
    outfit = ap.get("outfit") if isinstance(ap.get("outfit"), dict) else {}
    if outfit:
        current = appearance.get("outfit") if isinstance(appearance.get("outfit"), dict) else {}
        current.update({key: str(outfit.get(key) or current.get(key) or default) for key, default in
                        (("type", "suit"), ("main", "#2F3A4F"), ("accent", "#F5C518"), ("shirt", "#F4F1E8"))})
        appearance["outfit"] = current
    appearance.setdefault("outfit", {"type": "suit", "main": "#2F3A4F",
                                     "accent": "#F5C518", "shirt": "#F4F1E8"})
    appearance.setdefault("accessories", [])
    appearance.setdefault("marks", [])
    spec["appearance"] = appearance

    spec["id"] = character_id
    spec["name"] = str(character.get("label") or character.get("name") or spec.get("name") or character_id)
    spec["title"] = str(character.get("persona") or spec.get("title") or "")
    spec["persona"] = str(character.get("persona") or spec.get("persona") or "")
    style_meta = spec.get("style") if isinstance(spec.get("style"), dict) else {}
    style_meta.setdefault("primary", "3d_toon")
    style_meta.setdefault("variants", ["3d_toon", "2d_cel"])
    spec["style"] = style_meta
    extra_tags = spec.get("extra_tags") if isinstance(spec.get("extra_tags"), list) else []
    spec["extra_tags"] = [str(tag) for tag in extra_tags if str(tag).strip()]

    _ART_SPEC_DIR.mkdir(parents=True, exist_ok=True)
    spec_path = _ART_SPEC_DIR / f"{character_id}.json"
    _write_json(spec_path, spec)
    return spec_path


def _art_portrait_path(character_id: str, filename: str = "portrait.png") -> Path:
    return _ART_DIR / character_id / filename


def _art_portrait_file_for_style(style: str) -> str:
    return _ART_PORTRAIT_BY_STYLE.get(str(style or "").strip().lower(), "portrait.png")


def _art_portrait_variant_for_style(style: str) -> str:
    return _ART_PORTRAIT_VARIANT_BY_STYLE.get(str(style or "").strip().lower(), "anime")


def _art_asset_paths(character_id: str) -> Dict[str, Any]:
    """角色素材路径表：portrait 三版本 / three_view(3) / expressions(4) / sheet。"""
    base = _ART_DIR / character_id
    return {
        "portrait": base / "portrait.png",
        "portrait_25d": base / "portrait_25d.png",
        "portrait_real": base / "portrait_real.png",
        "three_view": {view: base / "three_view" / f"{view}.png" for view in _ART_VIEWS},
        "expressions": {expr: base / "expressions" / f"{expr}.png" for expr in _ART_EXPRESSIONS},
        "sheet": base / "sheet.png",
    }


def _art_file_ok(path: Path) -> bool:
    try:
        return path.is_file() and path.stat().st_size > 2000
    except OSError:
        return False


def _art_file_url(character_id: str, rel: str) -> Optional[str]:
    path = _ART_DIR / character_id / rel
    try:
        if not path.is_file() or path.stat().st_size <= 2000:
            return None
        return (f"/media/v5/characters/{character_id}/{rel}"
                f"?t={int(path.stat().st_mtime)}")
    except OSError:
        return None


def _art_portrait_url(character_id: str, filename: str = "portrait.png") -> Optional[str]:
    return _art_file_url(character_id, filename)


def _art_portraits_payload(character_id: str) -> Dict[str, Optional[str]]:
    return {
        "anime": _art_file_url(character_id, "portrait.png"),
        "stylized_25d": _art_file_url(character_id, "portrait_25d.png"),
        "realistic": _art_file_url(character_id, "portrait_real.png"),
    }


def _art_assets_payload(character_id: str) -> Dict[str, Any]:
    portraits = _art_portraits_payload(character_id)
    return {
        "portrait": portraits.get("anime"),
        "portraits": portraits,
        "three_view": {view: _art_file_url(character_id, f"three_view/{view}.png")
                       for view in _ART_VIEWS},
        "expressions": {expr: _art_file_url(character_id, f"expressions/{expr}.png")
                        for expr in _ART_EXPRESSIONS},
        "sheet": _art_file_url(character_id, "sheet.png"),
    }


def _art_asset_files(character_id: str) -> List[Dict[str, Any]]:
    base = _ART_DIR / character_id
    rels = ["portrait.png", "portrait_25d.png", "portrait_real.png"] \
        + [f"three_view/{view}.png" for view in _ART_VIEWS] \
        + [f"expressions/{expr}.png" for expr in _ART_EXPRESSIONS] \
        + ["sheet.png", "meta.json"]
    rows: List[Dict[str, Any]] = []
    for rel in rels:
        path = base / rel
        try:
            if not path.is_file() or path.stat().st_size <= 0:
                continue
            stat = path.stat()
        except OSError:
            continue
        rows.append({"name": rel, "size": int(stat.st_size),
                     "url": _art_file_url(character_id, rel),
                     "mtime": time.strftime("%Y-%m-%dT%H:%M:%S",
                                            time.localtime(stat.st_mtime))})
    return rows


def _normalize_art_modes(raw: Any) -> Tuple[List[str], List[str]]:
    """modes 归一化：["all"] 展开全部；返回 (modes, errors)。"""
    if raw is None:
        return ["portrait"], []
    if isinstance(raw, str):
        raw = [raw]
    if not isinstance(raw, (list, tuple)):
        return [], ["modes 需为字符串数组"]
    modes: List[str] = []
    errors: List[str] = []
    for item in raw:
        token = str(item or "").strip().lower()
        if not token:
            continue
        if token == "all":
            for mode in _ART_ALL_MODES:
                if mode not in modes:
                    modes.append(mode)
        elif token in _ART_MODES:
            if token not in modes:
                modes.append(token)
        else:
            errors.append(f"未知 mode: {token}（可选 {', '.join(_ART_MODES)} 或 all）")
    if not modes and not errors:
        modes = ["portrait"]
    return modes, errors


def _run_imagegen_cli(spec_path: Path, outdir: Path, *, mode: str, style: str, seed: int,
                      prompt_extra: str, timeout: float = _ART_TIMEOUT_SECONDS,
                      ckpt: Optional[str] = None) -> dict:
    """调用 imagegen CLI（子进程），解析末尾 JSON 摘要；失败抛 RuntimeError（含可读 tail）。"""
    cmd = [sys.executable, "-m", "imagegen.cli", "generate",
           "--spec", str(spec_path), "--outdir", str(outdir),
           "--mode", mode, "--style", style, "--seed", str(seed),
           "--timeout", str(int(max(60, timeout - 60)))]
    chosen_ckpt = (ckpt or os.environ.get("V5_ART_CKPT", "")).strip()
    if chosen_ckpt:
        cmd += ["--ckpt", chosen_ckpt]
    if prompt_extra:
        cmd += ["--prompt-extra", prompt_extra]
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    try:
        proc = subprocess.run(cmd, cwd=str(_REPO_ROOT), capture_output=True, text=True,
                              encoding="utf-8", errors="replace", env=env,
                              timeout=timeout + 30)
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"imagegen {mode} 超时（{timeout + 30:.0f}s）") from exc
    output = (proc.stdout or "") + "\n" + (proc.stderr or "")
    summary: dict = {}
    for line in reversed((proc.stdout or "").strip().splitlines()):
        line = line.strip()
        if line.startswith("{") and line.endswith("}"):
            try:
                summary = json.loads(line)
                break
            except ValueError:
                continue
    if proc.returncode != 0 or not summary.get("ok"):
        tail = "\n".join(output.strip().splitlines()[-10:])
        raise RuntimeError(f"imagegen {mode} 失败（returncode={proc.returncode}）：{tail[-1200:]}")
    return summary


def _art_modes_complete(character_id: str, modes: List[str], *,
                        portrait_file: str = "portrait.png") -> bool:
    paths = _art_asset_paths(character_id)
    for mode in modes:
        if mode == "portrait" and not _art_file_ok(
                _art_portrait_path(character_id, portrait_file)):
            return False
        if mode == "three_view" and not all(_art_file_ok(path) for path in
                                            paths["three_view"].values()):
            return False
        if mode == "expressions" and not all(_art_file_ok(path) for path in
                                             paths["expressions"].values()):
            return False
        if mode == "sheet" and not _art_file_ok(paths["sheet"]):
            return False
    return True


def _generate_art(character: dict, *, style: str, prompt_extra: str, seed: int,
                  force: bool, modes: Optional[List[str]] = None) -> dict:
    """生成角色素材（同步）：cached → 3D job 互斥 → 同角色互斥 → GPU 串行锁 → imagegen。

    V30：style 决定立绘落盘版本（anime 系列 portrait.png / stylized_25d portrait_25d.png /
    realistic portrait_real.png），cached 判断按对应版本文件；其他 mode（三视图/表情/sheet）共用。
    """
    normalized, errors = _normalize_art_modes(modes if modes is not None else ["portrait"])
    if errors:
        raise ValueError("; ".join(errors))
    character_id = str(character.get("id") or "")
    portrait_file = _art_portrait_file_for_style(style)
    portrait_variant = _art_portrait_variant_for_style(style)
    if not force and _art_modes_complete(character_id, normalized,
                                         portrait_file=portrait_file):
        return {
            "character": character_id,
            "portrait_url": _art_portrait_url(character_id, portrait_file),
            "portrait_file": portrait_file,
            "portrait_variant": portrait_variant,
            "assets": _art_assets_payload(character_id),
            "files": _art_asset_files(character_id),
            "generated": [],
            "modes": normalized,
            "elapsed_sec": 0.0,
            "style": style,
            "seed": seed,
            "cached": True,
            "warnings": [],
        }

    # 与 3D 生成互斥：正在跑 pipeline job 时直接 409，避免抢 ComfyUI/GPU
    try:
        from .pipeline_ui import active_pipeline_job  # noqa: PLC0415

        active = active_pipeline_job()
    except Exception:  # noqa: BLE001 - 内部查询失败不阻塞（仍受 GPU_LOCK 串行）
        active = None
    if active:
        raise _ArtBusyError(
            "3d_job_running",
            f"3D 生成任务运行中（{active.get('template_id') or active.get('id')}），请等它结束再生成角色素材",
            job_id=str(active.get("id") or "") or None)

    with _ART_STATE_LOCK:
        if character_id in _ART_INFLIGHT:
            raise _ArtBusyError("same_character_running",
                                f"角色 {character_id} 素材正在生成中，请稍后")
        _ART_INFLIGHT.add(character_id)
    started = time.time()
    warnings: List[str] = []
    staging: Optional[Path] = None
    try:
        from .. import jobs  # noqa: PLC0415 - 与 concept job / h3 共用 ComfyUI 串行锁
        # 2.5D/写实版本换用对应 checkpoint（V5.2 既有立绘分别用 AAM / RealVisXL）
        env_ckpt = os.environ.get("V5_ART_CKPT", "").strip()
        ckpt = env_ckpt
        if not ckpt and style == "realistic":
            ckpt = "RealVisXL_V5.0_fp16.safetensors"
        elif not ckpt and style == "stylized_25d":
            ckpt = "AAM_XL_Anime_Mix.safetensors"
        with jobs.GPU_LOCK:
            spec_path = _imagegen_spec_path(character)
            outdir = _ART_DIR / character_id
            outdir.mkdir(parents=True, exist_ok=True)
            direct = portrait_file == "portrait.png"
            run_outdir = outdir
            if not direct:
                staging = outdir / f".art-{uuid.uuid4().hex[:10]}"
                staging.mkdir(parents=True, exist_ok=True)
                run_outdir = staging
            if list(normalized) == list(_ART_ALL_MODES):
                runs = ["all"]
            else:
                runs = list(normalized)
            summaries: List[dict] = []
            for run_mode in runs:
                summaries.append(_run_imagegen_cli(
                    spec_path, run_outdir, mode=run_mode, style=style, seed=seed,
                    prompt_extra=prompt_extra, ckpt=ckpt))
            if not direct and staging is not None:
                if "portrait" in normalized:
                    src = staging / "portrait.png"
                    if not _art_file_ok(src):
                        raise RuntimeError(f"imagegen 未产出 portrait.png（staging={staging}）")
                    os.replace(src, _art_portrait_path(character_id, portrait_file))
                if "three_view" in normalized:
                    src_dir = staging / "three_view"
                    if src_dir.is_dir():
                        shutil.copytree(src_dir, outdir / "three_view", dirs_exist_ok=True)
                if "expressions" in normalized:
                    src_dir = staging / "expressions"
                    if src_dir.is_dir():
                        shutil.copytree(src_dir, outdir / "expressions", dirs_exist_ok=True)
                if "sheet" in normalized:
                    src_sheet = staging / "sheet.png"
                    if src_sheet.is_file():
                        os.replace(src_sheet, outdir / "sheet.png")
        elapsed = round(time.time() - started, 1)
        missing = []
        paths = _art_asset_paths(character_id)
        if "portrait" in normalized and not _art_file_ok(
                _art_portrait_path(character_id, portrait_file)):
            missing.append(portrait_file)
        if "three_view" in normalized:
            missing += [f"three_view/{view}.png" for view in _ART_VIEWS
                        if not _art_file_ok(paths["three_view"][view])]
        if "expressions" in normalized:
            missing += [f"expressions/{expr}.png" for expr in _ART_EXPRESSIONS
                        if not _art_file_ok(paths["expressions"][expr])]
        if "sheet" in normalized and not _art_file_ok(paths["sheet"]):
            missing.append("sheet.png")
        if missing:
            raise RuntimeError(f"imagegen 未产出: {', '.join(missing)}（outdir={outdir}）")
        return {
            "character": character_id,
            "portrait_url": _art_portrait_url(character_id, portrait_file),
            "portrait_file": portrait_file,
            "portrait_variant": portrait_variant,
            "assets": _art_assets_payload(character_id),
            "files": _art_asset_files(character_id),
            "generated": list(normalized),
            "modes": list(normalized),
            "elapsed_sec": elapsed,
            "style": style,
            "seed": seed,
            "cached": False,
            "images": sum(len(summary.get("images") or []) for summary in summaries),
            "imagegen_elapsed_sec": sum(float(summary.get("elapsed_sec") or 0.0)
                                        for summary in summaries),
            "warnings": warnings,
        }
    finally:
        if staging is not None:
            shutil.rmtree(staging, ignore_errors=True)
        with _ART_STATE_LOCK:
            _ART_INFLIGHT.discard(character_id)


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
    auto_art = _as_bool(data.get("auto_art"))  # V24：创建成功后同步生成概念图
    auto_art_modes: List[str] = ["portrait"]
    if auto_art:
        auto_art_modes, auto_mode_errors = _normalize_art_modes(data.get("auto_art_modes"))
        if auto_mode_errors:
            raise _err("auto_art_modes 不合法", auto_mode_errors)
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
    warnings: List[str] = list(result.get("warnings") or [])
    art: Optional[Dict[str, Any]] = None
    if auto_art:
        # V24/V28：同步生成角色素材（portrait 20–60s；all 约 40–90s）；失败不阻塞创建
        art_started = time.time()
        try:
            art_result = _generate_art(merged, style=_ART_DEFAULT_STYLE, prompt_extra="",
                                       seed=_default_art_seed(cid), force=False,
                                       modes=auto_art_modes)
            art = {
                "generated": True,
                "portrait_url": art_result.get("portrait_url"),
                "assets": art_result.get("assets"),
                "generated_modes": art_result.get("generated"),
                "modes": art_result.get("modes"),
                "elapsed_sec": art_result.get("elapsed_sec"),
                "cached": bool(art_result.get("cached")),
                "style": art_result.get("style"),
                "seed": art_result.get("seed"),
            }
        except Exception as exc:  # noqa: BLE001 - auto_art 失败不影响创建
            message = f"{type(exc).__name__}: {exc}"
            warnings.append(f"auto_art 素材生成失败：{message}")
            art = {"generated": False, "portrait_url": None, "assets": None,
                   "modes": auto_art_modes,
                   "elapsed_sec": round(time.time() - art_started, 1),
                   "cached": False, "error": message[:400]}
    return {
        "ok": True,
        "id": cid,
        "item": merged,
        "preview_url": f"/api/v5/preview/characters/{cid}",
        "art": art,
        "path": str(path),
        "warnings": warnings,
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
# V24/V28 角色素材：立绘 / 三视图 / 表情包 / 设定图（imagegen/SDXL）
# --------------------------------------------------------------------------- #
@router.post("/characters/{character}/art", summary="生成/重新生成角色素材（portrait/three_view/expressions/sheet/all）")
def generate_character_art(character: str, body: Dict[str, Any] = Body(default={})) -> dict:
    cid = str(character or "").strip().lower()
    if not _ID_RE.fullmatch(cid):
        raise HTTPException(status_code=404, detail=f"角色不存在: {character}")
    catalog = _catalog()
    char = (catalog.get("characters") or {}).get(cid)
    if not isinstance(char, dict):
        raise HTTPException(status_code=404, detail=f"角色不存在: {cid}")
    data = body if isinstance(body, dict) else {}
    raw_modes = data.get("modes")
    if raw_modes is None and data.get("mode") is not None:
        raw_modes = [data.get("mode")]
    modes, mode_errors = _normalize_art_modes(raw_modes)
    if mode_errors:
        raise _err("modes 不合法", mode_errors)
    style = str(data.get("style") or _ART_DEFAULT_STYLE).strip().lower()
    if style not in _ART_STYLES:
        raise _err(f"未知 style: {style}（可选 {', '.join(_ART_STYLES)}）")
    prompt_extra = str(data.get("prompt_extra") or "").strip()[:500]
    try:
        has_seed = data.get("seed") is not None
        seed = int(data.get("seed")) if has_seed else _default_art_seed(cid)
    except (TypeError, ValueError) as exc:
        raise _err(f"seed 必须是整数: {data.get('seed')!r}") from exc
    force = _as_bool(data.get("force"))
    if force and not has_seed:
        # 「重新生成」默认换一个 seed（前端也可显式传 seed 复现）；否则 ComfyUI 会命中相同 prompt 缓存
        seed = (seed + int(time.time() * 1000)) % (2 ** 31 - 1)
    try:
        result = _generate_art(char, style=style, prompt_extra=prompt_extra,
                               seed=seed, force=force, modes=modes)
    except _ArtBusyError as exc:
        raise HTTPException(status_code=409, detail={
            "message": str(exc), "reason": exc.reason,
            "job_id": exc.job_id, "character": cid,
        }) from exc
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001 - imagegen 失败可读返回
        raise HTTPException(status_code=500, detail=f"角色素材生成失败: {exc}") from exc
    return {
        "ok": True,
        **result,
        "mode": modes[0] if len(modes) == 1 else "mixed",
        "modes": modes,
    }


@router.get("/characters/{character}/assets", summary="扫描角色已生成素材（立绘/三视图/表情/设定图）")
def get_character_assets(character: str) -> dict:
    cid = str(character or "").strip().lower()
    if not _ID_RE.fullmatch(cid):
        raise HTTPException(status_code=404, detail=f"角色不存在: {character}")
    catalog = _catalog()
    char = (catalog.get("characters") or {}).get(cid)
    if not isinstance(char, dict):
        raise HTTPException(status_code=404, detail=f"角色不存在: {cid}")
    paths = _art_asset_paths(cid)
    assets = _art_assets_payload(cid)
    exists = {
        "portrait": _art_file_ok(paths["portrait"]),
        "portrait_25d": _art_file_ok(paths["portrait_25d"]),
        "portrait_real": _art_file_ok(paths["portrait_real"]),
        "three_view": all(_art_file_ok(path) for path in paths["three_view"].values()),
        "expressions": all(_art_file_ok(path) for path in paths["expressions"].values()),
        "sheet": _art_file_ok(paths["sheet"]),
        "meta": (paths["portrait"].parent / "meta.json").is_file(),
    }
    return {
        "ok": True,
        "character": cid,
        "assets": assets,
        "exists": exists,
        "files": _art_asset_files(cid),
        "any": any(exists[key] for key in ("portrait", "portrait_25d", "portrait_real",
                                           "three_view", "expressions", "sheet")),
    }


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
    # V41：节目类型（2d/3d/realistic）；缺省 real_* → realistic，否则 3d
    render_type = str(data.get("render_type") or "").strip().lower()
    if not render_type:
        render_type = "realistic" if tid.startswith("real_") else "3d"
    if render_type not in _RENDER_TYPES:
        errors.append(f"render_type 只支持 {', '.join(_RENDER_TYPES)}（当前 {render_type!r}）")
    # V44：2D 风格（仅 2D 有意义；缺省 jp_anime；3D/写实忽略）
    render_style = None
    keyframe_candidates = None
    if render_type == "2d":
        render_style = str(data.get("render_style") or "jp_anime").strip().lower()
        if render_style not in _RENDER_STYLES:
            errors.append(f"render_style 只支持 {', '.join(_RENDER_STYLES)}"
                          f"（当前 {render_style!r}）")
        # V46：2D 关键帧抽卡张数（1–6，默认 1）
        try:
            keyframe_candidates = max(1, min(6, int(data.get("keyframe_candidates") or 1)))
        except (TypeError, ValueError):
            errors.append("keyframe_candidates 必须是 1–6 的整数")
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
            "render_type": render_type,
            **({"render_style": render_style} if render_style else {}),
            **({"keyframe_candidates": keyframe_candidates} if keyframe_candidates and keyframe_candidates > 1 else {}),
            "cast": cast, "acts": template_acts,
            "packaging": {"subtitle": "default", "transition": "fade", "title": label},
            "lipsync": {"mode": "musetalk"},
            "audio": {},
            "meta": {"create_source": "v21", "three_d": render_type != "2d",
                     "render_type": render_type,
                     **({"render_style": render_style} if render_style else {}),
                     **({"keyframe_candidates": keyframe_candidates}
                        if keyframe_candidates and keyframe_candidates > 1 else {}),
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
            "render_type": render_type,
            "render_type_label": _RENDER_TYPE_LABELS.get(render_type, render_type),
            **({"render_style": render_style,
                "render_style_label": _RENDER_STYLES.get(render_style, render_style)}
               if render_style else {}),
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
