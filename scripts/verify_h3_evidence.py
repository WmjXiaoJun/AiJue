#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""H3 证据链独立核验：读 h3_meta.json，逐条用 prompt_id 反查 ComfyUI /history。

用途：验收时证明「每镜 prompt_id 是 ComfyUI 真实记录、不是编造」。
对每条 h3=true 的镜头 meta：
1. ``GET /history/{prompt_id}``：记录必须存在（missing_prompt_ids）；
2. ``status.status_str == "success"``（failed）；
3. ``outputs`` 里必须有视频文件（failed）；
4. workflow 与 mode 对应：audio_ref→05_ref2va_4step_sla，fl2v→04_i2v_fl2v_4step_sla，
   并用 history 里的 H3 节点（MiniMaxH3ReferenceToVideo / MiniMaxH3ImageToVideo）反证（mismatched）；
5. seed 与 history prompt 的 ``RandomNoise.noise_seed`` 一致（mismatched）。

用法：
    python scripts/verify_h3_evidence.py outputs/templates_v3/tonight_show/h3_meta.json
    python scripts/verify_h3_evidence.py outputs/templates_v3       # 扫描 */h3_meta.json
    python scripts/verify_h3_evidence.py tmp/v3_h3/s002_h3.meta.json --json-out tmp/v3_h3/evidence.json

输出：最后一行 JSON 报告（ok/checked/missing_prompt_ids/failed/mismatched/
history_retention_hint/...）；退出码 0=全绿，1=有缺失/失败/不一致/服务不可用。
只做 GET 查询，不提交任何生成任务（不占 GPU）。
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from pipeline import h3_comfy  # noqa: E402

WORKFLOWS = dict(h3_comfy.WORKFLOWS)                       # mode -> workflow
MODE_BY_WORKFLOW = {v: k for k, v in WORKFLOWS.items()}    # workflow -> mode
VIDEO_EXTS = {".mp4", ".webm", ".mkv", ".mov", ".m4v", ".avi"}
DEFAULT_PATHS = ["outputs/templates_v3"]


# --------------------------------------------------------------------------- #
# HTTP / 文件读取
# --------------------------------------------------------------------------- #
def _http_json(url: str, timeout: float = 15.0) -> Any:
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 - 本机 ComfyUI
        raw = resp.read().decode("utf-8", "replace")
    return json.loads(raw) if raw.strip() else {}


def _normalize_workflow(value: Any) -> str:
    """工作流名归一（去掉 .json / 路径），如 05_ref2va_4step_sla.json -> 05_ref2va_4step_sla。"""
    text = str(value or "").strip()
    if not text:
        return ""
    return Path(text).stem if text.endswith(".json") else text


def _meta_files(path: Path) -> list[Path]:
    """路径 -> h3 meta 文件列表（单文件 / templates_v3/{id} 目录 / 上级扫描目录）。"""
    if path.is_file():
        return [path]
    if not path.is_dir():
        return []
    direct = path / "h3_meta.json"
    if direct.is_file():
        return [direct]
    found = sorted(path.glob("*/h3_meta.json"))
    if found:
        return found
    return sorted(path.glob("*.meta.json"))


def _collect_metas(doc: Any) -> list[dict]:
    """从 h3_meta.json（{shots:[...]}）或单镜 meta / sid->meta 映射 / 列表里取 meta。"""
    if isinstance(doc, dict):
        shots = doc.get("shots")
        if isinstance(shots, list):
            return [m for m in shots if isinstance(m, dict)]
        if any(k in doc for k in ("prompt_id", "h3", "workflow", "mode")):
            return [dict(doc)]
        out: list[dict] = []
        for sid, item in doc.items():
            if isinstance(item, dict):
                merged = dict(item)
                merged.setdefault("id", str(sid))
                out.append(merged)
        return out
    if isinstance(doc, list):
        return [m for m in doc if isinstance(m, dict)]
    return []


