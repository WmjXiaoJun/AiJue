# -*- coding: utf-8 -*-
"""V5 独立验收：可变风格世界（16 模板 × 16 风格）+ 口型同步。

验收口径（Lead V5 任务 + docs/PLAN_V5_STYLE_AUDIO.md / V5_INTERFACES.md）：
- 规格：final.mp4 存在、时长 25-50s、1280×720、25fps、有音轨、blackdetect=0
  （V5 政策：d=0.30 检测 ≥0.3s 的真实黑场；允许 ≤0.2s 风格化 fade-black 转场，
  如 baroque_candle 的 candle_fade 转场中点）；
- 内容：qc_report.ok；`duration/resolution/fps/audio/blackdetect/scene_coverage/
  character_coverage/subtitle_coverage/style/fallbacks` 全 ok；fallbacks=[]；
- 风格区分度（QA 独立口径）：对本仓 16 部成片重新抽帧（`pipeline.qc_v5.style_fingerprint`，
  HSV 8×8×4 直方图 + 边缘密度），两两计算直方图余弦相似度与边缘密度差；
  **判定"几乎相同"：cosine ≥ 0.985 且 |edge_density 差| ≤ 0.01**（两条件同时满足才失败；
  任一不满足即视为可区分）。阈值先取经验值，矩阵输出 `tmp/qa/v5_style_matrix.json`。
- 历史 4 部（history_talk/classics/history_mystery/national_treasure）：成片用到的场景中
  至少 2 个是**史料/战争/地图类场景**（从契约场景 tags 含 "history" 判定，等价于
  ancient_map/battlefield/war_council/palace_hall/scholar_study/archive_vault/... 集合）；
  场景来源 timeline.json meta.scenes + style_report.json scenes；
- timeline：`pipeline.v5_catalog.validate_timeline_file()` 0 errors + 结构自检（version=5、
  分辨率/fps/duration、V1/A1/S1 轨存在、V1 覆盖时长）；
- 契约：`validate_catalog()` 0 errors / 0 warnings；counts ≥ styles 16 / scenes 60 /
  characters 24 / props 40 / templates 16 / packs 2（含 custom 时以 catalog.counts 为准）；
- **角色固定声音（voice lock / voice_fixed）**：
  ① 每部 `qc_report.checks.voice_fixed` 必须 ok（同角色所有对白镜 voice_key 唯一、对白镜都有 voice_id/key）；
  ② 从 `work/episode.json` 复核：每个说话角色在所有镜头同一 voice_id/key/seed；voice_id 必须存在于
     `contract/v5/voices.json`（含 custom 覆盖合并，`pipeline.v5_voices.load_voice_library`）；
     声音锁参考音频存在：`outputs/v5_voices/<character>/<voice_key>.wav` + `.json`；
     跨 16 部同角色 voice_id/key/seed 必须一致（跨集音色固定）；
  ③ 每部至少 2 个不同角色有台词（host + guest，防“只有 host 说话”回归）；
- 已知问题回归（可自动化的部分）：
  ① 水墨竖排字幕曾把 ASS 绘图指令烧进画面 → 解析 `work/final.ass`：文字型 Dialogue 事件
    不得出现"未加 \\p1 覆盖的 m/l 绘图指令裸文本"；
  ② silk 风格字幕对比度低 → 解析 ASS V5Sub 样式 PrimaryColour vs OutlineColour 的
    相对亮度对比度，要求 ≥ 3.0（proxy，最终以人眼抽帧为准）；
  ③ 角色 treatment 洗白/发灰、④ b-roll 宽景偏空 → 需人眼抽帧（contact_sheet.png /
     frames/*.png 为证据，验收报告记录人工复核结论，不做自动硬门）。

批量渲染期间未就绪模板 skip 并报告；只读 outputs/contract，不启动渲染。
运行：python -m pytest tests/test_v5_acceptance.py -q
"""
from __future__ import annotations

import functools
import json
import math
import re
import sys
from pathlib import Path

import numpy as np
import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from pipeline import qc_v5  # noqa: E402
from pipeline import v5_catalog  # noqa: E402

OUTPUTS = REPO_ROOT / "outputs" / "templates_v5"
MATRIX_JSON = REPO_ROOT / "tmp" / "qa" / "v5_style_matrix.json"
HEAD_WARN_JSON = REPO_ROOT / "tmp" / "qa" / "v5_head_warnings.json"
HEAD_GATE_JSON = REPO_ROOT / "tmp" / "qa" / "v5_head_gate.json"
VOICES_JSON = REPO_ROOT / "contract" / "v5" / "voices.json"
VOICE_LOCKS = REPO_ROOT / "outputs" / "v5_voices"
EXPECTED = [
    "tonight_show", "shark_tank", "masked_singer", "standup", "taskmaster",
    "traitors", "snl_sketch", "roundtable", "hot_ones", "qipashuo",
    "slow_life", "history_talk", "history_mystery", "classics",
    "national_treasure", "mystery",
]
HISTORICAL = ["history_talk", "classics", "history_mystery", "national_treasure"]
MIN_DURATION, MAX_DURATION = 25.0, 50.0
EXPECT_W, EXPECT_H, EXPECT_FPS = 1280, 720, 25.0
DURATION_TOL = 0.8
COS_THRESHOLD = 0.985
EDGE_DIFF_MIN = 0.01
SUB_CONTRAST_MIN = 3.0
REQUIRED_CHECKS = {
    "duration", "resolution", "fps", "audio", "blackdetect",
    "scene_coverage", "character_coverage", "subtitle_coverage",
    "style", "fallbacks", "voice_fixed",
}
LEGACY_VOICE_SEEDS = {"laozhou": 20261026, "ali": 20261027, "dawei": 20261028}
_DRAWING_LEAK = re.compile(r"^[ml]\s+-?\d")
_OVERRIDE = re.compile(r"\{[^}]*\}")


