# -*- coding: utf-8 -*-
"""Template v3 校验器单测（P0 验收）。

覆盖 docs/V3_INTERFACES.md §7 模板字段约束与 §9 验收口径：
- 必填字段 / style / aspect / target_duration；
- stage（type / plate 资产 / layout / props / camera_zones）；
- cast（角色合法、block 必须在 layout、站位唯一、道具引用）；
- action_library（>=6、id 唯一、h3_prompt、prop、被 shot 引用 / optional 豁免）；
- segments / shots（>=2 段、beats+shots 非空、shot 必填与枚举、camera_zone、props）；
- timeline（误差 >15% 报错、5%~15% 警告）；
- camera_language / packaging / audio / h3（workflow / duration / fps / resolution）；
- validate_file / validate_dir：id 与文件名一致、expect 数量、差异性签名重复、plate 复用警告；
- 便捷读取（load / load_file / shots / cast_for / actions_by_id / props_by_id /
  stage_layout / template_signature / plate_path / load_all）；
- CLI（python -m pipeline.template_v3）退出码与最后一行 JSON。

全部用 tmp_path 造 fixture，不依赖真实 16 模板（真实目录只在恰好 16 个时做一次附加冒烟）。
"""
from __future__ import annotations

import copy
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from pipeline import template_v3 as tv  # noqa: E402

# 模块导入时的真实仓库路径（fixture 会用 monkeypatch 换成 tmp 资产，这里先留底）
REAL_TEMPLATE_DIR = REPO_ROOT / "contract" / "templates_v3"
REAL_PLATE_DIRS = tv.PLATE_DIRS
REAL_PRESETS_PATH = tv.STYLE_PRESETS_PATH
REAL_CHARACTERS_DIR = tv.CHARACTERS_DIR


# ---------------------------------------------------------------------------
# fixture：所有模板/plate/preset/角色资产都落在 tmp_path，测试互不污染
# ---------------------------------------------------------------------------
@pytest.fixture(autouse=True)
def v3_env(tmp_path, monkeypatch):
    plates = tmp_path / "scene_plates"
    plates.mkdir()
    (plates / "test_stage.png").write_bytes(b"\x89PNG\r\n\x1a\n")

    characters = tmp_path / "characters"
    characters.mkdir()
    for cid, name in (("laozhou", "老周"), ("ali", "阿丽"), ("dawei", "大伟")):
        (characters / f"{cid}.json").write_text(
            json.dumps({"id": cid, "name": name, "voice": f"{cid}_voice"},
                       ensure_ascii=False),
            encoding="utf-8")

    presets = tmp_path / "style_presets.json"
    presets.write_text(json.dumps({"presets": {
        "cinematic_3d": {"character_style": "cutout_3d",
                         "prompt_prefix": "cinematic", "palette": "warm"},
        "history_ink_2d": {"character_style": "cutout_2d",
                           "prompt_prefix": "ink wash", "palette": "ink"},
    }}, ensure_ascii=False), encoding="utf-8")

    templates = tmp_path / "templates_v3"
    templates.mkdir()

    monkeypatch.setattr(tv, "PLATE_DIRS", (plates,))
    monkeypatch.setattr(tv, "STYLE_PRESETS_PATH", presets)
    monkeypatch.setattr(tv, "CHARACTERS_DIR", characters)
    monkeypatch.setattr(tv, "DEFAULT_TEMPLATE_DIR", templates)
    return SimpleNamespace(tmp=tmp_path, plates=plates, characters=characters,
                           presets=presets, templates=templates)


