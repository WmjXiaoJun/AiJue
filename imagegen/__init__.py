# -*- coding: utf-8 -*-
"""imagegen —— SDXL 动漫角色概念图生成包（ComfyUI 本地后端）。

模块
----
workflow.py : 原生 SDXL txt2img 的 API-format workflow 构造/校验
prompt.py   : 角色 spec -> Animagine XL 风格 anime tag / negative
comfy.py    : ComfyUI 客户端（复用 integrations/comfyui 的提交/轮询逻辑）
sheet.py    : Pillow 合成角色设定图 sheet.png
cli.py      : python -m imagegen.cli generate / check
"""
__version__ = "1.0.0"
