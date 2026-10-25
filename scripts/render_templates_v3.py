# -*- coding: utf-8 -*-
"""批量渲染 16 个 v3 模板（GPU 串行 + 断点续跑 + batch_report + contact sheet）。

用法（仓库根目录）：
  python scripts/render_templates_v3.py --duration 30 --h3 comfy
  python scripts/render_templates_v3.py --only tonight_show,history_talk --h3 previs
  python scripts/render_templates_v3.py --force --duration 45

规则：
- 模板按 id 排序串行渲染（H3 是 GPU 串行任务，禁止并发）；
- 断点续跑：outputs/templates_v3/{id}/qc_report.json 的 ok=true 且 final.mp4 存在则 skip；
  --force 强制重跑；
- 产物：outputs/templates_v3/{id}/{final.mp4,shots/,frames/contact_sheet.png,h3_meta.json,qc_report.json,work/}；
- 汇总：outputs/templates_v3/batch_report.json + 16 模板总 contact sheet（outputs/templates_v3/contact_sheet.png）。
- stdout 最后一行是 JSON 汇总；失败模板不影响后续模板（--stop-on-error 可改为立即停）。
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from pipeline import config, template_v3, utils  # noqa: E402

LOG = utils.LOG
DEFAULT_OUT_ROOT = REPO_ROOT / "outputs" / "templates_v3"
DEFAULT_TEMPLATE_DIR = REPO_ROOT / "contract" / "templates_v3"
MASTER_COLS = 4
THUMB_W, THUMB_H = 320, 180


# ---------------------------------------------------------------------------
# 续跑判断
# ---------------------------------------------------------------------------
def completed(out_dir: Path, qc_ok_only: bool = True,
              require_delivery: bool = False) -> bool:
    """已有产物可用（qc_report ok 且 final.mp4 存在）。

    require_delivery=True（comfy 批量）时，previs 预览产物不算完成——
    否则先跑过 previs 会让 comfy 批量误跳过。
    """
    final = out_dir / "final.mp4"
    if not final.is_file() or final.stat().st_size < 10000:
        return False
    report = utils.read_json(out_dir / "qc_report.json")
    if not qc_ok_only:
        return True
    if not isinstance(report, dict) or not report.get("ok"):
        return False
    if require_delivery:
        # comfy 批量只认交付态：previs 预览 / deliverable=false / 有静态兜底 → 都要重跑
        if report.get("previs"):
            return False
        if report.get("deliverable") is not True:
            return False
        if report.get("fallbacks"):
            return False
    return True


def _read_json(path: Path):
    data = utils.read_json(path)
    return data if isinstance(data, dict) else {}


# ---------------------------------------------------------------------------
# 总 contact sheet
# ---------------------------------------------------------------------------
def build_master_sheet(out_root: Path, items: list[dict]) -> str:
    """把每个模板的 contact_sheet 拼成 4x4 总览图（带模板 id 标签）。"""
    from PIL import Image, ImageDraw, ImageFont
    sheets = [(item["id"], Path(item["contact_sheet"])) for item in items
              if item.get("contact_sheet") and Path(item["contact_sheet"]).is_file()]
    if not sheets:
        return ""
    cols = MASTER_COLS
    rows = (len(sheets) + cols - 1) // cols
    pad = 8
    label_h = 24
    cell_w = THUMB_W + pad * 2
    cell_h = THUMB_H + label_h + pad * 2
    canvas = Image.new("RGB", (cols * cell_w, rows * cell_h), (14, 14, 18))
    draw = ImageDraw.Draw(canvas)
    try:
        font = ImageFont.load_default()
    except Exception:  # noqa: BLE001
        font = None
    for i, (tid, path) in enumerate(sheets):
        r, c = divmod(i, cols)
        x0 = c * cell_w + pad
        y0 = r * cell_h + pad
        try:
            img = Image.open(path).convert("RGB")
            img.thumbnail((THUMB_W, THUMB_H), Image.LANCZOS)
            canvas.paste(img, (x0, y0))
        except Exception as exc:  # noqa: BLE001
            LOG.warning("总 contact sheet 读取失败 %s: %s", path, exc)
        if font is not None:
            draw.text((x0, y0 + THUMB_H + 4), str(tid), font=font,
                      fill=(240, 220, 140))
    out = out_root / "contact_sheet.png"
    utils.ensure_dir(out.parent)
    canvas.save(out)
    return str(out)


# ---------------------------------------------------------------------------
# 单模板
# ---------------------------------------------------------------------------
def render_one(template_id: str, out_root: Path, ns_base) -> dict:
    from pipeline import render_v3
    out_dir = out_root / template_id
    started = time.time()
    args = argparse.Namespace(
        template=template_id,
        duration=ns_base.duration,
        h3=ns_base.h3,
        out=str(out_dir / "final.mp4"),
        workdir=str(out_dir / "work"),
        seed=ns_base.seed,
        force=ns_base.force,
        limit=ns_base.limit,
        fallback_static=ns_base.fallback_static,
        width=1280,
        height=720,
    )
    error = ""
    try:
        render_v3.run_render(args)
    except Exception as exc:  # noqa: BLE001
        error = str(exc)
        LOG.error("[%s] 渲染异常: %s", template_id, exc)
    qc = _read_json(out_dir / "qc_report.json")
    h3_meta = _read_json(out_dir / "h3_meta.json")
    final = out_dir / "final.mp4"
    probe = {}
    if final.is_file():
        try:
            probe = utils.probe_video(final)
        except Exception:  # noqa: BLE001
            probe = {}
    item = {
        "id": template_id,
        "mode": str(getattr(ns_base, "h3", "")),
        "status": "rendered" if final.is_file() else "failed",
        "ok": bool(qc.get("ok")),
        "deliverable": bool(qc.get("deliverable")),
        "previs": bool(qc.get("previs")),
        "path": str(final) if final.is_file() else "",
        "duration": probe.get("duration") or qc.get("stats", {}).get("actual_duration"),
        "resolution": (f"{probe.get('width')}x{probe.get('height')}"
                       if probe else ""),
        "fps": probe.get("fps"),
        "size_bytes": final.stat().st_size if final.is_file() else 0,
        "shots": qc.get("stats", {}).get("shots", h3_meta.get("shots_count")),
        "h3_shots": qc.get("stats", {}).get("h3_shots", h3_meta.get("h3_shots")),
        "h3_generated": h3_meta.get("stats", {}).get("h3"),
        "audio_silent_shots": sum(1 for m in h3_meta.get("shots") or []
                                  if m.get("audio_silent")),
        "fallbacks": qc.get("stats", {}).get("fallbacks", 0),
        "qc_ok": bool(qc.get("ok")),
        "qc_failed_checks": [k for k, v in (qc.get("checks") or {}).items()
                             if not v.get("ok")],
        "subtitle_coverage": qc.get("stats", {}).get("subtitle_coverage"),
        "black_segments": qc.get("stats", {}).get("black_segments"),
        "contact_sheet": str(out_dir / "frames" / "contact_sheet.png")
        if (out_dir / "frames" / "contact_sheet.png").is_file() else "",
        "qc_report": str(out_dir / "qc_report.json"),
        "h3_meta": str(out_dir / "h3_meta.json") if (out_dir / "h3_meta.json").is_file() else "",
        "elapsed_sec": round(time.time() - started, 1),
        "error": error,
    }
    if not qc and not error:
        item["error"] = "缺少 qc_report.json"
    return item


def entry_bad(result: dict, comfy: bool = True) -> bool:
    """批量失败判定：comfy 模式下 deliverable=false / 有静态兜底也算失败。"""
    if result.get("status") == "failed" or not result.get("ok"):
        return True
    if comfy:
        if result.get("deliverable") is not True:
            return True
        if int(result.get("fallbacks") or 0) > 0:
            return True
    return False


def load_template_order(template_dir: Path, only: list[str] | None) -> list[dict]:
    templates = template_v3.load_all(template_dir)
    items = []
    for tid in sorted(templates):
        tpl = templates[tid]
        items.append({"id": tid, "label": str(tpl.get("label") or tid),
                      "style": str(tpl.get("style") or ""),
                      "plate": str((tpl.get("stage") or {}).get("plate") or "")})
    if only:
        wanted = [x.strip() for x in only if x.strip()]
        known = {i["id"] for i in items}
        missing = [x for x in wanted if x not in known]
        if missing:
            raise SystemExit(f"--only 里有不存在的模板: {missing}（可用: {sorted(known)}）")
        items = [i for i in items if i["id"] in wanted]
    return items


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------
def run_batch(args) -> int:
    t_start = time.time()
    template_dir = Path(args.templates_dir or DEFAULT_TEMPLATE_DIR)
    out_root = Path(args.out_root or DEFAULT_OUT_ROOT).resolve()
    utils.ensure_dir(out_root)
    only = (args.only or "").split(",") if args.only else None
    items = load_template_order(template_dir, only)
    if not items:
        utils.emit_json_line({"ok": False, "error": f"没有可渲染模板: {template_dir}"})
        return 1
    LOG.info("v3 批量渲染: %d 个模板 / duration=%ss / h3=%s / force=%s",
             len(items), args.duration, args.h3, args.force)

    results: list[dict] = []
    for idx, item in enumerate(items, start=1):
        tid = item["id"]
        out_dir = out_root / tid
        if not args.force and completed(out_dir, qc_ok_only=not args.no_resume_qc,
                                       require_delivery=(args.h3 == "comfy")):
            report = _read_json(out_dir / "qc_report.json")
            contact = out_dir / "frames" / "contact_sheet.png"
            LOG.info("[%d/%d] %s 已完成（QC %s），跳过（--force 可重跑）",
                     idx, len(items), tid, "PASS" if report.get("ok") else "FAIL")
            results.append({
                **item, "status": "skipped", "mode": args.h3,
                "ok": bool(report.get("ok")),
                "deliverable": bool(report.get("deliverable")),
                "previs": bool(report.get("previs")),
                "qc_ok": bool(report.get("ok")), "path": str(out_dir / "final.mp4"),
                "duration": report.get("stats", {}).get("actual_duration"),
                "contact_sheet": str(contact) if contact.is_file() else "",
                "qc_report": str(out_dir / "qc_report.json"),
                "fallbacks": report.get("stats", {}).get("fallbacks", 0),
                "black_segments": report.get("stats", {}).get("black_segments"),
                "subtitle_coverage": report.get("stats", {}).get("subtitle_coverage"),
                "shots": report.get("stats", {}).get("shots"),
                "elapsed_sec": 0.0, "error": "",
            })
            continue
        LOG.info("[%d/%d] %s 开始渲染（%s %.0fs）", idx, len(items), tid,
                 args.h3, args.duration)
        result = render_one(tid, out_root, args)
        result.update({"label": item["label"], "style": item["style"],
                       "plate": item["plate"]})
        results.append(result)
        LOG.info("[%d/%d] %s → %s（qc_ok=%s fallbacks=%s %.1fs）",
                 idx, len(items), tid, result["status"], result["qc_ok"],
                 result.get("fallbacks"), result["elapsed_sec"])
        if args.stop_on_error and entry_bad(result, args.h3 == "comfy"):
            LOG.error("--stop-on-error：%s 失败，终止批量", tid)
            break

    elapsed = round(time.time() - t_start, 1)
    rendered = [r for r in results if r.get("status") == "rendered"]
    skipped = [r for r in results if r.get("status") == "skipped"]
    comfy = args.h3 == "comfy"
    failed = [r for r in results if entry_bad(r, comfy)]
    master = ""
    if not args.no_master_sheet:
        master = build_master_sheet(out_root, results)
    deliverable_count = sum(1 for r in results if r.get("deliverable") is True)
    report = {
        "ok": not failed and len(results) == len(items),
        "h3": args.h3,
        "comfy": comfy,
        "duration": args.duration,
        "seed": args.seed,
        "force": bool(args.force),
        "started_at": datetime.now().isoformat(timespec="seconds"),
        "elapsed_sec": elapsed,
        "total": len(items),
        "rendered": len(rendered),
        "skipped": len(skipped),
        "failed": len(failed),
        "deliverable": deliverable_count,
        "deliverable_ids": [r.get("id") for r in results
                            if r.get("deliverable") is True],
        "failed_ids": [r.get("id") for r in failed],
        "contact_sheet": master,
        "templates": results,
    }
    utils.write_json(out_root / "batch_report.json", report)
    summary = {k: v for k, v in report.items() if k != "templates"}
    summary["templates"] = [{k: r.get(k) for k in
                             ("id", "mode", "status", "ok", "deliverable",
                              "duration", "fallbacks", "black_segments",
                              "qc_failed_checks", "contact_sheet")}
                            for r in results]
    print(json.dumps(summary, ensure_ascii=False))
    return 0 if report["ok"] else 1


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="python scripts/render_templates_v3.py",
        description="批量渲染 16 个 v3 模板（GPU 串行 + 断点续跑）")
    ap.add_argument("--duration", type=float, default=30.0,
                    help="每个模板时长（30-45 demo / 300 五分钟版）")
    ap.add_argument("--h3", default="comfy", choices=["comfy", "previs"],
                    help="comfy=真跑 H3；previs=静态构图预览（不算交付）")
    ap.add_argument("--seed", type=int, default=20261026)
    ap.add_argument("--only", default=None, help="只跑指定模板，逗号分隔")
    ap.add_argument("--templates-dir", default=str(DEFAULT_TEMPLATE_DIR))
    ap.add_argument("--out-root", default=str(DEFAULT_OUT_ROOT))
    ap.add_argument("--force", action="store_true", help="忽略断点续跑，全部重跑")
    ap.add_argument("--limit", type=int, default=None, help="每模板只渲染前 N 镜（调试）")
    ap.add_argument("--fallback-static", action="store_true", default=False,
                    help="H3 失败镜回退静态（QC 标红，验收不接受）")
    ap.add_argument("--stop-on-error", action="store_true", default=False)
    ap.add_argument("--no-resume-qc", action="store_true", default=False,
                    help="续跑判断放宽为 final.mp4 存在（默认要求 qc_report ok）")
    ap.add_argument("--no-master-sheet", action="store_true", default=False)
    ap.add_argument("-v", "--verbose", action="store_true")
    return ap


def main(argv: list[str] | None = None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass
    args = build_parser().parse_args(argv)
    utils.setup_logging(bool(args.verbose))
    try:
        return run_batch(args)
    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001
        LOG.error("批量渲染失败: %s", exc)
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    sys.exit(main())
