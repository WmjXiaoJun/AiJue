# -*- coding: utf-8 -*-
"""R5 回归：剧本驱动台词（prefer_script / script_mode: script）。

背景：模板 act 里自带 `text` 时，`_act_lines` 每幕只取 1 句 → 5 分钟节目只有 ~9 句台词，
`expand_3d_shots` 又清空新镜 `line`，于是成片只有 7 条字幕、其余全是空镜。
`prefer_script=True`（或模板 `script_mode: "script"`）让剧本按幕数均分铺开，一句一镜。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from pipeline import v5_catalog, v5_story  # noqa: E402


@pytest.fixture(scope="module")
def catalog():
    return v5_catalog.load_catalog()


def _script(n: int) -> dict:
    speakers = ["老周", "阿栗", "大魏"]
    lines = [{"speaker": speakers[i % 3], "text": f"第{i + 1}句话，用来测试剧本铺开是否一句一镜。"}
             for i in range(n)]
    return {"title": "probe", "segments": [{"name": "全篇", "lines": lines}]}


def test_spread_distributes_evenly():
    acts = [{"id": f"a{i}"} for i in range(4)]
    lines = [{"speaker": "s", "text": f"L{i}"} for i in range(10)]
    buckets = v5_story._spread_lines(acts, lines)
    assert [len(b) for b in buckets] == [2, 2, 3, 3]
    assert sum(len(b) for b in buckets) == 10
    # 顺序不能乱（台词是叙事，不是集合）
    assert [b[0]["text"] for b in buckets] == ["L0", "L2", "L4", "L7"]


def test_prefer_script_gives_one_shot_per_line(catalog):
    tpl = catalog["templates"]["tonight_show"]
    script = _script(52)
    plan = v5_story.build_plan(tpl, 300.0, script=script, prefer_script=True, seed=1)
    lines = [s for s in plan["shots"] if str(s.get("line") or "").strip()]
    assert len(lines) == 52
    ids = [s["id"] for s in plan["shots"]]
    assert len(ids) == len(set(ids))
    # 每句台词都出现在计划里（不丢句）
    texts = [str(s["line"]) for s in lines]
    assert texts == [ln["text"] for ln in _script(52)["segments"][0]["lines"]]


def test_default_keeps_act_text(catalog):
    """模板**没有** `script_mode` 时保持旧行为（act 自带台词优先）。

    注意：`tonight_show` 已在 R6 里被显式设成 `script_mode:"script"`，所以这里先去掉它，
    才是在测"旧默认行为"。
    """
    tpl = {k: v for k, v in catalog["templates"]["tonight_show"].items() if k != "script_mode"}
    plan = v5_story.build_plan(tpl, 40.0, script=_script(52), seed=1)
    lines = [s for s in plan["shots"] if str(s.get("line") or "").strip()]
    assert len(lines) == len(tpl["acts"])
    assert len(lines) < 52


def test_template_script_mode_switches_without_flag(catalog):
    tpl = dict(catalog["templates"]["tonight_show"], script_mode="script")
    plan = v5_story.build_plan(tpl, 300.0, script=_script(20), seed=1)
    lines = [s for s in plan["shots"] if str(s.get("line") or "").strip()]
    assert len(lines) == 20


def test_duration_still_reaches_target_with_script(catalog):
    tpl = catalog["templates"]["tonight_show"]
    plan = v5_story.build_plan(tpl, 300.0, script=_script(52), prefer_script=True, seed=1)
    total = plan["episode"]["total_duration"]
    assert total >= 300.0 * v5_story.PAD_RATIO - 0.01, total
