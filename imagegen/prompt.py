# -*- coding: utf-8 -*-
"""从 contract/characters/*.json 的角色 spec 拼 SDXL 动漫 tag。

设计原则
--------
- appearance 字段（gender/hair/eye/outfit/accessories/marks/facial_hair/...）全部映射成
  Animagine XL 4.0 友好的 Danbooru 风 tag，而不是把原始 JSON 直接塞给模型；
- 颜色（#RRGGBB）映射到动漫模型认识的颜色词（black/purple/navy/...），用最近邻匹配；
- 构图按用途（compositon）切换：portrait / front / side / three_quarter / expression / scene；
- --style 提供 anime / illustrious / guoman 三套前缀与 negative 微调；
- 固定 seed 家族保证同一角色多图可复现、可拼设定图。
"""
from __future__ import annotations

import json
import math
import os
import re

# --------------------------------------------------------------------------- #
# 颜色名（最近邻）
# --------------------------------------------------------------------------- #
COLOR_TABLE = [
    ("black", (20, 20, 24)),
    ("black", (43, 43, 51)),
    ("dark grey", (60, 62, 70)),
    ("grey", (110, 112, 120)),
    ("silver", (185, 190, 200)),
    ("white", (245, 245, 245)),
    ("cream", (244, 241, 232)),
    ("brown", (110, 75, 50)),
    ("dark brown", (70, 48, 35)),
    ("light brown", (165, 120, 80)),
    ("blonde", (232, 205, 120)),
    ("gold", (245, 197, 24)),
    ("orange", (235, 140, 60)),
    ("red", (205, 60, 60)),
    ("coral", (255, 120, 110)),
    ("salmon", (235, 150, 140)),
    ("crimson", (170, 45, 55)),
    ("pink", (235, 150, 175)),
    ("magenta", (200, 80, 150)),
    ("purple", (140, 90, 175)),
    ("lavender", (170, 150, 205)),
    ("violet", (110, 70, 160)),
    ("navy blue", (47, 58, 79)),
    ("blue", (58, 111, 224)),
    ("sky blue", (90, 200, 250)),
    ("cyan", (80, 200, 220)),
    ("teal", (45, 130, 130)),
    ("green", (80, 160, 90)),
    ("dark green", (40, 90, 60)),
    ("olive", (110, 110, 70)),
    ("yellow", (240, 220, 80)),
    ("beige", (220, 200, 170)),
    ("tan", (210, 180, 140)),
    ("peach", (250, 200, 170)),
]

COLOR_ZH = {
    "black": "黑色", "dark grey": "深灰", "grey": "灰", "silver": "银", "white": "白",
    "cream": "米白", "brown": "棕", "dark brown": "深棕", "light brown": "浅棕",
    "blonde": "金", "gold": "金", "orange": "橙", "red": "红", "crimson": "绯红",
    "pink": "粉", "magenta": "洋红", "purple": "紫", "lavender": "薰衣草紫",
    "violet": "紫罗兰", "navy blue": "藏青", "blue": "蓝", "sky blue": "天蓝",
    "cyan": "青", "teal": "青绿", "green": "绿", "dark green": "墨绿",
    "olive": "橄榄绿", "yellow": "黄", "beige": "米", "tan": "棕褐", "peach": "蜜桃",
}


def hex2rgb(s: str):
    s = str(s or "").lstrip("#")
    if len(s) == 3:
        s = "".join(c * 2 for c in s)
    if len(s) != 6:
        return None
    try:
        return tuple(int(s[i:i + 2], 16) for i in (0, 2, 4))
    except ValueError:
        return None


def color_name(value, default="dark") -> str:
    """#RRGGBB -> 最近的动漫颜色词；也接受已是颜色名的字符串。"""
    if not value:
        return default
    rgb = hex2rgb(value)
    if rgb is None:
        return str(value).strip().lower()
    best, best_d = default, 1e9
    for name, ref in COLOR_TABLE:
        d = sum((rgb[i] - ref[i]) ** 2 for i in range(3))
        if d < best_d:
            best, best_d = name, d
    return best


