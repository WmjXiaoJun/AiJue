# -*- coding: utf-8 -*-
"""V5 目录 / 契约层：加载内建 + custom 覆盖、校验（JSON Schema + 引用完整性）、导出。

目录（`contract/v5/`，可用环境变量 `V5_CONTRACT_ROOT` 或 root= 覆盖）：
```
styles/{id}.json scenes/{id}.json characters/{id}.json props/{id}.json
templates/{id}.json packs/{id}.json schema/*.schema.json
custom/{kind}/{id}.json     # 用户覆盖：深合并（字典递归、数组替换），优先级最高
```
约定：
- 所有 id 小写开头，`^[a-z][a-z0-9_]{1,60}$`；用户新建对象用 `custom_` 前缀，
  覆盖内建对象时直接同名（如 custom/characters/laozhou.json 只写 `{"voice": {...}}`）。
- `load_catalog()` 永不因单个坏文件崩溃：解析错误进 `catalog["errors"]`，由 validate 报出。
- `validate_catalog()` 返回 `{"ok","errors","warnings","stats"}`；`--strict` 时 warning 也算失败。
- 不依赖 GPU/网络；除 `jsonschema`（校验）与可选 `PIL`（PNG alpha 资源检查）外只用标准库。

CLI 见 `pipeline/v5_cli.py`；后端见 `backend/app/routers/v5_catalog.py`。
"""
from __future__ import annotations

import copy
import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any

__all__ = [
    "V5Error",
    "CatalogError",
    "KINDS",
    "SCHEMA_KINDS",
    "SINGULAR",
    "DEFAULT_ROOT",
    "catalog_root",
    "normalize_kind",
    "deep_merge",
    "load_catalog",
    "load_schema",
    "schema_errors",
    "validate_catalog",
    "validate_item",
    "validate_timeline",
    "validate_timeline_file",
    "load_timeline_schema",
    "export_custom",
    "catalog_summary",
    "summarize_item",
    "item_source",
    "custom_path",
    "write_json_atomic",
]

# 契约根目录：仓库/contract/v5（可用环境变量覆盖，便于测试/多租户）
DEFAULT_ROOT = Path(__file__).resolve().parents[1] / "contract" / "v5"
ROOT_ENV = "V5_CONTRACT_ROOT"

KINDS = ("styles", "scenes", "characters", "props", "templates", "packs")
SCHEMA_KINDS = ("styles", "scenes", "characters", "props", "templates", "packs")
SINGULAR = {
    "style": "styles",
    "scene": "scenes",
    "character": "characters",
    "char": "characters",
    "prop": "props",
    "template": "templates",
    "pack": "packs",
}
ID_RE = re.compile(r"^[a-z][a-z0-9_]{1,60}$")
ROLE_RE = re.compile(r"^[a-z][a-z0-9_]{0,30}$")
HEX_RE = re.compile(r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})$")
CUSTOM_PREFIX = "custom_"

try:  # jsonschema 可选：缺失时 validate 给出 warning（CI 环境有）
    from jsonschema import Draft202012Validator

    _HAS_JSONSCHEMA = True
except Exception:  # noqa: BLE001 - 只在缺失时降级
    Draft202012Validator = None  # type: ignore[assignment]
    _HAS_JSONSCHEMA = False


class V5Error(ValueError):
    """V5 契约层错误（加载/导出失败、非法 kind/id 等）。"""


# 兼容别名（接口文档中其他模块也叫 V5Error）
CatalogError = V5Error


# ---------------------------------------------------------------------------
# 基础工具
# ---------------------------------------------------------------------------
def catalog_root(root: Any = None) -> Path:
    """解析契约根目录：root 参数 > 环境变量 V5_CONTRACT_ROOT > 仓库 contract/v5。"""
    if root is not None:
        return Path(root)
    env = os.environ.get(ROOT_ENV)
    if env:
        return Path(env)
    return DEFAULT_ROOT


def normalize_kind(kind: Any) -> str:
    """kind 归一化：styles/style/STYLES → styles；未知 kind raise V5Error。"""
    text = str(kind or "").strip().lower()
    if text in KINDS:
        return text
    if text in SINGULAR:
        return SINGULAR[text]
    raise V5Error(f"未知 kind: {kind!r}（合法值: {', '.join(KINDS)} 或其单数）")


def deep_merge(base: Any, override: Any) -> Any:
    """深合并：dict 递归合并，list/标量由 override 整体替换（契约规则）。"""
    if isinstance(base, dict) and isinstance(override, dict):
        merged = copy.deepcopy(base)
        for key, value in override.items():
            if key in merged:
                merged[key] = deep_merge(merged[key], value)
            else:
                merged[key] = copy.deepcopy(value)
        return merged
    return copy.deepcopy(override)


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise V5Error(f"无法读取 {path}: {exc}") from exc
    except ValueError as exc:
        raise V5Error(f"JSON 非法 {path}: {exc}") from exc


def write_json_atomic(path: Path | str, data: Any) -> Path:
    """原子写 JSON（UTF-8 / ensure_ascii=False / 2 空格缩进）。"""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
        fh.flush()
        os.fsync(fh.fileno())
    tmp.replace(path)
    return path


