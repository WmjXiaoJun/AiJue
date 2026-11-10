# -*- coding: utf-8 -*-
import io

# 1) 去掉未使用的 Card 引入（Agent/Library 只用了 PageHeader + Stat）
for p in ['frontend/src/components/agent/AgentPage.tsx', 'frontend/src/components/library/LibraryPage.tsx']:
    s = open(p, encoding='utf-8').read()
    old = 'import { Card } from "../ui/Card";\n'
    assert s.count(old) == 1, (p, s.count(old))
    open(p, 'w', encoding='utf-8', newline='').write(s.replace(old, ''))
    print('card-import removed', p)

# 2) 发布包入口不再与成片库同亮
p = 'frontend/src/components/shell/AppShell.tsx'
s = open(p, encoding='utf-8').read()
old = '{ key: "publish", label: "发布包", href: "#/library/final", icon: <IconSend />, hint: "在成片卡上导出发布包 zip", match: ["final"] },'
new = '{ key: "publish", label: "发布包", href: "#/library/final", icon: <IconSend />, hint: "在成片卡上导出发布包 zip" },'
assert s.count(old) == 1
open(p, 'w', encoding='utf-8', newline='').write(s.replace(old, new))
print('publish match removed')

# 3) 卡片内次级按钮收一档（降低视觉噪声）
p = 'frontend/src/styles/design.css'
s = open(p, encoding='utf-8').read()
block = '''
/* ---------------------------------------- 11. 列表页密度（R5 · 追加）
   成片卡/模板卡里一行 7 个同级按钮太吵：次级按钮统一压到 28px 高、12px 字。 */
:is(.v20-final-card, .cm-tpl-card, .ag-tpl) .cm-btn {
  height: 28px;
  padding: 0 var(--s-3);
  font-size: var(--f-12);
  border-radius: var(--r-8);
}

:is(.v20-final-card, .cm-tpl-card) .cm-btn-primary {
  background: var(--accent);
  border-color: var(--accent);
  color: var(--accent-ink);
}

/* 卡片操作行之间留一点呼吸，避免糊成一片 */
:is(.v20-final-card, .cm-tpl-card) :is(.v20-final-actions, .cm-tpl-actions, .v20-card-actions) {
  gap: 6px;
}
'''
open(p, 'w', encoding='utf-8', newline='').write(s.rstrip() + '\n' + block)
print('css density appended')
