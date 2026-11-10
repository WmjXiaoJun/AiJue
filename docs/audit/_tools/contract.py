import re, glob

files = ['backend/app/main.py'] + sorted(glob.glob('backend/app/routers/*.py'))
prefpat = re.compile(r'APIRouter\(([^)]*)\)', re.S)
pat = re.compile(r'@(?:app|router|r)\.(get|post|put|patch|delete)\(\s*["\']([^"\']*)["\']')
BE = []
for f in files:
    src = open(f, encoding='utf-8').read()
    prefix = ''
    m = prefpat.search(src)
    if m:
        pm = re.search(r'prefix\s*=\s*["\']([^"\']*)["\']', m.group(1))
        if pm:
            prefix = pm.group(1)
    if f.endswith('main.py'):
        prefix = '/api'
    for i, line in enumerate(src.splitlines(), 1):
        mm = pat.search(line)
        if mm:
            p = mm.group(2)
            if not p.startswith('/api') and not p.startswith('/media'):
                p = prefix + p
            BE.append((mm.group(1).upper(), p or '/', f.replace('\\', '/'), i))

def norm(p):
    p = re.sub(r'\{[^}]*\}', '{}', p)
    p = re.sub(r'<[^>]*>', '{}', p)
    return p.rstrip('/') or '/'

S = {}
for m, p, f, l in BE:
    S.setdefault(norm(p), []).append(m)

