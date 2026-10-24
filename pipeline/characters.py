"""角色工坊素材接入。

扫描 outputs/characters/{cid}/（可用环境变量 CHARACTERS_DIR 覆盖）：
- portrait.png                         半身立绘（768x768，深色影棚背景）
- expressions/{neutral,happy,surprised,thinking}.png
- turntable.mp4                        360 度转面（720x720 / 4s）
- meta.json（studio3d 输出，可选）
- style_<name>/ 子目录：若存在，优先使用 contract/characters/{cid}.json 里
  style.primary 对应的风格目录，其次字典序第一个。

另外合并 SDXL 概念图 outputs/concepts/{cid}/（可用 CONCEPTS_DIR 覆盖）：
- portrait.png               832x1216 竖构图概念立绘（SDXL / IP-Adapter 一致性）
- expressions/*.png          概念表情
- three_view/*.png、sheet.png、meta.json（选修）

成片素材优先级（contract/CONSISTENCY.md）：
h3 视频 > h3 中段帧 > 概念立绘 > Blender cutout > 2D > 文字卡。

把 episode.assets 里的角色资产（host / guest_a / ...）映射到角色素材，
供 mock_shot 合成「角色画面」镜头；素材缺失时调用方回退文字卡视觉。
"""
from __future__ import annotations

import logging
import os
from pathlib import Path

from . import config, utils

LOG = logging.getLogger("pipeline")

EXPRESSIONS = ("neutral", "happy", "surprised", "thinking")
CHARACTERS_SUBDIR = "characters"
CONCEPTS_SUBDIR = "concepts"
_ALPHA_CACHE: dict[tuple[str, int, int], bool] = {}


def default_root() -> Path:
    env = os.environ.get("CHARACTERS_DIR")
    if env:
        return Path(env)
    return config.REPO_ROOT / "outputs" / CHARACTERS_SUBDIR


def load_specs() -> dict:
    """读取 contract/characters/*.json（只读），返回 {id: spec}。"""
    specs: dict[str, dict] = {}
    spec_dir = config.REPO_ROOT / "contract" / "characters"
    if spec_dir.is_dir():
        for path in sorted(spec_dir.glob("*.json")):
            data = utils.read_json(path)
            if isinstance(data, dict) and data.get("id"):
                specs[str(data["id"])] = data
    return specs


def _valid(path: Path | None) -> bool:
    try:
        return path is not None and Path(path).is_file() and Path(path).stat().st_size > 512
    except OSError:
        return False


def concepts_root() -> Path:
    """SDXL 概念图目录（默认 outputs/concepts/，可用 CONCEPTS_DIR 覆盖）。

    概念图由 imagegen 生成：portrait.png（832x1216 竖构图）、
    expressions/*.png、three_view/*.png、sheet.png、meta.json。
    """
    env = os.environ.get("CONCEPTS_DIR")
    if env:
        return Path(env)
    return config.REPO_ROOT / "outputs" / CONCEPTS_SUBDIR


def _find_concept_assets(cid: str, root: Path | str | None = None) -> dict:
    """读取 outputs/concepts/{cid}/ 下的 SDXL 概念图素材。"""
    base = Path(root) if root else concepts_root()
    cdir = base / cid
    if not cdir.is_dir():
        return {}
    portrait = _first_valid(cdir / "portrait.png")
    expressions: dict[str, Path] = {}
    for name in EXPRESSIONS:
        found = _first_valid(cdir / "expressions" / f"{name}.png")
        if found:
            expressions[name] = found
    meta = utils.read_json(cdir / "meta.json") or {}
    if portrait is None and not expressions:
        return {}
    return {
        "concept_dir": cdir,
        "concept_portrait": portrait,
        "concept_expressions": expressions,
        "concept_meta": meta,
    }


def h3_dir() -> Path:
    """真实 H3 生成产物目录（默认 outputs/h3/，可用 H3_DIR 覆盖）。"""
    env = os.environ.get("H3_DIR")
    if env:
        return Path(env)
    return config.REPO_ROOT / "outputs" / "h3"


