# -*- coding: utf-8 -*-
"""V6.5 全 3D《国宝会说话》生成器：briefs -> 云端 MiniMax-H3 文生视频 -> 3D 镜头素材。

设计（见 docs/PLAN_V6_5_RELIC_3D.md）：
- 数据：contract/v5/cinema3d/relic_talk_3d_briefs.json（每镜 subject/action/camera/env/ambience）
- 输入：outputs/templates_v5/relic_talk/work/episode.json（镜头顺序/时长/台词）
- 调用：integrations.metaso.h3（MetaSo 云端 MiniMax-H3，768P，0.09 元/秒）
- 输出：outputs/templates_v5/relic_talk_3d/shots/<sid>.mp4 + <sid>.meta.json
        outputs/templates_v5/relic_talk_3d/frames/<sid>_sheet.png（2x2 抽帧）
- 断点续跑 / 并发 / 重试 / 成本统计

用法：
    python -m pipeline.cinema3d --pilot 10          # 前 10 个非卡片镜 + 片头/片尾卡片
    python -m pipeline.cinema3d                     # 全部（已有文件自动跳过）
    python -m pipeline.cinema3d --only v5_001,v5_002 --force
    python -m pipeline.cinema3d --dry-run           # 只打印 prompt
"""
from __future__ import annotations

import argparse
import json
import math
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
EPISODE = REPO / "outputs/templates_v5/relic_talk/work/episode.json"
BRIEFS = REPO / "contract/v5/cinema3d/relic_talk_3d_briefs.json"
OUT = REPO / "outputs/templates_v5/relic_talk_3d"
SHOTS = OUT / "shots"
FRAMES = OUT / "frames"
WORK = OUT / "work"
EP3D = WORK / "episode3d.json"
REPORT = OUT / "generation_report.json"
FFMPEG = REPO / "tools/node_modules/ffmpeg-static/ffmpeg.exe"
FFPROBE = REPO / "tools/node_modules/ffprobe-static/bin/win32/x64/ffprobe.exe"

PRICE_PER_SEC = 0.09  # 768P 元/秒
CARD_DUR = {"v5_card_title": 5.0, "v5_card_end": 4.0}

_LOCK = threading.Lock()


def load_json(p: Path) -> dict:
    return json.loads(Path(p).read_text(encoding="utf-8"))


def write_json(p: Path, obj) -> None:
    Path(p).parent.mkdir(parents=True, exist_ok=True)
    Path(p).write_text(json.dumps(obj, ensure_ascii=False, indent=1), encoding="utf-8")


def build_episode3d() -> dict:
    """在 episode.json 基础上调整卡片时长（片头 5s / 片尾 4s），重算 _edit_start。"""
    ep = load_json(EPISODE)
    shots = ep["shots"]
    cursor = 0.0
    for s in shots:
        if s["id"] in CARD_DUR:
            s["duration"] = CARD_DUR[s["id"]]
        s["_edit_start"] = round(cursor, 3)
        cursor += float(s.get("duration") or 0)
    ep["episode"]["total_duration"] = round(cursor, 3)
    ep["meta"]["cinema3d"] = {"total_duration": round(cursor, 3), "provider": "metaso/minimax-h3",
                              "resolution": "768P", "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S")}
    write_json(EP3D, ep)
    return ep


def clip_duration(dur: float) -> int:
    """H3 只接受 4-15 秒整数时长；取 ceil 保证覆盖镜头时长。"""
    return max(4, min(15, int(math.ceil(float(dur) - 1e-6))))


def compose_prompt(briefs: dict, shot: dict) -> str:
    b = next((x for x in briefs["shots"] if x["id"] == shot["id"]), None)
    if b is None:
        raise SystemExit(f"briefs 缺少镜头 {shot['id']}")
    style = b.get("style") or briefs["style_lock"]
    env_key = b.get("env", "")
    env = briefs["env"].get(env_key, env_key)
    chars = "；".join(briefs["chars"][c].rstrip("。") for c in b.get("chars", []))
    subject = b["subject"].strip()
    if not subject.endswith((".", "。", "!", "?")):
        subject += "."
    parts = [f"[Shot 1] {style}. {subject}"]
    if env:
        parts.append(f"Environment: {env}.")
    if chars:
        parts.append(chars + ".")
    if b.get("camera"):
        parts.append(f"Camera: {b['camera']}.")
    parts.append("The motion is natural, cinematic and continuous, with rich physical materials, "
                 "volumetric light and shallow depth of field.")
    parts.append(briefs["negative"] + ".")
    desc = " ".join(parts)
    amb = b.get("ambience") or "quiet cinematic ambience"
    return (f"integrated_multimodal_description: {desc}\n\n"
            f"overall_soundscape: {amb}. No dialogue, no human voices, no music.\n\n"
            f"non_diegetic_music: N/A")


def valid_clip(path: Path, min_dur: float = 3.0) -> bool:
    if not path.is_file() or path.stat().st_size < 100_000:
        return False
    try:
        out = subprocess.run([str(FFPROBE), "-v", "error", "-show_entries", "format=duration",
                              "-of", "default=nk=1:nw=1", str(path)],
                             capture_output=True, text=True, timeout=60)
        return float((out.stdout or "0").strip() or 0) >= min_dur
    except Exception:  # noqa: BLE001
        return False


