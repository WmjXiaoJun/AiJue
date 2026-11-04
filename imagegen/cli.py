# -*- coding: utf-8 -*-
"""imagegen CLI：从角色 spec 生成 SDXL 动漫概念图（ComfyUI 本地）。

用法
----
  python -m imagegen.cli generate --spec contract/characters/laozhou.json \
      --outdir outputs/concepts/laozhou --mode portrait --style anime --seed 42
  python -m imagegen.cli generate --spec <json> --outdir <dir> --mode all   # 立绘+三视图+表情+sheet
  python -m imagegen.cli check        # 检查 ComfyUI 服务与 checkpoint 是否就绪

产物（--mode all）：
  portrait.png (832x1216)  three_view/{front,side,three_quarter}.png (1024x1024)
  expressions/{neutral,happy,surprised,thinking}.png  sheet.png (1600x900)  meta.json
CLI 最后一行打印 JSON 摘要；失败 exit 1。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

from . import comfy
from .prompt import build_prompt, load_spec, STYLE_PRESETS
from .sheet import compose_sheet
from .workflow import (
    DEFAULT_CFG,
    DEFAULT_CKPT,
    DEFAULT_SAMPLER,
    DEFAULT_SCHEDULER,
    DEFAULT_STEPS,
    PORTRAIT_SIZE,
    SQUARE_SIZE,
    build_ipadapter_workflow,
    build_workflow,
    validate_workflow,
)

VIEWS = ["front", "side", "three_quarter"]
EXPRESSIONS = ["neutral", "happy", "surprised", "thinking"]
MODES = ["portrait", "three_view", "expressions", "scene", "sheet", "all"]
HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(HERE)
DEFAULT_OUT_ROOT = os.path.join(REPO_ROOT, "outputs", "concepts")


# --------------------------------------------------------------------------- #
# 参数 / 计划
# --------------------------------------------------------------------------- #
def parse_size(text: str):
    try:
        w, h = text.lower().split("x")
        w, h = int(w), int(h)
    except Exception:
        raise SystemExit("--size 需要 WxH 形式（如 832x1216），收到: %r" % text)
    if w % 8 or h % 8:
        raise SystemExit("--size 宽高必须是 8 的倍数: %dx%d" % (w, h))
    return w, h


def build_plan(args, spec: dict) -> list:
    """mode -> 待生成图片列表（tag/relpath/seed/composition/expression/size）。"""
    def size_for(composition):
        if args.size:
            return parse_size(args.size)
        return PORTRAIT_SIZE if composition == "portrait" else SQUARE_SIZE

    jobs = []
    base = int(args.seed)

    def add(tag, relpath, composition, expression=None, extra_seed=0):
        jobs.append({
            "tag": tag,
            "relpath": relpath,
            "seed": base + extra_seed,
            "composition": composition,
            "expression": expression,
            "size": size_for(composition),
        })

    n = 0
    if args.mode in ("portrait", "all"):
        count = max(1, int(args.count))
        for i in range(count):
            tag = "portrait" if i == 0 else "portrait_%d" % (i + 1)
            add(tag, tag + ".png", "portrait", extra_seed=n)
            n += 1
    if args.mode in ("three_view", "all"):
        for v in VIEWS:
            add(v, "three_view/%s.png" % v, v, extra_seed=n)
            n += 1
    if args.mode in ("expressions", "all"):
        for e in EXPRESSIONS:
            add(e, "expressions/%s.png" % e, "expressions", expression=e, extra_seed=n)
            n += 1
    if args.mode in ("scene", "all"):
        add("scene", "scene.png", "scene", extra_seed=n)
        n += 1
    if getattr(args, "views", None):
        want = {v.strip() for v in str(args.views).split(",") if v.strip()}
        jobs = [j for j in jobs if j["tag"] in want]
    override = getattr(args, "composition", None)
    if override:
        for job in jobs:  # V45：构图覆盖（scene → scene_wide 等），尺寸仍按 --size
            job["composition"] = override
    return jobs


# --------------------------------------------------------------------------- #
# 生成
# --------------------------------------------------------------------------- #
def generate_one(job, spec, args, object_info, outdir, log):
    """生成一张图；返回结果 dict。"""
    dest = os.path.join(outdir, job["relpath"])
    if args.skip_existing and os.path.isfile(dest):
        from PIL import Image
        with Image.open(dest) as im:
            size = im.size
        log("[skip] 已存在 %s (%dx%d)" % (job["relpath"], size[0], size[1]))
        return {"tag": job["tag"], "path": dest, "seed": job["seed"],
                "width": size[0], "height": size[1], "skipped": True, "gen_sec": 0.0}

    pr = build_prompt(spec, style=args.style, prompt_extra=args.prompt_extra,
                      composition=job["composition"], expression=job["expression"])
    if getattr(args, "negative_extra", ""):
        pr["negative"] = pr["negative"] + ", " + args.negative_extra.strip()
    w, h = job["size"]
    prefix = "%s/%s_%s" % (args.subfolder.strip("/\\"), spec["id"], job["tag"])
    ref_rel = getattr(args, "ref_rel", None)
    if ref_rel:
        # 一致性模式：SDXL txt2img + IP-Adapter（参考图锁脸/发色/服装）
        wf = build_ipadapter_workflow(
            positive=pr["positive"], negative=pr["negative"], ref_image=ref_rel,
            width=w, height=h, seed=job["seed"], filename_prefix=prefix,
            ckpt_name=args.ckpt, steps=args.steps, cfg=args.cfg,
            sampler_name=args.sampler, scheduler=args.scheduler,
            ipadapter_preset=args.ip_adapter_preset,
            ipadapter_weight=args.ip_adapter_strength,
            ipadapter_end=args.ip_adapter_end_at,
            ipadapter_weight_type=args.ip_adapter_weight_type,
            lora_name=getattr(args, "lora", None) or "",
            lora_strength=float(getattr(args, "lora_strength", 0.9)),
        )
    else:
        wf = build_workflow(
            positive=pr["positive"], negative=pr["negative"], width=w, height=h,
            seed=job["seed"], filename_prefix=prefix, ckpt_name=args.ckpt,
            steps=args.steps, cfg=args.cfg, sampler_name=args.sampler,
            scheduler=args.scheduler,
            lora_name=getattr(args, "lora", None) or "",
            lora_strength=float(getattr(args, "lora_strength", 0.9)),
        )
    for warn in validate_workflow(wf, object_info):
        log("[warn] " + warn)

    if args.dry_run:
        log("[dry-run] %s seed=%d %dx%d prefix=%s" % (job["tag"], job["seed"], w, h, prefix))
        return {"tag": job["tag"], "path": None, "seed": job["seed"],
                "width": w, "height": h, "dry_run": True, "gen_sec": 0.0,
                "prompt": pr["positive"], "negative": pr["negative"],
                "ref": getattr(args, "ref", None), "workflow": wf}

    t0 = time.time()
    pid = comfy.submit(args.server, wf, log)
    rec = comfy.wait_prompt(args.server, pid, args.timeout, log)
    imgs = comfy.fetch_images(rec)
    if not imgs:
        raise RuntimeError("ComfyUI 没有返回图片: %s" % json.dumps(rec.get("outputs"), ensure_ascii=False)[:800])
    imgs.sort(key=lambda it: it["filename"])
    comfy.save_image(args.server, args.comfy_root, imgs[0], dest, log)
    gen_sec = round(time.time() - t0, 1)

    from PIL import Image
    with Image.open(dest) as im:
        iw, ih = im.size
    if (iw, ih) != (w, h):
        log("[warn] 产物尺寸 %dx%d 与请求 %dx%d 不一致" % (iw, ih, w, h))
    log("[done] %-16s %4.1fs  %dx%d  seed=%d" % (job["tag"], gen_sec, iw, ih, job["seed"]))
    rec = {"tag": job["tag"], "path": dest, "seed": job["seed"],
           "width": iw, "height": ih, "gen_sec": gen_sec,
           "prompt": pr["positive"], "negative": pr["negative"],
           "composition": job["composition"], "expression": job["expression"]}
    if ref_rel:
        rec["ref"] = args.ref
        rec["ip_adapter"] = {"preset": args.ip_adapter_preset,
                             "weight": args.ip_adapter_strength,
                             "start_at": 0.0, "end_at": args.ip_adapter_end_at}
    return rec


def collect_files(outdir, spec_id):
    """扫描 outdir，返回 contract 要求的 files 映射。"""
    files = {
        "portrait": None,
        "three_view": {},
        "expressions": {},
        "scene": None,
        "sheet": None,
    }
    p = os.path.join(outdir, "portrait.png")
    if os.path.isfile(p):
        files["portrait"] = p
    extra = [os.path.join(outdir, f) for f in sorted(os.listdir(outdir))
             if f.startswith("portrait_") and f.endswith(".png")] if os.path.isdir(outdir) else []
    if extra:
        files["portrait_variants"] = extra
    for v in VIEWS:
        p = os.path.join(outdir, "three_view", v + ".png")
        if os.path.isfile(p):
            files["three_view"][v] = p
    for e in EXPRESSIONS:
        p = os.path.join(outdir, "expressions", e + ".png")
        if os.path.isfile(p):
            files["expressions"][e] = p
    p = os.path.join(outdir, "sheet.png")
    if os.path.isfile(p):
        files["sheet"] = p
    p = os.path.join(outdir, "scene.png")
    if os.path.isfile(p):
        files["scene"] = p
    return files


# --------------------------------------------------------------------------- #
# 子命令
# --------------------------------------------------------------------------- #
def cmd_check(args):
    comfy.ensure_comfy(args.server, args.comfy_root, not args.no_start, print)
    stats = comfy.http_json(args.server.rstrip("/") + "/system_stats")
    object_info = comfy.fetch_object_info(args.server)
    ckpts = comfy.get_checkpoints(args.server, object_info)

    def _file_len(path):
        return os.path.getsize(path) if os.path.isfile(path) else 0

    info = {
        "ok": args.ckpt in ckpts,
        "server": args.server,
        "comfyui_version": (stats.get("system") or {}).get("comfyui_version"),
        "devices": [(d.get("name"), d.get("vram_total")) for d in stats.get("devices", [])],
        "checkpoint": args.ckpt,
        "checkpoint_ready": args.ckpt in ckpts,
        "checkpoints": ckpts,
        "ipadapter": {
            "nodes_ready": ("IPAdapterUnifiedLoader" in object_info and
                            "IPAdapterAdvanced" in object_info),
            "model": "models/ipadapter/ip-adapter-plus_sdxl_vit-h.safetensors",
            "model_bytes": _file_len(os.path.join(
                args.comfy_root, "models", "ipadapter",
                "ip-adapter-plus_sdxl_vit-h.safetensors")),
            "clip_vision": "models/clip_vision/CLIP-ViT-H-14-laion2B-s32B-b79K.safetensors",
            "clip_vision_bytes": _file_len(os.path.join(
                args.comfy_root, "models", "clip_vision",
                "CLIP-ViT-H-14-laion2B-s32B-b79K.safetensors")),
        },
    }
    if not info["ipadapter"]["nodes_ready"]:
        info["ok"] = False
    print(json.dumps(info, ensure_ascii=False))
    return 0 if info["ok"] else 1


def cmd_generate(args):
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    spec = load_spec(args.spec)
    spec_id = str(spec["id"])
    outdir = args.outdir or os.path.join(DEFAULT_OUT_ROOT, spec_id)
    outdir = os.path.abspath(outdir)
    os.makedirs(outdir, exist_ok=True)

    t_start = time.time()
    summary = {
        "ok": False, "script": "imagegen.cli", "id": spec_id,
        "mode": args.mode, "style": args.style, "outdir": outdir,
        "model": args.ckpt, "seed": args.seed, "size": args.size,
        "images": [], "files": {}, "elapsed_sec": 0.0,
    }
    try:
        comfy.ensure_comfy(args.server, args.comfy_root, not args.no_start, print)
        object_info = comfy.fetch_object_info(args.server)
        ckpts = comfy.get_checkpoints(args.server, object_info)
        if args.ckpt not in ckpts:
            print("[warn] checkpoint 不在列表里: %s（现有: %s）" % (args.ckpt, ckpts[:10]))
        # 一致性模式：先把参考图（通常是 portrait.png）拷进 ComfyUI/input
        if getattr(args, "ref", None):
            ref_name = "%s_ref_%s" % (spec_id, os.path.basename(args.ref))
            args.ref_rel = comfy.copy_input_image(
                args.comfy_root, args.ref, subfolder=args.subfolder.strip("/\\"),
                name=ref_name, log=print)
            print("[consistency] IP-Adapter 参考图: %s (weight=%.2f end_at=%.2f preset=%s)"
                  % (args.ref, args.ip_adapter_strength, args.ip_adapter_end_at,
                     args.ip_adapter_preset))
        plan = build_plan(args, spec)
        if not plan and args.mode not in ("sheet", "all"):
            raise SystemExit("没有需要生成的图（mode=%s）" % args.mode)
        print("[plan] %s mode=%s style=%s seed=%s 共 %d 张: %s"
              % (spec_id, args.mode, args.style, args.seed, len(plan),
                 ", ".join(j["tag"] for j in plan)))
        for job in plan:
            summary["images"].append(generate_one(job, spec, args, object_info, outdir, print))

        if not args.dry_run and args.mode in ("sheet", "all"):
            print("[sheet] 合成设定图 ...")
            sp = compose_sheet(spec, outdir, seed=args.seed, style=args.style, model=args.ckpt)
            print("[sheet] %s" % sp)

        summary["files"] = collect_files(outdir, spec_id)
        summary["ok"] = True
    except BaseException as e:  # noqa: BLE001  失败也要打印 JSON 摘要
        summary["ok"] = False
        summary["error"] = "%s: %s" % (type(e).__name__, e)
        print("[error] %s" % summary["error"])
        if isinstance(e, SystemExit) and e.code not in (None, 0):
            summary["exit_code"] = e.code
    finally:
        summary["elapsed_sec"] = round(time.time() - t_start, 1)
        # 增量模式（three_view/sheet 单独跑）时保住历史 images/prompt 记录
        prev_images, prev_prompt = [], {}
        prev_meta_path = os.path.join(outdir, "meta.json")
        if os.path.isfile(prev_meta_path):
            try:
                with open(prev_meta_path, "r", encoding="utf-8") as f:
                    prev = json.load(f)
                prev_images = prev.get("images") or []
                prev_prompt = prev.get("prompt") or {}
            except Exception:
                pass
        merged = {str(img.get("tag")): img for img in prev_images}
        for img in summary["images"]:
            merged[str(img.get("tag"))] = img
        merged_prompt = dict(prev_prompt)
        for img in summary["images"]:
            if img.get("prompt"):
                merged_prompt[str(img.get("tag"))] = img["prompt"]
        summary["_merged_images"] = list(merged.values())
        summary["_merged_prompt"] = merged_prompt
        meta = {
            "id": spec_id,
            "name": spec.get("name"),
            "title": spec.get("title"),
            "persona": spec.get("persona"),
            "mode": args.mode,
            "style": args.style,
            "model": args.ckpt,
            "steps": args.steps,
            "cfg": args.cfg,
            "sampler": args.sampler,
            "scheduler": args.scheduler,
            "seed": args.seed,
            "size": args.size or {"portrait": list(PORTRAIT_SIZE), "other": list(SQUARE_SIZE)},
            "negative": build_prompt(spec, style=args.style)["negative"],
            "prompt": summary["_merged_prompt"],
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "elapsed_sec": summary["elapsed_sec"],
            "files": summary["files"],
            "images": [{k: v for k, v in img.items() if k not in ("workflow",)} for img in summary["_merged_images"]],
        }
        if getattr(args, "ref", None):
            meta["ip_adapter"] = {
                "enabled": True,
                "ref": os.path.abspath(args.ref),
                "ref_comfy_input": getattr(args, "ref_rel", None),
                "preset": args.ip_adapter_preset,
                "weight": args.ip_adapter_strength,
                "weight_type": args.ip_adapter_weight_type,
                "start_at": 0.0,
                "end_at": args.ip_adapter_end_at,
                "ckpt": args.ckpt,
            }
            summary["ip_adapter"] = meta["ip_adapter"]
        if not args.dry_run:
            meta_path = os.path.join(outdir, "meta.json")
            with open(meta_path, "w", encoding="utf-8") as f:
                json.dump(meta, f, ensure_ascii=False, indent=2)
            summary["meta"] = meta_path
        # 精简 summary 里的 image 条目（去掉超长 workflow/prompt 重复）
        summary["images"] = [
            {k: v for k, v in img.items() if k in ("tag", "path", "seed", "width", "height", "gen_sec", "skipped")}
            for img in summary["images"]
        ]

    print(json.dumps(summary, ensure_ascii=False))
    return 0 if summary.get("ok") else 1


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="imagegen", description="SDXL 动漫角色概念图生成（ComfyUI 本地）")
    sub = ap.add_subparsers(dest="cmd")

    def add_common(p):
        p.add_argument("--server", default=comfy.DEFAULT_SERVER, help="ComfyUI 地址")
        p.add_argument("--comfy-root", default=comfy.DEFAULT_COMFY_ROOT, help="ComfyUI 根目录")
        p.add_argument("--no-start", action="store_true", help="不自动启动 ComfyUI")
        p.add_argument("--ckpt", default=DEFAULT_CKPT, help="checkpoint 文件名")

    p_gen = sub.add_parser("generate", help="生成角色概念图")
    p_gen.add_argument("--spec", required=True, help="角色 spec JSON（contract/characters/*.json）")
    p_gen.add_argument("--outdir", default=None, help="输出目录（默认 outputs/concepts/{id}）")
    p_gen.add_argument("--mode", default="portrait", choices=MODES, help="生成模式（默认 portrait）")
    p_gen.add_argument("--style", default="anime", choices=list(STYLE_PRESETS.keys()),
                       help="提示词风格（默认 anime）")
    p_gen.add_argument("--seed", type=int, default=42, help="基础 seed（默认 42，同一角色内 seed+k）")
    p_gen.add_argument("--size", default=None, help="覆盖尺寸 WxH（默认 portrait 832x1216 / 其它 1024x1024）")
    p_gen.add_argument("--count", type=int, default=1, help="portrait 张数（默认 1）")
    p_gen.add_argument("--prompt-extra", default="", help="追加 tag（逗号分隔）")
    p_gen.add_argument("--steps", type=int, default=DEFAULT_STEPS)
    p_gen.add_argument("--cfg", type=float, default=DEFAULT_CFG)
    p_gen.add_argument("--sampler", default=DEFAULT_SAMPLER)
    p_gen.add_argument("--scheduler", default=DEFAULT_SCHEDULER)
    p_gen.add_argument("--subfolder", default="zaopianju_concepts",
                       help="ComfyUI output 子目录（默认 zaopianju_concepts）")
    p_gen.add_argument("--timeout", type=float, default=300.0, help="单图等待超时秒数（首图含模型加载）")
    p_gen.add_argument("--skip-existing", action="store_true", help="已存在的图跳过（断点续跑）")
    p_gen.add_argument("--dry-run", action="store_true", help="只打印 prompt/workflow，不提交")
    # ---- 角色一致性（IP-Adapter）----
    p_gen.add_argument("--ref", default=None,
                       help="参考图（通常 outputs/concepts/<id>/portrait.png）；给了就启用 IP-Adapter 一致性模式")
    p_gen.add_argument("--ip-adapter-strength", type=float, default=0.65,
                       help="IPAdapter weight（推荐 0.6-0.8，默认 0.65）")
    p_gen.add_argument("--ip-adapter-end-at", type=float, default=0.85,
                       help="IPAdapter end_at（默认 0.85，越大越锁脸但可能僵）")
    p_gen.add_argument("--ip-adapter-preset", default="PLUS (high strength)",
                       help="IPAdapterUnifiedLoader preset（默认 PLUS (high strength)）")
    p_gen.add_argument("--ip-adapter-weight-type", default="linear",
                       help="IPAdapter weight_type（linear/ease in/ease out/style transfer/composition）")
    p_gen.add_argument("--negative-extra", default="",
                       help="追加 negative tag（逗号分隔，给一致性模式压背景/风格漂移用）")
    p_gen.add_argument("--composition", default=None,
                       help="V45：覆盖构图预设（如 scene_wide；默认按 mode 决定）")
    p_gen.add_argument("--lora", default=None,
                       help="V45：追加 LoRA（models/loras 下的文件名，如 GhibliArt.safetensors）")
    p_gen.add_argument("--lora-strength", type=float, default=0.9, help="LoRA strength_model")
    p_gen.add_argument("--views", default=None,
                       help="只生成指定视图（逗号分隔，如 side / front,side），配合 --mode three_view")
    add_common(p_gen)
    p_gen.set_defaults(func=cmd_generate)

    p_check = sub.add_parser("check", help="检查 ComfyUI 与 checkpoint 就绪状态")
    add_common(p_check)
    p_check.set_defaults(func=cmd_check)
    return ap


def main(argv=None) -> int:
    ap = build_parser()
    args = ap.parse_args(argv)
    if not getattr(args, "func", None):
        ap.print_help()
        return 2
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
