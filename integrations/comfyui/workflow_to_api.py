#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ComfyUI UI-format workflow JSON  ->  /prompt API 格式转换器
================================================================
ComfyUI 前端保存的工作流是「UI 格式」：nodes / links / widgets_values。
POST /prompt 需要的是「API 格式」：{node_id: {"class_type": ..., "inputs": {...}}}。

本脚本按节点 schema 做无损转换：
  1. 连线：按 links 的 [link_id, src_node, src_slot, dst_node, dst_slot, type] 解析，
     目标输入名取 dst 节点 inputs[dst_slot].name（缺省时回退 /object_info 的 input_order）。
  2. 控件值：优先用新版前端写出的 widgets_values_named（键就是 prompt 里的输入名）；
     老格式没有该字段时，按 /object_info 的 required+optional 顺序做位置映射，
     并自动跳过 control_after_generate 这类前端附加控件值。
  3. 动态控件（如 SaveVideo 的 format.format / format.codec）在 prompt 里就是
     带点的扁平键，原样保留。
  4. mode=2(静音) / mode=4(旁路) 的节点、以及 Note/MarkdownNote/Reroute 等
     UI-only 节点会被跳过；缺少必填输入的节点会给出 warning。
  5. --set node_id.field=value 可在转换后覆盖任意输入（值按现有类型/schema 自动转型）。

用法：
  python workflow_to_api.py 04_i2v_fl2v_4step_sla.json --out api_workflows/04_api.json
  python workflow_to_api.py <workflow.json> --server http://127.0.0.1:8188 \
      --set 7.prompt="..." --set 7.width=768 --set 7.height=768 --set 9.noise_seed=123

仅依赖 Python 标准库（urllib），可用任意 Python 3.9+ 运行。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.request

DEFAULT_SERVER = os.environ.get("COMFYUI_URL", "http://127.0.0.1:8188")

# UI-only 节点（不属于后端节点类，直接跳过）
UI_ONLY_NODE_TYPES = {
    "Note", "MarkdownNote", "Reroute", "PrimitiveNode", "PrimitiveInt",
    "PrimitiveString", "PrimitiveStringMultiline", "PrimitiveFloat", "PrimitiveBoolean",
    "PreviewImage",  # 无输出/不影响生成
}

# 前端附加的控件值（不是真正的节点输入）
UI_EXTRA_WIDGET_KEYS = {"control_after_generate"}

# 非控件（纯连线）输入类型
LINK_TYPES = {
    "MODEL", "CLIP", "VAE", "CONDITIONING", "LATENT", "IMAGE", "MASK", "AUDIO",
    "VIDEO", "NOISE", "GUIDER", "SAMPLER", "SIGMAS", "CLIP_VISION",
    "CLIP_VISION_OUTPUT", "STYLE_MODEL", "CONTROL_NET", "UPSCALE_MODEL",
    "MODEL_PATCH", "WEBCAM", "SVG", "PROMPT", "EXTRA_PNGINFO",
}


# --------------------------------------------------------------------------- #
# HTTP / 文件工具
# --------------------------------------------------------------------------- #
def fetch_object_info(server: str = DEFAULT_SERVER, timeout: float = 120.0) -> dict:
    """GET /object_info，返回所有节点类的 schema。"""
    url = server.rstrip("/") + "/object_info"
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def load_workflow(path: str) -> dict:
    with open(path, "r", encoding="utf-8-sig") as f:
        return json.load(f)


# --------------------------------------------------------------------------- #
# schema 辅助
# --------------------------------------------------------------------------- #
def _schema_all_names(node_schema: dict) -> list:
    """按 required -> optional 顺序返回输入名列表。"""
    order = node_schema.get("input_order") or {}
    required = order.get("required")
    optional = order.get("optional")
    if required is None:
        required = list((node_schema.get("input") or {}).get("required", {}).keys())
    if optional is None:
        optional = list((node_schema.get("input") or {}).get("optional", {}).keys())
    return list(required) + list(optional)


def _input_defn(node_schema: dict, name: str):
    inp = node_schema.get("input") or {}
    for group in ("required", "optional"):
        grp = inp.get(group) or {}
        if name in grp:
            return grp[name]
    return None


def is_widget_input(defn) -> bool:
    """判断某个输入是否是控件（可填值），而不是纯连线槽。"""
    if not defn:
        return True  # 未知：按控件处理（宽松）
    typ = defn[0] if isinstance(defn, (list, tuple)) and defn else defn
    if isinstance(typ, (list, tuple)):
        return True          # 老式 COMBO（选项数组）
    if not isinstance(typ, str):
        return True
    t = typ.upper()
    if t in LINK_TYPES:
        return False
    return True              # INT/FLOAT/STRING/BOOLEAN/COMBO/COMFY_DYNAMICCOMBO_V3/...


