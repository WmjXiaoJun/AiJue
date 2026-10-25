# -*- coding: utf-8 -*-
"""qc_v3：v3 成片 QC 门（时长/分辨率/帧率/音轨/黑帧/字幕/H3 覆盖/可追溯/静态标记）。

docs/V3_INTERFACES.md §6：
  checks：duration（±0.6s）/ resolution 1280x720 / fps 25 / audio / blackdetect=0 /
          subtitle_coverage / h3_coverage（非卡片镜 100%）/ traceability
          （workflow∈{04,05}、prompt_id 非空唯一、seed 存在）/ static_marked /
          stage_meta（每镜 ref_image 存在）；
  ok = 全部 check ok；fallbacks 列出静态兜底镜（验收不接受非卡片静态兜底）。

用法（脚本内）：
  from pipeline import qc_v3
  report = qc_v3.evaluate(video=..., plan=..., shot_metas=..., template=...,
                          subtitles=workdir / "final.ass")
"""
from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path

from . import config, utils

CARD_TYPES = ("title_card", "end_card", "meme_card")
H3_WORKFLOWS = {"04_i2v_fl2v_4step_sla", "05_ref2va_4step_sla"}
WORKFLOW_PREFIXES = ("04", "05")
EXPECT_WIDTH = 1280
EXPECT_HEIGHT = 720
EXPECT_FPS = 25
DURATION_TOLERANCE = 0.6
SUBTITLE_MIN_COVERAGE = 0.6
BLACKDETECT_VF = "blackdetect=d=0.1:pix_th=0.10:pic_th=0.98"
NON_SUBTITLE_STYLES = {"chapter", "punch", "lower", "brand"}


# ---------------------------------------------------------------------------
# 基础
# ---------------------------------------------------------------------------
def _check(ok: bool, detail: str = "", **extra) -> dict:
    item = {"ok": bool(ok), "detail": detail}
    item.update(extra)
    return item


def plan_shots(plan: dict | list | None) -> list[dict]:
    if isinstance(plan, list):
        return list(plan)
    if isinstance(plan, dict):
        shots = plan.get("shots")
        return list(shots) if isinstance(shots, list) else []
    return []


def plan_duration(plan: dict | list | None) -> float:
    return round(sum(float(s.get("duration") or 0.0) for s in plan_shots(plan)), 3)


def shot_timings(plan: dict | list | None) -> dict:
    """{sid: {"shot","start","end","duration"}}，按 shots 顺序累计。"""
    out: dict[str, dict] = {}
    cursor = 0.0
    for shot in plan_shots(plan):
        dur = float(shot.get("duration") or 0.0)
        sid = str(shot.get("id") or f"shot_{len(out)}")
        out[sid] = {"shot": shot, "start": round(cursor, 3),
                    "end": round(cursor + dur, 3), "duration": dur}
        cursor += dur
    return out


def is_card(shot: dict) -> bool:
    return str(shot.get("type") or "") in CARD_TYPES or bool(shot.get("static_card"))


def non_card_shots(plan) -> list[dict]:
    return [s for s in plan_shots(plan) if not is_card(s)]


def dialogue_shots(plan) -> list[dict]:
    return [s for s in non_card_shots(plan) if str(s.get("line") or "").strip()]


def _probe_video(video: Path) -> dict:
    try:
        return utils.probe_video(video)
    except Exception as exc:  # noqa: BLE001
        return {"width": 0, "height": 0, "fps": 0.0, "duration": 0.0,
                "codec": "", "has_audio": False, "error": str(exc)}


def blackdetect_count(video: Path | str, timeout: float = 600) -> tuple[int, str]:
    """ffmpeg blackdetect 检测纯黑段；返回 (段数, stderr 摘要)。"""
    args = [config.FFMPEG_BIN, "-hide_banner", "-nostats", "-i", str(video),
            "-vf", BLACKDETECT_VF, "-an", "-f", "null", os.devnull]
    try:
        proc = utils.run(args, check=False, timeout=timeout)
    except Exception as exc:  # noqa: BLE001
        return -1, f"blackdetect 执行失败: {exc}"
    err = proc.stderr or ""
    starts = re.findall(r"black_start:([0-9.]+)", err)
    return len(starts), err[-600:]


# ---------------------------------------------------------------------------
# 字幕事件
# ---------------------------------------------------------------------------
ASS_TIME_RE = re.compile(r"^(\d+):(\d+):(\d+)[.:](\d+)$")
ASS_TAG_RE = re.compile(r"\{[^}]*\}")


def _ass_seconds(text: str) -> float:
    m = ASS_TIME_RE.match(str(text or "").strip())
    if not m:
        return 0.0
    h, mm, ss, frac = m.groups()
    return int(h) * 3600 + int(mm) * 60 + int(ss) + int(frac.ljust(3, "0")[:3]) / 1000.0


