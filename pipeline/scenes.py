"""场景合成：2D 绘景 2.5D 视差 + 全身角色分层 + 3D 片段（contract/SCENES.md）。

输入：
  outputs/scene_plates/{scene}.png          SDXL 2D 绘景（1280x720 无人物）
  outputs/characters/{id}/cutout_2d|cutout_3d/front.png  SDXL 全身透明角色
  outputs/characters/{id}/cutout/portrait.png    头肩 bust（回退）
  outputs/3d_scenes/{id}.mp4                3D 演播厅片段（tonight_show 混用）

输出：
  <workdir>/shots/{sid}.mp4                 1280x720/25fps 镜头片段（供 render 复用/拼接）
  <workdir>/scenes/meta/{sid}.json          scene/plate/char/plan/相机信息

合成规则：
- 背景按 ShotPlan 做缓慢推/移（zoompan）；角色以不同速率叠加产生 2.5D 视差；
- 全身角色按景别缩放（Medium≈0.54H、Wide≈0.30H；MCU/Close 裁上半身贴合），
  脚底锚定地平线（≈0.86H）并画柔和椭圆投影，按说话人分左右站位；
- 卡片镜头在绘景上烘焙标题字；可选 3D 场景片段作为背景。
"""
from __future__ import annotations

import json
from pathlib import Path

from . import config, fonts, shot_grammar, style, style_v2, utils

LOG = __import__("logging").getLogger("pipeline")

REPO = config.REPO_ROOT
SCENE_MAP_PATH = REPO / "contract" / "scene_map.json"
PLATE_DIR = REPO / "outputs" / "scene_plates"
THREED_DIR = REPO / "outputs" / "3d_scenes"
CHARS_DIR = config.CHARS_ROOT  # 真相源在 assets/v5/characters（见 config）

CARD_TYPES = ("title_card", "end_card", "meme_card")
DEFAULT_SCENE = {"plate": "late_night_desk", "camera": "slow push-in", "3d": None}
CHAR_NAME_MAP = {"老周": "laozhou", "阿栗": "ali", "大魏": "dawei",
                 "laozhou": "laozhou", "ali": "ali", "dawei": "dawei"}
# 全身角色：画面高度占比（lead 修正：Medium≈0.55、MCU≈0.45、Wide≈0.32；
# Close/Extreme 取全身图放大后让身体延伸出下画框，只保留头到胸口，且头顶距顶 6-13%，
# 保证头部直径≈0.12-0.18H、不超过 1/4H）
FULL_H = {"Wide": 0.32, "Wide / Establishing": 0.32, "Full": 0.45,
          "Medium": 0.55, "Medium Close-up": 0.45,
          "Close-up": 0.92, "Extreme Close-up": 1.02}
CLOSE_TOP = {"Close-up": 0.13, "Extreme Close-up": 0.06}
# 无裁切（全身图统一缩放）；保留常量仅用于旧 bust 回退逻辑
CROP_SRC = {}
BUST_H = {"Extreme Close-up": 1.10, "Close-up": 0.96, "Medium Close-up": 0.84,
          "Medium": 0.72, "Full": 0.60, "Wide": 0.52, "Wide / Establishing": 0.50}
HORIZON = 0.86   # 脚底/地平线 y ≈ 0.86H


def load_scene_map(path=None) -> dict:
    path = Path(path) if path else SCENE_MAP_PATH
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _key_candidates(template_id: str) -> list[str]:
    t = str(template_id or "").strip()
    if not t:
        return []
    keys = [t]
    for suffix in ("_5min", "_demo", "_scene"):
        if t.endswith(suffix):
            keys.append(t[: -len(suffix)])
    if t.startswith("demo_"):
        keys.append(t[5:])
    return keys


def template_scene(template_id: str, scene_map: dict | None = None) -> dict:
    scene_map = scene_map if scene_map is not None else load_scene_map()
    for key in _key_candidates(template_id):
        entry = scene_map.get(key)
        if isinstance(entry, dict):
            return {**DEFAULT_SCENE, **entry}
    return dict(DEFAULT_SCENE)


