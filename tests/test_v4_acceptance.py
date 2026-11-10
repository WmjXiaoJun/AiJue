# -*- coding: utf-8 -*-
"""V4 独立验收（Breeze TTS 2 语音 + OpenFilm 式 timeline 剪辑）。

验收口径（docs/PLAN_V4_AUDIO_EDIT.md §2.4/§5 + Lead 的 V4 收口要求）：
- 视频/画质不回退：1280×720 / 25fps / 有音轨 / blackdetect=0 / 时长与 plan 一致；
- H3 证据链不回退：非卡片镜 100% h3=true、workflow∈{04,05}、prompt_id 非空且模板内唯一、
  seed/ref_image/action 齐全、卡片 static 标记、fallbacks=[]；
- audio_pace（V4 新硬门）：qc `audio_pace` check ok；voice_provider=breeze；speech_driven=true；
  atempo 全 1.0、无截断；每镜时长 == voiced.target_duration(speech)（H3 17k+5 网格向上取整）；
  语音+lead+tail 必须装进镜头；
- timeline（V4 新硬门）：timeline.json 存在、version=4、五轨 V1/A1/A2/A3/S1 覆盖完整、
  V1 连续覆盖全时长（±0.05s）、A1 与配音镜一一对应、A2 全片 BGM+duck 配置、S1 覆盖对话镜；
  timeline.html 存在且可读；
- loudness（V4 新硬门）：成片 integrated LUFS ∈ [-17, -15]，true peak ≤ -1.0 dBTP；
- A/B 归档：V3 legacy 原片/旁白 wav 存在；A/B 试听样本存在且可分段；三角色 F0 相对关系可辨。

只读 outputs/ + tmp/qa/，不启动渲染。批量渲染期间：未达到 V4 可验收状态的模板 skip 并报告。
运行：python -m pytest tests/test_v4_acceptance.py -q
      python -m pytest tests/test_v4_acceptance.py -q -k tonight_show
"""
from __future__ import annotations

import functools
import json
import re
import subprocess
import sys
import wave
from pathlib import Path

import numpy as np
import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from pipeline import qc as pipeline_qc  # noqa: E402
from pipeline import template_v3 as tv  # noqa: E402
from pipeline import voiced as voiced_mod  # noqa: E402

OUTPUTS = REPO_ROOT / "outputs" / "templates_v3"
LEGACY = REPO_ROOT / "outputs" / "v3_legacy_audio"
AB_DIR = REPO_ROOT / "tmp" / "v4_voice_ab"
TEMPLATE_DIR = REPO_ROOT / "contract" / "templates_v3"
FFMPEG = pipeline_qc.FF
WORKFLOWS = {"04_i2v_fl2v_4step_sla", "05_ref2va_4step_sla"}
CARD_TYPES = {"title_card", "end_card", "meme_card"}
DURATION_TOL = 0.6
TIMELINE_TOL = 0.05
LUFS_RANGE = (-17.0, -15.0)
TP_MAX = -1.0
EXPECTED_TEMPLATES = [
    "tonight_show", "shark_tank", "masked_singer", "standup", "taskmaster",
    "traitors", "snl_sketch", "roundtable", "hot_ones", "qipashuo",
    "slow_life", "history_talk", "history_mystery", "classics",
    "national_treasure", "mystery",
]


# ---------------------------------------------------------------------------
# 读取 & 状态判定
# ---------------------------------------------------------------------------
def read_json(path: Path, default=None):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def tdir(tid: str) -> Path:
    return OUTPUTS / tid


def is_card(shot: dict) -> bool:
    stype = str(shot.get("type") or shot.get("intent") or "").lower()
    return stype in CARD_TYPES or "card" in stype


def qc_report(tid: str) -> dict:
    return read_json(tdir(tid) / "qc_report.json", {}) or {}


def checks_map(report: dict) -> dict:
    checks = report.get("checks") or {}
    if isinstance(checks, dict):
        return {str(k).strip().lower(): v for k, v in checks.items()
                if isinstance(v, dict)}
    if isinstance(checks, list):
        return {str(c.get("name") or "").strip().lower(): c for c in checks
                if isinstance(c, dict)}
    return {}


