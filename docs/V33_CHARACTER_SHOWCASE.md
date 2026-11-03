# V33 附加交付：2.5D 立绘批量重生成 + 角色试镜 demo（阿栗/木兰/大魏）

> 本文件记录与 V33 试镜字幕功能同批完成的两项展示型交付。

## 1. 24 角色 2.5D 立绘批量重生成

- 方式：`POST /api/v5/characters/{id}/art {style:"stylized_25d", prompt_extra, force:true}`，prompt_extra 按合同拼「2.5D anime / 3D anime render / cel-shaded CG + 性别/年龄/发色发型/服装/配饰 + 时代」
- 备份旧图：`tmp/v33/backup_25d/<id>.png`
- 结果：**24/24 完成**，总耗时 993.1s（其中 3 条因 GPU 被并发任务占用各约 245s，其余 10–15s/条；robot 首次连接中断后重试成功）
- 产物：`assets/v5/characters/<id>/portrait_25d.png`（24 张，约 24MB），已提交
- 对比：`tmp/v33/p25d_old_vs_new.jpg`（6 角色旧/新对比）、`tmp/v33/p25d_all_24.jpg`（24 角色总览）
- 角色详情「立绘版本 → 2.5D」可直接查看

## 2. 角色试镜 demo（2 句台词 + 烧录字幕）

使用 V33 audition API（`lines[]` + `subtitles:true`，写实立绘、角色音色、H3 图生视频、白字黑边字幕）：

| 角色 | 台词 | job | 时长/大小 | 看片 |
|---|---|---|---|---|
| 阿栗 | 大家好，我是阿栗。/ 今天想跟你聊聊，怎么把一个脑洞，变成一支片子。 | `au_24f9d7b19fb5`（脚本中断前完成） | 7.29s / 968,806B | `/media/character_demos/ali_audition_sub.mp4` |
| 木兰 | 我是木兰。/ 这一场，从替父从军开始。 | `au_8c78a542741d` | 5.17s / 763,983B | `/media/character_demos/mulan_audition_sub.mp4` |
| 大魏 | 我是大魏。/ 深夜的便利店，比白天更热闹。 | `au_464cb2a1c0df` | 5.88s | `/media/character_demos/dawei_audition_sub.mp4` |

- 三条均 1024×576、含音轨、字幕烧录可见（`tmp/v33/audition_demos_sheet.jpg`）
- SRT：`tmp/v33/demos/<char>_audition.srt`（阿栗 2 条、木兰 2 条、大魏 2 条）
- 说明：demo 脚本在第三条提交后轮询时遇到本地连接中止（httpx ReadError），任务本身在后台正常完成；已从 API 取回结果

## 3. 证据
- 2.5D 对比/总览：`tmp/v33/p25d_old_vs_new.jpg`、`tmp/v33/p25d_all_24.jpg`
- 试镜帧 sheet：`tmp/v33/audition_demos_sheet.jpg`
- 字幕功能证据：`tmp/v33/frame_sub.png`（后端）、`tmp/v33/ui/`（前端 6 张）、`tmp/v33/lead/`
- 文档：`docs/V33_AUDITION_SUBTITLES.md`（功能交付）
