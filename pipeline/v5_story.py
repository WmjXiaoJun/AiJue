# -*- coding: utf-8 -*-
"""V5 故事板规划：V5 模板（acts）→ 逐镜计划（镜头/场景/角色/机位/台词）。

与 V3/V4 的区别：
- V5 模板以 `acts` 描述叙事单元（场景 + 机位 + 台词 + 角色 + 时长权重），不再依赖单一 stage plate；
- 每 act 可含多句台词（`lines: [{"speaker": role, "text": ...}]`），一句一镜；
- 无台词的 act → 1 个 b-roll 静默镜（2-4s），用于场景展示/转场；
- 时长最终由 TTS 语音自然时长驱动（见 render_v5.pace_plan），与 V4 一致；
- 兼容从 `llm_scripts/{template_id}.json` 取剧本（若模板未内嵌 lines）。
"""
from __future__ import annotations

import json
from pathlib import Path

from . import config, utils

LOG = __import__("logging").getLogger("pipeline")

DEFAULT_TARGET = 40.0
MIN_SHOT = 1.6
MAX_SHOT = 12.0
BROLL_DURATION = 2.6
LEAD_SEC = 0.10
TAIL_SEC = 0.30


class V5StoryError(RuntimeError):
    pass


def load_script(template_id: str, script=None) -> dict:
    """读取剧本：显式 dict > llm_scripts/{id}.json > 空。"""
    if isinstance(script, dict):
        return script
    path = Path(config.REPO_ROOT) / "llm_scripts" / f"{template_id}.json"
    if path.is_file():
        data = utils.read_json(path)
        if isinstance(data, dict):
            return data
    return {}


def _script_lines(script: dict) -> list[dict]:
    """兼容 llm_scripts 常见结构：{lines:[{speaker,text}]} / {segments:[{lines:[...]}]}。"""
    lines: list[dict] = []
    if not isinstance(script, dict):
        return lines
    if isinstance(script.get("lines"), list):
        for item in script["lines"]:
            if isinstance(item, dict) and str(item.get("text") or "").strip():
                lines.append({"speaker": str(item.get("speaker") or ""),
                              "text": str(item["text"]).strip()})
    for seg in script.get("segments") or []:
        if not isinstance(seg, dict):
            continue
        for item in seg.get("lines") or []:
            if isinstance(item, dict) and str(item.get("text") or "").strip():
                lines.append({"speaker": str(item.get("speaker") or ""),
                              "text": str(item["text"]).strip()})
    return lines


def _cast_roles(template: dict) -> dict[str, dict]:
    """role → {character, name, voice...}。"""
    out: dict[str, dict] = {}
    for item in template.get("cast") or []:
        if not isinstance(item, dict):
            continue
        role = str(item.get("role") or "").strip()
        char = str(item.get("character") or item.get("character_id") or "").strip()
        if role and char:
            out[role] = {"role": role, "character": char,
                         "name": str(item.get("name") or char)}
    return out


def _resolve_role(speaker: str, cast: dict[str, dict]) -> str | None:
    s = str(speaker or "").strip()
    if not s:
        return None
    if s in cast:
        return s
    low = s.lower()
    for role in cast:
        if low == role.lower():
            return role
    # speaker 用角色 id 的情况
    for role, item in cast.items():
        if low == str(item.get("character")).lower():
            return role
    return None


def _act_lines(act: dict, script_lines: list[dict], cast: dict[str, dict],
               used: list[int]) -> list[dict]:
    """取本 act 的台词：
    1) act.lines = [{speaker,text}]（扩展写法）；
    2) act.text（单句文本，speaker 取 act.line / line_speaker / speaker）；
    3) act.line 是 cast role → 从剧本按顺序借下一句（speaker=role）；
    4) 无 → 空（B-roll）。
    """
    own: list[dict] = []
    if isinstance(act.get("lines"), list):
        for item in act["lines"]:
            if isinstance(item, dict) and str(item.get("text") or "").strip():
                own.append({"speaker": str(item.get("speaker") or ""),
                            "text": str(item["text"]).strip()})
    if own:
        return own
    text = str(act.get("text") or "").strip()
    if text:
        role = str(act.get("line") or act.get("line_speaker") or act.get("speaker") or "")
        if role not in cast:
            role = next(iter(cast), "")
        return [{"speaker": role, "text": text}]
    line = act.get("line")
    if isinstance(line, str) and line.strip() and line.strip() in cast:
        if script_lines and used[0] < len(script_lines):
            item = script_lines[used[0]]
            used[0] += 1
            return [{"speaker": line.strip(), "text": item.get("text", "")}]
    elif isinstance(act.get("line"), str) and act["line"].strip() and not cast.get(act["line"]):
        # act.line 既不是 role 也不是空：当作台词文本
        role = next(iter(cast), "")
        return [{"speaker": role, "text": act["line"].strip()}]
    # 最后回退：按顺序借剧本（不指定 speaker）
    if script_lines and used[0] < len(script_lines):
        item = script_lines[used[0]]
        used[0] += 1
        return [{"speaker": str(item.get("speaker") or ""), "text": item.get("text", "")}]
    return []


