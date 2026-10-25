# -*- coding: utf-8 -*-
"""scripts/verify_h3_evidence.py 单测：用假 HTTP server 模拟 ComfyUI /history。

覆盖：正常全绿、prompt_id 在 history 缺失、status error、seed 不一致、
outputs 无视频、workflow/mode 不对应、history 节点反证不对应、prompt_id 重复、
目录输入、单镜 meta 输入、服务不可用、卡片/previs-only。
不需要 GPU / 真 ComfyUI。
"""
from __future__ import annotations

import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
SCRIPTS_DIR = REPO_ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from pipeline import utils  # noqa: E402
import verify_h3_evidence as ve  # noqa: E402

WORKFLOWS = ve.WORKFLOWS
STATS = {"system": {"comfyui_version": "0.42.0", "os": "win32"},
         "devices": [{"name": "cuda:0"}]}


# --------------------------------------------------------------------------- #
# 假 ComfyUI
# --------------------------------------------------------------------------- #
class _HistoryHandler(BaseHTTPRequestHandler):
    routes: dict = {}

    def do_GET(self):  # noqa: N802
        body = self.routes.get(self.path)
        if body is None:  # 真实 ComfyUI 对未知 prompt_id 返回 {}
            body = {}
        raw = json.dumps(body).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def log_message(self, *args):  # noqa: D102
        pass


def _serve(payloads: dict):
    routes = {"/system_stats": STATS, **payloads}
    handler = type("Handler", (_HistoryHandler,), {"routes": routes})
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    return httpd, f"http://127.0.0.1:{httpd.server_address[1]}"


def _api(mode: str = "audio_ref", seed: int = 20261043) -> dict:
    node = ("MiniMaxH3ReferenceToVideo" if mode == "audio_ref"
            else "MiniMaxH3ImageToVideo")
    return {
        "1": {"class_type": "UNETLoader", "inputs": {}},
        "7": {"class_type": node, "inputs": {}},
        "9": {"class_type": "RandomNoise", "inputs": {"noise_seed": seed}},
        "16": {"class_type": "SaveVideo", "inputs": {}},
    }


def _history(pid: str, *, mode: str = "audio_ref", seed: int = 20261043,
             status: str = "success", outputs: str = "video") -> dict:
    if outputs == "video":
        outs: dict = {"16": {"images": [
            {"filename": f"{pid}_00001_.mp4", "subfolder": "zaopianju_dynamic",
             "type": "output", "metadata": {"kind": "video", "fps": 24.0}},
        ], "animated": [True]}}
    elif outputs == "image":
        outs = {"18": {"images": [{"filename": "frame.png", "subfolder": "",
                                   "type": "output"}]}}
    else:
        outs = {}
    return {
        "prompt": [7, pid, _api(mode, seed), {"create_time": 1790000000.0}, []],
        "outputs": outs,
        "status": {"status_str": status, "completed": status == "success",
                   "messages": ([] if status == "success" else
                                [["execution_error", {"exception_message": "boom"}]])},
        "meta": {},
    }


def _hroute(pid: str, **kwargs) -> dict:
    """真实 ComfyUI /history/{pid} 返回 {prompt_id: record}。"""
    return {pid: _history(pid, **kwargs)}


def _meta(pid: str = "p1", *, sid: str = "s001", mode: str = "audio_ref",
          workflow: str | None = None, seed: int | None = 20261043,
          h3: bool = True, **extra) -> dict:
    meta = {
        "id": sid, "type": "host_to_camera", "h3": h3,
        "mode": mode, "workflow": workflow if workflow is not None
        else WORKFLOWS.get(mode, ""),
        "prompt_id": pid, "seed": seed, "duration": 5.17,
        "action": "host_to_camera", "ref_image": "/tmp/first.png",
    }
    meta.update(extra)
    return meta


def _write_meta_json(path: Path, shots: list[dict], **doc_extra) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    doc = {"template": "tonight_show", "h3_mode": "comfy", "shots_count": len(shots),
           "shots": shots, **doc_extra}
    path.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    return path


def _last_report(capsys) -> dict:
    out = capsys.readouterr().out.strip().splitlines()
    assert out, "脚本必须输出报告 JSON"
    return json.loads(out[-1])


