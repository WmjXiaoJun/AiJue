# -*- coding: utf-8 -*-
"""V14 全量重做：16 个模板 = 语义重生成 + MuseTalk 口型 + 重新剪辑 + 海报刷新。

tonight_show 于 2026-11-01 21:38 已完成（语义 + 口型），跳过。
其余 15 个串行执行，进度写入 tmp/v14/full_run_status.json。
"""
import json
import pathlib
import subprocess
import sys
import time

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from pipeline import templates3d as t3  # noqa: E402
from pipeline import templates3d_semantic as sem  # noqa: E402

LOG = REPO / "tmp/v14/full_run.log"
STATUS = REPO / "tmp/v14/full_run_status.json"
FFMPEG = REPO / "tools/node_modules/ffmpeg-static/ffmpeg.exe"

ORDER = [
    "classics", "history_talk", "masked_singer", "roundtable",
    "national_treasure", "hot_ones", "mystery", "qipashuo", "shark_tank",
    "slow_life", "snl_sketch", "standup", "taskmaster", "traitors", "history_mystery",
]


def log(message: str) -> None:
    line = f"[{time.strftime('%H:%M:%S')}] {message}"
    print(line, flush=True)
    with LOG.open("a", encoding="utf-8") as handle:
        handle.write(line + "\n")


def read_status() -> dict:
    if STATUS.is_file():
        try:
            return json.loads(STATUS.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            return {}
    return {}


def write_status(data: dict) -> None:
    STATUS.parent.mkdir(parents=True, exist_ok=True)
    STATUS.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def refresh_poster(tid: str) -> None:
    final = t3.OUT3D / tid / "final.mp4"
    poster = t3.OUT3D / tid / "poster.jpg"
    if not final.is_file():
        return
    subprocess.run([
        str(FFMPEG), "-y", "-hide_banner", "-loglevel", "error",
        "-ss", "12", "-i", str(final), "-frames:v", "1", "-q:v", "3",
        "-vf", "scale=960:-1", str(poster),
    ], check=False)


def main() -> int:
    status = read_status()
    started = time.time()
    for tid in ORDER:
        if status.get(tid, {}).get("status") == "done":
            log(f"skip {tid} (done)")
            continue
        status[tid] = {"status": "running", "started": time.strftime("%Y-%m-%dT%H:%M:%S")}
        write_status(status)
        log(f"=== {tid} 开始（语义 + 口型 + 剪辑）===")
        t0 = time.time()
        try:
            code = sem.main(["--template", tid])
            if code != 0:
                raise RuntimeError(f"main exit {code}")
            refresh_poster(tid)
            elapsed = round(time.time() - t0, 1)
            status[tid] = {"status": "done", "elapsed_sec": elapsed,
                           "finished": time.strftime("%Y-%m-%dT%H:%M:%S")}
            write_status(status)
            log(f"=== {tid} 完成 {elapsed}s ===")
        except Exception as exc:  # noqa: BLE001
            status[tid] = {"status": "failed", "error": f"{type(exc).__name__}: {exc}"[-500:],
                           "finished": time.strftime("%Y-%m-%dT%H:%M:%S")}
            write_status(status)
            log(f"=== {tid} 失败: {exc} ===")

    total = round(time.time() - started, 1)
    done = sum(1 for tid in t3.TEMPLATES if status.get(tid, {}).get("status") == "done")
    log(f"全量结束：{done}/{len(t3.TEMPLATES)} done，总耗时 {total}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