def load_plan(tid: str) -> dict:
    return read_json(tdir(tid) / "work" / "episode.json", {}) or {}


def plan_shots(plan: dict) -> list[dict]:
    shots = plan.get("shots")
    if shots is None and isinstance(plan.get("episode"), dict):
        shots = plan["episode"].get("shots")
    return [s for s in shots or [] if isinstance(s, dict)]


def is_card_shot(shot: dict) -> bool:
    return is_card(shot)


def plan_total(plan: dict) -> float:
    for holder in (plan.get("episode") or {}, plan.get("meta") or {}):
        if isinstance(holder, dict) and isinstance(holder.get("total_duration"),
                                                   (int, float)):
            return float(holder["total_duration"])
    return sum(float(s.get("duration") or 0) for s in plan_shots(plan))


def h3_metas(tid: str) -> list[dict]:
    meta = read_json(tdir(tid) / "h3_meta.json", {}) or {}
    if isinstance(meta, list):
        return [m for m in meta if isinstance(m, dict)]
    shots = meta.get("shots")
    return [m for m in shots or [] if isinstance(m, dict)] if isinstance(shots, list) else []


def voice_map(tid: str) -> dict:
    return read_json(tdir(tid) / "work" / "voice" / "voice_map.json", {}) or {}


def load_timeline(tid: str) -> dict:
    return read_json(tdir(tid) / "timeline.json", {}) or {}


def resolve_ref(tid: str, ref) -> bool:
    if not ref:
        return False
    p = Path(str(ref))
    if p.is_file():
        return True
    if not p.is_absolute():
        for cand in (tdir(tid) / p, REPO_ROOT / p):
            if cand.is_file():
                return True
    return False


def track_map(timeline: dict) -> dict[str, dict]:
    out = {}
    for track in timeline.get("tracks") or []:
        if isinstance(track, dict) and track.get("id"):
            out[str(track["id"])] = track
    return out


def v4_state(tid: str) -> str:
    """v4 = 已按 V4 落盘的正式产物；pending/legacy = 未完成/旧 V3。"""
    base = tdir(tid)
    required = [base / "final.mp4", base / "qc_report.json", base / "h3_meta.json",
                base / "timeline.json", base / "timeline.html"]
    if not all(p.is_file() for p in required):
        return "pending"
    report = qc_report(tid)
    cmap = checks_map(report)
    stats = report.get("stats") or {}
    if report.get("ok") is not True or report.get("deliverable") is not True:
        return "not_ok"
    if stats.get("speech_driven") is not True:
        return "legacy"
    if "audio_pace" not in cmap:
        return "legacy"
    if cmap.get("audio_pace", {}).get("ok") is not True:
        return "audio_pace_fail"
    return "v4"


V4_READY = [tid for tid in EXPECTED_TEMPLATES if v4_state(tid) == "v4"]
PENDING = [tid for tid in EXPECTED_TEMPLATES if tid not in V4_READY]


def require_v4(tid: str) -> None:
    state = v4_state(tid)
    if state != "v4":
        pytest.skip(f"{tid}: 尚未达到 V4 可验收状态（{state}），等待批量渲染完成")


# ---------------------------------------------------------------------------
# 1) 视频规格 + 黑帧
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("tid", V4_READY)
def test_video_spec(tid: str):
    video = tdir(tid) / "final.mp4"
    probe = pipeline_qc.probe(video)
    plan = load_plan(tid)
    expected = plan_total(plan)
    assert (probe["width"], probe["height"]) == (1280, 720), (
        f"{tid}: 分辨率 {probe['width']}x{probe['height']} != 1280x720")
    assert abs(probe["fps"] - 25.0) < 0.1, f"{tid}: fps={probe['fps']} != 25"
    assert probe["has_audio"], f"{tid}: 成片无音轨"
    blacks = pipeline_qc.blackdetect(video)
    assert blacks == 0, f"{tid}: blackdetect={blacks} != 0"
    delta = abs(probe["duration"] - expected)
    assert delta <= DURATION_TOL, (
        f"{tid}: 成片 {probe['duration']}s vs plan {expected:.3f}s 差 {delta:.2f}s "
        f"> {DURATION_TOL}s")