def build_plan(template: dict, target_duration: float | None = None, *,
               script: dict | None = None, seed: int = 20261101) -> dict:
    """模板 → 计划（未配音版）。返回 {episode, shots, meta}。

    shots[i] = {
      id, no, type("dialogue"|"broll"), role, character, speaker_name,
      scene, camera, duration(预估), line, act_id, act_name, packaging
    }
    """
    if not isinstance(template, dict) or not template.get("id"):
        raise V5StoryError("模板非法：缺少 id")
    tid = str(template["id"])
    target = float(target_duration or template.get("target_duration") or DEFAULT_TARGET)
    acts = [a for a in (template.get("acts") or []) if isinstance(a, dict)]
    if not acts:
        raise V5StoryError(f"V5 模板 {tid} 缺少 acts")
    cast = _cast_roles(template)
    script_data = load_script(tid, script)
    script_lines = _script_lines(script_data)
    used = [0]

    shots: list[dict] = []
    no = 0
    for act in acts:
        act_id = str(act.get("id") or f"act{no + 1}")
        act_name = str(act.get("name") or act_id)
        scene = str(act.get("scene") or "")
        camera_raw = act.get("camera")
        if isinstance(camera_raw, dict):
            camera = dict(camera_raw)
        else:
            camera = {"type": str(camera_raw or "push_in"),
                      "strength": float(act.get("camera_strength") or 0.1)}
        chars = [str(c) for c in (act.get("characters") or []) if str(c).strip()]
        lines = _act_lines(act, script_lines, cast, used)
        is_broll = bool(act.get("broll")) or (not lines and act.get("broll", True))
        if lines:
            for line in lines:
                role = _resolve_role(line.get("speaker") or "", cast)
                if role is None and cast:
                    role = next(iter(cast))
                no += 1
                shots.append({
                    "id": f"v5_{no:03d}", "no": no, "type": "dialogue",
                    "act_id": act_id, "act_name": act_name,
                    "role": role,
                    "character": (cast.get(role) or {}).get("character"),
                    "speaker_name": (cast.get(role) or {}).get("name", ""),
                    "scene": scene, "camera": camera,
                    "characters": chars or ([role] if role else []),
                    "duration": float(act.get("duration_hint") or 5.2),
                    "line": line.get("text"),
                })
        # 无台词 / 显式 b-roll：加一个静默镜
        if not lines and (is_broll or act.get("broll")):
            no += 1
            shots.append({
                "id": f"v5_{no:03d}", "no": no, "type": "broll",
                "act_id": act_id, "act_name": act_name,
                "role": None, "character": None, "speaker_name": "",
                "scene": scene, "camera": camera,
                "characters": chars,
                "duration": float(act.get("duration_hint") or BROLL_DURATION),
                "line": "",
            })

    if not shots:
        raise V5StoryError(f"V5 模板 {tid} 未规划出任何镜头")
    total = round(sum(float(s["duration"]) for s in shots), 3)
    plan = {
        "episode": {
            "id": tid,
            "label": str(template.get("label") or tid),
            "style": str(template.get("style") or ""),
            "resolution": "1280x720",
            "fps": 25,
            "target_duration": target,
            "total_duration": total,
            "acts": acts,
            "segments": [{"id": str(a.get("id") or ""),
                          "name": str(a.get("name") or a.get("id") or ""),
                          "duration": 0.0} for a in acts],
        },
        "shots": shots,
        "meta": {"template": tid, "style": str(template.get("style") or ""),
                 "target_duration": target, "seed": int(seed),
                 "cast": cast, "script_source": "inline" if script_lines else "template",
                 "h3_shot_count": 0, "speech_driven": True},
    }
    return plan


def plan_duration(plan: dict) -> float:
    return round(sum(float(s.get("duration") or 0.0) for s in plan.get("shots") or []), 3)


def stage_targets(shots: list[dict], total: float) -> dict[str, tuple[float, float]]:
    """每镜在成片中的绝对起止时间。"""
    out: dict[str, tuple[float, float]] = {}
    cursor = 0.0
    for shot in shots:
        dur = float(shot.get("duration") or 0.0)
        out[str(shot.get("id"))] = (round(cursor, 3), round(cursor + dur, 3))
        cursor += dur
    return out
