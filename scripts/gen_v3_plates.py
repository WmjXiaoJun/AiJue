# -*- coding: utf-8 -*-
"""v3 场景 plate 生成/重生成（SDXL / ComfyUI，不用 Blender）。

复用 scripts/gen_style_plates.py 的生成管线（同模型/参数/裁剪），只替换 JOBS/SCENE_DESC：
  pitch_room__cinematic     shark_tank（投资路演会议室，cinematic_3d）
  castle_hall__roundtable   traitors（圆桌密谈城堡厅，roundtable_3d）
  crime_scene__ink          history_mystery（水墨公堂悬案，history_ink_2d）
  archive_room__ink         classics（水墨藏书阁，history_ink_2d）
  debate_hall__debateink    qipashuo（国风辩论台，全厅构图，debate_ink_2d，重生成）

用法:
  python scripts/gen_v3_plates.py                # 生成/补齐 4 张新 plate
  python scripts/gen_v3_plates.py --regen        # 连 debate_hall 一起重生成
  python scripts/gen_v3_plates.py --only archive_room__ink
"""
from __future__ import annotations

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, HERE)

import gen_style_plates as g  # noqa: E402

NEW_SCENES = {
    "pitch_room": (
        "a modern corporate boardroom with a long polished wooden table, leather office chairs, "
        "floor-to-ceiling windows with a night city skyline, a presentation screen on the wall, "
        "water glasses and documents on the table, empty room, no people"),
    "castle_hall": (
        "a medieval stone castle hall with a large round wooden table, tall candles in iron "
        "candelabras, high arched windows with cold moonlight, hanging banners, deep shadows, "
        "empty hall, no people"),
    "crime_scene": (
        "a Chinese ink-wash painting of an ancient magistrate's court crime scene: a dark wooden "
        "court hall, a judge's desk with a gavel and seals, an evidence board with torn paper, "
        "cold moonlight through lattice windows, faint red lanterns, empty hall, no people"),
    "archive_room": (
        "a Chinese ink-wash painting of an ancient archive library interior: towering floor-to-ceiling "
        "wooden shelves densely packed with rolled scrolls and bamboo slips on both sides, a low "
        "writing desk in the center foreground with an open book, brush and ink stone, a hanging "
        "paper lantern above, dust motes in a shaft of light, rich brush-stroke detail, empty room, "
        "no people"),
    "debate_hall": (
        "a Chinese ink-wash painting of a formal debate hall, wide establishing view: two red "
        "lacquer wooden podiums facing each other on a raised dark stage, a moderator's low desk "
        "between them, tall wooden columns and hanging banners behind, dramatic brush-stroke "
        "shadows, polished stage floor, warm lantern light, empty hall, no people"),
}

NEW_JOBS = [
    ("pitch_room", "cinematic_3d"),
    ("castle_hall", "roundtable_3d"),
    ("crime_scene", "history_ink_2d"),
    ("archive_room", "history_ink_2d"),
    ("debate_hall", "debate_ink_2d"),
]


def main() -> int:
    argv = sys.argv[1:]
    g.SCENE_DESC.update(NEW_SCENES)
    g.JOBS = list(NEW_JOBS)
    if "--regen" in argv:
        argv = [a for a in argv if a != "--regen"]
        # 重生成：不跳过已存在文件
    else:
        argv = argv + ["--skip-existing"]
    if not argv:
        argv = []
    return g.main(argv)


if __name__ == "__main__":
    raise SystemExit(main())
