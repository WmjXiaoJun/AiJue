# -*- coding: utf-8 -*-
"""MetaSo MiniMax H3 云端视频生成 CLI（测试/快速迭代用；生产默认仍走本地 ComfyUI）。

用法:
  python -m integrations.metaso.h3 --image outputs/concepts/laozhou/portrait.png \
    --prompt "anime style, cel shaded, ..." --out outputs/h3/metaso/laozhou_metaso.mp4 \
    [--resolution 768P|2K] [--duration 5] [--ratio 16:9] [--timeout 900]

协议（与 meta-sota/ComfyUI-MiniMaxH3-API 一致）:
  base_url = https://metaso.cn/api/minimax
  POST /v1/files/upload (multipart: purpose=video_generation_input, file)
  POST /v2/video_generation {model, content[], resolution, duration, ratio?}
  GET  /v2/query/video_generation/{task_id}

Key 优先级: --key > 环境变量 METASO_API_KEY > backend/data/metaso.env > integrations/metaso/.env
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

try:
    import httpx
except ImportError:  # pragma: no cover
    print("需要 httpx: pip install httpx", file=sys.stderr)
    raise

REPO = Path(__file__).resolve().parents[2]
BASE_DEFAULT = "https://metaso.cn/api/minimax"
MODEL = "MiniMax-H3"
PRICE_PER_SEC = {"768P": 0.09, "2K": 0.15}
FFPROBE = REPO / "tools" / "node_modules" / "ffprobe-static" / "bin" / "win32" / "x64" / "ffprobe.exe"


def load_key(cli_key: str | None) -> str:
    if cli_key:
        return cli_key
    if os.environ.get("METASO_API_KEY"):
        return os.environ["METASO_API_KEY"]
    for p in (REPO / "backend" / "data" / "metaso.env", REPO / "integrations" / "metaso" / ".env"):
        if p.exists():
            for line in p.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    if k.strip().upper() in ("METASO_API_KEY", "KEY", "TOKEN"):
                        return v.strip().strip('"').strip("'")
    raise SystemExit("缺少 MetaSo API Key：--key / METASO_API_KEY / backend/data/metaso.env")


def probe(path: Path) -> dict:
    try:
        out = subprocess.run([str(FFPROBE), "-v", "error", "-show_entries", "format=duration,size", "-show_entries",
                              "stream=codec_type,codec_name,width,height", "-of", "json", str(path)],
                             capture_output=True, text=True, timeout=60)
        return json.loads(out.stdout or "{}")
    except Exception as exc:  # noqa: BLE001
        return {"error": str(exc)}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--image", default=None, help="首帧图片路径（不给则文生视频）")
    ap.add_argument("--prompt", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--resolution", default="768P", choices=["768P", "2K"])
    ap.add_argument("--duration", type=int, default=5, choices=range(4, 16), metavar="4-15")
    ap.add_argument("--ratio", default=None, help="文生视频必填，如 16:9；图生视频可不填（跟随图片）")
    ap.add_argument("--base-url", default=os.environ.get("METASO_BASE_URL", BASE_DEFAULT))
    ap.add_argument("--key", default=None)
    ap.add_argument("--timeout", type=float, default=900.0)
    ap.add_argument("--meta", default=None, help="meta json 输出路径（默认 <out>.meta.json）")
    args = ap.parse_args(argv)

    key = load_key(args.key)
    base = args.base_url.rstrip("/")
    headers = {"Authorization": f"Bearer {key}"}
    t_start = time.time()
    timeline = {}

    content = [{"type": "text", "text": args.prompt}]
    image_info = None
    if args.image:
        img = Path(args.image)
        if not img.exists():
            raise SystemExit(f"图片不存在: {img}")
        t0 = time.time()
        with open(img, "rb") as f:
            r = httpx.post(base + "/v1/files/upload", headers=headers,
                           data={"purpose": "video_generation_input"},
                           files={"file": (img.name, f, "image/png")}, timeout=180)
        if r.status_code >= 400:
            raise SystemExit(f"上传失败 HTTP {r.status_code}: {r.text[:500]}")
        j = r.json()
        fobj = j.get("file") or {}
        fid = fobj.get("file_id")
        url = ("mm_file://" + str(fid)) if fid is not None else fobj.get("download_url")
        if not url:
            raise SystemExit("上传返回无 file_id/download_url: " + r.text[:400])
        image_info = {"path": str(img), "file_id": fid, "url": url, "upload_sec": round(time.time() - t0, 2)}
        content.append({"type": "image_url", "image_url": {"url": url}, "role": "first_frame"})
        timeline["upload_sec"] = image_info["upload_sec"]
        print(f"[upload] {img.name} -> {url} ({image_info['upload_sec']}s)", file=sys.stderr)

    if not args.image and not args.ratio:
        args.ratio = "16:9"
    payload = {"model": MODEL, "content": content, "resolution": args.resolution, "duration": args.duration}
    if args.ratio:
        payload["ratio"] = args.ratio
    t0 = time.time()
    r = httpx.post(base + "/v2/video_generation", headers={**headers, "Content-Type": "application/json"},
                   json=payload, timeout=180)
    if r.status_code >= 400:
        raise SystemExit(f"创建任务失败 HTTP {r.status_code}: {r.text[:600]}")
    j = r.json()
    task_id = j.get("task_id") or (j.get("task") or {}).get("id")
    if not task_id:
        raise SystemExit("创建任务无 task_id: " + r.text[:600])
    timeline["create_sec"] = round(time.time() - t0, 2)
    print(f"[create] task_id={task_id} ({timeline['create_sec']}s)", file=sys.stderr)

    deadline = time.time() + args.timeout
    video_url = None
    while time.time() < deadline:
        time.sleep(5)
        rr = httpx.get(f"{base}/v2/query/video_generation/{task_id}", headers=headers, timeout=60)
        jj = rr.json()
        task = jj.get("task") or {}
        st = str(task.get("status", "")).lower()
        video_url = (task.get("content") or {}).get("url")
        elapsed = time.time() - t_start
        print(f"[poll] {elapsed:5.1f}s status={st}", file=sys.stderr)
        timeline["poll_sec"] = round(elapsed, 2)
        if video_url:
            break
        if st in ("failed", "fail", "cancelled", "canceled", "expired", "error"):
            raise SystemExit(f"任务失败: {json.dumps(jj, ensure_ascii=False)[:800]}")
    if not video_url:
        raise SystemExit(f"超时（{args.timeout}s）未完成，task_id={task_id}")

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    with httpx.stream("GET", video_url, timeout=300) as rr:
        rr.raise_for_status()
        with open(out, "wb") as f:
            for chunk in rr.iter_bytes(1 << 20):
                f.write(chunk)
    timeline["download_sec"] = round(time.time() - t0, 2)
    duration = args.duration
    cost = round(PRICE_PER_SEC.get(args.resolution, 0.09) * duration, 3)
    meta = {
        "ok": True, "provider": "metaso", "base_url": base, "model": MODEL, "task_id": task_id,
        "out": str(out), "size": out.stat().st_size,
        "image": image_info, "prompt": args.prompt,
        "resolution": args.resolution, "duration": duration, "ratio": args.ratio,
        "timeline": timeline, "elapsed_sec": round(time.time() - t_start, 1),
        "est_cost_cny": cost, "video_url": video_url, "probe": probe(out),
    }
    meta_path = Path(args.meta) if args.meta else Path(str(out) + ".meta.json")
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(meta, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
