# 角色一致性（IP-Adapter）与成片升级 v1

目标：解决 SDXL 概念图「三视图/表情各自生成、脸不一致」的问题；并让成片使用 SDXL 概念角色 + 概念图驱动的 H3 动画。

## 1. 依赖（本机安装）
- 自定义节点：`ComfyUI/custom_nodes/ComfyUI_IPAdapter_plus`（cubiq，git clone）
- IP-Adapter 模型：`ComfyUI/models/ipadapter/ip-adapter-plus_sdxl_vit-h.safetensors`（847MB）
- CLIP Vision：`ComfyUI/models/clip_vision/CLIP-ViT-H-14-laion2B-s32B-b79K.safetensors`（3.9GB）
- 底座：Animagine XL 4.0（已有）
- 安装后需重启 ComfyUI（start/stop_comfyui.ps1）

## 2. imagegen 一致性模式
```
python -m imagegen.cli generate --spec contract/characters/<id>.json \
  --mode three_view|expressions --ref outputs/concepts/<id>/portrait.png \
  --outdir outputs/concepts/<id>/consistent \
  --ip-adapter-strength 0.65 --seed 42
```
- 工作流：SDXL txt2img + IPAdapterUnifiedLoader / IPAdapterAdvanced（weight 0.6–0.8, start_at 0, end_at 0.85）
- 产物：`consistent/three_view/{front,side,three_quarter}.png`、`consistent/expressions/{neutral,happy,surprised,thinking}.png`、`consistent/meta.json`
- 验收：同一角色三视图/表情与 portrait 的脸型、发色、服装、配色一致（人工抽检）；生成耗时目标 ≤30s/图。
- 必须离线可重复（固定 seed）。

## 3. H3 动画（概念图驱动）
```
python integrations/comfyui/run_h3.py --image outputs/concepts/<id>/portrait.png \
  --prompt-file integrations/comfyui/prompts/<id>_h3.txt --out outputs/h3/<id>_h3.mp4 --width 768 --height 768 --length 124
```
- 覆盖 outputs/h3/{laozhou,ali,dawei}_h3.mp4（保留 rawtest/概念 test 版本另行命名）
- 中段帧覆盖 outputs/characters/<id>/h3_portrait.png

## 4. 成片渲染优先级（pipeline 更新）
1) `outputs/h3/{cid}_h3.mp4`（H3 视频，概念图驱动）
2) `outputs/characters/{cid}/h3_portrait.png`
3) `outputs/concepts/{cid}/portrait.png`（SDXL 概念立绘，新）
4) `outputs/characters/{cid}/cutout/portrait.png`（Blender）
5) `outputs/characters/{cid}/2d/portrait.png`
6) 文字卡兜底
- 重渲 outputs/demo/final.mp4 与平台 ep_001 final.mp4；验收：299s/1280×720、blackdetect=0、画面全部为 SDXL 概念角色。
