# -*- coding: utf-8 -*-
"""V4 响度修复自测：两遍 loudnorm（render.py）+ qc_v3 loudness 硬门。

背景：classics V4 成片单遍动态 loudnorm 欠调到 -18.15 LUFS（要求 -17~-15），
QA 定位 PLAN_V4 §2.3 要求两遍（先测量后应用）。本测试用 CPU/ffmpeg 验证：
- render.measure_loudness / _loudnorm_filter / render_mix_wav 工作正常；
- final_encode 两遍 loudnorm 后成片 LUFS ∈ [-17,-15] 且 TP ≤ -1.0（含 duck_bgm 生产链路）；
- qc_v3 新增 loudness check 能识别欠调/过响。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from pipeline import config, qc_v3, render, utils

FF = str(config.FFMPEG_BIN)
DUR = 2.5


def _ffmpeg(args):
    utils.ffmpeg(args)


def make_video(path: Path, *, duration: float = DUR) -> Path:
    utils.ensure_dir(Path(path).parent)
    _ffmpeg(["-f", "lavfi", "-i", f"testsrc2=size=1280x720:rate=25",
             "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo",
             "-t", f"{duration:.3f}", "-c:v", "libx264", "-preset", "ultrafast",
             "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", "-y", str(path)])
    return path


def make_audio(path: Path, *, duration: float = DUR, freq: int = 440,
               volume_db: float = 0.0) -> Path:
    utils.ensure_dir(Path(path).parent)
    _ffmpeg(["-f", "lavfi",
             "-i", f"sine=frequency={freq}:sample_rate=48000:duration={duration:.3f}",
             "-af", f"volume={volume_db}dB,aformat=channel_layouts=stereo",
             "-c:a", "pcm_s16le", "-y", str(path)])
    return path


def make_noisy_audio(path: Path, *, duration: float = DUR,
                     volume_db: float = -26.0) -> Path:
    """模拟高 LRA 旁白：低电平 + 短暂强峰（单遍动态 loudnorm 容易欠调的形态）。"""
    utils.ensure_dir(Path(path).parent)
    expr = ("if(between(t,0.6,1.0)+between(t,1.6,1.9),"
            f"0.55,0.02)*sin(2*PI*320*t)")
    _ffmpeg(["-f", "lavfi",
             "-i", f"aevalsrc=exprs='{expr}|{expr}':s=48000:d={duration:.3f}",
             "-af", f"volume={volume_db}dB,aformat=channel_layouts=stereo",
             "-c:a", "pcm_s16le", "-y", str(path)])
    return path


def synth_video_with_audio(path: Path, audio_filter: str,
                           duration: float = DUR) -> Path:
    utils.ensure_dir(Path(path).parent)
    _ffmpeg(["-f", "lavfi", "-i", "testsrc2=size=1280x720:rate=25",
             "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000",
             "-af", audio_filter, "-t", f"{duration:.3f}",
             "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
             "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2",
             "-shortest", "-y", str(path)])
    return path


# ---------------------------------------------------------------------------
# 1) 测量 / 滤镜工具
# ---------------------------------------------------------------------------
def test_measure_loudness_orders_quiet_vs_loud(tmp_path):
    quiet = make_audio(tmp_path / "quiet.wav", volume_db=-30)
    loud = make_audio(tmp_path / "loud.wav", volume_db=-6)
    mq = render.measure_loudness(quiet)
    ml = render.measure_loudness(loud)
    assert mq and ml, "loudnorm 测量应成功"
    assert mq["i"] < ml["i"]
    assert mq["tp"] <= ml["tp"] + 0.1
    for key in ("i", "tp", "lra", "thresh", "offset"):
        assert isinstance(mq[key], float)


def test_measure_loudness_missing_file_returns_none(tmp_path):
    assert render.measure_loudness(tmp_path / "nope.wav") is None


def test_loudnorm_filter_uses_measured_params():
    measured = {"i": -22.26, "tp": -5.73, "lra": 10.6,
                "thresh": -34.1, "offset": 2.04}
    two_pass = render._loudnorm_filter(measured)
    assert "measured_I=-22.26" in two_pass
    assert "measured_TP=-5.73" in two_pass
    assert "offset=2.04" in two_pass
    assert "linear=true" in two_pass
    single = render._loudnorm_filter(None)
    assert "measured_I" not in single and "linear=true" not in single
    assert "loudnorm=I=" in single


# ---------------------------------------------------------------------------
# 2) final_encode：两遍 loudnorm 达标（含 V4 duck_bgm 生产链路）
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("duck", [False, True])
def test_final_encode_two_pass_hits_loudness_range(tmp_path, duck):
    workdir = tmp_path / ("work_duck" if duck else "work_plain")
    utils.ensure_dir(workdir / "audio")
    utils.ensure_dir(workdir / "bgm")
    utils.ensure_dir(workdir / "sfx")
    make_video(workdir / "video_raw.mp4")
    # 低电平旁白（模拟 classics 欠调场景）→ 两遍 loudnorm 应准确拉到 -16
    make_noisy_audio(workdir / "audio" / "narration.wav")
    bgm = make_audio(workdir / "bgm" / "soft_pad.wav", duration=4.0,
                     freq=220, volume_db=-20) if duck else None
    tsx = make_audio(workdir / "sfx" / "transitions.wav", freq=880,
                     volume_db=-26) if duck else None
    out = tmp_path / ("final_duck.mp4" if duck else "final_plain.mp4")

    render.final_encode(workdir, out, DUR, 25, 1280, 720, bgm, has_ass=False,
                        prog=lambda *a, **k: None, pct0=0, pct1=100,
                        transition_sfx=tsx, duck_bgm=duck)

    assert out.is_file() and out.stat().st_size > 10000
    assert (workdir / "audio" / "_final_mix.wav").is_file(), "两遍 loudnorm 应先渲混音 wav"
    probe = utils.probe_video(out)
    assert (probe["width"], probe["height"]) == (1280, 720)
    assert probe["has_audio"]

    info = qc_v3.measure_loudness(out)
    assert info and info.get("ok"), f"成片响度测量失败: {info}"
    low, high = qc_v3.LOUDNESS_RANGE
    assert low <= info["i"] <= high, (
        f"两遍 loudnorm 后 integrated LUFS={info['i']:.2f} 不在 [{low},{high}]")
    assert info["tp"] <= qc_v3.LOUDNESS_TP_MAX, (
        f"true peak={info['tp']:.2f} 超过 {qc_v3.LOUDNESS_TP_MAX}")


def test_enforce_loudness_corrects_quiet_video(tmp_path):
    """编码后闭环：欠调成片（-25 LUFS）能被 enforce_loudness 修正进 -17~-15。"""
    video = synth_video_with_audio(tmp_path / "quiet25.mp4", "volume=-25dB")
    before = qc_v3.measure_loudness(video)
    assert before["i"] < -18
    info = render.enforce_loudness(video, max_rounds=2)
    assert info is not None
    assert render.loudness_ok(info, tight=False), (
        f"闭环修正后仍不达标: I={info['i']:.2f} TP={info['tp']:.2f}")
    assert info["tp"] <= qc_v3.LOUDNESS_TP_MAX


def test_final_encode_sparse_content_hits_range(tmp_path):
    """稀疏内容（大量静音 + 低音量 BGM）走两遍+闭环后也应达标（防 classics 类回归）。"""
    workdir = tmp_path / "work_sparse"
    utils.ensure_dir(workdir / "audio")
    utils.ensure_dir(workdir / "bgm")
    make_video(workdir / "video_raw.mp4")
    expr = ("if(between(t,0.2,0.5)+between(t,1.8,2.05),"
            "0.5*sin(2*PI*300*t),0)")
    _ffmpeg(["-f", "lavfi",
             "-i", f"aevalsrc=exprs='{expr}|{expr}':s=48000:d={DUR:.3f}",
             "-c:a", "pcm_s16le", "-y", str(workdir / "audio" / "narration.wav")])
    bgm = make_audio(workdir / "bgm" / "soft_pad.wav", duration=4.0,
                     freq=220, volume_db=-24)
    out = tmp_path / "final_sparse.mp4"
    render.final_encode(workdir, out, DUR, 25, 1280, 720, bgm, has_ass=False,
                        prog=lambda *a, **k: None, pct0=0, pct1=100,
                        duck_bgm=True)
    info = qc_v3.measure_loudness(out)
    assert info and info.get("ok")
    assert render.loudness_ok(info, tight=False), (
        f"稀疏内容成片响度未达标: I={info['i']:.2f} TP={info['tp']:.2f}")


# ---------------------------------------------------------------------------
# 3) qc_v3 loudness check
# ---------------------------------------------------------------------------
def _quiet_plan(tmp_path: Path):
    ref = tmp_path / "ref.png"
    from PIL import Image
    utils.ensure_dir(ref.parent)
    Image.new("RGB", (64, 36), (40, 60, 90)).save(ref)
    shot = {"id": "v3_s001", "no": 1, "type": "shot_reverse",
            "duration": DUR, "line": "", "h3_mode": "audio_ref"}
    plan = {"episode": {"resolution": "1280x720", "fps": 25},
            "shots": [shot]}
    meta = {"id": "v3_s001", "h3": False, "previs": True,
            "ref_image": str(ref)}
    return plan, {"v3_s001": meta}


def test_qc_loudness_check_flags_quiet_video(tmp_path):
    video = synth_video_with_audio(tmp_path / "quiet.mp4", "volume=-30dB")
    plan, metas = _quiet_plan(tmp_path)
    report = qc_v3.evaluate(video=video, plan=plan, shot_metas=metas,
                            template={"id": "tonight_show"}, subtitles=None,
                            h3_require=False)
    check = report["checks"]["loudness"]
    assert check["ok"] is False
    assert check["loudness"] < qc_v3.LOUDNESS_RANGE[0]
    assert report["stats"]["lufs"] is not None
    assert report["ok"] is False


# ---------------------------------------------------------------------------
# 4) scripts/fix_loudness.py：存量成片批量修复入口（CPU）
# ---------------------------------------------------------------------------
def _load_fix_script():
    import importlib.util
    path = Path(__file__).resolve().parents[2] / "scripts" / "fix_loudness.py"
    spec = importlib.util.spec_from_file_location("fix_loudness", path)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


def test_fix_loudness_audio_only_repairs_and_backs_up(tmp_path):
    mod = _load_fix_script()
    out_dir = tmp_path / "tonight_show"
    utils.ensure_dir(out_dir)
    (out_dir / "timeline.json").write_text("{}", encoding="utf-8")  # 视为 V4 产物
    final = synth_video_with_audio(out_dir / "final.mp4", "volume=-25dB")
    before = qc_v3.measure_loudness(final)
    assert before["i"] < -18
    record = mod.fix_one(out_dir, "tonight_show", audio_only=True, max_rounds=3)
    assert record["ok"] is True, record
    assert record["method"] == "audio_only"
    assert (out_dir / "final.preloud.mp4").is_file(), "修复前应留备份"
    after = qc_v3.measure_loudness(final)
    assert -17.0 <= after["i"] <= -15.0 and after["tp"] <= -1.0
    # re_eval_qc 至少要把 loudness 写回 qc_report
    report_path = out_dir / "qc_report.json"
    if report_path.is_file():
        report = utils.read_json(report_path) or {}
        stats = report.get("stats") or {}
        assert stats.get("lufs") is not None
        assert report.get("checks", {}).get("loudness", {}).get("ok") is True


def test_fix_loudness_cli_scan_and_fix(tmp_path, capsys):
    mod = _load_fix_script()
    root = tmp_path / "root"
    out_dir = root / "tonight_show"
    utils.ensure_dir(out_dir)
    (out_dir / "timeline.json").write_text("{}", encoding="utf-8")
    synth_video_with_audio(out_dir / "final.mp4", "volume=-25dB")
    # 扫描模式（不修复）
    rc = mod.main(["--root", str(root)])
    assert rc == 0
    scanned = utils.read_json(root / "loudness_fix_report.json") or {}
    assert scanned.get("mode") == "scan"
    assert scanned["results"][0]["needs_fix"] is True
    # 修复模式
    rc = mod.main(["--root", str(root), "--fix", "--audio-only"])
    assert rc == 0
    report = utils.read_json(root / "loudness_fix_report.json") or {}
    assert report.get("mode") == "fix"
    assert "tonight_show" in (report.get("fixed") or [])
    assert report.get("failed") == []
    after = qc_v3.measure_loudness(out_dir / "final.mp4")
    assert -17.0 <= after["i"] <= -15.0


def test_fix_loudness_skips_in_range(tmp_path):
    mod = _load_fix_script()
    out_dir = tmp_path / "tonight_show"
    utils.ensure_dir(out_dir)
    (out_dir / "timeline.json").write_text("{}", encoding="utf-8")
    # 用 final_encode 产出一版达标成片
    workdir = tmp_path / "wk"
    utils.ensure_dir(workdir / "audio")
    make_video(workdir / "video_raw.mp4")
    make_noisy_audio(workdir / "audio" / "narration.wav")
    render.final_encode(workdir, out_dir / "final.mp4", DUR, 25, 1280, 720,
                        None, has_ass=False, prog=lambda *a, **k: None,
                        pct0=0, pct1=100)
    record = mod.fix_one(out_dir, "tonight_show", max_rounds=2)
    assert record["ok"] is True and record.get("skipped") is True
    assert record.get("reason") == "响度已达标"


def test_qc_loudness_check_passes_in_range_video(tmp_path):
    # 用 final_encode（两遍）产出合规音频
    workdir = tmp_path / "work"
    utils.ensure_dir(workdir / "audio")
    make_video(workdir / "video_raw.mp4")
    make_noisy_audio(workdir / "audio" / "narration.wav")
    video = tmp_path / "ok.mp4"
    render.final_encode(workdir, video, DUR, 25, 1280, 720, None, has_ass=False,
                        prog=lambda *a, **k: None, pct0=0, pct1=100)
    plan, metas = _quiet_plan(tmp_path)
    report = qc_v3.evaluate(video=video, plan=plan, shot_metas=metas,
                            template={"id": "tonight_show"}, subtitles=None,
                            h3_require=False)
    check = report["checks"]["loudness"]
    assert check["ok"] is True, check
    assert check["true_peak"] <= qc_v3.LOUDNESS_TP_MAX
    assert -17.0 <= check["loudness"] <= -15.0