# ---------------------------------------------------------------------------
# 2) H3 证据链（V3 硬门不回退）
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("tid", V4_READY)
def test_h3_evidence_chain(tid: str):
    plan = load_plan(tid)
    shots = plan_shots(plan)
    bodies = [s for s in shots if not is_card_shot(s)]
    cards = [s for s in shots if is_card_shot(s)]
    metas = h3_metas(tid)
    h3m = [m for m in metas if m.get("h3") is True]
    problems = []
    if len(h3m) != len(bodies) or not bodies:
        problems.append(f"H3 覆盖 {len(h3m)}/{len(bodies)}")
    prompt_ids = [str(m.get("prompt_id") or "").strip() for m in h3m]
    if not prompt_ids or any(not p for p in prompt_ids):
        problems.append("存在空 prompt_id")
    if len(set(prompt_ids)) != len(prompt_ids):
        problems.append(f"prompt_id 重复 {len(prompt_ids)-len(set(prompt_ids))}")
    workflows = {str(m.get("workflow") or "").strip() for m in h3m}
    if not workflows or not workflows <= WORKFLOWS:
        problems.append(f"workflow 非法 {workflows}")
    for m in h3m:
        sid = m.get("id") or m.get("shot_id")
        if m.get("seed") is None:
            problems.append(f"{sid}: 缺 seed")
        if not resolve_ref(tid, m.get("ref_image")):
            problems.append(f"{sid}: ref_image 不存在")
        if not str(m.get("action") or "").strip():
            problems.append(f"{sid}: action 为空")
    card_meta = [m for m in metas if m.get("static_card") is True]
    if len(card_meta) < len(cards):
        problems.append(f"卡片 static 标记 {len(card_meta)}/{len(cards)}")
    report = qc_report(tid)
    if report.get("fallbacks"):
        problems.append(f"qc fallbacks={len(report['fallbacks'])}")
    assert not problems, f"{tid}: H3 证据链问题：\n" + "\n".join(problems)


# ---------------------------------------------------------------------------
# 3) audio_pace（V4 新硬门）
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("tid", V4_READY)
def test_audio_pace(tid: str):
    report = qc_report(tid)
    cmap = checks_map(report)
    stats = report.get("stats") or {}
    pace = cmap.get("audio_pace") or {}
    plan = load_plan(tid)
    shots = plan_shots(plan)
    speech_shots = [s for s in shots if not is_card_shot(s)
                    and str(s.get("line") or "").strip()]
    vm = voice_map(tid)
    vm_shots = vm.get("shots") or {}
    problems = []
    if pace.get("ok") is not True:
        problems.append(f"qc audio_pace 未通过: {pace.get('detail')}")
    if str(stats.get("voice_provider") or "") != "breeze":
        problems.append(f"qc stats voice_provider={stats.get('voice_provider')!r} != breeze")
    if stats.get("speech_driven") is not True:
        problems.append("qc stats speech_driven != true")
    if abs(float(stats.get("atempo_max") or 0) - 1.0) > 1e-3:
        problems.append(f"qc stats atempo_max={stats.get('atempo_max')!r}")
    if stats.get("truncated_shots"):
        problems.append(f"qc stats truncated_shots={stats['truncated_shots']}")
    if str(vm.get("provider") or "") != "breeze":
        problems.append(f"voice_map.provider={vm.get('provider')!r} != breeze")
    if len(vm_shots) != len(speech_shots):
        problems.append(f"voice_map {len(vm_shots)} 镜 != 对话镜 {len(speech_shots)}")
    lead = float(vm.get("lead_sec") or voiced_mod.LEAD_SEC)
    tail = float(vm.get("tail_sec") or voiced_mod.TAIL_SEC)
    for sid, info in vm_shots.items():
        if abs(float(info.get("atempo") or 1.0) - 1.0) > 1e-3:
            problems.append(f"{sid}: atempo={info.get('atempo')!r}")
        if info.get("truncated"):
            problems.append(f"{sid}: truncated=true")
        speech = float(info.get("speech_duration") or 0.0)
        duration = float(info.get("duration") or 0.0)
        if speech <= 0 or duration <= 0:
            problems.append(f"{sid}: speech/duration 非法 ({speech}/{duration})")
            continue
        expected = voiced_mod.target_duration(speech)
        if abs(duration - expected) > 0.002:
            problems.append(f"{sid}: duration={duration} != target_duration({speech})={expected}")
        if speech + lead + tail > duration + 1e-6:
            problems.append(f"{sid}: speech+lead+tail {speech + lead + tail:.3f}s "
                            f"> 镜头 {duration:.3f}s（会被截断）")
        voice_path = info.get("path")
        if not voice_path or not Path(str(voice_path)).is_file():
            problems.append(f"{sid}: 配音文件不存在 {voice_path!r}")
        audio = tdir(tid) / "work" / "audio" / f"{sid}.wav"
        if not audio.is_file():
            problems.append(f"{sid}: work/audio/{sid}.wav 不存在")
    assert not problems, f"{tid}: audio_pace 问题：\n" + "\n".join(problems)


