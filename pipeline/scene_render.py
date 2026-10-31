# -*- coding: utf-8 -*-
"""V5 风格化场景渲染器：可组合视觉原语 → 分层 PIL 图像。

设计（docs/PLAN_V5_STYLE_AUDIO.md §3.1 + docs/V5_INTERFACES.md §1）：
- **风格 = 可组合视觉原语**：场景 JSON 只声明 layer.type + 归一化参数，
  具体画法由 primitive 按 ``style["technique"]`` / ``style["texture"]`` /
  ``style["line"]`` 自适应（同一场景在 4 个风格下观感截然不同）；
- 颜色**只从 style["palette"] 取**：layer 里写角色名（``"accent"`` / ``"accent:1"``），
  也允许显式 ``"#rrggbb"``（用户自定义场景用）；未知角色走 fallback 链，全缺抛 V5Error；
- 三层分离（background / midground / foreground）+ depth 视差系数，供 motion2d 做分层推拉；
- 坐标/尺寸一律 0-1 归一化（x, y, w, h, cx, cy, r, size...），由 ctx.PX()/PY()/S() 转像素；
- 纯 PIL/numpy，无 GPU/网络；1600×900 单场景 ≤1.5s。

对外接口（勿改签名，见 V5_INTERFACES §1）::

    class V5Error(RuntimeError)
    def primitive(name) -> Callable
    def register(name, fn=None) -> Callable
    def render_scene_layers(scene, style, size, seed=0) -> dict

    # 返回 {"background","midground","foreground","depth","anchors","camera_zones","meta"}

扩展加载点（多人并行，避免改本文件）：
    pipeline/v5_primitives_history.py / v5_primitives_modern.py /
    v5_primitives_modern_b.py 在 import 时执行 ``register(name, fn)`` 即可；
    也可用环境变量 ``V5_PRIMITIVE_MODULES=a,b`` 覆盖模块列表。

外部 primitive 签名::

    def my_primitive(img: PIL.Image.Image, spec: dict, ctx: SceneCtx) -> None

    - img: RGBA 图层（尺寸 = ctx.w × ctx.h），就地 draw/alpha_composite；
    - spec: scene.layers[i] 原 dict（type/layer/opacity 之外全由 primitive 读）；
    - ctx: 见 SceneCtx（坐标/调色板/rng/style/theme）。

命令（预览，不占 GPU）::

    python -m pipeline.scene_render --list-primitives
    python -m pipeline.scene_render --check-scenes
    python -m pipeline.scene_render --scene ancient_map --style ink_chronicle \
        --out tmp/v5_preview/ancient_map__ink_chronicle.png
"""
from __future__ import annotations

import argparse
import importlib
import json
import logging
import math
import os
import random
import sys
import time
import zlib
from pathlib import Path
from typing import Callable, Iterable, Sequence

import numpy as np
from PIL import Image, ImageChops, ImageDraw, ImageEnhance, ImageFilter, ImageFont

from . import config

LOG = logging.getLogger("pipeline")

REPO = config.REPO_ROOT
DEFAULT_SIZE = (1600, 900)
LAYERS = ("background", "midground", "foreground")
LAYER_ALIASES = {"bg": "background", "mid": "midground", "fg": "foreground",
                 "back": "background", "front": "foreground"}
DEFAULT_DEPTH = {"background": 0.02, "midground": 0.12, "foreground": 0.30}
DEFAULT_ANCHORS = {"host": (0.18, 0.90), "figure_a": (0.72, 0.90)}
DEFAULT_CAMERA_ZONES = {"wide": [0.0, 0.0, 1.0, 1.0]}
EXTENSION_MODULES = ("v5_primitives_history", "v5_primitives_modern",
                     "v5_primitives_modern_b")

FONT_SCRATCH = REPO / "tmp" / "v5_fonts"

# AI 场景 plate 目录（V5.2 美术升级）；可用 V5_SCENE_ART_DIR 覆盖（测试用）
SCENE_ART_DIR = Path(os.environ.get("V5_SCENE_ART_DIR", "") or (REPO / "assets" / "v5" / "scenes"))


def _scene_plate_path(scene: dict) -> Path | None:
    """返回该场景的 AI plate 路径；没有则 None（走程序化 fallback）。"""
    art = scene.get("art")
    if isinstance(art, dict):
        rel = art.get("plate") or art.get("image")
        if rel:
            p = Path(rel)
            if not p.is_absolute():
                p = REPO / p
            if p.is_file():
                return p
    sid = str(scene.get("id") or scene.get("scene") or "").strip()
    if not sid:
        return None
    # V6.3：场景画风变体（V5_SCENE_VARIANT=25d 时优先 <scene>_25d.png）
    variant = str(os.environ.get("V5_SCENE_VARIANT", "") or "").strip().lower()
    if variant:
        vp = Path(SCENE_ART_DIR) / f"{sid}_{variant}.png"
        if vp.is_file():
            return vp
    p = Path(SCENE_ART_DIR) / f"{sid}.png"
    return p if p.is_file() else None


def _fit_cover(img: Image.Image, size: tuple[int, int]) -> Image.Image:
    """等比缩放铺满 size，居中裁剪（避免 AI plate 比例不符变形）。"""
    w, h = size
    iw, ih = img.size
    scale = max(w / max(1, iw), h / max(1, ih))
    nw, nh = max(1, int(round(iw * scale))), max(1, int(round(ih * scale)))
    img = img.resize((nw, nh), Image.LANCZOS)
    left, top = (nw - w) // 2, (nh - h) // 2
    return img.crop((left, top, left + w, top + h))


def _render_plate_layers(scene: dict, style: dict, size: tuple[int, int],
                         seed: int, plate_path: Path) -> dict:
    """AI plate → 三层图层：plate 做 background（风格 finish），叠加雾/光/暗角。"""
    width, height = size
    plate = Image.open(plate_path).convert("RGBA")
    plate = _fit_cover(plate, size)
    try:
        from . import style_v5  # 延迟 import，避免循环
        finished = style_v5.apply_finish(plate, style)
        if isinstance(finished, Image.Image) and finished.size == size:
            plate = finished.convert("RGBA")
    except Exception as exc:  # noqa: BLE001
        LOG.debug("plate style finish 跳过: %s", exc)
    background = plate.copy()
    midground = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    foreground = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    palette = style.get("palette") or {}
    try:
        haze_color = tuple(resolve_color(palette, "highlight", fallback="paper"))[:3]
    except Exception:  # noqa: BLE001
        haze_color = (240, 240, 240)
    haze = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    hd = ImageDraw.Draw(haze)
    for i in range(6):
        y = int(height * (0.35 + i * 0.11))
        alpha = 8 + i * 3
        hd.rectangle([0, y, width, min(height, y + height // 7)],
                     fill=tuple(haze_color) + (alpha,))
    midground.alpha_composite(haze.filter(ImageFilter.GaussianBlur(40)))
    vignette = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    vd = ImageDraw.Draw(vignette)
    edge = int(min(width, height) * 0.16)
    for i in range(edge):
        a = int(46 * (1 - i / max(1, edge)) ** 1.6)
        if a <= 0:
            continue
        vd.rectangle([i, i, width - 1 - i, height - 1 - i], outline=(0, 0, 0, a))
    foreground.alpha_composite(vignette.filter(ImageFilter.GaussianBlur(12)))
    top = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    td = ImageDraw.Draw(top)
    for i in range(int(height * 0.18)):
        a = int(52 * (1 - i / (height * 0.18)))
        td.line([(0, i), (width, i)], fill=(0, 0, 0, a))
    foreground.alpha_composite(top)
    return {"background": background, "midground": midground,
            "foreground": foreground}





class V5Error(RuntimeError):
    """V5 风格/场景契约被违反（未知 primitive / 非法 palette / 非法图层等）。"""


# ===========================================================================
# 1. primitive 注册表
# ===========================================================================
_PRIMITIVES: dict[str, Callable] = {}


def register(name: str, fn: Callable | None = None) -> Callable:
    """注册 primitive。``register("x", fn)`` 或 ``@register("x")`` 两种写法都支持。

    - name 空 / fn 不可调用 → V5Error；
    - 同名后注册覆盖先注册（外部模块可覆盖内置实现，方便单人调试）。
    """
    if fn is None:  # 装饰器模式
        def _deco(func: Callable) -> Callable:
            register(name, func)
            return func
        return _deco
    key = str(name or "").strip()
    if not key:
        raise V5Error("primitive 名称不能为空")
    if not callable(fn):
        raise V5Error(f"primitive {key!r} 的绘制函数不可调用: {fn!r}")
    _PRIMITIVES[key] = fn
    return fn


def primitive(name: str) -> Callable:
    """按名称取 primitive；未注册 raise V5Error。"""
    key = str(name or "").strip()
    fn = _PRIMITIVES.get(key)
    if fn is None:
        raise V5Error(
            f"未知 primitive: {key!r}（已注册 {len(_PRIMITIVES)} 个；"
            f"用 scene_render.list_primitives() 查看可用名称）"
        )
    return fn


def list_primitives() -> list[str]:
    """已注册 primitive 名称（排序）。"""
    return sorted(_PRIMITIVES)


def _load_extensions() -> list[str]:
    """加载队友的 primitive 扩展模块（缺失/报错不阻断引擎）。"""
    env = os.environ.get("V5_PRIMITIVE_MODULES", "").strip()
    names = [n.strip() for n in env.split(",") if n.strip()] if env else list(EXTENSION_MODULES)
    loaded: list[str] = []
    pkg = __package__ or "pipeline"
    for name in names:
        full = f"{pkg}.{name}"
        try:
            importlib.import_module(f".{name}", pkg)
            loaded.append(name)
        except ImportError:
            continue  # 模块还没写/可选依赖缺失：正常
        except Exception as exc:  # noqa: BLE001 - 队友模块 bug 不拖垮引擎
            LOG.warning("V5 primitive 扩展 %s 加载失败: %s", full, exc)
    if loaded:
        LOG.debug("V5 primitive 扩展已加载: %s", ",".join(loaded))
    return loaded


# ===========================================================================
# 2. 颜色（只从 style["palette"] 取；角色名 / 角色:索引 / #hex）
# ===========================================================================
ROLE_FALLBACK: dict[str, tuple[str, ...]] = {
    "paper": ("bg",),
    "mid": ("fg",),
    "highlight": ("paper", "bg"),
    "shadow": ("fg",),
    "ground": ("mid", "bg"),
    "sky": ("bg",),
    "water": ("mid", "bg"),
    "river": ("water", "mid", "bg"),
    "wood": ("accent", "fg"),
    "stone": ("mid", "fg"),
    "glow": ("accent", "highlight"),
    "seal": ("accent", "fg"),
    "metal": ("accent", "highlight"),
    "line": ("fg",),
    "open": ("bg",),
    "ink": ("fg",),
    "silk": ("paper", "bg"),
    "gold": ("accent", "highlight"),
}


def hex_rgba(value, alpha: int = 255) -> tuple[int, int, int, int]:
    """``#rgb`` / ``#rrggbb`` / ``#rrggbbaa`` → (r, g, b, a)。非法 raise V5Error。"""
    s = str(value or "").strip()
    if s.startswith("#"):
        s = s[1:]
    if len(s) == 3:
        s = "".join(ch * 2 for ch in s)
    if len(s) == 8:
        s = s[:6]
    if len(s) != 6:
        raise V5Error(f"非法颜色: {value!r}（需要 #rgb/#rrggbb）")
    try:
        return (int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16), int(alpha))
    except ValueError as exc:
        raise V5Error(f"非法颜色: {value!r}") from exc


def mix(color_a, color_b, t: float) -> tuple[int, int, int, int]:
    """两色线性插值（t=0 → a，t=1 → b）。"""
    a = tuple(int(v) for v in color_a[:3]) + ((int(color_a[3]) if len(color_a) > 3 else 255),)
    b = tuple(int(v) for v in color_b[:3]) + ((int(color_b[3]) if len(color_b) > 3 else 255),)
    t = max(0.0, min(1.0, float(t)))
    return tuple(int(round(a[i] + (b[i] - a[i]) * t)) for i in range(4))  # type: ignore[return-value]


def shade(color, factor: float) -> tuple[int, int, int, int]:
    """整体明度缩放（factor<1 变暗；>1 变亮，自动 clamp）。"""
    f = max(0.0, float(factor))
    vals = [int(round(min(255.0, max(0.0, float(color[i]) * f)))) for i in range(3)]
    return (vals[0], vals[1], vals[2], int(color[3]) if len(color) > 3 else 255)


#: 未知颜色角色的语义关键字表（按顺序匹配；用于队友自定义 palette 角色的宽松回退，
#: 避免现代风格场景因 palette 少写一个角色就整场渲染失败）。命中的角色仍从 style.palette
#: 取色，因此换风格时颜色会跟着 palette 走。
ROLE_KEYWORDS: tuple[tuple[tuple[str, ...], str], ...] = (
    (("white", "light", "cream", "paper", "ivory", "snow", "highlight", "pale"),
     "highlight"),
    (("black", "dark", "ink", "deep", "shadow", "void", "night", "charcoal"),
     "fg"),
    (("gold", "brass", "amber", "yellow", "butter", "honey", "bronze", "candle"),
     "accent"),
    (("red", "wine", "seal", "blood", "rust", "crimson", "brick"),
     "accent"),
    (("green", "jade", "sage", "olive", "moss", "emerald"), "mid"),
    (("blue", "cyan", "teal", "glass", "sky", "water", "grid", "hologram",
      "neon", "aqua"), "mid"),
    (("stone", "concrete", "wall", "floor", "board", "slate", "marble"), "stone"),
    (("tape", "metal", "steel", "chrome", "silver"), "fg"),
    (("clay", "wood", "sand", "leather", "earth", "soil"), "accent"),
)


def _guess_role(role: str) -> str:
    """未知角色名 → 语义最接近的标准角色（命中不了返回空串）。"""
    key = str(role or "").strip().lower()
    if not key:
        return ""
    for needles, target in ROLE_KEYWORDS:
        if any(needle in key for needle in needles):
            return target
    return ""


def _palette_value(palette: dict, role: str, index: int = 0):
    """取 palette 角色原值（str / list / tuple），缺失返回 None。"""
    value = palette.get(role)
    if isinstance(value, (list, tuple)):
        if not value:
            return None
        idx = max(0, min(len(value) - 1, int(index)))
        return value[idx]
    return value


def resolve_role(palette: dict, role: str, index: int = 0):
    """palette 角色 → hex 字符串。

    回退顺序：精确角色 → ROLE_FALLBACK 链 → 角色名语义关键字（ROLE_KEYWORDS）→
    fg/mid/bg/paper 兜底；palette 完全为空才 raise V5Error。
    """
    value = _palette_value(palette, role, index)
    if value is not None:
        return value
    for alt in ROLE_FALLBACK.get(role, ()):
        value = _palette_value(palette, alt, index)
        if value is not None:
            return value
    guessed = _guess_role(role)
    if guessed:
        for alt in (guessed,) + ROLE_FALLBACK.get(guessed, ()):
            value = _palette_value(palette, alt, index)
            if value is not None:
                LOG.debug("palette 角色 %r 不存在，按语义回退到 %r", role, alt)
                return value
    for alt in ("fg", "mid", "bg", "paper", "accent"):
        value = _palette_value(palette, alt, index)
        if value is not None:
            LOG.debug("palette 角色 %r 不存在，兜底到 %r", role, alt)
            return value
    raise V5Error(
        f"style.palette 为空或缺少任何可用颜色（请求角色 {role!r}）；"
        f"palette 现有键: {sorted(palette)}"
    )


def resolve_color(palette: dict, ref=None, fallback: str = "fg",
                  index: int = 0, alpha: int = 255) -> tuple[int, int, int, int]:
    """颜色引用 → RGBA。

    ref 支持：
      - None → 用 fallback 角色；
      - ``"accent"`` / ``"accent:1"``（角色名:索引）→ style.palette；
      - ``"#b23a2e"`` → 显式颜色（用户自定义场景用）；
      - (r,g,b) / (r,g,b,a) → 原样；
      - {"role":..,"i":..,"a":..} → 结构化。
    """
    if isinstance(ref, dict):
        role = ref.get("role") or fallback
        return resolve_color(palette, role, fallback=fallback,
                             index=int(ref.get("i", index)), alpha=int(ref.get("a", alpha)))
    if isinstance(ref, (list, tuple)):
        vals = [int(max(0, min(255, v))) for v in ref[:4]]
        while len(vals) < 3:
            vals.append(0)
        return (vals[0], vals[1], vals[2], vals[3] if len(vals) > 3 else int(alpha))
    if ref is None or str(ref).strip() == "":
        ref = fallback
    text = str(ref).strip()
    if text.startswith("#"):
        base = hex_rgba(text)
        return (base[0], base[1], base[2], int(alpha))
    role, sep, tail = text.partition(":")
    idx = index
    if sep:
        try:
            idx = int(tail)
        except ValueError:
            role, idx = text, index
    value = resolve_role(palette, role.strip(), idx)
    if isinstance(value, str):
        base = hex_rgba(value)
        return (base[0], base[1], base[2], int(alpha))
    if isinstance(value, (list, tuple)):
        vals = [int(v) for v in value[:4]]
        while len(vals) < 3:
            vals.append(0)
        return (vals[0], vals[1], vals[2], vals[3] if len(vals) > 3 else int(alpha))
    raise V5Error(f"palette 角色 {role!r} 的值非法: {value!r}")


# ===========================================================================
# 3. 风格主题（technique → 布尔标记；primitive 按此换画法）
# ===========================================================================
THEME_KEYS = ("ink", "woodcut", "engrave", "gold", "silk", "neon", "vhs",
              "noir", "oil", "baroque", "blueprint", "collage", "clay",
              "chalk", "hologram", "film", "halftone")
_THEME_MATCH: dict[str, tuple[str, ...]] = {
    "ink": ("ink_wash", "rice_paper", "ink_bleed", "ink"),
    "woodcut": ("woodblock", "woodcut", "wood_grain"),
    "engrave": ("parchment_engrave", "engrave", "copperplate", "crosshatch"),
    "gold": ("museum_gold", "gold_leaf", "gold"),
    "silk": ("silk_gongbi", "silk", "gongbi"),
    "neon": ("neon",),
    "vhs": ("vhs",),
    "noir": ("noir",),
    "oil": ("oil_impasto", "oil"),
    "baroque": ("baroque_candle", "baroque"),
    "blueprint": ("blueprint",),
    "collage": ("collage_cutout", "collage"),
    "clay": ("claymation", "clay"),
    "chalk": ("chalk_sketch", "chalk"),
    "hologram": ("hologram",),
    "film": ("film_grain", "film"),
    "halftone": ("halftone",),
}


def theme_flags(style: dict) -> dict[str, bool]:
    """style → 17 个主题布尔（primitive 用它选画法）。"""
    parts: list[str] = []
    for key in ("technique", "texture"):
        value = style.get(key)
        if isinstance(value, (list, tuple)):
            parts.extend(str(v) for v in value)
        elif value:
            parts.append(str(value))
    line = style.get("line") or {}
    if isinstance(line, dict):
        parts.append(str(line.get("style") or ""))
    parts.append(str(style.get("character_treatment") or ""))
    parts.append(str(style.get("id") or ""))
    blob = " ".join(parts).lower()
    flags: dict[str, bool] = {}
    for theme, needles in _THEME_MATCH.items():
        flags[theme] = any(n in blob for n in needles)
    if flags["ink"] and "ink_chronicle" in blob:
        flags["ink"] = True
    return flags


# ===========================================================================
# 4. 字体（复用 pipeline/fonts.ensure_fonts，不下载新字体）
# ===========================================================================
_FONT_CACHE: dict[str, str | None] = {}


def _font_path(display: bool = False) -> str | None:
    key = "display" if display else "body"
    if key in _FONT_CACHE:
        return _FONT_CACHE[key]
    path: str | None = None
    try:
        from . import fonts
        info = fonts.ensure_fonts(FONT_SCRATCH)
        entry = info.get("display" if display else "body") or {}
        rel = str(entry.get("file") or "")
        if rel:
            candidate = FONT_SCRATCH / rel
            if candidate.is_file():
                path = str(candidate.resolve())
    except Exception as exc:  # noqa: BLE001 - 没字体不阻断几何绘制
        LOG.debug("V5 场景字体不可用: %s", exc)
    _FONT_CACHE[key] = path
    return path


def draw_text(draw: ImageDraw.ImageDraw, xy, text: str, size: int, fill,
              anchor: str = "mm", display: bool = False) -> bool:
    """画中文文字；无可用字体时返回 False（调用方可退化为几何图形）。"""
    path = _font_path(display=display)
    if not path:
        return False
    try:
        font = ImageFont.truetype(path, max(6, int(size)))
    except OSError:
        return False
    try:
        draw.text(xy, str(text), font=font, fill=fill, anchor=anchor)
        return True
    except (OSError, ValueError):
        return False


def text_size(text: str, size: int, display: bool = False) -> tuple[int, int]:
    """文字像素尺寸（无字体时按 0.9×字号估算）。"""
    path = _font_path(display=display)
    if path:
        try:
            font = ImageFont.truetype(path, max(6, int(size)))
            box = font.getbbox(str(text))
            return (int(box[2] - box[0]), int(box[3] - box[1]))
        except OSError:
            pass
    return (int(len(str(text)) * size * 0.92), int(size * 1.1))


# ===========================================================================
# 5. 图像/噪声 helper（numpy 为主，全部小图计算再放大）
# ===========================================================================
_NOISE_CACHE: dict[tuple, np.ndarray] = {}


def smooth_noise(w: int, h: int, scale: float, seed: int = 0, octaves: int = 4,
                 persistence: float = 0.5, normalize: bool = True) -> np.ndarray:
    """fBm 平滑噪声（float32，形状 (h, w)）；小分辨率生成 + 双线性放大，毫秒级。"""
    w, h = max(2, int(w)), max(2, int(h))
    scale = max(2.0, float(scale))
    key = (w, h, round(scale, 3), int(seed), int(octaves), round(persistence, 3), bool(normalize))
    cached = _NOISE_CACHE.get(key)
    if cached is not None:
        return cached
    rng = np.random.default_rng(int(seed) & 0xFFFFFFFF)
    total = np.zeros((h, w), dtype=np.float32)
    amp = 1.0
    norm = 0.0
    s = scale
    for _ in range(max(1, int(octaves))):
        gw = max(2, int(w / s) + 2)
        gh = max(2, int(h / s) + 2)
        base = rng.random((gh, gw), dtype=np.float32)
        small = Image.fromarray((base * 255.0).astype(np.uint8), "L")
        up = np.asarray(small.resize((w, h), Image.BILINEAR), dtype=np.float32) / 255.0
        total += amp * up
        norm += amp
        amp *= persistence
        s = max(2.0, s / 2.1)
    total /= max(1e-6, norm)
    if normalize:
        lo = float(total.min())
        hi = float(total.max())
        if hi - lo > 1e-6:
            total = (total - lo) / (hi - lo)
    arr = total.astype(np.float32)
    if len(_NOISE_CACHE) > 64:
        _NOISE_CACHE.pop(next(iter(_NOISE_CACHE)))
    _NOISE_CACHE[key] = arr
    return arr


def vgradient(w: int, h: int, top, bottom) -> Image.Image:
    """竖直渐变 RGBA 图（top/bottom 为 RGBA tuple）。"""
    w, h = max(1, int(w)), max(1, int(h))
    t = np.linspace(0.0, 1.0, h, dtype=np.float32)[:, None, None]
    a = np.array(top, dtype=np.float32)[None, None, :]
    b = np.array(bottom, dtype=np.float32)[None, None, :]
    arr = a * (1.0 - t) + b * t
    arr = np.broadcast_to(arr, (h, w, arr.shape[2]))
    return Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8), "RGBA")


def mask_layer(size: tuple[int, int], mask: Image.Image, color,
               alpha: int = 255) -> Image.Image:
    """单色 + L 蒙版 → RGBA 图层。"""
    w, h = int(size[0]), int(size[1])
    base = Image.new("RGBA", (w, h), (int(color[0]), int(color[1]), int(color[2]), 0))
    m = mask
    if m.size != (w, h):
        m = m.resize((w, h), Image.BILINEAR)
    m = m.convert("L")
    if alpha < 255:
        m = m.point(lambda v: int(v * alpha / 255.0))
    base.putalpha(m)
    return base


