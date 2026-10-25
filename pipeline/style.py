"""风格系统：按模板选择 2D / 3D 动漫风格（contract/style_map.json + docs/STYLE_GUIDE.md）。

- 3d_anime（默认综艺/舞台类）：场景 plate 用 outputs/scene_plates/{plate}_3d.png，
  角色 cutout 用 outputs/characters/{id}/cutout_3d/front.png；H3 prompt 注入 3D 影感前缀。
- 2d_anime（历史/文化类）：使用 2D plate + cutout_2d；prompt 注入手绘 2D 前缀。
- 资产缺失时自动回退 2D，并在 meta/verify 里记录 fallback。
"""
from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path

from . import config

FFMPEG = str(config.FFMPEG_BIN)
_ALPHA_CACHE: dict[str, float | None] = {}


def _corner_alpha(path: Path) -> float | None:
    """左上角 8x8 的 alpha 平均值：用于检测 cutout 是否真透明（纯白底也未抠干净）。"""
    key = str(path)
    if key in _ALPHA_CACHE:
        return _ALPHA_CACHE[key]
    try:
        p = subprocess.run([FFMPEG, "-v", "error", "-i", str(path), "-vf",
                            "alphaextract,crop=8:8:0:0,signalstats,"
                            "metadata=print:key=lavfi.signalstats.YAVG",
                            "-frames:v", "1", "-f", "null", "NUL"],
                           capture_output=True, text=True, errors="replace")
        m = re.search(r"YAVG=([0-9.]+)", p.stderr or "")
        val = float(m.group(1)) if m else None
    except Exception:  # noqa: BLE001
        val = None
    _ALPHA_CACHE[key] = val
    return val

REPO = config.REPO_ROOT
STYLE_MAP_PATH = REPO / "contract" / "style_map.json"
PLATE_DIR = REPO / "outputs" / "scene_plates"
CHARS_DIR = REPO / "outputs" / "characters"

DEFAULT_STYLE = "3d_anime"

# 场景英文描述（注入 H3 prompt，让动态镜头知道环境）
SCENE_DESC = {
    "late_night_desk": "a late-night talk-show studio with a wooden desk, city window, warm practical lamps and papers on the floor",
    "history_study": "a candle-lit ancient Chinese library with tall wooden bookshelves, wooden floor and warm candlelight",
    "roundtable_studio": "a modern roundtable TV studio with tiered audience seating, dark acoustic panels and ceiling lights",
    "taskmaster_stage": "a colorful taskmaster stage with trophy props, striped stage floor and theatrical side curtains",
    "debate_hall": "a debate hall with a large podium, warm spotlights and an audience area in the background",
    "mystery_room": "a dim investigation room with evidence boards, scattered papers, a desk lamp and cold practical light",
    "treasure_museum": "a grand museum gallery with golden artifacts, stone columns, glass display cases and dramatic spotlights",
    "kitchen_warm": "a warm rustic kitchen with copper pots, wooden shelves, a stove and soft window light",
    "stage_music": "a concert stage with colorful spotlights, stage floor, speakers and atmospheric haze",
    "talk_show_sofa": "a cozy talk-show set with a sofa, coffee table, warm lamps and a studio backdrop",
    "alley_night": "a neon-lit narrow city alley at night with wet pavement, signage and practical lights",
    "classroom_history": "a vintage classroom with wooden desks, a chalkboard and afternoon window light",
    "3d_roundtable": "a stylized 3D roundtable studio with a circular table, glowing screens and volumetric light",
    "3d_stage": "a stylized 3D concert stage with towering speaker stacks, colored beams and reflective floor",
    "3d_study": "a stylized 3D candle-lit study with layered bookshelves and warm volumetric light",
}


def load_style_map(path=None) -> dict:
    p = Path(path) if path else STYLE_MAP_PATH
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _template_keys(template_id: str) -> list[str]:
    t = str(template_id or "").strip()
    if not t:
        return []
    keys = [t]
    for suffix in ("_5min", "_demo", "_scene", "_3d"):
        if t.endswith(suffix):
            keys.append(t[: -len(suffix)])
    if t.startswith("demo_"):
        keys.append(t[5:])
    return keys


def resolve(template_id: str, override: str | None = None,
            style_map: dict | None = None) -> tuple[str, dict]:
    """返回 (style_name, style_dict)。override 优先；否则按模板映射；默认 3d_anime。"""
    smap = style_map if style_map is not None else load_style_map()
    styles = smap.get("styles") or {}
    if override:
        name = str(override)
        return name, dict(styles.get(name) or {"label": name, "prompt_prefix": "",
                                               "plate_suffix": ""})
    templates = smap.get("templates") or {}
    for key in _template_keys(template_id):
        name = templates.get(key)
        if name and name in styles:
            return name, dict(styles[name])
    default = next((n for n, s in styles.items() if s.get("is_default")), DEFAULT_STYLE)
    return default, dict(styles.get(default) or {"label": default, "prompt_prefix": "",
                                                 "plate_suffix": ""})


def style_prefix(style_name: str, style_map: dict | None = None) -> str:
    _, style = resolve("", override=style_name, style_map=style_map)
    return str(style.get("prompt_prefix") or "")


def scene_description(plate: str) -> str:
    key = str(plate or "").replace("_3d", "")
    return SCENE_DESC.get(key, SCENE_DESC.get(str(plate), "a cinematic anime studio scene"))


def plate_path(plate: str, style_name: str, style: dict | None = None,
               plate_dir: Path | None = None) -> tuple[Path, str, bool]:
    """返回 (plate_path, used_style, fallback_2d)。3d_anime 优先 {plate}_3d.png。"""
    base_dir = Path(plate_dir) if plate_dir else PLATE_DIR
    name = str(plate or "").replace("_3d", "")
    suffix = ""
    if style is None:
        _, style = resolve("", override=style_name)
    if style_name == "3d_anime":
        suffix = str(style.get("plate_suffix") or "_3d")
    p3 = base_dir / f"{name}{suffix}.png" if suffix else None
    if p3 is not None and p3.is_file():
        return p3, "3d_anime", False
    base = base_dir / f"{name}.png"
    if base.is_file():
        return base, ("2d_anime" if style_name != "2d_anime" else style_name), (style_name != "2d_anime")
    # 兜底：任选一个存在的 plate
    for alt in ("late_night_desk", "history_study", "roundtable_studio"):
        p = base_dir / f"{alt}.png"
        if p.is_file():
            return p, "2d_anime", True
    return base, style_name, True


def cutout_path(char_id: str, style_name: str = "3d_anime",
                chars_dir: Path | None = None) -> tuple[Path | None, str, bool]:
    """返回 (cutout_path, used_style, fallback_2d)。3d_anime 优先 cutout_3d/front.png。"""
    base = Path(chars_dir) if chars_dir else CHARS_DIR
    if style_name == "3d_anime" and not os.environ.get("PIPELINE_SKIP_3D_CUTOUT"):
        p = base / char_id / "cutout_3d" / "front.png"
        if p.is_file() and p.stat().st_size > 1000:
            alpha = _corner_alpha(p)
            # 左上角 alpha 高 = 背景没抠干净（不是透明 cutout），回退 2D 全身
            if alpha is None or alpha <= 32:
                return p, "3d_anime", False
    for rel in ("cutout_2d/front.png", "cutout_3d/front.png",
                "2d/portrait.png", "portrait.png"):
        p = base / char_id / rel
        if p.is_file() and p.stat().st_size > 1000:
            return p, "2d_fallback", (style_name == "3d_anime")
    return None, "missing", True
