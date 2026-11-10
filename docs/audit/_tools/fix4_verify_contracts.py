# -*- coding: utf-8 -*-
"""fix-round3 合同校验：33 份工作流仍合法、字段没少、只有 muse.depends 变过。

断言（逐字段）：
  1. 33 份 JSON 可解析，stack ∈ STACKS；
  2. stages 的 id / agent / tool / depends / enabled / optional / params / desc 与
     workflow.DEFAULT_STAGES（v5 栈）或 SHORTS_STAGES（hq 栈）**逐字段一致**
     —— 这等价于「除了我们想改的 muse.depends，别的一个字段都没动」；
  3. muse.depends == ["shots"]，musev.depends == []。

用法：python docs/audit/_tools/fix4_verify_contracts.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO))

from pipeline import v5_catalog                      # noqa: E402
from pipeline.agents.workflow import (SHORTS_STAGES, STACKS, DEFAULT_STAGES,  # noqa: E402
                                      is_shorts)

WF_DIR = REPO / "contract" / "v6" / "workflows"
FIELDS = ("id", "agent", "tool", "depends", "enabled", "optional", "params", "desc")


def main() -> int:
    cat = v5_catalog.load_catalog()
    templates = cat.get("templates") or {}
    files = sorted(WF_DIR.glob("*.json"))
    problems: list[str] = []
    if len(files) != 33:
        problems.append(f"文件数 {len(files)} != 33")
    for path in files:
        tid = path.stem
        try:
            wf = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:                       # noqa: BLE001
            problems.append(f"{tid}: JSON 解析失败 {exc}")
            continue
        if wf.get("stack") not in STACKS:
            problems.append(f"{tid}: stack={wf.get('stack')!r} 不在 {STACKS}")
        tpl = templates.get(tid) or {}
        expect = SHORTS_STAGES if is_shorts(tid, tpl) else DEFAULT_STAGES
        got = wf.get("stages") or []
        if [s.get("id") for s in got] != [s["id"] for s in expect]:
            problems.append(f"{tid}: stage 序列不一致 {[s.get('id') for s in got]}")
            continue
        for g, e in zip(got, expect):
            for key in FIELDS:
                if g.get(key) != e.get(key):
                    problems.append(f"{tid}.{g['id']}.{key}: {g.get(key)!r} != 代码常量 {e.get(key)!r}")
        muse = [s for s in got if s.get("id") == "muse"]
        musev = [s for s in got if s.get("id") == "musev"]
        if len(muse) != 1 or muse[0].get("depends") != ["shots"]:
            problems.append(f"{tid}: muse.depends != ['shots'] ({muse and muse[0].get('depends')})")
        if len(musev) != 1 or musev[0].get("depends") != []:
            problems.append(f"{tid}: musev.depends 被改动 ({musev and musev[0].get('depends')})")
    print(json.dumps({"files": len(files), "problems": problems,
                       "ok": not problems}, ensure_ascii=False, indent=2))
    return 0 if not problems else 1


if __name__ == "__main__":
    raise SystemExit(main())
