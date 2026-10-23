"""mock 镜头生成：ffmpeg lavfi 背景 + drawtext 差异化视觉。

按镜头类型给出不同画风（全部离线、单镜头约 0.5-2 秒生成）：
- title_card    暖黄径向渐变 + 大字标题 + 品牌角标
- host_to_camera 暖黄灯光 + 主持人名牌 + 大号水印名
- shot_reverse  蓝/红左右渐变 + 当前说话人高亮 + 双方标签
- reaction      暗红聚光 + REACTION 标签 + 说话人小标
- b_roll        冷蓝科技网格 + 扫描线 + 时间码
- meme_card     黑底 + 花字大字（punchline）
- end_card      品牌收尾 + 下期见（字幕阶段补）
- guest_intro   单人大字介绍（兜底）

约定：以 scratch_dir 为 ffmpeg cwd，filter 只用相对路径（fonts/、text/），
避免 Windows 盘符冒号在 filter 参数里被误解析。
"""
from __future__ import annotations

import logging
from pathlib import Path

from . import config, fonts, utils

LOG = logging.getLogger("pipeline")


def _hex_to_ff(color: str, default: str = "0xF5C518") -> str:
    """#RRGGBB -> 0xRRGGBB。"""
    c = (color or "").strip()
    if c.startswith("#") and len(c) == 7:
        return "0x" + c[1:].upper()
    if c.startswith("0x"):
        return c
    return default


def _q(value) -> str:
    """filter 表达式加引号（防逗号/冒号被当作分隔符）。"""
    return "'" + str(value).replace("'", "") + "'"


class _TextFiles:
    """把要绘制的文本写成 txt，drawtext 用 textfile= 加载（彻底避开转义）。"""

    def __init__(self, scratch: Path, prefix: str = "t") -> None:
        self.scratch = Path(scratch)
        self.dir = utils.ensure_dir(self.scratch / "text")
        self.prefix = utils.safe_slug(prefix, 16)
        self.count = 0

    def put(self, text: str, tag: str = "t") -> str:
        self.count += 1
        name = f"{self.prefix}_{utils.safe_slug(tag, 32)}_{self.count:03d}.txt"
        utils.write_text(self.dir / name, str(text))
        return f"text/{name}"


def _dt(textfile: str, *, size: int, color: str, x, y, font: str,
        border: int = 0, border_color: str = "black",
        shadow=None, alpha: str | None = None) -> str:
    parts = [
        f"fontfile={font}",
        f"textfile={textfile}",
        f"fontsize={int(size)}",
        f"fontcolor={color}",
        f"x={_q(x)}",
        f"y={_q(y)}",
    ]
    if border:
        parts += [f"borderw={int(border)}", f"bordercolor={border_color}"]
    if shadow:
        parts += [
            f"shadowx={int(shadow[0])}",
            f"shadowy={int(shadow[1])}",
            f"shadowcolor={shadow[2]}",
        ]
    if alpha is not None:
        parts += [f"alpha={_q(alpha)}"]
    return "drawtext=" + ":".join(parts)


def _box(x, y, w, h, color: str, thickness: int | None = None) -> str:
    t = str(thickness) if thickness else "fill"
    return f"drawbox=x={_q(x)}:y={_q(y)}:w={_q(w)}:h={_q(h)}:color={color}:t={t}"


def _fit_size(text: str, base: int, steps) -> int:
    """按字符数从 (阈值, 系数) 列表里挑字号。"""
    n = len(text or "")
    for limit, factor in steps:
        if n <= limit:
            return max(20, int(base * factor))
    return max(20, int(base * steps[-1][1]))


def _shorten(text: str, limit: int = 30) -> str:
    text = str(text or "").replace("\n", " ").strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"


def _asset_color(asset: dict, default: str = config.ACCENT_DEFAULT) -> str:
    return _hex_to_ff(asset.get("accent_color") or default)


def _role_text(asset: dict) -> str:
    if not asset:
        return "嘉宾"
    return str(asset.get("title") or ("主持人" if asset.get("role") == "host" else "嘉宾"))


