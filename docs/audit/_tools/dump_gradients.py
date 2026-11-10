# -*- coding: utf-8 -*-
import re, sys

sys.path.insert(0, 'docs/audit/_tools')
from gen_design_legacy import top_level_rules, split_decls, strip_comments, ORDER, CSS_DIR  # noqa

rows = []
for name in ORDER:
    try:
        text = strip_comments(open(f'{CSS_DIR}/{name}', encoding='utf-8').read())
    except FileNotFoundError:
        continue
    for sel, body in top_level_rules(text):
        if not sel or sel.startswith('@'):
            continue
        decls = split_decls(body)
        grad = [d.strip() for d in decls if 'gradient(' in d]
        clip = [d.strip() for d in decls if 'background-clip' in d or 'text-fill' in d]
        if grad or clip:
            rows.append((name, sel.strip(), grad, clip))

out = []
for name, sel, grad, clip in rows:
    out.append(f'[{name}] {sel}')
    for g in grad:
        out.append('   G ' + g[:150])
    for c in clip:
        out.append('   C ' + c[:150])
open('docs/audit/_tools/gradients.txt', 'w', encoding='utf-8').write('\n'.join(out))
print('rules with gradient/clip:', len(rows))
