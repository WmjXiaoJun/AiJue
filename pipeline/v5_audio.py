# -*- coding: utf-8 -*-
"""V5 风格化配乐：按 style.audio.bgm preset 程序化合成分轨 BGM（48kHz 立体声）。

不依赖任何采样库/网络；每个 preset 用简单合成（加法/减法、包络、音高序列）做出可辨识的氛围，
再由成片 final_encode 做 ducking + 两遍 loudnorm。目标：16 个风格听感明显不同、可循环、无版权风险。
"""
from __future__ import annotations

import math
import wave
from pathlib import Path

import numpy as np

from . import config, utils

LOG = __import__("logging").getLogger("pipeline")

SR = 48000
DEFAULT_SECONDS = 60.0

# 音名 → 频率（C4=261.63）
_NOTE_BASE = {"C": 0, "C#": 1, "D": 2, "D#": 3, "E": 4, "F": 5, "F#": 6,
              "G": 7, "G#": 8, "A": 9, "A#": 10, "B": 11}


def note_freq(name: str) -> float:
    token = str(name or "").strip()
    if not token:
        return 0.0
    base = token[0].upper()
    idx = token.find("#")
    if idx == 1:
        base = token[:2]
    elif idx == 1 and len(token) > 1:
        base = token[:2]
    semis = _NOTE_BASE.get(base)
    if semis is None:
        return 0.0
    octave = 4
    for ch in token[1:]:
        if ch.isdigit():
            octave = int(ch)
    return 440.0 * (2 ** ((semis + (octave - 4) * 12 - 9) / 12.0))


def _env(n: int, attack: float, decay: float, sustain: float = 0.0) -> np.ndarray:
    a = max(1, int(attack * SR))
    d = max(1, int(decay * SR))
    env = np.zeros(n, dtype=np.float32)
    a = min(a, n)
    env[:a] = np.linspace(0, 1, a, dtype=np.float32)
    rest = n - a
    if rest > 0:
        dd = min(d, rest)
        env[a:a + dd] = np.linspace(1, sustain if sustain else 0.0, dd, dtype=np.float32)
        if rest > dd:
            env[a + dd:] = sustain
    return env


def _pluck(freq: float, dur: float, *, harmonics=(1.0, 0.5, 0.25, 0.12),
           decay: float = 0.35, detune: float = 0.0) -> np.ndarray:
    n = int(dur * SR)
    if n <= 0 or freq <= 0:
        return np.zeros(max(0, n), dtype=np.float32)
    t = np.arange(n, dtype=np.float32) / SR
    out = np.zeros(n, dtype=np.float32)
    for i, amp in enumerate(harmonics):
        f = freq * (i + 1) * (1.0 + detune * (i + 1))
        if f > SR / 2:
            break
        out += amp * np.sin(2 * math.pi * f * t)
    env = _env(n, 0.004, dur * 0.8, 0.0)
    return (out / max(1e-6, float(np.max(np.abs(out))))) * env


def _pad(freqs, dur: float, *, attack=0.8, release=1.2, detune=0.15,
         harmonics=(1.0, 0.35, 0.15)) -> np.ndarray:
    n = int(dur * SR)
    t = np.arange(n, dtype=np.float32) / SR
    out = np.zeros(n, dtype=np.float32)
    for f in freqs:
        for i, amp in enumerate(harmonics):
            fh = f * (i + 1)
            if fh > SR / 2:
                break
            for d in (-detune, 0.0, detune):
                out += amp * np.sin(2 * math.pi * fh * (1 + d) * t
                                    + np.random.uniform(0, 6.28))
    env = _env(n, attack, release, 0.85)
    return (out / max(1e-6, float(np.max(np.abs(out))))) * env


def _kick(dur: float = 0.18, freq0: float = 120.0, freq1: float = 45.0) -> np.ndarray:
    n = int(dur * SR)
    t = np.arange(n, dtype=np.float32) / SR
    f = np.linspace(freq0, freq1, n)
    return (np.sin(2 * math.pi * f * t) * _env(n, 0.002, dur * 0.9)).astype(np.float32)


