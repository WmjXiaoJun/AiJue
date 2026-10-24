"""多语种本地化渲染（pipeline.localize）。

    python -m pipeline.localize --episode-json <serialized_episode.json> \
        --lang en --outdir outputs/episodes/{eid}/localized/en --translator nllb|mock

流程（contract/LOCALIZATION.md）：
1) 收集翻译单元（标题/字幕/台词/花字/名字条/段落/片尾 CTA）→ units.json；
2) 调用 localize.translate_cli（ComfyUI venv python）翻译，失败回退原文并标注；
3) 复用现有镜头画面 clips/{sid}.mp4（或 shots/{sid}.mp4）拼时间线，时长不变；
4) 每镜用 edge-tts 目标语种音色合成，atempo(1.0~1.25) 拟合镜头时长、不足补静音，
   拼 narration.wav；
5) 生成目标语 ASS（字体/行宽按 localize/terms.py，位置沿用中文样式）+ subtitles.srt；
6) 混音（BGM + loudnorm，与中文版一致）、编码 final.mp4；
7) 产出 final.mp4 / narration.wav / subtitles.srt / translations.json / meta.json。
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from . import __version__, ass_builder, characters, config, render, tts_engine, utils

LOG = logging.getLogger("pipeline")

REPO_ROOT = config.REPO_ROOT
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

try:  # terms 是纯数据；translator 顶层不 import torch
    from localize import terms  # type: ignore
    from localize.translator import NAME_MAP  # type: ignore
except Exception:  # noqa: BLE001 - 极端情况下退化为内置小表
    terms = None  # type: ignore
    NAME_MAP = {"老周": "Zhou", "阿栗": "Ali", "大魏": "Dawei"}

COMFY_PYTHON_DEFAULT = r"D:\BaiduSyncdisk\wmj_work\XJ\ComfyUI\.venv\Scripts\python.exe"
SUPPORTED_LANGS = ("en", "es", "ja", "ko", "pt", "fr", "de", "ar")
CHAR_SHOT_TYPES = ("host_to_camera", "shot_reverse", "reaction", "guest_intro")
TTS_LEAD_IN = 0.15
TTS_MAX_TEMPO = 1.25
WRAP_FALLBACK = 42

FONT_FILES = {
    "en": ["C:/Windows/Fonts/arial.ttf", "C:/Windows/Fonts/arialbd.ttf"],
    "es": ["C:/Windows/Fonts/arial.ttf", "C:/Windows/Fonts/arialbd.ttf"],
    "pt": ["C:/Windows/Fonts/arial.ttf", "C:/Windows/Fonts/arialbd.ttf"],
    "ja": ["C:/Windows/Fonts/YuGothR.ttc", "C:/Windows/Fonts/YuGothB.ttc"],
    "ko": ["C:/Windows/Fonts/malgun.ttf", "C:/Windows/Fonts/malgunbd.ttf"],
    "zh": ["C:/Windows/Fonts/msyh.ttc", "C:/Windows/Fonts/msyhbd.ttc"],
}


# --------------------------------------------------------------------------
# 小工具
# --------------------------------------------------------------------------
def _valid_file(path, min_size: int = 512) -> bool:
    try:
        p = Path(path)
        return p.is_file() and p.stat().st_size >= min_size
    except (TypeError, OSError):
        return False


def _shot_id(shot: dict) -> str:
    sid = shot.get("id")
    if sid:
        return str(sid)
    return "shot_" + utils.sha1_text(json.dumps(shot, sort_keys=True, ensure_ascii=False))[:10]


def _shot_duration(shot: dict) -> float:
    try:
        return max(0.0, float(shot.get("duration") or 0.0))
    except (TypeError, ValueError):
        return 0.0


def _ep_resolution(ep: dict) -> tuple[int, int]:
    width = int(ep.get("width") or 0)
    height = int(ep.get("height") or 0)
    if width and height:
        return width, height
    return utils.parse_resolution(ep.get("resolution"))


def _lang_terms(lang: str) -> dict:
    if terms is None:
        return {"fonts": "Arial", "wrap": WRAP_FALLBACK, "voices": {}}
    return {
        "fonts": terms.FONTS.get(lang, "Arial"),
        "wrap": int(terms.WRAP_WIDTH.get(lang, WRAP_FALLBACK)),
        "voices": dict(terms.VOICES.get(lang, {})),
    }


def _romanize(text: str) -> str:
    out = str(text or "")
    for zh, ro in NAME_MAP.items():
        out = out.replace(zh, ro)
    return out


def _term_translation(text: str, lang: str) -> str | None:
    if terms is None:
        return None
    text = str(text or "").strip()
    if not text:
        return None
    for table in (terms.ROLES, terms.SEGMENTS):
        for src, langs in table.items():
            if text == src and lang in langs:
                return langs[lang]
    return None


def _wrap_all(text: str, width: int) -> list[str]:
    """完整折行（不丢词）：拉丁按单词，CJK 按字符。"""
    text = re.sub(r"\s+", " ", str(text or "").strip())
    if not text:
        return []
    cjk = bool(re.search(r"[\u4e00-\u9fff\u3040-\u30ff\uac00-\ud7af]", text)) and " " not in text
    if cjk:
        return [text[i:i + width] for i in range(0, len(text), width)]
    words = text.split(" ")
    lines: list[str] = []
    cur = ""
    for word in words:
        candidate = word if not cur else cur + " " + word
        if len(candidate) <= width or not cur:
            cur = candidate
        else:
            lines.append(cur)
            cur = word
    if cur:
        lines.append(cur)
    return lines


def _fit_subtitle(text: str, base_width: int, max_lines: int = 2) -> tuple[list[str], int]:
    """目标语字幕自适应：优先 2 行，字号 42->30 逐级缩小，保证不丢词。"""
    text = re.sub(r"\s+", " ", str(text or "").strip())
    if not text:
        return [], 42
    for fs in (42, 38, 34, 30):
        cap = max(int(base_width), int(1240 / max(10.0, fs * 0.52)))
        lines = _wrap_all(text, cap)
        if len(lines) <= max_lines:
            return lines, fs
    return _wrap_all(text, int(1240 / (30 * 0.52))), 30


def _srt_time(seconds: float) -> str:
    ms = max(0, int(round(float(seconds) * 1000)))
    h, rem = divmod(ms, 3600000)
    m, rem = divmod(rem, 60000)
    s, ms = divmod(rem, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def _srt(events: list[dict]) -> str:
    blocks = [f"{i}\n{_srt_time(e['start'])} --> {_srt_time(e['end'])}\n{e['text']}"
              for i, e in enumerate(events, 1)]
    return "\n\n".join(blocks) + ("\n" if blocks else "")


def _ass_time(seconds: float) -> str:
    total = max(0.0, float(seconds))
    h = int(total // 3600)
    m = int((total % 3600) // 60)
    s = total % 60
    return f"{h:d}:{m:02d}:{s:05.2f}"


def _esc(text: str) -> str:
    return ass_builder._esc(text)


def _rect(w: int, h: int) -> str:
    return f"m 0 0 l {w} 0 l {w} {h} l 0 {h}"


# --------------------------------------------------------------------------
# 1) 翻译单元
# --------------------------------------------------------------------------
def collect_units(episode: dict) -> list[dict]:
    """收集需要翻译的文本单元（保持出现顺序，key 唯一）。"""
    ep = episode.get("episode") or episode
    shots = episode.get("shots") or []
    segments = {str(s.get("id")): str(s.get("name") or "")
                for s in (episode.get("segments") or []) if isinstance(s, dict)}
    units: list[dict] = []
    seen: set[str] = set()

    def add(key: str, text) -> None:
        # 人名先罗马化（Zhou/Ali/Dawei），避免模型把「老周」翻成 Old Zhou
        value = _romanize(str(text or "").strip())
        if not value or key in seen:
            return
        seen.add(key)
        units.append({"key": key, "text": value})

    add("episode.title", ep.get("title"))
    add("episode.subtitle", ep.get("subtitle"))
    # 资产身份（角色/身份会烘焙进 clips 名字条）；terms 已覆盖的不送翻
    for asset in episode.get("assets") or []:
        if not isinstance(asset, dict):
            continue
        raw_title = str(asset.get("title") or "").strip()
        if raw_title and _term_translation(raw_title, "en") is None:
            add(f"asset.{asset.get('id')}.title", raw_title)
    seen_segments: set[str] = set()
    for shot in shots:
        if not isinstance(shot, dict):
            continue
        sid = _shot_id(shot)
        typ = str(shot.get("type") or "")
        add(f"shot.{sid}.line", shot.get("line"))
        add(f"shot.{sid}.punchline", shot.get("punchline"))
        add(f"shot.{sid}.title", shot.get("title"))
        add(f"shot.{sid}.visual", shot.get("visual"))
        # 片头/片尾固定文案（品牌 SUBTITLES / CTA）不交给模型，避免自由翻译
        if typ not in ("title_card", "end_card"):
            add(f"shot.{sid}.subtitle", shot.get("subtitle"))
        lower = shot.get("lower_third")
        if isinstance(lower, dict):
            # 人名走 terms 罗马化（Zhou/Ali/Dawei），不放 units 交给模型
            role = str(lower.get("title") or "").strip()
            if role and _term_translation(role, "en") is None:
                add(f"shot.{sid}.lower.title", role)
        seg = shot.get("segment") or shot.get("segment_id")
        if seg:
            seg_key = str(seg)
            if seg_key not in seen_segments:
                seen_segments.add(seg_key)
                add(f"segment.{seg_key}", segments.get(seg_key) or seg_key)
    return units


def _mock_translate(text: str, lang: str) -> str:
    """离线 mock 翻译（测试/无模型）：固定术语 + 人名罗马化，其余原文。"""
    term = _term_translation(text, lang)
    if term is not None:
        return term
    return _romanize(text)


def _call_translator(units: list[dict], lang: str, provider: str, workdir: Path,
                     comfy_python: str | None = None, timeout: int = 900) -> dict:
    """调用 localize.translate_cli 翻译。

    provider=ollama：system python（只用 urllib 调本机 ollama，不需要 torch）；
    provider=nllb：ComfyUI venv python（torch/transformers 在 venv 里）。
    """
    if provider == "nllb":
        python_bin = Path(comfy_python or os.environ.get("COMFY_PYTHON") or COMFY_PYTHON_DEFAULT)
    else:
        python_bin = Path(sys.executable)
    if not python_bin.is_file():
        raise RuntimeError(f"翻译 python 不存在: {python_bin}")
    units_path = workdir / "units.json"
    out_path = workdir / f"translations.{provider}.json"
    utils.write_json(units_path, units)
    cmd = [str(python_bin), "-m", "localize.translate_cli",
           "--in", str(units_path), "--out", str(out_path), "--lang", lang,
           "--provider", provider]
    LOG.info("调用翻译 CLI: provider=%s python=%s（%d 单元）", provider, python_bin.name, len(units))
    proc = subprocess.run(cmd, cwd=str(REPO_ROOT), capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=timeout)
    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout or "")[-1200:]
        raise RuntimeError(f"翻译 CLI 失败 (exit {proc.returncode}): {tail}")
    data = utils.read_json(out_path)
    if not isinstance(data, dict) or not isinstance(data.get("translations"), dict):
        raise RuntimeError("翻译 CLI 输出格式不正确")
    return data


TRANSLATION_SCHEMA = "romanized-names-v2"


def translate_units(units: list[dict], lang: str, translator: str, outdir: Path,
                    comfy_python: str | None = None, force: bool = False) -> dict:
    """翻译（带缓存/回退）。"""
    outdir = utils.ensure_dir(outdir)
    cache_path = outdir / "translations.json"
    unique_texts: list[str] = []
    for unit in units:
        if unit["text"] not in unique_texts:
            unique_texts.append(unit["text"])
    unique_units = [{"key": f"u{i}", "text": text} for i, text in enumerate(unique_texts)]

    if not force:
        cached = utils.read_json(cache_path)
        cached_tx = (cached or {}).get("translations") if isinstance(cached, dict) else None
        covers_all = isinstance(cached_tx, dict) and all(
            str(u["key"]) in cached_tx for u in units)
        if isinstance(cached, dict) and cached.get("lang") == lang \
                and str(cached.get("provider") or "") == translator and covers_all \
                and cached.get("schema") == TRANSLATION_SCHEMA:
            LOG.info("命中翻译缓存: %s（provider=%s）", cache_path, translator)
            result = dict(cached)
            result["cached"] = True
            return result

    t0 = time.time()
    try:
        if translator == "mock":
            translations = {u["key"]: _mock_translate(u["text"], lang) for u in unique_units}
            payload = {"ok": True, "lang": lang, "provider": "mock", "model": "mock",
                       "translations": translations}
        elif translator in ("ollama", "nllb"):
            data = _call_translator(unique_units, lang, translator, outdir, comfy_python)
            translations = {str(k): str(v) for k, v in (data.get("translations") or {}).items()}
            payload = {"ok": True, "lang": lang, "provider": translator,
                       "model": str(data.get("model") or translator),
                       "translations": translations}
        else:
            raise RuntimeError(f"不支持的翻译器: {translator}")
    except Exception as exc:  # noqa: BLE001 - 翻译失败不阻塞渲染
        LOG.warning("翻译失败（回退原文）: %s", exc)
        translations = {u["key"]: _mock_translate(u["text"], lang) for u in unique_units}
        payload = {"ok": False, "lang": lang, "provider": translator,
                   "model": "none(original fallback)", "translations": translations,
                   "error": str(exc)[:500]}
    payload["elapsed_sec"] = round(time.time() - t0, 2)

    by_text = {text: translations.get(f"u{i}", text) for i, text in enumerate(unique_texts)}
    full: dict[str, str] = {}
    for unit in units:
        text = unit["text"]
        translated = by_text.get(text, text)
        term = _term_translation(text, lang)
        if term is not None:
            translated = term
        elif text in NAME_MAP:
            translated = NAME_MAP[text]
        full[unit["key"]] = translated
    payload["translations"] = full
    payload["count"] = len(full)
    payload["cached"] = False
    payload["schema"] = TRANSLATION_SCHEMA
    utils.write_json(cache_path, payload)
    return payload


_CJK_RE = re.compile(r"[\u4e00-\u9fff]+")
_OLLAMA_URL = "http://127.0.0.1:11434/api/generate"
_OLLAMA_MODEL = "qwen2.5:7b"


def _ollama_rewrite(text: str, lang: str, timeout: float = 120.0) -> str:
    """把译文里残留的中文片段改写掉（只用于 ollama provider 的兜底清洗）。"""
    import urllib.request
    lang_name = {"en": "English", "es": "Spanish", "pt": "Portuguese"}.get(lang, lang)
    prompt = (
        f"Rewrite the following {lang_name} sentence so that it contains NO Chinese characters. "
        f"Translate any Chinese fragments into natural {lang_name} (keep names Zhou/Ali/Dawei). "
        f"Output only the final {lang_name} sentence, no explanation:\n{text}"
    )
    payload = {"model": _OLLAMA_MODEL, "prompt": prompt, "stream": False,
               "options": {"temperature": 0.1, "num_predict": 256}}
    req = urllib.request.Request(
        _OLLAMA_URL, data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return str(json.loads(resp.read().decode("utf-8")).get("response") or "").strip()


def _sanitize_translations(tx: dict, lang: str, translator: str) -> int:
    """清除译文中的残留中文（ollama 偶尔会保留难词）；失败则去中文兜底。"""
    if lang in ("ja", "ko", "zh") or translator != "ollama":
        return 0
    fixed = 0
    for key, value in list(tx.items()):
        if not isinstance(value, str) or not _CJK_RE.search(value):
            continue
        fixed += 1
        cleaned = ""
        try:
            cleaned = _ollama_rewrite(_CJK_RE.sub(" ", value), lang).strip()
        except Exception as exc:  # noqa: BLE001
            LOG.warning("译文清洗失败（去中文兜底）: %s", exc)
        if not cleaned or _CJK_RE.search(cleaned):
            cleaned = re.sub(r"\s+", " ", _CJK_RE.sub(" ", value)).strip()
        tx[key] = cleaned
    return fixed


def _translated_map(units: list[dict], result: dict) -> dict:
    translations = result.get("translations") or {}
    mapping = {}
    for unit in units:
        key = unit["key"]
        value = translations.get(key)
        mapping[key] = value if isinstance(value, str) and value.strip() else unit["text"]
    return mapping


def _shot_lower_third(shot: dict, assets: dict, tx: dict, sid: str,
                      lang: str = "en") -> tuple[str, str] | None:
    """名字条内容：优先 shot.lower_third，缺失时回退资产 name/title（再本地化）。"""
    lower = shot.get("lower_third")
    if isinstance(lower, dict) and lower.get("name"):
        raw_name = str(lower.get("name") or "").strip()
        raw_role = str(lower.get("title") or "").strip()
    else:
        asset = assets.get(str(shot.get("speaker") or "")) or {}
        raw_name = str(shot.get("speaker_name") or asset.get("name") or "").strip()
        raw_role = str(asset.get("title") or "").strip()
    if not raw_name:
        return None
    name = NAME_MAP.get(raw_name) or _romanize(raw_name)
    role = _term_translation(raw_role, lang) or tx.get(f"shot.{sid}.lower.title") \
        or _romanize(raw_role)
    return name.strip(), role.strip()


def _fit_text_font(text: str, max_width_px: int, base: int = 52, min_fs: int = 28) -> int:
    """按可用宽度估算字号（拉丁/西语标题较长时自动缩小，避免出画）。"""
    length = max(1, len(str(text or "").strip()))
    fs = int(max_width_px / (0.52 * length))
    return max(min_fs, min(base, fs))


LOCALE_TEXT = {
    "en": {
        "brand_line": "ZAOPIANJU · AI MICRO-VARIETY",
        "brand_short": "ZAOPIANJU",
        "title_top": "ZAOPIANJU ORIGINAL · EP.{no:03d}",
        "episode_label": "EP.{no:03d}",
        "live": "● LIVE  Virtual Studio A",
        "speaking": "Speaking",
        "status_line": "● {name}  ·  {role}  ·  {speaking}",
        "status_line2": "● {speaking}  ·  {role}",
        "reaction_label": "REACTION · CLOSE-UP",
        "reaction_label2": "REACTION · CLOSE-UP",
        "reaction_role": "Reaction",
        "guest_intro_label": "GUEST INTRO",
        "host_role": "Host",
        "guest_role": "Guest",
        "end_brand": "ZAOPIANJU",
        "end_tagline": "AI MICRO-VARIETY",
        "meme_tag": "MEME",
        "meme_punch": "PUNCH",
        "broll_scene": "EDIT SUITE · SERVER LIGHTS",
    },
    "es": {
        "brand_line": "ZAOPIANJU · MICRO-VARIEDAD CON IA",
        "brand_short": "ZAOPIANJU",
        "title_top": "ZAOPIANJU ORIGINAL · EP.{no:03d}",
        "episode_label": "EP.{no:03d}",
        "live": "● LIVE  Estudio virtual A",
        "speaking": "Hablando",
        "status_line": "● {name}  ·  {role}  ·  {speaking}",
        "status_line2": "● {speaking}  ·  {role}",
        "reaction_label": "REACCIÓN · PRIMER PLANO",
        "reaction_label2": "REACCIÓN · PRIMER PLANO",
        "reaction_role": "Reacción",
        "guest_intro_label": "PRESENTACIÓN",
        "host_role": "Presentador",
        "guest_role": "Invitado",
        "end_brand": "ZAOPIANJU",
        "end_tagline": "MICRO-VARIEDAD CON IA",
        "meme_tag": "MEME",
        "meme_punch": "PUNCH",
        "broll_scene": "SALA DE EDICIÓN · LUCES",
    },
    "ja": {
        "brand_line": "ZAOPIANJU · AIマイクロバラエティ",
        "brand_short": "ZAOPIANJU",
        "title_top": "ZAOPIANJU ORIGINAL · EP.{no:03d}",
        "episode_label": "EP.{no:03d}",
        "live": "● LIVE バーチャルスタジオA",
        "speaking": "発言中",
        "status_line": "● {name}  ·  {role}  ·  {speaking}",
        "status_line2": "● {speaking}  ·  {role}",
        "reaction_label": "リアクション · クローズアップ",
        "reaction_label2": "リアクション · クローズアップ",
        "reaction_role": "リアクション",
        "guest_intro_label": "ゲスト紹介",
        "host_role": "司会",
        "guest_role": "ゲスト",
        "end_brand": "ZAOPIANJU",
        "end_tagline": "AIマイクロバラエティ",
        "meme_tag": "MEME",
        "meme_punch": "PUNCH",
        "broll_scene": "編集室 · サーバーライト",
    },
    "ko": {
        "brand_line": "ZAOPIANJU · AI 마이크로 버라이어티",
        "brand_short": "ZAOPIANJU",
        "title_top": "ZAOPIANJU ORIGINAL · EP.{no:03d}",
        "episode_label": "EP.{no:03d}",
        "live": "● LIVE 가상 스튜디오 A",
        "speaking": "발언 중",
        "status_line": "● {name}  ·  {role}  ·  {speaking}",
        "status_line2": "● {speaking}  ·  {role}",
        "reaction_label": "리액션 · 클로즈업",
        "reaction_label2": "리액션 · 클로즈업",
        "reaction_role": "리액션",
        "guest_intro_label": "게스트 소개",
        "host_role": "진행자",
        "guest_role": "게스트",
        "end_brand": "ZAOPIANJU",
        "end_tagline": "AI 마이크로 버라이어티",
        "meme_tag": "MEME",
        "meme_punch": "PUNCH",
        "broll_scene": "편집실 · 서버 라이트",
    },
    "pt": {
        "brand_line": "ZAOPIANJU · MICRO-VARIEDADE COM IA",
        "brand_short": "ZAOPIANJU",
        "title_top": "ZAOPIANJU ORIGINAL · EP.{no:03d}",
        "episode_label": "EP.{no:03d}",
        "live": "● LIVE  Estúdio virtual A",
        "speaking": "Falando",
        "status_line": "● {name}  ·  {role}  ·  {speaking}",
        "status_line2": "● {speaking}  ·  {role}",
        "reaction_label": "REAÇÃO · CLOSE-UP",
        "reaction_label2": "REAÇÃO · CLOSE-UP",
        "reaction_role": "Reação",
        "guest_intro_label": "APRESENTAÇÃO",
        "host_role": "Apresentador",
        "guest_role": "Convidado",
        "end_brand": "ZAOPIANJU",
        "end_tagline": "MICRO-VARIEDADE COM IA",
        "meme_tag": "MEME",
        "meme_punch": "PUNCH",
        "broll_scene": "SALA DE EDIÇÃO · LUZES",
    },
}


def _locale_for(lang: str) -> dict:
    """镜头内烘焙文案的目标语覆盖表（zh 用 mock_shot 默认中文）。"""
    if lang == "zh":
        return {}
    table = dict(LOCALE_TEXT.get(lang, LOCALE_TEXT["en"]))
    table["names"] = dict(NAME_MAP)   # 角色名罗马化映射（Zhou/Ali/Dawei）
    return table


def _localize_assets(assets: list, tx: dict, lang: str) -> list[dict]:
    out = []
    for asset in assets or []:
        if not isinstance(asset, dict):
            continue
        item = dict(asset)
        raw_name = str(item.get("name") or "").strip()
        item["name"] = NAME_MAP.get(raw_name) or _romanize(raw_name)
        raw_title = str(item.get("title") or "").strip()
        if raw_title:
            item["title"] = (_term_translation(raw_title, lang)
                             or tx.get(f"asset.{item.get('id')}.title") or raw_title)
        out.append(item)
    return out


def _localize_shots(shots: list, tx: dict, lang: str,
                    ep_no: int = 1) -> list[dict]:
    out = []
    for shot in shots or []:
        if not isinstance(shot, dict):
            continue
        item = dict(shot)
        item["episode_no"] = ep_no
        sid = _shot_id(shot)
        raw_name = str(item.get("speaker_name") or "").strip()
        if raw_name:
            item["speaker_name"] = NAME_MAP.get(raw_name) or _romanize(raw_name)
        visual = tx.get(f"shot.{sid}.visual")
        if visual:
            item["visual"] = visual
        for field in ("line", "punchline", "title"):
            value = tx.get(f"shot.{sid}.{field}")
            if value:
                item[field] = value
        lower = shot.get("lower_third")
        if isinstance(lower, dict) and lower.get("name"):
            raw_role = str(lower.get("title") or "").strip()
            item["lower_third"] = {
                "name": NAME_MAP.get(str(lower.get("name") or "").strip())
                        or _romanize(str(lower.get("name") or "")),
                "title": _term_translation(raw_role, lang)
                         or tx.get(f"shot.{sid}.lower.title") or _romanize(raw_role),
            }
        if str(shot.get("type")) == "title_card":
            item["subtitle"] = _fixed_brand_subtitle(lang)
        elif str(shot.get("type")) == "end_card":
            item["subtitle"] = _fixed_end_cta(lang)
        out.append(item)
    return out


def _regenerate_clips(local_shots: list, local_assets: list, episode: dict,
                      workdir: Path, width: int, height: int, fps: int,
                      char_map: dict, char_fp: str, locale: dict,
                      prog=None) -> dict:
    """用本地化后的 shot/assets 重生成目标语 clips（包装文字已译文，无中文）。"""
    regenerated = reused = 0
    total = max(1, len(local_shots))
    for i, shot in enumerate(local_shots):
        info = render.ensure_clip(shot, local_assets, episode, workdir, width, height,
                                  fps, char_map=char_map, char_fp=char_fp,
                                  locale=locale)
        if info.get("reused"):
            reused += 1
        else:
            regenerated += 1
        if prog is not None and (i % 5 == 0 or i == total - 1):
            prog(12 + 40.0 * (i + 1) / total, f"生成目标语镜头 {i + 1}/{total}")
    return {"regenerated": regenerated, "reused": reused}


def _fixed_brand_subtitle(lang: str) -> str:
    """片头副标题：品牌固定映射（terms.SUBTITLES），不交给模型。"""
    if terms is not None:
        return terms.SUBTITLES.get(lang) or terms.SUBTITLES.get("en") \
            or "Zaopianju · AI Micro-Variety"
    return "Zaopianju · AI Micro-Variety"


def _fixed_end_cta(lang: str) -> str:
    """片尾 CTA：品牌 + 下期见（固定映射）。"""
    base = _fixed_brand_subtitle(lang)
    tail = {
        "en": "See you next episode",
        "es": "Hasta el próximo episodio",
        "ja": "次回もお楽しみに",
        "ko": "다음 에피소드에서 만나요",
        "pt": "Até o próximo episódio",
    }.get(lang, "See you next episode")
    return f"{base} · {tail}"


# --------------------------------------------------------------------------
# 2) 目标语 ASS / SRT
# --------------------------------------------------------------------------
ASS_HEADER = """[Script Info]
Title: {{TITLE}}
ScriptType: v4.00+
WrapStyle: 2
PlayResX: 1280
PlayResY: 720
ScaledBorderAndShadow: yes
YCbCr Matrix: TV.709

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Sub,{{FONT}},42,&H00FFFFFF,&H000000FF,&H00141414,&H96000000,0,0,0,0,100,100,1,0,1,3,2,2,20,20,65,1
Style: SubChar,{{FONT}},42,&H00FFFFFF,&H000000FF,&H00141414,&H96000000,0,0,0,0,100,100,1,0,1,3,2,2,20,20,65,1
Style: Punch,{{FONT}},92,&H0018C5F5,&H000000FF,&H00FFFFFF,&H96000000,-1,0,0,0,100,100,2,0,1,5,3,5,60,60,60,1
Style: Chapter,{{FONT}},30,&H0018C5F5,&H000000FF,&H00101010,&H00000000,-1,0,0,0,100,100,1,0,1,2,1,7,64,60,88,1
Style: Chip,{{FONT}},44,&H00FFFFFF,&H000000FF,&H00101010,&H96000000,-1,0,0,0,100,100,1,0,1,2,1,7,0,0,0,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""


