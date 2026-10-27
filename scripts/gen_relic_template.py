# -*- coding: utf-8 -*-
"""生成 5 分钟「国宝会说话 · 历史文物解说」V5 模板（relic_talk）：
- 风格 museum_gold（国家宝藏金色博物馆）
- cast：host=laozhou、figure_a=simaqian（历史学者）、figure_b=sushi（画中诗人）
- 33 段解说 + 10 段 b-roll，目标 300s
- 带 project 元数据，关联项目「造片局·国宝会说话」
"""
from __future__ import annotations

import json
from pathlib import Path

REPO = Path(".").resolve()
OUT = REPO / "contract" / "v5" / "templates" / "relic_talk.json"

CAST = [
    {"role": "host", "character": "laozhou", "name": "老周"},
    {"role": "figure_a", "character": "simaqian", "name": "太史公"},
    {"role": "figure_b", "character": "sushi", "name": "东坡"},
]

# (scene, camera, strength, characters, speaker, text)
ACTS: list[tuple] = [
    ("museum_hall", "crane_up", 0.10, [], "", ""),
    ("display_case", "push_in", 0.10, ["host"], "host",
     "各位好，欢迎来到《国宝会说话》，我是老周。"),
    ("museum_hall", "pan_right", 0.08, ["host", "figure_a"], "figure_a",
     "今天不讲帝王将相，只讲三件文物，和它们背后的人间烟火。"),
    ("display_case", "tilt_down", 0.08, ["host", "figure_a"], "host",
     "它们沉默千年，却在细节里，藏着最真实的中国。"),
    ("museum_hall", "dolly", 0.10, [], "", ""),
    ("display_case", "push_in", 0.10, ["host", "figure_a"], "host",
     "第一件国宝，已经等了我们两千五百年。"),
    ("excavation_site", "pan_left", 0.08, ["figure_a"], "figure_a",
     "一九七六年，湖北江陵，一座楚墓被打开。"),
    ("display_case", "rack_focus", 0.08, ["figure_a"], "figure_a",
     "墓中出土的这把剑，主人不是楚王，而是越王勾践。"),
    ("display_case", "push_in", 0.10, ["host"], "host",
     "剑身满布菱形暗纹，出土时寒光四射，几乎没有锈蚀。"),
    ("archive_vault", "pan_right", 0.08, ["figure_a"], "figure_a",
     "更惊人的是剑格上的八个鸟篆铭文：越王勾践，自作用剑。"),
    ("war_council", "crane_down", 0.10, ["host"], "host",
     "卧薪尝胆的君王，把屈辱和野心，都锻进了这把剑里。"),
    ("archive_vault", "dolly", 0.08, ["figure_a"], "figure_a",
     "青铜配比、硫化处理、同心圆剑首，每一项都是顶尖工艺。"),
    ("display_case", "push_in", 0.10, ["host"], "host",
     "它像一句没说出口的话：忍耐，是为了有一天出鞘。"),
    ("display_case", "static", 0.06, ["figure_a"], "figure_a",
     "今天它静静躺在展柜里，锋芒仍在。"),
    ("palace_hall", "pan_left", 0.08, [], "", ""),
    ("scroll_split", "push_in", 0.10, ["host"], "host",
     "第二件国宝，是一幅画，也是一场十八岁少年的冒险。"),
    ("scholar_study", "tilt_up", 0.08, ["figure_a"], "figure_a",
     "北宋政和三年，王希孟用半年时间，画出了《千里江山图》。"),
    ("great_wall", "truck_left", 0.10, ["host"], "host",
     "十一米长卷，青绿设色，山峦叠翠，江河浩荡。"),
    ("river_fleet", "pan_right", 0.08, ["figure_a"], "figure_a",
     "石青石绿来自矿物，千年不褪，至今仍亮得惊人。"),
    ("scroll_split", "push_in", 0.10, ["figure_b"], "figure_b",
     "一个十八岁的年轻人，把一生只够画一次的山河，留给了我们。"),
    ("candle_gallery", "crane_up", 0.08, ["figure_a"], "figure_a",
     "画完不久，他便从历史中消失，只留下这卷江山。"),
    ("great_wall", "dolly_zoom", 0.09, ["host"], "host",
     "有人说，天才的代价，是把生命燃成一幅画。"),
    ("river_fleet", "static", 0.06, ["figure_b"], "figure_b",
     "也有人说，他把自己，画进了那片青山。"),
    ("palace_hall", "pan_right", 0.08, [], "", ""),
    ("temple_court", "crane_down", 0.10, ["host"], "host",
     "第三件，是一整套乐器：曾侯乙编钟。"),
    ("display_case", "dolly", 0.08, ["figure_a"], "figure_a",
     "六十五件青铜钟，总重两千五百多公斤，分三层悬挂。"),
    ("temple_court", "tilt_up", 0.08, ["host"], "host",
     "一钟双音，敲击正鼓和侧鼓，能发出两个不同的音。"),
    ("temple_court", "push_in", 0.10, ["figure_a"], "figure_a",
     "十二律俱全，音域跨五个半八度，今天仍能演奏。"),
    ("candle_gallery", "pan_left", 0.08, ["host"], "host",
     "两千四百年前的乐师，已经能演奏出完整的交响。"),
    ("archive_vault", "rack_focus", 0.08, ["figure_a"], "figure_a",
     "钟体上的铭文，记录着曾楚两国的礼乐往来。"),
    ("temple_court", "orbit", 0.08, ["host"], "host",
     "当钟声响起，我们听见的不只是音乐，是一个时代的秩序。"),
    ("temple_court", "static", 0.06, ["figure_a"], "figure_a",
     "礼乐文明，就在这金声玉振之间。"),
    ("museum_hall", "dolly", 0.10, [], "", ""),
    ("museum_hall", "push_in", 0.10, ["host", "figure_a"], "host",
     "三件文物，一把剑、一幅画、一套钟。"),
    ("museum_hall", "pan_right", 0.08, ["host", "figure_a"], "figure_a",
     "它们分别写着：忍耐、才华与秩序。"),
    ("display_case", "push_in", 0.10, ["host"], "host",
     "剑身上的菱形暗纹，是反复锻打留下的肌理。"),
    ("archive_vault", "pan_right", 0.08, ["figure_a"], "figure_a",
     "这不仅是兵器，更是一份越国的国家档案。"),
    ("great_wall", "truck_right", 0.10, ["host"], "host",
     "画中的每一棵树，都有不同的姿态。"),
    ("scroll_split", "pan_left", 0.08, ["figure_b"], "figure_b",
     "青绿之间，藏着北宋人对山水的全部想象。"),
    ("candle_gallery", "push_in", 0.10, ["figure_a"], "figure_a",
     "十八岁，半年，十一米，一生一次。"),
    ("temple_court", "crane_up", 0.10, ["host"], "host",
     "编钟出土时，钟架仍然屹立，仿佛刚刚演奏完。"),
    ("archive_vault", "rack_focus", 0.08, ["figure_a"], "figure_a",
     "六十五件钟，没有一件是多余的。"),
    ("temple_court", "push_in", 0.10, ["host"], "host",
     "先民把音乐，铸进了青铜里。"),
    ("museum_hall", "pan_right", 0.08, ["host", "figure_a"], "figure_a",
     "它们见过盛世，也见过离乱。"),
    ("museum_hall", "dolly_zoom", 0.10, ["host"], "host",
     "今天，它们只看着我们。"),
    ("display_case", "rack_focus", 0.08, ["host"], "host",
     "文物不会说话，但它们从不说谎。"),
    ("display_case", "dolly", 0.08, ["figure_a"], "figure_a",
     "你看那剑锋上的一道细痕，也许是某次出征留下的。"),
    ("archive_vault", "pan_left", 0.08, ["host"], "host",
     "青铜会生锈，纸张会发黄，但它们记录的心气不会。"),
    ("great_wall", "crane_up", 0.10, ["figure_b"], "figure_b",
     "江山代代有人画，可那抹青绿，只属于那个少年。"),
    ("temple_court", "pan_right", 0.08, ["host"], "host",
     "钟声一响，两千四百年就像昨天。"),
    ("museum_hall", "push_in", 0.10, ["host", "figure_a"], "figure_a",
     "所以我们看文物，其实是在看自己从哪里来。"),
    ("museum_hall", "static", 0.06, ["host"], "host",
     "这，就是国宝会说话的意义。"),
    ("museum_hall", "crane_up", 0.10, ["host", "figure_a"], "figure_a",
     "下次走进博物馆，请多停留三秒钟。"),
    ("museum_hall", "static", 0.06, ["host"], "host",
     "因为那三秒，可能就是一个千年的对话。"),
]


