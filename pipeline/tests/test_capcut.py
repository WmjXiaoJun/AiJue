"""pipeline.capcut 剪映草稿导出测试。"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from pipeline import capcut, utils  # noqa: E402


def _make_media(tmp_path: Path) -> dict:
    """生成 2 段小视频 + 1 段音频（320x180/25fps）。"""
    v1 = tmp_path / "clip_a.mp4"
    v2 = tmp_path / "clip_b.mp4"
    audio = tmp_path / "voice.wav"
    utils.ffmpeg(["-f", "lavfi", "-i", "color=c=0x304050:s=320x180:d=1",
                  "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
                  "-r", "25", str(v1)])
    utils.ffmpeg(["-f", "lavfi", "-i", "color=c=0x503040:s=320x180:d=2",
                  "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
                  "-r", "25", str(v2)])
    utils.ffmpeg(["-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo", "-t", "3",
                  "-c:a", "pcm_s16le", str(audio)])
    return {"v1": v1, "v2": v2, "audio": audio}


def _manifest(media: dict) -> dict:
    return {
        "name": "测试_EP001",
        "width": 320,
        "height": 180,
        "fps": 25,
        "duration": 3.0,
        "video": [
            {"path": str(media["v1"]), "start": 0, "duration": 1.0, "type": "main"},
            {"path": str(media["v2"]), "start": 1.0, "duration": 2.0, "type": "main"},
        ],
        "audio": [{"path": str(media["audio"]), "start": 0, "duration": 3.0, "volume": 0.9}],
        "subtitles": [
            {"start": 0.2, "end": 1.5, "text": "第一句字幕"},
            {"start": 1.8, "end": 2.8, "text": "第二句字幕"},
        ],
    }


# ---------------------------------------------------------------- manifest ----
def test_normalize_manifest(tmp_path):
    media = _make_media(tmp_path)
    raw = _manifest(media)
    raw["name"] = 'bad/name:test'
    man = capcut.normalize_manifest(raw, tmp_path)
    assert man["name"] == "bad_name_test"
    assert man["duration"] == 3.0
    assert man["video"][0]["path"] == str(media["v1"].resolve())
    assert len(man["subtitles"]) == 2


def test_normalize_manifest_derives_duration(tmp_path):
    media = _make_media(tmp_path)
    raw = _manifest(media)
    raw.pop("duration")
    man = capcut.normalize_manifest(raw)
    assert man["duration"] == 3.0


def test_build_manifest_from_workdir(tmp_path):
    media = _make_media(tmp_path)
    workdir = tmp_path / "work"
    clips = workdir / "clips"
    audio_dir = workdir / "audio"
    clips.mkdir(parents=True)
    audio_dir.mkdir(parents=True)
    import shutil
    shutil.copyfile(media["v1"], clips / "s001.mp4")
    shutil.copyfile(media["v2"], clips / "s002.mp4")
    shutil.copyfile(media["audio"], audio_dir / "narration.wav")
    (workdir / "final.ass").write_text(
        "[Script Info]\nScriptType: v4.00+\n\n[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
        "Dialogue: 0,0:00:00.20,0:00:01.20,Sub,,0,0,0,,{\\an2\\pos(640,655)}第一句\n"
        "Dialogue: 0,0:00:01.00,0:00:01.50,Chapter,,0,0,0,,◆ 章节\n",
        encoding="utf-8")
    episode = {
        "episode": {"id": "ep_001", "resolution": "320x180", "fps": 25,
                    "target_duration": 3.0},
        "assets": [],
        "shots": [{"id": "s001", "duration": 1}, {"id": "s002", "duration": 2}],
    }
    ep_path = tmp_path / "episode.json"
    ep_path.write_text(json.dumps(episode, ensure_ascii=False), encoding="utf-8")
    man = capcut.build_manifest_from_workdir(ep_path, workdir)
    assert man["name"] == "造片局_EP001"
    assert man["duration"] == 3.0
    assert [Path(v["path"]).name for v in man["video"]] == ["s001.mp4", "s002.mp4"]
    assert man["video"][1]["start"] == 1.0
    assert len(man["audio"]) == 1
    # 只保留字幕事件，章节标被过滤
    assert len(man["subtitles"]) == 1
    assert man["subtitles"][0]["text"] == "第一句"


# ---------------------------------------------------------------- subtitle ----
def test_subtitles_from_ass_filters_packaging(tmp_path):
    ass = tmp_path / "t.ass"
    ass.write_text(
        "[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
        "Dialogue: 4,0:00:01.00,0:00:02.00,SubChar,,0,0,0,,{\\fad(100,100)}正文一\n"
        "Dialogue: 0,0:00:01.00,0:00:02.00,Sub,,0,0,0,,正文二\n"
        "Dialogue: 0,0:00:01.00,0:00:02.00,Chapter,,0,0,0,,◆ 章节\n"
        "Dialogue: 0,0:00:01.00,0:00:02.00,Lower,,0,0,0,,人名条\n"
        "Dialogue: 0,0:00:01.00,0:00:02.00,Punch,,0,0,0,,花字\n"
        "Dialogue: 0,0:00:01.00,0:00:02.00,Brand,,0,0,0,,台标\n",
        encoding="utf-8")
    subs = capcut.subtitles_from_ass(ass)
    assert [s["text"] for s in subs] == ["正文一", "正文二"]
    assert subs[0]["start"] == 1.0 and subs[0]["end"] == 2.0


def test_build_srt():
    srt = capcut.build_srt([
        {"start": 0.0, "end": 1.234, "text": "你好"},
        {"start": 2.5, "end": 3.0, "text": "世界"},
    ])
    assert "00:00:00,000 --> 00:00:01,234" in srt
    assert "2\n00:00:02,500 --> 00:00:03,000\n世界" in srt


def test_load_subtitles_roundtrip(tmp_path):
    srt = tmp_path / "a.srt"
    srt.write_text("1\n00:00:00,000 --> 00:00:01,000\n测试\n", encoding="utf-8")
    subs = capcut.load_subtitles(srt)
    assert subs[0]["text"] == "测试" and subs[0]["end"] == 1.0


# ------------------------------------------------------------------ export ----
def test_export_draft(tmp_path):
    media = _make_media(tmp_path)
    outdir = tmp_path / "exports"
    summary = capcut.export_draft(_manifest(media), outdir, copy_media=True)
    draft_dir = Path(summary["draft_dir"])
    assert summary["ok"] is True, summary["audit"]
    assert (draft_dir / "draft_content.json").is_file()
    assert (draft_dir / "draft_meta_info.json").is_file()
    assert (draft_dir / "subtitles.srt").is_file()
    assert (draft_dir / "README.txt").is_file()
    content = json.loads((draft_dir / "draft_content.json").read_text(encoding="utf-8-sig"))
    assert content["canvas_config"]["width"] == 320
    assert content["canvas_config"]["height"] == 180
    assert content["canvas_config"]["ratio"] == "16:9"
    assert content["duration"] == 3 * capcut.SEC
    assert content["fps"] == 25
    tracks = content["tracks"]
    types = {t["type"]: len(t["segments"]) for t in tracks}
    assert types.get("video") == 2
    assert types.get("audio") == 1
    assert types.get("text") == 2
    materials = content["materials"]
    assert len(materials["videos"]) == 2
    assert len(materials["audios"]) == 1
    assert len(materials["texts"]) == 2
    # --copy-media 时媒体复制进草稿 materials/
    for item in materials["videos"] + materials["audios"]:
        p = Path(item["path"])
        assert p.is_file()
        assert "materials" in str(p)
    meta = json.loads((draft_dir / "draft_meta_info.json").read_text(encoding="utf-8-sig"))
    assert meta["draft_name"] == "测试_EP001"
    assert meta["tm_duration"] == 3 * capcut.SEC
    audit = capcut.validate_draft(draft_dir)
    assert audit["ok"] is True, audit["errors"]


def test_export_draft_without_copy_media(tmp_path):
    media = _make_media(tmp_path)
    outdir = tmp_path / "exports2"
    summary = capcut.export_draft(_manifest(media), outdir, name="草稿B")
    draft_dir = Path(summary["draft_dir"])
    assert summary["ok"] is True
    assert (draft_dir / "materials").is_dir()
    assert not any((draft_dir / "materials").iterdir())
    assert Path(summary["draft_dir"]).name == "草稿B"


def test_validate_detects_missing(tmp_path):
    empty = tmp_path / "empty_draft"
    empty.mkdir()
    audit = capcut.validate_draft(empty)
    assert audit["ok"] is False
    assert audit["errors"]
