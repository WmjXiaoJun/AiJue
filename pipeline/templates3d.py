# -*- coding: utf-8 -*-
"""V8 模板 3D 化：把 16 个 V5 模板（AI 微综艺，31–45s）重做成 3D 电影感样片。

复用：
- 现有 `outputs/templates_v5/<id>/work/episode.json` 时间线（镜号/时长/台词/旁白 audio）
- 现有片头/片尾卡片 clip（1.4/1.6s，沿用品牌卡）
- `render_v5.assemble()`：per-cut 转场 / 字幕 / BGM / 调色 / 包装 / 两遍 loudnorm
- `pipeline.cinema3d_local` 的本地 ComfyUI H3 T2VA（4step turbo 768×432）

产出：
- outputs/templates_3d/<id>/shots/<sid>.mp4（本地 H3 3D 镜头）
- outputs/templates_3d/<id>/final.mp4（1280×720/25fps 成片）
- contract/v5/cinema3d/templates3d/<id>.json（逐镜 3D brief，可编辑）
- outputs/templates_3d/progress.json（生成进度，供前端/看板读取）

CLI：
    python -m pipeline.templates3d --status
    python -m pipeline.templates3d --generate --templates all [--resolution 768x432]
    python -m pipeline.templates3d --assemble-watch          # 边生成边剪辑已完成模板
    python -m pipeline.templates3d --assemble --templates all
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
V5_OUT = REPO / "outputs/templates_v5"
OUT3D = REPO / "outputs/templates_3d"
BRIEFS_DIR = REPO / "contract/v5/cinema3d/templates3d"
PROGRESS = OUT3D / "progress.json"
FFPROBE = REPO / "tools/node_modules/ffprobe-static/bin/win32/x64/ffprobe.exe"

TEMPLATES = [
    "tonight_show", "classics", "history_talk", "masked_singer", "roundtable",
    "national_treasure", "hot_ones", "mystery", "qipashuo", "shark_tank",
    "slow_life", "snl_sketch", "standup", "taskmaster", "traitors", "history_mystery",
    "real_street", "real_cafe", "real_kitchen",
]

# 每模板的 3D 影调 + 场景（key 与 v5 场景 id 对齐）
STYLE3D = {
    "tonight_show": {
        "look": "glamorous 3D animated late-night talk show, neon-lit city skyline backdrop, glossy desk, warm studio spotlights, shallow depth of field",
        "scenes": {
            "neon_skyline": "a rooftop terrace at night overlooking a neon megacity skyline, glowing signs and floating light",
            "late_night_studio": "a glossy late-night talk show set with a curved desk, guest armchairs, warm spotlights and a neon city backdrop",
            "neon_alley": "a narrow neon-lit alley after rain, reflections on wet pavement",
            "backstage_neon": "a backstage corridor lined with glowing bulbs and mirrors",
        },
        "ambience": "soft studio room tone, a distant audience murmur",
    },
    "classics": {
        "look": "high-end 3D animated historical Chinese film, ink-black wood, gold leaf, candlelight, volumetric dust",
        "scenes": {
            "archive_vault": "an ancient Chinese archive hall with towering shelves of bamboo slips and scrolls, warm lamp light",
            "palace_hall": "a grand ancient Chinese palace hall with red lacquer columns, silk banners and bronze braziers",
            "scholar_study": "a candlelit scholar's study with scrolls, ink stones and a wooden desk",
            "temple_court": "an ancient temple courtyard with bronze bells, stone steps and incense smoke",
        },
        "ambience": "quiet hall room tone, a distant bronze bell",
    },
    "history_talk": {
        "look": "cinematic 3D ink-wash chronicle, blue-grey moonlit atmosphere, silk and bronze",
        "scenes": {
            "battlefield": "a misty ancient battlefield at dawn, broken banners and spears in the mud",
            "palace_hall": "an ancient Chinese palace hall at night, candles and deep shadows",
            "scholar_study": "a moonlit study with scroll maps and hanging brushes",
            "war_council": "an ancient war council tent with a sand table, copper lamps and silk maps",
        },
        "ambience": "wind outside, a quiet candle flame",
    },
    "masked_singer": {
        "look": "futuristic 3D holographic concert film, lasers, glossy black stage, star-field audience",
        "scenes": {
            "audience_star_sea": "a vast concert audience holding glowing lights like a star sea",
            "hologram_stage": "a futuristic holographic concert stage with floating light rings and laser beams",
            "light_tunnel": "a tunnel of prismatic light and floating particles",
            "mirror_room": "a mirrored dressing room with ring lights and reflective surfaces",
        },
        "ambience": "deep concert hum, distant crowd roar",
    },
    "roundtable": {
        "look": "warm Renaissance-oil-painting style 3D film, candlelight, velvet and old books",
        "scenes": {
            "candle_gallery": "a candlelit gallery of old oil paintings, warm gold light",
            "oil_library": "a library of leather books and oil paintings, a fire in the hearth",
            "oil_roundtable": "a round wooden table in a warm salon with velvet chairs and candles",
        },
        "ambience": "fire crackle, quiet room tone",
    },
    "national_treasure": {
        "look": "grand 3D museum film, dark lacquered columns, gold display cases, volumetric light",
        "scenes": {
            "archive_vault": "a dark museum archive with bronze vessels and sealed scrolls",
            "display_case": "a black stone pedestal with a glass display case under a single spotlight",
            "palace_hall": "a grand Chinese palace hall with gold details and silk banners",
        },
        "ambience": "quiet museum room tone, faint metallic shimmer",
    },
    "hot_ones": {
        "look": "warm 35mm film look 3D animated interview, amber practical lights, soft bokeh",
        "scenes": {
            "cafe_window": "a warm cafe by a rainy window, amber pendant lights and bokeh",
            "film_strip": "a film-strip lit corridor with soft amber light",
            "interview_sofa": "a cozy interview lounge with leather sofas and warm lamps",
            "kitchen_film": "a warm film-lit kitchen with copper pots and steam",
        },
        "ambience": "soft room tone, rain on glass",
    },
    "mystery": {
        "look": "film-noir 3D animation, rain, venetian-blind shadows, single hanging lamp, smoke",
        "scenes": {
            "interrogation_room": "a dim interrogation room with a single hanging lamp and venetian blind shadows",
            "noir_office": "a noir detective office at night, rain on the window, venetian blinds",
            "police_archive": "a police archive with rows of files and a desk lamp",
            "rainy_street": "a rainy night street with neon reflections and fog",
        },
        "ambience": "rain on the window, a distant siren",
    },
    "qipashuo": {
        "look": "dramatic 3D debate arena, silk drapes, glowing podiums, strong spotlights",
        "scenes": {
            "court_audience": "a debate arena audience in warm shadow, faces lit by stage light",
            "ink_arena": "a modern debate arena with ink-wash silk curtains and glowing podiums",
            "scroll_split": "a long hanging scroll backdrop illuminated by stage lights",
            "silk_debate_hall": "a debate hall draped in pale silk, dramatic spotlights and microphone stands",
        },
        "ambience": "hall reverb, a tense hush",
    },
    "shark_tank": {
        "look": "blue-lit 3D startup pitch film, glass and blueprint walls, holographic charts",
        "scenes": {
            "blueprint_wall": "a wall of glowing blueprints and diagrams in a dark lab",
            "pitch_boardroom": "a glass boardroom with blue light, a long table and city lights",
            "prototype_lab": "a prototype lab with robotic arms and glowing screens",
            "whiteboard_lab": "a whiteboard lab covered in diagrams, blue monitor glow",
        },
        "ambience": "quiet electronics hum, a low room tone",
    },
    "slow_life": {
        "look": "cozy handcrafted clay-diorama 3D animation, soft window light, miniature textures",
        "scenes": {
            "clay_diorama": "a cozy clay miniature room with tiny furniture and soft window light",
            "clay_garden": "a tiny clay garden with miniature plants, a watering can and morning light",
            "clay_kitchen": "a miniature clay kitchen with warm stove light and tiny pots",
            "clay_street": "a miniature clay street with tiny shops and warm lamps",
        },
        "ambience": "soft morning room tone, tiny birds",
    },
    "snl_sketch": {
        "look": "chalk-drawn theatre 3D animation, blackboard textures, spotlight, hand-drawn props",
        "scenes": {
            "audience_sketch": "a theatre audience in warm dark, faces lit by a chalk stage",
            "chalk_stage": "a stage made of chalk drawings on a blackboard, a single spotlight",
            "costume_room_chalk": "a backstage costume room drawn in chalk, mirrors and racks",
            "sketch_backdrop": "a hand-drawn sketch backdrop with chalk clouds and props",
        },
        "ambience": "theatre hush, distant laughter",
    },
    "standup": {
        "look": "retro VHS-lit 3D comedy club, warm tungsten light, brick wall, grain",
        "scenes": {
            "brick_wall": "a comedy club stage with a red brick wall and a microphone stand",
            "city_vhs": "a VHS-lit city street at night, warm tungsten signs",
            "dressing_room_vhs": "a retro dressing room with bulb mirrors and a worn sofa",
        },
        "ambience": "club room tone, a distant laugh",
    },
    "taskmaster": {
        "look": "playful candy-colored 3D game show, giant props, confetti, bright studio lights",
        "scenes": {
            "confetti_stage": "a colorful game-show stage with confetti falling and giant props",
            "game_stage_collage": "a bright game stage with oversized props and painted panels",
            "leaderboard_collage": "a glowing leaderboard wall in a playful studio",
            "prop_table": "a table covered with playful props, prizes and paper cutouts",
        },
        "ambience": "bright studio room tone, distant applause",
    },
    "traitors": {
        "look": "baroque candlelit 3D film, stone castle hall, hundreds of candles, gold and shadow",
        "scenes": {
            "candle_corridor": "a long castle corridor lined with hundreds of candles",
            "castle_hall_baroque": "a baroque castle hall with a long stone table and candelabras",
            "crypt": "a stone crypt lit by candles, cold shadows",
            "stone_roundtable": "a round stone table in a candlelit hall, heavy drapery",
        },
        "ambience": "candle flames, a deep stone reverb",
    },
    "history_mystery": {
        "look": "parchment-and-brass 3D archival film, dusty light, old maps, magnifiers",
        "scenes": {
            "ancient_map": "a dim room with a huge ancient map, brass compasses and candlelight",
            "archive_vault": "an archive vault with dusty shelves of old case files",
            "excavation_site": "an archaeological excavation at dusk, lanterns and mist",
            "great_wall": "the Great Wall winding over misty blue-green mountains",
        },
        "ambience": "dusty room tone, a distant wind",
    },

    "real_street": {
        "look": "photorealistic cinematic live-action documentary, real Chinese city street at night, natural skin texture, practical neon lights, handheld 35mm film look, shallow depth of field, realistic human faces",
        "realistic": True,
        "scenes": {
            "street_night": "a real Chinese city street at night, wet asphalt, neon shop signs, pedestrians in the background",
            "subway_exit": "the mouth of a subway station at night, warm lights from the stairs, people walking past",
            "convenience_store": "the entrance of a 24-hour convenience store, bright interior light spilling onto the sidewalk",
            "cafe_corner": "a real cafe corner by a window, warm pendant lights and coffee cups",
        },
        "ambience": "city night room tone, distant traffic and footsteps",
    },
    "real_cafe": {
        "look": "photorealistic cinematic live-action, real warm cafe interior in daytime, natural window light, realistic human faces and skin, 35mm film look, shallow depth of field",
        "realistic": True,
        "scenes": {
            "cafe_corner": "a real cafe corner by a window, wooden table, coffee cups and morning light",
            "cafe_counter": "a real cafe counter with an espresso machine and a barista in the background",
            "cafe_sofa": "a cozy sofa seat in a real cafe, warm lamps and plants",
            "cafe_window": "a rainy cafe window with reflections, people talking over coffee",
        },
        "ambience": "cafe room tone, low chatter, cups and espresso machine",
    },
    "real_kitchen": {
        "look": "photorealistic cinematic live-action, real home kitchen at night, warm tungsten light, steam and cooking smoke, realistic human faces, 35mm film look",
        "realistic": True,
        "scenes": {
            "kitchen_home": "a real home kitchen at night, warm lights, vegetables and a wok on the stove",
            "kitchen_table": "a real kitchen table with bowls of food, steam rising, warm lamp above",
            "kitchen_window": "a kitchen window at night with city lights outside, reflection of the room",
            "kitchen_stove": "close on a real gas stove with a wok and rising steam",
        },
        "ambience": "kitchen room tone, sizzling oil, a quiet night",
    },
}

CAM_PHRASE = {
    "push_in": "slow push-in toward the subject",
    "pull_out": "slow pull-back revealing the space",
    "pan_right": "smooth lateral pan",
    "pan_left": "smooth lateral pan",
    "dolly": "slow dolly move through the space",
    "crane_up": "slow crane rise",
    "crane_down": "slow crane descend",
    "rack_focus": "slow rack focus between foreground and background",
    "orbit": "slow orbiting camera move",
    "handheld": "subtle handheld camera drift",
    "whip_pan": "quick whip pan",
}

SHOW_ACTION = {
    "tonight_show": "the host leans on the desk and gestures while the guest laughs",
    "classics": "a scholar stands among the scrolls and speaks with quiet authority",
    "history_talk": "figures in dark ancient robes discuss beside a sand table",
    "masked_singer": "a masked performer stands under the lights as holograms turn",
    "roundtable": "seated guests talk around a candlelit round table",
    "national_treasure": "a curator gestures toward an illuminated display case",
    "hot_ones": "two friends talk on a sofa, warm lamplight between them",
    "mystery": "a detective paces beside a desk with case files",
    "qipashuo": "a debater at a glowing podium makes a point",
    "shark_tank": "a founder pitches in front of a blueprint wall",
    "slow_life": "a tiny clay figure tends a miniature garden",
    "snl_sketch": "performers act on a chalk-drawn stage",
    "standup": "a comedian speaks into a microphone on a small stage",
    "taskmaster": "contestants gesture at a table of props",
    "traitors": "guests sit around a candlelit stone table in a castle hall",
    "history_mystery": "an archivist studies an ancient map by candlelight",
}

COLOR_NAMES = [
    ("#", None),
]


def _color(px: str) -> str:
    """十六进制颜色 → 英文颜色词（粗粒度，用于 3D prompt）。"""
    try:
        h = str(px).lstrip("#")
        if len(h) != 6:
            return "dark"
        r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    except Exception:  # noqa: BLE001
        return "dark"
    mx, mn = max(r, g, b), min(r, g, b)
    if mx < 72:
        return "black"
    if mn > 205:
        return "white"
    if mx - mn < 20:
        return "grey"
    if b >= r and b >= g:
        if r > 110 and b > 130:
            return "purple"
        if b > 150 and g > 120 and r < 120:
            return "blue"
        return "navy"
    if r >= g and r >= b:
        if g > 120 and b < 110 and r > 150:
            return "gold"
        if g < 110:
            return "red-brown"
        return "amber"
    if g >= r and g >= b:
        if r > 150 and b > 120:
            return "cream"
        if b > 110 and r < 110:
            return "teal"
        return "green"
    if r > 140 and g > 110:
        return "beige"
    return "navy"


def character_desc(catalog: dict, cid: str, realistic: bool = False) -> str:
    ch = (catalog.get("characters") or {}).get(cid) or {}
    ap = ch.get("appearance") or {}
    gender = "man" if ap.get("gender") == "male" else ("woman" if ap.get("gender") == "female" else "person")
    age = int(ap.get("age") or 30)
    decade = "his" if gender == "man" else "her"
    hair = _color(ap.get("hair") or "#222")
    style = str(ap.get("hair_style") or "").replace("_", " ")
    outfit = ap.get("outfit") or {}
    otype = str(outfit.get("type") or "outfit").replace("_", " ")
    ocolor = _color(outfit.get("main") or "#333")
    accent = _color(outfit.get("accent") or "#888")
    hair_part = f"{hair} hair"
    if style and style not in ("none", "short swept"):
        hair_part = f"{style} {hair} hair"
    desc = f"a stylized 3D animated {gender} in {decade} {age0(age)}0s, {hair_part}, wearing a {ocolor} {otype}"
    if realistic:
        desc = f"a real {age}-year-old Chinese {gender}, {hair_part}, wearing a {ocolor} {otype}"
    if accent not in ("grey", "black", "white"):
        desc += f" with {accent} accents"
    acc = [str(a).replace("_", " ") for a in (ap.get("accessories") or []) if a]
    if acc:
        desc += f", with {' and '.join(acc[:2])}"
    return desc


def age0(age: int) -> int:
    return max(2, min(9, age // 10))


def load_catalog() -> dict:
    from . import v5_catalog
    return v5_catalog.load_catalog()


def load_episode(tid: str) -> dict:
    ep = V5_OUT / tid / "work" / "episode.json"
    if not ep.is_file():
        raise SystemExit(f"模板 {tid} 缺少 work/episode.json")
    return json.loads(ep.read_text(encoding="utf-8"))


def load_template(tid: str) -> dict:
    p = REPO / "contract/v5/templates" / f"{tid}.json"
    return json.loads(p.read_text(encoding="utf-8"))


def build_briefs(tid: str, catalog: dict, episode: dict, template: dict) -> dict:
    style = STYLE3D[tid]
    shots = episode["shots"]
    briefs = []
    for s in shots:
        sid = s["id"]
        if s.get("type") == "card":
            briefs.append({"id": sid, "kind": "card", "prompt": None})
            continue
        scene = s.get("scene") or ""
        scene_desc = style["scenes"].get(scene) or f"a cinematic 3D set inspired by {scene.replace('_', ' ')}"
        roles = [c for c in (s.get("characters") or []) if c in (episode["meta"].get("cast") or {})]
        chars = []
        for role in roles:
            cid = (episode["meta"]["cast"][role] or {}).get("character")
            if cid:
                chars.append(character_desc(catalog, cid))
        is_dialogue = bool(str(s.get("line") or "").strip())
        if is_dialogue:
            if "host" in roles:
                action = SHOW_ACTION[tid]
            elif roles:
                action = "the guest speaks and gestures, framed in a medium shot"
            else:
                action = "a voice-over moment: the camera drifts through the space, atmospheric"
        else:
            action = "the camera surveys the space, atmospheric and cinematic"
        cam = ""
        if isinstance(s.get("camera"), dict):
            cam = CAM_PHRASE.get(str(s["camera"].get("type") or ""), "")
        if not cam:
            cam = "slow cinematic camera move"
        char_part = ""
        if chars:
            uniq = []
            for c in chars:
                if c not in uniq:
                    uniq.append(c)
            char_part = " Featuring " + "; ".join(uniq) + ". " + action + "."
        desc = (f"[Shot 1] {style['look']}. Set in {scene_desc}.{char_part} "
                f"Camera: {cam}. The motion is natural and cinematic, with rich physical materials, "
                f"volumetric light and shallow depth of field. 3D animated feature film quality. "
                f"no text, no subtitles, no watermark, no logo, no extra people, "
                f"no distorted faces, no deformed hands, no plastic toy look.")
        briefs.append({
            "id": sid, "kind": "dialogue" if is_dialogue else "broll",
            "scene": scene, "duration": s.get("duration"),
            "roles": roles, "line": s.get("line") or "",
            "prompt": (f"integrated_multimodal_description: {desc}\n\n"
                       f"overall_soundscape: {style['ambience']}. No dialogue, no human voices, no music.\n\n"
                       f"non_diegetic_music: N/A"),
        })
    out = {"template": tid, "style": template.get("style"), "look": style["look"], "shots": briefs}
    BRIEFS_DIR.mkdir(parents=True, exist_ok=True)
    (BRIEFS_DIR / f"{tid}.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    return out


def _frame_length(duration: float) -> int:
    from .cinema3d_local import frame_length
    return frame_length(duration)


def generate_one(tid: str, brief: dict, resolution: tuple[int, int], force: bool) -> dict:
    from . import cinema3d_local as local
    sid = brief["id"]
    if brief.get("prompt") is None:
        return {"id": sid, "status": "card-skip"}
    out = OUT3D / tid / "shots" / f"{sid}.mp4"
    if not force and out.is_file() and out.stat().st_size > 100_000:
        return {"id": sid, "status": "cached"}
    dur = float(brief.get("duration") or 5.0)
    length = _frame_length(dur)
    seed = 20261101 + (abs(hash(tid + sid)) % 100000)
    t0 = time.time()
    try:
        wf = local.build_workflow(brief["prompt"], resolution[0], resolution[1], length, seed,
                                  f"templates3d/{tid}/{sid}")
        pid = local.submit(wf)
        res = local.wait_and_fetch(pid, out, timeout=2400)
        sec = round(time.time() - t0, 1)
        if "error" in res:
            return {"id": sid, "status": "failed", "seconds": sec, "error": res["error"]}
        meta = {"ok": True, "provider": "local-comfyui-h3", "mode": "t2va", "prompt_id": pid,
                "resolution": list(resolution), "length": length, "fps": 24,
                "prompt": brief["prompt"], "elapsed_sec": sec}
        (OUT3D / tid / "shots" / f"{sid}.meta.json").write_text(
            json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
        return {"id": sid, "status": "ok", "seconds": sec, "prompt_id": pid}
    except Exception as exc:  # noqa: BLE001
        return {"id": sid, "status": "failed", "seconds": round(time.time() - t0, 1),
                "error": f"{type(exc).__name__}: {exc}"[-400:]}


def read_progress() -> dict:
    if PROGRESS.is_file():
        try:
            return json.loads(PROGRESS.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            return {}
    return {}


def write_progress(data: dict) -> None:
    OUT3D.mkdir(parents=True, exist_ok=True)
    PROGRESS.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")


def generate(templates: list[str], resolution: tuple[int, int], force: bool = False,
             max_minutes: float = 0) -> int:
    catalog = load_catalog()
    progress = read_progress()
    t0 = time.time()
    for tid in templates:
        episode = load_episode(tid)
        template = load_template(tid)
        briefs = build_briefs(tid, catalog, episode, template)
        todo = [b for b in briefs["shots"] if b.get("prompt")]
        st = progress.setdefault(tid, {"status": "pending", "done": 0, "total": len(todo),
                                       "clips": {}, "updated": None})
        st["total"] = len(todo)
        st["status"] = "generating"
        print(f"=== {tid}: {len(todo)} 镜 ===", flush=True)
        for i, brief in enumerate(todo, 1):
            if max_minutes and (time.time() - t0) / 60 > max_minutes:
                print("达到本次时间预算，停止", flush=True)
                write_progress(progress)
                return 0
            res = generate_one(tid, brief, resolution, force)
            st["clips"][brief["id"]] = res
            done = sum(1 for v in st["clips"].values() if v.get("status") in ("ok", "cached", "card-skip"))
            st["done"] = done
            st["updated"] = time.strftime("%Y-%m-%dT%H:%M:%S")
            write_progress(progress)
            print(f"[{tid} {i}/{len(todo)}] {res['id']} {res['status']} {res.get('seconds', 0)}s "
                  f"({done}/{len(todo)})", flush=True)
        st["status"] = "generated"
        progress[tid] = st
        write_progress(progress)
        print(f"=== {tid} 完成 {st['done']}/{st['total']} ===", flush=True)
    return 0


def assemble(tid: str) -> dict:
    from . import render_v5, v5_catalog
    catalog = v5_catalog.load_catalog()
    episode = load_episode(tid)
    template = load_template(tid)
    style = catalog["styles"][template["style"]]
    out_dir = OUT3D / tid
    workdir = out_dir / "work"
    shots_dir = out_dir / "shots"
    for d in (out_dir, workdir, shots_dir, out_dir / "frames", workdir / "audio"):
        d.mkdir(parents=True, exist_ok=True)
    # 1) 卡片镜复用旧 V5 卡片 clip
    old_shots = V5_OUT / tid / "shots"
    old_audio = V5_OUT / tid / "work" / "audio"
    for s in episode["shots"]:
        sid = s["id"]
        if s.get("type") == "card":
            src = old_shots / f"{sid}.mp4"
            dst = shots_dir / f"{sid}.mp4"
            if src.is_file() and not dst.is_file():
                dst.write_bytes(src.read_bytes())
        aw = old_audio / f"{sid}.wav"
        if aw.is_file():
            dst_audio = workdir / "audio" / f"{sid}.wav"
            if not dst_audio.is_file():
                dst_audio.write_bytes(aw.read_bytes())
    paths = {"out": out_dir / "final.mp4", "out_dir": out_dir, "workdir": workdir,
             "shots_dir": shots_dir, "frames_dir": out_dir / "frames",
             "assets_dir": workdir / "assets"}
    plan = json.loads(json.dumps(episode))

    def _noop(progress: float, message: str = "") -> None:  # final_encode 会无条件调用 prog
        return None

    res = render_v5.assemble(plan, catalog, style, paths, _noop)
    probe = _probe(out_dir / "final.mp4")
    report = {"template": tid, "final": str(out_dir / "final.mp4"), "probe": probe,
              "assemble": {k: v for k, v in res.items() if k != "timeline"}}
    (out_dir / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    return report


def _probe(p: Path) -> dict:
    import subprocess
    try:
        raw = subprocess.run([str(FFPROBE), "-v", "error", "-show_entries",
                              "format=duration,size", "-show_entries",
                              "stream=codec_name,codec_type,width,height,r_frame_rate",
                              "-of", "json", str(p)], capture_output=True, text=True, timeout=60).stdout
        return json.loads(raw or "{}")
    except Exception:  # noqa: BLE001
        return {}


def assemble_completed(templates: list[str], force: bool = False) -> int:
    progress = read_progress()
    for tid in templates:
        st = progress.get(tid) or {}
        if st.get("status") not in ("generated", "generating"):
            continue
        if st.get("done", 0) < st.get("total", 1):
            continue
        final = OUT3D / tid / "final.mp4"
        if final.is_file() and not force:
            continue
        print(f"--- 剪辑 {tid} ---", flush=True)
        try:
            rep = assemble(tid)
            st = progress.setdefault(tid, {})
            st["status"] = "done"
            st["final"] = str(final)
            st["probe"] = rep.get("probe")
            st["assembled_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
            progress[tid] = st
            write_progress(progress)
            print(f"--- {tid} 剪辑完成 {rep.get('probe', {}).get('format', {})} ---", flush=True)
        except Exception as exc:  # noqa: BLE001
            st["status"] = "assemble_failed"
            st["error"] = f"{type(exc).__name__}: {exc}"[-500:]
            progress[tid] = st
            write_progress(progress)
            print(f"--- {tid} 剪辑失败: {exc} ---", flush=True)
    return 0


def watch(templates: list[str], interval: float = 30.0) -> int:
    print("assemble-watch 启动", flush=True)
    while True:
        assemble_completed(templates)
        progress = read_progress()
        if all(progress.get(t, {}).get("status") == "done" for t in templates):
            print("全部模板已生成并剪辑完成", flush=True)
            return 0
        time.sleep(interval)


def status() -> int:
    progress = read_progress()
    total_clips = total_done = 0
    for tid in TEMPLATES:
        st = progress.get(tid) or {}
        done, total = st.get("done", 0), st.get("total", 0)
        total_clips += total
        total_done += done
        print(f"{tid:20} {st.get('status', 'pending'):16} {done:3}/{total:<3} "
              f"final={'yes' if (OUT3D / tid / 'final.mp4').is_file() else 'no'}")
    print(f"TOTAL {total_done}/{total_clips} clips")
    return 0


def main(argv=None) -> int:
    try:
        import sys
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass
    ap = argparse.ArgumentParser(description="V8 16 模板 3D 化")
    ap.add_argument("--templates", default="all")
    ap.add_argument("--generate", action="store_true")
    ap.add_argument("--assemble", action="store_true")
    ap.add_argument("--assemble-watch", action="store_true")
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--resolution", default="768x432")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--max-minutes", type=float, default=0)
    a = ap.parse_args(argv)
    if a.templates == "all":
        tids = list(TEMPLATES)
    else:
        tids = [x.strip() for x in a.templates.split(",") if x.strip()]
    if a.status:
        return status()
    w, h = (int(x) for x in a.resolution.lower().split("x"))
    if a.generate:
        return generate(tids, (w, h), force=a.force, max_minutes=a.max_minutes)
    if a.assemble:
        assemble_completed(tids, force=a.force)
        return 0
    if a.assemble_watch:
        return watch(tids)
    return status()


if __name__ == "__main__":
    raise SystemExit(main())
