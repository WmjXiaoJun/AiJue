# -*- coding: utf-8 -*-
"""V5.2 角色 AI 美术：SDXL 立绘生成 + 白底抠图 + 口型/眨眼 overlay + 风格化。

管线（docs/PLAN_V5_2_ART_FRONTEND.md §2）：
    contract/v5/characters/<id>.json
      → tmp/v52_art/specs/<id>.json（V5→imagegen V3 spec，已由 tmp/gen_v52_char_specs.py 生成）
      → imagegen.cli（SDXL Animagine XL 4.0，ComfyUI 8188，串行）
          portrait 832x1216（角色卡/近景）
          three_view/front 1024x1024（白底全身 → 抠图）
      → remove_white / clean_background → cutout.png（RGBA 透明）
      → assets/v5/characters/<id>/{portrait.png,front.png,cutout.png,meta.json}
      → characters_v5.render_character 使用 AI 立绘 + style_v5.apply_finish(kind="character")
        + YuNet 脸部定位 + 口型/眨眼 overlay（与程序化路径同一接口）。

对外 API：
    generate_character_art(character_id, outdir=None, *, style="anime", seed=42,
                           force=False, prompt_extra=None, log=print) -> dict
    character_art(character_id) -> dict
    mouth_shapes() -> list[str]
    apply_mouth(img, viseme, *, geom=None) -> PIL.Image(RGBA)
    apply_blink(img, *, geom=None, strength=1.0) -> PIL.Image(RGBA)
    render_art_character(char, style, size, pose="front", mouth="closed",
                         blink=False) -> PIL.Image(RGBA) | None

约束：
- 不联网（除本地 ComfyUI 127.0.0.1:8188）；不重启 ComfyUI；不修改 imagegen 代码；
- 生成串行（ComfyUI 队列本身串行）；已存在 cutout 且非 force → 跳过（复用最早 3 角色）；
- 口型/眨眼若 YuNet 检测失败，用按脸部比例的兜底几何，不阻断。
"""
from __future__ import annotations

import json
import math
import os
import subprocess
import sys
import time
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

REPO = Path(__file__).resolve().parents[1]
ASSETS_DIR = REPO / "assets" / "v5" / "characters"
V52_ROOT = REPO / "tmp" / "v52_art"
SPECS_DIR = V52_ROOT / "specs"
CONTRACT_CHARS = REPO / "contract" / "v5" / "characters"

MOUTH_STATES: tuple[str, ...] = ("closed", "a", "o", "e", "i", "u", "m")
# 最早 3 个角色由 Lead 直接复用 V3 资产（source=v3_reuse），不重新生成
REUSE_IDS: tuple[str, ...] = ("laozhou", "ali", "dawei")
# 统一提示词库（template-author 交付：24 角色 portrait/front/extra/negative）
PROMPTS_PATH = REPO / "contract" / "v5" / "art" / "prompts.json"
# front 生成时强化的正向/负向（Lead P1：纯白底、无 ink blob/背景）
FRONT_EXTRA = ("full body, standing, simple background, pure white background, "
               "isolated on white background, no shadow, no scenery")
# IP-Adapter 参考一致性（portrait→front 同画风）：README §0.3 经验参数
FRONT_REF_PROMPT = ("highly detailed full body illustration, intricate clothing folds, "
                    "detailed shading, same character as reference")
FRONT_REF_NEGATIVE = ("flat color, simple cartoon, vector art, chibi, plain, "
                      "grey background, dark background, gradient background, "
                      "decorative background, halo, oversized prop, giant fan, "
                      "fan behind, canopy, umbrella, wings, background fan, "
                      "circular background, round backdrop, oval background, "
                      "colored circle, pastel circle, background circle")
FRONT_REF_IP = {"ip_strength": 0.35, "ip_end_at": 0.90,
                "ip_weight_type": "style transfer"}
# 参考模式强度回退表（第一档 strongest，白底/装饰物不达标则降档重试）
FRONT_REF_SCHEDULE = ((0.35, 0.90), (0.25, 0.80), (0.22, 0.75))
# front 提示词里剔除“场景/世界观” tag：SDXL 会因此画出青绿 ink wash 背景块
FRONT_SCENE_TAGS = {
    "chinese ancient setting", "historical", "sage strategist", "ancient china",
    "modern setting", "talk show host", "studio", "cinematic",
}
FRONT_NEG_EXTRA = ("background, scenery, gradient background, ink splatter, teal, "
                   "shadow on ground, multiple views, cropped, water splash, "
                   "color wash, paint splatter, background elements")
WHITE = 238          # 泛洪去白阈值（与 scripts/make_2d_cutouts.py 一致）
TOL = 18
WHITE_CORNER = 238   # front 四角白底阈值（RGB 均值 >=238 且色差 <=12）
FACE_MIN_SCORE = 0.3
# cutout 质量门限（Lead P1：alpha0 0.3-0.6、bbox 高度 >=60% 画面、四角 alpha=0）
# 注：front-ref 精细立绘可能有宽披风/裙摆，前景占比低 → 上限放宽到 0.72
CUTOUT_ALPHA0_RANGE = (0.30, 0.72)
CUTOUT_MIN_BBOX_H = 0.55
OVERLAY_MIN_FACE_W = 30.0   # 渲染后脸宽 < 30px 不叠口型/眨眼（Lead 拍板：中景 0.62H 需可见）
_FACE_MODEL = REPO / "local_models" / "lipsync_models" / "yunet" / "face_detection_yunet_2023mar.onnx"

_ART_CACHE: dict[str, dict] = {}
_FACE_CACHE: dict[tuple, dict] = {}
_YUNET = None


class V5ArtError(RuntimeError):
    """AI 美术资产生成/加载失败。"""


# ---------------------------------------------------------------------------
# 0. 小工具
# ---------------------------------------------------------------------------
def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def _log(log, msg: str) -> None:
    try:
        log(msg)
    except Exception:  # noqa: BLE001
        print(msg)


def _read_json(path: Path, default=None):
    try:
        with open(path, "r", encoding="utf-8-sig") as f:
            return json.load(f)
    except Exception:  # noqa: BLE001
        return default


def _write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def _hex(rgb) -> str:
    return "#" + "".join(f"{int(max(0, min(255, v))):02x}" for v in rgb[:3])


def _rgb(hex_color: str, default=(240, 216, 192)):
    try:
        s = str(hex_color or "").strip().lstrip("#")
        if len(s) == 3:
            s = "".join(c * 2 for c in s)
        if len(s) >= 6:
            return tuple(int(s[i:i + 2], 16) for i in (0, 2, 4))
    except Exception:  # noqa: BLE001
        pass
    return default


def _mix(a, b, t: float):
    t = max(0.0, min(1.0, float(t)))
    return tuple(int(round(a[i] + (b[i] - a[i]) * t)) for i in range(3))


def mouth_shapes() -> list[str]:
    """口型帧列表（与 characters_v5.mouth_states 顺序一致）。"""
    return list(MOUTH_STATES)


# ---------------------------------------------------------------------------
# 1. 抠图：白底泛洪 + 孤立色块清理 + 边缘羽化
# ---------------------------------------------------------------------------
def remove_ground_blobs(im: Image.Image) -> tuple[Image.Image, dict]:
    """删除人物脚下/背景残留的“地面圆斑/色块”（IP-Adapter 偶尔画出的装饰圆盘）。

    只删：面积≥1%、贴画面底部（top>0.72H）、宽≥35%画宽、高<28%画高 的宽扁组件。
    正常裙摆/披风从上半身连下来，top 不会这么低，不会被误删。
    """
    info = {"removed": 0, "components": []}
    try:
        import cv2
    except Exception:  # noqa: BLE001
        return im, info
    arr = np.array(im.convert("RGBA"))
    alpha = arr[:, :, 3]
    h, w = alpha.shape[:2]
    binary = (alpha > 16).astype(np.uint8)
    n, labels, stats, _ = cv2.connectedComponentsWithStats(binary, connectivity=8)
    total = float(h * w)
    drop = np.zeros_like(binary)
    for idx in range(1, n):
        area = float(stats[idx, cv2.CC_STAT_AREA])
        cw = float(stats[idx, cv2.CC_STAT_WIDTH])
        ch = float(stats[idx, cv2.CC_STAT_HEIGHT])
        top = float(stats[idx, cv2.CC_STAT_TOP])
        if (area / total >= 0.01 and top > h * 0.72 and cw >= w * 0.35
                and ch < h * 0.28):
            drop |= (labels == idx)
            info["components"].append([int(stats[idx, 0]), int(top), int(cw), int(ch)])
    if drop.any():
        arr[:, :, 3] = np.where(drop > 0, 0, alpha)
        info["removed"] = int(drop.sum())
        return Image.fromarray(arr, "RGBA"), info
    return im, info


