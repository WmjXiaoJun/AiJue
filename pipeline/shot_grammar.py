"""镜头语法（参考 OpenMontage skills/creative/video-gen-prompting.md + video-stitching.md）。

把节目模板的 segment.pattern 展开为「镜头级 shot plan」：
- 景别（Wide/Full/Medium/Medium Close/Close/Extreme Close/Insert）
- 机位高度与角度（eye-level/high/low/OTS/POV）
- 运镜（static/dolly-in/pan/truck/zoom/rack focus）
- 光线（key+rim/high-key/low-key/practical）
- 入点转场（hard_cut/crossfade/fade_black），依据 OpenMontage 转场决策树
- 镜头目的（establish/explain/emotion/reveal/reaction）

这些字段既用于模板验证片，也可注入 H3/图像生成 prompt（5-aspect 规范）。
"""
from __future__ import annotations

TYPE_PLANS = {
    "title_card": {"size": "GRAPHIC", "angle": "N/A", "movement": "static", "light": "brand",
                   "transition": "fade_black", "purpose": "open"},
    "host_to_camera": {"size": "Medium", "angle": "eye-level", "movement": "slow dolly-in",
                       "light": "key + rim", "transition": "hard_cut", "purpose": "explain"},
    "shot_reverse": {"size": "Medium Close-up", "angle": "eye-level", "movement": "static / micro-pan",
                     "light": "key + rim", "transition": "hard_cut", "purpose": "dialogue"},
    "reaction": {"size": "Close-up", "angle": "eye-level", "movement": "static",
                 "light": "low-key practical", "transition": "hard_cut", "purpose": "emotion"},
    "meme_card": {"size": "INSERT / GRAPHIC", "angle": "N/A", "movement": "static",
                  "light": "graphic", "transition": "crossfade", "purpose": "punchline"},
    "b_roll": {"size": "Wide / Establishing", "angle": "high-angle", "movement": "slow push-in",
               "light": "ambient", "transition": "crossfade", "purpose": "establish"},
    "end_card": {"size": "GRAPHIC", "angle": "N/A", "movement": "static", "light": "brand",
                 "transition": "fade_black", "purpose": "close"},
    "talking_head": {"size": "Medium Close-up", "angle": "eye-level", "movement": "static",
                     "light": "key + rim", "transition": "hard_cut", "purpose": "explain"},
    "narration": {"size": "Wide", "angle": "high-angle", "movement": "slow push-in",
                  "light": "ambient", "transition": "crossfade", "purpose": "context"},
}

# 转场决策树（OpenMontage video-stitching.md）
TRANSITION_RULES = [
    ("same_scene_continuous", "hard_cut", 0.0),
    ("same_topic_angle", "hard_cut", 0.0),
    ("topic_change", "crossfade", 0.6),
    ("time_or_mood_shift", "crossfade", 1.0),
    ("major_section_break", "fade_black", 0.7),
    ("dialogue_handoff", "L_cut", 0.4),
]


def plan_for_type(shot_type: str, variant: int = 0) -> dict:
    plan = dict(TYPE_PLANS.get(str(shot_type or ""), TYPE_PLANS["host_to_camera"]))
    if variant:
        variants = [
            {},
            {"size": "Medium", "movement": "static"},
            {"size": "Close-up", "angle": "low-angle", "movement": "slow dolly-in"},
        ]
        plan.update(variants[variant % len(variants)])
    plan["type"] = shot_type
    return plan


def plan_segment(segment: dict) -> list[dict]:
    """把一个 segment 的 pattern 展开为镜头列表（每段 1-3 镜）。"""
    plan = []
    patterns = segment.get("pattern") or ["host_to_camera"]
    for i, t in enumerate(patterns[:3]):
        p = plan_for_type(t, variant=i)
        p["segment_id"] = segment.get("id")
        p["segment_name"] = segment.get("name")
        plan.append(p)
    return plan


def plan_episode(segments: list[dict]) -> list[dict]:
    shots = []
    no = 0
    for seg in segments or []:
        for p in plan_segment(seg):
            no += 1
            p["no"] = no
            shots.append(p)
    return shots


SIZE_LABELS = {
    "Wide": "远景", "Wide / Establishing": "远景·环境", "Full": "全景", "Medium": "中景",
    "Medium Close-up": "中近景", "Close-up": "特写", "INSERT / GRAPHIC": "字卡", "GRAPHIC": "字卡",
}
CAMERA_LABELS = {
    "static": "固定", "slow dolly-in": "缓推", "static / micro-pan": "微摇",
    "slow push-in": "缓推", "static": "固定",
}


def label(p: dict) -> str:
    text = "%s · %s · %s" % (
        SIZE_LABELS.get(p.get("size"), p.get("size")),
        p.get("angle") or "",
        CAMERA_LABELS.get(p.get("movement"), p.get("movement")),
    )
    return text.strip(" ·")

# --------------------------------------------------------------------------
# ShotPlan → H3 prompt（contract/SHOT_PLAN_INJECTION.md）
# --------------------------------------------------------------------------
SUBJECT_ANCHOR = (
    "subject_definitions: Subject 1 is the character in the reference image, "
    "preserving their exact face, hairstyle, hair color, outfit, age and "
    "anime cel-shaded art style."
)

# 景别映射：采用 h3-comfy task-37 实测有效的描述（含构图范围）
SIZE_PROMPTS = {
    "Medium": "medium (waist-up)",
    "Medium Close-up": "medium close-up (chest-up)",
    "Close-up": "close-up (head and shoulders)",
    "Extreme Close-up": "extreme close-up (face only)",
    "Full": "full shot (full body)",
    "Wide": "wide establishing shot, full body and studio space visible",
    "Wide / Establishing": "wide establishing shot, full body and studio space visible",
}

