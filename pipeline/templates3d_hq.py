# -*- coding: utf-8 -*-
"""V16 高清样片：1024×576 语义 T2VA（保留运镜/远景）+ 1024×576 ref2va 原生口型（带动镜头提示）。

用法：
    python -m pipeline.templates3d_hq --template tonight_show [--skip-t2va] [--skip-ref2va]

流程：
1. 备份当前 768 版镜头到 outputs/_archive_v15_ref2va_768/<tid>/shots/
2. 每镜先用语义 prompt 做 T2VA 1024×576（画面 + 运镜 + 远景）
3. 有台词镜头：取 T2VA 首帧 + 该镜 TTS 音频 + 「带运镜/中景」提示词 → ref2va 1024×576 原生口型
4. 无台词镜头：直接用 T2VA 1024×576
5. 剪辑 + 海报
"""
from __future__ import annotations

import argparse
import json
import math
import pathlib
import random
import re
import shutil
import subprocess
import sys
import time
import zlib

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from pipeline import cinema3d_local as local  # noqa: E402
from pipeline import templates3d as t3  # noqa: E402

FFMPEG = REPO / "tools/node_modules/ffmpeg-static/ffmpeg.exe"
H3_REF = REPO / "integrations/comfyui/run_h3_ref.py"
WORK = REPO / "tmp/v16"
LOG = WORK / "hq_run.log"

MOTION_WORDS = {
    "push_in": "the camera slowly pushes in",
    "pull_out": "the camera slowly pulls back revealing the space",
    "pan_right": "the camera pans smoothly across the scene",
    "pan_left": "the camera pans smoothly across the scene",
    "dolly": "the camera dollies slowly through the space",
    "crane_up": "the camera rises on a slow crane move",
    "crane_down": "the camera descends on a slow crane move",
    "rack_focus": "the camera racks focus between foreground and background",
    "orbit": "the camera orbits slowly around the subject",
    "handheld": "the camera drifts with subtle handheld movement",
}

STYLE_2D_PREFIX = ("2D anime, cel-shaded, flat colors, clean line art, "
                   "hand-drawn animation, anime screenshot")
STYLE_2D_QUALITY = ("2D anime film quality, clean line art, flat cel-shaded colors, "
                    "hand-drawn animation, anime screenshot")
STYLE_2D_BROLL = ("anime key visual, Japanese TV anime screenshot, clean lineart, "
                  "flat cel-shaded colors, detailed background, no 3D, no cgi")
STYLE_2D_CACHE = "_2d_v45"

#: V45：2D 风格子类型 → 独立 checkpoint + 采样参数 + IP-Adapter 权重 + 提示词
#: - ckpt：每种风格一个真实 SDXL checkpoint（不同底模，画风差异来自模型而非只靠提示词）
#: - ip_strength / ip_end / ip_weight_type：V45 把 0.75 的「锁脸权重」降下来，并改成
#:   style transfer，让背景在采样后段自由生成（脸稳、背景不再是一团抽象色块）
#: - prompt：风格句（关键帧 / B-roll / ref2va 共用）；background：只压关键帧的环境句
STYLE_2D_STYLES = {
    "jp_anime": {
        "label": "日漫电影感",
        "imagegen": "anime",
        "ckpt": "animagine-xl-4.0.safetensors",
        "cfg": 5.5, "steps": 30, "sampler": "euler_ancestral", "scheduler": "normal",
        "ip_strength": 0.50, "ip_end": 0.58, "ip_weight_type": "style transfer",
        "prompt": ("Japanese anime film still, soft cel shading, thin detailed lineart, "
                   "muted cinematic color"),
        "background": ("detailed anime background art, painted environment, layered depth, "
                       "atmospheric perspective"),
    },
    "guoman": {
        "label": "国漫国风",
        "imagegen": "guoman",
        "ckpt": "AAM_XL_Anime_Mix.safetensors",
        "cfg": 6.0, "steps": 30, "sampler": "euler_ancestral", "scheduler": "normal",
        "ip_strength": 0.48, "ip_end": 0.56, "ip_weight_type": "style transfer",
        "prompt": ("Chinese guofeng animation film still, elegant ink-wash color accents, "
                   "Chinese architecture, elegant color"),
        "background": ("guofeng environment art, Chinese wooden lattice, lanterns, "
                       "ink-wash atmosphere, painted background"),
    },
    "retro_cel": {
        "label": "复古赛璐璐",
        "imagegen": "illustrious",
        "ckpt": "Illustrious-XL-v1.0.safetensors",
        "cfg": 5.5, "steps": 32, "sampler": "euler_ancestral", "scheduler": "normal",
        "ip_strength": 0.55, "ip_end": 0.62, "ip_weight_type": "style transfer",
        "prompt": ("90s retro cel anime, bold clean outlines, limited palette, "
                   "vintage film grain, hand-painted cel"),
        "background": ("90s hand-painted anime background art, painted city night, "
                       "soft gradients, sharp background detail"),
        # Illustrious 对“成熟”不敏感，会把人画年轻；这风格额外压年龄
        "age_extra": ("older mature adult, 45 years old, weathered skin, faint wrinkles, "
                      "sharp cheekbones, mature male face"),
    },
    "arcane_cinematic": {
        "label": "赛博电影感",
        "imagegen": "anime",
        # Arcane / 双城之战那类「绘画感半写实动画」：DreamShaper + arcane LoRA
        "ckpt": "DreamShaperXL_Turbo_v2_1.safetensors",
        "lora": "arcane-style-xl.safetensors",
        "lora_strength": 0.85,
        "cfg": 4.0, "steps": 16, "sampler": "dpmpp_sde", "scheduler": "karras",
        "ip_strength": 0.45, "ip_end": 0.55, "ip_weight_type": "style transfer",
        "prompt": ("arcane style, league of legends painted animation, cinematic semi-realistic "
                   "animation film still, painterly brushwork, volumetric light, "
                   "teal and amber palette"),
        "background": ("detailed painted background, cinematic depth, atmospheric perspective, "
                       "painterly environment"),
        # 这条风格不能沿用「平涂/赛璐璐」前缀，H3 视频也要写「绘画感半写实」
        "prefix": ("cinematic painted animation, semi-realistic stylized film still, "
                   "painterly textures, volumetric lighting, film grain"),
        "quality": ("cinematic animated film quality, painterly shading, detailed faces, "
                    "volumetric light, no flat cel shading"),
        "art_style": "painted semi-realistic animation art style",
        "finish": "painterly brushwork, volumetric light, semi-realistic shading",
        "negative_extra": ("multiple characters, 2boys, twin, close-up, photo, 3d render, "
                           "cartoon outline, flat colors"),
    },
    "soft_watercolor": {
        "label": "吉卜力治愈系",
        "imagegen": "anime",
        # DreamShaper XL（turbo）：SDXL 通用/插画底模，手绘/水彩质感比 AAM 更接近吉卜力
        "ckpt": "DreamShaperXL_Turbo_v2_1.safetensors",
        "cfg": 4.0, "steps": 16, "sampler": "dpmpp_sde", "scheduler": "karras",
        "ip_strength": 0.48, "ip_end": 0.55, "ip_weight_type": "style transfer",
        "prompt": ("studio ghibli inspired watercolor anime, gouache painting, hand-painted, "
                   "warm golden hour palette, gentle healing mood"),
        "background": ("hand-painted watercolor background, warm greens and amber, "
                       "lush painterly detail, soft dappled light"),
        # turbo 底模容易补出第二个人，额外压人数
        "negative_extra": ("2boys, multiple characters, twin, clone, duplicate person, "
                           "extra person, mirror image"),
    },
}


