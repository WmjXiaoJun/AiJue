# V5 MOTION（`pipeline/motion2d.py`）—— 2D 分层动画引擎

> 版本：V5.1（2026-10-26） 负责人：motion-dev
> 配套：`docs/PLAN_V5_STYLE_AUDIO.md` §3.1 / §3.2、`docs/V5_INTERFACES.md` §4（契约，签名不得改）
> 上游：`pipeline/scene_render.py`（静态分层底图，style-core-dev）
> 下游：`pipeline/render_v5.py`（逐 act 出片 → 套音轨/字幕/包装 → QC）

本文档记录：**camera 参数语义、帧生成流程、性能数据、如何被 render_v5 调用**，
以及角色动画/特效/验证脚本/已知限制。

---

## 1. 职责边界

`pipeline/motion2d.py` 是 V5 的「2D 动态镜头」渲染器，纯 CPU，不依赖 GPU / 网络 / ComfyUI。

| 属于 motion2d | 不属于 motion2d |
|---|---|
| 图层视差（推拉摇移/手持抖动）、相机缓动 | 静态底图绘制（`scene_render`） |
| 角色口型/眨眼/呼吸点头、相机跟随 | 角色立绘/风格后处理（`characters_v5`） |
| 动态特效（雨/雪/雾/烛火/粒子/速度线/暗角） | 调色/纹理/转场/字幕包装（`style_v5`） |
| 逐帧合成 + h264 编码（无音轨） | 音频合成、MuseTalk 后处理（`lipsync.py` / `render_v5`） |

产出的 mp4 **无音轨**，音频由调用方（render_v5 的 assemble 阶段）合成；
对白镜若走 2D viseme rig（本模块），口型已经是逐帧对齐的，不需要再跑 MuseTalk
（模板 `lipsync.mode = musetalk` 的对白镜则应绕开本模块或只当 B-roll）。

文件：`pipeline/motion2d.py`（唯一实现，本模块无其它文件）。
自测：`tmp/v5_motion_demo.py`、`tmp/v5_motion_demo/verify_motion.py`、
`tmp/v5_motion_demo/integration_scene_render.py`、`tmp/v5_motion_demo/profile_motion.py`。

---

## 2. 相机（camera）参数语义

### 2.1 输入结构

```python
camera = {
    "type": "push_in",     # 见下表；也接受别名 slow_push / pan_r / handheld_shake ...
    "strength": 0.08,      # 幅度（含义随 type 变化，见下表）
    "ease": "in_out",      # 缓动，默认 in_out
    "shake": 0.0,          # 附加抖动幅度（占画面短边比例），手持默认 0.008
    "parallax": 0.65,      # 视差指数（可选，默认 0.65，见 §2.5）
    "zone": "medium",      # 可选：基础取景窗（场景 camera_zones 的名字或 [x,y,w,h]），
                           #       type 的运动叠加在它上面（见 §2.3）
    "zone_from": [0,0,1,1],        # 可选：两个取景窗之间过渡（优先级最高）
    "zone_to": [0.35,0.42,0.42,0.42],
    "shake_freq": 0.9,     # 可选：抖动的基频
}
```

进度 `p = clamp(t / duration, 0, 1)`，`e = ease(p, ease)`。所有机位在整镜内单调一次到位
（没有往复/多段关键帧；多段请拆成多个 act/shot）。

### 2.2 7 种 type 的运动公式（`camera_state()` 的实现）

`W, H` = 输出宽高（默认 1280×720）；`x/y` 是**中景参考平面**在屏幕上的位移（像素，y 向下）。
若给了 `zone`（见 §2.3），`zoom/x/y` 都是**在取景窗（zone）基础上**叠加的。

| type | zoom(t) | x(t) | y(t) | 语义 / 用途 | 常用 strength |
|---|---|---|---|---|---|
| `static` | 1 | 0 | 0 | 固定机位（仅有 shake 时仍可抖） | — |
| `push_in` | `1 + strength·e` | 0 | 0 | 缓推近（强调/走近） | 0.06~0.12 |
| `pull_out` | `1 + strength·(1-e)` | 0 | 0 | 缓拉远（揭示环境） | 0.06~0.12 |
| `pan_left` | 1 | `+strength·W·e` | 0 | **相机左移** → 画面内容右移 | 0.08~0.18 |
| `pan_right` | 1 | `-strength·W·e` | 0 | **相机右移** → 画面内容左移 | 0.08~0.18 |
| `dolly` | `1 + strength·e` | `+0.12·strength·W·e` | `-0.35·strength·H·e` | 推轨：推近 + 轻微上抬/侧移 | 0.06~0.12 |
| `handheld` | `1 + 0.5·strength·e` | `0.16·strength·W·e`（+抖动） | `0.05·strength·H·sin(0.42πt)`（+抖动） | 手持：缓慢漂移 + 多频抖动 | 0.03~0.08 |

有 `zone` 时（`base = zone 取景的 zoom/dx/dy`）：

```
push_in : zoom = base.zoom·(1+strength·e)          # 在视图窗内部推近
pull_out: zoom = base.zoom·(1+strength·(1-e))      # 在视图窗内部拉远
pan_*   : x    = base.dx ± strength·W·e            # 在视图窗基准上摇移
dolly   : zoom = base.zoom·(1+strength·e)，x/y 在 base 上加漂移
handheld: zoom = base.zoom·(1+0.5·strength·e)，x/y 在 base 上加漂移+抖动
static  : 纯 zone 构图（= 固定特写/中景）
```

* `strength` 的语义**随 type 不同**：推拉 = 缩放增量（0.08 = 放大 8%）；摇移 = 中景平面
  在整镜中横向移动的比例（0.08 = 8% 屏宽）；手持 = 漂移幅度。
