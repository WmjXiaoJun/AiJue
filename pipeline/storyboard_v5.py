# -*- coding: utf-8 -*-
"""V5.4 分镜表（storyboard）：把剪辑计划 + 成片镜头生成分镜数据/HTML/PNG。

产物（outputs/templates_v5/<id>/）：
- storyboard.json：每镜 {id,index,type,scene,shot_size,camera,transition_out,j_cut,l_cut,
  duration,start,line,speaker,thumbnail}
- storyboard.html：暗色分镜表（可直接浏览器看）
- storyboard.png：分镜缩略图网格（4 列）
"""
from __future__ import annotations

import html
import json
import logging
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from . import config, utils

LOG = logging.getLogger("pipeline")
REPO = Path(config.REPO_ROOT)

SIZE_COLORS = {
    "EWS": "#6b8cae", "WS": "#7fa6c9", "MS": "#e0b84c", "MCU": "#e8a13c",
    "CU": "#e07a3c", "ECU": "#d9534f", "OTS": "#9b7fd4", "DETAIL": "#5cb85c",
    "INSERT": "#5bc0de",
}
TRANS_LABEL = {
    "cut": "硬切", "dissolve": "溶接", "fade": "淡入淡出", "fadeblack": "黑场",
    "wipeleft": "左划", "wiperight": "右划", "wipeup": "上划", "wipedown": "下划",
    "hblur": "水平模糊", "vblur": "垂直模糊", "circleopen": "圆开",
    "radial": "径向", "pixelize": "像素化", "zoomin": "推近", "zoom": "推近",
    "slideleft": "滑入", "slideright": "滑出", "smoothleft": "平滑左",
    "smoothright": "平滑右", "vertopen": "纵向开", "vertclose": "纵向合",
}


def _thumb(shot_mp4: Path, out: Path, *, at: float = 1.0) -> str:
    if not shot_mp4.is_file():
        return ""
    try:
        utils.ffmpeg(["-y", "-hide_banner", "-loglevel", "error",
                      "-ss", f"{max(0.0, at):.2f}", "-i", str(shot_mp4),
                      "-frames:v", "1", "-vf", "scale=480:-2", str(out)])
        return str(out)
    except Exception as exc:  # noqa: BLE001
        LOG.warning("storyboard 缩略图失败 %s: %s", shot_mp4.name, exc)
        return ""


def build_storyboard(plan: dict, paths: dict) -> dict:
    shots = plan.get("shots") or []
    meta = plan.get("meta") or {}
    workdir = Path(paths["workdir"])
    sb_dir = utils.ensure_dir(workdir / "storyboard")
    shots_dir = Path(paths["shots_dir"])
    rows = []
    cursor = 0.0
    body_index = 0
    for shot in shots:
        dur = float(shot.get("duration") or 0.0)
        start = float(shot.get("_edit_start", cursor))
        is_card = str(shot.get("type")) == "card"
        edit = shot.get("edit") or {}
        sid = str(shot.get("id"))
        thumb = ""
        if not is_card:
            thumb = _thumb(shots_dir / f"{sid}.mp4",
                           sb_dir / f"{sid}.jpg", at=min(1.0, max(0.2, dur * 0.4)))
            body_index += 1
        rows.append({
            "id": sid,
            "index": body_index if not is_card else 0,
            "type": "card" if is_card else "shot",
            "scene": shot.get("scene"),
            "role": shot.get("role"),
            "character": shot.get("character"),
            "line": shot.get("line") or "",
            "speaker": shot.get("speaker_name") or "",
            "duration": round(dur, 3),
            "start": round(start, 3),
            "shot_size": edit.get("shot_size") or ("CARD" if is_card else ""),
            "camera": (shot.get("camera") or {}).get("type") or (edit.get("camera") or ""),
            "transition_out": edit.get("transition_out") or {},
            "j_cut": edit.get("j_cut", 0.0),
            "l_cut": edit.get("l_cut", 0.0),
            "insert": bool(edit.get("insert")),
            "thumbnail": thumb,
        })
        cursor = start + dur
    total = round(cursor, 3)
    summary = meta.get("edit") or {}
    return {
        "template": meta.get("template") or "",
        "label": meta.get("label") or "",
        "style": (plan.get("episode") or {}).get("style") or "",
        "total": total,
        "shots": rows,
        "edit": summary,
    }


