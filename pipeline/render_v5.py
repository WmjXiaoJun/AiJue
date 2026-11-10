# -*- coding: utf-8 -*-
"""V5 渲染引擎：模板 + 风格 + 场景/角色库 → 成片。

流程：
1) catalog = pipeline/v5_catalog.load_catalog()（模板/风格/场景/角色/道具）
2) plan = v5_story.build_plan(template)（acts → 逐镜）
3) TTS = Breeze TTS 2（每个角色用角色 JSON 的 voice instruction/seed）→ **语音驱动镜头时长**
4) 视觉 = scene_render.render_scene_layers + characters_v5.render_character + motion2d.render_shot
5) 对白镜口型 = motion2d 内置 viseme 时间轴（保证同步）；可选 lipsync（MuseTalk）用于外部 H3 片段
6) 拼装 = xfade + 每镜音轨 + 风格化字幕 + BGM ducking + 两遍 loudnorm（复用 render.final_encode）
7) 产物 = final.mp4 / timeline.json / style_report.json / qc_report.json / shots/ / frames/

CLI:
  python -m pipeline.cli render_v5 --template history_talk --duration 40
"""
from __future__ import annotations

import inspect
import json
import logging
import math
import os
import time
from pathlib import Path

from . import config, render as render_mod, utils

LOG = logging.getLogger("pipeline")

OUT_ROOT = Path(config.REPO_ROOT) / "outputs" / "templates_v5"
FINAL_W, FINAL_H, FPS = 1280, 720, 25
LEAD_SEC = 0.10
TAIL_SEC = 0.35
MIN_SHOT = 1.4
MAX_SHOT = 12.0
TRANSITION_DUR = 0.35
BGM_GAIN = 0.16
#: `--duration` 是**下限**不是等号：成片短于 target×此比例直接判失败（P1-9）
MIN_DURATION_RATIO = 0.6


class RenderV5Error(RuntimeError):
    pass


def _voice_entry_ok(info, text: str, spec: dict) -> tuple[bool, str]:
    """voice_cache 条目是否还能用：文件在 + 大小没变 + 台词指纹/音色一致。

    P0-1 的另一半：缓存 key 是台词原文，但 value 里的 path 以前只按内下标命名，
    命中了却指向被覆盖/被截断的文件。现在 value 带 `text_sha1`/`bytes`/`voice_key`，
    任一对不上就当未命中，重新合成。
    """
    if not isinstance(info, dict):
        return False, "no_entry"
    path = Path(str(info.get("path") or ""))
    if not path.is_file():
        return False, "file_missing"
    try:
        size = path.stat().st_size
    except OSError:
        return False, "stat_failed"
    try:
        recorded_bytes = int(info.get("bytes") or 0)
    except (TypeError, ValueError):
        recorded_bytes = 0
    if recorded_bytes and size != recorded_bytes:
        return False, "size_changed"
    recorded_text = str(info.get("text_sha1") or "")
    if recorded_text:
        try:
            from . import tts_breeze as _tts  # noqa: PLC0415

            if recorded_text != _tts.text_sha1(text):
                return False, "text_hash_mismatch"
        except Exception:  # noqa: BLE001
            pass
    recorded_voice = str(info.get("voice_key") or "")
    if recorded_voice and recorded_voice != str(spec.get("key") or ""):
        return False, "voice_changed"
    return True, ""


# ---------------------------------------------------------------------------
# 基础工具
# ---------------------------------------------------------------------------
def _arg(args, name, default=None):
    return getattr(args, name, default) if args is not None else default


def resolve_paths(args, template_id: str) -> dict:
    out_arg = _arg(args, "out")
    if out_arg:
        out = Path(out_arg)
        if out.suffix.lower() != ".mp4":
            out = out / "final.mp4"
    else:
        out = OUT_ROOT / template_id / "final.mp4"
    out = out.resolve()
    out_dir = out.parent
    work_arg = _arg(args, "workdir")
    workdir = Path(work_arg).resolve() if work_arg else out_dir / "work"
    return {"out": out, "out_dir": out_dir, "workdir": workdir,
            "shots_dir": out_dir / "shots", "frames_dir": out_dir / "frames",
            "assets_dir": workdir / "assets"}


def _ensure_dirs(paths: dict) -> None:
    for key in ("out_dir", "workdir", "shots_dir", "frames_dir", "assets_dir"):
        utils.ensure_dir(paths[key])


def _load_catalog() -> dict:
    try:
        from . import v5_catalog
    except ImportError as exc:  # pragma: no cover
        raise RenderV5Error(f"v5_catalog 未就绪: {exc}") from exc
    return v5_catalog.load_catalog()


def _require(module_name: str):
    import importlib
    try:
        return importlib.import_module(f".{module_name}", __package__)
    except ImportError as exc:  # pragma: no cover
        raise RenderV5Error(f"模块 pipeline.{module_name} 未就绪: {exc}") from exc


def _template(catalog: dict, template_id: str) -> dict:
    templates = catalog.get("templates") or {}
    tpl = templates.get(template_id)
    if not tpl:
        raise RenderV5Error(f"模板不存在: {template_id}（可用: {', '.join(sorted(templates)[:8])}...）")
    return tpl


def _style(catalog: dict, style_id: str) -> dict:
    styles = catalog.get("styles") or {}
    st = styles.get(style_id)
    if not st:
        raise RenderV5Error(f"风格不存在: {style_id}")
    return st


def _scene(catalog: dict, scene_id: str) -> dict:
    scenes = catalog.get("scenes") or {}
    sc = scenes.get(scene_id)
    if not sc:
        raise RenderV5Error(f"场景不存在: {scene_id}")
    return sc


def _character(catalog: dict, char_id: str) -> dict:
    chars = catalog.get("characters") or {}
    ch = chars.get(char_id)
    if not ch:
        raise RenderV5Error(f"角色不存在: {char_id}")
    return ch