class _ShotCtx:
    def __init__(self, shot: dict, assets: list, scratch: Path, font_info: dict,
                 width: int, height: int, duration: float, fps: int) -> None:
        self.shot = shot or {}
        self.assets = [a for a in (assets or []) if isinstance(a, dict)]
        self.scratch = scratch
        self.font_body = font_info["body"]["file"]
        self.font_display = font_info["display"]["file"]
        self.w = width
        self.h = height
        self.dur = duration
        self.fps = fps
        self.texts = _TextFiles(scratch, utils.safe_slug(self.shot.get("id") or "shot", 12))
        self.type = str(self.shot.get("type") or "host_to_camera")
        self.speaker_id = self.shot.get("speaker") or ""
        self.speaker = self._asset(self.speaker_id)
        self.speaker_name = str(self.shot.get("speaker_name") or self.speaker.get("name") or "")
        self.visual = _shorten(self.shot.get("visual") or "", 30)

        chars = [a for a in self.assets if a.get("type") == "character"]
        self.hosts = [a for a in chars if a.get("role") == "host"]
        self.guests = [a for a in chars if a.get("role") != "host"]
        self.left = self.guests[0] if len(self.guests) >= 1 else (self.hosts[0] if self.hosts else {})
        self.right = self.guests[1] if len(self.guests) >= 2 else {}
        self.accent = _asset_color(self.speaker) if self.speaker else config.ACCENT_DEFAULT

    # ---- 小工具 ----------------------------------------------------------
    def _asset(self, aid: str) -> dict:
        for a in self.assets:
            if a.get("id") == aid:
                return a
        return {}

    def _accent_of(self, asset: dict) -> str:
        return _asset_color(asset)

    def _brand(self, parts: list, opacity: str = "white@0.72") -> None:
        accent = config.ACCENT_HOST
        parts.append(_box(46, 32, 6, 30, accent))
        parts.append(_dt(self.texts.put(f"造片局 · AI 微综艺", "brand"),
                         size=26, color=opacity, x=64, y=35,
                         font=self.font_body, border=1, border_color="black@0.35"))

    def _caption(self, parts: list) -> None:
        if not self.visual:
            return
        parts.append(_dt(self.texts.put(self.visual, "cap"),
                         size=23, color="white@0.34", x=f"w-tw-46", y=38,
                         font=self.font_body, border=1, border_color="black@0.25"))

    # ---- 各类型视觉 ------------------------------------------------------
    def _build_title_card(self, parts: list) -> None:
        accent = config.ACCENT_HOST
        title = str(self.shot.get("title") or self.shot.get("subtitle")
                    or config.BRAND_NAME)
        ep_no = int(self.shot.get("episode_no") or 1)
        parts.append(_dt(self.texts.put(f"造片局 出品  ·  EP.{ep_no:03d}", "top"),
                         size=27, color="white@0.6", x=46, y=32,
                         font=self.font_body, border=1, border_color="black@0.4"))
        parts.append(_box(46, 68, 300, 3, "0xFFFFFF@0.25"))
        size = _fit_size(title, 88, [(8, 1.0), (12, 0.88), (17, 0.74), (99, 0.62)])
        parts.append(_dt(self.texts.put(title, "title"), size=size,
                         color="white", x="(w-tw)/2", y="(h-th)/2-64",
                         font=self.font_display, border=5, border_color="black@0.85",
                         shadow=(0, 5, "black@0.55"),
                         alpha="min(1,max(0,t/0.45))"))
        parts.append(_box("(iw-420)/2", 470, 420, 5, accent))
        parts.append(_box("(iw-70)/2", 483, 70, 3, "0xFFFFFF@0.5"))
        parts.append("fade=t=in:st=0:d=0.5")

    def _build_end_card(self, parts: list) -> None:
        accent = config.ACCENT_HOST
        parts.append(_dt(self.texts.put(config.BRAND_NAME, "brandname"),
                         size=132, color="white", x="(w-tw)/2", y="(h-th)/2-96",
                         font=self.font_display, border=4, border_color="black@0.6",
                         shadow=(0, 6, "black@0.5"),
                         alpha="min(1,max(0,t/0.55))"))
        parts.append(_box("(iw-460)/2", 470, 460, 5, accent))
        parts.append(_dt(self.texts.put(config.BRAND_TAGLINE, "tagline"),
                         size=48, color=accent, x="(w-tw)/2", y=496,
                         font=self.font_body, border=2, border_color="black@0.5"))
        parts.append(_dt(self.texts.put("第 001 期", "next"),
                         size=28, color="white@0.6", x="(w-tw)/2", y=572,
                         font=self.font_body, alpha="min(1,max(0,(t-0.5)/0.6))"))
        parts.append(f"fade=t=in:st=0:d=0.5")
        parts.append(f"fade=t=out:st={max(0.0, self.dur - 1.2):.3f}:d=1.2")

    def _build_host_to_camera(self, parts: list) -> None:
        accent = self.accent
        name = self.speaker_name or "主持人"
        role = _role_text(self.speaker) if self.speaker else "主持人"
        if name:
            parts.append(_dt(self.texts.put(name, "wm"), size=300,
                             color="white@0.055", x="(w-tw)/2", y="(h-th)/2-40",
                             font=self.font_display))
        self._brand(parts)
        self._caption(parts)
        parts.append(_box(62, 556, 8, 96, accent))
        parts.append(_dt(self.texts.put(name, "name"), size=48, color=accent,
                         x=86, y=562, font=self.font_display, border=3,
                         border_color="black@0.7", shadow=(0, 3, "black@0.5"),
                         alpha="min(1,max(0,(t-0.15)/0.4))"))
        parts.append(_dt(self.texts.put(role, "role"), size=27,
                         color="white@0.82", x=88, y=622, font=self.font_body,
                         border=1, border_color="black@0.5",
                         alpha="min(1,max(0,(t-0.3)/0.4))"))
        parts.append("fade=t=in:st=0:d=0.35")

    def _build_shot_reverse(self, parts: list) -> None:
        accent = self.accent
        name = self.speaker_name or "嘉宾"
        role = _role_text(self.speaker) if self.speaker else "嘉宾"
        self._brand(parts)
        self._caption(parts)
        parts.append(_box(638, 0, 4, 720, "0xFFFFFF@0.12"))
        parts.append(_dt(self.texts.put(name, "name"), size=86, color=accent,
                         x="(w-tw)/2", y="(h-th)/2-90",
                         font=self.font_display, border=5,
                         border_color="black@0.8", shadow=(0, 4, "black@0.55"),
                         alpha="min(1,max(0,t/0.35))"))
        parts.append(_dt(self.texts.put(f"● 发言中  ·  {role}", "status"),
                         size=29, color="white@0.85", x="(w-tw)/2", y="(h/2)+18",
                         font=self.font_body, border=2, border_color="black@0.6",
                         alpha="min(1,max(0,(t-0.25)/0.4))"))
        # 左侧嘉宾
        lname = str(self.left.get("name") or "")
        if lname:
            active = self.speaker_id == self.left.get("id")
            lcolor = self._accent_of(self.left) if active else "white@0.45"
            lrole = _role_text(self.left)
            parts.append(_dt(self.texts.put(lname, "lname"), size=34,
                             color=lcolor, x=62, y=590, font=self.font_body,
                             border=2, border_color="black@0.6"))
            parts.append(_dt(self.texts.put(lrole, "lrole"), size=22,
                             color="white@0.4", x=64, y=636,
                             font=self.font_body, border=1,
                             border_color="black@0.4"))
            if active:
                parts.append(_box(62, 578, 74, 4, self._accent_of(self.left)))
        # 右侧嘉宾
        rname = str(self.right.get("name") or "")
        if rname:
            active = self.speaker_id == self.right.get("id")
            rcolor = self._accent_of(self.right) if active else "white@0.45"
            rrole = _role_text(self.right)
            parts.append(_dt(self.texts.put(rname, "rname"), size=34,
                             color=rcolor, x="w-tw-62", y=590,
                             font=self.font_body, border=2,
                             border_color="black@0.6"))
            parts.append(_dt(self.texts.put(rrole, "rrole"), size=22,
                             color="white@0.4", x="w-tw-60", y=636,
                             font=self.font_body, border=1,
                             border_color="black@0.4"))
            if active:
                parts.append(_box("iw-136", 578, 74, 4, self._accent_of(self.right)))
        parts.append("fade=t=in:st=0:d=0.3")

    def _build_reaction(self, parts: list) -> None:
        accent = self.accent
        name = self.speaker_name or "嘉宾"
        self._brand(parts)
        self._caption(parts)
        parts.append(_dt(self.texts.put("REACTION · 特写", "label"), size=40,
                         color="white@0.5", x="(w-tw)/2", y="(h-th)/2-10",
                         font=self.font_body, border=2,
                         border_color="black@0.5",
                         alpha="min(1,max(0,t/0.3))"))
        parts.append(_dt(self.texts.put(name, "name"), size=48, color=accent,
                         x="w-tw-70", y="h-th-150",
                         font=self.font_display, border=3,
                         border_color="black@0.75", shadow=(0, 3, "black@0.5")))
        parts.append(_dt(self.texts.put("反应镜头", "role"), size=24,
                         color="white@0.5", x="w-tw-68", y="h-th-84",
                         font=self.font_body))
        parts.append("fade=t=in:st=0:d=0.25")

    def _build_b_roll(self, parts: list) -> None:
        cy = "0x5AC8FA"
        self._brand(parts)
        parts.append("drawgrid=w=80:h=80:t=1:c=0x5AC8FA@0.10")
        parts.append(_box(0, "mod(t*300,720)", 1280, 10, "0x5AC8FA@0.16"))
        parts.append(_box(0, 0, 1280, 4, "0x5AC8FA@0.22"))
        parts.append(_dt(self.texts.put("B-ROLL", "br"), size=98, color="0x5AC8FA@0.8",
                         x=80, y=240, font=self.font_display, border=3,
                         border_color="black@0.45",
                         alpha="min(1,max(0,t/0.4))"))
        parts.append(_box(84, 372, 110, 4, cy))
        scene = _shorten(self.shot.get("visual") or "剪辑机房 · 服务器指示灯", 30)
        parts.append(_dt(self.texts.put(scene, "scene"), size=38,
                         color="white@0.72", x=84, y=394,
                         font=self.font_body, border=2,
                         border_color="black@0.5"))
        parts.append(_dt(self.texts.put("● REC  TC %{pts:hms}", "rec"), size=25,
                         color="0xFF5A5A", x="w-tw-46", y=38,
                         font=self.font_body, border=1,
                         border_color="black@0.4",
                         alpha="if(lt(mod(t,1.2),0.75),0.95,0.25)"))
        parts.append(_box(0, "ih-92", 1280, 2, "0x5AC8FA@0.18"))
        parts.append("fade=t=in:st=0:d=0.3")

    def _build_meme_card(self, parts: list) -> None:
        accent = config.ACCENT_HOST
        punch = str(self.shot.get("punchline") or self.shot.get("subtitle") or "花字")
        parts.append(_box(46, 32, 6, 30, accent))
        parts.append(_dt(self.texts.put("花字  MEME", "tag"), size=26,
                         color="white@0.42", x=64, y=35,
                         font=self.font_body, border=1, border_color="black@0.3"))
        parts.append(_dt(self.texts.put("造片局", "wm"), size=26,
                         color="white@0.4", x="w-tw-46", y="h-th-42",
                         font=self.font_body))
        top_y = 168 if len(punch) <= 8 else 128
        size = _fit_size(punch, 108, [(6, 1.0), (10, 0.86), (14, 0.72), (99, 0.6)])
        parts.append(_box("(iw-120)/2", top_y, 120, 6, accent))
        parts.append(_dt(self.texts.put(punch, "punch"), size=size,
                         color=accent, x="(w-tw)/2", y="(h-th)/2",
                         font=self.font_display, border=6,
                         border_color="white@0.9", shadow=(5, 5, "black@0.8"),
                         alpha="min(1,max(0,t/0.28))"))
        parts.append(_box("(iw-120)/2", "ih/2+150", 120, 6, accent))
        parts.append("fade=t=in:st=0:d=0.25")

    def _build_guest_intro(self, parts: list) -> None:
        accent = self.accent
        name = self.speaker_name or str(self.speaker.get("name") or "嘉宾")
        role = _role_text(self.speaker) if self.speaker else "嘉宾"
        self._brand(parts)
        self._caption(parts)
        parts.append(_dt(self.texts.put("嘉宾介绍", "label"), size=30,
                         color="white@0.5", x="(w-tw)/2", y="(h-th)/2-150",
                         font=self.font_body, border=1, border_color="black@0.4"))
        parts.append(_dt(self.texts.put(name, "name"), size=110, color=accent,
                         x="(w-tw)/2", y="(h-th)/2-70",
                         font=self.font_display, border=5,
                         border_color="black@0.8", shadow=(0, 4, "black@0.5"),
                         alpha="min(1,max(0,t/0.4))"))
        parts.append(_box("(iw-260)/2", "ih/2+90", 260, 4, accent))
        parts.append(_dt(self.texts.put(role, "role"), size=34,
                         color="white@0.82", x="(w-tw)/2", y="ih/2+112",
                         font=self.font_body, border=2, border_color="black@0.5"))
        parts.append("fade=t=in:st=0:d=0.3")

    # ---- 主入口 ----------------------------------------------------------
    def build(self) -> str:
        parts: list[str] = []
        handler = getattr(self, "_build_" + self.type, None)
        if handler is None or self.type == "guest_intro":
            handler = self._build_guest_intro if self.type == "guest_intro" \
                else (handler or self._build_host_to_camera)
        handler(parts)
        parts.append("format=yuv420p")
        return ",".join(parts)


