# -*- coding: utf-8 -*-
"""v3 渲染链路自测（不占 GPU）：

- storyboard_v3.build_plan：30s / 300s 镜头数、时长、字段、type 映射、台词分配；
- qc_v3.evaluate：假 meta（含/缺 prompt_id、静态兜底、previs）断言 ok/失败；
- render_v3.run_render：monkeypatch stage/action_library/h3_comfy 返回假首帧/假 mp4+meta，
  跑通 1 个 30s 模板编排（TTS 用静音兜底替换，不联网不占 GPU）。

运行：pytest pipeline/tests/test_v3_render.py -q
慢测（30s 真实 ffmpeg 编码，约 30-60s）可用 V3_SKIP_SLOW=1 跳过。
"""
from __future__ import annotations

import json
import os
import sys
import types
from pathlib import Path

import pytest

from pipeline import config, qc_v3, render_v3, storyboard_v3, template_v3, utils

REPO = Path(__file__).resolve().parents[2]
TEMPLATE_PATH = REPO / "contract" / "templates_v3" / "tonight_show.json"
CARD_TYPES = ("title_card", "end_card", "meme_card")
H3_WORKFLOWS = {"04_i2v_fl2v_4step_sla", "05_ref2va_4step_sla"}
SHOT_FIELDS = ("id", "no", "type", "duration", "speaker", "speaker_name", "line",
               "visual", "lower_third", "sfx", "shot_plan",
               "role", "v3", "h3_mode", "action")


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------
@pytest.fixture(scope="module")
def template() -> dict:
    if not TEMPLATE_PATH.is_file():
        pytest.skip(f"缺少参考模板 {TEMPLATE_PATH}")
    return template_v3.load_file(TEMPLATE_PATH)


def _ffmpeg(args):
    utils.ffmpeg(args)


def make_color_video(path: Path, *, duration: float = 2.0, width: int = 1280,
                     height: int = 720, fps: int = 25, black: bool = False,
                     audio: bool = True) -> Path:
    """生成测试用成片（默认 testsrc2 + 正弦音轨，blackdetect=0）。"""
    utils.ensure_dir(Path(path).parent)
    src = "color=c=black:s={}x{}:r={}".format(width, height, fps) if black else \
        "testsrc2=size={}x{}:rate={}".format(width, height, fps)
    args = ["-f", "lavfi", "-i", src]
    if audio:
        args += ["-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000"]
    args += ["-t", f"{duration:.3f}", "-c:v", "libx264", "-preset", "ultrafast",
             "-pix_fmt", "yuv420p"]
    args += ["-c:a", "aac", "-shortest", "-y", str(path)]
    _ffmpeg(args)
    return Path(path)


def make_png(path: Path, *, size=(1024, 576), color=(40, 60, 90)) -> Path:
    from PIL import Image
    utils.ensure_dir(Path(path).parent)
    Image.new("RGB", size, color).save(path)
    return Path(path)


def make_plan(duration: float = 2.0, *, line: str = "你好", with_card: bool = False):
    shots = [{
        "id": "v3_s001", "no": 1, "type": "host_to_camera",
        "duration": duration, "line": line, "speaker": "laozhou",
        "speaker_name": "老周", "role": "host", "h3_mode": "audio_ref",
    }]
    if with_card:
        shots.insert(0, {
            "id": "v3_card_title_card", "no": 0, "type": "title_card",
            "duration": 2.0, "static_card": True, "line": "",
        })
        for i, shot in enumerate(shots, start=1):
            shot["no"] = i
    episode = {"resolution": "1280x720", "fps": 25, "total_duration": duration}
    return {"episode": episode, "shots": shots}


