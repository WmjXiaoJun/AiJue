# -*- coding: utf-8 -*-
"""V4 编排集成测试：mock stage/h3 + fake 配音 → 语音驱动镜头 + timeline + audio_pace。"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))


def test_render_v3_v4_speech_driven_mock(monkeypatch, tmp_path):
    from pipeline import render_v3, utils, voiced
    from pipeline.tests.test_v3_render import _install_fake_modules, _namespace

    _install_fake_modules(monkeypatch, tmp_path)
    ff = str(REPO / "tools" / "node_modules" / "ffmpeg-static" / "ffmpeg.exe")

    def fake_synth(plan, pairs, workdir, force, prog=None):
        results = {}
        for i, (character, text) in enumerate(pairs):
            dur = max(1.6, min(4.0, len(text) / 6.0))
            wav = Path(workdir) / "voices" / f"fake_{i}_{character}.wav"
            wav.parent.mkdir(parents=True, exist_ok=True)
            subprocess.run([ff, "-y", "-hide_banner", "-loglevel", "error",
                            "-f", "lavfi", "-i", f"sine=frequency=220:duration={dur:.2f}",
                            "-ar", "48000", "-ac", "2", str(wav)], check=True)
            results[(character, text)] = {
                "path": str(wav), "duration": round(dur, 3), "provider": "fake",
                "voice": "fake-voice", "seed": 1, "character": character,
                "text": text, "atempo": 1.0,
            }
        return results

    monkeypatch.setattr(voiced, "_synth_lines", fake_synth)
    ns = _namespace(tmp_path)
    ns.legacy_audio = False
    rc = render_v3.run_render(ns)

    qc = utils.read_json(tmp_path / "qc_report.json") or {}
    ep = utils.read_json(tmp_path / "work" / "episode.json") or {}
    shots = ep.get("shots") or []
    plan_total = round(sum(float(s.get("duration") or 0) for s in shots), 3)
    probe = utils.probe_video(tmp_path / "final.mp4")

    assert rc == 0, f"QC 应通过: {qc.get('checks')}"
    assert qc["ok"] is True
    assert abs(probe["duration"] - plan_total) <= 0.6
    assert qc["stats"]["voice_provider"] == "fake"
    assert qc["stats"]["speech_driven"] is True
    assert qc["stats"]["atempo_max"] == 1.0
    assert qc["stats"]["truncated_shots"] == []
    assert qc["checks"]["audio_pace"]["ok"] is True
    assert (tmp_path / "timeline.json").is_file()
    assert (tmp_path / "timeline.html").is_file()
    # 每镜时长落在 H3 网格上，且 >= 语音 + lead/tail - 1 帧容差
    for shot in shots:
        dur = float(shot["duration"])
        assert (round(dur * 24) - 5) % 17 == 0 or abs(dur * 24 - round(dur * 24)) < 0.01
        if shot.get("speech_duration"):
            assert dur >= shot["speech_duration"] + 0.1 + 0.3 - 0.05
