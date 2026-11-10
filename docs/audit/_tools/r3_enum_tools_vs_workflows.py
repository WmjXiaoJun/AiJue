import json, glob, os, collections
from pathlib import Path
import sys
sys.path.insert(0, '.')
from pipeline.agents.registry import TOOLS
from pipeline.agents.workflow import SHORTS_STAGES, DEFAULT_STAGES, is_shorts

wfdir = Path('contract/v6/workflows')
files = sorted(wfdir.glob('*.json'))
print("workflow files:", len(files))
tools_registered = set(TOOLS)
used = collections.Counter()
bad = []
dep_bad = []
cyc = []
for f in files:
    d = json.loads(f.read_text(encoding='utf-8'))
    stages = d.get('stages') or []
    ids = [s.get('id') for s in stages]
    # duplicate ids
    if len(set(ids)) != len(ids):
        print("DUP ID", f.name, ids)
    for s in stages:
        t = s.get('tool')
        used[t] += 1
        if t not in tools_registered:
            bad.append((f.name, s.get('id'), t))
        for dep in (s.get('depends') or []):
            if dep not in ids:
                dep_bad.append((f.name, s.get('id'), dep))
    # cycle detection
    depmap = {s.get('id'): list(s.get('depends') or []) for s in stages}
    state = {}
    def dfs(n, path):
        if state.get(n) == 1:
            cyc.append((f.name, path + [n]))
            return
        if state.get(n) == 2: return
        state[n] = 1
        for m in depmap.get(n, []):
            dfs(m, path + [n])
        state[n] = 2
    for n in depmap: dfs(n, [])
print("\n-- tools in JSON not in TOOLS --")
for b in bad: print("  ", b)
print("count:", len(bad))
print("\n-- depends on nonexistent stage --")
for b in dep_bad: print("  ", b)
print("count:", len(dep_bad))
print("\n-- cycles --")
for c in cyc[:10]: print("  ", c)
print("count:", len(cyc))
print("\n-- tool usage count across 33 workflows --")
for t, n in used.most_common():
    mark = "" if t in tools_registered else "  <== MISSING"
    print(f"  {t:24s} {n:4d}{mark}")
print("\n-- registered but never used in any workflow JSON --")
for t in sorted(tools_registered):
    if used.get(t, 0) == 0:
        print("  ", t)
print("\n-- stage id sets --")
sigs = collections.Counter()
for f in files:
    d = json.loads(f.read_text(encoding='utf-8'))
    sigs[(d.get('stack'), tuple(s.get('id') for s in d.get('stages') or []))] += 1
for k, v in sigs.items():
    print("  ", v, k)
