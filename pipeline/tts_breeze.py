# -*- coding: utf-8 -*-
"""Breeze TTS 2 本地客户端（角色化语音合成）。

官方实现：https://github.com/breezeblue-ai/breeze-tts （PyTorch，权重 BreezeBlue/breeze-tts-2）
- 语音服务跑在独立 venv（local_models/breeze-venv），本模块通过 HTTP 调用：
  GET  /health
  POST /v1/audio/speech/batch  (multipart: texts=JSON数组, instruction, cfg_scale, seed)
  返回 s16le 24kHz 单声道 PCM 拼接 + X-Segment-Bytes 切分头
- 三角色使用 Voice Design（自然语言音色描述，无需参考音频），同角色固定 instruction+seed；
- 失败/服务不可用时抛 BreezeTTSUnavailable，由 tts_engine 回退 edge-tts。

用法（在 pipeline 内）：
  from . import tts_breeze
  tts_breeze.ensure_server()
  results = tts_breeze.synthesize({"laozhou": ["台词1"], "ali": ["台词2"]}, workdir)
"""
from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import time
import wave
from pathlib import Path

import httpx

from . import utils

LOG = __import__("logging").getLogger("pipeline")

REPO_ROOT = Path(__file__).resolve().parents[1]
BREEZE_ROOT = REPO_ROOT / "local_models" / "breeze-tts"
BREEZE_MODEL = REPO_ROOT / "local_models" / "breeze-tts-2"
BREEZE_VENV_PY = REPO_ROOT / "local_models" / "breeze-venv" / "Scripts" / "python.exe"
LOG_DIR = REPO_ROOT / "local_models" / "logs"
DEFAULT_PORT = int(os.environ.get("BREEZE_TTS_PORT", "7861"))
DEFAULT_BASE = os.environ.get("BREEZE_TTS_URL", f"http://127.0.0.1:{DEFAULT_PORT}")
SAMPLE_RATE = 24000

# 角色音色（Voice Design；中文 instruction，与台词同语言）
VOICES: dict[str, dict] = {
    "laozhou": {
        "instruction": (
            "一位42岁的男性深夜秀主持人，声音低沉富有磁性，胸腔共鸣明显，"
            "语速偏慢而松弛，带着一点冷幽默的笑意和掌控全场的从容感。"
        ),
        "seed": 20261026,
        "cfg_scale": 4.0,
        "label": "深夜秀主持（低沉磁性·冷幽默）",
    },
    "ali": {
        "instruction": (
            "一位24岁的年轻女性嘉宾，声音明亮清脆，音色偏甜，语速略快，"
            "语气俏皮活泼、情绪外放，说到兴奋处会有明显的上扬和笑声感。"
        ),
        "seed": 20261027,
        "cfg_scale": 4.0,
        "label": "年轻女嘉宾（明亮清脆·俏皮）",
    },
    "dawei": {
        "instruction": (
            "一位52岁的男性嘉宾，嗓音沉稳、略带沙哑，语速中等偏慢，"
            "语气保守严肃，措辞谨慎，重音落在关键判断上。"
        ),
        "seed": 20261028,
        "cfg_scale": 4.0,
        "label": "资深男嘉宾（沉稳沙哑·严肃）",
    },
}
DEFAULT_VOICE = "laozhou"
MAX_BATCH_TEXTS = 64  # 官方 128，保守取 64


class BreezeTTSUnavailable(RuntimeError):
    """Breeze TTS 服务不可用（未启动/健康检查失败/请求失败）。"""


# ---------------------------------------------------------------------------
# 文本处理
# ---------------------------------------------------------------------------
def clean_text(text: str) -> str:
    """台词清洗：去多余空白/引号，补句末标点（模型对无标点文本语气不稳）。"""
    t = str(text or "").replace("\u3000", " ").strip()
    t = t.strip("“”\"'「」『』")
    t = " ".join(t.split())
    if t and t[-1] not in "。！？!?…~～":
        t += "。"
    return t


def voice_for(role: str, overrides: dict | None = None) -> dict:
    voice = dict(VOICES.get(str(role or ""), VOICES[DEFAULT_VOICE]))
    if isinstance(overrides, dict):
        voice.update({k: v for k, v in overrides.items() if v is not None})
    return voice


