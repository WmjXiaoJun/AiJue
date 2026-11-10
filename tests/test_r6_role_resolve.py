# -*- coding: utf-8 -*-
"""R6 回归：台本说话人用中文名时，要能落到正确的 cast role。

缺陷：`_resolve_role` 只比 role 与 character id，DeepSeek 台本里的「老周/阿栗/大魏」
一律匹配不上 → 52 句全落到 host，整片一个音色（也解释了"人名条/角色换脸"不生效）。
"""
from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from pipeline import v5_catalog, v5_story  # noqa: E402


@pytest.fixture(scope="module")
def cast():
    cat = v5_catalog.load_catalog()
    return v5_story._cast_roles(cat["templates"]["tonight_show"])


@pytest.mark.parametrize("speaker,role", [
    ("老周", "host"), ("阿栗", "guest_a"), ("大魏", "guest_b"),
    ("host", "host"), ("guest_b", "guest_b"), ("laozhou", "host"),
])
def test_resolve_by_name_or_id(cast, speaker, role):
    assert v5_story._resolve_role(speaker, cast) == role


def test_resolve_unknown_and_empty(cast):
    assert v5_story._resolve_role("", cast) is None
    assert v5_story._resolve_role("查无此人", cast) is None


def test_script_driven_plan_spreads_roles(cast):
    """剧本驱动时台词必须分布在多个角色（否则配音/人名条全是同一个人）。"""
    cat = v5_catalog.load_catalog()
    tpl = cat["templates"]["tonight_show"]
    script = {"segments": [{"name": "全篇", "lines": [
        {"speaker": "老周", "text": "第一句话。"},
        {"speaker": "阿栗", "text": "第二句话。"},
        {"speaker": "大魏", "text": "第三句话。"},
    ]}]}
    plan = v5_story.build_plan(tpl, 60.0, script=script, prefer_script=True, seed=1)
    roles = Counter(s.get("role") for s in plan["shots"] if str(s.get("line") or "").strip())
    assert len(roles) == 3, roles