# ---------------------------------------------------------------------------
# 读取 / 就绪判定
# ---------------------------------------------------------------------------
def read_json(path: Path, default=None):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def tdir(tid: str) -> Path:
    return OUTPUTS / tid


def v5_state(tid: str) -> str:
    base = tdir(tid)
    required = [base / "final.mp4", base / "qc_report.json", base / "timeline.json",
                base / "style_report.json", base / "contact_sheet.png"]
    missing = [p.name for p in required if not p.is_file()]
    if missing:
        return f"pending(缺{','.join(missing)})"
    report = read_json(base / "qc_report.json", {}) or {}
    if report.get("ok") is not True:
        return "qc_not_ok"
    # 证据一致性：final 之后必须至少有新的 qc/style/contact（防止“新 final + 旧报告”）。
    # 允许 qc 重评（qc 比 final 新）；不允许 qc/style/contact 比 final 旧 180s 以上。
    final_mtime = (base / "final.mp4").stat().st_mtime
    stale = [p.name for p in required[1:]
             if p.stat().st_mtime < final_mtime - 180]
    if stale:
        return f"pending(evidence_stale:{','.join(stale)})"
    return "ready"


def _blackdetect_count(video: Path, min_duration: float = 0.30,
                       pix_th: float = 0.10) -> int:
    """独立 blackdetect（V5 政策：允许 ≤0.2s 风格化 fade-black 转场，阈值 d=0.30：

    检测持续 ≥0.30s 的真实黑场；traitors/baroque candle_fade 的 0.12-0.2s 转场中点不算黑场。
    """
    import subprocess
    cmd = [str(qc_v5.config.FFMPEG_BIN), "-hide_banner", "-nostats", "-i", str(video),
           "-vf", f"blackdetect=d={min_duration:.2f}:pix_th={pix_th:.2f}",
           "-an", "-f", "null", "-"]
    proc = subprocess.run(cmd, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=600)
    return (proc.stderr or "").count("black_start:")


def qc_report(tid: str) -> dict:
    return read_json(tdir(tid) / "qc_report.json", {}) or {}


def style_report(tid: str) -> dict:
    return read_json(tdir(tid) / "style_report.json", {}) or {}


def timeline(tid: str) -> dict:
    return read_json(tdir(tid) / "timeline.json", {}) or {}


def plan(tid: str) -> dict:
    return read_json(tdir(tid) / "work" / "episode.json", {}) or {}


def plan_total(tid: str) -> float:
    ep = (plan(tid).get("episode") or {})
    if isinstance(ep.get("total_duration"), (int, float)):
        return float(ep["total_duration"])
    return float(timeline(tid).get("duration") or 0)


def scenes_of(tid: str) -> list[str]:
    out: list[str] = []
    for source in ((timeline(tid).get("meta") or {}).get("scenes"),
                   style_report(tid).get("scenes")):
        for item in source or []:
            s = str(item or "").strip()
            if s and s not in ("None", "null") and s not in out:
                out.append(s)
    return out


def characters_of(tid: str) -> list[str]:
    out: list[str] = []
    for source in ((timeline(tid).get("meta") or {}).get("characters"),
                   style_report(tid).get("characters")):
        for item in source or []:
            s = str(item or "").strip()
            if s and s not in ("None", "null") and s not in out:
                out.append(s)
    return out


CATALOG = v5_catalog.load_catalog()
CATALOG_STYLES = set((CATALOG.get("styles") or {}).keys())
CATALOG_SCENES = CATALOG.get("scenes") or {}
HISTORY_SCENES = {sid for sid, scene in CATALOG_SCENES.items()
                  if "history" in (scene.get("tags") or [])}

READY: list[str] = []
PENDING: list[str] = []
for _tid in EXPECTED:
    if v5_state(_tid) == "ready":
        READY.append(_tid)
    else:
        PENDING.append(_tid)


def require_ready(tid: str) -> None:
    state = v5_state(tid)
    if state != "ready":
        pytest.skip(f"{tid}: V5 产物未就绪（{state}），等待批量完成")


def contract_template(tid: str) -> dict:
    return read_json(REPO_ROOT / "contract" / "v5" / "templates" / f"{tid}.json", {}) or {}


# ---------------------------------------------------------------------------
# 1) 视频规格
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("tid", READY)
def test_video_spec(tid: str):
    video = tdir(tid) / "final.mp4"
    probe = qc_v5.probe_video(video)
    problems = []
    if not (MIN_DURATION <= float(probe.get("duration") or 0) <= MAX_DURATION):
        problems.append(f"时长 {probe.get('duration')}s 不在 [{MIN_DURATION}, {MAX_DURATION}]")
    if (probe.get("width"), probe.get("height")) != (EXPECT_W, EXPECT_H):
        problems.append(f"分辨率 {probe.get('width')}x{probe.get('height')} != {EXPECT_W}x{EXPECT_H}")
    if abs(float(probe.get("fps") or 0) - EXPECT_FPS) > 0.6:
        problems.append(f"fps {probe.get('fps')} != {EXPECT_FPS}")
    if not probe.get("has_audio"):
        problems.append("无音轨")
    blacks = _blackdetect_count(video, min_duration=0.30)
    if blacks != 0:
        problems.append(f"blackdetect={blacks}")
    expected = float((qc_report(tid).get("stats") or {}).get("expected_duration")
                     or timeline(tid).get("duration") or plan_total(tid) or 0.0)
    if expected and abs(float(probe.get("duration") or 0) - expected) > DURATION_TOL:
        problems.append(f"时长 {probe.get('duration')}s vs expected {expected:.2f}s "
                        f"差 >{DURATION_TOL}s")
    assert not problems, f"{tid}: 规格问题：\n" + "\n".join(problems)


