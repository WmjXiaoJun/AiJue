# -*- coding: utf-8 -*-
"""V5 契约 API（FastAPI 路由）：目录浏览 / custom 读写 / 校验 / 预览。

挂载点：`/api/v5`（与既有 V3/V4 `/api/*` 接口互不影响，路由前缀独立）。

端点：
  GET  /api/v5/catalog                      目录摘要（styles/scenes/characters/props/templates/packs）
  GET  /api/v5/{kind}/{id}                  读取对象（内建 + custom 深合并后的完整值）
  PUT  /api/v5/{kind}/{id}                  保存 custom 覆盖（先 validate_item，通过才写
                                            contract/v5/custom/{kind}/{id}.json）
  POST /api/v5/validate                     全目录校验（body: {"strict": bool, "root": "..."}）
  GET  /api/v5/preview/{kind}/{id}          预览 PNG（style/scene/character/template；
                                            渲染模块未就绪返回 501）

kind 接受复数或单数：styles/style、scenes/scene …；未知 kind → 404。
"""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from pathlib import Path
from typing import Any, Dict, Optional

from fastapi import APIRouter, Body, HTTPException, Query, Response

# 允许以 `uvicorn app.main:app --app-dir backend` 启动时也能 import 仓库根下的 pipeline.*
_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from pipeline import v5_catalog  # noqa: E402

from .. import config  # noqa: E402

router = APIRouter(prefix="/api/v5", tags=["v5-contract"])

KIND_CHOICES = "、".join(v5_catalog.KINDS)

#: 角色试听 wav 目录（/media/v5/voice/<character>.wav 由 v5_studio 静态路由提供）
_VOICE_DIR = _REPO_ROOT / "tmp" / "v5_voice"
_CHAR_ASSET_DIR = _REPO_ROOT / "assets" / "v5" / "characters"
_PORTRAIT_NAMES = (
    "portrait.png", "portrait.jpg", "portrait.jpeg", "portrait.webp",
    "portrait_real.png", "portrait_25d.png", "front.png", "cutout.png", "thumb.png",
)
_PREVIEW_TIMEOUT_SECONDS = 60.0
_VOICE_CASTING_PATH = _REPO_ROOT / "contract" / "v5" / "voice_casting.json"


def _catalog() -> dict:
    """读取最新目录（每次请求实时扫描，队友新写入的实体立即可见）。"""
    try:
        return v5_catalog.load_catalog()
    except v5_catalog.V5Error as exc:  # 理论上 load_catalog 不抛，兜底成 500
        raise HTTPException(status_code=500, detail=f"目录读取失败: {exc}") from exc


def _kind_or_404(kind: str) -> str:
    try:
        return v5_catalog.normalize_kind(kind)
    except v5_catalog.V5Error as exc:
        raise HTTPException(status_code=404,
                            detail=f"未知 kind: {kind!r}（可用: {KIND_CHOICES}）") from exc


def _item_or_404(catalog: dict, kind: str, item_id: str) -> dict:
    item = (catalog.get(kind) or {}).get(str(item_id))
    if not isinstance(item, dict):
        raise HTTPException(status_code=404,
                            detail=f"{kind}/{item_id} 不存在")
    return item


# ---------------------------------------------------------------------------
# 目录 / 校验
# ---------------------------------------------------------------------------
@router.get("/catalog", summary="V5 目录摘要")
def get_catalog(kind: Optional[str] = Query(default=None, description="只看某个 kind（可选）"),
                full: bool = Query(default=False, description="true=返回完整对象（体积大）")):
    catalog = _catalog()
    if kind is None:
        summary = v5_catalog.catalog_summary(catalog)
    else:
        summary = v5_catalog.catalog_summary(catalog, _kind_or_404(kind))
    if full:
        kinds = summary.get("kinds") or []
        for current in kinds:
            summary[current] = [
                dict(item, source=_source_of(catalog, current, str(item.get("id"))))
                for item in (catalog.get(current) or {}).values()
                if isinstance(item, dict)
            ]
    _enrich_template_summaries(catalog, summary)
    return summary


