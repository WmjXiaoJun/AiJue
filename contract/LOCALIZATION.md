# 多语种本地化（出海）v1

目标：同一部成片，一键产出多语种配音 + 字幕版本（不改画面），用于 YouTube/TikTok 等海外发行。

## 1. 语言与优先级
- MVP：**en（英语，主发）、es（西语）**；同时支持 **ja / ko / pt**；fr/de/ar 预留。
- 每个语言版本 = 原画面（clips 复用）+ 该语言 TTS 配音 + 该语言字幕/花字/片头片尾文案；**不做口型**（规划 P3 可选）。

## 2. 翻译
- 本地模型：**NLLB-200-distilled-600M**，路径 localize/models/nllb-200-distilled-600M，用 ComfyUI venv 的 python 跑（torch 2.11+cu130）。
- CLI（由 pipeline 调用一次/语言）：
  ~~~powershell
  D:\BaiduSyncdisk\wmj_work\XJ\ComfyUI\.venv\Scripts\python.exe -m localize.translate_cli --in units.json --out translated.json --lang en
  ~~~
- 翻译单元（JSON 数组）：episode title / subtitle、每镜 line、punchline、lower_third 角色名+身份、segment 标题、片尾 CTA。
- 人名固定罗马化：老周→Zhou、阿栗→Ali、大魏→Dawei；身份：主持人→Host、AI 内容创业者→AI Content Creator、传统综艺导演→Traditional TV Director；segment：开场→Cold Open、正片→Main、结尾→Wrap-up。
- 缓存：outputs/episodes/{eid}/localized/{lang}/translations.json（含原文+译文+provider/model/耗时），重跑命中缓存。
- 失败回退：翻译失败时保留原文并在 meta 标注，不阻塞渲染。

## 3. 配音（TTS）
- edge-tts，多语种音色映射（settings 可覆盖）：
  - en: host en-US-GuyNeural / guest_a en-US-AriaNeural / guest_b en-US-JennyNeural
  - es: host es-ES-AlvaroNeural / guest_a es-ES-ElviraNeural / guest_b es-MX-DaliaNeural
  - ja: host ja-JP-KeitaNeural / guest_a ja-JP-NanamiNeural / guest_b ja-JP-MayuNeural
  - ko: host ko-KR-InJoonNeural / guest_a ko-KR-SunHiNeural / guest_b ko-KR-JiMinNeural
  - pt: host pt-BR-AntonioNeural / guest_a pt-BR-FranciscaNeural / guest_b pt-BR-ThalitaNeural
- 每镜 TTS 必须拟合原镜头时长：TTS 过长用 atempo（0.85–1.25）压缩，过短补静音；音画总时长不变（±0.2s）。
- 混音：人声 -16 LUFS，BGM 降为原版或 -25 LUFS，loudnorm 与中文版一致。

## 4. 字幕/包装
- 字幕语言：目标语言（可选双语）；行宽按语言适配（拉丁 ~42 字符/行、CJK ~18 字/行），最多 2 行。
- 字体：拉丁 Arial / 日文 Yu Gothic / 韩文 Malgun Gothic / 中文 Microsoft YaHei（按语言）。
- 片头标题、片尾 CTA、角色名字条、段落标签同步翻译（用 translated units）。

## 5. 产物
~~~
outputs/episodes/{eid}/localized/{lang}/
  final.mp4            # 1280x720/25fps，原画面 + 目标语配音 + 目标语字幕
  narration.wav
  subtitles.srt        # 目标语软字幕
  translations.json    # 原文+译文缓存
  meta.json            # 语言/版本/时长/翻译模型/耗时/发布元数据（title/description/hashtags）
~~~

## 6. API / 数据
- 表 Localization：id、episode_id、lang、status(draft/rendering/ready/failed)、video_url、srt_url、meta(JSON)、created_at。
- POST /api/episodes/{eid}/localize {langs:["en","es"]} → 每语言一个 Job(type=localize)，status=queued；任务调用 pipeline.localize。
- GET /api/episodes/{eid}/localizations → 列表（含 video_url/srt_url/meta）。
- GET /api/localizations/{lid}。
- Settings：localize_voices（JSON，按语言覆盖音色）、localize_langs（默认 ["en","es"]）、localize_translator（nllb|mock）。
- 测试：mock 翻译/假 TTS 覆盖 API/任务/失败回退；pipeline 测试覆盖 ASS/时长拟合/voice 选择。

## 7. 验收
- ep_002 English：75.95±0.2s / 1280×720 / blackdetect 0 / 17 条台词均英文配音 / 21 条英文字幕 / 片头片尾英文 / 无崩溃。
- ep_001 English（stretch）：299s 版本同标准。
- 前端成片房可一键生成、播放、下载 mp4/srt，显示发布元数据。
