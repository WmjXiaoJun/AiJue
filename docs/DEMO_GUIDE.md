# 明早验收指南（5 分钟看完）

## 1) 启动
在仓库根目录 PowerShell：
```
.\scripts\build-frontend.ps1     # 首次或前端有改动时
.\scripts\dev.ps1                # 启动 http://127.0.0.1:8090/
```
> 如果 8090 已被占用，先关掉旧进程或改端口。

## 2) 看应用
浏览器打开 http://127.0.0.1:8090/ ，检查：
1. 项目列表 → 进入「造片局 · 样片」；
2. 编剧间：台本、段落、说话人；
3. 资产库：3 位嘉宾 + 场景卡；
4. 分镜台：52 个镜头，表格/宫格切换；
5. 生成台：点「批量生成」，看任务进度与版本；
6. 成片房：点「渲染成片」，等待完成后播放 final.mp4。

## 3) 看样例成片（无需启动服务）
直接用播放器打开：outputs/demo/final.mp4
或 `ffprobe` 看参数。

## 4) 看代码/文档
- 架构：docs/ARCHITECTURE.md
- 决策：docs/decisions.md
- API：contract/API.md
- 流水线 CLI：contract/PIPELINE_CLI.md
- 最终报告：docs/FINAL_REPORT.md
- 方案：docs/ai-variety-platform-plan.md

## 5) 一键自检
```
.\scripts\verify.ps1
```
