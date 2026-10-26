"""V5.4 剪辑执行层：**每个剪辑点独立转场** + **J/L cut 音频重叠** + 甩镜过渡。

参考：libopenshot/OpenShot 的 clip + transition 数据模型（每个剪辑点独立 transition）
与 ffmpeg `xfade` 滤镜。供 `render_v5.assemble` 集成调用；不依赖 GPU / 不依赖模型。

公共 API
--------
- ``xfade_chain(shots, clip_paths, workdir, fps, width, height, total, boundaries, *, prog=None)``
  混合 `cut` 与 `xfade` 的链式拼接，返回 ``workdir/video_edit.mp4``（无音轨）。
  总时长公式：``sum(shot.duration) - sum(non_cut_transition_duration)``（±1 帧）。
- ``mix_audio_edit(shots, workdir, total, cuts, *, out_name="final_narration.wav")``
  按剪辑时间线摆放每镜音频；``j_cut`` 让本镜音频提前（压到上一镜尾部），
  ``l_cut`` 让本镜音频延后（压到下一镜开头）。48kHz stereo PCM。
- ``whip_pan_transition(clip_a, clip_b, out_path, ...)``
  由两镜首尾帧生成一段甩镜过渡（hblur 近似），供编辑档案里 `whip_*` 镜头单独插入。

设计约定
--------
- ``shots``：``[{"id": str, "duration": float}, ...]``（可带 ``start``/``audio`` 等字段，原样透传）。
- ``clip_paths``：``{shot_id: path}``（也接受与 ``shots`` 同序的路径列表）。
- ``boundaries``：长度 = 镜头数-1；每项 ``{"from","to","mode","duration"}`` 或直接 mode 字符串；
  ``mode=cut``（含 none/hard）直接 concat 不减时长，其余走 ffmpeg xfade；未知 mode 回退 ``fade``。
- 时间基准：24/25fps 均可；输出统一 ``fps``、``width x height``、yuv420p、H.264。
"""
from __future__ import annotations

import functools
import logging
import math
import re
import subprocess
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence

from . import config, utils

LOG = logging.getLogger("pipeline")

# 产物文件名（render_v5.assemble 读取约定）
VIDEO_EDITED = "video_edit.mp4"
AUDIO_EDITED = "final_narration.wav"

DEFAULT_MODE = "fade"
DEFAULT_DURATION = 0.30
MAX_DURATION = 0.80
MIN_DURATION = 0.04

# ffmpeg xfade 支持的转场名（ffmpeg 5.0+ / 6.x）
XFADE_MODES: dict[str, str] = {
    # 基础
    "fade": "fade",
    "dissolve": "dissolve",
    "fadeblack": "fadeblack",
    "fadewhite": "fadewhite",
    # 划像
    "wipeleft": "wipeleft",
    "wiperight": "wiperight",
    "wipeup": "wipeup",
    "wipedown": "wipedown",
    "wipetl": "wipetl",
    "wipetr": "wipetr",
    "wipebl": "wipebl",
    "wipebr": "wipebr",
    # 滑动
    "slideleft": "slideleft",
    "slideright": "slideright",
    "slideup": "slideup",
    "slidedown": "slidedown",
    "smoothleft": "smoothleft",
    "smoothright": "smoothright",
    "smoothup": "smoothup",
    "smoothdown": "smoothdown",
    # 形状 / 特效
    "circle": "circleopen",
    "circleopen": "circleopen",
    "circleclose": "circleclose",
    "radial": "radial",
    "pixelize": "pixelize",
    "pixelate": "pixelize",
    "hblur": "hblur",
    "vblur": "vblur",
    "diagtl": "diagtl",
    "diagbr": "diagbr",
    "zoomin": "zoomin",
    # 甩镜（ffmpeg 无原生 whip，用 hblur 近似；配 motion-dev 的 whip_pan 镜头更佳）
    "whip": "hblur",
    "whipleft": "hblur",
    "whipright": "hblur",
    "whip_left": "hblur",
    "whip_right": "hblur",
    # 切
    "cut": "",
    "none": "",
}

_CUT_MODES = {"", "cut", "none", "hard", "hard_cut"}


