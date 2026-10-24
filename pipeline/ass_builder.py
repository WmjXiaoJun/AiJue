"""ASS 字幕/花字生成：把镜头时间轴转成一条全局 ASS 轨道。

样式定义在 pipeline/templates/subtitle_variety.ass（契约要求），
这里只做占位符替换与 Events 追加。
"""
from __future__ import annotations

import logging
import re
from pathlib import Path

from . import config, utils

LOG = logging.getLogger("pipeline")


SUB_MAX_CHARS = 24      # 42 号字下约 1000px（中文字宽≈字号）
SUB_WRAP_TARGET = 15    # 超过最大宽度时手动换行，每行目标字数
_PUNCT = "，。！？；：、,.!?;:）)】」』"


def plain_subtitle(text: str) -> str:
    """字幕文本清洗：去掉空格/换行（避免 ASS 按空格断行，如「AI 版本」）。"""
    return re.sub(r"\s+", "", str(text or ""))


def wrap_subtitle(text: str) -> list[str]:
    """超过最大宽度时在标点附近均衡拆成两行；两行手动换行后仍以 x=640 居中。"""
    text = plain_subtitle(text)
    if len(text) <= SUB_MAX_CHARS:
        return [text]
    ideal = len(text) / 2.0
    best = None
    for i, ch in enumerate(text[:-1]):
        if ch not in _PUNCT:
            continue
        if abs((i + 1) - ideal) <= 6:
            best = i + 1
            break
    if best is None:
        best = max(1, min(len(text) - 1, int(round(ideal))))
    return [text[:best], text[best:]]


def ass_time(seconds: float) -> str:
    """秒 -> H:MM:SS.cc（ASS 时间格式）。"""
    seconds = max(0.0, float(seconds))
    total_cs = int(round(seconds * 100))
    h, rem = divmod(total_cs, 360000)
    m, rem = divmod(rem, 6000)
    s, cs = divmod(rem, 100)
    return f"{h:d}:{m:02d}:{s:02d}.{cs:02d}"


def _esc(text: str) -> str:
    """ASS 文本转义：反斜杠、花括号、换行。"""
    t = str(text or "")
    t = t.replace("\\", "\\\\")
    t = t.replace("{", "\\{").replace("}", "\\}")
    t = t.replace("\r", " ").replace("\n", "\\N")
    return t


def ass_color(hex_color: str, default: str = config.ACCENT_DEFAULT) -> str:
    """#RRGGBB -> ASS &H00BBGGRR。"""
    c = str(hex_color or "").strip()
    if c.startswith("#") and len(c) == 7:
        r, g, b = c[1:3], c[3:5], c[5:7]
        return f"&H00{b}{g}{r}".upper()
    return "&H0018C5F5"


def _accent_map(assets: list) -> dict:
    mapping = {}
    for a in assets or []:
        if isinstance(a, dict) and a.get("accent_color"):
            mapping[a.get("id")] = a["accent_color"]
    return mapping