# ---------------------------------------------------------------------------
# 1) TTS + 语音驱动时长
# ---------------------------------------------------------------------------
def tts_and_pace(plan: dict, catalog: dict, paths: dict, prog, force: bool = False) -> dict:
    """按角色合成台词（Breeze TTS 2 + 角色固定声音锁/voice clone），语音驱动镜头时长。

    - 角色声音来自 `contract/v5/voices.json` preset + 角色 JSON 的 voice 覆盖（v5_voices.resolve_voice）；
    - 首次为每个角色生成固定参考音频（声音锁），之后所有台词用 voice clone（ref_audio + ref_text），
      保证同一角色在任意镜头/任意集里音色一致；
    - 缓存键含 voice_key：修改角色声音后自动失效并重新合成。
    """
    from . import tts_breeze, v5_voices

    workdir = Path(paths["workdir"])
    audio_dir = utils.ensure_dir(workdir / "audio")
    voice_dir = utils.ensure_dir(workdir / "voice")
    raw_dir = utils.ensure_dir(voice_dir / "raw")
    shots = plan.get("shots") or []
    cast = (plan.get("meta") or {}).get("cast") or {}

    lib = v5_voices.load_voice_library()
    char_specs = v5_voices.resolve_all_characters(catalog, lib)

    # 1) 收集每句台词 → (character_id, text) + 角色声音 spec
    jobs: list[tuple[dict, str, str]] = []
    role_texts: dict[str, list[str]] = {}
    for shot in shots:
        line = str(shot.get("line") or "").strip()
        if not line:
            continue
        role = str(shot.get("role") or "")
        char_id = str(shot.get("character") or (cast.get(role) or {}).get("character") or role)
        if not char_id:
            continue
        if char_id not in char_specs:
            # 兼容：角色无 voice_id/声音库缺项时回退旧逻辑（character.voice 内联 instruction/seed）
            char = _character(catalog, char_id)
            legacy = tts_breeze.voice_spec_from_character(char)
            if not legacy.get("instruction"):
                legacy["instruction"] = (
                    f"{char.get('label') or char_id} 的声音，自然中文口语，"
                    f"{char.get('persona') or '沉稳'}")
            legacy.setdefault("seed", abs(hash(char_id)) % 100000 + 20264000)
            legacy.setdefault("cfg_scale", 4.0)
            char_specs[char_id] = {
                "character": char_id, "voice_id": f"inline_{char_id}",
                "provider": "breeze", "seed": int(legacy["seed"]),
                "cfg_scale": float(legacy["cfg_scale"]),
                "instruction": str(legacy["instruction"]),
                "key": v5_voices.voice_key({
                    "voice_id": f"inline_{char_id}", "provider": "breeze",
                    "instruction": legacy["instruction"], "seed": legacy["seed"],
                    "cfg_scale": legacy["cfg_scale"]}),
                "name": str(char.get("label") or char_id),
            }
            LOG.warning("角色 %s 未配置 voice_id，使用内联音色回退", char_id)
        jobs.append((shot, char_id, line))
        role_texts.setdefault(char_id, []).append(line)

    if not role_texts:
        # 纯视觉模板（custom_holo_talk 等）：不配音，按预估时长铺静音轨后直接返回
        total = 0.0
        for shot in shots:
            dur = float(shot.get("duration") or 2.6)
            out_wav = audio_dir / f"{shot['id']}.wav"
            _build_shot_audio(out_wav, None, dur, workdir)
            shot["audio"] = str(out_wav)
            shot["speech_duration"] = 0.0
            shot.setdefault("voice", {})
            total += dur
        plan["episode"]["total_duration"] = round(total, 3)
        LOG.info("无台词模板：%d 镜全部静音轨（%.1fs）", len(shots), total)
        return {"shots": len(shots), "trimmed": [], "duration": round(total, 3),
                "voices": [], "silent": True}

    # 2) 声音锁 + 缓存检查：全部就绪时不启动 Breeze（纯 CPU 重渲可省一次模型加载）
    if prog:
        prog(5, f"Breeze TTS 2：{len(role_texts)} 个角色 / 固定声音锁")
    relock = str(os.environ.get("V5_VOICE_RELOCK", "")).lower() in ("1", "true", "yes")
    cache_path = voice_dir / "voice_cache.json"
    cache = utils.read_json(cache_path) or {}
    reuse_tts = str(os.environ.get("V5_REUSE_TTS", "")).lower() in ("1", "true", "yes")
    if force and not reuse_tts:
        cache = {}
    pending: dict[str, list[str]] = {}
    for char_id, texts in role_texts.items():
        spec = char_specs[char_id]
        key_prefix = f"{char_id}|{spec['key']}|"
        for text in texts:
            if pending.get(char_id) and text in pending[char_id]:
                continue  # 同一句被多镜共用：只合成一次
            ok, why = _voice_entry_ok(cache.get(key_prefix + text), text, spec)
            if not ok:
                if key_prefix + text in cache:
                    LOG.warning("voice_cache 失效(%s)，重新合成：%s / %s",
                                why, char_id, str(text)[:20])
                pending.setdefault(char_id, []).append(text)
    locks: dict[str, dict] = {}
    missing_lock = False
    for cid in role_texts:
        spec = char_specs[cid]
        wav, _meta = v5_voices.lock_paths(cid, spec)
        if relock or not wav.is_file():
            missing_lock = True
            break
    if (pending or missing_lock) and not tts_breeze.server_alive():
        if prog:
            prog(6, "启动 Breeze TTS 2 服务（配音/声音锁需要，首次加载 1-2 分钟）")
        if not tts_breeze.start_server():
            raise RenderV5Error("Breeze TTS 服务启动失败")
    for cid in role_texts:
        spec = char_specs[cid]
        if relock or not v5_voices.lock_paths(cid, spec)[0].is_file():
            try:
                locks[cid] = v5_voices.ensure_voice_lock(
                    _character(catalog, cid), spec, force=relock)
            except Exception as exc:  # noqa: BLE001
                LOG.warning("角色 %s 声音锁生成失败（回退 Voice Design）: %s", cid, exc)
                locks[cid] = {"character": cid, "key": spec["key"], "wav": "",
                              "text": "", "cached": False, "voice_id": spec["voice_id"],
                              "seed": spec["seed"]}
        else:
            wav, meta_p = v5_voices.lock_paths(cid, spec)
            info = utils.read_json(meta_p) or {}
            locks[cid] = {"character": cid, "key": spec["key"], "wav": str(wav),
                          "text": info.get("text") or "", "cached": True,
                          "voice_id": spec["voice_id"], "seed": spec["seed"]}
    refs = {cid: (lock.get("wav"), lock.get("text") or "")
            for cid, lock in locks.items() if lock.get("wav")}
    voices = {cid: {"instruction": char_specs[cid].get("instruction"),
                    "seed": char_specs[cid]["seed"],
                    "cfg_scale": char_specs[cid]["cfg_scale"]}
              for cid in role_texts}

    # 3) 配音：只合成缓存缺失的句子（voice clone），写缓存与 meta 证据
    if prog:
        prog(8, f"配音：{sum(len(v) for v in pending.values())} 句 / "
                f"{len(role_texts)} 角色（voice clone）")
    if pending:
        results = tts_breeze.synthesize(pending, raw_dir, auto_start=False,
                                        voices=voices, refs=refs)
        for item in results:
            cid = item["role"]
            key = f"{cid}|{char_specs[cid]['key']}|{item['text']}"
            cache[key] = {"path": item["path"], "duration": item["duration"],
                          "provider": item.get("provider", "breeze"),
                          "seed": item.get("seed"),
                          "voice": item.get("voice", ""),
                          "voice_id": char_specs[cid]["voice_id"],
                          "voice_key": char_specs[cid]["key"],
                          "text_sha1": item.get("text_sha1", ""),
                          "bytes": item.get("bytes", 0),
                          "ref": refs.get(cid, (None, ""))[0] or ""}
        utils.write_json(cache_path, cache)
    meta = plan.setdefault("meta", {})
    meta["voice_provider"] = "breeze"
    meta["voices"] = [
        {"character": cid, "voice_id": char_specs[cid]["voice_id"],
         "key": char_specs[cid]["key"], "seed": char_specs[cid]["seed"],
         "ref": locks[cid].get("wav") or "",
         "ref_cached": bool(locks[cid].get("cached"))}
        for cid in role_texts]

    # 4) 每镜：语音时长 → 镜头时长；音轨 wav（48k stereo，voice 起点 +0.10s）
    total = 0.0
    for shot in shots:
        line = str(shot.get("line") or "").strip()
        role = str(shot.get("role") or "")
        if str(shot.get("type")) == "card":
            shot.setdefault("speech_duration", 0.0)
            shot["voice"] = {}
            out_wav = audio_dir / f"{shot['id']}.wav"
            if not out_wav.is_file() or force:
                _build_shot_audio(out_wav, None, float(shot.get("duration") or 1.4), workdir)
            shot["audio"] = str(out_wav)
            total += float(shot.get("duration") or 1.4)
            continue
        char_id = str(shot.get("character") or (cast.get(role) or {}).get("character") or role)
        speech = 0.0
        src_voice = None
        if line:
            spec = char_specs[char_id]
            info = cache.get(f"{char_id}|{spec['key']}|{line}")
            if not info:
                raise RenderV5Error(f"配音缺失: {char_id} / {line[:20]}")
            src_voice = Path(str(info.get("path") or ""))
            if not src_voice.is_file():
                raise RenderV5Error(f"配音文件缺失: {src_voice}（{char_id} / {line[:20]}）")
            # 时长以文件真实长度为准：缓存里的旧时长会让 atrim 把新音频从中间截断
            speech = float(utils.probe_duration(src_voice) or info.get("duration") or 0.0)
            info["duration"] = round(speech, 3)
            # 语音优先：先保 speech 不被截断，再尽量压到 MAX_SHOT 内（只能压 lead/tail）
            needed = round(speech + LEAD_SEC + TAIL_SEC, 2)
            dur = max(MIN_SHOT, needed)
            if needed > MAX_SHOT:
                shot["over_max_sec"] = round(needed - MAX_SHOT, 2)
                LOG.warning("镜头 %s 台词长于 MAX_SHOT：%.2fs（不截断语音，改用裁 b-roll 控总长）",
                            shot.get("id"), needed)
        else:
            dur = float(shot.get("duration") or 2.6)
        shot["speech_duration"] = round(speech, 3)
        shot["duration"] = round(dur, 3)
        spec = char_specs.get(char_id) or {}
        shot["voice"] = {"provider": "breeze" if line else "",
                         "character": char_id if line else "",
                         "voice_id": spec.get("voice_id", "") if line else "",
                         "voice_key": spec.get("key", "") if line else "",
                         "seed": spec.get("seed") if line else None,
                         "ref": (locks.get(char_id) or {}).get("wav", "") if line else "",
                         "text": line,
                         "speech_duration": round(speech, 3)}
        total += dur
        # 每镜音轨（digest 含 voice_key：改声音后强制重混）
        out_wav = audio_dir / f"{shot['id']}.wav"
        digest = utils.sha1_text("v5-audio", shot["id"], dur, str(src_voice or ""),
                                 str(shot["voice"].get("voice_key") or ""))
        meta_path = out_wav.with_suffix(".meta.json")
        m = utils.read_json(meta_path) or {}
        if not force and out_wav.is_file() and m.get("hash") == digest:
            shot["audio"] = str(out_wav)
            continue
        _build_shot_audio(out_wav, src_voice, dur, workdir)
        utils.write_json(meta_path, {"hash": digest, "duration": dur,
                                     "speech": round(speech, 3),
                                     "voice_key": shot["voice"].get("voice_key")})
        shot["audio"] = str(out_wav)

    target = float((plan.get("meta") or {}).get("target_duration") or 0.0)
    # 4.5) 真实语音比预估短 → 长版会在 TTS 后缩水；这里再扩一遍“已成音的镜头”（直接复制音轨，不再合成）
    if target >= 120 and total < target * 0.95:
        total = _append_fillers(plan, target, audio_dir,
                                Path(paths["shots_dir"]), workdir, total)
    plan["episode"]["total_duration"] = round(total, 3)
    plan["meta"]["speech_driven"] = True
    # 5) 自然语速超目标 → 裁掉尾部多余镜头（保留最后 1 镜）
    trimmed = _trim_to_target(plan, target)
    # trim 只缩短 b-roll（无台词）：把它们的静音轨按新时长重建，否则音画会整体错位
    for shot in plan["shots"]:
        if not shot.pop("_trimmed_duration", False):
            continue
        audio_path = Path(str(shot.get("audio") or ""))
        if not audio_path:
            continue
        _build_shot_audio(audio_path, None, float(shot.get("duration") or 0), workdir)
        utils.write_json(audio_path.with_suffix(".meta.json"),
                         {"hash": "trimmed", "duration": float(shot.get("duration") or 0),
                          "speech": 0.0, "voice_key": ""})
    if trimmed:
        plan["episode"]["total_duration"] = round(
            sum(float(s.get("duration") or 0) for s in plan["shots"]), 3)
    if prog:
        prog(16, f"语音驱动分镜：{len(plan['shots'])} 镜 / "
                 f"{plan['episode']['total_duration']:.2f}s")
    return {"shots": len(plan["shots"]), "trimmed": trimmed,
            "duration": plan["episode"]["total_duration"],
            "voices": meta.get("voices")}