def make_meta(shot: dict, workdir: Path, *, prompt_id: str = "pid-1",
              workflow: str = "05_ref2va_4step_sla", h3: bool = True,
              fallback: bool = False, previs: bool = False) -> dict:
    ref = make_png(workdir / f"{shot['id']}_ref.png")
    clip = workdir / f"{shot['id']}.mp4"
    if not clip.is_file():
        make_color_video(clip, duration=float(shot.get("duration") or 2.0))
    meta = {
        "id": shot["id"], "h3": h3, "mode": "audio_ref" if h3 else "static",
        "workflow": workflow if h3 else "", "prompt_id": prompt_id if h3 else "",
        "seed": 20261026 if h3 else None, "duration": shot.get("duration"),
        "ref_image": str(ref), "path": str(clip),
        "action": "walk_in_desk" if h3 else None,
    }
    if fallback:
        meta["fallback"] = True
        meta["fallback_reason"] = "fake H3 error"
    if previs:
        meta["previs"] = True
    return meta


SUBTITLES = [{"start": 0.0, "end": 2.0, "style": "Sub", "text": "你好"}]


# ---------------------------------------------------------------------------
# storyboard_v3
# ---------------------------------------------------------------------------
def test_storyboard_30s(template):
    plan = storyboard_v3.build_plan(template, 30)
    shots = plan["shots"]
    cards = [s for s in shots if s["type"] in CARD_TYPES]
    bodies = [s for s in shots if s["type"] not in CARD_TYPES]
    total = storyboard_v3.plan_duration(plan)

    assert len(cards) == 2
    assert [c["type"] for c in cards] == ["title_card", "end_card"]
    assert len(bodies) == 5
    assert total == pytest.approx(30.35, abs=0.01)
    assert plan["episode"]["total_duration"] == pytest.approx(30.35, abs=0.01)
    assert plan["episode"]["resolution"] == "1280x720"
    assert plan["episode"]["fps"] == 25
    assert plan["episode"]["aspect"] == "16:9"
    assert plan["episode"]["format_id"] == template["id"]
    assert set(plan["cards"]) == {"title", "end"}
    assert plan["meta"]["h3_shot_count"] == 5
    assert plan["meta"]["card_count"] == 2

    asset_ids = {a["id"] for a in plan["assets"]}
    roles = {a["role"] for a in plan["assets"]}
    for shot in shots:
        for field in SHOT_FIELDS:
            assert field in shot, f"{shot['id']} 缺字段 {field}"
        assert shot["duration"] > 0
        assert shot["shot_plan"]["type"] == shot["type"]
        if shot["type"] in CARD_TYPES:
            assert shot["static_card"] is True
            assert shot["h3_mode"] == "static"
            continue
        assert shot["duration"] == pytest.approx(storyboard_v3.H3_SHOT_DURATION)
        assert shot["h3_mode"] in ("audio_ref", "fl2v")
        if shot["h3_mode"] == "audio_ref":
            assert shot["line"], "audio_ref 必须有台词（H3 音驱动）"
            assert shot["type"] in ("host_to_camera", "shot_reverse")
            assert shot["type"] == ("host_to_camera" if shot["role"] == "host"
                                    else "shot_reverse")
            assert shot["speaker"] in asset_ids
            assert shot["role"] in roles
        else:
            assert shot["type"] in ("b_roll", "reaction")
    assert any(s["h3_mode"] == "fl2v" for s in bodies) is False
    # Lead 实测决策：非卡片镜统一 audio_ref（05_ref2va），不再用纸片感 fl2v
    assert all(s["h3_mode"] == "audio_ref" for s in bodies)
    dialogue = [s for s in bodies if str(s.get("line") or "").strip()]
    assert len(dialogue) == len(bodies), "demo 全部正文镜都应有台词"
    assert plan["meta"]["fl2v_with_line"] == []
    assert plan["meta"]["h3_mode_policy"] == "audio_ref"
    # demo 首尾保护：首镜=台本首句（长句按标点切分）、末镜=台本最后 1 句
    script = storyboard_v3.load_script(template["id"])
    all_lines = storyboard_v3.script_lines(script)
    assert all_lines[0]["text"].startswith(dialogue[0]["line"])
    assert all_lines[-1]["text"].endswith(dialogue[-1]["line"])
    assert plan["meta"]["opening_line_protected"] is True
    assert plan["meta"]["ending_line_protected"] is True
    # 中间镜按台本顺序取（前一句的首段），demo 内容连贯
    for i in range(1, len(dialogue) - 1):
        assert all_lines[i]["text"].startswith(dialogue[i]["line"])
    # 语速适配：每镜台词估算 TTS 时长必须能压进 5.17s 镜头（不裁切台词）
    for shot in dialogue:
        est = storyboard_v3.estimate_tts_sec(shot["line"])
        assert est <= storyboard_v3.DEMO_LINE_EST_MAX + 0.01, \
            f"{shot['id']} 台词过长会被裁切: est={est}s"
    assert plan["meta"]["max_dialogue_tts_est_sec"] <= storyboard_v3.DEMO_LINE_EST_MAX
    # 说话人在整条计划里落位一致（中文名 → cast 角色/资产）
    speakers = {}
    for shot in dialogue:
        speakers.setdefault(shot["speaker_name"], set()).add(shot["role"])
    assert all(len(roles) == 1 for roles in speakers.values())
    assert plan["meta"]["line_source"] == "llm_scripts"
    assert plan["meta"]["dialogue_coverage"] == 1.0