def _asset_character(asset: dict | None) -> str:
    asset = asset or {}
    for rel in asset.get("images") or []:
        parts = str(rel).replace("\\", "/").split("/")
        if "characters" in parts:
            i = parts.index("characters")
            if i + 1 < len(parts):
                cid = parts[i + 1]
                if (CHARS_DIR / cid).is_dir():
                    return cid
    name = str(asset.get("name") or "").strip()
    if name in CHAR_NAME_MAP:
        return CHAR_NAME_MAP[name]
    return "laozhou" if str(asset.get("role") or "") == "host" else "ali"


def cutout_path(char_id: str) -> Path | None:
    """优先全身透明 PNG，回退头肩 cutout。

    资产库实际布局是 `<cid>/cutout.png`（RGBA 全身，293x961 量级）；旧清单里
    只有 cutout_3d/cutout_2d 两个子目录，导致 26 个角色全部"无 cutout"。
    """
    for rel in ("cutout.png", "cutout_3d/front.png", "cutout_2d/front.png"):
        p = CHARS_DIR / char_id / rel
        if p.is_file() and p.stat().st_size > 5000:
            return p
    return None


def _is_full_body(path: Path | None) -> bool:
    """是否全身图：先看路径约定，再看图片纵横比（高/宽≥1.6 视为全身立绘）。"""
    if not path:
        return False
    p = str(path).replace("\\", "/")
    if "cutout_2d" in p or "cutout_3d" in p or p.endswith("/cutout.png"):
        return True
    try:
        from PIL import Image
        with Image.open(path) as im:
            w, h = im.size
        return bool(w and (h / float(w)) >= 1.6)
    except Exception:  # noqa: BLE001
        return False


def _plan(shot: dict, index: int) -> dict:
    plan = shot.get("shot_plan")
    if not isinstance(plan, dict) or not plan:
        plan = shot_grammar.plan_for_shot(shot, index)
    return plan


def _bg_motion(plan: dict, frames: int, strong: bool = False) -> tuple:
    movement = str(plan.get("movement") or "static")
    if "dolly" in movement or "push" in movement or "zoom" in movement:
        return 1.0, (1.14 if strong else 1.09), 0, -12
    if "pan" in movement or "truck" in movement:
        return 1.06, 1.06, 28 if "truck" in movement else 24, 10
    if "rack" in movement:
        return 1.03, 1.05, 0, -4
    return 1.02, 1.02, 0, 0


def _side_center(asset: dict | None, all_assets: list, index: int) -> float:
    """说话人站位（画面宽度比例）：host 右 0.74；guest 0.26/0.74 交替。"""
    asset = asset or {}
    if str(asset.get("role") or "") == "host":
        return 0.74
    guests = [a for a in all_assets if str(a.get("role")) != "host"]
    try:
        gi = [a.get("id") for a in guests].index(asset.get("id"))
    except ValueError:
        gi = index
    return 0.26 if gi % 2 == 0 else 0.74


def _write_text(workdir: Path, name: str, text: str) -> str:
    d = utils.ensure_dir(workdir / "text")
    p = d / name
    p.write_text(str(text), encoding="utf-8")
    return p.relative_to(workdir).as_posix()


def _drawtext(textfile: str, fontfile: str, size: int, x: str, y: str,
              color: str = "white", border: int = 3) -> str:
    return (f"drawtext=textfile='{textfile}':fontfile='{fontfile}':"
            f"fontsize={size}:fontcolor={color}:borderw={border}:bordercolor=black@0.55:"
            f"x={x}:y={y}")


