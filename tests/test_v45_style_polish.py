# -*- coding: utf-8 -*-
"""V45：2D 风格子类型精修（checkpoint / 角色年龄气质 / IP-Adapter 权重 / 背景）单测。

这层是纯数据/纯函数逻辑，不触 GPU：checkpoint 与 IP 参数是否齐全、角色年龄气质
是否按合同分层、关键帧提示词是否同时带环境句与角色约束。
"""
from __future__ import annotations

import json
import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from pipeline import templates3d_hq as hq  # noqa: E402

STYLES = ("jp_anime", "guoman", "retro_cel", "soft_watercolor")


def test_styles_have_own_checkpoint_and_sampling():
    """每个风格都要有自己的底模与采样参数（不能只换提示词）。"""
    ckpts = {}
    for style_id in STYLES:
        style = hq.STYLE_2D_STYLES[style_id]
        assert style["ckpt"].endswith(".safetensors"), style
        assert 0.0 < float(style["cfg"]) < 12.0
        # turbo 底模只要 8~16 步，普通 SDXL 24+；这里只保证不是空配置
        assert int(style["steps"]) >= 8
        assert style["sampler"]
        assert style["prompt"] and style["background"]
        ckpts[style_id] = style["ckpt"]
    # V45.1：4 种风格 4 个不同底模（animagine / AAM / Illustrious / DreamShaper）
    assert len(set(ckpts.values())) == 4, ckpts


def test_ip_adapter_weight_lowered_and_early_end():
    """IP 权重下调 + 提前 end_at + style transfer（V44 的 0.75/linear 只稳脸、背景糊）。"""
    for style_id in STYLES:
        style = hq.STYLE_2D_STYLES[style_id]
        assert float(style["ip_strength"]) <= 0.55, style
        assert float(style["ip_end"]) <= 0.65, style
        assert style["ip_weight_type"] == "style transfer"


def test_laozhou_is_mature_42():
    """老周 42 岁：关键帧/口型提示词都要按成年男性写，并禁掉少年词。"""
    look = hq._character_look("laozhou")
    assert look["age"] == 42
    assert "42-year-old man" in look["identity"]
    assert "mature" in look["identity"]
    assert "teen" in look["negatives"]
    assert "42-year-old man" in look["motion"]


def test_young_character_has_no_middle_aged_clause():
    look = hq._character_look("tianmei")
    assert look["age"] == 20
    assert "young adult" in look["identity"]
    assert "middle-aged" not in look["identity"]
    assert "middle-aged" in look["negatives"]


def test_robot_is_not_human_aged():
    look = hq._character_look("robot")
    assert look["robot"] is True
    assert "robot" in look["identity"].lower()
    assert "human skin" in look["negatives"]


def test_keyframe_prompt_has_background_and_identity():
    style = hq.STYLE_2D_STYLES["jp_anime"]
    look = hq._character_look("laozhou")
    prompt = hq._keyframe_prompt(style, "a neon studio with a city window", look)
    assert style["background"] in prompt
    assert look["identity"] in prompt
    assert "neon studio" in prompt
    assert "close-up" in hq.KEYFRAME_NEGATIVE
    assert "energy aura" in hq.KEYFRAME_NEGATIVE


def test_broll_prompt_has_background_detail():
    prompt = hq.apply_2d_style("[Shot 1] a neon-lit studio. Camera: slow dolly move.",
                               broll=True, style_id="guoman")
    assert hq.STYLE_2D_STYLES["guoman"]["prompt"] in prompt
    assert "detailed background" in prompt
    assert "no 3D" in prompt

def test_keyframe_dir_encodes_style_ckpt_and_ip():
    """V45：关键帧目录按 风格_底模_IP 分层，poster 与生成共用同一路径（换权重不会串图）。"""
    style = hq.STYLE_2D_STYLES["guoman"]
    d = hq._keyframe_dir("tpl", "guoman", style, 0.48)
    assert d.name == "guoman_aamxlanimemix_ip48", d
    d2 = hq._keyframe_dir("tpl", "retro_cel", hq.STYLE_2D_STYLES["retro_cel"], None)
    assert d2.name.startswith("retro_cel_illustriousxlv_ip"), d2


def test_outfit_is_pinned_from_contract():
    """V45：关键帧/口型提示词要带合同里的 outfit（老周=西装），并禁掉吊带等替换服装。"""
    look = hq._character_look("laozhou")
    assert "business suit" in look["identity"]
    assert "same suit" in look["identity"]
    assert "suspenders" in look["negatives"]
    assert "business suit" in look["motion"]


