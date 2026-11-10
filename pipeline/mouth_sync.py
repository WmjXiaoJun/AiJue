# -*- coding: utf-8 -*-
"""V5.6 口型同步：用**实际 TTS 音频能量包络**驱动口型（替代纯文本等分时间轴）。

动机：用户反馈"嘴形完全没对上"。原实现 viseme_timeline 把台词字符按权重均匀铺满
镜头时长，与 TTS 实际停顿/语速无关 → 口型与声音不同步。本模块按每镜 wav 的
短时能量（RMS）生成口型时间轴：有声音才张嘴、能量大张大、静音闭口；
再用台词字符的形状序列做形状选择（a/o/e/i/u/m），保证嘴形有变化。

接口兼容 characters_v5.viseme_timeline 的 [{"t0","t1","viseme"}] 列表。
"""
from __future__ import annotations

import hashlib
import math
import wave
from pathlib import Path

import numpy as np

MOUTH_OPEN_ORDER = ("a", "o", "e", "i", "u", "m")


class MouthSyncError(RuntimeError):
    pass


def _read_wav_mono(path: Path) -> tuple[np.ndarray, int]:
    p = Path(path)
    if not p.is_file():
        raise MouthSyncError(f"音频不存在: {p}")
    with wave.open(str(p), "rb") as fh:
        sr = fh.getframerate()
        ch = fh.getnchannels()
        n = fh.getnframes()
        raw = fh.readframes(n)
        width = fh.getsampwidth()
    if width != 2:
        raise MouthSyncError(f"仅支持 pcm_s16le wav（当前 {width*8}bit）")
    data = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    if ch > 1:
        data = data.reshape(-1, ch).mean(axis=1)
    return data, sr


def _shape_for(char: str, index: int) -> str:
    """按字符/序号选一个"小口型"形状（避免全部一样）。"""
    table = {"a": "a", "o": "o", "e": "e", "i": "i", "u": "u", "m": "m"}
    base = hashlib.md5(f"{char}|{index}".encode("utf-8")).hexdigest()
    pool = tuple(table.values())
    return pool[int(base[:4], 16) % len(pool)]


def envelope_timeline(audio_path: str | Path, fps: float = 25.0,
                      text: str = "", lead: float = 0.10) -> list[dict]:
    """按音频能量生成口型时间轴（与音频严格对齐）。

    返回 [{"t0","t1","viseme"}]；viseme ∈ closed/a/o/e/i/u/m。
    """
    data, sr = _read_wav_mono(Path(audio_path))
    fps = max(1.0, float(fps))
    frame = 1.0 / fps
    hop = max(1, int(round(sr * frame)))
    n_frames = max(1, int(math.ceil(len(data) / hop)))
    rms = np.zeros(n_frames, dtype=np.float32)
    for i in range(n_frames):
        seg = data[i * hop:(i + 1) * hop]
        if seg.size:
            rms[i] = float(np.sqrt(np.mean(seg.astype(np.float64) ** 2)))
    # 噪声底（最安静 20% 的中位数）与上限（95 分位）
    floor = float(np.median(np.sort(rms)[:max(1, int(n_frames * 0.2))]))
    peak = float(np.percentile(rms, 95)) if n_frames > 2 else float(rms.max())
    span = max(1e-4, peak - floor)
    norm = np.clip((rms - floor) / span, 0.0, 1.0)
    # 3 帧平滑
    smooth = np.copy(norm)
    for i in range(1, n_frames - 1):
        smooth[i] = 0.25 * norm[i - 1] + 0.5 * norm[i] + 0.25 * norm[i + 1]
    chars = [c for c in str(text) if not c.isspace()]
    # lead：开头 N 帧强制闭口（TTS 起始静音/换气），此参数以前被忽略
    lead_frames = max(0, int(round(max(0.0, float(lead)) * fps)))
    voiced = [i for i in range(n_frames) if float(smooth[i]) >= 0.30]
    voiced_count = max(1, len(voiced))
    voiced_seen = 0
    out: list[dict] = []
    for i in range(n_frames):
        level = float(smooth[i])
        t0 = round(i * frame, 3)
        t1 = round((i + 1) * frame, 3)
        if i < lead_frames or level < 0.10:
            viseme = "closed"
        elif level < 0.30:
            # 低能量段按「当前发声进度」取字，和台词位置对齐（不再 i%len 循环）
            ci = min(len(chars) - 1, int(voiced_seen * len(chars) / voiced_count)) if chars else 0
            viseme = _shape_for(chars[ci] if chars else "a", i)
            if viseme == "a":
                viseme = "e"
        elif level < 0.60:
            viseme = "o" if (i % 3) else "e"
            voiced_seen += 1
        else:
            viseme = "a"
            voiced_seen += 1
        if out and out[-1]["viseme"] == viseme:
            out[-1]["t1"] = t1
        else:
            out.append({"t0": t0, "t1": t1, "viseme": viseme})
    return out


def coverage_ratio(timeline: list[dict], t0: float, t1: float) -> float:
    """时间窗内"张嘴"占比（调试/验收用）。"""
    total = max(1e-6, t1 - t0)
    opened = 0.0
    for e in timeline:
        a, b = max(e["t0"], t0), min(e["t1"], t1)
        if b > a and e["viseme"] != "closed":
            opened += b - a
    return opened / total


__all__ = ["envelope_timeline", "coverage_ratio", "MouthSyncError"]
