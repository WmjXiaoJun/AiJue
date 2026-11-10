import re, glob

files = ['backend/app/main.py'] + sorted(glob.glob('backend/app/routers/*.py'))
print("---- APIRouter prefixes ----")
for f in files:
    src = open(f, encoding='utf-8').read()
    for m in re.finditer(r'APIRouter\(([^)]*)\)', src, re.S):
        print(f.replace('\\', '/'), '::', ' '.join(m.group(1).split()))
print()
print("---- main.py router include order ----")
main = open('backend/app/main.py', encoding='utf-8').read()
i = main.find('include_router')
print(main[max(0, i-800): i+1600])
