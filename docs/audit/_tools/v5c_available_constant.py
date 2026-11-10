# -*- coding: utf-8 -*-
"""AUDIT_render 静态核验 5c：available() 是否无论配置如何都返回 order[0]。"""
import json, sys
from pathlib import Path
REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO))
from pipeline import muse_video as mv

empty = lambda *a, **k: {"key": "", "url": "", "base": "", "model": "", "path": "", "poll": 6,
                         "timeout": 60, "token": "", "account": "", "poll_url": ""}
for name in ("apimart_config", "metaso_config", "minimax_config", "cloud_config",
             "replicate_config", "useapi_config"):
    if hasattr(mv, name):
        setattr(mv, name, empty)
mv._local_cmd = lambda provider: ""
st = mv.available()
print(json.dumps({
    "all_configs_emptied": True,
    "ok": st["ok"],
    "provider": st["provider"],
    "configured_flags": {k: v.get("configured", v.get("installed")) for k, v in st["checks"].items()},
}, ensure_ascii=False, indent=1))
