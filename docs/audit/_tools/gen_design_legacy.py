# -*- coding: utf-8 -*-
"""把历史 CSS 里的硬编码色值「压」到 design.css 的 token 上。

- 只处理顶层规则（@media/@keyframes 内部跳过）
- 只改颜色类属性（color / background* / border* / box-shadow / text-shadow / fill / stroke）
- 输出到 design.css 末尾 `/* ===== AUTO: ...` 标记之后，可重复运行
"""
import re, sys, os

CSS_DIR = 'frontend/src/styles'
ORDER = [
    'base.css', 'components.css', 'ui-consistency.css', 'settings.css',
    'platform.css', 'shell.css', 'agent.css', 'v18.css', 'v54.css', 'v62.css',
    'library.css', 'creation.css', 'publish.css',
    'cinema.css', 'cinema-final.css', 'cinema-shots.css', 'cinema-templates.css',
    'studio.css', 'studio-a.css', 'studio-b.css', 'studio-3d.css', 'v5world.css',
]
DESIGN = 'frontend/src/styles/design.css'
MARKER = '/* ===== AUTO: legacy literals → tokens'

COLOR_PROPS = {'color', 'fill', 'stroke', 'caret-color'}
BG_PROPS = {'background', 'background-color'}
BORDER_PREFIX = ('border', 'outline-color')
SHADOW_PROPS = {'box-shadow', 'text-shadow'}

HEX = re.compile(r'#([0-9a-fA-F]{3,8})\b')
RGBA = re.compile(r'rgba?\(\s*([\d.]+)\s*,\s*([\d.]+)\s*,\s*([\d.]+)\s*(?:,\s*([\d.]+)\s*)?\)')


def parse_color(token):
    m = HEX.fullmatch(token.strip())
    if m:
        h = m.group(1)
        if len(h) == 3:
            h = ''.join(c * 2 for c in h)
        if len(h) == 4:
            h = ''.join(c * 2 for c in h[:3])
        if len(h) == 8:
            h = h[:6]
        if len(h) != 6:
            return None
        r, g, b = (int(h[i:i + 2], 16) for i in (0, 2, 4))
        return (r / 255, g / 255, b / 255, 1.0)
    m = RGBA.fullmatch(token.strip())
    if m:
        r, g, b = (float(m.group(i)) / 255 for i in (1, 2, 3))
        a = float(m.group(4)) if m.group(4) is not None else 1.0
        return (r, g, b, a)
    return None


def luma(c):
    r, g, b = c[0], c[1], c[2]
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def is_gold(c):
    r, g, b, a = c
    return r > 0.55 and g > 0.33 and b < 0.45 and (r - b) > 0.18 and (r - g) >= 0


def is_white_alpha(c):
    r, g, b, a = c
    return r > 0.96 and g > 0.96 and b > 0.96 and a < 0.98


def pct(a):
    return f'{round(a * 100)}%'


def mix(token, a):
    return f'color-mix(in srgb, var({token}) {pct(a)}, transparent)'


def map_color(c):
    if is_gold(c):
        return None if c[3] < 0.98 else 'var(--accent)'
    L = luma(c)
    if is_white_alpha(c) or L >= 0.82:
        return 'var(--text)'
    if L >= 0.60:
        return 'var(--text-dim)'
    if L >= 0.42:
        return 'var(--text-mute)'
    return None  # 深色文字（多为金底上的墨色）保留


def map_bg(c):
    if is_gold(c):
        return 'var(--accent)' if c[3] >= 0.98 else mix('--accent', c[3])
    L = luma(c)
    a = c[3]
    if is_white_alpha(c):
        return 'var(--surface-2)' if a >= 0.01 else None
    if L <= 0.10:
        return 'var(--bg)' if a >= 0.98 else mix('--bg', a)
    if L <= 0.30:
        return 'var(--surface)' if a >= 0.98 else mix('--surface', a)
    if L <= 0.45:
        return 'var(--surface-2)'
    if L >= 0.90:
        return 'var(--surface)'
    return None


def map_border(c):
    if is_gold(c) or is_white_alpha(c):
        return 'var(--line-strong)' if c[3] >= 0.5 else 'var(--line)'
    L = luma(c)
    if L <= 0.32 or L >= 0.85:
        return 'var(--line)'
    return None