def _find_h3_video(cid: str, root: Path | None = None) -> Path | None:
    """角色对应的 H3 真实视频：{cid}_h3.mp4 / {cid}.mp4 / {cid}_*.mp4。"""
    base = Path(root) if root else h3_dir()
    for name in (f"{cid}_h3.mp4", f"{cid}.mp4"):
        candidate = base / name
        if _valid(candidate):
            return candidate
    if base.is_dir():
        for candidate in sorted(base.glob(f"{cid}*.mp4")):
            if _valid(candidate):
                return candidate
    return None


def _find_h3_portrait(cdir: Path, cid: str, root: Path | None = None) -> Path | None:
    """角色 H3 中段帧：characters/{cid}/h3_portrait.png 或 outputs/h3/{cid}_h3_fmid.png。"""
    for candidate in (
        cdir / "h3_portrait.png",
        cdir / "h3" / "h3_portrait.png",
        cdir / "cutout" / "h3_portrait.png",
    ):
        if _valid(candidate):
            return candidate
    base = Path(root) if root else h3_dir()
    for name in (f"{cid}_h3_fmid.png", f"{cid}_fmid.png", f"{cid}_portrait.png"):
        candidate = base / name
        if _valid(candidate):
            return candidate
    return None


def _style_dirs(cdir: Path) -> list[Path]:
    try:
        return [d for d in cdir.iterdir()
                if d.is_dir() and d.name.lower().startswith("style")]
    except OSError:
        return []


def _preferred_style_dir(cdir: Path, primary: str | None) -> Path | None:
    dirs = _style_dirs(cdir)
    if not dirs:
        return None
    if primary:
        key = str(primary).lower().replace("_", "").replace("-", "")
        for d in sorted(dirs):
            if key and key in d.name.lower().replace("_", "").replace("-", ""):
                return d
    return sorted(dirs)[0]


def _first_valid(*paths: Path | None) -> Path | None:
    for path in paths:
        if _valid(path):
            return Path(path)
    return None


def discover(root: Path | str | None = None,
             concepts_root_dir: Path | str | None = None) -> dict[str, dict]:
    """返回 {character_id: char_assets}；目录不存在或为空时返回 {}。

    root: 角色工坊目录（默认 outputs/characters/）
    concepts_root_dir: SDXL 概念图目录；None 时在 root 为默认目录的情况下自动合并
        outputs/concepts/（测试传入自定义 root 时默认不合并，保证隔离）。
    """
    root_path = Path(root) if root else default_root()
    specs = load_specs()
    result: dict[str, dict] = {}
    if not root_path.is_dir():
        result = {}
    else:
        _scan_character_dir(root_path, specs, result)
    # ---- 合并 SDXL 概念图（新优先级第 3 档）----
    croot = Path(concepts_root_dir) if concepts_root_dir is not None else (
        concepts_root() if root is None else None)
    if croot is not None and croot.is_dir():
        for cdir in sorted(croot.iterdir()):
            if not cdir.is_dir():
                continue
            cid = cdir.name
            concept = _find_concept_assets(cid, croot)
            if not concept:
                continue
            if cid in result:
                result[cid].update(concept)
            else:
                spec = specs.get(cid) or {}
                meta = concept.get("concept_meta") or {}
                name = str(meta.get("name") or spec.get("name") or cid)
                title = str(meta.get("title") or spec.get("title") or "")
                accent = str(meta.get("accent_color") or spec.get("accent_color")
                             or config.ACCENT_DEFAULT)
                entry = {
                    "id": cid,
                    "name": name,
                    "title": title,
                    "accent_color": accent,
                    "style": None,
                    "visual_style": "concept",
                    "basis": "concepts",
                    "dir": cdir,
                    "portrait": None,
                    "turntable": None,
                    "expressions": {},
                    "h3_video": _find_h3_video(cid),
                    "h3_portrait": _find_h3_portrait(cdir, cid),
                }
                entry.update(concept)
                result[cid] = entry
            result[cid]["concept_portrait"] = concept.get("concept_portrait")
            result[cid]["concept_expressions"] = concept.get("concept_expressions") or {}
            LOG.info("合并 SDXL 概念图: %s concept_portrait=%s expressions=%d",
                     cid, bool(concept.get("concept_portrait")),
                     len(concept.get("concept_expressions") or {}))
    return result