def _source_of(catalog: dict, kind: str, item_id: str) -> dict:
    return v5_catalog.item_source(catalog, kind, item_id)


_RENDER_TYPE_LABELS = {"2d": "2D 动画", "3d": "3D 电影", "realistic": "写实实拍"}
_RENDER_STYLE_LABELS = {"jp_anime": "日漫电影感", "guoman": "国漫国风",
                        "retro_cel": "复古赛璐璐", "soft_watercolor": "吉卜力治愈系"}


def _template_render_type(item_id: str, item: dict) -> str:
    raw = str((item or {}).get("render_type") or "").strip().lower()
    if raw in _RENDER_TYPE_LABELS:
        return raw
    return "realistic" if str(item_id).startswith("real_") else "3d"


def _template_render_style(item_id: str, item: dict) -> str | None:
    if _template_render_type(item_id, item) != "2d":
        return None
    raw = str((item or {}).get("render_style") or "").strip().lower()
    return raw if raw in _RENDER_STYLE_LABELS else "jp_anime"


def _template_type_fields(item_id: str, item: dict) -> dict:
    render_type = _template_render_type(item_id, item)
    fields = {"render_type": render_type,
              "render_type_label": _RENDER_TYPE_LABELS[render_type]}
    render_style = _template_render_style(item_id, item)
    if render_style:
        fields["render_style"] = render_style
        fields["render_style_label"] = _RENDER_STYLE_LABELS[render_style]
    return fields


def _enrich_template_summaries(catalog: dict, summary: dict) -> None:
    templates = catalog.get("templates") or {}
    for row in summary.get("templates") or []:
        if not isinstance(row, dict):
            continue
        tid = str(row.get("id") or "")
        item = templates.get(tid) if isinstance(templates.get(tid), dict) else {}
        if not item and "render_type" in row:
            item = row
        row.update(_template_type_fields(tid, item))


@router.post("/validate", summary="校验整个 V5 目录（Schema/引用/custom id/资源）")
def post_validate(payload: Optional[Dict[str, Any]] = Body(default=None)):
    payload = payload or {}
    strict = bool(payload.get("strict"))
    root = payload.get("root") or None
    catalog = _catalog() if not root else v5_catalog.load_catalog(root)
    result = v5_catalog.validate_catalog(catalog, strict=strict)
    return result


# ---------------------------------------------------------------------------
# 预览（放在 /{kind}/{id} 前注册，路径段更多，互不冲突）
# ---------------------------------------------------------------------------
@router.get("/preview/{kind}/{item_id}", summary="渲染预览 PNG（无渲染模块则 501）")
def get_preview(kind: str, item_id: str,
                style: Optional[str] = Query(default=None, description="scene/character 预览用 style id"),
                size: Optional[str] = Query(default=None, description="如 1280x720")):
    from pipeline import v5_cli  # 延迟 import：避免启动时拉起渲染依赖

    catalog = _catalog()
    current = _kind_or_404(kind)
    item = _item_or_404(catalog, current, item_id)
    try:
        data = v5_cli.render_preview_png(current, item, catalog,
                                        style_id=style, size=size)
    except v5_cli.PreviewNotReady as exc:
        raise HTTPException(status_code=501,
                            detail=f"渲染模块未就绪: {exc}") from exc
    except v5_catalog.V5Error as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001 - 渲染异常如实返回
        raise HTTPException(status_code=500, detail=f"渲染失败: {exc}") from exc
    return Response(content=data, media_type="image/png",
                    headers={"Cache-Control": "no-store"})


