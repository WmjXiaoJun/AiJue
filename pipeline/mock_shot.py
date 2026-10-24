"""mock 镜头生成：ffmpeg lavfi 背景 + 角色立绘 + drawtext 差异化视觉。

镜头类型画风（角色素材可用时以「角色画面」为主，缺失自动回退文字卡视觉）：
- title_card     角色蒙太奇背景（模糊+压暗）+ 大字标题 + 品牌角标
- host_to_camera 节目暖黄背景 + 主持人立绘/转面面板 + 主持人名牌
- shot_reverse   蓝/红节目背景 + 当前说话人立绘面板 + 发言状态
- reaction       暗红聚光 + 表情特写面板（按 mood/台词选 neutral/happy/surprised/thinking）
- b_roll         冷蓝科技纹理 + 主持人小窗
- meme_card      黑底 + 花字大字（punchline）+ 主持人小窗
- end_card       角色蒙太奇背景 + 品牌收尾
- guest_intro    单角色立绘 + 名字介绍（兜底）

约定：以 scratch_dir 为 ffmpeg cwd，drawtext 只引用相对路径（fonts/、text/），
避免 Windows 盘符冒号在 filter 参数里被误解析；角色图片/视频走 ffmpeg 输入参数。
"""
from __future__ import annotations

import logging
from pathlib import Path

from . import characters, config, fonts, utils

LOG = logging.getLogger("pipeline")

CHARACTER_SHOT_TYPES = ("host_to_camera", "shot_reverse", "reaction", "guest_intro")

# 台本→分镜契约（contract/SCRIPT_FORMAT.md）的 Shot.type 与现有类型的兼容映射：
# - talking_head（对白）/ narration（旁白/独白）按主持人说话镜头处理
# - 其余 unknown 保持原样（走文字卡兜底，向后兼容）
SHOT_TYPE_ALIASES = {
    "talking_head": "host_to_camera",
    "dialogue": "host_to_camera",
    "interview": "host_to_camera",
    "narration": "host_to_camera",
    "voiceover": "host_to_camera",
    "monologue": "host_to_camera",
    "reverse": "shot_reverse",
}


def normalize_shot_type(value) -> str:
    """把台本生成的新镜头类型归一化到现有视觉类型（空类型默认 host_to_camera）。"""
    key = str(value or "").strip().lower()
    if not key:
        return "host_to_camera"
    return SHOT_TYPE_ALIASES.get(key, key)
CARD_MONTAGE_TYPES = ("title_card", "end_card")
PIP_TYPES = ("meme_card", "b_roll")


# 镜头内烘焙文案的多语种覆盖（默认中文，向后兼容）；本地化重生成 clips 时传入译文
DEFAULT_LOCALE = {
    "brand_line": "造片局 · AI 微综艺",
    "brand_short": "造片局",
    "title_top": "造片局 出品  ·  EP.{no:03d}",
    "episode_label": "第 {no:03d} 期",
    "live": "● LIVE  虚拟演播厅 A",
    "speaking": "发言中",
    "status_line": "● {name}  ·  {role}  ·  {speaking}",
    "status_line2": "● {speaking}  ·  {role}",
    "reaction_label": "REACTION · 表情特写",
    "reaction_label2": "REACTION · 特写",
    "reaction_role": "反应镜头",
    "guest_intro_label": "嘉宾介绍",
    "host_role": "主持人",
    "guest_role": "嘉宾",
    "end_brand": "造片局",
    "end_tagline": "AI 微综艺",
    "meme_tag": "花字  MEME",
    "meme_punch": "花字",
    "broll_scene": "剪辑机房 · 服务器指示灯",
}