# ---------------------------------------------------------------------------
# 4) timeline（V4 新硬门）
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("tid", V4_READY)
def test_timeline_artifact(tid: str):
    timeline = load_timeline(tid)
    plan = load_plan(tid)
    shots = plan_shots(plan)
    bodies = [s for s in shots if not is_card_shot(s)]
    cards = [s for s in shots if is_card_shot(s)]
    total = plan_total(plan)
    probe = pipeline_qc.probe(tdir(tid) / "final.mp4")
    problems = []
    if int(timeline.get("version") or 0) != 4:
        problems.append(f"version={timeline.get('version')!r} != 4")
    if int(timeline.get("fps") or 0) != 25:
        problems.append(f"fps={timeline.get('fps')!r} != 25")
    if list(timeline.get("resolution") or []) != [1280, 720]:
        problems.append(f"resolution={timeline.get('resolution')!r} != [1280,720]")
    tl_duration = float(timeline.get("duration") or 0)
    if abs(tl_duration - total) > TIMELINE_TOL:
        problems.append(f"timeline.duration={tl_duration} vs plan {total:.3f} "
                        f"（容差 {TIMELINE_TOL}）")
    if abs(tl_duration - probe["duration"]) > DURATION_TOL:
        problems.append(f"timeline.duration={tl_duration} vs 成片 {probe['duration']}")
    tracks = track_map(timeline)
    for required in ("V1", "A1", "A2", "A3", "S1"):
        if required not in tracks:
            problems.append(f"缺轨道 {required}")
    # V1 视频轨：连续覆盖 + clip 数 = plan 镜头数 + kind 对应
    if "V1" in tracks:
        clips = tracks["V1"].get("clips") or []
        if len(clips) != len(shots):
            problems.append(f"V1 clips={len(clips)} != plan shots={len(shots)}")
        cursor = 0.0
        for i, clip in enumerate(clips):
            at = float(clip.get("at") or 0)
            out = float(clip.get("out") or 0)
            if abs(at - cursor) > 0.01:
                problems.append(f"V1#{i} at={at} != 累计 {cursor:.3f}")
            if out <= 0:
                problems.append(f"V1#{i} out={out} <= 0")
            cursor = round(cursor + out, 3)
        if clips and abs(cursor - tl_duration) > TIMELINE_TOL:
            problems.append(f"V1 总长 {cursor:.3f} vs timeline {tl_duration:.3f}")
        h3_clips = [c for c in clips if c.get("kind") == "h3"]
        card_clips = [c for c in clips if c.get("kind") == "card"]
        if len(h3_clips) != len(bodies) or len(card_clips) != len(cards):
            problems.append(f"V1 kind 分布 h3={len(h3_clips)}/{len(bodies)} "
                            f"card={len(card_clips)}/{len(cards)}")
        for clip in clips:
            if not Path(str(clip.get("src") or "")).is_file():
                problems.append(f"V1 src 不存在 {clip.get('src')!r}")
    # A1 配音轨
    vm_shots = voice_map(tid).get("shots") or {}
    if "A1" in tracks:
        clips = tracks["A1"].get("clips") or []
        if len(clips) != len(vm_shots):
            problems.append(f"A1 clips={len(clips)} != voice shots={len(vm_shots)}")
        v1_starts = {}
        for clip in (tracks.get("V1", {}).get("clips") or []):
            v1_starts[str(clip.get("id"))] = float(clip.get("at") or 0)
        for clip in clips:
            sid = str(clip.get("id") or "").removeprefix("v_")
            if not Path(str(clip.get("src") or "")).is_file():
                problems.append(f"A1 src 不存在 {clip.get('src')!r}")
            if abs(float(clip.get("atempo") or 1.0) - 1.0) > 1e-3:
                problems.append(f"A1 {sid}: atempo={clip.get('atempo')!r}")
            if str(clip.get("provider") or "") != "breeze":
                problems.append(f"A1 {sid}: provider={clip.get('provider')!r}")
            if sid in v1_starts and abs(float(clip.get("at") or 0) - v1_starts[sid]) > 0.01:
                problems.append(f"A1 {sid}: at={clip.get('at')} != V1 {v1_starts[sid]}")
    # A2 BGM 轨：覆盖全片 + duck 开启
    if "A2" in tracks:
        clips = tracks["A2"].get("clips") or []
        if not clips:
            problems.append("A2 无 BGM clip")
        else:
            bgm = clips[0]
            if abs(float(bgm.get("at") or 0)) > 0.01:
                problems.append(f"A2 at={bgm.get('at')} != 0")
            if abs(float(bgm.get("out") or 0) - tl_duration) > 0.1:
                problems.append(f"A2 out={bgm.get('out')} != timeline {tl_duration}")
            duck = bgm.get("duck") or {}
            if duck.get("enabled") is not True:
                problems.append("A2 duck 未启用")
            if not Path(str(bgm.get("src") or "")).is_file():
                problems.append(f"A2 src 不存在 {bgm.get('src')!r}")
    # A3 SFX 轨：转场 whoosh 数 = V1 中起始时间 >0 的 H3 镜数（转场切点）
    if "A3" in tracks:
        clips = tracks["A3"].get("clips") or []
        v1_clips = tracks.get("V1", {}).get("clips") or []
        expected_cut = len([c for c in v1_clips
                            if c.get("kind") == "h3" and float(c.get("at") or 0) > 0])
        if len(clips) != expected_cut:
            problems.append(f"A3 clips={len(clips)} != 切点 {expected_cut}")
        for clip in clips:
            if not Path(str(clip.get("src") or "")).is_file():
                problems.append(f"A3 src 不存在 {clip.get('src')!r}")
    # S1 字幕轨：对话镜一一对应
    if "S1" in tracks:
        clips = tracks["S1"].get("clips") or []
        speech_shots = [s for s in shots if not is_card_shot(s)
                        and str(s.get("line") or "").strip()]
        if len(clips) != len(speech_shots):
            problems.append(f"S1 clips={len(clips)} != 对话镜 {len(speech_shots)}")
        for clip in clips:
            if not str(clip.get("text") or "").strip():
                problems.append("S1 存在空字幕")
            if float(clip.get("out") or 0) <= float(clip.get("at") or 0):
                problems.append(f"S1 时间非法 at={clip.get('at')} out={clip.get('out')}")
    # meta 与 HTML
    meta = timeline.get("meta") or {}
    if meta.get("speech_driven") is not True:
        problems.append("timeline.meta.speech_driven != true")
    if str(meta.get("voice_provider") or "") != "breeze":
        problems.append(f"timeline.meta.voice_provider={meta.get('voice_provider')!r}")
    if int(meta.get("voice_clips") or 0) != len(vm_shots):
        problems.append(f"timeline.meta.voice_clips={meta.get('voice_clips')} "
                        f"!= {len(vm_shots)}")
    html_path = tdir(tid) / "timeline.html"
    html_text = html_path.read_text(encoding="utf-8", errors="replace") if html_path.is_file() else ""
    if len(html_text) < 500:
        problems.append("timeline.html 过小/缺失")
    for token in ("V1", "A1", "A2", "A3", "S1", tid):
        if token not in html_text:
            problems.append(f"timeline.html 缺内容 {token}")
    assert not problems, f"{tid}: timeline 问题：\n" + "\n".join(problems)


