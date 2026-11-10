import re, json, glob, os

src = open('frontend/src/lib/templates.ts', encoding='utf-8').read()
# split top-level records by `\n  {\n`
recs = re.split(r'\n  \{(?=\n    "id")', src)
fe = {}
for r in recs[1:]:
    m = re.search(r'"id":\s*"([^"]+)"', r)
    if not m:
        continue
    tid = m.group(1)
    segs = len(re.findall(r'\n        "id":', r))
    dur = re.search(r'"target_duration":\s*([0-9.]+)', r)
    asp = re.search(r'"aspect":\s*"([^"]+)"', r)
    fe[tid] = {'segs': segs, 'dur': float(dur.group(1)) if dur else None, 'aspect': asp.group(1) if asp else None}

be = {}
for f in sorted(glob.glob('contract/formats/*.json')):
    d = json.load(open(f, encoding='utf-8'))
    be[d.get('id')] = {'segs': len(d.get('segments') or []), 'dur': d.get('target_duration'), 'aspect': d.get('aspect')}

print(f"{'id':26s} {'FE segs':>8s} {'BE segs':>8s} {'FE dur':>8s} {'BE dur':>8s} {'FE asp':>8s} {'BE asp':>8s}")
allids = sorted(set(fe) | set(be))
for i in allids:
    a = fe.get(i)
    b = be.get(i)
    flag = ''
    if a is None:
        flag = '  <== MISSING IN FRONTEND'
    elif b is None:
        flag = '  <== NOT IN BACKEND CONTRACT'
    elif a['segs'] != b['segs'] or a['dur'] != b['dur'] or a['aspect'] != b['aspect']:
        flag = '  <== DRIFT'
    print(f"{i:26s} {str(a['segs']) if a else '-':>8s} {str(b['segs']) if b else '-':>8s} "
          f"{str(a['dur']) if a else '-':>8s} {str(b['dur']) if b else '-':>8s} "
          f"{str(a['aspect']) if a else '-':>8s} {str(b['aspect']) if b else '-':>8s}{flag}")