def voice_spec_from_character(char: dict) -> dict:
    """V5 角色定义 -> Breeze Voice Design 参数（instruction/seed/cfg_scale）。"""
    voice = (char or {}).get("voice") if isinstance(char, dict) else None
    if not isinstance(voice, dict):
        return {}
    spec: dict = {}
    instruction = str(voice.get("instruction") or "").strip()
    if instruction:
        spec["instruction"] = instruction
    if voice.get("seed") is not None:
        spec["seed"] = int(voice["seed"])
    if voice.get("cfg_scale") is not None:
        spec["cfg_scale"] = float(voice["cfg_scale"])
    if voice.get("label"):
        spec["label"] = str(voice["label"])
    return spec


# ---------------------------------------------------------------------------
# 服务生命周期
# ---------------------------------------------------------------------------
def _venv_ok() -> bool:
    return BREEZE_VENV_PY.is_file() and (BREEZE_ROOT / "infer.py").is_file()


def health(base: str | None = None, timeout: float = 3.0) -> dict | None:
    url = (base or DEFAULT_BASE).rstrip("/") + "/health"
    try:
        resp = httpx.get(url, timeout=timeout)
        if resp.status_code == 200:
            data = resp.json()
            if isinstance(data, dict) and data.get("status") == "ok":
                return data
    except Exception:  # noqa: BLE001
        return None
    return None


def server_alive(base: str | None = None) -> bool:
    return health(base) is not None


def free_comfy_gpu(timeout: float = 15.0) -> bool:
    """让 ComfyUI 卸载模型/释放显存（H3 与 Breeze TTS 不能同时驻留 12GB 显卡）。"""
    base = os.environ.get("COMFYUI_URL", "http://127.0.0.1:8188").rstrip("/")
    try:
        resp = httpx.post(f"{base}/free",
                          json={"unload_models": True, "free_memory": True},
                          timeout=timeout)
        ok = resp.status_code == 200
        LOG.info("ComfyUI /free %s（释放显存给 Breeze TTS）", "ok" if ok else resp.status_code)
        return ok
    except Exception as exc:  # noqa: BLE001
        LOG.info("ComfyUI /free 不可用（可能未启动）：%s", exc)
        return False


def start_server(base: str | None = None, *, wait_seconds: float = 300.0,
                 gpu_memory_gb: float | None = None) -> bool:
    """启动 Breeze TTS API 服务（后台进程，独立 venv）。已在运行则直接返回 True。"""
    if server_alive(base):
        return True
    free_comfy_gpu()
    if not _venv_ok():
        LOG.warning("Breeze TTS venv/代码缺失: %s / %s", BREEZE_VENV_PY, BREEZE_ROOT)
        return False
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_path = LOG_DIR / "tts_server.log"
    base = (base or DEFAULT_BASE).rstrip("/")
    port = str(base.rsplit(":", 1)[-1]) if base.rsplit(":", 1)[-1].isdigit() else str(DEFAULT_PORT)
    cmd = [str(BREEZE_VENV_PY), "-m", "breeze_infer.api", str(BREEZE_MODEL),
           "--host", "127.0.0.1", "--port", port]
    env = dict(os.environ)
    env.setdefault("PYTHONUTF8", "1")
    env.setdefault("PYTHONIOENCODING", "utf-8")
    env.setdefault("HF_HUB_OFFLINE", "1")  # 权重已本地化，避免启动时联网
    logfh = open(log_path, "a", encoding="utf-8", errors="replace")
    logfh.write(f"\n===== start {time.strftime('%Y-%m-%d %H:%M:%S')} =====\n")
    logfh.write("$ " + " ".join(cmd) + "\n")
    logfh.flush()
    try:
        creationflags = 0
        if sys.platform == "win32":
            creationflags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) | \
                getattr(subprocess, "DETACHED_PROCESS", 0)
        proc = subprocess.Popen(
            cmd, cwd=str(BREEZE_ROOT), env=env,
            stdout=logfh, stderr=subprocess.STDOUT,
            creationflags=creationflags, close_fds=True,
        )
    except Exception as exc:  # noqa: BLE001
        LOG.warning("Breeze TTS 服务启动失败: %s", exc)
        return False
    LOG.info("Breeze TTS 服务启动中 pid=%s 日志=%s", proc.pid, log_path)
    deadline = time.time() + max(10.0, float(wait_seconds))
    while time.time() < deadline:
        if server_alive(base):
            LOG.info("Breeze TTS 服务就绪: %s", base)
            return True
        if proc.poll() is not None:
            LOG.warning("Breeze TTS 服务进程退出 rc=%s（见 %s）", proc.returncode, log_path)
            return False
        time.sleep(2.0)
    LOG.warning("Breeze TTS 服务等待超时（%.0fs），见 %s", wait_seconds, log_path)
    return False


