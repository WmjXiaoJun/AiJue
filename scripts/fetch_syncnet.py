# -*- coding: utf-8 -*-
"""V52：SyncNet 权重下载/校验（MuseTalk 的 LSE 打分前置）。

当前仓库里的 local_models/lipsync_models/syncnet/latentsync_syncnet.pt 是**下载中断的损坏文件**
（zip 中央目录缺失）。本脚本负责：
1) 校验现有文件是否可读；
2) 从环境变量 SYNCNET_URL 下载并校验（默认不联网）。

用法：
  python scripts/fetch_syncnet.py --check
  $env:SYNCNET_URL="https://.../latentsync_syncnet.pt"; python scripts/fetch_syncnet.py
"""
from __future__ import annotations

import argparse
import os
import sys
import urllib.request
import zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
TARGET = REPO / "local_models" / "lipsync_models" / "syncnet" / "latentsync_syncnet.pt"


def validate(path: Path) -> tuple[bool, str]:
    if not path.is_file():
        return False, "文件不存在"
    size = path.stat().st_size
    try:
        with zipfile.ZipFile(path) as zf:
            entries = len(zf.namelist())
        return True, f"{size / 1048576:.1f}MB / {entries} entries"
    except Exception as exc:  # noqa: BLE001
        return False, f"{size / 1048576:.1f}MB，不可读: {exc}"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="SyncNet 权重下载/校验")
    ap.add_argument("--check", action="store_true", help="只校验现有文件")
    ap.add_argument("--force", action="store_true", help="覆盖已存在的文件")
    args = ap.parse_args(argv)
    ok, info = validate(TARGET)
    state = "OK" if ok else "BAD"
    print(f"现有权重: {TARGET} -> {state}（{info}）")
    if ok and not args.force:
        return 0
    url = str(os.environ.get("SYNCNET_URL") or "").strip()
    if args.check or not url:
        print("未配置 SYNCNET_URL；请从 LatentSync / MuseTalk 官方仓库下载 ",
              "latentsync_syncnet.pt 后放到上面的路径（或设置 SYNCNET_URL 让本脚本下载）。")
        return 0 if ok else 2
    TARGET.parent.mkdir(parents=True, exist_ok=True)
    tmp = TARGET.with_suffix(".download")
    print(f"下载 {url}")
    urllib.request.urlretrieve(url, tmp)
    ok, info = validate(tmp)
    if not ok:
        print(f"下载结果不可用：{info}（保留 {tmp} 供排查）")
        return 3
    os.replace(tmp, TARGET)
    print(f"已更新并校验通过：{info}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())