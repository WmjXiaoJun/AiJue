# -*- coding: utf-8 -*-
"""R5 文案：正文不出现接口路径/技术 ID，把它们挪进 title。"""
import io

def patch(path, pairs):
    s = open(path, encoding='utf-8').read()
    for old, new, n in pairs:
        c = s.count(old)
        assert c == n, f'{path}: expect {n} got {c}: {old[:70]!r}'
        s = s.replace(old, new)
    open(path, 'w', encoding='utf-8', newline='').write(s)
    print('patched', path)

patch('frontend/src/components/pipeline/LongformPanel.tsx', [
    (
        'LONGFORM · {LONGFORM_DURATION}s · /api/v5/longform/{running ? "status" : "run"}',
        '长版 · {LONGFORM_DURATION}s · {running ? "运行中" : "可启动"}',
        1,
    ),
    (
        '长版接口返回 404（GET /api/v5/longform/status）：按钮先禁用；面板每 30s 自检，恢复后自动点亮。',
        '长版接口暂不可用：按钮先禁用；面板每 30s 自检，恢复后自动点亮。',
        1,
    ),
])

patch('frontend/src/components/agent/AgentPage.tsx', [
    (
        'MUSETALK · /api/v6/muse · {selectedId || "未选择模板"}',
        'MuseTalk 口型 · {selectedId || "未选择模板"}',
        1,
    ),
    (
        ': "POST /api/v6/muse/sync {template, limit:2} → 给本集有台词镜头跑 MuseTalk 口型"',
        ': "给本集有台词镜头跑 MuseTalk 口型（默认 2 个镜头）"',
        1,
    ),
])
