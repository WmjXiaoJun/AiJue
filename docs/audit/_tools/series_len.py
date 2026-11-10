import json, glob, os

for f in sorted(glob.glob('contract/v5/series/*.json')):
    try:
        d = json.load(open(f, encoding='utf-8'))
    except Exception as e:
        print(f, 'ERR', e)
        continue
    sid = d.get('id', '')
    rid = 'season_' + sid + '_20261106_232820'
    limit = 40
    print(f"{sid:28s} sid_len={len(sid):2d} run_id_len={len(rid):2d} {'RISK >40 -> media 404' if len(rid) > limit else 'ok'}")
print()
print('threshold: 7 + len(sid) + 1 + 15 <= 40  =>  len(sid) <= 17')
