# -*- coding: utf-8 -*-
"""fix-round3 短片 QC 验证：用 ffmpeg lavfi 在 tmp/ 造假 mp4（不写 outputs/）。

覆盖：
  * evaluate_hq_qc 双写 qc_report.json + qc_shorts.json，且带宽窄的上下界字段；
  * 时长门：target=14.12 → [8.472, 21.18]；7s / 30s 必须红，14s 必须绿；
  * write_shorts_report：成片不存在 / QC 比成片旧 → 都必须 raise；正常 → qc_ok true。

用法：python docs/audit/_tools/fix4_verify_qc.py
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO))

from pipeline import config, v5_catalog          # noqa: E402
from pipeline.agents import registry             # noqa: E402

TID = "custom_tpl_v46_cyber"
PASS, FAIL = [], []


def check(name: str, cond: bool, detail: str = "") -> None:
    (PASS if cond else FAIL).append(name)
    print(f"   [{'PASS' if cond else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))


class Ctx:
    def __init__(self, run_dir: Path):
        cat = v5_catalog.load_catalog()
        self.template_id = TID
        self.template = cat["templates"][TID]
        self.duration = float(self.template.get("target_duration") or 12)
        self.force = False
        self.run_dir = run_dir
        self.paths = {}
        self.logs = []

    def log(self, msg):
        self.logs.append(str(msg))


def make_mp4(path: Path, seconds: float, with_audio: bool = True) -> None:
    """1280x720 + 测试图案（保证 >100KB），可选正弦音轨。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [config.FFMPEG_BIN, "-y", "-hide_banner", "-loglevel", "error",
           "-f", "lavfi", "-i", f"testsrc=size=1280x720:rate=25:duration={seconds}"]
    if with_audio:
        cmd += ["-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}"]
    cmd += ["-c:v", "libx264", "-preset", "ultrafast", "-b:v", "600k",
            "-pix_fmt", "yuv420p"]
    if with_audio:
        cmd += ["-c:a", "aac", "-b:a", "64k", "-shortest"]
    cmd += [str(path)]
    subprocess.run(cmd, check=True)


def make_poster(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run([config.FFMPEG_BIN, "-y", "-hide_banner", "-loglevel", "error",
                    "-f", "lavfi", "-i", "testsrc=size=960x540:duration=1",
                    "-frames:v", "1", str(path)], check=True)


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="fix4_qc_"))
    run_dir = tmp / "run"
    run_dir.mkdir(parents=True, exist_ok=True)
    old_root = registry._outputs_root
    registry._outputs_root = lambda: tmp          # 只改输出根，不碰 outputs/
    try:
        out = tmp / "templates_3d" / TID
        ctx = Ctx(run_dir)
        target = float(ctx.template.get("target_duration") or 0)
        print(f"模板 {TID} target_duration={target}s")

        print("\n== 1. 正常 14s 片 → 绿；双写两份证据 ==")
        make_mp4(out / "final.mp4", 14.0)
        make_poster(out / "poster.jpg")
        res = registry.evaluate_hq_qc(ctx, {})
        report = json.loads((out / "qc_report.json").read_text(encoding="utf-8"))
        alias = json.loads((out / "qc_shorts.json").read_text(encoding="utf-8"))
        check("evaluate_hq_qc 通过", res["qc_ok"] is True)
        check("写 qc_report.json", (out / "qc_report.json").is_file())
        check("写 qc_shorts.json（别名）", (out / "qc_shorts.json").is_file())
        check("两份内容一致", report["ok"] == alias["ok"] and report["checks"] == alias["checks"])
        dur = report["checks"]["duration"]
        check("证据带上下界",
              dur["min_duration"] == 8.472 and dur["max_duration"] == 21.18,
              f"min={dur['min_duration']} max={dur['max_duration']} d={dur['duration']}")
        check("报告顶层带 duration/min/max",
              {"duration", "min_duration", "max_duration"} <= set(report),
              str(sorted(report)))

        print("\n== 2. 7s（低于 0.6*target）→ 红 ==")
        make_mp4(out / "final.mp4", 7.0)
        try:
            registry.evaluate_hq_qc(ctx, {})
            check("7s 被判红", False)
        except RuntimeError as exc:
            check("7s 被判红", "duration" in str(exc), str(exc))
        red = json.loads((out / "qc_report.json").read_text(encoding="utf-8"))
        check("qc_report.json 记 duration.ok=False", red["checks"]["duration"]["ok"] is False,
              str(red["checks"]["duration"]))

        print("\n== 3. 30s（高于 max(1.5*target, target+2)=21.18）→ 红（原来无上限会过） ==")
        make_mp4(out / "final.mp4", 30.0)
        try:
            registry.evaluate_hq_qc(ctx, {})
            check("30s 被判红", False)
        except RuntimeError as exc:
            check("30s 被判红", "duration" in str(exc), str(exc))

        print("\n== 4. write_shorts_report 前置断言 ==")
        final = out / "final.mp4"
        make_mp4(final, 14.0)
        make_poster(out / "poster.jpg")
        registry.evaluate_hq_qc(ctx, {})          # QC 在成片之后 → 有效
        summary = registry.write_shorts_report(ctx, {})
        check("正常通过并标注证据文件名", summary["qc_ok"] is True
              and summary["qc_report"] == "qc_report.json", str(summary["qc_report"]))

        # 4b. 成片比 QC 新 → 陈旧证据必须拒绝
        import os as _os
        import time as _time
        _time.sleep(0.05)
        _os.utime(final, None)                    # 成片 mtime = now（比 QC 新 >1s 才拦）
        _os.utime(final, (_time.time() + 5, _time.time() + 5))
        try:
            registry.write_shorts_report(ctx, {})
            check("成片比 QC 新 → 拒绝发布", False)
        except RuntimeError as exc:
            check("成片比 QC 新 → 拒绝发布", "陈旧" in str(exc), str(exc))

        # 4c. 成片不存在 → 拒绝
        final.unlink()
        try:
            registry.write_shorts_report(ctx, {})
            check("成片不存在 → 拒绝发布", False)
        except RuntimeError as exc:
            check("成片不存在 → 拒绝发布", "不存在" in str(exc), str(exc))

        # 4d. 只有旧别名 qc_shorts.json 时也能读
        make_mp4(final, 14.0)
        registry.evaluate_hq_qc(ctx, {})
        (out / "qc_report.json").unlink()
        summary2 = registry.write_shorts_report(ctx, {})
        check("缺 qc_report.json 时回退读 qc_shorts.json",
              summary2["qc_report"] == "qc_shorts.json" and summary2["qc_ok"] is True,
              str(summary2["qc_report"]))
    finally:
        registry._outputs_root = old_root
        shutil.rmtree(tmp, ignore_errors=True)

    print(f"\n== 结果：PASS {len(PASS)} / FAIL {len(FAIL)} ==")
    if FAIL:
        print("FAILED:", FAIL)
    return 0 if not FAIL else 1


if __name__ == "__main__":
    raise SystemExit(main())