def _build_shot_audio(out_wav: Path, voice: Path | None, duration: float,
                      workdir: Path) -> None:
    utils.ensure_dir(out_wav.parent)
    args: list[str] = []
    fc: list[str] = []
    if voice and Path(voice).is_file():
        args += ["-i", str(voice)]
        delay = int(round(LEAD_SEC * 1000))
        fc.append(f"[0:a]aresample={config.SAMPLE_RATE},pan=stereo|c0=c0|c1=c0,"
                  f"adelay={delay}|{delay},apad=whole_dur={duration:.3f},"
                  f"atrim=0:{duration:.3f},afade=t=out:st={max(0.0, duration - 0.06):.3f}:d=0.06[a]")
    else:
        args += ["-f", "lavfi", "-i", f"anullsrc=r={config.SAMPLE_RATE}:cl=stereo"]
        fc.append(f"[0:a]atrim=0:{duration:.3f},asetpts=N/SR/TB[a]")
    utils.ffmpeg(args + ["-filter_complex", ";".join(fc), "-map", "[a]",
                         "-t", f"{duration:.3f}", "-ar", str(config.SAMPLE_RATE),
                         "-ac", "2", "-c:a", "pcm_s16le", str(out_wav)], cwd=workdir)


def _expand_after_tts(plan: dict, target: float, audio_dir: Path, total: float) -> float:
    """长版：TTS 后按真实时长把镜头循环补到 target（音轨直接复制，不再调 TTS）。"""
    import shutil as _shutil

    base = list(plan["shots"])
    rounds = 0
    while total < target and base and rounds < 20:
        rounds += 1
        for src_shot in base:
            clone = dict(src_shot)
            clone["id"] = f"v5_x{len(plan['shots']) + 1:03d}"
            clone["act_id"] = f"{src_shot.get('act_id')}_p{rounds}"
            clone["act_name"] = f"{src_shot.get('act_name')} · 第{rounds + 1}遍"
            clone["camera"] = _vary_camera(src_shot.get("camera"), rounds + 1)
            src_audio = Path(str(src_shot.get("audio") or ""))
            if src_audio.is_file():
                dst = audio_dir / f"{clone['id']}.wav"
                _shutil.copy2(src_audio, dst)
                clone["audio"] = str(dst)
            plan["shots"].append(clone)
            total += float(clone.get("duration") or 0)
    for idx, shot in enumerate(plan["shots"], start=1):
        shot["no"] = idx
    LOG.info("长版扩写：%d 镜 / %.1fs（%d 遍）", len(plan["shots"]), total, rounds)
    return round(total, 3)


def _trim_to_target(plan: dict, target: float) -> list[str]:
    if not target:
        return []
    # 长版（≥120s）只允许 1% 溢出，保证“满 5 分钟”但不超太多；短模板保持原宽松度
    max_total = (target * 1.01 if target >= 120
                 else (max(target, 45.0) if target <= 60 else target * 1.15))
    shots = plan["shots"]
    trimmed: list[str] = []
    while sum(float(s.get("duration") or 0) for s in shots) > max_total and len(shots) > 3:
        # 先压 b-roll 时长（min 1.2s），不整镜删除；删镜会与 episode.json 不一致
        shrunk = False
        for s in shots:
            if s.get("type") == "broll" and float(s.get("duration") or 0) > 1.2:
                over = sum(float(x.get("duration") or 0) for x in shots) - max_total
                cut = min(float(s["duration"]) - 1.2, max(0.2, over))
                if cut > 0:
                    s["duration"] = round(float(s["duration"]) - cut, 2)
                    s["_trimmed_duration"] = True  # 音轨必须按新时长重建
                    shrunk = True
                    break
        if shrunk:
            continue
        idx = None
        for i in range(len(shots) - 2, 0, -1):
            if shots[i].get("type") == "broll":
                idx = i
                break
        if idx is None:
            if len(shots) <= 4:
                break
            idx = len(shots) - 2
        trimmed.append(str(shots[idx].get("id")))
        shots.pop(idx)
    if trimmed:
        for i, shot in enumerate(shots):
            shot["no"] = i + 1
        total = round(sum(float(s.get("duration") or 0) for s in shots), 3)
        plan["episode"]["total_duration"] = total
    return trimmed


# ---------------------------------------------------------------------------
# 风格卡片镜头（片头/片尾）
# ---------------------------------------------------------------------------
CARD_TITLE_DUR = 1.4
CARD_END_DUR = 1.6


def _add_card_shots(plan: dict, template: dict) -> None:
    """在 plan.shots 前后插入片头/片尾卡（type=card，无台词无角色）。"""
    shots = plan.get("shots") or []
    packaging = template.get("packaging") if isinstance(template.get("packaging"), dict) else {}
    title = str(packaging.get("title") or template.get("label") or "").strip()
    end = str(packaging.get("end_card") or packaging.get("end") or "下期见").strip()
    head = {
        "id": "v5_card_title", "no": 0, "type": "card", "card": "title",
        "act_id": "card", "act_name": "片头", "role": None, "character": None,
        "speaker_name": "", "scene": None, "camera": {"type": "static"},
        "characters": [], "duration": CARD_TITLE_DUR, "line": "",
        "title": title, "subtitle": str(template.get("label") or ""),
    }
    tail = {
        "id": "v5_card_end", "no": 0, "type": "card", "card": "end",
        "act_id": "card", "act_name": "片尾", "role": None, "character": None,
        "speaker_name": "", "scene": None, "camera": {"type": "static"},
        "characters": [], "duration": CARD_END_DUR, "line": "",
        "title": end, "subtitle": "",
    }
    if not any(str(x.get("type")) == "card" for x in shots):
        plan["shots"] = [head] + shots + [tail]
    for i, shot in enumerate(plan["shots"]):
        shot["no"] = i + 1
    plan["episode"]["total_duration"] = round(
        sum(float(x.get("duration") or 0) for x in plan["shots"]), 3)


def _render_card_clip(shot: dict, style: dict, paths: dict, out_mp4: Path) -> dict:
    """风格化卡片 PNG → 平滑缩放 mp4（1.4/1.6s，silent）。"""
    style_v5 = _require("style_v5")
    png = Path(paths["workdir"]) / "cards" / f"{shot['id']}.png"
    utils.ensure_dir(png.parent)
    kind = str(shot.get("card") or "title")
    if kind == "end":
        style_v5.end_card(style, str(shot.get("title") or ""), png,
                          (FINAL_W, FINAL_H), str(shot.get("subtitle") or ""))
    else:
        style_v5.title_card(style, str(shot.get("title") or ""), png,
                            (FINAL_W, FINAL_H), str(shot.get("subtitle") or ""))
    dur = float(shot.get("duration") or CARD_TITLE_DUR)
    frames = max(2, int(round(dur * FPS)))
    # 轻微推近：zoompan 1.0→1.04
    vf = (f"scale={FINAL_W * 2}:{FINAL_H * 2},"
          f"zoompan=z='1.0+0.04*on/{frames}':x='iw/2-(iw/zoom/2)':"
          f"y='ih/2-(ih/zoom/2)':d={frames}:s={FINAL_W}x{FINAL_H}:fps={FPS},"
          f"format=yuv420p")
    utils.ffmpeg(["-y", "-hide_banner", "-loglevel", "error", "-loop", "1",
                  "-i", str(png), "-vf", vf, "-t", f"{dur:.3f}",
                  "-c:v", "libx264", "-preset", "medium", "-crf", "18",
                  "-pix_fmt", "yuv420p", str(out_mp4)])
    return {"id": shot["id"], "path": str(out_mp4), "kind": kind,
            "duration": dur, "card": True, "png": str(png)}


