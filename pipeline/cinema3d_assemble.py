# -*- coding: utf-8 -*-
"""V6.5 全 3D 成片剪辑器：云端 H3 3D 镜头 + 旁白 TTS + H3 环境声 + BGM → 1280×720/25fps 成片。

流程
----
1. 读 ``--episode``（``episode3d.json``）：片头/片尾卡片 + 54 个 3D 镜头，每镜
   ``duration`` / ``line`` / ``speech_duration`` / ``audio`` / ``_edit_start``；
2. 逐镜取 ``--shots/<sid>.mp4``：crop/scale 到 1280×720、fps=25、帧精确裁到镜长
   （偏差 ≤0.05s），写 ``<work>/clips/<sid>.mp4``（带缓存）；卡片镜优先用
   ``shots/v5_card_title.mp4`` / ``shots/v5_card_end.mp4``，缺失时纯黑底 + ASS 标题兜底；
   缺片段的非卡片镜跳过并把后续镜头顺接（pilot）；
3. 视频：``pipeline/editing.xfade_chain``（工程既有剪辑层，不引入新依赖）：
   顺剪硬切为主；**默认开**片头→v5_001 0.5s dissolve、v5_006/v5_016/v5_025 开头各
   0.4s dissolve、片尾最后 1.0s 淡出到黑；为保持总时长与 episode3d 完全一致，
   dissolve 用「左镜尾部延长 tf 帧（优先用源片多余素材，不足处克隆末帧）+ xfade」实现，
   即 output_total = Σ镜长，时间线起点与 episode ``_edit_start`` 对齐到帧；
4. 音频：``editing.mix_audio_edit`` 按时间线摆放每镜旁白 wav（含 episode 的 J/L cut，
   严格裁剪/补静音到镜长），写 ``<work>/audio/narration.wav``；
   H3 原声环境床**默认开**（``--ambience-db -20``）：每镜 H3 原声裁到镜长、
   volume=-20dB 后按时间线铺成 ``work/audio/ambience.wav``，与旁白 amix 进 narration.wav；
   ``--ambience-db 0`` 或 ``--no-ambience`` 关闭；
5. BGM：``v5_catalog`` museum_gold 风格 + ``v5_audio.ensure_bgm``，两遍 loudnorm
   （-16 LUFS）+ ducking 由 ``render.final_encode(..., duck_bgm=True, has_ass=True)`` 完成；
6. 字幕：本模块新写雅致款 ASS（SimSun 宋体系正文、KaiTi 片头/片尾、淡金细描边、底部
   安全区），不吃旧 ``final.ass``；片头主/副标题、三件国宝章节小标（左上角）、片尾
   「下期见」；片尾淡出用顶层黑色矩形 ``\\fad`` 实现（文字一起淡出，不额外重编码）；
7. 统一影调（默认开 ``--unify-look``）：解决云端 1344×768 锐利镜头与本地 H3
   768×432/1024×576 略软镜头混剪的质感差——逐镜 normalize 阶段按源分辨率分档
   ``unsharp``（锐利源 0.35 / 软源 1.0，实测校准见 ``SHARPEN_CALIBRATION``），
   整片再走 ``eq+colorbalance+vignette+noise`` 统一色调（暖金高光/青绿暗部）、
   轻暗角、极轻颗粒；参数与校准结论写进 final_report.json；
8. 报告：``<work>/final_report.json``（ffprobe/响度/blackdetect/字幕覆盖/每镜时长偏差/
   时间线核对/统一影调参数）+ ``frames_final/`` 8 张 + ``contact_sheet.png``（4×2）。

默认路径（都可以用参数覆盖）::

    python -m pipeline.cinema3d_assemble
    # 等价于：
    # --episode outputs/templates_v5/relic_talk_3d/work/episode3d.json
    # --shots   outputs/templates_v5/relic_talk_3d/shots
    # --out     outputs/templates_v5/relic_talk_3d/final.mp4
    # --work    outputs/templates_v5/relic_talk_3d/work_assemble
    # --fps 25 --width 1280 --height 720 --ambience-db -20

说明：``render.concat_audio`` 是按镜 back-to-back concat，遇到 xfade 转场重叠 / J-L cut
会与画面时间线错位，所以这里用同模块的 ``editing.mix_audio_edit``（带 start/j_cut/l_cut
的时间线混音）；若它不可用则回退 ``render.concat_audio``。
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import re
import shutil
import time
from datetime import datetime
from pathlib import Path

from . import config, editing, fonts, render as render_mod, utils, v5_audio, v5_catalog

LOG = logging.getLogger("pipeline")

DEFAULT_TITLE = "国宝会说话"
DEFAULT_TITLE_SUB = "造片局 · 5 分钟文物解说"
END_TITLE_FALLBACK = "下期见"
DEFAULT_AMBIENCE_DB = -20.0
DEFAULT_FADE_OUT = 1.0
#: 卡片镜 id → 类型（title / end）
CARD_IDS = {"v5_card_title": "title", "v5_card_end": "end"}
#: 三件国宝章节小标（Lead 指定 v5_006 / v5_016 / v5_025 开始处，左上角）
CHAPTER_START_IDS = {
    "v5_006": "第一件 · 越王勾践剑",
    "v5_016": "第二件 · 千里江山图",
    "v5_025": "第三件 · 曾侯乙编钟",
}
#: 章节小标兜底：按旁白文本识别（pilot 跳镜时 id 未必命中）
CHAPTER_TEXT_HINTS = (
    ("第一件", "第一件 · 越王勾践剑"),
    ("第二件", "第二件 · 千里江山图"),
    ("第三件", "第三件 · 曾侯乙编钟"),
)
#: 宋体系字体（雅致）；fonts.ensure_fonts 只准备 Deng/msyh，这里另拷宋体/楷体到 work/fonts
SERIF_FONT_SOURCES = (
    ("C:/Windows/Fonts/simsun.ttc", "simsun.ttc"),
    ("C:/Windows/Fonts/simkai.ttf", "simkai.ttf"),
)
CARD_DISSOLVE = 0.5
CHAPTER_DISSOLVE = 0.4
CLIP_VERSION = "cinema3d-v4"
MIN_SOURCE_BYTES = 2000
FRAME_COUNT = 8
REPORT_NAME = "final_report.json"
#: 统一影调（云端 1344×768 锐 vs 本地 768×432/1024×576 软 混剪的一致性处理）
#: 逐镜 unsharp：云端镜头轻锐化，本地低分辨率镜头多一档；整片再走统一色调/暗角/极轻颗粒
SHARPEN_CLOUD = 0.35
SHARPEN_LOCAL = 1.00
SOFT_SOURCE_MAX_WIDTH = 1200   # 源宽度 < 1200 视为「本地软素材」
#: sharpen 档位实测校准（2026-10-31，本地 H3 第一批素材到达后）
#: 方法：云端 v5_014 帧降采样 768×432 再 bicubic 升回 1280×720 模拟本地软素材，
#: 扫描 unsharp=5:5:X:5:5:0，用梯度能量(Tenengrad)/Laplacian 方差对齐云端处理档位，
#: 再用真实本地 v5_015 帧复核无过锐/晕轮。结论：本地取 1.0（≈54.8，与云端 0.35 的
#: ≈55.5 基本一致）；0.85 偏软、1.15 以上边缘开始过锐。
SHARPEN_CALIBRATION = {
    "method": ("云端 v5_014 帧 → 768×432 → 1280×720（bicubic，模拟本地 H3 分辨率损失）"
               "扫描 unsharp 档位，梯度能量对齐云端处理档位；真实本地 v5_015 帧复核"),
    "tenengrad": {
        "cloud_raw": 45.11, "cloud_us0.35": 55.54, "soft_raw": 37.29,
        "soft_us0.85": 51.44, "soft_us1.00": 54.82, "soft_us1.15": 58.50,
    },
    "laplacian_var": {
        "cloud_raw": 36.33, "cloud_us0.35": 55.75, "soft_raw": 19.50,
        "soft_us0.85": 38.82, "soft_us1.00": 44.48, "soft_us1.15": 51.87,
    },
    "conclusion": ("本地软素材 unsharp=1.0 后梯度能量≈54.8，与云端镜头 unsharp=0.35 "
                   "后≈55.5 基本一致；<0.85 偏软，>1.15 出现边缘过锐/晕轮，故取 1.0。"),
    "date": "2026-10-31",
    "evidence": "tmp/v61_3d/_sharpen2/compare.png + _sharpen2/calibration.json",
}
#: 逐镜微调色（QA warn 镜头的定向修复；不改变全片统一影调基线）
#: 依据 tmp/v61_3d/qa_review.md：v5_015 本地偏平/塑料 → 压暗+提对比；
#: v5_024 画中世界过饱和 → 降饱和+轻压暗；v5_026/027/028 现代白展板/钟墙 →
#: 压暗背景+轻降饱和。只对列出的镜头生效，其余镜头不额外调色。
SHOT_GRADE = {
    "v5_015": "eq=contrast=1.16:brightness=-0.055:saturation=1.02,"
              "colorbalance=rs=0.020:bs=-0.020",
    "v5_023": "eq=contrast=1.05:brightness=-0.020:saturation=0.97",
    "v5_024": "eq=contrast=1.06:brightness=-0.030:saturation=0.82",
    "v5_026": "eq=contrast=1.07:brightness=-0.035:saturation=0.94",
    "v5_027": "eq=contrast=1.06:brightness=-0.030:saturation=0.94",
    "v5_028": "eq=contrast=1.05:brightness=-0.025:saturation=0.95",
}
#: 逐镜源片入点（秒）：只能跳过片头且须保证剩余素材 ≥ 镜长（不足会冻结末帧）
#: v5_023 起始是「现代白墙美术馆」，跳过 0.7s（源 5.875s，镜长 5.17s，余量 0.705s）
SHOT_SOURCE_OFFSETS = {"v5_023": 0.70}
UNIFY_FILTER = (
    "eq=contrast=1.035:saturation=1.05:brightness=-0.004,"
    "colorbalance=rs=0.015:bs=-0.020:rm=0.010:bm=-0.010:rh=0.020:bh=-0.035,"
    "vignette=angle=0.38,"
    "noise=alls=3:allf=t+u"
)
REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_EPISODE3D = REPO_ROOT / "outputs/templates_v5/relic_talk_3d/work/episode3d.json"
DEFAULT_SHOTS_DIR = REPO_ROOT / "outputs/templates_v5/relic_talk_3d/shots"
DEFAULT_OUT = REPO_ROOT / "outputs/templates_v5/relic_talk_3d/final.mp4"
DEFAULT_WORK = REPO_ROOT / "outputs/templates_v5/relic_talk_3d/work_assemble"


# ---------------------------------------------------------------------------
# 小工具
# ---------------------------------------------------------------------------
def _valid_file(path: Path | str | None, min_size: int = MIN_SOURCE_BYTES) -> bool:
    try:
        p = Path(path) if path else None
        return bool(p and p.is_file() and p.stat().st_size >= min_size)
    except OSError:
        return False


def _file_fp(path: Path | str | None) -> str:
    if not path:
        return ""
    try:
        st = Path(path).stat()
        return f"{st.st_size}-{st.st_mtime_ns}"
    except OSError:
        return ""


def _shot_id(shot: dict) -> str:
    return str(shot.get("id") or "shot")


def _shot_duration(shot: dict) -> float:
    try:
        d = float(shot.get("duration") or 0.0)
    except (TypeError, ValueError):
        d = 0.0
    return d if d > 0.05 else 5.0


def _is_card(shot: dict) -> bool:
    return str(shot.get("type") or "") == "card" or _shot_id(shot) in CARD_IDS


def _chapter_label(shot: dict) -> str | None:
    sid = _shot_id(shot)
    if sid in CHAPTER_START_IDS:
        return CHAPTER_START_IDS[sid]
    line = str(shot.get("line") or "")
    for hint, label in CHAPTER_TEXT_HINTS:
        if hint in line:
            return label
    return None


def _json_write(path: Path | str, data) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    tmp.replace(p)


def _step(msg: str) -> None:
    print(f"[cinema3d] {msg}", flush=True)


# ---------------------------------------------------------------------------
# 1) episode / 镜头筛选
# ---------------------------------------------------------------------------
def load_episode(path: Path | str) -> tuple[dict, list[dict]]:
    data = utils.read_json(path)
    if not isinstance(data, dict):
        raise RuntimeError(f"无法读取 episode: {path}")
    episode = data.get("episode") if isinstance(data.get("episode"), dict) else {}
    shots = [s for s in (data.get("shots") or []) if isinstance(s, dict)]
    if not shots:
        raise RuntimeError(f"episode 没有 shots: {path}")
    return episode, shots


def classify_shots(shots: list[dict], shots_dir: Path, *,
                   only: set[str] | None = None,
                   limit: int | None = None) -> tuple[list[dict], list[dict], dict]:
    """逐镜判断来源：卡片（真片/黑底兜底）或普通 3D 镜头（缺片跳过）。

    返回 (entries, missing, cards)；entry = {"shot","id","duration","kind",
    "source","source_kind","source_fp"}。
    """
    picked = shots[:limit] if limit else list(shots)
    if only:
        picked = [s for s in picked if _shot_id(s) in only]
    entries: list[dict] = []
    missing: list[dict] = []
    cards: dict[str, dict] = {}
    for shot in picked:
        sid = _shot_id(shot)
        duration = _shot_duration(shot)
        if _is_card(shot):
            card_id = CARD_IDS.get(sid) or str(shot.get("card") or "card")
            src = shots_dir / f"{sid}.mp4"
            if _valid_file(src):
                entries.append({"shot": shot, "id": sid, "duration": duration,
                                "kind": "card", "source": str(src),
                                "source_kind": "card_video",
                                "source_fp": _file_fp(src)})
                cards[sid] = {"type": card_id, "source": "video", "path": str(src)}
            else:
                entries.append({"shot": shot, "id": sid, "duration": duration,
                                "kind": "card", "source": None,
                                "source_kind": "card_black",
                                "source_fp": f"black-{duration:.3f}"})
                cards[sid] = {"type": card_id, "source": "black", "path": None}
            continue
        src = shots_dir / f"{sid}.mp4"
        if not _valid_file(src):
            missing.append({"id": sid, "duration": duration,
                            "reason": f"缺少 {src}"})
            continue
        entries.append({"shot": shot, "id": sid, "duration": duration,
                        "kind": "shot", "source": str(src),
                        "source_kind": "h3", "source_fp": _file_fp(src)})
    return entries, missing, cards


# ---------------------------------------------------------------------------
# 2) 时间线 / 转场 / 帧计划
# ---------------------------------------------------------------------------
def _episode_starts(shots: list[dict]) -> list[float] | None:
    starts: list[float] = []
    for shot in shots:
        try:
            starts.append(float(shot["_edit_start"]))
        except (KeyError, TypeError, ValueError):
            return None
    return starts


def _transition_of(shot: dict) -> dict | None:
    """取镜头 edit.transition_out（dict 归一）；无声明返回 None。"""
    edit = shot.get("edit") if isinstance(shot.get("edit"), dict) else {}
    tr = edit.get("transition_out")
    if isinstance(tr, dict):
        mode = str(tr.get("mode") or "cut").strip().lower()
        try:
            dur = float(tr.get("duration") or 0.0)
        except (TypeError, ValueError):
            dur = 0.0
        return {"mode": mode, "duration": dur}
    if isinstance(tr, str) and tr.strip():
        return {"mode": tr.strip().lower(), "duration": 0.0}
    return None


def _episode_mode_for(shots: list[dict]) -> str:
    """判断 episode 的 ``_edit_start`` 是硬切时间线还是带转场重叠的时间线。"""
    starts = _episode_starts(shots)
    if not starts:
        return "cinema"
    overlaps = [round(starts[i - 1] + _shot_duration(shots[i - 1]) - starts[i], 6)
                for i in range(1, len(shots))]
    if all(abs(x) <= 0.02 for x in overlaps):
        return "cinema"
    declared = []
    for shot in shots[:-1]:
        tr = _transition_of(shot)
        declared.append(0.0 if tr is None else float(tr.get("duration") or 0.0))
    if all(abs(a - b) <= 0.06 for a, b in zip(overlaps, declared)):
        return "episode"
    return "cinema"


def build_raw_boundaries(shots: list[dict], mode: str,
                         chapter_dissolve: bool = True) -> list[dict]:
    """构建 n-1 个剪辑点：{"mode","duration"}；cut 即硬切。"""
    n = len(shots)
    if n < 2:
        return []
    if mode == "cut":
        return [{"mode": "cut", "duration": 0.0} for _ in range(n - 1)]
    out: list[dict] = []
    for i in range(n - 1):
        a, b = shots[i], shots[i + 1]
        if mode == "episode":
            tr = _transition_of(a)
            if tr is not None and tr["mode"] not in ("cut", "none", ""):
                # ffmpeg xfade 的 dissolve 是「随机噪声像素溶解」，纪录片式交叉溶解
                # 用 fade（平滑 crossfade）更雅致
                m = "fade" if tr["mode"] in ("dissolve", "fade") else tr["mode"]
                out.append({"mode": m,
                            "duration": tr["duration"] or editing.DEFAULT_DURATION})
            elif _is_card(a) or _is_card(b):
                out.append({"mode": "fade", "duration": CARD_DISSOLVE})
            else:
                out.append({"mode": "cut", "duration": 0.0})
        else:  # cinema：硬切为主 + 片头/章节 fade（默认开）
            if CARD_IDS.get(_shot_id(a)) == "title":
                out.append({"mode": "fade", "duration": CARD_DISSOLVE})
            elif chapter_dissolve and _chapter_label(b):
                out.append({"mode": "fade", "duration": CHAPTER_DISSOLVE})
            else:
                out.append({"mode": "cut", "duration": 0.0})
    return out


def resolve_boundaries(shots: list[dict], raw: list[dict]) -> list[dict]:
    """复用 editing 的边界归一/时长夹取逻辑（与 xfade_chain 内部一致）。"""
    if not raw:
        return []
    try:
        return editing._normalize_boundaries(shots, raw)  # noqa: SLF001 - 同工程剪辑层
    except Exception as exc:  # noqa: BLE001
        LOG.warning("editing 边界解析失败（回退简单模式）: %s", exc)
        out = []
        for i, item in enumerate(raw):
            mode = str(item.get("mode") or "cut").lower()
            cut = mode in ("", "cut", "none", "hard", "hard_cut")
            dur = 0.0
            if not cut:
                try:
                    dur = float(item.get("duration") or editing.DEFAULT_DURATION)
                except (TypeError, ValueError):
                    dur = editing.DEFAULT_DURATION
                dur = max(0.04, min(dur, 0.8,
                                    _shot_duration(shots[i]) * 0.5,
                                    _shot_duration(shots[i + 1]) * 0.5))
            out.append({"mode": mode if not cut else "cut", "duration": dur,
                        "cut": cut})
        return out


def plan_frame_timeline(entries: list[dict], boundaries: list[dict], fps: int,
                        episode_starts: list[float] | None,
                        episode_total: float | None) -> dict:
    """生成帧级时间线：每镜 nominal 帧 + 需要 dissolve 的「尾部延长」帧。

    关键设计（保持总时长 = Σ镜长）：
    - 每镜 nominal 帧 ``nf_i = F_i - F_{i-1}``（F_i = round(edit_start_i*fps)），
      与 episode ``_edit_start`` 逐镜对齐到帧；
    - 有 outgoing dissolve 的镜头把 clip 尾部延长 ``tf`` 帧（xfade 会吃掉同样的 tf 帧），
      因此 ``output_total = Σ(nf_i + tf_i)/fps - Σ tf_i/fps = Σ nf_i/fps``；
    - clip 延长的素材优先取源片多余时长，不足处由 tpad 克隆末帧补齐（freeze_sec 记录）。

    返回 {starts, total, nominal_frames, ext_frames, render_frames, total_frames,
    boundary_frames}。
    """
    n = len(entries)
    nominal = [float(e["duration"]) for e in entries]
    nominal_frames: list[int] = []
    total_frames = 0
    # 1) nominal 帧：整片运行且 episode 有 _edit_start 时做全局帧对齐
    #    （F_i = round(edit_start_i * fps)，总长 = round(episode.total_duration * fps)）
    aligned = False
    if episode_starts and len(episode_starts) == n and episode_total:
        f_vals = [int(round(s * fps)) for s in episode_starts]
        t_frames = int(round(float(episode_total) * fps))
        monotonic = all(f_vals[i] < f_vals[i + 1] for i in range(n - 1)) \
            if n > 1 else True
        if f_vals and f_vals[0] == 0 and monotonic and t_frames > f_vals[-1]:
            edges = list(f_vals) + [t_frames]
            nominal_frames = [max(1, edges[i + 1] - edges[i]) for i in range(n)]
            total_frames = sum(nominal_frames)
            aligned = True
    if not aligned:
        # pilot/跳镜/无 _edit_start：按镜长独立量化，残差补到最后一镜
        nominal_frames = [max(1, int(round(d * fps))) for d in nominal]
        total_frames = int(round(sum(nominal) * fps))
        delta = total_frames - sum(nominal_frames)
        if delta:
            nominal_frames[-1] = max(1, nominal_frames[-1] + delta)
            total_frames = sum(nominal_frames)
    # 2) 转场帧：dissolve 时长量化到帧
    boundary_frames: list[int] = []
    for i, b in enumerate(boundaries):
        if b.get("cut"):
            boundary_frames.append(0)
            continue
        try:
            dur = float(b.get("duration") or 0.0)
        except (TypeError, ValueError):
            dur = 0.0
        tf = max(1, int(round(dur * fps)))
        # 不能超过相邻镜时长的一半（与 xfade 语义一致）
        tf = max(1, min(tf, max(1, nominal_frames[i] // 2),
                        max(1, nominal_frames[i + 1] // 2)))
        boundary_frames.append(tf)
    ext_frames = [0] * n
    for i, tf in enumerate(boundary_frames):
        if tf > 0 and i < n - 1:
            ext_frames[i] = tf
            boundaries[i]["duration"] = round(tf / float(fps), 6)
            boundaries[i]["frames"] = tf
    render_frames = [nominal_frames[i] + ext_frames[i] for i in range(n)]
    starts = []
    acc = 0
    for f in nominal_frames:
        starts.append(round(acc / float(fps), 6))
        acc += f
    total = round(total_frames / float(fps), 6)
    return {
        "nominal": nominal,
        "nominal_frames": nominal_frames,
        "ext_frames": ext_frames,
        "render_frames": render_frames,
        "boundary_frames": boundary_frames,
        "total_frames": total_frames,
        "total": total,
        "starts": starts,
    }


def compute_starts(durations: list[float],
                   boundaries: list[dict]) -> tuple[list[float], float]:
    """xfade 语义下的时间线（仅 episode 旧模式回退用）。"""
    starts = [0.0]
    for i in range(1, len(durations)):
        gap = 0.0
        if i - 1 < len(boundaries) and not boundaries[i - 1].get("cut"):
            gap = float(boundaries[i - 1].get("duration") or 0.0)
        starts.append(round(starts[i - 1] + durations[i - 1] - gap, 6))
    total = round(starts[-1] + durations[-1], 6) if durations else 0.0
    return starts, total


# ---------------------------------------------------------------------------
# 3) 逐镜标准化（1280×720 / 25fps / 帧精确镜长）
# ---------------------------------------------------------------------------
def normalize_entry(entry: dict, work: Path, fps: int, width: int, height: int,
                    render_frames: int, nominal_frames: int,
                    force: bool = False, apply_adjust: bool = True) -> dict:
    """把源镜头标准化到成片规格；clip 长 = (nominal + dissolve 尾部延长) 帧。"""
    sid = entry["id"]
    frames = max(1, int(render_frames))
    target = round(frames / float(fps), 6)
    nominal_target = round(max(1, int(nominal_frames)) / float(fps), 6)
    clips_dir = utils.ensure_dir(work / "clips")
    dst = clips_dir / f"{sid}.mp4"
    meta_path = clips_dir / f"{sid}.meta.json"
    # 源分辨率分档（云端 1344×768 锐 / 本地 768×432、1024×576 软）→ 逐镜锐化档位
    source = entry.get("source")
    source_width = 0
    source_tier = "black"
    sharpen = 0.0
    if source:
        try:
            source_width = int((entry.get("_source_probe")
                                or utils.probe_video(source)).get("width") or 0)
        except Exception:  # noqa: BLE001
            source_width = 0
        source_tier = "soft" if 0 < source_width < SOFT_SOURCE_MAX_WIDTH else "sharp"
        sharpen = SHARPEN_LOCAL if source_tier == "soft" else SHARPEN_CLOUD
    # QA 定向微调：逐镜调色 / 源片入点（默认开，--no-shot-grade 可关）
    grade = SHOT_GRADE.get(sid) if apply_adjust else None
    source_offset = float(SHOT_SOURCE_OFFSETS.get(sid, 0.0)) if apply_adjust else 0.0
    digest = utils.sha1_text(sid, entry.get("source_fp"), frames, nominal_frames,
                             width, height, fps, sharpen, grade or "",
                             source_offset, CLIP_VERSION)
    if not force and _valid_file(dst, 1000):
        meta = utils.read_json(meta_path) or {}
        if meta.get("hash") == digest:
            actual = float(meta.get("actual") or utils.probe_duration(dst))
            if actual > 0.0:
                return {**entry, "clip": str(dst), "target_duration": target,
                        "nominal_duration": nominal_target,
                        "nominal_frames": nominal_frames,
                        "render_frames": frames,
                        "actual_duration": round(actual, 4),
                        "deviation": round(actual - target, 4),
                        "source_tier": source_tier,
                        "source_width": source_width,
                        "sharpen": sharpen, "shot_grade": grade,
                        "source_offset": source_offset, "reused": True}

    if source:
        us = f"unsharp=5:5:{sharpen:.2f}:5:5:0.0"
        parts = [
            f"scale={width}:{height}:force_original_aspect_ratio=increase",
            f"crop={width}:{height}",
            "setsar=1",
            f"fps={fps}",
        ]
        if grade:
            parts.append(grade)
        parts += [
            us,
            f"tpad=stop_mode=clone:stop_duration={target + 2.0:.3f}",
            f"trim=0:{target:.6f}",
            "setpts=PTS-STARTPTS",
            "format=yuv420p",
        ]
        args = []
        if source_offset > 0.001:
            args += ["-ss", f"{source_offset:.3f}"]
        args += ["-i", str(source), "-vf", ",".join(parts),
                 "-frames:v", str(frames), "-an",
                 "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
                 "-pix_fmt", "yuv420p", "-r", str(fps),
                 "-movflags", "+faststart", str(dst)]
        utils.ffmpeg(args)
    else:  # 卡片黑底兜底
        utils.ffmpeg([
            "-f", "lavfi", "-i",
            f"color=c=black:s={width}x{height}:r={fps}:d={target:.6f}",
            "-frames:v", str(frames), "-an",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
            "-pix_fmt", "yuv420p", "-r", str(fps),
            "-movflags", "+faststart", str(dst),
        ])
    actual = utils.probe_duration(dst)
    deviation = round(actual - target, 4)
    utils.write_json(meta_path, {
        "hash": digest, "nominal": float(entry["duration"]),
        "nominal_target": nominal_target, "target": target,
        "nominal_frames": nominal_frames, "frames": frames,
        "actual": actual, "source": str(source or ""),
        "source_kind": entry.get("source_kind"),
        "source_tier": source_tier, "source_width": source_width,
        "sharpen": sharpen, "shot_grade": grade,
        "source_offset": source_offset,
        "spec": f"{width}x{height}@{fps}", "version": CLIP_VERSION,
    })
    if abs(deviation) > 0.05:
        LOG.warning("镜头 %s 标准化时长偏差 %.3fs（目标 %.3fs / 实测 %.3fs）",
                    sid, deviation, target, actual)
    return {**entry, "clip": str(dst), "target_duration": target,
            "nominal_duration": nominal_target,
            "nominal_frames": nominal_frames, "render_frames": frames,
            "actual_duration": round(actual, 4), "deviation": deviation,
            "source_tier": source_tier, "source_width": source_width,
            "sharpen": sharpen, "shot_grade": grade,
            "source_offset": source_offset, "reused": False}


def normalize_all(entries: list[dict], plan: dict, work: Path, fps: int,
                  width: int, height: int, force: bool = False,
                  apply_adjust: bool = True) -> list[dict]:
    out: list[dict] = []
    for i, entry in enumerate(entries):
        out.append(normalize_entry(
            entry, work, fps, width, height,
            plan["render_frames"][i], plan["nominal_frames"][i], force,
            apply_adjust=apply_adjust))
        if (i + 1) % 10 == 0 or i + 1 == len(entries):
            _step(f"标准化镜头 {i + 1}/{len(entries)}")
    return out


# ---------------------------------------------------------------------------
# 4) 视频拼接（editing.xfade_chain：硬切 + dissolve，保持总时长）
# ---------------------------------------------------------------------------
def assemble_video(norm_entries: list[dict], boundaries: list[dict],
                   work: Path, fps: int, width: int, height: int,
                   total: float) -> Path:
    # 传给 xfade_chain 的镜头时长 = 延长后的 render 时长；其内部 expect
    # = Σrender - Σ转场 = Σnominal = total（帧精确，保持总时长）
    timeline_shots = [dict(e["shot"], duration=e["target_duration"],
                           _cinema3d_nominal=e["nominal_duration"])
                      for e in norm_entries]
    clip_paths = {e["id"]: Path(e["clip"]) for e in norm_entries}
    _step(f"拼接视频：{len(timeline_shots)} 镜 / {total:.3f}s / "
          f"{sum(1 for b in boundaries if not b.get('cut'))} 个 dissolve（保时长）")
    video = editing.xfade_chain(
        timeline_shots, clip_paths, work, fps, width, height, total,
        boundaries, prog=None)
    target = work / "video_raw.mp4"
    if Path(video).resolve() != target.resolve():
        shutil.copy2(video, target)
    return target


# ---------------------------------------------------------------------------
# 5) 音频：旁白时间线 + H3 环境声床（默认开）
# ---------------------------------------------------------------------------
def _copy_shot_audio(entries: list[dict], work: Path) -> list[dict]:
    """把每镜旁白 wav 复制到 work/audio/<sid>.wav（回退 concat 与留档）。"""
    audio_dir = utils.ensure_dir(work / "audio")
    resolved: list[dict] = []
    for entry in entries:
        shot = entry["shot"]
        src = Path(str(shot.get("audio") or ""))
        dst = audio_dir / f"{entry['id']}.wav"
        ok = _valid_file(src, 100)
        if ok and (not dst.is_file() or dst.stat().st_size != src.stat().st_size):
            try:
                shutil.copyfile(src, dst)
            except OSError as exc:  # noqa: BLE001
                LOG.warning("复制旁白失败 %s: %s", src, exc)
        resolved.append({**entry,
                         "audio": str(dst) if _valid_file(dst, 100) else None})
    return resolved


def build_narration(entries: list[dict], starts: list[float], work: Path,
                    total: float) -> tuple[Path, dict]:
    """时间线混音：每镜 wav 裁/补到 nominal 镜长后摆到 start（含 J/L cut）。"""
    work = Path(work)
    shots_timeline = []
    for entry, start in zip(entries, starts):
        shot = entry["shot"]
        edit = shot.get("edit") if isinstance(shot.get("edit"), dict) else {}
        shots_timeline.append({
            "id": entry["id"],
            "duration": entry["nominal_duration"],
            "start": round(float(start), 6),
            "audio": entry.get("audio") or str(shot.get("audio") or ""),
            "j_cut": float(edit.get("j_cut") or 0.0),
            "l_cut": float(edit.get("l_cut") or 0.0),
        })
    cuts = [{"shot": s["id"], "j_cut": s["j_cut"], "l_cut": s["l_cut"]}
            for s in shots_timeline]
    stats = {"engine": "editing.mix_audio_edit",
             "shots": len(shots_timeline),
             "j_cuts": sum(1 for s in shots_timeline if s["j_cut"] > 0),
             "l_cuts": sum(1 for s in shots_timeline if s["l_cut"] > 0),
             "missing_audio": [s["id"] for s in shots_timeline
                               if not _valid_file(s["audio"], 100)]}
    narration_path: Path | None = None
    try:
        mixed = editing.mix_audio_edit(shots_timeline, work, total, cuts)
        narration_path = work / "audio" / "narration.wav"
        utils.ensure_dir(narration_path.parent)
        if Path(mixed).resolve() != narration_path.resolve():
            shutil.copy2(mixed, narration_path)
    except Exception as exc:  # noqa: BLE001 - 回退 render.concat_audio
        LOG.warning("时间线混音失败（回退 render.concat_audio）: %s", exc)
        stats["engine"] = "render.concat_audio(fallback)"
        try:
            shots_for_concat = [dict(e["shot"], duration=e["nominal_duration"])
                               for e in entries]
            raw = render_mod.concat_audio(shots_for_concat, work, total)
            narration_path = work / "audio" / "narration.wav"
            utils.ensure_dir(narration_path.parent)
            shutil.copy2(raw, narration_path)
            stats["fallback_note"] = "旁白按镜 back-to-back 拼接（无转场重叠时正确）"
        except Exception as exc2:  # noqa: BLE001
            raise RuntimeError(f"旁白混音失败: {exc}; 回退也失败: {exc2}") from exc2
    real = utils.probe_duration(narration_path)
    stats["path"] = str(narration_path)
    stats["duration"] = round(real, 4)
    stats["deviation"] = round(real - total, 4)
    if abs(real - total) > 0.12:
        LOG.warning("旁白时长 %.3fs 与目标 %.3fs 偏差较大", real, total)
    return narration_path, stats


def build_ambience(entries: list[dict], starts: list[float], work: Path,
                   total: float, db: float) -> Path | None:
    """每镜 H3 原声裁到镜长、按 db 衰减后铺成环境声床（默认 -20dB）。"""
    gain = 10.0 ** (float(db) / 20.0)
    inputs: list[str] = []
    filters: list[str] = []
    idx = 0
    for entry, start in zip(entries, starts):
        src = entry.get("source")
        if not src or not _valid_file(src):
            continue
        try:
            if not utils.probe_video(src).get("has_audio"):
                continue
        except Exception:  # noqa: BLE001
            continue
        d = entry["nominal_duration"]
        delay = max(0, int(round(float(start) * 1000)))
        inputs += ["-i", str(src)]
        filters.append(
            f"[{idx}:a]aresample={config.SAMPLE_RATE},"
            f"aformat=sample_rates={config.SAMPLE_RATE}:channel_layouts=stereo,"
            f"atrim=0:{d:.6f},apad=whole_dur={d:.6f},"
            f"volume={gain:.4f},adelay={delay}|{delay}[a{idx}]"
        )
        idx += 1
    if idx == 0:
        LOG.warning("没有可用的 H3 原声，跳过环境声床")
        return None
    if idx == 1:
        filters.append("[a0]anull[aall]")
    else:
        filters.append("".join(f"[a{i}]" for i in range(idx))
                       + f"amix=inputs={idx}:duration=longest:normalize=0[aall]")
    filters.append(f"[aall]atrim=0:{total:.6f},aresample={config.SAMPLE_RATE}[aout]")
    out = work / "audio" / "ambience.wav"
    utils.ensure_dir(out.parent)
    utils.ffmpeg(inputs + [
        "-filter_complex", ";".join(filters), "-map", "[aout]",
        "-t", f"{total:.3f}", "-ar", str(config.SAMPLE_RATE), "-ac", "2",
        "-c:a", "pcm_s16le", str(out),
    ])
    _step(f"环境声床：{idx} 镜 H3 原声 / {db:+.1f}dB / "
          f"{utils.probe_duration(out):.2f}s")
    return out


def mix_ambience(narration: Path, ambience: Path, work: Path,
                 total: float) -> Path:
    """旁白 + 环境声床混音（旁白为主，环境低音量铺底），写 audio/narration.wav。"""
    out = work / "audio" / "narration.wav"
    tmp = work / "audio" / "_narration_mix.wav"
    utils.ffmpeg([
        "-i", str(narration), "-i", str(ambience),
        "-filter_complex",
        ("[0:a]volume=1.0[d];[1:a]volume=1.0[e];"
         f"[d][e]amix=inputs=2:duration=first:normalize=0,"
         f"alimiter=limit=0.95,atrim=0:{total:.6f},"
         f"aresample={config.SAMPLE_RATE}[a]"),
        "-map", "[a]", "-t", f"{total:.3f}",
        "-ar", str(config.SAMPLE_RATE), "-ac", "2",
        "-c:a", "pcm_s16le", str(tmp),
    ])
    tmp.replace(out)
    return out


# ---------------------------------------------------------------------------
# 6) 字幕 ASS（雅致款，新写）
# ---------------------------------------------------------------------------
def _ass_time(seconds: float) -> str:
    total_cs = max(0, int(round(float(seconds) * 100)))
    h, rem = divmod(total_cs, 360000)
    m, rem = divmod(rem, 6000)
    s, cs = divmod(rem, 100)
    return f"{h:d}:{m:02d}:{s:02d}.{cs:02d}"


def _ass_escape(text: str) -> str:
    t = str(text or "")
    t = t.replace("\\", "\\\\").replace("{", "\\{").replace("}", "\\}")
    return t.replace("\r", " ").replace("\n", " ")


def _wrap_cjk(text: str, max_chars: int = 22) -> str:
    """长句在标点处折两行（ASS \\N）；短句原样返回。"""
    plain = "".join(str(text or "").split())
    if not plain:
        return ""
    if len(plain) <= max_chars:
        return _ass_escape(plain)
    punct = "，。！？；：、,.!?;:）)】」』"
    ideal = len(plain) / 2.0
    split = None
    for i, ch in enumerate(plain[:-1]):
        if ch in punct and abs((i + 1) - ideal) <= 6:
            split = i + 1
            break
    if split is None:
        split = max(1, min(len(plain) - 1, int(round(ideal))))
    return _ass_escape(plain[:split]) + "\\N" + _ass_escape(plain[split:])


def _ensure_serif_fonts(work: Path) -> list[str]:
    """把宋体/楷体复制到 work/fonts（须在 fonts.ensure_fonts 之后调用，避免被清理）。"""
    font_dir = utils.ensure_dir(work / "fonts")
    copied: list[str] = []
    for src, name in SERIF_FONT_SOURCES:
        src_path = Path(src)
        if not src_path.is_file():
            continue
        dst = font_dir / name
        try:
            if not dst.is_file() or dst.stat().st_size != src_path.stat().st_size:
                shutil.copyfile(src_path, dst)
            copied.append(str(dst))
        except OSError as exc:  # noqa: BLE001
            LOG.warning("复制字体失败 %s: %s", src, exc)
    return copied


def _ass_header(width: int, height: int, styles: list[str]) -> str:
    lines = [
        "[Script Info]",
        "ScriptType: v4.00+",
        f"PlayResX: {width}",
        f"PlayResY: {height}",
        "WrapStyle: 2",
        "ScaledBorderAndShadow: yes",
        "YCbCr Matrix: TV.709",
        "",
        "[V4+ Styles]",
        ("Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
         "OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, "
         "ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, "
         "MarginL, MarginR, MarginV, Encoding"),
        *styles,
        "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, "
        "Effect, Text",
    ]
    return "\n".join(lines) + "\n"


def build_ass(entries: list[dict], starts: list[float], work: Path,
              width: int, height: int, *, title: str, total: float,
              fade_out: float = DEFAULT_FADE_OUT,
              title_sub: str = DEFAULT_TITLE_SUB) -> dict:
    """生成雅致款 ASS：正文宋体 + 淡金细描边，底部安全区；片头/章节/片尾/淡出。"""
    body_size = max(22, int(round(height * 0.0458)))
    chapter_size = max(16, int(round(height * 0.0361)))
    title_size = max(30, int(round(height * 0.1055)))
    title_sub_size = max(14, int(round(height * 0.0333)))
    end_size = max(28, int(round(height * 0.0944)))
    body_mv = int(round(height * 0.072))
    body_margin = int(round(width * 0.0586))
    gold = "&H0037AFD4"       # #D4AF37 → BGR
    gold_soft = "&H004AA2C9"  # #C9A24A
    cream = "&H00A8E2F4"      # #F4E2A8 → BGR
    dark = "&H0012100A"
    shadow = "&H96000000"
    body_back = "&H60000000"  # 正文阴影更深一点（白字在亮画面上更稳）
    styles = [
        (f"Style: Body,SimSun,{body_size},&H00FFFFFF,&H00FFFFFF,{gold},{body_back},"
         f"0,0,0,0,100,100,1,0,1,1,2,2,{body_margin},{body_margin},{body_mv},1"),
        (f"Style: Chapter,SimSun,{chapter_size},{gold},{gold},{dark},{shadow},"
         f"-1,0,0,0,100,100,3,0,1,1,0,7,{int(width * 0.050)},"
         f"{int(width * 0.050)},{int(height * 0.0722)},1"),
        (f"Style: TitleMain,KaiTi,{title_size},{cream},{cream},{dark},{shadow},"
         f"0,0,0,0,100,100,{max(4, int(height * 0.0111))},0,1,2,2,5,80,80,0,1"),
        (f"Style: TitleSub,DengXian,{title_sub_size},{gold_soft},{gold_soft},{dark},"
         f"{shadow},0,0,0,0,100,100,{max(2, int(height * 0.0069))},0,1,1,1,"
         f"5,80,80,0,1"),
        (f"Style: EndMain,KaiTi,{end_size},{cream},{cream},{dark},{shadow},"
         f"0,0,0,0,100,100,{max(4, int(height * 0.0111))},0,1,2,2,5,80,80,0,1"),
    ]
    lines: list[str] = _ass_header(width, height, styles).splitlines()
    stats = {"narration_events": 0, "title_events": 0, "chapter_events": 0,
             "end_events": 0, "fade_out_events": 0, "total_events": 0}
    cx = width // 2

    def _dialogue(style: str, t0: float, t1: float, layer: int, text: str,
                  tags: str = "") -> None:
        if t1 <= t0:
            return
        body = f"{{{tags}}}" if tags else ""
        lines.append(
            f"Dialogue: {layer},{_ass_time(t0)},{_ass_time(t1)},{style},,0,0,0,,"
            f"{body}{text}")

    for entry, start in zip(entries, starts):
        shot = entry["shot"]
        sid = entry["id"]
        start = float(start)
        dur = float(entry.get("nominal_duration") or entry["duration"])
        card_type = CARD_IDS.get(sid)
        if card_type == "title":
            main = str(shot.get("title") or title).strip() or title
            sub = str(shot.get("subtitle") or "").strip()
            if not sub or sub == main:
                sub = title_sub
            t0, t1 = start + 0.20, start + dur - 0.10
            _dialogue("TitleMain", t0, t1, 0, _ass_escape(main),
                      f"\\an5\\pos({cx},{int(height * 0.410)})\\fad(300,420)")
            rule_w = int(width * 0.070)
            rule_y = int(height * 0.410) + int(title_size * 0.72)
            _dialogue(
                "TitleMain", t0, t1, 0, "",
                (f"\\an5\\pos({cx},{rule_y})\\p1\\c{gold}\\bord0\\shad0"
                 f"\\fad(300,420)"
                 f"m -{rule_w} 0 l {rule_w} 0 {rule_w} 2 -{rule_w} 2{{\\p0}}"))
            _dialogue("TitleSub", t0 + 0.15, t1, 0, _ass_escape(sub),
                      f"\\an5\\pos({cx},{rule_y + int(height * 0.0520)})"
                      f"\\fad(420,420)")
            stats["title_events"] += 3
        elif card_type == "end":
            main = (str(shot.get("title") or "").strip()
                    or str(shot.get("subtitle") or "").strip()
                    or END_TITLE_FALLBACK)
            sub = str(shot.get("subtitle") or "").strip()
            t0, t1 = start + 0.20, start + dur - 0.10
            _dialogue("EndMain", t0, t1, 0, _ass_escape(main),
                      f"\\an5\\pos({cx},{int(height * 0.450)})\\fad(320,520)")
            if sub and sub != main:
                _dialogue("TitleSub", t0 + 0.2, t1, 0, _ass_escape(sub),
                          f"\\an5\\pos({cx},"
                          f"{int(height * 0.450) + int(height * 0.085)})"
                          f"\\fad(400,400)")
                stats["end_events"] += 2
            else:
                stats["end_events"] += 1
        else:
            label = _chapter_label(shot)
            if label:
                bar_x = int(width * 0.036)
                bar_y = int(height * 0.074)
                text_x = bar_x + int(width * 0.0125)
                c0 = start + 0.30
                c1 = min(start + dur - 0.15, c0 + 4.6)
                _dialogue("Chapter", c0, c1, 1, "",
                          (f"\\an7\\pos({bar_x},{bar_y})\\p1\\c{gold}"
                           f"\\bord0\\shad0\\fad(200,300)"
                           f"m 0 0 l 3 0 3 26 0 26{{\\p0}}"))
                _dialogue("Chapter", c0, c1, 1, _ass_escape(label),
                          f"\\an7\\pos({text_x},{bar_y})\\fad(200,300)")
                stats["chapter_events"] += 2
            line_text = str(shot.get("line") or "").strip()
            if line_text:
                try:
                    speech = float(shot.get("speech_duration") or 0.0)
                except (TypeError, ValueError):
                    speech = 0.0
                t0 = start + 0.10
                end_off = min(max(0.5, dur - 0.10),
                              max(0.5, speech + 0.40) if speech > 0
                              else dur - 0.10)
                t1 = start + end_off
                if t1 < t0 + 0.5:
                    t1 = t0 + 0.5
                _dialogue("Body", t0, t1, 0, _wrap_cjk(line_text),
                          "\\fad(150,180)")
                stats["narration_events"] += 1
    # 片尾淡出到黑：顶层整帧黑色矩形 1.0s 内由透明到全黑（连字幕一起淡出）
    if fade_out and fade_out > 0.05:
        f0 = max(0.0, float(total) - float(fade_out))
        f1 = float(total) + 0.5
        _dialogue(
            "EndMain", f0, f1, 9, "",
            (f"\\an7\\pos(0,0)\\p1\\c&H000000&\\bord0\\shad0"
             f"\\fad({int(round(fade_out * 1000))},0)"
             f"m 0 0 l {width} 0 {width} {height} 0 {height}{{\\p0}}"))
        stats["fade_out_events"] += 1
    stats["total_events"] = sum(
        1 for line in lines if line.startswith("Dialogue:"))
    out = Path(work) / "final.ass"
    out.parent.mkdir(parents=True, exist_ok=True)
    # 与工程内其它 ASS 一致：UTF-8 BOM
    out.write_text("\n".join(lines) + "\n", encoding="utf-8-sig")
    stats["path"] = str(out)
    stats["body_font"] = "SimSun"
    stats["title_font"] = "KaiTi"
    stats["size"] = [width, height]
    stats["fade_out_sec"] = float(fade_out or 0.0)
    return stats


# ---------------------------------------------------------------------------
# 7) 成片编码 + 报告 + 抽帧
# ---------------------------------------------------------------------------
def encode_final(work: Path, out: Path, total: float, fps: int, width: int,
                 height: int, bgm: Path | None,
                 grade_filter: str | None = None) -> dict:
    out.parent.mkdir(parents=True, exist_ok=True)
    _step(f"最终编码（{total:.2f}s / {width}x{height}@{fps} / BGM ducking / ASS"
          f"{' / 统一影调' if grade_filter else ''}）")
    last = {"pct": -10.0}

    def prog(pct: float, msg: str = "") -> None:
        if pct >= last["pct"] + 10.0:
            last["pct"] = pct
            print(f"[cinema3d] 编码 {pct:.0f}% {msg}".rstrip(), flush=True)

    t0 = time.time()
    render_mod.final_encode(
        work, out, total, fps, width, height, bgm,
        has_ass=True, prog=prog, pct0=0, pct1=100,
        grade_filter=grade_filter, duck_bgm=True)
    elapsed = round(time.time() - t0, 1)
    _step(f"编码完成：{out}（{elapsed}s）")
    return {"elapsed_sec": elapsed, "bgm_ducking": True, "ass": True,
            "grade_filter": grade_filter}


_BLACK_RE = re.compile(
    r"black_start:\s*([0-9.]+)\s*black_end:\s*([0-9.]+)\s*black_duration:\s*([0-9.]+)")


def detect_black(out: Path, min_duration: float = 0.1,
                 pix_th: float = 0.02) -> dict:
    try:
        proc = utils.run([
            config.FFMPEG_BIN, "-hide_banner", "-nostats", "-i", str(out),
            "-vf", f"blackdetect=d={min_duration}:pix_th={pix_th}",
            "-an", "-f", "null", os.devnull,
        ], check=False)
        text = proc.stderr or ""
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc), "segments": []}
    segments = [{"black_start": round(float(a), 3),
                 "black_end": round(float(b), 3),
                 "black_duration": round(float(c), 3)}
                for a, b, c in _BLACK_RE.findall(text)]
    return {"ok": True, "min_duration": min_duration, "pix_th": pix_th,
            "count": len(segments), "segments": segments}


def measure_media(out: Path) -> dict:
    probe = utils.probe_video(out)
    info = {"duration": probe.get("duration", 0.0),
            "width": probe.get("width", 0),
            "height": probe.get("height", 0),
            "fps": probe.get("fps", 0.0),
            "codec": probe.get("codec", ""),
            "has_audio": bool(probe.get("has_audio")),
            "size_bytes": 0}
    try:
        info["size_bytes"] = out.stat().st_size
    except OSError:
        pass
    try:
        data = utils.ffprobe_json(out)
        for stream in data.get("streams") or []:
            if stream.get("codec_type") == "audio":
                info["audio_codec"] = stream.get("codec_name") or ""
                info["audio_channels"] = int(stream.get("channels") or 0)
                info["audio_sample_rate"] = int(stream.get("sample_rate") or 0)
    except Exception:  # noqa: BLE001
        pass
    return info


def extract_frames(out: Path, frames_dir: Path, timeline: list[dict],
                   count: int = FRAME_COUNT) -> list[dict]:
    """成片均匀抽 count 帧到 frames_dir，返回 [{index,time,shot,path}]。"""
    frames_dir = utils.ensure_dir(frames_dir)
    duration = utils.probe_duration(out)
    result: list[dict] = []
    for i in range(count):
        t = duration * (i + 0.5) / count
        png = frames_dir / f"frame_{i + 1:02d}.png"
        try:
            utils.ffmpeg(["-ss", f"{t:.3f}", "-i", str(out), "-frames:v", "1",
                          "-q:v", "2", "-y", str(png)])
        except Exception as exc:  # noqa: BLE001
            LOG.warning("抽帧失败 t=%.2f: %s", t, exc)
            continue
        shot_id = ""
        for item in timeline:
            if item["start"] <= t < item["start"] + item["duration"] + 1e-6:
                shot_id = item["id"]
                break
        result.append({"index": i + 1, "time": round(t, 3),
                       "shot": shot_id, "path": str(png)})
    _step(f"抽帧 {len(result)} 张 → {frames_dir}")
    return result


def make_contact_sheet(frames: list[dict], out_png: Path, cols: int = 4,
                       rows: int = 2, cell: tuple[int, int] = (480, 270)) -> dict:
    """4×2 缩略图联系表（PIL）；失败返回 {"ok": False}。"""
    try:
        from PIL import Image, ImageDraw, ImageFont
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"PIL 不可用: {exc}"}
    if not frames:
        return {"ok": False, "error": "没有可用帧"}
    gap, pad = 10, 14
    label_h = 24
    cw, ch = cell
    width = pad * 2 + cols * cw + (cols - 1) * gap
    height = pad * 2 + rows * (ch + label_h) + (rows - 1) * gap
    sheet = Image.new("RGB", (width, height), (10, 10, 12))
    draw = ImageDraw.Draw(sheet)
    try:
        font = ImageFont.truetype("C:/Windows/Fonts/consola.ttf", 15)
    except Exception:  # noqa: BLE001
        font = ImageFont.load_default()
    for i, frame in enumerate(frames[:cols * rows]):
        row, col = divmod(i, cols)
        x = pad + col * (cw + gap)
        y = pad + row * (ch + label_h + gap)
        try:
            with Image.open(frame["path"]) as img:
                img = img.convert("RGB").resize((cw, ch), Image.LANCZOS)
                sheet.paste(img, (x, y))
        except Exception as exc:  # noqa: BLE001
            LOG.warning("缩略图失败 %s: %s", frame.get("path"), exc)
            continue
        draw.rectangle([x, y, x + cw - 1, y + ch - 1], outline=(212, 175, 55))
        label = f"{frame['time']:6.1f}s  {frame.get('shot') or ''}"
        draw.text((x + 4, y + ch + 3), label, fill=(230, 217, 176), font=font)
    out_png.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(out_png)
    return {"ok": True, "path": str(out_png), "size": [width, height],
            "grid": f"{cols}x{rows}", "frames": min(len(frames), cols * rows)}


def _source_freeze(entry: dict) -> dict:
    """源片时长达标分析：多余素材 / dissolve 延长 / 素材不足（冻结末帧）秒数。"""
    nominal = float(entry.get("nominal_duration") or 0.0)
    target = float(entry.get("target_duration") or nominal)
    ext_sec = max(0.0, target - nominal)
    source_dur = 0.0
    if entry.get("source"):
        try:
            source_dur = utils.probe_duration(entry["source"])
        except Exception:  # noqa: BLE001
            source_dur = 0.0
    source_extra = max(0.0, source_dur - nominal)
    shortfall = max(0.0, nominal - source_dur) if source_dur > 0 else 0.0
    return {"extension_sec": round(ext_sec, 4),
            "source_duration": round(source_dur, 4),
            "source_extra_sec": round(source_extra, 4),
            "shortfall_sec": round(shortfall, 4),
            "freeze_sec": round(max(0.0, nominal + ext_sec - source_dur), 4)
            if source_dur > 0 else 0.0}


def build_report(*, out: Path, episode_path: Path, shots_dir: Path, work: Path,
                 args, episode: dict, entries: list[dict], missing: list[dict],
                 cards: dict, norm_entries: list[dict], plan: dict,
                 boundaries: list[dict], timeline_mode: str,
                 episode_starts: list[float] | None, ass_stats: dict,
                 narration_stats: dict, ambience, ambience_db: float | None,
                 bgm: Path | None, media: dict, loudness: dict | None,
                 black: dict, frames: list[dict], sheet: dict,
                 timeline_shots: list[dict], edit_stats: dict,
                 unify_look: bool = True,
                 unify_filter: str | None = None) -> dict:
    starts = plan["starts"]
    total = plan["total"]
    clip_dev = [abs(float(e.get("deviation") or 0.0)) for e in norm_entries]
    nominal_dev = [abs(float(e.get("nominal_duration") or 0.0)
                       - float(e["duration"])) for e in norm_entries]
    max_clip_dev = round(max(clip_dev), 4) if clip_dev else 0.0
    mean_clip_dev = round(sum(clip_dev) / len(clip_dev), 4) if clip_dev else 0.0
    max_nom_dev = round(max(nominal_dev), 4) if nominal_dev else 0.0
    mean_nom_dev = round(sum(nominal_dev) / len(nominal_dev), 4) \
        if nominal_dev else 0.0
    timeline_check = {"episode_edit_start_available": bool(episode_starts),
                      "mode": timeline_mode}
    if episode_starts and len(episode_starts) == len(entries):
        deltas = [abs(a - b) for a, b in zip(starts, episode_starts)]
        timeline_check.update({
            "max_delta": round(max(deltas), 4),
            "mean_delta": round(sum(deltas) / len(deltas), 4),
            "note": ("帧对齐 episode._edit_start（硬切时间线，dissolve 不占时长）"
                     if max(deltas) <= 0.05 else
                     "与 episode._edit_start 有偏差（pilot 跳镜/旧转场计划，属预期）"),
        })
    elif episode_starts:
        timeline_check["note"] = "pilot 跳镜后镜头数与 episode 不同，未逐镜比对"
    narration_lines = sum(1 for e in entries
                          if str(e["shot"].get("line") or "").strip())
    coverage = (ass_stats.get("narration_events", 0) / narration_lines
                if narration_lines else 0.0)
    dissolves = [
        {"index": i, "from": entries[i]["id"], "to": entries[i + 1]["id"],
         "mode": b.get("mode"), "duration": round(float(b.get("duration") or 0.0), 3),
         "frames": int(b.get("frames") or 0),
         "freeze_sec": _source_freeze(norm_entries[i]).get("freeze_sec")}
        for i, b in enumerate(boundaries) if not b.get("cut")
    ]
    # 源素材时长分析（reuse 镜头需 ≥ episode 镜长；不足靠冻结末帧补齐 → 报告列出）
    freeze_info = {e["id"]: _source_freeze(e) for e in norm_entries}
    short_sources = [
        {"id": e["id"], "nominal": round(float(e["nominal_duration"]), 4),
         "source_duration": info["source_duration"],
         "shortfall_sec": info["shortfall_sec"]}
        for e in norm_entries
        for info in [freeze_info[e["id"]] if e["id"] in freeze_info else {}]
        if info.get("shortfall_sec", 0.0) > 0.05
    ]
    warn: list[str] = []
    if missing:
        warn.append(f"缺失镜头 {len(missing)} 个（pilot/生成未完成，已跳过顺接）")
    if short_sources:
        detail = "、".join(f"{s['id']}(-{s['shortfall_sec']:.2f}s)"
                          for s in short_sources[:12])
        warn.append(f"{len(short_sources)} 个镜头源素材短于镜长 >50ms，靠冻结末帧补齐："
                    f"{detail}")
    if abs(media.get("duration", 0.0) - total) > 0.3:
        warn.append(f"成片时长 {media.get('duration')}s 与目标 {total:.2f}s 偏差 >0.3s")
    if abs(total - sum(plan["nominal"])) > 0.2:
        warn.append(f"总时长 {total:.2f}s 与 episode 镜长和 "
                    f"{sum(plan['nominal']):.2f}s 偏差 >0.2s")
    if media.get("width") != args.width or media.get("height") != args.height:
        warn.append("成片分辨率与目标不一致")
    if abs(float(media.get("fps") or 0.0) - args.fps) > 0.3:
        warn.append("成片 fps 与目标不一致")
    if not media.get("has_audio"):
        warn.append("成片没有音轨")
    if black.get("count"):
        warn.append(f"blackdetect 命中 {black['count']} 段"
                    "（片尾淡出到黑/卡片黑底属正常，需人眼复核）")
    if max_clip_dev > 0.05:
        warn.append(f"镜头 clip 时长最大偏差 {max_clip_dev:.3f}s > 0.05s")
    if max_nom_dev > 0.05:
        warn.append(f"镜头可见时长最大偏差 {max_nom_dev:.3f}s > 0.05s")
    if loudness:
        lufs = float(loudness.get("i") or -99.0)
        if not (-18.0 <= lufs <= -14.0):
            warn.append(f"响度 {lufs:.1f} LUFS 偏离 -16±2")
    included_ids = {e["id"] for e in entries}
    return {
        "ok": True,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "out": str(out),
        "episode": str(episode_path),
        "shots_dir": str(shots_dir),
        "work": str(work),
        "args": {
            "title": args.title, "fps": args.fps,
            "width": args.width, "height": args.height,
            "transitions_requested": args.transitions,
            "transitions_used": timeline_mode,
            "ambience_db": ambience_db,
            "unify_look": unify_look,
            "shot_grade": bool(getattr(args, "shot_grade", True)),
            "fade_out_sec": ass_stats.get("fade_out_sec"),
            "episode_total_duration": episode.get("total_duration"),
        },
        "shot_adjust": {
            "enabled": bool(getattr(args, "shot_grade", True)),
            "grade_map": SHOT_GRADE,
            "source_offsets": SHOT_SOURCE_OFFSETS,
            "applied": [{"id": e["id"], "grade": e.get("shot_grade"),
                         "source_offset": e.get("source_offset") or 0.0}
                        for e in norm_entries
                        if e.get("shot_grade")
                        or float(e.get("source_offset") or 0.0) > 0.0],
            "note": ("QA warn 镜头的定向修复（依据 tmp/v61_3d/qa_review.md："
                     "v5_015 压暗提对比、v5_024 降饱和、v5_026/027/028 压暗降饱和、"
                     "v5_023 跳过现代白墙开头 0.7s）；不影响全片统一影调基线，"
                     "其余镜头只走统一影调。"),
        },
        "unify_look": {
            "enabled": bool(unify_look),
            "filter": unify_filter if unify_look else None,
            "components": {
                "color_curve": ("eq=contrast=1.035:saturation=1.05:"
                                "brightness=-0.004 + colorbalance "
                                "rs/bs/rm/bm/rh/bh=0.015/-0.020/0.010/-0.010/"
                                "0.020/-0.035（暖金高光 + 青绿暗部）"),
                "vignette": "angle=0.38（ffmpeg vignette，轻于默认 PI/5）",
                "grain": "noise=alls=3:allf=t+u（极轻颗粒）",
                "sharpen": {
                    "sharp_source": SHARPEN_CLOUD,
                    "soft_source": SHARPEN_LOCAL,
                    "soft_source_max_width": SOFT_SOURCE_MAX_WIDTH,
                    "note": "逐镜 normalize 阶段 unsharp=5:5:X:5:5:0（本地软素材 1.0 档）",
                    "calibration": SHARPEN_CALIBRATION,
                },
            },
            "source_tiers": {
                "sharp": sum(1 for e in norm_entries
                             if e.get("source_tier") == "sharp"),
                "soft": sum(1 for e in norm_entries
                            if e.get("source_tier") == "soft"),
                "black": sum(1 for e in norm_entries
                             if e.get("source_tier") == "black"),
            },
        },
        "timeline": {
            "shots_included": len(entries),
            "shots_total": len(entries) + len(missing),
            "cards": cards,
            "missing": missing,
            "mode": timeline_mode,
            "transition_count": sum(1 for b in boundaries if not b.get("cut")),
            "transition_loss": 0.0,
            "dissolves": dissolves,
            "total_seconds": round(total, 3),
            "total_frames": plan["total_frames"],
            "sum_shot_durations": round(sum(plan["nominal"]), 4),
            "fade_out_sec": ass_stats.get("fade_out_sec"),
            "shots": timeline_shots,
            "check": timeline_check,
        },
        "media": media,
        "loudness": {
            "integrated_lufs": (None if not loudness
                                else round(float(loudness["i"]), 2)),
            "true_peak_dbtp": (None if not loudness
                               else round(float(loudness["tp"]), 2)),
            "lra": (None if not loudness else round(float(loudness["lra"]), 2)),
        },
        "blackdetect": black,
        "subtitles": {
            **ass_stats,
            "narration_lines": narration_lines,
            "coverage_ratio": round(coverage, 4),
            "chapters": [CHAPTER_START_IDS[i] for i in CHAPTER_START_IDS
                         if i in included_ids],
        },
        "audio": {
            "narration": narration_stats,
            "ambience_db": ambience_db,
            "ambience": str(ambience) if ambience else None,
            "bgm": str(bgm) if bgm else None,
        },
        "shot_durations": {
            "max_abs_deviation": max_clip_dev,
            "mean_abs_deviation": mean_clip_dev,
            "nominal_max_abs_deviation": max_nom_dev,
            "nominal_mean_abs_deviation": mean_nom_dev,
            "short_sources": short_sources,
            "items": [{"id": e["id"], "target": round(e["nominal_duration"], 4),
                       "nominal_episode": round(float(e["duration"]), 4),
                       "probe": round(e["actual_duration"], 4),
                       "deviation": round(e["deviation"], 4),
                       "source": e["source_kind"],
                       "source_tier": e.get("source_tier"),
                       "source_width": e.get("source_width"),
                       "sharpen": e.get("sharpen"),
                       "shot_grade": e.get("shot_grade"),
                       "source_offset": e.get("source_offset") or 0.0,
                       **freeze_info.get(e["id"], {})}
                      for e in norm_entries],
        },
        "video_edit": edit_stats,
        "frames": {
            "dir": str(Path(frames[0]["path"]).parent) if frames else "",
            "count": len(frames), "items": frames,
            "contact_sheet": sheet,
        },
        "warnings": warn,
    }


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------
def _timeline_shots(norm_entries: list[dict], starts: list[float],
                    boundaries: list[dict]) -> list[dict]:
    out = []
    for i, (entry, start) in enumerate(zip(norm_entries, starts)):
        tr = "end"
        if i < len(boundaries):
            tr = str(boundaries[i].get("mode") or "cut")
        out.append({
            "id": entry["id"],
            "start": round(float(start), 3),
            "duration": round(entry["nominal_duration"], 4),
            "render_duration": round(entry["target_duration"], 4),
            "transition_out": tr,
            "source": entry["source_kind"],
            "line": bool(str(entry["shot"].get("line") or "").strip()),
        })
    return out


def run(args) -> int:
    t_start = time.time()
    episode_path = Path(args.episode).resolve()
    shots_dir = Path(args.shots).resolve()
    work = utils.ensure_dir(Path(args.work).resolve())
    out = Path(args.out).resolve()
    fps, width, height = int(args.fps), int(args.width), int(args.height)
    ambience_db = getattr(args, "ambience", None)
    if ambience_db is None:
        ambience_db = getattr(args, "ambience_db", None)
    if ambience_db is None:
        ambience_db = DEFAULT_AMBIENCE_DB
    ambience_on = (not getattr(args, "no_ambience", False)
                   and abs(float(ambience_db)) > 1e-9)
    fade_out = float(getattr(args, "fade_out", DEFAULT_FADE_OUT) or 0.0)
    _step(f"episode={episode_path}")
    _step(f"shots={shots_dir}  out={out}  work={work}  {width}x{height}@{fps}")
    _step(f"ambience={'on @ %+.1fdB' % ambience_db if ambience_on else 'off'}  "
          f"fade_out={fade_out:.2f}s")

    episode, shots = load_episode(episode_path)
    only = ({x.strip() for x in str(args.only).split(",") if x.strip()}
            if getattr(args, "only", None) else None)
    entries, missing, cards = classify_shots(
        shots, shots_dir, only=only, limit=getattr(args, "limit", None))
    if not entries:
        raise RuntimeError(f"没有可用镜头：{shots_dir} 为空或 --only 过滤后无匹配")
    timeline_mode = args.transitions
    if timeline_mode == "auto":
        timeline_mode = _episode_mode_for([e["shot"] for e in entries])
    if timeline_mode == "cards":  # 兼容旧参数名
        timeline_mode = "cinema"
    _step(f"镜头：可用 {len(entries)} / 缺失 {len(missing)}；时间线模式={timeline_mode}；"
          f"卡片={ {k: v['source'] for k, v in cards.items()} }")

    fonts.ensure_fonts(work)
    serif = _ensure_serif_fonts(work)
    _step(f"字体：{', '.join(Path(p).name for p in serif) or '仅系统字体'}")

    raw_boundaries = build_raw_boundaries(
        [e["shot"] for e in entries], timeline_mode,
        chapter_dissolve=not bool(getattr(args, "no_chapter_dissolve", False)))
    boundaries = resolve_boundaries([e["shot"] for e in entries], raw_boundaries)
    episode_starts = None
    if len(entries) == len(shots):
        episode_starts = _episode_starts([e["shot"] for e in entries])
    plan = plan_frame_timeline(entries, boundaries, fps, episode_starts,
                               episode.get("total_duration"))
    starts, total = plan["starts"], plan["total"]
    _step(f"时间线：{len(entries)} 镜 / 总长 {total:.3f}s（episode 镜长和 "
          f"{sum(plan['nominal']):.3f}s）/ {len(plan['boundary_frames'])} 个剪辑点 / "
          f"{sum(1 for b in boundaries if not b.get('cut'))} 个 dissolve")

    norm_entries = normalize_all(entries, plan, work, fps, width, height,
                                 force=bool(getattr(args, "force", False)),
                                 apply_adjust=bool(getattr(args, "shot_grade",
                                                          True)))

    # 视频
    video = assemble_video(norm_entries, boundaries, work, fps, width, height,
                           total)
    video_dur = utils.probe_duration(video)
    _step(f"视频轨：{video}（{video_dur:.3f}s）")

    # 音频：旁白 + 可选 H3 环境声床
    entries_audio = _copy_shot_audio(norm_entries, work)
    narration, narration_stats = build_narration(entries_audio, starts, work,
                                                 total)
    ambience = None
    if ambience_on:
        ambience = build_ambience(entries_audio, starts, work, total,
                                  float(ambience_db))
        if ambience is not None:
            narration = mix_ambience(narration, ambience, work, total)
            narration_stats["duration"] = round(
                utils.probe_duration(narration), 4)
            narration_stats["ambience_mixed"] = True
    _step(f"旁白：{narration}（{narration_stats.get('duration')}s / "
          f"{narration_stats.get('engine')}）")

    # 字幕（新写 ASS，不吃旧 final.ass）
    ass_stats = build_ass(entries, starts, work, width, height,
                          title=args.title, total=total, fade_out=fade_out,
                          title_sub=getattr(args, "title_sub",
                                            DEFAULT_TITLE_SUB))
    _step(f"字幕：{ass_stats['path']}（正文 {ass_stats['narration_events']} 条 / "
          f"章节 {ass_stats['chapter_events'] // 2} / "
          f"事件 {ass_stats['total_events']}）")

    # BGM + 编码（统一影调：色调曲线/暗角/极轻颗粒 + 逐镜锐化已在 normalize 阶段做）
    catalog = v5_catalog.load_catalog()
    style = (catalog.get("styles") or {}).get("museum_gold") or {}
    bgm = v5_audio.ensure_bgm(style, work)
    _step(f"BGM：{bgm}")
    unify = bool(getattr(args, "unify_look", True))
    grade_filter = UNIFY_FILTER if unify else None
    cloud_n = sum(1 for e in norm_entries if e.get("source_tier") == "sharp")
    soft_n = sum(1 for e in norm_entries if e.get("source_tier") == "soft")
    _step(f"统一影调：{'on' if unify else 'off'}（锐利源 {cloud_n} / 软源 "
          f"{soft_n}；unsharp {SHARPEN_CLOUD}/{SHARPEN_LOCAL}）")
    edit_stats = encode_final(work, out, total, fps, width, height, bgm,
                              grade_filter=grade_filter)

    # 报告要素
    media = measure_media(out)
    try:
        loudness = render_mod.measure_loudness(out)
    except Exception as exc:  # noqa: BLE001
        LOG.warning("响度测量失败: %s", exc)
        loudness = None
    black = detect_black(out)
    timeline_shots = _timeline_shots(norm_entries, starts, boundaries)
    frames_dir = Path(args.frames_dir).resolve() \
        if getattr(args, "frames_dir", None) else (out.parent / "frames_final")
    frames = extract_frames(out, frames_dir, timeline_shots)
    if getattr(args, "no_contact_sheet", False):
        sheet = {"ok": False, "skipped": True}
    else:
        sheet_png = Path(args.contact_sheet).resolve() \
            if getattr(args, "contact_sheet", None) \
            else (out.parent / "contact_sheet.png")
        sheet = make_contact_sheet(frames, sheet_png)

    report = build_report(
        out=out, episode_path=episode_path, shots_dir=shots_dir, work=work,
        args=args, episode=episode, entries=entries, missing=missing,
        cards=cards, norm_entries=norm_entries, plan=plan,
        boundaries=boundaries, timeline_mode=timeline_mode,
        episode_starts=episode_starts, ass_stats=ass_stats,
        narration_stats=narration_stats, ambience=ambience,
        ambience_db=ambience_db if ambience_on else None, bgm=bgm,
        media=media, loudness=loudness, black=black, frames=frames,
        sheet=sheet, timeline_shots=timeline_shots, edit_stats=edit_stats,
        unify_look=unify, unify_filter=grade_filter)
    report["elapsed_sec"] = round(time.time() - t_start, 1)
    report_path = work / REPORT_NAME
    _json_write(report_path, report)
    _step(f"报告：{report_path}")
    _step(f"完成：{out}（{media.get('duration')}s / {media.get('width')}x"
          f"{media.get('height')}@{media.get('fps')} / LUFS "
          f"{report['loudness']['integrated_lufs']} / blackdetect "
          f"{black.get('count')} / 字幕 {ass_stats['total_events']} 事件）")
    print(json.dumps({"ok": True, "out": str(out), "report": str(report_path),
                      "duration": media.get("duration"),
                      "resolution": f"{media.get('width')}x{media.get('height')}",
                      "fps": media.get("fps"),
                      "loudness_lufs": report["loudness"]["integrated_lufs"],
                      "blackdetect": black.get("count"),
                      "subtitles": ass_stats["total_events"],
                      "shots_included": len(entries),
                      "missing": len(missing),
                      "contact_sheet": sheet.get("path")},
                     ensure_ascii=False), flush=True)
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="pipeline.cinema3d_assemble",
        description="V6.5 全 3D 成片剪辑器（H3 镜头 + 旁白 + H3 环境声 + BGM + ASS + 报告）")
    p.add_argument("--episode", default=str(DEFAULT_EPISODE3D),
                   help="episode JSON（默认 outputs/templates_v5/relic_talk_3d/work/episode3d.json）")
    p.add_argument("--shots", default=str(DEFAULT_SHOTS_DIR),
                   help="3D 镜头目录（默认 outputs/templates_v5/relic_talk_3d/shots）")
    p.add_argument("--out", default=str(DEFAULT_OUT),
                   help="成片输出 mp4（默认 outputs/templates_v5/relic_talk_3d/final.mp4）")
    p.add_argument("--work", default=str(DEFAULT_WORK),
                   help="工作目录（默认 outputs/templates_v5/relic_talk_3d/work_assemble）")
    p.add_argument("--title", default=DEFAULT_TITLE, help="片名（卡片标题兜底）")
    p.add_argument("--title-sub", default=DEFAULT_TITLE_SUB,
                   help="片头副标题（卡片 subtitle 为空/与主标题相同时）")
    p.add_argument("--fps", type=int, default=config.FPS)
    p.add_argument("--width", type=int, default=config.VIDEO_W)
    p.add_argument("--height", type=int, default=config.VIDEO_H)
    p.add_argument("--ambience-db", type=float, default=None,
                   help="H3 原声环境床音量 dB（默认 -20；传 0 关闭）")
    p.add_argument("--ambience", type=float, default=None,
                   help="--ambience-db 的旧别名")
    p.add_argument("--no-ambience", action="store_true", help="关闭 H3 原声环境床")
    p.add_argument("--fade-out", type=float, default=DEFAULT_FADE_OUT,
                   help="片尾淡出到黑秒数（默认 1.0；0 关闭）")
    p.add_argument("--unify-look", action=argparse.BooleanOptionalAction,
                   default=True,
                   help="统一影调（默认开）：逐镜锐化 + 整片色调曲线/轻暗角/极轻颗粒；"
                        "--no-unify-look 关闭")
    p.add_argument("--shot-grade", action=argparse.BooleanOptionalAction,
                   default=True,
                   help="QA 定向逐镜微调色/入点（默认开；仅对 SHOT_GRADE/"
                        "SHOT_SOURCE_OFFSETS 中列出的镜头生效）")
    p.add_argument("--transitions", choices=("auto", "cut", "cinema", "episode",
                                             "cards"),
                   default="auto",
                   help="auto=按 episode._edit_start 判定（默认 cinema：硬切+片头/章节 "
                        "dissolve）；cut=全硬切；episode=按 edit.transition_out 计划")
    p.add_argument("--no-chapter-dissolve", action="store_true",
                   help="cinema 模式下关闭三件国宝章节 dissolve")
    p.add_argument("--frames-dir", default=None,
                   help="抽帧目录（默认 <out目录>/frames_final）")
    p.add_argument("--contact-sheet", default=None,
                   help="contact_sheet.png 路径（默认 <out目录>/contact_sheet.png）")
    p.add_argument("--no-contact-sheet", action="store_true")
    p.add_argument("--only", default=None,
                   help="只剪这些镜头 id（逗号分隔，调试用）")
    p.add_argument("--limit", type=int, default=None,
                   help="只剪前 N 镜（调试用）")
    p.add_argument("--force", action="store_true", help="忽略标准化缓存重做")
    p.add_argument("--verbose", action="store_true")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    utils.setup_logging(bool(args.verbose))
    try:
        return run(args)
    except Exception as exc:  # noqa: BLE001
        LOG.exception("剪辑失败: %s", exc)
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False),
              flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