def test_storyboard_all_templates_cast_speaker_mapping():
    """16 模板：台本说话人 100% 落到 cast（hot_ones/slow_life 只有 2 人也不报错），
    非卡片镜统一 audio_ref、demo 首尾句保护、说话人落位一致。"""
    templates = template_v3.load_all()
    assert len(templates) >= 16, f"应有 16 个模板，实际 {len(templates)}"
    for tid, tpl in sorted(templates.items()):
        plan = storyboard_v3.build_plan(tpl, 30)
        asset_ids = {a["id"] for a in plan["assets"]}
        roles = {a["role"] for a in plan["assets"]}
        bodies = [s for s in plan["shots"] if s["type"] not in CARD_TYPES]
        dialogue = [s for s in bodies if str(s.get("line") or "").strip()]
        assert dialogue, f"{tid}: 30s 应分配到台词"
        assert all(s["h3_mode"] == "audio_ref" for s in bodies), f"{tid}: 应统一 audio_ref"
        for shot in dialogue:
            assert shot["speaker"] in asset_ids, f"{tid}: speaker {shot['speaker']} 不在 cast"
            assert shot["role"] in roles, f"{tid}: role {shot['role']} 不在 cast"
        assert plan["meta"]["fl2v_with_line"] == [], f"{tid}: 不应有 fl2v 台词"
        script = storyboard_v3.load_script(tid)
        lines = storyboard_v3.script_lines(script)
        if lines:
            assert lines[0]["text"].startswith(dialogue[0]["line"]), f"{tid}: 首句未保护"
            assert lines[-1]["text"].endswith(dialogue[-1]["line"]), f"{tid}: 末句未保护"
            # 语速适配：demo 台词都要能压进 5.17s 镜（不裁切）
            for shot in dialogue:
                est = storyboard_v3.estimate_tts_sec(shot["line"])
                assert est <= storyboard_v3.DEMO_LINE_EST_MAX + 0.01, \
                    f"{tid}: {shot['id']} 台词过长 est={est}s"
        # 同一说话人全片落位一致
        by_name: dict[str, set] = {}
        for shot in dialogue:
            by_name.setdefault(shot["speaker_name"], set()).add(shot["role"])
        assert all(len(v) == 1 for v in by_name.values()), f"{tid}: 说话人落位不一致"
        # host role 兼容 host_detective 等变体：老周口播镜必须是 host_to_camera
        for shot in dialogue:
            if shot["speaker_name"] == "老周" and shot["role"].startswith("host"):
                assert shot["type"] == "host_to_camera", f"{tid}: host 镜 type 错"


