# -*- coding: utf-8 -*-
"""V56：综艺风字幕/花字（特效字幕）统一生成。

产出 ASS：
- Main：对白主字幕（白字 + 粗描边 + 轻投影，底部居中）
- Punch：花字（大号、强调色、弹入缩放 + 旋转，位置按句变化）
- Name：人名条（换人时左下角）
- Tag：场景/空镜标签（左下角小条，无台词段也有信息）

两条栈共用：render_v5 写 final.ass；短片 compose 拼接后用 libass 烧进去。
"""
from __future__ import annotations

from pathlib import Path

from . import config

FONT_MAIN = "Microsoft YaHei"
FONT_DISPLAY = "Microsoft YaHei"


def _t(seconds: float) -> str:
    seconds = max(0.0, float(seconds))
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = seconds % 60
    return f"{h:d}:{m:02d}:{s:05.2f}"


def _esc(text: str) -> str:
    return (str(text or "").replace("\\", "\\\\").replace("{", "\\{")
            .replace("}", "\\}").replace("\n", "\\N"))


def _wrap_pieces(text: str, per_line: int = 20, max_lines: int = 3) -> list[str]:
    """把中文长句按 `per_line` 折成若干行（返回纯文本行，不含转义）。"""
    s = str(text or "").strip()
    if len(s) <= per_line:
        return [s] if s else []
    no_lead = set("，。、！？；：）】》”’,.!?;:)]}")
    lines: list[str] = []
    buf = ""
    for ch in s:
        if len(buf) >= per_line and ch not in no_lead:
            lines.append(buf)
            buf = ch
            if len(lines) == max_lines - 1:
                break
        else:
            buf += ch
    consumed = sum(len(x) for x in lines)
    tail = s[consumed:]
    if tail:
        lines.append(tail)
    return [x for x in lines if x]


def _wrap_cjk(text: str, per_line: int = 20, max_lines: int = 3) -> str:
    """折行后直接用 ``\\N`` 连接（调用方**不要**再整体 _esc，否则会变成字面量 \\N）。"""
    return "\\N".join(_wrap_pieces(text, per_line, max_lines))


def _color(hex_rgb: str, alpha: int = 0) -> str:
    raw = str(hex_rgb or "#FFFFFF").lstrip("#")
    if len(raw) != 6:
        raw = "FFFFFF"
    rr, gg, bb = raw[0:2], raw[2:4], raw[4:6]
    return f"&H{alpha:02X}{bb}{gg}{rr}"


def _accent(style: dict | None) -> tuple[str, str]:
    style = style or {}
    palette = style.get("palette") if isinstance(style.get("palette"), dict) else {}
    accent = str(palette.get("accent") or style.get("accent") or config.ACCENT_DEFAULT)
    fg = str(palette.get("fg") or "#FFFFFF")
    return accent, fg


def _style_line(name: str, font: str, size: int, primary: str, outline_c: str,
                back_c: str, bold: int, outline: int, shadow: int, align: int,
                margin_l: int, margin_r: int, margin_v: int, border: int = 1) -> str:
    return (f"Style: {name},{font},{size},{primary},&H00FFFFFF,{outline_c},{back_c},"
            f"{bold},0,0,0,100,100,1,0,{border},{outline},{shadow},{align},"
            f"{margin_l},{margin_r},{margin_v},1")


