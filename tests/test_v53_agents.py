# -*- coding: utf-8 -*-
"""V53：智能体工具与合同守卫的回归测试（不跑 GPU、不写仓库数据）。

覆盖：工作流损坏拒绝执行 / check_story / muse 越栈跳过 / muse_video 未接跳过 /
短片 QC 缺成片 / deliver 缺片 / utils 的 BOM-GBK 与 NaN / 季 warnings 字段。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from pipeline import utils, v5_catalog  # noqa: E402
from pipeline.agents import registry, workflow  # noqa: E402
from pipeline.agents.orchestrator import run_template  # noqa: E402


class _Ctx:
    def __init__(self, tid: str, run_dir: Path, force: bool = False):
        cat = v5_catalog.load_catalog()
        self.template_id = tid
        self.template = cat["templates"][tid]
        self.duration = float(self.template.get("target_duration") or 12)
        self.force = force
        self.run_dir = run_dir
        self.paths = {}
        self.logs: list[str] = []
        # 与真 StageContext（pipeline/agents/registry.py::StageContext）字段对齐：
        # 早期夹具漏了 seed，导致 muse_video 一读 ctx.seed 就 AttributeError，
        # 把「未接 provider 应跳过」的用例变成假红。
        self.seed = 20261101

    def log(self, msg: str) -> None:
        self.logs.append(str(msg))


def test_broken_workflow_is_degraded_and_refused(tmp_path, monkeypatch):
    # 不写真实 contract 目录：把 WORKFLOW_DIR 指到 tmp
    monkeypatch.setattr(workflow, "WORKFLOW_DIR", tmp_path)
    path = tmp_path / "zzz_broken_test.json"
    path.write_text("{not json", encoding="utf-8")
    wf = workflow.load_workflow("zzz_broken_test", {})
    assert wf.get("degraded") is True
    assert "解析失败" in str(wf.get("degraded_reason"))
    with pytest.raises(RuntimeError):
        run_template("zzz_broken_test", dry_run=True)


def test_check_story_short_template(tmp_path):
    ctx = _Ctx("custom_tpl_v46_duologue", tmp_path)
    res = registry.check_story(ctx, {})
    assert res["acts"] >= 4
    assert res["dialogues"] >= 2
    assert res["brief_shots"] >= 4
    assert (tmp_path / "episode.json").is_file()


def test_muse_skips_v5_template(tmp_path):
    ctx = _Ctx("tonight_show", tmp_path)
    res = registry.muse_lipsync(ctx, {"limit": 1})
    assert res.get("skipped") is True
    assert "HQ" in str(res.get("reason"))


def test_muse_video_skips_without_provider(tmp_path, monkeypatch):
    for name in ("MUSE_API_URL", "REPLICATE_API_TOKEN", "USEAPI_TOKEN"):
        monkeypatch.delenv(name, raising=False)
    ctx = _Ctx("custom_tpl_v46_cyber", tmp_path)
    res = registry.muse_video(ctx, {})
    assert res.get("skipped") is True


def test_evaluate_hq_qc_requires_final(tmp_path, monkeypatch):
    monkeypatch.setattr(registry, "_outputs_root", lambda: tmp_path)
    ctx = _Ctx("custom_tpl_v46_cyber", tmp_path)
    with pytest.raises(RuntimeError):
        registry.evaluate_hq_qc(ctx, {})


def test_deliver_without_final(tmp_path, monkeypatch):
    from pipeline import deliver

    monkeypatch.setattr(deliver, "ROOTS", (tmp_path,))
    res = deliver.deliver("nope", hd=True)
    assert res["ok"] is False


def test_utils_read_json_bom_and_gbk(tmp_path):
    p = tmp_path / "bom.json"
    p.write_text(json.dumps({"a": "中文"}, ensure_ascii=False), encoding="utf-8-sig")
    assert utils.read_json(p) == {"a": "中文"}
    p.write_text(json.dumps({"a": "中文"}, ensure_ascii=False), encoding="gbk")
    assert utils.read_json(p) == {"a": "中文"}


def test_utils_write_json_sanitizes_nan(tmp_path):
    p = tmp_path / "nan.json"
    utils.write_json(p, {"x": float("nan"), "y": [float("inf"), 1.5]})
    raw = p.read_text(encoding="utf-8")
    assert "NaN" not in raw and "Infinity" not in raw
    assert json.loads(raw) == {"x": None, "y": [None, 1.5]}


def test_series_exposes_season_warnings(client):
    resp = client.get("/api/v6/agent/series")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert isinstance(body.get("series"), list)
    for item in body["series"]:
        assert "season_warnings" in item
        assert isinstance(item["season_warnings"], list)
        # 当前合同已按真实 cast 修过，不应再有 warning
        assert item["season_warnings"] == [], item


def test_season_checker_script_runs():
    import subprocess

    proc = subprocess.run([sys.executable, "scripts/check_seasons.py", "--json"],
                          cwd=str(REPO_ROOT), capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=180)
    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout)  # --json 是缩进输出，不是单行
    assert "series" in payload