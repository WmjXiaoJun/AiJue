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

#: P1-15：模板幕数少时 build_plan 会远短于 target（`custom_holo_talk --duration 30`
#: 实测只出 7.8s）。补足策略：先拉长空镜（无配音，安全），再插空镜；两者都有上限，
#: 绝不为了凑时长把单镜拉成 PPT。
PAD_RATIO = 0.95          # 达到 target 的 95% 就算达标
PAD_BROLL_MAX = 6.5       # 单个空镜最长（秒）
PAD_SHOT_MAX = 4.5        # 新插空镜的单镜时长（秒）
PAD_MAX_EXTRA = 40        # 最多插多少个空镜（防止病态模板无限膨胀）


class V5StoryError(RuntimeError):
    pass


def _pad_to_target(shots: list[dict], target: float) -> tuple[list[dict], dict]:
    """把过短的镜头表补到 target（P1-15）。

    两段式：① 拉长已有空镜到 `PAD_BROLL_MAX`；② 还不够就按空镜模板插新镜
    （带 `pad: true` 与换过的运镜，便于事后识别"这是补的"）。
    台词镜不动：`voiced` 阶段会按真实语音重算它们的时长，现在拉长也会被覆盖。
    """
    total = round(sum(float(s.get("duration") or 0) for s in shots), 3)
    info = {"target": round(float(target), 3), "before": total,
            "extended": 0, "added": 0, "after": total}
    need = round(float(target) * PAD_RATIO - total, 3)
    if need <= 0.05:
        return shots, info

    for shot in shots:
        if need <= 0.05:
            break
        if str(shot.get("line") or "").strip():
            continue
        cur = float(shot.get("duration") or 0)
        room = min(PAD_BROLL_MAX - cur, need)
        if room > 0.05:
            shot["duration"] = round(cur + room, 3)
            need = round(need - room, 3)
            info["extended"] += 1

    if need > 0.05:
        brolls = [s for s in shots if not str(s.get("line") or "").strip()] or shots
        idx = 0
        pads: list[dict] = []
        while need > 0.05 and idx < PAD_MAX_EXTRA:
            base = brolls[idx % len(brolls)]
            dur = round(min(PAD_SHOT_MAX, need), 3)
            clone = dict(base)
            clone["id"] = f"pad_{idx + 1:03d}"
            clone["line"] = ""
            clone["duration"] = dur
            clone["pad"] = True
            clone["camera"] = _vary_camera(base.get("camera"), idx + 1)
            pads.append(clone)
            need = round(need - dur, 3)
            idx += 1
        # 插在最后一个台词镜之后（把收尾留给原本的最后一镜），没有台词镜就追加
        last_line = max((i for i, s in enumerate(shots)
                         if str(s.get("line") or "").strip()), default=len(shots) - 1)
        shots = shots[:last_line + 1] + pads + shots[last_line + 1:]
        info["added"] = len(pads)

    for i, shot in enumerate(shots, start=1):
        shot["no"] = i
    info["after"] = round(sum(float(s.get("duration") or 0) for s in shots), 3)
    if info["added"] or info["extended"]:
        LOG.info("时长补齐：%.1fs → %.1fs（目标 %.1fs；拉长 %d 镜 / 新增 %d 镜）",
                 info["before"], info["after"], info["target"],
                 info["extended"], info["added"])
    return shots, info


def load_script(template_id: str, script=None) -> dict:
    """读取剧本：显式 dict > LLM 剧本文件 > 空。

    LLM（DeepSeek）生成的剧本落在 `outputs/llm_scripts/<id>.json`（写入方是后端
    `llm` 路由），而本函数旧实现只读 `<repo>/llm_scripts/` —— 那个目录根本不存在，
    于是 **渲染永远用不到 LLM 剧本**（`storyboard_v3` 走的是 outputs 路径，所以 V3 栈
    能用到）。现在按 `outputs/llm_scripts` → `<repo>/llm_scripts` 顺序找，
    兼容 `_5min` / `_demo` 后缀，同名时取**修改时间最新**的一份。
    """
    if isinstance(script, dict):
        return script
    repo = Path(config.REPO_ROOT)
    outputs = Path(config.OUTPUTS_ROOT)
    names = [f"{template_id}.json"]
    stem = str(template_id or "")
    for suffix in ("_5min", "_demo"):
        if stem.endswith(suffix):
            names.append(f"{stem[:-len(suffix)]}.json")
        else:
            names.append(f"{stem}{suffix}.json")
    candidates: list[Path] = []
    for base in (outputs / "llm_scripts", repo / "llm_scripts"):
        for name in names:
            p = base / name
            if p.is_file():
                candidates.append(p)
    if not candidates:
        return {}
    newest = max(candidates, key=lambda p: p.stat().st_mtime)
    data = utils.read_json(newest)
    if isinstance(data, dict) and data:
        LOG.debug("剧本来源: %s", newest)
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
    """把台本里的说话人（role / character id / **中文名**）解析成 cast role。

    R6 修复：DeepSeek 台本写的是「老周 / 阿栗 / 大魏」，而 cast 里是
    host / guest_a / guest_b + `name`。旧实现只比 role 和 character id，中文名一律
    匹配不上 → 52 句全落到 `next(iter(cast))`（host），整片只有一个音色。
    """
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
    # speaker 用中文名（老周/阿栗/大魏），或带标注（"老周（主持人）"）
    for role, item in cast.items():
        name = str(item.get("name") or "").strip()
        if not name:
            continue
        if low == name.lower() or low.startswith(name.lower()):
            return role
    # 再宽松一档：名字互相包含（"司马迁" vs "司马迁（史官）"）
    for role, item in cast.items():
        name = str(item.get("name") or "").strip()
        if name and (name in s or s in name):
            return role
    return None


