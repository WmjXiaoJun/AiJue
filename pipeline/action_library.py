# -*- coding: utf-8 -*-
"""动作库 → H3 prompt（v3）：动作/运镜/身份锚点/场景/声音四段式。

接口（docs/V3_INTERFACES.md §3）：
    resolve_action(template, action_id) -> dict          # action_library 条目，缺失 raise KeyError
    camera_phrase(movement, size=None) -> str            # 运镜英文短语（供 H3 prompt）
    subject_definitions(template) -> str                 # "subject_definitions: Subject 1 is the host (laozhou)..."
    scene_sentence(template) -> str                      # 场景一句英文（stage.describe_scene）
    build_h3_prompt(template, shot, *, mode, style_prefix=None) -> str
    build_action_variants(template) -> dict              # 供 storyboard 选择动作

H3 官方四段结构（mode=audio_ref）：
    subject_definitions: Subject 1 is ...; Subject 2 is ...
    summary: One continuous <size> shot ... <action.h3_prompt> ... <camera_phrase>;
             keep faces/hairstyles/outfits/background stable; <style_prefix>
    overall_soundscape: ...
    non_diegetic_music: N/A

mode=fl2v（04 i2v_fl2v）时 summary 首段改为：
    integrated_multimodal_description: [Shot 1] A smooth cinematic move from the
    first frame to the last frame: ...

约束：prompt 里**不写台词文本**（台词只进 TTS 音频）；动作必须来自模板 action_library。
"""
from __future__ import annotations

import re

from . import stage, style_v2, template_v3

# 景别 → H3 可读构图描述（后面直接跟 "One continuous ..."）
SIZE_PHRASE = {
    "Wide": "wide establishing shot with the full body and the whole set visible",
    "Wide / Establishing": "wide establishing shot with the full body and the whole set visible",
    "Full": "full shot (head-to-toe)",
    "Medium": "medium shot (waist-up)",
    "Medium Close-up": "medium close-up (chest-up)",
    "Close-up": "close-up (head and shoulders)",
    "Extreme Close-up": "extreme close-up of the face",
}

# 颜色 hex → 英文色名（身份锚点用自然语言，避免 prompt 里堆 hex）
_COLOR_NAMES = (
    ("black", (18, 18, 20)),
    ("off-white", (244, 241, 232)),
    ("white", (252, 252, 252)),
    ("dark charcoal", (43, 43, 51)),
    ("navy", (47, 58, 79)),
    ("blue", (58, 92, 160)),
    ("sky blue", (90, 200, 250)),
    ("gold", (245, 197, 24)),
    ("yellow", (232, 205, 90)),
    ("red", (214, 64, 64)),
    ("dark red", (120, 32, 36)),
    ("brown", (122, 84, 54)),
    ("gray", (132, 132, 138)),
    ("purple", (140, 90, 196)),
    ("lavender", (196, 176, 226)),
    ("pink", (232, 150, 180)),
    ("teal", (40, 128, 136)),
    ("green", (62, 160, 96)),
    ("cream", (240, 230, 200)),
    ("silver", (192, 196, 206)),
    ("skin tone", (242, 214, 195)),
)


def _color_name(value) -> str:
    text = str(value or "").strip().lstrip("#")
    if not re.fullmatch(r"[0-9a-fA-F]{6}", text or ""):
        return ""
    rgb = (int(text[0:2], 16), int(text[2:4], 16), int(text[4:6], 16))
    best, best_dist = "", 1 << 30
    for name, ref in _COLOR_NAMES:
        dist = sum((a - b) ** 2 for a, b in zip(rgb, ref))
        if dist < best_dist:
            best, best_dist = name, dist
    return "" if best_dist > 46000 else best


def _humanize(value) -> str:
    """snake_case / kebab-case → 自然英文短语。"""
    text = str(value or "").strip().replace("_", " ").replace("-", " ")
    return re.sub(r"\s+", " ", text).strip()


