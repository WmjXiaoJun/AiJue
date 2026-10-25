# -*- coding: utf-8 -*-
"""ComfyUI H3 客户端（v3）：每镜提交 04 fl2v / 05 ref2va，落可追溯的 meta 证据。

接口契约：docs/V3_INTERFACES.md §4（严格接口）
- ``health()``：GET /system_stats + /queue，并检查 04/05 工作流文件存在；
- ``generate()``：子进程调 ``integrations/comfyui/run_h3_ref.py``，解析 stdout 最后一行
  JSON；成功后原子写 ``<out stem>.meta.json``（h3/workflow/prompt_id/seed/duration/
  ref_image/action/...）；失败 raise ``H3Error``，**绝不静默静态兜底**
  （兜底只能由 render_v3 显式 ``--fallback-static`` 开启并写 QC 标记）；
- ``normalize_clip()``：H3 原生 768²/1024x576/24fps → 成片规格 1280x720/25fps/精确时长；
- ``meta_ok()`` / ``load_meta()`` / ``assert_unique_prompt_ids()``：QC 可追溯性校验。

约定：
- ``prompt_id`` 只来自 ComfyUI（CLI 从 /history 取回），绝不伪造；为空即失败。
- 默认 seed = ``DEFAULT_SEED + shot.index * 17``（稳定可复现）；显式 seed 优先。
- 模板 ``h3.fps`` / ``h3.resolution`` 可覆盖模块默认值（generate 的 ``template=`` 参数，
  调用方显式传入的 width/height/fps（非默认值）优先于模板）。
- 生成尺寸：audio_ref 与 fl2v 一致——保留调用方/模板尺寸，仅对齐 32 倍数（H3 约束），
  不强制 fl2v 768²；meta 的 width/height/fps 记录产物实际值（ffprobe）。
- audio_ref 的 wav 先转 32kHz 单声道 PCM s16le（H3 audio VAE 友好）；``audio_wav=None``
  时自动用 1.0s 静音参考音频（05 的 ref_audios 可选）驱动，meta 标 ``audio_silent=true``；
  fl2v 不使用音频（有尾帧时首尾帧同尺寸）。
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Iterable, Sequence

from . import config, utils

LOG = logging.getLogger("pipeline")

# ---- 契约常量（docs/V3_INTERFACES.md §4） --------------------------------- #
DEFAULT_SERVER = "http://127.0.0.1:8188"
DEFAULT_SEED = 20261026
WORKFLOWS = {"audio_ref": "05_ref2va_4step_sla", "fl2v": "04_i2v_fl2v_4step_sla"}

DEFAULT_WIDTH, DEFAULT_HEIGHT, DEFAULT_FPS = 1024, 576, 24
FINAL_WIDTH, FINAL_HEIGHT, FINAL_FPS = 1280, 720, 25
H3_AUDIO_RATE = 32000          # H3 audio VAE 友好采样率
SILENCE_SECONDS = 1.0          # 无 TTS 时 audio_ref 的静音参考音频时长
META_SUFFIX = ".meta.json"

DEFAULT_COMFY_ROOT = Path(r"D:\BaiduSyncdisk\wmj_work\XJ\ComfyUI")
COMFY_ROOT = Path(os.environ.get("COMFYUI_ROOT") or DEFAULT_COMFY_ROOT)
WORKFLOW_DIR = COMFY_ROOT / "user" / "default" / "workflows"
CLI_PATH = config.REPO_ROOT / "integrations" / "comfyui" / "run_h3_ref.py"
CLI_ENV = "H3_COMFY_CLI"


class H3Error(RuntimeError):
    """H3/ComfyUI 生成失败（不可静默兜底）。"""


# --------------------------------------------------------------------------- #
# 配置 / 路径
# --------------------------------------------------------------------------- #
def default_server() -> str:
    """ComfyUI 地址：COMFYUI_URL 环境变量优先，否则默认 127.0.0.1:8188。"""
    return str(os.environ.get("COMFYUI_URL") or DEFAULT_SERVER).rstrip("/")


def workflow_for_mode(mode: str) -> str:
    """mode -> H3 工作流名（audio_ref=05_ref2va，fl2v=04_i2v_fl2v）。"""
    try:
        return WORKFLOWS[str(mode)]
    except KeyError as exc:
        raise H3Error(
            f"未知 H3 模式 {mode!r}；支持 {sorted(WORKFLOWS)}"
        ) from exc


def workflow_path(name: str) -> Path:
    """工作流 UI JSON 路径（ComfyUI/user/default/workflows）。"""
    return WORKFLOW_DIR / f"{name}.json"


def resolve_cli(cli: str | Path | None = None) -> Path | None:
    """定位 H3 ref CLI：显式参数 > H3_COMFY_CLI > H3_DYNAMIC_CLI > 默认路径。

    返回 None 表示 CLI 不可用（generate 会 raise H3Error，不做静态兜底）。
    """
    if cli:
        p = Path(cli)
        return p.resolve() if p.is_file() else None
    for candidate in (os.environ.get(CLI_ENV), os.environ.get("H3_DYNAMIC_CLI"), CLI_PATH):
        if not candidate:
            continue
        p = Path(candidate)
        if p.is_file():
            return p.resolve()
    return None


def resolve_h3_config(template: dict | None = None, *, mode: str | None = None) -> dict:
    """从模板 h3 配置解析生成参数；缺省回退模块默认（1024x576/24fps）。

    返回 ``{"workflow","mode","width","height","fps","duration_min","duration_max"}``。
    尺寸只做 32 倍数对齐（generate 内完成），模板声明值原样保留（fl2v 不再强制 768²）。
    """
    h3 = (template or {}).get("h3") if isinstance(template, dict) else {}
    h3 = h3 if isinstance(h3, dict) else {}
    res = str(h3.get("resolution") or "").strip()
    width, height = utils.parse_resolution(res) if res else (DEFAULT_WIDTH, DEFAULT_HEIGHT)
    try:
        fps = int(h3.get("fps") or DEFAULT_FPS)
    except (TypeError, ValueError):
        fps = DEFAULT_FPS
    wf = str(h3.get("workflow") or "").strip()
    if mode:
        wf = WORKFLOWS.get(str(mode), wf)
    if not wf:
        wf = WORKFLOWS["audio_ref"]
    resolved_mode = str(mode or ("audio_ref" if ("05" in wf or "ref2va" in wf) else "fl2v"))
    duration = h3.get("duration")
    dur_min, dur_max = 2.0, 10.0
    if isinstance(duration, (list, tuple)) and len(duration) == 2:
        try:
            dur_min, dur_max = float(duration[0]), float(duration[1])
        except (TypeError, ValueError):
            pass
    elif isinstance(duration, (int, float)) and not isinstance(duration, bool):
        dur_min = dur_max = float(duration)
    return {"workflow": wf, "mode": resolved_mode, "width": int(width),
            "height": int(height), "fps": fps,
            "duration_min": dur_min, "duration_max": dur_max}


# --------------------------------------------------------------------------- #
# meta / seed 工具
# --------------------------------------------------------------------------- #
def meta_path_for(out_mp4: str | Path) -> Path:
    """``clips/s002.mp4`` -> ``clips/s002.meta.json``（<out stem>.meta.json）。"""
    p = Path(out_mp4)
    return p.with_suffix(META_SUFFIX) if p.suffix else Path(str(p) + META_SUFFIX)


def load_meta(path: str | Path) -> dict | None:
    """读镜 meta；缺失/损坏返回 None。也接受成片 mp4 路径（自动找同名 meta）。"""
    p = Path(path)
    if p.suffix.lower() != ".json":
        p = meta_path_for(p)
    meta = utils.read_json(p)
    return meta if isinstance(meta, dict) else None


def meta_ok(meta: Any) -> bool:
    """QC 可追溯性最低要求：h3=true + workflow + prompt_id + seed 齐全。"""
    if not isinstance(meta, dict):
        return False
    if meta.get("h3") is not True:
        return False
    workflow = str(meta.get("workflow") or "").strip()
    prompt_id = str(meta.get("prompt_id") or "").strip()
    if not workflow or not prompt_id:
        return False
    seed = meta.get("seed")
    if seed is None or isinstance(seed, bool):
        return False
    try:
        int(seed)
    except (TypeError, ValueError):
        return False
    return True


def _as_meta(item: Any) -> dict | None:
    if item is None:
        return None
    if isinstance(item, dict):
        nested = item.get("meta")
        if isinstance(nested, dict) and "prompt_id" not in item:
            return nested
        return item
    if isinstance(item, (str, Path)):
        return load_meta(item)
    return None


def assert_unique_prompt_ids(metas: Iterable[Any] | Any) -> list[str]:
    """校验每镜 ``prompt_id`` 非空且全局唯一（ComfyUI 证据可追溯）。

    ``metas``：meta dict / meta JSON 路径 / 成片路径的序列（None 跳过，h3=false 的卡片跳过）。
    返回按顺序的 prompt_id 列表；任一缺失/重复 raise ``H3Error``。
    """
    if isinstance(metas, (dict, str, Path)):
        metas = [metas]
    seen: dict[str, str] = {}
    ids: list[str] = []
    for item in metas or []:
        meta = _as_meta(item)
        if meta is None:
            continue
        if meta.get("h3") is False:
            continue
        sid = str(meta.get("shot_id") or meta.get("id") or "").strip()
        pid = str(meta.get("prompt_id") or "").strip()
        if not pid:
            raise H3Error(f"镜头 {sid or '?'} 缺少 prompt_id（H3 证据不可追溯）")
        if pid in seen:
            raise H3Error(
                f"prompt_id 重复: {pid}（镜头 {seen[pid] or '?'} / {sid or '?'}），"
                "ComfyUI 证据必须每镜唯一")
        seen[pid] = sid
        ids.append(pid)
    return ids


def _shot_index(shot: Any) -> int:
    """镜头序号（0 基）：shot.index > shot.shot_index > shot.no-1 > id 数字后缀。"""
    if not isinstance(shot, dict):
        return 0
    for key in ("index", "shot_index"):
        value = shot.get(key)
        if value is not None:
            try:
                return max(0, int(value))
            except (TypeError, ValueError):
                pass
    for key in ("no", "shot_no"):
        value = shot.get(key)
        if value is not None:
            try:
                return max(0, int(value) - 1)
            except (TypeError, ValueError):
                pass
    tail = "".join(ch for ch in str(shot.get("id") or "") if ch.isdigit())
    return max(0, int(tail) - 1) if tail else 0


def seed_for_shot(shot: Any) -> int:
    """默认 seed：DEFAULT_SEED + shot.index*17（稳定可复现）。"""
    return DEFAULT_SEED + _shot_index(shot) * 17


def _action_for(shot: Any) -> str:
    if not isinstance(shot, dict):
        return ""
    for key in ("action",):
        value = shot.get(key)
        if value:
            return str(value)
    v3 = shot.get("v3")
    if isinstance(v3, dict) and v3.get("action"):
        return str(v3["action"])
    plan = shot.get("shot_plan")
    if isinstance(plan, dict) and plan.get("action"):
        return str(plan["action"])
    return ""


# --------------------------------------------------------------------------- #
# ComfyUI HTTP
# --------------------------------------------------------------------------- #
def _get_json(url: str, timeout: float = 5.0) -> dict:
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 - 本机地址
        raw = resp.read().decode("utf-8", "replace")
    return json.loads(raw) if raw.strip() else {}


def _system_summary(stats: dict) -> dict:
    system = stats.get("system") if isinstance(stats, dict) else {}
    system = system if isinstance(system, dict) else {}
    devices = stats.get("devices") if isinstance(stats, dict) else []
    summary = {
        "comfyui_version": system.get("comfyui_version"),
        "os": system.get("os"),
        "python_version": system.get("python_version"),
    }
    if isinstance(devices, list):
        summary["devices"] = [
            {"name": d.get("name"), "vram_total": d.get("vram_total"),
             "vram_free": d.get("vram_free")}
            for d in devices[:2] if isinstance(d, dict)
        ]
    return summary


def server_alive(server: str | None = None, timeout: float = 2.0) -> bool:
    """ComfyUI 是否可达（/system_stats 返回 system 字段）。"""
    try:
        stats = _get_json(str(server or default_server()).rstrip("/") + "/system_stats",
                          timeout=timeout)
        return isinstance(stats, dict) and "system" in stats
    except Exception:  # noqa: BLE001 - health 语义：不可达即 False
        return False


def health(server: str | None = None, timeout: float = 3.0) -> dict:
    """ComfyUI 健康检查：/system_stats + /queue + 04/05 工作流文件存在性。

    ``ok`` = 服务可达（两个端点都通）且两个工作流文件都存在。
    服务离线不 raise（返回 ok=False + errors），方便 render 前给出可读诊断。
    """
    srv = str(server or default_server()).rstrip("/")
    result: dict[str, Any] = {
        "ok": False, "server": srv, "queue_running": 0, "queue_pending": 0,
        "workflows": list(WORKFLOWS.values()), "workflow_files": {},
        "system": {}, "server_ok": False, "errors": [],
    }
    for mode, name in WORKFLOWS.items():
        path = workflow_path(name)
        result["workflow_files"][name] = {
            "mode": mode, "path": str(path), "exists": path.is_file(),
        }
    missing = [name for name, info in result["workflow_files"].items()
               if not info["exists"]]
    if missing:
        result["errors"].append("工作流文件缺失: " + ", ".join(missing))

    try:
        stats = _get_json(srv + "/system_stats", timeout=timeout)
    except Exception as exc:  # noqa: BLE001
        result["errors"].append(f"/system_stats 不可用: {exc}")
    else:
        result["server_ok"] = True
        result["system"] = _system_summary(stats)

    try:
        queue = _get_json(srv + "/queue", timeout=timeout)
    except Exception as exc:  # noqa: BLE001
        result["errors"].append(f"/queue 不可用: {exc}")
    else:
        result["queue_running"] = len(queue.get("queue_running") or [])
        result["queue_pending"] = len(queue.get("queue_pending") or [])

    result["ok"] = not result["errors"]
    return result


# --------------------------------------------------------------------------- #
# 子进程 / 音频
# --------------------------------------------------------------------------- #
def _tail(text: Any, limit: int = 1600) -> str:
    if isinstance(text, bytes):
        text = text.decode("utf-8", "replace")
    return str(text or "")[-limit:]


def _run_cli(cli: Path, args: Sequence[str], timeout: float) -> subprocess.CompletedProcess:
    cmd = [sys.executable, str(cli), *args]
    LOG.info("H3 生成: %s", subprocess.list2cmdline(cmd))
    env = os.environ.copy()
    # ComfyUI/CLI 日志含中文时保持子进程 UTF-8，避免 Windows 默认 GBK 造成解析噪音
    env.setdefault("PYTHONIOENCODING", "utf-8")
    env.setdefault("PYTHONUTF8", "1")
    try:
        proc = subprocess.run(
            cmd, cwd=str(config.REPO_ROOT), capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=timeout, env=env,
        )
    except subprocess.TimeoutExpired as exc:
        raise H3Error(
            f"H3 生成超时（>{timeout}s）: {_tail(exc.stderr) or _tail(exc.stdout)}"
        ) from exc
    except OSError as exc:  # pragma: no cover - 环境异常
        raise H3Error(f"H3 CLI 无法执行: {cli}: {exc}") from exc
    if proc.returncode != 0:
        tail = _tail(proc.stderr) or _tail(proc.stdout)
        raise H3Error(f"H3 生成失败 (exit {proc.returncode}): {tail}")
    return proc


def _parse_last_json(stdout: str) -> dict:
    """解析 stdout 最后一行 JSON（CLI 日志行在前，结果 JSON 在最后）。"""
    for line in reversed((stdout or "").splitlines()):
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            return obj
    raise H3Error(f"H3 CLI stdout 没有有效 JSON 结果行: {_tail(stdout)}")


def _to_h3_audio(wav: str | Path, workdir: Path) -> Path:
    """把每镜 TTS wav 转成 32kHz / 单声道 / PCM s16le（H3 audio VAE 友好）。"""
    src = Path(wav).resolve()
    if not src.is_file():
        raise H3Error(f"H3 音频不存在: {src}")
    aux_dir = utils.ensure_dir(Path(workdir) / "h3_aux")
    dst = aux_dir / f"{src.stem}.h3_32k.wav"
    try:
        fresh = dst.is_file() and dst.stat().st_size > 100 \
            and dst.stat().st_mtime >= src.stat().st_mtime
    except OSError:
        fresh = False
    if fresh:
        return dst
    tmp = aux_dir / f"{src.stem}.h3_32k.tmp.wav"
    try:
        utils.ffmpeg([
            "-i", str(src), "-ar", str(H3_AUDIO_RATE), "-ac", "1",
            "-c:a", "pcm_s16le", str(tmp),
        ])
        tmp.replace(dst)
    except Exception as exc:  # noqa: BLE001
        try:
            tmp.unlink()
        except OSError:
            pass
        raise H3Error(f"H3 音频转 32kHz 单声道失败: {src}: {exc}") from exc
    if not dst.is_file() or dst.stat().st_size < 100:
        raise H3Error(f"H3 音频转换产物无效: {dst}")
    return dst


def _silence_audio(workdir: Path) -> Path:
    """生成/复用 1.0s 静音参考音频（32kHz 单声道 PCM s16le，05 ref_audios 可选输入）。

    用于无台词镜头：仍走 05_ref2va（画面由 H3 重绘），音频用静音参考驱动，
    避免 04 fl2v 把 2D cutout 原样烘进画面。
    """
    aux_dir = utils.ensure_dir(Path(workdir) / "h3_aux")
    name = f"silence_{SILENCE_SECONDS:.1f}s_32k"
    dst = aux_dir / f"{name}.wav"
    try:
        fresh = dst.is_file() and dst.stat().st_size > 100
    except OSError:
        fresh = False
    if fresh:
        return dst
    tmp = aux_dir / f"{name}.tmp.wav"
    try:
        utils.ffmpeg([
            "-f", "lavfi", "-i", f"anullsrc=r={H3_AUDIO_RATE}:cl=mono",
            "-t", f"{SILENCE_SECONDS:.3f}", "-c:a", "pcm_s16le", str(tmp),
        ])
        tmp.replace(dst)
    except Exception as exc:  # noqa: BLE001
        try:
            tmp.unlink()
        except OSError:
            pass
        raise H3Error(f"H3 静音参考音频生成失败: {exc}") from exc
    if not dst.is_file() or dst.stat().st_size < 100:
        raise H3Error(f"H3 静音参考音频产物无效: {dst}")
    return dst


def _probe_video(path: Path) -> dict:
    try:
        return utils.probe_video(path)
    except Exception as exc:  # noqa: BLE001
        LOG.warning("H3 产物 ffprobe 失败 %s: %s", path, exc)
        return {}


def _snap32(value: int) -> int:
    return max(256, (int(value) // 32) * 32)


# --------------------------------------------------------------------------- #
# 生成
# --------------------------------------------------------------------------- #
def generate(shot: dict, first_image: str | Path, out_mp4: str | Path, *,
             mode: str, prompt: str, duration: float,
             audio_wav: str | Path | None = None,
             last_image: str | Path | None = None,
             seed: int | None = None,
             width: int = DEFAULT_WIDTH, height: int = DEFAULT_HEIGHT,
             fps: int = DEFAULT_FPS,
             workdir: str | Path | None = None,
             timeout: int = 1800,
             template: dict | None = None,
             cli: str | Path | None = None) -> dict:
    """生成一个 H3 镜头并落 ``<out stem>.meta.json``，返回 meta dict。

    参数（严格接口见 docs/V3_INTERFACES.md §4）：
    - ``mode``: ``audio_ref``（05_ref2va 音驱动口型）或 ``fl2v``（04_i2v_fl2v 首尾帧）；
    - ``prompt``: H3 四段式 prompt（action_library.build_h3_prompt 产出）；
    - ``duration``: 目标时长（秒）；H3 原生长度由 CLI 按 124 帧最小/17 帧步进计算；
    - ``audio_wav``: audio_ref 的每镜 TTS wav（内部转 32kHz 单声道）；给 None 时自动用
      1.0s 静音参考音频（meta 标 ``audio_silent=true``、``audio_src=""``）；fl2v 忽略；
    - ``last_image``: fl2v 尾帧（可选；同首帧尺寸，最终尺寸由 width/height 32 对齐）；
    - ``seed``: 缺省用 ``DEFAULT_SEED + shot.index*17``；
    - ``template``: 模板 dict（可选）；其 ``h3.fps`` / ``h3.resolution`` 覆盖模块默认值，
      调用方显式传入的非默认 width/height/fps 优先；
    - ``cli``: CLI 覆盖（默认 integrations/comfyui/run_h3_ref.py，可被 H3_COMFY_CLI 覆盖）。

    失败一律 raise ``H3Error``（含 stderr 尾部），不写 meta、不静态兜底。
    """
    if mode not in WORKFLOWS:
        raise H3Error(f"未知 H3 模式 {mode!r}；支持 {sorted(WORKFLOWS)}")
    workflow = WORKFLOWS[mode]
    out = Path(out_mp4).resolve()
    first = Path(first_image).resolve()
    if not first.is_file():
        raise H3Error(f"H3 首帧不存在: {first}")
    prompt = str(prompt or "").strip()
    if not prompt:
        raise H3Error("H3 prompt 不能为空")
    try:
        req_duration = float(duration)
    except (TypeError, ValueError) as exc:
        raise H3Error(f"H3 duration 非法: {duration!r}") from exc
    if req_duration <= 0:
        raise H3Error(f"H3 duration 必须 > 0（当前 {req_duration}）")

    shot = shot if isinstance(shot, dict) else {}
    shot_index = _shot_index(shot)
    seed_value = int(seed) if seed is not None else seed_for_shot(shot)

    # 模板 h3.fps/resolution 覆盖模块默认值（显式非默认参数优先）
    if template is not None:
        cfg = resolve_h3_config(template, mode=mode)
        if (int(width), int(height)) == (DEFAULT_WIDTH, DEFAULT_HEIGHT):
            width, height = cfg["width"], cfg["height"]
        if int(fps) == DEFAULT_FPS:
            fps = cfg["fps"]
    width, height, fps = int(width), int(height), int(fps)

    # H3 尺寸约束：32 倍数（audio_ref 与 fl2v 都保留调用方/模板尺寸，不强制 768²）
    snapped = (_snap32(width), _snap32(height))
    if snapped != (width, height):
        LOG.info("H3 尺寸对齐 32 倍数: %sx%s -> %sx%s", width, height, *snapped)
    width, height = snapped

    work = Path(workdir).resolve() if workdir else out.parent / "h3_work"
    utils.ensure_dir(work)

    # 音频：audio_ref 用 TTS wav（转 32kHz 单声道），无 TTS 时用 1.0s 静音参考；fl2v 不用音频
    h3_audio: Path | None = None
    audio_src: Path | None = None
    audio_silent = False
    if mode == "audio_ref":
        if audio_wav:
            audio_src = Path(audio_wav).resolve()
            h3_audio = _to_h3_audio(audio_src, work)
        else:
            h3_audio = _silence_audio(work)
            audio_silent = True
            LOG.info("audio_ref 无 TTS，使用 %.1fs 静音参考音频: %s",
                     SILENCE_SECONDS, h3_audio)
    elif audio_wav:
        LOG.info("fl2v 不使用音频，忽略 audio_wav: %s", audio_wav)

    # 尾帧：仅 fl2v 有效
    last: Path | None = None
    if last_image:
        candidate = Path(last_image).resolve()
        if not candidate.is_file():
            raise H3Error(f"H3 尾帧不存在: {candidate}")
        if mode == "audio_ref":
            LOG.warning("audio_ref 模式忽略 last_image: %s", candidate)
        else:
            last = candidate
    elif mode == "fl2v":
        LOG.warning("fl2v 未提供 last_image，按 i2v（仅首帧）生成: %s", out.name)

    cli_path = resolve_cli(cli)
    if cli_path is None:
        raise H3Error(
            "H3 CLI 不可用：需要 integrations/comfyui/run_h3_ref.py"
            "（或用 H3_COMFY_CLI 指定）；拒绝静态兜底")

    prompts_dir = utils.ensure_dir(work / "prompts")
    prompt_file = prompts_dir / f"{out.stem}.txt"
    utils.write_text(prompt_file, prompt + "\n")

    if out.is_file():
        LOG.info("H3 目标已存在，将覆盖: %s", out)
    utils.ensure_dir(out.parent)

    args = [
        "--mode", mode, "--image", str(first), "--out", str(out),
        "--duration", f"{req_duration:.3f}",
        "--width", str(width), "--height", str(height), "--fps", str(fps),
        "--seed", str(seed_value), "--prompt-file", str(prompt_file),
    ]
    if last is not None:
        args += ["--last-frame", str(last)]
    if h3_audio is not None:
        args += ["--audio", str(h3_audio)]

    proc = _run_cli(cli_path, args, timeout)
    if not out.is_file() or out.stat().st_size < 512:
        raise H3Error(f"H3 CLI 未产出有效视频: {out}")

    result = _parse_last_json(proc.stdout)
    if result.get("ok") is False:
        raise H3Error(f"H3 生成失败（CLI ok=false）: {json.dumps(result, ensure_ascii=False)[:1200]}")
    prompt_id = str(result.get("prompt_id") or "").strip()
    if not prompt_id:
        raise H3Error(
            "H3 CLI 未返回 prompt_id（ComfyUI 证据不可追溯，拒绝伪造/兜底）: "
            + json.dumps(result, ensure_ascii=False, default=str)[:1200])

    raw_workflow = str(result.get("workflow") or "").strip()
    workflow_name = Path(raw_workflow).stem if raw_workflow else workflow
    if workflow_name != workflow:
        LOG.warning("H3 workflow 与模式不一致：mode=%s workflow=%s（期望 %s）",
                    mode, workflow_name, workflow)

    probe = result.get("probe") if isinstance(result.get("probe"), dict) else {}
    video_probe = probe.get("video") if isinstance(probe.get("video"), dict) else {}
    actual_width = int(video_probe.get("width") or result.get("width") or width)
    actual_height = int(video_probe.get("height") or result.get("height") or height)
    actual_fps_raw = video_probe.get("fps") or result.get("fps") or fps
    try:
        actual_fps = float(actual_fps_raw)
    except (TypeError, ValueError):
        actual_fps = float(fps)
    native_duration = probe.get("duration_sec") if probe else None
    try:
        native_duration = round(float(native_duration), 3) if native_duration is not None else None
    except (TypeError, ValueError):
        native_duration = None
    try:
        length = int(result.get("length") or 0)
    except (TypeError, ValueError):
        length = 0

    meta: dict[str, Any] = {
        "h3": True,
        "ok": True,
        "mode": mode,
        "workflow": workflow_name,
        "workflow_file": raw_workflow or f"{workflow}.json",
        "prompt_id": prompt_id,
        "seed": seed_value,
        "duration": round(req_duration, 3),
        "h3_duration": native_duration,
        "length": length,
        "width": actual_width,
        "height": actual_height,
        "fps": round(actual_fps, 3),
        "gen_width": width,
        "gen_height": height,
        "gen_fps": fps,
        "ref_image": str(first),
        "last_image": str(last) if last else "",
        "audio": str(h3_audio) if h3_audio else "",
        "audio_src": str(audio_src) if audio_src else "",
        "audio_silent": bool(audio_silent),
        "action": _action_for(shot),
        "shot_id": str(shot.get("id") or ""),
        "shot_index": shot_index,
        "prompt": prompt,
        "gen_sec": result.get("gen_sec"),
        "path": str(out),
        "has_audio": probe.get("has_audio") if probe else result.get("has_audio"),
        "probe": probe,
        "sheet": result.get("sheet") or "",
        "cli": str(cli_path),
        "server": default_server(),
        "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    if not meta_ok(meta):
        raise H3Error(f"H3 meta 证据不完整: {json.dumps(meta, ensure_ascii=False)[:1200]}")

    meta_path = meta_path_for(out)
    utils.write_json(meta_path, meta)  # 原子写：.tmp -> replace
    LOG.info("H3 ok shot=%s prompt_id=%s workflow=%s seed=%s gen=%.1fs -> %s",
             meta["shot_id"] or out.stem, prompt_id, workflow_name, seed_value,
             float(meta.get("gen_sec") or 0.0), meta_path.name)
    return meta


# --------------------------------------------------------------------------- #
# 归一化（H3 原生 -> 成片规格）
# --------------------------------------------------------------------------- #
def normalize_clip(src: str | Path, dst: str | Path, *, duration: float,
                   width: int = FINAL_WIDTH, height: int = FINAL_HEIGHT,
                   fps: int = FINAL_FPS) -> dict:
    """H3 原生尺寸/fps → 成片规格，并保证精确时长（不足克隆末帧，超出裁掉）。

    成片规格默认 1280x720/25fps；输出无音轨（成片音轨由 render_v3 的 TTS 混音负责）。
    返回 ``{"path","width","height","fps","duration","src","src_duration",...}``。
    """
    src_p = Path(src).resolve()
    dst_p = Path(dst).resolve()
    if not src_p.is_file():
        raise H3Error(f"待归一化片段不存在: {src_p}")
    try:
        target = float(duration)
    except (TypeError, ValueError) as exc:
        raise H3Error(f"normalize_clip duration 非法: {duration!r}") from exc
    if target <= 0:
        raise H3Error(f"normalize_clip duration 必须 > 0（当前 {target}）")
    w, h, f = int(width), int(height), int(fps)
    src_info = _probe_video(src_p)

    utils.ensure_dir(dst_p.parent)
    tmp = dst_p.with_name(f"{dst_p.stem}.normalize.tmp{dst_p.suffix or '.mp4'}")
    # tpad 克隆末帧保证短片段能凑够时长，再 trim 到精确秒数
    vf = (
        f"fps={f},scale={w}:{h}:force_original_aspect_ratio=increase,"
        f"crop={w}:{h},setsar=1,"
        f"tpad=stop_mode=clone:stop_duration={max(1.0, target + 1.0):.3f},"
        f"trim=0:{target:.3f},setpts=PTS-STARTPTS"
    )
    try:
        utils.ffmpeg([
            "-i", str(src_p), "-vf", vf, "-an", "-t", f"{target:.3f}",
            "-c:v", "libx264", "-preset", config.X264_PRESET,
            "-crf", config.X264_CRF, "-pix_fmt", "yuv420p", "-r", str(f),
            str(tmp),
        ])
        tmp.replace(dst_p)
    except Exception as exc:  # noqa: BLE001
        try:
            tmp.unlink()
        except OSError:
            pass
        raise H3Error(f"normalize_clip 失败 {src_p} -> {dst_p}: {exc}") from exc

    info = _probe_video(dst_p)
    out_duration = info.get("duration") or 0.0
    src_duration = src_info.get("duration") or 0.0
    frame = 1.0 / max(1, f)
    trimmed = bool(src_duration > target + frame / 2)
    padded = bool(0 < src_duration < target - frame / 2)
    if out_duration and abs(float(out_duration) - target) > max(0.12, 2 * frame):
        LOG.warning("normalize_clip 时长偏差 %.2fs（目标 %.2fs）: %s",
                    float(out_duration) - target, target, dst_p)
    return {
        "path": str(dst_p),
        "src": str(src_p),
        "width": info.get("width") or w,
        "height": info.get("height") or h,
        "fps": info.get("fps") or float(f),
        "duration": out_duration,
        "target_duration": target,
        "src_duration": src_duration,
        "src_width": src_info.get("width") or 0,
        "src_height": src_info.get("height") or 0,
        "src_fps": src_info.get("fps") or 0.0,
        "trimmed": trimmed,
        "padded": padded,
    }


__all__ = [
    "DEFAULT_SERVER", "DEFAULT_SEED", "WORKFLOWS", "DEFAULT_WIDTH", "DEFAULT_HEIGHT",
    "DEFAULT_FPS", "FINAL_WIDTH", "FINAL_HEIGHT", "FINAL_FPS", "H3_AUDIO_RATE",
    "SILENCE_SECONDS", "META_SUFFIX", "H3Error", "health", "server_alive",
    "workflow_for_mode", "resolve_h3_config", "resolve_cli", "generate",
    "normalize_clip", "meta_ok", "meta_path_for", "load_meta",
    "assert_unique_prompt_ids", "seed_for_shot",
]