def make_sheet(clip: Path, sheet: Path) -> None:
    try:
        subprocess.run([str(FFMPEG), "-y", "-hide_banner", "-loglevel", "error", "-i", str(clip),
                        "-vf", "fps=0.8,scale=672:-1,tile=2x2", "-frames:v", "1", str(sheet)],
                       capture_output=True, timeout=120)
    except Exception:  # noqa: BLE001
        pass


def generate_one(briefs: dict, shot: dict, duration: int, force: bool) -> dict:
    sid = shot["id"]
    out = SHOTS / f"{sid}.mp4"
    meta = SHOTS / f"{sid}.meta.json"
    if not force and valid_clip(out, min(3.0, duration - 1.0)):
        return {"id": sid, "status": "cached", "seconds": 0, "attempts": 0, "cost": 0.0}
    prompt = compose_prompt(briefs, shot)
    t0 = time.time()
    last_err = ""
    for attempt in range(1, 4):
        try:
            proc = subprocess.run(
                [sys.executable, "-m", "integrations.metaso.h3",
                 "--prompt", prompt, "--out", str(out), "--resolution", "768P",
                 "--duration", str(duration), "--ratio", "16:9",
                 "--meta", str(meta), "--timeout", "420"],
                cwd=str(REPO), capture_output=True, text=True, encoding="utf-8", errors="replace",
                timeout=600)
            if proc.returncode == 0 and valid_clip(out, min(3.0, duration - 1.0)):
                sheet = FRAMES / f"{sid}_sheet.png"
                make_sheet(out, sheet)
                return {"id": sid, "status": "ok", "seconds": round(time.time() - t0, 1),
                        "attempts": attempt, "cost": round(PRICE_PER_SEC * duration, 2),
                        "sheet": str(sheet)}
            last_err = ((proc.stderr or "") + (proc.stdout or ""))[-400:]
        except Exception as exc:  # noqa: BLE001
            last_err = f"{type(exc).__name__}: {exc}"
        time.sleep(2 * attempt)
    return {"id": sid, "status": "failed", "seconds": round(time.time() - t0, 1),
            "attempts": 3, "cost": 0.0, "error": last_err}


def main(argv=None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass
    ap = argparse.ArgumentParser(description="V6.5 全 3D 国宝会说话：云端 H3 逐镜生成")
    ap.add_argument("--pilot", type=int, default=0, help="只生成前 N 个非卡片镜 + 片头片尾卡片")
    ap.add_argument("--only", default="", help="逗号分隔镜头 id（只生成这些）")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--concurrency", type=int, default=3)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--timeout", type=float, default=0, help="整体运行超时（秒），0=不限")
    a = ap.parse_args(argv)

    for d in (SHOTS, FRAMES, WORK):
        d.mkdir(parents=True, exist_ok=True)

    ep = build_episode3d()
    briefs = load_json(BRIEFS)
    shots = ep["shots"]
    brief_ids = {b["id"] for b in briefs["shots"]}

    todo = []
    only = [x.strip() for x in a.only.split(",") if x.strip()]
    if only:
        todo = [s for s in shots if s["id"] in only]
    elif a.pilot:
        non_card = [s for s in shots if s.get("type") != "card"]
        pick = non_card[:a.pilot]
        card_ids = {"v5_card_title", "v5_card_end"}
        todo = [s for s in shots if s in pick or s["id"] in card_ids]
    else:
        todo = list(shots)
    missing = [s["id"] for s in todo if s["id"] not in brief_ids]
    if missing:
        raise SystemExit(f"briefs 缺少: {missing}")

    if a.dry_run:
        for s in todo:
            d = clip_duration(CARD_DUR.get(s["id"], s.get("duration") or 5))
            print(f"\n===== {s['id']} ({d}s) =====")
            print(compose_prompt(briefs, s))
        return 0

    print(f"生成 {len(todo)} 个镜头，并发 {a.concurrency}", flush=True)
    report = {"started": time.strftime("%Y-%m-%dT%H:%M:%S"), "total": len(todo), "results": []}
    t_start = time.time()
    with ThreadPoolExecutor(max_workers=max(1, a.concurrency)) as pool:
        futs = {}
        for s in todo:
            dur = clip_duration(CARD_DUR.get(s["id"], s.get("duration") or 5))
            futs[pool.submit(generate_one, briefs, s, dur, a.force)] = s["id"]
        done = 0
        for fut in as_completed(futs):
            res = fut.result()
            done += 1
            report["results"].append(res)
            report["elapsed_sec"] = round(time.time() - t_start, 1)
            report["cost_cny"] = round(sum(r.get("cost", 0) for r in report["results"]), 2)
            write_json(REPORT, report)
            print(f"[{done}/{len(todo)}] {res['id']} {res['status']} {res.get('seconds', 0)}s "
                  f"(total {report['elapsed_sec']}s, CNY {report['cost_cny']})", flush=True)
            if a.timeout and time.time() - t_start > a.timeout:
                print("达到整体超时，停止提交剩余任务", flush=True)
                break
    ok = sum(1 for r in report["results"] if r["status"] in ("ok", "cached"))
    failed = [r["id"] for r in report["results"] if r["status"] == "failed"]
    report.update({"finished": time.strftime("%Y-%m-%dT%H:%M:%S"), "ok": ok,
                   "failed": failed, "elapsed_sec": round(time.time() - t_start, 1)})
    write_json(REPORT, report)
    print(json.dumps({"ok": ok, "failed": failed, "elapsed_sec": report["elapsed_sec"],
                      "cost_cny": report["cost_cny"]}, ensure_ascii=False))
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