# ---------------------------------------------------------------------------
# shot 归一化（与 stage 保持同一套规则；storyboard shot / 模板 shot 都兼容）
# ---------------------------------------------------------------------------
def _shot_fields(shot: dict) -> dict:
    shot = shot if isinstance(shot, dict) else {}
    v3 = shot.get("v3") if isinstance(shot.get("v3"), dict) else {}
    out: dict = {}
    for key in ("intent", "size", "angle", "movement", "action", "props", "h3_mode",
                "camera_zone"):
        if key in v3 and v3.get(key) is not None:
            out[key] = v3[key]
    for key in ("size", "angle", "movement"):
        plan = shot.get("shot_plan") if isinstance(shot.get("shot_plan"), dict) else {}
        if key not in out and plan.get(key):
            out[key] = plan[key]
    for key in ("intent", "size", "angle", "movement", "action", "props", "h3_mode",
                "camera_zone", "line"):
        if key not in out and shot.get(key) is not None:
            out[key] = shot[key]
    out["speaker"] = ""
    return out


def _speaker_role(template: dict, shot: dict) -> str:
    """shot → 说话角色 role（与 stage._speaker_role 同规则，供本模块独立使用）。"""
    shot = shot if isinstance(shot, dict) else {}
    cast = template_v3.cast_for(template)
    roles = {str(c.get("role") or "") for c in cast}
    for value in (shot.get("role"), shot.get("speaker_role"), shot.get("speaker"),
                  shot.get("speaker_asset"), shot.get("speaker_name")):
        text = str(value or "").strip()
        if not text:
            continue
        if text in roles:
            return text
        for member in cast:
            if text in (str(member.get("character_id") or ""),
                        str(member.get("character") or ""),
                        str(member.get("name") or ""),
                        str(member.get("id") or "")):
                return str(member.get("role") or "")
    return ""


def _subject_index(template: dict, role: str) -> int:
    """cast 顺序 → Subject N（1-based）；未知角色返回 0。"""
    for i, member in enumerate(template_v3.cast_for(template)):
        if str(member.get("role") or "") == role:
            return i + 1
    return 0


# ---------------------------------------------------------------------------
# 动作库
# ---------------------------------------------------------------------------
def resolve_action(template: dict, action_id) -> dict:
    """action_library 条目；缺失 raise KeyError（模板校验应提前拦截）。"""
    actions = template_v3.actions_by_id(template or {})
    key = str(action_id or "").strip()
    if not key or key not in actions:
        raise KeyError(f"action {action_id!r} 不在模板 {((template or {}).get('id'))!r} 的 action_library 中"
                       f"（已有: {sorted(actions)}）")
    return dict(actions[key])


def _action_entry(template: dict, action_id) -> dict:
    """宽松版 resolve_action：缺失返回 {}（合成/预览不至于整镜失败）。"""
    try:
        return resolve_action(template, action_id)
    except KeyError:
        return {}


def build_action_variants(template: dict) -> dict:
    """动作库 → 供 storyboard 选择动作的变体表。

    返回 {"count", "ids", "by_id", "with_prop", "without_prop", "style_prefix"}；
    by_id[action_id] = {"id","h3_prompt","prop","optional","variants":[3 种措辞]}。
    """
    template = template or {}
    by_id: dict = {}
    with_prop: list[str] = []
    without_prop: list[str] = []
    for entry in template.get("action_library") or []:
        if not isinstance(entry, dict):
            continue
        aid = str(entry.get("id") or "").strip()
        if not aid:
            continue
        base = str(entry.get("h3_prompt") or "").strip()
        variants = [
            base,
            f"{base} Body language stays clear and readable in a single continuous take.",
            f"{base} Natural timing, no cut, no camera direction change.",
        ]
        item = {
            "id": aid,
            "h3_prompt": base,
            "prop": entry.get("prop"),
            "optional": bool(entry.get("optional")),
            "variants": [v for v in variants if v],
        }
        by_id[aid] = item
        (with_prop if entry.get("prop") else without_prop).append(aid)
    preset = stage._style_preset(template) if hasattr(stage, "_style_preset") else {}
    return {
        "count": len(by_id),
        "ids": list(by_id.keys()),
        "by_id": by_id,
        "with_prop": with_prop,
        "without_prop": without_prop,
        "style_prefix": str((preset or {}).get("prompt_prefix") or ""),
    }