def coerce_value(value, defn):
    """按 schema 把字符串/命令行值转成正确类型。"""
    typ = defn[0] if isinstance(defn, (list, tuple)) and defn else defn
    meta = defn[1] if isinstance(defn, (list, tuple)) and len(defn) > 1 and isinstance(defn[1], dict) else {}
    if isinstance(typ, str):
        t = typ.upper()
        if t == "INT":
            return int(value)
        if t == "FLOAT":
            return float(value)
        if t == "BOOLEAN":
            if isinstance(value, bool):
                return value
            return str(value).strip().lower() in ("1", "true", "yes", "y", "on")
        if t == "STRING":
            return str(value)
        if t in ("COMBO", "COMFY_DYNAMICCOMBO_V3"):
            opts = meta.get("options")
            if isinstance(opts, list):
                for opt in opts:
                    if isinstance(opt, str) and str(value) == opt and not _looks_numeric(value):
                        return opt
                    key = opt.get("key") if isinstance(opt, dict) else None
                    if key is not None and str(value) == str(key):
                        return key
            return value
    if isinstance(typ, (list, tuple)):  # 老式选项数组
        for opt in typ:
            if str(value) == str(opt):
                return opt
        return value
    # 无 schema（比如动态控件的点号键）：按字面量猜
    if isinstance(value, str):
        if re.fullmatch(r"-?\d+", value):
            return int(value)
        if re.fullmatch(r"-?\d+\.\d+", value):
            return float(value)
        if value.lower() in ("true", "false"):
            return value.lower() == "true"
    return value


def _looks_numeric(s) -> bool:
    return isinstance(s, (int, float)) and not isinstance(s, bool)


# --------------------------------------------------------------------------- #
# 核心转换
# --------------------------------------------------------------------------- #
def _parse_links(links) -> dict:
    """link_id -> (src_node, src_slot, dst_node, dst_slot)"""
    out = {}
    for l in links or []:
        if isinstance(l, dict):  # 少数导出器用对象
            out[l.get("id")] = (l.get("origin_id"), l.get("origin_slot"),
                                l.get("target_id"), l.get("target_slot"))
        elif isinstance(l, (list, tuple)) and len(l) >= 5:
            out[l[0]] = (l[1], l[2], l[3], l[4])
    return out


def ui_to_api(workflow: dict, object_info: dict, log=print):
    """UI 工作流 -> (api_prompt, warnings)。"""
    nodes = workflow.get("nodes") or []
    if not nodes:
        raise ValueError("工作流里没有 nodes，确认这是 ComfyUI 的 UI 格式工作流 JSON")
    links = _parse_links(workflow.get("links") or [])

    skipped = {}       # node_id -> reason
    prompt = {}
    warnings = []

    # 第一遍：确定哪些节点参与执行
    for node in nodes:
        nid = str(node.get("id"))
        ctype = node.get("type") or ""
        mode = node.get("mode", 0)
        if mode in (2, 4):
            skipped[nid] = "mode=%s(%s)" % (mode, "静音" if mode == 2 else "旁路")
        elif ctype in UI_ONLY_NODE_TYPES:
            skipped[nid] = "UI-only"
        elif ctype not in object_info:
            skipped[nid] = "节点类型不在 /object_info（未安装/已改名）"

    # 第二遍：转换
    for node in nodes:
        nid = str(node.get("id"))
        if nid in skipped:
            log("[skip] node %-4s %-28s %s" % (nid, node.get("type"), skipped[nid]))
            continue
        ctype = node["type"]
        schema = object_info[ctype]
        all_names = _schema_all_names(schema)
        inputs = {}

        # ---- 1) 连线 ----
        for slot_idx, slot in enumerate(node.get("inputs") or []):
            if not isinstance(slot, dict):
                continue
            link_id = slot.get("link")
            if link_id is None:
                continue
            name = slot.get("name")
            if not name:
                name = all_names[slot_idx] if slot_idx < len(all_names) else None
            if not name:
                warnings.append("node %s(%s): 第 %d 个输入槽找不到名字，连线被忽略"
                                % (nid, ctype, slot_idx))
                continue
            src = links.get(link_id)
            if not src:
                warnings.append("node %s(%s): link %s 找不到源节点" % (nid, ctype, link_id))
                continue
            src_id, src_slot = src[0], src[1]
            if str(src_id) in skipped:
                warnings.append("node %s(%s): 输入 %s 来自被跳过节点 %s"
                                % (nid, ctype, name, src_id))
                continue
            inputs[name] = [str(src_id), int(src_slot or 0)]

        # ---- 2) 控件值 ----
        named = node.get("widgets_values_named")
        if isinstance(named, dict) and named:
            for k, v in named.items():
                if k in UI_EXTRA_WIDGET_KEYS:
                    continue
                if k in inputs:          # 连线优先于控件值
                    continue
                inputs[k] = v
        else:
            # 位置映射回退（老格式）
            values = node.get("widgets_values") or []
            if isinstance(values, dict):
                values = list(values.values())
            vi = 0
            for nm in all_names:
                defn = _input_defn(schema, nm)
                if not is_widget_input(defn):
                    continue
                if nm in inputs:
                    continue
                if vi >= len(values):
                    break
                inputs[nm] = values[vi]
                vi += 1
                meta = defn[1] if isinstance(defn, (list, tuple)) and len(defn) > 1 and isinstance(defn[1], dict) else {}
                if meta.get("control_after_generate"):
                    vi += 1  # 跳过 "fixed"/"randomize" 附加控件值

        # ---- 3) 必填校验 ----
        inp_schema = schema.get("input") or {}
        for nm, defn in (inp_schema.get("required") or {}).items():
            if nm not in inputs:
                warnings.append("node %s(%s): 缺少必填输入 %s" % (nid, ctype, nm))

        prompt[nid] = {"class_type": ctype, "inputs": inputs}

    return prompt, warnings