def _hat(dur: float = 0.05) -> np.ndarray:
    n = int(dur * SR)
    rng = np.random.default_rng(7)
    return (rng.standard_normal(n) * _env(n, 0.001, dur * 0.9)).astype(np.float32)


def _place(track: np.ndarray, sound: np.ndarray, at: float, gain: float = 1.0) -> None:
    start = int(at * SR)
    end = start + len(sound)
    if start >= len(track):
        return
    end = min(end, len(track))
    track[start:end] += sound[:end - start] * gain


# ---------------------------------------------------------------------------
# 各风格 preset
# ---------------------------------------------------------------------------
def _synthwave(total: float) -> np.ndarray:
    track = np.zeros(int(total * SR), dtype=np.float32)
    chords = [["A2", "C3", "E3"], ["F2", "A2", "C3"], ["C3", "E3", "G3"], ["G2", "B2", "D3"]]
    bar = 2.0
    for i in range(int(total / bar) + 1):
        chord = chords[i % len(chords)]
        freqs = [note_freq(x) for x in chord]
        _place(track, _pad(freqs, bar * 1.1, attack=0.3, release=0.9, detune=0.1), i * bar, 0.5)
        for j, f in enumerate(freqs):
            _place(track, _pluck(f * 2, 0.5, harmonics=(1.0, 0.4, 0.2), detune=0.02),
                   i * bar + 0.25 * j, 0.25)
        _place(track, _kick(), i * bar, 0.6)
        _place(track, _hat(), i * bar + 0.5, 0.25)
    return track


def _future_bass(total: float) -> np.ndarray:
    track = np.zeros(int(total * SR), dtype=np.float32)
    bass = ["D2", "D2", "F2", "G2"]
    for i in range(int(total / 1.0) + 1):
        f = note_freq(bass[i % len(bass)])
        _place(track, _pluck(f, 0.9, harmonics=(1.0, 0.6, 0.3, 0.2), decay=0.5), i * 1.0, 0.7)
        _place(track, _pad([f * 2, f * 3], 1.0, attack=0.1, release=0.5, detune=0.05), i * 1.0, 0.25)
        _place(track, _kick(), i * 1.0, 0.5)
        _place(track, _hat(0.08), i * 1.0 + 0.5, 0.2)
    return track


def _guqin(total: float) -> np.ndarray:
    track = np.zeros(int(total * SR), dtype=np.float32)
    scale = ["D3", "E3", "G3", "A3", "C4", "D4", "E4", "G4", "A4"]
    rng = np.random.default_rng(11)
    t = 0.4
    while t < total - 1.0:
        f = note_freq(scale[int(rng.integers(0, len(scale)))])
        dur = float(rng.uniform(1.2, 3.0))
        _place(track, _pluck(f, dur, harmonics=(1.0, 0.35, 0.2, 0.1),
                             decay=0.6, detune=0.004), t, 0.55)
        # 偶尔低音散音
        if rng.random() < 0.3:
            _place(track, _pluck(note_freq("D2"), 3.2, harmonics=(1.0, 0.5),
                                 decay=0.7), t, 0.35)
        t += float(rng.uniform(0.7, 1.8))
    return track


def _pipa(total: float) -> np.ndarray:
    track = np.zeros(int(total * SR), dtype=np.float32)
    scale = ["A3", "C4", "D4", "E4", "G4", "A4", "C5"]
    rng = np.random.default_rng(23)
    t = 0.2
    while t < total - 0.6:
        for _ in range(int(rng.integers(2, 5))):
            f = note_freq(scale[int(rng.integers(0, len(scale)))])
            _place(track, _pluck(f, 0.35, harmonics=(1.0, 0.7, 0.4, 0.2),
                                 decay=0.4, detune=0.01), t, 0.35)
            t += float(rng.choice([0.12, 0.18, 0.24]))
        t += 0.3
    return track


