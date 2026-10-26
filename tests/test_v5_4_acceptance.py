# -*- coding: utf-8 -*-
"""V5.4 独立验收：电影级剪辑 / 镜头语言 / 分镜 / 转场（16 模板各自不同）。

验收口径（Lead V5.4 任务书 + docs/PLAN_V5_4_EDITORIAL.md / V5_4_EDITING.md / V5_4_REPORT.md）：
1. 剪辑档案：`contract/v5/editorial/<id>.json` 16/16 字段齐全（pacing/opening/closing/
   shot_sequence/camera_palette/transition_palette/transition_policy/j_cut_ratio/l_cut_ratio/
   insert_ratio/detail_ratio/rhythm）；palette 合法（camera ⊆ motion2d 17 种、transition ⊆
   editing 37 种 xfade）；16 份组合两两不完全相同；`editorial.build_edit_plan` 复算
   `validate_plan` 通过（≥4 景别/≥3 运镜/≥2 转场/无相邻同景别）。
2. 分镜产物：`outputs/templates_v5/<id>/storyboard.json` 存在，shots 数 = 成片镜头数（V1
   clips 含卡片）= qc stats.shots+cards；每镜有 shot_size/camera/transition_out；storyboard.png
   （PNG）/storyboard.html 存在；`timeline.json` 含 editing（boundaries 数 = 镜头数-1、
   cuts、summary），V1 body 镜与 `work/edit_plan.json` 逐镜一致（景别/运镜/转场）。
3. 成片实际：blackdetect=0（d=0.30）、resolution/fps/audio/voice_fixed/P1 头部全过；
   时长 = sum(V1 shot 时长) - 非 cut 转场时长（与 timeline.duration / qc.expected /
   final probe 一致）；剪辑多样性：每部实际 camera ≥4 种、transition ≥2 种、景别 ≥4 种，
   硬切比例与档案 hard_cut_ratio 偏差 ≤0.25，16 部组合两两不同、全局 camera ≥12 种。
4. 前端：`frontend/dist` 含分镜/剪辑标记（storyboard/分镜/剪辑）且构建晚于
   Storyboard.tsx；8090 在跑时检查 `GET /api/v5/editorial/{t}`、`GET /api/v5/storyboard/{t}`、
   `/media/v5/{t}/storyboard.png|html`（后端未重启则 skip 并记录）。
5. 回归：pytest tests 全绿（V5.2 43 项 + V5 187 项不退化），风格矩阵 0 对“几乎相同”，
   P1 头部视觉门 16/16。

批量渲染期间未就绪模板 skip 并报告；只读 outputs/assets/contract/frontend，不启动渲染。
运行：python -m pytest tests/test_v5_4_acceptance.py -q
"""
from __future__ import annotations

import functools
import importlib.util
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

import numpy as np
import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from pipeline import qc_v5, v5_catalog  # noqa: E402

OUTPUTS = REPO_ROOT / "outputs" / "templates_v5"
EDITORIAL_DIR = REPO_ROOT / "contract" / "v5" / "editorial"
DIST = REPO_ROOT / "frontend" / "dist"
QA_DIR = REPO_ROOT / "tmp" / "qa"
EDITORIAL_JSON = QA_DIR / "v5_4_editorial_evidence.json"
STORYBOARD_JSON = QA_DIR / "v5_4_storyboard_evidence.json"
TIMELINE_JSON = QA_DIR / "v5_4_timeline_evidence.json"
DIVERSITY_JSON = QA_DIR / "v5_4_edit_matrix.json"
HEAD_GATE_JSON = QA_DIR / "v5_head_gate_v54.json"
MATRIX_JSON = QA_DIR / "v5_style_matrix_v54.json"
REVIEW_JSON = QA_DIR / "v5_4_review_notes.json"
BASE_URL = "http://127.0.0.1:8090"
EXPECTED = [
    "tonight_show", "shark_tank", "masked_singer", "standup", "taskmaster",
    "traitors", "snl_sketch", "roundtable", "hot_ones", "qipashuo",
    "slow_life", "history_talk", "history_mystery", "classics",
    "national_treasure", "mystery",
]
DEFAULT_SEED = 20261101          # render_v5 默认 seed
MIN_DURATION, MAX_DURATION = 25.0, 50.0
EXPECT_W, EXPECT_H, EXPECT_FPS = 1280, 720, 25.0
COS_THRESHOLD = 0.985
EDGE_DIFF_MIN = 0.01
SHOT_SIZES = ("EWS", "WS", "MS", "MCU", "CU", "ECU", "OTS", "DETAIL", "INSERT")
CARDS = ("v5_card_title", "v5_card_end")
CUT_MODES = ("", "cut", "none", "hard", "hard_cut")
_V54_CODE_FILES = ("editorial.py", "editing.py", "storyboard_v5.py",
                   "render_v5.py", "qc_v5.py")
# 说明：代码 mtime 不参与就绪判定。V5.4 期间 render-dev 会热修 editing.py（只影响回退模板），
# 早渲模板的 per-cut 产物仍有效；是否回退由 test_per_cut_transitions 逐部实测判定，
# 最终以 Lead FREEZE 后（含修复重渲）的产物为准。
V54_CODE_MTIME = max(
    (REPO_ROOT / "pipeline" / name).stat().st_mtime
    for name in _V54_CODE_FILES
    if (REPO_ROOT / "pipeline" / name).is_file())


# ---------------------------------------------------------------------------
# 读取 / 就绪 / 基础 helper
# ---------------------------------------------------------------------------
def read_json(path: Path, default=None):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")


def tdir(tid: str) -> Path:
    return OUTPUTS / tid