def _run(argv: list[str]) -> tuple[int, dict]:
    # capsys 不能在普通 helper 里取，返回 nc 由调用方读；这里只执行
    rc = ve.main(argv)
    return rc, {}


# --------------------------------------------------------------------------- #
# 正常路径
# --------------------------------------------------------------------------- #
def test_verify_ok_with_json_out(tmp_path, capsys):
    meta_path = _write_meta_json(tmp_path / "h3_meta.json", [
        _meta("p1", sid="s001", mode="audio_ref", seed=111),
        _meta("p2", sid="s002", mode="fl2v", seed=222),
        _meta("", sid="card1", h3=False),
    ])
    report_path = tmp_path / "evidence.json"
    httpd, server = _serve({
        "/history/p1": _hroute("p1", mode="audio_ref", seed=111),
        "/history/p2": _hroute("p2", mode="fl2v", seed=222),
    })
    try:
        rc = ve.main([str(meta_path), "--server", server,
                      "--json-out", str(report_path), "--quiet"])
    finally:
        httpd.shutdown()
        httpd.server_close()
    report = _last_report(capsys)
    assert rc == 0
    assert report["ok"] is True
    assert report["checked"] == 2 and report["skipped"] == 1
    assert report["missing_prompt_ids"] == []
    assert report["failed"] == [] and report["mismatched"] == []
    assert report["comfyui"]["comfyui_version"] == "0.42.0"
    assert "全部 2 个 prompt_id 均可在 ComfyUI /history 查到" in report["history_retention_hint"]
    # 每条 prompt 的 history 证据
    details = {p["id"]: p for p in report["prompts"]}
    assert details["s001"]["status_str"] == "success"
    assert details["s001"]["has_video"] is True
    assert details["s001"]["workflow_history"] == "05_ref2va_4step_sla"
    assert details["s001"]["seed_history"] == [111]
    assert details["s001"]["video_files"] == ["p1_00001_.mp4"]
    assert details["s002"]["workflow_history"] == "04_i2v_fl2v_4step_sla"
    # --json-out 落盘
    saved = json.loads(report_path.read_text(encoding="utf-8"))
    assert saved == report


def test_verify_single_meta_file(tmp_path, capsys):
    meta_file = tmp_path / "s002_h3.meta.json"
    meta_file.write_text(json.dumps(_meta("real-pid", sid="s002"), ensure_ascii=False),
                         encoding="utf-8")
    httpd, server = _serve({"/history/real-pid": _hroute("real-pid")})
    try:
        rc = ve.main([str(meta_file), "--server", server, "--quiet"])
    finally:
        httpd.shutdown()
        httpd.server_close()
    report = _last_report(capsys)
    assert rc == 0 and report["ok"] is True and report["checked"] == 1
    assert report["inputs"] == [str(meta_file)]


def test_verify_directory_input(tmp_path, capsys):
    root = tmp_path / "templates_v3"
    _write_meta_json(root / "tonight_show" / "h3_meta.json",
                     [_meta("p1", sid="s001")])
    _write_meta_json(root / "history_talk" / "h3_meta.json",
                     [_meta("p2", sid="s002")])
    httpd, server = _serve({
        "/history/p1": _hroute("p1", seed=20261043),
        "/history/p2": _hroute("p2", seed=20261043),
    })
    try:
        rc = ve.main([str(root), "--server", server, "--quiet"])
    finally:
        httpd.shutdown()
        httpd.server_close()
    report = _last_report(capsys)
    assert rc == 0
    assert report["ok"] is True and report["checked"] == 2
    assert len(report["inputs"]) == 2
    assert str(root / "tonight_show" / "h3_meta.json") in report["inputs"]


# --------------------------------------------------------------------------- #
# 失败路径
# --------------------------------------------------------------------------- #
def test_missing_prompt_id_in_history(tmp_path, capsys):
    meta_path = _write_meta_json(tmp_path / "h3_meta.json",
                                 [_meta("p1", sid="s001"), _meta("p2", sid="s002")])
    httpd, server = _serve({"/history/p1": _hroute("p1")})  # p2 不存在
    try:
        rc = ve.main([str(meta_path), "--server", server, "--quiet"])
    finally:
        httpd.shutdown()
        httpd.server_close()
    report = _last_report(capsys)
    assert rc == 1
    assert report["ok"] is False
    assert report["missing_prompt_ids"] == ["p2"]
    assert "在 ComfyUI /history 查不到" in report["history_retention_hint"]
    # 能查到的那条仍然给出证据
    assert report["prompts"][0]["ok"] is True