# ---------------------------------------------------------------------------
# fixture 构造工具
# ---------------------------------------------------------------------------
def valid_template(template_id: str = "demo_show") -> dict:
    """最小但完整的合法模板（validator 全字段走一遍，warnings 为空）。"""
    return {
        "id": template_id,
        "label": "测试模板",
        "style": "cinematic_3d",
        "aspect": "16:9",
        "target_duration": 30,
        "stage": {
            "type": "3d_set",
            "plate": "test_stage",
            "scene_desc": "A cozy late-night studio with a desk and a sofa.",
            "layout": {
                "desk": [0.30, 0.62],
                "sofa": [0.70, 0.60],
                "ground_y": 0.86,
            },
            "props": [
                {"id": "mug", "zone": "desk", "interaction": "sip"},
                {"id": "script", "zone": "sofa", "interaction": "handoff"},
            ],
            "camera_zones": ["front", "desk_swap"],
        },
        "cast": [
            {"role": "host", "character": "laozhou", "block": "desk",
             "props": ["mug"], "wardrobe": "navy_suit"},
            {"role": "guest_a", "character": "ali", "block": "sofa",
             "props": ["script"], "wardrobe": "purple_hoodie"},
        ],
        "action_library": [
            {"id": "walk_in", "h3_prompt": "Subject walks in and sits at the desk",
             "prop": None},
            {"id": "pick_mug", "h3_prompt": "Subject lifts the mug and takes a sip",
             "prop": "mug"},
            {"id": "handoff_script",
             "h3_prompt": "Subject hands the script to the guest beside them",
             "prop": "script"},
            {"id": "lean_in", "h3_prompt": "Subject leans toward the guest",
             "prop": None},
            {"id": "react_laugh", "h3_prompt": "Subject laughs and reacts",
             "prop": None},
            {"id": "turn_camera", "h3_prompt": "Subject turns to the camera",
             "prop": None},
        ],
        "segments": [
            {
                "id": "cold_open",
                "name": "开场",
                "duration": 15,
                "beats": ["host 走入舞台", "host 在 desk 坐下"],
                "shots": [
                    {"intent": "establishing", "size": "Wide",
                     "angle": "eye-level", "movement": "truck left",
                     "action": "walk_in", "props": [], "h3_mode": "fl2v",
                     "camera_zone": "front"},
                    {"intent": "host_dialogue", "size": "Medium",
                     "angle": "eye-level", "movement": "slow dolly-in",
                     "action": "pick_mug", "props": ["mug"],
                     "h3_mode": "audio_ref", "camera_zone": "desk_swap"},
                ],
            },
            {
                "id": "interview",
                "name": "访谈",
                "duration": 15,
                "beats": ["host 与 guest 对谈", "反应镜头"],
                "shots": [
                    {"intent": "dialogue", "size": "Medium",
                     "angle": "eye-level", "movement": "static",
                     "action": "lean_in", "props": [],
                     "h3_mode": "audio_ref", "camera_zone": "front"},
                    {"intent": "reaction", "size": "Close-up",
                     "angle": "low-angle", "movement": "static",
                     "action": "react_laugh", "props": [],
                     "h3_mode": "fl2v", "camera_zone": "desk_swap"},
                    {"intent": "outro", "size": "Wide",
                     "angle": "high-angle", "movement": "static",
                     "action": "turn_camera", "props": [],
                     "h3_mode": "audio_ref", "camera_zone": "front"},
                    {"intent": "handoff", "size": "Medium",
                     "angle": "eye-level", "movement": "pan",
                     "action": "handoff_script", "props": ["script"],
                     "h3_mode": "audio_ref", "camera_zone": "front"},
                ],
            },
        ],
        "camera_language": {
            "default": "slow dolly-in",
            "wide": "truck left",
            "reaction": "static",
            "close": "slow push-in",
        },
        "packaging": {
            "lower_third": "zaopianju_yellow",
            "transition": "fade",
            "bgm": "talk_light",
            "subtitle_style": "variety",
        },
        "audio": {"ambience": "studio_room_tone", "sfx": ["mug_clink", "laugh"]},
        "h3": {
            "workflow": "05_ref2va_4step_sla",
            "duration": [4.0, 8.0],
            "fps": 25,
            "resolution": "1024x576",
        },
    }


def make_plate(env, name: str) -> None:
    (env.plates / f"{name}.png").write_bytes(b"\x89PNG\r\n\x1a\n")


def write_template(env, data: dict, filename: str | None = None) -> Path:
    path = env.templates / f"{filename or data['id']}.json"
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def unique_template(env, index: int) -> dict:
    """生成签名互不相同、且能通过严格校验的模板（用于 16/16 目录测试）。"""
    t = valid_template(f"tpl_{index:02d}")
    plate = f"plate_{index:02d}"
    make_plate(env, plate)
    t["stage"]["plate"] = plate
    return t


def all_errors(result: dict) -> str:
    return "\n".join(result["errors"])


def all_warnings(result: dict) -> str:
    return "\n".join(result["warnings"])


def assert_error(result: dict, needle: str) -> None:
    assert result["ok"] is False, f"应校验失败，但 ok=True（errors={result['errors']}）"
    assert needle in all_errors(result), f"错误信息缺少 {needle!r}：\n{all_errors(result)}"


def assert_ok(result: dict) -> None:
    assert result["ok"] is True, f"应校验通过：\n{all_errors(result)}"
    assert result["errors"] == [], all_errors(result)


# ===========================================================================
# A. 合法模板 / 加载 / 便捷读取
# ===========================================================================
def test_valid_template_passes_cleanly():
    res = tv.validate(valid_template())
    assert_ok(res)
    assert res["warnings"] == [], f"合法模板不应有警告：{res['warnings']}"
    stats = res["stats"]
    assert stats["style"] == "cinematic_3d"
    assert stats["stage_type"] == "3d_set"
    assert stats["plate"] == "test_stage"
    assert stats["cast"] == 2
    assert stats["actions"] == 6
    assert stats["segments"] == 2
    assert stats["shots"] == 6
    assert stats["total_duration"] == 30.0
    assert res["id"] == "demo_show"


