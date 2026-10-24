# -*- coding: utf-8 -*-
"""H3 动态镜头探针：音频驱动口型（05_ref2va）与首尾帧（04_i2v_fl2v）小样验证。

用法:
  python integrations/comfyui/tools/h3_dynamic_probe.py cn
  python integrations/comfyui/tools/h3_dynamic_probe.py en
  python integrations/comfyui/tools/h3_dynamic_probe.py fl2v
"""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))          # integrations/comfyui/tools
COMFY_DIR = os.path.dirname(HERE)                           # integrations/comfyui
REPO = os.path.dirname(os.path.dirname(COMFY_DIR))          # repo root
COMFY = os.environ.get("COMFYUI_ROOT", r"D:\BaiduSyncdisk\wmj_work\XJ\ComfyUI")
SERVER = os.environ.get("COMFYUI_URL", "http://127.0.0.1:8188")
sys.path.insert(0, COMFY_DIR)
from run_h3 import (  # noqa: E402
    fmt_sec, http_json, submit, wait_prompt, collect_outputs, probe_video, find_bin,
)
from workflow_to_api import fetch_object_info, load_workflow, ui_to_api  # noqa: E402

IN_DIR = os.path.join(COMFY, "input")
SUB = "zaopianju_dynamic"
FFMPEG = os.path.join(REPO, "tools", "node_modules", "ffmpeg-static", "ffmpeg.exe")
FFPROBE = os.path.join(REPO, "tools", "node_modules", "ffprobe-static", "bin", "win32", "x64", "ffprobe.exe")


def log(msg=""):
    print("[%s] %s" % (time.strftime("%H:%M:%S"), msg), flush=True)


def synth_audio(text: str, voice: str, out_wav: str) -> str:
    import edge_tts
    mp3 = out_wav + ".mp3"
    async def _run():
        c = edge_tts.Communicate(text, voice)
        await c.save(mp3)
    asyncio.run(_run())
    # 转 32kHz 单声道 pcm_s16le（H3 audio VAE 友好）
    subprocess.run([FFMPEG, "-y", "-v", "error", "-i", mp3, "-ar", "32000", "-ac", "1", "-c:a", "pcm_s16le", out_wav],
                   check=True)
    os.remove(mp3)
    # 拷进 ComfyUI/input
    relay = os.path.join(IN_DIR, SUB)
    os.makedirs(relay, exist_ok=True)
    shutil.copyfile(out_wav, os.path.join(relay, os.path.basename(out_wav)))
    log("[audio] %s (%s) -> ComfyUI/input/%s/%s" % (out_wav, voice, SUB, os.path.basename(out_wav)))
    return "%s/%s" % (SUB, os.path.basename(out_wav))


def prepare_prompt(template_name: str, ref_image: str, audio_rel: str = None,
                   last_image: str = None, prompt: str = None,
                   width=768, height=768, length=124, seed=20261024,
                   filename_prefix="zaopianju_dynamic/probe", ref_size="match"):
    wf_path = os.path.join(COMFY, "user", "default", "workflows", template_name)
    wf = load_workflow(wf_path)
    info = fetch_object_info(SERVER)
    prompt_api, warns = ui_to_api(wf, info, log=lambda *a: None)
    for w in warns:
        log("[warn] " + w)
    # 参考图：拷进 input 并替换 LoadImage
    relay = os.path.join(IN_DIR, SUB)
    os.makedirs(relay, exist_ok=True)
    img_name = os.path.basename(ref_image)
    shutil.copyfile(ref_image, os.path.join(relay, img_name))
    ref_rel = "%s/%s" % (SUB, img_name)
    load_nids = [nid for nid, n in prompt_api.items() if n["class_type"] == "LoadImage"]
    h3_nid = next(nid for nid, n in prompt_api.items()
                  if n["class_type"] in ("MiniMaxH3ReferenceToVideo", "MiniMaxH3ImageToVideo"))
    h3 = prompt_api[h3_nid]
    # 主 LoadImage = 首帧/参考图
    prompt_api[load_nids[0]]["inputs"]["image"] = ref_rel
    if h3["class_type"] == "MiniMaxH3ReferenceToVideo":
        h3["inputs"]["ref_images.ref_image_0"] = [load_nids[0], 0]
        h3["inputs"]["ref_image_size"] = ref_size
        if audio_rel:
            audio_nid = "90"
            prompt_api[audio_nid] = {"class_type": "LoadAudio", "inputs": {"audio": audio_rel}}
            h3["inputs"]["ref_audios.ref_audio_0"] = [audio_nid, 0]
    else:
        h3["inputs"]["first_frame"] = [load_nids[0], 0]
        if last_image:
            relay_last = os.path.basename(last_image)
            shutil.copyfile(last_image, os.path.join(relay, relay_last))
            last_rel = "%s/%s" % (SUB, relay_last)
            last_nid = "91"
            prompt_api[last_nid] = {"class_type": "LoadImage", "inputs": {"image": last_rel}}
            h3["inputs"]["last_frame"] = [last_nid, 0]
    h3["inputs"]["prompt"] = prompt
    h3["inputs"]["width"] = int(width)
    h3["inputs"]["height"] = int(height)
    h3["inputs"]["length"] = int(length)
    noise_nid = next(nid for nid, n in prompt_api.items() if n["class_type"] == "RandomNoise")
    prompt_api[noise_nid]["inputs"]["noise_seed"] = int(seed)
    save_nid = next(nid for nid, n in prompt_api.items() if n["class_type"] == "SaveVideo")
    prompt_api[save_nid]["inputs"]["filename_prefix"] = filename_prefix
    return prompt_api, h3_nid