def _base_source(ctx: _ShotCtx) -> str:
    w, h = ctx.w, ctx.h
    t = ctx.type
    if t == "title_card":
        return (f"gradients=s={w}x{h}:c0=0x5A4008:c1=0x0C0803:type=radial:"
                f"x0=640:y0=320:x1=640:y1=780:speed=0.006")
    if t == "end_card":
        return (f"gradients=s={w}x{h}:c0=0x4A3408:c1=0x0C0803:type=radial:"
                f"x0=640:y0=360:x1=640:y1=800:speed=0.004")
    if t == "meme_card":
        return (f"gradients=s={w}x{h}:c0=0x171006:c1=0x040404:type=radial:"
                f"x0=640:y0=360:x1=640:y1=780:speed=0.003")
    if t == "b_roll":
        return (f"gradients=s={w}x{h}:c0=0x05131F:c1=0x0D2B40:"
                f"x0=0:y0=0:x1=0:y1=720:type=linear:speed=0.006")
    if t == "reaction":
        return (f"gradients=s={w}x{h}:c0=0x4A1616:c1=0x140606:type=radial:"
                f"x0=640:y0=300:x1=980:y1=700:speed=0.02")
    if t == "shot_reverse":
        return (f"gradients=s={w}x{h}:c0=0x0A1B33:c1=0x2A0C10:"
                f"x0=0:y0=0:x1=1280:y1=0:type=linear:speed=0.012")
    if t == "guest_intro":
        return (f"gradients=s={w}x{h}:c0=0x6B4E14:c1=0x1A1206:type=radial:"
                f"x0=640:y0=300:x1=640:y1=780:speed=0.01")
    # host_to_camera / 兜底
    return (f"gradients=s={w}x{h}:c0=0x9A7220:c1=0x2B1B08:type=radial:"
            f"x0=520:y0=300:x1=520:y1=760:speed=0.008")