def _loc_value(locale: dict | None, key: str, **kwargs) -> str:
    text = str((locale or {}).get(key) or DEFAULT_LOCALE.get(key) or "")
    if not kwargs:
        return text
    try:
        return text.format(**kwargs)
    except (KeyError, IndexError, ValueError):
        return text


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
                 width: int, height: int, duration: float, fps: int,
                 char_map: dict | None = None, locale: dict | None = None) -> None:
        self.shot = shot or {}
        self.locale = dict(DEFAULT_LOCALE)
        self.locale.update(locale or {})
        self._names = dict((locale or {}).get("names") or {})
        self.assets = [a for a in (assets or []) if isinstance(a, dict)]
        self.scratch = scratch
        self.font_body = font_info["body"]["file"]
        self.font_display = font_info["display"]["file"]
        self.w = width
        self.h = height
        self.dur = duration
        self.fps = fps
        self.texts = _TextFiles(scratch, utils.safe_slug(self.shot.get("id") or "shot", 12))
        self._t = lambda key, **kw: _loc_value(self.locale, key, **kw)
        self._name = lambda value: self._names.get(str(value or ""), str(value or ""))
        self.type = normalize_shot_type(self.shot.get("type"))
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

        # ---- 角色素材映射 ----
        self.char_map = char_map or {}
        self.character = self.char_map.get(str(self.speaker_id)) or {}
        self.cast = [c for c in characters.order_cast(self.char_map)
                     if c.get("portrait") or c.get("h3_portrait")
                     or c.get("concept_portrait")]
        # 无 speaker 的角色镜头（如 reaction 冷笑）：按画面/台词里出现的角色名推断，兜底用主持人
        if not self.character and self.cast:
            hint = " ".join(str(self.shot.get(k) or "") for k in (
                "speaker_name", "visual", "line", "subtitle"))
            for char in self.cast:
                cname = str(char.get("name") or "")
                if cname and cname in hint:
                    self.character = char
                    break
        if (not self.character) and self.cast \
                and self.type in ("host_to_camera", "reaction", "guest_intro"):
            self.character = self.cast[0]
        if self.character and not self.speaker_name:
            self.speaker_name = str(self.character.get("name") or "")
        # 素材优先级（contract/CONSISTENCY.md）：
        # h3 视频 > h3 中段帧 > SDXL 概念立绘 > Blender cutout > 2D > 文字卡
        self.media = None
        if self.character:
            if self.type == "host_to_camera" and self.character.get("h3_video"):
                self.media = {"kind": "h3_video",
                              "path": self.character["h3_video"]}
            elif self.type in CHARACTER_SHOT_TYPES \
                    and self.character.get("h3_portrait"):
                self.media = {"kind": "h3_still",
                              "path": self.character["h3_portrait"]}
            elif self.type in CHARACTER_SHOT_TYPES \
                    and self.character.get("concept_portrait"):
                self.media = {"kind": "concept_still",
                              "path": self.character["concept_portrait"]}
        self.char_mode = (bool(self.character.get("portrait"))
                          or self.media is not None) \
            and self.type in CHARACTER_SHOT_TYPES
        self.cast_mode = bool(self.cast) and self.type in CARD_MONTAGE_TYPES
        self.cutout_active = False
        self.pip_char = None
        if self.type in PIP_TYPES and self.cast:
            host = next((c for c in self.cast if c.get("role") == "host"), self.cast[0])
            self.pip_char = host

    # ---- 小工具 ----------------------------------------------------------
    def _asset(self, aid: str) -> dict:
        for a in self.assets:
            if a.get("id") == aid:
                return a
        return {}

    def _accent_of(self, asset: dict) -> str:
        return _asset_color(asset)

    def _char_accent(self, char: dict) -> str:
        return _hex_to_ff(char.get("accent_color") or config.ACCENT_DEFAULT)

    def _brand(self, parts: list, opacity: str = "white@0.72") -> None:
        accent = config.ACCENT_HOST
        parts.append(_box(46, 32, 6, 30, accent))
        parts.append(_dt(self.texts.put(self._t("brand_line"), "brand"),
                         size=26, color=opacity, x=64, y=35,
                         font=self.font_body, border=1, border_color="black@0.35"))

    def _caption(self, parts: list, left: bool = False,
                 y: int | None = None) -> None:
        if not self.visual:
            return
        parts.append(_dt(self.texts.put(self.visual, "cap"),
                         size=23, color="white@0.34",
                         x=64 if left else "w-tw-46",
                         y=(78 if left else 38) if y is None else int(y),
                         font=self.font_body, border=1, border_color="black@0.25"))

    # ---- 角色合成 --------------------------------------------------------
    def _add_image(self, inputs: list, path) -> int:
        inputs.append(["-loop", "1", "-i", str(path)])
        return len(inputs)

    def _add_video(self, inputs: list, path) -> int:
        inputs.append(["-stream_loop", "-1", "-i", str(path)])
        return len(inputs)

    def _panel(self, inputs: list, filters: list, source, pw: int, ph: int,
               accent: str) -> str:
        """生成带黑边+品牌色描边的角色面板，返回 overlay 用标签。"""
        is_video = str(source).lower().endswith((".mp4", ".mov", ".webm", ".mkv"))
        idx = self._add_video(inputs, source) if is_video \
            else self._add_image(inputs, source)
        filters.append(
            f"[{idx}:v]scale={pw}:{ph}:force_original_aspect_ratio=increase,"
            f"crop={pw}:{ph},setsar=1,fps={self.fps}[p{idx}]"
        )
        filters.append(
            f"[p{idx}]pad=iw+20:ih+20:10:10:color=0x080808,"
            f"drawbox=x=0:y=0:w=iw:h=ih:color={accent}@0.92:t=4[panel{idx}]"
        )
        return f"panel{idx}"

    def _overlay(self, filters: list, current: str, panel: str, x, y,
                 out: str) -> str:
        filters.append(
            f"[{current}][{panel}]overlay=x={x}:y={y}:shortest=0[{out}]"
        )
        return out

    def _overlay_character(self, inputs: list, filters: list, source,
                           accent: str, *, drift: int = 0,
                           panel_size: int = 600, panel_x: int | None = None,
                           panel_y: int = 60,
                           cutout_height: int = 700,
                           cutout_x: int | None = None,
                           cutout_y: int | None = None) -> str:
        """透明 PNG 立绘 -> 抠像直接叠到节目背景；不透明/视频 -> 描边面板。"""
        is_video = str(source).lower().endswith((".mp4", ".mov", ".webm", ".mkv"))
        if (not is_video) and characters.has_alpha(source):
            self.cutout_active = True
            idx = self._add_image(inputs, source)
            filters.append(
                f"[{idx}:v]scale=-1:{cutout_height}:"
                f"force_original_aspect_ratio=decrease,setsar=1,"
                f"fps={self.fps},format=rgba[c{idx}]"
            )
            x = cutout_x if cutout_x is not None else self.w - cutout_height - 60
            y = cutout_y if cutout_y is not None else self.h - cutout_height
            filters.append(f"[0:v][c{idx}]overlay=x={x}:y={y}:shortest=0[base]")
            return "base"
        panel = self._panel(inputs, filters, source, panel_size, panel_size, accent)
        x = panel_x if panel_x is not None else self.w - panel_size - 80
        filters.append(
            f"[0:v][{panel}]overlay=x={x - 10}+{drift}*sin(t*0.6):y={panel_y - 10}"
            f":shortest=0[base]"
        )
        return "base"

    def _bust_geometry(self) -> tuple[int, int, int]:
        """统一胸像构图：高度≈画面 91%，底部对齐，人物放在右侧 1/3。"""
        height = int(round(self.h * 0.91))
        x = self.w - height - 50
        y = self.h - height
        return height, x, y

    def _h3_layout(self, inputs: list, filters: list, idx: int,
                   accent: str, ratio: float, ken_burns: bool,
                   top_crop: bool = False) -> str:
        """素材构图：节目背景 + 右侧 1/3 的胸像画窗（细描边 + Ken Burns）。

        768x768 的 H3 素材直接 cover 裁切会把脸放大到失真，这里保留完整构图，
        以「画窗」形式叠在节目背景右侧，人物按统一胸像规格（高≈91%、底部对齐）。
        top_crop=True 用于 832x1216 竖构图概念图：优先保留画面顶部（头部），
        只裁掉下半身，顶部安全边约 2%。
        """
        fg_h, fg_x, fg_y = self._bust_geometry()
        stretch = f",setpts=PTS*{ratio:.4f}" if ratio > 1.02 else ""
        if top_crop:
            crop_expr = (f"{fg_h}:{fg_h}:(iw-{fg_h})/2:"
                         f"'min(ih-{fg_h},ih*0.02)'")
        else:
            crop_expr = f"{fg_h}:{fg_h}"
        fg = (
            f"[{idx}:v]scale={fg_h}:{fg_h}:force_original_aspect_ratio=increase,"
            f"crop={crop_expr},setsar=1{stretch},fps={self.fps}"
        )
        if ken_burns:
            frames = max(2, int(round(self.dur * self.fps)))
            step = 0.12 / frames
            fg += (f",zoompan=z='min(zoom+{step:.6f},1.12)':"
                   "x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':"
                   f"d={frames}:s={fg_h}x{fg_h}:fps={self.fps}")
        fg += f",pad=iw+20:ih+20:10:10:color=0x080808,drawbox=x=0:y=0:w=iw:h=ih:color={accent}@0.85:t=4[fg]"
        filters.append(fg)
        filters.append(
            f"[0:v][fg]overlay=x={fg_x - 10}:y={fg_y - 10}:shortest=0,"
            "vignette=PI/5[base]"
        )
        return "base"

    def _compose_h3_video(self) -> dict | None:
        """H3 真实视频：广播式构图铺满画面，必要时慢放对齐镜头时长。"""
        src = self.media["path"]
        accent = self._char_accent(self.character)
        inputs: list = []
        filters: list = []
        idx = self._add_video(inputs, src)
        try:
            vdur = float(utils.probe_duration(src) or 0.0)
        except Exception:  # noqa: BLE001
            vdur = 0.0
        ratio = min(self.dur / vdur, 1.8) if (vdur > 0.1 and self.dur > vdur) else 1.0
        self._h3_layout(inputs, filters, idx, accent, ratio, ken_burns=False)
        LOG.debug("H3 视频镜头 %s: 源 %.2fs / 拉伸 %.2fx", src, vdur, ratio)
        return {"inputs": inputs, "filters": filters, "label": "base"}

    def _compose_h3_still(self) -> dict | None:
        """H3 中段帧：广播式构图 + Ken Burns 缓推（静帧有运动感）。"""
        src = self.media["path"]
        accent = self._char_accent(self.character)
        inputs: list = []
        filters: list = []
        idx = self._add_image(inputs, src)
        self._h3_layout(inputs, filters, idx, accent, 1.0, ken_burns=True)
        return {"inputs": inputs, "filters": filters, "label": "base"}

    def _compose_concept_still(self) -> dict | None:
        """SDXL 概念立绘（832x1216 竖构图）：右侧胸像画窗 + Ken Burns。

        竖构图顶部保留头部（安全边约 2%），只裁下半身，避免头部被裁切；
        reaction 镜头优先用概念表情图。
        """
        char = self.character
        src = self.media["path"]
        if self.type == "reaction":
            exprs = char.get("concept_expressions") or {}
            if exprs:
                picked = characters.choose_expression(
                    {"portrait": char.get("concept_portrait"),
                     "expressions": exprs},
                    text=(self.shot.get("line") or self.shot.get("subtitle") or ""),
                    mood=str(self.shot.get("mood") or ""),
                )
                if picked:
                    src = picked
        accent = self._char_accent(char)
        inputs: list = []
        filters: list = []
        idx = self._add_image(inputs, src)
        self._h3_layout(inputs, filters, idx, accent, 1.0, ken_burns=True,
                        top_crop=True)
        return {"inputs": inputs, "filters": filters, "label": "base"}

    def _compose_host_to_camera(self) -> dict | None:
        char = self.character
        source = char.get("turntable") or char.get("portrait")
        if source is None:
            return None
        accent = self._char_accent(char)
        inputs: list = []
        filters: list = []
        # 转面视频走面板（带运动感）；透明立绘优先抠像直叠，否则描边面板
        bust_h, bust_x, bust_y = self._bust_geometry()
        self._overlay_character(
            inputs, filters, source, accent,
            drift=4, panel_size=600, panel_y=60,
            cutout_height=bust_h, cutout_x=bust_x, cutout_y=bust_y,
        )
        return {"inputs": inputs, "filters": filters, "label": "base"}

    def _compose_reaction(self) -> dict | None:
        char = self.character
        source = characters.choose_expression(
            char,
            text=(self.shot.get("line") or self.shot.get("subtitle") or ""),
            mood=str(self.shot.get("mood") or ""),
        )
        if source is None:
            return None
        accent = self._char_accent(char)
        inputs: list = []
        filters: list = []
        bust_h, bust_x, bust_y = self._bust_geometry()
        self._overlay_character(
            inputs, filters, source, accent,
            panel_size=620, panel_y=40,
            cutout_height=bust_h, cutout_x=bust_x, cutout_y=bust_y,
        )
        return {"inputs": inputs, "filters": filters, "label": "base"}

    def _compose_shot_reverse(self) -> dict | None:
        char = self.character
        source = char.get("portrait") or char.get("turntable")
        if source is None:
            return None
        accent = self._char_accent(char)
        inputs: list = []
        filters: list = []
        bust_h, bust_x, bust_y = self._bust_geometry()
        self._overlay_character(
            inputs, filters, source, accent,
            drift=3, panel_size=540, panel_y=110,
            cutout_height=bust_h, cutout_x=bust_x, cutout_y=bust_y,
        )
        return {"inputs": inputs, "filters": filters, "label": "base"}

    def _compose_guest_intro(self) -> dict | None:
        char = self.character
        source = char.get("portrait")
        if source is None:
            return None
        accent = self._char_accent(char)
        inputs: list = []
        filters: list = []
        bust_h, bust_x, bust_y = self._bust_geometry()
        self._overlay_character(
            inputs, filters, source, accent,
            panel_size=560, panel_y=80,
            cutout_height=bust_h, cutout_x=bust_x, cutout_y=bust_y,
        )
        return {"inputs": inputs, "filters": filters, "label": "base"}

    def _preferred_still(self, char: dict):
        """角色静帧：H3 中段帧 > SDXL 概念立绘 > Blender 立绘。"""
        return (char.get("h3_portrait") or char.get("concept_portrait")
                or char.get("portrait"))

    def _compose_cast_montage(self) -> dict | None:
        cast = [c for c in self.cast if self._preferred_still(c)][:3]
        if not cast:
            return None
        n = len(cast)
        total = self.w
        widths = [total // n + (1 if i < total % n else 0) for i in range(n)]
        inputs: list = []
        filters: list = []
        labels: list[str] = []
        for i, char in enumerate(cast):
            idx = self._add_image(inputs, self._preferred_still(char))
            filters.append(
                f"[{idx}:v]scale={widths[i]}:{self.h}:"
                f"force_original_aspect_ratio=increase,"
                f"crop={widths[i]}:{self.h},setsar=1,fps={self.fps}[m{i}]"
            )
            labels.append(f"[m{i}]")
        blur = "boxblur=20:3,eq=saturation=0.85"
        if n == 1:
            filters.append(
                f"[m0]{blur},drawbox=x=0:y=0:w=iw:h=ih:color=black@0.62:t=fill[cast]"
            )
        else:
            filters.append(
                "".join(labels) + f"hstack=inputs={n},{blur},"
                "drawbox=x=0:y=0:w=iw:h=ih:color=black@0.62:t=fill[cast]"
            )
        filters.append("[0:v][cast]overlay=0:0:shortest=0[base]")
        return {"inputs": inputs, "filters": filters, "label": "base"}

    def _compose_pip(self) -> dict | None:
        char = self.pip_char
        still = self._preferred_still(char) if char else None
        if not char or not still:
            return None
        accent = self._char_accent(char)
        inputs: list = []
        filters: list = []
        if self.type == "meme_card":
            panel_x, panel_y = 52, 430
            cutout_x, cutout_y = 60, self.h - 250
        else:
            panel_x, panel_y = self.w - 220 - 72, 360
            cutout_x, cutout_y = self.w - 250 - 56, self.h - 250
        self._overlay_character(
            inputs, filters, still, accent,
            drift=3, panel_size=220, panel_x=panel_x, panel_y=panel_y,
            cutout_height=250, cutout_x=cutout_x, cutout_y=cutout_y,
        )
        return {"inputs": inputs, "filters": filters, "label": "base"}

    def compose(self) -> dict | None:
        """返回角色合成方案；无素材/该镜头不用角色画面时返回 None。"""
        if self.media is not None:
            kind = self.media["kind"]
            if kind == "h3_video":
                return self._compose_h3_video()
            if kind == "concept_still":
                return self._compose_concept_still()
            return self._compose_h3_still()
        if self.char_mode:
            handler = getattr(self, "_compose_" + self.type, None)
            if handler is not None:
                result = handler()
                if result:
                    return result
        if self.cast_mode:
            return self._compose_cast_montage()
        if self.type in PIP_TYPES:
            return self._compose_pip()
        return None

    def used_character_id(self) -> str:
        if self.char_mode and self.character:
            return str(self.character.get("id") or "")
        if self.cast_mode:
            return "cast"
        if self.pip_char:
            return str(self.pip_char.get("id") or "")
        return ""

    # ---- 各类型视觉（文字层） -------------------------------------------
    def _build_title_card(self, parts: list) -> None:
        accent = config.ACCENT_HOST
        title = str(self.shot.get("title") or self.shot.get("subtitle")
                    or config.BRAND_NAME)
        ep_no = int(self.shot.get("episode_no") or 1)
        parts.append(_dt(self.texts.put(self._t("title_top", no=ep_no), "top"),
                         size=27, color="white@0.72", x=46, y=32,
                         font=self.font_body, border=1, border_color="black@0.5"))
        parts.append(_box(46, 68, 300, 3, "0xFFFFFF@0.3"))
        # 宽感知字号：拉丁/译文标题可能远长于中文，按字数估算保证不出画
        size = max(22, min(88, int(1120 / (0.62 * max(1, len(title))))))
        parts.append(_dt(self.texts.put(title, "title"), size=size,
                         color="white", x="(w-tw)/2", y="(h-th)/2-64",
                         font=self.font_display, border=5, border_color="black@0.85",
                         shadow=(0, 5, "black@0.55"),
                         alpha="min(1,max(0,t/0.45))"))
        parts.append(_box("(iw-420)/2", 470, 420, 5, accent))
        parts.append(_box("(iw-70)/2", 483, 70, 3, "0xFFFFFF@0.5"))

    def _build_end_card(self, parts: list) -> None:
        accent = config.ACCENT_HOST
        parts.append(_dt(self.texts.put(self._t("end_brand"), "brandname"),
                         size=132, color="white", x="(w-tw)/2", y="(h-th)/2-96",
                         font=self.font_display, border=4, border_color="black@0.6",
                         shadow=(0, 6, "black@0.5"),
                         alpha="min(1,max(0,t/0.55))"))
        parts.append(_box("(iw-460)/2", 470, 460, 5, accent))
        parts.append(_dt(self.texts.put(self._t("end_tagline"), "tagline"),
                         size=48, color=accent, x="(w-tw)/2", y=496,
                         font=self.font_body, border=2, border_color="black@0.5"))
        parts.append(_dt(self.texts.put(self._t("episode_label", no=int(self.shot.get("episode_no") or 1)), "next"),
                         size=28, color="white@0.6", x="(w-tw)/2", y=572,
                         font=self.font_body, alpha="min(1,max(0,(t-0.5)/0.6))"))

    def _build_host_to_camera(self, parts: list) -> None:
        accent = self._char_accent(self.character) if self.char_mode else self.accent
        name = self._name(self.speaker_name) or self._t("host_role")
        role = _role_text(self.speaker) if self.speaker else self._t("host_role")
        if not self.char_mode:
            if name:
                parts.append(_dt(self.texts.put(name, "wm"), size=300,
                                 color="white@0.055", x="(w-tw)/2", y="(h-th)/2-40",
                                 font=self.font_display))
        self._brand(parts)
        self._caption(parts, y=22 if self.char_mode else None)
        # 名字条上移，给底部字幕（\pos(640,655)）让位
        parts.append(_box(62, 496, 8, 96, accent))
        parts.append(_dt(self.texts.put(name, "name"), size=48, color=accent,
                         x=86, y=502, font=self.font_display, border=3,
                         border_color="black@0.7", shadow=(0, 3, "black@0.5"),
                         alpha="min(1,max(0,(t-0.15)/0.4))"))
        parts.append(_dt(self.texts.put(role, "role"), size=27,
                         color="white@0.82", x=88, y=562, font=self.font_body,
                         border=1, border_color="black@0.5",
                         alpha="min(1,max(0,(t-0.3)/0.4))"))
        if self.char_mode:
            # 右侧角色面板的对侧小引语，强化综艺包装
            parts.append(_dt(self.texts.put(self._t("live"), "live"),
                             size=22, color="white@0.42", x=62, y=448,
                             font=self.font_body, border=1,
                             border_color="black@0.35"))

    def _build_shot_reverse(self, parts: list) -> None:
        accent = self._char_accent(self.character) if self.char_mode else self.accent
        name = self._name(self.speaker_name) or self._t("guest_role")
        role = _role_text(self.speaker) if self.speaker else self._t("guest_role")
        self._brand(parts)
        self._caption(parts)
        if self.char_mode:
            # 顶部发言状态 + 左下角当前说话人名字条（逐镜头按 shot.speaker_name 绑定）
            parts.append(_dt(self.texts.put(self._t("status_line", name=name, role=role, speaking=self._t("speaking")), "status"),
                             size=30, color=accent, x="(w-tw)/2", y=92,
                             font=self.font_display, border=3,
                             border_color="black@0.75",
                             alpha="min(1,max(0,t/0.35))"))
            parts.append(_box(62, 496, 8, 96, accent))
            parts.append(_dt(self.texts.put(name, "name"), size=48, color=accent,
                             x=86, y=502, font=self.font_display, border=3,
                             border_color="black@0.7", shadow=(0, 3, "black@0.5"),
                             alpha="min(1,max(0,(t-0.15)/0.4))"))
            parts.append(_dt(self.texts.put(role, "role"), size=27,
                             color="white@0.82", x=88, y=562, font=self.font_body,
                             border=1, border_color="black@0.5",
                             alpha="min(1,max(0,(t-0.3)/0.4))"))
            return
        parts.append(_box(638, 0, 4, 720, "0xFFFFFF@0.12"))
        parts.append(_dt(self.texts.put(name, "name"), size=86, color=accent,
                         x="(w-tw)/2", y="(h-th)/2-90",
                         font=self.font_display, border=5,
                         border_color="black@0.8", shadow=(0, 4, "black@0.55"),
                         alpha="min(1,max(0,t/0.35))"))
        parts.append(_dt(self.texts.put(self._t("status_line2", role=role, speaking=self._t("speaking")), "status"),
                         size=29, color="white@0.85", x="(w-tw)/2", y="(h/2)+18",
                         font=self.font_body, border=2, border_color="black@0.6",
                         alpha="min(1,max(0,(t-0.25)/0.4))"))
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

    def _build_reaction(self, parts: list) -> None:
        accent = self._char_accent(self.character) if self.char_mode else self.accent
        name = self._name(self.speaker_name) or self._t("guest_role")
        self._brand(parts)
        self._caption(parts, left=True, y=238) if self.char_mode else self._caption(parts)
        if self.char_mode:
            parts.append(_dt(self.texts.put(self._t("reaction_label"), "label"), size=38,
                             color="white@0.55", x=64, y=148,
                             font=self.font_display, border=2,
                             border_color="black@0.5",
                             alpha="min(1,max(0,t/0.3))"))
            parts.append(_box(66, 200, 96, 4, accent))
            parts.append(_dt(self.texts.put(name, "name"), size=52, color=accent,
                             x=66, y="h-th-160",
                             font=self.font_display, border=3,
                             border_color="black@0.75", shadow=(0, 3, "black@0.5")))
            parts.append(_dt(self.texts.put(self._t("reaction_role"), "role"), size=24,
                             color="white@0.5", x=68, y="h-th-92",
                             font=self.font_body))
            return
        parts.append(_dt(self.texts.put(self._t("reaction_label2"), "label"), size=40,
                         color="white@0.5", x="(w-tw)/2", y="(h-th)/2-10",
                         font=self.font_body, border=2,
                         border_color="black@0.5",
                         alpha="min(1,max(0,t/0.3))"))
        parts.append(_dt(self.texts.put(name, "name"), size=48, color=accent,
                         x="w-tw-70", y="h-th-150",
                         font=self.font_display, border=3,
                         border_color="black@0.75", shadow=(0, 3, "black@0.5")))
        parts.append(_dt(self.texts.put(self._t("reaction_role"), "role"), size=24,
                         color="white@0.5", x="w-tw-68", y="h-th-84",
                         font=self.font_body))

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
        scene = _shorten(self.shot.get("visual") or self._t("broll_scene"), 30)
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

    def _build_meme_card(self, parts: list) -> None:
        accent = config.ACCENT_HOST
        punch = str(self.shot.get("punchline") or self.shot.get("subtitle") or self._t("meme_punch"))
        parts.append(_box(46, 32, 6, 30, accent))
        parts.append(_dt(self.texts.put(self._t("meme_tag"), "tag"), size=26,
                         color="white@0.42", x=64, y=35,
                         font=self.font_body, border=1, border_color="black@0.3"))
        parts.append(_dt(self.texts.put(self._t("brand_short"), "wm"), size=26,
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

    def _build_guest_intro(self, parts: list) -> None:
        accent = self._char_accent(self.character) if self.char_mode else self.accent
        name = self._name(self.speaker_name) or self._name(str(self.speaker.get("name") or "")) or self._t("guest_role")
        role = _role_text(self.speaker) if self.speaker else "嘉宾"
        self._brand(parts)
        self._caption(parts, left=self.char_mode)
        if self.char_mode:
            parts.append(_dt(self.texts.put(self._t("guest_intro_label"), "label"), size=32,
                             color="white@0.55", x=70, y=170,
                             font=self.font_body, border=1,
                             border_color="black@0.4"))
            parts.append(_dt(self.texts.put(name, "name"), size=120, color=accent,
                             x=70, y=220, font=self.font_display, border=5,
                             border_color="black@0.8", shadow=(0, 4, "black@0.5"),
                             alpha="min(1,max(0,t/0.4))"))
            parts.append(_box(74, 400, 260, 4, accent))
            parts.append(_dt(self.texts.put(role, "role"), size=36,
                             color="white@0.85", x=74, y=422,
                             font=self.font_body, border=2,
                             border_color="black@0.5"))
            return
        parts.append(_dt(self.texts.put(self._t("guest_intro_label"), "label"), size=30,
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
                       fps: int | None = None,
                       char_map: dict | None = None,
                       locale: dict | None = None) -> dict:
    """按 contract 的 shot_spec.json 生成一个 mock 片段。

    char_map: {asset_id: char_assets}，由 characters.resolve_for_assets 构建；
    为 None 时自动扫描 outputs/characters/（供 gen-shot CLI 独立调用）。
    """
    spec = spec or {}
    shot = dict(spec.get("shot") or {})
    assets = list(spec.get("assets") or [])
    episode = dict(spec.get("episode") or {})
    # 集号传播（片头/片尾的 EP.xxx 文案；localize 已按集号写入 shot）
    if not shot.get("episode_no") and episode.get("no"):
        shot["episode_no"] = int(episode.get("no"))
    res = resolution or episode.get("resolution") or config.RESOLUTION
    width, height = utils.parse_resolution(res)
    fps = int(fps or episode.get("fps") or config.FPS)
    duration = float(shot.get("duration") or 5.0)
    if duration <= 0:
        duration = 5.0
    scratch = utils.ensure_dir(scratch_dir)
    font_info = fonts.ensure_fonts(scratch)
    if char_map is None:
        char_map = characters.resolve_for_assets(assets)
    ctx = _ShotCtx(shot, assets, scratch, font_info, width, height,
                   duration, fps, char_map=char_map,
                   locale=locale if locale is not None else spec.get("locale"))
    vf = ctx.build()
    src = _base_source(ctx)
    compose = ctx.compose()
    out = Path(out_path)
    if not out.is_absolute():
        out = scratch / out
    utils.ensure_dir(out.parent)
    args = ["-f", "lavfi", "-i", src]
    if compose:
        for extra in compose["inputs"]:
            args += extra
        filter_complex = (
            ";".join(compose["filters"])
            + f";[{compose['label']}]{vf}[vout]"
        )
        args += ["-filter_complex", filter_complex, "-map", "[vout]"]
    else:
        args += ["-vf", vf]
    args += [
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
        "character": ctx.used_character_id(),
        "visual": "character" if compose else "card",
    }
