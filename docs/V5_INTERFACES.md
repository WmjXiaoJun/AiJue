# V5 INTERFACES（模块契约，实现者勿改签名）

> 配套：`docs/PLAN_V5_STYLE_AUDIO.md`。所有模块在 `pipeline/` 下，Python 3.11，Windows。
> 约定：函数失败 raise `V5Error`（或 ValueError），**不静默兜底**；产物写 `tmp/v5_*` 或 `outputs/v5_*`。

## 0. 数据契约（`contract/v5/`）

```
styles/{style_id}.json      # 风格
scenes/{scene_id}.json      # 场景
characters/{char_id}.json   # 角色
props/{prop_id}.json        # 道具
templates/{template_id}.json# V5 模板
packs/{pack_id}.json        # 风格包
schema/*.schema.json
custom/...                  # 用户覆盖（同名深合并，数组替换）
```

最小字段（校验器以 `contract/v5/SCHEMA.md` 为准）：

### style
```json
{"id":"ink_chronicle","label":"水墨史书","version":1,
 "technique":["ink_wash","rice_paper"], "palette":{"bg":["#efe6d3","#d9cbb0"],
 "fg":["#1c1a16","#3a352c"],"accent":["#b23a2e","#c9a227"],"paper":"#efe6d3"},
 "texture":["paper_grain","ink_bleed"], "line":{"style":"brush","width":3,"color":"#1c1a16"},
 "character_treatment":"ink_brush",
 "typography":{"title_font":"serif","subtitle":"variety_ink","lower_third":"ink_seal"},
 "transition":"ink_wipe", "audio":{"bgm":"guqin","sfx":"taiko"},
 "h3":{"style_prompt":"traditional chinese ink-wash painting, rice paper, dry brush"}
}
```

### scene
```json
{"id":"ancient_map","label":"古地图","tags":["history","ink"],"aspect":[16,9],
 "layers":[{"type":"paper"},{"type":"terrain_contours","seed":11},
           {"type":"route_arrows","color":"#b23a2e"},{"type":"vignette"}],
 "camera_zones":{"wide":[0,0,1,1],"detail":[0.25,0.2,0.5,0.55]},
 "anchors":{"host":[0.18,0.9]},"variants":["campaign","treaty"]}
```
`layers[].type` 由 `pipeline/scene_render.py` 注册的 primitive 决定（见 §1）。

### character
```json
{"id":"zhuge","label":"诸葛亮","persona":"...","role_pool":["host","figure_a"],
 "voice":{"provider":"breeze","instruction":"40岁男性，清朗沉稳，略带沙哑的书卷气",
          "seed":20262001,"cfg_scale":4.0},
 "appearance":{"gender":"male","age":40,"skin":"#f0d8c0","hair":"#1a1a1a",
   "hair_style":"ancient_guan","eye":"#2a2a2a","brow":"long","beard":"long_goatee",
   "outfit":{"type":"hanfu","main":"#efe8d8","accent":"#7a1f1a","hat":"lun_jin"},
   "accessories":["feather_fan"],"head_ratio":1.1,"chibi":0.2},
 "poses":["front","side","point","think"],"mouth":["closed","a","o","e","i","u"],
 "style_overrides":{"ink_chronicle":{"line":4,"shade":0.6}}}
```

### template
```json
{"id":"history_ep01","label":"官渡之战","style":"ink_chronicle","target_duration":40,
 "cast":[{"role":"host","character":"laozhou"},{"role":"figure_a","character":"caocao"},
         {"role":"figure_b","character":"zhuge"}],
 "acts":[{"id":"cold_open","duration":8,"scene":"ancient_map","camera":"slow_push",
          "line":"host","beats":["展开地图","手指官渡"]},
         {"id":"war","duration":10,"scene":"battlefield","camera":"pan_right",
          "characters":["caocao"],"line":"figure_a"},
         {"id":"close","duration":6,"scene":"war_council","camera":"static","line":"host"}],
 "packaging":{"subtitle":"ink","transition":"ink_wipe","title":"官渡之战"}}
```

## 1. `pipeline/scene_render.py`

