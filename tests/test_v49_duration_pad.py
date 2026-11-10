# -*- coding: utf-8 -*-
"""V49 回归：幕数少的模板要按 target 补齐时长（P1-15）。

缺陷背景：`custom_holo_talk --duration 30` 实测只出 7.8s，QC 却拿 7.8 跟 7.8 自证全绿。
根治点在 `v5_story.build_plan`：现在先拉长空镜（单镜上限 6.5s），再插空镜（上限 4.5s/镜、
最多 40 镜），达标线 = target×0.95；台词镜不参与（`voiced` 会按真实语音重算）。
"""
from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from pipeline import v5_catalog, v5_story  # noqa: E402


@pytest.fixture(scope="module")
def catalog():
    return v5_catalog.load_catalog()


def test_short_template_is_padded_to_target(catalog):
    tpl = catalog["templates"]["custom_holo_talk"]
    plan = v5_story.build_plan(tpl, 30.0, seed=20261101)
    total = round(sum(float(s["duration"]) for s in plan["shots"]), 3)
    pad = plan["meta"]["pad"]
    assert pad["before"] < 10, pad          # 证明这确实是个"幕少 → 很短"的模板
    assert total >= 30.0 * 0.95, (total, pad)
    assert pad["added"] > 0, pad            # 只拉长不够，必须插空镜


def test_pad_marked_and_ids_unique(catalog):
    tpl = catalog["templates"]["custom_holo_talk"]
    shots = v5_story.build_plan(tpl, 40.0)["shots"]
    ids = [str(s["id"]) for s in shots]
    assert not [k for k, v in Counter(ids).items() if v > 1], ids
    pads = [s for s in shots if s.get("pad")]
    assert pads, "补出来的镜头必须带 pad:true，便于事后识别"
    assert all(float(s["duration"]) <= v5_story.PAD_SHOT_MAX + 1e-6 for s in pads)
    assert [s["no"] for s in shots] == list(range(1, len(shots) + 1))


def test_dialogue_shots_untouched(catalog):
    """台词镜的时长不能被补齐逻辑改动（否则会跟 TTS 真实语音打架）。"""
    tpl = catalog["templates"]["real_street"]
    base = v5_story.build_plan(tpl, 20.0, seed=1)["shots"]
    padded = v5_story.build_plan(tpl, 35.0, seed=1)["shots"]
    base_lines = {s["id"]: float(s["duration"]) for s in base if str(s.get("line") or "").strip()}
    padded_lines = {s["id"]: float(s["duration"]) for s in padded if str(s.get("line") or "").strip()}
    assert base_lines, "该模板应有台词镜"
    assert padded_lines == base_lines


def test_no_pad_when_already_long_enough(catalog):
    tpl = catalog["templates"]["tonight_show"]
    plan = v5_story.build_plan(tpl, 40.0)
    assert plan["meta"]["pad"]["added"] == 0
    assert plan["meta"]["pad"]["extended"] == 0