def _spread_lines(acts: list[dict], script_lines: list[dict]) -> list[list[dict]]:
    """把剧本台词**按幕数均分**（R5）。

    旧行为：`_act_lines` 每个 act 最多取 1 句剧本，且模板 act 里自带 `text` 时直接
    用 act 的 1 句 —— 于是 5 分钟节目只会有 ~9 句台词，剩下全是空镜。
    `expand_3d_shots` 又把新镜的 `line` 清空 → 成片 Only 7 条字幕（classics 实测）。
    这里让「有剧本」时剧本按幕数铺开：52 句 / 9 幕 → 每幕 5–6 句，一句一个台词镜。
    """
    n = max(1, len(acts))
    total = len(script_lines)
    base, extra = divmod(total, n)
    buckets: list[list[dict]] = []
    i = 0
    for k in range(n):
        take = base + (1 if k >= n - extra else 0)
        buckets.append(script_lines[i:i + take])
        i += take
    return buckets


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
               script: dict | None = None, seed: int = 20261101,
               prefer_script: bool | None = None) -> dict:
    """模板 → 计划（未配音版）。返回 {episode, shots, meta}。

    shots[i] = {
      id, no, type("dialogue"|"broll"), role, character, speaker_name,
      scene, camera, duration(预估), line, act_id, act_name, packaging
    }

    prefer_script（R5）：
      - None（默认）→ 看模板字段 `script_mode`：`"script"` 时用剧本，否则沿用
        "act 自带 text 优先"的旧行为（保护既有模板的演示台词）；
      - True → **剧本驱动台词**：act 只提供 scene/camera/characters，台词按幕数
        均分铺开（一句一镜），这是「5 分钟微综艺」有足够口播的前提；
      - False → 强制旧行为。
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
    if prefer_script is None:
        script_mode = str(template.get("script_mode") or "").strip().lower()
        prefer_script = script_mode == "script"
    buckets = _spread_lines(acts, script_lines) if (prefer_script and script_lines) else None

    shots: list[dict] = []
    no = 0
    for act_idx, act in enumerate(acts):
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
        if buckets is not None:
            lines = [{"speaker": str(item.get("speaker") or ""),
                      "text": str(item.get("text") or "").strip()}
                     for item in buckets[act_idx] if str(item.get("text") or "").strip()]
        else:
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

    # P1-15：幕数少 / duration_hint 短时按 target 补齐（台词镜不动，只补空镜）
    shots, pad_info = _pad_to_target(shots, target)

    total = round(sum(float(s["duration"]) for s in shots), 3)
    passes = 1
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
                 "pad": pad_info,
                 "cast": cast, "script_source": "inline" if script_lines else "template",
                 "h3_shot_count": 0, "speech_driven": True, "passes": 1},
    }
    return plan


def _vary_camera(camera, pass_no: int) -> dict:
    """多遍扩写时换运镜：同一条台词第二遍不再用同一机位。"""
    cam = dict(camera or {})
    kinds = ["push_in", "pull_out", "pan_left", "pan_right", "tilt_up", "handheld"]
    base = str(cam.get("type") or "push_in")
    try:
        start = kinds.index(base)
    except ValueError:
        start = 0
    cam["type"] = kinds[(start + pass_no - 1) % len(kinds)]
    strength = float(cam.get("strength") or 0.1)
    cam["strength"] = round(min(0.35, max(0.05, strength + 0.02 * ((pass_no % 3) - 1))), 3)
    return cam

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
