import re, glob, os

files = sorted(glob.glob('frontend/src/**/*.ts', recursive=True) + glob.glob('frontend/src/**/*.tsx', recursive=True))
rows = []
for f in files:
    src = open(f, encoding='utf-8').read()
    n_any = len(re.findall(r':\s*any\b|<any>|as any\b|\bany\[\]', src))
    n_as = len(re.findall(r'\bas\s+[A-Z]', src))
    n_unk = len(re.findall(r'as unknown as', src))
    n_bang = len(re.findall(r'\w!\.', src))
    n_catch = len(re.findall(r'\}\s*catch\s*(\([^)]*\))?\s*\{\s*\}', src))
    n_catch2 = len(re.findall(r'catch\s*\{\s*(/\*.*?\*/\s*)?\}', src))
    n_anyval = len(re.findall(r'catch\s*\(\s*\w+\s*:\s*any\s*\)', src))
    if n_any or n_unk or n_bang:
        rows.append((f.replace('\\','/'), n_any, n_as, n_unk, n_bang, n_catch2))
rows.sort(key=lambda r: -(r[1]*3 + r[3]*4 + r[4]))
print(f"{'file':58s} {'any':>4s} {'as T':>5s} {'unkAs':>6s} {'x!.':>5s} {'catch{}':>8s}")
for r in rows:
    print(f"{r[0]:58s} {r[1]:4d} {r[2]:5d} {r[3]:6d} {r[4]:5d} {r[5]:8d}")
print()
print("TOTAL any:", sum(r[1] for r in rows), "unknownAs:", sum(r[3] for r in rows), "bang:", sum(r[4] for r in rows))
