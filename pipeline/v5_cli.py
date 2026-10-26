# -*- coding: utf-8 -*-
"""V5 契约 CLI：目录浏览 / 校验 / 导出自定义副本 / 预览（不占 GPU）。

用法（仓库根目录，Python 3.11）：
  python -m pipeline.v5_cli list-styles
  python -m pipeline.v5_cli list-scenes --root contract/v5
  python -m pipeline.v5_cli list-characters
  python -m pipeline.v5_cli list-templates
  python -m pipeline.v5_cli list-props / list-packs
  python -m pipeline.v5_cli validate [--strict] [--root contract/v5]
  python -m pipeline.v5_cli export --kind styles --id neon_toon [--out DIR]
  python -m pipeline.v5_cli preview --kind style --id neon_toon --out tmp/v5_preview/x.png
  python -m pipeline.v5_cli preview --kind template --id history_talk --style ink_chronicle

约定：stdout 先输出人类可读行，最后一行是 JSON 结果；失败 exit code != 0。
预览调用 `pipeline.style_v5` / `pipeline.scene_render` / `pipeline.characters_v5`；
这些模块还没实现时输出 {"ok": false, "error": "模块未就绪: ..."}（exit 2），不静默兜底。
"""
from __future__ import annotations

import argparse
import importlib
import json
import sys
from pathlib import Path
from typing import Any

from . import v5_catalog

REPO = Path(__file__).resolve().parents[1]
DEFAULT_PREVIEW_DIR = REPO / "tmp" / "v5_preview"
DEFAULT_SIZE = {"styles": (1280, 720), "scenes": (1280, 720), "templates": (1280, 720),
                "characters": (768, 1152)}
LIST_KINDS = ("styles", "scenes", "characters", "props", "templates", "packs")


class PreviewNotReady(RuntimeError):
    """预览依赖的渲染模块（style_v5/scene_render/characters_v5）尚未就绪。"""


# ---------------------------------------------------------------------------
# 通用
# ---------------------------------------------------------------------------
def emit_json(obj: Any) -> None:
    print(json.dumps(obj, ensure_ascii=False), flush=True)


def _require_module(name: str):
    """动态 import pipeline.<name>；不存在/导入失败 → PreviewNotReady。"""
    try:
        return importlib.import_module(f"pipeline.{name}")
    except Exception as exc:  # noqa: BLE001 - 模块未实现/依赖缺失都按"未就绪"处理
        raise PreviewNotReady(f"pipeline.{name}（{type(exc).__name__}: {exc}）") from exc


def parse_size(text: str | None, default: tuple[int, int]) -> tuple[int, int]:
    if not text:
        return default
    raw = str(text).lower().replace(" ", "")
    if "x" in raw:
        left, _, right = raw.partition("x")
        try:
            width, height = int(left), int(right)
            if width >= 64 and height >= 64:
                return width, height
        except ValueError:
            pass
    raise SystemExit(f"--size 格式错误: {text}（示例 1280x720）")


def _load_or_die(root: str | None) -> dict:
    return v5_catalog.load_catalog(root)


def _get_item(catalog: dict, kind: str, item_id: str) -> dict:
    table = catalog.get(kind) or {}
    item = table.get(item_id)
    if not isinstance(item, dict):
        raise SystemExit(
            f"{kind}/{item_id} 不存在；可选: {', '.join(sorted(table)[:20])}"
            + ("…" if len(table) > 20 else ""))
    return item


def _style_for(catalog: dict, style_id: str | None) -> dict:
    styles = catalog.get("styles") or {}
    if style_id:
        item = styles.get(str(style_id))
        if not isinstance(item, dict):
            raise PreviewNotReady(f"style={style_id!r} 不存在（先创建 styles/{style_id}.json）")
        return item
    if not styles:
        raise PreviewNotReady("catalog 中没有 style，无法渲染预览")
    first_id = sorted(styles)[0]
    return styles[first_id]


