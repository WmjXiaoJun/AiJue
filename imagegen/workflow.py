# -*- coding: utf-8 -*-
"""原生 SDXL txt2img 的 ComfyUI API-format workflow 构造器。

节点图（与 ComfyUI 前端 UI 工作流等价，直接可 POST /prompt）：
  CheckpointLoaderSimple ─┬─> CLIPTextEncode(pos) ─┐
                          ├─> CLIPTextEncode(neg) ─┼─> KSampler -> VAEDecode -> SaveImage
                          ├─> (CLIP)               │
                          └─> MODEL ───────────────┘
  EmptyLatentImage ───────────────────────────────┘（latent_image）
  VAEDecode 的 VAE 来自 CheckpointLoaderSimple 的第 3 个输出(2)

默认参数（contract/CONCEPT.md）：Animagine XL 4.0、28 steps、cfg 5.5、
euler_ancestral、normal、竖图为 832x1216、其它 1024x1024、固定 seed。
"""
from __future__ import annotations

DEFAULT_CKPT = "animagine-xl-4.0.safetensors"
DEFAULT_STEPS = 28
DEFAULT_CFG = 5.5
DEFAULT_SAMPLER = "euler_ancestral"
DEFAULT_SCHEDULER = "normal"
PORTRAIT_SIZE = (832, 1216)
SQUARE_SIZE = (1024, 1024)
DEFAULT_NEGATIVE_CHECKPOINT = DEFAULT_CKPT


def build_workflow(positive: str,
                   negative: str,
                   width: int = 1024,
                   height: int = 1024,
                   seed: int = 42,
                   filename_prefix: str = "imagegen/preview",
                   ckpt_name: str = DEFAULT_CKPT,
                   steps: int = DEFAULT_STEPS,
                   cfg: float = DEFAULT_CFG,
                   sampler_name: str = DEFAULT_SAMPLER,
                   scheduler: str = DEFAULT_SCHEDULER,
                   batch_size: int = 1) -> dict:
    """构造给 POST /prompt 的 API prompt（class_type + inputs）。节点 id 固定，便于复现/调试。"""
    if width % 8 or height % 8:
        raise ValueError("SDXL 宽高必须是 8 的倍数: %dx%d" % (width, height))
    return {
        "1": {
            "class_type": "CheckpointLoaderSimple",
            "inputs": {"ckpt_name": ckpt_name},
        },
        "2": {
            "class_type": "CLIPTextEncode",
            "inputs": {"text": positive, "clip": ["1", 1]},
        },
        "3": {
            "class_type": "CLIPTextEncode",
            "inputs": {"text": negative, "clip": ["1", 1]},
        },
        "4": {
            "class_type": "EmptyLatentImage",
            "inputs": {"width": int(width), "height": int(height), "batch_size": int(batch_size)},
        },
        "5": {
            "class_type": "KSampler",
            "inputs": {
                "model": ["1", 0],
                "positive": ["2", 0],
                "negative": ["3", 0],
                "latent_image": ["4", 0],
                "seed": int(seed),
                "steps": int(steps),
                "cfg": float(cfg),
                "sampler_name": sampler_name,
                "scheduler": scheduler,
                "denoise": 1.0,
            },
        },
        "6": {
            "class_type": "VAEDecode",
            "inputs": {"samples": ["5", 0], "vae": ["1", 2]},
        },
        "7": {
            "class_type": "SaveImage",
            "inputs": {"images": ["6", 0], "filename_prefix": filename_prefix},
        },
    }


# 每个 input 期望的连线类型（用于本地轻量校验，避免不必要的 400）
_LINK_TYPES = {
    ("CLIPTextEncode", "clip"): "CLIP",
    ("KSampler", "model"): "MODEL",
    ("KSampler", "positive"): "CONDITIONING",
    ("KSampler", "negative"): "CONDITIONING",
    ("KSampler", "latent_image"): "LATENT",
    ("VAEDecode", "samples"): "LATENT",
    ("VAEDecode", "vae"): "VAE",
    ("SaveImage", "images"): "IMAGE",
}

_REQUIRED = {
    "CheckpointLoaderSimple": ["ckpt_name"],
    "CLIPTextEncode": ["text", "clip"],
    "EmptyLatentImage": ["width", "height", "batch_size"],
    "KSampler": ["model", "positive", "negative", "latent_image", "seed", "steps", "cfg",
                 "sampler_name", "scheduler", "denoise"],
    "VAEDecode": ["samples", "vae"],
    "SaveImage": ["images", "filename_prefix"],
}


def validate_workflow(workflow: dict, object_info: dict) -> list:
    """对照 /object_info 做轻量校验：节点类型存在、必填输入齐全、连线指向合法节点。
    返回 warning 列表（不抛异常，交给服务端最终裁决）。"""
    warnings = []
    for nid, node in workflow.items():
        ctype = node.get("class_type")
        if ctype not in object_info:
            warnings.append("节点 #%s: 未知 class_type %s" % (nid, ctype))
            continue
        for name in _REQUIRED.get(ctype, []):
            if name not in node.get("inputs", {}):
                warnings.append("节点 #%s(%s): 缺少必填输入 %s" % (nid, ctype, name))
        for name, value in node.get("inputs", {}).items():
            if isinstance(value, list) and len(value) == 2:
                src = str(value[0])
                if src not in workflow:
                    warnings.append("节点 #%s(%s): 输入 %s 连到不存在的节点 %s"
                                    % (nid, ctype, name, src))
    return warnings


BUILTIN_FIELDS = ["ckpt_name", "steps", "cfg", "sampler", "scheduler",
                  "size", "seed", "prompt", "negative", "filename_prefix"]
