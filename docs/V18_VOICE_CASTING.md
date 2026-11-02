# V18 角色声音选角（32 角色 → 24 breeze preset）

> 数据由 `tmp/v18/build_voice_casting.py` 生成；校验脚本 `tmp/v18/voice_casting_check.py`。所有角色都通过 `contract/v5/custom/characters/<id>.json` 的 `voice` 块绑定 preset，不改动 `contract/v5/characters/` 原文件，不改 outputs、不改代码。

## 1. 机制与交付

- 声音库：`contract/v5/voices.json`（24 个 breeze Voice Design preset，固定 instruction + seed + cfg_scale）。
- 绑定：`contract/v5/custom/characters/<character_id>.json`，形如 `{"id": "<cid>", "voice": {"voice_id": "<preset_id>"}}`；
  混合规则为「custom 深合并覆盖内建」（`pipeline/v5_catalog.py`），`pipeline/v5_voices.resolve_voice()` 读 `voice.voice_id` 解析完整音色。
- 选角数据：`contract/v5/voice_casting.json` → `map[character_id] = {voice_id, label, reason, preview_text}`。
- 试听文案：`preview_text` 均为角色口吻的 8–20 字中文，可直接作 `POST /api/v5/voice/{character}/preview` 的默认文本。
- 兼容别名：文件里同时提供 `characters`（与 `map` 相同），供后端 `_default_preview_text()` 读取 `preview_text`；`map` 为主结构。

## 2. 选角原则

1. **原有 24 个角色保留 V5 canonical 音色**：24 个 preset 与 24 个现代/历史角色是一一对应的（preset 中文名即角色名，如「萌新·清软」「制片·干练」），`outputs/v5_voices/` 里已有对应的声音锁与试听 wav；本次把它们显式固化到 custom 覆盖，避免内建文件后续被改动导致音色漂移。
2. **写实角色（V17 新增 7 个）重新选角**：它们原先用 edge-tts 的 `zh-CN-*` voice_id，不在 24 个 breeze preset 里，`resolve_voice()` 会抛「未知 voice_id」，角色页的声音 chip/试听/换声音全部不可用；本次从 24 个 preset 里挑人格与年龄最接近的声线，并与同模板 cast 的其他角色错开。
3. **自定义示例角色 custom_lin 也绑定 preset**：让 32 个角色都能走同一条解析链路。
4. **同模板 cast 不撞音**（第 7 节自动检查）。

## 3. 24 个 preset 一览（中文名 / 声音特点 / 使用者）

