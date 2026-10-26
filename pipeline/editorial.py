# -*- coding: utf-8 -*-
"""V5.4 剪辑/镜头/分镜层（editorial）。

每个模板一份剪辑档案 ``contract/v5/editorial/<template>.json``（由 template-author 编写，
缺失时用按风格的内置档案兜底）。本模块把"分镜/剪辑"落到逐镜参数：

- ``shot_size``：EWS/WS/MS/MCU/CU/OTS/DETAIL/INSERT（景别）
- ``camera``：运镜（push_in/pull_out/truck_left/.../whip_pan 等，motion2d 支持为准，不支持自动回退）
- ``zone``：取景窗（scene.camera_zones 名称，按景别挑选；保证角色头安全）
- ``transition_out``：到下一镜的转场（cut/dissolve/fade/wipe/hblur/vblur/circleopen/pixelize/...）
- ``j_cut``/``l_cut``：音频 J/L 切（秒）
- ``insert``：无角色插入镜（DETAIL/INSERT 时清空角色，避免裁头）

设计参考（见 docs/PLAN_V5_4_EDITORIAL.md §1）：
- libopenshot/OpenShot：每个 clip 边界独立 transition + in/out/keyframe 模型
- OpenTimelineIO：EDL 轨道结构；open-montage：蒙太奇序列节奏
- cinematic-shotlist-toolkit：subject/action/setting/camera/pacing/frame 字段
- parallax-maker：2.5D 视差分层（我们已有 depth）
- ffmpeg xfade：转场执行（per-boundary）
"""
from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path

from . import config

LOG = logging.getLogger("pipeline")
REPO = Path(config.REPO_ROOT)
EDITORIAL_DIR = REPO / "contract" / "v5" / "editorial"

#: 对话镜允许的景别（不允许会裁掉头部的 ECU/DETAIL/INSERT/EWS）
DIALOGUE_SIZES = ("MS", "MCU", "WS", "OTS", "MS", "MCU")

#: V5.4 新运镜在旧 motion2d 上的回退（motion-dev 升级后自动使用真实类型）
CAMERA_FALLBACK = {
    "crane_up": "dolly", "crane_down": "dolly",
    "truck_left": "pan_left", "truck_right": "pan_right",
    "tilt_up": "pan_right", "tilt_down": "pan_left",
    "dolly_zoom": "push_in", "rack_focus": "push_in",
    "orbit": "pan_right", "whip_pan": "pan_right",
}

BROLL_SIZES = ("EWS", "WS", "DETAIL", "INSERT", "CU", "WS")
SIZE_ORDER = ("EWS", "WS", "MS", "MCU", "CU", "ECU", "OTS", "DETAIL", "INSERT")

#: 景别 → 取景窗优先关键词（scene.camera_zones 名称包含即优先）
SIZE_ZONE_HINTS = {
    "EWS": ("wide", "establish", "full", "master"),
    "WS": ("wide", "full", "establish"),
    "MS": ("medium", "mid", "front", "speaker", "host", "table"),
    "MCU": ("medium", "front", "speaker", "host"),
    "CU": ("close", "front", "host", "detail"),
    "ECU": ("close", "detail", "eye", "mouth"),
    "OTS": ("host", "front", "over", "speaker"),
    "DETAIL": ("detail", "close", "map", "prop", "scroll", "board", "cup"),
    "INSERT": ("detail", "close", "table", "map", "board"),
}