# ---------------------------------------------------------------------------
# 5) loudness（V4 新硬门：LUFS -17~-15 / TP ≤ -1.0）
# ---------------------------------------------------------------------------
@functools.lru_cache(maxsize=64)
def measure_lufs(path_str: str, mtime: float) -> dict:
    cmd = [str(FFMPEG), "-hide_banner", "-i", path_str, "-af",
           "loudnorm=I=-16:TP=-1.5:LRA=11:print_format=json", "-f", "null", "-"]
    proc = subprocess.run(cmd, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=600)
    match = re.search(r"\{[^{}]*\"input_i\"[^{}]*\}", proc.stderr or "", re.S)
    if not match:
        return {"ok": False}
    data = json.loads(match.group(0))
    return {"ok": True,
            "lufs": float(data.get("input_i", "nan")),
            "tp": float(data.get("input_tp", "nan")),
            "lra": float(data.get("input_lra", "nan"))}


@pytest.mark.parametrize("tid", V4_READY)
def test_loudness(tid: str):
    video = tdir(tid) / "final.mp4"
    info = measure_lufs(str(video), video.stat().st_mtime)
    assert info.get("ok"), f"{tid}: LUFS 测量失败"
    low, high = LUFS_RANGE
    assert low <= info["lufs"] <= high, (
        f"{tid}: integrated LUFS={info['lufs']:.2f} 不在 [{low}, {high}]")
    assert info["tp"] <= TP_MAX, f"{tid}: true peak={info['tp']:.2f} > {TP_MAX} dBTP"
    # 新代码/修复后的 qc_report 应带 loudness check（-17~-15 + TP≤-1.0），存在则必须 ok
    report = qc_report(tid)
    cmap = checks_map(report)
    stats = report.get("stats") or {}
    loud = cmap.get("loudness")
    if isinstance(loud, dict):
        assert loud.get("ok") is True, (
            f"{tid}: qc_report.loudness check 未通过：{loud.get('detail')}")
        qc_lufs = (stats.get("loudness_lufs")
                   if isinstance(stats.get("loudness_lufs"), (int, float))
                   else None)
        for key in ("lufs", "loudness", "integrated_lufs"):
            if qc_lufs is None and isinstance(loud.get(key), (int, float)):
                qc_lufs = loud[key]
        qc_tp = (stats.get("loudness_tp")
                 if isinstance(stats.get("loudness_tp"), (int, float)) else None)
        for key in ("tp", "true_peak", "true_peak_dbtp"):
            if qc_tp is None and isinstance(loud.get(key), (int, float)):
                qc_tp = loud[key]
        if isinstance(qc_lufs, (int, float)):
            assert abs(float(qc_lufs) - info["lufs"]) <= 0.6, (
                f"{tid}: qc.loudness={qc_lufs} 与 QA 实测 {info['lufs']} 差 >0.6dB")
        if isinstance(qc_tp, (int, float)):
            assert abs(float(qc_tp) - info["tp"]) <= 0.6, (
                f"{tid}: qc.true_peak={qc_tp} 与 QA 实测 {info['tp']} 差 >0.6dB")


