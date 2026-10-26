# -*- coding: utf-8 -*-
"""V5 角色库：角色 spec 加载 + 程序化立绘渲染 + 口型 viseme 时间轴。

契约见 docs/V5_INTERFACES.md §3（函数签名不得改）：
    load_characters(root=None) -> dict
    render_character(char, style, size, pose="front", mouth="closed", blink=False) -> PIL.Image(RGBA)
    mouth_states() -> list[str]
    viseme_from_text(text) -> [{"char","pinyin","viseme"}]
    viseme_timeline(text, duration, lead=0.10) -> [{"t0","t1","viseme"}]
    render_pose_sheet(char, style, out_png, size=(1600,900)) -> dict

设计：
- 纯 PIL/numpy 程序化绘制，不依赖第三方美术素材（无许可问题）、不占 GPU、不联网；
- 外观全部来自 contract/v5/characters/*.json 的 appearance（发型/五官/胡须/服装/配饰/头身比）；
- 风格后处理按 style["character_treatment"]（ink_brush/woodcut/engrave/cutout_paper/clay/pixel/halftone/chalk/toon），
  若 pipeline/style_v5.py 的 apply_finish 已存在则优先调用，否则用内置简化版；
- 单角色渲染目标 ≤0.2s（2x 超采样 + 一次性下采样，numpy 后处理）。
"""
from __future__ import annotations

import hashlib
import json
import math
import random
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

from . import config, utils

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------
REPO_ROOT = Path(config.REPO_ROOT)
V5_ROOT = REPO_ROOT / "contract" / "v5"
CHARACTERS_DIR = V5_ROOT / "characters"
CUSTOM_DIR = V5_ROOT / "custom" / "characters"

MOUTH_STATES: tuple[str, ...] = ("closed", "a", "o", "e", "i", "u", "m")
POSES: tuple[str, ...] = ("front", "side", "point", "think")
DEFAULT_SIZE = (512, 768)

# 景别无关的默认风格（style json 缺失时兜底；风格引擎 style_v5 仍是首选）
DEFAULT_STYLE: dict = {
    "id": "toon_default",
    "label": "基础卡通",
    "technique": ["toon"],
    "palette": {"bg": ["#f2ede4", "#d9d2c4"], "fg": ["#1f1c18", "#3a352c"],
                "accent": ["#b23a2e", "#c9a227"], "paper": "#efe6d3"},
    "line": {"style": "round", "width": 3, "color": "#1f1c18"},
    "character_treatment": "toon",
}

# 风格处理时把角色衣服颜色向纸色/墨色靠拢的比例（让同一角色在不同风格里融入画面）
TREATMENT_TINT = {
    "ink_brush": 0.40, "woodcut": 0.32, "engrave": 0.30, "cutout_paper": 0.12,
    "clay": 0.10, "chalk": 0.30, "halftone": 0.06, "pixel": 0.04,
    "toon": 0.0, "oil": 0.10, "neon_toon": 0.0,
}

# 标点/空白在口型时间轴里的权重（相对一个汉字的时长）
_PUNCT_WEIGHT = {
    "，": 0.65, ",": 0.65, "。": 1.35, ".": 1.35, "！": 1.2, "!": 1.2,
    "？": 1.2, "?": 1.2, "；": 0.9, ";": 0.9, "：": 0.8, ":": 0.8,
    "、": 0.55, "…": 1.0, "—": 0.7, "-": 0.7, " ": 0.35, "\n": 1.6, "\t": 0.35,
    "（": 0.4, "）": 0.4, "(": 0.4, ")": 0.4, "《": 0.4, "》": 0.4,
    "“": 0.4, "”": 0.4, "\"": 0.4, "「": 0.4, "」": 0.4,
}

# pinyin 韵母 → viseme（长韵母优先匹配；声母 b/p/m 归 "m"）
_FINAL_VISEME: tuple[tuple[str, str], ...] = (
    ("iang", "a"), ("uang", "a"), ("iong", "o"), ("ueng", "e"),
    ("ian", "a"), ("iao", "a"), ("uai", "a"), ("ang", "a"), ("eng", "e"),
    ("ing", "i"), ("ong", "o"), ("van", "a"), ("uen", "u"), ("uei", "e"),
    ("ai", "a"), ("ao", "o"), ("an", "a"), ("ou", "u"), ("en", "e"),
    ("er", "e"), ("ei", "e"), ("ia", "a"), ("ie", "e"), ("iu", "u"),
    ("in", "i"), ("ua", "a"), ("uo", "o"), ("ui", "u"), ("un", "u"),
    ("ve", "e"), ("vn", "u"), ("a", "a"), ("o", "o"), ("e", "e"),
    ("i", "i"), ("u", "u"), ("v", "u"), ("n", "e"), ("m", "m"), ("ng", "e"),
)
_INITIAL_VISEME = {"b": "m", "p": "m", "m": "m"}

try:  # 主环境优先 pypinyin；缺失时按字符哈希轮换兜底（契约允许）
    from pypinyin import lazy_pinyin as _lazy_pinyin  # type: ignore

    _HAS_PYPINYIN = True
except Exception:  # noqa: BLE001
    _lazy_pinyin = None  # type: ignore[assignment]
    _HAS_PYPINYIN = False


class V5CharacterError(ValueError):
    """角色 spec 缺失/非法。"""


# ---------------------------------------------------------------------------
# 加载
# ---------------------------------------------------------------------------
def _deep_merge(base: dict, override: dict) -> dict:
    """字典深合并、数组/标量替换（custom 覆盖规则，见 PLAN §4.1）。"""
    out = dict(base or {})
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = value
    return out


def _read_json(path: Path) -> dict:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except OSError as exc:
        raise V5CharacterError(f"无法读取角色文件 {path}: {exc}") from exc
    except ValueError as exc:
        raise V5CharacterError(f"角色 JSON 非法 {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise V5CharacterError(f"角色文件根节点必须是 object: {path}")
    return data


def _character_files(folder: Path) -> dict[str, Path]:
    out: dict[str, Path] = {}
    if not folder.is_dir():
        return out
    for path in sorted(folder.glob("*.json")):
        out[path.stem] = path
    return out


def load_characters(root=None) -> dict:
    """加载 24 个角色 spec（contract/v5/characters + custom 覆盖）。

    root: 自定义角色目录（默认 contract/v5/characters）；
          custom 目录默认 contract/v5/custom/characters（root 指定时用 root 同级 custom/characters）。
    返回 {char_id: spec}，spec 含 id/label/persona/role_pool/voice/appearance/poses/mouth/style_overrides。
    """
    base = Path(root) if root else CHARACTERS_DIR
    if not base.exists():
        raise V5CharacterError(f"角色目录不存在: {base}")
    chars: dict[str, dict] = {}
    for stem, path in _character_files(base).items():
        data = _read_json(path)
        data.setdefault("id", stem)
        cid = str(data.get("id") or stem)
        if cid != stem:
            raise V5CharacterError(f"角色 id {cid!r} 必须与文件名 {stem!r} 一致")
        chars[cid] = data
    custom = (base.parent / "custom" / "characters") if root else CUSTOM_DIR
    for stem, path in _character_files(custom).items():
        overlay = _read_json(path)
        overlay.setdefault("id", stem)
        if stem in chars:
            chars[stem] = _deep_merge(chars[stem], overlay)
        else:  # custom 新角色：视为完整 spec
            chars[stem] = overlay
    if not chars:
        raise V5CharacterError(f"角色目录为空: {base}")
    return chars


def get_character(char, root=None) -> dict:
    """char 既可以是 spec dict，也可以是角色 id。"""
    if isinstance(char, dict):
        return char
    if isinstance(char, str):
        chars = load_characters(root)
        if char not in chars:
            raise V5CharacterError(f"未知角色: {char}")
        return chars[char]
    raise V5CharacterError(f"角色参数必须是 dict 或 id: {type(char)}")


def default_style() -> dict:
    return json.loads(json.dumps(DEFAULT_STYLE))


# ---------------------------------------------------------------------------
# 颜色 / 风格 / 字体
# ---------------------------------------------------------------------------
def _hx(color, default="#888888") -> tuple[int, int, int, int]:
    """hex / (r,g,b) / (r,g,b,a) → RGBA tuple（内部 _mix/_shade 大量接收已转换的 tuple）。"""
    if (isinstance(color, (tuple, list))
            or (not isinstance(color, str) and hasattr(color, "__len__")
                and hasattr(color, "__getitem__"))) and len(color) >= 3:
        try:
            rgba = [int(color[0]), int(color[1]), int(color[2])]
            rgba.append(int(color[3]) if len(color) > 3 else 255)
            return (max(0, min(255, rgba[0])), max(0, min(255, rgba[1])),
                    max(0, min(255, rgba[2])), max(0, min(255, rgba[3])))
        except (TypeError, ValueError):
            return _hx(default)
    text = str(color or "").strip()
    if text.startswith("#"):
        text = text[1:]
    if len(text) == 3:
        text = "".join(ch * 2 for ch in text)
    try:
        return (int(text[0:2], 16), int(text[2:4], 16), int(text[4:6], 16), 255)
    except (ValueError, IndexError):
        return _hx(default)


def _mix(c1, c2, t: float):
    a, b = _hx(c1), _hx(c2)
    t = max(0.0, min(1.0, float(t)))
    return tuple(int(round(a[i] * (1 - t) + b[i] * t)) for i in range(3)) + (255,)


def _shade(c, t: float):
    """t>0 变亮，t<0 变暗。"""
    base = _hx(c)
    target = (255, 255, 255, 255) if t > 0 else (0, 0, 0, 255)
    return _mix(base, target, abs(t))


def _alpha(c, a: int):
    r, g, b, _ = _hx(c)
    return (r, g, b, max(0, min(255, int(a))))


def _style_palette(style: dict) -> dict:
    style = style if isinstance(style, dict) else {}
    palette = style.get("palette") if isinstance(style.get("palette"), dict) else {}

    def _first(key, default):
        value = palette.get(key)
        if isinstance(value, (list, tuple)) and value:
            return str(value[0])
        if isinstance(value, str) and value:
            return value
        return default

    return {
        "bg": _first("bg", "#f2ede4"),
        "fg": _first("fg", "#1f1c18"),
        "accent": _first("accent", "#b23a2e"),
        "paper": str(palette.get("paper") or _first("bg", "#efe6d3")),
    }


def _style_line(style: dict, char: dict, key: str = "width", default: float = 3.0):
    style = style or {}
    line = style.get("line") if isinstance(style.get("line"), dict) else {}
    value = line.get(key, default)
    cid = str(char.get("id") or "")
    overrides = char.get("style_overrides") if isinstance(char.get("style_overrides"), dict) else {}
    sid = str(style.get("id") or "")
    per = overrides.get(sid) if isinstance(overrides.get(sid), dict) else {}
    if key == "width" and "line" in per:
        value = float(value) * float(per["line"])
    if key == "color":
        value = per.get("line_color", value)
    return value


def _treatment(style: dict) -> str:
    style = style or {}
    value = str(style.get("character_treatment") or "").strip().lower()
    return value or "toon"


def _tinted(color, style: dict, key: bool = False):
    """按风格把颜色向纸色靠拢（亮度自适应，保身份）。

    - key=True（hair / outfit main / accent / pants 等身份主色）：纸色融合 ≤0.12，
      深色/紫色/黑色不再被洗白（修「老周白发 / 阿栗浅紫帽 / 大魏灰西装」）；
    - 普通色：暗部（L<0.35）融合量 ×0.12（保留 ≥85% 原色），中间调 ×0.55，亮部保持原量。
    """
    treatment = _treatment(style)
    amount = TREATMENT_TINT.get(treatment, 0.0)
    if not amount:
        return _hx(color)
    palette = _style_palette(style)
    base = _hx(color)
    lum = (0.299 * base[0] + 0.587 * base[1] + 0.114 * base[2]) / 255.0
    if key:
        amount = min(amount * 0.25, 0.12)
    elif lum < 0.35:
        amount *= 0.12
    elif lum < 0.60:
        amount *= 0.55
    if amount <= 0.001:
        return base
    return _mix(base, palette["paper"], amount)


_FONT_CACHE: dict[tuple[str, int], ImageFont.FreeTypeFont] = {}


def _font_path(bold: bool = False) -> Path | None:
    candidates = list(getattr(config, "FONT_CANDIDATES", ()))
    ordered = []
    if bold:
        ordered += [c for c in candidates if "msyhbd" in str(c[0]).lower()]
    ordered += candidates
    for src, _family in ordered:
        try:
            path = Path(src)
            if path.is_file():
                return path
        except OSError:
            continue
    return None


def _font(size: int, bold: bool = False) -> ImageFont.ImageFont:
    size = max(8, int(size))
    key = ("b" if bold else "r", size)
    cached = _FONT_CACHE.get(key)
    if cached is not None:
        return cached
    path = _font_path(bold)
    try:
        font = ImageFont.truetype(str(path), size) if path else ImageFont.load_default()
    except OSError:
        font = ImageFont.load_default()
    _FONT_CACHE[key] = font
    return font


# ---------------------------------------------------------------------------
# 几何：把角色外观换算成画布坐标
# ---------------------------------------------------------------------------
def _geometry(appearance: dict, pose: str, W: int, H: int) -> dict:
    ap = appearance or {}
    chibi = max(0.0, min(0.5, float(ap.get("chibi") or 0.0)))
    head_ratio = max(0.7, min(1.6, float(ap.get("head_ratio") or 1.0)))
    body_width = max(0.7, min(1.6, float(ap.get("body_width") or 1.0)))
    gender = str(ap.get("gender") or "male").lower()
    outfit_type = str((ap.get("outfit") or {}).get("type") or "").lower()
    is_robot = gender == "robot" or outfit_type == "robot"
    shape_gender = "male" if is_robot else gender

    figure_h = H * 0.90
    feet_y = H * 0.965
    heads = max(3.3, min(5.6, 5.6 - 3.0 * chibi)) / head_ratio
    head_h = figure_h / max(3.4, heads)
    head_h = max(figure_h * 0.10, min(figure_h * 0.30, head_h))
    head_w = head_h * (1.02 if is_robot else (0.90 if shape_gender == "female" else 0.95))
    top_y = feet_y - figure_h
    head_cy = top_y + head_h * 0.52
    neck_h = head_h * 0.10
    shoulder_y = top_y + head_h + neck_h
    body_h = feet_y - shoulder_y
    shoulder_w = head_w * (1.9 if shape_gender == "male" else (1.55 if shape_gender == "female" else 1.7)) * body_width
    waist_w = shoulder_w * (0.64 if shape_gender == "male" else 0.54)
    hip_w = shoulder_w * (0.78 if shape_gender == "male" else 0.72)
    hip_y = shoulder_y + body_h * 0.46
    cx = W * 0.5
    shift = 0.0
    if pose == "side":
        shift = head_w * 0.045
        shoulder_w *= 0.80
        hip_w *= 0.84
    elif pose == "point":
        shift = head_w * 0.04
    elif pose == "think":
        shift = -head_w * 0.05
    cx += shift
    return {
        "W": W, "H": H, "cx": cx, "head_cx": cx + (head_w * 0.10 if pose == "side" else 0.0),
        "feet_y": feet_y, "figure_h": figure_h, "head_h": head_h, "head_w": head_w,
        "head_cy": head_cy, "head_top": head_cy - head_h / 2, "shoulder_y": shoulder_y,
        "body_h": body_h, "shoulder_w": shoulder_w, "waist_w": waist_w, "hip_w": hip_w,
        "hip_y": hip_y, "gender": gender, "is_robot": is_robot, "pose": pose,
        "neck_h": neck_h, "head_ratio": head_ratio, "chibi": chibi,
    }


def _eyes_geometry(g: dict, pose: str) -> dict:
    head_w, head_h, head_cy = g["head_w"], g["head_h"], g["head_cy"]
    head_cx = g["head_cx"]
    eye_y = head_cy - head_h * 0.04
    eye_dx = head_w * 0.21
    eye_w = head_w * 0.17
    eye_h = head_h * 0.085
    if pose == "side":  # 3/4 侧脸：五官整体偏向一侧
        off = head_w * 0.17
        return {"eye_y": eye_y, "left": (head_cx - eye_dx * 0.35 + off, eye_y),
                "right": (head_cx + eye_dx + off * 0.6, eye_y),
                "eye_w": eye_w * 0.92, "eye_h": eye_h, "single": True,
                "feature_dx": off}
    feature_dx = head_w * 0.05 if pose == "think" else 0.0
    return {"eye_y": eye_y + (head_h * 0.015 if pose == "think" else 0.0),
            "left": (head_cx - eye_dx + feature_dx, eye_y),
            "right": (head_cx + eye_dx + feature_dx, eye_y),
            "eye_w": eye_w, "eye_h": eye_h, "single": False, "feature_dx": feature_dx}


def _hand_positions(g: dict) -> dict:
    cx, shoulder_y = g["cx"], g["shoulder_y"]
    shoulder_w, body_h = g["shoulder_w"], g["body_h"]
    pose = g["pose"]
    left = (cx - shoulder_w * 0.60, shoulder_y + body_h * 0.55)
    right = (cx + shoulder_w * 0.60, shoulder_y + body_h * 0.55)
    if pose == "point":
        right = (cx + shoulder_w * 1.30, shoulder_y + body_h * 0.18)
    elif pose == "think":
        left = (cx - g["head_w"] * 0.52, g["head_cy"] + g["head_h"] * 0.30)
    elif pose == "side":
        left = (cx - shoulder_w * 0.30, shoulder_y + body_h * 0.58)
        right = (cx + shoulder_w * 0.42, shoulder_y + body_h * 0.52)
    return {"left": left, "right": right}


# ---------------------------------------------------------------------------
# 小工具（PIL 绘制）
# ---------------------------------------------------------------------------
def _norm_box(box):
    """归一化矩形（防止 x1<x0 / y1<y0 触发 PIL ValueError）。"""
    x0, y0, x1, y1 = (float(v) for v in box)
    return (min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1))


