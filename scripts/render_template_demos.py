# -*- coding: utf-8 -*-
"""为每个综艺模板生成一支镜头级验证片（基于 OpenMontage 镜头语法参考）。"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FORMATS = ROOT / "contract" / "formats"
OUT = ROOT / "outputs" / "template_demos"
sys.path.insert(0, str(ROOT))
from pipeline import shot_grammar  # noqa: E402

EP_JSON = ROOT / "backend" / "data" / "jobs" / "job_3591291c37_episode.json"

HOST_LINES = [
    "欢迎回来，这一期我们只聊一个关键问题。",
    "先别急着下结论，我们把规则拆开看。",
    "所以，你怎么看这个环节？",
    "如果只能保留一个镜头，你会留哪个？",
]
GUEST_LINES = [
    "我觉得最怕的不是犯错，是太安全。",
    "但观众要的从来不是正确答案。",
    "这个环节的节奏一旦起来，就停不下来。",
    "我反而想看看它什么时候翻车。",
]
REACTION_VISUALS = ["挑眉 · 沉默", "点头 · 记笔记", "憋笑 · 看向镜头"]


def short_name(name: str) -> str:
    return str(name or "").split(" · ")[0].strip()


def build_episode(fmt: dict) -> dict:
    fid = fmt.get("id")
    short = short_name(fmt.get("name"))
    title = "造片局 · %s · 模板验证" % short
    src = json.loads(EP_JSON.read_text(encoding="utf-8"))
    assets = []
    for a in src.get("assets") or []:
        b = dict(a)
        b["episode_id"] = "demo_" + fid
        assets.append(b)
    host = next((a for a in assets if a.get("role") == "host"), assets[0])
    guests = [a for a in assets if a.get("role") != "host"] or [assets[-1]]

    shots = []
    no = 0
    def add(shot_type, duration, speaker=None, line="", visual="", title_=None, subtitle=None, segment_id=""):
        nonlocal no
        no += 1
        plan = shot_grammar.plan_for_type(shot_type, variant=no % 3)
        shots.append({
            "id": "demo_s%03d" % no, "episode_id": "demo_" + fid, "segment_id": segment_id,
            "no": no, "type": shot_type, "duration": round(duration, 2),
            "speaker": speaker.get("id") if speaker else None,
            "speaker_name": speaker.get("name") if speaker else "",
            "line": line, "visual": visual or "%s · %s" % (title_ if title_ else "", shot_grammar.label(plan)),
            "lower_third": None, "punchline": "", "sfx": "", "mood": "",
            "status": "pending", "selected_version_id": None,
            "title": title_, "subtitle": subtitle, "shot_plan": plan,
        })
        return plan

    add("title_card", 2.6, visual="品牌片头", title_=title, subtitle="AI微综艺 · 模板验证", segment_id="open")
    segs = [s for s in (fmt.get("segments") or []) if s.get("id") not in ("open", "closing")]
    host_i = guest_i = 0
    for seg in segs:
        pattern = [p for p in (seg.get("pattern") or ["host_to_camera"]) if p not in ("title_card", "end_card")]
        if not pattern:
            pattern = ["host_to_camera"]
        for t in pattern[:2]:
            if t == "host_to_camera":
                line = HOST_LINES[host_i % len(HOST_LINES)]; host_i += 1
                add(t, 3.6, speaker=host, line=line, segment_id=str(seg.get("id")))
            elif t == "shot_reverse":
                g = guests[guest_i % len(guests)]; guest_i += 1
                line = GUEST_LINES[guest_i % len(GUEST_LINES)]
                add(t, 3.4, speaker=g, line=line, segment_id=str(seg.get("id")))
            elif t == "reaction":
                g = guests[(guest_i + 1) % len(guests)]
                add(t, 2.4, speaker=g, line="", visual="反应 · " + REACTION_VISUALS[no % len(REACTION_VISUALS)], segment_id=str(seg.get("id")))
            elif t in ("meme_card",):
                add(t, 2.2, visual="花字 · %s" % short, title_=seg.get("name"), segment_id=str(seg.get("id")))
            elif t in ("b_roll",):
                add(t, 2.8, visual="空镜 · %s" % seg.get("name"), segment_id=str(seg.get("id")))
            else:
                add(t, 3.0, speaker=host, line=HOST_LINES[host_i % len(HOST_LINES)], segment_id=str(seg.get("id")))
                host_i += 1
    # 收束镜头 + 片尾
    add("host_to_camera", 3.4, speaker=host, line="好，今天就到这儿，下期我们换个角度继续拆。", segment_id="closing")
    add("end_card", 2.6, visual="片尾卡", title_="下期见", subtitle="造片局 · AI微综艺", segment_id="closing")

    duration = round(sum(s["duration"] for s in shots), 2)
    episode = {
        "id": "demo_" + fid, "project_id": "demo", "no": 1, "title": title,
        "target_duration": duration, "status": "draft", "progress": 0.0,
        "shots_total": len(shots), "shots_done": 0, "cost": 0.0,
        "aspect": "16:9", "resolution": "1280x720", "fps": 25,
        "packaging": {**(fmt.get("packaging") or {}), "transition": "xfade"},
    }
    return {"episode": episode, "assets": assets, "shots": shots,
            "segments": fmt.get("segments") or [], "total_duration": duration,
            "format_id": fid, "template_name": fmt.get("name")}


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    only = sys.argv[1] if len(sys.argv) > 1 else None
    results = []
    files = sorted(FORMATS.glob("*.json"))
    for path in files:
        fmt = json.loads(path.read_text(encoding="utf-8"))
        fid = fmt.get("id") or path.stem
        if only and fid != only:
            continue
        demo_dir = OUT / fid
        demo_dir.mkdir(parents=True, exist_ok=True)
        data = build_episode(fmt)
        ep_json = demo_dir / "episode.json"
        ep_json.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        out_mp4 = demo_dir / (fid + ".mp4")
        cmd = [sys.executable, "-m", "pipeline.cli", "render", "--episode", str(ep_json),
               "--workdir", str(demo_dir / "work"), "--out", str(out_mp4),
               "--transitions", "xfade", "--transition-style", "fade", "--transition-duration", "0.25"]
        print("[demo] render", fid, flush=True)
        proc = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True, encoding="utf-8", errors="replace")
        ok = proc.returncode == 0 and out_mp4.exists()
        tail = (proc.stdout or proc.stderr or "")[-400:]
        print("[demo]", fid, "ok" if ok else "FAIL", tail.replace("\n", " ")[:300], flush=True)
        if ok:
            meta = {"format_id": fid, "name": fmt.get("name"), "duration": data["total_duration"],
                    "shots": len(data["shots"]), "video": str(out_mp4),
                    "shot_plan": data["shots"]}
            (demo_dir / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
            results.append({"id": fid, "name": fmt.get("name"), "ok": True, "video": str(out_mp4), "duration": data["total_duration"]})
        else:
            results.append({"id": fid, "name": fmt.get("name"), "ok": False, "error": tail})
    (OUT / "results.json").write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"ok": all(r.get("ok") for r in results), "count": len(results)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