#: 内置兜底档案（按风格 technique；template-author 的 editorial/<id>.json 优先）
DEFAULT_PROFILES = {
    "ink_chronicle": dict(
        label="水墨史书·沉静叙事",
        pacing={"lead_in": 1.35, "mid": 1.0, "climax": 0.88, "coda": 1.2},
        opening={"shot": "EWS", "camera": "crane_down", "hold": 2.2},
        closing={"shot": "MS", "camera": "pull_out", "hold": 2.0},
        shot_sequence=["EWS", "WS", "MS", "OTS", "CU", "DETAIL", "MS", "WS", "MCU"],
        camera_palette=["crane_down", "dolly", "pan_right", "push_in", "rack_focus", "pull_out"],
        transition_palette=["cut", "cut", "dissolve", "fade", "wipeleft"],
        transition_policy={"default": "cut", "hard_cut_ratio": 0.55,
                           "dissolve_on_scene_change": True, "max_transition_sec": 0.55},
        j_cut_ratio=0.25, l_cut_ratio=0.25, insert_ratio=0.25, detail_ratio=0.2,
        rhythm={"fast_shots_max": 2.4, "slow_shots_min": 3.4},
    ),
    "woodblock_classic": dict(
        label="版刻典籍·章回节奏",
        pacing={"lead_in": 1.3, "mid": 1.0, "climax": 0.85, "coda": 1.25},
        opening={"shot": "EWS", "camera": "crane_up", "hold": 2.0},
        closing={"shot": "WS", "camera": "pull_out", "hold": 2.0},
        shot_sequence=["EWS", "MS", "MCU", "WS", "CU", "OTS", "MS", "DETAIL"],
        camera_palette=["crane_up", "push_in", "pan_left", "dolly", "rack_focus", "pull_out"],
        transition_palette=["cut", "dissolve", "wipeleft", "cut", "wipeup"],
        transition_policy={"default": "cut", "hard_cut_ratio": 0.5,
                           "dissolve_on_scene_change": True, "max_transition_sec": 0.5},
        j_cut_ratio=0.2, l_cut_ratio=0.3, insert_ratio=0.2, detail_ratio=0.15,
        rhythm={"fast_shots_max": 2.5, "slow_shots_min": 3.3},
    ),
    "parchment_case": dict(
        label="羊皮纸档案·调查剪辑",
        pacing={"lead_in": 1.25, "mid": 1.0, "climax": 0.8, "coda": 1.15},
        opening={"shot": "WS", "camera": "push_in", "hold": 1.8},
        closing={"shot": "MCU", "camera": "pull_out", "hold": 1.8},
        shot_sequence=["WS", "MS", "CU", "OTS", "DETAIL", "INSERT", "MS", "MCU"],
        camera_palette=["push_in", "rack_focus", "pan_left", "dolly", "tilt_down", "pull_out"],
        transition_palette=["cut", "cut", "dissolve", "fade", "wiperight"],
        transition_policy={"default": "cut", "hard_cut_ratio": 0.6,
                           "dissolve_on_scene_change": True, "max_transition_sec": 0.5},
        j_cut_ratio=0.25, l_cut_ratio=0.15, insert_ratio=0.3, detail_ratio=0.25,
        rhythm={"fast_shots_max": 2.2, "slow_shots_min": 3.0},
    ),
    "museum_gold": dict(
        label="博物馆金黑·庄重受控",
        pacing={"lead_in": 1.4, "mid": 1.05, "climax": 0.9, "coda": 1.3},
        opening={"shot": "EWS", "camera": "crane_down", "hold": 2.4},
        closing={"shot": "WS", "camera": "pull_out", "hold": 2.2},
        shot_sequence=["EWS", "MS", "MCU", "WS", "CU", "DETAIL", "MS", "OTS"],
        camera_palette=["crane_down", "crane_up", "push_in", "pan_right", "dolly", "rack_focus"],
        transition_palette=["cut", "fade", "dissolve", "cut", "wipeleft"],
        transition_policy={"default": "cut", "hard_cut_ratio": 0.55,
                           "dissolve_on_scene_change": True, "max_transition_sec": 0.6},
        j_cut_ratio=0.15, l_cut_ratio=0.35, insert_ratio=0.2, detail_ratio=0.25,
        rhythm={"fast_shots_max": 2.6, "slow_shots_min": 3.6},
    ),
    "neon_toon": dict(
        label="霓虹卡通·综艺快剪",
        pacing={"lead_in": 1.0, "mid": 0.85, "climax": 0.7, "coda": 1.1},
        opening={"shot": "WS", "camera": "truck_right", "hold": 1.4},
        closing={"shot": "MS", "camera": "pull_out", "hold": 1.4},
        shot_sequence=["WS", "MS", "MCU", "CU", "OTS", "MS", "DETAIL", "MCU", "WS"],
        camera_palette=["truck_right", "push_in", "whip_pan", "dolly_zoom", "handheld",
                        "tilt_up", "pull_out"],
        transition_palette=["cut", "hblur", "wiperight", "cut", "slideleft", "zoom"],
        transition_policy={"default": "cut", "hard_cut_ratio": 0.45,
                           "dissolve_on_scene_change": False, "max_transition_sec": 0.4},
        j_cut_ratio=0.4, l_cut_ratio=0.15, insert_ratio=0.15, detail_ratio=0.1,
        rhythm={"fast_shots_max": 1.8, "slow_shots_min": 2.8},
    ),
    "silk_arena": dict(
        label="绢本辩论·对垒剪辑",
        pacing={"lead_in": 1.15, "mid": 0.95, "climax": 0.8, "coda": 1.15},
        opening={"shot": "WS", "camera": "push_in", "hold": 1.8},
        closing={"shot": "MS", "camera": "pull_out", "hold": 1.8},
        shot_sequence=["WS", "MS", "OTS", "MCU", "CU", "MS", "DETAIL", "OTS", "MCU"],
        camera_palette=["push_in", "rack_focus", "pan_right", "orbit", "dolly", "tilt_up"],
        transition_palette=["cut", "dissolve", "wipeleft", "cut", "fade"],
        transition_policy={"default": "cut", "hard_cut_ratio": 0.5,
                           "dissolve_on_scene_change": True, "max_transition_sec": 0.45},
        j_cut_ratio=0.45, l_cut_ratio=0.2, insert_ratio=0.15, detail_ratio=0.1,
        rhythm={"fast_shots_max": 2.0, "slow_shots_min": 3.0},
    ),
    "hologram_stage": dict(
        label="全息舞台·电子快切",
        pacing={"lead_in": 1.05, "mid": 0.85, "climax": 0.72, "coda": 1.1},
        opening={"shot": "WS", "camera": "truck_left", "hold": 1.5},
        closing={"shot": "MS", "camera": "pull_out", "hold": 1.5},
        shot_sequence=["WS", "MCU", "MS", "CU", "OTS", "DETAIL", "WS", "MCU"],
        camera_palette=["truck_left", "orbit", "dolly_zoom", "push_in", "whip_pan", "tilt_up"],
        transition_palette=["cut", "hblur", "pixelize", "circleopen", "cut", "radial"],
        transition_policy={"default": "cut", "hard_cut_ratio": 0.4,
                           "dissolve_on_scene_change": False, "max_transition_sec": 0.45},
        j_cut_ratio=0.35, l_cut_ratio=0.1, insert_ratio=0.15, detail_ratio=0.1,
        rhythm={"fast_shots_max": 1.9, "slow_shots_min": 2.9},
    ),
    "vhs_late_show": dict(
        label="80s VHS·老带剪辑",
        pacing={"lead_in": 1.1, "mid": 0.95, "climax": 0.82, "coda": 1.1},
        opening={"shot": "WS", "camera": "handheld", "hold": 1.6},
        closing={"shot": "MS", "camera": "pull_out", "hold": 1.6},
        shot_sequence=["WS", "MS", "MCU", "OTS", "CU", "DETAIL", "MS", "WS"],
        camera_palette=["handheld", "push_in", "pan_right", "tilt_down", "dolly", "pull_out"],
        transition_palette=["cut", "hblur", "dissolve", "cut", "fade"],
        transition_policy={"default": "cut", "hard_cut_ratio": 0.5,
                           "dissolve_on_scene_change": False, "max_transition_sec": 0.4},
        j_cut_ratio=0.25, l_cut_ratio=0.25, insert_ratio=0.15, detail_ratio=0.1,
        rhythm={"fast_shots_max": 2.1, "slow_shots_min": 3.0},
    ),
    "film_interview": dict(
        label="暖光胶片·访谈剪辑",
        pacing={"lead_in": 1.25, "mid": 1.0, "climax": 0.9, "coda": 1.2},
        opening={"shot": "WS", "camera": "push_in", "hold": 2.0},
        closing={"shot": "MCU", "camera": "pull_out", "hold": 2.0},
        shot_sequence=["WS", "MS", "MCU", "OTS", "CU", "DETAIL", "MS", "WS"],
        camera_palette=["push_in", "rack_focus", "pan_left", "dolly", "handheld", "pull_out"],
        transition_palette=["cut", "dissolve", "cut", "fade", "wipeleft"],
        transition_policy={"default": "cut", "hard_cut_ratio": 0.6,
                           "dissolve_on_scene_change": True, "max_transition_sec": 0.5},
        j_cut_ratio=0.35, l_cut_ratio=0.25, insert_ratio=0.15, detail_ratio=0.1,
        rhythm={"fast_shots_max": 2.3, "slow_shots_min": 3.2},
    ),
    "noir_rain": dict(
        label="黑色电影·悬疑慢剪",
        pacing={"lead_in": 1.4, "mid": 1.1, "climax": 0.9, "coda": 1.3},
        opening={"shot": "WS", "camera": "push_in", "hold": 2.4},
        closing={"shot": "CU", "camera": "pull_out", "hold": 2.0},
        shot_sequence=["WS", "MS", "CU", "OTS", "DETAIL", "MS", "MCU", "WS"],
        camera_palette=["push_in", "dolly", "rack_focus", "pan_right", "tilt_down", "pull_out"],
        transition_palette=["cut", "fade", "dissolve", "cut", "blinds"],
        transition_policy={"default": "cut", "hard_cut_ratio": 0.6,
                           "dissolve_on_scene_change": True, "max_transition_sec": 0.6},
        j_cut_ratio=0.2, l_cut_ratio=0.4, insert_ratio=0.15, detail_ratio=0.15,
        rhythm={"fast_shots_max": 2.6, "slow_shots_min": 3.6},
    ),
    "oil_salon": dict(
        label="油画沙龙·古典长镜",
        pacing={"lead_in": 1.45, "mid": 1.1, "climax": 0.92, "coda": 1.35},
        opening={"shot": "EWS", "camera": "crane_down", "hold": 2.6},
        closing={"shot": "WS", "camera": "pull_out", "hold": 2.2},
        shot_sequence=["EWS", "WS", "MS", "MCU", "OTS", "CU", "MS", "DETAIL"],
        camera_palette=["crane_down", "orbit", "push_in", "pan_left", "dolly", "pull_out"],
        transition_palette=["cut", "dissolve", "fade", "cut", "wipeleft"],
        transition_policy={"default": "cut", "hard_cut_ratio": 0.55,
                           "dissolve_on_scene_change": True, "max_transition_sec": 0.6},
        j_cut_ratio=0.15, l_cut_ratio=0.35, insert_ratio=0.2, detail_ratio=0.2,
        rhythm={"fast_shots_max": 2.8, "slow_shots_min": 3.8},
    ),
    "baroque_candle": dict(
        label="巴洛克烛光·慢推悬疑",
        pacing={"lead_in": 1.4, "mid": 1.05, "climax": 0.9, "coda": 1.3},
        opening={"shot": "WS", "camera": "crane_up", "hold": 2.4},
        closing={"shot": "CU", "camera": "pull_out", "hold": 2.0},
        shot_sequence=["WS", "MS", "CU", "OTS", "DETAIL", "MS", "MCU", "WS"],
        camera_palette=["crane_up", "push_in", "orbit", "rack_focus", "tilt_up", "pull_out"],
        transition_palette=["cut", "fade", "dissolve", "cut", "fadeblack"],
        transition_policy={"default": "cut", "hard_cut_ratio": 0.55,
                           "dissolve_on_scene_change": True, "max_transition_sec": 0.7},
        j_cut_ratio=0.2, l_cut_ratio=0.35, insert_ratio=0.2, detail_ratio=0.2,
        rhythm={"fast_shots_max": 2.7, "slow_shots_min": 3.7},
    ),
    "blueprint_lab": dict(
        label="科技蓝图·路演快剪",
        pacing={"lead_in": 1.15, "mid": 0.9, "climax": 0.75, "coda": 1.1},
        opening={"shot": "WS", "camera": "truck_right", "hold": 1.6},
        closing={"shot": "MS", "camera": "pull_out", "hold": 1.6},
        shot_sequence=["WS", "MS", "MCU", "CU", "DETAIL", "OTS", "WS", "MCU"],
        camera_palette=["truck_right", "push_in", "dolly_zoom", "tilt_up", "pan_left", "rack_focus"],
        transition_palette=["cut", "wipeleft", "circleopen", "cut", "pixelize"],
        transition_policy={"default": "cut", "hard_cut_ratio": 0.45,
                           "dissolve_on_scene_change": False, "max_transition_sec": 0.4},
        j_cut_ratio=0.3, l_cut_ratio=0.2, insert_ratio=0.25, detail_ratio=0.25,
        rhythm={"fast_shots_max": 2.0, "slow_shots_min": 3.0},
    ),
    "candy_collage": dict(
        label="糖果拼贴·鬼畜快剪",
        pacing={"lead_in": 1.0, "mid": 0.8, "climax": 0.68, "coda": 1.05},
        opening={"shot": "WS", "camera": "truck_left", "hold": 1.2},
        closing={"shot": "MS", "camera": "pull_out", "hold": 1.3},
        shot_sequence=["WS", "MCU", "CU", "MS", "DETAIL", "MCU", "WS", "OTS"],
        camera_palette=["truck_left", "whip_pan", "dolly_zoom", "push_in", "handheld", "tilt_down"],
        transition_palette=["cut", "hblur", "pixelize", "slideleft", "cut", "radial"],
        transition_policy={"default": "cut", "hard_cut_ratio": 0.35,
                           "dissolve_on_scene_change": False, "max_transition_sec": 0.35},
        j_cut_ratio=0.35, l_cut_ratio=0.1, insert_ratio=0.2, detail_ratio=0.15,
        rhythm={"fast_shots_max": 1.6, "slow_shots_min": 2.6},
    ),
    "clay_diorama": dict(
        label="黏土定格·治愈慢剪",
        pacing={"lead_in": 1.45, "mid": 1.12, "climax": 0.95, "coda": 1.35},
        opening={"shot": "EWS", "camera": "static", "hold": 2.6},
        closing={"shot": "WS", "camera": "pull_out", "hold": 2.4},
        shot_sequence=["EWS", "WS", "MS", "MCU", "DETAIL", "MS", "WS", "CU"],
        camera_palette=["static", "push_in", "pan_right", "handheld", "tilt_up", "pull_out"],
        transition_palette=["fade", "cut", "dissolve", "cut", "fade"],
        transition_policy={"default": "fade", "hard_cut_ratio": 0.45,
                           "dissolve_on_scene_change": True, "max_transition_sec": 0.7},
        j_cut_ratio=0.1, l_cut_ratio=0.35, insert_ratio=0.25, detail_ratio=0.2,
        rhythm={"fast_shots_max": 2.8, "slow_shots_min": 3.8},
    ),
    "chalk_theatre": dict(
        label="粉笔剧场·小品切",
        pacing={"lead_in": 1.2, "mid": 0.95, "climax": 0.8, "coda": 1.15},
        opening={"shot": "WS", "camera": "static", "hold": 1.8},
        closing={"shot": "MS", "camera": "pull_out", "hold": 1.8},
        shot_sequence=["WS", "MS", "OTS", "CU", "MCU", "DETAIL", "WS", "MS"],
        camera_palette=["static", "push_in", "whip_pan", "pan_left", "handheld", "tilt_up"],
        transition_palette=["cut", "wipeup", "cut", "fade", "hblur"],
        transition_policy={"default": "cut", "hard_cut_ratio": 0.6,
                           "dissolve_on_scene_change": False, "max_transition_sec": 0.4},
        j_cut_ratio=0.3, l_cut_ratio=0.2, insert_ratio=0.15, detail_ratio=0.1,
        rhythm={"fast_shots_max": 2.1, "slow_shots_min": 3.1},
    ),
}

