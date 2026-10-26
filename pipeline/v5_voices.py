# -*- coding: utf-8 -*-
"""V5 角色固定声音：声音库解析 + 声音锁（reference audio）+ 预览。

设计目标（用户需求）：
1) **每个角色的声音固定**：角色 JSON 只存 `voice.voice_id`（可选覆盖 seed/instruction/ref_audio），
   声音库 `contract/v5/voices.json` 里每个 preset 有固定 instruction + seed + cfg_scale；
   laozhou/ali/dawei 的 preset 与 V4 语音完全一致（跨版本音色不变）。
2) **声音锁**：首次为角色生成一段标准参考音频（"大家好，我是XX。这是我的固定声音。"），
   之后该角色所有台词都用 Voice Clone（ref_audio + ref_text）合成 → 跨镜/跨集音色一致；
   参考音频缓存到 `outputs/v5_voices/{character}/{voice_key}.wav`，可复用/校验/替换。
3) **在角色下可以修改**：改 `contract/v5/characters/{id}.json` 的 `voice` 块（voice_id/seed/
   instruction/cfg_scale/ref_audio/ref_text），或 `custom/characters/{id}.json` 覆盖；改完
   `v5_cli voice lock --character <id> --force` 重建锁并重渲。

CLI（由 pipeline/v5_cli.py 暴露）：
  voice list / voice show --character X / voice preview --character X --text "..." \
  voice lock --character X [--force] / voice set --character X --voice-id ... --seed ...
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from . import config, tts_breeze, utils

LOG = __import__("logging").getLogger("pipeline")

REPO = Path(config.REPO_ROOT)
DEFAULT_LIB = REPO / "contract" / "v5" / "voices.json"
CUSTOM_LIB = REPO / "contract" / "v5" / "custom" / "voices.json"
VOICE_ROOT = REPO / "outputs" / "v5_voices"
REF_TEXT_TEMPLATE = "大家好，我是{name}。这是我的固定声音，以后都用这个声音说话。"


class V5VoiceError(RuntimeError):
    pass


# ---------------------------------------------------------------------------
# 声音库
# ---------------------------------------------------------------------------
def _deep_merge(base: dict, override: dict) -> dict:
    out = dict(base or {})
    for k, v in (override or {}).items():
        if isinstance(out.get(k), dict) and isinstance(v, dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def load_voice_library(path: str | Path | None = None) -> dict:
    """读内建 voices.json + custom/voices.json（深合并 presets）。"""
    lib_path = Path(path) if path else DEFAULT_LIB
    data = utils.read_json(lib_path) or {}
    if not isinstance(data, dict) or not isinstance(data.get("presets"), dict):
        raise V5VoiceError(f"声音库非法（缺少 presets）: {lib_path}")
    presets = {k: dict(v or {}) for k, v in data["presets"].items()}
    custom_path = CUSTOM_LIB if path is None else Path(path).with_name("custom") / "voices.json"
    if custom_path.is_file():
        custom = utils.read_json(custom_path) or {}
        for k, v in (custom.get("presets") or {}).items():
            presets[k] = _deep_merge(presets.get(k) or {}, v)
    for pid, spec in presets.items():
        spec.setdefault("id", pid)
        spec.setdefault("provider", "breeze")
    return {"version": data.get("version", 1), "presets": presets,
            "path": str(lib_path), "custom_path": str(custom_path)}


def preset(library: dict, voice_id: str) -> dict:
    spec = (library.get("presets") or {}).get(str(voice_id or ""))
    if not spec:
        raise V5VoiceError(
            f"未知 voice_id: {voice_id!r}（检查 contract/v5/voices.json 或角色 JSON）")
    return spec


def voice_key(spec: dict) -> str:
    """声音指纹：instruction/seed/cfg/provider/ref_audio 的稳定 hash。"""
    ref = ""
    if spec.get("ref_audio"):
        p = Path(spec["ref_audio"])
        ref = f"{p.name}:{p.stat().st_size if p.is_file() else 0}"
    raw = "|".join(str(spec.get(k) or "") for k in
                   ("provider", "voice_id", "instruction", "seed", "cfg_scale")) + "|" + ref
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]


def resolve_voice(character: dict, library: dict) -> dict:
    """角色 JSON → 完整声音 spec（voice_id/preset + 角色内覆盖）。

    character.voice 支持：
      {"voice_id": "legacy_v4_laozhou"}                      # 只引用 preset（推荐，固定）
      {"voice_id": "...", "seed": 123456}                   # 覆盖 seed
      {"voice_id": "...", "instruction": "..."}              # 覆盖音色描述
      {"provider":"breeze","instruction":"...","seed":..}    # 旧格式（无 voice_id）仍兼容
    """
    if not isinstance(character, dict):
        raise V5VoiceError("角色非法")
    raw = character.get("voice") if isinstance(character.get("voice"), dict) else {}
    vid = str(raw.get("voice_id") or "")
    base: dict = {}
    if vid:
        base = dict(preset(library, vid))
    spec = {
        "character": character.get("id") or character.get("character_id"),
        "name": character.get("label") or character.get("name") or "",
        "voice_id": vid or f"inline_{character.get('id') or 'unknown'}",
        "provider": raw.get("provider") or base.get("provider") or "breeze",
        "instruction": raw.get("instruction") or base.get("instruction") or "",
        "seed": raw.get("seed", base.get("seed")),
        "cfg_scale": raw.get("cfg_scale", base.get("cfg_scale", 4.0)),
        "label": base.get("label") or raw.get("label") or "",
        "ref_audio": raw.get("ref_audio") or base.get("ref_audio"),
        "ref_text": raw.get("ref_text") or base.get("ref_text"),
        "preset": base or None,
        "source": "custom" if raw.get("voice_id") else ("inline" if raw.get("instruction") else "preset"),
    }
    if spec["seed"] is None:
        raise V5VoiceError(
            f"角色 {spec['character']} 的声音缺少 seed（voice_id={vid or '无'}），"
            "请在 voices.json preset 或角色 voice 里补 seed")
    spec["seed"] = int(spec["seed"])
    spec["cfg_scale"] = float(spec["cfg_scale"] or 4.0)
    spec["key"] = voice_key(spec)
    return spec


def validate_library(library: dict | None = None, catalog: dict | None = None) -> dict:
    """校验声音库 + 角色 voice_id 引用。返回 {ok, errors, warnings}。"""
    lib = library or load_voice_library()
    errors: list[str] = []
    warnings: list[str] = []
    for pid, spec in (lib.get("presets") or {}).items():
        for field in ("instruction", "seed"):
            if not spec.get(field):
                errors.append(f"preset[{pid}]: 缺 {field}")
        if spec.get("ref_audio") and not Path(spec["ref_audio"]).is_file():
            warnings.append(f"preset[{pid}]: ref_audio 不存在 {spec['ref_audio']}")
    chars = (catalog or {}).get("characters") or {}
    for cid, ch in chars.items():
        try:
            spec = resolve_voice(ch, lib)
        except V5VoiceError as exc:
            errors.append(f"character[{cid}]: {exc}")
            continue
        if not spec.get("instruction"):
            warnings.append(f"character[{cid}]: 无 instruction（将用默认音色）")
    return {"ok": not errors, "errors": errors, "warnings": warnings,
            "presets": len(lib.get("presets") or {}), "characters": len(chars)}


# ---------------------------------------------------------------------------
# 声音锁（reference audio，Voice Clone 用）
# ---------------------------------------------------------------------------
def lock_dir(character_id: str) -> Path:
    return VOICE_ROOT / str(character_id)


def lock_paths(character_id: str, spec: dict) -> tuple[Path, Path]:
    d = lock_dir(character_id)
    return d / f"{spec['key']}.wav", d / f"{spec['key']}.json"


def lock_info(character_id: str, spec: dict) -> dict:
    wav, meta = lock_paths(character_id, spec)
    info = utils.read_json(meta) or {}
    return {"character": character_id, "key": spec["key"], "wav": str(wav),
            "exists": wav.is_file(), "text": info.get("text"),
            "seed": info.get("seed"), "voice_id": info.get("voice_id"),
            "created_at": info.get("created_at")}


def ensure_voice_lock(character: dict, spec: dict, *, force: bool = False,
                      base: str | None = None, prog=None) -> dict:
    """确保角色有固定参考音频（首次用 Breeze 生成；之后复用）。

    返回 {"wav","text","key","cached",...}；失败抛 V5VoiceError。
    """
    cid = str(spec.get("character") or character.get("id") or "unknown")
    wav, meta = lock_paths(cid, spec)
    if force and wav.is_file():
        try:
            wav.unlink()
        except OSError:
            pass
    if wav.is_file() and meta.is_file():
        info = utils.read_json(meta) or {}
        if info.get("key") == spec["key"] and wav.stat().st_size > 2000:
            return {"character": cid, "key": spec["key"], "wav": str(wav),
                    "text": info.get("text") or "", "cached": True,
                    "voice_id": spec["voice_id"], "seed": spec["seed"]}
    if not tts_breeze.server_alive(base):
        if prog:
            prog(5, "启动 Breeze TTS 服务（生成角色固定声音参考）")
        if not tts_breeze.start_server(base):
            raise V5VoiceError("Breeze TTS 服务启动失败（voice lock 需要）")
    name = spec.get("name") or cid
    ref_text = spec.get("ref_text") or REF_TEXT_TEMPLATE.format(name=name)
    res = tts_breeze.synthesize_batch([ref_text], cid, out_dir=lock_dir(cid) / "tmp",
                                      base=base, voice_spec={
                                          "instruction": spec.get("instruction"),
                                          "seed": spec["seed"],
                                          "cfg_scale": spec["cfg_scale"]})
    if not res:
        raise V5VoiceError(f"生成角色 {cid} 参考音频失败")
    src = Path(res[0]["path"])
    utils.ensure_dir(wav.parent)
    utils.ffmpeg(["-y", "-hide_banner", "-loglevel", "error", "-i", str(src),
                  "-ar", "24000", "-ac", "1", "-c:a", "pcm_s16le", str(wav)])
    utils.write_json(meta, {
        "character": cid, "key": spec["key"], "voice_id": spec["voice_id"],
        "seed": spec["seed"], "cfg_scale": spec["cfg_scale"],
        "instruction": spec.get("instruction"), "text": ref_text,
        "duration": round(utils.probe_duration(wav), 3),
        "created_at": __import__("time").strftime("%Y-%m-%d %H:%M:%S"),
    })
    LOG.info("角色 %s 声音锁生成：%s（%.2fs）", cid, wav,
             utils.probe_duration(wav))
    return {"character": cid, "key": spec["key"], "wav": str(wav),
            "text": ref_text, "cached": False, "voice_id": spec["voice_id"],
            "seed": spec["seed"]}


def apply_lock(spec: dict, lock: dict | None) -> dict:
    """把 lock 的 ref_audio/ref_text 合入 spec（供 tts_breeze 做 voice clone）。"""
    out = dict(spec)
    if lock and lock.get("wav") and Path(lock["wav"]).is_file():
        out["ref_audio"] = lock["wav"]
        out["ref_text"] = lock.get("text") or ""
    return out


def resolve_all_characters(catalog: dict, library: dict | None = None) -> dict[str, dict]:
    """{character_id: spec}（不含锁）。"""
    lib = library or load_voice_library()
    chars = (catalog or {}).get("characters") or {}
    out = {}
    for cid, ch in chars.items():
        try:
            out[cid] = resolve_voice(ch, lib)
        except V5VoiceError as exc:
            LOG.warning("角色 %s 声音解析失败（将用默认）：%s", cid, exc)
    return out


def preview(character: dict, text: str, out_wav: str | Path, *,
            library: dict | None = None, base: str | None = None) -> dict:
    """试听角色声音（遵循声音锁：有锁用 clone，无锁用 Voice Design）。"""
    lib = library or load_voice_library()
    spec = resolve_voice(character, lib)
    cid = str(spec["character"])
    lock = ensure_voice_lock(character, spec, base=base)
    use = apply_lock(spec, lock)
    if not tts_breeze.server_alive(base):
        if not tts_breeze.start_server(base):
            raise V5VoiceError("Breeze TTS 服务启动失败")
    res = tts_breeze.synthesize_batch([str(text)], cid, out_dir=Path(out_wav).parent,
                                      base=base, voice_spec={
                                          "instruction": use.get("instruction"),
                                          "seed": use["seed"],
                                          "cfg_scale": use["cfg_scale"]},
                                      ref_audio=use.get("ref_audio"),
                                      ref_text=use.get("ref_text") or "")
    if not res:
        raise V5VoiceError("试听合成失败")
    src = Path(res[0]["path"])
    utils.ffmpeg(["-y", "-hide_banner", "-loglevel", "error", "-i", str(src),
                  "-ar", "48000", "-ac", "2", "-c:a", "pcm_s16le", str(out_wav)])
    return {"character": cid, "voice_id": spec["voice_id"], "key": spec["key"],
            "seed": spec["seed"], "out": str(out_wav),
            "duration": round(utils.probe_duration(out_wav), 3),
            "locked": bool(lock.get("wav"))}


def set_character_voice(character_id: str, updates: dict, *, catalog: dict,
                        root: str | Path | None = None) -> Path:
    """把声音修改写成 custom/characters/{id}.json 覆盖（只含 voice 块，深合并）。"""
    if not isinstance(updates, dict) or not updates:
        raise V5VoiceError("voice 修改为空")
    chars = (catalog or {}).get("characters") or {}
    if character_id not in chars:
        raise V5VoiceError(f"角色不存在: {character_id}")
    base_root = Path(root) if root else REPO / "contract" / "v5"
    out = base_root / "custom" / "characters" / f"{character_id}.json"
    utils.ensure_dir(out.parent)
    payload = {"id": character_id, "voice": updates}
    utils.write_json(out, payload)
    return out
