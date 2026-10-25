# -*- coding: utf-8 -*-
"""storyboard_v3：模板 + LLM 台本 → v3 shot 序列（render.py 兼容）。

职责（docs/V3_INTERFACES.md §5）：
- 模板 ``segments[].shots[]`` → 30-45s（demo）或 300s（5 分钟版）shot 序列；
- H3 单镜固定 5.17s（124 帧 @24fps，H3 最小帧数），片头卡 2.0s + 片尾卡 2.5s；
- 台词自动读 ``outputs/llm_scripts/{template_id}.json``，按脚本顺序填到 shot.line；
  说话人中文名（老周/阿栗/大魏）→ 模板 cast 的 role / character；
- 每镜输出 render.py 兼容字段（id/no/type/duration/speaker/speaker_name/line/
  visual/lower_third/sfx/shot_plan）+ v3 扩展字段（role/v3/h3_mode/action/subject_role）；
- type 映射：audio_ref → host_to_camera / shot_reverse（按角色）；fl2v → b_roll/reaction；
  卡片 → title_card / end_card。

用法：
  python -m pipeline.storyboard_v3 --template tonight_show --duration 30 --out work/episode.json
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

from . import config, template_v3

REPO_ROOT = Path(__file__).resolve().parents[1]
LLM_SCRIPTS_DIR = REPO_ROOT / "outputs" / "llm_scripts"

# ---- H3 单镜规格 ----------------------------------------------------------
H3_FPS = 24
H3_MIN_FRAMES = 124                     # H3 最小 124 帧
H3_SHOT_DURATION = round(H3_MIN_FRAMES / H3_FPS, 2)   # 5.17s
TITLE_CARD_DURATION = 2.0
END_CARD_DURATION = 2.5
DEFAULT_SEED = 20261026
DEFAULT_TARGET_DURATION = 30.0
CARD_TYPES = ("title_card", "end_card", "meme_card")
DROP_SHOT_TYPES = ("b_roll", "reaction")
# Lead 实测决策：v3 正片非卡片镜统一 audio_ref（05_ref2va）；"fl2v" 保留代码路径
H3_MODE_POLICY = "audio_ref"

# 台词说话人中文名 → contract/characters id（合同角色池只有 3 人）
SPEAKER_ALIASES = {
    "老周": "laozhou", "周": "laozhou", "laozhou": "laozhou",
    "阿栗": "ali", "栗": "ali", "ali": "ali",
    "大魏": "dawei", "魏": "dawei", "dawei": "dawei",
}


# ---------------------------------------------------------------------------
# 台本
# ---------------------------------------------------------------------------
def resolve_script_path(template_id: str) -> Path | None:
    """outputs/llm_scripts/{id}.json（兼容 _5min/_demo 后缀）。"""
    base = str(template_id or "").strip()
    names = [base]
    for suffix in ("_5min", "_demo", "_v3"):
        if base.endswith(suffix):
            names.append(base[: -len(suffix)])
        else:
            names.append(base + suffix)
    for name in names:
        if not name:
            continue
        p = LLM_SCRIPTS_DIR / f"{name}.json"
        if p.is_file():
            return p
    return None


def load_script(template_id: str, script=None) -> dict | None:
    """script 参数：dict / 路径字符串 / None（按模板 id 自动找）。"""
    if isinstance(script, dict):
        return script
    if script is not None:
        p = Path(script)
        if p.is_file():
            return json.loads(p.read_text(encoding="utf-8"))
        raise FileNotFoundError(f"台本不存在: {script}")
    p = resolve_script_path(template_id)
    if p is None:
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except ValueError:
        return None


def script_lines(script: dict | None) -> list[dict]:
    """把台本 segments[].lines[] 展平成 [{speaker,text,segment_name,segment_index}]。"""
    out: list[dict] = []
    seg_index = 0
    for seg in (script or {}).get("segments") or []:
        if not isinstance(seg, dict):
            continue
        has_line = False
        for line in seg.get("lines") or []:
            if not isinstance(line, dict):
                continue
            text = str(line.get("text") or "").strip()
            if not text:
                continue
            has_line = True
            out.append({
                "speaker": str(line.get("speaker") or "").strip(),
                "text": text,
                "segment_name": str(seg.get("name") or ""),
                "segment_index": seg_index,
            })
        if has_line:
            seg_index += 1
    if not out and str((script or {}).get("script") or "").strip():
        # 兜底：只有 script 纯文本时，按「名字：台词」行解析
        for raw in str(script.get("script") or "").splitlines():
            raw = raw.strip()
            if not raw or raw.startswith("["):
                continue
            if "：" in raw:
                name, _, text = raw.partition("：")
                if text.strip():
                    out.append({"speaker": name.strip(), "text": text.strip(),
                                "segment_name": "", "segment_index": 0})
            elif ":" in raw:
                name, _, text = raw.partition(":")
                if text.strip():
                    out.append({"speaker": name.strip(), "text": text.strip(),
                                "segment_name": "", "segment_index": 0})
    return out


def script_segment_count(script: dict | None) -> int:
    """台本段数（开场/正片/结尾 → 3；纯文本兜底 → 1）。"""
    count = 0
    for seg in (script or {}).get("segments") or []:
        if not isinstance(seg, dict):
            continue
        if any(str((line or {}).get("text") or "").strip()
               for line in seg.get("lines") or [] if isinstance(line, dict)):
            count += 1
    return max(1, count) if script_lines(script) else 1


# ---------------------------------------------------------------------------
# cast / 资产
# ---------------------------------------------------------------------------
def build_assets(template: dict) -> list[dict]:
    """模板 cast → render.py 兼容 assets（id=角色资产 id/character）。"""
    members = template_v3.cast_for(template)
    assets: list[dict] = []
    used: set[str] = set()
    for member in members:
        character = str(member.get("character_id") or member.get("character") or "").strip()
        role = str(member.get("role") or "").strip()
        asset_id = character or role
        if not asset_id:
            continue
        if asset_id in used:
            asset_id = f"{asset_id}_{role or len(assets)}"
        used.add(asset_id)
        images = []
        for cand in (REPO_ROOT / "outputs" / "characters" / character / "h3_portrait.png",
                     REPO_ROOT / "outputs" / "characters" / character / "portrait_from_concept.png"):
            if cand.is_file():
                images.append("/media/characters/" + character + "/" + cand.name)
                break
        assets.append({
            "id": asset_id,
            "episode_id": "",
            "role": role,
            "type": "character",
            "character": character,
            "character_id": character,
            "name": str(member.get("name") or role),
            "title": str(member.get("title") or ""),
            "persona": str(member.get("persona") or ""),
            "voice": str(member.get("voice") or config.DEFAULT_VOICE),
            "accent_color": str(member.get("accent_color") or config.ACCENT_DEFAULT),
            "block": str(member.get("block") or ""),
            "wardrobe": str(member.get("wardrobe") or ""),
            "images": images,
            "locked": True,
        })
    return assets


def _cast_roles(template: dict, assets: list[dict]) -> list[dict]:
    """[{role, character, asset_id, name}]，顺序与 cast 一致。"""
    out = []
    by_role = {}
    members = template_v3.cast_for(template)
    for member, asset in zip(members, assets):
        role = str(member.get("role") or "").strip()
        by_role[role] = {
            "role": role,
            "character": str(member.get("character_id") or ""),
            "asset_id": asset["id"],
            "name": asset["name"],
            "title": asset["title"],
        }
        out.append(by_role[role])
    return out


def _role_is_host(role: str) -> bool:
    """host role 兼容 host / host_detective / host_xxx（模板差异）。"""
    return str(role or "").strip().lower().startswith("host")


def speaker_map(template: dict, assets: list[dict]) -> dict:
    """中文名/character id → {role, asset_id, name}；附带兜底角色池。"""
    roles = _cast_roles(template, assets)
    chars = template_v3.known_characters()
    name_to_char = {}
    for cid, data in chars.items():
        nm = str(data.get("name") or "").strip()
        if nm:
            name_to_char[nm] = cid
    name_to_char.update(SPEAKER_ALIASES)
    by_character = {}
    for item in roles:
        by_character.setdefault(item["character"], item)
    hosts = [r for r in roles if _role_is_host(r["role"])]
    guests = [r for r in roles if not _role_is_host(r["role"])]
    return {
        "roles": roles,
        "by_role": {r["role"]: r for r in roles},
        "by_character": by_character,
        "name_to_char": name_to_char,
        "hosts": hosts,
        "guests": guests,
    }


def resolve_speaker(name: str, smap: dict) -> dict | None:
    """台本说话人（中文名/character id）→ cast 角色（确定性，不随镜头轮转）。

    - 说话人在 cast 里 → 直接命中；
    - 不在 cast（如 hot_ones/slow_life 只有 2 人、台本却有「大魏」）：
      laozhou → host 角色；ali → 第一个嘉宾角色；dawei → 最后一个嘉宾角色；
      其余未知 → 第一个嘉宾角色。保证同一说话人在整条片里落位一致。
    """
    raw = str(name or "").strip()
    roles = smap["roles"]
    if not roles:
        return None
    cid = smap["name_to_char"].get(raw) or (raw if raw in
                                             {r["character"] for r in roles} else "")
    if cid and cid in smap["by_character"]:
        return smap["by_character"][cid]
    if raw:
        for item in roles:
            if raw == item["name"] or raw == item["role"]:
                return item
    hosts = smap["hosts"]
    guests = smap["guests"]
    if cid == "laozhou" or "老周" in raw:
        return (hosts or roles)[0]
    if cid == "dawei":
        return (guests or roles)[-1]
    if cid == "ali":
        return (guests or roles)[0]
    return (guests or roles)[0]


# ---------------------------------------------------------------------------
# shot 映射
# ---------------------------------------------------------------------------
def normalize_h3_mode(raw_mode, line: str) -> str:
    """模板 h3_mode → audio_ref|fl2v。

    - fl2v → fl2v（首尾帧）
    - audio_ref：仅在有台词时保留（H3 音驱动口型需要音频），无台词降为 fl2v
    - 历史模板里的 i2v 视为 fl2v（仅首帧）
    """
    mode = str(raw_mode or "").strip().lower()
    if mode == "fl2v":
        return "fl2v"
    if mode == "audio_ref":
        return "audio_ref" if str(line or "").strip() else "fl2v"
    if mode in ("i2v", "2v", "img2video"):
        return "fl2v"
    return "audio_ref" if str(line or "").strip() else "fl2v"


def shot_type_for(h3_mode: str, role: str, intent: str = "", size: str = "") -> str:
    """v3 h3_mode → render.py 兼容 type。"""
    mode = str(h3_mode or "")
    if mode == "audio_ref":
        return "host_to_camera" if _role_is_host(role) else "shot_reverse"
    intent_l = str(intent or "").lower()
    size_l = str(size or "").lower()
    if "reaction" in intent_l or "close" in size_l:
        return "reaction"
    return "b_roll"


def _short_visual(segment_name: str, size: str, movement: str, action: str = "") -> str:
    bits = [b for b in (segment_name, size, movement, action) if b]
    text = " · ".join(bits)
    return text if len(text) <= 26 else text[:25] + "…"


def shot_count_for(target_duration: float, include_cards: bool = True) -> int:
    """shots 数 = round((target - cards)/5.17)（≥1）。"""
    cards = (TITLE_CARD_DURATION + END_CARD_DURATION) if include_cards else 0.0
    n = int(round((float(target_duration) - cards) / H3_SHOT_DURATION))
    return max(1, n)


def plan_duration(plan: dict) -> float:
    """计划总时长（所有 shot duration 之和）。"""
    return round(sum(float(s.get("duration") or 0.0)
                     for s in (plan or {}).get("shots") or []), 3)


def _spread_indices(count: int, total: int) -> list[int]:
    """从 0..total-1 里均匀取 count 个下标（必含首尾）。"""
    if count <= 0 or total <= 0:
        return []
    if count >= total:
        return list(range(total))
    if count == 1:
        return [0]
    idxs = sorted({int(round(j * (total - 1) / (count - 1)))
                   for j in range(count)})
    cursor = 0
    while len(idxs) < count:  # 极端情况下补齐
        if cursor not in idxs:
            idxs.append(cursor)
        cursor += 1
    return sorted(idxs)[:count]


def _script_group_for_shot(shot: dict, seg_index_by_id: dict,
                           n_template_segs: int, n_script_segs: int) -> int:
    """模板段 → 台本段映射：首段→开场、末段→结尾、中间段→正片。"""
    if n_script_segs <= 1 or n_template_segs <= 1:
        return 0
    si = int(seg_index_by_id.get(str(shot.get("segment_id") or ""), 0))
    if si <= 0:
        return 0
    if si >= n_template_segs - 1:
        return n_script_segs - 1
    middle_tpl = max(1, n_template_segs - 2)
    middle_script = n_script_segs - 2
    if middle_script <= 0:
        return min(1, n_script_segs - 1)
    if middle_tpl == 1:
        return 1
    frac = (si - 1) / max(1, middle_tpl - 1)
    return min(n_script_segs - 1, 1 + int(round(frac * (middle_script - 1))))


def _spread_pick(items: list, count: int) -> list:
    if count <= 0 or not items:
        return []
    return [items[i] for i in _spread_indices(count, len(items))]


# ---- 台词/镜头时长匹配（避免 5.17s 镜头装不下长句被裁切）---------------
# edge-tts 中文语速实测 ≈0.18s/字；5.17s 镜 - 0.3s 余量，atempo 上限 1.45
# → TTS 时长 ≤6.6s 都能被压进镜头（不裁切），超过就按标点切成多段跨镜。
DEMO_LINE_EST_MAX = 6.6


def estimate_tts_sec(text: str) -> float:
    """粗估中文 TTS 时长（脚本期用，实际以 tts_engine 为准）。"""
    return round(0.35 + 0.185 * len(str(text or "").strip()), 2)


def split_text_budget(text: str, max_est: float = DEMO_LINE_EST_MAX) -> list[str]:
    """按标点把长句切成若干段（每段估时 ≤max_est，不裁台词内容）。"""
    text = str(text or "").strip()
    if not text:
        return []
    if estimate_tts_sec(text) <= max_est:
        return [text]
    pieces = [p for p in re.findall(r"[^，。！？；：、,.!?;:\n]+[，。！？；：、,.!?;:\n]*", text) if p.strip()]
    if not pieces:
        pieces = [text]
    chunks: list[str] = []
    cur = ""
    for piece in pieces:
        if cur and estimate_tts_sec(cur + piece) > max_est:
            chunks.append(cur)
            cur = piece
        else:
            cur += piece
        while estimate_tts_sec(cur) > max_est and len(cur) > 8:
            cut = max(6, len(cur) // 2)
            chunks.append(cur[:cut])
            cur = cur[cut:]
    if cur:
        chunks.append(cur)
    return [c for c in chunks if c.strip()]


def _make_line_chunk(line: dict, text: str, line_index: int,
                     part_index: int, parts: int) -> dict:
    chunk = dict(line)
    chunk["text"] = text
    chunk["_line_index"] = line_index
    chunk["_part_index"] = part_index
    chunk["_parts"] = parts
    return chunk


def _chunk_lines(lines: list[dict],
                 max_est: float = DEMO_LINE_EST_MAX) -> list[dict]:
    out: list[dict] = []
    for idx, line in enumerate(lines):
        parts = split_text_budget(line.get("text"), max_est)
        for pi, part in enumerate(parts):
            out.append(_make_line_chunk(line, part, idx, pi, len(parts)))
    return out


def _assign_lines(shots: list[dict], lines: list[dict], *,
                  n_script_segs: int = 1,
                  seg_index_by_id: dict | None = None,
                  n_template_segs: int = 1) -> None:
    """把台本台词填到非卡片镜头（对标模板-author 内容适配核查 + 语速适配）。

    规则：
    - 统一 audio_ref 策略下所有正文镜都能承载台词（无台词镜走静音参考）；
    - demo（镜头 ≤ 台词）：每镜一句、按台本顺序；**首镜=台本首句、末镜=台本末句**；
      长句先按标点安全切分，保证 TTS 能被 atempo 压进 5.17s 镜头（不裁切台词）；
    - 5 分钟版（镜头 > 台词）：全部台词切分后均匀铺开；首尾句同样保护；
      其余镜头静音（仍是 H3 audio_ref 重绘）。
    """
    seg_index_by_id = seg_index_by_id or {}
    bodies = [s for s in shots if s.get("type") not in CARD_TYPES]
    if not bodies or not lines:
        return
    n_shots, n_lines = len(bodies), len(lines)
    last_idx = n_lines - 1
    last_parts = split_text_budget(lines[last_idx].get("text"))
    last_chunk = _make_line_chunk(lines[last_idx], last_parts[-1], last_idx,
                                  len(last_parts) - 1, len(last_parts))

    if n_shots <= n_lines:
        # demo：每个台本句取首段，按顺序一镜一句；末镜固定台本末句
        chosen: list[dict] = []
        for idx in range(last_idx):
            parts = split_text_budget(lines[idx].get("text"))
            if not parts:
                continue
            chosen.append(_make_line_chunk(lines[idx], parts[0], idx, 0, len(parts)))
            if len(chosen) >= n_shots - 1:
                break
        if len(chosen) < n_shots - 1:
            extra = [c for c in _chunk_lines(lines[:-1])
                     if c["_part_index"] > 0]
            chosen.extend(extra[:n_shots - 1 - len(chosen)])
        chosen = chosen[:max(0, n_shots - 1)] + [last_chunk]
        slots = bodies[:len(chosen)]
    else:
        # 5 分钟版：全部台词切分后均匀铺到时间线上
        chosen = _chunk_lines(lines)
        slots = [bodies[i] for i in _spread_indices(len(chosen), n_shots)]

    for shot, chunk in zip(slots, chosen):
        shot["_script_line"] = chunk
    if slots:
        first = chosen[0]
        last = chosen[-1]
        slots[0]["_script_first"] = (first["_line_index"] == 0
                                     and first["_part_index"] == 0)
        slots[-1]["_script_last"] = (last["_line_index"] == last_idx
                                     and last["_part_index"] == last["_parts"] - 1)


def _placeholders(template: dict) -> list[dict]:
    """无台本时的占位台词（模板 beats 生成，QC/渲染仍可跑通）。"""
    out: list[dict] = []
    for seg in template.get("segments") or []:
        if not isinstance(seg, dict):
            continue
        for beat in seg.get("beats") or []:
            text = str(beat or "").strip()
            if text:
                out.append({"speaker": "", "text": text,
                            "segment_name": str(seg.get("name") or "")})
    return out


# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------
def build_plan(template: dict, target_duration, *, script=None,
               include_cards: bool = True, seed: int = DEFAULT_SEED) -> dict:
    """模板 + 台本 → {episode, assets, shots, cards, meta}。"""
    if not isinstance(template, dict) or not template.get("id"):
        raise ValueError("template 必须是已加载的模板 dict（含 id）")
    try:
        target = float(target_duration)
    except (TypeError, ValueError):
        target = DEFAULT_TARGET_DURATION
    if target <= 0:
        target = DEFAULT_TARGET_DURATION

    template_id = str(template.get("id"))
    label = str(template.get("label") or template_id)
    style = str(template.get("style") or "")
    n_shots = shot_count_for(target, include_cards)
    flat = template_v3.shots(template)
    if not flat:
        raise ValueError(f"模板 {template_id} 没有 shots")

    assets = build_assets(template)
    smap = speaker_map(template, assets)
    script_data = load_script(template_id, script)
    lines = script_lines(script_data)
    line_source = "llm_scripts" if lines else "beats_placeholder"
    if not lines:
        lines = _placeholders(template)

    h3_cfg = template.get("h3") if isinstance(template.get("h3"), dict) else {}
    packaging = template.get("packaging") if isinstance(template.get("packaging"), dict) else {}
    audio_cfg = template.get("audio") if isinstance(template.get("audio"), dict) else {}
    sfx_pool = [str(x) for x in audio_cfg.get("sfx") or [] if str(x).strip()]
    h3_resolution = str(h3_cfg.get("resolution") or "1024x576")

    # 1) 卡片
    cards: dict[str, dict] = {}
    body: list[dict] = []
    if include_cards:
        cards["title"] = _card_shot(1, "title_card", TITLE_CARD_DURATION, template,
                                    title=f"{label} · {str((script_data or {}).get('title') or '造片局')}")
        cards["end"] = _card_shot(0, "end_card", END_CARD_DURATION, template,
                                  title=f"{label} · 下期见")

    # 2) 正文镜：按模板 shots 顺序循环取（跨段循环）
    for i in range(n_shots):
        src = flat[i % len(flat)]
        shot = _make_shot(i + 1, template, src, smap, seed, h3_resolution,
                          sfx_pool, line_source)
        body.append(shot)

    # 3) 台词分配（首尾保护 + 段组映射 + 优先 audio_ref 镜）
    seg_index_by_id = {str(seg.get("id") or ""): i
                       for i, seg in enumerate(template.get("segments") or [])
                       if isinstance(seg, dict)}
    n_script_segs = script_segment_count(script_data) if line_source == "llm_scripts" else 1
    _assign_lines(body, lines, n_script_segs=n_script_segs,
                  seg_index_by_id=seg_index_by_id,
                  n_template_segs=max(1, len(template.get("segments") or [])))
    for shot in body:
        line = shot.pop("_script_line", None)
        first_line = bool(shot.pop("_script_first", False))
        last_line = bool(shot.pop("_script_last", False))
        if line is not None:
            roll = resolve_speaker(line.get("speaker") or "", smap)
            if roll is not None:
                shot["role"] = roll["role"]
                shot["subject_role"] = roll["role"]
                shot["speaker"] = roll["asset_id"]
                shot["speaker_name"] = roll["name"]
                shot["lower_third"] = {"name": roll["name"], "title": roll.get("title") or ""}
            shot["line"] = str(line.get("text") or "").strip()
            shot["script_segment"] = str(line.get("segment_name") or "")
            shot["script_segment_index"] = int(line.get("segment_index") or 0)
        shot["script_first"] = first_line
        shot["script_last"] = last_line
        # 无台词镜：说话人置空（空镜/反应镜；仍走 audio_ref + 静音参考）
        if not shot["line"]:
            shot["speaker"] = None
            shot["speaker_name"] = ""
            shot["lower_third"] = None
        # Lead 实测决策（2026-10-26）：v3 非卡片镜统一走 05_ref2va（audio_ref）。
        # fl2v 会把 stage 的 2D cutout 纸片感烘进视频，正片不再使用；
        # fl2v 代码路径保留（normalize_h3_mode / 单测 / 兜底仍可测）。
        shot["h3_mode"] = H3_MODE_POLICY if H3_MODE_POLICY else "audio_ref"
        shot["template_h3_mode_used"] = bool(shot["h3_mode"] == shot.get("template_h3_mode"))
        shot["type"] = shot_type_for(shot["h3_mode"], shot.get("role") or "",
                                     str((shot.get("v3") or {}).get("intent") or ""),
                                     str((shot.get("v3") or {}).get("size") or ""))
        shot["speaker_role"] = shot.get("role") or shot.get("subject_role") or ""
        shot["visual"] = _short_visual(shot.get("segment_name") or "",
                                       str((shot.get("v3") or {}).get("size") or ""),
                                       str((shot.get("v3") or {}).get("movement") or ""),
                                       str(shot.get("action") or ""))
        _refresh_shot_plan(shot)

    # 4) 组装镜头序列（片头卡 → 正文 → 片尾卡）
    shots: list[dict] = []
    if "title" in cards:
        shots.append(cards["title"])
    shots.extend(body)
    if "end" in cards:
        shots.append(cards["end"])
    for i, shot in enumerate(shots):
        shot["no"] = i + 1

    total = plan_duration({"shots": shots})
    segments = _episode_segments(template, shots)

    episode = {
        "id": f"{template_id}_v3_{int(round(target))}s",
        "project_id": "demo",
        "no": 1,
        "title": f"造片局 · {label}",
        "topic": str((script_data or {}).get("topic") or ""),
        "template_id": template_id,
        "template_name": label,
        "format_id": template_id,
        "style": style,
        "h3_mode": "plan",
        "seed": int(seed),
        "status": "draft",
        "progress": 0.0,
        "target_duration": round(target, 3),
        "total_duration": total,
        "duration": total,
        "shots_total": len(shots),
        "shots_done": 0,
        "cost": 0.0,
        "aspect": str(template.get("aspect") or "16:9"),
        "resolution": "1280x720",
        "fps": 25,
        "segment_count": len(segments),
        "segments": segments,
        "packaging": {
            "intro": "title_card",
            "outro": "end_card",
            "lower_third_style": str(packaging.get("lower_third") or ""),
            "transition": str(packaging.get("transition") or "fade"),
            "bgm": str(packaging.get("bgm") or ""),
            "subtitle_style": str(packaging.get("subtitle_style") or "variety"),
        },
        "audio": {"ambience": str(audio_cfg.get("ambience") or ""), "sfx": sfx_pool},
        "h3": {"workflow": str(h3_cfg.get("workflow") or ""),
               "resolution": h3_resolution,
               "fps": int(h3_cfg.get("fps") or H3_FPS),
               "shot_duration": H3_SHOT_DURATION},
    }

    h3_count = sum(1 for s in shots if s["type"] not in CARD_TYPES)
    bodies = [s for s in shots if s["type"] not in CARD_TYPES]
    dialogue = [s for s in bodies if str(s.get("line") or "").strip()]
    fl2v_with_line = [s["id"] for s in dialogue if s.get("h3_mode") == "fl2v"]
    opening_protected = bool(dialogue and dialogue[0].get("script_first"))
    ending_protected = bool(dialogue and dialogue[-1].get("script_last"))
    dialogue_metrics = {
        "dialogue_shots": len(dialogue),
        "silent_shots": len(bodies) - len(dialogue),
        "dialogue_coverage": round(len(dialogue) / len(bodies), 3) if bodies else 0.0,
        "fl2v_with_line": fl2v_with_line,
        "opening_line_protected": opening_protected,
        "ending_line_protected": ending_protected,
        "script_lines_used": len(dialogue),
        "script_lines_total": len(script_lines(script_data)),
        # 语速适配：长句按标点切分跨镜，避免 5.17s 镜裁掉台词
        "line_budget_est_sec": DEMO_LINE_EST_MAX,
        "split_lines": sum(1 for line in script_lines(script_data)
                           if len(split_text_budget(line.get("text"))) > 1),
        "max_dialogue_tts_est_sec": round(max(
            [estimate_tts_sec(s.get("line")) for s in dialogue] or [0.0]), 2),
    }
    episode.update(dialogue_metrics)
    episode["h3_mode_policy"] = H3_MODE_POLICY
    meta = {
        "template_id": template_id,
        "label": label,
        "style": style,
        "plate": str((template.get("stage") or {}).get("plate") or ""),
        "target_duration": round(target, 3),
        "total_duration": total,
        "shot_count": len(shots),
        "h3_shot_count": h3_count,
        "card_count": len(shots) - h3_count,
        "h3_shot_duration": H3_SHOT_DURATION,
        "title_card_duration": TITLE_CARD_DURATION if "title" in cards else 0.0,
        "end_card_duration": END_CARD_DURATION if "end" in cards else 0.0,
        "seed": int(seed),
        "script_source": (script_data or {}).get("provider") or "",
        "script_title": str((script_data or {}).get("title") or ""),
        "script_lines": len(script_lines(script_data)),
        "script_segments": n_script_segs if line_source == "llm_scripts" else 0,
        "line_source": line_source,
        "segments": [s.get("id") for s in template.get("segments") or []],
        "h3_resolution": h3_resolution,
        "h3_workflow": str(h3_cfg.get("workflow") or ""),
        "h3_mode_policy": H3_MODE_POLICY,
        **dialogue_metrics,
    }
    return {"episode": episode, "assets": assets, "shots": shots,
            "cards": cards, "meta": meta}


def _card_shot(no: int, card_type: str, duration: float, template: dict,
               title: str) -> dict:
    template_id = str(template.get("id") or "")
    return {
        "id": f"v3_card_{card_type}",
        "no": no,
        "type": card_type,
        "duration": float(duration),
        "speaker": None,
        "speaker_name": "",
        "role": None,
        "subject_role": "",
        "speaker_role": "",
        "line": "",
        "subtitle": "",
        "visual": "品牌片头" if card_type == "title_card" else "片尾卡",
        "title": title,
        "lower_third": None,
        "punchline": "",
        "sfx": "",
        "mood": "",
        "status": "pending",
        "segment_id": "__title__" if card_type == "title_card" else "__end__",
        "segment_name": "片头" if card_type == "title_card" else "片尾",
        "h3_mode": "static",
        "action": None,
        "static_card": True,
        "v3": {
            "intent": card_type,
            "size": "Wide",
            "angle": "N/A",
            "movement": "static",
            "action": None,
            "props": [],
            "h3_mode": "static",
            "camera_zone": "front",
            "card": True,
            "card_title": title,
            "template": template_id,
        },
        "shot_plan": {
            "size": "Wide",
            "angle": "N/A",
            "movement": "static",
            "light": "brand",
            "transition": "fade",
            "purpose": card_type,
            "type": card_type,
            "h3_mode": "static",
            "static": True,
        },
    }


def _make_shot(no: int, template: dict, src: dict, smap: dict, seed: int,
               h3_resolution: str, sfx_pool: list[str], line_source: str) -> dict:
    """模板 shot → 计划 shot（台词/说话人稍后填）。"""
    template_id = str(template.get("id") or "")
    intent = str(src.get("intent") or "")
    size = str(src.get("size") or "")
    movement = str(src.get("movement") or "")
    action = str(src.get("action") or "")
    mode = normalize_h3_mode(src.get("h3_mode"), "")
    subject_role = ""
    cast_roles = smap["roles"]
    if cast_roles:
        if "establishing" in intent.lower() or "outro" in intent.lower():
            subject_role = (smap["hosts"] or cast_roles)[0]["role"]
        else:
            pool = smap["guests"] or cast_roles
            subject_role = pool[no % len(pool)]["role"]
    return {
        "id": f"v3_s{no:03d}",
        "no": no,
        "type": shot_type_for(mode, subject_role, intent, size),
        "duration": H3_SHOT_DURATION,
        "speaker": None,
        "speaker_name": "",
        "role": None,
        "subject_role": subject_role,
        "speaker_role": subject_role,
        "line": "",
        "subtitle": "",
        "visual": "",
        "lower_third": None,
        "punchline": "",
        "sfx": "",
        "mood": "",
        "status": "pending",
        "segment_id": str(src.get("segment_id") or ""),
        "segment_name": str(src.get("segment_name") or ""),
        "h3_mode": mode,
        "template_h3_mode": str(src.get("h3_mode") or ""),
        "action": action,
        "static_card": False,
        "seed": int(seed) + (no - 1),
        "h3_resolution": h3_resolution,
        "v3": {
            "intent": intent,
            "size": size,
            "angle": str(src.get("angle") or ""),
            "movement": movement,
            "action": action,
            "props": list(src.get("props") or []),
            "h3_mode": str(src.get("h3_mode") or ""),
            "camera_zone": str(src.get("camera_zone") or ""),
            "template": template_id,
            "segment_id": str(src.get("segment_id") or ""),
            "segment_name": str(src.get("segment_name") or ""),
            "shot_index": int(src.get("index") or 0),
        },
        "shot_plan": {
            "size": size,
            "angle": str(src.get("angle") or ""),
            "movement": movement,
            "light": "key + rim",
            "transition": "",
            "purpose": intent,
            "type": "",
            "action": action,
            "camera_zone": str(src.get("camera_zone") or ""),
            "h3_mode": mode,
            "props": list(src.get("props") or []),
            "sfx_pool": sfx_pool,
            "line_source": line_source,
        },
    }


def _refresh_shot_plan(shot: dict) -> None:
    plan = dict(shot.get("shot_plan") or {})
    v3 = shot.get("v3") or {}
    plan.update({
        "size": str(v3.get("size") or plan.get("size") or ""),
        "angle": str(v3.get("angle") or plan.get("angle") or ""),
        "movement": str(v3.get("movement") or plan.get("movement") or ""),
        "purpose": str(v3.get("intent") or plan.get("purpose") or ""),
        "type": shot.get("type"),
        "action": shot.get("action"),
        "camera_zone": str(v3.get("camera_zone") or plan.get("camera_zone") or ""),
        "h3_mode": shot.get("h3_mode"),
    })
    shot["shot_plan"] = plan


def _episode_segments(template: dict, shots: list[dict]) -> list[dict]:
    out = []
    for seg in template.get("segments") or []:
        if not isinstance(seg, dict):
            continue
        sid = str(seg.get("id") or "")
        dur = round(sum(float(s.get("duration") or 0.0) for s in shots
                        if s.get("segment_id") == sid), 3)
        out.append({
            "id": sid,
            "name": str(seg.get("name") or ""),
            "duration": dur,
            "pattern": [str((s.get("v3") or {}).get("intent") or "")
                        for s in shots if s.get("segment_id") == sid],
        })
    return out


# ---------------------------------------------------------------------------
# CLI（调试：导出 episode.json 看 shot 序列）
# ---------------------------------------------------------------------------
def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m pipeline.storyboard_v3",
                                 description="v3 storyboard：模板 + 台本 → shot 序列")
    ap.add_argument("--template", required=True)
    ap.add_argument("--duration", type=float, default=DEFAULT_TARGET_DURATION)
    ap.add_argument("--templates-dir", default=None)
    ap.add_argument("--script", default=None, help="台本 JSON（默认自动读 outputs/llm_scripts）")
    ap.add_argument("--no-cards", action="store_true")
    ap.add_argument("--seed", type=int, default=DEFAULT_SEED)
    ap.add_argument("--out", default=None, help="episode.json 输出路径")
    args = ap.parse_args(argv)
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass
    template = template_v3.load(args.template, Path(args.templates_dir)
                                if args.templates_dir else None)
    plan = build_plan(template, args.duration, script=args.script,
                      include_cards=not args.no_cards, seed=args.seed)
    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
    summary = {"ok": True, "template": args.template,
               "shots": len(plan["shots"]), "duration": plan_duration(plan),
               "out": args.out or "", **{k: plan["meta"].get(k) for k in
                                          ("h3_shot_count", "card_count", "line_source")}}
    if args.out:
        print(json.dumps(summary, ensure_ascii=False))
        return 0
    for shot in plan["shots"]:
        print(f"{shot['id']} {shot['type']:14s} {shot['duration']:.2f}s "
              f"{shot['h3_mode']:9s} {shot['segment_id']:11s} "
              f"{shot.get('speaker_name') or '-':4s} {shot['line'][:28]}")
    print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