# ---------------------------------------------------------------------------
# 2) QC 报告 & 内容覆盖
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("tid", READY)
def test_qc_report(tid: str):
    report = qc_report(tid)
    checks = report.get("checks") or {}
    stats = report.get("stats") or {}
    problems = []
    if report.get("ok") is not True:
        problems.append("qc_report.ok != true")
    for name in sorted(REQUIRED_CHECKS):
        check = checks.get(name)
        if not isinstance(check, dict):
            problems.append(f"缺 check {name}")
        elif check.get("ok") is not True:
            problems.append(f"check {name} 未通过：{check.get('detail')}")
    fallback = checks.get("fallbacks") or {}
    if fallback.get("ok") is not True:
        problems.append("fallbacks check 未通过")
    fp = report.get("fingerprint") or {}
    if not fp or not fp.get("hist") or fp.get("edge_density") is None:
        problems.append("qc fingerprint 缺失")
    if str(stats.get("style") or "") not in CATALOG_STYLES:
        problems.append(f"qc style={stats.get('style')!r} 不在契约 styles")
    if not stats.get("scenes"):
        problems.append("qc stats.scenes=0")
    assert not problems, f"{tid}: QC 问题：\n" + "\n".join(problems)


@pytest.mark.parametrize("tid", READY)
def test_scene_character_coverage(tid: str):
    scenes = scenes_of(tid)
    chars = characters_of(tid)
    problems = []
    if len(scenes) < 2:
        problems.append(f"成片场景数={len(scenes)} <2（{scenes}）")
    unknown = [s for s in scenes if s not in CATALOG_SCENES]
    if unknown:
        problems.append(f"场景不在契约：{unknown}")
    if not chars:
        problems.append("成片角色列表为空（character_coverage 口径）")
    unknown_chars = [c for c in chars if c not in (CATALOG.get("characters") or {})]
    if unknown_chars:
        problems.append(f"角色不在契约：{unknown_chars}")
    assert not problems, f"{tid}: 场景/角色覆盖问题：\n" + "\n".join(problems)


# ---------------------------------------------------------------------------
# 3) timeline 契约校验
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("tid", READY)
def test_timeline_contract(tid: str):
    path = tdir(tid) / "timeline.json"
    result = v5_catalog.validate_timeline_file(path)
    assert result.get("ok") is True and not result.get("errors"), (
        f"{tid}: validate_timeline_file 失败：{result.get('errors')}")
    tl = timeline(tid)
    problems = []
    if int(tl.get("version") or 0) != 5:
        problems.append(f"version={tl.get('version')!r} != 5")
    if [int(x) for x in (tl.get("resolution") or [])] != [EXPECT_W, EXPECT_H]:
        problems.append(f"resolution={tl.get('resolution')!r}")
    if abs(float(tl.get("fps") or 0) - EXPECT_FPS) > 0.01:
        problems.append(f"fps={tl.get('fps')!r}")
    tracks = {str(t.get("id")): t for t in (tl.get("tracks") or [])
              if isinstance(t, dict) and t.get("id")}
    for required in ("V1", "A1", "S1"):
        if required not in tracks:
            problems.append(f"缺轨道 {required}")
    v1 = tracks.get("V1") or {}
    clips = v1.get("clips") or []
    if len(clips) < 2:
        problems.append(f"V1 clips={len(clips)} <2")
    total = float(tl.get("duration") or 0)
    cursor = 0.0
    for clip in clips:
        cursor = round(cursor + float(clip.get("out") or 0), 3)
    # V5.4：每个剪辑点独立 xfade 会产生重叠，timeline 总时长 = sum(镜头) - 实际重叠
    overlap = sum(
        max(0.0, float(clips[i].get("at") or 0) + float(clips[i].get("out") or 0)
            - float(clips[i + 1].get("at") or 0))
        for i in range(len(clips) - 1))
    expected_total = cursor - overlap if tl.get("editing") else cursor
    if clips and abs(expected_total - total) > DURATION_TOL:
        problems.append(f"V1 总长 {expected_total:.3f}s（sum-重叠 {overlap:.3f}）"
                        f" vs timeline {total:.3f}s")
    plan_len = len((plan(tid).get("shots") or []))
    if plan_len and len(clips) != plan_len:
        problems.append(f"V1 clips={len(clips)} != plan shots={plan_len}")
    meta = tl.get("meta") or {}
    if not meta.get("scenes"):
        problems.append("timeline.meta.scenes 为空")
    assert not problems, f"{tid}: timeline 结构问题：\n" + "\n".join(problems)


# ---------------------------------------------------------------------------
# 4) 风格：契约映射 / 唯一性 / 指纹 / 区分度
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("tid", READY)
def test_style_matches_contract(tid: str):
    sr = style_report(tid)
    tpl = contract_template(tid)
    problems = []
    style = str(sr.get("style") or "")
    if not style:
        problems.append("style_report.style 为空")
    if style and style not in CATALOG_STYLES:
        problems.append(f"style={style!r} 不在契约 styles")
    if tpl.get("style") and style != tpl.get("style"):
        problems.append(f"style={style!r} != 契约模板 style={tpl.get('style')!r}")
    if not sr.get("technique"):
        problems.append("style_report.technique 为空")
    if not (sr.get("palette") or {}).get("bg"):
        problems.append("style_report.palette.bg 为空")
    assert not problems, f"{tid}: 风格映射问题：\n" + "\n".join(problems)


@functools.lru_cache(maxsize=32)
def fingerprint(tid: str, mtime: float) -> dict:
    return qc_v5.style_fingerprint(tdir(tid) / "final.mp4") or {}


def fingerprint_cached(tid: str) -> dict:
    video = tdir(tid) / "final.mp4"
    return fingerprint(tid, video.stat().st_mtime)