def collect_inputs(paths: list[str | Path]) -> dict:
    """解析输入路径 -> {"metas","inputs","errors","modes"}。"""
    metas: list[dict] = []
    inputs: list[str] = []
    errors: list[str] = []
    modes: list[str] = []
    seen_files: set[str] = set()
    for raw in paths:
        for pattern in ([str(raw)] if not glob.has_magic(str(raw))
                        else sorted(glob.glob(str(raw)))):
            path = Path(pattern)
            files = _meta_files(path)
            if not files:
                errors.append(f"找不到 h3 meta: {pattern}")
                continue
            for file in files:
                key = str(file.resolve())
                if key in seen_files:
                    continue
                seen_files.add(key)
                try:
                    doc = json.loads(file.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError) as exc:
                    errors.append(f"读取失败 {file}: {exc}")
                    continue
                items = _collect_metas(doc)
                if not items:
                    errors.append(f"没有镜头 meta: {file}")
                    continue
                inputs.append(str(file))
                if isinstance(doc, dict):
                    modes.append(str(doc.get("h3_mode") or ""))
                metas.extend(items)
    return {"metas": metas, "inputs": inputs, "errors": errors,
            "modes": [m for m in modes if m]}


# --------------------------------------------------------------------------- #
# history 记录解析
# --------------------------------------------------------------------------- #
def _prompt_api(rec: dict) -> dict | None:
    """history 的 prompt 字段 -> 提交时的 API workflow（{node_id: {class_type,inputs}}）。"""
    prompt = rec.get("prompt")
    if isinstance(prompt, dict):
        return prompt
    if isinstance(prompt, list) and len(prompt) > 2 and isinstance(prompt[2], dict):
        return prompt[2]
    return None


def _create_time(rec: dict) -> Any:
    prompt = rec.get("prompt")
    if isinstance(prompt, list) and len(prompt) > 3 and isinstance(prompt[3], dict):
        return prompt[3].get("create_time")
    return None


def _infer_workflow(api: dict | None) -> str | None:
    """从 history prompt 的 H3 节点类型反推工作流。"""
    if not isinstance(api, dict):
        return None
    for node in api.values():
        if not isinstance(node, dict):
            continue
        cls = str(node.get("class_type") or "")
        if cls == "MiniMaxH3ReferenceToVideo":
            return WORKFLOWS["audio_ref"]
        if cls == "MiniMaxH3ImageToVideo":
            return WORKFLOWS["fl2v"]
    return None


def _history_seeds(api: dict | None) -> list[int]:
    """history prompt 里所有 RandomNoise.noise_seed。"""
    seeds: list[int] = []
    if not isinstance(api, dict):
        return seeds
    for node in api.values():
        if not isinstance(node, dict) or node.get("class_type") != "RandomNoise":
            continue
        inputs = node.get("inputs") if isinstance(node.get("inputs"), dict) else {}
        try:
            seeds.append(int(inputs.get("noise_seed")))
        except (TypeError, ValueError):
            continue
    return seeds


def _output_files(outputs: Any) -> list[dict]:
    """history outputs -> [{filename,subfolder,type,ext,kind,node_id}]。"""
    files: list[dict] = []
    if not isinstance(outputs, dict):
        return files
    for nid, node_out in outputs.items():
        if not isinstance(node_out, dict):
            continue
        for items in node_out.values():
            if not isinstance(items, list):
                continue
            for item in items:
                if not isinstance(item, dict) or not item.get("filename"):
                    continue
                filename = str(item["filename"])
                metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
                files.append({
                    "filename": filename,
                    "subfolder": str(item.get("subfolder") or ""),
                    "type": str(item.get("type") or "output"),
                    "ext": os.path.splitext(filename)[1].lower(),
                    "kind": str(metadata.get("kind") or ""),
                    "node_id": str(nid),
                })
    return files


def _has_video(files: list[dict]) -> bool:
    return any(f["ext"] in VIDEO_EXTS or f["kind"] == "video" for f in files)


def _local_output_found(files: list[dict]) -> bool:
    """history 产出的视频是否仍在 ComfyUI/output 下（离线证据增强，不作为失败条件）。"""
    for f in files:
        if not (f["ext"] in VIDEO_EXTS or f["kind"] == "video"):
            continue
        if f["type"] != "output":
            continue
        candidate = h3_comfy.COMFY_ROOT / "output" / f["subfolder"] / f["filename"]
        try:
            if candidate.is_file():
                return True
        except OSError:
            continue
    return False