def stop_server() -> bool:
    """尽力停止服务（按端口/命令行匹配），返回是否已停止。"""
    if not server_alive():
        return True
    try:
        if sys.platform == "win32":
            ps = ("Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | "
                  "Where-Object { $_.CommandLine -like '*breeze_infer.api*' } | "
                  "ForEach-Object { Stop-Process -Id $_.ProcessId -Force }")
            subprocess.run(["powershell", "-NoProfile", "-Command", ps], timeout=30)
        else:
            subprocess.run(["pkill", "-f", "breeze_infer.api"], timeout=30)
    except Exception as exc:  # noqa: BLE001
        LOG.warning("Breeze TTS 服务停止失败: %s", exc)
        return False
    for _ in range(10):
        if not server_alive():
            return True
        time.sleep(1.0)
    return False


def server_status() -> dict:
    h = health()
    return {
        "available": h is not None,
        "base": DEFAULT_BASE,
        "sample_rate": (h or {}).get("sample_rate", SAMPLE_RATE),
        "venv": str(BREEZE_VENV_PY),
        "model": str(BREEZE_MODEL),
        "model_present": (BREEZE_MODEL / "config.json").is_file(),
        "voices": {k: {"label": v["label"], "seed": v["seed"]} for k, v in VOICES.items()},
    }


# ---------------------------------------------------------------------------
# 合成
# ---------------------------------------------------------------------------
def _write_wav(path: Path, pcm: bytes, sample_rate: int = SAMPLE_RATE) -> None:
    utils.ensure_dir(path.parent)
    with wave.open(str(path), "wb") as fh:
        fh.setnchannels(1)
        fh.setsampwidth(2)  # s16le
        fh.setframerate(int(sample_rate))
        fh.writeframes(pcm)


def _to_mix_wav(src: Path, dst: Path, duration: float | None = None) -> dict:
    """24k mono → 48k stereo wav（成片混音规格）；duration 给定时补齐/截断。"""
    args = ["-y", "-hide_banner", "-loglevel", "error", "-i", str(src),
            "-ar", "48000", "-ac", "2", "-c:a", "pcm_s16le"]
    if duration:
        args += ["-af", f"apad=whole_dur={float(duration):.3f}", "-t", f"{float(duration):.3f}"]
    args.append(str(dst))
    utils.ffmpeg(args)
    return {"path": str(dst), "duration": utils.probe_duration(dst)}