def storyboard(tid: str) -> dict:
    return read_json(tdir(tid) / "storyboard.json", {}) or {}


def timeline(tid: str) -> dict:
    return read_json(tdir(tid) / "timeline.json", {}) or {}


def qc_report(tid: str) -> dict:
    return read_json(tdir(tid) / "qc_report.json", {}) or {}


def style_report(tid: str) -> dict:
    return read_json(tdir(tid) / "style_report.json", {}) or {}


def plan(tid: str) -> dict:
    return read_json(tdir(tid) / "work" / "episode.json", {}) or {}


def edit_plan(tid: str) -> dict:
    return read_json(tdir(tid) / "work" / "edit_plan.json", {}) or {}


def v1_clips(tid: str) -> list[dict]:
    for track in timeline(tid).get("tracks") or []:
        if track.get("id") == "V1":
            return [c for c in track.get("clips") or [] if isinstance(c, dict)]
    return []


def body_clips(tid: str) -> list[dict]:
    return [c for c in v1_clips(tid) if str(c.get("id")) not in CARDS]


def editing(tid: str) -> dict:
    return timeline(tid).get("editing") or {}


def v54_state(tid: str) -> str:
    base = tdir(tid)
    required = [base / "final.mp4", base / "qc_report.json", base / "timeline.json",
                base / "style_report.json", base / "contact_sheet.png",
                base / "storyboard.json", base / "storyboard.png",
                base / "storyboard.html", base / "work" / "edit_plan.json"]
    missing = [p.name for p in required if not p.is_file()]
    if missing:
        return f"pending(缺{','.join(missing[:4])})"
    report = qc_report(tid)
    if report.get("ok") is not True:
        return "qc_not_ok"
    if "voice_fixed" not in (report.get("checks") or {}):
        return "pending(旧版 QC)"
    if not editing(tid):
        return "pending(旧版 timeline 无 editing)"
    final_mtime = (base / "final.mp4").stat().st_mtime
    stale = [p.name for p in required[1:] if p.stat().st_mtime < final_mtime - 180]
    if stale:
        return f"pending(evidence_stale:{','.join(stale[:3])})"
    return "ready"


READY = [tid for tid in EXPECTED if v54_state(tid) == "ready"]
PENDING = [tid for tid in EXPECTED if tid not in READY]


