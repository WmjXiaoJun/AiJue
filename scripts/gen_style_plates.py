# -*- coding: utf-8 -*-
"""12 种风格场景 plate（SDXL / ComfyUI）——task-47

按 contract/style_presets.json 的 12 个 preset 生成差异化 1280x720 场景绘景：
输出 outputs/scene_plates/{scene}__{suffix}.png + meta_styles.json + contact_sheet_styles.png

用法:
  python scripts/gen_style_plates.py [--skip-existing] [--only late_night_desk__cinematic,...]
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

from PIL import Image, ImageDraw, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, REPO)
sys.path.insert(0, os.path.join(REPO, "imagegen"))

from imagegen import comfy  # noqa: E402
from imagegen.workflow import (  # noqa: E402
    DEFAULT_CFG, DEFAULT_CKPT, DEFAULT_SAMPLER, DEFAULT_SCHEDULER, DEFAULT_STEPS, build_workflow,
)

OUT_DIR = os.path.join(REPO, "outputs", "scene_plates")
PRESETS_PATH = os.path.join(REPO, "contract", "style_presets.json")
GEN_W, GEN_H = 1344, 768
OUT_W, OUT_H = 1280, 720
SEED_BASE = 7001

# (scene_id, preset_id)
JOBS = [
    ("late_night_desk", "cinematic_3d"),
    ("stage_music", "neon_stage_3d"),
    ("taskmaster_stage", "game_show_3d"),
    ("roundtable_studio", "roundtable_3d"),
    ("stage_music", "sketch_3d"),
    ("talk_show_sofa", "warm_talk_3d"),
    ("kitchen_warm", "clay_life_3d"),
    ("mystery_room", "noir_mystery_3d"),
    ("stage_music", "retro_standup_2d"),
    ("history_study", "history_ink_2d"),
    ("debate_hall", "debate_ink_2d"),
    ("treasure_museum", "museum_gold_2d"),
]

SCENE_DESC = {
    "late_night_desk": "a late-night talk show office set with a large desk, microphone, coffee mug, papers, a warm desk lamp, a big window with a neon city skyline at night, an empty chair behind the desk",
    "stage_music": "a concert stage with a microphone stand in the center, light beams and haze, big speakers, glossy reflective floor, empty stage",
    "taskmaster_stage": "a colorful comedy task-show stage with a podium holding a golden trophy, a big red button on a pedestal, spotlights, simple backdrop without text, empty stage",
    "roundtable_studio": "a round-table TV talk studio with a large circular table, chairs around it, a big screen behind, ceiling light rig above, camera on a tripod at the side, empty studio",
    "talk_show_sofa": "a cozy TV talk-show set with two large comfortable sofas facing each other, a low wooden coffee table with two mugs, a soft rug, a warm floor lamp, gentle bokeh lights",
    "kitchen_warm": "a warm cozy kitchen with a wooden counter, vegetables and a kettle, hanging pots and herbs, sunlight through a window, steam from a pot, plants on the windowsill, empty room",
    "mystery_room": "a detective crime-scene room at night with yellow police tape, a chalk outline on the floor, scattered evidence markers, a detective corkboard, a single hanging lamp, a broken window with moonlight",
    "history_study": "a historical Chinese study room lit by candlelight, tall wooden bookshelves full of scrolls, an antique wooden desk with bamboo slips and a brush, a small fireplace, warm candle glow",
    "debate_hall": "a formal debate hall with two wooden podiums facing each other, dark wooden panel walls, velvet curtains, dramatic spotlights from above, polished wooden stage floor",
    "treasure_museum": "a national-treasure museum hall with glass display cases holding ancient bronze vessels and porcelain, marble floor reflections, golden spotlights, red silk ropes, tall columns",
}

PALETTE_WORDS = {
    "warm_teal": "warm amber highlights with teal shadows, teal-orange cinematic color grading",
    "neon_magenta_cyan": "neon magenta and cyan lighting, saturated neon color palette, strong backlight",
    "candy_highsat": "candy-bright high-saturation colors, vivid pink yellow and cyan, glossy props",
    "dark_amber_teal": "dark moody palette, deep teal shadows with warm amber accents",
    "theatre_red_gold": "theatre red and gold palette, deep crimson curtains, golden spotlights",
    "warm_amber": "warm amber and honey tones, soft warm color palette",
    "pastel_earth": "pastel earth tones, terracotta beige and sage, handmade textures",
    "noir_blue_amber": "cold blue shadows with narrow amber highlights, high-contrast noir palette, wet reflections",
    "vhs_warm": "warm faded yellow color cast, soft orange highlights, subtle vintage color grading",
    "ink_amber": "ink black and amber sepia palette, rice paper texture",
    "ink_red_gold": "black ink with vermilion red and gold accents, dramatic brush shadows",
    "gold_jade": "gold and jade-green palette, luxurious warm gold, ornate details",
}

PRESET_EXTRA = {
    "cinematic_3d": "cinematic anamorphic look, film still, shallow depth of field",
    "neon_stage_3d": "haze in the air, glowing neon tubes, strong rim light",
    "game_show_3d": "hard key light, high-energy game-show set, glossy surfaces",
    "roundtable_3d": "moody chiaroscuro, desk lamps, deep shadows",
    "sketch_3d": "theatre stage with red curtains, warm spotlight, dust motes in the air",
    "warm_talk_3d": "cozy soft light, wood textures, gentle bokeh",
    "clay_life_3d": "stop-motion clay miniature set, handmade texture, tilt-shift, soft studio light",
    "noir_mystery_3d": "hard venetian-blind shadows, cold blue light, wet floor reflections, film-noir mood",
    "retro_standup_2d": "1980s retro 2D anime illustration of a TV studio stage with a microphone stand and stool, "
                        "warm yellow tungsten light, slight film grain, soft focus, vintage poster look",
    "history_ink_2d": "Chinese ink-wash 2D illustration, brush strokes, paper texture, candlelight",
    "debate_ink_2d": "Chinese ink-wash 2D illustration, red lacquer and gold accents, brush-stroke shadows",
    "museum_gold_2d": "rich 2D anime illustration, ornate details, dramatic spotlights, gold and jade",
}


def log(msg=""):
    print("[%s] %s" % (time.strftime("%H:%M:%S"), msg), flush=True)


def center_crop_resize(src: str, dst: str):
    im = Image.open(src).convert("RGB")
    scale = max(OUT_W / im.width, OUT_H / im.height)
    nw, nh = max(OUT_W, int(round(im.width * scale))), max(OUT_H, int(round(im.height * scale)))
    im = im.resize((nw, nh), Image.LANCZOS)
    left, top = (nw - OUT_W) // 2, (nh - OUT_H) // 2
    im.crop((left, top, left + OUT_W, top + OUT_H)).save(dst)


def build_prompt(preset_id: str, preset: dict, scene_id: str):
    desc = SCENE_DESC[scene_id]
    palette_id = preset.get("palette", "")
    pos = ("masterpiece, best quality, " + preset["prompt_prefix"] +
           ", " + PALETTE_WORDS.get(palette_id, "") +
           ", " + PRESET_EXTRA.get(preset_id, "") +
           ", " + desc +
           ", empty scene, no characters, no people, no text, wide shot, 16:9 widescreen composition, "
           "foreground floor space for character placement, horizon around mid-frame, highly detailed")
    neg = ("people, human, character, 1girl, 1boy, crowd, text, watermark, logo, letters, signature, "
           "lowres, blurry, deformed, flat gradient background, oversaturated skin, frame border, "
           "abstract, glitch, static, black screen, color bars, test pattern")
    return pos, neg


def font(sz):
    try:
        return ImageFont.truetype("C:/Windows/Fonts/msyh.ttc", sz)
    except Exception:
        return ImageFont.load_default(sz)


def main(argv=None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    ap = argparse.ArgumentParser()
    ap.add_argument("--skip-existing", action="store_true")
    ap.add_argument("--only", default=None, help="逗号分隔的 场景__后缀 或 preset id")
    ap.add_argument("--timeout", type=float, default=900.0)
    args = ap.parse_args(argv)

    os.makedirs(OUT_DIR, exist_ok=True)
    presets = json.load(open(PRESETS_PATH, encoding="utf-8"))["presets"]
    only = set(x.strip() for x in args.only.split(",")) if args.only else None

    comfy.ensure_comfy(comfy.DEFAULT_SERVER, comfy.DEFAULT_COMFY_ROOT, False, log)
    object_info = comfy.fetch_object_info(comfy.DEFAULT_SERVER)
    ckpts = comfy.get_checkpoints(comfy.DEFAULT_SERVER, object_info)
    if DEFAULT_CKPT not in ckpts:
        raise SystemExit("checkpoint 缺失: %s" % DEFAULT_CKPT)

    t_all = time.time()
    jobs = []
    for idx, (scene_id, preset_id) in enumerate(JOBS):
        preset = presets[preset_id]
        suffix = preset["plate_suffix"]
        out_id = "%s%s" % (scene_id, suffix)
        if only and (out_id not in only and preset_id not in only):
            continue
        dst = os.path.join(OUT_DIR, out_id + ".png")
        if args.skip_existing and os.path.isfile(dst):
            log("[skip] %s" % dst)
            continue
        pos, neg = build_prompt(preset_id, preset, scene_id)
        wf = build_workflow(pos, neg, GEN_W, GEN_H, seed=SEED_BASE + idx,
                            filename_prefix="zaopianju_styles/%s" % out_id,
                            ckpt_name=DEFAULT_CKPT, steps=DEFAULT_STEPS, cfg=DEFAULT_CFG,
                            sampler_name=DEFAULT_SAMPLER, scheduler=DEFAULT_SCHEDULER)
        jobs.append({"id": out_id, "scene": scene_id, "preset": preset_id,
                     "label": preset.get("label"), "palette": preset.get("palette"),
                     "prompt_prefix": preset["prompt_prefix"], "dst": dst,
                     "seed": SEED_BASE + idx, "prompt": pos, "negative": neg,
                     "t0": time.time(), "pid": comfy.submit(comfy.DEFAULT_SERVER, wf, log)})

    results = []
    for j in jobs:
        rec = comfy.wait_prompt(comfy.DEFAULT_SERVER, j["pid"], args.timeout, log)
        imgs = comfy.fetch_images(rec)
        if not imgs:
            raise RuntimeError("%s 无产出" % j["id"])
        imgs.sort(key=lambda it: it["filename"])
        raw = j["dst"] + ".raw.png"
        comfy.save_image(comfy.DEFAULT_SERVER, comfy.DEFAULT_COMFY_ROOT, imgs[0], raw, log)
        center_crop_resize(raw, j["dst"])
        os.remove(raw)
        j["gen_sec"] = round(time.time() - j["t0"], 1)
        j["file"] = j["dst"]
        for k in ("t0", "pid"):
            j.pop(k, None)
        j["gen_size"] = [GEN_W, GEN_H]
        j["out_size"] = [OUT_W, OUT_H]
        log("[done] %-34s %5.1fs" % (j["id"], j["gen_sec"]))
        results.append(j)

    # meta
    meta_path = os.path.join(OUT_DIR, "meta_styles.json")
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

    # contact sheet（4 列）
    imgs = sorted(meta["images"], key=lambda r: r["id"])
    cell_w, cell_h, cols = 426, 240, 4
    rows = (len(imgs) + cols - 1) // cols
    sheet = Image.new("RGB", (cell_w * cols, (cell_h + 24) * rows), (12, 14, 20))
    d = ImageDraw.Draw(sheet)
    for i, r in enumerate(imgs):
        p = r["file"] if os.path.isabs(r["file"]) else os.path.join(REPO, r["file"])
        if not os.path.isfile(p):
            continue
        im = Image.open(p).convert("RGB").resize((cell_w, cell_h), Image.LANCZOS)
        x, y = (i % cols) * cell_w, (i // cols) * (cell_h + 24)
        sheet.paste(im, (x, y))
        d.text((x + 6, y + cell_h + 3), r["id"], font=font(14), fill=(200, 205, 215))
    sheet_path = os.path.join(OUT_DIR, "contact_sheet_styles.png")
    sheet.save(sheet_path)
    log("[sheet] %s" % sheet_path)

    print(json.dumps({"ok": True, "outdir": OUT_DIR, "count": len(results),
                      "ids": [r["id"] for r in results],
                      "meta": meta_path, "contact_sheet": sheet_path,
                      "elapsed_sec": round(time.time() - t_all, 1)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