def _ellipse(draw, box, fill=None, outline=None, width=0):
    b = _norm_box(box)
    draw.ellipse([int(v) for v in b], fill=fill, outline=outline, width=max(0, int(width)))


def _polygon(draw, pts, fill=None, outline=None, width=1):
    pts = [(int(round(x)), int(round(y))) for x, y in pts]
    if len(pts) < 3:
        return
    draw.polygon(pts, fill=fill)
    if outline and width:
        draw.line(pts + [pts[0]], fill=outline, width=max(1, int(width)), joint="curve")


def _rounded_rect(draw, box, radius, fill=None, outline=None, width=1):
    b = _norm_box(box)
    draw.rounded_rectangle([int(v) for v in b], radius=max(1, int(radius)),
                           fill=fill, outline=outline, width=max(1, int(width)))


def _line(draw, pts, color, width):
    pts = [(int(round(x)), int(round(y))) for x, y in pts]
    if len(pts) >= 2:
        draw.line(pts, fill=color, width=max(1, int(width)), joint="curve")


# ---------------------------------------------------------------------------
# 发型
# ---------------------------------------------------------------------------
def _hair_color(ap, style):
    return _tinted(ap.get("hair") or "#1a1a1a", style, key=True)


def _draw_hair_back(canvas, draw, g, ap, style, hair, line_c, lw):
    hs = str(ap.get("hair_style") or "short_swept")
    hc = g["head_cy"], g["head_w"], g["head_h"]
    head_cy, head_w, head_h = hc
    cx = g["head_cx"]
    long_styles = {"long_loose", "high_chignon", "scholar_bun", "twin_tail", "ponytail",
                   "twin_bun", "long_bun", "waist_long"}
    if hs in long_styles:
        top = head_cy - head_h * 0.35
        bottom = g["shoulder_y"] + g["body_h"] * (0.42 if hs == "waist_long" else 0.25)
        w = head_w * 1.28
        _polygon(draw, [(cx - w * 0.5, top), (cx + w * 0.5, top),
                        (cx + w * 0.42, bottom), (cx - w * 0.42, bottom)],
                 fill=hair, outline=line_c, width=lw)
    if hs == "twin_tail":
        for side in (-1, 1):
            x = cx + side * head_w * 0.62
            _ellipse(draw, (x - head_w * 0.16, head_cy + head_h * 0.05,
                            x + head_w * 0.16, head_cy + head_h * 0.75),
                     fill=hair, outline=line_c, width=lw)
    if hs == "ponytail":
        _polygon(draw, [(cx + head_w * 0.42, head_cy - head_h * 0.1),
                        (cx + head_w * 0.78, head_cy + head_h * 0.25),
                        (cx + head_w * 0.62, head_cy + head_h * 0.95),
                        (cx + head_w * 0.38, head_cy + head_h * 0.60)],
                 fill=hair, outline=line_c, width=lw)
    if hs == "curly":
        random.seed(int(hashlib.md5(str(cx).encode()).hexdigest()[:6], 16))
        for _ in range(9):
            rx = cx + random.uniform(-head_w * 0.55, head_w * 0.55)
            ry = head_cy + random.uniform(-head_h * 0.45, head_h * 0.15)
            r = head_w * random.uniform(0.13, 0.22)
            _ellipse(draw, (rx - r, ry - r, rx + r, ry + r), fill=hair, outline=line_c, width=lw)


def _draw_hair_front(draw, g, ap, style, hair, line_c, lw):
    hs = str(ap.get("hair_style") or "short_swept")
    cx, head_cy = g["head_cx"], g["head_cy"]
    head_w, head_h = g["head_w"], g["head_h"]
    top = head_cy - head_h * 0.52
    if hs in ("bald",):
        _line(draw, [(cx - head_w * 0.5, head_cy - head_h * 0.02),
                     (cx - head_w * 0.25, head_cy + head_h * 0.05)], hair, lw)
        _line(draw, [(cx + head_w * 0.5, head_cy - head_h * 0.02),
                     (cx + head_w * 0.25, head_cy + head_h * 0.05)], hair, lw)
        return
    if hs in ("robot_antenna", "hood", "veil"):
        return
    # 基础发盖（头顶覆盖）
    cap_bottom = head_cy - head_h * (0.10 if hs in ("long_loose", "high_chignon")
                                     else 0.16)
    if hs in ("twin_bun", "high_chignon"):
        cap_bottom = head_cy - head_h * 0.18
    _polygon(draw, [(cx - head_w * 0.50, head_cy - head_h * 0.08),
                    (cx - head_w * 0.48, top + head_h * 0.10),
                    (cx - head_w * 0.22, top),
                    (cx + head_w * 0.22, top),
                    (cx + head_w * 0.48, top + head_h * 0.10),
                    (cx + head_w * 0.50, cap_bottom),
                    (cx + head_w * 0.30, cap_bottom - head_h * 0.05),
                    (cx - head_w * 0.30, cap_bottom - head_h * 0.05)],
             fill=hair, outline=line_c, width=lw)
    # 刘海 / 分缝
    if hs in ("short_swept", "neat_short", "under_cut", "crew_short", "slick_back",
              "bob", "ancient_guan", "topknot", "scholar_bun", "low_bun", "long_bun",
              "twin_tail", "ponytail", "long_loose"):
        sweep = -1 if hs in ("short_swept", "slick_back") else 1
        _line(draw, [(cx + sweep * head_w * 0.05, top + head_h * 0.02),
                     (cx + sweep * head_w * 0.35, head_cy - head_h * 0.22),
                     (cx + sweep * head_w * 0.42, head_cy - head_h * 0.10)], line_c, lw)
        if hs == "bob":
            _polygon(draw, [(cx - head_w * 0.52, head_cy - head_h * 0.05),
                            (cx - head_w * 0.48, head_cy + head_h * 0.32),
                            (cx - head_w * 0.30, head_cy + head_h * 0.34),
                            (cx - head_w * 0.38, head_cy)],
                     fill=hair, outline=line_c, width=lw)
    if hs == "twin_bun":
        for side in (-1, 1):
            bx = cx + side * head_w * 0.42
            _ellipse(draw, (bx - head_w * 0.20, top - head_h * 0.10,
                            bx + head_w * 0.20, top + head_h * 0.24),
                     fill=hair, outline=line_c, width=lw)
    if hs == "high_chignon":
        _ellipse(draw, (cx - head_w * 0.26, top - head_h * 0.28,
                        cx + head_w * 0.26, top + head_h * 0.18),
                 fill=hair, outline=line_c, width=lw)
        _line(draw, [(cx - head_w * 0.22, head_cy - head_h * 0.30),
                     (cx + head_w * 0.22, head_cy - head_h * 0.30)], line_c, lw)
    if hs in ("topknot", "scholar_bun", "long_bun"):
        r = head_w * (0.20 if hs == "topknot" else 0.16)
        _ellipse(draw, (cx - r, top - r * 0.9, cx + r, top + r), fill=hair,
                 outline=line_c, width=lw)
        _line(draw, [(cx - r * 0.7, top + r * 0.2), (cx + r * 0.7, top + r * 0.2)], line_c, lw)
    if hs == "long_loose":
        for side in (-1, 1):
            _polygon(draw, [(cx + side * head_w * 0.46, head_cy - head_h * 0.10),
                            (cx + side * head_w * 0.60, head_cy + head_h * 0.40),
                            (cx + side * head_w * 0.40, head_cy + head_h * 0.44),
                            (cx + side * head_w * 0.34, head_cy + head_h * 0.02)],
                     fill=hair, outline=line_c, width=lw)
    if hs not in ("bald", "robot_antenna", "hood", "veil"):
        _line(draw, [(cx - head_w * 0.30, top + head_h * 0.10),
                     (cx - head_w * 0.05, top + head_h * 0.04),
                     (cx + head_w * 0.20, top + head_h * 0.12)],
              _mix(hair, "#ffffff", 0.28), max(1, int(lw * 0.8)))
    if hs == "hood":
        _polygon(draw, [(cx - head_w * 0.62, head_cy + head_h * 0.55),
                        (cx - head_w * 0.56, head_cy - head_h * 0.55),
                        (cx, head_cy - head_h * 0.78),
                        (cx + head_w * 0.56, head_cy - head_h * 0.55),
                        (cx + head_w * 0.62, head_cy + head_h * 0.55),
                        (cx, head_cy + head_h * 0.30)],
                 fill=hair, outline=line_c, width=lw)


# ---------------------------------------------------------------------------
# 帽子 / 头饰
# ---------------------------------------------------------------------------
def _draw_hat(draw, g, ap, style, outfit_color, accent, line_c, lw):
    outfit = ap.get("outfit") or {}
    hat = str(outfit.get("hat") or ap.get("hat") or "").strip().lower()
    if hat in ("", "none"):
        return
    cx, head_cy = g["head_cx"], g["head_cy"]
    head_w, head_h = g["head_w"], g["head_h"]
    top = head_cy - head_h * 0.55
    dark = _mix(outfit_color, "#141414", 0.65)
    if hat in ("lun_jin", "scholar_cap"):
        _polygon(draw, [(cx - head_w * 0.42, top + head_h * 0.05),
                        (cx - head_w * 0.30, top - head_h * 0.22),
                        (cx + head_w * 0.30, top - head_h * 0.22),
                        (cx + head_w * 0.42, top + head_h * 0.05)],
                 fill=_mix(outfit_color, "#ffffff", 0.25), outline=line_c, width=lw)
        for side in (-1, 1):  # 纶巾两条飘带
            _line(draw, [(cx + side * head_w * 0.40, top + head_h * 0.02),
                         (cx + side * head_w * 0.50, top + head_h * 0.36),
                         (cx + side * head_w * 0.58, top + head_h * 0.62)], line_c, lw)
    elif hat in ("futou", "official_hat", "eunuch_hat"):
        _rounded_rect(draw, (cx - head_w * 0.36, top - head_h * 0.18,
                             cx + head_w * 0.36, top + head_h * 0.06),
                      head_w * 0.09, fill=dark, outline=line_c, width=lw)
        wing = head_w * (0.72 if hat != "eunuch_hat" else 0.45)
        for side in (-1, 1):
            _line(draw, [(cx + side * head_w * 0.30, top + head_h * 0.01),
                         (cx + side * wing, top - head_h * 0.06)], dark, max(lw, 3))
        if hat == "official_hat":
            _polygon(draw, [(cx - head_w * 0.30, top - head_h * 0.16),
                            (cx + head_w * 0.30, top - head_h * 0.16),
                            (cx + head_w * 0.20, top - head_h * 0.40),
                            (cx - head_w * 0.20, top - head_h * 0.40)],
                     fill=dark, outline=line_c, width=lw)
            _ellipse(draw, (cx - head_w * 0.06, top - head_h * 0.46,
                            cx + head_w * 0.06, top - head_h * 0.34), fill=accent)
    elif hat in ("crown", "emperor_crown"):
        _rounded_rect(draw, (cx - head_w * 0.30, top - head_h * 0.40,
                             cx + head_w * 0.30, top - head_h * 0.20),
                      max(2, head_w * 0.03), fill="#1a1a1a", outline=accent, width=max(2, lw))
        _line(draw, [(cx - head_w * 0.34, top - head_h * 0.36),
                     (cx + head_w * 0.34, top - head_h * 0.36)], accent, max(lw, 2))
        for i in range(-2, 3):  # 冕旒
            _line(draw, [(cx + i * head_w * 0.11, top - head_h * 0.21),
                         (cx + i * head_w * 0.11, head_cy - head_h * 0.32)], accent, max(1, lw - 1))
    elif hat == "phoenix_crown":
        _polygon(draw, [(cx - head_w * 0.38, top - head_h * 0.02),
                        (cx - head_w * 0.30, top - head_h * 0.34),
                        (cx, top - head_h * 0.52),
                        (cx + head_w * 0.30, top - head_h * 0.34),
                        (cx + head_w * 0.38, top - head_h * 0.02)],
                 fill=accent, outline=line_c, width=lw)
        for i in range(-2, 3):
            _ellipse(draw, (cx + i * head_w * 0.16 - head_w * 0.04,
                            top - head_h * 0.40,
                            cx + i * head_w * 0.16 + head_w * 0.04,
                            top - head_h * 0.28), fill="#d94f8a")
    elif hat in ("helmet", "war_helmet"):
        _ellipse(draw, (cx - head_w * 0.52, top - head_h * 0.30,
                        cx + head_w * 0.52, top + head_h * 0.50),
                 fill=_mix(outfit_color, "#333333", 0.4), outline=line_c, width=lw)
        _polygon(draw, [(cx - head_w * 0.56, top + head_h * 0.02),
                        (cx + head_w * 0.56, top + head_h * 0.02),
                        (cx + head_w * 0.44, top + head_h * 0.16),
                        (cx - head_w * 0.44, top + head_h * 0.16)],
                 fill=_mix(outfit_color, "#111111", 0.35), outline=line_c, width=lw)
        _line(draw, [(cx - head_w * 0.06, top - head_h * 0.30),
                     (cx - head_w * 0.06, head_cy - head_h * 0.05)], line_c, max(lw, 2))
        _polygon(draw, [(cx, top - head_h * 0.38), (cx + head_w * 0.30, top - head_h * 0.60),
                        (cx + head_w * 0.12, top - head_h * 0.30)],
                 fill=_mix(outfit_color, "#d94f4f", 0.55))
    elif hat in ("headband", "band"):
        _line(draw, [(cx - head_w * 0.50, head_cy - head_h * 0.24),
                     (cx + head_w * 0.50, head_cy - head_h * 0.24)], accent, max(lw, 3))
    elif hat in ("fur_hat", "traveler_hat"):
        _polygon(draw, [(cx - head_w * 0.55, top + head_h * 0.02),
                        (cx - head_w * 0.36, top - head_h * 0.34),
                        (cx + head_w * 0.36, top - head_h * 0.34),
                        (cx + head_w * 0.55, top + head_h * 0.02)],
                 fill=_mix(outfit_color, "#6b5236", 0.45), outline=line_c, width=lw)
        for i in range(-4, 5):
            _line(draw, [(cx + i * head_w * 0.10, top - head_h * 0.30),
                         (cx + i * head_w * 0.11, top)], _mix(outfit_color, "#f0e0c0", 0.4),
                  max(1, lw - 1))
    elif hat == "king_hat":
        _rounded_rect(draw, (cx - head_w * 0.34, top - head_h * 0.30,
                             cx + head_w * 0.34, top + head_h * 0.02),
                      head_w * 0.05, fill=dark, outline=accent, width=max(2, lw))
        _line(draw, [(cx - head_w * 0.30, top - head_h * 0.24),
                     (cx + head_w * 0.30, top - head_h * 0.24)], accent, max(lw, 2))