def _chip_events(lower: tuple[str, str] | None, accent: str,
                 start: float, end: float, font: str) -> list[tuple]:
    """覆盖原中文名字条并绘制目标语名字条（ASS 叠加，不改原画面）。"""
    if not lower or end - start < 0.6:
        return []
    name, role = lower
    if not name:
        return []
    t0 = start + 0.08
    t1 = max(t0 + 0.5, end - 0.05)
    cover = "{\\an7\\pos(38,474)\\p1\\bord0\\shad0\\c&H0E0E10&}" + _rect(480, 138) + "{\\p0}"
    bar = ("{\\an7\\pos(62,496)\\p1\\bord0\\shad0\\c" + ass_builder.ass_color(accent)
           + "&\\alpha&H00&}" + _rect(8, 96) + "{\\p0}")
    name_ev = (f"{{\\an7\\pos(86,498)\\fn{font}\\fs46\\b1\\c&H00FFFFFF&}}"
               + _esc(name))
    events = [(t0, t1, 1, "Chip", cover), (t0, t1, 2, "Chip", bar),
              (t0, t1, 3, "Chip", name_ev)]
    if role:
        events.append((t0, t1, 3, "Chip",
                       f"{{\\an7\\pos(88,558)\\fn{font}\\fs26\\b0\\c&H00FFFFFF&\\alpha&H30&}}"
                       + _esc(role)))
    return events