| preset id | 中文名（label） | 声音特点 | 使用角色 |
|---|---|---|---|
| `legacy_v4_laozhou` | 深夜秀主持（低沉磁性·冷幽默） | 低沉磁性、语速偏慢、冷幽默、控场 | 老周、沈舟 |
| `legacy_v4_ali` | 年轻女嘉宾（明亮清脆·俏皮） | 明亮清脆、音色偏甜、语速快、俏皮 | 阿栗 |
| `legacy_v4_dawei` | 资深男嘉宾（沉稳沙哑·严肃） | 沉稳沙哑、语速偏慢、保守严肃 | 大魏 |
| `strategist_qinglang` | 谋士·清朗 | 清朗偏薄、从容、书卷气 | 诸葛亮 |
| `emperor_yazhong` | 帝王·威压 | 低沉厚重、咬字有力、不怒自威 | 曹操 |
| `commander_dichen` | 统帅·严峻 | 偏低偏硬、风沙感、短句顿挫 | 孙武 |
| `ruler_dark` | 始皇帝·冷峻 | 极低极稳、气息沉、压迫感留白 | 秦始皇 |
| `scholar_wenrun` | 文士·温润 | 温润宽厚、语速舒缓、尾音柔和 | 苏轼 |
| `poet_haofang` | 诗人·豪放 | 明亮开敞、情绪外放、咏叹调 | 李白 |
| `empress_weiyi` | 女皇·威仪 | 低中音、稳而锋利、端肃优雅 | 武则天、周岚 |
| `heroine_sashuang` | 女将·飒爽 | 中高音、清亮有力、果断干脆 | 花木兰、李静 |
| `warrior_houzhong` | 武圣·浑厚 | 浑厚宽大、胸腔共鸣强、傲气 | 关羽 |
| `envoy_jianyi` | 使者·坚毅 | 中低音、沙哑坚定、长途风霜 | 张骞 |
| `navigator_kai` | 航海家·开阔 | 宽亮舒展、从容带笑、海风感 | 郑和、高远 |
| `historian_kezhi` | 史官·克制 | 中低音、平缓克制、咬字工整 | 司马迁 |
| `bright_female` | 甜妹·明亮 | 清亮偏甜、语速快、尾音上扬 | 甜妹、林同（示例） |
| `young_male` | 学霸·清亮 | 清亮干净、语速快、尾音利落 | 学霸、阿凯 |
| `warm_male` | 胖子·温厚 | 厚实温暖、语速偏慢、自带喜感 | 胖子、老赵 |
| `fast_male` | 卷王·机敏 | 偏亮、语速极快、节奏抢话 | 卷王 |
| `cool_male` | 冷面·冷峻 | 低冷、音量偏小、语速慢、停顿多 | 冷面、陈野 |
| `soft_male` | 萌新·清软 | 偏轻偏软、中慢速、紧张真诚 | 萌新 |
| `steady_male` | 制片·干练 | 中音稳、语速中快、职业化掌控 | 制片 |
| `robot_flat` | 机器人·平直 | 机械平直、音高稳定、无情绪 | 机器人 |
| `mystery_low` | 神秘人·低沉 | 低沉偏暗、气息多、吐字半含 | 神秘嘉宾 |

说明：V5 原有 24 个角色与 24 个 preset 是一一对应的（`outputs/v5_voices/` 里有对应的声音锁与试听 wav），本次保持这些 canonical 绑定不变；V17 的 7 个写实角色与自定义示例角色 `custom_lin` 从这 24 个里挑最贴近的声线。

## 4. 32 角色选角结果

