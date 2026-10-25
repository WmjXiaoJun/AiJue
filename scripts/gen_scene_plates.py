# -*- coding: utf-8 -*-
"""生成 12 张 2D 动漫场景绘景 + 3 张 3D 渲染风 plate（SDXL / ComfyUI）。

输出: outputs/scene_plates/{id}.png (1280x720) + meta.json + contact_sheet.png
用法:
  python scripts/gen_scene_plates.py [--skip-existing] [--only id1,id2]
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, REPO)
sys.path.insert(0, os.path.join(REPO, "imagegen"))

from imagegen import comfy  # noqa: E402
from imagegen.workflow import (  # noqa: E402
    DEFAULT_CFG, DEFAULT_CKPT, DEFAULT_SAMPLER, DEFAULT_SCHEDULER, DEFAULT_STEPS,
    build_workflow,
)

OUT_DIR = os.path.join(REPO, "outputs", "scene_plates")
GEN_W, GEN_H = 1344, 768      # 16:9-ish，生成后中心裁到 1280x720（避免拉伸）
OUT_W, OUT_H = 1280, 720
SEED_BASE = 4001

COMMON = ("anime background, no humans, empty scene, detailed background, scenery, cinematic lighting, "
          "2D anime film background, clean lineart, vibrant colors, wide shot, "
          "foreground floor space for character placement, horizon around mid-frame")
NEGATIVE = ("people, human, person, character, 1girl, 1boy, crowd, text, watermark, logo, "
            "letters, signature, lowres, blurry, deformed, extra limbs")

SCENES = [
    ("late_night_desk", "a late-night talk show office set with a large desk, a microphone on the desk, "
                        "papers and a coffee mug, a warm desk lamp, a big window showing a neon city skyline at night, "
                        "an empty chair behind the desk, dark blue studio mood lighting"),
    ("roundtable_studio", "a bright round-table TV talk studio with a large circular wooden table in the center, "
                          "modern chairs around the table, warm circular ceiling lights above the table, "
                          "a big screen in the background, a camera on a tripod at the side, "
                          "audience seats in soft shadow, empty studio"),
    ("taskmaster_stage", "a colorful comedy task-show stage with a podium holding a golden trophy, "
                         "a big red button on a pedestal, spotlights and colorful stage lighting, "
                         "plain colorful backdrop without text, empty stage"),
    ("history_study", "a historical Chinese study room lit by candlelight, tall wooden bookshelves full of scrolls, "
                      "an antique wooden desk with bamboo slips and a brush, oil paintings and a small fireplace, "
                      "warm candle glow, floating dust, empty room"),
    ("debate_hall", "a formal debate hall with two wooden podiums facing each other, dark wooden panel walls, "
                    "stage lighting from above, velvet curtains, empty stage, dramatic lighting"),
    ("mystery_room", "an anime detective crime-scene room at night: yellow police tape stretched across the room, "
                     "a white chalk body outline on the wooden floor, scattered evidence markers and a magnifying glass, "
                     "a detective corkboard with photos on the wall, an overturned chair, a single hanging lamp casting "
                     "dramatic light, a broken window with moonlight, film-noir anime style, empty of people"),
    ("treasure_museum", "a Chinese national-treasure museum hall with glass display cases holding ancient bronze "
                        "ding vessels and porcelain, marble floor reflections, golden spotlights, red silk ropes, "
                        "tall columns, empty and quiet"),
    ("kitchen_warm", "a warm cozy kitchen in slow-living anime style, wooden counter with vegetables and a kettle, "
                     "hanging pots and herbs, sunlight streaming through a window, steam from a pot, "
                     "plants on the windowsill, empty room"),
    ("stage_music", "a concert stage with a microphone stand in the center, colorful stage lights and light beams, "
                    "light smoke in the air, big speakers on both sides, glossy dark floor, empty stage"),
    ("talk_show_sofa", "a warm TV talk-show set with two large comfortable fabric sofas facing each other at an angle, "
                       "a low wooden coffee table between them with two mugs, a soft textured rug, a warm floor lamp, "
                       "a dark studio background with soft bokeh lights, wide shot, empty set"),
    ("alley_night", "a narrow night street alley with neon signs without readable text, wet asphalt with colorful "
                    "reflections, a vending machine, steam from vents, distant city lights, no people"),
    ("classroom_history", "a history classroom with wooden desks and chairs in rows, a large world map on the "
                          "blackboard without text, sunlight through tall windows, chalk dust in the air, empty room"),
    ("3d_roundtable", "stylized 3D render of a modern roundtable TV studio, isometric three-quarter view, "
                      "ai3d-eevee cinematic lighting, soft shadows, clean glossy geometry, large LED screen, "
                      "no people"),
    ("3d_stage", "stylized 3D render of a TV show stage, wide cinematic shot, volumetric stage lighting, "
                 "glossy reflective floor, simple geometric set design, no people"),
    ("3d_study", "stylized 3D render of a cozy historical study room, isometric view, warm candle and lamp lighting, "
                 "wooden furniture and bookshelves, cinematic depth of field, no people"),
]
STYLE_3D = {"3d_roundtable", "3d_stage", "3d_study"}


def log(msg=""):
    print("[%s] %s" % (time.strftime("%H:%M:%S"), msg), flush=True)


def center_crop_resize(src: str, dst: str):
    im = Image.open(src).convert("RGB")
    # 先按 cover 缩放到 >= 1280x720，再中心裁切
    scale = max(OUT_W / im.width, OUT_H / im.height)
    nw, nh = max(OUT_W, int(round(im.width * scale))), max(OUT_H, int(round(im.height * scale)))
    im = im.resize((nw, nh), Image.LANCZOS)
    left = (nw - OUT_W) // 2
    top = (nh - OUT_H) // 2
    im = im.crop((left, top, left + OUT_W, top + OUT_H))
    im.save(dst)


def scene_prompt(sid: str, desc: str) -> tuple:
    if sid in STYLE_3D:
        pos = ("masterpiece, best quality, stylized 3D render, ai3d render, octane render, "
               "cinematic lighting, 8k, " + desc)
        neg = ("people, human, character, text, watermark, logo, letters, lowres, blurry, deformed, "
               "2d anime, flat illustration")
    else:
        pos = "masterpiece, best quality, " + COMMON + ", " + desc
        neg = NEGATIVE
    return pos, neg


def main(argv=None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    ap = argparse.ArgumentParser()
    ap.add_argument("--skip-existing", action="store_true")
    ap.add_argument("--only", default=None, help="只生成指定 id（逗号分隔）")
    ap.add_argument("--timeout", type=float, default=900.0)
    args = ap.parse_args(argv)

    os.makedirs(OUT_DIR, exist_ok=True)
    comfy.ensure_comfy(comfy.DEFAULT_SERVER, comfy.DEFAULT_COMFY_ROOT, False, log)
    object_info = comfy.fetch_object_info(comfy.DEFAULT_SERVER)
    ckpts = comfy.get_checkpoints(comfy.DEFAULT_SERVER, object_info)
    log("checkpoint: %s (%s)" % (DEFAULT_CKPT, "OK" if DEFAULT_CKPT in ckpts else "MISSING"))
    if DEFAULT_CKPT not in ckpts:
        raise SystemExit("checkpoint 不在 ComfyUI 列表: %s" % ckpts[:5])

    only = set(x.strip() for x in args.only.split(",")) if args.only else None
    scenes = [(i, d) for i, d in SCENES if (only is None or i in only)]
    work = []
    for idx, (sid, desc) in enumerate(scenes):
        dst = os.path.join(OUT_DIR, sid + ".png")
        if args.skip_existing and os.path.isfile(dst):
            log("[skip] %s" % dst)
            continue
        work.append({"id": sid, "desc": desc, "seed": SEED_BASE + SCENES.index((sid, desc)),
                     "dst": dst, "prefix": "zaopianju_scenes/%s" % sid})

    t_all = time.time()
    # 一次性入队，避免与其它 H3 任务交错导致反复换模型
    for w in work:
        pos, neg = scene_prompt(w["id"], w["desc"])
        w["prompt"], w["negative"] = pos, neg
        wf = build_workflow(pos, neg, GEN_W, GEN_H, seed=w["seed"],
                            filename_prefix=w["prefix"], ckpt_name=DEFAULT_CKPT,
                            steps=DEFAULT_STEPS, cfg=DEFAULT_CFG,
                            sampler_name=DEFAULT_SAMPLER, scheduler=DEFAULT_SCHEDULER)
        w["t0"] = time.time()
        w["prompt_id"] = comfy.submit(comfy.DEFAULT_SERVER, wf, log)
        log("[queue] %-20s seed=%d id=%s" % (w["id"], w["seed"], w["prompt_id"][:8]))

    results = []
    for w in work:
        rec = comfy.wait_prompt(comfy.DEFAULT_SERVER, w["prompt_id"], args.timeout, log)
        imgs = comfy.fetch_images(rec)
        if not imgs:
            raise RuntimeError("%s 没有返回图片" % w["id"])
        imgs.sort(key=lambda it: it["filename"])
        raw = w["dst"] + ".raw.png"
        comfy.save_image(comfy.DEFAULT_SERVER, comfy.DEFAULT_COMFY_ROOT, imgs[0], raw, log)
        center_crop_resize(raw, w["dst"])
        os.remove(raw)
        gen = round(time.time() - w["t0"], 1)
        results.append({"id": w["id"], "file": w["dst"], "seed": w["seed"],
                        "prompt": w["prompt"], "negative": w["negative"],
                        "gen_size": [GEN_W, GEN_H], "out_size": [OUT_W, OUT_H],
                        "gen_sec": gen})
        log("[done] %-20s %5.1fs -> %s" % (w["id"], gen, w["dst"]))

    # 合并 meta（增量跑时保留旧条目）
    meta_path = os.path.join(OUT_DIR, "meta.json")
    meta = {"model": DEFAULT_CKPT, "steps": DEFAULT_STEPS, "cfg": DEFAULT_CFG,
            "sampler": DEFAULT_SAMPLER, "scheduler": DEFAULT_SCHEDULER,
            "gen_size": [GEN_W, GEN_H], "out_size": [OUT_W, OUT_H],
            "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"), "images": []}
    if os.path.isfile(meta_path):
        try:
            old = json.load(open(meta_path, encoding="utf-8"))
            meta["images"] = old.get("images") or []
        except Exception:
            pass
    merged = {r["id"]: r for r in meta["images"]}
    for r in results:
        merged[r["id"]] = r
    meta["images"] = list(merged.values())
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)

    # 拼 contact sheet（3 列）
    rows = (len(meta["images"]) + 2) // 3
    cell_w, cell_h = 426, 240
    from PIL import ImageDraw, ImageFont
    font = ImageFont.truetype("C:/Windows/Fonts/msyh.ttc", 16)
    sheet = Image.new("RGB", (cell_w * 3, (cell_h + 24) * rows), (12, 14, 20))
    d = ImageDraw.Draw(sheet)
    for i, r in enumerate(sorted(meta["images"], key=lambda x: x["id"])):
        p = r["file"] if os.path.isabs(r["file"]) else os.path.join(REPO, r["file"])
        if not os.path.isfile(p):
            continue
        im = Image.open(p).convert("RGB").resize((cell_w, cell_h), Image.LANCZOS)
        x, y = (i % 3) * cell_w, (i // 3) * (cell_h + 24)
        sheet.paste(im, (x, y))
        d.text((x + 6, y + cell_h + 4), r["id"], font=font, fill=(200, 205, 215))
    sheet_path = os.path.join(OUT_DIR, "contact_sheet.png")
    sheet.save(sheet_path)
    log("[sheet] %s" % sheet_path)
    print(json.dumps({"ok": True, "outdir": OUT_DIR, "count": len(results),
                      "elapsed_sec": round(time.time() - t_all, 1),
                      "files": [r["file"] for r in results],
                      "meta": meta_path, "contact_sheet": sheet_path}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