def test_retro_has_age_bias_in_prompt():
    """V45：Illustrious 容易画年轻，retro_cel 的关键帧 prompt 额外带年龄偏置。"""
    style = hq.STYLE_2D_STYLES["retro_cel"]
    assert style.get("age_extra")
    prompt = hq._keyframe_prompt(style, "a studio", hq._character_look("laozhou"))
    assert "45 years old" in prompt
    assert "weathered skin" in prompt


def test_resolve_pick_priority(tmp_path, monkeypatch):
    """V45.2：选卡优先级 CLI > picks 文件 > 自动分。"""
    cands = [{"score": 1.0}, {"score": 5.0}, {"score": 3.0}]
    monkeypatch.setattr(hq, "KEYFRAME_PICKS", tmp_path / "none.json")
    assert hq._resolve_pick("t", "s", cands, 2) == (2, "cli")
    assert hq._resolve_pick("t", "s", cands, None) == (1, "auto")
    picks = tmp_path / "picks.json"
    picks.write_text(json.dumps({"t": {"s": 0}}), encoding="utf-8")
    monkeypatch.setattr(hq, "KEYFRAME_PICKS", picks)
    assert hq._resolve_pick("t", "s", cands, None) == (0, "picks")
    assert hq._resolve_pick("t", "s", cands, 1) == (1, "cli")


def test_score_candidate_returns_metrics(tmp_path):
    """V45.2：候选打分要给出 score/detail/subjects/subject_area（失败也要有默认值）。"""
    from PIL import Image, ImageDraw
    png = tmp_path / "cand.png"
    im = Image.new("RGB", (320, 180), (30, 40, 60))
    draw = ImageDraw.Draw(im)
    draw.ellipse((120, 40, 200, 130), fill=(240, 190, 150))
    im.save(png)
    score = hq._score_candidate(png)
    for key in ("score", "detail", "subjects", "subject_area"):
        assert key in score
    assert isinstance(score["score"], float)
    assert hq._score_candidate(tmp_path / "missing.png")["score"] == 0.0


def test_arcane_cinematic_uses_lora():
    """V46：赛博电影感 = DreamShaper + arcane LoRA，关键帧要透传 lora 参数。"""
    style = hq.STYLE_2D_STYLES["arcane_cinematic"]
    assert style["ckpt"].startswith("DreamShaperXL")
    assert style["lora"] == "arcane-style-xl.safetensors"
    assert 0.5 <= float(style["lora_strength"]) <= 1.0
    prompt = hq._keyframe_prompt(style, "a studio", hq._character_look("laozhou"))
    assert "arcane style" in prompt


def test_cyber_scene_templates_and_scenes():
    """V46：两个赛博场景模板的 act.scene 必须在 contract/v5/scenes 里存在，且 brief 镜数对齐。"""
    import json as _json
    repo = pathlib.Path(__file__).resolve().parents[1]
    scenes = {p.stem for p in (repo / "contract/v5/scenes").glob("*.json")}
    for tid in ("custom_tpl_v46_undercity", "custom_tpl_v46_council"):
        tpl = _json.loads((repo / f"contract/v5/custom/templates/{tid}.json").read_text(encoding="utf-8"))
        assert tpl["render_style"] == "arcane_cinematic"
        for act in tpl["acts"]:
            assert act["scene"] in scenes, (tid, act["scene"])
        brief = _json.loads((repo / f"contract/v5/cinema3d/templates3d/{tid}_semantic.json").read_text(encoding="utf-8"))
        assert len(brief["shots"]) == len(tpl["acts"])
        for shot, act in zip(brief["shots"], tpl["acts"]):
            assert shot["scene"] == act["scene"]


def test_cyber_templates_default_two_candidates():
    """V46：两个赛博场景模板默认 2 张抽卡，创建 API 也会把 1 视为不落盘。"""
    import json as _json
    repo = pathlib.Path(__file__).resolve().parents[1]
    for tid in ("custom_tpl_v46_undercity", "custom_tpl_v46_council"):
        tpl = _json.loads((repo / f"contract/v5/custom/templates/{tid}.json").read_text(encoding="utf-8"))
        assert int(tpl.get("keyframe_candidates") or 1) == 2
