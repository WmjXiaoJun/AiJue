import re, json, glob, os

src = open('frontend/src/lib/templates.ts', encoding='utf-8').read()
# top-level ids: lines starting with 4 spaces + "id":
ids = re.findall(r'^    "id":\s*"([^"]+)"', src, re.M)
print('frontend TEMPLATES top-level ids:', len(ids))
print(ids)

# segments count per template
print()
print('--- contract/formats ---')
fi = []
for f in sorted(glob.glob('contract/formats/*.json')):
    try:
        d = json.load(open(f, encoding='utf-8'))
        fi.append((os.path.basename(f)[:-5], d.get('id'), d.get('name'), len(d.get('segments') or []), d.get('target_duration')))
    except Exception as e:
        fi.append((os.path.basename(f)[:-5], 'ERR', str(e), 0, 0))
for r in fi:
    print(r)

print()
print('--- per-template compare (id / segments / duration) ---')
# parse frontend blocks
blocks = re.split(r'\n  \{\n    "id": ', src)
fe = {}
for b in blocks[1:]:
    tid = b.split('"')[0]
    segs = len(re.findall(r'^      \{\n        "id":', b, re.M))
    if segs == 0:
        segs = len(re.findall(r'"duration":', b))
    dur = re.search(r'"target_duration":\s*([0-9.]+)', b)
    fe[tid] = (segs, float(dur.group(1)) if dur else None)
for tid, (segs, dur) in fe.items():
    print(f'{tid:28s} segs={segs:3d} dur={dur}')