* 方向约定：`pan_right` 是"镜头向右摇"，所以**画面内容向左走**（`x` 为负）。
* `shake`：额外抖动，幅度 `shake·min(W,H)` 像素，由 3 个正弦叠加的平滑噪声驱动
  （`_smooth_noise`，seed 决定相位，可复现）。手持未显式给 `shake` 时默认 0.008
  （≈5.8px @720p）。

### 2.3 取景窗（zone）语义 —— 给 render_v5 的对白/空镜换景用

`zone` 就是 `scene_render` 返回的 `layers["camera_zones"]` 里的归一化矩形
`[x, y, w, h]`（0~1，相对整幅画面），可以直接传值，也可以传**名字**：

```python
# 三种写法等价于"取景到 scene.camera_zones['medium'] 这个窗"
{"type": "static",  "zone": [0.10, 0.26, 0.62, 0.70]}
{"type": "static",  "zone": "medium"}
{"type": "push_in", "strength": 0.10, "zone": "medium"}   # 在窗内推近
```

语义（`_zone_framing`）：

```
zoom = max(1, min(1/w, 1/h))        # 恰好装下整个视图窗（窗 aspect 与画面不同时多露一点边）
dx   = -(cx - W/2) · zoom           # 视图窗中心精确映射到画面中心
dy   = -(cy - H/2) · zoom
(cx, cy) = ((x + w/2)·W, (y + h/2)·H)
```

* `{"type":"push_in","zone":[...]}` = **在 zone 构图上推近**（不是整幅推近）——Lead 要求的语义。
* `zone_from` / `zone_to`：两个窗之间按 `ease` 插值（优先级高于 type 的默认曲线），
  适合 "wide → close" 这类换景推轨。两者也可写名字。
* 视图窗中心一定落在画面中心（单测 `verify_motion.py` §7.1 逐 rect 校验）。

**角色 anchor 坐标系**（重要）：

* 默认 `anchor_space = "scene"`：anchor 是**场景坐标**（与 `scene_render.anchors` 一致）。
  进入 zone 镜头后，角色按相机变换落到正确位置——若 anchor 在窗内，它就按比例出现在
  视图窗对应位置；若在窗外就是出画（符合"换景"预期），不是 bug。
* `anchor_space = "zone"`：anchor 视为**当前视图窗内**的归一化坐标
  （`world = zone_xy + local·zone_wh`），方便"这个特写里人物站在画面中偏左"这类直接编排。
* **角色大小**：`height` 是相对**最终画面**的比例，zone 的 zoom 会再把它放大
  （zone zoom≈1.4~2.2 时视觉尺寸 ×1.4~2.2）。所以换到 medium/close 窗拍对白时，
  建议 `height ≈ 0.62 / zone_zoom`（例如 medium 窗 zoom≈1.4 → height≈0.45），
  或直接按镜给 `height`/`scale`。

### 2.4 ease

`ease(p, name)`：`linear` / `in` / `out` / `in_out`(=smoothstep，默认) / `smootherstep` /
`sine` / `in_cubic` / `out_cubic` / `in_out_cubic` / `expo` / `in_expo` / `out_expo` /
`in_out_expo` / `back_out`，或自定义 `callable(p) -> p`（返回值会被 clamp 到 [0,1]）。
未知名字 → **warning + 回退 in_out**（配置错误不静默，但也不炸片）。

### 2.5 视差（parallax）与 depth

`scene_render` 返回 `layers["depth"] = {"background":0.02,"midground":0.12,"foreground":0.30}`。
每个图层的位移/缩放：

```
ratio_l = clamp((depth_l / depth_mid) ** parallax, 0.03, 3.0)     # parallax 默认 0.65
dx_l    = cam.x * ratio_l
dy_l    = cam.y * ratio_l
zoom_l  = 1 + (cam.zoom - 1) * (0.55 + 0.45 * clamp(ratio_l, 0.15, 2.2))
```

* `depth_mid` = `depth["midground"]`（缺失时用 0.12）；角色默认也贴这个平面
  （可用条目字段 `depth` 覆盖，例如让人物站在前景平面）。
* `parallax` 指数 <1 会**压低远景位移、抬升前景位移的幅度**。取 0.65 时：
  `bg:mid:fg ≈ 0.31 : 1 : 1.81`（纯线性比例会是 1:6:15，前景会飞出画面）。
  要更"真"的 2.5D 可给 1.0，要更"剪纸平"可给 0.4。
* 背景层位移最小（几乎不动）→ 天然形成"远景慢、近景快"的层次。
* 缩放也带景深差：越近的层推拉时缩放越明显（上式 `0.55+0.45·ratio`）。

实测（`verify_motion.py` §2，640×360，pan_right strength=0.16，取 t=half）：
中景位移 **-51.5px**（理论 -51.2）、前景 **-93.0px**（理论 -92.88），误差 <0.5px。

### 2.6 overscan：为什么推拉摇移不会露黑边

图层图片正好是整幅画面大小，一旦平移/缩放采样越界就会露出黑/透明边。
`prepare_layers()` 在开渲前**按整镜相机轨迹**算每层需要的外扩像素（`_overscan_needed`），
用 `np.pad(mode="edge")` 把图层四周扩边后再逐帧仿射：

```
对整镜采样 25 个时刻，取屏幕两端反算到图层坐标的最大越界量：
  lo_x = (0 - W/2 - dx)/z + W/2 ;  hi_x = (W - W/2 - dx)/z + W/2
  need_x = max(-lo_x, hi_x - W)        # 同理 need_y
  margin = ceil(need_x) + 3            # 3px 安全余量
  margin = min(margin, MAX_OVERSCAN_PX=480)
```

