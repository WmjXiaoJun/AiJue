# -*- coding: utf-8 -*-
"""V49 回归：发布可见性映射（P1-18）。

缺陷背景（docs/audit/REVIEW_ROUND3.md P1-18）：`publish/upload_post.py` 曾把
`privacy_level` 硬编码成 `PUBLIC_TO_EVERYONE`，`--privacy unlisted` 只对 YouTube 生效，
TikTok / Instagram **永远公开** —— 全仓唯一不可撤回的外部副作用。

本测试只测纯函数（不联网、不读 key）：
- 三档映射：public→PUBLIC_TO_EVERYONE / private→SELF_ONLY / unlisted→MUTUAL_FOLLOW_FRIENDS
- `unlisted` + 非 youtube 平台默认**拒发**（PrivacyRejected），除非显式放行
- youtube 走自己的 privacyStatus 字符串
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from publish import upload_post as up  # noqa: E402


def test_public_private_youtube_mapping():
    assert up._privacy_levels(["tiktok", "instagram"], "public") == {
        "tiktok": "PUBLIC_TO_EVERYONE", "instagram": "PUBLIC_TO_EVERYONE"}
    assert up._privacy_levels(["tiktok"], "private") == {"tiktok": "SELF_ONLY"}
    assert up._privacy_levels(["youtube"], "unlisted") == {"youtube": "unlisted"}
    assert up._privacy_levels(["youtube", "tiktok"], "public") == {
        "youtube": "public", "tiktok": "PUBLIC_TO_EVERYONE"}


def test_unlisted_rejects_non_youtube_by_default(monkeypatch):
    monkeypatch.delenv("UPLOAD_POST_ALLOW_PUBLIC_FALLBACK", raising=False)
    for platforms in (["tiktok"], ["instagram"], ["youtube", "instagram"]):
        with pytest.raises(up.PrivacyRejected):
            up._privacy_levels(platforms, "unlisted")
    assert up._privacy_levels(["tiktok"], "unlisted", allow_public_fallback=True) == {
        "tiktok": "MUTUAL_FOLLOW_FRIENDS"}
    monkeypatch.setenv("UPLOAD_POST_ALLOW_PUBLIC_FALLBACK", "1")
    assert up._privacy_levels(["tiktok"], "unlisted") == {"tiktok": "MUTUAL_FOLLOW_FRIENDS"}


def test_upload_video_blocks_without_network(monkeypatch, tmp_path):
    """拒发必须在**任何网络请求之前**返回，且 error 里给出出路。"""
    calls = []

    def _boom(*args, **kwargs):
        calls.append(args)
        raise AssertionError("拒发路径不应该发网络请求")

    monkeypatch.setattr(up, "_post_upload", _boom)
    clip = tmp_path / "clip.mp4"
    clip.write_bytes(b"\x00" * 2048)
    res = up.upload_video(video_path=clip, title="t", platforms=["tiktok"],
                          username="u", api_key="k", privacy="unlisted")
    assert res["success"] is False
    assert "allow_public_fallback" in str(res.get("error"))
    assert calls == []