def _make_shadow(workdir: Path, sid: str, width: int, height: int,
                 cx: int, cy: int, rx: int, ry: int, alpha: int = 110) -> Path:
    """柔和椭圆投影（透明 PNG），压在角色脚下。"""
    d = utils.ensure_dir(workdir / "scenes" / "shadow")
    out = d / f"{sid}.png"
    rx = max(20, int(rx)); ry = max(8, int(ry))
    cnt = max(1, int(round((rx * rx) / 900.0)))  # 半径越大越柔和
    expr = (f"if(lte(((X-{int(cx)})*(X-{int(cx)}))/{rx * rx}"
            f"+((Y-{int(cy)})*(Y-{int(cy)}))/{ry * ry},1),{alpha},0)")
    utils.ffmpeg(["-y", "-hide_banner", "-loglevel", "error",
                  "-f", "lavfi", "-i", f"color=c=black:s={width}x{height}:d=1",
                  "-vf", f"format=rgba,geq=r=0:g=0:b=0:a='{expr}',gblur=sigma={min(18, cnt + 6)}",
                  "-frames:v", "1", str(out)])
    return out


def _char_geometry(plan: dict, width: int, height: int, full_body: bool,
                   center_frac: float) -> dict:
    """返回角色 scale/crop/overlay 几何（全身按景别；bust 回退旧规则）。"""
    size = str(plan.get("size") or "Medium")
    if not full_body:
        ch = max(120, int(height * BUST_H.get(size, 0.72)))
        return {"mode": "bust", "target_h": ch, "crop_h": None,
                "center": center_frac, "y": height - ch + 40,
                "shadow": False}
    target_h = max(90, int(height * FULL_H.get(size, 0.55)))
    geo = {"mode": "full", "target_h": target_h, "center": center_frac,
           "crop_h": None, "shadow": True, "y": 0}
    if size in CLOSE_TOP:
        # Close/Extreme：全身图放大，身体延伸出下画框，只保留头到胸口；
        # 头顶距画面顶 6-13%，头部直径≈0.12-0.18H（不超过 1/4H）。
        top = int(height * CLOSE_TOP[size])
        geo["y"] = top
        geo["foot_y"] = int(height * 0.99)
        geo["close"] = True
    else:  # Medium/MCU/Wide：脚底锚定地平线 y≈0.86H
        foot_y = int(height * HORIZON)
        geo["y"] = foot_y - target_h
        geo["foot_y"] = foot_y
    return geo