| 角色 id | 名字 | era / 性别 / 年龄 | preset（中文名） | 声线理由 | 试听句 |
|---|---|---|---|---|---|
| `ali` | 阿栗 | 现代 / female / 24 | 年轻女嘉宾（明亮清脆·俏皮）（`legacy_v4_ali`） | V4 原声，明亮俏皮，贴 AI 创业者的语速 | 「我又想到一个新点子，要不要听听？」 |
| `caocao` | 曹操 | 历史 / male / 50 | 帝王·威压（`emperor_yazhong`） | 厚重威压、尾音下沉，枭雄的试探 | 「天下英雄，唯使君与操耳。」 |
| `custom_lin` | 林同（示例） | 自定义 / female / 28 | 甜妹·明亮（`bright_female`） | 清亮偏少女，贴年轻女学者的清爽 | 「把旧材料讲成新故事，很有意思。」 |
| `dawei` | 大魏 | 现代 / male / 52 | 资深男嘉宾（沉稳沙哑·严肃）（`legacy_v4_dawei`） | 沉沙哑而谨慎，老综艺导演的资历感 | 「我从业三十年，这话我可不敢乱说。」 |
| `guanyu` | 关羽 | 历史 / male / 45 | 武圣·浑厚（`warrior_houzhong`） | 浑厚刚正、句句落地，武圣的傲骨 | 「义字当头，关某从不含糊。」 |
| `juanwang` | 卷王 | 现代 / male / 28 | 卷王·机敏（`fast_male`） | 极快节奏、句句抢话，卷王本色 | 「第八版方案马上就好，再等我十分钟。」 |
| `laozhou` | 老周 | 现代 / male / 42 | 深夜秀主持（低沉磁性·冷幽默）（`legacy_v4_laozhou`） | V4 原声，低沉松弛带冷幽默，控场主持 | 「来，咱们把场面话放一边，聊点真的。」 |
| `lengmian` | 冷面 | 现代 / male / 35 | 冷面·冷峻（`cool_male`） | 低冷少起伏、停顿多，冷面笑匠的反差 | 「我没笑，是这事确实好笑。」 |
| `libai` | 李白 | 历史 / male / 35 | 诗人·豪放（`poet_haofang`） | 明亮外放、咏叹感，诗仙的狂放 | 「拿酒来，今朝有酒今朝醉！」 |
| `mengxin` | 萌新 | 现代 / female / 19 | 萌新·清软（`soft_male`） | 专为萌新设计：偏软、紧张又真诚 | 「那个……这样可以吗？我再改改。」 |
| `mulan` | 花木兰 | 历史 / female / 24 | 女将·飒爽（`heroine_sashuang`） | 清亮果断、尾音干脆，女将的利落 | 「多说无益，看我怎么把它做成。」 |
| `mystery_guest` | 神秘嘉宾 | 现代 / other / 30 | 神秘人·低沉（`mystery_low`） | 低沉含混、气息多，身份成谜 | 「你确定，你想知道答案吗？」 |
| `pangzi` | 胖子 | 现代 / male / 30 | 胖子·温厚（`warm_male`） | 厚实温暖自带喜感，胖子式的亲和 | 「先吃口热乎的，事儿慢慢说。」 |
| `producer` | 制片 | 现代 / female / 32 | 制片·干练（`steady_male`） | 中音稳、语速中快，制片人的干练 | 「时间不等人，各部门现在就位。」 |
| `qinshihuang` | 秦始皇 | 历史 / male / 45 | 始皇帝·冷峻（`ruler_dark`） | 极低极稳、压迫式留白，帝王冷峻 | 「朕意已决，不必再议。」 |
| `real_cafe_owner` | 周岚 | 现代 / female / 32 | 女皇·威仪（`empress_weiyi`） | 低中音稳而优雅，咖啡馆主理人的分寸 | 「先坐会儿，咖啡马上就好。」 |
| `real_cafe_regular` | 高远 | 现代 / male / 45 | 航海家·开阔（`navigator_kai`） | 宽亮从容带笑意，中年转行的松弛 | 「我啊，就爱这一口手冲的味道。」 |
| `real_host` | 沈舟 | 现代 / male / 38 | 深夜秀主持（低沉磁性·冷幽默）（`legacy_v4_laozhou`） | 低沉磁性、语速偏慢，贴记者型主持 | 「夜里慢慢走，听听别人的故事。」 |
| `real_kitchen_chef` | 老赵 | 现代 / male / 48 | 胖子·温厚（`warm_male`） | 厚实温暖、语速慢，灶台边的家常气 | 「锅里有热的，坐下吃点再走。」 |
| `real_kitchen_regular` | 阿凯 | 现代 / male / 26 | 学霸·清亮（`young_male`） | 清亮偏快、尾音利落，年轻人的懒散劲 | 「加完班，就馋这一口热汤。」 |
| `real_street_owner` | 李静 | 现代 / female / 34 | 女将·飒爽（`heroine_sashuang`） | 清亮干脆、带笑，便利店店长的爽利 | 「这么晚还来？给你留了杯热的。」 |
| `real_street_runner` | 陈野 | 现代 / male / 27 | 冷面·冷峻（`cool_male`） | 低冷少起伏、停顿多，贴疲惫夜归人 | 「刚下班，只想一个人走一走。」 |
| `robot` | 机器人 | 现代 / neutral / 3 | 机器人·平直（`robot_flat`） | 机械平直、无情绪起伏，AI 助理感 | 「数据处理完毕，结论如下。」 |
| `simaqian` | 司马迁 | 历史 / male / 45 | 史官·克制（`historian_kezhi`） | 平缓克制、咬字工整，史官的笔触 | 「此事须有据，我不敢妄书一笔。」 |
| `sunwu` | 孙武 | 历史 / male / 45 | 统帅·严峻（`commander_dichen`） | 偏硬带风沙、顿挫强，兵家的严峻 | 「先算败，再算胜，胜负已定。」 |
| `sushi` | 苏轼 | 历史 / male / 48 | 文士·温润（`scholar_wenrun`） | 温润宽厚带笑，东坡的旷达 | 「人间有味是清欢，且吃且看。」 |
| `tianmei` | 甜妹 | 现代 / female / 20 | 甜妹·明亮（`bright_female`） | 清亮带甜、尾音上扬，元气甜妹 | 「今天也超开心，你们呢？」 |
| `wuzetian` | 武则天 | 历史 / female / 38 | 女皇·威仪（`empress_weiyi`） | 低中音稳而锋利，女皇的端肃 | 「这天下的事，本宫自有分寸。」 |
| `xueba` | 学霸 | 现代 / male / 22 | 学霸·清亮（`young_male`） | 清亮利落带锐气，学霸的答辩腔 | 「第一，论点不清；第二，论据不足。」 |
| `zhangqian` | 张骞 | 历史 / male / 40 | 使者·坚毅（`envoy_jianyi`） | 沙哑坚定、气息稳，使者的路感 | 「这条路我走过，风沙大，但值得。」 |
| `zhenghe` | 郑和 | 历史 / male / 45 | 航海家·开阔（`navigator_kai`） | 宽亮舒展带笑意，航海家的开阔 | 「海那边还有天地，我们去看。」 |
| `zhuge` | 诸葛亮 | 历史 / male / 40 | 谋士·清朗（`strategist_qinglang`） | 清朗偏薄、从容，谋士的书卷气 | 「亮有一计，可解眼前之困。」 |

