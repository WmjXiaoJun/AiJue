# -*- coding: utf-8 -*-
"""AUDIT_render 静态核验 6：tts_breeze 输出文件名只含 batch 下标 → 跨 run 覆盖同名音频，
让 render_v5.voice_cache（按文本做 key）指向错误内容。全部用假 HTTP 层复现，不碰 GPU/网络。"""
import json, sys, types
from pathlib import Path
REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO))
from pipeline import tts_breeze as tb

OUT = REPO / "tmp" / "_audit_tts"
OUT.mkdir(parents=True, exist_ok=True)
for p in OUT.glob("*.wav"):
    p.unlink()

SR = 24000
def fake_post(url, data=None, files=None, timeout=None):
    texts = json.loads(data["texts"])
    # 每段音频 = 该文本长度 * 0.01s 的静音，段长按采样点严格切分
    segs = []
    for t in texts:
        n = int(SR * 0.01 * len(t))
        segs.append(b"\x00\x00" * n)
    pcm = b"".join(segs)
    resp = types.SimpleNamespace(
        status_code=200, content=pcm,
        headers={"X-Sample-Rate": str(SR),
                 "X-Segment-Bytes": ",".join(str(len(s)) for s in segs)},
        text="", raise_for_status=lambda: None)
    return resp

tb.httpx = types.SimpleNamespace(post=fake_post, get=lambda *a, **k: None,
                                 Timeout=lambda *a, **k: None)
tb.server_alive = lambda base=None: True

def run(role_texts, label):
    res = tb.synthesize(role_texts, OUT, auto_start=False)
    snap = {}
    for r in res:
        p = Path(r["path"])
        snap[r["text"]] = {"file": p.name, "bytes": p.stat().st_size, "duration": r["duration"]}
    print(f"[{label}] " + json.dumps(snap, ensure_ascii=False))
    return res

print("== run1：缓存为空，合成 A、B 两句 ==")
run({"laozhou": ["第一句台词内容比较长", "第二句"]}, "run1")
a1 = (OUT / "streamb_laozhou_000.wav").stat().st_size
print("  streamb_laozhou_000.wav bytes =", a1, " (== 第一句 'A' 的音频)")
print()
print("== run2：只改第 1 句（A→A2），第 2 句不动 ==")
run({"laozhou": ["改过的第一句台词比较长"]}, "run2")
a2 = (OUT / "streamb_laozhou_000.wav").stat().st_size
print("  streamb_laozhou_000.wav bytes =", a2, " (被 A2 覆盖)")
print()
print("结论：run1 的 B 句缓存 path=streamb_laozhou_001.wav 未受影响；")
print("      但 A 句的缓存 path=streamb_laozhou_000.wav 现在装的是 A2 的音频。")
print("      若计划里还留着 A 句（例如另一集/另一镜仍用 A），voice_cache 命中 → 播的是 A2 的声音。")