def _pick_zone(shot: dict, layers: dict) -> tuple[str | None, list[float] | None]:
    """按镜头类型选 scene.camera_zones 取景窗。"""
    zones = (layers or {}).get("camera_zones") or {}
    if not zones:
        return None, None
    explicit = str(shot.get("camera_zone") or "")
    if explicit and explicit in zones:
        return explicit, zones[explicit]
    keys = list(zones.keys())
    low = {k.lower(): k for k in keys}
    wants = (("medium", "front", "speaker", "host", "close", "detail")
             if shot.get("line") else ("wide", "establish", "full", "master"))
    for want in wants:
        for k, orig in low.items():
            if want in k:
                return orig, zones[orig]
    first = keys[0]
    return first, zones[first]


# ---------------------------------------------------------------------------
# 2) 逐镜视觉渲染（场景 + 角色 + 2D 动画）
# ---------------------------------------------------------------------------
def _fit_anchor_in_zone(anchor, rect, *, margin_x=0.06, margin_top=0.10,
                        margin_bottom=0.06):
    """把角色落脚点收进 camera zone（normalized），防止角色被取景裁出画面。"""
    if not anchor or not rect:
        return anchor
    ax, ay = float(anchor[0]), float(anchor[1])
    x, y, w, h = [float(v) for v in rect]
    lo_x, hi_x = x + margin_x * w, x + (1.0 - margin_x) * w
    lo_y, hi_y = y + margin_top * h, y + (1.0 - margin_bottom) * h
    ax = max(lo_x, min(hi_x, ax))
    ay = max(lo_y, min(hi_y, ay))
    return [round(ax, 4), round(ay, 4)]


#: V5.4 景别 → 角色屏幕高度倍率（在 base_ratio 之上；让 WS/MS/MCU/OTS 有真实大小差异）
SIZE_CHAR_SCALE = {
    "EWS": 0.82, "WS": 0.92, "MS": 1.05, "MCU": 1.35,
    "CU": 1.6, "ECU": 1.8, "OTS": 1.18, "DETAIL": 1.0, "INSERT": 1.0,
}


def eget(m: dict, key: str, default=None):
    return (m or {}).get(key, default)


POINT_WORDS = ("这", "那", "看", "指", "地图", "这里", "那里", "我们", "你",
               "这面", "这条", "那个", "这个", "场上", "镜头", "前面")
THINK_WORDS = ("为什么", "怎么", "是否", "想", "觉得", "难", "问题", "能不能",
               "是不是", "到底", "凭什么", "怎么办", "原因", "答案")


def _pose_timeline(line: str, duration: float) -> list:
    """按台词给说话角色一个表演节拍：front → 手势/情绪 → front。"""
    text = str(line or "")
    if not text or duration < 1.2:
        return []
    if any(w in text for w in POINT_WORDS):
        act = "point"
    elif any(w in text for w in THINK_WORDS):
        act = "think"
    else:
        act = "lean_in"
    dur = float(duration)
    t0 = round(min(0.9, dur * 0.22), 2)
    t1 = round(min(dur - 0.7, t0 + max(1.2, dur * 0.28)), 2)
    if t1 <= t0 + 0.3:
        return []
    return [[0.0, t0, "front"], [t0, t1, act], [t1, dur, "front"]]


def _fit_character_in_zone(anchor, rect, char_height: float,
                           *, top_pad: float = 0.02, bottom_pad: float = 0.02):
    """让角色（脚点 anchor_y、身高 char_height）完整落在 camera zone 内。

    返回 (adjusted_anchor, adjusted_height)。身高会按可用空间缩小（最小 0.30）。
    """
    if not anchor or not rect:
        return anchor, char_height
    ax, ay = float(anchor[0]), float(anchor[1])
    x, y, w, h = [float(v) for v in rect]
    lo_y, hi_y = y + top_pad, y + h - bottom_pad
    ay = min(max(ay, lo_y + 0.05), hi_y)
    available = max(0.30, ay - (y + top_pad))
    out_h = float(char_height)
    if out_h > available:
        out_h = available
    if out_h < 0.30:
        out_h = 0.30
        ay = min(hi_y, y + top_pad + out_h)
    return [round(ax, 4), round(ay, 4)], round(out_h, 3)


def _edit_boundaries(shots: list[dict]) -> list[dict]:
    """逐剪辑点转场计划：body shots 用 editorial 的 transition_out，卡片用 fade。"""
    boundaries = []
    for i in range(len(shots) - 1):
        a, b = shots[i], shots[i + 1]
        a_edit = a.get("edit") or {}
        t = a_edit.get("transition_out") or {}
        is_card = str(a.get("type")) == "card" or str(b.get("type")) == "card"
        mode = "fade" if is_card else str(t.get("mode") or "cut")
        dur = 0.5 if is_card else float(t.get("duration") or 0.0)
        if mode == "cut":
            dur = 0.0
        boundaries.append({"from": str(a.get("id")), "to": str(b.get("id")),
                           "mode": mode, "duration": round(dur, 3)})
    return boundaries


def _scene_anchors(catalog: dict, plan: dict) -> dict:
    """{scene_id: anchors}（场景 JSON 的演员锚点，用于对话镜取景窗选择）。"""
    out = {}
    scene_table = (catalog or {}).get("scenes") or {}
    for shot in plan.get("shots") or []:
        sid = str(shot.get("scene") or "")
        if not sid or sid in out:
            continue
        sc = scene_table.get(sid) or {}
        anchors = sc.get("anchors")
        if isinstance(anchors, dict) and anchors:
            out[sid] = anchors
    return out


def _scene_zones(catalog: dict, plan: dict) -> dict:
    """{scene_id: camera_zones}（直接取场景 JSON，无需渲染）。"""
    out = {}
    scene_table = (catalog or {}).get("scenes") or {}
    for shot in plan.get("shots") or []:
        sid = str(shot.get("scene") or "")
        if not sid or sid in out:
            continue
        sc = scene_table.get(sid) or {}
        zones = sc.get("camera_zones")
        if isinstance(zones, dict) and zones:
            out[sid] = zones
    return out


