# -*- coding: utf-8 -*-
"""pipeline/h3_comfy.py 单测（不需要 GPU / 不需要 ComfyUI）。

用替身 CLI（临时 .py，打印日志 + 最后一行 JSON 并生成小 mp4）验证：
- generate 落 meta（原子写）、字段齐全（workflow/prompt_id/seed/duration/ref_image/action）；
- audio_ref 的 wav 被转成 32kHz 单声道 PCM s16le 后传给 CLI；无 TTS 时用 1.0s 静音参考音频；
  fl2v 不用音频、保留传入尺寸（audio_ref/fl2v 都不再强制 768²）；
- 失败（exit != 0 / 无 JSON / prompt_id 为空）raise H3Error，且不写 meta；
- seed 默认规则（DEFAULT_SEED + shot.index*17）与显式 seed；
- health() 的 /system_stats + /queue + 工作流文件检查（本地假 HTTP 服务，无 ComfyUI 也不 skip）；
- normalize_clip 输出 1280x720/25fps/精确时长（长裁短补）；
- meta_ok / load_meta / assert_unique_prompt_ids。
"""
from __future__ import annotations

import json
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from pipeline import config, h3_comfy, utils  # noqa: E402

FF = config.FFMPEG_BIN
FP = config.FFPROBE_BIN

FAKE_CLI = r'''# -*- coding: utf-8 -*-
"""替身 H3 CLI：模拟 integrations/comfyui/run_h3_ref.py 的 CLI 约定与 stdout 结果行。"""
import argparse, json, os, subprocess, sys, time
from pathlib import Path

ap = argparse.ArgumentParser()
ap.add_argument("--mode", required=True)
ap.add_argument("--image", required=True)
ap.add_argument("--last-frame", default=None)
ap.add_argument("--audio", default=None)
ap.add_argument("--prompt-file", default=None)
ap.add_argument("--prompt", default=None)
ap.add_argument("--out", required=True)
ap.add_argument("--duration", type=float, default=5.0)
ap.add_argument("--length", type=int, default=None)
ap.add_argument("--width", type=int, default=768)
ap.add_argument("--height", type=int, default=768)
ap.add_argument("--fps", type=int, default=24)
ap.add_argument("--seed", type=int, default=42)
ap.add_argument("--timeout", type=int, default=1800)
a = ap.parse_args()

if os.environ.get("FAKE_H3_SLEEP"):
    time.sleep(float(os.environ["FAKE_H3_SLEEP"]))

exit_code = int(os.environ.get("FAKE_H3_EXIT") or 0)
if exit_code:
    sys.stderr.write(os.environ.get("FAKE_H3_STDERR", "fake h3 failure") + "\n")
    sys.stderr.flush()
    raise SystemExit(exit_code)

prompt = a.prompt
if a.prompt_file:
    prompt = Path(a.prompt_file).read_text(encoding="utf-8").strip()

ff = os.environ["FAKE_FFMPEG"]
fp = os.environ["FAKE_FFPROBE"]

width, height = int(a.width), int(a.height)
out = Path(a.out)
out.parent.mkdir(parents=True, exist_ok=True)
video_sec = max(0.4, min(float(a.duration), 1.0))
subprocess.run([ff, "-y", "-hide_banner", "-loglevel", "error", "-f", "lavfi",
                "-i", "testsrc2=size=%dx%d:rate=%d:duration=%.3f" % (width, height, int(a.fps), video_sec),
                "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-an", str(out)],
               check=True)

probe = {}
try:
    pr = subprocess.run([fp, "-v", "error", "-show_format", "-show_streams", "-of", "json", str(out)],
                        capture_output=True, text=True, encoding="utf-8", errors="replace")
    info = json.loads(pr.stdout or "{}")
    v = next((s for s in info.get("streams", []) if s.get("codec_type") == "video"), {})
    num, _, den = (v.get("avg_frame_rate") or "24/1").partition("/")
    fps_actual = float(num) / float(den or 1)
    probe = {
        "duration_sec": float((info.get("format") or {}).get("duration") or video_sec),
        "video": {"width": int(v.get("width") or width), "height": int(v.get("height") or height),
                  "fps": round(fps_actual, 3)},
        "has_audio": False,
    }
except Exception:
    probe = {}

Path(str(out) + ".args.json").write_text(json.dumps({
    "mode": a.mode, "image": a.image, "last_frame": a.last_frame, "audio": a.audio,
    "prompt": prompt, "duration": a.duration, "width": a.width, "height": a.height,
    "fps": a.fps, "seed": a.seed, "out": str(out),
}, ensure_ascii=False), encoding="utf-8")

if os.environ.get("FAKE_H3_NOJSON") == "1":
    print("[fake] no json result line")
    raise SystemExit(0)

pid = os.environ.get("FAKE_H3_PID")
if pid is None:
    pid = "fake-pid-default"
elif pid == "__EMPTY__":
    pid = ""  # Windows 上环境变量为空串会被删除，用哨兵模拟空 prompt_id
workflow = "05_ref2va_4step_sla.json" if a.mode == "audio_ref" else "04_i2v_fl2v_4step_sla.json"
print("[fake] submitting prompt ...")
print(json.dumps({
    "ok": True, "mode": a.mode, "out": str(out), "duration": a.duration,
    "length": 124, "seed": a.seed, "gen_sec": 1.5, "workflow": workflow,
    "prompt_id": pid, "width": width, "height": height, "fps": a.fps,
    "has_audio": bool(a.audio), "probe": probe, "sheet": "",
}, ensure_ascii=False))
'''


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _image(path: Path, size: str = "1024x576") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    utils.ffmpeg(["-f", "lavfi", "-i", f"color=c=0x334455:s={size}:d=1",
                  "-frames:v", "1", str(path)])
    return path


