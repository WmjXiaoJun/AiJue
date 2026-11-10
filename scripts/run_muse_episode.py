# -*- coding: utf-8 -*-
"""V78：用 Muse Video（APIMart 网关）生成一整期 5 分钟，并合成。

- 30 条 × 10s = 300s（Muse 只支持 10s/条）
- 每条都带资产库角色描述（锁人物）+ 场景/时间/运镜，避免“差不多”
- 产物：outputs/templates_3d/<tid>/shots_muse/<sid>.mp4
- 合成：字幕/3D 章节字/后期特效/精确 300s，输出 final_300s_muse.mp4
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor

REPO = pathlib.Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from pipeline import config, muse_video, unique_fill, utils, v5_catalog, v5_story  # noqa: E402

OUT3D = config.OUTPUTS_ROOT / "templates_3d"
LOOK_PROMPT = ("cinematic photoreal film still, live-action look, natural skin texture, "
               "35mm lens, shallow depth of field, practical neon lighting, film grain")
TIMES = ["late at night", "at dawn", "in the rain", "in drifting fog",
         "under warm practical lights", "at golden hour"]
PLACES = ["a narrow side street", "a wide hall", "a cluttered workshop",
          "a rooftop terrace", "a long table", "a doorway"]


def _lock() -> threading.Lock:
    return threading.Lock()


def build(tid: str, target: float = 300.0, workers: int = 3, force: bool = False) -> dict:
    catalog = v5_catalog.load_catalog()
    template = (catalog.get("templates") or {}).get(tid)
    if not template:
        return {"ok": False, "error": f"模板不存在: {tid}"}
    out_dir = OUT3D / tid
    shots_dir = utils.ensure_dir(out_dir / "shots_muse")
    clip_n = max(1, int(round(target / 10.0)))
    plan = v5_story.build_plan(template, target)
    base_shots = [s for s in plan.get("shots") or [] if s.get("line")] or plan["shots"]
    cfg = muse_video.apimart_config()
    if not cfg["key"]:
        return {"ok": False, "error": "未配置 APIMART_API_KEY"}

    def prompt_for(i: int, shot: dict) -> str:
        when = TIMES[i % len(TIMES)]
        where = PLACES[(i // 2) % len(PLACES)]
        line = str(shot.get("line") or "").strip()
        scene = str(shot.get("scene") or "")
        return (f"{LOOK_PROMPT}. Scene: {where}, {when}; {scene}. "
                f"The same Chinese host character, consistent face and outfit. "
                f"{('He is speaking: ' + line) if line else 'He looks off camera, thinking.'} "
                "Camera moves slowly, subtle handheld.")

    results = []
    lock = _lock()

    def one(i: int) -> dict:
        sid = f"muse_{i + 1:03d}"
        out = shots_dir / f"{sid}.mp4"
        if out.is_file() and out.stat().st_size > 200_000 and not force:
            return {"id": sid, "cached": True}
        shot = base_shots[i % len(base_shots)]
        spec = {"prompt": prompt_for(i, shot), "duration": 10}
        t0 = time.time()
        res = None
        last_err = None
        for attempt in range(3):
            try:
                res = muse_video.generate_video({**spec, "provider": "apimart_muse"}, prog=None)
                break
            except Exception as exc:  # noqa: BLE001
                last_err = exc
                time.sleep(4 + attempt * 6)
        try:
            if res is None:
                raise RuntimeError(str(last_err))
            src = pathlib.Path(res["out"])
            if src.is_file():
                out.write_bytes(src.read_bytes())
            with lock:
                results.append({"id": sid, "ok": True, "cost": res.get("cost_usd"),
                                "elapsed": round(time.time() - t0, 1)})
            print(f"[muse {i+1}/{clip_n}] {sid} ok {round(time.time()-t0)}s", flush=True)
            return {"id": sid, "ok": True}
        except Exception as exc:  # noqa: BLE001
            with lock:
                results.append({"id": sid, "ok": False, "error": str(exc)[:200]})
            print(f"[muse {i+1}/{clip_n}] {sid} 失败：{exc}", flush=True)
            return {"id": sid, "ok": False}

    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        list(pool.map(one, range(clip_n)))

    clips = [shots_dir / f"muse_{i+1:03d}.mp4" for i in range(clip_n)]
    clips = [c for c in clips if c.is_file()]
    # 写 episode：让合成知道 30 条镜头与对应文案
    ep = {"episode": {"id": tid, "label": str(template.get("label") or tid),
                      "style": str(template.get("style") or ""),
                      "resolution": "1280x720", "fps": 25,
                      "target_duration": target, "total_duration": len(clips) * 10.0},
          "shots": [], "meta": {"template": tid, "seed": 20261106}}
    for i, clip in enumerate(clips):
        shot = base_shots[i % len(base_shots)]
        ep["shots"].append({"id": clip.stem, "no": i + 1, "type": "dialogue",
                            "line": str(shot.get("line") or ""),
                            "speaker_name": str(shot.get("speaker_name") or ""),
                            "scene": str(shot.get("scene") or ""),
                            "duration": 10.0, "characters": [],
                            "camera": {"type": "push_in", "strength": 0.1}})
    utils.write_json(out_dir / "work" / "episode.json", ep)
    res = unique_fill.compose(tid, template, target, force=True,
                              shots_subdir="shots_muse", out_suffix="_muse",
                              allow_fillers=False)
    return {"ok": bool(res.get("ok")), "clips": len(clips),
            "cost_usd": round(sum(float(r.get("cost") or 0) for r in results), 3),
            "compose": {k: res.get(k) for k in ("duration", "unique_shots")}}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--template", required=True)
    ap.add_argument("--target", type=float, default=300.0)
    ap.add_argument("--workers", type=int, default=3)
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args(argv)
    print(json.dumps(build(args.template, args.target, args.workers, args.force),
                     ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
