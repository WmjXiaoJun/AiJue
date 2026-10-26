# -*- coding: utf-8 -*-
"""V6 智能体工作台：角色、工具注册、工作流、编排器、修改操作、CLI。"""
from .roles import ROLES, get_role
from .registry import TOOLS, call_tool
from .workflow import DEFAULT_STAGES, load_workflow, save_workflow, default_workflow
from .orchestrator import AgentRun, run_template
from .ops import apply_op, edit_op

__all__ = ["ROLES", "get_role", "TOOLS", "call_tool", "DEFAULT_STAGES",
           "load_workflow", "save_workflow", "default_workflow",
           "AgentRun", "run_template", "apply_op", "edit_op"]
