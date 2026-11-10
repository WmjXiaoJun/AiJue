# -*- coding: utf-8 -*-
"""归档旧成片/旧镜头（用户指示："以前生成的视频全部删除了"）。

**不硬删**：移动到 `outputs/_trash_r7/`（保留目录结构），确认新链路可用后你可以直接
把 `_trash_r7` 整个删掉。图片/音频（关键帧、场景图、配音）不动 —— 用户说"图片/语音本地先凑合"。

用法：
    python scripts/archive_old_videos.py --dry-run
    python scripts/archive_old_videos.py
    python scripts/archive_old_videos.py --purge   # 直接删（慎用）
"""
from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
OUT = REPO / "outputs"
TRASH = OUT / "_trash_r7"

#: 这些子目录里的 mp4 属于"旧成片/旧镜头"
TARGETS = (
    "templates_3d", "templates_v5", "templates_v3", "templates_2d",
    "episodes", "series", "longform", "muse", "h3", "fillers",
    "film", "exports",
)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="归档旧成片/旧镜头")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--purge", action="store_true", help="直接删除而不是移动")
    args = ap.parse_args(argv)
    moved, skipped, total = [], 0, 0
    for sub in TARGETS:
        root = OUT / sub
        if not root.is_dir():
            continue
        for p in sorted(root.rglob("*.mp4")):
            rel = p.relative_to(OUT)
            # 模型权重/本地资产目录不动
            if "_trash_r7" in rel.parts:
                continue
            total += p.stat().st_size
            moved.append(str(rel).replace("\\", "/"))
            if args.dry_run:
                continue
            if args.purge:
                p.unlink(missing_ok=True)
            else:
                dst = TRASH / rel
                dst.parent.mkdir(parents=True, exist_ok=True)
                if dst.is_file():
                    p.unlink(missing_ok=True)
                else:
                    shutil.move(str(p), str(dst))
    print({"dry_run": args.dry_run, "purge": args.purge, "files": len(moved),
           "size_mb": round(total / 1048576, 1), "trash": str(TRASH),
           "sample": moved[:8]})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