def _wav(path: Path, seconds: float = 2.0) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    utils.ffmpeg(["-f", "lavfi", "-i",
                  f"sine=frequency=440:sample_rate=48000:duration={seconds:.3f}",
                  "-ac", "2", "-c:a", "pcm_s16le", str(path)])
    return path


def _video(path: Path, seconds: float, size: str = "1024x576", fps: int = 24) -> Path:
    w, h = size.split("x")
    path.parent.mkdir(parents=True, exist_ok=True)
    utils.ffmpeg(["-f", "lavfi", "-i",
                  f"testsrc2=size={w}x{h}:rate={fps}:duration={seconds:.3f}",
                  "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
                  "-an", str(path)])
    return path


def _install_fake_cli(tmp_path: Path, monkeypatch) -> Path:
    cli = tmp_path / "fake_h3_ref.py"
    cli.write_text(FAKE_CLI, encoding="utf-8")
    monkeypatch.setattr(h3_comfy, "CLI_PATH", cli)
    monkeypatch.delenv("H3_COMFY_CLI", raising=False)
    monkeypatch.delenv("H3_DYNAMIC_CLI", raising=False)
    monkeypatch.setenv("FAKE_FFMPEG", FF)
    monkeypatch.setenv("FAKE_FFPROBE", FP)
    return cli


def _call_args(out: Path) -> dict:
    return json.loads(Path(str(out) + ".args.json").read_text(encoding="utf-8"))


def _shot(**overrides) -> dict:
    shot = {"id": "s002", "no": 3, "index": 2, "type": "host_to_camera",
            "duration": 4.0, "action": "host_to_camera", "line": "测试台词"}
    shot.update(overrides)
    return shot


def _generate(tmp_path: Path, monkeypatch, *, mode="audio_ref", duration=4.0,
              audio=True, last=False, seed=None, template=None, **kwargs) -> tuple:
    mono = _install_fake_cli(tmp_path, monkeypatch)
    work = tmp_path / "work"
    first = _image(tmp_path / "frames" / "first.png")
    out = tmp_path / "shots" / "s002.mp4"
    last_image = _image(tmp_path / "frames" / "last.png") if last else None
    audio_wav = _wav(tmp_path / "audio" / "shot_s002.wav") if audio else None
    shot = _shot()
    meta = h3_comfy.generate(
        shot, first, out, mode=mode, prompt="subject_definitions: Subject 1 ...",
        duration=duration, audio_wav=audio_wav, last_image=last_image, seed=seed,
        workdir=work, template=template, **kwargs)
    return mono, meta, out, first, audio_wav, last_image, work


