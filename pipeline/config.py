"""流水线全局配置：二进制路径、默认参数、字体候选、品牌常量。"""
from __future__ import annotations

import os
import shutil
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
# 唯一输出根：与后端 app/config.OUTPUTS_DIR 同口径（环境变量优先）。
# templates3d / templates3d_hq / registry / lipsync / muse_video / orchestrator 都从这取。
OUTPUTS_ROOT = Path(os.environ.get("ZAOPIANJU_OUTPUTS_DIR") or (REPO_ROOT / "outputs"))

# ---- 视频参数 -------------------------------------------------------------
VIDEO_W = 1280
VIDEO_H = 720
FPS = 25
RESOLUTION = f"{VIDEO_W}x{VIDEO_H}"

# ---- 音频参数 -------------------------------------------------------------
SAMPLE_RATE = 48000
CHANNELS = 2
AUDIO_BITRATE = "192k"

# ---- TTS ------------------------------------------------------------------
DEFAULT_VOICE = "zh-CN-YunxiNeural"
TTS_RATE = "+6%"
TTS_MAX_ATEMPO = 1.45
TTS_CONCURRENCY = 5
TTS_RETRIES = 3

# ---- 渲染参数 -------------------------------------------------------------
X264_PRESET = "veryfast"
X264_CRF = "20"
BGM_VOLUME = 0.075
LUFS_TARGET = -16.0

# ---- 品牌 ----------------------------------------------------------------
BRAND_NAME = "造片局"
BRAND_TAGLINE = "AI 微综艺"
ACCENT_DEFAULT = "#F5C518"
ACCENT_TEXT = "#FFFFFF"
ACCENT_HOST = "#F5C518"
ACCENT_GUEST_A = "#5AC8FA"
ACCENT_GUEST_B = "#FF6B6B"

TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"

# ---- 角色资产根 -----------------------------------------------------------
# 真相源是 assets/v5/characters（26 个角色：portrait.png / cutout.png / pose_* …）；
# 旧代码写死 outputs/characters（那里只剩 3 个 meta.json），于是所有镜头"无角色"。
# 优先级：环境变量 > assets/v5/characters > outputs/characters（兼容旧布局）。
ASSETS_ROOT = Path(os.environ.get("ZAOPIANJU_ASSETS_DIR") or (REPO_ROOT / "assets"))
CHARS_ROOT = Path(
    os.environ.get("ZAOPIANJU_CHARS_DIR")
    or ((ASSETS_ROOT / "v5" / "characters") if (ASSETS_ROOT / "v5" / "characters").is_dir()
        else (OUTPUTS_ROOT / "characters"))
)


def _resolve_bin(env_var: str, rel_path: str, exe_name: str) -> str:
    """按 环境变量 -> 仓库内置 -> PATH 顺序定位二进制。"""
    val = os.environ.get(env_var)
    if val:
        p = Path(val)
        if p.exists():
            return str(p)
    p = REPO_ROOT / rel_path
    if p.exists():
        return str(p)
    which = shutil.which(exe_name)
    if which:
        return which
    raise FileNotFoundError(
        f"未找到 {exe_name}；可用环境变量 {env_var} 指定完整路径"
    )


FFMPEG_BIN = _resolve_bin(
    "FFMPEG_BIN", "tools/node_modules/ffmpeg-static/ffmpeg.exe", "ffmpeg"
)
FFPROBE_BIN = _resolve_bin(
    "FFPROBE_BIN",
    "tools/node_modules/ffprobe-static/bin/win32/x64/ffprobe.exe",
    "ffprobe",
)

FONT_CANDIDATES = [
    ("C:/Windows/Fonts/Deng.ttf", "DengXian"),
    ("C:/Windows/Fonts/msyhbd.ttc", "Microsoft YaHei"),
    ("C:/Windows/Fonts/msyh.ttc", "Microsoft YaHei"),
    ("C:/Windows/Fonts/simhei.ttf", "SimHei"),
    ("C:/Windows/Fonts/simsun.ttc", "SimSun"),
]


def normalize_path(p: str) -> str:
    return str(p).replace(chr(92), "/")