# ---------------------------------------------------------------------------
# 胡须
# ---------------------------------------------------------------------------
def _draw_beard(draw, g, ap, style, hair_c, line_c, lw):
    """胡须：小型化并留出嘴部空隙（口型必须可见），细分 moustache/下巴/长髯/编髯。"""
    beard = str(ap.get("beard") or "none").strip().lower()
    if beard in ("", "none"):
        return
    cx, head_cy = g["head_cx"], g["head_cy"]
    head_w, head_h = g["head_w"], g["head_h"]
    mouth_y = head_cy + head_h * 0.285
    bc = _mix(hair_c, "#f5f0e8", 0.08)
    chin_y = head_cy + head_h * 0.52
    lw_b = max(1, int(lw - 1))
    if beard == "stubble":
        rng = random.Random(int(hashlib.md5(str(round(cx)).encode()).hexdigest()[:6], 16))
        for _ in range(34):
            sx = cx + rng.uniform(-head_w * 0.34, head_w * 0.34)
            sy = head_cy + rng.uniform(head_h * 0.33, head_h * 0.52)
            if abs(sx - cx) < head_w * 0.14 and abs(sy - mouth_y) < head_h * 0.06:
                continue
            _ellipse(draw, (sx - 1.7, sy - 1.7, sx + 1.7, sy + 1.7), fill=_mix(bc, "#f0d8c0", 0.45))
        return
    # 八字胡：嘴角两侧（不盖嘴）
    if beard in ("moustache", "short", "full", "long_goatee", "braided"):
        for side in (-1, 1):
            _polygon(draw, [(cx + side * head_w * 0.06, mouth_y - head_h * 0.10),
                            (cx + side * head_w * 0.20, mouth_y - head_h * 0.14),
                            (cx + side * head_w * 0.22, mouth_y - head_h * 0.05),
                            (cx + side * head_w * 0.08, mouth_y - head_h * 0.02)],
                     fill=bc, outline=line_c, width=lw_b)
    # 下巴胡：从嘴下 0.10 头高开始，不再压嘴
    if beard in ("short", "full", "long_goatee", "braided"):
        chin_top = mouth_y + head_h * 0.10
        half = head_w * {"short": 0.16, "full": 0.26, "long_goatee": 0.19,
                         "braided": 0.15}[beard]
        chin_bottom = chin_y + head_h * {"short": 0.02, "full": 0.04,
                                         "long_goatee": 0.06, "braided": 0.08}[beard]
        _polygon(draw, [(cx - half, chin_top), (cx - half * 0.6, chin_bottom),
                        (cx, chin_bottom + head_h * 0.03),
                        (cx + half * 0.6, chin_bottom), (cx + half, chin_top),
                        (cx, chin_top + head_h * 0.04)],
                 fill=bc, outline=line_c, width=lw_b)
        for i in range(3):  # 须丝
            sx = cx + (i - 1) * half * 0.45
            _line(draw, [(sx, chin_top + head_h * 0.02),
                         (sx * 1.02, chin_bottom - head_h * 0.01)], _mix("#e8e0d0", bc, 0.45), 1)
    # 长髯：细而长（到上胸）
    if beard == "long_goatee":
        top = mouth_y + head_h * 0.16
        bottom = head_cy + head_h * 0.95
        _polygon(draw, [(cx - head_w * 0.09, top), (cx - head_w * 0.12, bottom - head_h * 0.06),
                        (cx, bottom), (cx + head_w * 0.12, bottom - head_h * 0.06),
                        (cx + head_w * 0.09, top)],
                 fill=bc, outline=line_c, width=lw_b)
        for i in range(3):
            _line(draw, [(cx + (i - 1) * head_w * 0.045, top + head_h * 0.10),
                         (cx + (i - 1) * head_w * 0.055, bottom - head_h * 0.10)],
                  _mix("#e8e0d0", bc, 0.40), 1)
    if beard == "braided":
        _polygon(draw, [(cx - head_w * 0.06, chin_y), (cx - head_w * 0.08, chin_y + head_h * 0.36),
                        (cx + head_w * 0.08, chin_y + head_h * 0.36), (cx + head_w * 0.06, chin_y)],
                 fill=bc, outline=line_c, width=lw_b)
        for i in range(3):
            y = chin_y + head_h * (0.08 + i * 0.09)
            _line(draw, [(cx - head_w * 0.07, y), (cx + head_w * 0.07, y)], line_c, 1)


# ---------------------------------------------------------------------------
# 脸
# ---------------------------------------------------------------------------

def _face_params(ap, style):
    """外观 → 五官参数（眼型/年龄/标记/脸宽），保证 24 角色脸部可辨。"""
    age = int(ap.get("age") or 30)
    gender = str(ap.get("gender") or "male").lower()
    marks = {str(m).lower() for m in (ap.get("marks") or [])}
    return {
        "age": age, "gender": gender, "marks": marks,
        "eye_style": str(ap.get("eye_style") or "steady").lower(),
        "face_ratio": max(0.82, min(1.22, float(ap.get("face_ratio") or 1.0))),
        "female": gender == "female",
        "old": age >= 45,
        "young": age <= 26,
        "eyelash": gender == "female" or "eyelash" in marks,
        "cheek": gender == "female" or float(ap.get("chibi") or 0) > 0.28,
    }


def _draw_eye(draw, cx, cy, w, h, fp, eye_color, line_c, lw):
    """单只眼睛：眼白 + 虹膜 + 瞳孔 + 高光 + 上睑 + 睫毛/眼袋。"""
    style_eye = fp["eye_style"]
    lw_real = max(1, int(lw))
    if style_eye == "smiley":
        _line(draw, [(cx - w * 0.50, cy + h * 0.10), (cx, cy - h * 0.38),
                     (cx + w * 0.50, cy + h * 0.10)], line_c, max(2, int(lw * 1.5)))
        return
    if style_eye == "deadpan":
        _line(draw, [(cx - w * 0.50, cy), (cx + w * 0.50, cy)], line_c, max(2, int(lw * 1.3)))
        _ellipse(draw, (cx - w * 0.10, cy - h * 0.18, cx + w * 0.10, cy + h * 0.18),
                 fill=eye_color)
        return
    big = style_eye in ("big", "round_big", "bright")
    upper_dy = 0.62 if big else 0.46
    lower_dy = 0.55 if big else 0.38
    tilt = {"sharp": -0.30, "narrow": -0.12, "cold": -0.20, "phoenix": -0.36,
            "tired": 0.14, "determined": -0.22, "warm": 0.05}.get(style_eye, 0.0)
    inner = (cx - w * 0.5, cy - h * tilt * 0.4)
    outer = (cx + w * 0.5, cy + h * tilt * 0.4)
    pts = [inner,
           (cx - w * 0.20, cy - h * upper_dy),
           (cx + w * 0.18, cy - h * (upper_dy + 0.06)),
           outer,
           (cx + w * 0.16, cy + h * lower_dy),
           (cx - w * 0.22, cy + h * (lower_dy - 0.04))]
    _polygon(draw, pts, fill="#fbf7f0", outline=line_c, width=lw_real)
    iris = min(w, h) * (0.34 if big else 0.30)
    _ellipse(draw, (cx - iris, cy - iris * 1.05, cx + iris, cy + iris * 1.05), fill=eye_color)
    _ellipse(draw, (cx - iris * 0.48, cy - iris * 0.52, cx + iris * 0.42, cy + iris * 0.12),
             fill="#141414")
    _ellipse(draw, (cx + iris * 0.10, cy - iris * 0.72, cx + iris * 0.62, cy - iris * 0.18),
             fill="#ffffff")
    _line(draw, pts[:4], line_c, max(2, int(lw * 1.6)))
    if fp["eyelash"]:
        _line(draw, [(outer[0] - w * 0.02, outer[1] - h * 0.06),
                     (outer[0] + w * 0.16, outer[1] - h * 0.34)], line_c, max(1, lw_real))
        _line(draw, [(cx + w * 0.08, cy - h * upper_dy - h * 0.04),
                     (cx + w * 0.13, cy - h * upper_dy - h * 0.30)], line_c, max(1, lw_real))
    if style_eye == "tired" or fp["old"]:
        _line(draw, [(inner[0] + w * 0.12, cy + h * lower_dy + h * 0.20),
                     (outer[0] - w * 0.12, cy + h * lower_dy + h * 0.26)], _mix(line_c, "#f0d8c0", 0.62),
              max(1, lw_real))


def _draw_nose(draw, cx, top_y, length, width, skin, line_c, lw):
    shadow = _mix(skin, line_c, 0.30)
    _line(draw, [(cx, top_y), (cx - width * 0.10, top_y + length * 0.78)], shadow,
          max(1, int(lw)))
    _line(draw, [(cx - width * 0.30, top_y + length),
                 (cx + width * 0.30, top_y + length)], _mix(skin, line_c, 0.45),
          max(1, int(lw)))
    _ellipse(draw, (cx - width * 0.16, top_y + length * 0.86,
                    cx + width * 0.16, top_y + length * 1.06), fill=_mix(skin, line_c, 0.12))


def _draw_think_hand(draw, g, ap, style, line_c, lw):
    """think 姿态：前臂从肘部抬到下巴，手托腮（画在脸前）。"""
    hands = _hand_positions(g)
    hx, hy = hands["left"]
    cx, shoulder_y = g["cx"], g["shoulder_y"]
    body_h = g["body_h"]
    skin = _tinted(ap.get("skin") or "#f0d8c0", style)
    sleeve = _tinted(str((ap.get("outfit") or {}).get("main") or "#4a4f57"), style)
    elbow = (cx - g["shoulder_w"] * 0.34, shoulder_y + body_h * 0.30)
    _line(draw, [elbow, (hx - g["head_w"] * 0.06, hy + g["head_h"] * 0.10)], sleeve,
          max(4, int(g["shoulder_w"] * 0.17)))
    _ellipse(draw, (hx - g["hand_r"] * 1.25, hy - g["hand_r"] * 1.05,
                    hx + g["hand_r"] * 1.25, hy + g["hand_r"] * 1.25),
             fill=skin, outline=line_c, width=max(1, int(lw)))
    _line(draw, [(hx, hy - g["hand_r"] * 0.4), (hx + g["hand_r"] * 0.8, hy - g["hand_r"] * 0.9)],
          _mix(skin, line_c, 0.35), max(1, int(lw)))


def _draw_face(draw, g, ap, pose, mouth, blink, style, skin, line_c, lw):
    fp = _face_params(ap, style)
    eyes = _eyes_geometry(g, pose)
    head_w, head_h = g["head_w"], g["head_h"]
    head_cy = g["head_cy"]
    head_cx = g["head_cx"]
    eyes_c = _tinted(ap.get("eye") or "#3a2a22", style)
    eye_w = eyes["eye_w"] * (1.10 if fp["eye_style"] in ("big", "round_big", "bright") else 1.0)
    eye_h = eyes["eye_h"] * (1.15 if fp["eye_style"] in ("big", "round_big", "bright") else 1.0)
    brow = str(ap.get("brow") or "soft").lower()
    lw_face = max(1, int(lw * 1.05))
    profile = pose == "side"
    for key in ("left", "right"):
        if eyes["single"] and key == "left":
            continue
        ex, ey = eyes[key]
        if blink:
            _line(draw, [(ex - eye_w * 0.62, ey + eye_h * 0.08),
                         (ex, ey - eye_h * 0.20),
                         (ex + eye_w * 0.62, ey + eye_h * 0.08)], line_c, lw_face + 1)
        else:
            _draw_eye(draw, ex, ey, eye_w, eye_h, fp, eyes_c, line_c, lw_face)
        # 眉毛：粗细/角度按 brow 风格 + 年龄/角色标记
        by = ey - eye_h * (2.0 if brow == "thick" else 1.7)
        blen = eye_w * (1.5 if brow in ("long", "soft") else 1.2)
        tilt = eye_h * {"angled": 0.45, "straight": 0.0, "thick": 0.05,
                        "long": 0.12, "soft": 0.18}.get(brow, 0.1)
        if brow == "thick":
            _line(draw, [(ex - blen / 2, by + tilt), (ex, by - tilt * 0.4),
                         (ex + blen / 2, by + tilt * 0.2)], line_c, lw_face + 3)
        elif brow == "angled":
            _line(draw, [(ex - blen / 2, by + eye_h * 0.5), (ex + blen / 2, by - eye_h * 0.2)],
                  line_c, lw_face + 1)
        elif brow == "long":
            _line(draw, [(ex - blen / 2, by + eye_h * 0.15), (ex - blen * 0.1, by),
                         (ex + blen * 0.6, by - eye_h * 0.12)], line_c, lw_face + 1)
        elif brow == "straight":
            _line(draw, [(ex - blen / 2, by), (ex + blen / 2, by)], line_c, lw_face + 2)
        else:
            _line(draw, [(ex - blen / 2, by + eye_h * 0.12), (ex, by - eye_h * 0.10),
                         (ex + blen / 2, by + eye_h * 0.05)], line_c, lw_face + 1)
        if "scar_brow" in fp["marks"] and key == "right":
            _line(draw, [(ex - eye_w * 0.4, by - eye_h * 1.6),
                         (ex + eye_w * 0.3, by + eye_h * 0.9)], _mix(line_c, "#b23a2e", 0.5),
                  max(2, lw_face))
    # 鼻子：正面中线 / 侧面外凸
    nose_top = head_cy + head_h * 0.01
    nose_len = head_h * (0.115 if fp["old"] else 0.10)
    nose_w = head_w * (0.13 if fp["female"] else 0.15)
    if profile:
        px = head_cx + head_w * 0.30
        _polygon(draw, [(px - head_w * 0.05, nose_top - head_h * 0.02),
                        (px + head_w * 0.16, nose_top + nose_len * 0.55),
                        (px + head_w * 0.02, nose_top + nose_len * 0.92),
                        (px - head_w * 0.06, nose_top + nose_len * 0.80)],
                 fill=skin, outline=line_c, width=max(1, lw_face))
    else:
        _draw_nose(draw, head_cx + eyes["feature_dx"], nose_top, nose_len, nose_w,
                   skin, line_c, lw_face)
    # 腮红 / 皱纹 / 雀斑 / 痣
    if fp["cheek"]:
        for side in (-1, 1):
            bx = head_cx + eyes["feature_dx"] + side * head_w * 0.34
            by = head_cy + head_h * 0.16
            _ellipse(draw, (bx - head_w * 0.10, by - head_h * 0.05,
                            bx + head_w * 0.10, by + head_h * 0.05),
                     fill=_mix("#f28b8b", skin, 0.62))
    if fp["old"]:
        for i in range(2):
            wy = head_cy - head_h * (0.30 - i * 0.09)
            _line(draw, [(head_cx + eyes["feature_dx"] - head_w * 0.24, wy),
                         (head_cx + eyes["feature_dx"] + head_w * 0.22, wy)], _mix(line_c, skin, 0.70),
                  max(1, lw_face - 1))
        for side in (-1, 1):
            _line(draw, [(head_cx + eyes["feature_dx"] + side * head_w * 0.20,
                          head_cy + head_h * 0.10),
                         (head_cx + eyes["feature_dx"] + side * head_w * 0.30,
                          head_cy + head_h * 0.34)], _mix(line_c, skin, 0.68), max(1, lw_face - 1))
    if "smile_lines" in fp["marks"]:
        for side in (-1, 1):
            _line(draw, [(head_cx + eyes["feature_dx"] + side * head_w * 0.22,
                          head_cy + head_h * 0.22),
                         (head_cx + eyes["feature_dx"] + side * head_w * 0.30,
                          head_cy + head_h * 0.34)], _mix(line_c, skin, 0.62), max(1, lw_face - 1))
    if "freckles" in fp["marks"]:
        rng = random.Random(int(hashlib.md5(str(round(head_cx)).encode()).hexdigest()[:6], 16))
        for _ in range(7):
            fx = head_cx + eyes["feature_dx"] + rng.uniform(-head_w * 0.34, head_w * 0.34)
            fy = head_cy + rng.uniform(head_h * 0.10, head_h * 0.26)
            _ellipse(draw, (fx - 1.5, fy - 1.5, fx + 1.5, fy + 1.5), fill=_mix("#b06a4a", skin, 0.45))
    if "beauty_mark" in fp["marks"]:
        _ellipse(draw, (head_cx + eyes["feature_dx"] + head_w * 0.30,
                        head_cy + head_h * 0.08,
                        head_cx + eyes["feature_dx"] + head_w * 0.34,
                        head_cy + head_h * 0.12), fill=_mix("#5a2a2a", skin, 0.32))
    if "dark_circles" in fp["marks"]:
        for key in ("left", "right"):
            ex, ey = eyes[key]
            _line(draw, [(ex - eye_w * 0.45, ey + eye_h * 1.1), (ex, ey + eye_h * 1.5),
                         (ex + eye_w * 0.45, ey + eye_h * 1.1)], _mix("#5a4a6a", skin, 0.70),
                  max(1, lw_face - 1))
    _draw_mouth(draw, g, mouth, line_c, skin, lw_face, fp)
    # 下颌/颈部阴影（增强立体）
    jaw_y = head_cy + head_h * 0.40
    _line(draw, [(head_cx - head_w * 0.20, jaw_y), (head_cx, jaw_y + head_h * 0.10),
                 (head_cx + head_w * 0.20, jaw_y)], _mix(line_c, skin, 0.74), max(1, lw_face - 1))
    _draw_eye_accessories(draw, g, ap, style, eyes, line_c, lw)