def test_storyboard_300s(template):
    plan = storyboard_v3.build_plan(template, storyboard_v3.DEFAULT_TARGET_DURATION
                                    * 10)  # 300
    bodies = [s for s in plan["shots"] if s["type"] not in CARD_TYPES]
    assert len(bodies) == 57
    assert storyboard_v3.plan_duration(plan) == pytest.approx(299.19, abs=0.01)
    for shot in plan["shots"]:
        assert shot["id"].startswith("v3_")
        assert shot["shot_plan"]["size"]
        assert shot.get("v3") and shot["v3"].get("intent")
        if shot["type"] not in CARD_TYPES:
            # 5 分钟版：台词铺开 + 无台词镜仍走 audio_ref 静音参考
            assert shot["h3_mode"] == "audio_ref"
            assert shot["action"]
    # 300s 时台词应铺开（21 条台本 → 均匀分布在不同镜头），其余为静音 audio_ref 镜
    dialogue = [s for s in bodies if str(s.get("line") or "").strip()]
    assert 10 <= len(dialogue) <= len(bodies)
    assert len(dialogue) < len(bodies), "300s 应有无台词（静音参考）镜"
    assert plan["meta"]["silent_shots"] == len(bodies) - len(dialogue)
    assert len({s["segment_id"] for s in bodies}) >= 2


def test_storyboard_no_cards_and_placeholder(template):
    plan = storyboard_v3.build_plan(template, 20, include_cards=False)
    bodies = [s for s in plan["shots"] if s["type"] not in CARD_TYPES]
    assert len(bodies) == storyboard_v3.shot_count_for(20, include_cards=False)
    assert storyboard_v3.plan_duration(plan) == pytest.approx(
        len(bodies) * storyboard_v3.H3_SHOT_DURATION, abs=0.01)
    # 空台本 → beats 占位台词，不会抛异常
    plan2 = storyboard_v3.build_plan(template, 30, script={"segments": []})
    assert plan2["meta"]["line_source"] == "beats_placeholder"


def test_black_frame_guard(tmp_path):
    """卡片/首帧纯黑会被 blackdetect 硬门抓到，渲染侧用黑帧占比提前识别。"""
    black = make_png(tmp_path / "black.png", size=(64, 36), color=(0, 0, 0))
    dark = make_png(tmp_path / "dark.png", size=(64, 36), color=(8, 8, 8))
    bright = make_png(tmp_path / "bright.png", size=(64, 36), color=(64, 52, 36))
    assert render_v3.is_black_frame(black) is True
    assert render_v3.is_black_frame(dark) is True
    assert render_v3.is_black_frame(bright) is False
    assert 0.0 <= render_v3.frame_black_fraction(bright) < 1.0


def test_render_lock_prevents_concurrent(tmp_path):
    """同一输出目录禁止并发 render_v3（防止两个进程互相覆盖 final.mp4）。"""
    workdir = tmp_path / "work"
    lock = render_v3._acquire_lock(workdir, "tonight_show")
    assert lock.is_file()
    with pytest.raises(render_v3.RenderV3Error):
        render_v3._acquire_lock(workdir, "tonight_show")
    render_v3._release_lock(lock)
    assert not lock.is_file()
    lock2 = render_v3._acquire_lock(workdir, "tonight_show")
    render_v3._release_lock(lock2)


def _load_batch_module():
    import importlib.util
    path = REPO / "scripts" / "render_templates_v3.py"
    spec = importlib.util.spec_from_file_location("render_templates_v3", path)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