# ---------------------------------------------------------------------------
# 运镜
# ---------------------------------------------------------------------------
def camera_phrase(movement, size=None) -> str:
    """运镜英文短语（H3 一次只允许一类运动；结景别给构图提示）。"""
    text = _humanize(movement).lower() or "static"
    direction = ""
    if "left" in text:
        direction = " to the left"
    elif "right" in text:
        direction = " to the right"
    elif "up" in text:
        direction = " upward"
    elif "down" in text:
        direction = " downward"
    if "static" in text or "locked" in text or "still" in text:
        base = ("very slow micro-pan (rotation only, no dolly, no zoom)"
                if "micro" in text else "static camera, locked-off, zero camera movement")
    elif any(k in text for k in ("whip", "fast pan", "quick pan")):
        base = f"fast whip pan{direction} (rotation only, no dolly, no zoom)"
    elif any(k in text for k in ("orbit", "arc")):
        base = "slow orbit around the subject (rotation only, no dolly, no zoom)"
    elif any(k in text for k in ("truck", "track", "dolly left", "dolly right")):
        base = f"slow truck{direction} (translation only, no pan, no zoom)"
    elif "pan" in text or "swish" in text:
        base = f"very slow pan{direction} (rotation only, no dolly, no zoom)"
    elif any(k in text for k in ("tilt", "crane", "boom")):
        base = f"slow vertical tilt{direction} (rotation only, no dolly, no pan)"
    elif any(k in text for k in ("pull", "zoom out", "zoom-out", "back")):
        base = "slow pull-out away from the subject (translation only, no zoom)"
    elif any(k in text for k in ("rack", "focus")):
        base = "slow rack focus (lens only, no dolly, no pan)"
    elif any(k in text for k in ("handheld", "follow")):
        base = "gentle handheld follow with subtle natural shake (single move class)"
    elif any(k in text for k in ("dolly", "push", "zoom in", "zoom-in", "creep")):
        base = "slow dolly-in toward the subject (translation only, no zoom)"
    else:
        base = f"{text} (single camera move only; no zoom/pan mix)"
    if size:
        size_key = str(size)
        if size_key in ("Close-up", "Extreme Close-up", "Medium Close-up") \
                and "dolly" in base:
            base += ", ending on the face"
        elif size_key in ("Wide", "Wide / Establishing", "Full"):
            base += ", keeping the whole stage in frame"
    return base


def size_phrase(size) -> str:
    key = str(size or "Medium")
    return SIZE_PHRASE.get(key, f"{key} shot")


# ---------------------------------------------------------------------------
# 身份锚点 / 场景
# ---------------------------------------------------------------------------
def _article(phrase: str) -> str:
    """a / an（按首字母发音粗判）。"""
    text = str(phrase or "").strip()
    if not text:
        return "a"
    return "an" if text[0].lower() in "aeiou" else "a"


def _role_label(role) -> str:
    """guest_a / figure_b / suspect_1 → guest / historical figure / suspect（更贴近自然语言）。"""
    text = _humanize(role) or "character"
    if re.search(r"[_\s][a-z0-9]$", str(role or "")):
        text = re.sub(r"[_\s]*[a-z0-9]$", "", text)
    alias = {
        "figure": "historical figure",
        "host": "host",
        "guest": "guest",
        "suspect": "suspect",
        "detective": "detective",
        "investor": "investor",
        "contestant": "contestant",
        "performer": "performer",
    }
    return alias.get(text, text)