def test_load_load_file_and_suffix_resolution(v3_env):
    data = valid_template()
    path = write_template(v3_env, data)
    assert tv.load("demo_show", template_dir=v3_env.templates)["id"] == "demo_show"
    assert tv.load_file(path)["id"] == "demo_show"
    # 兼容旧的 xxx_5min / xxx_demo 调用
    assert tv.load("demo_show_5min", template_dir=v3_env.templates)["id"] == "demo_show"
    assert tv.resolve_template_path("demo_show_demo",
                                    v3_env.templates).name == "demo_show.json"
    with pytest.raises(tv.TemplateError):
        tv.load("no_such_template", template_dir=v3_env.templates)
    bad = v3_env.templates / "bad.json"
    bad.write_text("{ not json", encoding="utf-8")
    with pytest.raises(tv.TemplateError):
        tv.load_file(bad)


def test_load_all(v3_env):
    write_template(v3_env, valid_template("show_a"))
    write_template(v3_env, valid_template("show_b"))
    loaded = tv.load_all(v3_env.templates)
    assert set(loaded) == {"show_a", "show_b"}
    assert loaded["show_a"]["id"] == "show_a"


def test_plate_path(v3_env):
    t = valid_template()
    assert tv.plate_path(t) == v3_env.plates / "test_stage.png"
    # plate 写全文件名（含 .png）也能命中
    t["stage"]["plate"] = "test_stage.png"
    assert tv.plate_path(t) == v3_env.plates / "test_stage.png"
    t["stage"]["plate"] = "not_exist"
    assert tv.plate_path(t) is None
    assert tv.plate_path({}) is None


def test_shots_flatten_indexes():
    flat = tv.shots(valid_template())
    assert len(flat) == 6
    assert flat[0]["index"] == 0
    assert flat[0]["segment_id"] == "cold_open"
    assert flat[0]["segment_index"] == 0
    assert flat[0]["shot_index"] == 0
    assert flat[1]["shot_index"] == 1
    assert flat[2]["segment_id"] == "interview"
    assert flat[2]["segment_index"] == 1
    assert [s["index"] for s in flat] == list(range(6))
    # 附加索引不改变原始字段
    assert flat[0]["h3_mode"] == "fl2v"


def test_convenience_readers(v3_env):
    t = valid_template()
    cast = tv.cast_for(t)
    assert [c["role"] for c in cast] == ["host", "guest_a"]
    assert cast[0]["name"] == "老周"          # 合并 contract/characters
    assert cast[0]["voice"] == "laozhou_voice"
    assert cast[0]["character_id"] == "laozhou"
    assert cast[0]["block"] == "desk"
    assert set(tv.cast_by_role(t)) == {"host", "guest_a"}
    assert set(tv.actions_by_id(t)) == {a["id"] for a in t["action_library"]}
    assert set(tv.props_by_id(t)) == {"mug", "script"}
    layout = tv.stage_layout(t)
    assert layout["ground_y"] == 0.86 and layout["desk"] == [0.30, 0.62]


def test_template_signature_distinguishes(v3_env):
    a = valid_template("show_a")
    b = valid_template("show_b")
    assert tv.template_signature(a) == tv.template_signature(b)  # id 不参与签名
    make_plate(v3_env, "other_stage")
    b["stage"]["plate"] = "other_stage"
    assert tv.template_signature(a) != tv.template_signature(b)


# ===========================================================================
# B. 必填字段 / 顶层基础字段
# ===========================================================================
@pytest.mark.parametrize("key", list(tv.REQUIRED_TOP))
def test_missing_required_top_field(key):
    t = valid_template()
    del t[key]
    assert_error(tv.validate(t), f"{key}: 缺少必填字段")


def test_empty_template():
    for bad in ({}, None, []):
        res = tv.validate(bad)
        assert_error(res, "模板为空")
        assert res["id"] == ""


def test_label_aspect_target_duration():
    t = valid_template()
    t["label"] = "  "
    assert_error(tv.validate(t), "label: 必填且非空")

    t = valid_template()
    t["aspect"] = "4:3"
    assert_error(tv.validate(t), "aspect: 必须是 16:9 或 9:16")

    t = valid_template()
    t["target_duration"] = 0
    assert_error(tv.validate(t), "target_duration: 必须是正数")

    t = valid_template()
    t["target_duration"] = "300"
    assert_error(tv.validate(t), "target_duration: 必须是正数")


# ===========================================================================
# C. style / stage
# ===========================================================================
def test_style_must_be_known_preset():
    t = valid_template()
    t["style"] = "no_such_preset"
    assert_error(tv.validate(t), "不在 contract/style_presets.json")
    t["style"] = ""
    assert_error(tv.validate(t), "style: 必填")