def _id_of(data: Any, fallback: str = "") -> str:
    if isinstance(data, dict):
        value = data.get("id")
        if isinstance(value, str) and value.strip():
            return value.strip()
    return fallback


def _sources_entry(path: Path, kind: str, *, custom: bool,
                   base: str | None = None) -> dict:
    entry = {"path": str(path), "custom": custom, "kind": kind}
    if base:
        entry["base"] = base
    return entry


# ---------------------------------------------------------------------------
# 加载
# ---------------------------------------------------------------------------
def _empty_catalog(root: Path) -> dict:
    catalog: dict = {
        "version": 1,
        "root": str(root),
        "custom_root": str(root / "custom"),
        "sources": {kind: {} for kind in KINDS},
        "errors": [],
    }
    for kind in KINDS:
        catalog[kind] = {}
    return catalog


def _load_dir(catalog: dict, base_dir: Path, kind: str, *, custom: bool) -> None:
    """读一个目录下所有 *.json，合并进 catalog（custom 深合并、内建/重复报错）。"""
    if not base_dir.is_dir():
        return
    for path in sorted(base_dir.glob("*.json")):
        try:
            data = _read_json(path)
        except V5Error as exc:
            catalog["errors"].append(f"{kind}/{path.name}: {exc}")
            continue
        if not isinstance(data, dict):
            catalog["errors"].append(
                f"{kind}/{path.name}: 根节点必须是 JSON object（实际 {type(data).__name__}）")
            continue
        item_id = _id_of(data, path.stem)
        if not item_id:
            catalog["errors"].append(f"{kind}/{path.name}: 缺少 id（且无法用文件名兜底）")
            continue
        if custom:
            existed = item_id in catalog[kind]
            if existed:
                base_path = catalog["sources"][kind].get(item_id, {}).get("path", "")
                catalog[kind][item_id] = deep_merge(catalog[kind][item_id], data)
                # 覆盖片段可能省略 id；合并后保证 id 正确
                if isinstance(catalog[kind][item_id], dict):
                    catalog[kind][item_id]["id"] = item_id
                catalog["sources"][kind][item_id] = _sources_entry(
                    path, kind, custom=True, base=base_path or None)
            else:
                catalog["sources"][kind][item_id] = _sources_entry(path, kind, custom=True)
                catalog[kind][item_id] = copy.deepcopy(data)
        else:
            if item_id in catalog[kind]:
                catalog["errors"].append(
                    f"{kind}/{item_id}: id 重复（{catalog['sources'][kind][item_id]['path']} "
                    f"与 {path}）")
                catalog[kind][item_id] = deep_merge(catalog[kind][item_id], data)
            else:
                catalog[kind][item_id] = copy.deepcopy(data)
            catalog["sources"][kind][item_id] = _sources_entry(path, kind, custom=False)


def load_catalog(root: Any = None) -> dict:
    """加载内建 + custom 覆盖后的完整目录。

    返回 {"styles","scenes","characters","props","templates","packs"}（dict: id → item）
    + "sources"（每 kind: id → {path, custom, kind, base?}）+ "root"/"custom_root"
    + "errors"（坏文件/重复 id，交给 validate_catalog 报出）+ "counts"。
    """
    root_path = catalog_root(root)
    catalog = _empty_catalog(root_path)
    for kind in KINDS:
        _load_dir(catalog, root_path / kind, kind, custom=False)
    for kind in KINDS:
        _load_dir(catalog, root_path / "custom" / kind, kind, custom=True)
    catalog["counts"] = {kind: len(catalog[kind]) for kind in KINDS}
    catalog["custom_counts"] = {
        kind: sum(1 for src in catalog["sources"][kind].values() if src.get("custom"))
        for kind in KINDS
    }
    return catalog


# ---------------------------------------------------------------------------
# JSON Schema
# ---------------------------------------------------------------------------
_SCHEMA_CACHE: dict[tuple[str, float], dict] = {}


def load_schema(kind: Any, root: Any = None) -> dict | None:
    """读 contract/v5/schema/{kind}.schema.json；不存在返回 None。"""
    kind = normalize_kind(kind)
    if kind not in SCHEMA_KINDS:
        return None
    path = catalog_root(root) / "schema" / f"{kind[:-1] if kind.endswith('s') else kind}.schema.json"
    # styles→style.schema.json / scenes→scene…（文件名用单数）
    if not path.is_file():
        singular = kind[:-1]
        alt = catalog_root(root) / "schema" / f"{singular}.schema.json"
        if not alt.is_file():
            return None
        path = alt
    return _load_schema_file(path)


def _load_schema_file(path: Path) -> dict | None:
    """读 + 缓存任意 JSON Schema 文件（按 mtime 失效）。"""
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return None
    key = (str(path), mtime)
    cached = _SCHEMA_CACHE.get(key)
    if cached is not None:
        return copy.deepcopy(cached)
    try:
        schema = _read_json(path)
    except V5Error:
        return None
    if not isinstance(schema, dict):
        return None
    _SCHEMA_CACHE[key] = schema
    return copy.deepcopy(schema)


