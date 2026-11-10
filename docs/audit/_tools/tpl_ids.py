import re, json, glob, os

src = open('frontend/src/lib/templates.ts', encoding='utf-8').read()
ids = re.findall(r'\bid:\s*"([A-Za-z0-9_]+)"', src)
print('frontend TEMPLATES ids:', len(ids))
print(ids)

# backend contract templates
print()
print('--- contract dirs ---')
for d in ['contract/v5/templates', 'contract/v6/workflows']:
    if os.path.isdir(d):
        fl = sorted(os.listdir(d))
        print(d, len(fl))
        print([os.path.splitext(f)[0] for f in fl])