def test_stage_type_invalid():
    t = valid_template()
    t["stage"]["type"] = "3d_stage"
    assert_error(tv.validate(t), "stage.type")
    t["stage"]["type"] = None
    assert_error(tv.validate(t), "stage.type")


def test_stage_must_be_object():
    t = valid_template()
    t["stage"] = []
    assert_error(tv.validate(t), "stage: 必须是 object")


def test_plate_missing_strict_and_lax(v3_env):
    t = valid_template()
    t["stage"]["plate"] = "missing_plate"
    # 严格模式：资产缺失 = 错误
    assert_error(tv.validate(t), "stage.plate: 资产缺失")
    # 宽松模式（--allow-missing-plate / strict_plates=False）：只警告、仍通过
    res = tv.validate(t, strict_plates=False)
    assert_ok(res)
    assert "stage.plate: 资产暂缺" in all_warnings(res)
    # plate_generate 标记同样放行
    t["stage"]["plate_generate"] = True
    res = tv.validate(t)
    assert_ok(res)
    assert "stage.plate: 资产暂缺" in all_warnings(res)


def test_stage_plate_required():
    t = valid_template()
    t["stage"]["plate"] = " "
    assert_error(tv.validate(t), "stage.plate: 必填")


def test_layout_validation():
    # 站位 zone 少于 2 个
    t = valid_template()
    t["stage"]["layout"] = {"desk": [0.30, 0.62], "ground_y": 0.86}
    assert_error(tv.validate(t), "至少需要 2 个站位 zone")

    # 坐标越界
    t = valid_template()
    t["stage"]["layout"]["desk"] = [1.20, 0.50]
    assert_error(tv.validate(t), "stage.layout.desk")

    # 坐标不是二元组
    t = valid_template()
    t["stage"]["layout"]["desk"] = [0.30, 0.50, 0.70]
    assert_error(tv.validate(t), "stage.layout.desk")

    # ground_y 越界
    t = valid_template()
    t["stage"]["layout"]["ground_y"] = 0.10
    assert_error(tv.validate(t), "ground_y")

    # layout 不是 object
    t = valid_template()
    t["stage"]["layout"] = "desk"
    assert_error(tv.validate(t), "stage.layout: 必须是 object")


def test_stage_props_validation():
    t = valid_template()
    t["stage"]["props"] = []
    assert_error(tv.validate(t), "stage.props: 必须是非空数组")

    t = valid_template()
    t["stage"]["props"].append({"id": "mug", "zone": "desk", "interaction": "sip"})
    assert_error(tv.validate(t), "重复 mug")

    t = valid_template()
    t["stage"]["props"].append({"id": "lamp", "zone": "window", "interaction": "flip"})
    assert_error(tv.validate(t), "不在 layout 站位 zone 中")

    t = valid_template()
    t["stage"]["props"].append({"id": "lamp", "zone": "desk", "interaction": ""})
    assert_error(tv.validate(t), "interaction: 必填")

    t = valid_template()
    t["stage"]["props"].append("lamp")
    assert_error(tv.validate(t), "stage.props[2]: 必须是 object")


def test_stage_camera_zones_validation():
    t = valid_template()
    t["stage"]["camera_zones"] = ["front"]
    assert_error(tv.validate(t), "至少 2 个机位 zone")

    t = valid_template()
    t["stage"]["camera_zones"] = ["front", "front"]
    assert_error(tv.validate(t), "重复 front")

    t = valid_template()
    t["stage"]["camera_zones"] = ["front", 2]
    assert_error(tv.validate(t), "元素必须是非空字符串")


def test_scene_desc_missing_only_warns():
    t = valid_template()
    del t["stage"]["scene_desc"]
    res = tv.validate(t)
    assert_ok(res)
    assert "scene_desc" in all_warnings(res)


# ===========================================================================
# D. cast
# ===========================================================================
def test_cast_min_two():
    t = valid_template()
    t["cast"] = t["cast"][:1]
    assert_error(tv.validate(t), "cast: 至少 2 个角色")


def test_cast_member_must_be_object():
    t = valid_template()
    t["cast"].append("nobody")
    assert_error(tv.validate(t), "cast[2]: 必须是 object")


def test_cast_role_required():
    t = valid_template()
    t["cast"][0]["role"] = " "
    assert_error(tv.validate(t), "cast[0].role: 必填")


def test_cast_character_must_exist():
    t = valid_template()
    t["cast"][1]["character"] = "nobody"
    assert_error(tv.validate(t), "cast[1].character")
    assert_error(tv.validate(t), "不是现有角色")


def test_cast_block_must_be_in_layout(v3_env):
    """§9 验收：cast.block 必须来自本模板 stage.layout 的 zone。"""
    t = valid_template()
    t["cast"][1]["block"] = "nowhere"
    assert_error(tv.validate(t), "cast[1].block")
    assert_error(tv.validate(t), "不在 stage.layout 站位 zone 中")


