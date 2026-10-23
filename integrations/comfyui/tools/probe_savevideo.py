# -*- coding: utf-8 -*-
"""快速验证 SaveVideo 动态控件 (format.format / format.codec) 在 API prompt 里的正确写法。
LoadImage(example.png) -> CreateVideo(fps=24) -> SaveVideo，几秒内可完成，不占显存。"""
import json, sys, time, urllib.request, urllib.error, uuid, os, shutil

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8188"

def post(path, payload):
    req = urllib.request.Request(BASE + path, data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode())

def get(path):
    with urllib.request.urlopen(BASE + path, timeout=60) as r:
        return json.loads(r.read().decode())

prompt = {
  "1": {"class_type": "LoadImage", "inputs": {"image": "example.png"}},
  "2": {"class_type": "CreateVideo", "inputs": {"images": ["1", 0], "fps": 24,
                                                "bit_depth": 8, "color_space": "sRGB", "codec": "none"}},
  "3": {"class_type": "SaveVideo", "inputs": {
        "video": ["2", 0], "filename_prefix": "zaopianju/_probe_savevideo",
        "format": "mp4", "format.codec": "h264", "format.codec.encoding": "auto", "codec": "auto"}},
}
status, body = post("/prompt", {"prompt": prompt, "client_id": uuid.uuid4().hex})
print("SUBMIT", status, json.dumps(body, ensure_ascii=False)[:3000])
if status != 200 or "prompt_id" not in body:
    sys.exit(2)
pid = body["prompt_id"]
for i in range(60):
    time.sleep(1)
    h = get("/history/" + pid)
    if pid in h:
        rec = h[pid]
        print("DONE", json.dumps(rec.get("status"), ensure_ascii=False))
        print("OUTPUTS", json.dumps(rec.get("outputs"), ensure_ascii=False))
        sys.exit(0)
print("TIMEOUT")
sys.exit(1)