def remove_white(im: Image.Image, with_report: bool = False):
    """白底泛洪抠图（scripts/make_2d_cutouts.remove_white 的工程化版本）。

    - 只在亮度高/低饱和的近白像素上泛洪，且只删除与画布边界连通的区域；
    - 额外删除人物背后的大块均匀色 wash（remove_wide_wash）；
    - 边缘 alpha 羽化 0.8px，按 bbox 裁掉透明外框（保留 8px padding）。
    with_report=True 时返回 (image, wash_info)。
    """
    from collections import deque

    im = im.convert("RGBA")
    a = np.array(im).astype(np.int16)
    h, w = a.shape[:2]
    rgb = a[:, :, :3]
    mx = rgb.max(axis=2)
    mn = rgb.min(axis=2)
    whiteish = (mn >= WHITE - TOL) & ((mx - mn) <= 12)
    seen = np.zeros((h, w), dtype=bool)
    q = deque()
    for x in range(w):
        for y in (0, h - 1):
            if whiteish[y, x] and not seen[y, x]:
                seen[y, x] = True
                q.append((y, x))
    for y in range(h):
        for x in (0, w - 1):
            if whiteish[y, x] and not seen[y, x]:
                seen[y, x] = True
                q.append((y, x))
    while q:
        y, x = q.popleft()
        for dy, dx in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            ny, nx = y + dy, x + dx
            if 0 <= ny < h and 0 <= nx < w and whiteish[ny, nx] and not seen[ny, nx]:
                seen[ny, nx] = True
                q.append((ny, nx))
    alpha = np.where(seen, 0, 255).astype(np.uint8)
    out = Image.fromarray(np.dstack([rgb.astype(np.uint8), alpha]), "RGBA")
    out, wash = remove_wide_wash(out)
    out, ground = remove_ground_blobs(out)
    if ground.get("removed"):
        wash = dict(wash or {})
        wash["ground"] = ground
    al = out.getchannel("A").filter(ImageFilter.GaussianBlur(0.8))
    out.putalpha(al)
    out = clean_islands(out)
    bbox = out.getbbox()
    if bbox:
        pad = 8
        out = out.crop((max(0, bbox[0] - pad), max(0, bbox[1] - pad),
                        min(out.width, bbox[2] + pad), min(out.height, bbox[3] + pad)))
    return (out, wash) if with_report else out


def clean_islands(im: Image.Image, min_ratio: float = 0.0015) -> Image.Image:
    """删除抠图后的碎小孤岛（白底残留/飞点），保留主要连通组件。

    SDXL 偶尔会在人物背后画色块/水花；它们通常是与人物断开的独立组件。
    这里删除面积 < min_ratio×画布 的组件，并把其余全部保留（避免误删帽饰、
    飘带等与身体断开的小部件）。
    """
    try:
        import cv2
    except Exception:  # noqa: BLE001
        return im
    arr = np.array(im.convert("RGBA"))
    alpha = arr[:, :, 3]
    binary = (alpha > 16).astype(np.uint8)
    total = float(binary.size) or 1.0
    n, labels, stats, _ = cv2.connectedComponentsWithStats(binary, connectivity=8)
    keep = np.zeros_like(binary)
    for idx in range(1, n):
        area = float(stats[idx, cv2.CC_STAT_AREA])
        if area / total >= min_ratio:
            keep[labels == idx] = 1
    if not keep.any():  # 全部被判定为小岛 → 保留原图
        return im
    arr[:, :, 3] = np.where(keep > 0, alpha, 0)
    return Image.fromarray(arr, "RGBA")


