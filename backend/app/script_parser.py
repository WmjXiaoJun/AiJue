"""台本 -> 分镜解析（contract/SCRIPT_FORMAT.md v1）。

面向非技术用户的台本格式：
  [开场] / 【正片】            分段标记（不单独成镜）
  老周：台词 / 老周: 台词      对白（中文/英文冒号均可）
  （推了推眼镜） / (冷笑)       整行括号动作 -> reaction 镜头
  没有冒号的一行                旁白（host 音色）
  空行                          分隔

输出镜头顺序：title_card -> 台本镜头（按顺序）-> end_card，no 从 1 递增。
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Sequence, Tuple

SEGMENT_RE = re.compile(r"^\s*[\[【](.+?)[\]】]\s*$")
ACTION_RE = re.compile(r"^\s*[（(](.+?)[）)]\s*$")
DIALOGUE_RE = re.compile(r"^\s*([^：:]{1,30})[：:]\s*(.*)$")

TITLE_DURATION = 3.5
MIN_DURATION = 2.5
MAX_DURATION = 8.0
DEFAULT_SEGMENT = "正片"


def _field(obj: Any, name: str, default: Any = "") -> Any:
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def clamp_duration(text: str) -> float:
    """单镜时长：clamp(1.2 + 台词字数*0.13, 2.5, 8.0)。"""
    chars = len(re.sub(r"\s+", "", text or ""))
    value = 1.2 + chars * 0.13
    return round(max(MIN_DURATION, min(MAX_DURATION, value)), 2)


def _character_assets(assets: Sequence[Any]) -> List[Any]:
    return [
        asset
        for asset in assets or []
        if str(_field(asset, "type", "")) == "character"
    ]


def _host_asset(assets: Sequence[Any]) -> Optional[Any]:
    characters = _character_assets(assets)
    for asset in characters:
        if str(_field(asset, "role", "")) == "host":
            return asset
    return characters[0] if characters else None


def match_speaker(
    name: str,
    assets: Sequence[Any],
    characters: Optional[Sequence[Any]] = None,
) -> Tuple[Optional[str], str]:
    """把台本里的名字匹配到资产/角色（包含匹配）。

    返回 (speaker_id, display_name)；匹配失败时 speaker_id=None、display_name=原文名字，
    voice 由渲染端回退 host 音色。
    """
    target = str(name or "").strip()
    if not target:
        return None, ""
    for asset in _character_assets(assets):
        asset_name = str(_field(asset, "name", "")).strip()
        asset_title = str(_field(asset, "title", "")).strip()
        for candidate in (asset_name, asset_title):
            if candidate and (
                target == candidate
                or target in candidate
                or candidate in target
            ):
                return str(_field(asset, "id", "")), asset_name or candidate or target
    for character in characters or []:
        char_name = str(_field(character, "name", "")).strip()
        char_title = str(_field(character, "title", "")).strip()
        for candidate in (char_name, char_title):
            if candidate and (
                target == candidate
                or target in candidate
                or candidate in target
            ):
                asset_id = _field(character, "asset_id", None)
                return (str(asset_id) if asset_id else None), char_name or candidate or target
    return None, target


def _base_shot(
    *,
    shot_type: str,
    duration: float,
    segment: str,
    speaker: Optional[str] = None,
    speaker_name: str = "",
    line: str = "",
    visual: str = "",
    sfx: str = "",
    title: Optional[str] = None,
    subtitle: Optional[str] = None,
) -> Dict[str, Any]:
    return {
        "segment_id": segment,
        "type": shot_type,
        "duration": round(float(duration), 2),
        "speaker": speaker,
        "speaker_name": speaker_name,
        "line": line,
        "visual": visual,
        "lower_third": None,
        "punchline": "",
        "sfx": sfx,
        "mood": "",
        "status": "pending",
        "selected_version_id": None,
        "title": title,
        "subtitle": subtitle,
    }


def build_shots(
    episode_title: str,
    script: str,
    assets: Sequence[Any],
    characters: Optional[Sequence[Any]] = None,
) -> List[Dict[str, Any]]:
    """把台本解析成镜头列表（未落库，no 由调用方按顺序赋值）。"""
    host = _host_asset(assets)
    host_id = str(_field(host, "id", "")) if host is not None else None
    host_name = str(_field(host, "name", "")) if host is not None else "主持人"
    host_id = host_id or None

    shots: List[Dict[str, Any]] = [
        _base_shot(
            shot_type="title_card",
            duration=TITLE_DURATION,
            segment="open",
            speaker=None,
            speaker_name="",
            visual="品牌片头 · 造片局 · 暖黄光晕",
            sfx="whoosh",
            title=episode_title,
            subtitle="造片局 · AI 微综艺",
        )
    ]
    segment = DEFAULT_SEGMENT
    previous_speaker: Optional[str] = host_id
    previous_name: str = host_name
    for raw_line in str(script or "").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        segment_match = SEGMENT_RE.match(line)
        if segment_match:
            segment = segment_match.group(1).strip() or DEFAULT_SEGMENT
            continue
        action_match = ACTION_RE.match(line)
        if action_match:
            visual = action_match.group(1).strip()
            if not visual:
                continue
            speaker = previous_speaker or host_id
            speaker_name = previous_name or host_name
            shots.append(
                _base_shot(
                    shot_type="reaction",
                    duration=clamp_duration(visual),
                    segment=segment,
                    speaker=speaker,
                    speaker_name=speaker_name,
                    visual=visual,
                    sfx="ding",
                    subtitle=("（" + visual + "）"),
                )
            )
            previous_speaker, previous_name = speaker, speaker_name
            continue
        dialogue_match = DIALOGUE_RE.match(line)
        if dialogue_match:
            name = dialogue_match.group(1).strip()
            text = dialogue_match.group(2).strip()
            if not text:
                continue
            speaker_id, display_name = match_speaker(name, assets, characters)
            # 台词行首的括号动作（如「大魏：（冷笑）台词」）拆成 reaction 镜头再接对白，
            # 避免把动作说明念进 TTS / 字幕。
            leading_actions: List[str] = []
            while True:
                action_match = re.match(r"^[（(](.+?)[）)]\s*", text)
                if not action_match:
                    break
                leading_actions.append(action_match.group(1).strip())
                text = text[action_match.end():].strip()
            for action in leading_actions:
                if not action:
                    continue
                shots.append(
                    _base_shot(
                        shot_type="reaction",
                        duration=clamp_duration(action),
                        segment=segment,
                        speaker=speaker_id,
                        speaker_name=display_name,
                        visual=action,
                        sfx="ding",
                        subtitle=("（" + action + "）"),
                    )
                )
            if not text:
                previous_speaker, previous_name = speaker_id, display_name
                continue
            is_host = bool(host is not None and speaker_id == host_id)
            shot_type = "host_to_camera" if (is_host or speaker_id is None) else "shot_reverse"
            shots.append(
                _base_shot(
                    shot_type=shot_type,
                    duration=clamp_duration(text),
                    segment=segment,
                    speaker=speaker_id,
                    speaker_name=display_name,
                    line=text,
                    visual=f"{display_name} · 中景",
                )
            )
            previous_speaker = speaker_id
            previous_name = display_name
            continue
        # 无冒号 -> 主持人旁白
        shots.append(
            _base_shot(
                shot_type="host_to_camera",
                duration=clamp_duration(line),
                segment=segment,
                speaker=host_id,
                speaker_name=host_name,
                line=line,
                visual="主持人旁白 · 中景",
            )
        )
        previous_speaker, previous_name = host_id, host_name

    shots.append(
        _base_shot(
            shot_type="end_card",
            duration=TITLE_DURATION,
            segment="closing",
            speaker=None,
            speaker_name="",
            visual="品牌片尾 · 造片局 logo · 下期见",
            sfx="whoosh",
            title="造片局 · AI 微综艺",
            subtitle="下期见",
        )
    )
    for index, shot in enumerate(shots, start=1):
        shot["no"] = index
    return shots


def total_duration(shots: Sequence[Dict[str, Any]]) -> float:
    return round(sum(float(shot.get("duration") or 0.0) for shot in shots), 2)