* 单层单边最多扩 480px（1280×720 下最猛的前景摇移 strength≈0.2 也够用）；
  超过会被夹住——极端配置下近景层的边缘内容会先"用尽"（背景仍在，不会黑屏）。
* 预扩边是**一次性**成本（`prepare_layers` ~25-45ms），逐帧零代价。
* 不透明层（`alpha` 全 255）在仿射时用 `BORDER_REPLICATE` 兜底；带 alpha 的层用
  `BORDER_CONSTANT(0)`（透明），配合 content-box 局部合成。
* 验证：`verify_motion.py` §3 对 6 种机位 × 5 个时间点扫描，画面内**零个**越界黑像素。

---

## 3. 帧生成流程

```
① prepare_layers（每镜一次，25-45ms）
   scene_render.layers ──► 统一 RGBA / 尺寸对齐(≠size 则 LANCZOS)
                        ──► 前景层按 style 做 1-2px 景深虚化（默认 1.2px，可关）
                        ──► 按整镜相机轨迹算 overscan → np.pad(edge)
                        ──► 不透明层丢弃 alpha（合成更快）；算 alpha 内容框
② 角色准备（每镜一次 + 懒加载立绘）
   characters[] ──► 眨眼时间表（3~6s 随机，0.15s）/ 呼吸相位（seed+role 决定）
                ──► 入场/离场帧表（默认 8 帧淡入淡出）
                ──► 立绘懒缓存：首次遇到 (pose, mouth, blink) 才调
                    characters_v5.render_character()（~35-60ms/张，之后命中缓存）
                ──► 口型混合立绘缓存（2-3 帧过渡 + 1 帧闭口）
③ 特效准备（每镜一次）
   style.fx / layers.meta.fx / layers.fx ──► 纹理烘焙（黑底 RGB，可环绕滚动）
   style.texture 含 vignette（或 style.grade 有）→ 自动追加暗角
④ 逐帧（每帧 12-20ms @1280×720）
   t = i/fps
   cam = camera_state(camera, t, duration, size, seed)      # 含 zone 基础构图
   base = Image.new(RGB)
   for layer in [background, midground, foreground]:
       M = 前向仿射(zoom_l, dx_l, dy_l, overscan)         # cv2.warpAffine 0.5ms
       if 不透明: base.paste(warped_rgb)                   # 整块
       else:      base.paste(warped_rgb[box], warped_a[box])  # 只贴 alpha 内容框
   for char in characters:                                # 层序：midground 之后、foreground 之前
       paste 脚底软阴影（可选）
       小图仿射 warp 立绘（含淡入淡出 alpha 系数）→ 局部 paste（0.5ms/角色）
   buf = bytearray(base.tobytes())
   frame = np.frombuffer(buf).reshape(H,W,3)
   fx.apply(frame, "back")   # 雾、背后的光晕（加色）
   fx.apply(frame, "front")  # 雨、雪、粒子、速度线、暗角（加色/乘法）
   ffmpeg.stdin.write(buf)                              # 裸 rgb24，不落盘
⑤ writer.close() → ffprobe 校验 → 返回 dict
```

编码参数：`-f rawvideo -pix_fmt rgb24 -s WxH -r fps -i pipe:0 -an -c:v libx264
-preset veryfast -crf 20 -pix_fmt yuv420p -movflags +faststart`（`veryfast/20` 取
`config.X264_PRESET/X264_CRF`）。

### 3.1 契约 API

```python
render_shot(layers, characters, camera, duration, fps, out_mp4, style, *,
            width=1280, height=720, seed=0, workdir=None) -> dict
compose_frame(layers, characters_state, camera_state, size, t=0.0, *,
              style=None, duration=3.0, seed=0) -> PIL.Image   # RGB，含动态特效
render_still(layers, characters_state, camera_state, size=(1280,720), t=0.0,
             out_png=None, *, style=None, duration=3.0, seed=0) -> PIL.Image
```

* `render_shot` 返回：`{"path","duration","fps","frames","width","height",
  "elapsed_sec","render_fps","meta"}`；`meta` 里带 `camera`（实际解析出的机位/缓动）、
  `layers`（每层 depth/是否不透明/overscan 余量）、`fx`、`characters`、`char_source`
  （`characters_v5` / `fallback`）、`char_sprites`、`compose_ms_avg/max`、`probe`
  （ffprobe 结果，QC 可直接用）、`stills`（给了 workdir 时首帧/中帧 PNG 路径）。
* `compose_frame` / `render_still` 的 `camera_state` 既可以是 `camera_state()` 解析后的
  状态 dict，也可以是 camera spec（自动按 `t/duration` 解析）；`characters_state` 可以是
  `character_state()` 结果、原始角色条目、或 `{"image": PIL, "pos": (x,y)}` 直接贴图。
* 失败统一 `raise V5Error`（若同进程已 import 过 `scene_render`，则复用它的 `V5Error` 类；
  这样 `except scene_render.V5Error` 也能抓到）。
* 其他可复用 helper：`camera_state()` / `prepare_layers()` / `character_state()` /
  `mouth_at()` / `blink_schedule()` / `normalize_fx()` / `ease()` / `reset_character_cache()`。

### 3.2 characters 条目字段（契约 §4 + 扩展）