def remove_wide_wash(im: Image.Image) -> tuple[Image.Image, dict]:
    """删除人物背后的大块均匀色 wash（如 SDXL 画的青绿 ink blob）。

    只在“高饱和且宽幅”时动手：把不透明像素按 HSV 粗量化找最大色簇，若该色簇占比
    >8% 且存在面积>4%、横向跨度>45% 画宽的连通块，则认为是背景 wash 并删除；
    人物衣服上的同色小块（腰带/滚边）不会被删。
    """
    info = {"removed": False, "color": None, "ratio": 0.0}
    try:
        import cv2
    except Exception:  # noqa: BLE001
        return im, info
    arr = np.array(im.convert("RGBA"))
    alpha = arr[:, :, 3]
    opaque = alpha > 16
    total = float(opaque.sum())
    if total < 100:
        return im, info
    hsv = cv2.cvtColor(arr[:, :, :3], cv2.COLOR_RGB2HSV)
    sat = hsv[:, :, 1].astype(np.int16)
    val = hsv[:, :, 2].astype(np.int16)
    cand = opaque & (sat >= 55) & (val >= 55)
    if cand.sum() / total < 0.08:
        return im, info
    hue_bin = (hsv[:, :, 0].astype(np.int16) // 12)
    s_bin = (sat // 64)
    key = hue_bin * 16 + s_bin
    keys = key[cand]
    vals, counts = np.unique(keys, return_counts=True)
    if not len(vals):
        return im, info
    best = vals[int(np.argmax(counts))]
    ratio = float(counts.max()) / total
    if ratio < 0.08:
        return im, info
    color_mask = cand & (key == best)
    n, labels, stats, _ = cv2.connectedComponentsWithStats(
        color_mask.astype(np.uint8), connectivity=8)
    width = arr.shape[1]
    drop = np.zeros_like(opaque)
    for idx in range(1, n):
        area = float(stats[idx, cv2.CC_STAT_AREA])
        cw = float(stats[idx, cv2.CC_STAT_WIDTH])
        if area / total >= 0.04 and cw / max(1.0, float(width)) >= 0.45:
            drop |= (labels == idx)
    if not drop.any():
        return im, info
    arr[:, :, 3] = np.where(drop, 0, alpha)
    px = arr[:, :, :3][drop]
    info = {"removed": True, "ratio": round(float(drop.sum()) / total, 3),
            "color": [int(v) for v in np.median(px.reshape(-1, 3), axis=0)] if len(px) else None}
    return Image.fromarray(arr, "RGBA"), info


def _border_white_ratio(im: Image.Image) -> float:
    """画布边缘 6px 的近白像素比例（判断白底是否干净，用于重试）。"""
    a = np.array(im.convert("RGB"))
    edge = np.concatenate([a[:6, :, :].reshape(-1, 3), a[-6:, :, :].reshape(-1, 3),
                           a[:, :6, :].reshape(-1, 3), a[:, -6:, :].reshape(-1, 3)])
    mn = edge.min(axis=1)
    mx = edge.max(axis=1)
    white = (mn >= 225) & ((mx - mn) <= 16)
    return float(white.mean()) if len(edge) else 0.0


def _wide_artifact(front: Image.Image) -> dict | None:
    """检测 front 里的“宽幅高饱和装饰物”（如角色背后巨大扇子/光环/背景色块）。

    只在白底图里检测：若存在面积>2% 且宽>40%画宽、高>12%画高、顶边<45%画高、
    饱和度中位>=60 的连通块，判定为背景装饰物（IP-Adapter 从 portrait 带出的道具）。
    """
    try:
        import cv2
    except Exception:  # noqa: BLE001
        return None
    arr = np.asarray(front.convert("RGB"))
    h, w = arr.shape[:2]
    hsv = cv2.cvtColor(arr, cv2.COLOR_RGB2HSV)
    sat = hsv[:, :, 1]
    cand = ((sat >= 60) & (hsv[:, :, 2] >= 55)).astype(np.uint8)
    n, labels, stats, _ = cv2.connectedComponentsWithStats(cand, connectivity=8)
    total = float(h * w)
    for idx in range(1, n):
        area = float(stats[idx, cv2.CC_STAT_AREA])
        cw = float(stats[idx, cv2.CC_STAT_WIDTH])
        ch = float(stats[idx, cv2.CC_STAT_HEIGHT])
        top = float(stats[idx, cv2.CC_STAT_TOP])
        if area / total < 0.02 or cw / w < 0.40 or ch / h < 0.12 or top / h > 0.45:
            continue
        # 背景装饰物（扇子/光环/墨块）是“宽扁”的；盔甲/披风/皇冠是“高瘦”的，不能误杀
        if ch > 0 and cw / ch < 1.30:
            continue
        mask = labels == idx
        med_sat = float(np.median(sat[mask])) if mask.any() else 0.0
        low_sat_wide = (med_sat >= 60 and ch > 0 and cw / ch >= 1.60)
        if med_sat >= 80 or low_sat_wide:
            return {"area_ratio": round(area / total, 3),
                    "bbox": [int(stats[idx, 0]), int(top), int(cw), int(ch)],
                    "sat": round(med_sat, 1), "low_sat_wide": bool(low_sat_wide)}
    return None


def remove_background_adaptive(im: Image.Image, tol: int = 22) -> Image.Image:
    """从四边做**颜色连续性**泛洪去背景（处理非纯白渐变底，如 IP-Adapter 带出的灰底）。

    与 remove_white 的纯白泛洪互补：邻居颜色差 <= tol 才继续扩散，遇到角色线稿
    （明暗突变）即停止；适合 SDXL 生成的平滑渐变背景。
    """
    from collections import deque

    im = im.convert("RGBA")
    arr = np.array(im)
    rgb = arr[:, :, :3].astype(np.int16)
    h, w = rgb.shape[:2]
    bg = np.zeros((h, w), dtype=bool)
    q = deque()
    for x in range(w):
        for y in (0, h - 1):
            if not bg[y, x]:
                bg[y, x] = True
                q.append((y, x))
    for y in range(h):
        for x in (0, w - 1):
            if not bg[y, x]:
                bg[y, x] = True
                q.append((y, x))
    while q:
        y, x = q.popleft()
        base = rgb[y, x]
        for dy, dx in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            ny, nx = y + dy, x + dx
            if 0 <= ny < h and 0 <= nx < w and not bg[ny, nx]:
                if int(np.abs(rgb[ny, nx] - base).max()) <= tol:
                    bg[ny, nx] = True
                    q.append((ny, nx))
    arr[:, :, 3] = np.where(bg, 0, arr[:, :, 3])
    out = Image.fromarray(arr, "RGBA")
    al = out.getchannel("A").filter(ImageFilter.GaussianBlur(0.8))
    out.putalpha(al)
    out = clean_islands(out)
    bbox = out.getbbox()
    if bbox:
        pad = 8
        out = out.crop((max(0, bbox[0] - pad), max(0, bbox[1] - pad),
                        min(out.width, bbox[2] + pad), min(out.height, bbox[3] + pad)))
    return out


def remove_background_robust(im: Image.Image) -> tuple[Image.Image, dict]:
    """稳健抠图：白底泛洪 + 渐变背景泛洪 + 宽幅 wash 清理，最后质量校验。

    返回 (cutout, info)；info 记录各步骤是否命中，供 meta 追溯。
    """
    cut, wash = remove_white(im, with_report=True)
    info = {"white_flood": True, "wash": wash, "adaptive": None}
    # 若白底泛洪后仍残留大量不透明非角色区域（四角非透明/bbox 贴边），再跑颜色连续泛洪
    arr = np.array(cut)
    h, w = arr.shape[:2]
    corners = [int(arr[2, 2, 3]), int(arr[2, w - 3, 3]),
               int(arr[h - 3, 2, 3]), int(arr[h - 3, w - 3, 3])]
    if any(c > 10 for c in corners):
        cut2 = remove_background_adaptive(im.convert("RGBA"))
        cut2, wash2 = remove_wide_wash(cut2)
        a2 = np.array(cut2)
        alpha0 = float((a2[:, :, 3] == 0).mean())
        if 0.2 <= alpha0 <= 0.85:
            cut = cut2
            info["adaptive"] = {"used": True, "alpha0": round(alpha0, 3)}
            if wash2.get("removed"):
                info["wash"] = wash2
    return cut, info


# ---------------------------------------------------------------------------
# 2. imagegen 生成
# ---------------------------------------------------------------------------
def _run_imagegen(spec_path: Path, outdir: Path, mode: str, seed: int,
                  prompt_extra: str, *, style: str = "anime",
                  negative_extra: str = "", views: str | None = None,
                  ref: str | Path | None = None,
                  ip_strength: float = 0.35, ip_end_at: float = 0.90,
                  ip_weight_type: str = "style transfer",
                  timeout: float = 600.0) -> dict:
    """调用 imagegen CLI 生成（子进程隔离；ComfyUI 共享队列串行）。

    ref 非空时启用 IP-Adapter 一致性模式（README §2.1：three_view 推荐
    strength=0.35 / end_at=0.90 / weight_type="style transfer"）。
    """
    cmd = [sys.executable, "-m", "imagegen.cli", "generate",
           "--spec", str(spec_path), "--outdir", str(outdir),
           "--mode", mode, "--style", style, "--seed", str(seed)]
    if prompt_extra:
        cmd += ["--prompt-extra", prompt_extra]
    if negative_extra:
        cmd += ["--negative-extra", negative_extra]
    if views:
        cmd += ["--views", views]
    if ref:
        cmd += ["--ref", str(ref),
                "--ip-adapter-strength", str(ip_strength),
                "--ip-adapter-end-at", str(ip_end_at),
                "--ip-adapter-weight-type", ip_weight_type]
    try:
        proc = subprocess.run(cmd, cwd=str(REPO), capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        raise V5ArtError(f"imagegen 超时（{timeout}s）: {mode} seed={seed}") from exc
    out = (proc.stdout or "") + "\n" + (proc.stderr or "")
    summary = {}
    for line in reversed((proc.stdout or "").strip().splitlines()):
        line = line.strip()
        if line.startswith("{") and line.endswith("}"):
            try:
                summary = json.loads(line)
                break
            except Exception:  # noqa: BLE001
                continue
    if proc.returncode != 0 or not summary.get("ok"):
        tail = out.strip().splitlines()[-12:]
        raise V5ArtError("imagegen 失败（%s seed=%s）：%s"
                         % (mode, seed, "\n".join(tail)[-1200:]))
    return summary


def _ensure_spec(character_id: str) -> Path:
    spec_path = SPECS_DIR / f"{character_id}.json"
    if spec_path.is_file():
        return spec_path
    gen = REPO / "tmp" / "gen_v52_char_specs.py"
    if gen.is_file():
        subprocess.run([sys.executable, str(gen)], cwd=str(REPO), check=False,
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=120)
    if not spec_path.is_file():
        raise V5ArtError(f"缺少 imagegen spec: {spec_path}（先跑 tmp/gen_v52_char_specs.py）")
    return spec_path


def _spec_prompt_extra(spec: dict) -> str:
    tags = [str(t).strip() for t in (spec.get("extra_tags") or []) if str(t).strip()]
    return ", ".join(tags)


def _front_prompt(front_tags: str) -> str:
    """front prompt：剔除场景/世界观 tag（会诱发背景 ink wash），追加纯白底 tag。"""
    tags = [t.strip() for t in str(front_tags or "").split(",") if t.strip()]
    tags = [t for t in tags if t.lower() not in FRONT_SCENE_TAGS]
    return ", ".join(tags + [FRONT_EXTRA])


def _prompt_profile(character_id: str) -> dict:
    """读 contract/v5/art/prompts.json 的 characters[<id>]（统一提示词库）。

    返回 {portrait_tags, front_tags, extra_tags, negative}；缺失返回 {}（走 spec 兜底）。
    """
    data = _read_json(PROMPTS_PATH, {}) or {}
    chars = data.get("characters") or {}
    entry = chars.get(str(character_id))
    if not isinstance(entry, dict):
        return {}
    out = {}
    for key in ("portrait_tags", "front_tags", "extra_tags", "negative"):
        value = entry.get(key)
        if isinstance(value, list):
            value = ", ".join(str(t) for t in value)
        out[key] = str(value or "").strip()
    return out


# ---------------------------------------------------------------------------
# 3. YuNet 脸部检测 + 几何（嘴/眼）
# ---------------------------------------------------------------------------
def _yunet():
    global _YUNET
    if _YUNET is None:
        try:
            import cv2  # noqa: F401
        except Exception as exc:  # noqa: BLE001
            raise V5ArtError("需要 opencv-python（YuNet 脸部检测）") from exc
        if not _FACE_MODEL.is_file():
            raise V5ArtError(f"YuNet 模型缺失: {_FACE_MODEL}")
        _YUNET = cv2.FaceDetectorYN.create(str(_FACE_MODEL), "", (320, 320), 0.5, 0.3, 5000)
    return _YUNET


def _detect_face_yunet(img: Image.Image) -> dict | None:
    """YuNet 检测人脸（返回像素坐标 bbox/landmarks/score）；失败返回 None。"""
    try:
        import cv2
        arr = np.asarray(img.convert("RGB"))
        h, w = arr.shape[:2]
        if w < 32 or h < 32:
            return None
        det = _yunet()
        det.setInputSize((w, h))
        _, faces = det.detect(np.ascontiguousarray(arr[:, :, ::-1]))
    except Exception:  # noqa: BLE001
        return None
    if faces is None or not len(faces):
        return None
    cands = []
    for f in faces:
        x1, y1, bw, bh = (float(v) for v in f[:4])
        score = float(f[-1])
        if score < FACE_MIN_SCORE or bw < 12 or bh < 12:
            continue
        lms = [(float(f[4 + 2 * i]), float(f[5 + 2 * i])) for i in range(5)]
        aspect = bw / max(1e-6, bh)
        # 动漫脸接近 0.6-1.2；score 为主，比例只做小惩罚（面积不能压过置信度）
        aspect_factor = 1.0 if 0.45 <= aspect <= 1.45 else 0.45
        cands.append({"bbox": (x1, y1, x1 + bw, y1 + bh), "score": score,
                      "landmarks": lms, "_rank": score * aspect_factor})
    if not cands:
        return None
    cands.sort(key=lambda d: d["_rank"], reverse=True)
    best = cands[0]
    best.pop("_rank", None)
    best.setdefault("source", "yunet")
    return best


def detect_face(img: Image.Image) -> dict | None:
    """人脸检测兜底链：YuNet 全图 → YuNet 头部裁剪放大 → Haar 正面脸。

    返回 {bbox, score, landmarks, source}；全失败返回 None（调用方用估算几何）。
    """
    face = _detect_face_yunet(img)
    if face is not None:
        return face
    try:
        w, h = img.size
        if w >= 40 and h >= 40:
            # 头部裁剪（顶部 38%）放大 3x 再检测：全身立绘里人头相对输入太小
            crop = img.crop((0, 0, w, max(8, int(h * 0.38))))
            crop = crop.resize((crop.width * 3, crop.height * 3), Image.LANCZOS)
            face = _detect_face_yunet(crop)
            if face is not None:
                face["bbox"] = tuple(float(v) / 3.0 for v in face["bbox"])
                face["landmarks"] = [(float(x) / 3.0, float(y) / 3.0)
                                     for x, y in face.get("landmarks") or []]
                face["source"] = "yunet_headcrop"
                return face
    except Exception:  # noqa: BLE001
        pass
    face = _detect_face_haar(img)
    if face is not None:
        return face
    return None


_FACE_CASCADE = None


def _haar_cascade():
    global _FACE_CASCADE
    if _FACE_CASCADE is None:
        import cv2
        _FACE_CASCADE = cv2.CascadeClassifier(
            os.path.join(cv2.data.haarcascades, "haarcascade_frontalface_default.xml"))
    return _FACE_CASCADE


def _detect_face_haar(img: Image.Image) -> dict | None:
    """Haar 正面脸兜底（多参数扫描 + 几何 sanity），对动漫大眼脸召回更好。"""
    try:
        import cv2
        arr = np.asarray(img.convert("RGB"))
        h, w = arr.shape[:2]
        gray = cv2.cvtColor(np.ascontiguousarray(arr), cv2.COLOR_RGB2GRAY)
        faces = []
        for sf, mn in ((1.05, 3), (1.05, 4), (1.08, 4)):
            found = _haar_cascade().detectMultiScale(
                gray, scaleFactor=sf, minNeighbors=mn, minSize=(30, 30))
            if found is not None and len(found):
                faces.extend(found.tolist())
    except Exception:  # noqa: BLE001
        return None
    cands = []
    for x, y, fw, fh in faces:
        cx, cy = x + fw / 2.0, y + fh / 2.0
        # 全身立绘脸只可能在画面上部；尺寸/比例合理性过滤
        if cy > h * 0.38 or fh < h * 0.04 or fh > h * 0.32:
            continue
        if not (0.6 <= fw / max(1.0, fh) <= 1.5):
            continue
        if not (0.06 * w <= fw <= 0.40 * w):
            continue
        cands.append({"bbox": (float(x), float(y), float(x + fw), float(y + fh)),
                      "score": 0.45, "landmarks": [], "estimated": False,
                      "source": "haar", "_rank": fw * fh})
    if not cands:
        return None
    cands.sort(key=lambda d: d["_rank"], reverse=True)
    best = cands[0]
    best.pop("_rank", None)
    return best

def _estimate_face(img: Image.Image, kind: str = "cutout") -> dict:
    """检测全失败时的兜底几何（按 AI 立绘常见构图比例 / 顶部不透明重心定 x）。"""
    w, h = img.size
    if kind == "portrait":
        x1, y1, x2, y2 = w * 0.30, h * 0.18, w * 0.70, h * 0.52
    else:
        # 全身 cutout：头在上部 9%-30%；用顶部 32% 不透明像素重心修正水平位置
        cx = w * 0.5
        try:
            arr = np.array(img.convert("RGBA"))
            top = arr[: max(8, int(h * 0.32)), :, 3] > 16
            ys, xs = np.where(top)
            if len(xs):
                cx = float(np.median(xs))
        except Exception:  # noqa: BLE001
            pass
        face_w = w * 0.30
        x1, y1 = cx - face_w / 2.0, h * 0.09
        x2, y2 = cx + face_w / 2.0, h * 0.30
    cy = (y1 + y2) / 2
    ex = (x2 - x1) * 0.22
    eye_y = y1 + (y2 - y1) * 0.42
    mouth_y = y1 + (y2 - y1) * 0.78
    return {"bbox": (x1, y1, x2, y2), "score": 0.0, "estimated": True,
            "source": "estimate",
            "landmarks": [(x1 + (x2 - x1) * 0.30, eye_y),
                          (x1 + (x2 - x1) * 0.70, eye_y),
                          ((x1 + x2) / 2, cy),
                          ((x1 + x2) / 2 - ex * 0.5, mouth_y),
                          ((x1 + x2) / 2 + ex * 0.5, mouth_y)]}


def _sample_region(arr: np.ndarray, cx: float, cy: float, r: int = 3) -> tuple:
    h, w = arr.shape[:2]
    x0 = max(0, int(cx) - r)
    x1 = min(w, int(cx) + r + 1)
    y0 = max(0, int(cy) - r)
    y1 = min(h, int(cy) + r + 1)
    patch = arr[y0:y1, x0:x1, :3].reshape(-1, 3).astype(np.float32)
    if not len(patch):
        return (240, 216, 192)
    return tuple(int(round(v)) for v in np.median(patch, axis=0))


def face_geometry(img: Image.Image, kind: str = "cutout") -> dict:
    """脸部几何（像素坐标）：bbox + 嘴/眼（按 Lead 规则从 face box 比例推导）。

    - 嘴中心 = face box 水平中心、垂直 0.76（0.72-0.80）；
    - 嘴宽 ≈ face_w * 0.26；眼 y = face box 0.42 高度，双眼在中心 ±0.22*face_w；
    - 颜色从图像采样（皮肤/唇色/线稿色），供 overlay 使用。
    """
    face = detect_face(img) or _estimate_face(img, kind)
    arr = np.array(img.convert("RGB")).astype(np.int16)
    h, w = arr.shape[:2]
    x1, y1, x2, y2 = face["bbox"]
    x1, y1 = max(0.0, min(w - 4, x1)), max(0.0, min(h - 4, y1))
    x2, y2 = max(x1 + 8.0, min(float(w), x2)), max(y1 + 8.0, min(float(h), y2))
    fw, fh = x2 - x1, y2 - y1
    cx = (x1 + x2) / 2.0
    mouth = {"cx": cx, "cy": y1 + fh * 0.78,
             "w": max(6.0, fw * 0.34), "h": max(3.0, fw * 0.34 * 0.45)}
    eyes = {"y": y1 + fh * 0.42, "w": max(5.0, fw * 0.22),
            "cx": [cx - fw * 0.24, cx + fw * 0.24]}
    # 皮肤色：脸颊两侧 + 嘴上方采样，剔除过暗像素（线稿/眼睫）
    samples = []
    for fx, fy in ((0.30, 0.58), (0.70, 0.58), (0.30, 0.86), (0.70, 0.86), (0.5, 0.55)):
        sx, sy = x1 + fw * fx, y1 + fh * fy
        samples.append(_sample_region(arr, sx, sy, 3))
    bright = [s for s in samples if sum(s) / 3.0 > 70] or samples
    skin = tuple(int(round(sum(c[i] for c in bright) / len(bright))) for i in range(3))
    # 唇色：嘴部小区域中比皮肤更暗的像素
    mcx, mcy = mouth["cx"], mouth["cy"]
    r = max(2, int(round(mouth["w"] * 0.5)))
    y0m, y1m = max(0, int(mcy - r)), min(h, int(mcy + r) + 1)
    x0m, x1m = max(0, int(mcx - r * 1.5)), min(w, int(mcx + r * 1.5) + 1)
    lip = (112, 52, 56)
    patch = arr[y0m:y1m, x0m:x1m, :3].reshape(-1, 3).astype(np.float32)
    if len(patch):
        luma = patch @ np.array([0.299, 0.587, 0.114], dtype=np.float32)
        skin_luma = 0.299 * skin[0] + 0.587 * skin[1] + 0.114 * skin[2]
        dark = patch[luma < skin_luma * 0.85]
        if len(dark) >= 3:
            lip = tuple(int(v) for v in dark.mean(axis=0))
            # 向动漫唇色靠拢（避免采样到胡须/阴影变成棕色）
            lip = _mix(lip, (150, 62, 64), 0.45)
            lip = tuple(max(58, int(v)) for v in lip)
    # 线稿色：脸框内最暗 4% 像素
    patch2 = arr[int(y1):int(y2), int(x1):int(x2), :3]
    line = (26, 22, 20)
    if patch2.size:
        flat = patch2.reshape(-1, 3).astype(np.float32)
        luma = flat @ np.array([0.299, 0.587, 0.114], dtype=np.float32)
        idx = np.argsort(luma)[:max(1, len(luma) // 25)]
        line = tuple(int(v) for v in flat[idx].mean(axis=0))
    return {
        "bbox": [round(float(v), 1) for v in (x1, y1, x2, y2)],
        "score": round(float(face.get("score") or 0.0), 3),
        "estimated": bool(face.get("estimated")),
        "source": face.get("source") or ("estimate" if face.get("estimated") else "yunet"),
        "size": [w, h],
        "face_w": round(float(fw), 2),
        "mouth": {"cx": round(mcx, 2), "cy": round(mcy, 2),
                  "w": round(mouth["w"], 2), "h": round(mouth["h"], 2)},
        "eyes": {"y": round(eyes["y"], 2), "w": round(eyes["w"], 2),
                 "cx": [round(c, 2) for c in eyes["cx"]]},
        "skin": _hex(skin),
        "lip": _hex(lip),
        "line": _hex(line),
    }


def _norm_geom(geom: dict) -> dict:
    """像素几何 → 归一化（0..1，随裁图缩放稳定）。"""
    w, h = geom.get("size") or [1, 1]
    w = float(w) or 1.0
    h = float(h) or 1.0
    return {
        "score": geom.get("score", 0.0),
        "estimated": bool(geom.get("estimated")),
        "source": geom.get("source") or ("estimate" if geom.get("estimated") else "yunet"),
        "bbox": [geom["bbox"][0] / w, geom["bbox"][1] / h,
                 geom["bbox"][2] / w, geom["bbox"][3] / h],
        "mouth": {"cx": geom["mouth"]["cx"] / w, "cy": geom["mouth"]["cy"] / h,
                  "w": geom["mouth"]["w"] / w, "h": geom["mouth"]["h"] / h},
        "eyes": {"y": geom["eyes"]["y"] / h, "w": geom["eyes"]["w"] / w,
                 "cx": [c / w for c in geom["eyes"]["cx"]]},
        "skin": geom.get("skin"),
        "lip": geom.get("lip"),
        "line": geom.get("line"),
        "face_w": geom.get("face_w"),
    }


def _denorm_geom(norm: dict, size: tuple[int, int]) -> dict:
    w, h = float(size[0]), float(size[1])
    return {
        "size": [w, h],
        "score": norm.get("score", 0.0),
        "estimated": bool(norm.get("estimated")),
        "source": norm.get("source") or ("estimate" if norm.get("estimated") else "yunet"),
        "bbox": [norm["bbox"][0] * w, norm["bbox"][1] * h,
                 norm["bbox"][2] * w, norm["bbox"][3] * h],
        "mouth": {"cx": norm["mouth"]["cx"] * w, "cy": norm["mouth"]["cy"] * h,
                  "w": norm["mouth"]["w"] * w, "h": norm["mouth"]["h"] * h},
        "eyes": {"y": norm["eyes"]["y"] * h, "w": norm["eyes"]["w"] * w,
                 "cx": [c * w for c in norm["eyes"]["cx"]]},
        "skin": norm.get("skin"), "line": norm.get("line"),
        "lip": norm.get("lip"),
        "face_w": norm.get("face_w"),
    }


# ---------------------------------------------------------------------------
# 4. 口型 / 眨眼 overlay（动漫风）
# ---------------------------------------------------------------------------
def _viseme_shape(draw: ImageDraw.ImageDraw, mc, mw: float, viseme: str,
                  fill, line) -> None:
    """在 mouth center 处按 viseme 画一个动漫风嘴型（尺寸以嘴宽 mw 为准）。"""
    cx, cy = mc
    w = max(6.0, float(mw))
    lw = max(1, int(round(w * 0.10)))
    v = str(viseme or "closed").lower()
    if v == "closed":
        draw.arc((cx - w * 0.50, cy - w * 0.22, cx + w * 0.50, cy + w * 0.30),
                 20, 160, fill=line, width=lw)
    elif v == "a":
        box = (cx - w * 0.34, cy - w * 0.26, cx + w * 0.34, cy + w * 0.55)
        draw.ellipse(box, fill=fill, outline=line, width=lw)
        draw.ellipse((box[0] + w * 0.10, box[1] + w * 0.32,
                      box[2] - w * 0.10, box[3] - w * 0.04),
                     fill=_mix(fill[:3], (230, 110, 115), 0.5) + (235,))
    elif v == "o":
        r = w * 0.26
        draw.ellipse((cx - r, cy - r * 0.9, cx + r, cy + r * 1.15),
                     fill=fill, outline=line, width=lw)
    elif v == "e":
        box = (cx - w * 0.50, cy - w * 0.14, cx + w * 0.50, cy + w * 0.26)
        draw.ellipse(box, fill=fill, outline=line, width=lw)
        draw.line([(box[0] + w * 0.08, cy + w * 0.03),
                   (box[2] - w * 0.08, cy + w * 0.03)],
                  fill=_mix(line[:3], (255, 255, 255), 0.30) + (220,),
                  width=max(1, lw // 2))
    elif v == "i":
        box = (cx - w * 0.18, cy - w * 0.18, cx + w * 0.18, cy + w * 0.44)
        draw.ellipse(box, fill=fill, outline=line, width=lw)
    elif v == "u":
        box = (cx - w * 0.23, cy - w * 0.06, cx + w * 0.23, cy + w * 0.40)
        draw.ellipse(box, fill=fill, outline=line, width=lw)
        draw.line([(cx - w * 0.10, cy + w * 0.10), (cx + w * 0.10, cy + w * 0.10)],
                  fill=_mix(fill[:3], (255, 200, 200), 0.5) + (215,),
                  width=max(1, lw // 2))
    elif v == "m":
        draw.line([(cx - w * 0.48, cy + w * 0.12), (cx - w * 0.24, cy - w * 0.03),
                   (cx, cy + w * 0.12), (cx + w * 0.24, cy - w * 0.03),
                   (cx + w * 0.48, cy + w * 0.12)], fill=line, width=lw,
                  joint="curve")
        draw.line([(cx - w * 0.30, cy - w * 0.30), (cx + w * 0.30, cy - w * 0.30)],
                  fill=_mix(line[:3], (255, 255, 255), 0.25) + (200,),
                  width=max(1, lw // 2))
    else:  # 未知 → 闭合线
        draw.line([(cx - w * 0.35, cy), (cx + w * 0.35, cy)], fill=line, width=lw)


def apply_mouth(img: Image.Image, viseme: str,
                *, geom: dict | None = None, strength: float = 1.0,
                face_w: float | None = None) -> Image.Image:
    """在动漫立绘上叠加口型（肤色局部柔化原嘴 + 半透明唇色嘴型，羽化 1-2px）。

    渲染后脸宽 < OVERLAY_MIN_FACE_W(40px) 时不叠（宽景肉眼不可见，避免噪点）。
    """
    out = img.convert("RGBA").copy()
    g = geom or face_geometry(out, "cutout")
    if face_w is None:
        face_w = float(g.get("face_w") or (g["bbox"][2] - g["bbox"][0]))
    if face_w < OVERLAY_MIN_FACE_W:
        return out
    m = g["mouth"]
    mw = float(m["w"])
    cx, cy = float(m["cx"]), float(m["cy"])
    skin = _rgb(g.get("skin"), (240, 216, 192))
    lip = _rgb(g.get("lip"), (112, 52, 56))
    line = _rgb(g.get("line"), (26, 22, 20))
    # 1) 极小范围肤色柔化（只盖住原嘴，不画大白块；alpha 低避免色块感）
    erase_w, erase_h = mw * 0.50, mw * 0.24
    layer = Image.new("RGBA", out.size, (0, 0, 0, 0))
    ImageDraw.Draw(layer).ellipse((cx - erase_w, cy - erase_h, cx + erase_w, cy + erase_h),
                                  fill=skin + (int(45 * max(0.3, strength)),))
    layer = layer.filter(ImageFilter.GaussianBlur(max(0.8, mw * 0.06)))
    out.alpha_composite(layer)
    # 2) 嘴型（半透明唇色 + 线稿；中景可见但不抢脸）
    layer2 = Image.new("RGBA", out.size, (0, 0, 0, 0))
    _viseme_shape(ImageDraw.Draw(layer2), (cx, cy), mw, viseme,
                  lip + (int(195 * max(0.3, strength)),),
                  line + (int(205 * max(0.3, strength)),))
    layer2 = layer2.filter(ImageFilter.GaussianBlur(max(0.5, mw * 0.03)))
    out.alpha_composite(layer2)
    return out


def apply_blink(img: Image.Image, *, geom: dict | None = None,
                strength: float = 1.0, closed: bool = True,
                face_w: float | None = None) -> Image.Image:
    """眨眼：肤色眼睑厚弧线 + 轻阴影 + 细眼线（不用白色椭圆）。"""
    out = img.convert("RGBA").copy()
    g = geom or face_geometry(out, "cutout")
    if face_w is None:
        face_w = float(g.get("face_w") or (g["bbox"][2] - g["bbox"][0]))
    if face_w < OVERLAY_MIN_FACE_W or not closed:
        return out
    eyes = g["eyes"]
    ew = float(eyes["w"])
    ey = float(eyes["y"])
    skin = _rgb(g.get("skin"), (240, 216, 192))
    line = _rgb(g.get("line"), (26, 22, 20))
    lid = _mix(skin, line, 0.38)
    shadow = _mix(skin, line, 0.55)
    layer = Image.new("RGBA", out.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    for cx in eyes["cx"]:
        cx = float(cx)
        # 上眼睑肤色覆盖（闭眼时遮住眼白/瞳孔）+ 深肤色眼睑线 + 轻微阴影
        d.ellipse((cx - ew * 0.60, ey - ew * 0.34, cx + ew * 0.60, ey + ew * 0.16),
                  fill=skin + (int(210 * max(0.3, strength)),))
        d.line([(cx - ew * 0.62, ey - ew * 0.02), (cx - ew * 0.18, ey + ew * 0.24),
                (cx + ew * 0.18, ey + ew * 0.23), (cx + ew * 0.62, ey - ew * 0.05)],
               fill=lid + (int(215 * max(0.3, strength)),),
               width=max(2, int(round(ew * 0.32))), joint="curve")
        d.line([(cx - ew * 0.50, ey + ew * 0.24), (cx + ew * 0.50, ey + ew * 0.22)],
               fill=shadow + (int(95 * max(0.3, strength)),),
               width=max(1, int(round(ew * 0.10))))
    layer = layer.filter(ImageFilter.GaussianBlur(max(0.7, ew * 0.06)))
    out.alpha_composite(layer)
    layer2 = Image.new("RGBA", out.size, (0, 0, 0, 0))
    d2 = ImageDraw.Draw(layer2)
    for cx in eyes["cx"]:
        cx = float(cx)
        d2.line([(cx - ew * 0.58, ey), (cx - ew * 0.16, ey + ew * 0.14),
                 (cx + ew * 0.16, ey + ew * 0.13), (cx + ew * 0.58, ey - ew * 0.03)],
                fill=line + (int(240 * max(0.3, strength)),),
                width=max(1, int(round(ew * 0.06))), joint="curve")
    layer2 = layer2.filter(ImageFilter.GaussianBlur(0.5))
    out.alpha_composite(layer2)
    return out


# ---------------------------------------------------------------------------
# 5. 资产加载 / 生成
# ---------------------------------------------------------------------------
def _asset_paths(character_id: str, art: dict | None = None) -> dict:
    base = ASSETS_DIR / str(character_id)
    paths = {
        "dir": base,
        "portrait": base / "portrait.png",
        "front": base / "front.png",
        "cutout": base / "cutout.png",
        "meta": base / "meta.json",
    }
    if isinstance(art, dict):
        for key in ("portrait", "front", "cutout", "meta"):
            value = art.get(key)
            if value:
                p = Path(value)
                paths[key] = p if p.is_absolute() else (REPO / p)
    return paths


def _art_face(paths: dict, meta: dict | None) -> dict | None:
    """取 cutout 的归一化脸几何（meta 里有就用，没有就现算并缓存）。"""
    cutout = paths.get("cutout")
    if not cutout or not Path(cutout).is_file():
        return None
    try:
        mtime = Path(cutout).stat().st_mtime
    except OSError:
        mtime = 0
    key = (str(Path(cutout)), round(mtime, 3))
    hit = _FACE_CACHE.get(key)
    if hit is not None:
        return hit
    try:
        im = Image.open(cutout).convert("RGBA")
        norm = (meta or {}).get("face_norm")
        if isinstance(norm, dict) and norm.get("mouth"):
            geom = dict(norm)
        else:
            geom = _norm_geom(face_geometry(im, "cutout"))
    except Exception as exc:  # noqa: BLE001
        raise V5ArtError(f"cutout 读取/脸部定位失败: {cutout}（{exc}）") from exc
    _FACE_CACHE[key] = geom
    return geom


def character_art(character_id: str, art: dict | None = None) -> dict:
    """角色美术资产 dict（不存在时 exists=False；不抛错，调用方可回退程序化）。"""
    cid = str(character_id or "")
    paths = _asset_paths(cid, art)
    cutout = paths["cutout"]
    meta = _read_json(paths["meta"], {}) or {}
    info = {
        "id": cid,
        "exists": bool(Path(cutout).is_file()),
        "dir": str(paths["dir"]),
        "portrait": str(paths["portrait"]) if Path(paths["portrait"]).is_file() else None,
        "front": str(paths["front"]) if Path(paths["front"]).is_file() else None,
        "cutout": str(cutout) if Path(cutout).is_file() else None,
        "meta": meta,
        "source": meta.get("source") or meta.get("mode") or "",
        "size": None,
        "face_norm": None,
    }
    if info["exists"]:
        try:
            with Image.open(cutout) as im:
                info["size"] = list(im.size)
                info["face_norm"] = _art_face(paths, meta)
        except Exception as exc:  # noqa: BLE001
            info["exists"] = False
            info["error"] = str(exc)
    _ART_CACHE[cid] = info
    return info


def _corner_white(im: Image.Image, patch: int = 10) -> dict:
    """检测四角是否接近纯白（RGB 均值 >=238、色差 <=12）→ front 白底是否干净。"""
    a = np.array(im.convert("RGB"))
    h, w = a.shape[:2]
    corners = []
    for cy, cx in ((0, 0), (0, w - 1), (h - 1, 0), (h - 1, w - 1)):
        y0, y1 = (0, min(h, patch)) if cy == 0 else (max(0, h - patch), h)
        x0, x1 = (0, min(w, patch)) if cx == 0 else (max(0, w - patch), w)
        block = a[y0:y1, x0:x1, :].reshape(-1, 3).astype(np.float32)
        mean = block.mean(axis=0) if len(block) else np.array([0, 0, 0])
        spread = float(block.max() - block.min()) if len(block) else 999.0
        corners.append({"rgb": [int(round(v)) for v in mean],
                        "mean": round(float(mean.mean()), 1),
                        "spread": round(spread, 1)})
    ok = all(c["mean"] >= WHITE_CORNER and c["spread"] <= 12 for c in corners)
    return {"ok": ok, "corners": corners}


def _white_ratio(im: Image.Image) -> float:
    """整图近白像素比例（>0.42 说明背景基本是白底，可进入抠图）。"""
    a = np.array(im.convert("RGB")).astype(np.int16)
    mn = a.min(axis=2)
    mx = a.max(axis=2)
    white = (mn >= WHITE_CORNER) & ((mx - mn) <= 12)
    return round(float(white.mean()), 3)


def _white_report(im: Image.Image) -> dict:
    rep = _corner_white(im)
    rep["white_ratio"] = _white_ratio(im)
    rep["ok"] = bool(rep["ok"] and rep["white_ratio"] >= 0.42)
    return rep


def _validate_cutout(cutout: Image.Image) -> dict:
    """cutout 质量校验：alpha0 0.3-0.6 / 四角 alpha=0 / bbox 高度 >=55% 且不贴满边。"""
    arr = np.array(cutout.convert("RGBA"))
    alpha = arr[:, :, 3]
    h, w = alpha.shape[:2]
    alpha0 = float((alpha == 0).mean())
    visible = float((alpha > 16).mean())
    corners = [int(alpha[2, 2]), int(alpha[2, w - 3]),
               int(alpha[h - 3, 2]), int(alpha[h - 3, w - 3])]
    bbox = cutout.getbbox()
    ratio = float(w) / max(1, h)
    lo, hi = CUTOUT_ALPHA0_RANGE
    bbox_h_ratio = ((bbox[3] - bbox[1]) / h) if bbox else 0.0
    ok = (lo <= alpha0 <= hi and all(c <= 10 for c in corners)
          and bbox is not None and bbox_h_ratio >= CUTOUT_MIN_BBOX_H
          and 0.18 <= ratio <= 2.2)
    return {"ok": bool(ok), "alpha0": round(alpha0, 3),
            "visible": round(visible, 3), "corners_alpha": corners,
            "bbox": list(bbox) if bbox else None, "aspect": round(ratio, 3),
            "bbox_h_ratio": round(bbox_h_ratio, 3)}


def generate_character_art(character_id: str, outdir: str | Path | None = None,
                           *, style: str = "anime", seed: int = 42,
                           force: bool = False, prompt_extra: str | None = None,
                           front_retries: int = 3, log=print) -> dict:
    """生成/补齐一个角色的 AI 美术资产（portrait + front → cutout + meta）。

    - 提示词统一读 `contract/v5/art/prompts.json` 的 characters 段，front 追加
      ``FRONT_EXTRA``（纯白底/全身/无影），negative 追加 ``FRONT_NEG_EXTRA``；
    - front 生成后校验四角 RGB≥238/色差≤12 且全图近白比例≥0.42，不白则换 seed 重试
      最多 ``front_retries`` 次；
    - 抠图后校验 alpha0 0.3-0.6 / 四角 alpha=0 / bbox 高度≥55% 画面；不达标重试 1 次，
      仍不达标则写 meta(status=invalid) 并抛 V5ArtError（不产出坏 cutout）；
    - 已存在 cutout.png 且非 force → 直接返回（复用最早 3 角色 laozhou/ali/dawei）。
    """
    cid = str(character_id or "").strip()
    if not cid:
        raise V5ArtError("character_id 不能为空")
    base = Path(outdir) if outdir else ASSETS_DIR / cid
    base = base if base.is_absolute() else (REPO / base)
    cutout_path = base / "cutout.png"
    if cutout_path.is_file() and not force:
        _log(log, f"[skip] {cid}: 已有 cutout.png（非 force，复用现有资产）")
        return character_art(cid)

    spec_path = _ensure_spec(cid)
    spec = _read_json(spec_path, {}) or {}
    profile = _prompt_profile(cid)
    spec_extra = prompt_extra if prompt_extra is not None else _spec_prompt_extra(spec)
    portrait_tags = profile.get("portrait_tags") or spec_extra
    raw_front = profile.get("front_tags") or (
        f"{spec_extra}, full body, front view, standing, arms at sides, "
        f"simple background, white background, plain background")
    front_tags = _front_prompt(raw_front)
    negative = profile.get("negative") or ""
    front_negative = ", ".join(t for t in (negative, FRONT_NEG_EXTRA) if t)
    base.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    portrait = base / "portrait.png"
    front = base / "front.png"
    three_front = base / "three_view" / "front.png"
    meta_path = base / "meta.json"

    # ---- portrait（角色卡，允许带背景）----
    if force or not portrait.is_file():
        _run_imagegen(spec_path, base, "portrait", seed, portrait_tags,
                      style=style, negative_extra=negative)
        _log(log, f"[gen] {cid}: portrait {portrait.stat().st_size // 1024}KB")
    else:
        _log(log, f"[skip] {cid}: portrait 已存在")

    # ---- front（纯白底全身，供抠图）----
    front_white = None
    attempts = 0
    if front.is_file() and not force:
        try:
            front_white = _white_report(Image.open(front))
        except Exception:  # noqa: BLE001
            front_white = {"ok": False, "corners": [], "white_ratio": 0.0}
        if not front_white["ok"]:
            _log(log, f"[regen] {cid}: 已有 front 背景不干净，重新生成")
    while front_white is None or not front_white["ok"]:
        attempts += 1
        if attempts > front_retries:
            _write_json(meta_path, {
                "id": cid, "source": "imagegen", "status": "invalid",
                "created_at": _now(), "seed": seed,
                "error": "front 非纯白底（四角/白底比例不达标）",
                "front_white": front_white,
                "prompts": {"front_tags": front_tags, "negative": front_negative},
            })
            raise V5ArtError(
                f"{cid}: front 生成 {attempts - 1} 次仍非纯白底"
                f"（white_ratio={(front_white or {}).get('white_ratio')}，"
                f"corners={(front_white or {}).get('corners')}）")
        s = seed + 1 + 2 * (attempts - 1)
        _run_imagegen(spec_path, base, "three_view", s, front_tags,
                      style=style, negative_extra=front_negative, views="front")
        if not three_front.is_file():
            raise V5ArtError(f"{cid}: imagegen 未产出 three_view/front.png")
        Image.open(three_front).save(front)
        front_white = _white_report(Image.open(front))
        _log(log, f"[front] {cid}: seed={s} white_ok={front_white['ok']} "
                  f"ratio={front_white.get('white_ratio')}")

    # ---- 抠图 + 质量校验（不达标重试 1 次；仍不达标 → 不产出坏资产）----
    cutout, wash = remove_white(Image.open(front), with_report=True)
    validation = _validate_cutout(cutout)
    validation["wash"] = wash
    retried = 0
    while not validation["ok"] and retried < 1:
        retried += 1
        s = seed + 9 + retried
        _run_imagegen(spec_path, base, "three_view", s, front_tags,
                      style=style, negative_extra=front_negative, views="front")
        if three_front.is_file():
            Image.open(three_front).save(front)
            white2 = _white_report(Image.open(front))
            if not white2["ok"]:
                continue
            front_white = white2
        cutout, wash = remove_white(Image.open(front), with_report=True)
        validation = _validate_cutout(cutout)
        validation["wash"] = wash
        _log(log, f"[retry] {cid}: cutout 校验重试 seed={s} ok={validation['ok']}")
    if not validation["ok"]:
        _write_json(meta_path, {
            "id": cid, "source": "imagegen", "status": "invalid",
            "created_at": _now(), "seed": seed,
            "error": "cutout 质量校验不达标（alpha0/bbox/四角）",
            "cutout_validation": validation, "front_white": front_white,
            "prompts": {"front_tags": front_tags, "negative": front_negative},
        })
        if cutout_path.is_file():
            cutout_path.unlink()
        raise V5ArtError(f"{cid}: cutout 质量校验不达标: {validation}")

    cutout.save(cutout_path)
    alpha_ratio = float((np.array(cutout)[:, :, 3] > 0).mean())
    status = "ok"
    geo = face_geometry(cutout, "cutout")
    portrait_geo = None
    if portrait.is_file():
        try:
            portrait_geo = _norm_geom(face_geometry(Image.open(portrait), "portrait"))
        except Exception:  # noqa: BLE001
            portrait_geo = None
    meta = {
        "id": cid,
        "name": spec.get("name"),
        "source": "imagegen",
        "model": "animagine-xl-4.0.safetensors",
        "style": style,
        "seed": seed,
        "created_at": _now(),
        "elapsed_sec": round(time.time() - t0, 1),
        "created_by": "pipeline/v5_art.generate_character_art",
        "status": status,
        "prompt_source": "contract/v5/art/prompts.json#characters" if profile else "spec.extra_tags",
        "files": {"portrait": str(portrait) if portrait.is_file() else None,
                  "front": str(front), "cutout": str(cutout_path)},
        "sizes": {"portrait": list(Image.open(portrait).size) if portrait.is_file() else None,
                  "cutout": list(cutout.size)},
        "alpha_ratio": round(alpha_ratio, 3),
        "front_white": front_white,
        "front_attempts": attempts,
        "cutout_validation": validation,
        "face_norm": _norm_geom(geo),
        "face_portrait_norm": portrait_geo,
        "prompts": {"portrait_tags": portrait_tags, "front_tags": front_tags,
                    "negative": front_negative},
    }
    old = _read_json(meta_path, {}) or {}
    merged = dict(old)
    merged.update(meta)
    _write_json(meta_path, merged)
    _ART_CACHE.pop(cid, None)
    _FACE_CACHE.clear()
    _log(log, f"[done] {cid}: cutout {cutout.size} alpha0={validation['alpha0']:.3f} "
              f"status={status} face={'yunet' if (geo.get('score') or 0) >= FACE_MIN_SCORE else 'estimate'} "
              f"{round(time.time() - t0, 1)}s")
    return character_art(cid)


def _make_white_reference(portrait: str | Path, dest: str | Path) -> Path | None:
    """把 portrait 去背景后贴到白底，作为 IP-Adapter 的“干净参考图”。

    解决 portrait 背景里的圆形/色块被 front 复制的问题（如 xueba 的浅蓝圆）。
    失败返回 None（调用方继续用原 portrait）。
    """
    try:
        im = Image.open(portrait).convert("RGBA")
        if _white_ratio(im) >= 0.45:
            return Path(portrait)
        cut, _info = remove_background_robust(im)
        arr = np.array(cut)
        visible = float((arr[:, :, 3] > 16).mean())
        if cut.getbbox() is None or not (0.06 <= visible <= 0.65):
            return None
        W, H = im.size
        canvas = Image.new("RGBA", (W, H), (255, 255, 255, 255))
        pw, ph = cut.size
        scale = min(W * 0.90 / max(1, pw), H * 0.95 / max(1, ph))
        sprite = cut.resize((max(1, int(pw * scale)), max(1, int(ph * scale))),
                            Image.LANCZOS)
        canvas.alpha_composite(sprite, ((W - sprite.width) // 2,
                                        (H - sprite.height) // 2))
        dest = Path(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        canvas.convert("RGB").save(dest)
        return dest
    except Exception:  # noqa: BLE001
        return None


def generate_character_front(character_id: str, outdir: str | Path | None = None,
                             *, ref: str | Path | None = None, seed: int = 43,
                             force: bool = True, prompt_extra: str | None = None,
                             style: str = "anime", front_retries: int = 3,
                             write_meta: bool = True, log=print) -> dict:
    """用 portrait 作 IP-Adapter 参考，重生成 front（白底全身）→ cutout + meta。

    目标：front 与 portrait 同画风/同服装细节（解决 SDXL 全身图"扁平简笔画化"）。
    - ref 默认 assets/v5/characters/<id>/portrait.png；
    - README §0.3 推荐：weight 0.35 / end_at 0.90 / weight_type "style transfer"；
    - 白底/四角/alpha0/bbox 校验与 generate_character_art 相同；不达标重试/抛错。
    """
    cid = str(character_id or "").strip()
    if not cid:
        raise V5ArtError("character_id 不能为空")
    base = Path(outdir) if outdir else ASSETS_DIR / cid
    base = base if base.is_absolute() else (REPO / base)
    ref_path = Path(ref) if ref else (ASSETS_DIR / cid / "portrait.png")
    ref_path = ref_path if ref_path.is_absolute() else (REPO / ref_path)
    if not ref_path.is_file():
        raise V5ArtError(f"{cid}: 缺少 portrait 参考图 {ref_path}")
    cutout_path = base / "cutout.png"
    if cutout_path.is_file() and not force:
        _log(log, f"[skip] {cid}: 已有 cutout.png（非 force）")
        return character_art(cid)

    spec_path = _ensure_spec(cid)
    spec = _read_json(spec_path, {}) or {}
    profile = _prompt_profile(cid)
    spec_extra = prompt_extra if prompt_extra is not None else _spec_prompt_extra(spec)
    raw_front = profile.get("front_tags") or (
        f"{spec_extra}, full body, front view, standing, arms at sides, "
        f"simple background, white background, plain background")
    front_tags = f"{_front_prompt(raw_front)}, {FRONT_REF_PROMPT}"
    negative = profile.get("negative") or ""
    front_negative = ", ".join(t for t in (negative, FRONT_NEG_EXTRA, FRONT_REF_NEGATIVE) if t)
    base.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    front = base / "front.png"
    three_front = base / "three_view" / "front.png"
    meta_path = base / "meta.json"
    tries = 0
    chosen = None
    errors: list[str] = []
    while chosen is None:
        tries += 1
        if tries > max(1, int(front_retries)):
            raise V5ArtError(f"{cid}: front-ref 生成 {tries - 1} 次仍不达标: {errors[-2:]}")
        strength, end_at = FRONT_REF_SCHEDULE[min(tries - 1, len(FRONT_REF_SCHEDULE) - 1)]
        s = seed + 2 * (tries - 1)
        ref_for_try = ref_path
        if tries >= 2:
            white_ref = _make_white_reference(ref_path, base / "ref_white.png")
            if white_ref is not None:
                ref_for_try = white_ref
        _run_imagegen(spec_path, base, "three_view", s, front_tags, style=style,
                      negative_extra=front_negative, views="front",
                      ref=str(ref_for_try), ip_strength=strength, ip_end_at=end_at,
                      ip_weight_type="style transfer")
        if not three_front.is_file():
            raise V5ArtError(f"{cid}: imagegen 未产出 three_view/front.png")
        front_img = Image.open(three_front)
        front_img.save(front)
        white = _white_report(front_img)
        artifact = _wide_artifact(front_img)
        _log(log, f"[front-ref] {cid}: seed={s} ip={strength:.2f}/{end_at:.2f} "
                  f"white_ok={white['ok']} ratio={white.get('white_ratio')} "
                  f"artifact={'yes' if artifact else 'no'}")
        if artifact is not None:
            errors.append(f"seed={s} 装饰物={artifact}")
            continue
        if white["ok"]:
            cutout, wash = remove_white(front_img, with_report=True)
            validation = _validate_cutout(cutout)
            validation["wash"] = wash
            mode = "white"
        else:
            cutout, info = remove_background_robust(front_img)
            validation = _validate_cutout(cutout)
            validation["wash"] = info.get("wash")
            validation["adaptive"] = info.get("adaptive")
            mode = "adaptive"
        if validation["ok"]:
            chosen = {"front": front_img, "cutout": cutout, "white": white,
                      "validation": validation, "mode": mode,
                      "ip": {"strength": strength, "end_at": end_at,
                             "weight_type": "style transfer"}, "seed": s,
                      "ref_used": str(ref_for_try)}
        else:
            errors.append(f"seed={s} white_ratio={white.get('white_ratio')} "
                          f"validation={validation}")
    front_img = chosen["front"]
    cutout = chosen["cutout"]
    front_white = chosen["white"]
    validation = chosen["validation"]
    wash = validation.get("wash") or {}
    bg_mode = chosen["mode"]
    ip_used = chosen["ip"]
    seed_used = chosen["seed"]
    cutout.save(cutout_path)
    geo = face_geometry(cutout, "cutout")
    meta = {
        "id": cid,
        "source": "imagegen_ref",
        "model": "animagine-xl-4.0.safetensors",
        "style": style,
        "seed": seed_used,
        "seed_base": seed,
        "attempts": tries,
        "created_at": _now(),
        "elapsed_sec": round(time.time() - t0, 1),
        "created_by": "pipeline/v5_art.generate_character_front",
        "status": "ok",
        "portrait_ref": str(ref_path),
        "ref_used": chosen.get("ref_used"),
        "ip_adapter": ip_used,
        "bg_mode": bg_mode,
        "files": {"front": str(front), "cutout": str(cutout_path)},
        "sizes": {"cutout": list(cutout.size)},
        "front_white": front_white,
        "cutout_validation": validation,
        "face_norm": _norm_geom(geo),
        "prompts": {"front_tags": front_tags, "negative": front_negative},
    }
    if write_meta:
        old = _read_json(meta_path, {}) or {}
        merged = dict(old)
        merged.update(meta)
        _write_json(meta_path, merged)
    _ART_CACHE.pop(cid, None)
    _FACE_CACHE.clear()
    result = {"id": cid, "exists": cutout_path.is_file(),
              "dir": str(base), "portrait": str(ref_path),
              "front": str(front), "cutout": str(cutout_path),
              "size": list(cutout.size), "meta": meta,
              "face_norm": _norm_geom(geo)}
    _log(log, f"[done] {cid}: front-ref cutout {cutout.size} "
              f"alpha0={validation['alpha0']:.3f} {round(time.time() - t0, 1)}s")
    return result


# ---------------------------------------------------------------------------
# 6. 渲染（供 characters_v5 调用）
# ---------------------------------------------------------------------------
def _load_style_finish():
    try:
        from . import style_v5
        return style_v5.apply_finish
    except Exception:  # noqa: BLE001
        return None


def _fit_cutout(cutout: Image.Image, size: tuple[int, int],
                max_h: float = 0.985, max_w: float = 0.985) -> dict:
    """把 cutout 等比缩放贴到透明画布（底部对齐、水平对齐人物重心）。

    - **优先按高度**缩放（保证角色在画面里高度正确，符合 motion2d 0.62 身高假设）；
    - 若按高度缩放后过宽（如持扇张开的角色），按 alpha 重心水平裁掉两侧，
      只保留人物主体；
    - 返回 {canvas, scale, dx, dy, crop_x}：cutout 像素 p →
      canvas = (dx + (p.x - crop_x) * scale, dy + p.y * scale)。
    """
    W, H = int(size[0]), int(size[1])
    cw, ch = cutout.size
    # 先尝试“整张塞进画布”；宽高都允许时直接缩放到画布内（底部+水平居中）
    scale = min(H * max_h / max(1, ch), W * max_w / max(1, cw))
    crop_x = 0.0
    src = cutout
    if cw * scale > W * max_w:
        # 宽幅姿态（持扇/披风）：按高度缩放，按 alpha 重心水平裁两侧
        scale = H * max_h / max(1, ch)
        try:
            arr = np.asarray(cutout.convert("RGBA"))
            weight = arr[:, :, 3].astype(np.float32)
            col = weight.sum(axis=0)
            total = float(col.sum()) or 1.0
            centroid = float((col * np.arange(len(col))).sum() / total)
        except Exception:  # noqa: BLE001
            centroid = cw / 2.0
        crop_w = min(float(cw), W / scale)
        crop_x = min(max(0.0, centroid - crop_w / 2.0), max(0.0, cw - crop_w))
        x0 = int(round(crop_x))
        x1 = int(round(min(float(cw), crop_x + crop_w)))
        if x1 - x0 < 4:
            x0, x1 = 0, cw
            crop_x = 0.0
        src = cutout.crop((x0, 0, x1, ch))
        crop_x = float(x0)
    sprite = src.resize((max(1, int(round(src.width * scale))),
                         max(1, int(round(src.height * scale)))), Image.LANCZOS)
    canvas = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    dx = (W - sprite.width) / 2.0
    dy = H - sprite.height
    canvas.alpha_composite(sprite, (int(round(dx)), int(round(dy))))
    return {"canvas": canvas, "scale": scale, "dx": dx, "dy": float(dy),
            "crop_x": crop_x}


def _face_detected(norm: dict | None) -> bool:
    """脸框是否来自实际检测（YuNet/Haar/头部裁剪放大），而非纯估算。"""
    if not isinstance(norm, dict):
        return False
    src = str(norm.get("source") or "")
    return (float(norm.get("score") or 0.0) >= FACE_MIN_SCORE
            or src in ("haar", "yunet_headcrop"))


def _skin_like(hex_color) -> bool:
    """估算几何的肤色合理性（r>g>b 且偏暖），避免把口型画到衣服/胸甲上。"""
    try:
        r, g, b = _rgb(hex_color, (0, 0, 0))
    except Exception:  # noqa: BLE001
        return False
    luma = 0.299 * r + 0.587 * g + 0.114 * b
    return (r >= g >= b) and (r - b) >= 18 and 60 <= luma <= 252


def _overlay_geom_ok(norm: dict | None) -> bool:
    """overlay 几何是否可信：检测成功直接可信；估算时要求肤色合理。"""
    if _face_detected(norm):
        return True
    return _skin_like((norm or {}).get("skin"))


def _char_overlay_allowed(char_spec: dict) -> bool:
    """机器/蒙面/兜帽类角色不叠口型/眨眼（估算几何不可靠且本来无可见嘴）。"""
    app = (char_spec or {}).get("appearance") or {}
    hair = str(app.get("hair_style") or "").lower()
    acc = {str(a).lower() for a in (app.get("accessories") or [])}
    if hair in ("robot_antenna", "hood", "helmet", "mask", "veil"):
        return False
    if acc & {"mask", "led_panel", "visor", "helmet", "face_mask"}:
        return False
    if "robot" in str((char_spec or {}).get("id") or "").lower():
        return False
    return True


def render_art_character(char, style, size=(512, 768), pose: str = "front",
                         mouth: str = "closed", blink: bool = False) -> Image.Image | None:
    """AI 立绘渲染（characters_v5.render_character 的 art 分支）。

    返回与 size 同尺寸的 RGBA；角色没有 AI 资产时返回 None（调用方回退程序化）。
    """
    try:
        char_spec = char if isinstance(char, dict) else {"id": str(char)}
        cid = str(char_spec.get("id") or "")
        if not cid:
            return None
        art = character_art(cid, char_spec.get("art") if isinstance(char_spec.get("art"), dict) else None)
        if not art.get("exists"):
            return None
        cutout = Image.open(art["cutout"]).convert("RGBA")
        W, H = int(size[0]), int(size[1])
        if W < 8 or H < 8:
            return None
        fitted = _fit_cutout(cutout, (W, H))
        canvas = fitted["canvas"]
        scale, dx, dy, crop_x = (fitted["scale"], fitted["dx"], fitted["dy"],
                                 fitted["crop_x"])
        cw, ch = cutout.size
        # 脸几何：归一化 → 贴图后像素（考虑水平裁切 crop_x）
        norm = art.get("face_norm") or {}
        if norm:
            def _mx(nx: float) -> float:
                return dx + (nx * cw - crop_x) * scale

            def _my(ny: float) -> float:
                return dy + ny * ch * scale

            geom = {
                "mouth": {"cx": _mx(norm["mouth"]["cx"]), "cy": _my(norm["mouth"]["cy"]),
                          "w": norm["mouth"]["w"] * cw * scale,
                          "h": norm["mouth"]["h"] * ch * scale},
                "eyes": {"y": _my(norm["eyes"]["y"]),
                         "w": norm["eyes"]["w"] * cw * scale,
                         "cx": [_mx(c) for c in norm["eyes"]["cx"]]},
                "skin": norm.get("skin"), "line": norm.get("line"),
            }
        else:
            geom = face_geometry(canvas, "cutout")
        mouth = str(mouth or "closed").lower()
        if mouth not in MOUTH_STATES:
            mouth = "closed"
        face_w_px = None
        if norm:
            face_w_px = (norm["bbox"][2] - norm["bbox"][0]) * cw * scale
        overlay_allowed = (_char_overlay_allowed(char_spec)
                           and _overlay_geom_ok(norm))
        if overlay_allowed and mouth != "closed":
            canvas = apply_mouth(canvas, mouth, geom=geom, face_w=face_w_px)
        if overlay_allowed and blink:
            canvas = apply_blink(canvas, geom=geom, face_w=face_w_px)
        # 风格化（角色版 finish；保留原 alpha，避免背景/边缘被填色）
        finish = _load_style_finish()
        if finish is not None and isinstance(style, dict) and style:
            alpha = canvas.getchannel("A")
            try:
                styled = finish(canvas, style, "character")
                if styled.size == canvas.size:
                    styled = styled.convert("RGBA")
                    styled.putalpha(alpha)
                    canvas = styled
            except Exception:  # noqa: BLE001  风格化失败不阻断
                pass
        return canvas
    except Exception:  # noqa: BLE001  art 失败 → 让调用方回退程序化
        return None


# ---------------------------------------------------------------------------
# 7. 自检 / CLI（可选）
# ---------------------------------------------------------------------------
def audit(character_ids: list[str] | None = None) -> dict:
    """扫描资产完整性（24 角色 / cutout+portrait+meta）。"""
    ids = character_ids
    if not ids:
        ids = sorted(p.stem for p in CONTRACT_CHARS.glob("*.json"))
    rows = []
    for cid in ids:
        art = character_art(cid)
        rows.append({"id": cid, "exists": art["exists"],
                     "cutout": bool(art["cutout"]),
                     "portrait": bool(art["portrait"]),
                     "size": art["size"], "source": art["source"],
                     "face": bool(art["face_norm"]),
                     "face_score": round(float((art["face_norm"] or {}).get("score") or 0), 3)})
    ok = all(r["exists"] for r in rows)
    return {"ok": ok, "total": len(rows),
            "missing": [r["id"] for r in rows if not r["exists"]],
            "estimated_face": [r["id"] for r in rows if r["exists"] and not r["face_score"]],
            "rows": rows}


def clear_cache() -> None:
    _ART_CACHE.clear()
    _FACE_CACHE.clear()


if __name__ == "__main__":  # pragma: no cover - 手工自检入口
    import argparse

    ap = argparse.ArgumentParser(description="V5.2 角色美术自检")
    ap.add_argument("--audit", action="store_true")
    ap.add_argument("--generate", default=None, help="生成指定角色 id（逗号分隔）")
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()
    if args.generate:
        for cid in [c.strip() for c in args.generate.split(",") if c.strip()]:
            print(json.dumps(generate_character_art(cid, force=args.force),
                             ensure_ascii=False)[:600])
    else:
        print(json.dumps(audit(), ensure_ascii=False, indent=2))
