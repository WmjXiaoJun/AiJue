# V5.4 前端：模板「分镜 / 剪辑时间线 / 转场」视图

> 用户诉求：「每个模板还要有不同的剪辑、电影级镜头、分镜、跳转（转场），参考开源库全部集成好」。
> 数据层：`contract/v5/editorial/<template>.json`（16 份剪辑档案）+ 渲染产物
> `outputs/templates_v5/<template>/{storyboard.json,storyboard.png,storyboard.html}`。
> 计划：`docs/PLAN_V5_4_EDITORIAL.md` §5；本页把「每个模板自己的剪辑/镜头/分镜/转场」变成可见。

## 1. 页面位置与入口

V5 世界 → **模板库** → 模板卡「分镜/剪辑」按钮（或「工作流」详情里子 tab「分镜 / 剪辑」）。

深链：

| URL | 内容 |
|---|---|
| `#/v5/templates` | 模板库列表（每卡带「剪辑签名」） |
| `#/v5/templates/<id>` | 模板详情 · 工作流子 tab |
| `#/v5/templates/<id>/storyboard` | 模板详情 · **分镜/剪辑子 tab** |
| `#/v5/templates/<id>?edit=1` | 编辑模板 JSON 弹层 |

## 2. 页面结构（`frontend/src/components/v5/Storyboard.tsx`）

### 2.1 顶部统计
`N 镜 · X 景别 · Y 运镜 · Z 转场 · 硬切比例` + QC 徽标 + 「分镜大图 / storyboard.html」外链。
数字优先取 `storyboard.json` 的 `edit` 块（`edit.shots/shot_sizes/camera_types/transitions/hard_cut_ratio`），
缺失时从逐镜数据推算。

### 2.2 分镜表
- 按 act 分组（storyboard 只有 scene 时按 acts 顺序 + scene 匹配；片头/片尾卡片单独分组「片头 / 片尾卡片」）；
- 每镜卡：序号、缩略图（`thumbnail_url`；缺失回退 scene AI plate / 占位）、时长、`shot_size` 徽章（EWS/WS/MS/MCU/CU/ECU/OTS/DETAIL/INSERT/CARD，颜色区分）、`camera`、`transition_out`（硬切/溶接/淡入淡出/划像…）、`J`/`L` cut 标记、台词 role + 文本截断；
- 「预览」模式（storyboard 未生成时）：用模板 acts（scene/duration/camera/line）生成虚线卡片 + 场景 plate 缩略图，明确标注「非最终分镜」。

### 2.3 剪辑时间线
- 横向时间条：每镜宽度 ∝ 时长、颜色 = shot_size、`CARD` 灰色；
- 剪辑点标记：非 cut 转场显示标签（如「溶接」「淡入淡出」「左划」），cut 不标（竖切）；
- `J`/`L` cut 角标标在对应镜头两端；
- 顶部时间刻度（自动 0.5s 对齐，约 8 个刻度）；
- 点击/悬停镜头条 → 底部显示该镜信息（景别/时长/camera/转场/J-L/台词），并联动分镜表高亮。

### 2.4 剪辑档案（editorial）
- 来源：`GET /api/v5/editorial/{template}`；**404 时自动用 `storyboard.json` 内嵌的 `edit.profile` 兜底**（标签会显示「storyboard.edit.profile（内嵌）」）；
- 内容：pacing 4 点折线（lead_in/mid/climax/coda 倍率，SVG）+ J/L/insert 计数；camera 调色板 chips；
  transition 调色板 chips；J/L/insert/detail 比例条；opening/closing（shot/camera/hold）；
  transition_policy（默认/硬切比例/场景切换溶接/最长转场）；shot_sequence 徽章；notes。

### 2.5 模板列表「剪辑签名」
每张模板卡新增一行：`剪辑签名：9 镜 · 7 景别 · 5 运镜 · 4 转场`
- 优先 `GET /api/v5/editorial/{id}` 的 shot_sequence/camera_palette/transition_palette 计算；
- 其次 storyboard.json 的 `edit.signature` / edit 统计；
- 都没有 → 显示「尚未生成分镜数据」。

