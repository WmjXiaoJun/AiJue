# -*- coding: utf-8 -*-
"""固定术语的多语种译法 + 字幕字体/行宽。"""

ROLES = {
    "主持人": {"en": "Host", "es": "Presentador", "ja": "司会", "ko": "진행자", "pt": "Apresentador"},
    "AI 内容创业者": {"en": "AI Content Creator", "es": "Creador de contenido con IA", "ja": "AIコンテンツ起業家", "ko": "AI 콘텐츠 창업자", "pt": "Criador de conteúdo de IA"},
    "传统综艺导演": {"en": "Traditional TV Director", "es": "Director de TV tradicional", "ja": "従来型バラエティ監督", "ko": "전통 예능 연출가", "pt": "Diretor de TV tradicional"},
    "反应镜头": {"en": "Reaction", "es": "Reacción", "ja": "リアクション", "ko": "리액션", "pt": "Reação"},
    "表情特写": {"en": "Close-up", "es": "Primer plano", "ja": "クローズアップ", "ko": "클로즈업", "pt": "Close-up"},
    "发言中": {"en": "Speaking", "es": "Hablando", "ja": "発言中", "ko": "발언 중", "pt": "Falando"},
}

SEGMENTS = {
    "开场": {"en": "Cold Open", "es": "Apertura", "ja": "オープニング", "ko": "오프닝", "pt": "Abertura"},
    "正片": {"en": "Main", "es": "Principal", "ja": "本編", "ko": "본편", "pt": "Principal"},
    "结尾": {"en": "Wrap-up", "es": "Cierre", "ja": "エンディング", "ko": "엔딩", "pt": "Encerramento"},
}

FONTS = {
    "en": "Arial",
    "es": "Arial",
    "pt": "Arial",
    "ja": "Yu Gothic",
    "ko": "Malgun Gothic",
    "zh": "Microsoft YaHei",
}

WRAP_WIDTH = {"en": 42, "es": 42, "pt": 42, "ja": 20, "ko": 20, "zh": 24}

VOICES = {
    "en": {"host": "en-US-GuyNeural", "guest_a": "en-US-AriaNeural", "guest_b": "en-US-JennyNeural"},
    "es": {"host": "es-ES-AlvaroNeural", "guest_a": "es-ES-ElviraNeural", "guest_b": "es-MX-DaliaNeural"},
    "ja": {"host": "ja-JP-KeitaNeural", "guest_a": "ja-JP-NanamiNeural", "guest_b": "ja-JP-MayuNeural"},
    "ko": {"host": "ko-KR-InJoonNeural", "guest_a": "ko-KR-SunHiNeural", "guest_b": "ko-KR-JiMinNeural"},
    "pt": {"host": "pt-BR-AntonioNeural", "guest_a": "pt-BR-FranciscaNeural", "guest_b": "pt-BR-ThalitaNeural"},
}

# 品牌与固定文案（不交给模型自由翻译）
BRAND = {
    "造片局": {"en": "Zaopianju", "es": "Zaopianju", "ja": "Zaopianju", "ko": "Zaopianju", "pt": "Zaopianju"},
}
SUBTITLES = {
    "en": "Zaopianju · AI Micro-Variety",
    "es": "Zaopianju · Micro-variedad con IA",
    "ja": "Zaopianju · AIマイクロバラエティ",
    "ko": "Zaopianju · AI 마이크로 버라이어티",
    "pt": "Zaopianju · Micro-variedade com IA",
}
