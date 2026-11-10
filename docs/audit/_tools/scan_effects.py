import re, glob

def scan(path):
    src = open(path, encoding='utf-8').read()
    lines = src.splitlines()
    out = []
    for m in re.finditer(r'\buseEffect\s*\(', src):
        i = m.end() - 1  # at '('
        depth = 0
        j = i
        commas = []
        while j < len(src):
            ch = src[j]
            if ch in '([{':
                depth += 1
            elif ch in ')]}':
                depth -= 1
                if depth == 0:
                    break
            elif ch == ',' and depth == 1:
                commas.append(j)
            j += 1
        end = j
        startline = src[:m.start()].count('\n') + 1
        endline = src[:end].count('\n') + 1
        body = src[m.start():end]
        has_deps = len(commas) >= 1
        dep_txt = ''
        if has_deps:
            dep_txt = src[commas[-1] + 1: end].strip().replace('\n', ' ')
        n_lines = endline - startline + 1
        out.append((path.replace('\\', '/'), startline, n_lines, has_deps, dep_txt[:150]))
    return out

rows = []
for f in sorted(glob.glob('frontend/src/**/*.tsx', recursive=True) + glob.glob('frontend/src/**/*.ts', recursive=True)):
    rows.extend(scan(f))

print("=== useEffect WITHOUT dependency array (runs every render) ===")
for r in rows:
    if not r[3]:
        print(f"{r[0]}:{r[1]}  ({r[2]} lines)")

print()
print("=== useEffect whose deps contain objects/arrays literals or inline arrows ===")
for r in rows:
    d = r[4]
    if r[3] and (('{ ' in d) or ('=>' in d) or ('[' in d and 'key' not in d)):
        print(f"{r[0]}:{r[1]}  deps=[{d}]")

print()
print("total useEffect:", len(rows))
