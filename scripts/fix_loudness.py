# -*- coding: utf-8 -*-
"""批量修复已落盘成片的响度（V4 硬门：-17~-15 LUFS / TP ≤ -1.0 dBTP）。

纯 CPU：不占 GPU、不重跑 H3/TTS；修复后重评 QC 并回写 qc_report.json。

两条路径（优先 1）：
1) **workdir 重建**（最准）：目录含 `work/episode.json` + `shots/*.mp4` +
   `work/video_raw`（或可由 shots 重建）时，复用 `render_v3.assemble_video`
   走生产同款剪辑/混音，内部两遍 loudnorm + 编码后闭环校正；
2) **仅音频重封装**（兜底）：只有 final.mp4 时，用 `render.enforce_loudness`
   逐轮动态 loudnorm 重编码音频（视频流 copy）。

用法（Lead 批量后统一跑）：
  python scripts/fix_loudness.py --root outputs/templates_v3            # 只扫描（不发修复）
  python scripts/fix_loudness.py --root outputs/templates_v3 --fix      # 修复不达标成片
  python scripts/fix_loudness.py --templates classics,history_mystery --fix
  python scripts/fix_loudness.py --root outputs/templates_v3 --fix --audio-only
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from pipeline import (config, qc_v3, render, render_v3, style_v2,  # noqa: E402
                      template_v3, utils)

LOG = utils.LOG
DEFAULT_ROOT = REPO_ROOT / "outputs" / "templates_v3"
DEFAULT_TEMPLATE_DIR = REPO_ROOT / "contract" / "templates_v3"


class NoopProgress:
    def __call__(self, *args, **kwargs):
        return None


def template_ids(template_dir: Path) -> list[str]:
    return sorted(template_v3.load_all(template_dir))


def is_rendering(out_dir: Path) -> bool:
    """同目录 render_v3 正在渲染（存在锁）时跳过，避免覆盖。"""
    return (out_dir / render_v3.LOCK_NAME).is_file()


def probe_loudness(video: Path) -> dict:
    info = qc_v3.measure_loudness(video)
    if not info or not info.get("ok"):
        return {"ok": False, "in_range": False,
                "error": str((info or {}).get("error") or "measure failed")}
    low, high = qc_v3.LOUDNESS_RANGE
    in_range = low <= info["i"] <= high and info["tp"] <= qc_v3.LOUDNESS_TP_MAX
    return {"ok": True, "lufs": round(info["i"], 2), "tp": round(info["tp"], 2),
            "lra": round(info["lra"], 2), "in_range": in_range}


def _backup(final: Path) -> str:
    bak = final.with_name(final.stem + ".preloud" + final.suffix)
    if not bak.is_file():
        shutil.copy2(final, bak)
    return str(bak)


def rebuild_from_workdir(out_dir: Path, template_id: str,
                         template: dict) -> Path | None:
    """用 work/episode.json + shots 走 render_v3.assemble_video 重建成片（生产同款链路）。

    返回重建文件路径（final.rebuild.mp4），失败抛异常。
    """
    workdir = out_dir / "work"
    episode = utils.read_json(workdir / "episode.json")
    if not isinstance(episode, dict):
        raise RuntimeError("work/episode.json 缺失/非法")
    shots_dir = out_dir / "shots"
    if not shots_dir.is_dir() or not any(shots_dir.glob("*.mp4")):
        raise RuntimeError("shots/ 缺失（无法重建视频）")
    manifest = utils.read_json(workdir / "manifest.json") or {}
    profile = style_v2.profile_for(template_id,
                                   override=str(template.get("style") or "") or None)
    rebuilt = out_dir / "final.rebuild.mp4"
    paths = {
        "out": rebuilt,
        "out_dir": out_dir,
        "workdir": workdir,
        "shots_dir": shots_dir,
        "frames_dir": out_dir / "frames",
        "h3_dir": workdir / "h3",
    }
    duck = manifest.get("duck_bgm")
    duck_bgm = True if duck is None else bool(duck)
    info = render_v3.assemble_video(
        episode, template, paths, template_id, NoopProgress(),
        profile=profile, mode="comfy", duck_bgm=duck_bgm)
    LOG.info("[%s] 重建完成：%s（%.2fs）", template_id, rebuilt,
             info.get("total_duration") or 0.0)
    return rebuilt


def audio_only_fix(final: Path, max_rounds: int = 3) -> dict:
    """仅重编码音频（视频 copy）：用 render.enforce_loudness 闭环修正。"""
    before = probe_loudness(final)
    info = render.enforce_loudness(final, max_rounds=max_rounds)
    after = probe_loudness(final)
    return {"before": before, "after": after, "ok": bool(after.get("in_range"))}


def re_eval_qc(out_dir: Path, template_id: str, video: Path) -> dict:
    """用 qc_v3 全量重评并回写 qc_report.json（含 loudness 硬门）。"""
    workdir = out_dir / "work"
    episode = utils.read_json(workdir / "episode.json")
    h3_meta = utils.read_json(out_dir / "h3_meta.json") or {}
    shot_metas = {str(m.get("id") or m.get("shot_id")): m
                  for m in (h3_meta.get("shots") or []) if isinstance(m, dict)}
    template = template_v3.load(template_id)
    old = utils.read_json(out_dir / "qc_report.json") or {}
    previs = bool(old.get("previs")) or bool(h3_meta.get("previs"))
    report = qc_v3.evaluate(video=video, plan=episode, shot_metas=shot_metas,
                            template=template,
                            subtitles=workdir / "final.ass",
                            h3_require=not previs)
    qc_v3.write_report(report, out_dir / "qc_report.json")
    return report


def fix_one(out_dir: Path, template_id: str, *, audio_only: bool = False,
            force: bool = False, max_rounds: int = 3) -> dict:
    final = out_dir / "final.mp4"
    record: dict = {"id": template_id, "dir": str(out_dir)}
    if not final.is_file():
        record.update({"ok": False, "skipped": True, "reason": "final.mp4 缺失"})
        return record
    if is_rendering(out_dir):
        record.update({"ok": False, "skipped": True, "reason": "该目录正在渲染（存在 lock）"})
        return record
    before = probe_loudness(final)
    record["before"] = before
    if not before.get("ok"):
        record.update({"ok": False, "method": "none",
                       "reason": f"响度测量失败: {before.get('error')}"})
        return record
    if before.get("in_range") and not force:
        record.update({"ok": True, "skipped": True, "method": "skip",
                       "reason": "响度已达标"})
        return record

    template = template_v3.load(template_id)
    method = ""
    backup = ""
    rebuilt: Path | None = None
    if not audio_only:
        try:
            rebuilt = rebuild_from_workdir(out_dir, template_id, template)
            after = probe_loudness(rebuilt)
            if not after.get("in_range"):
                LOG.warning("[%s] 重建后仍不达标（%s），走音频兜底修正",
                            template_id, after)
                audio_only_fix(rebuilt, max_rounds)
                after = probe_loudness(rebuilt)
            if not after.get("in_range"):
                raise RuntimeError(f"重建+兜底修正后仍不达标: {after}")
            method = "rebuild"
            backup = _backup(final)
            os.replace(rebuilt, final)
            record["after"] = after
        except Exception as exc:  # noqa: BLE001
            LOG.warning("[%s] workdir 重建失败，回退仅音频修复: %s", template_id, exc)
            if rebuilt is not None:
                try:
                    rebuilt.unlink(missing_ok=True)
                except OSError:
                    pass
            rebuilt = None
    if not method:
        res = audio_only_fix(final, max_rounds=max_rounds)
        method = "audio_only"
        backup = _backup(final) if res.get("ok") else ""
        record["after"] = res.get("after")
        if not res.get("ok"):
            record.update({"ok": False, "method": method, "backup": backup,
                           "reason": "音频修复后仍不达标"})
            return record
    record.update({"ok": True, "method": method, "backup": backup,
                   "skipped": False})
    try:
        report = re_eval_qc(out_dir, template_id, final)
        record["qc_ok"] = bool(report.get("ok"))
        record["qc_failed_checks"] = [k for k, v in (report.get("checks") or {}).items()
                                      if not v.get("ok")]
        record["qc_lufs"] = (report.get("stats") or {}).get("lufs")
        record["qc_tp"] = (report.get("stats") or {}).get("true_peak")
    except Exception as exc:  # noqa: BLE001
        LOG.warning("[%s] 修复后 QC 重评失败: %s", template_id, exc)
        record["qc_ok"] = False
        record["qc_error"] = str(exc)
    return record


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="python scripts/fix_loudness.py",
        description="批量修复成片响度（V4 硬门 -17~-15 LUFS / TP≤-1.0；CPU，不占 GPU）")
    ap.add_argument("--root", default=str(DEFAULT_ROOT),
                    help="成片根目录（默认 outputs/templates_v3）")
    ap.add_argument("--templates-dir", default=str(DEFAULT_TEMPLATE_DIR))
    ap.add_argument("--templates", default=None, help="只处理指定模板，逗号分隔")
    ap.add_argument("--fix", action="store_true", help="执行修复（默认只扫描测量）")
    ap.add_argument("--audio-only", action="store_true",
                    help="跳过 workdir 重建，只重编码音频（视频 copy）")
    ap.add_argument("--force", action="store_true", help="已达标也重做")
    ap.add_argument("--include-v3", action="store_true",
                    help="也处理无 timeline.json 的旧 V3 产物（默认跳过，等 V4 批量）")
    ap.add_argument("--max-rounds", type=int, default=3,
                    help="仅音频修复时的闭环轮数（默认 3）")
    ap.add_argument("-v", "--verbose", action="store_true")
    return ap


def main(argv: list[str] | None = None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass
    args = build_parser().parse_args(argv)
    utils.setup_logging(bool(args.verbose))
    t0 = time.time()
    root = Path(args.root).resolve()
    template_dir = Path(args.templates_dir)
    ids = template_ids(template_dir)
    if args.templates:
        wanted = [x.strip() for x in args.templates.split(",") if x.strip()]
        unknown = [x for x in wanted if x not in ids]
        if unknown:
            print(json.dumps({"ok": False,
                              "error": f"未知模板 {unknown}；可用 {ids}"},
                             ensure_ascii=False))
            return 2
        ids = [tid for tid in ids if tid in set(wanted)]
    dirs = [(tid, root / tid) for tid in ids if (root / tid).is_dir()]
    if not dirs:
        print(json.dumps({"ok": False, "error": f"没有可处理目录: {root}"},
                         ensure_ascii=False))
        return 1
    results = []
    for tid, out_dir in dirs:
        is_v4 = (out_dir / "timeline.json").is_file()
        if not is_v4 and not args.include_v3:
            record = {"id": tid, "ok": True, "skipped": True,
                      "reason": "非 V4 产物（无 timeline.json），等 V4 批量渲染后再修"}
            results.append(record)
            LOG.info("[%s] 跳过：%s", tid, record["reason"])
            continue
        if args.fix:
            record = fix_one(out_dir, tid, audio_only=args.audio_only,
                             force=args.force, max_rounds=args.max_rounds)
        else:
            final = out_dir / "final.mp4"
            if not final.is_file():
                record = {"id": tid, "ok": False, "skipped": True,
                          "reason": "final.mp4 缺失"}
            else:
                info = probe_loudness(final)
                record = {"id": tid, **info,
                          "skipped": False,
                          "needs_fix": not info.get("in_range")}
        results.append(record)
        LOG.info("[%s] %s", tid, json.dumps(record, ensure_ascii=False))
    needs = [r["id"] for r in results
             if not r.get("skipped") and not r.get("ok")]
    issues = [r["id"] for r in results
              if (r.get("needs_fix") or (not r.get("ok") and not r.get("skipped")))]
    report = {
        "ok": (not needs) if args.fix else True,
        "mode": "fix" if args.fix else "scan",
        "root": str(root),
        "audio_only": bool(args.audio_only),
        "elapsed_sec": round(time.time() - t0, 1),
        "total": len(results),
        "fixed": [r["id"] for r in results
                  if args.fix and r.get("ok") and not r.get("skipped")],
        "skipped_ok": [r["id"] for r in results
                       if r.get("skipped") and r.get("ok")],
        "failed": needs,
        "issues": issues,
        "in_range": [r["id"] for r in results
                     if r.get("in_range") or (r.get("skipped") and r.get("ok")
                                              and r.get("reason") == "响度已达标")],
        "results": results,
    }
    utils.write_json(root / "loudness_fix_report.json", report)
    summary = {k: v for k, v in report.items() if k != "results"}
    print(json.dumps(summary, ensure_ascii=False))
    if args.fix:
        return 0 if not needs else 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
