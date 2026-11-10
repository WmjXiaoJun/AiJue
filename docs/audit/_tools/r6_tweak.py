# -*- coding: utf-8 -*-
# 4 项导航默认展开（只有 4 项，标签比图标更清楚）；占位字母降噪
p = 'frontend/src/components/shell/AppShell.tsx'
s = open(p, encoding='utf-8').read()
old = '  return false;\n}'
new = '  return true;\n}'
assert s.count(old) == 1, s.count(old)
open(p, 'w', encoding='utf-8', newline='').write(s.replace(old, new))
print('rail default open')

p = 'frontend/src/styles/design.css'
s = open(p, encoding='utf-8').read()
old = '''.wall-letter {
  font-size: var(--f-28);
  color: var(--accent);
  opacity: 0.55;
}'''
new = '''.wall-letter {
  font-size: var(--f-20);
  color: var(--text-mute);
  opacity: 0.7;
}'''
assert s.count(old) == 1, s.count(old)
open(p, 'w', encoding='utf-8', newline='').write(s.replace(old, new))
print('letter toned down')