def generate_mock_clip(spec: dict, out_path, scratch_dir,
                       resolution: str | None = None,
                       fps: int | None = None) -> dict:
    """按 contract 的 shot_spec.json 生成一个 mock 片段。"""
    spec = spec or {}
    shot = dict(spec.get("shot") or {})
    assets = list(spec.get("assets") or [])
    episode = dict(spec.get("episode") or {})
    res = resolution or episode.get("resolution") or config.RESOLUTION
    width, height = utils.parse_resolution(res)
    fps = int(fps or episode.get("fps") or config.FPS)
    duration = float(shot.get("duration") or 5.0)
    if duration <= 0:
        duration = 5.0
    scratch = utils.ensure_dir(scratch_dir)
    font_info = fonts.ensure_fonts(scratch)
    ctx = _ShotCtx(shot, assets, scratch, font_info, width, height,
                   duration, fps)
    vf = ctx.build()
    src = _base_source(ctx)
    out = Path(out_path)
    if not out.is_absolute():
        out = scratch / out
    utils.ensure_dir(out.parent)
    args = [
        "-f", "lavfi", "-i", src,
        "-vf", vf,
        "-t", f"{duration:.3f}",
        "-r", str(fps),
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
        "-pix_fmt", "yuv420p", "-an", "-movflags", "+faststart",
        str(out),
    ]
    utils.ffmpeg(args, cwd=scratch)
    return {
        "ok": True,
        "path": str(out),
        "duration": round(utils.probe_duration(out), 3),
        "provider": "mock",
        "width": width,
        "height": height,
        "fps": fps,
        "type": ctx.type,
    }
