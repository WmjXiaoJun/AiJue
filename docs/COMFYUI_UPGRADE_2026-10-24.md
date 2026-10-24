# ComfyUI 本地升级与冒烟测试记录（2026-10-24）

## 1. 结论
本机 ComfyUI 已从 **0.39.0 升级到 0.42.0**（拉取最新 master，116 个提交），自定义节点已更新到最新，依赖已补齐；**模型全部在位**；SDXL、IP-Adapter、MiniMax H3 三条链路均冒烟通过；平台后端/流水线无需改动即可继续使用本地 H3。

## 2. 版本与仓库
| 项 | 升级前 | 升级后 |
|---|---|---|
| ComfyUI | 0.39.0 / c9d8a6e6 | **0.42.0 / dea870e1** |
| 前端包 | 1.55.14 | **1.57.1** |
| 工作流模板 | 0.11.77 | **0.11.96** |
| torch | 2.11.0+cu130 | 2.11.0+cu130（未变） |
| transformers | 5.8.0 | 5.8.0 |

自定义节点（均更新到 origin 最新）：
- ComfyUI-KJNodes → eaa8fee
- ComfyUI-LTXVideo → 220e1d4（补装 requirements：colour-science/diffusers/kornia/openimageio 等）
- ComfyUI-VideoHelperSuite → 4d907be
- rgthree-comfy → d7297b4
- ComfyUI-GGUF / ComfyUI_IPAdapter_plus 已是最新
- 非 git 目录 newapi_openai_compatible 未动

## 3. 模型核对（全部存在）
- diffusion_models/minimax_h3_fl2va_pruned_int8_convrot.safetensors = 19,999 MB
- loras/minimax_h3_fl2v_turbo_4step_v1.0_768p_comfyui_bf16.safetensors = 1,866 MB
- text_encoders/qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors = 14,960 MB
- vae/minimax_h3_video_vae_fp16.safetensors = 4,967 MB
- vae/minimax_h3_audio_vae_fp32.safetensors = 577 MB
- checkpoints/animagine-xl-4.0.safetensors = 6,617 MB
- ipadapter/ip-adapter-plus_sdxl_vit-h.safetensors = 808 MB
- clip_vision/CLIP-ViT-H-14-laion2B-s32B-b79K.safetensors = 3,762 MB

## 4. 冒烟测试（全部通过）
| 测试 | 命令/入口 | 结果 |
|---|---|---|
| SDXL 概念图 | imagegen.cli generate --mode portrait | ✅ 832×1216 / 14.0s（重启后再测 ali 14.1s） |
| IP-Adapter 一致性 | imagegen.cli generate --mode three_view --ref ... | ✅ 3 张 1024×1024（10.1–16.1s/张，共 38.4s） |
| 本地 H3 i2v | integrations/comfyui/run_h3.py（concept portrait，768×768/124 帧） | ✅ 5.167s / 768×768 / 24fps / h264+aac / 1,449,497 B / 1m59s |
| 节点可用性 | /object_info | ✅ 共 1,442 节点；MiniMaxH3ImageToVideo / ReferenceToVideo / IPAdapterUnifiedLoader / IPAdapterAdvanced / VHS / LTX 节点均在 |
| 服务 | 8090 / 8188 | ✅ 均 200；后端本地 H3 路径不变 |

## 5. 已知提示/可选事项
- KJNodes 的 PatchTritonVAE 需要 triton；当前未装（仅可选 HDR/VAE patch 节点），不影响本平台链路。如需消除报警可 pip install triton-windows（需匹配 torch 版本，谨慎）。
- LTXVideo 首次因缺 colour-science 等导入失败，安装其 requirements 后已恢复（0.8s 导入）。
- 自定义节点有 1 个本地提交与远端分叉（干净工作区），已用 reset --hard origin/<branch> 对齐；更新前已打 tag。

## 6. 回滚方式（备份标签）
~~~powershell
# ComfyUI 核心
cd D:\BaiduSyncdisk\wmj_work\XJ\ComfyUI
git reset --hard pre-update-20261024
# 自定义节点（示例）
cd custom_nodes\ComfyUI-KJNodes; git reset --hard pre-update-20261024
cd ..\ComfyUI-LTXVideo; git reset --hard pre-update-20261024
cd ..\ComfyUI-VideoHelperSuite; git reset --hard pre-update-20261024
cd ..\rgthree-comfy; git reset --hard pre-update-20261024
~~~
回滚核心后需 pip install -r requirements.txt 一次（前端包版本回到旧版），并重启 ComfyUI/后端。

## 7. 当前服务
- ComfyUI：http://127.0.0.1:8188（0.42.0）
- 平台后端：http://127.0.0.1:8090（常驻）
- 测试产物：tmp/upgrade_test/（SDXL portrait / consistent 三视图 / H3 升级测试片）
