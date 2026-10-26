# -*- coding: utf-8 -*-
"""V6 美术工具：generate_art / set_pose / list_art（art_director 智能体）。

- generate_art：调用 v5_art 生成/重建角色 AI 立绘（默认跳过已有资产，force 才重建）
- set_pose：设置角色姿态资产启用策略（contract/v6/art/pose_policy.json），characters_v5 运行时遵守
- list_art：列出角色/场景资产状态，供美术智能体决策
"""
from __future__ import annotations

import json
from pathlib import Path

from .. import config, utils

REPO = Path(config.REPO_ROOT)
POLICY = REPO / "contract" / "v6" / "art" / "pose_policy.json"


def _read_policy() -> dict:
    try:
        return json.loads(POLICY.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {"version": 1, "characters": {}}


def _write_policy(data: dict) -> None:
    POLICY.parent.mkdir(parents=True, exist_ok=True)
    tmp = POLICY.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(POLICY)


def pose_allowed(character_id: str, pose: str) -> bool:
    """运行时查询：某角色某姿态是否允许用姿态资产（默认允许）。"""
    pos = str(pose or "front").lower()
    if pos == "front":
        return True
    data = _read_policy().get("characters") or {}
    entry = data.get(str(character_id)) or {}
    if not isinstance(entry, dict):
        return True
    if entry.get("disabled") and pos in entry["disabled"]:
        return False
    if entry.get("enabled") and pos not in entry["enabled"]:
        return False
    return True


def generate_art(ctx, params: dict | None = None) -> dict:
    from .. import v5_art
    params = params or {}
    cid = str(params.get("character") or "").strip()
    if not cid:
        raise ValueError("generate_art 需要 character")
    kind = str(params.get("kind") or "character")
    if kind != "character":
        raise ValueError("当前 generate_art 仅支持 character（场景走 gen_v5_scene_art.py）")
    force = bool(params.get("force", False))
    res = v5_art.generate_character_art(cid, force=force)
    out = {"ok": True, "kind": "character", "character": cid,
           "force": force, "result": res if isinstance(res, dict) else str(res)}
    # 可选：同时生成姿态变体
    if params.get("poses"):
        poses = [str(p) for p in (params.get("poses") or [])]
        pose_res = {}
        for pose in poses:
            try:
                pose_res[pose] = v5_art.generate_character_pose(cid, pose, force=force)
            except Exception as exc:  # noqa: BLE001
                pose_res[pose] = {"ok": False, "error": str(exc)}
        out["poses"] = pose_res
    return out


def set_pose(ctx, params: dict | None = None) -> dict:
    params = params or {}
    cid = str(params.get("character") or "").strip()
    pose = str(params.get("pose") or "").strip().lower()
    if not cid or not pose:
        raise ValueError("set_pose 需要 character + pose")
    enabled = bool(params.get("enabled", True))
    data = _read_policy()
    chars = data.setdefault("characters", {})
    entry = chars.setdefault(cid, {})
    if enabled:
        entry.pop("disabled", None)
        enabled_list = list(entry.get("enabled") or [])
        if pose not in enabled_list:
            enabled_list.append(pose)
        entry["enabled"] = enabled_list
    else:
        entry["disabled"] = sorted(set(list(entry.get("disabled") or []) + [pose]))
    data["version"] = int(data.get("version") or 1)
    data["updated_at"] = utils.now_iso() if hasattr(utils, "now_iso") else ""
    _write_policy(data)
    return {"ok": True, "op": "set_pose", "character": cid, "pose": pose,
            "enabled": enabled, "policy": str(POLICY),
            "affected_stages": ["shots", "assemble", "storyboard", "qc", "publish"]}


def list_art(ctx, params: dict | None = None) -> dict:
    assets = REPO / "assets" / "v5" / "characters"
    rows = []
    for cdir in sorted(p for p in assets.iterdir() if p.is_dir()):
        cid = cdir.name
        rows.append({
            "character": cid,
            "portrait": (cdir / "portrait.png").is_file(),
            "front": (cdir / "front.png").is_file(),
            "cutout": (cdir / "cutout.png").is_file(),
            "point": (cdir / "cutout_point.png").is_file(),
            "think": (cdir / "cutout_think.png").is_file(),
        })
    return {"ok": True, "count": len(rows), "characters": rows,
            "policy": _read_policy()}


__all__ = ["generate_art", "set_pose", "list_art", "pose_allowed", "POLICY"]
