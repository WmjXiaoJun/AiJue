# -*- coding: utf-8 -*-
"""自检：muse_video.available() 是否按真实配置判定 + _local_generate 模板 → argv。"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO))

from pipeline import muse_video as mv  # noqa: E402


def main() -> int:
    print("== 1) 本机真实配置（backend/data/*.env 里的 key 也算配置） ==")
    st_real = mv.available()
    print(json.dumps({"ok": st_real["ok"], "provider": st_real["provider"],
                      "configured_providers": st_real["configured_providers"]},
                     ensure_ascii=False))

    print("\n== 2) 清空所有 env + env 文件后 available()（必须 ok=False, provider=stub） ==")
    for name in ("APIMART_API_KEY", "METASO_API_KEY", "METASO_H3_KEY", "MINIMAX_API_KEY",
                 "MUSE_API_URL", "MUSE_API_KEY", "REPLICATE_API_TOKEN", "USEAPI_TOKEN",
                 "USEAPI_ACCOUNT", "MUSEV_CMD", "MUSEPOSE_CMD"):
        os.environ.pop(name, None)
    orig_env_file = mv._env_file
    mv._env_file = lambda *a, **k: {}
    try:
        st = mv.available()
    finally:
        mv._env_file = orig_env_file
    print(json.dumps({"ok": st["ok"], "provider": st["provider"],
                      "configured_providers": st["configured_providers"],
                      "musev_local": st["checks"]["musev_local"],
                      "stub": st["checks"]["stub"],
                      "hint": st["hint"][:70]}, ensure_ascii=False, indent=1))
    assert st["ok"] is False, "清空配置后 ok 仍为 True（假能力未修）"
    assert st["provider"] == "stub", st["provider"]

    print("\n== 3) 模板 → argv（中文/空格/&/|/反斜杠路径） ==")
    tpl = (r'.venv\Scripts\python.exe scripts/inference/text2video.py '
           r'--prompt "{prompt}" --height {height} --width {width} --output {out}')
    argv = mv._argv_from_template(tpl, {
        "prompt": "他说: 你好 & 再见 | 100% 完成", "height": "384",
        "width": "256", "out": r"C:\tmp\a b.mp4"})
    print(json.dumps(argv, ensure_ascii=False))

    print("\n== 未知占位符不崩 ==")
    print(json.dumps(mv._argv_from_template("run --cfg {cfg} --prompt {prompt}",
                                            {"prompt": "hi"}), ensure_ascii=False))

    print("\n== 4) 工具入口在未配置时必须 skipped（不抛） ==")
    from pipeline.agents import registry  # noqa: PLC0415

    class _Ctx:
        template_id = "custom_tpl_v46_cyber"
        template = {"label": "V46 2D·赛博电影感"}
        seed = 1

        def log(self, *_a, **_k):
            return None

    mv._env_file = lambda *a, **k: {}
    try:
        res = registry.muse_video(_Ctx(), {})
    finally:
        mv._env_file = orig_env_file
    print(json.dumps({k: (v if k != "checks" else "...") for k, v in res.items()},
                     ensure_ascii=False, indent=1))
    assert res.get("skipped") is True, res
    assert "缺" in str(res.get("reason")), res.get("reason")

    print("\n== 5) 已配置但没给 prompt/image/pose → 也 skipped（不打付费接口） ==")
    res2 = registry.muse_video(_Ctx(), {})
    print(json.dumps({"skipped": res2.get("skipped"), "reason": res2.get("reason"),
                      "provider": res2.get("provider")}, ensure_ascii=False))
    assert res2.get("skipped") is True
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
