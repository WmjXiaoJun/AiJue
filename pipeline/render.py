"""render 命令：episode.json -> 成片 mp4。

流程：
  1) 确保每镜头 mock 片段（缺失/过期则生成，可复用）
  2) 逐镜头 edge-tts 旁白（失败 -> 1s 静音兜底，不中断）
  3) 镜头音轨：语速补偿 + 前导静音 + 精确补/裁到镜头时长（含 sfx）
  4) concat 拼接视频片段 -> video_raw.mp4
  5) 生成全局 ASS（旁白字幕 + 花字 + 人名条 + 台标 + 章节标）
  6) concat 拼接音轨 -> narration.wav
  7) BGM（ffmpeg 合成的柔和和弦垫）/ SFX
  8) 一次编码：烧入 ASS + 混音 + loudnorm -> final.mp4（H.264）
所有中间文件都在 workdir，可重复执行复用。
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from . import ass_builder, characters, config, fonts, mock_shot, tts_engine, utils

LOG = logging.getLogger("pipeline")

CLIP_WORKERS = 6
AUDIO_WORKERS = 8
CLIP_VERSION = "mock-v13-l10n"
TTS_LEAD_IN = 0.15
BGM_DURATION = 32.0


# --------------------------------------------------------------------------
# 通用
# --------------------------------------------------------------------------
def _valid_file(path: Path | str | None, min_size: int = 512) -> bool:
    try:
        p = Path(path)
        return p.is_file() and p.stat().st_size >= min_size
    except OSError:
        return False


def _shot_id(shot: dict) -> str:
    return str(shot.get("id") or "shot")


def _shot_duration(shot: dict) -> float:
    try:
        d = float(shot.get("duration") or 0)
    except (TypeError, ValueError):
        d = 0.0
    return d if d > 0.05 else 5.0


# --------------------------------------------------------------------------
# Stage 1: 镜头片段
# --------------------------------------------------------------------------
def _clip_hash(shot: dict, width: int, height: int, fps: int) -> str:
    return utils.sha1_text(
        json.dumps(shot, ensure_ascii=False, sort_keys=True),
        width, height, fps, CLIP_VERSION,
    )


def _shot_source_path(workdir: Path, sid: str) -> Path | None:
    """平台「生成台」产物：<workdir>/shots/<sid>.mp4（contract 约定的镜头片段位置）。"""
    cand = workdir / "shots" / f"{sid}.mp4"
    return cand if _valid_file(cand, 2000) else None


def _shot_source_fingerprint(path: Path | None) -> str:
    """平台片段指纹（文件大小 + mtime_ns）。

    生成台重新生成镜头后指纹变化，使 clips/ 旧缓存自动失效、改用新版本。
    """
    if path is None:
        return ""
    try:
        st = path.stat()
        return f"{st.st_size}-{st.st_mtime_ns}"
    except OSError:
        return ""


def _probe_external(path: Path, width: int, height: int, fps: int,
                    duration: float) -> dict:
    """校验平台片段能否直接进 concat：分辨率/帧率/编码/时长都要对齐。"""
    try:
        info = utils.probe_video(path)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)}
    ok = (
        info["width"] == width
        and info["height"] == height
        and abs(info["fps"] - fps) < 0.5
        and str(info["codec"]).lower() == "h264"
        and abs(info["duration"] - duration) <= 0.20
    )
    return {"ok": ok, **info}


def _normalize_external_clip(src: Path, dst: Path, width: int, height: int,
                             fps: int, duration: float) -> None:
    """平台片段参数不匹配时：缩放/补边/改帧率/按镜头时长裁齐后再复用。"""
    utils.ensure_dir(dst.parent)
    vf = (
        f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
        f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,setsar=1,"
        f"fps={fps},tpad=stop_mode=clone:stop_duration=30"
    )
    utils.ffmpeg([
        "-i", str(src), "-vf", vf, "-t", f"{duration:.3f}", "-an",
        "-c:v", "libx264", "-preset", config.X264_PRESET,
        "-crf", config.X264_CRF, "-pix_fmt", "yuv420p",
        "-movflags", "+faststart", str(dst),
    ])
    real = utils.probe_duration(dst)
    if abs(real - duration) > 0.30:
        raise RuntimeError(f"标准化后时长 {real:.2f}s != 镜头时长 {duration:.2f}s")


def ensure_clip(shot: dict, assets: list, episode: dict, workdir: Path,
                width: int, height: int, fps: int, force: bool = False,
                char_map: dict | None = None, char_fp: str = "",
                h3_dir: Path | None = None,
                locale: dict | None = None) -> dict:
    """返回 {"id","path","duration","reused","source","character","elapsed"}。

    片段来源优先级：clips/ 缓存 -> <workdir>/shots/<sid>.mp4 平台片段
    ->（--use-h3 时）outputs/h3/<sid>.mp4 真镜头 -> 角色/mock 生成。
    """
    sid = _shot_id(shot)
    clip_path = workdir / "clips" / f"{sid}.mp4"
    meta_path = workdir / "clips" / f"{sid}.meta.json"
    duration = _shot_duration(shot)
    external = None if force else _shot_source_path(workdir, sid)
    external_fp = _shot_source_fingerprint(external)
    h3_path = None
    if h3_dir is not None:
        candidate = Path(h3_dir) / f"{sid}.mp4"
        if _valid_file(candidate, 2000):
            h3_path = candidate
    h3_fp = _shot_source_fingerprint(h3_path)
    digest = _clip_hash(shot, width, height, fps)
    if external_fp:
        # 平台片段变化（生成台重新生成）时让 clips 缓存失效
        digest = utils.sha1_text(digest, external_fp)
    if char_fp:
        # 角色素材更新（重新生成立绘/表情/转面）时让 clips 缓存失效
        digest = utils.sha1_text(digest, char_fp)
    if h3_fp:
        digest = utils.sha1_text(digest, "h3", h3_fp)
    if not force:
        meta = utils.read_json(meta_path) or {}
        if _valid_file(clip_path, 2000) and meta.get("hash") == digest:
            return {"id": sid, "path": str(clip_path),
                    "duration": float(meta.get("duration") or duration),
                    "reused": True,
                    "source": meta.get("source") or "cache",
                    "character": meta.get("character") or "",
                    "elapsed": 0.0}
    # ---- 平台「生成台」版本优先复用（生成台选版本 -> 成片房渲染 闭环）----
    if not force:
        if external is not None:
            probe = _probe_external(external, width, height, fps, duration)
            if probe.get("ok"):
                utils.ensure_dir(clip_path.parent)
                shutil.copyfile(external, clip_path)
                utils.write_json(meta_path, {
                    "hash": digest, "duration": probe["duration"],
                    "source": "shots", "shots_fp": external_fp,
                    "version": CLIP_VERSION,
                })
                LOG.info("复用平台片段: %s (shots/%s.mp4)", sid, sid)
                return {"id": sid, "path": str(clip_path),
                        "duration": probe["duration"], "reused": True,
                        "source": "shots", "elapsed": 0.0}
            try:
                _normalize_external_clip(external, clip_path, width, height,
                                         fps, duration)
                utils.write_json(meta_path, {
                    "hash": digest, "duration": duration,
                    "source": "shots-normalized", "shots_fp": external_fp,
                    "version": CLIP_VERSION,
                })
                LOG.info("复用平台片段（已标准化到 %dx%d/%dfps）: %s",
                         width, height, fps, sid)
                return {"id": sid, "path": str(clip_path),
                        "duration": duration, "reused": True,
                        "source": "shots-normalized", "elapsed": 0.0}
            except Exception as exc:  # noqa: BLE001
                LOG.warning("平台片段 %s 不可用（%s），回退 mock 生成", external, exc)
    # ---- 可选：真实 H3 动漫镜头替换（--use-h3，默认关闭）----
    if (not force) and h3_path is not None:
        probe = _probe_external(h3_path, width, height, fps, duration)
        try:
            if probe.get("ok"):
                utils.ensure_dir(clip_path.parent)
                shutil.copyfile(h3_path, clip_path)
            else:
                _normalize_external_clip(h3_path, clip_path, width, height,
                                         fps, duration)
            utils.write_json(meta_path, {
                "hash": digest, "duration": duration, "source": "h3",
                "h3_fp": h3_fp, "character": "", "version": CLIP_VERSION,
            })
            LOG.info("使用 H3 真镜头: %s (outputs/h3/%s.mp4)", sid, sid)
            return {"id": sid, "path": str(clip_path), "duration": duration,
                    "reused": True, "source": "h3", "character": "",
                    "elapsed": 0.0}
        except Exception as exc:  # noqa: BLE001
            LOG.warning("H3 镜头 %s 不可用（%s），回退角色/mock 生成", h3_path, exc)
    spec = {
        "shot": shot,
        "assets": assets,
        "episode": {
            "aspect": (episode.get("aspect") or "16:9"),
            "resolution": f"{width}x{height}",
            "fps": fps,
        },
        "mode": "mock",
        "params": {},
    }
    t0 = time.time()
    result = mock_shot.generate_mock_clip(
        spec, clip_path, workdir, resolution=f"{width}x{height}", fps=fps,
        char_map=char_map, locale=locale,
    )
    elapsed = round(time.time() - t0, 2)
    utils.write_json(meta_path, {
        "hash": digest,
        "duration": result.get("duration"),
        "type": shot.get("type"),
        "elapsed": elapsed,
        "source": "mock",
        "character": result.get("character") or "",
        "shots_fp": external_fp,
        "version": CLIP_VERSION,
    })
    result["reused"] = False
    result["elapsed"] = elapsed
    return result


# --------------------------------------------------------------------------
# Stage 2: TTS
# --------------------------------------------------------------------------
def _voice_map(assets: list) -> dict:
    mapping = {}
    for a in assets or []:
        if isinstance(a, dict) and a.get("voice"):
            mapping[a.get("id")] = str(a["voice"])
    return mapping


def _tts_key(text: str, voice: str) -> str:
    return utils.sha1_text(text, voice, config.TTS_RATE)


def prepare_tts(shots: list, assets: list, workdir: Path, prog,
                pct0: float, pct1: float, force: bool = False) -> dict:
    """为有台词的镜头合成 TTS；返回 {key: {ok,path,duration,voice,error}}。"""
    tts_dir = utils.ensure_dir(workdir / "tts")
    voices = _voice_map(assets)
    tasks: dict[str, dict] = {}
    for shot in shots:
        text = str(shot.get("line") or "").strip()
        if not text:
            continue
        voice = voices.get(shot.get("speaker")) or config.DEFAULT_VOICE
        key = _tts_key(text, voice)
        tasks.setdefault(key, {
            "text": text, "voice": voice,
            "mp3": tts_dir / f"{key}.mp3", "vtt": tts_dir / f"{key}.vtt",
        })
    results: dict[str, dict] = {}
    pending = []
    for key, task in tasks.items():
        meta = utils.read_json(task["mp3"].with_suffix(".meta.json")) or {}
        if (not force) and _valid_file(task["mp3"], 1000) \
                and meta.get("text") == task["text"] and meta.get("voice") == task["voice"]:
            results[key] = {
                "ok": True, "path": str(task["mp3"]),
                "duration": float(meta.get("duration") or 0.0),
                "voice": task["voice"], "cached": True,
            }
        else:
            pending.append((key, task))
    prog(pct0, f"旁白合成：待生成 {len(pending)} 条 / 命中缓存 {len(tasks) - len(pending)} 条")

    def _one(item):
        key, task = item
        try:
            res = tts_engine.synth_with_retry(
                task["text"], task["voice"], task["mp3"], task["vtt"]
            )
            utils.write_json(task["mp3"].with_suffix(".meta.json"), {
                "text": task["text"], "voice": task["voice"],
                "duration": res["duration"],
            })
            res["cached"] = False
            return key, res
        except Exception as exc:  # noqa: BLE001
            LOG.warning("TTS 失败，静音兜底: %s", exc)
            ok = False
            try:
                tts_engine.make_silence_mp3(task["mp3"], 1.0)
                ok = True
            except Exception as exc2:  # noqa: BLE001
                LOG.warning("静音兜底也失败: %s", exc2)
            return key, {
                "ok": ok and False,  # 标记失败
                "path": str(task["mp3"]) if ok else None,
                "duration": 1.0 if ok else 0.0,
                "voice": task["voice"], "cached": False,
                "error": str(exc),
            }

    if pending:
        done = 0
        with ThreadPoolExecutor(max_workers=config.TTS_CONCURRENCY) as pool:
            futures = [pool.submit(_one, item) for item in pending]
            for fut in as_completed(futures):
                key, res = fut.result()
                results[key] = res
                done += 1
                prog(pct0 + (pct1 - pct0) * done / len(pending),
                     f"旁白合成 {done}/{len(pending)}")
    return results


def shot_tts_info(shot: dict, tts_results: dict, voices: dict) -> dict:
    text = str(shot.get("line") or "").strip()
    if not text:
        return {}
    voice = voices.get(shot.get("speaker")) or config.DEFAULT_VOICE
    return tts_results.get(_tts_key(text, voice)) or {}


# --------------------------------------------------------------------------
# Stage 3: 镜头音轨
# --------------------------------------------------------------------------
def _atempo_for(tts_info: dict, duration: float) -> float:
    if not tts_info or not tts_info.get("ok") or not tts_info.get("duration"):
        return 1.0
    target = max(0.6, duration - 0.30)
    ratio = float(tts_info["duration"]) / target
    if ratio <= 1.0:
        return 1.0
    return round(min(ratio, config.TTS_MAX_ATEMPO), 4)


def build_shot_audio(shot: dict, tts_info: dict, duration: float, workdir: Path,
                     sfx_wav: Path | None, force: bool = False) -> dict:
    sid = _shot_id(shot)
    out = workdir / "audio" / f"{sid}.wav"
    utils.ensure_dir(out.parent)
    meta_path = out.with_suffix(".meta.json")
    atempo = _atempo_for(tts_info, duration)
    digest = utils.sha1_text(
        (tts_info or {}).get("path"), (tts_info or {}).get("duration"),
        round(duration, 4), atempo, shot.get("sfx"), str(sfx_wav or ""),
    )
    if not force and _valid_file(out, 1000):
        meta = utils.read_json(meta_path) or {}
        if meta.get("hash") == digest:
            return {"id": sid, "path": str(out), "duration": duration,
                    "atempo": atempo, "reused": True}
    has_voice = bool(
        tts_info and tts_info.get("ok") and tts_info.get("path")
        and _valid_file(tts_info["path"], 1000)
    )
    args: list[str] = []
    filters: list[str] = []
    if has_voice:
        args += ["-i", str(tts_info["path"])]
        chain = [f"aresample={config.SAMPLE_RATE}", "pan=stereo|c0=c0|c1=c0"]
        if atempo > 1.001:
            chain.append(f"atempo={atempo:.4f}")
        delay_ms = int(TTS_LEAD_IN * 1000)
        chain += [f"adelay={delay_ms}|{delay_ms}", "apad"]
        filters.append("[0:a]" + ",".join(chain) + "[voice]")
    else:
        args += ["-f", "lavfi", "-i",
                 f"anullsrc=r={config.SAMPLE_RATE}:cl=stereo"]
        filters.append("[0:a]anull[voice]")
    if sfx_wav and _valid_file(sfx_wav, 512):
        args += ["-i", str(sfx_wav)]
        filters.append("[1:a]volume=0.42,adelay=120|120[sfx]")
        filters.append(
            f"[voice][sfx]amix=inputs=2:duration=first:normalize=0,"
            f"atrim=0:{duration:.3f}[aout]"
        )
    else:
        filters.append(f"[voice]atrim=0:{duration:.3f}[aout]")
    cmd = args + [
        "-filter_complex", ";".join(filters),
        "-map", "[aout]", "-t", f"{duration:.3f}",
        "-ar", str(config.SAMPLE_RATE), "-ac", "2",
        "-c:a", "pcm_s16le", str(out),
    ]
    utils.ffmpeg(cmd, cwd=workdir)
    utils.write_json(meta_path, {"hash": digest, "duration": duration,
                                 "atempo": atempo})
    return {"id": sid, "path": str(out), "duration": duration,
            "atempo": atempo, "reused": False, "has_voice": has_voice}


# --------------------------------------------------------------------------
# Stage 4/6: 拼接
# --------------------------------------------------------------------------
def concat_video(shots: list, workdir: Path, fps: int,
                 total_duration: float) -> Path:
    lines = [f"file 'clips/{_shot_id(shot)}.mp4'" for shot in shots]
    utils.write_text(workdir / "concat_video.txt", "\n".join(lines) + "\n")
    out = workdir / "video_raw.mp4"
    utils.ffmpeg([
        "-f", "concat", "-safe", "0", "-i", "concat_video.txt",
        "-c", "copy", "-an", str(out),
    ], cwd=workdir)
    dur = utils.probe_duration(out)
    if abs(dur - total_duration) > 0.6:
        LOG.warning("concat 时长偏差 %.2fs（期望 %.2fs），改用重编码拼接",
                    dur - total_duration, total_duration)
        utils.ffmpeg([
            "-f", "concat", "-safe", "0", "-i", "concat_video.txt",
            "-c:v", "libx264", "-preset", config.X264_PRESET,
            "-crf", config.X264_CRF, "-pix_fmt", "yuv420p",
            "-r", str(fps), "-an", str(out),
        ], cwd=workdir)
        dur = utils.probe_duration(out)
    if abs(dur - total_duration) > 0.6:
        raise RuntimeError(f"视频拼接时长异常: {dur:.2f}s != {total_duration:.2f}s")
    return out


def concat_audio(shots: list, workdir: Path, total_duration: float) -> Path:
    lines = [f"file 'audio/{_shot_id(shot)}.wav'" for shot in shots]
    utils.write_text(workdir / "concat_audio.txt", "\n".join(lines) + "\n")
    raw = workdir / "audio" / "_narration_raw.wav"
    out = workdir / "audio" / "narration.wav"
    utils.ffmpeg([
        "-f", "concat", "-safe", "0", "-i", "concat_audio.txt",
        "-c", "copy", str(raw),
    ], cwd=workdir)
    # 统一精确时长（不足补静音，超出裁掉）
    utils.ffmpeg([
        "-i", str(raw), "-af", "apad", "-t", f"{total_duration:.3f}",
        "-ar", str(config.SAMPLE_RATE), "-ac", "2", "-c:a", "pcm_s16le", str(out),
    ], cwd=workdir)
    return out


# --------------------------------------------------------------------------
# Stage 7: BGM / SFX
# --------------------------------------------------------------------------
def _chord(f1: str, f2: str, f3: str, f4: str) -> str:
    return (
        f"0.028*sin(2*PI*{f1}*t)+0.020*sin(2*PI*{f2}*t)"
        f"+0.016*sin(2*PI*{f3}*t)+0.010*sin(2*PI*{f4}*t)"
    )


def _bgm_expr() -> str:
    c = _chord("130.81", "261.63", "329.63", "392.00")   # C
    am = _chord("110.00", "220.00", "261.63", "329.63")  # Am
    f = _chord("87.31", "174.61", "220.00", "261.63")    # F
    g = _chord("98.00", "196.00", "246.94", "293.66")    # G
    cycle = "mod(t,32)"
    inner = (f"if(lt({cycle},8),{c},if(lt({cycle},16),{am},"
             f"if(lt({cycle},24),{f},{g})))")
    return f"(0.82+0.18*sin(2*PI*0.0625*t))*({inner})"


def ensure_bgm(workdir: Path) -> Path | None:
    out = workdir / "bgm" / "soft_pad.wav"
    if _valid_file(out, 5000):
        return out
    expr = _bgm_expr()
    utils.ensure_dir(out.parent)
    try:
        utils.ffmpeg([
            "-f", "lavfi",
            "-i", f"aevalsrc=exprs='{expr}|{expr}':s=48000:d={BGM_DURATION:.1f}",
            "-af", "lowpass=f=1800,highpass=f=50",
            "-c:a", "pcm_s16le", str(out),
        ])
        return out
    except Exception as exc:  # noqa: BLE001
        LOG.warning("BGM 生成失败（将不加背景音乐）: %s", exc)
        return None


def ensure_sfx(workdir: Path) -> dict:
    d = utils.ensure_dir(workdir / "sfx")
    whoosh = d / "whoosh.wav"
    ding = d / "ding.wav"
    if not _valid_file(whoosh, 1000):
        try:
            utils.ffmpeg([
                "-f", "lavfi",
                "-i", "anoisesrc=d=0.7:c=pink:r=48000:a=0.4",
                "-af", ("highpass=f=350,lowpass=f=6000,"
                        "afade=t=in:st=0:d=0.12,afade=t=out:st=0.22:d=0.48,"
                        "volume=0.7"),
                "-ac", "2", "-ar", "48000", "-c:a", "pcm_s16le", str(whoosh),
            ])
        except Exception as exc:  # noqa: BLE001
            LOG.warning("whoosh 生成失败: %s", exc)
    if not _valid_file(ding, 1000):
        try:
            utils.ffmpeg([
                "-f", "lavfi",
                "-i", "sine=frequency=1046.5:duration=0.8:r=48000",
                "-af", "afade=t=out:st=0.03:d=0.77,volume=0.55",
                "-ac", "2", "-ar", "48000", "-c:a", "pcm_s16le", str(ding),
            ])
        except Exception as exc:  # noqa: BLE001
            LOG.warning("ding 生成失败: %s", exc)
    return {
        "whoosh": whoosh if _valid_file(whoosh, 1000) else None,
        "ding": ding if _valid_file(ding, 1000) else None,
    }


# --------------------------------------------------------------------------
# Stage 8: 最终编码
# --------------------------------------------------------------------------
def _run_ffmpeg_progress(args: list, cwd: Path, prog, pct0: float, pct1: float,
                         duration: float, label: str) -> None:
    cmd = [config.FFMPEG_BIN, "-hide_banner", "-nostdin", "-loglevel", "error",
           "-y", "-progress", "pipe:1", "-nostats", *args]
    LOG.debug("$ %s", subprocess.list2cmdline([str(c) for c in cmd]))
    proc = subprocess.Popen(
        [str(c) for c in cmd], cwd=str(cwd),
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, encoding="utf-8", errors="replace",
    )
    last = pct0
    try:
        assert proc.stdout is not None
        for line in proc.stdout:
            line = line.strip()
            if line.startswith("out_time_us="):
                try:
                    sec = float(line.split("=", 1)[1]) / 1e6
                except ValueError:
                    continue
                frac = min(1.0, max(0.0, sec / max(0.1, duration)))
                pct = pct0 + (pct1 - pct0) * frac
                if pct >= last + 1.0:
                    prog(pct, label)
                    last = pct
            elif line == "progress=end":
                break
    finally:
        proc.wait()
    err = proc.stderr.read() if proc.stderr else ""
    if proc.returncode != 0:
        raise RuntimeError(f"最终编码失败 (exit {proc.returncode})\n{err[-2000:]}")
    prog(pct1, label)


def final_encode(workdir: Path, out: Path, total_duration: float, fps: int,
                 width: int, height: int, bgm: Path | None, has_ass: bool,
                 prog, pct0: float, pct1: float) -> None:
    inputs = ["-i", "video_raw.mp4", "-i", "audio/narration.wav"]
    fc: list[str] = []
    if has_ass:
        fc.append("[0:v]ass=filename=final.ass:fontsdir=fonts[v]")
    else:
        fc.append("[0:v]null[v]")
    if bgm is not None:
        inputs += ["-stream_loop", "-1", "-i", str(bgm)]
        fc.append("[1:a]volume=1.0[nar]")
        fc.append(
            f"[2:a]volume={config.BGM_VOLUME},atrim=0:{total_duration:.3f},"
            f"afade=t=in:st=0:d=2,"
            f"afade=t=out:st={max(0.0, total_duration - 4.0):.3f}:d=4[bg]"
        )
        fc.append(
            "[nar][bg]amix=inputs=2:duration=first:normalize=0,"
            f"loudnorm=I={config.LUFS_TARGET}:TP=-1.5:LRA=11,"
            f"aresample={config.SAMPLE_RATE}[a]"
        )
    else:
        fc.append(
            "[1:a]volume=1.0,"
            f"loudnorm=I={config.LUFS_TARGET}:TP=-1.5:LRA=11,"
            f"aresample={config.SAMPLE_RATE}[a]"
        )
    args = inputs + [
        "-filter_complex", ";".join(fc),
        "-map", "[v]", "-map", "[a]",
        "-c:v", "libx264", "-preset", config.X264_PRESET,
        "-crf", config.X264_CRF, "-pix_fmt", "yuv420p",
        "-r", str(fps), "-s", f"{width}x{height}",
        "-c:a", "aac", "-b:a", config.AUDIO_BITRATE,
        "-ar", str(config.SAMPLE_RATE), "-ac", "2",
        "-movflags", "+faststart", "-shortest",
        "-t", f"{total_duration:.3f}",
        str(out),
    ]
    _run_ffmpeg_progress(args, workdir, prog, pct0, pct1, total_duration,
                         "编码成片")


# --------------------------------------------------------------------------
# 主流程
# --------------------------------------------------------------------------
def run_render(args) -> int:
    t_start = time.time()
    prog = utils.Progress()
    episode_path = Path(args.episode)
    data = utils.read_json(episode_path)
    if not isinstance(data, dict):
        utils.emit_json_line({"ok": False, "error": f"无法读取 episode: {episode_path}"})
        return 1
    episode = data.get("episode") or {}
    assets = data.get("assets") or []
    shots = list(data.get("shots") or [])
    if args.limit:
        shots = shots[: int(args.limit)]
    if not shots:
        utils.emit_json_line({"ok": False, "error": "episode 没有 shots"})
        return 1

    workdir = Path(args.workdir).resolve()
    utils.ensure_dir(workdir)
    out = Path(args.out).resolve()
    utils.ensure_dir(out.parent)

    resolution = args.resolution or episode.get("resolution") or config.RESOLUTION
    width, height = utils.parse_resolution(resolution)
    fps = int(args.fps or episode.get("fps") or config.FPS)
    total_duration = round(sum(_shot_duration(s) for s in shots), 3)
    force = bool(getattr(args, "force", False))

    prog(1, f"初始化：{len(shots)} 镜头 / {width}x{height}@{fps}fps / 目标 {total_duration:.1f}s")
    font_info = fonts.ensure_fonts(workdir)
    LOG.info("字体: body=%s display=%s",
             font_info["body"]["family"], font_info["display"]["family"])

    # ---- 角色工坊素材（outputs/characters/，可用 CHARACTERS_DIR 覆盖）----
    char_assets = characters.discover()
    char_map = characters.resolve_for_assets(assets, char_assets)
    char_fp = characters.fingerprint(char_map)
    LOG.info("角色素材: %d 个 | 映射到角色资产 %d 个%s",
             len(char_assets), len(char_map),
             "" if char_map else "（无素材，回退文字卡视觉）")
    h3_dir = None
    if getattr(args, "use_h3", False):
        h3_env = os.environ.get("H3_DIR")
        h3_dir = Path(h3_env) if h3_env \
            else (config.REPO_ROOT / "outputs" / "h3")

    # ---- 1) 镜头片段 ---------------------------------------------------
    prog(2, "生成/复用镜头片段")
    clip_results: dict[str, dict] = {}
    with ThreadPoolExecutor(max_workers=CLIP_WORKERS) as pool:
        futures = {
            pool.submit(ensure_clip, shot, assets, episode, workdir,
                        width, height, fps, force,
                        char_map=char_map, char_fp=char_fp,
                        h3_dir=h3_dir): _shot_id(shot)
            for shot in shots
        }
        done = 0
        for fut in as_completed(futures):
            sid = futures[fut]
            clip_results[sid] = fut.result()
            done += 1
            prog(2 + 38.0 * done / len(shots),
                 f"镜头片段 {done}/{len(shots)}")
    reused = sum(1 for r in clip_results.values() if r.get("reused"))
    from_shots = sum(1 for r in clip_results.values()
                     if str(r.get("source") or "").startswith("shots"))
    from_h3 = sum(1 for r in clip_results.values() if r.get("source") == "h3")
    char_shots = sum(1 for r in clip_results.values() if r.get("character"))
    LOG.info("镜头片段: %d 个 | 平台片段复用 %d | H3 真镜头 %d | 缓存复用 %d | 新生成 %d",
             len(clip_results), from_shots, from_h3, reused - from_shots - from_h3,
             len(clip_results) - reused)
    LOG.info("角色画面镜头: %d / %d（角色素材 %d 个）",
             char_shots, len(clip_results), len(char_assets))

    # ---- 2) TTS --------------------------------------------------------
    voices = _voice_map(assets)
    tts_results = prepare_tts(shots, assets, workdir, prog, 41, 58, force=force)
    tts_fail = sum(1 for r in tts_results.values() if not r.get("ok"))
    LOG.info("TTS: %d 条（失败 %d）", len(tts_results), tts_fail)

    # ---- 3) 镜头音轨 ---------------------------------------------------
    prog(59, "构建镜头音轨")
    sfx = ensure_sfx(workdir)
    audio_results: dict[str, dict] = {}
    with ThreadPoolExecutor(max_workers=AUDIO_WORKERS) as pool:
        futures = {}
        for shot in shots:
            sid = _shot_id(shot)
            info = shot_tts_info(shot, tts_results, voices)
            sfx_path = None
            sfx_name = str(shot.get("sfx") or "").lower()
            if sfx_name in ("whoosh", "ding") and sfx.get(sfx_name):
                sfx_path = sfx[sfx_name]
            futures[pool.submit(build_shot_audio, shot, info,
                                _shot_duration(shot), workdir, sfx_path,
                                force)] = sid
        done = 0
        for fut in as_completed(futures):
            sid = futures[fut]
            audio_results[sid] = fut.result()
            done += 1
            prog(59 + 8.0 * done / len(shots), f"镜头音轨 {done}/{len(shots)}")
    audio_reused = sum(1 for r in audio_results.values() if r.get("reused"))
    LOG.info("镜头音轨: %d 个（复用 %d）", len(audio_results), audio_reused)

    # ---- 4) 拼接视频 ---------------------------------------------------
    prog(68, "拼接视频片段")
    video_raw = concat_video(shots, workdir, fps, total_duration)
    LOG.info("video_raw: %s (%.2fs)", video_raw, utils.probe_duration(video_raw))

    # ---- 5) ASS --------------------------------------------------------
    prog(71, "生成字幕/花字轨")
    timings = []
    cursor = 0.0
    for shot in shots:
        dur = _shot_duration(shot)
        timings.append({"shot": shot, "start": cursor, "end": cursor + dur,
                        "duration": dur})
        cursor += dur
    segments = data.get("segments") or episode.get("segments") or []
    ass_episode = dict(episode)
    ass_episode["segments"] = segments
    char_shot_ids = {sid for sid, res in clip_results.items()
                     if res.get("character")}
    ass_info = ass_builder.build_ass(
        ass_episode, timings, assets, font_info,
        workdir / "final.ass", width=width, height=height,
        char_shot_ids=char_shot_ids,
    )

    # ---- 6) 拼接音轨 ---------------------------------------------------
    prog(74, "拼接旁白音轨")
    narration = concat_audio(shots, workdir, total_duration)
    LOG.info("narration: %.2fs", utils.probe_duration(narration))

    # ---- 7) BGM --------------------------------------------------------
    bgm_path = None
    if not getattr(args, "no_bgm", False):
        prog(76, "准备背景音乐")
        bgm_path = ensure_bgm(workdir)

    # ---- 8) 最终编码 ---------------------------------------------------
    prog(78, "编码成片（烧字幕 + 混音 + loudnorm）")
    final_encode(workdir, out, total_duration, fps, width, height,
                 bgm_path, has_ass=ass_info["events"] > 0,
                 prog=prog, pct0=78, pct1=99)

    # ---- 校验与摘要 ----------------------------------------------------
    info = utils.probe_video(out)
    if info["width"] != width or info["height"] != height:
        LOG.warning("成片分辨率异常: %sx%s (期望 %sx%s)",
                    info["width"], info["height"], width, height)
    size_bytes = out.stat().st_size if out.exists() else 0
    elapsed = round(time.time() - t_start, 1)
    summary = {
        "ok": True,
        "path": str(out),
        "duration": info["duration"] or utils.probe_duration(out),
        "shots": len(shots),
        "size_bytes": size_bytes,
        "resolution": f"{info['width']}x{info['height']}",
        "fps": info["fps"] or fps,
        "elapsed_sec": elapsed,
        "tts_count": len(tts_results),
        "tts_failures": tts_fail,
        "clips_reused": reused,
        "clips_from_shots": from_shots,
        "clips_from_h3": from_h3,
        "characters": len(char_assets),
        "character_shots": char_shots,
        "audio_reused": audio_reused,
        "bgm": bgm_path is not None,
        "ass_events": ass_info["events"],
        "workdir": str(workdir),
    }
    utils.write_json(workdir / "manifest.json", summary)
    prog(100, f"完成：{utils.fmt_sec(elapsed)} / {size_bytes / 1048576:.1f}MB")
    utils.emit_json_line(summary)
    return 0


def main(args) -> int:
    try:
        return run_render(args)
    except Exception as exc:  # noqa: BLE001
        LOG.error("render 失败: %s", exc)
        LOG.debug("%s", __import__("traceback").format_exc())
        utils.emit_json_line({"ok": False, "error": str(exc)})
        return 1
