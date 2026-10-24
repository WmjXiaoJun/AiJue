# -*- coding: utf-8 -*-
"""场景升级展示：16 个 2D 场景模板 demo 精选 + 3D Blender 场景镜头。"""
from __future__ import annotations
import json, os, subprocess
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
SD = ROOT / "outputs" / "scene_demos"
D3 = ROOT / "outputs" / "3d_scenes"
FF = ROOT / "tools" / "node_modules" / "ffmpeg-static" / "ffmpeg.exe"
FONT = r"C:\Windows\Fonts\Deng.ttf"
OUT = ROOT / "outputs" / "scene_demos" / "_showcase"
OUT.mkdir(exist_ok=True)

def text_card(path, title, sub):
    canvas = Image.new("RGB", (1280, 720), (12, 12, 14))
    d = ImageDraw.Draw(canvas)
    f1 = ImageFont.truetype(FONT, 64); f2 = ImageFont.truetype(FONT, 34)
    d.text((70, 260), title, font=f1, fill=(245, 197, 24))
    d.text((70, 360), sub, font=f2, fill=(225, 225, 230))
    canvas.save(path)

def still_card(png, mp4, seconds=1.8):
    subprocess.run([str(FF), "-y", "-v", "error", "-loop", "1", "-t", str(seconds), "-i", str(png),
                    "-r", "25", "-c:v", "libx264", "-preset", "veryfast", "-crf", "21",
                    "-pix_fmt", "yuv420p", "-an", str(mp4)], capture_output=True)

def labeled(mp4, out, label, ss=5.0, t=4.0):
    vf = ("scale=1280:720,setsar=1,drawtext=fontfile='%s':text='%s':x=40:y=h-64:fontsize=30:"
          "fontcolor=white:box=1:boxcolor=black@0.55:boxborderw=12") % (FONT.replace("\\", "/"), label)
    subprocess.run([str(FF), "-y", "-v", "error", "-ss", str(ss), "-t", str(t), "-i", str(mp4),
                    "-vf", vf, "-r", "25", "-c:v", "libx264", "-preset", "veryfast", "-crf", "21",
                    "-pix_fmt", "yuv420p", "-an", str(out)], capture_output=True)

segs = []
# intro
intro_png = OUT / "intro.png"
text_card(intro_png, "造片局 · AI微综艺", "场景升级：2D 绘景 + 3D 场景 + 角色分层")
intro_mp4 = OUT / "00_intro.mp4"; still_card(intro_png, intro_mp4, 2.2); segs.append(str(intro_mp4))
# 2D scene demos
names = sorted([f.stem for f in SD.glob("*.mp4") if f.is_file() and not f.name.startswith("_")])
labels = {
 "history_talk":"历史夜话 · 2D 书房场景","history_mystery":"历史悬案 · 案发现场","classics":"典籍对话 · 书房",
 "tonight_show":"美式深夜秀 · 2D 办公室","roundtable":"圆桌演播厅 · 2D","taskmaster":"任务大师 · 彩色舞台",
 "qipashuo":"奇葩辩论 · 幕布辩论台","mystery":"推理现场 · 案发现场","national_treasure":"国宝守护 · 金色展厅",
 "slow_life":"慢生活 · 暖色厨房","masked_singer":"蒙面唱将 · 彩虹舞台","standup":"脱口秀大会 · 舞台",
 "snl_sketch":"SNL 小品 · 舞台","hot_ones":"辣访谈 · 暖光访谈区","shark_tank":"创智赢家 · 办公桌",
 "traitors":"叛徒圆桌 · 演播厅",
}
for i, n in enumerate(names):
    p = SD / (n + ".mp4")
    if not p.exists(): continue
    out = OUT / ("2d_%02d_%s.mp4" % (i, n))
    labeled(p, out, labels.get(n, n), ss=5.0, t=4.0)
    if out.exists(): segs.append(str(out))
# concat
lst = OUT / "concat.txt"
lst.write_text("\n".join("file '%s'" % s.replace("\\", "/") for s in segs) + "\n", encoding="utf-8")
showcase = ROOT / "outputs" / "scene_demos" / "scene_showcase.mp4"
subprocess.run([str(FF), "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", str(lst),
                "-c", "copy", "-movflags", "+faststart", str(showcase)], capture_output=True)
print(json.dumps({"ok": showcase.exists(), "showcase": str(showcase),
                  "size_mb": round(showcase.stat().st_size / 1048576, 2) if showcase.exists() else 0,
                  "segments": len(segs)}, ensure_ascii=False))