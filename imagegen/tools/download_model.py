# -*- coding: utf-8 -*-
"""多连接分块下载（用于 HuggingFace 大模型文件，绕开单连接限速/断流）。

思路：把待下载区间按 chunk 切成很多块，用 N 个线程各自发 HTTP Range 请求，
下到临时 part 文件，全部完成后按顺序追加到目标文件；已完成的 part 会跳过，
所以中断后重跑可续传。结束后校验文件大小（可选 sha256）。

用法:
  python imagegen/tools/download_model.py \
    --url https://huggingface.co/cagliostrolab/animagine-xl-4.0/resolve/main/animagine-xl-4.0.safetensors \
    --out D:/.../animagine-xl-4.0.safetensors --connections 40 --chunk-mb 32
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed

MB = 1024 * 1024


def http_head(url: str, timeout: float = 60.0):
    """HEAD 跟随重定向，拿到最终 CDN URL / 总大小 / Accept-Ranges。"""
    req = urllib.request.Request(url, method="HEAD")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.geturl(), {k.lower(): v for k, v in r.headers.items()}


class Resolver:
    """缓存最终 URL，遇到签名过期(403)时重新解析。"""

    def __init__(self, url: str):
        self.src = url
        self.lock = threading.Lock()
        self.url, self.headers = http_head(url)

    def get(self):
        with self.lock:
            return self.url

    def refresh(self):
        with self.lock:
            self.url, self.headers = http_head(self.src)
            return self.url

    @property
    def total(self) -> int:
        return int(self.headers.get("content-length") or 0)


def fetch_range(resolver: Resolver, start: int, end: int, dest: str,
                retries: int = 8, timeout: float = 180.0) -> int:
    """下载 [start, end] 闭区间到 dest；返回字节数。"""
    want = end - start + 1
    last = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(resolver.get(), headers={"Range": "bytes=%d-%d" % (start, end)})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                if r.status != 206:
                    raise RuntimeError("expected 206, got %s" % r.status)
                left = want
                with open(dest + ".tmp", "wb") as f:
                    while left > 0:
                        buf = r.read(min(MB, left))
                        if not buf:
                            break
                        f.write(buf)
                        left -= len(buf)
            if left != 0:
                raise RuntimeError("short read, left=%d" % left)
            os.replace(dest + ".tmp", dest)
            return want
        except Exception as e:  # noqa: BLE001
            last = e
            code = getattr(e, "code", None)
            if code in (401, 403, 416):
                try:
                    resolver.refresh()
                except Exception:
                    pass
            time.sleep(min(20, 2 * (attempt + 1)))
    raise RuntimeError("chunk %d-%d 失败: %r" % (start, end, last))


def sha256_file(path: str, block: int = 8 * MB) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            b = f.read(block)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--sha256", default=None)
    ap.add_argument("--connections", type=int, default=40)
    ap.add_argument("--chunk-mb", type=int, default=32)
    ap.add_argument("--parts-dir", default=None)
    ap.add_argument("--retries", type=int, default=8)
    args = ap.parse_args(argv)

    out = os.path.abspath(args.out)
    os.makedirs(os.path.dirname(out), exist_ok=True)
    parts_dir = args.parts_dir or (out + ".parts")
    os.makedirs(parts_dir, exist_ok=True)

    resolver = Resolver(args.url)
    total = resolver.total
    if total <= 0:
        raise SystemExit("无法获取文件大小（HEAD 没有 content-length）")
    start_off = os.path.getsize(out) if os.path.exists(out) else 0
    if start_off > total:
        print("[warn] 现有文件比目标大，从头下载")
        start_off = 0
    print("[start] %s  total=%d bytes (%.1f MB)  existing=%.1f MB  conns=%d chunk=%dMB"
          % (out, total, total / MB, start_off / MB, args.connections, args.chunk_mb), flush=True)
    if start_off == total:
        pass
    else:
        chunk = args.chunk_mb * MB
        ranges = [(s, min(s + chunk - 1, total - 1)) for s in range(start_off, total, chunk)]
        todo = []
        for (s, e) in ranges:
            part = os.path.join(parts_dir, "%012d.part" % s)
            if os.path.isfile(part) and os.path.getsize(part) == (e - s + 1):
                continue
            todo.append((s, e, part))
        print("[plan] %d chunks, %d 待下载（%d 已完成缓存）"
              % (len(ranges), len(todo), len(ranges) - len(todo)), flush=True)

        t0 = time.time()
        done = [0]
        lock = threading.Lock()

        def work(item):
            s, e, part = item
            n = fetch_range(resolver, s, e, part, retries=args.retries)
            with lock:
                done[0] += n
            return n

        failures = []
        last_log = [0.0]
        with ThreadPoolExecutor(max_workers=max(1, args.connections)) as ex:
            futs = {ex.submit(work, it): it for it in todo}
            for fut in as_completed(futs):
                try:
                    fut.result()
                except Exception as e:  # noqa: BLE001
                    failures.append(str(e))
                now = time.time()
                if now - last_log[0] >= 5:
                    el = now - t0
                    rate = done[0] / el / MB if el > 0 else 0
                    remain = (total - start_off) - done[0]
                    eta = remain / (rate * MB) if rate > 0 else 0
                    print("[progress] %.0f/%.0f MB  %.2f MB/s  eta %dm%02ds"
                          % (done[0] / MB, (total - start_off) / MB, rate,
                             int(eta // 60), int(eta % 60)), flush=True)
                    last_log[0] = now
        if failures:
            print("[error] %d 个分块失败，保留 parts 供续传:" % len(failures), flush=True)
            for f in failures[:10]:
                print("  " + f, flush=True)
            return 2

        # 按偏移顺序追加
        print("[assemble] 追加 %d 个 part ..." % len(ranges), flush=True)
        with open(out, "ab") as fo:
            for (s, e) in ranges:
                part = os.path.join(parts_dir, "%012d.part" % s)
                with open(part, "rb") as fi:
                    while True:
                        b = fi.read(8 * MB)
                        if not b:
                            break
                        fo.write(b)
            fo.flush()
            os.fsync(fo.fileno())
        import shutil
        shutil.rmtree(parts_dir, ignore_errors=True)

    size = os.path.getsize(out)
    result = {"ok": size == total, "out": out, "bytes": size, "expected": total,
              "elapsed_sec": None}
    if size != total:
        print(json.dumps(result, ensure_ascii=False))
        return 3
    if args.sha256:
        print("[verify] 计算 sha256 ...", flush=True)
        got = sha256_file(out)
        result["sha256"] = got
        result["sha256_ok"] = (got == args.sha256)
        if not result["sha256_ok"]:
            print(json.dumps(result, ensure_ascii=False))
            return 4
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
