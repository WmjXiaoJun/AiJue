# 综艺模式参考库（VARIETY_REFERENCES）

> 用途：为 `contract/formats/*.json` 中的 15 个节目模式提供「参考节目 + 可借鉴点 + 我们的差异化」背书，并回答「为什么历史综艺适合做 AI 微综艺」。
> 版本：2026-10-24 · 范围：国外 7 个 / 国内 6 个 / 历史 2 个，共 15 个模板。
> 核实方式：链接均来自 2026-10-24 的 `web_search` 结果（百度百科、央视/人民日报/中国日报、BBC、NBC、ABC、Britannica、Variety、NYT 等）；本机对部分境外站点直连受限，未逐页打开，引用以「可检索到的权威页面」为准。

## 0. 模板总览

| # | format id | 模板名 | 参考节目（简） | 时长 |
|---|---|---|---|---|
| 1 | `tonight_show_5min` | 美式深夜秀 · 5 分钟 | The Tonight Show（NBC） | 312s |
| 2 | `snl_sketch_5min` | SNL 式小品 · 5 分钟 | Saturday Night Live（NBC） | 301s |
| 3 | `taskmaster_5min` | 任务大师 · 5 分钟 | Taskmaster（Channel 4） | 311s |
| 4 | `traitors_5min` | 叛徒 · 圆桌 · 5 分钟 | The Traitors（BBC） | 312s |
| 5 | `shark_tank_5min` | 创智赢家 · 5 分钟 | Shark Tank（ABC） | 306s |
| 6 | `masked_singer_5min` | 蒙面唱将 · 5 分钟 | The Masked Singer（FOX） | 306s |
| 7 | `hot_ones_5min` | 辣访谈 · 5 分钟 | Hot Ones（First We Feast） | 306s |
| 8 | `qipashuo_5min` | 奇葩辩论 · 5 分钟 | 奇葩说（爱奇艺） | 306s |
| 9 | `standup_5min` | 脱口秀大会 · 5 分钟 | 脱口秀大会（腾讯视频） | 306s |
| 10 | `slow_life_5min` | 慢生活 · 5 分钟 | 向往的生活（湖南卫视） | 306s |
| 11 | `mystery_5min` | 推理现场 · 5 分钟 | 大侦探 / 明星大侦探（芒果 TV） | 316s |
| 12 | `national_treasure_5min` | 国宝守护 · 5 分钟 | 国家宝藏（央视） | 306s |
| 13 | `classics_5min` | 典籍对话 · 5 分钟 | 典籍里的中国（央视） | 311s |
| 14 | `history_talk_5min` | 历史夜话 · 5 分钟 | 观复嘟嘟 + Drunk History + 典籍里的中国 | 348s |
| 15 | `history_mystery_5min` | 历史悬案 · 5 分钟 | 历史那些事 + 大侦探 | 359s |

> 全部模板：`aspect=16:9`；首段 `open/title_card`、末段 `closing/end_card`；镜头 pattern 仅使用 `title_card / host_to_camera / shot_reverse / reaction / meme_card / b_roll / end_card` 七种；包装沿用平台默认（`intro=logo_sting`、`outro=end_card`、`lower_third_style=zaopianju_yellow`、`transition=hard_cut`、`bgm=talk_light`、`subtitle_style=variety`）。

---

## 1. 国外模式（7）

