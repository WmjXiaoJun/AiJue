#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
MiniMax H3 本地真实生成 CLI（ComfyUI 后端）
==========================================
一条命令跑通：确保 ComfyUI 在线(必要时自动启动) -> 转换 workflow -> 传首帧 ->
提交 /prompt -> 轮询 /history -> 取回 mp4 -> ffprobe 校验 -> 抽帧 -> 打印 JSON 摘要。

典型用法：
  python run_h3.py --image tmp/test_input.png --prompt "integrated_multimodal_description: ..." \
      --out outputs/h3/test_001.mp4
  python run_h3.py --image outputs/characters/laozhou/portrait.png --prompt-file tmp/laozhou.txt \
      --out outputs/h3/laozhou_h3.mp4 --width 768 --height 768 --length 124 --seed 20261024

关键参数：
  --workflow   工作流（默认 04_i2v_fl2v_4step_sla.json，含 LoadImage->first_frame）
  --width/--height  省略时按输入图长宽比、短边=--short-side(默认768)、32 对齐推导
  --length     帧数（24fps；124≈5.17s，17k+5 网格）
  --timeout    等待生成完成的总秒数（默认 900）
  --dry-run    只转换并打印 API prompt，不提交

仅依赖 Python 标准库 + ffmpeg/ffprobe（repo 内 tools/node_modules 自带）。
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, HERE)

from workflow_to_api import (  # noqa: E402
    DEFAULT_SERVER, fetch_object_info, load_workflow, resolve_workflow,
    ui_to_api, apply_overrides, summarize,
)

LOG_DIR = os.path.join(HERE, "logs")
OUT_DIR = os.path.join(REPO_ROOT, "outputs", "h3")
DEFAULT_COMFY_ROOT = os.environ.get("COMFYUI_ROOT", r"D:\BaiduSyncdisk\wmj_work\XJ\ComfyUI")
DEFAULT_WORKFLOW = "04_i2v_fl2v_4step_sla.json"

# 各类节点在生成流程里的角色（用于定位要覆盖的字段）
NODE_IMAGE_TO_VIDEO = ("MiniMaxH3ImageToVideo", "MiniMaxH3ReferenceToVideo")
NODE_LOAD_IMAGE = "LoadImage"
NODE_NOISE = "RandomNoise"
NODE_SAVE_VIDEO = "SaveVideo"


# --------------------------------------------------------------------------- #
# 小工具
# --------------------------------------------------------------------------- #
def now() -> float:
    return time.time()