def main() -> int:
    acts = []
    for i, (scene, camera, strength, chars, speaker, text) in enumerate(ACTS, 1):
        broll = not speaker and not text
        acts.append({
            "id": f"act{i:02d}",
            "name": f"第 {i} 幕",
            "scene": scene,
            "camera": camera,
            "camera_strength": strength,
            "duration": 8.0 if broll else 6.5,
            "duration_hint": 8.0 if broll else 6.5,
            "line": speaker,
            "text": text,
            "characters": chars,
            "broll": broll,
        })
    template = {
        "id": "relic_talk",
        "label": "国宝会说话",
        "version": 5,
        "description": "V5 模板：历史文物解说（5 分钟长片 demo，风格 museum_gold，三件国宝：越王勾践剑/千里江山图/曾侯乙编钟）",
        "tags": ["v5", "museum_gold", "relic", "longform", "demo"],
        "style": "museum_gold",
        "target_duration": 300,
        "aspect": [16, 9],
        "format_id": "relic_talk_5min",
        "project": {
            "id": "relic_5min",
            "project_id": "proj_59fc69016c",
            "name": "造片局 · 国宝会说话",
            "description": "5 分钟历史文物解说 demo，关联节目模板 relic_talk",
        },
        "cast": CAST,
        "acts": acts,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(template, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"ok": True, "path": str(OUT), "acts": len(acts),
                      "lines": sum(1 for a in acts if a["line"])},
                     ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