## 5. 写实角色（V17 新增 7 个）推荐说明

写实角色原先用 edge-tts 音色（`zh-CN-YunxiNeural` / `XiaoxiaoNeural` / `YunjianNeural`），不在 24 个 breeze preset 里，`resolve_voice()` 会报「未知 voice_id」，V18 角色页的「试听 / 换声音 / 声音 chip」也就无法工作。本次统一重新选角到 breeze preset，让 32 个角色走同一条链路（Breeze Voice Design → 声音锁 → 全站试听）。

| 角色 | 新音色 | 选型说明 |
|---|---|---|
| 沈舟（`real_host`） | 深夜秀主持（低沉磁性·冷幽默）（`legacy_v4_laozhou`） | 低沉磁性、语速偏慢而松弛：前记者出身的采访者需要「让人愿意多说两句」的声音，冷幽默底子适合街头偶遇。 |
| 陈野（`real_street_runner`） | 冷面·冷峻（`cool_male`） | 低冷、停顿多、不主动煽情：贴 27 岁加班程序员的疲惫与克制。 |
| 李静（`real_street_owner`） | 女将·飒爽（`heroine_sashuang`） | 清亮干脆、带笑：夜班店长的市井爽利，语速快但不扎人。 |
| 周岚（`real_cafe_owner`） | 女皇·威仪（`empress_weiyi`） | 低中音、稳而优雅：32 岁主理人的分寸感，音量不必高就能压住空间。 |
| 高远（`real_cafe_regular`） | 航海家·开阔（`navigator_kai`） | 宽亮舒展、带笑意：45 岁中年转行者的松弛与开阔，适合讲人生结构。 |
| 老赵（`real_kitchen_chef`） | 胖子·温厚（`warm_male`） | 厚实温暖、语速偏慢：「锅里有热的」式家常气，比沙哑更劝饭。 |
| 阿凯（`real_kitchen_regular`） | 学霸·清亮（`young_male`） | 清亮偏快、尾音利落：26 岁创意人的懒散与嘴硬，收在年轻人的节奏里。 |

补充：写实角色要求「真人感」，breeze preset 是风格化人声设计；当前先把链路跑通，如果后续需要更写实的中年男/市井女声，建议在 `contract/v5/custom/voices.json` 新增专属 preset（instruction 写明「真实生活感、无播音腔」），再把对应角色的 `voice_id` 切过去即可，无需改代码。

