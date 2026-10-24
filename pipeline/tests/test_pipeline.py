"""pipeline 单元测试：不依赖 ffmpeg / 网络（ComfyUI 用 httpx mock）。"""
from __future__ import annotations

import json
import sys
import types
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from pipeline import (  # noqa: E402
    ass_builder, characters, cli, config, mock_shot, render, tts_engine,
)

DEMO = REPO_ROOT / "contract" / "demo_episode.json"
FONT_STUB = {
    "body": {"file": "fonts/Deng.ttf", "name": "Deng.ttf", "family": "DengXian"},
    "display": {"file": "fonts/msyhbd.ttc", "name": "msyhbd.ttc",
                "family": "Microsoft YaHei"},
}


def _demo() -> dict:
    return json.loads(DEMO.read_text(encoding="utf-8"))


def _timings(shots: list) -> list:
    out = []
    cursor = 0.0
    for shot in shots:
        dur = float(shot["duration"])
        out.append({"shot": shot, "start": cursor, "end": cursor + dur,
                    "duration": dur})
        cursor += dur
    return out


# ---------------------------------------------------------------- ASS ----
def test_ass_time_and_color():
    assert ass_builder.ass_time(0) == "0:00:00.00"
    assert ass_builder.ass_time(65.5) == "0:01:05.50"
    assert ass_builder.ass_color("#5AC8FA") == "&H00FAC85A"
    # 非法颜色回退品牌黄 #F5C518
    assert ass_builder.ass_color("bad") == "&H0018C5F5"


def test_ass_esc_braces_and_newline():
    assert ass_builder._esc("a{b}c\\nd") == "a\\{b\\}c\\\\nd"


def test_ass_events_demo_episode():
    data = _demo()
    events = ass_builder.build_events(data, _timings(data["shots"]), data["assets"])
    styles = [e[3] for e in events]
    assert "Sub" in styles
    assert "Lower" in styles
    assert "Chapter" in styles
    # meme_card 的旁白/花字在片段内绘制，ASS 不应重复
    meme = [e for e in events if e[3] == "Punch"]
    assert meme == []
    lower_texts = [e[4] for e in events if e[3] == "Lower"]
    assert any("阿栗" in t for t in lower_texts)
    assert any("大魏" in t for t in lower_texts)
    # 覆盖全片，最后一条不应超过总时长
    assert max(e[1] for e in events) <= sum(s["duration"] for s in data["shots"]) + 0.01


def test_ass_skips_lower_third_for_character_shots():
    data = _demo()
    timings = _timings(data["shots"])
    events = ass_builder.build_events(data, timings, data["assets"],
                                      char_shot_ids={"s006", "s008"})
    assert [e for e in events if e[3] == "Lower"] == []
    # 不传 char_shot_ids 时仍输出人名条（非角色画面兜底）
    events2 = ass_builder.build_events(data, timings, data["assets"])
    assert any(e[3] == "Lower" for e in events2)


def test_ass_build_writes_file(tmp_path):
    data = _demo()
    timings = _timings(data["shots"])
    out = tmp_path / "final.ass"
    info = ass_builder.build_ass(
        {"segments": data["segments"]}, timings, data["assets"],
        FONT_STUB, out,
    )
    text = out.read_text(encoding="utf-8")
    assert info["events"] > 0
    assert "Style: Sub,DengXian" in text
    assert "Style: Punch,Microsoft YaHei" in text
    assert "Dialogue:" in text
    assert "{{FONT" not in text


# ------------------------------------------------------------ mock_shot ----
def test_mock_filter_all_shot_types(tmp_path):
    data = _demo()
    shots = {s["id"]: s for s in data["shots"]}
    samples = {
        "title_card": "s001", "host_to_camera": "s002",
        "shot_reverse": "s008", "reaction": "s009",
        "b_roll": "s024", "meme_card": "s051", "end_card": "s052",
    }
    for typ, sid in samples.items():
        shot = dict(shots[sid])
        ctx = mock_shot._ShotCtx(shot, data["assets"], tmp_path, FONT_STUB,
                                 1280, 720, float(shot["duration"]), 25)
        vf = ctx.build()
        assert "drawtext=" in vf, typ
        assert "format=yuv420p" in vf, typ
        assert "gradients=" in mock_shot._base_source(ctx) or typ == "meme_card"
    # meme 镜头文案来自 punchline
    assert (tmp_path / "text").is_dir()
    meme_texts = [p.read_text(encoding="utf-8")
                  for p in (tmp_path / "text").glob("*.txt")]
    assert any("你正在看一档 AI 综艺" in t for t in meme_texts)


