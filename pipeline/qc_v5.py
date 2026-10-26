# -*- coding: utf-8 -*-
"""V5 QC：成片规格 + 风格 + 口型 + 字幕 + 风格指纹。

与 V3/V4 QC 的区别：
- V5 无 H3 证据链要求（对白/画面由 motion2d 渲染），改为检查**场景/角色/风格一致性**；
- 新增 `style_fingerprint`（调色直方图 + 边缘密度）供 16 部两两区分度对比；
- 新增 `lip_sync`（若 plan 提供 LSE-C 结果）；
- 保留 V3 通用硬门：时长/分辨率/fps/音轨/黑帧/字幕覆盖。
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from . import config, utils

LOG = __import__("logging").getLogger("pipeline")

EXPECT_W, EXPECT_H, EXPECT_FPS = 1280, 720, 25
DURATION_TOLERANCE = 0.8
SUBTITLE_MIN_COVERAGE = 0.9


def _check(ok: bool, detail: str = "", **extra) -> dict:
    out = {"ok": bool(ok), "detail": detail}
    out.update(extra)
    return out


def probe_video(path: Path) -> dict:
    try:
        return utils.probe_video(path)
    except Exception as exc:  # noqa: BLE001
        return {"duration": 0.0, "width": 0, "height": 0, "fps": 0.0,
                "has_audio": False, "error": str(exc)}


def blackdetect_count(video: Path, timeout: float = 600,
                      min_duration: float = 0.30,
                      pix_th: float = 0.10) -> tuple[int, str]:
    try:
        proc = utils.run([str(config.FFMPEG_BIN), "-hide_banner", "-nostats",
                          "-i", str(video), "-vf",
                          f"blackdetect=d={min_duration:.2f}:pix_th={pix_th:.2f}", "-an", "-f", "null",
                          "-"], check=False, timeout=timeout)
        text = (proc.stderr or "")
        count = text.count("black_start:")
        return count, text[-400:]
    except Exception as exc:  # noqa: BLE001
        return -1, str(exc)


def style_fingerprint(video: Path, frames: int = 6) -> dict:
    """抽取若干帧，算颜色直方图（HSV 8x8x4）+ 边缘密度，作为风格指纹。"""
    try:
        import numpy as np
    except Exception:  # noqa: BLE001
        return {}
    tmp = Path(config.REPO_ROOT) / "tmp" / "v5_qc" / f"_fp_{video.stem}"
    utils.ensure_dir(tmp)
    clip = utils.probe_video(video)
    dur = float(clip.get("duration") or 0)
    hist = np.zeros(8 * 8 * 4, dtype=np.float64)
    edges: list[float] = []
    try:
        import subprocess
        for i in range(frames):
            t = dur * (i + 0.5) / frames
            png = tmp / f"{i:02d}.png"
            utils.ffmpeg(["-ss", f"{t:.2f}", "-i", str(video), "-frames:v", "1",
                          "-y", str(png)])
            from PIL import Image
            img = Image.open(png).convert("RGB").resize((160, 90))
            arr = np.asarray(img, dtype=np.float32) / 255.0
            # HSV 粗直方图
            import colorsys
            h = np.zeros((8, 8, 4))
            r, g, b = arr[..., 0], arr[..., 1], arr[..., 2]
            mx = arr.max(axis=-1)
            mn = arr.min(axis=-1)
            v = mx
            s = np.where(mx > 0, (mx - mn) / np.maximum(mx, 1e-6), 0)
            # 近似 hue（用简单公式）
            hue = np.zeros_like(mx)
            delta = np.maximum(mx - mn, 1e-6)
            rmask = (mx == r)
            gmask = (mx == g) & ~rmask
            bmask = ~(rmask | gmask)
            hue[rmask] = ((g - b)[rmask] / delta[rmask]) % 6
            hue[gmask] = ((b - r)[gmask] / delta[gmask]) + 2
            hue[bmask] = ((r - g)[bmask] / delta[bmask]) + 4
            hue = hue / 6.0
            for yy in range(90):
                for xx in range(160):
                    hh = min(7, int(hue[yy, xx] * 8))
                    ss = min(7, int(s[yy, xx] * 8))
                    vv = min(3, int(v[yy, xx] * 4))
                    h[hh, ss, vv] += 1
            hist += h.reshape(-1) / (160 * 90)
            # 边缘密度（灰度梯度）
            gray = arr.mean(axis=-1)
            gx = np.abs(np.diff(gray, axis=1)).mean()
            gy = np.abs(np.diff(gray, axis=0)).mean()
            edges.append(float(gx + gy))
    finally:
        try:
            for p in tmp.glob("*.png"):
                p.unlink()
        except OSError:
            pass
    total = hist.sum() or 1.0
    return {"hist": [round(float(x / total), 6) for x in hist],
            "edge_density": round(float(sum(edges) / max(1, len(edges))), 4),
            "frames": frames}


def _fmt_shots(plan: dict) -> list[dict]:
    shots = (plan or {}).get("shots") or []
    return [s for s in shots if isinstance(s, dict)]


def evaluate(*, video: Path, plan: dict, qc_extra: dict | None = None,
             subtitles: Path | None = None) -> dict:
    """V5 QC 主入口；返回 {ok, checks, stats, fingerprint}。"""
    video = Path(video)
    probe = probe_video(video) if video.is_file() else {
        "duration": 0.0, "width": 0, "height": 0, "fps": 0.0, "has_audio": False}
    shots = _fmt_shots(plan)
    expected = round(sum(float(s.get("duration") or 0) for s in shots), 3)
    checks: dict[str, dict] = {}

    actual = float(probe.get("duration") or 0.0)
    checks["duration"] = _check(abs(actual - expected) <= DURATION_TOLERANCE,
                                f"成片 {actual:.2f}s / 计划 {expected:.2f}s",
                                expected=expected, actual=actual)
    checks["resolution"] = _check(
        (probe.get("width"), probe.get("height")) == (EXPECT_W, EXPECT_H),
        f"{probe.get('width')}x{probe.get('height')}")
    checks["fps"] = _check(abs(float(probe.get("fps") or 0) - EXPECT_FPS) <= 0.6,
                           f"{probe.get('fps')}fps")
    checks["audio"] = _check(bool(probe.get("has_audio")), "有音轨" if probe.get("has_audio") else "缺音轨")

    black = -1
    detail = ""
    if video.is_file():
        black, detail = blackdetect_count(video)
    checks["blackdetect"] = _check(black == 0, f"黑帧段={black}", black_segments=black)

    # 场景/角色覆盖：计划里每个场景都被引用且角色存在（卡片镜头不参与）
    body_shots = [s for s in shots if str(s.get("type")) != "card"]
    scenes = {str(s.get("scene")) for s in body_shots if s.get("scene")}
    chars = {str(s.get("character")) for s in body_shots if s.get("character")}
    missing_scene = [s.get("id") for s in body_shots if not s.get("scene")]
    missing_char = [s.get("id") for s in body_shots
                    if s.get("type") == "dialogue" and not s.get("character")]
    checks["scene_coverage"] = _check(not missing_scene,
                                      f"{len(scenes)} 个场景" if not missing_scene
                                      else f"缺场景: {missing_scene}", scenes=sorted(scenes))
    checks["character_coverage"] = _check(not missing_char,
                                          f"{len(chars)} 个角色" if not missing_char
                                          else f"缺角色: {missing_char}", characters=sorted(chars))

    # 字幕覆盖：按镜头时间窗 + 字符覆盖率匹配（兼容水墨竖排把一句拆成多列事件）
    sub_ok = True
    sub_detail = "未提供字幕文件（跳过）"
    if subtitles and Path(subtitles).is_file():
        raw = Path(subtitles).read_text(encoding="utf-8", errors="replace")
        events = []
        for line in raw.splitlines():
            if not line.startswith("Dialogue:"):
                continue
            parts = line.split(",", 9)
            if len(parts) < 10:
                continue
            try:
                def _ts(txt: str) -> float:
                    hh, mm, ss = txt.strip().split(":")
                    return int(hh) * 3600 + int(mm) * 60 + float(ss)
                t0 = _ts(parts[1])
                t1 = _ts(parts[2])
            except (ValueError, IndexError):
                continue
            text = parts[9]
            text = re.sub(r"\{[^}]*\}", "", text)
            text = text.replace("\\N", "").replace("\\n", "")
            text = re.sub(r"\s+", "", text)
            events.append((t0, t1, text))

        def _norm(value: str) -> str:
            v = re.sub(r"[，。！？、；：,.!?;:\"'（）()【】《》…—\-]", "", str(value or ""))
            return re.sub(r"\s+", "", v)

        # 镜头绝对起止
        cursor = 0.0
        windows = {}
        for shot in body_shots:
            dur = float(shot.get("duration") or 0.0)
            windows[str(shot.get("id"))] = (cursor, cursor + dur)
            cursor += dur

        dialog = [s2 for s2 in body_shots if s2.get("line")]
        missing = []
        for shot in dialog:
            line = _norm(shot.get("line"))
            if not line:
                continue
            w0, w1 = windows.get(str(shot.get("id")), (0.0, 0.0))
            texts = "".join(t for (e0, e1, t) in events
                            if e1 > w0 - 0.15 and e0 < w1 + 0.15)
            if not texts:
                missing.append(shot.get("id"))
                continue
            chars = set(line)
            hit = sum(1 for c in chars if c in texts)
            coverage = hit / max(1, len(chars))
            if coverage < 0.70:
                missing.append(shot.get("id"))
        sub_ok = not missing
        sub_detail = (f"对话镜 {len(dialog) - len(missing)}/{len(dialog)} 有字幕"
                      if dialog else "无对话镜")
        checks["subtitle_coverage"] = _check(sub_ok, sub_detail, missing=missing)

    extra = qc_extra or {}
    if extra.get("lip_sync"):
        ls = extra["lip_sync"]
        ok = bool(ls.get("ok", True))
        checks["lip_sync"] = _check(ok, ls.get("detail", ""), **{
            k: v for k, v in ls.items() if k not in ("ok", "detail")})
    if extra.get("style"):
        checks["style"] = _check(bool(extra["style"].get("style")), 
                                 str(extra["style"].get("style")))
    if extra.get("fallbacks") is not None:
        checks["fallbacks"] = _check(not extra["fallbacks"],
                                     f"fallbacks={extra['fallbacks']}")

    ok = all(v.get("ok") for v in checks.values())
    fp = style_fingerprint(video) if video.is_file() else {}
    stats = {
        "expected_duration": expected, "actual_duration": actual,
        "shots": len(body_shots),
        "cards": sum(1 for s in shots if str(s.get("type")) == "card"),
        "dialogue_shots": sum(1 for s in body_shots if s.get("line")),
        "broll_shots": sum(1 for s in body_shots if not s.get("line")),
        "scenes": len(scenes), "characters": len(chars),
        "style": str((plan.get("episode") or {}).get("style") or ""),
        "black_segments": black,
        "edge_density": fp.get("edge_density"),
    }
    return {"ok": ok, "checks": checks, "stats": stats, "fingerprint": fp}


def write_report(report: dict, path: Path) -> None:
    utils.write_json(path, report)
