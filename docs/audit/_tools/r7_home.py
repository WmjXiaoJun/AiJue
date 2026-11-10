# -*- coding: utf-8 -*-
"""R7：把 git HEAD 的 PlatformHome 恢复成「项目大厅」，并去掉模板 3D 面板（模板另有页面）。"""
import io, re

legacy = open('tmp/ui_r5/PlatformHome.legacy.tsx', encoding='utf-8-sig').read()

# 1) 找模板库面板并整块删除（模板墙已迁到 #/library/templates）
anchor = '{tplTotal} 模板 · 3D 影库'
i = legacy.find(anchor)
assert i > 0, 'panel anchor not found'
start = legacy.rfind('<section className="pl-panel">', 0, i)
assert start > 0, 'panel start not found'

# 从 start 起做 <section> 深度匹配
depth = 0
j = start
while j < len(legacy):
    nxt_open = legacy.find('<section', j)
    nxt_close = legacy.find('</section>', j)
    if nxt_close < 0:
        raise SystemExit('unbalanced')
    if 0 <= nxt_open < nxt_close:
        depth += 1
        j = nxt_open + len('<section')
    else:
        depth -= 1
        j = nxt_close + len('</section>')
        if depth == 0:
            break
end = j
panel = legacy[start:end]
assert '3D 影库' in panel and len(panel) < 4000, len(panel)
out = legacy[:start] + legacy[end:]

# 2) 不再需要 3D 模板数据：去掉 fetch 与相关派生
out = out.replace(
    '    try {\n      setTpl(await cinemaApi.templates());\n    } catch {\n      setTpl(null);\n    }\n',
    '',
    1,
)
out = out.replace('  const [tpl, setTpl] = useState<CinemaTemplatesResponse | null>(null);\n', '', 1)
out = re.sub(r'  const tplSummary = tpl\?\.summary;\n  const tplDone = tplSummary\?\.done \?\? 0;\n'
             r'  // V17：3D 模板总数动态（16 → 19\+）；接口失败时保留原 16 fallback\n'
             r'  const tplTotal = tplSummary\?\.total \?\? 16;\n', '', out, count=1)

# 3) chips 去掉「3D 模板」一项
out = re.sub(r'\n\s*\{ label: "3D 模板", value: `\$\{tplDone\}/\$\{tplTotal\}`, tone: "gold" \},', '', out, count=1)

# 4) 顶部文案：项目大厅（续做 / 新建 / 最近任务）
out = out.replace('kicker="PRODUCTION BOARD · 造片局"', 'kicker="造片局"', 1)
out = out.replace('title="项目大厅"', 'title="项目大厅"', 1)

# 5) 清理不再使用的 import（cinema 相关）
out = out.replace('import { cinemaApi, isRealTemplate } from "../../lib/cinema";\n', '', 1)
out = out.replace('import type { CinemaTemplate, CinemaTemplatesResponse } from "../../lib/cinema";\n', '', 1)
out = out.replace('import { TEMPLATES, templateLabel } from "../../lib/templates";\n',
                  'import { TEMPLATES, templateLabel } from "../../lib/templates";\n', 1)

open('frontend/src/components/platform/PlatformHome.tsx', 'w', encoding='utf-8', newline='').write(out)
print('project hall written', len(out), 'panel removed', len(panel))
print('has tplTotal:', 'tplTotal' in out, ' has cinemaApi:', 'cinemaApi' in out)