_MOUTH_SHAPE = {
    "closed": (0.55, 0.045), "m": (0.80, 0.10), "a": (0.92, 0.34),
    "o": (0.72, 0.28), "e": (1.10, 0.24), "i": (0.62, 0.20), "u": (0.58, 0.18),
}


def _draw_mouth(draw, g, mouth, line_c, skin, lw, fp=None):
    """口型：closed/m 抿线、a/o/u 开口、e 横向张口露齿、i 窄口；宽度按头高缩放。"""
    mouth = str(mouth or "closed").lower()
    if mouth not in _MOUTH_SHAPE:
        mouth = "closed"
    head_w, head_h = g["head_w"], g["head_h"]
    feats = _eyes_geometry(g, g["pose"])
    cx = g["head_cx"] + feats["feature_dx"]
    if g["pose"] == "side":
        cx += head_w * 0.14
    my = g["head_cy"] + head_h * 0.285
    base = head_w * 0.30
    width_ratio, height_ratio = _MOUTH_SHAPE[mouth]
    mw = base * width_ratio
    mh = head_h * height_ratio
    lip = _mix("#9a4a48", skin, 0.30)
    lip_dark = _mix("#6a2a2a", line_c, 0.25)
    lw_m = max(2, int(lw))
    if mouth in ("closed", "m"):
        # 唇形：上唇双弧 + 下唇
        _line(draw, [(cx - mw / 2, my), (cx - mw * 0.18, my - mh * 0.55),
                     (cx, my - mh * 0.20), (cx + mw * 0.18, my - mh * 0.55),
                     (cx + mw / 2, my)], line_c, lw_m)
        _line(draw, [(cx - mw * 0.42, my + mh * 0.35), (cx, my + mh * 0.55),
                     (cx + mw * 0.42, my + mh * 0.35)], lip, max(2, lw_m))
        if mouth == "m":
            _ellipse(draw, (cx - mw * 0.46, my - mh * 0.4, cx + mw * 0.46, my + mh * 0.7),
                     fill=lip, outline=line_c, width=lw_m)
            _line(draw, [(cx - mw * 0.30, my + mh * 0.12), (cx + mw * 0.30, my + mh * 0.12)],
                  lip_dark, max(1, lw_m))
    elif mouth == "a":  # 纵向大张
        _ellipse(draw, (cx - mw * 0.55, my - mh * 0.55, cx + mw * 0.55, my + mh * 0.65),
                 fill="#4a1a1e", outline=line_c, width=lw_m)
        _ellipse(draw, (cx - mw * 0.34, my - mh * 0.50, cx + mw * 0.34, my - mh * 0.18),
                 fill="#f7f2ea")
        _ellipse(draw, (cx - mw * 0.18, my + mh * 0.22, cx + mw * 0.18, my + mh * 0.48),
                 fill="#c96a6a")
    elif mouth == "o":  # 圆口
        _ellipse(draw, (cx - mw * 0.45, my - mh * 0.60, cx + mw * 0.45, my + mh * 0.60),
                 fill="#4a1a1e", outline=line_c, width=lw_m)
        _ellipse(draw, (cx - mw * 0.28, my - mh * 0.42, cx + mw * 0.28, my + mh * 0.05),
                 fill="#f7f2ea")
    elif mouth == "e":  # 横向张口露齿（最宽）
        _ellipse(draw, (cx - mw * 0.55, my - mh * 0.45, cx + mw * 0.55, my + mh * 0.60),
                 fill="#4a1a1e", outline=line_c, width=lw_m)
        _rounded_rect(draw, (cx - mw * 0.44, my - mh * 0.40, cx + mw * 0.44, my - mh * 0.05),
                      2, fill="#f7f2ea")
        for i in range(3):
            tx = cx - mw * 0.30 + i * mw * 0.30
            _line(draw, [(tx, my - mh * 0.38), (tx, my - mh * 0.08)], _mix(line_c, "#f7f2ea", 0.55),
                  max(1, lw_m - 1))
    elif mouth == "i":  # 窄口
        _ellipse(draw, (cx - mw * 0.32, my - mh * 0.45, cx + mw * 0.32, my + mh * 0.60),
                 fill="#4a1a1e", outline=line_c, width=lw_m)
        _rounded_rect(draw, (cx - mw * 0.24, my - mh * 0.40, cx + mw * 0.24, my - mh * 0.12),
                      2, fill="#f7f2ea")
    else:  # u 小圆口
        _ellipse(draw, (cx - mw * 0.34, my - mh * 0.35, cx + mw * 0.34, my + mh * 0.50),
                 fill="#4a1a1e", outline=line_c, width=lw_m)
        _ellipse(draw, (cx - mw * 0.18, my - mh * 0.05, cx + mw * 0.18, my + mh * 0.30),
                 fill="#d98a8a")
    # 下巴下唇阴影（增强立体）
    _line(draw, [(cx - mw * 0.30, my + mh * 0.75), (cx + mw * 0.30, my + mh * 0.75)],
          _mix(line_c, skin, 0.72), max(1, lw_m - 1))


def _draw_eye_accessories(draw, g, ap, style, eyes, line_c, lw):
    accessories = [str(a).lower() for a in (ap.get("accessories") or [])]
    accents = _style_palette(style)["accent"]
    if "glasses" in accessories:
        for key in ("left", "right"):
            if eyes["single"] and key == "left":
                continue
            ex, ey = eyes[key]
            r = eyes["eye_w"] * 0.72
            _ellipse(draw, (ex - r, ey - r * 0.75, ex + r, ey + r * 0.75),
                     outline=line_c, width=max(2, int(lw)))
        exl = eyes["left"][0] if not eyes["single"] else eyes["right"][0] - eyes["eye_w"] * 0.6
        exr = eyes["right"][0]
        _line(draw, [(exl, eyes["eye_y"]), (exr, eyes["eye_y"])], line_c, max(2, int(lw)))
    if "sunglasses" in accessories:
        for key in ("left", "right"):
            if eyes["single"] and key == "left":
                continue
            ex, ey = eyes[key]
            r = eyes["eye_w"] * 0.85
            _rounded_rect(draw, (ex - r, ey - r * 0.62, ex + r, ey + r * 0.62), 3,
                          fill="#14161a", outline=line_c, width=max(1, int(lw)))
        _line(draw, [(eyes["left"][0], eyes["eye_y"]), (eyes["right"][0], eyes["eye_y"])],
              "#14161a", max(2, int(lw)))
    if "glow" in accessories:  # 神秘嘉宾：眼底微光
        for key in ("left", "right"):
            if eyes["single"] and key == "left":
                continue
            ex, ey = eyes[key]
            r = eyes["eye_w"] * 1.15
            _ellipse(draw, (ex - r, ey - r * 0.6, ex + r, ey + r * 0.6),
                     fill=_mix(accents, "#f0d8c0", 0.72))


# ---------------------------------------------------------------------------
# 服装 / 身体
# ---------------------------------------------------------------------------
def _draw_legs(draw, g, ap, style, main, accent, line_c, lw):
    outfit = ap.get("outfit") or {}
    otype = str(outfit.get("type") or "suit").lower()
    cx, hip_y, feet_y = g["cx"], g["hip_y"], g["feet_y"]
    hip_w = g["hip_w"]
    pants = _tinted(str(outfit.get("pants") or _mix(main, "#222222", 0.55)), style, key=True)
    if otype in ("hanfu", "robe", "imperial_robe", "official_robe", "scholar", "traveler_robe"):
        skirt_w = hip_w * (1.5 if g["gender"] != "male" else 1.35)
        _polygon(draw, [(cx - hip_w * 0.42, hip_y), (cx + hip_w * 0.42, hip_y),
                        (cx + skirt_w * 0.5, feet_y - g["H"] * 0.005),
                        (cx - skirt_w * 0.5, feet_y - g["H"] * 0.005)],
                 fill=main, outline=line_c, width=lw)
        _line(draw, [(cx, hip_y + g["H"] * 0.01), (cx, feet_y - g["H"] * 0.01)],
              _mix(main, line_c, 0.55), lw)
        _line(draw, [(cx - skirt_w * 0.5, feet_y - g["H"] * 0.02),
                     (cx + skirt_w * 0.5, feet_y - g["H"] * 0.02)], accent, max(lw, 2))
    elif otype in ("armor",):
        _polygon(draw, [(cx - hip_w * 0.40, hip_y), (cx + hip_w * 0.40, hip_y),
                        (cx + hip_w * 0.46, feet_y), (cx - hip_w * 0.46, feet_y)],
                 fill=_mix(main, "#333333", 0.35), outline=line_c, width=lw)
        for side in (-1, 1):  # 腿甲
            _polygon(draw, [(cx + side * hip_w * 0.05, hip_y + g["body_h"] * 0.06),
                            (cx + side * hip_w * 0.42, hip_y),
                            (cx + side * hip_w * 0.46, feet_y - g["H"] * 0.03),
                            (cx + side * hip_w * 0.08, feet_y - g["H"] * 0.03)],
                     fill=_mix(main, "#888888", 0.25), outline=line_c, width=lw)
    elif otype in ("dress", "gown"):
        skirt_w = hip_w * 1.85
        _polygon(draw, [(cx - hip_w * 0.46, hip_y), (cx + hip_w * 0.46, hip_y),
                        (cx + skirt_w * 0.5, feet_y - g["H"] * 0.005),
                        (cx - skirt_w * 0.5, feet_y - g["H"] * 0.005)],
                 fill=main, outline=line_c, width=lw)
        _line(draw, [(cx - skirt_w * 0.48, feet_y - g["H"] * 0.03),
                     (cx + skirt_w * 0.48, feet_y - g["H"] * 0.03)], accent, max(lw, 2))
    elif otype in ("robot",):
        for side in (-1, 1):
            _rounded_rect(draw, (cx + side * hip_w * 0.10 - hip_w * 0.13, hip_y,
                                 cx + side * hip_w * 0.10 + hip_w * 0.13, feet_y),
                          hip_w * 0.08, fill=_mix(main, "#444444", 0.25), outline=line_c, width=lw)
            _line(draw, [(cx + side * hip_w * 0.10, hip_y + g["body_h"] * 0.1),
                         (cx + side * hip_w * 0.10, hip_y + g["body_h"] * 0.3)],
                  _mix(accent, main, 0.55), lw)
    else:  # 裤子
        for side in (-1, 1):
            _polygon(draw, [(cx + side * hip_w * 0.03, hip_y),
                            (cx + side * hip_w * 0.44, hip_y),
                            (cx + side * hip_w * 0.34, feet_y),
                            (cx + side * hip_w * 0.06, feet_y)],
                     fill=pants, outline=line_c, width=lw)
    # 鞋（所有服装统一：脚部小鞋，长袍只露鞋尖）
    shoe = _tinted(str(outfit.get("shoes") or _mix(pants, "#000000", 0.35)), style)
    if otype in ("hanfu", "robe", "imperial_robe", "official_robe", "scholar",
                 "traveler_robe", "dress", "gown"):
        for side in (-1, 1):
            hx = cx + side * hip_w * 0.26
            _ellipse(draw, (hx - hip_w * 0.12, feet_y - g["H"] * 0.012,
                            hx + hip_w * 0.12, feet_y + g["H"] * 0.008),
                     fill=shoe, outline=line_c, width=max(1, lw - 1))
    else:
        for side in (-1, 1):
            hx = cx + side * hip_w * 0.24
            _rounded_rect(draw, (hx - hip_w * 0.13, feet_y - g["H"] * 0.020,
                                 hx + hip_w * 0.15, feet_y + g["H"] * 0.006),
                          max(2, int(hip_w * 0.03)), fill=shoe, outline=line_c,
                          width=max(1, lw - 1))


