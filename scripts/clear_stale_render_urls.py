# -*- coding: utf-8 -*-
"""清理 DB 里指向已不存在文件的 `Episode.render_url`（并可把 ready 打回 pending）。

V69 修（AUDIT_extras.md P1-2）：旧版本**模块级执行**——
`import clear_stale_render_urls`（pytest 收集、编辑器插件、`python -m` 之外的复用）
就会立刻扫描并 `db.commit()` 改生产库；而且无 dry-run、无备份、`sys.path` 依赖 cwd、
对任何非 `/media/` 开头的 URL（外部 http 链接）也一律判"文件不存在"→ 清空。

现在：
- `main(argv)` + `if __name__ == "__main__"`；import 无副作用；
- **默认 dry-run**，要写库必须显式 `--apply`；
- `sys.path` 用 `Path(__file__).resolve().parents[1] / "backend"`（与 cwd 无关）；
- 只处理 `/media/...` 本地媒体 URL，外部 URL 一律跳过并计数；
- 待改清单先写 JSON（`--report`），`--apply` 才 `commit()`，异常 `rollback()`。
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
BACKEND = REPO / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

DEFAULT_REPORT = REPO / "tmp" / "clear_stale_render_urls.json"


def _local_rel(url: str) -> str:
    """/media/xxx/yy.mp4 → xxx/yy.mp4；非 /media/ 前缀返回空串（外部 URL，别动）。"""
    text = str(url or "").strip()
    if not text.startswith("/media/"):
        return ""
    return text[len("/media/"):].lstrip("/").replace("/", "\\" if sys.platform == "win32" else "/")


def collect(limit: int = 0) -> dict:
    """只读扫描：返回待清清单（不 commit）。"""
    from app import config, models  # noqa: PLC0415
    from app.db import SessionLocal  # noqa: PLC0415

    outputs = Path(config.OUTPUTS_DIR)
    db = SessionLocal()
    stale: list[dict] = []
    external = 0
    try:
        for ep in db.query(models.Episode).all():
            url = str(ep.render_url or "").strip()
            if not url:
                continue
            rel = _local_rel(url)
            if not rel:
                external += 1
                continue
            path = outputs / rel
            if path.is_file():
                continue
            stale.append({"id": getattr(ep, "id", None),
                          "template": getattr(ep, "template_id", None),
                          "render_url": url, "status": str(ep.status or ""),
                          "missing_path": str(path)})
            if limit and len(stale) >= limit:
                break
    finally:
        db.close()
    return {"outputs_dir": str(outputs), "stale": stale, "external_skipped": external}


def apply_changes(ids: list) -> dict:
    """按收集到的 id 清 render_url / 打回 pending（显式 --apply 才调用）。"""
    from app import models  # noqa: PLC0415
    from app.db import SessionLocal  # noqa: PLC0415

    db = SessionLocal()
    changed = 0
    reset = 0
    try:
        for item in db.query(models.Episode).filter(models.Episode.id.in_(ids)).all():
            item.render_url = None
            if str(item.status or "") == "ready":
                item.status = "pending"
                reset += 1
            changed += 1
        db.commit()
    except BaseException:
        db.rollback()
        raise
    finally:
        db.close()
    return {"changed": changed, "status_reset": reset}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="清理陈旧的 Episode.render_url（默认 dry-run）")
    ap.add_argument("--apply", action="store_true", help="真正写库（默认只打印）")
    ap.add_argument("--root", default=str(REPO), help="仓库根（默认按脚本位置推断）")
    ap.add_argument("--report", default=str(DEFAULT_REPORT), help="待改清单 JSON 落盘路径")
    ap.add_argument("--limit", type=int, default=0, help="最多处理多少条（0=不限）")
    args = ap.parse_args(argv)
    info = collect(limit=max(0, int(args.limit)))
    result = {"dry_run": not args.apply, "scanned_at": time.strftime("%Y-%m-%d %H:%M:%S"),
              **info}
    report = Path(args.report)
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[{'APPLY' if args.apply else 'dry-run'}] stale={len(info['stale'])} "
          f"external_skipped={info['external_skipped']} report={report}")
    if not args.apply:
        for row in info["stale"][:20]:
            print(f"  - ep={row['id']} {row['template']} {row['render_url']} "
                  f"(missing: {row['missing_path']})")
        print("未写库。确认无误后加 --apply 执行。")
        return 0
    ids = [row["id"] for row in info["stale"] if row.get("id") is not None]
    if not ids:
        print("没有需要清理的记录")
        return 0
    applied = apply_changes(ids)
    result["applied"] = applied
    report.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(applied, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