def test_cast_block_must_be_unique():
    t = valid_template()
    t["cast"][1]["block"] = "desk"
    assert_error(tv.validate(t), "站位重复 desk")


def test_cast_props_must_be_defined():
    t = valid_template()
    t["cast"][1]["props"] = ["laser_gun"]
    assert_error(tv.validate(t), "未定义道具 laser_gun")


def test_cast_host_and_wardrobe_warnings():
    t = valid_template()
    t["cast"][0]["role"] = "anchor"
    t["cast"][0]["character"] = "dawei"
    del t["cast"][1]["wardrobe"]
    res = tv.validate(t)
    assert_ok(res)
    assert "建议至少一个 host" in all_warnings(res)
    assert "wardrobe" in all_warnings(res)


# ===========================================================================
# E. action_library
# ===========================================================================
def test_action_library_min_six():
    t = valid_template()
    t["action_library"] = t["action_library"][:5]
    assert_error(tv.validate(t), "action_library: 至少 6 个动作")


def test_action_library_member_must_be_object():
    t = valid_template()
    t["action_library"].append("wave")
    assert_error(tv.validate(t), "action_library[6]: 必须是 object")


def test_action_id_required_and_unique():
    t = valid_template()
    t["action_library"][0]["id"] = ""
    assert_error(tv.validate(t), "action_library[0].id: 必填")

    t = valid_template()
    t["action_library"].append({"id": "walk_in", "h3_prompt": "dup"})
    assert_error(tv.validate(t), "重复 walk_in")


def test_action_h3_prompt_required():
    t = valid_template()
    t["action_library"][0]["h3_prompt"] = " "
    assert_error(tv.validate(t), "h3_prompt: 必填")


def test_action_prop_must_be_defined():
    t = valid_template()
    t["action_library"][0]["prop"] = "laser_gun"
    assert_error(tv.validate(t), "未定义道具 laser_gun")


def test_action_must_be_referenced_by_shot():
    """§9 验收：默认每个 action 至少被一个 shot 引用。"""
    t = valid_template()
    t["action_library"].append({"id": "unused_move", "h3_prompt": "waves slowly"})
    res = tv.validate(t)
    assert_error(res, "未被任何 shot 引用")
    assert "unused_move" in all_errors(res)


def test_optional_action_unreferenced_only_warns():
    t = valid_template()
    t["action_library"].append(
        {"id": "rare_move", "h3_prompt": "rarely used", "optional": True})
    res = tv.validate(t)
    assert_ok(res)
    assert "rare_move" in all_warnings(res)
    assert "optional" in all_warnings(res)


def test_shot_action_must_exist_in_library():
    t = valid_template()
    t["segments"][0]["shots"][0]["action"] = "not_in_library"
    assert_error(tv.validate(t), "not_in_library")
    assert_error(tv.validate(t), "不在 action_library 中")


# ===========================================================================
# F. segments / shots / timeline
# ===========================================================================
def test_segments_min_two():
    t = valid_template()
    t["segments"] = t["segments"][:1]
    assert_error(tv.validate(t), "segments: 至少 2 个段落")


def test_segment_fields():
    t = valid_template()
    t["segments"][0]["id"] = ""
    assert_error(tv.validate(t), "segments[0].id: 必填")

    t = valid_template()
    t["segments"][1]["id"] = t["segments"][0]["id"]
    assert_error(tv.validate(t), "重复 cold_open")

    t = valid_template()
    t["segments"][0]["name"] = ""
    assert_error(tv.validate(t), "segments[0].name: 必填")

    t = valid_template()
    t["segments"][0]["duration"] = -1
    assert_error(tv.validate(t), "segments[0].duration: 必须是正数")

    t = valid_template()
    t["segments"][0]["beats"] = []
    assert_error(tv.validate(t), "beats: 必须是非空数组")

    t = valid_template()
    t["segments"][0]["beats"] = [""]
    assert_error(tv.validate(t), "节拍必须是非空字符串")

    t = valid_template()
    t["segments"][0]["shots"] = []
    assert_error(tv.validate(t), "shots: 必须是非空数组")


@pytest.mark.parametrize("key", list(tv.REQUIRED_SHOT))
def test_shot_required_fields(key):
    t = valid_template()
    del t["segments"][0]["shots"][0][key]
    assert_error(tv.validate(t), f"缺少必填字段 {key}")