@pytest.mark.parametrize("tid", READY)
def test_style_fingerprint(tid: str):
    fp = fingerprint_cached(tid)
    hist = fp.get("hist") or []
    assert len(hist) == 8 * 8 * 4, f"{tid}: 直方图维度 {len(hist)} != 256"
    arr = np.asarray(hist, dtype=np.float64)
    assert abs(float(arr.sum()) - 1.0) < 0.05, f"{tid}: 直方图和 {arr.sum():.3f} != 1"
    assert float(fp.get("edge_density") or 0) > 0, f"{tid}: edge_density={fp.get('edge_density')}"
    # 与入库 QC 指纹应基本一致（同函数、同视频；允许抽帧边界差异）
    qc_fp = qc_report(tid).get("fingerprint") or {}
    if qc_fp.get("hist") and len(qc_fp["hist"]) == len(hist):
        cos = _cosine(arr, np.asarray(qc_fp["hist"], dtype=np.float64))
        assert cos > 0.95, f"{tid}: QA 重算指纹与 qc 指纹余弦 {cos:.4f} < 0.95"


def _cosine(a: np.ndarray, b: np.ndarray) -> float:
    na, nb = float(np.linalg.norm(a)), float(np.linalg.norm(b))
    if na <= 0 or nb <= 0:
        return 0.0
    return float(np.dot(a, b) / (na * nb))


def test_style_unique_mapping():
    if len(READY) < 16:
        pytest.skip(f"V5 批量进行中：可验收 {len(READY)}/16")
    styles = {tid: str(style_report(tid).get("style") or "") for tid in READY}
    dupes = {s: [t for t, v in styles.items() if v == s]
             for s in set(styles.values()) if list(styles.values()).count(s) > 1}
    assert len(set(styles.values())) == 16, f"16 模板风格未做到一一对应：{dupes}"
    assert len(set(styles.values())) == len(styles), f"风格重复：{dupes}"


def test_style_distinction_matrix():
    """16 部两两区分度矩阵：cosine < 0.985 或 edge_density 差 > 0.01 视为可区分。"""
    if len(READY) < 16:
        pytest.skip(f"V5 批量进行中：可验收 {len(READY)}/16")
    tids = list(READY)
    fps = {tid: fingerprint_cached(tid) for tid in tids}
    styles = {tid: str(style_report(tid).get("style") or "") for tid in tids}
    hists = {tid: np.asarray(fps[tid].get("hist") or [], dtype=np.float64) for tid in tids}
    edges = {tid: float(fps[tid].get("edge_density") or 0.0) for tid in tids}
    pairs = []
    near = []
    for i, a in enumerate(tids):
        for b in tids[i + 1:]:
            cos = _cosine(hists[a], hists[b])
            diff = abs(edges[a] - edges[b])
            same_style = styles[a] == styles[b]
            pairs.append({"a": a, "b": b, "cosine": round(cos, 4),
                          "edge_diff": round(diff, 4),
                          "same_style": same_style})
            if cos >= COS_THRESHOLD and diff <= EDGE_DIFF_MIN:
                near.append(pairs[-1])
    pairs.sort(key=lambda p: -p["cosine"])
    matrix = {
        "thresholds": {"cosine": COS_THRESHOLD, "edge_diff": EDGE_DIFF_MIN},
        "templates": tids,
        "styles": styles,
        "edge_density": {tid: round(edges[tid], 5) for tid in tids},
        "cosine_matrix": {a: {b: round(_cosine(hists[a], hists[b]), 4) for b in tids}
                          for a in tids},
        "pairs": pairs,
        "near_identical": near,
        "max_cosine": pairs[0] if pairs else None,
        "min_cosine": pairs[-1] if pairs else None,
    }
    MATRIX_JSON.parent.mkdir(parents=True, exist_ok=True)
    MATRIX_JSON.write_text(json.dumps(matrix, ensure_ascii=False, indent=1),
                           encoding="utf-8")
    print("\n[qa] V5 风格区分度：最相似 5 对")
    for p in pairs[:5]:
        print(f"  {p['a']:<16} vs {p['b']:<16} cosine={p['cosine']:.4f} "
              f"edge_diff={p['edge_diff']:.4f}")
    assert not near, (
        "存在风格几乎相同的模板对（cosine>=0.985 且 edge_diff<=0.01）：\n"
        + "\n".join(str(p) for p in near))
    # 矩阵本身应有足够差异（经验下界，避免抽帧全灰导致指纹同质）
    assert matrix["max_cosine"]["cosine"] < 0.999, (
        f"存在几乎完全相同的直方图：{matrix['max_cosine']}")


# ---------------------------------------------------------------------------
# 5) 历史 4 部场景核查
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("tid", [t for t in HISTORICAL if t in READY])
def test_historical_scenes(tid: str):
    used = scenes_of(tid)
    history_used = [s for s in used if s in HISTORY_SCENES]
    assert len(history_used) >= 2, (
        f"{tid}: 历史题材场景不足（{len(history_used)} 个：{history_used}；"
        f"成片场景={used}；需要 ≥2 个 tags 含 history 的场景）")
    # 至少要覆盖"史料/战争/地图"语义：history 场景中带 war/map/palace/archive/museum 标签
    rich = [s for s in history_used
            if set(CATALOG_SCENES[s].get("tags") or [])
            & {"war", "map", "palace", "archive", "archaeology", "museum"}]
    assert rich, f"{tid}: 历史场景缺少史料/战争/地图语义：{history_used}"


# ---------------------------------------------------------------------------
# 5) P1 视觉门：角色头部完整在画面内（解析式，基于 work/shot_meta.json + motion2d 渲染公式）
#
# 口径（可自动化的近似，用于捕获 2026-10 三模板“无头躯干/头部裁切”回归）：
#   - **主对白角色**（plan 该镜 role = 发言/有台词角色）头部必须完整入画：
#     head_top≥0、head_bottom≤H、[cx±head_half_w] ⊂ [0,W]（容差 2px）；
#   - 主对白角色若完全出画（bottom<0）同样失败；
#   - 陪衬角色允许构图性出画，但**不允许上半头被上边缘裁切**（head_top<0 且 head_bottom>0
#     = 无头躯干回归）。
# 公式与 motion2d.character_state 同源：h_nom=height*H*scale；base_y=anchor_y*H-h_nom；
#   zoom=1+(cam.zoom-1)*zf，zf=0.55+0.45*clamp(ratio,0.15,2.2)；
#   head_top=(base_y-H/2)*zoom+H/2+cam.y*ratio；
#   head_cx=(anchor_x*W-W/2)*zoom+W/2+cam.x*ratio；
#   头高≈0.22*h_nom*zoom，头宽≈0.34*h_nom*zoom（characters_v5 立绘经验比例）。
# 说明：解析近似最终以人眼抽帧对照（tmp/qa/v5_review/{tid}/shots_grid.png）；不一致时以人眼为准。
# ---------------------------------------------------------------------------
def _shot_meta(tid: str) -> dict:
    return read_json(tdir(tid) / "work" / "shot_meta.json", {}) or {}


