# -*- coding: utf-8 -*-
"""汇总 16 个模板验证片：contact sheet + 60-80s 模板巡礼 showcase。"""
from __future__ import annotations
import json, os, subprocess
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "outputs" / "template_demos"
FF = ROOT / "tools" / "node_modules" / "ffmpeg-static" / "ffmpeg.exe"
FP = ROOT / "tools" / "node_modules" / "ffprobe-static" / "bin" / "win32" / "x64" / "ffprobe.exe"
FONT = r"C:\Windows\Fonts\Deng.ttf"

results = json.loads((OUT / "results.json").read_text(encoding="utf-8"))
ok = [r for r in results if r.get("ok")]
entries = []
for r in ok:
    vid = Path(r["video"])
    entries.append({"id": r["id"], "name": r.get("name"), "video": str(vid), "duration": r.get("duration")})

# 1) contact sheet (4x4)
font = ImageFont.truetype(FONT, 24)
cw, ch = 480, 270
cols = 4
rows = (len(entries) + cols - 1) // cols
canvas = Image.new("RGB", (cols * cw, rows * (ch + 36)), (14, 14, 16))
d = ImageDraw.Draw(canvas)
for i, e in enumerate(entries):
    tmp = OUT / "_thumbs" / (e["id"] + ".png")
    tmp.parent.mkdir(exist_ok=True)
    subprocess.run([str(FF), "-y", "-v", "error", "-ss", "2", "-i", e["video"], "-frames:v", "1", str(tmp)], capture_output=True)
    if tmp.exists():
        im = Image.open(tmp).convert("RGB").resize((cw, ch))
        x, y = (i % cols) * cw, (i // cols) * (ch + 36)
        canvas.paste(im, (x, y))
        d.text((x + 8, y + ch + 4), str(e.get("name") or e["id"]), font=font, fill=(245, 200, 65))
sheet = ROOT / "docs" / "screenshots" / "48_templates_contact_sheet.png"
canvas.save(sheet)
print("sheet", sheet)

# 2) showcase: 4s excerpt each with label
seg_dir = OUT / "_showcase"
seg_dir.mkdir(exist_ok=True)
segs = []
for idx, e in enumerate(entries):
    mp4 = seg_dir / ("%02d_%s.mp4" % (idx, e["id"]))
    label = str(e.get("name") or e["id"]).replace(":", "：")
    vf = ("scale=1280:720,setsar=1,drawtext=fontfile='%s':text='%s':x=48:y=h-72:fontsize=34:"
          "fontcolor=white:box=1:boxcolor=black@0.55:boxborderw=14") % (FONT.replace("\\", "/"), label)
    subprocess.run([str(FF), "-y", "-v", "error", "-ss", "2", "-t", "4.0", "-i", e["video"],
                    "-vf", vf, "-r", "25", "-c:v", "libx264", "-preset", "veryfast", "-crf", "21",
                    "-pix_fmt", "yuv420p", "-an", str(mp4)], capture_output=True)
    if mp4.exists():
        segs.append(str(mp4))
# intro title 2s
intro_png = seg_dir / "intro.png"
canvas2 = Image.new("RGB", (1280, 720), (12, 12, 14))
d2 = ImageDraw.Draw(canvas2)
f1 = ImageFont.truetype(FONT, 72); f2 = ImageFont.truetype(FONT, 36)
d2.text((80, 260), "造片局 · AI微综艺", font=f1, fill=(245, 197, 24))
d2.text((80, 370), "16 个综艺模板 · 镜头级验证片", font=f2, fill=(230, 230, 235))
d2.text((80, 430), "景别 / 机位 / 运镜 / xfade 转场 / TTS / 字幕", font=f2, fill=(160, 160, 170))
canvas2.save(intro_png)
intro_mp4 = seg_dir / "00_intro.mp4"
subprocess.run([str(FF), "-y", "-v", "error", "-loop", "1", "-t", "2.2", "-i", str(intro_png),
                "-r", "25", "-c:v", "libx264", "-preset", "veryfast", "-crf", "21",
                "-pix_fmt", "yuv420p", "-an", str(intro_mp4)], capture_output=True)
segs = [str(intro_mp4)] + segs
lst = seg_dir / "concat.txt"
lst.write_text("\n".join("file '%s'" % s.replace("\\", "/") for s in segs) + "\n", encoding="utf-8")
showcase = ROOT / "outputs" / "template_demos" / "template_showcase.mp4"
subprocess.run([str(FF), "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", str(lst),
                "-c", "copy", "-movflags", "+faststart", str(showcase)], capture_output=True)
# manifest
manifest = ROOT / "outputs" / "template_demos" / "manifest.json"
manifest.write_text(json.dumps({"count": len(entries), "templates": entries, "showcase": str(showcase)}, ensure_ascii=False, indent=2), encoding="utf-8")
print("showcase", showcase, "size", round(showcase.stat().st_size/1048576, 1) if showcase.exists() else None, "MB")