#: 16 风格 → template 档案兜底映射（template 无 editorial 文件时）
TEMPLATE_STYLE_FALLBACK = {}


# ---------------------------------------------------------------------------
# 档案加载
# ---------------------------------------------------------------------------
def load_editorial(template_id: str, style: dict | None = None) -> dict:
    """读模板剪辑档案；缺失时按风格 technique 兜底。"""
    path = EDITORIAL_DIR / f"{template_id}.json"
    if path.is_file():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict) and data.get("shot_sequence"):
                return data
        except (OSError, json.JSONDecodeError) as exc:
            LOG.warning("editorial 档案读取失败 %s: %s", path, exc)
    techniques = [str(t) for t in ((style or {}).get("technique") or [])]
    for tech in techniques:
        if tech in DEFAULT_PROFILES:
            prof = dict(DEFAULT_PROFILES[tech])
            prof["id"] = template_id
            prof.setdefault("notes", f"内置兜底档案（{tech}）")
            return prof
    style_id = str((style or {}).get("id") or "")
    for key, prof in DEFAULT_PROFILES.items():
        if key in style_id:
            out = dict(prof)
            out["id"] = template_id
            out.setdefault("notes", f"内置兜底档案（{key}）")
            return out
    prof = dict(DEFAULT_PROFILES["film_interview"])
    prof["id"] = template_id
    prof["notes"] = "通用兜底档案"
    return prof


