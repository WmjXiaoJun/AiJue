# -*- coding: utf-8 -*-
"""成片曝光归一：把偏暗的镜头/成片抬到可看（用户反馈"外面全黑、中间一个亮圈"）。

思路：用 ffmpeg 的 signalstats 量一遍平均亮度（YAVG），再按目标亮度算 gamma：
    gamma = log(target/255) / log(mean/255)
gamma>1 提亮暗部、<1 压暗，单次滤镜、不重编码多次。默认只**提亮**（暗片才动），
避免把本来就亮的片子洗白。

用法：
    python scripts/fix_exposure.py --video outputs/templates_3d/classics/final.mp4
    python scripts/fix_exposure.py --video X --target 105 --dry-run
"""
from __future__ import annotations

import argparse
import math
import re
import shutil
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from pipeline import config, utils  # noqa: E402

FFMPEG = str(config.FFMPEG_BIN)


def measure_luma(video: Path, samples: int = 6) -> float:
    """取 N 个时间点的 YAVG，返回中位数（比全片遍历快得多）。"""
    dur = float(utils.probe_duration(video) or 0.0)
    if dur <= 0:
        raise RuntimeError(f"读不到时长: {video}")
    vals: list[float] = []
    for i in range(samples):
        t = dur * (i + 1) / (samples + 1)
        proc = subprocess.run(
            [FFMPEG, "-hide_banner", "-nostdin", "-ss", f"{t:.2f}", "-i", str(video),
             "-frames:v", "1", "-vf", "signalstats,metadata=print", "-f", "null", "NUL"],
            capture_output=True, text=True, errors="replace", timeout=120)
        blob = (proc.stdout or "") + "\n" + (proc.stderr or "")
        m = re.search(r"YAVG[=:]\s*([0-9.]+)", blob)
        if m:
            vals.append(float(m.group(1)))
    if not vals:
        raise RuntimeError("signalstats 没读到亮度")
    vals.sort()
    return vals[len(vals) // 2]


def gamma_for(mean: float, target: float) -> float:
    """保留接口：返回 ffmpeg 的 gamma 值（>1 提亮）。仅在需要时用。"""
    if mean <= 2 or mean >= target:
        return 1.0
    # 经验：ffmpeg eq=gamma 大于 1 提亮中间调，约 1 + 0.5*(缺口比例)
    return round(max(1.0, min(2.2, 1.0 + 0.9 * (target - mean) / 255.0)), 3)


def fix(video: Path, *, target: float = 104.0, dry: bool = False,
        backup: bool = True) -> dict:
    """把暗片抬到 target 附近：先按缺口加 brightness，再轻微 gamma 提中间调；渲染后复测。"""
    video = Path(video)
    before = measure_luma(video)
    info = {"video": str(video), "mean_before": round(before, 1), "target": target,
            "applied": False}
    if before >= target - 4:
        info["note"] = "已经够亮，跳过"
        return info
    lift = round(min(0.16, max(0.02, (target - before) / 255.0 * 0.95)), 3)
    gamma = gamma_for(before, target)
    info.update({"brightness": lift, "gamma": gamma})
    if dry:
        info["note"] = "dry-run：未写盘"
        return info
    tmp = video.with_name(video.stem + ".grade.mp4")
    vf = (f"eq=brightness={lift}:gamma={gamma}:saturation=1.04:contrast=0.98,"
          f"format=yuv420p")
    utils.ffmpeg(["-i", str(video), "-vf", vf,
                  "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
                  "-c:a", "copy", "-movflags", "+faststart", "-y", str(tmp)])
    if backup:
        bak = video.with_name(video.stem + ".pre_grade.mp4")
        if not bak.is_file():
            shutil.copy2(video, bak)
    tmp.replace(video)
    info["applied"] = True
    info["mean_after"] = round(measure_luma(video), 1)
    return info


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="成片曝光归一（提亮暗片）")
    ap.add_argument("--video", required=True)
    ap.add_argument("--target", type=float, default=104.0)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--no-backup", action="store_true")
    args = ap.parse_args(argv)
    print(fix(Path(args.video), target=args.target, dry=args.dry_run,
              backup=not args.no_backup))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
