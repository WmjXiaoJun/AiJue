import re, glob, json, os

BACKEND = []
files = ['backend/app/main.py'] + sorted(glob.glob('backend/app/routers/*.py'))
prefpat = re.compile(r'APIRouter\(([^)]*)\)', re.S)
pat = re.compile(r'@(?:app|router|r)\.(get|post|put|patch|delete)\(\s*["\']([^"\']+)["\']')

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
    for i, line in enumerate(src.splitlines(), 1):
        mm = pat.search(line)
        if mm:
            p = prefix + mm.group(2)
            BACKEND.append({'method': mm.group(1).upper(), 'path': p,
                            'file': f.replace('\\', '/'), 'line': i})

def norm(p):
    p = re.sub(r'\{[^}]*\}', '{}', p)
    return p.rstrip('/') or '/'

BSET = {}
for r in BACKEND:
    BSET.setdefault(norm(r['path']), []).append(r)

# frontend path literals
fe = []
for f in sorted(glob.glob('frontend/src/lib/*.ts') + glob.glob('frontend/src/components/**/*.tsx', recursive=True) + glob.glob('frontend/src/*.tsx')):
    for i, line in enumerate(open(f, encoding='utf-8').read().splitlines(), 1):
        for m in re.finditer(r'["\'`](/[A-Za-z][A-Za-z0-9_\-./{}\[\]]*)["\'`]', line):
            p = m.group(1)
            if p.startswith('//'):
                continue
            fe.append({'path': p, 'file': f.replace('\\', '/'), 'line': i, 'code': line.strip()[:160]})

print("BACKEND ROUTES:", len(BACKEND))
print("FRONTEND PATH LITERALS:", len(fe))
print()

def fe_norm(p):
    p = re.sub(r'\$\{[^}]*\}', '{}', p)
    return p.rstrip('/') or '/'

print("=== FRONTEND LITERALS NOT MATCHING ANY BACKEND ROUTE (candidate dead paths) ===")
seen = set()
for e in fe:
    n = fe_norm(e['path'])
    if n in BSET:
        continue
    # fuzzy: backend has same shape with more segments? skip
    key = (e['file'], e['line'], e['path'])
    if key in seen:
        continue
    seen.add(key)
    print(f"{e['file']}:{e['line']}  {e['path']}   |  {e['code']}")