def color_zh(value) -> str:
    return COLOR_ZH.get(color_name(value), "主色")


# --------------------------------------------------------------------------- #
# appearance 字段 -> tag
# --------------------------------------------------------------------------- #
def _gender_tags(app: dict) -> list:
    g = str(app.get("gender", "neutral")).lower()
    age = int(app.get("age") or 30)
    tags = []
    if g == "male":
        tags.append("1boy")
        tags.append("male")
        if age >= 50:
            tags.append("middle-aged man")
        elif age >= 40:
            tags.append("mature male")
        elif age >= 28:
            tags.append("adult male")
        else:
            tags.append("young adult male")
    elif g == "female":
        tags.append("1girl")
        tags.append("female")
        if age >= 45:
            tags.append("mature female")
        elif age >= 28:
            tags.append("adult female")
        else:
            tags.append("young adult female")
    else:
        tags.append("androgynous")
    return tags


HAIR_STYLE_TAGS = {
    "short_swept": ["short hair", "swept bangs"],
    "spiky": ["spiky hair"],
    "bob": ["bob cut"],
    "twin_tail": ["twintails", "long hair"],
    "ponytail": ["ponytail", "long hair"],
    "long_straight": ["long straight hair"],
    "bald": ["bald"],
    "bun": ["hair bun"],
}

FACIAL_HAIR_TAGS = {
    "stubble": ["stubble"],
    "goatee": ["goatee"],
    "mustache": ["mustache"],
    "beard": ["beard"],
    "none": [],
}

BROW_TAGS = {
    "thick": ["thick eyebrows"],
    "soft": ["soft eyebrows"],
    "thin": ["thin eyebrows"],
}

ACCESSORY_TAGS = {
    "glasses": ["glasses"],
    "watch": ["wristwatch"],
    "headphones": ["headphones"],
    "earring": ["earrings"],
}

OUTFIT_BASE = {
    "suit": ["business suit", "necktie", "white shirt", "formal"],
    "hoodie": ["hoodie", "casual"],
    "jacket": ["jacket", "collared shirt"],
    "shirt": ["shirt"],
    "dress": ["dress"],
    "coat": ["coat", "formal"],
}

MARK_TAGS = {
    "warm_smile": ["slight smile", "warm smile"],
    "star_clip": ["star hair ornament", "hair clip"],
    "scar_brow": ["scar on eyebrow"],
    "glasses": ["glasses"],
    "stubble": ["stubble"],
}


def appearance_tags(app: dict) -> list:
    """appearance dict -> 有序 tag 列表（去重保序）。"""
    tags = []
    tags += _gender_tags(app)

    hair_color = color_name(app.get("hair_color"))
    hair_style = HAIR_STYLE_TAGS.get(str(app.get("hair_style", "")), [])
    if "bald" in hair_style:
        tags.append("bald")
    else:
        tags.append(hair_color + " hair")
    tags += hair_style

    eye_color = color_name(app.get("eye_color"), default="dark")
    tags.append(eye_color + " eyes")
    eye_size = float(app.get("eye_size") or 1.0)
    if eye_size >= 1.15:
        tags.append("big eyes")
    elif eye_size <= 0.9:
        tags.append("narrow eyes")

    tags += BROW_TAGS.get(str(app.get("brow", "")), [])
    tags += FACIAL_HAIR_TAGS.get(str(app.get("facial_hair", "")), [])

    outfit = app.get("outfit") or {}
    otype = str(outfit.get("type", "shirt"))
    main_c = color_name(outfit.get("main"), default="dark")
    acc_c = color_name(outfit.get("accent"), default="gold")
    tags.append(main_c + " " + otype)
    tags += OUTFIT_BASE.get(otype, [])
    tags.append(acc_c + " accents")

    for a in app.get("accessories") or []:
        tags += ACCESSORY_TAGS.get(str(a), [])
    for m in app.get("marks") or []:
        tags += MARK_TAGS.get(str(m), [])

    # 角色个性 -> 表情/气质 tag（persona 是中文，做关键词粗映射）
    return _dedup(tags)


