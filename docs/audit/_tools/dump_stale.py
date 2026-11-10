import os, re

FILES = []
for root, dirs, names in os.walk('frontend/src'):
    for n in names:
        if n.endswith(('.ts', '.tsx')):
            FILES.append(os.path.join(root, n).replace('\\', '/'))
FILES.sort()

PAT = re.compile(r'尚未就绪|未就绪|实现中|暂不可用|尚未上线|未挂载')
out = []
for f in FILES:
    lines = open(f, encoding='utf-8').read().splitlines()
    for i, line in enumerate(lines, 1):
        if PAT.search(line):
            out.append(f'{f}:{i}: {line}')
open('docs/audit/_tools/stale.txt', 'w', encoding='utf-8').write('\n'.join(out) + '\n')
print('hits:', len(out))
