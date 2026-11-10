# -*- coding: utf-8 -*-
"""P0-3 自证：TTS 文件名按「批内下标」→ 按「台词内容」，并用假 HTTP 层跑**真实** synthesize。

对照实验（不联网、不启动 Breeze、不占 GPU；只写 tmp/）：
  run1：合成 A、B 两句
  run2：只改第 1 句（A→A2）
  旧行为（streamb_<role>_<idx>.wav）：A 的音频被 A2 覆盖 → voice_cache 里 "A" 的 path 指向 A2
  新行为（streamb_<role>_<sha1(text)[:10]>.wav）：A 的文件保持原样，A2 落到另一个文件名
"""
from __future__ import annotations

import json
import shutil
import sys
import types
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO))

from pipeline import tts_breeze as tb  # noqa: E402

OUT = REPO / "tmp" / "_r4_tts_content_addressed"
SR = 24000


def _fake_post(url, data=None, files=None, timeout=None):
    texts = json.loads(data["texts"])
    segs = [b"\x00\x00" * int(SR * 0.01 * len(t)) for t in texts]
    return types.SimpleNamespace(
        status_code=200, content=b"".join(segs),
        headers={"X-Sample-Rate": str(SR),
                 "X-Segment-Bytes": ",".join(str(len(s)) for s in segs)},
        text="", raise_for_status=lambda: None)


def _install_fake_http() -> None:
    tb.httpx = types.SimpleNamespace(post=_fake_post, get=lambda *a, **k: None,
                                     Timeout=lambda *a, **k: None)
    tb.server_alive = lambda base=None: True


def _old_style_name(role: str, idx: int) -> str:
    """旧实现的命名（就为了在报告里做对照，不参与产品逻辑）。"""
    return f"streamb_{role}_{idx:03d}.wav"


def run(role_texts, label: str) -> dict:
    res = tb.synthesize(role_texts, OUT, auto_start=False)
    snap = {}
    for item in res:
        path = Path(item["path"])
        snap[item["text"]] = {
            "file": path.name,
            "bytes": path.stat().st_size,
            "duration": item["duration"],
            "reused": item.get("reused"),
            "text_sha1": item.get("text_sha1"),
        }
    print(f"[{label}] " + json.dumps(snap, ensure_ascii=False, indent=1))
    return {"items": res, "snap": snap}


def main() -> int:
    if OUT.exists():
        shutil.rmtree(OUT, ignore_errors=True)
    OUT.mkdir(parents=True, exist_ok=True)
    _install_fake_http()

    print("== run1：合成 A、B 两句 ==")
    r1 = run({"laozhou": ["第一句台词内容比较长", "第二句"]}, "run1")
    a_file = Path(r1["items"][0]["path"])
    a_bytes = a_file.stat().st_size
    print(f"  A 落在 {a_file.name}（{a_bytes}B）；旧命名会是 {_old_style_name('laozhou', 0)}")

    print("\n== run2：只改第 1 句（A→A2），第 2 句仍在计划里 ==")
    r2 = run({"laozhou": ["改过的第一句台词比较长", "第二句"]}, "run2")
    a_after = a_file.stat().st_size
    print(f"  A 的文件 {a_file.name} 现在 {a_after}B（应仍是 {a_bytes}B，未被覆盖）")

    print("\n== run3：原样再跑一次（应全部复用，不重新合成）==")
    r3 = run({"laozhou": ["改过的第一句台词比较长", "第二句"]}, "run3")
    reused = [i.get("reused") for i in r3["items"]]

    print("\n== 对照：同一批文本，旧的 idx 命名必然撞名 ==")
    old_names = [_old_style_name("laozhou", i) for i in range(2)]
    new_names = [Path(i["path"]).name for i in r1["items"]]
    print("  run1 旧命名:", old_names)
    print("  run1 新命名:", new_names)
    print("  run2 的新句文件名:", Path(r2["items"][0]["path"]).name,
          "（与 A 不同名 → 不会覆盖 A）")

    ok = (a_after == a_bytes and bool(reused) and all(reused)
          and len(set(new_names)) == len(new_names))
    print("\n自证结论：")
    print(f"  - A 的音频在 run2 后仍是 {a_after}B：{'未被覆盖' if a_after == a_bytes else '被覆盖(FAIL)'}")
    print(f"  - run3 全部命中已有音频（省合成）: {reused}")
    print(f"  - 文件名唯一: {len(set(new_names)) == len(new_names)}")
    assert ok, "P0-3 未修好"
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