def persona_tags(spec: dict) -> list:
    persona = str(spec.get("persona") or "") + " " + str(spec.get("title") or "")
    tags = []
    if re.search(r"冷幽默|毒舌|保守", persona):
        tags += ["confident expression", "smirk"]
    if re.search(r"乐观|语速快", persona):
        tags += ["cheerful", "smile"]
    if re.search(r"主持人|控场", persona):
        tags += ["confident", "looking at viewer"]
    if re.search(r"导演", persona):
        tags += ["serious", "authoritative"]
    return _dedup(tags)


# --------------------------------------------------------------------------- #
# 风格预设 / negative / 构图
# --------------------------------------------------------------------------- #
STYLE_PRESETS = {
    "anime": {
        "prefix": ["masterpiece", "best quality", "anime style", "solo"],
        "suffix": ["clean lineart", "vibrant colors", "detailed face"],
        "negative": [],
    },
    "illustrious": {
        "prefix": ["masterpiece", "best quality", "very aesthetic", "absurdres",
                   "newest", "anime style", "solo"],
        "suffix": ["clean lineart", "highly detailed", "beautiful lighting"],
        "negative": ["worst quality", "low quality", "jpeg artifacts"],
    },
    "guoman": {
        "prefix": ["masterpiece", "best quality", "chinese animation style",
                   "guofeng", "anime style", "solo"],
        "suffix": ["clean lineart", "elegant color palette", "detailed face"],
        "negative": ["western comic", "3d render"],
    },
    "realistic": {
        "prefix": ["photorealistic", "raw photo", "masterpiece", "best quality",
                   "ultra realistic", "solo", "detailed skin texture",
                   "professional photography"],
        "suffix": ["sharp focus", "detailed face", "detailed makeup",
                   "cinematic lighting", "8k"],
        "negative": ["anime", "cartoon", "illustration", "2d", "cel shading",
                     "flat colors", "sketch", "painting", "cgi", "3d render",
                     "doll", "plastic skin"],
    },
    "stylized_25d": {
        "prefix": ["masterpiece", "best quality", "2.5D anime character",
                   "3D anime render", "cel-shaded CG", "stylized game character",
                   "ultra detailed character art", "detailed skin shading",
                   "korean beauty", "elegant fashion", "solo"],
        "suffix": ["highly detailed face", "detailed makeup", "detailed costume",
                   "soft cinematic lighting", "depth of field", "8k",
                   "sharp focus", "beautiful detailed eyes"],
        "negative": ["photorealistic", "real human", "photo", "realistic photo",
                     "live action", "photography", "realistic skin pores",
                     "anime screencap", "flat 2d", "cartoon", "chibi",
                     "sketch", "low detail", "doll", "plastic skin", "deformed",
                     "extra limbs", "bad hands"],
    },
}

NEGATIVE_BASE = ["lowres", "bad anatomy", "bad hands", "text", "watermark",
                 "extra fingers", "deformed", "realistic"]

COMPOSITIONS = {
    # 用途 -> 构图 tag
    "portrait": ["upper body", "looking at viewer", "centered composition",
                 "studio lighting", "dark background"],
    "front": ["full body", "solo", "front view", "standing", "arms at sides",
              "simple background", "white background", "plain background"],
    "side": ["full body", "solo", "from side", "profile", "standing",
             "simple background", "white background", "plain background"],
    "three_quarter": ["full body", "solo", "three-quarter view", "standing",
                      "simple background", "white background", "plain background"],
    "expressions": ["upper body", "looking at viewer", "studio lighting", "dark background"],
    "scene": ["cinematic anime film still", "medium shot", "dramatic lighting",
              "dark studio background", "depth of field"],
    # V45：关键帧用「环境优先」构图（角色在画面中但背景必须成景，避免 IP-Adapter 把参考立绘的
    # 纯色/黑色底 + 光效带进来，出现「脸很大、背景抽象」的问题）
    "scene_wide": ["cinematic anime film still", "medium wide shot", "wide composition",
                   "environment visible", "detailed background", "cinematic lighting",
                   "depth of field"],
}

