# -*- coding: utf-8 -*-
"""舞台首帧合成（v3）：模板 stage plate + 多角色 cutout 站位 → H3 首帧 / fl2v 尾帧 / 卡片帧。

设计（docs/V3_INTERFACES.md §2 + Lead 实测反馈）：
- 背景 = 模板 stage.plate（1280x720 绘景），按 shot.camera_zone 取景（ZONE_VIEWS 裁切表，
  未知 zone 回落全画面）；角色先放回 plate 空间，再统一 crop + 缩放到输出尺寸，
  避免角色被 zone 二次裁切；
- cast 全部入场（近景/特写只留说话人，见 cast_hidden）：block → 站位，脚底锚在
  stage.layout.ground_y（block.y 作纵深微调）；高度按 shot.size
  （Wide .42H / Full .52H / Medium .60H / Medium Close-up .56H / Close-up .86H）；
  角色按脚底 y 排序做前后遮挡（y 大者更近、后画）；说话人略放大/靠中；
- **按 h3_mode 分流**（Lead 实测：H3 audio_ref 会把首帧重绘干净，fl2v 则帧锁定）：
    audio_ref → 按模板风格选 cutout（cutout_3d 概念图的白色丝带可接受，H3 会重绘）；
    fl2v      → clean 合成：角色一律 cutout_2d（干净透明线稿）；shot.role 为空的
                b_roll/establishing 空镜只用 plate 取景、不叠角色；
- 柔和椭圆投影（PIL 高斯模糊）+ 轻调色（不烘焙文字/字幕/包装，避免 H3 文字伪影）；
- 输出 1024x576 RGB PNG（16:9，≈768² 像素预算，H3 32 倍数；成片再升 1280x720）。

对外接口（勿改签名）：
    load_plate(template) -> Path
    cutout_for(template, character_id) -> dict
    describe_scene(template) -> str
    compose_first_frame(template, shot, out_png, *, width=1024, height=576,
                        workdir=None, index=0, clean=None) -> dict
    compose_last_frame(template, shot, first_meta, out_png, *, width=None, height=None) -> dict | None
    compose_card_frame(template, shot, out_png, *, width=1024, height=576) -> dict

CLI（自测/预览，不占 GPU）：
    python -m pipeline.stage --template tonight_show --limit 4 --contact-sheet
    python -m pipeline.stage --list-zones
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont

from . import config, fonts, style_v2, template_v3, utils

LOG = __import__("logging").getLogger("pipeline")

REPO = config.REPO_ROOT
DEFAULT_W, DEFAULT_H = 1024, 576
PLATE_DIR = REPO / "outputs" / "scene_plates"
CHARS_DIR = REPO / "outputs" / "characters"
# 卡片字体缓存目录（ensure_fonts 只复制一次，~32MB；避免每个输出目录各拷一份）
DEFAULT_FONT_SCRATCH = REPO / "tmp" / "v3_stage" / "_fonts"

# 景别 → 角色画面高度占比（v3 契约；相对输出帧高，脚底锚定时即缩放系数）
SIZE_H = {
    "Wide": 0.42,
    "Wide / Establishing": 0.42,
    "Full": 0.52,
    "Medium": 0.60,
    "Medium Close-up": 0.56,
    "Close-up": 0.86,
    "Extreme Close-up": 1.00,
}
# 景别 → 取景窗宽度区间（camera_zone 表机位语义，size 约束景别；
# template-author 实测 50/147 镜 zone/size 不匹配，这里统一 cap + 锚定）。
# 宽度为 plate 归一化；16:9 plate + 16:9 输出时窗高 = 窗宽（compose 里按真实像素校正）。
SIZE_VIEW_WIDTH = {
    "Wide": (0.90, 1.00),
    "Wide / Establishing": (0.90, 1.00),
    "Full": (0.58, 0.92),
    "Medium": (0.55, 0.80),
    "Medium Close-up": (0.34, 0.42),
    "Close-up": (0.32, 0.38),
    "Extreme Close-up": (0.28, 0.34),
}
# 近景/特写：取景窗锚定说话人、允许脚下不被地平线约束
NEAR_SIZES = {"Medium Close-up", "Close-up", "Extreme Close-up"}
# 近景/特写：画面只留说话人（多人挤在一个特写里没有意义）；记录在 meta.cast_hidden
SPEAKER_ONLY_SIZES = set(NEAR_SIZES)
# 头顶安全边距（输出帧比例）：避免 Close-up 放大后头顶被切
HEAD_MARGIN = 0.045
# 脚底在输出帧的高度（取景窗竖直落点基准，≈地平线）
FOOT_TARGET = 0.90
# 空镜/establishing 取景窗（fl2v 无 role 时只出 plate）
EMPTY_ZONES = {"wide", "wide / establishing", "establishing", "master", "window_wide",
               "stage_wide", "audience", "panel"}

# camera_zone → 在 plate 上的取景窗 [x, y, w, h]（plate 归一化坐标，全画面=0,0,1,1）。
# 精确映射只放高频/语义明确的机位；其余 16 模板共 40+ 个新机位名走下面的关键词规则。
ZONE_VIEWS: dict[str, tuple[float, float, float, float]] = {
    # 通用
    "front": (0.02, 0.05, 0.96, 0.92),
    "wide": (0.03, 0.00, 0.94, 1.00),
    "wide / establishing": (0.03, 0.00, 0.94, 1.00),
    "establishing": (0.03, 0.00, 0.94, 1.00),
    "master": (0.02, 0.00, 0.96, 1.00),
    "two_shot": (0.10, 0.04, 0.82, 0.94),
    "group": (0.00, 0.02, 0.98, 0.96),
    "reaction": (0.34, 0.05, 0.52, 0.90),
    "close": (0.34, 0.06, 0.32, 0.84),
    "close_up": (0.34, 0.06, 0.32, 0.84),
    "host_close": (0.28, 0.06, 0.36, 0.86),
    "guest_ots": (0.30, 0.05, 0.62, 0.92),
    # 深夜秀 / 沙发访谈
    "desk_swap": (0.08, 0.05, 0.66, 0.92),
    "desk_ots": (0.14, 0.03, 0.72, 0.95),
    "sofa_ots": (0.26, 0.05, 0.68, 0.92),
    "window_wide": (0.03, 0.00, 0.94, 1.00),
    "sofa_two_shot": (0.12, 0.04, 0.84, 0.94),
    # 水墨书房 / 历史类
    "seat_ots": (0.10, 0.04, 0.74, 0.94),
    "seat_left_ots": (0.02, 0.04, 0.66, 0.94),
    "seat_right_ots": (0.34, 0.04, 0.66, 0.94),
    "scroll_close": (0.34, 0.08, 0.32, 0.84),
    "desk_top": (0.18, 0.18, 0.64, 0.76),
    "candle_close": (0.36, 0.10, 0.30, 0.80),
    # 舞台 / 演播厅
    "stage_wide": (0.00, 0.00, 1.00, 1.00),
    "stage_center": (0.14, 0.02, 0.72, 0.96),
    "stage_side": (0.02, 0.02, 0.62, 0.96),
    "podium": (0.16, 0.02, 0.68, 0.96),
    "audience": (0.06, 0.00, 0.90, 0.98),
    "booth": (0.14, 0.03, 0.72, 0.95),
    "panel": (0.04, 0.02, 0.92, 0.96),
}

# 关键词规则（16 模板 44 个机位名不再逐个枚举）：
#   high 类  → 上俯（略窄高、画面顶部留白）——优先于 close，避免 overhead 被 "head" 误判；
#   close 类 → 0.25-0.35 画面宽 tight crop，x 以说话人/区块为中心；
#   ots 类   → 贴说话人一侧的过肩/侧向 crop；
#   low 类   → 中景偏下；
#   wide 类  → 0.90-0.94 全画面 zoom。
ZONE_KEYWORD_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("high", ("high", "overhead", "orbit", "crane", "top_down", "bird")),
    ("close", ("close", "mic", "product", "case", "cue", "evidence", "timer",
               "shelf", "door", "lantern", "candle", "window", "screen", "head",
               "table_close", "detail", "insert")),
    ("ots", ("ots", "chair", "guest", "host", "suspect", "investor", "wing",
             "couch", "seat", "affirmative", "negate", "shoulder", "reverse")),
    ("low", ("low_angle", "low angle", "low-angle")),
    ("wide", ("wide", "hall", "stage", "room", "gallery", "kitchen", "noir",
              "warm", "split", "task", "vhs", "fireplace", "archive", "crowd")),
)

# 道具/站位没有独立剪影资产：meta 里记录引用关系，画面由 plate 承载。
META_VERSION = 2
CLEAN_CUTOUT_STYLE = "cutout_2d"


class StageError(RuntimeError):
    """plate/cutout 资产缺失或合成失败。"""


# ---------------------------------------------------------------------------
# 模板/镜头字段归一化（storyboard_v3 产出字段 + 模板原始 shot 字段都兼容）
# ---------------------------------------------------------------------------
SHOT_FIELDS = ("intent", "size", "angle", "movement", "action", "props",
               "h3_mode", "camera_zone", "chair")


def shot_v3(shot: dict) -> dict:
    """把 storyboard shot 归一化成模板镜头字段（v3 优先，其次 shot 本体/shot_plan）。"""
    shot = shot if isinstance(shot, dict) else {}
    out: dict = {}
    v3 = shot.get("v3") if isinstance(shot.get("v3"), dict) else {}
    for key in SHOT_FIELDS:
        if key in v3 and v3.get(key) is not None:
            out[key] = v3[key]
    plan = shot.get("shot_plan") if isinstance(shot.get("shot_plan"), dict) else {}
    for key in ("size", "angle", "movement"):
        if key not in out and plan.get(key):
            out[key] = plan[key]
    for key in SHOT_FIELDS:
        if key not in out and shot.get(key) is not None:
            out[key] = shot[key]
    out["type"] = str(shot.get("type") or v3.get("type") or "")
    out["id"] = str(shot.get("id") or v3.get("id") or "")
    out["line"] = str(shot.get("line") or "")
    out["h3_mode"] = str(out.get("h3_mode") or shot.get("h3_mode") or "")
    return out


def _speaker_role(template: dict, shot: dict) -> str:
    """shot → 说话角色 role（兼容 role / speaker_role / speaker(资产 id) / speaker_name(中文名)）。"""
    shot = shot if isinstance(shot, dict) else {}
    candidates = [shot.get("role"), shot.get("speaker_role"),
                  shot.get("speaker"), shot.get("speaker_asset"),
                  shot.get("speaker_name")]
    cast = template_v3.cast_for(template)
    roles = {str(c.get("role") or "") for c in cast}
    for value in candidates:
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


def _movement(shot: dict) -> str:
    return str(shot_v3(shot).get("movement") or "static").strip().lower()


def _h3_mode(shot: dict) -> str:
    return str(shot_v3(shot).get("h3_mode") or "").strip().lower()


# ---------------------------------------------------------------------------
# 资产定位
# ---------------------------------------------------------------------------
def load_plate(template: dict) -> Path:
    """模板 stage.plate 的真实 PNG 路径；缺失 raise StageError。"""
    path = template_v3.plate_path(template)
    if path is None or not Path(path).is_file():
        plate = str((template.get("stage") or {}).get("plate") or "")
        raise StageError(f"缺少 stage.plate 资产: {plate or '<空>'}（outputs/scene_plates/）")
    return Path(path)


def _alpha_border_frac(im: Image.Image, band_ratio: float = 0.01) -> float:
    """图像外圈不透明占比：真透明 cutout ≈0，背景没抠干净会偏高。"""
    alpha = im.getchannel("A")
    w, h = im.size
    band = max(2, int(min(w, h) * band_ratio))
    total = opaque = 0
    for box in ((0, 0, w, band), (0, h - band, w, h), (0, 0, band, h), (w - band, 0, h, h)):
        hist = alpha.crop(box).histogram()
        total += sum(hist)
        opaque += sum(hist[16:])
    return (opaque / total) if total else 0.0


def _wash_score(im: Image.Image, block: int = 8) -> float:
    """浅色平滑区域占比：baked aura / 背景残影的 cutout 该值很高（干净 cutout < ~0.4）。"""
    rgba = im.convert("RGBA")
    alpha = rgba.getchannel("A")
    rgb = rgba.convert("RGB")
    w, h = rgba.size
    if w < block * 4 or h < block * 4:
        return 0.0
    lum = rgb.resize((w // block, h // block), Image.BILINEAR).convert("L")
    edges = lum.filter(ImageFilter.FIND_EDGES)
    small_alpha = alpha.resize((w // block, h // block), Image.BILINEAR)
    blocks = smooth = 0
    for edge, value, a in zip(edges.getdata(), lum.getdata(), small_alpha.getdata()):
        if a <= 128:
            continue
        blocks += 1
        if edge < 9 and value > 140:
            smooth += 1
    return (smooth / blocks) if blocks else 0.0


_CUTOUT_CACHE: dict[str, dict] = {}


def _cutout_info(path: Path) -> dict:
    """cutout 透明度体检（边框不透明 / 浅色平滑残影），带 mtime 缓存。"""
    key = str(path)
    cached = _CUTOUT_CACHE.get(key)
    mtime = path.stat().st_mtime
    if cached and cached.get("mtime") == mtime:
        return dict(cached)
    info: dict = {"clean": True, "reason": "", "wash": 0.0, "mtime": mtime}
    try:
        with Image.open(path) as raw:
            im = raw.convert("RGBA")
        border = _alpha_border_frac(im)
        wash = _wash_score(im)
        info["wash"] = round(wash, 4)
        if border > 0.02:
            info["clean"] = False
            info["reason"] = f"opaque_border={border:.2f}"
        elif wash > 0.45:
            info["clean"] = False
            info["reason"] = f"baked_wash={wash:.2f}"
    except OSError as exc:  # 坏图当作不可用
        info["clean"] = False
        info["reason"] = f"unreadable:{exc}"
    _CUTOUT_CACHE[key] = dict(info)
    return info


def _requested_cutout_style(template: dict) -> str:
    """模板 style preset → character_style（cutout_3d / cutout_2d）。"""
    presets = style_v2.load_presets()
    name = str(template.get("style") or "")
    preset = (presets.get("presets") or {}).get(name)
    if not isinstance(preset, dict):
        preset = style_v2.profile_for(str(template.get("id") or ""), presets=presets)
    style = str((preset or {}).get("character_style") or "cutout_3d")
    forced = os.environ.get("STAGE_CUTOUT_MODE", "").strip().lower()
    if forced in ("cutout_3d", "cutout_2d"):
        style = forced
    return style


def _style_preset(template: dict) -> dict:
    presets = style_v2.load_presets()
    preset = (presets.get("presets") or {}).get(str(template.get("style") or ""))
    if isinstance(preset, dict):
        return dict(preset)
    return style_v2.profile_for(str(template.get("id") or ""), presets=presets)


_PORTRAIT_FALLBACKS = (
    ("cutout_2d/three_quarter.png", "full_body"),
    ("cutout/portrait.png", "bust"),
    ("2d/portrait.png", "bust"),
    ("portrait.png", "bust"),
    ("portrait_from_concept.png", "portrait"),
)


def _resolve_cutout(template: dict, character_id: str, *, style: str | None = None,
                    require_clean: bool = False) -> dict:
    """cutout 选择核心：按风格优先 → 回退另一套 → 头像/概念图兜底；可要求“干净”。"""
    base = CHARS_DIR / str(character_id or "")
    requested = str(style or _requested_cutout_style(template))
    other = CLEAN_CUTOUT_STYLE if requested != CLEAN_CUTOUT_STYLE else "cutout_3d"
    notes: list[str] = []
    for candidate in (requested, other):
        path = base / candidate / "front.png"
        if not (path.is_file() and path.stat().st_size > 5000):
            notes.append(f"{candidate}: 文件缺失")
            continue
        if require_clean:
            info = _cutout_info(path)
            if not info["clean"]:
                notes.append(f"{candidate}: 不干净（{info['reason']}），fl2v 跳过")
                continue
        return {"path": path, "style": candidate, "fallback": candidate != requested,
                "kind": "full_body", "requested_style": requested,
                "note": "；".join(notes)}
    for rel, kind in _PORTRAIT_FALLBACKS:
        path = base / rel
        if path.is_file() and path.stat().st_size > 2000:
            notes.append(f"回退 {rel}")
            return {"path": path, "style": "portrait", "fallback": True,
                    "kind": kind, "requested_style": requested,
                    "note": "；".join(notes)}
    # 最后一档：沿用不干净的全身边缘资产（记录原因，避免整镜无角色）
    for candidate in (requested, other):
        path = base / candidate / "front.png"
        if path.is_file() and path.stat().st_size > 5000:
            notes.append(f"无干净替代，沿用 {candidate} 瑕疵资产")
            return {"path": path, "style": candidate, "fallback": True,
                    "kind": "full_body", "requested_style": requested,
                    "note": "；".join(notes)}
    return {"path": None, "style": "missing", "fallback": True,
            "kind": "missing", "requested_style": requested,
            "note": "；".join(notes) or "角色资产缺失"}


def cutout_for(template: dict, character_id: str) -> dict:
    """角色立绘（模板风格优先，不因概念图丝带而回退）：见 _resolve_cutout。

    返回 {"path","style","fallback","kind","requested_style","note"}；path 可能为 None。
    """
    return _resolve_cutout(template, character_id, require_clean=False)


def describe_scene(template: dict) -> str:
    """场景一句英文（H3 prompt 用）：stage.scene_desc，缺失用 plate 名映射兜底。"""
    stage = template.get("stage") if isinstance(template.get("stage"), dict) else {}
    desc = str(stage.get("scene_desc") or "").strip()
    if desc:
        return desc
    plate = str(stage.get("plate") or "")
    try:
        from . import style as style_v1

        mapped = style_v1.scene_description(plate)
    except Exception:  # noqa: BLE001 - 兜底描述不阻断合成
        mapped = ""
    if mapped and mapped != "a cinematic anime studio scene":
        return mapped
    cleaned = plate.replace("__", " ").replace("_", " ").strip()
    if cleaned:
        return f"a cinematic anime studio set ({cleaned})"
    return "a cinematic anime studio scene"


# ---------------------------------------------------------------------------
# 取景（camera_zone）
# ---------------------------------------------------------------------------
def zone_kind(zone: str) -> str:
    """camera_zone → 取景类型（exact/close/ots/high/low/wide/fallback）。"""
    key = str(zone or "").strip().lower()
    if not key:
        return "fallback"
    if key in ZONE_VIEWS:
        return "exact"
    parsed = key.replace("-", "_").replace(" ", "_")
    for kind, keywords in ZONE_KEYWORD_RULES:
        for word in keywords:
            if word.replace("-", "_").replace(" ", "_") in parsed:
                return kind
    return "fallback"


def _clamp_view(view, min_w: float = 0.20, min_h: float = 0.20):
    x, y, w, h = (float(v) for v in view)
    w = max(min_w, min(1.0, w))
    h = max(min_h, min(1.0, h))
    x = max(0.0, min(1.0 - w, x))
    y = max(0.0, min(1.0 - h, y))
    return (round(x, 4), round(y, 4), round(w, 4), round(h, 4))


def _side_anchor(x: float | None, width: float) -> float:
    """把锚点 x 映射到取景窗 x0（锚点落在窗内 0.55 处，靠边时贴边）。"""
    if x is None:
        return max(0.0, min(1.0 - width, 0.5 - width / 2))
    return max(0.0, min(1.0 - width, x - width * 0.55))


def zone_view(zone: str, *, size: str | None = None, speaker_x: float | None = None,
              cast_xs: list[float] | None = None) -> tuple[float, float, float, float]:
    """camera_zone（机位语义）× shot.size（景别）→ plate 归一化取景窗。

    精确映射优先，其次关键词规则，最后全画面；给了 size 后按 SIZE_VIEW_WIDTH
    收窄/放宽窗宽（zone 只管机位，size 决定景别）：
    - Wide 下限 0.90；Full ≤0.92；Medium ≤0.80（以 cast 质心为中心，保证多人同框）；
    - MCU/CU/ECU 收到 0.42/0.38/0.34 并用 _side_anchor 锚定说话人（窗内 0.55 处）；
    - 窗高 = 窗宽（16:9 plate → 16:9 输出，compose 里再按真实像素校正，避免拉伸）。
    """
    key = str(zone or "").strip().lower()
    base = ZONE_VIEWS.get(key)
    if base is not None:
        view = tuple(float(v) for v in base)
    else:
        kind = zone_kind(key)
        anchor = speaker_x
        if anchor is None and cast_xs:
            anchor = sum(cast_xs) / len(cast_xs)
        if kind == "close":
            width = 0.32
            view = (_side_anchor(anchor, width), 0.06, width, 0.86)
        elif kind == "ots":
            width = 0.60
            view = (_side_anchor(anchor, width), 0.04, width, 0.93)
        elif kind == "high":
            view = (0.08, 0.01, 0.84, 0.93)
        elif kind == "low":
            view = (0.11, 0.07, 0.78, 0.89)
        elif kind == "wide":
            view = (0.03, 0.00, 0.94, 1.00)
        else:
            view = (0.0, 0.0, 1.0, 1.0)
    if not size:
        return _clamp_view(view)
    bounds = SIZE_VIEW_WIDTH.get(str(size))
    if bounds is None:
        return _clamp_view(view)
    lo, hi = bounds
    width = max(lo, min(hi, view[2]))
    near = str(size) in NEAR_SIZES
    if near:
        x0 = _side_anchor(speaker_x, width)
    else:
        # 中/全景：zone 机位中心 + cast 质心（说话人轻加权）各一半，保证多人仍同框
        centroid = (sum(cast_xs) / len(cast_xs)) if cast_xs else 0.5
        cast_center = centroid if speaker_x is None else (0.35 * speaker_x + 0.65 * centroid)
        zone_center = view[0] + view[2] / 2.0
        center = 0.5 * zone_center + 0.5 * cast_center
        x0 = max(0.0, min(1.0 - width, center - width / 2.0))
    return _clamp_view((x0, view[1], width, width), min_w=0.20, min_h=0.20)


def _expand_view_for_cast(view, cast_xs: list[float], ground_y: float, *,
                          include_ground: bool = True, pad: float = 0.09):
    """保证入画角色都在取景窗内（轻微扩窗）；近景不收地平线约束（允许脚下出画）。"""
    x, y, w, h = (float(v) for v in view)
    expanded = False
    if cast_xs:
        xmin = max(0.0, min(cast_xs) - pad)
        xmax = min(1.0, max(cast_xs) + pad)
        if xmin < x or xmax > x + w:
            nx = min(x, xmin)
            nx1 = max(x + w, xmax)
            x, w = nx, nx1 - nx
            expanded = True
    if include_ground:
        # 脚底线必须在窗内（否则角色会被竖向裁掉）
        if ground_y > y + h:
            h = min(1.0 - y, ground_y - y + 0.02)
            expanded = True
        elif ground_y < y + 0.12:
            y = max(0.0, ground_y - 0.12)
            h = min(1.0 - y, h + (ground_y - y))
            expanded = True
    return _clamp_view((x, y, w, h), min_w=0.20, min_h=0.20), expanded


def _view_px(view, plate_w: int, plate_h: int) -> tuple[int, int, int, int]:
    x, y, w, h = view
    cx = int(round(x * plate_w))
    cy = int(round(y * plate_h))
    cw = max(16, int(round(w * plate_w)))
    ch = max(16, int(round(h * plate_h)))
    cx = max(0, min(plate_w - cw, cx))
    cy = max(0, min(plate_h - ch, cy))
    return (cx, cy, min(cw, plate_w), min(ch, plate_h))


# ---------------------------------------------------------------------------
# 合成工具
# ---------------------------------------------------------------------------
def _open_rgb(path: Path) -> Image.Image:
    with Image.open(path) as raw:
        return raw.convert("RGB")


def _save_png(image: Image.Image, out_png: Path) -> Path:
    """原子写 PNG（RGB），返回绝对路径。"""
    out_png = Path(out_png)
    utils.ensure_dir(out_png.parent)
    tmp = out_png.with_suffix(out_png.suffix + ".tmp")
    image.convert("RGB").save(tmp, format="PNG", optimize=True)
    tmp.replace(out_png)
    return out_png.resolve()


def _ground_line(template: dict) -> float:
    layout = template_v3.stage_layout(template)
    try:
        gy = float(layout.get("ground_y", 0.86))
    except (TypeError, ValueError):
        gy = 0.86
    return max(0.30, min(1.0, gy))


def _foot_frac(layout: dict, block: str, ground_y: float) -> float:
    """脚底相对 plate 的高度：ground_y 为地平线，block.y 略抬高表示更靠里（不悬空）。"""
    pair = layout.get(block)
    block_y = None
    if isinstance(pair, (list, tuple)) and len(pair) == 2:
        try:
            block_y = float(pair[1])
        except (TypeError, ValueError):
            block_y = None
    if block_y is None:
        return ground_y
    if block_y >= ground_y - 0.02:
        return min(block_y, 0.98)
    # block 靠里：最多抬到 ground_y-0.05，既有纵深又不悬空
    depth = min(0.05, (ground_y - block_y) * 0.22)
    return ground_y - depth


def _trim_cutout(im: Image.Image) -> Image.Image:
    """按 alpha 裁掉透明边距，让脚底贴住裁切框底边。"""
    alpha = im.getchannel("A")
    bbox = alpha.point(lambda v: 255 if v > 16 else 0).getbbox()
    return im.crop(bbox) if bbox else im


def _resize_cutout(im: Image.Image, target_h: int) -> Image.Image:
    target_h = max(8, int(target_h))
    w, h = im.size
    target_w = max(1, int(round(w * target_h / max(h, 1))))
    out = im.resize((target_w, target_h), Image.LANCZOS)
    # 轻羽化 alpha，避免硬抠图边缘在 H3 首帧里像贴纸
    alpha = out.getchannel("A").filter(ImageFilter.GaussianBlur(0.6))
    out.putalpha(alpha)
    return out


def _grade_cutout(im: Image.Image) -> Image.Image:
    """角色轻调色：略压亮/降饱和/提对比，融入 plate（scenes.py 同款参数）。"""
    alpha = im.getchannel("A")
    rgb = im.convert("RGB")
    rgb = ImageEnhance.Brightness(rgb).enhance(0.965)
    rgb = ImageEnhance.Color(rgb).enhance(0.95)
    rgb = ImageEnhance.Contrast(rgb).enhance(1.03)
    out = rgb.convert("RGBA")
    out.putalpha(alpha)
    return out


def _vignette_mask(width: int, height: int, strength: float = 0.10) -> Image.Image:
    """径向暗角遮罩（先小图算再放大，避免逐像素大循环）。"""
    gw, gh = 96, 54
    mask = Image.new("L", (gw, gh), 0)
    px = mask.load()
    cx, cy = (gw - 1) / 2.0, (gh - 1) / 2.0
    max_d = math.hypot(cx, cy)
    for y in range(gh):
        for x in range(gw):
            d = math.hypot(x - cx, y - cy) / max_d
            px[x, y] = int(max(0.0, (d - 0.55) / 0.45) ** 1.6 * 255 * strength)
    return mask.resize((width, height), Image.BILINEAR)


def _apply_grade(img: Image.Image) -> Image.Image:
    """成帧轻调色：微对比/微饱和 + 很淡暗角；不烘焙文字包装。"""
    out = ImageEnhance.Contrast(img).enhance(1.02)
    out = ImageEnhance.Color(out).enhance(1.02)
    mask = _vignette_mask(out.width, out.height, 0.10)
    black = Image.new("RGB", out.size, (0, 0, 0))
    return Image.composite(black, out, mask.point(lambda v: int(v * 0.55)))


def _add_shadows(base: Image.Image, shadows: list[dict]) -> Image.Image:
    """统一画柔和椭圆投影（一次高斯模糊），压在角色脚下。"""
    if not shadows:
        return base
    layer = Image.new("RGBA", base.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    max_ry = 8
    for sh in shadows:
        cx, cy = int(sh["cx"]), int(sh["cy"])
        rx = max(12, int(sh["rx"]))
        ry = max(6, int(sh["ry"]))
        max_ry = max(max_ry, ry)
        draw.ellipse((cx - rx, cy - ry, cx + rx, cy + ry), fill=(0, 0, 0, int(sh["alpha"])))
        # 接触阴影（脚下更实一点）
        draw.ellipse((cx - int(rx * 0.62), cy - int(ry * 0.55),
                      cx + int(rx * 0.62), cy + int(ry * 0.55)),
                     fill=(0, 0, 0, int(sh["alpha"] * 0.75)))
    layer = layer.filter(ImageFilter.GaussianBlur(radius=max(4, int(max_ry * 0.85))))
    canvas = base.convert("RGBA")
    canvas.alpha_composite(layer)
    return canvas.convert("RGB")


# ---------------------------------------------------------------------------
# 角色布局
# ---------------------------------------------------------------------------
def _layout_characters(template: dict, shot: dict, view, plate_w: int, plate_h: int,
                       *, clean: bool, speaker: str,
                       tight: bool = False) -> tuple[list[dict], list[str], float]:
    """算每个角色的 plate 空间位置/尺寸（脚底锚定 + 景别 + 说话人处理）。

    clean=True（fl2v）：角色一律 cutout_2d 干净线稿，且无说话人的空镜不叠角色。
    tight=True（close 类 camera_zone）：只留说话人，避免 tight crop 被多人挤爆。
    """
    layout = template_v3.stage_layout(template)
    ground_y = _ground_line(template)
    v3 = shot_v3(shot)
    size = str(v3.get("size") or "Medium")
    size_h = SIZE_H.get(size, 0.55)
    vx, vy, vw, vh = (float(v) for v in view)
    crop_h = max(1.0, vh * plate_h)
    visible: list[dict] = []
    hidden: list[str] = []
    if clean and not speaker:
        return visible, ["clean_empty_shot: 无说话角色（b_roll/establishing），只取景 plate"], ground_y
    for member in template_v3.cast_for(template):
        role = str(member.get("role") or "")
        character = str(member.get("character_id") or member.get("character") or "")
        block = str(member.get("block") or "")
        pair = layout.get(block)
        if not isinstance(pair, (list, tuple)) or len(pair) != 2:
            hidden.append(f"{role}:block({block}) 不存在")
            continue
        try:
            block_x = float(pair[0])
        except (TypeError, ValueError):
            hidden.append(f"{role}:block({block}) 非法")
            continue
        is_speaker = bool(speaker) and role == speaker
        if size in SPEAKER_ONLY_SIZES and speaker and not is_speaker:
            hidden.append(role)
            continue
        if tight and speaker and not is_speaker:
            hidden.append(f"{role}(close_zone)")
            continue
        char_h = size_h * crop_h
        if is_speaker:
            char_h *= 1.05  # 说话人略放大
        # 竖直落点：脚底锚地平线；若头顶会出画则整体下移（特写）
        foot_reference = _foot_frac(layout, block, ground_y)
        foot_out = (foot_reference - vy) / max(vh, 1e-6)
        top_out = foot_out - char_h / crop_h
        if top_out < HEAD_MARGIN:
            foot_out = HEAD_MARGIN + char_h / crop_h
        foot_out = min(foot_out, 1.02)  # Close-up 允许脚底略溢出画面
        foot_plate_px = vy * plate_h + foot_out * crop_h
        x_plate = block_x * plate_w
        if is_speaker:
            x_plate = x_plate * 0.90 + 0.5 * plate_w * 0.10  # 说话人略靠中
        cut_style = CLEAN_CUTOUT_STYLE if clean else None
        cut = _resolve_cutout(template, character, style=cut_style, require_clean=clean)
        if cut.get("path") is None:
            hidden.append(f"{role}:{cut.get('note')}")
            continue
        try:
            with Image.open(cut["path"]) as raw:
                sprite = raw.convert("RGBA")
        except OSError as exc:
            hidden.append(f"{role}:读取 cutout 失败 {exc}")
            continue
        sprite = _grade_cutout(_resize_cutout(_trim_cutout(sprite), int(round(char_h))))
        visible.append({
            "role": role,
            "character": character,
            "name": str(member.get("name") or ""),
            "block": block,
            "speaker": is_speaker,
            "sprite": sprite,
            "char_w": sprite.width,
            "char_h": sprite.height,
            "x_center": x_plate,
            "foot_y": foot_plate_px,
            "cutout": str(cut["path"]),
            "cutout_style": str(cut["style"]),
            "cutout_fallback": bool(cut["fallback"]),
            "cutout_kind": str(cut.get("kind") or ""),
            "cutout_note": str(cut.get("note") or ""),
            "requested_style": str(cut.get("requested_style") or ""),
            "size": size,
        })
    # 前后遮挡：脚底 y 小（远）先画，y 大（近）后画；同高度说话人最后画
    visible.sort(key=lambda c: (round(c["foot_y"], 1), 1 if c["speaker"] else 0))
    return visible, hidden, ground_y


# ---------------------------------------------------------------------------
# 首帧
# ---------------------------------------------------------------------------
def _derive_zone(template: dict, shot: dict) -> str:
    """取 camera_zone；模板 shot 缺省时按景别兜底（Wide→wide，其余 front）。"""
    v3 = shot_v3(shot)
    zone = str(v3.get("camera_zone") or "").strip()
    if zone:
        return zone
    size = str(v3.get("size") or "Medium")
    return "wide" if size in ("Wide", "Wide / Establishing") else "front"


def compose_first_frame(template: dict, shot: dict, out_png, *, width: int = DEFAULT_W,
                        height: int = DEFAULT_H, workdir=None, index: int = 0,
                        clean: bool | None = None) -> dict:
    """合成单镜首帧（plate 取景 + cast 站位 + 投影 + 轻调色）→ RGB PNG。

    shot: storyboard_v3 shot（含 v3 原模板字段）或模板原始 shot 都兼容。
    clean: None=按 shot.h3_mode 自动（fl2v→True，其余→False）；显式 True/False 可覆盖。
    """
    template = template if isinstance(template, dict) else {}
    shot = shot if isinstance(shot, dict) else {}
    plate_path = load_plate(template)
    plate = _open_rgb(plate_path)
    plate_w, plate_h = plate.size
    v3 = shot_v3(shot)
    mode = _h3_mode(shot)
    clean_flag = (mode == "fl2v") if clean is None else bool(clean)
    layout = template_v3.stage_layout(template)
    ground_y = _ground_line(template)
    zone = _derive_zone(template, shot)
    rule = zone_kind(zone)
    size = str(v3.get("size") or "Medium")
    speaker = _speaker_role(template, shot)
    cast = template_v3.cast_for(template)
    cast_xs: list[float] = []
    speaker_x: float | None = None
    for member in cast:
        pair = layout.get(str(member.get("block") or ""))
        if not (isinstance(pair, (list, tuple)) and len(pair) == 2):
            continue
        try:
            x = float(pair[0])
        except (TypeError, ValueError):
            continue
        cast_xs.append(x)
        if speaker and str(member.get("role") or "") == speaker:
            speaker_x = x
    # 只有真正会入画的角色才参与扩窗（near-close / close 机位只留说话人；clean 空镜无角色）
    # 说话人落画时 x 会略“靠中”（见 _layout_characters），机位锚定要用落画后的 x
    speaker_draw_x = (speaker_x * 0.90 + 0.5 * 0.10) if speaker_x is not None else None
    if clean_flag and not speaker:
        relevant_xs: list[float] = []
    elif (size in SPEAKER_ONLY_SIZES or rule == "close") and speaker:
        relevant_xs = [speaker_draw_x] if speaker_draw_x is not None else []
    else:
        relevant_xs = cast_xs
    base = zone_view(zone, size=size, speaker_x=speaker_draw_x, cast_xs=relevant_xs)
    include_ground = size not in NEAR_SIZES
    # 反拉伸：取景窗必须与输出同宽高比（16:9 plate→16:9 输出时 h=w）
    w_norm = base[2]
    h_norm = w_norm * plate_w * height / max(1, width * plate_h)
    # 竖直落点：脚底锚在地平线（输出 90% 高度），近景天然头顶贴近上沿
    y_norm = ground_y - FOOT_TARGET * h_norm
    view = _clamp_view((base[0], y_norm, w_norm, h_norm))
    view, expanded = _expand_view_for_cast(view, relevant_xs, ground_y,
                                           include_ground=include_ground, pad=0.07)
    # 扩窗保 cast 全入画后仍轻微超 size cap：允许最多 +0.08（中景群像视觉仍成立），
    # 超出部分以窗中心回缩（极端情况下角色边缘被裁，但优先保证景别不被撑成 wide）
    bounds = SIZE_VIEW_WIDTH.get(size)
    capped = False
    if bounds is not None and include_ground and view[2] > bounds[1]:
        max_w = min(1.0, bounds[1] + (0.08 if size == "Medium" else 0.06))
        if view[2] > max_w:
            center = view[0] + view[2] / 2.0
            view = _clamp_view((center - max_w / 2.0, view[1], max_w, view[3]))
            capped = True
    if abs(view[2] - w_norm) > 1e-6:  # 扩窗/回缩改了宽度 → 重算窗高/落点，保持 16:9
        h_norm = view[2] * plate_w * height / max(1, width * plate_h)
        view = _clamp_view((view[0], ground_y - FOOT_TARGET * h_norm, view[2], h_norm))
    crop_x, crop_y, crop_w, crop_h = _view_px(view, plate_w, plate_h)
    real_view = (crop_x / plate_w, crop_y / plate_h, crop_w / plate_w, crop_h / plate_h)

    characters, hidden, _ = _layout_characters(
        template, shot, real_view, plate_w, plate_h, clean=clean_flag, speaker=speaker,
        tight=(rule == "close"))

    canvas = plate.copy()
    # 1) 柔和椭圆投影（脚下）
    shadows = [{
        "cx": char["x_center"],
        "cy": char["foot_y"] - 2,
        "rx": char["char_w"] * 0.72,
        "ry": max(6, char["char_h"] * 0.032),
        "alpha": 105,
    } for char in characters]
    canvas = _add_shadows(canvas, shadows)
    # 2) 角色（按脚底 y 排序后的顺序贴图：后画的更近）
    canvas = canvas.convert("RGBA")
    for char in characters:
        left = int(round(char["x_center"] - char["char_w"] / 2.0))
        top = int(round(char["foot_y"] - char["char_h"]))
        canvas.alpha_composite(char["sprite"], (left, top))
    canvas = canvas.convert("RGB")
    # 3) 取景 crop → 输出尺寸（唯一一次缩放，角色不会被二次裁切）
    frame = canvas.crop((crop_x, crop_y, crop_x + crop_w, crop_y + crop_h))
    frame = frame.resize((int(width), int(height)), Image.LANCZOS)
    frame = _apply_grade(frame)
    out_path = _save_png(frame, Path(out_png))

    # 输出像素空间 bbox：plate 像素 → 输出像素（[x, y, w, h]）
    def out_x(px: float) -> float:
        return (px - crop_x) / max(crop_w, 1) * width

    def out_y(py: float) -> float:
        return (py - crop_y) / max(crop_h, 1) * height

    char_meta = []
    for char in characters:
        left = char["x_center"] - char["char_w"] / 2.0
        top = char["foot_y"] - char["char_h"]
        x0, y0 = out_x(left), out_y(top)
        x1, y1 = out_x(left + char["char_w"]), out_y(char["foot_y"])
        char_meta.append({
            "role": char["role"],
            "character": char["character"],
            "name": char["name"],
            "block": char["block"],
            "speaker": char["speaker"],
            "bbox": [int(round(x0)), int(round(y0)),
                     int(round(x1 - x0)), int(round(y1 - y0))],
            "foot_y": int(round(y1)),
            "cutout": char["cutout"],
            "cutout_style": char["cutout_style"],
            "cutout_fallback": char["cutout_fallback"],
            "cutout_kind": char["cutout_kind"],
            "requested_style": char["requested_style"],
            "cutout_note": char["cutout_note"],
        })
    props_meta = []
    prop_ids = list(v3.get("props") or [])
    props_by_id = template_v3.props_by_id(template)
    for pid in prop_ids:
        prop = props_by_id.get(str(pid)) or {}
        props_meta.append({"id": str(pid), "zone": str(prop.get("zone") or ""),
                           "interaction": str(prop.get("interaction") or "")})
    preset = _style_preset(template)
    meta = {
        "path": str(out_path),
        "width": int(width),
        "height": int(height),
        "plate": str(plate_path),
        "plate_size": [plate_w, plate_h],
        "camera_zone": zone,
        "camera_zone_rule": rule,
        "camera_zone_known": rule != "fallback",
        # crop 为 plate 归一化 [x, y, w, h]（接口契约），像素值另存 crop_px
        "crop": [round(real_view[0], 4), round(real_view[1], 4),
                 round(real_view[2], 4), round(real_view[3], 4)],
        "crop_px": [crop_x, crop_y, crop_w, crop_h],
        "view_expanded": bool(expanded),
        "crop_capped": bool(capped),
        "characters": char_meta,
        "cast_hidden": hidden,
        "props": props_meta,
        "ground_y": round(ground_y, 4),
        "size": str(v3.get("size") or ""),
        "angle": str(v3.get("angle") or ""),
        "movement": str(v3.get("movement") or ""),
        "action": str(v3.get("action") or ""),
        "h3_mode": mode,
        "clean": bool(clean_flag),
        "speaker_role": speaker,
        "style": str(template.get("style") or ""),
        "character_style": _requested_cutout_style(template),
        "style_prefix": str(preset.get("prompt_prefix") or ""),
        "h3_input": True,
        "index": int(index),
        "meta_version": META_VERSION,
    }
    return meta


# ---------------------------------------------------------------------------
# fl2v 尾帧
# ---------------------------------------------------------------------------
def _motion_spec(movement: str) -> dict | None:
    """movement 字符串 → 尾帧运动参数；None 表示无相机运动（尾帧返回 None）。"""
    text = str(movement or "static").strip().lower().replace("_", " ").replace("-", " ")
    if not text:
        text = "static"
    if "static" in text and "micro" not in text:
        return None
    speed = "slow"
    amount = 0.05
    if any(k in text for k in ("whip", "fast", "quick")):
        speed, amount = "fast", 0.08
    elif "micro" in text:
        speed, amount = "micro", 0.015
    if any(k in text for k in ("pull", "zoom out", "out")):
        return {"kind": "zoom_out", "amount": amount, "speed": speed}
    if any(k in text for k in ("truck", "tracking", "track")):
        direction = -1.0 if "left" in text else (1.0 if "right" in text else 1.0)
        return {"kind": "truck", "amount": amount, "dir": direction, "speed": speed}
    if any(k in text for k in ("pan", "orbit")):
        direction = -1.0 if "left" in text else (1.0 if "right" in text else 1.0)
        return {"kind": "pan", "amount": amount * 0.85, "dir": direction, "speed": speed}
    if any(k in text for k in ("rack", "focus")):
        return {"kind": "push_in", "amount": 0.025, "speed": "micro"}
    # dolly / push / zoom / 其它运动统一按缓推处理
    return {"kind": "push_in", "amount": amount, "speed": speed}


def _zoom_out_frame(frame: Image.Image, amount: float) -> Image.Image:
    """拉远：画面缩到中心，四周用模糊压暗的自身延展填充（3-8% 内不明显）。"""
    w, h = frame.size
    amount = max(0.02, min(0.10, amount))
    inner = frame.resize((max(16, int(w * (1 - amount))), max(16, int(h * (1 - amount)))),
                         Image.LANCZOS)
    backdrop = frame.resize((w, h), Image.LANCZOS).filter(ImageFilter.GaussianBlur(max(6, w // 48)))
    backdrop = ImageEnhance.Brightness(backdrop).enhance(0.82)
    backdrop.paste(inner, ((w - inner.width) // 2, (h - inner.height) // 2))
    return backdrop


def compose_last_frame(template: dict, shot: dict, first_meta: dict, out_png, *,
                       width: int | None = None, height: int | None = None) -> dict | None:
    """fl2v 尾帧：在首帧基础上按 shot.movement 做 3-8% 推/移/摇（PIL 裁切重采样）。

    无相机运动（static）返回 None，调用方用首帧兜底。
    """
    first_meta = first_meta if isinstance(first_meta, dict) else {}
    src = first_meta.get("path")
    if not src or not Path(src).is_file():
        raise StageError(f"首帧不存在，无法生成尾帧: {src}")
    spec = _motion_spec(_movement(shot))
    if spec is None:
        return None
    with Image.open(src) as raw:
        frame = raw.convert("RGB")
    w = int(width or first_meta.get("width") or DEFAULT_W)
    h = int(height or first_meta.get("height") or DEFAULT_H)
    if frame.size != (w, h):
        frame = frame.resize((w, h), Image.LANCZOS)
    kind = spec["kind"]
    amount = float(spec["amount"])
    if kind == "zoom_out":
        out = _zoom_out_frame(frame, amount)
        motion = {"kind": "zoom_out", "movement": _movement(shot),
                  "zoom": round(1.0 - amount, 4),
                  "description": f"slow pull-out {amount * 100:.0f}% (synthetic edge extension)"}
    else:
        k = 1.0 - amount
        cw, ch = max(16, int(round(w * k))), max(16, int(round(h * k)))
        if kind == "push_in":
            cx, cy = (w - cw) // 2, (h - ch) // 2
        elif kind == "truck":
            moving_left = float(spec.get("dir", 1.0)) < 0
            cx = int((w - cw) * 0.9) if not moving_left else 0
            cy = (h - ch) // 2
        else:  # pan
            moving_left = float(spec.get("dir", 1.0)) < 0
            cx = 0 if moving_left else int((w - cw) * 0.8)
            cy = (h - ch) // 2
        out = frame.crop((cx, cy, cx + cw, cy + ch)).resize((w, h), Image.LANCZOS)
        motion = {"kind": kind, "movement": _movement(shot), "zoom": round(w / cw, 4),
                  "offset": [int(cx), int(cy)],
                  "description": f"{kind} {amount * 100:.0f}% (PIL crop + resample)"}
    out = ImageEnhance.Contrast(out).enhance(1.01)
    out_path = _save_png(out, Path(out_png))
    return {"path": str(out_path), "width": w, "height": h, "motion": motion,
            "first_frame": str(Path(src).resolve()), "h3_input": True,
            "static_card": False}


# ---------------------------------------------------------------------------
# 卡片帧（片头/片尾）
# ---------------------------------------------------------------------------
def _font_paths(scratch: Path) -> dict:
    info = fonts.ensure_fonts(scratch)
    base = Path(scratch)
    return {
        "body": str((base / str(info["body"]["file"])).resolve()),
        "display": str((base / str(info["display"]["file"])).resolve()),
    }


def _load_font(path: str, size: int) -> ImageFont.FreeTypeFont:
    try:
        return ImageFont.truetype(path, max(8, int(size)))
    except OSError:
        return ImageFont.load_default()


def _fit_font(path: str, text: str, max_w: int, start: int,
              min_size: int = 20) -> ImageFont.FreeTypeFont:
    size = max(min_size, int(start))
    while size > min_size:
        font = _load_font(path, size)
        try:
            box = font.getbbox(text)
        except Exception:  # noqa: BLE001
            break
        if box[2] - box[0] <= max_w:
            return font
        size -= 4
    return _load_font(path, min_size)


def _centered_text(draw: ImageDraw.ImageDraw, xy, text: str, font, fill,
                   anchor: str = "mm", shadow: bool = True) -> None:
    x, y = xy
    if shadow:
        draw.text((x + 2, y + 2), text, font=font, fill=(0, 0, 0, 170), anchor=anchor)
    draw.text((x, y), text, font=font, fill=fill, anchor=anchor)


def _card_title(template: dict, shot: dict) -> tuple[str, str, str, str]:
    """(卡片类型, 标题, 副标题, 品牌行)。兼容 storyboard 卡片/模板 shot/自由 dict。"""
    shot = shot if isinstance(shot, dict) else {}
    kind = str(shot.get("type") or shot.get("kind") or shot.get("card_type")
               or shot.get("intent") or "title_card")
    is_end = "end" in kind.lower()
    template_label = str(template.get("label") or template.get("id") or "")
    title = ""
    for key in ("title", "text", "main", "headline", "label", "name"):
        value = shot.get(key)
        if isinstance(value, str) and value.strip():
            title = value.strip()
            break
    subtitle = ""
    for key in ("subtitle", "tagline", "sub", "desc", "caption"):
        value = shot.get(key)
        if isinstance(value, str) and value.strip():
            subtitle = value.strip()
            break
    brand = ""
    for key in ("brand", "footer"):
        value = shot.get(key)
        if isinstance(value, str) and value.strip():
            brand = value.strip()
            break
    if not brand:
        brand = f"{config.BRAND_NAME} · {config.BRAND_TAGLINE}"
    if not title:
        title = "下期见" if is_end else (template_label or config.BRAND_NAME)
    if not subtitle:
        subtitle = (template_label
                    if (is_end and template_label and template_label != title)
                    else config.BRAND_TAGLINE)
    return ("end_card" if is_end else "title_card"), title, subtitle, brand


def compose_card_frame(template: dict, shot: dict, out_png, *, width: int = DEFAULT_W,
                       height: int = DEFAULT_H) -> dict:
    """片头/片尾卡片帧（静态，允许非 H3；QC 标记 static_card）。"""
    template = template if isinstance(template, dict) else {}
    shot = shot if isinstance(shot, dict) else {}
    out_png = Path(out_png)
    utils.ensure_dir(out_png.parent)
    try:
        font_paths = _font_paths(DEFAULT_FONT_SCRATCH)
    except RuntimeError:  # 字体缺失不阻断卡片（降级到 PIL 默认字体）
        font_paths = {"body": "", "display": ""}
    plate_path = template_v3.plate_path(template)
    if plate_path is not None and Path(plate_path).is_file():
        bg = _open_rgb(Path(plate_path))
        bg = bg.resize((int(width), int(height)), Image.LANCZOS)
        bg = bg.filter(ImageFilter.GaussianBlur(max(2, width // 220)))
        bg = ImageEnhance.Brightness(bg).enhance(0.34)
        bg = ImageEnhance.Color(bg).enhance(0.75)
    else:
        bg = Image.new("RGB", (int(width), int(height)), (18, 18, 24))
    kind, title, subtitle, brand = _card_title(template, shot)
    canvas = bg.convert("RGBA")
    # 上下压暗，突出文字
    overlay = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    od = ImageDraw.Draw(overlay)
    od.rectangle((0, 0, canvas.width, int(canvas.height * 0.18)), fill=(0, 0, 0, 60))
    od.rectangle((0, int(canvas.height * 0.82), canvas.width, canvas.height), fill=(0, 0, 0, 90))
    canvas.alpha_composite(overlay)
    draw = ImageDraw.Draw(canvas)
    accent = config.ACCENT_DEFAULT
    try:
        pack = style_v2.PACKAGING.get(str(template.get("style") or ""), {})
        accent = str(pack.get("color") or accent).replace("0x", "#")
    except Exception:  # noqa: BLE001
        pass
    accent_rgb = accent if len(accent) != 7 else accent + "cc"
    cx = canvas.width / 2
    is_end = kind == "end_card"
    title_y = canvas.height * (0.47 if is_end else 0.45)
    main = f"「{title}」" if (is_end and len(title) <= 6) else title
    title_font = _fit_font(font_paths["display"], main, int(canvas.width * 0.80),
                           start=int(canvas.height * 0.17))
    _centered_text(draw, (cx, title_y), main, title_font, (255, 255, 255, 255))
    tbox = title_font.getbbox(main)
    title_h = tbox[3] - tbox[1]
    line_y = title_y + title_h / 2 + canvas.height * 0.055
    draw.line((cx - canvas.width * 0.09, line_y, cx + canvas.width * 0.09, line_y),
              fill=accent_rgb, width=max(2, canvas.height // 180))
    if subtitle:
        sub_font = _fit_font(font_paths["body"], subtitle, int(canvas.width * 0.76),
                             start=int(canvas.height * 0.062), min_size=16)
        _centered_text(draw, (cx, line_y + canvas.height * 0.075), subtitle,
                       sub_font, (240, 232, 214, 235))
    # 左上角品牌 / 左下角 kicker
    brand_font = _load_font(font_paths["body"], max(16, int(canvas.height * 0.032)))
    draw.text((int(canvas.width * 0.045), int(canvas.height * 0.05)), brand,
              font=brand_font, fill=(255, 255, 255, 190))
    kicker = "片头" if not is_end else "片尾"
    kicker_font = _load_font(font_paths["body"], max(14, int(canvas.height * 0.028)))
    draw.text((int(canvas.width * 0.045), int(canvas.height * 0.90)),
              f"{kicker} · {str(template.get('label') or template.get('id') or '')}",
              font=kicker_font, fill=(230, 220, 205, 170))
    frame = canvas.convert("RGB")
    frame = ImageEnhance.Contrast(frame).enhance(1.02)
    out_path = _save_png(frame, out_png)
    return {
        "path": str(out_path),
        "width": int(width),
        "height": int(height),
        "static_card": True,
        "h3_input": False,
        "kind": kind,
        "title": title,
        "subtitle": subtitle,
        "brand": brand,
        "template": str(template.get("id") or ""),
    }


# ---------------------------------------------------------------------------
# 便捷：整模板预览（自测/给 Lead 看构图，不占 GPU）
# ---------------------------------------------------------------------------
def _guess_preview_shot(template: dict, raw_shot: dict) -> dict:
    """模板原始 shot → storyboard 形状的预览 shot（补 role/说话人，供构图分流）。"""
    item = {"v3": dict(raw_shot)}
    item.update({k: raw_shot.get(k) for k in ("size", "angle", "movement", "action",
                                              "props", "h3_mode", "camera_zone", "intent")})
    item["id"] = str(raw_shot.get("intent") or raw_shot.get("id") or "shot")
    v3 = item["v3"]
    zone = str(v3.get("camera_zone") or "").lower()
    size = str(v3.get("size") or "")
    role = ""
    if not (zone in EMPTY_ZONES or size == "Wide / Establishing"):
        action = template_v3.actions_by_id(template).get(str(v3.get("action") or "")) or {}
        prop = str(action.get("prop") or "")
        cast = template_v3.cast_for(template)
        if prop:
            for member in cast:
                if prop in [str(p) for p in (member.get("props") or [])]:
                    role = str(member.get("role") or "")
                    break
        if not role:
            host = next((c for c in cast if str(c.get("role") or "").startswith("host")), None)
            role = str((host or (cast[0] if cast else {})).get("role") or "")
    item["role"] = role
    item["speaker_name"] = ""
    return item


def compose_preview(template: dict, out_dir, *, limit: int = 4, width: int = DEFAULT_W,
                    height: int = DEFAULT_H) -> dict:
    """按模板 segments 顺序合成 limit 张首帧（含 fl2v 尾帧）+ 1 张卡片，返回汇总。"""
    out_dir = Path(out_dir)
    utils.ensure_dir(out_dir)
    shots = [_guess_preview_shot(template, shot) for shot in template_v3.shots(template)]
    picked: list[dict] = []
    if shots:
        # 优先音频镜（构图主证据），再 fl2v 干净帧，再顺延
        audio = [s for s in shots if _h3_mode(s) == "audio_ref"]
        fl2v = [s for s in shots if _h3_mode(s) == "fl2v"]
        for shot in audio[:2] + fl2v[:1] + shots:
            if shot not in picked:
                picked.append(shot)
            if len(picked) >= max(1, limit):
                break
    picked = picked[:max(1, limit)]
    results = []
    for i, shot in enumerate(picked):
        sid = str(shot.get("id") or f"shot_{i:02d}")
        first = out_dir / f"{i:02d}_{sid}_first.png"
        meta = compose_first_frame(template, shot, first, width=width, height=height, index=i)
        utils.write_json(out_dir / f"{i:02d}_{sid}_first.json", meta)
        entry = {"shot": i, "id": sid, "meta": meta, "last": None}
        if _h3_mode(shot) == "fl2v":
            last_png = out_dir / f"{i:02d}_{sid}_last.png"
            last = compose_last_frame(template, shot, meta, last_png)
            if last is not None:
                utils.write_json(out_dir / f"{i:02d}_{sid}_last.json", last)
                entry["last"] = last
        results.append(entry)
    card_shot = {"type": "title_card",
                 "title": str(template.get("label") or template.get("id") or "")}
    card = compose_card_frame(template, card_shot, out_dir / "card_title.png",
                              width=width, height=height)
    utils.write_json(out_dir / "card_title.json", card)
    summary = {
        "template": str(template.get("id") or ""),
        "out_dir": str(out_dir.resolve()),
        "shots": results,
        "card": card,
        "count": len(results),
        "size": [int(width), int(height)],
    }
    utils.write_json(out_dir / "preview.json", summary)
    return summary


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def _make_contact_sheet(images: list[Path], out_png: Path, cols: int = 2,
                        label: str = "") -> Path | None:
    tiles = []
    for path in images:
        if not Path(path).is_file():
            continue
        with Image.open(path) as raw:
            tiles.append(raw.convert("RGB"))
    if not tiles:
        return None
    tw, th = tiles[0].size
    scale = 0.5 if cols <= 2 else 0.34
    cell_w, cell_h = int(tw * scale), int(th * scale)
    rows = int(math.ceil(len(tiles) / cols))
    pad = 8
    top = 22 if label else 0
    sheet = Image.new("RGB", (cols * cell_w + (cols + 1) * pad,
                              rows * cell_h + (rows + 1) * pad + top), (14, 14, 18))
    draw = ImageDraw.Draw(sheet)
    if label:
        draw.text((pad, 6), label, fill=(240, 230, 200))
    for i, tile in enumerate(tiles):
        r, c = divmod(i, cols)
        x = pad + c * (cell_w + pad)
        y = pad + top + r * (cell_h + pad)
        sheet.paste(tile.resize((cell_w, cell_h), Image.LANCZOS), (x, y))
    out_png = Path(out_png)
    utils.ensure_dir(out_png.parent)
    sheet.save(out_png, format="PNG", optimize=True)
    return out_png


def main(argv: list[str] | None = None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass
    ap = argparse.ArgumentParser(
        prog="python -m pipeline.stage",
        description="v3 舞台首帧合成（plate + 多角色站位 + camera zone，输出 1024x576 PNG）")
    ap.add_argument("--template", action="append", default=None,
                    help="模板 id（可多次；默认 tonight_show + history_talk）")
    ap.add_argument("--out", default=None, help="输出目录（默认 tmp/v3_stage）")
    ap.add_argument("--limit", type=int, default=4, help="每模板合成镜数（默认 4）")
    ap.add_argument("--width", type=int, default=DEFAULT_W)
    ap.add_argument("--height", type=int, default=DEFAULT_H)
    ap.add_argument("--list-zones", action="store_true", help="列出 camera_zone 取景表")
    ap.add_argument("--contact-sheet", action="store_true", help="输出 contact sheet")
    args = ap.parse_args(argv)
    if args.list_zones:
        for name, zone in sorted(ZONE_VIEWS.items()):
            print(f"{name:18s} {zone}")
        return 0
    template_ids = args.template or ["tonight_show", "history_talk"]
    base_out = Path(args.out) if args.out else REPO / "tmp" / "v3_stage"
    utils.setup_logging()
    summaries = []
    for tid in template_ids:
        template = template_v3.load(tid)
        out_dir = base_out / tid
        summary = compose_preview(template, out_dir, limit=args.limit,
                                  width=args.width, height=args.height)
        images = [Path(s["meta"]["path"]) for s in summary["shots"]]
        if summary.get("card"):
            images.append(Path(summary["card"]["path"]))
        if args.contact_sheet:
            sheet = _make_contact_sheet(images, out_dir / "contact_sheet.png",
                                        label=f"{tid} · {template.get('label') or ''}")
            summary["contact_sheet"] = str(sheet) if sheet else ""
            utils.write_json(out_dir / "preview.json", summary)
        summaries.append(summary)
        print(f"[stage] {tid}: {summary['count']} 镜 → {out_dir} "
              f"(card={Path(summary['card']['path']).name})")
    print(json.dumps({"ok": True, "templates": [
        {"template": s["template"], "out_dir": s["out_dir"], "count": s["count"],
         "contact_sheet": s.get("contact_sheet", "")} for s in summaries]},
        ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
