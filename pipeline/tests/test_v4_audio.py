# -*- coding: utf-8 -*-
"""V4 语音/时间线单测（CPU，不依赖 GPU/Breeze 模型）。"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from pipeline import timeline as timeline_mod  # noqa: E402
from pipeline import voiced  # noqa: E402


# ---------------------------------------------------------------------------
# H3 时长网格
# ---------------------------------------------------------------------------
def test_h3_frames_grid_and_bounds():
    assert voiced.h3_frames(5.0) == 124            # 最小 5.17s（124 帧）
    assert voiced.h3_frames(5.18) in (124, 141)    # 1 帧容差内
    assert voiced.h3_frames(5.25) == 141           # 向上取整
    assert voiced.h3_frames(6.0) == 158
    assert voiced.h3_frames(100.0) == 362          # 训练上限
    for sec in (5.0, 5.2, 6.7, 9.9, 12.4):
        n = voiced.h3_frames(sec)
        assert (n - 5) % 17 == 0
        assert 124 <= n <= 362
        assert n / 24 >= sec - 1.0 / 24 - 1e-6  # 允许 1 帧容差


def test_target_duration_includes_lead_tail():
    tts = 4.0
    dur = voiced.target_duration(tts)
    assert dur >= tts + voiced.LEAD_SEC + voiced.TAIL_SEC - 1e-6
    assert abs(dur * 24 - round(dur * 24)) < 0.01


# ---------------------------------------------------------------------------
# 长句切分
# ---------------------------------------------------------------------------
def test_split_text_short_unchanged():
    text = "今天这期，我们聊一个得罪人的话题。"
    assert voiced.split_text(text, max_chars=50) == [text]


def test_split_text_long_splits_at_punctuation():
    text = ("欢迎回到造片局。今天这期我们聊一个得罪人的话题：" "AI 主持人会不会抢走深夜秀的饭碗？"
            "先说结论：不会，但会改行。")
    parts = voiced.split_text(text, max_chars=20)
    assert len(parts) >= 2
    assert all(len(p) <= 20 for p in parts)
    assert "".join(parts).replace(" ", "") == text.replace(" ", "")


# ---------------------------------------------------------------------------
# 每镜音轨
# ---------------------------------------------------------------------------
def test_build_shot_audio_without_voice(tmp_path):
    shot = {"id": "s1", "duration": 5.17}
    info = voiced.build_shot_audio(shot, None, tmp_path, force=True)
    assert Path(info["path"]).is_file()
    assert 4.9 <= info["duration"] <= 5.4


def test_prepare_and_pace_paces_shots(tmp_path, monkeypatch):
    """用假 TTS 替换合成函数，验证「语音时长 → 镜头时长」与超长切分。"""
    fake_voices: dict[tuple, dict] = {}

    def fake_synth(plan, pairs, workdir, force, prog=None):
        for character, text in pairs:
            wav = Path(workdir) / "voices" / f"fake_{len(fake_voices)}.wav"
            wav.parent.mkdir(parents=True, exist_ok=True)
            import subprocess
            ff = str(REPO / "tools" / "node_modules" / "ffmpeg-static" / "ffmpeg.exe")
            dur = max(0.8, len(text) / 20.0)
            subprocess.run([ff, "-y", "-hide_banner", "-loglevel", "error",
                            "-f", "lavfi", "-i", f"sine=frequency=220:duration={dur:.2f}",
                            "-ar", "48000", "-ac", "2", str(wav)], check=True)
            fake_voices[(character, text)] = {
                "path": str(wav), "duration": round(dur, 3), "provider": "fake",
                "voice": "fake", "seed": 1, "character": character, "text": text,
                "atempo": 1.0,
            }
        return fake_voices

    monkeypatch.setattr(voiced, "_synth_lines", fake_synth)
    plan = {
        "shots": [
            {"id": "a", "no": 1, "type": "dialogue", "speaker": "laozhou",
             "duration": 5.17, "line": "短句。", "segment_id": "seg1"},
            {"id": "b", "no": 2, "type": "dialogue", "speaker": "ali",
             "duration": 5.17,
             "line": "这是一段相当长的台词，" * 12, "segment_id": "seg1"},
            {"id": "c", "no": 3, "type": "b_roll", "duration": 5.17,
             "line": "", "segment_id": "seg2"},
        ],
        "assets": [
            {"id": "laozhou", "character_id": "laozhou", "voice": "v1"},
            {"id": "ali", "character_id": "ali", "voice": "v2"},
        ],
        "episode": {"resolution": "1280x720", "width": 1280, "height": 720},
        "meta": {},
    }
    info = voiced.prepare_and_pace(plan, tmp_path, force=True)
    shots = plan["shots"]
    assert len(shots) >= 4  # 长句被切成多镜
    assert info["speech_shots"] >= 2
    for shot in shots:
        dur = float(shot["duration"])
        assert abs(dur * 24 - round(dur * 24)) < 0.01
        if shot.get("speech_duration"):
            assert dur >= shot["speech_duration"] + voiced.LEAD_SEC + voiced.TAIL_SEC - 1e-6
    assert info["splits"], "长句应记录切分"


# ---------------------------------------------------------------------------
# timeline / edit_v4
# ---------------------------------------------------------------------------
def test_timeline_build_and_write(tmp_path):
    paths = {"shots_dir": tmp_path / "shots", "workdir": tmp_path / "work",
             "out_dir": tmp_path / "out"}
    for d in paths.values():
        d.mkdir(parents=True, exist_ok=True)
    (paths["shots_dir"] / "s1.mp4").write_bytes(b"x")
    plan = {
        "shots": [
            {"id": "s1", "duration": 5.17, "type": "dialogue", "speaker_name": "老周",
             "role": "host", "line": "你好。", "speech_duration": 4.2,
             "voice": {"provider": "fake", "voice": "v", "seed": 1, "character": "laozhou"},
             "segment_id": "seg1"},
            {"id": "s2", "duration": 2.0, "type": "end_card", "static_card": True},
        ],
        "episode": {"resolution": "1280x720", "width": 1280, "height": 720},
        "meta": {"speech_driven": True, "voice_provider": "fake", "lead_sec": 0.1},
    }
    tl = timeline_mod.build_timeline(plan, {"id": "tpl"}, paths)
    assert tl["version"] == 4
    tracks = {t["id"]: t for t in tl["tracks"]}
    assert len(tracks["V1"]["clips"]) == 2
    assert len(tracks["A1"]["clips"]) == 1
    assert tracks["A1"]["clips"][0]["atempo"] == 1.0
    assert len(tracks["S1"]["clips"]) == 1
    art = timeline_mod.write_timeline(tl, paths["out_dir"])
    assert Path(art["json"]).is_file() and Path(art["html"]).is_file()
    loaded = timeline_mod.load_timeline(art["json"])
    assert loaded["template"] == "tpl"
    html = Path(art["html"]).read_text(encoding="utf-8")
    assert "Timeline v4" in html


def test_edit_v4_build_ass(tmp_path):
    from pipeline import edit_v4
    tl = {"version": 4, "template": "tpl", "duration": 5.0,
          "tracks": [
              {"id": "S1", "kind": "subtitle", "clips": [
                  {"id": "s1", "text": "第一句", "at": 0.2, "out": 2.0, "speaker": "老周"},
                  {"id": "s2", "text": "第二句", "at": 2.2, "out": 4.0, "speaker": "阿栗"},
              ]},
              {"id": "A1", "kind": "voice", "clips": []},
          ]}
    ass = tmp_path / "x.ass"
    count = edit_v4._build_ass(tl, ass)
    assert count == 2
    text = ass.read_text(encoding="utf-8")
    assert "Dialogue: 0,0:00:00.20" in text
    assert "第一句" in text
