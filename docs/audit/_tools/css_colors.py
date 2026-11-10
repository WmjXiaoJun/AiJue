# -*- coding: utf-8 -*-
import re, collections, glob

colors = collections.Counter()
props = collections.Counter()
pairs = collections.Counter()
for f in glob.glob('frontend/src/styles/*.css'):
    for line in open(f, encoding='utf-8'):
        m = re.search(r'^\s*([a-z-]+)\s*:\s*([^;]+);', line)
        if not m:
            continue
        prop, val = m.group(1), m.group(2).strip()
        for c in re.findall(r'#[0-9a-fA-F]{3,8}|rgba?\([^)]*\)', val):
            colors[c.lower()] += 1
            if prop in ('color', 'background', 'background-color', 'border', 'border-color', 'box-shadow'):
                pairs[(prop, c.lower())] += 1
print('=== 最常见颜色字面量 ===')
for c, n in colors.most_common(28):
    print(f'{n:4d}  {c}')
print()
print('=== color: 属性最常见的字面量 ===')
for (p, c), n in pairs.most_common(40):
    if p == 'color':
        print(f'{n:4d}  color: {c}')
print()
print('=== background 最常见字面量 ===')
for (p, c), n in pairs.most_common(60):
    if p.startswith('background'):
        print(f'{n:4d}  {p}: {c}')