```python
{"char": <character spec>,        # 必填，pipeline/characters_v5 的 render_character 入参
 "style": <style spec>,           # 可选，缺省用 render_shot 的 style
 "role": "host",                  # 用于 anchors 查表 / 眨眼种子
 "pose": "front",                 # front/side/point/think
 "mouth_timeline": [(t0, t1, viseme), ...],   # 或 [{"t0","t1","viseme"}]
 "mouth": "closed",               # mouth_timeline 之外的静态口型兜底
 "anchor": (0.27, 0.945),         # 归一化「脚底中心」；缺省查 layers["anchors"][role]
 "anchor_space": "scene",         # "scene"(默认) | "zone"（zone 内局部坐标）
 "scale": 1.0,                    # 高度倍率
 "height": 0.62,                  # 角色高度占画面比例（默认 0.62）
 "aspect": 0.62,                  # 宽高比
 "depth": 0.12,                   # 所在平面（缺省=中景平面）
 "blink": True,                   # 眨眼开关
 "breath": True,                  # 呼吸/点头微动开关
 "shadow": True,                  # 脚底软阴影（默认跟随 style.character_shadow，缺省开）
 "mouth_strength": 0.85,          # 可选：口型峰值幅度（0~1，缺省 style 或 1.0）
 "mouth_transition": 2,           # 可选：口型过渡帧数（默认 2）
 "fade_in": 8, "fade_out": 8,     # 可选：入场/离场帧数（0 = 关；默认 8）
 "flip": False}                   # 左右翻转（朝向）
```

### 3.2.1 口型平滑（避免 viseme 逐帧硬跳）

`mouth_plan_at(timeline, t, fps, strength)` 把离散 viseme 变成**逐帧混合方案**
`[(viseme, weight), ...]`，合成时用 `characters_v5` 的对应立绘做加权混合：

1. **2-3 帧过渡**：目标 viseme 到达后按斜坡 `0.5 → 0.78 → 1.0`（`mouth_transition=2`）
   与上一个口型/闭口混合，不再一帧跳到位；
2. **张口↔张口之间插 1 帧闭口**（`closed_gap`）：避免长时间停在 O 型嘴；
3. **峰值可调**：最终权重 = 斜坡 × `mouth_strength`（render_v5 传 **0.85**），
   峰值 0.85 表示"与闭口立绘混合 15%"，张口幅度更小、更耐看；
4. 目标为 `closed` 时 1 帧内闭口，不拖尾。

混合结果按 `(角色, pose, blink, flip, 尺寸, [(viseme, 权重/64)])` 缓存，
逐帧只用缓存数组（混合本身只在首次出现时算一次，~1ms/张），所以不影响帧率。
`MOUTH_TRANSITION_FRAMES` / `MOUTH_DEFAULT_STRENGTH` 是模块级默认值。

### 3.2.2 入场 / 离场

每个镜头开头默认 8 帧：角色从 anchor **下方 20px** 升起并 alpha `0.06 → 1` 淡入；
结尾 8 帧淡出（`_char_fade`）。可用条目 `fade_in`/`fade_out`（帧数，0=关）或
`style["character_fade"] = false / {"in": 8, "out": 8}` 覆盖。
`meta.characters[*].fade_in/out` 会记录实际生效值，方便 QC 与后续调整。

角色立绘按 `(char_id, pose, viseme, blink, 立绘尺寸, flip, style_id)` **懒缓存**：
一镜里最多 7 viseme × 2 blink × 1 pose = 14 张/角色，首次遇到才渲染（~35-60ms/张），
之后逐帧零成本；同一进程内（render_v5 连续渲 16 部）跨镜复用。

口型：`mouth_timeline` 是**半开区间** `[t0, t1)`，找不到就回落 `mouth`（默认 closed）。
建议由 `characters_v5.viseme_timeline(text, duration, lead=0.10)` 生成，直接就是 dict 形态。

眨眼：`blink_schedule(duration, seed, role)` 每 3~6s 一次、每次 0.15s，seed+role 决定相位，
可复现；`is_blinking()` 判定当前帧。

呼吸/点头（`char_micro_motion`，逐帧 ±1~2px，只做平移不重绘立绘）：

```
dx = 0.9·sin(2πt/6.7 + φ2)
dy = -1.5·sin(2πt/4.2 + φ1)                # 呼吸：肩部起伏
dy += 1.2·sin(2πt/2.6 + φ3)   # 仅在说话（mouth != closed）时点头
```

### 3.3 characters_v5 对接与 fallback

* 首次使用时 `from . import characters_v5` 并检查 `render_character`；
  调用 `render_character(char, style, size, pose=..., mouth=..., blink=...)`
  （先 kwargs，`TypeError` 时再按位置参数调一次，兼容不同实现）。
* 模块缺失 / 导入失败 / 渲染抛错 → **自动回退**内置占位角色
  （`_draw_placeholder_character`，纯 PIL 程序化半身像：肤色/发色/服装/胡须/冠/羽扇 +
  7 种口型 + 闭眼），并在 `meta.char_warnings` 记录原因、`meta.char_source` 标 `fallback`。
* 同一进程内新出现的 `characters_v5` 会在下一次 `render_shot` 时重试导入
  （`retry_characters_v5`），方便并行开发时热接。
* 立绘尺寸：`round(w_nom·CHAR_SPRITE_SUPERSAMPLE)`，`SUPERSAMPLE=1.12`，
  逐帧用仿射缩回（推近时不糊）。

---

## 4. 特效层（fx）

### 4.1 配置来源与优先级

```
style["fx"]  →  layers["meta"]["fx"]  →  layers["fx"]
```
三者都会被 `normalize_fx()` 合并（同名只取第一个，未知类型 warning + 忽略）。
支持三种写法：

```json
"fx": ["rain", "vignette"]
"fx": {"rain": {"weight": 1.0, "speed": 640}, "fog": {"weight": 0.9}}
"fx": [{"type": "glow", "x": 0.885, "y": 0.615, "radius": 0.155, "color": "#ffb347"}]
```

### 4.2 类型与参数