def _parse_ass(path: Path) -> list[dict]:
    events: list[dict] = []
    try:
        text = Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return events
    for raw in text.splitlines():
        if not raw.startswith("Dialogue:"):
            continue
        parts = raw[len("Dialogue:"):].lstrip().split(",", 9)
        if len(parts) < 10:
            continue
        layer, start, end, style = parts[0], parts[1], parts[2], parts[3]
        body = ASS_TAG_RE.sub("", parts[9]).strip()
        events.append({"start": _ass_seconds(start), "end": _ass_seconds(end),
                       "style": style.strip(), "text": body})
    return events


def subtitle_events(subtitles=None) -> list[dict]:
    """subtitles 可为：.ass 路径 / ass_builder 返回 dict / 事件 list / None。"""
    if subtitles is None:
        return []
    if isinstance(subtitles, (str, Path)):
        return _parse_ass(Path(subtitles))
    if isinstance(subtitles, dict):
        for key in ("path", "ass_path"):
            p = subtitles.get(key)
            if p:
                events = _parse_ass(Path(p))
                if events:
                    return events
        raw = subtitles.get("events")
        if isinstance(raw, list):
            subtitles = raw
        elif isinstance(raw, (str, Path)):
            return _parse_ass(Path(raw))
        else:
            return []
    if isinstance(subtitles, list):
        out = []
        for item in subtitles:
            if isinstance(item, dict):
                out.append({"start": float(item.get("start") or 0.0),
                            "end": float(item.get("end") or 0.0),
                            "style": str(item.get("style") or "Sub"),
                            "text": str(item.get("text") or "")})
            elif isinstance(item, (list, tuple)) and len(item) >= 3:
                start, end = float(item[0]), float(item[1])
                style = str(item[2]) if len(item) < 4 else str(item[3])
                text = str(item[-1])
                out.append({"start": start, "end": end, "style": style, "text": text})
        return out
    return []


def _overlap(a0: float, a1: float, b0: float, b1: float) -> float:
    return max(0.0, min(a1, b1) - max(a0, b0))


def subtitle_coverage(plan, subtitles=None) -> tuple[float, list[str], int]:
    """对话镜字幕覆盖率（每镜窗口被非空字幕事件覆盖的比例，取最小）。"""
    shots = dialogue_shots(plan)
    if not shots:
        return 1.0, [], 0
    events = [e for e in subtitle_events(subtitles)
              if e["end"] > e["start"] and e["text"].strip()
              and e["style"].strip().lower() not in NON_SUBTITLE_STYLES]
    timings = shot_timings(plan)
    if not events:
        return 0.0, [str(s.get("id")) for s in shots], len(shots)
    missing: list[str] = []
    min_ratio = 1.0
    for shot in shots:
        sid = str(shot.get("id"))
        info = timings.get(sid) or {"start": 0.0, "end": float(shot.get("duration") or 0.0)}
        start = info["start"] + 0.10
        end = max(start, info["end"] - 0.05)
        span = max(0.1, end - start)
        covered = sum(_overlap(start, end, e["start"], e["end"]) for e in events)
        ratio = min(1.0, covered / span)
        min_ratio = min(min_ratio, ratio)
        if ratio < SUBTITLE_MIN_COVERAGE:
            missing.append(sid)
    return round(min_ratio, 3), missing, len(shots)


# ---------------------------------------------------------------------------
# H3 证据
# ---------------------------------------------------------------------------
def _norm_mode(value) -> str:
    return str(value or "").strip().lower()


def _workflow_ok(workflow) -> bool:
    wf = str(workflow or "").strip()
    if wf in H3_WORKFLOWS:
        return True
    return any(wf.startswith(p + "_") for p in WORKFLOW_PREFIXES)


def _seed_present(meta: dict) -> bool:
    seed = (meta or {}).get("seed")
    if seed is None:
        return False
    return str(seed).strip() != ""


def _ref_image_exists(meta: dict) -> bool:
    ref = str((meta or {}).get("ref_image") or "").strip()
    return bool(ref) and Path(ref).is_file()


def _meta_of(shot_metas: dict | None, sid: str) -> dict:
    if not isinstance(shot_metas, dict):
        return {}
    meta = shot_metas.get(sid)
    return meta if isinstance(meta, dict) else {}


# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------
def evaluate(*, video, plan, shot_metas, template, subtitles=None,
             h3_require: bool = True) -> dict:
    """v3 QC；返回 {ok, checks, fallbacks, stats}（并标注 previs）。"""
    t0 = time.time()
    video = Path(video) if video else Path("__missing__.mp4")
    template_id = str((template or {}).get("id") or "")
    shots = plan_shots(plan)
    cards = [s for s in shots if is_card(s)]
    bodies = [s for s in shots if not is_card(s)]
    expected = plan_duration(plan)
    probe = _probe_video(video) if video.is_file() else {
        "width": 0, "height": 0, "fps": 0.0, "duration": 0.0,
        "codec": "", "has_audio": False, "error": "成片不存在"}
    episode = (plan or {}).get("episode") if isinstance(plan, dict) else {}
    episode = episode if isinstance(episode, dict) else {}
    width, height = EXPECT_WIDTH, EXPECT_HEIGHT
    res_text = str(episode.get("resolution") or "")
    if "x" in res_text:
        try:
            w, h = res_text.lower().split("x", 1)
            width, height = int(w), int(h)
        except ValueError:
            width, height = EXPECT_WIDTH, EXPECT_HEIGHT

    checks: dict[str, dict] = {}

    # 1) 时长
    actual_duration = float(probe.get("duration") or 0.0)
    delta = round(actual_duration - expected, 3)
    checks["duration"] = _check(
        abs(delta) <= DURATION_TOLERANCE,
        f"成片 {actual_duration:.2f}s / 计划 {expected:.2f}s（差 {delta:+.2f}s，容差 ±{DURATION_TOLERANCE}s）",
        expected=expected, actual=actual_duration, delta=delta)

    # 2) 分辨率
    checks["resolution"] = _check(
        (probe.get("width"), probe.get("height")) == (width, height),
        f"{probe.get('width')}x{probe.get('height')}（期望 {width}x{height}）",
        actual=f"{probe.get('width')}x{probe.get('height')}",
        expected=f"{width}x{height}")

    # 3) 帧率
    fps = float(probe.get("fps") or 0.0)
    checks["fps"] = _check(abs(fps - EXPECT_FPS) <= 0.6,
                           f"{fps:.2f}fps（期望 {EXPECT_FPS}）", actual=fps)

    # 4) 音轨
    checks["audio"] = _check(bool(probe.get("has_audio")),
                             "有音轨" if probe.get("has_audio") else "缺少音轨")

    # 5) 黑帧
    black = -1
    black_detail = ""
    if video.is_file():
        black, black_detail = blackdetect_count(video)
    checks["blackdetect"] = _check(
        black == 0,
        "0 段纯黑" if black == 0 else f"检测到黑帧段 {black}（{black_detail[-180:]}）",
        black_segments=black)

    # 6) 字幕覆盖
    coverage, sub_missing, dialog_count = subtitle_coverage(plan, subtitles)
    checks["subtitle_coverage"] = _check(
        coverage >= SUBTITLE_MIN_COVERAGE,
        (f"对话镜 {dialog_count - len(sub_missing)}/{dialog_count} 有字幕"
         f"（最小覆盖 {coverage:.0%}）") if dialog_count else "无对话镜",
        coverage=coverage, missing=sub_missing, dialogue_shots=dialog_count)

    # 7) H3 覆盖（非卡片镜）
    metas = {str(s.get("id")): _meta_of(shot_metas, str(s.get("id"))) for s in bodies}
    h3_shots = [sid for sid, m in metas.items() if m.get("h3") is True]
    h3_ratio = round(len(h3_shots) / len(bodies), 3) if bodies else 1.0
    if h3_require:
        checks["h3_coverage"] = _check(
            len(h3_shots) == len(bodies),
            f"H3 非卡片镜 {len(h3_shots)}/{len(bodies)}",
            covered=len(h3_shots), total=len(bodies), ratio=h3_ratio,
            missing=[sid for sid in metas if sid not in h3_shots])
    else:
        checks["h3_coverage"] = _check(
            True, f"previs：未要求 H3（静态 {len(bodies)} 镜仅预览构图）",
            covered=len(h3_shots), total=len(bodies), ratio=h3_ratio, skipped=True)

    # 8) 可追溯（workflow/prompt_id/seed）
    workflow_bad, prompt_bad, seed_bad, prompt_ids = [], [], [], []
    for sid, meta in metas.items():
        if h3_require and meta.get("h3") is not True:
            continue
        if not _workflow_ok(meta.get("workflow")):
            workflow_bad.append(sid)
        pid = str(meta.get("prompt_id") or "").strip()
        if not pid:
            prompt_bad.append(sid)
        else:
            prompt_ids.append(pid)
        if not _seed_present(meta):
            seed_bad.append(sid)
    dup_prompts = sorted({p for p in prompt_ids if prompt_ids.count(p) > 1})
    trace_ok = not workflow_bad and not prompt_bad and not seed_bad and not dup_prompts
    if h3_require:
        checks["traceability"] = _check(
            trace_ok,
            "workflow/prompt_id/seed 齐全且 prompt_id 唯一",
            workflow_bad=workflow_bad, prompt_id_missing=prompt_bad,
            seed_missing=seed_bad, prompt_id_duplicated=dup_prompts)
    else:
        checks["traceability"] = _check(
            True, "previs：无 H3 证据（不计交付）", skipped=True,
            prompt_ids=prompt_ids)

    # 9) 静态标记（静态只允许卡片或显式兜底）
    fallbacks: list[dict] = []
    unmarked: list[str] = []
    for shot in bodies:
        sid = str(shot.get("id"))
        meta = metas.get(sid) or {}
        if meta.get("h3") is True:
            continue
        entry = {"id": sid, "type": shot.get("type"),
                 "reason": meta.get("error") or meta.get("fallback_reason") or "static_fallback",
                 "path": meta.get("path") or ""}
        if meta.get("fallback") is True or meta.get("fallback_static") is True:
            fallbacks.append(entry)
        elif not h3_require:
            fallbacks.append({**entry, "previs": True})
        else:
            unmarked.append(sid)
    card_missing = [str(s.get("id")) for s in cards
                    if not (_meta_of(shot_metas, str(s.get("id"))).get("static_card") is True)]
    static_ok = not unmarked and not card_missing
    checks["static_marked"] = _check(
        static_ok,
        ("静态仅卡片 + 显式兜底" if not unmarked else
         f"{len(unmarked)} 个非卡片镜静态且未标记兜底：{unmarked}"),
        unmarked=unmarked, card_unmarked=card_missing,
        fallbacks=[f["id"] for f in fallbacks if not f.get("previs")],
        previs=not h3_require)

    # 10) stage 证据（每镜 ref_image 存在）
    ref_missing = [sid for sid, meta in metas.items() if not _ref_image_exists(meta)]
    checks["stage_meta"] = _check(
        not ref_missing,
        "每镜 ref_image 均存在" if not ref_missing else f"{len(ref_missing)} 镜缺 ref_image",
        missing=ref_missing)

    ok = all(item["ok"] for item in checks.values())
    previs = not h3_require
    stats = {
        "template": template_id,
        "shots": len(shots),
        "h3_shots": len(bodies),
        "cards": len(cards),
        "fallbacks": len([f for f in fallbacks if not f.get("previs")]),
        "expected_duration": expected,
        "actual_duration": actual_duration,
        "duration_delta": delta,
        "resolution": f"{probe.get('width')}x{probe.get('height')}",
        "fps": fps,
        "black_segments": black,
        "subtitle_coverage": coverage,
        "dialogue_shots": dialog_count,
        "h3_coverage": h3_ratio,
        "prompt_ids": len(set(prompt_ids)),
        "elapsed_sec": round(time.time() - t0, 1),
    }
    report = {
        "ok": ok,
        "deliverable": bool(ok and not previs and not [f for f in fallbacks if not f.get("previs")]),
        "previs": previs,
        "template": template_id,
        "video": str(video),
        "checks": checks,
        "fallbacks": fallbacks,
        "stats": stats,
    }
    return report