def _bells(total: float) -> np.ndarray:
    track = np.zeros(int(total * SR), dtype=np.float32)
    scale = ["C4", "E4", "G4", "B4", "D5"]
    rng = np.random.default_rng(31)
    t = 0.0
    while t < total - 2.0:
        f = note_freq(scale[int(rng.integers(0, len(scale)))])
        bell = _pluck(f, 3.0, harmonics=(1.0, 0.5, 0.3, 0.2, 0.15), decay=0.9,
                      detune=0.003)
        _place(track, bell, t, 0.4)
        t += float(rng.uniform(2.0, 4.0))
    _place(track, _pad([note_freq("C3"), note_freq("G3")], total,
                       attack=2.0, release=3.0, detune=0.05), 0.0, 0.25)
    return track


def _mystery_strings(total: float) -> np.ndarray:
    track = np.zeros(int(total * SR), dtype=np.float32)
    freqs = [note_freq("A2"), note_freq("D3"), note_freq("F3")]
    n = int(total * SR)
    t = np.arange(n, dtype=np.float32) / SR
    trem = 0.55 + 0.45 * np.sin(2 * math.pi * 5.0 * t)
    out = np.zeros(n, dtype=np.float32)
    for f in freqs:
        for d in (1.0, 1.003, 0.997):
            out += np.sin(2 * math.pi * f * d * t) + 0.3 * np.sin(2 * math.pi * f * 2 * d * t)
    track += (out / max(1e-6, float(np.max(np.abs(out))))) * trem * 0.5
    # 低音心跳
    for i in range(int(total / 1.2) + 1):
        _place(track, _kick(0.25, 70, 40), i * 1.2, 0.3)
    return track


def _noir_jazz(total: float) -> np.ndarray:
    track = np.zeros(int(total * SR), dtype=np.float32)
    chords = [["A2", "C3", "E3", "G3"], ["D3", "F3", "A3", "C4"],
              ["G2", "B2", "D3", "F3"], ["C3", "E3", "G3", "B3"]]
    bar = 3.0
    for i in range(int(total / bar) + 1):
        freqs = [note_freq(x) for x in chords[i % len(chords)]]
        for f in freqs:
            _place(track, _pluck(f, 1.6, harmonics=(1.0, 0.3, 0.12), decay=0.7), i * bar, 0.3)
        # 走低音
        _place(track, _pluck(freqs[0] / 2, 1.2, harmonics=(1.0, 0.4), decay=0.6),
               i * bar + 1.2, 0.4)
    return track


def _jazz_piano(total: float) -> np.ndarray:
    track = np.zeros(int(total * SR), dtype=np.float32)
    chords = [["C3", "E3", "G3", "B3"], ["A2", "C3", "E3", "G3"],
              ["D3", "F3", "A3", "C4"], ["G2", "B2", "D3", "F3"]]
    bar = 2.4
    for i in range(int(total / bar) + 1):
        freqs = [note_freq(x) for x in chords[i % len(chords)]]
        for j, f in enumerate(freqs):
            _place(track, _pluck(f, 1.0, harmonics=(1.0, 0.45, 0.25, 0.12),
                                 decay=0.6), i * bar + 0.04 * j, 0.25)
        _place(track, _pluck(freqs[0] / 2, 1.4, harmonics=(1.0, 0.4), decay=0.7),
               i * bar + 0.5, 0.35)
    return track


def _cello(total: float) -> np.ndarray:
    track = np.zeros(int(total * SR), dtype=np.float32)
    notes = ["C2", "G2", "A2", "E2"]
    seg = 4.0
    for i in range(int(total / seg) + 1):
        f = note_freq(notes[i % len(notes)])
        _place(track, _pad([f, f * 2], seg * 1.05, attack=0.4, release=1.0,
                           detune=0.004), i * seg, 0.45)
    return track


