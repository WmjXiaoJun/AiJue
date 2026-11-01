# V17：写实模板内容（real_street / real_cafe / real_kitchen）

> 三个「真人感角色 + 真实生活场景」的 V5 模板，数据层已按 `contract/v5/` 合同落盘，台词音频用 edge-tts 生成并转成 24k 单声道 wav。3D 画面由 Lead 用 `pipeline/templates3d_new_templates.py` 生成（本任务未触发 GPU）。

## 1. 交付清单

| 类别 | 文件 |
|---|---|
| 模板 | `contract/v5/templates/real_street.json` / `real_cafe.json` / `real_kitchen.json` |
| 角色 | `contract/v5/characters/` 下 7 个 `real_*.json`（1 主持 + 6 嘉宾/路人） |
| 场景 | `contract/v5/scenes/` 下 9 个写实场景（每个模板 3 个） |
| 时间线 | `outputs/templates_v5/<tid>/work/episode.json`（每模板 7 镜） |
| 音频 | `outputs/templates_v5/<tid>/work/audio/v5_001..007.wav`（24k mono）+ `<sid>.meta.json`；TTS 源文件在 `work/audio/_tts/<sid>.mp3/.vtt` |

## 2. 校验

```powershell
python -c "from pipeline import v5_catalog; c=v5_catalog.load_catalog(); print(sorted(c['templates']))"
python -c "from pipeline import v5_catalog; c=v5_catalog.load_catalog(); r=v5_catalog.validate_catalog(c); print('errors', len(r['errors']))"
```

## 3. 角色（7 个写实现代中国人）

| id | 名字 | 设定 | 年龄/性别 | 声线 | persona |
|---|---|---|---|---|---|
| `real_host` | 沈舟 | 38 岁，城市夜访 / 咖啡对谈 / 深夜食堂 三档写实节目的固定主持人。 | 38 / 男 | `zh-CN-YunxiNeural` | 前社会新闻记者，做了十年街头采访；说话慢、习惯听人把话说完，擅长把镜头拉回普通人的生活。 |
| `real_street_runner` | 陈野 | 27 岁，凌晨下班的程序员，城市夜访里的第一位路人。 | 27 / 男 | `zh-CN-YunjianNeural` | 互联网公司后端工程师，常年加班；话不多，习惯用夜跑和夜宵消化情绪。 |
| `real_street_owner` | 李静 | 34 岁，夜班便利店店长，城市夜访里的第二位路人。 | 34 / 女 | `zh-CN-XiaoxiaoNeural` | 24 小时便利店夜班店长，见过凌晨四点的各种人生；说话直，心软，习惯给人留一杯热咖啡。 |
| `real_cafe_owner` | 周岚 | 32 岁，街角咖啡馆主理人，咖啡对谈里的第一位客人。 | 32 / 女 | `zh-CN-XiaoxiaoNeural` | 独立咖啡馆主理人、手冲师；开店五年，擅长在别人说话时安静地听，再补一句让人愣住的话。 |
| `real_cafe_regular` | 高远 | 45 岁，咖啡馆常客，每周来三次的熟客。 | 45 / 男 | `zh-CN-YunjianNeural` | 建筑设计师，中年转行做木工；话多但真诚，爱把人生比作结构，说一半会自己笑。 |
| `real_kitchen_chef` | 老赵 | 48 岁，深夜食堂的掌勺，见过太多深夜来客。 | 48 / 男 | `zh-CN-YunjianNeural` | 夜宵小馆老板，做了二十多年家常菜；不爱讲道理，只劝人多吃一口，锅里永远留着热的。 |
| `real_kitchen_regular` | 阿凯 | 26 岁，深夜食堂的年轻常客。 | 26 / 男 | `zh-CN-YunxiNeural` | 广告公司创意，加班到深夜的常客；嘴上说随便，其实很挑嘴，吃完才肯说一句好话。 |