def _status_events(shot: dict, lower: tuple[str, str] | None, accent: str, lang: str,
                   start: float, end: float, font: str) -> list[tuple]:
    """反打镜头顶部「● Name · Role · Speaking」覆盖（覆盖旧中文状态条）。"""
    if str(shot.get("type")) != "shot_reverse" or not lower:
        return []
    name, role = lower
    speaking = "Speaking"
    if terms is not None:
        speaking = terms.ROLES.get("发言中", {}).get(lang) or terms.ROLES.get("发言中", {}).get("en") or speaking
    label = f"● {name}" + (f"  ·  {role}" if role else "") + f"  ·  {speaking}"
    t0 = start + 0.08
    t1 = max(t0 + 0.5, end - 0.05)
    cover = "{\\an7\\pos(120,60)\\p1\\bord0\\shad0\\c&H0E0E10&}" + _rect(1040, 72) + "{\\p0}"
    text = (f"{{\\an5\\pos(640,96)\\fn{font}\\fs28\\b1\\c"
            + ass_builder.ass_color(accent) + "&}" + _esc(label))
    return [(t0, t1, 1, "Chip", cover), (t0, t1, 3, "Chip", text)]


def build_ass(episode: dict, tx: dict, lang: str) -> dict:
    """生成目标语 ASS：字幕/花字/段落/名字条/片头标题/片尾 CTA。"""
    ep = episode.get("episode") or episode
    shots = [s for s in episode.get("shots") or [] if isinstance(s, dict)]
    assets = [a for a in episode.get("assets") or [] if isinstance(a, dict)]
    accent_of = {str(a.get("id")): str(a.get("accent_color") or config.ACCENT_DEFAULT)
                 for a in assets}
    lang_terms = _lang_terms(lang)
    font = lang_terms["fonts"]
    wrap = lang_terms["wrap"]

    timings: list[dict] = []
    cursor = 0.0
    for shot in shots:
        dur = _shot_duration(shot)
        timings.append({"shot": shot, "start": cursor, "end": cursor + dur})
        cursor += dur
    total = cursor
    char_shot_ids = {_shot_id(s) for s in shots if s.get("speaker")}
    assets_map = {str(a.get("id")): a for a in assets if a.get("id")}
    lowers = {_shot_id(s): _shot_lower_third(s, assets_map, tx, _shot_id(s), lang)
              for s in shots}
    ep_no = int(ep.get("no") or 1)

    events: list[tuple] = []
    # 段落标签
    seen_segments: set[str] = set()
    for item in timings:
        shot = item["shot"]
        seg = shot.get("segment") or shot.get("segment_id")
        if not seg or seg in seen_segments:
            continue
        seen_segments.add(seg)
        if str(seg) == "open":
            continue
        label = tx.get(f"segment.{seg}")
        if not label:
            continue
        start = item["start"] + 0.35
        end = min(item["end"], start + 4.0)
        if end - start >= 1.2:
            events.append((start, end, 2, "Chapter", r"{\fad(160,200)}◆ " + _esc(label)))

    for item in timings:
        shot = item["shot"]
        sid = _shot_id(shot)
        typ = str(shot.get("type") or "")
        start, end = float(item["start"]), float(item["end"])
        if typ != "meme_card":
            punch = tx.get(f"shot.{sid}.punchline")
            if punch:
                p_start = start + 0.12
                p_end = max(p_start + 0.8, end - 0.12)
                events.append((p_start, p_end, 3, "Punch",
                               r"{\fad(140,160)\t(0,220,\fscx106\fscy106)}" + _esc(punch)))
            if typ == "title_card":
                body = _fixed_brand_subtitle(lang)
            elif typ == "end_card":
                body = _fixed_end_cta(lang)
            else:
                body = tx.get(f"shot.{sid}.line") or tx.get(f"shot.{sid}.subtitle")
            if body:
                lines, fs = _fit_subtitle(body, wrap)
                s_start = start + 0.12
                s_end = max(s_start + 0.6, end - 0.10)
                style = "SubChar" if sid in char_shot_ids else "Sub"
                pos_y = 655
                if sid in char_shot_ids and len(lines) > 1:
                    # 双行英文避免压到左下名字条：字号略降 + 基线略下移
                    fs = min(fs, 36)
                    pos_y = 668
                events.append((s_start, s_end, 4, style,
                               f"{{\\an2\\pos(640,{pos_y})\\fad(110,90)\\fs{fs}}}"
                               + r"\N".join(_esc(x) for x in lines)))
        # 名字条/品牌/镜头标签已由重生成的本地化 clips 烘焙，这里不再叠加 ASS 文字层

    # 片头标题 / 片尾 CTA 已烘焙在本地化 clips 中（mock_shot locale），ASS 不再叠加

    events.sort(key=lambda e: (e[0], e[2]))
    header = ASS_HEADER.replace("{{FONT}}", font).replace(
        "{{TITLE}}", f"Zaopianju EP - {lang}")
    lines = [header]
    for start, end, layer, style, text in events:
        lines.append(f"Dialogue: {layer},{_ass_time(start)},{_ass_time(end)},{style},,0,0,0,,{text}")
    ass_text = "\n".join(lines) + "\n"

    subtitles = []
    for item in timings:
        shot = item["shot"]
        sid = _shot_id(shot)
        typ = str(shot.get("type") or "")
        if typ == "meme_card":
            continue
        if typ == "title_card":
            body = _fixed_brand_subtitle(lang)
        elif typ == "end_card":
            body = _fixed_end_cta(lang)
        else:
            body = tx.get(f"shot.{sid}.line") or tx.get(f"shot.{sid}.subtitle")
        if not body:
            continue
        s_start = float(item["start"]) + 0.12
        s_end = max(s_start + 0.6, float(item["end"]) - 0.10)
        subtitles.append({"start": round(s_start, 3), "end": round(s_end, 3),
                          "text": body})
    return {"ass": ass_text, "events": len(events), "subtitles": subtitles,
            "duration": round(total, 3), "font": font, "wrap": wrap}


