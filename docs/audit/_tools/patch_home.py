# -*- coding: utf-8 -*-
import io, sys

P = 'frontend/src/components/platform/PlatformHome.tsx'
src = open(P, encoding='utf-8').read()
seg = open('tmp/ui_r5/home_body.seg.tsx', encoding='utf-8').read()

def rep(old, new, count=1):
    global src
    n = src.count(old)
    assert n == count, f'expect {count} got {n}: {old[:70]!r}'
    src = src.replace(old, new)

# 1) imports
rep(
    'import { BrandMark } from "../icons";',
    'import { BrandMark, IconClock, IconSearch, IconTemplate } from "../icons";\n'
    'import { PageHeader } from "../ui/PageHeader";\n'
    'import { Card } from "../ui/Card";\n'
    'import { agentApi } from "../../lib/agent";\n'
    'import type { AgentTemplate } from "../../lib/agent";',
)

# 2) 模块级类型/常量（插在 Props 之前）
rep(
    'interface Props {',
    'type TplFilter = "all" | "base" | "custom";\n\n'
    'interface TplCard {\n'
    '  id: string;\n'
    '  label: string;\n'
    '  kind: "base" | "custom";\n'
    '  stack: string;\n'
    '  duration: number;\n'
    '  status: string;\n'
    '}\n\n'
    'const TPL_FILTERS: { id: TplFilter; label: string }[] = [\n'
    '  { id: "all", label: "全部" },\n'
    '  { id: "base", label: "综艺" },\n'
    '  { id: "custom", label: "短片" },\n'
    '];\n\n'
    'interface Props {',
)

# 3) state
rep(
    '  const [newOpen, setNewOpen] = useState(false);',
    '  const [newOpen, setNewOpen] = useState(false);\n'
    '  const [overview, setOverview] = useState<AgentTemplate[]>([]);\n'
    '  const [query, setQuery] = useState("");\n'
    '  const [tplFilter, setTplFilter] = useState<TplFilter>("all");',
)

# 4) load(): 追加 overview（33 模板）
rep(
    '    try {\n      setTpl(await cinemaApi.templates());\n    } catch {\n      setTpl(null);\n    }',
    '    try {\n      setTpl(await cinemaApi.templates());\n    } catch {\n      setTpl(null);\n    }\n'
    '    try {\n      setOverview((await agentApi.overview()).templates);\n    } catch {\n      setOverview([]);\n    }',
)

# 5) 派生数据（插在 return 之前）
rep(
    '  const activeJobs = jobs.filter(isActiveJob).length;\n\n  return (',
    '  const activeJobs = jobs.filter(isActiveJob).length;\n\n'
    '  const mergedTemplates = useMemo<TplCard[]>(() => {\n'
    '    const statusOf = new Map<string, string>();\n'
    '    (tpl?.templates || []).forEach((item) => statusOf.set(item.id, item.status || ""));\n'
    '    if (overview.length) {\n'
    '      return overview.map((item) => ({\n'
    '        id: item.id,\n'
    '        label: item.label || item.id,\n'
    '        kind: item.kind === "custom" ? ("custom" as const) : ("base" as const),\n'
    '        stack: item.stack || "",\n'
    '        duration: Number(item.duration) || 0,\n'
    '        status: statusOf.get(item.id) || "",\n'
    '      }));\n'
    '    }\n'
    '    return (tpl?.templates || []).map((item) => ({\n'
    '      id: item.id,\n'
    '      label: item.label || item.id,\n'
    '      kind: item.id.startsWith("custom_") ? ("custom" as const) : ("base" as const),\n'
    '      stack: "",\n'
    '      duration: Number(item.final?.duration) || 0,\n'
    '      status: item.status || "",\n'
    '    }));\n'
    '  }, [overview, tpl]);\n\n'
    '  const templateCount = mergedTemplates.length || TEMPLATES.length;\n'
    '  const readyCount = mergedTemplates.filter((item) => item.status === "done").length;\n'
    '  const filteredTemplates = useMemo(() => {\n'
    '    const q = query.trim().toLowerCase();\n'
    '    return mergedTemplates.filter((item) => {\n'
    '      if (tplFilter !== "all" && item.kind !== tplFilter) return false;\n'
    '      if (!q) return true;\n'
    '      return item.id.toLowerCase().includes(q) || item.label.toLowerCase().includes(q);\n'
    '    });\n'
    '  }, [mergedTemplates, query, tplFilter]);\n'
    '  const finalCards = useMemo(\n'
    '    () =>\n'
    '      (tpl?.templates || [])\n'
    '        .filter((item) => Boolean(item.final?.url))\n'
    '        .slice(0, 4)\n'
    '        .map((item) => ({\n'
    '          id: item.id,\n'
    '          label: item.label || item.id,\n'
    '          poster: item.poster_url || "",\n'
    '          duration: Number(item.final?.duration) || 0,\n'
    '        })),\n'
    '    [tpl],\n'
    '  );\n\n  return (',
)

# 6) 替换 body
start = src.find('  return (\n    <AppShell')
end = src.find('\n  async function createEpisodeFor')
assert start > 0 and end > start, (start, end)
src = src[:start] + seg + src[end:]

open(P, 'w', encoding='utf-8', newline='').write(src)
print('patched PlatformHome.tsx', len(src))