def run_and_fetch(tag: str, prompt_api: dict, out_path: str):
    t0 = time.time()
    pid = submit(SERVER, prompt_api, log)
    rec = wait_prompt(SERVER, pid, 900, log)
    collect_outputs(rec, COMFY, SERVER, out_path, log)
    gen = time.time() - t0
    info = probe_video(out_path)
    log("[%s] done %.1fs  duration=%.2fs  %dx%d  audio=%s" % (
        tag, gen, info["duration_sec"], info["video"]["width"], info["video"]["height"], info["has_audio"]))
    # 抽 8 帧拼图（每秒约 1.5 帧）
    sheet = out_path.rsplit(".", 1)[0] + "_sheet.png"
    vf = "fps=8/%d,scale=192:192,tile=4x2" % max(1, round(info["duration_sec"]))
    subprocess.run([FFMPEG, "-y", "-v", "error", "-i", out_path, "-vf", vf, "-frames:v", "1", sheet], check=False)
    log("[%s] frames sheet: %s" % (tag, sheet))
    return {"tag": tag, "out": out_path, "gen_sec": round(gen, 1), "ffprobe": info, "sheet": sheet, "prompt_id": pid}


PROMPT_CN = ("subject_definitions: Subject 1 is the man in the reference image, a 42-year-old anime TV host "
             "with black swept hair, stubble and a navy suit.\n\n"
             "summary: One continuous medium close-up of Subject 1 speaking directly to the camera in a dark studio "
             "with warm golden rim light. His lips move naturally in sync with the reference speech audio, he blinks, "
             "raises his eyebrows and gives small confident head nods while talking. Keep his face, hairstyle, outfit "
             "and the composition of the reference image stable; only the mouth, eyes and head move subtly. "
             "Anime cel-shaded style, clean line art, no text, no watermark.\n\n"
             "overall_soundscape: The same spoken Chinese sentence, close-mic voice, quiet studio room tone.\n\n"
             "non_diegetic_music: N/A")

PROMPT_EN = ("subject_definitions: Subject 1 is the man in the reference image, a 42-year-old anime TV host "
             "with black swept hair, stubble and a navy suit.\n\n"
             "summary: One continuous medium close-up of Subject 1 speaking directly to the camera in a dark studio "
             "with warm golden rim light. His lips move naturally in sync with the reference English speech audio, "
             "he blinks and nods slightly while talking. Keep his face, hairstyle, outfit and composition stable. "
             "Anime cel-shaded style, clean line art, no text, no watermark.\n\n"
             "overall_soundscape: The same spoken English sentence, close-mic voice, quiet room tone.\n\n"
             "non_diegetic_music: N/A")

PROMPT_FL2V = ("integrated_multimodal_description: [Shot 1] A slow cinematic transition from the first frame to the "
               "last frame: the anime character turns slightly and the camera drifts gently, keeping the same face, "
               "hair and outfit consistent between both frames. Subtle breathing, blinking and small head movement. "
               "Preserve the composition and colors of both frames; no big camera moves, no face swap, no text.\n\n"
               "overall_soundscape: Quiet studio room tone with a faint airy ambience.\n\n"
               "non_diegetic_music: N/A")


def main():
    test = (sys.argv[1] if len(sys.argv) > 1 else "cn").lower()
    os.makedirs(os.path.join(REPO, "tmp"), exist_ok=True)
    portrait = os.path.join(REPO, "outputs", "concepts", "laozhou", "portrait.png")
    if test == "cn":
        text = "今天这期，我们聊一个得罪人的话题。"
        wav = os.path.join(REPO, "tmp", "audio_cn.wav")
        rel = synth_audio(text, "zh-CN-YunxiNeural", wav)
        api, _ = prepare_prompt("05_ref2va_4step_sla.json", portrait, audio_rel=rel, prompt=PROMPT_CN,
                                width=768, height=768, length=124,
                                filename_prefix="zaopianju_dynamic/h3_refva_cn")
        res = run_and_fetch("refva_cn", api, os.path.join(REPO, "tmp", "h3_refva_test.mp4"))
    elif test == "en":
        text = "Tonight, we are talking about a topic that offends people."
        wav = os.path.join(REPO, "tmp", "audio_en.wav")
        rel = synth_audio(text, "en-US-GuyNeural", wav)
        api, _ = prepare_prompt("05_ref2va_4step_sla.json", portrait, audio_rel=rel, prompt=PROMPT_EN,
                                width=768, height=768, length=124,
                                filename_prefix="zaopianju_dynamic/h3_refva_en")
        res = run_and_fetch("refva_en", api, os.path.join(REPO, "tmp", "h3_refva_test_en.mp4"))
    elif test == "fl2v":
        first = os.path.join(REPO, "outputs", "concepts", "laozhou", "consistent", "three_view", "front.png")
        last = os.path.join(REPO, "outputs", "concepts", "laozhou", "consistent", "three_view", "three_quarter.png")
        api, _ = prepare_prompt("04_i2v_fl2v_4step_sla.json", first, last_image=last, prompt=PROMPT_FL2V,
                                width=768, height=768, length=124,
                                filename_prefix="zaopianju_dynamic/h3_fl2v")
        res = run_and_fetch("fl2v", api, os.path.join(REPO, "tmp", "h3_fl2v_test.mp4"))
    else:
        raise SystemExit("unknown test: %s" % test)
    print(json.dumps({"ok": True, **res}, ensure_ascii=False))


if __name__ == "__main__":
    main()