# ---------------------------------------------------------------------------
# 单对象读取 / 保存（custom 覆盖）
# ---------------------------------------------------------------------------
@router.get("/{kind}/{item_id}", summary="读取单个对象（内建+custom 合并）")
def get_item(kind: str, item_id: str):
    catalog = _catalog()
    current = _kind_or_404(kind)
    item = _item_or_404(catalog, current, item_id)
    source = _source_of(catalog, current, item_id)
    response_item = dict(item)
    if current == "templates":
        response_item.update(_template_type_fields(item_id, item))
    return {
        "ok": True,
        "kind": current,
        "id": item_id,
        "item": response_item,
        "custom": bool(source.get("custom")),
        "source": source.get("path", ""),
        "base": source.get("base", ""),
        "custom_path": str(v5_catalog.custom_path(current, item_id)),
    }


@router.put("/{kind}/{item_id}", summary="保存 custom 覆盖（保存前校验）")
def put_item(kind: str, item_id: str,
             payload: Dict[str, Any] = Body(
                 ..., description="对象本体；也接受 {\"item\": {...}, \"dry_run\": bool}"),
             dry_run: bool = Query(default=False, description="只校验不落盘")):
    current = _kind_or_404(kind)
    catalog = _catalog()
    body = payload or {}
    body_dry = bool(body.get("dry_run")) if isinstance(body, dict) else False
    item = body.get("item") if isinstance(body.get("item"), dict) else body
    if not isinstance(item, dict):
        raise HTTPException(status_code=400, detail="请求体必须是 JSON object")
    item = dict(item)
    body_id = item.get("id")
    if body_id not in (None, "", item_id):
        raise HTTPException(status_code=400,
                            detail=f"body.id={body_id!r} 与 URL id={item_id!r} 不一致")
    item["id"] = item_id
    result = v5_catalog.validate_item(current, item, catalog, as_custom=True)
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail={
            "message": "校验失败，未写入",
            "kind": current,
            "id": item_id,
            "errors": result.get("errors") or [],
            "warnings": result.get("warnings") or [],
        })
    # partial 覆盖语义：只有当存在"内建基线"时，落盘片段才有意义（加载时深合并）；
    # 对纯 custom 对象（无内建基线）必须落盘完整对象，否则下次加载会缺字段。
    source = v5_catalog.item_source(catalog, current, item_id)
    existing = (catalog.get(current) or {}).get(item_id)
    has_base = bool(source) and (not source.get("custom") or bool(source.get("base")))
    write_mode = "full"
    to_write = item
    if isinstance(existing, dict):
        if has_base:
            write_mode = "partial"
        else:
            to_write = v5_catalog.deep_merge(existing, item)
            to_write["id"] = item_id
    path = v5_catalog.custom_path(current, item_id, root=catalog.get("root"))
    if dry_run or body_dry:
        return {"ok": True, "kind": current, "id": item_id, "dry_run": True,
                "validated": True, "warnings": result.get("warnings") or [],
                "path": str(path), "write_mode": write_mode}
    v5_catalog.write_json_atomic(path, to_write)
    return {"ok": True, "kind": current, "id": item_id, "path": str(path),
            "custom": True, "validated": True,
            "merged": bool(result.get("merged")),
            "write_mode": write_mode,
            "warnings": result.get("warnings") or []}


# ---------------------------------------------------------------------------
# 角色与声音：试听 / 选角（V18）
# ---------------------------------------------------------------------------
def _preview_wav_path(character: str) -> Path:
    return _VOICE_DIR / f"{character}.wav"


def _preview_meta_path(character: str) -> Path:
    return _VOICE_DIR / f"{character}.json"


def _preview_url_for(name: str) -> str:
    path = _VOICE_DIR / f"{name}.wav"
    try:
        stamp = int(path.stat().st_mtime)
    except OSError:
        stamp = 0
    return f"{config.MEDIA_URL_PREFIX}/v5/voice/{name}.wav?t={stamp}"


def _preview_url(character: str) -> str:
    return _preview_url_for(character)


