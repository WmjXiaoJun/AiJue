# -*- coding: utf-8 -*-
"""V6.5 本地 MiniMax-H3 文生视频（ComfyUI）生成器：云端余额不足后的本地兜底。

与 cinema3d.py 共用：
- briefs：contract/v5/cinema3d/relic_talk_3d_briefs.json
- episode：outputs/templates_v5/relic_talk_3d/work/episode3d.json
- 输出：outputs/templates_v5/relic_talk_3d/shots/<sid>.mp4（已有有效文件自动跳过）
- 报告：outputs/templates_v5/relic_talk_3d/local_generation_report.json

做法：读取本地 H3 i2v API workflow（integrations/comfyui/api_workflows/04_i2v_api.json），
去掉首帧 LoadImage 输入 → 纯文生视频（同一模型权重 T2VA），4step turbo。
ComfyUI 单卡串行（12GB），一次一个镜头。

用法：
    python -m pipeline.cinema3d_local --only v5_027,v5_028 --resolution 1024x576
    python -m pipeline.cinema3d_local --limit 5            # 按 episode 顺序补 5 个缺失镜头
    python -m pipeline.cinema3d_local                      # 补齐全部缺失镜头
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
import time
import urllib.request
import uuid
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from pipeline import cinema3d  # noqa: E402
from pipeline.cinema3d import (  # noqa: E402
    BRIEFS, EP3D, FRAMES, OUT, REPORT, SHOTS, WORK, compose_prompt, load_json, valid_clip, write_json,
)

SERVER = "http://127.0.0.1:8188"
TEMPLATE = REPO / "integrations/comfyui/api_workflows/04_i2v_api.json"
LOCAL_REPORT = OUT / "local_generation_report.json"
DUR_KEY = cinema3d.CARD_DUR


def frame_length(duration: float) -> int:
    """长度取 17k+5（24fps），并按 ceil 保证不短于镜头时长（后期要精确裁到 dur）。"""
    import math
    frames = max(124.0, float(duration or 5.0) * 24.0)
    k = max(7, int(math.ceil((frames - 5.0) / 17.0 - 1e-9)))
    return 17 * k + 5


def build_workflow(prompt: str, width: int, height: int, length: int, seed: int,
                   filename_prefix: str) -> dict:
    d = json.loads(TEMPLATE.read_text(encoding="utf-8"))
    d.pop("18", None)
    d["7"]["inputs"].pop("first_frame", None)
    d["7"]["inputs"].update({"width": width, "height": height, "length": length, "prompt": prompt})
    d["9"]["inputs"]["noise_seed"] = int(seed)
    d["11"]["inputs"]["steps"] = 4
    d["16"]["inputs"]["filename_prefix"] = filename_prefix
    return d


def submit(prompt_api: dict) -> str:
    body = json.dumps({"prompt": prompt_api, "client_id": uuid.uuid4().hex}).encode("utf-8")
    req = urllib.request.Request(SERVER + "/prompt", data=body,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=120) as r:
        j = json.loads(r.read().decode("utf-8"))
    if j.get("node_errors"):
        raise RuntimeError(f"ComfyUI node_errors: {json.dumps(j['node_errors'], ensure_ascii=False)[:800]}")
    return j["prompt_id"]


def wait_and_fetch(pid: str, out: Path, timeout: float = 3600) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        time.sleep(3)
        try:
            with urllib.request.urlopen(SERVER + "/history/" + pid, timeout=60) as r:
                h = json.loads(r.read().decode("utf-8"))
        except Exception:  # noqa: BLE001
            continue
        rec = h.get(pid)
        if not rec:
            continue
        if str(rec.get("status", {}).get("status_str", "")).lower() == "error":
            msgs = json.dumps(rec.get("status", {}), ensure_ascii=False)[:800]
            raise RuntimeError(f"ComfyUI 执行失败: {msgs}")
        outputs = rec.get("outputs") or {}
        for _node, val in outputs.items():
            for key in ("videos", "gifs", "images"):
                for item in val.get(key, []):
                    fn = item.get("filename")
                    q = urllib.parse.urlencode({"filename": fn,
                                                "subfolder": item.get("subfolder", ""),
                                                "type": item.get("type", "output")})
                    data = urllib.request.urlopen(SERVER + "/view?" + q, timeout=300).read()
                    out.parent.mkdir(parents=True, exist_ok=True)
                    out.write_bytes(data)
                    return {"file": str(out), "bytes": len(data), "raw": fn}
        return {"error": "history 无输出"}
    raise TimeoutError(f"等待 ComfyUI 超时: {pid}")


import urllib.parse  # noqa: E402  (wait_and_fetch 使用)


def generate_one(briefs: dict, shot: dict, resolution: tuple[int, int], seed: int,
                 force: bool) -> dict:
    sid = shot["id"]
    out = SHOTS / f"{sid}.mp4"
    dur = float(DUR_KEY.get(sid, shot.get("duration") or 5.0))
    length = frame_length(dur)
    need = max(3.0, dur - 0.05)  # 必须覆盖镜头时长，后期才能精确裁切
    if not force and valid_clip(out, need):
        return {"id": sid, "status": "cached", "seconds": 0}
    note = ""
    key = next((x.get("local_setting_key") for x in briefs["shots"] if x["id"] == sid), None)
    if key and key in briefs.get("local_setting", {}):
        note = "Setting: " + briefs["local_setting"][key].rstrip(".") + "."
    prompt = compose_prompt(briefs, shot,
                            local_extra=(briefs.get("local_style_suffix", "") + " " + note).strip())
    t0 = time.time()
    try:
        wf = build_workflow(prompt, resolution[0], resolution[1], length, seed + abs(hash(sid)) % 100000,
                            f"zaopianju_local/{sid}")
        pid = submit(wf)
        res = wait_and_fetch(pid, out, timeout=2400)
        sec = round(time.time() - t0, 1)
        if "error" in res:
            return {"id": sid, "status": "failed", "seconds": sec, "error": res["error"]}
        meta = {"ok": True, "provider": "local-comfyui-h3", "prompt_id": pid, "mode": "t2va",
                "resolution": list(resolution), "length": length, "fps": 24,
                "prompt": prompt, "out": str(out), "elapsed_sec": sec}
        write_json(SHOTS / f"{sid}.meta.json", meta)
        try:
            cinema3d.make_sheet(out, FRAMES / f"{sid}_sheet.png")
        except Exception:  # noqa: BLE001
            pass
        return {"id": sid, "status": "ok", "seconds": sec, "prompt_id": pid}
    except Exception as exc:  # noqa: BLE001
        return {"id": sid, "status": "failed", "seconds": round(time.time() - t0, 1),
                "error": f"{type(exc).__name__}: {exc}"[-500:]}


def main(argv=None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass
    ap = argparse.ArgumentParser(description="本地 MiniMax-H3 文生视频（T2VA）")
    ap.add_argument("--only", default="")
    ap.add_argument("--limit", type=int, default=0, help="按时间线顺序补 N 个缺失镜头")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--resolution", default="1024x576")
    ap.add_argument("--seed", type=int, default=20261101)
    a = ap.parse_args(argv)
    width, height = (int(x) for x in a.resolution.lower().split("x"))

    briefs = load_json(BRIEFS)
    ep = load_json(EP3D) if EP3D.is_file() else cinema3d.build_episode3d()
    shots = ep["shots"]
    only = [x.strip() for x in a.only.split(",") if x.strip()]
    if only:
        todo = [s for s in shots if s["id"] in only]
    else:
        todo = []
        for s in shots:
            sid = s["id"]
            dur = float(DUR_KEY.get(sid, s.get("duration") or 5.0))
            if a.force or not valid_clip(SHOTS / f"{sid}.mp4", max(3.0, dur - 1.5)):
                todo.append(s)
        if a.limit:
            todo = todo[:a.limit]
    print(f"本地 H3 待生成 {len(todo)} 镜，{a.resolution}", flush=True)
    report = {"started": time.strftime("%Y-%m-%dT%H:%M:%S"), "total": len(todo), "results": []}
    t0 = time.time()
    for i, s in enumerate(todo, 1):
        res = generate_one(briefs, s, (width, height), a.seed, a.force)
        report["results"].append(res)
        report["elapsed_sec"] = round(time.time() - t0, 1)
        write_json(LOCAL_REPORT, report)
        print(f"[{i}/{len(todo)}] {res['id']} {res['status']} {res.get('seconds', 0)}s "
              f"(total {report['elapsed_sec']}s)", flush=True)
    ok = sum(1 for r in report["results"] if r["status"] in ("ok", "cached"))
    failed = [r["id"] for r in report["results"] if r["status"] == "failed"]
    report.update({"finished": time.strftime("%Y-%m-%dT%H:%M:%S"), "ok": ok, "failed": failed})
    write_json(LOCAL_REPORT, report)
    print(json.dumps({"ok": ok, "failed": failed, "elapsed_sec": report["elapsed_sec"]},
                     ensure_ascii=False))
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