| type | 参数 | 实现（逐帧代价 ~0.1-0.5ms） |
|---|---|---|
| `rain` | `speed`(px/s, 默认 620) `weight` `phase` | 预烘雨丝纹理（黑底，可上下环绕），按 `speed·t` 滚动切片 + `cv2.addWeighted` 加色 |
| `snow` | `speed`(默认 70) `weight` `drift` | 雪点纹理，纵向滚动 + 整数像素横向漂移（`np.roll`） |
| `dust`/`particles` | `speed`(26) `weight` | 浮尘/微粒，向上飘 |
| `embers` | `speed`(26) | 暖色火星（向上飘） |
| `fog`/`mist` | `speed`(12) `weight` `layer` | 低分辨率高斯雾纹理放大，纵向缓移 + 亮度呼吸，默认画在角色**后面** |
| `glow`/`candle_glow`/`halo` | `x`,`y` 或 `anchor`、`radius`(占短边比例)、`color`、`weight`、`flicker`、`layer` | 径向光晕贴图，按 `1 - flicker/2·(1+sin(6.1t))` 闪烁，局部加色 |
| `speed_lines` | `weight` | 从画面中心放射的速度线纹理，`weight` 随时间脉动 |
| `vignette` | `strength`(0~1) | 预烘乘性暗角遮罩（1/8 降采样放大），`cv2.multiply` |

要点：
* 所有粒子类特效**不逐帧画图元**，而是"预烘纹理 + 滚动切片 + 加色混合"，
  纹理四周做环绕复制（`_fx_canvas`）所以滚动无缝。
* 加色（add）而非 alpha 混合：雨雪/光晕天然偏亮，叠在暗背景上质感更好且只要一次
  `cv2.addWeighted`（0.1-0.5ms/层）。
* `layer: "back"` 可把任意特效放到角色之后（默认雨雪粒子在角色之前、雾在之后）。
* 逐帧特效开关不需要改代码：`style["fx"]` 里删掉即可；也可以给 `style` 传 `"fx": []`。

### 4.3 前景景深虚化（硬边 → 软焦）

`prepare_layers(..., style=...)` 会对**前景层**做高斯虚化（默认 1.2px，一次烘焙，逐帧零成本），
让前景遮挡层不再"硬边剪纸感"。配置优先级：

```json
"foreground_blur": 1.6          // style 里直接给半径（px）
"dof": {"foreground_blur": 1.6} // 或放在 dof 里
"dof": true / false             // true → 1.2px，false → 关
// 都不给 → 非不透明前景默认 1.2px，整幅不透明前景默认 0
```

`meta.layers[*].blur_px` 记录实际生效值。单测见 `verify_motion.py` §7.6
（blur=2.0 时边缘峰值梯度 240 → 46）。

### 4.4 自动暗角

`style["texture"]` 里出现 `vignette`，或 `style["grade"]` 文本里出现 `vignette`，
且 `style["fx"]` 没有显式配置暗角时，自动追加
`{"type": "vignette", "strength": style.get("vignette_strength", 0.25)}`
（强度也可用 `style["vignette"]` 直接给数字）。`meta.fx` 里会显示 `vignette`。
显式配置优先，不会叠加两次。

---

## 5. 性能数据

### 5.1 环境

| | |
|---|---|
| CPU | 12th Gen Intel i9-12900KF（24 线程）/ 64GB RAM |
| OS / Python | Windows / Python 3.11.9 |
| 依赖 | Pillow 11.3、numpy 2.3.5、OpenCV 4.10（仅 CPU，`warpAffine`）、ffmpeg 6.1 static（libx264） |
| 分辨率 / 帧率 | 1280×720 / 25fps / h264 yuv420p / 无音轨 |
| GPU | 不使用 |

### 5.2 单帧耗时分解（1280×720，稳态，`tmp/v5_motion_demo/profile_motion.py`）

| 步骤 | 耗时 |
|---|---|
| `Image.new` 画布 | 0.7 ms |
| 背景层（不透明）warp + 整块 paste | 0.9 + 0.13 ms |
| 中景层 warp rgb + warp alpha + 内容框 paste（覆盖 31%） | 0.9 + 0.33 + 1.7 ms |
| 前景层（整幅覆盖）warp×2 + paste | ~2.5 ms |
| 2 个角色：状态解析 + 小图 warp + 局部 paste + 阴影 | ~2.0 ms |
| `tobytes + bytearray`（PIL→ffmpeg 直通） | 1.3 ms |
| fx（rain 一层 / fog+暗角） | 0.4 / 1.0 ms |
| **`_compose_base` 合计** | **11.8~14 ms/帧** |
| 编码（libx264 veryfast + 管道） | 1~3 ms/帧（与内容复杂度相关） |

### 5.3 整镜实测

**Mock demo（`tmp/v5_motion_demo.py`，3×8s = 600 帧）**

| 镜头 | 帧数 | 用时 | 渲染速度 | 合成均值 | fx |
|---|---|---|---|---|---|
| `01_push_in_rain.mp4` | 200 | 4.52s | **44.3 帧/秒** | 18.2 ms/帧 | rain + 自动暗角 |
| `02_pan_right_fog.mp4` | 200 | 3.67s | **54.5 帧/秒** | 14.2 ms/帧 | fog + vignette |
| `03_handheld_glow.mp4` | 200 | 3.91s | **51.2 帧/秒** | 14.6 ms/帧 | rain + glow + 自动暗角 |
| **合计** | **600** | **12.09s** | **49.6 帧/秒** | — | 目标 ≥25 帧/秒 |

> 同一机器多次运行有 ±10~15% 波动（Windows 调度/CPU 频率）；每次以 `timings.json` 为准。

> demo 已包含：口型平滑（strength 0.85）、角色 8 帧淡入淡出、前景 1.6px 虚化、自动暗角。

**真实 scene_render 对接（`tmp/v5_motion_demo/integration_scene_render.py`）**

