# -*- coding: utf-8 -*-
"""V6 智能体工作流定义/读写（contract/v6/workflows/<template>.json）。

V49：两条生产栈各有一套默认阶段
- base 模板（16 个内建综艺）：V5 渲染栈（render_v5：scene_render + motion2d）
- 短片模板（带 render_style 的 2D custom）：HQ 栈（templates3d_hq：关键帧 + H3 + 合成）
"""
from __future__ import annotations

import copy
import json
import re
from pathlib import Path

from .. import config

REPO = Path(config.REPO_ROOT)
WORKFLOW_DIR = REPO / "contract" / "v6" / "workflows"

#: 默认阶段（每个模板一份可编辑副本）
DEFAULT_STAGES: list[dict] = [
    {"id": "story", "agent": "story_architect", "tool": "build_story",
     "depends": [], "enabled": True, "optional": False,
     "params": {}, "desc": "模板 → 分镜脚本/台词/节奏"},
    {"id": "voice", "agent": "voice_director", "tool": "synthesize_voices",
     "depends": ["story"], "enabled": True, "optional": False,
     "params": {}, "desc": "固定音色 TTS + 语速/停顿适配"},
    {"id": "art", "agent": "art_director", "tool": "generate_art",
     "depends": ["story"], "enabled": False, "optional": True,
     "params": {"character": "", "poses": []},
     "desc": "生成/重建角色美术资产（GPU，默认关闭）"},
    {"id": "editorial", "agent": "editor", "tool": "plan_editing",
     "depends": ["voice"], "enabled": True, "optional": False,
     "params": {}, "desc": "剪辑档案 → 逐镜景别/运镜/取景/转场/J-L"},
    {"id": "shots", "agent": "video_director", "tool": "render_shots",
     "depends": ["editorial"], "enabled": True, "optional": False,
     "params": {}, "desc": "2D 电影级动效逐镜渲染（17 运镜 + 表演 + 音频口型）"},
    {"id": "h3", "agent": "video_director", "tool": "render_h3",
     "depends": ["editorial"], "enabled": False, "optional": True,
     "params": {"hero_shots": 2}, "desc": "H3 图生视频 hero 镜（GPU，默认关闭）"},
    {"id": "muse", "agent": "video_director", "tool": "muse_lipsync",
     "depends": ["shots"], "enabled": False, "optional": True,
     "params": {"limit": 2, "apply": False},
     "desc": "MuseTalk 口型修正（可选；启用后需同时跑 assemble 重新合成）"},
    {"id": "musev", "agent": "video_director", "tool": "muse_video",
     "depends": [], "enabled": False, "optional": True,
     "params": {"prompt": "", "duration": 4, "aspect": "16:9", "provider": ""},
     "desc": "Muse 视频生成（MuseV/MusePose/云端；未接入时自动跳过）"},
    {"id": "assemble", "agent": "editor", "tool": "assemble_episode",
     "depends": ["shots"], "enabled": True, "optional": False,
     "params": {}, "desc": "per-cut 转场/J-L/字幕/包装/BGM 成片"},
    {"id": "storyboard", "agent": "editor", "tool": "build_storyboard",
     "depends": ["assemble"], "enabled": True, "optional": False,
     "params": {}, "desc": "分镜表 json/html/png"},
    {"id": "qc", "agent": "qa", "tool": "evaluate_qc",
     "depends": ["assemble"], "enabled": True, "optional": False,
     "params": {}, "desc": "QC 硬门 + 抽帧"},
    {"id": "publish", "agent": "publisher", "tool": "write_report",
     "depends": ["qc"], "enabled": True, "optional": False,
     "params": {}, "desc": "成片/报告/批量汇总"},
]