def load_timeline_schema(root: Any = None) -> dict | None:
    """render_v5 产物 timeline.json 的 schema（schema/timeline.schema.json）。"""
    return _load_schema_file(catalog_root(root) / "schema" / "timeline.schema.json")


def _json_path(parts) -> str:
    text = "/".join(str(p) for p in parts)
    return f"/{text}" if text else "$"


def _schema_errors_for(schema: dict, item: Any) -> list[str]:
    if not _HAS_JSONSCHEMA or not isinstance(item, dict):
        return []
    validator = Draft202012Validator(schema)
    errors: list[str] = []
    for error in sorted(validator.iter_errors(item), key=lambda e: list(e.absolute_path)):
        field = _json_path(error.absolute_path)
        message = error.message.replace("\n", " ")
        errors.append(f"{field}: {message}")
    return errors


def schema_errors(kind: Any, item: Any, root: Any = None) -> list[str]:
    """用对应 schema 校验单个 item，返回 "field: message" 列表（无 schema/无 jsonschema 时 []）。"""
    if not _HAS_JSONSCHEMA:
        return []
    kind = normalize_kind(kind)
    schema = load_schema(kind, root)
    if schema is None:
        return []
    return _schema_errors_for(schema, item)


# ---------------------------------------------------------------------------
# 校验
# ---------------------------------------------------------------------------
_HEX_KEYS = ("color", "skin", "hair", "eye")
_COLOR_OBJ_KEYS = ("main", "accent", "shirt", "pants", "shoes", "bg", "fg", "paper")


def _check_hex_values(kind: str, item_id: str, item: dict, warnings: list[str],
                      file_hint: str = "") -> None:
    """palette 里每个 hex 值做格式检查（schema 之外再兜一层友好提示）。"""
    palette = item.get("palette") if isinstance(item, dict) else None
    if isinstance(palette, dict):
        for key, value in palette.items():
            values = value if isinstance(value, list) else [value]
            for entry in values:
                if isinstance(entry, str) and not HEX_RE.match(entry):
                    warnings.append(
                        f"{kind}/{item_id}: palette.{key}={entry!r} 不是 #RRGGBB 颜色 "
                        f"[{file_hint or kind}]")


def _resolve_resource(value: str, root: Path) -> Path:
    path = Path(str(value)).expanduser()
    if path.is_absolute():
        return path
    candidates = [root / path, root.parents[1] / path, Path.cwd() / path]
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return candidates[1] if len(candidates) > 1 else candidates[0]


def _check_resource(kind: str, item_id: str, field: str, value: Any, root: Path,
                    errors: list[str], file_hint: str, *,
                    require_alpha: bool = False) -> None:
    if not isinstance(value, str) or not value.strip():
        errors.append(f"{kind}/{item_id}: {field} 必须是非空路径字符串 [{file_hint}]")
        return
    path = _resolve_resource(value, root)
    if not path.is_file():
        errors.append(f"{kind}/{item_id}: {field} 资源不存在: {value}（已尝试仓库根/contract/v5）")
        return
    if require_alpha and path.suffix.lower() in (".png", ".webp"):
        try:
            from PIL import Image

            with Image.open(path) as image:
                mode = image.mode
            if "A" not in mode and mode not in ("LA", "PA"):
                errors.append(
                    f"{kind}/{item_id}: {field} 需要带 alpha 的透明图，实际 {path.name} mode={mode} "
                    f"（可在字段 adjacent 写 image_alpha=false 关闭检查）")
        except Exception as exc:  # noqa: BLE001 - 资源不可读也算错误
            errors.append(f"{kind}/{item_id}: {field} 图片无法读取: {path} ({exc})")


def _iter_refs(item: dict, field: str) -> list:
    value = item.get(field)
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def _ref(errors: list[str], kind: str, owner: str, field: str, ref: Any,
         table: dict) -> None:
    if not isinstance(ref, str) or not ref:
        errors.append(f"{owner}: {field} 引用必须是非空字符串（实际 {ref!r}）")
    elif ref not in table:
        errors.append(f"{owner}: {field}={ref!r} 不存在于 {kind}/（先创建或修正引用）")


def _check_style_refs(item_id: str, style: dict, tables: dict, errors: list[str],
                      warnings: list[str]) -> None:
    if not isinstance(style, dict):
        return
    if style.get("extends"):
        _ref(errors, "styles", f"styles/{item_id}", "extends", style.get("extends"),
             tables["styles"])


def _check_scene_refs(item_id: str, scene: dict, tables: dict, errors: list[str],
                      warnings: list[str]) -> None:
    if not isinstance(scene, dict):
        return
    for prop_id in _iter_refs(scene, "props"):
        _ref(errors, "props", f"scenes/{item_id}", "props[]", prop_id, tables["props"])


def _check_character_refs(item_id: str, char: dict, tables: dict,
                          errors: list[str], warnings: list[str]) -> None:
    if not isinstance(char, dict):
        return
    overrides = char.get("style_overrides")
    if isinstance(overrides, dict):
        for style_id in overrides:
            _ref(errors, "styles", f"characters/{item_id}", "style_overrides",
                 style_id, tables["styles"])


