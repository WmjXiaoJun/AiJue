# -*- coding: utf-8 -*-
"""V58：5 分钟长版验收（等批量跑完 → 逐条体检 → 出验收表）。"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from pipeline import config, utils, v5_catalog  # noqa: E402

TARGET = 300.0
OUT = config.OUTPUTS_ROOT / "longform"
REPORT = OUT / "acceptance_300s.json"
DOC = REPO / "docs" / "V58_LONGFORM_ACCEPTANCE.md"


def _alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        out = subprocess.run(["tasklist", "/FI", "PID eq %d" % pid, "/NH"],
                             capture_output=True, text=True, timeout=30).stdout
        return str(pid) in (out or "")
    except Exception:
        return False


def _final_of(tid: str):
    for root in (config.OUTPUTS_ROOT / "templates_v5",
                 config.OUTPUTS_ROOT / "templates_3d"):
        for name in ("final_%ds.mp4" % int(TARGET), "final.mp4"):
            path = root / tid / name
            if path.is_file() and path.stat().st_size > 0:
                return path
    return None


def _subtitle_lines(tid: str) -> int:
    best = 0
    for path in (config.OUTPUTS_ROOT / "templates_3d" / tid / "work" / "final.ass",
                 config.OUTPUTS_ROOT / "templates_v5" / tid / "work" / "final.ass",
                 config.OUTPUTS_ROOT / "fillers" / tid / "final.ass"):
        if not path.is_file():
            continue
        try:
            best = max(best, path.read_text(encoding="utf-8").count("Dialogue:"))
        except OSError:
            continue
    return best


def _chapters(tid: str) -> int:
    best = 0
    for path in (config.OUTPUTS_ROOT / "templates_3d" / tid / "work" / "episode.json",
                 config.OUTPUTS_ROOT / "templates_v5" / tid / "work" / "episode.json"):
        if not path.is_file():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        best = max(best, sum(1 for s in data.get("shots") or []
                             if str(s.get("id", "")).startswith("v5_ch")))
    return best


def _row(tid: str, label: str) -> dict:
    final = _final_of(tid)
    row = {"template": tid, "label": label, "final": str(final) if final else None}
    if final is None:
        row.update(ok=False, problems=["没有成片"])
        return row
    probe = utils.probe_video(final) or {}
    row.update({
        "duration": round(float(probe.get("duration") or 0), 2),
        "bytes": final.stat().st_size,
        "width": probe.get("width"), "height": probe.get("height"),
        "has_audio": bool(probe.get("has_audio")),
        "subtitles": _subtitle_lines(tid),
        "chapters": _chapters(tid),
    })
    ev_path = final.parent / ("longform_%ds.json" % int(TARGET))
    if ev_path.is_file():
        try:
            ev = json.loads(ev_path.read_text(encoding="utf-8"))
            row["unique_shots"] = ev.get("unique_shots")
            row["fillers"] = ev.get("fillers")
        except (OSError, ValueError):
            pass
    problems = []
    if row["duration"] < TARGET - 1.0:
        problems.append("时长 %ss" % row["duration"])
    if not row["has_audio"]:
        problems.append("无音轨")
    if row["subtitles"] < 3:
        problems.append("字幕 %d 条" % row["subtitles"])
    if (row["width"], row["height"]) != (1280, 720):
        problems.append("%sx%s" % (row["width"], row["height"]))
    row["problems"] = problems
    row["ok"] = not problems
    return row


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="5 分钟长版验收")
    ap.add_argument("--wait-pid", type=int, default=0)
    ap.add_argument("--poll", type=int, default=60)
    ap.add_argument("--timeout", type=int, default=6 * 3600)
    args = ap.parse_args(argv)
    if args.wait_pid:
        started = time.time()
        while _alive(args.wait_pid) and time.time() - started < args.timeout:
            print("[wait] 批量仍在跑（pid=%d），%ds 后复查" % (args.wait_pid, args.poll), flush=True)
            time.sleep(args.poll)
        print("[wait] 批量已结束，开始验收", flush=True)
    catalog = v5_catalog.load_catalog()
    templates = catalog.get("templates") or {}
    rows = [_row(tid, str(tpl.get("label") or tid)) for tid, tpl in sorted(templates.items())]
    ok = [r for r in rows if r.get("ok")]
    bad = [r for r in rows if not r.get("ok")]
    OUT.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(json.dumps({"target": TARGET,
                                  "generated": time.strftime("%Y-%m-%d %H:%M:%S"),
                                  "ok": len(ok), "total": len(rows), "rows": rows},
                                 ensure_ascii=False, indent=2), encoding="utf-8")
    lines = ["# V58 5 分钟长版验收", "",
             "- 目标时长：%ds　达标：**%d/%d**" % (int(TARGET), len(ok), len(rows)),
             "- 生成时间：%s" % time.strftime("%Y-%m-%d %H:%M:%S"), "",
             "| # | 模板 | 时长 | 分辨率 | 音轨 | 字幕 | 章节 | 镜头 | 状态 |",
             "|---|---|---|---|---|---|---|---|---|"]
    for idx, row in enumerate(rows, start=1):
        status = "OK" if row.get("ok") else "；".join(row.get("problems") or ["失败"])
        lines.append("| %d | %s | %s | %sx%s | %s | %s | %s | %s | %s |" % (
            idx, row["template"], row.get("duration", "-"), row.get("width", "-"),
            row.get("height", "-"), "有" if row.get("has_audio") else "无",
            row.get("subtitles", "-"), row.get("chapters", "-"),
            row.get("unique_shots") or row.get("fillers") or "-", status))
    if bad:
        lines += ["", "## 未达标", ""]
        lines += ["- %s：%s" % (r["template"], "；".join(r.get("problems") or ["失败"]))
                  for r in bad]
    DOC.parent.mkdir(parents=True, exist_ok=True)
    DOC.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps({"ok": len(ok), "total": len(rows), "report": str(REPORT),
                      "doc": str(DOC), "bad": [r["template"] for r in bad]},
                     ensure_ascii=False))
    return 0 if not bad else 1


if __name__ == "__main__":
    raise SystemExit(main())