@pytest.mark.parametrize("tid", V4_READY)
def test_mix_ducking_contract(tid: str):
    """ducking 契约级核对：timeline A2 duck 启用 + manifest duck_bgm=true + 音频模式 v4。"""
    timeline = load_timeline(tid)
    music = track_map(timeline).get("A2", {})
    clips = music.get("clips") or [{}]
    duck = (clips[0] or {}).get("duck") or {}
    manifest = read_json(tdir(tid) / "work" / "manifest.json", {}) or {}
    assert duck.get("enabled") is True, f"{tid}: A2 duck 未启用"
    assert manifest.get("duck_bgm") is True, f"{tid}: manifest.duck_bgm != true"
    assert str(manifest.get("audio_mode") or "") == "v4_breeze", (
        f"{tid}: audio_mode={manifest.get('audio_mode')!r} != v4_breeze")


# ---------------------------------------------------------------------------
# 6) A/B：V3 legacy 归档 + 试听样本 + 音色可分
# ---------------------------------------------------------------------------
def _ab_ready() -> bool:
    return all((LEGACY / tid / "final_v3_edge.mp4").is_file()
               for tid in ("tonight_show", "history_talk"))


@pytest.mark.skipif(not _ab_ready(), reason="V3 legacy 归档尚未就绪")
def test_legacy_archive_v3_ab():
    problems = []
    for tid in ("tonight_show", "history_talk"):
        legacy = LEGACY / tid
        final = legacy / "final_v3_edge.mp4"
        narration = legacy / "narration_v3_edge.wav"
        if not final.is_file() or final.stat().st_size < 100 * 1024:
            problems.append(f"{tid}: final_v3_edge.mp4 缺失/过小")
            continue
        if not narration.is_file() or narration.stat().st_size < 100 * 1024:
            problems.append(f"{tid}: narration_v3_edge.wav 缺失/过小")
        probe = pipeline_qc.probe(final)
        if (probe["width"], probe["height"]) != (1280, 720) or not probe["has_audio"]:
            problems.append(f"{tid}: V3 归档规格异常 {probe['width']}x{probe['height']} "
                            f"audio={probe['has_audio']}")
    assert not problems, "V3 legacy A/B 归档问题：\n" + "\n".join(problems)


