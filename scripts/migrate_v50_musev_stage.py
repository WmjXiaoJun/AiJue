# -*- coding: utf-8 -*-
"""V50：给已有工作流合同补 `musev` 阶段（Muse 视频生成，默认关闭）。"""
from __future__ import annotations

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from pipeline.agents.workflow import SHORTS_STAGES, WORKFLOW_DIR  # noqa: E402


def main() -> int:
    stage = next(s for s in SHORTS_STAGES if s["id"] == "musev")
    order = [s["id"] for s in SHORTS_STAGES]
    changed = []
    for path in sorted(WORKFLOW_DIR.glob("*.json")):
        try:
            wf = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        stages = wf.get("stages") or []
        have = {s.get("id"): i for i, s in enumerate(stages)}
        if "musev" in have:
            # 已有 musev：只修正顺序（必须排在 muse 之后）
            if "muse" in have and have["musev"] < have["muse"]:
                stage_obj = stages.pop(have["musev"])
                target = [i for i, s in enumerate(stages) if s.get("id") == "muse"][0] + 1
                stages.insert(target, stage_obj)
                wf["stages"] = stages
                path.write_text(json.dumps(wf, ensure_ascii=False, indent=2) + "\n",
                                encoding="utf-8")
                changed.append(path.stem + ":reorder")
            continue
        # 顺序跟 SHORTS_STAGES 一致：muse → musev → qc
        idx = next((i for i, s in enumerate(stages) if s.get("id") == "muse"), None)
        if idx is not None:
            idx += 1
        else:
            idx = next((i for i, s in enumerate(stages) if s.get("id") == "assemble"), len(stages))
        stages.insert(idx, dict(stage))
        wf["stages"] = stages
        path.write_text(json.dumps(wf, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        changed.append(path.stem)
    print(json.dumps({"changed_count": len(changed), "changed": changed[:6],
                      "order": order}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())