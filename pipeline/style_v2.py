"""风格系统 v2：模板级风格 Profile（contract/style_presets.json）。

- 16 模板 → preset（12 种）+ 模板级 overrides；
- 资产：plate 优先 outputs/scene_plates/{scene}{plate_suffix}.png（如 stage_music__neon.png），
  cutout 按 character_style 选 cutout_3d/cutout_2d，缺失回退并记录；
- 渲染：调色（palette ffmpeg 滤镜）、转场（preset.transition → render 参数）、包装（tag/accent/bars/scanlines）；
- prompt_prefix 供 H3 动态镜头注入（本轮静态 demo 也记录在 verify）。
"""
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

from . import config

REPO = config.REPO_ROOT
PRESETS_PATH = REPO / "contract" / "style_presets.json"
PLATE_DIR = REPO / "outputs" / "scene_plates"
CHARS_DIR = REPO / "outputs" / "characters"
FFMPEG = str(config.FFMPEG_BIN)
_ALPHA_CACHE: dict[str, float | None] = {}


def load_presets(path=None) -> dict:
    p = Path(path) if path else PRESETS_PATH
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _template_key(template_id: str) -> str:
    t = str(template_id or "").strip()
    if not t:
        return ""
    for suffix in ("_5min", "_demo", "_scene"):
        if t.endswith(suffix):
            return t[: -len(suffix)] + suffix
    return t


def _find_mapping(mapping: dict, template_id: str):
    t = str(template_id or "")
    if t in mapping:
        return mapping[t]
    for suffix in ("_5min", "_demo", "_scene"):
        if t.endswith(suffix) and (t[: -len(suffix)] + suffix) in mapping:
            return mapping[t[: -len(suffix)] + suffix]
        if (t + suffix) in mapping:
            return mapping[t + suffix]
    return None


def profile_for(template_id: str, override: str | None = None,
                presets: dict | None = None) -> dict:
    """返回完整 profile：preset 字段 + template 覆盖 + template/preset 标识。"""
    data = presets if presets is not None else load_presets()
    all_presets = data.get("presets") or {}
    templates = data.get("templates") or {}
    overrides = data.get("template_overrides") or {}
    tkey = _template_key(template_id)
    preset_name = None
    if override and override != "auto":
        if override in all_presets:
            preset_name = override
        else:
            override_preset = _find_mapping(templates, override)
            if override_preset in all_presets:
                preset_name = override_preset
    if not preset_name:
        mapped = _find_mapping(templates, tkey) or _find_mapping(templates, template_id)
        preset_name = mapped if mapped in all_presets else next(iter(all_presets), "cinematic_3d")
    p = dict(all_presets.get(preset_name) or {})
    ov = _find_mapping(overrides, tkey) or _find_mapping(overrides, template_id) or {}
    p.update({k: v for k, v in (ov or {}).items() if v not in (None, "")})
    p["preset"] = preset_name
    p["template"] = _find_mapping(templates, tkey) and tkey or template_id
    p["override"] = override or ""
    return p


def render_style(profile: dict) -> str:
    cs = str((profile or {}).get("character_style") or "cutout_2d")
    return "3d_anime" if "3d" in cs else "2d_anime"


def _corner_alpha(path: Path) -> float | None:
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


def plate_path(profile: dict, scene_plate: str,
               plate_dir: Path | None = None) -> tuple[Path, str, bool]:
    """返回 (plate_path, level, fallback)。
    level: style_v2 / style_v1 / base；fallback=True 表示没有专属风格 plate。
    """
    base_dir = Path(plate_dir) if plate_dir else PLATE_DIR
    name = str(scene_plate or "").replace("_3d", "")
    suffix = str((profile or {}).get("plate_suffix") or "")
    candidates = []
    if suffix:
        candidates.append((f"{name}{suffix}.png", "style_v2"))
    candidates.append((f"{name}_3d.png", "style_v1_3d"))
    candidates.append((f"{name}.png", "base"))
    for fname, level in candidates:
        p = base_dir / fname
        if p.is_file():
            return p, level, level != "style_v2"
    for alt in ("late_night_desk", "history_study", "roundtable_studio"):
        p = base_dir / f"{alt}.png"
        if p.is_file():
            return p, "base-fallback", True
    return base_dir / f"{name}.png", "missing", True