| 镜头 | 场景 | 帧数 | 用时 | 渲染速度 | 合成均值 |
|---|---|---|---|---|---|
| ancient_map_push（push_in + 雨） | 古地图 | 150 | 3.64s | 41.2 帧/秒 | 20.1 ms/帧 |
| ancient_map_zone（dolly wide→detail 窗 + 雾） | 古地图 | 125 | 2.44s | 51.3 帧/秒 | 15.5 ms/帧 |
| ancient_map_zone_push（**push_in + zone=medium**） | 古地图 | 100 | 2.19s | 45.8 帧/秒 | 16.7 ms/帧 |
| battlefield_pan（pan_right + 雨） | 战场 | 150 | 2.93s | 51.2 帧/秒 | 15.0 ms/帧 |
| battlefield_handheld（手持 + 光晕） | 战场 | 125 | 2.32s | 53.9 帧/秒 | 15.0 ms/帧 |
| **合计** | — | **650** | **14.1~14.8s** | **44~48 帧/秒** | — |

`scene_render.render_scene_layers` 本身 160-270ms/场景（1600×900 内部分辨率），
`prepare_layers` 25-45ms/镜，均摊到 5-8s 镜头可忽略。

`scene_render.render_scene_layers` 本身 160-260ms/场景（1600×900 内部分辨率），
`prepare_layers` 25-45ms/镜，均摊到 5-8s 镜头可忽略。

### 5.4 结论与优化记录

* **达标情况**：单镜 1280×720 稳定 **43~70 帧/秒**，远超「≥25 帧/秒」要求；
  即 1 分钟成片 ≈ 25-35s CPU 渲染（16 部 × 40s ≈ 8-12 分钟，可接受）。
* 首镜会额外付出**角色立绘首渲**（每张 35-60ms，一镜最多 ~14-17 张 ≈ 0.6-1.1s），
  之后同进程跨镜命中缓存；`meta.render_fps` 是含首渲的真实值，
  `meta.compose_ms_avg` 同理，长片里会被摊薄。
* 已试过但**不采用**的方案：
  * 每帧 `PIL.save(png)` + ffmpeg 图像序列 → 磁盘 IO 直接吃掉 3-5 倍时间；
  * 全帧 `Image.alpha_composite`（3.85-4.5ms/层）→ 改为 content-box
    `paste(mask=alpha)`（0.4-1.7ms/层），省一半；
  * numpy 全帧 alpha 混合（uint16 18ms / float32 25ms）→ 太慢，只在 fx 用
    `cv2.addWeighted`（0.1-0.5ms）；
  * PIL `transform(AFFINE)` 逐层（15-22ms/层）→ 换 `cv2.warpAffine`（0.5-0.9ms/层，24 线程）。
* 无 cv2 时的回退路径（PIL transform + 全帧 alpha）仍可用，但会慢 3-5 倍，
  且大摇移时边缘质量略差（已在代码里标注 `# pragma: no cover`）。

---

## 6. 如何被 `render_v5` 调用

### 6.1 最小集成示例（逐 act 出片）

```python
from pathlib import Path
from pipeline import scene_render, motion2d, characters_v5

def render_act(act: dict, style: dict, cast: dict, scenes: dict,
               characters: dict, out_dir: Path, fps: int = 25) -> dict:
    size = (1280, 720)
    scene = scenes[act["scene"]]

    # ① 静态分层底图（style-core-dev 负责；160-260ms）
    layers = scene_render.render_scene_layers(scene, style, size,
                                              seed=act.get("seed", 0))

    # ② 组装角色：anchor 用 scene_render 给的 anchors；口型用 viseme_timeline
    dur = float(act["duration"])
    char_entries = []
    for i, item in enumerate(act.get("cast", [])):
        role = item["role"]
        char = characters[item["character"]]
        text = act.get("lines", {}).get(role, "")
        timeline = characters_v5.viseme_timeline(text, dur, lead=0.10) if text else []
        char_entries.append({
            "char": char, "style": style, "role": role,
            "pose": item.get("pose", "front"),
            "mouth_timeline": [(v["t0"], v["t1"], v["viseme"]) for v in timeline],
            "anchor": layers["anchors"].get(role, (0.5, 0.93)),
            "scale": item.get("scale", 1.0),
            "blink": True, "depth": 0.12,
        })

    # ③ 相机：模板 act.camera 直接给（别名自动归一），补 strength/ease
    camera = {"type": act.get("camera", "static"),
              "strength": act.get("camera_strength", 0.08),
              "ease": act.get("ease", "in_out"),
              "shake": act.get("shake", 0.0)}

    # ④ 换景：对白/空镜需要 medium/close 构图时，直接给 scene.camera_zones 的窗
    zone_name = act.get("camera_zone")       # 例如 "medium" / "close"
    if zone_name:
        camera["zone"] = zone_name            # {"type":"push_in", "zone":"medium"} → 窗内推近
        # 换窗后按 zone 的 zoom 回调角色高度，保持"人物在画面里的观感大小"
        z_zoom = 1.0 / max(0.05, camera_zones_zoom(scene, zone_name))
        for entry in char_entries:
            entry.setdefault("height", 0.62 * z_zoom)
    elif act.get("camera_zone_to"):
        camera["zone_from"] = act.get("camera_zone_from", "wide")
        camera["zone_to"] = act["camera_zone_to"]

    work = out_dir / f"work_{act['id']}"
    res = motion2d.render_shot(layers, char_entries, camera, dur, fps,
                               out_dir / f"shot_{act['id']}.mp4", style,
                               width=size[0], height=size[1],
                               seed=act.get("seed", 0), workdir=work,
                               mouth_strength=0.85)   # 口型峰值 85%，避免大张口
    # res: path/duration/fps/frames/elapsed_sec/render_fps/meta
    return res


def camera_zones_zoom(scene: dict, zone_name: str) -> float:
    """scene.camera_zones[zone] → 取景 zoom = max(1, min(1/w, 1/h))。"""
    rect = (scene.get("camera_zones") or {}).get(zone_name) or [0, 0, 1, 1]
    x, y, w, h = (float(v) for v in rect[:4])
    return max(1.0, min(1.0 / max(1e-3, w), 1.0 / max(1e-3, h)))
```

