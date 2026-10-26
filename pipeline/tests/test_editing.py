# -*- coding: utf-8 -*-
"""V5.4 剪辑执行层单测：per-boundary 转场 xfade_chain + J/L cut 混音。

全部用 ffmpeg lavfi 生成的纯色小视频/正弦波音频，CPU 级、不占 GPU、不依赖模型。
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from pipeline import config, editing, utils  # noqa: E402


# ---------------------------------------------------------------------------
# 工具
# ---------------------------------------------------------------------------
def _make_clip(path: Path, color: str, secs: float = 1.6,
               size: tuple[int, int] = (320, 180), fps: int = 25) -> Path:
    w, h = size
    utils.ffmpeg([
        "-f", "lavfi", "-i", f"color=c={color}:s={w}x{h}:r={fps}:d={secs}",
        "-an", "-c:v", "libx264", "-preset", "veryfast", "-crf", "24",
        "-pix_fmt", "yuv420p", "-t", f"{secs:.3f}", str(path),
    ])
    assert path.is_file() and utils.probe_duration(path) > 0
    return path


def _make_audio(path: Path, freq: int = 440, secs: float = 2.0) -> Path:
    utils.ffmpeg([
        "-f", "lavfi", "-i",
        f"sine=frequency={freq}:sample_rate={config.SAMPLE_RATE}:duration={secs}",
        "-af", "pan=stereo|c0=c0|c1=c0", "-c:a", "pcm_s16le", str(path),
    ])
    assert path.is_file() and utils.probe_duration(path) > 0
    return path


@pytest.fixture()
def two_clips(tmp_path):
    a = _make_clip(tmp_path / "a.mp4", "red")
    b = _make_clip(tmp_path / "b.mp4", "blue")
    return tmp_path, [{"id": "a", "duration": 1.6}, {"id": "b", "duration": 1.6}], \
        {"a": a, "b": b}


# ---------------------------------------------------------------------------
# 时长公式（纯函数）
# ---------------------------------------------------------------------------
def test_expected_duration_formula():
    shots = [{"id": f"s{i}", "duration": d} for i, d in enumerate((2.0, 2.0, 2.0))]
    assert editing.expected_duration(shots, None) == pytest.approx(6.0)
    assert editing.expected_duration(
        shots, [{"mode": "dissolve", "duration": 0.4}, {"mode": "cut"}]
    ) == pytest.approx(5.6)
    # 未知 mode 回退 fade：仍按 fade 时长扣减
    assert editing.expected_duration(
        shots, [{"mode": "nonsense", "duration": 0.3}, {"mode": "cut"}]
    ) == pytest.approx(5.7)
    # 边界不足自动按 cut 补齐
    assert editing.expected_duration(
        shots, [{"mode": "fade", "duration": 0.2}]
    ) == pytest.approx(5.8)


def test_mode_alias_and_cut_resolution():
    assert editing._resolve_mode("cut") is None
    assert editing._resolve_mode("none") is None
    assert editing._resolve_mode("hard") is None
    assert editing._resolve_mode("whip_left") == "hblur"
    assert editing._resolve_mode("pixelate") == "pixelize"
    assert editing._resolve_mode("dissolve") == "dissolve"
    assert editing._resolve_mode("unknown_mode") == "fade"


# ---------------------------------------------------------------------------
# xfade_chain：cut / 混合 / 未知回退
# ---------------------------------------------------------------------------
def test_cut_chain_duration_and_format(two_clips):
    workdir, shots, clips = two_clips
    out = editing.xfade_chain(
        shots, clips, workdir, fps=25, width=320, height=180,
        total=3.2, boundaries=[{"from": "a", "to": "b", "mode": "cut"}])
    assert out.is_file() and out.name == editing.VIDEO_EDITED
    dur = utils.probe_duration(out)
    assert dur == pytest.approx(3.2, abs=0.12)
    info = utils.probe_video(out)
    assert (info["width"], info["height"]) == (320, 180)
    assert info["fps"] == pytest.approx(25.0, abs=0.1)


def test_mixed_cut_and_xfade_duration(tmp_path):
    clips = {
        "a": _make_clip(tmp_path / "a.mp4", "red"),
        "b": _make_clip(tmp_path / "b.mp4", "green"),
        "c": _make_clip(tmp_path / "c.mp4", "blue"),
    }
    shots = [{"id": sid, "duration": 1.6} for sid in ("a", "b", "c")]
    boundaries = [{"from": "a", "to": "b", "mode": "dissolve", "duration": 0.4},
                  {"from": "b", "to": "c", "mode": "cut"}]
    expect = editing.expected_duration(shots, boundaries)
    assert expect == pytest.approx(4.4)
    out = editing.xfade_chain(shots, clips, tmp_path, fps=25, width=320,
                              height=180, total=expect, boundaries=boundaries)
    assert utils.probe_duration(out) == pytest.approx(expect, abs=0.12)
    meta = utils.read_json(tmp_path / "edit_meta.json")
    assert len(meta["boundaries"]) == 2
    assert meta["boundaries"][0]["style"] == "dissolve"
    assert meta["boundaries"][1]["style"] is None


def test_unknown_mode_falls_back_to_fade(two_clips, caplog):
    workdir, shots, clips = two_clips
    boundaries = [{"from": "a", "to": "b", "mode": "totally_unknown", "duration": 0.3}]
    expect = editing.expected_duration(shots, boundaries)
    assert expect == pytest.approx(2.9)
    with caplog.at_level(logging.WARNING, logger="pipeline"):
        out = editing.xfade_chain(shots, clips, workdir, fps=25, width=320,
                                  height=180, total=expect, boundaries=boundaries)
    assert utils.probe_duration(out) == pytest.approx(expect, abs=0.12)
    assert "回退" in caplog.text
    meta = utils.read_json(workdir / "edit_meta.json")
    assert meta["boundaries"][0]["style"] == "fade"


def test_single_shot_chain(two_clips):
    workdir, shots, clips = two_clips
    out = editing.xfade_chain(shots[:1], {"a": clips["a"]}, workdir, fps=25,
                              width=320, height=180, total=1.6, boundaries=[])
    assert utils.probe_duration(out) == pytest.approx(1.6, abs=0.12)


# ---------------------------------------------------------------------------
# 回归：concat 中间产物 timebase 不一致（V5.4 history_mystery 真 bug）
# ---------------------------------------------------------------------------
def _make_varied_clip(path: Path, color: str, secs: float, fps: int,
                      size: tuple[int, int]) -> Path:
    w, h = size
    utils.ffmpeg([
        "-f", "lavfi", "-i", f"color=c={color}:s={w}x{h}:r={fps}:d={secs}",
        "-an", "-c:v", "libx264", "-preset", "veryfast", "-crf", "24",
        "-pix_fmt", "yuv420p", "-t", f"{secs:.3f}", str(path),
    ])
    return path


def _black_segments(path: Path) -> int:
    proc = utils.ffmpeg(["-i", str(path), "-vf",
                         "blackdetect=d=0.30:pix_th=0.10", "-an", "-f", "null", "-"],
                        check=False)
    text = (proc.stderr or "") + (proc.stdout or "")
    return sum(1 for line in text.splitlines() if "blackdetect" in line)


def test_mixed_sources_concat_then_xfade_no_timebase_error(tmp_path):
    """不同来源/帧率/尺寸 + 先 concat 再 xfade：不得再报 timebase 不一致。"""
    specs = [
        ("c1", "red", 1.2, 24, (320, 180)),
        ("c2", "green", 1.4, 25, (640, 360)),
        ("c3", "blue", 1.0, 30, (320, 240)),
        ("c4", "yellow", 1.6, 25, (160, 90)),
        ("c5", "white", 0.8, 25, (320, 120)),
    ]
    clips = {}
    for sid, color, secs, fps, size in specs:
        clips[sid] = _make_varied_clip(tmp_path / f"{sid}.mp4", color, secs, fps, size)
    shots = [{"id": sid, "duration": secs} for sid, _c, secs, _f, _s in specs]
    # 关键模式：cut（concat）之后再接 3 个 xfade
    boundaries = [
        {"from": "c1", "to": "c2", "mode": "cut"},
        {"from": "c2", "to": "c3", "mode": "dissolve", "duration": 0.3},
        {"from": "c3", "to": "c4", "mode": "wipeleft", "duration": 0.3},
        {"from": "c4", "to": "c5", "mode": "vblur", "duration": 0.3},
    ]
    expect = editing.expected_duration(shots, boundaries)
    assert expect == pytest.approx(5.1)
    out = editing.xfade_chain(shots, clips, tmp_path, fps=25, width=320,
                              height=180, total=expect, boundaries=boundaries)
    assert utils.probe_duration(out) == pytest.approx(expect, abs=0.15)
    assert _black_segments(out) == 0


def test_previous_concat_output_reused_as_input(tmp_path):
    """上一轮 xfade_chain 产物（concat 中间结果）再作为新链输入。"""
    a = _make_clip(tmp_path / "a.mp4", "red")
    b = _make_clip(tmp_path / "b.mp4", "green")
    first = editing.xfade_chain(
        [{"id": "a", "duration": 1.6}, {"id": "b", "duration": 1.6}],
        {"a": a, "b": b}, tmp_path / "step1", fps=25, width=320, height=180,
        total=3.2, boundaries=[{"mode": "cut"}])
    c = _make_clip(tmp_path / "c.mp4", "blue")
    d = _make_varied_clip(tmp_path / "d.mp4", "white", 1.0, 24, (640, 360))
    shots = [{"id": "ab", "duration": 3.2}, {"id": "c", "duration": 1.6},
             {"id": "d", "duration": 1.0}]
    boundaries = [{"from": "ab", "to": "c", "mode": "dissolve", "duration": 0.4},
                  {"from": "c", "to": "d", "mode": "cut"}]
    expect = editing.expected_duration(shots, boundaries)
    out = editing.xfade_chain(shots, {"ab": first, "c": c, "d": d},
                              tmp_path / "step2", fps=25, width=320, height=180,
                              total=expect, boundaries=boundaries)
    assert utils.probe_duration(out) == pytest.approx(expect, abs=0.15)
    assert _black_segments(out) == 0


# ---------------------------------------------------------------------------
# mix_audio_edit：J/L cut
# ---------------------------------------------------------------------------
def test_mix_audio_edit_j_l_cut(tmp_path):
    a1 = _make_audio(tmp_path / "a1.wav", 440, 2.0)
    a2 = _make_audio(tmp_path / "a2.wav", 660, 2.0)
    shots = [
        {"id": "s1", "duration": 2.0, "start": 0.0, "audio": str(a1)},
        {"id": "s2", "duration": 2.0, "start": 2.0, "audio": str(a2)},
    ]
    cuts = [{"shot": "s1", "l_cut": 0.2}, {"shot": "s2", "j_cut": 0.2}]
    out = editing.mix_audio_edit(shots, tmp_path, total=4.0, cuts=cuts)
    assert out.is_file() and out.name == editing.AUDIO_EDITED
    assert utils.probe_duration(out) == pytest.approx(4.0, abs=0.06)
    info = utils.ffprobe_json(out)
    astreams = [s for s in info["streams"] if s.get("codec_type") == "audio"]
    assert len(astreams) == 1
    assert int(astreams[0]["sample_rate"]) == config.SAMPLE_RATE
    assert int(astreams[0]["channels"]) == 2


def test_mix_audio_edit_missing_audio_pads_silence(tmp_path):
    shots = [{"id": "s1", "duration": 1.5, "start": 0.0},
             {"id": "s2", "duration": 1.5, "start": 1.5}]
    out = editing.mix_audio_edit(shots, tmp_path, total=3.0, cuts=None,
                                 out_name="silence.wav")
    assert utils.probe_duration(out) == pytest.approx(3.0, abs=0.06)


# ---------------------------------------------------------------------------
# 甩镜过渡（可选工具）
# ---------------------------------------------------------------------------
def test_whip_pan_transition(two_clips):
    workdir, _shots, clips = two_clips
    out = editing.whip_pan_transition(clips["a"], clips["b"],
                                      workdir / "whip.mp4",
                                      fps=25, width=320, height=180,
                                      duration=0.30)
    assert out.is_file()
    assert utils.probe_duration(out) == pytest.approx(0.30, abs=0.10)
    info = utils.probe_video(out)
    assert (info["width"], info["height"]) == (320, 180)