def _scan_character_dir(root_path: Path, specs: dict, result: dict) -> None:
    """扫描角色工坊目录，填充 result（供 discover 调用）。"""
    for cdir in sorted(root_path.iterdir()):
        if not cdir.is_dir():
            continue
        cid = cdir.name
        spec = specs.get(cid) or {}
        meta = utils.read_json(cdir / "meta.json") or {}
        style = (meta.get("style") or spec.get("style") or {})
        primary = style.get("primary") if isinstance(style, dict) else style
        # 素材优先级（lead 约定）：3D 透明 cutout -> 3D 根目录 -> 2D -> style_* 子目录；
        # 透明底立绘（cutout/）优先，便于抠像直接叠到节目背景。
        style_dirs = _style_dirs(cdir)
        preferred = _preferred_style_dir(cdir, primary)
        ordered_dirs: list[Path] = []

        def _add_dir(path: Path | None) -> None:
            if path is None or not path.is_dir() or path in ordered_dirs:
                return
            ordered_dirs.append(path)

        _add_dir(cdir / "cutout")
        _add_dir(cdir)
        _add_dir(cdir / "2d" / "cutout")
        _add_dir(cdir / "2d")
        for style_dir in ([preferred] if preferred else []) + style_dirs:
            _add_dir(style_dir / "cutout")
            _add_dir(style_dir)

        def pick(rel: str) -> Path | None:
            return _first_valid(*(base / rel for base in ordered_dirs))

        portrait = pick("portrait.png")
        turntable = pick("turntable.mp4")
        expressions: dict[str, Path] = {}
        for name in EXPRESSIONS:
            found = pick(f"expressions/{name}.png")
            if found:
                expressions[name] = found
        # 额外表情（容错：只补第一个未命名表情）
        for base in ordered_dirs:
            exp_dir = base / "expressions"
            if not exp_dir.is_dir():
                continue
            for png in sorted(exp_dir.glob("*.png")):
                if png.stem not in expressions and _valid(png):
                    expressions[png.stem] = png
                    break
        h3_video = _find_h3_video(cid)
        h3_portrait = _find_h3_portrait(cdir, cid)
        if portrait is None and turntable is None and not expressions \
                and h3_video is None and h3_portrait is None:
            continue
        name = str(meta.get("name") or spec.get("name") or cid)
        title = str(meta.get("title") or spec.get("title") or "")
        accent = str(meta.get("accent_color") or spec.get("accent_color")
                     or config.ACCENT_DEFAULT)
        portrait = portrait or next(iter(expressions.values()), None)
        basis = ""
        if portrait is not None:
            try:
                rel = portrait.parent.relative_to(cdir)
                basis = "" if str(rel) == "." else str(rel).replace("\\", "/")
            except ValueError:
                basis = ""
        visual_style = "2d" if (("2d" in basis) or "2d" in str(primary or "").lower()) else "3d"
        result[cid] = {
            "id": cid,
            "name": name,
            "title": title,
            "accent_color": accent,
            "style": primary,
            "visual_style": visual_style,
            "basis": basis,
            "dir": cdir,
            "portrait": portrait,
            "turntable": turntable,
            "expressions": expressions,
            "h3_video": h3_video,
            "h3_portrait": h3_portrait,
        }
        LOG.info("发现角色素材: %s (%s) style=%s basis=%s portrait=%s turntable=%s "
                 "expressions=%d h3_video=%s h3_portrait=%s",
                 cid, name, primary, basis or "根目录", bool(portrait),
                 bool(turntable), len(expressions), bool(h3_video), bool(h3_portrait))


