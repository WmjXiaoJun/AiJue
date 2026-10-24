# 台本格式与「台本 → 分镜」契约 v1

目标：让用户用自己的台本驱动分镜，而不是只用格式模板骨架。

## 1. 存储
- Episode 新增字段 script（TEXT，默认 ""）。
- PATCH /api/episodes/{eid} 支持 script；GET /api/episodes/{eid} 返回 script。
- 注意 SQLite 已存在表：启动时需做轻量迁移 ALTER TABLE episodes ADD COLUMN script TEXT DEFAULT ''（或等价 ensure_column）。

## 2. 台本格式（面向非技术用户）
~~~text
[开场]
老周：今天这期，我们聊一个得罪人的话题。
阿栗：AI 现在能写脚本、能剪片，还能自己找梗。
大魏：（冷笑）机器先学会让人笑，再谈取代。

[正片]
老周：所以第一个问题——你上一次被 AI 逗笑是什么时候？
阿栗：上周三，它把我的口头禅重复了七遍。
大魏：这不是好笑，这是故障。

[结尾]
老周：好，今天先聊到这儿，下期我们继续拆。
~~~
规则：
- 段落标题：以 [ ] 或 【 】 包裹的一行（如 [开场] / 【正片】），只做分段标记，不单独成镜；缺省时全部归入"正片"。
- 对白：一行以「名字：台词」或「名字: 台词」开头（中文/英文冒号均可）。名字匹配资产/角色名（包含匹配，如"老周"匹配 name/title）；匹配失败时 speaker_name 用原文名字，speaker 置空、voice 用 host 音色。
- 旁白/独白：无冒号的行，视为主持人旁白（speaker=host 资产），保留原文。
- 动作/反应：以（）或 () 包裹整行的，生成 reaction 镜头，visual=括号内文字，无台词；speaker 沿用上一个说话人。
- 空行只做分隔。
- 片头 title_card（episode.title）与片尾 end_card 自动生成，各 3–4s。
- 单镜时长：clamp(1.2 + 台词字数*0.13, 2.5, 8.0) 秒；标题/片尾固定 3.5s。
- 生成顺序：title_card → 各段镜头（按台本顺序）→ end_card，no 递增。
- Shot.type：对白 talking_head / 反应 reaction / 旁白 narration（沿用现有 type 值域，缺省 host_to_camera 可接受，但请与 pipeline 现有类型兼容）。

## 3. API
- POST /api/projects/{pid}/episodes {title?, target_duration?} → 创建下一集（no 自增，status=draft，空 script/分镜）。
- PATCH /api/episodes/{eid} {script?, title?, target_duration?}。
- POST /api/episodes/{eid}/storyboard/generate {force?: false} → 若已有镜头且未 force 返回 {created:0}；否则按 script 解析生成；script 为空回退旧模板骨架（保持兼容）。
- POST /api/episodes/{eid}/shots {type?, line?, duration?, speaker_name?, visual?} → 追加镜头并 reindex no。
- DELETE /api/shots/{sid} → 删除并 reindex。
- 测试：解析（中英文冒号/括号动作/旁白/未知说话人/空行）、时长 clamp、自动片头片尾、force 语义、创建剧集、删除重排、SQLite 迁移。

## 4. 前端
- 编剧间：台本 textarea（读/写 episode.script）、保存（PATCH）、脏标记；「生成分镜」按钮改为「从台本生成分镜」，有镜头时二次确认（force=true）；显示 created 数量与分段摘要。
- 剧集：项目页支持「新建剧集」；剧集列表可切换。
- 分镜台：支持添加镜头、删除镜头、编辑台词/时长（PATCH 已有）。
- 台本为空时给出示例格式提示（可一键填入示例）。

## 5. 成片验证
- 新项目（或 demo 项目新建 ep_002）→ 台本 60–90s（3 角色、含反应/旁白）→ 生成分镜 → 渲染成片 → 验收：时长≈sum(duration)、1280×720、blackdetect=0、TTS 按角色音色、字幕与台词一致、无崩溃。