def compose_shot(shot: dict, assets: list, workdir: Path, out_path: Path,
                 template_id: str = "", plate: Path | None = None,
                 bg_video: Path | None = None, width: int = 1280,
                 height: int = 720, fps: int = 25, index: int = 0,
                 force: bool = False, style_name: str = "2d_anime",
                 profile: dict | None = None) -> dict:
    """合成单个镜头片段（背景视差 + 全身角色分层 + 投影/可选 3D 背景/字卡）。

    profile: style_v2 风格 profile（优先决定 plate/cutout/调色/包装，调色在 render 阶段应用）。
    """
    workdir = Path(workdir)
    out_path = Path(out_path)
    duration = max(0.5, float(shot.get("duration") or 4.0))
    frames = max(2, int(round(duration * fps)))
    plan = _plan(shot, index)
    typ = str(shot.get("type") or "")
    asset = None
    for a in assets or []:
        if str(a.get("id")) == str(shot.get("speaker")):
            asset = a
            break
    char_id = _asset_character(asset) if asset and typ not in CARD_TYPES and typ != "b_roll" else None
    if char_id and profile:
        # style_v2：按 profile.character_style 选 cutout_3d/cutout_2d，缺失回退并记录
        char_png, cutout_style, cutout_fallback = style_v2.cutout_path(profile, char_id)
    elif char_id:
        # 风格系统：3d_anime 优先 cutout_3d/front.png，缺失回退 cutout_2d
        char_png, cutout_style, cutout_fallback = style.cutout_path(char_id, style_name)
    else:
        char_png, cutout_style, cutout_fallback = None, "none", False
    full_body = _is_full_body(char_png)
    font_info = fonts.ensure_fonts(workdir)
    body_font = font_info["body"]["file"]
    size = str(plan.get("size") or "Medium")
    z0, z1, pan, drift = _bg_motion(plan, frames, strong=(typ == "b_roll"))

    inputs: list[str] = []
    fc: list[str] = []
    bg_src = bg_video if (bg_video and Path(bg_video).is_file()
                          and (typ == "b_roll" or index % 4 == 0)) else plate
    use_video_bg = bg_src is not None and str(bg_src).lower().endswith(".mp4")
    if use_video_bg:
        inputs += ["-stream_loop", "-1", "-i", str(bg_src)]
    else:
        inputs += ["-loop", "1", "-i", str(bg_src)]
    zexpr = f"{z0}+({z1 - z0})*on/{max(1, frames)}"
    xexpr = f"(iw-iw/zoom)/2+{pan}*on/{max(1, frames)}"
    fc.append(f"[0:v]scale={width * 2}:{height * 2}:force_original_aspect_ratio=increase,"
              f"crop={width * 2}:{height * 2},"
              f"zoompan=z='{zexpr}':x='{xexpr}':y='(ih-ih/zoom)/2':"
              f"d={frames}:s={width}x{height}:fps={fps}[bg]")
    last = "bg"
    geo = None
    if char_png is not None and char_png.is_file():
        inputs += ["-loop", "1", "-i", str(char_png)]
        geo = _char_geometry(plan, width, height, full_body,
                             _side_center(asset, assets or [], index))
        ch = geo["target_h"]
        if full_body and geo.get("crop_h"):
            # 裁上半身：源图 1024x1536 上部 crop_h，按原始宽高比缩放（避免拉伸大头）
            cw = max(40, int(round(ch * (1024 / geo["crop_h"]))))
            fc.append(f"[1:v]crop=1024:{geo['crop_h']}:0:0,"
                      f"scale={cw}:{ch},format=rgba[char0]")
        else:
            cw = max(40, int(round(ch * (2 / 3)))) if full_body else ch
            fc.append(f"[1:v]scale={cw}:{ch},format=rgba[char0]")
        char_x = max(0, int(width * geo["center"] - cw / 2))
        # 全身默认边缘干净不羽化；但裁上半身的 Close/MCU 底部是硬切边，
        # 做一层轻羽化让它融入场景，避免"漂浮大头"观感。
        if full_body and geo.get("crop_h"):
            fc.append("[char0]split[char1][mask0]")
            fc.append("[mask0]alphaextract,vignette=angle=PI/5.2,boxblur=4[mask]")
            fc.append("[char1][mask]alphamerge,"
                      "eq=brightness=-0.05:saturation=0.92:contrast=1.03,"
                      "colorbalance=rs=-0.02:bs=0.03[char]")
        elif full_body:
            fc.append("[char0]eq=brightness=-0.05:saturation=0.92:contrast=1.03,"
                      "colorbalance=rs=-0.02:bs=0.03[char]")
        else:
            fc.append("[char0]split[char1][mask0]")
            fc.append("[mask0]alphaextract,vignette=angle=PI/4.4,boxblur=5[mask]")
            fc.append("[char1][mask]alphamerge,"
                      "eq=brightness=-0.06:saturation=0.90:contrast=1.03,"
                      "colorbalance=rs=-0.02:bs=0.03[char]")
        if geo.get("shadow"):
            shadow = _make_shadow(workdir, str(shot.get("id")), width, height,
                                  int(width * geo["center"]), geo["foot_y"],
                                  int(cw * 0.62), max(10, int(height * 0.025)))
            inputs += ["-loop", "1", "-i", str(shadow)]
            fc.append("[2:v]null[shadow]")
            fc.append("[bg][shadow]overlay=0:0[bgsh]")
            last = "bgsh"
        fc.append(f"[{last}][char]overlay=x='{char_x}+{drift}*t/{duration:.2f}':"
                  f"y='{geo['y']}':eval=frame:format=auto[over]")
        last = "over"

    post = ["vignette=PI/4.6",
            "drawbox=x=0:y=ih*0.86:w=iw:h=ih*0.14:color=black@0.22:t=fill"]
    accent = str((asset or {}).get("accent_color") or "#F5C518").replace("#", "0x")
    if typ not in CARD_TYPES:
        tf_brand = _write_text(workdir, f"scene_{shot.get('id')}_brand.txt",
                               "造片局 · AI 微综艺")
        post.append(_drawtext(tf_brand, body_font, 22, "44", "32",
                              color="white@0.62", border=1))
        name = str(shot.get("speaker_name") or (asset or {}).get("name") or "")
        role = str((asset or {}).get("title") or
                   ("主持人" if (asset or {}).get("role") == "host" else "嘉宾"))
        if name:
            tf_name = _write_text(workdir, f"scene_{shot.get('id')}_name.txt", name)
            tf_role = _write_text(workdir, f"scene_{shot.get('id')}_role.txt", role)
            post.append(f"drawbox=x=0:y=ih-210:w=8:h=70:color={accent}@0.95:t=fill")
            post.append(_drawtext(tf_name, body_font, 40, "30", "h-210",
                                  color=accent, border=2))
            post.append(_drawtext(tf_role, body_font, 22, "32", "h-158",
                                  color="white@0.82", border=1))
    if typ in CARD_TYPES:
        title = str(shot.get("title") or shot.get("punchline") or "")
        subtitle = str(shot.get("subtitle") or "")
        if typ == "meme_card" and not title:
            title = str(shot.get("punchline") or "")
        tf_title = _write_text(workdir, f"scene_{shot.get('id')}_t.txt", title)
        tf_sub = _write_text(workdir, f"scene_{shot.get('id')}_s.txt", subtitle or " ")
        tf_brand = _write_text(workdir, f"scene_{shot.get('id')}_b.txt",
                               "造片局 · AI 微综艺")
        fs = 72 if len(title) <= 14 else (56 if len(title) <= 22 else 42)
        post.append(_drawtext(tf_title, body_font, fs, "(w-text_w)/2", "260"))
        if subtitle:
            post.append(_drawtext(tf_sub, body_font, 30, "(w-text_w)/2", "380"))
        post.append(_drawtext(tf_brand, body_font, 24, "44", "36", color="white@0.75"))
    fc.append(f"[{last}]" + ",".join(post) + ",format=yuv420p[v]")

    args = inputs + [
        "-filter_complex", ";".join(fc), "-map", "[v]",
        "-t", f"{duration:.3f}", "-r", str(fps), "-an",
        "-c:v", "libx264", "-preset", config.X264_PRESET,
        "-crf", config.X264_CRF, "-pix_fmt", "yuv420p", str(out_path.resolve()),
    ]
    utils.ffmpeg(args, cwd=workdir)
    return {"id": shot.get("id"), "type": typ, "duration": duration,
            "plan": plan, "scene": template_id,
            "style": style_name,
            "style_profile": (profile or {}).get("preset") if profile else "",
            "style_palette": (profile or {}).get("palette") if profile else "",
            "style_prompt_prefix": (profile or {}).get("prompt_prefix") if profile else "",
            "plate": str(plate) if plate else "",
            "bg_video": str(bg_src) if use_video_bg else "",
            "character": char_id or "", "char_image": str(char_png) if char_png else "",
            "cutout_style": cutout_style, "cutout_fallback": cutout_fallback,
            "char_mode": (geo or {}).get("mode", ""),
            "char_height": (geo or {}).get("target_h"),
            "char_center": (geo or {}).get("center"),
            "char_y": (geo or {}).get("y"),
            "bg_motion": {"z0": z0, "z1": z1, "pan": pan, "drift": drift}}