# --------------------------------------------------------------------------
# 3) 配音
# --------------------------------------------------------------------------
def select_voices(lang: str, settings: dict | None = None) -> dict:
    defaults = dict(_lang_terms(lang)["voices"])
    overrides: dict = {}
    if isinstance(settings, dict):
        raw = settings.get("localize_voices")
        if isinstance(raw, dict):
            if isinstance(raw.get(lang), dict):
                overrides = {str(k): str(v) for k, v in raw[lang].items()}
            elif all(not isinstance(v, dict) for v in raw.values()):
                overrides = {str(k): str(v) for k, v in raw.items()}
    voices = dict(defaults)
    voices.update({k: v for k, v in overrides.items() if v})
    return voices


def _asset_voice_map(episode: dict, voices: dict) -> dict:
    """资产 id -> 音色：host->host，guest 按出现顺序 guest_a/guest_b/guest_c。"""
    mapping: dict[str, str] = {}
    guest_index = 0
    for asset in episode.get("assets") or []:
        if not isinstance(asset, dict):
            continue
        aid = str(asset.get("id") or "")
        if not aid:
            continue
        if str(asset.get("role") or "") == "host":
            mapping[aid] = voices.get("host") or config.DEFAULT_VOICE
        else:
            key = ("guest_a", "guest_b", "guest_c")[min(guest_index, 2)]
            guest_index += 1
            mapping[aid] = voices.get(key) or voices.get("guest_a") or config.DEFAULT_VOICE
    return mapping


