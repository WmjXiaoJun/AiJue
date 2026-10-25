# -*- coding: utf-8 -*-
"""3D 动漫风格资产生成（无 AI 3D，纯 SDXL / ComfyUI）——task-42

产出:
  1) 8 张 1280x720 3D 动漫场景 plate: outputs/scene_plates/{id}_3d.png
  2) 3 角色 3D 动漫全身立绘(白底, IP-Adapter 保身份): outputs/concepts/{id}/three_view_3d/front.png
  3) 去白底 cutout: outputs/characters/{id}/cutout_3d/front.png（供 pipeline 合成）
  4) contact sheet + meta_3d.json（prompt/seed/耗时）

用法:
  python scripts/gen_3d_anime_assets.py                 # 场景 + 角色全跑
  python scripts/gen_3d_anime_assets.py --part scenes   # 只跑 8 张场景
  python scripts/gen_3d_anime_assets.py --part chars    # 只跑 3 角色
  python scripts/gen_3d_anime_assets.py --skip-existing # 断点续跑
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections import deque

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, REPO)
sys.path.insert(0, os.path.join(REPO, "imagegen"))

from imagegen import comfy  # noqa: E402
from imagegen.workflow import (  # noqa: E402
    DEFAULT_CFG, DEFAULT_CKPT, DEFAULT_SAMPLER, DEFAULT_SCHEDULER, DEFAULT_STEPS,
    build_ipadapter_workflow, build_workflow,
)

SCENE_DIR = os.path.join(REPO, "outputs", "scene_plates")
CONCEPT_DIR = os.path.join(REPO, "outputs", "concepts")
CHAR_DIR = os.path.join(REPO, "outputs", "characters")
GEN_W, GEN_H = 1344, 768
OUT_W, OUT_H = 1280, 720
CHAR_W, CHAR_H = 1024, 1536
SCENE_SEED = 5001
CHAR_SEED = 6201

# ---- 3D 动漫风格前缀（与 contract/style_map.json 的 3d_anime.prompt_prefix 对齐）----
STYLE_3D = ("cinematic 3D anime cel-shaded render (three-dimensional stylized anime, like a high-end 3D "
            "animated film), strong depth, layered foreground midground background, volumetric lighting, "
            "depth of field, realistic shadows, octane render, unreal engine 5, one-point perspective")
NEG_SCENE = ("people, human, character, 1girl, 1boy, crowd, text, watermark, logo, letters, "
             "signature, flat 2d illustration, hand-drawn lineart, sketch, lowres, blurry, deformed")

SCENES_3D = [
    ("roundtable_studio_3d",
     "a modern round-table TV talk studio: a large circular table in the center with chairs, a big LED screen "
     "behind, circular ceiling light rig above, camera on a tripod at the side, audience seats in soft shadow, "
     "glossy floor with reflections, empty studio"),
    ("stage_music_3d",
     "a concert stage with a microphone stand in the center, colorful stage lights and volumetric light beams, "
     "light haze in the air, big speakers on both sides, glossy dark reflective floor, empty stage"),
    ("taskmaster_stage_3d",
     "a colorful comedy task-show stage: a podium with a golden trophy, a big red button on a pedestal, "
     "colorful geometric backdrop without text, spotlights from above, polished stage floor, empty stage"),
    ("late_night_desk_3d",
     "a late-night talk show office set: a large desk with a microphone, coffee mug and papers, a warm desk lamp, "
     "a big window showing a neon city skyline at night, an empty chair behind the desk, dark blue studio lighting, "
     "glossy floor reflections"),
    ("talk_show_sofa_3d",
     "a warm TV talk-show set with two large comfortable fabric sofas facing each other, a low wooden coffee table "
     "with two mugs between them, a soft rug, a warm floor lamp, dark studio background with soft bokeh lights, "
     "empty set"),
    ("kitchen_warm_3d",
     "a warm cozy kitchen: a wooden counter with vegetables and a kettle, hanging pots and herbs, sunlight "
     "streaming through a window with dust motes, steam rising from a pot, plants on the windowsill, empty room"),
    ("debate_hall_3d",
     "a formal debate hall with two wooden podiums facing each other, dark wooden panel walls, velvet curtains, "
     "dramatic spotlights from above, polished wooden stage floor, empty stage"),
    ("alley_night_3d",
     "a narrow night street alley: neon signs without readable text, wet asphalt with colorful reflections, "
     "a vending machine, steam from vents, distant city lights, shallow depth of field, no people"),
]

CHARS = ["laozhou", "ali", "dawei"]
CHAR_TRAITS = {
    "laozhou": ("1boy, a 42-year-old male TV host with short swept black hair, stubble, thick eyebrows, "
                "a navy blue business suit with a white shirt, a yellow necktie and a wristwatch, confident slight smile"),
    "ali": ("1girl, a 24-year-old female AI content creator with long deep purple twintails, blue eyes, "
            "a lavender purple hoodie with cyan accents, cyan headphones and a star hair clip, cheerful smile"),
    "dawei": ("1boy, a 52-year-old male traditional TV director, bald head, black goatee, glasses, a brow scar, "
              "a dark charcoal jacket with red accents and a grey shirt, stern expression"),
}
CHAR_NEG_T2I = ("2d flat illustration, hand-drawn lineart, sketch, cel-shaded 2d, multiple characters, multiple views, "
                "character sheet, reference sheet, split screen, clones, duplicate, duplicate character, ghosting, "
                "double exposure, floating shards, collage, extra face, extra body, ribbons, scarf, props, "
                "background elements, effects, ground shadow, drop shadow, floor, horizon, dark background, "
                "colored background, gradient background, flames, fire, text, watermark, logo, cropped head, "
                "cropped feet, extra limbs, deformed, lowres, blurry")
CHAR_NEG = ("2d flat illustration, hand-drawn lineart, sketch, dark background, flames, fire, energy, "
            "abstract background, collage, large face in background, duplicate face, ribbons, scarf, bow, props, "
            "background elements, effects, ground shadow, drop shadow, casting shadow, floor, horizon, "
            "gradient background, colored background, text, watermark, logo, multiple characters, "
            "cropped head, cropped feet, extra limbs, deformed, lowres")


def log(msg=""):
    print("[%s] %s" % (time.strftime("%H:%M:%S"), msg), flush=True)


def scene_prompt(desc: str) -> tuple:
    pos = ("masterpiece, best quality, " + STYLE_3D + ", " + desc +
           ", empty scene, no characters, no text, wide shot, sharp focus, high detail")
    return pos, NEG_SCENE


def char_prompt_t2i(cid: str) -> str:
    """纯 txt2img 版 3D 动漫全身立绘（不依赖 IP-Adapter，白底/单人/全身更可控）。"""
    return ("masterpiece, best quality, 3D anime cel-shaded render, 3d cg character, octane render, "
            "unreal engine 5, soft studio lighting, subsurface scattering, detailed 3d model, "
            "solo, full body standing, " + CHAR_TRAITS[cid] +
            ", plain pure white background, centered composition, feet near the bottom edge of the frame, "
            "looking at viewer, clean silhouette, no props, no text, no watermark")


def char_prompt(cid: str) -> str:
    traits = CHAR_TRAITS[cid]
    return (
        "subject_definitions: 3D anime cel-shaded render of the same character as in the reference image "
        "(preserve the exact face design, hairstyle, hair color, eye color, outfit, accessories and color palette): "
        + traits + ".\n\n"
        "summary: One continuous full-body shot, eye-level camera, of Subject 1 standing in a relaxed confident pose "
        "facing the camera, on a plain pure white background (#FFFFFF), isolated single character, no props, no ribbons, "
        "no scarf, no background elements, no ground shadow. Stylized 3D anime character like a high-end 3D animated "
        "film: clean silhouette, subtle rim light, strong dimensionality, detailed clothing folds, cel-shaded materials. "
        "Full body standing from head to feet, centered composition, feet near the bottom edge of the frame, "
        "no text, no watermark.\n\n"
        "overall_soundscape: quiet studio room tone.\n\n"
        "non_diegetic_music: N/A")


def center_crop_resize(src: str, dst: str):
    im = Image.open(src).convert("RGB")
    scale = max(OUT_W / im.width, OUT_H / im.height)
    nw, nh = max(OUT_W, int(round(im.width * scale))), max(OUT_H, int(round(im.height * scale)))
    im = im.resize((nw, nh), Image.LANCZOS)
    left, top = (nw - OUT_W) // 2, (nh - OUT_H) // 2
    im.crop((left, top, left + OUT_W, top + OUT_H)).save(dst)


def remove_white(im: Image.Image, white=238, tol=18) -> Image.Image:
    """泛洪去白底（只删与四边连通的近白像素），输出 RGBA 并裁边。"""
    im = im.convert("RGBA")
    a = np.array(im).astype(np.int16)
    h, w = a.shape[:2]
    rgb = a[:, :, :3]
    mx, mn = rgb.max(axis=2), rgb.min(axis=2)
    whiteish = (mn >= white - tol) & ((mx - mn) <= 12)
    seen = np.zeros((h, w), dtype=bool)
    q = deque()
    for x in range(w):
        for y in (0, h - 1):
            if whiteish[y, x] and not seen[y, x]:
                seen[y, x] = True
                q.append((y, x))
    for y in range(h):
        for x in (0, w - 1):
            if whiteish[y, x] and not seen[y, x]:
                seen[y, x] = True
                q.append((y, x))
    while q:
        y, x = q.popleft()
        for dy, dx in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            ny, nx = y + dy, x + dx
            if 0 <= ny < h and 0 <= nx < w and whiteish[ny, nx] and not seen[ny, nx]:
                seen[ny, nx] = True
                q.append((ny, nx))
    alpha = np.where(seen, 0, 255).astype(np.uint8)
    out = Image.fromarray(np.dstack([rgb.astype(np.uint8), alpha]), "RGBA")
    out.putalpha(out.getchannel("A").filter(ImageFilter.GaussianBlur(0.8)))
    bbox = out.getbbox()
    if bbox:
        pad = 8
        out = out.crop((max(0, bbox[0] - pad), max(0, bbox[1] - pad),
                        min(w, bbox[2] + pad), min(h, bbox[3] + pad)))
    return out


def font(sz):
    p = "C:/Windows/Fonts/msyh.ttc"
    try:
        return ImageFont.truetype(p, sz)
    except Exception:
        return ImageFont.load_default(sz)


def build_contact_sheet(items, cell_w, cell_h, cols, out_path, label_h=22):
    rows = (len(items) + cols - 1) // cols
    sheet = Image.new("RGB", (cell_w * cols, (cell_h + label_h) * rows), (12, 14, 20))
    d = ImageDraw.Draw(sheet)
    for i, (label, path) in enumerate(items):
        if not os.path.isfile(path):
            continue
        im = Image.open(path).convert("RGB")
        scale = min(cell_w / im.width, cell_h / im.height)
        im = im.resize((max(1, int(im.width * scale)), max(1, int(im.height * scale))), Image.LANCZOS)
        x, y = (i % cols) * cell_w, (i // cols) * (cell_h + label_h)
        sheet.paste(im, (x + (cell_w - im.width) // 2, y + (cell_h - im.height) // 2))
        d.text((x + 6, y + cell_h + 3), label, font=font(15), fill=(200, 205, 215))
    sheet.save(out_path)
    return out_path


def main(argv=None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    ap = argparse.ArgumentParser()
    ap.add_argument("--part", default="both", choices=["both", "scenes", "chars"])
    ap.add_argument("--pure-t2i", action="store_true",
                    help="角色用纯 txt2img（不用 IP-Adapter，白底/单人更可控）")
    ap.add_argument("--skip-existing", action="store_true")
    ap.add_argument("--timeout", type=float, default=900.0)
    args = ap.parse_args(argv)

    os.makedirs(SCENE_DIR, exist_ok=True)
    meta_path = os.path.join(SCENE_DIR, "meta_3d.json")
    meta = {"model": DEFAULT_CKPT, "steps": DEFAULT_STEPS, "cfg": DEFAULT_CFG,
            "sampler": DEFAULT_SAMPLER, "scheduler": DEFAULT_SCHEDULER,
            "style": "3d_anime", "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "scenes": [], "characters": []}
    if os.path.isfile(meta_path):
        try:
            old = json.load(open(meta_path, encoding="utf-8"))
            meta["scenes"] = old.get("scenes") or []
            meta["characters"] = old.get("characters") or []
        except Exception:
            pass

    comfy.ensure_comfy(comfy.DEFAULT_SERVER, comfy.DEFAULT_COMFY_ROOT, False, log)
    object_info = comfy.fetch_object_info(comfy.DEFAULT_SERVER)
    t_all = time.time()
    results = {"scenes": [], "characters": []}

    # ---------------- 场景 plate ----------------
    if args.part in ("both", "scenes"):
        jobs = []
        for idx, (sid, desc) in enumerate(SCENES_3D):
            dst = os.path.join(SCENE_DIR, sid + ".png")
            if args.skip_existing and os.path.isfile(dst):
                log("[skip] %s" % dst)
                continue
            pos, neg = scene_prompt(desc)
            wf = build_workflow(pos, neg, GEN_W, GEN_H, seed=SCENE_SEED + idx,
                                filename_prefix="zaopianju_scenes3d/%s" % sid,
                                ckpt_name=DEFAULT_CKPT, steps=DEFAULT_STEPS, cfg=DEFAULT_CFG,
                                sampler_name=DEFAULT_SAMPLER, scheduler=DEFAULT_SCHEDULER)
            jobs.append({"id": sid, "dst": dst, "seed": SCENE_SEED + idx,
                         "prompt": pos, "negative": neg, "t0": time.time(),
                         "pid": comfy.submit(comfy.DEFAULT_SERVER, wf, log)})
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
            j.pop("t0"); j.pop("pid")
            log("[scene] %-24s %5.1fs -> %s" % (j["id"], j["gen_sec"], j["dst"]))
            results["scenes"].append(j)
        merged = {r["id"]: r for r in meta["scenes"]}
        for r in results["scenes"]:
            merged[r["id"]] = r
        meta["scenes"] = list(merged.values())
        # 场景 3D contact sheet
        items = [(os.path.basename(r["dst"]).replace(".png", ""), r["dst"]) for r in meta["scenes"]]
        meta["scenes_contact_sheet"] = build_contact_sheet(
            items, 426, 240, 4, os.path.join(SCENE_DIR, "contact_sheet_3d.png"))
        log("[sheet] %s" % meta["scenes_contact_sheet"])

    # ---------------- 角色 3D 全身立绘（IP-Adapter） ----------------
    if args.part in ("both", "chars"):
        jobs = []
        for idx, cid in enumerate(CHARS):
            outdir = os.path.join(CONCEPT_DIR, cid, "three_view_3d")
            os.makedirs(outdir, exist_ok=True)
            dst = os.path.join(outdir, "front.png")
            if args.skip_existing and os.path.isfile(dst):
                log("[skip] %s" % dst)
                continue
            # 参考图优先用「干净白底全身」概念图（构图/背景不会把 bust portrait 的脸和特效带进来）
            ref_candidates = [
                os.path.join(CONCEPT_DIR, cid, "three_view", "front.png"),          # 干净白底 2D 全身
                os.path.join(CONCEPT_DIR, cid, "consistent", "three_view", "front.png"),
                os.path.join(CONCEPT_DIR, cid, "portrait.png"),
            ]
            ref = next((p for p in ref_candidates if os.path.isfile(p)), None)
            if not ref:
                log("[warn] 缺少参考图 %s" % ref_candidates)
                continue
            if args.pure_t2i:
                pos, neg = char_prompt_t2i(cid), CHAR_NEG_T2I
                ref_rel = None
                wf = build_workflow(pos, neg, CHAR_W, CHAR_H, seed=CHAR_SEED + idx,
                                    filename_prefix="zaopianju_concepts3d/%s_front" % cid,
                                    ckpt_name=DEFAULT_CKPT, steps=DEFAULT_STEPS, cfg=DEFAULT_CFG,
                                    sampler_name=DEFAULT_SAMPLER, scheduler=DEFAULT_SCHEDULER)
                log("[char] pure-t2i: %s" % cid)
            else:
                ref_rel = comfy.copy_input_image(comfy.DEFAULT_COMFY_ROOT, ref,
                                                 subfolder="zaopianju_concepts",
                                                 name="%s_3d_ref.png" % cid, log=log)
                pos, neg = char_prompt(cid), CHAR_NEG
                wf = build_ipadapter_workflow(
                    pos, neg, ref_rel, width=CHAR_W, height=CHAR_H,
                    seed=CHAR_SEED + idx, filename_prefix="zaopianju_concepts3d/%s_front" % cid,
                    ckpt_name=DEFAULT_CKPT, steps=DEFAULT_STEPS, cfg=DEFAULT_CFG,
                    sampler_name=DEFAULT_SAMPLER, scheduler=DEFAULT_SCHEDULER,
                    ipadapter_preset="PLUS (high strength)", ipadapter_weight=0.55,
                    ipadapter_end=0.8, ipadapter_weight_type="linear")
            job_rec = {"id": cid, "dst": dst, "seed": CHAR_SEED + idx,
                       "prompt": pos, "negative": neg, "pure_t2i": bool(args.pure_t2i),
                       "t0": time.time(), "pid": comfy.submit(comfy.DEFAULT_SERVER, wf, log)}
            if not args.pure_t2i:
                job_rec["ref"] = ref
                job_rec["ip_adapter"] = {"preset": "PLUS (high strength)", "weight": 0.55,
                                         "start_at": 0.0, "end_at": 0.8, "weight_type": "linear"}
            jobs.append(job_rec)
        for j in jobs:
            rec = comfy.wait_prompt(comfy.DEFAULT_SERVER, j["pid"], args.timeout, log)
            imgs = comfy.fetch_images(rec)
            if not imgs:
                raise RuntimeError("%s 无产出" % j["id"])
            imgs.sort(key=lambda it: it["filename"])
            raw = j["dst"] + ".raw.png"
            comfy.save_image(comfy.DEFAULT_SERVER, comfy.DEFAULT_COMFY_ROOT, imgs[0], raw, log)
            im = Image.open(raw).convert("RGB")
            if im.size != (CHAR_W, CHAR_H):
                im = im.resize((CHAR_W, CHAR_H), Image.LANCZOS)
            im.save(j["dst"])
            # cutout 去白底（供 pipeline 分层合成）
            cut_dir = os.path.join(CHAR_DIR, j["id"], "cutout_3d")
            os.makedirs(cut_dir, exist_ok=True)
            cut = remove_white(im)
            cut_path = os.path.join(cut_dir, "front.png")
            cut.save(cut_path)
            os.remove(raw)
            j["gen_sec"] = round(time.time() - j["t0"], 1)
            j.pop("t0"); j.pop("pid")
            j["cutout"] = cut_path
            j["alpha_ratio"] = round(float((np.array(cut)[:, :, 3] > 0).mean()), 3)
            # 单角色 meta
            with open(os.path.join(CONCEPT_DIR, j["id"], "three_view_3d", "meta.json"),
                      "w", encoding="utf-8") as f:
                json.dump({"id": j["id"], "view": "front", "style": "3d_anime",
                           "size": [CHAR_W, CHAR_H], **{k: v for k, v in j.items() if k != "dst"}},
                          f, ensure_ascii=False, indent=2)
            log("[char] %-10s %5.1fs -> %s | cutout alpha=%.3f" % (j["id"], j["gen_sec"], j["dst"], j["alpha_ratio"]))
            results["characters"].append(j)
        merged = {r["id"]: r for r in meta["characters"]}
        for r in results["characters"]:
            merged[r["id"]] = r
        meta["characters"] = list(merged.values())
        items = [(cid, os.path.join(CONCEPT_DIR, cid, "three_view_3d", "front.png")) for cid in CHARS]
        meta["characters_contact_sheet"] = build_contact_sheet(
            items, 300, 450, 3, os.path.join(CONCEPT_DIR, "three_view_3d_contact_sheet.png"))
        log("[sheet] %s" % meta["characters_contact_sheet"])

    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)
    print(json.dumps({"ok": True, "part": args.part,
                      "scenes": [r["id"] for r in results["scenes"]],
                      "characters": [r["id"] for r in results["characters"]],
                      "meta": meta_path, "elapsed_sec": round(time.time() - t_all, 1)},
                     ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
