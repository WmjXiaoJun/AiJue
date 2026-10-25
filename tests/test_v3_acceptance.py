# -*- coding: utf-8 -*-
"""V3 P1/P2 独立验收（只读 outputs/ 与 contract/，不启动任何渲染/GPU）。

验收口径（docs/V3_INTERFACES.md §4/§6/§9）：
- 非卡片镜 100% h3=true，每镜 meta 含 workflow / prompt_id / seed / ref_image / action；
- workflow ∈ {04_i2v_fl2v_4step_sla, 05_ref2va_4step_sla}；
- prompt_id 非空且「模板内 + 全局」唯一（硬门）；
- ref_image 文件必须真实存在；
- 成片 1280x720 / 25fps / 有音轨 / blackdetect=0 / 时长与 episode 计划误差 ≤0.6s；
- 静态只允许片头片尾卡片；qc_report.fallbacks 必须为空（验收不接受非卡片静态兜底）；
- 元数据与 episode 非卡片 shot 一一对应（数量 + id/no 匹配）；
- 16 模板：id/style/plate 与 §8 映射一致、差异性签名两两唯一、plate 复用/Jaccard 相似度记录；
- 2D 水墨（history_talk）：契约级校验 + 抽帧证据落 tmp/qa/ink_review/ 供人审。

运行：
  python -m pytest tests/test_v3_acceptance.py -q                # 全部
  python -m pytest tests/test_v3_acceptance.py -q -k tonight_show
  python -m pytest tests/test_v3_acceptance.py -q -k signature    # 仅模板差异复核

落盘未完成时自动 skip 并在 skip 原因中报告缺失模板；落盘后无需改代码直接生效。
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from pipeline import qc as pipeline_qc  # 复用生产同一套 ffmpeg/ffprobe 探测与黑帧检测
from pipeline import template_v3 as tv

OUTPUTS = REPO_ROOT / "outputs" / "templates_v3"
TEMPLATE_DIR = REPO_ROOT / "contract" / "templates_v3"
INK_EVIDENCE_DIR = REPO_ROOT / "tmp" / "qa" / "ink_review"

WORKFLOWS = {"04_i2v_fl2v_4step_sla", "05_ref2va_4step_sla"}
H3_MODES = {"audio_ref", "fl2v"}
CARD_TYPES = {"title_card", "end_card", "meme_card"}
TARGET_RESOLUTION = (1280, 720)
TARGET_FPS = 25.0
DURATION_TOLERANCE = 0.6

# docs/V3_INTERFACES.md §8：16 模板 → style / plate 映射（契约硬门）
EXPECTED_STYLE = {
    "tonight_show": "cinematic_3d",
    "shark_tank": "cinematic_3d",
    "masked_singer": "neon_stage_3d",
    "standup": "retro_standup_2d",
    "taskmaster": "game_show_3d",
    "traitors": "roundtable_3d",
    "snl_sketch": "sketch_3d",
    "roundtable": "roundtable_3d",
    "hot_ones": "warm_talk_3d",
    "qipashuo": "debate_ink_2d",
    "slow_life": "clay_life_3d",
    "history_talk": "history_ink_2d",
    "history_mystery": "history_ink_2d",
    "classics": "history_ink_2d",
    "national_treasure": "museum_gold_2d",
    "mystery": "noir_mystery_3d",
}
EXPECTED_PLATE = {
    "tonight_show": "late_night_desk__cinematic",
    "shark_tank": "pitch_room__cinematic",
    "masked_singer": "stage_music__neon",
    "standup": "stage_music__vhs",
    "taskmaster": "taskmaster_stage__game",
    "traitors": "castle_hall__roundtable",
    "snl_sketch": "stage_music__theatre",
    "roundtable": "roundtable_studio__roundtable",
    "hot_ones": "talk_show_sofa__warmtalk",
    "qipashuo": "debate_hall__debateink",
    "slow_life": "kitchen_warm__clay",
    "history_talk": "history_study__ink",
    "history_mystery": "crime_scene__ink",
    "classics": "archive_room__ink",
    "national_treasure": "treasure_museum__gold",
    "mystery": "mystery_room__noir",
}
EXPECTED_TEMPLATES = set(EXPECTED_STYLE)


# ---------------------------------------------------------------------------
# 产物扫描 / 通用读取
# ---------------------------------------------------------------------------
def rendered_template_ids() -> list[str]:
    """outputs/templates_v3 下已有 final.mp4 的模板 id。"""
    if not OUTPUTS.is_dir():
        return []
    return [p.name for p in sorted(OUTPUTS.iterdir())
            if p.is_dir() and (p / "final.mp4").is_file()]


def template_dir(template_id: str) -> Path:
    return OUTPUTS / template_id


def read_json(path: Path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def find_output(template_id: str, *names: str) -> Path | None:
    for name in names:
        p = template_dir(template_id) / name
        if p.is_file():
            return p
    return None


def _read_optional_json(path: Path | None):
    if path is None or not path.is_file():
        return None
    try:
        return read_json(path)
    except (OSError, ValueError):
        return None


def acceptance_ready(template_id: str) -> bool:
    """「可验收」口径（渲染期间按 Lead 要求只断言已完成产物）：

    final.mp4 + qc_report.ok=true + h3_meta.json 已落盘且非 previs 预览。
    中间态（正在渲染/旧 previs 残留）→ 不算可验收，相关用例 skip 并报告。
    """
    if not (template_dir(template_id) / "final.mp4").is_file():
        return False
    report = _read_optional_json(find_output(template_id, "qc_report.json"))
    if not isinstance(report, dict) or report.get("ok") is not True:
        return False
    meta = _read_optional_json(find_output(template_id, "h3_meta.json"))
    if not isinstance(meta, dict):
        return False
    if meta.get("previs") is True:
        return False
    return True


RENDERED_RAW = rendered_template_ids()
# 只验收 §8 的 16 个模板 id；outputs/templates_v3 下可能还有 Lead 的版本归档目录
RENDERED = [tid for tid in RENDERED_RAW if tid in EXPECTED_TEMPLATES]
UNEXPECTED_DIRS = [tid for tid in RENDERED_RAW if tid not in EXPECTED_TEMPLATES]
# 可验收（已完成且 qc_ok 的正式产物）；渲染期间其余目录按中间态处理
READY = [tid for tid in RENDERED if acceptance_ready(tid)]
PENDING_OUTPUTS = [tid for tid in RENDERED if tid not in READY]


def is_version_archive(dir_name: str) -> bool:
    """非 16 模板目录的允许形态：Lead 的版本归档 / P3 五分钟版。

    - 版本归档：tonight_show_40s_v1 / history_talk_30s_v2
    - P3 五分钟版：tonight_show_5min（不属 P2 的 16×30-45s 验收范围）
    """
    import re
    if not any(dir_name.startswith(f"{tid}_") for tid in EXPECTED_TEMPLATES):
        return False
    return re.search(r"_(?:\d+s(?:_v\d+)?|5min)$", dir_name) is not None


def load_episode(template_id: str) -> dict:
    """render.py 兼容的 episode/plan（契约优先 work/episode.json，兼容旧布局）。"""
    path = find_output(template_id, "work/episode.json", "episode.json",
                       "work/plan.json", "plan.json")
    assert path is not None, (
        f"{template_id}: 缺少 episode.json（契约 §0/§6：outputs/templates_v3/"
        f"{template_id}/work/episode.json）")
    data = read_json(path)
    assert isinstance(data, dict), f"{template_id}: episode.json 根节点必须是 object"
    return data


def episode_shots(episode: dict) -> list[dict]:
    shots = episode.get("shots")
    if shots is None and isinstance(episode.get("episode"), dict):
        shots = episode["episode"].get("shots")
    assert isinstance(shots, list) and shots, "episode.json 缺少非空 shots 数组"
    return [s for s in shots if isinstance(s, dict)]


def is_card_shot(shot: dict) -> bool:
    stype = str(shot.get("type") or shot.get("intent") or "").lower()
    if stype in CARD_TYPES:
        return True
    return "card" in stype


def _meta_entries(data) -> list[dict]:
    """兼容 h3_meta.json 的几种合理落盘形态，返回「每镜 meta dict」列表。"""
    if isinstance(data, list):
        items = data
    elif isinstance(data, dict):
        items = None
        for key in ("shots", "metas", "h3_meta", "h3_metas", "items", "clips"):
            value = data.get(key)
            if isinstance(value, list):
                items = value
                break
            if isinstance(value, dict):
                nested = [v for v in value.values() if isinstance(v, dict)]
                if nested:
                    items = nested
                    break
        if items is None:
            # dict{shot_id / shot_no : meta}
            items = [v for v in data.values() if isinstance(v, dict)]
    else:
        items = []
    out: list[dict] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        merged = dict(item)
        for wrap in ("meta", "h3", "comfy", "evidence"):
            nested = item.get(wrap)
            if isinstance(nested, dict):
                for k, v in nested.items():
                    merged.setdefault(k, v)
        out.append(merged)
    return out


def load_shot_metas(template_id: str) -> list[dict]:
    path = find_output(template_id, "h3_meta.json")
    assert path is not None, (
        f"{template_id}: 缺少 h3_meta.json（契约 §0/§4：每镜 H3 证据）")
    return _meta_entries(read_json(path))


def load_h3_meta_raw(template_id: str):
    """聚合 h3_meta.json 原始 dict（用于和逐镜明细交叉核对，防记账错误）。"""
    path = find_output(template_id, "h3_meta.json")
    if path is None:
        return None
    return read_json(path)


def resolve_ref_image(template_id: str, ref) -> Path | None:
    """ref_image 可能是绝对路径 / 相对仓库根 / 相对模板输出目录。"""
    if isinstance(ref, (list, tuple)):
        ref = ref[0] if ref else None
    if not ref:
        return None
    p = Path(str(ref))
    candidates = [p] if p.is_absolute() else [
        template_dir(template_id) / p,
        OUTPUTS / template_id / p,
        REPO_ROOT / p,
    ]
    for cand in candidates:
        if cand.is_file():
            return cand
    return None


def expected_total_duration(episode: dict) -> float:
    """episode/plan 总时长：优先显式字段，否则 shots + cards 求和（§5 卡片独立于 shots）。"""
    for holder in (episode, episode.get("episode") if isinstance(episode.get("episode"), dict) else {}):
        if not isinstance(holder, dict):
            continue
        for key in ("total_duration", "duration"):
            value = holder.get(key)
            if isinstance(value, (int, float)) and float(value) > 0:
                return float(value)
    total = sum(float(s.get("duration") or 0) for s in episode_shots(episode))
    cards = episode.get("cards")
    if isinstance(cards, dict):
        total += sum(float(c.get("duration") or 0) for c in cards.values()
                     if isinstance(c, dict))
    elif isinstance(cards, list):
        total += sum(float(c.get("duration") or 0) for c in cards
                     if isinstance(c, dict))
    meta = episode.get("meta")
    if isinstance(meta, dict):
        for key in ("total_duration", "duration"):
            value = meta.get(key)
            if isinstance(value, (int, float)) and float(value) > 0:
                return float(value)
    return total


# ---------------------------------------------------------------------------
# 1) 每模板：H3 证据链（硬门）
# ---------------------------------------------------------------------------
def _plan_meta_maps(metas: list[dict]) -> tuple[dict, dict]:
    by_id: dict[str, dict] = {}
    by_no: dict[str, dict] = {}
    for meta in metas:
        sid = str(meta.get("shot_id") or meta.get("id") or "").strip()
        if sid:
            by_id.setdefault(sid, meta)
        no = meta.get("no")
        if no is not None and str(no).strip() != "":
            by_no.setdefault(str(no), meta)
    return by_id, by_no


def _meta_for_shot(shot: dict, by_id: dict, by_no: dict) -> dict | None:
    sid = str(shot.get("id") or "").strip()
    if sid and sid in by_id:
        return by_id[sid]
    no = shot.get("no")
    if no is not None and str(no) in by_no:
        return by_no[str(no)]
    return None


def _evidence_problems(template_id: str, where: str, meta: dict) -> list[str]:
    """校验单镜 meta 的 workflow/prompt_id/seed/ref_image/action 证据链。"""
    problems: list[str] = []
    if meta.get("h3") is not True:
        problems.append(f"{where}: h3 != true ({meta.get('h3')!r})")
    workflow = str(meta.get("workflow") or "").strip()
    if workflow not in WORKFLOWS:
        problems.append(f"{where}: workflow={workflow!r} 不在 {sorted(WORKFLOWS)}")
    if not str(meta.get("prompt_id") or "").strip():
        problems.append(f"{where}: prompt_id 为空（ComfyUI 不可追溯）")
    if "seed" not in meta or meta.get("seed") is None:
        problems.append(f"{where}: 缺少 seed")
    ref = meta.get("ref_image")
    if not ref:
        problems.append(f"{where}: 缺少 ref_image")
    else:
        refs = ref if isinstance(ref, (list, tuple)) else [ref]
        for one_ref in refs:
            if resolve_ref_image(template_id, one_ref) is None:
                problems.append(f"{where}: ref_image 文件不存在 {one_ref!r}")
    if not str(meta.get("action") or "").strip():
        problems.append(f"{where}: 缺少 action")
    mode = meta.get("mode")
    if mode is not None and str(mode) not in H3_MODES:
        problems.append(f"{where}: mode={mode!r} 不在 {sorted(H3_MODES)}")
    duration = meta.get("duration")
    if duration is not None and not (isinstance(duration, (int, float))
                                     and float(duration) > 0):
        problems.append(f"{where}: duration 非法 {duration!r}")
    return problems


@pytest.mark.parametrize("template_id", READY)
def test_h3_evidence_chain(template_id: str):
    metas = load_shot_metas(template_id)
    assert metas, f"{template_id}: h3_meta.json 没有任何镜头证据"
    episode = load_episode(template_id)
    shots = episode_shots(episode)
    cards = [s for s in shots if is_card_shot(s)]
    bodies = [s for s in shots if not is_card_shot(s)]
    assert bodies, f"{template_id}: episode 里没有非卡片镜头"

    by_id, by_no = _plan_meta_maps(metas)
    problems: list[str] = []
    prompt_ids: list[str] = []
    h3_metas: list[tuple[str, dict]] = []      # 非卡片镜 meta

    if by_id or by_no:
        # meta 带 shot id/no：必须与计划非卡片镜一一对应，且卡片不得冒充 H3
        for shot in bodies:
            sid = str(shot.get("id") or "")
            meta = _meta_for_shot(shot, by_id, by_no)
            if meta is None:
                problems.append(f"{template_id}: 非卡片镜 {sid or shot.get('no')} 缺 h3 meta")
                continue
            h3_metas.append((sid or str(shot.get("no")), meta))
        for shot in cards:
            meta = _meta_for_shot(shot, by_id, by_no)
            if meta is not None and meta.get("h3") is True:
                problems.append(
                    f"{template_id}: 卡片 {shot.get('id')} 被标成 h3=true（静态卡不能冒充 H3）")
        plan_ids = {str(s.get("id")) for s in shots if s.get("id")}
        for sid in by_id:
            if sid not in plan_ids:
                problems.append(f"{template_id}: meta 的 shot id {sid!r} 不在 episode 计划中")
    else:
        # 无法按 id 对齐（聚合文件只有 H3 镜列表）：按数量 + h3 标记校验
        h3_metas = [(str(i), m) for i, m in enumerate(metas) if m.get("h3") is True]
        if len(h3_metas) != len(bodies):
            problems.append(
                f"{template_id}: H3 meta {len(h3_metas)} 条与 episode 非卡片镜 "
                f"{len(bodies)} 条不一致")

    for where, meta in h3_metas:
        problems.extend(_evidence_problems(template_id, f"{template_id} meta[{where}]", meta))
        prompt_id = str(meta.get("prompt_id") or "").strip()
        if prompt_id:
            prompt_ids.append(prompt_id)

    # 非卡片镜 100% H3（硬门）
    if len(h3_metas) != len(bodies):
        problems.append(
            f"{template_id}: 非卡片镜 H3 覆盖 {len(h3_metas)}/{len(bodies)}，要求 100%")

    # 聚合 h3_meta.json 的自报汇总必须与明细/计划一致（防记账错误）
    raw = load_h3_meta_raw(template_id)
    if isinstance(raw, dict):
        if raw.get("previs") is True:
            problems.append(f"{template_id}: h3_meta.previs=true（交付版不允许 previs）")
        if "shots_count" in raw and raw["shots_count"] != len(shots):
            problems.append(
                f"{template_id}: h3_meta.shots_count={raw['shots_count']} != 计划 {len(shots)}")
        if "h3_shots" in raw and raw["h3_shots"] != len(bodies):
            problems.append(
                f"{template_id}: h3_meta.h3_shots={raw['h3_shots']} != 非卡片镜 {len(bodies)}")
        if "cards" in raw and raw["cards"] != len(cards):
            problems.append(
                f"{template_id}: h3_meta.cards={raw['cards']} != 计划卡片 {len(cards)}")
        if raw.get("prompt_ids_unique") is False:
            problems.append(f"{template_id}: h3_meta.prompt_ids_unique=false")
        if raw.get("fallbacks"):
            problems.append(f"{template_id}: h3_meta.fallbacks 非空：{raw['fallbacks']}")

    assert not problems, "H3 证据链不完整：\n" + "\n".join(problems)

    # prompt_id 模板内唯一
    duplicates = sorted({pid for pid in prompt_ids if prompt_ids.count(pid) > 1})
    assert not duplicates, f"{template_id}: prompt_id 重复（必须全局唯一）：{duplicates}"


# ---------------------------------------------------------------------------
# 2) 每模板：成片规格 / 黑帧（硬门）
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("template_id", READY)
def test_video_spec_and_blackdetect(template_id: str):
    video = template_dir(template_id) / "final.mp4"
    size = video.stat().st_size
    assert size > 10 * 1024, f"{template_id}: final.mp4 过小/损坏（{size} bytes）"

    probe = pipeline_qc.probe(video)
    assert (probe["width"], probe["height"]) == TARGET_RESOLUTION, (
        f"{template_id}: 分辨率 {probe['width']}x{probe['height']} != 1280x720")
    assert abs(probe["fps"] - TARGET_FPS) < 0.1, (
        f"{template_id}: fps {probe['fps']} != 25")
    assert probe["has_audio"], f"{template_id}: 成片没有音轨"
    blacks = pipeline_qc.blackdetect(video)
    assert blacks == 0, f"{template_id}: blackdetect={blacks}（验收要求 0）"


# ---------------------------------------------------------------------------
# 3) 每模板：元数据 / 时长与计划一致（硬门）
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("template_id", READY)
def test_metadata_matches_plan(template_id: str):
    episode = load_episode(template_id)
    shots = episode_shots(episode)
    total = expected_total_duration(episode)
    assert total > 0, f"{template_id}: episode 总时长为 0"

    probe = pipeline_qc.probe(template_dir(template_id) / "final.mp4")
    delta = abs(probe["duration"] - total)
    assert delta <= DURATION_TOLERANCE, (
        f"{template_id}: 成片 {probe['duration']}s 与 episode {total:.2f}s "
        f"误差 {delta:.2f}s > {DURATION_TOLERANCE}s")
    # P2 demo 档（30-45s）：计划总时长应在 30-45s 区间（5 分钟版 P3 不在此断言）
    if total <= 60:
        assert 29.0 <= total <= 46.0, (
            f"{template_id}: demo 计划时长 {total:.2f}s 不在 30-45s（§9）")

    # episode 规格字段（契约 §5：resolution 1280x720 / fps 25 / aspect 16:9 / format_id）
    meta_ep = episode.get("episode") if isinstance(episode.get("episode"), dict) else episode
    resolution = meta_ep.get("resolution") or episode.get("resolution")
    fps = meta_ep.get("fps") or episode.get("fps")
    if resolution is not None:
        assert str(resolution).replace("×", "x").replace("*", "x") == "1280x720", (
            f"{template_id}: episode resolution={resolution!r} != 1280x720")
    if fps is not None:
        assert float(fps) == TARGET_FPS, f"{template_id}: episode fps={fps!r} != 25"
    assert str(meta_ep.get("aspect") or episode.get("aspect") or "16:9") == "16:9", (
        f"{template_id}: episode aspect 不是 16:9")
    manifest = find_output(template_id, "work/manifest.json", "manifest.json")
    assert manifest is not None, (
        f"{template_id}: 缺少 manifest.json（契约 §0/§6）")

    # 卡片只允许片头/片尾静态镜头
    cards = [s for s in shots if is_card_shot(s)]
    for card in cards:
        assert str(card.get("type") or "") in CARD_TYPES, (
            f"{template_id}: 非法卡片类型 {card.get('type')!r}")


# ---------------------------------------------------------------------------
# 4) 每模板：QC 报告必须全绿 + 无静态兜底（硬门）
# ---------------------------------------------------------------------------
BLACK_CHECK_ALIASES = ("blackdetect", "black_frames", "black_frame", "black")
STATIC_CHECK_ALIASES = ("static_marked", "static", "static_cards", "cards")
STAGE_CHECK_ALIASES = ("stage_meta", "ref_image", "stage")
H3_CHECK_ALIASES = ("h3_coverage", "h3", "h3_required")


def report_checks(report: dict) -> dict[str, dict]:
    """兼容 qc_v3 的 dict 形式 checks（name -> {ok,detail}）与旧 list 形式。"""
    raw = report.get("checks")
    checks: dict[str, dict] = {}
    if isinstance(raw, dict):
        for name, item in raw.items():
            if isinstance(item, dict):
                checks[str(name).strip().lower()] = item
            elif isinstance(item, bool):
                checks[str(name).strip().lower()] = {"ok": item, "detail": ""}
    elif isinstance(raw, list):
        for item in raw:
            if isinstance(item, dict):
                checks[str(item.get("name") or "").strip().lower()] = item
    return checks


def find_check(report: dict, aliases: tuple[str, ...]) -> dict | None:
    checks = report_checks(report)
    for alias in aliases:
        if alias in checks:
            return checks[alias]
    return None


@pytest.mark.parametrize("template_id", READY)
def test_qc_report_green(template_id: str):
    path = find_output(template_id, "qc_report.json")
    assert path is not None, (
        f"{template_id}: 缺少 qc_report.json（契约 §0/§6）")
    report = read_json(path)
    assert isinstance(report, dict), f"{template_id}: qc_report.json 根节点必须是 object"
    assert report.get("ok") is True, (
        f"{template_id}: qc_report.ok != true；未通过 checks="
        f"{[k for k, v in report_checks(report).items() if v.get('ok') is not True]}")
    # 交付级 QC（qc_v3.deliverable）：previs 或存在静态兜底都不算可交付
    if "deliverable" in report:
        assert report["deliverable"] is True, (
            f"{template_id}: qc_report.deliverable != true（previs/静态兜底不算交付）")
    assert report.get("previs") is not True, (
        f"{template_id}: qc_report.previs=true，成片不是 H3 交付版")

    required = {
        "duration": ("duration",),
        "resolution": ("resolution",),
        "fps": ("fps",),
        "audio": ("audio",),
        "black": BLACK_CHECK_ALIASES,
        "subtitle_coverage": ("subtitle_coverage", "subtitle", "subtitles"),
        "h3_coverage": H3_CHECK_ALIASES,
        "traceability": ("traceability", "traceable"),
        "static_marked": STATIC_CHECK_ALIASES,
        "stage_meta": STAGE_CHECK_ALIASES,
    }
    present_names = sorted(report_checks(report))
    problems = []
    for label, aliases in required.items():
        check = find_check(report, aliases)
        if check is None:
            problems.append(f"缺少 check {label!r}（现有 {present_names}）")
        elif check.get("ok") is not True:
            problems.append(f"check {label!r} 未通过：{check.get('detail')!r}")
    assert not problems, f"{template_id}: QC 未全绿：\n" + "\n".join(problems)

    # 验收不接受任何非卡片静态兜底
    fallbacks = report.get("fallbacks")
    assert fallbacks == [] or fallbacks is None, (
        f"{template_id}: qc_report.fallbacks 非空（静态兜底镜头）：{fallbacks}")
    if fallbacks is None:
        assert "fallbacks" in report, (
            f"{template_id}: qc_report 缺少 fallbacks 字段（契约 §6 要求列出兜底镜头）")


# ---------------------------------------------------------------------------
# 5) 批级：prompt_id 全局唯一 + workflow 合法（硬门）
# ---------------------------------------------------------------------------
def test_prompt_ids_globally_unique():
    if not READY:
        pytest.skip("尚无已完成（qc_ok 且非 previs）的模板，跳过全局 prompt_id 复核")
    all_prompt_ids: list[tuple[str, str]] = []
    bad_workflows = []
    for template_id in READY:
        for meta in load_shot_metas(template_id):
            # 只看 H3 镜（卡片 static_card / previs 静态镜不参与 H3 证据）
            if meta.get("h3") is not True:
                continue
            prompt_id = str(meta.get("prompt_id") or "").strip()
            if prompt_id:
                all_prompt_ids.append((template_id, prompt_id))
            workflow = str(meta.get("workflow") or "").strip()
            if workflow not in WORKFLOWS:
                bad_workflows.append(f"{template_id}: {workflow!r}")
    assert all_prompt_ids, "所有已渲染模板都没有 H3 镜的 prompt_id"
    seen: dict[str, str] = {}
    collisions = []
    for template_id, prompt_id in all_prompt_ids:
        if prompt_id in seen:
            collisions.append(f"{prompt_id}: {seen[prompt_id]} vs {template_id}")
        else:
            seen[prompt_id] = template_id
    assert not collisions, "prompt_id 全局重复：\n" + "\n".join(collisions)
    assert not bad_workflows, f"非法 workflow：{bad_workflows}"


# ---------------------------------------------------------------------------
# 6) 16 模板差异复核（独立于 validate_dir 实现）
# ---------------------------------------------------------------------------
def pending_template_ids() -> list[str]:
    return sorted(EXPECTED_TEMPLATES - set(READY))


def test_all_16_templates_rendered():
    """未渲染完时 skip 并报告缺哪些（任务要求：对未完成模板 skip + 报告）。"""
    if not RENDERED:
        pytest.skip("outputs/templates_v3 尚无任何模板落盘（等 Lead 渲染）")
    missing = sorted(EXPECTED_TEMPLATES - set(RENDERED))
    if missing:
        pytest.skip(f"渲染进行中：已落盘 {len(RENDERED)}/16，未渲染：{missing}")
    assert set(RENDERED) == EXPECTED_TEMPLATES, (
        f"outputs/templates_v3 目录集合与 16 模板不一致；多出 "
        f"{sorted(set(RENDERED) - EXPECTED_TEMPLATES)}")


def test_unexpected_output_dirs():
    """outputs/templates_v3 下除 16 模板外的目录：只允许版本归档命名（Lead 备份用）。"""
    if not UNEXPECTED_DIRS:
        return
    bad = [name for name in UNEXPECTED_DIRS if not is_version_archive(name)]
    assert not bad, (
        f"outputs/templates_v3 存在非契约/非归档目录：{bad}；"
        f"允许的归档命名形如 <template>_<NN>s[_vN]（当前归档 {UNEXPECTED_DIRS}）")


def test_pending_outputs_report():
    """渲染期间：列出尚未达到可验收标准的目录（中间态/旧 previs 残留），skip 不误报。"""
    if not RENDERED:
        pytest.skip("outputs/templates_v3 尚无任何模板落盘（等 Lead 渲染）")
    if not PENDING_OUTPUTS:
        return
    pytest.skip(f"以下目录尚未达到可验收标准（渲染中/旧 previs 残留），暂不验收："
                f"{PENDING_OUTPUTS}；可验收 {len(READY)}/16")


def test_all_16_templates_acceptable():
    """最终门：16 个模板全部落盘且全部达到可验收标准（qc_ok + 非 previs）。"""
    if len(RENDERED) < 16:
        pytest.skip(f"渲染进行中：已落盘 {len(RENDERED)}/16")
    assert set(RENDERED) == EXPECTED_TEMPLATES, (
        f"outputs/templates_v3 目录集合与 16 模板不一致；多出 "
        f"{sorted(set(RENDERED) - EXPECTED_TEMPLATES)}，缺 "
        f"{sorted(EXPECTED_TEMPLATES - set(RENDERED))}")
    assert set(READY) == EXPECTED_TEMPLATES, (
        f"16/16 已落盘但未全部达到可验收标准；未达标：{PENDING_OUTPUTS}")
    # 最终交付不允许 previs/静态兜底残留
    problems = []
    for tid in sorted(EXPECTED_TEMPLATES):
        report = _read_optional_json(find_output(tid, "qc_report.json")) or {}
        meta = _read_optional_json(find_output(tid, "h3_meta.json")) or {}
        if report.get("previs") is True or meta.get("previs") is True:
            problems.append(f"{tid}: 仍是 previs")
        if report.get("deliverable") is False:
            problems.append(f"{tid}: qc deliverable=false")
        if report.get("fallbacks"):
            problems.append(f"{tid}: qc fallbacks={len(report['fallbacks'])}")
    assert not problems, "最终交付存在非交付级产物：\n" + "\n".join(problems)


@pytest.mark.skipif(
    not all((TEMPLATE_DIR / f"{tid}.json").is_file() for tid in EXPECTED_TEMPLATES),
    reason="16 个 templates_v3 模板尚未全部落盘")
def test_16_template_contract_mapping():
    """16 模板 id / style / plate 必须符合 §8 契约映射。"""
    loads = {tid: tv.load_file(TEMPLATE_DIR / f"{tid}.json")
             for tid in EXPECTED_TEMPLATES}
    problems = []
    for tid, template in loads.items():
        style = str(template.get("style") or "")
        if style != EXPECTED_STYLE[tid]:
            problems.append(f"{tid}: style={style!r} != {EXPECTED_STYLE[tid]!r}（§8）")
        plate = str((template.get("stage") or {}).get("plate") or "")
        if plate != EXPECTED_PLATE[tid]:
            problems.append(f"{tid}: plate={plate!r} != {EXPECTED_PLATE[tid]!r}（§8）")
    assert not problems, "模板与 §8 契约映射不一致：\n" + "\n".join(problems)


@pytest.mark.skipif(
    not all((TEMPLATE_DIR / f"{tid}.json").is_file() for tid in EXPECTED_TEMPLATES),
    reason="16 个 templates_v3 模板尚未全部落盘")
def test_16_template_signatures_unique():
    """独立复核：plate + cast 站位组合 + action 集合签名两两唯一（§9 硬门）。"""
    signatures: dict[tuple, str] = {}
    duplicates = []
    for tid in sorted(EXPECTED_TEMPLATES):
        template = tv.load_file(TEMPLATE_DIR / f"{tid}.json")
        sig = tv.template_signature(template)
        if sig in signatures:
            duplicates.append(f"{tid} == {signatures[sig]}")
        else:
            signatures[sig] = tid
    assert not duplicates, (
        "16 模板差异性签名重复（plate+cast站位+action集合）：\n" + "\n".join(duplicates))
    assert len(signatures) == 16, f"签名唯一数量 {len(signatures)} != 16"

    # plate 复用必须能说明理由 -> 打印记录，交给 docs/V3_ACCEPTANCE.md 复核
    plate_map: dict[str, list[str]] = {}
    action_sets: dict[str, set[str]] = {}
    for tid in sorted(EXPECTED_TEMPLATES):
        template = tv.load_file(TEMPLATE_DIR / f"{tid}.json")
        plate = str((template.get("stage") or {}).get("plate") or "")
        plate_map.setdefault(plate, []).append(tid)
        actions = {str(a.get("id") or "") for a in template.get("action_library") or []
                   if isinstance(a, dict)}
        action_sets[tid] = actions
    reused = {plate: ids for plate, ids in plate_map.items() if len(ids) > 1}
    print(f"[qa] plate 复用情况: {reused or '无（16/16 唯一）'}")
    high_similarity = []
    tids = sorted(action_sets)
    for i, a in enumerate(tids):
        for b in tids[i + 1:]:
            sa, sb = action_sets[a], action_sets[b]
            if not sa or not sb:
                continue
            jac = len(sa & sb) / len(sa | sb)
            if jac > 0.8:
                high_similarity.append(f"{a}/{b} Jaccard={jac:.0%}")
    print(f"[qa] 动作集合相似度>80%: {high_similarity or '无'}")


def test_templates_validate_strict():
    """P0 验收命令的 pytest 版：16/16 全通过（含 plate 资产，strict）。"""
    template_files = sorted(TEMPLATE_DIR.glob("*.json"))
    if len(template_files) != 16:
        pytest.skip(f"templates_v3 当前 {len(template_files)}/16 个，跳过 16/16 严格校验")
    result = tv.validate_dir(TEMPLATE_DIR, expect=16, strict_plates=True)
    fails = {r["id"]: r["errors"] for r in result["results"] if not r["ok"]}
    assert result["ok"] is True, f"16/16 严格校验未通过：{fails}"


# ---------------------------------------------------------------------------
# 7) 抽帧证据：contact sheet / 2D 水墨（history_talk）人审帧
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("template_id", READY)
def test_contact_sheet_evidence(template_id: str):
    frames = template_dir(template_id) / "frames"
    assert frames.is_dir(), f"{template_id}: 缺少 frames/ 抽帧目录（契约 §0）"
    images = sorted(frames.glob("*.png")) + sorted(frames.glob("*.jpg"))
    assert len(images) >= 1, f"{template_id}: frames/ 下没有抽帧图片"


def extract_review_frames(video: Path, out_dir: Path, ratio_points=(0.15, 0.5, 0.85)) -> list[Path]:
    """ffmpeg 抽 3 帧作人审证据（写 tmp/qa/，不进业务产物）。"""
    out_dir.mkdir(parents=True, exist_ok=True)
    duration = float(pipeline_qc.probe(video)["duration"])
    assert duration > 0, "成片时长为 0，无法抽帧"
    ffmpeg = pipeline_qc.FF
    frames: list[Path] = []
    for i, ratio in enumerate(ratio_points):
        out = out_dir / f"frame_{i}_{int(ratio * 100):02d}pct.jpg"
        cmd = [str(ffmpeg), "-y", "-ss", f"{duration * ratio:.3f}", "-i", str(video),
               "-frames:v", "1", "-q:v", "2", str(out)]
        proc = subprocess.run(cmd, capture_output=True, text=True,
                              encoding="utf-8", errors="replace")
        assert proc.returncode == 0, f"ffmpeg 抽帧失败：{proc.stderr[-400:]}"
        assert out.is_file() and out.stat().st_size > 1024, f"抽帧文件异常：{out}"
        frames.append(out)
    return frames


def test_history_talk_ink_review_frames():
    """2D 水墨路径：契约级确认 + 抽帧证据（供 Lead 人审）。"""
    template_id = "history_talk"
    if template_id not in RENDERED:
        pytest.skip(f"{template_id} 尚未渲染，跳过水墨抽帧人审")
    template = tv.load_file(TEMPLATE_DIR / f"{template_id}.json")
    assert str(template.get("style") or "") == "history_ink_2d", (
        f"{template_id}: style={template.get('style')!r} != history_ink_2d")
    assert str((template.get("stage") or {}).get("type") or "") in ("2d_plate", "h3_scene"), (
        f"{template_id}: stage.type 不是 2D（{(template.get('stage') or {}).get('type')!r}）")
    plate = str((template.get("stage") or {}).get("plate") or "")
    assert "ink" in plate, f"{template_id}: plate={plate!r} 不是水墨场景"

    episode = load_episode(template_id)
    dump = json.dumps(episode, ensure_ascii=False)
    assert "history_ink_2d" in dump or "ink" in dump.lower(), (
        f"{template_id}: episode.json 中找不到 2D 水墨风格标记")

    frames = extract_review_frames(template_dir(template_id) / "final.mp4",
                                   INK_EVIDENCE_DIR / template_id)
    print("[qa] history_talk 水墨人审帧（Lead 请目检）：")
    for frame in frames:
        print(f"[qa]   {frame}")
    assert len(frames) == 3


def test_batch_report_covered():
    """批量报告（契约 §0 outputs/templates_v3/batch_report.json）覆盖已渲染模板且交付状态正确。"""
    path = OUTPUTS / "batch_report.json"
    if not RENDERED:
        pytest.skip("尚无已渲染模板，跳过 batch_report 复核")
    if not path.is_file():
        pytest.skip("batch_report.json 尚未生成（批量脚本可能仍在跑）")
    report = read_json(path)
    dump = json.dumps(report, ensure_ascii=False)
    missing = [tid for tid in RENDERED if tid not in dump]
    assert not missing, f"batch_report.json 未覆盖已渲染模板：{missing}"

    entries = report.get("templates")
    master = report.get("contact_sheet")
    if master:
        master_path = Path(str(master))
        if not master_path.is_absolute():
            master_path = REPO_ROOT / master_path
        assert master_path.is_file(), (
            f"batch_report.contact_sheet 指向的文件不存在：{master_path}")
    elif len(READY) == len(EXPECTED_TEMPLATES):
        raise AssertionError("16/16 已渲染但 batch_report 缺少总 contact sheet（§9）")
    # 全部 16 个模板可验收后，batch_report 的合并覆盖字段必须是满覆盖
    if len(READY) == len(EXPECTED_TEMPLATES):
        if "coverage_ok" in report:
            assert report["coverage_ok"] is True, (
                f"batch_report.coverage_ok=false，missing={report.get('missing_ids')}")
        if isinstance(report.get("covered_total"), int):
            assert report["covered_total"] >= len(EXPECTED_TEMPLATES), (
                f"batch_report.covered_total={report['covered_total']} < 16")
        if "missing_ids" in report:
            assert not report["missing_ids"], (
                f"batch_report.missing_ids={report['missing_ids']}")
    if not isinstance(entries, list):
        return
    by_id = {str(e.get("id")): e for e in entries if isinstance(e, dict)}
    uncovered = [tid for tid in RENDERED if tid not in by_id]
    if uncovered:
        pytest.skip(f"batch_report 为中间态（覆盖 {len(by_id)}/{len(RENDERED)} 已渲染模板，"
                    f"未含 {uncovered}），等最终批合并后再验")
    problems = []
    for tid in RENDERED:
        entry = by_id.get(tid)
        if entry is None:
            problems.append(f"{tid}: batch_report 无该模板条目")
            continue
        if entry.get("status") not in ("rendered", "skipped"):
            problems.append(f"{tid}: batch status={entry.get('status')!r}")
        if entry.get("ok") is not True:
            problems.append(f"{tid}: batch ok != true")
        if entry.get("qc_ok") is not True:
            problems.append(f"{tid}: batch qc_ok != true")
        if entry.get("deliverable") is False:
            problems.append(f"{tid}: batch deliverable=false（不算交付）")
        if entry.get("previs") is True:
            problems.append(f"{tid}: batch previs=true")
        fallbacks = entry.get("fallbacks")
        if isinstance(fallbacks, (int, float)) and fallbacks > 0:
            problems.append(f"{tid}: batch fallbacks={fallbacks}（验收不接受静态兜底）")
        black = entry.get("black_segments")
        if isinstance(black, (int, float)) and black != 0:
            problems.append(f"{tid}: batch black_segments={black}")
    assert not problems, "batch_report 交付状态有问题：\n" + "\n".join(problems)
