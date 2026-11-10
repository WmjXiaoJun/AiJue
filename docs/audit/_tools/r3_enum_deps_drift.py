import json, sys
from pathlib import Path
sys.path.insert(0,'.')
from pipeline.agents.workflow import SHORTS_STAGES, DEFAULT_STAGES, load_workflow, is_shorts
from pipeline import v5_catalog
cat = v5_catalog.load_catalog()
tpls = cat['templates']

print("=== enabled stage depending on disabled/missing-enabled dependency ===")
for p in sorted(Path('contract/v6/workflows').glob('*.json')):
    d = json.loads(p.read_text(encoding='utf-8'))
    st = {s['id']: s for s in d.get('stages') or []}
    for s in d.get('stages') or []:
        if not s.get('enabled', True): continue
        for dep in s.get('depends') or []:
            tgt = st.get(dep)
            if tgt is not None and not tgt.get('enabled', True):
                print(f"  {p.stem}: enabled '{s['id']}' depends on DISABLED '{dep}'")
print("(none above = ok)")

print("\n=== drift: JSON stages vs current code defaults ===")
def sig(stages):
    return [(s['id'], s.get('tool'), s.get('agent'), tuple(s.get('depends') or []), s.get('enabled', True), json.dumps(s.get('params') or {}, sort_keys=True)) for s in stages]
for p in sorted(Path('contract/v6/workflows').glob('*.json')):
    d = json.loads(p.read_text(encoding='utf-8'))
    tid = p.stem
    expect = SHORTS_STAGES if is_shorts(tid, tpls.get(tid)) else DEFAULT_STAGES
    a, b = sig(d.get('stages') or []), sig(expect)
    if a != b:
        print(f"  DRIFT {tid} (stack={d.get('stack')})")
        for x, y in zip(a, b):
            if x != y:
                print("    json:", x)
                print("    code:", y)
        if len(a) != len(b):
            print("    len json/code:", len(a), len(b), [s[0] for s in a], [s[0] for s in b])
print("(no DRIFT lines = code and contract in sync)")

print("\n=== muse stage params per stack ===")
for p in sorted(Path('contract/v6/workflows').glob('*.json')):
    d = json.loads(p.read_text(encoding='utf-8'))
    m = [s for s in d.get('stages') or [] if s['id']=='muse']
    if m: print(f"  {p.stem:36s} {d.get('stack'):10s} enabled={m[0].get('enabled')} params={m[0].get('params')}")