FRONT = [
 ('GET',  '/api/health'), ('GET', '/api/projects'), ('POST', '/api/projects'),
 ('GET', '/api/projects/{}'), ('GET', '/api/projects/{}/episodes'), ('POST', '/api/projects/{}/episodes'),
 ('GET', '/api/episodes/{}'), ('PATCH', '/api/episodes/{}'),
 ('GET', '/api/episodes/{}/assets'), ('POST', '/api/episodes/{}/assets'), ('PATCH', '/api/assets/{}'),
 ('GET', '/api/episodes/{}/shots'), ('POST', '/api/episodes/{}/shots'),
 ('DELETE', '/api/shots/{}'), ('PATCH', '/api/shots/{}'),
 ('POST', '/api/shots/{}/generate'),
 ('POST', '/api/episodes/{}/shots/generate-dynamic'),
 ('GET', '/api/shots/{}/versions'), ('POST', '/api/shots/{}/versions/{}/select'),
 ('POST', '/api/episodes/{}/storyboard/generate'), ('POST', '/api/episodes/{}/render'),
 ('GET', '/api/jobs'), ('GET', '/api/jobs/{}'), ('POST', '/api/jobs/{}/retry'),
 ('GET', '/api/episodes/{}/costs'),
 ('GET', '/api/projects/{}/characters'), ('POST', '/api/projects/{}/characters'),
 ('GET', '/api/characters/{}'), ('PATCH', '/api/characters/{}'),
 ('POST', '/api/characters/{}/generate'), ('GET', '/api/characters/{}/files'),
 ('GET', '/api/characters/{}/concepts'), ('POST', '/api/characters/{}/concept/generate'),
 ('POST', '/api/characters/{}/portrait/from-concept'), ('POST', '/api/characters/{}/h3-animate'),
 ('POST', '/api/episodes/{}/export'), ('POST', '/api/episodes/{}/localize'),
 ('GET', '/api/episodes/{}/localizations'), ('GET', '/api/localizations/{}'),
 ('GET', '/api/publish/config'), ('POST', '/api/episodes/{}/publish'),
 ('GET', '/api/episodes/{}/publishes'), ('GET', '/api/publishes/{}'),
 ('GET', '/api/llm/config'), ('GET', '/api/llm/providers'), ('POST', '/api/llm/test'),
 ('POST', '/api/projects/{}/generate-script'), ('GET', '/api/settings'), ('PATCH', '/api/settings'),
 ('GET', '/api/episodes/{}/publish/preflight'),
 # cinema
 ('GET', '/api/cinema/overview'), ('GET', '/api/cinema/shots'), ('PATCH', '/api/cinema/shots/{}'),
 ('POST', '/api/cinema/generate'), ('POST', '/api/cinema/assemble'),
 ('GET', '/api/cinema/jobs'), ('GET', '/api/cinema/jobs/{}'), ('POST', '/api/cinema/jobs/{}/cancel'),
 ('GET', '/api/cinema/report'), ('GET', '/api/cinema/workers'),
 ('GET', '/api/cinema/templates'), ('GET', '/api/cinema/templates/{}'),
 ('POST', '/api/cinema/templates/generate'), ('POST', '/api/cinema/templates/assemble'),
 ('POST', '/api/cinema/templates/{}/publish-package'), ('GET', '/api/cinema/templates/{}/publish-package'),
 ('POST', '/api/cinema/publish-packages/batch'), ('GET', '/api/cinema/publish-packages/batch/{}'),
 ('POST', '/api/cinema/publish-packages/batch/{}/cancel'), ('GET', '/api/cinema/publish-packages/batches'),
 # v5
 ('POST', '/api/v5/characters'), ('POST', '/api/v5/characters/{}'), ('DELETE', '/api/v5/characters/{}'),
 ('POST', '/api/v5/characters/dialogue'), ('POST', '/api/v5/characters/{}/audition'),
 ('GET', '/api/auditions/{}'), ('POST', '/api/auditions/{}/cancel'),
 ('GET', '/api/v5/characters/{}/auditions'), ('POST', '/api/v5/characters/{}/promo'),
 ('GET', '/api/promos/{}'), ('POST', '/api/promos/{}/cancel'), ('GET', '/api/v5/characters/{}/promos'),
 ('GET', '/api/v5/dialogues'), ('GET', '/api/v5/dialogues/{}'), ('POST', '/api/v5/dialogues/{}/cancel'),
 ('POST', '/api/v5/character-showcase/split-packages'), ('GET', '/api/v5/character-showcase/split-packages'),
 ('POST', '/api/v5/character-showcase/publish-package'), ('GET', '/api/v5/character-showcase/publish-package'),
 ('POST', '/api/v5/characters/{}/art'), ('GET', '/api/v5/characters/{}/assets'),
 ('POST', '/api/v5/templates'), ('DELETE', '/api/v5/templates/{}'),
 ('GET', '/api/v5/outputs'), ('DELETE', '/api/v5/outputs/{}'), ('GET', '/api/v5/catalog'),
 ('GET', '/api/v5/{}/{}'), ('PUT', '/api/v5/{}/{}'), ('GET', '/api/v5/series'),
 ('GET', '/api/v5/series/{}'), ('POST', '/api/v5/series/{}/export'),
 ('GET', '/api/v5/voices'), ('GET', '/api/v5/casting'),
 ('GET', '/api/v5/voice/{}'), ('PUT', '/api/v5/voice/{}'),
 ('POST', '/api/v5/voice/{}/preview'), ('POST', '/api/v5/voice/{}/lock'),
 ('GET', '/api/v5/render'), ('POST', '/api/v5/render'), ('GET', '/api/v5/render/{}'), ('DELETE', '/api/v5/render/{}'),
 ('GET', '/api/v5/editorial/{}'), ('GET', '/api/v5/storyboard/{}'),
 ('GET', '/api/v5/preview/{}/{}'),
 ('GET', '/api/v5/longform/status'), ('POST', '/api/v5/longform/run'),
 # pipeline
 ('POST', '/api/pipeline/run'), ('GET', '/api/pipeline/run/{}'), ('POST', '/api/pipeline/run/{}/cancel'),
 ('GET', '/api/pipeline/runs'), ('POST', '/api/pipeline/batch'), ('GET', '/api/pipeline/batch/{}'),
 ('POST', '/api/pipeline/batch/{}/cancel'), ('GET', '/api/pipeline/batches'),
 # v6 agent / muse
 ('GET', '/api/v6/agent/overview'), ('GET', '/api/v6/agent/workflow/{}'), ('PUT', '/api/v6/agent/workflow/{}'),
 ('POST', '/api/v6/agent/run'), ('GET', '/api/v6/agent/runs'), ('GET', '/api/v6/agent/runs/{}'),
 ('POST', '/api/v6/agent/edit'), ('GET', '/api/v6/agent/art'), ('GET', '/api/v6/agent/series'),
 ('POST', '/api/v6/agent/season/{}/run'), ('GET', '/api/v6/agent/season/{}/status'),
 ('GET', '/api/v6/muse/status'), ('POST', '/api/v6/muse/sync'), ('GET', '/api/v6/muse/jobs/{}'),
 ('GET', '/api/v6/muse/video/status'), ('POST', '/api/v6/muse/video'), ('GET', '/api/v6/muse/video/jobs/{}'),
]

print(f"{'METHOD':7s} {'PATH':48s} BACKEND")
for m, p in FRONT:
    k = norm(p)
    got = S.get(k)
    if got is None:
        print(f"MISS    {p:48s} NOT FOUND")
    elif m not in got:
        print(f"M-MISMATCH {p:43s} backend has {got} (frontend uses {m})")
    else:
        print(f"OK      {p:48s} {got}")