def write_storyboard(storyboard: dict, out_dir: Path) -> dict:
    out_dir = Path(out_dir)
    json_path = out_dir / "storyboard.json"
    utils.write_json(json_path, storyboard)
    html_path = out_dir / "storyboard.html"
    html_path.write_text(_render_html(storyboard), encoding="utf-8")
    png_path = out_dir / "storyboard.png"
    try:
        _render_png(storyboard, png_path)
    except Exception as exc:  # noqa: BLE001
        LOG.warning("storyboard.png 渲染失败: %s", exc)
        png_path = None
    return {"json": str(json_path), "html": str(html_path),
            "png": str(png_path) if png_path else ""}


def _render_html(sb: dict) -> str:
    cards = []
    for row in sb.get("shots") or []:
        if row.get("type") == "card":
            cards.append(
                '<div class="card card-title"><div class="idx">片头/片尾</div>'
                f'<div class="thumb" style="background:#222"></div>'
                f'<div class="meta">时长 {row["duration"]}s</div></div>')
            continue
        edit = row.get("transition_out") or {}
        trans = TRANS_LABEL.get(str(edit.get("mode")), edit.get("mode") or "")
        size_color = SIZE_COLORS.get(str(row.get("shot_size")), "#888")
        thumb = row.get("thumbnail") or ""
        img = f'<img class="thumb" src="{Path(thumb).name}" alt="">' if thumb else '<div class="thumb"></div>'
        cards.append(f'''<div class="card">
  <div class="idx">#{row["index"]:02d} <span class="dur">{row["duration"]}s</span></div>
  {img}
  <div class="badges">
    <span class="badge" style="background:{size_color}">{row.get("shot_size")}</span>
    <span class="badge cam">{html.escape(str(row.get("camera") or ""))}</span>
    <span class="badge trans">{html.escape(str(trans))}</span>
  </div>
  <div class="meta"><b>{html.escape(str(row.get("scene") or ""))}</b></div>
  <div class="line">{html.escape(str(row.get("line") or ""))}</div>
</div>''')
    summary = sb.get("edit") or {}
    chips = "".join(f'<span class="chip">{html.escape(str(x))}</span>'
                    for x in (summary.get("camera_types") or []))
    sizes = "".join(f'<span class="chip" style="background:{SIZE_COLORS.get(str(x), "#666")}">{x}</span>'
                    for x in (summary.get("shot_sizes") or []))
    return f'''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<title>{html.escape(str(sb.get("label") or sb.get("template")))} · 分镜表</title>
<style>
body{{background:#111318;color:#e8e6e0;font-family:system-ui,"Microsoft YaHei",sans-serif;margin:24px}}
h1{{font-size:20px}} .sub{{color:#9aa0a8;margin-bottom:16px}}
.grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(240px,1fr));gap:14px}}
.card{{background:#1b1e26;border:1px solid #2a2f3a;border-radius:10px;padding:10px;display:flex;flex-direction:column;gap:6px}}
.card-title{{opacity:.7}}
.idx{{font-size:12px;color:#f5c518}} .dur{{color:#9aa0a8}}
.thumb{{width:100%;aspect-ratio:16/9;object-fit:cover;border-radius:6px;background:#0d0f14}}
.badges{{display:flex;gap:6px;flex-wrap:wrap}}
.badge{{font-size:11px;padding:2px 7px;border-radius:9px;color:#111;font-weight:600}}
.cam{{background:#2f3a4f;color:#dbe6ff}} .trans{{background:#3a2f4f;color:#e6dbff}}
.meta{{font-size:12px;color:#c9c4b8}} .line{{font-size:12px;color:#9aa0a8;min-height:2.4em}}
.chip{{display:inline-block;font-size:10px;padding:2px 7px;border-radius:8px;background:#2a2f3a;color:#cfd6e0;margin:0 4px 4px 0}}
</style></head><body>
<h1>{html.escape(str(sb.get("label") or ""))} · 分镜表</h1>
<div class="sub">{sb.get("template")} · {sb.get("style")} · 共 {len(sb.get("shots") or [])} 个片段 · 总时长 {sb.get("total")}s<br>
剪辑签名：{html.escape(str(summary.get("signature") or ""))} · 硬切比例 {summary.get("hard_cut_ratio", "")} · J-cut {summary.get("j_cuts", 0)} / L-cut {summary.get("l_cuts", 0)}</div>
<div class="sub">景别：{sizes}</div><div class="sub">运镜：{chips}</div>
<div class="grid">{''.join(cards)}</div></body></html>'''