def _atempo_for(tts_duration: float, shot_duration: float) -> float:
    """契约：TTS 过长用 atempo(1.0~1.25) 压缩，过短补静音。"""
    if tts_duration <= 0 or shot_duration <= 0:
        return 1.0
    target = max(0.6, shot_duration - 0.30)
    ratio = float(tts_duration) / target
    if ratio <= 1.0:
        return 1.0
    return round(min(max(ratio, 1.0), TTS_MAX_TEMPO), 4)


def _synthesize(text: str, voice: str, workdir: Path, mock: bool = False) -> dict:
    text = str(text or "").strip()
    if not text:
        return {"ok": False, "path": None, "duration": 0.0, "voice": voice}
    key = utils.sha1_text(text, voice, "loc")[:20]
    mp3 = utils.ensure_dir(workdir / "tts") / f"{key}.mp3"
    meta = mp3.with_suffix(".meta.json")
    cached = utils.read_json(meta)
    if isinstance(cached, dict) and _valid_file(mp3, 500):
        result = dict(cached)
        result["cached"] = True
        return result
    try:
        if mock:
            duration = max(0.5, min(6.0, len(text) * 0.11))
            tts_engine.make_silence_mp3(mp3, duration=duration)
            result = {"ok": True, "path": str(mp3), "duration": round(duration, 3),
                      "voice": voice, "mock": True}
        else:
            synth = tts_engine.synth_with_retry(text, voice, mp3)
            result = {"ok": True, "path": str(mp3),
                      "duration": round(float(synth["duration"]), 3), "voice": voice}
        utils.write_json(meta, result)
        return result
    except Exception as exc:  # noqa: BLE001 - 单条失败用静音兜底
        LOG.warning("TTS 失败（静音兜底）[%s] %s: %s", voice, text[:20], exc)
        try:
            tts_engine.make_silence_mp3(mp3, duration=1.0)
            return {"ok": False, "path": str(mp3), "duration": 1.0,
                    "voice": voice, "error": str(exc)[:200]}
        except Exception:  # noqa: BLE001
            return {"ok": False, "path": None, "duration": 0.0,
                    "voice": voice, "error": str(exc)[:200]}


