# -*- coding: utf-8 -*-
"""多语种翻译：优先本地 Ollama qwen2.5:7b（质量好），回退 NLLB-200。

Ollama provider 默认（http://127.0.0.1:11434，model=qwen2.5:7b）。
NLLB provider 需要 ComfyUI venv 的 torch/transformers（本 CLI 在 ComfyUI venv 下运行时可用）。
"""
from __future__ import annotations

import json
import re
import time
import urllib.request
from pathlib import Path
from typing import Iterable

REPO = Path(__file__).resolve().parents[1]
MODEL_DIR = REPO / "localize" / "models" / "nllb-200-distilled-600M"
OLLAMA_URL = "http://127.0.0.1:11434/api/generate"
OLLAMA_MODEL = "qwen2.5:7b"

LANG_CODES = {
    "en": "eng_Latn",
    "es": "spa_Latn",
    "ja": "jpn_Jpan",
    "ko": "kor_Hang",
    "pt": "por_Latn",
    "fr": "fra_Latn",
    "de": "deu_Latn",
    "ar": "arb_Arab",
    "zh": "zho_Hans",
}

LANG_NAMES = {
    "en": "English",
    "es": "Spanish",
    "ja": "Japanese",
    "ko": "Korean",
    "pt": "Portuguese",
    "fr": "French",
    "de": "German",
    "ar": "Arabic",
}

CJK_LEFT = re.compile(r"[\u4e00-\u9fff]")
CJK_KEEP = {"ja", "zh"}

# 人名罗马化（源文本中直接替换，避免音译漂移）
NAME_MAP = {
    "老周": "Zhou",
    "阿栗": "Ali",
    "大魏": "Dawei",
}

_nllb = {"model": None, "tokenizer": None}


def prepare_source(text: str) -> str:
    out = text or ""
    for zh, ro in NAME_MAP.items():
        out = out.replace(zh, ro)
    return out