def write_report(report: dict, path: Path | str) -> Path:
    out = Path(path)
    utils.write_json(out, report)
    return out


# ---------------------------------------------------------------------------
# CLI（对已渲染产物单独跑 QC）
# ---------------------------------------------------------------------------
def main(argv: list[str] | None = None) -> int:
    import argparse
    ap = argparse.ArgumentParser(prog="python -m pipeline.qc_v3",
                                 description="v3 成片 QC（读 h3_meta / episode.json）")
    ap.add_argument("--dir", required=True, help="outputs/templates_v3/{id}")
    ap.add_argument("--previs", action="store_true", help="按 previs 口径（不要求 H3）")
    args = ap.parse_args(argv)
    base = Path(args.dir)
    final = base / "final.mp4"
    episode = utils.read_json(base / "work" / "episode.json") or {}
    h3_meta = utils.read_json(base / "h3_meta.json") or {}
    shot_metas = {str(s.get("id")): s for s in h3_meta.get("shots") or []}
    template = {"id": h3_meta.get("template") or (episode.get("episode") or {}).get("template_id") or base.name}
    if not episode:
        print(json.dumps({"ok": False, "error": f"缺少 work/episode.json: {base}"},
                         ensure_ascii=False))
        return 1
    report = evaluate(video=final, plan=episode, shot_metas=shot_metas,
                      template=template,
                      subtitles=base / "work" / "final.ass",
                      h3_require=not args.previs)
    write_report(report, base / "qc_report.json")
    print(json.dumps(report, ensure_ascii=False))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    import sys
    sys.exit(main())