def cutout_path(profile: dict, char_id: str,
                chars_dir: Path | None = None) -> tuple[Path | None, str, bool]:
    """返回 (cutout, used_style, fallback)。优先 profile.character_style 指定变体。"""
    base = Path(chars_dir) if chars_dir else CHARS_DIR
    import os
    style = str((profile or {}).get("character_style") or "cutout_2d")
    if os.environ.get("STYLE_V2_FORCE_2D") == "1":
        style = "cutout_2d"
    order = [style, "cutout_3d", "cutout_2d",
             "cutout/portrait.png", "2d/portrait.png", "portrait.png"]
    seen = set()
    for item in order:
        if item in seen:
            continue
        seen.add(item)
        rel = item if "/" in item or item.endswith(".png") else f"{item}/front.png"
        p = base / char_id / rel
        if not (p.is_file() and p.stat().st_size > 1000):
            continue
        if "cutout_3d" in rel:
            alpha = _corner_alpha(p)
            if alpha is not None and alpha > 32:
                continue
        used = rel.split("/")[0] if "/" in rel else rel
        fallback = not (used == style)
        return p, used, fallback
    return None, "missing", True


PALETTE_FILTERS = {
    "warm_teal": "colorbalance=rs=0.06:gs=-0.02:bs=-0.06:rm=0.03:bm=0.04,"
                 "eq=contrast=1.06:saturation=0.95:gamma=0.98",
    "neon_magenta_cyan": "eq=contrast=1.12:saturation=1.35:brightness=0.02,"
                         "colorbalance=rs=0.10:bs=0.12:gs=-0.04",
    "candy_highsat": "eq=contrast=1.10:saturation=1.60:brightness=0.03",
    "dark_amber_teal": "eq=contrast=1.15:brightness=-0.05:saturation=0.90,"
                       "colorbalance=rs=0.08:bs=0.05",
    "theatre_red_gold": "colorbalance=rs=0.12:gs=0.02:bs=-0.08,"
                        "eq=contrast=1.12:saturation=1.15",
    "warm_amber": "colorbalance=rs=0.06:gs=0.03:bs=-0.05,"
                  "eq=saturation=1.02:brightness=0.015",
    "pastel_earth": "eq=saturation=0.85:brightness=0.03:contrast=0.97,"
                    "colorbalance=rs=0.03:bs=-0.02",
    "noir_blue_amber": "hue=s=0.25,eq=contrast=1.25:brightness=-0.04,"
                       "colorbalance=bs=0.12:rs=0.03",
    "vhs_warm": "eq=contrast=1.05:saturation=0.90:brightness=0.03,"
                "colorbalance=rs=0.10:bs=-0.08",
    "ink_amber": "hue=s=0.50,eq=contrast=1.08:brightness=0.02,"
                 "colorbalance=rs=0.05:bs=-0.04",
    "ink_red_gold": "hue=s=0.70,colorbalance=rs=0.10:gs=0.02:bs=-0.05,"
                    "eq=contrast=1.10",
    "ink_cold_red": "hue=s=0.55,colorbalance=bs=0.08:rs=0.06,"
                    "eq=contrast=1.10:brightness=-0.02",
    "ink_sepia": "colorchannelmixer=.393:.769:.189:0:.349:.686:.168:0:"
                 ".272:.534:.131:0,eq=contrast=1.02:saturation=0.80",
    "gold_jade": "eq=saturation=1.20:contrast=1.08,"
                 "colorbalance=rs=0.06:gs=0.03:bs=-0.03",
    "cold_blue_gold": "colorbalance=bs=0.10:rs=-0.03,"
                      "eq=contrast=1.10:saturation=1.00",
}