def test_mock_unknown_type_falls_back(tmp_path):
    data = _demo()
    shot = {"id": "sX", "type": "unknown_type", "duration": 5,
            "speaker": "host", "speaker_name": "老周"}
    ctx = mock_shot._ShotCtx(shot, data["assets"], tmp_path, FONT_STUB,
                             1280, 720, 5.0, 25)
    assert "drawtext=" in ctx.build()


# ------------------------------------------------------------------ TTS ----
def test_vtt_format():
    words = [
        {"offset": 0.0, "duration": 0.35, "text": "今天"},
        {"offset": 0.35, "duration": 0.4, "text": "这期"},
    ]
    vtt = tts_engine.build_vtt(words)
    assert vtt.startswith("WEBVTT")
    assert "00:00:00.000 --> 00:00:00.350" in vtt
    assert "今天" in vtt


def test_tts_key_stable():
    assert render._tts_key("a", "v") == render._tts_key("a", "v")
    assert render._tts_key("a", "v") != render._tts_key("b", "v")


# ---------------------------------------------------------------- audio ----
def test_atempo_for():
    assert render._atempo_for({}, 7.0) == 1.0
    assert render._atempo_for({"ok": True, "duration": 5.0}, 7.0) == 1.0
    # 8s 台词塞进 7s 镜头：(8 / (7-0.3)) ≈ 1.194
    assert render._atempo_for({"ok": True, "duration": 8.0}, 7.0) == pytest.approx(1.194, abs=1e-2)
    # 超长台词命中语速补偿上限
    assert render._atempo_for({"ok": True, "duration": 20.0}, 7.0) == config.TTS_MAX_ATEMPO


# -------------------------------------------------------------- comfyui ----
class _FakeResponse:
    def __init__(self, payload=None, content=b"", status_code=200):
        self._payload = payload or {}
        self.content = content
        self.status_code = status_code

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError("http error")


class _FakeClient:
    def __init__(self, *args, **kwargs):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def post(self, url, json=None):
        assert url.endswith("/prompt")
        assert json["prompt"] is not None
        return _FakeResponse({"prompt_id": "pid-123"})

    def get(self, url, params=None):
        if "/history/" in url:
            return _FakeResponse({"pid-123": {
                "status": {"completed": True, "status_str": "success"},
                "outputs": {"9": {"videos": [
                    {"filename": "out.mp4", "subfolder": "", "type": "output"}
                ]}},
            }})
        return _FakeResponse(content=b"fake-video-bytes")


def test_comfyui_adapter(monkeypatch, tmp_path):
    fake_httpx = types.ModuleType("httpx")

    class _Timeout:
        def __init__(self, *a, **k):
            pass

    fake_httpx.Timeout = _Timeout
    fake_httpx.Client = _FakeClient
    monkeypatch.setitem(sys.modules, "httpx", fake_httpx)

    calls = {}
    monkeypatch.setattr("pipeline.comfyui.utils.ffmpeg",
                        lambda *a, **k: calls.setdefault("ffmpeg", True))
    monkeypatch.setattr("pipeline.comfyui.utils.probe_duration",
                        lambda p: 5.0)
    spec = {
        "shot": {"id": "s1", "duration": 5, "line": "测试台词"},
        "episode": {"resolution": "1280x720", "fps": 25},
        "settings": {
            "comfyui_url": "http://127.0.0.1:8188",
            "workflow_template": {
                "1": {"class_type": "CLIPTextEncode", "inputs": {"text": "old"}},
            },
        },
    }
    out = tmp_path / "clip.mp4"
    result = comfyui_generate(spec, out)
    assert result["ok"] is True
    assert result["provider"] == "comfyui"
    assert result["prompt_id"] == "pid-123"
    assert calls.get("ffmpeg") is True
    assert not (tmp_path / "clip.comfy_raw.bin").exists()