def resolve_for_assets(assets: list, chars: dict | None = None) -> dict[str, dict]:
    """角色资产 id -> 角色素材；按 character_id / id / name 依次匹配。"""
    chars = chars if chars is not None else discover()
    if not chars:
        return {}
    by_name: dict[str, dict] = {}
    for char in chars.values():
        by_name.setdefault(char["name"], char)
    mapping: dict[str, dict] = {}
    for asset in assets or []:
        if not isinstance(asset, dict):
            continue
        aid = asset.get("id")
        if aid is None:
            continue
        target = None
        cid = asset.get("character_id")
        if cid:
            target = chars.get(str(cid))
        if target is None:
            target = chars.get(str(aid))
        if target is None:
            target = by_name.get(str(asset.get("name") or ""))
        if target is not None:
            mapping[str(aid)] = target
    return mapping


def order_cast(char_map: dict) -> list[dict]:
    """去重后按资产顺序返回角色（host, guest_a, guest_b...），用于片头片尾蒙太奇。"""
    seen: set[str] = set()
    ordered: list[dict] = []
    for char in (char_map or {}).values():
        key = str(char.get("id"))
        if key in seen:
            continue
        seen.add(key)
        ordered.append(char)
    return ordered


def fingerprint(char_map: dict) -> str:
    """角色素材指纹（文件大小 + mtime），素材更新后让镜头片段缓存自动失效。"""
    parts: list[str] = []
    seen: set[str] = set()
    for char in (char_map or {}).values():
        key = str(char.get("id"))
        if key in seen:
            continue
        seen.add(key)
        paths: list[Path] = []
        for key in ("portrait", "turntable", "h3_video", "h3_portrait",
                    "concept_portrait"):
            if char.get(key):
                paths.append(char[key])
        paths.extend((char.get("expressions") or {}).values())
        paths.extend((char.get("concept_expressions") or {}).values())
        for path in paths:
            try:
                st = Path(path).stat()
                parts.append(f"{Path(path).name}:{st.st_size}:{st.st_mtime_ns}")
            except OSError:
                continue
    return utils.sha1_text(*parts) if parts else ""


_MOOD_KEYWORDS = (
    ("surprised", ("？！", "?!", "！？", "惊讶", "没想到", "哇", "啊？")),
    ("happy", ("笑", "哈哈", "乐", "妙", "好家伙", "精彩")),
    ("thinking", ("……", "...", "嗯", "想", "认", "？", "？")),
)


def choose_expression(char: dict, text: str = "", mood: str = "") -> Path | None:
    """按 shot.mood 或台词关键词挑表情；缺表情时回退立绘。"""
    exprs: dict[str, Path] = char.get("expressions") or {}
    if not exprs:
        return char.get("portrait")
    mood = str(mood or "").strip().lower()
    if mood in exprs:
        return exprs[mood]
    content = str(text or "")
    for name, keywords in _MOOD_KEYWORDS:
        if any(k in content for k in keywords):
            return exprs.get(name) or exprs.get("neutral")
    return exprs.get("neutral") or next(iter(exprs.values()))


def has_alpha(path: Path | str | None) -> bool:
    """PNG 是否带透明通道（alpha 立绘可直接抠像叠加）。"""
    if not path:
        return False
    p = Path(path)
    try:
        st = p.stat()
    except OSError:
        return False
    cache_key = (str(p), int(st.st_size), int(st.st_mtime_ns))
    cached = _ALPHA_CACHE.get(cache_key)
    if cached is not None:
        return cached
    result = False
    try:
        info = utils.ffprobe_json(p)
        for stream in info.get("streams", []):
            if stream.get("codec_type") == "video":
                pix_fmt = str(stream.get("pix_fmt") or "").lower()
                result = pix_fmt in (
                    "rgba", "bgra", "argb", "abgr", "yuva420p", "ya8", "pal8",
                )
                break
    except Exception as exc:  # noqa: BLE001
        LOG.debug("探测 alpha 失败 %s: %s", p, exc)
    _ALPHA_CACHE[cache_key] = result
    return result