def glow_layer(size: tuple[int, int], blobs: Iterable[tuple], color,
               power: float = 2.0, downscale: int = 6,
               blur: float = 0.0) -> Image.Image:
    """径向柔光：blobs = [(cx_px, cy_px, rx_px, ry_px, strength 0-1), ...]。"""
    w, h = max(2, int(size[0])), max(2, int(size[1]))
    ds = max(2, int(downscale))
    sw, sh = max(8, w // ds), max(8, h // ds)
    mask = np.zeros((sh, sw), dtype=np.float32)
    yy, xx = np.mgrid[0:sh, 0:sw].astype(np.float32)
    for blob in blobs:
        try:
            cx, cy, rx, ry = (float(blob[i]) for i in range(4))
            strength = float(blob[4]) if len(blob) > 4 else 1.0
        except (TypeError, ValueError, IndexError):
            continue
        sx, sy = cx / ds, cy / ds
        srx = max(0.8, rx / ds)
        sry = max(0.8, ry / ds)
        dist = np.sqrt(((xx - sx) / srx) ** 2 + ((yy - sy) / sry) ** 2)
        falloff = np.clip(1.0 - dist, 0.0, 1.0) ** max(0.2, float(power))
        mask = np.maximum(mask, falloff * max(0.0, min(1.0, strength)))
    mask_u8 = (np.clip(mask, 0.0, 1.0) * 255.0).astype(np.uint8)
    small = Image.fromarray(mask_u8, "L")
    if blur > 0:
        small = small.filter(ImageFilter.GaussianBlur(float(blur)))
    full = small.resize((w, h), Image.BILINEAR)
    return mask_layer((w, h), full, color)


def soft_layer(size: tuple[int, int], painter: Callable, blur: float = 6.0,
               downscale: int = 4) -> Image.Image:
    """在小图（1/downscale）上画好 → 模糊 → 放大：烟雾/雾/光晕等柔边元素。

    painter(draw, sw, sh) 使用小图像素坐标绘制。
    """
    w, h = max(2, int(size[0])), max(2, int(size[1]))
    ds = max(2, int(downscale))
    sw, sh = max(8, w // ds), max(8, h // ds)
    small = Image.new("RGBA", (sw, sh), (0, 0, 0, 0))
    draw = ImageDraw.Draw(small)
    painter(draw, sw, sh)
    if blur > 0:
        small = small.filter(ImageFilter.GaussianBlur(float(blur)))
    return small.resize((w, h), Image.BILINEAR)


def blurred_layer(img: Image.Image, blur: float) -> Image.Image:
    """模糊 RGBA 图层（alpha 一起模糊）。"""
    return img.filter(ImageFilter.GaussianBlur(float(max(0.0, blur))))


# ===========================================================================
# 6. 几何/路径/描边 helper
# ===========================================================================
def _stable_hash(text: str) -> int:
    return int(zlib.crc32(str(text).encode("utf-8")) & 0xFFFFFFFF)


def _f(spec: dict, key: str, default: float) -> float:
    try:
        return float(spec.get(key, default))
    except (TypeError, ValueError):
        return float(default)


def _i(spec: dict, key: str, default: int) -> int:
    try:
        return int(spec.get(key, default))
    except (TypeError, ValueError):
        return int(default)


def chaikin(points: Sequence[Sequence[float]], iterations: int = 2) -> list[tuple[float, float]]:
    """Chaikin 曲线细分：折线 → 平滑曲线（切角法）。"""
    pts = [(float(p[0]), float(p[1])) for p in points]
    if len(pts) < 3:
        return pts
    for _ in range(max(1, int(iterations))):
        out: list[tuple[float, float]] = [pts[0]]
        for a, b in zip(pts, pts[1:]):
            out.append((0.75 * a[0] + 0.25 * b[0], 0.75 * a[1] + 0.25 * b[1]))
            out.append((0.25 * a[0] + 0.75 * b[0], 0.25 * a[1] + 0.75 * b[1]))
        out.append(pts[-1])
        pts = out
    return pts


def jitter_points(points: Sequence[Sequence[float]], amp: float,
                  rng: random.Random) -> list[tuple[float, float]]:
    """点列加随机抖动（手绘/飞白/木刻毛边）。"""
    a = max(0.0, float(amp))
    return [(float(p[0]) + rng.uniform(-a, a), float(p[1]) + rng.uniform(-a, a))
            for p in points]


def as_points(seq, ctx: "SceneCtx", smooth: int = 0) -> list[tuple[float, float]]:
    """归一化点列 → 像素点列（可顺带 chaikin 平滑）。"""
    if not isinstance(seq, (list, tuple)) or len(seq) < 2:
        return []
    pts: list[tuple[float, float]] = []
    for item in seq:
        if isinstance(item, (list, tuple)) and len(item) >= 2:
            pts.append((ctx.X(float(item[0])), ctx.Y(float(item[1]))))
    if smooth and len(pts) > 2:
        pts = chaikin(pts, smooth)
    return pts


def path_length(points: Sequence[Sequence[float]]) -> float:
    return sum(math.hypot(b[0] - a[0], b[1] - a[1]) for a, b in zip(points, points[1:]))


def point_at(points: Sequence[Sequence[float]], t: float) -> tuple[float, float]:
    """沿折线取 t∈[0,1] 处的点。"""
    if not points:
        return (0.0, 0.0)
    if len(points) == 1:
        return (float(points[0][0]), float(points[0][1]))
    total = path_length(points)
    if total <= 1e-6:
        return (float(points[-1][0]), float(points[-1][1]))
    target = max(0.0, min(1.0, float(t))) * total
    walked = 0.0
    for a, b in zip(points, points[1:]):
        seg = math.hypot(b[0] - a[0], b[1] - a[1])
        if walked + seg >= target:
            k = (target - walked) / max(seg, 1e-6)
            return (a[0] + (b[0] - a[0]) * k, a[1] + (b[1] - a[1]) * k)
        walked += seg
    return (float(points[-1][0]), float(points[-1][1]))


def brush_line(draw: ImageDraw.ImageDraw, pts: Sequence[Sequence[float]], fill,
               width: float, taper: bool = False) -> None:
    """折线描边；taper=True 模拟毛笔（中间粗两端细）。"""
    points = [(int(round(p[0])), int(round(p[1]))) for p in pts]
    if len(points) < 2:
        if points:
            r = max(1, int(round(float(width) / 2.0)))
            x, y = points[0]
            draw.ellipse((x - r, y - r, x + r, y + r), fill=fill)
        return
    w = max(1, int(round(float(width))))
    if not taper or len(points) < 4:
        draw.line(points, fill=fill, width=w, joint="curve")
        return
    n = len(points)
    for i in range(n - 1):
        t = i / float(max(1, n - 2))
        factor = 0.35 + 0.65 * math.sin(math.pi * t) ** 0.6
        seg_w = max(1, int(round(w * factor)))
        draw.line([points[i], points[i + 1]], fill=fill, width=seg_w)
    r = max(1, int(round(w * 0.42)))
    for x, y in points:
        draw.ellipse((x - r, y - r, x + r, y + r), fill=fill)


def dashed_line(draw: ImageDraw.ImageDraw, pts: Sequence[Sequence[float]], fill,
                width: float, dash: float = 18.0, gap: float = 12.0) -> None:
    """虚线折线（沿路径按弧长切段）。"""
    points = [(float(p[0]), float(p[1])) for p in pts]
    if len(points) < 2:
        return
    dash = max(1.0, float(dash))
    gap = max(1.0, float(gap))
    w = max(1, int(round(float(width))))
    period = dash + gap
    travelled = 0.0
    for a, b in zip(points, points[1:]):
        seg = math.hypot(b[0] - a[0], b[1] - a[1])
        if seg <= 1e-6:
            continue
        pos = 0.0
        while pos < seg:
            phase = (travelled + pos) % period
            if phase < dash:
                run = min(seg - pos, dash - phase)
                x0 = a[0] + (b[0] - a[0]) * (pos / seg)
                y0 = a[1] + (b[1] - a[1]) * (pos / seg)
                x1 = a[0] + (b[0] - a[0]) * ((pos + run) / seg)
                y1 = a[1] + (b[1] - a[1]) * ((pos + run) / seg)
                draw.line([(x0, y0), (x1, y1)], fill=fill, width=w)
                pos += run
            else:
                pos += min(seg - pos, period - phase)
        travelled += seg


def arrow_head(draw: ImageDraw.ImageDraw, tip: Sequence[float], angle: float,
               size: float, fill) -> None:
    """箭头（angle 为前进方向弧度）。"""
    cx, cy = float(tip[0]), float(tip[1])
    s = max(2.0, float(size))
    spread = math.pi / 7.0
    p1 = (cx - s * math.cos(angle - spread), cy - s * math.sin(angle - spread))
    p2 = (cx - s * math.cos(angle + spread), cy - s * math.sin(angle + spread))
    draw.polygon([(cx, cy), p1, p2], fill=fill)


def hatch_layer(size: tuple[int, int], mask: Image.Image, bbox,
                angle_deg: float, spacing: float, color,
                width: int = 1, alpha: int = 255) -> Image.Image:
    """在 mask 范围内画平行排线（铜版/木刻阴影）。bbox 为像素 (x0,y0,x1,y1)。"""
    w, h = int(size[0]), int(size[1])
    layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    x0, y0, x1, y1 = (float(v) for v in bbox)
    cx, cy = (x0 + x1) / 2.0, (y0 + y1) / 2.0
    length = math.hypot(x1 - x0, y1 - y0) * 0.75 + 4.0
    ang = math.radians(float(angle_deg))
    dx, dy = math.cos(ang), math.sin(ang)
    nx, ny = -dy, dx
    step = max(1.5, float(spacing))
    offset = -length
    while offset <= length:
        sx = cx + nx * offset - dx * length
        sy = cy + ny * offset - dy * length
        ex = cx + nx * offset + dx * length
        ey = cy + ny * offset + dy * length
        draw.line([(sx, sy), (ex, ey)], fill=tuple(int(c) for c in color[:3]) + (int(alpha),),
                  width=max(1, int(width)))
        offset += step
    a = layer.getchannel("A")
    m = mask.convert("L")
    if m.size != (w, h):
        m = m.resize((w, h), Image.BILINEAR)
    layer.putalpha(ImageChops.multiply(a, m))
    return layer


# ===========================================================================
# 7. 场景上下文（primitive 使用）
# ===========================================================================
class SceneCtx:
    """单层绘制上下文：坐标换算 / 调色板 / 确定性 rng / 风格主题。

    - ``scene_w/scene_h``：整幅场景像素尺寸（归一化坐标基准）；
    - ``w/h``：当前图层像素尺寸（clip 时是 clip 窗口尺寸）；
    - ``off_x/off_y``：clip 窗口在整幅场景中的像素原点；
    - ``X/Y/PX/PY``：归一化 → 当前图层像素坐标（已扣掉 clip 偏移）；
    - ``S``：归一化尺寸 → 像素（按场景短边）；
    - ``color``：风格调色板取色（角色名 / 角色:索引 / #hex）。
    """

    __slots__ = ("scene_w", "scene_h", "w", "h", "off_x", "off_y", "style",
                 "scene", "seed", "rng", "theme", "layer_name", "palette",
                 "line", "texture")

    def __init__(self, scene_w: int, scene_h: int, img_size: tuple[int, int],
                 style: dict, scene: dict, seed: int, rng: random.Random,
                 layer_name: str = "midground", offset: tuple[float, float] = (0.0, 0.0)):
        self.scene_w = int(scene_w)
        self.scene_h = int(scene_h)
        self.w = int(img_size[0])
        self.h = int(img_size[1])
        self.off_x = float(offset[0])
        self.off_y = float(offset[1])
        self.style = style if isinstance(style, dict) else {}
        self.scene = scene if isinstance(scene, dict) else {}
        self.seed = int(seed)
        self.rng = rng
        self.theme = theme_flags(self.style)
        self.layer_name = str(layer_name or "midground")
        self.palette = self.style.get("palette") or {}
        self.line = self.style.get("line") or {}
        self.texture = self.style.get("texture") or []

    # -- 坐标 -------------------------------------------------------------
    def X(self, value: float) -> float:
        return float(value) * self.scene_w - self.off_x

    def Y(self, value: float) -> float:
        return float(value) * self.scene_h - self.off_y

    def PX(self, value: float) -> int:
        return int(round(self.X(value)))

    def PY(self, value: float) -> int:
        return int(round(self.Y(value)))

    def S(self, value: float) -> float:
        """归一化尺寸 → 像素（按场景短边缩放）。"""
        return float(value) * min(self.scene_w, self.scene_h)

    def box(self, spec: dict, defaults=(0.0, 0.0, 1.0, 1.0)) -> tuple[float, float, float, float]:
        """x/y/w/h 归一化 → 当前图层像素 (x0, y0, x1, y1)。"""
        vals = []
        for key, dflt in zip(("x", "y", "w", "h"), defaults):
            vals.append(_f(spec, key, dflt))
        x0, y0 = self.X(vals[0]), self.Y(vals[1])
        x1, y1 = self.X(vals[0] + vals[2]), self.Y(vals[1] + vals[3])
        return (x0, y0, x1, y1)

    def clip_box(self, spec: dict) -> tuple[int, int, int, int]:
        """clip 归一化 → 场景像素 (x, y, w, h)，已 clamp 在画布内。"""
        x = _f(spec, "x", 0.0)
        y = _f(spec, "y", 0.0)
        w = max(0.01, _f(spec, "w", 1.0))
        h = max(0.01, _f(spec, "h", 1.0))
        x0 = max(0, min(self.scene_w - 1, int(round(x * self.scene_w))))
        y0 = max(0, min(self.scene_h - 1, int(round(y * self.scene_h))))
        x1 = max(x0 + 1, min(self.scene_w, int(round((x + w) * self.scene_w))))
        y1 = max(y0 + 1, min(self.scene_h, int(round((y + h) * self.scene_h))))
        return (x0, y0, x1 - x0, y1 - y0)

    # -- 颜色 -------------------------------------------------------------
    def color(self, ref=None, fallback: str = "fg", index: int = 0,
              alpha: int = 255) -> tuple[int, int, int, int]:
        return resolve_color(self.palette, ref, fallback=fallback, index=index, alpha=alpha)

    def spec_color(self, spec: dict, key: str = "color", fallback: str = "fg",
                   alpha: int | None = None) -> tuple[int, int, int, int]:
        """读 layer 的 color/palette 字段（缺省用 primitive 的 fallback 角色）。"""
        if not isinstance(spec, dict):
            return self.color(None, fallback=fallback)
        ref = spec.get(key)
        if ref is None:
            ref = spec.get("palette") or spec.get("palette_key")
        a = int(alpha) if alpha is not None else int(_f(spec, "alpha", 255))
        return self.color(ref, fallback=fallback, alpha=a)

    # -- 随机 / 主题 -------------------------------------------------------
    def rng_from(self, salt) -> random.Random:
        return random.Random(_stable_hash(f"{self.seed}|{salt}|{self.layer_name}"))

    def theme_on(self, key: str) -> bool:
        return bool(self.theme.get(str(key)))

    # 常用主题快捷属性
    @property
    def ink(self) -> bool:
        return bool(self.theme.get("ink"))

    @property
    def woodcut(self) -> bool:
        return bool(self.theme.get("woodcut"))

    @property
    def engrave(self) -> bool:
        return bool(self.theme.get("engrave"))

    @property
    def gold(self) -> bool:
        return bool(self.theme.get("gold"))

    @property
    def silk(self) -> bool:
        return bool(self.theme.get("silk"))


# ===========================================================================
# 8. 图层默认归属（自定义 primitive 不写 layer 时落 midground）
# ===========================================================================
DEFAULT_LAYER: dict[str, str] = {
    # 背景
    "paper": "background", "canvas": "background", "sky_gradient": "background",
    "marble": "background", "mountains": "background", "terrain_contours": "background",
    "skyline": "background", "brick_wall": "background", "torn_paper": "background",
    "blueprint_lines": "background", "film_border": "background", "fog": "background",
    "cross_section_grid": "background", "halftone": "background",
    # 前景（氛围/遮挡）
    "spotlights": "foreground", "smoke": "foreground", "rain": "foreground",
    "snow": "foreground", "particles": "foreground", "vignette": "foreground",
    "grain": "foreground", "hologram_grid": "foreground", "chalk_lines": "foreground",
    "stickers": "foreground",
}


# ===========================================================================
# 9. 场景渲染主流程
# ===========================================================================
def _layer_name(spec: dict) -> str:
    raw = str(spec.get("layer") or "").strip().lower()
    raw = LAYER_ALIASES.get(raw, raw)
    if raw in LAYERS:
        return raw
    if raw:
        raise V5Error(f"layer {raw!r} 非法（只允许 background/midground/foreground）")
    return DEFAULT_LAYER.get(str(spec.get("type") or ""), "midground")


def _layer_applies(spec: dict, style_id: str) -> bool:
    styles = spec.get("styles")
    if not styles:
        return True
    if isinstance(styles, str):
        styles = [styles]
    return str(style_id) in {str(s) for s in styles}


def _apply_variant(layers: list, variant: str) -> list[dict]:
    out: list[dict] = []
    for spec in layers:
        if not isinstance(spec, dict):
            raise V5Error(f"scene.layers 元素必须是 dict，得到 {type(spec).__name__}")
        merged = dict(spec)
        per = spec.get("variants")
        if variant and isinstance(per, dict):
            patch = per.get(variant)
            if isinstance(patch, dict):
                merged.update(patch)
        merged.pop("variants", None)
        out.append(merged)
    return out


def _scale_alpha(img: Image.Image, opacity: float) -> Image.Image:
    opacity = max(0.0, min(1.0, float(opacity)))
    if opacity >= 0.999:
        return img
    alpha = img.getchannel("A").point(lambda v: int(v * opacity))
    out = img.copy()
    out.putalpha(alpha)
    return out


def _validate_rect(value, name: str) -> list[float]:
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        raise V5Error(f"{name} 必须是 [x, y, w, h]（归一化），得到 {value!r}")
    try:
        return [float(v) for v in value]
    except (TypeError, ValueError) as exc:
        raise V5Error(f"{name} 含非数字: {value!r}") from exc


def render_scene_layers(scene: dict, style: dict, size: tuple[int, int],
                        seed: int = 0) -> dict:
    """按场景 spec + 风格渲染三层 RGBA（契约见 docs/V5_INTERFACES.md §1）。

    返回::

        {
          "background": Image(RGBA, size),          # 天空/远景/纸纹（不透明）
          "midground":  Image(RGBA, size),          # 建筑/地形/道具
          "foreground": Image(RGBA, size) | None,   # 前景遮挡/氛围（无前景层时 None）
          "depth": {"background":0.02,"midground":0.12,"foreground":0.3},
          "anchors": {role: (x, y)},                # 归一化脚底锚点
          "camera_zones": {zone: [x,y,w,h]},        # 归一化取景窗
          "meta": {"scene","style","variants", ...},
        }
    """
    started = time.perf_counter()
    if not isinstance(scene, dict):
        raise V5Error(f"scene 必须是 dict，得到 {type(scene).__name__}")
    if not isinstance(style, dict):
        raise V5Error(f"style 必须是 dict，得到 {type(style).__name__}")
    try:
        width, height = int(size[0]), int(size[1])
    except (TypeError, ValueError, IndexError) as exc:
        raise V5Error(f"size 必须是 (width, height)，得到 {size!r}") from exc
    if width < 32 or height < 32:
        raise V5Error(f"size 太小: {width}x{height}")

    scene_id = str(scene.get("id") or scene.get("scene") or "scene")
    style_id = str(style.get("id") or style.get("style") or "style")
    palette = style.get("palette")
    if not isinstance(palette, dict) or not palette:
        raise V5Error(f"style {style_id!r} 缺少 palette")
    layers_raw = scene.get("layers")
    if not isinstance(layers_raw, list) or not layers_raw:
        raise V5Error(f"scene {scene_id!r} 缺少 layers（非空 list）")

    variants = [str(v) for v in (scene.get("variants") or []) if str(v).strip()]
    variant = str(scene.get("variant") or "").strip()
    if variant and variants and variant not in variants:
        raise V5Error(f"scene {scene_id!r} 变体 {variant!r} 未在 variants 中声明: {variants}")
    layers = _apply_variant(layers_raw, variant)

    plate_path = _scene_plate_path(scene)
    plate_art = ""
    if plate_path is not None:
        plate_layers = _render_plate_layers(scene, style, (width, height), seed, plate_path)
        imgs = {name: plate_layers[name] for name in LAYERS}
        used = [{"index": 0, "type": f"plate:{plate_path.name}", "layer": "background"}]
        foreground_used = True
        plate_art = str(plate_path)
        LOG.debug("scene %s 使用 AI plate: %s", scene_id, plate_path)
    else:
        imgs = {
            name: Image.new("RGBA", (width, height), (0, 0, 0, 0)) for name in LAYERS
        }
        used = []
        foreground_used = False
    for index, spec in enumerate(layers if plate_path is None else []):
        if not _layer_applies(spec, style_id):
            continue
        name = str(spec.get("type") or "").strip()
        if not name:
            raise V5Error(f"scene {scene_id!r} 第 {index} 层缺少 type")
        draw_fn = primitive(name)
        target = _layer_name(spec)
        if target == "foreground":
            foreground_used = True
        used.append({"index": index, "type": name, "layer": target})

        layer_seed = int(seed) + _i(spec, "seed", 0)
        rng = random.Random(_stable_hash(
            f"{scene_id}|{style_id}|{index}|{name}|{layer_seed}|{variant}"
        ))
        ctx = SceneCtx(width, height, (width, height), style, scene,
                       layer_seed, rng, target)
        clip = spec.get("clip")
        if clip is not None:
            cx, cy, cw, ch = ctx.clip_box(spec)
            sub = Image.new("RGBA", (cw, ch), (0, 0, 0, 0))
            sub_ctx = SceneCtx(width, height, (cw, ch), style, scene,
                               layer_seed, rng, target, offset=(cx, cy))
            draw_fn(sub, spec, sub_ctx)
            canvas = Image.new("RGBA", (width, height), (0, 0, 0, 0))
            canvas.alpha_composite(sub, (cx, cy))
            layer_img = canvas
        else:
            layer_img = Image.new("RGBA", (width, height), (0, 0, 0, 0))
            draw_fn(layer_img, spec, ctx)
        opacity = _f(spec, "opacity", 1.0)
        if opacity < 0.999:
            layer_img = _scale_alpha(layer_img, opacity)
        imgs[target].alpha_composite(layer_img)

    # 背景必须不透明（缺省用 paper 色兜底）
    background = imgs["background"]
    if background.getchannel("A").getextrema()[0] < 255:
        paper = resolve_color(palette, "paper", fallback="bg")
        base = Image.new("RGBA", (width, height), paper)
        base.alpha_composite(background)
        background = base

    depth = dict(DEFAULT_DEPTH)
    raw_depth = scene.get("depth")
    if isinstance(raw_depth, dict):
        for key in LAYERS:
            if key in raw_depth:
                try:
                    depth[key] = float(raw_depth[key])
                except (TypeError, ValueError) as exc:
                    raise V5Error(f"scene {scene_id!r} depth.{key} 非法: {raw_depth[key]!r}") from exc

    anchors_raw = scene.get("anchors")
    anchors: dict[str, tuple[float, float]] = {}
    if isinstance(anchors_raw, dict) and anchors_raw:
        for role, pair in anchors_raw.items():
            if not isinstance(pair, (list, tuple)) or len(pair) < 2:
                raise V5Error(f"scene {scene_id!r} anchors.{role} 必须是 [x, y]")
            try:
                anchors[str(role)] = (float(pair[0]), float(pair[1]))
            except (TypeError, ValueError) as exc:
                raise V5Error(f"scene {scene_id!r} anchors.{role} 含非数字") from exc
    else:
        anchors = dict(DEFAULT_ANCHORS)

    zones_raw = scene.get("camera_zones")
    zones: dict[str, list[float]] = {}
    if isinstance(zones_raw, dict) and zones_raw:
        for zone, rect in zones_raw.items():
            zones[str(zone)] = _validate_rect(rect, f"scene {scene_id!r} camera_zones.{zone}")
    else:
        zones = {k: list(v) for k, v in DEFAULT_CAMERA_ZONES.items()}

    elapsed_ms = round((time.perf_counter() - started) * 1000.0, 2)
    meta = {
        "scene": scene_id,
        "label": str(scene.get("label") or ""),
        "style": style_id,
        "art": plate_art or "procedural",
        "variants": variants,
        "variant": variant,
        "size": [width, height],
        "seed": int(seed),
        "depth": depth,
        "layers": used,
        "foreground": bool(foreground_used),
        "tags": [str(t) for t in (scene.get("tags") or [])],
        "aspect": list(scene.get("aspect") or [16, 9]),
        "elapsed_ms": elapsed_ms,
        "primitive_count": len(_PRIMITIVES),
    }
    return {
        "background": background.convert("RGBA"),
        "midground": imgs["midground"].convert("RGBA"),
        "foreground": imgs["foreground"].convert("RGBA") if foreground_used else None,
        "depth": depth,
        "anchors": anchors,
        "camera_zones": zones,
        "meta": meta,
    }


def flatten_layers(result: dict) -> Image.Image:
    """把 render_scene_layers 的结果压成单张 RGBA（预览/静帧用）。"""
    if not isinstance(result, dict) or "background" not in result:
        raise V5Error("flatten_layers 需要 render_scene_layers 的返回值")
    background = result["background"]
    canvas = background.convert("RGBA").copy()
    for name in ("midground", "foreground"):
        layer = result.get(name)
        if isinstance(layer, Image.Image):
            canvas.alpha_composite(layer.convert("RGBA"))
    return canvas


def validate_scene(scene: dict, style: dict | None = None) -> list[str]:
    """场景自检（返回错误字符串列表；不 raise）。供 CLI / 校验器复用。"""
    errors: list[str] = []
    if not isinstance(scene, dict):
        return ["scene 不是 dict"]
    scene_id = str(scene.get("id") or "?")
    layers = scene.get("layers")
    if not isinstance(layers, list) or not layers:
        errors.append(f"{scene_id}: layers 缺失或为空")
        return errors
    palette = (style or {}).get("palette") if isinstance(style, dict) else None
    palette = palette if isinstance(palette, dict) else None
    variants = {str(v) for v in (scene.get("variants") or [])}
    for index, spec in enumerate(layers):
        if not isinstance(spec, dict):
            errors.append(f"{scene_id}[{index}]: layer 不是 dict")
            continue
        name = str(spec.get("type") or "").strip()
        if not name:
            errors.append(f"{scene_id}[{index}]: 缺少 type")
            continue
        if name not in _PRIMITIVES:
            errors.append(f"{scene_id}[{index}]: 未知 primitive {name!r}")
            continue
        try:
            _layer_name(spec)
        except V5Error as exc:
            errors.append(f"{scene_id}[{index}]: {exc}")
        per = spec.get("variants")
        if isinstance(per, dict):
            for key in per:
                if variants and str(key) not in variants:
                    errors.append(f"{scene_id}[{index}]: 变体 {key!r} 未在顶层 variants 声明")
        clip = spec.get("clip")
        if clip is not None and (not isinstance(clip, (list, tuple)) or len(clip) != 4):
            errors.append(f"{scene_id}[{index}]: clip 必须是 [x,y,w,h]")
        if palette:
            for key in ("color", "fill", "stroke", "palette"):
                ref = spec.get(key)
                if not isinstance(ref, str) or ref.startswith("#"):
                    continue
                role = ref.split(":")[0]
                try:
                    resolve_role(palette, role)
                except V5Error as exc:
                    errors.append(f"{scene_id}[{index}]: {exc}")
    if "anchors" in scene:
        anchors = scene.get("anchors")
        if not isinstance(anchors, dict):
            errors.append(f"{scene_id}: anchors 必须是 dict")
        else:
            for role, pair in anchors.items():
                if not isinstance(pair, (list, tuple)) or len(pair) < 2:
                    errors.append(f"{scene_id}: anchors.{role} 必须是 [x, y]")
    if "camera_zones" in scene:
        zones = scene.get("camera_zones")
        if not isinstance(zones, dict):
            errors.append(f"{scene_id}: camera_zones 必须是 dict")
        else:
            for zone, rect in zones.items():
                if not isinstance(rect, (list, tuple)) or len(rect) != 4:
                    errors.append(f"{scene_id}: camera_zones.{zone} 必须是 [x,y,w,h]")
    return errors


# ===========================================================================
# 10. CLI（预览，不占 GPU）
# ===========================================================================
def _safe_print(text: str) -> None:
    try:
        print(text)
    except UnicodeEncodeError:  # pragma: no cover - 老控制台
        print(text.encode("utf-8", "replace").decode("utf-8", "replace"))


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m pipeline.scene_render",
        description="V5 场景渲染预览（PIL，无 GPU）",
    )
    parser.add_argument("--scene", help="场景 id（contract/v5/scenes/<id>.json）")
    parser.add_argument("--style", help="风格 id（contract/v5/styles/<id>.json）")
    parser.add_argument("--out", help="输出 PNG 路径（默认 tmp/v5_preview/<scene>__<style>.png）")
    parser.add_argument("--width", type=int, default=DEFAULT_SIZE[0])
    parser.add_argument("--height", type=int, default=DEFAULT_SIZE[1])
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--variant", default="", help="场景变体 id")
    parser.add_argument("--root", default="", help="contract/v5 根目录（默认仓库内）")
    parser.add_argument("--no-finish", action="store_true", help="跳过 style_v5.apply_finish 后处理")
    parser.add_argument("--list-primitives", action="store_true", help="列出已注册 primitive")
    parser.add_argument("--check-scenes", action="store_true", help="校验全部场景 JSON")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    if args.list_primitives:
        names = list_primitives()
        _safe_print(f"已注册 primitive: {len(names)}")
        _safe_print(", ".join(names))
        return 0
    from . import style_v5

    root = Path(args.root) if args.root else None
    if args.check_scenes:
        catalog = style_v5.load_catalog(root)
        scenes = catalog.get("scenes") or {}
        styles = catalog.get("styles") or {}
        total_errors = 0
        for scene_id, scene in sorted(scenes.items()):
            style_id = str((scene.get("styles") or ["ink_chronicle"])[0]) if scene.get("styles") else ""
            style = styles.get(style_id) or (next(iter(styles.values()), None) if styles else None)
            errors = validate_scene(scene, style)
            if errors:
                total_errors += len(errors)
                _safe_print(f"FAIL {scene_id}: {len(errors)} 个问题")
                for err in errors:
                    _safe_print(f"  - {err}")
        _safe_print(f"场景 {len(scenes)} 个，错误 {total_errors} 条")
        return 0 if total_errors == 0 else 1
    if not args.scene or not args.style:
        _safe_print("需要 --scene 与 --style（或用 --list-primitives/--check-scenes）")
        return 2
    catalog = style_v5.load_catalog(root)
    style = style_v5.get_style(catalog, args.style)
    scenes = catalog.get("scenes") or {}
    if args.scene not in scenes:
        _safe_print(f"场景不存在: {args.scene}（可用: {', '.join(sorted(scenes))}）")
        return 2
    scene = dict(scenes[args.scene])
    if args.variant:
        scene["variant"] = args.variant
    started = time.perf_counter()
    result = render_scene_layers(scene, style, (args.width, args.height), seed=args.seed)
    image = flatten_layers(result)
    if not args.no_finish:
        image = style_v5.apply_finish(image, style, kind="plate")
    out = Path(args.out) if args.out else (
        REPO / "tmp" / "v5_preview" / f"{args.scene}__{args.style}.png"
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    image.convert("RGB").save(out, format="PNG", optimize=True)
    elapsed_ms = round((time.perf_counter() - started) * 1000.0, 1)
    payload = {
        "out": str(out.resolve()),
        "scene": args.scene,
        "style": args.style,
        "size": [args.width, args.height],
        "variant": scene.get("variant", ""),
        "layers": len(result["meta"]["layers"]),
        "elapsed_ms": elapsed_ms,
        "render_ms": result["meta"]["elapsed_ms"],
        "anchors": {k: list(v) for k, v in result["anchors"].items()},
        "camera_zones": result["camera_zones"],
    }
    _safe_print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


# ===========================================================================
# 11. 内置 primitive — 通用小工具
# ===========================================================================
def _len(ctx: SceneCtx, spec: dict, key: str, default) -> float:
    """长度参数：<0.25 视为归一化（按短边），否则按 1600 宽基准像素。"""
    value = spec.get(key, default)
    if isinstance(value, (list, tuple)):
        try:
            value = ctx.rng.uniform(float(value[0]), float(value[1]))
        except (TypeError, ValueError, IndexError):
            value = default if not isinstance(default, (list, tuple)) else default[0]
    try:
        v = float(value)
    except (TypeError, ValueError):
        v = 4.0
    return ctx.S(v) if abs(v) <= 0.25 else v * (ctx.scene_w / 1600.0)


def _poly_mask(size: tuple[int, int], polygons: Iterable[Sequence]) -> Image.Image:
    """多边形列表 → L 蒙版（用于填充/排线局部化）。"""
    w, h = int(size[0]), int(size[1])
    mask = Image.new("L", (w, h), 0)
    draw = ImageDraw.Draw(mask)
    for poly in polygons:
        pts = [(float(p[0]), float(p[1])) for p in poly]
        if len(pts) >= 3:
            draw.polygon(pts, fill=255)
    return mask


def _noise_mask(size: tuple[int, int], seed: int, scale: float,
                octaves: int = 4) -> np.ndarray:
    """小图平滑噪声（0-1，float32）；尺寸为图层 1/2，供纸纹/斑驳。"""
    w, h = int(size[0]), int(size[1])
    sw, sh = max(8, w // 2), max(8, h // 2)
    return smooth_noise(sw, sh, scale=max(3.0, min(sw, sh) * float(scale)),
                        seed=seed, octaves=octaves)


def _composite_masks(img: Image.Image, masks: Sequence[tuple], size: tuple[int, int]) -> None:
    """按 (mask_u8_small, color, alpha_scale) 依次上色并合成。"""
    w, h = int(size[0]), int(size[1])
    for small, color, scale in masks:
        m = Image.fromarray(np.clip(small * float(scale), 0, 255).astype(np.uint8), "L")
        img.alpha_composite(mask_layer((w, h), m, color))


# ===========================================================================
# 12. 内置 primitive — 纸/布/天空/大理石（background 基底）
# ===========================================================================
def p_paper(img: Image.Image, spec: dict, ctx: SceneCtx) -> None:
    """纸底：宣纸/木刻纸/羊皮纸/皮革（按 style.technique 换纹理）。"""
    w, h = ctx.w, ctx.h
    base = ctx.spec_color(spec, "color", "paper")
    light = mix(base, ctx.color("highlight"), 0.22)
    dark = mix(base, ctx.color("shadow"), 0.10)
    img.alpha_composite(vgradient(w, h, light, dark))
    seed = ctx.seed + _i(spec, "seed", 0)
    mottle = max(0.0, min(2.0, _f(spec, "mottle", 0.55)))
    if mottle > 0.02:
        noise = _noise_mask((w, h), seed, 0.16, octaves=5)
        dark_m = np.clip((noise - 0.52) / 0.48, 0, 1) * 255.0 * mottle
        light_m = np.clip((0.48 - noise) / 0.48, 0, 1) * 255.0 * mottle * 0.8
        _composite_masks(img, [
            (dark_m, mix(base, ctx.color("shadow"), 0.55), 0.30),
            (light_m, ctx.color("highlight"), 0.22),
        ], (w, h))
    rng = ctx.rng_from(f"paper{seed}")
    # 纸纤维：宣纸=短横向纤维；木刻纸=长纤维；羊皮纸=污渍斑块
    if ctx.ink or "rice_paper" in [str(t) for t in ctx.texture]:
        count = _i(spec, "fibers", 90)
        for _ in range(max(0, count)):
            x = rng.uniform(0, w)
            y = rng.uniform(0, h)
            ln = rng.uniform(0.006, 0.035) * w
            ang = rng.uniform(-0.12, 0.12)
            c = mix(base, ctx.color("shadow"), rng.uniform(0.15, 0.5))
            draw = ImageDraw.Draw(img)
            draw.line([(x, y), (x + ln, y + ln * ang)],
                      fill=c[:3] + (rng.randint(8, 30),), width=1)
    if ctx.woodcut:
        for _ in range(_i(spec, "fibers", 55)):
            x = rng.uniform(-0.1, w)
            y = rng.uniform(0, h)
            ln = rng.uniform(0.05, 0.22) * w
            c = mix(base, ctx.color("wood"), rng.uniform(0.15, 0.45))
            ImageDraw.Draw(img).line([(x, y), (x + ln, y + rng.uniform(-2, 2))],
                                     fill=c[:3] + (rng.randint(10, 34),), width=1)
    if ctx.engrave:
        for _ in range(_i(spec, "stains", 14)):
            cx, cy = rng.uniform(0, w), rng.uniform(0, h)
            rx, ry = rng.uniform(0.03, 0.16) * w, rng.uniform(0.02, 0.10) * h
            c = mix(base, ctx.color("shadow"), rng.uniform(0.25, 0.55))
            glow = glow_layer((w, h), [(cx, cy, rx, ry, rng.uniform(0.20, 0.5))],
                              c, power=1.4, downscale=8)
            img.alpha_composite(glow)
    if ctx.gold:
        # 博物馆深底 + 极轻金粉
        for _ in range(_i(spec, "specks", 90)):
            x, y = rng.uniform(0, w), rng.uniform(0, h)
            r = max(1.0, ctx.S(rng.uniform(0.0004, 0.0012)))
            c = ctx.color("highlight")
            ImageDraw.Draw(img).ellipse((x - r, y - r, x + r, y + r),
                                        fill=c[:3] + (rng.randint(14, 52),))
    edge = max(0.0, min(1.0, _f(spec, "edge", 0.45)))
    if edge > 0.02:
        sw, sh = 64, 36
        yy, xx = np.mgrid[0:sh, 0:sw].astype(np.float32)
        dx = np.minimum(xx, sw - 1 - xx) / (sw * 0.5)
        dy = np.minimum(yy, sh - 1 - yy) / (sh * 0.5)
        border = np.clip(1.0 - np.minimum(dx / 0.06, dy / 0.08), 0.0, 1.0) ** 1.5
        img.alpha_composite(mask_layer(
            (w, h), Image.fromarray((border * 255).astype(np.uint8), "L"),
            ctx.color("shadow"), int(70 * edge)))


def p_canvas(img: Image.Image, spec: dict, ctx: SceneCtx) -> None:
    """布底/绢底：织纹 + 轻微皱褶（军帐、帐篷、绢本）。"""
    w, h = ctx.w, ctx.h
    base = ctx.spec_color(spec, "color", "paper")
    img.alpha_composite(vgradient(w, h, mix(base, ctx.color("highlight"), 0.12), shade(base, 0.94)))
    draw = ImageDraw.Draw(img)
    step = max(4, int(_len(ctx, spec, "thread", 7)))
    light = mix(base, ctx.color("highlight"), 0.30)[:3]
    dark = mix(base, ctx.color("shadow"), 0.22)[:3]
    for x in range(0, w, step):
        draw.line([(x, 0), (x, h)], fill=light + (int(26 if (x // step) % 2 else 16),), width=1)
        draw.line([(x + step // 2, 0), (x + step // 2, h)], fill=dark + (18,), width=1)
    for y in range(0, h, step):
        draw.line([(0, y), (w, y)], fill=light + (22,), width=1)
        draw.line([(0, y + step // 2), (w, y + step // 2)], fill=dark + (16,), width=1)
    if _f(spec, "crease", 0.35) > 0.02:
        for _ in range(_i(spec, "wrinkles", 5)):
            x0 = ctx.rng.uniform(0.1, 0.9)
            pts = [(ctx.X(x0 + i * 0.02), ctx.Y(ctx.rng.uniform(0.0, 1.0))) for i in range(3)]
            brush_line(draw, chaikin(pts, 2), dark + (20,), max(1, int(ctx.S(0.002))))


def p_sky_gradient(img: Image.Image, spec: dict, ctx: SceneCtx) -> None:
    """天空渐变 + 地平线雾带 + 可选日/月。"""
    w, h = ctx.w, ctx.h
    top = ctx.color(spec.get("top") or "sky:0", fallback="bg", index=0)
    bottom = ctx.color(spec.get("bottom") or "sky:1", fallback="bg", index=1)
    img.alpha_composite(vgradient(w, h, top, bottom))
    horizon = _f(spec, "horizon", 0.62)
    if _f(spec, "haze", 0.55) > 0.02:
        hy = ctx.Y(horizon)
        c = ctx.color(spec.get("haze_color") or "highlight", fallback="highlight")
        band = glow_layer((w, h), [(w * 0.5, hy, w * 0.62, h * 0.055,
                                    _f(spec, "haze", 0.55))], c, power=1.3, downscale=8)
        img.alpha_composite(band)
    sun = spec.get("sun")
    if isinstance(sun, dict):
        sx, sy = ctx.X(_f(sun, "x", 0.72)), ctx.Y(_f(sun, "y", 0.30))
        r = ctx.S(_f(sun, "r", 0.065))
        c = ctx.color(sun.get("color") or "accent", fallback="accent")
        img.alpha_composite(glow_layer((w, h), [(sx, sy, r * 5.5, r * 5.5, 0.55)],
                                       c, power=2.2, downscale=6))
        draw = ImageDraw.Draw(img)
        draw.ellipse((sx - r, sy - r, sx + r, sy + r), fill=mix(c, ctx.color("highlight"), 0.45))
    # 云带（可选，默认 0 层）
    for i in range(_i(spec, "clouds", 0)):
        cy = ctx.Y(_f(spec, "y", 0.30) + i * 0.06)
        cx = ctx.X(0.5 + 0.2 * math.sin(i * 2.1))
        c = mix(ctx.color("highlight"), ctx.color("bg"), 0.25)
        img.alpha_composite(glow_layer((w, h), [(cx, cy, w * 0.3, h * 0.045, 0.5)],
                                       c, power=1.2, downscale=8))


def p_marble(img: Image.Image, spec: dict, ctx: SceneCtx) -> None:
    """大理石/深色石材底：渐变 + 纹理脉络 + 抛光高光（博物馆金黑）。"""
    w, h = ctx.w, ctx.h
    base = ctx.spec_color(spec, "color", "paper")
    dark = mix(base, ctx.color("shadow"), 0.45)
    img.alpha_composite(vgradient(w, h, mix(base, ctx.color("highlight"), 0.10), dark))
    rng = ctx.rng_from(f"marble{ctx.seed}")
    vein_color = ctx.color(spec.get("vein") or "highlight", fallback="highlight")
    vein_layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    vd = ImageDraw.Draw(vein_layer)
    count = max(2, _i(spec, "veins", 8))
    for _ in range(count):
        pts = [(ctx.X(-0.05 + i * 0.16 + rng.uniform(-0.04, 0.04)),
                ctx.Y(rng.uniform(-0.1, 1.1))) for i in range(8)]
        pts = chaikin(pts, 3)
        width = _len(ctx, spec, "vein_width", rng.uniform(0.001, 0.004))
        alpha = rng.randint(26, 74)
        brush_line(vd, pts, vein_color[:3] + (alpha,), max(1, int(width)), taper=True)
        if rng.random() < 0.4:  # 细分支
            k = rng.randint(1, len(pts) - 2)
            branch = chaikin([pts[k], (pts[k][0] + rng.uniform(-0.1, 0.1) * w,
                                       pts[k][1] + rng.uniform(0.05, 0.2) * h),
                              (pts[k][0] + rng.uniform(-0.16, 0.16) * w,
                               pts[k][1] + rng.uniform(0.15, 0.35) * h)], 2)
            brush_line(vd, branch, vein_color[:3] + (max(12, alpha - 18),),
                       max(1, int(width * 0.6)))
    img.alpha_composite(vein_layer.filter(ImageFilter.GaussianBlur(max(0.6, h * 0.0012))))
    if ctx.gold:  # 金色反光带
        sheen = vgradient(w, h, (255, 240, 200, 0), (255, 240, 200, 0))
        sa = np.zeros((36, 64), dtype=np.uint8)
        yy, xx = np.mgrid[0:36, 0:64].astype(np.float32)
        band = np.exp(-((xx - 64 * _f(spec, "sheen_x", 0.62)) ** 2) / (2 * (64 * 0.10) ** 2))
        sa = (band * 255 * _f(spec, "sheen", 0.35)).astype(np.uint8)
        img.alpha_composite(mask_layer((w, h), Image.fromarray(sa, "L"),
                                       ctx.color("highlight"), 90))


# ===========================================================================
# 13. 内置 primitive — 地形/地图（history 核心）
# ===========================================================================
def p_mountains(img: Image.Image, spec: dict, ctx: SceneCtx) -> None:
    """远山/山脊（多层视差）：水墨=淡彩晕染+飞白脊线；版刻=实块+粗线；铜版=排线。"""
    w, h = ctx.w, ctx.h
    count = max(1, min(6, _i(spec, "count", 3)))
    base_y_norm = _f(spec, "y", 0.62)
    height_norm = _f(spec, "height", 0.36)
    color = ctx.spec_color(spec, "color", "fg")
    bg_color = ctx.color("bg")
    seed = ctx.seed + _i(spec, "seed", 0)
    rng = ctx.rng_from(f"mount{seed}")
    for i in range(count):
        t = i / max(1, count - 1)
        base_y = ctx.Y(base_y_norm - 0.03 + t * 0.06)
        ridge = ctx.S(height_norm) * (1.0 - 0.26 * t)
        ctrl = np.array([rng.random() for _ in range(11)], dtype=np.float32)
        ctrl = np.convolve(np.pad(ctrl, (1, 1), mode="edge"),
                           np.array([0.25, 0.5, 0.25], dtype=np.float32), mode="valid")
        xs = np.linspace(-0.05, 1.05, 160)
        vals = np.interp(xs, np.linspace(0, 1, len(ctrl)), ctrl)
        vals = np.clip(0.5 + (vals - 0.5) * 1.7, 0.0, 1.0) ** 0.85
        top_y = base_y - ridge * (0.16 + 0.84 * vals)
        # 山脊平滑成起伏曲线（chaikin），避免折线三角感
        ridge_pts = chaikin([(float(ctx.X(float(x))), float(yv))
                             for x, yv in zip(xs, top_y)], 2)
        poly = ridge_pts + [(float(ctx.X(1.05)), float(ctx.Y(1.02))),
                            (float(ctx.X(-0.05)), float(ctx.Y(1.02)))]
        mask = _poly_mask((w, h), [poly])
        fill_color = mix(color, bg_color, max(0.0, 0.42 - 0.32 * t))
        ridge_c = mix(color, ctx.color("ink", fallback="fg"), 0.35)
        draw = ImageDraw.Draw(img)
        if ctx.ink:
            # 水墨：羽化边缘的淡墨块 + 湿笔脊线 + 山脚二次渲染
            soft = mask.filter(ImageFilter.GaussianBlur(max(1.2, ctx.S(0.0035))))
            img.alpha_composite(mask_layer((w, h), soft, fill_color, int(96 + 62 * t)))
            foot = _poly_mask((w, h), [[(x, min(float(ctx.Y(1.02)), y + ctx.S(0.05)))
                                        for x, y in ridge_pts] + [
                (float(ctx.X(1.05)), float(ctx.Y(1.02))),
                (float(ctx.X(-0.05)), float(ctx.Y(1.02)))]]).filter(
                ImageFilter.GaussianBlur(max(1.0, ctx.S(0.006))))
            img.alpha_composite(mask_layer((w, h), foot, shade(fill_color, 0.82),
                                           int(42 + 30 * t)))
            ridge_line = ridge_pts[2:-2:2]
            brush_line(draw, ridge_line, ridge_c[:3] + (205,),
                       max(2, int(ctx.S(0.0042) * (0.85 + 0.4 * t))), taper=True)
            brush_line(draw, ridge_line[4:-4] if len(ridge_line) > 12 else ridge_line,
                       mix(ridge_c, bg_color, 0.45)[:3] + (120,),
                       max(1, int(ctx.S(0.0018))))
        elif ctx.engrave:
            img.alpha_composite(mask_layer((w, h), mask,
                                           mix(fill_color, bg_color, 0.35), 205))
            img.alpha_composite(hatch_layer(
                (w, h), mask, (0, top_y.min(), w, h),
                -38 + i * 5, max(4, int(ctx.S(0.008))), color, width=1, alpha=122))
            brush_line(draw, ridge_pts[2:-2], color[:3] + (225,),
                       max(1, int(ctx.S(0.0018))))
        elif ctx.woodcut:
            img.alpha_composite(mask_layer((w, h), mask, fill_color, 235))
            img.alpha_composite(hatch_layer(
                (w, h), mask, (0, top_y.min(), w, h),
                52, max(8, int(ctx.S(0.019))), color,
                width=max(2, int(ctx.S(0.002))), alpha=92))
            brush_line(draw, ridge_pts[2:-2], color[:3] + (255,),
                       max(2, int(ctx.S(0.0042))))
        elif ctx.gold:
            img.alpha_composite(mask_layer((w, h), mask, mix(color, bg_color, 0.45), 220))
            gc = ctx.color("accent")
            brush_line(draw, ridge_pts[2:-2], gc[:3] + (165,),
                       max(1, int(ctx.S(0.0024))))
            img.alpha_composite(glow_layer(
                (w, h), [(w * 0.5, top_y.mean(), w * 0.5, h * 0.12, 0.18)],
                gc, power=1.6, downscale=8))
        else:
            img.alpha_composite(mask_layer((w, h), mask, fill_color,
                                           int(175 + 60 * t)))
            brush_line(draw, ridge_pts[2:-2],
                       mix(color, bg_color, 0.15)[:3] + (200,),
                       max(1, int(ctx.S(0.0024))))


def p_terrain_contours(img: Image.Image, spec: dict, ctx: SceneCtx) -> None:
    """等高线（古地图/发掘地形图）：大尺度噪声等值线，两级线宽。

    参数 scale 为特征尺度（相对短边，默认 0.42）；levels 为等高线条数（默认 6）；
    step 为每几级加粗一条（默认 2）。
    """
    w, h = ctx.w, ctx.h
    levels = max(3, min(16, _i(spec, "levels", 6)))
    step = max(1, _i(spec, "step", 2))
    seed = ctx.seed + _i(spec, "seed", 11)
    scale = max(0.10, _f(spec, "scale", 0.42))
    color = ctx.spec_color(spec, "color", "shadow")
    bold_color = ctx.color(spec.get("bold_color") or "fg", fallback="fg")
    sw, sh = max(32, w // 2), max(24, h // 2)
    field = smooth_noise(sw, sh, scale=max(6.0, min(sw, sh) * scale),
                         seed=seed, octaves=4)
    q = np.floor(field * float(levels)).astype(np.int16)
    edge = np.zeros((sh, sw), dtype=bool)
    edge[:, 1:] |= q[:, 1:] != q[:, :-1]
    edge[1:, :] |= q[1:, :] != q[:-1, :]
    bold = edge & (q % step == 0)
    thin = edge & ~bold
    img.alpha_composite(mask_layer((w, h), Image.fromarray(
        (thin * 255).astype(np.uint8), "L").resize((w, h), Image.BILINEAR),
        color, int(_f(spec, "alpha", 130))))
    img.alpha_composite(mask_layer((w, h), Image.fromarray(
        (bold * 255).astype(np.uint8), "L").resize((w, h), Image.BILINEAR),
        bold_color, int(_f(spec, "bold_alpha", 190))))
    # 高/低地淡彩（让地图有明暗层次；默认很轻）
    shade = _f(spec, "shade", 0.18)
    if shade > 0.02:
        low = (field < 0.40).astype(np.uint8) * 255
        high = (field > 0.68).astype(np.uint8) * 255
        img.alpha_composite(mask_layer(
            (w, h), Image.fromarray(low, "L").resize((w, h), Image.BILINEAR),
            mix(ctx.color("bg"), ctx.color("accent"), 0.35), int(60 * shade)))
        img.alpha_composite(mask_layer(
            (w, h), Image.fromarray(high, "L").resize((w, h), Image.BILINEAR),
            mix(ctx.color("paper"), ctx.color("shadow"), 0.30), int(46 * shade)))


def p_rivers(img: Image.Image, spec: dict, ctx: SceneCtx) -> None:
    """河流/水系（地图）：蜿蜒水带 + 岸线 + 波纹（版刻=短排线）。"""
    w, h = ctx.w, ctx.h
    count = max(1, min(4, _i(spec, "count", 2)))
    color = ctx.spec_color(spec, "color", "water")
    rng = ctx.rng_from(f"river{ctx.seed}{_i(spec, 'seed', 0)}")
    width = _len(ctx, spec, "width", [2, 5])
    draw = ImageDraw.Draw(img)
    for i in range(count):
        y0 = _f(spec, "y", 0.52) + i * _f(spec, "gap", 0.16) + rng.uniform(-0.04, 0.04)
        pts = [(ctx.X(-0.06), ctx.Y(y0))]
        steps = 5
        for k in range(1, steps + 1):
            x = -0.06 + k * (1.12 / steps)
            y = y0 + math.sin(k * 1.7 + i) * 0.07 + rng.uniform(-0.035, 0.035)
            pts.append((ctx.X(x), ctx.Y(y)))
        pts = chaikin(pts, 2)
        w_main = max(1.0, width * (1.0 + i * 0.35))
        # 水带柔光
        img.alpha_composite(soft_layer((w, h), lambda d, sw, sh, p=pts: [
            d.line([(x / 2, y / 2) for x, y in p], fill=color[:3] + (90,),
                   width=max(1, int(w_main * 0.9)), joint="curve")], blur=3, downscale=2))
        brush_line(draw, pts, color[:3] + (200,), max(1, int(w_main * 1.5)), taper=False)
        # 中心高光
        brush_line(draw, pts, mix(color, ctx.color("highlight"), 0.5)[:3] + (150,),
                   max(1, int(w_main * 0.6)))
        if ctx.woodcut or ctx.engrave:
            for k in range(0, len(pts) - 1, 3):
                x, y = pts[k]
                ln = _len(ctx, spec, "ripple", 0.010) * rng.uniform(0.6, 1.4)
                draw.line([(x - ln, y + 2), (x + ln, y + 2)],
                          fill=ctx.color("fg")[:3] + (110,), width=1)
        elif not ctx.ink:
            for k in range(0, len(pts) - 2, 4):
                x, y = pts[k]
                ln = _len(ctx, spec, "ripple", 0.012) * rng.uniform(0.7, 1.5)
                draw.line([(x - ln, y + 2), (x + ln, y + 2)],
                          fill=mix(color, ctx.color("highlight"), 0.6)[:3] + (120,), width=1)


def p_waves(img: Image.Image, spec: dict, ctx: SceneCtx) -> None:
    """水面波纹（渡口/江面）：横向正弦波纹行 + 浪花短线。"""
    w, h = ctx.w, ctx.h
    color = ctx.spec_color(spec, "color", "water")
    rows = max(3, _i(spec, "count", 14))
    y0 = _f(spec, "y", 0.58)
    span = _f(spec, "span", 0.42)
    amp = _f(spec, "amp", 0.006)
    freq = _f(spec, "freq", 5.0)
    rng = ctx.rng_from(f"waves{ctx.seed}")
    draw = ImageDraw.Draw(img)
    for row in range(rows):
        t = row / max(1, rows - 1)
        y_base = ctx.Y(y0 + span * t)
        a = ctx.S(amp * (0.6 + 0.8 * t))
        phase = rng.uniform(0, math.tau)
        pts = []
        for k in range(0, 41):
            x = ctx.X(-0.02 + k * 0.026)
            yv = y_base + math.sin(k * 0.38 * freq * 0.2 + phase) * a
            pts.append((x, yv))
        alpha = int(70 + 120 * t)
        brush_line(draw, pts, mix(color, ctx.color("highlight"), 0.15 * t)[:3] + (alpha,),
                   max(1, int(ctx.S(0.0012 + 0.0018 * t))))
        if row % 3 == 0:
            for k in range(2, 39, 6):
                x, yv = pts[k]
                ln = _len(ctx, spec, "foam", 0.008) * rng.uniform(0.6, 1.4)
                draw.line([(x, yv), (x + ln, yv + rng.uniform(-1, 1))],
                          fill=ctx.color("highlight")[:3] + (int(alpha * 0.7),), width=1)


def p_grid(img: Image.Image, spec: dict, ctx: SceneCtx) -> None:
    """网格：正交网格 / 透视地格 / 剖面网格（发掘/蓝图/博物馆地砖）。"""
    w, h = ctx.w, ctx.h
    color = ctx.spec_color(spec, "color", "mid")
    spacing = max(6, int(_len(ctx, spec, "spacing", 0.045)))
    alpha = int(_f(spec, "alpha", 120))
    draw = ImageDraw.Draw(img)
    fade = max(0.0, min(1.0, _f(spec, "fade", 0.55)))
    if _i(spec, "perspective", 0) == 1 or _f(spec, "y_horizon", -1) >= 0:
        yh = ctx.Y(_f(spec, "y_horizon", 0.55))
        vx = ctx.X(_f(spec, "vanish_x", 0.5))
        for x in range(0, w + spacing, spacing):
            c = color[:3] + (int(alpha * (0.5 + 0.5 * fade)),)
            draw.line([(vx, yh), (x, h)], fill=c, width=1)
        row_y = yh
        step = max(4.0, h * 0.012)
        i = 0
        while row_y < h:
            a = int(alpha * (0.35 + 0.65 * min(1.0, (row_y - yh) / max(1.0, h - yh))))
            draw.line([(0, row_y), (w, row_y)], fill=color[:3] + (a,), width=1)
            i += 1
            step *= 1.22
            row_y += step
    else:
        for x in range(0, w + spacing, spacing):
            draw.line([(x, 0), (x, h)], fill=color[:3] + (alpha,), width=1)
        for y in range(0, h + spacing, spacing):
            draw.line([(0, y), (w, y)], fill=color[:3] + (alpha,), width=1)


def p_brick_wall(img: Image.Image, spec: dict, ctx: SceneCtx) -> None:
    """砖墙（现代场景/仓库）：错缝砖 + 灰缝 + 斑驳。"""
    w, h = ctx.w, ctx.h
    base = ctx.spec_color(spec, "color", "stone")
    mortar = ctx.color(spec.get("mortar") or "shadow", fallback="shadow")
    img.alpha_composite(vgradient(w, h, mix(base, ctx.color("highlight"), 0.10),
                                 mix(base, mortar, 0.25)))
    rng = ctx.rng_from(f"brick{ctx.seed}")
    rows = max(4, _i(spec, "rows", 14))
    brick_h = h / rows
    brick_w = max(20.0, brick_h * _f(spec, "ratio", 2.4))
    draw = ImageDraw.Draw(img)
    for row in range(rows):
        y0 = row * brick_h
        offset = (brick_w / 2.0) if row % 2 else 0.0
        x = -offset
        while x < w + brick_w:
            c = shade(mix(base, ctx.color("highlight"), rng.uniform(0.0, 0.10)),
                      rng.uniform(0.88, 1.06))
            draw.rectangle((x + 1, y0 + 1, x + brick_w - 2, y0 + brick_h - 2),
                           fill=c[:3] + (255,))
            x += brick_w
    for row in range(rows + 1):
        y = row * brick_h
        draw.line([(0, y), (w, y)], fill=mortar[:3] + (150,), width=max(1, int(ctx.S(0.0018))))
    noise = _noise_mask((w, h), ctx.seed + 5, 0.30, octaves=4)
    _composite_masks(img, [(np.clip((noise - 0.55) / 0.45, 0, 1) * 255,
                            ctx.color("shadow"), 0.22)], (w, h))


def p_halftone(img: Image.Image, spec: dict, ctx: SceneCtx) -> None:
    """半调网点（拼贴/漫画/波普）：网点大小随噪声变化。"""
    w, h = ctx.w, ctx.h
    color = ctx.spec_color(spec, "color", "fg")
    spacing = max(4, int(_len(ctx, spec, "spacing", 0.009)))
    r_max = spacing * _f(spec, "radius", 0.5)
    noise = _noise_mask((w, h), ctx.seed + 7, 0.22, octaves=4)
    small = Image.fromarray((noise * 255).astype(np.uint8), "L").resize(
        (max(2, w // spacing), max(2, h // spacing)), Image.BILINEAR)
    field = np.asarray(small, dtype=np.float32) / 255.0
    draw = ImageDraw.Draw(img)
    for gy in range(field.shape[0]):
        for gx in range(field.shape[1]):
            r = r_max * (0.15 + 0.85 * float(field[gy, gx]))
            if r < 0.4:
                continue
            cx = gx * spacing + spacing * 0.5
            cy = gy * spacing + spacing * 0.5
            draw.ellipse((cx - r, cy - r, cx + r, cy + r), fill=color[:3] + (235,))


def p_grain(img: Image.Image, spec: dict, ctx: SceneCtx) -> None:
    """颗粒/胶片噪点（叠加在已画内容上，alpha 合成）。"""
    w, h = ctx.w, ctx.h
    strength = max(0.0, min(1.0, _f(spec, "strength", 0.16)))
    if strength <= 0.001:
        return
    rng = np.random.default_rng((ctx.seed + _i(spec, "seed", 0)) & 0xFFFFFFFF)
    sw, sh = max(8, w // 2), max(8, h // 2)
    noise = rng.standard_normal((sh, sw), dtype=np.float32)
    noise = np.clip(noise * 0.5 + 0.5, 0.0, 1.0)
    luma = Image.fromarray((noise * 255).astype(np.uint8), "L")
    k = strength * 2.6
    dark = luma.point(lambda v: int(max(0, 128 - v) * k))
    light = luma.point(lambda v: int(max(0, v - 128) * k))
    img.alpha_composite(mask_layer((w, h), dark.resize((w, h), Image.BILINEAR),
                                   ctx.color(spec.get("color") or "shadow", fallback="shadow")))
    img.alpha_composite(mask_layer((w, h), light.resize((w, h), Image.BILINEAR),
                                   ctx.color(spec.get("light") or "highlight", fallback="highlight")))


def p_vignette(img: Image.Image, spec: dict, ctx: SceneCtx) -> None:
    """暗角/压角（可只压上下、可反向提亮）。"""
    w, h = ctx.w, ctx.h
    strength = max(0.0, min(1.5, _f(spec, "strength", 0.25)))
    if strength <= 0.002:
        return
    sw, sh = 72, 40
    yy, xx = np.mgrid[0:sh, 0:sw].astype(np.float32)
    nx = (xx - (sw - 1) / 2.0) / ((sw - 1) / 2.0)
    ny = (yy - (sh - 1) / 2.0) / ((sh - 1) / 2.0)
    radius = max(0.05, min(1.2, _f(spec, "radius", 0.72)))
    power = max(0.2, _f(spec, "power", 1.6))
    mode = str(spec.get("mode") or "radial")
    if mode == "top_bottom":
        m = np.clip((np.abs(ny) - radius) / max(0.05, 1.0 - radius), 0, 1) ** power
    elif mode == "left_right":
        m = np.clip((np.abs(nx) - radius) / max(0.05, 1.0 - radius), 0, 1) ** power
    else:
        d = np.sqrt(nx ** 2 + ny ** 2)
        m = np.clip((d - radius) / max(0.05, 1.0 - radius), 0, 1) ** power
    mask = Image.fromarray(np.clip(m * 255 * strength, 0, 255).astype(np.uint8), "L")
    color = ctx.color(spec.get("color") or "shadow", fallback="shadow")
    if str(spec.get("mode") or "") == "lift":
        color = ctx.color(spec.get("color") or "highlight", fallback="highlight")
    img.alpha_composite(mask_layer((w, h), mask.resize((w, h), Image.BILINEAR), color))


def p_fog(img: Image.Image, spec: dict, ctx: SceneCtx) -> None:
    """雾带/水汽（前景）：多条柔化横向云带。"""
    w, h = ctx.w, ctx.h
    color = ctx.spec_color(spec, "color", "highlight")
    count = max(1, min(8, _i(spec, "count", 3)))
    y0 = _f(spec, "y", 0.72)
    spread = _f(spec, "spread", 0.10)
    alpha = int(_f(spec, "alpha", 120))
    rng = ctx.rng_from(f"fog{ctx.seed}")

    def painter(draw: ImageDraw.ImageDraw, sw: int, sh: int) -> None:
        for i in range(count):
            cy = (y0 + i * spread * 0.6) * sh
            for k in range(5):
                cx = (0.12 + k * 0.2 + rng.uniform(-0.06, 0.06)) * sw
                rx, ry = (0.18 + rng.uniform(0, 0.12)) * sw, (0.025 + rng.uniform(0, 0.02)) * sh
                draw.ellipse((cx - rx, cy - ry, cx + rx, cy + ry),
                             fill=color[:3] + (alpha // max(1, count),))

    img.alpha_composite(soft_layer((w, h), painter, blur=8, downscale=6))


# ===========================================================================
# 14. 内置 primitive — 地图标注（city_markers / route_arrows / compass / seal）
# ===========================================================================
def _fake_text_column(draw: ImageDraw.ImageDraw, x: float, y_top: float, y_bottom: float,
                      size: float, color, rng: random.Random) -> None:
    """竖排“伪文字”列：每个字位画 2-4 笔抽象笔画（远看像书法/雕版字）。"""
    cell = max(7.0, float(size) * 1.18)
    y = float(y_top)
    stroke_w = max(1, int(cell * 0.10))
    while y + cell <= float(y_bottom):
        cx, cy = float(x), y + cell * 0.5
        half = cell * 0.34
        for _ in range(rng.randint(2, 4)):
            kind = rng.random()
            if kind < 0.45:
                sy = cy + rng.uniform(-half, half)
                draw.line([(cx - half, sy), (cx + half, sy + rng.uniform(-1, 1))],
                          fill=color, width=stroke_w)
            elif kind < 0.80:
                sx = cx + rng.uniform(-half, half)
                draw.line([(sx, cy - half * 0.8), (sx, cy + half * 0.8)],
                          fill=color, width=stroke_w)
            else:
                draw.line([(cx - half * 0.8, cy - half * 0.6),
                           (cx + half * 0.8, cy + half * 0.5)],
                          fill=color, width=stroke_w)
        y += cell


def p_city_markers(img: Image.Image, spec: dict, ctx: SceneCtx) -> None:
    """城池标记（古地图）：朱印方/圆点/关隘三种，带投影与微标签。"""
    w, h = ctx.w, ctx.h
    count = max(1, min(40, _i(spec, "count", 9)))
    size = _len(ctx, spec, "size", 0.016)
    color = ctx.spec_color(spec, "color", "accent")
    dark = mix(color, ctx.color("fg"), 0.45)
    style = str(spec.get("style") or "seal")
    y0, y1 = _f(spec, "y0", 0.14), _f(spec, "y1", 0.84)
    rng = ctx.rng_from(f"city{ctx.seed}{_i(spec, 'seed', 0)}")
    draw = ImageDraw.Draw(img)
    for _ in range(count):
        cx = ctx.X(rng.uniform(0.06, 0.94))
        cy = ctx.Y(rng.uniform(y0, y1))
        s = size * rng.uniform(0.7, 1.45)
        draw.ellipse((cx - s * 0.9, cy + s * 0.25, cx + s * 0.9, cy + s * 1.0),
                     fill=ctx.color("shadow")[:3] + (60,))
        if style == "dot":
            draw.ellipse((cx - s, cy - s, cx + s, cy + s), fill=color[:3] + (235,))
            draw.ellipse((cx - s * 1.9, cy - s * 1.9, cx + s * 1.9, cy + s * 1.9),
                         outline=dark[:3] + (190,), width=max(1, int(s * 0.22)))
        elif style == "fort":
            bw, bh = s * 2.2, s * 1.5
            draw.rectangle((cx - bw / 2, cy - bh / 2, cx + bw / 2, cy + bh / 2),
                           fill=color[:3] + (225,))
            for k in range(3):
                mx = cx - bw / 2 + k * bw / 3 + bw / 12
                draw.rectangle((mx, cy - bh / 2 - s * 0.42, mx + bw / 6, cy - bh / 2),
                               fill=color[:3] + (225,))
            draw.rectangle((cx - bw / 2, cy - bh / 2, cx + bw / 2, cy + bh / 2),
                           outline=dark[:3] + (230,), width=max(1, int(s * 0.18)))
        else:  # seal：方印 + 内框 + 中心点
            bw = s * 1.9
            draw.rectangle((cx - bw / 2, cy - bw / 2, cx + bw / 2, cy + bw / 2),
                           fill=color[:3] + (225,))
            draw.rectangle((cx - bw / 2 + bw * 0.16, cy - bw / 2 + bw * 0.16,
                            cx + bw / 2 - bw * 0.16, cy + bw / 2 - bw * 0.16),
                           outline=ctx.color("paper")[:3] + (235,),
                           width=max(1, int(bw * 0.09)))
            draw.ellipse((cx - bw * 0.16, cy - bw * 0.16, cx + bw * 0.16, cy + bw * 0.16),
                         fill=ctx.color("paper")[:3] + (235,))
        # 微标签（两条短横线模拟地名）
        if rng.random() < 0.55:
            ly = cy + s * 1.6
            lw = s * rng.uniform(1.4, 2.8)
            draw.line([(cx, ly), (cx + lw, ly)], fill=dark[:3] + (150,),
                      width=max(1, int(s * 0.16)))
            if rng.random() < 0.6:
                draw.line([(cx, ly + s * 0.7), (cx + lw * 0.7, ly + s * 0.7)],
                          fill=dark[:3] + (110,), width=max(1, int(s * 0.14)))


def p_route_arrows(img: Image.Image, spec: dict, ctx: SceneCtx) -> None:
    """行军路线（古地图）：平滑路径 + 虚线/箭头/节点；羊皮纸风格细化成红线牵引。"""
    w, h = ctx.w, ctx.h
    color = ctx.spec_color(spec, "color", "accent")
    width = _len(ctx, spec, "width", 4)
    raw = spec.get("path")
    if isinstance(raw, (list, tuple)) and len(raw) >= 2:
        pts = as_points(raw, ctx, smooth=2)
    else:
        pts = as_points([(0.14, 0.72), (0.28, 0.62), (0.34, 0.48), (0.50, 0.44),
                         (0.58, 0.30), (0.74, 0.26), (0.84, 0.16)], ctx, smooth=2)
    if len(pts) < 2:
        return
    draw = ImageDraw.Draw(img)
    dashed = bool(spec.get("dashed", True))
    # 柔光底（让路线从纸面浮起）
    img.alpha_composite(soft_layer(
        (w, h), lambda d, sw, sh, p=pts: d.line(
            [(x / 2, y / 2) for x, y in p], fill=color[:3] + (40,),
            width=max(1, int(width)), joint="curve"), blur=3, downscale=2))
    if ctx.engrave and not dashed:
        pass
    if dashed:
        dash = _len(ctx, spec, "dash", 22)
        gap = _len(ctx, spec, "gap", 14)
        dashed_line(draw, pts, color[:3] + (235,), width, dash=dash, gap=gap)
    else:
        brush_line(draw, pts, color[:3] + (240,), width, taper=ctx.ink)
    # 箭头：终点 + 中间节点
    if bool(spec.get("arrowheads", True)):
        for t in (1.0,) + (tuple(spec.get("arrow_at") or ())):
            try:
                tip = point_at(pts, float(t))
                prev = point_at(pts, max(0.0, float(t) - 0.035))
            except (TypeError, ValueError):
                continue
            ang = math.atan2(tip[1] - prev[1], tip[0] - prev[0])
            arrow_head(draw, tip, ang, width * 2.6, color[:3] + (245,))
    # 节点圆点
    for t in spec.get("nodes") or ():
        try:
            x, y = point_at(pts, float(t))
        except (TypeError, ValueError):
            continue
        r = width * 1.4
        draw.ellipse((x - r, y - r, x + r, y + r), fill=color[:3] + (245,))
        draw.ellipse((x - r * 1.9, y - r * 1.9, x + r * 1.9, y + r * 1.9),
                     outline=color[:3] + (170,), width=max(1, int(r * 0.5)))


def p_compass_rose(img: Image.Image, spec: dict, ctx: SceneCtx) -> None:
    """罗盘/指北针（古地图）：双环 + 八角星 + 刻度 + N/S/E/W（或 北/南/东/西）。"""
    w, h = ctx.w, ctx.h
    cx, cy = ctx.X(_f(spec, "x", 0.85)), ctx.Y(_f(spec, "y", 0.16))
    r = _len(ctx, spec, "size", 0.09) * 0.5
    color = ctx.spec_color(spec, "color", "fg")
    accent = ctx.color(spec.get("accent") or "accent", fallback="accent")
    draw = ImageDraw.Draw(img)
    ring_w = max(1, int(r * 0.05))
    draw.ellipse((cx - r, cy - r, cx + r, cy + r), outline=color[:3] + (200,), width=ring_w)
    draw.ellipse((cx - r * 0.78, cy - r * 0.78, cx + r * 0.78, cy + r * 0.78),
                 outline=color[:3] + (140,), width=max(1, int(ring_w * 0.7)))
    # 刻度
    for k in range(32):
        ang = k * math.tau / 32.0
        r0 = r * (0.86 if k % 4 else 0.72)
        r1 = r * 0.97
        draw.line([(cx + math.cos(ang) * r0, cy + math.sin(ang) * r0),
                   (cx + math.cos(ang) * r1, cy + math.sin(ang) * r1)],
                  fill=color[:3] + (180,), width=1)
    # 八角星
    for k in range(8):
        ang = k * math.tau / 8.0 - math.pi / 2.0
        length = r * (0.70 if k % 2 == 0 else 0.40)
        half = r * (0.10 if k % 2 == 0 else 0.07)
        tipx, tipy = cx + math.cos(ang) * length, cy + math.sin(ang) * length
        px, py = math.cos(ang + math.pi / 2), math.sin(ang + math.pi / 2)
        base = r * 0.10
        poly = [(tipx, tipy),
                (cx + px * half + math.cos(ang) * base,
                 cy + py * half + math.sin(ang) * base),
                (cx - px * half + math.cos(ang) * base,
                 cy - py * half + math.sin(ang) * base)]
        fill = accent if k % 2 == 0 else color
        draw.polygon(poly, fill=fill[:3] + (200 if k % 2 == 0 else 165,))
    draw.ellipse((cx - r * 0.09, cy - r * 0.09, cx + r * 0.09, cy + r * 0.09),
                 fill=accent[:3] + (240,))
    labels = spec.get("labels") or ("N", "E", "S", "W")
    fs = max(9, int(r * 0.30))
    for k, text in enumerate(labels[:4]):
        ang = k * math.tau / 4.0 - math.pi / 2.0
        tx = cx + math.cos(ang) * r * 1.14
        ty = cy + math.sin(ang) * r * 1.14
        draw_text(draw, (tx, ty), str(text), fs, color[:3] + (225,))


def p_seal_stamp(img: Image.Image, spec: dict, ctx: SceneCtx) -> None:
    """印章/朱印：方印或圆印 + 刻字（反白）+ 磨损/缺口 + 轻微倾斜。"""
    w, h = ctx.w, ctx.h
    text = str(spec.get("text") or "史")
    size = _len(ctx, spec, "size", 0.075)
    color = ctx.spec_color(spec, "color", "accent")
    shape = str(spec.get("shape") or "square")
    angle = _f(spec, "angle", -4.0)
    ss = max(12, int(round(size)))
    small = Image.new("RGBA", (ss, ss), (0, 0, 0, 0))
    sd = ImageDraw.Draw(small)
    pad = max(1, int(ss * 0.05))
    if shape == "circle":
        sd.ellipse((pad, pad, ss - pad, ss - pad), fill=color[:3] + (238,))
        sd.ellipse((pad * 2.2, pad * 2.2, ss - pad * 2.2, ss - pad * 2.2),
                   outline=ctx.color("paper")[:3] + (225,), width=max(1, int(ss * 0.035)))
    else:
        sd.rectangle((pad, pad, ss - pad, ss - pad), fill=color[:3] + (238,))
        sd.rectangle((pad * 2.0, pad * 2.0, ss - pad * 2.0, ss - pad * 2.0),
                     outline=ctx.color("paper")[:3] + (232,), width=max(1, int(ss * 0.035)))
    # 刻字：单字居中 / 多字竖排
    paper_c = ctx.color("paper")
    chars = text[:4]
    if len(chars) <= 1:
        draw_text(sd, (ss / 2, ss / 2 + ss * 0.02), chars or "印",
                  int(ss * 0.56), paper_c[:3] + (245,), display=True)
    else:
        cell = ss / (len(chars) + 0.6)
        fs = int(cell * 0.92)
        for k, ch in enumerate(chars):
            draw_text(sd, (ss / 2, cell * (0.62 + k)), ch, fs,
                      paper_c[:3] + (245,), display=True)
    # 磨损：随机纸色缺口
    rng = ctx.rng_from(f"seal{text}{ctx.seed}")
    for _ in range(int(ss * 0.55)):
        x, y = rng.uniform(0, ss), rng.uniform(0, ss)
        r = max(0.6, ss * rng.uniform(0.006, 0.02))
        sd.ellipse((x - r, y - r, x + r, y + r),
                   fill=paper_c[:3] + (rng.randint(60, 190),))
    if abs(angle) > 0.2:
        small = small.rotate(angle, expand=True, resample=Image.BICUBIC)
    px = int(round(ctx.X(_f(spec, "x", 0.08)) - small.width / 2))
    py = int(round(ctx.Y(_f(spec, "y", 0.88)) - small.height / 2))
    shadow = small.getchannel("A").point(lambda v: int(v * 0.22))
    sh_layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    sh_layer.paste((0, 0, 0, 255), (px + 2, py + 3), shadow)
    img.alpha_composite(sh_layer)
    img.alpha_composite(small, (px, py))


# ===========================================================================
# 15. 内置 primitive — 卷轴/竹简/书架/案几/沙盘/文物（历史道具）
# ===========================================================================
def p_scroll(img: Image.Image, spec: dict, ctx: SceneCtx) -> None:
    """卷轴/手卷：纸身 + 两端卷轴 + 竖排文字（真字体或伪书法）+ 系带。"""
    w, h = ctx.w, ctx.h
    box = ctx.box(spec, (0.18, 0.30, 0.64, 0.34))
    x0, y0, x1, y1 = box
    bw, bh = x1 - x0, y1 - y0
    if bw < 8 or bh < 8:
        return
    paper_c = ctx.spec_color(spec, "color", "paper")
    edge_c = mix(paper_c, ctx.color("shadow"), 0.45)
    accent_c = ctx.color(spec.get("accent") or "accent", fallback="accent")
    layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    # 投影
    d.rounded_rectangle((x0 + 6, y0 + 10, x1 + 8, y1 + 14), radius=max(2, int(bh * 0.05)),
                        fill=(0, 0, 0, 65))
    # 纸身
    d.rectangle((x0, y0, x1, y1), fill=paper_c[:3] + (250,))
    layer.alpha_composite(glow_layer(
        (w, h), [(x0 + bw * 0.5, y0 + bh * 0.5, bw * 0.55, bh * 0.6, 0.30)],
        edge_c, power=1.2, downscale=8))
    d.rectangle((x0, y0, x1, y1), outline=edge_c[:3] + (190,),
                width=max(1, int(ctx.S(0.0016))))
    d.line([(x0 + bw * 0.04, y0 + bh * 0.06), (x0 + bw * 0.04, y1 - bh * 0.06)],
           fill=edge_c[:3] + (150,), width=max(1, int(ctx.S(0.0012))))
    d.line([(x1 - bw * 0.04, y0 + bh * 0.06), (x1 - bw * 0.04, y1 - bh * 0.06)],
           fill=edge_c[:3] + (150,), width=max(1, int(ctx.S(0.0012))))
    # 竖排文字
    ink_c = ctx.color("fg")
    text = str(spec.get("text") or "")
    cols = max(1, min(8, _i(spec, "columns", 3)))
    fs = max(9, int(bh * 0.085))
    rng = ctx.rng_from(f"scroll{ctx.seed}")
    for c in range(cols):
        cx = x1 - bw * (0.10 + c * 0.13)
        if text:
            for k, ch in enumerate(text[c::cols]):
                draw_text(d, (cx, y0 + bh * (0.14 + k * 0.11)), ch, fs,
                          ink_c[:3] + (225,), display=True)
        else:
            _fake_text_column(d, cx, y0 + bh * 0.10, y1 - bh * 0.08,
                              fs, ink_c[:3] + (190,), rng)
    # 两端卷轴
    for side in (0, 1):
        rx = x0 if side == 0 else x1
        rw = max(4.0, bh * 0.09)
        body = ((rx - rw, y0 - bh * 0.06, rx, y1 + bh * 0.06) if side == 0
                else (rx, y0 - bh * 0.06, rx + rw, y1 + bh * 0.06))
        d.rounded_rectangle(body, radius=int(rw * 0.5),
                            fill=mix(paper_c, ctx.color("wood"), 0.55)[:3] + (250,))
        d.ellipse((body[0], body[1], body[2], body[1] + rw),
                  fill=mix(paper_c, ctx.color("shadow"), 0.35)[:3] + (250,))
        d.ellipse((body[0], body[3] - rw, body[2], body[3]),
                  fill=mix(paper_c, ctx.color("shadow"), 0.35)[:3] + (250,))
        d.line([(body[0] + rw * 0.35, body[1] + rw * 0.4),
                (body[0] + rw * 0.35, body[3] - rw * 0.4)],
               fill=edge_c[:3] + (120,), width=1)
    # 系带
    if bool(spec.get("tie", True)):
        for t in (0.26, 0.74):
            tx = x0 + bw * t
            d.line([(tx, y0 - bh * 0.10), (tx, y1 + bh * 0.10)],
                   fill=accent_c[:3] + (215,), width=max(2, int(ctx.S(0.0035))))
            d.ellipse((tx - bw * 0.012, y0 - bh * 0.13, tx + bw * 0.012, y1 + bh * 0.13),
                      outline=accent_c[:3] + (200,), width=max(1, int(ctx.S(0.0016))))
    angle = _f(spec, "angle", 0.0)
    if abs(angle) > 0.2:
        layer = layer.rotate(angle, center=(x0 + bw / 2, y0 + bh / 2),
                             resample=Image.BICUBIC)
    img.alpha_composite(layer)


def p_bamboo_slips(img: Image.Image, spec: dict, ctx: SceneCtx) -> None:
    """竹简：单根竹片 + 竹节 + 编绳 + 竖排文字（or 伪书法）。"""
    w, h = ctx.w, ctx.h
    box = ctx.box(spec, (0.20, 0.30, 0.60, 0.34))
    x0, y0, x1, y1 = box
    bw, bh = x1 - x0, y1 - y0
    if bw < 8 or bh < 8:
        return
    count = max(3, min(30, _i(spec, "count", 12)))
    base = ctx.spec_color(spec, "color", "wood")
    paper_c = ctx.color("paper")
    ink_c = ctx.color("fg")
    rng = ctx.rng_from(f"bamboo{ctx.seed}")
    layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    strip_w = bw / float(count)
    for k in range(count):
        sx0 = x0 + k * strip_w + strip_w * 0.06
        sx1 = x0 + (k + 1) * strip_w - strip_w * 0.06
        tone = shade(mix(base, paper_c, rng.uniform(0.10, 0.45)), rng.uniform(0.92, 1.06))
        d.rounded_rectangle((sx0, y0, sx1, y1), radius=max(1, int(strip_w * 0.18)),
                            fill=tone[:3] + (250,))
        # 竹节
        for node_t in (rng.uniform(0.18, 0.30), rng.uniform(0.55, 0.72),
                       rng.uniform(0.82, 0.94)):
            ny = y0 + bh * node_t
            d.line([(sx0 + 1, ny), (sx1 - 1, ny)],
                   fill=mix(tone, ctx.color("shadow"), 0.45)[:3] + (160,), width=1)
        d.line([(sx0 + strip_w * 0.18, y0 + bh * 0.05),
                (sx0 + strip_w * 0.18, y1 - bh * 0.05)],
               fill=mix(tone, ctx.color("shadow"), 0.30)[:3] + (90,), width=1)
        # 文字
        text = str(spec.get("text") or "")
        fs = max(7, int(strip_w * 0.78))
        if text:
            ch = text[k % len(text)]
            draw_text(d, ((sx0 + sx1) / 2, y0 + bh * 0.12), ch, fs,
                      ink_c[:3] + (220,), display=True)
        elif rng.random() < 0.8:
            _fake_text_column(d, (sx0 + sx1) / 2, y0 + bh * 0.12, y1 - bh * 0.10,
                              fs, ink_c[:3] + (185,), rng)
    # 编绳（上下两道）
    for t in (0.20, 0.80):
        cy = y0 + bh * t
        d.line([(x0 - bw * 0.02, cy), (x1 + bw * 0.02, cy)],
               fill=mix(ctx.color("shadow"), ink_c, 0.35)[:3] + (205,),
               width=max(2, int(ctx.S(0.0032))))
        for k in range(count + 1):
            kx = x0 + k * strip_w
            d.ellipse((kx - strip_w * 0.10, cy - strip_w * 0.10,
                       kx + strip_w * 0.10, cy + strip_w * 0.10),
                      outline=mix(ctx.color("shadow"), ink_c, 0.4)[:3] + (170,), width=1)
    angle = _f(spec, "angle", -2.0)
    if abs(angle) > 0.2:
        layer = layer.rotate(angle, center=(x0 + bw / 2, y0 + bh / 2),
                             resample=Image.BICUBIC)
    img.alpha_composite(layer)


def p_shelves(img: Image.Image, spec: dict, ctx: SceneCtx) -> None:
    """书架/档案架：木框 + 隔板 + 书卷/卷轴/匣（颜色取自 palette）。"""
    w, h = ctx.w, ctx.h
    box = ctx.box(spec, (0.0, 0.10, 0.42, 0.80))
    x0, y0, x1, y1 = box
    bw, bh = x1 - x0, y1 - y0
    if bw < 8 or bh < 8:
        return
    wood = ctx.spec_color(spec, "color", "wood")
    dark = mix(wood, ctx.color("fg"), 0.55)
    paper_c = ctx.color("paper")
    accent = ctx.color("accent")
    rng = ctx.rng_from(f"shelf{ctx.seed}")
    rows = max(2, min(8, _i(spec, "rows", 4)))
    draw = ImageDraw.Draw(img)
    # 背板
    draw.rectangle((x0, y0, x1, y1), fill=mix(wood, ctx.color("shadow"), 0.55)[:3] + (240,))
    row_h = bh / rows
    for r in range(rows):
        ry0 = y0 + r * row_h
        # 书本/卷轴
        cx = x0 + bw * 0.04
        while cx < x1 - bw * 0.05:
            iw = bw * rng.uniform(0.035, 0.10)
            ih = row_h * rng.uniform(0.45, 0.78)
            if cx + iw > x1 - bw * 0.03:
                break
            c = rng.choice([paper_c, accent, mix(paper_c, accent, 0.4),
                            mix(wood, paper_c, 0.35), mix(ctx.color("mid"), paper_c, 0.3)])
            draw.rectangle((cx, ry0 + row_h * 0.82 - ih, cx + iw, ry0 + row_h * 0.82),
                           fill=c[:3] + (245,))
            draw.line([(cx + iw * 0.5, ry0 + row_h * 0.82 - ih),
                       (cx + iw * 0.5, ry0 + row_h * 0.82)],
                      fill=dark[:3] + (80,), width=1)
            if rng.random() < 0.22:  # 横放卷轴
                r = min(iw, row_h * 0.20) * 0.8
                draw.ellipse((cx, ry0 + row_h * 0.82 - r * 2, cx + iw, ry0 + row_h * 0.82 - r * 0.2),
                             outline=dark[:3] + (170,), width=max(1, int(r * 0.35)))
            cx += iw + bw * rng.uniform(0.006, 0.02)
        # 隔板
        draw.rectangle((x0, ry0 + row_h * 0.84, x1, ry0 + row_h * 0.92),
                       fill=mix(wood, ctx.color("highlight"), 0.15)[:3] + (250,))
        draw.line([(x0, ry0 + row_h * 0.92), (x1, ry0 + row_h * 0.92)],
                  fill=dark[:3] + (200,), width=max(1, int(ctx.S(0.0018))))
    # 立框
    frame_w = max(2, int(bw * 0.025))
    for bx in (x0, x1 - frame_w):
        draw.rectangle((bx, y0, bx + frame_w, y1), fill=wood[:3] + (250,))
    draw.rectangle((x0, y0, x1, y0 + frame_w), fill=wood[:3] + (250,))
    draw.rectangle((x0, y1 - frame_w, x1, y1), fill=wood[:3] + (250,))
    draw.rectangle((x0, y0, x1, y1), outline=dark[:3] + (210,), width=1)


def p_desk(img: Image.Image, spec: dict, ctx: SceneCtx) -> None:
    """案几/桌面（透视）：桌面 + 前缘 + 腿 + 木纹 + 可放道具。"""
    w, h = ctx.w, ctx.h
    box = ctx.box(spec, (0.12, 0.62, 0.76, 0.30))
    x0, y0, x1, y1 = box
    bw, bh = x1 - x0, y1 - y0
    if bw < 10 or bh < 8:
        return
    wood = ctx.spec_color(spec, "color", "wood")
    dark = mix(wood, ctx.color("fg"), 0.5)
    light = mix(wood, ctx.color("highlight"), 0.25)
    layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    top_h = bh * 0.52
    inset = bw * 0.06
    top_poly = [(x0 + inset, y0), (x1 - inset, y0), (x1, y0 + top_h), (x0, y0 + top_h)]
    # 投影
    layer.alpha_composite(glow_layer(
        (w, h), [(x0 + bw * 0.5, y1 - bh * 0.05, bw * 0.55, bh * 0.16, 0.35)],
        (0, 0, 0, 255), power=1.5, downscale=8))
    d.polygon(top_poly, fill=wood[:3] + (250,))
    # 桌面前缘
    d.polygon([(x0, y0 + top_h), (x1, y0 + top_h), (x1, y0 + top_h + bh * 0.10),
               (x0, y0 + top_h + bh * 0.10)], fill=dark[:3] + (250,))
    # 腿
    leg_w = bw * 0.05
    for lx in (x0 + bw * 0.04, x1 - bw * 0.04 - leg_w):
        d.rectangle((lx, y0 + top_h + bh * 0.10, lx + leg_w, y1), fill=dark[:3] + (250,))
    # 木纹
    rng = ctx.rng_from(f"desk{ctx.seed}")
    for k in range(9):
        t = (k + 0.5) / 9.0
        yy0 = y0 + top_h * t
        yy1 = y0 + top_h * (t + 0.85 / 9.0)
        off = (t - 0.5) * inset * 1.9
        d.line([(x0 + inset + off, yy0), (x1 - inset + off, yy0)],
               fill=mix(wood, light if k % 2 else dark, 0.28)[:3] + (110,), width=1)
    # 桌沿高光
    d.line([(x0 + inset, y0), (x1 - inset, y0)], fill=light[:3] + (180,),
           width=max(1, int(ctx.S(0.0018))))
    d.line([(x0, y0 + top_h), (x1, y0 + top_h)], fill=light[:3] + (160,),
           width=max(1, int(ctx.S(0.0014))))
    # 桌面道具
    items = spec.get("items") or ()
    for item in items:
        cx = x0 + bw * float(item.get("x", 0.5)) if isinstance(item, dict) else x0 + bw * 0.5
        cy = y0 + top_h * float(item.get("y", 0.55)) if isinstance(item, dict) else y0 + top_h * 0.55
        kind = str(item.get("type") if isinstance(item, dict) else item)
        _draw_desk_item(layer, d, cx, cy, top_h, kind, ctx, rng)
    img.alpha_composite(layer)


def _draw_desk_item(layer: Image.Image, d: ImageDraw.ImageDraw, cx: float, cy: float,
                    scale: float, kind: str, ctx: SceneCtx, rng: random.Random) -> None:
    """案几小道具（brush/cup/scroll/inkstone/seal/map）。"""
    s = max(3.0, scale * 0.16)
    fg = ctx.color("fg")
    accent = ctx.color("accent")
    if kind == "brush":
        d.line([(cx - s * 1.6, cy + s * 0.5), (cx + s * 1.4, cy - s * 0.3)],
               fill=mix(ctx.color("wood"), fg, 0.3)[:3] + (240,), width=max(1, int(s * 0.12)))
        d.polygon([(cx + s * 1.4, cy - s * 0.3), (cx + s * 2.0, cy - s * 0.55),
                   (cx + s * 1.6, cy - s * 0.2)], fill=fg[:3] + (240,))
    elif kind == "cup":
        d.rounded_rectangle((cx - s * 0.5, cy - s * 0.55, cx + s * 0.5, cy + s * 0.45),
                            radius=int(s * 0.3), fill=mix(fg, ctx.color("paper"), 0.5)[:3] + (245,))
        d.ellipse((cx - s * 0.5, cy - s * 0.7, cx + s * 0.5, cy - s * 0.35),
                  fill=mix(fg, ctx.color("paper"), 0.65)[:3] + (245,))
        d.ellipse((cx - s * 0.22, cy - s * 0.62, cx + s * 0.22, cy - s * 0.42),
                  fill=mix(fg, accent, 0.3)[:3] + (245,))
    elif kind == "scroll":
        d.rounded_rectangle((cx - s * 1.1, cy - s * 0.22, cx + s * 1.1, cy + s * 0.22),
                            radius=int(s * 0.1), fill=ctx.color("paper")[:3] + (250,))
        d.line([(cx - s * 0.8, cy), (cx + s * 0.8, cy)], fill=accent[:3] + (180,), width=1)
    elif kind == "inkstone":
        d.ellipse((cx - s * 0.9, cy - s * 0.4, cx + s * 0.9, cy + s * 0.4),
                  fill=mix(fg, ctx.color("shadow"), 0.4)[:3] + (250,))
        d.ellipse((cx - s * 0.5, cy - s * 0.22, cx + s * 0.5, cy + s * 0.18),
                  fill=mix(fg, ctx.color("paper"), 0.08)[:3] + (250,))
    elif kind == "seal":
        d.rectangle((cx - s * 0.38, cy - s * 0.38, cx + s * 0.38, cy + s * 0.38),
                    fill=accent[:3] + (245,))
    elif kind == "map":
        d.polygon([(cx - s * 1.1, cy + s * 0.35), (cx - s * 0.7, cy - s * 0.3),
                   (cx + s * 1.2, cy - s * 0.35), (cx + s * 0.8, cy + s * 0.4)],
                  fill=ctx.color("paper")[:3] + (250,))
        d.line([(cx - s * 0.6, cy + s * 0.05), (cx + s * 0.7, cy - s * 0.1)],
               fill=accent[:3] + (170,), width=1)
    else:  # 默认小卷/镇纸
        d.rounded_rectangle((cx - s * 0.9, cy - s * 0.18, cx + s * 0.9, cy + s * 0.18),
                            radius=int(s * 0.15), fill=mix(fg, accent, 0.2)[:3] + (240,))


def p_ladder(img: Image.Image, spec: dict, ctx: SceneCtx) -> None:
    """木梯（书架/档案库）：两根斜梁 + 横档。"""
    w, h = ctx.w, ctx.h
    box = ctx.box(spec, (0.04, 0.18, 0.12, 0.66))
    x0, y0, x1, y1 = box
    bw, bh = x1 - x0, y1 - y0
    if bw < 4 or bh < 20:
        return
    wood = ctx.spec_color(spec, "color", "wood")
    dark = mix(wood, ctx.color("fg"), 0.5)
    draw = ImageDraw.Draw(img)
    lean = bw * _f(spec, "lean", 0.55)
    left_top = (x0 + lean, y0)
    left_bot = (x0, y1)
    right_top = (x0 + bw, y0)
    right_bot = (x0 + bw - lean, y1)
    rail = max(2, int(bw * 0.09))
    for a, b in ((left_top, left_bot), (right_top, right_bot)):
        draw.line([a, b], fill=wood[:3] + (245,), width=rail)
        draw.line([(a[0] + rail * 0.4, a[1]), (b[0] + rail * 0.4, b[1])],
                  fill=dark[:3] + (120,), width=1)
    rungs = max(3, _i(spec, "rungs", 7))
    for k in range(1, rungs):
        t = k / float(rungs)
        p0 = (left_top[0] + (left_bot[0] - left_top[0]) * t,
              left_top[1] + (left_bot[1] - left_top[1]) * t)
        p1 = (right_top[0] + (right_bot[0] - right_top[0]) * t,
              right_top[1] + (right_bot[1] - right_top[1]) * t)
        draw.line([p0, p1], fill=mix(wood, dark, 0.3)[:3] + (245,),
                  width=max(2, int(rail * 0.65)))


def _draw_artifact(img: Image.Image, cx: float, cy: float, aw: float, ah: float,
                   kind: str, ctx: SceneCtx, spec: dict) -> None:
    """文物剪影（鼎/尊/玉璧/瓷瓶）：渐变高光 + 纹饰，供 display_case/museum 复用。"""
    w, h = img.size
    metal = ctx.spec_color(spec, "color", "metal")
    dark = mix(metal, ctx.color("fg"), 0.55)
    light = mix(metal, ctx.color("highlight"), 0.55)
    layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    # 形状蒙版（最后用它裁掉高光溢出；玉璧中间抠孔）
    art_a = Image.new("L", (w, h), 0)
    ad = ImageDraw.Draw(art_a)
    body_h = ah * 0.55
    body_box = (cx - aw * 0.5, cy - ah * 0.30, cx + aw * 0.5, cy - ah * 0.30 + body_h)
    if kind == "vase":
        # 梅瓶轮廓：顶部收口 → 丰肩 → 束腰 → 撇足（左右对称，顺序沿轮廓走一圈）
        prof = [(0.38, 0.00), (0.34, 0.05), (0.36, 0.12), (0.30, 0.22),
                (0.24, 0.38), (0.23, 0.55), (0.28, 0.72), (0.34, 0.84),
                (0.35, 0.92), (0.34, 1.00),
                (0.66, 1.00), (0.65, 0.92), (0.66, 0.84), (0.72, 0.72),
                (0.77, 0.55), (0.76, 0.38), (0.70, 0.22), (0.64, 0.12),
                (0.66, 0.05), (0.62, 0.00)]
        poly = [(cx - aw / 2 + aw * px, cy - ah / 2 + ah * py) for px, py in prof]
        d.polygon(poly, fill=metal[:3] + (250,))
        ad.polygon(poly, fill=255)
        # 颈/足环 + 釉面高光
        d.line([(cx - aw * 0.13, cy - ah * 0.44), (cx + aw * 0.13, cy - ah * 0.44)],
               fill=dark[:3] + (210,), width=max(1, int(aw * 0.02)))
        d.line([(cx - aw * 0.16, cy + ah * 0.44), (cx + aw * 0.16, cy + ah * 0.44)],
               fill=dark[:3] + (190,), width=max(1, int(aw * 0.018)))
        d.line([(cx - aw * 0.10, cy - ah * 0.36), (cx - aw * 0.20, cy + ah * 0.36)],
               fill=light[:3] + (120,), width=max(1, int(aw * 0.035)))
    elif kind == "jade":
        d.ellipse((cx - aw / 2, cy - ah / 2, cx + aw / 2, cy + ah / 2),
                  fill=metal[:3] + (245,))
        ad.ellipse((cx - aw / 2, cy - ah / 2, cx + aw / 2, cy + ah / 2), fill=255)
        hole = Image.new("L", (w, h), 0)
        ImageDraw.Draw(hole).ellipse(
            (cx - aw * 0.17, cy - ah * 0.17, cx + aw * 0.17, cy + ah * 0.17), fill=255)
        art_a = ImageChops.subtract(art_a, hole)
        # 内圈薄壁高光
        d.ellipse((cx - aw * 0.24, cy - ah * 0.24, cx + aw * 0.24, cy + ah * 0.24),
                  outline=dark[:3] + (200,), width=max(1, int(aw * 0.02)))
    else:  # ding（青铜鼎）
        d.rounded_rectangle(body_box, radius=int(aw * 0.10), fill=metal[:3] + (250,))
        ad.rounded_rectangle(body_box, radius=int(aw * 0.10), fill=255)
        for side in (-1, 1):
            ex = cx + side * aw * 0.36
            d.arc((ex - aw * 0.12, cy - ah * 0.46, ex + aw * 0.12, cy - ah * 0.22),
                  start=180, end=360, fill=metal[:3] + (250,), width=max(2, int(aw * 0.045)))
            ad.arc((ex - aw * 0.12, cy - ah * 0.46, ex + aw * 0.12, cy - ah * 0.22),
                   start=180, end=360, fill=255, width=max(2, int(aw * 0.045)))
        for side in (-1, 0, 1):
            lx = cx + side * aw * 0.30
            leg = [(lx - aw * 0.05, cy + ah * 0.24), (lx + aw * 0.05, cy + ah * 0.24),
                   (lx + aw * 0.035, cy + ah * 0.50), (lx - aw * 0.035, cy + ah * 0.50)]
            d.polygon(leg, fill=metal[:3] + (250,))
            ad.polygon(leg, fill=255)
        d.line([(cx - aw * 0.46, cy - ah * 0.16), (cx + aw * 0.46, cy - ah * 0.16)],
               fill=dark[:3] + (200,), width=max(1, int(ah * 0.012)))
        for k in range(5):
            mx = cx - aw * 0.32 + k * aw * 0.16
            d.rectangle((mx, cy - ah * 0.10, mx + aw * 0.09, cy - ah * 0.01),
                        outline=dark[:3] + (190,), width=1)
    # 金属渐变高光/暗部（先画后裁，保证只落在形状内）
    layer.alpha_composite(glow_layer((w, h), [(cx - aw * 0.18, cy - ah * 0.12,
                                               aw * 0.30, ah * 0.30, 0.55)],
                                     light, power=1.8, downscale=8))
    layer.alpha_composite(glow_layer((w, h), [(cx + aw * 0.30, cy + ah * 0.18,
                                               aw * 0.28, ah * 0.35, 0.55)],
                                     dark, power=1.8, downscale=8))
    keep = ImageChops.multiply(layer.getchannel("A"), art_a)
    layer.putalpha(keep)
    # 深色外轮廓（让文物从玻璃/大理石背景里“跳”出来）
    try:
        grow = art_a.filter(ImageFilter.MaxFilter(5))
        outline = ImageChops.subtract(grow, art_a)
        outline_layer = mask_layer(
            (w, h), outline, tuple(int(v) for v in dark[:3]) + (235,))
        img.alpha_composite(outline_layer)
    except (ValueError, MemoryError):  # pragma: no cover - 极小图跳过描边
        pass
    img.alpha_composite(layer)
    # 顶部棱线高光 + 底部接触阴影
    d2 = ImageDraw.Draw(img)
    if kind == "ding":
        d2.line([(body_box[0] + aw * 0.02, body_box[1] + ah * 0.03),
                 (body_box[2] - aw * 0.02, body_box[1] + ah * 0.03)],
                fill=light[:3] + (190,), width=max(1, int(ah * 0.014)))
    img.alpha_composite(glow_layer(
        (w, h), [(cx, cy + ah * 0.46, aw * 0.55, ah * 0.06, 0.35)],
        (0, 0, 0), power=1.6, downscale=10))


def p_artifact(img: Image.Image, spec: dict, ctx: SceneCtx) -> None:
    """文物特写（鼎/瓷瓶/玉璧）：金属渐变 + 纹饰 + 柔光。"""
    cx = ctx.X(_f(spec, "x", 0.5))
    cy = ctx.Y(_f(spec, "y", 0.56))
    aw = _len(ctx, spec, "w", 0.20)
    ah = _len(ctx, spec, "h", 0.34)
    kind = str(spec.get("kind") or "ding")
    if bool(spec.get("glow", True)):
        gc = ctx.color(spec.get("glow_color") or "glow", fallback="accent")
        img.alpha_composite(glow_layer(
            (ctx.w, ctx.h), [(cx, cy, aw * 1.1, ah * 0.9, 0.28)],
            gc, power=1.8, downscale=6))
    _draw_artifact(img, cx, cy, aw, ah, kind, ctx, spec)


def p_sand_table(img: Image.Image, spec: dict, ctx: SceneCtx) -> None:
    """沙盘（军议）：台面 + 起伏地形 + 方格 + 小旗标记。"""
    w, h = ctx.w, ctx.h
    box = ctx.box(spec, (0.18, 0.46, 0.64, 0.30))
    x0, y0, x1, y1 = box
    bw, bh = x1 - x0, y1 - y0
    if bw < 12 or bh < 10:
        return
    wood = ctx.color("wood")
    base = ctx.spec_color(spec, "color", "shadow")
    accent = ctx.color("accent")
    layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    inset = bw * 0.05
    top = [(x0 + inset, y0), (x1 - inset, y0), (x1, y1), (x0, y1)]
    d.polygon([(x0, y1), (x1, y1), (x1, y1 + bh * 0.10), (x0, y1 + bh * 0.10)],
              fill=mix(wood, ctx.color("fg"), 0.5)[:3] + (250,))
    d.polygon(top, fill=mix(base, ctx.color("bg"), 0.55)[:3] + (250,))
    # 地形起伏
    rng = ctx.rng_from(f"sand{ctx.seed}")
    for _ in range(_i(spec, "hills", 16)):
        hx = x0 + bw * rng.uniform(0.10, 0.90)
        hy = y0 + bh * rng.uniform(0.15, 0.85)
        rx = bw * rng.uniform(0.04, 0.12)
        ry = bh * rng.uniform(0.04, 0.10)
        d.ellipse((hx - rx, hy - ry, hx + rx, hy + ry),
                  fill=mix(base, ctx.color("highlight"), rng.uniform(0.05, 0.22))[:3] + (235,))
        d.arc((hx - rx, hy - ry, hx + rx, hy + ry), start=20, end=160,
              fill=mix(base, ctx.color("fg"), 0.35)[:3] + (150,), width=1)
    # 方格
    for k in range(1, 6):
        t = k / 6.0
        d.line([(x0 + inset + (x1 - x0 - 2 * inset) * t, y0),
                (x0 + (x1 - x0) * t, y1)], fill=ctx.color("fg")[:3] + (55,), width=1)
    # 小旗
    for _ in range(_i(spec, "flags", 5)):
        fx = x0 + bw * rng.uniform(0.15, 0.85)
        fy = y0 + bh * rng.uniform(0.25, 0.85)
        fh = bh * 0.22
        d.line([(fx, fy), (fx, fy - fh)], fill=accent[:3] + (240,),
               width=max(1, int(ctx.S(0.0016))))
        d.polygon([(fx, fy - fh), (fx + fh * 0.55, fy - fh * 0.78), (fx, fy - fh * 0.55)],
                  fill=accent[:3] + (245,))
    # 台面边框
    d.polygon(top, outline=mix(wood, ctx.color("fg"), 0.4)[:3] + (235,),
              width=max(1, int(ctx.S(0.002))))
    img.alpha_composite(layer)


def p_glass_case(img: Image.Image, spec: dict, ctx: SceneCtx) -> None:
    """展柜（博物馆）：基座 + 玻璃箱 + 反光 + 内部文物 + 铭牌。"""
    w, h = ctx.w, ctx.h
    box = ctx.box(spec, (0.28, 0.22, 0.44, 0.62))
    x0, y0, x1, y1 = box
    bw, bh = x1 - x0, y1 - y0
    if bw < 16 or bh < 16:
        return
    stone = ctx.color("stone")
    dark = mix(stone, ctx.color("fg"), 0.6)
    glass_c = ctx.color("highlight")
    accent = ctx.color("accent")
    layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    # 基座
    plinth_h = bh * 0.22
    d.polygon([(x0 + bw * 0.06, y1 - plinth_h), (x1 - bw * 0.06, y1 - plinth_h),
               (x1, y1), (x0, y1)], fill=mix(stone, dark, 0.35)[:3] + (250,))
    d.rectangle((x0 + bw * 0.06, y1 - plinth_h * 0.35, x1 - bw * 0.06, y1 - plinth_h * 0.22),
                fill=stone[:3] + (250,))
    # 玻璃箱
    gx0, gy0 = x0 + bw * 0.10, y0
    gx1, gy1 = x1 - bw * 0.10, y1 - plinth_h
    d.polygon([(gx0 + bw * 0.05, gy0), (gx1 - bw * 0.05, gy0), (gx1, gy1), (gx0, gy1)],
              fill=glass_c[:3] + (16,))
    # 玻璃边
    for a, b in (((gx0 + bw * 0.05, gy0), (gx1 - bw * 0.05, gy0)),
                 ((gx0, gy1), (gx1, gy1)),
                 ((gx0 + bw * 0.05, gy0), (gx0, gy1)),
                 ((gx1 - bw * 0.05, gy0), (gx1, gy1))):
        d.line([a, b], fill=glass_c[:3] + (125,), width=max(1, int(ctx.S(0.0016))))
    # 反光斜条
    for k, t in enumerate((0.18, 0.42)):
        sx = gx0 + (gx1 - gx0) * t
        d.polygon([(sx, gy0 + bh * 0.05), (sx + bw * 0.10, gy0 + bh * 0.02),
                   (sx + bw * 0.20, gy1 - bh * 0.05), (sx + bw * 0.10, gy1)],
                  fill=glass_c[:3] + (22,))
    # 文物
    art = spec.get("artifact") if isinstance(spec.get("artifact"), dict) else {}
    kind = str(art.get("kind") or spec.get("kind") or "ding")
    aw = bw * float(art.get("w", 0.55))
    ah = bh * float(art.get("h", 0.42))
    _draw_artifact(layer, (gx0 + gx1) / 2, (gy0 + gy1) / 2 + bh * 0.03,
                   aw, ah, kind, ctx, {**spec, "color": art.get("color") or spec.get("color")})
    # 顶部聚光
    layer.alpha_composite(glow_layer(
        (w, h), [((gx0 + gx1) / 2, gy0 + bh * 0.05, bw * 0.55, bh * 0.25, 0.30)],
        glass_c, power=2.0, downscale=6))
    # 铭牌
    px0 = gx0 + bw * 0.22
    py0 = y1 - plinth_h * 0.90
    d.rounded_rectangle((px0, py0, px0 + bw * 0.56, py0 + plinth_h * 0.24),
                        radius=max(1, int(plinth_h * 0.06)), fill=accent[:3] + (225,))
    label = str(spec.get("label") or "")
    if label:
        draw_text(d, (px0 + bw * 0.28, py0 + plinth_h * 0.12), label,
                  max(8, int(plinth_h * 0.15)), dark[:3] + (245,))
    img.alpha_composite(layer)


# ===========================================================================
# 16. 内置 primitive — 建筑（柱/拱/屏风/宝座/帐）
# ===========================================================================
def p_columns(img: Image.Image, spec: dict, ctx: SceneCtx) -> None:
    """立柱（宫殿/庙堂/博物馆）：柱础 + 柱身 + 柱头 + 明暗；可选楹联/金环。"""
    w, h = ctx.w, ctx.h
    count = max(1, min(12, _i(spec, "count", 4)))
    x0 = _f(spec, "x0", 0.06)
    x1 = _f(spec, "x1", 0.94)
    y_top = _f(spec, "y", 0.04)
    y_bot = _f(spec, "y_bottom", 0.88)
    col_w = _len(ctx, spec, "width", 0.075)
    color = ctx.spec_color(spec, "color", "accent")
    dark = mix(color, ctx.color("fg"), 0.55)
    light = mix(color, ctx.color("highlight"), 0.35)
    draw = ImageDraw.Draw(img)
    warm = ctx.color("glow", fallback="highlight")
    for i in range(count):
        cx_norm = x0 + (x1 - x0) * (i / max(1, count - 1)) if count > 1 else (x0 + x1) / 2
        cx = ctx.X(cx_norm)
        half_top = col_w * 0.45
        half_bot = col_w * 0.52
        top_y = ctx.Y(y_top)
        bot_y = ctx.Y(y_bot)
        shaft = [(cx - half_top, top_y), (cx + half_top, top_y),
                 (cx + half_bot, bot_y), (cx - half_bot, bot_y)]
        # 柱阴影（右下方）
        draw.polygon([(cx + half_top * 0.15, top_y), (cx + half_top, top_y),
                      (cx + half_bot, bot_y), (cx + half_bot * 1.25, bot_y)],
                     fill=(0, 0, 0, 46))
        draw.polygon(shaft, fill=color[:3] + (250,))
        # 明暗
        draw.polygon([(cx - half_top, top_y), (cx - half_top * 0.25, top_y),
                      (cx - half_bot * 0.25, bot_y), (cx - half_bot, bot_y)],
                     fill=light[:3] + (110,))
        draw.polygon([(cx + half_top * 0.35, top_y), (cx + half_top, top_y),
                      (cx + half_bot, bot_y), (cx + half_bot * 0.35, bot_y)],
                     fill=dark[:3] + (120,))
        # 柱头/柱础
        for yy, hh, ww in ((top_y, 0.055, 1.35), (bot_y - ctx.S(0.045), 0.06, 1.42)):
            by0 = yy - (ctx.S(hh) if yy == bot_y - ctx.S(0.045) else 0)
            draw.rectangle((cx - col_w * ww * 0.5, by0,
                            cx + col_w * ww * 0.5, by0 + ctx.S(hh)),
                           fill=mix(color, ctx.color("stone"), 0.35)[:3] + (252,))
        if ctx.gold:
            gc = ctx.color("accent")
            draw.rectangle((cx - half_top * 1.05, top_y + ctx.S(0.012),
                            cx + half_top * 1.05, top_y + ctx.S(0.024)),
                           fill=gc[:3] + (210,))
            draw.rectangle((cx - half_bot * 1.1, bot_y - ctx.S(0.035),
                            cx + half_bot * 1.1, bot_y - ctx.S(0.022)),
                           fill=gc[:3] + (200,))
        # 柱身凹槽
        for k in (-0.45, 0.0, 0.45):
            draw.line([(cx + col_w * k * 0.55, top_y + ctx.S(0.02)),
                       (cx + col_w * k * 0.60, bot_y - ctx.S(0.02))],
                      fill=dark[:3] + (70,), width=1)
        # 楹联（水墨/版刻宫殿）
        if bool(spec.get("couplets", False)):
            cw = col_w * 0.32
            strip = [(cx - cw, top_y + ctx.S(0.10)), (cx + cw, top_y + ctx.S(0.10)),
                     (cx + cw, bot_y - ctx.S(0.12)), (cx - cw, bot_y - ctx.S(0.12))]
            draw.polygon(strip, fill=ctx.color("paper")[:3] + (235,))
            fs = max(8, int(col_w * 0.26))
            rng = ctx.rng_from(f"couplet{i}{ctx.seed}")
            for k in range(int((bot_y - top_y) / (fs * 1.25))):
                _fake_text_column(draw, cx, top_y + ctx.S(0.11) + k * fs * 1.25,
                                  top_y + ctx.S(0.11) + (k + 1) * fs * 1.25,
                                  fs, ctx.color("fg")[:3] + (200,), rng)
        # 地面光影
        img.alpha_composite(glow_layer(
            (w, h), [(cx, bot_y + ctx.S(0.01), col_w * 0.9, ctx.S(0.02), 0.30)],
            warm if ctx.gold else dark, power=1.6, downscale=8))


def p_arches(img: Image.Image, spec: dict, ctx: SceneCtx) -> None:
    """拱门/券门（宫殿/庙堂/展廊）：连拱 + 暗部开口 + 梁枋。"""
    w, h = ctx.w, ctx.h
    count = max(1, min(8, _i(spec, "count", 3)))
    x0 = _f(spec, "x0", 0.06)
    x1 = _f(spec, "x1", 0.94)
    y_spring = _f(spec, "y", 0.30)
    arch_h = _f(spec, "height", 0.34)
    color = ctx.spec_color(spec, "color", "stone")
    dark = mix(color, ctx.color("fg"), 0.55)
    open_c = mix(ctx.color("shadow"), ctx.color("bg"), 0.55)
    draw = ImageDraw.Draw(img)
    spring_y = ctx.Y(y_spring)
    bottom_y = ctx.Y(min(0.98, y_spring + arch_h + 0.36))
    span = (ctx.X(x1) - ctx.X(x0)) / float(count)
    pointed = str(spec.get("style") or "").lower() in ("pointed", "gothic")
    for i in range(count):
        cx = ctx.X(x0) + span * (i + 0.5)
        half = span * 0.34
        # 开口
        d0 = (cx - half, spring_y - half * 0.95, cx + half, spring_y + half * 0.95)
        draw.rectangle((cx - half, spring_y, cx + half, bottom_y),
                       fill=open_c[:3] + (235,))
        if pointed:
            draw.polygon([(cx - half, spring_y), (cx, spring_y - half * 1.55),
                          (cx + half, spring_y)], fill=open_c[:3] + (235,))
            draw.arc((cx - half * 2, spring_y - half * 1.55,
                      cx, spring_y + half * 0.45), start=270, end=360,
                     fill=color[:3] + (235,), width=max(2, int(ctx.S(0.004))))
            draw.arc((cx, spring_y - half * 1.55,
                      cx + half * 2, spring_y + half * 0.45), start=180, end=270,
                     fill=color[:3] + (235,), width=max(2, int(ctx.S(0.004))))
        else:
            draw.pieslice((cx - half, spring_y - half, cx + half, spring_y + half),
                          start=180, end=360, fill=open_c[:3] + (235,))
            draw.arc(d0, start=180, end=360, fill=color[:3] + (240,),
                     width=max(2, int(ctx.S(0.005))))
        # 门柱
        post_w = max(2.0, span * 0.05)
        for side in (-1, 1):
            px = cx + side * (half + post_w * 0.4)
            draw.rectangle((px - post_w / 2, spring_y - half * 0.2,
                            px + post_w / 2, bottom_y),
                           fill=color[:3] + (250,))
            draw.line([(px + post_w * 0.2, spring_y), (px + post_w * 0.2, bottom_y)],
                      fill=dark[:3] + (110,), width=1)
    # 梁枋
    beam_h = ctx.S(0.045)
    draw.rectangle((ctx.X(x0), spring_y - beam_h, ctx.X(x1), spring_y),
                   fill=color[:3] + (252,))
    draw.line([(ctx.X(x0), spring_y), (ctx.X(x1), spring_y)],
              fill=dark[:3] + (200,), width=max(1, int(ctx.S(0.0022))))
    if ctx.gold:
        draw.line([(ctx.X(x0), spring_y - beam_h * 0.55),
                   (ctx.X(x1), spring_y - beam_h * 0.55)],
                  fill=ctx.color("accent")[:3] + (190,),
                  width=max(1, int(ctx.S(0.0018))))


def p_screens(img: Image.Image, spec: dict, ctx: SceneCtx) -> None:
    """屏风/窗棂/障子：框架 + 分隔 + 菱格/横竖棂 + 可选山水画心。"""
    w, h = ctx.w, ctx.h
    box = ctx.box(spec, (0.10, 0.16, 0.80, 0.46))
    x0, y0, x1, y1 = box
    bw, bh = x1 - x0, y1 - y0
    if bw < 10 or bh < 10:
        return
    frame = ctx.spec_color(spec, "color", "wood")
    dark = mix(frame, ctx.color("fg"), 0.55)
    paper_c = ctx.color("paper")
    panels = max(1, min(8, _i(spec, "panels", 3)))
    style = str(spec.get("style") or "lattice")
    layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    # 底
    d.rectangle((x0, y0, x1, y1), fill=paper_c[:3] + (232,))
    pw = bw / float(panels)
    for p in range(panels):
        px0 = x0 + p * pw
        px1 = px0 + pw
        if style == "painted" or (p % 2 == 1 and "painted" in style):
            # 画心：淡墨山水
            mid_y = (y0 + y1) / 2
            for k in range(3):
                rng = ctx.rng_from(f"screen{p}{k}{ctx.seed}")
                pts = [(px0 + pw * 0.12, mid_y + bh * (0.1 - k * 0.08))]
                for s in range(1, 5):
                    pts.append((px0 + pw * (0.12 + 0.19 * s),
                                mid_y + bh * (0.10 - k * 0.08)
                                + (rng.uniform(-0.05, 0.05) * bh)))
                brush_line(d, chaikin(pts, 2), ctx.color("ink")[:3] + (120 - k * 18,),
                           max(1, int(ctx.S(0.0022))))
            d.ellipse((px0 + pw * 0.58, y0 + bh * 0.14, px0 + pw * 0.70,
                       y0 + bh * 0.26), outline=ctx.color("accent")[:3] + (150,), width=1)
        else:
            # 棂格
            cols = 3
            rows = max(2, int(bh / pw * 1.6))
            for c in range(1, cols):
                lx = px0 + pw * c / cols
                d.line([(lx, y0), (lx, y1)], fill=dark[:3] + (85,), width=1)
            for r in range(1, rows):
                ly = y0 + bh * r / rows
                d.line([(px0, ly), (px1, ly)], fill=dark[:3] + (85,), width=1)
            if style == "diamond":
                for r in range(rows - 1):
                    for c in range(cols - 1):
                        ax, ay = px0 + pw * c / cols, y0 + bh * r / rows
                        bx, by = px0 + pw * (c + 1) / cols, y0 + bh * (r + 1) / rows
                        d.line([(ax, ay), (bx, by)], fill=dark[:3] + (45,), width=1)
                        d.line([(bx, ay), (ax, by)], fill=dark[:3] + (45,), width=1)
        # 分隔框
        d.rectangle((px0, y0, px1, y1), outline=dark[:3] + (180,),
                    width=max(1, int(ctx.S(0.0022))))
    # 外框
    fw = max(2, int(bw * 0.018))
    for bx in (x0, x1 - fw):
        d.rectangle((bx, y0, bx + fw, y1), fill=frame[:3] + (252,))
    d.rectangle((x0, y0, x1, y0 + fw), fill=frame[:3] + (252,))
    d.rectangle((x0, y1 - fw, x1, y1), fill=frame[:3] + (252,))
    d.rectangle((x0, y0, x1, y1), outline=dark[:3] + (210,), width=1)
    img.alpha_composite(layer)


def p_throne(img: Image.Image, spec: dict, ctx: SceneCtx) -> None:
    """宝座/御座（宫殿）：台阶 + 台基 + 高背椅 + 金色装饰。"""
    w, h = ctx.w, ctx.h
    box = ctx.box(spec, (0.38, 0.34, 0.24, 0.50))
    x0, y0, x1, y1 = box
    bw, bh = x1 - x0, y1 - y0
    if bw < 10 or bh < 10:
        return
    gold_c = ctx.spec_color(spec, "color", "accent")
    dark = mix(gold_c, ctx.color("fg"), 0.65)
    wood = ctx.color("wood")
    layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    # 台阶（3 级）
    for k in range(3):
        t = k / 3.0
        sx0 = x0 - bw * (0.25 - t * 0.08)
        sx1 = x1 + bw * (0.25 - t * 0.08)
        sy0 = y1 - bh * (0.20 - t * 0.055)
        d.polygon([(sx0, sy0), (sx1, sy0), (sx1, sy0 + bh * 0.05),
                   (sx0, sy0 + bh * 0.05)],
                  fill=mix(wood, ctx.color("stone"), 0.45)[:3] + (250,))
        d.line([(sx0, sy0 + bh * 0.05), (sx1, sy0 + bh * 0.05)],
               fill=dark[:3] + (140,), width=1)
    # 台基
    base_y = y1 - bh * 0.16
    d.rectangle((x0 - bw * 0.10, base_y, x1 + bw * 0.10, y1),
                fill=mix(wood, ctx.color("stone"), 0.30)[:3] + (252,))
    d.rectangle((x0 - bw * 0.10, base_y, x1 + bw * 0.10, base_y + bh * 0.02),
                fill=gold_c[:3] + (190,))
    # 高背椅
    seat_y = y0 + bh * 0.62
    back_top = y0 + bh * 0.02
    d.polygon([(x0 + bw * 0.10, back_top), (x1 - bw * 0.10, back_top),
               (x1 - bw * 0.06, seat_y), (x0 + bw * 0.06, seat_y)],
              fill=mix(gold_c, dark, 0.35)[:3] + (252,))
    d.pieslice((x0 + bw * 0.10, back_top - bw * 0.02, x1 - bw * 0.10, back_top + bw * 0.30),
               start=180, end=360, fill=mix(gold_c, ctx.color("highlight"), 0.30)[:3] + (252,))
    # 靠背纹样
    d.ellipse((x0 + bw * 0.30, back_top + bh * 0.10, x1 - bw * 0.30, back_top + bh * 0.30),
              outline=ctx.color("highlight")[:3] + (170,), width=max(1, int(bw * 0.02)))
    # 座面
    d.rectangle((x0 + bw * 0.02, seat_y, x1 - bw * 0.02, seat_y + bh * 0.10),
                fill=gold_c[:3] + (250,))
    d.line([(x0 + bw * 0.02, seat_y + bh * 0.10), (x1 - bw * 0.02, seat_y + bh * 0.10)],
           fill=dark[:3] + (190,), width=max(1, int(bh * 0.012)))
    # 扶手
    for side in (-1, 1):
        ax = x0 + bw / 2 + side * bw * 0.44
        d.rectangle((ax - bw * 0.03, seat_y - bh * 0.02, ax + bw * 0.03, seat_y),
                    fill=gold_c[:3] + (250,))
        d.ellipse((ax - bw * 0.05, seat_y - bh * 0.05, ax + bw * 0.05, seat_y + bh * 0.01),
                  fill=mix(gold_c, ctx.color("highlight"), 0.35)[:3] + (250,))
    img.alpha_composite(layer)


def p_tents(img: Image.Image, spec: dict, ctx: SceneCtx) -> None:
    """军帐/营帐：三角/脊式帐篷 + 帐门 + 旗杆 + 拉绳（远中近三排）。"""
    w, h = ctx.w, ctx.h
    rows = max(1, min(3, _i(spec, "rows", 2)))
    color = ctx.spec_color(spec, "color", "paper")
    dark = mix(color, ctx.color("shadow"), 0.45)
    accent = ctx.color("accent")
    rng = ctx.rng_from(f"tents{ctx.seed}{_i(spec, 'seed', 0)}")
    draw = ImageDraw.Draw(img)
    for r in range(rows):
        t = r / max(1, rows - 1) if rows > 1 else 1.0
        base_y = ctx.Y(_f(spec, "y", 0.56) + t * 0.16)
        scale = ctx.S((0.10 + 0.05 * t) * _f(spec, "size", 1.0))
        count = _i(spec, "count", 3) + (r % 2)
        for k in range(count):
            cx = ctx.X(0.08 + (k + 0.5) * (0.84 / count) + rng.uniform(-0.02, 0.02))
            c = mix(color, ctx.color("bg"), 0.25 - 0.15 * t)
            half = scale * rng.uniform(0.85, 1.15)
            tent_h = scale * rng.uniform(1.0, 1.25)
            # 帐身
            draw.polygon([(cx - half, base_y), (cx - half * 0.15, base_y - tent_h),
                          (cx + half * 0.15, base_y - tent_h), (cx + half, base_y)],
                         fill=c[:3] + (252,))
            # 门
            door_w = half * 0.34
            draw.polygon([(cx - door_w, base_y), (cx, base_y - tent_h * 0.72),
                          (cx + door_w, base_y)], fill=dark[:3] + (235,))
            # 帐脊阴影
            draw.line([(cx, base_y - tent_h), (cx, base_y - tent_h * 0.70)],
                      fill=dark[:3] + (120,), width=max(1, int(ctx.S(0.002))))
            # 拉绳
            draw.line([(cx - half * 0.15, base_y - tent_h), (cx - half * 1.35, base_y)],
                      fill=dark[:3] + (120,), width=1)
            draw.line([(cx + half * 0.15, base_y - tent_h), (cx + half * 1.35, base_y)],
                      fill=dark[:3] + (120,), width=1)
            # 旗
            if rng.random() < 0.7:
                draw.line([(cx, base_y - tent_h), (cx, base_y - tent_h * 1.5)],
                          fill=dark[:3] + (220,), width=max(1, int(ctx.S(0.0018))))
                draw.polygon([(cx, base_y - tent_h * 1.5),
                              (cx + half * 0.42, base_y - tent_h * 1.38),
                              (cx, base_y - tent_h * 1.26)],
                             fill=accent[:3] + (235,))
    img.alpha_composite(glow_layer(
        (w, h), [(w * 0.5, ctx.Y(0.62), w * 0.5, h * 0.18, 0.16)],
        ctx.color("highlight"), power=1.5, downscale=8))


# ===========================================================================
# 17. 内置 primitive — 灯/幡/火/香/光（氛围）
# ===========================================================================
def p_lanterns(img: Image.Image, spec: dict, ctx: SceneCtx) -> None:
    """灯笼串（市井/宫殿/庙堂）：悬链线 + 灯体 + 灯骨 + 流苏 + 暖光。"""
    w, h = ctx.w, ctx.h
    count = max(1, min(20, _i(spec, "count", 6)))
    y = _f(spec, "y", 0.16)
    x0 = _f(spec, "x0", 0.04)
    x1 = _f(spec, "x1", 0.96)
    size = _len(ctx, spec, "size", 0.038)
    sag = _f(spec, "sag", 0.05)
    color = ctx.spec_color(spec, "color", "accent")
    glow_c = ctx.color("glow", fallback="highlight")
    draw = ImageDraw.Draw(img)
    # 悬链线
    wire = []
    for k in range(25):
        t = k / 24.0
        wx = ctx.X(x0 + (x1 - x0) * t)
        wy = ctx.Y(y + math.sin(math.pi * t) * sag)
        wire.append((wx, wy))
    brush_line(draw, wire, ctx.color("fg")[:3] + (170,), max(1, int(ctx.S(0.0016))))
    for i in range(count):
        t = (i + 0.5) / count
        wx, wy = point_at(wire, t)
        hang = ctx.S(_f(spec, "hang", 0.035)) * (0.7 + 0.6 * math.sin(t * 9.1) * 0.5 + 0.5)
        cy = wy + hang + size
        draw.line([(wx, wy), (wx, cy - size * 1.15)], fill=ctx.color("fg")[:3] + (180,), width=1)
        # 光晕
        img.alpha_composite(glow_layer(
            (w, h), [(wx, cy, size * 3.2, size * 3.2, 0.55)], glow_c,
            power=2.0, downscale=6))
        # 灯体
        body = (wx - size * 0.72, cy - size * 0.85, wx + size * 0.72, cy + size * 0.85)
        draw.ellipse(body, fill=color[:3] + (252,))
        draw.ellipse((body[0] + size * 0.18, body[1] + size * 0.12,
                      body[2] - size * 0.18, body[3] - size * 0.12),
                     fill=mix(color, glow_c, 0.45)[:3] + (240,))
        # 灯骨
        for k in (-0.5, 0.0, 0.5):
            draw.arc(body, start=250, end=290, fill=mix(color, ctx.color("fg"), 0.35)[:3] + (150,),
                     width=1)
            x_off = size * 0.55 * k
            draw.line([(wx + x_off, cy - size * 0.78), (wx + x_off, cy + size * 0.78)],
                      fill=mix(color, ctx.color("fg"), 0.3)[:3] + (80,), width=1)
        # 上下盖
        draw.rectangle((wx - size * 0.28, cy - size * 1.05, wx + size * 0.28, cy - size * 0.82),
                       fill=ctx.color("wood")[:3] + (250,))
        draw.rectangle((wx - size * 0.28, cy + size * 0.82, wx + size * 0.28, cy + size * 0.98),
                       fill=ctx.color("wood")[:3] + (250,))
        # 流苏
        draw.line([(wx, cy + size * 0.98), (wx, cy + size * 1.42)],
                  fill=color[:3] + (220,), width=max(1, int(ctx.S(0.0018))))
        for k in (-1, 0, 1):
            draw.line([(wx, cy + size * 1.15), (wx + size * 0.16 * k, cy + size * 1.5)],
                      fill=color[:3] + (170,), width=1)


def p_banners(img: Image.Image, spec: dict, ctx: SceneCtx) -> None:
    """旗帜/幡：旗杆 + 布料（燕尾/长幡）+ 纹样 + 飘动边 + 阴影。"""
    w, h = ctx.w, ctx.h
    count = max(1, min(12, _i(spec, "count", 3)))
    x0 = _f(spec, "x0", 0.10)
    x1 = _f(spec, "x1", 0.90)
    y_top = _f(spec, "y", 0.10)
    bw = _len(ctx, spec, "width", 0.055)
    bh = _len(ctx, spec, "height", 0.22)
    color = ctx.spec_color(spec, "color", "accent")
    dark = mix(color, ctx.color("fg"), 0.5)
    pole_c = ctx.color("wood")
    kind = str(spec.get("kind") or "war")
    rng = ctx.rng_from(f"banner{ctx.seed}{_i(spec, 'seed', 0)}")
    draw = ImageDraw.Draw(img)
    for i in range(count):
        t = (i + 0.5) / count if count > 1 else 0.5
        cx = ctx.X(x0 + (x1 - x0) * t)
        top = ctx.Y(y_top + rng.uniform(-0.02, 0.02))
        cloth_h = bh * rng.uniform(0.85, 1.15)
        if kind == "temple":
            cloth_h = bh * rng.uniform(1.5, 1.9)
            bw_i = bw * 0.65
        else:
            bw_i = bw
        # 旗杆
        draw.line([(cx, top - ctx.S(0.02)), (cx, top + cloth_h + ctx.S(0.03))],
                  fill=pole_c[:3] + (240,), width=max(2, int(ctx.S(0.003))))
        draw.ellipse((cx - bw_i * 0.10, top - ctx.S(0.028), cx + bw_i * 0.10,
                      top - ctx.S(0.010)), fill=ctx.color("accent")[:3] + (240,))
        # 布料（右边自由，底边燕尾）
        left = cx + bw_i * 0.06
        right = cx + bw_i * 0.94
        wave = math.sin(i * 1.7) * bw_i * 0.06
        if kind == "temple":
            poly = [(left, top), (right + wave, top + cloth_h * 0.02),
                    (right - bw_i * 0.12 + wave, top + cloth_h * 0.82),
                    ((left + right) / 2 + wave, top + cloth_h),
                    (left + bw_i * 0.12, top + cloth_h * 0.82)]
        else:
            poly = [(left, top), (right + wave, top + cloth_h * 0.04),
                    (right - bw_i * 0.10 + wave, top + cloth_h * 0.55),
                    (right + bw_i * 0.18 + wave, top + cloth_h * 0.72),
                    ((left + right) / 2, top + cloth_h),
                    (left + bw_i * 0.10, top + cloth_h * 0.55)]
        draw.polygon(poly, fill=color[:3] + (250,))
        # 布料阴影/褶皱
        draw.line([(left + bw_i * 0.25, top + cloth_h * 0.05),
                   (left + bw_i * 0.22, top + cloth_h * 0.9)],
                  fill=dark[:3] + (90,), width=1)
        draw.line([(left + bw_i * 0.62, top + cloth_h * 0.05),
                   (left + bw_i * 0.58, top + cloth_h * 0.92)],
                  fill=dark[:3] + (80,), width=1)
        # 纹样（圆徽 + 字/线）
        emblem_c = mix(color, ctx.color("paper"), 0.65)
        ecx, ecy = (left + right) / 2, top + cloth_h * 0.36
        er = bw_i * 0.22
        draw.ellipse((ecx - er, ecy - er, ecx + er, ecy + er),
                     outline=emblem_c[:3] + (220,), width=max(1, int(er * 0.18)))
        label = str(spec.get("text") or "")
        if label:
            draw_text(draw, (ecx, ecy), label[i % len(label)], max(8, int(er * 1.5)),
                      emblem_c[:3] + (235,), display=True)
        # 旗边
        draw.polygon(poly, outline=dark[:3] + (190,), width=max(1, int(ctx.S(0.0014))))


def p_torches(img: Image.Image, spec: dict, ctx: SceneCtx) -> None:
    """火把（军帐/宫殿外墙）：托架 + 火焰 + 强光晕 + 火星。"""
    w, h = ctx.w, ctx.h
    count = max(1, min(10, _i(spec, "count", 3)))
    y = _f(spec, "y", 0.42)
    x0 = _f(spec, "x0", 0.10)
    x1 = _f(spec, "x1", 0.90)
    size = _len(ctx, spec, "size", 0.042)
    flame_c = ctx.color(spec.get("color") or "accent", fallback="accent")
    rng = ctx.rng_from(f"torch{ctx.seed}")
    draw = ImageDraw.Draw(img)
    for i in range(count):
        t = (i + 0.5) / count if count > 1 else 0.5
        cx = ctx.X(x0 + (x1 - x0) * t)
        cy = ctx.Y(y + rng.uniform(-0.03, 0.03))
        # 托架
        draw.line([(cx - size * 1.4, cy + size * 0.6), (cx, cy + size * 0.15)],
                  fill=ctx.color("wood")[:3] + (235,), width=max(2, int(size * 0.16)))
        draw.line([(cx, cy + size * 0.15), (cx, cy - size * 0.15)],
                  fill=ctx.color("wood")[:3] + (235,), width=max(2, int(size * 0.20)))
        draw.rounded_rectangle((cx - size * 0.26, cy - size * 0.35,
                                cx + size * 0.26, cy + size * 0.05),
                               radius=int(size * 0.1),
                               fill=mix(ctx.color("wood"), ctx.color("fg"), 0.4)[:3] + (245,))
        # 光晕
        img.alpha_composite(glow_layer(
            (w, h), [(cx, cy - size * 0.9, size * 5.2, size * 5.2, 0.78)],
            flame_c, power=2.0, downscale=6))
        # 火焰（三层）
        for k, (fw, fh, col) in enumerate((
                (0.72, 1.60, flame_c), (0.50, 1.25, mix(flame_c, ctx.color("highlight"), 0.5)),
                (0.26, 0.80, mix(flame_c, ctx.color("highlight"), 0.85)))):
            fy = cy - size * (0.30 + fh * 0.35)
            ex = cx + rng.uniform(-1, 1) * size * 0.05
            draw.polygon([(ex - size * fw * 0.5, fy + size * fh * 0.45),
                          (ex - size * fw * 0.16, fy - size * fh * 0.35),
                          (ex + size * fw * 0.10, fy - size * fh * 0.60),
                          (ex + size * fw * 0.36, fy - size * fh * 0.10),
                          (ex + size * fw * 0.5, fy + size * fh * 0.45)],
                         fill=col[:3] + (235,))
        # 火星
        for _ in range(5):
            sx = cx + rng.uniform(-size * 1.2, size * 1.2)
            sy = cy - size * rng.uniform(1.4, 3.2)
            r = max(0.8, size * rng.uniform(0.02, 0.05))
            draw.ellipse((sx - r, sy - r, sx + r, sy + r),
                         fill=mix(flame_c, ctx.color("highlight"), 0.7)[:3] + (rng.randint(80, 180),))


def p_candle_glow(img: Image.Image, spec: dict, ctx: SceneCtx) -> None:
    """烛光/灯烛：蜡烛 + 火苗 + 半径暖光 + 桌面光池（室内历史场景）。"""
    w, h = ctx.w, ctx.h
    count = max(1, min(12, _i(spec, "count", 2)))
    y = _f(spec, "y", 0.62)
    x0 = _f(spec, "x0", 0.15)
    x1 = _f(spec, "x1", 0.85)
    size = _len(ctx, spec, "size", 0.05)
    warm = ctx.color(spec.get("color") or "glow", fallback="highlight")
    draw = ImageDraw.Draw(img)
    for i in range(count):
        t = (i + 0.5) / count if count > 1 else 0.5
        cx = ctx.X(x0 + (x1 - x0) * t)
        cy = ctx.Y(y)
        # 光池 + 光晕
        img.alpha_composite(glow_layer(
            (w, h), [(cx, cy + size * 0.5, size * 5.0, size * 1.6, 0.30)],
            warm, power=1.6, downscale=8))
        img.alpha_composite(glow_layer(
            (w, h), [(cx, cy - size * 0.5, size * 2.8, size * 2.8, 0.55)],
            warm, power=2.0, downscale=6))
        # 蜡烛
        draw.rectangle((cx - size * 0.10, cy - size * 0.15, cx + size * 0.10, cy + size * 0.75),
                       fill=mix(ctx.color("paper"), warm, 0.35)[:3] + (252,))
        draw.ellipse((cx - size * 0.10, cy + size * 0.66, cx + size * 0.10, cy + size * 0.82),
                     fill=mix(ctx.color("paper"), ctx.color("shadow"), 0.3)[:3] + (250,))
        # 火苗
        draw.polygon([(cx - size * 0.09, cy - size * 0.16),
                      (cx, cy - size * 0.55),
                      (cx + size * 0.09, cy - size * 0.16)],
                     fill=mix(warm, ctx.color("highlight"), 0.75)[:3] + (250,))
        draw.polygon([(cx - size * 0.045, cy - size * 0.18),
                      (cx, cy - size * 0.40),
                      (cx + size * 0.045, cy - size * 0.18)],
                     fill=ctx.color("highlight")[:3] + (245,))


def p_incense(img: Image.Image, spec: dict, ctx: SceneCtx) -> None:
    """香炉/铜炉（庙堂）：炉身 + 三足 + 盖 + 香烟缭绕 + 炉口微光。"""
    w, h = ctx.w, ctx.h
    cx = ctx.X(_f(spec, "x", 0.5))
    cy = ctx.Y(_f(spec, "y", 0.66))
    size = _len(ctx, spec, "size", 0.11)
    metal = ctx.spec_color(spec, "color", "metal")
    dark = mix(metal, ctx.color("fg"), 0.55)
    light = mix(metal, ctx.color("highlight"), 0.5)
    draw = ImageDraw.Draw(img)
    # 炉身
    draw.ellipse((cx - size, cy - size * 0.55, cx + size, cy + size * 0.55),
                 fill=metal[:3] + (252,))
    draw.ellipse((cx - size * 0.72, cy - size * 0.42, cx + size * 0.72, cy + size * 0.30),
                 fill=mix(metal, dark, 0.35)[:3] + (230,))
    # 炉口
    draw.ellipse((cx - size * 0.92, cy - size * 0.64, cx + size * 0.92, cy - size * 0.30),
                 fill=mix(metal, ctx.color("highlight"), 0.25)[:3] + (252,))
    # 三足
    for side in (-0.5, 0.0, 0.5):
        lx = cx + size * side
        draw.polygon([(lx - size * 0.10, cy + size * 0.42), (lx + size * 0.10, cy + size * 0.42),
                      (lx + size * 0.06, cy + size * 0.82), (lx - size * 0.06, cy + size * 0.82)],
                     fill=dark[:3] + (250,))
    # 双耳
    for side in (-1, 1):
        ex = cx + side * size * 0.92
        draw.arc((ex - size * 0.22, cy - size * 0.80, ex + size * 0.22, cy - size * 0.30),
                 start=90, end=270, fill=metal[:3] + (245,), width=max(2, int(size * 0.10)))
    # 高光
    img.alpha_composite(glow_layer(
        (w, h), [(cx - size * 0.35, cy - size * 0.30, size * 0.5, size * 0.3, 0.5)],
        light, power=1.8, downscale=8))
    # 香烟（上升曲线，多条）
    rng = ctx.rng_from(f"incense{ctx.seed}")
    smoke_c = mix(ctx.color("highlight"), ctx.color("bg"), 0.25)
    for k in range(3):
        pts = [(cx + rng.uniform(-size * 0.2, size * 0.2), cy - size * 0.72)]
        for s in range(1, 8):
            pts.append((cx + math.sin(s * 0.9 + k * 2.1) * size * 0.30
                        + rng.uniform(-size * 0.06, size * 0.06),
                        cy - size * (0.72 + s * 0.42)))
        pts = chaikin(pts, 3)
        for j in range(len(pts) - 1):
            a = int(26 + 34 * (j / max(1, len(pts) - 2)))
            brush_line(draw, pts[j:j + 2], smoke_c[:3] + (a,),
                       max(1, int(size * 0.05)))
    # 香火微光
    img.alpha_composite(glow_layer(
        (w, h), [(cx, cy - size * 0.62, size * 1.2, size * 0.9, 0.35)],
        ctx.color("accent"), power=2.2, downscale=8))


def p_spotlights(img: Image.Image, spec: dict, ctx: SceneCtx) -> None:
    """聚光灯（博物馆/舞台）：锥形光柱 + 地面光池 + 光尘。"""
    w, h = ctx.w, ctx.h
    count = max(1, min(8, _i(spec, "count", 3)))
    color = ctx.spec_color(spec, "color", "highlight")
    strength = max(0.0, min(1.5, _f(spec, "strength", 0.55)))
    y_src = _f(spec, "y_source", -0.06)
    y_tgt = _f(spec, "y_target", 0.92)
    spread = _f(spec, "spread", 0.16)
    x0 = _f(spec, "x0", 0.22)
    x1 = _f(spec, "x1", 0.78)
    ds = 6
    sw, sh = max(16, w // ds), max(9, h // ds)
    mask = np.zeros((sh, sw), dtype=np.float32)
    yy = np.linspace(0.0, 1.0, sh, dtype=np.float32)[:, None]
    for i in range(count):
        t = (i + 0.5) / count if count > 1 else 0.5
        tx = x0 + (x1 - x0) * t
        sx = 0.5 + (tx - 0.5) * 0.35
        top_px = sx * sw
        bot_px = tx * sw
        half_bot = spread * sw * (0.55 + 0.45 * (i % 2))
        half_top = max(1.0, half_bot * 0.10)
        xa = np.clip(np.minimum(np.abs(np.arange(sw)[None, :] - bot_px)
                                - (half_top + (half_bot - half_top) * yy), 0), -1, 0)
        cone = np.where(xa > -1, 1.0, 0.0)
        # 纵向衰减
        falloff = np.clip(1.0 - (yy - 0.0) * 0.85, 0.0, 1.0) ** 1.4
        mask = np.maximum(mask, cone * falloff)
    mask_u8 = Image.fromarray((np.clip(mask, 0, 1) * 255).astype(np.uint8), "L")
    full = mask_u8.resize((w, h), Image.BILINEAR)
    if _f(spec, "blur", 1.0) > 0:
        full = full.filter(ImageFilter.GaussianBlur(max(0.5, ctx.S(0.006))))
    img.alpha_composite(mask_layer((w, h), full, color, int(140 * strength)))
    # 地面光池
    pools = []
    for i in range(count):
        t = (i + 0.5) / count if count > 1 else 0.5
        pools.append((ctx.X(x0 + (x1 - x0) * t), ctx.Y(y_tgt),
                      ctx.S(spread * 1.1), ctx.S(0.02), 0.55))
    img.alpha_composite(glow_layer((w, h), pools, color, power=1.5, downscale=5))
    # 光尘
    rng = ctx.rng_from(f"spot{ctx.seed}")
    draw = ImageDraw.Draw(img)
    for i in range(count * 14):
        t = (i % count + 0.5) / count
        px = ctx.X(x0 + (x1 - x0) * t) + rng.uniform(-ctx.S(spread * 0.5), ctx.S(spread * 0.5))
        py = rng.uniform(ctx.Y(0.02), ctx.Y(y_tgt))
        r = max(0.6, ctx.S(rng.uniform(0.0006, 0.0016)))
        draw.ellipse((px - r, py - r, px + r, py + r),
                     fill=color[:3] + (rng.randint(40, 130),))


# ===========================================================================
# 18. 内置 primitive — 战场（士兵/骑兵/长矛/关隘）
# ===========================================================================
def _draw_figure(draw: ImageDraw.ImageDraw, cx: float, foot_y: float, height: float,
                 color, *, spear: bool = False, dir: int = 1,
                 rng: random.Random | None = None) -> None:
    """人物剪影（士兵/民众）：头 + 盔 + 躯干 + 腿 + 手臂（+长矛）。"""
    h = max(6.0, float(height))
    fg = tuple(int(c) for c in color[:3]) + (int(color[3]) if len(color) > 3 else 255,)
    head_r = h * 0.075
    head_cy = foot_y - h + head_r
    # 腿
    leg_top = foot_y - h * 0.44
    draw.polygon([(cx - h * 0.075, leg_top), (cx - h * 0.010, leg_top),
                  (cx - h * 0.030, foot_y), (cx - h * 0.095, foot_y)], fill=fg)
    draw.polygon([(cx + h * 0.010, leg_top), (cx + h * 0.075, leg_top),
                  (cx + h * 0.095, foot_y), (cx + h * 0.030, foot_y)], fill=fg)
    # 躯干（战袍）
    draw.polygon([(cx - h * 0.10, head_cy + head_r * 0.8),
                  (cx + h * 0.10, head_cy + head_r * 0.8),
                  (cx + h * 0.13, leg_top + h * 0.02),
                  (cx - h * 0.13, leg_top + h * 0.02)], fill=fg)
    # 头 + 盔
    draw.ellipse((cx - head_r, head_cy - head_r, cx + head_r, head_cy + head_r), fill=fg)
    draw.polygon([(cx - head_r * 1.25, head_cy - head_r * 0.55),
                  (cx, head_cy - head_r * 1.75),
                  (cx + head_r * 1.25, head_cy - head_r * 0.55)], fill=fg)
    if rng is not None and rng.random() < 0.5:  # 盔缨
        draw.line([(cx, head_cy - head_r * 1.7), (cx + dir * head_r * 0.9,
                                                 head_cy - head_r * 2.4)],
                  fill=fg, width=max(1, int(head_r * 0.3)))
    # 手臂
    draw.line([(cx - h * 0.10, head_cy + head_r), (cx - h * 0.16, leg_top)],
              fill=fg, width=max(1, int(h * 0.030)))
    draw.line([(cx + h * 0.10, head_cy + head_r), (cx + h * 0.15, leg_top - h * 0.02)],
              fill=fg, width=max(1, int(h * 0.030)))
    if spear:
        sx = cx + dir * h * 0.16
        draw.line([(sx, foot_y - h * 0.06), (sx + dir * h * 0.06, foot_y - h * 1.35)],
                  fill=fg, width=max(1, int(h * 0.018)))
        tipx = sx + dir * h * 0.06
        tipy = foot_y - h * 1.35
        draw.polygon([(tipx, tipy - h * 0.10),
                      (tipx - dir * h * 0.025, tipy + h * 0.02),
                      (tipx + dir * h * 0.025, tipy + h * 0.02)], fill=fg)


def p_soldier_silhouettes(img: Image.Image, spec: dict, ctx: SceneCtx) -> None:
    """军阵/人群剪影（战场）：3 个纵深排，越远越淡越小（视差层）。"""
    w, h = ctx.w, ctx.h
    bands = max(1, min(4, _i(spec, "bands", 3)))
    total = max(3, min(120, _i(spec, "count", 18)))
    color = ctx.spec_color(spec, "color", "fg")
    bg_c = ctx.color("bg")
    y_base = _f(spec, "y", 0.84)
    scale = _f(spec, "scale", 0.16)
    rng = ctx.rng_from(f"soldiers{ctx.seed}{_i(spec, 'seed', 0)}")
    draw = ImageDraw.Draw(img)
    for band in range(bands):
        t = band / max(1, bands - 1)
        row_y = ctx.Y(y_base - 0.035 + t * 0.10)
        band_color = mix(color, bg_c, 0.26 - 0.14 * t)
        per_band = max(1, total // bands + (band % 2))
        band_scale = scale * (0.72 + 0.42 * t)
        for i in range(per_band):
            cx = ctx.X(-0.03 + (i + 0.5) / per_band * 1.06 + rng.uniform(-0.012, 0.012))
            gh = ctx.S(band_scale) * rng.uniform(0.86, 1.12)
            _draw_figure(draw, cx, row_y + rng.uniform(-2, 2), gh, band_color,
                         spear=bool(spec.get("spears", True)) and rng.random() < 0.75,
                         dir=1 if rng.random() > 0.35 else -1, rng=rng)
    # 脚下尘土
    if _f(spec, "dust", 0.5) > 0.05:
        dust = ctx.color("paper")
        img.alpha_composite(glow_layer(
            (w, h), [(w * 0.5, ctx.Y(y_base + 0.02), w * 0.55, h * 0.035,
                      0.28 * _f(spec, "dust", 0.5))], dust, power=1.4, downscale=8))


def _draw_horse(draw: ImageDraw.ImageDraw, cx: float, foot_y: float, height: float,
                color, *, dir: int = 1, rider: bool = False,
                rng: random.Random | None = None) -> None:
    """马/骑兵剪影：躯干椭圆 + 颈头 + 四腿 + 尾（+骑手长矛）。"""
    hh = max(8.0, float(height))
    c = tuple(int(v) for v in color[:3]) + (int(color[3]) if len(color) > 3 else 255,)
    body_y = foot_y - hh * 0.62
    # 躯干
    draw.ellipse((cx - hh * 0.34, body_y - hh * 0.16, cx + hh * 0.34, body_y + hh * 0.18),
                 fill=c)
    # 颈 + 头
    neck_x = cx + dir * hh * 0.30
    draw.polygon([(neck_x - dir * hh * 0.10, body_y - hh * 0.04),
                  (neck_x - dir * hh * 0.02, body_y - hh * 0.42),
                  (neck_x + dir * hh * 0.14, body_y - hh * 0.44),
                  (neck_x + dir * hh * 0.10, body_y - hh * 0.06)], fill=c)
    draw.polygon([(neck_x + dir * hh * 0.10, body_y - hh * 0.44),
                  (neck_x + dir * hh * 0.28, body_y - hh * 0.38),
                  (neck_x + dir * hh * 0.26, body_y - hh * 0.30),
                  (neck_x + dir * hh * 0.10, body_y - hh * 0.32)], fill=c)
    # 马鬃/耳
    draw.polygon([(neck_x + dir * hh * 0.02, body_y - hh * 0.44),
                  (neck_x - dir * hh * 0.02, body_y - hh * 0.52),
                  (neck_x + dir * hh * 0.10, body_y - hh * 0.46)], fill=c)
    # 四腿
    for i, (lx, spread) in enumerate(((-0.24, 0.02), (-0.12, -0.03),
                                      (0.14, 0.05), (0.26, -0.02))):
        angle = spread * (1 if i % 2 == 0 else -1) * 2.2
        draw.line([(cx + lx * hh, body_y + hh * 0.10),
                   (cx + lx * hh + angle * hh, foot_y)],
                  fill=c, width=max(1, int(hh * 0.045)))
    # 尾
    tail = [(cx - dir * hh * 0.34, body_y - hh * 0.10),
            (cx - dir * hh * 0.46, body_y + hh * 0.04),
            (cx - dir * hh * 0.44, body_y + hh * 0.22)]
    draw.line(chaikin(tail, 2), fill=c, width=max(1, int(hh * 0.04)))
    if rider:
        _draw_figure(draw, cx - dir * hh * 0.06, body_y - hh * 0.10, hh * 0.62, c,
                     spear=True, dir=dir, rng=rng)


def p_horses(img: Image.Image, spec: dict, ctx: SceneCtx) -> None:
    """骑兵/战马（战场、渡口）：远近两排，带骑手与烟尘。"""
    w, h = ctx.w, ctx.h
    count = max(1, min(16, _i(spec, "count", 4)))
    y = _f(spec, "y", 0.86)
    scale = _f(spec, "scale", 0.20)
    color = ctx.spec_color(spec, "color", "fg")
    bg_c = ctx.color("bg")
    rng = ctx.rng_from(f"horse{ctx.seed}{_i(spec, 'seed', 0)}")
    draw = ImageDraw.Draw(img)
    for i in range(count):
        t = i / max(1, count - 1)
        cx = ctx.X(0.06 + 0.88 * (i + 0.5) / count + rng.uniform(-0.02, 0.02))
        depth = rng.random()
        gh = ctx.S(scale) * (0.75 + 0.35 * depth)
        row_y = ctx.Y(y - depth * 0.06)
        col = mix(color, bg_c, 0.16 - 0.10 * depth)
        _draw_horse(draw, cx, row_y, gh, col,
                    dir=1 if t < 0.5 else -1,
                    rider=bool(spec.get("riders", True)) and rng.random() < 0.8, rng=rng)
    if _f(spec, "dust", 0.6) > 0.05:
        img.alpha_composite(glow_layer(
            (w, h), [(w * 0.5, ctx.Y(y + 0.01), w * 0.55, h * 0.04,
                      0.30 * _f(spec, "dust", 0.6))],
            ctx.color("paper"), power=1.4, downscale=8))


def p_spears(img: Image.Image, spec: dict, ctx: SceneCtx) -> None:
    """矛林（军阵/关隘）：斜向长矛 + 枪尖 + 缨穗。"""
    w, h = ctx.w, ctx.h
    count = max(4, min(80, _i(spec, "count", 22)))
    y = _f(spec, "y", 0.88)
    length = _f(spec, "length", 0.34)
    color = ctx.spec_color(spec, "color", "fg")
    rng = ctx.rng_from(f"spears{ctx.seed}")
    draw = ImageDraw.Draw(img)
    for i in range(count):
        base_x = ctx.X(0.02 + 0.96 * (i + 0.5) / count + rng.uniform(-0.01, 0.01))
        base_y = ctx.Y(y - rng.uniform(0.0, 0.03))
        ln = ctx.S(length * rng.uniform(0.85, 1.15))
        lean = rng.uniform(-0.10, 0.10)
        tipx = base_x + ln * lean
        tipy = base_y - ln
        draw.line([(base_x, base_y), (tipx, tipy)], fill=color[:3] + (230,),
                  width=max(1, int(ctx.S(0.0016))))
        # 枪尖
        draw.polygon([(tipx, tipy - ctx.S(0.018)),
                      (tipx - ctx.S(0.005), tipy + ctx.S(0.002)),
                      (tipx + ctx.S(0.005), tipy + ctx.S(0.002))],
                     fill=color[:3] + (240,))
        # 缨穗
        if rng.random() < 0.5:
            draw.line([(tipx, tipy + ctx.S(0.004)),
                       (tipx + rng.uniform(-8, 8), tipy + ctx.S(0.030))],
                      fill=ctx.color("accent")[:3] + (200,), width=1)


def p_wall_gate(img: Image.Image, spec: dict, ctx: SceneCtx) -> None:
    """关隘/城墙（长城）：城墙 + 垛口 + 城门楼 + 烽火台 + 石缝。"""
    w, h = ctx.w, ctx.h
    x0 = ctx.X(_f(spec, "x", 0.02))
    x1 = ctx.X(_f(spec, "x", 0.02) + _f(spec, "w", 0.96))
    base_y = ctx.Y(_f(spec, "y", 0.82))
    wall_h = ctx.S(_f(spec, "height", 0.16))
    color = ctx.spec_color(spec, "color", "stone")
    dark = mix(color, ctx.color("fg"), 0.55)
    light = mix(color, ctx.color("highlight"), 0.28)
    draw = ImageDraw.Draw(img)
    # 山体基座
    draw.rectangle((x0, base_y, x1, ctx.Y(0.99)), fill=mix(color, ctx.color("shadow"), 0.5)[:3] + (250,))
    # 城墙主体（透视：略有斜度）
    wall = [(x0, base_y), (x1, base_y), (x1, base_y - wall_h), (x0, base_y - wall_h)]
    draw.polygon(wall, fill=color[:3] + (252,))
    # 砖缝
    rows = max(3, int(wall_h / max(4, ctx.S(0.014))))
    for r in range(1, rows):
        ly = base_y - wall_h * r / rows
        draw.line([(x0, ly), (x1, ly)], fill=dark[:3] + (110,), width=1)
    for cidx in range(1, 6):
        lx = x0 + (x1 - x0) * cidx / 6.0
        draw.line([(lx, base_y), (lx, base_y - wall_h)], fill=dark[:3] + (80,), width=1)
    # 垛口
    merlon_w = (x1 - x0) / 15.0
    for k in range(15):
        mx = x0 + k * merlon_w
        if k % 2 == 0:
            draw.rectangle((mx, base_y - wall_h - ctx.S(0.022), mx + merlon_w * 0.85,
                            base_y - wall_h), fill=color[:3] + (252,))
    draw.line([(x0, base_y - wall_h), (x1, base_y - wall_h)],
              fill=light[:3] + (150,), width=max(1, int(ctx.S(0.002))))
    # 城门楼（双层）
    gate_w = _f(spec, "gate_w", 0.12)
    gx = x0 + (x1 - x0) * _f(spec, "gate_x", 0.5)
    gw = ctx.S(gate_w) * 0.5
    tower_h = ctx.S(0.20)
    ty = base_y - wall_h
    for level in range(2):
        lw = gw * (1.35 - level * 0.25)
        lh = tower_h * (0.42 - level * 0.06)
        ly = ty - tower_h * (0.40 + level * 0.34)
        draw.rectangle((gx - lw, ly - lh, gx + lw, ly), fill=color[:3] + (252,))
        draw.rectangle((gx - lw * 1.20, ly - lh, gx + lw * 1.20, ly - lh + ctx.S(0.012)),
                       fill=mix(color, ctx.color("fg"), 0.35)[:3] + (250,))
        # 屋顶（翘角）
        roof = [(gx - lw * 1.45, ly - lh), (gx + lw * 1.45, ly - lh),
                (gx + lw * 1.05, ly - lh - ctx.S(0.030)),
                (gx - lw * 1.05, ly - lh - ctx.S(0.030))]
        draw.polygon(roof, fill=mix(color, ctx.color("fg"), 0.30)[:3] + (252,))
        draw.line([(gx - lw * 1.45, ly - lh), (gx + lw * 1.45, ly - lh)],
                  fill=light[:3] + (180,), width=max(1, int(ctx.S(0.0018))))
        # 窗（暖光）
        for wnd in (-0.5, 0.5):
            wx = gx + lw * wnd
            draw.rectangle((wx - lw * 0.10, ly - lh * 0.65, wx + lw * 0.10, ly - lh * 0.25),
                           fill=ctx.color("glow", fallback="accent")[:3] + (190,))
        if level == 0:
            # 城门洞
            draw.pieslice((gx - gw * 0.42, ly - lh * 1.8, gx + gw * 0.42, ly - lh * 0.6),
                          start=180, end=360, fill=ctx.color("shadow")[:3] + (245,))
            draw.rectangle((gx - gw * 0.42, ly - lh * 1.2, gx + gw * 0.42, ly),
                           fill=ctx.color("shadow")[:3] + (245,))
    # 烽火台（两侧）
    for side in (-1, 1):
        bx = gx + side * (x1 - x0) * 0.33
        bw2 = ctx.S(0.045)
        bh2 = wall_h * 1.75
        draw.polygon([(bx - bw2 * 0.75, base_y - wall_h), (bx + bw2 * 0.75, base_y - wall_h),
                      (bx + bw2 * 0.55, base_y - wall_h - bh2),
                      (bx - bw2 * 0.55, base_y - wall_h - bh2)], fill=color[:3] + (252,))
        draw.rectangle((bx - bw2 * 0.65, base_y - wall_h - bh2, bx + bw2 * 0.65,
                        base_y - wall_h - bh2 + ctx.S(0.012)),
                       fill=mix(color, ctx.color("fg"), 0.3)[:3] + (250,))
    # 城墙脚阴影
    img.alpha_composite(glow_layer(
        (w, h), [(w * 0.5, base_y + ctx.S(0.01), w * 0.55, h * 0.025, 0.30)],
        ctx.color("shadow"), power=1.5, downscale=8))


# ===========================================================================
# 19. 内置 primitive — 市井/舟船
# ===========================================================================
def p_crowd(img: Image.Image, spec: dict, ctx: SceneCtx) -> None:
    """人群剪影（市井/观众）：肩头轮廓两层，越远越淡。"""
    w, h = ctx.w, ctx.h
    count = max(4, min(80, _i(spec, "count", 16)))
    y = _f(spec, "y", 0.86)
    color = ctx.spec_color(spec, "color", "fg")
    bg_c = ctx.color("bg")
    rng = ctx.rng_from(f"crowd{ctx.seed}{_i(spec, 'seed', 0)}")
    draw = ImageDraw.Draw(img)
    for layer in range(2):
        t = layer / 1.0
        row_y = ctx.Y(y - 0.02 + t * 0.06)
        col = mix(color, bg_c, 0.28 - 0.16 * t)
        per = max(2, count // 2)
        for i in range(per):
            cx = ctx.X(-0.02 + (i + 0.5) / per * 1.04 + rng.uniform(-0.015, 0.015))
            hh = ctx.S(_f(spec, "scale", 0.11)) * rng.uniform(0.85, 1.1) * (0.8 + 0.3 * t)
            head_r = hh * 0.13
            cy = row_y - hh
            draw.ellipse((cx - head_r, cy - head_r, cx + head_r, cy + head_r),
                         fill=col[:3] + (235,))
            # 肩部只在脖子以下（否则整体像保龄球瓶）
            shoulder_top = cy + head_r * 1.9
            draw.ellipse((cx - hh * 0.42, shoulder_top, cx + hh * 0.42,
                          row_y + hh * 0.15), fill=col[:3] + (235,))
            draw.rectangle((cx - hh * 0.30, shoulder_top, cx + hh * 0.30,
                            row_y + hh * 0.2), fill=col[:3] + (235,))


def p_stalls(img: Image.Image, spec: dict, ctx: SceneCtx) -> None:
    """市井摊位：条纹布篷 + 支杆 + 摊桌 + 货物 + 摊旗。"""
    w, h = ctx.w, ctx.h
    count = max(1, min(10, _i(spec, "count", 3)))
    y = _f(spec, "y", 0.72)
    scale = _f(spec, "scale", 0.22)
    color = ctx.spec_color(spec, "color", "accent")
    alt = ctx.color("paper")
    wood = ctx.color("wood")
    rng = ctx.rng_from(f"stall{ctx.seed}{_i(spec, 'seed', 0)}")
    draw = ImageDraw.Draw(img)
    for i in range(count):
        t = (i + 0.5) / count if count > 1 else 0.5
        cx = ctx.X(0.06 + 0.88 * t + rng.uniform(-0.02, 0.02))
        base_y = ctx.Y(y + rng.uniform(-0.02, 0.02))
        sw = ctx.S(scale) * rng.uniform(0.75, 1.1)
        s_ceiling = base_y - sw * 0.95
        # 布篷（条纹）
        canopy = [(cx - sw, s_ceiling + sw * 0.12), (cx + sw, s_ceiling + sw * 0.12),
                  (cx + sw * 0.9, s_ceiling), (cx - sw * 0.9, s_ceiling)]
        draw.polygon(canopy, fill=alt[:3] + (252,))
        stripes = 7
        for k in range(stripes):
            if k % 2:
                continue
            ax = cx - sw * 0.9 + (k / stripes) * sw * 1.8
            bx = cx - sw * 0.9 + ((k + 0.8) / stripes) * sw * 1.8
            draw.polygon([(ax, s_ceiling), (bx, s_ceiling),
                          (bx + sw * 0.03, s_ceiling + sw * 0.12),
                          (ax + sw * 0.03, s_ceiling + sw * 0.12)],
                         fill=color[:3] + (240,))
        # 支杆
        for side in (-1, 1):
            px = cx + side * sw * 0.88
            draw.line([(px, s_ceiling + sw * 0.10), (px, base_y)],
                      fill=wood[:3] + (245,), width=max(2, int(sw * 0.035)))
        # 摊桌
        draw.rectangle((cx - sw * 0.82, base_y - sw * 0.25, cx + sw * 0.82, base_y),
                       fill=mix(wood, ctx.color("shadow"), 0.25)[:3] + (252,))
        # 货物
        for _ in range(rng.randint(3, 6)):
            gx = cx + rng.uniform(-sw * 0.65, sw * 0.65)
            gr = sw * rng.uniform(0.07, 0.13)
            gc = rng.choice([ctx.color("paper"), ctx.color("accent"),
                             mix(ctx.color("accent"), ctx.color("highlight"), 0.45),
                             ctx.color("glow", fallback="highlight")])
            draw.ellipse((gx - gr, base_y - sw * 0.25 - gr * 1.6, gx + gr, base_y - sw * 0.25),
                         fill=gc[:3] + (240,))
        # 摊旗
        draw.line([(cx + sw * 0.6, s_ceiling), (cx + sw * 0.6, s_ceiling - sw * 0.42)],
                  fill=wood[:3] + (240,), width=max(1, int(sw * 0.02)))
        draw.polygon([(cx + sw * 0.6, s_ceiling - sw * 0.42),
                      (cx + sw * 1.05, s_ceiling - sw * 0.34),
                      (cx + sw * 0.6, s_ceiling - sw * 0.24)],
                     fill=color[:3] + (240,))
        # 灯笼暖光
        img.alpha_composite(glow_layer(
            (w, h), [(cx, s_ceiling + sw * 0.22, sw * 1.2, sw * 0.7, 0.28)],
            ctx.color("glow", fallback="highlight"), power=1.8, downscale=8))


def p_boats(img: Image.Image, spec: dict, ctx: SceneCtx) -> None:
    """舟船/战船（渡口/江面）：船体 + 桅杆 + 帆 + 旗 + 倒影。"""
    w, h = ctx.w, ctx.h
    count = max(1, min(8, _i(spec, "count", 3)))
    y = _f(spec, "y", 0.68)
    scale = _f(spec, "scale", 0.28)
    hull_c = ctx.spec_color(spec, "color", "wood")
    sail_c = ctx.color("paper")
    accent = ctx.color("accent")
    rng = ctx.rng_from(f"boat{ctx.seed}{_i(spec, 'seed', 0)}")
    draw = ImageDraw.Draw(img)
    for i in range(count):
        t = (i + 0.5) / count if count > 1 else 0.5
        cx = ctx.X(0.10 + 0.80 * t + rng.uniform(-0.03, 0.03))
        by = ctx.Y(y + rng.uniform(-0.02, 0.04))
        L = ctx.S(scale) * rng.uniform(0.65, 1.0)
        direction = 1 if rng.random() > 0.5 else -1
        # 船体
        hull = [(cx - L * 0.55, by), (cx - L * 0.35, by + L * 0.14),
                (cx + L * 0.35, by + L * 0.14), (cx + L * 0.58, by - L * 0.02),
                (cx + L * 0.30, by - L * 0.06), (cx - L * 0.38, by - L * 0.06)]
        draw.polygon(hull, fill=hull_c[:3] + (252,))
        draw.line([(cx - L * 0.38, by - L * 0.06), (cx + L * 0.30, by - L * 0.06)],
                  fill=mix(hull_c, ctx.color("highlight"), 0.4)[:3] + (200,),
                  width=max(1, int(L * 0.02)))
        # 篷/舷窗
        draw.rectangle((cx - L * 0.10, by - L * 0.20, cx + L * 0.12, by - L * 0.06),
                       fill=mix(hull_c, ctx.color("shadow"), 0.4)[:3] + (245,))
        # 桅杆 + 帆
        mast_x = cx + direction * L * 0.04
        mast_h = L * rng.uniform(0.75, 1.05)
        draw.line([(mast_x, by - L * 0.12), (mast_x, by - L * 0.12 - mast_h)],
                  fill=mix(hull_c, ctx.color("fg"), 0.4)[:3] + (245,),
                  width=max(2, int(L * 0.022)))
        sail_w = L * rng.uniform(0.42, 0.58)
        sail = [(mast_x, by - L * 0.12 - mast_h * 0.94),
                (mast_x + direction * sail_w, by - L * 0.12 - mast_h * 0.74),
                (mast_x + direction * sail_w * 0.86, by - L * 0.12 - mast_h * 0.18),
                (mast_x, by - L * 0.12 - mast_h * 0.12)]
        draw.polygon(sail, fill=sail_c[:3] + (245,))
        # 帆骨
        for k in range(1, 5):
            kt = k / 5.0
            draw.line([(mast_x, by - L * 0.12 - mast_h * (0.94 - 0.80 * kt)),
                       (mast_x + direction * sail_w * (1 - 0.12 * kt),
                        by - L * 0.12 - mast_h * (0.74 - 0.56 * kt))],
                      fill=mix(sail_c, ctx.color("shadow"), 0.35)[:3] + (140,), width=1)
        # 旗
        draw.polygon([(mast_x, by - L * 0.12 - mast_h),
                      (mast_x + direction * L * 0.16, by - L * 0.12 - mast_h * 0.92),
                      (mast_x, by - L * 0.12 - mast_h * 0.84)],
                     fill=accent[:3] + (240,))
        # 倒影
        refl = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        rd = ImageDraw.Draw(refl)
        rd.polygon([(x, 2 * by - yy) for x, yy in hull], fill=hull_c[:3] + (60,))
        refl = refl.filter(ImageFilter.GaussianBlur(max(1.0, L * 0.02)))
        img.alpha_composite(refl)
    # 水面微光
    img.alpha_composite(glow_layer(
        (w, h), [(w * 0.5, ctx.Y(y + 0.12), w * 0.5, h * 0.03, 0.20)],
        ctx.color("highlight"), power=1.4, downscale=8))


# ===========================================================================
# 20. 内置 primitive — 烟/雨/雪/粒子
# ===========================================================================
def p_smoke(img: Image.Image, spec: dict, ctx: SceneCtx) -> None:
    """烟/雾/烽火（前景或中景）：柔化团块沿上升曲线堆叠。"""
    w, h = ctx.w, ctx.h
    count = max(1, min(8, _i(spec, "count", 3)))
    color = ctx.spec_color(spec, "color", "shadow")
    density = max(0.1, min(2.0, _f(spec, "density", 1.0)))
    y0 = _f(spec, "y", 0.72)
    rise = _f(spec, "rise", 0.30)
    spread = _f(spec, "spread", 0.18)
    alpha = int(max(20, min(230, _f(spec, "alpha", 120))))
    rng = ctx.rng_from(f"smoke{ctx.seed}{_i(spec, 'seed', 0)}")
    plume = bool(spec.get("plume", False))
    ds = 3
    sw, sh = max(24, w // ds), max(14, h // ds)

    def painter(draw: ImageDraw.ImageDraw, cw: int, ch: int) -> None:
        for c in range(count):
            cx0 = (0.12 + 0.76 * (c + 0.5) / count + rng.uniform(-0.05, 0.05)) * cw
            base = y0 * ch
            col = color[:3]
            steps = int(14 * density)
            for k in range(steps):
                t = k / float(max(1, steps - 1))
                yy = base - rise * ch * t
                xx = cx0 + math.sin(t * 3.2 + c) * spread * cw * 0.25 + rng.uniform(-2, 2)
                rx = spread * cw * (0.12 + 0.55 * t) * (1.2 if plume else 1.0)
                ry = rx * (0.55 if plume else 0.5)
                a = int(alpha * (0.30 + 0.70 * t) * (0.45 + 0.55 * rng.random()))
                draw.ellipse((xx - rx, yy - ry, xx + rx, yy + ry),
                             fill=col + (max(6, a // 3),))

    img.alpha_composite(soft_layer((w, h), painter, blur=max(4.0, ctx.S(0.012)),
                                   downscale=ds))


def p_rain(img: Image.Image, spec: dict, ctx: SceneCtx) -> None:
    """雨：斜向雨丝（前景），可选浅雾。"""
    w, h = ctx.w, ctx.h
    count = max(10, min(900, _i(spec, "count", 220)))
    color = ctx.spec_color(spec, "color", "highlight")
    angle = math.radians(_f(spec, "angle", 14.0))
    length = _len(ctx, spec, "length", 0.055)
    width = max(1, int(_len(ctx, spec, "width", 1.2)))
    rng = ctx.rng_from(f"rain{ctx.seed}")
    layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    dx, dy = math.sin(angle), math.cos(angle)
    for _ in range(count):
        x, y = rng.uniform(-0.05, 1.05) * w, rng.uniform(-0.05, 1.0) * h
        ln = length * rng.uniform(0.5, 1.5)
        draw.line([(x, y), (x + dx * ln, y + dy * ln)],
                  fill=color[:3] + (rng.randint(28, 110),), width=width)
    img.alpha_composite(layer.filter(ImageFilter.GaussianBlur(0.6)))
    if _f(spec, "mist", 0.0) > 0.05:
        p_fog(img, {"y": 0.75, "count": 2, "alpha": int(80 * _f(spec, "mist", 0.0)),
                    "color": "highlight"}, ctx)


def p_snow(img: Image.Image, spec: dict, ctx: SceneCtx) -> None:
    """雪：不同大小雪点（远景小/近景大），轻微模糊。"""
    w, h = ctx.w, ctx.h
    count = max(10, min(700, _i(spec, "count", 160)))
    color = ctx.spec_color(spec, "color", "highlight")
    r_max = _len(ctx, spec, "size", 0.006)
    rng = ctx.rng_from(f"snow{ctx.seed}")
    layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    for _ in range(count):
        x, y = rng.uniform(0, 1.0) * w, rng.uniform(0, 1.0) * h
        depth = rng.random()
        r = max(0.6, r_max * (0.3 + 1.5 * depth))
        a = int(70 + 160 * depth)
        draw.ellipse((x - r, y - r, x + r, y + r), fill=color[:3] + (a,))
    img.alpha_composite(layer.filter(ImageFilter.GaussianBlur(0.7)))


def p_particles(img: Image.Image, spec: dict, ctx: SceneCtx) -> None:
    """浮尘/光尘/花瓣：小点 + 柔晕 + 少量亮粒子。"""
    w, h = ctx.w, ctx.h
    count = max(4, min(400, _i(spec, "count", 60)))
    color = ctx.spec_color(spec, "color", "highlight")
    size = _len(ctx, spec, "size", 0.0022)
    drift = _f(spec, "drift", 0.0)
    rng = ctx.rng_from(f"particles{ctx.seed}{_i(spec, 'seed', 0)}")
    layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    points = []
    for _ in range(count):
        x = rng.uniform(0, 1.0) * w + drift * w * rng.uniform(-0.5, 0.5)
        y = rng.uniform(0, 1.0) * h
        depth = rng.random()
        r = max(0.6, size * (0.4 + 1.8 * depth))
        a = int(40 + 150 * depth)
        points.append((x, y, r, a))
        draw.ellipse((x - r, y - r, x + r, y + r), fill=color[:3] + (a,))
    img.alpha_composite(layer.filter(ImageFilter.GaussianBlur(max(0.6, ctx.S(0.0012)))))
    draw = ImageDraw.Draw(img)
    for x, y, r, a in points:
        if a > 120 and r > size * 0.9:
            draw.ellipse((x - r * 0.5, y - r * 0.5, x + r * 0.5, y + r * 0.5),
                         fill=color[:3] + (min(255, a + 70),))


# ===========================================================================
# 21. 内置 primitive — 现代风格（霓虹/天际线/蓝图/全息/胶片/拼贴/黏土/粉笔）
# ===========================================================================
def p_neon_signs(img: Image.Image, spec: dict, ctx: SceneCtx) -> None:
    """霓虹招牌（现代夜景/演播厅）：发光灯带 + 灯箱 + 地面反射。"""
    w, h = ctx.w, ctx.h
    count = max(1, min(14, _i(spec, "count", 5)))
    color = ctx.spec_color(spec, "color", "accent")
    alt = ctx.color("accent", fallback="accent", index=1)
    rng = ctx.rng_from(f"neon{ctx.seed}{_i(spec, 'seed', 0)}")
    draw = ImageDraw.Draw(img)
    for i in range(count):
        t = (i + 0.5) / count
        cx = ctx.X(0.08 + 0.84 * t + rng.uniform(-0.03, 0.03))
        cy = ctx.Y(rng.uniform(0.14, 0.52))
        sw = ctx.S(rng.uniform(0.06, 0.13))
        sh = ctx.S(rng.uniform(0.012, 0.026))
        c = color if rng.random() < 0.6 else alt
        vertical = rng.random() < 0.3
        if vertical:
            sw, sh = sh * 1.4, sw * 1.8
        # 灯箱
        draw.rounded_rectangle((cx - sw, cy - sh, cx + sw, cy + sh),
                               radius=max(1, int(sh * 0.6)), fill=(6, 6, 10, 235))
        # 发光描边（多圈）
        for k, (scale, alpha) in enumerate(((1.02, 160), (1.10, 80), (1.22, 40))):
            draw.rounded_rectangle((cx - sw * scale, cy - sh * scale,
                                    cx + sw * scale, cy + sh * scale),
                                   radius=max(1, int(sh * 0.6)),
                                   outline=c[:3] + (alpha,), width=max(1, 3 - k))
        img.alpha_composite(glow_layer(
            (w, h), [(cx, cy, sw * 1.8, sh * 2.2, 0.55)], c, power=1.8, downscale=6))
        # 文字
        text = str(spec.get("text") or "")
        fs = max(9, int(sh * 1.05))
        if text:
            ch = text[i % len(text)]
            draw_text(draw, (cx, cy), ch, fs, c[:3] + (245,), display=True)
        else:
            for k in range(3):
                off = (k - 1) * sh * 0.45
                if vertical:
                    draw.line([(cx + off, cy - sh * 0.6), (cx + off, cy + sh * 0.6)],
                              fill=c[:3] + (230,), width=max(1, int(sh * 0.16)))
                else:
                    draw.line([(cx - sw * 0.7, cy + off), (cx + sw * 0.7, cy + off)],
                              fill=c[:3] + (230,), width=max(1, int(sh * 0.16)))
        # 地面反射
        img.alpha_composite(glow_layer(
            (w, h), [(cx, ctx.Y(0.94), sw * 1.2, ctx.S(0.02), 0.20)],
            c, power=1.6, downscale=8))


def p_skyline(img: Image.Image, spec: dict, ctx: SceneCtx) -> None:
    """城市天际线（现代夜景）：3 层楼群剪影 + 窗灯 + 层间雾。"""
    w, h = ctx.w, ctx.h
    color = ctx.spec_color(spec, "color", "fg")
    bg_c = ctx.color("bg")
    y = _f(spec, "y", 0.66)
    rows = max(1, min(4, _i(spec, "rows", 3)))
    count = max(6, min(60, _i(spec, "count", 14)))
    rng = ctx.rng_from(f"skyline{ctx.seed}{_i(spec, 'seed', 0)}")
    draw = ImageDraw.Draw(img)
    for row in range(rows):
        t = row / max(1, rows - 1)
        base_y = ctx.Y(y + t * 0.14)
        col = mix(color, bg_c, 0.55 - 0.42 * t)
        bw = w / float(count) * rng.uniform(0.8, 1.5)
        for i in range(count):
            bx = i * (w / float(count)) + rng.uniform(-6, 6) - bw / 2
            bld_h = ctx.S(_f(spec, "height", 0.30)) * rng.uniform(0.35, 1.0) * (0.7 + 0.4 * t)
            draw.rectangle((bx, base_y - bld_h, bx + bw, base_y), fill=col[:3] + (250,))
            # 顶灯
            if rng.random() < 0.4:
                draw.line([(bx + bw * 0.5, base_y - bld_h),
                           (bx + bw * 0.5, base_y - bld_h - ctx.S(0.012))],
                          fill=ctx.color("accent")[:3] + (180,), width=1)
            # 窗灯
            wc = ctx.color("glow", fallback="accent")
            win_w = max(2.0, bw * 0.10)
            win_h = max(2.0, win_w * 1.3)
            nx = max(1, int(bw / (win_w * 2.4)))
            ny = max(1, int(bld_h / (win_h * 2.2)))
            for ix in range(nx):
                for iy in range(ny):
                    if rng.random() < _f(spec, "windows", 0.30):
                        wx = bx + win_w * 0.8 + ix * win_w * 2.4
                        wy = base_y - bld_h + win_h * 0.8 + iy * win_h * 2.2
                        draw.rectangle((wx, wy, wx + win_w, wy + win_h),
                                       fill=wc[:3] + (rng.randint(90, 210),))
        # 层间雾
        img.alpha_composite(glow_layer(
            (w, h), [(w * 0.5, base_y, w * 0.6, h * 0.02, 0.20)], bg_c, power=1.4,
            downscale=8))


def p_blueprint_lines(img: Image.Image, spec: dict, ctx: SceneCtx) -> None:
    """工业蓝图（科技/档案）：细网格 + 尺寸线 + 圆规弧 + 标注。"""
    w, h = ctx.w, ctx.h
    color = ctx.spec_color(spec, "color", "highlight")
    thin = max(1, int(ctx.S(0.0012)))
    draw = ImageDraw.Draw(img)
    rng = ctx.rng_from(f"blueprint{ctx.seed}{_i(spec, 'seed', 0)}")
    grid = max(8, int(_len(ctx, spec, "spacing", 0.035)))
    for x in range(0, w, grid):
        draw.line([(x, 0), (x, h)], fill=color[:3] + (26,), width=thin)
    for y in range(0, h, grid):
        draw.line([(0, y), (w, y)], fill=color[:3] + (26,), width=thin)
    # 尺寸线 + 箭头
    for _ in range(_i(spec, "dimensions", 5)):
        x0, y0 = rng.uniform(0.08, 0.55) * w, rng.uniform(0.12, 0.85) * h
        ln = rng.uniform(0.10, 0.30) * w
        horizontal = rng.random() < 0.6
        if horizontal:
            x1, y1 = x0 + ln, y0
        else:
            x1, y1 = x0, y0 + ln * 0.6
        draw.line([(x0, y0), (x1, y1)], fill=color[:3] + (160,), width=thin)
        for tip, ang in ((x0, math.pi if horizontal else -math.pi / 2),
                         (x1, 0 if horizontal else math.pi / 2)):
            arrow_head(draw, (tip, y0 if horizontal else y1), ang,
                       ctx.S(0.012), color[:3] + (180,))
        draw.line([(x0 - ctx.S(0.008), y0 + (ctx.S(0.008) if horizontal else 0)),
                   (x0 + ctx.S(0.008), y0 - (ctx.S(0.008) if horizontal else 0))],
                  fill=color[:3] + (120,), width=thin)
    # 圆规弧 + 十字
    for _ in range(_i(spec, "circles", 3)):
        cx, cy = rng.uniform(0.15, 0.85) * w, rng.uniform(0.15, 0.85) * h
        r = rng.uniform(0.03, 0.10) * min(w, h)
        draw.ellipse((cx - r, cy - r, cx + r, cy + r),
                     outline=color[:3] + (140,), width=thin)
        draw.line([(cx - r * 1.15, cy), (cx + r * 1.15, cy)],
                  fill=color[:3] + (90,), width=thin)
        draw.line([(cx, cy - r * 1.15), (cx, cy + r * 1.15)],
                  fill=color[:3] + (90,), width=thin)
    # 标注短线
    for _ in range(_i(spec, "labels", 10)):
        lx, ly = rng.uniform(0.05, 0.9) * w, rng.uniform(0.05, 0.9) * h
        lw = rng.uniform(0.02, 0.06) * w
        draw.line([(lx, ly), (lx + lw, ly)], fill=color[:3] + (120,), width=thin)
        draw.line([(lx, ly), (lx, ly + ctx.S(0.012))], fill=color[:3] + (120,), width=thin)


def p_hologram_grid(img: Image.Image, spec: dict, ctx: SceneCtx) -> None:
    """全息网格（舞台/镜屋）：透视发光网格 + 扫描线 + 交点光点。"""
    w, h = ctx.w, ctx.h
    color = ctx.spec_color(spec, "color", "accent")
    horizon = _f(spec, "y_horizon", 0.52)
    draw = ImageDraw.Draw(img)
    vx, vy = ctx.X(_f(spec, "vanish_x", 0.5)), ctx.Y(horizon)
    for k in range(-8, 9):
        x = ctx.X(0.5 + k * 0.09)
        draw.line([(vx, vy), (x, ctx.Y(1.02))], fill=color[:3] + (120,), width=1)
    yy = vy
    step = max(3.0, ctx.S(0.008))
    while yy < h:
        draw.line([(0, yy), (w, yy)], fill=color[:3] + (100,), width=1)
        yy += step
        step *= 1.16
    # 扫描线
    for y in range(0, h, max(3, int(ctx.S(0.004)))):
        draw.line([(0, y), (w, y)], fill=color[:3] + (22,), width=1)
    # 交点光点
    rng = ctx.rng_from(f"holo{ctx.seed}")
    for _ in range(_i(spec, "dots", 46)):
        dx = rng.uniform(0, 1.0) * w
        dy = ctx.Y(horizon + rng.random() * (1.0 - horizon))
        r = max(1.0, ctx.S(rng.uniform(0.001, 0.0028)))
        draw.ellipse((dx - r, dy - r, dx + r, dy + r),
                     fill=color[:3] + (rng.randint(60, 180),))
    img.alpha_composite(glow_layer(
        (w, h), [(w * 0.5, vy, w * 0.5, h * 0.03, 0.30)], color,
        power=1.5, downscale=8))


def p_film_border(img: Image.Image, spec: dict, ctx: SceneCtx) -> None:
    """胶片边框（16mm 访谈/复古）：齿孔 + 片框 + 划痕 + 轻颗粒。"""
    w, h = ctx.w, ctx.h
    color = ctx.spec_color(spec, "color", "fg")
    draw = ImageDraw.Draw(img)
    strip = max(14.0, ctx.S(_f(spec, "strip", 0.055)))
    hole_w = strip * 0.45
    hole_h = strip * 0.30
    gap = strip * 0.55
    frame_c = (8, 7, 6, 245)
    for side in (0, 1):
        x0 = 0 if side == 0 else w - strip
        draw.rectangle((x0, 0, x0 + strip, h), fill=frame_c)
        y = gap
        while y < h:
            hx = x0 + strip * 0.28
            draw.rounded_rectangle((hx, y, hx + hole_w, y + hole_h),
                                   radius=int(hole_w * 0.2), fill=(238, 232, 218, 210))
            y += hole_h + gap
    # 上下片框细线
    for y in (0, h - max(2, int(strip * 0.16))):
        draw.rectangle((0, y, w, y + max(2, int(strip * 0.16))), fill=frame_c)
    # 划痕/灰尘
    rng = ctx.rng_from(f"film{ctx.seed}")
    for _ in range(_i(spec, "scratches", 12)):
        x = rng.uniform(0, w)
        y0 = rng.uniform(0, h * 0.7)
        draw.line([(x, y0), (x + rng.uniform(-3, 3), y0 + rng.uniform(0.05, 0.3) * h)],
                  fill=(240, 235, 220, rng.randint(18, 60)), width=1)
    for _ in range(_i(spec, "dust", 30)):
        x, y = rng.uniform(0, w), rng.uniform(0, h)
        r = max(0.6, ctx.S(rng.uniform(0.0004, 0.0012)))
        draw.ellipse((x - r, y - r, x + r, y + r), fill=(230, 224, 208, rng.randint(20, 80)))


def p_torn_paper(img: Image.Image, spec: dict, ctx: SceneCtx) -> None:
    """撕纸边（拼贴/剪纸）：不规则撕边 + 纸面 + 阴影。"""
    w, h = ctx.w, ctx.h
    color = ctx.spec_color(spec, "color", "paper")
    edge_y = _f(spec, "y", 0.72)
    jag = _f(spec, "jagged", 0.035)
    rng = ctx.rng_from(f"torn{ctx.seed}")
    pts = [(ctx.X(-0.02), ctx.Y(edge_y))]
    steps = 26
    for k in range(steps + 1):
        x = -0.02 + 1.04 * k / steps
        y = edge_y + math.sin(k * 1.7) * jag * 0.35 + rng.uniform(-jag, jag) * 0.6
        pts.append((ctx.X(x), ctx.Y(y)))
    pts.append((ctx.X(1.02), ctx.Y(1.02)))
    pts.append((ctx.X(-0.02), ctx.Y(1.02)))
    layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    # 撕边阴影
    shadow = [(x, y + ctx.S(0.004)) for x, y in pts]
    d.polygon(shadow, fill=(0, 0, 0, 70))
    d.polygon(pts, fill=color[:3] + (250,))
    # 毛边
    edge_pts = pts[1:steps + 2]
    brush_line(d, edge_pts, mix(color, ctx.color("highlight"), 0.55)[:3] + (210,),
               max(1, int(ctx.S(0.002))))
    brush_line(d, [(x, y + ctx.S(0.0015)) for x, y in edge_pts],
               mix(color, ctx.color("shadow"), 0.4)[:3] + (130,),
               max(1, int(ctx.S(0.0012))))
    img.alpha_composite(layer.filter(ImageFilter.GaussianBlur(0.4)))


def _star_points(cx: float, cy: float, r: float, n: int = 5,
                 inner: float = 0.45, rot: float = -math.pi / 2) -> list[tuple[float, float]]:
    pts = []
    for k in range(n * 2):
        rr = r if k % 2 == 0 else r * inner
        ang = rot + k * math.pi / n
        pts.append((cx + math.cos(ang) * rr, cy + math.sin(ang) * rr))
    return pts


def p_stickers(img: Image.Image, spec: dict, ctx: SceneCtx) -> None:
    """贴纸/彩带（糖果拼贴）：星/心/圆/方，白边 + 旋转。"""
    w, h = ctx.w, ctx.h
    count = max(2, min(80, _i(spec, "count", 14)))
    color = ctx.spec_color(spec, "color", "accent")
    alt = ctx.color("accent", fallback="accent", index=1)
    paper_c = ctx.color("paper")
    rng = ctx.rng_from(f"sticker{ctx.seed}{_i(spec, 'seed', 0)}")
    draw = ImageDraw.Draw(img)
    for _ in range(count):
        cx = ctx.X(rng.uniform(0.03, 0.97))
        cy = ctx.Y(rng.uniform(0.05, 0.95))
        r = ctx.S(rng.uniform(0.012, 0.038))
        c = color if rng.random() < 0.6 else alt
        kind = rng.choice(["star", "heart", "circle", "square", "triangle"])
        # 白边（放大一份）
        if kind == "star":
            poly = _star_points(cx, cy, r * 1.22, rot=rng.uniform(0, math.tau))
            draw.polygon(poly, fill=paper_c[:3] + (245,))
            draw.polygon(_star_points(cx, cy, r, rot=rng.uniform(0, 0)), fill=c[:3] + (250,))
        elif kind == "heart":
            pts = []
            for k in range(24):
                t = k / 24.0 * math.tau
                hx = 16 * math.sin(t) ** 3
                hy = -(13 * math.cos(t) - 5 * math.cos(2 * t)
                       - 2 * math.cos(3 * t) - math.cos(4 * t))
                pts.append((cx + hx * r / 16.0 * 1.3, cy + hy * r / 16.0 * 1.3))
            draw.polygon(pts, fill=paper_c[:3] + (245,))
            draw.polygon([(cx + (x - cx) * 0.8, cy + (y - cy) * 0.8) for x, y in pts],
                         fill=c[:3] + (250,))
        elif kind == "circle":
            draw.ellipse((cx - r * 1.18, cy - r * 1.18, cx + r * 1.18, cy + r * 1.18),
                         fill=paper_c[:3] + (245,))
            draw.ellipse((cx - r, cy - r, cx + r, cy + r), fill=c[:3] + (250,))
        elif kind == "triangle":
            pts = [(cx, cy - r * 1.2), (cx + r * 1.1, cy + r * 0.8),
                   (cx - r * 1.1, cy + r * 0.8)]
            draw.polygon([(cx + (x - cx) * 1.18, cy + (y - cy) * 1.18) for x, y in pts],
                         fill=paper_c[:3] + (245,))
            draw.polygon(pts, fill=c[:3] + (250,))
        else:
            draw.rounded_rectangle((cx - r * 1.2, cy - r * 1.2, cx + r * 1.2, cy + r * 1.2),
                                   radius=int(r * 0.3), fill=paper_c[:3] + (245,))
            draw.rounded_rectangle((cx - r, cy - r, cx + r, cy + r),
                                   radius=int(r * 0.25), fill=c[:3] + (250,))


def p_clay_shapes(img: Image.Image, spec: dict, ctx: SceneCtx) -> None:
    """黏土块（定格动画）：圆润形体 + 高光/暗部 + 指纹弧。"""
    w, h = ctx.w, ctx.h
    count = max(1, min(40, _i(spec, "count", 8)))
    color = ctx.spec_color(spec, "color", "accent")
    alt = ctx.color("paper")
    rng = ctx.rng_from(f"clay{ctx.seed}{_i(spec, 'seed', 0)}")
    draw = ImageDraw.Draw(img)
    for _ in range(count):
        cx = ctx.X(rng.uniform(0.05, 0.95))
        cy = ctx.Y(rng.uniform(0.08, 0.92))
        rx = ctx.S(rng.uniform(0.02, 0.08))
        ry = rx * rng.uniform(0.55, 1.0)
        c = color if rng.random() < 0.55 else alt
        draw.ellipse((cx - rx, cy - ry + ry * 0.12, cx + rx, cy + ry * 1.15),
                     fill=mix(c, ctx.color("shadow"), 0.35)[:3] + (235,))
        draw.ellipse((cx - rx, cy - ry, cx + rx, cy + ry * 1.02),
                     fill=c[:3] + (250,))
        # 高光
        draw.ellipse((cx - rx * 0.60, cy - ry * 0.72, cx + rx * 0.05, cy - ry * 0.02),
                     fill=mix(c, ctx.color("highlight"), 0.55)[:3] + (190,))
        # 暗部
        draw.arc((cx - rx * 0.92, cy - ry * 0.85, cx + rx * 0.92, cy + ry * 0.95),
                 start=20, end=150, fill=mix(c, ctx.color("shadow"), 0.5)[:3] + (120,),
                 width=max(1, int(rx * 0.16)))
        # 指纹弧
        if rng.random() < 0.5:
            draw.arc((cx - rx * 0.4, cy - ry * 0.4, cx + rx * 0.4, cy + ry * 0.4),
                     start=200, end=340, fill=mix(c, ctx.color("shadow"), 0.45)[:3] + (70,),
                     width=1)


def p_chalk_lines(img: Image.Image, spec: dict, ctx: SceneCtx) -> None:
    """粉笔线（粉笔剧场）：毛糙笔触 + 粉尘（前景涂鸦/素描）。"""
    w, h = ctx.w, ctx.h
    color = ctx.spec_color(spec, "color", "highlight")
    rng = ctx.rng_from(f"chalk{ctx.seed}{_i(spec, 'seed', 0)}")
    layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    strokes = max(1, _i(spec, "strokes", 7))
    for _ in range(strokes):
        x0 = rng.uniform(0.06, 0.90) * w
        y0 = rng.uniform(0.10, 0.90) * h
        pts = [(x0, y0)]
        for k in range(1, 5):
            pts.append((x0 + rng.uniform(-0.02, 0.04) * w * k / 4,
                        y0 + rng.uniform(-0.10, 0.10) * h * k / 4))
        pts = chaikin(pts, 2)
        for _pass in range(4):
            jittered = jitter_points(pts, 2.2, rng)
            brush_line(d, jittered, color[:3] + (rng.randint(40, 90),),
                       max(1, int(ctx.S(rng.uniform(0.002, 0.005)))))
    # 粉尘
    for _ in range(_i(spec, "dust", 40)):
        x, y = rng.uniform(0, w), rng.uniform(0, h)
        r = max(0.6, ctx.S(rng.uniform(0.0005, 0.0016)))
        d.ellipse((x - r, y - r, x + r, y + r), fill=color[:3] + (rng.randint(20, 70),))
    img.alpha_composite(layer.filter(ImageFilter.GaussianBlur(0.8)))


def p_cross_section_grid(img: Image.Image, spec: dict, ctx: SceneCtx) -> None:
    """考古剖面（断代/发掘档案）：土层条带 + 剖面网格 + 深度刻度 + 出土点。"""
    w, h = ctx.w, ctx.h
    box = ctx.box(spec, (0.02, 0.16, 0.52, 0.62))
    x0, y0, x1, y1 = box
    bw, bh = x1 - x0, y1 - y0
    if bw < 20 or bh < 20:
        return
    base = ctx.spec_color(spec, "color", "paper")
    dark = mix(base, ctx.color("shadow"), 0.55)
    accent = ctx.color("accent")
    rng = ctx.rng_from(f"section{ctx.seed}{_i(spec, 'seed', 0)}")
    layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    layers = max(3, min(8, _i(spec, "layers", 5)))
    band_h = bh / layers
    for i in range(layers):
        by0 = y0 + i * band_h
        by1 = by0 + band_h
        tone = shade(mix(base, dark, rng.uniform(0.05, 0.55)), rng.uniform(0.92, 1.06))
        pts = [(x0, by0)]
        for k in range(1, 9):  # 波状层界
            pts.append((x0 + bw * k / 8.0, by0 + math.sin(k * 1.9 + i) * band_h * 0.10))
        pts += [(x1, by1), (x0, by1)]
        d.polygon(pts, fill=tone[:3] + (252,))
        # 土质点
        for _ in range(int(bw / 40)):
            sx = x0 + rng.uniform(0, bw)
            sy = by0 + rng.uniform(band_h * 0.15, band_h * 0.9)
            d.ellipse((sx, sy, sx + 2, sy + 2), fill=dark[:3] + (90,))
    # 剖面网格
    for k in range(1, 6):
        gx = x0 + bw * k / 6.0
        d.line([(gx, y0), (gx, y1)], fill=ctx.color("fg")[:3] + (70,), width=1)
    for k in range(1, layers + 1):
        gy = y0 + bh * k / layers
        d.line([(x0, gy), (x1, gy)], fill=ctx.color("fg")[:3] + (55,), width=1)
    # 框
    d.rectangle((x0, y0, x1, y1), outline=dark[:3] + (200,),
                width=max(1, int(ctx.S(0.0018))))
    # 深度刻度 + 标注
    for k in range(layers + 1):
        gy = y0 + bh * k / layers
        d.line([(x0 - ctx.S(0.010), gy), (x0, gy)], fill=dark[:3] + (190,),
               width=max(1, int(ctx.S(0.0014))))
        draw_text(d, (x0 + bw * 0.045, gy - band_h * 0.12),
                  f"{k * (int(spec.get('depth_step', 1)))}m", max(7, int(band_h * 0.22)),
                  ctx.color("fg")[:3] + (200,))
    # 出土标记
    for _ in range(_i(spec, "finds", 5)):
        fx = x0 + rng.uniform(0.08, 0.92) * bw
        fy = y0 + rng.uniform(0.10, 0.90) * bh
        r = max(2.0, bw * 0.010)
        d.ellipse((fx - r, fy - r, fx + r, fy + r),
                  outline=accent[:3] + (230,), width=max(1, int(r * 0.45)))
        d.line([(fx - r * 1.8, fy), (fx + r * 1.8, fy)], fill=accent[:3] + (180,), width=1)
        d.line([(fx, fy - r * 1.8), (fx, fy + r * 1.8)], fill=accent[:3] + (180,), width=1)
    img.alpha_composite(layer)


# ===========================================================================
# 22. 内置 primitive 自动注册（所有 p_* 函数 → primitive 名称 = 去掉 p_ 前缀）
# ===========================================================================
def _register_builtin_primitives() -> int:
    count = 0
    for name in list(globals()):
        if not name.startswith("p_") or name in ("p_", "_p"):
            continue
        fn = globals()[name]
        if callable(fn) and not isinstance(fn, type):
            register(name[2:], fn)
            count += 1
    return count


_register_builtin_primitives()

# --- PRIMITIVES APPEND POINT (由后续批次在此前追加内置 primitive) ---


_load_extensions()


if __name__ == "__main__":  # pragma: no cover
    # 以 python -m pipeline.scene_render 运行时本模块是 __main__，与
    # pipeline.scene_render 是两份实例；队友的 primitive 扩展注册在规范模块
    # （pipeline.scene_render）里，因此 CLI 统一委托给规范模块实例执行。
    from .scene_render import main as _canonical_main
    raise SystemExit(_canonical_main())