> 之后把 `res["path"]` 交给 assemble（拼接 + TTS 音轨 + 字幕 + ducking + loudnorm）。
> 本模块输出**无音轨**，`res["duration"] == frames/fps` 可直接用于 timeline 对齐；
> `res["meta"]["probe"]` 是 ffprobe 结果，QC 可据此检查 codec/pix_fmt/帧率/无音轨。

### 6.2 模板 act.camera 名称映射

`SUPPORTED_CAMERA_TYPES = static / push_in / pull_out / pan_left / pan_right / dolly / handheld`，
并接受别名（`CAMERA_ALIASES`）：`slow_push→push_in`、`slow_pull→pull_out`、
`zoom_in/out→push_in/pull_out`、`pan/pan_r→pan_right`、`pan_l→pan_left`、
`dolly_in→dolly`、`hand_shake/shake→handheld` 等。未知 type 会 `raise V5Error`
（不静默），请在模板里用上面 7 个名字或已知别名。

### 6.3 预览 / QC 静帧

```python
# 单帧（不编码）：给 previs、QC contact sheet、style_report 用
img = motion2d.render_still(layers, char_entries, camera, (1280, 720),
                            t=act["duration"] * 0.5, style=style,
                            duration=act["duration"], seed=0)
img.save("previs.png")

# 或者直接落盘
motion2d.render_still(layers, char_entries, camera, (1280, 720), t=2.0,
                      style=style, duration=act["duration"],
                      out_png=Path("previs/shot.png"))
```

给了 `workdir` 时 `render_shot` 会自动存**首帧与中帧** PNG
（`work/motion2d_<shot>_f000.png` / `_f100.png`）和 meta json，方便人工抽检。

> 注意：默认有 8 帧淡入，`t=0` 的静帧角色几乎不可见（这是入场动画首帧）。
> previs / contact sheet 想看到"稳定画面"，请取 `t ∈ [8/fps, duration-8/fps]`
> （例如 `t=0.5·duration`），或给条目 `fade_in=0, fade_out=0`。

### 6.4 与 lip-sync 的关系

* 对白镜走本模块（2D viseme rig）时，口型已经是逐帧 viseme 对齐的（±1 帧），
  **不需要 MuseTalk**；`render_v5` 应把这类镜头标 `lipsync = "viseme(2d)"`，
  跳过 GPU 后处理，QC 的 lip_sync 检查按"2D rig 覆盖"计。
* 模板 `lipsync.mode = musetalk` 时，对白镜应改用 H3/plate 视频，不要用本模块的对白镜；
  本模块仍可用于 B-roll / 环境镜。
* `lipsync.py` 的 `measure_sync` 也可以对本模块产出的对白镜打分（有真实音轨之后）。

---

## 7. 自测与验证

```powershell
# 1) 3×8s mock demo（自造 3 层图层 + 2 个角色 + 雨/雾/光晕）
python tmp\v5_motion_demo.py                 # --quick 只跑 1 镜 2s 冒烟
# → tmp\v5_motion_demo\*.mp4 / contact_sheet.png / camera_modes.png / timings.json

# 2) 运动学 & 契约自检（45 项：7 机位公式、视差系数、overscan、口型/眨眼/呼吸、
#    特效、render_shot 返回结构、非法参数抛错；以及 zone 取景 / zone 内推拉 /
#    anchor_space=zone / 口型平滑 / 淡入淡出 / 前景虚化 / 自动暗角）
python tmp\v5_motion_demo\verify_motion.py   # 期望：全部 PASS

# 3) 与真实 scene_render 对接（古地图/战场，真实 layers + characters_v5，
#    含 push_in+zone=medium 与 zone 内 anchor 落位）
python tmp\v5_motion_demo\integration_scene_render.py

# 4) 单帧性能分解 + cProfile
python tmp\v5_motion_demo\profile_motion.py
```

关键产物（人工抽检用）：
`tmp/v5_motion_demo/contact_sheet.png`（3 镜 × 3 时刻）、
`tmp/v5_motion_demo/camera_modes.png`（7 种机位 + 3 种 zone 用法，t=0/end 对照）、
`tmp/v5_motion_demo/integration_zone_medium.png`（真实古地图 + **push_in+zone=medium**）、
`tmp/v5_motion_demo/integration_zone_anchor.png`（anchor_space=zone 落位）、
`tmp/v5_motion_demo/integration_map_shadow.png`（真实古地图 + 双角色 + 雨）、
`tmp/v5_motion_demo/integration_battle_withcast.png`（真实战场）。

`verify_motion.py` 覆盖点：
1. 7 种 camera 的运动量/缩放与公式逐项比对（含 handheld 抖动幅度区间）；
2. 视差：marker 重心法测中景/前景位移，与 `(depth/depth_mid)^parallax` 理论值误差 <1px；
3. overscan：6 机位 × 5 时间点扫描，画面内零越界黑像素；
4. 角色动画：口型换帧/眨眼帧/呼吸微动都会改变像素；眨眼间隔与时长符合规则；
5. 特效：7 种 fx 逐帧有效；多来源 fx 合并与未知类型过滤；
6. 契约：`render_shot` 返回结构、帧数/时长/fps 精确、无音轨、非法参数与未知
   camera.type 抛 `V5Error`；