def _header(width: int, height: int, accent: str, fg: str) -> str:
    main_size = max(22, int(height * 0.042))
    punch_size = max(34, int(height * 0.085))
    name_size = max(18, int(height * 0.030))
    tag_size = max(15, int(height * 0.024))
    styles = [
        _style_line("Main", FONT_MAIN, main_size, _color(fg), "&H00101010", "&H64000000",
                    -1, max(2, int(height * 0.004)), max(1, int(height * 0.002)), 2, 80, 80,
                    int(height * 0.065)),
        _style_line("Punch", FONT_DISPLAY, punch_size, _color(accent), "&H00101010",
                    "&H78000000", -1, max(4, int(height * 0.008)),
                    max(2, int(height * 0.004)), 8, 40, 40, int(height * 0.10)),
        _style_line("Name", FONT_MAIN, name_size, _color(accent), "&H00101010",
                    "&H78000000", -1, max(3, int(height * 0.006)), 0, 1,
                    int(width * 0.045), int(width * 0.045), int(height * 0.135), border=3),
        _style_line("Tag", FONT_MAIN, tag_size, "&H00E8E8E8", "&H00101010", "&H8C000000",
                    0, max(2, int(height * 0.005)), 0, 1,
                    int(width * 0.045), int(width * 0.045), int(height * 0.045), border=3),
        _style_line("Title3D", FONT_DISPLAY, max(48, int(height * 0.125)), _color(accent),
                    "&H00202020", "&H90000000", -1, max(6, int(height * 0.012)),
                    max(3, int(height * 0.006)), 5, 40, 40, 0),
    ]
    return "\n".join([
        "[Script Info]", "ScriptType: v4.00+", f"PlayResX: {width}", f"PlayResY: {height}",
        "WrapStyle: 2", "ScaledBorderAndShadow: yes", "YCbCr Matrix: TV.709", "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, "
        "BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, "
        "BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
        *styles, "", "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]) + "\n"


def _punch_text(line: str) -> str:
    cleaned = str(line or "").strip("。！？!?…，,、；; ")
    if not cleaned:
        return ""
    core = cleaned[-10:] if len(cleaned) > 10 else cleaned
    return f"「{core}」"


def _layered_3d(style_name: str, start: float, end: float, text: str,
                width: int, height: int, rot: int = -6, base_size: int = 0) -> list[str]:
    """3D 立体字：两层偏移暗影 + 主体（\frx/\fry 透视 + 弹入）。"""
    body = _esc(text)
    y = int(height * 0.42)
    x = int(width * 0.5)
    layers = []
    for dx, dy, color in ((5, 6, "&H50000000"), (3, 4, "&H78000000")):
        layers.append(
            f"Dialogue: 3,{_t(start)},{_t(end)},Title3D,,0,0,0,,"
            f"{{\pos({x + dx},{y + dy})\frx18\fry-12\frz{rot}\fscy96"
            f"\bord0\shad0\1c{color}\fad(140,160)}}{body}")
    layers.append(
        f"Dialogue: 4,{_t(start)},{_t(end)},Title3D,,0,0,0,,"
        f"{{\pos({x},{y})\frx18\fry-12\frz{rot}"
        f"\fscx70\fscy70\t(0,180,\fscx104\fscy104)\t(180,300,\fscx100\fscy100)"
        f"\fad(120,140)}}{body}")
    return layers


def build_ass(shots, out_ass: Path, width: int = 1280, height: int = 720,
              style: dict | None = None, title: str = "",
              extra_tags: list[dict] | None = None,
              captions: list[str] | None = None,
              title_cues: list[dict] | None = None) -> dict:
    """shots: [{id,line,speaker_name,start,end,type,scene,filler}]（start/end 绝对秒）。"""
    accent, fg = _accent(style)
    body = _header(int(width), int(height), accent, fg)
    events: list[str] = []
    last_speaker = ""
    punch_index = 0
    dialogue_count = 0
    filler_count = 0
    for shot in shots or []:
        start = float(shot.get("start") or 0.0)
        end = float(shot.get("end") or start + 2.0)
        line = str(shot.get("line") or "").strip()
        speaker = str(shot.get("speaker_name") or "").strip()
        scene = str(shot.get("scene") or "").strip()
        is_filler = bool(shot.get("filler"))
        if line:
            dialogue_count += 1
            s = round(start + 0.08, 2)
            e = round(min(end - 0.04, s + max(1.6, min(6.5, end - start - 0.1))), 2)
            # R7：一段里有多句台词时（Muse 10s 段常合并 2–3 句），按句切成连续字幕，
            # 每句再按 20 全角字符折行。**先折行再转义**，否则 \N 会被转成字面量。
            cue_lines = [_esc(p) for p in _wrap_pieces(line)]
            events.append(f"Dialogue: 1,{_t(s)},{_t(e)},Main,,0,0,0,,"
                          f"{{\\fad(90,90)}}" + "\\N".join(cue_lines))
            if speaker and speaker != last_speaker:
                last_speaker = speaker
                events.append(f"Dialogue: 3,{_t(s)},{_t(min(e, s + 2.6))},Name,,0,0,0,,"
                              f"{{\\fad(80,120)}}{_esc(speaker)}")
            if dialogue_count % 2 == 1 or len(line) <= 8:
                punch = _punch_text(line)
                if punch:
                    punch_index += 1
                    rot = [-4, 3, -2, 5][punch_index % 4]
                    py = int(height * (0.16 + 0.05 * (punch_index % 3)))
                    px = int(width * (0.30 + 0.12 * (punch_index % 4)))
                    events.append(
                        f"Dialogue: 4,{_t(s)},{_t(min(e, s + 2.2))},Punch,,0,0,0,,"
                        f"{{\\pos({px},{py})\\frz{rot}\\fscx55\\fscy55"
                        f"\\t(0,120,\\fscx112\\fscy112)\\t(120,220,\\fscx100\\fscy100)}}"
                        f"{_esc(punch)}")
        if is_filler or not line:
            # R5：空镜**不再**打 "空镜" 这类内部标记（观众不该看到制作术语）。
            # 优先用分幕名/场景中文名做花字；都没有就这一镜不加字。
            label = scene or ""
            if captions:
                label = str(captions[(dialogue_count + filler_count) % len(captions)]) or label
            filler_count += 1
            if not label:
                continue
            # 每 3 条空镜出一条花字（大号弹入），其余用小标签，保证“综艺效果字幕”密度
            if is_filler and filler_count % 3 == 1:
                fy = int(height * (0.62 + 0.08 * (filler_count % 2)))
                fx = int(width * (0.10 + 0.10 * (filler_count % 3)))
                events.append(
                    f"Dialogue: 4,{_t(start + 0.2)},{_t(min(end - 0.05, start + 2.6))},Punch,,0,0,0,,"
                    f"{{\\pos({fx},{fy})\\frz{-3}\\fscx60\\fscy60"
                    f"\\t(0,140,\\fscx108\\fscy108)\\t(140,240,\\fscx100\\fscy100)}}"
                    f"{_esc(label)}")
            else:
                events.append(f"Dialogue: 2,{_t(start + 0.1)},{_t(max(start + 1.2, end - 0.1))},"
                              f"Tag,,0,0,0,,{{\\fad(140,160)}}{_esc(label)}")
    for cue in title_cues or []:
        try:
            s0 = float(cue.get("start") or 0.0)
            s1 = float(cue.get("end") or s0 + 2.4)
            text = str(cue.get("text") or "").strip()
        except (TypeError, ValueError):
            continue
        if text:
            events.extend(_layered_3d("Title3D", s0 + 0.1, s1 - 0.05, text, width, height))
    for tag in extra_tags or []:
        s = float(tag.get("start") or 0.0)
        e = float(tag.get("end") or s + 3.0)
        text = str(tag.get("text") or "")
        if text:
            events.append(f"Dialogue: 2,{_t(s)},{_t(e)},Tag,,0,0,0,,"
                          f"{{\\fad(140,160)}}{_esc(text)}")
    path = Path(out_ass)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body + "\n".join(events) + "\n", encoding="utf-8")
    return {"path": str(path), "count": len(events), "dialogue": dialogue_count,
            "punch": punch_index}