def transform(prop, value, important):
    raw = value.strip()
    out = raw
    changed = False

    if prop in BG_PROPS and 'gradient(' in raw:
        # 渐变面：取其中最"实"的一档颜色压成平色；全是低透明度叠加（遮罩/光斑）则保留
        cols = [parse_color(t) for t in re.findall(r'#[0-9a-fA-F]{3,8}\b|rgba?\([^)]*\)', raw)]
        cols = [c for c in cols if c]
        strong = [c for c in cols if c[3] >= 0.5]
        if not strong:
            return None
        c = max(strong, key=lambda x: x[3])
        v = map_bg(c) or 'var(--surface)'
        return v + (' !important' if important else '')

    if 'gradient(' in raw:
        return None

    if prop in COLOR_PROPS:
        def rep(m):
            nonlocal changed
            c = parse_color(m.group(0))
            if not c:
                return m.group(0)
            v = map_color(c)
            if v:
                changed = True
                return v
            return m.group(0)
        out = re.sub(r'#[0-9a-fA-F]{3,8}\b|rgba?\([^)]*\)', rep, raw)
    elif prop in BG_PROPS:
        # 整值就是一个颜色时优先整体映射
        c = parse_color(raw)
        if c:
            v = map_bg(c)
            if v:
                return v + (' !important' if important else '')
            return None
        return None
    elif prop.startswith(BORDER_PREFIX):
        c = parse_color(raw)
        if c:
            v = map_border(c)
            if v:
                return ('1px solid ' + v if prop == 'border' else v) + (' !important' if important else '')
            return None
        # border: 1px solid #xxx 形式
        m = re.fullmatch(r'([\d.]+px\s+\w+\s+)(#[0-9a-fA-F]{3,8}|rgba?\([^)]*\))', raw)
        if m:
            c = parse_color(m.group(2))
            v = map_border(c) if c else None
            if v:
                return m.group(1) + v + (' !important' if important else '')
        return None
    elif prop in SHADOW_PROPS:
        if raw == 'none':
            return None
        if 'gradient(' in raw:
            return None
        has_dark = re.search(r'rgba?\(\s*(?:0|1?\d|2\d)\s*,\s*(?:0|1?\d|2\d)\s*,\s*(?:0|1?\d|2\d)', raw)
        if prop == 'text-shadow':
            return 'none' + (' !important' if important else '')
        if has_dark:
            return 'var(--shadow-1)' + (' !important' if important else '')
        return None
    else:
        return None

    if changed:
        return out + (' !important' if important else '')
    return None


def strip_comments(s):
    return re.sub(r'/\*.*?\*/', '', s, flags=re.S)


def top_level_rules(text):
    """产出 (selector, body) —— 仅顶层规则。"""
    i, n, depth = 0, len(text), 0
    buf = ''
    out = []
    for ch in text:
        if ch == '{':
            if depth == 0:
                sel, buf = buf.strip(), ''
                depth = 1
                cur_sel = sel
                cur_body = ''
            else:
                depth += 1
                if depth == 2:
                    cur_body += ch
            continue
        if ch == '}':
            depth -= 1
            if depth == 0:
                out.append((cur_sel, cur_body))
                cur_body = ''
            elif depth == 1:
                cur_body += ch
            continue
        if depth == 0:
            buf += ch
        elif depth == 1:
            cur_body += ch
    return out


def split_decls(body):
    parts, buf, paren = [], '', 0
    for ch in body:
        if ch == '(':
            paren += 1
        elif ch == ')':
            paren -= 1
        if ch == ';' and paren == 0:
            parts.append(buf)
            buf = ''
        else:
            buf += ch
    if buf.strip():
        parts.append(buf)
    return parts


rows = []   # (selector, prop, value)
for name in ORDER:
    path = os.path.join(CSS_DIR, name)
    if not os.path.exists(path):
        print('skip missing', path)
        continue
    text = strip_comments(open(path, encoding='utf-8').read())
    for sel, body in top_level_rules(text):
        if not sel or sel.startswith('@'):
            continue
        if re.match(r'^(from|to|\d+%)$', sel.strip()):
            continue
        for decl in split_decls(body):
            if ':' not in decl:
                continue
            prop, _, value = decl.partition(':')
            prop = prop.strip().lower()
            important = '!important' in value
            value = value.replace('!important', '').strip()
            if not prop or not value:
                continue
            # 渐变文字（background-clip:text + color:transparent）：改成实色强调字
            if 'background-clip' in prop and 'text' in value:
                rows.append((sel.strip(), 'background-image', 'none !important'))
                rows.append((sel.strip(), 'color', 'var(--accent) !important'))
                rows.append((sel.strip(), '-webkit-text-fill-color', 'currentColor !important'))
                continue
            new = transform(prop, value, important)
            if new and new != value:
                rows.append((sel.strip(), prop, new))

# 同 (selector, prop) 只保留最后一次（等价于原级联结果）
dedup = {}
for sel, prop, val in rows:
    dedup[(sel, prop)] = val

# 按选择器聚合
grouped = {}
order = []
for (sel, prop), val in dedup.items():
    if sel not in grouped:
        grouped[sel] = []
        order.append(sel)
    grouped[sel].append(f'{prop}: {val}')

block = ['', '/* 以下由脚本生成：旧硬编码色值 → token；只含颜色属性，不改布局 */', '']
for sel in order:
    block.append(sel + ' {')
    for d in grouped[sel]:
        block.append('  ' + d + ';')
    block.append('}')
block.append('')

gen = '\n'.join(block)

src = open(DESIGN, encoding='utf-8').read()
idx = src.find(MARKER)
if idx < 0:
    print('MARKER not found in design.css'); sys.exit(1)
END = '/* ===== END AUTO ===== */'
tail = ''
t = src.find(END)
if t >= 0:
    tail = src[t:]
head = src[:idx]
open(DESIGN, 'w', encoding='utf-8', newline='').write(
    head + MARKER + '（由 docs/audit/_tools/gen_design_legacy.py 生成，勿手改） ===== */' + gen + tail)

print('rules:', len(order), 'decls:', len(dedup), 'out bytes:', len(gen))
