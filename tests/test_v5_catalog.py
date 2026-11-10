# -*- coding: utf-8 -*-
"""V5 契约层测试：catalog 加载/深合并/校验/导出/CLI（不依赖 GPU、不写仓库契约）。

覆盖：
- load_catalog：内建 + custom 深合并（dict 递归、数组替换）、sources 溯源、坏文件容错；
- validate_catalog：JSON Schema、引用完整性、custom id 规则、资源/alpha、strict 语义；
- validate_item：API PUT 的 partial 覆盖 / 新对象 / 非法对象；
- export_custom：导出可编辑副本 + 覆盖后重新加载生效；
- schema 文件与 custom 示例有效性；CLI 的 JSON 输出与 exit code。
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from pipeline import v5_catalog  # noqa: E402

REAL_ROOT = REPO_ROOT / "contract" / "v5"
KINDS = v5_catalog.KINDS


# ---------------------------------------------------------------------------
# fixture 构造
# ---------------------------------------------------------------------------
def _style(style_id: str = "s1") -> dict:
    return {
        "id": style_id, "label": "测试风格", "version": 1,
        "technique": ["neon_toon"],
        "palette": {"bg": ["#070b1f", "#0f1838"], "fg": ["#eaf6ff"],
                    "accent": ["#ff2fd6", "#33e6ff"]},
        "texture": ["grid"], "character_treatment": "toon",
        "transition": "fade", "h3": {"style_prompt": "test style prompt"},
    }


def _scene(scene_id: str = "sc1") -> dict:
    return {
        "id": scene_id, "label": "测试场景", "tags": ["test"],
        "aspect": [16, 9], "seed": 1,
        "layers": [{"type": "paper"}, {"type": "grid", "color": "accent:1"}],
        "camera_zones": {"wide": [0.0, 0.0, 1.0, 1.0],
                         "host_close": [0.3, 0.15, 0.4, 0.5]},
        "anchors": {"host": [0.5, 0.9]},
    }


def _character(char_id: str = "ch1") -> dict:
    return {
        "id": char_id, "label": "测试角色", "persona": "测试人设",
        "role_pool": ["host", "narrator"],
        "voice": {"provider": "breeze", "instruction": "30岁男性，平稳", "seed": 1,
                  "cfg_scale": 4.0},
        "appearance": {
            "gender": "male", "age": 30,
            "skin": "#f0d8c0", "hair": "#111111", "hair_style": "short",
            "eye": "#222222", "brow": "straight",
            "outfit": {"type": "suit", "main": "#1f2a3a", "accent": "#ffcc00"},
            "accessories": ["watch"], "head_ratio": 1.0, "chibi": 0.2,
        },
        "poses": ["front", "side"], "mouth": ["closed", "a", "o"],
        "style_overrides": {"s1": {"line": 1.2}},
    }


def _template(template_id: str = "t1") -> dict:
    return {
        "id": template_id, "label": "测试模板", "style": "s1",
        "target_duration": 10, "aspect": [16, 9],
        "cast": [{"role": "host", "character": "ch1"}],
        "acts": [
            {"id": "a1", "duration": 6, "scene": "sc1", "camera": "push_in",
             "camera_zone": "wide", "line": "host", "text": "台词"},
            {"id": "a2", "duration": 4, "scene": "sc1", "camera": "static",
             "line": "", "broll": True},
        ],
        "packaging": {"subtitle": "s1", "transition": "fade", "title": "测试"},
    }


def _prop(prop_id: str = "p1") -> dict:
    return {"id": prop_id, "label": "测试道具", "kind": "banner"}


def _pack(pack_id: str = "pack1") -> dict:
    return {"id": pack_id, "label": "测试包", "styles": ["s1"], "scenes": ["sc1"],
            "characters": ["ch1"], "props": ["p1"], "templates": ["t1"]}


def _write(root: Path, kind: str, item: dict) -> Path:
    path = root / kind / f"{item['id']}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(item, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def _make_root(tmp_path: Path, *, with_optional: bool = True) -> Path:
    root = tmp_path / "contract_v5"
    (root / "schema").mkdir(parents=True, exist_ok=True)
    for name in ("style", "scene", "character", "template", "prop", "pack", "timeline"):
        src = REAL_ROOT / "schema" / f"{name}.schema.json"
        (root / "schema" / f"{name}.schema.json").write_text(
            src.read_text(encoding="utf-8"), encoding="utf-8")
    for kind in KINDS:
        (root / kind).mkdir(parents=True, exist_ok=True)
        (root / "custom" / kind).mkdir(parents=True, exist_ok=True)
    _write(root, "styles", _style())
    _write(root, "scenes", _scene())
    _write(root, "characters", _character())
    _write(root, "templates", _template())
    if with_optional:
        _write(root, "props", _prop())
        _write(root, "packs", _pack())
    return root


# ---------------------------------------------------------------------------
# load_catalog / 深合并
# ---------------------------------------------------------------------------
def test_load_catalog_empty_root(tmp_path):
    root = tmp_path / "empty_v5"
    for kind in KINDS:
        (root / kind).mkdir(parents=True, exist_ok=True)
    catalog = v5_catalog.load_catalog(root)
    for kind in KINDS:
        assert catalog[kind] == {}
    assert catalog["errors"] == []
    assert catalog["counts"] == {kind: 0 for kind in KINDS}
    assert catalog["root"] == str(root)


def test_deep_merge_rules():
    base = {"a": {"x": 1, "y": 2}, "arr": [1, 2], "scalar": "old", "keep": True}
    override = {"a": {"y": 20, "z": 30}, "arr": [9], "scalar": "new"}
    merged = v5_catalog.deep_merge(base, override)
    assert merged["a"] == {"x": 1, "y": 20, "z": 30}   # dict 递归
    assert merged["arr"] == [9]                        # list 替换
    assert merged["scalar"] == "new"
    assert merged["keep"] is True
    # 不修改入参
    assert base["a"] == {"x": 1, "y": 2} and base["arr"] == [1, 2]


def test_load_catalog_custom_partial_override(tmp_path):
    root = _make_root(tmp_path)
    override = {"id": "ch1", "voice": {"instruction": "新的音色"}}
    _write(root, "custom/characters", override)
    catalog = v5_catalog.load_catalog(root)
    char = catalog["characters"]["ch1"]
    assert char["voice"]["instruction"] == "新的音色"      # 覆盖生效
    assert char["voice"]["provider"] == "breeze"            # 深合并保留
    assert char["appearance"]["outfit"]["main"] == "#1f2a3a"  # 其余字段保留
    assert catalog["sources"]["characters"]["ch1"]["custom"] is True
    assert catalog["sources"]["characters"]["ch1"]["base"]
    assert catalog["custom_counts"]["characters"] == 1


def test_load_catalog_custom_array_replace(tmp_path):
    root = _make_root(tmp_path)
    _write(root, "custom/styles", {"id": "s1", "palette": {"accent": ["#00ff00"]}})
    catalog = v5_catalog.load_catalog(root)
    assert catalog["styles"]["s1"]["palette"]["accent"] == ["#00ff00"]
    assert catalog["styles"]["s1"]["palette"]["bg"] == ["#070b1f", "#0f1838"]


def test_load_catalog_custom_new_item(tmp_path):
    root = _make_root(tmp_path)
    new_style = _style("custom_extra")
    _write(root, "custom/styles", new_style)
    catalog = v5_catalog.load_catalog(root)
    assert catalog["styles"]["custom_extra"]["label"] == "测试风格"
    assert catalog["sources"]["styles"]["custom_extra"]["custom"] is True
    assert "base" not in catalog["sources"]["styles"]["custom_extra"]


def test_load_catalog_tolerates_bad_files(tmp_path):
    root = _make_root(tmp_path)
    (root / "characters" / "broken.json").write_text("{not json", encoding="utf-8")
    (root / "characters" / "array_root.json").write_text("[1,2]", encoding="utf-8")
    catalog = v5_catalog.load_catalog(root)
    assert "ch1" in catalog["characters"]          # 好文件仍加载
    assert catalog["errors"]                        # 坏文件记录错误
    result = v5_catalog.validate_catalog(catalog)
    assert not result["ok"]
    assert any("broken.json" in err for err in result["errors"])


# ---------------------------------------------------------------------------
# validate_catalog
# ---------------------------------------------------------------------------
def test_validate_catalog_ok_and_strict(tmp_path, monkeypatch):
    # 隔离 scene_render 的 primitive 注册表状态（契约测试只测契约；primitive 对齐是渲染模块的事，
    # 单独由 test_validate_catalog_primitive_warning_aggregated 覆盖）
    monkeypatch.setattr(v5_catalog, "_scene_primitives", lambda: None)
    root = _make_root(tmp_path)
    result = v5_catalog.validate_catalog(root=root)
    assert result["ok"] is True, result["errors"]
    assert result["errors"] == []
    assert result["stats"]["counts"]["styles"] == 1
    strict = v5_catalog.validate_catalog(root=root, strict=True)
    assert strict["ok"] is True, strict["warnings"]  # fixture 字段齐全、无 warning
    assert strict["warnings"] == []


def test_validate_catalog_primitive_warning_aggregated(tmp_path, monkeypatch):
    """scene_render primitive 未对齐时：聚合成 1 条 warning（不刷屏），--strict 下失败。"""
    root = _make_root(tmp_path)
    monkeypatch.setattr(v5_catalog, "_scene_primitives", lambda: {"paper"})
    result = v5_catalog.validate_catalog(root=root)
    assert result["ok"] is True  # warning 不阻塞默认校验
    primitive_warnings = [w for w in result["warnings"]
                          if "layer type 未在 scene_render 注册" in w]
    assert len(primitive_warnings) == 1, result["warnings"]
    assert "grid" in primitive_warnings[0]
    strict = v5_catalog.validate_catalog(root=root, strict=True)
    assert strict["ok"] is False
    monkeypatch.setattr(v5_catalog, "_scene_primitives", lambda: None)
    assert v5_catalog.validate_catalog(root=root, strict=True)["ok"] is True


def test_validate_catalog_strict_fails_on_warning(tmp_path):
    root = _make_root(tmp_path, with_optional=False)  # props/packs 为空 → warning
    normal = v5_catalog.validate_catalog(root=root)
    strict = v5_catalog.validate_catalog(root=root, strict=True)
    assert normal["ok"] is True
    assert normal["warnings"]
    assert strict["ok"] is False


def test_validate_catalog_reference_errors(tmp_path):
    root = _make_root(tmp_path)
    bad_template = _template("t_bad")
    bad_template["style"] = "__no_style__"
    bad_template["cast"] = [{"role": "host", "character": "__no_char__"}]
    bad_template["acts"] = [{"id": "a1", "duration": 5, "scene": "__no_scene__",
                             "camera": "static", "line": "host"}]
    _write(root, "templates", bad_template)
    result = v5_catalog.validate_catalog(root=root)
    assert not result["ok"]
    joined = "\n".join(result["errors"])
    assert "__no_style__" in joined
    assert "__no_char__" in joined
    assert "__no_scene__" in joined


def test_validate_catalog_schema_errors(tmp_path):
    root = _make_root(tmp_path)
    bad_char = _character("bad_char")
    del bad_char["appearance"]
    # 新契约：character.voice 支持 voice_id 引用或 inline instruction（anyOf），
    # 两者都没有时必须被 schema 拒绝（旧断言要求错误文本含 "instruction"，已改为
    # 断言 voice 非法由 schema anyOf 报错，避免绑定具体措辞）。
    bad_char["voice"] = {"provider": "breeze"}
    _write(root, "characters", bad_char)
    bad_scene = _scene("bad_scene")
    bad_scene["layers"] = [{"type": "paper", "color": "not-a-color!"}]
    _write(root, "scenes", bad_scene)
    result = v5_catalog.validate_catalog(root=root)
    assert not result["ok"]
    joined = "\n".join(result["errors"])
    assert "appearance" in joined
    assert "bad_char" in joined
    assert "bad_scene" in joined


def test_validate_catalog_line_and_role_errors(tmp_path):
    root = _make_root(tmp_path)
    template = _template("t_line")
    template["acts"] = [{"id": "a1", "duration": 5, "scene": "sc1",
                         "camera": "static", "line": "guest_x"}]
    template["cast"] = [{"role": "host", "character": "ch1"},
                        {"role": "host", "character": "ch1"}]  # role 重复
    _write(root, "templates", template)
    result = v5_catalog.validate_catalog(root=root)
    joined = "\n".join(result["errors"])
    assert not result["ok"]
    assert "重复" in joined
    assert "guest_x" in joined


def test_validate_catalog_custom_id_rules(tmp_path):
    root = _make_root(tmp_path)
    # 新对象但没有 custom_ 前缀
    bad = _style("extra_style")
    _write(root, "custom/styles", bad)
    # 文件名与 id 不一致
    (root / "custom/scenes" / "wrong_name.json").write_text(
        json.dumps(_scene("scene_other"), ensure_ascii=False), encoding="utf-8")
    result = v5_catalog.validate_catalog(root=root)
    joined = "\n".join(result["errors"])
    assert not result["ok"]
    assert "custom_" in joined
    assert "文件名必须与 id 一致" in joined


def test_validate_catalog_resource_alpha(tmp_path):
    root = _make_root(tmp_path)
    from PIL import Image

    image = root / "assets" / "no_alpha.png"
    image.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (32, 32), (255, 0, 0)).save(image)
    char = _character("ch_img")
    char["image"] = "assets/no_alpha.png"
    _write(root, "characters", char)
    result = v5_catalog.validate_catalog(root=root)
    assert not result["ok"]
    assert any("alpha" in err for err in result["errors"])
    # image_alpha=false 可关闭检查
    char["image_alpha"] = False
    _write(root, "characters", char)
    relaxed = v5_catalog.validate_catalog(root=root)
    assert not any("alpha" in err for err in relaxed["errors"])


def test_validate_catalog_props_and_pack_refs(tmp_path):
    root = _make_root(tmp_path)
    pack = _pack("pack_bad")
    pack["scenes"] = ["__missing_scene__"]
    _write(root, "packs", pack)
    result = v5_catalog.validate_catalog(root=root)
    assert not result["ok"]
    assert any("__missing_scene__" in err for err in result["errors"])


# ---------------------------------------------------------------------------
# validate_item（API PUT 语义）
# ---------------------------------------------------------------------------
def test_validate_item_new_and_partial(tmp_path):
    root = _make_root(tmp_path)
    catalog = v5_catalog.load_catalog(root)
    # 新对象（完整合法）
    new_char = _character("custom_new_char")
    ok = v5_catalog.validate_item("characters", new_char, catalog)
    assert ok["ok"] is True, ok["errors"]
    # 新对象缺字段
    bad = _character("custom_bad")
    del bad["voice"]
    bad_res = v5_catalog.validate_item("characters", bad, catalog)
    assert bad_res["ok"] is False
    # partial 覆盖内建（只改 voice.instruction）
    partial = {"id": "ch1", "voice": {"instruction": "只改这一句"}}
    part_res = v5_catalog.validate_item("characters", partial, catalog)
    assert part_res["ok"] is True, part_res["errors"]
    assert part_res["merged"] is True
    # 非 custom_ 前缀的新 id
    no_prefix = v5_catalog.validate_item("characters", _character("nope"), catalog)
    assert no_prefix["ok"] is False
    # 未知 kind
    unknown = v5_catalog.validate_item("dragon", {}, catalog)
    assert unknown["ok"] is False


# ---------------------------------------------------------------------------
# export_custom
# ---------------------------------------------------------------------------
def test_export_custom_and_reload(tmp_path):
    root = _make_root(tmp_path)
    result = v5_catalog.export_custom("characters", "ch1", root=root)
    assert result["ok"] is True
    exported = Path(result["path"])
    assert exported.is_file()
    data = json.loads(exported.read_text(encoding="utf-8"))
    assert data["id"] == "ch1" and data["appearance"]
    # 修改导出副本 → 重新加载生效（深合并）
    data["voice"]["instruction"] = "导出后改过的音色"
    exported.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    catalog = v5_catalog.load_catalog(root)
    assert catalog["characters"]["ch1"]["voice"]["instruction"] == "导出后改过的音色"
    assert catalog["characters"]["ch1"]["appearance"]["age"] == 30
    # 不存在的 id → V5Error
    with pytest.raises(v5_catalog.V5Error):
        v5_catalog.export_custom("characters", "__missing__", root=root)


# ---------------------------------------------------------------------------
# 摘要 / schema 文件 / 真实目录
# ---------------------------------------------------------------------------
def test_catalog_summary(tmp_path):
    root = _make_root(tmp_path)
    catalog = v5_catalog.load_catalog(root)
    summary = v5_catalog.catalog_summary(catalog)
    assert summary["ok"] is True
    assert summary["counts"]["styles"] == 1
    style_rows = summary["styles"]
    assert style_rows[0]["id"] == "s1"
    assert "palette" in style_rows[0]
    char_row = summary["characters"][0]
    assert char_row["role_pool"] == ["host", "narrator"]
    tpl_row = summary["templates"][0]
    assert tpl_row["style"] == "s1" and tpl_row["act_count"] == 2
    # 单 kind 过滤
    filtered = v5_catalog.catalog_summary(catalog, "styles")
    assert filtered["kinds"] == ["styles"] and "scenes" not in filtered


def test_schema_files_draft_2020():
    for name in ("style", "scene", "character", "template", "prop", "pack", "timeline"):
        path = REAL_ROOT / "schema" / f"{name}.schema.json"
        assert path.is_file(), path
        schema = json.loads(path.read_text(encoding="utf-8"))
        assert "draft/2020-12" in str(schema.get("$schema"))
        assert isinstance(schema.get("properties"), dict)
        assert schema.get("required")


def test_real_catalog_smoke():
    """真实 contract/v5：结构可读 + custom 4 类示例存在且单项校验通过。"""
    catalog = v5_catalog.load_catalog()
    assert set(KINDS) <= set(catalog)
    assert catalog["root"] == str(REAL_ROOT)
    result = v5_catalog.validate_catalog(catalog)
    assert {"ok", "errors", "warnings", "stats"} <= set(result)
    # V19 角色清理删掉了 custom_lin；这里换成当前真实存在的 4 类 custom 示例
    # （V47 新增角色的 custom_old_inventor 仍是 custom_ 前缀的“新增对象”语义）。
    custom_ids = {
        "styles": "custom_holo_paper",
        "characters": "custom_old_inventor",
        "scenes": "custom_holo_gallery",
        "templates": "custom_holo_talk",
    }
    for kind, item_id in custom_ids.items():
        item = catalog[kind].get(item_id)
        assert isinstance(item, dict), f"缺少自定义示例 {kind}/{item_id}"
        single = v5_catalog.validate_item(kind, item, catalog)
        assert single["ok"] is True, single["errors"]


def test_real_catalog_all_green_and_quantities():
    """验收级：真实目录 validate 全绿（0 errors / 0 warnings），数量达标。"""
    catalog = v5_catalog.load_catalog()
    result = v5_catalog.validate_catalog(catalog, strict=True)
    assert result["ok"] is True, {
        "errors": result["errors"][:20], "warnings": result["warnings"][:20]}
    counts = result["stats"]["counts"]
    assert counts["styles"] >= 16, counts
    assert counts["scenes"] >= 60, counts
    assert counts["characters"] >= 24, counts
    assert counts["props"] >= 40, counts
    assert counts["templates"] >= 16, counts
    assert counts["packs"] >= 2, counts


# ---------------------------------------------------------------------------
# props / packs / timeline schema
# ---------------------------------------------------------------------------
def test_prop_and_pack_schema(tmp_path):
    root = _make_root(tmp_path)
    # 合法道具（带 appearance/style）通过
    good_prop = {"id": "custom_good_prop", "label": "好道具", "kind": "banner",
                 "tags": ["history"],
                 "appearance": {"shape": "rectangle_banner", "main": "#7a1f1a",
                                "accent": "#c9a227", "material": "silk",
                                "size": [0.16, 0.3]},
                 "style": {"default": {"line": 1.0},
                           "ink_chronicle": {"line": 1.3}}}
    assert v5_catalog.schema_errors("props", good_prop, root) == []
    # 缺 label → schema error
    bad_prop = {"id": "custom_bad_prop", "appearance": {"shape": "x"}}
    assert any("label" in err for err in
               v5_catalog.schema_errors("props", bad_prop, root))
    # appearance 颜色非法 → schema error
    bad_color = dict(good_prop, id="custom_bad_color",
                     appearance={"shape": "x", "main": "not-a-color"})
    assert any("appearance" in err for err in
               v5_catalog.schema_errors("props", bad_color, root))
    # pack schema：引用数组必须是 id 字符串
    good_pack = _pack("custom_good_pack")
    assert v5_catalog.schema_errors("packs", good_pack, root) == []
    bad_pack = {"id": "custom_bad_pack", "label": "坏包", "styles": "not-a-list"}
    assert v5_catalog.schema_errors("packs", bad_pack, root)
    # 经 validate_catalog 走完整链路：坏 props 进 errors
    _write(root, "props", bad_prop)
    result = v5_catalog.validate_catalog(root=root)
    assert any("custom_bad_prop" in err for err in result["errors"])


def test_prop_and_pack_validate_item(tmp_path):
    root = _make_root(tmp_path)
    catalog = v5_catalog.load_catalog(root)
    prop = {"id": "custom_uploaded_prop", "label": "用户道具"}
    ok = v5_catalog.validate_item("props", prop, catalog)
    assert ok["ok"] is True, ok["errors"]
    bad = {"id": "custom_uploaded_prop"}
    assert v5_catalog.validate_item("props", bad, catalog)["ok"] is False
    pack = _pack("custom_uploaded_pack")
    assert v5_catalog.validate_item("packs", pack, catalog)["ok"] is True
    bad_pack = dict(pack, id="custom_uploaded_pack", scenes=["__nope__"])
    bad_result = v5_catalog.validate_item("packs", bad_pack, catalog)
    assert bad_result["ok"] is False
    assert any("__nope__" in err for err in bad_result["errors"])


def _timeline_fixture() -> dict:
    return {
        "version": 5, "template": "t1", "label": "测试", "mode": "v5_motion2d",
        "fps": 25, "resolution": [1280, 720], "duration": 12.0,
        "style": {"id": "s1", "label": "风格", "technique": ["neon_toon"],
                  "palette": {"bg": ["#000000"]}},
        "tracks": [
            {"id": "V1", "kind": "video", "clips": [
                {"id": "v1", "src": "a.mp4", "in": 0.0, "out": 6.0, "at": 0.0,
                 "kind": "dialogue", "scene": "sc1", "role": "host"},
                {"id": "v2", "src": "b.mp4", "in": 0.0, "out": 6.0, "at": 6.0,
                 "kind": "broll", "scene": "sc1", "role": None},
            ]},
            {"id": "A1", "kind": "voice", "clips": [
                {"id": "a1", "src": "a.wav", "at": 0.0, "out": 6.0,
                 "text": "台词", "character": "ch1", "provider": "breeze",
                 "lead_sec": 0.1, "speech_duration": 5.8},
            ]},
            {"id": "A2", "kind": "music", "clips": [
                {"id": "bgm", "src": "bgm.wav", "at": 0.0, "out": 12.0,
                 "gain": 0.16, "fade_in": 1.5, "fade_out": 3.0},
            ]},
            {"id": "S1", "kind": "subtitle", "clips": [
                # subtitle 的 out 是绝对结束时间（render_v5 约定）
                {"id": "s1", "text": "台词", "at": 0.1, "out": 5.95, "speaker": "老周"},
            ]},
        ],
        "meta": {"speech_driven": True, "voice_provider": "breeze"},
    }


def test_validate_timeline_fixture():
    timeline = _timeline_fixture()
    result = v5_catalog.validate_timeline(timeline)
    assert result["ok"] is True, result["errors"]
    assert result["errors"] == []
    # 缺 video 轨 → error
    no_video = dict(timeline, tracks=[t for t in timeline["tracks"]
                                      if t["kind"] != "video"])
    assert v5_catalog.validate_timeline(no_video)["ok"] is False
    # 缺 voice → subtitle 轨 warn
    no_voice = dict(timeline, tracks=[t for t in timeline["tracks"]
                                      if t["kind"] != "voice"])
    voice_missing = v5_catalog.validate_timeline(no_voice)
    assert any("voice" in w for w in voice_missing["warnings"])
    # 片段 id 同轨重复 → error
    dup = _timeline_fixture()
    dup["tracks"][0]["clips"][1]["id"] = "v1"
    assert v5_catalog.validate_timeline(dup)["ok"] is False
    # 时间越界 → warning（video: at+out；subtitle: 绝对 out）
    over = _timeline_fixture()
    over["tracks"][0]["clips"][1]["out"] = 99.0
    over_result = v5_catalog.validate_timeline(over)
    assert any("超出 timeline.duration" in w for w in over_result["warnings"])
    # 类型错误（fps 字符串）→ schema error
    bad_type = dict(timeline, fps="25")
    assert v5_catalog.validate_timeline(bad_type)["ok"] is False


def test_validate_timeline_real_render_output():
    path = (REPO_ROOT / "outputs" / "templates_v5" / "tonight_show" / "timeline.json")
    if not path.is_file():
        pytest.skip("render_v5 产物尚未生成")
    result = v5_catalog.validate_timeline_file(path)
    assert result["ok"] is True, result["errors"]
    assert Path(result["path"]).is_file()


def test_cli_validate_and_list(tmp_path):
    root = _make_root(tmp_path)
    env = {"PYTHONIOENCODING": "utf-8"}
    proc = subprocess.run(
        [sys.executable, "-m", "pipeline.v5_cli", "validate", "--root", str(root)],
        cwd=str(REPO_ROOT), capture_output=True, text=True, encoding="utf-8",
        errors="replace", env={**__import__("os").environ, **env}, timeout=180)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    last_line = [line for line in proc.stdout.splitlines() if line.strip()][-1]
    payload = json.loads(last_line)
    assert payload["ok"] is True

    # list 子命令
    proc_list = subprocess.run(
        [sys.executable, "-m", "pipeline.v5_cli", "list-styles", "--root", str(root)],
        cwd=str(REPO_ROOT), capture_output=True, text=True, encoding="utf-8",
        errors="replace", env={**__import__("os").environ, **env}, timeout=180)
    assert proc_list.returncode == 0, proc_list.stderr
    last = [line for line in proc_list.stdout.splitlines() if line.strip()][-1]
    listed = json.loads(last)
    assert listed["styles"][0]["id"] == "s1"

    # validate --timeline（render_v5 产物）
    timeline_path = tmp_path / "timeline.json"
    timeline_path.write_text(json.dumps(_timeline_fixture(), ensure_ascii=False),
                             encoding="utf-8")
    proc_timeline = subprocess.run(
        [sys.executable, "-m", "pipeline.v5_cli", "validate", "--root", str(root),
         "--timeline", str(timeline_path)],
        cwd=str(REPO_ROOT), capture_output=True, text=True, encoding="utf-8",
        errors="replace", env={**__import__("os").environ, **env}, timeout=180)
    assert proc_timeline.returncode == 0, proc_timeline.stdout + proc_timeline.stderr
    payload_timeline = json.loads(
        [line for line in proc_timeline.stdout.splitlines() if line.strip()][-1])
    assert payload_timeline["timeline"]["ok"] is True

    # 失败路径：坏 root（不存在的目录）也能给出 JSON + exit != 0
    proc_bad = subprocess.run(
        [sys.executable, "-m", "pipeline.v5_cli", "validate", "--root",
         str(tmp_path / "no_such_root")],
        cwd=str(REPO_ROOT), capture_output=True, text=True, encoding="utf-8",
        errors="replace", env={**__import__("os").environ, **env}, timeout=180)
    assert proc_bad.returncode != 0
    assert "{" in proc_bad.stdout.splitlines()[-1]