def _draw_torso(draw, g, ap, style, main, accent, line_c, lw):
    outfit = ap.get("outfit") or {}
    otype = str(outfit.get("type") or "suit").lower()
    cx, shoulder_y = g["cx"], g["shoulder_y"]
    shoulder_w, waist_w, hip_w, hip_y = g["shoulder_w"], g["waist_w"], g["hip_w"], g["hip_y"]
    torso = [(cx - shoulder_w / 2, shoulder_y),
             (cx + shoulder_w / 2, shoulder_y),
             (cx + waist_w / 2 + (hip_w - waist_w) * 0.3, hip_y),
             (cx - waist_w / 2 - (hip_w - waist_w) * 0.3, hip_y)]
    _polygon(draw, torso, fill=main, outline=line_c, width=lw)
    accent_c = _tinted(accent, style, key=True)
    if otype in ("hanfu", "imperial_robe", "official_robe", "traveler_robe", "scholar"):
        # 交领右衽
        _polygon(draw, [(cx - shoulder_w * 0.30, shoulder_y),
                        (cx + shoulder_w * 0.22, shoulder_y),
                        (cx + waist_w * 0.10, hip_y - g["body_h"] * 0.02),
                        (cx - waist_w * 0.24, hip_y - g["body_h"] * 0.02)],
                 fill=_mix(main, "#ffffff", 0.18), outline=line_c, width=max(1, lw - 1))
        _line(draw, [(cx - shoulder_w * 0.30, shoulder_y),
                     (cx + waist_w * 0.04, hip_y - g["body_h"] * 0.04)], accent_c, max(lw, 2))
        _rounded_rect(draw, (cx - hip_w * 0.40, hip_y - g["body_h"] * 0.10,
                             cx + hip_w * 0.40, hip_y - g["body_h"] * 0.03),
                      g["H"] * 0.01, fill=accent_c, outline=line_c, width=max(1, lw - 1))
        for i in range(-1, 2):  # 腰带纹样
            _ellipse(draw, (cx + i * hip_w * 0.16 - hip_w * 0.04,
                            hip_y - g["body_h"] * 0.075,
                            cx + i * hip_w * 0.16 + hip_w * 0.04,
                            hip_y - g["body_h"] * 0.045), fill=_mix(accent_c, "#ffffff", 0.4))
        if otype == "imperial_robe":
            _polygon(draw, [(cx - shoulder_w * 0.34, shoulder_y + g["body_h"] * 0.10),
                            (cx + shoulder_w * 0.34, shoulder_y + g["body_h"] * 0.10),
                            (cx + shoulder_w * 0.30, shoulder_y + g["body_h"] * 0.16),
                            (cx - shoulder_w * 0.30, shoulder_y + g["body_h"] * 0.16)],
                     fill=_mix(accent_c, "#ffd76a", 0.35))
    elif otype == "armor":
        _polygon(draw, [(cx - shoulder_w * 0.44, shoulder_y + g["body_h"] * 0.02),
                        (cx + shoulder_w * 0.44, shoulder_y + g["body_h"] * 0.02),
                        (cx + waist_w * 0.52, hip_y),
                        (cx - waist_w * 0.52, hip_y)],
                 fill=_mix(main, "#9a9a9a", 0.25), outline=line_c, width=lw)
        for row in range(2):
            for col in range(-2, 3):
                bx = cx + col * shoulder_w * 0.17
                by = shoulder_y + g["body_h"] * (0.10 + row * 0.14)
                _ellipse(draw, (bx - shoulder_w * 0.045, by - g["body_h"] * 0.035,
                                bx + shoulder_w * 0.045, by + g["body_h"] * 0.035),
                         fill=_mix(accent_c, "#ffffff", 0.35), outline=line_c, width=max(1, lw - 1))
        _line(draw, [(cx, shoulder_y + g["body_h"] * 0.02), (cx, hip_y)], line_c, max(1, lw - 1))
    elif otype in ("suit", "blazer", "jacket"):
        _polygon(draw, [(cx - shoulder_w * 0.18, shoulder_y),
                        (cx + shoulder_w * 0.18, shoulder_y),
                        (cx + shoulder_w * 0.10, hip_y),
                        (cx - shoulder_w * 0.10, hip_y)],
                 fill=_mix(main, "#ffffff", 0.75), outline=line_c, width=max(1, lw - 1))
        _polygon(draw, [(cx - shoulder_w * 0.10, shoulder_y + g["body_h"] * 0.02),
                        (cx + shoulder_w * 0.10, shoulder_y + g["body_h"] * 0.02),
                        (cx + shoulder_w * 0.04, hip_y),
                        (cx - shoulder_w * 0.04, hip_y)],
                 fill=accent_c, outline=line_c, width=max(1, lw - 1))
        for side in (-1, 1):  # 翻领
            _polygon(draw, [(cx + side * shoulder_w * 0.18, shoulder_y),
                            (cx + side * shoulder_w * 0.34, shoulder_y),
                            (cx + side * shoulder_w * 0.20, shoulder_y + g["body_h"] * 0.20),
                            (cx + side * shoulder_w * 0.10, shoulder_y + g["body_h"] * 0.16)],
                     fill=_mix(main, "#000000", 0.12), outline=line_c, width=max(1, lw - 1))
        for i in range(2):
            _ellipse(draw, (cx + shoulder_w * 0.02, shoulder_y + g["body_h"] * (0.24 + i * 0.10),
                            cx + shoulder_w * 0.045, shoulder_y + g["body_h"] * (0.24 + i * 0.10) + 3),
                     fill=accent_c)
    elif otype in ("hoodie", "oversize_hoodie"):
        _polygon(draw, [(cx - shoulder_w * 0.30, shoulder_y),
                        (cx + shoulder_w * 0.30, shoulder_y),
                        (cx + shoulder_w * 0.26, hip_y),
                        (cx - shoulder_w * 0.26, hip_y)],
                 fill=_mix(main, "#000000", 0.10), outline=line_c, width=lw)  # 帽子后檐
        _rounded_rect(draw, (cx - waist_w * 0.42, hip_y - g["body_h"] * 0.22,
                             cx + waist_w * 0.42, hip_y - g["body_h"] * 0.06),
                      g["H"] * 0.02, outline=line_c, width=max(1, lw - 1))
        for side in (-1, 1):  # 抽绳
            _line(draw, [(cx + side * waist_w * 0.12, shoulder_y + g["body_h"] * 0.02),
                         (cx + side * waist_w * 0.16, shoulder_y + g["body_h"] * 0.22)],
                  accent_c, max(lw, 2))
    elif otype in ("tee", "t_shirt"):
        _line(draw, [(cx - shoulder_w * 0.26, shoulder_y + g["body_h"] * 0.005),
                     (cx, shoulder_y + g["body_h"] * 0.06),
                     (cx + shoulder_w * 0.26, shoulder_y + g["body_h"] * 0.005)],
              accent_c, max(lw, 2))
    elif otype in ("shirt", "cardigan", "turtleneck"):
        if otype == "turtleneck":
            _rounded_rect(draw, (cx - waist_w * 0.22, shoulder_y - g["body_h"] * 0.04,
                                 cx + waist_w * 0.22, shoulder_y + g["body_h"] * 0.03),
                          g["H"] * 0.01, fill=_mix(main, "#000000", 0.12),
                          outline=line_c, width=lw)
        else:
            _polygon(draw, [(cx - shoulder_w * 0.12, shoulder_y),
                            (cx + shoulder_w * 0.12, shoulder_y),
                            (cx + shoulder_w * 0.04, hip_y),
                            (cx - shoulder_w * 0.04, hip_y)],
                     fill=_mix(main, "#ffffff", 0.65), outline=line_c, width=max(1, lw - 1))
            if otype == "cardigan":
                _polygon(draw, [(cx - shoulder_w * 0.22, shoulder_y),
                                (cx - shoulder_w * 0.34, shoulder_y),
                                (cx - shoulder_w * 0.30, hip_y),
                                (cx - shoulder_w * 0.10, hip_y)],
                         fill=_mix(main, "#000000", 0.15), outline=line_c, width=max(1, lw - 1))
                _polygon(draw, [(cx + shoulder_w * 0.22, shoulder_y),
                                (cx + shoulder_w * 0.34, shoulder_y),
                                (cx + shoulder_w * 0.30, hip_y),
                                (cx + shoulder_w * 0.10, hip_y)],
                         fill=_mix(main, "#000000", 0.15), outline=line_c, width=max(1, lw - 1))
            for i in range(3):
                _ellipse(draw, (cx - 2, shoulder_y + g["body_h"] * (0.10 + i * 0.10) - 2,
                                cx + 2, shoulder_y + g["body_h"] * (0.10 + i * 0.10) + 2),
                         fill=line_c)
    elif otype in ("cloak",):
        _polygon(draw, [(cx - shoulder_w * 0.60, shoulder_y - g["body_h"] * 0.03),
                        (cx + shoulder_w * 0.60, shoulder_y - g["body_h"] * 0.03),
                        (cx + shoulder_w * 0.80, hip_y + g["body_h"] * 0.18),
                        (cx - shoulder_w * 0.80, hip_y + g["body_h"] * 0.18)],
                 fill=_mix(main, "#000000", 0.20), outline=line_c, width=lw)
        _ellipse(draw, (cx - shoulder_w * 0.10, shoulder_y + g["body_h"] * 0.02,
                        cx + shoulder_w * 0.10, shoulder_y + g["body_h"] * 0.08),
                 fill=accent_c, outline=line_c, width=max(1, lw - 1))
    elif otype == "robot":
        _rounded_rect(draw, (cx - shoulder_w * 0.34, shoulder_y,
                             cx + shoulder_w * 0.34, hip_y), g["H"] * 0.02,
                      fill=_mix(main, "#ffffff", 0.10), outline=line_c, width=lw)
        _rounded_rect(draw, (cx - shoulder_w * 0.16, shoulder_y + g["body_h"] * 0.14,
                             cx + shoulder_w * 0.16, shoulder_y + g["body_h"] * 0.24),
                      3, fill=_mix(accent_c, "#ffffff", 0.18), outline=line_c, width=max(1, lw - 1))
        for side in (-1, 1):
            _ellipse(draw, (cx + side * shoulder_w * 0.16 - 4, shoulder_y + g["body_h"] * 0.34 - 4,
                            cx + side * shoulder_w * 0.16 + 4, shoulder_y + g["body_h"] * 0.34 + 4),
                     fill=accent_c)
    # 统一衣褶 + 侧光阴影（所有服装都加，增加体积感）
    fold = _mix(main, line_c, 0.32)
    for k in (0.28, 0.62):
        y0 = shoulder_y + g["body_h"] * k
        _line(draw, [(cx - waist_w * 0.30, y0),
                     (cx - waist_w * 0.10, y0 + g["body_h"] * 0.14)], fold, max(1, int(lw)))
    _polygon(draw, [(cx - shoulder_w * 0.50, shoulder_y),
                    (cx - shoulder_w * 0.20, shoulder_y),
                    (cx - waist_w * 0.24, hip_y),
                    (cx - shoulder_w * 0.50, hip_y)],
             fill=_mix(main, "#000000", 0.20))


def _draw_arms(draw, g, ap, style, main, skin, line_c, lw):
    """手臂：front 自然下垂、side 只画近侧臂、point 右臂前上抬、think 左臂交肘（前臂另由 _draw_think_hand 画）。"""
    pose = g["pose"]
    hands = _hand_positions(g)
    cx, shoulder_y = g["cx"], g["shoulder_y"]
    shoulder_w, body_h = g["shoulder_w"], g["body_h"]
    outfit = ap.get("outfit") or {}
    sleeve_col = _tinted(str(outfit.get("main") or main), style, key=True)
    lw_sleeve = max(3, int(shoulder_w * 0.19))
    lw_hand = max(1, int(lw))

    def _arm(pts, col, width):
        _line(draw, pts, col, int(width * 1.35))
        _line(draw, pts, _mix(col, "#ffffff", 0.08), int(width * 0.8))

    def _hand(hx, hy, r, col):
        _ellipse(draw, (hx - r, hy - r * 0.9, hx + r, hy + r * 1.05),
                 fill=col, outline=line_c, width=lw_hand)

    left_shoulder = (cx - shoulder_w * 0.44, shoulder_y + body_h * 0.03)
    right_shoulder = (cx + shoulder_w * 0.44, shoulder_y + body_h * 0.03)
    hand_r = g["hand_r"]
    if pose == "point":
        # 左臂自然，右臂前上抬 30° 指出去
        _arm([left_shoulder, hands["left"]], sleeve_col, lw_sleeve)
        _hand(*hands["left"], hand_r, skin)
        elbow = (cx + shoulder_w * 0.62, shoulder_y + body_h * 0.16)
        _arm([right_shoulder, elbow, hands["right"]], sleeve_col, lw_sleeve)
        hx, hy = hands["right"]
        _hand(hx, hy, hand_r * 1.05, skin)
        _line(draw, [(hx + hand_r * 0.2, hy - hand_r * 0.15),
                     (hx + hand_r * 2.8, hy - hand_r * 0.55)], skin, max(2, int(hand_r * 0.8)))
        _ellipse(draw, (hx + hand_r * 2.6, hy - hand_r * 0.95,
                        hx + hand_r * 3.3, hy - hand_r * 0.25), fill=skin)
    elif pose == "think":
        # 只画右臂；左臂上臂向下、前臂托腮（前臂/手在脸部之后由 _draw_think_hand 补）
        if pose != "side":
            _arm([right_shoulder, hands["right"]], sleeve_col, lw_sleeve)
            _hand(*hands["right"], hand_r, skin)
        elbow = (cx - shoulder_w * 0.36, shoulder_y + body_h * 0.30)
        _arm([left_shoulder, elbow], sleeve_col, lw_sleeve)
    elif pose == "side":
        _arm([right_shoulder, (cx + shoulder_w * 0.30, shoulder_y + body_h * 0.55),
              hands["right"]], sleeve_col, lw_sleeve)
        _hand(*hands["right"], hand_r, skin)
    else:
        for shoulder, side in ((left_shoulder, -1), (right_shoulder, 1)):
            hand = hands["left"] if side < 0 else hands["right"]
            elbow = (shoulder[0] + side * shoulder_w * 0.10, shoulder[1] + body_h * 0.26)
            _arm([shoulder, elbow, hand], sleeve_col, lw_sleeve)
            _hand(*hand, hand_r, skin)
        # 袖口
        for shoulder, side in ((left_shoulder, -1), (right_shoulder, 1)):
            _line(draw, [(shoulder[0] + side * shoulder_w * 0.06, shoulder[1] + body_h * 0.04),
                         (shoulder[0] + side * shoulder_w * 0.02, shoulder[1] + body_h * 0.20)],
                  _mix(sleeve_col, line_c, 0.35), max(1, int(lw)))


