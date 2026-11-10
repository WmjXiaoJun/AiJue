# -*- coding: utf-8 -*-
"""核验：父进程按 utf-8 解码、子进程（未 reconfigure）用 gbk 写 stderr 时的可读性。

只写 tmp/audit3，不碰源码与合同。结果写到 tmp/audit3/probe_out.txt（UTF-8）。
"""
import subprocess
import sys
from pathlib import Path

CHILD = """
import sys
print("child_stdout_enc=%s child_stderr_enc=%s" % (sys.stdout.encoding, sys.stderr.encoding),
      file=sys.stderr)
raise RuntimeError("中文错误：checkpoint animagine-xl-4.0.safetensors 不在列表里")
"""


def main() -> int:
    tmp = Path("tmp/audit3/encoding_child.py")
    tmp.parent.mkdir(parents=True, exist_ok=True)
    tmp.write_text(CHILD, encoding="utf-8")
    lines = []
    for label, kwargs in (
        ("utf-8 + errors=replace (现状 pipeline/v5_art.py:713)", dict(encoding="utf-8", errors="replace")),
        ("encoding=gbk (对照: 按子进程真实编码解码)", dict(encoding="gbk", errors="replace")),
    ):
        proc = subprocess.run([sys.executable, str(tmp)], capture_output=True, text=True, **kwargs)
        lines.append("=" * 70)
        lines.append(label + "   returncode=%s" % proc.returncode)
        for line in (proc.stderr or "").strip().splitlines()[-2:]:
            lines.append("  | " + line)
    out = Path("tmp/audit3/probe_out.txt")
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("written:", out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