def _build_shot_audio(shot: dict, tts_info: dict, duration: float, workdir: Path) -> dict:
    sid = _shot_id(shot)
    audio_dir = utils.ensure_dir(workdir / "audio")
    out = audio_dir / f"{sid}.wav"
    tmp = audio_dir / f"{sid}.tmp.wav"
    atempo = _atempo_for(float((tts_info or {}).get("duration") or 0.0), duration)
    has_voice = bool(tts_info and tts_info.get("path") and _valid_file(tts_info["path"], 500))
    if has_voice:
        chain = [f"aresample={config.SAMPLE_RATE}", "pan=stereo|c0=c0|c1=c0"]
        if atempo > 1.001:
            chain.append(f"atempo={atempo:.4f}")
        delay_ms = int(TTS_LEAD_IN * 1000)
        chain += [f"adelay={delay_ms}|{delay_ms}", "apad"]
        utils.ffmpeg(["-i", str(tts_info["path"]), "-af", ",".join(chain),
                      "-t", f"{duration:.3f}", "-ar", str(config.SAMPLE_RATE),
                      "-ac", "2", "-c:a", "pcm_s16le", str(tmp)], cwd=workdir)
    else:
        utils.ffmpeg(["-f", "lavfi", "-i", f"anullsrc=r={config.SAMPLE_RATE}:cl=stereo",
                      "-t", f"{duration:.3f}", "-ar", str(config.SAMPLE_RATE),
                      "-ac", "2", "-c:a", "pcm_s16le", str(tmp)], cwd=workdir)
    # 二次校准：保证单镜音轨严格等于镜头时长（拼接后时间线不漂移）
    actual = utils.probe_duration(tmp)
    if abs(actual - duration) > 0.02:
        if actual < duration:
            utils.ffmpeg(["-i", str(tmp), "-af", "apad", "-t", f"{duration:.3f}",
                          "-ar", str(config.SAMPLE_RATE), "-ac", "2",
                          "-c:a", "pcm_s16le", str(out)], cwd=workdir)
        else:
            utils.ffmpeg(["-i", str(tmp), "-af",
                          f"atrim=0:{duration:.3f},asetpts=PTS-STARTPTS",
                          "-t", f"{duration:.3f}", "-ar", str(config.SAMPLE_RATE),
                          "-ac", "2", "-c:a", "pcm_s16le", str(out)], cwd=workdir)
        try:
            tmp.unlink()
        except OSError:
            pass
    else:
        tmp.replace(out)
    return {"id": sid, "path": str(out), "duration": duration,
            "atempo": atempo, "has_voice": has_voice}