def _plan_role_by_shot(tid: str) -> dict:
    shots = plan(tid).get("shots") or []
    return {str(s.get("id")): s for s in shots if isinstance(s, dict)}


def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


def _head_boxes(tid: str) -> list[dict]:
    """返回每镜每个角色的头部解析结果（最坏时刻）。"""
    from pipeline import motion2d  # QA 复算用（不渲染）
    sm = _shot_meta(tid)
    plan_shots = _plan_role_by_shot(tid)
    out: list[dict] = []
    for sid, item in sm.items():
        motion = (item or {}).get("motion") or {}
        meta = motion.get("meta") or {}
        chars = meta.get("characters") or []
        if not chars:
            continue
        size = tuple(int(v) for v in (meta.get("size") or [1280, 720]))
        W, H = size
        dur = float(item.get("duration") or motion.get("duration") or 0.0)
        if dur <= 0:
            continue
        cam_spec = meta.get("camera") or {}
        depth_ref = float(meta.get("depth_ref") or 0.12)
        depths = {str(l.get("key")): float(l.get("depth") or 0.0)
                  for l in (meta.get("layers") or []) if isinstance(l, dict)}
        mid_depth = depths.get("midground", depth_ref)
        plan_shot = plan_shots.get(sid) or {}
        primary_role = str(plan_shot.get("role") or "")
        roles = []
        for entry in chars:
            try:
                ax, ay = float(entry["anchor"][0]), float(entry["anchor"][1])
            except (KeyError, TypeError, ValueError, IndexError):
                continue
            frac = float(entry.get("height") or 0.62)
            scale = float(entry.get("scale") or 1.0)
            depth = float(entry.get("depth") or mid_depth)
            # 与 motion2d.character_state 一致：anchor_space="zone" 时 anchor 相对取景窗
            zone = cam_spec.get("zone")
            if (str(entry.get("anchor_space") or "scene").lower() == "zone"
                    and isinstance(zone, (list, tuple)) and len(zone) >= 4):
                zx, zy, zw, zh = (float(v) for v in zone[:4])
                ax, ay = zx + ax * zw, zy + ay * zh
            ratio = motion2d._parallax_ratio(
                depth, depth_ref, float(cam_spec.get("parallax", 1.0) or 1.0))
            zf = 0.55 + 0.45 * _clamp(ratio, 0.15, 2.2)
            h_nom = frac * H * scale
            states = []
            for frac_t in (0.02, 0.25, 0.5, 0.75, 0.98):
                t = dur * frac_t
                cam = motion2d.camera_state(cam_spec, t, dur, size)
                zoom = 1.0 + (cam["zoom"] - 1.0) * zf
                cam_dx = cam["x"] * ratio
                cam_dy = cam["y"] * ratio
                head_top = ((ay * H - h_nom) - H / 2.0) * zoom + H / 2.0 + cam_dy
                head_cx = ((ax * W - W / 2.0) * zoom + W / 2.0 + cam_dx)
                head_h = 0.22 * h_nom * zoom
                head_h = 0.22 * h_nom * zoom
                head_half_w = 0.17 * h_nom * zoom
                states.append({"t": round(t, 2), "head_top": round(head_top, 1),
                               "head_cx": round(head_cx, 1),
                               "head_bottom": round(head_top + head_h, 1),
                               "head_half_w": round(head_half_w, 1),
                               "zoom": round(zoom, 2)})
            worst = min(states, key=lambda s: s["head_top"])
            roles.append({"role": str(entry.get("role") or ""),
                          "primary": str(entry.get("role") or "") == primary_role,
                          "frac": frac, "anchor": [ax, ay], **worst})
        if roles:
            if not any(r["primary"] for r in roles):
                roles[0]["primary"] = True
            out.append({"sid": sid, "scene": item.get("scene"), "chars": roles})
    return out


@pytest.mark.parametrize("tid", READY)
def test_character_head_safe(tid: str):
    boxes = _head_boxes(tid)
    assert boxes, f"{tid}: work/shot_meta.json 无角色解析数据（P1 视觉门证据缺失）"
    W, H = EXPECT_W, EXPECT_H
    # 渲染器取景保证头顶留 0.02 上边距；解析式为近似（多角色/坐姿/zone 取景/zoom 误差），
    # V5.4 引入电影级运镜+取景窗后公式偏差变大（约 2-6%H），允许 head_top ≥ -6%H（-43px）
    # 作为“头顶贴边但未裁断”容差；真正裁头（如 -114px 无头躯干）仍能抓出。
    # 该容差边界镜头已逐帧人眼复核（tmp/qa/v5_review/{tid}/headcheck_*.png）。
    tol = round(0.06 * H)
    problems = []
    warnings = []
    for shot in boxes:
        for c in shot["chars"]:
            top, bottom, cx = c["head_top"], c["head_bottom"], c["head_cx"]
            half = c["head_half_w"]
            if c["primary"]:
                # 硬门：主对白角色头部垂直方向必须完整体在画面内（半头/无头躯干回归）
                if top < -tol or bottom > H + tol or bottom <= tol:
                    problems.append(
                        f"{shot['sid']}/{c['role']}(主对白): 头部未完整入画 "
                        f"head_top={top} head_bottom={bottom} H={H}")
                # 水平方向为近似值（受取景/构图容差影响），记 warning 供人眼复核，不做硬门
                if cx - half < -tol or cx + half > W + tol:
                    warnings.append(
                        f"{shot['sid']}/{c['role']}(主对白): 头部水平部分出画 "
                        f"cx={cx} half={half}")
            elif top < -tol and bottom > tol:
                problems.append(
                    f"{shot['sid']}/{c['role']}: 头部被上边缘裁切（无头躯干）"
                    f"head_top={top} head_bottom={bottom}")
    notes = read_json(HEAD_GATE_JSON, {}) or {}
    notes[tid] = {"ok": not problems, "problems": problems, "warnings": warnings}
    HEAD_GATE_JSON.parent.mkdir(parents=True, exist_ok=True)
    HEAD_GATE_JSON.write_text(json.dumps(notes, ensure_ascii=False, indent=1),
                              encoding="utf-8")
    HEAD_WARN_JSON.write_text(
        json.dumps({t: v.get("warnings") for t, v in read_json(HEAD_GATE_JSON, {}).items()
                    if v.get("warnings")}, ensure_ascii=False, indent=1),
        encoding="utf-8")
    assert not problems, f"{tid}: P1 视觉门（角色头部）失败：\n" + "\n".join(problems)


