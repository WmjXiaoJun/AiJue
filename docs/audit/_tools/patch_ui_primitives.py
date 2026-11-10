# -*- coding: utf-8 -*-
"""把 R5 的 PageHeader / Card / Stat 接进 AgentPage 与 LibraryPage（只加不改旧结构）。"""
import io

def patch(path, pairs):
    s = open(path, encoding='utf-8').read()
    for old, new, n in pairs:
        c = s.count(old)
        assert c == n, f'{path}: expect {n} got {c} for {old[:60]!r}'
        s = s.replace(old, new)
    open(path, 'w', encoding='utf-8', newline='').write(s)
    print('patched', path)

# ---------------- AgentPage ----------------
A = 'frontend/src/components/agent/AgentPage.tsx'
patch(A, [
    (
        'import { AppShell } from "../shell/AppShell";',
        'import { AppShell } from "../shell/AppShell";\n'
        'import { PageHeader } from "../ui/PageHeader";\n'
        'import { Card } from "../ui/Card";\n'
        'import { Stat, StatRow } from "../ui/Stat";',
        1,
    ),
    (
        '        )}\n\n        {tab === "workflow" ? (',
        '        )}\n\n'
        '        <PageHeader\n'
        '          title={tab === "workflow" ? "单集工作流" : "整季生产"}\n'
        '          subtitle={\n'
        '            tab === "workflow"\n'
        '              ? "选一个模板 → 勾阶段 → 跑一集；日志与产物在右栏。"\n'
        '              : "一个世界一季：串行跑多集，只补缺失的集。"\n'
        '          }\n'
        '          meta={\n'
        '            <StatRow>\n'
        '              <Stat label="模板" value={templates.length} hint="综艺 + 短片" />\n'
        '              <Stat\n'
        '                label="阶段"\n'
        '                value={stages.length}\n'
        '                hint={`已启用 ${enabledStages.length}`}\n'
        '                tone="accent"\n'
        '              />\n'
        '              <Stat label="本次 run" value={job ? agentStatusLabel(job.status) : "空闲"} hint={jobTemplate || "未选择模板"} />\n'
        '              <Stat label="季" value={seriesList.length} hint={seasonSelected ? seasonSelected.label : "未选择系列"} />\n'
        '            </StatRow>\n'
        '          }\n'
        '        />\n\n'
        '        {tab === "workflow" ? (',
        1,
    ),
])

# ---------------- LibraryPage ----------------
L = 'frontend/src/components/library/LibraryPage.tsx'
patch(L, [
    (
        'import { AppShell } from "../shell/AppShell";',
        'import { AppShell } from "../shell/AppShell";\n'
        'import { PageHeader } from "../ui/PageHeader";\n'
        'import { Card } from "../ui/Card";\n'
        'import { Stat, StatRow } from "../ui/Stat";',
        1,
    ),
    (
        '    >\n      {error && (',
        '    >\n'
        '      <PageHeader\n'
        '        title={tab === "final" ? "成片与合集" : tab === "templates" ? "模板库" : tab === "styles" ? "画面风格" : "场景底板"}\n'
        '        subtitle={\n'
        '          tab === "final"\n'
        '            ? "已出片的模板、季合集与 QC / 发布包入口，都从这里进。"\n'
        '            : "素材与模板的目录；点击卡片查看详情或直接进生产。"\n'
        '        }\n'
        '        meta={\n'
        '          <StatRow>\n'
        '            <Stat label="已出片" value={`${doneCount}/${templates.length || "—"}`} tone="accent" hint="含 3D 样片" />\n'
        '            <Stat label="已选" value={selectedIds.size} hint="删除 / 批量导出" />\n'
        '            <Stat label="批量任务" value={batch ? isBatchActive(batch) ? "进行中" : "已结束" : "空闲"} />\n'
        '          </StatRow>\n'
        '        }\n'
        '      />\n\n'
        '      {error && (',
        1,
    ),
])
