import re, glob, sys

files = ['backend/app/main.py'] + sorted(glob.glob('backend/app/routers/*.py'))
pat = re.compile(r'@(?:app|router|r)\.(get|post|put|patch|delete)\(\s*["\']([^"\']+)["\']')
routes = []
for f in files:
    src = open(f, encoding='utf-8').read().splitlines()
    for i, l in enumerate(src, 1):
        m = pat.search(l)
        if m:
            routes.append((m.group(2), m.group(1).upper(), f.replace('\\', '/'), i))

print("TOTAL", len(routes))
main = open('backend/app/main.py', encoding='utf-8').read()
for m in re.finditer(r'include_router\(\s*\n?\s*([\w\.]+)', main):
    pass
print("---- included routers ----")
for m in re.finditer(r'include_router\([^)]*\)', main, re.S):
    print(' '.join(m.group(0).split()))

print("---- ROUTES ----")
for r in sorted(set(routes), key=lambda x: (x[0], x[1])):
    print(r[1], r[0], r[2] + ':' + str(r[3]))
