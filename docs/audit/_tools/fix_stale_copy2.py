# -*- coding: utf-8 -*-
import sys

R = [
 ('frontend/src/lib/pipeline.ts',
  '/** 404/405 → 后端接口尚未就绪（友好提示用） */',
  '/** 404/405/501 → 接口不可用（友好提示用） */', 1),
 ('frontend/src/lib/agent.ts',
  '/** 404/405/501 → 接口不可用（季接口已上线；仍 404 多为资源不存在或未挂载） */',
  '/** 404/405/501 → 接口不可用（季接口已上线；仍 404 多为系列/资源 id 不存在） */', 1),
]
errs = []
for p, old, new, n in R:
    s = open(p, encoding='utf-8').read()
    if s.count(old) != n:
        errs.append(f'{p}: expect {n} got {s.count(old)}')
if errs:
    print('ABORT', errs); sys.exit(1)
for p, old, new, n in R:
    s = open(p, encoding='utf-8').read()
    open(p, 'w', encoding='utf-8', newline='').write(s.replace(old, new))
print('OK', len(R))