def build_events(episode: dict, timings: list, assets: list,
                 char_shot_ids: set[str] | None = None) -> list[tuple]:
    """返回 [(start, end, layer, style, text)]。

    char_shot_ids: 使用角色画面的镜头 id 集合；这些镜头的字幕用 SubChar 样式
    （右移避开左下名字条、最大宽度约 62%）。
    """
    char_shot_ids = char_shot_ids or set()
    events: list[tuple] = []
    accents = _accent_map(assets)
    segment_names = {}
    for seg in (episode or {}).get("segments") or []:
        segment_names[seg.get("id")] = str(seg.get("name") or "")

    # 说明：mock 片段本身已在左上角绘制台标，这里不再叠加全局台标，避免重影。
    # Brand 样式保留在模板中，供后续真实素材阶段使用。

    # 段落小标：每个 segment 第一次出现时打 4 秒
    seen_segments = set()
    for item in timings:
        shot = item["shot"]
        seg = shot.get("segment") or shot.get("segment_id")
        if not seg or seg in seen_segments:
            continue
        seen_segments.add(seg)
        if seg == "open":
            continue
        name = segment_names.get(seg) or ""
        if not name:
            continue
        start = item["start"] + 0.35
        end = min(item["end"], start + 4.0)
        if end - start < 1.2:
            continue
        events.append((start, end, 2, "Chapter",
                       r"{\fad(160,200)}◆ " + _esc(name)))

    for item in timings:
        shot = item["shot"]
        typ = str(shot.get("type") or "")
        start = float(item["start"])
        end = float(item["end"])
        line = str(shot.get("line") or "").strip()
        subtitle = str(shot.get("subtitle") or "").strip()
        lower = shot.get("lower_third")

        # ---- 花字大字（meme_card 已在片段里绘制，跳过避免重影）----
        punch = str(shot.get("punchline") or "").strip()
        if punch and typ != "meme_card":
            p_start = start + 0.12
            p_end = max(p_start + 0.8, end - 0.12)
            events.append((p_start, p_end, 3, "Punch",
                           r"{\fad(140,160)\t(0,220,\fscx106\fscy106)}" + _esc(punch)))

        # ---- 正文旁白字幕 ----
        if typ != "meme_card":
            body = line or subtitle
            if body:
                s_start = start + 0.12
                s_end = max(s_start + 0.6, end - 0.10)
                sub_style = "SubChar" if str(shot.get("id")) in char_shot_ids else "Sub"
                lines = [_esc(part) for part in wrap_subtitle(body)]
                sub_text = (r"{\an2\pos(640,655)\fad(110,90)}" + r"\N".join(lines))
                events.append((s_start, s_end, 4, sub_style, sub_text))

        # ---- 人名条（lower_third）----
        # 角色画面镜头里片段已绘制当前说话人名字条，跳过 ASS 人名条避免重复/叠字
        if isinstance(lower, dict) and lower.get("name") \
                and str(shot.get("id")) not in char_shot_ids:
            name = str(lower.get("name") or "").strip()
            title = str(lower.get("title") or "").strip()
            color = ass_color(accents.get(shot.get("speaker"), config.ACCENT_DEFAULT))
            label = name + ("  ·  " + title if title else "")
            lt_start = start + 0.35
            lt_dur = min(5.0, max(2.4, (end - start) - 0.6))
            lt_end = lt_start + lt_dur
            y = 520
            tag = (r"{\fad(120,140)\move(-420," + str(y) + ",64," + str(y)
                   + r",0,360)\3c" + color + "&}")
            events.append((lt_start, lt_end, 5, "Lower", tag + _esc(label)))

    events.sort(key=lambda e: (e[0], e[2]))
    return events


def build_ass(episode: dict, timings: list, assets: list, fonts: dict,
              out_path, width: int = 1280, height: int = 720,
              char_shot_ids: set[str] | None = None) -> dict:
    """生成全局 ASS 文件，返回统计信息。"""
    template_path = config.TEMPLATES_DIR / "subtitle_variety.ass"
    try:
        template = template_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise RuntimeError(f"读取字幕样式模板失败: {exc}") from exc
    body_family = (fonts.get("body") or {}).get("family") or "DengXian"
    display_family = (fonts.get("display") or {}).get("family") or body_family
    header = template.replace("{{FONT_BODY}}", body_family)
    header = header.replace("{{FONT_DISPLAY}}", display_family)
    if (width, height) != (1280, 720):
        header = header.replace("PlayResX: 1280", f"PlayResX: {width}")
        header = header.replace("PlayResY: 720", f"PlayResY: {height}")

    events = build_events(episode or {}, timings, assets or [],
                          char_shot_ids=char_shot_ids)
    lines = []
    for start, end, layer, style, text in events:
        lines.append(
            f"Dialogue: {layer},{ass_time(start)},{ass_time(end)},{style},,0,0,0,,{text}"
        )
    content = header + "\n".join(lines) + "\n"
    utils.write_text(out_path, content)
    LOG.info("ASS 生成完成: %d 条事件 -> %s", len(events), out_path)
    return {"path": str(out_path), "events": len(events),
            "body_font": body_family, "display_font": display_family}
