# -*- coding: utf-8 -*-
"""V49 回归：episode / semantic brief 两套 id 空间的时间线解析与 assemble 前置门。

历史缺陷（docs/audit/REVIEW_ROUND3.md P0-2）：
- `templates3d.assemble` 死读 templates_v5 的 episode（78 镜），而 HQ 短片栈按 brief
  渲镜头（89 镜）→ 拿 78 个 id 去 shots/ 找片，把缺失路径喂给 ffmpeg（exit 4294967294）；
- 同一 shot id 在 brief 里重复时，shots/<id>.mp4 互相覆盖、seed 也相同。

本测试不跑 GPU：用 ffmpeg lavfi 生成 3 个 0.4s 的假镜头，monkeypatch 输出根与 brief 路径，
走完整 `templates3d.assemble`，断言能出片；再断言镜头缺失时给出**可读错误**而不是 ffmpeg 报错。
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from pipeline import config, templates3d as t3  # noqa: E402

TID = "zzz_timeline_probe"


def _tiny_clip(path: Path, seconds: float = 0.4) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run([str(config.FFMPEG_BIN), "-y", "-hide_banner", "-loglevel", "error",
                    "-f", "lavfi", "-i", f"color=c=0x203040:s=320x180:r=25:d={seconds}",
                    "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo",
                    "-t", f"{seconds}", "-c:v", "libx264", "-pix_fmt", "yuv420p",
                    "-c:a", "aac", "-ar", "48000", "-ac", "2", str(path)],
                   check=True, capture_output=True, timeout=180)


@pytest.fixture()
def sandbox(tmp_path, monkeypatch):
    """把 templates3d 的输出根指到 tmp，并给一份 3 镜 brief。"""
    out3d = tmp_path / "templates_3d"
    v5out = tmp_path / "templates_v5"
    (out3d / TID / "shots").mkdir(parents=True)
    (v5out / TID / "work").mkdir(parents=True)
    brief = tmp_path / "brief.json"
    shots = [{"id": f"v5_{i:03d}", "kind": "dialogue" if i == 2 else "broll",
              "scene": "late_night_studio", "duration": 0.4,
              "line": "一句台词" if i == 2 else "", "roles": ["host"] if i == 2 else [],
              "prompt": "probe shot"}
             for i in (1, 2, 3)]
    brief.write_text(json.dumps({"template": TID, "mode": "semantic", "shots": shots},
                                ensure_ascii=False), encoding="utf-8")
    # 模板合同：复用 tonight_show 的 style，避免新造合同
    tpl_dir = tmp_path / "templates"
    tpl_dir.mkdir(parents=True)
    base_tpl = json.loads((REPO / "contract/v5/templates/tonight_show.json").read_text(encoding="utf-8"))
    base_tpl["id"] = TID
    base_tpl["label"] = "时间线探针"
    (tpl_dir / f"{TID}.json").write_text(json.dumps(base_tpl, ensure_ascii=False), encoding="utf-8")

    monkeypatch.setattr(t3, "OUT3D", out3d)
    monkeypatch.setattr(t3, "V5_OUT", v5out)
    monkeypatch.setattr(t3, "_brief_path", lambda tid: brief)
    monkeypatch.setattr(t3, "load_template", lambda tid: base_tpl)
    return {"out3d": out3d, "v5out": v5out, "brief": brief}


def test_resolve_prefers_brief_when_only_brief_shots_exist(sandbox):
    for i in (1, 2, 3):
        _tiny_clip(sandbox["out3d"] / TID / "shots" / f"v5_{i:03d}.mp4")
    ep, source, report = t3.resolve_timeline(TID)
    assert source == "semantic_brief", report
    assert report["ratio"] == 1.0
    assert len(ep["shots"]) == 3
    assert ep["shots"][0]["type"] in ("broll", "dialogue")
    assert ep["shots"][1]["line"] == "一句台词"


def test_assemble_from_brief_produces_final(sandbox):
    for i in (1, 2, 3):
        _tiny_clip(sandbox["out3d"] / TID / "shots" / f"v5_{i:03d}.mp4")
    rep = t3.assemble(TID)
    final = sandbox["out3d"] / TID / "final.mp4"
    assert final.is_file() and final.stat().st_size > 10_000, rep
    assert rep["timeline"]["chosen"] == "semantic_brief"
    dur = float((rep.get("probe", {}).get("format", {}) or {}).get("duration") or 0)
    assert dur > 0.6, rep


def test_assemble_fails_readably_when_shots_missing(sandbox):
    _tiny_clip(sandbox["out3d"] / TID / "shots" / "v5_001.mp4")  # 只渲了 1/3
    with pytest.raises(RuntimeError) as exc:
        t3.assemble(TID)
    msg = str(exc.value)
    assert "缺" in msg and "v5_002" in msg, msg
    assert "ffmpeg" not in msg.lower()


def test_episode_json_written_only_after_validation(sandbox):
    """校验失败时不能把半成品时间线写进 work/episode.json。"""
    _tiny_clip(sandbox["out3d"] / TID / "shots" / "v5_001.mp4")
    with pytest.raises(RuntimeError):
        t3.assemble(TID)
    assert not (sandbox["out3d"] / TID / "work" / "episode.json").is_file()