def templates_with_editorial() -> list[str]:
    if not EDITORIAL_DIR.is_dir():
        return []
    return sorted(p.stem for p in EDITORIAL_DIR.glob("*.json"))


# ---------------------------------------------------------------------------
# 剪辑计划
# ---------------------------------------------------------------------------
def _stable_rng(template_id: str, seed: int, salt: str = "") -> "random.Random":
    import random
    digest = hashlib.sha1(f"{template_id}|{seed}|{salt}".encode("utf-8")).hexdigest()
    return random.Random(int(digest[:12], 16))



#: 虚拟取景窗（归一化 [x,y,w,h]）：角色镜由 editorial 合成，不依赖 scene JSON zones
VIRTUAL_ZONES = {
    "EWS": [0.0, 0.0, 1.0, 1.0],
    "WS":  [0.04, 0.03, 0.92, 0.94],
    "MS":  [0.14, 0.10, 0.72, 0.81],
    "MCU": [0.24, 0.12, 0.54, 0.72],
    "OTS": [0.10, 0.10, 0.70, 0.85],
    "CU":  [0.30, 0.20, 0.40, 0.55],
    "DETAIL": [0.32, 0.30, 0.36, 0.40],
    "INSERT": [0.22, 0.22, 0.56, 0.56],
}