def comfyui_generate(spec, out):
    from pipeline import comfyui
    return comfyui.generate_comfyui_clip(spec, out)


def test_comfyui_requires_settings():
    from pipeline import comfyui
    with pytest.raises(RuntimeError):
        comfyui.generate_comfyui_clip({"shot": {}}, "x.mp4")


# ----------------------------------------------------------------- CLI ----
def test_cli_parser_render():
    parser = cli.build_parser()
    args = parser.parse_args([
        "render", "--episode", "e.json", "--workdir", "w",
        "--out", "o.mp4", "--no-bgm", "--limit", "3",
    ])
    assert args.command == "render"
    assert args.no_bgm is True
    assert args.limit == 3


def test_cli_parser_gen_shot():
    parser = cli.build_parser()
    args = parser.parse_args(["gen-shot", "--spec", "s.json", "--out", "c.mp4", "--mock"])
    assert args.command == "gen-shot"
    assert args.mock is True


# ------------------------------------------------- 平台生成台片段复用 ----
def _fake_probe(width=1280, height=720, fps=25.0, duration=6.0, codec="h264"):
    return {"width": width, "height": height, "fps": fps,
            "duration": duration, "codec": codec, "has_audio": False}


def test_platform_shot_probe_and_path(monkeypatch, tmp_path):
    shots_dir = tmp_path / "shots"
    shots_dir.mkdir()
    fake_clip = shots_dir / "s001.mp4"
    fake_clip.write_bytes(b"x" * 4000)
    monkeypatch.setattr(render.utils, "probe_video", lambda p: _fake_probe())
    assert render._shot_source_path(tmp_path, "s001") == fake_clip
    assert render._shot_source_path(tmp_path, "s999") is None
    assert render._probe_external(fake_clip, 1280, 720, 25, 6.0)["ok"] is True
    # 分辨率 / 帧率 / 时长不一致都应判定不可直接复用
    assert render._probe_external(fake_clip, 1920, 1080, 25, 6.0)["ok"] is False
    assert render._probe_external(fake_clip, 1280, 720, 30, 6.0)["ok"] is False
    assert render._probe_external(fake_clip, 1280, 720, 25, 8.0)["ok"] is False


def test_ensure_clip_reuses_platform_shot(monkeypatch, tmp_path):
    shots_dir = tmp_path / "shots"
    shots_dir.mkdir()
    (shots_dir / "s001.mp4").write_bytes(b"x" * 4000)
    monkeypatch.setattr(render.utils, "probe_video", lambda p: _fake_probe())
    shot = {"id": "s001", "type": "host_to_camera", "duration": 6}
    result = render.ensure_clip(shot, [], {}, tmp_path, 1280, 720, 25, False)
    assert result["reused"] is True
    assert result["source"] == "shots"
    assert (tmp_path / "clips" / "s001.mp4").read_bytes() == b"x" * 4000
    meta = json.loads((tmp_path / "clips" / "s001.meta.json").read_text(encoding="utf-8"))
    assert meta["source"] == "shots"


def test_h3_switch_reuses_real_clip(monkeypatch, tmp_path):
    h3_dir = tmp_path / "h3"
    h3_dir.mkdir()
    (h3_dir / "s001.mp4").write_bytes(b"h" * 5000)
    monkeypatch.setattr(render.utils, "probe_video", lambda p: _fake_probe())
    shot = {"id": "s001", "type": "title_card", "duration": 6}
    result = render.ensure_clip(shot, [], {}, tmp_path, 1280, 720, 25, False,
                                h3_dir=h3_dir)
    assert result["source"] == "h3"
    assert (tmp_path / "clips" / "s001.mp4").read_bytes() == b"h" * 5000
    # 未开启 --use-h3（h3_dir=None）时不走 H3 分支
    result2 = render.ensure_clip({"id": "s002", "type": "meme_card",
                                  "duration": 3}, [], {}, tmp_path,
                                 1280, 720, 25, True)
    assert result2.get("source") != "h3"


