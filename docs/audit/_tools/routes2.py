import re, glob

files = ['backend/app/main.py'] + sorted(glob.glob('backend/app/routers/*.py'))
prefpat = re.compile(r'APIRouter\(([^)]*)\)', re.S)
# decorator may span lines: @router.post(\n "path", ...)
deco = re.compile(r'@(?:app|router|r)\.(get|post|put|patch|delete)\s*\(\s*\n?\s*f?["\']([^"\']*)["\']')

BE = []
for f in files:
    src = open(f, encoding='utf-8').read()
    prefix = ''
    m = prefpat.search(src)
    if m:
        pm = re.search(r'prefix\s*=\s*["\']([^"\']*)["\']', m.group(1))
        if pm:
            prefix = pm.group(1)
    if f.endswith('main.py'):
        prefix = '/api'
    for mm in deco.finditer(src):
        p = mm.group(2)
        if not p.startswith('/api') and not p.startswith('/media'):
            p = prefix + p
        line = src[:mm.start()].count('\n') + 1
        BE.append({'m': mm.group(1).upper(), 'p': p or '/', 'f': f.replace('\\', '/'), 'l': line})

def norm(p):
    p = re.sub(r'\{[^}]*\}', '{}', p)
    p = re.sub(r'<[^>]*>', '{}', p)
    return p.rstrip('/') or '/'

S = {}
for r in BE:
    S.setdefault(norm(r['p']), []).append((r['m'], r['f'] + ':' + str(r['l'])))

print('BACKEND ROUTES:', len(BE), ' unique paths:', len(S))
print()
import json, sys
json.dump([{k: v for k, v in r.items()} for r in BE], open('docs/audit/_tools/backend_routes.json', 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
print('wrote docs/audit/_tools/backend_routes.json')
for k in sorted(S):
    print(f"{k:52s} {[x[0] for x in S[k]]}  {S[k][0][1]}")