角色外观参数（hair_style / outfit / accessories / marks）写在 JSON 的 `appearance` 字段，3D 流水线的 `realistic:true` 分支会据此生成「真实年龄/发型/服装」的人物提示词。三个模板共用主持人 `real_host`（沈舟）。

## 4. 场景（9 个真实生活场景）

| id | 名称 | 画面描述 | 分层 primitive |
|---|---|---|---|
| `street_night` | 城市夜景街道 | 真实中国城市夜景街道：湿沥青反光、霓虹招牌、路灯、远处行人与车流；适合街头采访与夜归 B-roll。 | bokeh / canvas / crowd / grain / neon_signs / rain / sky_gradient / skyline / street_lamp / vignette |
| `subway_exit` | 地铁口 | 真实地铁站出入口：台阶、暖黄灯光、晚高峰后的人流、街边霓虹与广告灯箱。 | arches / bokeh / canvas / crowd / grain / light_tunnel / sky_gradient / stage_floor / vignette |
| `convenience_store` | 24 小时便利店 | 真实 24 小时便利店门口：玻璃门、店内明亮白光、货架、街边台阶，夜里是整条街最暖的一块光。 | bokeh / canvas / desk / glass_wall / grain / neon_signs / shelves / stage_floor / street_lamp / vignette |
| `cafe_corner` | 咖啡馆窗边座位 | 真实咖啡馆窗边：木桌、咖啡杯、窗外的街与树影、上午斜射的自然光，安静适合聊天。 | bokeh / canvas / curtains / desk / desk_lamp / grain / shelves / vignette / window_light |
| `cafe_counter` | 咖啡馆吧台 | 真实咖啡馆吧台：木吧台、咖啡机、吊灯、蒸汽、背后摆满豆罐与杯子，适合手冲与 B-roll。 | bokeh / canvas / desk_lamp / grain / kitchen_counter / monitor / shelves / smoke / vignette |
| `cafe_sofa` | 咖啡馆沙发角 | 真实咖啡馆沙发角：旧沙发、矮桌、暖灯、绿植，适合长时间对谈。 | bokeh / canvas / curtains / desk / desk_lamp / grain / shelves / sofa_set / vignette / window_light |
| `kitchen_home` | 家庭厨房 | 真实家庭厨房的夜晚：吊灯暖光、灶台与锅、橱柜、小餐桌，有生活痕迹的厨房。 | bokeh / canvas / desk_lamp / grain / kitchen_counter / shelves / smoke / stage_floor / vignette / window_light |
| `kitchen_table` | 厨房餐桌 | 真实厨房小餐桌：木桌、热汤热菜、头顶暖灯、蒸汽升起，适合坐下吃饭的对谈。 | bokeh / canvas / desk / desk_lamp / grain / shelves / smoke / vignette |
| `kitchen_stove` | 灶台特写 | 真实家用灶台：灶火、炒锅、升腾的热气、抽油烟机的暖光，适合做饭与食物 B-roll。 | bokeh / canvas / desk_lamp / grain / kitchen_counter / smoke / vignette |

## 5. 三个模板

### 城市夜访（`real_street`）

- 风格：`film_interview`（16mm 暖光胶片访谈）
- cast：沈舟（host / `real_host`）、陈野（figure_a / `real_street_runner`）、李静（figure_b / `real_street_owner`）
- 场景：`street_night` / `subway_exit` / `convenience_store`
- 目标时长：35s · 实际时间线：35.212s（7 镜 = 4 对白 + 3 B-roll）
- acts：6 幕（2 B-roll + 4 对白，时长 4.5–7s）；`v5_007` 是收尾 B-roll（不在 acts 内）