## 6. 自定义示例角色 `custom_lin`

`custom_lin`（林同）是 V5 自定义目录里的示例角色，原本用 inline voice（provider=breeze + instruction + seed，无 voice_id）。为了让 32 个角色都能在角色页显示「声音 chip」并试听，本次把它也绑定到 `bright_female`（清亮偏少女，最接近 28 岁女学者的清爽感），若正式启用建议单独建一个更贴「克制书卷气」的 preset。

## 7. 模板 cast 音色检查

22 个模板（19 个内置 + V17 新增 3 个写实）的 cast 内均无音色撞车：22 个模板全部通过。

| 模板 | cast（role：角色 → preset） |
|---|---|
| `classics` 典籍 | host：老周（`legacy_v4_laozhou`）；figure_a：司马迁（`historian_kezhi`）；figure_b：苏轼（`scholar_wenrun`） |
| `custom_holo_talk` 全息史料夜话（示例） | host：林同（示例）（`bright_female`） |
| `history_mystery` 历史悬案 | host：老周（`legacy_v4_laozhou`）；figure_a：张骞（`envoy_jianyi`）；figure_b：司马迁（`historian_kezhi`） |
| `history_talk` 历史夜话 | host：老周（`legacy_v4_laozhou`）；figure_a：曹操（`emperor_yazhong`）；figure_b：诸葛亮（`strategist_qinglang`） |
| `hot_ones` 热辣对话 | host：阿栗（`legacy_v4_ali`）；figure_a：甜妹（`bright_female`）；figure_b：大魏（`legacy_v4_dawei`） |
| `masked_singer` 蒙面歌手 | host：老周（`legacy_v4_laozhou`）；figure_a：阿栗（`legacy_v4_ali`）；figure_b：甜妹（`bright_female`） |
| `mystery` 悬疑现场 | host：老周（`legacy_v4_laozhou`）；figure_a：冷面（`cool_male`）；figure_b：神秘嘉宾（`mystery_low`） |
| `national_treasure` 国宝 | host：老周（`legacy_v4_laozhou`）；figure_a：苏轼（`scholar_wenrun`）；figure_b：武则天（`empress_weiyi`） |
| `qipashuo` 奇葩说 | host：老周（`legacy_v4_laozhou`）；figure_a：诸葛亮（`strategist_qinglang`）；figure_b：花木兰（`heroine_sashuang`） |
| `real_cafe` 咖啡对谈 | host：沈舟（`legacy_v4_laozhou`）；figure_a：周岚（`empress_weiyi`）；figure_b：高远（`navigator_kai`） |
| `real_kitchen` 深夜食堂 | host：沈舟（`legacy_v4_laozhou`）；figure_a：阿凯（`young_male`）；figure_b：老赵（`warm_male`） |
| `real_street` 城市夜访 | host：沈舟（`legacy_v4_laozhou`）；figure_a：陈野（`cool_male`）；figure_b：李静（`heroine_sashuang`） |
| `relic_talk` 国宝会说话 | host：老周（`legacy_v4_laozhou`）；figure_a：司马迁（`historian_kezhi`）；figure_b：苏轼（`scholar_wenrun`） |
| `relic_talk_3d` 国宝会说话 · 3D | host：老周（`legacy_v4_laozhou`）；figure_a：司马迁（`historian_kezhi`）；figure_b：苏轼（`scholar_wenrun`） |
| `roundtable` 圆桌派 | host：老周（`legacy_v4_laozhou`）；figure_a：苏轼（`scholar_wenrun`）；figure_b：李白（`poet_haofang`） |
| `shark_tank` 创投路演 | host：老周（`legacy_v4_laozhou`）；figure_a：制片（`steady_male`）；figure_b：学霸（`young_male`） |
| `slow_life` 慢生活 | host：阿栗（`legacy_v4_ali`）；figure_a：甜妹（`bright_female`）；figure_b：胖子（`warm_male`） |
| `snl_sketch` 周六夜现场 | host：大魏（`legacy_v4_dawei`）；figure_a：老周（`legacy_v4_laozhou`）；figure_b：阿栗（`legacy_v4_ali`） |
| `standup` 脱口秀 | host：老周（`legacy_v4_laozhou`）；figure_a：胖子（`warm_male`）；figure_b：冷面（`cool_male`） |
| `taskmaster` 任务大师 | host：老周（`legacy_v4_laozhou`）；figure_a：胖子（`warm_male`）；figure_b：甜妹（`bright_female`） |
| `tonight_show` 美式深夜秀 | host：老周（`legacy_v4_laozhou`）；guest_a：阿栗（`legacy_v4_ali`）；guest_b：卷王（`fast_male`） |
| `traitors` 叛徒圆桌 | host：老周（`legacy_v4_laozhou`）；figure_a：武则天（`empress_weiyi`）；figure_b：曹操（`emperor_yazhong`） |

