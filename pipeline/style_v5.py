# -*- coding: utf-8 -*-
"""V5 风格引擎：目录加载 + 画面后处理（16 technique）+ 调色/包装/字幕/片头片尾/转场。

设计（docs/PLAN_V5_STYLE_AUDIO.md §3 + docs/V5_INTERFACES.md §2）：
- **风格 = 可组合视觉原语**：本模块负责“风格感”的后半段——调色、纹理、线条、
  排版、包装、转场；前半段（分层绘景）在 ``pipeline/scene_render.py``；
- 所有参数都从 style JSON 读（``finish`` / ``grade`` / ``packaging`` / ``typography`` /
  ``transition`` / ``audio`` / ``h3``），未知键忽略、缺失走技法默认值；
- 16 个 technique 全部有独立分支，**不会静默 fallback 成同一效果**：
  ``neon_toon hologram vhs film_grain noir oil_impasto baroque_candle blueprint
  collage_cutout claymation chalk_sketch ink_wash woodblock parchment_engrave
  museum_gold silk_gongbi``
- 字体复用 ``pipeline/fonts.py`` 的 ensure_fonts（中文字体，不下载新字体）；
- 不依赖 GPU/网络，纯 PIL/numpy。

对外接口（勿改签名，见 docs/V5_INTERFACES.md §2）::

    load_catalog(root=None) -> dict
    get_style(catalog, style_id) -> dict
    style_prompt(style) -> str
    apply_finish(img, style, kind="plate") -> PIL.Image
    grade_filter(style) -> str                      # ffmpeg 调色链
    packaging_filters(style, workdir, width, height, font_file) -> str
    subtitle_ass(style, events, out_ass, width, height) -> dict
    title_card(style, text, out_png, size=(1280,720), subtitle="") -> dict
    end_card(style, text, out_png, size=(1280,720), subtitle="") -> dict
    transition(style, **kwargs) -> tuple[str, str]

CLI（预览用）::

    python -m pipeline.style_v5 --list
    python -m pipeline.style_v5 --style ink_chronicle --title 史书 --out tmp/v5_preview/card.png
"""
from __future__ import annotations

import argparse
import json
import logging
import math
import random
import re
import time
import zlib
from pathlib import Path

import numpy as np
from PIL import Image, ImageChops, ImageDraw, ImageEnhance, ImageFilter, ImageFont, ImageOps

from . import config, fonts, scene_render
from .scene_render import V5Error

LOG = logging.getLogger("pipeline")

REPO = config.REPO_ROOT
CONTRACT_ROOT = REPO / "contract" / "v5"
FONT_SCRATCH = REPO / "tmp" / "v5_fonts"
KINDS = ("styles", "scenes", "characters", "props", "templates", "packs")

#: 16 个 technique（契约键，顺序即 apply_finish 的特效应用顺序）
TECHNIQUES = (
    "ink_wash", "woodblock", "parchment_engrave", "museum_gold", "silk_gongbi",
    "neon_toon", "hologram", "vhs", "film_grain", "noir", "oil_impasto",
    "baroque_candle", "blueprint", "collage_cutout", "claymation", "chalk_sketch",
)

TECH_LABELS = {
    "ink_wash": "水墨", "woodblock": "版刻", "parchment_engrave": "羊皮纸铜版",
    "museum_gold": "博物馆金黑", "silk_gongbi": "绢本工笔", "neon_toon": "霓虹卡通",
    "hologram": "全息", "vhs": "VHS 录像带", "film_grain": "胶片", "noir": "黑色电影",
    "oil_impasto": "油画厚涂", "baroque_candle": "巴洛克烛光", "blueprint": "蓝图",
    "collage_cutout": "拼贴剪纸", "claymation": "黏土定格", "chalk_sketch": "粉笔素描",
}


# ===========================================================================
# 1. 目录（委托 pipeline/v5_catalog.py；缺失时本地兜底）
# ===========================================================================
def _deep_merge(base, override):
    if isinstance(base, dict) and isinstance(override, dict):
        out = dict(base)
        for key, value in override.items():
            out[key] = _deep_merge(base[key], value) if key in base else value
        return out
    return override


def _fallback_load(root: Path | None = None) -> dict:
    base = Path(root) if root else CONTRACT_ROOT
    catalog: dict = {}
    for kind in KINDS:
        data: dict = {}
        for folder in (base / kind, base / "custom" / kind):
            if not folder.is_dir():
                continue
            for path in sorted(folder.glob("*.json")):
                try:
                    payload = json.loads(path.read_text(encoding="utf-8"))
                except (OSError, ValueError) as exc:
                    raise V5Error(f"读取 {path} 失败: {exc}") from exc
                if not isinstance(payload, dict):
                    continue
                item_id = str(payload.get("id") or path.stem)
                payload.setdefault("id", item_id)
                if item_id in data:
                    data[item_id] = _deep_merge(data[item_id], payload)
                else:
                    data[item_id] = payload
        catalog[kind] = data
    return catalog


def load_catalog(root=None) -> dict:
    """加载 contract/v5 目录（styles/scenes/characters/props/templates/packs）。

    - 优先委托 ``pipeline/v5_catalog.load_catalog``（与 Lead/后端共用同一实现）；
    - 该模块不可用时本地兜底（同样支持 custom/ 深合并覆盖）；
    - 返回 dict 至少含 6 个 kind（缺失为空 dict）。
    """
    catalog: dict
    try:
        from . import v5_catalog  # 共享目录层（Lead 提供）
        catalog = v5_catalog.load_catalog(root)
    except Exception as exc:  # noqa: BLE001 - 目录层不可用不阻断风格引擎
        LOG.debug("v5_catalog 不可用，使用 style_v5 兜底加载: %s", exc)
        catalog = _fallback_load(Path(root) if root else None)
    if not isinstance(catalog, dict):
        raise V5Error("load_catalog 返回值不是 dict")
    for kind in KINDS:
        if not isinstance(catalog.get(kind), dict):
            catalog[kind] = {}
    return catalog


def get_style(catalog: dict, style_id: str) -> dict:
    """按 id 取 style；不存在 raise V5Error（列出可用 id）。"""
    styles = (catalog or {}).get("styles") or {}
    key = str(style_id or "").strip()
    style = styles.get(key)
    if style is None:
        lowered = {str(k).lower(): v for k, v in styles.items()}
        style = lowered.get(key.lower())
    if not isinstance(style, dict):
        raise V5Error(f"风格不存在: {style_id!r}（可用: {', '.join(sorted(styles)) or '<空>'}）")
    return style


def get_scene(catalog: dict, scene_id: str) -> dict:
    """按 id 取 scene；不存在 raise V5Error。"""
    scenes = (catalog or {}).get("scenes") or {}
    scene = scenes.get(str(scene_id or "").strip())
    if not isinstance(scene, dict):
        raise V5Error(f"场景不存在: {scene_id!r}（可用: {', '.join(sorted(scenes))}）")
    return scene


def validate_catalog(catalog: dict | None = None, strict: bool = False, root=None) -> dict:
    """委托共享校验器（JSON Schema + 引用完整性）；不可用时做最小自检。"""
    try:
        from . import v5_catalog
        return v5_catalog.validate_catalog(catalog, strict=strict, root=root)
    except Exception as exc:  # noqa: BLE001
        LOG.debug("v5_catalog.validate_catalog 不可用: %s", exc)
        catalog = catalog if isinstance(catalog, dict) else load_catalog(root)
        errors: list[str] = []
        warnings: list[str] = []
        for kind in ("styles", "scenes", "characters", "templates"):
            if not (catalog.get(kind) or {}):
                warnings.append(f"{kind}/ 为空")
        for sid, style in (catalog.get("styles") or {}).items():
            if not isinstance(style, dict):
                errors.append(f"style {sid}: 不是 object")
                continue
            if not style.get("label"):
                errors.append(f"style {sid}: 缺 label")
            palette = style.get("palette")
            if not isinstance(palette, dict):
                errors.append(f"style {sid}: 缺 palette")
            else:
                for role in ("bg", "fg", "accent"):
                    if role not in palette:
                        errors.append(f"style {sid}: palette 缺 {role}")
        return {"ok": not errors, "errors": errors, "warnings": warnings,
                "stats": {"counts": {k: len(catalog.get(k) or {}) for k in KINDS}},
                "strict": bool(strict)}


def style_prompt(style: dict) -> str:
    """风格 → 图像/H3 prompt 字符串（h3.style_prompt 优先，缺失按 technique 组合）。"""
    style = style if isinstance(style, dict) else {}
    h3 = style.get("h3") if isinstance(style.get("h3"), dict) else {}
    parts: list[str] = []
    if h3.get("style_prompt"):
        parts.append(str(h3["style_prompt"]).strip())
    techniques = [str(t) for t in (style.get("technique") or [])]
    for tech in techniques:
        fragment = TECH_PROMPT.get(tech)
        if fragment:
            parts.append(fragment)
    for texture in (style.get("texture") or []):
        fragment = TEXTURE_PROMPT.get(str(texture))
        if fragment:
            parts.append(fragment)
    line = style.get("line") if isinstance(style.get("line"), dict) else {}
    line_style = str(line.get("style") or "")
    if line_style in LINE_PROMPT:
        parts.append(LINE_PROMPT[line_style])
    character = str(style.get("character_treatment") or "")
    if character in CHARACTER_PROMPT:
        parts.append(CHARACTER_PROMPT[character])
    # 去重保序
    seen: set[str] = set()
    unique: list[str] = []
    for part in parts:
        key = part.lower().strip()
        if key and key not in seen:
            seen.add(key)
            unique.append(part)
    base = ", ".join(unique) if unique else "cinematic 2D illustration"
    if "no text" not in base.lower():
        base += ", no text, no watermark, no logo"
    return base


TECH_PROMPT = {
    "ink_wash": "traditional Chinese ink-wash painting (shuimo), rice paper, dry brush strokes, ink bleed, restrained pale color",
    "woodblock": "Chinese woodblock print (banhua), carved lines, wood grain, flat ink impression on aged paper",
    "parchment_engrave": "copperplate engraving on aged parchment, fine cross-hatching, sepia archival document",
    "museum_gold": "museum exhibition, black marble, gold accents, dramatic spotlights, polished stone",
    "silk_gongbi": "gongbi fine-brush painting on silk, mineral pigments, elegant outlines, pale silk ground",
    "neon_toon": "neon-lit 3D cartoon, thick clean outlines, glowing neon rim light, cinematic bokeh",
    "hologram": "holographic stage, particle grid, laser beams, iridescent cyan-magenta, futuristic",
    "vhs": "1980s VHS footage, scanlines, chromatic aberration, analog noise, warm magenta",
    "film_grain": "16mm film look, warm halation, gate weave, soft grain, cinematic interview",
    "noir": "film noir, high-contrast chiaroscuro, rain-slick streets, venetian blind shadows, monochrome with cold blue",
    "oil_impasto": "renaissance oil painting, thick impasto brushwork, chiaroscuro, varnished canvas",
    "baroque_candle": "baroque castle interior, candlelight, gothic arches, velvet and gold, deep shadows",
    "blueprint": "engineering blueprint, cyanotype blue grid, white technical linework, dimension annotations",
    "collage_cutout": "paper collage cutout, torn paper edges, halftone dots, saturated candy colors",
    "claymation": "stop-motion clay diorama, handmade clay texture, miniature set, soft studio light",
    "chalk_sketch": "chalk sketch on green blackboard, hand-drawn lines, chalk dust, theatre stage",
}
TEXTURE_PROMPT = {
    "paper_grain": "rice paper texture",
    "canvas": "canvas texture",
    "scanline": "scanlines",
    "vhs_noise": "analog tape noise",
    "halftone": "halftone dots",
    "clay_noise": "clay surface texture",
    "chalk_dust": "chalk dust",
    "grid": "technical grid",
    "bokeh": "bokeh light",
    "silk_weave": "silk weave texture",
    "wood_grain": "wood grain texture",
}
LINE_PROMPT = {
    "brush_edge": "brush-edged ink outlines",
    "woodcut": "carved woodcut lines",
    "engrave": "fine engraved hatching lines",
    "thick_outline": "thick cartoon outlines",
    "sketch": "sketchy hand-drawn lines",
    "none": "",
}
CHARACTER_PROMPT = {
    "ink_brush": "characters rendered as ink-brush figures",
    "cutout_paper": "characters as paper cutout figures with white sticker edge",
    "clay": "characters sculpted in clay",
    "pixel": "pixel-art characters",
    "halftone": "halftone-shaded characters",
    "oil": "characters painted with oil brush strokes",
    "chalk": "characters drawn in chalk",
    "toon": "characters with clean toon shading",
    "gongbi": "characters painted in gongbi fine-line style",
}


# ===========================================================================
# 2. 调色/字色 helper
# ===========================================================================
def palette_color(style: dict, ref=None, fallback: str = "fg", index: int = 0,
                  alpha: int = 255) -> tuple[int, int, int, int]:
    """从 style.palette 取色（复刻 scene_render.resolve_color 语义）。"""
    palette = (style or {}).get("palette") or {}
    return scene_render.resolve_color(palette, ref, fallback=fallback, index=index, alpha=alpha)


def _rgb(style: dict, ref=None, fallback: str = "fg", index: int = 0):
    return palette_color(style, ref, fallback=fallback, index=index)[:3]


def _mix(a, b, t: float):
    return scene_render.mix(tuple(a)[:3], tuple(b)[:3], t)


def _rgba(img: Image.Image) -> Image.Image:
    return img.convert("RGBA") if img.mode != "RGBA" else img


def _apply_alpha_mask(img: Image.Image, original: Image.Image) -> Image.Image:
    out = img.convert("RGBA")
    out.putalpha(original.getchannel("A"))
    return out


def _overlay(img: Image.Image, layer: Image.Image) -> Image.Image:
    """RGBA 叠加（保持原 alpha；对角色类图像用原 alpha 裁掉溢出）。"""
    out = _rgba(img).copy()
    if layer.mode != "RGBA":
        layer = layer.convert("RGBA")
    if layer.size != out.size:
        layer = layer.resize(out.size, Image.BILINEAR)
    out.alpha_composite(layer)
    return out


def _vignette(img: Image.Image, strength: float, color=(0, 0, 0), radius: float = 0.72,
              power: float = 1.6) -> Image.Image:
    """径向压角（strength 0-1，自动 clamp）。"""
    if strength <= 0.004:
        return img
    w, h = img.size
    sw, sh = 72, 40
    yy, xx = np.mgrid[0:sh, 0:sw].astype(np.float32)
    nx = (xx - (sw - 1) / 2.0) / ((sw - 1) / 2.0)
    ny = (yy - (sh - 1) / 2.0) / ((sh - 1) / 2.0)
    d = np.sqrt(nx ** 2 + ny ** 2)
    m = np.clip((d - radius) / max(0.05, 1.0 - radius), 0, 1) ** power
    mask = Image.fromarray(np.clip(m * 255 * min(1.0, strength), 0, 255).astype(np.uint8), "L")
    mask = mask.resize((w, h), Image.BILINEAR)
    overlay = scene_render.mask_layer((w, h), mask, tuple(int(c) for c in color[:3]) + (255,))
    return _overlay(img, overlay)


