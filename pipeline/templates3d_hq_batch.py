# -*- coding: utf-8 -*-
"""V16 全量高清重做：15 个模板（tonight_show 样片已完成）逐个跑 1024×576 T2VA + ref2va。"""

import json
import pathlib
import sys
import time

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from pipeline import templates3d as t3  # noqa: E402
from pipeline import templates3d_hq as hq  # noqa: E402

LOG = REPO / "tmp/v16/hq_batch.log"
STATUS = REPO / "tmp/v16/hq_batch_status.json"

ORDER = [tid for tid in t3.TEMPLATES if tid != "tonight_show"]


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
    STATUS.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> int:
    status = read_status()
    started = time.time()
    for tid in ORDER:
        if status.get(tid, {}).get("status") == "done":
            log(f"skip {tid}")
            continue
        status[tid] = {"status": "running", "started": time.strftime("%Y-%m-%dT%H:%M:%S")}
        write_status(status)
        log(f"=== {tid} 开始（1024×576 HQ）===")
        t0 = time.time()
        try:
            code = hq.main(["--template", tid])
            if code != 0:
                raise RuntimeError(f"exit {code}")
            status[tid] = {"status": "done", "elapsed_sec": round(time.time() - t0, 1)}
            log(f"=== {tid} 完成 {status[tid]['elapsed_sec']}s ===")
        except Exception as exc:  # noqa: BLE001
            status[tid] = {"status": "failed", "error": f"{type(exc).__name__}: {exc}"[-400:]}
            log(f"=== {tid} 失败: {exc} ===")
        write_status(status)
    log(f"HQ 全量结束，总耗时 {round(time.time() - started, 1)}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
