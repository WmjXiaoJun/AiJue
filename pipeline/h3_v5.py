# -*- coding: utf-8 -*-
"""V5.5 H3 混合模式：把 V5 模板的 hero 镜头用 H3（图生视频）生成真实动作镜头，
其余镜头仍走 V5 2D 动效；最后用 V5 assemble（字幕/包装/BGM/per-cut 转场）出片。

用法（脚本/CLI 均可）：
    from pipeline import h3_v5
    h3_v5.render_hybrid("tonight_show", duration=40, hero_shots=2, force=True)

CLI：
    python -m pipeline.h3_v5 --template tonight_show --hero-shots 2 --duration 40

流程：
1. v5_story.build_plan + tts_and_pace（复用 V5 固定声音/缓存）
2. render_v5 正常渲染 2D 全部镜头（作为 fallback / hero 镜以外的画面）
3. 选 hero 镜（对白镜、含角色；可指定 --shots v5_002,...）
4. 每个 hero 镜：V5 首帧（AI plate+立绘合成 1024×576）→ H3 audio_ref（该镜 TTS 音频）
   → 归一化 1280×720/25fps/裁剪到镜长 → 替换 shots/<id>.mp4
5. render_v5.assemble + qc_v5 出片、写 storyboard/timeline/style_report
"""
from __future__ import annotations

import json
import logging
import subprocess
import sys
from pathlib import Path

from . import characters_v5, config, render_v5, scene_render, utils, v5_catalog

LOG = logging.getLogger("pipeline")
REPO = Path(config.REPO_ROOT)
H3_W, H3_H = 1024, 576
H3_PROMPT = ("cinematic 3D anime shot of the same characters as the reference image, "
             "natural subtle body motion and talking, consistent faces and outfits, "
             "camera slow cinematic move, high detail, no text, no extra characters")


class H3V5Error(RuntimeError):
    pass


def compose_first_frame(plan: dict, catalog: dict, style: dict, shot: dict,
                        out_png: Path) -> dict:
    """用 V5 场景 plate + AI 立绘合成 H3 参考首帧（1024x576）。"""
    layers = scene_render.render_scene_layers(
        catalog["scenes"][shot["scene"]], style, (1600, 900), seed=7)
    base = scene_render.flatten_layers(layers).convert("RGBA").resize((H3_W, H3_H))
    anchors = layers.get("anchors") or {}
    cast = (plan.get("meta") or {}).get("cast") or {}
    placed = []
    for role in shot.get("characters") or []:
        cid = shot.get("character") if role == shot.get("role") else (cast.get(role) or {}).get("character")
        if not cid:
            continue
        anchor = anchors.get(role) or anchors.get("host") or (0.2, 0.9)
        char_h = int(H3_H * 0.72)
        pose = "front"
        try:
            sprite = characters_v5.render_character(
                catalog["characters"][cid], style,
                (int(char_h * 0.42), char_h), pose=pose, mouth="closed", blink=False)
        except Exception:  # noqa: BLE001
            continue
        x = int(anchor[0] * H3_W - sprite.width / 2)
        y = int(anchor[1] * H3_H - sprite.height)
        base.alpha_composite(sprite, (max(0, x), max(0, y)))
        placed.append({"role": role, "char": cid, "anchor": list(anchor)})
    base.convert("RGB").save(out_png)
    return {"image": str(out_png), "characters": placed, "size": [H3_W, H3_H]}


def h3_generate(frame: Path, audio: Path, out_mp4: Path, duration: float,
                prompt: str = H3_PROMPT, seed: int = 20261101) -> dict:
    """调 H3 CLI（audio_ref 模式），返回 {out,seconds,meta}。"""
    cmd = [sys.executable, "integrations/comfyui/run_h3_ref.py",
           "--mode", "audio_ref", "--image", str(frame), "--audio", str(audio),
           "--out", str(out_mp4), "--duration", f"{duration:.2f}",
           "--width", str(H3_W), "--height", str(H3_H), "--fps", "24",
           "--seed", str(seed), "--prompt", prompt]
    import time
    t0 = time.time()
    proc = subprocess.run(cmd, cwd=str(REPO), capture_output=True, text=True,
                          encoding="utf-8", errors="replace")
    sec = round(time.time() - t0, 1)
    if proc.returncode != 0 or not out_mp4.is_file():
        raise H3V5Error(f"H3 生成失败（{sec}s）: {(proc.stderr or '')[-800:]}")
    return {"out": str(out_mp4), "seconds": sec}


def normalize_clip(src: Path, dst: Path, duration: float) -> None:
    utils.ffmpeg(["-y", "-hide_banner", "-loglevel", "error", "-i", str(src),
                  "-vf", "scale=1280:720:force_original_aspect_ratio=increase,"
                         "crop=1280:720,fps=25",
                  "-t", f"{duration:.3f}", "-an", "-c:v", "libx264",
                  "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p", str(dst)])


