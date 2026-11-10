# -*- coding: utf-8 -*-
"""AUDIT_render 静态核验 5：muse_video.available() 的 provider 解析 / _local_generate 的 shell=True 拼接。"""
import os, sys, subprocess
from pathlib import Path
REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO))
for k in ("MUSE_API_URL","REPLICATE_API_TOKEN","USEAPI_TOKEN","MINIMAX_API_KEY","METASO_API_KEY","METASO_KEY","MUSEV_CMD","MUSEPOSE_CMD"):
    os.environ.pop(k, None)
from pipeline import muse_video as mv

st = mv.available()
print("== 无任何密钥时 available() ==")
print("ok =", st["ok"], " provider =", st["provider"], " docs =", Path(str(st.get('docs'))).name)
print("metaso_h3.configured =", st["checks"]["metaso_h3"]["configured"],
      " minimax.configured =", st["checks"]["minimax_cloud"]["configured"])
print("→ generate_video({}) 会直接分发给:", st["provider"])
try:
    mv.generate_video({"prompt": "x"})
except Exception as exc:
    print("   实际报错:", str(exc)[:120])

print()
print("== _local_generate 的 shell=True 拼接（用无害命令复现）==")
prompt = '赛博朋克 城市 & echo INJECTED'
template = 'python -c "import sys;print(sys.argv[1])" "{prompt}"'
cmd = template.format(prompt=prompt, image="", pose="", duration="4", out="out.mp4",
                      seed="1", width="512", height="768")
print("cmd =", cmd)
p = subprocess.run(cmd, shell=True, capture_output=True, text=True, encoding="utf-8", errors="replace")
print("rc =", p.returncode)
print("stdout =", repr((p.stdout or "").strip()[:200]))
print("stderr =", repr((p.stderr or "").strip()[:200]))