def render_shot_clips(plan: dict, catalog: dict, style: dict, paths: dict,
                      prog, force: bool = False) -> dict:
    scene_render = _require("scene_render")
    characters_v5 = _require("characters_v5")
    motion2d = _require("motion2d")

    style_id = str((plan.get("episode") or {}).get("style") or "")
    style = _style(catalog, style_id) if style_id else style
    shots = plan.get("shots") or []
    shot_paths: dict[str, str] = {}
    metas: dict[str, dict] = {}
    for i, shot in enumerate(shots):
        sid = str(shot["id"])
        out_mp4 = Path(paths["shots_dir"]) / f"{sid}.mp4"
        dur = float(shot.get("duration") or 2.6)
        if shot.get("_prerendered") and out_mp4.is_file():
            shot_paths[sid] = str(out_mp4)
            metas[sid] = {"id": sid, "scene": shot.get("scene"),
                          "characters": [], "duration": dur, "fps": FPS,
                          "elapsed_sec": 0.0, "motion": {"prerendered": True},
                          "filler": shot.get("_filler")}
            continue
        if str(shot.get("type")) == "card":
            if prog and i % 2 == 0:
                prog(22 + 55.0 * i / max(1, len(shots)), f"逐镜动画 {i + 1}/{len(shots)}（卡片）")
            try:
                card_res = _render_card_clip(shot, style, paths, out_mp4)
            except Exception as exc:  # noqa: BLE001
                raise RenderV5Error(f"卡片 {sid} 渲染失败: {exc}") from exc
            shot_paths[sid] = str(out_mp4)
            metas[sid] = {"id": sid, "scene": None, "characters": [],
                          "duration": dur, "fps": FPS, "card": True,
                          "elapsed_sec": 0.0, "motion": card_res}
            continue
        scene = _scene(catalog, str(shot.get("scene") or ""))
        layers = scene_render.render_scene_layers(scene, style, (1600, 900),
                                                  seed=(i + 1) * 17)
        cam = dict(shot.get("camera") or {"type": "push_in"})
        edit = shot.get("edit") or {}
        zone_name, zone_rect = _pick_zone(shot, layers)
        edit_zone = edit.get("zone_rect")
        if edit_zone:
            zones_now = (layers or {}).get("camera_zones") or {}
            ezname = str(edit.get("zone") or "edit")
            if ezname in zones_now:
                zone_name, zone_rect = ezname, zones_now[ezname]
            else:
                zone_name, zone_rect = ezname, edit_zone
        if zone_rect:
            cam["zone"] = zone_rect
            shot["camera_zone_used"] = zone_name
        edit_cam = str(edit.get("camera") or "")
        if edit_cam:
            supported = getattr(motion2d, "SUPPORTED_CAMERA_TYPES", ())
            if not supported or edit_cam in supported:
                cam["type"] = edit_cam
        chars_spec = []
        for role in (shot.get("characters") or []):
            char_id = (shot.get("character") if role == shot.get("role")
                       else ((plan["meta"]["cast"].get(role) or {}).get("character")))
            if not char_id:
                continue
            char = _character(catalog, char_id)
            mouth = []
            if role == shot.get("role") and str(shot.get("line") or "").strip():
                shot_line = str(shot["line"])
                try:
                    from . import mouth_sync
                    audio_for_mouth = str(shot.get("audio") or "")
                    if audio_for_mouth:
                        mouth = mouth_sync.envelope_timeline(audio_for_mouth, FPS,
                                                             shot_line)
                except Exception as exc:  # noqa: BLE001
                    LOG.warning("音频口型失败（回退文本 viseme）: %s", exc)
                    mouth = []
                if not mouth:
                    try:
                        mouth = characters_v5.viseme_timeline(shot_line, dur,
                                                              lead=LEAD_SEC)
                    except Exception:  # noqa: BLE001
                        mouth = []
            anchor = (layers.get("anchors") or {}).get(role)
            zone_rect = cam.get("zone") if isinstance(cam, dict) else None
            if anchor:
                anchor = _fit_anchor_in_zone(anchor, zone_rect)
            zone_h = float(zone_rect[3]) if zone_rect else 1.0
            speaking = role == shot.get("role") and str(shot.get("line") or "").strip()
            base_ratio = 0.68 if speaking else 0.52
            char_height = max(0.40, min(0.92,
                                         base_ratio * max(0.55, min(1.0, zone_h))))
            size_scale = SIZE_CHAR_SCALE.get(str(edit.get("shot_size") or ""), 1.0)
            char_height = min(0.95, char_height * size_scale)
            anchor, char_height = _fit_character_in_zone(anchor, zone_rect, char_height)
            speaking = role == shot.get("role") and bool(str(shot.get("line") or "").strip())
            perform = str(os.environ.get("V55_PERFORM", "")).lower() in ("1", "true", "yes")
            entry = {"char": char, "role": role, "pose": "front",
                     "mouth_timeline": mouth, "anchor": anchor,
                     "scale": float(shot.get("scale") or 1.0),
                     "height": round(char_height, 3),
                     "mouth_strength": 0.85,
                     "fade_in": 8, "fade_out": 8,
                     "blink": True,
                     "idle_strength": 1.0 if perform else 0.0,
                     "gesture": perform,
                     "speaking": speaking}
            shot_line = str(shot.get("line") or "").strip()
            if perform and speaking and shot_line:
                pt = _pose_timeline(shot_line, dur)
                if pt:
                    entry["pose_timeline"] = pt
            # 首次出场（前 2 镜且有角色）从画面外走入，替代默认上浮
            first_appearance = i <= 1 and not any(
                str(x.get("character")) == str(char.get("id")) for x in
                [s0 for s0 in shots[:i] if s0.get("character")])
            if perform and first_appearance and not speaking and not eget(shot, "insert"):
                entry["entrance"] = "walk_in"
                entry["walk_in_from"] = ("left" if (anchor and anchor[0] < 0.5) else "right")
            chars_spec.append(entry)
        if prog and i % 2 == 0:
            prog(22 + 55.0 * i / max(1, len(shots)), f"逐镜动画 {i + 1}/{len(shots)}")
        t0 = time.time()
        _shot_kwargs = dict(width=FINAL_W, height=FINAL_H, seed=(i + 1) * 31,
                            workdir=paths["workdir"])
        if "mouth_strength" in inspect.signature(motion2d.render_shot).parameters:
            _shot_kwargs["mouth_strength"] = 0.85
        res = motion2d.render_shot(layers, chars_spec, cam, dur, FPS, out_mp4, style,
                                   **_shot_kwargs)
        shot_paths[sid] = str(out_mp4)
        metas[sid] = {"id": sid, "scene": shot.get("scene"),
                      "characters": [c["char"].get("id") for c in chars_spec],
                      "duration": dur, "fps": FPS,
                      "elapsed_sec": round(time.time() - t0, 2),
                      "motion": res if isinstance(res, dict) else {}}
        LOG.info("镜头 %s 渲染完成（%.1fs）：%s", sid, time.time() - t0, out_mp4)
    return {"shot_paths": shot_paths, "metas": metas, "style": style_id}


# ---------------------------------------------------------------------------
# 3) 拼装（视频/字幕/混音/编码）
# ---------------------------------------------------------------------------
def assemble(plan: dict, catalog: dict, style: dict, paths: dict, prog) -> dict:
    from . import ass_builder, fonts as fonts_mod
    timeline_mod = _require("timeline")
    style_v5 = _require("style_v5")

    shots = plan.get("shots") or []
    total = round(sum(float(s.get("duration") or 0) for s in shots), 3)
    clip_paths = {str(s["id"]): Path(paths["shots_dir"]) / f"{s['id']}.mp4" for s in shots}
    if prog:
        prog(82, f"拼接视频（{len(shots)} 镜 / {total:.2f}s）")
    # V5.4 剪辑：每个剪辑点独立转场（editing.xfade_chain）；不可用时回退原单转场
    boundaries = _edit_boundaries(shots)
    video_raw = None
    transition = {"mode": "editorial", "style": "per-cut"}
    editing_mod = None
    try:
        from . import editing as editing_mod  # type: ignore
        video_raw = editing_mod.xfade_chain(
            shots, clip_paths, paths["workdir"], FPS, FINAL_W, FINAL_H, total,
            boundaries, prog=prog)
        loss = round(sum(float(b.get("duration") or 0.0) for b in boundaries
                         if str(b.get("mode")) != "cut"), 3)
        render_total = round(total - loss, 6)
        plan.setdefault("meta", {})["transition_loss"] = loss
        plan["meta"]["_render_total"] = render_total
        plan["episode"]["total_duration"] = round(render_total, 3)
        cursor = 0.0
        for i, sh in enumerate(shots):
            sh["_edit_start"] = round(cursor, 3)
            if i < len(boundaries):
                b = boundaries[i]
                cursor += float(sh.get("duration") or 0.0) - (
                    float(b.get("duration") or 0.0) if str(b.get("mode")) != "cut" else 0.0)
        LOG.info("V5.4 剪辑转场：%d 个剪辑点，转场损耗 %.2fs，成片 %.2fs",
                 len(boundaries), loss, render_total)
        utils.write_json(Path(paths["workdir"]) / "episode.json", plan)
    except Exception as exc:  # noqa: BLE001
        LOG.warning("V5.4 editing 不可用，回退单转场: %s", exc)
        editing_mod = None
        video_raw = None
    if video_raw is None:
        transition = ("fade", "fade")
        try:
            transition = style_v5.transition(style)
        except Exception:  # noqa: BLE001
            pass
        t_mode = str(transition[0] if transition else "fade")
        t_style = str(transition[1] if len(transition or ()) > 1 else "fade")
        if t_mode == "cut" or len(shots) <= 1:
            video_raw = render_mod.concat_video(shots, paths["workdir"], FPS, total,
                                                clip_paths=clip_paths)
        else:
            video_raw = render_mod.concat_video_xfade(
                shots, clip_paths, paths["workdir"], FPS, FINAL_W, FINAL_H, total,
                transition=t_style, transition_duration=TRANSITION_DUR, prog=prog)
        transition = {"mode": transition[0], "style": transition[1]}
    # 关键：final_encode 读取 workdir/video_raw.mp4；把本次拼好的视频同步到该文件名，
    # 避免使用上一轮遗留的 video_raw.mp4（否则 per-cut 转场不会进入成片）。
    import shutil as _shutil
    target = Path(paths["workdir"]) / "video_raw.mp4"
    try:
        if Path(video_raw).resolve() != target.resolve():
            _shutil.copy2(video_raw, target)
        LOG.info("video_raw 同步: %s -> %s", video_raw, target)
    except Exception as exc:  # noqa: BLE001
        # 不能吞：final_encode 固定读 workdir/video_raw.mp4，吞掉就会用上一轮的旧画面出片
        raise RenderV5Error(f"video_raw 同步失败（拒绝用旧素材出片）: {exc}") from exc
    if not target.is_file() or target.stat().st_size <= 0:
        raise RenderV5Error(f"video_raw 缺失或为空: {target}")
    LOG.info("video_raw: %s (%.2fs)", video_raw, utils.probe_duration(video_raw))

    # 字幕：优先风格化 subtitle_ass，否则 ass_builder
    if prog:
        prog(87, "生成字幕/人名条")
    timings = []
    cursor = 0.0
    for shot in shots:
        dur = float(shot.get("duration") or 0)
        start = float(shot.get("_edit_start", cursor))
        timings.append({"shot": shot, "start": start, "end": start + dur,
                        "duration": dur})
        cursor = start + dur
    ass_path = Path(paths["workdir"]) / "final.ass"
    events = []
    for item in timings:
        shot = item["shot"]
        line = str(shot.get("line") or "").strip()
        if not line:
            continue
        start = round(item["start"] + LEAD_SEC, 3)
        end = round(min(item["end"] - 0.05,
                        item["start"] + shot.get("speech_duration", 0) + 0.5), 3)
        events.append({"start": start, "end": max(start + 0.4, end),
                       "text": line, "speaker": shot.get("speaker_name") or ""})
    # 综艺花字字幕（主字幕 + 花字 + 人名条 + 空镜标签）
    from . import variety_subs  # noqa: PLC0415
    sub_shots = []
    for item in timings:
        shot = item["shot"]
        sub_shots.append({"id": shot.get("id"), "line": str(shot.get("line") or ""),
                          "speaker_name": str(shot.get("speaker_name") or ""),
                          "scene": str(shot.get("scene") or ""),
                          "start": float(item["start"]), "end": float(item["end"]),
                          "filler": bool(shot.get("_prerendered"))})
    sub_info = variety_subs.build_ass(sub_shots, ass_path, FINAL_W, FINAL_H, style=style)
    LOG.info("综艺字幕：%s 条（对白 %s / 花字 %s）", sub_info.get("count"),
             sub_info.get("dialogue"), sub_info.get("punch"))
    narration = None
    if editing_mod is not None:
        try:
            cuts = [{"shot": str(s.get("id")),
                     "j_cut": float((s.get("edit") or {}).get("j_cut") or 0.0),
                     "l_cut": float((s.get("edit") or {}).get("l_cut") or 0.0)}
                    for s in shots]
            if any(c["j_cut"] > 0 or c["l_cut"] > 0 for c in cuts):
                shots_started = [dict(s, start=float(s.get("_edit_start", 0.0)))
                                 for s in shots]
                narration = editing_mod.mix_audio_edit(
                    shots_started, paths["workdir"],
                    plan["meta"].get("_render_total") or total, cuts)
        except Exception as exc:  # noqa: BLE001
            LOG.warning("J/L cut 混音失败（回退直线混音）: %s", exc)
            narration = None
    render_total = plan["meta"].get("_render_total") or total
    if narration is None:
        narration = render_mod.concat_audio(shots, paths["workdir"], render_total)
    # final_encode/render_mix_wav 固定读 audio/narration.wav；把本次混音结果同步过去，
    # 避免新模板（无历史缓存）最终编码报 "Error opening input file audio/narration.wav"。
    import shutil as _shutil
    narr_target = Path(paths["workdir"]) / "audio" / "narration.wav"
    try:
        if narration and Path(narration).is_file() and \
                Path(narration).resolve() != narr_target.resolve():
            utils.ensure_dir(narr_target.parent)
            _shutil.copy2(narration, narr_target)
            LOG.info("narration 同步: %s -> %s", narration, narr_target)
            narration = str(narr_target)
    except Exception as exc:  # noqa: BLE001
        raise RenderV5Error(f"narration 同步失败（拒绝用旧音轨出片）: {exc}") from exc
    bgm = None
    try:
        from . import v5_audio
        bgm = v5_audio.ensure_bgm(style, paths["workdir"])
    except Exception as exc:  # noqa: BLE001
        LOG.warning("V5 BGM 生成失败，回退 V4 BGM: %s", exc)
        bgm = render_mod.ensure_bgm(paths["workdir"])
    grade = ""
    packaging = ""
    try:
        grade = style_v5.grade_filter(style)
        packaging = style_v5.packaging_filters(
            style, paths["workdir"], FINAL_W, FINAL_H,
            fonts_mod.ensure_fonts(paths["workdir"])["body"]["file"])
    except Exception as exc:  # noqa: BLE001
        LOG.warning("V5 调色/包装失败（跳过）: %s", exc)
    if prog:
        prog(90, "编码成片（字幕/调色/包装/混音）")
    render_mod.final_encode(
        paths["workdir"], paths["out"], render_total, FPS, FINAL_W, FINAL_H, bgm,
        has_ass=True, prog=prog, pct0=90, pct1=99,
        grade_filter=grade or None,
        packaging_filter=packaging or None,
        duck_bgm=True)
    # 长版（≥120s）：转场重叠会让成片比目标短（66 个转场 ≈ 16s），编码后补齐到目标时长
    long_target = float((plan.get("meta") or {}).get("target_duration") or 0.0)
    if long_target >= 120:
        render_total = _conform_duration(Path(paths["out"]), long_target, Path(paths["workdir"]))
        plan["episode"]["total_duration"] = round(render_total, 3)
        plan["meta"]["_render_total"] = render_total
    # timeline 产物
    tl = _build_timeline(plan, style, paths, "editorial", "per-cut")

    timeline_art = timeline_mod.write_timeline(tl, paths["out_dir"])
    return {"video_raw": str(video_raw), "ass": str(ass_path),
            "narration": str(narration), "bgm": bool(bgm),
            "transition": transition if isinstance(transition, dict)
            else {"mode": str(transition[0]), "style": str(transition[1])},
            "boundaries": boundaries,
            "timeline": timeline_art}