def _zone_candidates(zones: dict) -> list[tuple[str, list[float], float]]:
    out = []
    for name, rect in (zones or {}).items():
        try:
            x, y, w, h = [float(v) for v in rect[:4]]
        except (TypeError, ValueError):
            continue
        out.append((str(name), [x, y, w, h], float(max(1e-6, w * h))))
    out.sort(key=lambda item: item[2], reverse=True)
    return out


def _pick_zone_for_size(zones: dict, size: str, anchor=None) -> tuple[str | None, list[float] | None]:
    """返回 (zone_name, rect)。角色镜优先 scene 命名窗（含锚点/够大），否则用虚拟窗。"""
    candidates = _zone_candidates(zones)
    if size in ("EWS", "WS", "MS", "MCU", "OTS"):
        best = None
        if anchor and candidates:
            ax, ay = float(anchor[0]), float(anchor[1])
            for name, rect, area in candidates:
                x, y, w, h = rect
                if area < 0.30:
                    continue
                if not (x + 0.02 * w <= ax <= x + 0.98 * w):
                    continue
                if not (y + 0.02 * h <= ay <= y + 0.98 * h):
                    continue
                if 0.6 <= (w / max(1e-6, h)) <= 2.0:
                    best = (name, rect)
                    break
        if best is not None and size in ("EWS", "WS"):
            return best
        rect = list(VIRTUAL_ZONES.get(size) or VIRTUAL_ZONES["MS"])
        return f"v5_{size.lower()}", rect
    # b-roll 细节/插入：优先 scene 的 detail/close 小窗，否则虚拟窗
    hints = SIZE_ZONE_HINTS.get(size, ("detail", "close"))
    for hint in hints:
        for name, rect, area in candidates:
            if hint in name.lower() and area < 0.45:
                return name, rect
    rect = list(VIRTUAL_ZONES.get(size) or VIRTUAL_ZONES["DETAIL"])
    return f"v5_{size.lower()}", rect