# --------------------------------------------------------------------------- #
# 核验
# --------------------------------------------------------------------------- #
def _mismatch(item: dict, field: str, expected: Any, actual: Any) -> dict:
    return {"id": item.get("_sid") or "", "prompt_id": item.get("_pid") or "",
            "field": field, "expected": expected, "actual": actual}


def _failed(item: dict, reason: str) -> dict:
    return {"id": item.get("_sid") or "", "prompt_id": item.get("_pid") or "",
            "reason": reason}


def verify(metas: list[dict], *, server: str, timeout: float = 15.0,
           fetch_json: Callable[[str], Any] | None = None) -> dict:
    """核验 meta 列表与 ComfyUI /history 的一致性，返回报告 dict。"""
    fetch = fetch_json or (lambda url: _http_json(url, timeout=timeout))
    server = str(server).rstrip("/")
    missing: list[str] = []
    failed: list[dict] = []
    mismatched: list[dict] = []
    prompts: list[dict] = []
    fallbacks: list[str] = []
    skipped = 0
    candidates: list[dict] = []
    for raw in metas:
        meta = dict(raw)
        if meta.get("h3") is False:
            skipped += 1
            if meta.get("fallback") or meta.get("fallback_static"):
                fallbacks.append(str(meta.get("id") or meta.get("shot_id") or ""))
            continue
        candidates.append(meta)

    report: dict[str, Any] = {
        "ok": False,
        "server": server,
        "inputs": [],
        "checked": len(candidates),
        "skipped": skipped,
        "fallbacks": fallbacks,
        "missing_prompt_ids": missing,
        "failed": failed,
        "mismatched": mismatched,
        "duplicate_prompt_ids": [],
        "prompts": prompts,
        "comfyui": {},
        "history_retention_hint": "",
        "generated_at": datetime.now().isoformat(timespec="seconds"),
    }

    # 服务探测（只读；服务不可用则不做逐条 GET，直接给 failed 诊断）
    server_ok = False
    try:
        stats = fetch(server + "/system_stats")
        server_ok = isinstance(stats, dict) and "system" in stats
        system = (stats.get("system") if isinstance(stats, dict) else {}) or {}
        report["comfyui"] = {
            "server_ok": server_ok,
            "comfyui_version": system.get("comfyui_version"),
            "os": system.get("os"),
        }
    except Exception as exc:  # noqa: BLE001
        report["error"] = f"ComfyUI /system_stats 不可用: {exc}"
        report["comfyui"] = {"server_ok": False}
    if not server_ok and candidates:
        for meta in candidates:
            meta["_sid"] = str(meta.get("id") or meta.get("shot_id") or "")
            meta["_pid"] = str(meta.get("prompt_id") or "").strip()
            failed.append(_failed(meta, "ComfyUI 不可用，无法核验 /history"))
        report["history_retention_hint"] = (
            "ComfyUI 不可达，本次未核验任何 prompt_id；请先启动 ComfyUI（http://127.0.0.1:8188）"
            "后重跑本脚本。")
        return report

    if not candidates:
        report["error"] = ("没有可核验的 H3 镜头（全部为卡片/previs/静态兜底？）；"
                           "验收要求非卡片镜 100% h3=true 且带 prompt_id")
        report["history_retention_hint"] = (
            "previs/静态兜底镜不产生 prompt_id；/history 为 ComfyUI 内存记录（重启即清空），"
            "验收/交付请保留 h3_meta.json 与 ComfyUI/output 成片。")
        return report

    seen: dict[str, str] = {}
    for meta in candidates:
        sid = str(meta.get("id") or meta.get("shot_id") or "")
        pid = str(meta.get("prompt_id") or "").strip()
        meta["_sid"], meta["_pid"] = sid, pid
        mode = str(meta.get("mode") or meta.get("h3_mode") or "").strip()
        wf_meta = _normalize_workflow(meta.get("workflow"))
        seed_meta = meta.get("seed")
        expected = WORKFLOWS.get(mode) if mode in WORKFLOWS else (wf_meta or None)

        detail: dict[str, Any] = {
            "id": sid, "prompt_id": pid, "mode": mode,
            "workflow_meta": wf_meta, "seed_meta": seed_meta,
            "status_str": None, "has_video": False, "video_files": [],
            "workflow_history": None, "seed_history": [],
            "local_output_found": False, "create_time": None, "ok": True,
        }
        before = len(failed) + len(mismatched)

        if not pid:
            missing.append("")
            failed.append(_failed(meta, "meta 缺少 prompt_id（无法核验 ComfyUI 记录）"))
            detail["ok"] = False
            prompts.append(detail)
            continue
        if pid in seen:
            mismatch = _mismatch(meta, "prompt_id_duplicate", seen[pid], sid)
            mismatched.append(mismatch)
            report["duplicate_prompt_ids"].append(pid)
        seen[pid] = sid

        # ---- 1) /history 存在性 ----
        try:
            hist = fetch(server + "/history/" + urllib.parse.quote(pid))
        except Exception as exc:  # noqa: BLE001
            failed.append(_failed(meta, f"/history 查询失败: {exc}"))
            detail["ok"] = False
            prompts.append(detail)
            continue
        rec = hist.get(pid) if isinstance(hist, dict) else None
        if not isinstance(rec, dict):
            missing.append(pid)
            prompts.append(detail)
            detail["ok"] = False
            continue

        # ---- 2) 状态 ----
        status = rec.get("status") if isinstance(rec.get("status"), dict) else {}
        status_str = status.get("status_str") or (
            "success" if status.get("completed") else "")
        detail["status_str"] = status_str
        if status_str != "success":
            messages = status.get("messages")
            hint = json.dumps(messages, ensure_ascii=False)[:300] if messages else ""
            failed.append(_failed(meta, f"history status={status_str or '?'}（非 success）"
                                      + (f"：{hint}" if hint else "")))
            detail["ok"] = False

        # ---- 3) 视频产出 ----
        files = _output_files(rec.get("outputs"))
        detail["has_video"] = _has_video(files)
        detail["video_files"] = [
            f["filename"] for f in files
            if f["ext"] in VIDEO_EXTS or f["kind"] == "video"]
        detail["local_output_found"] = _local_output_found(files)
        if not detail["has_video"]:
            failed.append(_failed(meta, "history outputs 里没有视频文件"))

        # ---- 4) workflow ↔ mode ----
        if mode not in WORKFLOWS and not wf_meta:
            mismatched.append(_mismatch(
                meta, "mode/workflow", sorted(WORKFLOWS), {"mode": mode,
                                                           "workflow": wf_meta}))
        elif mode in WORKFLOWS:
            expected = WORKFLOWS[mode]
            if wf_meta != expected:
                mismatched.append(_mismatch(meta, "workflow", expected, wf_meta))
        elif wf_meta in MODE_BY_WORKFLOW:
            detail["mode"] = MODE_BY_WORKFLOW[wf_meta]  # 由 workflow 反推

        api = _prompt_api(rec)
        if api is None:
            failed.append(_failed(meta, "history 里没有 prompt 内容（无法核对 workflow/seed）"))
        else:
            inferred = _infer_workflow(api)
            detail["workflow_history"] = inferred
            if expected and inferred != expected:
                mismatched.append(_mismatch(meta, "history_workflow", expected, inferred))
            # ---- 5) seed 一致性 ----
            seeds = _history_seeds(api)
            detail["seed_history"] = seeds
            if not seeds:
                mismatched.append(_mismatch(meta, "seed", seed_meta, None))
            else:
                try:
                    seed_meta_i = int(seed_meta)
                except (TypeError, ValueError):
                    seed_meta_i = None
                if seed_meta_i is None or seed_meta_i not in seeds:
                    mismatched.append(_mismatch(meta, "seed", seed_meta, seeds[0]))

        detail["create_time"] = _create_time(rec)
        detail["ok"] = len(failed) + len(mismatched) == before
        prompts.append(detail)

    report["ok"] = bool(
        candidates and not missing and not failed and not mismatched
        and not report["duplicate_prompt_ids"]
    )
    if missing:
        report["history_retention_hint"] = (
            f"{len(missing)} 个 prompt_id 在 ComfyUI /history 查不到：/history 只保留"
            " ComfyUI 本次启动以来的内存记录（重启/清理即丢失），不能作为长期离线证据；"
            "交付请同时保留 h3_meta.json + ComfyUI/output 成片，缺失镜需重跑并重新核验。")
    elif report["ok"]:
        report["history_retention_hint"] = (
            f"全部 {len(candidates)} 个 prompt_id 均可在 ComfyUI /history 查到"
            "（/history 是 ComfyUI 本次启动后的内存记录，重启即清空）；"
            "验收/交付请同时保留 h3_meta.json 与 ComfyUI/output 下的成片作为离线证据。")
    else:
        report["history_retention_hint"] = (
            "prompt_id 存在但状态/产出/参数有异常：先看 failed / mismatched；"
            "/history 为内存记录（重启即清空），交付请保留 h3_meta.json + ComfyUI/output 成片。")
    return report


