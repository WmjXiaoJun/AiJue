"""Style presets, palette derivation and spec normalization for the character studio."""
import colorsys

STYLE_PRESETS = {
    "3d_toon": dict(kind="toon", bands=3, outline=1.0, ortho=False, samples=24, outline_flat=0.0),
    "3d_cel": dict(kind="cel", bands=2, outline=1.7, ortho=False, samples=24, outline_flat=0.0),
    "2d_cel": dict(kind="flat", bands=1, outline=1.5, ortho=True, samples=8, outline_flat=0.0),
    "2d_paint": dict(kind="paint", bands=1, outline=1.1, ortho=True, samples=8, outline_flat=0.0),
    "anime": dict(kind="cel", bands=2, outline=1.35, ortho=True, samples=24, outline_flat=0.0),
    "guoman": dict(kind="toon", bands=3, outline=1.2, ortho=False, samples=24, outline_flat=0.0),
}

DEFAULT_APPEARANCE = {
    "gender": "neutral",
    "age": 30,
    "height_ratio": 1.0,
    "head_scale": 1.12,
    "chibi": 0.25,
    "skin": "#F2D6C3",
    "hair_color": "#2B2B33",
    "hair_style": "short_swept",
    "eye_color": "#2B2B33",
    "eye_size": 1.0,
    "brow": "thick",
    "facial_hair": "none",
    "outfit": {"type": "suit", "main": "#2F3A4F", "accent": "#F5C518", "shirt": "#F4F1E8"},
    "accessories": ["none"],
    "marks": [],
}


def hex_rgb(h):
    h = str(h).strip().lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    return tuple(int(h[i:i + 2], 16) / 255.0 for i in (0, 2, 4))


def rgb_hex(rgb):
    return "#" + "".join("%02X" % max(0, min(255, int(round(c * 255.0)))) for c in rgb)


def shade(h, f):
    r, g, b = hex_rgb(h)
    return rgb_hex((r * f, g * f, b * f))


def lighten(h, f):
    r, g, b = hex_rgb(h)
    return rgb_hex((r + (1 - r) * (f - 1) if f > 1 else r * f,
                    g + (1 - g) * (f - 1) if f > 1 else g * f,
                    b + (1 - b) * (f - 1) if f > 1 else b * f))


def mix(a, b, t):
    ra, ga, ba = hex_rgb(a)
    rb, gb, bb = hex_rgb(b)
    return rgb_hex((ra + (rb - ra) * t, ga + (gb - ga) * t, ba + (bb - ba) * t))


def lum(h):
    r, g, b = hex_rgb(h)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def saturate(h, f):
    r, g, b = hex_rgb(h)
    hh, ll, ss = colorsys.rgb_to_hls(r, g, b)
    ss = max(0.0, min(1.0, ss * f))
    rr, gg, bb = colorsys.hls_to_rgb(hh, ll, ss)
    return rgb_hex((rr, gg, bb))


def normalize_spec(spec):
    """Fill defaults, return a plain dict usable by the builder."""
    out = {}
    out["id"] = spec.get("id") or "character"
    out["name"] = spec.get("name") or out["id"]
    out["title"] = spec.get("title") or ""
    out["persona"] = spec.get("persona") or ""
    out["voice"] = spec.get("voice") or ""
    out["accent_color"] = spec.get("accent_color") or "#F5C518"
    st = spec.get("style") or {}
    primary = st.get("primary") or "3d_toon"
    variants = st.get("variants") or [primary]
    out["style"] = {"primary": primary, "variants": list(dict.fromkeys(variants))}
    ap = dict(DEFAULT_APPEARANCE)
    ap.update(spec.get("appearance") or {})
    outfit = dict(DEFAULT_APPEARANCE["outfit"])
    outfit.update(ap.get("outfit") or {})
    ap["outfit"] = outfit
    if not isinstance(ap.get("accessories"), list):
        ap["accessories"] = [ap["accessories"]]
    if not isinstance(ap.get("marks"), list):
        ap["marks"] = [ap["marks"]]
    out["appearance"] = ap
    return out


def palette_for(spec):
    """Derive the full material palette (hex strings) from the spec."""
    ap = spec["appearance"]
    outfit = ap["outfit"]
    accent = spec.get("accent_color") or outfit.get("accent") or "#F5C518"
    skin = ap["skin"]
    hair = ap["hair_color"]
    eye = ap["eye_color"]
    main = outfit.get("main") or "#2F3A4F"
    shirt = outfit.get("shirt") or "#F4F1E8"
    p = {}
    p["accent"] = accent
    p["accent_dark"] = shade(accent, 0.72)
    p["accent_light"] = lighten(accent, 1.35)
    p["skin"] = skin
    p["skin_shade"] = mix(shade(skin, 0.90), accent, 0.04)
    p["skin_deep"] = shade(skin, 0.78)
    p["skin_light"] = lighten(skin, 1.12)
    p["blush"] = mix(skin, "#E8878F", 0.42)
    p["hair"] = hair
    p["hair_dark"] = shade(hair, 0.70)
    p["hair_light"] = lighten(hair, 1.45)
    p["hair_shine"] = lighten(hair, 1.75)
    p["eye_white"] = "#FBFAFF"
    p["iris"] = eye
    p["iris_dark"] = shade(eye, 0.55)
    p["iris_light"] = lighten(eye, 1.55)
    p["pupil"] = "#141018"
    p["highlight"] = "#FFFFFF"
    p["lash"] = "#17131A"
    p["brow"] = shade(hair, 0.82)
    p["mouth"] = mix(skin, "#B4575F", 0.55)
    p["mouth_inner"] = "#7A2E38"
    p["tongue"] = "#D9737E"
    p["teeth"] = "#FFF8F2"
    p["main"] = main
    p["main_dark"] = shade(main, 0.76)
    p["main_light"] = lighten(main, 1.22)
    p["shirt"] = shirt
    p["shirt_shade"] = shade(shirt, 0.88)
    p["pants"] = shade(main, 0.62)
    p["pants_dark"] = shade(main, 0.48)
    p["shoes"] = "#16161C"
    p["metal"] = "#C9CED8"
    p["metal_dark"] = "#6B7280"
    p["frame"] = "#1E222A"
    p["lens"] = "#DCE7F2"
    p["facial_hair"] = mix(hair, skin, 0.18)
    p["stubble"] = mix(skin, hair, 0.17)
    p["outline"] = "#101218"
    p["outline_2d"] = "#191320"
    p["bg_dark"] = "#0B0D14"
    p["bg_mid"] = "#232838"
    return p


def style_config(name):
    cfg = dict(STYLE_PRESETS.get(name) or STYLE_PRESETS["3d_toon"])
    cfg["name"] = name
    if name not in STYLE_PRESETS:
        cfg = dict(STYLE_PRESETS["3d_toon"])
        cfg["name"] = name
    return cfg
