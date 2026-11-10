# -*- coding: utf-8 -*-
p = 'frontend/src/components/platform/PlatformHome.tsx'
s = open(p, encoding='utf-8').read()

pairs = [
    # 页头动作降为次级，避免和顶栏主按钮两个琥珀色 CTA 抢焦点
    ('<button type="button" className="ui-btn is-primary" onClick={() => onOpenProject(heroPick.project)}>\n                继续生产',
     '<button type="button" className="ui-btn" onClick={() => onOpenProject(heroPick.project)}>\n                继续生产'),
    ('<button type="button" className="ui-btn is-primary" onClick={() => setNewOpen(true)}>\n                新建第一个项目',
     '<button type="button" className="ui-btn" onClick={() => setNewOpen(true)}>\n                新建第一个项目'),
    # "已出片" 指 3D 样片，避免与「成片 0」混淆
    ('<span className="ui-chip is-ok">{readyCount} 支已出片</span>',
     '<span className="ui-chip is-ok">{readyCount} 个有样片</span>'),
    ('<span className="ui-chip is-ok">已出片</span>',
     '<span className="ui-chip is-ok">有样片</span>'),
]
for old, new in pairs:
    n = s.count(old)
    assert n == 1, (n, old[:60])
    s = s.replace(old, new)
open(p, 'w', encoding='utf-8', newline='').write(s)
print('ok')