def _check_template_refs(item_id: str, template: dict, tables: dict,
                         errors: list[str], warnings: list[str]) -> None:
    if not isinstance(template, dict):
        return
    owner = f"templates/{item_id}"
    style_id = template.get("style")
    if style_id is not None:
        _ref(errors, "styles", owner, "style", style_id, tables["styles"])
    overrides = template.get("style_overrides")
    if isinstance(overrides, dict):
        for style_id in overrides:
            _ref(errors, "styles", owner, "style_overrides", style_id, tables["styles"])
    cast = template.get("cast") if isinstance(template.get("cast"), list) else []
    cast_roles: set[str] = set()
    for index, member in enumerate(cast):
        if not isinstance(member, dict):
            errors.append(f"{owner}: cast[{index}] 必须是 object")
            continue
        role = str(member.get("role") or "")
        if role in cast_roles:
            errors.append(f"{owner}: cast[{index}].role={role!r} 重复（cast role 必须唯一）")
        cast_roles.add(role)
        _ref(errors, "characters", owner, f"cast[{index}].character",
             member.get("character"), tables["characters"])
        for prop_id in _iter_refs(member, "props"):
            _ref(errors, "props", owner, f"cast[{index}].props[]", prop_id, tables["props"])
    acts = template.get("acts") if isinstance(template.get("acts"), list) else []
    for index, act in enumerate(acts):
        if not isinstance(act, dict):
            errors.append(f"{owner}: acts[{index}] 必须是 object")
            continue
        act_id = act.get("id") or index
        act_owner = f"{owner}.acts[{act_id}]"
        _ref(errors, "scenes", act_owner, "scene", act.get("scene"), tables["scenes"])
        line = act.get("line")
        if isinstance(line, str):
            if line and line not in cast_roles:
                errors.append(
                    f"{act_owner}: line={line!r} 不是本模板 cast role "
                    f"{sorted(cast_roles)}（无台词请写空字符串）")
        elif line is not None:
            errors.append(f"{act_owner}: line 必须是字符串（cast role 或 \"\"）")
        for ref in _iter_refs(act, "characters"):
            if not isinstance(ref, str) or (ref not in cast_roles
                                            and ref not in tables["characters"]):
                errors.append(f"{act_owner}: characters[]={ref!r} 既不是 cast role "
                              f"也不是 characters/ 中的 id")
        for prop_id in _iter_refs(act, "props"):
            _ref(errors, "props", act_owner, "props[]", prop_id, tables["props"])


def _check_pack_refs(item_id: str, pack: dict, tables: dict, errors: list[str],
                     warnings: list[str]) -> None:
    if not isinstance(pack, dict):
        return
    owner = f"packs/{item_id}"
    mapping = (("styles", "styles"), ("scenes", "scenes"),
               ("characters", "characters"), ("props", "props"),
               ("templates", "templates"))
    for field, kind in mapping:
        for ref in _iter_refs(pack, field):
            _ref(errors, kind, owner, f"{field}[]", ref, tables[field])


_REF_CHECKERS = {
    "styles": _check_style_refs,
    "scenes": _check_scene_refs,
    "characters": _check_character_refs,
    "templates": _check_template_refs,
    "packs": _check_pack_refs,
}


def _tables(catalog: dict) -> dict:
    return {kind: (catalog.get(kind) or {}) for kind in KINDS}


def _validate_refs(catalog: dict, errors: list[str], warnings: list[str]) -> None:
    tables = _tables(catalog)
    for kind, checker in _REF_CHECKERS.items():
        for item_id, item in (catalog.get(kind) or {}).items():
            if isinstance(item, dict):
                checker(item_id, item, tables, errors, warnings)


def _validate_custom_ids(catalog: dict, errors: list[str], warnings: list[str]) -> None:
    """custom 文件命名/id 规则：id 与文件名一致；新对象必须 custom_ 前缀（覆盖内建除外）。"""
    for kind in KINDS:
        sources = catalog.get("sources", {}).get(kind, {})
        for item_id, src in sources.items():
            path = Path(str(src.get("path") or ""))
            if path.name and path.stem != item_id:
                errors.append(
                    f"{kind}/{item_id}: 文件名必须与 id 一致（文件 {path.name}，id {item_id}）")
            if src.get("custom"):
                if not (item_id.startswith(CUSTOM_PREFIX) or src.get("base")):
                    errors.append(
                        f"{kind}/{item_id}: custom 新建对象的 id 必须以 {CUSTOM_PREFIX!r} 开头，"
                        f"或与内建 id 同名以便覆盖（文件 {path}）")
            elif item_id.startswith(CUSTOM_PREFIX):
                warnings.append(
                    f"{kind}/{item_id}: 内建目录里的 custom_ 前缀对象建议挪到 custom/{kind}/")


def _validate_resources(catalog: dict, errors: list[str]) -> None:
    root = Path(catalog.get("root") or DEFAULT_ROOT)
    for item_id, char in (catalog.get("characters") or {}).items():
        src = (catalog.get("sources", {}).get("characters", {}).get(item_id) or {})
        hint = str(src.get("path") or "")
        image = char.get("image")
        if image:
            require_alpha = bool(char.get("image_alpha", True))
            _check_resource("characters", item_id, "image", image, root, errors, hint,
                            require_alpha=require_alpha)
    for item_id, scene in (catalog.get("scenes") or {}).items():
        src = (catalog.get("sources", {}).get("scenes", {}).get(item_id) or {})
        hint = str(src.get("path") or "")
        image = scene.get("background_image")
        if image:
            _check_resource("scenes", item_id, "background_image", image, root, errors, hint)