def resolve_2d_style(style_id: str | None) -> str:
    key = str(style_id or "jp_anime").strip().lower()
    return key if key in STYLE_2D_STYLES else "jp_anime"


def apply_2d_style(prompt: str, broll: bool = False, style_id: str = "jp_anime") -> str:
    """V42/V43/V44：把 3D/T2VA 提示词改写为指定 2D 风格。"""
    style_key = resolve_2d_style(style_id)
    style = STYLE_2D_STYLES[style_key]
    prefix = str(style.get("prefix") or STYLE_2D_PREFIX)
    quality = str(style.get("quality") or STYLE_2D_QUALITY)
    text = str(prompt or "")
    text = re.sub(r"3D animated feature film quality[^.]*\.",
                  quality + ".", text, flags=re.IGNORECASE)
    text = re.sub(r"(?:3D|3d)\s+toon", "2D anime", text)
    text = re.sub(r"stylized\s+(?:3D|3d)\s+animated", "stylized 2D anime", text)
    text = re.sub(r"(?:3D|3d)\s+animated", "2D anime", text)
    text = re.sub(r"(?:3D|3d)\s+animated\s+art\s+style", "2D anime art style", text)
    text = re.sub(r"\b(?:3D|3d)\b", "2D", text)
    text = re.sub(r"\brich physical materials,\s*", "", text, flags=re.IGNORECASE)
    text = re.sub(r"volumetric light", "flat cel-shaded lighting", text, flags=re.IGNORECASE)
    text = prefix + ". " + style["prompt"] + ". " + text
    if broll:
        text += (" " + STYLE_2D_BROLL
                 + " detailed background, environment art, rich scene detail.")
    return text


# --------------------------------------------------------------------------- #
# V43：2D 对话镜头关键帧（SDXL anime + IP-Adapter 锁脸）
# --------------------------------------------------------------------------- #
KEYFRAMES = WORK / "keyframes"
#: V45.2：2D 关键帧「抽卡」——多 seed 候选 + 人工/自动选优
KEYFRAME_PICKS = WORK / "keyframe_picks.json"


def _character_contract(cid: str) -> dict:
    for path in (
        REPO / "contract" / "characters" / f"{cid}.json",
        REPO / "contract" / "v5" / "characters" / f"{cid}.json",
        REPO / "contract" / "v5" / "custom" / "characters" / f"{cid}.json",
    ):
        if path.is_file():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    return data
            except (OSError, ValueError):
                continue
    return {}


def _shot_character_id(tid: str, shot: dict) -> str:
    """用 episode.json 的 shots 映射 shot id → 角色 id（brief 只保留 prompt）。"""
    try:
        episode = t3.load_episode(tid)
    except SystemExit:
        return ""
    for item in episode.get("shots") or []:
        if str(item.get("id")) == str(shot.get("id")):
            return str(item.get("character") or "")
    return ""