def _grain(img: Image.Image, strength: float, seed: int = 0, color=(0, 0, 0),
           light=(255, 255, 255)) -> Image.Image:
    """颗粒/胶片噪点（亮暗两层，保持原 alpha）。"""
    if strength <= 0.002:
        return img
    w, h = img.size
    rng = np.random.default_rng(int(seed) & 0xFFFFFFFF)
    sw, sh = max(8, w // 2), max(8, h // 2)
    noise = np.clip(rng.standard_normal((sh, sw), dtype=np.float32) * 0.5 + 0.5, 0, 1)
    luma = Image.fromarray((noise * 255).astype(np.uint8), "L")
    k = min(1.0, float(strength)) * 2.4
    dark = luma.point(lambda v: int(max(0, 128 - v) * k))
    bright = luma.point(lambda v: int(max(0, v - 128) * k))
    out = _overlay(img, scene_render.mask_layer((w, h), dark.resize((w, h), Image.BILINEAR),
                                                tuple(int(c) for c in color[:3]) + (255,)))
    out = _overlay(out, scene_render.mask_layer(
        (w, h), bright.resize((w, h), Image.BILINEAR),
        tuple(int(c) for c in light[:3]) + (255,)))
    return out


def _scanlines(img: Image.Image, strength: float, spacing: int = 4,
               color=(0, 0, 0)) -> Image.Image:
    if strength <= 0.004:
        return img
    w, h = img.size
    layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    a = int(min(1.0, strength) * 255)
    for y in range(0, h, max(2, int(spacing))):
        d.line([(0, y), (w, y)], fill=tuple(int(c) for c in color[:3]) + (a,), width=1)
    return _overlay(img, layer)


def _bloom(img: Image.Image, threshold: float = 190.0, radius: float = 12.0,
           strength: float = 0.35, tint=None) -> Image.Image:
    """高光泛光（screen 混合，保持 alpha）。"""
    if strength <= 0.005:
        return img
    w, h = img.size
    rgb = img.convert("RGB")
    luma = rgb.convert("L")
    mask = luma.point(lambda v: 255 if v >= float(threshold) else 0)
    bright = Image.composite(rgb, Image.new("RGB", (w, h), (0, 0, 0)), mask)
    if tint is not None:
        tint_img = Image.new("RGB", (w, h), tuple(int(c) for c in tint[:3]))
        bright = ImageChops.multiply(bright, tint_img)
    blurred = bright.filter(ImageFilter.GaussianBlur(max(1.0, float(radius))))
    if strength < 1.0:
        blurred = ImageEnhance.Brightness(blurred).enhance(max(0.05, float(strength)))
    out_rgb = ImageChops.screen(rgb, blurred)
    out = out_rgb.convert("RGBA")
    out.putalpha(img.getchannel("A"))
    return out


def _edge_ink(img: Image.Image, strength: float, color=(0, 0, 0), gain: float = 2.2,
              blur: float = 0.0) -> Image.Image:
    """边缘墨线（FIND_EDGES → 加深边缘；不改变原色相）。"""
    if strength <= 0.004:
        return img
    rgb = img.convert("RGB")
    edges = rgb.convert("L").filter(ImageFilter.FIND_EDGES)
    edges = edges.point(lambda v: min(255, int(v * float(gain))))
    if blur > 0:
        edges = edges.filter(ImageFilter.GaussianBlur(float(blur)))
    mask = edges.point(lambda v: int(v * min(1.5, float(strength))))
    layer = scene_render.mask_layer(img.size, mask,
                                    tuple(int(c) for c in color[:3]) + (255,))
    return _overlay(img, layer)


def _hatch_shadows(img: Image.Image, strength: float, spacing: float, color=(0, 0, 0),
                   angle: float = -40.0) -> Image.Image:
    """暗部排线（铜版/木刻阴影）。"""
    if strength <= 0.004:
        return img
    w, h = img.size
    luma = img.convert("RGB").convert("L")
    dark_mask = luma.point(lambda v: int(min(255.0, (255 - v) * float(strength) * 1.6)))
    hatch = scene_render.hatch_layer((w, h), dark_mask, (0, 0, w, h), angle,
                                     max(3.0, float(spacing)),
                                     tuple(int(c) for c in color[:3]) + (255,),
                                     width=1, alpha=190)
    # 只用原图 alpha 区域
    alpha = img.getchannel("A")
    hatch.putalpha(ImageChops.multiply(hatch.getchannel("A"), alpha))
    return _overlay(img, hatch)


def _paper_texture(img: Image.Image, base_color, strength: float, seed: int = 7,
                   fibers: int = 60) -> Image.Image:
    """纸纹（噪声斑驳 + 纤维），叠加在图像上。"""
    if strength <= 0.004:
        return img
    w, h = img.size
    noise = scene_render.smooth_noise(max(8, w // 2), max(8, h // 2),
                                      scale=max(3.0, min(w, h) * 0.14),
                                      seed=seed, octaves=4)
    dark_m = np.clip((noise - 0.52) / 0.48, 0, 1) * 255.0 * float(strength)
    light_m = np.clip((0.48 - noise) / 0.48, 0, 1) * 255.0 * float(strength) * 0.7
    dark_c = tuple(int(c) for c in _mix(base_color, (0, 0, 0), 0.45)[:3])
    light_c = tuple(int(c) for c in _mix(base_color, (255, 255, 255), 0.55)[:3])
    out = _overlay(img, scene_render.mask_layer(
        (w, h), Image.fromarray(dark_m.astype(np.uint8), "L").resize((w, h), Image.BILINEAR),
        dark_c + (255,)))
    out = _overlay(out, scene_render.mask_layer(
        (w, h), Image.fromarray(light_m.astype(np.uint8), "L").resize((w, h), Image.BILINEAR),
        light_c + (255,)))
    if fibers > 0:
        rng = random.Random(seed)
        layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        d = ImageDraw.Draw(layer)
        fiber_c = tuple(int(c) for c in _mix(base_color, (0, 0, 0), 0.35)[:3])
        for _ in range(int(fibers)):
            x, y = rng.uniform(0, w), rng.uniform(0, h)
            ln = rng.uniform(0.004, 0.03) * w
            d.line([(x, y), (x + ln, y + rng.uniform(-2, 2))],
                   fill=fiber_c + (rng.randint(4, 18),), width=1)
        out = _overlay(out, layer)
    return out


def _canvas_texture(img: Image.Image, strength: float, color=(0, 0, 0)) -> Image.Image:
    if strength <= 0.004:
        return img
    w, h = img.size
    step = max(3, int(min(w, h) * 0.006))
    layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    a = int(min(1.0, strength) * 120)
    c = tuple(int(v) for v in color[:3]) + (a,)
    for x in range(0, w, step):
        d.line([(x, 0), (x, h)], fill=c, width=1)
    for y in range(0, h, step):
        d.line([(0, y), (w, y)], fill=c, width=1)
    return _overlay(img, layer)


def _silk_weave(img: Image.Image, strength: float) -> Image.Image:
    """丝绢织纹 + 斜向柔光（silk_gongbi）。"""
    if strength <= 0.004:
        return img
    w, h = img.size
    sw, sh = max(8, w // 3), max(8, h // 3)
    yy, xx = np.mgrid[0:sh, 0:sw].astype(np.float32)
    weave = (np.sin(xx * 0.8) * 0.5 + np.sin(yy * 0.8) * 0.5) * 0.5 + 0.5
    weave = (weave * 255).astype(np.uint8)
    mask = Image.fromarray(weave, "L").resize((w, h), Image.BILINEAR).point(
        lambda v: int(abs(v - 128) * 0.5 * min(1.0, float(strength))))
    out = _overlay(img, scene_render.mask_layer((w, h), mask, (240, 235, 220, 255)))
    # 斜向光泽
    sheen = np.linspace(0, 1, w, dtype=np.float32)[None, :]
    band = np.exp(-((sheen - 0.68) ** 2) / (2 * 0.16 ** 2)) * 255 * 0.16 * min(1.0, strength)
    band_img = Image.fromarray(np.tile(band, (h, 1)).astype(np.uint8), "L")
    out = _overlay(out, scene_render.mask_layer((w, h), band_img, (255, 250, 235, 255)))
    return out


def _gold_dust(img: Image.Image, strength: float, color=(230, 196, 106), seed: int = 5) -> Image.Image:
    if strength <= 0.002:
        return img
    w, h = img.size
    rng = random.Random(seed)
    layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    for _ in range(int(220 * min(1.0, float(strength) * 3))):
        x, y = rng.uniform(0, w), rng.uniform(0, h)
        r = rng.uniform(0.5, 1.8)
        d.ellipse((x - r, y - r, x + r, y + r),
                  fill=tuple(int(c) for c in color[:3]) + (rng.randint(20, 120),))
    return _overlay(img, layer)


def _crackle(img: Image.Image, strength: float, color=(60, 45, 30), seed: int = 9) -> Image.Image:
    """油画/古画裂纹。"""
    if strength <= 0.004:
        return img
    w, h = img.size
    rng = random.Random(seed)
    layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    c = tuple(int(v) for v in color[:3]) + (int(min(1.0, strength) * 110),)
    for _ in range(int(26 * min(1.0, float(strength) * 2))):
        x, y = rng.uniform(0, w), rng.uniform(0, h)
        pts = [(x, y)]
        for _k in range(4):
            pts.append((pts[-1][0] + rng.uniform(-40, 40), pts[-1][1] + rng.uniform(-40, 40)))
        d.line(pts, fill=c, width=1)
    return _overlay(img, layer.filter(ImageFilter.GaussianBlur(0.4)))


def _brush_strokes(img: Image.Image, style: dict, strength: float, seed: int = 3) -> Image.Image:
    """油画/水墨笔触：短弧线叠加（取自 palette）。"""
    if strength <= 0.004:
        return img
    w, h = img.size
    rng = random.Random(seed)
    layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    colors = [palette_color(style, "fg")[:3], palette_color(style, "highlight")[:3],
              palette_color(style, "mid")[:3]]
    luma = img.convert("L").resize((max(16, w // 8), max(9, h // 8)), Image.BILINEAR)
    for _ in range(int(160 * min(1.0, float(strength) * 2.5))):
        x, y = rng.uniform(0, w), rng.uniform(0, h)
        px = min(luma.width - 1, max(0, int(x / 8)))
        py = min(luma.height - 1, max(0, int(y / 8)))
        bright = luma.getpixel((px, py))
        c = colors[1] if bright > 150 else (colors[2] if bright > 80 else colors[0])
        ln = rng.uniform(4, 18)
        ang = rng.uniform(-0.6, 0.6)
        d.line([(x, y), (x + math.cos(ang) * ln, y + math.sin(ang) * ln)],
               fill=tuple(int(v) for v in c) + (rng.randint(8, 30),),
               width=rng.randint(1, 3))
    return _overlay(img, layer.filter(ImageFilter.GaussianBlur(0.5)))


def _clay_shade(img: Image.Image, strength: float) -> Image.Image:
    """黏土柔光/微模糊（定格质感）。"""
    if strength <= 0.004:
        return img
    w, h = img.size
    blurred = img.filter(ImageFilter.GaussianBlur(max(0.6, min(w, h) * 0.0016)))
    out = Image.blend(img.convert("RGB"), blurred.convert("RGB"), float(strength) * 0.35)
    out = out.convert("RGBA")
    out.putalpha(img.getchannel("A"))
    return _vignette(out, float(strength) * 0.18, radius=0.5)


def _chalk_dust(img: Image.Image, strength: float, color=(240, 240, 235), seed: int = 4) -> Image.Image:
    if strength <= 0.004:
        return img
    w, h = img.size
    rng = random.Random(seed)
    layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    for _ in range(int(300 * min(1.0, float(strength) * 3))):
        x, y = rng.uniform(0, w), rng.uniform(0, h)
        r = rng.uniform(0.5, 2.4)
        d.ellipse((x - r, y - r, x + r, y + r),
                  fill=tuple(int(v) for v in color[:3]) + (rng.randint(20, 90),))
    return _overlay(img, layer.filter(ImageFilter.GaussianBlur(0.6)))


def _halftone_overlay(img: Image.Image, strength: float, color=(0, 0, 0),
                      spacing: int = 9) -> Image.Image:
    """半调网点叠加（拼贴/复古）。"""
    if strength <= 0.004:
        return img
    w, h = img.size
    luma = img.convert("L")
    small = luma.resize((max(4, w // spacing), max(4, h // spacing)), Image.BILINEAR)
    field = np.asarray(small, dtype=np.float32) / 255.0
    layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    a_base = int(min(1.0, float(strength)) * 170)
    r_max = spacing * 0.62
    c = tuple(int(v) for v in color[:3])
    for gy in range(field.shape[0]):
        for gx in range(field.shape[1]):
            r = r_max * (1.0 - float(field[gy, gx])) * 0.85
            if r < 0.6:
                continue
            cx = gx * spacing + spacing * 0.5
            cy = gy * spacing + spacing * 0.5
            d.ellipse((cx - r, cy - r, cx + r, cy + r), fill=c + (a_base,))
    return _overlay(img, layer)


def _duotone(img: Image.Image, dark_color, light_color) -> Image.Image:
    """亮度映射到双色渐变（蓝图/复印/黑白）。"""
    luma = img.convert("L")
    w, h = img.size
    dark = np.array(tuple(int(c) for c in dark_color[:3]), dtype=np.float32)
    light = np.array(tuple(int(c) for c in light_color[:3]), dtype=np.float32)
    t = (np.asarray(luma, dtype=np.float32) / 255.0)[..., None]
    arr = dark[None, None, :] * (1.0 - t) + light[None, None, :] * t
    out = Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8), "RGB").convert("RGBA")
    out.putalpha(img.getchannel("A"))
    return out


def _channel_shift(img: Image.Image, dx: int, dy: int = 0) -> Image.Image:
    """RGB 通道错位（印刷套色偏差 / VHS 色差）。"""
    if dx == 0 and dy == 0:
        return img
    r, g, b, a = img.split()
    r = ImageChops.offset(r, dx, dy)
    b = ImageChops.offset(b, -dx, -dy)
    out = Image.merge("RGBA", (r, g, b, a))
    return out


def _row_wobble(img: Image.Image, amp: float, freq: float = 0.06, seed: int = 2) -> Image.Image:
    """行抖动（VHS 磁带抖 / 胶片 gate weave）。"""
    if amp <= 0.05:
        return img
    arr = np.asarray(img, dtype=np.uint8)
    h = arr.shape[0]
    yy = np.arange(h, dtype=np.float32)
    offsets = np.sin(yy * freq + seed) * float(amp) + np.sin(yy * freq * 2.7) * float(amp) * 0.4
    out = np.empty_like(arr)
    for y in range(h):
        shift = int(round(float(offsets[y])))
        if shift == 0:
            out[y] = arr[y]
        else:
            out[y] = np.roll(arr[y], shift, axis=0)
    return Image.fromarray(out, "RGBA")


def _glitch_bands(img: Image.Image, strength: float, seed: int = 6) -> Image.Image:
    """故障带（全息/赛博）：随机横条错位 + 通道偏移。"""
    if strength <= 0.005:
        return img
    w, h = img.size
    rng = random.Random(seed)
    out = img.copy()
    for _ in range(int(3 + 9 * min(1.0, float(strength)))):
        y0 = rng.randint(0, h - 4)
        bh = rng.randint(2, max(3, int(h * 0.02)))
        shift = rng.randint(-int(w * 0.02), int(w * 0.02))
        band = img.crop((0, y0, w, min(h, y0 + bh)))
        if shift >= 0:
            band_img = ImageChops.offset(band, shift, 0)
        else:
            band_img = ImageChops.offset(band, shift, 0)
        out.paste(band_img, (0, y0), band_img)
    return _channel_shift(out, max(1, int(w * 0.0018 * min(3.0, 1 + strength))))


def _posterize(img: Image.Image, bits: int = 4) -> Image.Image:
    rgb = ImageOps.posterize(img.convert("RGB"), max(1, min(8, int(bits))))
    out = rgb.convert("RGBA")
    out.putalpha(img.getchannel("A"))
    return out


def _sharpen(img: Image.Image, amount: float = 1.0) -> Image.Image:
    if amount <= 0.01:
        return img
    rgb = img.convert("RGB").filter(ImageFilter.UnsharpMask(
        radius=2.0, percent=int(80 * min(3.0, float(amount))), threshold=3))
    out = rgb.convert("RGBA")
    out.putalpha(img.getchannel("A"))
    return out


def _emboss_relief(img: Image.Image, strength: float) -> Image.Image:
    """浮雕笔触（油画厚涂）。"""
    if strength <= 0.004:
        return img
    rgb = img.convert("RGB")
    emboss = rgb.filter(ImageFilter.EMBOSS).convert("L")
    emboss = emboss.point(lambda v: int(v * min(1.0, float(strength))))
    out_rgb = ImageChops.overlay(rgb, Image.merge("RGB", (emboss, emboss, emboss)))
    out = out_rgb.convert("RGBA")
    out.putalpha(img.getchannel("A"))
    return out


# ===========================================================================
# 3. 16 technique 的默认 finish 参数（style JSON 的 "finish" 可覆盖）
# ===========================================================================
_NEUTRAL_FINISH = {
    "brightness": 1.0, "contrast": 1.0, "saturation": 1.0,
    "temperature": 0.0, "tint": 0.0,
    "vignette": 0.0, "grain": 0.0, "scanlines": 0.0, "chroma_shift": 0.0,
    "bloom": 0.0, "paper": 0.0, "hatch": 0.0, "edge_ink": 0.0,
    "gold_foil": 0.0, "silk_sheen": 0.0, "crackle": 0.0, "brush_strokes": 0.0,
    "clay_shade": 0.0, "chalk_dust": 0.0, "canvas": 0.0, "halftone": 0.0,
    "posterize": 0.0, "glitch": 0.0, "wobble": 0.0, "emboss": 0.0,
    "sharpen": 0.0, "duotone": 0,
}

TECHNIQUE_FINISH: dict[str, dict] = {
    "ink_wash": {"brightness": 1.02, "contrast": 1.05, "saturation": 0.72,
                 "temperature": 0.05, "paper": 0.20, "edge_ink": 0.34,
                 "vignette": 0.18, "grain": 0.05, "bloom": 0.05, "hatch": 0.0},
    "woodblock": {"brightness": 1.03, "contrast": 1.16, "saturation": 0.82,
                  "temperature": 0.10, "paper": 0.24, "edge_ink": 0.30,
                  "vignette": 0.20, "grain": 0.04, "posterize": 0.0, "chroma_shift": 0.0},
    "parchment_engrave": {"brightness": 1.00, "contrast": 1.10, "saturation": 0.55,
                          "temperature": 0.16, "paper": 0.26, "hatch": 0.30,
                          "edge_ink": 0.24, "vignette": 0.30, "grain": 0.08,
                          "crackle": 0.10},
    "museum_gold": {"brightness": 0.98, "contrast": 1.18, "saturation": 1.05,
                    "temperature": 0.04, "vignette": 0.34, "bloom": 0.40,
                    "grain": 0.05, "gold_foil": 0.30, "sharpen": 0.35, "hatch": 0.0},
    "silk_gongbi": {"brightness": 1.03, "contrast": 1.02, "saturation": 0.94,
                    "temperature": 0.04, "silk_sheen": 0.26, "paper": 0.10,
                    "edge_ink": 0.12, "vignette": 0.14, "grain": 0.03},
    "neon_toon": {"brightness": 1.02, "contrast": 1.10, "saturation": 1.30,
                  "temperature": -0.04, "bloom": 0.50, "chroma_shift": 0.30,
                  "vignette": 0.22, "grain": 0.03, "sharpen": 0.30, "scanlines": 0.0},
    "hologram": {"brightness": 1.04, "contrast": 1.12, "saturation": 1.22,
                 "temperature": -0.10, "bloom": 0.42, "chroma_shift": 0.55,
                 "glitch": 0.30, "scanlines": 0.20, "vignette": 0.26, "grain": 0.04},
    "vhs": {"brightness": 1.03, "contrast": 1.05, "saturation": 0.96,
            "temperature": 0.16, "chroma_shift": 0.80, "wobble": 0.60,
            "scanlines": 0.28, "grain": 0.12, "vignette": 0.24, "bloom": 0.18},
    "film_grain": {"brightness": 1.02, "contrast": 1.05, "saturation": 0.92,
                   "temperature": 0.14, "grain": 0.14, "vignette": 0.24,
                   "bloom": 0.16, "wobble": 0.10, "hatch": 0.0},
    "noir": {"brightness": 0.94, "contrast": 1.34, "saturation": 0.14,
             "temperature": -0.14, "grain": 0.12, "vignette": 0.42, "bloom": 0.10},
    "oil_impasto": {"brightness": 1.00, "contrast": 1.12, "saturation": 1.06,
                    "temperature": 0.14, "emboss": 0.30, "brush_strokes": 0.30,
                    "canvas": 0.20, "vignette": 0.30, "crackle": 0.14, "grain": 0.04},
    "baroque_candle": {"brightness": 0.97, "contrast": 1.22, "saturation": 1.04,
                       "temperature": 0.22, "vignette": 0.46, "bloom": 0.34,
                       "grain": 0.10, "crackle": 0.06},
    "blueprint": {"brightness": 1.02, "contrast": 1.10, "saturation": 0.35,
                  "temperature": -0.18, "duotone": 1, "paper": 0.10,
                  "edge_ink": 0.20, "vignette": 0.26, "grain": 0.04},
    "collage_cutout": {"brightness": 1.03, "contrast": 1.10, "saturation": 1.28,
                       "temperature": 0.04, "paper": 0.18, "halftone": 0.16,
                       "vignette": 0.12, "grain": 0.03, "sharpen": 0.25},
    "claymation": {"brightness": 1.03, "contrast": 0.98, "saturation": 1.04,
                   "temperature": 0.10, "clay_shade": 0.35, "grain": 0.04,
                   "vignette": 0.22, "bloom": 0.10},
    "chalk_sketch": {"brightness": 1.03, "contrast": 1.14, "saturation": 0.42,
                     "temperature": -0.04, "chalk_dust": 0.30,
                     "edge_ink": 0.18, "grain": 0.06, "vignette": 0.26},
}

#: texture 键 → finish 参数（style.texture 自动映射）
TEXTURE_FINISH = {
    "paper_grain": {"paper": 0.14},
    "rice_paper": {"paper": 0.16},
    "canvas": {"canvas": 0.16},
    "scanline": {"scanlines": 0.16},
    "vhs_noise": {"scanlines": 0.12, "grain": 0.10},
    "halftone": {"halftone": 0.14},
    "clay_noise": {"clay_shade": 0.16},
    "chalk_dust": {"chalk_dust": 0.18},
    "grid": {},
    "bokeh": {"bloom": 0.12},
    "silk_weave": {"silk_sheen": 0.16},
    "ink_bleed": {"edge_ink": 0.10, "paper": 0.08},
    "wood_grain": {"paper": 0.10},
    "grain": {"grain": 0.08},
}


def finish_params(style: dict) -> dict:
    """合并 neutral 默认 + technique 默认 + texture 映射 + style.finish 覆盖。"""
    style = style if isinstance(style, dict) else {}
    params = dict(_NEUTRAL_FINISH)
    techniques = [str(t) for t in (style.get("technique") or [])]
    for tech in TECHNIQUES:
        if tech in techniques:
            params.update(TECHNIQUE_FINISH[tech])
    for texture in (style.get("texture") or []):
        params.update(TEXTURE_FINISH.get(str(texture), {}))
    override = style.get("finish")
    if isinstance(override, dict):
        params.update(override)
    return params


# ===========================================================================
# 4. 调色
# ===========================================================================
def _grade(img: Image.Image, params: dict, kind: str) -> Image.Image:
    """基础调色：明度/对比/饱和/色温/色调（保留 alpha）。"""
    scale = 0.72 if kind == "character" else 1.0
    rgb = img.convert("RGB")
    contrast = 1.0 + (float(params.get("contrast", 1.0)) - 1.0) * scale
    saturation = 1.0 + (float(params.get("saturation", 1.0)) - 1.0) * scale
    brightness = 1.0 + (float(params.get("brightness", 1.0)) - 1.0) * scale
    if abs(saturation - 1.0) > 1e-3:
        rgb = ImageEnhance.Color(rgb).enhance(saturation)
    if abs(contrast - 1.0) > 1e-3:
        rgb = ImageEnhance.Contrast(rgb).enhance(contrast)
    if abs(brightness - 1.0) > 1e-3:
        rgb = ImageEnhance.Brightness(rgb).enhance(brightness)
    temperature = float(params.get("temperature", 0.0)) * scale
    tint = float(params.get("tint", 0.0)) * scale
    if abs(temperature) > 0.01 or abs(tint) > 0.01:
        arr = np.asarray(rgb, dtype=np.float32)
        if abs(temperature) > 0.01:
            arr[..., 0] *= (1.0 + 0.20 * temperature)
            arr[..., 2] *= (1.0 - 0.20 * temperature)
            arr[..., 1] *= (1.0 + 0.04 * temperature)
        if abs(tint) > 0.01:
            arr[..., 1] *= (1.0 - 0.14 * tint)
            arr[..., 0] *= (1.0 + 0.05 * tint)
            arr[..., 2] *= (1.0 + 0.05 * tint)
        rgb = Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8), "RGB")
    out = rgb.convert("RGBA")
    out.putalpha(img.getchannel("A"))
    return out


def _technique_fx(img: Image.Image, style: dict, params: dict, tech: str,
                  kind: str) -> Image.Image:
    """单个 technique 的特征化特效（16 个分支，互不相同的画法）。"""
    scale = 0.6 if kind == "character" else 1.0
    fg = _rgb(style, "fg")
    accent = _rgb(style, "accent")
    highlight = _rgb(style, "highlight")
    paper = _rgb(style, "paper", fallback="bg")
    shadow = _rgb(style, "shadow", fallback="fg")
    w, h = img.size

    if tech == "ink_wash":
        img = _paper_texture(img, paper, float(params.get("paper", 0.18)) * scale,
                             seed=11, fibers=70)
        img = _edge_ink(img, float(params.get("edge_ink", 0.3)) * scale, color=fg, gain=2.4,
                        blur=0.6)
        # 淡彩晕染：低饱和色块叠加
        img = _brush_strokes(img, style, 0.06 * scale, seed=21)
        img = _bloom(img, threshold=205, radius=max(2, w * 0.008),
                     strength=0.10 * scale, tint=highlight)
    elif tech == "woodblock":
        img = _paper_texture(img, paper, float(params.get("paper", 0.22)) * scale,
                             seed=13, fibers=40)
        # 木刻：提高对比 + 边缘黑线 + 套色微偏差
        img = _edge_ink(img, float(params.get("edge_ink", 0.30)) * scale, color=fg, gain=2.8,
                        blur=0.4)
        img = _posterize(img, 5 if kind == "plate" else 6)
        if float(params.get("chroma_shift", 0.0)) > 0.01:
            img = _channel_shift(img, max(1, int(w * 0.0015)))
        # 纸黄染色
        tint_layer = Image.new("RGBA", img.size, _mix(paper, accent, 0.10)[:3] + (32,))
        img = _overlay(img, tint_layer)
    elif tech == "parchment_engrave":
        img = _paper_texture(img, paper, float(params.get("paper", 0.24)) * scale,
                             seed=17, fibers=30)
        img = _hatch_shadows(img, float(params.get("hatch", 0.28)) * scale,
                             spacing=max(3.0, min(w, h) * 0.006), color=shadow, angle=-42)
        img = _edge_ink(img, float(params.get("edge_ink", 0.22)) * scale, color=fg, gain=2.0)
        img = _vignette(img, float(params.get("vignette", 0.30)) * 0.8, color=shadow)
        img = _crackle(img, float(params.get("crackle", 0.08)) * scale, color=shadow)
    elif tech == "museum_gold":
        # 深黑 + 金色高光 + 聚光灯感
        img = _vignette(img, float(params.get("vignette", 0.34)), color=(0, 0, 0),
                        radius=0.62, power=1.8)
        img = _bloom(img, threshold=170, radius=max(4, w * 0.012),
                     strength=float(params.get("bloom", 0.4)), tint=accent)
        img = _gold_dust(img, float(params.get("gold_foil", 0.28)) * scale, color=accent)
        img = _sharpen(img, float(params.get("sharpen", 0.3)))
        img = _hatch_shadows(img, 0.04 * scale, spacing=max(4.0, min(w, h) * 0.01),
                             color=shadow, angle=-38)
    elif tech == "silk_gongbi":
        img = _silk_weave(img, float(params.get("silk_sheen", 0.24)) * scale)
        img = _paper_texture(img, paper, float(params.get("paper", 0.08)) * scale,
                             seed=19, fibers=20)
        img = _edge_ink(img, float(params.get("edge_ink", 0.12)) * scale, color=fg, gain=1.8,
                        blur=0.8)
        img = _bloom(img, threshold=215, radius=max(2, w * 0.006),
                     strength=0.10 * scale, tint=highlight)
    elif tech == "neon_toon":
        img = _bloom(img, threshold=150, radius=max(4, w * 0.012),
                     strength=float(params.get("bloom", 0.5)) * scale, tint=accent)
        if float(params.get("chroma_shift", 0.0)) > 0.01:
            img = _channel_shift(img, max(1, int(w * 0.0012 * float(params.get("chroma_shift", 1.0)))))
        img = _scanlines(img, float(params.get("scanlines", 0.0)) * scale, spacing=3)
        img = _edge_ink(img, 0.10 * scale, color=(8, 8, 16), gain=2.0)
        img = _sharpen(img, float(params.get("sharpen", 0.25)))
    elif tech == "hologram":
        img = _bloom(img, threshold=150, radius=max(4, w * 0.014),
                     strength=float(params.get("bloom", 0.4)), tint=accent)
        img = _scanlines(img, float(params.get("scanlines", 0.2)) * scale, spacing=3)
        img = _glitch_bands(img, float(params.get("glitch", 0.3)), seed=31)
        if float(params.get("chroma_shift", 0.0)) > 0.01:
            img = _channel_shift(img, max(1, int(w * 0.002)),
                                 dy=max(1, int(h * 0.001)))
        img = _vignette(img, float(params.get("vignette", 0.26)), color=(4, 4, 12))
    elif tech == "vhs":
        img = _row_wobble(img, float(params.get("wobble", 0.6)), freq=0.09, seed=41)
        img = _channel_shift(img, max(1, int(w * 0.0018)))
        img = _scanlines(img, float(params.get("scanlines", 0.26)) * scale, spacing=3,
                         color=(20, 10, 0))
        img = _grain(img, float(params.get("grain", 0.12)) * scale, seed=43,
                     color=(20, 0, 20), light=(255, 240, 220))
        img = _bloom(img, threshold=195, radius=max(3, w * 0.01), strength=0.15,
                     tint=(255, 220, 190))
    elif tech == "film_grain":
        img = _bloom(img, threshold=185, radius=max(4, w * 0.011),
                     strength=float(params.get("bloom", 0.16)) * scale, tint=(255, 226, 190))
        img = _grain(img, float(params.get("grain", 0.14)) * scale, seed=47,
                     color=(30, 20, 10), light=(255, 245, 230))
        img = _vignette(img, float(params.get("vignette", 0.24)), color=(24, 14, 8),
                        radius=0.68)
        img = _row_wobble(img, float(params.get("wobble", 0.1)), freq=0.03, seed=49)
    elif tech == "noir":
        img = _duotone(img, _mix((10, 12, 18), accent, 0.05),
                       _mix((235, 240, 245), highlight, 0.25))
        img = _bloom(img, threshold=175, radius=max(5, w * 0.014), strength=0.22,
                     tint=(200, 220, 255))
        img = _vignette(img, float(params.get("vignette", 0.42)), color=(0, 0, 0),
                        radius=0.55, power=1.5)
        img = _grain(img, float(params.get("grain", 0.12)) * scale, seed=53)
    elif tech == "oil_impasto":
        img = _canvas_texture(img, float(params.get("canvas", 0.2)) * scale, color=shadow)
        img = _emboss_relief(img, float(params.get("emboss", 0.3)) * scale)
        img = _brush_strokes(img, style, float(params.get("brush_strokes", 0.3)) * scale,
                             seed=57)
        img = _crackle(img, float(params.get("crackle", 0.14)) * scale, color=shadow)
        img = _vignette(img, float(params.get("vignette", 0.30)), color=(20, 10, 4))
    elif tech == "baroque_candle":
        img = _bloom(img, threshold=150, radius=max(5, w * 0.016),
                     strength=float(params.get("bloom", 0.34)), tint=(255, 200, 120))
        img = _vignette(img, float(params.get("vignette", 0.46)), color=(10, 6, 2),
                        radius=0.52, power=1.4)
        img = _grain(img, float(params.get("grain", 0.10)) * scale, seed=59,
                     color=(20, 10, 4), light=(255, 230, 190))
        img = _crackle(img, float(params.get("crackle", 0.06)) * scale, color=shadow)
    elif tech == "blueprint":
        # 双色蓝图 + 网格 + 技术线
        img = _duotone(img, (8, 20, 48), (222, 238, 255))
        img = _grid_overlay(img, accent=highlight, strength=0.10, spacing=max(10, int(w * 0.02)))
        img = _edge_ink(img, 0.16 * scale, color=highlight, gain=2.0)
        img = _vignette(img, float(params.get("vignette", 0.26)), color=(4, 10, 26))
    elif tech == "collage_cutout":
        img = _paper_texture(img, paper, float(params.get("paper", 0.16)) * scale,
                             seed=61, fibers=50)
        img = _halftone_overlay(img, float(params.get("halftone", 0.16)) * scale,
                                color=shadow, spacing=max(7, int(w * 0.0075)))
        img = _sharpen(img, float(params.get("sharpen", 0.25)))
        if kind == "character":
            img = _character_outline(img, style, width=max(2, int(min(w, h) * 0.012)),
                                     color=paper, style_kind="sticker")
    elif tech == "claymation":
        img = _clay_shade(img, float(params.get("clay_shade", 0.35)) * scale)
        img = _grain(img, float(params.get("grain", 0.04)) * scale, seed=67)
        img = _bloom(img, threshold=200, radius=max(3, w * 0.008), strength=0.12,
                     tint=(255, 235, 210))
    elif tech == "chalk_sketch":
        img = _chalk_dust(img, float(params.get("chalk_dust", 0.30)) * scale, seed=71)
        img = _edge_ink(img, float(params.get("edge_ink", 0.18)) * scale,
                        color=(245, 245, 240), gain=1.8, blur=0.5)
        img = _grain(img, float(params.get("grain", 0.06)) * scale, seed=73)
    return img


def _grid_overlay(img: Image.Image, accent, strength: float, spacing: int) -> Image.Image:
    w, h = img.size
    layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    a = int(min(1.0, float(strength)) * 255)
    c = tuple(int(v) for v in accent[:3]) + (a,)
    for x in range(0, w, max(4, int(spacing))):
        d.line([(x, 0), (x, h)], fill=c, width=1)
    for y in range(0, h, max(4, int(spacing))):
        d.line([(0, y), (w, y)], fill=c, width=1)
    return _overlay(img, layer)


def _character_outline(img: Image.Image, style: dict, width: int = 3,
                       color=None, style_kind: str = "") -> Image.Image:
    """角色描边/贴纸白边（只作用于 alpha 边缘）。"""
    alpha = img.getchannel("A")
    if not alpha.getbbox():
        return img
    grow = max(1, int(width))
    bigger = alpha.filter(ImageFilter.MaxFilter(grow * 2 + 1))
    border = ImageChops.subtract(bigger, alpha)
    if color is None:
        treatment = str(style.get("character_treatment") or "")
        if treatment in ("cutout_paper", "collage"):
            color = _rgb(style, "paper", fallback="bg")
        elif treatment in ("toon",):
            color = _rgb(style, "fg")
        else:
            color = _rgb(style, "fg")
    border_layer = scene_render.mask_layer(img.size, border,
                                           tuple(int(c) for c in color[:3]) + (255,))
    out = _rgba(img).copy()
    out.alpha_composite(border_layer)
    # 贴纸阴影
    if style_kind == "sticker":
        shadow_alpha = bigger.point(lambda v: int(v * 0.30))
        shadow = Image.new("RGBA", img.size, (0, 0, 0, 0))
        shadow.paste((0, 0, 0, 255), (max(1, grow // 2), max(1, grow // 2)), shadow_alpha)
        base = Image.new("RGBA", img.size, (0, 0, 0, 0))
        base.alpha_composite(shadow)
        base.alpha_composite(out)
        return base
    return out


def apply_finish(img: Image.Image, style: dict, kind: str = "plate") -> Image.Image:
    """风格后处理（plate=绘景 / character=角色立绘）。

    - 调色（finish brightness/contrast/saturation/temperature/tint）；
    - 16 technique 各自的特征特效（墨边/木刻/排线/金箔/丝绢/扫描线/故障/颗粒…）；
    - 通用叠加（paper/hatch/gold_foil/vignette/grain/bloom/…）；
    - kind="character" 时强度减半、并追加 character_treatment 描边。
    """
    if not isinstance(style, dict):
        raise V5Error("apply_finish 需要 style dict")
    if not isinstance(img, Image.Image):
        raise V5Error("apply_finish 需要 PIL.Image")
    kind = "character" if str(kind) == "character" else "plate"
    params = finish_params(style)
    source = img
    out = _rgba(img)
    started = time.perf_counter()

    out = _grade(out, params, kind)
    techniques = [str(t) for t in (style.get("technique") or [])]
    if not techniques:  # 从 style.id / texture 猜一个主 technique
        techniques = [t for t in TECHNIQUES if t in str(style.get("id") or "")]
    # V5.2：AI 素材走强化链路（plate 强处理 / character 中强度且脸安全）；
    # 程序化素材继续走旧强度（不改变旧路径观感）。
    ai_plate = kind == "plate" and bool(techniques) and looks_like_ai_art(source)
    if ai_plate:
        out = _ai_plate_finish(out, style, params, techniques)
        params = _ai_overlay_params(params)  # 强链路已含纹理/暗角/颗粒，避免双层叠加
    elif kind == "character" and techniques:
        out = _ai_character_finish(out, style, params, techniques)
    else:
        for tech in TECHNIQUES:
            if tech in techniques:
                out = _technique_fx(out, style, params, tech, kind)

    # ---- 通用叠加 ----
    scale = 0.55 if kind == "character" else 1.0
    paper_c = _rgb(style, "paper", fallback="bg")
    if float(params.get("paper", 0.0)) > 0.02 and kind == "plate":
        already_paper = any(t in ("ink_wash", "woodblock", "parchment_engrave",
                                  "collage_cutout", "blueprint") for t in techniques)
        if not already_paper:
            out = _paper_texture(out, paper_c, float(params["paper"]) * scale, seed=7)
    if float(params.get("canvas", 0.0)) > 0.02 and not any(
            t == "oil_impasto" for t in techniques):
        out = _canvas_texture(out, float(params["canvas"]) * scale, color=_rgb(style, "shadow"))
    if float(params.get("silk_sheen", 0.0)) > 0.02 and "silk_gongbi" not in techniques:
        out = _silk_weave(out, float(params["silk_sheen"]) * scale)
    if float(params.get("gold_foil", 0.0)) > 0.01 and "museum_gold" not in techniques:
        out = _gold_dust(out, float(params["gold_foil"]) * scale, color=_rgb(style, "accent"))
    if float(params.get("hatch", 0.0)) > 0.02 and "parchment_engrave" not in techniques:
        out = _hatch_shadows(out, float(params["hatch"]) * scale,
                             spacing=max(3.0, min(out.size) * 0.006),
                             color=_rgb(style, "shadow"))
    if float(params.get("edge_ink", 0.0)) > 0.02 and "ink_wash" not in techniques:
        out = _edge_ink(out, float(params["edge_ink"]) * scale, color=_rgb(style, "fg"))
    if float(params.get("bloom", 0.0)) > 0.01:
        already = any(t in ("neon_toon", "hologram", "museum_gold", "film_grain",
                            "baroque_candle") for t in techniques)
        if not already:
            out = _bloom(out, threshold=195, radius=max(3, out.size[0] * 0.01),
                         strength=float(params["bloom"]) * scale,
                         tint=_rgb(style, "highlight"))
    if float(params.get("scanlines", 0.0)) > 0.01 and not any(
            t in ("vhs", "hologram") for t in techniques):
        out = _scanlines(out, float(params["scanlines"]) * scale, spacing=3)
    if float(params.get("chroma_shift", 0.0)) > 0.01 and not any(
            t in ("vhs", "hologram", "neon_toon", "woodblock") for t in techniques):
        out = _channel_shift(out, max(1, int(out.size[0] * 0.0016)))
    if float(params.get("glitch", 0.0)) > 0.01 and "hologram" not in techniques:
        out = _glitch_bands(out, float(params["glitch"]) * scale, seed=29)
    if float(params.get("wobble", 0.0)) > 0.01 and not any(
            t in ("vhs", "film_grain") for t in techniques):
        out = _row_wobble(out, float(params["wobble"]) * scale, freq=0.07, seed=37)
    if float(params.get("halftone", 0.0)) > 0.01 and "collage_cutout" not in techniques:
        out = _halftone_overlay(out, float(params["halftone"]) * scale,
                                color=_rgb(style, "shadow"),
                                spacing=max(7, int(out.size[0] * 0.0075)))
    if float(params.get("chalk_dust", 0.0)) > 0.01 and "chalk_sketch" not in techniques:
        out = _chalk_dust(out, float(params["chalk_dust"]) * scale)
    if float(params.get("clay_shade", 0.0)) > 0.01 and "claymation" not in techniques:
        out = _clay_shade(out, float(params["clay_shade"]) * scale)
    if float(params.get("brush_strokes", 0.0)) > 0.01 and "oil_impasto" not in techniques:
        out = _brush_strokes(out, style, float(params["brush_strokes"]) * scale, seed=23)
    if float(params.get("crackle", 0.0)) > 0.01 and not any(
            t in ("oil_impasto", "parchment_engrave", "baroque_candle") for t in techniques):
        out = _crackle(out, float(params["crackle"]) * scale, color=_rgb(style, "shadow"))
    if str(params.get("duotone") or 0) not in ("0", 0, "", None) and "blueprint" not in techniques \
            and "noir" not in techniques:
        out = _duotone(out, _mix(_rgb(style, "bg"), (0, 0, 0), 0.3),
                       _mix(_rgb(style, "highlight"), (255, 255, 255), 0.4))
    if float(params.get("vignette", 0.0)) > 0.01:
        out = _vignette(out, float(params["vignette"]) * (0.7 if kind == "character" else 1.0),
                        color=_rgb(style, "shadow"), radius=0.66)
    if float(params.get("grain", 0.0)) > 0.01:
        out = _grain(out, float(params["grain"]) * scale, seed=13,
                     color=_rgb(style, "shadow"), light=_rgb(style, "highlight"))
    sharpen = float(params.get("sharpen", 0.0))
    if sharpen > 0.01 and "museum_gold" not in techniques:
        out = _sharpen(out, sharpen * scale)

    if kind == "character":
        treatment = str(style.get("character_treatment") or "")
        if treatment in ("ink_brush", "engrave", "gongbi"):
            out = _edge_ink(out, 0.30, color=_rgb(style, "fg"), gain=2.2, blur=0.4)
        elif treatment in ("cutout_paper", "collage"):
            out = _character_outline(out, style, width=max(2, int(min(out.size) * 0.012)),
                                     style_kind="sticker")
        elif treatment == "toon":
            out = _character_outline(out, style, width=max(2, int(min(out.size) * 0.010)))
        elif treatment == "clay":
            out = _character_outline(out, style, width=max(1, int(min(out.size) * 0.006)),
                                     color=_rgb(style, "shadow"))
        out.putalpha(source.getchannel("A"))
    # 保持原 mode（RGB 输入返回 RGB，RGBA 输入返回 RGBA）
    return out if source.mode == "RGBA" else out.convert("RGB")


# ===========================================================================
# 4b. V5.2 AI 素材风格化（AI plate / AI 立绘专用强处理）
# ---------------------------------------------------------------------------
# 背景：V5.2 起场景/角色换成 SDXL 动漫美术（assets/v5/...）。同一张 AI 素材如果
# 只走旧版轻微调色，16 个风格会像同一部片。本节给 AI plate 叠加**强影像处理**，
# 由 style.technique 分派；程序化 fallback 继续走 §4 的旧强度（行为不变）。
#   - 仅 plate 路径（AI 检测命中）启用强处理；角色立绘用中强度、脸/嘴安全版本；
#   - 全部随机量由 style.id 派生 seed（确定性：同素材+同风格=同输出）；
#   - 只读 style JSON 的 finish/palette，不新增契约字段。
# ===========================================================================
_V52_SEED = 20261026


def _stable_seed(style: dict, salt: str = "") -> int:
    """style.id 派生的确定性 seed（同风格同 salt 永远一致）。"""
    style = style if isinstance(style, dict) else {}
    key = f"{style.get('id') or style.get('style') or 'style'}|{salt}|{_V52_SEED}"
    return int(zlib.crc32(key.encode("utf-8")) & 0xFFFFFFFF)


def looks_like_ai_art(img: Image.Image) -> bool:
    """粗判是否 AI/SDXL 绘景（用于启用 V5.2 强风格化）。

    特征：160×90 缩略图的唯一色数 + 高频能量（相对高斯模糊的残差）+ 拉普拉斯均值。
    实测：AI plate uniq≈9k-13k / hf≈14-19 / lap≈26-33；程序化 plate uniq≈2.5k-6.5k /
    hf≈7.5-10.4 / lap≈14-18。阈值取中间，程序化路径维持旧强度。
    """
    try:
        small = img.convert("RGB").resize((160, 90), Image.BILINEAR)
        arr = np.asarray(small, dtype=np.float32)
        uniq = int(len(np.unique(np.asarray(small, dtype=np.uint8).reshape(-1, 3), axis=0)))
        blur = np.asarray(small.filter(ImageFilter.GaussianBlur(2.0)), dtype=np.float32)
        hf = float(np.abs(arr - blur).mean())
        luma = arr.mean(axis=2)
        lap = float(np.abs(4.0 * luma[1:-1, 1:-1] - luma[:-2, 1:-1] - luma[2:, 1:-1]
                           - luma[1:-1, :-2] - luma[1:-1, 2:]).mean())
    except Exception:  # noqa: BLE001 - 判定失败回退程序化路径
        return False
    return (uniq >= 8000 and hf >= 12.0) or (uniq >= 12000 and lap >= 24.0)


# ---- 通用影像工具（全部保持 alpha；确定性 seed 由调用方传入）-------
def _rgb_op(img: Image.Image, op) -> Image.Image:
    """对 RGB 做处理并保留原 alpha（RGB 输入原样返回 RGB）。"""
    has_alpha = img.mode == "RGBA"
    alpha = img.getchannel("A") if has_alpha else None
    rgb = op(img.convert("RGB"))
    if rgb.mode != "RGB":
        rgb = rgb.convert("RGB")
    if not has_alpha:
        return rgb
    out = rgb.convert("RGBA")
    out.putalpha(alpha)
    return out


def _duotone_rgb(rgb: Image.Image, dark, light, strength: float = 1.0) -> Image.Image:
    """RGB 图按亮度映射到双色渐变（strength<1 时与原图混合）。"""
    l = np.asarray(rgb.convert("L"), dtype=np.float32) / 255.0
    d = np.array(tuple(int(v) for v in dark[:3]), dtype=np.float32)
    h = np.array(tuple(int(v) for v in light[:3]), dtype=np.float32)
    arr = d[None, None, :] * (1.0 - l[..., None]) + h[None, None, :] * l[..., None]
    duo = Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8), "RGB")
    if strength >= 0.999:
        return duo
    return Image.blend(rgb, duo, max(0.0, min(1.0, float(strength))))


def _tone_map(img: Image.Image, dark, light, strength: float = 1.0) -> Image.Image:
    return _rgb_op(img, lambda rgb: _duotone_rgb(rgb, dark, light, strength))


def _curve_lut(img: Image.Image, contrast: float = 1.0, gamma: float = 1.0,
               shadows: float = 0.0, highlights: float = 1.0,
               pivot: float = 0.5) -> Image.Image:
    """LUT 曲线：对比/伽马/抬黑/压高光（一次 point 完成）。"""
    def op(rgb: Image.Image) -> Image.Image:
        x = np.arange(256, dtype=np.float32) / 255.0
        y = np.clip((x - pivot) * float(contrast) + pivot, 0.0, 1.0) ** float(gamma)
        y = y * float(highlights) + float(shadows) * (1.0 - y)
        lut = np.clip(y * 255.0, 0, 255).astype(np.uint8).tolist()
        return rgb.point(lut * 3)
    return _rgb_op(img, op)


def _limit_colors(img: Image.Image, colors: int = 6, blend: float = 0.6,
                  dither: bool = False) -> Image.Image:
    """平涂限色（木刻/拼贴）：中位切分量化后与��图混合。"""
    def op(rgb: Image.Image) -> Image.Image:
        method = Image.Dither.FLOYDSTEINBERG if dither else Image.Dither.NONE
        q = rgb.quantize(colors=max(2, int(colors)), method=Image.Quantize.MEDIANCUT,
                         dither=method).convert("RGB")
        return Image.blend(rgb, q, max(0.0, min(1.0, float(blend))))
    return _rgb_op(img, op)


def _luma_posterize(img: Image.Image, levels: int = 6, blend: float = 0.5) -> Image.Image:
    """按亮度色阶压平（水墨块面/版画），保留色相。"""
    def op(rgb: Image.Image) -> Image.Image:
        arr = np.asarray(rgb, dtype=np.float32)
        l = arr.mean(axis=2, keepdims=True) / 255.0
        steps = max(2, int(levels)) - 1
        q = np.round(l * steps) / steps
        out = np.clip(arr * (q / np.maximum(l, 1e-3)), 0, 255).astype(np.uint8)
        poster = Image.fromarray(out, "RGB")
        return Image.blend(rgb, poster, max(0.0, min(1.0, float(blend))))
    return _rgb_op(img, op)


def _split_tone(img: Image.Image, shadow_color, highlight_color,
                strength: float = 0.4) -> Image.Image:
    """分离色调：暗部→shadow_color，亮部→highlight_color。"""
    def op(rgb: Image.Image) -> Image.Image:
        arr = np.asarray(rgb, dtype=np.float32)
        l = (arr.mean(axis=2, keepdims=True) / 255.0)
        d = np.array(tuple(int(v) for v in shadow_color[:3]), dtype=np.float32)
        h = np.array(tuple(int(v) for v in highlight_color[:3]), dtype=np.float32)
        tone = d[None, None, :] * (1.0 - l) + h[None, None, :] * l
        s = max(0.0, min(1.0, float(strength)))
        out = arr * (1.0 - s) + tone * s
        return Image.fromarray(np.clip(out, 0, 255).astype(np.uint8), "RGB")
    return _rgb_op(img, op)


def _hard_edges(img: Image.Image, color, threshold: float = 24.0, thickness: int = 1,
                alpha: int = 220, seed: int = 0, blur: float = 0.4,
                jitter: float = 0.5) -> Image.Image:
    """粗黑刻线（版画/蚀刻）：阈值边缘 + 飞白抖动 + 加粗。"""
    def op(rgb: Image.Image) -> Image.Image:
        w, h = rgb.size
        luma = rgb.convert("L").filter(ImageFilter.GaussianBlur(max(0.0, float(blur))))
        edges = luma.filter(ImageFilter.FIND_EDGES).point(
            lambda v: 255 if v > float(threshold) else 0)
        if thickness > 1:
            edges = edges.filter(ImageFilter.MaxFilter(int(thickness) * 2 + 1))
        if jitter > 0.02:
            n = scene_render.smooth_noise(max(8, w // 4), max(8, h // 4),
                                          scale=max(3.0, min(w, h) * 0.035),
                                          seed=seed, octaves=3)
            n_img = Image.fromarray((np.clip(n, 0, 1) * 255).astype(np.uint8), "L").resize(
                (w, h), Image.BILINEAR)
            edges = ImageChops.multiply(
                edges, n_img.point(lambda v: int(90 + v * (165.0 / 255.0) * float(jitter))))
        layer = scene_render.mask_layer((w, h), edges,
                                        tuple(int(c) for c in color[:3]) + (int(alpha),))
        base = rgb.convert("RGBA")
        base.alpha_composite(layer)
        return base.convert("RGB")
    return _rgb_op(img, op)


def _edge_glow(img: Image.Image, color_a, color_b, threshold: float = 28.0,
               radius: float = 5.0, strength: float = 0.7) -> Image.Image:
    """霓虹描边光：边缘 → 双色泛光（glow + 硬芯）。"""
    def op(rgb: Image.Image) -> Image.Image:
        w, h = rgb.size
        luma = rgb.convert("L").filter(ImageFilter.GaussianBlur(0.5))
        edges = luma.filter(ImageFilter.FIND_EDGES).point(
            lambda v: 255 if v > float(threshold) else 0)
        soft = edges.filter(ImageFilter.GaussianBlur(max(1.0, float(radius)))).point(
            lambda v: int(v * min(1.0, float(strength))))
        base = rgb.convert("RGBA")
        base.alpha_composite(scene_render.mask_layer(
            (w, h), soft, tuple(int(c) for c in color_a[:3]) + (255,)))
        base.alpha_composite(scene_render.mask_layer(
            (w, h), edges, tuple(int(c) for c in color_b[:3]) + (200,)))
        return base.convert("RGB")
    return _rgb_op(img, op)


def _wet_bleed(img: Image.Image, color, strength: float = 0.6, seed: int = 0) -> Image.Image:
    """湿边/洇墨：边缘扩散到两侧，低饱和冷色晕开（水墨）。"""
    def op(rgb: Image.Image) -> Image.Image:
        w, h = rgb.size
        edges = rgb.convert("L").filter(ImageFilter.GaussianBlur(0.4)).filter(
            ImageFilter.FIND_EDGES)
        edges = edges.filter(ImageFilter.GaussianBlur(max(2.0, min(w, h) * 0.004)))
        edges = edges.point(lambda v: int(v * min(1.0, float(strength)) * 0.55))
        layer = scene_render.mask_layer((w, h), edges,
                                        tuple(int(c) for c in color[:3]) + (255,))
        base = rgb.convert("RGBA")
        base.alpha_composite(layer)
        return base.convert("RGB")
    return _rgb_op(img, op)


def _blinds(img: Image.Image, color, count: int = 9, strength: float = 0.35,
            seed: int = 0, angle: float = 12.0) -> Image.Image:
    """百叶窗硬光带（noir / baroque 用）。"""
    def op(rgb: Image.Image) -> Image.Image:
        w, h = rgb.size
        layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        draw = ImageDraw.Draw(layer)
        band = max(4.0, h / (count * 2.6))
        alpha = int(255 * max(0.0, min(0.9, float(strength))))
        step = h / float(max(1, count))
        for i in range(count):
            y = i * step + step * 0.22
            draw.line([(-w * 0.1, y - band * 0.5), (w * 1.1, y - band * 0.5 + math.tan(
                math.radians(angle)) * w)],
                fill=tuple(int(c) for c in color[:3]) + (alpha,), width=int(band))
        layer = layer.filter(ImageFilter.GaussianBlur(max(2.0, h * 0.006)))
        base = rgb.convert("RGBA")
        base.alpha_composite(layer)
        return base.convert("RGB")
    return _rgb_op(img, op)


def _candle_light(img: Image.Image, style: dict, strength: float = 0.8,
                  seed: int = 0) -> Image.Image:
    """烛光照明：上方暖色光晕 + 强暗角 + 暗部冷褐（baroque / 烛光室内）。"""
    def op(rgb: Image.Image) -> Image.Image:
        w, h = rgb.size
        warm = _rgb(style, "accent", fallback="highlight")
        glow = scene_render.glow_layer(
            (w, h), [(w * 0.5, h * 0.30, w * 0.42, h * 0.44,
                      max(0.0, min(1.5, float(strength))))],
            tuple(int(c) for c in warm[:3]) + (255,), power=1.7, downscale=6)
        base = rgb.convert("RGBA")
        base.alpha_composite(glow)
        out = base.convert("RGB")
        out = _duotone_rgb(out, _mix(_rgb(style, "shadow", fallback="bg"), (20, 10, 4), 0.5),
                           _mix(_rgb(style, "highlight", fallback="paper"),
                                (255, 214, 150), 0.5), 0.30)
        return out
    return _rgb_op(img, op)


def _spotlight(img: Image.Image, color, strength: float = 0.8,
               cx: float = 0.5, cy: float = 0.34) -> Image.Image:
    """博物馆聚光：中心提亮 + 四周压暗。"""
    def op(rgb: Image.Image) -> Image.Image:
        w, h = rgb.size
        glow = scene_render.glow_layer(
            (w, h), [(w * cx, h * cy, w * 0.40, h * 0.42,
                      max(0.0, min(1.5, float(strength))))],
            tuple(int(c) for c in color[:3]) + (255,), power=1.4, downscale=6)
        base = rgb.convert("RGBA")
        base.alpha_composite(glow)
        return base.convert("RGB")
    return _rgb_op(img, op)


def _tilt_blur(img: Image.Image, strength: float = 0.5) -> Image.Image:
    """移轴/黏土微缩感：上下轻模糊、中间清晰。"""
    def op(rgb: Image.Image) -> Image.Image:
        w, h = rgb.size
        blurred = rgb.filter(ImageFilter.GaussianBlur(max(1.0, min(w, h) * 0.012)))
        sw, sh = 64, 36
        yy = np.mgrid[0:sh, 0:sw][0].astype(np.float32) / max(1, sh - 1)
        mask = np.clip((np.abs(yy - 0.52) * 2.0 - 0.25) / 0.75, 0, 1) ** 1.3
        m = Image.fromarray((np.clip(mask, 0, 1) * 255
                             * max(0.0, min(1.0, float(strength)))).astype(np.uint8), "L")
        m = m.resize((w, h), Image.BILINEAR)
        return Image.composite(blurred, rgb, m)
    return _rgb_op(img, op)


def _chalkify(img: Image.Image, style: dict, seed: int = 0,
              strength: float = 0.75) -> Image.Image:
    """黑板粉笔：深色板面 + 白色粗糙粉笔线 + 粉笔灰。"""
    def op(rgb: Image.Image) -> Image.Image:
        w, h = rgb.size
        # 黑板底：无论 plate 多亮都压成深色板面（粉笔是“线”，不是“灰照片”）
        board = _mix((13, 23, 19), _rgb(style, "shadow", fallback="bg"), 0.22)
        l = np.asarray(rgb.convert("L"), dtype=np.float32) / 255.0
        tone = np.array(tuple(int(c) for c in board), dtype=np.float32)[None, None, :] \
            * (0.72 + 0.50 * (1.0 - l[..., None]))
        base = Image.fromarray(np.clip(tone, 0, 255).astype(np.uint8), "RGB")
        luma = rgb.convert("L").filter(ImageFilter.GaussianBlur(1.2))
        edges = luma.filter(ImageFilter.FIND_EDGES)
        strong = edges.point(lambda v: 255 if v > 62 else 0)
        weak = edges.point(lambda v: 120 if v > 40 else 0)
        rng = np.random.default_rng(int(seed) & 0xFFFFFFFF)
        noise = np.clip(rng.standard_normal((max(8, h // 3), max(8, w // 3)),
                                            dtype=np.float32) * 0.5 + 0.5, 0, 1)
        n_img = Image.fromarray((noise * 255).astype(np.uint8), "L").resize(
            (w, h), Image.BILINEAR)
        strong = ImageChops.multiply(strong, n_img.point(lambda v: int(130 + v * 0.45)))
        chalk = _mix((236, 236, 228), _rgb(style, "highlight", fallback="paper"), 0.45)
        out = base.convert("RGBA")
        out.alpha_composite(scene_render.mask_layer(
            (w, h), weak, tuple(int(c) for c in chalk[:3]) + (46,)))
        out.alpha_composite(scene_render.mask_layer(
            (w, h), strong, tuple(int(c) for c in chalk[:3]) + (232,)))
        out = _chalk_dust(out, 0.26 * float(strength), color=chalk,
                          seed=int(seed) & 0xFFFF)
        return out.convert("RGB")
    return _rgb_op(img, op)


def _blueprint_lineify(img: Image.Image, style: dict, seed: int = 0) -> Image.Image:
    """蓝图白线：深蓝底 + 亮度分层 + 白色技术线 + 网格。"""
    def op(rgb: Image.Image) -> Image.Image:
        w, h = rgb.size
        navy = _mix(_rgb(style, "bg", fallback="fg"), (6, 18, 48), 0.55)
        ice = _mix(_rgb(style, "highlight", fallback="paper"), (205, 232, 255), 0.6)
        base = _duotone_rgb(rgb, navy, _mix(navy, ice, 0.38), 0.9)
        arr = np.asarray(base, dtype=np.float32)
        layers = np.clip((arr.mean(axis=2) - 72.0) / 150.0, 0.0, 1.0)[..., None] * 0.28
        arr = arr + (np.array(tuple(int(c) for c in ice[:3]), dtype=np.float32)
                     [None, None, :] - arr) * layers
        base = Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8), "RGB")
        luma = base.convert("L").filter(ImageFilter.GaussianBlur(0.6))
        edges = luma.filter(ImageFilter.FIND_EDGES).point(lambda v: 255 if v > 20 else 0)
        edges = edges.point(lambda v: int(v * 0.85))
        out = base.convert("RGBA")
        out.alpha_composite(scene_render.mask_layer(
            (w, h), edges, tuple(int(c) for c in ice[:3]) + (205,)))
        # 技术网格
        _grid = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        gd = ImageDraw.Draw(_grid)
        step = max(24, int(min(w, h) * 0.045))
        for x in range(0, w, step):
            gd.line([(x, 0), (x, h)], fill=tuple(int(c) for c in ice[:3]) + (24,), width=1)
        for y in range(0, h, step):
            gd.line([(0, y), (w, y)], fill=tuple(int(c) for c in ice[:3]) + (24,), width=1)
        out.alpha_composite(_grid)
        return out.convert("RGB")
    return _rgb_op(img, op)


def _gold_accent(img: Image.Image, color, strength: float = 0.55,
                 threshold: float = 0.42, power: float = 1.3) -> Image.Image:
    """金色高光：只把亮部染成金色（暗部保持黑，博物馆金黑的关键）。"""
    def op(rgb: Image.Image) -> Image.Image:
        arr = np.asarray(rgb, dtype=np.float32)
        l = arr.mean(axis=2, keepdims=True) / 255.0
        mask = np.clip((l - float(threshold)) / max(1e-3, 1.0 - float(threshold)),
                       0.0, 1.0) ** float(power)
        gold = np.array(tuple(int(c) for c in color[:3]), dtype=np.float32)
        out = arr * (1.0 - float(strength) * mask) + gold[None, None, :] * (float(strength) * mask)
        return Image.fromarray(np.clip(out, 0, 255).astype(np.uint8), "RGB")
    return _rgb_op(img, op)


def _torn_border(img: Image.Image, style: dict, seed: int = 0,
                 width_ratio: float = 0.035) -> Image.Image:
    """拼贴撕纸边：四周不规则纸边 + 轻投影（collage）。"""
    def op(rgb: Image.Image) -> Image.Image:
        w, h = rgb.size
        rng = random.Random(int(seed) & 0x7FFFFFFF)
        paper = _rgb(style, "paper", fallback="bg")
        t = max(6.0, min(w, h) * float(width_ratio))
        mask = Image.new("L", (w, h), 255)
        md = ImageDraw.Draw(mask)
        # 内侧不规则撕边（把外圈 mask 掉一部分 → 形成纸边）
        teeth = 26
        for i in range(teeth + 1):
            x = i * w / max(1, teeth)
            y = rng.uniform(0.25, 1.25) * t
            md.polygon([(x - w / teeth * 0.7, 0), (x + w / teeth * 0.7, 0),
                        (x + w / teeth * 0.35, y), (x - w / teeth * 0.35, y * 0.8)], fill=0)
            md.polygon([(x - w / teeth * 0.7, h), (x + w / teeth * 0.7, h),
                        (x + w / teeth * 0.35, h - y), (x - w / teeth * 0.35, h - y * 0.8)],
                       fill=0)
        for i in range(teeth + 1):
            y = i * h / max(1, teeth)
            x = rng.uniform(0.25, 1.25) * t
            md.polygon([(0, y - h / teeth * 0.7), (0, y + h / teeth * 0.7),
                        (x, y + h / teeth * 0.35), (x * 0.8, y - h / teeth * 0.35)], fill=0)
            md.polygon([(w, y - h / teeth * 0.7), (w, y + h / teeth * 0.7),
                        (w - x, y + h / teeth * 0.35), (w - x * 0.8, y - h / teeth * 0.35)],
                       fill=0)
        border = scene_render.mask_layer(
            (w, h), ImageChops.invert(mask), tuple(int(c) for c in paper[:3]) + (248,))
        shadow = border.filter(ImageFilter.GaussianBlur(max(2.0, t * 0.35)))
        base = rgb.convert("RGBA")
        sh = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        sh.paste((0, 0, 0, 110), (int(t * 0.12), int(t * 0.16)),
                 shadow.getchannel("A"))
        base.alpha_composite(sh)
        base.alpha_composite(border)
        return base.convert("RGB")
    return _rgb_op(img, op)


def _film_frame(img: Image.Image, style: dict, seed: int = 0) -> Image.Image:
    """16mm 胶片边框：上下片框 + 两侧齿孔 + 划痕。"""
    def op(rgb: Image.Image) -> Image.Image:
        w, h = rgb.size
        rng = random.Random(int(seed) & 0x7FFFFFFF)
        frame = _mix(_rgb(style, "shadow", fallback="fg"), (8, 7, 6), 0.6)
        layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        d = ImageDraw.Draw(layer)
        side = max(7.0, w * 0.022)
        top = max(5.0, h * 0.030)
        d.rectangle((0, 0, side, h), fill=tuple(int(c) for c in frame[:3]) + (240,))
        d.rectangle((w - side, 0, w, h), fill=tuple(int(c) for c in frame[:3]) + (240,))
        d.rectangle((0, 0, w, top), fill=tuple(int(c) for c in frame[:3]) + (240,))
        d.rectangle((0, h - top, w, h), fill=tuple(int(c) for c in frame[:3]) + (240,))
        hole_w, hole_h = side * 0.48, max(3.0, h * 0.020)
        y = top + h * 0.03
        while y < h - top - hole_h:
            for x0 in (side * 0.26, w - side * 0.26 - hole_w):
                d.rounded_rectangle((x0, y, x0 + hole_w, y + hole_h),
                                    radius=int(hole_w * 0.25),
                                    fill=tuple(int(c) for c in
                                               _mix(rgb.getpixel((int(x0 + 1), int(y + 1))),
                                                    (235, 228, 210), 0.7)[:3]) + (215,))
            y += hole_h * 2.4
        for _ in range(10):
            x = rng.uniform(0, w)
            y0 = rng.uniform(0, h * 0.6)
            d.line([(x, y0), (x + rng.uniform(-2, 2), y0 + rng.uniform(h * 0.05, h * 0.3))],
                   fill=(235, 230, 215, rng.randint(12, 42)), width=1)
        base = rgb.convert("RGBA")
        base.alpha_composite(layer)
        return base.convert("RGB")
    return _rgb_op(img, op)


def _vhs_osd(img: Image.Image, style: dict, seed: int = 0) -> Image.Image:
    """VHS OSD：左上 ●REC + 右下时间码（低透明度，模拟录像带）。"""
    def op(rgb: Image.Image) -> Image.Image:
        w, h = rgb.size
        layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        d = ImageDraw.Draw(layer)
        red = (228, 62, 52)
        white = (238, 236, 226)
        dot = max(3, int(h * 0.011))
        x0, y0 = int(w * 0.035), int(h * 0.045)
        d.ellipse((x0, y0, x0 + dot, y0 + dot), fill=red + (215,))
        try:
            info = _font_paths()
            from PIL import ImageFont as _IF
            font = _IF.truetype(info["body"], max(10, int(h * 0.034)))
            small = _IF.truetype(info["body"], max(9, int(h * 0.026)))
        except Exception:  # noqa: BLE001
            font = small = None
        d.text((x0 + dot + 6, y0 - dot * 0.6), "REC", font=font, fill=white + (200,))
        code = f"PM {11 + (int(seed) % 5)}:{32 + (int(seed) % 20)}:0{int(seed) % 9}"
        d.text((w - int(w * 0.04), h - int(h * 0.065)), code, font=small,
               fill=white + (185,), anchor="ra")
        base = rgb.convert("RGBA")
        base.alpha_composite(layer)
        return base.convert("RGB")
    return _rgb_op(img, op)


def _chroma_bleed(img: Image.Image, amount: float = 0.5) -> Image.Image:
    """色度横向拖尾（VHS/录像带）：R/B 通道横向模糊。"""
    def op(rgb: Image.Image) -> Image.Image:
        w, h = rgb.size
        a = max(0.0, min(1.0, float(amount)))
        r, g, bch = rgb.split()
        k = max(2, int(w * 0.004))
        r = Image.blend(r, r.filter(ImageFilter.GaussianBlur(k)), a * 0.8)
        bch = Image.blend(bch, bch.filter(ImageFilter.GaussianBlur(k)), a * 0.8)
        return Image.merge("RGB", (r, g, bch))
    return _rgb_op(img, op)


def _draw_seal(img: Image.Image, style: dict, ratio: float = 0.075,
               pos=(0.065, 0.845), seed: int = 0) -> Image.Image:
    """朱印点缀（水墨/版刻）：风格 accent 方印 + 反白刻字 + 磨损。"""
    def op(rgb: Image.Image) -> Image.Image:
        w, h = rgb.size
        ss = max(16, int(min(w, h) * float(ratio)))
        small = Image.new("RGBA", (ss, ss), (0, 0, 0, 0))
        sd = ImageDraw.Draw(small)
        accent = _rgb(style, "accent", fallback="seal")
        paper = _rgb(style, "paper", fallback="bg")
        pad = max(1, int(ss * 0.06))
        sd.rectangle((pad, pad, ss - pad, ss - pad),
                     fill=tuple(int(c) for c in accent[:3]) + (232,))
        sd.rectangle((pad * 2, pad * 2, ss - pad * 2, ss - pad * 2),
                     outline=tuple(int(c) for c in paper[:3]) + (230,),
                     width=max(1, int(ss * 0.035)))
        text = "史"
        pack = style.get("packaging") if isinstance(style.get("packaging"), dict) else {}
        if pack.get("seal"):
            text = str(pack["seal"])[:1]
        try:
            info = _font_paths()
            from PIL import ImageFont as _IF
            font = _IF.truetype(info["display"], max(10, int(ss * 0.62)))
        except Exception:  # noqa: BLE001
            font = None
        try:
            sd.text((ss / 2, ss / 2 + ss * 0.02), text, font=font,
                    fill=tuple(int(c) for c in paper[:3]) + (245,), anchor="mm")
        except Exception:  # noqa: BLE001
            pass
        rng = random.Random(int(seed) & 0x7FFFFFFF)
        for _ in range(max(4, int(ss * 0.4))):
            x, y = rng.uniform(0, ss), rng.uniform(0, ss)
            rr = max(0.6, ss * rng.uniform(0.006, 0.02))
            sd.ellipse((x - rr, y - rr, x + rr, y + rr),
                       fill=tuple(int(c) for c in paper[:3]) + (rng.randint(50, 170),))
        angle = -6 + (int(seed) % 9)
        small = small.rotate(angle, expand=True, resample=Image.BICUBIC)
        px = int(round(w * float(pos[0]) - small.width / 2.0))
        py = int(round(h * float(pos[1]) - small.height / 2.0))
        shadow_alpha = small.getchannel("A").point(lambda v: int(v * 0.20))
        sh = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        sh.paste((0, 0, 0, 255), (px + 2, py + 3), shadow_alpha)
        base = rgb.convert("RGBA")
        base.alpha_composite(sh)
        base.alpha_composite(small, (px, py))
        return base.convert("RGB")
    return _rgb_op(img, op)


# ---- AI plate 的 16 风格强处理链 -------------------------------------------
#: 强链路已覆盖的通用项；主流程据此把旧版通用叠加的参数清零，避免双层叠加。
_AI_HANDLED_KEYS = (
    "paper", "canvas", "hatch", "edge_ink", "bloom", "scanlines", "chroma_shift",
    "glitch", "wobble", "halftone", "chalk_dust", "clay_shade", "brush_strokes",
    "crackle", "silk_sheen", "gold_foil", "emboss", "duotone",
)


def _ai_overlay_params(params: dict) -> dict:
    """AI 强链路后，旧版通用叠加只保留很轻的暗角/颗粒/锐化。"""
    out = dict(params or {})
    for key in _AI_HANDLED_KEYS:
        out[key] = 0
    out["vignette"] = float(out.get("vignette", 0.0)) * 0.45
    out["grain"] = float(out.get("grain", 0.0)) * 0.55
    return out


def _ai_plate_finish(img: Image.Image, style: dict, params: dict,
                     techniques: list[str]) -> Image.Image:
    """V5.2：AI plate 的强风格化（按第一 technique 分派；保证 16 风格肉眼可分）。"""
    tech = techniques[0] if techniques else _primary_technique(style)
    seed = _stable_seed(style, f"plate:{tech}")
    fg = _rgb(style, "fg")
    accent = _rgb(style, "accent")
    highlight = _rgb(style, "highlight", fallback="paper")
    paper = _rgb(style, "paper", fallback="bg")
    shadow = _rgb(style, "shadow", fallback="fg")
    bg = _rgb(style, "bg")
    mid = _rgb(style, "mid", fallback="fg")
    out = img
    if tech == "ink_wash":
        # 冷灰墨 + 湿边/飞白 + 宣纸 + 朱印
        out = _tone_map(out, _mix(fg, (18, 24, 28), 0.55), _mix(paper, (250, 249, 242), 0.5),
                        0.62)
        out = _rgb_op(out, lambda rgb: ImageEnhance.Color(rgb).enhance(0.30))
        out = _curve_lut(out, contrast=1.06, gamma=1.0, shadows=0.04, highlights=0.95)
        out = _luma_posterize(out, levels=7, blend=0.42)
        out = _wet_bleed(out, _mix(fg, (78, 96, 104), 0.55), 0.75, seed)
        out = _paper_texture(out, paper, 0.20, seed=seed & 0xFFFF, fibers=120)
        out = _hard_edges(out, _mix(fg, (26, 30, 34), 0.55), threshold=24.0,
                          thickness=1, alpha=150, seed=seed & 0xFFFF, jitter=0.8)
        out = _draw_seal(out, style, 0.078, (0.072, 0.862), seed)
        out = _vignette(out, 0.20, color=_mix(shadow, (40, 48, 52), 0.5))
        out = _grain(out, 0.030, seed=seed & 0xFFFF, color=_mix(shadow, (60, 70, 76), 0.4),
                     light=paper)
    elif tech == "woodblock":
        # 高对比黑刻线 + 平涂限色 + 纸黄
        out = _rgb_op(out, lambda rgb: ImageEnhance.Color(rgb).enhance(0.62))
        out = _limit_colors(out, 5, 0.58)
        out = _curve_lut(out, contrast=1.22, gamma=0.95, shadows=0.02, highlights=0.98)
        out = _paper_texture(out, paper, 0.20, seed=seed & 0xFFFF, fibers=50)
        out = _rgb_op(out, lambda rgb: Image.blend(
            rgb, Image.new("RGB", rgb.size, _mix(paper, accent, 0.14)), 0.16))
        out = _hard_edges(out, _mix(fg, (18, 12, 8), 0.6), threshold=20.0,
                          thickness=2, alpha=235, seed=seed & 0xFFFF, jitter=0.45)
        out = _vignette(out, 0.16, color=shadow)
        out = _grain(out, 0.035, seed=seed & 0xFFFF, color=shadow, light=paper)
    elif tech == "parchment_engrave":
        # 做旧褐 + 铜版排线/交叉网点 + 档案暗角
        out = _tone_map(out, _mix(shadow, (46, 30, 16), 0.66),
                        _mix(paper, (236, 206, 152), 0.55), 0.86)
        out = _rgb_op(out, lambda rgb: ImageEnhance.Color(rgb).enhance(0.30))
        out = _curve_lut(out, contrast=1.16, gamma=1.02, shadows=0.02, highlights=0.97)
        out = _hatch_shadows(out, 0.42, spacing=max(3.0, min(out.size) * 0.0044),
                             color=_mix(shadow, (66, 42, 22), 0.55), angle=-42.0)
        out = _hatch_shadows(out, 0.26, spacing=max(4.0, min(out.size) * 0.0068),
                             color=_mix(shadow, (88, 58, 30), 0.45), angle=48.0)
        out = _paper_texture(out, paper, 0.24, seed=seed & 0xFFFF, fibers=40)
        out = _edge_ink(out, 0.20, color=_mix(fg, (56, 38, 22), 0.4), gain=1.9)
        out = _crackle(out, 0.14, color=_mix(shadow, (56, 36, 20), 0.5),
                       seed=seed & 0xFFFF)
        out = _vignette(out, 0.42, color=_mix(shadow, (40, 26, 14), 0.4))
        out = _grain(out, 0.07, seed=seed & 0xFFFF)
    elif tech == "museum_gold":
        # 黑金 + 聚光 + 强暗角：先压成“黑底”，只给亮部上金（否则像普通金色场景）
        out = _curve_lut(out, contrast=1.24, gamma=1.92, shadows=0.0, highlights=1.0)
        out = _rgb_op(out, lambda rgb: ImageEnhance.Color(rgb).enhance(0.26))
        out = _gold_accent(out, _mix(accent, (255, 228, 162), 0.45), 0.66, 0.36, 1.2)
        out = _split_tone(out, (1, 1, 7), _mix(accent, highlight, 0.3), 0.26)
        out = _spotlight(out, _mix(accent, (255, 228, 168), 0.5), 0.85, 0.50, 0.32)
        out = _bloom(out, threshold=145, radius=max(4, out.size[0] * 0.014),
                     strength=0.60, tint=accent)
        out = _gold_dust(out, 0.42, color=accent, seed=(seed + 7) & 0xFFFF)
        out = _vignette(out, 0.78, color=(0, 0, 2), radius=0.38, power=1.55)
        out = _sharpen(out, 0.42)
        out = _grain(out, 0.040, seed=seed & 0xFFFF)
    elif tech == "neon_toon":
        # 霓虹 glow + 青洋红偏移 + 扫描线（暗部偏青、亮部偏洋红）
        out = _rgb_op(out, lambda rgb: ImageEnhance.Color(rgb).enhance(1.44))
        out = _split_tone(out, (10, 54, 96), (255, 66, 206), 0.34)
        out = _bloom(out, threshold=132, radius=max(4, out.size[0] * 0.015),
                     strength=0.70, tint=accent)
        out = _edge_glow(out, _mix(mid, (40, 220, 255), 0.6),
                         _mix(accent, (255, 60, 210), 0.5), threshold=26.0,
                         radius=max(3.0, out.size[0] * 0.007), strength=0.75)
        out = _channel_shift(out, max(2, int(out.size[0] * 0.0022)))
        out = _scanlines(out, 0.06, spacing=3)
        out = _vignette(out, 0.26, color=(4, 4, 12))
        out = _grain(out, 0.025, seed=seed & 0xFFFF)
    elif tech == "hologram":
        # 全息粒子网格 + 故障带 + 扫描光
        out = _tone_map(out, _mix(bg, (4, 10, 30), 0.5), _mix(mid, (150, 235, 255), 0.7), 0.62)
        out = _grid_overlay(out, accent=_mix(mid, (120, 220, 255), 0.5),
                            strength=0.10, spacing=max(18, int(out.size[0] * 0.035)))
        out = _scanlines(out, 0.22, spacing=3)
        out = _glitch_bands(out, 0.36, seed=seed & 0xFFFF)
        out = _channel_shift(out, max(2, int(out.size[0] * 0.003)),
                             dy=max(1, int(out.size[1] * 0.0012)))
        out = _bloom(out, threshold=145, radius=max(5, out.size[0] * 0.016),
                     strength=0.52, tint=_mix(accent, (120, 230, 255), 0.5))
        out = _vignette(out, 0.30, color=(4, 4, 16))
    elif tech == "vhs":
        # 扫描线 + 色差 + 磁带走带 + REC OSD（暖洋红 + 强 scanline）
        out = _rgb_op(out, lambda rgb: ImageEnhance.Color(rgb).enhance(1.02))
        out = _chroma_bleed(out, 0.85)
        out = _rgb_op(out, lambda rgb: Image.blend(
            rgb, Image.new("RGB", rgb.size, (176, 38, 132)), 0.11))
        out = _split_tone(out, (26, 2, 30), (255, 214, 188), 0.26)
        out = _channel_shift(out, max(4, int(out.size[0] * 0.0042)))
        out = _row_wobble(out, 1.1, freq=0.10, seed=seed & 0xFFFF)
        out = _scanlines(out, 0.44, spacing=3, color=(24, 8, 0))
        out = _bloom(out, threshold=180, radius=max(3, out.size[0] * 0.01),
                     strength=0.18, tint=(255, 225, 195))
        out = _vhs_osd(out, style, seed)
        out = _grain(out, 0.15, seed=seed & 0xFFFF, color=(24, 0, 22),
                     light=(255, 242, 226))
        out = _vignette(out, 0.26, color=(20, 8, 4))
    elif tech == "film_grain":
        # 16mm 颗粒 + 暖调 + 片框 + 轻晃
        out = _split_tone(out, _mix(bg, (24, 14, 8), 0.4), (255, 232, 198), 0.34)
        out = _bloom(out, threshold=175, radius=max(4, out.size[0] * 0.012),
                     strength=0.20, tint=(255, 226, 190))
        out = _row_wobble(out, 0.14, freq=0.03, seed=seed & 0xFFFF)
        out = _film_frame(out, style, seed)
        out = _grain(out, 0.15, seed=seed & 0xFFFF, color=(28, 18, 10),
                     light=(255, 246, 232))
        out = _vignette(out, 0.28, color=(22, 12, 6))
    elif tech == "noir":
        # 高反差去色 + 百叶硬光 + 强暗角
        out = _rgb_op(out, lambda rgb: ImageEnhance.Color(rgb).enhance(0.10))
        out = _curve_lut(out, contrast=1.46, gamma=0.88, shadows=0.0, highlights=1.0)
        out = _blinds(out, (0, 0, 0), count=9, strength=0.34, seed=seed & 0xFFFF)
        out = _split_tone(out, _mix(bg, (2, 6, 14), 0.5), (232, 240, 246), 0.30)
        out = _bloom(out, threshold=175, radius=max(5, out.size[0] * 0.014),
                     strength=0.20, tint=(190, 215, 255))
        out = _vignette(out, 0.56, color=(0, 0, 0), radius=0.48, power=1.5)
        out = _grain(out, 0.13, seed=seed & 0xFFFF)
    elif tech == "oil_impasto":
        # 厚涂笔触 + 色斑 + 画布 + 清漆暖调（提亮成“上光油画”，避免与黑金混淆）
        out = _limit_colors(out, 10, 0.26)
        out = _canvas_texture(out, 0.24, color=_mix(shadow, (60, 40, 20), 0.4))
        out = _emboss_relief(out, 0.40)
        out = _brush_strokes(out, style, 0.50, seed=seed & 0xFFFF)
        out = _curve_lut(out, contrast=1.04, gamma=0.90, shadows=0.02, highlights=1.06)
        out = _rgb_op(out, lambda rgb: ImageEnhance.Color(rgb).enhance(1.16))
        out = _split_tone(out, _mix(bg, (54, 32, 12), 0.4), (255, 232, 186), 0.32)
        out = _crackle(out, 0.14, color=_mix(shadow, (70, 48, 24), 0.5),
                       seed=seed & 0xFFFF)
        out = _vignette(out, 0.22, color=(24, 12, 4))
        out = _sharpen(out, 0.34)
    elif tech == "baroque_candle":
        # 烛光暖暗 + 明暗对照（压得比 oil 更深更暖，暗部冷褐）
        out = _candle_light(out, style, 1.05, seed)
        out = _curve_lut(out, contrast=1.22, gamma=1.20, shadows=0.0, highlights=1.0)
        out = _split_tone(out, _mix(bg, (26, 4, 6), 0.55), (255, 196, 116), 0.58)
        out = _blinds(out, (0, 0, 0), count=5, strength=0.28, seed=seed & 0xFFFF,
                      angle=42.0)
        out = _bloom(out, threshold=150, radius=max(5, out.size[0] * 0.018),
                     strength=0.50, tint=(255, 192, 112))
        out = _vignette(out, 0.60, color=(12, 3, 4), radius=0.46, power=1.45)
        out = _crackle(out, 0.08, color=_mix(shadow, (52, 32, 12), 0.5),
                       seed=seed & 0xFFFF)
        out = _grain(out, 0.080, seed=seed & 0xFFFF, color=(22, 10, 4),
                     light=(255, 226, 180))
    elif tech == "blueprint":
        # 蓝图白线 + 技术网格
        out = _blueprint_lineify(out, style, seed)
        out = _vignette(out, 0.28, color=(2, 8, 22))
        out = _grain(out, 0.035, seed=seed & 0xFFFF)
    elif tech == "collage_cutout":
        # 限色平涂 + 半调网点 + 撕纸边
        out = _rgb_op(out, lambda rgb: ImageEnhance.Color(rgb).enhance(1.32))
        out = _limit_colors(out, 8, 0.46)
        out = _paper_texture(out, paper, 0.16, seed=seed & 0xFFFF, fibers=70)
        out = _halftone_overlay(out, 0.20, color=_mix(shadow, (40, 30, 24), 0.4),
                                spacing=max(7, int(out.size[0] * 0.0075)))
        out = _torn_border(out, style, seed)
        out = _sharpen(out, 0.22)
        out = _vignette(out, 0.12, color=shadow)
    elif tech == "claymation":
        # 黏土颗粒 + 柔焦 + 微缩移轴（提亮成柔嫩手作感，避免与暗色系混淆）
        out = _limit_colors(out, 12, 0.30)
        out = _curve_lut(out, contrast=0.98, gamma=0.88, shadows=0.02, highlights=1.12)
        out = _rgb_op(out, lambda rgb: ImageEnhance.Color(rgb).enhance(1.08))
        out = _clay_shade(out, 0.40)
        out = _tilt_blur(out, 0.52)
        out = _split_tone(out, _mix(bg, (80, 62, 44), 0.4), (255, 242, 220), 0.24)
        out = _bloom(out, threshold=188, radius=max(3, out.size[0] * 0.01),
                     strength=0.20, tint=(255, 238, 216))
        out = _grain(out, 0.04, seed=seed & 0xFFFF)
        out = _vignette(out, 0.12, color=_mix(shadow, (70, 52, 36), 0.4))
    elif tech == "chalk_sketch":
        # 黑板粉笔 + 白线 + 粉笔灰（强制深色板面，避免变成浅灰素描）
        out = _chalkify(out, style, seed, 0.92)
        out = _vignette(out, 0.30, color=(2, 6, 4))
        out = _grain(out, 0.05, seed=seed & 0xFFFF)
    elif tech == "silk_gongbi":
        # 绢本淡彩：整体提亮/降饱和成“淡绢”，与暖色黏土/油画拉开
        out = _rgb_op(out, lambda rgb: ImageEnhance.Color(rgb).enhance(0.58))
        out = _curve_lut(out, contrast=0.98, gamma=0.86, shadows=0.02, highlights=1.12)
        out = _tone_map(out, _mix(shadow, (92, 82, 68), 0.40), (252, 246, 232), 0.46)
        out = _silk_weave(out, 0.38)
        out = _paper_texture(out, paper, 0.10, seed=seed & 0xFFFF, fibers=34)
        out = _edge_ink(out, 0.10, color=_mix(fg, (52, 38, 24), 0.35), gain=1.7,
                        blur=0.6)
        out = _bloom(out, threshold=205, radius=max(2, out.size[0] * 0.008),
                     strength=0.14, tint=highlight)
        out = _vignette(out, 0.12, color=_mix(shadow, (70, 54, 36), 0.30))
        out = _grain(out, 0.020, seed=seed & 0xFFFF)
    return out


def _ai_character_finish(img: Image.Image, style: dict, params: dict,
                         techniques: list[str]) -> Image.Image:
    """V5.2：AI 立绘的中强度风格化（脸/嘴安全，不糊五官，保留 alpha）。"""
    treatment = str(style.get("character_treatment") or "")
    tech = techniques[0] if techniques else _primary_technique(style)
    seed = _stable_seed(style, f"char:{treatment or tech}")
    fg = _rgb(style, "fg")
    accent = _rgb(style, "accent")
    paper = _rgb(style, "paper", fallback="bg")
    shadow = _rgb(style, "shadow", fallback="fg")
    out = img
    if treatment in ("ink_brush",) or tech == "ink_wash":
        out = _rgb_op(out, lambda rgb: ImageEnhance.Color(rgb).enhance(0.34))
        out = _tone_map(out, _mix(fg, (20, 26, 32), 0.55),
                        _mix(paper, (246, 244, 236), 0.45), 0.58)
        out = _paper_texture(out, paper, 0.14, seed=seed & 0xFFFF, fibers=60)
        out = _edge_ink(out, 0.36, color=_mix(fg, (32, 38, 44), 0.45), gain=2.2, blur=0.35)
    elif treatment in ("cutout_paper", "collage") or tech == "collage_cutout":
        out = _rgb_op(out, lambda rgb: ImageEnhance.Color(rgb).enhance(1.32))
        out = _limit_colors(out, 7, 0.52)
        out = _paper_texture(out, paper, 0.10, seed=seed & 0xFFFF, fibers=40)
        out = _hard_edges(out, _mix(fg, (16, 12, 8), 0.6), threshold=22.0,
                          thickness=2, alpha=210, seed=seed & 0xFFFF, jitter=0.4)
        out = _halftone_overlay(out, 0.14, color=_mix(shadow, (40, 30, 24), 0.4),
                                spacing=max(6, int(out.size[0] * 0.012)))
    elif treatment in ("engrave",) or tech == "parchment_engrave":
        out = _tone_map(out, _mix(shadow, (48, 32, 18), 0.6),
                        _mix(paper, (230, 202, 156), 0.5), 0.74)
        out = _rgb_op(out, lambda rgb: ImageEnhance.Color(rgb).enhance(0.34))
        out = _hatch_shadows(out, 0.30, spacing=max(3.0, min(out.size) * 0.0052),
                             color=_mix(shadow, (76, 52, 28), 0.55), angle=-42.0)
        out = _hatch_shadows(out, 0.18, spacing=max(4.0, min(out.size) * 0.0075),
                             color=_mix(shadow, (96, 66, 34), 0.45), angle=48.0)
        out = _edge_ink(out, 0.20, color=_mix(fg, (56, 40, 24), 0.4), gain=1.9)
    elif treatment in ("gold_silhouette", "candle") or tech in ("museum_gold", "baroque_candle"):
        out = _curve_lut(out, contrast=1.14, gamma=1.35)
        out = _rgb_op(out, lambda rgb: ImageEnhance.Color(rgb).enhance(0.42))
        out = _gold_accent(out, _mix(accent, (255, 218, 146), 0.5), 0.52, 0.20, 1.1)
        out = _split_tone(out, _mix(shadow, (4, 4, 6), 0.55),
                          _mix(accent, (255, 218, 146), 0.55), 0.40)
        out = _edge_glow(out, _mix(accent, (255, 200, 120), 0.4), accent,
                         threshold=58.0, radius=max(2.0, out.size[0] * 0.010),
                         strength=0.55)
        out = _bloom(out, threshold=158, radius=max(3, out.size[0] * 0.012),
                     strength=0.36, tint=accent)
        out = _gold_dust(out, 0.13, color=accent, seed=seed & 0xFFFF)
        out = _vignette(out, 0.32, color=(0, 0, 0))
    elif treatment in ("clay",) or tech == "claymation":
        out = _curve_lut(out, contrast=0.96, gamma=0.86, highlights=1.14)
        out = _rgb_op(out, lambda rgb: ImageEnhance.Color(rgb).enhance(1.16))
        out = _clay_shade(out, 0.44)
        out = _limit_colors(out, 10, 0.30)
        out = _split_tone(out, _mix(shadow, (88, 68, 46), 0.4), (255, 244, 222), 0.26)
        out = _bloom(out, threshold=192, radius=max(3, out.size[0] * 0.01),
                     strength=0.20, tint=(255, 238, 216))
    elif treatment in ("toon",) or tech == "neon_toon":
        out = _rgb_op(out, lambda rgb: ImageEnhance.Color(rgb).enhance(1.42))
        out = _split_tone(out, (12, 60, 104), (255, 70, 208), 0.28)
        out = _bloom(out, threshold=155, radius=max(3, out.size[0] * 0.012),
                     strength=0.40, tint=accent)
        out = _edge_glow(out, _mix(accent, (255, 80, 200), 0.4),
                         _mix(accent, (80, 230, 255), 0.4), threshold=30.0,
                         radius=max(2.0, out.size[0] * 0.008), strength=0.55)
    elif treatment in ("oil",) or tech == "oil_impasto":
        out = _rgb_op(out, lambda rgb: ImageEnhance.Color(rgb).enhance(1.16))
        out = _curve_lut(out, contrast=1.04, gamma=0.92, highlights=1.04)
        out = _emboss_relief(out, 0.32)
        out = _brush_strokes(out, style, 0.32, seed=seed & 0xFFFF)
        out = _split_tone(out, _mix(shadow, (46, 28, 12), 0.4), (255, 232, 190), 0.30)
        out = _vignette(out, 0.20, color=(24, 12, 4))
    elif treatment in ("chalk",) or tech == "chalk_sketch":
        out = _rgb_op(out, lambda rgb: ImageEnhance.Color(rgb).enhance(0.62))
        out = _chalk_dust(out, 0.20, color=(235, 235, 226), seed=seed & 0xFFFF)
        out = _edge_ink(out, 0.18, color=(240, 240, 232), gain=1.8, blur=0.5)
    elif treatment in ("gongbi",) or tech == "silk_gongbi":
        out = _silk_weave(out, 0.30)
        out = _edge_ink(out, 0.20, color=_mix(fg, (52, 38, 24), 0.35), gain=1.8, blur=0.5)
        out = _split_tone(out, _mix(shadow, (62, 46, 30), 0.3), (255, 246, 228), 0.28)
    elif treatment in ("vhs",) or tech == "vhs":
        out = _chroma_bleed(out, 0.6)
        out = _channel_shift(out, max(3, int(out.size[0] * 0.016)))
        out = _scanlines(out, 0.30, spacing=3)
        out = _grain(out, 0.12, seed=seed & 0xFFFF)
    elif treatment in ("film",) or tech == "film_grain":
        out = _split_tone(out, _mix(shadow, (24, 14, 8), 0.4), (255, 232, 198), 0.34)
        out = _bloom(out, threshold=180, radius=max(3, out.size[0] * 0.012),
                     strength=0.18, tint=(255, 226, 190))
        out = _grain(out, 0.12, seed=seed & 0xFFFF)
    elif treatment in ("noir",) or tech == "noir":
        out = _rgb_op(out, lambda rgb: ImageEnhance.Color(rgb).enhance(0.12))
        out = _curve_lut(out, contrast=1.32, gamma=0.92)
        out = _vignette(out, 0.34, color=(0, 0, 0))
    elif treatment in ("hologram",) or tech == "hologram":
        out = _tone_map(out, _mix(shadow, (6, 10, 28), 0.5), _mix(accent, (150, 235, 255), 0.5),
                        0.40)
        out = _scanlines(out, 0.16, spacing=3)
        out = _bloom(out, threshold=160, radius=max(3, out.size[0] * 0.012),
                     strength=0.30, tint=accent)
    elif treatment in ("blueprint",) or tech == "blueprint":
        out = _blueprint_lineify(out, style, seed)
        out = _rgb_op(out, lambda rgb: Image.blend(
            rgb, ImageEnhance.Brightness(rgb).enhance(1.10), 0.4))
    else:
        out = _rgb_op(out, lambda rgb: ImageEnhance.Color(rgb).enhance(
            0.75 + 0.35 * float(params.get("saturation", 1.0))))
    if treatment in ("ink_brush", "engrave", "gongbi"):
        out = _edge_ink(out, 0.24, color=_mix(fg, (30, 34, 38), 0.4), gain=2.0, blur=0.35)
    else:
        out = _edge_ink(out, 0.14, color=_mix(fg, shadow, 0.5), gain=1.7, blur=0.5)
    return out



#: 每个 technique 的兜底调色链（style.grade 缺省时用；保证 16 风格观感不同）
TECHNIQUE_GRADE: dict[str, str] = {
    "ink_wash": "eq=contrast=1.06:saturation=0.66:gamma=1.02,colorbalance=rs=0.03:gs=0.01:bs=-0.02",
    "woodblock": "eq=contrast=1.14:saturation=0.82:brightness=0.01,colorbalance=rs=0.05:gs=0.02:bs=-0.04",
    "parchment_engrave": "eq=contrast=1.10:saturation=0.55:brightness=0.01,colorbalance=rs=0.07:gs=0.03:bs=-0.05",
    "museum_gold": "eq=contrast=1.18:saturation=1.04:brightness=-0.01,colorbalance=rs=0.03:bs=-0.03",
    "silk_gongbi": "eq=contrast=1.03:saturation=0.94:brightness=0.01,colorbalance=rs=0.04:gs=0.01:bs=-0.02",
    "neon_toon": "eq=contrast=1.12:saturation=1.34:brightness=0.01,colorbalance=rs=0.06:bs=0.10",
    "hologram": "eq=contrast=1.14:saturation=1.26:brightness=0.02,colorbalance=rs=0.04:bs=0.14",
    "vhs": "eq=contrast=1.04:saturation=0.94:brightness=0.02,colorbalance=rs=0.10:bs=-0.06",
    "film_grain": "eq=contrast=1.05:saturation=0.90:brightness=0.01,colorbalance=rs=0.08:gs=0.02:bs=-0.06",
    "noir": "hue=s=0.16,eq=contrast=1.34:brightness=-0.04,colorbalance=bs=0.12:rs=0.02",
    "oil_impasto": "eq=contrast=1.10:saturation=1.06:brightness=0.00,colorbalance=rs=0.07:gs=0.02:bs=-0.05",
    "baroque_candle": "eq=contrast=1.20:brightness=-0.03:saturation=1.02,colorbalance=rs=0.10:gs=0.02:bs=-0.10",
    "blueprint": "hue=s=0.30,eq=contrast=1.10:brightness=0.01,colorbalance=bs=0.14:rs=-0.04",
    "collage_cutout": "eq=contrast=1.10:saturation=1.30:brightness=0.02,colorbalance=rs=0.04:bs=0.02",
    "claymation": "eq=contrast=0.99:saturation=1.04:brightness=0.02,colorbalance=rs=0.05:gs=0.02:bs=-0.02",
    "chalk_sketch": "eq=contrast=1.14:saturation=0.42:brightness=0.02,colorbalance=bs=0.04:rs=0.01",
}


def _primary_technique(style: dict) -> str:
    techniques = [str(t) for t in ((style or {}).get("technique") or [])]
    for tech in TECHNIQUES:
        if tech in techniques:
            return tech
    for tech in TECHNIQUES:
        if tech in str((style or {}).get("id") or ""):
            return tech
    return ""


def grade_filter(style: dict) -> str:
    """style → ffmpeg 调色链（``-vf`` 用，逗号分隔）。

    优先用 style JSON 的 ``grade``（``eq`` / ``hue`` / ``colorbalance`` / ``curves`` /
    ``extra``）；缺省时按 technique 生成兜底链。返回空字符串表示不调色。
    """
    style = style if isinstance(style, dict) else {}
    grade = style.get("grade") if isinstance(style.get("grade"), dict) else {}
    parts: list[str] = []
    eq = str(grade.get("eq") or "").strip()
    if not eq:
        params = finish_params(style)
        bits: list[str] = []
        contrast = float(params.get("contrast", 1.0))
        saturation = float(params.get("saturation", 1.0))
        brightness = float(params.get("brightness", 1.0))
        if abs(contrast - 1.0) > 0.004:
            bits.append(f"contrast={contrast:.3f}")
        if abs(saturation - 1.0) > 0.004:
            bits.append(f"saturation={saturation:.3f}")
        if abs(brightness - 1.0) > 0.004:
            bits.append(f"brightness={brightness - 1.0:+.3f}")
        if bits:
            eq = ":".join(bits)
    if eq:
        parts.append(f"eq={eq}")
    hue = str(grade.get("hue") or "").strip()
    if hue:
        parts.append(f"hue={hue}")
    colorbalance = str(grade.get("colorbalance") or "").strip()
    if not colorbalance:
        temperature = float(finish_params(style).get("temperature", 0.0))
        if abs(temperature) > 0.02:
            colorbalance = (f"rs={0.10 * temperature:+.3f}:gs={0.02 * temperature:+.3f}:"
                            f"bs={-0.10 * temperature:+.3f}")
    if colorbalance:
        parts.append(f"colorbalance={colorbalance}")
    curves = str(grade.get("curves") or "").strip()
    if curves:
        parts.append(f"curves={curves}")
    extra = str(grade.get("extra") or "").strip()
    if extra:
        parts.append(extra)
    if not parts:
        fallback = TECHNIQUE_GRADE.get(_primary_technique(style), "")
        if fallback:
            parts.append(fallback)
    return ",".join(p for p in parts if p)


# ===========================================================================
# 6. ffmpeg 包装（packaging_filters）
# ===========================================================================
def _textfile(workdir: Path, name: str, text: str) -> str:
    text_dir = Path(workdir) / "text"
    text_dir.mkdir(parents=True, exist_ok=True)
    path = text_dir / name
    path.write_text(str(text).replace("\n", " "), encoding="utf-8")
    return f"text/{name}"


def _hex_ff(hex_color: str, alpha: float = 1.0) -> str:
    """#RRGGBB → ffmpeg 0xRRGGBB@a。"""
    color = str(hex_color or "#FFFFFF").strip()
    if not color.startswith("#"):
        color = "#" + color.lstrip("0x")
    if len(color) == 4:
        color = "#" + "".join(ch * 2 for ch in color[1:])
    if len(color) != 7:
        color = "#FFFFFF"
    return f"0x{color[1:].upper()}@{max(0.0, min(1.0, float(alpha))):.2f}"



def _font_exists(workdir, font_file) -> bool:
    """字体存在性：font_file 可能是相对 workdir 的路径（fonts.py 约定），
    也可能是绝对路径；不能按进程 CWD 判断。"""
    text = str(font_file or '').strip()
    if not text:
        return False
    path = Path(text)
    if path.is_absolute():
        return path.is_file()
    return (Path(workdir) / path).is_file()

def packaging_filters(style: dict, workdir, width: int, height: int,
                      font_file: str) -> str:
    """风格包装：上下条/画框/频道 tag/印章水印（ffmpeg 滤镜链）。

    读取 style JSON 的 ``packaging``：
    ``{"tag","tag_color","bars","frame","seal","brand","watermark","corner"}``；
    ``bars ∈ none|thin|letterbox|gold_frame|red_tape|scanlines|blueprint``；
    ``frame ∈ none|thin|double|gold``。
    """
    style = style if isinstance(style, dict) else {}
    pack = style.get("packaging") if isinstance(style.get("packaging"), dict) else {}
    if not pack:
        return ""
    width, height = int(width), int(height)
    workdir = Path(workdir)
    font_file = str(font_file or "")
    tag_color = str(pack.get("tag_color") or "").strip()
    if not tag_color:
        tag_color = "#" + "".join(f"{v:02X}" for v in _rgb(style, "accent"))
    parts: list[str] = []
    bars = str(pack.get("bars") or "none").lower()
    accent = _hex_ff(tag_color, 0.9)
    accent_dim = _hex_ff(tag_color, 0.55)
    bar_h = max(2, int(height * 0.006))
    if bars == "letterbox":
        bh = max(4, int(height * 0.055))
        parts.append(f"drawbox=x=0:y=0:w=iw:h={bh}:color=black@1:t=fill")
        parts.append(f"drawbox=x=0:y=ih-{bh}:w=iw:h={bh}:color=black@1:t=fill")
        parts.append(f"drawbox=x=0:y={bh}:w=iw:h={bar_h}:color={accent}:t=fill")
    elif bars == "thin":
        parts.append(f"drawbox=x=0:y=0:w=iw:h={bar_h}:color={accent}:t=fill")
        parts.append(f"drawbox=x=0:y=ih-{bar_h}:w=iw:h={bar_h}:color={accent}:t=fill")
    elif bars == "gold_frame":
        inset = max(6, int(width * 0.012))
        for k, color in enumerate((accent, accent_dim)):
            off = inset + k * max(3, int(width * 0.004))
            parts.append(f"drawbox=x={off}:y={off}:w=iw-{off * 2}:h=ih-{off * 2}:"
                         f"color={color}:t={max(1, int(width * 0.0012))}")
    elif bars == "red_tape":
        tw = max(8, int(width * 0.012))
        parts.append(f"drawbox=x=0:y=0:w=iw:h={tw}:color={accent}:t=fill")
        parts.append(f"drawbox=x=0:y=0:w={tw}:h=ih*0.30:color={accent}:t=fill")
        parts.append(f"drawbox=x=iw-{tw}:y=ih*0.70:w={tw}:h=ih*0.30:color={accent_dim}:t=fill")
    elif bars == "scanlines":
        parts.append(f"drawgrid=w=iw:h=3:t=1:c=black@0.10")
        parts.append(f"drawbox=x=0:y=0:w=iw:h={bar_h}:color={accent}:t=fill")
    elif bars in ("blueprint", "grid"):
        grid = max(8, int(width * 0.02))
        parts.append(f"drawgrid=w={grid}:h={grid}:t=1:c={_hex_ff(tag_color, 0.10)}")
        border = max(2, int(width * 0.0025))
        parts.append(f"drawbox=x=0:y=0:w=iw:h=ih:color={accent_dim}:t={border}")
    frame = str(pack.get("frame") or "").lower()
    if frame in ("thin", "double", "gold"):
        color = accent if frame != "thin" else accent_dim
        pad = max(4, int(height * 0.018))
        parts.append(f"drawbox=x={pad}:y={pad}:w=iw-{pad * 2}:h=ih-{pad * 2}:"
                     f"color={color}:t={max(1, int(height * 0.0022))}")
        if frame == "double":
            pad2 = pad + max(3, int(height * 0.010))
            parts.append(f"drawbox=x={pad2}:y={pad2}:w=iw-{pad2 * 2}:h=ih-{pad2 * 2}:"
                         f"color={accent_dim}:t=1")
    # 右上频道 tag
    tag = str(pack.get("tag") or style.get("label") or "").strip()
    if tag and font_file and _font_exists(workdir, font_file):
        tag_file = _textfile(workdir, "v5_tag.txt", tag)
        tag_size = max(16, int(height * 0.032))
        parts.append(
            f"drawtext=textfile='{tag_file}':fontfile='{font_file}':"
            f"fontsize={tag_size}:fontcolor={accent}:borderw=2:"
            f"bordercolor=black@0.55:x=w-text_w-{int(width * 0.035)}:y={int(height * 0.028)}"
        )
    # 印章（左上角红方印）
    seal = str(pack.get("seal") or "").strip()
    if seal and font_file and _font_exists(workdir, font_file):
        seal_file = _textfile(workdir, "v5_seal.txt", seal)
        seal_size = max(18, int(height * 0.045))
        sx = int(width * 0.030)
        sy = int(height * 0.032)
        seal_color = _hex_ff(pack.get("seal_color") or "#B23A2E", 0.92)
        parts.append(f"drawbox=x={sx}:y={sy}:w={seal_size + 10}:h={seal_size + 10}:"
                     f"color={seal_color}:t=fill")
        parts.append(
            f"drawtext=textfile='{seal_file}':fontfile='{font_file}':"
            f"fontsize={seal_size}:fontcolor=0xEFE6D3@0.95:"
            f"x={sx + 5}:y={sy + 4}"
        )
    # 品牌/水印（右下角小字）
    brand = str(pack.get("brand") or "").strip()
    if brand and font_file and _font_exists(workdir, font_file) and pack.get("watermark", True):
        brand_file = _textfile(workdir, "v5_brand.txt", brand)
        brand_size = max(12, int(height * 0.020))
        parts.append(
            f"drawtext=textfile='{brand_file}':fontfile='{font_file}':"
            f"fontsize={brand_size}:fontcolor=0xFFFFFF@{0.34 if pack.get('watermark') else 0.5}:"
            f"x=w-text_w-{int(width * 0.030)}:y=h-text_h-{int(height * 0.020)}"
        )
    return ",".join(parts)


# ===========================================================================
# 7. 风格化字幕（subtitle_ass）
# ===========================================================================
def _ass_color(color, alpha: int = 0) -> str:
    """RGB/A → ASS ``&HAABBGGRR``（alpha 00=不透明）。"""
    if isinstance(color, str) and color.startswith("#"):
        rgba = scene_render.hex_rgba(color)
    else:
        rgba = tuple(int(v) for v in tuple(color)[:4])
        if len(rgba) < 4:
            rgba = rgba + (255,)
    a = max(0, min(255, 255 - int(rgba[3]))) if len(rgba) > 3 else 0
    if alpha:
        a = max(0, min(255, int(alpha)))
    return f"&H{a:02X}{rgba[2]:02X}{rgba[1]:02X}{rgba[0]:02X}"


def _ass_time(seconds: float) -> str:
    total_cs = max(0, int(round(float(seconds) * 100)))
    h, rem = divmod(total_cs, 360000)
    m, rem = divmod(rem, 6000)
    s, cs = divmod(rem, 100)
    return f"{h:d}:{m:02d}:{s:02d}.{cs:02d}"


def _ass_escape(text: str) -> str:
    t = str(text or "")
    t = t.replace("\\", "\\\\").replace("{", "\\{").replace("}", "\\}")
    t = t.replace("\r", " ").replace("\n", " ")
    return t


def _ass_point(text: str) -> str:
    """ASS 里逐字竖排：字符间用 \\N。"""
    chars = [ch for ch in str(text or "") if not ch.isspace()]
    return "\\N".join(_ass_escape(ch) for ch in chars)


def _font_paths() -> dict:
    """复用 pipeline/fonts.ensure_fonts 的中文字体（不下载新字体）。"""
    info = fonts.ensure_fonts(FONT_SCRATCH)
    scratch = Path(FONT_SCRATCH)
    body = info.get("body") or {}
    display = info.get("display") or body
    body_path = str((scratch / str(body.get("file") or "")).resolve())
    display_path = str((scratch / str(display.get("file") or "")).resolve())
    return {
        "body": body_path,
        "display": display_path,
        "body_family": str(body.get("family") or "DengXian"),
        "display_family": str(display.get("family") or body.get("family") or "DengXian"),
        "dir": str(scratch),
    }


def _normalize_events(events) -> list[tuple[float, float, str]]:
    """事件归一化：兼容 [(start,end,text)] / [(start,end,layer,style,text)] / dict。"""
    out: list[tuple[float, float, str]] = []
    for item in events or []:
        try:
            if isinstance(item, dict):
                start = float(item.get("start", item.get("t0", 0.0)))
                end = float(item.get("end", item.get("t1", start + 1.0)))
                text = str(item.get("text", ""))
            elif isinstance(item, (list, tuple)):
                if len(item) >= 5:
                    start, end, text = float(item[0]), float(item[1]), str(item[4])
                elif len(item) >= 3:
                    start, end, text = float(item[0]), float(item[1]), str(item[-1])
                else:
                    continue
            else:
                continue
        except (TypeError, ValueError):
            continue
        text = re.sub(r"\s+", "", text)
        if not text or end <= start:
            continue
        out.append((start, max(start + 0.2, end), text))
    return out


def subtitle_ass(style: dict, events, out_ass, width: int, height: int) -> dict:
    """生成风格化 ASS 字幕（水墨=竖排/印章；版刻=纸框；羊皮纸=档案条；博物馆=金字牌）。

    events 支持 ``[{"start","end","text"}]`` 或 render_v3 的 5 元组
    ``(start, end, layer, style, text)``。返回 ``{"path","count","style",...}``。
    """
    style = style if isinstance(style, dict) else {}
    width, height = int(width), int(height)
    if width < 64 or height < 64:
        raise V5Error(f"subtitle_ass 尺寸太小: {width}x{height}")
    texts = _normalize_events(events)
    fonts_info = _font_paths()
    tech = _primary_technique(style)
    style_id = str(style.get("id") or "")
    typography = style.get("typography") if isinstance(style.get("typography"), dict) else {}
    subtitle_preset = str(typography.get("subtitle") or "")
    primary = _ass_color(_rgb(style, "fg"))
    accent = _ass_color(_rgb(style, "accent"))
    paper = _ass_color(_rgb(style, "paper", fallback="bg"))
    outline = _ass_color(_rgb(style, "paper", fallback="bg"))
    # 风格族：决定版式（竖排/纸框/档案/金牌/常规）
    if tech == "ink_wash" or style_id.startswith("ink") or "ink" in subtitle_preset:
        family, vertical = "ink", True
    elif tech == "woodblock" or "woodblock" in style_id:
        family, vertical = "woodblock", False
    elif tech == "parchment_engrave" or "parchment" in style_id:
        family, vertical = "parchment", False
    elif tech == "museum_gold" or "museum" in style_id:
        family, vertical = "museum", False
    elif tech == "silk_gongbi":
        family, vertical = "silk", False
    else:
        family, vertical = "modern", False

    back_c = "&H64000000"  # 默认 BackColour（BorderStyle=1 时只作阴影色）
    if vertical:
        size = max(20, int(height * 0.047))
        font_name = fonts_info["display_family"]
        outline_c = outline
        border_style, outline_w, shadow = 1, max(1, int(height * 0.0035)), 2
        primary_c = primary
        margin_v = int(height * 0.07)
    elif family == "woodblock":
        size = max(18, int(height * 0.045))
        font_name = fonts_info["body_family"]
        # BorderStyle=3 的“纸底框”必须给 Outline>0 才有 padding（=0 时框不可见）
        border_style = 3
        outline_w, shadow = max(5, int(height * 0.010)), 1
        primary_c = primary
        outline_c = _ass_color(_rgb(style, "paper", fallback="bg"), alpha=10)
        back_c = _ass_color(_rgb(style, "paper", fallback="bg"), alpha=8)
        margin_v = int(height * 0.075)
    elif family == "parchment":
        size = max(18, int(height * 0.040))
        font_name = fonts_info["body_family"]
        border_style = 3
        outline_w, shadow = max(6, int(height * 0.011)), 2
        primary_c = _ass_color(_rgb(style, "highlight", fallback="paper"))
        outline_c = _ass_color(_mix(_rgb(style, "bg"), (0, 0, 0), 0.45), alpha=60)
        back_c = _ass_color(_mix(_rgb(style, "bg"), (0, 0, 0), 0.40), alpha=38)
        margin_v = int(height * 0.070)
    elif family == "museum":
        size = max(18, int(height * 0.042))
        font_name = fonts_info["display_family"]
        border_style = 3
        outline_w, shadow = max(7, int(height * 0.012)), 3
        primary_c = _ass_color(_rgb(style, "accent"))
        outline_c = _ass_color((0, 0, 0), alpha=60)
        back_c = _ass_color((0, 0, 0), alpha=42)
        margin_v = int(height * 0.085)
    elif family == "silk":
        # 绢本：深墨字 + 纸色不透明描边（横排）；margin 提高到 9.5% 避开近景下巴
        size = max(18, int(height * 0.042))
        font_name = fonts_info["body_family"]
        border_style = 1
        outline_w, shadow = max(3, int(height * 0.0042)), 1
        primary_c = _ass_color(_rgb(style, "fg"))
        outline_c = _ass_color(_rgb(style, "paper", fallback="bg"), alpha=6)
        margin_v = int(height * 0.095)
    else:
        size = max(18, int(height * 0.042))
        font_name = fonts_info["body_family"]
        border_style, outline_w, shadow = 1, max(2, int(height * 0.0034)), 2
        primary_c = _ass_color(_rgb(style, "highlight", fallback="fg"))
        outline_c = _ass_color(_rgb(style, "shadow", fallback="fg"))
        margin_v = int(height * 0.075)

    # 主样式 + 竖排样式
    style_line = (
        "Style: V5Sub,{font},{size},{primary},{primary},{outline},{back},"
        "{bold},0,0,0,100,100,{spacing},0,{border_style},{outline_w},{shadow},2,"
        "48,48,{margin_v},1"
    ).format(font=font_name, size=size, primary=primary_c, outline=outline_c,
             bold=-1 if family in ("woodblock", "museum") else 0,
             spacing=max(0, int(height * 0.0022)) if family in ("museum", "silk") else 0,
             border_style=border_style, outline_w=outline_w, shadow=shadow,
             margin_v=margin_v, back=back_c)
    style_v = (
        "Style: V5SubV,{font},{size},{primary},{primary},{outline},&H00000000,"
        "0,0,0,0,100,100,2,0,1,{outline_w},{shadow},7,24,24,0,1"
    ).format(font=fonts_info["display_family"], size=size, primary=primary_c,
             outline=outline_c, outline_w=outline_w, shadow=shadow)
    header = [
        "[Script Info]",
        "ScriptType: v4.00+",
        f"PlayResX: {width}",
        f"PlayResY: {height}",
        "WrapStyle: 2",
        "ScaledBorderAndShadow: yes",
        "YCbCr Matrix: TV.709",
        "",
        "[V4+ Styles]",
        ("Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, "
         "BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, "
         "BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding"),
        style_line,
        style_v,
        "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]
    lines: list[str] = list(header)
    seal_done = False
    for start, end, text in texts:
        t0, t1 = _ass_time(start), _ass_time(end)
        if vertical:
            # 右侧竖排：默认 9 字/列；长句自动加列（最多 3 列），列多则缩字号，
            # 保证 1280×720 下 3 列不出画、且总高度 ≤ 画面 72%（不压角色脸）。
            chars = [ch for ch in text if not ch.isspace()]
            # 排版参数可由 style.typography 覆盖（水墨默认 8 字/列、字号 3.6%H、
            # 右边距 0.955W，保证 3 列不压角色脸、不出画）
            max_chars = max(6, min(12, int(typography.get("line_chars", 8))))
            cols_n = max(1, min(3, math.ceil(len(chars) / float(max_chars))))
            per_col = max(1, math.ceil(len(chars) / float(cols_n)))
            base_size = max(16, int(height * float(typography.get("font_ratio", 0.036))))
            fitted = int((height * 0.62) / (per_col * 1.22))
            size_v = max(14, min(base_size, fitted))
            right_x = int(width * float(typography.get("right_x", 0.955)))
            step = max(int(width * 0.042), int(size_v * 1.55))
            y0 = int(height * float(typography.get("margin_y", 0.07)))
            for col_index, column in enumerate(_vertical_columns(text, max_chars=per_col)):
                x = right_x - col_index * step
                body = _ass_point(column)
                lines.append(
                    f"Dialogue: 1,{t0},{t1},V5SubV,,0,0,0,,"
                    f"{{\\pos({x},{y0})\\fs{size_v}\\bord{outline_w}\\shad2}}{body}"
                )
            # 右下角朱印（不参与竖排列，避免压字）
            if not seal_done:
                seal_text = str((style.get("packaging") or {}).get("seal") or "史")
                ss = max(22, int(height * 0.048))
                sx = int(width * 0.945) - ss
                sy = int(height * 0.90) - ss
                lines.append(
                    "Dialogue: 2,{}, {},V5SubV,,0,0,0,,"
                    "{{\\an7\\pos({},{})\\p1\\c{}\\bord0\\shad0}}"
                    "m 0 0 l {} 0 {} {} 0 {} 0{{\\p0}}".format(
                        t0, t1, sx, sy, accent, ss, ss, ss, ss)
                )
                lines.append(
                    f"Dialogue: 2,{t0},{t1},V5SubV,,0,0,0,,"
                    f"{{\\an5\\pos({sx + ss // 2},{sy + ss // 2})\\fs{max(14, int(ss * 0.66))}"
                    f"\\c{paper}\\bord0\\shad0}}{_ass_escape(seal_text[:1])}"
                )
                seal_done = True
        elif family == "woodblock":
            body = _escaped_wrapped(text, max_chars=18)
            lines.append(
                f"Dialogue: 0,{t0},{t1},V5Sub,,0,0,0,,"
                f"{{\\fad(140,180)\\bord0\\shad1}}{body}"
            )
        elif family == "parchment":
            body = _escaped_wrapped(text, max_chars=20)
            tag = str((style.get("packaging") or {}).get("tag") or "档案")
            lines.append(
                f"Dialogue: 0,{t0},{t1},V5Sub,,0,0,0,,"
                f"{{\\fad(120,160)}}{tag} // {body}"
            )
        elif family == "museum":
            body = _escaped_wrapped(text, max_chars=18)
            lines.append(
                f"Dialogue: 0,{t0},{t1},V5Sub,,0,0,0,,"
                f"{{\\fad(160,200)}}{body}"
            )
            # 上下金线
            lw = int(width * 0.16)
            cx, cy = int(width * 0.5), int(height * 0.865)
            lines.append(
                "Dialogue: 2,{}, {},V5Sub,,0,0,0,,"
                "{{\\an5\\pos({},{})\\p1\\c{}\\bord0\\shad0}}m -{} 0 l {} 0{} 0 2 {} 0{{\\p0}}".format(
                    t0, t1, cx, cy, accent, lw, lw, -lw, -lw)
            )
        elif family == "silk":
            body = _escaped_wrapped(text, max_chars=18)
            lines.append(
                f"Dialogue: 0,{t0},{t1},V5Sub,,0,0,0,,"
                f"{{\\fad(140,180)}}{body}"
            )
        else:
            body = _escaped_wrapped(text, max_chars=18)
            lines.append(
                f"Dialogue: 0,{t0},{t1},V5Sub,,0,0,0,,"
                f"{{\\fad(120,160)}}{body}"
            )
    content = "\n".join(lines) + "\n"
    out_path = Path(out_ass)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = out_path.with_suffix(out_path.suffix + ".tmp")
    tmp.write_text(content, encoding="utf-8-sig")
    tmp.replace(out_path)
    return {
        "path": str(out_path.resolve()),
        "count": len(texts),
        "style": style_id,
        "technique": tech,
        "family": family,
        "vertical": bool(vertical),
        "font": font_name,
        "width": width,
        "height": height,
        "format": "ass",
    }


def _vertical_columns(text: str, max_chars: int = 12) -> list[str]:
    """竖排字幕分列（首列从右往左）。"""
    chars = [ch for ch in str(text or "") if not ch.isspace()]
    if not chars:
        return []
    if len(chars) <= max_chars:
        return ["".join(chars)]
    return ["".join(chars[i:i + max_chars]) for i in range(0, len(chars), max_chars)]


def _escaped_wrapped(text: str, max_chars: int = 18) -> str:
    """长句在标点处拆两行（ASS \\N）。"""
    plain = re.sub(r"\s+", "", str(text or ""))
    if len(plain) <= max_chars:
        return _ass_escape(plain)
    punct = "，。！？；：、,.!?;:）)】」』"
    ideal = len(plain) / 2.0
    split = None
    for i, ch in enumerate(plain[:-1]):
        if ch in punct and abs((i + 1) - ideal) <= 6:
            split = i + 1
            break
    if split is None:
        split = max(1, min(len(plain) - 1, int(round(ideal))))
    return _ass_escape(plain[:split]) + "\\N" + _ass_escape(plain[split:])


# ===========================================================================
# 8. 片头/片尾卡片（title_card / end_card）
# ===========================================================================
#: technique → 卡片背景场景（scene_render primitive 组合）
CARD_SCENES: dict[str, dict] = {
    "ink_wash": {"layers": [
        {"type": "paper", "layer": "background", "mottle": 0.8, "edge": 0.6},
        {"type": "mountains", "layer": "background", "y": 0.86, "height": 0.30,
         "count": 2, "color": "fg"},
        {"type": "fog", "layer": "foreground", "y": 0.80, "count": 2, "alpha": 60},
        {"type": "particles", "layer": "foreground", "count": 26, "size": 0.0018},
        {"type": "vignette", "layer": "foreground", "strength": 0.22},
        {"type": "grain", "layer": "foreground", "strength": 0.05}]},
    "woodblock": {"layers": [
        {"type": "paper", "layer": "background", "mottle": 0.9, "edge": 0.5},
        {"type": "mountains", "layer": "background", "y": 0.88, "height": 0.24,
         "count": 2, "color": "fg"},
        {"type": "grid", "layer": "midground", "spacing": 0.09, "alpha": 26},
        {"type": "vignette", "layer": "foreground", "strength": 0.24},
        {"type": "grain", "layer": "foreground", "strength": 0.05}]},
    "parchment_engrave": {"layers": [
        {"type": "paper", "layer": "background", "mottle": 1.0, "edge": 0.6},
        {"type": "terrain_contours", "layer": "background", "levels": 5,
         "scale": 0.6, "alpha": 70},
        {"type": "cross_section_grid", "layer": "midground", "x": 0.04, "y": 0.58,
         "w": 0.30, "h": 0.30, "layers": 4, "finds": 3},
        {"type": "vignette", "layer": "foreground", "strength": 0.30},
        {"type": "grain", "layer": "foreground", "strength": 0.08}]},
    "museum_gold": {"layers": [
        {"type": "marble", "layer": "background", "veins": 9, "sheen_x": 0.6},
        {"type": "spotlights", "layer": "foreground", "count": 2, "strength": 0.5,
         "y_target": 0.62, "spread": 0.20},
        {"type": "particles", "layer": "foreground", "count": 40, "size": 0.0016},
        {"type": "vignette", "layer": "foreground", "strength": 0.36},
        {"type": "grain", "layer": "foreground", "strength": 0.04}]},
    "silk_gongbi": {"layers": [
        {"type": "canvas", "layer": "background", "thread": 5},
        {"type": "mountains", "layer": "background", "y": 0.82, "height": 0.26,
         "count": 2, "color": "mid"},
        {"type": "vignette", "layer": "foreground", "strength": 0.18},
        {"type": "grain", "layer": "foreground", "strength": 0.03}]},
    "neon_toon": {"layers": [
        {"type": "sky_gradient", "layer": "background", "top": "bg:1",
         "bottom": "bg:0", "horizon": 0.74},
        {"type": "skyline", "layer": "midground", "y": 0.64, "rows": 2, "windows": 0.30},
        {"type": "grid", "layer": "foreground", "perspective": 1, "y_horizon": 0.66,
         "spacing": 0.05, "alpha": 34},
        {"type": "particles", "layer": "foreground", "count": 34, "size": 0.0018},
        {"type": "vignette", "layer": "foreground", "strength": 0.30},
        {"type": "grain", "layer": "foreground", "strength": 0.04}]},
    "hologram": {"layers": [
        {"type": "sky_gradient", "layer": "background", "top": "bg:1",
         "bottom": "bg:0"},
        {"type": "hologram_grid", "layer": "midground", "dots": 60},
        {"type": "particles", "layer": "foreground", "count": 70, "size": 0.002},
        {"type": "vignette", "layer": "foreground", "strength": 0.26},
        {"type": "grain", "layer": "foreground", "strength": 0.04}]},
    "vhs": {"layers": [
        {"type": "sky_gradient", "layer": "background", "top": "bg:1",
         "bottom": "bg:0"},
        {"type": "grid", "layer": "midground", "spacing": 0.06, "alpha": 40},
        {"type": "vignette", "layer": "foreground", "strength": 0.26},
        {"type": "grain", "layer": "foreground", "strength": 0.10}]},
    "film_grain": {"layers": [
        {"type": "sky_gradient", "layer": "background", "top": "bg:1",
         "bottom": "bg:0", "horizon": 0.66},
        {"type": "fog", "layer": "foreground", "y": 0.72, "count": 2, "alpha": 70},
        {"type": "vignette", "layer": "foreground", "strength": 0.26},
        {"type": "grain", "layer": "foreground", "strength": 0.10}]},
    "noir": {"layers": [
        {"type": "sky_gradient", "layer": "background", "top": "bg:1",
         "bottom": "bg:0"},
        {"type": "skyline", "layer": "midground", "y": 0.62, "rows": 2, "windows": 0.15},
        {"type": "rain", "layer": "foreground", "count": 160},
        {"type": "vignette", "layer": "foreground", "strength": 0.40},
        {"type": "grain", "layer": "foreground", "strength": 0.10}]},
    "oil_impasto": {"layers": [
        {"type": "canvas", "layer": "background", "thread": 6},
        {"type": "mountains", "layer": "background", "y": 0.84, "height": 0.28,
         "count": 3, "color": "shadow"},
        {"type": "vignette", "layer": "foreground", "strength": 0.34},
        {"type": "grain", "layer": "foreground", "strength": 0.04}]},
    "baroque_candle": {"layers": [
        {"type": "marble", "layer": "background", "veins": 6},
        {"type": "columns", "layer": "midground", "count": 4, "y": 0.06,
         "y_bottom": 0.86, "width": 0.07},
        {"type": "spotlights", "layer": "foreground", "count": 2, "strength": 0.45,
         "y_target": 0.72, "spread": 0.18},
        {"type": "vignette", "layer": "foreground", "strength": 0.42},
        {"type": "grain", "layer": "foreground", "strength": 0.08}]},
    "blueprint": {"layers": [
        {"type": "sky_gradient", "layer": "background", "top": "bg:1",
         "bottom": "bg:0"},
        {"type": "blueprint_lines", "layer": "midground", "dimensions": 6,
         "circles": 4, "labels": 12},
        {"type": "vignette", "layer": "foreground", "strength": 0.26},
        {"type": "grain", "layer": "foreground", "strength": 0.05}]},
    "collage_cutout": {"layers": [
        {"type": "paper", "layer": "background", "mottle": 0.7, "edge": 0.4},
        {"type": "stickers", "layer": "midground", "count": 16},
        {"type": "torn_paper", "layer": "foreground", "y": 0.78, "jagged": 0.03},
        {"type": "vignette", "layer": "foreground", "strength": 0.14},
        {"type": "grain", "layer": "foreground", "strength": 0.04}]},
    "claymation": {"layers": [
        {"type": "paper", "layer": "background", "mottle": 0.6, "edge": 0.3},
        {"type": "clay_shapes", "layer": "midground", "count": 7},
        {"type": "vignette", "layer": "foreground", "strength": 0.22},
        {"type": "grain", "layer": "foreground", "strength": 0.04}]},
    "chalk_sketch": {"layers": [
        {"type": "brick_wall", "layer": "background", "rows": 10, "ratio": 3.0},
        {"type": "chalk_lines", "layer": "foreground", "strokes": 8},
        {"type": "vignette", "layer": "foreground", "strength": 0.26},
        {"type": "grain", "layer": "foreground", "strength": 0.05}]},
}
_CARD_SCENE_FALLBACK = {"layers": [
    {"type": "paper", "layer": "background", "mottle": 0.6, "edge": 0.4},
    {"type": "mountains", "layer": "background", "y": 0.84, "height": 0.26,
     "count": 2, "color": "mid"},
    {"type": "vignette", "layer": "foreground", "strength": 0.24},
    {"type": "grain", "layer": "foreground", "strength": 0.05}]}


def card_background(style: dict, size=(1280, 720), variant: str = "") -> Image.Image:
    """按风格生成卡片背景（复用 scene_render primitive + apply_finish）。"""
    style = style if isinstance(style, dict) else {}
    width, height = int(size[0]), int(size[1])
    tech = _primary_technique(style)
    scene = dict(CARD_SCENES.get(tech) or _CARD_SCENE_FALLBACK)
    scene["id"] = f"card_{tech or 'default'}"
    # 深色系风格整体压暗，便于叠字
    dark_bg = tech in ("museum_gold", "baroque_candle", "neon_toon", "hologram",
                       "vhs", "noir", "blueprint", "chalk_sketch", "film_grain")
    render_size = (1600, 900)
    try:
        layers = scene_render.render_scene_layers(scene, style, render_size,
                                                  seed=7 if variant != "end" else 11)
        image = scene_render.flatten_layers(layers).convert("RGB")
    except Exception as exc:  # noqa: BLE001 - 卡片背景失败退化为纯色
        LOG.warning("卡片背景渲染失败(%s)，退化为纯色: %s", tech, exc)
        image = Image.new("RGB", render_size,
                          tuple(_rgb(style, "bg", fallback="paper")))
    image = image.resize((width, height), Image.LANCZOS)
    image = apply_finish(image, style, kind="plate").convert("RGBA")
    # 压暗/氛围叠层（字体对比度）
    strength = 0.52 if dark_bg else 0.24
    if variant == "end":
        strength += 0.10
    overlay = Image.new("RGBA", (width, height),
                        tuple(int(v * 0.25) for v in _rgb(style, "bg")) + (int(255 * strength),))
    canvas = image.convert("RGBA")
    canvas.alpha_composite(overlay)
    return canvas


_FONT_INFO: dict = {}


def _font_info_cached() -> dict:
    global _FONT_INFO
    if not _FONT_INFO:
        _FONT_INFO = _font_paths()
    return _FONT_INFO


def _load_font(path: str, size: int):
    try:
        return ImageFont.truetype(path, max(8, int(size)))
    except OSError:
        try:
            return ImageFont.load_default(size=max(8, int(size)))
        except TypeError:  # pragma: no cover
            return ImageFont.load_default()


def _fit_font(path: str, text: str, max_width: int, start: int, min_size: int = 18):
    size = max(min_size, int(start))
    while size > min_size:
        font = _load_font(path, size)
        try:
            box = font.getbbox(str(text))
            if box[2] - box[0] <= max_width:
                return font
        except (OSError, ValueError):
            break
        size -= max(2, size // 12)
    return _load_font(path, min_size)


def _text(draw, xy, text, font, fill, anchor="mm", shadow=None) -> None:
    if shadow is not None:
        draw.text((xy[0] + 2, xy[1] + 3), str(text), font=font, fill=shadow, anchor=anchor)
    draw.text(xy, str(text), font=font, fill=fill, anchor=anchor)


def _card_family(style: dict) -> str:
    tech = _primary_technique(style)
    style_id = str(style.get("id") or "")
    if tech == "ink_wash" or style_id.startswith("ink"):
        return "ink"
    if tech == "woodblock" or "woodblock" in style_id:
        return "woodblock"
    if tech == "parchment_engrave" or "parchment" in style_id:
        return "parchment"
    if tech == "museum_gold" or "museum" in style_id:
        return "museum"
    return "modern"


def _draw_ink_card(canvas: Image.Image, style: dict, text: str, subtitle: str,
                   is_end: bool) -> Image.Image:
    """水墨卡片：右侧竖排标题 + 印章 + 左下副标题线。"""
    w, h = canvas.size
    info = _font_info_cached()
    draw = ImageDraw.Draw(canvas)
    fg = _rgb(style, "fg")
    accent = _rgb(style, "accent")
    paper = _rgb(style, "paper", fallback="bg")
    title = str(text or ("终" if is_end else "史书"))
    subtitle = str(subtitle or "")
    chars = [ch for ch in title if not ch.isspace()]
    fs = min(int(h * 0.115), int(w * 0.20 / max(1, len(chars))))
    font = _load_font(info["display"], max(28, fs))
    x = int(w * (0.80 if not is_end else 0.52))
    y = int(h * 0.13)
    step = int(fs * 1.10)
    for ch in chars:
        _text(draw, (x, y), ch, font, fg + (255,), anchor="mm")
        y += step
    # 朱印
    seal = str((style.get("packaging") or {}).get("seal") or ("终" if is_end else "史"))
    ss = max(28, int(h * 0.085))
    sy = min(int(h * 0.80), y + int(h * 0.02))
    draw.rectangle((x - ss // 2, sy, x + ss // 2, sy + ss), fill=accent + (240,))
    draw.rectangle((x - ss // 2 + ss * 0.14, sy + ss * 0.14,
                    x + ss // 2 - ss * 0.14, sy + ss - ss * 0.14),
                   outline=paper + (235,), width=max(1, int(ss * 0.05)))
    seal_font = _load_font(info["display"], int(ss * 0.62))
    _text(draw, (x, sy + ss // 2), seal[:1], seal_font, paper + (245,))
    # 左侧副标题 + 引线
    if subtitle:
        sub_font = _fit_font(info["body"], subtitle, int(w * 0.34), int(h * 0.045),
                             min_size=16)
        _text(draw, (int(w * 0.10), int(h * 0.84)), subtitle, sub_font, fg + (235,),
              anchor="lm")
        draw.line((int(w * 0.10), int(h * 0.885), int(w * 0.10) + int(w * 0.16),
                   int(h * 0.885)), fill=accent + (200,), width=max(2, int(h * 0.003)))
    # 竖排装饰细线
    draw.line((int(w * 0.74), int(h * 0.12), int(w * 0.74), int(h * 0.88)),
              fill=fg + (80,), width=1)
    return canvas


def _draw_woodblock_card(canvas: Image.Image, style: dict, text: str, subtitle: str,
                         is_end: bool) -> Image.Image:
    """版刻卡片：双线书框 + 居中标题 + 朱印。"""
    w, h = canvas.size
    info = _font_info_cached()
    draw = ImageDraw.Draw(canvas)
    fg = _rgb(style, "fg")
    accent = _rgb(style, "accent")
    paper = _rgb(style, "paper", fallback="bg")
    title = str(text or ("卷终" if is_end else "典籍"))
    subtitle = str(subtitle or "")
    # 双线框
    margin = int(min(w, h) * 0.075)
    for k, width in enumerate((max(2, int(h * 0.004)), 1)):
        off = margin + k * max(3, int(h * 0.010))
        draw.rectangle((off, off, w - off, h - off), outline=fg + (200 - k * 70,),
                       width=width)
    # 角饰
    corner = int(min(w, h) * 0.055)
    for cx, cy, dx, dy in ((margin, margin, 1, 1), (w - margin, margin, -1, 1),
                           (margin, h - margin, 1, -1), (w - margin, h - margin, -1, -1)):
        draw.line((cx, cy, cx + dx * corner, cy), fill=accent + (210,), width=max(2, int(h * 0.004)))
        draw.line((cx, cy, cx, cy + dy * corner), fill=accent + (210,), width=max(2, int(h * 0.004)))
    title_font = _fit_font(info["display"], title, int(w * 0.62), int(h * 0.15))
    _text(draw, (w // 2, int(h * 0.45)), title, title_font, fg + (255,),
          shadow=(0, 0, 0, 40))
    if subtitle:
        sub_font = _fit_font(info["body"], subtitle, int(w * 0.56), int(h * 0.052),
                             min_size=16)
        _text(draw, (w // 2, int(h * 0.62)), subtitle, sub_font, fg + (215,))
        draw.line((w // 2 - int(w * 0.10), int(h * 0.565), w // 2 + int(w * 0.10),
                   int(h * 0.565)), fill=accent + (190,), width=max(1, int(h * 0.003)))
    # 朱印
    seal = str((style.get("packaging") or {}).get("seal") or ("终" if is_end else "典籍"))
    ss = max(24, int(h * 0.075))
    sx, sy = int(w * 0.80), int(h * 0.74)
    draw.rectangle((sx, sy, sx + ss, sy + ss), fill=accent + (235,))
    seal_font = _load_font(info["display"], int(ss * 0.58))
    _text(draw, (sx + ss // 2, sy + ss // 2), seal[:1], seal_font, paper + (245,))
    # 顶部小字
    kicker = "版刻典籍" if not is_end else "卷终"
    kicker_font = _load_font(info["body"], max(14, int(h * 0.030)))
    _text(draw, (w // 2, int(h * 0.145)), f"· {kicker} ·", kicker_font, fg + (160,))
    return canvas


def _draw_parchment_card(canvas: Image.Image, style: dict, text: str, subtitle: str,
                         is_end: bool) -> Image.Image:
    """羊皮纸档案卡：红色卷宗印 + 左对齐标题 + 虚线框 + 档案编号。"""
    w, h = canvas.size
    info = _font_info_cached()
    draw = ImageDraw.Draw(canvas)
    fg = _rgb(style, "fg")
    accent = _rgb(style, "accent")
    paper = _rgb(style, "paper", fallback="bg")
    title = str(text or ("封存" if is_end else "档案"))
    subtitle = str(subtitle or "")
    # 虚线框
    margin = int(min(w, h) * 0.055)
    dash, gap = max(6, int(w * 0.010)), max(4, int(w * 0.006))
    for x in range(margin, w - margin, dash + gap):
        draw.line((x, margin, min(w - margin, x + dash), margin), fill=fg + (150,), width=1)
        draw.line((x, h - margin, min(w - margin, x + dash), h - margin), fill=fg + (150,), width=1)
    for y in range(margin, h - margin, dash + gap):
        draw.line((margin, y, margin, min(h - margin, y + dash)), fill=fg + (150,), width=1)
        draw.line((w - margin, y, w - margin, min(h - margin, y + dash)), fill=fg + (150,), width=1)
    # 卷宗 stamp
    stamp = "END OF FILE" if is_end else "CASE FILE"
    stamp_font = _load_font(info["display"], max(16, int(h * 0.034)))
    sw = int(w * 0.20)
    sh = int(h * 0.075)
    sx, sy = margin + int(w * 0.02), margin + int(h * 0.02)
    draw.rectangle((sx, sy, sx + sw, sy + sh), outline=accent + (230,), width=max(2, int(h * 0.004)))
    _text(draw, (sx + sw // 2, sy + sh // 2), stamp, stamp_font, accent + (240,))
    # 标题
    title_font = _fit_font(info["display"], title, int(w * 0.60), int(h * 0.16))
    _text(draw, (int(w * 0.10), int(h * 0.47)), title, title_font, fg + (255,), anchor="lm")
    draw.line((int(w * 0.10), int(h * 0.55), int(w * 0.52), int(h * 0.55)),
              fill=accent + (200,), width=max(2, int(h * 0.004)))
    if subtitle:
        sub_font = _fit_font(info["body"], subtitle, int(w * 0.52), int(h * 0.048),
                             min_size=15)
        _text(draw, (int(w * 0.10), int(h * 0.61)), subtitle, sub_font, fg + (220,),
              anchor="lm")
    # 档案编号 + 红色证据线
    code = "NO. 03-1878" if not is_end else "ARCHIVED"
    code_font = _load_font(info["body"], max(13, int(h * 0.026)))
    _text(draw, (int(w * 0.10), int(h * 0.86)), code, code_font, fg + (170,), anchor="lm")
    draw.line((int(w * 0.10), int(h * 0.89), int(w * 0.88), int(h * 0.89)),
              fill=accent + (170,), width=max(1, int(h * 0.002)))
    # 右上十字定位标记
    mx, my = int(w * 0.88), int(h * 0.18)
    draw.line((mx - 12, my, mx + 12, my), fill=fg + (160,), width=1)
    draw.line((mx, my - 12, mx, my + 12), fill=fg + (160,), width=1)
    return canvas


def _draw_museum_card(canvas: Image.Image, style: dict, text: str, subtitle: str,
                      is_end: bool) -> Image.Image:
    """博物馆卡：黑底金色铭牌 + 聚光 + 双向金线。"""
    w, h = canvas.size
    info = _font_info_cached()
    draw = ImageDraw.Draw(canvas)
    gold = _rgb(style, "accent")
    highlight = _rgb(style, "highlight")
    title = str(text or ("终章" if is_end else "国宝"))
    subtitle = str(subtitle or "")
    # 铭牌
    pw = int(w * 0.62)
    ph = int(h * 0.30)
    px, py = (w - pw) // 2, int(h * 0.38)
    panel = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    pd = ImageDraw.Draw(panel)
    pd.rounded_rectangle((px, py, px + pw, py + ph), radius=max(4, int(h * 0.012)),
                         fill=(6, 6, 8, 190), outline=gold + (230,),
                         width=max(2, int(h * 0.0035)))
    inner = max(3, int(h * 0.006))
    pd.rounded_rectangle((px + inner, py + inner, px + pw - inner, py + ph - inner),
                         radius=max(2, int(h * 0.008)), outline=gold + (120,), width=1)
    canvas.alpha_composite(panel)
    draw = ImageDraw.Draw(canvas)
    title_font = _fit_font(info["display"], title, int(pw * 0.78), int(ph * 0.46))
    _text(draw, (w // 2, py + int(ph * 0.40)), title, title_font, highlight + (255,),
          shadow=(0, 0, 0, 150))
    draw.line((w // 2 - int(pw * 0.22), py + int(ph * 0.60), w // 2 + int(pw * 0.22),
               py + int(ph * 0.60)), fill=gold + (220,), width=max(1, int(h * 0.0025)))
    if subtitle:
        sub_font = _fit_font(info["body"], subtitle, int(pw * 0.72), int(ph * 0.24),
                             min_size=15)
        _text(draw, (w // 2, py + int(ph * 0.75)), subtitle, sub_font, gold + (225,))
    # 上方聚光
    canvas.alpha_composite(scene_render.glow_layer(
        (w, h), [(w * 0.5, py - h * 0.05, w * 0.22, h * 0.12, 0.35)],
        tuple(int(v) for v in highlight) + (255,), power=1.7, downscale=6))
    # 顶部展签
    kicker = "NATIONAL TREASURE" if not is_end else "THE END"
    kicker_font = _load_font(info["body"], max(13, int(h * 0.026)))
    _text(draw, (w // 2, int(h * 0.16)), kicker, kicker_font, gold + (200,))
    return canvas


def _text_outlined(draw: ImageDraw.ImageDraw, xy, text: str, font, fill,
                   outline, anchor: str = "mm", outline_w: int = 2) -> None:
    """带描边的文字（保证在复杂背景上也有对比度）。"""
    x, y = xy
    for dx in range(-outline_w, outline_w + 1):
        for dy in range(-outline_w, outline_w + 1):
            if dx == 0 and dy == 0:
                continue
            draw.text((x + dx, y + dy), str(text), font=font, fill=outline, anchor=anchor)
    draw.text((x, y), str(text), font=font, fill=fill, anchor=anchor)


def _scrim_band(canvas: Image.Image, center_y: float, height_frac: float,
                strength: int = 130, width_frac: float = 0.92) -> None:
    """标题安全区：中间横向渐变暗带（保证文字与背景分离）。"""
    w, h = canvas.size
    sw, sh = 96, 54
    yy, xx = np.mgrid[0:sh, 0:sw].astype(np.float32)
    cy = center_y * sh
    sigma = max(2.0, height_frac * sh * 0.55)
    band = np.exp(-((yy - cy) ** 2) / (2 * sigma ** 2))
    edge = np.clip((0.5 - np.abs(xx / sw - 0.5)) / 0.08, 0, 1)
    alpha = (band * edge * float(strength)).astype(np.uint8)
    mask = Image.fromarray(alpha, "L").resize((w, h), Image.BILINEAR)
    overlay = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    overlay.putalpha(mask)
    canvas.alpha_composite(overlay)


def _draw_modern_card(canvas: Image.Image, style: dict, text: str, subtitle: str,
                      is_end: bool) -> Image.Image:
    """通用现代卡：暗色标题安全区 + 居中标题（描边）+ 副标题 + 风格色细线。"""
    w, h = canvas.size
    info = _font_info_cached()
    accent = _rgb(style, "accent")
    highlight = _rgb(style, "highlight", fallback="fg")
    title = str(text or ("下期见" if is_end else style.get("label") or "V5"))
    subtitle = str(subtitle or "")
    if subtitle.strip() == title.strip():
        subtitle = ""
    # 标题安全区（压暗背景，避免被霓虹管/高光压住）
    _scrim_band(canvas, 0.46, 0.30, strength=150 if not is_end else 165)
    if subtitle:
        _scrim_band(canvas, 0.60, 0.12, strength=110)
    title_font = _fit_font(info["display"], title, int(w * 0.74), int(h * 0.16))
    # 发光底层
    canvas.alpha_composite(scene_render.glow_layer(
        (w, h), [(w * 0.5, h * 0.46, w * 0.26, h * 0.085, 0.26)],
        tuple(int(v) for v in accent) + (255,), power=1.8, downscale=6))
    draw = ImageDraw.Draw(canvas)
    _text_outlined(draw, (w // 2, int(h * 0.455)), title, title_font, highlight + (255,),
                   (6, 6, 10, 235), outline_w=max(2, int(h * 0.006)))
    if subtitle:
        sub_font = _fit_font(info["body"], subtitle, int(w * 0.60), int(h * 0.046),
                             min_size=15)
        _text_outlined(draw, (w // 2, int(h * 0.585)), subtitle, sub_font,
                       highlight + (240,), (6, 6, 10, 220),
                       outline_w=max(1, int(h * 0.003)))
    line_w = int(w * 0.20)
    cy = int(h * 0.525)
    draw.line((w // 2 - line_w, cy, w // 2 - int(w * 0.045), cy),
              fill=accent + (235,), width=max(2, int(h * 0.0035)))
    draw.line((w // 2 + int(w * 0.045), cy, w // 2 + line_w, cy),
              fill=accent + (235,), width=max(2, int(h * 0.0035)))
    kicker = str((style.get("packaging") or {}).get("tag") or style.get("label") or "")
    if kicker:
        kicker_font = _load_font(info["body"], max(13, int(h * 0.026)))
        _text_outlined(draw, (w // 2, int(h * 0.16)), kicker, kicker_font,
                       accent + (235,), (4, 4, 8, 200), outline_w=1)
    return canvas


def _draw_card_text(canvas: Image.Image, style: dict, text: str, subtitle: str,
                    kind: str) -> Image.Image:
    family = _card_family(style)
    is_end = str(kind) == "end"
    if family == "ink":
        return _draw_ink_card(canvas, style, text, subtitle, is_end)
    if family == "woodblock":
        return _draw_woodblock_card(canvas, style, text, subtitle, is_end)
    if family == "parchment":
        return _draw_parchment_card(canvas, style, text, subtitle, is_end)
    if family == "museum":
        return _draw_museum_card(canvas, style, text, subtitle, is_end)
    return _draw_modern_card(canvas, style, text, subtitle, is_end)


def _save_card(canvas: Image.Image, out_png) -> Path:
    out_path = Path(out_png)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = out_path.with_suffix(out_path.suffix + ".tmp")
    canvas.convert("RGB").save(tmp, format="PNG", optimize=True)
    tmp.replace(out_path)
    return out_path.resolve()


def title_card(style: dict, text: str, out_png, size=(1280, 720),
               subtitle: str = "") -> dict:
    """片头卡片（按风格族排版：水墨竖排+印章 / 版刻书框 / 档案卷宗 / 金色铭牌）。"""
    style = style if isinstance(style, dict) else {}
    width, height = int(size[0]), int(size[1])
    text = str(text or "")
    subtitle = str(subtitle or "")
    if subtitle.strip() == text.strip():
        subtitle = ""  # 与标题重复的副标题自动去重（避免 pilot 里出现两遍同名）
    canvas = card_background(style, (width, height), variant="title")
    canvas = _draw_card_text(canvas, style, text, subtitle, kind="title")
    path = _save_card(canvas, out_png)
    return {"path": str(path), "kind": "title_card", "style": str(style.get("id") or ""),
            "text": text, "subtitle": subtitle, "size": [width, height],
            "family": _card_family(style)}


def end_card(style: dict, text: str, out_png, size=(1280, 720),
             subtitle: str = "", **kwargs) -> dict:
    """片尾卡片（同风格族排版，语义为“终/封存/下期再见”）。"""
    style = style if isinstance(style, dict) else {}
    width, height = int(size[0]), int(size[1])
    text = str(text or "")
    subtitle = str(subtitle or "")
    if subtitle.strip() == text.strip():
        subtitle = ""
    canvas = card_background(style, (width, height), variant="end")
    canvas = _draw_card_text(canvas, style, text, subtitle, kind="end")
    path = _save_card(canvas, out_png)
    return {"path": str(path), "kind": "end_card", "style": str(style.get("id") or ""),
            "text": text, "subtitle": subtitle, "size": [width, height],
            "family": _card_family(style)}


# ===========================================================================
# 9. 转场（transition）
# ===========================================================================
#: 语义转场 → ffmpeg xfade 名称（xfade 可用集合的保守子集）
TRANSITION_MAP: dict[str, str] = {
    "ink_wipe": "fade",
    "ink_bleed": "fade",
    "scroll_unroll": "wipeleft",
    "scroll_roll": "wiperight",
    "woodblock_wipe": "wipeleft",
    "woodblock_cut": "fadeblack",
    "redline": "smoothleft",
    "gold_cut": "fadeblack",
    "spotlight": "circleopen",
    "spotlight_iris": "circleopen",
    "spotlight_close": "circleclose",
    "tape_jump": "hblur",
    "tape_glitch": "pixelize",
    "film_burn": "fadewhite",
    "film_cut": "dissolve",
    "neon_wipe": "wiperight",
    "light_wipe": "smoothright",
    "glitch_wipe": "pixelize",
    "hologram_wipe": "circleopen",
    "blinds_wipe": "vertopen",
    "candle_fade": "fadeblack",
    "blueprint_wipe": "smoothleft",
    "sticker_pop": "zoomin",
    "stopmotion_jump": "fade",
    "chalk_wipe": "wipeup",
    "silk_wipe": "fade",
    "oil_wipe": "dissolve",
    "fade": "fade",
    "dissolve": "dissolve",
    "wipe": "wipeleft",
    "cut": "fade",
    "hard_cut": "fade",
}


def transition(style: dict, **kwargs) -> tuple[str, str]:
    """style.transition → ``(语义名, ffmpeg xfade 转场名)``。

    未知语义回退 ``("fade", "fade")``，不报错（渲染链不因转场名中断）。
    kwargs 可传 ``duration`` 等，仅作记录用途（返回值固定二元组）。
    """
    style = style if isinstance(style, dict) else {}
    raw = str(style.get("transition") or "").strip().lower()
    if not raw:
        return ("fade", "fade")
    xfade = TRANSITION_MAP.get(raw)
    if xfade is None:
        for key, value in TRANSITION_MAP.items():
            if key in raw:
                xfade = value
                break
    return (raw, xfade or "fade")


# ===========================================================================
# 10. CLI（预览片头/片尾/字幕，不占 GPU）
# ===========================================================================
def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m pipeline.style_v5",
        description="V5 风格引擎预览（卡片/字幕/调色链）",
    )
    parser.add_argument("--list", action="store_true", help="列出全部风格")
    parser.add_argument("--style", default="ink_chronicle")
    parser.add_argument("--title", default="", help="片头标题")
    parser.add_argument("--end", default="", help="渲染片尾卡片（文本）")
    parser.add_argument("--subtitle", default="")
    parser.add_argument("--card", choices=("title", "end", "both"), default="title")
    parser.add_argument("--out", default="", help="输出目录或 PNG")
    parser.add_argument("--size", default="1280x720")
    parser.add_argument("--root", default="")
    parser.add_argument("--print-filter", action="store_true", help="打印 grade/packaging/transition")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    root = Path(args.root) if args.root else None
    catalog = load_catalog(root)
    styles = catalog.get("styles") or {}
    if args.list:
        for sid in sorted(styles):
            style = styles[sid]
            tech = ",".join(str(t) for t in (style.get("technique") or []))
            print(f"{sid:22s} {str(style.get('label') or ''):12s} {tech}")
        print(f"共 {len(styles)} 个风格")
        return 0
    style = get_style(catalog, args.style)
    try:
        width, height = (int(v) for v in str(args.size).lower().split("x"))
    except ValueError:
        width, height = 1280, 720
    if args.print_filter:
        print(json.dumps({
            "style": style.get("id"),
            "grade": grade_filter(style),
            "transition": transition(style),
            "finish": finish_params(style),
            "techniques": style.get("technique"),
        }, ensure_ascii=False, indent=2))
        return 0
    out = Path(args.out) if args.out else (REPO / "tmp" / "v5_preview")
    if out.suffix.lower() == ".png":
        out_dir = out.parent
        out_name = out.stem
    else:
        out_dir = out
        out_name = ""
    out_dir.mkdir(parents=True, exist_ok=True)
    results: list[dict] = []
    if args.card in ("title", "both"):
        title = args.title or str(style.get("label") or style.get("id"))
        path = out_dir / (f"{out_name}.png" if out_name else f"card_title__{style.get('id')}.png")
        results.append(title_card(style, title, path, (width, height), args.subtitle))
    if args.card in ("end", "both") or args.end:
        text = args.end or "下期再见"
        path = out_dir / (f"{out_name}_end.png" if out_name else f"card_end__{style.get('id')}.png")
        results.append(end_card(style, text, path, (width, height), args.subtitle))
    print(json.dumps(results, ensure_ascii=False, indent=2))
    return 0


# --- style_v5 APPEND POINT ---


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