def _scene_primitives() -> set[str] | None:
    """若 scene_render 已实现并暴露注册表，返回 primitive 名集合；否则 None（跳过检查）。"""
    try:
        from . import scene_render  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001 - 模块未就绪时跳过（不阻塞契约校验）
        return None
    names: set[str] = set()
    for attr in ("PRIMITIVES", "_PRIMITIVES", "REGISTRY", "_REGISTRY"):
        table = getattr(scene_render, attr, None)
        if isinstance(table, dict):
            names |= {str(k) for k in table}
        elif isinstance(table, (set, list, tuple)):
            names |= {str(k) for k in table}
    return names or None


def _warn_recommended(catalog: dict, warnings: list[str]) -> None:
    for item_id, style in (catalog.get("styles") or {}).items():
        h3 = style.get("h3") if isinstance(style.get("h3"), dict) else {}
        if not h3.get("style_prompt"):
            warnings.append(f"styles/{item_id}: 建议填写 h3.style_prompt（H3/图像生成用）")
        if not style.get("character_treatment"):
            warnings.append(f"styles/{item_id}: 建议填写 character_treatment（角色风格化处理）")
    for item_id, scene in (catalog.get("scenes") or {}).items():
        if not scene.get("camera_zones"):
            warnings.append(f"scenes/{item_id}: 建议填写 camera_zones（机位取景窗）")
        if not scene.get("anchors"):
            warnings.append(f"scenes/{item_id}: 建议填写 anchors（角色脚底锚点）")
    for item_id, char in (catalog.get("characters") or {}).items():
        if not char.get("poses"):
            warnings.append(f"characters/{item_id}: 建议填写 poses（front/side/point…）")
        if not char.get("mouth"):
            warnings.append(f"characters/{item_id}: 建议填写 mouth（viseme 帧列表）")
        if not char.get("persona"):
            warnings.append(f"characters/{item_id}: 建议填写 persona（LLM 台本用）")
    for item_id, template in (catalog.get("templates") or {}).items():
        if not template.get("packaging"):
            warnings.append(f"templates/{item_id}: 建议填写 packaging（字幕/转场/片头）")
    for kind in KINDS:
        if not catalog.get(kind):
            warnings.append(f"{kind}/ 为空（等待实体写入，或检查目录/文件名）")


def validate_catalog(catalog: dict | None = None, strict: bool = False,
                     root: Any = None) -> dict:
    """校验整个目录：JSON Schema + 引用完整性 + custom id 规则 + 资源存在性。

    catalog=None 时自动 load_catalog(root)。返回 {"ok","errors","warnings","stats","strict"}。
    ok = 无 errors（strict=True 时还要求无 warnings）。
    """
    if not isinstance(catalog, dict) or "sources" not in catalog:
        catalog = load_catalog(root if root is not None else catalog)
    errors: list[str] = list(catalog.get("errors") or [])
    warnings: list[str] = []
    catalog_root_path = Path(str(catalog.get("root") or catalog_root(root)))
    if not catalog_root_path.is_dir():
        errors.append(f"契约根目录不存在: {catalog_root_path}"
                      f"（检查 --root / V5_CONTRACT_ROOT）")

    if not _HAS_JSONSCHEMA:
        warnings.append("jsonschema 未安装：跳过 JSON Schema 校验（pip install jsonschema）")
    for kind in SCHEMA_KINDS:
        schema = load_schema(kind, catalog_root_path)
        if schema is None:
            warnings.append(f"schema/{kind[:-1]}.schema.json 缺失，跳过 {kind} 的 JSON Schema 校验")
            continue
        for item_id, item in sorted((catalog.get(kind) or {}).items()):
            if not isinstance(item, dict):
                errors.append(f"{kind}/{item_id}: 必须是 JSON object")
                continue
            for message in schema_errors(kind, item, catalog_root_path):
                src = (catalog.get("sources", {}).get(kind, {}).get(item_id) or {})
                errors.append(f"{kind}/{item_id}: {message} [{src.get('path') or kind}]")

    _validate_custom_ids(catalog, errors, warnings)
    _validate_refs(catalog, errors, warnings)
    _validate_resources(catalog, errors)
    _warn_recommended(catalog, warnings)
    # 可选：对照 scene_render 的 primitive 注册表检查 layers[].type（聚合成 1 条 warning，避免刷屏）
    primitives = _scene_primitives()
    if primitives is not None:
        missing: dict[str, int] = {}
        for item_id, scene in (catalog.get("scenes") or {}).items():
            layers = scene.get("layers") if isinstance(scene, dict) else None
            for layer in layers or []:
                if isinstance(layer, dict):
                    layer_type = str(layer.get("type") or "")
                    if layer_type and layer_type not in primitives:
                        missing[layer_type] = missing.get(layer_type, 0) + 1
        if missing:
            top = sorted(missing.items(), key=lambda kv: (-kv[1], kv[0]))[:10]
            detail = "、".join(f"{name}×{count}" for name, count in top)
            warnings.append(
                f"scenes/: {len(missing)} 个 layer type 未在 scene_render 注册"
                f"（共 {sum(missing.values())} 层，Top: {detail}）——"
                f"请 scene_render 注册这些 primitive，或场景改用已注册名")
    for item_id, style in (catalog.get("styles") or {}).items():
        if isinstance(style, dict):
            src = (catalog.get("sources", {}).get("styles", {}).get(item_id) or {})
            _check_hex_values("styles", item_id, style, warnings, str(src.get("path") or ""))

    stats = {
        "root": str(catalog_root_path),
        "counts": {kind: len(catalog.get(kind) or {}) for kind in KINDS},
        "custom_counts": {
            kind: sum(1 for src in (catalog.get("sources", {}).get(kind) or {}).values()
                      if src.get("custom"))
            for kind in KINDS
        },
        "schemas": [kind for kind in SCHEMA_KINDS
                    if load_schema(kind, catalog_root_path) is not None],
        "error_count": len(errors),
        "warning_count": len(warnings),
    }
    ok = not errors and (not strict or not warnings)
    return {"ok": bool(ok), "errors": errors, "warnings": warnings,
            "stats": stats, "strict": bool(strict)}


