# -*- coding: utf-8 -*-
"""v3 stage 首帧合成 + action_library 测试（纯 CPU，不依赖 GPU/ComfyUI）。

用真实模板（contract/templates_v3/tonight_show.json、history_talk.json）与真实资产
（outputs/scene_plates、outputs/characters/cutout_*）验证接口契约：
- 首帧 1024x576 RGB、meta 字段齐全（camera_zone/crop/clean/h3_mode/characters/bbox/foot_y）；
- 3D 模板 audio_ref：按 preset 用 cutout_3d，多角色全部入场、脚底落在同一地平线；
- fl2v：clean 合成（cutout_2d 干净线稿），无说话人空镜只取景 plate 不叠角色；
- 2D 水墨模板 history_talk 一律 cutout_2d；
- fl2v 尾帧与首帧不同、static 返回 None；卡片帧烘焙中文标题并标记 static_card；
- camera_zone 关键词规则（44 个新 zone 名不依赖逐个枚举）；
- action_library 四段 prompt（fl2v integrated_multimodal_description、不写台词）。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
from PIL import Image, ImageChops, ImageStat

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from pipeline import action_library, stage, template_v3  # noqa: E402

SIZE = (1024, 576)
TEMPLATES = ("tonight_show", "history_talk")


# ---------------------------------------------------------------------------
# 工具
# ---------------------------------------------------------------------------
def _template(tid: str) -> dict:
    return template_v3.load(tid)


def _raw_shot(template: dict, *, mode: str | None = None, size: str | None = None,
              zone: str | None = None) -> dict:
    """从真实模板里挑一个满足条件的镜头（找不到就退回首镜，由调用方覆盖字段）。"""
    shots = template_v3.shots(template)
    for shot in shots:
        if mode and str(shot.get("h3_mode")) != mode:
            continue
        if size and str(shot.get("size")) != size:
            continue
        if zone and str(shot.get("camera_zone")) != zone:
            continue
        return dict(shot)
    return dict(shots[0])


def _shot(raw: dict, *, role: str | None = None, mode: str | None = None,
          size: str | None = None, zone: str | None = None) -> dict:
    """模板镜头 → storyboard shot 形状（含 v3 原字段 + role/line）。"""
    v3 = dict(raw)
    if mode is not None:
        v3["h3_mode"] = mode
    if size is not None:
        v3["size"] = size
    if zone is not None:
        v3["camera_zone"] = zone
    shot = {"id": str(v3.get("intent") or "shot"), "v3": v3, "line": "这是一句不该进 prompt 的台词"}
    if role is not None:
        shot["role"] = role
        shot["speaker_name"] = ""
    return shot


def _assert_png(path, size=SIZE) -> None:
    path = Path(path)
    assert path.is_file(), path
    assert path.stat().st_size > 1000, path
    with Image.open(path) as im:
        assert im.size == size, (path, im.size)
        assert im.mode == "RGB", (path, im.mode)


# ---------------------------------------------------------------------------
# 资产 / 场景
# ---------------------------------------------------------------------------
def test_load_plate_cutout_describe_scene():
    for tid in TEMPLATES:
        template = _template(tid)
        plate = stage.load_plate(template)
        assert plate.is_file() and plate.suffix.lower() == ".png"
        assert stage.describe_scene(template).strip()
        for member in template_v3.cast_for(template):
            info = stage.cutout_for(template, member["character_id"])
            assert set(info) >= {"path", "style", "fallback", "kind"}
            assert Path(info["path"]).is_file()
            assert info["kind"] == "full_body"
    # 缺失 plate → StageError（接口要求 raise）
    with pytest.raises(stage.StageError):
        stage.load_plate({"id": "x", "stage": {"plate": "__missing_plate__"}})


def test_history_talk_uses_cutout_2d():
    template = _template("history_talk")
    assert template["style"] == "history_ink_2d"
    for member in template_v3.cast_for(template):
        info = stage.cutout_for(template, member["character_id"])
        assert info["style"] == "cutout_2d", info
        assert info["fallback"] is False, info


# ---------------------------------------------------------------------------
# 首帧：audio_ref（多角色、3D cutout）
# ---------------------------------------------------------------------------
def test_compose_first_frame_audio_ref_multi_cast(tmp_path):
    template = _template("tonight_show")
    raw = _raw_shot(template, mode="audio_ref", size="Medium")
    shot = _shot(raw, role="host", mode="audio_ref", size="Medium", zone="front")
    out = tmp_path / "first.png"
    meta = stage.compose_first_frame(template, shot, out)
    _assert_png(out)

    required = {"path", "width", "height", "plate", "camera_zone", "crop", "characters",
                "props", "ground_y", "style", "h3_input", "clean", "h3_mode"}
    assert required <= set(meta), required - set(meta)
    assert meta["width"] == SIZE[0] and meta["height"] == SIZE[1]
    assert meta["camera_zone"] == "front"
    assert meta["clean"] is False and meta["h3_mode"] == "audio_ref"
    assert meta["h3_input"] is True
    # crop 为 plate 归一化 [x,y,w,h]（渲染端要用），且取景窗是 16:9（不拉伸）
    x, y, w, h = meta["crop"]
    assert 0.0 <= x <= 1.0 and 0.0 <= y <= 1.0 and 0.0 < w <= 1.0 and 0.0 < h <= 1.0
    pw, ph = meta["plate_size"]
    assert abs((w * pw) / (h * ph) - SIZE[0] / SIZE[1]) < 0.02, meta["crop"]
    # Medium 景别取景窗宽度 ≤0.80（zone 只管机位，size 约束景别）
    assert 0.55 <= w <= 0.80, meta["crop"]
    # 全部 cast 入场
    cast_roles = {str(c.get("role")) for c in template_v3.cast_for(template)}
    assert {c["role"] for c in meta["characters"]} == cast_roles
    # 3D preset（cinematic_3d）：audio_ref 用 cutout_3d
    assert all(c["cutout_style"] == "cutout_3d" for c in meta["characters"]), meta["characters"]
    for char in meta["characters"]:
        assert Path(char["cutout"]).is_file()
        bx, by, bw, bh = char["bbox"]
        assert bw > 20 and bh > 20
        assert by >= -12  # 头顶基本在画内
        # bbox 底 = 脚底锚点 foot_y
        assert abs((by + bh) - char["foot_y"]) <= 2
        assert 0 <= char["foot_y"] <= SIZE[1]
        assert isinstance(char["cutout_fallback"], bool)
    # 脚底同一地平线（±8% H），且分散在不同 x
    feet = [c["foot_y"] for c in meta["characters"]]
    assert max(feet) - min(feet) <= SIZE[1] * 0.08, feet
    lefts = [c["bbox"][0] for c in meta["characters"]]
    assert len(set(lefts)) >= 2, lefts


def test_compose_first_frame_2d_ink_template(tmp_path):
    template = _template("history_talk")
    raw = _raw_shot(template, mode="audio_ref")
    shot = _shot(raw, role="host", mode="audio_ref", zone="front")
    meta = stage.compose_first_frame(template, shot, tmp_path / "ink.png")
    _assert_png(tmp_path / "ink.png")
    assert meta["character_style"] == "cutout_2d"
    assert meta["characters"], meta["cast_hidden"]
    for char in meta["characters"]:
        assert char["cutout_style"] == "cutout_2d", char
        assert char["cutout_fallback"] is False, char


# ---------------------------------------------------------------------------
# 首帧：fl2v clean 分流
# ---------------------------------------------------------------------------
def test_compose_first_frame_fl2v_clean(tmp_path):
    template = _template("tonight_show")
    # 1) 无说话人空镜（role 为空）→ 只取景 plate，不叠角色
    raw = _raw_shot(template, size="Wide")
    empty_shot = _shot(raw, role="", mode="fl2v", size="Wide", zone="window_wide")
    empty_png = tmp_path / "empty.png"
    empty = stage.compose_first_frame(template, empty_shot, empty_png)
    _assert_png(empty_png)
    assert empty["clean"] is True and empty["h3_mode"] == "fl2v"
    assert empty["characters"] == []
    assert empty["cast_hidden"]  # 记录原因
    # 2) 有说话人 fl2v → 一律 cutout_2d 干净线稿（frame-locked，不许丝带光晕）
    close_shot = _shot(raw, role="host", mode="fl2v", size="Close-up", zone="front")
    close_png = tmp_path / "clean.png"
    clean = stage.compose_first_frame(template, close_shot, close_png)
    _assert_png(close_png)
    assert clean["clean"] is True
    assert clean["characters"], clean["cast_hidden"]
    for char in clean["characters"]:
        assert char["cutout_style"] == "cutout_2d", char
        assert char["cutout_fallback"] is False, char
    # 3) clean 显式覆盖：audio_ref 也能强制干净合成
    raw_audio = _raw_shot(template, mode="audio_ref")
    forced_shot = _shot(raw_audio, role="host", mode="audio_ref", zone="front")
    forced = stage.compose_first_frame(template, forced_shot, tmp_path / "forced.png",
                                      clean=True)
    assert forced["clean"] is True
    assert all(c["cutout_style"] == "cutout_2d" for c in forced["characters"])
    # 两张图内容不同（空镜 vs 单人特写）
    with Image.open(empty_png) as a, Image.open(close_png) as b:
        diff = ImageChops.difference(a.convert("L"), b.convert("L"))
    assert ImageStat.Stat(diff).mean[0] > 1.0


# ---------------------------------------------------------------------------
# 尾帧（fl2v）
# ---------------------------------------------------------------------------
def test_compose_last_frame_motion(tmp_path):
    template = _template("tonight_show")
    raw = _raw_shot(template, mode="fl2v")
    raw["movement"] = "slow push-in"
    shot = _shot(raw, role="host", mode="fl2v")
    first_png = tmp_path / "first.png"
    first_meta = stage.compose_first_frame(template, shot, first_png)
    last_png = tmp_path / "last.png"
    last = stage.compose_last_frame(template, shot, first_meta, last_png)
    assert last is not None, "push-in 应生成尾帧"
    _assert_png(last_png, (first_meta["width"], first_meta["height"]))
    assert set(last) >= {"path", "width", "height", "motion"}
    assert last["motion"]["kind"] == "push_in"
    with Image.open(first_png) as a, Image.open(last_png) as b:
        diff = ImageChops.difference(a.convert("L"), b.convert("L"))
    assert ImageStat.Stat(diff).mean[0] > 0.2, "尾帧应与首帧不同"
    # 横摇
    pan_shot = _shot(raw, role="host", mode="fl2v")
    pan_shot["v3"]["movement"] = "truck left"
    pan = stage.compose_last_frame(template, pan_shot, first_meta, tmp_path / "pan.png")
    assert pan is not None and pan["motion"]["kind"] == "truck"
    # static → None（调用方用首帧兜底）
    static_shot = _shot(raw, role="host", mode="fl2v")
    static_shot["v3"]["movement"] = "static"
    assert stage.compose_last_frame(template, static_shot, first_meta,
                                    tmp_path / "static.png") is None


# ---------------------------------------------------------------------------
# 卡片帧
# ---------------------------------------------------------------------------
def test_compose_card_frame(tmp_path):
    template = _template("tonight_show")
    card = stage.compose_card_frame(
        template, {"type": "title_card", "title": "美式深夜秀", "subtitle": "造片局 · 第 1 期"},
        tmp_path / "card.png")
    _assert_png(tmp_path / "card.png")
    assert card["static_card"] is True and card["h3_input"] is False
    assert card["kind"] == "title_card"
    assert card["title"] == "美式深夜秀"
    end = stage.compose_card_frame(template, {"type": "end_card"},
                                   tmp_path / "end.png")
    _assert_png(tmp_path / "end.png")
    assert end["kind"] == "end_card" and end["title"]


# ---------------------------------------------------------------------------
# camera_zone 关键词规则（16 模板 44 个新 zone 名）
# ---------------------------------------------------------------------------
def test_zone_keyword_rules():
    for name in ("front", "wide", "desk_swap", "sofa_ots", "window_wide", "stage_wide"):
        assert stage.zone_kind(name) == "exact", name
    assert stage.zone_kind("mic_close") == "close"
    assert stage.zone_kind("door_close") == "close"
    assert stage.zone_kind("head_ots") == "close"      # 头肩 → tight
    assert stage.zone_kind("candelabra_close") == "close"
    assert stage.zone_kind("timer_close") == "close"
    assert stage.zone_kind("affirmative_ots") == "ots"
    assert stage.zone_kind("couch_ots") == "ots"
    assert stage.zone_kind("wing_ots") == "ots"
    assert stage.zone_kind("crane_high") == "high"
    assert stage.zone_kind("overhead") == "high"       # 不能被 "head" 误判成 close
    assert stage.zone_kind("overhead_light") == "high"
    assert stage.zone_kind("low_angle") == "low"
    assert stage.zone_kind("vhs_wide") == "wide"
    assert stage.zone_kind("noir_wide") == "wide"
    assert stage.zone_kind("totally_unknown_zone") == "fallback"
    # 未知 zone 回落全画面；已知/规则内 zone 都在 plate 内
    assert stage.zone_view("totally_unknown_zone") == (0.0, 0.0, 1.0, 1.0)
    for zone in ("mic_close", "couch_ots", "crane_high", "low_angle", "vhs_wide",
                 "guest_close", "table_high"):
        x, y, w, h = stage.zone_view(zone, speaker_x=0.32)
        assert 0.0 <= x <= 1.0 and 0.0 <= y <= 1.0
        assert 0.0 < w <= 1.0 and 0.0 < h <= 1.0
    # tight close 明显比 wide 窄
    assert stage.zone_view("mic_close", speaker_x=0.5)[2] < \
        stage.zone_view("vhs_wide")[2] * 0.5


def test_zone_size_caps_and_anchor():
    """size（景别）约束 zone（机位）：Medium ≤0.80，MCU/CU 收窄并锚定说话人。"""
    x, y, w, h = stage.zone_view("front", size="Medium")
    assert 0.55 <= w <= 0.80, w
    x, y, w, h = stage.zone_view("front", size="Medium Close-up", speaker_x=0.63)
    assert 0.34 <= w <= 0.42, w
    assert abs((x + 0.55 * w) - 0.63) < 0.03, (x, w)      # 说话人落在窗内 55% 处
    x, y, w, h = stage.zone_view("sofa_ots", size="Close-up", speaker_x=0.30)
    assert 0.32 <= w <= 0.38, w
    assert abs((x + 0.55 * w) - 0.30) < 0.03, (x, w)
    assert stage.zone_view("front", size="Wide")[2] >= 0.90
    assert stage.zone_view("front", size="Full")[2] <= 0.92


def test_compose_near_size_zooms_in(tmp_path):
    """tonight_show: MCU/CU 镜头取景窗收窄（之前 zone 没和 size 结合，接近全画面）。"""
    template = _template("tonight_show")
    for size, lo, hi in (("Medium Close-up", 0.34, 0.42), ("Close-up", 0.32, 0.38)):
        raw = _raw_shot(template, mode="audio_ref")
        shot = _shot(raw, role="host", mode="audio_ref", size=size, zone="front")
        meta = stage.compose_first_frame(template, shot, tmp_path / f"{size}.png", clean=True)
        w = meta["crop"][2]
        assert lo - 0.006 <= w <= hi + 0.006, (size, meta["crop"])
        assert len(meta["characters"]) == 1  # 近景只留说话人
        char = meta["characters"][0]
        assert char["cutout_style"] == "cutout_2d"
        bh = char["bbox"][3]
        assert bh >= SIZE[1] * 0.5, char["bbox"]   # 近景角色够大
        assert char["bbox"][1] <= SIZE[1] * 0.22, char["bbox"]  # 头顶贴近上沿
        assert 0 <= char["foot_y"] <= SIZE[1]


# ---------------------------------------------------------------------------
# 16 模板集成：每模板至少能合成 1 帧
# ---------------------------------------------------------------------------
def test_all_templates_compose_one_frame(tmp_path):
    templates = template_v3.load_all()
    assert len(templates) >= 16, sorted(templates)
    for tid in sorted(templates):
        template = templates[tid]
        raw = dict(template_v3.shots(template)[0])
        shot = _shot(raw, role="")
        out = tmp_path / f"{tid}.png"
        meta = stage.compose_first_frame(template, shot, out)
        _assert_png(out)
        assert meta["h3_input"] is True
        assert meta["camera_zone"], tid
        assert Path(meta["plate"]).is_file()


# ---------------------------------------------------------------------------
# action_library
# ---------------------------------------------------------------------------
def test_resolve_action_and_camera_phrase():
    template = _template("tonight_show")
    action = action_library.resolve_action(template, "pick_cue_cards")
    assert isinstance(action, dict) and action["h3_prompt"]
    with pytest.raises(KeyError):
        action_library.resolve_action(template, "__not_an_action__")
    assert "static" in action_library.camera_phrase("static")
    assert "dolly-in" in action_library.camera_phrase("slow dolly-in", "Medium")
    assert "truck" in action_library.camera_phrase("truck left")
    assert "pan" in action_library.camera_phrase("slow pan")
    # 未知运镜不崩、仍返回英文短语
    assert action_library.camera_phrase("weird swoop")


def test_subject_definitions_and_scene_sentence():
    template = _template("tonight_show")
    text = action_library.subject_definitions(template)
    assert text.startswith("subject_definitions: ")
    assert "Subject 1 is the host (laozhou)" in text
    assert "Subject 2 is" in text
    assert "face, hairstyle, hair color, outfit and age" in text
    scene = action_library.scene_sentence(template)
    assert scene and scene.isascii(), scene
    ink_scene = action_library.scene_sentence(_template("history_talk"))
    assert ink_scene and ink_scene.isascii(), ink_scene


def test_build_h3_prompt_audio_ref():
    template = _template("tonight_show")
    raw = _raw_shot(template, mode="audio_ref")
    shot = _shot(raw, role="host", mode="audio_ref")
    prompt = action_library.build_h3_prompt(template, shot, mode="audio_ref")
    parts = prompt.split("\n\n")
    assert len(parts) == 4, parts
    assert parts[0].startswith("subject_definitions: ")
    assert parts[1].startswith("summary: ")
    assert "One continuous" in parts[1]
    assert "Camera:" in parts[1]
    assert "speaks in sync with the reference speech audio" in parts[1]
    assert "keep faces" in parts[1].lower()
    assert parts[2].startswith("overall_soundscape: ")
    assert parts[3].strip() == "non_diegetic_music: N/A"
    # 台词只进音频，不进 prompt
    assert "台词" not in prompt and "这是一句" not in prompt
    # 风格前缀注入
    assert "Visual style:" in prompt
    # 动作来自模板 action_library（首词 Subject 换成 Subject N 后，其余描述保留）
    action_text = str(action_library.resolve_action(template, raw["action"])["h3_prompt"])
    tail = " ".join(action_text.split()[1:])[:36].rstrip(".,;")
    assert tail.lower() in prompt.lower(), (tail, prompt)


def test_build_h3_prompt_fl2v():
    template = _template("tonight_show")
    raw = _raw_shot(template, mode="fl2v")
    # 空镜：无 role → integrated_multimodal_description 且明确 empty set
    empty_shot = _shot(raw, role="", mode="fl2v", size="Wide", zone="window_wide")
    empty_prompt = action_library.build_h3_prompt(template, empty_shot, mode="fl2v")
    parts = empty_prompt.split("\n\n")
    assert parts[1].startswith("integrated_multimodal_description: [Shot 1]")
    assert "empty set" in parts[1]
    assert "No people are in frame" in parts[1]
    assert parts[3].strip() == "non_diegetic_music: N/A"
    # 有说话人 → 动作来自动作库，不写台词
    close_shot = _shot(raw, role="host", mode="fl2v", size="Close-up", zone="front")
    close_prompt = action_library.build_h3_prompt(template, close_shot, mode="fl2v")
    assert "integrated_multimodal_description: [Shot 1]" in close_prompt
    assert "empty set" not in close_prompt
    assert "Only the subjects move between the two frames" in close_prompt
    assert "这是一句" not in close_prompt


def test_build_action_variants():
    template = _template("tonight_show")
    variants = action_library.build_action_variants(template)
    ids = [str(a["id"]) for a in template["action_library"]]
    assert variants["count"] == len(ids)
    assert set(variants["ids"]) == set(ids)
    for aid in ids:
        item = variants["by_id"][aid]
        assert item["h3_prompt"]
        assert item["variants"]
    assert variants["with_prop"] and variants["without_prop"]
