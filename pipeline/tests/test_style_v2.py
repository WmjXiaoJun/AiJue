"""style_v2（contract/style_presets.json）测试：映射/调色/包装/转场/资产回退。"""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from pipeline import style_v2, utils  # noqa: E402


def _transparent_png(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    utils.ffmpeg(["-f", "lavfi", "-i", "color=c=black@0.0:s=256x384:d=1",
                  "-vf", "format=rgba,drawbox=x=80:y=60:w=96:h=260:color=white@1:t=fill",
                  "-frames:v", "1", "-pix_fmt", "rgba", str(path)])


def test_profile_mapping_16_templates():
    presets = style_v2.load_presets()
    templates = presets["templates"]
    assert len(templates) == 16
    checks = {
        "tonight_show_5min": ("cinematic_3d", "cutout_3d"),
        "shark_tank_5min": ("cinematic_3d", "cutout_3d"),
        "masked_singer_5min": ("neon_stage_3d", "cutout_3d"),
        "standup_5min": ("retro_standup_2d", "cutout_2d"),
        "taskmaster_5min": ("game_show_3d", "cutout_3d"),
        "traitors_5min": ("roundtable_3d", "cutout_3d"),
        "snl_sketch_5min": ("sketch_3d", "cutout_3d"),
        "roundtable_5min": ("roundtable_3d", "cutout_3d"),
        "hot_ones_5min": ("warm_talk_3d", "cutout_3d"),
        "qipashuo_5min": ("debate_ink_2d", "cutout_2d"),
        "slow_life_5min": ("clay_life_3d", "cutout_3d"),
        "history_talk_5min": ("history_ink_2d", "cutout_2d"),
        "history_mystery_5min": ("history_ink_2d", "cutout_2d"),
        "classics_5min": ("history_ink_2d", "cutout_2d"),
        "national_treasure_5min": ("museum_gold_2d", "cutout_2d"),
        "mystery_5min": ("noir_mystery_3d", "cutout_3d"),
    }
    for fid, (preset, char_style) in checks.items():
        p = style_v2.profile_for(fid)
        assert p["preset"] == preset, (fid, p["preset"])
        assert p["character_style"] == char_style
        assert p["palette"] and p["prompt_prefix"]
    # 模板级 overrides
    assert style_v2.profile_for("shark_tank_5min")["palette"] == "cold_blue_gold"
    assert style_v2.profile_for("history_mystery_5min")["transition"] == "fade_black"
    assert style_v2.profile_for("classics_5min")["palette"] == "ink_sepia"
    # 手动覆盖 preset
    assert style_v2.profile_for("tonight_show_5min", override="neon_stage_3d")["preset"] == "neon_stage_3d"


def test_grade_and_packaging_distinct():
    presets = style_v2.load_presets()
    grades = set()
    for preset in presets["presets"]:
        profile = style_v2.profile_for("", override=preset)
        g = style_v2.grade_filter(profile)
        assert g, preset
        grades.add(g)
        transition = style_v2.transition_args(profile)
        assert transition[0] in ("cut", "xfade")
    assert len(grades) >= 10
    packs = set()
    for preset in presets["presets"]:
        profile = style_v2.profile_for("", override=preset)
        filt = style_v2.packaging_filters(profile, Path("outputs/style_demos_v2/_test"),
                                          1280, 720, "fonts/Deng.ttf")
        assert "drawtext" in filt and "style_tag.txt" in filt
        packs.add(filt.split("fontsize")[0][:120])
    assert len(packs) >= 5


def test_plate_prefers_style_v2(tmp_path):
    plate_dir = tmp_path / "plates"
    plate_dir.mkdir()
    for name in ("late_night_desk.png", "late_night_desk_3d.png",
                 "late_night_desk__cinematic.png"):
        (plate_dir / name).write_bytes(b"x")
    profile = style_v2.profile_for("tonight_show_5min")
    p, level, fb = style_v2.plate_path(profile, "late_night_desk", plate_dir=plate_dir)
    assert p.name == "late_night_desk__cinematic.png"
    assert level == "style_v2" and fb is False
    # 缺失 → 回退 _3d / base 并标记 fallback
    p2, level2, fb2 = style_v2.plate_path(profile, "history_study", plate_dir=plate_dir)
    assert fb2 is True


def test_cutout_prefers_profile_style(tmp_path):
    chars = tmp_path / "chars"
    _transparent_png(chars / "laozhou" / "cutout_3d" / "front.png")
    _transparent_png(chars / "laozhou" / "cutout_2d" / "front.png")
    p3, used3, fb3 = style_v2.cutout_path(
        style_v2.profile_for("tonight_show_5min"), "laozhou", chars_dir=chars)
    assert "cutout_3d" in p3.as_posix() and used3 == "cutout_3d"
    p2, used2, fb2 = style_v2.cutout_path(
        style_v2.profile_for("standup_5min"), "laozhou", chars_dir=chars)
    assert "cutout_2d" in p2.as_posix() and used2 == "cutout_2d"


def test_scenes_meta_style_profile(tmp_path):
    from pipeline import scenes
    profile = style_v2.profile_for("qipashuo_5min")
    shot = {"id": "s1", "type": "host_to_camera", "duration": 0.6,
            "speaker": "host",
            "shot_plan": {"size": "Medium", "angle": "eye-level",
                          "movement": "slow pan", "purpose": "explain"}}
    assets = [{"id": "host", "role": "host", "name": "老周",
               "images": ["/media/characters/laozhou/portrait_from_concept.png"]}]
    plate_dir = REPO_ROOT / "outputs" / "scene_plates"
    plate = plate_dir / "debate_hall__debateink.png"
    info = scenes.compose_shot(shot, assets, tmp_path / "w", tmp_path / "s1.mp4",
                               template_id="qipashuo", plate=plate,
                               width=640, height=360, fps=25, profile=profile)
    assert info["style_profile"] == "debate_ink_2d"
    assert info["style_palette"] == "ink_red_gold"
    assert info["character"]
