import json, sys
from pathlib import Path
sys.path.insert(0,'.')
from pipeline.agents.workflow import load_workflow, is_shorts
from pipeline import v5_catalog
cat = v5_catalog.load_catalog()
tpls = cat.get('templates') or {}
print("catalog templates:", len(tpls))
wfdir = Path('contract/v6/workflows')
wfs = {p.stem for p in wfdir.glob('*.json')}
print("\n%-38s %-10s %-12s %-11s %s" % ("template","style","render_style","wf_stack","is_shorts"))
mismatch=[]
for tid in sorted(tpls):
    t=tpls[tid]
    wf = load_workflow(tid, t)
    rs = str(t.get('render_style') or '')
    iss = is_shorts(tid, t)
    st = wf.get('stack')
    exp = 'hq_shorts' if iss else 'v5_render'
    flag = '' if st==exp else '  <== STACK MISMATCH'
    print("%-38s %-10s %-12s %-11s %-5s%s" % (tid, t.get('style'), rs or '-', st, iss, flag))
    if st!=exp: mismatch.append(tid)
print("\ntemplates without workflow file:", sorted(set(tpls)-wfs))
print("workflow files without template:", sorted(wfs-set(tpls)))
print("stack mismatch count:", len(mismatch), mismatch)
