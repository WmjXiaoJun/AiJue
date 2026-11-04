# -*- coding: utf-8 -*-
"""V41：custom 模板的 V5 2D 渲染启动包装（不修改 pipeline 代码）。

`pipeline.render_v5` 通过 `pipeline.v5_catalog.load_catalog()` 读模板；custom 模板通常已在
`contract/v5/custom/templates/` 且会合并进 catalog。本包装额外在子进程启动时做一次兼容：
若 catalog 因缓存/旧版本未包含某 custom 模板，则从 contract 目录补入，再调用 render_v5。

用法（由 backend pipeline job 调用）：
    python backend/app/routers/custom_2d_runner.py \
      --template custom_xxx \
      --out outputs/templates_2d/custom_xxx/final.mp4 \
      --workdir outputs/templates_v5/custom_xxx/work
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from pipeline import v5_catalog  # noqa: E402

_ORIG_LOAD_CATALOG = v5_catalog.load_catalog


def _custom_template_path(tid: str) -> Path:
    return REPO / "contract" / "v5" / "custom" / "templates" / f"{tid}.json"


def _load_catalog_compat(root=None):
    """先走原加载；custom 模板缺失时补入（兼容新建/旧缓存场景）。"""
    catalog = _ORIG_LOAD_CATALOG(root)
    templates = catalog.setdefault("templates", {})
    custom_dir = REPO / "contract" / "v5" / "custom" / "templates"
    if custom_dir.is_dir():
        for path in sorted(custom_dir.glob("*.json")):
            tid = path.stem
            if tid in templates:
                continue
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if isinstance(data, dict) and data.get("id"):
                templates[tid] = data
    return catalog


v5_catalog.load_catalog = _load_catalog_compat

if __name__ == "__main__":
    from pipeline import render_v5  # noqa: E402 - 在 patch 之后再 import

    parser = argparse.ArgumentParser(description="V41 custom 2D runner（V5 render_v5）")
    parser.add_argument("--template", required=True)
    parser.add_argument("--duration", type=float, default=40.0)
    parser.add_argument("--out", default=None)
    parser.add_argument("--workdir", default=None)
    parser.add_argument("--seed", type=int, default=20261101)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--limit", type=int, default=None)
    ns = parser.parse_args()
    raise SystemExit(render_v5.main(ns))