def test_shot_enums_and_values():
    t = valid_template()
    t["segments"][0]["shots"][0]["size"] = "Super Wide"
    assert_error(tv.validate(t), ".size:")

    # H3 只有 audio_ref / fl2v 两种模式（i2v/其它值都是模板错误）
    t = valid_template()
    t["segments"][0]["shots"][0]["h3_mode"] = "i2v"
    assert_error(tv.validate(t), ".h3_mode:")

    t = valid_template()
    t["segments"][0]["shots"][0]["camera_zone"] = "bogus"
    assert_error(tv.validate(t), "不在 stage.camera_zones 中")

    # camera_zone 必填且非空（H3 首帧取景依赖，空串不能蒙混过关）
    t = valid_template()
    t["segments"][0]["shots"][0]["camera_zone"] = ""
    assert_error(tv.validate(t), "camera_zone: 必填且非空")

    t = valid_template()
    t["segments"][0]["shots"][0]["angle"] = " "
    assert_error(tv.validate(t), ".angle: 必填")

    t = valid_template()
    t["segments"][0]["shots"][0]["movement"] = ""
    assert_error(tv.validate(t), ".movement: 必填")

    t = valid_template()
    t["segments"][0]["shots"][0]["props"] = ["laser_gun"]
    assert_error(tv.validate(t), "未定义道具 laser_gun")

    t = valid_template()
    t["segments"][0]["shots"][0]["props"] = "mug"
    assert_error(tv.validate(t), "必须是数组")

    t = valid_template()
    t["segments"][0]["shots"].append("medium shot")
    assert_error(tv.validate(t), "shots[2]: 必须是 object")


def test_timeline_over_15_percent_errors():
    """§9 验收：segments duration 合计与 target_duration 误差 >15% 报错。"""
    t = valid_template()
    t["target_duration"] = 40  # 30s vs 40s = 25%
    res = tv.validate(t)
    assert_error(res, "timeline:")
    assert "15%" in all_errors(res)


def test_timeline_between_5_and_15_percent_warns():
    t = valid_template()
    t["target_duration"] = 32  # 30s vs 32s = 6.25%
    res = tv.validate(t)
    assert_ok(res)
    assert "timeline:" in all_warnings(res)


# ===========================================================================
# G. camera_language / packaging / audio
# ===========================================================================
def test_camera_language_required_keys():
    for key in ("default", "wide", "reaction", "close"):
        t = valid_template()
        del t["camera_language"][key]
        assert_error(tv.validate(t), f"camera_language.{key}: 必填")
    t = valid_template()
    t["camera_language"] = []
    assert_error(tv.validate(t), "camera_language: 必须是 object")


def test_packaging_validation():
    t = valid_template()
    t["packaging"]["transition"] = "whip_pan"
    assert_error(tv.validate(t), "packaging.transition")

    for key in ("lower_third", "bgm", "subtitle_style"):
        t = valid_template()
        del t["packaging"][key]
        assert_error(tv.validate(t), f"packaging.{key}: 必填")

    t = valid_template()
    t["packaging"] = "fade"
    assert_error(tv.validate(t), "packaging: 必须是 object")


def test_audio_validation():
    t = valid_template()
    del t["audio"]["ambience"]
    assert_error(tv.validate(t), "audio.ambience: 必填")

    t = valid_template()
    t["audio"]["sfx"] = []
    res = tv.validate(t)
    assert_ok(res)
    assert "audio.sfx" in all_warnings(res)

    t = valid_template()
    t["audio"]["sfx"] = "laugh"
    assert_error(tv.validate(t), "audio.sfx: 必须是数组")

    t = valid_template()
    t["audio"] = []
    assert_error(tv.validate(t), "audio: 必须是 object")


# ===========================================================================
# H. h3
# ===========================================================================
def test_h3_workflow_invalid():
    """§9 验收：h3.workflow 只能是 04/05 两个 ComfyUI 工作流。"""
    t = valid_template()
    t["h3"]["workflow"] = "01_t2v_default"
    assert_error(tv.validate(t), "h3.workflow:")
    assert "不在" in all_errors(tv.validate(t))


def test_h3_workflows_both_accepted():
    for wf in sorted(tv.H3_WORKFLOWS):
        t = valid_template()
        t["h3"]["workflow"] = wf
        assert_ok(tv.validate(t))


def test_h3_duration_constraints():
    for bad in ([8, 4], [1, 8], [4, 12], [4], "4-8"):
        t = valid_template()
        t["h3"]["duration"] = bad
        assert_error(tv.validate(t), "h3.duration")


def test_h3_fps_constraints():
    t = valid_template()
    t["h3"]["fps"] = 30
    assert_error(tv.validate(t), "h3.fps: 必须是 24 或 25")
    t["h3"]["fps"] = 24
    assert_ok(tv.validate(t))


def test_h3_resolution_constraints():
    # H3 要求宽高为 32 的倍数
    t = valid_template()
    t["h3"]["resolution"] = "1024x600"
    assert_error(tv.validate(t), "h3.resolution: 宽高")

    t = valid_template()
    t["h3"]["resolution"] = "abc"
    assert_error(tv.validate(t), "必须是 WIDTHxHEIGHT")

    t = valid_template()
    t["h3"]["resolution"] = "1280x704"
    assert_ok(tv.validate(t))


