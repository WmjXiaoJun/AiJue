# -*- coding: utf-8 -*-
"""V6 智能体修改操作（声音/剪辑/模板/工作流），返回受影响阶段用于增量重跑。"""
from __future__ import annotations

import json
from pathlib import Path

from .. import config, utils, v5_catalog
from .workflow import (WORKFLOW_DIR, default_workflow, load_workflow, merge_dict,
                       save_workflow)

REPO = Path(config.REPO_ROOT)
CONTRACT = REPO / "contract" / "v5"
V6_CONTRACT = REPO / "contract" / "v6"

#: 各操作影响的阶段（用于增量重跑建议）
AFFECTED: dict[str, list[str]] = {
    "set_voice": ["voice", "shots", "assemble", "storyboard", "qc", "publish"],
    "set_pose": ["shots", "assemble", "storyboard", "qc", "publish"],
    "patch_editorial": ["editorial", "shots", "assemble", "storyboard", "qc", "publish"],
    "patch_template": ["story", "voice", "editorial", "shots", "assemble",
                       "storyboard", "qc", "publish"],
    "patch_workflow": [],
    "set_stage_param": ["story"],
    "reset_workflow": [],
}


def _read(path: Path, default=None):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return default


def _write(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def _char_file(character_id: str) -> Path:
    return CONTRACT / "characters" / f"{character_id}.json"


def set_voice(template: str, character: str, value: str) -> dict:
    """声音导演：把角色固定音色换成指定 preset（contract/v5/voices.json）。"""
    voices = _read(CONTRACT / "voices.json", {}) or {}
    presets = voices.get("presets") or {}
    if value not in presets:
        raise ValueError(f"未知 voice preset: {value}；可用: {sorted(presets)[:12]} ...")
    char_path = _char_file(character)
    data = _read(char_path, {})
    if not data:
        raise FileNotFoundError(f"角色不存在: {char_path}")
    old = (data.get("voice") or {}).get("voice_id")
    data.setdefault("voice", {})["voice_id"] = value
    data["voice"]["provider"] = presets[value].get("provider") or "breeze"
    _write(char_path, data)
    catalog = v5_catalog.load_catalog()
    affected_templates = sorted(
        tid for tid, tpl in (catalog.get("templates") or {}).items()
        if any(str(e.get("character")) == character
               for e in (tpl.get("cast") or []) if isinstance(e, dict)))
    return {"ok": True, "op": "set_voice", "template": template,
            "character": character, "old": old, "new": value,
            "scope": "global",  # 角色音色是全局资产；受影响模板全部列出
            "affected_templates": affected_templates,
            "affected_stages": AFFECTED["set_voice"],
            "rerun": " && ".join(
                f"python -m pipeline.agents run --template {tid} "
                f"--stages {','.join(AFFECTED['set_voice'])}"
                for tid in (affected_templates or [template])[:5])}


def set_pose(template: str, character: str, pose: str, enabled: bool = True) -> dict:
    """美术：按角色/姿态启停姿态资产（写入 contract/v6/art/pose_policy.json）。"""
    from .art import set_pose as _set_pose
    return _set_pose(None, {"character": character, "pose": pose,
                            "enabled": bool(enabled)})


def patch_editorial(template: str, patch: dict) -> dict:
    """剪辑师：修改 contract/v5/editorial/<template>.json（深合并）。"""
    path = CONTRACT / "editorial" / f"{template}.json"
    data = _read(path, {})
    if not data:
        raise FileNotFoundError(f"剪辑档案不存在: {path}")
    _write(path, merge_dict(data, patch or {}))
    return {"ok": True, "op": "patch_editorial", "template": template,
            "patch": patch, "affected_stages": AFFECTED["patch_editorial"],
            "rerun": f"python -m pipeline.agents run --template {template} "
                     f"--stages {','.join(AFFECTED['patch_editorial'])}"}


def patch_template(template: str, patch: dict) -> dict:
    """修改模板（风格/角色/场景/时长等），深合并；custom_tpl_* 落 custom 目录。"""
    path = CONTRACT / "templates" / f"{template}.json"
    if not path.is_file():
        custom = CONTRACT / "custom" / "templates" / f"{template}.json"
        if custom.is_file():
            path = custom
    data = _read(path, {})
    if not data:
        raise FileNotFoundError(f"模板不存在: {path}")
    _write(path, merge_dict(data, patch or {}))
    return {"ok": True, "op": "patch_template", "template": template,
            "patch": patch, "affected_stages": AFFECTED["patch_template"],
            "rerun": f"python -m pipeline.agents run --template {template}"}


def patch_workflow(template: str, patch: dict) -> dict:
    """修改工作流：{"enable":[...],"disable":[...],"params":{"shots":{...}}}

    也支持 {"stages":[...]} 整体替换。合同损坏（degraded）时拒绝改写，避免把默认栈覆盖进用户文件。
    """
    wf = load_workflow(template)
    if wf.get("degraded"):
        raise ValueError("工作流合同已损坏（degraded），请先 reset_workflow 或手工修复后再改")
    patch = patch or {}
    if "stages" in patch:
        wf["stages"] = patch["stages"]
    else:
        enable = {str(x) for x in (patch.get("enable") or [])}
        disable = {str(x) for x in (patch.get("disable") or [])}
        params = patch.get("params") or {}
        known = {str(s.get("id")) for s in wf.get("stages") or []}
        unknown = sorted((enable | disable | {str(k) for k in params}) - known)
        if unknown:   # 阶段名写错就报错，别静默空转（P3-7 / set_stage_param 空操作）
            raise ValueError(f"未知阶段: {unknown}；可用: {sorted(known)}")
        changed = False
        for stage in wf.get("stages") or []:
            sid = stage.get("id")
            if sid in enable and not stage.get("enabled", True):
                stage["enabled"] = True
                changed = True
            if sid in disable and stage.get("enabled", True):
                stage["enabled"] = False
                changed = True
            if sid in params:
                merged = merge_dict(stage.get("params") or {}, params[sid])
                if merged != (stage.get("params") or {}):
                    stage["params"] = merged
                    changed = True
        if "variables" in patch:
            wf["variables"] = merge_dict(wf.get("variables") or {}, patch["variables"])
            changed = True
        if not changed:      # 空操作不重写合同（不刷 mtime、不制造 git 噪声）
            return {"ok": True, "op": "patch_workflow", "template": template,
                    "path": str(WORKFLOW_DIR / f"{template}.json"),
                    "noop": True, "affected_stages": [],
                    "stages": [{"id": s.get("id"), "enabled": s.get("enabled", True)}
                               for s in wf.get("stages") or []]}
    path = save_workflow(wf)
    return {"ok": True, "op": "patch_workflow", "template": template,
            "path": str(path), "affected_stages": [],
            "stages": [{"id": s.get("id"), "enabled": s.get("enabled", True)}
                       for s in wf.get("stages") or []]}


def set_stage_param(template: str, stage: str, params: dict) -> dict:
    return patch_workflow(template, {"params": {stage: params or {}}})


def reset_workflow(template: str) -> dict:
    wf = default_workflow(template)
    path = save_workflow(wf)
    return {"ok": True, "op": "reset_workflow", "template": template,
            "path": str(path), "affected_stages": []}


def apply_op(template: str, op: str, **kwargs) -> dict:
    if op == "set_voice":
        return set_voice(template, str(kwargs.get("character") or ""),
                         str(kwargs.get("value") or ""))
    if op == "set_pose":
        value = kwargs.get("value")
        enabled = True
        if isinstance(value, dict):
            enabled = bool(value.get("enabled", True))
        elif isinstance(value, str):
            enabled = value.strip().lower() not in ("0", "false", "off", "no")
        return set_pose(template, str(kwargs.get("character") or ""),
                        str(kwargs.get("pose") or ""), enabled)
    if op == "patch_editorial":
        return patch_editorial(template, kwargs.get("json") or {})
    if op == "patch_template":
        return patch_template(template, kwargs.get("json") or {})
    if op == "patch_workflow":
        return patch_workflow(template, kwargs.get("json") or {})
    if op == "set_stage_param":
        return set_stage_param(template, str(kwargs.get("stage") or ""),
                               kwargs.get("json") or {})
    if op == "reset_workflow":
        return reset_workflow(template)
    raise ValueError(f"未知 op: {op}；可用: {sorted(AFFECTED)}")


#: 兼容别名（外部调用）
edit_op = apply_op

__all__ = ["apply_op", "edit_op", "AFFECTED", "set_voice", "patch_editorial",
           "patch_template", "patch_workflow", "set_stage_param", "reset_workflow"]
