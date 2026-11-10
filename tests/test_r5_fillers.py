# -*- coding: utf-8 -*-
"""R5 回归：空镜（填充镜）不能再出现"全黑 + 羽化圈"和"空镜 · X"标记。

用户原话：「现在为啥全部都是外面都是黑色的了 羽化那样的一个圈」。
三个成因（都已修）：
1. `variety_subs` / `unique_fill` 把内部术语 `空镜 · <模板名>` 烧进画面；
2. 空镜素材本身是暗场（四周全黑 + 中间一团光），zoompan 放大后变成黑框；
3. 空镜素材混进了人物白底图/特写关键帧。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
from PIL import Image

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from pipeline import unique_fill, variety_subs  # noqa: E402


def test_filler_label_never_leaks_into_subtitles(tmp_path):
    """空镜 cue 不允许出现『空镜』二字（观众不该看到制作术语）。"""
    shots = [
        {"start": 0.0, "end": 3.0, "line": "", "speaker_name": "", "scene": "现场", "filler": True},
        {"start": 3.0, "end": 6.0, "line": "", "speaker_name": "", "scene": "", "filler": True},
        {"start": 6.0, "end": 9.0, "line": "这句是台词。", "speaker_name": "老周", "scene": "", "filler": False},
    ]
    out = tmp_path / "probe.ass"
    variety_subs.build_ass(shots, out, captions=["第 1 幕", "第 2 幕"], title_cues=[])
    text = out.read_text(encoding="utf-8")
    assert "空镜" not in text
    # 台词必须还在
    assert "这句是台词" in text


def test_filler_sources_are_scenes_not_cutouts():
    """空镜素材应优先取本模板的场景图，且不能是角色 cutout/front 这类白底资产。"""
    import json

    tpl = json.loads((REPO / "contract/v5/templates/tonight_show.json").read_text(encoding="utf-8"))
    wanted = unique_fill._wanted_scenes(tpl)
    imgs = unique_fill._images("tonight_show", tpl)
    assert imgs, "应该有可用素材"
    names = [p.name for p in imgs]
    assert not [n for n in names if n.startswith(("cutout", "front"))], names[:5]
    # 第一条应命本模板的场景（tier 0）
    assert unique_fill._tier(imgs[0], wanted) == 0, imgs[0]
    # 素材亮度普遍可用（避免"全黑"素材）
    assert unique_fill._luma(imgs[0]) >= 55


def test_prefer_bright_drops_dark_frames(tmp_path):
    dark = tmp_path / "dark.png"
    bright = tmp_path / "bright.png"
    Image.new("RGB", (320, 180), (6, 6, 8)).save(dark)
    Image.new("RGB", (320, 180), (170, 160, 150)).save(bright)
    keep = unique_fill._prefer_bright([dark, bright], min_luma=55.0)
    assert bright in keep
    assert dark not in keep


def test_defringe_crops_and_brightens(tmp_path):
    """暗角素材经 _defringe 后，四周应明显变亮（黑框被裁掉）。"""
    import numpy as np

    src = tmp_path / "vignette.png"
    arr = np.zeros((180, 320, 3), dtype=np.uint8)
    arr[40:140, 90:230] = 200          # 中间一团光
    Image.fromarray(arr).save(src)
    fixed = unique_fill._defringe(src, tmp_path / "_src", margin=0.25)
    before = np.asarray(Image.open(src).convert("L"), dtype=float)
    after = np.asarray(Image.open(fixed).convert("L"), dtype=float)
    assert after.mean() > before.mean()
