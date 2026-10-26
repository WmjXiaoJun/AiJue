# -*- coding: utf-8 -*-
"""V6 智能体工具注册表：每个阶段 = 一个工具，可被任意智能体调用。

工具签名：tool(ctx: StageContext, params: dict) -> dict
所有工具只调用现有 pipeline 模块，不重复实现渲染逻辑。
"""
from __future__ import annotations

import argparse
import json
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
    info = rv.tts_and_pace(plan, ctx.catalog, ctx.paths, ctx.prog, force=ctx.force)
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