def validate_item(kind: Any, item: Any, catalog: dict | None = None, *,
                  as_custom: bool = True) -> dict:
    """校验单个对象（API PUT 保存前用）：schema + id 规则 + 引用 + 资源。

    as_custom=True 时按 custom 覆盖规则检查 id（custom_ 前缀或覆盖内建）；
    对已存在的内建对象，会把 item 深合并到基线后再校验（支持 partial 覆盖片段）。
    """
    try:
        kind = normalize_kind(kind)
    except V5Error as exc:
        return {"ok": False, "errors": [str(exc)], "warnings": [], "kind": str(kind)}
    if not isinstance(item, dict):
        return {"ok": False, "errors": ["对象必须是 JSON object"], "warnings": [],
                "kind": kind}
    base_catalog = catalog if isinstance(catalog, dict) and "sources" in catalog \
        else load_catalog()
    root = Path(str(base_catalog.get("root") or DEFAULT_ROOT))
    item = copy.deepcopy(item)
    item_id = _id_of(item)
    errors: list[str] = []
    warnings: list[str] = []
    owner = f"{kind}/{item_id or '<新对象>'}"

    # ---- id ----
    if not item_id:
        errors.append(f"{owner}: 缺少 id（文件名必须与 id 一致）")
    elif not ID_RE.match(item_id):
        errors.append(f"{owner}: id={item_id!r} 格式非法（^[a-z][a-z0-9_]{{1,60}}$）")
    elif as_custom:
        builtin_ids = {
            iid for iid, src in (base_catalog.get("sources", {}).get(kind, {}) or {}).items()
            if not src.get("custom")
        }
        if not (item_id.startswith(CUSTOM_PREFIX) or item_id in builtin_ids
                or item_id in (base_catalog.get(kind) or {})):
            errors.append(f"{owner}: custom 新建对象 id 必须以 {CUSTOM_PREFIX!r} 开头"
                          f"，或与内建 id 同名以便覆盖")

    # ---- 合并基线（partial 覆盖也能通过 schema/引用校验） ----
    existing = (base_catalog.get(kind) or {}).get(item_id) if item_id else None
    is_override = isinstance(existing, dict) and as_custom
    if is_override:
        item_for_checks = deep_merge(existing, item)
        item_for_checks["id"] = item_id
    else:
        item_for_checks = item
        if item_id and "id" not in item_for_checks:
            item_for_checks["id"] = item_id

    # ---- JSON Schema ----
    if kind in SCHEMA_KINDS:
        for message in schema_errors(kind, item_for_checks, root):
            errors.append(f"{owner}: {message}")

    # ---- 引用完整性（只检查本对象） ----
    tables = _tables(base_catalog)
    tables[kind] = dict(tables.get(kind) or {})
    if item_id:
        tables[kind][item_id] = item_for_checks
    checker = _REF_CHECKERS.get(kind)
    if checker is not None:
        checker(item_id or "<新对象>", item_for_checks, tables, errors, warnings)

    # ---- 资源存在性（只检查本对象） ----
    if kind == "characters" and item_for_checks.get("image"):
        _check_resource("characters", item_id or "<新对象>", "image",
                        item_for_checks.get("image"), root, errors, "PUT",
                        require_alpha=bool(item_for_checks.get("image_alpha", True)))
    if kind == "scenes" and item_for_checks.get("background_image"):
        _check_resource("scenes", item_id or "<新对象>", "background_image",
                        item_for_checks.get("background_image"), root, errors, "PUT")

    return {"ok": not errors, "errors": errors, "warnings": warnings,
            "kind": kind, "id": item_id, "merged": bool(is_override)}