def compose_episode(data: dict, workdir: Path, template_id: str | None = None,
                    width: int = 1280, height: int = 720, fps: int = 25,
                    force: bool = True, scene_map: dict | None = None,
                    style_name: str | None = None,
                    profile: dict | None = None) -> dict:
    """把一集所有镜头合成为场景 clip，写入 <workdir>/shots/{sid}.mp4。

    style_name: 覆盖风格（3d_anime / 2d_anime）；默认按 contract/style_map.json 的模板映射。
    profile: style_v2 风格 profile（优先；决定 plate 后缀/cutout/风格元数据）。
    """
    workdir = Path(workdir)
    shots = list(data.get("shots") or [])
    assets = list(data.get("assets") or [])
    episode = data.get("episode") or {}
    template_id = template_id or data.get("format_id") or episode.get("id") or ""
    scene = template_scene(template_id, scene_map)
    if profile:
        style_name = style_v2.render_style(profile)
        style_dict = {"label": profile.get("preset"), "prompt_prefix": style_v2.prompt_prefix(profile)}
        plate, plate_style, plate_fallback = style_v2.plate_path(
            profile, str(scene.get("plate") or ""))
    else:
        style_name, style_dict = style.resolve(template_id, override=style_name)
        plate, plate_style, plate_fallback = style.plate_path(
            str(scene.get("plate") or ""), style_name, style_dict)
    if not plate.is_file():
        raise RuntimeError(f"缺少场景 plate: {scene.get('plate')}")
    bg_video = None
    # 场景全部使用 2D 绘景 + AI 角色分层（H3 负责动态立体感）
    if False and scene.get("3d"):
        p = THREED_DIR / f"{scene['3d']}.mp4"
        if p.is_file():
            bg_video = p
        else:
            LOG.info("3D 场景 %s 暂缺，先走 2D plate 混用", p)
    shots_dir = utils.ensure_dir(workdir / "shots")
    meta_dir = utils.ensure_dir(workdir / "scenes" / "meta")
    info = {"scene": scene, "template_id": template_id, "plate": str(plate),
            "style": style_name, "style_label": style_dict.get("label"),
            "style_prefix": style_dict.get("prompt_prefix"),
            "style_profile": (profile or {}).get("preset") if profile else "",
            "style_palette": (profile or {}).get("palette") if profile else "",
            "style_transition": (profile or {}).get("transition") if profile else "",
            "expected_plate": (f"{str(scene.get('plate') or '')}{profile.get('plate_suffix') or ''}.png"
                               if profile else ""),
            "plate_style": plate_style, "plate_fallback": plate_fallback,
            "three_d": str(bg_video) if bg_video else "", "shots": {},
            "card_sids": []}
    for i, shot in enumerate(shots):
        sid = str(shot.get("id"))
        out = shots_dir / f"{sid}.mp4"
        meta_path = meta_dir / f"{sid}.json"
        reuse = False
        if not force and out.is_file() and out.stat().st_size > 5000 and meta_path.is_file():
            reuse = True
            try:
                info["shots"][sid] = json.loads(meta_path.read_text(encoding="utf-8"))
            except ValueError:
                info["shots"][sid] = {"id": sid}
        if not reuse:
            try:
                shot_info = compose_shot(shot, assets, workdir, out,
                                         template_id=template_id, plate=plate,
                                         bg_video=bg_video, width=width,
                                         height=height, fps=fps, index=i,
                                         style_name=style_name, profile=profile)
            except Exception as exc:  # noqa: BLE001
                LOG.warning("场景合成失败 %s（回退纯绘景）: %s", sid, exc)
                shot_info = compose_shot(shot, [], workdir, out, template_id=template_id,
                                         plate=plate, bg_video=bg_video, width=width,
                                         height=height, fps=fps, index=i,
                                         style_name=style_name, profile=profile)
            utils.write_json(meta_path, shot_info)
            info["shots"][sid] = shot_info
        if str(shot.get("type")) in CARD_TYPES:
            info["card_sids"].append(sid)
    LOG.info("场景合成完成: %s（%d 镜，plate=%s，3d=%s）",
             template_id, len(shots), plate.name,
             bg_video.name if bg_video else "none")
    return info