def _read_wav_mono(path: Path) -> tuple[np.ndarray, int]:
    with wave.open(str(path), "rb") as fh:
        channels, width = fh.getnchannels(), fh.getsampwidth()
        rate = fh.getframerate()
        raw = fh.readframes(fh.getnframes())
    dtype = {1: np.int8, 2: np.int16, 4: np.int32}.get(width)
    if dtype is None:
        raise ValueError(f"unsupported sample width {width}")
    data = np.frombuffer(raw, dtype=dtype).astype(np.float32)
    if width == 2:
        data /= 32768.0
    elif width == 4:
        data /= 2147483648.0
    if channels > 1:
        data = data.reshape(-1, channels).mean(axis=1)
    return data, rate


def _f0_yin(path: Path, fmin: float = 60.0, fmax: float = 420.0) -> float | None:
    try:
        signal, rate = _read_wav_mono(path)
    except (OSError, wave.Error, ValueError):
        return None
    frame = int(rate * 0.04)
    hop = int(rate * 0.02)
    lo, hi = max(2, int(rate / fmax)), int(rate / fmin)
    f0s = []
    for start in range(0, max(1, signal.size - frame), hop):
        chunk = signal[start:start + frame].astype(np.float64)
        if np.sqrt(np.mean(chunk ** 2)) < 0.01:
            continue
        chunk = chunk - chunk.mean()
        n = chunk.size
        corr = np.correlate(chunk, chunk, mode="full")[n - 1:]
        cum = np.cumsum(chunk ** 2)
        total = cum[-1]
        taus = np.arange(lo, min(hi, n - 2) + 1)
        if taus.size < 3:
            continue
        e_left = cum[n - taus - 1]
        e_right = total - cum[taus - 1]
        d = np.maximum(e_left + e_right - 2 * corr[taus], 0.0)
        dprime = np.ones_like(d)
        denom = np.cumsum(d[1:])
        denom[denom == 0] = 1e-9
        dprime[1:] = d[1:] * np.arange(1, d.size) / denom
        idx = None
        for i in range(1, dprime.size - 1):
            if dprime[i] < 0.15 and dprime[i] <= dprime[i - 1] and dprime[i] <= dprime[i + 1]:
                idx = i
                break
        if idx is None:
            idx = int(np.argmin(dprime[1:])) + 1
        f0 = rate / (lo + idx)
        if fmin <= f0 <= fmax:
            f0s.append(f0)
    return round(float(np.median(f0s)), 1) if f0s else None