PACKAGING = {
    "cinematic_3d": {"tag": "CINEMATIC 3D", "color": "0xE8C46A", "bars": "letterbox"},
    "neon_stage_3d": {"tag": "NEON STAGE", "color": "0xFF3FD8", "bars": "thin"},
    "game_show_3d": {"tag": "GAME SHOW", "color": "0xFFD400", "bars": "thin"},
    "roundtable_3d": {"tag": "ROUNDTABLE", "color": "0x7FD4E8", "bars": "thin"},
    "sketch_3d": {"tag": "THEATRE", "color": "0xFF6B6B", "bars": "curtain"},
    "warm_talk_3d": {"tag": "WARM TALK", "color": "0xF2B06B", "bars": "thin"},
    "clay_life_3d": {"tag": "CLAY STOP-MOTION", "color": "0xC9A27A", "bars": "thin"},
    "noir_mystery_3d": {"tag": "NOIR MYSTERY", "color": "0x9FB8D8", "bars": "letterbox"},
    "retro_standup_2d": {"tag": "VHS 1985", "color": "0xF5D36A", "bars": "scanlines"},
    "history_ink_2d": {"tag": "INK HISTORY", "color": "0xD8B36A", "bars": "thin"},
    "debate_ink_2d": {"tag": "INK DEBATE", "color": "0xE05A4F", "bars": "thin"},
    "museum_gold_2d": {"tag": "GOLD JADE", "color": "0xE8C46A", "bars": "thin"},
}

TRANSITION_ARGS = {
    "hard_cut": ("cut", "fade"),
    "fade": ("xfade", "fade"),
    "crossfade": ("xfade", "dissolve"),
    # fade_black 会插入纯黑帧（QC blackdetect>0），demo 用深色 fade 代替；
    # profile 里仍记录 transition=fade_black 供 H3/后期参考
    "fade_black": ("xfade", "fade"),
}


def transition_args(profile: dict) -> tuple[str, str]:
    key = str((profile or {}).get("transition") or "fade")
    return TRANSITION_ARGS.get(key, ("xfade", "fade"))


def grade_filter(profile: dict) -> str:
    palette = str((profile or {}).get("palette") or "")
    return PALETTE_FILTERS.get(palette, "")


def packaging_filters(profile: dict, workdir: Path, width: int, height: int,
                      font_file: str) -> str:
    """风格包装：顶部/底部 accent 条 + 风格 tag；VHS/电影/剧院额外效果。"""
    workdir = Path(workdir)
    pack = PACKAGING.get(str((profile or {}).get("preset") or ""), {})
    if not pack:
        return ""
    color = str(pack.get("color") or "0xFFFFFF")
    tag = str(pack.get("tag") or "")
    text_dir = workdir / "text"
    text_dir.mkdir(parents=True, exist_ok=True)
    tag_file = text_dir / "style_tag.txt"
    tag_file.write_text(tag or " ", encoding="utf-8")
    bars = str(pack.get("bars") or "thin")
    parts = []
    if bars == "letterbox":
        parts.append(f"drawbox=x=0:y=0:w=iw:h=ih*0.055:color=black@1:t=fill")
        parts.append(f"drawbox=x=0:y=ih*0.945:w=iw:h=ih*0.055:color=black@1:t=fill")
    elif bars == "thin":
        parts.append(f"drawbox=x=0:y=0:w=iw:h=4:color={color}@0.85:t=fill")
        parts.append(f"drawbox=x=0:y=ih-4:w=iw:h=4:color={color}@0.85:t=fill")
    elif bars == "curtain":
        parts.append(f"drawbox=x=0:y=0:w=iw*0.045:h=ih:color=0x6B0F14@0.85:t=fill")
        parts.append(f"drawbox=x=iw*0.955:y=0:w=iw*0.045:h=ih:color=0x6B0F14@0.85:t=fill")
        parts.append(f"drawbox=x=0:y=0:w=iw:h=4:color=0xE8C46A@0.85:t=fill")
    elif bars == "scanlines":
        parts.append(f"drawgrid=w=iw:h=3:t=1:c=black@0.10")
        parts.append(f"drawbox=x=0:y=0:w=iw:h=4:color={color}@0.85:t=fill")
    # 右上风格 tag
    parts.append(f"drawtext=textfile='text/style_tag.txt':fontfile='{font_file}':"
                 f"fontsize=24:fontcolor={color}:borderw=2:bordercolor=black@0.6:"
                 f"x=w-text_w-36:y=28")
    return ",".join(parts)


def prompt_prefix(profile: dict) -> str:
    return str((profile or {}).get("prompt_prefix") or "")