def test_platform_shot_change_invalidates_clips_cache(monkeypatch, tmp_path):
    shots_dir = tmp_path / "shots"
    shots_dir.mkdir()
    src = shots_dir / "s001.mp4"
    src.write_bytes(b"a" * 4000)
    monkeypatch.setattr(render.utils, "probe_video", lambda p: _fake_probe())
    shot = {"id": "s001", "type": "host_to_camera", "duration": 6}
    r1 = render.ensure_clip(shot, [], {}, tmp_path, 1280, 720, 25, False)
    assert r1["source"] == "shots" and r1["reused"] is True
    # 生成台重新生成（文件指纹变化）后，clips 缓存必须失效并改用新版本
    src.write_bytes(b"b" * 5000)
    r2 = render.ensure_clip(shot, [], {}, tmp_path, 1280, 720, 25, False)
    assert r2["source"] == "shots"
    assert (tmp_path / "clips" / "s001.mp4").read_bytes() == b"b" * 5000
    # 未变化时再次命中缓存
    r3 = render.ensure_clip(shot, [], {}, tmp_path, 1280, 720, 25, False)
    assert r3["reused"] is True and r3["source"] == "shots"


def test_normalize_external_clip_mismatch(monkeypatch, tmp_path):
    monkeypatch.setattr(render.utils, "ffmpeg", lambda *a, **k: None)
    monkeypatch.setattr(render.utils, "probe_duration", lambda p: 3.0)
    with pytest.raises(RuntimeError):
        render._normalize_external_clip(
            tmp_path / "a.mp4", tmp_path / "b.mp4", 1280, 720, 25, 6.0,
        )


# --------------------------------------------------- 角色素材接入 ----
def _write(path: Path, data: bytes = b"x" * 1024) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def test_characters_discover_priority(tmp_path, monkeypatch):
    monkeypatch.delenv("CHARACTERS_DIR", raising=False)
    root = tmp_path / "characters"
    # laozhou: 3D 根目录 + 2D 子目录 -> 3D 优先
    _write(root / "laozhou" / "portrait.png", b"3d" * 600)
    _write(root / "laozhou" / "2d" / "portrait.png", b"2d" * 600)
    _write(root / "laozhou" / "expressions" / "neutral.png")
    # dawei: 只有 2D -> 用 2d/
    _write(root / "dawei" / "2d" / "portrait.png", b"d2" * 600)
    chars = characters.discover(root)
    assert chars["laozhou"]["visual_style"] == "3d"
    assert chars["laozhou"]["portrait"].parent.name == "laozhou"
    assert chars["dawei"]["visual_style"] == "2d"
    assert chars["dawei"]["portrait"].parent.name == "2d"


def test_characters_style_dir_fallback(tmp_path):
    root = tmp_path / "characters"
    _write(root / "ali" / "style2d_cel" / "portrait.png")
    chars = characters.discover(root)
    assert chars["ali"]["portrait"].parent.name == "style2d_cel"
    assert chars["ali"]["visual_style"] == "2d"


def test_characters_resolve_for_assets(tmp_path):
    root = tmp_path / "characters"
    _write(root / "laozhou" / "portrait.png")
    chars = characters.discover(root)
    assets = [
        {"id": "host", "type": "character", "name": "老周", "role": "host"},
        {"id": "guest_a", "type": "character", "name": "阿栗", "role": "guest"},
        {"id": "scene", "type": "scene", "name": "演播厅"},
    ]
    mapping = characters.resolve_for_assets(assets, chars)
    assert mapping["host"]["id"] == "laozhou"
    assert "guest_a" not in mapping  # 阿栗无素材
    assert "scene" not in mapping


def test_characters_choose_expression(tmp_path):
    exprs = {name: tmp_path / f"{name}.png"
             for name in ("neutral", "happy", "surprised", "thinking")}
    char = {"portrait": tmp_path / "p.png", "expressions": exprs}
    assert characters.choose_expression(char, "哈哈") == exprs["happy"]
    assert characters.choose_expression(char, "？！") == exprs["surprised"]
    assert characters.choose_expression(char, "……这话我认。") == exprs["thinking"]
    assert characters.choose_expression(char, "", mood="happy") == exprs["happy"]
    assert characters.choose_expression(char, "普通台词") == exprs["neutral"]
    # 无表情素材时回退立绘
    assert characters.choose_expression({"portrait": tmp_path / "p.png"}) == tmp_path / "p.png"


