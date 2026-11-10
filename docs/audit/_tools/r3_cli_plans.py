import io, json, sys, contextlib
sys.path.insert(0, r"D:\BaiduSyncdisk\wmj_work\XJ\AiJue")
from pipeline.agents.cli import main

def run(argv):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        code = main(argv)
    txt = buf.getvalue()
    start = txt.find("{")
    d = json.loads(txt[start:]) if start >= 0 else {}
    return code, d

for tag, argv in {
    "A 短片 --stages publish --dry-run": ["run","--template","custom_tpl_v46_duologue","--stages","publish","--dry-run"],
    "B 短片 --stages muse --dry-run":    ["run","--template","custom_tpl_v46_duologue","--stages","muse","--dry-run"],
    "C V5  --stages qc --dry-run":       ["run","--template","tonight_show","--stages","qc","--dry-run"],
    "D V5  --stages story,asemble(拼错)": ["run","--template","tonight_show","--stages","story,asemble","--dry-run"],
}.items():
    try:
        code, d = run(argv)
        print(f"== {tag}\n   exit={code} status={d.get('status')} stages={[(s['id'],s['status']) for s in d.get('stages',[])]}")
    except SystemExit as e:
        print(f"== {tag}\n   SystemExit {e}")
