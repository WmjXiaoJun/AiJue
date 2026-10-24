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