def supported_modes() -> list[str]:
    """返回可用的转场模式名（含 cut），供编辑档案/前端展示。"""
    names = sorted({m for m in XFADE_MODES if m not in _CUT_MODES})
    return ["cut", *names]


@functools.lru_cache(maxsize=1)
def _xfade_supported_names() -> frozenset[str] | None:
    """探测当前 ffmpeg 的 xfade 支持哪些 transition（不同构建能力不同）。"""
    try:
        proc = subprocess.run(
            [config.FFMPEG_BIN, "-hide_banner", "-h", "filter=xfade"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=20,
        )
    except Exception:  # noqa: BLE001
        return None
    out = (proc.stdout or "") + (proc.stderr or "")
    names = set(re.findall(r"^\s+([a-z0-9_]+)\s+-?\d+\s+\.\.FV", out, re.MULTILINE))
    names.discard("transition")
    return frozenset(names) if names else None


# 某些构建缺失的转场 → 用邻近可用模式替代
_UNSUPPORTED_ALIASES = {"vblur": "hblur"}


def _resolve_mode(mode: Any) -> str | None:
    """mode → ffmpeg transition 名；cut 返回 None；未知/不支持回退。"""
    raw = str(mode or "cut").strip().lower().replace("-", "_").replace(" ", "_")
    if raw in _CUT_MODES:
        return None
    style = XFADE_MODES.get(raw)
    if style is None:
        LOG.warning("未知转场 mode=%r，回退 %s", mode, DEFAULT_MODE)
        return XFADE_MODES[DEFAULT_MODE]
    if style == "":
        return None
    supported = _xfade_supported_names()
    if supported is not None and style not in supported:
        alias = _UNSUPPORTED_ALIASES.get(style)
        if alias and alias in supported:
            LOG.warning("转场 mode=%r（%s）当前 ffmpeg 不支持，改用 %s",
                        mode, style, alias)
            return alias
        LOG.warning("转场 mode=%r（%s）当前 ffmpeg 不支持，回退 %s",
                    mode, style, DEFAULT_MODE)
        return XFADE_MODES[DEFAULT_MODE]
    return style


def _shot_id(shot: Mapping[str, Any]) -> str:
    return str(shot.get("id") or shot.get("shot") or "shot")


def _shot_duration(shot: Mapping[str, Any]) -> float:
    try:
        d = float(shot.get("duration") or 0.0)
    except (TypeError, ValueError):
        d = 0.0
    return d if d > 0.05 else 5.0


def _clip_for(shot: Mapping[str, Any], clip_paths: Any, index: int,
              workdir: Path) -> Path:
    sid = _shot_id(shot)
    if isinstance(clip_paths, Mapping):
        src = clip_paths.get(sid) or clip_paths.get(index)
    elif isinstance(clip_paths, Sequence) and not isinstance(clip_paths, (str, bytes)):
        src = clip_paths[index] if index < len(clip_paths) else None
    else:
        src = clip_paths if index == 0 else None
    if src is None:
        src = workdir / "clips" / f"{sid}.mp4"
    src = Path(src).resolve()
    if not src.is_file():
        raise FileNotFoundError(f"剪辑片段不存在: {src}")
    return src


def _normalize_boundaries(shots: Sequence[Mapping[str, Any]],
                          boundaries: Any) -> list[dict[str, Any]]:
    """把各种写法归一为长度 n-1 的边界 list（缺省 cut）。"""
    n = max(0, len(shots) - 1)
    items: list[Any] = []
    if boundaries is None:
        items = []
    elif isinstance(boundaries, Mapping):
        if "mode" in boundaries:  # 单边界
            items = [boundaries]
        else:  # {from_id: mode|dict}
            by_from = {_shot_id(s): False for s in shots[:-1]}
            mapped: list[Any] = []
            for i, shot in enumerate(shots[:-1]):
                sid = _shot_id(shot)
                entry = boundaries.get(sid)
                if entry is None:  # 按位置 fallback
                    entry = boundaries.get(i)
                mapped.append(entry if entry is not None else {"mode": "cut"})
                by_from[sid] = entry is not None
            items = mapped
    elif isinstance(boundaries, (str, bytes)):
        items = [boundaries]
    else:
        items = list(boundaries)
    if len(items) < n:
        LOG.warning("剪辑点边界不足（%d/%d），缺失部分按 cut 处理", len(items), n)
        items = [*items, *[{"mode": "cut"} for _ in range(n - len(items))]]
    elif len(items) > n:
        LOG.warning("剪辑点边界过多（%d/%d），多余部分忽略", len(items), n)
        items = items[:n]

    out: list[dict[str, Any]] = []
    for i in range(n):
        item = items[i]
        if isinstance(item, str):
            item = {"mode": item}
        elif not isinstance(item, Mapping):
            item = {"mode": "cut"}
        mode = item.get("mode") or item.get("transition") or "cut"
        style = _resolve_mode(mode)
        d_prev, d_next = _shot_duration(shots[i]), _shot_duration(shots[i + 1])
        try:
            dur = float(item.get("duration") or item.get("transition_duration")
                        or DEFAULT_DURATION)
        except (TypeError, ValueError):
            dur = DEFAULT_DURATION
        if style is not None:
            dur = max(MIN_DURATION, min(dur, MAX_DURATION,
                                         d_prev * 0.5, d_next * 0.5))
        else:
            dur = 0.0
        out.append({
            "from": _shot_id(shots[i]),
            "to": _shot_id(shots[i + 1]),
            "mode": str(mode),
            "style": style,
            "duration": dur,
            "cut": style is None,
        })
    return out


def expected_duration(shots: Sequence[Mapping[str, Any]],
                      boundaries: Any) -> float:
    """总时长公式：sum(shot.duration) - sum(non_cut transition duration)（秒）。"""
    total = sum(_shot_duration(s) for s in shots)
    for b in _normalize_boundaries(shots, boundaries):
        if not b["cut"]:
            total -= float(b["duration"])
    return round(total, 4)


def _normalize_filter(duration: float, fps: int,
                      width: int, height: int) -> str:
    """统一规格的 filter 表达式（不含输入/输出标签）：fps/尺寸/像素格式/timebase 一致。"""
    return (
        f"fps={fps},"
        f"scale={width}:{height}:force_original_aspect_ratio=increase,"
        f"crop={width}:{height},setsar=1,format=yuv420p,"
        f"settb=AVTB,setpts=PTS-STARTPTS,"
        f"tpad=stop_mode=clone:stop_duration={duration + 0.5:.3f},"
        f"trim=0:{duration:.3f},setpts=PTS-STARTPTS"
    )


def _filter_normalize(idx: int, duration: float, fps: int,
                      width: int, height: int) -> str:
    """每段视频统一规格并 trim 到精确镜头时长（不足用末帧补齐）。"""
    return f"[{idx}:v]{_normalize_filter(duration, fps, width, height)}[v{idx}]"


def _post_norm(label_in: str, label_out: str, fps: int) -> str:
    """concat/xfade 输出后**再次**统一 timebase/fps/像素格式。

    关键修复（V5.4）：concat 输出 timebase 会变成 1/12800，而 xfade 的第二个输入
    是 1/1000000（AVTB），直接链式会报 `First input link main timebase do not match`。
    每个中间结果都重新 `fps=.../settb=AVTB/setpts=PTS-STARTPTS` 后再进下一个滤镜。
    """
    return (f"[{label_in}]fps={fps},settb=AVTB,setpts=PTS-STARTPTS,"
            f"format=yuv420p[{label_out}]")


def _write_video(workdir: Path, total: float, duration: float,
                 boundaries: list[dict[str, Any]]) -> None:
    utils.write_json(workdir / "edit_meta.json", {
        "total_expected": round(total, 4),
        "duration": round(duration, 4),
        "out": str(workdir / VIDEO_EDITED),
        "boundaries": [
            {"from": b["from"], "to": b["to"], "mode": b["mode"],
             "style": b["style"], "duration": round(float(b["duration"]), 4)}
            for b in boundaries
        ],
    })


def xfade_chain(shots: Sequence[Mapping[str, Any]], clip_paths: Any,
                workdir: Path | str, fps: int, width: int, height: int,
                total: float, boundaries: Any, *,
                prog: Callable[[float, str], None] | None = None) -> Path:
    """按**每个剪辑点独立转场**拼接镜头。

    - ``mode=cut``：直接 concat（不缩短总时长）；
    - 其它：ffmpeg ``xfade``（链式 filter graph，offset 由累计时长算出）；
    - 总时长 = sum(shots) - sum(非 cut 转场时长)，允许 ±1 帧。
    """
    workdir = Path(workdir)
    utils.ensure_dir(workdir)
    shots = list(shots or [])
    if not shots:
        raise ValueError("xfade_chain: shots 不能为空")
    if fps <= 0 or width <= 0 or height <= 0:
        raise ValueError("xfade_chain: fps/width/height 必须为正")
    out = workdir / VIDEO_EDITED
    durations = [_shot_duration(s) for s in shots]
    bs = _normalize_boundaries(shots, boundaries)
    expect = round(sum(durations) - sum(b["duration"] for b in bs if not b["cut"]), 4)
    tolerance = max(0.10, 2.0 / float(fps))
    if total is not None and abs(float(total) - expect) > tolerance:
        LOG.warning("xfade_chain: 传入 total=%.3fs 与期望 %.3fs 不一致，按期望时长裁剪",
                    float(total), expect)
    if len(shots) == 1:
        src = _clip_for(shots[0], clip_paths, 0, workdir)
        utils.ffmpeg([
            "-i", str(src),
            "-vf", (f"fps={fps},scale={width}:{height}:force_original_aspect_ratio=increase,"
                    f"crop={width}:{height},setsar=1,format=yuv420p,"
                    f"trim=0:{expect:.3f},setpts=PTS-STARTPTS"),
            "-an", "-c:v", "libx264", "-preset", config.X264_PRESET,
            "-crf", config.X264_CRF, "-pix_fmt", "yuv420p",
            "-r", str(fps), "-t", f"{expect:.3f}", str(out),
        ], cwd=workdir)
        _write_video(workdir, expect, utils.probe_duration(out), bs)
        return out

    inputs: list[str] = []
    fc: list[str] = []
    for i, shot in enumerate(shots):
        src = _clip_for(shot, clip_paths, i, workdir)
        inputs += ["-i", str(src)]
        fc.append(_filter_normalize(i, durations[i], int(fps),
                                    int(width), int(height)))
        if prog is not None:
            prog(60 + 4.0 * (i + 1) / len(shots), f"剪辑准备 {i + 1}/{len(shots)}")

    acc = "v0"
    acc_dur = durations[0]
    for i in range(1, len(shots)):
        b = bs[i - 1]
        raw = f"r{i}"
        label = f"e{i}"
        if b["cut"]:
            fc.append(f"[{acc}][v{i}]concat=n=2:v=1:a=0[{raw}]")
            acc_dur += durations[i]
        else:
            td = float(b["duration"])
            offset = acc_dur - td
            if offset <= 0.001:
                raise ValueError(f"转场时长 {td:.2f}s 超过前段时长 {acc_dur:.2f}s")
            fc.append(
                f"[{acc}][v{i}]xfade=transition={b['style']}:"
                f"duration={td:.3f}:offset={offset:.3f}[{raw}]")
            acc_dur += durations[i] - td
        # concat/xfade 输出统一 timebase 后再进入下一次链式运算
        fc.append(_post_norm(raw, label, int(fps)))
        acc = label
        if prog is not None:
            prog(64 + 4.0 * i / max(1, len(shots) - 1),
                 f"转场 {i}/{len(shots) - 1}（{b['mode']}）")

    fc.append(f"[{acc}]trim=0:{expect:.3f},setpts=PTS-STARTPTS,"
              f"fps={fps},settb=AVTB,format=yuv420p,"
              f"setpts=PTS-STARTPTS[vout]")
    args = inputs + [
        "-filter_complex", ";".join(fc),
        "-map", "[vout]", "-an",
        "-c:v", "libx264", "-preset", config.X264_PRESET,
        "-crf", config.X264_CRF, "-pix_fmt", "yuv420p",
        "-r", str(fps), "-t", f"{expect:.3f}", str(out),
    ]
    try:
        utils.ffmpeg(args, cwd=workdir)
    except RuntimeError as exc:
        # concat/xfade 混链偶尔因时间基不一致失败：退化为“逐边界两两渲染”的慢速兜底
        LOG.warning("单次 filter graph 失败（%s），改用分段两两渲染兜底", exc)
        return _xfade_chain_fallback(shots, clip_paths, workdir, int(fps),
                                     int(width), int(height), expect, bs, prog=prog)
    real = utils.probe_duration(out)
    if abs(real - expect) > tolerance:
        raise RuntimeError(
            f"剪辑拼接时长异常: {real:.2f}s != 期望 {expect:.2f}s")
    _write_video(workdir, expect, real, bs)
    LOG.info("剪辑拼接完成: %.2fs（%d 镜头，%d 个转场，%d 个硬切）", real,
             len(shots), sum(0 if b["cut"] else 1 for b in bs),
             sum(1 for b in bs if b["cut"]))
    if prog is not None:
        prog(70, "剪辑拼接完成")
    return out


def _xfade_chain_fallback(shots, clip_paths, workdir: Path, fps: int,
                          width: int, height: int, expect: float,
                          boundaries: list[dict[str, Any]],
                          prog=None) -> Path:
    """兜底：逐边界渲染到中间文件（每步只处理两个输入），最后整体 trim。"""
    tmp = utils.ensure_dir(workdir / "edit_tmp")
    durations = [_shot_duration(s) for s in shots]
    cur = tmp / "acc_0.mp4"
    src0 = _clip_for(shots[0], clip_paths, 0, workdir)
    utils.ffmpeg([
        "-i", str(src0), "-vf", _normalize_filter(durations[0], fps, width, height),
        "-an", "-c:v", "libx264", "-preset", config.X264_PRESET,
        "-crf", config.X264_CRF, "-pix_fmt", "yuv420p", "-r", str(fps), str(cur),
    ], cwd=workdir)
    acc_dur = durations[0]
    for i in range(1, len(shots)):
        b = boundaries[i - 1]
        nxt = tmp / f"acc_{i}.mp4"
        src = _clip_for(shots[i], clip_paths, i, workdir)
        td = float(b["duration"]) if not b["cut"] else 0.0
        target = acc_dur + durations[i] - td
        norm_acc = _normalize_filter(acc_dur, fps, width, height)
        norm_next = _normalize_filter(durations[i], fps, width, height)
        if b["cut"]:
            fc = (f"[0:v]{norm_acc}[a];[1:v]{norm_next}[b];"
                  f"[a][b]concat=n=2:v=1:a=0[r];"
                  f"{_post_norm('r', 'vn', fps)};"
                  f"[vn]trim=0:{target:.3f},setpts=PTS-STARTPTS[vout]")
        else:
            fc = (f"[0:v]{norm_acc}[a];[1:v]{norm_next}[b];"
                  f"[a][b]xfade=transition={b['style']}:duration={td:.3f}:"
                  f"offset={max(0.001, acc_dur - td):.3f}[r];"
                  f"{_post_norm('r', 'vn', fps)};"
                  f"[vn]trim=0:{target:.3f},setpts=PTS-STARTPTS[vout]")
        utils.ffmpeg(["-i", str(cur), "-i", str(src),
                      "-filter_complex", fc, "-map", "[vout]", "-an",
                      "-c:v", "libx264", "-preset", config.X264_PRESET,
                      "-crf", config.X264_CRF, "-pix_fmt", "yuv420p",
                      "-r", str(fps), str(nxt)], cwd=workdir)
        acc_dur = target
        cur = nxt
        if prog is not None:
            prog(64 + 4.0 * i / max(1, len(shots) - 1), f"转场兜底 {i}")
    out = workdir / VIDEO_EDITED
    utils.ffmpeg([
        "-i", str(cur),
        "-vf", f"fps={fps},settb=AVTB,setpts=PTS-STARTPTS,format=yuv420p",
        "-an", "-c:v", "libx264", "-preset", config.X264_PRESET,
        "-crf", config.X264_CRF, "-pix_fmt", "yuv420p", "-r", str(fps),
        "-t", f"{expect:.3f}", str(out),
    ], cwd=workdir)
    real = utils.probe_duration(out)
    _write_video(workdir, expect, real, boundaries)
    return out


# ---------------------------------------------------------------------------
# 音频：J/L cut
# ---------------------------------------------------------------------------
def _normalize_cuts(shots: Sequence[Mapping[str, Any]], cuts: Any) -> dict[str, dict[str, float]]:
    out: dict[str, dict[str, float]] = {}
    if not cuts:
        return out
    items = list(cuts.values()) if isinstance(cuts, Mapping) else list(cuts)
    ids = [_shot_id(s) for s in shots]
    for item in items:
        if not isinstance(item, Mapping):
            continue
        sid = str(item.get("shot") or item.get("id") or "")
        if sid not in ids:
            LOG.warning("mix_audio_edit: cuts 里的 shot=%r 不在镜头列表，忽略", sid)
            continue
        try:
            j = max(0.0, float(item.get("j_cut") or 0.0))
            l = max(0.0, float(item.get("l_cut") or 0.0))
        except (TypeError, ValueError):
            continue
        out[sid] = {"j_cut": j, "l_cut": l}
    return out


def _shot_start(shot: Mapping[str, Any], index: int,
                cumulative: float) -> float:
    for key in ("start", "edit_start", "timeline_start"):
        if shot.get(key) is not None:
            try:
                return max(0.0, float(shot[key]))
            except (TypeError, ValueError):
                break
    return cumulative


def _resolve_audio(shot: Mapping[str, Any], workdir: Path) -> Path | None:
    sid = _shot_id(shot)
    for key in ("audio", "audio_path", "wav"):
        value = shot.get(key)
        if value:
            p = Path(value)
            p = p if p.is_absolute() else workdir / p
            if p.is_file():
                return p
    candidates = [
        workdir / "audio" / f"{sid}.wav",
        workdir / "audio" / f"shot_{sid}.wav",
        workdir / f"{sid}.wav",
    ]
    for p in candidates:
        if p.is_file():
            return p
    return None


def mix_audio_edit(shots: Sequence[Mapping[str, Any]], workdir: Path | str,
                   total: float, cuts: Any, *,
                   out_name: str = AUDIO_EDITED) -> Path:
    """按剪辑时间线混音：J cut（本镜音频提前）/ L cut（本镜音频延后）。

    - ``shots`` 的 ``start`` 若提供则作为剪辑后时间线起点；否则用累计镜头时长；
      有非 cut 转场时请由调用方（editorial/render_v5）传入准确的 ``start``。
    - ``cuts``：``[{"shot": id, "j_cut": 0.25, "l_cut": 0.2}]``；
      本镜音频占 [start-j_cut, start+duration+l_cut]，与相邻镜自然交叠。
    - 输出 48kHz stereo PCM PCM 16bit，时长严格 = ``total``。
    """
    workdir = Path(workdir)
    utils.ensure_dir(workdir)
    shots = list(shots or [])
    if not shots:
        raise ValueError("mix_audio_edit: shots 不能为空")
    total = max(0.05, float(total))
    cut_map = _normalize_cuts(shots, cuts)
    duration_map = {_shot_id(s): _shot_duration(s) for s in shots}

    args: list[str] = []
    filters: list[str] = []
    cumulative = 0.0
    for i, shot in enumerate(shots):
        sid = _shot_id(shot)
        d = duration_map[sid]
        start = _shot_start(shot, i, cumulative)
        cumulative += d
        c = cut_map.get(sid, {"j_cut": 0.0, "l_cut": 0.0})
        j, l = float(c["j_cut"]), float(c["l_cut"])
        place = max(0.0, start - j)
        delay_ms = int(round(place * 1000))
        span = max(0.05, d + j + l)  # 音频覆盖时长
        src = _resolve_audio(shot, workdir)
        if src is None:
            args += ["-f", "lavfi", "-i",
                     f"anullsrc=r={config.SAMPLE_RATE}:cl=stereo"]
            filters.append(f"[{i}:a]anull,atrim=0:{span:.3f}[a{i}]")
        else:
            args += ["-i", str(src)]
            filters.append(
                f"[{i}:a]aresample={config.SAMPLE_RATE},"
                f"pan=stereo|c0=c0|c1=c0,"
                f"atrim=0:{d + j + l:.3f},"
                f"apad=whole_dur={span:.3f},"
                f"adelay={delay_ms}|{delay_ms}[a{i}]"
            )
    labels = "".join(f"[a{i}]" for i in range(len(shots)))
    if len(shots) == 1:
        filters.append(f"{labels}anull[aall]")
    else:
        filters.append(
            f"{labels}amix=inputs={len(shots)}:duration=longest:normalize=0[aall]")
    filters.append(
        f"[aall]alimiter=limit=0.95,"
        f"apad=whole_dur={total:.3f},atrim=0:{total:.3f},"
        f"aresample={config.SAMPLE_RATE},asetpts=PTS-STARTPTS[aout]")
    out = workdir / out_name
    utils.ffmpeg(args + [
        "-filter_complex", ";".join(filters),
        "-map", "[aout]", "-t", f"{total:.3f}",
        "-ar", str(config.SAMPLE_RATE), "-ac", "2",
        "-c:a", "pcm_s16le", str(out),
    ], cwd=workdir)
    real = utils.probe_duration(out)
    if abs(real - total) > 0.06:
        raise RuntimeError(f"混音时长异常: {real:.3f}s != {total:.3f}s")
    LOG.info("J/L cut 混音完成: %.3fs（%d 镜，J cut=%d，L cut=%d）", real, len(shots),
             sum(1 for v in cut_map.values() if v["j_cut"] > 0),
             sum(1 for v in cut_map.values() if v["l_cut"] > 0))
    return out


# ---------------------------------------------------------------------------
# 甩镜过渡（可选工具）
# ---------------------------------------------------------------------------
def whip_pan_transition(clip_a: Path | str, clip_b: Path | str,
                        out_path: Path | str, *, fps: int = config.FPS,
                        width: int = config.VIDEO_W, height: int = config.VIDEO_H,
                        duration: float = 0.30) -> Path:
    """用 A 尾帧 + B 首帧生成一段 ``duration`` 秒的水平甩镜过渡（hblur 近似）。

    适合编辑档案里的 ``whip_left``/``whip_right``：作为独立过渡片段插在两镜之间，
    或直接作为 render_v5 的额外 insert。生成失败抛 RuntimeError。
    """
    a, b = Path(clip_a), Path(clip_b)
    out = Path(out_path)
    utils.ensure_dir(out.parent)
    work = out.parent / "_whip_frames"
    utils.ensure_dir(work)
    dur_a = utils.probe_duration(a)
    frame_a = work / "a.png"
    frame_b = work / "b.png"
    utils.ffmpeg(["-ss", f"{max(0.0, dur_a - 0.10):.3f}", "-i", str(a),
                  "-frames:v", "1", "-q:v", "2", str(frame_a)])
    utils.ffmpeg(["-ss", "0.000", "-i", str(b),
                  "-frames:v", "1", "-q:v", "2", str(frame_b)])
    half = max(0.10, duration / 2.0)
    fc = (
        f"[0:v]scale={width}:{height},setsar=1,format=yuv420p,"
        f"gblur=sigma=4.0,settb=AVTB,setpts=PTS-STARTPTS,"
        f"trim=0:{duration:.3f},setpts=PTS-STARTPTS[va];"
        f"[1:v]scale={width}:{height},setsar=1,format=yuv420p,"
        f"gblur=sigma=4.0,settb=AVTB,setpts=PTS-STARTPTS,"
        f"trim=0:{duration:.3f},setpts=PTS-STARTPTS[vb];"
        f"[va][vb]xfade=transition=hblur:duration={half:.3f}:"
        f"offset={max(0.001, duration - half):.3f},"
        f"trim=0:{duration:.3f},setpts=PTS-STARTPTS[vout]"
    )
    utils.ffmpeg([
        "-loop", "1", "-t", f"{duration:.3f}", "-i", str(frame_a),
        "-loop", "1", "-t", f"{duration:.3f}", "-i", str(frame_b),
        "-filter_complex", fc, "-map", "[vout]", "-an",
        "-c:v", "libx264", "-preset", config.X264_PRESET,
        "-crf", config.X264_CRF, "-pix_fmt", "yuv420p",
        "-r", str(fps), "-t", f"{duration:.3f}", str(out),
    ])
    LOG.info("甩镜过渡完成: %s（%.2fs）", out, utils.probe_duration(out))
    return out


__all__ = [
    "XFADE_MODES",
    "VIDEO_EDITED",
    "AUDIO_EDITED",
    "DEFAULT_MODE",
    "DEFAULT_DURATION",
    "supported_modes",
    "expected_duration",
    "xfade_chain",
    "mix_audio_edit",
    "whip_pan_transition",
]
