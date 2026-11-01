# -*- coding: utf-8 -*-
"""3D 模板语义增强（V14 试点）：把每镜台词转成英文画面描述，让 3D 画面与文字内容对应。

流程：
1. 读 template / episode（真实 line / scene / duration）
2. 用本地 LLM（backend/app/llm.py，DeepSeek）把台词批量翻译成「电影画面描述」（JSON）
3. 组合 3D brief（模板影调 + 场景 + 语义画面 + 角色 + 运镜 + negative）
4. 逐镜生成（复用 templates3d.generate_one）+ 剪辑（复用 templates3d.assemble）
5. 输出对照表 contract/v5/cinema3d/templates3d/<id>_semantic.json + 抽帧

用法：
    python -m pipeline.templates3d_semantic --template tonight_show [--resolution 768x432] [--skip-generate]
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys

from . import templates3d as base

REPO = pathlib.Path(__file__).resolve().parents[1]
SEMANTIC_DIR = REPO / "contract/v5/cinema3d/templates3d"
NEGATIVE = ("no text, no subtitles, no captions, no watermark, no logo, no UI, "
            "no distorted faces, no deformed hands, no extra people, no plastic toy look")


def load_llm():
    backend = str(REPO / "backend")
    if backend not in sys.path:
        sys.path.insert(0, backend)
    from app import llm  # type: ignore
    return llm


def semantic_beats(tid: str, episode: dict, template: dict) -> dict:
    llm = load_llm()
    cast = episode.get("meta", {}).get("cast") or {}
    lines = []
    for shot in episode.get("shots") or []:
        if shot.get("type") == "card":
            continue
        line = str(shot.get("line") or "").strip()
        if not line:
            continue
        role = (shot.get("characters") or [None])[0]
        speaker = (cast.get(role) or {}).get("name") if role else ""
        lines.append({"id": shot["id"], "speaker": speaker, "line": line,
                      "scene": shot.get("scene") or ""})
    if not lines:
        return {"template": tid, "shots": []}

    system = (
        "You are a cinematographer for a 3D animated variety show. Convert each Chinese line of "
        "dialogue into one concise English visual description for text-to-video generation. "
        "Describe what the camera sees: subjects, actions, objects, environment, lighting and mood. "
        "Never mention subtitles, text, words, captions or logos. Do not describe spoken words; "
        "show the idea through visible action or a related object. Keep it 1-2 sentences, at most 45 words."
    )
    prompt = json.dumps({
        "show_theme": template.get("label") or tid,
        "style": template.get("style"),
        "scenes": {s.get("id"): s.get("description") or s.get("label") for s in
                   (template.get("scenes") or []) if isinstance(s, dict)},
        "shots": lines,
        "output_format": {"shots": [{"id": "v5_002", "visual": "English cinematic description", "framing": "wide|medium|close"}]},
    }, ensure_ascii=False)
    schema = {
        "type": "object",
        "properties": {
            "shots": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "string"},
                        "visual": {"type": "string"},
                        "framing": {"type": "string"},
                    },
                    "required": ["id", "visual", "framing"],
                    "additionalProperties": False,
                },
            }
        },
        "required": ["shots"],
        "additionalProperties": False,
    }
    raw = llm.complete(prompt, system=system, json_schema=schema, temperature=0.7)
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        start, end = raw.find("{"), raw.rfind("}")
        data = json.loads(raw[start:end + 1])
    return {"template": tid, "shots": data.get("shots") or []}


def build_semantic_briefs(tid: str, catalog: dict, episode: dict, template: dict, beats: dict) -> dict:
    style = base.STYLE3D[tid]
    beat_map = {b.get("id"): b for b in beats.get("shots", [])}
    briefs = []
    for shot in episode["shots"]:
        sid = shot["id"]
        if shot.get("type") == "card":
            briefs.append({"id": sid, "kind": "card", "prompt": None})
            continue
        scene = shot.get("scene") or ""
        scene_desc = style["scenes"].get(scene) or f"a cinematic 3D set inspired by {scene.replace('_', ' ')}"
        roles = [c for c in (shot.get("characters") or []) if c in (episode.get("meta", {}).get("cast") or {})]
        chars = []
        for role in roles:
            cid = (episode["meta"]["cast"][role] or {}).get("character")
            if cid:
                desc = base.character_desc(catalog, cid)
                if desc not in chars:
                    chars.append(desc)
        beat = beat_map.get(sid) or {}
        visual = (beat.get("visual") or "").strip()
        framing = (beat.get("framing") or "").strip()
        is_dialogue = bool(str(shot.get("line") or "").strip())
        if is_dialogue:
            action = (f"The shot shows the idea through visible action: {visual}. " if visual else
                      "the guest speaks and gestures, framed in a medium shot")
        else:
            action = f"{visual} " if visual else "The camera surveys the space, atmospheric and cinematic. "
        if is_dialogue and framing:
            action += f"Framing: {framing} shot, characters seen naturally, mouth not emphasized. "
        cam = base.CAM_PHRASE.get(str((shot.get("camera") or {}).get("type") or ""), "") if isinstance(shot.get("camera"), dict) else ""
        if not cam:
            cam = "slow cinematic camera move"
        char_part = (" Featuring " + "; ".join(chars) + ".") if chars else ""
        desc = (f"[Shot 1] {style['look']}. Set in {scene_desc}.{char_part} {action}"
                f"Camera: {cam}. Rich physical materials, volumetric light, shallow depth of field, "
                f"3D animated feature film quality. {NEGATIVE}.")
        briefs.append({
            "id": sid,
            "kind": "dialogue" if is_dialogue else "broll",
            "scene": scene,
            "duration": shot.get("duration"),
            "line": shot.get("line") or "",
            "semantic_visual": visual,
            "framing": framing,
            "prompt": (f"integrated_multimodal_description: {desc}\n\n"
                       f"overall_soundscape: {style['ambience']}. No dialogue, no human voices, no music.\n\n"
                       f"non_diegetic_music: N/A"),
        })
    out = {"template": tid, "style": template.get("style"), "look": style["look"],
           "mode": "semantic", "shots": briefs}
    SEMANTIC_DIR.mkdir(parents=True, exist_ok=True)
    (SEMANTIC_DIR / f"{tid}_semantic.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    return out


def main(argv=None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass
    ap = argparse.ArgumentParser(description="3D 模板语义增强")
    ap.add_argument("--template", required=True)
    ap.add_argument("--resolution", default="768x432")
    ap.add_argument("--skip-generate", action="store_true")
    ap.add_argument("--skip-assemble", action="store_true")
    ap.add_argument("--lipsync", choices=["musetalk", "none"], default="musetalk",
                    help="对话镜是否用 MuseTalk 按该镜 TTS 音频做口型同步（默认 musetalk）")
    ap.add_argument("--lipsync-only", action="store_true",
                    help="跳过生成，只对已有 3D 镜头做口型同步并重新剪辑")
    args = ap.parse_args(argv)

    tid = args.template
    catalog = base.load_catalog()
    episode = base.load_episode(tid)
    template = base.load_template(tid)
    print(f"=== {tid}: 台词语义翻译（{len(episode['shots'])} 镜） ===", flush=True)
    beats = semantic_beats(tid, episode, template)
    print(f"语义画面: {len(beats.get('shots', []))} 条", flush=True)
    briefs = build_semantic_briefs(tid, catalog, episode, template, beats)
    for shot in briefs["shots"]:
        if shot.get("semantic_visual"):
            print(f"  {shot['id']} | {shot['line'][:38]} -> {shot['semantic_visual'][:90]}", flush=True)

    w, h = (int(x) for x in args.resolution.lower().split("x"))
    def apply_lipsync(shot: dict) -> None:
        sid = shot["id"]
        line = str(shot.get("line") or "").strip()
        if not line or args.lipsync == "none":
            return
        clip = base.OUT3D / tid / "shots" / f"{sid}.mp4"
        audio = base.V5_OUT / tid / "work" / "audio" / f"{sid}.wav"
        if not clip.is_file() or not audio.is_file():
            print(f"  lipsync skip {sid}: clip/audio missing", flush=True)
            return
        tmp_out = base.OUT3D / tid / "shots_lip" / f"{sid}.mp4"
        tmp_out.parent.mkdir(parents=True, exist_ok=True)
        try:
            from . import lipsync
            import time as _time
            t0 = _time.time()
            res = lipsync.sync_clip(clip, audio, tmp_out)
            ok = bool(res.get("ok"))
            sec = round(_time.time() - t0, 1)
            detail = "" if ok else f" | {res.get('reason') or res.get('error')}"
            print(f"  lipsync {sid}: {'ok' if ok else 'fail'} {sec}s{detail}", flush=True)
            if ok and tmp_out.is_file():
                import shutil
                shutil.copy2(tmp_out, clip)
        except Exception as exc:  # noqa: BLE001
            print(f"  lipsync {sid} error: {type(exc).__name__}: {exc}", flush=True)

    def free_comfy_vram() -> None:
        try:
            import httpx
            httpx.post("http://127.0.0.1:8188/free",
                       json={"unload_models": True, "free_memory": True}, timeout=15)
            print("  已释放 ComfyUI 显存（给 MuseTalk 让路）", flush=True)
        except Exception as exc:  # noqa: BLE001
            print("  释放显存失败（继续）:", exc, flush=True)

    todo = [s for s in briefs["shots"] if s.get("prompt")]
    if args.lipsync_only:
        print("=== 仅口型同步（复用已有 3D 镜头）===", flush=True)
        free_comfy_vram()
        for i, shot in enumerate(todo, 1):
            print(f"[{i}/{len(todo)}]", end=" ", flush=True)
            apply_lipsync(shot)
    elif not args.skip_generate:
        print("=== 逐镜生成（本地 H3）===", flush=True)
        for i, shot in enumerate(todo, 1):
            res = base.generate_one(tid, shot, (w, h), force=True)
            print(f"[{i}/{len(todo)}] {shot['id']} {res.get('status')} {res.get('seconds', 0)}s", flush=True)
            if res.get("status") == "failed":
                print("  error:", res.get("error"), flush=True)
        # 两阶段：全部生成完再统一做口型，避免 ComfyUI 占满显存导致 MuseTalk 卡住
        if args.lipsync != "none":
            print("=== 统一口型同步（MuseTalk）===", flush=True)
            free_comfy_vram()
            for i, shot in enumerate(todo, 1):
                apply_lipsync(shot)
    if not args.skip_assemble:
        print("=== 剪辑 ===", flush=True)
        report = base.assemble(tid)
        print("final:", report.get("probe", {}).get("format"), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
