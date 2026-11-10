# -*- coding: utf-8 -*-
"""V63：把 3D 分镜 brief 扩写到目标镜数（减少 5 分钟里的"空镜"占比）。

做法：读 contract/v5/cinema3d/templates3d/<tid>_semantic.json 的 shots，
按现有镜头的场景/人物/动作轮换生成新条目（新 id、新运镜词），写回 brief（先备份）。
之后 templates3d_hq 会照常把这些新镜头渲成 H3 视频——是真镜头，不是静帧空镜。

V69 修（AUDIT_extras.md P1-1 / REVIEW_ROUND3 P0-2）：
- 新 id 用 `v5_x{next:03d}`，`next = max(已有 v5_x 编号)+1`；旧实现 `v5_e{added+1}`
  每次从 001 重排，重复跑同一份 brief 必然撞 id（已污染 13/30 合同）；
- 写盘前断言「新 id 唯一 + 与已有 id 不冲突」，冲突直接失败（不写盘）；
- **幂等**：已经达到 target 就不写盘（重复跑不再产生新镜头）；
- `.bak` 每次覆盖写（保留最近一次原文），不再是"只备份第一次"。

用法：
  python scripts/expand_3d_shots.py --template classics --target-shots 55
  python scripts/expand_3d_shots.py --all --target-shots 55 --dry-run
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

BRIEFS = REPO / "contract" / "v5" / "cinema3d" / "templates3d"
NEW_PREFIX = "v5_x"
_ID_RE = re.compile(rf"^{NEW_PREFIX}(\d+)$")

MOVES = [
    "Camera: slow push in, subtle handheld.",
    "Camera: lateral tracking left to right, shallow depth of field.",
    "Camera: slow pull back revealing the room, steady dolly.",
    "Camera: gentle orbit around the subject, 35mm lens.",
    "Camera: low angle tilt up, practical lights flaring.",
    "Camera: over-the-shoulder framing, slow drift forward.",
    "Camera: static wide shot, actors move within frame.",
    "Camera: rack focus from foreground object to the character.",
]
TIMES = ["at dawn", "at dusk", "late at night", "in the rain", "in drifting fog",
         "under warm practical lights", "in cold moonlight", "at golden hour"]
PLACES = ["a narrow side corridor", "a wide hall seen from the far end",
          "a cluttered back room", "a rooftop terrace", "a long table close-up",
          "a doorway looking inward", "a window seat", "a busy street beyond the gate"]
ACTIONS = [
    "The character pauses, looks off frame, then continues.",
    "The character leans on the table and gestures while speaking.",
    "The character walks slowly through the space, glancing around.",
    "The character turns toward the window, thinking.",
    "Two characters exchange a look; one nods.",
    "The character picks up an object and examines it.",
]


def next_id_index(shots: list) -> int:
    """下一个可用序号：max(已有 v5_xNNN)+1；没有则从 1 开始。"""
    nums = [int(m.group(1)) for s in shots
            if (m := _ID_RE.match(str(s.get("id") or "").strip()))]
    return (max(nums) + 1) if nums else 1


def duplicate_ids(ids: list[str]) -> list[str]:
    counts: dict[str, int] = {}
    for item in ids:
        counts[item] = counts.get(item, 0) + 1
    return sorted(k for k, v in counts.items() if v > 1 and k)


def expand_one(tid: str, target: int, dry: bool = False,
               strict_ids: bool = False) -> dict:
    path = BRIEFS / f"{tid}_semantic.json"
    if not path.is_file():
        return {"template": tid, "ok": False, "error": "no semantic brief"}
    data = json.loads(path.read_text(encoding="utf-8"))
    shots = list(data.get("shots") or [])
    base = [s for s in shots if s.get("prompt")]
    if not base:
        return {"template": tid, "ok": False, "error": "no prompt shots"}
    ids_before = [str(s.get("id") or "") for s in shots]
    dup_before = duplicate_ids(ids_before)
    # 幂等：已经达标就直接返回，不写盘、不改 id
    if len(shots) >= int(target):
        return {"template": tid, "ok": True, "skipped": True,
                "reason": f"already {len(shots)} >= target {target}",
                "added": 0, "total": len(shots), "next_id": next_id_index(shots),
                "duplicate_ids_before": dup_before}
    added = 0
    idx = 0
    next_n = next_id_index(shots)
    new_ids: list[str] = []
    while len(shots) < target and idx < 200:
        src = base[idx % len(base)]
        move = MOVES[idx % len(MOVES)]
        action = ACTIONS[idx % len(ACTIONS)]
        clone = dict(src)
        clone["id"] = f"{NEW_PREFIX}{next_n:03d}"
        next_n += 1
        new_ids.append(clone["id"])
        clone["line"] = ""            # 新增镜头按空台词处理（H3 只出画面+环境声）
        try:
            clone["duration"] = round(min(5.0, max(3.0, float(src.get("duration") or 5.0))), 2)
        except (TypeError, ValueError):
            clone["duration"] = 4.5
        prompt = str(src.get("prompt") or "")
        # 去掉原 Camera 段落，换新运镜，追加动作描述
        if "Camera:" in prompt:
            prompt = prompt.split("Camera:", 1)[0].rstrip()
        when = TIMES[idx % len(TIMES)]
        where = PLACES[(idx // 2) % len(PLACES)]
        clone["prompt"] = (f"{prompt} Different setup: {where}, {when}. "
                           f"{action} {move}").strip()
        clone["semantic_visual"] = str(src.get("semantic_visual") or "") + " " + action
        shots.append(clone)
        added += 1
        idx += 1
    # 写盘前硬校验：新 id 自身唯一、且不与已有 id 冲突（冲突就不写，绝不制造重复）
    assert len(set(new_ids)) == len(new_ids), f"{tid}: 新 id 内部重复 {new_ids}"
    clash = sorted(set(new_ids) & set(ids_before))
    assert not clash, f"{tid}: 新 id 与已有 id 冲突 {clash}"
    all_ids = [str(s.get("id") or "") for s in shots]
    dup_after = duplicate_ids(all_ids)
    # 本脚本只允许「不改变历史重复集合」：多出一个重复就说明分配逻辑坏了
    assert dup_after == dup_before, \
        f"{tid}: 写盘前出现新的重复 id {sorted(set(dup_after) - set(dup_before))}"
    result = {"template": tid, "ok": not (strict_ids and bool(dup_before)),
              "added": added, "total": len(shots), "next_id": next_n,
              "ids": new_ids, "duplicate_ids_before": dup_before}
    if strict_ids and dup_before:
        result["error"] = f"brief 里已有重复 shot id（先修数据再扩写）：{dup_before}"
    if dry:
        result["dry_run"] = True
        return result
    backup = path.with_suffix(".json.bak")
    shutil.copy2(path, backup)   # 每次覆盖写：只保留最近一次原文
    data["shots"] = shots
    path.write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return result


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="扩写 3D 分镜 brief")
    ap.add_argument("--template", default="")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--target-shots", type=int, default=55)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--reset", action="store_true", help="先从 .bak 还原再扩写（换一批镜头）")
    ap.add_argument("--strict-ids", action="store_true",
                    help="brief 里已有重复 shot id 时判失败（数据修复前不要扩写）")
    args = ap.parse_args(argv)
    if args.all:
        ids = sorted(p.name.replace("_semantic.json", "") for p in BRIEFS.glob("*_semantic.json"))
    elif args.template:
        ids = [args.template]
    else:
        print("需要 --template 或 --all")
        return 2
    if args.reset:
        for tid in ids:
            path = BRIEFS / f"{tid}_semantic.json"
            bak = path.with_suffix(".json.bak")
            if bak.is_file():
                shutil.copy2(bak, path)
    rows = [expand_one(tid, args.target_shots, dry=args.dry_run,
                       strict_ids=args.strict_ids) for tid in ids]
    print(json.dumps({"target": args.target_shots, "dry_run": bool(args.dry_run),
                      "items": rows}, ensure_ascii=False, indent=1))
    bad = [r for r in rows if not r.get("ok")]
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