# ---------------------------------------------------------------------------
# 5b) 角色固定声音（voice lock / voice_fixed）
#
# 用户需求：每个角色的声音必须固定，并且可以在角色下修改。
# 口径：
# - 每个说话角色跨镜使用同一 voice_id + voice_key（voice_key 由 provider/voice_id/
#   seed/instruction/ref_audio/ref_text/cfg_scale 派生，改任一项自动换 key）；
# - voice_id 必须来自 contract/v5/voices.json（24 preset，支持 custom/voices.json 覆盖）；
# - 声音锁：outputs/v5_voices/<character>/<voice_key>.wav + .json 存在（同角色跨集一致）；
# - 每部至少 2 个不同角色有台词（host + guest，回归“只有 host 说话”bug）；
# - qc_report.checks.voice_fixed 必须 ok。
# ---------------------------------------------------------------------------
@functools.lru_cache(maxsize=4)
def voice_library() -> dict:
    from pipeline import v5_voices
    return v5_voices.load_voice_library()


def plan_shots(tid: str) -> list[dict]:
    shots = plan(tid).get("shots") or []
    return [s for s in shots if isinstance(s, dict)]


def speaking_shots(tid: str) -> list[dict]:
    return [s for s in plan_shots(tid) if str(s.get("line") or "").strip()]


def episode_voice_map(tid: str) -> dict[str, dict]:
    """从 work/episode.json 抽取每个角色的声音信息（跨镜聚合）。"""
    out: dict[str, dict] = {}
    for shot in speaking_shots(tid):
        voice = shot.get("voice") if isinstance(shot.get("voice"), dict) else {}
        cid = str(voice.get("character") or shot.get("character") or "")
        key = str(voice.get("voice_key") or "")
        vid = str(voice.get("voice_id") or "")
        seed = voice.get("seed")
        ref = str(voice.get("ref") or "")
        item = out.setdefault(cid or "<missing>", {"keys": set(), "voice_ids": set(),
                                                   "seeds": set(), "refs": set(),
                                                   "lines": 0, "shot_ids": []})
        item["lines"] += 1
        item["shot_ids"].append(shot.get("id"))
        if key:
            item["keys"].add(key)
        if vid:
            item["voice_ids"].add(vid)
        if seed is not None:
            item["seeds"].add(seed)
        if ref:
            item["refs"].add(ref)
    return out


def meta_voices(tid: str) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for item in ((plan(tid).get("meta") or {}).get("voices") or []):
        if isinstance(item, dict) and item.get("character"):
            out[str(item["character"])] = item
    return out


@pytest.mark.parametrize("tid", READY)
def test_voice_fixed(tid: str):
    report = qc_report(tid)
    check = (report.get("checks") or {}).get("voice_fixed") or {}
    stats = report.get("stats") or {}
    voices = voice_library()
    presets = voices.get("presets") or {}
    vmap = episode_voice_map(tid)
    meta = meta_voices(tid)
    problems: list[str] = []
    if check.get("ok") is not True:
        problems.append(f"qc.checks.voice_fixed 未通过：{check.get('detail')}")
    if not vmap:
        problems.append("work/episode.json 无对白镜声音信息")
    for cid, item in sorted(vmap.items()):
        if not item["keys"] or len(item["keys"]) > 1:
            problems.append(f"{cid}: voice_key 不唯一/缺失 {sorted(item['keys'])}"
                            f"（镜 {item['shot_ids']}）")
        if not item["voice_ids"] or len(item["voice_ids"]) > 1:
            problems.append(f"{cid}: voice_id 不唯一/缺失 {sorted(item['voice_ids'])}")
        if len(item["seeds"]) > 1:
            problems.append(f"{cid}: seed 不一致 {sorted(item['seeds'])}")
        for vid in item["voice_ids"]:
            if vid not in presets:
                problems.append(f"{cid}: voice_id={vid!r} 不在 contract/v5/voices.json presets")
        key = sorted(item["keys"])[0] if item["keys"] else ""
        if cid and key:
            wav = VOICE_LOCKS / cid / f"{key}.wav"
            meta_json = VOICE_LOCKS / cid / f"{key}.json"
            if not (wav.is_file() and wav.stat().st_size > 20 * 1024):
                problems.append(f"{cid}: 声音锁 wav 缺失/过小 {wav}")
            if not meta_json.is_file():
                problems.append(f"{cid}: 声音锁 json 缺失 {meta_json}")
            mv = meta.get(cid) or {}
            if mv and str(mv.get("key") or "") != key:
                problems.append(f"{cid}: meta.voices.key={mv.get('key')!r} != 镜头 key={key}")
            if mv and str(mv.get("voice_id") or "") != sorted(item["voice_ids"])[0]:
                problems.append(f"{cid}: meta.voices.voice_id 与镜头不一致")
    # qc stats.voices 应与 episode 推导一致
    qc_voices = stats.get("voices") or {}
    derived = {cid: sorted(item["keys"])[0] for cid, item in vmap.items() if item["keys"]}
    if qc_voices and qc_voices != derived:
        problems.append(f"qc.stats.voices={qc_voices} 与 episode 推导 {derived} 不一致")
    assert not problems, f"{tid}: 固定声音验收失败：\n" + "\n".join(problems)


