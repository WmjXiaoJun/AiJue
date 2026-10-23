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

from pipeline import ass_builder, cli, config, mock_shot, render, tts_engine  # noqa: E402

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