# ---------------------------------------------------------------------------
# render_v5 产物：timeline.json 校验
# ---------------------------------------------------------------------------
def _validate_timeline_semantics(timeline: dict, errors: list[str],
                                 warnings: list[str]) -> None:
    """timeline.json 语义校验：轨道/片段 id 唯一、时间范围、video/voice/subtitle 覆盖。"""
    tracks = timeline.get("tracks")
    if not isinstance(tracks, list) or not tracks:
        errors.append("timeline.tracks: 必须是非空数组")
        return
    try:
        duration = float(timeline.get("duration") or 0.0)
    except (TypeError, ValueError):
        duration = 0.0
        errors.append("timeline.duration: 必须是数字")
    seen_track_ids: set[str] = set()
    kinds: dict[str, int] = {}
    for index, track in enumerate(tracks):
        if not isinstance(track, dict):
            errors.append(f"timeline.tracks[{index}]: 必须是 object")
            continue
        track_id = str(track.get("id") or "")
        kind = str(track.get("kind") or "")
        kinds[kind] = kinds.get(kind, 0) + 1
        if not track_id:
            errors.append(f"timeline.tracks[{index}]: 缺少 id")
        elif track_id in seen_track_ids:
            errors.append(f"timeline.tracks[{index}].id={track_id!r} 重复")
        seen_track_ids.add(track_id)
        clips = track.get("clips")
        if not isinstance(clips, list):
            errors.append(f"timeline.tracks[{index}]({track_id}): clips 必须是数组")
            continue
        seen_clip_ids: set[str] = set()
        for clip_index, clip in enumerate(clips):
            owner = f"timeline.tracks[{track_id}].clips[{clip_index}]"
            if not isinstance(clip, dict):
                errors.append(f"{owner}: 必须是 object")
                continue
            clip_id = str(clip.get("id") or "")
            if not clip_id:
                errors.append(f"{owner}: 缺少 id")
            elif clip_id in seen_clip_ids:
                errors.append(f"{owner}.id={clip_id!r} 在同一轨道内重复")
            seen_clip_ids.add(clip_id)
            at, out = clip.get("at"), clip.get("out")
            if isinstance(at, (int, float)) and isinstance(out, (int, float)):
                # 约定：video/voice/music 的 out = 片段时长；subtitle 的 out = 绝对结束时间
                # （render_v5 现状；见 contract/v5/schema/timeline.schema.json）
                if kind == "subtitle":
                    if float(out) <= float(at):
                        warnings.append(f"{owner}: subtitle out={out} 应晚于 at={at}"
                                        f"（subtitle 的 out 是绝对结束时间）")
                    elif duration > 0 and float(out) > duration + 0.05:
                        warnings.append(f"{owner}: subtitle 结束 {float(out):.2f}s "
                                        f"超出 timeline.duration={duration:.2f}s")
                else:
                    if out <= 0:
                        warnings.append(f"{owner}: out={out} 应为正数（片段时长）")
                    elif duration > 0 and float(at) + float(out) > duration + 0.05:
                        warnings.append(f"{owner}: at+out={float(at) + float(out):.2f}s "
                                        f"超出 timeline.duration={duration:.2f}s")
    if not kinds.get("video"):
        errors.append("timeline.tracks: 缺少 video 轨（kind=video）")
    if kinds.get("voice") and not kinds.get("subtitle"):
        warnings.append("timeline.tracks: 有 voice 轨但没有 subtitle 轨（对话镜建议带字幕）")
    if kinds.get("subtitle") and not kinds.get("voice"):
        warnings.append("timeline.tracks: 有 subtitle 轨但没有 voice 轨（检查台词/音频）")


def validate_timeline(timeline: Any, root: Any = None) -> dict:
    """校验 render_v5 的 timeline.json（schema + 语义）。返回 {"ok","errors","warnings"}。"""
    errors: list[str] = []
    warnings: list[str] = []
    schema = load_timeline_schema(root)
    if not _HAS_JSONSCHEMA:
        warnings.append("jsonschema 未安装：跳过 timeline JSON Schema 校验（pip install jsonschema）")
    if schema is None:
        warnings.append("schema/timeline.schema.json 缺失，跳过 timeline JSON Schema 校验")
    elif not isinstance(timeline, dict):
        errors.append("timeline: 根节点必须是 JSON object")
    else:
        for message in _schema_errors_for(schema, timeline):
            errors.append(f"timeline: {message}")
    if isinstance(timeline, dict):
        _validate_timeline_semantics(timeline, errors, warnings)
    return {"ok": not errors, "errors": errors, "warnings": warnings}


def validate_timeline_file(path: Path | str, root: Any = None) -> dict:
    """读 timeline.json 并校验（文件不存在/JSON 非法 → ok=False）。"""
    timeline_path = Path(path)
    try:
        timeline = _read_json(timeline_path)
    except V5Error as exc:
        return {"ok": False, "errors": [str(exc)], "warnings": [],
                "path": str(timeline_path)}
    result = validate_timeline(timeline, root)
    result["path"] = str(timeline_path.resolve())
    return result


