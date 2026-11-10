# -*- coding: utf-8 -*-
"""程序化场景底板（plate）生成器 —— 不需要 GPU / SDXL / ComfyUI。

背景：`outputs/scene_plates/` 的 AI 绘景在某次清理后全部丢失，导致
`pipeline/scenes.py:380` 直接 raise，V5 渲染栈与 16 个 v3 模板全部起不来。
这个脚本用确定性程序绘图生成**代用底板**，让两条栈立刻能跑；每张图在
`procedural.json` 里标记 `procedural: true`，将来用 `scripts/gen_scene_plates.py`
（SDXL）重生成真绘景时会被覆盖，不会被误当成 AI 作品。

命名规则（两套命名空间都要覆盖）：
- v3 模板：`contract/templates_v3/*.json` 的 `stage.plate`，形如 `<scene>__<style>`（16 个）
- v5 模板：`contract/scene_map.json` 的 `plate`，形如 `<scene>`（14 个）

用法：
    python scripts/gen_scene_plates_procedural.py            # 只补缺失
    python scripts/gen_scene_plates_procedural.py --force     # 全部重画
    python scripts/gen_scene_plates_procedural.py --out DIR   # 换输出目录（默认 outputs/scene_plates）
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

W, H = 1280, 720

#: 后缀风格 → (天空/背景主色, 地面主色, 光源色, 对比强度)
STYLE_PALETTE = {
    "ink":        ((238, 228, 208), (206, 192, 168), (255, 244, 222), 0.55),
    "debateink":  ((226, 228, 230), (188, 192, 198), (246, 248, 252), 0.50),
    "cinematic":  ((14, 26, 44), (26, 40, 58), (120, 200, 255), 0.85),
    "noir":       ((30, 32, 36), (18, 20, 24), (230, 232, 238), 0.90),
    "neon":       ((18, 12, 34), (36, 16, 50), (255, 90, 200), 0.88),
    "theatre":    ((46, 12, 16), (30, 10, 12), (255, 196, 120), 0.80),
    "vhs":        ((44, 40, 52), (30, 28, 36), (200, 214, 255), 0.55),
    "game":       ((34, 46, 70), (52, 64, 92), (255, 224, 130), 0.70),
    "gold":       ((28, 22, 14), (44, 34, 20), (255, 206, 110), 0.85),
    "clay":       ((232, 206, 176), (198, 168, 138), (255, 236, 208), 0.45),
    "warmtalk":   ((38, 28, 24), (56, 40, 32), (255, 186, 120), 0.75),
    "roundtable": ((40, 32, 28), (58, 44, 36), (255, 214, 150), 0.70),
    "base":       ((36, 40, 52), (52, 58, 70), (210, 226, 255), 0.60),
}

#: 场景关键词 → 家具/结构提示（决定画什么几何体）
SCENE_HINTS = {
    "desk": "desk", "sofa": "sofa", "studio": "studio", "stage": "stage",
    "hall": "hall", "room": "room", "museum": "museum", "kitchen": "counter",
    "castle": "hall", "pitch": "room", "study": "bookshelf", "store": "shelves",
    "shop": "shelves", "street": "street", "cafe": "table", "office": "desk",
    "assembly": "bench", "undercity": "street", "council": "hall",
}


def _seed(name: str) -> int:
    return int(hashlib.sha1(name.encode("utf-8")).hexdigest()[:8], 16)


def _vgradient(top: tuple, bottom: tuple) -> Image.Image:
    t = np.array(top, dtype=np.float32)
    b = np.array(bottom, dtype=np.float32)
    ramp = np.linspace(0.0, 1.0, H, dtype=np.float32)[:, None, None]
    arr = (t[None, None, :] * (1.0 - ramp) + b[None, None, :] * ramp)
    arr = np.repeat(arr, W, axis=1)
    return Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8))


def _add_light(img: Image.Image, color: tuple, strength: float, cx: float, cy: float,
               radius: float) -> Image.Image:
    """加一团软光（径向衰减）。"""
    yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)
    d = np.sqrt(((xx - cx * W) / (radius * W)) ** 2 + ((yy - cy * H) / (radius * H)) ** 2)
    falloff = np.clip(1.0 - d, 0.0, 1.0) ** 2
    base = np.asarray(img, dtype=np.float32)
    lit = base + falloff[:, :, None] * np.array(color, dtype=np.float32) * strength
    return Image.fromarray(np.clip(lit, 0, 255).astype(np.uint8))


def _vignette(img: Image.Image, strength: float = 0.45) -> Image.Image:
    yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)
    nx = (xx / W - 0.5) * 2.0
    ny = (yy / H - 0.5) * 2.0
    r = np.sqrt(nx * nx + ny * ny) / 1.4142
    mask = np.clip(1.0 - strength * (r ** 2), 0.0, 1.0)
    base = np.asarray(img, dtype=np.float32)
    return Image.fromarray(np.clip(base * mask[:, :, None], 0, 255).astype(np.uint8))


def _grain(img: Image.Image, rng: np.random.Generator, amount: float = 5.0) -> Image.Image:
    base = np.asarray(img, dtype=np.float32)
    noise = rng.normal(0.0, amount, base.shape)
    return Image.fromarray(np.clip(base + noise, 0, 255).astype(np.uint8))


def _level(img: Image.Image, target_mean: float = 96.0, floor: int = 26) -> Image.Image:
    """把整体亮度拉到可用区间。

    底板是给 H3 / 2D 合成当背景用的，太暗会撞 QC 的 blackdetect 门（要求 0 个黑场），
    所以这里做一次温和的亮度归一：抬到 target_mean，并保证最低通道不低于 floor。
    """
    arr = np.asarray(img, dtype=np.float32)
    mean = float(arr.mean())
    if mean <= 1.0:
        return img
    gain = max(0.75, min(2.2, target_mean / mean))
    arr = np.maximum(arr * gain, float(floor))
    return Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8))


def _silhouette(draw: ImageDraw.ImageDraw, kind: str, palette: dict, rng: np.random.Generator) -> None:
    """画家具/结构剪影。全是几何体，不画人、不画字。"""
    dark = tuple(int(c * 0.42) for c in palette[1])
    mid = tuple(int(c * 0.62) for c in palette[1])
    horizon = int(H * 0.62)
    # 地面
    draw.rectangle([0, horizon, W, H], fill=dark)
    # 远景墙线
    draw.rectangle([0, 0, W, horizon - 2], outline=mid, width=2)

    if kind in ("desk", "counter", "bench", "table"):
        w = int(W * 0.52)
        x = (W - w) // 2
        draw.rectangle([x, horizon - 70, x + w, horizon + 10], fill=mid)
        draw.rectangle([x + int(w * 0.12), horizon - 150, x + int(w * 0.30), horizon - 70], fill=mid)  # 桌上物件
        draw.rectangle([x + int(w * 0.62), horizon - 120, x + int(w * 0.70), horizon - 70], fill=mid)
    elif kind in ("sofa",):
        w = int(W * 0.60)
        x = (W - w) // 2
        draw.rectangle([x, horizon - 120, x + w, horizon + 10], fill=mid)
        draw.rectangle([x, horizon - 190, x + w, horizon - 120], fill=mid)
    elif kind in ("bookshelf", "shelves"):
        for i in range(5):
            x = int(W * 0.06) + i * int(W * 0.075)
            draw.rectangle([x, int(H * 0.12), x + int(W * 0.055), horizon], fill=mid)
        draw.rectangle([int(W * 0.5), horizon - 90, int(W * 0.5) + int(W * 0.30), horizon + 10], fill=mid)
    elif kind in ("stage",):
        draw.rectangle([int(W * 0.08), int(H * 0.18), int(W * 0.92), horizon], fill=dark)
        for i in range(2):  # 台口幕布褶
            x = int(W * 0.08) + i * int(W * 0.08)
            draw.rectangle([x, int(H * 0.10), x + int(W * 0.04), horizon], fill=mid)
        cx = W // 2
        draw.ellipse([cx - 90, horizon - 210, cx + 90, horizon - 40], outline=mid, width=6)
    elif kind in ("hall", "room"):
        for i in range(4):  # 柱/展柜
            x = int(W * 0.12) + i * int(W * 0.20)
            draw.rectangle([x, int(H * 0.16), x + int(W * 0.055), horizon], fill=mid)
        draw.rectangle([0, horizon - 20, W, horizon + 6], fill=mid)
    elif kind in ("museum",):
        for i in range(3):
            x = int(W * 0.18) + i * int(W * 0.24)
            draw.rectangle([x, horizon - 240, x + int(W * 0.14), horizon], outline=mid, width=5)
    elif kind in ("studio",):
        draw.rectangle([0, horizon - 12, W, horizon + 8], fill=mid)
        for i in range(6):  # 灯架
            x = int(W * 0.05) + i * int(W * 0.17)
            draw.line([x, 0, x, int(H * 0.22)], fill=mid, width=3)
            draw.ellipse([x - 26, int(H * 0.22), x + 26, int(H * 0.22) + 40], fill=mid)
    else:  # 通用：几块体块
        for _ in range(4):
            x = int(rng.uniform(0.06, 0.72) * W)
            w = int(rng.uniform(0.06, 0.18) * W)
            y = int(rng.uniform(0.30, 0.56) * H)
            draw.rectangle([x, y, x + w, horizon + 10], fill=mid)


def build_plate(name: str, force: bool = False) -> dict:
    """生成一张 1280x720 代用底板，返回 manifest 条目。"""
    base_name, _, suffix = name.partition("__")
    style_key = suffix if suffix in STYLE_PALETTE else "base"
    top, bottom, light, contrast = STYLE_PALETTE[style_key]
    hint = "base"
    for key, val in SCENE_HINTS.items():
        if key in base_name:
            hint = val
            break

    seed = _seed(name)
    rng = np.random.default_rng(seed)
    img = _vgradient(top, bottom)
    draw = ImageDraw.Draw(img)
    _silhouette(draw, hint, STYLE_PALETTE[style_key], rng)
    img = img.filter(ImageFilter.GaussianBlur(0.6))
    img = _add_light(img, light, contrast, cx=float(rng.uniform(0.30, 0.70)),
                     cy=float(rng.uniform(0.20, 0.34)), radius=float(rng.uniform(0.42, 0.62)))
    img = _add_light(img, light, contrast * 0.55, cx=float(rng.uniform(0.10, 0.90)),
                     cy=float(rng.uniform(0.06, 0.14)), radius=0.30)
    img = _vignette(img, strength=0.35 + 0.35 * contrast)
    img = _grain(img, rng, amount=4.5)
    img = _level(img)
    return {"name": name, "style": style_key, "hint": hint, "seed": seed, "image": img}


def collect_targets() -> list[str]:
    """汇总两套命名空间需要的底板名。"""
    names: list[str] = []
    tpl3 = REPO / "contract" / "templates_v3"
    for path in sorted(tpl3.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            continue
        plate = str((data.get("stage") or {}).get("plate") or "").strip()
        if plate:
            names.append(plate)
    scene_map = REPO / "contract" / "scene_map.json"
    if scene_map.is_file():
        for _tid, info in (json.loads(scene_map.read_text(encoding="utf-8")) or {}).items():
            plate = str((info or {}).get("plate") or "").strip()
            if plate:
                names.append(plate)
    seen, out = set(), []
    for n in names:
        if n not in seen:
            seen.add(n)
            out.append(n)
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="程序化生成场景底板（无 AI 依赖）")
    ap.add_argument("--out", default=str(REPO / "outputs" / "scene_plates"))
    ap.add_argument("--force", action="store_true", help="覆盖已存在的 png（默认只补缺失）")
    args = ap.parse_args(argv)

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = out_dir / "procedural.json"
    manifest = {}
    if manifest_path.is_file():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            manifest = {}

    targets = collect_targets()
    made, skipped = [], []
    for name in targets:
        png = out_dir / f"{name}.png"
        if png.is_file() and not args.force:
            skipped.append(name)
            continue
        item = build_plate(name, force=args.force)
        item["image"].save(png, "PNG", optimize=True)
        manifest[name] = {
            "file": png.name, "procedural": True, "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "style": item["style"], "hint": item["hint"], "seed": item["seed"],
            "size": [W, H],
            "note": "程序化代用底板：非 AI 绘景；可用 scripts/gen_scene_plates.py 覆盖为真绘景",
        }
        made.append(name)

    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({"ok": True, "out": str(out_dir), "targets": len(targets),
                      "made": made, "skipped": skipped}, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