#: V49 短片栈（2D custom 模板）：HQ 一次性渲染关键帧 + H3 + 合成
SHORTS_STAGES: list[dict] = [
    {"id": "story", "agent": "story_architect", "tool": "check_story",
     "depends": [], "enabled": True, "optional": False,
     "params": {}, "desc": "核对分幕/台词/时长 + 语义 brief 是否齐备（episode.json）"},
    {"id": "art", "agent": "art_director", "tool": "list_art",
     "depends": ["story"], "enabled": False, "optional": True,
     "params": {}, "desc": "列出角色/场景资产状态（默认只读）"},
    {"id": "shots", "agent": "video_director", "tool": "render_2d_hq",
     "depends": ["story"], "enabled": True, "optional": False,
     "params": {"keyframe_candidates": 2, "reuse_shots": True},
     "desc": "2D 关键帧 + H3 逐镜 + 合成（GPU，单片约 6–10 分钟）"},
    {"id": "muse", "agent": "video_director", "tool": "muse_lipsync",
     "depends": ["shots"], "enabled": False, "optional": True,
     "params": {"limit": 2, "apply": False, "reassemble": True},
     "desc": "MuseTalk 1.5 口型修正（GPU 串行，~20s/3s 镜头；apply=true 回写 shots 并重合成）"},
    {"id": "musev", "agent": "video_director", "tool": "muse_video",
     "depends": [], "enabled": False, "optional": True,
     "params": {"prompt": "", "duration": 4, "aspect": "16:9", "provider": ""},
     "desc": "Muse 视频生成（MuseV/MusePose/云端；未接入时自动跳过）"},
    {"id": "qc", "agent": "qa", "tool": "evaluate_hq_qc",
     "depends": ["shots"], "enabled": True, "optional": False,
     "params": {}, "desc": "成片存在性/时长/海报/抽帧 QC"},
    {"id": "publish", "agent": "publisher", "tool": "write_shorts_report",
     "depends": ["qc"], "enabled": True, "optional": False,
     "params": {}, "desc": "短片交付摘要（final/时长/QC/证据）"},
]


def is_shorts(template_id: str, template: dict | None = None) -> bool:
    """短片栈判定：模板合同带 render_style（V44+ 的 2D 子风格）即走 HQ 栈。"""
    tpl = template or {}
    if str(tpl.get("render_style") or "").strip():
        return True
    # custom_tpl_* 都是 templates3d_hq 产线（V22/V23 的 3D、V44+ 的 2D 子风格）
    return template_id.startswith("custom_tpl_")


def _path(template_id: str) -> Path:
    return WORKFLOW_DIR / f"{template_id}.json"


#: 合法栈取值（写进合同 stack 字段）
STACKS: tuple[str, ...] = ("v5_render", "hq_shorts")


def topo_order(stages: list[dict]) -> list[dict]:
    """稳定拓扑排序：能保持合同原顺序时**完全保持原顺序**，只有顺序与依赖冲突时才调整。

    每步从「依赖已就绪」的候选里取**合同里最靠前**的那个（而不是整层一起放），
    这样已经合法的合同排序零改动，只有把 shots 拖到 story 前面这类错序才被纠正。
    重复 id / 空 id / 依赖不存在的阶段 / 依赖成环 一律 ``ValueError``（可读文案）。
    """
    if not isinstance(stages, list) or not stages:
        raise ValueError("workflow.stages 必须是非空数组")
    ids = [str(s.get("id") or "") for s in stages]
    if any(not sid for sid in ids):
        raise ValueError("存在空的 stage id")
    dup = sorted({x for x in ids if ids.count(x) > 1})
    if dup:
        raise ValueError(f"stage id 重复: {dup}")
    deps = {str(s["id"]): [str(d) for d in (s.get("depends") or [])] for s in stages}
    for sid, ds in deps.items():
        for dep in ds:
            if dep not in deps:
                raise ValueError(f"{sid} 依赖不存在的阶段: {dep}；可用: {ids}")
    order = {str(s["id"]): i for i, s in enumerate(stages)}
    done: set[str] = set()
    out: list[dict] = []
    pending = list(stages)
    while pending:
        ready = [s for s in pending if all(d in done for d in deps[str(s["id"])])]
        if not ready:
            raise ValueError(f"阶段依赖存在环: {[str(s['id']) for s in pending]}")
        nxt = min(ready, key=lambda s: order[str(s["id"])])   # 就绪集里取合同最靠前的
        out.append(nxt)
        done.add(str(nxt["id"]))
        pending.remove(nxt)
    return out