# ---------------------------------------------------------------------------
# 导出
# ---------------------------------------------------------------------------
def export_custom(kind: Any, item_id: str, out_dir: Any = None,
                  root: Any = None) -> dict:
    """把内建对象导出为可编辑的 custom 覆盖副本（默认写 contract/v5/custom/{kind}/）。"""
    kind = normalize_kind(kind)
    catalog = load_catalog(root)
    table = catalog.get(kind) or {}
    item = table.get(str(item_id))
    if not isinstance(item, dict):
        raise V5Error(f"{kind}/{item_id} 不存在（可用 id: {sorted(table)[:12]}…）")
    target_dir = Path(out_dir) if out_dir else Path(catalog["custom_root"]) / kind
    target_dir.mkdir(parents=True, exist_ok=True)
    path = target_dir / f"{item_id}.json"
    write_json_atomic(path, item)
    src = (catalog.get("sources", {}).get(kind, {}) or {}).get(item_id) or {}
    return {
        "ok": True,
        "kind": kind,
        "id": str(item_id),
        "path": str(path),
        "custom": str(item_id).startswith(CUSTOM_PREFIX),
        "override": not str(item_id).startswith(CUSTOM_PREFIX),
        "source": src.get("path", ""),
        "note": ("已导出内置对象；修改后在 custom/ 下同名即覆盖（深合并，数组替换）。"
                 if not str(item_id).startswith(CUSTOM_PREFIX)
                 else "已导出 custom 对象副本。"),
    }


def custom_path(kind: Any, item_id: str, root: Any = None) -> Path:
    """custom 覆盖文件路径（API PUT / CLI 写入用）。"""
    kind = normalize_kind(kind)
    return catalog_root(root) / "custom" / kind / f"{item_id}.json"


def item_source(catalog: dict, kind: Any, item_id: str) -> dict:
    kind = normalize_kind(kind)
    return dict((catalog.get("sources", {}).get(kind, {}) or {}).get(item_id) or {})


# ---------------------------------------------------------------------------
# 摘要（CLI / API 列表用）
# ---------------------------------------------------------------------------
def summarize_item(kind: Any, item: dict) -> dict:
    """把一个对象压缩成列表摘要（不返回全文，避免 catalog 接口过大）。"""
    kind = normalize_kind(kind)
    item = item if isinstance(item, dict) else {}
    summary: dict = {
        "id": item.get("id"),
        "label": item.get("label") or item.get("id"),
        "tags": item.get("tags") or [],
    }
    if kind == "styles":
        summary.update({
            "technique": item.get("technique") or [],
            "character_treatment": item.get("character_treatment") or "",
            "transition": item.get("transition") or "",
            "palette": {k: v for k, v in (item.get("palette") or {}).items()
                        if k in ("bg", "fg", "accent")},
        })
    elif kind == "scenes":
        layers = item.get("layers") if isinstance(item.get("layers"), list) else []
        summary.update({
            "layer_count": len(layers),
            "layer_types": [str(layer.get("type")) for layer in layers
                            if isinstance(layer, dict)][:8],
            "camera_zones": sorted((item.get("camera_zones") or {}).keys()),
            "aspect": item.get("aspect") or [16, 9],
            "variants": item.get("variants") or [],
        })
    elif kind == "characters":
        appearance = item.get("appearance") if isinstance(item.get("appearance"), dict) else {}
        summary.update({
            "role_pool": item.get("role_pool") or [],
            "gender": appearance.get("gender"),
            "age": appearance.get("age"),
            "hat": (appearance.get("outfit") or {}).get("hat"),
            "voice_provider": (item.get("voice") or {}).get("provider"),
            "has_image": bool(item.get("image")),
        })
    elif kind == "templates":
        cast = item.get("cast") if isinstance(item.get("cast"), list) else []
        acts = item.get("acts") if isinstance(item.get("acts"), list) else []
        summary.update({
            "style": item.get("style"),
            "target_duration": item.get("target_duration"),
            "cast": [{"role": c.get("role"), "character": c.get("character")}
                     for c in cast if isinstance(c, dict)],
            "act_count": len(acts),
            "act_ids": [a.get("id") for a in acts if isinstance(a, dict)],
            "scene_route": [a.get("scene") for a in acts if isinstance(a, dict)],
        })
    elif kind == "props":
        summary.update({"kind": item.get("kind") or item.get("type") or ""})
    elif kind == "packs":
        summary.update({key: len(item.get(key) or []) for key in
                        ("styles", "scenes", "characters", "props", "templates")})
    return summary


def catalog_summary(catalog: dict | None = None, kind: Any = None) -> dict:
    """catalog → 列表摘要（GET /api/v5/catalog 用）。"""
    catalog = catalog if isinstance(catalog, dict) and "sources" in catalog \
        else load_catalog()
    kinds = [normalize_kind(kind)] if kind is not None else list(KINDS)
    result: dict = {"ok": True, "root": catalog.get("root"), "kinds": kinds,
                    "counts": {k: len(catalog.get(k) or {}) for k in kinds}}
    for current in kinds:
        items = []
        for item_id, item in sorted((catalog.get(current) or {}).items()):
            if not isinstance(item, dict):
                continue
            summary = summarize_item(current, item)
            summary["custom"] = bool(
                (catalog.get("sources", {}).get(current, {}).get(item_id) or {}).get("custom"))
            items.append(summary)
        result[current] = items
    return result