| 镜号 | 类型 | 场景 | 出场 | 景别 · 运镜 | 时长 | 台词（中文） | 建议英文画面描述（3D 提示词） |
|---|---|---|---|---|---|---|---|
| `v5_001` | B-roll | `subway_exit` | — | WS · 缓慢推近 | 4.80s | — | Wide establishing shot at 11pm: the stairway mouth of a subway station glows warm, a stream of tired commuters climbs up into the neon night, the camera slowly pushes in through light drizzle. |
| `v5_002` | 对白 | `street_night` | 沈舟（host） | MS · 轨道横移 | 4.94s | 这么晚还在街上走的人，心里多半都装着点事儿。 | Medium shot of a 38-year-old Chinese man in a dark wool coat standing at the curb under a street lamp, speaking to camera; wet asphalt reflects red and cyan neon signs, passersby blur in the background. |
| `v5_003` | 对白 | `street_night` | 陈野（figure_a） | MCU · 手持轻微晃动 | 5.42s | 刚下班，出来走一走，才觉得这一天属于自己。 | Close medium shot of a 27-year-old Chinese programmer in a grey hoodie and headphones, walking slowly along the wet street, neon light sliding across his tired face as he speaks. |
| `v5_004` | B-roll | `convenience_store` | — | WS · 轨道横移 | 4.60s | — | A 24-hour convenience store on a street corner, bright white interior light spilling onto the sidewalk, a customer pushes the glass door open, the camera glides past the entrance. |
| `v5_005` | 对白 | `convenience_store` | 李静（figure_b） | MCU · 缓慢推近 | 6.00s | 夜里的便利店最诚实，谁饿了、谁睡不着，全写在脸上。 | Inside the convenience store, a 34-year-old Chinese woman in a green work jacket behind the counter speaks while arranging goods; warm fluorescent light, shelves of snacks behind her. |
| `v5_006` | 对白 | `street_night` | 沈舟（host） | MS · 向左横移 | 6.14s | 所以别怕晚归，街灯和便利店的热咖啡，都替我们留着。 | Medium-wide shot of the host standing alone at the empty street corner, street lamps and the convenience store glowing behind him, he speaks to camera and gives a small smile. |
| `v5_007` | B-roll | `street_night` | — | WS · 缓慢升起 | 4.20s | — | Final wide shot rising above the night street: neon signs, wet asphalt and one late taxi passing, the city keeps its lights on. |

### 咖啡对谈（`real_cafe`）

- 风格：`film_interview`（16mm 暖光胶片访谈）
- cast：沈舟（host / `real_host`）、周岚（figure_a / `real_cafe_owner`）、高远（figure_b / `real_cafe_regular`）
- 场景：`cafe_corner` / `cafe_counter` / `cafe_sofa`
- 目标时长：34s · 实际时间线：33.844s（7 镜 = 4 对白 + 3 B-roll）
- acts：6 幕（2 B-roll + 4 对白，时长 4.5–7s）；`v5_007` 是收尾 B-roll（不在 acts 内）

| 镜号 | 类型 | 场景 | 出场 | 景别 · 运镜 | 时长 | 台词（中文） | 建议英文画面描述（3D 提示词） |
|---|---|---|---|---|---|---|---|
| `v5_001` | B-roll | `cafe_corner` | — | WS · 缓慢推近 | 4.80s | — | Morning light through a cafe window, dust motes in the air, a wooden table with two cups of coffee, the camera slowly pushes in from the window. |
| `v5_002` | 对白 | `cafe_corner` | 沈舟（host） | MS · 轨道横移 | 5.81s | 上午的咖啡馆最适合聊天，光刚好，人也刚好。 | Medium shot of a 38-year-old Chinese man in a dark coat sitting by the cafe window, warm daylight on his face, he speaks to camera while a cup of coffee steams on the wooden table. |
| `v5_003` | 对白 | `cafe_counter` | 周岚（figure_a） | MCU · 手持轻微晃动 | 5.16s | 开店五年，最常听见的一句话就是：再坐一会儿。 | A 32-year-old Chinese woman with long hair in a sage shirt stands behind the cafe counter pouring a hand-drip coffee, warm pendant light above, she looks up and speaks to camera. |
| `v5_004` | B-roll | `cafe_counter` | — | WS · 轨道横移 | 4.60s | — | A barista pulls an espresso shot behind a wooden counter, steam rises, warm pendant lights over the bar, the camera glides along the counter past cups and coffee beans. |
| `v5_005` | 对白 | `cafe_sofa` | 高远（figure_b） | MCU · 缓慢推近 | 5.23s | 每周来三次，一杯手冲坐一下午，比开会管用。 | A 45-year-old Chinese man with glasses and a goatee sits on an old cafe sofa, holding a coffee cup, afternoon light from the side, he speaks to camera with a relaxed laugh. |
| `v5_006` | 对白 | `cafe_corner` | 沈舟（host） | MS · 固定机位 | 4.94s | 咖啡不解决问题，它只是让人有勇气再想一遍。 | Medium-close shot of the host at the cafe window, coffee cup in hand, soft daylight and blurred street outside; he speaks calmly to camera, then looks out of the window. |
| `v5_007` | B-roll | `cafe_corner` | — | WS · 缓慢拉远 | 4.20s | — | Final wide shot of the cafe corner in golden afternoon light: two empty cups, a hand turning a page, dust motes drifting, the camera slowly pulls back. |