def _preset_preview_url(voice_id: str) -> Optional[str]:
    path = _VOICE_DIR / f"{voice_id}.wav"
    try:
        if not path.is_file() or path.stat().st_size <= 1000:
            return None
        return f"{config.MEDIA_URL_PREFIX}/v5/voice/{voice_id}.wav?t={int(path.stat().st_mtime)}"
    except OSError:
        return None


def _wav_duration(path: Path) -> float:
    try:
        import wave

        with wave.open(str(path), "rb") as handle:
            rate = handle.getframerate() or 1
            return round(handle.getnframes() / float(rate), 3)
    except Exception:  # noqa: BLE001
        return 0.0


def _default_preview_text(character: str, char: dict) -> str:
    """默认试听文本：优先 studio 的 voice_casting.json（若已落地），否则按角色名生成短句。"""
    try:
        if _VOICE_CASTING_PATH.is_file():
            data = json.loads(_VOICE_CASTING_PATH.read_text(encoding="utf-8"))
            chars = data.get("characters") if isinstance(data, dict) else None
            if not isinstance(chars, dict):
                chars = data.get("map") if isinstance(data, dict) else None
            if isinstance(chars, dict):
                entry = chars.get(character)
                if isinstance(entry, dict):
                    for key in ("preview_text", "line", "text", "preview_line"):
                        value = str(entry.get(key) or "").strip()
                        if value:
                            return value
    except (OSError, ValueError):
        pass
    name = str(char.get("label") or char.get("name") or character)
    return f"你好，我是{name}。今天想和你聊两句。"


def _edge_character_spec(char: dict, raw: dict) -> dict:
    cid = str(char.get("id") or "")
    voice_id = str(raw.get("voice_id") or "").strip()
    instruction = str(raw.get("instruction") or "")
    return {
        "character": cid,
        "voice_id": voice_id,
        "provider": "edge",
        "label": str(raw.get("label") or char.get("label") or "") or instruction[:40] or voice_id,
        "instruction": instruction,
        "seed": raw.get("seed"),
        "cfg_scale": raw.get("cfg_scale"),
        "rate": str(raw.get("rate") or "+0%"),
        "key": "edge:" + hashlib.sha1(
            f"{voice_id}|{instruction}|{raw.get('rate') or ''}".encode("utf-8")).hexdigest()[:12],
    }


def _effective_voice(char: dict) -> dict:
    """解析角色声音；`provider=edge` 的写实角色不走 Breeze voice 库（无 seed/preset）。"""
    from pipeline import v5_voices  # noqa: PLC0415 - 延迟 import，避免启动依赖

    raw = char.get("voice") if isinstance(char.get("voice"), dict) else {}
    provider = str(raw.get("provider") or "").strip().lower()
    voice_id = str(raw.get("voice_id") or "").strip()
    if provider == "edge":
        return _edge_character_spec(char, raw)
    try:
        return v5_voices.resolve_voice(char, v5_voices.load_voice_library())
    except Exception:  # noqa: BLE001 - 兼容 provider 未标 edge 但 voice_id 是 edge 音色的角色
        if voice_id.lower().startswith("zh-cn-"):
            return _edge_character_spec(char, raw)
        raise


def _edge_voice_for(spec: dict) -> str:
    """Breeze preset / 未知音色 → 选一个语义相近的 edge 中文音色（试听兜底）。"""
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
    return "zh-CN-YunxiNeural"


def _synth_edge(text: str, voice: str, rate: str, out_wav: Path) -> None:
    from pipeline import tts_engine, utils  # noqa: PLC0415

    mp3 = out_wav.with_name(f"{out_wav.stem}.edge.mp3")
    try:
        tts_engine.synth(text, voice, mp3, rate=rate or "+0%")
        utils.ffmpeg(["-y", "-hide_banner", "-loglevel", "error", "-i", str(mp3),
                      "-ar", "48000", "-ac", "2", "-c:a", "pcm_s16le", str(out_wav)])
    finally:
        try:
            mp3.unlink(missing_ok=True)
        except OSError:
            pass


