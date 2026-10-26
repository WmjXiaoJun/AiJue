# -*- coding: utf-8 -*-
"""V5 2D 分层动画渲染器（motion2d）。

契约见 ``docs/V5_INTERFACES.md`` §4（签名不得改）：

    render_shot(layers, characters, camera, duration, fps, out_mp4, style, *,
                width=1280, height=720, seed=0, workdir=None) -> dict
    compose_frame(layers, characters_state, camera_state, size, t) -> PIL.Image
    render_still(layers, characters_state, camera_state, size, t=0.0, out_png=None)

分工
----
* ``pipeline/scene_render.py`` 负责**静态**分层底图（background / midground /
  foreground + depth 视差系数 + anchors 角色锚点）；
* 本模块负责**动态**：相机运动（视差推拉摇移 / 手持抖动）、角色动画（口型、
  眨眼、呼吸点头）、动态特效层（雨/雪/烛火/粒子/雾/速度线）、逐帧合成并把裸
  RGB 帧直接写入 ffmpeg stdin（不落盘），输出 h264/yuv420p、无音轨 mp4。

性能要点（1280x720，纯 CPU，实测见 ``docs/V5_MOTION.md`` §5）
-------------------------------------------------------------
1. 图层一次性准备：RGBA → 预扩边（overscan，防止推拉摇移露边）+ numpy 连续数组；
2. 每帧仿射用 OpenCV ``warpAffine``（~0.6ms/层；无 cv2 时回退 PIL ``transform``）；
3. 合成走 PIL ``paste(..., mask=alpha)`` 且**只贴内容包围盒**；不透明底层整块
   paste，不读 alpha，省掉一次全帧混合；
4. 特效层预先烘成「黑底 RGB 纹理」，逐帧只做滚动切片 + ``cv2.addWeighted``
   （加色混合，~0.5ms/层），避免每帧画几百条雨丝；
5. 角色立绘按 (pose, mouth, blink, flip) **懒缓存**，逐帧只做一次小的仿射 + 局部
   paste；
6. 帧数据 ``PIL -> bytearray -> ffmpeg.stdin`` 直通，无临时 png 序列。

相机语义（详见 ``docs/V5_MOTION.md`` §2 / §2.7）
----------------------------------------------
``camera = {"type": "push_in|...|whip_pan", "strength": 0.08, "ease": "in_out",
           "shake": 0.0, "zone": ...}``

基础 7 种：``static`` ``push_in`` ``pull_out`` ``pan_left`` ``pan_right``
``dolly`` ``handheld``。
V5.4 电影级 10 种：``truck_left`` ``truck_right`` ``tilt_up`` ``tilt_down``
``crane_up`` ``crane_down`` ``dolly_zoom`` ``rack_focus`` ``orbit`` ``whip_pan``
（``SUPPORTED_CAMERA_TYPES`` 共 17 种）。

* ``strength``：推拉 = 画面缩放增量（0.08 → 放大 8%）；摇移/横移/纵摇/升降 = 中景
  参考平面位移占画面宽/高的比例；``dolly_zoom`` = 变焦量；``orbit`` = 弧线幅度；
  ``whip_pan`` = 甩镜位移；手持 = 漂移幅度。
* ``ease``：``linear`` / ``in`` / ``out`` / ``in_out`` / ``in_out_sine`` /
  ``in_sine`` / ``out_sine`` / ``in_out_quart`` / ``in_out_cubic`` /
  ``in_out_expo`` / ``out_back`` / ``in_out_back`` / ``in_out_circ`` 或自定义
  ``callable(p) -> p``。
* ``shake``：额外抖动幅度（占画面短边比例），手持默认 0.008；``shake_freq`` 调基频。
* ``parallax``：视差指数（默认 0.65），见 ``_parallax_ratio``。
* 可选 ``zone_from`` / ``zone_to`` / ``zone``：归一化取景窗（或 ``camera_zones`` 里的
  名字），见 ``_zone_framing`` / ``_resolve_zone_refs``。

V5.4 附加输出：``camera_state()`` 额外返回 ``mode`` / ``focus_shift`` /
``motion_blur`` / ``motion_blur_px`` / ``shear`` / ``orbit_*`` /
``dolly_zoom_amt`` / ``parallax_eff``；``rack_focus`` 在图层合成阶段按 depth 做前后景
高斯虚化混合，``whip_pan`` 在特效之后对整帧做水平运动模糊。

本模块失败统一 raise ``V5Error``（若 ``scene_render`` 已定义则复用同一个类）。
"""
from __future__ import annotations

import json
import logging
import math
import random
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

from . import config, utils

try:  # pragma: no cover - 失败时降级
    import cv2  # type: ignore
except Exception:  # noqa: BLE001
    cv2 = None  # type: ignore[assignment]

# 若 scene_render 已在当前进程导入过，则复用它的 V5Error（异常可互通）；
# 不主动 import：scene_render 依赖较重，且未落盘时不应阻塞本模块。
_SharedV5Error = None
_scene_mod = sys.modules.get("pipeline.scene_render")
if _scene_mod is not None:
    _SharedV5Error = getattr(_scene_mod, "V5Error", None)

if _SharedV5Error is not None:  # pragma: no cover
    V5Error = _SharedV5Error  # type: ignore[misc]
else:

    class V5Error(RuntimeError):
        """motion2d 渲染错误（与 scene_render.V5Error 同名同义）。"""


LOG = utils.LOG if hasattr(utils, "LOG") else logging.getLogger("pipeline")


# --------------------------------------------------------------------------- #
# 常量
# --------------------------------------------------------------------------- #
LAYER_KEYS: tuple[str, ...] = ("background", "midground", "foreground")

#: scene_render 未给 depth 时的缺省视差系数（越小 = 越远 = 位移越少）
DEFAULT_DEPTH: dict[str, float] = {
    "background": 0.02,
    "midground": 0.12,
    "foreground": 0.30,
}

#: 视差指数缺省值（<1 会压低前景位移，避免 2D 剪纸感过猛）
DEFAULT_PARALLAX = 0.65

#: 支持的相机类型 + 常用别名（模板里可能写 slow_push / pan_r / dolly_in 等）
CAMERA_ALIASES: dict[str, str] = {
    "static": "static", "still": "static", "fixed": "static", "none": "static",
    "push_in": "push_in", "pushin": "push_in", "push": "push_in",
    "slow_push": "push_in", "zoom_in": "push_in", "in": "push_in",
    "pull_out": "pull_out", "pullout": "pull_out", "pull": "pull_out",
    "slow_pull": "pull_out", "zoom_out": "pull_out",
    "pan_left": "pan_left", "pan_l": "pan_left", "panleft": "pan_left",
    "pan_right": "pan_right", "pan_r": "pan_right", "panright": "pan_right",
    "pan": "pan_right",
    "dolly": "dolly", "dolly_in": "dolly",
    "handheld": "handheld", "hand_held": "handheld", "shake": "handheld",
    "handheld_shake": "handheld",
    # ---- V5.4 电影级镜头 ----
    "truck_left": "truck_left", "truck_l": "truck_left", "truckleft": "truck_left",
    "truck_right": "truck_right", "truck_r": "truck_right",
    "truckright": "truck_right", "truck": "truck_right",
    "tilt_up": "tilt_up", "tilt_u": "tilt_up", "tiltup": "tilt_up",
    "tilt_down": "tilt_down", "tilt_d": "tilt_down", "tiltdown": "tilt_down",
    "tilt": "tilt_up",
    "crane_up": "crane_up", "crane_u": "crane_up", "craneup": "crane_up",
    "crane_down": "crane_down", "crane_d": "crane_down", "cranedown": "crane_down",
    "crane": "crane_up", "bow_up": "crane_up",
    "dolly_zoom": "dolly_zoom", "dollyzoom": "dolly_zoom",
    "vertigo": "dolly_zoom", "hitchcock": "dolly_zoom", "zolly": "dolly_zoom",
    "rack_focus": "rack_focus", "rackfocus": "rack_focus", "rack": "rack_focus",
    "focus_pull": "rack_focus", "focus": "rack_focus", "pull_focus": "rack_focus",
    "orbit": "orbit", "turntable": "orbit", "arc": "orbit", "orbit_2_5d": "orbit",
    "whip_pan": "whip_pan", "whip": "whip_pan", "whippan": "whip_pan",
    "whip_pan_right": "whip_pan", "whip_r": "whip_pan",
}
SUPPORTED_CAMERA_TYPES: tuple[str, ...] = (
    # 基础 7 种
    "static", "push_in", "pull_out", "pan_left", "pan_right", "dolly", "handheld",
    # V5.4 电影级 10 种
    "truck_left", "truck_right", "tilt_up", "tilt_down",
    "crane_up", "crane_down", "dolly_zoom", "rack_focus", "orbit", "whip_pan",
)
#: 电影级镜头（V5.4）子集，便于文档/CLI/editorial 引用
CINEMATIC_CAMERA_TYPES: tuple[str, ...] = SUPPORTED_CAMERA_TYPES[7:]

SUPPORTED_FX: tuple[str, ...] = (
    "rain", "snow", "dust", "embers", "fog", "glow", "speed_lines", "vignette",
)

#: 预扩边（overscan）单边上限（像素）。超出则夹住相机位移，避免内存/画质恶化。
#: 1280x720 下 480px 单边 ≈ 最猛的前景摇移（strength~0.2）也不会露边。
MAX_OVERSCAN_PX = 480
#: 角色立绘相对内部分辨率的超采样（再由相机仿射缩回，保证推近时不糊）
CHAR_SPRITE_SUPERSAMPLE = 1.12
#: 角色默认高度（占画面高度比例）与宽高比
CHAR_DEFAULT_HEIGHT = 0.62
CHAR_DEFAULT_ASPECT = 0.62
#: 口型过渡帧数（0 = 硬切；2 → 0.5/0.78/1.0 三帧斜坡）与峰值幅度
MOUTH_TRANSITION_FRAMES = 2
MOUTH_DEFAULT_STRENGTH = 1.0
#: 角色入场/离场（帧数、入场时从 anchor 下方多少像素升起）
CHAR_FADE_FRAMES = 8
CHAR_FADE_RISE_PX = 20.0
#: 角色落地软阴影缓存（按整数尺寸复用）
_CHAR_SHADOW_CACHE: dict[tuple[int, int], Image.Image] = {}
#: 内部缓存（compose_frame/render_still 单独调用时使用）
_PREP_CACHE: dict[int, tuple[Any, Any, str]] = {}
_PREP_CACHE_MAX = 8
_FX_CACHE: dict[tuple, Any] = {}
_FX_CACHE_MAX = 8


# --------------------------------------------------------------------------- #
# 小工具
# --------------------------------------------------------------------------- #
def _clamp(v: float, lo: float, hi: float) -> float:
    return lo if v < lo else hi if v > hi else v


def _lerp(a: float, b: float, p: float) -> float:
    return a + (b - a) * p


def _hex_rgb(value: Any, default: tuple[int, int, int]) -> tuple[int, int, int]:
    """``#rrggbb`` / ``(r,g,b)`` / ``[r,g,b]`` → (r,g,b) 三元组。"""
    if value is None:
        return default
    if isinstance(value, (tuple, list)) and len(value) >= 3:
        try:
            return tuple(int(_clamp(float(x), 0, 255)) for x in value[:3])  # type: ignore[return-value]
        except (TypeError, ValueError):
            return default
    text = str(value).strip().lstrip("#")
    if len(text) == 3:
        text = "".join(ch * 2 for ch in text)
    if len(text) == 8:  # #rrggbbaa
        text = text[:6]
    if len(text) != 6:
        return default
    try:
        return (int(text[0:2], 16), int(text[2:4], 16), int(text[4:6], 16))
    except ValueError:
        return default


def _rgba(color: Sequence[int], alpha: int = 255) -> tuple[int, int, int, int]:
    """RGB 三元组 → RGBA 四元组（占位角色/特效绘制用）。"""
    return (int(color[0]), int(color[1]), int(color[2]), int(alpha))


def _scale_rgb(color: Sequence[int], k: float) -> tuple[int, int, int]:
    return (int(_clamp(color[0] * k, 0, 255)), int(_clamp(color[1] * k, 0, 255)),
            int(_clamp(color[2] * k, 0, 255)))


def _mix_rgb(a: Sequence[int], b: Sequence[int], p: float) -> tuple[int, int, int]:
    p = _clamp(p, 0.0, 1.0)
    return (int(round(a[0] + (b[0] - a[0]) * p)),
            int(round(a[1] + (b[1] - a[1]) * p)),
            int(round(a[2] + (b[2] - a[2]) * p)))


def _to_image(value: Any, name: str = "layer") -> Image.Image:
    """PIL.Image / numpy 数组 → PIL.Image（RGB 或 RGBA）。"""
    if isinstance(value, Image.Image):
        return value
    if isinstance(value, np.ndarray):
        arr = np.ascontiguousarray(value)
        if arr.dtype != np.uint8:
            arr = np.clip(arr, 0, 255).astype(np.uint8)
        if arr.ndim == 2:
            return Image.fromarray(arr).convert("RGBA")
        if arr.shape[2] == 4:
            return Image.fromarray(arr)
        if arr.shape[2] == 3:
            return Image.fromarray(arr)
    raise V5Error(f"{name} 不是 PIL.Image / numpy 图像: {type(value)!r}")


def _char_dict(entry: dict) -> dict:
    """角色条目里的 ``char`` 可能是完整 spec dict，也可能只是字符 id 字符串。"""
    char = entry.get("char") if isinstance(entry, dict) else None
    if isinstance(char, str):
        return {"id": char}
    return char if isinstance(char, dict) else {}


def _char_key(char: Any) -> str:
    if isinstance(char, str):
        return char
    if isinstance(char, dict):
        return str(char.get("id") or char.get("label") or "char")
    return "char"


def _affine(z: float, tx: float, ty: float) -> np.ndarray:
    """前向仿射（cv2.warpAffine 默认约定）：src→dst 的缩放+平移。"""
    return np.array([[z, 0.0, tx], [0.0, z, ty]], dtype=np.float64)


def _invert_affine(m: np.ndarray) -> tuple[float, float, float, float, float, float]:
    """2x3 前向仿射 → PIL ``transform`` 需要的逆向 6 元组。"""
    a, b, c = float(m[0, 0]), float(m[0, 1]), float(m[0, 2])
    d, e, f = float(m[1, 0]), float(m[1, 1]), float(m[1, 2])
    det = a * e - b * d
    if abs(det) < 1e-12:
        raise V5Error("仿射矩阵不可逆")
    ia, ib, ie2 = e / det, -b / det, a / det
    id_ = -d / det
    ic = -(ia * c + ib * f)
    if_ = -(id_ * c + ie2 * f)
    return (ia, ib, ic, id_, ie2, if_)


def _dest_bbox_rect(x0: float, y0: float, x1: float, y1: float, m: np.ndarray,
                    out_size: tuple[int, int], pad: int = 2) -> tuple[int, int, int, int]:
    """源矩形经前向仿射后，在输出画布上的整数包围盒。"""
    w, h = out_size
    corners = np.array([[x0, y0, 1.0], [x1, y0, 1.0], [x0, y1, 1.0], [x1, y1, 1.0]])
    pts = corners @ m.T
    bx0 = max(0, int(math.floor(pts[:, 0].min())) - pad)
    bx1 = min(w, int(math.ceil(pts[:, 0].max())) + pad)
    by0 = max(0, int(math.floor(pts[:, 1].min())) - pad)
    by1 = min(h, int(math.ceil(pts[:, 1].max())) + pad)
    return bx0, by0, bx1, by1