def _organ(total: float) -> np.ndarray:
    track = np.zeros(int(total * SR), dtype=np.float32)
    chords = [["D2", "A2", "D3", "F3"], ["C2", "G2", "C3", "E3"],
              ["A1", "E2", "A2", "C3"], ["D2", "A2", "F3"]]
    seg = 4.0
    for i in range(int(total / seg) + 1):
        freqs = [note_freq(x) for x in chords[i % len(chords)]]
        _place(track, _pad(freqs, seg * 1.02, attack=0.2, release=0.6, detune=0.0,
                           harmonics=(1.0, 0.5, 0.3, 0.2, 0.1)), i * seg, 0.4)
    return track


def _electronic(total: float) -> np.ndarray:
    track = np.zeros(int(total * SR), dtype=np.float32)
    seq = ["C3", "E3", "G3", "B3", "C4", "G3", "E3", "B3"]
    step = 0.25
    for i in range(int(total / step) + 1):
        f = note_freq(seq[i % len(seq)])
        _place(track, _pluck(f, 0.2, harmonics=(1.0, 0.5, 0.3), decay=0.4), i * step, 0.18)
        if i % 8 == 0:
            _place(track, _kick(), i * step, 0.4)
        if i % 4 == 2:
            _place(track, _hat(0.04), i * step, 0.15)
    return track


