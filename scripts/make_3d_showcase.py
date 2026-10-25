# -*- coding: utf-8 -*-
"""11 个 3D 风格模板 demo 巡礼。"""
import json, subprocess
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont
ROOT = Path(r"D:\BaiduSyncdisk\wmj_work\XJ\AiJue")
D = ROOT / "outputs" / "scene_demos_3d"
FF = ROOT / "tools" / "node_modules" / "ffmpeg-static" / "ffmpeg.exe"
FONT = r"C:\Windows\Fonts\Deng.ttf"
OUT = D / "_showcase"; OUT.mkdir(exist_ok=True)
def card(png, mp4, title, sub):
    c = Image.new("RGB", (1280, 720), (12,12,14)); d = ImageDraw.Draw(c)
    f1 = ImageFont.truetype(FONT, 64); f2 = ImageFont.truetype(FONT, 34)
    d.text((70, 250), title, font=f1, fill=(245,197,24)); d.text((70, 350), sub, font=f2, fill=(225,225,230))
    c.save(png)
    subprocess.run([str(FF),"-y","-v","error","-loop","1","-t","2.0","-i",str(png),"-r","25",
                    "-c:v","libx264","-preset","veryfast","-crf","21","-pix_fmt","yuv420p","-an",str(mp4)],capture_output=True)
def seg(src, out, label):
    vf = ("scale=1280:720,setsar=1,drawtext=fontfile='%s':text='%s':x=40:y=h-64:fontsize=30:"
          "fontcolor=white:box=1:boxcolor=black@0.55:boxborderw=12") % (FONT.replace("\\","/"), label)
    subprocess.run([str(FF),"-y","-v","error","-ss","4","-t","4","-i",str(src),"-vf",vf,"-r","25",
                    "-c:v","libx264","-preset","veryfast","-crf","21","-pix_fmt","yuv420p","-an",str(out)],capture_output=True)
names = sorted([p.stem for p in D.glob("*.mp4") if p.parent == D])
labels = {"tonight_show":"美式深夜秀 · 3D","roundtable":"圆桌 · 3D","snl_sketch":"SNL 小品 · 3D","taskmaster":"任务大师 · 3D",
          "traitors":"叛徒 · 3D","shark_tank":"创智赢家 · 3D","masked_singer":"蒙面唱将 · 3D","hot_ones":"辣访谈 · 3D",
          "qipashuo":"奇葩辩论 · 3D","standup":"脱口秀 · 3D","slow_life":"慢生活 · 3D"}
segs=[]
intro=OUT/"intro.png"; im=OUT/"00_intro.mp4"; card(intro, im, "造片局 · 3D 动漫风格", "11 个综艺模板 · 3D 绘景 + 3D 角色分层"); segs.append(str(im))
for i,n in enumerate(names):
    p=D/(n+".mp4")
    if not p.exists(): continue
    o=OUT/("%02d_%s.mp4"%(i,n)); seg(p,o,labels.get(n,n)); segs.append(str(o))
lst=OUT/"concat.txt"; lst.write_text("\n".join("file '%s'"%s.replace("\\","/") for s in segs)+"\n",encoding="utf-8")
show=D/"showcase_3d.mp4"
subprocess.run([str(FF),"-y","-v","error","-f","concat","-safe","0","-i",str(lst),"-c","copy","-movflags","+faststart",str(show)],capture_output=True)
print(json.dumps({"ok":show.exists(),"size_mb":round(show.stat().st_size/1048576,2) if show.exists() else 0,"segments":len(segs)}))
