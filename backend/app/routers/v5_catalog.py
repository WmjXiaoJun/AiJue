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

import sys
from pathlib import Path
from typing import Any, Dict, Optional

from fastapi import APIRouter, Body, HTTPException, Query, Response

# 允许以 `uvicorn app.main:app --app-dir backend` 启动时也能 import 仓库根下的 pipeline.*
_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from pipeline import v5_catalog  # noqa: E402

router = APIRouter(prefix="/api/v5", tags=["v5-contract"])

KIND_CHOICES = "、".join(v5_catalog.KINDS)


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
    return summary


def _source_of(catalog: dict, kind: str, item_id: str) -> dict:
    return v5_catalog.item_source(catalog, kind, item_id)


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
    return {
        "ok": True,
        "kind": current,
        "id": item_id,
        "item": item,
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
# 角色固定声音（voice library / voice lock）
# ---------------------------------------------------------------------------
@router.get("/voices")
def list_voices() -> dict:
    """列出声音库预设（contract/v5/voices.json + custom/voices.json）。"""
    from pipeline import v5_voices
    lib = v5_voices.load_voice_library()
    return {"ok": True, "count": len(lib.get("presets") or {}),
            "presets": [{"voice_id": pid, **{k: spec.get(k) for k in
                                            ("label", "seed", "cfg_scale", "tags")}}
                        for pid, spec in sorted((lib.get("presets") or {}).items())]}


@router.get("/voice/{character}")
def get_character_voice(character: str) -> dict:
    """读取角色当前固定声音（resolved）与声音锁状态。"""
    from pipeline import v5_voices
    catalog = _catalog()
    char = (catalog.get("characters") or {}).get(character)
    if not char:
        raise HTTPException(status_code=404, detail=f"角色不存在: {character}")
    spec = v5_voices.resolve_voice(char, v5_voices.load_voice_library())
    info = v5_voices.lock_info(character, spec)
    return {"ok": True, "character": character, "voice_id": spec["voice_id"],
            "seed": spec["seed"], "cfg_scale": spec["cfg_scale"],
            "instruction": spec.get("instruction"), "key": spec["key"],
            "ref_text": spec.get("ref_text") or "", "lock": info}


@router.post("/voice/{character}/lock")
def lock_character_voice(character: str, body: Dict[str, Any] = Body(default={})) -> dict:
    """生成/重建角色固定声音参考（需要 Breeze TTS 服务；首次会加载模型）。"""
    from pipeline import v5_voices
    catalog = _catalog()
    char = (catalog.get("characters") or {}).get(character)
    if not char:
        raise HTTPException(status_code=404, detail=f"角色不存在: {character}")
    spec = v5_voices.resolve_voice(char, v5_voices.load_voice_library())
    try:
        lock = v5_voices.ensure_voice_lock(char, spec, force=bool(body.get("force")))
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=503, detail=f"声音锁生成失败: {exc}") from exc
    return {"ok": True, "character": character, "voice_id": spec["voice_id"],
            "key": spec["key"], "lock": lock}


@router.post("/voice/{character}/preview")
def preview_character_voice(character: str, body: Dict[str, Any] = Body(default={})) -> dict:
    """试听角色声音（返回 wav 文件）。"""
    from pipeline import v5_voices
    catalog = _catalog()
    char = (catalog.get("characters") or {}).get(character)
    if not char:
        raise HTTPException(status_code=404, detail=f"角色不存在: {character}")
    text = str(body.get("text") or "").strip() or \
        f"大家好，我是{char.get('label') or character}。这是我的固定声音。"
    out = v5_voices.REPO / "tmp" / "v5_voice" / f"{character}.wav"
    out.parent.mkdir(parents=True, exist_ok=True)
    try:
        res = v5_voices.preview(char, text, out)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=503, detail=f"试听失败: {exc}") from exc
    data = out.read_bytes()
    return {"ok": True, **res, "wav_base64_len": len(data)}

