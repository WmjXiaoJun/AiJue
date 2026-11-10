# -*- coding: utf-8 -*-
"""R8 回归：云端 Muse 出镜链路。

用户口径（2026-11-07）：
- 视频**优先云端 Muse**；
- **每个镜头必须按角色生成**（同一张脸），参考图 = 说话角色立绘；
- 长场景/连续镜头要有**首尾帧衔接**；
- 旧成片已归档，图片/语音仍本地。

实测结论（锁定在测试里，防止回退）：
- 2D 关键帧当参考图 → APIMart 内容安全拒；角色立绘 `portrait_real.png` → 通过。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
from PIL import Image

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from pipeline import muse_shots, variety_subs  # noqa: E402


def test_plan_segments_shape_and_ids():
    segs = muse_shots.plan_segments("tonight_show", segments=6)
    # R8：段数不再"等于请求值"——按说话人切段后只会**更多**（段内不换人优先于段数）
    assert len(segs) >= 6, len(segs)
    assert len({s["id"] for s in segs}) == len(segs)
    for s in segs:
        assert s["duration"] == muse_shots.MUSE_SECONDS == 10
        assert s["source_ids"], s
        assert s["prompt"], s


def test_every_segment_has_character_reference():
    """每个"有角色"的段都必须拿到角色立绘（这是"按角色生成"的前提）。"""
    segs = muse_shots.plan_segments("tonight_show", segments=6)
    refs = [muse_shots.reference_image("tonight_show", s) for s in segs]
    assert any(r is not None for r in refs), "至少要有参考图"
    with_char = [s for s in segs if s.get("roles")]
    assert with_char, "brief 里应该有带角色的段"
    for seg in with_char:
        ref = muse_shots.reference_image("tonight_show", seg)
        assert ref is not None, seg["id"]
        assert "portrait" in ref.name or "front" in ref.name or "cutout" in ref.name, ref.name


def test_to_16x9_makes_1280x720(tmp_path):
    src = tmp_path / "portrait.png"
    Image.new("RGB", (600, 900), (90, 80, 70)).save(src)
    out = muse_shots._to_16x9(src, tmp_path / "cache")
    with Image.open(out) as im:
        assert im.size == (1280, 720), im.size


def test_wrap_pieces_rejoins_text():
    text = "救场，AI 接不住梗的时候总得有人顶上去。那我们就试试，看今晚谁先接不住。"
    pieces = variety_subs._wrap_pieces(text, per_line=20, max_lines=3)
    assert 1 <= len(pieces) <= 3
    assert "".join(pieces) == text, pieces
    assert all(pieces)


def test_wrap_short_text_untouched():
    assert variety_subs._wrap_pieces("短句。") == ["短句。"]


def test_segments_never_mix_speakers():
    """R8：10s 段内不能换人（否则观众看到"一个人说两个人的台词"）。"""
    segs = muse_shots.plan_segments("classics", segments=30)
    assert segs, "classics 应该有段"
    for seg in segs:
        keys = {muse_shots._speaker_key(s) for s in seg["src_shots"]}
        assert len(keys) == 1, (seg["id"], keys)


def test_classics_segments_resolve_character_portrait():
    """classics 的 55 句台本每句都要落到角色立绘上（不能回退成场景图）。"""
    segs = muse_shots.plan_segments("classics", segments=30)
    with_char = [s for s in segs if str(s.get("line") or "").strip()]
    assert with_char, "应该有带台词的段"
    missing = []
    for seg in with_char:
        ref = muse_shots.reference_image("classics", seg)
        if ref is None or "scenes" in str(ref):
            missing.append(seg["id"])
    assert not missing, missing[:5]