7. zone / 精修：视图窗中心必落画面中心、`{"type":"push_in","zone":...}` 在窗内推、
   pan 在窗基准上叠加、`anchor_space="zone"` 落位、口型 2-3 帧斜坡 + 张口间 1 帧闭口 +
   `mouth_strength` 峰值、淡入淡出曲线、前景虚化峰值梯度下降、自动暗角四角变暗。

---

## 8. 已知限制 / TODO

1. **无旋转/透视**：只做缩放+平移；`handheld` 用抖动位移模拟，没有 roll/俯仰。
   2.5D 的"斜视差"（每层不同 y 偏移）没做。
2. **不支持镜头内多段/往返**：一个 camera 只有一次单调运动。多段请拆 act；
   若需要 `A→B→A`，后续可加 `camera["keys"] = [[p, zoom, x, y], ...]`（TODO）。
3. **特效是贴图近似**：雨/雪不是物理粒子，不会与角色/前景遮挡交互（雨会画在前景之上）；
   需要"雨只落在背景"时可拆成两个 fx 层（`layer: back`）。
4. **角色阴影固定方向**（脚下软椭圆，向下），没有随光源移动；可用 `shadow: false` 关掉。
5. **ovserscan 上限 480px/边**：极端大摇移（strength > ~0.2 且前景 depth 很大）会先耗尽
   近景层边缘；建议 strength ≤ 0.18。
6. **角色立绘首渲成本**在短镜头里占比明显（0.6-1.1s/镜）：render_v5 建议
   （a）同角色多镜连续渲染以复用缓存，或（b）本模块后续可加"预渲整镜所有 viseme"的
   并行预热（characters_v5 是纯 Python 绘制，线程无收益，需要多进程，暂未做）。
7. **compose_frame 单独调用**（非 render_shot）时，若传的是"已解析相机状态"而不是
   camera spec，overscan 余量是按该状态反推的，可能不足以覆盖整镜轨迹；
   预览/静帧没问题，长镜头请传 camera spec 或直接 `render_shot`。
8. **zone 是"构图窗"而不是"每层独立取景"**：zoom/dx 按中景平面精确构图，背景/前景
   因视差系数不同会有轻微取景差（这是刻意的 2.5D 深度感）；若需要每层都精确对齐
   窗口，请把 `parallax` 调成 0（= 纯平移缩放，无景深差）。
9. **zone 会放大角色**：`height` 相对画面，zone zoom 会再乘上去（见 §2.3）；
   换 medium/close 窗时记得按 zoom 回调 height。
10. **淡入淡出默认开启**（每镜 8 帧）：如果成片需要"连续场景硬切"，给
    `style["character_fade"] = false` 或条目 `fade_in/fade_out = 0` 关掉。
11. 未做 HDR/色彩管理；输出 8bit sRGB，符合 25fps h264 交付要求。

---

## 9. 参考：帧数据流

```
scene_render.render_scene_layers()
        │  {background, midground, foreground} PIL RGBA
        │  depth{...} anchors{...} camera_zones{...} meta{...}
        ▼
motion2d.prepare_layers()        ← 相机轨迹 → 每层 overscan → np.pad → uint8 数组
        ▼
motion2d.camera_state()          ← (type, strength, ease, shake, parallax) → 每帧 zoom/x/y
        ▼
motion2d._compose_base()         ← cv2.warpAffine + content-box paste + 角色仿射贴图
        ▼
motion2d._FxBank.apply()         ← 加色纹理滚动 / 光晕 / 暗角
        ▼
bytearray 裸 RGB 帧 ── ffmpeg stdin ──► h264 yuv420p 25fps（无音轨）
        ▼
render_v5: assemble + TTS 音轨 + 字幕 + ducking + loudnorm → final.mp4
```

---

## 10. 变更记录

### V5.1 修订（2026-10-26，响应 Lead 4 项改进）

1. **camera.zone 语义确认 + 修正**：`zone`、`zone_from`、`zone_to` 都支持
   `scene.camera_zones` 的**名字**或归一化矩形 `[x,y,w,h]`；语义 = 归一化视图窗。
   `{"type":"push_in","zone":[...]}` 确认为**在 zone 构图上推拉**（叠加而非覆盖）；
   修正了取景窗中心偏移需乘 zoom 的问题（之前非居中窗会偏），单测逐 rect 校验
   "窗口中心必落画面中心"。新增 `anchor_space="zone"`（anchor 按 zone 局部坐标换算）。
2. **口型平滑**：`mouth_plan_at()` 实现 2-3 帧斜坡过渡（0.5/0.78/1.0）+
   相邻张口 viseme 之间插 1 帧闭口；新增 `mouth_strength`（`render_shot` 关键字、
   `style`、单角色条目三处可设，render_v5 传 **0.85**）压低张口峰值。
   混合立绘按 (viseme, 权重/64) 缓存，不影响帧率。
3. **角色入场/离场**：每镜开头默认 8 帧从 anchor 下方 20px 升起 + `alpha 0→1` 淡入、
   结尾 8 帧淡出；`fade_in`/`fade_out`/`style["character_fade"]` 可调可关。
4. **轻微景深 + 暗角**：前景层默认 1.2-1.6px 高斯虚化（`style["foreground_blur"]` /
   `style["dof"]` 可调可关）；`style["texture"]` 含 `vignette` 或 `style["grade"]` 提到
   vignette 时自动追加全画面暗角（`meta.fx` 可见，显式配置优先不重复）。

附带：角色脚底软阴影（`shadow`，可关）、`render_shot` 新增 `mouth_strength` 关键字、
`meta` 增补 `camera.zone` / `layers[*].blur_px` / `mouth` / `characters[*].fade_*`。
回归：`verify_motion.py` 45 项全 PASS；demo 600 帧 49.6 帧/秒；真实场景对接 650 帧
44.1 帧/秒、5/5 PASS。
