"""pipeline.localize 多语种本地化测试（mock 翻译/TTS）。"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from pipeline import config, localize, utils  # noqa: E402

FONT_STUB = {
    "body": {"file": "fonts/Deng.ttf", "name": "Deng.ttf", "family": "DengXian"},
    "display": {"file": "fonts/msyhbd.ttc", "name": "msyhbd.ttc",
                "family": "Microsoft YaHei"},
}


def _episode() -> dict:
    return {
        "episode": {"id": "ep_test", "no": 2, "title": "AI 做的综艺，观众看得出来吗？",
                    "target_duration": 20, "resolution": "1280x720", "fps": 25},
        "segments": [{"id": "open", "name": "开场"}, {"id": "main", "name": "正片"},
                     {"id": "close", "name": "结尾"}],
        "assets": [
            {"id": "a_host", "role": "host", "type": "character", "name": "老周",
             "title": "主持人", "accent_color": "#F5C518", "voice": "zh-CN-YunxiNeural"},
            {"id": "a_g1", "role": "guest", "type": "character", "name": "阿栗",
             "title": "AI 内容创业者", "accent_color": "#5AC8FA", "voice": "zh-CN-XiaoxiaoNeural"},
            {"id": "a_g2", "role": "guest", "type": "character", "name": "大魏",
             "title": "传统综艺导演", "accent_color": "#FF6B6B", "voice": "zh-CN-YunjianNeural"},
        ],
        "shots": [
            {"id": "s001", "no": 1, "type": "title_card", "duration": 3.5, "segment": "open",
             "title": "AI 做的综艺，观众看得出来吗？", "subtitle": "造片局 · AI 微综艺 · 第 002 期",
             "speaker": None, "line": "", "punchline": "", "lower_third": None},
            {"id": "s002", "no": 2, "type": "host_to_camera", "duration": 5.0, "segment": "main",
             "speaker": "a_host", "speaker_name": "老周",
             "line": "今天这期，我们聊一个更狠的问题。",
             "lower_third": {"name": "老周", "title": "主持人"}, "punchline": ""},
            {"id": "s003", "no": 3, "type": "shot_reverse", "duration": 4.0, "segment": "main",
             "speaker": "a_g1", "speaker_name": "阿栗",
             "line": "我先说结论——看不看得出不重要。",
             "lower_third": {"name": "阿栗", "title": "AI 内容创业者"}, "punchline": ""},
            {"id": "s004", "no": 4, "type": "reaction", "duration": 2.5, "segment": "main",
             "speaker": "a_g2", "speaker_name": "大魏", "line": "",
             "lower_third": {"name": "大魏", "title": "传统综艺导演"}, "punchline": ""},
            {"id": "s005", "no": 5, "type": "end_card", "duration": 3.5, "segment": "close",
             "title": "", "subtitle": "造片局 · AI 微综艺 · 下期见",
             "speaker": None, "line": "", "punchline": "", "lower_third": None},
        ],
    }


# ---------------------------------------------------------------- units ----
def test_collect_units_skips_names_and_packaging():
    units = localize.collect_units(_episode())
    keys = [u["key"] for u in units]
    texts = {u["key"]: u["text"] for u in units}
    # 台词/标题/段落都要翻
    assert "episode.title" in keys
    assert "shot.s002.line" in keys
    assert "segment.main" in keys
    assert texts["segment.main"] == "正片"
    # 人名不送翻（用 terms 罗马化）
    assert not any(k.endswith(".lower.name") for k in keys)
    # 片头/片尾固定文案不送翻（品牌 SUBTITLES / CTA）
    assert "shot.s001.subtitle" not in keys
    assert "shot.s005.subtitle" not in keys


def test_terms_and_names_are_fixed():
    ep = _episode()
    units = localize.collect_units(ep)
    tx = {u["key"]: u["text"] for u in units}
    # 角色身份命中 terms.ROLES，不走模型
    assert localize._term_translation("主持人", "en") == "Host"
    assert localize._term_translation("传统综艺导演", "es") == "Director de TV tradicional"
    assert localize._term_translation("正片", "en") == "Main"
    # 人名罗马化
    assert localize.NAME_MAP["老周"] == "Zhou"
    amap = {a["id"]: a for a in ep["assets"]}
    name, role = localize._shot_lower_third(ep["shots"][1], amap, tx, "s002", "en")
    assert name == "Zhou" and role == "Host"
    name2, role2 = localize._shot_lower_third(ep["shots"][2], amap, tx, "s003", "en")
    assert name2 == "Ali" and role2 == "AI Content Creator"
    # 没有 lower_third 时回退资产 name/title
    shot_no_lower = {"id": "s9", "type": "host_to_camera", "speaker": "a_g2",
                     "speaker_name": "大魏"}
    name3, role3 = localize._shot_lower_third(shot_no_lower, amap, {}, "s9", "en")
    assert name3 == "Dawei" and role3 == "Traditional TV Director"
    # 品牌/CTA 固定映射
    assert localize._fixed_brand_subtitle("en").startswith("Zaopianju")
    assert "See you next episode" in localize._fixed_end_cta("en")


# ------------------------------------------------------------- provider ----
def test_translate_mock_and_fallback(monkeypatch, tmp_path):
    units = localize.collect_units(_episode())
    result = localize.translate_units(units, "en", "mock", tmp_path)
    assert result["ok"] is True and result["cached"] is False
    assert result["translations"]["shot.s002.line"] == "今天这期，我们聊一个更狠的问题。"
    assert (tmp_path / "translations.json").is_file()

    # ollama 失败 -> 回退原文并标注 ok=false
    def boom(*a, **k):
        raise RuntimeError("ollama down")
    monkeypatch.setattr(localize, "_call_translator", boom)
    result2 = localize.translate_units(units, "en", "ollama", tmp_path, force=True)
    assert result2["ok"] is False and "ollama down" in result2["error"]
    assert result2["translations"]["shot.s002.line"]


# ------------------------------------------------------------------ ass ----
def test_build_ass_localizes_packaging():
    ep = _episode()
    units = localize.collect_units(ep)
    tx = {u["key"]: u["text"] for u in units}
    tx["shot.s002.line"] = "Tonight we ask a tougher question."
    tx["shot.s003.line"] = "I'll start with the conclusion."
    tx["segment.main"] = "Main"
    tx["segment.close"] = "Wrap-up"
    ass = localize.build_ass(ep, tx, "en")
    text = ass["ass"]
    assert "Arial" in text
    assert "Tonight we ask a tougher question." in text
    assert "I'll start with the conclusion." in text
    # 段落标签 + 片头/片尾固定字幕（名字条/品牌已烘焙进本地化 clips，ASS 不再叠加）
    assert "◆ Main" in text and "◆ Wrap-up" in text
    assert "Zaopianju · AI Micro-Variety" in text
    assert "See you next episode" in text
    assert ",Chip," not in text
    # SRT 事件：片头固定文案 + 2 条台词 + 片尾 CTA（reaction 无台词不出字幕）
    subs = ass["subtitles"]
    assert len(subs) == 4
    assert subs[0]["text"].startswith("Zaopianju")
    assert "See you next episode" in subs[-1]["text"]
    assert localize._srt(subs).count("-->") == 4


def test_subtitle_wrap_keeps_all_words():
    long_en = ("This is a deliberately long English line that should be wrapped "
               "into at most two subtitle lines for the target language.")
    lines, fs = localize._fit_subtitle(long_en, 42)
    assert 1 <= len(lines) <= 2
    assert 30 <= fs <= 42
    assert " ".join(lines) == long_en
    # 超长文本也不会丢词
    huge = " ".join(["word"] * 40)
    lines2, fs2 = localize._fit_subtitle(huge, 42)
    assert " ".join(lines2) == huge
    assert fs2 <= fs


def test_voices_selection_and_map():
    default = localize.select_voices("en")
    assert default["host"] == "en-US-GuyNeural"
    assert default["guest_a"] == "en-US-AriaNeural"
    assert default["guest_b"] == "en-US-JennyNeural"
    override = localize.select_voices("en", {"localize_voices": {"en": {"host": "custom-voice"}}})
    assert override["host"] == "custom-voice"
    assert override["guest_a"] == "en-US-AriaNeural"
    mapping = localize._asset_voice_map(_episode(), override)
    assert mapping["a_host"] == "custom-voice"
    assert mapping["a_g1"] == "en-US-AriaNeural"
    assert mapping["a_g2"] == "en-US-JennyNeural"


# ---------------------------------------------------------------- audio ----
def test_atempo_fit():
    # 短 TTS 不加速（补静音）
    assert localize._atempo_for(1.0, 5.0) == 1.0
    # 长 TTS 压缩，上限 1.25
    assert localize._atempo_for(5.0, 3.0) == 1.25
    assert 1.0 < localize._atempo_for(2.0, 2.0) <= 1.25
    assert localize._atempo_for(0.0, 3.0) == 1.0


def test_build_shot_audio_fits_duration(tmp_path):
    tts = tmp_path / "tts.wav"
    utils.ffmpeg(["-f", "lavfi", "-i", "anullsrc=r=48000:cl=mono", "-t", "2.0",
                  "-c:a", "pcm_s16le", str(tts)])
    work = tmp_path / "work"
    info = {"ok": True, "path": str(tts), "duration": 2.0}
    out = localize._build_shot_audio({"id": "s1", "duration": 1.5}, info, 1.5, work)
    assert Path(out["path"]).is_file()
    assert abs(utils.probe_duration(out["path"]) - 1.5) < 0.08
    assert out["atempo"] == 1.25


# ------------------------------------------------------------ integration ----
def _make_clip(path: Path, seconds: float, color: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    utils.ffmpeg(["-f", "lavfi", "-i", f"color=c={color}:s=1280x720:d={seconds}",
                  "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
                  "-r", "25", str(path)])


def test_localize_episode_mock(tmp_path):
    ep = _episode()
    ep["episode"]["resolution"] = "1280x720"
    ep["episode"]["fps"] = 25
    clips_root = tmp_path / "ep"
    for i, shot in enumerate(ep["shots"]):
        _make_clip(clips_root / "clips" / f"{shot['id']}.mp4", float(shot["duration"]),
                   ["0x304050", "0x503040", "0x405030", "0x303050", "0x504030"][i % 5])
    outdir = tmp_path / "out"
    meta = localize.localize_episode(
        ep, "en", outdir, translator="mock", mock_tts=True, no_bgm=True,
        clips_root=clips_root,
    )
    assert meta["ok"] is True
    assert (outdir / "final.mp4").is_file()
    assert (outdir / "subtitles.srt").is_file()
    assert (outdir / "narration.wav").is_file()
    assert (outdir / "translations.json").is_file()
    assert (outdir / "meta.json").is_file()
    duration = utils.probe_duration(outdir / "final.mp4")
    assert abs(duration - meta["target_duration"]) < 0.3, (duration, meta["target_duration"])
    assert meta["translator"]["provider"] == "mock"
    assert len(meta["publish"]["hashtags"]) >= 3
    srt = (outdir / "subtitles.srt").read_text(encoding="utf-8")
    assert "-->" in srt


# ------------------------------------------------------- localized clips ----
def test_localize_shots_romanized_and_ep_no():
    ep = _episode()
    units = localize.collect_units(ep)
    tx = {u["key"]: u["text"] for u in units}
    tx["shot.s002.visual"] = "Zhou · Medium shot"
    tx["shot.s003.visual"] = "Ali · medium shot"
    shots = localize._localize_shots(ep["shots"], tx, "en", ep_no=2)
    assert all(s["episode_no"] == 2 for s in shots)
    host = next(s for s in shots if s["id"] == "s002")
    assert host["speaker_name"] == "Zhou"
    assert host["visual"] == "Zhou · Medium shot"
    assert host["lower_third"]["name"] == "Zhou"
    assert host["lower_third"]["title"] == "Host"
    # 片头/片尾固定文案
    title = next(s for s in shots if s["id"] == "s001")
    assert title["subtitle"].startswith("Zaopianju")


def test_mock_shot_locale_has_no_cjk(tmp_path):
    from pipeline import mock_shot
    locale = localize._locale_for("en")
    shot = {"id": "s1", "type": "end_card", "duration": 3.5, "episode_no": 2}
    ctx = mock_shot._ShotCtx(shot, [], tmp_path, FONT_STUB, 1280, 720, 3.5, 25,
                             locale=locale)
    ctx.build()
    texts = []
    for p in tmp_path.rglob("*.txt"):
        try:
            texts.append(p.read_text(encoding="utf-8", errors="replace"))
        except OSError:
            continue
    joined = "".join(texts)
    assert "ZAOPIANJU" in joined
    assert "AI MICRO-VARIETY" in joined
    assert not re.search(r"[\u4e00-\u9fff]", joined)


def test_locale_tables_cover_all_supported_langs():
    # 拉丁语种必须完全无中文（ja/ko 用日文汉字/韩文，不在此断言）
    for lang in ("en", "es", "pt"):
        locale = localize._locale_for(lang)
        assert locale, lang
        joined = "".join(str(v) for k, v in locale.items() if k != "names")
        assert not re.search(r"[\u4e00-\u9fff]", joined), lang
    for lang in ("ja", "ko"):
        assert localize._locale_for(lang), lang
    # zh 用 mock_shot 默认中文
    assert localize._locale_for("zh") == {}
