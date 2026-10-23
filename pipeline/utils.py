"""通用工具：子进程、ffprobe、进度输出、哈希、JSON。"""
from __future__ import annotations

import hashlib
import json
import logging
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Iterable, Sequence

from . import config

LOG = logging.getLogger("pipeline")


def setup_logging(verbose: bool = False) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter("[%(levelname)s] %(message)s"))
    root = logging.getLogger("pipeline")
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level)
    root.propagate = False


def run(cmd: Sequence[Any], cwd: Path | str | None = None, check: bool = True,
        timeout: float | None = None, text: bool = True) -> subprocess.CompletedProcess:
    """执行命令，失败时抛 RuntimeError（包含 stderr 尾部）。"""
    argv = [str(c) for c in cmd]
    LOG.debug("$ %s", subprocess.list2cmdline(argv))
    try:
        proc = subprocess.run(
            argv,
            cwd=str(cwd) if cwd else None,
            capture_output=True,
            text=text,
            encoding="utf-8" if text else None,
            errors="replace" if text else None,
            timeout=timeout,
        )
    except FileNotFoundError as exc:  # pragma: no cover
        raise RuntimeError(f"命令不存在: {argv[0]}") from exc
    if check and proc.returncode != 0:
        err = (proc.stderr or "")[-1600:]
        raise RuntimeError(
            f"命令失败 (exit {proc.returncode}): {subprocess.list2cmdline(argv)}\n{err}"
        )
    return proc


def ffmpeg(args: Iterable[Any], cwd: Path | str | None = None, check: bool = True,
           timeout: float | None = None) -> subprocess.CompletedProcess:
    cmd = [config.FFMPEG_BIN, "-hide_banner", "-nostdin", "-loglevel", "error", "-y", *args]
    return run(cmd, cwd=cwd, check=check, timeout=timeout)


def ffprobe_json(path: Path | str, timeout: float = 60) -> dict:
    proc = run(
        [config.FFPROBE_BIN, "-v", "error", "-print_format", "json",
         "-show_format", "-show_streams", str(path)],
        timeout=timeout,
    )
    return json.loads(proc.stdout or "{}")


def probe_duration(path: Path | str) -> float:
    """媒体时长（秒）；失败返回 0.0。"""
    try:
        info = ffprobe_json(path)
        dur = float(info.get("format", {}).get("duration") or 0.0)
        return round(dur, 4)
    except Exception as exc:  # noqa: BLE001
        LOG.warning("ffprobe 读取时长失败 %s: %s", path, exc)
        return 0.0


def probe_video(path: Path | str) -> dict:
    """返回 {width,height,fps,duration,codec,has_audio}。"""
    info = ffprobe_json(path)
    out = {
        "width": 0, "height": 0, "fps": 0.0, "duration": 0.0,
        "codec": "", "has_audio": False,
    }
    for st in info.get("streams", []):
        if st.get("codec_type") == "video" and not out["codec"]:
            out["width"] = int(st.get("width") or 0)
            out["height"] = int(st.get("height") or 0)
            out["codec"] = st.get("codec_name") or ""
            rate = st.get("avg_frame_rate") or st.get("r_frame_rate") or "0/1"
            num, _, den = rate.partition("/")
            try:
                out["fps"] = round(float(num) / float(den or 1), 3)
            except ZeroDivisionError:  # pragma: no cover
                out["fps"] = 0.0
        elif st.get("codec_type") == "audio":
            out["has_audio"] = True
    try:
        out["duration"] = round(float(info.get("format", {}).get("duration") or 0.0), 4)
    except (TypeError, ValueError):
        pass
    return out


def parse_resolution(text: str | None) -> tuple[int, int]:
    if not text:
        return config.VIDEO_W, config.VIDEO_H
    t = str(text).lower().replace(" ", "")
    if "x" in t:
        a, _, b = t.partition("x")
        try:
            return int(a), int(b)
        except ValueError:
            pass
    return config.VIDEO_W, config.VIDEO_H


def sha1_text(*parts: Any) -> str:
    h = hashlib.sha1()
    for part in parts:
        h.update(str(part).encode("utf-8"))
        h.update(b"\x00")
    return h.hexdigest()[:16]


def ensure_dir(path: Path | str) -> Path:
    p = Path(path)
    p.mkdir(parents=True, exist_ok=True)
    return p


def read_json(path: Path | str, default: Any = None) -> Any:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, json.JSONDecodeError):
        return default


def write_json(path: Path | str, obj: Any, indent: int = 2) -> None:
    p = Path(path)
    ensure_dir(p.parent)
    tmp = p.with_suffix(p.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, ensure_ascii=False, indent=indent)
    tmp.replace(p)


def write_text(path: Path | str, text: str) -> None:
    p = Path(path)
    ensure_dir(p.parent)
    with open(p, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)


def emit_json_line(obj: Any) -> None:
    """stdout 输出一行 JSON（契约要求的最后一行结果）。"""
    print(json.dumps(obj, ensure_ascii=False), flush=True)


class Progress:
    """按契约向 stdout 打印进度行 PROGRESS 0-100 message。"""

    def __init__(self) -> None:
        self.current = -1.0
        self._start = time.time()

    def __call__(self, pct: float, message: str = "") -> None:
        pct = max(0.0, min(100.0, float(pct)))
        if pct <= self.current + 1e-6:
            pct = self.current
        self.current = pct
        print(f"PROGRESS {pct:.0f} {message}".rstrip(), flush=True)

    def elapsed(self) -> float:
        return time.time() - self._start


def fmt_sec(seconds: float) -> str:
    seconds = max(0.0, float(seconds))
    m, s = divmod(int(round(seconds)), 60)
    return f"{m}m{s:02d}s"


def safe_slug(text: str, max_len: int = 24) -> str:
    keep = []
    for ch in str(text):
        if ch.isalnum() or ch in "-_":
            keep.append(ch)
        else:
            keep.append("_")
    slug = "".join(keep).strip("_")
    return (slug or "x")[:max_len]
