import re, json, glob

BE = json.load(open('docs/audit/_tools/backend_routes.json', encoding='utf-8'))

def norm(p):
    p = re.sub(r'\$\{[^}]*\}', '{}', p)
    p = re.sub(r'\{[^}]*\}', '{}', p)
    p = p.split('?')[0]
    p = re.sub(r'\{\}+', '{}', p)
    p = re.sub(r'<[^>]*>', '{}', p)
    return p.rstrip('/') or '/'

S = {}
for r in BE:
    S.setdefault(norm(r['p']), []).append(r['m'] + '@' + r['f'].split('/')[-1] + ':' + str(r['l']))

CALL = re.compile(r'\b(request|get|post|put|del|fetch|apiUrl)\s*(?:<[^>]*>)?\s*\(')
LIT = re.compile(r'["\'`](/[^"\'`]*)["\'`]')
VAR = re.compile(r'\(\s*([A-Za-z_$][\w$.]*(?:\([^)]*\))?)')

rows = []
for f in sorted(glob.glob('frontend/src/lib/*.ts')) + ['frontend/src/components/studio/Templates3D.tsx']:
    lines = open(f, encoding='utf-8').read().splitlines()
    for i, line in enumerate(lines):
        for m in CALL.finditer(line):
            buf = line[m.end():]
            j = i
            # join continuation lines until parentheses balance or 4 lines
            depth = buf.count('(') - buf.count(')')
            while depth > 0 and j + 1 < len(lines) and j - i < 4:
                j += 1
                buf += ' ' + lines[j]
                depth = buf.count('(') - buf.count(')')
            # find first argument
            depth2 = 0
            expr = ''
            for ch_i, ch in enumerate(buf):
                if ch in '([':
                    depth2 += 1
                elif ch in ')]':
                    depth2 -= 1
                    if depth2 < 0:
                        break
                elif ch == ',' and depth2 == 0:
                    break
                elif depth2 == 0 and ch == '<' and ch_i == 0:
                    pass
                else:
                    expr += ch
                if depth2 < 0:
                    break
            lits = LIT.findall(expr)
            if not lits or not lits[0].startswith('/'):
                continue
            path = ''
            prev_end = 0
            for lm in LIT.finditer(expr):
                gap = expr[prev_end:lm.start()]
                if re.search(r'[A-Za-z_$]', gap) and path:
                    path += '{}'
                path += lm.group(1)
                prev_end = lm.end()
            tail = expr[prev_end:]
            if re.search(r'[A-Za-z_$]', tail) and not path.endswith('{}'):
                path = path.rstrip('/') + '/{}' if path.endswith('/') else path + '{}'
            tag = re.search(r'\{ method: "([A-Z]+)"', buf)
            method = tag.group(1) if tag else ('GET' if re.search(r'\b(get|fetch|apiUrl)\s*\(', line) else '?')
            if 'method: "POST"' in buf:
                method = 'POST'
            for mm in re.finditer(r'method:\s*"([A-Z]+)"', buf):
                method = mm.group(1)
            rows.append({'file': f.replace('\\', '/'), 'line': i + 1, 'method': method, 'path': path})

seen = set()
out = []
for r in rows:
    k = (r['path'], r['file'], r['line'])
    if k in seen:
        continue
    seen.add(k)
    out.append(r)

print(f"{'#':>3} {'METHOD':7s} {'FRONTEND PATH':52s} {'BACKEND':30s} RESULT  LOCATION")
miss = 0
for idx, r in enumerate(sorted(out, key=lambda x: (x['path'], x['file'])), 1):
    p = r['path']
    if p.startswith('/media'):
        res = 'STATIC'
        be = '-'
    else:
        cand = norm(p if p.startswith('/api') else '/api' + p)
        got = S.get(cand)
        if not got and cand.endswith('/{}'):
            cand2 = cand[:-3]
            got = S.get(cand2)
            if got:
                cand = cand2
        if not got:
            res, be = 'MISS', '-'
            miss += 1
        elif r['method'] in ('?', 'GET') and not any(g.startswith('GET') for g in got):
            res, be = 'METHOD?', ','.join(got)
        else:
            res, be = 'OK', ','.join(sorted({g.split('@')[0] for g in got}))
    print(f"{idx:3d} {r['method']:7s} {p:52s} {be:30s} {res:7s} {r['file']}:{r['line']}")
print()
print('TOTAL frontend call sites:', len(out), ' MISS:', miss)