def _draw_accessories(canvas, draw, g, ap, style, line_c, lw):
    accessories = [str(a).lower() for a in (ap.get("accessories") or [])]
    if not accessories:
        return
    palette = _style_palette(style)
    accent = _tinted(palette["accent"], style, key=True)
    outfit = ap.get("outfit") or {}
    main = _tinted(str(outfit.get("main") or "#444444"), style, key=True)
    skin = _tinted(ap.get("skin") or "#f0d8c0", style)
    hands = _hand_positions(g)
    cx, shoulder_y, body_h = g["cx"], g["shoulder_y"], g["body_h"]
    shoulder_w, head_w, head_h = g["shoulder_w"], g["head_w"], g["head_h"]
    head_cy = g["head_cy"]

    def _hand(item):
        if item in ("staff", "guandao", "bow"):
            return (cx - shoulder_w * 0.75, shoulder_y + body_h * 0.55)
        if item in ("trophy",):
            return (cx + shoulder_w * 0.68, shoulder_y + body_h * 0.20)
        if item in ("phone", "coffee", "snack"):
            return hands["left"]
        return hands["right"]

    for item in accessories:
        hx, hy = _hand(item)
        if item == "feather_fan":
            _ellipse(draw, (hx - head_w * 0.10, hy - head_h * 0.38,
                            hx + head_w * 0.42, hy - head_h * 0.02),
                     fill=_mix("#f4efe4", "#e8e0d0", 0.4), outline=line_c, width=lw)
            for i in range(4):
                _line(draw, [(hx, hy - head_h * 0.04),
                             (hx + head_w * 0.10 + i * head_w * 0.07, hy - head_h * 0.34)],
                      line_c, max(1, lw - 1))
            _line(draw, [(hx, hy - head_h * 0.02), (hx - head_w * 0.02, hy + head_h * 0.06)],
                  line_c, max(2, lw))
        elif item == "scroll":
            _rounded_rect(draw, (hx - head_w * 0.28, hy - head_h * 0.05,
                                 hx + head_w * 0.28, hy + head_h * 0.10), 3,
                          fill="#efe6d0", outline=line_c, width=lw)
            _ellipse(draw, (hx - head_w * 0.30, hy - head_h * 0.07,
                            hx - head_w * 0.24, hy + head_h * 0.12), fill="#d9c8a8",
                     outline=line_c, width=max(1, lw - 1))
        elif item == "brush":
            _line(draw, [(hx, hy), (hx + head_w * 0.18, hy - head_h * 0.22)], "#6b4a2a",
                  max(2, lw))
            _polygon(draw, [(hx + head_w * 0.18, hy - head_h * 0.22),
                            (hx + head_w * 0.24, hy - head_h * 0.28),
                            (hx + head_w * 0.21, hy - head_h * 0.16)], fill=line_c)
        elif item == "book":
            _polygon(draw, [(hx - head_w * 0.34, hy + head_h * 0.04),
                            (hx - head_w * 0.02, hy - head_h * 0.10),
                            (hx - head_w * 0.02, hy + head_h * 0.10),
                            (hx - head_w * 0.34, hy + head_h * 0.20)],
                     fill="#efe6d0", outline=line_c, width=lw)
            _polygon(draw, [(hx - head_w * 0.02, hy - head_h * 0.10),
                            (hx + head_w * 0.30, hy),
                            (hx + head_w * 0.30, hy + head_h * 0.18),
                            (hx - head_w * 0.02, hy + head_h * 0.10)],
                     fill="#e2d6bc", outline=line_c, width=lw)
        elif item == "mic":
            _line(draw, [(hx, hy), (hx, hy + head_h * 0.30)], "#2a2a2a", max(3, lw))
            _ellipse(draw, (hx - head_h * 0.07, hy - head_h * 0.22,
                            hx + head_h * 0.07, hy - head_h * 0.06),
                     fill="#3a3a3a", outline=line_c, width=max(1, lw - 1))
        elif item == "headphones":
            _line(draw, [(cx - head_w * 0.50, head_cy - head_h * 0.10),
                         (cx, head_cy - head_h * 0.62),
                         (cx + head_w * 0.50, head_cy - head_h * 0.10)],
                  "#2a2a33", max(4, lw * 2))
            for side in (-1, 1):
                _rounded_rect(draw, (cx + side * head_w * 0.45 - head_w * 0.07,
                                     head_cy - head_h * 0.18,
                                     cx + side * head_w * 0.59,
                                     head_cy + head_h * 0.10),
                              3, fill="#3a3a45", outline=line_c, width=max(1, lw - 1))
        elif item == "headset":
            _line(draw, [(cx - head_w * 0.50, head_cy), (cx, head_cy - head_h * 0.62),
                         (cx + head_w * 0.50, head_cy)], "#23232b", max(4, lw * 2))
            _line(draw, [(cx + head_w * 0.52, head_cy + head_h * 0.05),
                         (cx + head_w * 0.35, head_cy + head_h * 0.30)], "#23232b", max(4, lw * 2))
        elif item == "glasses":
            pass  # 由脸部处理
        elif item == "sunglasses":
            pass
        elif item == "sword":
            _line(draw, [(cx - g["hip_w"] * 0.5, g["hip_y"]),
                         (cx - g["hip_w"] * 0.95, g["hip_y"] + body_h * 0.28)],
                  "#4a3a2a", max(3, lw))
            _line(draw, [(cx - g["hip_w"] * 0.5, g["hip_y"]),
                         (cx - g["hip_w"] * 0.62, g["hip_y"] + body_h * 0.16)],
                  accent, max(3, lw))
        elif item == "staff":
            _line(draw, [(hx, g["feet_y"]), (hx, shoulder_y - body_h * 0.05)], "#6b4a2a",
                  max(4, lw * 2))
            _ellipse(draw, (hx - head_w * 0.09, shoulder_y - body_h * 0.14,
                            hx + head_w * 0.09, shoulder_y - body_h * 0.02),
                     fill=accent, outline=line_c, width=lw)
        elif item == "guandao":
            _line(draw, [(hx, g["feet_y"]), (hx, shoulder_y - body_h * 0.22)], "#5a3a22",
                  max(5, lw * 2))
            _polygon(draw, [(hx, shoulder_y - body_h * 0.22), (hx + head_w * 0.34, shoulder_y - body_h * 0.34),
                            (hx + head_w * 0.44, shoulder_y - body_h * 0.10),
                            (hx + head_w * 0.18, shoulder_y - body_h * 0.02)],
                     fill="#b8bcc4", outline=line_c, width=lw)
        elif item == "bow":
            _line(draw, [(hx - head_w * 0.30, shoulder_y + body_h * 0.10),
                         (hx + head_w * 0.10, shoulder_y - body_h * 0.05),
                         (hx + head_w * 0.30, shoulder_y + body_h * 0.20)], "#6b4a2a", max(3, lw))
            _line(draw, [(hx - head_w * 0.30, shoulder_y + body_h * 0.10),
                         (hx + head_w * 0.30, shoulder_y + body_h * 0.20)], "#d9d2c0", max(1, lw - 1))
        elif item == "flagon":
            _rounded_rect(draw, (hx - head_w * 0.10, hy - head_h * 0.02,
                                 hx + head_w * 0.10, hy + head_h * 0.16), 2,
                          fill="#7a8a96", outline=line_c, width=lw)
            _line(draw, [(hx + head_w * 0.10, hy + head_h * 0.04),
                         (hx + head_w * 0.20, hy + head_h * 0.10)], "#7a8a96", max(2, lw))
        elif item == "trophy":
            _polygon(draw, [(hx - head_w * 0.16, hy - head_h * 0.20),
                            (hx + head_w * 0.16, hy - head_h * 0.20),
                            (hx + head_w * 0.10, hy + head_h * 0.02),
                            (hx - head_w * 0.10, hy + head_h * 0.02)],
                     fill="#e8c15a", outline=line_c, width=lw)
            for side in (-1, 1):
                _line(draw, [(hx + side * head_w * 0.16, hy - head_h * 0.14),
                             (hx + side * head_w * 0.26, hy - head_h * 0.02)], "#e8c15a",
                      max(2, lw))
        elif item == "clipboard":
            _rounded_rect(draw, (hx - head_w * 0.18, hy - head_h * 0.05,
                                 hx + head_w * 0.18, hy + head_h * 0.22), 2,
                          fill="#e9e2d2", outline=line_c, width=lw)
            _line(draw, [(hx - head_w * 0.10, hy + head_h * 0.06),
                         (hx + head_w * 0.10, hy + head_h * 0.06)], line_c, max(1, lw))
        elif item == "laptop":
            _polygon(draw, [(hx - head_w * 0.32, hy + head_h * 0.10),
                            (hx + head_w * 0.32, hy + head_h * 0.10),
                            (hx + head_w * 0.26, hy + head_h * 0.18),
                            (hx - head_w * 0.26, hy + head_h * 0.18)],
                     fill="#b9c2cc", outline=line_c, width=lw)
            _polygon(draw, [(hx - head_w * 0.28, hy + head_h * 0.10),
                            (hx + head_w * 0.28, hy + head_h * 0.10),
                            (hx + head_w * 0.22, hy - head_h * 0.20),
                            (hx - head_w * 0.22, hy - head_h * 0.20)],
                     fill="#2a3038", outline=line_c, width=lw)
        elif item == "coffee":
            _rounded_rect(draw, (hx - head_w * 0.09, hy - head_h * 0.02,
                                 hx + head_w * 0.09, hy + head_h * 0.14), 3,
                          fill="#e9e2d2", outline=line_c, width=lw)
        elif item == "phone":
            _rounded_rect(draw, (hx - head_w * 0.05, hy - head_h * 0.02,
                                 hx + head_w * 0.05, hy + head_h * 0.14), 2,
                          fill="#22262c", outline=line_c, width=lw)
        elif item == "snack":
            _polygon(draw, [(hx - head_w * 0.12, hy), (hx + head_w * 0.12, hy),
                            (hx + head_w * 0.08, hy + head_h * 0.16),
                            (hx - head_w * 0.08, hy + head_h * 0.16)],
                     fill="#e8a13a", outline=line_c, width=lw)
        elif item == "compass":
            _ellipse(draw, (hx - head_w * 0.09, hy - head_h * 0.05,
                            hx + head_w * 0.09, hy + head_h * 0.10),
                     fill="#c9b070", outline=line_c, width=lw)
            _line(draw, [(hx, hy), (hx + head_w * 0.04, hy - head_h * 0.01)],
                  accent, max(1, lw))
        elif item == "hairclip":
            hx0 = cx - head_w * 0.34
            hy0 = head_cy - head_h * 0.34
            _polygon(draw, [(hx0, hy0 - 5), (hx0 + 6, hy0), (hx0, hy0 + 5), (hx0 - 6, hy0)],
                     fill=accent, outline=line_c, width=max(1, lw - 1))
        elif item == "jade_pendant":
            _line(draw, [(cx - head_w * 0.18, g["shoulder_y"]),
                         (cx, g["shoulder_y"] + body_h * 0.14),
                         (cx + head_w * 0.18, g["shoulder_y"])], accent, max(1, lw))
            _ellipse(draw, (cx - head_w * 0.05, g["shoulder_y"] + body_h * 0.14,
                            cx + head_w * 0.05, g["shoulder_y"] + body_h * 0.22),
                     fill="#8fc9a8", outline=line_c, width=max(1, lw - 1))
        elif item == "backpack":
            for side in (-1, 1):
                _line(draw, [(cx + side * shoulder_w * 0.22, shoulder_y),
                             (cx + side * shoulder_w * 0.30, shoulder_y + body_h * 0.36)],
                      _mix(main, "#222222", 0.5), max(4, lw * 2))
        elif item == "watch":
            wx = hands["left"][0]
            wy = hands["left"][1] - g["head_h"] * 0.18
            _line(draw, [(wx - 4, wy), (wx + 4, wy)], accent, max(3, lw))
        elif item == "mask":
            _polygon(draw, [(cx - head_w * 0.40, head_cy + head_h * 0.12),
                            (cx + head_w * 0.40, head_cy + head_h * 0.12),
                            (cx + head_w * 0.30, head_cy + head_h * 0.52),
                            (cx - head_w * 0.30, head_cy + head_h * 0.52)],
                     fill=_mix("#f4f0e6", main, 0.08), outline=line_c, width=max(1, lw))
        elif item in ("antenna", "led_panel", "glow"):
            pass  # 机器人的天线/LED 由头部与服装处理
        elif item == "ruler":
            _line(draw, [(hx, hy - head_h * 0.10), (hx, hy + head_h * 0.20)], "#d9b86a",
                  max(2, lw))


# ---------------------------------------------------------------------------
# 头部（含机器人/披风特殊处理）
# ---------------------------------------------------------------------------
def _draw_head(canvas, draw, g, ap, style, skin, line_c, lw):
    head_w, head_h, head_cy = g["head_w"], g["head_h"], g["head_cy"]
    cx = g["head_cx"]
    neck_w = head_w * 0.30
    _rounded_rect(draw, (cx - neck_w / 2, head_cy + head_h * 0.30,
                         cx + neck_w / 2, g["shoulder_y"] + head_h * 0.04), head_w * 0.08,
                  fill=_shade(skin, -0.12), outline=line_c, width=max(1, lw - 1))
    if g.get("is_robot"):
        radius = head_w * 0.12
        _rounded_rect(draw, (cx - head_w * 0.5, head_cy - head_h * 0.5,
                             cx + head_w * 0.5, head_cy + head_h * 0.5),
                      radius, fill=_mix(skin, "#ffffff", 0.15), outline=line_c, width=lw)
        _rounded_rect(draw, (cx - head_w * 0.34, head_cy - head_h * 0.22,
                             cx + head_w * 0.34, head_cy + head_h * 0.20),
                      3, fill="#101820", outline=line_c, width=max(1, lw - 1))
        return
    # 正常头
    _ellipse(draw, (cx - head_w / 2, head_cy - head_h / 2,
                    cx + head_w / 2, head_cy + head_h / 2),
             fill=skin, outline=line_c, width=lw)
    for side in (-1, 1):  # 耳朵
        if g["pose"] == "side" and side == -1:
            continue
        _ellipse(draw, (cx + side * head_w * 0.47 - head_w * 0.045,
                        head_cy - head_h * 0.01,
                        cx + side * head_w * 0.47 + head_w * 0.045,
                        head_cy + head_h * 0.12),
                 fill=skin, outline=line_c, width=max(1, lw - 1))


def _draw_robot_face(draw, g, ap, pose, mouth, blink, style):
    head_w, head_h, head_cy = g["head_w"], g["head_h"], g["head_cy"]
    cx = g["head_cx"]
    accent = _tinted(ap.get("eye") or "#39E6FF", style)
    _line(draw, [(cx, head_cy - head_h * 0.50), (cx, head_cy - head_h * 0.76)],
          _mix(accent, "#8a96a2", 0.5), max(3, int(head_w * 0.03)))
    _ellipse(draw, (cx - head_w * 0.07, head_cy - head_h * 0.86,
                    cx + head_w * 0.07, head_cy - head_h * 0.72), fill=accent)
    eye_cy = head_cy - head_h * 0.08
    for side in (-1, 1):
        ex = cx + side * head_w * 0.20
        if blink:
            _line(draw, [(ex - head_w * 0.08, eye_cy), (ex + head_w * 0.08, eye_cy)],
                  accent, max(2, 3))
        else:
            _rounded_rect(draw, (ex - head_w * 0.09, eye_cy - head_h * 0.05,
                                 ex + head_w * 0.09, eye_cy + head_h * 0.05),
                          3, fill=accent)
    mouth_y = head_cy + head_h * 0.22
    widths = {"closed": 0.10, "a": 0.16, "o": 0.12, "e": 0.18, "i": 0.06, "u": 0.10, "m": 0.14}
    mw = head_w * widths.get(str(mouth or "closed"), 0.10)
    mh = head_h * (0.045 if mouth in ("a", "o", "u") else 0.025)
    _rounded_rect(draw, (cx - mw / 2, mouth_y - mh / 2, cx + mw / 2, mouth_y + mh / 2),
                  2, fill=_mix(accent, "#101820", 0.25))
    for i in range(3):  # 面板螺丝
        _ellipse(draw, (cx - head_w * 0.42, head_cy - head_h * 0.42,
                        cx - head_w * 0.37, head_cy - head_h * 0.37), fill="#6a7480")


# ---------------------------------------------------------------------------
# 主绘制：角色一张立绘（透明底 RGBA）
# ---------------------------------------------------------------------------
def _draw_character(char: dict, style: dict, pose: str, mouth: str, blink: bool,
                    W: int, H: int) -> Image.Image:
    ap = char.get("appearance") if isinstance(char.get("appearance"), dict) else {}
    outfit = ap.get("outfit") if isinstance(ap.get("outfit"), dict) else {}
    palette = _style_palette(style)
    line_c = _tinted(_style_line(style, char, "color", palette["fg"]), style)
    line_w = max(1.0, float(_style_line(style, char, "width", 3.0)) * (W / 512.0) * 0.9)
    skin = _tinted(ap.get("skin") or "#f0d8c0", style)
    hair = _hair_color(ap, style)
    main = _tinted(str(outfit.get("main") or "#4a4f57"), style, key=True)
    accent = _tinted(str(outfit.get("accent") or palette["accent"]), style, key=True)
    gender = str(ap.get("gender") or "male").lower()

    canvas = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(canvas)
    g = _geometry(ap, pose, W, H)
    g["hand_r"] = max(4.0, g["head_w"] * 0.10)

    # 地面投影
    shadow_w = g["shoulder_w"] * 1.15
    shadow_h = H * 0.022
    _ellipse(draw, (g["cx"] - shadow_w / 2, g["feet_y"] - shadow_h * 0.5,
                    g["cx"] + shadow_w / 2, g["feet_y"] + shadow_h * 0.5),
             fill=_alpha("#000000", 55))
    # 大件背持道具（先画在身体后）
    _draw_hair_back(canvas, draw, g, ap, style, hair, line_c, line_w)
    _draw_legs(draw, g, ap, style, main, accent, line_c, line_w)
    _draw_torso(draw, g, ap, style, main, accent, line_c, line_w)
    _draw_arms(draw, g, ap, style, main, skin, line_c, line_w)
    # 披风（盖住肩部）
    if str(outfit.get("type") or "").lower() == "cloak":
        _polygon(draw, [(g["cx"] - g["shoulder_w"] * 0.62, g["shoulder_y"]),
                        (g["cx"] - g["shoulder_w"] * 0.30, g["shoulder_y"] - g["head_h"] * 0.18),
                        (g["cx"] + g["shoulder_w"] * 0.30, g["shoulder_y"] - g["head_h"] * 0.18),
                        (g["cx"] + g["shoulder_w"] * 0.62, g["shoulder_y"])],
                 fill=_mix(main, "#000000", 0.35), outline=line_c, width=line_w)
    _draw_head(canvas, draw, g, ap, style, skin, line_c, line_w)
    if g.get("is_robot"):
        _draw_robot_face(draw, g, ap, pose, mouth, blink, style)
    else:
        _draw_beard(draw, g, ap, style, hair, line_c, line_w)
        _draw_face(draw, g, ap, pose, mouth, blink, style, skin, line_c, line_w)
    _draw_hair_front(draw, g, ap, style, hair, line_c, line_w)
    _draw_hat(draw, g, ap, style, main, accent, line_c, line_w)
    if pose == "think" and not g.get("is_robot"):
        _draw_think_hand(draw, g, ap, style, line_c, line_w)
    _draw_accessories(canvas, draw, g, ap, style, line_c, line_w)
    # 神秘嘉宾：兜帽下的阴影 + 发光眼
    if str(outfit.get("type") or "").lower() == "cloak":
        _polygon(draw, [(g["head_cx"] - g["head_w"] * 0.34, g["head_cy"] - g["head_h"] * 0.30),
                        (g["head_cx"] + g["head_w"] * 0.34, g["head_cy"] - g["head_h"] * 0.30),
                        (g["head_cx"] + g["head_w"] * 0.26, g["head_cy"] + g["head_h"] * 0.34),
                        (g["head_cx"] - g["head_w"] * 0.26, g["head_cy"] + g["head_h"] * 0.34)],
                 fill=_alpha("#0b0a12", 120))
    return canvas


# ---------------------------------------------------------------------------
# 风格后处理
# ---------------------------------------------------------------------------
def _rgb_array(img: Image.Image) -> np.ndarray:
    return np.asarray(img.convert("RGBA")).astype(np.float32)


def _to_image(arr: np.ndarray) -> Image.Image:
    return Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8), mode="RGBA")


def _edge_mask(arr: np.ndarray, threshold: float = 26.0) -> np.ndarray:
    """基于亮度梯度的内部线条 mask（float 0..1）。"""
    rgb = arr[..., :3]
    lum = rgb @ np.array([0.299, 0.587, 0.114], dtype=np.float32)
    gx = np.abs(np.diff(lum, axis=1, prepend=lum[:, :1]))
    gy = np.abs(np.diff(lum, axis=0, prepend=lum[:1, :]))
    edge = np.clip((gx + gy - threshold) / 55.0, 0.0, 1.0)
    alpha = arr[..., 3] / 255.0  # (h, w)
    return edge * alpha