def _noun_with_article(phrase: str) -> str:
    text = str(phrase or "").strip()
    if not text:
        return ""
    # 复数/不可数（glasses, headphones）不加冠词
    if text.endswith("s") and not text.endswith("ss"):
        return text
    return f"{_article(text)} {text}"


def _appearance_phrase(member: dict) -> str:
    """角色资料 → 身份/服装锚点英文（H3 5-aspect 公式的 identity 部分）。"""
    member = member or {}
    appearance = member.get("appearance") if isinstance(member.get("appearance"), dict) else {}
    role_words = _role_label(member.get("role") or "character")
    age = appearance.get("age")
    gender = str(appearance.get("gender") or "").strip()
    if age and gender:
        identity = f"a {age}-year-old {gender} {role_words}"
    elif gender:
        identity = f"a {gender} {role_words}"
    else:
        identity = f"the {role_words}"
    clauses: list[str] = []
    hair_style = _humanize(appearance.get("hair_style"))
    hair_color = _color_name(appearance.get("hair_color"))
    if "bald" in hair_style:
        clauses.append("a bald head")
    elif hair_style or hair_color:
        clauses.append(" ".join(x for x in (hair_style, hair_color, "hair") if x))
    facial_hair = _humanize(appearance.get("facial_hair"))
    if facial_hair and facial_hair not in ("none", "no", "clean shaven", "clean-shaven"):
        clauses.append(f"light {facial_hair}")
    text = identity
    if clauses:
        text += " with " + " and ".join(clauses)
    outfit = appearance.get("outfit") if isinstance(appearance.get("outfit"), dict) else {}
    if outfit:
        main = _color_name(outfit.get("main")) or _humanize(outfit.get("main"))
        accent = _color_name(outfit.get("accent")) or _humanize(outfit.get("accent"))
        shirt = _color_name(outfit.get("shirt")) or _humanize(outfit.get("shirt"))
        outfit_words = _humanize(outfit.get("type")) or "outfit"
        desc = _noun_with_article(f"{main} {outfit_words}".strip())
        if accent:
            desc += f" with {accent} trim"
        if shirt:
            desc += f" and {_noun_with_article(shirt + ' shirt')}"
        text += f", wearing {desc}"
    else:
        wardrobe = _humanize(member.get("wardrobe"))
        if wardrobe:
            text += f", wearing {_noun_with_article(wardrobe)}"
    accessories = [_humanize(a) for a in (appearance.get("accessories") or []) if _humanize(a)]
    if accessories:
        text += ", plus " + " and ".join(_noun_with_article(a) for a in accessories)
    marks = [_humanize(m) for m in (appearance.get("marks") or []) if _humanize(m)]
    if marks:
        text += ", with " + " and ".join(_noun_with_article(m) for m in marks)
    return text


def subject_definitions(template: dict) -> str:
    """四段式第一段：每个角色一条身份锚点（Subject N + 角色/角色 id + 外观/服装）。"""
    template = template or {}
    lines: list[str] = []
    for i, member in enumerate(template_v3.cast_for(template)):
        role = str(member.get("role") or f"character {i + 1}")
        character = str(member.get("character_id") or member.get("character") or "")
        label = f"the {_role_label(role)}"
        if character:
            label += f" ({character})"
        lines.append(f"Subject {i + 1} is {label}, {_appearance_phrase(member)}.")
    if not lines:
        lines.append("Subject 1 is the character in the reference image.")
    lines.append("Keep each subject's exact face, hairstyle, hair color, outfit and age "
                 "consistent with the first frame and with the reference image.")
    return "subject_definitions: " + " ".join(lines)


def scene_sentence(template: dict) -> str:
    """场景一句英文（stage.describe_scene：stage.scene_desc，缺失用 plate 名兜底）。"""
    return str(stage.describe_scene(template or {})).strip()