def test_batch_resume_requires_delivery(tmp_path):
    """批量断点续跑：comfy 只认交付态（非 previs / deliverable / 无静态兜底）。"""
    mod = _load_batch_module()
    out = tmp_path / "tonight_show"
    utils.ensure_dir(out / "frames")
    (out / "final.mp4").write_bytes(b"0" * 20000)
    report = out / "qc_report.json"

    utils.write_json(report, {"ok": True, "previs": False,
                              "deliverable": True, "fallbacks": []})
    assert mod.completed(out, require_delivery=True) is True
    assert mod.entry_bad({"status": "rendered", "ok": True,
                          "deliverable": True, "fallbacks": 0}, True) is False

    utils.write_json(report, {"ok": True, "previs": True,
                              "deliverable": False, "fallbacks": []})
    assert mod.completed(out, require_delivery=True) is False, "previs 不能挡住 comfy 重跑"
    assert mod.completed(out, require_delivery=False) is True, "previs 批量可续跑"
    assert mod.entry_bad({"status": "rendered", "ok": True,
                          "deliverable": False, "fallbacks": 0}, True) is True

    utils.write_json(report, {"ok": True, "previs": False,
                              "deliverable": False,
                              "fallbacks": [{"id": "v3_s001"}]})
    assert mod.completed(out, require_delivery=True) is False, "静态兜底不能算完成"
    assert mod.entry_bad({"status": "rendered", "ok": True,
                          "deliverable": True, "fallbacks": 1}, True) is True
    assert mod.entry_bad({"status": "failed", "ok": False}, True) is True

    utils.write_json(report, {"ok": False, "previs": False,
                              "deliverable": False, "fallbacks": []})
    assert mod.completed(out, require_delivery=True) is False


def test_storyboard_h3_mode_normalization():
    assert storyboard_v3.normalize_h3_mode("audio_ref", "有台词") == "audio_ref"
    assert storyboard_v3.normalize_h3_mode("audio_ref", "") == "fl2v"
    assert storyboard_v3.normalize_h3_mode("fl2v", "有台词") == "fl2v"
    assert storyboard_v3.normalize_h3_mode("i2v", "") == "fl2v"
    assert storyboard_v3.shot_count_for(30) == 5
    assert storyboard_v3.shot_count_for(40) == 7
    assert storyboard_v3.shot_count_for(300) == 57


# ---------------------------------------------------------------------------
# qc_v3
# ---------------------------------------------------------------------------
def test_qc_ok(tmp_path):
    video = make_color_video(tmp_path / "final.mp4", duration=2.0)
    plan = make_plan(2.0)
    meta = make_meta(plan["shots"][0], tmp_path)
    report = qc_v3.evaluate(video=video, plan=plan,
                            shot_metas={meta["id"]: meta},
                            template={"id": "tonight_show"},
                            subtitles=SUBTITLES, h3_require=True)
    failed = {k: v for k, v in report["checks"].items() if not v["ok"]}
    assert report["ok"] is True, f"QC 应全绿，失败项: {failed}"
    assert report["fallbacks"] == []
    assert report["stats"]["h3_coverage"] == 1.0
    assert report["stats"]["subtitle_coverage"] == 1.0
    assert report["stats"]["black_segments"] == 0
    assert report["deliverable"] is True
    for name in ("duration", "resolution", "fps", "audio", "blackdetect",
                 "subtitle_coverage", "h3_coverage", "traceability",
                 "static_marked", "stage_meta"):
        assert name in report["checks"]


def test_qc_missing_prompt_id(tmp_path):
    video = make_color_video(tmp_path / "final.mp4", duration=2.0)
    plan = make_plan(2.0)
    meta = make_meta(plan["shots"][0], tmp_path, prompt_id="")
    report = qc_v3.evaluate(video=video, plan=plan,
                            shot_metas={meta["id"]: meta},
                            template={"id": "tonight_show"}, subtitles=SUBTITLES)
    assert report["ok"] is False
    assert report["checks"]["traceability"]["ok"] is False
    assert report["checks"]["traceability"]["prompt_id_missing"] == [meta["id"]]
    assert report["deliverable"] is False


