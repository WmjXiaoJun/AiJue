from pathlib import Path
src = Path("pipeline/render_v5.py").read_text(encoding="utf-8").splitlines()
for n in range(1243, 1257):
    line = src[n-1]
    print(f"{n:5d}|{len(line) - len(line.lstrip()):2d}| {line}")
print()
src2 = Path("pipeline/lipsync.py").read_text(encoding="utf-8").splitlines()
for n in (875, 876, 877, 878):
    print(f"{n:5d}| {src2[n-1]}")