## 3. 新增后端 API（`backend/app/routers/v5_studio.py`）

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/v5/editorial/{template}` | 读 `contract/v5/editorial/<id>.json`，返回 `{ok, template, editorial, path}`；缺失 → 404 `{message: "该模板尚未生成剪辑档案", …}` |
| GET | `/api/v5/storyboard/{template}` | 读 `outputs/templates_v5/<t>/storyboard.json`，返回 `{ok, template, storyboard, shots, storyboard_png_url, storyboard_html_url, path}`；缺失 → 404 `{message: "该模板尚未生成分镜数据（重渲后可见）"}`。服务端会：① 每镜补 `thumbnail_url`（thumbnail 绝对路径必须落在该模板产物目录内；支持 shots/*.png、frames/*.png、work/storyboard/*.jpg）；② 补 storyboard.png/html URL；③ 携带 timeline.json 的 `edit` 轨（若已接入） |
| GET | `/media/v5/{template}/{file}` | 白名单扩展：`storyboard.png`、`storyboard.html`、`shots/*.png`、`work/storyboard/*.jpg|png`（仍 `resolve()` 后校验在模板目录内，防穿越） |

> 路由注册顺序：v5_studio 先于 v5_catalog（main.py 已有），因此 `/api/v5/storyboard/...` 不会被 catalog 的 `/{kind}/{id}` 通配拦截。

## 4. 数据 schema（实际产物示例）

`outputs/templates_v5/history_talk/storyboard.json`：

```json
{
  "template": "history_talk", "style": "ink_chronicle", "total": 38.13,
  "shots": [
    {"id": "v5_002", "index": 1, "type": "shot", "scene": "war_council",
     "duration": 10.13, "start": 1.4, "shot_size": "EWS", "camera": "dolly",
     "transition_out": {"mode": "dissolve", "duration": 0.5},
     "j_cut": 0.19, "l_cut": 0.0, "line": "...", "speaker": "老周",
     "thumbnail": ".../work/storyboard/v5_002.jpg"}
  ],
  "edit": {
    "shots": 5, "shot_sizes": ["EWS","MS","MCU","WS"],
    "camera_types": ["crane_down","rack_focus","tilt_down","pan_right","pull_out"],
    "transitions": ["dissolve","wipeleft","fade"], "hard_cut_ratio": 0.5,
    "j_cuts": 1, "l_cuts": 2, "inserts": 0,
    "signature": "5 镜 · 4 景别 · 5 运镜 · 2 转场",
    "profile": { "pacing": {...}, "opening": {...}, "closing": {...},
                 "camera_palette": [...], "transition_palette": [...],
                 "transition_policy": {...}, "j_cut_ratio": 0.25, ... }
  }
}
```

## 5. 构建 / 启动

```bash
# 后端（需要重启才能加载新增 2 个 GET / media 白名单）
python -m uvicorn app.main:app --port 8090 --app-dir backend

# 前端
cd frontend && npm run build          # tsc --noEmit + vite build → frontend/dist（8090 直接托管）
# 深链验证
# http://127.0.0.1:8090/#/v5/templates/history_talk/storyboard
```

构建结果（V5.4 前端）：
```
> tsc --noEmit && vite build
✓ 80 modules transformed.
dist/assets/index-CAICfLW4.css  136.92 kB │ gzip: 22.49 kB
dist/assets/index-B4zwAB_P.js   404.43 kB │ gzip: 125.00 kB
✓ built in 740ms
```

## 6. 自测证据（`tmp/v52_frontend/`）

### 6.1 截图（8090 正式后端 + 最新 dist，真实 V5.4 数据）

> 截图取自 **8090**（已加载本次新增的两个 GET）：`/api/v5/editorial/*`、`/api/v5/storyboard/*`、
> `/media/v5/<t>/storyboard.png` 均 200；8090 自动托管最新 `frontend/dist`。
> **数据状态：FREEZE（2026-10-26 20:35）——16/16 部 per-cut 重渲完成、QC PASS**（`history_mystery`
> per-cut timebase 已修复重渲）；editorial 16/16 已落盘；storyboard 16/16 已生成。
> 重拍脚本：`python tmp/v52_frontend/storyboard_shots.py http://127.0.0.1:8090`（13/13 PASS）。
> 本组截图（20:36）使用 `history_talk`；其余 15 部签名见 6.4。

| 截图 | 内容 |
|---|---|
| `screenshots/v5_storyboard.png` | 分镜视图：统计（5 镜·4 景别·4 运镜·2 转场·硬切 50%）+ 分镜表（缩略图/景别/运镜/转场/J-L/台词，按 act 分组） |
| `screenshots/v5_timeline.png` | 剪辑时间线（宽度=时长/颜色=景别/转场标记/J-L/刻度/选中信息）+ 剪辑档案（pacing 曲线、调色板、比例条、opening-closing） |
| `screenshots/v5_editorial_archive.png` | 剪辑档案区（editorial.json 或 storyboard.edit.profile 兜底） |
| `screenshots/v5_storyboard_missing.png` | 404 容错：未重渲模板「尚未生成分镜数据，重渲后可见」+ acts 预览（不白屏） |
| `screenshots/v5_templates_signature.png` | 模板列表「剪辑签名」：16 部内置模板全部显示真实 EDL 签名（FREEZE 后无「计划 · …」占位） |

### 6.2 CDP UI 断言 13/13 PASS（`evidence/storyboard_ui_check.json`）
深链自动打开分镜视图；顶部统计/7 镜卡/7 段timeline/档案/选中信息/转场+J-L 标记；3 张截图落盘；404 模板显示占位提示 + acts 预览卡。

### 6.3 API 证据 12/12 PASS（`tmp/v52_frontend/editorial_evidence.py` → `evidence/v54_api_summary.json`）
- `GET /api/v5/editorial/{t}`：16/16 模板可读，字段齐全（pacing/shot_sequence/camera_palette/transition_palette/j-l…）；custom 模板 404
- `GET /api/v5/storyboard/{t}`：已重渲模板 200（total/shots/edit、每镜 shot_size/camera/transition_out/j_cut/l_cut、thumbnail_url）；未重渲模板 404
- `/media/v5/<t>/storyboard.png|storyboard.html`、分镜缩略图 jpg：200
- media 路径穿越：404

### 6.4 FREEZE 剪辑签名核对 5/5 PASS（`tmp/v52_frontend/signature_final_check.py` → `evidence/v54_signature_final.json`）
- 模板库 17 卡渲染；**16 部内置模板全部为真实 EDL 剪辑签名**，无「计划 · …」占位；
- 签名（实测）：classics 8镜·5景别·6运镜·2转场；history_mystery 8·5·6·4；history_talk 5·4·4·2；hot_ones 8·4·4·2；
  masked_singer 7·5·4·4；mystery 8·5·5·3；national_treasure 6·5·4·2；qipashuo 8·4·6·5；roundtable 8·5·5·3；
  shark_tank 7·4·5·3；slow_life 8·6·5·3；snl_sketch 8·4·5·4；standup 6·4·6·2；taskmaster 8·4·6·4；
  tonight_show 9·4·5·4；traitors 8·5·5·2（`custom_holo_talk` 示例未在批量重渲内 → 「尚未生成分镜数据」）。

## 7. 404 容错 / 已知限制

- **404 容错常备**：未来新增模板 / 尚未重渲模板仍会走「尚未生成分镜数据」占位 + acts 预览；FREEZE 后 16/16 均已有真实数据；
- `editorial` 缺失时用 `storyboard.json` 内嵌 `edit.profile`（渲染产物自带剪辑档案），因此即使 `contract/v5/editorial` 尚未落盘也能看档案；
- storyboard 缩略图直接引用渲染产物 `work/storyboard/*.jpg`（服务端已做目录内校验），重渲会替换同名文件；
- 未重渲模板的「分镜表」为 acts 预览（虚线卡片），不是最终 EDL；
- 新增两个 GET 需后端重启一次才生效（FREEZE 时 8090 已重启并验证 200；dist 已是新 build）；
- 模板列表卡的「剪辑签名」：有 storyboard 取实际 EDL 签名；仅 editorial 时显示「计划 · …」；两者都无则「尚未生成分镜数据」。`custom_holo_talk` 示例不在 16 部批量重渲内，保持占位。
