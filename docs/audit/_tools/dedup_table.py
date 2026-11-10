import re, collections

rows = []
for line in open('docs/audit/_tools/contract_table.txt', encoding='utf-8'):
    m = re.match(r'\s*\d+\s+(\S+)\s+(\S+)\s+(\S+)\s+(OK|MISS|STATIC|METHOD\?)\s+(\S+)', line)
    if m:
        rows.append({'method': m.group(1), 'path': m.group(2), 'be': m.group(3), 'res': m.group(4), 'loc': m.group(5)})

# dedup by normalized path, keep first location + set of methods
g = collections.OrderedDict()
for r in rows:
    p = re.sub(r'\$\{[^}]*\}', '{}', r['path'])
    p = re.sub(r'\{[^}]*\}', '{}', p)
    p = re.sub(r'\{\}+', '{}', p)
    p = p.split('?')[0]
    key = p
    if key not in g:
        g[key] = {'methods': set(), 'be': r['be'], 'res': r['res'], 'loc': r['loc'], 'n': 0}
    g[key]['methods'].add(r['method'])
    g[key]['n'] += 1
    if g[key]['res'] == 'MISS' and r['res'] != 'MISS':
        g[key]['res'] = r['res']

print(f"{'METHOD':16s} {'FRONTEND PATH':50s} {'后端':22s} {'结论':8s} 首次出现")
for k, v in g.items():
    methods = ','.join(sorted(m if m != '?' else 'GET*' for m in v['methods']))
    print(f"{methods:16s} {k:50s} {v['be']:22s} {v['res']:8s} {v['loc']}")
print()
print('rows:', len(rows), 'unique paths:', len(g))
