# -*- coding: utf-8 -*-
"""V5.2 场景 AI 美术管线：SDXL Animagine 动漫场景 plate（无人物背景美术）。

契约（见 docs/PLAN_V5_2_ART_FRONTEND.md §3）::

    generate_scene_plate(scene_id, out_png, *, prompt=None, negative=None,
                         seed=42, size=(1280, 720), force=False) -> dict

产物：
    assets/v5/scenes/<scene_id>.png     1280x720（或指定尺寸）anime 场景 plate
    assets/v5/scenes/<scene_id>.json    prompt/negative/seed/size/prompt_id/gen_sec/model...

说明：
- 走 ComfyUI 8188 的 SDXL txt2img（imagegen.workflow.build_workflow），与最早 3 个角色同一条
  模型/采样管线（Animagine XL 4.0）。**不重启/不改 ComfyUI**，共享队列自动串行。
- prompt 优先级：显式参数 > contract/v5/art/prompts.json 的 scenes[<id>]（若已就绪）
  > contract/v5/scenes/<id>.json 的 art 字段 > 本模块内置 FALLBACK_PROMPTS（按 id）
  > 由 label/tags 自动拼。
- 历史场景 prompt 内置历史内容（地图路线/军旗/沙盘/宫殿/藏书楼/博物馆/考古/关隘/船队），
  并在 positive 里写 ``no humans``、negative 压人像，保证 plate 是纯背景美术。
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from PIL import Image

from . import utils

REPO_ROOT = Path(__file__).resolve().parent.parent
SCENES_DIR = REPO_ROOT / "contract" / "v5" / "scenes"
ART_PROMPTS_PATH = REPO_ROOT / "contract" / "v5" / "art" / "prompts.json"
ASSETS_DIR = REPO_ROOT / "assets" / "v5" / "scenes"

DEFAULT_SERVER = "http://127.0.0.1:8188"
DEFAULT_SEED = 20261101
DEFAULT_SIZE = (1280, 720)          # 16:9，8 的倍数；和成片比例一致
DEFAULT_MODEL = "animagine-xl-4.0.safetensors"
DEFAULT_STEPS = 28
DEFAULT_CFG = 5.5
DEFAULT_SAMPLER = "euler_ancestral"
DEFAULT_SCHEDULER = "normal"
DEFAULT_TIMEOUT = 900.0             # 共享队列（render-dev 并发角色图）留足等待时间
PLATE_MIN_BYTES = 20_000

# 正向基础/收尾 tag（风格无关；运行时由 style_v5.apply_finish 做 16 风格差异化）
PROMPT_PREFIX = (
    "masterpiece, best quality, very aesthetic, absurdres, anime style, "
    "detailed background art, cinematic lighting, scenery, no humans"
)
PROMPT_SUFFIX = "wide shot, depth of field, 16:9, high detail, no text"

# 默认 negative：压人像/文字/写实，避免 plate 里出现角色（角色由 characters_v5 叠加）
NEGATIVE_BASE = (
    "lowres, worst quality, low quality, bad anatomy, bad hands, text, watermark, "
    "signature, logo, jpeg artifacts, human, person, character, face, portrait, "
    "close-up, 3d render, photo, realistic, blurry"
)

# ---------------------------------------------------------------------------
# 内置 fallback prompt（60 场景；template-author 的 contract/v5/art/prompts.json
# 就绪后会被其 scenes 段覆盖，这里保证在任何时候都能出图，且历史场景内容正确）
# ---------------------------------------------------------------------------
FALLBACK_PROMPTS: dict[str, str] = {
    # ---- 历史包（必须有历史内容：地图/战场/军帐/宫殿/藏书楼/博物馆/考古/关隘/船队）----
    "ancient_map": (
        "top-down view of an ancient chinese silk map, painted ink mountains and "
        "winding rivers, red marching route with arrows, walled city markers, "
        "compass rose, aged torn silk edges, warm lamplight"
    ),
    "battlefield": (
        "vast ancient chinese battlefield at dusk, distant misty mountains, torn "
        "war banners on spears, drifting smoke and dust, broken war chariots, "
        "crows in the sky, epic wide cinematic shot"
    ),
    "war_council": (
        "interior of an ancient chinese military war tent, wooden table with a "
        "miniature terrain sandbox, unrolled battle map, torches and candlelight, "
        "armor stand, fur rugs, dramatic shadows"
    ),
    "palace_hall": (
        "grand ancient chinese palace throne hall, red lacquered columns with "
        "golden dragon carvings, ornate throne on elevated dais, hanging silk "
        "banners and palace lanterns, light shafts, symmetrical composition"
    ),
    "archive_vault": (
        "ancient chinese archive library, towering wooden shelves filled with "
        "rolled scrolls and bamboo slips, wooden ladder, reading desk with "
        "candle, dust motes in sunbeams"
    ),
    "lantern_market": (
        "ancient chinese night market street, hundreds of glowing red and gold "
        "paper lanterns, wooden stalls with silk goods, stone paved road, warm "
        "festive atmosphere"
    ),
    "river_fleet": (
        "ancient chinese river fleet at misty dawn, wooden warships with tall "
        "sails and dragon head prows, wooden pier, distant mountains and fog, "
        "calm water reflections"
    ),
    "great_wall": (
        "ancient chinese great wall fortress on a mountain pass, stone "
        "battlements and watchtowers, winding wall over misty ridges, banners "
        "on towers, epic wide landscape"
    ),
    "temple_court": (
        "ancient chinese temple courtyard, bronze incense burner with rising "
        "smoke, red pillars, hanging prayer banners, stone floor, ginkgo "
        "leaves, soft golden light"
    ),
    "scholar_study": (
        "ancient chinese scholar study, wooden desk with bamboo slips, ink "
        "brush and inkstone, burning candle, bookshelves full of scrolls, "
        "lattice window with garden view, warm afternoon light"
    ),
    "excavation_site": (
        "archaeological excavation site in a desert, deep rectangular dig pit "
        "with grid ropes, brushes and tools, ancient stone ruins and broken "
        "pottery, tents in the background, sunset light"
    ),
    "museum_hall": (
        "grand museum hall, marble floor, illuminated glass display cases with "
        "ancient chinese bronze vessels and jade artifacts, spotlight beams, "
        "tall columns, elegant dark walls"
    ),
    "display_case": (
        "close-up of a museum glass display case, ancient bronze ritual vessel "
        "and jade artifacts on a stone pedestal, museum spotlight, reflections, "
        "dark background"
    ),
    # ---- 现代/霓虹/综艺包 ----
    "late_night_studio": (
        "late night talk show studio, dark set with neon accents, host desk with "
        "coffee mug and cue cards, city lights backdrop, warm spotlights"
    ),
    "neon_skyline": (
        "cyberpunk city skyline at night, neon signs in pink and cyan, rain-slick "
        "rooftops, glowing windows, futuristic skyscrapers, anime background art"
    ),
    "backstage_neon": (
        "backstage corridor of a neon tv studio, cables and equipment cases, "
        "neon strip lights, dark industrial walls, moody atmosphere"
    ),
    "comedy_club_vhs": (
        "small comedy club stage at night, brick wall, single vintage microphone "
        "stand, wooden stool, warm stage light, retro 80s vhs look"
    ),
    "brick_wall": (
        "empty stage corner with exposed brick wall, neon sign glow, worn wooden "
        "floor, single spotlight"
    ),
    "interview_sofa": (
        "warm interview studio set, cream fabric sofa, coffee table with cups, "
        "soft practical lamps, film-like ambience, shallow depth of field"
    ),
    "kitchen_film": (
        "cozy home kitchen, wooden cabinets, warm morning light through window, "
        "kettle and ceramic bowls on counter, small plants"
    ),
    "cafe_window": (
        "cozy cafe interior by a rainy window, wooden tables, hanging lamps, "
        "coffee cup on table, bokeh city outside, warm tones"
    ),
    "noir_office": (
        "film noir private detective office at night, venetian blind shadows, "
        "wooden desk with lamp and whiskey glass, rain outside window, cold blue "
        "monochrome"
    ),
    "rainy_street": (
        "rainy noir city street at night, wet asphalt reflections, neon signs, "
        "streetlights, fog, fire escape stairs"
    ),
    "interrogation_room": (
        "interrogation room, single hanging lamp over a metal table, two chairs, "
        "concrete walls, harsh shadows, cold blue tone"
    ),
    "police_archive": (
        "police archive room, tall shelves of file boxes, wooden desks, green "
        "banker lamps, scattered papers, dim light"
    ),
    "pitch_boardroom": (
        "modern boardroom for startup pitch, long conference table, glass wall "
        "with city view, presentation screen, blueprints on table, blue accent "
        "lighting"
    ),
    "blueprint_wall": (
        "large blueprint wall with technical drawings, grid lines, pinned "
        "schematics, desk with rolled blueprints and lamp, blue monochrome"
    ),
    "whiteboard_lab": (
        "whiteboard covered with diagrams and sticky notes in a bright lab, desk "
        "with monitors, prototype parts, clean tech aesthetic"
    ),
    "prototype_lab": (
        "technology prototype workshop, workbench with robotic components and "
        "tools, 3d printer, blue led strip lighting"
    ),
    "hologram_stage": (
        "futuristic hologram concert stage, glowing wireframe grids, laser beams, "
        "floating particle effects, purple and cyan lighting"
    ),
    "mirror_room": (
        "backstage mirror room with large costume mirrors and bulb lights, makeup "
        "tables, reflective surfaces, glamorous lighting"
    ),
    "audience_star_sea": (
        "dark concert arena seen from the stage, sea of glowing penlights in the "
        "audience, haze and light beams"
    ),
    "light_tunnel": (
        "futuristic light tunnel with glowing neon strips, symmetrical corridor, "
        "motion blur, white and blue glow"
    ),
    "city_vhs": (
        "80s city street at night with vhs artifacts, neon storefronts, parked "
        "cars, street lamps, retro vibe"
    ),
    "dressing_room_vhs": (
        "vintage dressing room with mirrors and costumes, tv screen showing "
        "static, warm 80s lighting"
    ),
    "film_strip": (
        "empty film studio backdrop with lighting rigs, rolling film reels, "
        "clapperboard on crate, warm tungsten light"
    ),
    "game_stage_collage": (
        "bright colorful game show stage made of paper cutouts, colorful panels "
        "and stickers, confetti, spotlights, playful set design"
    ),
    "prop_table": (
        "game show prop table with colorful cut paper labels, trophy, buttons, "
        "cards, confetti, bright candy colors"
    ),
    "leaderboard_collage": (
        "colorful paper collage leaderboard with ranked rows and score panels, "
        "stickers and stars, bright game show set"
    ),
    "confetti_stage": (
        "festive stage covered in confetti rain, colorful spotlights, paper "
        "cutout decorations, celebratory atmosphere"
    ),
    "clay_kitchen": (
        "handmade clay diorama kitchen, miniature clay furniture and pots, pastel "
        "terra cotta colors, tilt-shift macro photography"
    ),
    "clay_garden": (
        "miniature clay garden diorama, clay trees and flowers, tiny fence and "
        "watering can, soft cream sky, tilt-shift macro"
    ),
    "clay_street": (
        "miniature clay street diorama, tiny clay houses and lamp post, "
        "cobblestone road, warm sunset light, tilt-shift macro"
    ),
    "clay_diorama": (
        "round clay diorama base with tiny scene, miniature houses trees and "
        "props, showcase lighting, tilt-shift macro"
    ),
    "chalk_stage": (
        "blackboard chalk drawing of a theatre stage, white chalk proscenium and "
        "curtains, red chalk accents, chalk dust, rough hand drawn"
    ),
    "sketch_backdrop": (
        "chalk sketch on dark green blackboard of a city street backdrop, white "
        "chalk lines and hatching, rough hand drawn"
    ),
    "costume_room_chalk": (
        "chalk drawing of a backstage costume room on blackboard, mirror, clothes "
        "rack, trunk, white chalk lines, hand drawn"
    ),
    "audience_sketch": (
        "chalk drawing of theatre audience rows on a blackboard, white outline "
        "silhouettes and applause marks, rough hand drawn"
    ),
    # ---- 绢本 / 油画 / 巴洛克（现代 B 场景）----
    "silk_debate_hall": (
        "traditional chinese silk gongbi painting debate hall, pale silk scroll "
        "background with fine ink mountains, two red vertical banners, ink brush "
        "circle stage, faint audience silhouettes, mineral pigments"
    ),
    "scroll_split": (
        "two ancient chinese silk scroll panels side by side, pale silk texture, "
        "fine ink mountains, split composition, traditional gongbi painting"
    ),
    "court_audience": (
        "ancient chinese court audience hall, rows of faint seated silhouettes, "
        "hanging lanterns, columns and painted screens, silk painting atmosphere"
    ),
    "ink_arena": (
        "circular ink-wash arena stage on pale silk, ink splashes and dry brush "
        "strokes, red seal stamp, minimal chinese ink painting"
    ),
    "oil_roundtable": (
        "renaissance oil painting of a round table salon, dark umber background, "
        "oval wooden table, candelabra, red velvet drapery, gilded frame, "
        "chiaroscuro lighting"
    ),
    "oil_library": (
        "renaissance oil painting library, tall dark wood bookshelves, arched "
        "windows, reading desk with candle, warm gold light, oil canvas texture"
    ),
    "candle_gallery": (
        "baroque candlelit gallery corridor, arched stone columns, red velvet "
        "curtains, candelabra glow, oil painting atmosphere"
    ),
    "castle_hall_baroque": (
        "grand baroque gothic castle hall, stone pointed arches, crimson velvet "
        "drapery, iron chandelier with candles, house banners, candlelight haze"
    ),
    "candle_corridor": (
        "candlelit gothic castle corridor, pointed stone arches, wall sconces, "
        "drifting smoke, dramatic shadows"
    ),
    "stone_roundtable": (
        "baroque stone round table in a castle chamber, candelabra, high backed "
        "chairs, vaulted ceiling, warm candle glow"
    ),
    "crypt": (
        "gothic crypt under a castle, ribbed stone vaults, stone sarcophagi, "
        "candles and cobwebs, cold blue grey with warm candle light"
    ),
    # ---- 现代日常包（V45 补齐：这 9 个场景此前没有 plate，场景库会 404 后回落到程序化预览）----
    "cafe_corner": (
        "cozy cafe corner by a tall window, wooden table and two chairs, warm "
        "pendant lamp, rain streaks on the glass, blurred city lights outside, "
        "potted plants, anime background art"
    ),
    "cafe_counter": (
        "cafe service counter with espresso machine and pastry case, wooden "
        "shelves of cups, chalkboard menu, warm hanging lights, no people, "
        "anime background art"
    ),
    "cafe_sofa": (
        "cafe lounge with soft fabric sofa and cushions, low coffee table, "
        "warm floor lamp, large window with evening city view, anime background art"
    ),
    "convenience_store": (
        "24-hour convenience store interior at night, bright shelves of snacks "
        "and drinks, glass door with street glow, fluorescent ceiling light, "
        "no people, anime background art"
    ),
    "kitchen_home": (
        "warm home kitchen, wooden cabinets, window with daylight, kettle and "
        "cutting board on the counter, cosy domestic atmosphere, anime background art"
    ),
    "kitchen_stove": (
        "home kitchen stove corner, gas stove with a steaming pot, range hood, "
        "tiled backsplash, warm task light, anime background art"
    ),
    "kitchen_table": (
        "home kitchen dining table with wooden chairs, breakfast set on the "
        "table, morning sunlight through the window, anime background art"
    ),
    "street_night": (
        "city street at night, wet asphalt with neon reflections, street lamps "
        "and storefront signs, glowing vending machine, no people, anime background art"
    ),
    "subway_exit": (
        "subway station exit at night, stairs leading up to the street, tiled "
        "walls, fluorescent light, city glow at the top of the stairs, anime background art"
    ),
    # ---- V46 赛博 / Arcane 风场景 ----
    "undercity_street": (
        "cyberpunk undercity street at night, wet asphalt reflections, dense neon "
        "signs, steam rising from vents, pipes and cables overhead, distant towers, "
        "no people, painted animation background, teal and amber light"
    ),
    "arcane_workshop": (
        "inventor workshop interior, wooden shelves and metal parts, workbench with "
        "tools and blueprints, warm desk lamp, brass gears, cool window light, "
        "no people, painted animation background"
    ),
    "council_hall": (
        "grand council hall interior, tall stone columns and stained glass windows, "
        "long table with an emblem, spotlights falling from above, dramatic "
        "atmosphere, no people, painted animation background"
    ),
}

# 历史场景（pilot/验收重点：必须有历史内容）
HISTORY_SCENES = [
    "ancient_map", "battlefield", "war_council", "palace_hall", "archive_vault",
    "lantern_market", "river_fleet", "great_wall", "temple_court", "scholar_study",
    "excavation_site", "museum_hall", "display_case",
]
PILOT_SCENES = ["ancient_map", "battlefield", "war_council", "palace_hall",
                "excavation_site", "museum_hall"]

# tag 关键词 → 补充 tag（prompt 自动拼兜底用）
TAG_HINTS = {
    "history": "ancient chinese historical setting, historical architecture",
    "ink": "ink painting atmosphere, muted colors",
    "parchment": "aged parchment texture",
    "silk": "silk texture, fine brushwork",
    "oil": "oil painting texture, chiaroscuro",
    "baroque": "baroque architecture, candlelight",
    "neon": "neon lights, cyberpunk",
    "vhs": "vhs aesthetic, retro 80s",
    "film": "cinematic film still, shallow depth of field",
    "noir": "film noir, high contrast shadows",
    "hologram": "holographic glow, futuristic",
    "blueprint": "blueprint grid, technical drawing",
    "collage": "paper cutout collage",
    "clay": "clay miniature, tilt-shift",
    "chalk": "chalk drawing on blackboard",
    "museum": "museum gallery, spotlights",
    "castle": "castle interior, stone arches",
    "office": "office interior",
    "street": "city street",
    "studio": "tv studio set",
    "stage": "performance stage",
    "garden": "lush garden",
    "kitchen": "warm kitchen interior",
}


class V5SceneArtError(RuntimeError):
    """场景 plate 生成失败（ComfyUI 不可用/无 checkpoint/产物异常）。"""


# ---------------------------------------------------------------------------
# 场景 / prompt 解析
# ---------------------------------------------------------------------------
def scenes_dir(root: str | Path | None = None) -> Path:
    return Path(root) if root else SCENES_DIR


def list_scene_ids(root: str | Path | None = None) -> list[str]:
    base = scenes_dir(root)
    if not base.is_dir():
        return []
    return sorted(p.stem for p in base.glob("*.json"))


def load_scene(scene_id: str, *, root: str | Path | None = None) -> dict:
    path = scenes_dir(root) / f"{scene_id}.json"
    if not path.is_file():
        raise V5SceneArtError(f"场景不存在: {path}")
    data = utils.read_json(path) or {}
    if not isinstance(data, dict):
        raise V5SceneArtError(f"场景 JSON 非法: {path}")
    return data


def load_art_prompts(path: str | Path | None = None) -> dict:
    """读 contract/v5/art/prompts.json（template-author 维护）；缺失返回 {}。

    兼容结构：
      {"scenes": {"<id>": {"prompt","negative"}}} / {"scenes": [{"id","prompt"}]}
      / 顶层直接 {"<id>": {...}}
    """
    p = Path(path) if path else ART_PROMPTS_PATH
    if not p.is_file():
        return {}
    data = utils.read_json(p) or {}
    if not isinstance(data, dict):
        return {}
    scenes = data.get("scenes")
    out: dict[str, dict] = {}
    if isinstance(scenes, dict):
        for sid, value in scenes.items():
            out[str(sid)] = _normalize_prompt_entry(value)
    elif isinstance(scenes, list):
        for item in scenes:
            if isinstance(item, dict) and item.get("id"):
                out[str(item["id"])] = _normalize_prompt_entry(item)
    else:
        for sid, value in data.items():
            if isinstance(value, (str, dict)) and sid not in ("characters", "meta", "styles"):
                out[str(sid)] = _normalize_prompt_entry(value)
    return {k: v for k, v in out.items() if v}


def _normalize_prompt_entry(value: Any) -> dict:
    if isinstance(value, str):
        return {"prompt": value.strip()}
    if isinstance(value, dict):
        prompt = str(value.get("prompt") or value.get("positive") or "").strip()
        negative = str(value.get("negative") or "").strip()
        entry = {"prompt": prompt}
        if negative:
            entry["negative"] = negative
        for key in ("seed", "steps", "cfg", "sampler", "scheduler"):
            if value.get(key) is not None:
                entry[key] = value[key]
        return entry if prompt or negative else {}
    return {}


def auto_prompt_from_scene(scene: dict) -> str:
    """无任何 prompt 来源时，用 label/tags/id 拼一个兜底 prompt。"""
    scene_id = str(scene.get("id") or "")
    tags = [str(t) for t in (scene.get("tags") or [])]
    hints = [TAG_HINTS[t] for t in tags if t in TAG_HINTS]
    words = scene_id.replace("_", " ")
    base = f"anime background art of {words}"
    if hints:
        base += ", " + ", ".join(dict.fromkeys(hints))
    label = str(scene.get("label") or "")
    if label:
        base += f", {label}"
    return base


def _looks_complete_prompt(text: str) -> bool:
    """判断 prompt 是否已是完整成品 prompt（自带质量前缀/后缀，如 prompts.json）。"""
    low = str(text or "").lower()
    if not low:
        return False
    if "masterpiece" in low and ("no humans" in low or "background plate" in low):
        return True
    return low.startswith("masterpiece") or ("anime style" in low and "no humans" in low)


def build_scene_prompt(scene_id: str, *, scene: dict | None = None,
                       prompt: str | None = None, negative: str | None = None,
                       prompts: dict | None = None,
                       root: str | Path | None = None) -> dict:
    """返回 {"positive","negative","source","raw_prompt"}。

    prompt 优先级：显式参数 > contract/v5/art/prompts.json > scene.art > 内置 fallback。
    prompts.json 的 prompt 视为**完整 prompt**（不重复加前缀/后缀）；fallback/自动拼才包前缀。
    """
    if not isinstance(scene, dict):
        try:
            scene = load_scene(scene_id, root=root)
        except V5SceneArtError:
            scene = {}
    prompts = prompts if prompts is not None else load_art_prompts()
    entry = dict(prompts.get(scene_id) or {})
    raw = str(prompt or entry.get("prompt") or "").strip()
    source = "explicit" if prompt else ("prompts.json" if raw else "")
    neg_extra = str(negative or entry.get("negative") or "").strip()
    if not raw:
        art = scene.get("art") if isinstance(scene.get("art"), dict) else {}
        raw = str(art.get("prompt") or "").strip()
        if raw:
            source = "scene.art"
    if not raw:
        raw = FALLBACK_PROMPTS.get(scene_id) or auto_prompt_from_scene(scene)
        source = "fallback" if scene_id in FALLBACK_PROMPTS else "auto"
    if not raw:
        raise V5SceneArtError(
            f"{scene_id}: 场景 JSON 与 prompts.json 都没有，且无法自动拼 prompt")
    if _looks_complete_prompt(raw):
        positive = raw
        if "16:9" not in positive.lower():
            positive = f"{positive}, 16:9"
        negative = neg_extra or NEGATIVE_BASE
    else:
        positive = f"{PROMPT_PREFIX}, {raw}, {PROMPT_SUFFIX}"
        negative = ", ".join(p for p in (NEGATIVE_BASE, neg_extra) if p)
    return {
        "positive": positive,
        "negative": negative,
        "source": source,
        "raw_prompt": raw,
        "scene": scene,
        "entry": entry,
    }


def plate_paths(scene_id: str, out_png: str | Path | None = None,
                out_dir: str | Path | None = None) -> tuple[Path, Path]:
    """返回 (png, meta.json)。"""
    if out_png:
        png = Path(out_png)
    else:
        png = Path(out_dir or ASSETS_DIR) / f"{scene_id}.png"
    return png, png.with_suffix(".json")


# ---------------------------------------------------------------------------
# ComfyUI 调用
# ---------------------------------------------------------------------------
def _comfy():
    """延迟 import imagegen（避免模块导入时强依赖）。"""
    from imagegen import comfy, workflow  # noqa: WPS433
    return comfy, workflow


def _check_server(server: str, log=print) -> dict:
    comfy, _ = _comfy()
    try:
        stats = comfy.http_json(server.rstrip("/") + "/system_stats", timeout=5)
    except Exception as exc:  # noqa: BLE001
        raise V5SceneArtError(
            f"ComfyUI 不可用（{server}）：{exc}；请确认 8188 已启动（勿重启/勿抢改）") from exc
    return stats


def _model_info(server: str, ckpt: str, log=print) -> dict:
    comfy, _ = _comfy()
    object_info = comfy.fetch_object_info(server)
    ckpts = comfy.get_checkpoints(server, object_info)
    if ckpt not in ckpts:
        raise V5SceneArtError(
            f"checkpoint 不在 ComfyUI 列表: {ckpt}（现有: {ckpts[:8]}）")
    return {"object_info": object_info, "checkpoints": ckpts}


def _valid_plate(path: Path) -> bool:
    try:
        if not path.is_file() or path.stat().st_size < PLATE_MIN_BYTES:
            return False
        with Image.open(path) as im:
            im.verify()
        return True
    except Exception:  # noqa: BLE001
        return False


def _meta_matches(meta: dict, *, prompt: str, negative: str, seed: int,
                  size: tuple[int, int], model: str) -> bool:
    if not isinstance(meta, dict):
        return False
    try:
        return (str(meta.get("prompt")) == prompt
                and str(meta.get("negative")) == negative
                and int(meta.get("seed")) == int(seed)
                and [int(v) for v in (meta.get("size") or [])] == [int(size[0]), int(size[1])]
                and str(meta.get("model")) == model)
    except (TypeError, ValueError):
        return False


def generate_scene_plate(scene_id: str, out_png: str | Path | None = None, *,
                         prompt: str | None = None,
                         negative: str | None = None,
                         seed: int = DEFAULT_SEED,
                         size: tuple[int, int] = DEFAULT_SIZE,
                         force: bool = False,
                         out_dir: str | Path | None = None,
                         server: str = DEFAULT_SERVER,
                         model: str = DEFAULT_MODEL,
                         steps: int = DEFAULT_STEPS,
                         cfg: float = DEFAULT_CFG,
                         sampler: str = DEFAULT_SAMPLER,
                         scheduler: str = DEFAULT_SCHEDULER,
                         timeout: float = DEFAULT_TIMEOUT,
                         scene: dict | None = None,
                         prompts: dict | None = None,
                         log=print) -> dict:
    """生成单个场景 plate（SDXL txt2img，无人物），写 PNG + 同名 .json 证据。

    - 已有 PNG 且 meta 的 prompt/seed/size/model 与本次一致时直接复用（force=True 重生成）；
    - 颜色/尺寸：默认 1280x720（8 的倍数），也可传 (1024,1024)；
    - 返回 dict（含 prompt/negative/seed/size/prompt_id/gen_sec/path/meta/reused）。
    """
    scene_id = str(scene_id or "").strip()
    if not scene_id:
        raise V5SceneArtError("scene_id 不能为空")
    w, h = int(size[0]), int(size[1])
    if w % 8 or h % 8:
        raise V5SceneArtError(f"尺寸必须为 8 的倍数: {w}x{h}")
    if w < 256 or h < 256:
        raise V5SceneArtError(f"尺寸过小: {w}x{h}")
    seed = int(seed)
    png, meta_path = plate_paths(scene_id, out_png, out_dir)
    built = build_scene_prompt(scene_id, scene=scene, prompt=prompt,
                               negative=negative, prompts=prompts)
    positive = built["positive"]
    neg = built["negative"]
    raw_prompt = built["raw_prompt"]
    entry = built["entry"]
    steps = int(entry.get("steps") or steps)
    cfg = float(entry.get("cfg") or cfg)
    sampler = str(entry.get("sampler") or sampler)
    scheduler = str(entry.get("scheduler") or scheduler)
    scene_seed = entry.get("seed")
    if prompt is None and scene_seed is not None:
        try:
            seed = int(scene_seed)
        except (TypeError, ValueError):
            pass

    existing = utils.read_json(meta_path) or {}
    if not force and _valid_plate(png) and _meta_matches(
            existing, prompt=positive, negative=neg, seed=seed,
            size=(w, h), model=model):
        log(f"[skip] {scene_id} 已存在且参数一致: {png.name}")
        return {**existing, "reused": True, "path": str(png),
                "meta": str(meta_path), "scene": scene_id}

    _check_server(server, log)
    _model_info(server, model, log)
    comfy, workflow = _comfy()
    wf = workflow.build_workflow(
        positive=positive, negative=neg, width=w, height=h, seed=seed,
        filename_prefix=f"zaopianju_v52_scenes/{scene_id}",
        ckpt_name=model, steps=steps, cfg=cfg,
        sampler_name=sampler, scheduler=scheduler)
    for warn in workflow.validate_workflow(wf, comfy.fetch_object_info(server)):
        log(f"[warn] {scene_id}: {warn}")

    log(f"[gen] {scene_id} seed={seed} {w}x{h} steps={steps} cfg={cfg} "
        f"source={built['source']}")
    gen_t0 = time.time()
    pid = comfy.submit(server, wf, log)
    rec = comfy.wait_prompt(server, pid, timeout, log)
    imgs = comfy.fetch_images(rec)
    if not imgs:
        raise V5SceneArtError(
            f"{scene_id}: ComfyUI 没有返回图片: "
            f"{json.dumps(rec.get('outputs'), ensure_ascii=False)[:600]}")
    imgs.sort(key=lambda it: it["filename"])
    utils.ensure_dir(png.parent)
    tmp_png = png.with_name(png.stem + ".tmp" + png.suffix)
    comfy.save_image(server, comfy.DEFAULT_COMFY_ROOT, imgs[0], str(tmp_png), log)
    tmp_png.replace(png)
    gen_sec = round(time.time() - gen_t0, 1)
    with Image.open(png) as im:
        iw, ih = im.size
    if (iw, ih) != (w, h):
        log(f"[warn] {scene_id}: 产物 {iw}x{ih} != 请求 {w}x{h}")
    meta = {
        "scene": scene_id,
        "label": str((built.get("scene") or {}).get("label") or ""),
        "path": str(png),
        "png": png.name,
        "prompt": positive,
        "raw_prompt": raw_prompt,
        "prompt_source": built["source"],
        "negative": neg,
        "seed": seed,
        "size": [iw, ih],
        "requested_size": [w, h],
        "model": model,
        "steps": steps,
        "cfg": cfg,
        "sampler": sampler,
        "scheduler": scheduler,
        "prompt_id": pid,
        "gen_sec": gen_sec,
        "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    utils.write_json(meta_path, meta)  # 原子写：.tmp -> replace
    log(f"[ok] {scene_id} {gen_sec:.1f}s -> {png.name} (prompt_id={pid})")
    return {**meta, "prompt_id": pid, "gen_sec": gen_sec, "reused": False,
            "path": str(png), "meta": str(meta_path)}


def scene_plate_ok(scene_id: str, *, out_dir: str | Path | None = None) -> bool:
    """资产是否已就绪（png + meta 存在且 meta 引用一致）。"""
    png, meta_path = plate_paths(scene_id, out_dir=out_dir)
    meta = utils.read_json(meta_path) or {}
    return _valid_plate(png) and isinstance(meta, dict) and bool(meta.get("prompt"))