def test_qc_duplicated_prompt_id(tmp_path):
    video = make_color_video(tmp_path / "final.mp4", duration=4.0)
    plan = make_plan(2.0)
    second = dict(plan["shots"][0], id="v3_s002", no=2)
    plan["shots"].append(second)
    meta1 = make_meta(plan["shots"][0], tmp_path, prompt_id="dup")
    meta2 = make_meta(second, tmp_path, prompt_id="dup")
    report = qc_v3.evaluate(video=video, plan=plan,
                            shot_metas={meta1["id"]: meta1, meta2["id"]: meta2},
                            template={"id": "tonight_show"},
                            subtitles=[{"start": 0, "end": 4, "style": "Sub",
                                        "text": "你好"}])
    assert report["ok"] is False
    assert report["checks"]["traceability"]["prompt_id_duplicated"] == ["dup"]


def test_qc_static_fallback_marked(tmp_path):
    video = make_color_video(tmp_path / "final.mp4", duration=2.0)
    plan = make_plan(2.0)
    sid = plan["shots"][0]["id"]
    meta = make_meta(plan["shots"][0], tmp_path, h3=False, fallback=True)
    report = qc_v3.evaluate(video=video, plan=plan, shot_metas={sid: meta},
                            template={"id": "tonight_show"}, subtitles=SUBTITLES)
    assert report["ok"] is False
    assert report["checks"]["h3_coverage"]["ok"] is False
    assert report["checks"]["static_marked"]["ok"] is True  # 已显式标记
    assert [f["id"] for f in report["fallbacks"]] == [sid]
    assert report["deliverable"] is False


def test_qc_static_unmarked_fails(tmp_path):
    video = make_color_video(tmp_path / "final.mp4", duration=2.0)
    plan = make_plan(2.0)
    sid = plan["shots"][0]["id"]
    meta = make_meta(plan["shots"][0], tmp_path, h3=False)
    report = qc_v3.evaluate(video=video, plan=plan, shot_metas={sid: meta},
                            template={"id": "tonight_show"}, subtitles=SUBTITLES)
    assert report["ok"] is False
    assert report["checks"]["static_marked"]["ok"] is False
    assert report["checks"]["static_marked"]["unmarked"] == [sid]


def test_qc_previs_is_not_deliverable(tmp_path):
    video = make_color_video(tmp_path / "final.mp4", duration=2.0)
    plan = make_plan(2.0)
    sid = plan["shots"][0]["id"]
    meta = make_meta(plan["shots"][0], tmp_path, h3=False, previs=True)
    report = qc_v3.evaluate(video=video, plan=plan, shot_metas={sid: meta},
                            template={"id": "tonight_show"}, subtitles=SUBTITLES,
                            h3_require=False)
    assert report["previs"] is True
    assert report["ok"] is True
    assert report["deliverable"] is False
    assert report["checks"]["h3_coverage"]["skipped"] is True


def test_qc_wrong_resolution_and_black(tmp_path):
    video = make_color_video(tmp_path / "final.mp4", duration=2.0, black=True,
                             width=640, height=360)
    plan = make_plan(2.0)
    meta = make_meta(plan["shots"][0], tmp_path)
    report = qc_v3.evaluate(video=video, plan=plan,
                            shot_metas={meta["id"]: meta},
                            template={"id": "tonight_show"}, subtitles=SUBTITLES)
    assert report["checks"]["resolution"]["ok"] is False
    assert report["checks"]["blackdetect"]["ok"] is False
    assert report["ok"] is False


