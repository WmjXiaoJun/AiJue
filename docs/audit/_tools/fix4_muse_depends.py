# -*- coding: utf-8 -*-
"""fix-round3：把 33 份工作流合同里 **muse 阶段** 的 depends 从 [] 改成 ["shots"]。

只做这一处文本替换（其余字节不动），并断言：
  * 33 份仍是合法 JSON，stages 数量/字段集合不变；
  * 只有 muse.depends 变了；musev.depends 仍是 []。

用法：
    python docs/audit/_tools/fix4_muse_depends.py            # 只看将要改什么（dry-run）
    python docs/audit/_tools/fix4_muse_depends.py --apply    # 真改
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
WF_DIR = REPO / "contract" / "v6" / "workflows"
MARK = '"id": "muse",'
OLD = '"depends": []'
NEW = '"depends": ["shots"]'


def patch_text(text: str) -> tuple[str, bool]:
    """只改 muse 对象里的 depends。返回 (新文本, 是否改动)。"""
    i = text.find(MARK)
    if i < 0:
        raise SystemExit("找不到 muse 阶段")
    j = text.find(OLD, i)
    if j < 0:
        raise SystemExit("muse 阶段后面找不到 depends: []")
    k = text.find('"id":', i + len(MARK))
    if 0 <= k < j:
        raise SystemExit("muse 对象里没有 depends，文件结构异常")
    return text[:j] + NEW + text[j + len(OLD):], True


def main() -> int:
    apply = "--apply" in sys.argv
    changed, skipped, errors = [], [], []
    for path in sorted(WF_DIR.glob("*.json")):
        raw = path.read_text(encoding="utf-8")
        before = json.loads(raw)
        muse = [s for s in before.get("stages") or [] if s.get("id") == "muse"]
        if len(muse) != 1:
            skipped.append((path.stem, f"muse 阶段数={len(muse)}"))
            continue
        if muse[0].get("depends") == ["shots"]:
            skipped.append((path.stem, "已是 [shots]"))
            continue
        new_text, _ = patch_text(raw)
        after = json.loads(new_text)                 # 必须仍然合法
        # —— 结构断言：除 muse.depends 之外一切不变
        assert before.keys() == after.keys(), path.stem
        bs, as_ = before["stages"], after["stages"]
        assert len(bs) == len(as_), path.stem
        for b, a in zip(bs, as_):
            if b.get("id") == "muse":
                assert b.keys() == a.keys(), path.stem
                for key in set(b) - {"depends"}:
                    assert b[key] == a[key], (path.stem, key)
                assert a["depends"] == ["shots"], path.stem
            else:
                assert b == a, (path.stem, b.get("id"))
        for key in set(before) - {"stages"}:
            assert before[key] == after[key], (path.stem, key)
        if apply:
            path.write_text(new_text, encoding="utf-8")
        changed.append(path.stem)
    print(json.dumps({"apply": apply, "changed_count": len(changed),
                      "changed": changed, "skipped": skipped, "errors": errors},
                     ensure_ascii=False, indent=2))
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