# --------------------------------------------------------------------------- #
# generate：成功 & meta 证据
# --------------------------------------------------------------------------- #
def test_generate_audio_ref_writes_meta(tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_H3_PID", "pid-audio-001")
    cli, meta, out, first, wav, _, work = _generate(tmp_path, monkeypatch)

    meta_path = out.with_suffix(".meta.json")
    assert meta_path.is_file(), "必须落 <out stem>.meta.json"
    disk = json.loads(meta_path.read_text(encoding="utf-8"))
    assert disk == meta
    assert not list(out.parent.glob("*.tmp*")), "原子写不应残留 tmp"

    # 契约字段（docs/PLAN_V3_TEMPLATE_REFACTOR.md §4）
    for key in ("h3", "ok", "mode", "workflow", "prompt_id", "seed", "duration",
                "length", "width", "height", "fps", "ref_image", "action",
                "gen_sec", "path", "probe", "created_at"):
        assert key in meta, f"meta 缺字段 {key}"
    assert meta["h3"] is True and meta["ok"] is True
    assert meta["mode"] == "audio_ref"
    assert meta["workflow"] == "05_ref2va_4step_sla"
    assert meta["prompt_id"] == "pid-audio-001"
    assert meta["seed"] == h3_comfy.DEFAULT_SEED + 2 * 17
    assert meta["duration"] == 4.0
    assert meta["length"] == 124
    assert meta["width"] == 1024 and meta["height"] == 576
    assert meta["fps"] == 24
    assert meta["action"] == "host_to_camera"
    assert Path(meta["ref_image"]) == first.resolve()
    assert Path(meta["path"]) == out.resolve()
    assert meta["created_at"]
    assert meta["gen_sec"] == 1.5
    assert meta["h3_duration"] and meta["h3_duration"] > 0
    assert h3_comfy.meta_ok(meta)
    assert h3_comfy.load_meta(out)["prompt_id"] == "pid-audio-001"
    assert h3_comfy.load_meta(meta_path) == meta
    assert h3_comfy.load_meta(tmp_path / "nope.json") is None

    # audio_ref：wav 转 32kHz 单声道 s16le 后交给 CLI
    args = _call_args(out)
    assert args["mode"] == "audio_ref"
    assert args["audio"] and Path(args["audio"]) == Path(meta["audio"])
    assert Path(meta["audio"]).is_file()
    info = utils.ffprobe_json(meta["audio"])
    stream = info["streams"][0]
    assert stream["codec_name"] == "pcm_s16le"
    assert int(stream["sample_rate"]) == h3_comfy.H3_AUDIO_RATE
    assert int(stream["channels"]) == 1
    # prompt 走 --prompt-file，内容原样
    assert args["prompt"].startswith("subject_definitions:")
    assert args["width"] == 1024 and args["height"] == 576 and args["fps"] == 24
    assert args["seed"] == meta["seed"]
    assert str(cli) == meta["cli"]

    # audio_src 记录原始 wav、audio_silent=false；prompt 落盘可审计
    assert Path(meta["audio_src"]) == wav.resolve()
    assert meta["audio_silent"] is False


def test_generate_audio_ref_uses_silence_when_no_tts(tmp_path, monkeypatch):
    """无台词镜：audio_wav=None → 内部 1.0s 静音参考音频（32kHz mono s16le）。"""
    monkeypatch.setenv("FAKE_H3_PID", "pid-silent-001")
    cli, meta, out, first, _wav_none, _, work = _generate(
        tmp_path, monkeypatch, mode="audio_ref", duration=4.0, audio=False)
    args = _call_args(out)
    assert Path(meta["audio"]).is_file()
    assert Path(args["audio"]) == Path(meta["audio"]), "静音参考也要传给 CLI --audio"
    assert meta["audio_silent"] is True
    assert meta["audio_src"] == ""
    assert Path(meta["audio"]).name.startswith("silence_")
    info = utils.ffprobe_json(meta["audio"])
    stream = info["streams"][0]
    assert stream["codec_name"] == "pcm_s16le"
    assert int(stream["sample_rate"]) == h3_comfy.H3_AUDIO_RATE
    assert int(stream["channels"]) == 1
    assert abs(utils.probe_duration(meta["audio"]) - h3_comfy.SILENCE_SECONDS) < 0.1
    assert meta["mode"] == "audio_ref"
    assert meta["workflow"] == "05_ref2va_4step_sla"
    assert h3_comfy.meta_ok(meta)
    # 静音参考缓存复用（同 workdir 不重复生成）
    assert h3_comfy._silence_audio(work) == Path(meta["audio"])


def test_generate_fl2v_keeps_requested_size_and_ignores_audio(tmp_path, monkeypatch):
    """fl2v 保留调用方尺寸（仅 32 对齐），meta 记录实际尺寸；不使用音频。"""
    monkeypatch.setenv("FAKE_H3_PID", "pid-fl2v-001")
    cli, meta, out, first, wav, last, work = _generate(
        tmp_path, monkeypatch, mode="fl2v", duration=3.0, audio=True, last=True,
        seed=42)
    args = _call_args(out)
    assert args["width"] == 1024 and args["height"] == 576, "fl2v 不再强制 768²"
    assert args["audio"] in (None, "")
    assert args["last_frame"] and Path(args["last_frame"]) == last.resolve()
    assert meta["mode"] == "fl2v"
    assert meta["workflow"] == "04_i2v_fl2v_4step_sla"
    assert meta["width"] == 1024 and meta["height"] == 576, "meta 记录实际尺寸"
    assert meta["gen_width"] == 1024 and meta["gen_height"] == 576
    assert meta["audio"] == "" and meta["audio_src"] == ""
    assert meta["audio_silent"] is False
    assert meta["last_image"] == str(last.resolve())
    assert meta["seed"] == 42
    assert not (work / "h3_aux").exists() or not list((work / "h3_aux").glob("*.wav"))
    assert h3_comfy.meta_ok(meta)


def test_generate_fl2v_template_resolution_not_forced(tmp_path, monkeypatch):
    """fl2v 走模板 1280x704 也保留（以前会被强制 768²）。"""
    monkeypatch.setenv("FAKE_H3_PID", "pid-fl2v-tpl-001")
    template = {"id": "tonight_show",
                "h3": {"fps": 25, "resolution": "1280x704"}}
    _, meta, out, *_ = _generate(tmp_path, monkeypatch, mode="fl2v",
                                 duration=3.0, last=True, template=template)
    args = _call_args(out)
    assert args["width"] == 1280 and args["height"] == 704 and args["fps"] == 25
    assert meta["width"] == 1280 and meta["height"] == 704
    assert meta["fps"] == 25
    # H3 尺寸 32 对齐规则仍生效
    assert h3_comfy._snap32(1000) == 992
    assert h3_comfy._snap32(704) == 704


def test_generate_template_overrides_resolution_fps(tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_H3_PID", "pid-tpl-001")
    template = {"id": "tonight_show",
                "h3": {"workflow": "05_ref2va_4step_sla", "fps": 25,
                       "resolution": "1280x704", "duration": [4, 8]}}
    cfg = h3_comfy.resolve_h3_config(template)
    assert cfg["width"] == 1280 and cfg["height"] == 704 and cfg["fps"] == 25
    assert cfg["duration_min"] == 4.0 and cfg["duration_max"] == 8.0
    assert h3_comfy.resolve_h3_config(None)["width"] == 1024
    assert h3_comfy.resolve_h3_config(None)["fps"] == 24

    _, meta, out, *_ = _generate(tmp_path, monkeypatch, template=template)
    args = _call_args(out)
    assert args["width"] == 1280 and args["height"] == 704 and args["fps"] == 25
    assert meta["width"] == 1280 and meta["height"] == 704
    assert meta["fps"] == 25
    assert meta["gen_width"] == 1280 and meta["gen_height"] == 704


def test_seed_defaults():
    assert h3_comfy.seed_for_shot({"index": 0}) == h3_comfy.DEFAULT_SEED
    assert h3_comfy.seed_for_shot({"index": 2}) == h3_comfy.DEFAULT_SEED + 34
    assert h3_comfy.seed_for_shot({"no": 3}) == h3_comfy.DEFAULT_SEED + 34
    assert h3_comfy.seed_for_shot({"id": "s004"}) == h3_comfy.DEFAULT_SEED + 51
    assert h3_comfy.seed_for_shot({"index": 2, "no": 3}) == h3_comfy.DEFAULT_SEED + 34
    assert h3_comfy.seed_for_shot({}) == h3_comfy.DEFAULT_SEED


# --------------------------------------------------------------------------- #
# generate：失败必须 raise（不静默兜底）
# --------------------------------------------------------------------------- #
def test_generate_failure_raises_with_stderr_and_no_meta(tmp_path, monkeypatch):
    _install_fake_cli(tmp_path, monkeypatch)
    monkeypatch.setenv("FAKE_H3_EXIT", "3")
    monkeypatch.setenv("FAKE_H3_STDERR", "CUDA out of memory: boom traceback")
    first = _image(tmp_path / "first.png")
    out = tmp_path / "shots" / "s002.mp4"
    with pytest.raises(h3_comfy.H3Error) as excinfo:
        h3_comfy.generate({"id": "s002"}, first, out, mode="fl2v",
                          prompt="p", duration=2.0)
    msg = str(excinfo.value)
    assert "boom traceback" in msg and "exit 3" in msg
    assert not out.with_suffix(".meta.json").exists(), "失败不得写 meta"


def test_generate_missing_prompt_id_raises(tmp_path, monkeypatch):
    _install_fake_cli(tmp_path, monkeypatch)
    monkeypatch.setenv("FAKE_H3_PID", "__EMPTY__")
    first = _image(tmp_path / "first.png")
    out = tmp_path / "shots" / "s002.mp4"
    with pytest.raises(h3_comfy.H3Error, match="prompt_id"):
        h3_comfy.generate({"id": "s002"}, first, out, mode="fl2v", prompt="p",
                          duration=2.0)
    assert not out.with_suffix(".meta.json").exists()


def test_generate_without_json_result_raises(tmp_path, monkeypatch):
    _install_fake_cli(tmp_path, monkeypatch)
    monkeypatch.setenv("FAKE_H3_NOJSON", "1")
    first = _image(tmp_path / "first.png")
    out = tmp_path / "shots" / "s002.mp4"
    with pytest.raises(h3_comfy.H3Error, match="JSON"):
        h3_comfy.generate({"id": "s002"}, first, out, mode="fl2v", prompt="p",
                          duration=2.0)


def test_generate_timeout_raises(tmp_path, monkeypatch):
    _install_fake_cli(tmp_path, monkeypatch)
    monkeypatch.setenv("FAKE_H3_SLEEP", "5")
    first = _image(tmp_path / "first.png")
    out = tmp_path / "shots" / "s002.mp4"
    with pytest.raises(h3_comfy.H3Error, match="超时"):
        h3_comfy.generate({"id": "s002"}, first, out, mode="fl2v", prompt="p",
                          duration=2.0, timeout=1)


def test_generate_missing_inputs_raise(tmp_path, monkeypatch):
    _install_fake_cli(tmp_path, monkeypatch)
    first = _image(tmp_path / "first.png")
    out = tmp_path / "shots" / "s002.mp4"
    # audio_ref 指定了不存在的 wav
    with pytest.raises(h3_comfy.H3Error, match="音频不存在"):
        h3_comfy.generate({"id": "s002"}, first, out, mode="audio_ref",
                          prompt="p", duration=2.0,
                          audio_wav=tmp_path / "missing.wav")
    # 首帧不存在
    with pytest.raises(h3_comfy.H3Error, match="首帧"):
        h3_comfy.generate({"id": "s002"}, tmp_path / "missing.png", out,
                          mode="fl2v", prompt="p", duration=2.0)
    # 非法 mode
    with pytest.raises(h3_comfy.H3Error, match="模式"):
        h3_comfy.generate({"id": "s002"}, first, out, mode="bad", prompt="p",
                          duration=2.0)
    # prompt 为空
    with pytest.raises(h3_comfy.H3Error, match="prompt"):
        h3_comfy.generate({"id": "s002"}, first, out, mode="fl2v", prompt=" ",
                          duration=2.0)


def test_workflow_for_mode():
    assert h3_comfy.workflow_for_mode("audio_ref") == "05_ref2va_4step_sla"
    assert h3_comfy.workflow_for_mode("fl2v") == "04_i2v_fl2v_4step_sla"
    with pytest.raises(h3_comfy.H3Error):
        h3_comfy.workflow_for_mode("nope")


# --------------------------------------------------------------------------- #
# meta_ok / assert_unique_prompt_ids
# --------------------------------------------------------------------------- #
def _meta(pid="p1", **overrides) -> dict:
    meta = {"h3": True, "ok": True, "workflow": "05_ref2va_4step_sla",
            "prompt_id": pid, "seed": 20261026, "shot_id": "s001"}
    meta.update(overrides)
    return meta


def test_meta_ok_rules():
    assert h3_comfy.meta_ok(_meta())
    assert not h3_comfy.meta_ok(None)
    assert not h3_comfy.meta_ok({})
    assert not h3_comfy.meta_ok(_meta(h3=False))
    assert not h3_comfy.meta_ok(_meta(workflow=""))
    assert not h3_comfy.meta_ok(_meta(prompt_id="   "))
    assert not h3_comfy.meta_ok(_meta(seed=None))
    assert not h3_comfy.meta_ok(_meta(seed=True))
    assert not h3_comfy.meta_ok(_meta(seed="abc"))
    assert h3_comfy.meta_ok(_meta(seed=0))


def test_assert_unique_prompt_ids(tmp_path):
    metas = [_meta("p1", shot_id="s1"), _meta("p2", shot_id="s2")]
    assert h3_comfy.assert_unique_prompt_ids(metas) == ["p1", "p2"]
    # 卡片（h3=false）不参与唯一性
    card = {"shot_id": "card1", "h3": False, "path": "x.mp4"}
    assert h3_comfy.assert_unique_prompt_ids([*metas, card, None]) == ["p1", "p2"]
    # 重复
    with pytest.raises(h3_comfy.H3Error, match="重复"):
        h3_comfy.assert_unique_prompt_ids([_meta("p1"), _meta("p1", shot_id="s9")])
    # 空 prompt_id
    with pytest.raises(h3_comfy.H3Error, match="prompt_id"):
        h3_comfy.assert_unique_prompt_ids([_meta("")])
    # 也接受 meta json 路径
    path = tmp_path / "m.json"
    utils.write_json(path, _meta("p3"))
    assert h3_comfy.assert_unique_prompt_ids([path]) == ["p3"]


# --------------------------------------------------------------------------- #
# health：假 HTTP 服务，不依赖本机 ComfyUI
# --------------------------------------------------------------------------- #
class _HealthHandler(BaseHTTPRequestHandler):
    payloads: dict = {}

    def do_GET(self):  # noqa: N802
        body = self.payloads.get(self.path)
        if body is None:
            self.send_response(404)
            self.end_headers()
            return
        raw = json.dumps(body).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def log_message(self, *args):  # noqa: D102
        pass


def _serve(payloads: dict):
    handler = type("Handler", (_HealthHandler,), {"payloads": payloads})
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    return httpd, f"http://127.0.0.1:{httpd.server_address[1]}"


def _workflow_files(tmp_path: Path, monkeypatch, names=None) -> Path:
    names = names or list(h3_comfy.WORKFLOWS.values())
    wf_dir = tmp_path / "comfy" / "user" / "default" / "workflows"
    wf_dir.mkdir(parents=True, exist_ok=True)
    for name in names:
        (wf_dir / f"{name}.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(h3_comfy, "WORKFLOW_DIR", wf_dir)
    return wf_dir


def test_health_ok_with_fake_server(tmp_path, monkeypatch):
    _workflow_files(tmp_path, monkeypatch)
    httpd, server = _serve({
        "/system_stats": {"system": {"comfyui_version": "0.3.60", "os": "nt",
                                     "python_version": "3.11.9"},
                          "devices": [{"name": "cuda:0", "vram_total": 1,
                                       "vram_free": 1}]},
        "/queue": {"queue_running": [[0, "run-1", {}, {}]],
                   "queue_pending": [[0, "pend-1", {}, {}], [0, "pend-2", {}, {}]]},
    })
    try:
        result = h3_comfy.health(server=server, timeout=3)
        alive = h3_comfy.server_alive(server, timeout=2)
    finally:
        httpd.shutdown()
        httpd.server_close()
    assert result["ok"] is True
    assert result["server"] == server
    assert result["queue_running"] == 1
    assert result["queue_pending"] == 2
    assert result["workflows"] == ["05_ref2va_4step_sla", "04_i2v_fl2v_4step_sla"]
    assert set(result["workflow_files"]) == set(h3_comfy.WORKFLOWS.values())
    assert all(info["exists"] for info in result["workflow_files"].values())
    assert result["server_ok"] is True
    assert result["system"]["comfyui_version"] == "0.3.60"
    assert result["errors"] == []
    assert alive is True


def test_health_offline_and_missing_workflows(tmp_path, monkeypatch):
    monkeypatch.setattr(h3_comfy, "WORKFLOW_DIR", tmp_path / "no_such_dir")
    result = h3_comfy.health(server="http://127.0.0.1:9", timeout=0.5)
    assert result["ok"] is False
    assert result["queue_running"] == 0 and result["queue_pending"] == 0
    assert result["server_ok"] is False
    joined = " ".join(result["errors"])
    assert "05_ref2va_4step_sla" in joined and "04_i2v_fl2v_4step_sla" in joined
    assert h3_comfy.server_alive("http://127.0.0.1:9", timeout=0.5) is False


# --------------------------------------------------------------------------- #
# normalize_clip：H3 原生 -> 1280x720/25fps/精确时长
# --------------------------------------------------------------------------- #
def test_normalize_clip_trims_and_rescales(tmp_path):
    src = _video(tmp_path / "h3_raw" / "s002.mp4", 2.0, size="1024x576", fps=24)
    dst = tmp_path / "clips" / "s002.mp4"
    result = h3_comfy.normalize_clip(src, dst, duration=1.2)
    assert dst.is_file()
    assert not list(dst.parent.glob("*.tmp*"))
    assert result["width"] == 1280 and result["height"] == 720
    assert abs(float(result["fps"]) - 25.0) < 0.01
    assert abs(float(result["duration"]) - 1.2) < 0.08
    assert result["trimmed"] is True and result["padded"] is False
    assert result["src_width"] == 1024 and result["src_height"] == 576
    assert abs(float(result["src_duration"]) - 2.0) < 0.1


def test_normalize_clip_pads_short_768_clip(tmp_path):
    src = _video(tmp_path / "h3_raw" / "s002.mp4", 0.4, size="768x768", fps=24)
    dst = tmp_path / "clips" / "s002.mp4"
    result = h3_comfy.normalize_clip(src, dst, duration=1.0)
    assert result["width"] == 1280 and result["height"] == 720
    assert abs(float(result["duration"]) - 1.0) < 0.08
    assert result["padded"] is True and result["trimmed"] is False


def test_normalize_clip_missing_src_raises(tmp_path):
    with pytest.raises(h3_comfy.H3Error, match="不存在"):
        h3_comfy.normalize_clip(tmp_path / "missing.mp4", tmp_path / "out.mp4",
                                duration=1.0)
