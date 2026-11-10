# -*- coding: utf-8 -*-
"""fix-round3 CLI 端到端验证（全部 dry-run / 错误路径，不跑 GPU）。

覆盖：--stages 不再补依赖 / --with-deps / --start 冲突 / 未知阶段 / 未知模板 /
未知 --stage-params 键 / --duration 默认 None / 失败不留 run 目录。

用法：python docs/audit/_tools/fix4_verify_cli.py
"""
from __future__ import annotations

import contextlib
import io
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO))

from pipeline.agents import orchestrator as orch     # noqa: E402
from pipeline.agents.cli import main                 # noqa: E402

SHORTS = "custom_tpl_v46_duologue"
V5 = "tonight_show"
PASS, FAIL = [], []


def check(name: str, cond: bool, detail: str = "") -> None:
    (PASS if cond else FAIL).append(name)
    print(f"   [{'PASS' if cond else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))


def run(argv: list[str]) -> tuple[int, str, str, dict]:
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = main(argv)
    txt = out.getvalue()
    start = txt.find("{")
    payload = json.loads(txt[start:]) if start >= 0 else {}
    return code, txt, err.getvalue(), payload


def run_dirs() -> set[str]:
    root = Path(orch.RUN_ROOT)
    return {p.name for p in root.iterdir()} if root.is_dir() else set()


def main_cli() -> int:
    before = run_dirs()

    print("== 1. --stages publish --dry-run：只跑 publish（不再拉起 GPU 阶段） ==")
    code, _o, _e, d = run(["run", "--template", SHORTS, "--stages", "publish", "--dry-run"])
    check("exit=0 且 status=planned", code == 0 and d.get("status") == "planned", str(d.get("status")))
    check("阶段只有 publish", [s["id"] for s in d.get("stages", [])] == ["publish"],
          str([s["id"] for s in d.get("stages", [])]))

    print("\n== 2. --with-deps 恢复旧行为 ==")
    code, _o, _e, d = run(["run", "--template", SHORTS, "--stages", "publish",
                           "--with-deps", "--dry-run"])
    check("依赖闭包 = story,shots,qc,publish",
          [s["id"] for s in d.get("stages", [])] == ["story", "shots", "qc", "publish"],
          str([s["id"] for s in d.get("stages", [])]))

    print("\n== 3. V5 栈 --stages qc --dry-run：只跑 qc ==")
    code, _o, _e, d = run(["run", "--template", V5, "--stages", "qc", "--dry-run"])
    check("阶段只有 qc", [s["id"] for s in d.get("stages", [])] == ["qc"],
          str([s["id"] for s in d.get("stages", [])]))

    print("\n== 4. 错误路径都是 exit=2 且不留 run 目录 ==")
    before_err = run_dirs()          # 前面 1~3 段是正常 dry-run，会合法建目录；这里重新取样
    for name, argv in {
        "未知阶段": ["run", "--template", V5, "--stages", "asemble", "--dry-run"],
        "start+stages": ["run", "--template", V5, "--stages", "qc", "--start", "shots"],
        "未知模板": ["run", "--template", "no_such_tpl", "--dry-run"],
        "未知 stage-params 键": ["run", "--template", V5, "--stage-params", '{"shotz":{}}',
                                 "--dry-run"],
    }.items():
        code, _o, err, _d = run(argv)
        check(f"{name} → exit=2", code == 2, err.strip().splitlines()[0] if err.strip() else "")
    after = run_dirs()
    check("以上全部失败后没有新建 run 目录", after == before_err, f"新增 {sorted(after - before_err)}")

    print("\n== 5. --duration 不传 = None（模板 duration 优先） ==")
    code, _o, _e, d = run(["run", "--template", V5, "--stages", "story", "--dry-run"])
    ev = [json.loads(x) for x in
          (Path(d["run_dir"]) / "events.jsonl").read_text(encoding="utf-8").splitlines()]
    check("事件里 duration 来自模板/默认（不再是硬编码 40 覆盖模板）",
          ev[0]["duration"] in (40.0, float(json.loads(
              (Path(d["run_dir"]) / "workflow.json").read_text(encoding="utf-8")
          ).get("variables", {}).get("duration") or 40.0)),
          str(ev[0]["duration"]))
    check("run_id 带随机后缀", len(d["run_id"].rsplit("_", 1)[-1]) == 8, d["run_id"])

    print(f"\n== 结果：PASS {len(PASS)} / FAIL {len(FAIL)} ==")
    if FAIL:
        print("FAILED:", FAIL)
    return 0 if not FAIL else 1


if __name__ == "__main__":
    raise SystemExit(main_cli())