def _render_png(sb: dict, out: Path, *, cols: int = 4, cell_w: int = 320) -> Path:
    from . import fonts
    font_info = fonts.ensure_fonts(out.parent / "_fonts")
    body_font = ImageFont.truetype(str(font_info["body"]["file"]), 13)
    title_font = ImageFont.truetype(str(font_info["body"]["file"]), 17)
    rows = [r for r in (sb.get("shots") or []) if r.get("type") != "card"]
    if not rows:
        raise ValueError("没有可绘制的镜头")
    thumb_h = int(cell_w * 9 / 16)
    label_h = 58
    cell_h = thumb_h + label_h
    n = len(rows)
    grid_rows = (n + cols - 1) // cols
    pad = 8
    header = 46
    canvas = Image.new("RGB", (cols * cell_w, header + grid_rows * cell_h), (17, 19, 24))
    draw = ImageDraw.Draw(canvas)
    summary = sb.get("edit") or {}
    draw.text((12, 12), f"{sb.get('label') or sb.get('template')} · 分镜表 · "
                        f"{summary.get('signature', '')}", font=title_font, fill=(245, 197, 24))
    for i, row in enumerate(rows):
        cx = (i % cols) * cell_w
        cy = header + (i // cols) * cell_h
        thumb = row.get("thumbnail")
        if thumb and Path(thumb).is_file():
            img = Image.open(thumb).convert("RGB")
            img = img.resize((cell_w, thumb_h))
            canvas.paste(img, (cx, cy))
        else:
            draw.rectangle([cx, cy, cx + cell_w, cy + thumb_h], fill=(28, 31, 38))
        size = str(row.get("shot_size") or "")
        color = SIZE_COLORS.get(size, "#888")
        draw.rectangle([cx, cy + thumb_h, cx + cell_w, cy + thumb_h + 4], fill=color)
        tx = cx + 6
        ty = cy + thumb_h + 8
        draw.text((tx, ty), f"#{row.get('index'):02d}  {size}  {row.get('camera') or ''}",
                  font=body_font, fill=(230, 230, 235))
        draw.text((tx, ty + 17), f"{row.get('duration')}s · {row.get('scene') or ''}",
                  font=body_font, fill=(160, 165, 175))
        trans = (row.get("transition_out") or {}).get("mode") or ""
        trans_label = TRANS_LABEL.get(str(trans), str(trans))
        line = str(row.get("line") or "")
        if len(line) > 18:
            line = line[:18] + "…"
        draw.text((tx, ty + 34), f"→ {trans_label}  {line}", font=body_font,
                  fill=(200, 185, 150))
    out.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(out)
    return out


__all__ = ["build_storyboard", "write_storyboard", "SIZE_COLORS", "TRANS_LABEL"]
