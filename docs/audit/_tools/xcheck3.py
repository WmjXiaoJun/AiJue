import re, glob, json

# ---------- backend ----------
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

# ---------- frontend: reconstruct concatenated path expressions ----------
def collect_expr(lines, start_idx, start_col, max_lines=4):
    """Join literal fragments from a JS expression starting at (line,col)."""
    buf = ''
    depth_paren = 0
    i = start_idx
    col = start_col
    while i < len(lines) and i < start_idx + max_lines:
        line = lines[i]
        j = col
        while j < len(line):
            ch = line[j]
            if ch in '"\'':
                q = ch
                j += 1
                lit = ''
                while j < len(line) and line[j] != q:
                    if line[j] == '\\':
                        j += 2
                        continue
                    lit += line[j]
                    j += 1
                buf += lit
                j += 1
                continue
            if ch == '`':
                j += 1
                lit = ''
                while j < len(line) and line[j] != '`':
                    lit += line[j]
                    j += 1
                buf += re.sub(r'\$\{[^}]*\}', '{}', lit)
                j += 1
                continue
            if ch in '()' or ch in '[]':
                depth_paren += 1 if ch in '([' else -1
            if ch == ',' and depth_paren == 0:
                return buf, i, j
            if ch in ' \t\r\n+':
                j += 1
                continue
            # an identifier => a variable is being concatenated
            if ch.isalpha() or ch in '_$':
                k = j
                while k < len(line) and (line[k].isalnum() or line[k] in '_$.'):
                    k += 1
                token = line[j:k]
                if token in ('encodeURIComponent', 'enc', 'String', 'JSON', 'qs', 'apiUrl', 'mediaSrc', 'mediaUrl', 'API_BASE'):
                    j = k
                    continue
                buf += '{}'
                j = k
                continue
            j += 1
        i += 1
        col = 0
    return buf, i, col

FE = []
callpat = re.compile(r'\brequest\b|\bfetch\s*\(')
for f in sorted(glob.glob('frontend/src/lib/*.ts')):
    lines = open(f, encoding='utf-8').read().splitlines()
    for i, line in enumerate(lines):
        for m in re.finditer(r'(request|fetch|apiUrl|apiGet|apiPost|post|put|del|get)\s*(?:<[^>]*>)?\s*\(', line):
            col = m.end()
            # skip generic type already consumed; find first arg
            expr, _, _ = collect_expr(lines, i, col)
            expr = expr.strip()
            if not expr.startswith('/'):
                continue
            FE.append({'p': expr, 'f': f.replace('\\', '/'), 'l': i + 1, 'c': line.strip()[:150]})

print("backend:", len(BE), "fe calls:", len(FE))
print()
print("=== lib/*.ts API paths NOT found in backend ===")
rows = []
for e in FE:
    n = re.sub(r'\{\}', '{}', e['p']).rstrip('/') or '/'
    if n.startswith('/media'):
        continue
    cand = n if n.startswith('/api') else '/api' + n
    cand = norm(cand)
    if cand in BSET:
        continue
    rows.append(e)
    print(f"{e['f']}:{e['l']}  {e['p']}  -> {cand}")
print()
print("=== ALL FRONTEND API CALLS (dedup path) ===")
allp = {}
for e in FE:
    n = e['p'].rstrip('/') or '/'
    cand = norm((n if n.startswith('/api') else '/api' + n))
    allp.setdefault(cand, []).append(e)
for k in sorted(allp):
    okcnt = len(BSET.get(k, []))
    methods = sorted({r['m'] for r in BSET.get(k, [])})
    first = allp[k][0]
    print(f"{'OK ' if okcnt else 'MISS'} {k:55s} backend={methods if okcnt else '-'}   {first['f']}:{first['l']}")