# --------------------------------------------------------------------------- #
# 缓动
# --------------------------------------------------------------------------- #
def ease(p: Any, name: Any = "in_out") -> float:
    """缓动：``name`` 为字符串或 ``callable(p)->p``；结果 clamp 到 [0,1]。"""
    x = _clamp(float(p), 0.0, 1.0)
    if callable(name):
        try:
            return _clamp(float(name(x)), 0.0, 1.0)
        except Exception as exc:  # noqa: BLE001
            raise V5Error(f"camera.ease 自定义函数失败: {exc}") from exc
    key = str(name or "in_out").strip().lower()
    if key in ("linear", "none"):
        return x
    if key in ("in", "in_quad"):
        return x * x
    if key in ("out", "out_quad"):
        return 1.0 - (1.0 - x) ** 2
    if key in ("in_out", "smooth", "smoothstep", "in_out_quad"):
        return x * x * (3.0 - 2.0 * x)
    if key == "smootherstep":
        return x * x * x * (x * (x * 6.0 - 15.0) + 10.0)
    # ---- sine（V5.4）----
    if key in ("sine", "in_out_sine"):
        return 0.5 - 0.5 * math.cos(math.pi * x)
    if key == "in_sine":
        return 1.0 - math.cos((x * math.pi) / 2.0)
    if key == "out_sine":
        return math.sin((x * math.pi) / 2.0)
    if key == "in_cubic":
        return x ** 3
    if key == "out_cubic":
        return 1.0 - (1.0 - x) ** 3
    if key == "in_out_cubic":
        return 4.0 * x ** 3 if x < 0.5 else 1.0 - ((-2.0 * x + 2.0) ** 3) / 2.0
    # ---- quart（V5.4，编辑/剪辑常用）----
    if key == "in_quart":
        return x ** 4
    if key == "out_quart":
        return 1.0 - (1.0 - x) ** 4
    if key == "in_out_quart":
        return 8.0 * x ** 4 if x < 0.5 else 1.0 - ((-2.0 * x + 2.0) ** 4) / 2.0
    if key == "in_quint":
        return x ** 5
    if key == "out_quint":
        return 1.0 - (1.0 - x) ** 5
    if key == "in_out_quint":
        return 16.0 * x ** 5 if x < 0.5 else 1.0 - ((-2.0 * x + 2.0) ** 5) / 2.0
    # ---- expo ----
    if key in ("expo", "in_expo"):
        return 0.0 if x <= 0 else 2.0 ** (10.0 * x - 10.0)
    if key == "out_expo":
        return 1.0 if x >= 1 else 1.0 - 2.0 ** (-10.0 * x)
    if key == "in_out_expo":
        if x <= 0:
            return 0.0
        if x >= 1:
            return 1.0
        return (2.0 ** (20.0 * x - 10.0) / 2.0 if x < 0.5
                else (2.0 - 2.0 ** (-20.0 * x + 10.0)) / 2.0)
    # ---- back（带轻微过冲，V5.4：out_back 为默认命名）----
    if key in ("back_out", "out_back"):
        c1 = 1.70158
        return 1.0 + (c1 + 1.0) * (x - 1.0) ** 3 + c1 * (x - 1.0) ** 2
    if key == "in_back":
        c1 = 1.70158
        return (c1 + 1.0) * x ** 3 - c1 * x ** 2
    if key == "in_out_back":
        c2 = 1.70158 * 1.525
        if x < 0.5:
            return ((2.0 * x) ** 2 * ((c2 + 1.0) * 2.0 * x - c2)) / 2.0
        return ((2.0 * x - 2.0) ** 2 * ((c2 + 1.0) * (x * 2.0 - 2.0) + c2) + 2.0) / 2.0
    # ---- circ ----
    if key == "in_circ":
        return 1.0 - math.sqrt(max(0.0, 1.0 - x * x))
    if key == "out_circ":
        return math.sqrt(max(0.0, 1.0 - (x - 1.0) ** 2))
    if key == "in_out_circ":
        if x < 0.5:
            return (1.0 - math.sqrt(max(0.0, 1.0 - (2.0 * x) ** 2))) / 2.0
        return (math.sqrt(max(0.0, 1.0 - (-2.0 * x + 2.0) ** 2)) + 1.0) / 2.0
    LOG.warning("motion2d: 未知 ease=%r，回退 in_out", name)
    return x * x * (3.0 - 2.0 * x)


# --------------------------------------------------------------------------- #
# 相机
# --------------------------------------------------------------------------- #
def _normalize_camera_type(value: Any) -> str:
    key = str(value or "static").strip().lower().replace("-", "_").replace(" ", "_")
    if key in CAMERA_ALIASES:
        return CAMERA_ALIASES[key]
    raise V5Error(f"未知 camera.type={value!r}；支持 {', '.join(SUPPORTED_CAMERA_TYPES)}")


def _smooth_noise(t: float, seed: int, freq: float = 0.55, octaves: int = 3) -> float:
    """确定性平滑噪声 ∈ [-1,1]（正弦叠加，手持抖动用）。"""
    rng = random.Random(int(seed) * 7919 + 13)
    phases = [rng.uniform(0.0, 2.0 * math.pi) for _ in range(octaves)]
    amps = [1.0, 0.5, 0.22][:octaves]
    total = sum(amps)
    acc = 0.0
    for i, (ph, amp) in enumerate(zip(phases, amps)):
        f = freq * (2.0 ** i) * (1.0 + 0.13 * i)
        acc += amp * math.sin(2.0 * math.pi * f * t + ph)
    return acc / total


def _zone_rect(zone: Any, zones: dict | None = None) -> list[float] | None:
    """归一化取景窗 [x, y, w, h]（或 camera_zones 里的名字）→ 校验后的矩形。"""
    if zone is None:
        return None
    if isinstance(zone, str):
        if zones and zone in zones:
            zone = zones[zone]
        else:
            return None
    try:
        vals = [float(v) for v in list(zone)[:4]]
    except (TypeError, ValueError):
        return None
    while len(vals) < 4:
        vals.append(0.0)
    x, y, zw, zh = vals
    zw = _clamp(zw, 1e-3, 1.0)
    zh = _clamp(zh, 1e-3, 1.0)
    x = _clamp(x, 0.0, 1.0 - zw)
    y = _clamp(y, 0.0, 1.0 - zh)
    return [x, y, zw, zh]


def _zone_framing(zone: Sequence[float], size: tuple[int, int]) -> tuple[float, float, float]:
    """取景窗 [x,y,w,h]（归一化）→ (zoom, dx, dy)。

    zoom 取 ``min(1/w, 1/h)``：**恰好装下整个视图窗**（若窗口 aspect 与画面不同，
    会多露一点边）；dx/dy 把视图窗中心**精确**移到画面中心（中景参考平面）：
    ``dx = -(cx - W/2) * zoom``，代入相机公式后 zone 中心恰落在画面中心。
    """
    w, h = size
    x, y, zw, zh = _zone_rect(zone) or [0.0, 0.0, 1.0, 1.0]
    zoom = max(1.0, min(1.0 / zw, 1.0 / zh))
    cx = (x + zw / 2.0) * w
    cy = (y + zh / 2.0) * h
    return zoom, -(cx - w / 2.0) * zoom, -(cy - h / 2.0) * zoom


# 兼容旧名（内部/测试可能引用）
_zone_to_camera = _zone_framing


def _resolve_zone_refs(camera: Any, zones: dict | None) -> Any:
    """把 camera 里的取景窗**名字**（如 "detail"）解析成归一化矩形。

    支持 ``zone``（基础取景窗，type 的运动叠加在其上）、``zone_from`` / ``zone_to``
    （两个取景窗之间过渡，优先于 type 的默认曲线）。
    """
    if not isinstance(camera, dict) or not zones:
        return camera
    out = dict(camera)
    for key in ("zone", "zone_from", "zone_to"):
        v = out.get(key)
        if isinstance(v, str):
            rect = _zone_rect(v, zones)
            if rect is None:
                LOG.warning("motion2d: camera.%s=%r 不在 layers.camera_zones（%s）中，已忽略",
                            key, v, ", ".join(map(str, zones)))
                out.pop(key, None)
            else:
                out[key] = rect
    return out


def camera_state(camera: dict | None, t: float, duration: float,
                 size: tuple[int, int] = (1280, 720), seed: int = 0) -> dict:
    """解析某一时刻的相机状态。

    返回（旧键始终存在，新键为 V5.4 追加、默认中性值）::

        {"zoom", "x", "y",                  # 中景参考平面：缩放 + 屏幕位移(px)
         "shake_x", "shake_y", "type", "progress", "strength", "ease",
         "shake", "parallax", "zone",
         # V5.4 电影级镜头附加键（旧调用可忽略）
         "mode",            # "normal" | "dolly_zoom" | "orbit"
         "parallax_eff",    # 本镜实际使用的视差指数（truck 会略加强）
         "focus_shift",     # rack_focus 的焦点进度 0..1（0=焦点在远，1=焦点在近）
         "focus_blur_px",   # rack_focus 的最大模糊半径（px，1280x720 基准）
         "motion_blur",     # whip_pan 的水平运动模糊强度 0..1
         "motion_blur_px",  # motion_blur 折算的水平模糊像素
         "shear",           # orbit 的轻微切变（affine 第二列系数）
         "orbit_dx", "orbit_dy", "orbit_dz",   # orbit 每单位视差比的正弦位移/缩放
         "dolly_zoom_amt"   # dolly_zoom 的变焦量（按 |ratio-1| 施加到各层）
        }

    ``x``/``y`` 是**中景参考平面**在屏幕上的位移（像素），各图层按
    ``(depth/depth_ref)**parallax`` 缩放（见 ``_plane_motion``）。
    """
    cam = dict(camera or {})
    width, height = size
    duration = max(1e-6, float(duration or 0.0))
    p = _clamp(float(t) / duration, 0.0, 1.0)
    ctype = _normalize_camera_type(cam.get("type", "static"))
    strength = float(cam.get("strength", 0.08) or 0.0)
    ease_name = cam.get("ease", "in_out")
    e = ease(p, ease_name)

    # V5.4 附加状态（默认中性）
    mode = "normal"
    parallax_eff = float(cam.get("parallax", DEFAULT_PARALLAX) or DEFAULT_PARALLAX)
    focus_shift = 0.0
    focus_blur_px = 0.0
    motion_blur = 0.0
    motion_blur_px = 0.0
    shear = 0.0
    orbit_dx = orbit_dy = orbit_dz = 0.0
    dolly_zoom_amt = 0.0

    # ① 基础取景（zone）：zone_from/zone_to 直接插值（优先于 type 曲线）；
    #    zone 则作为"底"取景窗，type 的运动在它之上叠加（如 push_in 在特写里推）。
    zone_rect = _zone_rect(cam.get("zone"))
    zfrom = _zone_rect(cam.get("zone_from"))
    zto = _zone_rect(cam.get("zone_to"))
    base_zoom, base_dx, base_dy = 1.0, 0.0, 0.0
    if zfrom is not None or zto is not None:
        z0, x0, y0 = _zone_framing(zfrom or [0.0, 0.0, 1.0, 1.0], size)
        z1, x1, y1 = _zone_framing(zto or [0.0, 0.0, 1.0, 1.0], size)
        zoom = _lerp(z0, z1, e)
        dx = _lerp(x0, x1, e)
        dy = _lerp(y0, y1, e)
    else:
        if zone_rect is not None:
            base_zoom, base_dx, base_dy = _zone_framing(zone_rect, size)
        zoom, dx, dy = base_zoom, base_dx, base_dy
        if ctype == "push_in":
            zoom = base_zoom * (1.0 + strength * e)          # 在 zone 里推近
        elif ctype == "pull_out":
            zoom = base_zoom * (1.0 + strength * (1.0 - e))  # 在 zone 里拉远
        elif ctype == "pan_left":
            dx = base_dx + strength * width * e              # 相机左移 → 内容右移
        elif ctype == "pan_right":
            dx = base_dx - strength * width * e              # 相机右移 → 内容左移
        elif ctype == "dolly":
            zoom = base_zoom * (1.0 + strength * e)
            dy = base_dy - 0.35 * strength * height * e
            dx = base_dx + 0.12 * strength * width * e
        elif ctype == "handheld":
            zoom = base_zoom * (1.0 + 0.5 * strength * e)
            dx = base_dx + 0.16 * strength * width * e
            dy = base_dy + 0.05 * strength * height * math.sin(2.0 * math.pi * 0.21 * t)
        # ---------------- V5.4 电影级镜头 ----------------
        elif ctype == "truck_left":
            # 横移：恒定 zoom，dx 线性移动 + 更明显的视差（近景跑得更快）
            dx = base_dx + strength * width * e
            parallax_eff *= 1.25
        elif ctype == "truck_right":
            dx = base_dx - strength * width * e
            parallax_eff *= 1.25
        elif ctype == "tilt_up":
            # 纵摇：相机上仰 → 画面内容下移
            dy = base_dy + strength * height * e
            parallax_eff *= 1.15
        elif ctype == "tilt_down":
            dy = base_dy - strength * height * e
            parallax_eff *= 1.15
        elif ctype == "crane_up":
            # 升降：从略低的机位升起（zoom 由低到高）+ 内容下移
            dy = base_dy + strength * height * e
            zoom = base_zoom * (1.0 - 0.25 * strength + 0.40 * strength * e)
            parallax_eff *= 1.20
        elif ctype == "crane_down":
            dy = base_dy - strength * height * e
            zoom = base_zoom * (1.0 + 0.25 * strength - 0.40 * strength * e)
            parallax_eff *= 1.20
        elif ctype == "dolly_zoom":
            # 希区柯克变焦：主体平面锁住，远近层按 |ratio-1| 反向膨胀（见 _plane_motion）
            mode = "dolly_zoom"
            dolly_zoom_amt = strength * e
            zoom = base_zoom
        elif ctype == "rack_focus":
            # 焦点由远到近拉（不动镜头），渲染层按 depth 做模糊渐变
            focus_shift = _clamp(e, 0.0, 1.0)
            zoom = base_zoom
            focus_blur_px = float(cam.get("blur", 3.2) or 3.2) * (height / 720.0)
        elif ctype == "orbit":
            # 2.5D 环绕：主体（中景）锁住，远近层反向正弦位移 + 微缩放 + 轻微切变
            theta = math.pi * _clamp(strength, 0.0, 1.0) * e
            s = math.sin(theta)
            mode = "orbit"
            orbit_dx = -width * 0.20 * s          # 每单位 (ratio-1) 的横向弧线
            orbit_dy = height * 0.014 * s
            orbit_dz = 0.045 * s
            shear = 0.020 * s
            zoom = base_zoom
        elif ctype == "whip_pan":
            # 甩镜：前段快速位移 + 水平运动模糊（motion_blur 峰值在前段）
            e_fast = 1.0 - (1.0 - e) ** 2.5
            dx = base_dx - strength * width * e_fast
            motion_blur = _clamp((1.0 - e) ** 1.6, 0.0, 1.0)
            motion_blur_px = motion_blur * _clamp(
                0.05 * width * (float(cam.get("strength", 0.2) or 0.2) / 0.2),
                0.0, float(cam.get("blur_max", 48.0) or 48.0))

    shake = float(cam.get("shake", 0.0) or 0.0)
    if ctype == "handheld" and shake <= 0.0:
        shake = 0.008
    sx = sy = 0.0
    if shake > 0.0:
        amp = shake * min(width, height)
        freq = float(cam.get("shake_freq", 0.9) or 0.9)
        sx = amp * _smooth_noise(t, seed + 11, freq)
        sy = amp * 0.72 * _smooth_noise(t, seed + 29, freq * 1.13)

    return {
        "zoom": float(zoom),
        "x": float(dx + sx),
        "y": float(dy + sy),
        "shake_x": float(sx),
        "shake_y": float(sy),
        "type": ctype,
        "progress": float(p),
        "strength": float(strength),
        "ease": ease_name if isinstance(ease_name, str) else "custom",
        "shake": float(shake),
        "parallax": float(cam.get("parallax", DEFAULT_PARALLAX) or DEFAULT_PARALLAX),
        "zone": zone_rect,
        # ---- V5.4 电影级镜头附加键 ----
        "mode": mode,
        "parallax_eff": float(parallax_eff),
        "focus_shift": float(focus_shift),
        "focus_blur_px": float(focus_blur_px),
        "motion_blur": float(motion_blur),
        "motion_blur_px": float(motion_blur_px),
        "shear": float(shear),
        "orbit_dx": float(orbit_dx),
        "orbit_dy": float(orbit_dy),
        "orbit_dz": float(orbit_dz),
        "dolly_zoom_amt": float(dolly_zoom_amt),
    }


def _parallax_ratio(depth: float, depth_ref: float, gain: float) -> float:
    """图层位移系数：``(depth / depth_ref) ** gain``，限制在 [0.05, 3]。"""
    if depth_ref <= 1e-6:
        return 1.0
    ratio = depth / depth_ref
    if ratio <= 0:
        return 0.0
    return _clamp(ratio ** max(0.05, float(gain)), 0.03, 3.0)


def _plane_motion(cam: dict, ratio: float) -> tuple[float, float, float, float]:
    """中景平面运动 → 某视差比层的 (zoom, dx, dy, shear)。

    V5.4 特殊模式：
    * ``dolly_zoom``：中景（ratio=1）锁死，远近层按 ``|ratio-1|`` 膨胀；
    * ``orbit``：中景锁死，远近层按 ``(ratio-1)`` 反向正弦位移 + 微缩放。
    """
    mode = str(cam.get("mode") or "normal")
    if mode == "dolly_zoom":
        amt = float(cam.get("dolly_zoom_amt", 0.0) or 0.0)
        zoom = cam["zoom"] * (1.0 + amt * abs(ratio - 1.0))
        return zoom, cam["x"] * ratio, cam["y"] * ratio, 0.0
    if mode == "orbit":
        r1 = ratio - 1.0
        dx = float(cam.get("orbit_dx", 0.0) or 0.0) * r1
        dy = float(cam.get("orbit_dy", 0.0) or 0.0) * r1
        zoom = 1.0 + float(cam.get("orbit_dz", 0.0) or 0.0) * r1
        return zoom, dx, dy, float(cam.get("shear", 0.0) or 0.0)
    dx = cam["x"] * ratio
    dy = cam["y"] * ratio
    zf = 0.55 + 0.45 * _clamp(ratio, 0.15, 2.2)  # 越近的层推拉越明显
    zoom = 1.0 + (cam["zoom"] - 1.0) * zf
    return zoom, dx, dy, float(cam.get("shear", 0.0) or 0.0)