def test_2d_preset_with_3d_set_warns(v3_env):
    t = valid_template()
    t["style"] = "history_ink_2d"
    res = tv.validate(t)
    assert_ok(res)
    assert "2D 风格模板建议用" in all_warnings(res)


# ===========================================================================
# I. validate_file / validate_dir：文件名一致、expect、差异性
# ===========================================================================
def test_validate_file_ok(v3_env):
    path = write_template(v3_env, valid_template())
    res = tv.validate_file(path)
    assert_ok(res)
    assert res["id"] == path.stem


def test_validate_file_id_filename_mismatch(v3_env):
    """§9 验收：模板 id 必须与文件名一致。"""
    data = valid_template("demo_show")
    path = write_template(v3_env, data, filename="other_name")
    res = tv.validate_file(path)
    assert res["ok"] is False
    assert "必须与文件名" in all_errors(res)


def test_validate_file_invalid_json(v3_env):
    bad = v3_env.templates / "bad.json"
    bad.write_text("{oops", encoding="utf-8")
    with pytest.raises(tv.TemplateError):
        tv.validate_file(bad)


def test_validate_dir_all_pass(v3_env):
    for i in range(3):
        write_template(v3_env, unique_template(v3_env, i))
    res = tv.validate_dir(v3_env.templates, expect=3)
    assert res["ok"] is True
    assert res["total"] == 3 and res["passed"] == 3 and res["failed"] == 0
    assert res["expect"] == 3
    assert len(res["results"]) == 3


def test_validate_dir_expect_count_mismatch(v3_env):
    for i in range(3):
        write_template(v3_env, unique_template(v3_env, i))
    res = tv.validate_dir(v3_env.templates, expect=16)
    assert res["ok"] is False
    assert "模板数量 3 != 期望 16" in res["error"]


def test_validate_dir_id_filename_mismatch(v3_env):
    write_template(v3_env, unique_template(v3_env, 0))
    data = unique_template(v3_env, 1)
    write_template(v3_env, data, filename="wrong_stem")
    res = tv.validate_dir(v3_env.templates)
    assert res["ok"] is False
    assert "必须与文件名" in all_errors(
        next(r for r in res["results"] if r["id"] == "tpl_01"))


def test_validate_dir_duplicate_signature_errors(v3_env):
    """§9 验收：16 模板差异性签名（plate+cast站位+action集合）重复必须报错。"""
    t1 = valid_template("show_a")
    t2 = copy.deepcopy(t1)
    t2["id"] = "show_b"
    assert tv.template_signature(t1) == tv.template_signature(t2)
    write_template(v3_env, t1)
    write_template(v3_env, t2)
    res = tv.validate_dir(v3_env.templates)
    assert res["ok"] is False
    results = {r["id"]: r for r in res["results"]}
    assert results["show_a"]["ok"] is True
    assert results["show_b"]["ok"] is False
    assert "差异性:" in all_errors(results["show_b"])


def test_validate_dir_similar_action_set_warns(v3_env):
    """跨模板动作集合 Jaccard>80% 只警告（16 模板不应只是换皮）。"""
    t1 = valid_template("show_a")
    t2 = unique_template(v3_env, 0)
    t2["id"] = "show_b"
    write_template(v3_env, t1)
    write_template(v3_env, t2)
    res = tv.validate_dir(v3_env.templates)
    assert res["ok"] is True
    assert any("相似度" in w for r in res["results"] for w in r["warnings"])


def test_validate_dir_reused_plate_warns(v3_env):
    t1 = valid_template("show_a")
    t2 = copy.deepcopy(t1)
    t2["id"] = "show_b"
    t2["stage"]["layout"]["guest_b"] = [0.80, 0.60]
    t2["cast"][1]["block"] = "guest_b"  # 站位不同 => 签名不同、允许复用 plate
    write_template(v3_env, t1)
    write_template(v3_env, t2)
    res = tv.validate_dir(v3_env.templates)
    assert res["ok"] is True
    assert "复用" in all_warnings(res["results"][1])


def test_validate_dir_invalid_json(v3_env):
    write_template(v3_env, unique_template(v3_env, 0))
    (v3_env.templates / "broken.json").write_text("{oops", encoding="utf-8")
    res = tv.validate_dir(v3_env.templates)
    assert res["ok"] is False
    broken = next(r for r in res["results"] if r["id"] == "broken")
    assert "JSON 非法" in all_errors(broken)


