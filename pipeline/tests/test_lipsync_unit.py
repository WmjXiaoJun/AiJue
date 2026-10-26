# -*- coding: utf-8 -*-
"""pipeline.lipsync 单元测试（不加载模型、不占 GPU）。

重点回归 2026-10-26 的“chain spawn 进程风暴”：
- venv 判定必须显式（sys.executable 路径）；
- 转发链中（LIPSYNC_FORWARD_DEPTH>=1）必须拒绝再次 spawn；
- 正常转发时子进程 env 必须带 LIPSYNC_INPROC=1 / depth+1。
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from pipeline import lipsync as L

REPO = L.REPO
CLIP = REPO / "outputs" / "templates_v3" / "tonight_show" / "work" / "h3" / "v3_s001.mp4"
AUDIO = REPO / "outputs" / "templates_v3" / "tonight_show" / "work" / "audio" / "v3_s001.wav"


def test_available_contract():
    a = L.available()
    assert {"ok", "providers", "deps", "files", "unet_bytes", "unet_expected_bytes",
            "torch_in_env", "venv_python"} <= set(a)
    # V5_INTERFACES §5 契约：扁平布尔
    assert {"musetalk", "wav2lip", "syncnet"} <= set(a)
    assert isinstance(a["musetalk"], bool) and a["wav2lip"] is False
    assert "musetalk" in a["providers"]
    assert a["unet_expected_bytes"] == 3_400_074_924


def test_measure_sync_returns_contract_dict():
    res = L.measure_sync("fake.mp4", "fake.wav")
    assert isinstance(res, dict)
    assert "ok" in res
    if not res["ok"]:
        assert "reason" in res


def test_sync_clip_result_contract_keys(monkeypatch):
    """sync_clip 返回值必须含 V5_INTERFACES §5 契约字段（用假后端跑通结果组装）。"""
    np = pytest.importorskip("numpy")
    monkeypatch.setattr(L, "_in_lipsync_venv", lambda: True)
    monkeypatch.setattr(L, "_missing_deps", lambda: [])
    monkeypatch.setattr(L, "probe", lambda *_a, **_k: {"width": 64, "height": 64, "fps": 24.0,
                                                       "duration": 1.0, "n_frames": 24})
    monkeypatch.setattr(L, "_cut_audio", lambda *_a, **_k: Path("fake_audio.wav"))
    monkeypatch.setattr(L, "_ffmpeg_frames_in",
                        lambda *_a, **_k: ([np.zeros((64, 64, 3), np.uint8)] * 6, 24.0))
    monkeypatch.setattr(L, "_prepare_frames",
                        lambda frames, *a, **k: ([(8, 8, 40, 40)] * len(frames),
                                                 {"face_found_frames": len(frames),
                                                  "frames": len(frames), "face_found": True,
                                                  "valid_frames": len(frames),
                                                  "detect_seconds": 0.1, "detect_ms_per_frame": 1.0},
                                                 [True] * len(frames)))
    monkeypatch.setattr(L, "_musetalk_infer",
                        lambda frames, coords, *a, **k: {"frames": len(frames),
                                                         "out_frames": list(frames),
                                                         "crops": coords,
                                                         "timing": {"gpu": {"peak_alloc_mb": 123.4},
                                                                    "per_frame_ms": 5.0,
                                                                    "model_load_s": 1.0,
                                                                    "preprocess_vae_s": 0.1,
                                                                    "unet_decode_s": 0.1,
                                                                    "paste_s": 0.01}})
    monkeypatch.setattr(L, "_write_video", lambda *_a, **_k: {"encode_seconds": 0.1})
    res = L.sync_clip("fake.mp4", "fake.wav", "fake_out.mp4")
    for key in ("ok", "provider", "frames", "elapsed_sec", "vram_peak_mb",
                "face_found", "before", "after"):
        assert key in res, f"契约字段缺失: {key}"
    assert res["ok"] is True and res["provider"] == "musetalk"
    assert res["frames"] == 6 and res["vram_peak_mb"] == 123.4


def test_venv_detection_explicit(monkeypatch):
    # 当前解释器即 VENV_PY → 必须判定 in-venv（永不转发）
    monkeypatch.setattr(L, "VENV_PY", Path(sys.executable))
    assert L._in_lipsync_venv() is True
    # 当前解释器 ≠ VENV_PY → 不在 venv
    monkeypatch.setattr(L, "VENV_PY", Path("C:/definitely/not/this/python.exe"))
    assert L._in_lipsync_venv() is False


def test_forward_depth_blocks_spawn(monkeypatch):
    monkeypatch.setenv("LIPSYNC_FORWARD_DEPTH", "1")
    monkeypatch.delenv("LIPSYNC_INPROC", raising=False)
    monkeypatch.setattr(L, "_in_lipsync_venv", lambda: False)
    monkeypatch.setattr(L, "_venv_ready", lambda: False)
    monkeypatch.setattr(L, "VENV_PY", Path(sys.executable))  # 存在，避免走 not exists 分支
    monkeypatch.setattr(L, "probe", lambda *_a, **_k: {"n_frames": 10, "fps": 24.0,
                                                       "width": 64, "height": 64, "duration": 0.4})
    calls = []
    monkeypatch.setattr(L.subprocess, "run", lambda *a, **k: calls.append((a, k)))
    with pytest.raises(RuntimeError, match="转发深度"):
        L.sync_clip("fake.mp4", "fake.wav", "fake_out.mp4")
    assert calls == [], "转发链中不应 spawn 任何子进程"


def test_forward_sets_guard_env(monkeypatch, tmp_path):
    monkeypatch.delenv("LIPSYNC_FORWARD_DEPTH", raising=False)
    monkeypatch.delenv("LIPSYNC_INPROC", raising=False)
    monkeypatch.setattr(L, "_in_lipsync_venv", lambda: False)
    monkeypatch.setattr(L, "_venv_ready", lambda: False)
    monkeypatch.setattr(L, "VENV_PY", Path(sys.executable))
    monkeypatch.setattr(L, "probe", lambda *_a, **_k: {"n_frames": 10, "fps": 24.0,
                                                       "width": 64, "height": 64, "duration": 0.4})
    captured = {}

    def fake_run(cmd, **kw):
        captured["cmd"] = cmd
        captured["env"] = kw.get("env", {})
        payload = {"ok": True, "provider": "musetalk"}
        return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps(payload), stderr="")

    monkeypatch.setattr(L.subprocess, "run", fake_run)
    res = L.sync_clip("fake.mp4", "fake.wav", str(tmp_path / "out.mp4"))
    assert res["ok"] is True
    assert captured["env"]["LIPSYNC_INPROC"] == "1"
    assert captured["env"]["LIPSYNC_FORWARD_DEPTH"] == "1"
    assert str(L.VENV_PY) in captured["cmd"]


def test_in_proc_missing_deps_raises(monkeypatch):
    monkeypatch.setattr(L, "_in_lipsync_venv", lambda: True)
    monkeypatch.setattr(L, "_missing_deps", lambda: ["torch(missing)"])
    monkeypatch.setattr(L, "probe", lambda *_a, **_k: {"n_frames": 10, "fps": 24.0,
                                                       "width": 64, "height": 64, "duration": 0.4})
    with pytest.raises(RuntimeError, match="缺少 MuseTalk 依赖"):
        L.sync_clip("fake.mp4", "fake.wav", "fake_out.mp4")


def test_max_frames_guard(monkeypatch):
    monkeypatch.setattr(L, "probe", lambda *_a, **_k: {"n_frames": 9999, "fps": 25.0,
                                                       "width": 1280, "height": 720, "duration": 400})
    with pytest.raises(ValueError, match="max_frames"):
        L.sync_clip("fake.mp4", "fake.wav", "fake_out.mp4")


def test_bbox_to_crop_rules():
    pytest.importorskip("cv2")
    bbox = (100.0, 100.0, 160.0, 160.0)
    lms = [(120, 120), (140, 120), (130, 140), (122, 150), (138, 150)]
    x1, y1, x2, y2 = L.bbox_to_crop(bbox, lms, extra_margin=10, frame_hw=(576, 1024))
    assert x1 < x2 and y1 < y2
    assert y1 >= 0 and y2 <= 576 and x2 <= 1024
    assert y1 < 140 < y2          # 鼻尖（landmark[2]）必须在 crop 内
    assert y2 >= 165              # 含下巴 + 10px margin
    # 无 landmark 时也要能退化
    x1b, y1b, x2b, y2b = L.bbox_to_crop(bbox, None, frame_hw=(576, 1024))
    assert y1b < y2b and x1b < x2b


def test_ellipse_mask_shape():
    cv2 = pytest.importorskip("cv2")
    m = L.ellipse_mask(64, 80)
    assert m.shape == (80, 64)
    assert 0.0 <= float(m.min()) and float(m.max()) <= 1.0
    # 嘴部（cy=0.72 附近）必须被覆盖，顶部（眼睛上方）必须接近 0
    assert m[int(80 * 0.72), 32] > 0.8
    assert m[2, 32] < 0.05


def test_paste_face_changes_mouth_area_only():
    cv2 = pytest.importorskip("cv2")
    np = pytest.importorskip("numpy")
    frame = np.zeros((120, 120, 3), dtype=np.uint8)
    gen = np.full((256, 256, 3), 200, dtype=np.uint8)
    out = L.paste_face(frame, gen, (20, 20, 80, 100))
    # 嘴部区域（crop y≈0.72）被替换
    assert int(out[20 + int(80 * 0.72), 50].sum()) > 100
    # 远处完全不变
    assert int(out[0, 0].sum()) == 0
