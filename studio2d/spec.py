"""studio2d.spec - 角色 Spec 读取、派生调色板与绘制参数。"""

from __future__ import annotations

import json
import math

from .shapekit import hex2rgb, rgb2hex, mix, lighten, darken, desaturate, col

DEFAULT_OUTFIT = {"type": "suit", "main": "#2F3A4F", "accent": "#F5C518", "shirt": "#F4F1E8"}

DEFAULT_APPEARANCE = {
    "gender": "neutral", "age": 30, "height_ratio": 1.0, "head_scale": 1.0,
    "chibi": 0.2, "skin": "#F2D6C3", "hair_color": "#2B2B33",
    "hair_style": "short_swept", "eye_color": "#2B2B33", "eye_size": 1.0,
    "brow": "thick", "facial_hair": "none", "outfit": DEFAULT_OUTFIT,
    "accessories": [], "marks": [],
}

STYLE_ALIASES = {
    "auto": "auto",
    "2d_cel": "2d_cel", "2dcel": "2d_cel", "anime_cel": "2d_cel", "anime": "2d_cel",
    "cel": "2d_cel", "2d_paint": "2d_cel", "2dpaint": "2d_cel",
    "3d_toon": "2d_cel", "3d_cel": "2d_cel",
    "guoman": "guoman", "guo_man": "guoman", "cn": "guoman",
}

STYLE_LABELS = {
    "2d_cel": "2D CEL / 日系赛璐璐",
    "guoman": "GUOMAN / 国漫暖色",
}


class Character:
    def __init__(self, spec, style):
        self.spec = spec
        self.id = str(spec.get("id", "char"))
        self.name = str(spec.get("name", self.id))
        self.title = str(spec.get("title", ""))
        self.persona = str(spec.get("persona", ""))
        self.accent = str(spec.get("accent_color", "#F5C518"))
        self.style = style
        self.style_label = STYLE_LABELS.get(style, style)
        ap = dict(DEFAULT_APPEARANCE)
        ap.update(spec.get("appearance") or {})
        self.appearance = ap
        self.gender = str(ap.get("gender", "neutral"))
        self.age = int(ap.get("age", 30))
        self.chibi = max(0.0, min(0.5, float(ap.get("chibi", 0.2))))
        self.head_scale = float(ap.get("head_scale", 1.0)) * (1.0 + self.chibi * 0.55)
        self.eye_scale = float(ap.get("eye_size", 1.0)) * (1.00 + 0.36 * self.chibi)
        self.brow = str(ap.get("brow", "thick"))
        self.facial_hair = str(ap.get("facial_hair", "none"))
        self.hair_style = str(ap.get("hair_style", "short_swept"))
        self.accessories = list(ap.get("accessories") or [])
        self.marks = list(ap.get("marks") or [])
        outfit = dict(DEFAULT_OUTFIT)
        outfit.update(ap.get("outfit") or {})
        self.outfit = outfit
        self.skin = str(ap.get("skin", "#F2D6C3"))
        self.hair = str(ap.get("hair_color", "#2B2B33"))
        self.eye = str(ap.get("eye_color", "#2B2B33"))
        self.warm = self.style == "guoman"
        self._derive()

    def _derive(self):
        accent = self.accent
        skin = self.skin
        hair = self.hair
        eye = self.eye
        main = str(self.outfit.get("main", "#2F3A4F"))
        accent2 = str(self.outfit.get("accent", accent))
        shirt = str(self.outfit.get("shirt", "#F4F1E8"))
        self.outfit_main = main
        self.outfit_accent = accent2
        self.outfit_shirt = shirt
        self.skin_sh = mix(skin, (150.0, 100.0, 96.0), 0.30)
        self.skin_hi = lighten(skin, 0.22)
        self.skin_deep = mix(skin, (128.0, 78.0, 78.0), 0.45)
        self.hair_sh = darken(hair, 0.28)
        lum = (0.299 * hex2rgb(hair)[0] + 0.587 * hex2rgb(hair)[1]
               + 0.114 * hex2rgb(hair)[2]) / 255.0
        if lum < 0.45:
            self.hair_hi = mix(lighten(hair, 0.19), accent, 0.03)
            self.hair_hi2 = mix(lighten(hair, 0.30), accent, 0.04)
        else:
            self.hair_hi = mix(lighten(hair, 0.26), accent, 0.10)
            self.hair_hi2 = mix(lighten(hair, 0.40), accent, 0.14)
        line = darken(mix(hair, accent, 0.10), 0.66)
        if self.warm:
            line = mix(line, (62.0, 40.0, 34.0), 0.32)
        self.line = line
        self.line_soft = mix(line, skin, 0.35)
        self.eye_light = mix(lighten(eye, 0.50), accent, 0.10)
        self.eye_dark = darken(eye, 0.42)
        self.eye_white = "#FBFDFF"
        self.eye_lash = darken(mix(eye, (20.0, 18.0, 24.0), 0.55), 0.35)
        self.shirt = shirt
        self.shirt_sh = darken(shirt, 0.14)
        self.outfit_sh = darken(main, 0.20)
        self.outfit_hi = lighten(main, 0.16)
        kind = str(self.outfit.get("type", "suit"))
        if kind == "hoodie":
            self.pants = "#3E4C6E"
        elif kind == "jacket":
            self.pants = "#252A33"
        else:
            self.pants = darken(main, 0.16)
        self.shoe = darken(main, 0.55)
        if kind == "hoodie":
            self.shoe = "#2A3038"
        self.lip = mix(skin, (176.0, 92.0, 96.0), 0.55)
        self.mouth_in = "#6A3036"
        self.tongue = "#C9676A"
        self.eye_mouth_line = darken(mix(skin, self.line, 0.55), 0.15)
        self.brow_color = mix(hair, self.line, 0.35)
        if self.gender == "female":
            self.blush = mix(skin, (240.0, 120.0, 140.0), 0.45)
        else:
            self.blush = mix(skin, (230.0, 150.0, 130.0), 0.34)
        self.teeth = "#FFFDF8"
        self.palette = {
            "accent": self.accent,
            "skin": self.skin,
            "skin_shadow": rgb2hex(self.skin_sh),
            "hair": self.hair,
            "hair_highlight": rgb2hex(self.hair_hi),
            "eye": self.eye,
            "outfit_main": self.outfit_main,
            "outfit_accent": self.outfit_accent,
            "outfit_shirt": self.outfit_shirt,
        }

    @property
    def is_female(self):
        return self.gender == "female"


def _resolve_style(spec, style_arg):
    name = str(style_arg or "auto").strip().lower()
    if name not in STYLE_ALIASES:
        raise ValueError("unknown style: %s (support: auto, 2d_cel, anime_cel, guoman)" % style_arg)
    resolved = STYLE_ALIASES[name]
    if resolved == "auto":
        variants = [str(v).lower() for v in ((spec.get("style") or {}).get("variants") or [])]
        primary = str((spec.get("style") or {}).get("primary", "")).lower()
        candidates = [primary] + variants
        if "guoman" in candidates:
            return "guoman"
        return "2d_cel"
    return resolved


def load_character(path, style=None):
    with open(path, "r", encoding="utf-8") as f:
        spec = json.load(f)
    return build_character(spec, style=style)


def build_character(spec, style=None):
    resolved = _resolve_style(spec, style)
    return Character(spec, resolved)