def fmt_sec(s: float) -> str:
    s = float(s)
    if s < 60:
        return "%.1fs" % s
    return "%dm%02ds" % (int(s // 60), int(s % 60))


def find_bin(env_name: str, candidates: list) -> str:
    v = os.environ.get(env_name)
    if v and os.path.isfile(v):
        return v
    for c in candidates:
        c = os.path.abspath(os.path.join(REPO_ROOT, c)) if not os.path.isabs(c) else c
        if os.path.isfile(c):
            return c
    raise SystemExit("找不到 %s，可用环境变量 %s 指定" % (env_name, env_name))


def ffmpeg_bin() -> str:
    return find_bin("FFMPEG_BIN", [
        "tools/node_modules/ffmpeg-static/ffmpeg.exe",
        "tools/node_modules/ffmpeg-static/ffmpeg",
    ])


def ffprobe_bin() -> str:
    return find_bin("FFPROBE_BIN", [
        "tools/node_modules/ffprobe-static/bin/win32/x64/ffprobe.exe",
        "tools/node_modules/ffprobe-static/bin/win32/ia32/ffprobe.exe",
        "tools/node_modules/ffprobe-static/bin/linux/x64/ffprobe",
    ])


def http_json(url: str, data=None, timeout: float = 60.0):
    """GET/POST JSON；data 不为 None 时 POST。"""
    headers = {"Content-Type": "application/json"}
    body = None if data is None else json.dumps(data).encode("utf-8")
    req = urllib.request.Request(url, data=body, headers=headers,
                                 method="GET" if data is None else "POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read().decode("utf-8", "replace")
    return json.loads(raw) if raw.strip() else {}


def http_post_json(url: str, data, timeout: float = 120.0):
    """POST 并返回 (status, body)；HTTP 4xx 也返回 body 而不是抛异常。"""
    body = json.dumps(data).encode("utf-8")
    req = urllib.request.Request(url, data=body,
                                 headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read().decode("utf-8", "replace")
            return r.status, (json.loads(raw) if raw.strip() else {})
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace")
        try:
            parsed = json.loads(raw)
        except Exception:
            parsed = {"raw": raw}
        return e.code, parsed


def probe_image(path: str) -> dict:
    """ffprobe 读图片宽高。"""
    cmd = [ffprobe_bin(), "-v", "error", "-select_streams", "v:0",
           "-show_entries", "stream=width,height", "-of", "json", path]
    out = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if out.returncode != 0:
        raise SystemExit("ffprobe 读图失败: %s\n%s" % (path, out.stderr[-800:]))
    st = json.loads(out.stdout)["streams"][0]
    return {"width": int(st["width"]), "height": int(st["height"])}


def probe_video(path: str) -> dict:
    cmd = [ffprobe_bin(), "-v", "error", "-show_format", "-show_streams", "-of", "json", path]
    out = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if out.returncode != 0:
        raise RuntimeError("ffprobe 校验失败: %s\n%s" % (path, out.stderr[-800:]))
    info = json.loads(out.stdout)
    fmt = info.get("format", {})
    v = next((s for s in info.get("streams", []) if s.get("codec_type") == "video"), None)
    a = next((s for s in info.get("streams", []) if s.get("codec_type") == "audio"), None)
    if v is None:
        raise RuntimeError("产物没有视频流: %s" % path)
    num, den = (v.get("avg_frame_rate") or "0/1").split("/")
    fps = (float(num) / float(den)) if float(den or 1) != 0 else None
    nb = v.get("nb_frames")
    return {
        "duration_sec": round(float(fmt.get("duration") or v.get("duration") or 0), 3),
        "size_bytes": int(fmt.get("size") or os.path.getsize(path)),
        "format": fmt.get("format_name"),
        "video": {
            "codec": v.get("codec_name"), "width": int(v["width"]), "height": int(v["height"]),
            "fps": round(fps, 3) if fps else None, "nb_frames": int(nb) if nb else None,
            "pix_fmt": v.get("pix_fmt"),
        },
        "audio": None if not a else {
            "codec": a.get("codec_name"), "sample_rate": a.get("sample_rate"),
            "channels": a.get("channels"),
        },
        "has_audio": a is not None,
    }


def snap(x: float, step: int = 32, lo: int = 256) -> int:
    return max(lo, int(round(x / step)) * step)


def derive_resolution(img_wh: dict, short_side: int):
    """按短边=short_side 等比缩放，宽高 32 对齐。"""
    w, h = img_wh["width"], img_wh["height"]
    scale = float(short_side) / min(w, h)
    return snap(w * scale), snap(h * scale)


# --------------------------------------------------------------------------- #
# ComfyUI 服务管理
# --------------------------------------------------------------------------- #
def parse_server(server: str):
    u = urllib.parse.urlparse(server if "://" in server else "http://" + server)
    return u.hostname or "127.0.0.1", u.port or 8188


def server_alive(server: str, timeout: float = 3.0) -> bool:
    try:
        st = http_json(server.rstrip("/") + "/system_stats", timeout=timeout)
        return "system" in st
    except Exception:
        return False


def ensure_server(server: str, comfy_root: str, do_start: bool, log,
                  start_timeout: float = 300.0) -> dict:
    """确认 ComfyUI 在线；离线且允许时后台启动。返回 {started, log_out, log_err, stats}。"""
    if server_alive(server):
        log("[server] 已在线: %s" % server)
        return {"started": False, "stats": http_json(server.rstrip("/") + "/system_stats")}
    if not do_start:
        raise SystemExit("ComfyUI 未运行且指定了 --no-start: %s" % server)

    host, port = parse_server(server)
    py = os.path.join(comfy_root, ".venv", "Scripts", "python.exe")
    if not os.path.isfile(py):
        py = os.path.join(comfy_root, ".venv", "bin", "python")
    if not os.path.isfile(py) or not os.path.isfile(os.path.join(comfy_root, "main.py")):
        raise SystemExit("ComfyUI 目录无效（缺 main.py 或 .venv python）: %s" % comfy_root)

    os.makedirs(LOG_DIR, exist_ok=True)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    log_out = os.path.join(LOG_DIR, "comfyui_%s.out.log" % stamp)
    log_err = os.path.join(LOG_DIR, "comfyui_%s.err.log" % stamp)
    env = dict(os.environ)
    # 关键：重定向日志到文件时，Python 默认用 GBK 写 stderr，遇到 emoji/中文会
    # UnicodeEncodeError 直接把启动流程打崩，必须强制 UTF-8。
    env.setdefault("PYTHONUTF8", "1")
    env.setdefault("PYTHONIOENCODING", "utf-8")
    env.setdefault("PYTHONUNBUFFERED", "1")
    fo = open(log_out, "w", encoding="utf-8", errors="replace")
    fe = open(log_err, "w", encoding="utf-8", errors="replace")
    flags = 0x08000000 if os.name == "nt" else 0  # CREATE_NO_WINDOW
    proc = subprocess.Popen(
        [py, "main.py", "--listen", host, "--port", str(port)],
        cwd=comfy_root, env=env, stdout=fo, stderr=fe,
        creationflags=flags, stdin=subprocess.DEVNULL,
    )
    log("[server] 启动 ComfyUI pid=%d  %s" % (proc.pid, server))
    log("[server] 日志: %s | %s" % (log_out, log_err))

    t0 = now()
    while now() - t0 < start_timeout:
        if proc.poll() is not None:
            raise SystemExit("ComfyUI 启动后退出 (exit=%s)，见 %s" % (proc.returncode, log_err))
        if server_alive(server):
            log("[server] 就绪，用时 %s" % fmt_sec(now() - t0))
            return {"started": True, "pid": proc.pid, "log_out": log_out,
                    "log_err": log_err, "stats": http_json(server.rstrip("/") + "/system_stats")}
        time.sleep(2)
    raise SystemExit("ComfyUI 启动超时 (%ss)，见 %s" % (start_timeout, log_err))


# --------------------------------------------------------------------------- #
# workflow 准备
# --------------------------------------------------------------------------- #
def find_node(prompt: dict, class_types) -> tuple:
    if isinstance(class_types, str):
        class_types = (class_types,)
    for nid, node in prompt.items():
        if node["class_type"] in class_types:
            return nid, node
    return None, None


def prepare_prompt(args, object_info, log):
    wf_path = resolve_workflow(args.workflow)
    workflow = load_workflow(wf_path)
    prompt, warnings = ui_to_api(workflow, object_info, log=log)
    for w in warnings:
        log("[warn] " + w)

    # --- 首帧：拷贝到 ComfyUI/input/<subfolder>/ 并写入 LoadImage ---
    img_abs = os.path.abspath(args.image)
    if not os.path.isfile(img_abs):
        raise SystemExit("输入图不存在: %s" % img_abs)
    img_wh = probe_image(img_abs)
    sub = args.subfolder.strip("/\\")
    os.makedirs(os.path.join(args.comfy_input, sub), exist_ok=True)
    # 用「输出文件名_原图名」做 ComfyUI/input 里的文件名，避免多个角色都叫
    # portrait.png 时互相覆盖（并发/排队时 LoadImage 可能读到别人的图）。
    run_stem = os.path.splitext(os.path.basename(args.out))[0]
    src_name = os.path.basename(img_abs)
    src_base, src_ext = os.path.splitext(src_name)
    dest_name = "%s_%s%s" % (run_stem, src_base, src_ext.lower())
    rel_name = "%s/%s" % (sub, dest_name)
    dst = os.path.join(args.comfy_input, sub, dest_name)
    shutil.copyfile(img_abs, dst)
    log("[input] 首帧已拷贝: %s -> %s" % (img_abs, dst))

    load_nid, load_node = find_node(prompt, NODE_LOAD_IMAGE)
    if load_nid is None:
        raise SystemExit("workflow 里没有 LoadImage 节点，无法做 i2v（可用 --workflow 指向含 LoadImage 的工作流）")
    load_node["inputs"]["image"] = rel_name
    log("[input] LoadImage#%s.image = %s" % (load_nid, rel_name))

    # --- 目标分辨率 / 帧数 ---
    if args.width and args.height:
        width, height = int(args.width), int(args.height)
    else:
        width, height = derive_resolution(img_wh, args.short_side)
    log("[size] 输入图 %dx%d -> 生成 %dx%d (length=%d, %.2fs@%dfps)"
        % (img_wh["width"], img_wh["height"], width, height, args.length,
           args.length / float(args.fps), args.fps))

    # --- H3 条件节点 ---
    h3_nid, h3_node = find_node(prompt, NODE_IMAGE_TO_VIDEO)
    if h3_nid is None:
        raise SystemExit("workflow 里没有 MiniMaxH3ImageToVideo/ReferenceToVideo 节点")
    h3_node["inputs"]["prompt"] = args.prompt
    h3_node["inputs"]["width"] = width
    h3_node["inputs"]["height"] = height
    h3_node["inputs"]["length"] = int(args.length)
    log("[h3] node#%s %s prompt=%d 字符, %dx%d, length=%d"
        % (h3_nid, h3_node["class_type"], len(args.prompt), width, height, args.length))

    # --- seed ---
    seed = args.seed if args.seed is not None else 20261024
    noise_nid, noise_node = find_node(prompt, NODE_NOISE)
    if noise_nid is not None:
        noise_node["inputs"]["noise_seed"] = int(seed)

    # --- 输出前缀 ---
    save_nid, save_node = find_node(prompt, NODE_SAVE_VIDEO)
    if save_nid is None:
        raise SystemExit("workflow 里没有 SaveVideo 节点")
    out_stem = os.path.splitext(os.path.basename(args.out))[0]
    prefix = "%s/%s" % (sub, out_stem)
    save_node["inputs"]["filename_prefix"] = prefix
    log("[output] SaveVideo#%s.filename_prefix = %s" % (save_nid, prefix))

    prompt = apply_set_overrides(prompt, args, object_info)
    return prompt, {
        "workflow": wf_path, "wf_name": os.path.basename(wf_path),
        "image": img_abs, "image_wh": img_wh, "image_rel": rel_name,
        "width": width, "height": height, "length": int(args.length),
        "fps": args.fps, "seed": int(seed), "save_node": save_nid,
        "h3_node": h3_nid, "load_node": load_nid,
    }


def apply_set_overrides(prompt: dict, args, object_info: dict) -> dict:
    if not args.set:
        return prompt
    ws = apply_overrides(prompt, args.set, object_info)
    for w in ws:
        print("[warn] " + w)
    return prompt


# --------------------------------------------------------------------------- #
# 提交 / 轮询
# --------------------------------------------------------------------------- #
def submit(server: str, prompt: dict, log) -> str:
    client_id = uuid.uuid4().hex
    payload = {"prompt": prompt, "client_id": client_id}
    status, body = http_post_json(server.rstrip("/") + "/prompt", payload, timeout=120)
    if status != 200 or "prompt_id" not in body:
        log("[submit] 失败 HTTP %s" % status)
        log(json.dumps(body, ensure_ascii=False, indent=2)[:6000])
        raise RuntimeError("ComfyUI 拒绝 prompt (HTTP %s): %s"
                           % (status, json.dumps(body, ensure_ascii=False)[:1500]))
    pid = body["prompt_id"]
    log("[submit] 已入队 prompt_id=%s (queue_remaining=%s)"
        % (pid, body.get("number", "?")))
    return pid


def wait_prompt(server: str, prompt_id: str, timeout: float, log, poll: float = 2.0) -> dict:
    base = server.rstrip("/")
    t0 = now()
    last_report = 0.0
    while True:
        elapsed = now() - t0
        try:
            hist = http_json(base + "/history/" + prompt_id, timeout=30)
        except Exception as e:
            log("[poll] /history 异常（忽略）: %s" % e)
            hist = {}
        if prompt_id in hist:
            rec = hist[prompt_id]
            st = rec.get("status") or {}
            status_str = st.get("status_str") or ("success" if st.get("completed") else "?")
            if status_str == "error" or not st.get("completed", status_str == "success"):
                log("[poll] 生成失败 status=%s" % status_str)
                for m in st.get("messages") or []:
                    log("  %s" % json.dumps(m, ensure_ascii=False)[:1200])
                raise RuntimeError("ComfyUI 执行失败: %s" % json.dumps(st, ensure_ascii=False)[:2000])
            log("[poll] 完成，用时 %s" % fmt_sec(elapsed))
            return rec
        # 队列位置
        try:
            q = http_json(base + "/queue", timeout=15)
            running = [it[1] for it in (q.get("queue_running") or []) if len(it) > 1]
            pending = [it[1] for it in (q.get("queue_pending") or []) if len(it) > 1]
            state = "running" if prompt_id in running else ("pending #%d" % (pending.index(prompt_id) + 1)
                                                            if prompt_id in pending else "unknown")
        except Exception:
            state = "?"
        if elapsed > timeout:
            log("[poll] 超时 %s，尝试 /interrupt" % fmt_sec(elapsed))
            try:
                http_post_json(base + "/interrupt", {}, timeout=10)
            except Exception:
                pass
            raise TimeoutError("等待生成超时 (%ss)，prompt_id=%s" % (timeout, prompt_id))
        if now() - last_report > 15 or elapsed < 5:
            log("[poll] %s 已等待 %s（%s）" % (prompt_id[:8], fmt_sec(elapsed), state))
            last_report = now()
        time.sleep(poll)


def collect_outputs(rec: dict, comfy_root: str, server: str, out_path: str, log) -> list:
    """从 history outputs 里找所有产出文件，下载/拷贝到 out_path（同名校验）。"""
    outputs = rec.get("outputs") or {}
    files = []  # (filename, subfolder, type)
    for nid, node_out in outputs.items():
        for key, items in (node_out or {}).items():
            if not isinstance(items, list):
                continue
            for it in items:
                if isinstance(it, dict) and it.get("filename"):
                    files.append((it["filename"], it.get("subfolder") or "", it.get("type") or "output"))
    log("[output] history 产出: %s" % json.dumps(files, ensure_ascii=False))
    if not files:
        raise RuntimeError("history 里没有产出文件: %s" % json.dumps(outputs, ensure_ascii=False)[:1500])

    # 优先视频类（扩展名），其次第一个
    files.sort(key=lambda f: (0 if os.path.splitext(f[0])[1].lower() in
                              (".mp4", ".webm", ".mkv", ".mov") else 1, f[0]))
    filename, subfolder, typ = files[0]
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)

    local_candidates = []
    if typ == "output":
        local_candidates.append(os.path.join(comfy_root, "output", subfolder, filename))
    if subfolder:
        local_candidates.append(os.path.join(comfy_root, subfolder, filename))

    copied = None
    for cand in local_candidates:
        if os.path.isfile(cand):
            shutil.copyfile(cand, out_path)
            copied = "local:" + cand
            break
    if copied is None:
        url = server.rstrip("/") + "/view?" + urllib.parse.urlencode(
            {"filename": filename, "subfolder": subfolder, "type": typ})
        with urllib.request.urlopen(url, timeout=300) as r, open(out_path, "wb") as f:
            shutil.copyfileobj(r, f)
        copied = "http:" + url
    log("[output] 已取回: %s (%d bytes) <- %s" % (out_path, os.path.getsize(out_path), copied))
    return files


def extract_frames(video: str, out_stem: str, log) -> dict:
    """抽首帧和中间帧到 outputs/h3/*.png。"""
    ff = ffmpeg_bin()
    frames = {}
    try:
        dur = probe_video(video)["duration_sec"]
    except Exception:
        dur = 0
    targets = [("f000", "0"), ("fmid", str(max(0.0, dur / 2.0)))]
    for tag, ss in targets:
        p = "%s_%s.png" % (out_stem, tag)
        cmd = [ff, "-y", "-v", "error", "-ss", ss, "-i", video, "-frames:v", "1", p]
        r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
        if r.returncode == 0 and os.path.isfile(p):
            frames[tag] = p
            log("[frames] %s" % p)
    return frames


# --------------------------------------------------------------------------- #
# main
# --------------------------------------------------------------------------- #
def build_argparser():
    ap = argparse.ArgumentParser(description="MiniMax H3 本地 i2v 生成（ComfyUI 后端）")
    ap.add_argument("--image", required=True, help="首帧图片路径（png/jpg）")
    ap.add_argument("--prompt", default=None, help="H3 提示词（与 --prompt-file 二选一）")
    ap.add_argument("--prompt-file", default=None, help="从文件读提示词（UTF-8）")
    ap.add_argument("--out", required=True, help="输出 mp4 路径")
    ap.add_argument("--workflow", default=DEFAULT_WORKFLOW,
                    help="工作流文件名或路径（默认 %(default)s）")
    ap.add_argument("--seed", type=int, default=None, help="随机种子（默认 20261024）")
    ap.add_argument("--length", type=int, default=124, help="帧数，24fps，124≈5.17s（默认）")
    ap.add_argument("--width", type=int, default=None, help="生成宽（默认按图推导）")
    ap.add_argument("--height", type=int, default=None, help="生成高（默认按图推导）")
    ap.add_argument("--short-side", type=int, default=768, help="推导分辨率时的短边（默认 768=768p）")
    ap.add_argument("--fps", type=int, default=24, help="输出帧率（默认 24）")
    ap.add_argument("--timeout", type=float, default=900.0, help="等待生成完成超时秒数（默认 900）")
    ap.add_argument("--server", default=DEFAULT_SERVER, help="ComfyUI 地址（默认 %(default)s）")
    ap.add_argument("--comfy-root", default=DEFAULT_COMFY_ROOT, help="ComfyUI 根目录")
    ap.add_argument("--subfolder", default="zaopianju",
                    help="ComfyUI/input 与 output 下使用的子目录（默认 zaopianju）")
    ap.add_argument("--no-start", action="store_true", help="不自动启动 ComfyUI（要求已在线）")
    ap.add_argument("--start-timeout", type=float, default=300.0, help="自动启动等待秒数")
    ap.add_argument("--dry-run", action="store_true", help="只转换+打印，不提交生成")
    ap.add_argument("--set", action="append", default=[], metavar="NODE.FIELD=VALUE",
                    help="额外覆盖 API prompt 输入，可多次")
    ap.add_argument("--quiet", action="store_true", help="少打印")
    return ap


def main(argv=None) -> int:
    # Windows 下控制台/重定向默认 GBK，中文日志会被 harness 按 UTF-8 读成乱码
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    args = build_argparser().parse_args(argv)
    if not args.prompt and not args.prompt_file:
        raise SystemExit("必须提供 --prompt 或 --prompt-file")
    if args.prompt_file:
        with open(args.prompt_file, "r", encoding="utf-8-sig") as f:
            args.prompt = f.read().strip()
    os.makedirs(LOG_DIR, exist_ok=True)
    os.makedirs(OUT_DIR, exist_ok=True)
    args.comfy_input = os.path.join(args.comfy_root, "input")

    run_stamp = time.strftime("%Y%m%d_%H%M%S")
    run_log_path = os.path.join(LOG_DIR, "run_%s.log" % run_stamp)
    log_fh = open(run_log_path, "w", encoding="utf-8")

    def log(msg=""):
        line = "[%s] %s" % (time.strftime("%H:%M:%S"), msg)
        print(line, flush=True)
        log_fh.write(line + "\n")
        log_fh.flush()

    summary = {"ok": False, "script": "run_h3.py", "args": {
        "image": os.path.abspath(args.image), "out": os.path.abspath(args.out),
        "workflow": args.workflow, "seed": args.seed, "length": args.length,
        "width": args.width, "height": args.height, "short_side": args.short_side,
        "fps": args.fps, "server": args.server,
    }, "run_log": run_log_path}
    t_start = now()
    try:
        log("=" * 78)
        log("MiniMax H3 i2v | %s -> %s" % (os.path.basename(args.image), args.out))
        log("server=%s comfy_root=%s" % (args.server, args.comfy_root))
        srv = ensure_server(args.server, args.comfy_root, not args.no_start, log,
                            start_timeout=args.start_timeout)
        summary["server"] = {"url": args.server, "started": srv.get("started"),
                             "stats": srv.get("stats", {}).get("system", {})}

        t0 = now()
        object_info = fetch_object_info(args.server)
        log("[schema] /object_info: %d 个节点类，用时 %s" % (len(object_info), fmt_sec(now() - t0)))

        prompt, meta = prepare_prompt(args, object_info, log)
        summary.update({k: meta[k] for k in
                        ("workflow", "wf_name", "width", "height", "length", "seed", "fps")})
        summary["image"] = {"path": meta["image"], "width": meta["image_wh"]["width"],
                            "height": meta["image_wh"]["height"], "comfy_rel": meta["image_rel"]}
        log("[prompt] 节点数=%d" % len(prompt))
        if not args.quiet:
            log(summarize(prompt))
        api_dir = os.path.join(HERE, "api_workflows")
        os.makedirs(api_dir, exist_ok=True)
        api_path = os.path.join(api_dir, "%s.api.json" % os.path.splitext(os.path.basename(args.out))[0])
        with open(api_path, "w", encoding="utf-8") as f:
            json.dump(prompt, f, ensure_ascii=False, indent=2)
        summary["api_prompt"] = api_path
        log("[prompt] API prompt 已存: %s" % api_path)
        if args.dry_run:
            summary["ok"] = True
            summary["dry_run"] = True
            summary["elapsed_sec"] = round(now() - t_start, 1)
            print(json.dumps(summary, ensure_ascii=False))
            return 0

        pid = submit(args.server, prompt, log)
        summary["prompt_id"] = pid
        t_gen = now()
        rec = wait_prompt(args.server, pid, args.timeout, log)
        summary["gen_sec"] = round(now() - t_gen, 1)

        files = collect_outputs(rec, args.comfy_root, args.server, args.out, log)
        summary["comfy_files"] = files
        verify = probe_video(args.out)
        summary["ffprobe"] = verify
        log("[verify] %s  %.2fs  %dx%d  %s  audio=%s  %.2fMB"
            % (os.path.basename(args.out), verify["duration_sec"],
               verify["video"]["width"], verify["video"]["height"],
               verify["video"]["codec"], verify["has_audio"],
               verify["size_bytes"] / 1e6))
        stem = os.path.join(OUT_DIR, os.path.splitext(os.path.basename(args.out))[0])
        summary["frames"] = extract_frames(args.out, stem, log)
        summary["ok"] = True
        summary["elapsed_sec"] = round(now() - t_start, 1)
        log("DONE ok=true 总耗时 %s" % fmt_sec(now() - t_start))
    except Exception as e:
        summary["ok"] = False
        summary["error"] = "%s: %s" % (type(e).__name__, e)
        log("[error] %s" % summary["error"])
        # 附上 ComfyUI 服务端日志尾部，便于复盘
        try:
            logs = sorted(
                [os.path.join(LOG_DIR, f) for f in os.listdir(LOG_DIR) if f.startswith("comfyui_")],
                key=os.path.getmtime, reverse=True)
            if logs:
                summary["server_log"] = logs[0]
                log("[error] ComfyUI 日志: %s" % logs[0])
        except Exception:
            pass
    finally:
        summary["elapsed_sec"] = round(now() - t_start, 1)
        meta_path = os.path.splitext(os.path.abspath(args.out))[0] + ".meta.json"
        try:
            os.makedirs(os.path.dirname(meta_path), exist_ok=True)
            with open(meta_path, "w", encoding="utf-8") as f:
                json.dump(summary, f, ensure_ascii=False, indent=2)
            summary["meta"] = meta_path
        except Exception:
            pass
        log_fh.close()
    print(json.dumps(summary, ensure_ascii=False))
    return 0 if summary.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