def _vhs_funk(total: float) -> np.ndarray:
    track = np.zeros(int(total * SR), dtype=np.float32)
    bass = ["E2", "E2", "G2", "A2"]
    for i in range(int(total / 0.5) + 1):
        if i % 2 == 0:
            f = note_freq(bass[(i // 2) % len(bass)])
            _place(track, _pluck(f, 0.4, harmonics=(1.0, 0.6, 0.4), decay=0.4),
                   i * 0.5, 0.4)
        else:
            _place(track, _hat(0.04), i * 0.5, 0.2)
        if i % 4 == 0:
            _place(track, _kick(0.15, 100, 50), i * 0.5, 0.45)
    return track


def _cartoon(total: float) -> np.ndarray:
    track = np.zeros(int(total * SR), dtype=np.float32)
    scale = ["C4", "E4", "G4", "C5", "G4", "E4"]
    step = 0.3
    for i in range(int(total / step) + 1):
        if i % 3 == 0:
            f = note_freq(scale[(i // 3) % len(scale)])
            _place(track, _pluck(f, 0.25, harmonics=(1.0, 0.6, 0.3), decay=0.3),
                   i * step, 0.25)
        if i % 6 == 0:
            _place(track, _kick(0.1, 200, 80), i * step, 0.3)
    return track


def _ukulele(total: float) -> np.ndarray:
    track = np.zeros(int(total * SR), dtype=np.float32)
    chords = [["C3", "E3", "G3", "C4"], ["A2", "C3", "E3", "A3"],
              ["F2", "A2", "C3", "F3"], ["G2", "B2", "D3", "G3"]]
    bar = 2.0
    for i in range(int(total / bar) + 1):
        freqs = [note_freq(x) for x in chords[i % len(chords)]]
        for j, f in enumerate(freqs):
            _place(track, _pluck(f, 0.6, harmonics=(1.0, 0.4, 0.2), decay=0.5),
                   i * bar + 0.02 * j, 0.22)
        _place(track, _pluck(freqs[0] / 2, 0.8, harmonics=(1.0, 0.5), decay=0.5),
               i * bar + 0.1, 0.3)
    return track


def _live_drums(total: float) -> np.ndarray:
    track = np.zeros(int(total * SR), dtype=np.float32)
    step = 0.2
    for i in range(int(total / step) + 1):
        if i % 8 in (0, 3):
            _place(track, _kick(), i * step, 0.5)
        if i % 8 in (4, 7):
            _place(track, _hat(0.06), i * step, 0.3)
        if i % 16 in (2, 6, 10, 14):
            _place(track, _pluck(note_freq("A3"), 0.12, harmonics=(1.0, 0.6, 0.3),
                                 decay=0.3), i * step, 0.2)
    return track


PRESETS = {
    "synthwave": _synthwave, "future_bass": _future_bass, "guqin": _guqin,
    "pipa": _pipa, "bells": _bells, "mystery_strings": _mystery_strings,
    "noir_jazz": _noir_jazz, "jazz_piano": _jazz_piano, "cello": _cello,
    "organ": _organ, "electronic": _electronic, "vhs_funk": _vhs_funk,
    "cartoon": _cartoon, "ukulele": _ukulele, "live_drums": _live_drums,
}

# 风格 JSON 里使用的 preset 别名（不同作者命名不同）→ 实际合成器
ALIASES = {
    "pipe_organ": "organ", "organ_drone": "organ",
    "electronic_beat": "electronic", "electronic_pulse": "electronic",
    "cartoon_bounce": "cartoon", "cartoon_toy": "cartoon",
    "live_drum": "live_drums", "drum_live": "live_drums",
    "ukulele_soft": "ukulele", "ukulele_warm": "ukulele",
    "synth_guqin": "guqin", "guqin_ambient": "guqin",
    "guqin_pluck": "guqin", "ink_ambient": "guqin",
    "cold_jazz": "noir_jazz", "noir": "noir_jazz", "noir_sax": "noir_jazz",
    "cello_salon": "cello", "cello_drone": "cello",
    "pipa_drum": "pipa", "pipa_pluck": "pipa",
    "synth_funk": "vhs_funk", "vhs": "vhs_funk", "funk": "vhs_funk",
    "jazz": "jazz_piano", "piano": "jazz_piano",
    "bells_temple": "bells", "bell": "bells", "museum_bells": "bells",
    "mystery": "mystery_strings", "strings": "mystery_strings",
    "synth": "synthwave", "synthwave_80s": "synthwave",
}


def _resolve_preset(preset: str) -> str:
    name = str(preset or "").strip().lower()
    if not name:
        return "synthwave"
    if name in PRESETS:
        return name
    if name in ALIASES:
        return ALIASES[name]
    for key, target in ALIASES.items():
        if key in name:
            return target
    for key in PRESETS:
        if key in name:
            return key
    return "synthwave"


def ensure_bgm(style: dict, workdir: Path, *, seconds: float = DEFAULT_SECONDS,
               force: bool = False) -> Path:
    """按风格生成/复用 BGM（workdir/bgm/v5_bgm.wav，48k 立体声）。"""
    style = style or {}
    audio = style.get("audio") if isinstance(style.get("audio"), dict) else {}
    preset = str(audio.get("bgm") or style.get("bgm") or "pad").strip()
    resolved = _resolve_preset(preset)
    bgm_dir = utils.ensure_dir(Path(workdir) / "bgm")
    out = bgm_dir / "v5_bgm.wav"
    meta_path = bgm_dir / "v5_bgm.meta.json"
    digest = utils.sha1_text("v5-bgm", style.get("id"), preset, seconds)
    meta = utils.read_json(meta_path) or {}
    if not force and out.is_file() and meta.get("hash") == digest:
        return out
    fn = PRESETS.get(resolved)
    if fn is None:
        LOG.info("V5 BGM preset 未知（%s），用 synthwave 兜底", preset)
        fn = _synthwave
    mono = fn(seconds).astype(np.float32)
    if len(mono) < int(seconds * SR):
        mono = np.pad(mono, (0, int(seconds * SR) - len(mono)))
    mono = mono[: int(seconds * SR)]
    peak = float(np.max(np.abs(mono))) or 1.0
    mono = (mono / peak) * 0.6
    # 轻微立体声宽度（哈斯）
    d = int(0.012 * SR)
    left = mono
    right = np.concatenate([np.zeros(d, dtype=np.float32), mono[:-d]])
    stereo = np.stack([left, right], axis=1)
    stereo = np.clip(stereo * 32767, -32768, 32767).astype(np.int16)
    with wave.open(str(out), "wb") as fh:
        fh.setnchannels(2)
        fh.setsampwidth(2)
        fh.setframerate(SR)
        fh.writeframes(stereo.tobytes())
    utils.write_json(meta_path, {"hash": digest, "preset": preset,
                                 "seconds": seconds, "style": style.get("id")})
    return out