# --------------------------------------------------------------------------
# 4) 画面复用 / 字体
# --------------------------------------------------------------------------
def _prepare_clips(episode: dict, eid: str, workdir: Path,
                  src_root: Path | None = None) -> dict:
    src_root = Path(src_root) if src_root else (
        config.REPO_ROOT / "outputs" / "episodes" / eid)
    clips_dir = utils.ensure_dir(workdir / "clips")
    reused = 0
    missing: list[str] = []
    for shot in episode.get("shots") or []:
        sid = _shot_id(shot)
        dst = clips_dir / f"{sid}.mp4"
        if _valid_file(dst, 1000):
            reused += 1
            continue
        src = None
        for candidate in (src_root / "clips" / f"{sid}.mp4",
                          src_root / "shots" / f"{sid}.mp4"):
            if _valid_file(candidate, 1000):
                src = candidate
                break
        if src is None:
            missing.append(sid)
            continue
        try:
            os.link(src, dst)
        except OSError:
            shutil.copyfile(src, dst)
        reused += 1
    if missing:
        raise RuntimeError(f"缺少镜头画面（先渲染原片再本地化）: {missing[:8]}")
    return {"reused": reused, "clips_dir": str(clips_dir)}


def _prepare_fonts(lang: str, workdir: Path) -> str:
    font_family = _lang_terms(lang)["fonts"]
    font_dir = utils.ensure_dir(workdir / "fonts")
    for src in FONT_FILES.get(lang, FONT_FILES["en"]):
        p = Path(src)
        if not p.is_file():
            continue
        dst = font_dir / p.name
        if not dst.exists() or dst.stat().st_size != p.stat().st_size:
            shutil.copyfile(p, dst)
    return font_family


# --------------------------------------------------------------------------
# 5) 发布元数据
# --------------------------------------------------------------------------
def _publish_metadata(ep: dict, tx: dict, lang: str) -> dict:
    title = tx.get("episode.title") or str(ep.get("title") or "")
    no = int(ep.get("no") or 1)
    tagline = "AI Micro-Variety" if lang != "zh" else "AI 微综艺"
    hashtags = {
        "en": ["#AIVariety", "#AIComedy", "#Zaopianju", "#AIContent", "#Shorts"],
        "es": ["#VariedadIA", "#ComediaIA", "#Zaopianju", "#ContenidoIA", "#Shorts"],
        "ja": ["#AIバラエティ", "#AIコメディ", "#造片局", "#AI動画"],
        "ko": ["#AI예능", "#AI코미디", "#Zaopianju", "#AI콘텐츠"],
        "pt": ["#VariedadeIA", "#ComediaIA", "#Zaopianju", "#ConteudoIA"],
    }.get(lang, ["#AIVariety", "#Zaopianju"])
    description = {
        "en": f"{title} — {tagline} EP.{no:03d}. AI hosts and guests debate whether AI can really make variety shows.",
        "es": f"{title} — {tagline} EP.{no:03d}. Presentadores e invitados de IA debaten si la IA puede hacer variedades.",
        "ja": f"{title} — {tagline} EP.{no:03d}。AI司会とゲストがAIバラエティの可能性を語ります。",
        "ko": f"{title} — {tagline} EP.{no:03d}. AI 진행자와 게스트가 AI 예능의 가능성을 토론합니다.",
        "pt": f"{title} — {tagline} EP.{no:03d}. Apresentadores e convidados de IA debatem se a IA pode fazer variedades.",
    }.get(lang, f"{title} — {tagline} EP.{no:03d}")
    return {"title": title, "description": description, "hashtags": hashtags}


# --------------------------------------------------------------------------
# 6) 主流程
# --------------------------------------------------------------------------
def localize_episode(episode: dict, lang: str, outdir: Path | str,
                     translator: str = "ollama", settings: dict | None = None,
                     mock_tts: bool = False, force_translate: bool = False,
                     comfy_python: str | None = None, no_bgm: bool = False,
                     clips_root: Path | str | None = None,
                     char_map: dict | None = None) -> dict:
    if lang not in SUPPORTED_LANGS:
        raise RuntimeError(f"不支持的语言: {lang}（可选 {', '.join(SUPPORTED_LANGS)}）")
    t_start = time.time()
    ep = episode.get("episode") or episode
    eid = str(ep.get("id") or "")
    if not eid:
        raise RuntimeError("episode JSON 缺少 episode.id")
    outdir = utils.ensure_dir(Path(outdir).resolve())
    workdir = utils.ensure_dir(outdir / "work")
    prog = utils.Progress()
    width, height = _ep_resolution(ep)
    fps = int(ep.get("fps") or config.FPS)
    shots = [s for s in episode.get("shots") or [] if isinstance(s, dict)]
    total_duration = round(sum(_shot_duration(s) for s in shots), 3)

    # 1) 翻译
    prog(2, "收集翻译单元")
    units = collect_units(episode)
    if not units:
        raise RuntimeError("没有可翻译的文本单元")
    utils.write_json(outdir / "units.json", units)
    prog(5, f"翻译 {len(units)} 个单元（{translator}）")
    tx_result = translate_units(units, lang, translator, outdir,
                                comfy_python=comfy_python, force=force_translate)
    tx = _translated_map(units, tx_result)
    cjk_fixed = _sanitize_translations(tx, lang, translator)
    if cjk_fixed:
        tx_result["cjk_fixed"] = cjk_fixed
        # 清洗后写回缓存，避免下次重复清洗
        cached_payload = dict(tx_result)
        cached_payload["translations"] = tx
        utils.write_json(outdir / "translations.json", cached_payload)
    LOG.info("翻译完成: ok=%s provider=%s cached=%s units=%d cjk_fixed=%d",
             tx_result.get("ok"), tx_result.get("provider"),
             tx_result.get("cached"), len(units), cjk_fixed)

    # 2) 本地化 manifest + 重生成目标语 clips（品牌/镜头标签/名字条/片头片尾全部译文）
    prog(10, "构建本地化镜头文案")
    locale = _locale_for(lang)
    local_assets = _localize_assets(episode.get("assets") or [], tx, lang)
    local_shots = _localize_shots(shots, tx, lang, ep_no=int(ep.get("no") or 1))
    local_episode = {
        "episode": dict(ep),
        "assets": local_assets,
        "shots": local_shots,
        "segments": episode.get("segments") or [],
    }
    episode_arg = {
        "aspect": ep.get("aspect") or "16:9",
        "resolution": f"{width}x{height}",
        "fps": fps,
        "id": eid,
        "no": ep.get("no"),
        "title": ep.get("title"),
    }
    if char_map is None:
        all_chars = characters.discover()
        char_map = characters.resolve_for_assets(episode.get("assets") or [], all_chars)
    char_fp = characters.fingerprint(char_map) if char_map else ""
    prog(12, f"生成目标语镜头画面（{lang}，{len(local_shots)} 镜）")
    clip_info = _regenerate_clips(local_shots, local_assets, episode_arg, workdir,
                                  width, height, fps, char_map or {}, char_fp,
                                  locale, prog=prog)

    # 3) ASS / SRT（字幕 + 段落标签；包装文字已烘焙进 clips）
    prog(54, "生成目标语字幕/包装")
    _prepare_fonts(lang, workdir)
    ass_info = build_ass(local_episode, tx, lang)
    utils.write_text(workdir / "final.ass", ass_info["ass"])
    utils.write_text(outdir / "subtitles.srt", _srt(ass_info["subtitles"]))

    # 4) 配音
    voices = select_voices(lang, settings)
    voice_map = _asset_voice_map(episode, voices)
    prog(24, f"合成 {lang} 配音")
    tts_stats = {"lines": 0, "failures": 0, "atempo": []}
    tasks: list[dict] = []
    for shot in shots:
        sid = _shot_id(shot)
        line = tx.get(f"shot.{sid}.line") or ""
        speaker = str(shot.get("speaker") or "")
        voice = voice_map.get(speaker) or voices.get("host") or config.DEFAULT_VOICE
        tasks.append({"sid": sid, "line": line, "voice": voice, "shot": shot})

    def _one(task: dict) -> tuple[dict, dict]:
        if not task["line"]:
            return task, {"ok": False, "path": None, "duration": 0.0,
                          "voice": task["voice"]}
        return task, _synthesize(task["line"], task["voice"], workdir, mock=mock_tts)

    results: dict[str, dict] = {}
    workers = max(1, int(getattr(config, "TTS_CONCURRENCY", 5)))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for task, tts_info in pool.map(_one, tasks):
            results[task["sid"]] = tts_info
    for task in tasks:
        shot = task["shot"]
        tts_info = results.get(task["sid"]) or {"ok": False, "path": None, "duration": 0.0}
        if task["line"]:
            tts_stats["lines"] += 1
            if not tts_info.get("ok"):
                tts_stats["failures"] += 1
        audio = _build_shot_audio(shot, tts_info, _shot_duration(shot), workdir)
        tts_stats["atempo"].append(audio["atempo"])

    # 5) 拼接 + 编码
    prog(62, "拼接视频/音轨")
    render.concat_video(local_shots if local_shots else shots, workdir, fps, total_duration)
    narration = render.concat_audio(shots, workdir, total_duration)
    bgm = None if no_bgm else render.ensure_bgm(workdir)
    prog(70, "编码目标语成片（烧字幕+混音+loudnorm）")
    out_video = outdir / "final.mp4"
    render.final_encode(workdir, out_video, total_duration, fps, width, height,
                        bgm, has_ass=ass_info["events"] > 0,
                        prog=prog, pct0=70, pct1=98)
    shutil.copyfile(narration, outdir / "narration.wav")
    duration = utils.probe_duration(out_video)

    meta = {
        "ok": True,
        "episode_id": eid,
        "lang": lang,
        "provider": "pipeline.localize",
        "version": __version__,
        "duration": round(duration, 3),
        "target_duration": total_duration,
        "duration_delta": round(duration - total_duration, 3),
        "resolution": f"{width}x{height}",
        "fps": fps,
        "translator": {k: tx_result.get(k) for k in
                       ("ok", "provider", "model", "cached", "elapsed_sec", "count", "error")},
        "translate_units": len(units),
        "translation_cjk_fixed": int(cjk_fixed),
        "voices": voices,
        "tts": {"lines": tts_stats["lines"], "failures": tts_stats["failures"],
                "atempo_max": max(tts_stats["atempo"] or [1.0]),
                "atempo_min": min(tts_stats["atempo"] or [1.0])},
        "clips": {"regenerated": clip_info.get("regenerated", 0),
                  "reused": clip_info.get("reused", 0)},
        "ass": {"events": ass_info["events"], "font": ass_info["font"],
                "wrap": ass_info["wrap"]},
        "bgm": bgm is not None,
        "mock_tts": bool(mock_tts),
        "publish": _publish_metadata(ep, tx, lang),
        "files": {"video": str(out_video), "narration": str(outdir / "narration.wav"),
                  "srt": str(outdir / "subtitles.srt"),
                  "translations": str(outdir / "translations.json"),
                  "units": str(outdir / "units.json")},
        "elapsed_sec": round(time.time() - t_start, 2),
    }
    utils.write_json(outdir / "meta.json", meta)
    prog(100, "完成")
    return meta


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------
def _force_utf8_stdio() -> None:
    for name in ("stdout", "stderr"):
        stream = getattr(sys, name, None)
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass


def cmd_localize(args: argparse.Namespace) -> int:
    episode_path = Path(args.episode_json)
    data = utils.read_json(episode_path)
    if not isinstance(data, dict):
        utils.emit_json_line({"ok": False, "error": f"无法读取 episode JSON: {episode_path}"})
        return 1
    settings = utils.read_json(args.settings) if args.settings else {}
    if not isinstance(settings, dict):
        settings = {}
    if args.voices_json:
        voices_raw = utils.read_json(args.voices_json)
        if isinstance(voices_raw, dict):
            settings = dict(settings)
            settings["localize_voices"] = voices_raw
    try:
        meta = localize_episode(
            data, args.lang, args.outdir, translator=args.translator,
            settings=settings if isinstance(settings, dict) else {},
            mock_tts=bool(args.mock_tts),
            force_translate=bool(args.force_translate),
            comfy_python=args.comfy_python, no_bgm=bool(args.no_bgm),
        )
        utils.emit_json_line(meta)
        return 0 if meta.get("ok") else 1
    except Exception as exc:  # noqa: BLE001
        LOG.error("本地化失败: %s", exc)
        LOG.debug("%s", __import__("traceback").format_exc())
        utils.emit_json_line({"ok": False, "error": str(exc)})
        return 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m pipeline.localize",
        description="造片局 · 多语种本地化渲染（画面复用 + 目标语配音/字幕/包装）",
    )
    parser.add_argument("--version", action="version", version=__version__)
    parser.add_argument("-v", "--verbose", action="store_true")
    parser.add_argument("--episode-json", required=True, help="后端序列化的 episode JSON")
    parser.add_argument("--lang", required=True, choices=list(SUPPORTED_LANGS))
    parser.add_argument("--outdir", required=True,
                        help="输出目录，如 outputs/episodes/{eid}/localized/en")
    parser.add_argument("--translator", default="ollama",
                        choices=["ollama", "nllb", "mock"],
                        help="翻译 provider（默认 ollama=qwen2.5:7b，nllb 兜底，mock 测试）")
    parser.add_argument("--provider", dest="translator",
                        choices=["ollama", "nllb", "mock"], help=argparse.SUPPRESS)
    parser.add_argument("--settings", default=None, help="settings JSON（localize_voices 覆盖）")
    parser.add_argument("--voices-json", default=None,
                        help='音色覆盖 JSON，如 {"en":{"host":"en-US-GuyNeural",...}}')
    parser.add_argument("--comfy-python", default=None, help="ComfyUI venv python（NLLB 用）")
    parser.add_argument("--mock-tts", action="store_true", help="静音模拟 TTS（离线测试）")
    parser.add_argument("--force-translate", action="store_true", help="忽略翻译缓存")
    parser.add_argument("--no-bgm", action="store_true", help="不加 BGM")
    parser.set_defaults(func=cmd_localize)
    return parser


def main(argv: list[str] | None = None) -> int:
    _force_utf8_stdio()
    parser = build_parser()
    args = parser.parse_args(argv)
    utils.setup_logging(bool(getattr(args, "verbose", False)))
    try:
        return int(args.func(args))
    except KeyboardInterrupt:
        return 130
    except Exception as exc:  # noqa: BLE001
        LOG.error("执行失败: %s", exc)
        utils.emit_json_line({"ok": False, "error": str(exc)})
        return 1


if __name__ == "__main__":
    sys.exit(main())