# ---------------------------------------------------------------------------
# render_v3 编排（假 stage / action_library / h3_comfy，静音 TTS，不占 GPU）
# ---------------------------------------------------------------------------
def _install_fake_modules(monkeypatch, tmp_path: Path):
    """把 stage/action_library/h3_comfy 替换为确定性假实现。"""
    from pipeline import stage as real_stage
    from pipeline import h3_comfy as real_h3
    from pipeline import tts_engine

    calls = {"first": [], "last": [], "card": [], "generate": []}

    def fake_first(template, shot, out_png, *, width=1024, height=576,
                   workdir=None, index=0):
        make_png(Path(out_png), size=(width, height),
                 color=(30 + index * 7 % 200, 60, 90))
        calls["first"].append(shot["id"])
        return {"path": str(out_png), "width": width, "height": height,
                "plate": "fake_plate", "camera_zone": "front",
                "characters": [], "props": [], "h3_input": True,
                "clean": shot.get("h3_mode") == "fl2v", "style": "fake"}

    def fake_last(template, shot, first_meta, out_png, *, width=None, height=None):
        make_png(Path(out_png), size=(width or 1024, height or 576),
                 color=(90, 80, 40))
        calls["last"].append(shot["id"])
        return {"path": str(out_png), "width": width, "height": height,
                "motion": "push-in"}

    def fake_card(template, shot, out_png, *, width=1024, height=576):
        # 非纯黑卡片（真实 stage 卡片是 plate 底图；纯黑会命中 blackdetect 硬门）
        make_png(Path(out_png), size=(width, height), color=(64, 52, 36))
        calls["card"].append(shot["id"])
        return {"path": str(out_png), "width": width, "height": height,
                "card": True}

    def fake_generate(shot, first_image, out_mp4, *, mode, prompt, duration,
                      audio_wav=None, last_image=None, seed=None, width=1024,
                      height=576, fps=24, workdir=None, timeout=1800):
        out_mp4 = Path(out_mp4)
        calls["generate"].append({"id": shot.get("id"), "mode": mode,
                                  "seed": seed, "prompt": prompt,
                                  "audio_wav": str(audio_wav or ""),
                                  "last_image": str(last_image or "")})
        render_v3.make_static_clip(first_image, out_mp4, duration, width, height,
                                   fps=fps)
        meta = {
            "h3": True, "ok": True, "mode": mode,
            "workflow": ("05_ref2va_4step_sla" if mode == "audio_ref"
                         else "04_i2v_fl2v_4step_sla"),
            "prompt_id": f"pid-{shot.get('id')}-{len(calls['generate'])}",
            "seed": seed, "duration": duration, "length": 124,
            "width": width, "height": height, "fps": fps,
            "ref_image": str(first_image),
            "last_image": str(last_image or ""),
            "audio": str(audio_wav or ""),
            "action": shot.get("action"),
            "gen_sec": 0.1, "path": str(out_mp4),
            "created_at": "2026-01-01T00:00:00",
        }
        utils.ensure_dir(out_mp4.parent)
        utils.write_json(out_mp4.with_suffix(".meta.json"), meta)
        return meta

    def fake_normalize(src, dst, *, duration, width=1280, height=720, fps=25):
        return render_v3.normalize_clip_fallback(
            src, dst, duration=duration, width=width, height=height, fps=fps)

    def fake_synth(text, voice, out_mp3, out_vtt=None, rate=None, retries=None):
        out = Path(out_mp3)
        tts_engine.make_silence_mp3(out, 1.2)
        return {"ok": True, "path": str(out), "vtt": "", "duration": 1.2,
                "voice": voice, "chars": len(text), "words": 0}

    monkeypatch.setattr(real_stage, "compose_first_frame", fake_first, raising=False)
    monkeypatch.setattr(real_stage, "compose_last_frame", fake_last, raising=False)
    monkeypatch.setattr(real_stage, "compose_card_frame", fake_card, raising=False)
    monkeypatch.setattr(real_h3, "generate", fake_generate, raising=False)
    monkeypatch.setattr(real_h3, "normalize_clip", fake_normalize, raising=False)
    monkeypatch.setattr(real_h3, "meta_ok",
                        lambda meta: bool(meta.get("h3") and meta.get("prompt_id")
                                          and meta.get("workflow")), raising=False)
    monkeypatch.setattr(tts_engine, "synth_with_retry", fake_synth)

    fake_action = types.ModuleType("pipeline.action_library")

    def build_h3_prompt(template, shot, *, mode, style_prefix=None):
        return f"[fake {mode}] {shot.get('action')} / {shot.get('line')[:12]}"

    fake_action.build_h3_prompt = build_h3_prompt
    fake_action.resolve_action = lambda template, action_id: {"id": action_id,
                                                              "h3_prompt": "fake"}
    fake_action.camera_phrase = lambda movement, size=None: str(movement)
    monkeypatch.setitem(sys.modules, "pipeline.action_library", fake_action)
    return calls