def test_validate_dir_strict_plates_flag(v3_env):
    for i in range(2):
        t = valid_template(f"show_{i}")
        t["stage"]["plate"] = f"absent_{i}"
        write_template(v3_env, t)
    strict = tv.validate_dir(v3_env.templates, strict_plates=True)
    assert strict["ok"] is False
    lax = tv.validate_dir(v3_env.templates, strict_plates=False)
    assert lax["ok"] is True
    assert "资产暂缺" in all_warnings(lax["results"][0])


def test_validate_dir_16_templates_passes(v3_env):
    """§9 验收：16/16 模板校验通过（目录级 expect=16）。"""
    for i in range(16):
        write_template(v3_env, unique_template(v3_env, i))
    res = tv.validate_dir(v3_env.templates, expect=16)
    assert res["ok"] is True
    assert res["total"] == 16 and res["passed"] == 16 and res["failed"] == 0


def test_validate_dir_16_signatures_unique(v3_env):
    """独立复核：16 模板差异性签名两两不同（不依赖 validate_dir 的内部实现）。"""
    templates = [unique_template(v3_env, i) for i in range(16)]
    signatures = {tv.template_signature(t) for t in templates}
    assert len(signatures) == 16


# ===========================================================================
# J. CLI
# ===========================================================================
def _last_json_line(text: str) -> dict:
    lines = [ln for ln in text.splitlines() if ln.strip()]
    return json.loads(lines[-1])


def test_cli_validate_ok(v3_env, capsys):
    for i in range(2):
        write_template(v3_env, unique_template(v3_env, i))
    rc = tv.main(["--validate", str(v3_env.templates), "--expect", "2"])
    out = capsys.readouterr().out
    assert rc == 0
    summary = _last_json_line(out)
    assert summary["ok"] is True and summary["total"] == 2


def test_cli_validate_fail(v3_env, capsys):
    t = valid_template()
    del t["h3"]
    write_template(v3_env, t)
    rc = tv.main(["--validate", str(v3_env.templates)])
    out = capsys.readouterr().out
    assert rc == 1
    assert "FAIL" in out


def test_cli_expect_mismatch_fail(v3_env, capsys):
    for i in range(2):
        write_template(v3_env, unique_template(v3_env, i))
    rc = tv.main(["--validate", str(v3_env.templates), "--expect", "16"])
    out = capsys.readouterr().out
    assert rc == 1
    assert "模板数量 2 != 期望 16" in out


def test_cli_allow_missing_plate(v3_env, capsys):
    t = valid_template("show_a")
    t["stage"]["plate"] = "absent_plate"
    write_template(v3_env, t)
    assert tv.main(["--validate", str(v3_env.templates)]) == 1
    capsys.readouterr()
    assert tv.main(["--validate", str(v3_env.templates),
                    "--allow-missing-plate"]) == 0


def test_cli_validate_file(v3_env, capsys):
    path = write_template(v3_env, valid_template())
    assert tv.main(["--validate-file", str(path)]) == 0
    bad_path = write_template(v3_env, copy.deepcopy(valid_template()),
                              filename="renamed")
    capsys.readouterr()
    assert tv.main(["--validate-file", str(bad_path)]) == 1


def test_cli_list(v3_env, capsys):
    write_template(v3_env, valid_template("show_a"))
    write_template(v3_env, valid_template("show_b"))
    rc = tv.main(["--list"])
    out = capsys.readouterr().out
    assert rc == 0
    summary = _last_json_line(out)
    assert summary["total"] == 2
    assert summary["ids"] == ["show_a", "show_b"]


# ===========================================================================
# K. 真实模板目录冒烟（不阻断 P0 单测：未满 16 个时 skip）
# ===========================================================================
def test_real_templates_v3_validate_when_complete(monkeypatch):
    """真实 contract/templates_v3 满 16 个时，必须 16/16 通过（P0 验收冒烟）。

    P0 单测本身不依赖真实模板；此用例在模板未齐时 skip，齐了以后自动生效。
    """
    if not REAL_TEMPLATE_DIR.is_dir():
        pytest.skip("contract/templates_v3 不存在")
    count = len(sorted(REAL_TEMPLATE_DIR.glob("*.json")))
    if count != 16:
        pytest.skip(f"真实模板 {count}/16，待 template-author 补齐后自动生效")

    # 覆盖 autouse fixture 的 tmp 资产，改用仓库真实 presets/角色/plate 目录
    monkeypatch.setattr(tv, "PLATE_DIRS", REAL_PLATE_DIRS)
    monkeypatch.setattr(tv, "STYLE_PRESETS_PATH", REAL_PRESETS_PATH)
    monkeypatch.setattr(tv, "CHARACTERS_DIR", REAL_CHARACTERS_DIR)
    res = tv.validate_dir(REAL_TEMPLATE_DIR, expect=16, strict_plates=False)
    fails = {r["id"]: r["errors"] for r in res["results"] if not r["ok"]}
    assert res["ok"] is True, f"真实模板未全通过: {fails}"