def run_verify(paths: list[str | Path], *, server: str, timeout: float = 15.0,
               fetch_json: Callable[[str], Any] | None = None) -> dict:
    """解析输入路径并核验，返回报告（main 与测试共用）。"""
    collected = collect_inputs(paths)
    report = verify(collected["metas"], server=server, timeout=timeout,
                    fetch_json=fetch_json)
    report["inputs"] = collected["inputs"]
    if collected["errors"]:
        report["errors"] = collected["errors"]
        report["ok"] = False
    return report


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def _summary_lines(report: dict) -> list[str]:
    lines = []
    comfy = report.get("comfyui") or {}
    lines.append("[evidence] server=%s comfyui=%s server_ok=%s" % (
        report.get("server"), comfy.get("comfyui_version") or "?",
        comfy.get("server_ok")))
    lines.append("[evidence] inputs=%d checked=%d skipped=%d missing=%d failed=%d "
                 "mismatched=%d" % (
                     len(report.get("inputs") or []), report.get("checked") or 0,
                     report.get("skipped") or 0,
                     len(report.get("missing_prompt_ids") or []),
                     len(report.get("failed") or []),
                     len(report.get("mismatched") or [])))
    for item in report.get("missing_prompt_ids") or []:
        lines.append(f"[evidence][MISSING] prompt_id={item or '(空)'}")
    for item in report.get("failed") or []:
        lines.append("[evidence][FAIL] %s %s %s" % (
            item.get("id"), item.get("prompt_id"), item.get("reason")))
    for item in report.get("mismatched") or []:
        lines.append("[evidence][MISMATCH] %s %s field=%s expected=%r actual=%r" % (
            item.get("id"), item.get("prompt_id"), item.get("field"),
            item.get("expected"), item.get("actual")))
    for err in report.get("errors") or []:
        lines.append(f"[evidence][ERROR] {err}")
    lines.append("[evidence] OK" if report.get("ok") else "[evidence] FAILED")
    return lines


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        description="H3 证据链核验：用 prompt_id 反查 ComfyUI /history")
    ap.add_argument("paths", nargs="*", default=None,
                    help="h3_meta.json / 模板目录 / outputs/templates_v3（默认）")
    ap.add_argument("--server", default=None,
                    help="ComfyUI 地址（默认 COMFYUI_URL 或 http://127.0.0.1:8188）")
    ap.add_argument("--timeout", type=float, default=15.0, help="单次 HTTP 超时（秒）")
    ap.add_argument("--json-out", default=None, help="把 JSON 报告另存到文件")
    ap.add_argument("--quiet", action="store_true", help="只输出最后一行 JSON")
    return ap


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    paths = args.paths or [str(REPO_ROOT / p) for p in DEFAULT_PATHS]
    server = str(args.server or h3_comfy.default_server())
    report = run_verify(paths, server=server, timeout=args.timeout)
    if args.json_out:
        out = Path(args.json_out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    if not args.quiet:
        for line in _summary_lines(report):
            print(line, flush=True)
    print(json.dumps(report, ensure_ascii=False), flush=True)
    return 0 if report.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