def _camera_for_shot(size: str, palette: list[str], rng, idx: int) -> str:
    """按景别挑运镜（兼容景别优先，再按 idx 轮换避免重复）。"""
    if not palette:
        return "push_in"
    prefer = {
        "EWS": ("crane", "truck", "pan", "static", "dolly"),
        "WS": ("crane", "truck", "pan", "dolly", "push"),
        "MS": ("push", "dolly", "rack", "pan", "static", "orbit"),
        "MCU": ("push", "rack", "orbit", "dolly", "tilt"),
        "CU": ("push", "rack", "dolly_zoom", "whip", "orbit"),
        "ECU": ("push", "rack", "dolly_zoom", "whip"),
        "OTS": ("push", "rack", "orbit", "dolly"),
        "DETAIL": ("push", "rack", "tilt", "pan", "static"),
        "INSERT": ("tilt", "pan", "push", "static"),
    }.get(size, ("push",))
    compatible = [c for c in palette if any(p in c for p in prefer)]
    pool = compatible or list(palette)
    return pool[(idx * 3 + idx // 2) % len(pool)]


def _transition_for_boundary(profile: dict, *, scene_change: bool, is_card: bool,
                             rng, idx: int) -> dict:
    policy = profile.get("transition_policy") or {}
    palette = [str(t) for t in (profile.get("transition_palette") or ["cut"])]
    if is_card:
        return {"mode": "fade", "duration": 0.5}
    hard_ratio = float(policy.get("hard_cut_ratio", 0.55))
    dissolve_on_change = bool(policy.get("dissolve_on_scene_change", True))
    max_dur = float(policy.get("max_transition_sec", 0.6))
    if scene_change and dissolve_on_change and rng.random() < 0.55:
        return {"mode": "dissolve", "duration": round(min(max_dur, 0.5), 2)}
    if rng.random() < hard_ratio:
        return {"mode": "cut", "duration": 0.0}
    non_cut = [t for t in palette if t not in ("cut", "fadeblack")]
    mode = non_cut[idx % len(non_cut)] if non_cut else "fade"
    if mode == "blinds":
        mode = "vertopen"
    return {"mode": mode, "duration": round(min(max_dur, 0.4 + 0.1 * (idx % 3)), 2)}


def _norm_camera(cam: str, supported) -> str:
    if not cam:
        return "push_in"
    if not supported or cam in supported:
        return cam
    return CAMERA_FALLBACK.get(cam, "push_in")


def build_edit_plan(template: dict, plan: dict, style: dict, *, seed: int = 0,
                    zones_by_scene: dict | None = None,
                    anchors_by_scene: dict | None = None,
                    supported_cameras=None) -> dict:
    """为 plan 的每个 body shot 生成剪辑参数；返回 {"profile","shots","summary"}。

    - 对话镜只用 DIALOGUE_SIZES（保证演员头不被裁）；b-roll 用 BROLL_SIZES
    - DETAIL/INSERT 且无台词 → 作为"无角色插入镜"（清空 characters）
    - 输出 rng 确定性（template id + seed）
    """
    shots = [s for s in (plan.get("shots") or []) if str(s.get("type")) != "card"]
    profile = load_editorial(str(template.get("id") or ""), style)
    rng = _stable_rng(str(template.get("id") or "tpl"), seed)
    seq = [str(x) for x in (profile.get("shot_sequence") or ["WS", "MS", "CU"])]
    camera_palette = [str(x) for x in (profile.get("camera_palette") or ["push_in"])]
    trans_palette = [str(x) for x in (profile.get("transition_palette") or ["cut"])]
    policy = profile.get("transition_policy") or {}
    j_ratio = float(profile.get("j_cut_ratio", 0.25))
    l_ratio = float(profile.get("l_cut_ratio", 0.2))
    insert_ratio = float(profile.get("insert_ratio", 0.15))
    detail_ratio = float(profile.get("detail_ratio", 0.15))
    rhythm = profile.get("rhythm") or {}
    open_spec = profile.get("opening") or {}
    close_spec = profile.get("closing") or {}
    zones_by_scene = zones_by_scene or {}

    edits: list[dict] = []
    last_scene = None
    sizes_seen: list[str] = []
    dialogue_idx = 0
    broll_idx = 0
    for i, shot in enumerate(shots):
        sid = str(shot.get("id"))
        scene = str(shot.get("scene") or "")
        has_line = bool(str(shot.get("line") or "").strip())
        desired = str(seq[i % len(seq)]) if seq else ""
        if i == 0 and open_spec.get("shot"):
            size = str(open_spec["shot"])
        elif i == len(shots) - 1 and close_spec.get("shot"):
            size = str(close_spec["shot"])
        elif has_line:
            allowed = list(DIALOGUE_SIZES)
            if desired in allowed:
                size = desired
            else:
                size = allowed[(dialogue_idx) % len(allowed)]
            dialogue_idx += 1
        else:
            if desired in BROLL_SIZES:
                size = desired
            else:
                size = BROLL_SIZES[broll_idx % len(BROLL_SIZES)]
            broll_idx += 1
            # insert/detail 比例：把部分 b-roll 变成 DETAIL/INSERT
            if rng.random() < insert_ratio:
                size = "INSERT"
            elif size not in ("DETAIL", "INSERT") and rng.random() < detail_ratio:
                size = "DETAIL"
        # 避免相邻同景别（同类别内顺延）
        if sizes_seen and sizes_seen[-1] == size:
            pool = [x for x in (DIALOGUE_SIZES if has_line else BROLL_SIZES) if x != size]
            if pool:
                # 优先 shot_sequence 里相邻的另一种景别
                nxt = [x for x in seq if x in pool]
                size = nxt[i % len(nxt)] if nxt else pool[i % len(pool)]
        sizes_seen.append(size)
        # 运镜
        camera = str(open_spec.get("camera")) if i == 0 and open_spec.get("camera") else None
        if camera is None and i == len(shots) - 1 and close_spec.get("camera"):
            camera = str(close_spec["camera"])
        if not camera:
            camera = _camera_for_shot(size, camera_palette, rng, i)
        camera = _norm_camera(camera, supported_cameras)
        # 取景窗（scene zones）
        scene_zones = zones_by_scene.get(scene) or {}
        anchor = None
        if has_line:
            anchors = (anchors_by_scene or {}).get(scene) or {}
            role = str(shot.get("role") or "")
            anchor = anchors.get(role) or anchors.get("host")
        zone_name, zone_rect = _pick_zone_for_size(scene_zones, size, anchor)
        # J/L cut（仅对话镜）
        j_cut = round(0.18 + 0.12 * rng.random(), 2) if (has_line and rng.random() < j_ratio) else 0.0
        l_cut = round(0.15 + 0.15 * rng.random(), 2) if (has_line and rng.random() < l_ratio) else 0.0
        edits.append({
            "shot": sid, "index": i, "scene": scene, "has_line": has_line,
            "shot_size": size, "camera": camera,
            "zone": zone_name, "zone_rect": zone_rect,
            "j_cut": j_cut, "l_cut": l_cut,
            "insert": bool(size in ("DETAIL", "INSERT") and not has_line),
            "transition_out": None,
        })
    # 转场（到下一镜；最后一镜 → 片尾卡 fade）
    for idx, e in enumerate(edits):
        if idx == len(edits) - 1:
            e["transition_out"] = {"mode": "fade", "duration": 0.5}
            continue
        nxt = edits[idx + 1]
        scene_change = e["scene"] != nxt["scene"]
        rng2 = _stable_rng(str(template.get("id") or "tpl"), seed, f"trans{idx}")
        e["transition_out"] = _transition_for_boundary(
            profile, scene_change=scene_change, is_card=False, rng=rng2, idx=idx)
    # 硬切比例校正：至少 1 个 cut；若比例偏离 target 太多，翻转部分边界
    if len(edits) >= 3:
        target = float(policy.get("hard_cut_ratio", 0.55))
        n_b = len(edits) - 1
        cuts = [i for i, e in enumerate(edits[:-1])
                if (e.get("transition_out") or {}).get("mode") == "cut"]
        want = max(1, int(round(target * n_b)))
        rng3 = _stable_rng(str(template.get("id") or "tpl"), seed, "rebalance")
        if len(cuts) < want:
            non_cut = [i for i, e in enumerate(edits[:-1])
                       if (e.get("transition_out") or {}).get("mode") != "cut"]
            rng3.shuffle(non_cut)
            for i in non_cut[:want - len(cuts)]:
                edits[i]["transition_out"] = {"mode": "cut", "duration": 0.0}
        elif len(cuts) > want + 1:
            rng3.shuffle(cuts)
            for i in cuts[want:]:
                edits[i]["transition_out"] = {"mode": "dissolve", "duration": 0.45}

    # 应用到 plan shots
    by_id = {str(s.get("id")): s for s in (plan.get("shots") or [])}
    for e in edits:
        shot = by_id.get(e["shot"])
        if not shot:
            continue
        shot["edit"] = e
        shot.setdefault("camera", {})
        if isinstance(shot["camera"], dict):
            cam = shot["camera"]
            cam["type"] = e["camera"]
            cam.setdefault("strength", 0.08)
            cam.setdefault("ease", "in_out")
            if e["zone"]:
                cam["zone"] = e["zone_rect"]
        if e["insert"]:
            shot["characters"] = []
            shot["character"] = None
            shot["line"] = ""
            shot["line"] = ""
    # 汇总
    sizes = [e["shot_size"] for e in edits]
    cams = [e["camera"] for e in edits]
    trans = [e["transition_out"]["mode"] for e in edits[:-1] if e.get("transition_out")]
    hard = sum(1 for m in trans if m == "cut") / max(1, len(trans))
    summary = {
        "template": str(template.get("id") or ""),
        "label": profile.get("label") or "",
        "shots": len(edits),
        "shot_sizes": sorted(set(sizes)),
        "camera_types": sorted(set(cams)),
        "transitions": sorted(set(trans)),
        "hard_cut_ratio": round(hard, 3),
        "j_cuts": sum(1 for e in edits if e["j_cut"] > 0),
        "l_cuts": sum(1 for e in edits if e["l_cut"] > 0),
        "inserts": sum(1 for e in edits if e["insert"]),
        "signature": f"{len(edits)} 镜 · {len(set(sizes))} 景别 · {len(set(cams))} 运镜 · {len(set(trans))} 转场",
        "profile": profile,
    }
    plan.setdefault("meta", {})["edit"] = summary
    return {"profile": profile, "shots": edits, "summary": summary}


def validate_plan(edit: dict, *, min_sizes: int = 4, min_cameras: int = 3,
                  min_transitions: int = 2) -> dict:
    """剪辑计划硬门：景别/运镜/转场多样性 + 相邻不同景别。"""
    shots = edit.get("shots") or []
    errors = []
    if len(shots) < 2:
        errors.append("镜头数 < 2")
    sizes = [s.get("shot_size") for s in shots]
    cams = {s.get("camera") for s in shots}
    trans = {s.get("transition_out", {}).get("mode") for s in shots[:-1]}
    trans.discard(None)
    if len(set(sizes)) < min_sizes:
        errors.append(f"景别种类 {len(set(sizes))} < {min_sizes}")
    if len(cams) < min_cameras:
        errors.append(f"运镜种类 {len(cams)} < {min_cameras}")
    if len(trans) < min_transitions:
        errors.append(f"转场种类 {len(trans)} < {min_transitions}")
    adjacent_same = [sizes[i] for i in range(1, len(sizes)) if sizes[i] == sizes[i - 1]]
    if adjacent_same:
        errors.append(f"相邻同景别 {len(adjacent_same)} 处")
    return {"ok": not errors, "errors": errors,
            "summary": edit.get("summary") or {}}


__all__ = [
    "EDITORIAL_DIR", "DEFAULT_PROFILES", "load_editorial", "templates_with_editorial",
    "build_edit_plan", "validate_plan", "SIZE_ZONE_HINTS", "DIALOGUE_SIZES", "BROLL_SIZES",
]