```python
class V5Error(RuntimeError): ...

def primitive(name: str) -> Callable:                # 注册表
def register(name: str, fn): ...

def render_scene_layers(scene: dict, style: dict, size: tuple[int, int],
                        seed: int = 0) -> dict:
    """返回 {
      "background": PIL.Image(RGBA, size),   # 天空/远景/纸纹
      "midground":  PIL.Image(RGBA, size),   # 建筑/地形/道具
      "foreground": PIL.Image(RGBA, size)|None,  # 前景遮挡（窗框/桌角/雾）
      "depth": {"background":0.02,"midground":0.12,"foreground":0.3},  # 视差系数
      "anchors": {role: (x, y)},             # 归一化，角色脚底锚点
      "camera_zones": {zone: [x,y,w,h]},     # 归一化取景窗
      "meta": {"scene": id, "style": id, "variants": [...]}
    }"""
```
要求：
- 纯 PIL/numpy 绘制，不依赖 GPU/网络；单场景渲染 ≤ 1.5s（1600×900 内部分辨率）；
- 图层必须可移动（parallax），前景层透明区域足够；
- 所有颜色取自 `style["palette"]`；未知 `layer.type` 抛 V5Error；
- 内置 primitive（最小集合）：`paper` `canvas` `sky_gradient` `mountains` `terrain_contours`
  `rivers` `city_markers` `route_arrows` `compass_rose` `seal_stamp` `bamboo_slips` `scroll`
  `lanterns` `columns` `arches` `screens` `desk` `shelves` `glass_case` `spotlights`
  `neon_signs` `skyline` `banners` `soldier_silhouettes` `horses` `spears` `smoke`
  `rain` `snow` `candle_glow` `particles` `vignette` `grain` `halftone` `grid` `blueprint_lines`
  `brick_wall` `torn_paper` `stickers` `clay_shapes` `chalk_lines` `hologram_grid` `film_border`

## 2. `pipeline/style_v5.py`

```python
def load_catalog(root=None) -> dict:      # {"styles","scenes","characters","props","templates"}
def get_style(catalog, style_id) -> dict
def style_prompt(style) -> str            # H3/图像生成用
def apply_finish(img: PIL.Image, style: dict, kind: str = "plate") -> PIL.Image
def grade_filter(style) -> str            # ffmpeg 调色链
def packaging_filters(style, workdir, width, height, font_file) -> str
def subtitle_ass(style, events, out_ass, width, height) -> dict   # 风格化字幕
def title_card(style, text, out_png, size=(1280,720), subtitle="") -> dict
def end_card(style, text, out_png, ...)
def transition(style, **kwargs) -> tuple[str, str]   # (mode, style) → ffmpeg xfade 参数
```

## 3. `pipeline/characters_v5.py`

```python
def load_characters(root=None) -> dict
def render_character(char: dict, style: dict, size: tuple[int,int],
                     pose: str = "front", mouth: str = "closed",
                     blink: bool = False) -> PIL.Image   # RGBA
def mouth_states() -> list[str]        # ["closed","a","o","e","i","u","m"]
def viseme_from_text(text: str) -> list[dict]  # [{"char":..,"pinyin":..,"viseme":..}]
def viseme_timeline(text: str, duration: float, lead: float = 0.10) -> list[dict]
    # [{"t0":..,"t1":..,"viseme":..}] 绝对秒；中文按 pypinyin 声母/韵母映射
def render_pose_sheet(char, style, out_png, size=(1600,900)) -> dict   # contact sheet
```

## 4. `pipeline/motion2d.py`

```python
def render_shot(layers: dict, characters: list[dict], camera: dict,
                duration: float, fps: int, out_mp4: Path,
                style: dict, *, width=1280, height=720, seed=0,
                workdir=None) -> dict:
    """分层视差 + 角色动画 + 特效 → mp4（h264，无音轨；音频由调用方合成）。
    camera: {"type":"push_in|pan_left|pan_right|static|dolly","strength":0.08,
             "ease":"in_out","shake":0.0}
    characters: [{"char": spec, "style":..., "role":"host","pose":"front",
                  "mouth_timeline":[(t0,t1,viseme),...],"anchor":(x,y),
                  "scale":1.0,"blink":True}]
    返回 {"path":str, "duration":..., "fps":..., "frames":N, "meta":{...}}
    """
def compose_frame(layers, characters_state, camera_state, size, t) -> PIL.Image
def render_still(layers, characters_state, camera_state, size, t=0.0, out_png=None)
```

## 5. `pipeline/lipsync.py`（由 lipsync-dev 实现）

```python
def available() -> dict            # {"musetalk": bool, "wav2lip": bool, "syncnet": bool}
def sync_clip(clip_mp4, audio_wav, out_mp4, *, provider="musetalk",
              workdir=None, device="cuda") -> dict
    # {"provider","ok","face_found","frames","elapsed_sec","vram_peak_mb",
    #  "before":str|None,"after":str|None}
def measure_sync(clip_mp4, audio_wav) -> dict  # {"lse_c":..,"lse_d":..} 或 {"ok":false}
```

## 6. `pipeline/render_v5.py`（Lead 实现）

```python
def run_render(args) -> int   # CLI: python -m pipeline.cli render_v5 --template <id> --duration 40
# 流程：load catalog → plan(acts) → TTS(V4) → 逐 act: scene layers → motion2d → (对白镜 lipsync) →
#       assemble(时间线/字幕/ducking/两遍 loudnorm) → QC → style_report
```

## 7. QC 新增

- `style_report.json`：{style, palette, technique, scene 使用列表, 图层/角色统计, 风格自检}
- `lip_sync` check：per-shot before/after LSE-C；门限见 PLAN §6
- `distinctness`：全片调色直方图/边缘密度指纹（供 16 部两两对比，QA 用）
