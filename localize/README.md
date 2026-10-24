# localize：多语种本地化（出海）

## 组件
- translator.py：NLLB-200-distilled-600M 本地翻译（zh→en/es/ja/ko/pt/fr/de/ar），人名罗马化（老周Zhou/阿栗Ali/大魏Dawei）。
- translate_cli.py：units.json → translations.json（由 pipeline.localize 调用）。
- terms.py：固定术语（角色/段落）、字幕字体与行宽、多语 TTS 音色表。
- models/nllb-200-distilled-600M：本地模型（约 2.4GB）。

## 运行环境
翻译需要 torch + transformers，使用 ComfyUI venv 的 python：
~~~powershell
D:\BaiduSyncdisk\wmj_work\XJ\ComfyUI\.venv\Scripts\python.exe -m localize.translate_cli --in units.json --out out.json --lang en
~~~

## 单元格式
~~~json
[{"key":"title","text":"AI 会取代综艺导演吗？"},{"key":"shot.s002.line","text":"今天这期……"}]
~~~
输出：
~~~json
{"ok":true,"lang":"en","model":"facebook/nllb-200-distilled-600M","translations":{"title":"Will AI Replace Variety Directors?","shot.s002.line":"..."}}
~~~

## Provider（2026-10-24 更新）
- **默认 ollama**：本机 Ollama qwen2.5:7b（http://127.0.0.1:11434），质量好、快（19 条约 5.5s、66 条约 12s、120 条约 22s）；system python 可直接跑：
  ~~~powershell
  python -m localize.translate_cli --in units.json --out out.json --lang en --provider ollama
  ~~~
- **nllb 回退**：NLLB-200-distilled-600M（localize/models，2.3GB），需 ComfyUI venv python（torch）。
- **mock**：测试用。
- 固定译法：造片局=Zaopianju、微综艺=Micro-Variety、综艺=variety show、翻车=flop；人名 Zhou/Ali/Dawei。