def _synth_breeze(spec: dict, text: str, out_wav: Path) -> None:
    from pipeline import tts_breeze, utils  # noqa: PLC0415

    if not tts_breeze.server_alive():
        raise RuntimeError("Breeze TTS 服务未运行")
    results = tts_breeze.synthesize_batch(
        [text], str(spec.get("character") or "preview"), out_dir=out_wav.parent,
        voice_spec={"instruction": spec.get("instruction"), "seed": spec.get("seed"),
                    "cfg_scale": spec.get("cfg_scale")},
        ref_audio=spec.get("ref_audio"), ref_text=spec.get("ref_text") or "",
    )
    if not results:
        raise RuntimeError("Breeze 合成结果为空")
    utils.ffmpeg(["-y", "-hide_banner", "-loglevel", "error", "-i", str(results[0]["path"]),
                  "-ar", "48000", "-ac", "2", "-c:a", "pcm_s16le", str(out_wav)])


def _character_portrait_url(character: str) -> Optional[str]:
    base = _CHAR_ASSET_DIR / character
    if not base.is_dir():
        return None
    for name in _PORTRAIT_NAMES:
        path = base / name
        try:
            if path.is_file() and path.stat().st_size > 0:
                return f"/media/v5/characters/{character}/{name}"
        except OSError:
            continue
    return None


# ---------------------------------------------------------------------------
# 角色固定声音（voice library / voice lock）
# ---------------------------------------------------------------------------
@router.get("/voices")
def list_voices(with_preview: bool = Query(default=True, description="是否附带 preview_url")) -> dict:
    """列出声音库预设（contract/v5/voices.json + custom/voices.json）。

    V18 扩展：每个 preset 增加 `provider` 与 `preview_url`（tmp/v5_voice/<voice_id>.wav 存在时）。
    """
    from pipeline import v5_voices

    lib = v5_voices.load_voice_library()
    items = []
    for pid, spec in sorted((lib.get("presets") or {}).items()):
        items.append({
            "voice_id": pid,
            "label": spec.get("label"),
            "seed": spec.get("seed"),
            "cfg_scale": spec.get("cfg_scale"),
            "tags": spec.get("tags"),
            "provider": spec.get("provider"),
            "preview_url": _preset_preview_url(pid) if with_preview else None,
        })
    return {"ok": True, "count": len(items), "presets": items}


@router.get("/casting")
def get_casting(template: str = Query(default="", description="3D 模板 id（如 tonight_show / real_street）")):
    """模板 cast → 角色 + 解析后的音色 + 立绘 URL（V18 角色与声音页）。"""
    tid = str(template or "").strip()
    if not tid:
        raise HTTPException(status_code=400, detail="缺少 query 参数 template")
    catalog = _catalog()
    tpl = (catalog.get("templates") or {}).get(tid)
    if not isinstance(tpl, dict):
        raise HTTPException(status_code=404, detail=f"模板不存在: {tid}")
    characters = catalog.get("characters") or {}
    rows = []
    for entry in tpl.get("cast") or []:
        if not isinstance(entry, dict):
            continue
        cid = str(entry.get("character") or "").strip()
        char = characters.get(cid) if isinstance(characters.get(cid), dict) else {}
        row: Dict[str, Any] = {
            "role": entry.get("role"),
            "character": cid,
            "label": str(entry.get("name") or char.get("label") or char.get("name") or cid),
            "persona": str(char.get("persona") or ""),
            "voice": None,
            "portrait_url": _character_portrait_url(cid) if cid else None,
        }
        if char:
            try:
                spec = _effective_voice(char)
                row["voice"] = {
                    "voice_id": spec.get("voice_id"),
                    "label": spec.get("label") or str(spec.get("instruction") or "")[:40] or spec.get("voice_id"),
                    "provider": spec.get("provider"),
                    "seed": spec.get("seed"),
                    "rate": spec.get("rate"),
                }
            except Exception as exc:  # noqa: BLE001 - 单个角色声音解析失败不影响整页
                row["voice"] = {"voice_id": None, "label": "", "provider": None,
                                "error": str(exc)[:200]}
        rows.append(row)
    return {
        "ok": True,
        "template_id": tid,
        "label": tpl.get("label"),
        "style": tpl.get("style"),
        "count": len(rows),
        "cast": rows,
    }