## 8. 试听与回退

```bash
# 音色库（24 preset）
python -c "from pipeline import v5_voices; lib=v5_voices.load_voice_library(); print(len(lib['presets']))"
# 校验 32 角色绑定
python tmp/v18/voice_casting_check.py
# 角色页 API（tpl-backend 提供）：试听 / 改声音
curl -X POST http://127.0.0.1:8090/api/v5/voice/real_host/preview -H 'Content-Type: application/json' -d '{"text":"夜里慢慢走，听听别人的故事。"}'
curl -X PUT  http://127.0.0.1:8090/api/v5/voice/real_host -H 'Content-Type: application/json' -d '{"voice_id":"steady_male"}'
```

回退：删除 `contract/v5/custom/characters/<id>.json` 即恢复内建角色的原始绑定（`custom_lin` 除外，它本身就是自定义定义）。

## 9. 遗留

1. 写实角色借用现有 24 个风格化 preset，未新增写实专属 preset（可按第 5 节建议在 custom/voices.json 扩展）。
2. 24 个 canonical 角色的映射与 V5 声音锁一致，本次只做显式固化（custom 覆盖），声线本身不变；重设计集中在 7 个写实角色 + custom_lin。
3. `preview_text` 只写文案，实际试听 wav 由后端 preview API 生成（本任务不生成、不改 outputs）。
4. 若后续新增角色，需要同步更新 `voice_casting.json` 与 custom 覆盖，并跑 `tmp/v18/voice_casting_check.py`。
5. **已知性别描述问题（保留 canonical，不建议本次改）**：`mengxin`（19 岁女性）映射的 `soft_male` 与 `producer`（32 岁女性）映射的 `steady_male`，在 V5.2 建立 preset 时把 instruction 写成了男性；二者已有声音锁/试听 wav 按此生成。若用户要求严格女声，可在 `contract/v5/custom/voices.json` 新增「19 岁女性软糯怯生生」「32 岁女性干练清晰」两个 preset，再把 voice_id 换过去（无需改代码），并重生成声音锁。
6. `wuzetian` 原设计 38 岁，`empress_weiyi` 的 instruction 写的是 48 岁；气质（低中音、端肃、锋利）一致，年龄感略成熟，重生成声音锁时会体现。
7. `real_host`（沈舟）复用了 `legacy_v4_laozhou` 的低音 host 声线；两个角色不会出现在同一模板 cast 中（第 7 节已检查）。
8. **后端集成提醒（tpl-backend）**：V18 `PUT /api/v5/characters/<id>` 以 partial 覆盖写入；若「换声音」只提交 `{voice:{voice_id}}`，`real_*` 内建 JSON 里残留的 edge `instruction/rate` 会在深合并后盖掉 preset 参数。当前 custom 覆盖已显式写入 preset 的 provider/instruction/seed 兜底；建议后端保存时按 `resolve_voice()` 写完整 voice spec（或清空旧 instruction），否则写实角色换声后需要重新点一次「恢复默认/试听」才会用新 preset。