def _scene_embed(scene: str) -> str:
    """场景描述嵌进句子：去尾部句号、去掉 'A/An/The' 大写，避免 'in A ... floor..'。"""
    text = str(scene or "").strip().rstrip(".")
    if not text:
        return "a cinematic anime studio scene"
    lowered = text.lower()
    if lowered.startswith("the scene is "):
        text = text[len("the scene is "):]
    elif lowered.startswith("the scene: "):
        text = text[len("the scene: "):]
    for prefix in ("A ", "An ", "The "):
        if text.startswith(prefix):
            text = text[0].lower() + text[1:]
            break
    return text


def _angle_phrase(angle) -> str:
    text = str(angle or "eye-level").replace("_", " ").strip() or "eye-level"
    if "angle" not in text.lower():
        text += " angle"
    return text


def _fallback_subject_role(template: dict) -> str:
    """没有 shot.role 时：audio_ref 默认把 host（或第一个角色）当说话人。"""
    cast = template_v3.cast_for(template or {})
    for member in cast:
        if str(member.get("role") or "").startswith("host"):
            return str(member.get("role") or "")
    return str(cast[0].get("role") or "") if cast else ""


def style_prefix_for(template: dict) -> str:
    """模板 style preset 的 H3 prompt 前缀。"""
    preset = stage._style_preset(template or {})
    return str((preset or {}).get("prompt_prefix") or "")


def _style_sentence(style_prefix: str | None) -> str:
    text = str(style_prefix or "").strip()
    if not text:
        return ""
    return f"Visual style: {text}."


# ---------------------------------------------------------------------------
# H3 prompt
# ---------------------------------------------------------------------------
def _soundscape(template: dict, mode: str) -> str:
    audio = template.get("audio") if isinstance(template.get("audio"), dict) else {}
    ambience = _humanize(audio.get("ambience")) or "quiet studio room tone"
    sfx = [_humanize(s) for s in (audio.get("sfx") or []) if _humanize(s)][:3]
    parts = [ambience]
    if sfx:
        joined = sfx[0] if len(sfx) == 1 else ", ".join(sfx[:-1]) + f" and {sfx[-1]}"
        parts.append(f"occasional soft {joined}")
    parts.append("natural room tone")
    parts.append("subtle cloth movement")
    if mode == "fl2v":
        parts.append("no dialogue, no music")
    else:
        parts.append("the speaking subject's own close-mic voice; no music")
    return "overall_soundscape: " + "; ".join(parts) + "."


def _action_sentence(template: dict, fields: dict, speaker_role: str) -> str:
    """动作库 h3_prompt → 挂在 Subject N 上的动作句（不写台词）。"""
    action = _action_entry(template, fields.get("action"))
    raw = str(action.get("h3_prompt") or "").strip().rstrip(".")
    if not raw:
        intent = _humanize(fields.get("intent") or "")
        raw = (f"Subject moves naturally and interacts with the set, {intent}"
               if intent else "Subject performs the moment naturally, interacting with the set")
    index = _subject_index(template, speaker_role) if speaker_role else 0
    if raw.lower().startswith("subject"):
        if index:
            replacement = f"Subject {index} (the {_role_label(speaker_role)})"
            raw = re.sub(r"^subject\b", replacement, raw, count=1, flags=re.IGNORECASE)
        else:
            raw = re.sub(r"^subject\b", "The subject", raw, count=1, flags=re.IGNORECASE)
    elif index:
        raw = f"Subject {index} (the {_role_label(speaker_role)}) {raw[0].lower() + raw[1:]}"
    prop = str(action.get("prop") or "")
    raw = raw.rstrip(".")
    if prop and prop not in raw:
        raw += f". The action involves the {_humanize(prop)}"
    return raw.rstrip(".") + "."


def _fl2v_like(mode) -> bool:
    text = str(mode or "").strip().lower()
    return text in ("fl2v", "i2v", "flf2v", "first_last", "first-last")


