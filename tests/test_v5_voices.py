# -*- coding: utf-8 -*-
"""V5 固定声音/声音锁单测（不依赖 GPU；预览/lock 用 mock 替换 TTS）。"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from pipeline import v5_catalog, v5_voices  # noqa: E402


def test_voice_library_loads_and_validates():
    lib = v5_voices.load_voice_library()
    assert len(lib["presets"]) >= 24
    cat = v5_catalog.load_catalog()
    res = v5_voices.validate_library(lib, cat)
    assert res["ok"], res["errors"]
    assert res["characters"] >= 24


def test_legacy_voices_match_v4():
    from pipeline import tts_breeze
    lib = v5_voices.load_voice_library()
    cat = v5_catalog.load_catalog()
    for role, preset_id in (("laozhou", "legacy_v4_laozhou"),
                            ("ali", "legacy_v4_ali"),
                            ("dawei", "legacy_v4_dawei")):
        spec = v5_voices.resolve_voice(cat["characters"][role], lib)
        assert spec["voice_id"] == preset_id
        assert spec["instruction"] == tts_breeze.VOICES[role]["instruction"]
        assert spec["seed"] == tts_breeze.VOICES[role]["seed"]


def test_character_voice_refs_exist():
    lib = v5_voices.load_voice_library()
    cat = v5_catalog.load_catalog()
    for cid, ch in (cat.get("characters") or {}).items():
        voice = ch.get("voice") or {}
        if voice.get("voice_id"):
            assert voice["voice_id"] in lib["presets"], f"{cid}: {voice['voice_id']}"


def test_resolve_voice_override_seed_and_instruction():
    lib = v5_voices.load_voice_library()
    ch = {"id": "test", "label": "测试", "voice": {
        "provider": "breeze", "voice_id": "strategist_qinglang",
        "seed": 123456, "instruction": "自定义声音描述"}}
    spec = v5_voices.resolve_voice(ch, lib)
    assert spec["seed"] == 123456
    assert spec["instruction"] == "自定义声音描述"
    assert spec["voice_id"] == "strategist_qinglang"
    assert spec["key"]


def test_voice_key_stable_and_sensitive():
    lib = v5_voices.load_voice_library()
    cat = v5_catalog.load_catalog()
    ch = cat["characters"]["zhuge"]
    a = v5_voices.resolve_voice(ch, lib)
    b = v5_voices.resolve_voice(dict(ch), lib)
    assert a["key"] == b["key"]  # 稳定

    # seed 改变 → key 改变
    ch2 = dict(ch)
    ch2["voice"] = dict(ch["voice"])
    ch2["voice"]["seed"] = 999
    c = v5_voices.resolve_voice(ch2, lib)
    assert c["key"] != a["key"]

    # instruction 改变 → key 改变
    ch3 = dict(ch)
    ch3["voice"] = dict(ch["voice"])
    ch3["voice"]["instruction"] = "完全不同的音色"
    d = v5_voices.resolve_voice(ch3, lib)
    assert d["key"] != a["key"]


def test_unknown_voice_id_raises():
    lib = v5_voices.load_voice_library()
    with pytest.raises(v5_voices.V5VoiceError):
        v5_voices.resolve_voice({"id": "x", "voice": {"voice_id": "not_exist"}}, lib)


def test_inline_legacy_voice_still_supported():
    lib = v5_voices.load_voice_library()
    ch = {"id": "inline", "voice": {"provider": "breeze",
                                    "instruction": "40岁男性，低沉", "seed": 20269001}}
    spec = v5_voices.resolve_voice(ch, lib)
    assert spec["instruction"] == "40岁男性，低沉"
    assert spec["seed"] == 20269001
    assert spec["voice_id"].startswith("inline_")


def test_apply_lock_injects_ref():
    spec = {"ref_audio": None, "ref_text": None, "key": "k"}
    lock = {"wav": str(REPO / "tmp" / "not_exist.wav"), "text": "参考文本"}
    out = v5_voices.apply_lock(spec, lock)
    assert "ref_audio" not in out or not out.get("ref_audio")
    real = REPO / "tmp" / "v5_voice_lock_test.wav"
    real.parent.mkdir(parents=True, exist_ok=True)
    real.write_bytes(b"x" * 5000)
    try:
        out2 = v5_voices.apply_lock(spec, {"wav": str(real), "text": "参考文本"})
        assert out2["ref_audio"] == str(real)
        assert out2["ref_text"] == "参考文本"
    finally:
        real.unlink(missing_ok=True)


def test_set_character_voice_writes_custom(tmp_path, monkeypatch):
    cat = v5_catalog.load_catalog()
    root = tmp_path / "contract_v5"
    path = v5_voices.set_character_voice("zhuge", {"seed": 42,
                                                  "voice_id": "strategist_qinglang"},
                                         catalog=cat, root=root)
    assert path.is_file()
    import json
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["id"] == "zhuge"
    assert data["voice"]["seed"] == 42