def _layer_motion(layer: "_PreparedLayer", cam: dict, depth_ref: float,
                  gain: float) -> tuple[float, float, float, float]:
    """某层 (zoom, dx, dy, shear)：位移按视差比例，缩放带轻微景深差。"""
    ratio = _parallax_ratio(layer.depth, depth_ref, gain)
    return _plane_motion(cam, ratio)


# --------------------------------------------------------------------------- #
# 图层准备（overscan + numpy）
# --------------------------------------------------------------------------- #
@dataclass
class _PreparedLayer:
    key: str
    depth: float
    rgb: np.ndarray                        # (Hc, Wc, 3) uint8（已扩边）
    alpha: np.ndarray | None               # (Hc, Wc) uint8；None = 完全不透明
    opaque: bool
    margin: tuple[int, int]                # (mx, my) 单边扩边像素
    content_box: tuple[int, int, int, int]  # alpha>0 内容框（扩边坐标系）
    blur_px: float = 0.0                   # 前景景深虚化半径（0 = 未虚化）
    rgb_focus: np.ndarray | None = None    # rack_focus 用的额外模糊版本（RGB）
    alpha_focus: np.ndarray | None = None  # rack_focus 用的额外模糊版本（A）


@dataclass
class _LayerSet:
    size: tuple[int, int]
    layers: list[_PreparedLayer]
    depth: dict[str, float]
    depth_ref: float
    anchors: dict[str, tuple[float, float]] = field(default_factory=dict)
    camera_zones: dict[str, list[float]] = field(default_factory=dict)
    meta: dict[str, Any] = field(default_factory=dict)
    bg_color: tuple[int, int, int] = (0, 0, 0)
    camera_key: str = ""
    style_key: str = ""

    @property
    def keys(self) -> list[str]:
        return [lyr.key for lyr in self.layers]


def _foreground_blur_px(style: dict | None, opaque: bool) -> float:
    """前景景深虚化半径（px）。默认对带 alpha 的前景层做 1.2px 软焦。

    优先级：``style["foreground_blur"]`` > ``style["dof"]["foreground_blur"]`` >
    ``style["dof"]``（bool/数字）> 默认（不透明前景 0，带 alpha 前景 1.2）。
    """
    st = style or {}
    val = st.get("foreground_blur")
    if val is None:
        dof = st.get("dof")
        if isinstance(dof, dict):
            val = dof.get("foreground_blur", dof.get("blur"))
        elif dof is not None:
            val = 1.2 if dof else 0.0
    if val is None:
        val = 0.0 if opaque else 1.2
    try:
        return _clamp(float(val), 0.0, 8.0)
    except (TypeError, ValueError):
        return 0.0


def _overscan_needed(cam_samples: Iterable[dict], depth: float, depth_ref: float,
                     gain: float, size: tuple[int, int]) -> tuple[int, int]:
    """按整镜相机轨迹算某层需要的单边扩边像素（含 orbit/shear/dolly_zoom）。"""
    width, height = size
    ratio = _parallax_ratio(depth, depth_ref, gain)
    need_x = need_y = 0.0
    for cam in cam_samples:
        z, dx, dy, shear = _plane_motion(cam, ratio)
        z = max(1e-4, z)
        lo_x = (0.0 - width / 2.0 - dx) / z + width / 2.0
        hi_x = (width - width / 2.0 - dx) / z + width / 2.0
        lo_y = (0.0 - height / 2.0 - dy) / z + height / 2.0
        hi_y = (height - height / 2.0 - dy) / z + height / 2.0
        need_x = max(need_x, -lo_x, hi_x - width)
        need_y = max(need_y, -lo_y, hi_y - height)
        if shear:  # 切变把 x 按 y 偏移，最多 |shear| * H
            need_x += abs(shear) * height
    mx = int(min(max(0.0, need_x) + 3, MAX_OVERSCAN_PX))
    my = int(min(max(0.0, need_y) + 3, MAX_OVERSCAN_PX))
    return int(math.ceil(mx)), int(math.ceil(my))


def _pad_layer(rgba: Image.Image, mx: int, my: int
               ) -> tuple[np.ndarray, np.ndarray | None, bool, tuple[int, int, int, int]]:
    """RGBA → (rgb, alpha|None, opaque, content_box)；边缘复制扩边。"""
    arr = np.asarray(rgba.convert("RGBA"), dtype=np.uint8)
    h, w = arr.shape[:2]
    rgb = np.ascontiguousarray(arr[:, :, :3])
    alpha = np.ascontiguousarray(arr[:, :, 3])
    opaque = bool((alpha == 255).all())
    if mx or my:
        rgb = np.ascontiguousarray(np.pad(rgb, ((my, my), (mx, mx), (0, 0)), mode="edge"))
        if not opaque:
            alpha = np.ascontiguousarray(np.pad(alpha, ((my, my), (mx, mx)), mode="edge"))
    if opaque:
        return rgb, None, True, (0, 0, w + 2 * mx, h + 2 * my)
    ys, xs = np.nonzero(alpha)
    if ys.size == 0:
        box = (mx, my, mx + 1, my + 1)
    else:
        box = (int(xs.min()) + mx, int(ys.min()) + my,
               int(xs.max()) + 1 + mx, int(ys.max()) + 1 + my)
    return rgb, alpha, False, box


def prepare_layers(layers: dict, size: tuple[int, int], camera: dict | None = None,
                   duration: float = 3.0, seed: int = 0,
                   style: dict | None = None) -> _LayerSet:
    """把 ``scene_render.render_scene_layers`` 的返回值转成逐帧合成的内部结构。

    * 统一 RGBA、尺寸对齐到 ``size``；
    * 前景层按 ``style`` 做 1-2px 景深虚化（``_foreground_blur_px``，可关）；
    * 依据整镜相机轨迹预扩边（避免摇移/推拉露黑边）；
    * 不透明层丢弃 alpha 通道（合成更快）。

    不是契约函数（``render_shot`` 内部也会调用），但便于单独调试静态帧。
    """
    if not isinstance(layers, dict):
        raise V5Error(f"layers 必须是 dict，收到 {type(layers)!r}")
    width, height = int(size[0]), int(size[1])
    if width <= 0 or height <= 0:
        raise V5Error(f"非法 size={size!r}")

    depth_in = layers.get("depth") or {}
    depth: dict[str, float] = {}
    for key in LAYER_KEYS:
        try:
            depth[key] = float(depth_in.get(key, DEFAULT_DEPTH[key]))
        except (TypeError, ValueError):
            depth[key] = DEFAULT_DEPTH[key]
    depth_ref = float(depth.get("midground") or DEFAULT_DEPTH["midground"])
    if abs(depth_ref) <= 1e-6:
        depth_ref = DEFAULT_DEPTH["midground"]

    cam = dict(camera or {"type": "static"})
    cam.pop("duration", None)
    cam = _resolve_zone_refs(cam, layers.get("camera_zones") or {})
    gain = float(cam.get("parallax", DEFAULT_PARALLAX) or DEFAULT_PARALLAX)
    samples = [camera_state(cam, duration * (i / 24.0), max(1e-6, duration),
                            (width, height), seed) for i in range(25)]
    ctype = _normalize_camera_type(cam.get("type", "static"))
    # rack_focus：预先烘出「额外高斯模糊」版本（逐帧只做混合，无运行时模糊开销）
    focus_on = ctype == "rack_focus"
    focus_blur = float(cam.get("blur", 3.2) or 3.2) * (height / 720.0)
    focus_blur = _clamp(focus_blur, 0.5, 16.0)

    prepared: list[_PreparedLayer] = []
    for key in LAYER_KEYS:
        raw = layers.get(key)
        if raw is None:
            continue
        img = _to_image(raw, key)
        if img.size != (width, height):
            LOG.debug("motion2d: %s 尺寸 %s → %s（LANCZOS 缩放）",
                      key, img.size, (width, height))
            img = img.convert("RGBA").resize((width, height), Image.LANCZOS)
        blur_px = 0.0
        if key == "foreground":
            arr0 = np.asarray(img.convert("RGBA"), dtype=np.uint8)
            opaque0 = bool((arr0[:, :, 3] == 255).all())
            blur_px = _foreground_blur_px(style, opaque0)
            if blur_px > 0:
                img = img.filter(ImageFilter.GaussianBlur(float(blur_px)))
        mx, my = _overscan_needed(samples, depth[key], depth_ref, gain, (width, height))
        rgb, alpha, opaque, box = _pad_layer(img, mx, my)
        rgb_focus = alpha_focus = None
        if focus_on:
            blurred = img.filter(ImageFilter.GaussianBlur(float(focus_blur)))
            rgb_focus, alpha_focus, _, _ = _pad_layer(blurred, mx, my)
            if alpha_focus is None and alpha is not None:  # 理论不会发生
                alpha_focus = np.ascontiguousarray(
                    np.pad(np.asarray(blurred.getchannel("A")), ((my, my), (mx, mx)),
                           mode="edge"))
        prepared.append(_PreparedLayer(
            key=key, depth=depth[key], rgb=rgb, alpha=alpha, opaque=opaque,
            margin=(mx, my), content_box=box, blur_px=blur_px,
            rgb_focus=rgb_focus, alpha_focus=alpha_focus))
    if not prepared:
        raise V5Error("layers 中没有任何可用图层（background/midground/foreground）")

    anchors: dict[str, tuple[float, float]] = {}
    for role, xy in (layers.get("anchors") or {}).items():
        try:
            anchors[str(role)] = (float(xy[0]), float(xy[1]))
        except (TypeError, ValueError, IndexError):
            continue

    meta = dict(layers.get("meta") or {})
    return _LayerSet(
        size=(width, height),
        layers=prepared,
        depth=depth,
        depth_ref=depth_ref,
        anchors=anchors,
        camera_zones=dict(layers.get("camera_zones") or {}),
        meta=meta,
        bg_color=_hex_rgb(meta.get("bg_color"), (0, 0, 0)),
        camera_key=json.dumps({k: v for k, v in cam.items() if not callable(v)},
                              sort_keys=True, default=str),
        style_key=_style_sig(style),
    )


def _style_sig(style: dict | None) -> str:
    """风格缓存签名（只取影响图层/立绘的字段，避免整份 style 太大）。"""
    if not isinstance(style, dict):
        return ""
    keys = ("id", "technique", "foreground_blur", "dof", "palette", "line",
            "character_treatment", "texture", "grade", "character_fade",
            "character_shadow", "vignette")
    return json.dumps({k: style.get(k) for k in keys if k in style},
                      sort_keys=True, default=str)


def _get_layerset(layers: Any, size: tuple[int, int], camera: Any = None,
                  duration: float = 3.0, seed: int = 0,
                  style: dict | None = None) -> _LayerSet:
    """复用已准备的 LayerSet；否则（id 缓存）准备一次。"""
    if isinstance(layers, _LayerSet):
        return layers
    key = id(layers)
    cam_for_prep = dict(camera) if isinstance(camera, dict) else None
    if cam_for_prep is not None:
        # 单帧相机状态里的瞬时量不参与缓存键（否则逐帧 miss）
        for volatile in ("zoom", "x", "y", "progress", "shake_x", "shake_y",
                         "focus_shift", "motion_blur", "motion_blur_px",
                         "orbit_dx", "orbit_dy", "orbit_dz", "dolly_zoom_amt",
                         "shear", "parallax_eff", "focus_blur_px"):
            cam_for_prep.pop(volatile, None)
    camspec = json.dumps({k: v for k, v in (cam_for_prep or {}).items()
                          if not callable(v)}, sort_keys=True, default=str)
    style_key = _style_sig(style)
    hit = _PREP_CACHE.get(key)
    if hit is not None and hit[0] is layers and hit[2] == camspec \
            and hit[1].style_key == style_key \
            and hit[1].size == (int(size[0]), int(size[1])):
        return hit[1]
    prepared = prepare_layers(layers, size, cam_for_prep, duration, seed, style=style)
    if len(_PREP_CACHE) >= _PREP_CACHE_MAX:
        _PREP_CACHE.pop(next(iter(_PREP_CACHE)))
    _PREP_CACHE[key] = (layers, prepared, camspec)
    return prepared


# --------------------------------------------------------------------------- #
# 仿射采样
# --------------------------------------------------------------------------- #
def _warp_pil_array(arr: np.ndarray, m: np.ndarray, out_size: tuple[int, int]) -> np.ndarray:
    """PIL 回退路径（cv2 不可用时）：对 (H,W,C) uint8 做前向仿射。"""
    inv = _invert_affine(m)
    if arr.ndim == 2:
        out = Image.fromarray(arr).transform(out_size, Image.AFFINE, inv,
                                             resample=Image.BILINEAR)
        return np.asarray(out, dtype=np.uint8)
    out = Image.fromarray(np.ascontiguousarray(arr)).transform(
        out_size, Image.AFFINE, inv, resample=Image.BILINEAR)
    return np.asarray(out, dtype=np.uint8)


def _warp_rgb(rgb: np.ndarray, m: np.ndarray, out_size: tuple[int, int],
              replicate: bool) -> np.ndarray:
    if cv2 is not None:
        border = cv2.BORDER_REPLICATE if replicate else cv2.BORDER_CONSTANT
        return cv2.warpAffine(
            rgb, m.astype(np.float32), (int(out_size[0]), int(out_size[1])),
            flags=cv2.INTER_LINEAR, borderMode=border, borderValue=(0, 0, 0))
    return _warp_pil_array(rgb, m, out_size)


def _warp_alpha(alpha: np.ndarray, m: np.ndarray, out_size: tuple[int, int]) -> np.ndarray:
    if cv2 is not None:
        return cv2.warpAffine(
            alpha, m.astype(np.float32), (int(out_size[0]), int(out_size[1])),
            flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT, borderValue=0)
    return _warp_pil_array(alpha, m, out_size)


def _blend_into(dst: np.ndarray, other: np.ndarray, w: float) -> np.ndarray:
    """把 ``dst`` 与 ``other`` 按权重 w 混合（V5.4 rack_focus 层混合）。

    cv2 可用时**原地**写 ``dst``（~0.4ms/全帧），否则回退 uint16 定点混合。
    """
    w = _clamp(float(w), 0.0, 1.0)
    if w <= 0.002:
        return dst
    if w >= 0.998:
        return other
    if cv2 is not None and dst.shape == other.shape and dst.dtype == other.dtype:
        cv2.addWeighted(dst, 1.0 - w, other, w, 0.0, dst=dst)
        return dst
    wi = int(round(w * 256.0))
    return (((dst.astype(np.uint16) * (256 - wi))
             + (other.astype(np.uint16) * wi)) >> 8).astype(np.uint8)


def _blend_np(a: np.ndarray, b: np.ndarray, w: float) -> np.ndarray:
    """向后兼容别名（uint8 线性混合）。"""
    return _blend_into(np.array(a, copy=True), b, w)