def apply_overrides(prompt: dict, assignments: list, object_info: dict) -> list:
    """--set node_id.field=value，返回 warning 列表。"""
    warnings = []
    for item in assignments:
        if "=" not in item:
            raise SystemExit("--set 需要 node_id.field=value 形式，收到: %r" % item)
        key, raw = item.split("=", 1)
        if "." not in key:
            raise SystemExit("--set 的键需要 node_id.field 形式，收到: %r" % key)
        nid, field = key.split(".", 1)
        nid = nid.strip()
        field = field.strip()
        if nid not in prompt:
            warnings.append("--set %s: 节点 %s 不存在/被跳过" % (item, nid))
            continue
        node = prompt[nid]
        ctype = node["class_type"]
        schema = object_info.get(ctype) or {}
        defn = _input_defn(schema, field)
        if defn is not None:
            try:
                value = coerce_value(raw, defn)
            except (TypeError, ValueError) as e:
                raise SystemExit("--set %s: 值无法转换为 %s（%s）" % (item, defn[0], e))
        else:
            cur = node["inputs"].get(field)
            if isinstance(cur, bool):
                value = str(raw).lower() in ("1", "true", "yes")
            elif isinstance(cur, int):
                value = int(raw)
            elif isinstance(cur, float):
                value = float(raw)
            else:
                value = coerce_value(raw, "STRING")
        node["inputs"][field] = value
    return warnings


def summarize(prompt: dict) -> str:
    lines = []
    for nid in sorted(prompt, key=lambda x: int(x) if x.isdigit() else x):
        node = prompt[nid]
        links = {k: v for k, v in node["inputs"].items() if isinstance(v, list)}
        vals = {k: v for k, v in node["inputs"].items() if not isinstance(v, list)}
        lines.append("  #%-3s %-28s inputs=%d links=%d widgets=%d"
                     % (nid, node["class_type"], len(node["inputs"]), len(links), len(vals)))
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def resolve_workflow(path_or_name: str) -> str:
    if os.path.isfile(path_or_name):
        return path_or_name
    comfy_root = os.environ.get("COMFYUI_ROOT",
                                r"D:\BaiduSyncdisk\wmj_work\XJ\ComfyUI")
    cand = os.path.join(comfy_root, "user", "default", "workflows", path_or_name)
    if os.path.isfile(cand):
        return cand
    raise SystemExit("找不到工作流: %s（也不在 %s）" % (path_or_name, cand))


def main(argv=None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    ap = argparse.ArgumentParser(description="ComfyUI UI workflow JSON -> API prompt JSON")
    ap.add_argument("workflow", help="UI 工作流 JSON 路径，或 user/default/workflows 下的文件名")
    ap.add_argument("--server", default=DEFAULT_SERVER, help="ComfyUI 地址（默认 %(default)s）")
    ap.add_argument("--out", default=None, help="输出 API prompt JSON 路径；省略则打印到 stdout")
    ap.add_argument("--set", dest="assignments", action="append", default=[],
                    metavar="NODE.FIELD=VALUE", help="覆盖输入值，可多次")
    ap.add_argument("--object-info", default=None, help="离线 object_info JSON（默认在线拉取）")
    ap.add_argument("--quiet", action="store_true", help="不打印节点转换明细")
    args = ap.parse_args(argv)

    wf_path = resolve_workflow(args.workflow)
    workflow = load_workflow(wf_path)
    if args.object_info:
        object_info = load_workflow(args.object_info)
    else:
        object_info = fetch_object_info(args.server)

    log = (lambda *a, **k: None) if args.quiet else print
    log("workflow : %s" % wf_path)
    log("server   : %s (object_info: %d node types)" % (args.server, len(object_info)))

    prompt, warnings = ui_to_api(workflow, object_info, log=log)
    warnings += apply_overrides(prompt, args.assignments, object_info)

    if not args.quiet:
        log("converted nodes: %d" % len(prompt))
        log(summarize(prompt))
    for w in warnings:
        log("[warn] " + w)

    text = json.dumps(prompt, ensure_ascii=False, indent=2)
    if args.out:
        os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(text + "\n")
        log("wrote: %s (%d nodes)" % (args.out, len(prompt)))
    else:
        print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