def default_workflow(template_id: str, template: dict | None = None,
                     duration: float = 40.0) -> dict:
    tpl = template or {}
    shorts = is_shorts(template_id, tpl)
    stages = SHORTS_STAGES if shorts else DEFAULT_STAGES
    return {
        "version": 2,
        "id": template_id,
        "name": f"{tpl.get('label') or template_id} 工作流",
        "description": (f"模板 {template_id} 的智能体编排（可编辑 stages/params）；"
                        + ("栈=HQ 短片" if shorts else "栈=V5 渲染")),
        "stack": "hq_shorts" if shorts else "v5_render",
        "variables": {"template": template_id, "duration": float(duration),
                      "seed": 20261101},
        "stages": copy.deepcopy(stages),
    }


def load_workflow(template_id: str, template: dict | None = None,
                  duration: float = 40.0) -> dict:
    p = _path(template_id)
    if p.is_file():
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(data, dict) and data.get("stages"):
                # V49：老工作流（v1）没有 stack 字段，按模板判定补上，不改用户编辑
                data.setdefault("stack",
                                "hq_shorts" if is_shorts(template_id, template)
                                else "v5_render")
                return data
            reason = "stages 为空"
        except Exception as exc:  # noqa: BLE001
            reason = f"{type(exc).__name__}: {exc}"
        # 文件存在但坏了：不能静默按默认栈跑，必须让调用方知道（orchestrator 会拒绝执行）
        wf = default_workflow(template_id, template, duration)
        wf["degraded"] = True
        wf["degraded_reason"] = f"{p.name} 解析失败（{reason}），当前展示的是默认工作流"
        return wf
    return default_workflow(template_id, template, duration)


def save_workflow(workflow: dict) -> Path:
    """校验并写盘。所有失败一律 ``ValueError``（后端可直接转 400）。"""
    template_id = str(workflow.get("id") or "").strip()
    if not template_id:
        raise ValueError("workflow.id 不能为空")
    if not re.match(r"^[a-z][a-z0-9_]{1,60}$", template_id):
        raise ValueError(f"非法 workflow.id: {template_id}")
    stack = workflow.get("stack")
    if stack not in (None, "") and str(stack) not in STACKS:
        raise ValueError(f"非法 workflow.stack: {stack}；可用: {list(STACKS)}")
    stages = workflow.get("stages") or []
    ordered = topo_order(stages)      # 空/重复 id/悬空 depends/环 都在这里拦
    from .registry import TOOLS  # 延迟导入，避免循环依赖
    for stage in ordered:
        tool = stage.get("tool")
        if tool and tool not in TOOLS:
            raise ValueError(f"未知工具: {tool}（{stage.get('id')}）")
    WORKFLOW_DIR.mkdir(parents=True, exist_ok=True)
    p = _path(template_id)
    tmp = p.with_suffix(".json.tmp")
    try:
        tmp.write_text(json.dumps(workflow, ensure_ascii=False, indent=2) + "\n",
                       encoding="utf-8")
        tmp.replace(p)
    except OSError as exc:      # 写盘失败也统一成 ValueError，别让后端吞成 500
        raise ValueError(f"工作流写入失败（{p.name}）: {exc}") from exc
    return p


def merge_dict(base: dict, patch: dict) -> dict:
    """深合并（list 整体替换）。"""
    out = dict(base or {})
    for k, v in (patch or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = merge_dict(out[k], v)
        else:
            out[k] = v
    return out


__all__ = ["DEFAULT_STAGES", "SHORTS_STAGES", "STACKS", "WORKFLOW_DIR",
           "default_workflow", "is_shorts", "load_workflow", "save_workflow",
           "topo_order", "merge_dict"]