# ---------------------------------------------------------------------------
# 预览渲染（backend router 也复用）
# ---------------------------------------------------------------------------
def _compose_scene_layers(layers: dict, size: tuple[int, int]):
    from PIL import Image

    width, height = int(size[0]), int(size[1])
    canvas = Image.new("RGBA", (width, height), (255, 255, 255, 255))
    if not isinstance(layers, dict):
        raise PreviewNotReady("scene_render.render_scene_layers 返回格式不正确")
    for key in ("background", "midground", "foreground"):
        image = layers.get(key)
        if image is None:
            continue
        image = image.convert("RGBA")
        if image.size != (width, height):
            image = image.resize((width, height), Image.LANCZOS)
        canvas.alpha_composite(image)
    return canvas


def _png_bytes(image, out_path: Path | None = None) -> bytes:
    from io import BytesIO

    buffer = BytesIO()
    image.convert("RGBA").save(buffer, format="PNG", optimize=True)
    data = buffer.getvalue()
    if out_path is not None:
        out_path = Path(out_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(data)
    return data


def render_scene_still(scene: dict, style: dict, size: tuple[int, int],
                       seed: int = 0, *, characters: list[dict] | None = None,
                       catalog: dict | None = None, out_path: Path | None = None) -> bytes:
    """场景（+可选角色）静帧 → PNG bytes。依赖 scene_render / characters_v5。"""
    scene_render = _require_module("scene_render")
    render_layers = getattr(scene_render, "render_scene_layers", None)
    if not callable(render_layers):
        raise PreviewNotReady("pipeline.scene_render 缺少 render_scene_layers()")
    layers = render_layers(scene, style, size, seed=seed)
    canvas = _compose_scene_layers(layers, size)
    if characters:
        characters_v5 = _require_module("characters_v5")
        render_character = getattr(characters_v5, "render_character", None)
        if not callable(render_character):
            raise PreviewNotReady("pipeline.characters_v5 缺少 render_character()")
        anchors = scene.get("anchors") if isinstance(scene.get("anchors"), dict) else {}
        width, height = size
        for entry in characters:
            char = entry.get("char") if isinstance(entry, dict) else None
            if not isinstance(char, dict):
                continue
            role = str(entry.get("role") or "")
            anchor = anchors.get(role) or anchors.get(str(char.get("id") or ""))
            if not anchor:
                continue
            sprite_size = (max(64, int(width * 0.32)), max(64, int(height * 0.72)))
            sprite = render_character(char, style, sprite_size,
                                      pose=str(entry.get("pose") or "front"),
                                      mouth=str(entry.get("mouth") or "closed"))
            if sprite is None:
                raise PreviewNotReady("characters_v5.render_character 返回 None")
            sprite = sprite.convert("RGBA")
            cx = float(anchor[0]) * width
            feet = float(anchor[1]) * height
            left = int(round(cx - sprite.width / 2))
            top = int(round(feet - sprite.height))
            canvas.alpha_composite(sprite, (left, top))
    return _png_bytes(canvas, out_path)


def render_preview_png(kind: str, item: dict, catalog: dict, *, style_id: str | None = None,
                       size: Any = None, out_path: Any = None) -> bytes:
    """按 kind 渲染预览 PNG；渲染模块未就绪 raise PreviewNotReady。"""
    kind = v5_catalog.normalize_kind(kind)
    default_size = DEFAULT_SIZE.get(kind, (1280, 720))
    if isinstance(size, (tuple, list)) and len(size) == 2:
        width, height = int(size[0]), int(size[1])
    else:
        width, height = parse_size(size if isinstance(size, str) else None, default_size)
    out = Path(out_path) if out_path else None

    if kind == "styles":
        style_v5 = _require_module("style_v5")
        render = getattr(style_v5, "apply_finish", None)
        title_card = getattr(style_v5, "title_card", None)
        if callable(title_card):
            path = out or (DEFAULT_PREVIEW_DIR / f"style_{item.get('id')}.png")
            path.parent.mkdir(parents=True, exist_ok=True)
            result = title_card(item, str(item.get("label") or item.get("id") or "V5"),
                                path, size=(width, height))
            if isinstance(result, dict) and result.get("path") and Path(result["path"]).is_file():
                return Path(result["path"]).read_bytes()
            if path.is_file():
                return path.read_bytes()
        if callable(render):
            from PIL import Image, ImageDraw

            path = out or (DEFAULT_PREVIEW_DIR / f"style_{item.get('id')}.png")
            base = Image.new("RGB", (width, height), (24, 24, 28))
            draw = ImageDraw.Draw(base)
            palette = item.get("palette") if isinstance(item.get("palette"), dict) else {}
            swatches = []
            for key in ("bg", "fg", "accent", "paper", "shadow", "highlight"):
                value = palette.get(key)
                for entry in (value if isinstance(value, list) else [value]):
                    if isinstance(entry, str) and v5_catalog.HEX_RE.match(entry):
                        swatches.append(entry)
            swatch_w = max(8, width // max(1, len(swatches)))
            for index, color in enumerate(swatches):
                draw.rectangle((index * swatch_w, 0, (index + 1) * swatch_w, height),
                               fill=color)
            finished = render(base, item, kind="plate")
            return _png_bytes(finished, path)
        raise PreviewNotReady("pipeline.style_v5 缺少 title_card()/apply_finish()")

    if kind in ("scenes", "templates"):
        style = _style_for(catalog, style_id)
        if kind == "scenes":
            return render_scene_still(item, style, (width, height),
                                      seed=int(item.get("seed") or 0), out_path=out)
        # template：首幕场景 + cast 角色（用 scene.anchors 摆放）
        acts = item.get("acts") if isinstance(item.get("acts"), list) else []
        if not acts:
            raise PreviewNotReady(f"templates/{item.get('id')} 没有 acts")
        first = acts[0]
        scenes = catalog.get("scenes") or {}
        scene = scenes.get(str(first.get("scene")))
        if not isinstance(scene, dict):
            raise PreviewNotReady(
                f"templates/{item.get('id')} 引用的 scene={first.get('scene')!r} 尚未就绪")
        characters = catalog.get("characters") or {}
        cast_entries: list[dict] = []
        act_chars = first.get("characters")
        wanted = list(act_chars) if isinstance(act_chars, list) and act_chars else None
        for member in (item.get("cast") or []):
            if not isinstance(member, dict):
                continue
            role = str(member.get("role") or "")
            if wanted is not None and role not in wanted and member.get("character") not in wanted:
                continue
            char = characters.get(str(member.get("character")))
            if isinstance(char, dict):
                cast_entries.append({"char": char, "role": role})
        return render_scene_still(scene, style, (width, height),
                                  seed=int(scene.get("seed") or 0),
                                  characters=cast_entries, catalog=catalog, out_path=out)

    if kind == "characters":
        style = _style_for(catalog, style_id if not isinstance(style_id, dict) else None)
        characters_v5 = _require_module("characters_v5")
        render_character = getattr(characters_v5, "render_character", None)
        if not callable(render_character):
            raise PreviewNotReady("pipeline.characters_v5 缺少 render_character()")
        sprite = render_character(item, style, (width, height), pose="front", mouth="closed")
        if sprite is None:
            raise PreviewNotReady("characters_v5.render_character 返回 None")
        return _png_bytes(sprite, out)

    raise PreviewNotReady(f"{kind} 不支持预览（仅 style/scene/character/template）")


# ---------------------------------------------------------------------------
# 子命令
# ---------------------------------------------------------------------------
def _print_items(kind: str, rows: list[dict]) -> None:
    print(f"== {kind}（{len(rows)}）")
    for row in rows:
        flags = []
        if row.get("custom"):
            flags.append("custom")
        label = row.get("label") or ""
        extra = ""
        if kind == "styles":
            extra = " / ".join(row.get("technique") or [])
        elif kind == "scenes":
            extra = f"{row.get('layer_count')} layers · zones={','.join(row.get('camera_zones') or [])}"
        elif kind == "characters":
            extra = f"{row.get('gender')} {row.get('age')} · roles={','.join(row.get('role_pool') or [])}"
        elif kind == "templates":
            extra = (f"style={row.get('style')} · {row.get('act_count')} acts · "
                     f"{row.get('target_duration')}s")
        suffix = f" [{', '.join(flags)}]" if flags else ""
        print(f"  {row.get('id'):22s} {label:12s} {extra}{suffix}")


def cmd_list(args: argparse.Namespace) -> int:
    catalog = _load_or_die(args.root)
    kind = args.kind
    if kind in (None, "all"):
        kinds = list(LIST_KINDS)
    else:
        kinds = [v5_catalog.normalize_kind(kind)]
    summary = v5_catalog.catalog_summary(catalog, kind if kind not in (None, "all") else None)
    for current in kinds:
        _print_items(current, summary.get(current) or [])
    print(f"root={summary.get('root')} counts={summary.get('counts')}")
    emit_json(summary)
    return 0


def cmd_validate(args: argparse.Namespace) -> int:
    catalog = _load_or_die(args.root)
    result = v5_catalog.validate_catalog(catalog, strict=bool(args.strict))
    # 可选：附带校验 render_v5 产物 timeline.json（schema/timeline.schema.json + 语义）
    timeline_path = getattr(args, "timeline", None)
    if timeline_path:
        timeline_result = v5_catalog.validate_timeline_file(timeline_path, root=args.root)
        result["timeline"] = timeline_result
        result["errors"] = list(result.get("errors") or []) + list(
            timeline_result.get("errors") or [])
        result["warnings"] = list(result.get("warnings") or []) + [
            f"timeline: {message}" for message in (timeline_result.get("warnings") or [])
        ]
        result["ok"] = bool(result.get("ok") and timeline_result.get("ok"))
        print(f"[validate] timeline: {timeline_path} → "
              f"{'OK' if timeline_result.get('ok') else 'FAIL'} "
              f"(errors={len(timeline_result.get('errors') or [])}, "
              f"warnings={len(timeline_result.get('warnings') or [])})")
    for message in result.get("errors") or []:
        print(f"ERR  {message}")
    for message in result.get("warnings") or []:
        print(f"WARN {message}")
    stats = result.get("stats") or {}
    print(f"== {'OK' if result['ok'] else 'FAIL'}  errors={len(result.get('errors') or [])} "
          f"warnings={len(result.get('warnings') or [])} "
          f"counts={stats.get('counts')} strict={bool(args.strict)}")
    emit_json(result)
    return 0 if result["ok"] else 1


def cmd_export(args: argparse.Namespace) -> int:
    try:
        result = v5_catalog.export_custom(args.kind, args.id, args.out, root=args.root)
    except v5_catalog.V5Error as exc:
        print(f"ERR  {exc}")
        emit_json({"ok": False, "error": str(exc), "kind": args.kind, "id": args.id})
        return 1
    print(f"[export] {result['kind']}/{result['id']} → {result['path']}")
    emit_json(result)
    return 0


def cmd_preview(args: argparse.Namespace) -> int:
    catalog = _load_or_die(args.root)
    try:
        kind = v5_catalog.normalize_kind(args.kind)
    except v5_catalog.V5Error as exc:
        print(f"ERR  {exc}")
        emit_json({"ok": False, "error": str(exc)})
        return 1
    try:
        item = _get_item(catalog, kind, args.id)
    except SystemExit as exc:  # 保持统一 JSON 输出
        emit_json({"ok": False, "error": str(exc), "kind": kind, "id": args.id})
        return 1
    out = Path(args.out) if args.out else DEFAULT_PREVIEW_DIR / f"{kind}_{args.id}.png"
    try:
        data = render_preview_png(kind, item, catalog, style_id=args.style,
                                  size=args.size, out_path=out)
    except PreviewNotReady as exc:
        print(f"[preview] 模块未就绪: {exc}")
        emit_json({"ok": False, "error": f"模块未就绪: {exc}",
                   "kind": kind, "id": args.id, "hint":
                   "等待 pipeline.style_v5 / scene_render / characters_v5 实现后重试"})
        return 2
    except Exception as exc:  # noqa: BLE001 - 渲染错误如实报出
        print(f"[preview] 渲染失败: {exc}")
        emit_json({"ok": False, "error": f"渲染失败: {exc}", "kind": kind, "id": args.id})
        return 1
    print(f"[preview] {kind}/{args.id} → {out}")
    emit_json({"ok": True, "kind": kind, "id": args.id, "path": str(out),
               "bytes": len(data)})
    return 0


# ---------------------------------------------------------------------------
# parser
# ---------------------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m pipeline.v5_cli",
        description="V5 契约 CLI：目录浏览 / 校验 / 导出自定义副本 / 预览（不占 GPU）")
    parser.add_argument("--root", default=None,
                        help="contract/v5 根目录（默认环境变量 V5_CONTRACT_ROOT 或仓库 contract/v5）")
    sub = parser.add_subparsers(dest="command", required=True)

    def _add_root(p: argparse.ArgumentParser) -> None:
        """允许 --root 写在子命令后（如 `validate --root contract/v5`）；
        未提供时保留顶层 --root 的值（default=SUPPRESS 才不会覆盖）。"""
        p.add_argument("--root", default=argparse.SUPPRESS,
                       help="contract/v5 根目录（默认环境变量 V5_CONTRACT_ROOT 或仓库 contract/v5）")

    for name in LIST_KINDS:
        p = sub.add_parser(f"list-{name}", help=f"列出 {name}")
        _add_root(p)
        p.set_defaults(func=cmd_list, kind=name)

    p_list = sub.add_parser("list", help="列出全部（或 --kind 一种）")
    p_list.add_argument("--kind", default="all",
                        choices=list(LIST_KINDS) + ["all", "style", "scene",
                                                    "character", "template", "prop", "pack"])
    _add_root(p_list)
    p_list.set_defaults(func=cmd_list)

    p_validate = sub.add_parser("validate", help="校验目录（Schema/引用/custom id/资源）")
    p_validate.add_argument("--strict", action="store_true", help="warning 也视为失败（CI）")
    p_validate.add_argument("--timeline", default=None,
                            help="可选：同时校验 render_v5 产物 timeline.json")
    _add_root(p_validate)
    p_validate.set_defaults(func=cmd_validate)

    p_export = sub.add_parser("export", help="导出内置对象为 custom 可编辑副本")
    p_export.add_argument("--kind", required=True, choices=list(LIST_KINDS)
                          + ["style", "scene", "character", "template", "prop", "pack"])
    p_export.add_argument("--id", required=True)
    p_export.add_argument("--out", default=None, help="输出目录（默认 contract/v5/custom/{kind}/）")
    _add_root(p_export)
    p_export.set_defaults(func=cmd_export)

    p_preview = sub.add_parser("preview", help="渲染预览 PNG（依赖渲染模块，未就绪会提示）")
    p_preview.add_argument("--kind", required=True,
                           choices=["style", "styles", "scene", "scenes", "character",
                                    "characters", "template", "templates"])
    p_preview.add_argument("--id", required=True)
    p_preview.add_argument("--out", default=None, help="输出 PNG（默认 tmp/v5_preview/）")
    p_preview.add_argument("--style", default=None, help="scene/character 预览用的 style id")
    p_preview.add_argument("--size", default=None, help="输出尺寸，如 1280x720")
    _add_root(p_preview)
    p_preview.set_defaults(func=cmd_preview)
    return parser


def main(argv: list[str] | None = None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except KeyboardInterrupt:
        print("已中断", file=sys.stderr)
        return 130
    except Exception as exc:  # noqa: BLE001
        print(f"ERR  {exc}", file=sys.stderr)
        emit_json({"ok": False, "error": str(exc)})
        return 1


if __name__ == "__main__":
    sys.exit(main())