def test_characters_fingerprint_changes(tmp_path):
    portrait = tmp_path / "portrait.png"
    portrait.write_bytes(b"a" * 1000)
    fp1 = characters.fingerprint({"host": {"id": "laozhou", "portrait": portrait}})
    portrait.write_bytes(b"b" * 2000)
    fp2 = characters.fingerprint({"host": {"id": "laozhou", "portrait": portrait}})
    assert fp1 and fp2 and fp1 != fp2
    assert characters.fingerprint({}) == ""


def test_shot_ctx_character_compose(tmp_path):
    portrait = tmp_path / "portrait.png"
    portrait.write_bytes(b"x" * 1000)
    char = {
        "id": "laozhou", "name": "老周", "title": "主持人",
        "accent_color": "#F5C518", "portrait": portrait,
        "turntable": None, "expressions": {},
    }
    shot = {"id": "s002", "type": "host_to_camera", "duration": 6,
            "speaker": "host", "speaker_name": "老周", "visual": "虚拟演播厅"}
    ctx = mock_shot._ShotCtx(shot, [], tmp_path, FONT_STUB, 1280, 720, 6.0, 25,
                             char_map={"host": char})
    assert ctx.char_mode is True
    plan = ctx.compose()
    assert plan and plan["inputs"]
    assert any("overlay=" in item for item in plan["filters"])
    assert "drawtext=" in ctx.build()
    assert ctx.used_character_id() == "laozhou"
    # 无角色素材 -> 回退文字卡，不合成
    ctx2 = mock_shot._ShotCtx(shot, [], tmp_path, FONT_STUB, 1280, 720, 6.0, 25,
                              char_map={})
    assert ctx2.char_mode is False
    assert ctx2.compose() is None


def test_shot_ctx_reaction_expression_and_fallback(tmp_path):
    exprs = {}
    for name in ("neutral", "happy", "surprised", "thinking"):
        exprs[name] = _write(tmp_path / f"{name}.png")
    char = {"id": "dawei", "name": "大魏", "title": "传统综艺导演",
            "accent_color": "#FF6B6B", "portrait": tmp_path / "portrait.png",
            "turntable": None, "expressions": exprs}
    # 无 speaker 的 reaction：按台词推断角色并选表情
    shot = {"id": "s004", "type": "reaction", "duration": 4,
            "speaker": None, "speaker_name": "", "subtitle": "（冷笑）",
            "visual": "主持人反应特写", "line": ""}
    ctx = mock_shot._ShotCtx(shot, [], tmp_path, FONT_STUB, 1280, 720, 4.0, 25,
                             char_map={"guest_b": char})
    # visual 未出现角色名 -> 兜底主持人（cast[0]），仍应进入角色画面
    assert ctx.char_mode is True
    assert ctx.compose() is not None


# ---------------------------------------------- SDXL 概念图（第 3 优先级）----
def test_characters_concept_discovery(tmp_path):
    chars_root = tmp_path / "characters"
    concepts = tmp_path / "concepts"
    _write(chars_root / "laozhou" / "cutout" / "portrait.png")
    _write(concepts / "laozhou" / "portrait.png", b"c" * 2000)
    _write(concepts / "laozhou" / "expressions" / "neutral.png")
    _write(concepts / "newbie" / "portrait.png", b"n" * 2000)
    (concepts / "newbie" / "meta.json").write_text(
        json.dumps({"name": "新人"}), encoding="utf-8")
    chars = characters.discover(chars_root, concepts_root_dir=concepts)
    assert chars["laozhou"]["concept_portrait"].is_file()
    assert "neutral" in chars["laozhou"]["concept_expressions"]
    # 只有概念图、没有角色工坊目录的角色也应被发现
    assert "newbie" in chars
    assert chars["newbie"]["name"] == "新人"
    assert chars["newbie"]["concept_portrait"] is not None
    # 自定义 root 且未显式给 concepts 时不合并（测试隔离）
    chars2 = characters.discover(chars_root)
    assert "newbie" not in chars2
    assert not chars2["laozhou"].get("concept_portrait")