EXPRESSION_TAGS = {
    "neutral": ["neutral expression", "calm"],
    "happy": ["happy", "smiling", "open mouth"],
    "surprised": ["surprised", "wide eyes", "open mouth"],
    "thinking": ["thinking", "hand on chin", "thoughtful"],
}

# 三视图 / 表情 / 立绘的额外 negative（避免多图拼贴、避免构图被拉歪）
COMPOSITION_NEGATIVES = {
    "front": ["multiple views", "split screen", "cropped"],
    "side": ["multiple views", "split screen", "cropped"],
    "three_quarter": ["multiple views", "split screen", "cropped"],
    "portrait": ["full body", "multiple characters"],
    "expressions": ["full body", "multiple characters"],
    "scene": ["multiple characters", "text box"],
}


def _dedup(tags: list) -> list:
    seen = set()
    out = []
    for t in tags:
        t = str(t).strip().lower()
        if not t or t in seen:
            continue
        seen.add(t)
        out.append(t)
    return out


def build_prompt(spec: dict, style: str = "anime", prompt_extra: str = "",
                 composition: str = "portrait", expression: str = None,
                 view: str = None) -> dict:
    """returns {positive, negative, tags, style, composition}.
    view 用于 three_view 的 front/side/three_quarter（优先于 composition）。
    """
    if style not in STYLE_PRESETS:
        raise ValueError("未知 style: %s（可选 %s）" % (style, ", ".join(STYLE_PRESETS)))
    comp = view or composition
    if comp not in COMPOSITIONS:
        raise ValueError("未知 composition: %s" % comp)
    preset = STYLE_PRESETS[style]
    app = spec.get("appearance") or {}

    tags = list(preset["prefix"])
    tags += appearance_tags(app)
    tags += persona_tags(spec)
    tags += COMPOSITIONS[comp]
    if expression:
        tags += EXPRESSION_TAGS.get(expression, [])
    if prompt_extra:
        for t in re.split(r"[,，]", prompt_extra):
            if t.strip():
                tags.append(t.strip())
    tags += preset["suffix"]
    tags = _dedup(tags)

    neg_base = [t for t in NEGATIVE_BASE
                if not (style == "realistic" and t == "realistic")]
    negative = _dedup(neg_base + preset["negative"]
                      + COMPOSITION_NEGATIVES.get(comp, []))
    return {
        "positive": ", ".join(tags),
        "negative": ", ".join(negative),
        "tags": tags,
        "style": style,
        "composition": comp,
    }


# --------------------------------------------------------------------------- #
# spec 读取
# --------------------------------------------------------------------------- #
def load_spec(path: str) -> dict:
    with open(path, "r", encoding="utf-8-sig") as f:
        spec = json.load(f)
    if not isinstance(spec, dict) or "id" not in spec:
        raise ValueError("角色 spec 需要包含 id 字段: %s" % path)
    return spec


def spec_accent(spec: dict) -> str:
    return str(spec.get("accent_color") or "#F5C518")


if __name__ == "__main__":
    # 自测：打印三个角色的 prompt
    import glob
    base = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "contract", "characters")
    for p in sorted(glob.glob(os.path.join(base, "*.json"))):
        s = load_spec(p)
        r = build_prompt(s, style="anime", composition="portrait")
        print("=" * 20, s["id"])
        print("POS:", r["positive"])
        print("NEG:", r["negative"])
