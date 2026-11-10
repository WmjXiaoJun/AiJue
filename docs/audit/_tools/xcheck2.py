import re, glob

# ---------- backend routes with prefix ----------
files = ['backend/app/main.py'] + sorted(glob.glob('backend/app/routers/*.py'))
prefpat = re.compile(r'APIRouter\(([^)]*)\)', re.S)
pat = re.compile(r'@(?:app|router|r)\.(get|post|put|patch|delete)\(\s*["\']([^"\']+)["\']')
BE = []
for f in files:
    src = open(f, encoding='utf-8').read()
    prefix = ''
    m = prefpat.search(src)
    if m:
        pm = re.search(r'prefix\s*=\s*["\']([^"\']*)["\']', m.group(1))
        if pm:
            prefix = pm.group(1)
    if f.endswith('main.py') or f.endswith('v5_studio.py') or f.endswith('v6_agent.py') \
       or 'character_' in f:
        # these files define routes with explicit /api/... paths
        pass
    for i, line in enumerate(src.splitlines(), 1):
        mm = pat.search(line)
        if mm:
            p = mm.group(2)
            if not p.startswith('/api') and not p.startswith('/media'):
                p = prefix + p
            BE.append({'m': mm.group(1).upper(), 'p': p, 'f': f.replace('\\', '/'), 'l': i})

def norm(p):
    p = re.sub(r'\{[^}]*\}', '{}', p)
    p = re.sub(r'<[^>]*>', '{}', p)
    return p.rstrip('/') or '/'

BSET = {}
for r in BE:
    BSET.setdefault(norm(r['p']), []).append(r)

# ---------- frontend path literals ----------
FE = []
for f in sorted(glob.glob('frontend/src/lib/*.ts') + glob.glob('frontend/src/**/*.tsx', recursive=True)):
    for i, line in enumerate(open(f, encoding='utf-8').read().splitlines(), 1):
        for m in re.finditer(r'["\'`](/[A-Za-z][A-Za-z0-9_\-./{}\[\]$]*)["\'`]', line):
            FE.append({'p': m.group(1), 'f': f.replace('\\', '/'), 'l': i, 'c': line.strip()[:150]})

def fe_norm(p):
    p = re.sub(r'\$\{[^}]*\}', '{}', p)
    return p.rstrip('/') or '/'

print("backend routes:", len(BE), " frontend literals:", len(FE))
print()
print("=== A. lib/*.ts paths with NO backend route (potential 404) ===")
seen = set()
for e in FE:
    if '/lib/' not in e['f'].replace('\\', '/'):
        continue
    n = fe_norm(e['p'])
    if n.startswith('/media'):
        continue
    cand = n if n.startswith('/api') else '/api' + n
    if norm(cand) in BSET:
        continue
    k = (e['f'], e['l'])
    if k in seen:
        continue
    seen.add(k)
    print(f"{e['f']}:{e['l']}  {e['p']}   -> looked for {cand}   | {e['c']}")