# 机位角度：H3 对角度只有弱控制（task-37 结论），仍写入 prompt 供模型参考
ANGLE_PROMPTS = {
    "eye-level": "eye-level angle",
    "low-angle": "low angle, looking slightly up at the subject, below eye-level",
    "high-angle": "high angle, looking slightly down at the subject, above eye-level",
    "over-the-shoulder": "over-the-shoulder angle",
    "POV": "POV angle",
}

# 运镜只能选一类（translation/rotation/lens/hybrid/stillness），禁止混用；
# task-37 实测 only 推近有效，其余类型记录为限制。
MOVEMENT_PROMPTS = {
    "static": "static camera, locked-off, zero camera movement",
    "slow dolly-in": "slow dolly-in toward the face (translation only, no zoom)",
    "slow push-in": "slow push-in (translation only, no zoom)",
    "static / micro-pan": "very slow micro-pan (rotation only, no dolly, no zoom)",
    "pan": "very slow pan (rotation only, no dolly, no zoom)",
    "truck": "slow truck (translation only, no pan, no zoom)",
    "zoom": "slow zoom (lens only, no dolly, no pan)",
    "rack focus": "slow rack focus (lens only, no dolly, no pan)",
}

_ANGLE_HEIGHT = {
    "low-angle": "low", "high-angle": "high", "eye-level": "eye-level",
    "over-the-shoulder": "eye-level", "POV": "eye-level",
}


def _size_phrase(plan: dict) -> str:
    size = str(plan.get("size") or "Medium Close-up")
    return SIZE_PROMPTS.get(size, size)


def _angle_phrase(plan: dict) -> str:
    angle = str(plan.get("angle") or "eye-level")
    return ANGLE_PROMPTS.get(angle, f"{angle} angle")


def _height_phrase(plan: dict) -> str:
    height = _height_for(plan)
    return {"low": "below eye-level (low camera position)",
            "high": "above eye-level (high camera position)",
            "eye-level": "eye-level"}.get(height, height)


def _height_for(plan: dict) -> str:
    if plan.get("height"):
        return str(plan["height"])
    return _ANGLE_HEIGHT.get(str(plan.get("angle") or ""), "eye-level")


def _movement_phrase(plan: dict) -> str:
    movement = str(plan.get("movement") or "static").strip()
    return MOVEMENT_PROMPTS.get(
        movement, f"{movement} (single movement class only; no zoom/pan mix)")


def plan_for_shot(shot: dict, index: int = 0) -> dict:
    """取镜头 plan：优先 shot.shot_plan，否则 plan_for_type(type, no % 3)。"""
    shot = shot or {}
    override = shot.get("shot_plan")
    if isinstance(override, dict) and override:
        plan = plan_for_type(str(shot.get("type") or ""))
        plan.update({k: v for k, v in override.items() if v not in (None, "")})
        plan["source"] = "shot.shot_plan"
        plan["type"] = shot.get("type")
        return plan
    no = shot.get("no")
    try:
        variant = int(no) % 3 if no not in (None, "") else (int(index) + 1) % 3
    except (TypeError, ValueError):
        variant = (int(index) + 1) % 3
    plan = plan_for_type(str(shot.get("type") or ""), variant=variant)
    plan["source"] = "shot_grammar.plan_for_type"
    return plan


def build_h3_prompt(plan: dict, mode: str = "audio_ref") -> str:
    """按 5-aspect 公式拼 H3 prompt（身份锚点 + 景别/角度/高度/光线 + 运镜 + 目的）。"""
    plan = plan or {}
    size = _size_phrase(plan)
    angle = _angle_phrase(plan)
    height = _height_phrase(plan)
    light = str(plan.get("light") or "key + rim")
    movement = _movement_phrase(plan)
    purpose = str(plan.get("purpose") or "dialogue")
    if mode == "fl2v":
        action = (f"Starting from the first frame and ending at the last frame, "
                  f"Subject 1 performs a continuous {size} shot, {angle}, "
                  f"{height} camera height, in a dark studio with {light}, with "
                  f"camera movement {movement} (only this movement, no zoom/pan "
                  f"mix). Subtle body movement, head turn and expression change "
                  f"only; keep identity, framing and lighting consistent from the "
                  f"first frame to the last frame.")
        motion = ("React naturally with subtle body and facial movement. "
                  "No text, no watermark.")
        soundscape = "overall_soundscape: quiet studio room tone, subtle cloth movement."
    else:
        action = (f"One continuous {size} shot, {angle}, {height} camera "
                  f"height, of Subject 1 speaking directly to the camera in a dark "
                  f"studio with {light}. Camera: {movement} (only this movement, "
                  f"no zoom/pan mix).")
        motion = ("Lips move naturally in sync with the reference speech audio; subtle "
                  "blinking, eyebrow movement and small head nods. Keep identity, hair, "
                  "outfit and lighting consistent with the reference image; only the "
                  "mouth, eyes and head move subtly. No text, no watermark.")
        soundscape = ("overall_soundscape: the same spoken sentence, close-mic voice, "
                      "quiet studio room tone.")
    return "\n\n".join([
        SUBJECT_ANCHOR,
        f"summary: {action} Purpose: {purpose}. {motion}",
        soundscape,
        "non_diegetic_music: N/A",
    ])


def plan_label(plan: dict) -> str:
    """给 demo 抽帧/QC 用的简短计划标签。"""
    return "%s / %s / %s / %s" % (
        plan.get("size") or "-", plan.get("angle") or "-",
        _height_for(plan), plan.get("movement") or "-")