def pick_hero_shots(plan: dict, n: int) -> list[str]:
    """优先选对白镜（有角色、有台词），按台词长度/时长排序取前 n 个。"""
    cands = []
    for s in plan.get("shots") or []:
        if str(s.get("type")) == "card":
            continue
        if not str(s.get("line") or "").strip():
            continue
        if not (s.get("characters") or []) and not s.get("character"):
            continue
        cands.append((len(str(s.get("line") or "")), float(s.get("duration") or 0), s["id"]))
    cands.sort(reverse=True)
    return [c[2] for c in cands[:max(0, n)]]


def render_hybrid(template_id: str, *, duration: float = 40.0, hero_shots: int = 2,
                  shots_explicit: list[str] | None = None, force: bool = True,
                  prog=None, seed: int = 20261101) -> dict:
    """渲染 V5 混合成片：hero 镜 H3 + 其余 2D。返回 summary。"""
    catalog = v5_catalog.load_catalog()
    template = catalog["templates"][template_id]
    # 输出目录：outputs/templates_v5_hybrid/<id>/
    out_dir = REPO / "outputs" / "templates_v5_hybrid" / template_id
    workdir = out_dir / "work"
    shots_dir = out_dir / "shots"
    for d in (out_dir, workdir, shots_dir):
        utils.ensure_dir(d)
    args = {"template": template_id, "duration": duration,
            "out": out_dir / "final.mp4", "work": workdir, "shots": shots_dir,
            "force": force, "seed": seed}
    paths = render_v5.resolve_paths(args, template_id)
    # 复用 render_v5 的 plan/TTS/editorial 流程（不直接调 run_render，避免重复渲染）
    from . import v5_story
    plan = v5_story.build_plan(template, duration, seed=seed)
    plan["meta"]["template"] = template_id
    render_v5._add_card_shots(plan, template)
    render_v5.tts_and_pace(plan, catalog, paths, prog, force=False)
    style = render_v5._style(catalog, str(plan["episode"].get("style") or template.get("style")))
    try:
        from . import editorial
        zones = render_v5._scene_zones(catalog, plan)
        anchors = render_v5._scene_anchors(catalog, plan)
        from . import motion2d
        editorial.build_edit_plan(
            template, plan, style, seed=seed, zones_by_scene=zones,
            anchors_by_scene=anchors,
            supported_cameras=getattr(motion2d, "SUPPORTED_CAMERA_TYPES", ()))
        utils.write_json(workdir / "episode.json", plan)
    except Exception as exc:  # noqa: BLE001
        LOG.warning("editorial 计划失败: %s", exc)
    clips = render_v5.render_shot_clips(plan, catalog, style, paths, prog, force=force)
    utils.write_json(workdir / "shot_meta.json", clips["metas"])
    # 选 hero 镜并生成 H3
    shots = {s["id"]: s for s in plan["shots"]}
    hero_ids = shots_explicit or pick_hero_shots(plan, hero_shots)
    h3_results = []
    frames_dir = utils.ensure_dir(workdir / "h3_frames")
    h3_raw_dir = utils.ensure_dir(workdir / "h3_raw")
    for sid in hero_ids:
        shot = shots.get(sid)
        if not shot or not shot.get("audio"):
            continue
        fp = frames_dir / f"{sid}.png"
        compose_first_frame(plan, catalog, style, shot, fp)
        raw = h3_raw_dir / f"{sid}.mp4"
        res = h3_generate(fp, Path(shot["audio"]), raw, float(shot["duration"]), seed=seed)
        target = shots_dir / f"{sid}.mp4"
        normalize_clip(raw, target, float(shot["duration"]))
        # 让 assemble 看到新 clip 已替换即可（clip_paths 按 shots_dir 解析）
        h3_results.append({"shot": sid, "raw": str(raw), "seconds": res["seconds"],
                           "duration": shot["duration"]})
        LOG.info("H3 hero 镜完成 %s (%.1fs)", sid, res["seconds"])
    asm = render_v5.assemble(plan, catalog, style, paths, prog)
    probe = utils.probe_video(paths["out"])
    summary = {"ok": True, "template": template_id, "path": str(paths["out"]),
               "duration": probe["duration"], "hero_shots": h3_results,
               "transition": asm.get("transition"), "edit": plan["meta"].get("edit")}
    utils.write_json(out_dir / "hybrid_report.json", summary)
    return summary


def main(argv: list[str] | None = None) -> int:
    import argparse
    ap = argparse.ArgumentParser(description="V5 H3 混合渲染（hero 镜 3D + 其余 2D）")
    ap.add_argument("--template", required=True)
    ap.add_argument("--duration", type=float, default=40.0)
    ap.add_argument("--hero-shots", type=int, default=2)
    ap.add_argument("--shots", default=None, help="逗号分隔的指定镜头 id")
    ap.add_argument("--seed", type=int, default=20261101)
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args(argv)
    shots = [x.strip() for x in (a.shots or "").split(",") if x.strip()] or None
    res = render_hybrid(a.template, duration=a.duration, hero_shots=a.hero_shots,
                        shots_explicit=shots, force=a.force, seed=a.seed)
    print(json.dumps(res, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