def test_history_status_error(tmp_path, capsys):
    meta_path = _write_meta_json(tmp_path / "h3_meta.json", [_meta("p1")])
    httpd, server = _serve({"/history/p1": _hroute("p1", status="error")})
    try:
        rc = ve.main([str(meta_path), "--server", server, "--quiet"])
    finally:
        httpd.shutdown()
        httpd.server_close()
    report = _last_report(capsys)
    assert rc == 1 and report["ok"] is False
    assert report["failed"]
    assert "status=error" in report["failed"][0]["reason"]
    assert report["prompts"][0]["status_str"] == "error"


def test_seed_mismatch(tmp_path, capsys):
    meta_path = _write_meta_json(tmp_path / "h3_meta.json",
                                 [_meta("p1", seed=111)])
    httpd, server = _serve({"/history/p1": _hroute("p1", seed=999)})
    try:
        rc = ve.main([str(meta_path), "--server", server, "--quiet"])
    finally:
        httpd.shutdown()
        httpd.server_close()
    report = _last_report(capsys)
    assert rc == 1 and report["ok"] is False
    mismatch = {m["field"]: m for m in report["mismatched"]}
    assert "seed" in mismatch
    assert mismatch["seed"]["expected"] == 111
    assert mismatch["seed"]["actual"] == 999


def test_outputs_without_video(tmp_path, capsys):
    meta_path = _write_meta_json(tmp_path / "h3_meta.json", [_meta("p1")])
    httpd, server = _serve({"/history/p1": _hroute("p1", outputs="none")})
    try:
        rc = ve.main([str(meta_path), "--server", server, "--quiet"])
    finally:
        httpd.shutdown()
        httpd.server_close()
    report = _last_report(capsys)
    assert rc == 1 and report["ok"] is False
    assert any("视频" in item["reason"] for item in report["failed"])
    assert report["prompts"][0]["has_video"] is False


def test_outputs_images_only_is_not_video(tmp_path, capsys):
    meta_path = _write_meta_json(tmp_path / "h3_meta.json", [_meta("p1")])
    httpd, server = _serve({"/history/p1": _hroute("p1", outputs="image")})
    try:
        rc = ve.main([str(meta_path), "--server", server, "--quiet"])
    finally:
        httpd.shutdown()
        httpd.server_close()
    report = _last_report(capsys)
    assert rc == 1 and any("视频" in item["reason"] for item in report["failed"])


def test_workflow_not_matching_mode(tmp_path, capsys):
    # audio_ref 却写了 fl2v 的 workflow（meta 自相矛盾）
    meta_path = _write_meta_json(tmp_path / "h3_meta.json",
                                 [_meta("p1", mode="audio_ref",
                                        workflow="04_i2v_fl2v_4step_sla")])
    httpd, server = _serve({"/history/p1": _hroute("p1", mode="fl2v")})
    try:
        rc = ve.main([str(meta_path), "--server", server, "--quiet"])
    finally:
        httpd.shutdown()
        httpd.server_close()
    report = _last_report(capsys)
    assert rc == 1 and report["ok"] is False
    fields = {m["field"] for m in report["mismatched"]}
    assert "workflow" in fields
    wf = [m for m in report["mismatched"] if m["field"] == "workflow"][0]
    assert wf["expected"] == "05_ref2va_4step_sla"
    assert wf["actual"] == "04_i2v_fl2v_4step_sla"
    assert "history_workflow" in fields


def test_history_workflow_contradiction(tmp_path, capsys):
    # meta 自洽（audio_ref/05），但 history 里实际执行的是 04 ImageToVideo
    meta_path = _write_meta_json(tmp_path / "h3_meta.json", [_meta("p1")])
    httpd, server = _serve({"/history/p1": _hroute("p1", mode="fl2v")})
    try:
        rc = ve.main([str(meta_path), "--server", server, "--quiet"])
    finally:
        httpd.shutdown()
        httpd.server_close()
    report = _last_report(capsys)
    assert rc == 1
    assert [m for m in report["mismatched"]
            if m["field"] == "history_workflow"], report["mismatched"]


