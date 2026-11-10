# -*- coding: utf-8 -*-
"""R6 减法：装入 4 项导航的 AppShell + 模板墙首页；AgentPage 读 localStorage 选中模板；
   追加极简样式（无渐变/无阴影/1px 线/8px 圆角）。"""
import io, shutil

def install(src, dst):
    s = open(src, encoding='utf-8').read()
    open(dst, 'w', encoding='utf-8', newline='').write(s)
    print('installed', dst, len(s))

install('tmp/ui_r5/AppShell.r6.tsx', 'frontend/src/components/shell/AppShell.tsx')
install('tmp/ui_r5/PlatformHome.r6.tsx', 'frontend/src/components/platform/PlatformHome.tsx')

# AgentPage：初始选中模板从 localStorage 读（首页点卡片写进去）
p = 'frontend/src/components/agent/AgentPage.tsx'
s = open(p, encoding='utf-8').read()
old = '  const [selectedId, setSelectedId] = useState<string>("");'
new = ('  // R6：首页模板墙点卡片会写 localStorage，这里作为初始选中\n'
       '  const [selectedId, setSelectedId] = useState<string>(() => {\n'
       '    try {\n'
       '      return localStorage.getItem("zpj_current_template") || "";\n'
       '    } catch {\n'
       '      return "";\n'
       '    }\n'
       '  });')
assert s.count(old) == 1, s.count(old)
open(p, 'w', encoding='utf-8', newline='').write(s.replace(old, new))
print('AgentPage initial template ok')
