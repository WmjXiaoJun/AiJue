"""edge-tts 封装：生成 mp3 + 词级 VTT；失败重试，渲染侧可静音兜底。"""
from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path

from . import config, utils

LOG = logging.getLogger("pipeline")


def _vtt_ts(seconds: float) -> str:
    seconds = max(0.0, float(seconds))
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = seconds % 60
    return f"{h:02d}:{m:02d}:{s:06.3f}"


def _vtt_escape(text: str) -> str:
    return str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def build_vtt(words: list[dict]) -> str:
    lines = ["WEBVTT", ""]
    for w in words:
        text = str(w.get("text") or "").strip()
        if not text:
            continue
        start = float(w.get("offset") or 0.0)
        end = start + float(w.get("duration") or 0.2)
        lines.append(f"{_vtt_ts(start)} --> {_vtt_ts(end)}")
        lines.append(_vtt_escape(text))
        lines.append("")
    return "\n".join(lines)


def _synth_async(text: str, voice: str, out_mp3: Path, rate: str | None) -> list[dict]:
    import edge_tts

    communicate = edge_tts.Communicate(
        text, voice, rate=rate or config.TTS_RATE, boundary="WordBoundary"
    )
    words: list[dict] = []
    with open(out_mp3, "wb") as fh:
        async def _consume() -> None:
            async for chunk in communicate.stream():
                ctype = chunk.get("type")
                if ctype == "audio" and chunk.get("data"):
                    fh.write(chunk["data"])
                elif ctype in ("WordBoundary", "SentenceBoundary"):
                    words.append({
                        "offset": float(chunk.get("offset") or 0) / 1e7,
                        "duration": float(chunk.get("duration") or 0) / 1e7,
                        "text": chunk.get("text") or "",
                    })
        asyncio.run(_consume())
    return words


def synth(text: str, voice: str, out_mp3, out_vtt=None,
          rate: str | None = None) -> dict:
    """单次合成（无重试）。"""
    text = str(text or "").strip()
    if not text:
        raise ValueError("TTS 文本为空")
    out = Path(out_mp3)
    utils.ensure_dir(out.parent)
    words = _synth_async(text, voice, out, rate)
    duration = utils.probe_duration(out)
    if duration <= 0.05:
        raise RuntimeError("TTS 输出音频时长为 0")
    result = {
        "ok": True,
        "path": str(out),
        "vtt": None,
        "duration": round(duration, 3),
        "voice": voice,
        "chars": len(text),
        "words": len(words),
    }
    if out_vtt:
        vtt_path = Path(out_vtt)
        utils.ensure_dir(vtt_path.parent)
        utils.write_text(vtt_path, build_vtt(words))
        result["vtt"] = str(vtt_path)
    return result


def synth_with_retry(text: str, voice: str, out_mp3, out_vtt=None,
                     rate: str | None = None,
                     retries: int | None = None) -> dict:
    """带重试的合成；全部失败抛 RuntimeError。"""
    attempts = max(1, int(retries if retries is not None else config.TTS_RETRIES))
    last: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            return synth(text, voice, out_mp3, out_vtt, rate=rate)
        except Exception as exc:  # noqa: BLE001
            last = exc
            LOG.warning("TTS 第 %d/%d 次失败: %s", attempt, attempts, exc)
            if attempt < attempts:
                time.sleep(0.8 * attempt)
    raise RuntimeError(f"TTS 连续 {attempts} 次失败: {last}")


def make_silence_mp3(out_path, duration: float = 1.0,
                     sample_rate: int = 24000) -> Path:
    """生成静音 mp3（TTS 失败兜底）。"""
    out = Path(out_path)
    utils.ensure_dir(out.parent)
    utils.ffmpeg([
        "-f", "lavfi",
        "-i", f"anullsrc=r={sample_rate}:cl=mono",
        "-t", f"{max(0.05, float(duration)):.3f}",
        "-c:a", "libmp3lame", "-q:a", "5",
        str(out),
    ])
    return out