### 1.1 `tonight_show_5min` · 美式深夜秀
- **参考节目**：*The Tonight Show Starring Jimmy Fallon* /《吉米·法伦今夜秀》（美国 · NBC）
- **模式核心**：主持人开场独白（monologue，当晚新闻梗）→ 明星嘉宾登场 → 访谈 → 游戏/音乐表演 → 道别预告；固定演播室、固定机位、强主持人格。
- **可借鉴点**：① 独白是一集的「钩子」，可用 2–3 个段子 + 花字卡快速建立节奏；② 访谈用「一个问题 + 一个反问 + 一个游戏」的三段式，正反打即可撑满；③ 结尾固定「下周嘉宾」钩子，利于系列化。
- **我们的模板差异**：无现场观众与乐队，独白改为面向镜头 + 反应镜头 + 花字卡；游戏环节用 b_roll/meme_card 替代真实道具；整集压到 5 分钟、17 个左右镜头，靠台本与包装出综艺感。
- **来源**：[The Tonight Show（Wikipedia REST）](https://en.wikipedia.org/api/rest_v1/page/mobile-html/The_Tonight_Show_with_Jimmy_Fallon) · [吉米晚间脱口秀（百度百科）](https://baike.baidu.com/item/%E5%90%89%E7%B1%B3%E6%99%9A%E9%97%B4%E8%84%B1%E5%8F%A3%E7%A7%80)

### 1.2 `snl_sketch_5min` · SNL 式小品
- **参考节目**：*Saturday Night Live* /《周六夜现场》（美国 · NBC），含 *Weekend Update* 新闻播报版块
- **模式核心**：冷开场 → 主持独白 → 2–3 个独立小品（固定场景、角色反转、社会梗）→ 谢幕；单集由多个 3–6 分钟 sketch 组成。
- **可借鉴点**：① 「一集两小品」结构适合 5 分钟微综艺；② sketch 靠「错位身份 + 重复升级 + 一句 punchline」推进，本平台可用 meme_card 承接 punchline；③ Weekend Update 式「新闻播报恶搞」可用 host_to_camera + shot_reverse + reaction 低成本实现。
- **我们的模板差异**：AI 角色固定班底复用（不必每集换演员）；场景只保留 2 套（办公室/演播台）降低资产成本；笑点依赖台本与花字，无罐头笑声与现场乐队。
- **来源**：[Saturday Night Live（NBC 官方）](https://www.nbc.com/saturday-night-live) · [Weekend Update（Wikipedia REST）](https://en.wikipedia.org/api/rest_v1/page/mobile-html/Saturday_Night_News) · [Weekend Update（Web Archive）](https://webarchiveweb.wayback.bac-lac.canada.ca/web/20051215000000/http://en.wikipedia.org/wiki/Weekend_Update)

### 1.3 `taskmaster_5min` · 任务大师
- **参考节目**：*Taskmaster* /《任务大师》（英国 · Channel 4，原 Dave）
- **模式核心**：主持人给 5 位喜剧演员布置荒诞任务（限时/限定道具），播放任务录像并现场评分吐槽；「任务规则 + 失败集锦 + 主持人毒舌」是笑点核心。
- **可借鉴点**：① 规则越具体越好笑，适合用花字卡（meme_card）陈列任务原文；② 「选手执行 + 演播室点评」双线剪辑，天然对应 shot_reverse/b_roll；③ 评分环节可做互动投票，利于系列化。
- **我们的模板差异**：无真实选手，用 2 个 AI 常驻角色「参赛 + 互评」；任务执行改为旁白 + b_roll 情景演绎；评分改为主持人/观众投票，时长压到 5 分钟。
- **来源**：[What is the point of Taskmaster?（Britannica）](https://www.britannica.com/question/What-is-the-point-of-the-show-Taskmaster) · [Taskmaster 节目页（British Comedy Guide）](https://www.comedy.co.uk/tv/taskmaster/episodes/20261/1/)

### 1.4 `traitors_5min` · 叛徒 · 圆桌
- **参考节目**：*The Traitors* /《叛徒》（英国 · BBC One；多国版本）
- **模式核心**：主持人统领「城堡」游戏，玩家分「叛徒/忠臣」，白天做任务、夜间密谋淘汰，圆桌讨论后投票放逐；悬疑感来自信息不对称与身份揭晓。
- **可借鉴点**：① 圆桌互指 + 投票是最强戏剧结构，纯对白即可成立；② 主持人冷幽默串场 + 规则宣告，可复用为 host_to_camera；③ 结尾「身份揭晓/下一轮悬念」是天然钩子。
- **我们的模板差异**：AI 微综艺不做真人淘汰赛，改为「2 位嘉宾 + 主持人」的轻量推理局；无城堡实景，用 b_roll 做氛围、meme_card 做身份牌；一集一个完整小案件。
- **来源**：[Claudia Winkleman announced as host of The Traitors（BBC Media Centre）](https://www.bbc.co.uk/mediacentre/2022/claudia-winkleman-the-traitors) · [The Traitors reveals ceremonial dagger（RTE）](https://www.rte.ie/entertainment/2026/0121/1554400-the-traitors-reveals-ceremonial-dagger/)

### 1.5 `shark_tank_5min` · 创智赢家
- **参考节目**：*Shark Tank* /《创智赢家》（美国 · ABC，源自日本《マネーの虎》）
- **模式核心**：创业者电梯路演 → 投资人（Sharks）连环质询 → 出价/还价 → 成交或离场；「估值博弈 + 人身攻击式提问」制造张力。
- **可借鉴点**：① 路演 30 秒讲清痛点，适合 5 分钟开篇；② 质询用「连环追问 + 拒绝」二段式，正反打即可；③ 结局（成交/被拒/离场）天然是结尾钩子。
- **我们的模板差异**：虚拟项目与虚拟投资人，避免真实商业信息与广告合规风险；出价环节用花字卡展示数字，无真实合同；整集压缩为「1 个项目 1 轮谈判」。
- **来源**：[About Shark Tank（ABC 官方）](https://abc.com/shows/shark-tank/about-the-show/1000) · [Shark Tank S17 Lead Sheet（ABC 节目资料）](https://www.detpress.com/storage/uploads/8A/F0/8AF0E877-C215-F9F7-30B9-73A2A06F4C96/Shark_Tank_S17_Lead_Sheet_2025-2026.pdf) · [Want to Understand America? Watch 'Shark Tank'（NYT）](https://www.nytimes.com/interactive/2024/10/17/business/shark-tank-economy.html)

### 1.6 `masked_singer_5min` · 蒙面唱将
- **参考节目**：*The Masked Singer* /《蒙面唱将》（美国 · FOX，源自韩国《蒙面歌王》）
- **模式核心**：蒙面名人舞台表演 → 猜评团根据线索推理 → 每轮揭面淘汰；「身份悬念 + 竞猜互动」是核心。
- **可借鉴点**：① 每集 1–2 个「面具身份」即可成立；② 表演段落用 b_roll/舞台镜头 + 反应镜头；③ 揭面时刻做情绪高点，适合 meme_card 与 end_card 前的爆点。
- **我们的模板差异**：AI 虚拟歌手/角色戴面具，规避真人肖像与音乐版权（BGM/歌曲需另配授权或原创）；舞台用生成式空镜，无真实灯光秀；猜评环节改为观众投票。
- **来源**：[How to watch The Masked Singer Season 14（USA Today）](https://www.usatoday.com/story/entertainment/tv/2026/01/06/how-to-watch-the-masked-singer-season-14/88021678007/) · [FOX 节目单（The Futon Critic）](http://www.thefutoncritic.com/listings/20260311fox05/) · [The Masked Singer 节目页（TV Guide）](https://www.tvguide.com/tvshows/the-masked-singer/episodes-season-1/1000683748)

### 1.7 `hot_ones_5min` · 辣访谈
- **参考节目**：*Hot Ones* /《辣访谈》（美国 · First We Feast / YouTube）
- **模式核心**：嘉宾边吃越来越辣的鸡翅边接受访谈，辣度上升 → 问题变尖锐；「生理反应 + 走心回答」形成反差。
- **可借鉴点**：① 单一固定场景、双人正反打，制作成本极低、可批量；② 「辣度阶梯」= 内容分段的天然节拍器（每级一个问题）；③ 来宾反应镜头是表情包富矿，meme_card 利用率最高。
- **我们的模板差异**：AI 角色不真吃辣，「辣度」改为「敏感度」分级（问题逐渐冒犯）；无真实食物道具，用花字卡标注辣度等级；主持人固定、嘉宾每集一换。
- **来源**：[Hot Ones 新系列报道（Variety）](https://variety.com/2026/digital/news/hot-ones-game-night-joe-santagato-first-we-feast-1236867172/) · [Hot Ones 相关报道（Yahoo Sports）](https://sports.yahoo.com/articles/iceman-hot-ones-chicago-bears-215700635.html)

---

## 2. 国内模式（6）

### 2.1 `qipashuo_5min` · 奇葩辩论
- **参考节目**：《奇葩说》（中国 · 爱奇艺）
- **模式核心**：主持人开题 → 正反双方（奇葩辩手）开杠 → 自由辩论 → 结辩/观众投票；「金句 + 情绪 + 立场反转」是传播核心。
- **可借鉴点**：① 一集一题、正反双方各 2 人即可撑满 5 分钟；② 开杠与自由辩论是纯对白结构，适配 shot_reverse + reaction；③ 金句用 meme_card 停格放大，适合短视频切条。
- **我们的模板差异**：AI 辩手人设固定（逻辑型/情绪型），无现场观众投票改为弹幕式花字呈现；选题偏轻话题（AI、职场、生活），规避敏感议题；结辩压到 1 分钟。
- **来源**：[奇葩说（百度百科）](https://baike.baidu.com/item/%E5%A5%87%E8%91%A9%E8%AF%B4) · [奇葩说（百度百科·移动版）](https://wapbaike.baidu.com/item/%E5%A5%87%E8%91%A9%E8%AF%B4)

### 2.2 `standup_5min` · 脱口秀大会
- **参考节目**：《脱口秀大会》（中国 · 腾讯视频）
- **模式核心**：主持人热场 → 选手轮番上台讲段子 → 领笑员点评/爆梗 → 投票晋级；「个人段子 + 生活观察 + 爆梗」是核心。
- **可借鉴点**：① 单口段落用 host_to_camera 为主、b_roll 做情境补画面，是 7 种 pattern 里最省成本的结构；② 热场与点评可做 reaction/花字；③ 结尾留「下期主题」钩子。
- **我们的模板差异**：AI 脱口秀演员固定 1–2 人，段子由 LLM 按选题生成；无真实观众笑声，用 BGM/花字替代；保留「爆梗停格」节奏但压缩到 5 分钟。
- **来源**：[脱口秀大会（百度百科）](https://baike.baidu.com/item/%E8%84%B1%E5%8F%A3%E7%A7%80%E5%A4%A7%E4%BC%9A)

### 2.3 `slow_life_5min` · 慢生活
- **参考节目**：《向往的生活》（中国 · 湖南卫视）
- **模式核心**：固定院落（蘑菇屋）+ 常驻嘉宾 + 飞行嘉宾，做饭、干农活、聊天、夜话；弱赛制、强氛围，靠生活流与人物关系。
- **可借鉴点**：① 空镜/环境镜头（b_roll）承担情绪，适合生成式画面；② 「做饭 + 围炉夜话」两段式即可成集；③ 慢节奏对镜头运动要求低，是 AI 最友好的综艺类型之一。
- **我们的模板差异**：全 AI 场景，无真实劳作；用旁白 + 空镜建立氛围；夜话加入「今日一问」互动；单集 5 分钟、以情绪收尾而非赛制高潮。
- **来源**：[“慢综艺”与生活追求——以《向往的生活》为例（人民网）](http://media.people.com.cn/n1/2019/0112/c424560-30524183.html) · [慢综艺节奏因素浅析——以《向往的生活》为例（人民网）](http://media.people.com.cn/GB/n1/2018/1203/c422573-30439764.html)

### 2.4 `mystery_5min` · 推理现场
- **参考节目**：《大侦探》（原名《明星大侦探》，中国 · 芒果 TV）
- **模式核心**：案件背景（情景再现）→ 现场搜证 → 集中推理 → 投票/揭晓；「角色扮演 + 线索链 + 反转」是核心。
- **可借鉴点**：① 5 分钟可做「单案件单线索链」，结构为：案件 → 2 条线索 → 推演 → 投票 → 揭晓；② 情景再现用 b_roll，推理用 shot_reverse，投票用 meme_card；③ 结尾揭晓是天然完播钩子。
- **我们的模板差异**：AI 虚拟案件（非真实命案），规避血腥与真实案件改编风险；线索以花字卡/道具图呈现；投票为观众互动，无明星玩家。
- **来源**：[芒果 TV 普法教育推理节目《大侦探第 11 季》开播（中国日报）](https://cn.chinadaily.com.cn/a/202602/11/WS698c3c69a310942cc499f50c.html) · [《大侦探第 11 季》开播报道（南方新闻网）](https://www.nfnews.com/content/5ypp1z2nyp.html)

### 2.5 `national_treasure_5min` · 国宝守护
- **参考节目**：《国家宝藏》（中国 · 央视）
- **模式核心**：001 号讲解员开场 → 国宝守护人 + 明星演绎「前世传奇」情景剧 → 讲述「今生故事」→ 入藏仪式；「文物 + 戏剧 + 仪式感」是核心。
- **可借鉴点**：① 「前世传奇（情景剧）+ 今生故事（访谈）」双段式，正好对应 b_roll + shot_reverse；② 讲解员开场用 host_to_camera，仪式感靠 meme_card/end_card；③ 每集一件文物，天然系列化。
- **我们的模板差异**：AI 复现历史场景与人物（无需剧组/服化道），文物用生成图 + 版权说明；去掉真实明星守护人，改为虚拟守护人；单集压到 5 分钟、一件文物。
- **来源**：[《国家宝藏》节目热播原因探析（人民网）](http://media.people.com.cn/GB/n1/2018/0925/c421527-30312176.html) · [从《国家宝藏》看文博探索类综艺的创新策略（知网）](https://wap-cnki-net-443.webvpn.imac.edu.cn/touch/web/Journal/Article/XBGS202502017.html)

### 2.6 `classics_5min` · 典籍对话
- **参考节目**：《典籍里的中国》（中国 · 央视）
- **模式核心**：主持人（撒贝宁）+ 当代读书人 + 典籍情景戏剧 + 「古今对话」；把典籍作者请到现场与当代人对谈。
- **可借鉴点**：① 「古今对话」是历史类 AI 内容最好的结构——让历史人物直接回应现代问题；② 戏剧段用 shot_reverse/b_roll，对话段用 host_to_camera；③ 典籍金句适合 meme_card 字幕卡。
- **我们的模板差异**：AI 历史人物分身与主持人对话（无需演员），戏剧段用生成画面；每集只讲一本典籍/一个章节；时长 5 分钟、以一句金句收尾。
- **来源**：[《典籍里的中国》正月初一创新开启“古今对话”（央视网）](https://tv.cctv.com/2021/02/12/ARTI38JBGYXBLRo8CpeWDvCl210212.shtml) · [《典籍里的中国》树文化节目新标杆（中国文艺网）](https://www.cflac.org.cn/ys/ysds/dsrdtj/202103/t20210315_537578.html)

---

## 3. 历史综艺（2，用户点名）

### 3.1 `history_talk_5min` · 历史夜话
- **参考节目**：
  1. *Drunk History*（美国 · Comedy Central）：醉酒讲述者讲历史，演员对口型情景再现；
  2. 《观复嘟嘟》（中国 · 马未都文化脱口秀）：单人讲述 + 文物/历史掌故 + 通俗类比；
  3. 《典籍里的中国》（中国 · 央视）：「古今对话」让历史人物回应现代。
- **模式核心**：主持人 + 2 位历史人物 AI 分身圆桌。主持人抛现代问题 → 人物 A 以史料立场自述 → 人物 B 反驳/补充（不同史料或对立身份）→ 三人就一个争议点辩论 → 主持人收束并留悬念。
- **可借鉴点**：① 历史人物自带「人设与立场」（秦始皇/孔子/王安石/苏轼等），冲突不用编；② 圆桌辩论 = 纯对白结构，7 种 pattern 全覆盖（host_to_camera/shot_reverse/reaction/meme_card/b_roll）；③ 用 meme_card 出史料原文、用 b_roll 出「情景再现」，知识性与综艺感兼得。
- **我们的模板差异**：AI 分身替代演员扮相，避免「像不像」的选角争议；史料出处用花字卡强制标注（可信度）；不做真实醉酒/饮酒桥段，改为「夜谈」氛围；单集 5 分钟、一个争议话题收口。
- **来源**：[Drunk History（Comedy Central 官方）](https://www.cc.com/shows/drunk-history) · [Drunk History（IMDb 新闻页）](https://s.media-imdb.com/news/ni61884551/) · [观复嘟嘟（百度百科）](https://baike.baidu.com/item/%E8%A7%82%E5%A4%8D%E5%98%9F%E5%98%9F/19328813) · [《典籍里的中国》“古今对话”（央视网）](https://tv.cctv.com/2021/02/12/ARTI38JBGYXBLRo8CpeWDvCl210212.shtml)

### 3.2 `history_mystery_5min` · 历史悬案
- **参考节目**：
  1. 《历史那些事》（中国 · B 站）：纪录片 + 喜剧化情景再现，把史料拍成「段子」；
  2. 《大侦探》（中国 · 芒果 TV）：案件 → 线索 → 推理 → 投票 → 揭晓的推理综艺结构；
  3. *Horrible Histories*（英国 · BBC）：用 sketch 喜剧讲历史的「暗黑/搞笑」一面。
- **模式核心**：悬案背景（情景再现）→ 线索一（史料比对）→ 线索二（人物动机）→ 推演与质疑（多假说）→ 观众投票 → 揭晓主流学界观点 + 遗留疑点。
- **可借鉴点**：① 历史悬案天然具备「案件—线索—反转」三段式，与推理综艺同构；② 情景再现（b_roll）+ 线索花字（meme_card）成本低、信息密度高；③ 结尾投票 + 学界观点揭晓，兼顾互动与知识正确性。
- **我们的模板差异**：AI 情景再现替代剧组重演；假说用「三选一」投票降低理解门槛；结尾必须标注「本集为艺术演绎 / 主流观点见参考文献」；单集一桩悬案，5 分钟收口。
- **来源**：[历史那些事 第三季（百度百科）](https://baike.baidu.com/item/%E5%8E%86%E5%8F%B2%E9%82%A3%E4%BA%9B%E4%BA%8B%20%E7%AC%AC%E4%B8%89%E5%AD%A3) · [历史那些事（Plex 节目页）](https://watch.plex.tv/zh/show/chinas-historical-highlights/season/1) · [Horrible Histories 新闻稿（BBC Press Office）](https://www.bbc.co.uk/pressoffice/pressreleases/stories/2010/12_december/06/histories.shtml) · [Horrible Histories 节目信息（BBC Press Office）](https://www.bbc.co.uk/pressoffice/proginfo/tv/2009/wk15/thu.shtml) · [《大侦探第 11 季》报道（中国日报）](https://cn.chinadaily.com.cn/a/202602/11/WS698c3c69a310942cc499f50c.html)

---

## 4. 历史综艺为什么适合 AI 微综艺

1. **镜头语法最简单，正好落在平台 7 种 pattern 的能力圈内**：历史类以「讲述 + 对谈 + 情景再现 + 花字」为主，host_to_camera / shot_reverse / reaction / b_roll / meme_card 全覆盖；不依赖大场面、复杂动作与多人同框，规避了当前生成模型最容易崩的部分。
2. **角色是「公共形象」，一致性成本低、合规风险小**：历史人物的画像、服饰、性格有大众共识与大量公有领域参考，AI 生成容错高；不像真人明星需要肖像/声音授权，也不像虚构角色要从零锁脸。
3. **资产复用率极高，边际成本低**：朝堂、书房、战场、街市 4–6 套场景 + 10 来个历史人物服装，可支撑一整季；符合平台「资产先行 + 模板包装 + 批量出片」的 MVP 逻辑，单集成本可控。
4. **冲突与剧本自带**：史料本身就有多方记载、争议与悬案，天然产出「圆桌辩论 / 悬案推演 / 投票」结构，LLM 写台本有明确的事实锚点，比纯虚构综艺更好控。
5. **知识型内容的「信息密度」可以弥补画面精度**：观众对历史综艺的期待是「讲明白 + 有观点」，花字卡（meme_card）承担史料出处与金句，画面只需服务气氛；这正好是 AI 微综艺「轻画面、重包装」的强项。
6. **系列化与分发友好**：按朝代/人物/悬案做系列，单集 5 分钟可切条、可跨平台分发；「历史夜话」适合横屏长谈，「历史悬案」适合竖屏切条，一套资产两种分发。
7. **模式已被市场验证**：海外有 *Drunk History*（历史 + 情景再现）与 *Horrible Histories*（历史 + sketch 喜剧），国内有《典籍里的中国》（古今对话）、《国家宝藏》（前世传奇）、《历史那些事》（喜剧化再现）——「历史 + 综艺化」是成熟模式；AI 只是把「再现」和「扮演」的成本降到可批量，而不改变观众已经接受的节目结构。

> 落地建议：先用 `history_talk_5min` 验证「主持 + 2 位历史人物 AI 分身」的三角对话节奏（纯对白、最省成本），再用 `history_mystery_5min` 叠加情景再现与投票互动做系列化；两套模板共用历史人物/场景资产库，可把单集边际成本压到最低。

---

## 5. 备注（工程侧）

- 新增格式放在 `contract/formats/`，后端 `backend/app/main.py::_load_format` 按 `FORMATS_DIR/{format_id}.json` 直接读取，无需改代码；新建项目时传 `format_id` 即可拉出对应骨架分镜（`_create_skeleton_shots` 取每个 segment 的 `pattern[0]` 作为镜头类型，`open`→title_card、`closing`→end_card）。
- 前端模板卡片 `frontend/src/lib/templates.ts` 目前仍只列 `roundtable_5min`，若要在 UI 展示新模板需另行同步（本任务只写 `contract/formats/` 与本文档，未改代码）。
- 所有模板的 `target_duration` 等于各 segment 之和，范围 301–359s，符合 300–480s 约束。
