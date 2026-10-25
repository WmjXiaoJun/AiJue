# 角色契约（AI 概念图 + SDXL cutout + H3）

> 角色工坊已改为 AI 生成：SDXL 概念图（立绘/三视图/表情/设定总览）+ 去白底 cutout + H3 角色动画；不再使用 SDXL/3D 建模。

## 1. Character spec
~~~json
{
  "id": "laozhou", "name": "老周", "title": "主持人",
  "persona": "...", "voice": "zh-CN-YunxiNeural", "accent_color": "#F5C842",
  "style": {"primary": "anime", "variants": ["anime"]},
  "spec": {...}
}
~~~

## 2. 产物（磁盘）
- outputs/concepts/{cid}/portrait.png：概念立绘（SDXL）；
- outputs/concepts/{cid}/three_view/{front,side,three_quarter}.png：三视图；
- outputs/concepts/{cid}/expressions/{neutral,happy,surprised,thinking}.png：表情；
- outputs/concepts/{cid}/sheet.png：设定总览；
- outputs/characters/{cid}/cutout_2d/front.png、cutout_3d/front.png：去白底全身透明 cutout（供场景合成）；
- outputs/characters/{cid}/portrait_from_concept.png：概念立绘落位；
- 访问前缀 /media/concepts/{cid}/...、/media/characters/{cid}/...。

## 3. 后端 API
```
GET  /api/projects/{pid}/characters -> Character[]
POST /api/projects/{pid}/characters {spec} -> Character
GET  /api/characters/{cid} -> Character
PATCH /api/characters/{cid} {name?,persona?,voice?,accent_color?,spec?} -> Character
POST /api/characters/{cid}/generate {modes?:[portrait,three_view,expressions]} -> Job(type=concept)
GET  /api/characters/{cid}/files -> {portrait, sheet, turntable:null, expressions, meta}
```
- 生成任务走 imagegen（SDXL/ComfyUI），产物回填 concept_* 字段；
- turntable 字段仅为兼容保留，恒为 null；
- 创建角色时在 assets 表生成 type=character 的资产，供分镜/渲染引用。

## 4. 渲染约定
- 分镜说话镜头：优先 H3 动画参考图（概念立绘/场景合成帧）；静态兜底用概念立绘或 cutout；
- 场景合成：使用 cutout_2d/front.png（2D 风格）或 cutout_3d/front.png（3D 风格），缺失回退概念立绘；
- 角色动画：H3 audio_ref（口型）/ fl2v（反应），见 contract/SHOT_PLAN_INJECTION.md；
- 不使用旧 SDXL 产物（portrait.png/sheet.png/turntable.mp4 已清理）。