def test_fingerprint_includes_concept(tmp_path):
    portrait = tmp_path / "concept.png"
    portrait.write_bytes(b"a" * 1000)
    fp1 = characters.fingerprint({"host": {"id": "x", "concept_portrait": portrait}})
    portrait.write_bytes(b"b" * 2000)
    fp2 = characters.fingerprint({"host": {"id": "x", "concept_portrait": portrait}})
    assert fp1 and fp2 and fp1 != fp2


def test_shot_type_aliases():
    # 台本契约里的新类型映射到现有视觉类型
    assert mock_shot.normalize_shot_type("talking_head") == "host_to_camera"
    assert mock_shot.normalize_shot_type("Narration") == "host_to_camera"
    assert mock_shot.normalize_shot_type("dialogue") == "host_to_camera"
    assert mock_shot.normalize_shot_type("reaction") == "reaction"
    assert mock_shot.normalize_shot_type("shot_reverse") == "shot_reverse"
    assert mock_shot.normalize_shot_type("") == "host_to_camera"
    assert mock_shot.normalize_shot_type("custom_visual") == "custom_visual"


def test_shot_ctx_normalizes_script_types(tmp_path):
    still = _write(tmp_path / "h3.png")
    char = {"id": "laozhou", "name": "老周", "title": "主持人",
            "accent_color": "#F5C518", "portrait": None,
            "h3_video": None, "h3_portrait": still}
    base = {"id": "s1", "duration": 5, "speaker": "host",
            "speaker_name": "老周", "visual": "演播厅",
            "line": "今天聊一个话题。"}
    for stype in ("talking_head", "narration"):
        ctx = mock_shot._ShotCtx(dict(base, type=stype), [], tmp_path, FONT_STUB,
                                 1280, 720, 5.0, 25, char_map={"host": char})
        assert ctx.type == "host_to_camera"
        assert ctx.char_mode is True
        assert ctx.compose() is not None
    # 未知类型保持原样，不误判为角色画面（仍可走文字卡）
    ctx2 = mock_shot._ShotCtx(dict(base, type="custom_visual"), [], tmp_path,
                              FONT_STUB, 1280, 720, 5.0, 25,
                              char_map={"host": char})
    assert ctx2.type == "custom_visual"
    assert ctx2.char_mode is False


def test_shot_ctx_concept_media(tmp_path):
    portrait = tmp_path / "concept.png"
    portrait.write_bytes(b"x" * 2000)
    char = {
        "id": "x", "name": "老周", "title": "主持人",
        "accent_color": "#F5C518", "portrait": None,
        "h3_video": None, "h3_portrait": None,
        "concept_portrait": portrait,
        "concept_expressions": {"neutral": portrait},
    }
    shot = {"id": "s1", "type": "host_to_camera", "duration": 6,
            "speaker": "host", "speaker_name": "老周", "visual": "演播厅"}
    ctx = mock_shot._ShotCtx(shot, [], tmp_path, FONT_STUB, 1280, 720, 6.0, 25,
                             char_map={"host": char})
    assert ctx.media and ctx.media["kind"] == "concept_still"
    plan = ctx.compose()
    assert plan is not None
    joined = ";".join(plan["filters"])
    # 竖构图概念图按顶部安全边裁切 + 右侧画窗 overlay
    assert "min(ih-655,ih*0.02)" in joined
    assert "overlay=" in joined
    assert "drawtext=" in ctx.build()
    # reaction 用概念表情，仍走概念立绘画窗
    shot2 = dict(shot, id="s2", type="reaction", line="……有点东西")
    ctx2 = mock_shot._ShotCtx(shot2, [], tmp_path, FONT_STUB, 1280, 720, 4.0, 25,
                              char_map={"host": char})
    assert ctx2.media["kind"] == "concept_still"
    assert ctx2.compose() is not None
