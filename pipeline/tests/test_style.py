"""风格系统（pipeline/style.py）测试：模板映射/前缀/3D 资产优先与回退/prompt 注入。"""
from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from pipeline import shot_grammar, style, utils  # noqa: E402


def _transparent_png(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    utils.ffmpeg(["-f", "lavfi", "-i", "color=c=black@0.0:s=256x384:d=1",
                  "-vf", "format=rgba,drawbox=x=80:y=60:w=96:h=260:color=white@1:t=fill",
                  "-frames:v", "1", "-pix_fmt", "rgba", str(path)])


def test_style_map_mapping():
    name, st = style.resolve("tonight_show_5min")
    assert name == "3d_anime"
    assert "3D" in st["prompt_prefix"]
    name2, st2 = style.resolve("history_talk_5min")
    assert name2 == "2d_anime"
    assert "2D" in st2["prompt_prefix"]
    assert style.resolve("unknown_template")[0] == "3d_anime"
    assert style.resolve("tonight_show_5min", override="2d_anime")[0] == "2d_anime"


def test_plate_path_prefers_3d(tmp_path):
    plate_dir = tmp_path / "plates"
    plate_dir.mkdir()
    (plate_dir / "late_night_desk.png").write_bytes(b"2d")
    (plate_dir / "late_night_desk_3d.png").write_bytes(b"3d")
    p, used, fallback = style.plate_path("late_night_desk", "3d_anime",
                                         plate_dir=plate_dir)
    assert p.name == "late_night_desk_3d.png" and used == "3d_anime"
    assert fallback is False


def test_cutout_path_prefers_3d(tmp_path):
    chars = tmp_path / "chars"
    (chars / "laozhou" / "cutout_3d").mkdir(parents=True)
    (chars / "laozhou" / "cutout_2d").mkdir(parents=True)
    _transparent_png(chars / "laozhou" / "cutout_3d" / "front.png")
    _transparent_png(chars / "laozhou" / "cutout_2d" / "front.png")
    p, used, fb = style.cutout_path("laozhou", "3d_anime", chars_dir=chars)
    assert "cutout_3d" in p.as_posix() and used == "3d_anime" and fb is False
    p2, used2, fb2 = style.cutout_path("ali", "3d_anime", chars_dir=chars)
    assert p2 is None
    p3, used3, _ = style.cutout_path("laozhou", "2d_anime", chars_dir=chars)
    assert "cutout_2d" in p3.as_posix()


def test_prompt_style_injection():
    plan = shot_grammar.plan_for_type("host_to_camera")
    p3 = shot_grammar.build_h3_prompt(
        plan, "audio_ref", scene_description="a late-night studio with a desk",
        style_prefix=style.style_prefix("3d_anime"))
    assert p3.startswith("style:")
    assert "three-dimensional" in p3.lower()
    assert "volumetric" in p3.lower()
    assert "depth of field" in p3.lower()
    assert "late-night studio" in p3
    p2 = shot_grammar.build_h3_prompt(
        plan, "audio_ref", scene_description="a candle-lit library",
        style_prefix=style.style_prefix("2d_anime"))
    assert "Flat 2D hand-drawn" in p2 or "2D anime" in p2
    assert "candle-lit library" in p2


def test_style_scene_meta(tmp_path):
    from pipeline import scenes
    shot = {"id": "s1", "type": "host_to_camera", "duration": 0.6,
            "speaker": "host",
            "shot_plan": {"size": "Medium", "angle": "eye-level",
                          "movement": "static", "purpose": "explain"}}
    assets = [{"id": "host", "role": "host", "name": "老周",
               "images": ["/media/characters/laozhou/portrait_from_concept.png"]}]
    plate = scenes.PLATE_DIR / "history_study.png"
    info = scenes.compose_shot(shot, assets, tmp_path / "w", tmp_path / "s1.mp4",
                               template_id="history_talk", plate=plate,
                               width=640, height=360, fps=25,
                               style_name="2d_anime")
    assert info["style"] == "2d_anime"
    assert info["cutout_style"] in ("2d_anime", "2d_fallback")
