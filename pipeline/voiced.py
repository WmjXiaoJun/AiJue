# -*- coding: utf-8 -*-
"""V4 语音编排：Breeze TTS 2 角色化配音 + 语音驱动镜头时长（无 atempo）。

核心变化（相对 V3）：
1) 台词先合成（Breeze TTS 2 Voice Design，按角色），自然语速；
2) 每个对话镜时长 = snap_up_h3(语音时长 + LEAD + TAIL)，H3 合法长度 = 17k+5 帧 @24fps
   （124/141/158/.../362 帧 = 5.17~15.08s）；超长台词在标点处切分为多镜；
3) 不再用 atempo 把台词压进固定 5.17s 镜头，避免加速/截断；
4) 每镜音轨 = 自然语速 voice（LEAD 偏移）+ 静音补齐，供 H3 audio_ref 与成片混音共用。

镜头时长单位：秒。H3 帧率固定 24fps（H3 原生）。
"""
from __future__ import annotations

import hashlib
import json
import re
import time
from pathlib import Path

from . import config, utils

LOG = __import__("logging").getLogger("pipeline")

H3_FPS = 24
H3_MIN_FRAMES = 124
H3_MAX_FRAMES = 362          # 训练范围上限（~15.08s）
H3_FRAME_STEP = 17
LEAD_SEC = 0.10              # 台词相对镜头起点延后（避免吃字/爆音）
TAIL_SEC = 0.30              # 镜头尾部留白
MAX_SPEECH_SEC = (H3_MAX_FRAMES / H3_FPS) - LEAD_SEC - TAIL_SEC  # ≈14.68s
EST_CHARS_PER_SEC = 4.2      # 中文自然语速估计（切分长句预判用）


CARD_TYPES = ("title_card", "end_card", "meme_card")


def is_card(shot: dict) -> bool:
    return bool(shot.get("static_card")) or str(shot.get("type") or "") in CARD_TYPES


def trim_to_target(plan: dict, target: float, *, tolerance: float = 1.15) -> dict:
    """自然语速会让成片变长：按目标时长裁掉尾部多余的 H3 镜（保留片尾卡）。

    - demo（<=60s）：上限 max(target, 45)（验收 30-45s 的上界）；
    - 5min 版（>60s）：上限 target*tolerance。
    """
    shots = list(plan.get("shots") or [])
    if not shots or not target:
        return {"trimmed": []}
    max_total = max(float(target), 45.0) if target <= 60 else float(target) * tolerance
    trimmed: list[str] = []
    while sum(float(s.get("duration") or 0) for s in shots) > max_total:
        # 找最后一个非卡片镜（片尾卡之前）
        candidates = [i for i, s in enumerate(shots[:-1]) if not is_card(s)]
        if len(candidates) <= 3:
            break
        idx = candidates[-1]
        trimmed.append(str(shots[idx].get("id")))
        shots.pop(idx)
    if trimmed:
        for i, shot in enumerate(shots):
            shot["no"] = i + 1
        plan["shots"] = shots
        ep = plan.get("episode") or {}
        total = round(sum(float(s.get("duration") or 0) for s in shots), 3)
        ep["target_duration"] = total
        ep["total_duration"] = total
        ep["shots_total"] = len(shots)
        for seg in ep.get("segments") or []:
            seg["duration"] = round(sum(
                float(s.get("duration") or 0) for s in shots
                if s.get("segment_id") == seg.get("id")), 3)
        plan["episode"] = ep
        LOG.info("自然语速超目标 %.1fs：裁掉 %d 个尾部 H3 镜 %s，总时长 %.2fs",
                 max_total, len(trimmed), trimmed, total)
    return {"trimmed": trimmed, "max_total": max_total}


