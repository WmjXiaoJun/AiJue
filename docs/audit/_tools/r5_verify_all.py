# -*- coding: utf-8 -*-
"""R5：三观感一键验收（抽帧质量 + 字幕/包装 + 降级统计 + 对比图）。

    python docs/audit/_tools/r5_verify_all.py --tid tonight_show \
        --looks 3d_anim,2d_anim,realistic --target 300

产出：
- `tmp/r5_frames/<tid>_<look>_qc.json`（每 look）
- `tmp/r5_frames/compare_<tid>.png`（同时间点多观感对比图）
- stdout：一张 markdown 表（可直接贴 R5_THREE_LOOKS.md）
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "docs" / "audit" / "_tools"))

import r5_quality_check as qc  # noqa: E402

OUT3D = REPO / "outputs" / "templates_3d"
FRAMES = REPO / "tmp" / "r5_frames"


def _hq(look_path: Path) -> dict:
    if not look_path.is_file():
        return {}
    try:
        data = json.loads(look_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    fallbacks = data.get("fallbacks") or []
    return {"shots": data.get("shots"), "meta_ok": data.get("meta_ok"),
            "unique_shot_ids": data.get("unique_shot_ids"),
            "duplicate_ids": data.get("duplicate_ids") or [],
            "fallbacks": len(fallbacks),
            "fallback_shots": [f.get("shot") for f in fallbacks][:12],
            "missing_prompt_id": data.get("missing_prompt_id") or []}


def _compare(tid: str, looks: list[str], at: float) -> str:
    try:
        from PIL import Image, ImageDraw  # noqa: PLC0415
    except Exception:  # noqa: BLE001
        return ""
    tiles = []
    for look in looks:
        video = OUT3D / tid / f"final_{int(at)}s_{look}.mp4"
        if not video.is_file():
            continue
        src = FRAMES / f"{tid}_{look}_cmp.png"
        if not qc.grab(video, at, src):
            continue
        img = Image.open(src).convert("RGB").resize((640, 360))
        draw = ImageDraw.Draw(img)
        draw.rectangle([0, 0, 200, 28], fill=(0, 0, 0))
        draw.text((8, 6), f"{tid} / {look} / t={at:.0f}s", fill=(255, 255, 255))
        tiles.append(img)
    if not tiles:
        return ""
    canvas = Image.new("RGB", (640 * len(tiles), 360), (16, 18, 24))
    for i, tile in enumerate(tiles):
        canvas.paste(tile, (640 * i, 0))
    out = FRAMES / f"compare_{tid}.png"
    canvas.save(out)
    return str(out)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="三观感一键验收")
    ap.add_argument("--tid", default="tonight_show")
    ap.add_argument("--looks", default="3d_anim,2d_anim,realistic")
    ap.add_argument("--target", type=float, default=300.0)
    ap.add_argument("--step", type=float, default=10.0)
    ap.add_argument("--at", type=float, default=30.0, help="对比图取样时间点")
    ap.add_argument("--json", default="", help="把汇总写成 JSON")
    args = ap.parse_args(argv)
    looks = [x.strip() for x in args.looks.split(",") if x.strip()]
    FRAMES.mkdir(parents=True, exist_ok=True)
    rows = []
    for look in looks:
        video = OUT3D / args.tid / f"final_{int(args.target)}s_{look}.mp4"
        if not video.is_file():
            rows.append({"look": look, "ok": False, "error": "成片不存在"})
            continue
        res = qc.qc_video(video, args.tid, look, step=args.step)
        ass_snap = OUT3D / args.tid / "work" / f"final_{look}.ass"
        ass = qc.ass_stats(ass_snap if ass_snap.is_file() else
                           (REPO / "outputs" / "fillers" / args.tid / "final.ass"))
        ep = qc.episode_stats(OUT3D / args.tid / "work" / f"episode_{look}.json")
        hq = _hq(OUT3D / args.tid / "work" / f"hq_run_{look}.json")
        row = {"look": look, "video": str(video), "duration": res["duration"],
               "width": res["width"], "height": res["height"], "fps": res["fps"],
               "has_audio": res["has_audio"], "frames": res["frames"],
               "no_face_frames": res["no_face_frames"],
               "no_face_ratio": res["no_face_ratio"],
               "no_face_fail": res["no_face_fail"],
               "multi_face_frames": res["multi_face_frames"],
               "dup_suspect_frames": res["dup_suspect_frames"],
               "ass": ass, "episode": ep, "hq": hq,
               "qc_json": str(FRAMES / f"{args.tid}_{look}_qc.json")}
        rows.append(row)
        (FRAMES / f"{args.tid}_{look}_qc.json").write_text(
            json.dumps({**res, "ass": ass, "episode": ep, "hq": hq},
                       ensure_ascii=False, indent=2), encoding="utf-8")
    compare = _compare(args.tid, looks, args.at)

    print("| look | 时长 | 分辨率/fps | 音轨 | 无脸帧/抽帧 | 无脸占比 | 多人脸帧 | 复制疑似 | Main字幕 | Punch | Name | Tag | Title3D | 降级镜 |")
    print("|---|---|---|---|---|---|---|---|---|---|---|---|---|---|")
    for row in rows:
        if row.get("error"):
            print(f"| {row['look']} | — | — | — | — | — | — | — | — | — | — | — | — | {row['error']} |")
            continue
        ass = row["ass"].get("by_style") or {}
        print(f"| {row['look']} | {row['duration']}s | {row['width']}×{row['height']}/{row['fps']} "
              f"| {'有' if row['has_audio'] else '无'} "
              f"| {row['no_face_frames']}/{row['frames']} | {row['no_face_ratio']:.1%} "
              f"| {row['multi_face_frames']} | {row['dup_suspect_frames']} "
              f"| {ass.get('Main', 0)} | {ass.get('Punch', 0)} | {ass.get('Name', 0)} "
              f"| {ass.get('Tag', 0)} | {ass.get('Title3D', 0)} "
              f"| {row['hq'].get('fallbacks', '-')} |")
    print()
    for row in rows:
        if row.get("error"):
            continue
        print(f"- {row['look']}: 对话镜 {row['episode'].get('dialogue_shots')} / "
              f"镜头 {row['episode'].get('shots')} / 渲染 {row['episode'].get('rendered')} / "
              f"episode 来源 {row['episode'].get('source')} / "
              f"ASS 事件 {row['ass'].get('dialogue_events')}")
        if row["hq"]:
            print(f"  hq_run: 镜 {row['hq'].get('shots')} meta_ok {row['hq'].get('meta_ok')} "
                  f"降级 {row['hq'].get('fallbacks')} 镜 {row['hq'].get('fallback_shots')} "
                  f"重复 id {row['hq'].get('duplicate_ids')}")
    print(f"\n对比图: {compare}")
    if args.json:
        Path(args.json).write_text(json.dumps({"rows": rows, "compare": compare},
                                              ensure_ascii=False, indent=2),
                                   encoding="utf-8")
    failed = [r["look"] for r in rows if r.get("no_face_fail")]
    print(f"无脸超阈值(>20%)的观感: {failed}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
