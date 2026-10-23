"""字体准备：把系统字体复制到 workdir/fonts，供 drawtext / libass 用相对路径加载。

Windows 上 ffmpeg 的 filter 参数用绝对路径会因盘符冒号解析失败，
因此统一约定：以 workdir 为 cwd，filter 内只用相对路径 fonts/xxx.ttf。
"""
from __future__ import annotations

import math
import shutil
from pathlib import Path

from . import config, utils

LOG = __import__("logging").getLogger("pipeline")

FONTS_DIRNAME = "fonts"


def _color_eq(c0: str, c1: str) -> bool:
    return (c0 or "").lower() == (c1 or "").lower()


def _copy_font(src_path: Path, dst: Path) -> None:
    """原子复制：先写 .tmp 再 replace，避免多进程/多线程并发读到半个文件。"""
    if dst.exists() and dst.stat().st_size == src_path.stat().st_size:
        return
    tmp = dst.with_suffix(dst.suffix + ".tmp")
    shutil.copyfile(src_path, tmp)
    tmp.replace(dst)


def ensure_fonts(scratch_dir: Path | str) -> dict:
    """把正文 + 标题两个中文字体复制到 <scratch>/fonts（最多 2 个文件，约 32MB）。

    body: {"file": "fonts/Deng.ttf", "name": "Deng.ttf", "family": "DengXian"}
    display: 用于标题/花字的字体（优先 msyhbd/Deng）
    """
    scratch = Path(scratch_dir)
    font_dir = utils.ensure_dir(scratch / FONTS_DIRNAME)
    candidates = []
    for src, family in config.FONT_CANDIDATES:
        src_path = Path(src)
        try:
            if src_path.exists():
                candidates.append((src_path, family))
        except OSError:
            continue
    if not candidates:
        raise RuntimeError(
            "找不到可用的中文字体；请在 C:/Windows/Fonts 放置 Deng.ttf/msyh.ttc"
        )
    body_src, body_family = candidates[0]
    display_src, display_family = body_src, body_family
    for src_path, family in candidates:
        if "msyhbd" in src_path.name.lower():
            display_src, display_family = src_path, family
            break
    wanted = [body_src] if display_src == body_src else [body_src, display_src]
    found: list[dict] = []
    for src_path, family in ((body_src, body_family), (display_src, display_family)):
        if any(f["name"] == src_path.name for f in found):
            continue
        dst = font_dir / src_path.name
        try:
            _copy_font(src_path, dst)
            found.append({
                "file": f"{FONTS_DIRNAME}/{dst.name}",
                "name": dst.name,
                "family": family,
            })
        except OSError as exc:
            LOG.warning("复制字体失败 %s: %s", src_path, exc)
    if not found:
        raise RuntimeError("中文字体复制失败")
    # 清理历史版本遗留的多余字体（早期版本会复制 5 个字体，约 60MB）
    wanted_names = {p.name for p in wanted}
    for stale in font_dir.glob("*"):
        if stale.is_file() and stale.suffix.lower() in (".ttf", ".ttc", ".otf") \
                and stale.name not in wanted_names:
            try:
                stale.unlink()
                LOG.info("清理多余字体缓存: %s", stale.name)
            except OSError:
                pass
    body = found[0]
    display = found[1] if len(found) > 1 else body
    return {"dir": font_dir, "body": body, "display": display, "all": found}