def build_h3_prompt(template: dict, shot: dict, *, mode, style_prefix=None) -> str:
    """H3 四段式 prompt（audio_ref / fl2v 两种开头；不写台词文本）。"""
    template = template or {}
    shot = shot if isinstance(shot, dict) else {}
    fields = _shot_fields(shot)
    mode_text = str(mode or fields.get("h3_mode") or "audio_ref").strip().lower()
    fl2v = _fl2v_like(mode_text)
    speaker_role = _speaker_role(template, shot)
    if not speaker_role and not fl2v:  # audio_ref 必须有说话人：默认 host
        speaker_role = _fallback_subject_role(template)
    subject = (f"Subject {_subject_index(template, speaker_role)} "
               f"(the {_role_label(speaker_role)})" if speaker_role else "the subjects")
    size = size_phrase(fields.get("size"))
    angle = _angle_phrase(fields.get("angle"))
    scene = _scene_embed(scene_sentence(template))
    camera = camera_phrase(fields.get("movement"), fields.get("size"))
    style_text = _style_sentence(style_prefix if style_prefix is not None
                                 else style_prefix_for(template))
    keep = ("Keep faces, hairstyles, outfits, body proportions and the background "
            "exactly as in the first frame; do not change the set, props, lighting or "
            "camera framing. No on-screen text, no subtitles, no watermark.")
    if fl2v and not speaker_role:
        # 空镜（b_roll/establishing）：首帧只有 plate，不允许 H3 凭空加人
        summary = (
            "integrated_multimodal_description: [Shot 1] A smooth cinematic move from "
            f"the first frame to the last frame: one continuous {size}, {angle}, of the "
            f"empty set: {scene}. No people are in frame; the camera move is a {camera} "
            "across the set with only subtle environmental motion such as drifting dust "
            "and flickering practical light. Keep the background, props and lighting "
            "exactly as in the first frame; do not add any people, characters, text or "
            f"watermarks. {style_text}"
        ).strip()
    elif fl2v:
        action = _action_sentence(template, fields, speaker_role)
        summary = (
            "integrated_multimodal_description: [Shot 1] A smooth cinematic move from "
            f"the first frame to the last frame: one continuous {size}, {angle}, in "
            f"{scene}. {action} Camera: {camera}. The motion happens as a single "
            "continuous take with natural timing and subtle body language. "
            f"Only the subjects move between the two frames. {keep} {style_text}"
        ).strip()
    else:
        action = _action_sentence(template, fields, speaker_role)
        summary = (
            f"summary: One continuous {size}, {angle}, in {scene}. {action} "
            f"Camera: {camera}. {subject} speaks in sync with the reference speech "
            "audio, with subtle blinking, eyebrow movement and small head nods. "
            f"{keep} {style_text}"
        ).strip()
    soundscape = _soundscape(template, "fl2v" if fl2v else "audio_ref")
    return "\n\n".join([
        subject_definitions(template),
        summary,
        soundscape,
        "non_diegetic_music: N/A",
    ])


def prompt_meta(template: dict, shot: dict, *, mode) -> dict:
    """附带元数据：storyboard/render 落 episode.json / h3_meta 时可直接用。"""
    fields = _shot_fields(shot)
    speaker_role = _speaker_role(template, shot)
    return {
        "template": str(template.get("id") or ""),
        "shot_id": str(shot.get("id") or ""),
        "mode": str(mode or fields.get("h3_mode") or ""),
        "action": str(fields.get("action") or ""),
        "speaker_role": speaker_role,
        "subject": (f"Subject {_subject_index(template, speaker_role)}"
                    if speaker_role else ""),
        "size": str(fields.get("size") or ""),
        "movement": str(fields.get("movement") or ""),
        "camera_phrase": camera_phrase(fields.get("movement"), fields.get("size")),
        "scene": scene_sentence(template),
        "style_prefix": style_prefix_for(template),
    }