# --------------------------------------------------------------------------- #
# Ollama（默认）
# --------------------------------------------------------------------------- #
def _ollama_generate(prompt: str, timeout: float = 600.0) -> str:
    payload = {
        "model": OLLAMA_MODEL,
        "prompt": prompt,
        "stream": False,
        "options": {"temperature": 0.2, "num_predict": 4096},
    }
    req = urllib.request.Request(OLLAMA_URL, data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8")).get("response", "")


def _ollama_prompt(batch: list[dict], target_lang: str) -> str:
    lang_name = LANG_NAMES.get(target_lang, target_lang)
    return (
        f"你是专业的影视本地化译者，把下面的中文台词翻译成{lang_name}。要求：\n"
        "1) 口语自然、符合综艺对白语气；2) 人名保持 Zhou/Ali/Dawei 不翻译，AI 保留；\n"
        "3) 固定译法：造片局=Zaopianju，微综艺=Micro-Variety，综艺=variety show，翻车=flop/wipeout；\n"
        "4) 逐条翻译，不增不减；5) 只输出 JSON 数组，每项含 key 和 text 两个字段，不要 markdown、不要解释。\n"
        + json.dumps(batch, ensure_ascii=False)
    )


def _parse_json_array(text: str) -> list[dict]:
    text = text.strip()
    if text.startswith("~~~"):
        text = re.sub(r"^~~~[a-zA-Z]*", "", text).strip()
        if text.endswith("~~~"):
            text = text[:-3].strip()
    start = text.find("[")
    end = text.rfind("]")
    if start < 0 or end <= start:
        raise ValueError("响应中没有 JSON 数组")
    data = json.loads(text[start:end + 1])
    if not isinstance(data, list):
        raise ValueError("JSON 不是数组")
    return [d for d in data if isinstance(d, dict) and "key" in d]


def _translate_ollama(units: list[dict], target_lang: str, batch_size: int = 8) -> dict[str, str]:
    out: dict[str, str] = {}
    for i in range(0, len(units), batch_size):
        batch = units[i:i + batch_size]
        got: dict[str, str] = {}
        try:
            raw = _ollama_generate(_ollama_prompt(batch, target_lang))
            for item in _parse_json_array(raw):
                got[str(item["key"])] = str(item.get("text") or "").strip()
        except Exception:  # noqa: BLE001 - 批次失败时逐条重试
            pass
        for unit in batch:
            key = str(unit["key"])
            text = got.get(key)
            if not text or (target_lang not in CJK_KEEP and CJK_LEFT.search(text)):
                raw = _ollama_generate(
                    f"把下面这一句中文翻译成{LANG_NAMES.get(target_lang, target_lang)}，口语自然；人名保持 Zhou/Ali/Dawei；只输出译文本身：\n{text or unit.get('text') or ''}"
                ).strip()
                text = raw.splitlines()[0].strip() if raw else str(unit.get("text") or "")
            out[key] = text
    return out


# --------------------------------------------------------------------------- #
# NLLB（离线回退）
# --------------------------------------------------------------------------- #
def _load_nllb():
    if _nllb["model"] is None:
        import torch
        from transformers import AutoModelForSeq2SeqLM, AutoTokenizer

        if not MODEL_DIR.exists():
            raise FileNotFoundError(f"NLLB 模型不存在: {MODEL_DIR}")
        _nllb["tokenizer"] = AutoTokenizer.from_pretrained(str(MODEL_DIR))
        model = AutoModelForSeq2SeqLM.from_pretrained(str(MODEL_DIR))
        device = "cuda" if torch.cuda.is_available() else "cpu"
        model.to(device)
        model.eval()
        _nllb["model"] = model
    return _nllb["model"], _nllb["tokenizer"]


def _translate_nllb(units: list[dict], target_lang: str, batch_size: int = 8) -> dict[str, str]:
    model, tokenizer = _load_nllb()
    import torch

    tokenizer.src_lang = LANG_CODES["zh"]
    forced_bos = tokenizer.convert_tokens_to_ids(LANG_CODES[target_lang])
    texts = [prepare_source(str(u.get("text") or "")) for u in units]
    out: dict[str, str] = {}
    for i in range(0, len(texts), batch_size):
        batch = texts[i:i + batch_size]
        enc = tokenizer(batch, return_tensors="pt", padding=True, truncation=True, max_length=512)
        enc = {k: v.to(model.device) for k, v in enc.items()}
        with torch.no_grad():
            generated = model.generate(**enc, forced_bos_token_id=forced_bos, max_new_tokens=256)
        decoded = tokenizer.batch_decode(generated, skip_special_tokens=True)
        for unit, text in zip(units[i:i + batch_size], decoded):
            out[str(unit["key"])] = text.strip()
    return out


# --------------------------------------------------------------------------- #
# 统一入口
# --------------------------------------------------------------------------- #
def translate_texts(texts: Iterable[str], target_lang: str, provider: str = "ollama") -> list[str]:
    units = [{"key": str(i), "text": t} for i, t in enumerate(texts)]
    result = translate_units(units, target_lang, provider=provider)["translations"]
    return [result[str(i)] for i in range(len(units))]


def translate_units(units: list[dict], target_lang: str, provider: str = "ollama") -> dict:
    if target_lang not in LANG_CODES:
        raise ValueError(f"不支持的语言: {target_lang}")
    t0 = time.time()
    if provider == "mock":
        translations = {str(u["key"]): f"[{target_lang}] {u.get('text') or ''}" for u in units}
        model = "mock"
    elif provider == "nllb":
        translations = _translate_nllb(units, target_lang)
        model = "facebook/nllb-200-distilled-600M"
    else:
        translations = _translate_ollama(units, target_lang)
        model = f"ollama/{OLLAMA_MODEL}"
    return {
        "ok": True,
        "lang": target_lang,
        "provider": provider,
        "model": model,
        "count": len(translations),
        "elapsed_sec": round(time.time() - t0, 1),
        "translations": translations,
    }