def _namespace(tmp_path: Path, **overrides):
    import argparse
    base = dict(template="tonight_show", duration=30, h3="comfy",
                out=str(tmp_path / "final.mp4"),
                workdir=str(tmp_path / "work"),
                seed=20261026, force=True, limit=None,
                fallback_static=False, width=1280, height=720)
    base.update(overrides)
    return argparse.Namespace(**base)


@pytest.mark.skipif(os.environ.get("V3_SKIP_SLOW") == "1",
                    reason="V3_SKIP_SLOW=1 跳过 30s 完整编排测试")
def test_render_v3_orchestration_mock_h3(monkeypatch, tmp_path):
    """monkeypatch h3_comfy.generate 返回假 mp4+meta，跑通 30s 模板编排 + QC。"""
    calls = _install_fake_modules(monkeypatch, tmp_path)
    rc = render_v3.run_render(_namespace(tmp_path))
    out_dir = tmp_path
    final = out_dir / "final.mp4"
    qc_report = utils.read_json(out_dir / "qc_report.json") or {}
    h3_meta = utils.read_json(out_dir / "h3_meta.json") or {}

    assert final.is_file(), "应产出 final.mp4"
    assert rc == 0, f"QC 应通过: {qc_report.get('checks')}"
    probe = utils.probe_video(final)
    assert (probe["width"], probe["height"]) == (1280, 720)
    assert abs(probe["fps"] - 25) < 0.6
    assert probe["has_audio"] is True
    assert abs(probe["duration"] - 30.35) <= 0.6
    assert qc_report["ok"] is True
    assert qc_report["stats"]["black_segments"] == 0
    assert qc_report["fallbacks"] == []

    bodies = [m for m in h3_meta["shots"] if not m.get("static_card")]
    assert len(bodies) == 5
    assert len(calls["generate"]) == 5
    assert all(m.get("h3") is True for m in bodies)
    prompt_ids = [m["prompt_id"] for m in bodies]
    assert len(set(prompt_ids)) == len(prompt_ids)
    assert all(m["workflow"] in H3_WORKFLOWS for m in bodies)
    assert all(Path(m["ref_image"]).is_file() for m in bodies)
    audio_ref = [c for c in calls["generate"] if c["mode"] == "audio_ref"]
    assert len(audio_ref) == 5, "非卡片镜应全部走 audio_ref（05_ref2va）"
    assert all(c["audio_wav"] for c in audio_ref), \
        "30s demo 每镜都有台词 → 都应带该镜 TTS wav"
    # 每镜成片段 + 抽帧 + contact sheet 落盘
    assert len(list((out_dir / "shots").glob("*.mp4"))) == 7
    assert (out_dir / "frames" / "contact_sheet.png").is_file()
    assert (tmp_path / "work" / "episode.json").is_file()
    assert (tmp_path / "work" / "manifest.json").is_file()

    # 每镜首帧由 stage.compose_first_frame 产出（卡片走 compose_card_frame）
    assert len(calls["first"]) == 5
    assert len(calls["card"]) == 2


def test_render_v3_previs_runs(monkeypatch, tmp_path):
    """previs 模式不调用 H3（QC 标 previs / 不算交付）。"""
    calls = _install_fake_modules(monkeypatch, tmp_path)
    rc = render_v3.run_render(_namespace(tmp_path, h3="previs", duration=30,
                                         limit=3))
    assert (tmp_path / "final.mp4").is_file()
    report = utils.read_json(tmp_path / "qc_report.json") or {}
    assert report.get("previs") is True
    assert report.get("deliverable") is False
    assert rc == 0
    assert calls["generate"] == [], "previs 不允许调用 H3"