@router.get("/voice/{character}")
def get_character_voice(character: str) -> dict:
    """读取角色当前固定声音（resolved + 锁状态；V18 支持 edge 写实角色）。"""
    from pipeline import v5_voices

    catalog = _catalog()
    char = (catalog.get("characters") or {}).get(character)
    if not char:
        raise HTTPException(status_code=404, detail=f"角色不存在: {character}")
    try:
        spec = _effective_voice(char)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=f"角色声音解析失败: {exc}") from exc
    info = v5_voices.lock_info(character, spec)
    return {"ok": True, "character": character, "voice_id": spec["voice_id"],
            "seed": spec.get("seed"), "cfg_scale": spec.get("cfg_scale"),
            "instruction": spec.get("instruction"), "key": spec["key"],
            "provider": spec.get("provider"), "label": spec.get("label"),
            "rate": spec.get("rate"),
            "ref_text": spec.get("ref_text") or "", "lock": info}


@router.post("/voice/{character}/lock")
def lock_character_voice(character: str, body: Dict[str, Any] = Body(default={})) -> dict:
    """生成/重建角色固定声音参考（需要 Breeze TTS 服务；首次会加载模型）。"""
    from pipeline import v5_voices

    catalog = _catalog()
    char = (catalog.get("characters") or {}).get(character)
    if not char:
        raise HTTPException(status_code=404, detail=f"角色不存在: {character}")
    try:
        spec = _effective_voice(char)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=f"角色声音解析失败: {exc}") from exc
    if str(spec.get("provider") or "").lower() == "edge":
        return {"ok": True, "character": character, "voice_id": spec["voice_id"],
                "key": spec["key"], "skipped": True,
                "reason": "edge 音色无需声音锁（只有 Breeze Voice Design 需要参考音频）"}
    try:
        lock = v5_voices.ensure_voice_lock(char, spec, force=bool(body.get("force")))
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=503, detail=f"声音锁生成失败: {exc}") from exc
    return {"ok": True, "character": character, "voice_id": spec["voice_id"],
            "key": spec["key"], "lock": lock}