def _append_fillers(plan: dict, target: float, audio_dir: Path, shots_dir: Path,
                    workdir: Path, total: float) -> float:
    """零重复长版：用该模板自己的素材生成**新的** b-roll 镜（不同取景/运镜/调色），
    不再循环复用已有镜头。填充镜预渲染到 shots_dir，render_shot_clips 会跳过它们。"""
    from . import unique_fill  # noqa: PLC0415

    tid = str(plan.get("meta", {}).get("template") or plan["episode"]["id"])
    template = (plan.get("meta", {}).get("template_contract") or {"id": tid})
    need = max(0.0, target * 1.01 - total)
    fillers = unique_fill.make_fillers(tid, template, need, shots_dir, seed=int(plan["meta"].get("seed") or 7))
    scene_pool = [str(s.get("scene")) for s in plan["shots"] if s.get("scene")] or ["stage"]
    # 章节卡：把空镜分成 4 段，段间插一张「第 N 章 · 幕名」卡（综艺段落感）
    acts = [a for a in ((template or {}).get("acts") or []) if isinstance(a, dict)]
    try:
        from . import v5_catalog as _v5c  # noqa: PLC0415
        _scenes = _v5c.load_catalog().get("scenes") or {}
    except Exception:  # noqa: BLE001
        _scenes = {}

    def _scene_label(scene_id: str) -> str:
        scene = _scenes.get(str(scene_id)) or {}
        return str(scene.get("label") or scene_id or "")

    # 章节名优先用场景中文标签（第1章 · 霓虹天际线），拿不到再退回幕名
    chapter_names = []
    for act in acts:
        label = _scene_label(str(act.get("scene") or ""))
        chapter_names.append(label or str(act.get("name") or act.get("id") or ""))
    chapter_names = [n for n in chapter_names if n][:3]
    chapter_at = {max(1, len(fillers) * k // 4) for k in (1, 2, 3)} if len(acts) >= 3 else set()
    chapter_idx = 0
    for idx, item in enumerate(fillers):
        if idx in chapter_at and chapter_idx < len(chapter_names):
            chapter_idx += 1
            card_id = f"v5_ch{chapter_idx}"
            card = {"id": card_id, "no": len(plan["shots"]) + 1, "type": "card",
                    "card": "title", "act_id": "chapter", "act_name": "章节",
                    "role": None, "character": None, "speaker_name": "",
                    "scene": None, "camera": {"type": "static"}, "characters": [],
                    "duration": 2.2, "line": "",
                    "title": f"第{chapter_idx}章 · {chapter_names[chapter_idx - 1]}",
                    "subtitle": ""}
            card_wav = audio_dir / f"{card_id}.wav"
            _build_shot_audio(card_wav, None, 2.2, workdir)
            card["audio"] = str(card_wav)
            plan["shots"].append(card)
            total += 2.2
        sid = item["id"]
        shot = {"id": sid, "no": len(plan["shots"]) + 1, "type": "broll",
                "act_id": "fill", "act_name": "空镜补充",
                "scene": scene_pool[idx % len(scene_pool)],
                "camera": {"type": "push_in", "strength": 0.12},
                "role": None, "character": None, "speaker_name": "",
                "characters": [], "duration": float(item["duration"]),
                "line": "", "speech_duration": 0.0,
                "_prerendered": True, "_filler": item}
        out_wav = audio_dir / f"{sid}.wav"
        _build_shot_audio(out_wav, None, float(item["duration"]), workdir)
        shot["audio"] = str(out_wav)
        shot["voice"] = {"provider": "", "character": "", "voice_id": "", "voice_key": "",
                         "seed": None, "ref": "", "text": "", "speech_duration": 0.0}
        plan["shots"].append(shot)
        total += float(item["duration"])
    for idx, shot in enumerate(plan["shots"], start=1):
        shot["no"] = idx
    LOG.info("长版空镜：新增 %d 条不重复镜头 / %.1fs", len(fillers), total)
    return round(total, 3)


def _conform_duration(path: Path, target: float, workdir: Path) -> float:
    """把成片精确拉到 target 秒：短了用末帧定格补（最多 40s），长了直接裁。"""
    actual = float(utils.probe_duration(path) or 0.0)
    if actual <= 0 or abs(actual - target) < 0.2:
        return actual
    tmp = path.with_name(path.stem + "_conform.mp4")
    if actual < target:
        pad = min(40.0, target - actual + 0.05)
        utils.ffmpeg(["-i", str(path),
                      "-vf", f"tpad=stop_mode=clone:stop_duration={pad:.3f},fps=25,format=yuv420p",
                      "-af", f"apad=whole_dur={target:.3f}",
                      "-t", f"{target:.3f}",
                      "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
                      "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2",
                      "-movflags", "+faststart", "-y", str(tmp)])
    else:
        utils.ffmpeg(["-i", str(path), "-t", f"{target:.3f}",
                      "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
                      "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2",
                      "-movflags", "+faststart", "-y", str(tmp)])
    if tmp.is_file() and tmp.stat().st_size > 0:
        tmp.replace(path)
    final = float(utils.probe_duration(path) or 0.0)
    LOG.info("长版对齐：%.2fs -> %.2fs（目标 %.1fs）", actual, final, target)
    return final


def _build_timeline(plan: dict, style: dict, paths: dict, t_mode: str,
                    t_style: str) -> dict:
    shots = plan.get("shots") or []
    loss = float((plan.get("meta") or {}).get("transition_loss") or 0.0)
    total = round(sum(float(s.get("duration") or 0) for s in shots) - loss, 3)
    video, voice, subs = [], [], []
    cursor = 0.0
    for shot in shots:
        sid = str(shot["id"])
        dur = round(float(shot.get("duration") or 0), 3)
        start = float(shot.get("_edit_start", cursor))
        edit = shot.get("edit") or {}
        video.append({"id": sid, "src": str((Path(paths["shots_dir"]) / f"{sid}.mp4").resolve()),
                      "in": 0.0, "out": dur, "at": round(start, 3),
                      "kind": "broll" if not shot.get("line") else "dialogue",
                      "scene": shot.get("scene"), "role": shot.get("role"),
                      "shot_size": edit.get("shot_size"),
                      "camera": edit.get("camera") or (shot.get("camera") or {}).get("type"),
                      "transition": edit.get("transition_out") or {
                          "mode": t_mode, "style": t_style, "duration": TRANSITION_DUR}})
        if shot.get("line"):
            voice.append({"id": f"v_{sid}", "src": str(Path(shot.get("audio", "")).resolve()),
                          "at": round(start, 3), "out": dur,
                          "lead_sec": LEAD_SEC, "speech_duration": shot.get("speech_duration"),
                          "provider": "breeze", "character": shot.get("character"),
                          "text": shot.get("line")})
            subs.append({"id": f"s_{sid}", "text": shot.get("line"),
                         "at": round(start + LEAD_SEC, 3),
                         "out": round(start + dur - 0.05, 3),
                         "speaker": shot.get("speaker_name")})
        cursor = start + dur
    work = Path(paths["workdir"])
    return {
        "version": 5, "template": (plan.get("episode") or {}).get("id"),
        "label": (plan.get("episode") or {}).get("label"),
        "mode": "v5_motion2d", "fps": FPS, "resolution": [FINAL_W, FINAL_H],
        "duration": total,
        "style": {"id": style.get("id"), "label": style.get("label"),
                  "technique": style.get("technique"),
                  "palette": style.get("palette"),
                  "transition": {"type": t_mode, "style": t_style,
                                 "duration": TRANSITION_DUR}},
        "tracks": [
            {"id": "V1", "kind": "video", "clips": video},
            {"id": "A1", "kind": "voice", "clips": voice},
            {"id": "A2", "kind": "music", "clips": [
                {"id": "bgm", "src": str((work / "bgm" / ("v5_bgm.wav")).resolve()),
                 "at": 0.0, "out": total, "gain": BGM_GAIN,
                 "duck": {"enabled": True, "threshold": 0.035, "ratio": 6,
                          "attack": 15, "release": 350},
                 "fade_in": 1.5, "fade_out": 3.0}]},
            {"id": "S1", "kind": "subtitle", "clips": subs},
        ],
        "editing": {"mode": "per-cut",
                    "boundaries": _edit_boundaries(shots),
                    "cuts": [{"shot": str(sh.get("id")),
                              "j_cut": (sh.get("edit") or {}).get("j_cut", 0),
                              "l_cut": (sh.get("edit") or {}).get("l_cut", 0)}
                             for sh in shots
                             if (sh.get("edit") or {}).get("j_cut")
                             or (sh.get("edit") or {}).get("l_cut")],
                    "summary": (plan.get("meta") or {}).get("edit") or {}},
        "meta": {"speech_driven": True, "voice_provider": "breeze",
                 "lead_sec": LEAD_SEC, "tail_sec": TAIL_SEC,
                 "scenes": sorted({str(s.get("scene")) for s in shots if s.get("scene")}),
                 "characters": sorted({str(s.get("character")) for s in shots if s.get("character")})},
    }


# ---------------------------------------------------------------------------
# 4) QC + 产物
# ---------------------------------------------------------------------------
def _duration_gate(target: float, actual: float, *, ratio: float | None = None,
                   shots: int = 0) -> dict:
    """时长门：期望/实际/差值/下限一次算清，既能 raise 也能直接进报告。"""
    target = float(target or 0.0)
    actual = float(actual or 0.0)
    r = float(MIN_DURATION_RATIO if ratio is None else ratio)
    try:  # 允许用环境变量放宽（例如 5s 也合规的短模板）
        r = float(os.environ.get("V5_MIN_DURATION_RATIO") or r)
    except (TypeError, ValueError):
        pass
    floor = round(target * r, 3)
    return {"target": round(target, 3), "actual": round(actual, 3),
            "floor": floor, "ratio": round(r, 3),
            "delta": round(actual - target, 3),
            "short_by": round(max(0.0, target - actual), 3),
            "shots": int(shots),
            "ok": bool(target > 0 and actual >= floor),
            "detail": (f"成片 {actual:.2f}s / 目标 {target:.2f}s"
                       f"（下限 {floor:.2f}s，差 {actual - target:+.2f}s）")}


def run_qc(plan: dict, style: dict, paths: dict, prog=None) -> dict:
    from . import qc_v5
    dialogue = [s for s in plan.get("shots") or [] if str(s.get("line") or "").strip()]
    no_speech = [str(s.get("id")) for s in dialogue
                 if float(s.get("speech_duration") or 0) <= 0.2]
    # 口型真检查：对白镜的 viseme 时间轴里必须有≥5% 的张嘴段（coverage），否则等于没驱动
    per_shot = []
    low_cov = []
    failures: list[dict] = []
    try:
        from . import mouth_sync as _mouth_sync  # noqa: PLC0415
    except Exception as exc:  # noqa: BLE001
        _mouth_sync = None
        failures.append({"shot": "*", "reason": "mouth_sync_import_failed",
                         "error": f"{type(exc).__name__}: {exc}"[-200:]})
    for shot in dialogue:
        sid = str(shot.get("id"))
        audio_path = Path(str(shot.get("audio") or ""))
        if _mouth_sync is None:
            continue
        if not audio_path.is_file():
            failures.append({"shot": sid, "reason": "audio_missing",
                             "audio": str(audio_path)})
            continue
        # envelope_timeline 会抛（非 pcm_s16le wav 等）→ 不能让它把整条 run 判失败
        try:
            timeline = _mouth_sync.envelope_timeline(
                audio_path, config.FPS, str(shot.get("line") or ""), lead=LEAD_SEC)
            window = max(0.2, float(shot.get("duration") or 0.2))
            cov = _mouth_sync.coverage_ratio(timeline, 0.0, window)
        except Exception as exc:  # noqa: BLE001
            failures.append({"shot": sid, "reason": "envelope_failed",
                             "error": f"{type(exc).__name__}: {exc}"[-300:]})
            continue
        per_shot.append({"shot": sid, "coverage": round(cov, 3)})
        if cov < 0.05:
            low_cov.append(sid)
    # fail-open 修正：有对白镜但一行口型数据都没有 → ok=False + reason="no_data"
    reason = ""
    if no_speech:
        reason = "no_speech"
    elif failures:
        reason = "no_data" if not per_shot else "partial_data"
    elif low_cov:
        reason = "low_coverage"
    elif dialogue and not per_shot:
        reason = "no_data"
    lip_ok = not reason
    qc_extra = {"style": {"style": style.get("id"), "label": style.get("label")},
                "fallbacks": [],
                # 口型是 motion2d 的 viseme 时间轴驱动：对白镜必须有 speech_duration
                "lip_sync": {"ok": lip_ok, "provider": "viseme", "reason": reason,
                             "detail": (
                                 f"{len(per_shot) or len(dialogue)} 个对白镜 viseme 覆盖达标"
                                 if lip_ok else
                                 f"口型异常[{reason or 'unknown'}]：缺语音时长 {no_speech}；"
                                 f"张嘴覆盖过低 {low_cov}；取数失败 {len(failures)} 镜"),
                             "shots": len(dialogue), "no_speech": no_speech,
                             "low_coverage": low_cov, "per_shot": per_shot,
                             "failures": failures}}
    report = qc_v5.evaluate(video=paths["out"], plan=plan,
                            qc_extra=qc_extra,
                            subtitles=Path(paths["workdir"]) / "final.ass")
    report["style"] = {"id": style.get("id"), "label": style.get("label"),
                       "technique": style.get("technique"),
                       "palette": style.get("palette")}
    qc_v5.write_report(report, Path(paths["out_dir"]) / "qc_report.json")
    return report


def extract_frames(video: Path, paths: dict, total: float, count: int = 8) -> dict:
    frames_dir = utils.ensure_dir(paths["frames_dir"])
    times = [round((i + 0.5) * max(0.1, total) / count, 3) for i in range(count)]
    samples = []
    for i, t in enumerate(times, start=1):
        out = frames_dir / f"frame_{i:02d}.png"
        try:
            utils.ffmpeg(["-ss", f"{t:.3f}", "-i", str(video), "-frames:v", "1",
                          "-q:v", "2", "-y", str(out)])
            if out.is_file():
                samples.append(str(out))
        except Exception as exc:  # noqa: BLE001
            LOG.warning("抽帧失败 t=%.2f: %s", t, exc)
    sheet = paths["out_dir"] / "contact_sheet.png"
    cols = min(4, max(1, len(samples)))
    rows = max(1, math.ceil(len(samples) / cols))
    try:
        utils.ffmpeg(["-framerate", "1", "-start_number", "1",
                      "-i", str(frames_dir / "frame_%02d.png"), "-frames:v", "1",
                      "-vf", f"scale=320:180,tile={cols}x{rows}:padding=6:margin=6",
                      "-y", str(sheet)])
    except Exception:  # noqa: BLE001
        sheet = None
    return {"frames": samples, "sheet": str(sheet) if sheet else ""}


# ---------------------------------------------------------------------------
# 5) 主入口
# ---------------------------------------------------------------------------
def run_render(args) -> int:
    t0 = time.time()
    template_id = str(_arg(args, "template", "") or "")
    duration = float(_arg(args, "duration", 40) or 40)
    force = bool(_arg(args, "force", False))
    seed = int(_arg(args, "seed", 20261101) or 20261101)
    prog = _arg(args, "prog")
    if prog is None:
        def prog(pct, msg=""):
            print(f"PROGRESS {int(pct)} {msg}", flush=True)

    catalog = _load_catalog()
    template = _template(catalog, template_id)
    paths = resolve_paths(args, template_id)
    _ensure_dirs(paths)
    prog(1, f"V5 渲染：{template_id} / {template.get('style')} / 目标 {duration:.0f}s")
    from . import v5_story
    plan = v5_story.build_plan(template, duration, seed=seed)
    plan["meta"]["template"] = template_id
    plan["meta"]["template_contract"] = template
    # 片头/片尾卡（风格化包装；不参与 TTS，固定时长）
    _add_card_shots(plan, template)
    utils.write_json(paths["workdir"] / "episode.json", plan)
    prog(3, f"分镜：{len(plan['shots'])} 镜（场景 {len({s.get('scene') for s in plan['shots']})}）")

    info = tts_and_pace(plan, catalog, paths, prog, force=force)
    LOG.info("TTS/配速：%s", info)
    utils.write_json(paths["workdir"] / "episode.json", plan)
    # TTS 完成后释放显存（V5 渲染为 CPU 2D 动画，不需要 GPU 常驻）
    if str(os.environ.get("BREEZE_TTS_KEEP", "")).lower() not in ("1", "true", "yes"):
        try:
            from . import tts_breeze
            tts_breeze.stop_server()
            LOG.info("已停止 Breeze TTS 服务（释放显存）")
        except Exception as exc:  # noqa: BLE001
            LOG.warning("停止 Breeze TTS 服务失败: %s", exc)

    style = _style(catalog, str(plan["episode"].get("style") or template.get("style") or ""))
    # V5.4 剪辑计划（每模板不同：景别/运镜/取景/转场/J-L cut）
    try:
        from . import editorial
        zones = _scene_zones(catalog, plan)
        anchors = _scene_anchors(catalog, plan)
        from . import motion2d as _motion2d
        edit_plan = editorial.build_edit_plan(
            template, plan, style, seed=seed, zones_by_scene=zones,
            anchors_by_scene=anchors,
            supported_cameras=getattr(_motion2d, "SUPPORTED_CAMERA_TYPES", ()))
        utils.write_json(paths["workdir"] / "edit_plan.json", {
            "summary": edit_plan["summary"], "shots": edit_plan["shots"]})
        utils.write_json(paths["workdir"] / "episode.json", plan)
        LOG.info("V5.4 剪辑：%s", edit_plan["summary"].get("signature"))
    except Exception as exc:  # noqa: BLE001
        LOG.warning("V5.4 剪辑计划失败（回退原节奏）：%s", exc)
        edit_plan = None

    prog(20, f"逐镜 2D 动画渲染（{len(plan['shots'])} 镜，风格 {style.get('label')}）")
    clips = render_shot_clips(plan, catalog, style, paths, prog, force=force)
    utils.write_json(paths["workdir"] / "shot_meta.json", clips["metas"])

    asm = assemble(plan, catalog, style, paths, prog)
    # V5.4 分镜表（json/html/png）
    storyboard_art = {}
    try:
        from . import storyboard_v5
        sb = storyboard_v5.build_storyboard(plan, paths)
        storyboard_art = storyboard_v5.write_storyboard(sb, Path(paths["out_dir"]))
        LOG.info("V5.4 分镜表：%s", storyboard_art.get("json"))
    except Exception as exc:  # noqa: BLE001
        LOG.warning("分镜表生成失败: %s", exc)
    if not Path(paths["out"]).is_file():
        raise RenderV5Error(f"成片未生成: {paths['out']}")
    probe = utils.probe_video(paths["out"])
    # 时长硬门（P1-9）：`--duration` 是下限，不是等号。
    # custom_holo_talk --duration 30 实际只出 7.8s 时 QC 会拿 7.8 比 7.8 自证全绿 → 必须在编码后拦一次。
    gate = _duration_gate(duration, float(probe.get("duration") or 0.0),
                            shots=len(plan.get("shots") or []))
    utils.write_json(Path(paths["out_dir"]) / "duration_gate.json", gate)
    plan.setdefault("meta", {})["duration_gate"] = gate
    if not gate["ok"]:
        msg = (f"成片时长不足：{gate['actual']:.2f}s < 目标 {gate['target']:.1f}s × "
               f"{gate['ratio']:.2f} = {gate['floor']:.2f}s（差 {gate['delta']:.2f}s，"
               f"镜头 {gate['shots']} 个）")
        LOG.error("%s", msg)
        utils.emit_json_line({"ok": False, "error": "duration_below_floor", **gate})
        raise RenderV5Error(msg)
    LOG.info("时长门通过：实际 %.2fs / 目标 %.1fs（下限 %.2fs）",
             gate["actual"], gate["target"], gate["floor"])
    prog(99, "QC + 抽帧")
    qc = run_qc(plan, style, paths, prog)
    frames = extract_frames(paths["out"], paths, float(probe["duration"]))
    elapsed = round(time.time() - t0, 1)
    summary = {
        "ok": bool(qc["ok"]), "template": template_id,
        "label": template.get("label"), "style": style.get("id"),
        "style_label": style.get("label"),
        "path": str(paths["out"]), "out_dir": str(paths["out_dir"]),
        "duration": probe["duration"],
        "expected_duration": plan["episode"]["total_duration"],
        "resolution": f"{probe['width']}x{probe['height']}", "fps": probe["fps"],
        "shots": len(plan["shots"]),
        "dialogue_shots": sum(1 for s in plan["shots"] if s.get("line")),
        "scenes": sorted({str(s.get("scene")) for s in plan["shots"]}),
        "characters": sorted({str(s.get("character")) for s in plan["shots"] if s.get("character")}),
        "qc_ok": qc["ok"],
        "qc_failed": [k for k, v in qc["checks"].items() if not v.get("ok")],
        "duration_gate": gate,
        "qc_report": str(paths["out_dir"] / "qc_report.json"),
        "timeline": asm.get("timeline"),
        "storyboard": storyboard_art,
        "edit": (plan.get("meta") or {}).get("edit") or {},
        "transition": asm.get("transition"),
        "contact_sheet": frames.get("sheet", ""),
        "elapsed_sec": elapsed,
    }
    utils.write_json(paths["out_dir"] / "style_report.json", {
        "template": template_id, "style": style.get("id"),
        "label": style.get("label"), "technique": style.get("technique"),
        "palette": style.get("palette"), "transition": asm.get("transition"),
        "edit": (plan.get("meta") or {}).get("edit") or {},
        "storyboard": storyboard_art,
        "scenes": summary["scenes"], "characters": summary["characters"],
        "shots": [{"id": s["id"], "scene": s.get("scene"),
                   "character": s.get("character"), "duration": s.get("duration")}
                  for s in plan["shots"]],
    })
    utils.write_json(paths["workdir"] / "manifest.json", summary)
    prog(100, f"完成：{paths['out']} / QC {'PASS' if qc['ok'] else 'FAIL'}")
    utils.emit_json_line(summary)
    return 0 if qc["ok"] else 1


def main(args) -> int:
    try:
        return run_render(args)
    except Exception as exc:  # noqa: BLE001
        LOG.error("render_v5 失败: %s", exc)
        LOG.debug("%s", __import__("traceback").format_exc())
        utils.emit_json_line({"ok": False, "error": str(exc)})
        return 1
