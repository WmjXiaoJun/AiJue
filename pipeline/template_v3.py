# -*- coding: utf-8 -*-
"""Template v3 加载 + 校验：模板即节目形态（舞台/站位/动作/镜头语言/H3 配置）。

设计目标（docs/PLAN_V3_TEMPLATE_REFACTOR.md）：
- 16 个模板必须填 stage / layout / cast / props / action_library / beats / shots / h3；
- 校验器强制必填字段、cast/style 合法、timeline 总长、shot 覆盖、16 模板差异性；
- 作为 pipeline.stage / storyboard_v3 / render_v3 的唯一模板入口。

用法（仓库根目录）：
  python -m pipeline.template_v3 --validate contract/templates_v3
  python -m pipeline.template_v3 --validate contract/templates_v3 --expect 16
  python -m pipeline.template_v3 --validate-file contract/templates_v3/tonight_show.json
  python -m pipeline.template_v3 --list

约定：stdout 先输出人类可读行，最后一行是 JSON 结果；失败 exit code != 0。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_TEMPLATE_DIR = REPO_ROOT / "contract" / "templates_v3"
CHARACTERS_DIR = REPO_ROOT / "contract" / "characters"
STYLE_PRESETS_PATH = REPO_ROOT / "contract" / "style_presets.json"
PLATE_DIRS = (
    REPO_ROOT / "outputs" / "scene_plates",
    REPO_ROOT / "outputs" / "scene_plates" / "v3",
)

STAGE_TYPES = {"3d_set", "2d_plate", "h3_scene"}
H3_WORKFLOWS = {"04_i2v_fl2v_4step_sla", "05_ref2va_4step_sla"}
H3_MODES = {"audio_ref", "fl2v"}
SHOT_SIZES = {
    "Wide", "Wide / Establishing", "Full", "Medium",
    "Medium Close-up", "Close-up", "Extreme Close-up",
}
TRANSITIONS = {"hard_cut", "fade", "crossfade", "fade_black"}
CARD_INTENTS = {"title_card", "end_card", "meme_card"}
REQUIRED_TOP = (
    "id", "label", "style", "aspect", "target_duration", "stage", "cast",
    "action_library", "segments", "camera_language", "packaging", "audio", "h3",
)
REQUIRED_SHOT = (
    "intent", "size", "angle", "movement", "action", "props",
    "h3_mode", "camera_zone",
)
CARD_SHOT_FIELDS = ("intent", "size", "angle", "movement", "action", "props", "h3_mode")


class TemplateError(ValueError):
    """模板文件缺失 / JSON 非法 / 结构错误。"""


# ---------------------------------------------------------------------------
# 基础工具
# ---------------------------------------------------------------------------
def _read_json(path: Path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except OSError as exc:
        raise TemplateError(f"无法读取 {path}: {exc}") from exc
    except ValueError as exc:
        raise TemplateError(f"JSON 非法 {path}: {exc}") from exc


def _is_num(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _is_pos_num(value) -> bool:
    return _is_num(value) and float(value) > 0


def _nonempty_str(value) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _num_pair(value) -> bool:
    return (isinstance(value, (list, tuple)) and len(value) == 2
            and all(_is_num(v) for v in value))


def _err(errors: list, msg: str) -> None:
    errors.append(msg)


def _warn(warnings: list, msg: str) -> None:
    warnings.append(msg)


def load_presets(path: Path | None = None) -> dict:
    data = _read_json(path or STYLE_PRESETS_PATH)
    return data if isinstance(data, dict) else {}


def known_characters(chars_dir: Path | None = None) -> dict:
    base = Path(chars_dir) if chars_dir else CHARACTERS_DIR
    out: dict[str, dict] = {}
    for p in sorted(base.glob("*.json")):
        try:
            data = _read_json(p)
        except TemplateError:
            continue
        if isinstance(data, dict) and data.get("id"):
            out[str(data["id"])] = data
    return out


def resolve_template_path(template_id: str, template_dir: Path | None = None) -> Path | None:
    """按 id 找模板文件；兼容传 ``xxx_5min`` / ``xxx_demo`` 的旧调用。"""
    base = Path(template_dir) if template_dir else DEFAULT_TEMPLATE_DIR
    candidates = [str(template_id or "").strip()]
    for suffix in ("_5min", "_demo", "_v3"):
        if candidates[0].endswith(suffix):
            candidates.append(candidates[0][: -len(suffix)])
        else:
            candidates.append(candidates[0] + suffix)
    for name in candidates:
        if not name:
            continue
        p = base / f"{name}.json"
        if p.is_file():
            return p
    return None


def load(template_id: str, template_dir: Path | None = None) -> dict:
    """加载模板 JSON（不校验；校验用 validate/validate_file）。"""
    p = resolve_template_path(template_id, template_dir)
    if p is None:
        raise TemplateError(f"模板不存在: {template_id}（目录 {template_dir or DEFAULT_TEMPLATE_DIR}）")
    data = _read_json(p)
    if not isinstance(data, dict):
        raise TemplateError(f"模板根节点必须是 object: {p}")
    data.setdefault("_path", str(p))
    return data


def load_file(path: Path | str) -> dict:
    data = _read_json(Path(path))
    if not isinstance(data, dict):
        raise TemplateError(f"模板根节点必须是 object: {path}")
    data.setdefault("_path", str(Path(path)))
    return data


def _plate_candidates(plate: str) -> list[Path]:
    name = str(plate or "").strip()
    if not name:
        return []
    if name.lower().endswith((".png", ".jpg", ".jpeg", ".webp")):
        names = [name]
    else:
        names = [f"{name}.png", name]
    out: list[Path] = []
    for d in PLATE_DIRS:
        for n in names:
            out.append(Path(d) / n)
    return out


def plate_path(template: dict) -> Path | None:
    """模板 stage.plate 对应的真实资产路径；缺失返回 None。"""
    for p in _plate_candidates(str((template.get("stage") or {}).get("plate") or "")):
        if p.is_file():
            return p
    return None


# ---------------------------------------------------------------------------
# 单模板校验
# ---------------------------------------------------------------------------
def validate(template: dict, *, template_dir: Path | None = None,
             presets: dict | None = None, characters: dict | None = None,
             strict_plates: bool = True) -> dict:
    """校验单个模板，返回 {id, ok, errors, warnings, stats}。"""
    errors: list[str] = []
    warnings: list[str] = []
    data = template if isinstance(template, dict) else {}
    presets = presets if presets is not None else load_presets()
    characters = characters if characters is not None else known_characters()
    preset_map = (presets or {}).get("presets") or {}

    if not data:
        _err(errors, "模板为空")
        return _result("", errors, warnings)
    tid = str(data.get("id") or "").strip()
    if not tid:
        _err(errors, "id: 必填且非空")
    for key in REQUIRED_TOP:
        if key not in data:
            _err(errors, f"{key}: 缺少必填字段")
    if _nonempty_str(data.get("label")) is False:
        _err(errors, "label: 必填且非空")
    if data.get("aspect") not in ("16:9", "9:16"):
        _err(errors, "aspect: 必须是 16:9 或 9:16")
    if not _is_pos_num(data.get("target_duration")):
        _err(errors, "target_duration: 必须是正数")

    # ---- style ----
    style = str(data.get("style") or "")
    preset = preset_map.get(style)
    if not style:
        _err(errors, "style: 必填")
    elif preset is None:
        _err(errors, f"style: {style!r} 不在 contract/style_presets.json 的 presets 中")

    # ---- stage ----
    stage = data.get("stage")
    layout: dict = {}
    prop_ids: set[str] = set()
    if not isinstance(stage, dict):
        _err(errors, "stage: 必须是 object")
    else:
        stype = str(stage.get("type") or "")
        if stype not in STAGE_TYPES:
            _err(errors, f"stage.type: {stype!r} 不在 {sorted(STAGE_TYPES)}")
        plate = str(stage.get("plate") or "").strip()
        if not plate:
            _err(errors, "stage.plate: 必填")
        else:
            found = plate_path({"stage": {"plate": plate}})
            if found is None:
                if stage.get("plate_generate") or not strict_plates:
                    _warn(warnings, f"stage.plate: 资产暂缺（允许生成）{plate}")
                else:
                    _err(errors, f"stage.plate: 资产缺失 outputs/scene_plates/{plate}.png")
        raw_layout = stage.get("layout")
        if not isinstance(raw_layout, dict):
            _err(errors, "stage.layout: 必须是 object（zone_id -> [x,y] + ground_y）")
        else:
            layout = raw_layout
            zones = [k for k in layout.keys() if k != "ground_y"]
            if len(zones) < 2:
                _err(errors, "stage.layout: 至少需要 2 个站位 zone")
            for z in zones:
                v = layout.get(z)
                if not _num_pair(v) or not all(0.0 <= float(x) <= 1.0 for x in v):
                    _err(errors, f"stage.layout.{z}: 必须是 [x,y]，0<=x,y<=1")
            gy = layout.get("ground_y")
            if not _is_num(gy) or not (0.30 <= float(gy) <= 1.0):
                _err(errors, "stage.layout.ground_y: 必须是 0.30~1.0 的数字")
        raw_props = stage.get("props")
        if not isinstance(raw_props, list) or not raw_props:
            _err(errors, "stage.props: 必须是非空数组（道具/互动）")
        else:
            for i, prop in enumerate(raw_props):
                if not isinstance(prop, dict):
                    _err(errors, f"stage.props[{i}]: 必须是 object")
                    continue
                pid = str(prop.get("id") or "").strip()
                if not pid:
                    _err(errors, f"stage.props[{i}].id: 必填")
                elif pid in prop_ids:
                    _err(errors, f"stage.props[{i}].id: 重复 {pid}")
                prop_ids.add(pid)
                zone = str(prop.get("zone") or "").strip()
                if zone not in layout or zone == "ground_y":
                    _err(errors, f"stage.props[{i}].zone: {zone!r} 不在 layout 站位 zone 中")
                if not _nonempty_str(prop.get("interaction")):
                    _err(errors, f"stage.props[{i}].interaction: 必填")
        zones = stage.get("camera_zones")
        if not isinstance(zones, list) or len(zones) < 2:
            _err(errors, "stage.camera_zones: 至少 2 个机位 zone")
        else:
            seen = set()
            for z in zones:
                if not _nonempty_str(z):
                    _err(errors, "stage.camera_zones: 元素必须是非空字符串")
                elif z in seen:
                    _err(errors, f"stage.camera_zones: 重复 {z}")
                seen.add(z)
        if not _nonempty_str(stage.get("scene_desc")):
            _warn(warnings, "stage.scene_desc: 建议填写（H3 prompt 的场景描述）")

    # ---- cast ----
    cast = data.get("cast")
    cast_blocks: set[str] = set()
    used_character_roles: list[tuple[str, str]] = []
    if not isinstance(cast, list) or len(cast) < 2:
        _err(errors, "cast: 至少 2 个角色")
    else:
        roles_seen = {str(c.get("role") or "") for c in cast if isinstance(c, dict)}
        if not any(r == "host" or r.startswith("host") for r in roles_seen):
            _warn(warnings, "cast: 建议至少一个 host 角色")
        for i, member in enumerate(cast):
            if not isinstance(member, dict):
                _err(errors, f"cast[{i}]: 必须是 object")
                continue
            role = str(member.get("role") or "").strip()
            if not role:
                _err(errors, f"cast[{i}].role: 必填")
            character = str(member.get("character") or "").strip()
            if character not in characters:
                _err(errors, f"cast[{i}].character: {character!r} 不是现有角色 {sorted(characters)}")
            block = str(member.get("block") or "").strip()
            if block not in layout or block == "ground_y":
                _err(errors, f"cast[{i}].block: {block!r} 不在 stage.layout 站位 zone 中")
            elif block in cast_blocks:
                _err(errors, f"cast[{i}].block: 站位重复 {block}")
            cast_blocks.add(block)
            if not _nonempty_str(member.get("wardrobe")):
                _warn(warnings, f"cast[{i}].wardrobe: 建议填写（身份/服装锚点）")
            mprops = member.get("props")
            if mprops is not None:
                if not isinstance(mprops, list):
                    _err(errors, f"cast[{i}].props: 必须是数组")
                else:
                    for p in mprops:
                        if str(p) not in prop_ids:
                            _err(errors, f"cast[{i}].props: 未定义道具 {p}")
            used_character_roles.append((role, character))

    # ---- action_library ----
    actions = data.get("action_library")
    action_ids: set[str] = set()
    action_optional: set[str] = set()
    if not isinstance(actions, list) or len(actions) < 6:
        _err(errors, "action_library: 至少 6 个动作")
        actions = actions if isinstance(actions, list) else []
    for i, act in enumerate(actions):
        if not isinstance(act, dict):
            _err(errors, f"action_library[{i}]: 必须是 object")
            continue
        aid = str(act.get("id") or "").strip()
        if not aid:
            _err(errors, f"action_library[{i}].id: 必填")
        elif aid in action_ids:
            _err(errors, f"action_library[{i}].id: 重复 {aid}")
        action_ids.add(aid)
        if not _nonempty_str(act.get("h3_prompt")):
            _err(errors, f"action_library[{i}].h3_prompt: 必填")
        prop = act.get("prop")
        if prop not in (None, "") and str(prop) not in prop_ids:
            _err(errors, f"action_library[{i}].prop: 未定义道具 {prop}")
        if act.get("optional") is True:
            action_optional.add(aid)

    # ---- segments / shots ----
    segments = data.get("segments")
    used_actions: dict[str, int] = {}
    total_duration = 0.0
    shot_count = 0
    if not isinstance(segments, list) or len(segments) < 2:
        _err(errors, "segments: 至少 2 个段落")
        segments = segments if isinstance(segments, list) else []
    seg_ids: set[str] = set()
    for si, seg in enumerate(segments):
        if not isinstance(seg, dict):
            _err(errors, f"segments[{si}]: 必须是 object")
            continue
        sid = str(seg.get("id") or "").strip()
        if not sid:
            _err(errors, f"segments[{si}].id: 必填")
        elif sid in seg_ids:
            _err(errors, f"segments[{si}].id: 重复 {sid}")
        seg_ids.add(sid)
        if not _nonempty_str(seg.get("name")):
            _err(errors, f"segments[{si}].name: 必填")
        dur = seg.get("duration")
        if not _is_pos_num(dur):
            _err(errors, f"segments[{si}].duration: 必须是正数")
        else:
            total_duration += float(dur)
        beats = seg.get("beats")
        if not isinstance(beats, list) or not beats:
            _err(errors, f"segments[{si}].beats: 必须是非空数组（叙事节拍）")
        else:
            for b in beats:
                if not _nonempty_str(b):
                    _err(errors, f"segments[{si}].beats: 节拍必须是非空字符串")
        shots = seg.get("shots")
        if not isinstance(shots, list) or not shots:
            _err(errors, f"segments[{si}].shots: 必须是非空数组（镜头意图）")
            continue
        for shi, shot in enumerate(shots):
            shot_count += 1
            label = f"segments[{si}].shots[{shi}]"
            if not isinstance(shot, dict):
                _err(errors, f"{label}: 必须是 object")
                continue
            for key in REQUIRED_SHOT:
                if key not in shot:
                    _err(errors, f"{label}: 缺少必填字段 {key}")
            intent = shot.get("intent")
            if not _nonempty_str(intent):
                _err(errors, f"{label}.intent: 必填")
            if shot.get("size") not in SHOT_SIZES:
                _err(errors, f"{label}.size: {shot.get('size')!r} 不在 {sorted(SHOT_SIZES)}")
            for key in ("angle", "movement"):
                if not _nonempty_str(shot.get(key)):
                    _err(errors, f"{label}.{key}: 必填")
            action = str(shot.get("action") or "").strip()
            if action and action not in action_ids:
                _err(errors, f"{label}.action: {action!r} 不在 action_library 中")
            elif action:
                used_actions[action] = used_actions.get(action, 0) + 1
            mode = shot.get("h3_mode")
            if mode not in H3_MODES:
                _err(errors, f"{label}.h3_mode: {mode!r} 不在 {sorted(H3_MODES)}")
            zone = str(shot.get("camera_zone") or "").strip()
            czones = stage.get("camera_zones") if isinstance(stage, dict) else []
            if not zone:
                _err(errors, f"{label}.camera_zone: 必填且非空（H3 首帧取景依赖）")
            elif isinstance(czones, list) and czones and zone not in czones:
                _err(errors, f"{label}.camera_zone: {zone!r} 不在 stage.camera_zones 中")
            props = shot.get("props")
            if not isinstance(props, list):
                _err(errors, f"{label}.props: 必须是数组（可为空）")
            else:
                for p in props:
                    if str(p) not in prop_ids:
                        _err(errors, f"{label}.props: 未定义道具 {p}")
    target = data.get("target_duration")
    if _is_pos_num(target) and total_duration > 0:
        delta = abs(total_duration - float(target)) / float(target)
        if delta > 0.15:
            _err(errors, f"timeline: segments 合计 {total_duration:.1f}s 与 "
                         f"target_duration {float(target):.1f}s 误差 {delta:.1%} > 15%")
        elif delta > 0.05:
            _warn(warnings, f"timeline: segments 合计 {total_duration:.1f}s 与 "
                            f"target_duration {float(target):.1f}s 误差 {delta:.1%}")
    for aid in sorted(action_ids):
        if aid and used_actions.get(aid, 0) == 0:
            if aid in action_optional:
                _warn(warnings, f"action_library.{aid}: 未被任何 shot 引用（optional）")
            else:
                _err(errors, f"action_library.{aid}: 未被任何 shot 引用（加 shot 或标记 optional）")

    # ---- camera_language ----
    cam = data.get("camera_language")
    if not isinstance(cam, dict):
        _err(errors, "camera_language: 必须是 object")
    else:
        for key in ("default", "wide", "reaction", "close"):
            if not _nonempty_str(cam.get(key)):
                _err(errors, f"camera_language.{key}: 必填（运镜描述）")

    # ---- packaging / audio ----
    pack = data.get("packaging")
    if not isinstance(pack, dict):
        _err(errors, "packaging: 必须是 object")
    else:
        for key in ("lower_third", "bgm", "subtitle_style"):
            if not _nonempty_str(pack.get(key)):
                _err(errors, f"packaging.{key}: 必填")
        if pack.get("transition") not in TRANSITIONS:
            _err(errors, f"packaging.transition: {pack.get('transition')!r} 不在 {sorted(TRANSITIONS)}")
    audio = data.get("audio")
    if not isinstance(audio, dict):
        _err(errors, "audio: 必须是 object")
    else:
        if not _nonempty_str(audio.get("ambience")):
            _err(errors, "audio.ambience: 必填")
        if not isinstance(audio.get("sfx"), list):
            _err(errors, "audio.sfx: 必须是数组")
        elif not audio.get("sfx"):
            _warn(warnings, "audio.sfx: 建议至少 1 个音效")

    # ---- h3 ----
    h3 = data.get("h3")
    if not isinstance(h3, dict):
        _err(errors, "h3: 必须是 object")
    else:
        if h3.get("workflow") not in H3_WORKFLOWS:
            _err(errors, f"h3.workflow: {h3.get('workflow')!r} 不在 {sorted(H3_WORKFLOWS)}")
        dur = h3.get("duration")
        if not _num_pair(dur) or not (2.0 <= float(dur[0]) <= float(dur[1]) <= 10.0):
            _err(errors, "h3.duration: 必须是 [min,max]，2<=min<=max<=10")
        if h3.get("fps") not in (24, 25):
            _err(errors, "h3.fps: 必须是 24 或 25")
        res = str(h3.get("resolution") or "")
        m = re.fullmatch(r"(\d+)x(\d+)", res)
        if not m:
            _err(errors, "h3.resolution: 必须是 WIDTHxHEIGHT")
        else:
            w, hgt = int(m.group(1)), int(m.group(2))
            if w % 32 or hgt % 32 or w < 256 or hgt < 256:
                _err(errors, "h3.resolution: 宽高必须是 >=256 的 32 倍数（H3 约束）")

    # ---- 2D 墨水/2D preset 与 stage.type 一致性（提示不阻断）----
    if preset and style:
        char_style = str(preset.get("character_style") or "")
        if char_style == "cutout_2d" and isinstance(stage, dict) \
                and str(stage.get("type") or "") == "3d_set":
            _warn(warnings, "stage.type: 2D 风格模板建议用 2d_plate/h3_scene")

    if not tid and data.get("_path"):
        tid = Path(str(data["_path"])).stem
    stats = {
        "style": style,
        "stage_type": str((stage or {}).get("type") or ""),
        "plate": str((stage or {}).get("plate") or ""),
        "cast": len(cast or []),
        "actions": len(action_ids),
        "segments": len(segments or []),
        "shots": shot_count,
        "total_duration": round(total_duration, 1),
    }
    return _result(tid, errors, warnings, stats)


def _result(tid: str, errors: list, warnings: list, stats: dict | None = None) -> dict:
    return {"id": tid, "ok": not errors, "errors": errors,
            "warnings": warnings, "stats": stats or {}}


def template_signature(template: dict) -> tuple:
    """(stage.plate, cast 站位组合, action 使用集合) 用于 16 模板差异性检查。"""
    stage = template.get("stage") if isinstance(template.get("stage"), dict) else {}
    cast = template.get("cast") if isinstance(template.get("cast"), list) else []
    cast_sig = tuple(sorted(
        (str(c.get("role") or ""), str(c.get("character") or ""), str(c.get("block") or ""))
        for c in cast if isinstance(c, dict)
    ))
    action_ids: set[str] = set()
    for seg in template.get("segments") or []:
        if not isinstance(seg, dict):
            continue
        for shot in seg.get("shots") or []:
            if isinstance(shot, dict) and shot.get("action"):
                action_ids.add(str(shot["action"]))
    return (str(stage.get("plate") or ""), cast_sig, tuple(sorted(action_ids)))


def validate_file(path: Path | str, **kwargs) -> dict:
    data = load_file(path)
    result = validate(data, **kwargs)
    expected = Path(path).stem
    if result["id"] != expected:
        result["errors"].append(f"id: {result['id']!r} 必须与文件名 {expected!r} 一致")
        result["ok"] = False
    return result


def validate_dir(template_dir: Path | str | None = None, *, expect: int | None = None,
                 strict_plates: bool = True) -> dict:
    """校验目录下所有模板 + 跨模板差异性，返回汇总 JSON。"""
    base = Path(template_dir) if template_dir else DEFAULT_TEMPLATE_DIR
    presets = load_presets()
    characters = known_characters()
    paths = sorted(base.glob("*.json"))
    results: list[dict] = []
    signatures: dict[tuple, str] = {}
    plates: dict[str, list[str]] = {}
    for p in paths:
        try:
            data = load_file(p)
            result = validate(data, template_dir=base, presets=presets,
                              characters=characters, strict_plates=strict_plates)
            expected = p.stem
            if result["id"] != expected:
                result["errors"].append(
                    f"id: {result['id']!r} 必须与文件名 {expected!r} 一致")
                result["ok"] = False
        except TemplateError as exc:
            result = {"id": p.stem, "ok": False, "errors": [str(exc)],
                      "warnings": [], "stats": {}}
        sig = None
        try:
            data = load_file(p)
            sig = template_signature(data)
            plate = str((data.get("stage") or {}).get("plate") or "")
            plates.setdefault(plate, []).append(result["id"])
        except TemplateError:
            pass
        if sig is not None:
            if sig in signatures:
                result["errors"].append(
                    f"差异性: 与 {signatures[sig]} 的 stage.plate+cast站位+action集合 完全相同")
                result["ok"] = False
            else:
                signatures[sig] = result["id"]
        results.append(result)
    # 同一 plate 被多个模板复用 -> 警告（布局/cast 不同仍可接受）
    for plate, ids in sorted(plates.items()):
        if len(ids) > 1:
            for r in results:
                if r["id"] in ids:
                    r["warnings"].append(
                        f"stage.plate: {plate} 被 {len(ids)} 个模板复用 {ids}")
    # 动作集合相似度 -> 警告（16 模板形态不应只是换皮）
    action_sets: dict[str, set[str]] = {}
    for p in paths:
        try:
            data = load_file(p)
        except TemplateError:
            continue
        ids = {str(shot.get("action") or "")
               for seg in data.get("segments") or [] if isinstance(seg, dict)
               for shot in seg.get("shots") or [] if isinstance(shot, dict)}
        action_sets[str(data.get("id") or p.stem)] = {a for a in ids if a}
    keys = sorted(action_sets)
    for i, a in enumerate(keys):
        for b in keys[i + 1:]:
            sa, sb = action_sets[a], action_sets[b]
            if not sa or not sb:
                continue
            jac = len(sa & sb) / len(sa | sb)
            if jac > 0.8:
                for r in results:
                    if r["id"] in (a, b):
                        r["warnings"].append(
                            f"差异性: 动作集合与 {b if r['id'] == a else a} 相似度 {jac:.0%}（>80%）")
    passed = sum(1 for r in results if r["ok"])
    summary = {
        "ok": passed == len(results) and (expect is None or len(results) == expect),
        "dir": str(base),
        "total": len(results),
        "passed": passed,
        "failed": len(results) - passed,
        "expect": expect,
        "results": results,
    }
    if expect is not None and len(results) != expect:
        summary["error"] = f"模板数量 {len(results)} != 期望 {expect}"
    return summary


# ---------------------------------------------------------------------------
# 便捷读取（供 stage / storyboard_v3 / render_v3 使用）
# ---------------------------------------------------------------------------
def shots(template: dict) -> list[dict]:
    """展平 segments[].shots[]，并附加 segment/shot 索引（保留原字段）。"""
    out: list[dict] = []
    for si, seg in enumerate(template.get("segments") or []):
        if not isinstance(seg, dict):
            continue
        for shi, shot in enumerate(seg.get("shots") or []):
            if not isinstance(shot, dict):
                continue
            item = dict(shot)
            item["segment_id"] = str(seg.get("id") or "")
            item["segment_name"] = str(seg.get("name") or "")
            item["segment_index"] = si
            item["shot_index"] = shi
            item["index"] = len(out)
            out.append(item)
    return out


def cast_for(template: dict, roles: dict | None = None) -> list[dict]:
    """返回 cast（合并 contract/characters 的角色资料），按 cast 顺序。"""
    chars = known_characters() if roles is None else roles
    out: list[dict] = []
    for member in template.get("cast") or []:
        if not isinstance(member, dict):
            continue
        item = dict(member)
        item.update(chars.get(str(member.get("character") or ""), {}))
        # 保证 id/name/voice 等字段以角色资料为主，role/block 以模板为主
        for key in ("role", "block", "props", "wardrobe", "character"):
            item[key] = member.get(key)
        item["character_id"] = str(member.get("character") or "")
        out.append(item)
    return out


def cast_by_role(template: dict) -> dict[str, dict]:
    return {str(c.get("role") or ""): c for c in cast_for(template)}


def actions_by_id(template: dict) -> dict[str, dict]:
    return {str(a.get("id") or ""): a for a in template.get("action_library") or []
            if isinstance(a, dict)}


def props_by_id(template: dict) -> dict[str, dict]:
    stage = template.get("stage") if isinstance(template.get("stage"), dict) else {}
    return {str(p.get("id") or ""): p for p in stage.get("props") or []
            if isinstance(p, dict)}


def stage_layout(template: dict) -> dict:
    stage = template.get("stage") if isinstance(template.get("stage"), dict) else {}
    layout = stage.get("layout") if isinstance(stage.get("layout"), dict) else {}
    return {str(k): v for k, v in layout.items()}


def load_all(template_dir: Path | None = None) -> dict[str, dict]:
    base = Path(template_dir) if template_dir else DEFAULT_TEMPLATE_DIR
    out: dict[str, dict] = {}
    for p in sorted(base.glob("*.json")):
        try:
            data = load_file(p)
        except TemplateError:
            continue
        out[str(data.get("id") or p.stem)] = data
    return out


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def _print_result(r: dict) -> None:
    mark = "ok  " if r["ok"] else "FAIL"
    stats = r.get("stats") or {}
    extra = ""
    if stats:
        extra = (f" [{stats.get('stage_type')} / {stats.get('plate')} / "
                 f"cast {stats.get('cast')} / act {stats.get('actions')} / "
                 f"{stats.get('segments')}段 {stats.get('shots')}镜 / "
                 f"{stats.get('total_duration')}s]")
    print(f"[{mark}] {r['id']}{extra}")
    for e in r.get("errors") or []:
        print(f"       ERR  {e}")
    for w in r.get("warnings") or []:
        print(f"       warn {w}")


def main(argv: list[str] | None = None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass
    ap = argparse.ArgumentParser(
        prog="python -m pipeline.template_v3",
        description="Template v3 校验器（舞台/站位/动作/beat/H3 配置）")
    group = ap.add_mutually_exclusive_group(required=True)
    group.add_argument("--validate", nargs="?", const=str(DEFAULT_TEMPLATE_DIR),
                       help="校验模板目录（默认 contract/templates_v3）")
    group.add_argument("--validate-file", default=None, help="校验单个模板文件")
    group.add_argument("--list", action="store_true", help="列出模板 id")
    ap.add_argument("--expect", type=int, default=None, help="期望模板数量（验收用 16）")
    ap.add_argument("--allow-missing-plate", action="store_true",
                    help="plate 资产缺失只警告（批量出图前用）")
    args = ap.parse_args(argv)

    if args.list:
        base = Path(DEFAULT_TEMPLATE_DIR)
        ids = sorted(p.stem for p in base.glob("*.json"))
        print("\n".join(ids))
        print(json.dumps({"ok": True, "total": len(ids), "ids": ids}, ensure_ascii=False))
        return 0

    if args.validate_file:
        res = validate_file(args.validate_file,
                            strict_plates=not args.allow_missing_plate)
        _print_result(res)
        print(json.dumps(res, ensure_ascii=False))
        return 0 if res["ok"] else 1

    res = validate_dir(args.validate, expect=args.expect,
                       strict_plates=not args.allow_missing_plate)
    for r in res["results"]:
        _print_result(r)
    if res.get("error"):
        print(f"[FAIL] {res['error']}")
    print(f"== {res['passed']}/{res['total']} 通过"
          + (f"（期望 {res['expect']}）" if res["expect"] else ""))
    summary = {k: v for k, v in res.items() if k != "results"}
    print(json.dumps(summary, ensure_ascii=False))
    return 0 if res["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