def test_ab_round_sample():
    ab = AB_DIR / "AB_round1.wav"
    if not ab.is_file():
        pytest.skip(f"缺少 A/B 试听样本 {ab}（由 V4 音频归档流程生成，不是仓库资产）")
    assert ab.stat().st_size > 500 * 1024, "AB_round1.wav 过小"
    data, rate = _read_wav_mono(ab)
    duration = data.size / rate
    assert duration > 10.0, f"AB_round1.wav 时长 {duration:.1f}s < 10s"
    for provider in ("breeze", "edge"):
        for role in ("laozhou", "ali", "dawei"):
            files = sorted(AB_DIR.glob(f"{provider}_{role}_*.wav"))
            assert files, f"缺少 A/B 分角色样本 {provider}_{role}_*.wav"
    # 音色可分（相对关系）：ali 基频 > 两位男声
    f0s = {}
    for role in ("laozhou", "ali", "dawei"):
        files = sorted(AB_DIR.glob(f"breeze_{role}_*.wav"))
        vals = [v for v in (_f0_yin(f) for f in files) if v]
        f0s[role] = round(float(np.median(vals)), 1) if vals else None
    assert all(f0s[r] for r in f0s), f"F0 估计失败: {f0s}"
    assert f0s["ali"] > f0s["laozhou"] and f0s["ali"] > f0s["dawei"], (
        f"三角色音色未区分（F0: {f0s}）")
    print(f"[qa] A/B breeze F0（YIN 独立估计）: {f0s}")


# ---------------------------------------------------------------------------
# 7) 批量 V4 进度 / 16 终门
# ---------------------------------------------------------------------------
def test_pending_v4_report():
    if not PENDING:
        return
    pytest.skip(f"V4 批量进行中：可验收 {len(V4_READY)}/16，未就绪 {PENDING}")


def test_all_16_v4_ready():
    if len(V4_READY) < len(EXPECTED_TEMPLATES):
        pytest.skip(f"V4 批量进行中：{len(V4_READY)}/16")
    assert set(V4_READY) == set(EXPECTED_TEMPLATES), (
        f"存在非契约目录/缺模板：ready={sorted(V4_READY)}")
    problems = []
    for tid in EXPECTED_TEMPLATES:
        report = qc_report(tid)
        cmap = checks_map(report)
        stats = report.get("stats") or {}
        if cmap.get("audio_pace", {}).get("ok") is not True:
            problems.append(f"{tid}: audio_pace 未通过")
        if stats.get("speech_driven") is not True:
            problems.append(f"{tid}: speech_driven != true")
        if str(stats.get("voice_provider") or "") != "breeze":
            problems.append(f"{tid}: voice_provider={stats.get('voice_provider')!r}")
        if not (tdir(tid) / "timeline.json").is_file():
            problems.append(f"{tid}: 缺 timeline.json")
    # batch_report 覆盖
    report_path = OUTPUTS / "batch_report.json"
    if report_path.is_file():
        batch = read_json(report_path, {}) or {}
        entries = {str(e.get("id")): e for e in (batch.get("templates") or [])
                   if isinstance(e, dict)}
        for tid in EXPECTED_TEMPLATES:
            entry = entries.get(tid)
            if entry is None:
                problems.append(f"{tid}: batch_report 无条目")
            elif entry.get("status") not in ("rendered", "skipped"):
                problems.append(f"{tid}: batch status={entry.get('status')!r}")
        if isinstance(batch.get("covered_total"), int) and batch["covered_total"] < 16:
            problems.append(f"batch_report.covered_total={batch['covered_total']} < 16")
    else:
        problems.append("缺 batch_report.json")
    assert not problems, "V4 16/16 终门问题：\n" + "\n".join(problems)
