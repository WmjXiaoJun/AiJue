import re, glob

files = sorted(glob.glob('frontend/src/**/*.ts', recursive=True) + glob.glob('frontend/src/**/*.tsx', recursive=True))
tot_si = tot_sic = tot_st = 0
rows = []
for f in files:
    src = open(f, encoding='utf-8').read()
    si = len(re.findall(r'\bsetInterval\b', src))
    sic = len(re.findall(r'\bclearInterval\b', src))
    st = len(re.findall(r'\bsetTimeout\b', src))
    stc = len(re.findall(r'\bclearTimeout\b', src))
    fe = len(re.findall(r'\bfetch\s*\(', src))
    ok = len(re.findall(r'res\.ok|r\.ok|ok\s*\?', src))
    tot_si += si
    tot_sic += sic
    tot_st += st
    if si or st or fe:
        rows.append((f.replace('\\', '/'), si, sic, st, stc, fe, ok))
for r in rows:
    print(f"{r[0]:58s} setInterval={r[1]:2d} clearInterval={r[2]:2d} setTimeout={r[3]:2d} clearTimeout={r[4]:2d} fetch={r[5]} okRef={r[6]}")
print()
print('TOTAL setInterval =', tot_si, ' clearInterval =', tot_sic, ' setTimeout =', tot_st)
# gap check: files where setInterval > clearInterval
print()
print('files with setInterval but no clearInterval:')
for r in rows:
    if r[1] and not r[2]:
        print('  ', r[0])