# ---------------------------------------------------------------------------
# H3 时长网格
# ---------------------------------------------------------------------------
def h3_frames(seconds: float) -> int:
    """向上取整到 H3 合法帧数（17k+5），并夹在 [124,362]。

    允许 1 帧（~0.042s）容差：镜头时长来自 target_duration() 的 3 位小数舍入，
    若严格向上取整会把 5.167s 抬到 141 帧，造成不必要的生成浪费。
    """
    sec = max(0.3, float(seconds or 0.3))
    frames = int(round(sec * H3_FPS))
    k = max(7, int(-(-(frames - 5) // H3_FRAME_STEP)))  # ceil
    k = min(k, (H3_MAX_FRAMES - 5) // H3_FRAME_STEP)
    while k < (H3_MAX_FRAMES - 5) // H3_FRAME_STEP and \
            (H3_FRAME_STEP * k + 5) / H3_FPS < sec - 1.0 / H3_FPS + 1e-9:
        k += 1
    return max(H3_MIN_FRAMES, min(H3_MAX_FRAMES, H3_FRAME_STEP * k + 5))


def h3_length(seconds: float) -> float:
    return round(h3_frames(seconds) / H3_FPS, 3)


def target_duration(speech_sec: float) -> float:
    """语音 → 镜头时长（H3 网格向上取整，含 lead/tail）。"""
    return h3_length(max(H3_MIN_FRAMES / H3_FPS, float(speech_sec) + LEAD_SEC + TAIL_SEC))


# ---------------------------------------------------------------------------
# 台词切分（超长句 → 多镜）
# ---------------------------------------------------------------------------
_SENT_END = re.compile(r"(?<=[。！？!?；;…])")
_CLAUSE_END = re.compile(r"(?<=[，,、：:])")


def split_text(text: str, max_chars: int) -> list[str]:
    """把长台词在标点处切成若干段，每段不超过 max_chars（尽量均衡）。"""
    text = str(text or "").strip()
    if not text:
        return []
    if len(text) <= max_chars:
        return [text]
    # 先按句末标点
    parts = [p for p in _SENT_END.split(text) if p.strip()]
    out: list[str] = []
    for part in parts:
        if len(part) <= max_chars:
            out.append(part)
            continue
        clauses = [p for p in _CLAUSE_END.split(part) if p.strip()]
        buf = ""
        for clause in clauses:
            if len(buf) + len(clause) <= max_chars:
                buf += clause
                continue
            if buf:
                out.append(buf)
            while len(clause) > max_chars:
                out.append(clause[:max_chars])
                clause = clause[max_chars:]
            buf = clause
        if buf:
            out.append(buf)
    # 合并过短段（<6 字）到前一段
    merged: list[str] = []
    for seg in out:
        if merged and len(seg) < 6 and len(merged[-1]) + len(seg) <= max_chars:
            merged[-1] += seg
        else:
            merged.append(seg)
    return merged or [text]


# ---------------------------------------------------------------------------
# 配音合成（Breeze 优先，edge 回退）
# ---------------------------------------------------------------------------
def _cache_key(provider: str, character: str, text: str, voice: str) -> str:
    return utils.sha1_text("v4-voice", provider, character, voice, text)


def _synth_one_edge(text: str, voice: str, out_wav: Path) -> dict:
    from . import tts_engine
    mp3 = out_wav.with_suffix(".mp3")
    res = tts_engine.synth_with_retry(text, voice, mp3, None, rate="+0%")
    utils.ffmpeg(["-y", "-hide_banner", "-loglevel", "error", "-i", str(mp3),
                  "-ar", "48000", "-ac", "2", "-c:a", "pcm_s16le", str(out_wav)])
    try:
        mp3.unlink()
    except OSError:
        pass
    return {"path": str(out_wav), "duration": round(utils.probe_duration(out_wav), 3),
            "provider": "edge", "voice": voice, "seed": 0, "atempo": 1.0}


def _synth_lines(plan: dict, pairs: list[tuple], workdir: Path, force: bool,
                 prog=None) -> dict[tuple, dict]:
    """pairs = [(character, text)]；返回 {(character, text): info}。"""
    from . import tts_breeze
    voice_dir = utils.ensure_dir(Path(workdir) / "voices")
    assets = plan.get("assets") or []
    edge_voice = {str(a.get("id")): str(a.get("voice") or config.DEFAULT_VOICE)
                  for a in assets}
    results: dict[tuple, dict] = {}
    todo: list[tuple[str, str]] = []
    for character, text in pairs:
        key = (character, text)
        if key in results or key in todo:
            continue
        cache_path = voice_dir / f"{_cache_key('auto', character, text, edge_voice.get(character, ''))}.json"
        meta = utils.read_json(cache_path) or {}
        wav = voice_dir / f"{_cache_key('auto', character, text, edge_voice.get(character, ''))}.wav"
        if (not force) and wav.is_file() and meta.get("text") == text and wav.stat().st_size > 2000:
            results[key] = {**meta, "path": str(wav), "cached": True}
        else:
            todo.append(key)
    if not todo:
        return results

    provider_used = "breeze"
    breeze_error = ""
    try:
        from . import tts_breeze
        if not tts_breeze.server_alive():
            if prog is not None:
                prog(7, "启动 Breeze TTS 2 服务（首次加载 ~1-2 分钟）")
            if not tts_breeze.start_server():
                raise tts_breeze.BreezeTTSUnavailable("服务启动失败")
        by_char: dict[str, list[str]] = {}
        for character, text in todo:
            by_char.setdefault(character, []).append(text)
        synth = tts_breeze.synthesize(by_char, voice_dir, auto_start=False)
        info_by_text: dict[tuple, dict] = {}
        for item in synth:
            key = (item["role"], item["text"])
            info_by_text[key] = item
        for character, text in todo:
            item = info_by_text.get((character, text))
            if not item:
                raise tts_breeze.BreezeTTSUnavailable(f"Breeze 未返回: {character}/{text[:20]}")
            # 转 48k stereo（保留自然时长）
            mix = voice_dir / f"{Path(item['path']).stem}_48k.wav"
            utils.ffmpeg(["-y", "-hide_banner", "-loglevel", "error", "-i", item["path"],
                          "-ar", "48000", "-ac", "2", "-c:a", "pcm_s16le", str(mix)])
            rec = {
                "path": str(mix), "raw_path": item["path"],
                "duration": round(utils.probe_duration(mix), 3),
                "provider": "breeze", "voice": item.get("voice", ""),
                "seed": item.get("seed", 0), "character": character, "text": text,
                "cfg_scale": item.get("cfg_scale"), "atempo": 1.0, "cached": False,
            }
            results[(character, text)] = rec
    except Exception as exc:  # noqa: BLE001
        provider_used = "edge"
        breeze_error = str(exc)[:400]
        LOG.warning("Breeze TTS 不可用，回退 edge-tts：%s", breeze_error)
        for character, text in todo:
            wav = voice_dir / f"{_cache_key('edge', character, text, edge_voice.get(character, ''))}.wav"
            try:
                rec = _synth_one_edge(text, edge_voice.get(character) or config.DEFAULT_VOICE, wav)
                rec.update({"character": character, "text": text, "cached": False})
            except Exception as exc2:  # noqa: BLE001
                LOG.error("edge-tts 也失败（%s）：%s", character, exc2)
                continue
            results[(character, text)] = rec
    # 写缓存 meta
    for character, text in todo:
        rec = results.get((character, text))
        if not rec:
            continue
        key = _cache_key("auto", character, text, edge_voice.get(character, ""))
        utils.write_json(voice_dir / f"{key}.json", {k: v for k, v in rec.items()
                                                     if k != "cached"})
    if prog is not None:
        prog(8, f"配音完成（provider={provider_used}，{len(todo)} 条）")
    plan.setdefault("meta", {})["voice_provider"] = provider_used
    if breeze_error:
        plan["meta"]["breeze_error"] = breeze_error
    return results


# ---------------------------------------------------------------------------
# 主流程：配音 + 镜头时长
# ---------------------------------------------------------------------------
def prepare_and_pace(plan: dict, workdir: Path, prog=None, force: bool = False) -> dict:
    """为 plan 配音并按语音自然时长设置镜头时长（会修改 plan['shots']）。

    返回 {"voice_map": {sid: info}, "provider": str, "total_duration": float,
          "splits": [...], "speech_coverage": float}
    """
    workdir = Path(workdir)
    shots = list(plan.get("shots") or [])
    assets = plan.get("assets") or []
    char_by_asset = {str(a.get("id")): str(a.get("character_id") or a.get("character") or a.get("id"))
                     for a in assets}

    # 1) 预切分超长台词（按估计语速）
    max_chars = int(MAX_SPEECH_SEC * EST_CHARS_PER_SEC)
    job_shots: list[dict] = []
    pairs: list[tuple] = []
    for shot in shots:
        line = str(shot.get("line") or "").strip()
        if not line or not shot.get("speaker"):
            job_shots.append({"shot": shot, "chunks": []})
            continue
        chunks = split_text(line, max_chars=max_chars)
        job_shots.append({"shot": shot, "chunks": chunks})
        character = char_by_asset.get(str(shot.get("speaker")), str(shot.get("speaker")))
        for chunk in chunks:
            pairs.append((character, chunk))
    voice_map = _synth_lines(plan, pairs, workdir, force, prog=prog)

    # 2) 生成最终镜头列表（超长台词拆成多镜）
    new_shots: list[dict] = []
    splits: list[dict] = []
    voice_by_sid: dict[str, dict] = {}
    for item in job_shots:
        shot = dict(item["shot"])
        chunks = item["chunks"]
        line = str(shot.get("line") or "").strip()
        character = char_by_asset.get(str(shot.get("speaker")), str(shot.get("speaker")))
        if not chunks:
            # 无台词镜：卡片保持原时长；H3 空镜保持模板节奏（保证落在 17k+5 网格）
            if not is_card(shot):
                shot["duration"] = h3_length(shot.get("duration") or H3_MIN_FRAMES / H3_FPS)
            shot["speech_duration"] = 0.0
            shot["voice"] = {}
            new_shots.append(shot)
            continue
        infos = [voice_map.get((character, chunk)) for chunk in chunks]
        if any(info is None for info in infos):
            # 配音失败：保留原时长并标记（QC 可见）
            shot["duration"] = h3_length(shot.get("duration") or H3_MIN_FRAMES / H3_FPS)
            shot["speech_duration"] = 0.0
            shot["voice"] = {"provider": "missing", "error": "voice missing"}
            new_shots.append(shot)
            continue
        if len(chunks) > 1:
            splits.append({"id": shot.get("id"), "parts": len(chunks)})
        for idx, (chunk, info) in enumerate(zip(chunks, infos)):
            sub = dict(shot)
            if idx > 0:
                sub["id"] = f"{shot.get('id')}b{idx if idx > 1 else ''}"
                sub["no"] = int(shot.get("no") or 0)
                sub["title"] = shot.get("title")
            sub["line"] = chunk
            speech = float(info.get("duration") or 0.0)
            sub["duration"] = target_duration(speech)
            sub["speech_duration"] = round(speech, 3)
            sub["voice"] = {k: info.get(k) for k in
                            ("provider", "voice", "seed", "character", "path", "raw_path")}
            sub["voice"]["atempo"] = 1.0
            sub["voice"]["truncated"] = bool(speech + LEAD_SEC + TAIL_SEC > sub["duration"] + 1e-6)
            voice_by_sid[str(sub.get("id"))] = {**sub["voice"],
                                               "duration": sub["duration"],
                                               "speech_duration": round(speech, 3),
                                               "text": chunk}
            new_shots.append(sub)

    # 3) 自然语速可能让成片超目标：按目标裁掉尾部多余 H3 镜；再重新编号
    natural_total = round(sum(float(s.get("duration") or 0.0) for s in new_shots), 3)
    plan["shots"] = new_shots
    plan["episode"] = plan.get("episode") or {}
    target = float((plan.get("meta") or {}).get("target_duration") or 0.0)
    trim_info = trim_to_target(plan, target) if target else {"trimmed": []}
    new_shots = plan.get("shots") or new_shots
    keep_ids = {str(s.get("id")) for s in new_shots}
    voice_by_sid = {k: v for k, v in voice_by_sid.items() if k in keep_ids}
    for i, shot in enumerate(new_shots):
        shot["no"] = i + 1
    total = round(sum(float(s.get("duration") or 0.0) for s in new_shots), 3)
    plan["shots"] = new_shots
    ep = plan.get("episode") or {}
    ep["target_duration"] = total
    ep["total_duration"] = total
    ep["shots_total"] = len(new_shots)
    ep["h3_shot_count"] = sum(1 for s in new_shots
                              if not s.get("static_card") and not s.get("previs"))
    plan["episode"] = ep
    meta = plan.setdefault("meta", {})
    meta["total_duration"] = total
    meta["natural_duration"] = natural_total
    meta["trimmed"] = trim_info.get("trimmed") or []
    meta["speech_driven"] = True
    meta["lead_sec"] = LEAD_SEC
    meta["tail_sec"] = TAIL_SEC
    meta["voice_provider"] = meta.get("voice_provider", "breeze")
    meta["speech_shots"] = sum(1 for s in new_shots if s.get("speech_duration"))
    meta["splits"] = splits
    meta["atempo_max"] = 1.0
    meta["truncated_shots"] = [sid for sid, v in voice_by_sid.items() if v.get("truncated")]
    utils.write_json(workdir / "voice" / "voice_map.json",
                     {"provider": meta.get("voice_provider"), "shots": voice_by_sid,
                      "lead_sec": LEAD_SEC, "tail_sec": TAIL_SEC, "total_duration": total})
    LOG.info("语音驱动镜头: %d 镜（对话 %d，切分 %d），总时长 %.2fs，provider=%s",
             len(new_shots), meta["speech_shots"], len(splits), total,
             meta.get("voice_provider"))
    return {"voice_map": voice_by_sid, "provider": meta.get("voice_provider"),
            "total_duration": total, "splits": splits,
            "speech_shots": meta["speech_shots"]}


# ---------------------------------------------------------------------------
# 每镜音轨（48k stereo，voice + lead + sfx）
# ---------------------------------------------------------------------------
def build_shot_audio(shot: dict, voice: dict | None, workdir: Path,
                     sfx_wav: Path | None = None, force: bool = False) -> dict:
    """生成 audio/{sid}.wav：自然语速播音（LEAD 偏移）+ 静音补齐到镜头时长 + 可选 sfx。"""
    sid = str(shot.get("id"))
    duration = round(float(shot.get("duration") or H3_MIN_FRAMES / H3_FPS), 3)
    out = Path(workdir) / "audio" / f"{sid}.wav"
    utils.ensure_dir(out.parent)
    meta_path = out.with_suffix(".meta.json")
    voice_path = Path(voice.get("path")) if voice and voice.get("path") else None
    digest = utils.sha1_text("v4-audio", sid, duration, str(voice_path),
                             (voice or {}).get("provider"), str(sfx_wav or ""))
    if not force and out.is_file() and out.stat().st_size > 2000:
        meta = utils.read_json(meta_path) or {}
        if meta.get("hash") == digest:
            return {"id": sid, "path": str(out), "duration": duration,
                    "speech_duration": shot.get("speech_duration", 0.0), "reused": True}
    args: list[str] = []
    fc: list[str] = []
    inputs = 0
    if voice_path and voice_path.is_file():
        args += ["-i", str(voice_path)]
        delay = int(round(LEAD_SEC * 1000))
        fc.append(f"[0:a]aresample={config.SAMPLE_RATE},pan=stereo|c0=c0|c1=c0,"
                  f"adelay={delay}|{delay},apad=whole_dur={duration:.3f},"
                  f"atrim=0:{duration:.3f},afade=t=out:st={max(0.0, duration - 0.05):.3f}:d=0.05[a1]")
        inputs = 1
    else:
        args += ["-f", "lavfi", "-i",
                 f"anullsrc=r={config.SAMPLE_RATE}:cl=stereo"]
        fc.append(f"[0:a]atrim=0:{duration:.3f},asetpts=N/SR/TB[a1]")
        inputs = 1
    if sfx_wav and Path(sfx_wav).is_file():
        args += ["-i", str(sfx_wav)]
        fc.append("[1:a]volume=0.35,adelay=80|80[sfx]")
        fc.append(f"[a1][sfx]amix=inputs=2:duration=first:normalize=0[aout]")
    else:
        fc.append("[a1]anull[aout]")
    utils.ffmpeg(args + ["-filter_complex", ";".join(fc), "-map", "[aout]",
                         "-t", f"{duration:.3f}", "-ar", str(config.SAMPLE_RATE),
                         "-ac", "2", "-c:a", "pcm_s16le", str(out)], cwd=workdir)
    utils.write_json(meta_path, {"hash": digest, "duration": duration,
                                 "speech_duration": shot.get("speech_duration", 0.0),
                                 "provider": (voice or {}).get("provider", "")})
    return {"id": sid, "path": str(out), "duration": duration,
            "speech_duration": shot.get("speech_duration", 0.0), "reused": False}


def build_all_shot_audio(plan: dict, voice_map: dict, workdir: Path, prog=None,
                         force: bool = False) -> dict[str, dict]:
    from . import render as render_mod
    workdir = Path(workdir)
    sfx = render_mod.ensure_sfx(workdir)
    results: dict[str, dict] = {}
    shots = plan.get("shots") or []
    for i, shot in enumerate(shots):
        sid = str(shot.get("id"))
        sfx_path = None
        sfx_name = str(shot.get("sfx") or "").lower()
        if sfx_name in ("whoosh", "ding") and sfx.get(sfx_name):
            sfx_path = sfx[sfx_name]
        try:
            results[sid] = build_shot_audio(shot, voice_map.get(sid), workdir,
                                            sfx_path, force=force)
        except Exception as exc:  # noqa: BLE001
            raise VoicedError(f"{sid} 音轨构建失败: {exc}") from exc
        if prog is not None and i % 5 == 0:
            prog(18 + 4.0 * (i + 1) / max(1, len(shots)), f"镜头音轨 {i + 1}/{len(shots)}")
    return results