def _finish_builtin(img: Image.Image, style: dict, char: dict) -> Image.Image:
    """内置风格后处理：各 treatment 明显不同（墨线/木刻/铜版/剪纸硬阴影/黏土/像素/半调/粉笔/卡通/烛光）。"""
    treatment = _treatment(style)
    palette = _style_palette(style)
    fg = np.array(_hx(palette["fg"])[:3], dtype=np.float32)
    paper = np.array(_hx(palette["paper"])[:3], dtype=np.float32)
    accent = np.array(_hx(palette["accent"])[:3], dtype=np.float32)
    arr = _rgb_array(img)
    rgb = arr[..., :3]
    alpha = arr[..., 3:4]
    h, w = rgb.shape[:2]
    if h < 2 or w < 2:
        return img
    rng = np.random.default_rng(17)
    a_flat = arr[..., 3] / 255.0
    lum = rgb @ np.array([0.299, 0.587, 0.114], dtype=np.float32)
    edge = _edge_mask(arr, 22)[..., None]

    if treatment == "pixel":
        small_w, small_h = max(8, w // 7), max(8, h // 7)
        small = img.resize((small_w, small_h), Image.NEAREST)
        out = small.resize((w, h), Image.NEAREST)
        oa = np.asarray(out).astype(np.float32)
        oa[..., 3] = np.where(oa[..., 3] > 90, 255.0, 0.0)
        oa[..., :3] = np.floor(oa[..., :3] / 42.0) * 42.0  # 6 级量化
        return _to_image(oa)

    if treatment == "cutout_paper":
        # 硬阴影（剪贴画纸片投影）+ 纸纹 + 白纸边
        shadow = np.zeros_like(arr)
        shadow[..., :3] = fg * 0.15
        shadow[..., 3] = arr[..., 3] * 0.55
        shadow = np.roll(shadow, shift=(max(3, h // 90), max(4, w // 90)), axis=(0, 1))
        rgb_out = rgb * 0.88 + paper * 0.12
        core = Image.fromarray(np.where(arr[..., 3] > 10, 255, 0).astype(np.uint8))
        border = np.clip(np.asarray(core.filter(ImageFilter.MaxFilter(7)),
                                    dtype=np.float32) / 255.0 - a_flat, 0.0, 1.0)
        edge_color = paper * 0.9 + 255 * 0.10
        rgb_out = rgb_out * (1 - border[..., None]) + edge_color * border[..., None]
        out = np.concatenate([rgb_out, (arr[..., 3:4] + border[..., None] * 210.0)], axis=-1)
        out = out + shadow * 0.9
        out[..., :3] = np.clip(out[..., :3], 0, 255)
        return _to_image(out)

    if treatment in ("ink_brush", "engrave", "woodcut", "chalk"):
        # 统一：宣纸/羊皮纸淡彩底 + 保留角色本色（不再是黑剪影），再叠线/排线
        preserve = {"ink_brush": 0.58, "engrave": 0.52, "woodcut": 0.46, "chalk": 0.40}[treatment]
        wash = rgb * preserve + paper * (1.0 - preserve)
        gray = wash.mean(axis=-1, keepdims=True)
        sat = {"ink_brush": 0.62, "engrave": 0.42, "woodcut": 0.50, "chalk": 0.22}[treatment]
        wash = gray + (wash - gray) * sat
        contrast = {"ink_brush": 1.10, "engrave": 1.16, "woodcut": 1.34, "chalk": 1.04}[treatment]
        wash = (wash - 128.0) * contrast + 128.0
        # 暗部墨韵（轻微压暗，保留细节）
        dark = np.clip((175.0 - lum) / 175.0, 0.0, 1.0)[..., None]
        wash = wash * (1.0 - dark * {"ink_brush": 0.16, "engrave": 0.20,
                                     "woodcut": 0.30, "chalk": 0.10}[treatment])
        yy, xx = np.mgrid[0:h, 0:w]
        if treatment in ("engrave", "woodcut"):
            period = 5 if treatment == "engrave" else 4
            hatch = (((xx + yy) // period) % 2 == 0).astype(np.float32)
            strength = 10.0 if treatment == "engrave" else 22.0
            mid = np.clip((200.0 - lum) / 200.0, 0.0, 1.0)
            wash = wash - (hatch * strength * mid)[..., None]
        if treatment == "chalk":
            speck = (rng.random((h, w)) > 0.93).astype(np.float32)
            wash = wash + speck[..., None] * 60.0 * a_flat[..., None]
        if treatment == "woodcut":
            levels = 5
            wash = np.floor(wash / (256.0 / levels)) * (256.0 / levels)
        wash = wash * (1.0 - 0.55 * edge) + fg * (0.55 * edge)
        out = np.concatenate([wash, alpha], axis=-1)
        grain = (rng.random((h, w)) - 0.5) * 8.0
        out[..., :3] = np.clip(out[..., :3] + grain[..., None] * a_flat[..., None], 0, 255)
        return _to_image(out)

    if treatment == "clay":
        base = img.filter(ImageFilter.GaussianBlur(0.9))
        ca = np.asarray(base).astype(np.float32)
        crgb = ca[..., :3] * 0.92 + paper * 0.08
        crgb = (crgb - 128.0) * 1.05 + 128.0
        yy, xx = np.mgrid[0:h, 0:w]
        light = np.clip(1.12 - (yy / max(1, h)) * 0.30, 0.8, 1.12)[..., None]
        crgb = crgb * light
        crgb = crgb + (np.roll(crgb, 2, axis=0) - crgb) * 0.45  # 软高光/阴影
        noise = (rng.random((h, w)) - 0.5) * 9.0
        crgb = crgb + noise[..., None] * ca[..., 3:4] / 255.0
        out = np.concatenate([crgb, ca[..., 3:4]], axis=-1)
        return _to_image(out)

    if treatment == "halftone":
        cell = max(4, int(min(w, h) / 70))
        yy, xx = np.mgrid[0:h, 0:w]
        gx = (xx // cell) * cell + cell / 2.0
        gy = (yy // cell) * cell + cell / 2.0
        dist = np.sqrt((xx - gx) ** 2 + (yy - gy) ** 2)
        dark = 1.0 - lum / 255.0
        radius = cell * 0.52 * dark
        dots = (dist < radius).astype(np.float32)
        base_rgb = rgb * 0.35 + paper * 0.42
        base_rgb = base_rgb * (1 - dots[..., None] * 0.85) + fg * (dots[..., None] * 0.85)
        # 强调色网点
        accent_dots = ((dist < radius * 0.55) & (xx % (cell * 3) < cell * 2)).astype(np.float32)
        base_rgb = base_rgb * (1 - accent_dots[..., None] * 0.25) + accent * (accent_dots[..., None] * 0.25)
        out = np.concatenate([base_rgb, alpha], axis=-1)
        out[..., 3] = a_flat * 255.0
        return _to_image(out)

    # 默认 / toon / neon_toon / oil / candle / baroque
    warm = treatment in ("candle", "baroque_candle", "oil", "baroque")
    sat = 1.18 if treatment in ("toon", "neon_toon") else 1.08
    mean = rgb.mean(axis=-1, keepdims=True)
    rgb_out = mean + (rgb - mean) * sat
    if warm:
        rgb_out = rgb_out * 0.88 + np.array([255, 190, 120], dtype=np.float32) * 0.12
        yy, xx = np.mgrid[0:h, 0:w]
        rim = np.clip((np.abs(xx - w / 2) / (w / 2) - 0.55) * 2.0, 0, 1)[..., None]
        rgb_out = rgb_out + accent * rim * 0.16 * a_flat[..., None]
        dark = np.clip((yy / max(1, h) - 0.55) * 1.6, 0, 1)[..., None]
        rgb_out = rgb_out * (1 - dark * 0.28)
    rgb_out = (rgb_out - 128.0) * (1.06 if not warm else 1.12) + 128.0
    rgb_out = rgb_out * (1 - 0.50 * edge) + fg * (0.50 * edge)
    out = np.concatenate([rgb_out, alpha], axis=-1)
    grain = (rng.random((h, w)) - 0.5) * (7.0 if warm else 5.0)
    out[..., :3] = np.clip(out[..., :3] + grain[..., None] * a_flat[..., None], 0, 255)
    return _to_image(out)



# ---------------------------------------------------------------------------
# 角色身份保持 / chalk / warm / 白边修正（跨镜身份一致 + 风格可读）
# ---------------------------------------------------------------------------
WARM_TREATMENTS = ("candle", "baroque", "baroque_candle", "oil", "oil_impasto")
IDENTITY_TREATMENTS = ("ink_brush", "engrave", "woodcut", "cutout_paper", "silk", "gongbi")


def _finish_preserve_identity(styled: Image.Image, orig: Image.Image, style: dict,
                              char: dict) -> Image.Image:
    """墨/版/绢类洗白修正：暗部（L<0.35）与 hair/outfit 主色保留 ≥0.85 原色。

    风格后处理对深色头发/深色西装无差别混入纸色会导致跨镜身份不一致（老周白发、
    阿栗紫帽变浅紫、大魏黑西装变浅灰）。这里按原图亮度 + 角色主色做自适应回填；
    同时清理「原图非白、风格后变成近白」的伪白边（silk/clay 外圈白边）。
    """
    treatment = _treatment(style)
    if treatment not in IDENTITY_TREATMENTS:
        return styled
    o = np.asarray(orig.convert("RGBA")).astype(np.float32)
    s = np.asarray(styled.convert("RGBA")).astype(np.float32)
    if s.shape != o.shape:
        return styled
    rgb_o, rgb_s = o[..., :3], s[..., :3]
    alpha = s[..., 3]
    lum = (rgb_o @ np.array([0.299, 0.587, 0.114], dtype=np.float32)) / 255.0
    ap = char.get("appearance") if isinstance(char.get("appearance"), dict) else {}
    palette = _style_palette(style)
    outfit = ap.get("outfit") if isinstance(ap.get("outfit"), dict) else {}

    def _color_mask(color_hex, tol):
        c = np.array(_hx(color_hex)[:3], dtype=np.float32)
        diff = rgb_o - c
        return (diff * diff).sum(axis=-1) < (tol * tol)

    weight = np.zeros(lum.shape, dtype=np.float32)
    weight = np.maximum(weight, np.where(lum < 0.35, 0.85, 0.0))
    weight = np.maximum(weight, np.where(_color_mask(ap.get("hair") or "#1a1a1a", 46), 0.85, 0.0))
    weight = np.maximum(weight, np.where(
        _color_mask(str(outfit.get("main") or palette["accent"]), 50), 0.85, 0.0))
    weight = np.maximum(weight, np.where(
        _color_mask(str(outfit.get("accent") or palette["accent"]), 42), 0.70, 0.0))
    rgb_out = rgb_s * (1.0 - weight[..., None]) + rgb_o * weight[..., None]
    # 伪白边/白斑修正：风格输出近白但原图不是白（纸色误入角色内部/描边）
    white_bad = ((rgb_s.min(axis=-1) > 205) & (rgb_o.min(axis=-1) < 168)
                 & (alpha > 60))
    rgb_out[white_bad] = rgb_o[white_bad]
    out = np.concatenate([np.clip(rgb_out, 0, 255), s[..., 3:4]], axis=-1)
    return _to_image(out)


def _finish_chalk(styled: Image.Image, orig: Image.Image, style: dict) -> Image.Image:
    """粉笔 treatment：粉笔白填充 + 深绿描边 + 深色五官（可读）+ chalk dust 高光。

    不用 style_v5 的灰化 chalk（角色像灰幽灵）；以黑板绿纸色为底，粉笔白粉填充，
    头发/眉眼/嘴等原图暗部保留为深绿粉笔线，外轮廓加粗深绿描边，表面撒粉笔灰。
    """
    o = np.asarray(orig.convert("RGBA")).astype(np.float32)
    if o.ndim != 3:
        return styled
    rgb, alpha = o[..., :3], o[..., 3:4]
    h, w = alpha.shape[:2]
    rng = np.random.default_rng(23)
    palette = _style_palette(style)
    board = np.array(_hx(palette["paper"])[:3], dtype=np.float32)   # 黑板绿
    chalk = np.array(_hx(palette["fg"])[:3], dtype=np.float32)      # 粉笔白
    dark_line = np.array(_hx(_mix(board, "#0b1a13", 0.45))[:3], dtype=np.float32)
    lum = (rgb @ np.array([0.299, 0.587, 0.114], dtype=np.float32)) / 255.0
    a = alpha[..., 0] / 255.0
    # 1) 粉笔白填充：亮部近白，暗部略压灰（保留体积）
    fill = chalk * (0.62 + 0.32 * lum[..., None]) + rgb * 0.10
    # 2) 深色五官/头发保留为深绿粉笔线（可读性）
    dark_mask = np.clip((0.42 - lum) / 0.42, 0.0, 1.0)
    fill = fill * (1.0 - 0.72 * dark_mask[..., None]) + dark_line * (0.72 * dark_mask[..., None])
    # 3) 深绿外轮廓（加粗 7px）
    binary = Image.fromarray(np.where(a > 0.05, 255, 0).astype(np.uint8))
    grown = np.asarray(binary.filter(ImageFilter.MaxFilter(7)), dtype=np.float32) / 255.0
    shrink = np.asarray(binary.filter(ImageFilter.MinFilter(5)), dtype=np.float32) / 255.0
    outline = np.clip(grown - shrink, 0.0, 1.0)[..., None]
    fill = fill * (1.0 - 0.90 * outline) + dark_line * (0.90 * outline)
    # 4) chalk dust：内部白色颗粒 + 擦痕高光
    dust = (rng.random((h, w)) > 0.955).astype(np.float32) * a
    streak = np.clip((np.sin(np.mgrid[0:h, 0:w][0] * 2.1
                             + np.mgrid[0:h, 0:w][1] * 0.12) - 0.965) * 18.0, 0.0, 1.0) * a
    fill = fill + (dust * 0.9 + streak * 0.35)[..., None] * chalk
    out = np.concatenate([np.clip(fill, 0, 255), alpha], axis=-1)
    return _to_image(out)


def _finish_warm_correct(styled: Image.Image, style: dict) -> Image.Image:
    """烛光/油画暖调修正：底部压暗 0.28 → ~0.10，左右轮廓光 ×1.5（oil 再减 1/3）。"""
    treatment = _treatment(style)
    if treatment not in WARM_TREATMENTS:
        return styled
    arr = np.asarray(styled.convert("RGBA")).astype(np.float32)
    if arr.ndim != 3:
        return styled
    h, w = arr.shape[:2]
    yy = np.linspace(0.0, 1.0, h, dtype=np.float32)[:, None]
    zone = np.clip((yy - 0.52) / 0.48, 0.0, 1.0)          # 底部 0.52 起
    boost = 0.13 if "oil" in treatment else 0.20           # 抵消过重压暗
    rgb = arr[..., :3] * (1.0 + boost * zone[..., None])
    # 轮廓光 ×1.5（暖色）
    xx = np.linspace(-1.0, 1.0, w, dtype=np.float32)[None, :]
    rim = np.clip((np.abs(xx) - 0.52) / 0.48, 0.0, 1.0)[..., None]
    alpha = arr[..., 3:4] / 255.0
    warm = np.array([255.0, 186.0, 118.0], dtype=np.float32)
    rgb = rgb + warm * (rim * 0.11 * alpha)
    out = np.concatenate([np.clip(rgb, 0, 255), arr[..., 3:4]], axis=-1)
    return _to_image(out)


def _finish_matte_cleanup(styled: Image.Image, orig: Image.Image, style: dict) -> Image.Image:
    """半透明边缘去白边：轻微收 alpha + 边缘 RGB 预乘压暗，避免合成到深色场景时出现白圈。"""
    if _treatment(style) == "chalk":
        return styled
    arr = np.asarray(styled.convert("RGBA")).astype(np.float32)
    if arr.ndim != 3:
        return styled
    alpha = arr[..., 3]
    alpha2 = np.clip((alpha - 20.0) * (255.0 / 235.0), 0.0, 255.0)
    scale = np.sqrt(np.clip(alpha2 / 255.0, 0.0, 1.0))[..., None]
    rgb = arr[..., :3] * scale
    out = np.concatenate([rgb, alpha2[..., None]], axis=-1)
    return _to_image(out)


def _finish(img: Image.Image, style: dict, char: dict) -> Image.Image:
    """角色风格后处理：style_v5.apply_finish → 身份保持 / chalk / warm / 白边修正。"""
    style = style if isinstance(style, dict) else {}
    treatment = _treatment(style)
    # chalk：自定义黑板粉笔（不用 style_v5 的灰化版本）
    if treatment == "chalk":
        return _finish_chalk(img, img, style)
    styled: Image.Image | None = None
    try:  # 延迟导入，避免其它模块未落盘时互相阻塞
        from . import style_v5  # type: ignore

        apply_finish = getattr(style_v5, "apply_finish", None)
        if callable(apply_finish):
            result = apply_finish(img, style, kind="character")
            if isinstance(result, Image.Image) and result.size == img.size:
                if result.mode != "RGBA":
                    result = result.convert("RGBA")
                    result.putalpha(img.getchannel("A"))
                styled = result
    except Exception:  # noqa: BLE001 - 风格引擎失败时回退内置处理，不阻断渲染
        styled = None
    if styled is None:
        styled = _finish_builtin(img, style, char)
    styled = _finish_preserve_identity(styled, img, style, char)
    styled = _finish_warm_correct(styled, style)
    return _finish_matte_cleanup(styled, img, style)


# ---------------------------------------------------------------------------
# 契约函数：render_character
# ---------------------------------------------------------------------------
_RENDER_CACHE: dict[tuple, Image.Image] = {}
_RENDER_CACHE_MAX = 24


def clear_cache() -> None:
    """清空立绘缓存（角色/风格数据热更新后调用）。"""
    _RENDER_CACHE.clear()
    mod = _art_module()
    if mod is not None:
        try:
            mod.clear_cache()
        except Exception:  # noqa: BLE001
            pass


def _style_key(style: dict) -> str:
    keys = ("id", "character_treatment", "palette", "line", "technique", "finish", "texture")
    return hashlib.md5(json.dumps({k: (style or {}).get(k) for k in keys},
                                  ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()


def _char_key(char: dict) -> str:
    return hashlib.md5(json.dumps(char.get("appearance") or {}, ensure_ascii=False,
                                  sort_keys=True).encode("utf-8")).hexdigest()


# --- V5.2：AI 美术资产（assets/v5/characters/<id>/cutout.png）优先，缺失回退程序化 ---
_ART_MODULE = None
_ART_FAILED = False


def _art_module():
    """懒加载 pipeline/v5_art.py（缺失/异常时静默回退程序化立绘）。"""
    global _ART_MODULE, _ART_FAILED
    if _ART_MODULE is not None or _ART_FAILED:
        return _ART_MODULE
    try:
        from . import v5_art as mod  # type: ignore
        _ART_MODULE = mod
    except Exception:  # noqa: BLE001
        _ART_FAILED = True
        _ART_MODULE = None
    return _ART_MODULE


def art_character(char) -> dict:
    """角色 AI 美术资产信息（不存在时 exists=False；供 API/前端角色卡用）。"""
    mod = _art_module()
    try:
        spec = get_character(char)
    except Exception:  # noqa: BLE001
        spec = {"id": str(char)}
    art = spec.get("art") if isinstance(spec.get("art"), dict) else None
    if mod is None:
        return {"id": str(spec.get("id") or char), "exists": False, "error": "v5_art 不可用"}
    try:
        return mod.character_art(str(spec.get("id") or char), art)
    except Exception as exc:  # noqa: BLE001
        return {"id": str(spec.get("id") or char), "exists": False, "error": str(exc)}


def _render_art_character(char_spec: dict, style_spec: dict, size: tuple[int, int],
                          pose: str, mouth: str, blink: bool):
    """AI 立绘渲染；无资产/异常返回 None（调用方回退程序化）。"""
    mod = _art_module()
    if mod is None:
        return None
    try:
        import os as _os
        if _os.environ.get("MOTION2D_POSE_ART", "").strip().lower() in ("off", "0", "false", "no"):
            pose = "front"
        return mod.render_art_character(char_spec, style_spec, size, pose, mouth, bool(blink))
    except Exception:  # noqa: BLE001
        return None


def render_character(char, style, size=DEFAULT_SIZE, pose: str = "front",
                     mouth: str = "closed", blink: bool = False) -> Image.Image:
    """角色立绘（RGBA 透明底）。

    char: 角色 spec（或角色 id）；style: 风格 spec（或 None → DEFAULT_STYLE）；
    size: (w, h) 输出尺寸；pose: front/side/point/think；mouth: closed/a/o/e/i/u/m。
    单角色渲染目标 ≤0.2s：2x 超采样绘制 → 下采样 → 风格后处理。
    """
    char_spec = get_character(char)
    style_spec = style if isinstance(style, dict) and style else DEFAULT_STYLE
    try:
        W, H = int(size[0]), int(size[1])
    except (TypeError, ValueError, IndexError) as exc:
        raise V5CharacterError(f"size 必须是 (w, h): {size!r}") from exc
    if W < 8 or H < 8:
        raise V5CharacterError(f"size 过小: {size!r}")
    pose = str(pose or "front").lower()
    if pose not in POSES:
        pose = "front"
    # V5.5：生产可关闭姿态资产（MOTION2D_POSE_ART=off），只保留 front + 体态表演
    try:
        import os as _os
        if _os.environ.get("MOTION2D_POSE_ART", "").strip().lower() in ("off", "0", "false", "no"):
            pose = "front"
    except Exception:  # noqa: BLE001
        pass
    mouth = str(mouth or "closed").lower()
    if mouth not in MOUTH_STATES:
        mouth = "closed"
    cache_key = (str(char_spec.get("id") or ""), _char_key(char_spec),
                 str(style_spec.get("id") or ""), _style_key(style_spec),
                 pose, mouth, bool(blink), W, H)
    cached = _RENDER_CACHE.get(cache_key)
    if cached is not None:
        return cached.copy()
    # V5.2：有 AI 立绘（cutout.png）时优先走 AI 美术路径（风格化+口型/眨眼）
    art_img = _render_art_character(char_spec, style_spec, (W, H), pose, mouth, bool(blink))
    if isinstance(art_img, Image.Image):
        art_img = art_img.convert("RGBA")
        if art_img.size != (W, H):
            art_img = art_img.resize((W, H), Image.LANCZOS)
        if len(_RENDER_CACHE) >= _RENDER_CACHE_MAX:
            _RENDER_CACHE.pop(next(iter(_RENDER_CACHE)))
        _RENDER_CACHE[cache_key] = art_img
        return art_img.copy()
    # 2x 超采样上限（避免大尺寸过慢）
    scale = 2 if W * H <= 1_200_000 else 1
    SS_W, SS_H = W * scale, H * scale
    canvas = _draw_character(char_spec, style_spec, pose, mouth, bool(blink), SS_W, SS_H)
    if scale != 1:
        canvas = canvas.resize((W, H), Image.LANCZOS)
    result = _finish(canvas, style_spec, char_spec)
    if len(_RENDER_CACHE) >= _RENDER_CACHE_MAX:
        _RENDER_CACHE.pop(next(iter(_RENDER_CACHE)))
    _RENDER_CACHE[cache_key] = result
    return result.copy()


# ---------------------------------------------------------------------------
# 口型 viseme
# ---------------------------------------------------------------------------
def mouth_states() -> list[str]:
    """口型帧列表（契约固定顺序）。"""
    return list(MOUTH_STATES)


def _pinyin_of(ch: str) -> str:
    if _HAS_PYPINYIN and ch.strip():
        try:
            parts = _lazy_pinyin(ch)
            if parts:
                return str(parts[0]).lower()
        except Exception:  # noqa: BLE001
            pass
    return ""


def _final_viseme(pinyin: str) -> str:
    py = str(pinyin or "").lower().strip()
    if not py:
        return ""
    initial = ""
    if py[:2] in ("zh", "ch", "sh"):
        initial, rest = py[:2], py[2:]
    elif py[:1] in "bpmfdtnlgkhjqxrzcsyw" and len(py) > 1:
        initial, rest = py[:1], py[1:]
    else:
        rest = py
    for final, viseme in _FINAL_VISEME:
        if rest.endswith(final) or rest == final:
            return viseme
    return ""


def _fallback_viseme(ch: str) -> str:
    """无 pypinyin 时按字符确定性轮换（契约允许的兜底）。"""
    digest = hashlib.md5(ch.encode("utf-8")).hexdigest()
    value = int(digest[:8], 16)
    rotation = ("a", "o", "e", "i", "u", "m")
    return rotation[value % len(rotation)]


def viseme_from_text(text: str) -> list[dict]:
    """逐字符 → {char, pinyin, viseme}。中文用 pypinyin 声母/韵母映射，缺失时字符轮换。"""
    out: list[dict] = []
    for ch in str(text or ""):
        if ch in _PUNCT_WEIGHT or ch.isspace():
            out.append({"char": ch, "pinyin": "", "viseme": "closed"})
            continue
        pinyin = _pinyin_of(ch)
        if pinyin:
            lexeme = _final_viseme(pinyin)
            if not lexeme:
                initial = pinyin[:1] if pinyin[:1] in _INITIAL_VISEME else ""
                lexeme = _INITIAL_VISEME.get(initial) or _fallback_viseme(ch)
            out.append({"char": ch, "pinyin": pinyin, "viseme": lexeme})
        else:
            out.append({"char": ch, "pinyin": "", "viseme": _fallback_viseme(ch)})
    return out


def viseme_timeline(text: str, duration: float, lead: float = 0.10) -> list[dict]:
    """台词 → 绝对秒 viseme 时间轴 [{"t0","t1","viseme"}]。

    duration: 台词语音总时长（秒）；lead: 台词前静音偏移（秒），时间轴从 0 开始，
    最后一个 viseme 结束于 lead + duration。标点按停顿权重分摊更长时长；相邻同口型合并。
    """
    items = viseme_from_text(text)
    duration = float(duration or 0.0)
    lead = max(0.0, float(lead or 0.0))
    if not items or duration <= 0:
        return []
    weights = [float(_PUNCT_WEIGHT.get(item["char"], 1.0)) for item in items]
    total = sum(weights) or float(len(items))
    scale = duration / total
    entries: list[dict] = []
    cursor = lead
    for item, weight in zip(items, weights):
        step = weight * scale
        entries.append({"t0": round(cursor, 3), "t1": round(cursor + step, 3),
                        "viseme": item["viseme"]})
        cursor += step
    entries[-1]["t1"] = round(lead + duration, 3)
    merged: list[dict] = []
    for entry in entries:
        if merged and merged[-1]["viseme"] == entry["viseme"]:
            merged[-1]["t1"] = entry["t1"]
        else:
            merged.append(dict(entry))
    return merged


# ---------------------------------------------------------------------------
# 契约函数：render_pose_sheet
# ---------------------------------------------------------------------------
def render_pose_sheet(char, style, out_png, size=(1600, 900)) -> dict:
    """角色 contact sheet：正/侧/指/思考 + 7 口型 + 眨眼。

    返回 {"path","char_id","label","style","size","cells","poses","mouths","elapsed_sec"}。
    """
    char_spec = get_character(char)
    style_spec = style if isinstance(style, dict) and style else DEFAULT_STYLE
    W, H = int(size[0]), int(size[1])
    out_path = Path(out_png)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    palette = _style_palette(style_spec)
    paper = _hx(palette["paper"])
    bg = _mix(palette["paper"], "#ffffff", 0.25)
    sheet = Image.new("RGBA", (W, H), bg)
    draw = ImageDraw.Draw(sheet)
    margin = int(W * 0.015)
    title_h = int(H * 0.085)
    label = str(char_spec.get("label") or char_spec.get("id") or "")
    cid = str(char_spec.get("id") or "")
    era = str(char_spec.get("era") or "")
    style_id = str(style_spec.get("id") or "")
    _line(draw, [(margin, title_h - 6), (W - margin, title_h - 6)], _hx(palette["fg"]), 2)
    font_title = _font(int(title_h * 0.44), bold=True)
    draw.text((margin, int(title_h * 0.16)), f"{label}（{cid}）",
              font=font_title, fill=_hx(palette["fg"]))
    font_info = _font(int(title_h * 0.28))
    draw.text((W - margin - 520, int(title_h * 0.26)),
              f"era: {era}   style: {style_id}   lines: {_treatment(style_spec)}",
              font=font_info, fill=_mix(palette["fg"], palette["paper"], 0.35))
    import time
    started = time.perf_counter()
    poses = list(POSES)
    mouths = list(MOUTH_STATES)
    row1_y = title_h + 8
    row1_h = int(H * 0.56)
    row2_y = row1_y + row1_h + 26
    row2_h = H - row2_y - margin - 24
    gap = 8
    cell_w = (W - margin * 2 - gap * (len(poses) - 1)) // len(poses)
    for i, pose in enumerate(poses):
        x = margin + i * (cell_w + gap)
        cell = render_character(char_spec, style_spec, (cell_w - 6, row1_h - 26),
                                pose=pose, mouth="closed")
        sheet.alpha_composite(cell, (x + 3, row1_y))
        _rounded_rect(draw, (x, row1_y, x + cell_w, row1_y + row1_h - 2), 6,
                      outline=_mix(palette["fg"], palette["paper"], 0.55), width=2)
        f = _font(max(12, int(row1_h * 0.045)), bold=True)
        draw.text((x + 8, row1_y + row1_h - 24), f"pose: {pose}", font=f,
                  fill=_hx(palette["fg"]))
    mouth_items = [(mouth, False) for mouth in mouths] + [("closed", True)]
    cell_w2 = (W - margin * 2 - gap * (len(mouth_items) - 1)) // len(mouth_items)
    for i, (mouth, blink) in enumerate(mouth_items):
        x = margin + i * (cell_w2 + gap)
        cell = render_character(char_spec, style_spec, (cell_w2 - 6, row2_h - 26),
                                pose="front", mouth=mouth, blink=blink)
        sheet.alpha_composite(cell, (x + 3, row2_y))
        _rounded_rect(draw, (x, row2_y, x + cell_w2, row2_y + row2_h - 2), 6,
                      outline=_mix(palette["fg"], palette["paper"], 0.55), width=2)
        f = _font(max(10, int(row2_h * 0.055)))
        text = "blink" if blink else f"mouth: {mouth}"
        draw.text((x + 6, row2_y + row2_h - 22), text, font=f, fill=_hx(palette["fg"]))
    elapsed = round(time.perf_counter() - started, 3)
    sheet.convert("RGB").save(out_path)
    return {"path": str(out_path), "char_id": cid, "label": label, "style": style_id,
            "size": [W, H], "cells": len(poses) + len(mouth_items),
            "poses": poses, "mouths": mouths, "elapsed_sec": elapsed,
            "background": "#%02x%02x%02x" % paper[:3]}


# ---------------------------------------------------------------------------
# CLI（调试用：python -m pipeline.characters_v5 --char zhuge --out tmp/x.png）
# ---------------------------------------------------------------------------
def main(argv: list[str] | None = None) -> int:
    import argparse
    import sys

    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass
    ap = argparse.ArgumentParser(prog="python -m pipeline.characters_v5",
                                description="V5 角色立绘/口型调试")
    ap.add_argument("--char", default=None, help="角色 id（默认全部）")
    ap.add_argument("--style", default=None, help="风格 id（contract/v5/styles/<id>.json）")
    ap.add_argument("--out", default=None, help="输出 png 或目录")
    ap.add_argument("--size", default="512x768")
    ap.add_argument("--pose", default="front")
    ap.add_argument("--mouth", default="closed")
    ap.add_argument("--sheet", action="store_true", help="输出 pose sheet")
    args = ap.parse_args(argv)
    try:
        w, h = (int(v) for v in args.size.lower().split("x"))
    except ValueError:
        raise SystemExit("--size 形如 512x768")
    style_spec = default_style()
    if args.style:
        style_path = V5_ROOT / "styles" / f"{args.style}.json"
        if style_path.is_file():
            style_spec = _read_json(style_path)
        else:
            style_spec = dict(default_style(), id=args.style)
    chars = load_characters()
    targets = [args.char] if args.char else list(chars)
    out_dir = Path(args.out) if args.out else (REPO_ROOT / "tmp" / "v5_characters")
    out_dir.mkdir(parents=True, exist_ok=True)
    import time
    for cid in targets:
        if cid not in chars:
            print(f"[skip] 未知角色 {cid}")
            continue
        started = time.perf_counter()
        if args.sheet:
            path = out_dir / f"{cid}_sheet.png"
            info = render_pose_sheet(chars[cid], style_spec, path, (1600, 900))
            print(f"{cid}: {path} ({info['elapsed_sec']}s)")
        else:
            img = render_character(chars[cid], style_spec, (w, h), pose=args.pose,
                                   mouth=args.mouth)
            path = out_dir / f"{cid}_{args.pose}_{args.mouth}.png"
            img.save(path)
            print(f"{cid}: {path} ({round(time.perf_counter() - started, 3)}s)"
                  + (" [fallback viseme: 无 pypinyin]" if not _HAS_PYPINYIN else ""))
    print(json.dumps({"ok": True, "characters": len(targets),
                      "pypinyin": _HAS_PYPINYIN, "out": str(out_dir)},
                     ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