def test_duplicate_prompt_ids(tmp_path, capsys):
    meta_path = _write_meta_json(tmp_path / "h3_meta.json",
                                 [_meta("p1", sid="s001"), _meta("p1", sid="s002")])
    httpd, server = _serve({"/history/p1": _hroute("p1")})
    try:
        rc = ve.main([str(meta_path), "--server", server, "--quiet"])
    finally:
        httpd.shutdown()
        httpd.server_close()
    report = _last_report(capsys)
    assert rc == 1 and report["ok"] is False
    assert report["duplicate_prompt_ids"] == ["p1"]
    assert any(m["field"] == "prompt_id_duplicate" for m in report["mismatched"])


def test_meta_missing_prompt_id_fails(tmp_path, capsys):
    meta_path = _write_meta_json(tmp_path / "h3_meta.json", [_meta("", sid="s001")])
    httpd, server = _serve({})
    try:
        rc = ve.main([str(meta_path), "--server", server, "--quiet"])
    finally:
        httpd.shutdown()
        httpd.server_close()
    report = _last_report(capsys)
    assert rc == 1 and report["ok"] is False
    assert report["missing_prompt_ids"] == [""]
    assert report["failed"] and "缺少 prompt_id" in report["failed"][0]["reason"]


def test_server_unreachable(tmp_path, capsys):
    meta_path = _write_meta_json(tmp_path / "h3_meta.json", [_meta("p1")])
    rc = ve.main([str(meta_path), "--server", "http://127.0.0.1:9",
                  "--timeout", "0.5", "--quiet"])
    report = _last_report(capsys)
    assert rc == 1 and report["ok"] is False
    assert report.get("error") and "不可用" in report["error"]
    assert report["failed"] and "ComfyUI 不可用" in report["failed"][0]["reason"]


def test_no_meta_files_found(tmp_path, capsys):
    empty = tmp_path / "empty"
    empty.mkdir()
    rc = ve.main([str(empty), "--server", "http://127.0.0.1:9", "--timeout", "0.5",
                  "--quiet"])
    report = _last_report(capsys)
    assert rc == 1 and report["ok"] is False
    assert report.get("errors")


def test_cards_only_has_no_h3(tmp_path, capsys):
    meta_path = _write_meta_json(tmp_path / "h3_meta.json", [
        {"id": "card1", "h3": False, "mode": "static", "prompt_id": None},
        {"id": "previs1", "h3": False, "mode": "previs", "prompt_id": None},
    ])
    httpd, server = _serve({})
    try:
        rc = ve.main([str(meta_path), "--server", server, "--quiet"])
    finally:
        httpd.shutdown()
        httpd.server_close()
    report = _last_report(capsys)
    assert rc == 1 and report["ok"] is False
    assert report["checked"] == 0 and report["skipped"] == 2
    assert "没有可核验的 H3 镜头" in report.get("error", "")


# --------------------------------------------------------------------------- #
# 单元级 helper
# --------------------------------------------------------------------------- #
def test_helper_parsers():
    api = _api("audio_ref", seed=7)
    assert ve._infer_workflow(api) == "05_ref2va_4step_sla"
    assert ve._history_seeds(api) == [7]
    assert ve._infer_workflow(_api("fl2v")) == "04_i2v_fl2v_4step_sla"
    rec = _history("p1")
    assert ve._prompt_api(rec) is not None
    files = ve._output_files(rec["outputs"])
    assert files and ve._has_video(files)
    assert ve._normalize_workflow("05_ref2va_4step_sla.json") == "05_ref2va_4step_sla"
    # seed 归一化：history 里 noise_seed 是 int
    assert ve._history_seeds(_api("audio_ref", seed=20261043)) == [20261043]
    # 未知 prompt_id -> 空 dict（真实 ComfyUI 行为）
    assert ve._collect_metas({}) == []
    assert len(ve._collect_metas({"shots": [{"id": "a"}]})) == 1
    assert len(ve._collect_metas(_meta("p1"))) == 1
