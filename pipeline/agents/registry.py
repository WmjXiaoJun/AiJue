# -*- coding: utf-8 -*-
"""V6 智能体工具注册表：每个阶段 = 一个工具，可被任意智能体调用。

工具签名：tool(ctx: StageContext, params: dict) -> dict
所有工具只调用现有 pipeline 模块，不重复实现渲染逻辑。
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path

from .. import config, utils, v5_catalog

REPO = Path(config.REPO_ROOT)


@dataclass
class StageContext:
    template_id: str
    duration: float
    seed: int
    force: bool
    prog: object
    run_dir: Path
    catalog: dict = field(default_factory=dict)
    template: dict = field(default_factory=dict)
    style: dict = field(default_factory=dict)
    paths: dict = field(default_factory=dict)
    state: dict = field(default_factory=dict)
    logs: list = field(default_factory=list)

    def log(self, msg: str) -> None:
        self.logs.append({"t": time.strftime("%H:%M:%S"), "msg": str(msg)})
        try:
            if callable(self.prog):
                self.prog(msg)
        except Exception:  # noqa: BLE001
            pass


def _render_v5():
    from .. import render_v5
    return render_v5


def _stop_breeze(ctx: StageContext | None = None) -> bool:
    """停 Breeze TTS 释放显存；``BREEZE_TTS_KEEP=1`` 时保留（调试/连续多次配音用）。

    为什么智能体路径必须停：``tts_and_pace`` 会拉起 Breeze（~2.3GB 显存），只有
    ``render_v5.run_render`` 的收尾会停；``--stages voice`` 走的是本模块，不停就会让
    紧接着的 HQ（SDXL/H3）在 12GB 卡上 OOM。
    """
    if str(os.environ.get("BREEZE_TTS_KEEP", "")).lower() in ("1", "true", "yes"):
        return False
    try:
        from .. import tts_breeze  # noqa: PLC0415
        stopped = bool(tts_breeze.stop_server())
    except Exception as exc:  # noqa: BLE001 - 停服失败不能把已完成的配音判失败
        if ctx is not None:
            ctx.log(f"[voice] 停止 Breeze TTS 失败：{exc}")
        return False
    if stopped and ctx is not None:
        ctx.log("[voice] 已停止 Breeze TTS 服务（释放显存）")
    return stopped


def _load_plan(ctx: StageContext) -> dict:
    p = Path(ctx.paths["workdir"]) / "episode.json"
    if not p.is_file():
        raise FileNotFoundError(f"缺少 episode.json（先跑 story 阶段）: {p}")
    return json.loads(p.read_text(encoding="utf-8"))


def _save_plan(ctx: StageContext, plan: dict) -> None:
    utils.write_json(Path(ctx.paths["workdir"]) / "episode.json", plan)


def _style_of(ctx: StageContext, plan: dict) -> dict:
    rv = _render_v5()
    sid = str((plan.get("episode") or {}).get("style") or ctx.template.get("style") or "")
    style = rv._style(ctx.catalog, sid)
    ctx.style = style
    return style


# --------------------------------------------------------------------------- #
# 工具实现
# --------------------------------------------------------------------------- #
def build_story(ctx: StageContext, params: dict | None = None) -> dict:
    from .. import v5_story
    rv = _render_v5()
    plan = v5_story.build_plan(ctx.template, ctx.duration, seed=ctx.seed)
    plan["meta"]["template"] = ctx.template_id
    rv._add_card_shots(plan, ctx.template)
    _save_plan(ctx, plan)
    return {"shots": len(plan["shots"]),
            "dialogue": sum(1 for s in plan["shots"] if s.get("line")),
            "scenes": sorted({str(s.get("scene")) for s in plan["shots"] if s.get("scene")}),
            "episode": {k: plan["episode"].get(k) for k in ("id", "title", "style")}}


def synthesize_voices(ctx: StageContext, params: dict | None = None) -> dict:
    rv = _render_v5()
    plan = _load_plan(ctx)
    try:
        info = rv.tts_and_pace(plan, ctx.catalog, ctx.paths, ctx.prog, force=ctx.force)
    finally:
        # 配音成功/失败都停服：失败时更不能把 2.3GB 模型留在卡上。
        _stop_breeze(ctx)
    _save_plan(ctx, plan)
    return {"voices": info if isinstance(info, dict) else str(info),
            "lines": sum(1 for s in plan["shots"] if s.get("line"))}


def plan_editing(ctx: StageContext, params: dict | None = None) -> dict:
    from .. import editorial, motion2d
    rv = _render_v5()
    plan = _load_plan(ctx)
    style = _style_of(ctx, plan)
    zones = rv._scene_zones(ctx.catalog, plan)
    anchors = rv._scene_anchors(ctx.catalog, plan)
    ep = editorial.build_edit_plan(
        ctx.template, plan, style, seed=ctx.seed, zones_by_scene=zones,
        anchors_by_scene=anchors,
        supported_cameras=getattr(motion2d, "SUPPORTED_CAMERA_TYPES", ()))
    utils.write_json(Path(ctx.paths["workdir"]) / "edit_plan.json",
                     {"summary": ep["summary"], "shots": ep["shots"]})
    _save_plan(ctx, plan)
    return ep.get("summary") or {}


def render_shots(ctx: StageContext, params: dict | None = None) -> dict:
    rv = _render_v5()
    plan = _load_plan(ctx)
    style = _style_of(ctx, plan)
    clips = rv.render_shot_clips(plan, ctx.catalog, style, ctx.paths, ctx.prog,
                                 force=ctx.force)
    metas = clips.get("metas") or []
    utils.write_json(Path(ctx.paths["workdir"]) / "shot_meta.json", metas)
    clip_ids = []
    for m in metas:
        if isinstance(m, dict):
            clip_ids.append(str(m.get("id") or m.get("shot") or ""))
        else:
            clip_ids.append(str(m))
    return {"shots": len(metas), "clips": clip_ids}


def render_h3(ctx: StageContext, params: dict | None = None) -> dict:
    """H3 hero 镜：生成到 outputs/templates_v5_hybrid/<id>/（不覆盖主成片）。"""
    from .. import h3_v5
    params = params or {}
    return h3_v5.render_hybrid(ctx.template_id, duration=ctx.duration,
                               hero_shots=int(params.get("hero_shots") or 2),
                               force=ctx.force, seed=ctx.seed)


def assemble_episode(ctx: StageContext, params: dict | None = None) -> dict:
    rv = _render_v5()
    plan = _load_plan(ctx)
    style = _style_of(ctx, plan)
    asm = rv.assemble(plan, ctx.catalog, style, ctx.paths, ctx.prog)
    _save_plan(ctx, plan)
    return {"out": str(ctx.paths["out"]), "transition": asm.get("transition"),
            "ass": asm.get("ass"), "narration": asm.get("narration")}


def build_storyboard(ctx: StageContext, params: dict | None = None) -> dict:
    from .. import storyboard_v5
    plan = _load_plan(ctx)
    sb = storyboard_v5.build_storyboard(plan, ctx.paths)
    art = storyboard_v5.write_storyboard(sb, Path(ctx.paths["out_dir"]))
    return {"json": str(art.get("json")), "png": str(art.get("png")),
            "html": str(art.get("html")), "shots": len(sb.get("shots") or [])}


def evaluate_qc(ctx: StageContext, params: dict | None = None) -> dict:
    rv = _render_v5()
    plan = _load_plan(ctx)
    style = _style_of(ctx, plan)
    qc = rv.run_qc(plan, style, ctx.paths, ctx.prog)
    if not qc.get("ok"):
        failed = [k for k, v in (qc.get("checks") or {}).items() if not v.get("ok")]
        raise RuntimeError(f"QC 失败: {failed}")
    probe = utils.probe_video(ctx.paths["out"])
    frames = rv.extract_frames(ctx.paths["out"], ctx.paths, float(probe["duration"]))
    return {"qc_ok": True, "duration": probe["duration"],
            "resolution": f"{probe['width']}x{probe['height']}",
            "frames": frames.get("count") if isinstance(frames, dict) else None}


def write_report(ctx: StageContext, params: dict | None = None) -> dict:
    out_dir = Path(ctx.paths["out_dir"])
    workdir = Path(ctx.paths["workdir"])
    qc = utils.read_json(out_dir / "qc_report.json", {}) or {}
    manifest = utils.read_json(workdir / "manifest.json", {}) or {}
    timeline = utils.read_json(out_dir / "timeline.json", {}) or {}
    sb = utils.read_json(out_dir / "storyboard.json", {}) or {}
    summary = {
        "template": ctx.template_id, "label": ctx.template.get("label"),
        "final": str(ctx.paths["out"]), "ok": bool(qc.get("ok")),
        "duration": manifest.get("duration"),
        "transition": manifest.get("transition"),
        "shots": len(timeline.get("shots") or sb.get("shots") or []),
        "edit": (sb.get("edit") or {}).get("signature"),
        "qc": {k: v.get("ok") for k, v in (qc.get("checks") or {}).items()},
        "files": {name: str(out_dir / name) for name in
                  ("final.mp4", "storyboard.json", "storyboard.html",
                   "storyboard.png", "timeline.json", "qc_report.json")
                  if (out_dir / name).is_file()},
    }
    utils.write_json(Path(ctx.run_dir) / "template_summary.json", summary)
    return summary


# --------------------------------------------------------------------------- #
# V49 短片栈工具（2D custom 模板：templates3d_hq）
# --------------------------------------------------------------------------- #
def _outputs_root() -> Path:
    """唯一输出根：pipeline.config.OUTPUTS_ROOT（与后端 app/config 同口径）。"""
    return Path(config.OUTPUTS_ROOT)


def _shorts_dir(ctx: StageContext) -> Path:
    return _outputs_root() / "templates_3d" / ctx.template_id


def _semantic_brief(template_id: str) -> dict:
    path = REPO / "contract" / "v5" / "cinema3d" / "templates3d" / f"{template_id}_semantic.json"
    if not path.is_file():
        raise RuntimeError(f"缺少语义 brief: {path}")
    return utils.read_json(path, {}) or {}


def check_story(ctx: StageContext, params: dict | None = None) -> dict:
    """短片栈 story 阶段：核对分幕/台词/时长 + 语义 brief，写 episode.json。"""
    tpl = ctx.template or {}
    acts = [a for a in (tpl.get("acts") or []) if isinstance(a, dict)]
    cast = {str(c.get("role")): str(c.get("character"))
            for c in (tpl.get("cast") or []) if isinstance(c, dict)}
    problems: list[str] = []
    if not acts:
        problems.append("模板没有 acts")
    duration = 0.0
    dialogues = 0
    roles_used: set[str] = set()
    for act in acts:
        duration += float(act.get("duration") or 0)
        role = str(act.get("line") or "").strip()
        text = str(act.get("text") or "").strip()
        if role and role not in cast:
            problems.append(f"{act.get('id')}: line 角色 {role} 不在 cast")
        if text:
            dialogues += 1
            roles_used.add(role)
        if not str(act.get("scene") or "").strip():
            problems.append(f"{act.get('id')}: 缺 scene")
    brief = _semantic_brief(ctx.template_id)
    shots = [s for s in (brief.get("shots") or []) if s.get("prompt")]
    if not shots:
        problems.append("语义 brief 里没有带 prompt 的镜头")
    brief_seconds = round(sum(float(s.get("duration") or 0) for s in shots), 2)
    target_duration = float(tpl.get("target_duration") or 0)
    # 只记录、不在这里 raise：brief 可能是有意做长（季/合辑）；真正拦 GPU 的硬门在
    # render_2d_hq（否则 93 镜 307s 会对 19s 的模板全量开渲）。
    brief_over_target = bool(target_duration and brief_seconds > target_duration * 1.5 + 2.0)
    episode = {
        "template": ctx.template_id, "label": tpl.get("label"),
        "style": tpl.get("style"), "render_style": tpl.get("render_style"),
        "duration": round(duration, 2), "target_duration": tpl.get("target_duration"),
        "acts": len(acts), "dialogues": dialogues, "roles": sorted(roles_used),
        "brief_shots": len(shots), "brief_seconds": brief_seconds,
        "brief_over_target": brief_over_target, "cast": cast, "problems": problems,
    }
    utils.write_json(Path(ctx.run_dir) / "episode.json", episode)
    if problems:
        raise RuntimeError("短片合同不完整: " + "; ".join(problems[:6]))
    return {"acts": len(acts), "dialogues": dialogues, "duration": round(duration, 2),
            "brief_shots": len(shots), "brief_seconds": brief_seconds,
            "brief_over_target": brief_over_target,
            "render_style": tpl.get("render_style")}


def render_2d_hq(ctx: StageContext, params: dict | None = None) -> dict:
    """短片栈 shots 阶段：调 templates3d_hq（关键帧 + H3 + 合成），日志回流到 run。"""
    params = params or {}
    from .. import templates3d as t3  # noqa: PLC0415
    from .. import templates3d_hq as hq  # noqa: PLC0415

    tid = ctx.template_id
    # GPU 前置门（审查 2026-11-08）：brief 是一整季/脚本展开时可能有 93 镜 307s，而模板
    # target 只有 19s；旧实现会全量开渲，QC 再按 target 判死。这里先拦，避免白烧小时级 GPU。
    limit = int(params.get("limit") or 0)
    if limit <= 0:
        brief = _semantic_brief(tid)
        brief_seconds = sum(float(s.get("duration") or 0) for s in (brief.get("shots") or []))
        target = float((ctx.template or {}).get("target_duration") or ctx.duration or 0)
        if (target > 0 and brief_seconds > target * 1.5 + 2.0
                and not params.get("allow_brief_overflow")):
            raise RuntimeError(
                f"语义 brief 总时长 {brief_seconds:.1f}s 远超模板 target {target:.1f}s"
                f"（{len(brief.get('shots') or [])} 镜）：拒绝全量开渲以免白烧 GPU。"
                f" 请先对齐 target_duration，或在本阶段 params 设 limit=N / "
                f"allow_brief_overflow=true。")
    builtin = REPO / "contract" / "v5" / "templates" / f"{tid}.json"
    custom = REPO / "contract" / "v5" / "custom" / "templates" / f"{tid}.json"

    def _load_template(_tid: str) -> dict:
        for base in (REPO / "contract" / "v5" / "templates",
                     REPO / "contract" / "v5" / "custom" / "templates"):
            path = base / f"{_tid}.json"
            if path.is_file():
                return json.loads(path.read_text(encoding="utf-8"))
        raise RuntimeError(f"缺少模板合同: {_tid}")

    orig_load = t3.load_template
    t3.load_template = _load_template  # custom 模板兼容（与 custom_hq_runner 一致）

    render_style = str((ctx.template or {}).get("render_style") or "").strip()
    mode = "2d" if render_style else str(params.get("style_mode") or "3d")
    style_id = str(params.get("style_2d") or render_style or "jp_anime")
    argv = ["--template", tid, "--style-mode", mode]
    if limit:
        argv += ["--limit", str(limit)]
    if mode == "2d":
        argv += ["--2d-style", style_id]
    cand = int(params.get("keyframe_candidates")
               or (ctx.template or {}).get("keyframe_candidates") or 1)
    if cand > 1:
        argv += ["--keyframe-candidates", str(cand)]
    if params.get("keyframe_pick") is not None:
        argv += ["--keyframe-pick", str(params["keyframe_pick"])]
    if params.get("reuse_shots", True) and not ctx.force:
        argv += ["--reuse-shots"]
    if ctx.force:
        argv += ["--force"]
    if params.get("skip_t2va"):
        argv += ["--skip-t2va"]
    if params.get("skip_ref2va"):
        argv += ["--skip-ref2va"]

    orig_log = hq.log

    def _log(msg: str) -> None:
        text = str(msg)
        ctx.log(text)
        try:
            orig_log(text)
        except Exception:  # noqa: BLE001
            pass

    hq.log = _log
    try:
        try:
            code = int(hq.main(argv) or 0)
        except SystemExit as exc:  # brief 缺失等硬错误
            raise RuntimeError(f"templates3d_hq 中止: {exc}") from exc
    finally:
        hq.log = orig_log
        t3.load_template = orig_load  # 必须恢复，否则同进程后续模板全串味
    if code != 0:
        raise RuntimeError(f"templates3d_hq 退出码 {code}")

    out = _shorts_dir(ctx)
    # 镜头重渲后，旧的口型产物与 _orig 备份都失效：不清理会让下次 muse apply 用旧口型覆盖新镜头
    if ctx.force or not params.get("reuse_shots", True):
        for stale in (out / "shots" / "_orig", _outputs_root() / "muse" / ctx.template_id):
            if stale.is_dir():
                shutil.rmtree(stale, ignore_errors=True)
        ctx.log("[hq] 镜头已重渲：已清理旧 _orig/ 与 muse 产物")
    report = utils.read_json(out / "report.json", {}) or {}
    probe = (report.get("probe") or {}).get("format") or {}

    def _num(value, cast):
        try:
            return cast(value)
        except (TypeError, ValueError):
            return None

    return {"final": str(out / "final.mp4"), "style": style_id,
            "duration": _num(probe.get("duration"), float),
            "bytes": _num(probe.get("size"), int), "mode": mode}


def evaluate_hq_qc(ctx: StageContext, params: dict | None = None) -> dict:
    """短片栈 QC：成片存在 / 时长（上下界）/ 海报 / 音轨 / 分辨率。

    证据双写：``qc_report.json``（与 V5 栈同名同结构，发布台账统一消费）+ ``qc_shorts.json``（兼容别名）。
    """
    out = _shorts_dir(ctx)
    final = out / "final.mp4"
    poster = out / "poster.jpg"
    target = float((ctx.template or {}).get("target_duration") or ctx.duration or 0)
    probe = utils.probe_video(final) if final.is_file() else {}
    duration = float(probe.get("duration") or 0)
    # 时长门：0.6*target ≤ d ≤ max(1.5*target, target+2)；上下界都写进证据，事后可复核
    min_duration = round(target * 0.6, 3) if target > 0 else 0.0
    max_duration = round(max(target * 1.5, target + 2.0), 3) if target > 0 else 0.0
    duration_ok = bool(duration) and (
        target <= 0 or (duration >= min_duration and duration <= max_duration))
    checks = {
        "final_exists": {"ok": final.is_file() and final.stat().st_size > 100_000,
                         "bytes": final.stat().st_size if final.is_file() else 0},
        "poster": {"ok": poster.is_file()},
        "duration": {"ok": duration_ok, "duration": duration, "target": target,
                     "min_duration": min_duration, "max_duration": max_duration},
        "audio": {"ok": bool(probe.get("has_audio"))},
        "resolution": {"ok": int(probe.get("width") or 0) >= 1280,
                       "size": f"{probe.get('width')}x{probe.get('height')}"},
    }
    ok = all(bool(c.get("ok")) for c in checks.values())
    report = {"ok": ok, "checks": checks, "probe": probe,
              "duration": duration, "target_duration": target,
              "min_duration": min_duration, "max_duration": max_duration}
    utils.write_json(out / "qc_report.json", report)    # 统一名字（与 V5 一致）
    utils.write_json(out / "qc_shorts.json", report)    # 兼容别名：双写不删，老消费方不断
    if not ok:
        failed = [k for k, v in checks.items() if not v.get("ok")]
        raise RuntimeError(f"短片 QC 失败: {failed}")
    return {"qc_ok": True, "duration": duration,
            "min_duration": min_duration, "max_duration": max_duration,
            "shots_resolution": checks["resolution"]["size"]}


def write_shorts_report(ctx: StageContext, params: dict | None = None) -> dict:
    """短片栈 publish：交付摘要（final/时长/QC/证据）。

    发布前先断言：成片必须存在，且 **QC 证据不旧于成片**（否则 QC 绿是上一版成片的）。
    """
    out = _shorts_dir(ctx)
    final = out / "final.mp4"
    qc_path = out / "qc_report.json"
    if not qc_path.is_file():
        qc_path = out / "qc_shorts.json"
    qc = utils.read_json(qc_path, {}) or {}
    report = utils.read_json(out / "report.json", {}) or {}
    if not final.is_file():
        raise RuntimeError(f"短片成片不存在，不能发布: {final}")
    if qc_path.is_file():
        # QC 在成片之后生成才是有效证据；成片比 QC 新 = 成片已被替换/回滚（容差 1s 抗文件系统粒度）
        if final.stat().st_mtime - qc_path.stat().st_mtime > 1.0:
            raise RuntimeError(
                f"QC 证据陈旧：成片比 {qc_path.name} 新 "
                f"({final.stat().st_mtime:.0f} > {qc_path.stat().st_mtime:.0f})，请先重跑 qc")
    probe = utils.probe_video(final)
    summary = {
        "template": ctx.template_id, "label": (ctx.template or {}).get("label"),
        "stack": "hq_shorts", "final": str(final),
        "poster": str(out / "poster.jpg") if (out / "poster.jpg").is_file() else None,
        "duration": probe.get("duration"),
        "resolution": f"{probe.get('width')}x{probe.get('height')}" if probe else None,
        "qc_ok": bool(qc.get("ok")),
        "qc_report": qc_path.name,
        "qc": {k: v.get("ok") for k, v in (qc.get("checks") or {}).items()},
        "assemble": (report.get("assemble") or {}),
        "run_dir": str(ctx.run_dir),
    }
    utils.write_json(Path(ctx.run_dir) / "template_summary.json", summary)
    md = [f"# {summary['label']}（{ctx.template_id}）", "",
          f"- 栈：HQ 短片（关键帧 + H3）",
          f"- 成片：{summary['final']}",
          f"- 时长：{summary['duration']}s ｜ 分辨率：{summary['resolution']}",
          f"- QC（{summary['qc_report']}）：{'通过' if summary['qc_ok'] else '未通过'} {summary['qc']}", ""]
    (Path(ctx.run_dir) / "summary.md").write_text("\n".join(md), encoding="utf-8")
    return summary


def _dialogue_pairs(template_id: str, limit: int = 2) -> list:
    """对白镜头 + 音频 对（规则与 pipeline.lipsync.dialogue_pairs 一致）。"""
    from .. import lipsync  # noqa: PLC0415
    return [(p["clip"], p["audio"]) for p in lipsync.dialogue_pairs(template_id, limit)]


def muse_lipsync(ctx: StageContext, params: dict | None = None) -> dict:
    """Muse 阶段：MuseTalk 1.5 口型修正（GPU 串行，产物 outputs/muse/<tid>/）。"""
    from .. import lipsync  # noqa: PLC0415

    params = params or {}
    from .workflow import is_shorts  # noqa: PLC0415

    if not is_shorts(ctx.template_id, ctx.template):
        return {"skipped": True,
                "reason": "Muse 口型只支持 HQ 短片栈（custom_tpl_*）；V5 栈请用 motion2d 的 viseme"}
    avail = lipsync.available()
    if not avail.get("ok"):
        raise RuntimeError(f"MuseTalk 不可用：{avail.get('reason')}")
    limit = max(1, min(int(params.get("limit") or 2), 8))
    clip_start = float(params.get("start") or 0.0)
    pairs = _dialogue_pairs(ctx.template_id, limit)
    if not pairs:
        return {"skipped": True, "reason": "没有「镜头 + 音频」对（先跑 shots 阶段）"}
    # 幂等：上一次 apply 会把 shots 换成口型版；再次跑 Muse 必须从 _orig/ 的原件取样，
    # 否则会对已经改过的嘴再改一次（画质/口型越改越差）。
    targets = []
    for clip, audio in pairs:
        backup = Path(clip).parent / "_orig" / Path(clip).name
        targets.append((Path(clip), backup if backup.is_file() else Path(clip), audio))
    out_dir = _outputs_root() / "muse" / ctx.template_id
    out_dir.mkdir(parents=True, exist_ok=True)
    apply_back = bool(params.get("apply"))
    items = []
    for idx, (clip, src, audio) in enumerate(targets, 1):
        out = out_dir / f"{clip.stem}_muse.mp4"
        if out.is_file() and not ctx.force:
            items.append({"shot": clip.stem, "out": str(out), "reused": True})
            ctx.log(f"[muse {idx}/{len(pairs)}] {clip.stem} 复用已有产物")
            continue
        ctx.log(f"[muse {idx}/{len(pairs)}] {clip.stem} 开始"
                f"（{'原件 ' if src != clip else ''}{src.name} + {audio.name}）")
        t0 = time.time()
        res = lipsync.sync_clip(src, audio, out,
                                start=(clip_start or None),
                                duration=(params.get("duration") or None))
        elapsed = round(time.time() - t0, 1)
        item = {"shot": clip.stem, "out": str(out), "elapsed": elapsed,
                "result": {k: res.get(k) for k in
                           ("ok", "frames", "elapsed_sec", "face_found",
                            "before", "after", "output")}
                if isinstance(res, dict) else None}
        if params.get("score", True):
            try:
                sync = lipsync.measure_sync(out, audio, start=clip_start)
                item["sync"] = {k: sync.get(k) for k in
                                ("provider", "corr_best", "lag_frames", "face_ratio")}
            except Exception as exc:  # noqa: BLE001
                item["sync"] = {"error": str(exc)[:120]}
        items.append(item)
        ctx.log(f"[muse {idx}/{len(pairs)}] {clip.stem} 完成 {elapsed}s")

    applied = 0
    assemble = None
    reassembled = False
    if apply_back:
        by_shot = {clip.stem: clip for clip, _src, _audio in targets}
        replaced: list[tuple[Path, Path]] = []
        try:
            for item in items:
                out = Path(item["out"])
                clip = by_shot.get(str(item["shot"]))
                if clip is None or not out.is_file():
                    continue
                backup_dir = clip.parent / "_orig"
                backup_dir.mkdir(parents=True, exist_ok=True)
                backup = backup_dir / clip.name
                if not backup.is_file():
                    backup.write_bytes(clip.read_bytes())
                # 原子替换：写临时文件再 os.replace，避免中途失败留下半个 mp4
                tmp = clip.with_suffix(clip.suffix + ".muse_tmp")
                tmp.write_bytes(out.read_bytes())
                os.replace(tmp, clip)
                replaced.append((clip, backup))
                applied += 1
                ctx.log(f"[muse] 回写 shots/{clip.name}（原件存 _orig/）")
        except Exception as exc:  # noqa: BLE001 - 回写失败要把已替换的镜头还原
            for clip, backup in replaced:
                try:
                    if backup.is_file():
                        shutil.copy2(backup, clip)
                except OSError:
                    pass
            raise RuntimeError(f"muse 回写失败，已回滚：{exc}") from exc
        if applied and params.get("reassemble", True):
            from .. import templates3d as t3  # noqa: PLC0415
            builtin = REPO / "contract" / "v5" / "templates" / f"{ctx.template_id}.json"
            custom = REPO / "contract" / "v5" / "custom" / "templates" / f"{ctx.template_id}.json"

            def _load_template(_tid: str) -> dict:
                for base in (REPO / "contract" / "v5" / "templates",
                             REPO / "contract" / "v5" / "custom" / "templates"):
                    path = base / f"{_tid}.json"
                    if path.is_file():
                        return json.loads(path.read_text(encoding="utf-8"))
                raise RuntimeError(f"缺少模板合同: {_tid}")

            orig_load = t3.load_template
            t3.load_template = _load_template
            ctx.log("[muse] 重新合成成片（templates3d.assemble）")
            try:
                report = t3.assemble(ctx.template_id)
            except Exception as exc:  # noqa: BLE001 - 合成失败必须还原镜头
                for clip, backup in replaced:
                    try:
                        if backup.is_file():
                            shutil.copy2(backup, clip)
                    except OSError:
                        pass
                raise RuntimeError(f"muse 重合成失败，已回滚镜头：{exc}") from exc
            finally:
                t3.load_template = orig_load
            assemble = (report.get("probe") or {}).get("format") or {}
            ctx.log(f"[muse] 合成完成 duration={assemble.get('duration')}")
            reassembled = True
            # 合成后必须刷新 QC：否则 qc_shorts.json 还是上一版成片的证据
            qc = evaluate_hq_qc(ctx, {})  # 失败必须冒泡：不能“QC 红、run 绿”
            ctx.log(f"[muse] QC 已刷新 duration={qc.get('duration')}")
    result = {"clips": len(items), "out_dir": str(out_dir), "provider": "musetalk",
              "applied": applied, "assemble": assemble, "items": items}
    if apply_back and applied and not reassembled:
        # 镜头已回写但成片没重出：必须显式告诉调用方（muse reassemble=false 或合成被跳过）
        result["needs_assemble"] = True
        result["reason_needs_assemble"] = "muse 已回写 shots，但未重新合成；请再跑 assemble 阶段"
        ctx.log("[muse] 已回写镜头但未重新合成：needs_assemble=true（需再跑 assemble）")
    return result

def _muse_missing(state: dict) -> str:
    """把 available().checks 里「缺什么」汇总成一句人话（用于 skipped reason）。"""
    gaps: list[str] = []
    for name, item in (state.get("checks") or {}).items():
        if not isinstance(item, dict) or item.get("configured"):
            continue
        missing = [str(m) for m in (item.get("missing") or []) if m]
        if missing:
            gaps.append(f"{name} 缺 {'/'.join(missing)}")
    return "；".join(gaps[:6])


def muse_video(ctx: StageContext, params: dict | None = None) -> dict:
    """Muse 视频生成（MuseV / MusePose / 云端）；未接入或没选题时**跳过**并给原因。

    跳过而不是抛（契约：stage 未配置 = skipped + reason，不是 failed）：
    1) `available().ok is False` → 按 checks 说明缺哪些 env/文件；
    2) 只剩 stub 且没要 allow_stub；
    3) 没有 prompt/image/pose → 空选题不调用付费视频接口（旧实现会拿模板 label 当提示词）。
    """
    from .. import muse_video as mv  # noqa: PLC0415

    params = params or {}
    state = mv.available()
    provider = str(params.get("provider") or state.get("provider") or "")
    if not state.get("ok") and not params.get("provider"):
        return {"skipped": True,
                "reason": "未配置任何 Muse provider（"
                          + (_muse_missing(state) or
                             "MUSEV_CMD / MUSEPOSE_CMD / REPLICATE_API_TOKEN / USEAPI_TOKEN …")
                          + "）",
                "configured_providers": state.get("configured_providers") or [],
                "checks": state.get("checks"), "docs": state.get("docs"),
                "hint": state.get("hint")}
    if provider == "stub" and not params.get("allow_stub"):
        return {"skipped": True, "reason": "Muse 视频模型未接入（本地未安装 / 云端未配置）",
                "checks": state.get("checks"), "docs": state.get("docs")}
    prompt = str(params.get("prompt") or "").strip()
    if not prompt and not params.get("image") and not params.get("pose"):
        return {"skipped": True,
                "reason": "muse_video 未提供 prompt/image/pose（空选题不调用付费视频接口）",
                "provider": provider, "checks": state.get("checks"),
                "docs": state.get("docs")}
    out = (_outputs_root() / "muse_video" / ctx.template_id /
           f"{time.strftime('%Y%m%d_%H%M%S')}_{provider or 'muse'}.mp4")
    spec = {"provider": provider, "prompt": prompt,
            "image": params.get("image"), "pose": params.get("pose"),
            "duration": float(params.get("duration") or 4.0),
            "aspect": str(params.get("aspect") or "16:9"),
            "seed": int(params.get("seed") or ctx.seed), "out": str(out)}
    result = mv.generate_video(spec, prog=lambda stage, msg: ctx.log(f"[{stage}] {msg}"))
    return {k: result.get(k) for k in ("provider", "out", "bytes", "elapsed_sec", "stub")}

TOOLS: dict[str, dict] = {
    "build_story": {"fn": build_story, "agent": "story_architect",
                    "desc": "模板 → 分镜脚本/台词/节奏（episode.json）"},
    "generate_art": {"fn": None, "agent": "art_director",
                     "desc": "生成/重建角色 AI 立绘与姿态资产（GPU，默认跳过已有）"},
    "set_pose": {"fn": None, "agent": "art_director",
                 "desc": "设置角色姿态资产启用策略（contract/v6/art/pose_policy.json）"},
    "list_art": {"fn": None, "agent": "art_director",
                 "desc": "列出角色/场景资产状态"},
    "synthesize_voices": {"fn": synthesize_voices, "agent": "voice_director",
                          "desc": "固定音色 TTS + 语速/停顿（音色缓存）"},
    "plan_editing": {"fn": plan_editing, "agent": "editor",
                     "desc": "剪辑档案 → 逐镜景别/运镜/取景/转场/J-L"},
    "render_shots": {"fn": render_shots, "agent": "video_director",
                     "desc": "2D 电影级动效逐镜渲染"},
    "render_h3": {"fn": render_h3, "agent": "video_director",
                  "desc": "H3 图生视频 hero 镜（GPU，默认关闭）"},
    "assemble_episode": {"fn": assemble_episode, "agent": "editor",
                         "desc": "per-cut 转场/字幕/包装/BGM 成片"},
    "build_storyboard": {"fn": build_storyboard, "agent": "editor",
                         "desc": "分镜表 json/html/png"},
    "evaluate_qc": {"fn": evaluate_qc, "agent": "qa",
                    "desc": "QC 硬门 + 抽帧"},
    "write_report": {"fn": write_report, "agent": "publisher",
                     "desc": "成片/报告汇总"},
    # V49 短片栈（2D custom → templates3d_hq）
    "check_story": {"fn": check_story, "agent": "story_architect",
                    "desc": "短片分幕/台词/brief 核对（episode.json）"},
    "render_2d_hq": {"fn": render_2d_hq, "agent": "video_director",
                     "desc": "2D 关键帧 + H3 逐镜 + 合成（GPU，~6–10 分钟/片）"},
    "evaluate_hq_qc": {"fn": evaluate_hq_qc, "agent": "qa",
                       "desc": "短片成片/时长/海报/音轨 QC"},
    "write_shorts_report": {"fn": write_shorts_report, "agent": "publisher",
                            "desc": "短片交付摘要 + summary.md"},
    # V50 Muse（MuseTalk 1.5）
    "muse_lipsync": {"fn": muse_lipsync, "agent": "video_director",
                     "desc": "MuseTalk 口型修正（GPU 串行，逐镜，产物 outputs/muse/<tid>/）"},
    "muse_video": {"fn": muse_video, "agent": "video_director",
                   "desc": "Muse 视频生成（MuseV/MusePose/云端；未接入时跳过并给原因）"},
}


def call_tool(name: str, ctx: StageContext, params: dict | None = None) -> dict:
    tool = TOOLS.get(str(name))
    if not tool:
        raise KeyError(f"未知工具: {name}；可用: {sorted(TOOLS)}")
    fn = tool.get("fn")
    if fn is None:
        # 美术类工具按需延迟导入（避免 agents 包循环引用）
        from . import art as art_mod
        fn = {"generate_art": art_mod.generate_art,
              "set_pose": art_mod.set_pose,
              "list_art": art_mod.list_art}[str(name)]
        tool["fn"] = fn
    return fn(ctx, params or {})


def new_context(template_id: str, *, duration: float = 40.0, seed: int = 20261101,
                force: bool = False, run_dir: Path | None = None,
                prog=None) -> StageContext:
    rv = _render_v5()
    catalog = v5_catalog.load_catalog()
    template = catalog["templates"][template_id]
    args = argparse.Namespace(template=template_id, duration=duration, seed=seed,
                              force=force, out=None, workdir=None, limit=0)
    paths = rv.resolve_paths(args, template_id)
    rv._ensure_dirs(paths)
    return StageContext(template_id=template_id, duration=duration, seed=seed,
                        force=force, prog=prog, run_dir=Path(run_dir or REPO / "tmp"),
                        catalog=catalog, template=template, paths=paths)


__all__ = ["StageContext", "TOOLS", "call_tool", "new_context"]