### 深夜食堂（`real_kitchen`）

- 风格：`film_interview`（16mm 暖光胶片访谈）
- cast：沈舟（host / `real_host`）、阿凯（figure_a / `real_kitchen_regular`）、老赵（figure_b / `real_kitchen_chef`）
- 场景：`kitchen_home` / `kitchen_table` / `kitchen_stove`
- 目标时长：34s · 实际时间线：33.556s（7 镜 = 4 对白 + 3 B-roll）
- acts：6 幕（2 B-roll + 4 对白，时长 4.5–7s）；`v5_007` 是收尾 B-roll（不在 acts 内）

| 镜号 | 类型 | 场景 | 出场 | 景别 · 运镜 | 时长 | 台词（中文） | 建议英文画面描述（3D 提示词） |
|---|---|---|---|---|---|---|---|
| `v5_001` | B-roll | `kitchen_stove` | — | WS · 缓慢推近 | 4.80s | — | A gas stove in a real home kitchen at night, blue flame under a wok, steam rising, warm tungsten light, the camera pushes in on the sizzling pan. |
| `v5_002` | 对白 | `kitchen_home` | 沈舟（host） | MS · 轨道横移 | 5.71s | 深夜的厨房，是这座城市最后的客厅，谁都能坐。 | Medium shot of a 38-year-old Chinese man in a dark coat standing in a warm home kitchen at night, steam and hanging lamp above the stove, he speaks to camera. |
| `v5_003` | 对白 | `kitchen_stove` | 老赵（figure_b） | MCU · 手持轻微晃动 | 4.80s | 开火就行，想吃啥说一声，锅里正热着。 | A 48-year-old Chinese chef in a dark apron stands at the stove stirring a wok, steam rising, warm hood light on his weathered face as he speaks to camera. |
| `v5_004` | B-roll | `kitchen_table` | — | WS · 轨道横移 | 4.60s | — | A wooden kitchen table with bowls of food, steam curling under a warm pendant lamp, chopsticks resting on a bowl, the camera glides across the table. |
| `v5_005` | 对白 | `kitchen_table` | 阿凯（figure_a） | MCU · 缓慢推近 | 5.23s | 加完班闻到这口热气，才觉得自己又活过来了。 | A 26-year-old Chinese man in a grey hoodie and glasses sits at the kitchen table, steam rising from the bowl in front of him, warm lamp light, he speaks to camera and smiles. |
| `v5_006` | 对白 | `kitchen_home` | 沈舟（host） | MS · 固定机位 | 5.11s | 一日三餐认真吃，就是普通人最了不起的坚持。 | Medium-close shot of the host leaning against the kitchen counter at night, warm lamp light and steam around him, he speaks to camera then looks toward the table. |
| `v5_007` | B-roll | `kitchen_home` | — | WS · 缓慢升起 | 4.20s | — | Final wide shot of the small home kitchen at night: warm light, steam, two bowls on the table, the window reflecting city lights, the camera slowly rises. |

