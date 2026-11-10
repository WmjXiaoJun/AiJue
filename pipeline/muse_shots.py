# -*- coding: utf-8 -*-
"""云端 Muse 出镜（APIMart · Muse Video Ext，10 秒/镜）。

用户口径（2026-11-07）：**视频优先用云端 Muse**；图片与语音先用本地。

链路：
    brief（52 句台词 / 56 镜）→ 合并成 ~30 个 10s 段
      → 每段一张参考图（优先已有关键帧 → 场景底板 → 纯文生视频）
      → apimart Muse 生成 10s 片段 → 落 `shots_muse/<sid>.mp4`
      → 写 `work/episode.json`（timeline_source=muse_cloud）
      → 复用 `templates3d.assemble` 合成（字幕/包装由既有链路负责）

用法：
    python -m pipeline.muse_shots --template tonight_show --limit 3 --dry-run
    python -m pipeline.muse_shots --template tonight_show --limit 3
    python -m pipeline.muse_shots --template tonight_show --segments 30 --assemble
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

from . import config, muse_video, utils

REPO = Path(config.REPO_ROOT)
OUT = Path(config.OUTPUTS_ROOT)
BRIEF_DIR = REPO / "contract" / "v5" / "cinema3d" / "templates3d"
MUSE_SECONDS = 10          # muse-video-ext 只接受 10s
DEFAULT_SEGMENTS = 30      # 300s / 10s


# --------------------------------------------------------------------------- 计划
def load_brief(tid: str) -> dict:
    p = BRIEF_DIR / f"{tid}_semantic.json"
    if not p.is_file():
        raise SystemExit(f"缺少语义 brief: {p}（先跑 story/shots 阶段生成）")
    return json.loads(p.read_text(encoding="utf-8"))


def plan_segments(tid: str, *, segments: int | None = None,
                  target: float = 300.0) -> list[dict]:
    """把 brief 的镜头合并成 `MUSE_SECONDS` 秒一段的生成单元。

    Muse 每次固定 10s，所以按"累计时长 ≈10s"把相邻镜头并成一段；台词拼起来
    （字幕/配音仍按整段走）。段数默认 `target / 10`。
    """
    brief = load_brief(tid)
    shots = [s for s in (brief.get("shots") or []) if s.get("prompt")]
    if not shots:
        return []
    want = int(segments or max(1, round(float(target) / MUSE_SECONDS)))
    per = max(1, round(len(shots) / want))
    # R8：**按说话人切段** —— Muse 一段 10s 是连续镜头，段内换人会让观众看到
    # "同一个人说了两个人的台词"。先把连续同说话人的镜头分组，再把小组并到 ~per 镜。
    groups: list[list[dict]] = []
    cur: list[dict] = []
    for shot in shots:
        spk = _speaker_key(shot)
        if cur and _speaker_key(cur[-1]) != spk:
            groups.append(cur)
            cur = []
        cur.append(shot)
    if cur:
        groups.append(cur)
    chunks: list[list[dict]] = []
    for group in groups:
        if chunks and len(chunks[-1]) + len(group) <= per and \
                _speaker_key(chunks[-1][-1]) == _speaker_key(group[0]):
            chunks[-1].extend(group)
        else:
            chunks.append(list(group))
    out: list[dict] = []
    for chunk in chunks:
        if not chunk:
            continue
        head = chunk[0]
        lines = [str(s.get("line") or "").strip() for s in chunk]
        lines = [ln for ln in lines if ln]
        # 一个 10s 段只挂**一句**字幕：把多句拼成长句会让字幕横穿整屏（实测很难看）。
        # 第一句放 `line`，其余留在 `lines` 里供后续做多 cue。
        first = lines[0] if lines else ""
        for sep in ("。", "？", "！", "?", "!"):
            if sep in first and first.index(sep) < len(first) - 1:
                first = first[:first.index(sep) + 1]
                break
        roles: list[str] = []
        for s in chunk:
            for r in (s.get("roles") or []) + ([s["character"]] if s.get("character") else []):
                if r and r not in roles:
                    roles.append(str(r))
        out.append({
            "id": f"muse_{len(out) + 1:03d}",
            "source_ids": [str(s.get("id")) for s in chunk],
            "scene": str(head.get("scene") or ""),
            "prompt": str(head.get("prompt") or ""),
            "line": first,
            "lines": lines,
            "speaker": str(head.get("speaker") or (roles[0] if roles else "")),
            "roles": roles,
            "duration": MUSE_SECONDS,
            "src_shots": chunk,
        })
    return out


def _speaker_key(shot: dict) -> str:
    """段内换人判定用的键：优先 character，其次 speaker_name / speaker / roles[0]。"""
    for key in ("character", "speaker_name", "speaker"):
        val = str((shot or {}).get(key) or "").strip()
        if val:
            return val
    roles = (shot or {}).get("roles") or []
    return str(roles[0]) if roles else ""


# --------------------------------------------------------------------------- 参考图
def _scene_plate(scene: str) -> Path | None:
    if not scene:
        return None
    plate_dir = OUT / "scene_plates"
    for cand in (plate_dir / f"{scene}.png", *sorted(plate_dir.glob(f"{scene}__*.png"))):
        if cand.is_file() and cand.stat().st_size > 20_000:
            return cand
    lib = REPO / "assets" / "v5" / "scenes" / f"{scene}.png"
    return lib if lib.is_file() else None


def _existing_keyframe(tid: str, seg: dict) -> Path | None:
    roots = [REPO / "tmp" / "v16" / "keyframes" / tid]
    for sid in seg.get("source_ids") or []:
        for root in roots:
            if not root.is_dir():
                continue
            for p in root.rglob(f"{sid}.png"):
                if p.is_file() and p.stat().st_size > 30_000:
                    return p
    return None


def reference_image(tid: str, seg: dict) -> Path | None:
    """参考图：**角色段用角色立绘**，空镜段用场景底板。

    R8 实测（这是云端 Muse 能"按角色生成"的关键）：
    - 2D 关键帧当参考图 → 被内容安全拒（"did not pass content safety review"）
    - **真实感角色立绘 `portrait_real.png` → 通过**，且脸部/服装与立绘一致
    - 两张场景图当首尾帧 → 通过
    所以每镜都用说话角色的立绘，保证"同一张脸"。
    """
    if str(seg.get("line") or "").strip() or seg.get("roles"):
        ref = _char_portrait(seg)
        if ref is not None:
            return ref
    return _scene_plate(seg.get("scene") or "")


#: 立绘优先级：真实感 > 3D > 通用 > 三视图
PORTRAIT_ORDER = ("portrait_real.png", "portrait_3d.png", "portrait_25d.png",
                  "portrait.png", "front.png", "cutout.png")


def _char_portrait(seg: dict) -> Path | None:
    """按说话角色找立绘（`assets/v5/characters/<id>/portrait*.png`）。"""
    cands: list[str] = []
    sp = str(seg.get("speaker") or "").strip()
    if sp:
        cands.append(sp)
    for r in seg.get("roles") or []:
        cands.append(str(r))
    # brief 没填说话人时（classics 现状），回台本按台词反查
    if not cands:
        guess = _speaker_from_script(str(seg.get("line") or ""))
        if guess:
            cands.append(guess)
    for name in cands:
        for cid in (name, _role_to_character(name), _name_to_character(name)):
            if not cid:
                continue
            base = REPO / "assets" / "v5" / "characters" / cid
            if not base.is_dir():
                continue
            for pat in PORTRAIT_ORDER:
                p = base / pat
                if p.is_file() and p.stat().st_size > 30_000:
                    return p
    return None


def _role_to_character(role: str) -> str:
    """role(host/guest_a) → character id，读模板 cast。"""
    try:
        from . import v5_catalog

        cat = v5_catalog.load_catalog()
        for tpl in (cat.get("templates") or {}).values():
            for item in tpl.get("cast") or []:
                if str(item.get("role")) == role:
                    return str(item.get("character") or "")
    except Exception:  # noqa: BLE001
        return ""
    return ""


def _name_to_character(name: str) -> str:
    """中文名（司马迁/苏轼/老周）→ character id，读模板 cast 的 `name`。"""
    want = str(name or "").strip()
    if not want:
        return ""
    try:
        from . import v5_catalog

        cat = v5_catalog.load_catalog()
        for tpl in (cat.get("templates") or {}).values():
            for item in tpl.get("cast") or []:
                cname = str(item.get("name") or "").strip()
                if cname and (cname == want or cname in want or want in cname):
                    return str(item.get("character") or "")
    except Exception:  # noqa: BLE001
        return ""
    return ""


def _speaker_from_script(line: str) -> str:
    """brief 没写说话人时，回到台本按台词文本反查 speaker（classics 现在就是这个情况）。"""
    text = str(line or "").strip()
    if len(text) < 4:
        return ""
    scripts = sorted((OUT / "llm_scripts").glob("*.json"),
                     key=lambda p: p.stat().st_mtime, reverse=True)
    for path in scripts:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            continue
        segments = data.get("segments") if isinstance(data, dict) else data
        if not isinstance(segments, list):
            continue
        for seg in segments:
            if not isinstance(seg, dict):
                continue
            for ln in seg.get("lines") or []:
                if not isinstance(ln, dict):
                    continue
                body = str(ln.get("text") or "").strip()
                if body and (body[:12] in text or text[:12] in body):
                    return str(ln.get("speaker") or "").strip()
    return ""


# --------------------------------------------------------------------------- 生成
def _to_16x9(src: Path, cache_dir: Path, width: int = 1280, height: int = 720) -> Path:
    """把参考图铺成 16:9（立绘多为竖图，直接喂会被模型输出成左右黑边）。

    做法：底图 = 原图放大模糊铺满，前景 = 原图等比缩放居中。
    """
    dst = cache_dir / f"{src.stem}_{src.stat().st_mtime_ns % 10 ** 10}_169.png"
    if dst.is_file() and dst.stat().st_size > 20_000:
        return dst
    cache_dir.mkdir(parents=True, exist_ok=True)
    from PIL import Image, ImageFilter

    with Image.open(src) as raw:
        im = raw.convert("RGB")
        bg = im.resize((width, height), Image.LANCZOS).filter(ImageFilter.GaussianBlur(28))
        fg = im.copy()
        fg.thumbnail((width, height), Image.LANCZOS)
        bg.paste(fg, ((width - fg.width) // 2, (height - fg.height) // 2))
        bg.save(dst, "PNG")
    return dst


def _last_frame(clip: Path, cache_dir: Path) -> Path | None:
    """取上一段片子的最后一帧（作为下一段的首帧 → 长场景/同角色连续）。"""
    if not clip.is_file():
        return None
    cache_dir.mkdir(parents=True, exist_ok=True)
    dst = cache_dir / f"{clip.stem}_last169.png"
    if dst.is_file() and dst.stat().st_size > 20_000:
        return dst
    dur = float(utils.probe_duration(clip) or 0)
    tmp = cache_dir / f"{clip.stem}_last_raw.png"
    try:
        utils.ffmpeg(["-sseof", f"-{max(0.2, min(1.0, dur * 0.1)):.2f}", "-i", str(clip),
                      "-frames:v", "1", "-update", "1", "-y", str(tmp)], check=True)
    except Exception:  # noqa: BLE001
        return None
    if not tmp.is_file():
        return None
    return _to_16x9(tmp, cache_dir)


def generate_one(tid: str, seg: dict, *, out_dir: Path, force: bool = False,
                 prog=None, prev_clip: Path | None = None, chain: bool = True) -> dict:
    out = out_dir / f"{seg['id']}.mp4"
    if out.is_file() and out.stat().st_size > 200_000 and not force:
        return {"id": seg["id"], "ok": True, "reused": True, "out": str(out),
                "duration": utils.probe_duration(out)}
    cache = out_dir / "_ref"
    # 参考图：优先"上一段最后一帧"（首尾帧衔接，长场景一致），否则用本段角色立绘
    ref: Path | None = None
    if chain and prev_clip is not None:
        ref = _last_frame(prev_clip, cache)
    if ref is None:
        ref = reference_image(tid, seg)
    if ref is not None:
        ref = _to_16x9(ref, cache)
    spec = {
        "provider": os.environ.get("ZAOPIANJU_MUSE_PROVIDER", "apimart_muse"),
        "prompt": _prompt_for(seg),
        "image": str(ref) if ref else "",
        "duration": MUSE_SECONDS,
        "aspect": "16:9",
    }
    t0 = time.time()
    res: dict = {}
    # R8：内容安全是**概率性**的（同一张立绘/同一句 prompt，前一次过、后一次被拒）。
    # 逐级降级重试，保证一段失败不会让整片少一镜：
    #   ① 原 prompt + 角色立绘 → ② 极简安全 prompt + 立绘 → ③ 场景底板（保镜头、弃人物）
    attempts: list[dict] = [dict(spec)]
    if ref is not None:
        attempts.append({**spec, "prompt": _SAFE_FALLBACK_PROMPT})
    plate = _scene_plate(seg.get("scene") or "")
    if plate is not None:
        p169 = _to_16x9(plate, cache)
        attempts.append({**spec, "image": str(p169), "prompt": _SAFE_FALLBACK_PROMPT})
    errors: list[str] = []
    for attempt, use in enumerate(attempts, start=1):
        try:
            res = muse_video.generate_video(use, prog=prog)
        except Exception as exc:  # noqa: BLE001
            msg = f"{type(exc).__name__}: {exc}"
            errors.append(f"#{attempt} {msg[:160]}")
            if prog:
                prog("muse", f"{seg['id']} 第 {attempt} 次失败，降级重试：{msg[:120]}")
            continue
        if res.get("ok"):
            if attempt > 1:
                res["degraded"] = attempt
            break
    if not res.get("ok"):
        return {"id": seg["id"], "ok": False,
                "error": (str(res.get("error"))[:300] if res.get("error")
                          else "; ".join(errors)[:300] or "全部降级尝试失败")}
    src = Path(str(res.get("out") or ""))
    if not src.is_file():
        return {"id": seg["id"], "ok": False, "error": f"未产出文件: {src}"}
    out_dir.mkdir(parents=True, exist_ok=True)
    if src != out:
        # R7：Muse 会在画面**底部自己写一行文字**（实测是一条中文长句），和我们的字幕
        # 打架。裁掉底部 12% 再拉回 1280×720，把这行模型自带的字切掉。
        ok = _crop_model_text(src, out)
        if not ok:
            out.write_bytes(src.read_bytes())
    return {"id": seg["id"], "ok": True, "out": str(out),
            "duration": round(float(utils.probe_duration(out) or 0), 2),
            "elapsed": round(time.time() - t0, 1), "provider": res.get("provider")}


def _crop_model_text(src: Path, dst: Path, bottom_band: float = 0.11,
                     width: int = 1280, height: int = 720) -> bool:
    """盖住 Muse 自己写在画面底部的那行字（它不听 prompt 里的 "no text"）。

    做法：在底部 `bottom_band` 比例处画一条纯色"字幕条"（渐入两段，避免生硬），
    我们的 Main 字幕正好压在这条带上 —— 既是电视字幕条的观感，也遮住模型的乱码文字。
    """
    try:
        # 一条**不透明**的字幕条：Muse 自己写的字压在这条带里会被彻底盖住，
        # 我们的 Main 字幕正好落在带上（MarginV≈54），可读性也最好。
        vf = (f"drawbox=x=0:y=ih*{1 - bottom_band:.3f}:w=iw:h=ih*{bottom_band:.4f}:"
              f"color=0x000000@1.0:t=fill,"
              f"scale={width}:{height}")
        utils.ffmpeg(["-i", str(src), "-vf", vf, "-an",
                      "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
                      "-pix_fmt", "yuv420p", "-y", str(dst)], check=True)
        return dst.is_file() and dst.stat().st_size > 50_000
    except Exception as exc:  # noqa: BLE001
        print(f"[warn] 遮盖模型文字失败，用原片: {exc}", flush=True)
        return False


#: 降级用的极简 prompt：内容安全最不容易挑刺的说法（只描述镜头，不描述情节/品牌/文字）。
_SAFE_FALLBACK_PROMPT = ("A person sitting at a desk in a television studio, medium shot from the front, "
                         "calm expression, natural motion, soft studio lighting, no text")


#: Muse(APIMart) 的内容安全很挑措辞：实测**直接发 brief 的长 prompt 会被拒**
#: （"did not pass content safety review"），而"自写的短句"能过。所以这里不用 brief 原文，
#: 只从里面抽场景关键词，再套一层安全模板。
SCENE_HINT = {
    "late_night_studio": "a modern late-night TV studio with a glossy desk, microphone and warm lamps",
    "neon_skyline": "a night city skyline with neon lights seen through a studio window",
    "backstage_neon": "a neon-lit backstage corridor with equipment cases",
    "neon_alley": "a rainy neon alley outside a TV studio",
    "archive_vault": "an old library vault with wooden shelves and scrolls",
    "scholar_study": "a candle-lit scholar study with books and a writing desk",
    "palace_hall": "a grand ancient palace hall with red pillars",
    "temple_court": "an ancient temple courtyard in soft morning light",
}


def _safe_prompt(seg: dict) -> str:
    scene = str(seg.get("scene") or "").strip()
    place = SCENE_HINT.get(scene) or "a cinematic TV studio interior at night"
    if str(seg.get("line") or "").strip():
        return (f"A talk-show host sits at a desk in {place}, medium shot from the front, "
                f"speaking to camera, natural subtle motion, warm cinematic lighting, "
                f"photoreal film still, no text, no watermark")
    return (f"Empty {place}, no people, slow cinematic camera push-in, "
            f"cinematic lighting, photoreal film still, no text, no watermark")


def _prompt_for(seg: dict) -> str:
    """Muse 用的 prompt（短、稳、可过审）。"""
    return _safe_prompt(seg)


# --------------------------------------------------------------------------- 时间线
def write_episode(tid: str, segs: list[dict], clips: dict[str, dict],
                  filename: str = "episode.json") -> Path:
    """把成功的 Muse 片段写成 assemble 用的时间线（timeline_source=muse_cloud）。

    ``filename`` 默认写权威的 ``episode.json``；只有全部片段成功才允许覆盖它，
    部分成功写 ``episode.partial.json``（resolve_timeline 不会选中，避免 0 镜/缺镜
    的时间线把可用成片顶掉）。
    """
    shots: list[dict] = []
    for seg in segs:
        info = clips.get(seg["id"]) or {}
        if not info.get("ok"):
            continue
        dur = float(info.get("duration") or MUSE_SECONDS)
        shots.append({
            "id": seg["id"], "type": "dialogue" if seg.get("line") else "broll",
            "duration": round(dur, 3), "line": seg.get("line") or "",
            "scene": seg.get("scene") or "", "roles": seg.get("roles") or [],
            "characters": seg.get("roles") or [], "speaker": seg.get("speaker") or "",
            "character": seg.get("speaker") or "",
            "prompt_source": "muse_cloud",
        })
    for i, s in enumerate(shots, start=1):
        s["no"] = i
    work = OUT / "templates_3d" / tid / "work"
    work.mkdir(parents=True, exist_ok=True)
    total = round(sum(s["duration"] for s in shots), 3)
    ep = {
        "episode": {"id": tid, "label": tid, "resolution": "1280x720", "fps": 25,
                    "target_duration": total,
                    "total_duration": total},
        # 关键：target_duration 必须等于实际片段总长。写成 300 会触发 render_v5 的
        # "长片补空镜"分支（实测 3×10s 的片子被撑成 300s）。
        "meta": {"template": tid, "timeline_source": "muse_cloud", "seed": 20261107,
                 "target_duration": total},
        "shots": shots,
    }
    path = work / filename
    utils.write_json(path, ep)
    return path


def render(tid: str, *, limit: int | None = None, segments: int | None = None,
           target: float = 300.0, force: bool = False, dry_run: bool = False,
           assemble: bool = False, chain: bool = True, prog=None) -> dict:
    segs = plan_segments(tid, segments=segments, target=target)
    if limit:
        segs = segs[:int(limit)]
    plan = [{"id": s["id"], "scene": s["scene"], "roles": s["roles"],
             "speaker": s.get("speaker"),
             "ref": str(reference_image(tid, s) or ""), "prompt": _prompt_for(s)[:120]}
            for s in segs]
    if dry_run:
        return {"ok": True, "dry_run": True, "template": tid,
                "segments": len(segs), "plan": plan, "chain": chain,
                "provider": muse_video.available().get("provider")}
    out_dir = OUT / "templates_3d" / tid / "shots"
    out_dir.mkdir(parents=True, exist_ok=True)
    clips: dict[str, dict] = {}
    failed: list[str] = []
    prev_clip: Path | None = None
    for i, seg in enumerate(segs, start=1):
        if prog:
            prog("muse", f"[{i}/{len(segs)}] {seg['id']} · {seg.get('scene') or '-'} · "
                        f"{seg.get('speaker') or '空镜'}")
        try:
            info = generate_one(tid, seg, out_dir=out_dir, force=force, prog=prog,
                                prev_clip=prev_clip, chain=chain)
        except Exception as exc:  # noqa: BLE001
            info = {"id": seg["id"], "ok": False, "error": f"{type(exc).__name__}: {exc}"[:300]}
        clips[seg["id"]] = info
        if info.get("ok"):
            prev_clip = Path(str(info.get("out")))
        else:
            failed.append(f"{seg['id']}: {info.get('error')}")
    ok_count = sum(1 for c in clips.values() if c.get("ok"))
    all_ok = bool(segs) and ok_count == len(segs)
    # 全成：写权威 episode.json；部分成：只写 episode.partial.json；全败：不写。
    ep = write_episode(tid, segs, clips) if all_ok else (
        write_episode(tid, segs, clips, filename="episode.partial.json")
        if ok_count else None)
    result = {"ok": bool(ok_count), "partial": not all_ok,
              "template": tid, "segments": len(segs),
              "episode": str(ep) if ep else None,
              "ok_count": ok_count,
              "failed": failed[:6], "clips": [c.get("out") for c in clips.values()
                                              if c.get("ok")][:6]}
    if assemble and not all_ok:
        result["assemble"] = {"skipped": "有片段失败/缺失，未写权威时间线；修复后重跑 --assemble"}
    elif assemble:
        from . import templates3d as t3
        try:
            rep = t3.assemble(tid)
            result["assemble"] = {"final": rep.get("final"),
                                  "dur": (rep.get("probe") or {}).get("format", {}).get("duration")}
        except Exception as exc:  # noqa: BLE001
            result["assemble"] = {"error": f"{type(exc).__name__}: {exc}"[:300]}
    return result


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="云端 Muse 出镜（10s/镜）")
    ap.add_argument("--template", required=True)
    ap.add_argument("--limit", type=int, default=None, help="只生成前 N 段（冒烟）")
    ap.add_argument("--segments", type=int, default=None, help="目标段数（默认 30 段=300s）")
    ap.add_argument("--target", type=float, default=300.0)
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--assemble", action="store_true", help="生成后直接合成")
    args = ap.parse_args(argv)
    res = render(args.template, limit=args.limit, segments=args.segments,
                 target=args.target, force=args.force, dry_run=args.dry_run,
                 assemble=args.assemble,
                 prog=lambda who, msg: print(f"[{who}] {msg}", flush=True))
    print(json.dumps(res, ensure_ascii=False, indent=1)[:4000])
    return 0 if res.get("ok") or res.get("dry_run") else 1


if __name__ == "__main__":
    raise SystemExit(main())