def _motion_blur_inplace(frame: np.ndarray, px: float) -> None:
    """水平运动模糊（V5.4 whip_pan）：原地 box blur，成本 ~0.5-2.5ms。"""
    k = int(round(float(px)))
    if k < 2 or frame.ndim != 3:
        return
    k |= 1                      # 奇数核，保持左右对称
    k = int(min(k, 65))
    if cv2 is not None:
        frame[:] = cv2.blur(frame, (k, 1))
        return
    # 无 cv2 回退：滚动平均（whip 镜专用，帧数少）
    half = k // 2
    acc = frame.astype(np.uint16) * (2 * half + 1)   # pragma: no cover
    for i in range(1, half + 1):                     # pragma: no cover
        acc += np.roll(frame, i, axis=1)
        acc += np.roll(frame, -i, axis=1)
    frame[:] = (acc // (2 * half + 1)).astype(np.uint8)  # pragma: no cover


def _apply_motion_blur(frame: np.ndarray, cam: dict) -> None:
    """按相机状态对整帧施加水平运动模糊（原地）。"""
    px = float(cam.get("motion_blur_px", 0.0) or 0.0)
    if px >= 2.0:
        _motion_blur_inplace(frame, px)


# --------------------------------------------------------------------------- #
# 角色：口型 / 眨眼 / 呼吸
# --------------------------------------------------------------------------- #
MOUTH_STATES: tuple[str, ...] = ("closed", "a", "o", "e", "i", "u", "m")
_MOUTH_ALIASES = {
    "sil": "closed", "silence": "closed", "close": "closed", "c": "closed",
    "aa": "a", "ah": "a", "open": "a",
    "oh": "o", "oo": "o",
    "ee": "e", "eh": "e",
    "ih": "i", "ii": "i",
    "uh": "u", "uu": "u",
    "mm": "m", "mbp": "m", "p": "m", "b": "m",
}


def normalize_mouth(value: Any) -> str:
    key = str(value or "closed").strip().lower()
    key = _MOUTH_ALIASES.get(key, key)
    return key if key in MOUTH_STATES else "closed"


def mouth_at(timeline: Sequence[Any] | None, t: float, default: str = "closed") -> str:
    """从 ``[(t0, t1, viseme), ...]`` 取当前口型（半开区间 [t0,t1)）。"""
    if not timeline:
        return normalize_mouth(default)
    for item in timeline:
        try:
            if isinstance(item, dict):
                t0 = float(item.get("t0", 0.0))
                t1 = float(item.get("t1", 0.0))
                vis = item.get("viseme")
            else:
                t0, t1, vis = float(item[0]), float(item[1]), item[2]
        except (TypeError, ValueError, IndexError):
            continue
        if t0 <= t < t1:
            return normalize_mouth(vis)
    return normalize_mouth(default)


def mouth_plan_at(timeline: Sequence[Any] | None, t: float, fps: float = 25.0,
                  strength: float | None = None, default: str = "closed",
                  transition_frames: int = MOUTH_TRANSITION_FRAMES,
                  closed_gap: bool = True) -> list[tuple[str, float]]:
    """逐帧口型方案 ``[(viseme, weight), ...]``（weight 之和 = 1）。

    平滑规则（避免 viseme 逐帧硬跳，单帧幅度过大）：

    * 新 viseme 到达后按 ``transition_frames``（默认 2）做斜坡过渡
      （权重 0.5 → 0.78 → 1.0，共 2-3 帧），即与上一口型做立绘混合；
    * 相邻两个**张口** viseme 切换时，先插 **1 帧闭口**（``closed_gap``），
      避免一直张着嘴（O 型嘴长时间停住）；
    * ``strength``（0~1，render_v5 建议 0.85）压低张口峰值：最终权重 = 斜坡 × strength，
      权重 <1 时与闭口立绘混合 → 张口幅度更小；
    * 目标为 closed 时 1 帧内闭口，不拖尾。
    """
    fps = max(1e-6, float(fps or 25.0))
    frame = max(0, int(round(float(t) * fps)))
    cur = mouth_at(timeline, t, default)
    if cur == "closed":
        return [("closed", 1.0)]
    # 目标 viseme 已持续几帧（0 = 本帧刚切换）
    age = 0
    probe = frame - 1
    prev = "closed"
    while probe >= 0 and age < 8:
        prev = mouth_at(timeline, probe / fps, default)
        if prev == cur:
            age += 1
            probe -= 1
        else:
            break
    strength_v = _clamp(float(strength if strength is not None
                              else MOUTH_DEFAULT_STRENGTH), 0.0, 1.5)
    tf = max(0, int(transition_frames or 0))
    if tf and closed_gap and age == 0 and prev != "closed" and prev != cur:
        return [("closed", 1.0)]                     # 张口↔张口：先闭 1 帧
    if tf <= 0:
        weight = 1.0
    else:
        weight = (min(age, tf) + 1.0) / (tf + 1.0)
        weight = weight ** 0.8
    weight = _clamp(weight * strength_v, 0.0, 1.0)
    if weight >= 0.999:
        return [(cur, 1.0)]
    if weight <= 0.001:
        return [("closed", 1.0)]
    return [("closed", 1.0 - weight), (cur, weight)]


def blink_schedule(duration: float, seed: int = 0, role: str = "",
                   hold: float = 0.15,
                   gap: tuple[float, float] = (3.0, 6.0)) -> list[tuple[float, float]]:
    """确定性眨眼时间表：每 3~6s 一次，每次 0.15s。"""
    rng = random.Random(f"{seed}|{role}|blink")
    out: list[tuple[float, float]] = []
    t = rng.uniform(0.6, 2.2)
    duration = max(0.0, float(duration))
    while t < duration:
        out.append((t, t + hold))
        t += rng.uniform(gap[0], gap[1])
    return out


def is_blinking(schedule: Sequence[tuple[float, float]], t: float) -> bool:
    for t0, t1 in schedule:
        if t0 <= t < t1:
            return True
    return False


def _char_phase(seed: int, role: str) -> tuple[float, float, float]:
    rng = random.Random(f"{seed}|{role}|anim")
    return (rng.uniform(0, 2 * math.pi), rng.uniform(0, 2 * math.pi),
            rng.uniform(0, 2 * math.pi))


def char_micro_motion(t: float, phase: tuple[float, float, float],
                      speaking: bool = False) -> tuple[float, float]:
    """呼吸/点头微动（±1~2px）：返回 (dx, dy) 像素偏移。"""
    p1, p2, p3 = phase
    dx = 0.9 * math.sin(2.0 * math.pi * t / 6.7 + p2)
    dy = -1.5 * math.sin(2.0 * math.pi * t / 4.2 + p1)      # 呼吸：肩部起伏
    if speaking:                                            # 说话时轻微点头
        dy += 1.2 * math.sin(2.0 * math.pi * t / 2.6 + p3)
    return dx, dy


# --------------------------------------------------------------------------- #
# 角色占位绘制（characters_v5 不可用时的内置 fallback）
# --------------------------------------------------------------------------- #
def _draw_mouth(d: ImageDraw.ImageDraw, cx: float, cy: float, head_r: float,
                mouth: str, inner: tuple[int, int, int],
                line: tuple[int, int, int], lw: int) -> None:
    w = 0.17 * head_r        # 嘴宽 ~ 头宽 17%
    h = 0.11 * head_r
    lwm = max(1, lw - 1)
    if mouth == "closed":
        d.line([(cx - w * 0.75, cy), (cx + w * 0.75, cy)], fill=line, width=lwm)
    elif mouth == "a":
        d.ellipse([cx - w * 0.72, cy - h * 1.15, cx + w * 0.72, cy + h * 1.25],
                  fill=inner, outline=line, width=lwm)
    elif mouth == "o":
        r = w * 0.58
        d.ellipse([cx - r, cy - r * 1.12, cx + r, cy + r * 1.12],
                  fill=inner, outline=line, width=lwm)
    elif mouth == "e":
        d.ellipse([cx - w * 1.0, cy - h * 0.62, cx + w * 1.0, cy + h * 0.62],
                  fill=inner, outline=line, width=lwm)
    elif mouth == "i":
        d.ellipse([cx - w * 0.95, cy - h * 0.28, cx + w * 0.95, cy + h * 0.28],
                  fill=inner, outline=line, width=lwm)
    elif mouth == "u":
        r = w * 0.46
        d.ellipse([cx - r, cy - r * 0.92, cx + r, cy + r * 0.92],
                  fill=inner, outline=line, width=lwm)
    elif mouth == "m":
        d.line([(cx - w * 0.85, cy), (cx + w * 0.85, cy)], fill=line, width=max(1, lw))
    else:
        d.line([(cx - w * 0.75, cy), (cx + w * 0.75, cy)], fill=line, width=lwm)


def _draw_placeholder_character(char: dict, style: dict, size: tuple[int, int],
                                pose: str = "front", mouth: str = "closed",
                                blink: bool = False) -> Image.Image:
    """内置占位角色（``characters_v5`` 缺失/失败时使用）。

    纯 PIL 程序化绘制：按 ``appearance`` 的肤色/发色/服装/胡须/帽子 + 风格线条色
    画一个半身角色；``mouth`` 决定口型，``blink`` 决定闭眼。内部 2× 超采样。
    """
    width, height = int(size[0]), int(size[1])
    if width < 8 or height < 8:
        raise V5Error(f"角色画布过小: {size!r}")
    ss = 2
    W, H = width * ss, height * ss
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    app = char.get("appearance") or {}
    line_spec = style.get("line") or {}
    line = _hex_rgb(line_spec.get("color"), (28, 26, 22))
    lw = max(1, int(round(float(line_spec.get("width") or 3) * ss * 0.5)))
    skin = _hex_rgb(app.get("skin"), (240, 216, 192))
    skin_shadow = _scale_rgb(skin, 0.88)
    hair = _hex_rgb(app.get("hair"), (26, 26, 26))
    eye = _hex_rgb(app.get("eye"), (42, 42, 42))
    outfit = app.get("outfit") or {}
    cloth = _hex_rgb(outfit.get("main"), (239, 232, 216))
    accent = _hex_rgb(outfit.get("accent"), (122, 31, 26))
    chibi = _clamp(float(app.get("chibi") or 0.0), 0.0, 1.0)
    mouth_key = normalize_mouth(mouth)
    mouth_inner = _mix_rgb(accent, (90, 30, 30), 0.55)

    cx = W * 0.5
    head_r = W * (0.30 + 0.05 * chibi)
    head_cy = head_r * 1.04 + H * 0.020
    shoulder_y = head_cy + head_r * 1.30
    hem_y = H * 0.998
    body_half = W * (0.40 + 0.03 * chibi)
    neck_w = head_r * 0.42

    # 躯干
    torso = [(cx - body_half * 0.70, shoulder_y), (cx + body_half * 0.70, shoulder_y),
             (cx + body_half, hem_y), (cx - body_half, hem_y)]
    d.polygon(torso, fill=cloth)
    d.line(torso + [torso[0]], fill=line, width=lw, joint="curve")
    collar_y = shoulder_y + head_r * 0.06
    d.polygon([(cx - neck_w * 1.55, collar_y), (cx + neck_w * 1.55, collar_y),
               (cx, collar_y + head_r * 0.55)], fill=accent)
    d.line([(cx - neck_w * 1.55, collar_y), (cx, collar_y + head_r * 0.55),
            (cx + neck_w * 1.55, collar_y)], fill=line, width=lw, joint="curve")
    belt_y = shoulder_y + (hem_y - shoulder_y) * 0.54
    d.rectangle([cx - body_half * 0.95, belt_y, cx + body_half * 0.95,
                 belt_y + H * 0.020], fill=accent)

    # 手臂
    arm_w = W * 0.13
    for side in (-1, 1):
        ax = cx + side * (body_half * 0.94)
        d.rounded_rectangle([ax - arm_w / 2, shoulder_y + head_r * 0.10,
                             ax + arm_w / 2, hem_y - H * 0.05],
                            radius=arm_w / 2, fill=cloth, outline=line, width=lw)
    if pose == "point":
        ax = cx + body_half * 1.02
        ay = shoulder_y + (hem_y - shoulder_y) * 0.30
        d.rounded_rectangle([ax - arm_w * 0.5, ay - arm_w * 0.5,
                             ax + arm_w * 1.9, ay + arm_w * 0.5],
                            radius=arm_w * 0.5, fill=cloth, outline=line, width=lw)
        d.ellipse([ax + arm_w * 1.7, ay - arm_w * 0.28,
                   ax + arm_w * 2.1, ay + arm_w * 0.28],
                  fill=skin, outline=line, width=lw)
    elif pose == "think":
        ay = shoulder_y + (hem_y - shoulder_y) * 0.16
        d.line([(cx - body_half * 0.8, ay + arm_w * 0.8),
                (cx - body_half * 0.35, ay),
                (cx + head_r * 0.40, head_cy + head_r * 0.98)],
               fill=line, width=int(arm_w * 0.72), joint="curve")
        d.ellipse([cx + head_r * 0.28, head_cy + head_r * 0.74,
                   cx + head_r * 0.58, head_cy + head_r * 1.04],
                  fill=skin, outline=line, width=lw)

    # 脖子
    d.rounded_rectangle([cx - neck_w / 2, head_cy + head_r * 0.55,
                         cx + neck_w / 2, shoulder_y + head_r * 0.18],
                        radius=neck_w * 0.3, fill=skin_shadow)

    # 头
    d.ellipse([cx - head_r, head_cy - head_r, cx + head_r, head_cy + head_r * 1.04],
              fill=skin, outline=line, width=lw)
    for side in (-1, 1):
        ex = cx + side * head_r * 0.96
        d.ellipse([ex - head_r * 0.10, head_cy - head_r * 0.12,
                   ex + head_r * 0.10, head_cy + head_r * 0.22],
                  fill=skin_shadow, outline=line, width=max(1, lw - 1))

    # 头发 / 发髻 / 冠
    hair_style = str(app.get("hair_style") or "short").lower()
    d.pieslice([cx - head_r * 1.02, head_cy - head_r * 1.06,
                cx + head_r * 1.02, head_cy + head_r * 0.40],
               start=182, end=358, fill=hair, outline=line, width=lw)
    for side in (-1, 1):
        lx = cx + side * head_r * 0.92
        d.polygon([(lx, head_cy - head_r * 0.36),
                   (lx + side * head_r * 0.20, head_cy + head_r * 0.10),
                   (lx - side * head_r * 0.06, head_cy + head_r * 0.44)],
                  fill=hair, outline=line)
    if any(k in hair_style for k in ("guan", "bun", "topknot", "ancient")):
        d.ellipse([cx - head_r * 0.24, head_cy - head_r * 1.52,
                   cx + head_r * 0.24, head_cy - head_r * 1.04],
                  fill=hair, outline=line, width=lw)
    hat = str(outfit.get("hat") or app.get("hat") or "").lower()
    if hat:
        hat_h = head_r * 0.52
        hat_fill = hair if any(k in hat for k in ("guan", "lun", "dark")) \
            else _mix_rgb(cloth, (0, 0, 0), 0.45)
        d.polygon([(cx - head_r * 0.92, head_cy - head_r * 0.70),
                   (cx + head_r * 0.92, head_cy - head_r * 0.70),
                   (cx + head_r * 0.72, head_cy - head_r * 0.70 - hat_h),
                   (cx - head_r * 0.72, head_cy - head_r * 0.70 - hat_h)],
                  fill=hat_fill, outline=line, width=lw)
        d.line([(cx - head_r * 0.92, head_cy - head_r * 0.76),
                (cx + head_r * 0.92, head_cy - head_r * 0.76)],
               fill=accent, width=max(1, lw))

    # 眼睛 / 眉毛
    eye_dx = head_r * 0.42
    eye_cy = head_cy + head_r * 0.06
    eye_w = head_r * 0.26
    eye_h = head_r * 0.20
    for side in (-1, 1):
        if pose == "side" and side == -1:
            continue
        ex = cx + side * eye_dx
        if blink:
            d.arc([ex - eye_w, eye_cy - eye_h, ex + eye_w, eye_cy + eye_h * 0.95],
                  start=15, end=165, fill=line, width=max(1, lw))
        else:
            d.ellipse([ex - eye_w, eye_cy - eye_h * 0.95,
                       ex + eye_w, eye_cy + eye_h * 0.95],
                      fill=eye, outline=line, width=max(1, lw - 1))
            d.ellipse([ex - eye_w * 0.52, eye_cy - eye_h * 0.64,
                       ex - eye_w * 0.06, eye_cy - eye_h * 0.10], fill=(255, 255, 255))
        brow_y = eye_cy - eye_h * 1.75
        brow = str(app.get("brow") or "flat").lower()
        tilt = head_r * (0.09 if brow in ("long", "raised") else 0.025)
        d.line([(ex - eye_w * 1.18, brow_y + tilt * side),
                (ex + eye_w * 1.18, brow_y - tilt * side)],
               fill=hair, width=max(1, lw))

    # 鼻子 / 嘴 / 胡须
    d.line([(cx, eye_cy + head_r * 0.30), (cx - head_r * 0.05, eye_cy + head_r * 0.40)],
           fill=_scale_rgb(skin, 0.75), width=max(1, lw - 1))
    mouth_cy = eye_cy + head_r * 0.66
    _draw_mouth(d, cx, mouth_cy, head_r, mouth_key, mouth_inner, line, lw)
    beard = str(app.get("beard") or "").lower()
    if beard and beard not in ("none", "null", "false"):
        if "goatee" in beard:
            d.polygon([(cx - head_r * 0.20, mouth_cy + head_r * 0.14),
                       (cx + head_r * 0.20, mouth_cy + head_r * 0.14),
                       (cx + head_r * 0.05, mouth_cy + head_r * 0.92),
                       (cx - head_r * 0.05, mouth_cy + head_r * 0.92)],
                      fill=hair, outline=line)
        else:
            d.arc([cx - head_r * 0.62, mouth_cy - head_r * 0.26,
                   cx + head_r * 0.62, mouth_cy + head_r * 1.00],
                  start=20, end=160, fill=hair, width=int(max(2, head_r * 0.15)))

    # 配饰：羽扇
    if any("fan" in str(a).lower() for a in (app.get("accessories") or [])):
        fx0 = cx - body_half * 1.06
        fy0 = shoulder_y + (hem_y - shoulder_y) * 0.42
        d.ellipse([fx0 - W * 0.16, fy0 - H * 0.10, fx0 + W * 0.16, fy0 + H * 0.10],
                  fill=cloth, outline=line, width=lw)
        d.line([(fx0 + W * 0.14, fy0 + H * 0.08),
                (cx - body_half * 0.55, hem_y - H * 0.05)],
               fill=_mix_rgb(cloth, (0, 0, 0), 0.4), width=max(2, lw))

    return img.resize((width, height), Image.LANCZOS)


# --------------------------------------------------------------------------- #
# 角色立绘缓存 / characters_v5 对接
# --------------------------------------------------------------------------- #
class _CharacterRenderer:
    """统一入口：优先 ``pipeline.characters_v5.render_character``，失败则 fallback。

    按 ``(char_key, pose, mouth, blink, sprite_size, flip)`` 懒缓存；来源记录在
    ``source``（``characters_v5`` / ``fallback``）供 meta 与文档核对。
    """

    def __init__(self) -> None:
        self._module: Any = None
        self._module_checked = False
        self._characters_v5_ok = True
        self._cache: dict[tuple, Image.Image] = {}
        self._np_cache: dict[int, tuple[np.ndarray, np.ndarray]] = {}
        self._blend_cache: dict[tuple, tuple[np.ndarray, np.ndarray]] = {}
        self.source = "fallback"
        self.warnings: list[str] = []

    def _load_module(self) -> Any:
        if self._module_checked:
            return self._module
        self._module_checked = True
        try:
            from . import characters_v5 as mod  # type: ignore
            if hasattr(mod, "render_character"):
                self._module = mod
                LOG.info("motion2d: 使用 pipeline.characters_v5.render_character 渲染角色")
            else:  # pragma: no cover
                self._warn_once("characters_v5 无 render_character，使用内置占位角色")
        except Exception as exc:  # noqa: BLE001
            self._module = None
            LOG.debug("motion2d: characters_v5 不可用（%s），使用内置占位角色", exc)
        return self._module

    def _warn_once(self, msg: str) -> None:
        if msg not in self.warnings:
            self.warnings.append(msg)
            LOG.warning("motion2d: %s", msg)

    def retry_characters_v5(self) -> None:
        """characters_v5 之后才出现时重试（同进程热更新用）。"""
        if self._module is None:
            self._module_checked = False
            self._load_module()

    def get(self, char: dict, style: dict, spr_size: tuple[int, int],
            pose: str = "front", mouth: str = "closed", blink: bool = False,
            flip: bool = False) -> Image.Image:
        char_key = _char_key(char)
        pose = str(pose or "front")
        mouth = normalize_mouth(mouth)
        # 风格 id 参与缓存键：同一角色在不同风格下外观不同
        style_key = (style or {}).get("id") if isinstance(style, dict) else None
        if style_key is None:
            style_key = id(style)
        key = (char_key, pose, mouth, bool(blink), int(spr_size[0]), int(spr_size[1]),
               bool(flip), style_key)
        hit = self._cache.get(key)
        if hit is not None:
            return hit
        img: Image.Image | None = None
        mod = self._load_module()
        if mod is not None and self._characters_v5_ok:
            try:
                try:
                    img = mod.render_character(char, style, spr_size, pose=pose,
                                               mouth=mouth, blink=bool(blink))
                except TypeError:  # 兼容只接受位置参数的实现
                    img = mod.render_character(char, style, spr_size, pose, mouth,
                                               bool(blink))
                if not isinstance(img, Image.Image):
                    img = _to_image(img, "character")
                img = img.convert("RGBA")
                if img.size != tuple(spr_size):
                    img = img.resize(tuple(spr_size), Image.LANCZOS)
                self.source = "characters_v5"
            except Exception as exc:  # noqa: BLE001
                self._characters_v5_ok = False
                self._warn_once(f"characters_v5.render_character 失败（{exc}），"
                                f"后续使用内置占位角色")
                img = None
        if img is None:
            try:
                img = _draw_placeholder_character(char or {}, style or {},
                                                  tuple(spr_size), pose=pose,
                                                  mouth=mouth, blink=bool(blink))
            except Exception as exc:  # noqa: BLE001
                raise V5Error(f"占位角色绘制失败（{char_key}): {exc}") from exc
            if self.source != "characters_v5":
                self.source = "fallback"
        if flip:
            img = img.transpose(Image.FLIP_LEFT_RIGHT)
        self._cache[key] = img
        return img

    def arrays(self, char: dict, style: dict, spr_size: tuple[int, int], pose: str,
               mouth: str, blink: bool, flip: bool = False) -> tuple[np.ndarray, np.ndarray]:
        img = self.get(char, style, spr_size, pose, mouth, blink, flip)
        key = id(img)
        hit = self._np_cache.get(key)
        if hit is not None:
            return hit
        arr = np.asarray(img.convert("RGBA"), dtype=np.uint8)
        pair = (np.ascontiguousarray(arr[:, :, :3]), np.ascontiguousarray(arr[:, :, 3]))
        self._np_cache[key] = pair
        return pair

    def blend_arrays(self, char: dict, style: dict, spr_size: tuple[int, int], pose: str,
                     plan: Sequence[tuple[str, float]], blink: bool = False,
                     flip: bool = False) -> tuple[np.ndarray, np.ndarray]:
        """按 ``mouth_plan_at()`` 的口型方案做立绘混合（结果缓存）。

        ``plan = [("closed", 0.5), ("a", 0.5)]`` → 闭口立绘与 a 立绘各半，
        得到 2-3 帧的口型过渡；权重被量化到 1/64，保证缓存命中率。
        """
        plan = [(normalize_mouth(m), float(w)) for m, w in (plan or []) if float(w) > 0.001]
        total = sum(w for _, w in plan)
        if not plan or total <= 0:
            return self.arrays(char, style, spr_size, pose, "closed", blink, flip)
        plan = [(m, w / total) for m, w in plan]
        if len(plan) == 1:
            return self.arrays(char, style, spr_size, pose, plan[0][0], blink, flip)
        char_key = _char_key(char)
        style_key = (style or {}).get("id") if isinstance(style, dict) else None
        if style_key is None:
            style_key = id(style)
        quant = tuple((m, int(round(w * 64))) for m, w in plan)
        key = (char_key, str(pose), bool(blink), bool(flip), int(spr_size[0]),
               int(spr_size[1]), style_key, quant)
        hit = self._blend_cache.get(key)
        if hit is not None:
            return hit
        acc_rgb = None
        acc_a = None
        for mouth, w in plan:
            rgb, alpha = self.arrays(char, style, spr_size, pose, mouth, blink, flip)
            if acc_rgb is None:
                acc_rgb = rgb.astype(np.float32) * w
                acc_a = alpha.astype(np.float32) * w
            else:
                acc_rgb += rgb.astype(np.float32) * w
                acc_a += alpha.astype(np.float32) * w
        pair = (np.ascontiguousarray(np.clip(acc_rgb, 0, 255).astype(np.uint8)),
                np.ascontiguousarray(np.clip(acc_a, 0, 255).astype(np.uint8)))
        self._blend_cache[key] = pair
        return pair

    @property
    def cache_size(self) -> int:
        return len(self._cache)


_CHAR_RENDERER: _CharacterRenderer | None = None


def _char_renderer() -> _CharacterRenderer:
    global _CHAR_RENDERER
    if _CHAR_RENDERER is None:
        _CHAR_RENDERER = _CharacterRenderer()
    return _CHAR_RENDERER


def reset_character_cache() -> None:
    """清空角色立绘缓存（characters_v5 热更新后 / 测试用）。"""
    global _CHAR_RENDERER
    _CHAR_RENDERER = None


def _char_shadow_mask(w: int, h: int) -> Image.Image:
    """角色落地软阴影（L 遮罩，缓存；按 8px 对齐复用）。"""
    w = max(8, int(round(w / 8.0)) * 8)
    h = max(4, int(round(h / 8.0)) * 8)
    hit = _CHAR_SHADOW_CACHE.get((w, h))
    if hit is not None:
        return hit
    small = Image.new("L", (64, 16), 0)
    ImageDraw.Draw(small).ellipse([2, 2, 62, 14], fill=255)
    mask = small.resize((w, h), Image.BILINEAR).filter(ImageFilter.GaussianBlur(h / 3.0))
    mask = mask.point(lambda v: int(v * 0.38))     # 半透明
    _CHAR_SHADOW_CACHE[(w, h)] = mask
    return mask


# --------------------------------------------------------------------------- #
# 角色状态解析
# --------------------------------------------------------------------------- #
def _char_box(entry: dict, size: tuple[int, int]) -> tuple[float, float]:
    """角色显示尺寸（未超采样）：(w_nom, h_nom) 像素。"""
    width, height = size
    scale = float(entry.get("scale", 1.0) or 1.0)
    frac = float(entry.get("height", CHAR_DEFAULT_HEIGHT) or CHAR_DEFAULT_HEIGHT)
    aspect = float(entry.get("aspect", CHAR_DEFAULT_ASPECT) or CHAR_DEFAULT_ASPECT)
    h_nom = max(8.0, frac * height * scale)
    return max(8.0, h_nom * aspect), h_nom


def _char_fade(entry: dict, style: dict | None, t: float, duration: float,
               fps: float) -> tuple[float, float]:
    """入场/离场：返回 (alpha 系数 0~1, 入场时的纵向下沉像素)。

    默认：镜头开头 ``CHAR_FADE_FRAMES``(8) 帧从 anchor 下方 20px 升起并淡入，
    结尾同帧数淡出。可用条目字段 ``fade_in``/``fade_out``（帧数，0=关）或
    ``style["character_fade"] = false / {"in": 8, "out": 8}`` 覆盖。
    """
    st = style or {}
    fin = entry.get("fade_in")
    fout = entry.get("fade_out")
    if fin is None or fout is None:
        cfg = st.get("character_fade")
        if isinstance(cfg, dict):
            fin = cfg.get("in", CHAR_FADE_FRAMES) if fin is None else fin
            fout = cfg.get("out", CHAR_FADE_FRAMES) if fout is None else fout
        elif cfg is False:
            fin = 0 if fin is None else fin
            fout = 0 if fout is None else fout
        else:
            fin = CHAR_FADE_FRAMES if fin is None else fin
            fout = CHAR_FADE_FRAMES if fout is None else fout
    fps = max(1e-6, float(fps or 25.0))
    frames = max(1, int(round(float(duration) * fps)))
    frame = int(_clamp(round(float(t) * fps), 0, frames - 1))
    try:
        fin_i = int(max(0, int(fin)))
        fout_i = int(max(0, int(fout)))
    except (TypeError, ValueError):
        fin_i = fout_i = 0
    fade = 1.0
    rise = 0.0
    if fin_i > 0 and frame < fin_i:
        fade = (frame + 0.5) / float(fin_i)
        rise = CHAR_FADE_RISE_PX * (1.0 - fade)
    if fout_i > 0 and frame >= frames - fout_i:
        fade_out = max(0.0, (frames - frame - 0.5) / float(fout_i))
        fade = min(fade, fade_out)
    return _clamp(fade, 0.0, 1.0), rise


def character_state(entry: dict, layerset: _LayerSet, cam: dict, t: float,
                    duration: float, seed: int = 0,
                    renderer: _CharacterRenderer | None = None,
                    default_style: dict | None = None,
                    fps: float = 25.0,
                    mouth_strength: float | None = None) -> dict | None:
    """把一个角色条目解析成单帧状态（含立绘 numpy 与仿射矩阵）。

    角色条目（契约 §4 ``characters[i]``）::

        {"char": <character json>, "style": <style dict>|None, "role": "host",
         "pose": "front", "mouth_timeline": [(t0, t1, viseme), ...],
         "anchor": (nx, ny), "anchor_space": "scene"|"zone",
         "scale": 1.0, "blink": True, "depth": 0.12,
         "height": 0.62, "flip": False, "breath": True,
         "mouth_strength": 0.85, "mouth_transition": 2,
         "fade_in": 8, "fade_out": 8, "shadow": True}

    ``anchor_space="zone"`` 时 anchor 视为 **camera.zone 内**的归一化坐标
    （按当前取景窗换算回场景坐标）；默认 ``"scene"``，与 scene_render.anchors 一致。
    """
    cache = renderer or _char_renderer()
    char = _char_dict(entry)
    style = entry.get("style") or default_style or {}
    role = str(entry.get("role") or _char_key(entry.get("char")) or "char")
    width, height = layerset.size
    scale = float(entry.get("scale", 1.0) or 1.0)
    w_nom, h_nom = _char_box(entry, layerset.size)

    anchor = entry.get("anchor")
    if anchor is None:
        anchor = layerset.anchors.get(role, (0.5, 0.92))
    try:
        ax, ay = float(anchor[0]), float(anchor[1])
    except (TypeError, ValueError, IndexError):
        ax, ay = 0.5, 0.92
    if str(entry.get("anchor_space") or "scene").lower() == "zone":
        zone = cam.get("zone")
        if zone:
            zx, zy, zw, zh = (float(v) for v in zone)
            ax, ay = zx + ax * zw, zy + ay * zh
        else:
            LOG.debug("motion2d: 角色 %s anchor_space=zone 但机位没有 zone，按 scene 处理", role)

    if mouth_strength is None:
        mouth_strength = entry.get("mouth_strength")
    if mouth_strength is None:
        mouth_strength = (style or {}).get("mouth_strength", MOUTH_DEFAULT_STRENGTH)

    mouth_plan = mouth_plan_at(entry.get("mouth_timeline"), t, fps=fps,
                               strength=mouth_strength, default=entry.get("mouth", "closed"),
                               transition_frames=entry.get("mouth_transition",
                                                          MOUTH_TRANSITION_FRAMES))
    mouth = max(mouth_plan, key=lambda p: p[1])[0] if mouth_plan else "closed"
    blink = False
    if entry.get("blink", True):
        schedule = entry.get("_blink_schedule")
        if schedule is None:
            schedule = blink_schedule(duration, seed, role)
            entry["_blink_schedule"] = schedule
        blink = is_blinking(schedule, t)
    pose = str(entry.get("pose") or "front")

    phase = entry.get("_phase")
    if phase is None:
        phase = _char_phase(seed, role)
        entry["_phase"] = phase
    speaking = mouth != "closed"
    micro_x, micro_y = char_micro_motion(t, phase, speaking)
    if entry.get("breath", True) is False:
        micro_x = micro_y = 0.0

    spr_w = max(8, int(round(w_nom * CHAR_SPRITE_SUPERSAMPLE)))
    spr_h = max(8, int(round(h_nom * CHAR_SPRITE_SUPERSAMPLE)))
    flip = bool(entry.get("flip", False))
    if len(mouth_plan) == 1:
        rgb, alpha = cache.arrays(char, style, (spr_w, spr_h), pose,
                                  mouth_plan[0][0], blink, flip)
    else:
        rgb, alpha = cache.blend_arrays(char, style, (spr_w, spr_h), pose,
                                        mouth_plan, blink, flip)

    # 入场/离场（开头 6-10 帧从 anchor 下方 20px 升起 + alpha 0→1；结尾淡出）
    fade, rise_px = _char_fade(entry, style, t, duration, fps)

    # 相机：角色默认贴中景平面
    gain = float(cam.get("parallax_eff") or cam.get("parallax") or DEFAULT_PARALLAX)
    depth = float(entry.get("depth") or layerset.depth.get("midground",
                                                          DEFAULT_DEPTH["midground"]))
    ratio = _parallax_ratio(depth, layerset.depth_ref, gain)
    zoom, cam_dx, cam_dy, cam_shear = _plane_motion(cam, ratio)

    base_x = ax * width - w_nom / 2.0 + micro_x * scale
    base_y = ay * height - h_nom + micro_y * scale + rise_px
    kx = zoom * (w_nom / spr_w)
    ky = zoom * (h_nom / spr_h)
    tx = (base_x - width / 2.0) * zoom + width / 2.0 + cam_dx
    ty = (base_y - height / 2.0) * zoom + height / 2.0 + cam_dy
    m = np.array([[kx, cam_shear * ky,
                   tx + cam_shear * (ty - height / 2.0 - cam_dy)],
                  [0.0, ky, ty]], dtype=np.float64)

    # 落地软阴影（脚底锚点 + 相机变换后）
    foot_x = (ax * width - width / 2.0) * zoom + width / 2.0 + cam_dx + micro_x * zoom
    foot_y = ((ay * height + rise_px) - height / 2.0) * zoom + height / 2.0 + cam_dy
    if cam_shear:
        foot_x += cam_shear * (foot_y - height / 2.0 - cam_dy)
    shadow_on = entry.get("shadow")
    if shadow_on is None:
        shadow_on = (style or {}).get("character_shadow", True)
    shadow = None
    if shadow_on and fade > 0.02:
        shadow = (w_nom * zoom * 1.02, max(6.0, h_nom * zoom * 0.052), fade)
    return {
        "role": role, "rgb": rgb, "alpha": alpha, "matrix": m,
        "spr_size": (spr_w, spr_h), "pose": pose, "mouth": mouth, "blink": blink,
        "mouth_plan": mouth_plan, "alpha_mul": float(fade),
        "depth": depth, "anchor": (ax, ay), "scale": scale,
        "foot": (foot_x, foot_y), "shadow": shadow,
    }


def _resolve_characters_state(characters_state: Any, layerset: _LayerSet,
                              cam: dict, t: float, duration: float,
                              seed: int = 0,
                              default_style: dict | None = None,
                              fps: float = 25.0,
                              mouth_strength: float | None = None) -> list[dict]:
    """把各种形态的角色状态统一成 ``character_state`` 结果列表。"""
    out: list[dict] = []
    if not characters_state:
        return out
    if isinstance(characters_state, dict):
        characters_state = [characters_state]
    if not isinstance(characters_state, (list, tuple)):
        raise V5Error(f"characters_state 类型非法: {type(characters_state)!r}")
    width, height = layerset.size
    for entry in characters_state:
        if not isinstance(entry, dict):
            continue
        if "matrix" in entry and "rgb" in entry:      # 已解析状态
            out.append(entry)
            continue
        if entry.get("image") is not None:            # 直接贴图（cutout 立绘）
            img = _to_image(entry["image"], "character.image").convert("RGBA")
            # 可选整体缩放：height（占画面高度比例）优先，否则 scale（倍率）
            try:
                target_h = entry.get("height")
                if target_h:
                    th = max(8, int(round(float(target_h) * height)))
                    tw = max(1, int(round(th * img.size[0] / float(img.size[1]))))
                    img = img.resize((tw, th), Image.LANCZOS)
                else:
                    sc = float(entry.get("scale", 1.0) or 1.0)
                    if abs(sc - 1.0) > 1e-3:
                        img = img.resize((max(1, int(round(img.size[0] * sc))),
                                          max(1, int(round(img.size[1] * sc)))),
                                         Image.LANCZOS)
            except (TypeError, ValueError, ZeroDivisionError):
                pass
            arr = np.asarray(img, dtype=np.uint8)
            pos = entry.get("pos") or entry.get("anchor") or (0.5, 0.9)
            try:
                px, py = float(pos[0]), float(pos[1])
            except (TypeError, ValueError, IndexError):
                px, py = 0.5, 0.9
            if abs(px) <= 1.5 and abs(py) <= 1.5:
                px, py = px * width, py * height
            # 相机变换（与真实角色一致：绕锚点缩放 + 视差位移 + 切变）
            gain_i = float(cam.get("parallax_eff") or cam.get("parallax")
                           or DEFAULT_PARALLAX)
            depth_i = float(entry.get("depth") or 0.12)
            ratio_i = _parallax_ratio(depth_i, layerset.depth_ref, gain_i)
            z_i, dx_i, dy_i, sh_i = _plane_motion(cam, ratio_i)
            base_x = px - img.size[0] / 2.0
            base_y = py - img.size[1]
            tx = (base_x - width / 2.0) * z_i + width / 2.0 + dx_i
            ty = (base_y - height / 2.0) * z_i + height / 2.0 + dy_i
            out.append({
                "role": str(entry.get("role") or "char"),
                "rgb": np.ascontiguousarray(arr[:, :, :3]),
                "alpha": np.ascontiguousarray(arr[:, :, 3]),
                "matrix": np.array(
                    [[z_i, sh_i * z_i, tx + sh_i * (ty - height / 2.0 - dy_i)],
                     [0.0, z_i, ty]], dtype=np.float64),
                "spr_size": img.size, "pose": "front", "mouth": "closed",
                "blink": False, "depth": depth_i,
                "alpha_mul": float(entry.get("alpha", 1.0) or 1.0),
            })
            continue
        if not entry.get("_resolved_copy"):
            entry = dict(entry)
        out.append(character_state(entry, layerset, cam, t, duration, seed,
                                   default_style=default_style, fps=fps,
                                   mouth_strength=mouth_strength))
    return [s for s in out if s]


# --------------------------------------------------------------------------- #
# 特效层
# --------------------------------------------------------------------------- #
def _fx_canvas(key: str, size: tuple[int, int], seed: int) -> Image.Image:
    """生成无缝（上下/左右可环绕）黑底 RGB 特效纹理。"""
    width, height = size
    rng = random.Random(f"{seed}|{key}")
    tex = Image.new("RGB", (width, height), (0, 0, 0))
    d = ImageDraw.Draw(tex)

    def line(x0: float, y0: float, x1: float, y1: float, fill, w: int) -> None:
        # 环绕复制，保证滚动无缝
        for ox in (-width, 0, width):
            for oy in (-height, 0, height):
                d.line([(x0 + ox, y0 + oy), (x1 + ox, y1 + oy)], fill=fill, width=w)

    def dot(x: float, y: float, r: float, fill) -> None:
        for ox in (-width, 0, width):
            for oy in (-height, 0, height):
                d.ellipse([x - r + ox, y - r + oy, x + r + ox, y + r + oy], fill=fill)

    if key == "rain":
        for _ in range(max(40, int(width * height / 5200))):
            x = rng.uniform(0, width)
            y = rng.uniform(0, height)
            length = rng.uniform(height * 0.020, height * 0.075)
            ang = math.radians(rng.uniform(8, 16))
            b = rng.randint(70, 190)
            line(x, y, x + length * math.sin(ang), y + length * math.cos(ang),
                 (min(255, int(b * 0.86)), min(255, int(b * 0.94)), min(255, b)),
                 1 if b < 140 else 2)
    elif key == "snow":
        for _ in range(max(30, int(width * height / 7000))):
            b = rng.randint(120, 220)
            dot(rng.uniform(0, width), rng.uniform(0, height),
                rng.uniform(0.8, 2.6), (b, b, min(255, b + 18)))
    elif key in ("dust", "embers"):
        for _ in range(max(30, int(width * height / 9000))):
            b = rng.randint(90, 210)
            col = (b, int(b * 0.55), int(b * 0.22)) if key == "embers" \
                else (int(b * 0.9), int(b * 0.92), b)
            dot(rng.uniform(0, width), rng.uniform(0, height), rng.uniform(0.7, 2.2), col)
    elif key == "fog":
        small_w, small_h = max(8, width // 8), max(8, height // 8)
        small = Image.new("L", (small_w, small_h), 0)
        ds = ImageDraw.Draw(small)
        srng = random.Random(f"{seed}|fog")
        for _ in range(8):
            bx = srng.uniform(0, small_w)
            by = srng.uniform(0, small_h)
            rw = srng.uniform(small_w * 0.22, small_w * 0.62)
            rh = srng.uniform(small_h * 0.16, small_h * 0.44)
            for ox in (-small_w, 0, small_w):
                for oy in (-small_h, 0, small_h):
                    ds.ellipse([bx - rw + ox, by - rh + oy, bx + rw + ox, by + rh + oy],
                               fill=srng.randint(70, 130))
        small = small.filter(ImageFilter.GaussianBlur(max(1.0, small_w / 10.0)))
        arr = np.asarray(small.resize((width, height), Image.BILINEAR),
                         dtype=np.float32) / 255.0
        col = np.array([236, 241, 248], dtype=np.float32)
        tex = Image.fromarray((arr[:, :, None] * col[None, None, :]).astype(np.uint8))
    elif key == "speed_lines":
        cx, cy = width / 2.0, height / 2.0
        n = 96
        for i in range(n):
            ang = 2.0 * math.pi * i / n + rng.uniform(-0.02, 0.02)
            r0 = rng.uniform(0.30, 0.52) * max(width, height)
            r1 = r0 + rng.uniform(0.05, 0.22) * max(width, height)
            b = rng.randint(55, 160)
            d.line([(cx + math.cos(ang) * r0, cy + math.sin(ang) * r0),
                    (cx + math.cos(ang) * r1, cy + math.sin(ang) * r1)],
                   fill=(b, b, b), width=rng.choice([1, 1, 2]))
    else:
        raise V5Error(f"未知特效类型: {key!r}")
    return tex


def _radial_glow(radius: int, color: Sequence[int], power: float = 2.2) -> np.ndarray:
    """径向光晕（黑底 RGB，加色叠加）。"""
    r = max(2, int(radius))
    size = 2 * r + 1
    yy, xx = np.mgrid[0:size, 0:size]
    dist = np.sqrt((xx - r) ** 2 + (yy - r) ** 2) / float(r)
    falloff = np.clip(1.0 - dist, 0.0, 1.0) ** power
    sprite = falloff[:, :, None] * np.array(color, dtype=np.float32)[None, None, :]
    return np.ascontiguousarray(sprite.astype(np.uint8))


def _vignette_mask(size: tuple[int, int], strength: float) -> np.ndarray:
    """乘性暗角遮罩（H,W,3 uint8）。"""
    width, height = size
    sw, sh = max(16, width // 8), max(16, height // 8)
    yy, xx = np.mgrid[0:sh, 0:sw]
    nx = (xx / (sw - 1) - 0.5) * 2.0
    ny = (yy / (sh - 1) - 0.5) * 2.0
    r = np.sqrt(nx ** 2 + ny ** 2) / math.sqrt(2.0)
    mask = np.clip(1.0 - float(_clamp(strength, 0.0, 1.0)) * (r ** 1.6), 0.0, 1.0)
    m = np.ascontiguousarray((mask * 255.0).astype(np.uint8))
    if cv2 is not None:
        m = cv2.resize(m, (width, height), interpolation=cv2.INTER_LINEAR)
    else:  # pragma: no cover
        m = np.asarray(Image.fromarray(m).resize((width, height), Image.BILINEAR),
                       dtype=np.uint8)
    return np.ascontiguousarray(np.repeat(m[:, :, None], 3, axis=2))


def normalize_fx(*sources: Any) -> list[dict]:
    """把 style/fx、scene meta/fx 等来源统一成 ``[{"type": str, **params}]``。"""
    out: list[dict] = []
    seen: set[str] = set()

    def add(item: Any) -> None:
        if item is None:
            return
        if isinstance(item, str):
            key = item.strip().lower()
            if key and key not in seen:
                seen.add(key)
                out.append({"type": key})
            return
        if isinstance(item, dict):
            if "type" in item:
                key = str(item.get("type", "")).strip().lower()
                if key and key not in seen:
                    seen.add(key)
                    out.append(dict(item, type=key))
                return
            for key, params in item.items():
                k = str(key).strip().lower()
                if k in seen:
                    continue
                seen.add(k)
                entry = dict(params) if isinstance(params, dict) else {}
                entry["type"] = k
                out.append(entry)
            return
        if isinstance(item, (list, tuple)):
            for sub in item:
                add(sub)

    for src in sources:
        add(src)
    canon = {"mist": "fog", "halo": "glow", "candle_glow": "glow",
             "candle": "glow", "particles": "dust", "rain_heavy": "rain",
             "snow_light": "snow"}
    final: list[dict] = []
    for item in out:
        t = canon.get(item["type"], item["type"])
        if t not in SUPPORTED_FX:
            LOG.warning("motion2d: 未知特效 %r 已忽略（支持 %s）", item["type"],
                        ", ".join(SUPPORTED_FX))
            continue
        item["type"] = t
        final.append(item)
    return final


class _FxBank:
    """把特效配置烘成纹理，逐帧做加色/乘法合成（~0.1-0.5ms/层）。"""

    def __init__(self, specs: Sequence[dict], size: tuple[int, int], seed: int,
                 anchors: dict | None = None) -> None:
        self.size = (int(size[0]), int(size[1]))
        self.seed = int(seed)
        self.anchors = dict(anchors or {})
        self.back: list[dict] = []
        self.front: list[dict] = []
        self.types: list[str] = []
        w, h = self.size
        for spec in specs:
            t = str(spec.get("type"))
            side = str(spec.get("layer") or "").lower()
            try:
                if t in ("rain", "snow", "dust", "fog", "speed_lines"):
                    tex_size = (w, h) if t == "speed_lines" else (w, int(h * 1.6))
                    tex = np.ascontiguousarray(np.asarray(_fx_canvas(t, tex_size, self.seed),
                                                          dtype=np.uint8))
                    item = {
                        "type": t, "tex": tex,
                        "speed": float(spec.get("speed", 0.0) or 0.0),
                        "drift": float(spec.get("drift", 0.0) or 0.0),
                        "weight": float(spec.get("weight", 1.0) or 0.0),
                        "phase": float(spec.get("phase", 0.0) or 0.0),
                    }
                    is_back = side == "back" or (not side and t == "fog")
                    (self.back if is_back else self.front).append(item)
                    self.types.append(t)
                elif t == "glow":
                    radius = int(max(4, float(spec.get("radius", 0.22) or 0.22)
                                     * min(w, h)))
                    color = _hex_rgb(spec.get("color"), (255, 176, 92))
                    item = {
                        "type": "glow",
                        "sprite": _radial_glow(radius, color,
                                               float(spec.get("power", 2.2) or 2.2)),
                        "pos": self._resolve_pos(spec),
                        "weight": float(spec.get("weight", 0.85) or 0.0),
                        "flicker": float(spec.get("flicker", 0.22) or 0.0),
                        "phase": float(spec.get("phase", 0.0) or 0.0),
                    }
                    (self.back if side == "back" else self.front).append(item)
                    self.types.append(t)
                elif t == "vignette":
                    self.front.append({
                        "type": "vignette",
                        "mask": _vignette_mask(self.size,
                                               float(spec.get("strength", 0.35) or 0.0)),
                    })
                    self.types.append(t)
            except V5Error:
                raise
            except Exception as exc:  # noqa: BLE001
                LOG.warning("motion2d: 特效 %s 构建失败（%s），跳过", t, exc)

    def _resolve_pos(self, spec: dict) -> tuple[float, float]:
        anchor = spec.get("anchor")
        if isinstance(anchor, str) and anchor in self.anchors:
            ax, ay = self.anchors[anchor]
            return float(ax) * self.size[0], float(ay) * self.size[1]
        w, h = self.size
        return float(spec.get("x", 0.5) or 0.0) * w, float(spec.get("y", 0.5) or 0.0) * h

    # -- 帧处理 ----------------------------------------------------------- #
    def _window(self, tex: np.ndarray, off_y: float) -> np.ndarray:
        """纵向滚动取窗（带环绕），返回 (H, W, 3)。"""
        h = self.size[1]
        th = tex.shape[0]
        oy = int(round(off_y)) % th
        if oy + h <= th:
            return tex[oy:oy + h]
        return np.vstack([tex[oy:], tex[:h - (th - oy)]])

    def _apply_texture(self, frame: np.ndarray, item: dict, t: float) -> None:
        kind = item["type"]
        if kind == "rain":
            speed = item["speed"] or 620.0
            off_y = speed * t + item["phase"]
            weight = item["weight"]
        elif kind == "snow":
            speed = item["speed"] or 70.0
            off_y = speed * t + item["phase"]
            weight = item["weight"] * 0.95
        elif kind in ("dust", "embers"):
            speed = item["speed"] or 26.0
            off_y = -speed * t + item["phase"]
            weight = item["weight"] * 0.9
        elif kind == "fog":
            speed = item["speed"] or 12.0
            off_y = speed * t + item["phase"] + 40.0
            weight = item["weight"] * (0.13 + 0.05 * math.sin(t * 0.7 + item["phase"]))
        elif kind == "speed_lines":
            off_y = item["phase"]
            weight = item["weight"] * (0.45 + 0.55 * abs(math.sin(t * 3.1)))
        else:
            return
        if weight <= 0.002:
            return
        window = self._window(item["tex"], off_y)
        if kind in ("snow", "dust", "embers"):
            # 轻微横向漂移（整数像素滚动，成本 ~1ms）
            dx = int(round(9.0 * math.sin(t * 0.45 + item["phase"])))
            if dx:
                window = np.roll(window, dx, axis=1)
        self._add_weighted(frame, window, weight)

    @staticmethod
    def _add_weighted(frame: np.ndarray, overlay: np.ndarray, weight: float) -> None:
        w = float(_clamp(weight, 0.0, 4.0))
        if cv2 is not None:
            cv2.addWeighted(frame, 1.0, overlay, w, 0.0, dst=frame)
        else:  # pragma: no cover
            frame[:] = np.clip(frame.astype(np.float32)
                               + overlay.astype(np.float32) * w, 0, 255).astype(np.uint8)

    def _apply_glow(self, frame: np.ndarray, item: dict, t: float) -> None:
        sprite = item["sprite"]
        sh, sw = sprite.shape[:2]
        cx, cy = item["pos"]
        x0, y0 = int(round(cx - sw / 2.0)), int(round(cy - sh / 2.0))
        h, w = frame.shape[:2]
        sx0, sy0 = max(0, -x0), max(0, -y0)
        dx0, dy0 = max(0, x0), max(0, y0)
        dx1, dy1 = min(w, x0 + sw), min(h, y0 + sh)
        if dx1 <= dx0 or dy1 <= dy0:
            return
        sub = frame[dy0:dy1, dx0:dx1]
        ov = sprite[sy0:sy0 + (dy1 - dy0), sx0:sx0 + (dx1 - dx0)]
        flick = 1.0 - item["flicker"] * 0.5 * (1.0 + math.sin(t * 6.1 + item["phase"]))
        weight = item["weight"] * float(_clamp(flick, 0.0, 2.0))
        if cv2 is not None:
            cv2.addWeighted(sub, 1.0, ov, weight, 0.0, dst=sub)
        else:  # pragma: no cover
            sub[:] = np.clip(sub.astype(np.float32)
                             + ov.astype(np.float32) * weight, 0, 255).astype(np.uint8)

    def apply(self, frame: np.ndarray, t: float, side: str = "front") -> None:
        items = self.back if side == "back" else self.front
        for item in list(items):
            try:
                if item["type"] == "vignette":
                    if cv2 is not None:
                        cv2.multiply(frame, item["mask"], dst=frame, scale=1.0 / 255.0)
                    else:  # pragma: no cover
                        frame[:] = (frame.astype(np.float32) * item["mask"] / 255.0
                                    ).astype(np.uint8)
                elif item["type"] == "glow":
                    self._apply_glow(frame, item, t)
                else:
                    self._apply_texture(frame, item, t)
            except Exception as exc:  # noqa: BLE001
                LOG.warning("motion2d: 特效 %s 应用失败（%s），跳过该特效", item["type"], exc)
                items.remove(item)


def _build_fx_bank(fx_specs: Sequence[dict], size: tuple[int, int], seed: int,
                   anchors: dict | None = None) -> _FxBank | None:
    if not fx_specs:
        return None
    try:
        sig = json.dumps(list(fx_specs), sort_keys=True, default=str)
    except (TypeError, ValueError):
        sig = repr(fx_specs)
    key = (sig, int(size[0]), int(size[1]), int(seed))
    bank = _FX_CACHE.get(key)
    if bank is None:
        bank = _FxBank(fx_specs, size, seed, anchors)
        if len(_FX_CACHE) >= _FX_CACHE_MAX:
            _FX_CACHE.pop(next(iter(_FX_CACHE)))
        _FX_CACHE[key] = bank
    return bank


def _fx_specs_for(layers: Any, style: dict | None) -> list[dict]:
    """从 style / scene meta / layers["fx"] 收集特效配置。

    另外按风格自动补暗角：``style["texture"]`` 含 ``vignette``，或 ``style["grade"]``
    里出现 ``vignette``/``vignette=…``，且没有显式配置 vignette 时，追加
    ``{"type": "vignette", "strength": style.get("vignette_strength", 0.25)}``。
    """
    scene_fx = None
    lay_fx = None
    if isinstance(layers, dict):
        scene_fx = (layers.get("meta") or {}).get("fx")
        lay_fx = layers.get("fx")
    style_fx = (style or {}).get("fx") if isinstance(style, dict) else None
    specs = normalize_fx(style_fx, scene_fx, lay_fx)
    if not any(s.get("type") == "vignette" for s in specs):
        st = style if isinstance(style, dict) else {}
        texture = st.get("texture") or []
        grade = st.get("grade") or st.get("grade_filter") or ""
        wants = (st.get("vignette") is not None
                 or (isinstance(texture, (list, tuple))
                     and any("vignette" in str(t).lower() for t in texture))
                 or "vignette" in str(grade).lower())
        if wants:
            strength = st.get("vignette")
            if not isinstance(strength, (int, float)):
                strength = st.get("vignette_strength", 0.25)
            try:
                strength = _clamp(float(strength), 0.0, 1.0)
            except (TypeError, ValueError):
                strength = 0.25
            if strength > 0:
                specs.append({"type": "vignette", "strength": strength,
                              "auto": True})
    return specs


# --------------------------------------------------------------------------- #
# 合成
# --------------------------------------------------------------------------- #
def _resolve_cam(cam_in: Any, t: float, duration: float,
                 size: tuple[int, int], seed: int = 0) -> dict:
    """接受「已解析状态」或「camera spec」两种形态。"""
    if not isinstance(cam_in, dict):
        return camera_state(None, t, duration, size, seed)
    if "zoom" in cam_in and "progress" in cam_in:
        return cam_in
    if "type" in cam_in or "zone_from" in cam_in or "zone_to" in cam_in:
        dur = float(cam_in.get("duration", duration) or duration)
        if dur <= 0:
            dur = max(1.0, float(t))
        return camera_state(cam_in, t, dur, size, seed)
    return {
        "zoom": float(cam_in.get("zoom", 1.0) or 1.0),
        "x": float(cam_in.get("x", 0.0) or 0.0),
        "y": float(cam_in.get("y", 0.0) or 0.0),
        "shake_x": float(cam_in.get("shake_x", 0.0) or 0.0),
        "shake_y": float(cam_in.get("shake_y", 0.0) or 0.0),
        "type": str(cam_in.get("type") or "static"),
        "progress": float(cam_in.get("progress", 0.0) or 0.0),
        "strength": float(cam_in.get("strength", 0.0) or 0.0),
        "ease": str(cam_in.get("ease") or "in_out"),
        "shake": float(cam_in.get("shake", 0.0) or 0.0),
        "parallax": float(cam_in.get("parallax", DEFAULT_PARALLAX) or DEFAULT_PARALLAX),
        "zone": cam_in.get("zone"),
        # ---- V5.4 附加键（部分状态也要齐全，避免下游 KeyError）----
        "mode": str(cam_in.get("mode") or "normal"),
        "parallax_eff": float(cam_in.get("parallax_eff")
                              or cam_in.get("parallax", DEFAULT_PARALLAX)
                              or DEFAULT_PARALLAX),
        "focus_shift": float(cam_in.get("focus_shift", 0.0) or 0.0),
        "focus_blur_px": float(cam_in.get("focus_blur_px", 0.0) or 0.0),
        "motion_blur": float(cam_in.get("motion_blur", 0.0) or 0.0),
        "motion_blur_px": float(cam_in.get("motion_blur_px", 0.0) or 0.0),
        "shear": float(cam_in.get("shear", 0.0) or 0.0),
        "orbit_dx": float(cam_in.get("orbit_dx", 0.0) or 0.0),
        "orbit_dy": float(cam_in.get("orbit_dy", 0.0) or 0.0),
        "orbit_dz": float(cam_in.get("orbit_dz", 0.0) or 0.0),
        "dolly_zoom_amt": float(cam_in.get("dolly_zoom_amt", 0.0) or 0.0),
    }


def _paste_shadow(base: Image.Image, foot: Any, size: Any) -> None:
    """在脚底贴一块软阴影（提升"落地感"）；size = (w, h, alpha 系数)。"""
    try:
        fx, fy = float(foot[0]), float(foot[1])
        sw, sh = int(round(size[0])), int(round(size[1]))
        strength = float(size[2]) if len(size) > 2 else 1.0
    except (TypeError, ValueError, IndexError):
        return
    if sw < 8 or sh < 4 or strength <= 0.02:
        return
    mask = _char_shadow_mask(sw, sh)
    sw, sh = mask.size          # 遮罩按 8px 对齐，以实际尺寸为准
    if strength < 0.995:
        mask = mask.point(lambda v: int(v * strength))
    w, h = base.size
    x0 = int(round(fx - sw / 2.0))
    y0 = int(round(fy - sh / 2.0))
    bx0, by0 = max(0, x0), max(0, y0)
    bx1, by1 = min(w, x0 + sw), min(h, y0 + sh)
    if bx1 <= bx0 or by1 <= by0:
        return
    if (bx0, by0, bx1, by1) != (x0, y0, x0 + sw, y0 + sh):
        mask = mask.crop((bx0 - x0, by0 - y0, bx1 - x0, by1 - y0))
    base.paste((26, 22, 17), (bx0, by0, bx1, by1), mask)


def _char_layer_image(char_state: dict, out_size: tuple[int, int],
                      ) -> tuple[tuple[Image.Image, Image.Image] | None, tuple[int, int]]:
    """把立绘按仿射贴到目标画布区域，返回 ((RGB, A), (x0, y0))。"""
    rgb = char_state["rgb"]
    alpha = char_state["alpha"]
    m = char_state["matrix"]
    sh, sw = rgb.shape[:2]
    x0, y0, x1, y1 = _dest_bbox_rect(0, 0, sw, sh, m, out_size, pad=2)
    if x1 <= x0 or y1 <= y0:
        return None, (0, 0)
    m2 = m.copy()
    m2[0, 2] -= x0
    m2[1, 2] -= y0
    local = (x1 - x0, y1 - y0)
    warped_rgb = _warp_rgb(rgb, m2, local, replicate=False)
    warped_a = _warp_alpha(alpha, m2, local)
    mul = float(char_state.get("alpha_mul", 1.0))
    if mul < 0.999:  # 入场/离场淡入淡出：只缩放 alpha，不改 RGB
        warped_a = (warped_a.astype(np.uint16) * int(_clamp(mul, 0.0, 1.0) * 255)
                    // 255).astype(np.uint8)
    return (Image.fromarray(warped_rgb), Image.fromarray(warped_a)), (x0, y0)


def _affine_full(zoom: float, tx: float, ty: float, shear: float = 0.0,
                 width: float = 0.0, height: float = 0.0,
                 dx: float = 0.0, dy: float = 0.0) -> np.ndarray:
    """带轻微切变的前向仿射（V5.4 orbit 用）。

    屏幕坐标：``x = zoom·u + shear·(zoom·v + ty - H/2 - dy) + tx``，
    即围绕画面中心做横向切变（x 随 y 偏移），用于 2.5D 环绕的"轻微切变"。
    """
    if not shear:
        return _affine(zoom, tx, ty)
    m = np.array([[zoom, shear * zoom,
                   tx + shear * (ty - height / 2.0 - dy)],
                  [0.0, zoom, ty]], dtype=np.float64)
    return m


def _focus_weights(cam: dict, layerset: "_LayerSet") -> dict[str, float]:
    """rack_focus：按 depth 到焦平面的距离算每层模糊权重 0..1。

    焦平面深度 = lerp(背景depth, 前景depth, focus_shift)；权重 = 距离 / 景深范围。
    """
    shift = float(cam.get("focus_shift", 0.0) or 0.0)
    if shift <= 0.001 and not cam.get("_focus_active"):
        # shift==0 时仍可能处于"焦点在远"的起始状态（前景模糊），照常计算
        pass
    d_bg = float(layerset.depth.get("background", DEFAULT_DEPTH["background"]))
    d_mid = float(layerset.depth.get("midground", DEFAULT_DEPTH["midground"]))
    d_fg = float(layerset.depth.get("foreground", DEFAULT_DEPTH["foreground"]))
    focus_depth = _lerp(d_bg, d_fg, _clamp(shift, 0.0, 1.0))
    span = max(1e-3, abs(d_fg - d_bg))
    out = {}
    for key, d in (("background", d_bg), ("midground", d_mid), ("foreground", d_fg)):
        out[key] = _clamp(abs(d - focus_depth) / span, 0.0, 1.0)
    return out


def _compose_base(layers: Any, characters_state: Any, camera_state: Any,
                  size: tuple[int, int], t: float, style: dict | None,
                  duration: float, seed: int, fps: float = 25.0,
                  mouth_strength: float | None = None
                  ) -> tuple[Image.Image, _LayerSet, dict]:
    """图层 + 角色合成（不含动态特效），返回 (RGB 图, LayerSet, 相机状态)。"""
    width, height = int(size[0]), int(size[1])
    layerset = _get_layerset(layers, (width, height), camera_state, duration, seed,
                             style=style)
    cam_spec = _resolve_zone_refs(camera_state, layerset.camera_zones)
    cam = _resolve_cam(cam_spec, t, duration, (width, height), seed)
    # V5.4：truck 等会加强视差（parallax_eff），orbit/dolly_zoom 用 mode 分支
    gain = float(cam.get("parallax_eff") or cam.get("parallax")
                 or DEFAULT_PARALLAX)
    focus_w: dict[str, float] = {}
    if str(cam.get("mode") or "normal") == "normal" and float(
            cam.get("focus_shift", 0.0) or 0.0) > 0.0 or cam.get("type") == "rack_focus":
        focus_w = _focus_weights(cam, layerset)

    base = Image.new("RGB", (width, height), layerset.bg_color)
    for layer in layerset.layers:
        zoom, dx, dy, shear = _layer_motion(layer, cam, layerset.depth_ref, gain)
        mx, my = layer.margin
        tx = (width / 2.0 + dx) - (width / 2.0 + mx) * zoom
        ty = (height / 2.0 + dy) - (height / 2.0 + my) * zoom
        m = _affine_full(zoom, tx, ty, shear, width, height, dx, dy)
        warped_rgb = _warp_rgb(layer.rgb, m, (width, height), replicate=layer.opaque)
        warped_a = None
        if not layer.opaque:
            assert layer.alpha is not None
            warped_a = _warp_alpha(layer.alpha, m, (width, height))
        # ---- rack_focus：按 depth 权重与"额外模糊版本"做混合 ----
        fw = float(focus_w.get(layer.key, 0.0))
        if fw > 0.02 and layer.rgb_focus is not None \
                and layer.rgb_focus.shape == layer.rgb.shape:
            blur_rgb = _warp_rgb(layer.rgb_focus, m, (width, height),
                                 replicate=layer.opaque)
            if layer.alpha is None or layer.alpha_focus is None:
                warped_rgb = _blend_into(warped_rgb, blur_rgb, fw)
            else:
                blur_a = _warp_alpha(layer.alpha_focus, m, (width, height))
                warped_rgb = _blend_into(warped_rgb, blur_rgb, fw)
                assert warped_a is not None
                warped_a = _blend_into(warped_a, blur_a, fw)
        if layer.opaque:
            base.paste(Image.fromarray(warped_rgb), (0, 0))
            continue
        assert warped_a is not None
        cx0, cy0, cx1, cy1 = layer.content_box
        x0, y0, x1, y1 = _dest_bbox_rect(cx0, cy0, cx1, cy1, m, (width, height), pad=1)
        if x1 <= x0 or y1 <= y0:
            continue
        sub_rgb = np.ascontiguousarray(warped_rgb[y0:y1, x0:x1])
        sub_a = np.ascontiguousarray(warped_a[y0:y1, x0:x1])
        base.paste(Image.fromarray(sub_rgb), (x0, y0), Image.fromarray(sub_a))

    for st in _resolve_characters_state(characters_state, layerset, cam, t, duration,
                                        seed, default_style=style, fps=fps,
                                        mouth_strength=mouth_strength):
        try:
            shadow = st.get("shadow")
            if shadow:
                _paste_shadow(base, st.get("foot"), shadow)
            pair, (x0, y0) = _char_layer_image(st, (width, height))
        except Exception as exc:  # noqa: BLE001
            LOG.warning("motion2d: 角色 %s 合成失败（%s），跳过", st.get("role"), exc)
            continue
        if pair is None:
            continue
        base.paste(pair[0], (x0, y0), pair[1])
    return base, layerset, cam


def compose_frame(layers: Any, characters_state: Any, camera_state: Any,
                  size: tuple[int, int] = (1280, 720), t: float = 0.0,
                  *, style: dict | None = None, duration: float = 3.0,
                  seed: int = 0, fps: float = 25.0,
                  mouth_strength: float | None = None) -> Image.Image:
    """合成单帧并返回 ``PIL.Image``（RGB，含动态特效）。

    * ``layers``：``scene_render.render_scene_layers`` 返回值，或
      ``prepare_layers()`` 预处理结果（``render_shot`` 走后者更快）；
    * ``characters_state``：``character_state()`` 结果列表，或角色条目
      （``{"char":..., "anchor":..., "mouth_timeline":...}``），或
      ``{"image": PIL/RGB, "pos": (x, y)}`` 直接贴图；
    * ``camera_state``：``camera_state()`` 结果或 camera spec（按 ``t`` 自动解析）；
    * ``fps`` / ``mouth_strength``：口型平滑与淡入淡出的帧率/峰值幅度（可省）。

    V5.4：若相机是 ``rack_focus`` 会在图层合成阶段做前后景虚化混合；若是
    ``whip_pan`` 会在特效之后对整帧施加水平运动模糊。
    """
    width, height = int(size[0]), int(size[1])
    base, layerset, cam = _compose_base(layers, characters_state, camera_state,
                                        (width, height), t, style, duration, seed,
                                        fps=fps, mouth_strength=mouth_strength)
    fx_specs = _fx_specs_for(layers, style)
    bank = None
    if fx_specs:
        bank = _build_fx_bank(fx_specs, (width, height), seed, layerset.anchors)
    motion_blur_px = float(cam.get("motion_blur_px", 0.0) or 0.0)
    if bank is not None or motion_blur_px >= 2.0:
        frame = np.frombuffer(bytearray(base.tobytes()), dtype=np.uint8).reshape(
            height, width, 3)
        if bank is not None:
            bank.apply(frame, t, "back")
            bank.apply(frame, t, "front")
        _apply_motion_blur(frame, cam)
        return Image.fromarray(frame)
    return base


def render_still(layers: Any, characters_state: Any, camera_state: Any,
                 size: tuple[int, int] = (1280, 720), t: float = 0.0,
                 out_png: str | Path | None = None, *,
                 style: dict | None = None, duration: float = 3.0,
                 seed: int = 0, fps: float = 25.0,
                 mouth_strength: float | None = None) -> Image.Image:
    """渲染单帧静帧（预览 / contact sheet / QC）。

    返回 ``PIL.Image``（RGB）；给 ``out_png`` 时同时落盘 PNG（父目录自动创建）。
    """
    img = compose_frame(layers, characters_state, camera_state, size, t,
                        style=style, duration=duration, seed=seed, fps=fps,
                        mouth_strength=mouth_strength)
    if out_png is not None:
        path = Path(out_png)
        path.parent.mkdir(parents=True, exist_ok=True)
        img.save(path)
        img.info["saved"] = str(path)
    return img


# --------------------------------------------------------------------------- #
# 视频编码
# --------------------------------------------------------------------------- #
class _VideoWriter:
    """裸帧 → ffmpeg stdin（h264 / yuv420p / 无音轨）。"""

    def __init__(self, out_mp4: Path, size: tuple[int, int], fps: int,
                 crf: str | None = None, preset: str | None = None,
                 log_path: Path | None = None) -> None:
        self.out = Path(out_mp4)
        self.out.parent.mkdir(parents=True, exist_ok=True)
        self.size = (int(size[0]), int(size[1]))
        self.fps = int(fps)
        self.crf = str(crf or config.X264_CRF)
        self.preset = str(preset or config.X264_PRESET)
        self.log_path = log_path
        self.frames = 0
        self.cmd = [
            config.FFMPEG_BIN, "-hide_banner", "-nostdin", "-loglevel", "error", "-y",
            "-f", "rawvideo", "-pix_fmt", "rgb24",
            "-s", f"{self.size[0]}x{self.size[1]}", "-r", str(self.fps),
            "-i", "pipe:0", "-an",
            "-c:v", "libx264", "-preset", self.preset, "-crf", self.crf,
            "-pix_fmt", "yuv420p", "-movflags", "+faststart",
            str(self.out),
        ]
        self._err_file = None
        try:
            if self.log_path is not None:
                self.log_path.parent.mkdir(parents=True, exist_ok=True)
                self._err_file = open(self.log_path, "w", encoding="utf-8",
                                      errors="replace")
                stderr: Any = self._err_file
            else:
                stderr = subprocess.PIPE
            self.proc = subprocess.Popen(self.cmd, stdin=subprocess.PIPE,
                                        stdout=subprocess.DEVNULL, stderr=stderr)
        except FileNotFoundError as exc:  # pragma: no cover
            raise V5Error(f"找不到 ffmpeg: {config.FFMPEG_BIN}") from exc

    def write(self, frame: Any) -> None:
        if isinstance(frame, np.ndarray):
            if frame.shape[:2] != (self.size[1], self.size[0]):
                raise V5Error(f"帧尺寸 {frame.shape[:2]} != {self.size[::-1]}")
            data: Any = np.ascontiguousarray(frame, dtype=np.uint8).tobytes()
        elif isinstance(frame, (bytes, bytearray, memoryview)):
            data = frame
        else:
            raise V5Error(f"不支持的帧类型: {type(frame)!r}")
        try:
            self.proc.stdin.write(data)  # type: ignore[union-attr]
        except (BrokenPipeError, OSError) as exc:
            raise V5Error(f"ffmpeg 写入失败（{exc}）: {self._stderr_tail()}") from exc
        self.frames += 1

    def _stderr_tail(self) -> str:
        if self._err_file is not None and self.log_path is not None:
            try:
                self._err_file.flush()
                return Path(self.log_path).read_text(encoding="utf-8",
                                                     errors="replace")[-1500:]
            except OSError:
                return ""
        try:
            if self.proc.stderr is not None:
                return self.proc.stderr.read().decode("utf-8", "replace")[-1500:]
        except (OSError, ValueError):
            pass
        return ""

    def close(self) -> None:
        try:
            if self.proc.stdin is not None:
                self.proc.stdin.close()
        except (OSError, ValueError):
            pass
        code = self.proc.wait()
        if self._err_file is not None:
            try:
                self._err_file.close()
            except OSError:
                pass
        if code != 0:
            raise V5Error(f"ffmpeg 编码失败 (exit {code}): {self._stderr_tail()}")

    def abort(self) -> None:
        try:
            if self.proc.stdin is not None:
                self.proc.stdin.close()
        except (OSError, ValueError):
            pass
        try:
            self.proc.kill()
        except OSError:
            pass
        try:
            self.proc.wait(timeout=10)
        except Exception:  # noqa: BLE001
            pass
        if self._err_file is not None:
            try:
                self._err_file.close()
            except OSError:
                pass


# --------------------------------------------------------------------------- #
# 主入口
# --------------------------------------------------------------------------- #
def _normalize_characters(characters: Sequence[dict] | dict | None) -> list[dict]:
    if not characters:
        return []
    if isinstance(characters, dict):
        characters = [characters]
    if not isinstance(characters, (list, tuple)):
        raise V5Error(f"characters 类型非法: {type(characters)!r}")
    out: list[dict] = []
    for i, entry in enumerate(characters):
        if not isinstance(entry, dict):
            raise V5Error(f"characters[{i}] 必须是 dict，收到 {type(entry)!r}")
        item = dict(entry)
        if "char" not in item:
            item["char"] = {k: v for k, v in item.items()
                            if k in ("id", "label", "appearance", "persona")}
        item.setdefault("role", str(_char_key(item.get("char")) or f"char{i}"))
        out.append(item)
    return out


def render_shot(layers: dict, characters: list[dict], camera: dict,
                duration: float, fps: int, out_mp4: str | Path,
                style: dict, *, width: int = 1280, height: int = 720,
                seed: int = 0, workdir: str | Path | None = None,
                mouth_strength: float | None = None) -> dict:
    """分层视差 + 角色动画 + 特效 → mp4（h264，无音轨；音频由调用方合成）。

    ``mouth_strength``（0~1，可选）压低口型峰值幅度，render_v5 建议传 0.85；
    也可用 ``style["mouth_strength"]`` 或单角色条目里的 ``mouth_strength`` 覆盖。

    返回::

        {"path": str, "duration": float, "fps": int, "frames": int,
         "width": int, "height": int, "elapsed_sec": float,
         "render_fps": float, "meta": {...}}
    """
    t_start = time.perf_counter()
    width, height = int(width), int(height)
    if width <= 0 or height <= 0:
        raise V5Error(f"非法分辨率 {width}x{height}")
    duration = float(duration or 0.0)
    if not math.isfinite(duration) or duration <= 0:
        raise V5Error(f"duration 必须 > 0，收到 {duration!r}")
    fps = int(fps or 0)
    if fps <= 0 or fps > 120:
        raise V5Error(f"fps 非法: {fps!r}")
    frames = max(1, int(round(duration * fps)))
    style = dict(style or {})
    camera = dict(camera or {"type": "static"})
    if not str(camera.get("type") or "").strip():
        camera["type"] = "static"
    camera = _resolve_zone_refs(camera, (layers or {}).get("camera_zones") or {})

    workdir_path = Path(workdir) if workdir is not None else None
    if workdir_path is not None:
        workdir_path.mkdir(parents=True, exist_ok=True)

    # ---- 1. 图层准备（含 overscan / 前景景深虚化） -----------------------
    t_prep = time.perf_counter()
    layerset = prepare_layers(layers, (width, height), camera, duration, seed,
                              style=style)
    prep_ms = (time.perf_counter() - t_prep) * 1000.0

    # ---- 2. 角色准备 -----------------------------------------------------
    char_entries = _normalize_characters(characters)
    if len(char_entries) > 12:
        raise V5Error(f"单镜角色过多: {len(char_entries)}（上限 12）")
    renderer = _char_renderer()
    renderer.retry_characters_v5()
    for entry in char_entries:
        entry["style"] = entry.get("style") or style
        if entry.get("blink", True):
            entry["_blink_schedule"] = blink_schedule(duration, seed,
                                                      str(entry.get("role")))
        entry["_phase"] = _char_phase(seed, str(entry.get("role")))

    # ---- 3. 特效 ---------------------------------------------------------
    fx_specs = _fx_specs_for(layers, style)
    fx_bank = _FxBank(fx_specs, (width, height), seed, layerset.anchors) if fx_specs else None

    # ---- 4. 逐帧合成 + 编码 ---------------------------------------------
    out_path = Path(out_mp4)
    log_path = (workdir_path / f"ffmpeg_{out_path.stem}.log") if workdir_path else None
    writer = _VideoWriter(out_path, (width, height), fps, log_path=log_path)
    compose_times: list[float] = []
    cam_log: dict[str, Any] = {}
    v54_log: dict[str, Any] = {"focus_shift_end": 0.0, "motion_blur_peak": 0.0,
                               "shear_peak": 0.0, "dolly_zoom_end": 0.0,
                               "parallax_eff": 1.0}
    stills: dict[str, str] = {}
    still_targets = {0: "f000", frames // 2: f"f{frames // 2:03d}"}
    try:
        for i in range(frames):
            t = i / float(fps)
            cam = camera_state(camera, t, duration, (width, height), seed)
            if i == 0:
                cam_log = {"type": cam["type"], "strength": cam["strength"],
                           "ease": cam["ease"], "zoom0": round(cam["zoom"], 6),
                           "zone": cam.get("zone"), "mode": cam.get("mode"),
                           "parallax_eff": round(float(cam.get("parallax_eff", 0.0)), 4)}
            v54_log["focus_shift_end"] = float(cam.get("focus_shift", 0.0) or 0.0)
            v54_log["motion_blur_peak"] = max(
                v54_log["motion_blur_peak"], float(cam.get("motion_blur", 0.0) or 0.0))
            v54_log["shear_peak"] = max(
                v54_log["shear_peak"], abs(float(cam.get("shear", 0.0) or 0.0)))
            v54_log["dolly_zoom_end"] = float(cam.get("dolly_zoom_amt", 0.0) or 0.0)
            v54_log["parallax_eff"] = round(float(cam.get("parallax_eff", 1.0) or 1.0), 4)
            t0 = time.perf_counter()
            img = _compose_base(layerset, char_entries, cam, (width, height), t,
                                style, duration, seed, fps=fps,
                                mouth_strength=mouth_strength)[0]
            compose_times.append((time.perf_counter() - t0) * 1000.0)
            if workdir_path is not None and i in still_targets:
                try:
                    p = workdir_path / f"motion2d_{out_path.stem}_{still_targets[i]}.png"
                    img.save(p)
                    stills[still_targets[i]] = str(p)
                except OSError as exc:
                    LOG.warning("motion2d: 静帧落盘失败（%s）", exc)
            buf = bytearray(img.tobytes())
            frame = np.frombuffer(buf, dtype=np.uint8).reshape(height, width, 3)
            if fx_bank is not None:
                fx_bank.apply(frame, t, "back")
                fx_bank.apply(frame, t, "front")
            _apply_motion_blur(frame, cam)      # V5.4 whip_pan 水平运动模糊（原地）
            writer.write(buf)
    except BaseException:
        writer.abort()
        raise
    writer.close()

    elapsed = time.perf_counter() - t_start
    render_fps = frames / elapsed if elapsed > 0 else 0.0
    probe: dict[str, Any] = {}
    try:
        probe = utils.probe_video(out_path)
    except Exception as exc:  # noqa: BLE001
        LOG.warning("motion2d: ffprobe 失败（%s）", exc)

    meta: dict[str, Any] = {
        "camera": {**cam_log, "spec": {k: ("callable" if callable(v) else v)
                                        for k, v in camera.items()}},
        "layers": [{"key": lyr.key, "depth": lyr.depth, "opaque": lyr.opaque,
                    "margin": lyr.margin, "content_box": list(lyr.content_box),
                    "blur_px": lyr.blur_px}
                   for lyr in layerset.layers],
        "depth_ref": layerset.depth_ref,
        "size": [width, height],
        "fx": [spec.get("type") for spec in fx_specs],
        "characters": [{"role": e.get("role"), "pose": e.get("pose", "front"),
                        "anchor": e.get("anchor"), "scale": e.get("scale", 1.0),
                        "height": e.get("height", CHAR_DEFAULT_HEIGHT),
                        "anchor_space": e.get("anchor_space", "scene"),
                        "fade_in": e.get("fade_in", CHAR_FADE_FRAMES),
                        "fade_out": e.get("fade_out", CHAR_FADE_FRAMES),
                        "mouth_strength": e.get("mouth_strength",
                                                style.get("mouth_strength",
                                                          mouth_strength))}
                       for e in char_entries],
        "mouth": {"strength": mouth_strength if mouth_strength is not None
                              else style.get("mouth_strength", MOUTH_DEFAULT_STRENGTH),
                  "transition_frames": MOUTH_TRANSITION_FRAMES,
                  "closed_gap": True},
        "char_source": renderer.source,
        "char_sprites": renderer.cache_size,
        "char_warnings": list(renderer.warnings),
        "seed": seed,
        "codec": "h264",
        "pix_fmt": "yuv420p",
        "audio": False,
        "scene": layerset.meta.get("scene") if isinstance(layerset.meta, dict) else None,
        "style": style.get("id"),
        "prep_ms": round(prep_ms, 2),
        "compose_ms_avg": round(float(np.mean(compose_times)), 2) if compose_times else 0.0,
        "compose_ms_max": round(float(np.max(compose_times)), 2) if compose_times else 0.0,
        "stills": stills,
        "probe": probe,
        # V5.4 电影级镜头统计（给 editorial/QC 用）
        "v54": {k: round(v, 4) if isinstance(v, float) else v
                for k, v in v54_log.items()},
    }
    result = {
        "path": str(out_path),
        "duration": round(frames / float(fps), 4),
        "fps": fps,
        "frames": frames,
        "width": width,
        "height": height,
        "elapsed_sec": round(elapsed, 3),
        "render_fps": round(render_fps, 2),
        "meta": meta,
    }
    if workdir_path is not None:
        try:
            utils.write_json(workdir_path / f"motion2d_{out_path.stem}.json", result)
        except Exception as exc:  # noqa: BLE001
            LOG.warning("motion2d: 写 meta 失败（%s）", exc)
    LOG.info("motion2d: %s %dx%d %df@%dfps 用时 %.2fs（%.1f 帧/秒，合成 %.1fms/帧）",
             out_path.name, width, height, frames, fps, elapsed, render_fps,
             meta["compose_ms_avg"])
    return result


__all__ = [
    "V5Error",
    "SUPPORTED_CAMERA_TYPES",
    "CINEMATIC_CAMERA_TYPES",
    "CAMERA_ALIASES",
    "SUPPORTED_FX",
    "LAYER_KEYS",
    "DEFAULT_DEPTH",
    "DEFAULT_PARALLAX",
    "MOUTH_STATES",
    "camera_state",
    "ease",
    "prepare_layers",
    "compose_frame",
    "render_still",
    "render_shot",
    "character_state",
    "mouth_at",
    "mouth_plan_at",
    "normalize_mouth",
    "blink_schedule",
    "is_blinking",
    "normalize_fx",
    "reset_character_cache",
]


# --------------------------------------------------------------------------- #
# 简易 CLI（`python -m pipeline.motion2d --help`）
# --------------------------------------------------------------------------- #
def _cli(argv: Sequence[str] | None = None) -> int:
    import argparse

    ap = argparse.ArgumentParser(
        prog="python -m pipeline.motion2d",
        description="V5 motion2d：分层视差动画渲染器（列出能力 / 单镜自测渲染）")
    ap.add_argument("--list-cameras", action="store_true", help="列出全部 camera 类型与别名")
    ap.add_argument("--list-easings", action="store_true", help="列出全部 easing 名称")
    ap.add_argument("--list-fx", action="store_true", help="列出全部特效类型")
    ap.add_argument("--self-test", metavar="OUT_MP4",
                    help="用程序化 mock 图层渲染 1 个 1.2s 片段（验证链路）")
    ap.add_argument("--camera", default="orbit", help="--self-test 使用的 camera 类型")
    args = ap.parse_args(argv)
    if args.list_cameras:
        print("camera 类型（%d）：" % len(SUPPORTED_CAMERA_TYPES))
        for name in SUPPORTED_CAMERA_TYPES:
            print("  -", name, "(cinematic)" if name in CINEMATIC_CAMERA_TYPES else "")
        print("\n别名：")
        for alias, target in sorted(CAMERA_ALIASES.items()):
            if alias != target:
                print(f"  {alias} -> {target}")
        return 0
    if args.list_easings:
        print("easing：linear in out in_out smootherstep "
              "in_sine out_sine in_out_sine in_quad out_quad in_out_quad "
              "in_cubic out_cubic in_out_cubic in_quart out_quart in_out_quart "
              "in_quint out_quint in_out_quint in_expo out_expo in_out_expo "
              "in_back out_back in_out_back in_circ out_circ in_out_circ")
        return 0
    if args.list_fx:
        print("fx：" + " ".join(SUPPORTED_FX))
        return 0
    if args.self_test:
        # 极简程序化图层（不依赖 scene_render / characters_v5）
        w, h = 640, 360
        bg = Image.new("RGB", (w, h))
        px = bg.load()
        for y in range(h):
            for x in range(w):
                px[x, y] = (40 + x * 120 // w, 60 + y * 120 // h, 110)
        bg = bg.convert("RGBA")
        mid = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        d = ImageDraw.Draw(mid)
        for i in range(6):
            x = 40 + i * 95
            d.rectangle([x, h * 0.45, x + 46, h * 0.9], fill=(220, 210, 190, 235))
            d.ellipse([x + 9, h * 0.33, x + 37, h * 0.61], fill=(236, 214, 186, 255))
        fg = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        ImageDraw.Draw(fg).rectangle([0, 0, w, 16], fill=(20, 18, 16, 230))
        layers = {"background": bg, "midground": mid, "foreground": fg,
                  "depth": {"background": 0.02, "midground": 0.12, "foreground": 0.3}}
        style = {"id": "cli_test", "palette": {"bg": ["#808080"], "fg": ["#101010"],
                                               "accent": ["#b23a2e"]},
                 "line": {"width": 2, "color": "#101010"}, "fx": ["rain"]}
        res = render_shot(layers, [], {"type": args.camera, "strength": 0.12},
                          1.2, 25, args.self_test, style, width=w, height=h)
        print(f"OK {res['path']}  {res['frames']}帧  {res['render_fps']:.1f} 帧/秒  "
              f"camera={args.camera}  v54={res['meta'].get('v54')}")
        return 0
    ap.print_help()
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(_cli())