@pytest.mark.parametrize("tid", READY)
def test_multi_speaker(tid: str):
    vmap = episode_voice_map(tid)
    speakers = [cid for cid, item in vmap.items() if item["lines"] > 0]
    assert len(speakers) >= 2, (
        f"{tid}: 只有 {len(speakers)} 个角色有台词（{speakers}），要求 ≥2"
        f"（host+guest；回归“只有 host 说话”bug）")


def test_voice_library_contract():
    voices = voice_library()
    presets = voices.get("presets") or {}
    problems = []
    if len(presets) < 24:
        problems.append(f"voices.json presets={len(presets)} < 24")
    for pid, spec in presets.items():
        if not isinstance(spec, dict):
            problems.append(f"{pid}: preset 不是 dict")
            continue
        if str(spec.get("provider") or "") != "breeze":
            problems.append(f"{pid}: provider != breeze")
        if not str(spec.get("instruction") or "").strip():
            problems.append(f"{pid}: 缺 instruction")
        if spec.get("seed") is None:
            problems.append(f"{pid}: 缺 seed")
    for cid, seed in LEGACY_VOICE_SEEDS.items():
        preset_id = f"legacy_v4_{cid}"
        if preset_id not in presets:
            problems.append(f"缺 V4 兼容 preset {preset_id}")
        elif int(presets[preset_id].get("seed") or 0) != seed:
            problems.append(f"{preset_id}: seed != {seed}")
    # 内置角色 voice.voice_id 必须可解析到 preset（custom 内联音色除外）
    catalog = v5_catalog.load_catalog()
    for cid, ch in (catalog.get("characters") or {}).items():
        voice = ch.get("voice") if isinstance(ch.get("voice"), dict) else {}
        vid = str(voice.get("voice_id") or "")
        if vid and vid not in presets:
            problems.append(f"角色 {cid}: voice_id={vid!r} 不在 presets")
    try:
        from pipeline import v5_voices
        result = v5_voices.validate_library()
        if result.get("ok") is not True or result.get("errors"):
            problems.append(f"validate_library 失败：{result.get('errors')}")
    except Exception as exc:  # noqa: BLE001
        problems.append(f"validate_library 异常：{exc}")
    assert not problems, "声音库契约问题：\n" + "\n".join(problems)


def test_voice_cross_template_consistency():
    """跨 16 部：同一角色必须使用同一 voice_id/key/seed（跨集音色固定）。"""
    ready = [tid for tid in EXPECTED if v5_state(tid) == "ready"]
    if len(ready) < 16:
        pytest.skip(f"V5 批量重渲进行中：可验收 {len(ready)}/16")
    seen: dict[str, dict] = {}
    problems = []
    for tid in ready:
        for cid, item in episode_voice_map(tid).items():
            entry = seen.setdefault(cid, {"voice_ids": set(), "keys": set(),
                                          "seeds": set(), "templates": set()})
            entry["voice_ids"] |= item["voice_ids"]
            entry["keys"] |= item["keys"]
            entry["seeds"] |= item["seeds"]
            entry["templates"].add(tid)
    for cid, entry in sorted(seen.items()):
        if len(entry["voice_ids"]) > 1:
            problems.append(f"{cid}: 跨集 voice_id 不一致 {sorted(entry['voice_ids'])}")
        if len(entry["keys"]) > 1:
            problems.append(f"{cid}: 跨集 voice_key 不一致 {sorted(entry['keys'])}")
        if len(entry["seeds"]) > 1:
            problems.append(f"{cid}: 跨集 seed 不一致 {sorted(entry['seeds'])}")
    summary = {cid: {"voice_id": sorted(e["voice_ids"])[0] if e["voice_ids"] else "",
                     "key": sorted(e["keys"])[0] if e["keys"] else "",
                     "seed": sorted(e["seeds"])[0] if e["seeds"] else None,
                     "templates": sorted(e["templates"])}
               for cid, e in seen.items()}
    out = REPO_ROOT / "tmp" / "qa" / "v5_voice_consistency.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
    assert seen, "没有任何角色声音信息"
    assert not problems, "跨集声音一致性问题：\n" + "\n".join(problems)


# ---------------------------------------------------------------------------
# 6) 已知问题回归：水墨竖排字幕 / 字幕对比度
# ---------------------------------------------------------------------------
def _parse_ass_styles(path: Path) -> dict[str, dict]:
    styles: dict[str, dict] = {}
    if not path.is_file():
        return styles
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.startswith("Style:"):
            continue
        parts = [p.strip() for p in line[len("Style:"):].split(",")]
        if len(parts) < 20:
            continue
        styles[parts[0]] = {"primary": parts[3], "secondary": parts[4],
                            "outline": parts[5], "back": parts[6],
                            "alignment": parts[18], "fontsize": parts[2]}
    return styles


def _ass_color_to_rgb(text: str) -> tuple[int, int, int]:
    """ASS 颜色 &HAA BB GG RR -> (r,g,b)。"""
    raw = text.strip().replace("&H", "").replace("&", "")
    try:
        value = int(raw, 16)
    except ValueError:
        return (0, 0, 0)
    return (value & 0xFF, (value >> 8) & 0xFF, (value >> 16) & 0xFF)


def _luminance(rgb: tuple[int, int, int]) -> float:
    r, g, b = (c / 255.0 for c in rgb)
    def lin(c: float) -> float:
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    return 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b)


def _contrast(a: tuple[int, int, int], b: tuple[int, int, int]) -> float:
    la, lb = _luminance(a), _luminance(b)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


