# -*- coding: utf-8 -*-
# 1) CSS: StatRow 在 flex meta 里要占满宽度；rail foot 加底色避免与导航重叠
p = 'frontend/src/styles/design.css'
s = open(p, encoding='utf-8').read()
assert '.ui-page-meta {\n  display: flex;' in s
s = s.replace(
    '.ui-page-meta {\n  display: flex;\n  flex-wrap: wrap;\n  gap: var(--s-2);\n  margin-top: var(--s-3);\n}',
    '.ui-page-meta {\n  display: flex;\n  flex-wrap: wrap;\n  gap: var(--s-3);\n  margin-top: var(--s-3);\n}\n\n'
    '.ui-page-meta > .ui-stat-row {\n  width: 100%;\n  max-width: 720px;\n}\n\n'
    '.ui-page-meta .ui-stat {\n  min-width: 96px;\n}',
    1,
)
s = s.replace(
    '.sh-rail-foot {\n  display: flex;\n  flex-direction: column;\n  gap: var(--s-2);\n  padding-top: var(--s-3);\n  border-top: 1px solid var(--line);\n}',
    '.sh-rail-foot {\n  display: flex;\n  flex-direction: column;\n  gap: var(--s-2);\n  padding: var(--s-3) 0 var(--s-1);\n  border-top: 1px solid var(--line);\n  background: var(--surface);\n  position: sticky;\n  bottom: 0;\n}',
    1,
)
open(p, 'w', encoding='utf-8', newline='').write(s)
print('css ok')

# 2) 默认展开阈值 1280 → 1366（1280 下先给内容留宽度）
p = 'frontend/src/components/shell/AppShell.tsx'
s = open(p, encoding='utf-8').read()
old = 'return typeof window !== "undefined" ? window.innerWidth >= 1280 : true;'
new = 'return typeof window !== "undefined" ? window.innerWidth >= 1366 : true;'
assert s.count(old) == 1
open(p, 'w', encoding='utf-8', newline='').write(s.replace(old, new))
print('threshold ok')
