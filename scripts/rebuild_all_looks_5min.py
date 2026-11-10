# -*- coding: utf-8 -*-
"""V61：三种观感（3D 动画 / 2D 动画 / 真实）× 5 分钟，全模板批产。

每个观感独立镜头目录：outputs/templates_3d/<tid>/shots_<look>/
成品：outputs/templates_3d/<tid>/final_<target>s_<look>.mp4

V69 修（AUDIT_extras.md P0-1 / REVIEW_ROUND3 P0-4 的成因）：
- **永不删生产目录**：旧实现 `shutil.rmtree(outputs/templates_3d/<tid>/shots)` 会把
  全栈共用的规范镜头目录直接删掉（失败后不可恢复，跑完 2d_anim 还会让 `shots/` 里
  躺着 2D 镜头被后续 3D 复用）。现在改成：
    1) 渲染前把现有 `shots/` **改名**到 `shots_bak_<stamp>/`（保留，不删）；
    2) HQ 渲染照常写 `shots/`（CLI 只认这个名字）；
    3) 成功后 `shots/` → `shots_<look>/`（旧 `shots_<look>` 改名到 `..._bak_<stamp>`），
       再把 `shots_bak_<stamp>/` 换回 `shots/` —— 生产目录回到跑之前的样子；
    4) 失败自动回滚：半成品留 `shots_failed_<stamp>/`，`shots/` 复原。
- `--dry-run` 只打印计划；`--force` 由参数透传（不再写死 `--force`，避免全量重渲）。
- episode 按 look 分文件：`work/episode_<look>.json`（并维护 `work/looks.json` 索引）。
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from pipeline import config, unique_fill, utils, v5_catalog, v5_story  # noqa: E402

TARGET = 300.0  # 可被 --target 覆盖
NO_FILLERS = False
OUT3D = config.OUTPUTS_ROOT / "templates_3d"
REPORT = config.OUTPUTS_ROOT / "longform" / "report_looks_5min.json"
LOOKS = ("3d_anim", "2d_anim", "realistic")
STYLE_2D = {"default": "arcane_cinematic"}


def _final(tid: str, look: str) -> Path:
    return OUT3D / tid / f"final_{int(TARGET)}s_{look}.mp4"


def _shots_dir(tid: str, look: str) -> Path:
    return OUT3D / tid / f"shots_{look}"


def _live_dir(tid: str) -> Path:
    return OUT3D / tid / "shots"


def _stamp() -> str:
    return time.strftime("%Y%m%d_%H%M%S")


def _inputs_newer_than(final: Path, tid: str) -> bool:
    """成片之后输入被改过吗（brief / 模板 / V5 episode）？改过就不算"已达标"。

    否则换了台本、重建 brief 之后，`final_<target>s_<look>.mp4` 还是旧内容，
    脚本会直接跳过（R5 冒烟就是这样被跳过一次）。
    """
    try:
        film_mtime = final.stat().st_mtime
    except OSError:
        return False
    watch = [
        REPO / "contract" / "v5" / "cinema3d" / "templates3d" / f"{tid}_semantic.json",
        REPO / "contract" / "v5" / "templates" / f"{tid}.json",
        REPO / "outputs" / "templates_v5" / tid / "work" / "episode.json",
    ]
    for path in watch:
        try:
            if path.is_file() and path.stat().st_mtime > film_mtime:
                return True
        except OSError:
            continue
    return False


def _write_episode(tid: str, template: dict, shots_dir: Path, look: str) -> dict:
    """写 `work/episode_<look>.json` + `work/episode.json`（**id 空间 = 语义 brief**）。

    为什么以 brief 为准：`unique_fill.compose` 会用 `episode.json` 的 shot id 过滤源镜头
    （`allowed`），而 HQ 栈渲出来的镜头 id 来自 brief。旧实现用 `v5_story.build_plan`
    （V5 时间线 id，`fill_000…` 那套）→ 与 brief 交集只剩十来个 → 成片把真镜头全丢、
    只剩空镜（P0-2/P1-3 的 id 分裂）。现在 episode 的 id 集合 == 实际渲染的镜头集合；
    没有 brief 的模板才回退到 v5_story 计划。
    """
    brief = _brief_shots(tid)
    plan: dict
    if brief:
        shots: list[dict] = []
        for item in brief:
            sid = str(item.get("id"))
            clip = shots_dir / f"{sid}.mp4"
            rendered = clip.is_file()
            try:
                dur = float(utils.probe_duration(clip) or 0) if rendered else 0.0
            except Exception:  # noqa: BLE001
                dur = 0.0
            shots.append({
                "id": sid,
                "type": "card" if str(item.get("kind")) == "card" else "dialogue",
                "kind": str(item.get("kind") or ""),
                "scene": str(item.get("scene") or ""),
                "line": str(item.get("line") or ""),
                # 人名条/花字要用：brief 里已按台本还原说话人（没有就空着，包装会自动跳过）
                "speaker_name": str(item.get("speaker_name") or ""),
                "character": str(item.get("character") or ""),
                "roles": [str(r) for r in (item.get("roles") or []) if r],
                "framing": str(item.get("framing") or ""),
                "duration": round(dur or float(item.get("duration") or 5.0), 3),
                "rendered": rendered,
                "clip": str(clip) if rendered else "",
            })
        plan = {
            "version": 5, "episode": {
                "id": tid, "label": template.get("label"),
                "style": template.get("style"),
                "total_duration": round(sum(s["duration"] for s in shots), 3),
            },
            "meta": {"template": tid, "template_contract": template, "look": look,
                     "shots_subdir": f"shots_{look}", "source": "semantic_brief",
                     "timeline_source": "semantic_brief",
                     "target_duration": TARGET,
                     "brief_shots": len(shots),
                     "rendered": sum(1 for s in shots if s["rendered"])},
            "shots": shots,
        }
    else:
        plan = v5_story.build_plan(template, TARGET)
        plan["meta"]["template"] = tid
        plan["meta"]["template_contract"] = template
        plan["meta"]["look"] = look
        plan["meta"]["shots_subdir"] = f"shots_{look}"
        plan["meta"]["source"] = "v5_story"
        for shot in plan.get("shots") or []:
            clip = shots_dir / f"{shot['id']}.mp4"
            if clip.is_file():
                dur = float(utils.probe_duration(clip) or 0)
                if dur > 0.2:
                    shot["duration"] = round(dur, 3)
    work = utils.ensure_dir(OUT3D / tid / "work")
    utils.write_json(work / f"episode_{look}.json", plan)
    # 兼容旧读者：work/episode.json 仍是"最近一次跑的 look"，但显式标注 active_look
    utils.write_json(work / "episode.json", plan)
    index_path = work / "looks.json"
    index = utils.read_json(index_path) or {}
    if not isinstance(index, dict):
        index = {}
    index[look] = {"shots_subdir": f"shots_{look}",
                   "shots_dir": str(shots_dir),
                   "episode": f"episode_{look}.json",
                   "final": str(_final(tid, look)),
                   "episode_source": (plan.get("meta") or {}).get("source"),
                   "shots": len(plan.get("shots") or []),
                   "updated": time.strftime("%Y-%m-%d %H:%M:%S")}
    index["active_look"] = look
    utils.write_json(index_path, index)
    return plan


def _render(tid: str, look: str, log: Path, *, force: bool = False,
            limit: int = 0) -> int:
    cmd = [sys.executable, "-m", "pipeline.templates3d_hq", "--template", tid]
    if look == "2d_anim":
        cmd += ["--style-mode", "2d", "--2d-style", STYLE_2D["default"], "--look", "2d_anim"]
    else:
        cmd += ["--style-mode", "3d", "--look", look, "--identity", "keyframe"]
    if limit:
        cmd += ["--limit", str(int(limit))]
    if force:
        cmd.append("--force")
    with log.open("a", encoding="utf-8") as fh:
        fh.write("\n$ " + " ".join(cmd) + "\n")
        fh.flush()
        return subprocess.call(cmd, cwd=str(REPO), stdout=fh, stderr=subprocess.STDOUT)


def _rename(src: Path, dst: Path) -> None:
    """目录改名（同盘原子）；dst 已存在时加序号，绝不覆盖。"""
    if not src.exists():
        return
    candidate = dst
    n = 1
    while candidate.exists():
        n += 1
        candidate = dst.with_name(f"{dst.name}_{n}")
    os.replace(src, candidate)


def _brief_shots(tid: str) -> list[dict]:
    """语义 brief 里"会被 HQ 渲"的镜头（有 prompt 的那些）。"""
    path = REPO / "contract" / "v5" / "cinema3d" / "templates3d" / f"{tid}_semantic.json"
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return [s for s in (data.get("shots") or [])
            if isinstance(s, dict) and s.get("prompt")]


def _look_qc(tid: str, look: str, final: Path, res: dict) -> dict:
    """每 look 的 QC 证据（probe / 空镜占比 / 黑场 / 字幕事件），写 work/qc_<look>.json。

    HQ 5 分钟线不走 render_v5 的 duration_gate，也不写 qc_report.json；
    这里补齐同一套可核对的数字，避免「只有成片没有证据」。
    """
    from pipeline import qc_v5  # noqa: PLC0415

    info = qc_v5.probe_video(final) if final.is_file() else {}
    black, detail = (qc_v5.blackdetect_count(final, timeout=1200)
                     if final.is_file() else (0, ""))
    ass = OUT3D / tid / "work" / f"final_{look}.ass"
    styles: dict[str, int] = {}
    events = 0
    if ass.is_file():
        text = ass.read_text(encoding="utf-8", errors="replace")
        for line in text.splitlines():
            if not line.startswith("Dialogue:"):
                continue
            events += 1
            parts = line.split(",")
            key = parts[3] if len(parts) > 3 else "?"
            styles[key] = styles.get(key, 0) + 1
    source = int(res.get("source_clips") or 0)
    fillers = int(res.get("fillers") or 0)
    qc = {
        "template": tid, "look": look, "target": TARGET,
        "final": str(final),
        "duration": info.get("duration"), "width": info.get("width"),
        "height": info.get("height"), "fps": info.get("fps"),
        "has_audio": info.get("has_audio"),
        "delta_vs_target": round(float(info.get("duration") or 0) - TARGET, 3),
        "source_clips": source, "fillers": fillers,
        "filler_ratio": round(fillers / max(1, source + fillers), 3),
        "black_segments": black,
        "black_detail_tail": detail[-200:],
        "ass_events": events, "ass_by_style": styles,
        "ass_file": str(ass) if ass.is_file() else "",
        "ok": bool(info.get("has_audio")) and abs(
            float(info.get("duration") or 0) - TARGET) <= 1.0 and black == 0,
        "qc_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    utils.write_json(utils.ensure_dir(OUT3D / tid / "work") / f"qc_{look}.json", qc)
    return qc


def build_one(tid: str, template: dict, look: str, *, force: bool = False,
              dry_run: bool = False, render_limit: int = 0) -> dict:
    started = time.time()
    log = utils.ensure_dir(config.OUTPUTS_ROOT / "longform" / "logs_looks") / f"{tid}_{look}.log"
    live = _live_dir(tid)
    target = _shots_dir(tid, look)
    stamp = _stamp()
    plan = {"live": str(live), "target": str(target),
            "live_backup": str(OUT3D / tid / f"shots_bak_{stamp}"),
            "old_target_backup": str(OUT3D / tid / f"shots_{look}_bak_{stamp}"),
            "failed_dir": str(OUT3D / tid / f"shots_failed_{stamp}")}
    if dry_run:
        brief = _brief_shots(tid)
        return {"template": tid, "look": look, "ok": True, "dry_run": True,
                "stage": "dry-run", "plan": plan,
                "target_sec": TARGET, "force": force,
                "brief_shots": len(brief),
                "will_render": min(render_limit, len(brief)) if render_limit else len(brief),
                "final": str(_final(tid, look)),
                "elapsed_sec": round(time.time() - started, 1)}
    live_was_there = live.is_dir()
    # 1) 生产目录改名让位（保留，不删）+ 让 HQ 往干净的 shots/ 里渲
    _rename(live, Path(plan["live_backup"]))
    exit_code = None
    try:
        exit_code = _render(tid, look, log, force=force, limit=render_limit)
        shots = sorted(live.glob("*.mp4")) if live.is_dir() else []
        if exit_code != 0:
            raise RuntimeError(f"templates3d_hq 退出码 {exit_code}（日志: {log}）")
        if not shots:
            raise RuntimeError(f"渲染未产出任何镜头（exit={exit_code}，日志: {log}）")
    except BaseException:
        # 2) 失败：半成品留证据 + 生产目录回滚
        _rename(live, Path(plan["failed_dir"]))
        if live_was_there:
            _rename(Path(plan["live_backup"]), live)
        raise
    # 3) 成功：原子切换 —— 旧 shots_<look> 让位，本次产物落到 shots_<look>
    if target.is_dir():
        _rename(target, Path(plan["old_target_backup"]))
    os.replace(live, target)
    # 4) 生产目录复原（本次运行不改变 shots/ 的"当前内容"）
    restored = False
    if live_was_there:
        _rename(Path(plan["live_backup"]), live)
        restored = True
    _write_episode(tid, template, target, look)
    # 每 look 的证据快照：hq_run.json（fallbacks/降级）与 compose 用的 final.ass
    # （ass 在 outputs/fillers/<tid>/ 里是共享的，后跑的 look 会覆盖 → 必须拷一份）
    work = utils.ensure_dir(OUT3D / tid / "work")
    snapshots = {}
    try:
        hq = OUT3D / tid / "work" / "hq_run.json"
        if hq.is_file():
            snap = work / f"hq_run_{look}.json"
            snap.write_bytes(hq.read_bytes())
            snapshots["hq_run"] = str(snap)
    except OSError as exc:
        print(f"[warn] 快照 hq_run 失败: {exc}", flush=True)
    res = unique_fill.compose(tid, template, TARGET, force=True,
                              shots_subdir=f"shots_{look}", out_suffix=f"_{look}",
                              allow_fillers=not NO_FILLERS)
    try:
        shared_ass = config.OUTPUTS_ROOT / "fillers" / tid / "final.ass"
        if shared_ass.is_file():
            snap = work / f"final_{look}.ass"
            snap.write_bytes(shared_ass.read_bytes())
            snapshots["ass"] = str(snap)
    except OSError as exc:
        print(f"[warn] 快照 final.ass 失败: {exc}", flush=True)
    try:
        qc = _look_qc(tid, look, _final(tid, look), res)
        snapshots["qc"] = str(OUT3D / tid / "work" / f"qc_{look}.json")
        print(f"[qc] {look}: dur={qc['duration']} black={qc['black_segments']} "
              f"fillers={qc['fillers']}/{qc['fillers'] + qc['source_clips']} "
              f"ass={qc['ass_events']}", flush=True)
    except Exception as exc:  # noqa: BLE001 - QC 证据失败不阻塞出片
        print(f"[warn] look QC 失败: {exc}", flush=True)
    return {"template": tid, "look": look, **res,
            "shots": len(shots), "shots_dir": str(target), "exit": exit_code,
            "live_restored": restored, "backup": str(target),
            "artifacts": snapshots,
            "elapsed_sec": round(time.time() - started, 1)}


def main(argv=None) -> int:
    global TARGET, NO_FILLERS
    ap = argparse.ArgumentParser(description="三种观感 5 分钟批产")
    ap.add_argument("--looks", default=",".join(LOOKS))
    ap.add_argument("--only", default="")
    ap.add_argument("--force", action="store_true",
                    help="透传给 templates3d_hq（忽略镜头缓存重渲）")
    ap.add_argument("--no-fillers", action="store_true",
                    help="禁止空镜：镜头不够就用定格补齐（配合 expand_3d_shots 使用）")
    ap.add_argument("--target", type=float, default=TARGET,
                    help="目标时长（秒）；120 时几乎不产生空镜")
    ap.add_argument("--dry-run", action="store_true",
                    help="只打印将做哪些改名/渲染，不写盘")
    ap.add_argument("--render-limit", type=int, default=0,
                    help="只渲前 N 镜（冒烟用；0=全部。成片仍按 target 用空镜补齐）")
    args = ap.parse_args(argv)
    TARGET = float(args.target)
    NO_FILLERS = bool(args.no_fillers)
    render_limit = max(0, int(args.render_limit or 0))
    catalog = v5_catalog.load_catalog()
    templates = catalog.get("templates") or {}
    ids = ([t.strip() for t in args.only.split(",") if t.strip()] if args.only
           else sorted(templates))
    looks = [x.strip() for x in args.looks.split(",") if x.strip() in LOOKS]
    results = []
    if REPORT.is_file() and not args.dry_run:
        try:
            results = json.loads(REPORT.read_text(encoding="utf-8")).get("items") or []
        except (OSError, ValueError):
            results = []
    # 模板外层、观感内层：一个模板的三种观感先出齐，方便逐条验收
    failed: list[dict] = []
    for idx, tid in enumerate(ids, start=1):
        for look in looks:
            final = _final(tid, look)
            stale = final.is_file() and _inputs_newer_than(final, tid)
            if stale:
                print(f"[{idx}/{len(ids)}] {tid} {look} 成片早于 brief/模板变更 → 重跑",
                      flush=True)
            if (not args.force) and (not stale) and final.is_file() and \
                    float(utils.probe_duration(final) or 0) >= TARGET - 1.0:
                print(f"[{idx}/{len(ids)}] {tid} {look} 已达标，跳过", flush=True)
                continue
            print(f"[{idx}/{len(ids)}] {tid} {look} 开始"
                  f"{'（dry-run）' if args.dry_run else ''}", flush=True)
            try:
                res = build_one(tid, templates.get(tid) or {}, look,
                                force=bool(args.force), dry_run=bool(args.dry_run),
                                render_limit=render_limit)
            except Exception as exc:  # noqa: BLE001 - 单模板失败不拖垮整批
                res = {"template": tid, "look": look, "ok": False,
                       "error": f"{type(exc).__name__}: {exc}"[-500:]}
                failed.append({"template": tid, "look": look, "error": res["error"]})
            print(f"[{idx}/{len(ids)}] {tid} {look} -> ok={res.get('ok')} "
                  f"dur={res.get('duration')} {res.get('elapsed_sec')}s", flush=True)
            if args.dry_run:
                print(json.dumps(res, ensure_ascii=False, indent=1), flush=True)
                continue
            results = [r for r in results
                       if not (r.get("template") == tid and r.get("look") == look)] + [res]
            utils.write_json(REPORT, {"target": TARGET,
                                      "updated": time.strftime("%Y-%m-%d %H:%M:%S"),
                                      "items": results})
    ok = len([r for r in results if r.get("ok")])
    print(json.dumps({"ok": ok, "total": len(results), "failed": failed,
                      "dry_run": bool(args.dry_run)}, ensure_ascii=False))
    if args.dry_run:
        return 0
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