## 6. 3D 生成建议（Lead 执行）

```powershell
# 数据校验通过后，逐个生成（会占用 ComfyUI GPU；不在此任务执行）
python -m pipeline.templates3d_new_templates --templates real_street,real_cafe,real_kitchen
```

1. `pipeline/templates3d.py::STYLE3D` 已包含三个模板的 `look/scenes/realistic:true/ambience`（由 3D 侧维护）；`scenes` 键与本文件场景 id 一致。
2. `templates3d_new_templates.py` 会先做语义 brief（本地 LLM 把中文台词转英文画面），再 1024×576 T2VA；有台词的镜头用该镜 wav 做 ref2va 原生口型。
3. 角色参考：写实角色暂无立绘资产，ref2va 用 T2VA 首帧做参考；如需更强的角色一致性，可先用 SDXL 生成写实风格角色概念图（注意 `imagegen` 默认 anime 风格，写实需调 prompt）。
4. 模板 acts 只含 6 幕；`episode.json` 额外包含 `v5_007` 收尾 B-roll（3D 流水线按 episode shots 生成即可）。
5. BGM/字幕/包装沿用 `film_interview` 风格配置；本数据不含卡片镜（3D 剪辑会直接拼接 7 个镜头）。
6. 每镜英文画面同时写入 `episode.json` 的 `shots[*].visual_en`（模板 `acts[].visual` 同步保存），可直接作为 3D 提示词或人工修订的底稿。

## 7. 音频

| 模板 | 对白镜 | 音频 |
|---|---|---|
| `real_street` | `v5_002`（沈舟） | `outputs/templates_v5/real_street/work/audio/v5_002.wav` · speech 4.34s · 镜长 4.94s |
| `real_street` | `v5_003`（陈野） | `outputs/templates_v5/real_street/work/audio/v5_003.wav` · speech 4.82s · 镜长 5.42s |
| `real_street` | `v5_005`（李静） | `outputs/templates_v5/real_street/work/audio/v5_005.wav` · speech 5.40s · 镜长 6.00s |
| `real_street` | `v5_006`（沈舟） | `outputs/templates_v5/real_street/work/audio/v5_006.wav` · speech 5.54s · 镜长 6.14s |
| `real_cafe` | `v5_002`（沈舟） | `outputs/templates_v5/real_cafe/work/audio/v5_002.wav` · speech 5.21s · 镜长 5.81s |
| `real_cafe` | `v5_003`（周岚） | `outputs/templates_v5/real_cafe/work/audio/v5_003.wav` · speech 4.56s · 镜长 5.16s |
| `real_cafe` | `v5_005`（高远） | `outputs/templates_v5/real_cafe/work/audio/v5_005.wav` · speech 4.63s · 镜长 5.23s |
| `real_cafe` | `v5_006`（沈舟） | `outputs/templates_v5/real_cafe/work/audio/v5_006.wav` · speech 4.34s · 镜长 4.94s |
| `real_kitchen` | `v5_002`（沈舟） | `outputs/templates_v5/real_kitchen/work/audio/v5_002.wav` · speech 5.11s · 镜长 5.71s |
| `real_kitchen` | `v5_003`（老赵） | `outputs/templates_v5/real_kitchen/work/audio/v5_003.wav` · speech 4.20s · 镜长 4.80s |
| `real_kitchen` | `v5_005`（阿凯） | `outputs/templates_v5/real_kitchen/work/audio/v5_005.wav` · speech 4.63s · 镜长 5.23s |
| `real_kitchen` | `v5_006`（沈舟） | `outputs/templates_v5/real_kitchen/work/audio/v5_006.wav` · speech 4.51s · 镜长 5.11s |

全部 wav 为 24kHz / 单声道 / pcm_s16le；B-roll 镜为等长静音 wav（便于 3D 生成时占位/对齐）。