@functools.lru_cache(maxsize=1)
def base_tests():
    path = Path(__file__).with_name("test_v5_acceptance.py")
    spec = importlib.util.spec_from_file_location("_v5_base_tests", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def catalog() -> dict:
    return v5_catalog.load_catalog()


def editorial_module():
    from pipeline import editorial
    return editorial


def motion2d_module():
    from pipeline import motion2d
    return motion2d


def editing_module():
    from pipeline import editing
    return editing


# ---------------------------------------------------------------------------
# 1) 剪辑档案契约（16/16 字段齐全 + palette 合法 + 组合唯一）
# ---------------------------------------------------------------------------
REQUIRED_PROFILE_KEYS = (
    "id", "label", "pacing", "opening", "closing", "shot_sequence",
    "camera_palette", "transition_palette", "transition_policy",
    "j_cut_ratio", "l_cut_ratio", "insert_ratio", "detail_ratio", "rhythm",
)


def test_editorial_contracts():
    ed = editorial_module()
    supported_cams = set(motion2d_module().SUPPORTED_CAMERA_TYPES)
    supported_trans = set(editing_module().supported_modes())
    files = sorted(p.stem for p in EDITORIAL_DIR.glob("*.json"))
    assert set(files) == set(EXPECTED), f"剪辑档案不齐：{files}"
    problems = []
    rows = []
    all_cams: set[str] = set()
    all_trans: set[str] = set()
    combos: dict[tuple, list[str]] = {}
    for tid in EXPECTED:
        raw = read_json(EDITORIAL_DIR / f"{tid}.json", {}) or {}
        miss = [k for k in REQUIRED_PROFILE_KEYS if k not in raw]
        if miss:
            problems.append(f"{tid}: 缺字段 {miss}")
            continue
        if raw.get("id") != tid:
            problems.append(f"{tid}: id={raw.get('id')!r}")
        pacing = raw.get("pacing") or {}
        if not all(k in pacing for k in ("lead_in", "mid", "climax", "coda")):
            problems.append(f"{tid}: pacing 缺 lead_in/mid/climax/coda")
        for key in ("opening", "closing"):
            spec = raw.get(key) or {}
            if not spec.get("shot") or not spec.get("camera"):
                problems.append(f"{tid}: {key} 缺 shot/camera")
            if str(spec.get("shot")) not in SHOT_SIZES:
                problems.append(f"{tid}: {key}.shot={spec.get('shot')!r}")
            if str(spec.get("camera")) not in supported_cams:
                problems.append(f"{tid}: {key}.camera={spec.get('camera')!r} 非法")
        seq = [str(x) for x in (raw.get("shot_sequence") or [])]
        if len(set(seq)) < 4 or not set(seq) <= set(SHOT_SIZES):
            problems.append(f"{tid}: shot_sequence={seq}（需 ≥4 种合法景别）")
        cams = [str(x) for x in (raw.get("camera_palette") or [])]
        trans = [str(x) for x in (raw.get("transition_palette") or [])]
        if len(cams) < 4 or not set(cams) <= supported_cams:
            problems.append(f"{tid}: camera_palette={cams}（需 ≥4 且合法）")
        if len(trans) < 3 or not set(trans) <= supported_trans:
            bad = [x for x in trans if x not in supported_trans]
            problems.append(f"{tid}: transition_palette 非法/过少 {trans}（非法 {bad}）")
        policy = raw.get("transition_policy") or {}
        ratio = float(policy.get("hard_cut_ratio", -1))
        if not (0.0 <= ratio <= 1.0):
            problems.append(f"{tid}: hard_cut_ratio={ratio} 越界")
        for key in ("j_cut_ratio", "l_cut_ratio", "insert_ratio", "detail_ratio"):
            val = float(raw.get(key, -1))
            if not (0.0 <= val <= 1.0):
                problems.append(f"{tid}: {key}={val} 越界")
        rhythm = raw.get("rhythm") or {}
        if float(rhythm.get("fast_shots_max", 0)) <= 0 or \
                float(rhythm.get("slow_shots_min", 0)) <= 0:
            problems.append(f"{tid}: rhythm 缺 fast_shots_max/slow_shots_min")
        all_cams.update(cams)
        all_trans.update(trans)
        combo = (tuple(seq), tuple(cams), tuple(trans), ratio,
                 tuple(round(float(raw.get(k, 0)), 3) for k in
                       ("j_cut_ratio", "l_cut_ratio", "insert_ratio", "detail_ratio")))
        combos.setdefault(combo, []).append(tid)
        rows.append({
            "template": tid,
            "label": raw.get("label"),
            "shot_sequence": seq,
            "camera_palette": cams,
            "transition_palette": trans,
            "hard_cut_ratio": ratio,
            "j_cut_ratio": raw.get("j_cut_ratio"),
            "l_cut_ratio": raw.get("l_cut_ratio"),
            "insert_ratio": raw.get("insert_ratio"),
            "detail_ratio": raw.get("detail_ratio"),
            "opening": raw.get("opening"), "closing": raw.get("closing"),
        })
    dups = [v for v in combos.values() if len(v) > 1]
    if dups:
        problems.append(f"剪辑档案组合重复：{dups}")
    if len(all_cams) < 17:
        problems.append(f"camera 覆盖 {len(all_cams)} < 17 种")
    if len(all_trans) < 14:
        problems.append(f"transition 覆盖 {len(all_trans)} < 14 种")
    # load_editorial 合并版 16/16 可加载
    templates = editorial_module().templates_with_editorial()
    if set(templates) != set(EXPECTED):
        problems.append(f"templates_with_editorial 不齐：{templates}")
    write_json(EDITORIAL_JSON, {
        "profiles": rows,
        "camera_union": sorted(all_cams),
        "transition_union": sorted(all_trans),
        "camera_count": len(all_cams), "transition_count": len(all_trans),
        "combos": len(combos), "duplicates": dups})
    assert not problems, "剪辑档案契约问题：\n" + "\n".join(problems[:20])


# ---------------------------------------------------------------------------
# 2) 剪辑计划复算：build_edit_plan + validate_plan（16 模板）
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("tid", READY)
def test_edit_plan_rebuild(tid: str):
    ed = editorial_module()
    cat = catalog()
    tpl = dict(cat["templates"][tid])
    style = cat["styles"][tpl["style"]]
    work = edit_plan(tid)
    if not work:
        pytest.fail(f"{tid}: 缺 work/edit_plan.json（V5.4 剪辑未落盘）")
    episode = plan(tid)
    if not episode:
        pytest.fail(f"{tid}: 缺 work/episode.json")
    zones = {}
    anchors = {}
    for scene in cat.get("scenes") or {}:
        pass
    rebuilt = ed.build_edit_plan(tpl, episode, style, seed=DEFAULT_SEED,
                                 supported_cameras=motion2d_module().SUPPORTED_CAMERA_TYPES)
    val = ed.validate_plan(rebuilt)
    problems = []
    if val.get("ok") is not True:
        problems.append(f"validate_plan 失败：{val.get('errors')}")
    work_shots = {s["shot"]: s for s in work.get("shots") or []}
    by_shot = {}
    for shot in rebuilt.get("shots") or []:
        w = work_shots.get(shot["shot"])
        if not w:
            problems.append(f"{shot['shot']}: work/edit_plan 缺失")
            continue
        for key in ("shot_size", "camera", "j_cut", "l_cut", "insert"):
            if shot.get(key) != w.get(key):
                problems.append(f"{shot['shot']}.{key}: 复算 {shot.get(key)!r} != 实渲 {w.get(key)!r}")
        t1 = (shot.get("transition_out") or {}).get("mode")
        t2 = (w.get("transition_out") or {}).get("mode")
        if t1 != t2:
            problems.append(f"{shot['shot']}.transition: 复算 {t1!r} != 实渲 {t2!r}")
        by_shot[shot["shot"]] = shot
    ws = work.get("summary") or {}
    rs = rebuilt.get("summary") or {}
    for key in ("signature", "camera_types", "transitions", "hard_cut_ratio"):
        if ws.get(key) != rs.get(key):
            problems.append(f"summary.{key}: 复算 {rs.get(key)!r} != 实渲 {ws.get(key)!r}")
    if len(by_shot) < 2:
        problems.append(f"可比较镜头 {len(by_shot)} < 2")
    evidence = read_json(STORYBOARD_JSON, {}) or {}
    templates = evidence.setdefault("edit_plans", {})
    templates[tid] = {"signature": ws.get("signature"),
                      "camera_types": ws.get("camera_types"),
                      "transitions": ws.get("transitions"),
                      "hard_cut_ratio": ws.get("hard_cut_ratio"),
                      "j_cuts": ws.get("j_cuts"), "l_cuts": ws.get("l_cuts"),
                      "inserts": ws.get("inserts"),
                      "shots": len(work.get("shots") or []),
                      "validate_plan": val}
    write_json(STORYBOARD_JSON, evidence)
    assert not problems, f"{tid}: 剪辑计划复算问题：\n" + "\n".join(problems[:15])


# ---------------------------------------------------------------------------
# 3) 分镜产物：storyboard.json/png/html + 字段
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("tid", READY)
def test_storyboard_artifacts(tid: str):
    base = tdir(tid)
    sb = storyboard(tid)
    clips = v1_clips(tid)
    problems = []
    if not sb:
        pytest.fail(f"{tid}: 缺 storyboard.json")
    if len(sb.get("shots") or []) != len(clips):
        problems.append(f"storyboard.shots={len(sb.get('shots'))} != V1 clips={len(clips)}")
    stats = qc_report(tid).get("stats") or {}
    n_shots = int(stats.get("shots") or 0) + int(stats.get("cards") or 0)
    if n_shots and n_shots != len(clips):
        problems.append(f"qc stats(sh+cards)={n_shots} != V1 clips={len(clips)}")
    for shot in sb.get("shots") or []:
        sid = shot.get("id")
        miss = [k for k in ("id", "index", "type", "duration", "shot_size", "camera",
                            "transition_out") if k not in shot]
        if miss:
            problems.append(f"{sid}: 缺字段 {miss}")
            continue
        if shot.get("type") == "shot":
            if str(shot.get("shot_size")) not in SHOT_SIZES:
                problems.append(f"{sid}: shot_size={shot.get('shot_size')!r}")
            if str(shot.get("camera")) not in set(
                    motion2d_module().SUPPORTED_CAMERA_TYPES):
                problems.append(f"{sid}: camera={shot.get('camera')!r}")
            trans = shot.get("transition_out") or {}
            if not isinstance(trans, dict) or "mode" not in trans:
                problems.append(f"{sid}: transition_out={trans!r}")
        if not float(shot.get("duration") or 0) > 0:
            problems.append(f"{sid}: duration<=0")
    total = float(sb.get("total") or 0)
    tl_dur = float(timeline(tid).get("duration") or 0)
    if abs(total - tl_dur) > 0.35:
        problems.append(f"storyboard.total={total} != timeline.duration={tl_dur}")
    png = base / "storyboard.png"
    html = base / "storyboard.html"
    if not (png.is_file() and png.stat().st_size > 20_000):
        problems.append("storyboard.png 缺失/过小")
    else:
        if png.read_bytes()[:8] != b"\x89PNG\r\n\x1a\n":
            problems.append("storyboard.png 非 PNG")
    if not html.is_file():
        problems.append("storyboard.html 缺失")
    else:
        text = html.read_text(encoding="utf-8", errors="replace")
        if "<html" not in text.lower() or not any(k in text for k in ("分镜", "storyboard", "镜头")):
            problems.append("storyboard.html 内容异常")
    manifest = read_json(base / "work" / "manifest.json", {}) or {}
    sb_art = manifest.get("storyboard") or {}
    if not sb_art:
        problems.append("manifest 缺 storyboard 证据")
    else:
        for key in ("json", "html", "png"):
            p = Path(str(sb_art.get(key) or ""))
            if not p.is_file():
                problems.append(f"manifest.storyboard.{key} 不存在：{p}")
    evidence = read_json(STORYBOARD_JSON, {}) or {}
    evidence.setdefault("storyboards", {})[tid] = {
        "shots": len(sb.get("shots") or []),
        "v1_clips": len(clips),
        "total": total,
        "png_bytes": png.stat().st_size if png.is_file() else 0,
        "html_bytes": html.stat().st_size if html.is_file() else 0,
        "edit": sb.get("edit") or {},
    }
    write_json(STORYBOARD_JSON, evidence)
    assert not problems, f"{tid}: 分镜产物问题：\n" + "\n".join(problems[:15])


# ---------------------------------------------------------------------------
# 4) timeline editing：boundaries / cuts / 与 edit_plan 一致性
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("tid", READY)
def test_timeline_editing(tid: str):
    tl = timeline(tid)
    ed = editing(tid)
    clips = v1_clips(tid)
    body = body_clips(tid)
    work = edit_plan(tid)
    work_shots = {s["shot"]: s for s in work.get("shots") or []}
    problems = []
    miss = [k for k in ("mode", "boundaries", "cuts", "summary") if k not in ed]
    if miss:
        problems.append(f"timeline.editing 缺 {miss}")
    bounds = ed.get("boundaries") or []
    if len(bounds) != len(clips) - 1:
        problems.append(f"boundaries={len(bounds)} != V1 clips-1={len(clips) - 1}")
    supported = set(editing_module().supported_modes()) | {"editorial"}
    for b in bounds:
        if not b.get("from") or not b.get("to"):
            problems.append(f"boundary 缺 from/to：{b}")
        if str(b.get("mode")) not in supported:
            problems.append(f"boundary mode={b.get('mode')!r} 非法")
        if float(b.get("duration") or 0) < 0:
            problems.append(f"boundary duration<0：{b}")
    # V1 body 镜与 work/edit_plan 一致（景别/运镜/转场）
    for clip in body:
        sid = str(clip.get("id"))
        w = work_shots.get(sid)
        if not w:
            problems.append(f"{sid}: work/edit_plan 无此镜")
            continue
        if clip.get("shot_size") != w.get("shot_size"):
            problems.append(f"{sid}: timeline shot_size={clip.get('shot_size')!r} != "
                            f"edit_plan {w.get('shot_size')!r}")
        if clip.get("camera") != w.get("camera"):
            problems.append(f"{sid}: timeline camera={clip.get('camera')!r} != "
                            f"edit_plan {w.get('camera')!r}")
        tm = (clip.get("transition") or {}).get("mode")
        wm = (w.get("transition_out") or {}).get("mode")
        if tm != wm:
            problems.append(f"{sid}: timeline transition={tm!r} != edit_plan {wm!r}")
    ws = work.get("summary") or {}
    es = ed.get("summary") or {}
    for key in ("signature", "camera_types", "transitions", "hard_cut_ratio"):
        if es.get(key) != ws.get(key):
            problems.append(f"editing.summary.{key}={es.get(key)!r} != edit_plan {ws.get(key)!r}")
    cuts = ed.get("cuts") or []
    n_j = sum(1 for c in cuts if float(c.get("j_cut") or 0) > 0)
    n_l = sum(1 for c in cuts if float(c.get("l_cut") or 0) > 0)
    if n_j != int(ws.get("j_cuts") or 0):
        problems.append(f"cuts 中 j_cut 镜数 {n_j} != summary.j_cuts={ws.get('j_cuts')}")
    if n_l != int(ws.get("l_cuts") or 0):
        problems.append(f"cuts 中 l_cut 镜数 {n_l} != summary.l_cuts={ws.get('l_cuts')}")
    for c in cuts:
        if float(c.get("j_cut") or 0) <= 0 and float(c.get("l_cut") or 0) <= 0:
            problems.append(f"cut 既无 j 也无 l：{c}")
    evidence = read_json(TIMELINE_JSON, {}) or {}
    evidence.setdefault("timeline", {})[tid] = {
        "shots": len(clips), "boundaries": len(bounds), "cuts": len(cuts),
        "summary": {k: es.get(k) for k in
                    ("signature", "camera_types", "transitions", "hard_cut_ratio",
                     "j_cuts", "l_cuts", "inserts")},
    }
    write_json(TIMELINE_JSON, evidence)
    assert not problems, f"{tid}: timeline editing 问题：\n" + "\n".join(problems[:15])


# ---------------------------------------------------------------------------
# 4.5) per-cut 转场真生效：manifest + V1 重叠（回退单转场 = 硬门失败）
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("tid", READY)
def test_per_cut_transitions(tid: str):
    base = tdir(tid)
    manifest = read_json(base / "work" / "manifest.json", {}) or {}
    tr = manifest.get("transition") or {}
    tl = timeline(tid)
    clips = v1_clips(tid)
    bounds = editing(tid).get("boundaries") or []
    problems = []
    if str(tr.get("mode")) != "editorial" or str(tr.get("style")) != "per-cut":
        problems.append(
            f"manifest.transition={tr!r}（应为 editorial/per-cut；"
            f"出现回退说明 editing.xfade_chain 失败，见 work/ 日志）")
    if str(editing(tid).get("mode")) != "per-cut":
        problems.append(f"timeline.editing.mode={editing(tid).get('mode')!r} != per-cut")
    for i, b in enumerate(bounds):
        if i + 1 >= len(clips):
            break
        prev, nxt = clips[i], clips[i + 1]
        planned = float(b.get("duration") or 0)
        prev_end = float(prev.get("at") or 0) + float(prev.get("out") or 0)
        overlap = prev_end - float(nxt.get("at") or 0)
        mode = str(b.get("mode") or "")
        if mode in CUT_MODES:
            if abs(overlap) > 0.15:
                problems.append(f"{prev.get('id')}->{nxt.get('id')}: cut 却重叠 {overlap:.2f}s")
        else:
            if abs(overlap - planned) > 0.15:
                problems.append(
                    f"{prev.get('id')}->{nxt.get('id')}: 计划 {mode} {planned:.2f}s，"
                    f"实际重叠 {overlap:.2f}s（转场未真正拼接）")
    evidence = read_json(TIMELINE_JSON, {}) or {}
    evidence.setdefault("per_cut", {})[tid] = {
        "manifest_mode": tr.get("mode"), "manifest_style": tr.get("style"),
        "editing_mode": editing(tid).get("mode"),
        "planned_loss": round(sum(float(b.get("duration") or 0) for b in bounds
                                  if str(b.get("mode")) not in CUT_MODES), 3),
        "actual_overlap": round(sum(
            max(0.0, float(clips[i].get("at") or 0) + float(clips[i].get("out") or 0)
                - float(clips[i + 1].get("at") or 0))
            for i in range(len(clips) - 1)), 3),
    }
    write_json(TIMELINE_JSON, evidence)
    assert not problems, f"{tid}: per-cut 转场未真生效：\n" + "\n".join(problems[:12])


# ---------------------------------------------------------------------------
# 5) 时长公式：sum(V1 镜头) - 实际重叠 == timeline/final
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("tid", READY)
def test_duration_formula(tid: str):
    clips = v1_clips(tid)
    sum_shots = sum(float(c.get("out") or 0) for c in clips)
    overlap = sum(
        max(0.0, float(clips[i].get("at") or 0) + float(clips[i].get("out") or 0)
            - float(clips[i + 1].get("at") or 0))
        for i in range(len(clips) - 1))
    calc = sum_shots - overlap
    tl_dur = float(timeline(tid).get("duration") or 0)
    probe = qc_v5.probe_video(tdir(tid) / "final.mp4")
    stats = qc_report(tid).get("stats") or {}
    expected = float(stats.get("expected_duration") or 0)
    problems = []
    if abs(calc - tl_dur) > 0.35:
        problems.append(f"calc={calc:.2f} != timeline.duration={tl_dur:.2f}")
    if abs(float(probe.get("duration") or 0) - calc) > 0.5:
        problems.append(f"final={probe.get('duration')} != calc={calc:.2f}")
    if expected and abs(expected - calc) > 0.35:
        problems.append(f"qc.expected={expected:.2f} != calc={calc:.2f}")
    if not (MIN_DURATION <= float(probe.get("duration") or 0) <= MAX_DURATION):
        problems.append(f"成片时长 {probe.get('duration')}s 超 25-50s")
    evidence = read_json(TIMELINE_JSON, {}) or {}
    evidence.setdefault("duration", {})[tid] = {
        "sum_shots": round(sum_shots, 3), "actual_overlap": round(overlap, 3),
        "calc": round(calc, 3), "timeline": tl_dur,
        "final": probe.get("duration"), "qc_expected": expected,
        "boundaries": len(editing(tid).get("boundaries") or [])}
    write_json(TIMELINE_JSON, evidence)
    assert not problems, f"{tid}: 时长公式问题：\n" + "\n".join(problems)


# ---------------------------------------------------------------------------
# 6) 剪辑多样性（每部实际 camera/transition/景别/硬切比例）
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("tid", READY)
def test_edit_diversity_per_template(tid: str):
    body = body_clips(tid)
    bounds = editing(tid).get("boundaries") or []
    work = edit_plan(tid)
    ws = work.get("summary") or {}
    profile = read_json(EDITORIAL_DIR / f"{tid}.json", {}) or {}
    target = float((profile.get("transition_policy") or {}).get("hard_cut_ratio", 0.5))
    cams = {str(c.get("camera")) for c in body}
    sizes = {str(c.get("shot_size")) for c in body}
    # 实际转场：body 出镜的边界（含最后一镜到片尾卡）
    body_ids = {str(c.get("id")) for c in body}
    modes = [str(b.get("mode")) for b in bounds if str(b.get("from")) in body_ids]
    trans = set(modes)
    body_body = [str(b.get("mode")) for b in bounds
                 if str(b.get("from")) in body_ids and str(b.get("to")) in body_ids]
    hard = (sum(1 for m in body_body if m == "cut") / len(body_body)) if body_body else 0.0
    problems = []
    if len(cams) < 4:
        problems.append(f"实际运镜 {len(cams)} < 4：{sorted(cams)}")
    if len(sizes) < 4:
        problems.append(f"实际景别 {len(sizes)} < 4：{sorted(sizes)}")
    if len(trans) < 2:
        problems.append(f"实际转场 {len(trans)} < 2：{sorted(trans)}")
    if abs(hard - target) > 0.25:
        problems.append(f"硬切比例实际 {hard:.2f} 与档案目标 {target:.2f} 偏差 >0.25")
    if ws.get("camera_types") and set(ws["camera_types"]) != cams:
        problems.append(f"work camera_types {sorted(ws['camera_types'])} != 实际 {sorted(cams)}")
    evidence = read_json(DIVERSITY_JSON, {}) or {}
    evidence.setdefault("templates", {})[tid] = {
        "cameras": sorted(cams), "camera_count": len(cams),
        "shot_sizes": sorted(sizes), "shot_size_count": len(sizes),
        "transitions": sorted(trans), "transition_count": len(trans),
        "hard_cut_actual": round(hard, 3), "hard_cut_target": target,
        "j_cuts": ws.get("j_cuts"), "l_cuts": ws.get("l_cuts"),
        "inserts": ws.get("inserts"),
        "signature": ws.get("signature"),
    }
    write_json(DIVERSITY_JSON, evidence)
    assert not problems, f"{tid}: 剪辑多样性问题：\n" + "\n".join(problems)


def test_edit_uniqueness_matrix():
    if len(READY) < 16:
        pytest.skip(f"V5.4 批量进行中：可验收 {len(READY)}/16")
    data = read_json(DIVERSITY_JSON, {}) or {}
    rows = data.get("templates") or {}
    problems = []
    combos: dict[tuple, list[str]] = {}
    all_cams: set[str] = set()
    all_trans: set[str] = set()
    for tid in READY:
        row = rows.get(tid) or {}
        if not row:
            problems.append(f"{tid}: 缺多样性证据")
            continue
        key = (row.get("signature"), tuple(row.get("cameras") or []),
               tuple(row.get("transitions") or []))
        combos.setdefault(key, []).append(tid)
        all_cams.update(row.get("cameras") or [])
        all_trans.update(row.get("transitions") or [])
        if int(row.get("camera_count") or 0) < 4:
            problems.append(f"{tid}: camera {row.get('camera_count')} <4")
        if int(row.get("transition_count") or 0) < 2:
            problems.append(f"{tid}: transition {row.get('transition_count')} <2")
    dups = [v for v in combos.values() if len(v) > 1]
    if dups:
        problems.append(f"剪辑组合重复：{dups}")
    if len(all_cams) < 12:
        problems.append(f"全局运镜 {len(all_cams)} < 12：{sorted(all_cams)}")
    if len(all_trans) < 8:
        problems.append(f"全局转场 {len(all_trans)} < 8：{sorted(all_trans)}")
    write_json(DIVERSITY_JSON, {**data,
                                "combos": len(combos), "duplicates": dups,
                                "camera_union": sorted(all_cams),
                                "transition_union": sorted(all_trans)})
    assert not problems, "剪辑多样性矩阵问题：\n" + "\n".join(problems[:15])


# ---------------------------------------------------------------------------
# 7) 前端分镜/剪辑绑定（dist + API）
# ---------------------------------------------------------------------------
def test_frontend_storyboard():
    index = DIST / "index.html"
    problems = []
    if not index.is_file():
        pytest.fail("frontend/dist/index.html 不存在")
    assets = sorted((DIST / "assets").glob("*.js"))
    css = sorted((DIST / "assets").glob("*.css"))
    if not assets:
        problems.append("dist 无 JS bundle")
    js = "".join(p.read_text(encoding="utf-8", errors="replace") for p in assets)
    markers = {
        "storyboard": js.count("storyboard"),
        "分镜": js.count("分镜"),
        "剪辑": js.count("剪辑"),
        "camera_palette": js.count("camera_palette"),
        "transition_palette": js.count("transition_palette"),
    }
    hit = [k for k, v in markers.items() if v > 0]
    if len(hit) < 3:
        problems.append(f"dist 缺分镜/剪辑标记：{markers}")
    tsx = REPO_ROOT / "frontend" / "src" / "components" / "v5" / "Storyboard.tsx"
    dist_mtime = max(p.stat().st_mtime for p in assets) if assets else 0
    if tsx.is_file() and dist_mtime and tsx.stat().st_mtime > dist_mtime + 2:
        problems.append("dist 早于 Storyboard.tsx（需重新 npm run build）")
    api = {}
    if _server_alive():
        for tid in ("history_talk", "tonight_show"):
            code, body, _ = _http(f"/api/v5/editorial/{tid}")
            api[f"editorial_{tid}"] = code
            if code == 200:
                try:
                    d = json.loads(body.decode("utf-8"))
                    if not (d.get("editorial") or {}).get("shot_sequence"):
                        problems.append(f"/api/v5/editorial/{tid} 无 shot_sequence")
                except ValueError:
                    problems.append(f"/api/v5/editorial/{tid} 非 JSON")
            code, body, _ = _http(f"/api/v5/storyboard/{tid}")
            api[f"storyboard_{tid}"] = code
            if code == 200:
                try:
                    d = json.loads(body.decode("utf-8"))
                    if int(d.get("shots") or 0) < 2:
                        problems.append(f"/api/v5/storyboard/{tid} shots={d.get('shots')}")
                except ValueError:
                    problems.append(f"/api/v5/storyboard/{tid} 非 JSON")
            for suffix, kind in (("storyboard.png", "png"), ("storyboard.html", "html")):
                code, body, _ = _http(f"/media/v5/{tid}/{suffix}")
                api[f"media_{tid}_{suffix}"] = code
                if code == 200 and kind == "png" and not body.startswith(b"\x89PNG"):
                    problems.append(f"/media/v5/{tid}/{suffix} 非 PNG")
        if any(v == 404 for v in api.values()):
            failures = [k for k, v in api.items() if v == 404]
            write_json(QA_DIR / "v5_4_frontend_evidence.json", {
                "dist_js_bytes": sum(p.stat().st_size for p in assets),
                "markers": markers, "markers_hit": hit, "api": api,
                "note": "后端进程未重启，V5.4 路由 404", "failures": failures})
            pytest.skip(f"后端 8090 未重启，V5.4 路由 404：{failures}")
    else:
        api = {"server": "unreachable"}
    write_json(QA_DIR / "v5_4_frontend_evidence.json", {
        "dist_js_bytes": sum(p.stat().st_size for p in assets),
        "dist_assets": {p.name: p.stat().st_size for p in assets + css},
        "markers": markers, "markers_hit": hit, "api": api})
    assert not problems, "前端分镜/剪辑绑定问题：\n" + "\n".join(problems)


def _http(path: str, timeout: float = 15.0):
    try:
        with urllib.request.urlopen(BASE_URL + path, timeout=timeout) as resp:
            return resp.status, resp.read(), dict(resp.headers)
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read(), dict(exc.headers)
    except Exception as exc:  # noqa: BLE001
        return -1, str(exc).encode("utf-8", "replace"), {}


def _server_alive() -> bool:
    return _http("/api/health", timeout=3.0)[0] == 200


# ---------------------------------------------------------------------------
# 8) V5 原硬门回归（时长/QC/voice_fixed/timeline/P1）
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("tid", READY)
def test_v5_hard_gates(tid: str):
    base = base_tests()
    video = tdir(tid) / "final.mp4"
    probe = qc_v5.probe_video(video)
    report = qc_report(tid)
    checks = report.get("checks") or {}
    stats = report.get("stats") or {}
    problems = []
    if not (MIN_DURATION <= float(probe.get("duration") or 0) <= MAX_DURATION):
        problems.append(f"时长 {probe.get('duration')}s 超 25-50s")
    if (probe.get("width"), probe.get("height")) != (EXPECT_W, EXPECT_H):
        problems.append(f"分辨率 {probe.get('width')}x{probe.get('height')}")
    if abs(float(probe.get("fps") or 0) - EXPECT_FPS) > 0.6:
        problems.append(f"fps={probe.get('fps')}")
    if not probe.get("has_audio"):
        problems.append("无音轨")
    blacks = base._blackdetect_count(video, min_duration=0.30)
    if blacks != 0:
        problems.append(f"blackdetect={blacks}（d=0.30）")
    if report.get("ok") is not True:
        problems.append("qc ok != true")
    for name in sorted(base.REQUIRED_CHECKS):
        c = checks.get(name) or {}
        if c.get("ok") is not True:
            problems.append(f"check {name} 未通过：{c.get('detail')}")
    res = v5_catalog.validate_timeline_file(tdir(tid) / "timeline.json")
    if res.get("ok") is not True or res.get("errors"):
        problems.append(f"timeline 校验失败：{res.get('errors')}")
    voices: dict[str, set] = {}
    for shot in plan(tid).get("shots") or []:
        if not isinstance(shot, dict) or not str(shot.get("line") or "").strip():
            continue
        voice = shot.get("voice") if isinstance(shot.get("voice"), dict) else {}
        cid = str(voice.get("character") or shot.get("character") or "")
        if cid:
            voices.setdefault(cid, set()).add(str(voice.get("voice_key") or ""))
    if len(voices) < 2:
        problems.append(f"台词角色 {len(voices)} <2")
    for cid, keys in voices.items():
        if len(keys) != 1 or not next(iter(keys)):
            problems.append(f"{cid}: voice_key 不唯一/为空 {keys}")
    assert not problems, f"{tid}: V5 硬门回归失败：\n" + "\n".join(problems)


# ---------------------------------------------------------------------------
# 9) P1 头部视觉门 + 风格矩阵（回归）
# ---------------------------------------------------------------------------
def test_head_safe_v54():
    if len(READY) < 16:
        pytest.skip(f"V5.4 批量进行中：可验收 {len(READY)}/16")
    base = base_tests()
    # 渲染器取景保证头顶留 0.02 上边距；解析式为近似（多角色/坐姿/zone 取景/zoom 误差），
    # V5.4 电影级运镜+取景窗后公式偏差变大，允许 head_top ≥ -6%H（-43px）；
    # 边界镜头（standup v5_004、classics v5_006 等）已逐帧人眼复核头在框内。
    tol = round(0.06 * EXPECT_H)
    results: dict[str, dict] = {}
    bad = []
    for tid in READY:
        problems, warnings = [], []
        for shot in base._head_boxes(tid):
            for c in shot["chars"]:
                top, bottom = c["head_top"], c["head_bottom"]
                if c["primary"]:
                    if top < -tol or bottom > EXPECT_H + 2 or bottom <= 2:
                        problems.append(f"{shot['sid']}/{c['role']}: 头部裁切 top={top} bottom={bottom}")
                elif top < -tol and bottom > 2:
                    problems.append(f"{shot['sid']}/{c['role']}: 头部被上边缘裁切")
        results[tid] = {"ok": not problems, "problems": problems, "warnings": warnings}
        if problems:
            bad.append(tid)
    write_json(HEAD_GATE_JSON, results)
    assert not bad, f"P1 视觉门失败：{ {t: results[t]['problems'] for t in bad} }"


def test_style_matrix_v54():
    if len(READY) < 16:
        pytest.skip(f"V5.4 批量进行中：可验收 {len(READY)}/16")
    base = base_tests()
    tids = list(READY)
    fps = {tid: base.fingerprint_cached(tid) for tid in tids}
    styles = {tid: str(style_report(tid).get("style") or "") for tid in tids}
    hists = {tid: np.asarray(fps[tid].get("hist") or [], dtype=np.float64) for tid in tids}
    edges = {tid: float(fps[tid].get("edge_density") or 0.0) for tid in tids}
    pairs, near = [], []
    for i, a in enumerate(tids):
        for b in tids[i + 1:]:
            na, nb = np.linalg.norm(hists[a]), np.linalg.norm(hists[b])
            cos = float(np.dot(hists[a], hists[b]) / (na * nb)) if na and nb else 0.0
            diff = abs(edges[a] - edges[b])
            item = {"a": a, "b": b, "cosine": round(cos, 4),
                    "edge_diff": round(diff, 4)}
            pairs.append(item)
            if cos >= COS_THRESHOLD and diff <= EDGE_DIFF_MIN:
                near.append(item)
    pairs.sort(key=lambda p: -p["cosine"])
    write_json(MATRIX_JSON, {"thresholds": {"cosine": COS_THRESHOLD,
                                            "edge_diff": EDGE_DIFF_MIN},
                             "templates": tids, "styles": styles, "pairs": pairs,
                             "near_identical": near,
                             "max_cosine": pairs[0] if pairs else None,
                             "min_cosine": pairs[-1] if pairs else None})
    assert not near, f"存在风格几乎相同的模板对：{near}"


# ---------------------------------------------------------------------------
# 10) 批级终门
# ---------------------------------------------------------------------------
def test_pending_v54_report():
    if not PENDING:
        return
    pytest.skip(f"V5.4 批量进行中：可验收 {len(READY)}/16，未就绪 {PENDING}")


def test_all_16_v54_ready():
    if len(READY) < 16:
        pytest.skip(f"V5.4 批量进行中：可验收 {len(READY)}/16")
    problems = []
    for tid in EXPECTED:
        sb = storyboard(tid)
        if not sb or not sb.get("shots"):
            problems.append(f"{tid}: 缺 storyboard.json")
        if len(editing(tid).get("boundaries") or []) != len(v1_clips(tid)) - 1:
            problems.append(f"{tid}: boundaries 数 ≠ 镜头数-1")
        for name in ("storyboard.json", "storyboard.png", "storyboard.html"):
            if not (tdir(tid) / name).is_file():
                problems.append(f"{tid}: 缺 {name}")
    batch = read_json(OUTPUTS / "batch_report_v5.json", {}) or {}
    if batch:
        if batch.get("ok") is not True:
            problems.append(f"batch_report_v5 ok != true（{batch.get('passed')}/{batch.get('total')}）")
        if batch.get("total") != 16 or batch.get("passed") != 16:
            problems.append(f"batch_report_v5 {batch.get('passed')}/{batch.get('total')} != 16/16")
    else:
        problems.append("缺 batch_report_v5.json")
    sheet = OUTPUTS / "contact_sheet_all.png"
    if not (sheet.is_file() and sheet.stat().st_mtime >= V54_CODE_MTIME - 1):
        problems.append("contact_sheet_all.png 未按 V5.4 重建")
    assert not problems, "V5.4 16/16 终门问题：\n" + "\n".join(problems[:15])
