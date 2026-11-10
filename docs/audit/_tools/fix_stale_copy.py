# -*- coding: utf-8 -*-
"""把「后端 Vxx 实现中 / 未挂载 / 尚未就绪」这类已过期的用户可见文案改为中性、可行动的措辞。
逐条 exact-match 替换，每条断言命中次数，命中数不符即中止（不写文件）。"""
import io, sys

R = [
 # ---------------- AgentPage ----------------
 ('frontend/src/components/agent/AgentPage.tsx',
  '"Muse 状态接口尚未就绪（404）：/api/v6/muse/status 未挂载，口型按钮暂不可用。"',
  '"Muse 状态读取失败（404）：请确认后端已在 8090 启动，再点「刷新」重试。"', 2),
 ('frontend/src/components/agent/AgentPage.tsx',
  '"Muse 视频接口尚未就绪（404）：/api/v6/muse/video/status 未挂载。"',
  '"Muse 视频状态读取失败（404）：请确认后端已在 8090 启动，再点「刷新」重试。"', 2),
 ('frontend/src/components/agent/AgentPage.tsx',
  '"工作流接口尚未就绪（404），已用概览阶段兜底；保存暂不可用。"',
  '"工作流接口返回 404，已用概览阶段兜底；请确认该模板的合同存在后重试。"', 1),
 ('frontend/src/components/agent/AgentPage.tsx',
  '"智能体 run 接口尚未就绪（404）：后端 /api/v6/agent/run 未挂载。"',
  '"智能体 run 接口返回 404：请确认后端已启动（/api/v6/agent/run 可访问）后再试。"', 1),
 ('frontend/src/components/agent/AgentPage.tsx',
  '"季状态接口尚未就绪（404）：集列表来自系列合同 /api/v5/series，成片/最近 run 以合同为准。"',
  '"季状态接口返回 404：集列表暂用系列合同 /api/v5/series 兜底，成片/最近 run 以合同为准。"', 1),
 ('frontend/src/components/agent/AgentPage.tsx',
  '"季生产接口尚未就绪（404，后端 M3 实现中）：POST /api/v6/agent/season/" + seasonSid + "/run 暂不可用；单集 run 与工作流编辑不受影响。"',
  '"季生产接口返回 404（POST /api/v6/agent/season/" + seasonSid + "/run）：请确认系列 id 存在、后端已启动；单集 run 与工作流编辑不受影响。"', 1),
 ('frontend/src/components/agent/AgentPage.tsx',
  '"Muse 接口尚未就绪（404）：POST /api/v6/muse/sync 未挂载。"',
  '"Muse 口型接口返回 404（POST /api/v6/muse/sync）：请确认后端已启动、local_models/lipsync 就绪。"', 1),
 ('frontend/src/components/agent/AgentPage.tsx',
  '"Muse 视频接口尚未就绪（404）：POST /api/v6/muse/video 未挂载。"',
  '"Muse 视频接口返回 404（POST /api/v6/muse/video）：请确认后端已启动。"', 1),
 ('frontend/src/components/agent/AgentPage.tsx',
  '该系列合同的 members 为空，或季状态接口尚未就绪。',
  '该系列合同的 members 为空，或季状态接口返回 404。', 1),

 # ---------------- CharactersPage ----------------
 ('frontend/src/components/characters/CharactersPage.tsx',
  '"试镜任务接口尚未就绪（后端 V32 实现中）"',
  '"试镜任务接口返回 404：请确认角色 id 存在、后端已启动"', 1),
 ('frontend/src/components/characters/CharactersPage.tsx',
  '"试镜视频接口尚未就绪（后端 V32 实现中）"',
  '"试镜视频接口返回 404：请确认任务 id 存在、后端已启动"', 2),
 ('frontend/src/components/characters/CharactersPage.tsx',
  '"宣传片任务接口尚未就绪（后端 V35 实现中）"',
  '"宣传片任务接口返回 404：请确认角色 id 存在、后端已启动"', 1),
 ('frontend/src/components/characters/CharactersPage.tsx',
  '"角色宣传片接口尚未就绪（后端 V35 实现中）"',
  '"角色宣传片接口返回 404：请确认角色 id 存在、后端已启动"', 2),
 ('frontend/src/components/characters/CharactersPage.tsx',
  '"切条包接口尚未就绪（后端 V38 实现中）"',
  '"切条包接口返回 404：请确认角色 id 存在、后端已启动"', 1),
 ('frontend/src/components/characters/CharactersPage.tsx',
  '"双人对戏接口尚未就绪（后端 V36 实现中）"',
  '"双人对戏接口返回 404：请确认 A/B 角色 id 存在、后端已启动"', 3),
 ('frontend/src/components/characters/CharactersPage.tsx',
  '"合集发布包接口尚未就绪（后端 V36 实现中）；可点「导出发布包」重试"',
  '"合集发布包接口返回 404：请确认角色 id 存在，可再点「导出发布包」重试"', 1),
 ('frontend/src/components/characters/CharactersPage.tsx',
  '"合集发布包接口尚未就绪（后端 V36 实现中）"',
  '"合集发布包接口返回 404：请确认角色 id 存在、后端已启动"', 1),
 ('frontend/src/components/characters/CharactersPage.tsx',
  '"试听接口尚未就绪（V18 后端 API 实现中）；保存音色不受影响，生成时生效。"',
  '"试听接口返回 404：保存音色不受影响，生成时生效。"', 1),
 ('frontend/src/components/characters/CharactersPage.tsx',
  '（试听接口可能尚未就绪）',
  '（部分试听接口返回 404）', 1),

 # ---------------- CreateCharacterDrawer / CreateTemplateWizard ----------------
 ('frontend/src/components/characters/CreateCharacterDrawer.tsx',
  '"创建接口尚未就绪（后端 V21 API 实现中），可稍后重试"',
  '"创建接口返回 404：请确认后端已启动，可稍后重试"', 1),
 ('frontend/src/components/library/CreateTemplateWizard.tsx',
  '"创建接口尚未就绪（后端 V21 API 实现中），可稍后重试"',
  '"创建接口返回 404：请确认后端已启动，可稍后重试"', 1),

 # ---------------- LibraryPage ----------------
 ('frontend/src/components/library/LibraryPage.tsx',
  '"批量接口尚未就绪（后端 V25 实现中），请稍后重试"',
  '"批量接口返回 404：请确认后端已启动，稍后重试"', 1),
 ('frontend/src/components/library/LibraryPage.tsx',
  '"批量接口尚未就绪（后端 V25 实现中）"',
  '"批量接口返回 404"', 1),
 ('frontend/src/components/library/LibraryPage.tsx',
  '"批量导出接口尚未就绪（后端 V29 实现中），请稍后重试"',
  '"批量导出接口返回 404：请确认后端已启动，稍后重试"', 1),
 ('frontend/src/components/library/LibraryPage.tsx',
  '"批量导出接口尚未就绪（后端 V29 实现中）"',
  '"批量导出接口返回 404"', 1),

 # ---------------- LongformPanel ----------------
 ('frontend/src/components/pipeline/LongformPanel.tsx',
  '"长版接口尚未就绪（404）：/api/v5/longform/run"',
  '"长版接口返回 404：POST /api/v5/longform/run"', 1),
 ('frontend/src/components/pipeline/LongformPanel.tsx',
  '长版接口未就绪（GET /api/v5/longform/status 404）：按钮先禁用；接口上线后本面板 30s 内自动点亮。',
  '长版接口返回 404（GET /api/v5/longform/status）：按钮先禁用；面板每 30s 自检，恢复后自动点亮。', 1),
 ('frontend/src/components/pipeline/LongformPanel.tsx',
  '"接口未就绪：GET /api/v5/longform/status 404"',
  '"接口返回 404：GET /api/v5/longform/status"', 1),
 ('frontend/src/components/pipeline/LongformPanel.tsx',
  'title={notReady ? "接口未就绪" :',
  'title={notReady ? "长版接口返回 404" :', 1),

 # ---------------- PipelineConsole ----------------
 ('frontend/src/components/pipeline/PipelineConsole.tsx',
  '"casting 接口尚未就绪（V18 后端实现中），已用 catalog + 角色合同兜底；音色信息以后端为准。"',
  '"casting 接口返回 404，已用 catalog + 角色合同兜底；音色信息以后端为准。"', 1),
 ('frontend/src/components/pipeline/PipelineConsole.tsx',
  '"设置接口未就绪；发布账号状态无法读取。"',
  '"设置接口不可用；发布账号状态无法读取。"', 1),
 ('frontend/src/components/pipeline/PipelineConsole.tsx',
  '"pipeline 状态接口不可用（后端 V18 API 实现中）；请稍后刷新后重试。"',
  '"pipeline 状态接口不可用；请稍后刷新后重试。"', 1),
 ('frontend/src/components/pipeline/PipelineConsole.tsx',
  '"pipeline 接口尚未就绪（V18 后端 API 实现中，404）。可先用 Cinema 模板库 / 工作台 3D 模板页手动生成与合成；接口就绪后此处即可一键运行。"',
  '"pipeline 接口返回 404。可先用 Cinema 模板库 / 工作台 3D 模板页手动生成与合成；接口恢复后此处即可一键运行。"', 1),

 # ---------------- steps ----------------
 ('frontend/src/components/pipeline/steps.tsx',
  '<span className="v18-tag is-slate">接口未就绪</span>',
  '<span className="v18-tag is-slate">暂无音色</span>', 1),
 ('frontend/src/components/pipeline/steps.tsx',
  '"试听接口尚未就绪（V18 后端 API 实现中）；可先应用音色保存，生成时生效。"',
  '"试听接口返回 404；可先应用音色保存，生成时生效。"', 1),
 ('frontend/src/components/pipeline/steps.tsx',
  'desc="模板合同里没有 cast 字段，或后端 casting 接口尚未就绪；可到角色与声音页统一调整音色。"',
  'desc="模板合同里没有 cast 字段，或 casting 接口返回 404；可到角色与声音页统一调整音色。"', 1),

 # ---------------- PublishPackageModal ----------------
 ('frontend/src/components/publish/PublishPackageModal.tsx',
  '"发布包接口尚未就绪（后端 V26 实现中），请稍后重试"',
  '"发布包接口返回 404：请确认模板 id 存在、后端已启动，稍后重试"', 1),

 # ---------------- Assets (H3) ----------------
 ('frontend/src/components/studio/Assets.tsx',
  '"H3 接口未就绪：后端未提供 /characters/{cid}/h3-animate，请稍后再试"',
  '"H3 接口返回 404：请确认角色 id 存在、后端已启动，稍后再试"', 1),

 # ---------------- Templates3D ----------------
 ('frontend/src/components/studio/Templates3D.tsx',
  '"切换接口未就绪：后端 PATCH /api/projects/{id} 尚未上线（tpl-backend 开发中），请稍后重试"',
  '"切换模板失败：PATCH /api/projects/{id} 返回 404，请确认项目 id 存在后重试"', 1),
 ('frontend/src/components/studio/Templates3D.tsx',
  '"模板库接口未就绪，暂不能提交 3D 生成任务"',
  '"模板库接口不可用，暂不能提交 3D 生成任务"', 1),
 ('frontend/src/components/studio/Templates3D.tsx',
  '"3D 生成接口未就绪，先跳 Cinema 模板库查看进度"',
  '"3D 生成接口不可用，先跳 Cinema 模板库查看进度"', 1),
 ('frontend/src/components/studio/Templates3D.tsx',
  ': "接口未就绪"}',
  ': "暂无状态"}', 2),
 ('frontend/src/components/studio/Templates3D.tsx',
  ': "模板库接口未就绪"}',
  ': "模板库接口不可用"}', 1),

 # ---------------- cinema/Templates ----------------
 ('frontend/src/components/cinema/Templates.tsx',
  'return `${action}接口未就绪（404）`;',
  'return `${action}接口返回 404`;', 1),
 ('frontend/src/components/cinema/Templates.tsx',
  '"模板库接口尚未就绪："',
  '"模板库接口不可用："', 1),
 ('frontend/src/components/cinema/Templates.tsx',
  ': "接口未就绪";',
  ': "未生成";', 1),
 ('frontend/src/components/cinema/Templates.tsx',
  '"模板进度接口未就绪，无法提交生成"',
  '"模板进度不可用，无法提交生成"', 1),

 # ---------------- cinema/Shots ----------------
 ('frontend/src/components/cinema/Shots.tsx',
  '后端 /api/cinema/shots 可能尚未就绪；可重试，或稍后刷新。',
  '后端 /api/cinema/shots 暂不可用；可重试，或稍后刷新。', 1),

 # ---------------- VoicePicker ----------------
 ('frontend/src/components/voice/VoicePicker.tsx',
  '"试听接口尚未就绪（V18 后端 API 实现中）；音色仍可「应用」保存，保存后由生成任务使用。"',
  '"试听接口返回 404；音色仍可「应用」保存，保存后由生成任务使用。"', 1),

 # ---------------- lib/creation ----------------
 ('frontend/src/lib/creation.ts',
  '["接口尚未就绪（后端创建/概念图 API 实现中），请稍后重试"]',
  '["接口返回 404：请确认后端已启动，稍后重试"]', 1),

 # ---------------- SettingsModal ----------------
 ('frontend/src/components/SettingsModal.tsx',
  '"AI 模型测试接口未就绪（等待后端 task-45：POST /api/llm/test）"',
  '"AI 模型测试接口返回 404（POST /api/llm/test）：请确认后端已启动"', 1),
 ('frontend/src/components/SettingsModal.tsx',
  '（后端 PATCH/GET /settings 未就绪时功能自动降级，不影响制作流程）',
  '（设置读写不可用时功能自动降级，不影响制作流程）', 1),
 ('frontend/src/components/SettingsModal.tsx',
  '{llmSupported ? "接口可用" : "接口未就绪"}',
  '{llmSupported ? "接口可用" : "接口不可用"}', 1),

 # ---------------- lib/agent 注释 ----------------
 ('frontend/src/lib/agent.ts',
  '/** 404/405/501 → 后端接口尚未就绪（季接口在 M3 才落地） */',
  '/** 404/405/501 → 接口不可用（季接口已上线；仍 404 多为资源不存在或未挂载） */', 1),
]

errors = []
for path, old, new, expect in R:
    src = open(path, encoding='utf-8').read()
    got = src.count(old)
    if got != expect:
        errors.append(f'{path}: expect {expect} got {got} :: {old[:60]}')

if errors:
    print('ABORT — 未写任何文件：')
    for e in errors:
        print(' ', e)
    sys.exit(1)

for path, old, new, expect in R:
    src = open(path, encoding='utf-8').read()
    open(path, 'w', encoding='utf-8', newline='').write(src.replace(old, new))

print('OK — 已替换', len(R), '条规则 /', sum(r[3] for r in R), '处文案')