@router.post("/voice/{character}/preview")
def preview_character_voice(character: str, body: Dict[str, Any] = Body(default={})) -> dict:
    """角色试听（V18）：breeze 优先（服务已运行才用，避免冷启动占 GPU），edge-tts 兜底。

    - 写 `tmp/v5_voice/<character>.wav`；相同文本/音色重复调用直接复用（cached=true）。
    - 返回 `{ok, character, url: "/media/v5/voice/<character>.wav?t=...", cached, engine}`。
    """
    catalog = _catalog()
    char = (catalog.get("characters") or {}).get(character)
    if not isinstance(char, dict):
        raise HTTPException(status_code=404, detail=f"角色不存在: {character}")
    payload = body if isinstance(body, dict) else {}
    text = str(payload.get("text") or "").strip() or _default_preview_text(character, char)
    if len(text) > 200:
        raise HTTPException(status_code=400, detail="试听文本过长（最多 200 字）")
    try:
        spec = _effective_voice(char)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=f"角色声音解析失败: {exc}") from exc

    # 可选：用指定 preset 试听（音色库「试听」按钮）——结果单独缓存为 <character>__<voice_id>.wav
    requested_voice = str(payload.get("voice_id") or "").strip()
    preview_name = character
    if requested_voice:
        from pipeline import v5_voices  # noqa: PLC0415

        try:
            base_spec = dict(v5_voices.preset(v5_voices.load_voice_library(), requested_voice))
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=400, detail=f"未知 voice_id: {requested_voice}（{exc}）") from exc
        base_spec.update({"character": character, "voice_id": requested_voice,
                          "provider": base_spec.get("provider") or "breeze",
                          "rate": str(base_spec.get("rate") or "+0%")})
        base_spec["key"] = v5_voices.voice_key(base_spec)
        spec = base_spec
        preview_name = f"{character}__{requested_voice}"

    _VOICE_DIR.mkdir(parents=True, exist_ok=True)
    wav_path = _preview_wav_path(preview_name)
    meta_path = _preview_meta_path(preview_name)

    edge_rate = str(spec.get("rate") or "+0%")
    engine = "edge"
    if str(spec.get("provider") or "").lower() != "edge":
        try:
            from pipeline import tts_breeze  # noqa: PLC0415

            if tts_breeze.server_alive():
                engine = "breeze"
        except Exception:  # noqa: BLE001 - breeze 不可用时直接走 edge
            engine = "edge"

    cache_key = hashlib.sha1(json.dumps(
        {"text": text, "engine": engine, "voice_id": spec.get("voice_id"), "key": spec.get("key")},
        ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
    meta: Dict[str, Any] = {}
    try:
        if meta_path.is_file():
            raw_meta = json.loads(meta_path.read_text(encoding="utf-8"))
            meta = raw_meta if isinstance(raw_meta, dict) else {}
    except (OSError, ValueError):
        meta = {}
    try:
        wav_ready = wav_path.is_file() and wav_path.stat().st_size > 1000
    except OSError:
        wav_ready = False
    if wav_ready and meta.get("cache_key") == cache_key:
        return {"ok": True, "character": character, "url": _preview_url_for(preview_name),
                "cached": True, "engine": meta.get("engine", engine),
                "duration": meta.get("duration"), "text": meta.get("text", text),
                "voice_id": spec.get("voice_id"), "requested_voice_id": requested_voice or None,
                "out": str(wav_path)}

    breeze_error = ""

    def _synthesize() -> str:
        nonlocal breeze_error
        if engine == "breeze":
            try:
                _synth_breeze(spec, text, wav_path)
                return "breeze"
            except Exception as exc:  # noqa: BLE001 - 失败回退 edge
                breeze_error = str(exc)[:300]
        _synth_edge(text, _edge_voice_for(spec), edge_rate, wav_path)
        return "edge"

    executor = ThreadPoolExecutor(max_workers=1)
    try:
        future = executor.submit(_synthesize)
        used_engine = future.result(timeout=_PREVIEW_TIMEOUT_SECONDS)
    except FutureTimeout as exc:
        raise HTTPException(status_code=504,
                            detail=f"试听合成超时（>{int(_PREVIEW_TIMEOUT_SECONDS)}s）") from exc
    except Exception as exc:  # noqa: BLE001
        detail = f"试听合成失败: {exc}"
        if breeze_error:
            detail += f"（breeze 失败已回退 edge：{breeze_error}）"
        raise HTTPException(status_code=503, detail=detail) from exc
    finally:
        executor.shutdown(wait=False)

    duration = _wav_duration(wav_path)
    try:
        wav_size = wav_path.stat().st_size
    except OSError:
        wav_size = 0
    if wav_size <= 1000:
        raise HTTPException(status_code=503, detail="试听合成产物为空")
    meta = {"cache_key": cache_key, "engine": used_engine, "text": text,
            "voice_id": spec.get("voice_id"), "key": spec.get("key"),
            "duration": duration, "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "breeze_error": breeze_error or None}
    try:
        meta_path.write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass
    return {"ok": True, "character": character, "url": _preview_url_for(preview_name),
            "cached": False, "engine": used_engine, "duration": duration,
            "text": text, "voice_id": spec.get("voice_id"),
            "requested_voice_id": requested_voice or None, "out": str(wav_path)}