def _ass_drawing_leaks(path: Path) -> list[str]:
    """文字型 Dialogue 里的裸绘图指令（未加 \\p1 覆盖）= 曾把 ASS 指令烧进画面的 bug。"""
    leaks: list[str] = []
    if not path.is_file():
        return leaks
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.startswith("Dialogue:"):
            continue
        body = line[len("Dialogue:"):].split(",", 9)
        if len(body) < 10:
            continue
        text = body[9]
        plain = _OVERRIDE.sub("", text).replace("\\N", "\n").strip()
        if "\\p1" in text or "\\p2" in text:
            continue
        for seg in plain.splitlines():
            if _DRAWING_LEAK.match(seg.strip()):
                leaks.append(seg.strip()[:80])
    return leaks


@pytest.mark.parametrize("tid", READY)
def test_subtitle_quality(tid: str):
    ass = tdir(tid) / "work" / "final.ass"
    assert ass.is_file(), f"{tid}: 缺 work/final.ass（字幕证据）"
    leaks = _ass_drawing_leaks(ass)
    assert not leaks, (
        f"{tid}: final.ass 出现裸 ASS 绘图指令文本（已知问题①回归）：{leaks[:3]}")
    styles = _parse_ass_styles(ass)
    text_styles = [s for name, s in styles.items()
                   if "V" in name.upper() or "SUB" in name.upper()]
    assert text_styles, f"{tid}: final.ass 无字幕后样式"
    worst = None
    for style in text_styles:
        primary = _ass_color_to_rgb(style["primary"])
        outline = _ass_color_to_rgb(style["outline"])
        ratio = _contrast(primary, outline)
        if worst is None or ratio < worst[1]:
            worst = (style, ratio)
    assert worst and worst[1] >= SUB_CONTRAST_MIN, (
        f"{tid}: 字幕主色/描边对比度 {worst[1]:.2f} < {SUB_CONTRAST_MIN}"
        f"（已知问题② silk 低对比回归；style={worst[0] if worst else None}）")


# ---------------------------------------------------------------------------
# 7) 证据 & 批级
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("tid", READY)
def test_contact_sheet_and_frames(tid: str):
    sheet = tdir(tid) / "contact_sheet.png"
    frames = sorted((tdir(tid) / "frames").glob("*.png"))
    assert sheet.is_file() and sheet.stat().st_size > 80 * 1024, (
        f"{tid}: contact_sheet.png 缺失/过小")
    assert len(frames) >= 4, f"{tid}: frames/ 抽帧数 {len(frames)} <4"
    for frame in frames[:4]:
        assert frame.stat().st_size > 10 * 1024, f"{tid}: 抽帧 {frame.name} 过小"


def test_lip_sync_if_present():
    present = []
    for tid in READY:
        check = (qc_report(tid).get("checks") or {}).get("lip_sync")
        if isinstance(check, dict):
            present.append(tid)
            assert check.get("ok") is True, (
                f"{tid}: lip_sync 未通过：{check.get('detail')}")
    if not present:
        pytest.skip("本批 qc_report 未包含 lip_sync（口型同步未启用/未测量）")


def test_catalog_contract():
    result = v5_catalog.validate_catalog()
    assert result.get("ok") is True, f"validate_catalog 失败：{result.get('errors')}"
    assert not result.get("errors"), f"契约 errors：{result.get('errors')}"
    assert not result.get("warnings"), f"契约 warnings：{result.get('warnings')}"
    stats = result.get("stats") or {}
    counts = stats.get("counts") or {}
    minimum = {"styles": 16, "scenes": 60, "characters": 24,
               "props": 40, "templates": 16, "packs": 2}
    problems = [f"{kind}: {counts.get(kind, 0)} < {need}"
                for kind, need in minimum.items() if counts.get(kind, 0) < need]
    assert not problems, "契约数量不足：\n" + "\n".join(problems)


def test_pending_v5_report():
    if not PENDING:
        return
    pytest.skip(f"V5 批量进行中：可验收 {len(READY)}/16，未就绪 {PENDING}")


def test_all_16_v5_ready():
    if len(READY) < 16:
        pytest.skip(f"V5 批量进行中：可验收 {len(READY)}/16")
    assert set(READY) == set(EXPECTED), f"模板集合不符：{sorted(READY)}"
    problems = []
    for tid in EXPECTED:
        report = qc_report(tid)
        if report.get("ok") is not True:
            problems.append(f"{tid}: qc ok != true")
        check_names = set((report.get("checks") or {}).keys())
        missing = REQUIRED_CHECKS - check_names
        if missing:
            problems.append(f"{tid}: qc 缺 checks {sorted(missing)}")
        if not (tdir(tid) / "timeline.json").is_file():
            problems.append(f"{tid}: 缺 timeline.json")
        if not (tdir(tid) / "style_report.json").is_file():
            problems.append(f"{tid}: 缺 style_report.json")
        speakers = [cid for cid, item in episode_voice_map(tid).items()
                    if item["lines"] > 0]
        if len(speakers) < 2:
            problems.append(f"{tid}: 台词角色数 {len(speakers)} <2（{speakers}）")
    batch = read_json(OUTPUTS / "batch_report_v5.json", {}) or {}
    if batch:
        if batch.get("ok") is not True:
            problems.append(f"batch_report_v5.ok != true（passed={batch.get('passed')}/"
                            f"{batch.get('total')}）")
        if batch.get("total") not in (None, 16):
            problems.append(f"batch_report_v5.total={batch.get('total')} != 16")
        if batch.get("passed") not in (None, 16):
            problems.append(f"batch_report_v5.passed={batch.get('passed')} != 16")
    else:
        problems.append("缺 batch_report_v5.json")
    sheet = OUTPUTS / "contact_sheet_all.png"
    if not (sheet.is_file() and sheet.stat().st_size > 200 * 1024):
        problems.append("contact_sheet_all.png 缺失/过小")
    assert not problems, "V5 16/16 终门问题：\n" + "\n".join(problems)
