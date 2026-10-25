"""动态镜头 / xfade 转场 / 每镜 TTS wav 测试（H3 CLI 用假实现）。"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from pipeline import config, dynamic, render, utils  # noqa: E402

FF = config.FFMPEG_BIN


def _color_clip(path: Path, seconds: float, color: str = "0x304050") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    utils.ffmpeg(["-f", "lavfi", "-i", f"color=c={color}:s=1280x720:d={seconds}",
                  "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
                  "-r", "25", str(path)])


def _silent_wav(path: Path, seconds: float = 1.0) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    utils.ffmpeg(["-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo", "-t",
                  f"{max(0.1, seconds):.3f}", "-c:a", "pcm_s16le", str(path)])


def _episode() -> dict:
    return {
        "episode": {"id": "ep_dyn_test", "title": "dyn", "resolution": "1280x720",
                    "fps": 25, "aspect": "16:9"},
        "assets": [{"id": "host", "role": "host", "type": "character",
                    "name": "老周", "title": "主持人", "accent_color": "#F5C518"}],
        "shots": [
            {"id": "s001", "type": "title_card", "duration": 1.0, "title": "T"},
            {"id": "s002", "type": "host_to_camera", "duration": 2.0,
             "speaker": "host", "speaker_name": "老周", "line": "测试台词"},
            {"id": "s003", "type": "reaction", "duration": 1.0,
             "speaker": "host", "speaker_name": "老周", "line": ""},
            {"id": "s004", "type": "end_card", "duration": 1.0},
        ],
    }


FAKE_CLI = r'''# -*- coding: utf-8 -*-
import argparse, subprocess, sys
ap = argparse.ArgumentParser()
ap.add_argument("--mode", required=True)
ap.add_argument("--image", required=True)
ap.add_argument("--last-frame", default=None)
ap.add_argument("--audio", default=None)
ap.add_argument("--prompt-file", default=None)
ap.add_argument("--prompt", default=None)
ap.add_argument("--out", required=True)
ap.add_argument("--duration", type=float, default=1.0)
ap.add_argument("--width", type=int, default=320)
ap.add_argument("--height", type=int, default=180)
ap.add_argument("--fps", type=int, default=25)
ap.add_argument("--seed", type=int, default=42)
a = ap.parse_args()
dur = max(0.3, min(float(a.duration), 1.0))
ff = r"__FFMPEG__"
subprocess.run([ff, "-y", "-hide_banner", "-loglevel", "error", "-f", "lavfi",
                "-i", f"testsrc2=size={a.width}x{a.height}:rate={a.fps}:duration={dur}",
                "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
                "-an", a.out], check=True)
print("fake h3 ok", a.mode, a.out)
'''


def _write_fake_cli(tmp_path: Path) -> Path:
    cli = tmp_path / "fake_h3_ref.py"
    cli.write_text(FAKE_CLI.replace("__FFMPEG__", str(FF).replace("\\", "\\\\")),
                   encoding="utf-8")
    return cli


# --------------------------------------------------------------- unit ----
def test_export_shot_tts_audio(tmp_path):
    work = tmp_path / "work"
    for sid in ("s001", "s002"):
        _silent_wav(work / "audio" / f"{sid}.wav")
    out = render.export_shot_tts_audio([{"id": "s001"}, {"id": "s002"}, {"id": "s003"}],
                                       work)
    assert set(out) == {"s001", "s002"}
    assert Path(out["s001"]).is_file()
    assert Path(out["s001"]).name == "shot_s001.wav"


def test_build_transition_sfx(tmp_path):
    whoosh = tmp_path / "whoosh.wav"
    _silent_wav(whoosh, 0.5)
    out = render.build_transition_sfx(tmp_path / "work", [1.0, 2.0], whoosh, 3.0)
    assert out is not None and out.is_file()
    assert abs(utils.probe_duration(out) - 3.0) < 0.2


def test_concat_video_xfade_keeps_duration(tmp_path):
    shots = [{"id": "s1", "duration": 1.0}, {"id": "s2", "duration": 1.0},
             {"id": "s3", "duration": 1.0}]
    colors = ["0x304050", "0x503040", "0x405030"]
    paths = {}
    for shot, color in zip(shots, colors):
        p = tmp_path / "work" / "clips" / f"{shot['id']}.mp4"
        _color_clip(p, 1.0, color)
        paths[shot["id"]] = p
    out = render.concat_video_xfade(shots, paths, tmp_path / "work", 25, 1280, 720,
                                    3.0, transition="fade", transition_duration=0.3)
    assert out.is_file()
    assert abs(utils.probe_duration(out) - 3.0) < 0.25


# ------------------------------------------------------------ dynamic ----
def test_dynamic_ensure_fake_cli(tmp_path):
    ep = _episode()
    work = tmp_path / "work"
    shots = ep["shots"]
    for shot in shots:
        _color_clip(work / "clips" / f"{shot['id']}.mp4", float(shot["duration"]))
        _silent_wav(work / "audio" / f"shot_{shot['id']}.wav", 0.8)
    cli = _write_fake_cli(tmp_path)
    tts_map = {s["id"]: str(work / "audio" / f"shot_{s['id']}.wav") for s in shots}
    results = dynamic.ensure_dynamic_clips(
        ep, shots, ep["assets"], work, 1280, 720, 25,
        tts_map=tts_map, cli=cli)
    assert results["s001"]["mode"] == "static-card"
    assert results["s002"]["mode"] == "audio_ref"
    assert results["s003"]["mode"] == "fl2v"
    assert results["s004"]["mode"] == "static-card"
    for sid in ("s002", "s003"):
        assert Path(results[sid]["path"]).is_file()
        meta = json.loads((work / "dynamic" / "meta" / f"{sid}.json").read_text(encoding="utf-8"))
        assert meta["mode"] == results[sid]["mode"]
        assert meta["first_frame"] and Path(meta["first_frame"]).is_file()
        assert meta["last_frame"] and Path(meta["last_frame"]).is_file()
        assert meta.get("plan") and meta["plan"].get("size")
        assert meta.get("prompt", "").startswith(("subject_definitions:", "style:"))
        assert meta.get("ref_strategy")
        assert Path(meta["prompt_file"]).is_file()
    # 二次调用命中缓存
    again = dynamic.ensure_dynamic_clips(
        ep, shots, ep["assets"], work, 1280, 720, 25,
        tts_map=tts_map, cli=cli)
    assert again["s002"]["reused"] is True
    assert again["s003"]["reused"] is True


def test_dynamic_missing_cli_raises_or_falls_back(tmp_path):
    ep = _episode()
    work = tmp_path / "work"
    shots = ep["shots"]
    for shot in shots:
        _color_clip(work / "clips" / f"{shot['id']}.mp4", float(shot["duration"]))
    with pytest.raises(RuntimeError):
        dynamic.ensure_dynamic_clips(ep, shots, ep["assets"], work, 1280, 720, 25,
                                     cli=tmp_path / "missing.py")
    results = dynamic.ensure_dynamic_clips(
        ep, shots, ep["assets"], work, 1280, 720, 25,
        cli=tmp_path / "missing.py", fallback_static=True)
    assert results["s002"]["mode"] == "static-fallback"
    assert results["s002"]["path"].endswith("s002.mp4")


# ------------------------------------------------------- render dynamic ----
def test_render_dynamic_mode_fake(tmp_path):
    ep = _episode()
    ep_path = tmp_path / "episode.json"
    ep_path.write_text(json.dumps(ep, ensure_ascii=False), encoding="utf-8")
    work = tmp_path / "work"
    cli = _write_fake_cli(tmp_path)
    out_path = tmp_path / "final.mp4"

    class Args:
        episode = str(ep_path)
        workdir = str(work)
        out = str(out_path)
        resolution = None
        fps = None
        no_bgm = True
        limit = None
        force = True
        use_h3 = False
        dynamic = True
        dynamic_fallback_static = False
        h3_ref_cli = str(cli)
        transitions = "xfade"
        transition_style = "fade"
        transition_duration = 0.3

    rc = render.run_render(Args())
    assert rc == 0
    manifest = json.loads((work / "manifest.json").read_text(encoding="utf-8"))
    assert out_path.is_file()
    assert abs(utils.probe_duration(out_path) - 5.0) < 0.3
    assert manifest["dynamic"]["dynamic"] == 2
    assert manifest["transition"]["xfade"] is True
    assert manifest["shot_tts_wavs"] >= 1
    for sid in ("s002", "s003"):
        assert (work / "audio" / f"shot_{sid}.wav").is_file()


# ------------------------------------------------------- ShotPlan prompt ----
def test_plan_for_shot_and_prompt():
    from pipeline import shot_grammar as sg
    plan = sg.plan_for_shot({"type": "host_to_camera", "no": 2})
    assert plan["size"] == "Close-up" and plan["angle"] == "low-angle"
    prompt = sg.build_h3_prompt(plan, "audio_ref")
    assert "One continuous close-up (head and shoulders) shot" in prompt
    assert "low angle, looking slightly up" in prompt
    assert "below eye-level (low camera position) camera height" in prompt
    assert "slow dolly-in toward the face (translation only, no zoom)" in prompt
    assert "only this movement, no zoom/pan mix" in prompt
    assert "Purpose: explain" in prompt
    assert "no text, no watermark" in prompt.lower()
    # shot.shot_plan 优先
    plan2 = sg.plan_for_shot({"type": "reaction",
                              "shot_plan": {"size": "Extreme Close-up",
                                            "movement": "static",
                                            "light": "low-key practical"}})
    assert plan2["size"] == "Extreme Close-up"
    prompt2 = sg.build_h3_prompt(plan2, "fl2v")
    assert "extreme close-up (face only)" in prompt2
    assert "zero camera movement" in prompt2
    assert "first frame" in prompt2 and "last frame" in prompt2
    for word in ("cinematic", "moody", "epic"):
        assert word not in prompt2.lower()

