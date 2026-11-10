"""通用工具：子进程、ffprobe、进度输出、哈希、JSON。"""
from __future__ import annotations

import hashlib
import json
import logging
import math
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
            num, _, den = str(rate).partition("/")
            try:
                out["fps"] = round(float(num) / float(den or 1), 3)
            except (ValueError, ZeroDivisionError):
                # ffprobe 可能给 "N/A" / "0/0"，不能让它把整条链路打成 500
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


#: gbk 兜底只对「人手写的小文件」生效：程序写的大文件基本是 utf-8，
#: 用 gbk 去"猜"只会把损坏文件解成一堆乱码（R8 遗留问题）
GBK_FALLBACK_MAX_BYTES = 8 * 1024


def read_json(path: Path | str, default: Any = None) -> Any:
    # 缺文件/空文件是常态（缓存未生成），静默返回默认值，不当异常刷日志
    try:
        p = Path(path)
        if not p.is_file():
            return default
        size = p.stat().st_size
        if size == 0:
            return default
    except OSError:
        return default
    # utf-8-sig：兼容带 BOM 的 JSON（PowerShell/记事本写出来的常见）
    for enc in ("utf-8-sig", "utf-8"):
        try:
            with open(path, "r", encoding=enc) as fh:
                return json.load(fh)
        except (OSError, json.JSONDecodeError, UnicodeDecodeError):
            continue
    # gbk 兜底：只对 <8KB 的小文件尝试一次，且必须打 warning
    if size <= GBK_FALLBACK_MAX_BYTES:
        try:
            with open(path, "r", encoding="gbk") as fh:
                data = json.load(fh)
        except (OSError, json.JSONDecodeError, UnicodeDecodeError) as exc:
            LOG.warning("读取 JSON 失败（utf-8/gbk 都不通，按损坏处理返回默认值）: %s（%s）",
                        path, type(exc).__name__)
            return default
        LOG.warning("JSON 非 UTF-8，已按 gbk 兜底读取（%.1fKB < 8KB，请确认不是乱码）: %s",
                    size / 1024.0, path)
        return data
    LOG.warning("读取 JSON 失败（%.1fKB ≥ 8KB，不再尝试 gbk 乱码猜测）: %s",
                size / 1024.0, path)
    return default


def _finite(value: Any) -> Any:
    """递归把 NaN/Infinity 变成 None（JSON 不支持这两个字面量）。"""
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, dict):
        return {k: _finite(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_finite(v) for v in value]
    return value


def write_json(path: Path | str, obj: Any, indent: int = 2) -> None:
    p = Path(path)
    ensure_dir(p.parent)
    tmp = p.with_suffix(p.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        # NaN/Infinity 不是合法 JSON：写盘前统一转成 null，避免前端 JSON.parse 崩
        json.dump(_finite(obj), fh, ensure_ascii=False, indent=indent, allow_nan=False)
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
