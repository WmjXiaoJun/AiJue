# -*- coding: utf-8 -*-
"""V6 智能体工作流定义/读写（contract/v6/workflows/<template>.json）。"""
from __future__ import annotations

import json
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
    {"id": "editorial", "agent": "editor", "tool": "plan_editing",
     "depends": ["voice"], "enabled": True, "optional": False,
     "params": {}, "desc": "剪辑档案 → 逐镜景别/运镜/取景/转场/J-L"},
    {"id": "shots", "agent": "video_director", "tool": "render_shots",
     "depends": ["editorial"], "enabled": True, "optional": False,
     "params": {}, "desc": "2D 电影级动效逐镜渲染（17 运镜 + 表演 + 音频口型）"},
    {"id": "h3", "agent": "video_director", "tool": "render_h3",
     "depends": ["editorial"], "enabled": False, "optional": True,
     "params": {"hero_shots": 2}, "desc": "H3 图生视频 hero 镜（GPU，默认关闭）"},
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


def _path(template_id: str) -> Path:
    return WORKFLOW_DIR / f"{template_id}.json"


def default_workflow(template_id: str, template: dict | None = None,
                     duration: float = 40.0) -> dict:
    tpl = template or {}
    return {
        "version": 1,
        "id": template_id,
        "name": f"{tpl.get('label') or template_id} 工作流",
        "description": f"模板 {template_id} 的智能体编排（可编辑 stages/params）",
        "variables": {"template": template_id, "duration": float(duration),
                      "seed": 20261101},
        "stages": [dict(stage) for stage in DEFAULT_STAGES],
    }


def load_workflow(template_id: str, template: dict | None = None,
                  duration: float = 40.0) -> dict:
    p = _path(template_id)
    if p.is_file():
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(data, dict) and data.get("stages"):
                return data
        except Exception:  # noqa: BLE001
            pass
    return default_workflow(template_id, template, duration)


def save_workflow(workflow: dict) -> Path:
    template_id = str(workflow.get("id") or "").strip()
    if not template_id:
        raise ValueError("workflow.id 不能为空")
    WORKFLOW_DIR.mkdir(parents=True, exist_ok=True)
    p = _path(template_id)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(workflow, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(p)
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


__all__ = ["DEFAULT_STAGES", "WORKFLOW_DIR", "default_workflow", "load_workflow",
           "save_workflow", "merge_dict"]
