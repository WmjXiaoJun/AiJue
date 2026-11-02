# -*- coding: utf-8 -*-
"""V21：custom 模板的 `templates3d_hq` 启动包装（不修改 pipeline 代码）。

`pipeline.templates3d.load_template()` 只读 `contract/v5/templates/<id>.json`（内建模板），
V21 的 custom 模板写在 `contract/v5/custom/templates/<id>.json`。本包装在子进程启动时
把 `templates3d.load_template` 替换为「先内建、后 custom」的兼容实现，再调用原 hq main。

用法（由 backend pipeline job 调用）：
    python backend/app/routers/custom_hq_runner.py --template custom_xxx [--reuse-shots]
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from pipeline import templates3d as t3  # noqa: E402


def _load_template_compat(tid: str) -> dict:
    builtin = REPO / "contract" / "v5" / "templates" / f"{tid}.json"
    custom = REPO / "contract" / "v5" / "custom" / "templates" / f"{tid}.json"
    path = builtin if builtin.is_file() else custom
    if not path.is_file():
        raise SystemExit(f"缺少模板合同: {tid}（{builtin} / {custom}）")
    return json.loads(path.read_text(encoding="utf-8"))


t3.load_template = _load_template_compat

if __name__ == "__main__":
    from pipeline import templates3d_hq as hq  # noqa: E402 - 在 patch 之后再 import

    raise SystemExit(hq.main(sys.argv[1:]))
