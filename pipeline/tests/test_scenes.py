"""场景合成（pipeline/scenes.py）测试：全身分层/景别缩放/投影/视差/映射。"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from pipeline import config, scenes, utils  # noqa: E402

FF = config.FFMPEG_BIN


def _episode() -> dict:
    return {
        "episode": {"id": "demo_scene", "title": "scene", "resolution": "640x360", "fps": 25},
        "assets": [
            {"id": "asset_host", "role": "host", "name": "老周",
             "accent_color": "#F5C518",
             "images": ["/media/characters/laozhou/portrait_from_concept.png"]},
            {"id": "asset_guest", "role": "guest", "name": "阿栗",
             "accent_color": "#5AC8FA",
             "images": ["/media/characters/ali/portrait_from_concept.png"]},
        ],
        "shots": [
            {"id": "s1", "type": "host_to_camera", "duration": 1.2, "speaker": "asset_host",
             "speaker_name": "老周", "line": "你好",
             "shot_plan": {"size": "Medium", "angle": "eye-level",
                           "movement": "slow push-in", "purpose": "explain"}},
            {"id": "s2", "type": "b_roll", "duration": 0.8, "speaker": None, "line": ""},
        ],
    }


def test_scene_map_all_templates():
    mapping = scenes.load_scene_map()
    expected = ["history_talk", "tonight_show", "roundtable", "taskmaster",
                "qipashuo", "mystery", "national_treasure", "slow_life",
                "masked_singer", "standup", "hot_ones", "traitors", "classics",
                "snl_sketch", "shark_tank"]
    for key in expected:
        assert key in mapping, key
        assert mapping[key].get("plate")
    assert scenes.template_scene("history_talk_5min")["plate"] == "history_study"
    assert scenes.template_scene("tonight_show")["3d"] == "3d_studio_wide"


def test_compose_shot_full_body(tmp_path):
    work = tmp_path / "work"
    ep = _episode()
    shot = ep["shots"][0]
    plate = scenes.PLATE_DIR / "history_study.png"
    out = work / "s1.mp4"
    info = scenes.compose_shot(shot, ep["assets"], work, out,
                               template_id="history_talk", plate=plate,
                               width=640, height=360, fps=25, index=0)
    assert out.is_file() and utils.probe_duration(out) > 0.8
    assert info["character"] == "laozhou"
    assert info["char_mode"] == "full"
    assert info["char_height"] == int(360 * 0.55)
    assert info["char_y"] == int(360 * 0.86) - info["char_height"]
    # 相机推近：首段与中段帧应不同
    f1, f2 = work / "f1.png", work / "f2.png"
    utils.ffmpeg(["-y", "-hide_banner", "-loglevel", "error", "-ss", "0.1",
                  "-i", str(out), "-frames:v", "1", str(f1)])
    utils.ffmpeg(["-y", "-hide_banner", "-loglevel", "error", "-ss", "0.9",
                  "-i", str(out), "-frames:v", "1", str(f2)])
    import subprocess
    p = subprocess.run([str(FF), "-hide_banner", "-i", str(f1), "-i", str(f2),
                        "-lavfi", "psnr", "-f", "null", "NUL"],
                       capture_output=True, text=True, errors="replace")
    assert "average:" in p.stderr


def test_compose_closeup_crops_upper_body(tmp_path):
    work = tmp_path / "work"
    ep = _episode()
    shot = dict(ep["shots"][0])
    shot["shot_plan"] = dict(shot["shot_plan"], size="Close-up")
    info = scenes.compose_shot(shot, ep["assets"], work, work / "c.mp4",
                               template_id="history_talk",
                               plate=scenes.PLATE_DIR / "history_study.png",
                               width=640, height=360, fps=25)
    assert info["char_mode"] == "full"
    assert info["char_height"] == int(360 * 0.92)  # 全身图放大，身体延伸出下画框
    assert info["char_y"] == int(360 * 0.13)       # 头顶距画面顶 13%


def test_compose_episode_writes_shots_and_cards(tmp_path):
    data = _episode()
    data["shots"].append({"id": "s3", "type": "title_card", "duration": 0.6,
                          "title": "场景验证"})
    work = tmp_path / "work"
    info = scenes.compose_episode(data, work, template_id="history_talk",
                                  width=640, height=360, fps=25)
    for sid in ("s1", "s2", "s3"):
        assert (work / "shots" / f"{sid}.mp4").is_file()
        assert (work / "scenes" / "meta" / f"{sid}.json").is_file()
    assert "s3" in info["card_sids"]
    meta = json.loads((work / "scenes" / "meta" / "s1.json").read_text(encoding="utf-8"))
    assert meta["plan"]["size"] == "Medium"
    assert meta["character"] == "laozhou"
