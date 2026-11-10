# -*- coding: utf-8 -*-
"""静态扫描 scripts/*.py 的危险操作（只读，不执行被扫脚本）。

输出每脚本：仓库根解析方式 / 是否写 contract / 删除调用 / dry-run 开关 /
__main__ 守卫 / 失败退出码语义 / 外部重活（imagegen、templates3d_hq、DB）。

用法: python docs/audit/_tools/scan_scripts_danger.py [--md]
"""
from __future__ import annotations

import argparse
import ast
import json
import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
SCRIPTS = REPO / "scripts"

DEL_FUNCS = {"rmtree", "unlink", "remove", "rmdir", "removedirs"}
HEAVY = ("imagegen.cli", "templates3d_hq", "render_v3", "render_v5", "uvicorn",
         "enforce_loudness", "assemble_video", "tts", "ffmpeg")


def repo_root_kind(tree: ast.AST, src: str) -> str:
    if re.search(r'Path\(\s*r?"[A-Za-z]:\\\\', src) or "BaiduSyncdisk" in src:
        return "HARDCODED-ABS"
    if re.search(r'Path\(\s*"\."\s*\)\.resolve\(\)', src) or re.search(r'Path\(\s*os\.getcwd', src):
        return "CWD"
    if "__file__" in src and "parents[1]" in src:
        return "__file__"
    if "__file__" in src:
        return "__file__(other)"
    return "none"


def scan(path: Path) -> dict:
    src = path.read_text(encoding="utf-8", errors="replace")
    try:
        tree = ast.parse(src)
    except SyntaxError as exc:
        return {"script": path.name, "syntax_error": str(exc)}

    deletes, contract_writes, db, heavy, subshell = [], [], [], set(), []
    has_dryrun = "--dry-run" in src or "dry_run" in src
    main_returns = []
    has_guard = False
    toplevel_calls = 0

    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            fn = node.func
            name = getattr(fn, "attr", None) or getattr(fn, "id", None)
            if name in DEL_FUNCS:
                try:
                    arg = ast.unparse(node.args[0]) if node.args else ""
                except Exception:
                    arg = "?"
                deletes.append("%s(%s)" % (name, arg[:70]))
            if name in ("write_text", "write_bytes", "dump", "write_json", "save_workflow"):
                try:
                    target = ast.unparse(node.args[0]) if node.args else ast.unparse(fn.value)
                except Exception:
                    target = "?"
                if "contract" in target:
                    contract_writes.append(target[:90])
            if name in ("SessionLocal", "sessionmaker", "commit"):
                db.append(name)
            if name == "run" or name == "call" or name == "Popen":
                try:
                    subshell.append(ast.unparse(node)[:110])
                except Exception:
                    pass
        if isinstance(node, ast.If):
            try:
                if "__name__" in ast.unparse(node.test):
                    has_guard = True
            except Exception:
                pass
        if isinstance(node, ast.Return) and getattr(node.value, "value", None) is not None:
            main_returns.append(repr(getattr(node.value, "value")))
    for tok in HEAVY:
        if tok in src:
            heavy.add(tok)
    # 顶层（模块级）可执行语句：import 即执行
    for node in tree.body:
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Call):
            toplevel_calls += 1
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Call):
            fname = getattr(node.value.func, "attr", None) or getattr(node.value.func, "id", None)
            if fname in ("SessionLocal",):
                toplevel_calls += 1
    return {
        "script": path.name,
        "root": repo_root_kind(tree, src),
        "deletes": deletes,
        "contract_writes": contract_writes,
        "db": sorted(set(db)),
        "heavy": sorted(heavy),
        "subprocess": len(subshell),
        "dry_run": has_dryrun,
        "guard": has_guard,
        "toplevel_exec": toplevel_calls,
        "returns": sorted(set(main_returns)),
        "lines": src.count("\n") + 1,
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--md", action="store_true", help="输出 markdown 表格")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    rows = [scan(p) for p in sorted(SCRIPTS.glob("*.py"))]
    if args.json:
        print(json.dumps(rows, ensure_ascii=False, indent=1))
        return 0
    if args.md:
        print("| 脚本 | 仓库根 | 删除调用 | 写 contract | DB | dry-run | __main__ | 失败码 | 行 |")
        print("|---|---|---|---|---|---|---|---|---|")
        for r in rows:
            if "syntax_error" in r:
                print("| %s | SYNTAX ERROR: %s | | | | | | | |" % (r["script"], r["syntax_error"]))
                continue
            print("| %s | %s | %s | %s | %s | %s | %s | %s | %s |" % (
                r["script"], r["root"],
                ("; ".join(r["deletes"])[:100] or "-"),
                ("; ".join(r["contract_writes"])[:80] or "-"),
                (",".join(r["db"]) or "-"),
                "Y" if r["dry_run"] else "**N**",
                "Y" if r["guard"] else "**N**",
                (",".join(r["returns"]) or "-"),
                r["lines"]))
        return 0
    for r in rows:
        print(json.dumps(r, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