def synthesize_batch(texts: list[str], role: str, *, out_dir: Path,
                     base: str | None = None, timeout: float = 900.0,
                     cfg_scale: float | None = None, seed: int | None = None,
                     voice_spec: dict | None = None,
                     ref_audio: str | Path | None = None,
                     ref_text: str = "") -> list[dict]:
    """单角色批量合成：返回 [{index, path(24k mono wav), duration, text}]。"""
    base = (base or DEFAULT_BASE).rstrip("/")
    if not texts:
        return []
    voice = voice_for(role, voice_spec)
    if not server_alive(base):
        raise BreezeTTSUnavailable(f"Breeze TTS 服务不可用: {base}（先 ensure_server/start_server）")
    results: list[dict] = []
    for start in range(0, len(texts), MAX_BATCH_TEXTS):
        chunk = texts[start:start + MAX_BATCH_TEXTS]
        payload_texts = json.dumps([clean_text(t) for t in chunk], ensure_ascii=False)
        data = {
            "texts": payload_texts,
            "cfg_scale": str(float(cfg_scale if cfg_scale is not None else voice["cfg_scale"])),
            "seed": str(int(seed if seed is not None else voice["seed"])),
            "max_new_tokens": "1500",
        }
        if voice.get("instruction"):
            data["instruction"] = voice["instruction"]
        ref_path = Path(ref_audio) if ref_audio else None
        if ref_path is not None:
            if not ref_path.is_file():
                raise BreezeTTSUnavailable(f"voice clone 参考音频不存在: {ref_path}")
            if not ref_text.strip():
                raise BreezeTTSUnavailable("voice clone 需要 ref_text（参考音频文本）")
            data["ref_text"] = ref_text.strip()
        t0 = time.time()
        resp = None
        last_exc = None
        for attempt in range(1, 13):
            files = None
            fh = None
            if ref_path is not None:
                fh = open(ref_path, "rb")
                files = {"ref_audio": (ref_path.name, fh, "audio/wav")}
            try:
                resp = httpx.post(f"{base}/v1/audio/speech/batch", data=data,
                                  files=files,
                                  timeout=httpx.Timeout(timeout, connect=10.0))
                if resp.status_code == 409:
                    # 单推理槽忙：等待后重试（最多约 60s）
                    wait = min(8.0, 1.5 * attempt)
                    LOG.info("Breeze 服务忙(409)，%.1fs 后重试（第 %d 次）", wait, attempt)
                    time.sleep(wait)
                    continue
                resp.raise_for_status()
                break
            except Exception as exc:  # noqa: BLE001
                last_exc = exc
                raise BreezeTTSUnavailable(f"Breeze TTS batch 请求失败: {exc}") from exc
            finally:
                if fh is not None:
                    try:
                        fh.close()
                    except OSError:
                        pass
        if resp is None:
            raise BreezeTTSUnavailable(f"Breeze TTS batch 始终 409（服务忙）: {base}")
        if resp.status_code != 200:
            raise BreezeTTSUnavailable(
                f"Breeze TTS batch HTTP {resp.status_code}: {resp.text[:300]}")
        sr = int(resp.headers.get("X-Sample-Rate") or SAMPLE_RATE)
        seg_bytes = [int(x) for x in str(resp.headers.get("X-Segment-Bytes") or "").split(",")
                     if x.strip()]
        pcm = resp.content
        if len(seg_bytes) != len(chunk) or sum(seg_bytes) > len(pcm):
            # 回退：按文本字符数比例切分（不应发生）
            LOG.warning("Breeze batch 分段头异常（segments=%d texts=%d bytes=%d），按比例切分",
                        len(seg_bytes), len(chunk), len(pcm))
            total_chars = max(1, sum(len(t) for t in chunk))
            seg_bytes = []
            acc = 0
            for i, t in enumerate(chunk):
                if i == len(chunk) - 1:
                    seg_bytes.append(len(pcm) - acc)
                else:
                    n = int(round(len(pcm) * len(t) / total_chars / 2)) * 2
                    seg_bytes.append(n)
                    acc += n
        gen_sec = round(time.time() - t0, 2)
        cursor = 0
        for i, nbytes in enumerate(seg_bytes):
            seg = pcm[cursor:cursor + nbytes]
            cursor += nbytes
            idx = start + i
            wav_path = Path(out_dir) / f"streamb_{role}_{idx:03d}.wav"
            _write_wav(wav_path, seg, sr)
            results.append({
                "index": idx,
                "role": role,
                "text": chunk[i],
                "path": str(wav_path),
                "duration": round(utils.probe_duration(wav_path), 3),
                "sample_rate": sr,
                "gen_sec": gen_sec,
                "provider": "breeze",
                "voice": voice["label"],
                "seed": int(seed if seed is not None else voice["seed"]),
                "cfg_scale": float(cfg_scale if cfg_scale is not None else voice["cfg_scale"]),
            })
    return results


def synthesize(role_texts: dict[str, list[str]], out_dir: Path, *,
               base: str | None = None, timeout: float = 900.0,
               auto_start: bool = True,
               voices: dict[str, dict] | None = None,
               refs: dict[str, tuple] | None = None) -> list[dict]:
    """多角色合成：{role: [texts]} → 扁平结果列表（保持 role 内顺序）。"""
    base = (base or DEFAULT_BASE).rstrip("/")
    if not server_alive(base):
        if not auto_start:
            raise BreezeTTSUnavailable(f"Breeze TTS 服务不可用: {base}")
        if not start_server(base):
            raise BreezeTTSUnavailable(f"Breeze TTS 服务启动失败: {base}")
    out_dir = Path(out_dir)
    results: list[dict] = []
    for role, texts in role_texts.items():
        cleaned = [t for t in (texts or []) if str(t or "").strip()]
        if not cleaned:
            continue
        ref = (refs or {}).get(role) or (None, "")
        ref_wav, ref_txt = (ref[0], ref[1]) if isinstance(ref, (tuple, list)) else (None, "")
        results.extend(synthesize_batch(cleaned, role, out_dir=out_dir, base=base,
                                        timeout=timeout,
                                        voice_spec=(voices or {}).get(role),
                                        ref_audio=ref_wav, ref_text=ref_txt))
    return results


def convert_result_to_mix(result: dict, workdir: Path, duration: float | None = None) -> dict:
    """单条合成结果 → 48k stereo 混音 wav。"""
    src = Path(result["path"])
    dst = Path(workdir) / "tts" / "mix" / f"{src.stem}_48k.wav"
    info = _to_mix_wav(src, dst, duration=duration)
    out = dict(result)
    out.update({"mix_path": str(dst), "mix_duration": info["duration"]})
    return out
