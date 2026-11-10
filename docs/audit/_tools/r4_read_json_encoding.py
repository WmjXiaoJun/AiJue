# -*- coding: utf-8 -*-
"""自检：utils.read_json 的编码兜底（utf-8 → utf-8-sig → gbk 仅 <8KB → 损坏返回 default）。"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO))

from pipeline import utils  # noqa: E402


def main() -> int:
    d = Path(tempfile.mkdtemp(prefix="rj_"))
    gbk_small = d / "gbk_small.json"
    gbk_small.write_text(json.dumps({"a": "中文"}, ensure_ascii=False), encoding="gbk")
    print("gbk 小文件(<8KB)   ->", utils.read_json(gbk_small, "DEFAULT"))

    gbk_big = d / "gbk_big.json"
    gbk_big.write_text(json.dumps({"a": "中文" * 5000}, ensure_ascii=False), encoding="gbk")
    print(f"gbk 大文件({gbk_big.stat().st_size}B) ->",
          utils.read_json(gbk_big, "DEFAULT"))

    broken = d / "broken.json"
    broken.write_bytes(b'{"a": 1,')
    print("损坏文件            ->", utils.read_json(broken, "DEFAULT"))

    bom = d / "bom.json"
    bom.write_text(json.dumps({"a": "中文"}, ensure_ascii=False), encoding="utf-8-sig")
    print("utf-8 BOM           ->", utils.read_json(bom, "DEFAULT"))

    latin = d / "latin.json"
    latin.write_bytes(b'{"a": "caf\xe9"}')
    print("非法 UTF-8/GBK 均不通 ->", utils.read_json(latin, "DEFAULT"))

    assert utils.read_json(gbk_small) == {"a": "中文"}
    assert utils.read_json(gbk_big, "DEFAULT") == "DEFAULT"
    assert utils.read_json(broken, "DEFAULT") == "DEFAULT"
    print("OK：4 条断言通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
