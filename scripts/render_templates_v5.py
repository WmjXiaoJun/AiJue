# -*- coding: utf-8 -*-
"""V5 批量渲染：16 个模板（不同风格）→ outputs/templates_v5/{id}/final.mp4 + 汇总。

用法：
  python scripts/render_templates_v5.py                          # 全部（跳过已完成）
  python scripts/render_templates_v5.py --only history_talk,classics
  python scripts/render_templates_v5.py --force --duration 40
  python scripts/render_templates_v5.py --list

产物：
  outputs/templates_v5/batch_report_v5.json   # 逐模板摘要
  outputs/templates_v5/contact_sheet_all.png  # 16 宫格总览
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
sys.path.insert(0, str(REPO))

from pipeline import config, utils  # noqa: E402

LOG = __import__("logging").getLogger("pipeline")
OUT_ROOT = REPO / "outputs" / "templates_v5"
TEMPLATE_DIR = REPO / "contract" / "v5" / "templates"
REPORT = OUT_ROOT / "batch_report_v5.json"


def _read_json(path: Path):
    return utils.read_json(path)


def completed(out_dir: Path) -> bool:
    final = out_dir / "final.mp4"
    if not final.is_file() or final.stat().st_size < 10000:
        return False
    report = _read_json(out_dir / "qc_report.json")
    return isinstance(report, dict) and bool(report.get("ok"))


def render_one(template_id: str, out_root: Path, ns) -> dict:
    from pipeline import render_v5
    out_dir = out_root / template_id
    started = time.time()
    args = argparse.Namespace(
        template=template_id, duration=ns.duration,
        out=str(out_dir / "final.mp4"), workdir=str(out_dir / "work"),
        seed=ns.seed, force=ns.force, limit=ns.limit)
    error = ""
    try:
        render_v5.run_render(args)
    except Exception as exc:  # noqa: BLE001
        error = str(exc)
        LOG.error("[%s] 渲染异常: %s", template_id, exc)
    qc = _read_json(out_dir / "qc_report.json") or {}
    final = out_dir / "final.mp4"
    probe = {}
    if final.is_file():
        try:
            probe = utils.probe_video(final)
        except Exception:  # noqa: BLE001
            probe = {}
    return {
        "id": template_id,
        "ok": bool(isinstance(qc, dict) and qc.get("ok")),
        "error": error,
        "duration": round(float(probe.get("duration") or 0), 2),
        "resolution": f"{probe.get('width')}x{probe.get('height')}",
        "fps": probe.get("fps"),
        "size_bytes": final.stat().st_size if final.is_file() else 0,
        "style": (qc.get("stats") or {}).get("style"),
        "shots": (qc.get("stats") or {}).get("shots"),
        "elapsed_sec": round(time.time() - started, 1),
        "qc_failed": [k for k, v in (qc.get("checks") or {}).items()
                      if not v.get("ok")] if isinstance(qc, dict) else [],
    }


def contact_sheet(rows: list[dict]) -> str:
    frames = []
    for row in rows:
        out_dir = OUT_ROOT / row["id"]
        sheet = out_dir / "contact_sheet.png"
        if sheet.is_file():
            frames.append(sheet)
    if not frames:
        return ""
    try:
        from PIL import Image
        imgs = [Image.open(p).convert("RGB") for p in frames]
        w = 480
        h = int(w * imgs[0].height / max(1, imgs[0].width))
        imgs = [img.resize((w, h)) for img in imgs]
        cols = 4
        rowsn = (len(imgs) + cols - 1) // cols
        canvas = Image.new("RGB", (cols * w, rowsn * h), (16, 16, 20))
        for i, img in enumerate(imgs):
            canvas.paste(img, ((i % cols) * w, (i // cols) * h))
        out = OUT_ROOT / "contact_sheet_all.png"
        canvas.save(out)
        return str(out)
    except Exception as exc:  # noqa: BLE001
        LOG.warning("总览 contact sheet 失败: %s", exc)
        return ""


def run_batch(args) -> int:
    templates = sorted(p.stem for p in TEMPLATE_DIR.glob("*.json"))
    if not args.include_custom:
        templates = [t for t in templates if not t.startswith("custom_")]
    if args.only:
        wanted = [x.strip() for x in args.only.split(",") if x.strip()]
        templates = [t for t in templates if t in wanted]
    if args.list:
        print(json.dumps({"templates": templates}, ensure_ascii=False))
        return 0
    OUT_ROOT.mkdir(parents=True, exist_ok=True)
    results: list[dict] = []
    for i, tid in enumerate(templates, 1):
        out_dir = OUT_ROOT / tid
        if completed(out_dir) and not args.force:
            qc = _read_json(out_dir / "qc_report.json") or {}
            results.append({"id": tid, "ok": True, "skipped": True,
                            "style": (qc.get("stats") or {}).get("style"),
                            "duration": (qc.get("stats") or {}).get("actual_duration")})
            LOG.info("[%d/%d] %s 已完成，跳过", i, len(templates), tid)
            continue
        LOG.info("[%d/%d] 渲染 %s ...", i, len(templates), tid)
        if args.refresh:
            os.environ["V5_REUSE_TTS"] = "1"
            args.force = True
        else:
            os.environ.pop("V5_REUSE_TTS", None)
        results.append(render_one(tid, OUT_ROOT, args))
    ok = all(r.get("ok") for r in results)
    sheet = contact_sheet([r for r in results if r.get("ok")])
    report = {
        "ok": ok, "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "duration": args.duration, "total": len(results),
        "passed": sum(1 for r in results if r.get("ok")),
        "templates": results, "contact_sheet": sheet,
    }
    utils.write_json(REPORT, report)
    print(json.dumps(report, ensure_ascii=False))
    return 0 if ok else 1


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="python scripts/render_templates_v5.py")
    ap.add_argument("--duration", type=float, default=38.0, help="目标时长（秒）")
    ap.add_argument("--only", default=None, help="只渲染指定模板（逗号分隔）")
    ap.add_argument("--seed", type=int, default=20261101)
    ap.add_argument("--force", action="store_true", help="忽略已完成重新渲染")
    ap.add_argument("--include-custom", action="store_true",
                    help="把 contract/v5/templates/custom_*.json 也纳入批量（默认跳过）")
    ap.add_argument("--refresh", action="store_true",
                    help="重渲视觉但复用已缓存 TTS（不占 GPU，只有缺缓存时才合成）")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--list", action="store_true", help="只列出模板")
    ap.add_argument("-v", "--verbose", action="store_true")
    return ap


def main(argv=None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass
    args = build_parser().parse_args(argv)
    utils.setup_logging(bool(args.verbose))
    return run_batch(args)


if __name__ == "__main__":
    sys.exit(main())
