# -*- coding: utf-8 -*-
"""AUDIT_render 静态核验 4：scene_plates 缺失对 16 个 V3 模板严格校验的影响 + 渲染期行为判定。"""
import json, sys
from pathlib import Path
REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO))
from pipeline import template_v3 as tv, style_v2

TPL = REPO / "contract" / "templates_v3"
res = tv.validate_dir(TPL, expect=16, strict_plates=True)
fails = {r["id"]: r["errors"] for r in res["results"] if not r["ok"]}
print("validate_dir ok =", res["ok"], " 模板数 =", len(res["results"]), " 失败 =", len(fails))
if fails:
    k = next(iter(fails))
    print(f"失败样例 {k}: {json.dumps(fails[k], ensure_ascii=False)[:500]}")
print()
print("== style_v2.plate_path 逐个模板解析（plate_dir 默认 outputs/scene_plates）==")
missing = 0
n = 0
for p in sorted(TPL.glob("*.json")):
    n += 1
    tpl = json.loads(p.read_text(encoding="utf-8"))
    name = str(((tpl.get("stage") or {}).get("plate")) or "")
    plate, level, fb = style_v2.plate_path(None, name)
    if not plate.is_file():
        missing += 1
    print(f"{p.stem:22s} plate={name:30s} level={level:13s} fallback={str(fb):5s} exists={plate.is_file()}")
print()
print("解析不到的模板数:", missing, "/", n)