def _imagegen_spec_for(cid: str, spec_dir: pathlib.Path) -> pathlib.Path:
    """写 imagegen spec（优先精选 spec，否则用角色合同构造），返回 spec 路径。"""
    contract = _character_contract(cid)
    curated = REPO / "tmp" / "v52_art" / "specs" / f"{cid}.json"
    spec: dict = {}
    if curated.is_file():
        try:
            loaded = json.loads(curated.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                spec = loaded
        except (OSError, ValueError):
            spec = {}
    if not spec:
        spec = {
            "id": cid,
            "name": str(contract.get("name") or contract.get("label") or cid),
            "title": str(contract.get("title") or ""),
            "persona": str(contract.get("persona") or ""),
            "accent_color": str(contract.get("accent_color") or "#F5C518"),
            "style": contract.get("style") or {"primary": "2d_cel", "variants": ["2d_cel"]},
            "appearance": contract.get("appearance") or {},
            "extra_tags": ["anime", "cel-shaded"],
        }
    spec["id"] = cid
    if contract.get("name") or contract.get("label"):
        spec["name"] = str(contract.get("name") or contract.get("label"))
    spec_dir.mkdir(parents=True, exist_ok=True)
    path = spec_dir / f"{cid}.json"
    path.write_text(json.dumps(spec, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


#: V45：角色气质关键词（中文合同 persona）→ 英文提示词；顺序即优先级
_PERSONA_RULES = (
    (("老练", "毒舌", "冷幽默", "控场", "沉稳", "经验", "保守", "从容", "胸襟", "务实", "克制"),
     "composed, experienced, calm, confident"),
    (("语速快", "乐观", "脑洞", "元气", "开心", "活泼", "快乐", "直爽", "真诚", "自嘲"),
     "energetic, cheerful, expressive"),
    (("威严", "寡言", "杀伐", "雄猜", "不容置疑", "雍容", "锋利", "命令句"),
     "dignified, authoritative, stern"),
    (("逻辑", "条理", "学霸", "效率", "焦虑", "冷静", "胜负"), "focused, sharp, intelligent"),
    (("狂放", "不羁", "夸张", "旷达", "比喻"), "charismatic, theatrical"),
    (("冷面", "面无表情"), "deadpan expression"),
    (("紧张", "萌新"), "slightly nervous, earnest"),
)

#: 年龄分层：身份硬约束 / 口型句 / 负面词（V45 核心：老角色不许再画成少年）
_AGE_TIERS = (
    (60, ("mature adult", "senior presence", "grey hair", "deep facial lines", "weathered face"),
     "senior adult", ["teen", "teenager", "boy", "shonen", "young adult", "baby face", "chibi"]),
    (45, ("mature adult", "defined jawline", "subtle facial age lines", "mature presence"),
     "mature adult", ["teen", "teenager", "boy", "shonen", "young adult", "baby face", "chibi"]),
    (35, ("mature adult", "middle-aged appearance", "defined jawline", "subtle facial age lines"),
     "mature adult", ["teen", "teenager", "boy", "shonen", "young adult", "smooth youthful skin",
                      "baby face", "chibi", "boyish"]),
    (30, ("adult", "age-accurate"), "adult", ["teen", "teenager", "child", "chibi", "elderly"]),
    (18, ("young adult", "age-accurate"), "young adult", ["middle-aged", "elderly", "old man"]),
    (0, ("child", "age-accurate"), "child", ["adult", "middle-aged", "elderly"]),
)


def _character_look(cid: str) -> dict:
    """V45：角色合同（age/gender/persona）→ 关键帧年龄/气质硬约束 + 负面词 + 口型句。

    关键帧提示词与 H3 ref2va 共用同一份「年龄 + 气质」描述，保证 AI 生成的 42 岁老周
    不会因为参考立绘偏年轻而被画回少年感。
    """
    contract = _character_contract(cid)
    appearance = contract.get("appearance") if isinstance(contract.get("appearance"), dict) else {}
    persona = str(contract.get("persona") or "")
    try:
        age = int(appearance.get("age") or 0)
    except (TypeError, ValueError):
        age = 0
    gender_raw = str(appearance.get("gender") or "").strip().lower()
    is_robot = ("robot" in cid.lower() or "机器人" in persona or "机械" in persona)
    if gender_raw.startswith("f") or gender_raw in ("woman", "girl", "女"):
        gender_tag, noun = "female", "woman"
    elif gender_raw.startswith("m") or gender_raw in ("man", "boy", "男"):
        gender_tag, noun = "male", "man"
    else:
        gender_tag, noun = "androgynous", "person"

    outfit = appearance.get("outfit") if isinstance(appearance.get("outfit"), dict) else {}
    outfit_type = str(outfit.get("type") or "").strip().lower()
    outfit_clause = ""
    outfit_negative: list[str] = []
    if outfit_type in ("suit", "business_suit"):
        outfit_clause = "wearing a tailored business suit, white shirt and necktie, same suit as the reference"
        outfit_negative = ["suspenders", "overalls", "casual clothes", "t-shirt", "different outfit"]
    elif outfit_type:
        outfit_clause = f"wearing the same {outfit_type.replace('_', ' ')} as the reference"

    if is_robot:
        identity = (f"android robot character {cid}, mechanical joints, metal panels, "
                    "non-human, humanoid robot, age-accurate")
        motion = "a humanoid robot assistant, non-human, mechanical"
        negatives = ["human skin", "realistic human face", "wrinkles", "child"]
    else:
        tier = next(item for item in _AGE_TIERS if age >= item[0])
        _, traits, motion_tag, negatives = tier
        persona_tags = []
        for words, tag in _PERSONA_RULES:
            if any(word in persona for word in words) and tag not in persona_tags:
                persona_tags.append(tag)
        if not persona_tags:
            persona_tags.append("natural, neutral expression")
        head = f"{age}-year-old {noun}" if age else f"adult {gender_tag}"
        identity = ", ".join([part for part in (head, *traits, outfit_clause, *persona_tags) if part])
        motion = (f"a {age}-year-old {noun}, {motion_tag}, {persona_tags[0]}" if age
                  else f"an adult {gender_tag}, {motion_tag}")
        if outfit_clause:
            motion += f", {outfit_clause}"
        negatives = [*negatives, *outfit_negative]
    return {"identity": identity, "motion": motion, "negatives": list(negatives),
            "age": age, "gender": gender_tag, "persona": persona, "robot": is_robot}


def _character_age_persona(cid: str) -> tuple[str, list[str]]:
    """兼容旧调用：返回 (身份约束, 负面词)。"""
    look = _character_look(cid)
    return look["identity"], look["negatives"]


#: V45：关键帧负面词（压掉 IP-Adapter 从参考立绘带进来的光效/黑底/大特写构图）
KEYFRAME_NEGATIVE = (
    "abstract background, gradient backdrop, solid color background, black background, "
    "empty background, close-up, face fills frame, bust portrait, cropped face, multiple views, "
    "split screen, duplicated face, mirrored, collage, diptych, poster, character sheet, "
    "energy aura, glowing aura, magic circle, floating sparks, flame, fire, lightning, "
    "swirling light, abstract shapes, speed lines, text")


def _keyframe_prompt(style: dict, visual: str, look: dict) -> str:
    """V45：风格句 + 场景句 + 环境句 + 角色年龄气质句（环境在前，避免只画脸）。"""
    scene = visual or "a cinematic interior scene with a fully visible background"
    age_extra = str(style.get("age_extra") or "").strip()
    parts = [style["prompt"], scene, style["background"], look["identity"],
             age_extra, "detailed face, cel-shaded, clean lineart, official anime art"]
    return "; ".join(part for part in parts if part)


def _ckpt_tag(ckpt: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(ckpt).split(".")[0].lower())[:14] or "ckpt"


def _keyframe_dir(tid: str, style_key: str, style: dict,
                  ip_strength: float | None = None) -> pathlib.Path:
    """V45：关键帧缓存目录（风格 _ 底模 _ IP 权重），poster/生成共用同一套命名。"""
    ip = float(ip_strength) if ip_strength is not None else float(style["ip_strength"])
    ip = max(0.0, min(1.0, ip))
    return KEYFRAMES / tid / f"{style_key}_{_ckpt_tag(style['ckpt'])}_ip{int(round(ip * 100))}"


def _load_keyframe_pick(tid: str, shot_id: str) -> int | None:
    """读取人工选卡：tmp/v16/keyframe_picks.json = {tid: {shot_id: index}}。"""
    if not KEYFRAME_PICKS.is_file():
        return None
    try:
        data = json.loads(KEYFRAME_PICKS.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    per_template = data.get(tid) if isinstance(data, dict) else None
    if not isinstance(per_template, dict):
        return None
    try:
        return int(per_template.get(shot_id))
    except (TypeError, ValueError):
        return None


def _score_candidate(png: pathlib.Path) -> dict:
    """候选关键帧打分（辅助）：细节/饱和度 + 主体面积 + 多主体惩罚。

    肤色连通域只是粗代理，审美判断仍以选卡图 + 人工 picks 为准。
    """
    try:
        import cv2  # noqa: PLC0415
        import numpy as np  # noqa: PLC0415
    except Exception:  # noqa: BLE001
        return {"score": 0.0, "detail": 0.0, "subjects": 0, "subject_area": 0.0}
    img = cv2.imread(str(png))
    if img is None:
        return {"score": 0.0, "detail": 0.0, "subjects": 0, "subject_area": 0.0}
    h, w = img.shape[:2]
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    skin = cv2.inRange(hsv, np.array([0, 30, 60]), np.array([28, 200, 255]))
    skin = cv2.morphologyEx(skin, cv2.MORPH_CLOSE, np.ones((9, 9), np.uint8))
    skin = cv2.morphologyEx(skin, cv2.MORPH_OPEN, np.ones((5, 5), np.uint8))
    n, _labels, stats, _cent = cv2.connectedComponentsWithStats(skin, connectivity=8)
    total = float(h * w)
    areas = []
    for i in range(1, n):
        _x, _y, bw, bh, area = stats[i]
        ratio = area / total
        if ratio < 0.012:
            continue
        aspect = bw / max(1, bh)
        if 0.35 <= aspect <= 3.0:
            areas.append(float(ratio))
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    detail = float(cv2.Laplacian(gray, cv2.CV_64F).var())
    sat = float(np.std(hsv[:, :, 1]))
    largest = max(areas, default=0.0)
    score = 0.55 * math.log1p(detail) + 0.25 * (sat / 50.0)
    if 0.03 <= largest <= 0.30:
        score += 1.0
    if len(areas) >= 2:
        score -= 0.6 * (len(areas) - 1)
    return {"score": round(score, 3), "detail": round(detail, 1),
            "subjects": len(areas), "subject_area": round(largest, 3)}


def _contact_sheet(items: list, out: pathlib.Path, title: str) -> None:
    """把候选关键帧拼成带序号/seed/分数的选卡图。"""
    try:
        from PIL import Image, ImageDraw, ImageFont
    except Exception:  # noqa: BLE001
        return
    if not items:
        return
    thumb_w, thumb_h, bar, pad = 512, 288, 34, 10
    cols = min(2, len(items))
    rows = (len(items) + cols - 1) // cols
    canvas = Image.new("RGB", (thumb_w * cols + pad * (cols + 1),
                               (thumb_h + bar) * rows + pad * (rows + 1)), (16, 18, 24))
    draw = ImageDraw.Draw(canvas)
    try:
        font = ImageFont.truetype("C:/Windows/Fonts/arialbd.ttf", 18)
    except OSError:
        font = ImageFont.load_default()
    for idx, item in enumerate(items):
        try:
            with Image.open(item["path"]) as im:
                tile = im.convert("RGB").resize((thumb_w, thumb_h))
        except OSError:
            continue
        col, row = idx % cols, idx // cols
        x = pad + col * (thumb_w + pad)
        y = pad + row * (thumb_h + bar + pad)
        draw.text((x + 6, y + 6),
                  f"#{idx} seed={item['seed']} score={item['score']} subj={item['subjects']}",
                  fill=(255, 255, 255), font=font)
        canvas.paste(tile, (x, y + bar))
    draw.text((pad, 6), title, fill=(201, 162, 39), font=font)
    out.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(out, quality=90)


def _resolve_pick(tid: str, shot_id: str, candidates: list, pick: int | None) -> tuple:
    """选卡优先级：CLI > keyframe_picks.json > 自动最高分 > 0。"""
    n = len(candidates)
    if not n:
        return 0, "none"
    if pick is not None and 0 <= int(pick) < n:
        return int(pick), "cli"
    saved = _load_keyframe_pick(tid, shot_id)
    if saved is not None and 0 <= saved < n:
        return saved, "picks"
    best = max(range(n), key=lambda i: candidates[i].get("score") or 0.0)
    return best, "auto"


def _generate_keyframe(tid: str, shot: dict, visual: str, seed: int,
                       style_id: str = "jp_anime",
                       ip_strength: float | None = None,
                       ip_end: float | None = None,
                       candidates: int = 1,
                       pick: int | None = None) -> pathlib.Path:
    """SDXL 关键帧（scene 1280x720，portrait IP-Adapter 锁脸）→ 1024×576。

    V45：每种风格自己的 checkpoint + 采样参数；IP 权重降到 0.5 左右并切 style transfer。
    V45.2：candidates>1 时同 prompt 多 seed「抽卡」，落候选图 + 选卡图，
    按 CLI 指定 > tmp/v16/keyframe_picks.json > 自动分 选一张作为正式关键帧。
    """
    style_key = resolve_2d_style(style_id)
    style = STYLE_2D_STYLES[style_key]
    effective_ip = float(ip_strength) if ip_strength is not None else float(style["ip_strength"])
    effective_ip = max(0.0, min(1.0, effective_ip))
    effective_end = float(ip_end) if ip_end is not None else float(style["ip_end"])
    effective_end = max(0.0, min(1.0, effective_end))
    shot_id = str(shot.get("id"))
    cid = _shot_character_id(tid, shot)
    if not cid:
        raise RuntimeError(f"{shot_id}: 找不到说话角色")
    portrait = REPO / "assets" / "v5" / "characters" / cid / "portrait.png"
    if not portrait.is_file():
        raise RuntimeError(f"{shot_id}: 缺少 anime 立绘 {portrait}")
    look = _character_look(cid)
    ckpt_tag = _ckpt_tag(style["ckpt"])
    n_cand = max(1, int(candidates))
    base_dir = _keyframe_dir(tid, style_key, style, effective_ip)
    key_dir = base_dir if n_cand == 1 else base_dir.parent / f"{base_dir.name}_c{n_cand}"
    key_dir.mkdir(parents=True, exist_ok=True)
    key_png = key_dir / f"{shot_id}.png"
    meta_path = key_dir / f"{shot_id}.meta.json"
    saved_pick = _load_keyframe_pick(tid, shot_id)
    expected_pick = int(pick) if pick is not None else saved_pick
    if key_png.is_file() and key_png.stat().st_size > 100_000 and meta_path.is_file():
        try:
            cached_meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            cached_meta = {}
        cached_chosen = int(cached_meta.get("chosen") or 0)
        params_ok = (abs(float(cached_meta.get("ip_strength") or -1) - effective_ip) < 0.001
                     and abs(float(cached_meta.get("ip_end") or -1) - effective_end) < 0.001
                     and str(cached_meta.get("ckpt") or "") == str(style["ckpt"])
                     and int(cached_meta.get("candidates_count") or 1) == n_cand)
        if params_ok and (expected_pick is None or expected_pick == cached_chosen):
            return key_png
    spec_path = _imagegen_spec_for(cid, KEYFRAMES / "specs")
    prompt_extra = _keyframe_prompt(style, visual, look)
    style_negative = str(style.get("negative_extra") or "").strip()
    negative_extra = ", ".join(
        part for part in (KEYFRAME_NEGATIVE, style_negative, ", ".join(look["negatives"])) if part)

    def _render(seed_value: int, raw_dir: pathlib.Path, dest: pathlib.Path) -> int:
        if dest.is_file() and dest.stat().st_size > 100_000:
            return dest.stat().st_size
        cmd = [
            sys.executable, "-m", "imagegen.cli", "generate",
            "--spec", str(spec_path), "--outdir", str(raw_dir),
            "--mode", "scene", "--style", style["imagegen"], "--size", "1280x720",
            "--ckpt", str(style["ckpt"]),
            "--composition", "scene_wide",
            "--cfg", f"{float(style['cfg']):.1f}", "--steps", str(int(style["steps"])),
            "--sampler", str(style["sampler"]), "--scheduler", str(style["scheduler"]),
            "--ref", str(portrait), "--ip-adapter-strength", f"{effective_ip:.2f}",
            "--ip-adapter-end-at", f"{effective_end:.2f}",
            "--ip-adapter-weight-type", str(style["ip_weight_type"]),
            *(["--lora", str(style["lora"]),
               "--lora-strength", f"{float(style.get('lora_strength', 0.85)):.2f}"]
              if style.get("lora") else []),
            "--prompt-extra", prompt_extra,
            "--negative-extra", negative_extra,
            "--seed", str(seed_value), "--timeout", "600",
        ]
        proc = subprocess.run(cmd, cwd=str(REPO), capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=660)
        raw_png = raw_dir / "scene.png"
        if proc.returncode != 0 or not raw_png.is_file():
            tail = "\n".join(((proc.stdout or "") + "\n" + (proc.stderr or "")).strip().splitlines()[-8:])
            raise RuntimeError(f"{shot_id}: imagegen 关键帧失败：{tail[-700:]}")
        dest.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run([
            str(FFMPEG), "-y", "-hide_banner", "-loglevel", "error",
            "-i", str(raw_png),
            "-vf", "scale=1024:576:force_original_aspect_ratio=increase,crop=1024:576",
            str(dest),
        ], check=True, capture_output=True, timeout=120)
        return dest.stat().st_size

    items: list[dict] = []
    chosen, pick_source = 0, "single"
    if n_cand == 1:
        size = _render(seed, KEYFRAMES / tid / f"{style_key}_{ckpt_tag}" / f"raw_{shot_id}", key_png)
        score = _score_candidate(key_png)
        items = [{"index": 0, "seed": seed, "path": str(key_png), "bytes": size, **score}]
        sheet_path = None
    else:
        cand_dir = key_dir / "candidates"
        for k in range(n_cand):
            seed_k = seed + k * 7919
            cand_png = cand_dir / f"{shot_id}_c{k}.png"
            raw_dir_k = KEYFRAMES / tid / f"{style_key}_{ckpt_tag}" / f"raw_{shot_id}_c{k}"
            size = _render(seed_k, raw_dir_k, cand_png)
            score = _score_candidate(cand_png)
            items.append({"index": k, "seed": seed_k, "path": str(cand_png),
                          "bytes": size, **score})
        sheet = cand_dir / f"{shot_id}_sheet.jpg"
        _contact_sheet(items, sheet, f"{tid} · {shot_id} · {style_key} · {n_cand} cands")
        chosen, pick_source = _resolve_pick(tid, shot_id, items, pick)
        shutil.copy2(items[chosen]["path"], key_png)
        sheet_path = str(sheet)
    meta = {
        "shot": shot_id, "character": cid, "style_2d": style_key,
        "style_label": style["label"], "imagegen_style": style["imagegen"],
        "ckpt": style["ckpt"], "cfg": style["cfg"], "steps": style["steps"],
        "sampler": style["sampler"], "scheduler": style["scheduler"],
        "composition": "scene_wide",
        "ip_strength": effective_ip, "ip_end": effective_end,
        "ip_weight_type": style["ip_weight_type"],
        "candidates_count": n_cand, "chosen": chosen, "pick_source": pick_source,
        "candidates": items, "sheet": sheet_path,
        "age_identity": look["identity"], "age": look["age"], "gender": look["gender"],
        "age_negatives": look["negatives"],
        "prompt_extra": prompt_extra, "negative_extra": negative_extra,
        "prompt": prompt_extra, "path": str(key_png),
    }
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    log(f"[2D] 关键帧 {shot_id} ← {cid}（{style_key}/{style['label']}，{ckpt_tag}，"
        f"IP={effective_ip:.2f}→{effective_end:.2f}/{style['ip_weight_type']}，"
        f"cands={n_cand}，pick=#{chosen}/{pick_source}）")
    return key_png

def log(message: str) -> None:
    line = f"[{time.strftime('%H:%M:%S')}] {message}"
    print(line, flush=True)
    WORK.mkdir(parents=True, exist_ok=True)
    with LOG.open("a", encoding="utf-8") as handle:
        handle.write(line + "\n")


def briefs_for(tid: str) -> dict:
    cached = REPO / f"contract/v5/cinema3d/templates3d/{tid}_semantic.json"
    if cached.is_file():
        return json.loads(cached.read_text(encoding="utf-8"))
    raise SystemExit(f"缺少语义 brief: {cached}")


def frame_length(duration: float) -> int:
    frames = max(124.0, float(duration or 5.0) * 24.0)
    k = max(7, int(round((frames - 5.0) / 17.0)))
    return 17 * k + 5


def gen_t2va(tid: str, shot: dict, seed: int, style_mode: str = "3d",
             broll: bool = False, style_id: str = "jp_anime") -> pathlib.Path:
    style_key = resolve_2d_style(style_id)
    suffix = f"{STYLE_2D_CACHE}_{style_key}" if style_mode == "2d" else ""
    out = WORK / "clips" / f"{tid}_{shot['id']}_t2va1024{suffix}.mp4"
    if out.is_file() and out.stat().st_size > 200_000:
        return out
    duration = float(shot.get("duration") or 5.0)
    length = frame_length(duration)
    prompt = apply_2d_style(str(shot["prompt"]), broll=broll, style_id=style_key) \
        if style_mode == "2d" else str(shot["prompt"])
    wf = local.build_workflow(prompt, 1024, 576, length, seed,
                              f"v16/{tid}/{shot['id']}_t2va{suffix}")
    pid = local.submit(wf)
    res = local.wait_and_fetch(pid, out, timeout=1800)
    if not out.is_file():
        raise RuntimeError(f"T2VA 失败: {res}")
    return out


def motion_prompt(shot: dict, style_mode: str = "3d", style_id: str = "jp_anime",
                  age_identity: str = "") -> str:
    style_key = resolve_2d_style(style_id)
    style = STYLE_2D_STYLES[style_key]
    visual = str(shot.get("semantic_visual") or "").strip()
    camera_text = ""
    prompt = str(shot.get("prompt") or "")
    if "Camera:" in prompt:
        camera_text = prompt.split("Camera:", 1)[1].split(".", 1)[0].strip()
    move = ""
    for key, text in MOTION_WORDS.items():
        if key in camera_text:
            move = text
            break
    if not move:
        move = "the camera drifts gently across the scene"
    if style_mode == "2d":
        age_line = f" The character is {age_identity}." if age_identity else ""
        return (
            "subject_definitions: Subject 1 is the character shown in the reference image, preserving their exact face, "
            "hairstyle, hair color, outfit (same jacket, same necktie, same colors), age and "
            f"{str(style.get('art_style') or '2D anime art style')}; "
            f"{str(style.get('finish') or 'cel-shaded, clean line art, flat colors')}."
            f"{age_line}\n\n"
            f"summary: {str(style.get('prefix') or STYLE_2D_PREFIX)}. {style['prompt']}. {visual or 'A cinematic 2D anime scene.'} "
            f"The shot is a medium or medium-wide 2D anime composition that keeps the surrounding set visible; {move}. "
            "The character speaks naturally with lips moving in sync with the reference speech audio, blinking and "
            "gesturing, while the camera and body have gentle hand-drawn motion. Keep the character identity consistent "
            f"with the reference image.{age_line} {str(style.get('quality') or STYLE_2D_QUALITY)}; {style['prompt']}. "
            "no text, no subtitles, no watermark.\n\n"
            "overall_soundscape: the same spoken sentence, close-mic voice, quiet room tone.\n\n"
            "non_diegetic_music: N/A"
        )
    return (
        "subject_definitions: Subject 1 is the character shown in the reference image, preserving their exact face, "
        "hairstyle, hair color, outfit, age and 3D animated art style.\n\n"
        f"summary: {visual or 'A cinematic 3D animated scene.'} The shot is a medium or medium-wide cinematic composition "
        f"that keeps the surrounding set visible; {move}. The character speaks naturally with lips moving in sync with the "
        "reference speech audio, blinking and gesturing, while the camera and body have gentle real motion. Keep the "
        "character identity consistent with the reference image. 3D animated feature film quality, rich physical "
        "materials, volumetric light, shallow depth of field. no text, no subtitles, no watermark.\n\n"
        "overall_soundscape: the same spoken sentence, close-mic voice, quiet room tone.\n\n"
        "non_diegetic_music: N/A"
    )


def gen_ref2va(tid: str, shot: dict, t2va: pathlib.Path, seed: int,
               style_mode: str = "3d", keyframe: pathlib.Path | None = None,
               style_id: str = "jp_anime", age_identity: str = "") -> pathlib.Path:
    style_key = resolve_2d_style(style_id)
    suffix = f"{STYLE_2D_CACHE}_{style_key}" if style_mode == "2d" else ""
    out = WORK / "clips" / f"{tid}_{shot['id']}_ref2va1024{suffix}.mp4"
    if keyframe is not None:
        # 关键帧模式：旧 ref2va 缓存（V42 T2VA 首帧）不能复用，强制重生成
        try:
            if out.is_file():
                out.unlink()
        except OSError:
            pass
    elif out.is_file() and out.stat().st_size > 200_000:
        return out
    if keyframe is not None and keyframe.is_file():
        first = keyframe
    else:
        first = WORK / "clips" / f"{tid}_{shot['id']}_first{suffix}.png"
        subprocess.run([str(FFMPEG), "-y", "-hide_banner", "-loglevel", "error",
                        "-i", str(t2va), "-frames:v", "1", str(first)], check=False)
    audio = t3.V5_OUT / tid / "work" / "audio" / f"{shot['id']}.wav"
    prompt_file = WORK / "clips" / f"{tid}_{shot['id']}_prompt{suffix}.txt"
    prompt_file.write_text(motion_prompt(shot, style_mode, style_key, age_identity),
                           encoding="utf-8")
    duration = float(shot.get("duration") or 5.0)
    proc = subprocess.run([
        sys.executable, str(H3_REF), "--mode", "audio_ref",
        "--image", str(first), "--audio", str(audio), "--out", str(out),
        "--duration", f"{duration:.3f}", "--width", "1024", "--height", "576",
        "--fps", "24", "--seed", str(seed), "--prompt-file", str(prompt_file),
        "--timeout", "1200",
    ], capture_output=True, text=True, encoding="utf-8", errors="replace")
    if proc.returncode != 0 or not out.is_file():
        raise RuntimeError(f"ref2va 失败: {(proc.stderr or '')[-300:]}")
    return out


def _ensure_card_shots(tid: str) -> None:
    """V42：custom 模板首次生成时没有旧 V5 卡片 clip；按 episode 补生成片头/片尾卡。"""
    from pipeline import render_v5, v5_catalog

    episode = t3.load_episode(tid)
    template = t3.load_template(tid)
    catalog = v5_catalog.load_catalog()
    style = (catalog.get("styles") or {}).get(str(template.get("style") or ""))
    if not isinstance(style, dict):
        raise RuntimeError(f"卡片生成缺少 style: {template.get('style')}")
    out_dir = t3.OUT3D / tid
    workdir = out_dir / "work"
    shots_dir = out_dir / "shots"
    shots_dir.mkdir(parents=True, exist_ok=True)
    workdir.mkdir(parents=True, exist_ok=True)
    paths = {
        "out": out_dir / "final.mp4", "out_dir": out_dir,
        "workdir": workdir, "shots_dir": shots_dir,
        "frames_dir": out_dir / "frames", "assets_dir": workdir / "assets",
    }
    for shot in episode.get("shots") or []:
        if str(shot.get("type")) != "card":
            continue
        dest = shots_dir / f"{shot['id']}.mp4"
        if dest.is_file() and dest.stat().st_size > 100_000:
            continue
        old = t3.V5_OUT / tid / "shots" / f"{shot['id']}.mp4"
        if old.is_file() and old.stat().st_size > 100_000:
            shutil.copy2(old, dest)
            continue
        try:
            render_v5._render_card_clip(shot, style, paths, dest)
            log(f"补生成卡片 {shot['id']}")
        except Exception as exc:  # noqa: BLE001 - 卡片失败不阻塞正片
            log(f"卡片 {shot['id']} 生成失败（跳过）：{exc}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--template", required=True)
    ap.add_argument("--style-mode", choices=("3d", "2d"), default="3d",
                    help="V42：2d 使用 AI 动漫(cel-shaded/线稿)提示词，3d 保持原 hq")
    ap.add_argument("--2d-style", dest="style_2d", choices=tuple(STYLE_2D_STYLES),
                    default="jp_anime",
                    help="V44/V45：2D 风格子类型（仅在 --style-mode 2d 时生效）")
    ap.add_argument("--ip-strength", dest="ip_strength", type=float, default=None,
                    help="V45：覆盖 2D 关键帧 IP-Adapter 权重（默认按风格，0.45~0.5）")
    ap.add_argument("--ip-end", dest="ip_end", type=float, default=None,
                    help="V45：覆盖 IP-Adapter 结束步（默认按风格，越早结束背景越自由）")
    ap.add_argument("--keyframe-candidates", dest="keyframe_candidates", type=int, default=1,
                    help="V45.2：2D 关键帧抽卡数（>1 时同 prompt 多 seed，出候选图+选卡图）")
    ap.add_argument("--keyframe-pick", dest="keyframe_pick", type=int, default=None,
                    help="V45.2：指定候选序号（缺省读 tmp/v16/keyframe_picks.json，再退回自动分）")
    ap.add_argument("--skip-t2va", action="store_true")
    ap.add_argument("--skip-ref2va", action="store_true")
    ap.add_argument("--reuse-shots", action="store_true",
                    help="已有 shots/<sid>.mp4 时跳过生成，直接复用（用于快速重剪辑/UI「复用已有镜头」）")
    args = ap.parse_args(argv)
    tid = args.template
    style_mode = str(args.style_mode or "3d")
    style_2d = resolve_2d_style(getattr(args, "style_2d", "jp_anime"))
    ip_strength = getattr(args, "ip_strength", None)
    ip_end = getattr(args, "ip_end", None)
    keyframe_candidates = max(1, int(getattr(args, "keyframe_candidates", 1) or 1))
    keyframe_pick = getattr(args, "keyframe_pick", None)
    briefs = briefs_for(tid)
    shots = [s for s in briefs["shots"] if s.get("prompt")]
    archive = REPO / f"outputs/_archive_v15_ref2va_768/{tid}/shots"
    archive.mkdir(parents=True, exist_ok=True)
    out_shots = t3.OUT3D / tid / "shots"
    out_shots.mkdir(parents=True, exist_ok=True)  # 新模板首次生成时目录不存在
    for shot in shots:
        src = out_shots / f"{shot['id']}.mp4"
        dst = archive / f"{shot['id']}.mp4"
        if src.is_file() and not dst.is_file():
            shutil.copy2(src, dst)
    style_note = ""
    if style_mode == "2d":
        style_conf = STYLE_2D_STYLES[style_2d]
        style_note = (f"，2d_style={style_2d}/{style_conf['label']}，ckpt={style_conf['ckpt']}，"
                      f"IP={style_conf['ip_strength']:.2f}→{style_conf['ip_end']:.2f}"
                      f"/{style_conf['ip_weight_type']}")
    log(f"=== {tid}: {len(shots)} 镜，1024×576 T2VA + ref2va（style_mode={style_mode}{style_note}）===")
    for i, shot in enumerate(shots, 1):
        existing = out_shots / f"{shot['id']}.mp4"
        if args.reuse_shots and existing.is_file() and existing.stat().st_size > 100_000:
            log(f"[{i}/{len(shots)}] {shot['id']} ok (reuse)")
            continue
        # V45：用 crc32 而不是内置 hash()（后者受 PYTHONHASHSEED 随机化，重跑同模板会换 seed）
        seed = 20261102 + zlib.crc32((tid + shot["id"]).encode("utf-8")) % 100000
        has_line = bool(str(shot.get("line") or "").strip())
        age_identity = ""
        if style_mode == "2d" and has_line:
            cid = _shot_character_id(tid, shot)
            if cid:
                age_identity = _character_look(cid)["motion"]
        final_src = None
        if style_mode == "2d" and has_line and not args.skip_ref2va:
            visual = str(shot.get("semantic_visual") or "").strip()
            try:
                keyframe = _generate_keyframe(tid, shot, visual, seed, style_2d,
                                             ip_strength, ip_end,
                                             candidates=keyframe_candidates,
                                             pick=keyframe_pick)
                final_src = gen_ref2va(tid, shot, keyframe, seed, style_mode,
                                       keyframe=keyframe, style_id=style_2d,
                                       age_identity=age_identity)
                log(f"[{i}/{len(shots)}] {shot['id']} ok (keyframe+ref2va)")
            except Exception as exc:  # noqa: BLE001 - 关键帧失败回退 T2VA
                log(f"[{i}/{len(shots)}] {shot['id']} 关键帧失败，回退 T2VA：{exc}")
        if final_src is None:
            t2va = gen_t2va(tid, shot, seed, style_mode,
                            broll=(style_mode == "2d" and not has_line),
                            style_id=style_2d)
            final_src = t2va
            if has_line and not args.skip_ref2va:
                final_src = gen_ref2va(tid, shot, t2va, seed, style_mode,
                                       style_id=style_2d, age_identity=age_identity)
            log(f"[{i}/{len(shots)}] {shot['id']} ok "
                f"({'ref2va' if final_src != t2va else 't2va'})")
        shutil.copy2(final_src, out_shots / f"{shot['id']}.mp4")
    log("=== 剪辑 ===")
    _ensure_card_shots(tid)
    report = t3.assemble(tid)
    final = t3.OUT3D / tid / "final.mp4"
    # V45：海报优先用「有台词镜头」的风格关键帧（带角色 + 该风格的脸/服装），
    # 这样 4 种风格的封面才看得出差异；没有关键帧时回落到该镜头 1.2s 处抽帧。
    poster_out = t3.OUT3D / tid / "poster.jpg"
    poster_shot = next((s for s in shots if str(s.get("line") or "").strip()), None) or shots[0]
    clip = out_shots / f"{poster_shot['id']}.mp4"
    poster_key = None
    if style_mode == "2d":
        key_dir_base = _keyframe_dir(tid, style_2d, STYLE_2D_STYLES[style_2d], ip_strength)
        # V46：抽卡时关键帧在 <base>_cN/ 下，两个位置都要找
        key_candidates = [key_dir_base / f"{poster_shot['id']}.png",
                          *sorted(key_dir_base.parent.glob(
                              f"{key_dir_base.name}_c*/{poster_shot['id']}.png"))]
        poster_key = next((path for path in key_candidates if path.is_file()), None)
    if poster_key is not None:
        subprocess.run([str(FFMPEG), "-y", "-hide_banner", "-loglevel", "error",
                        "-i", str(poster_key), "-frames:v", "1", "-q:v", "3",
                        "-vf", "scale=960:-1", str(poster_out)], check=False)
        log(f"海报 ← 关键帧 {poster_shot['id']}（{style_2d}）")
    elif clip.is_file():
        subprocess.run([str(FFMPEG), "-y", "-hide_banner", "-loglevel", "error",
                        "-ss", "1.2", "-i", str(clip), "-frames:v", "1", "-q:v", "3",
                        "-vf", "scale=960:-1", str(poster_out)], check=False)
        log(f"海报 ← 镜头 {poster_shot['id']} @1.2s")
    log(f"完成: {json.dumps(report.get('probe', {}).get('format', {}), ensure_ascii=False)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
