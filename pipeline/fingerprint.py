# -*- coding: utf-8 -*-
"""统一缓存指纹：把「文件存在 + 体积够大」这种假命中判据，换成「输入指纹一致」。

背景（docs/audit/AUDIT_render.md P1-1 / P1-2 / AUDIT_extras.md 跨域根因 2）：
- `templates3d_hq.gen_t2va/gen_ref2va` 只判 `st_size > 200_000` → 改 prompt / seed /
  时长（frame_length）后仍复用旧片；
- `_render` 的体积短路会把**旧图**登记成新 `prompt_sig`（缓存毒化）；
- `v5_scene_art._meta_matches` 漏 steps/cfg/sampler/scheduler、`motion2d._get_layerset`
  的缓存键漏 duration/seed —— 同一套"判据不全"的问题。

本模块只做一件事：给定一组**决定产物的输入**，算出一个稳定指纹，并把它原子写进
`<产物>.meta.json`；命中条件是「产物存在 + meta 里的指纹与本次一致」。

用法::

    from . import fingerprint as fp

    sig = fp.signature("hq-t2va", prompt=prompt, seed=seed, width=1024, height=576,
                       length=length, mode=style_mode, look=look, style_id=style_key)
    if not force and fp.meta_matches(out, sig):
        return out                      # 真命中
    ...真渲染...
    fp.write_meta(out, sig, {"kind": "hq-t2va", "prompt_id": pid, "seed": seed})

约定：`meta_matches` 只认 `meta["fingerprint"]`，**绝不**用体积当命中判据；
`size`/`mtime` 只用来排除 0 字节/半截文件（`MIN_ASSET_BYTES`）。
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

META_SUFFIX = ".meta.json"
#: 只用来排除"文件是空的/写了一半"，不是命中判据
MIN_ASSET_BYTES = 1024
#: meta 里存指纹的键名（h3_comfy 的 meta 也用同一个名字，便于跨栈对账）
SIG_KEY = "fingerprint"


def canonical(obj: Any) -> str:
    """稳定 JSON 序列化：key 排序、无空白、非字符串走 repr 兜底。"""
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, default=repr,
                      separators=(",", ":"))


def signature(*parts: Any, **fields: Any) -> str:
    """输入指纹：位置参数按顺序拼，关键字参数按 key 排序拼；输出 16 位 hex。

    - 浮点统一量化到 4 位小数，避免 0.1+0.2 这类噪音让缓存永远 miss；
    - None / "" / False 不做等价折叠（它们对产物可能不同）。
    """
    h = hashlib.sha1()
    for part in parts:
        h.update(_norm(part).encode("utf-8"))
        h.update(b"\x00")
    if fields:
        h.update(canonical({k: _norm(v) for k, v in fields.items()}).encode("utf-8"))
    return h.hexdigest()[:16]


def _norm(value: Any) -> Any:
    """把标量规整成稳定形式（浮点量化、Path 转字符串）。"""
    if isinstance(value, float):
        return round(value, 4)
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(k): _norm(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_norm(v) for v in value]
    return value


def meta_path_for(asset: Path | str) -> Path:
    """``clips/a.mp4`` -> ``clips/a.meta.json``（与 ``h3_comfy.meta_path_for`` 同一约定）。

    换成后缀而不是追加，是为了让 ``h3_comfy.load_meta(shots/<sid>.mp4)`` 能直接找到
    本模块写的 ``shots/<sid>.meta.json`` —— 两栈的证据链必须同名同位置。
    """
    p = Path(asset)
    return p.with_suffix(META_SUFFIX) if p.suffix else Path(str(p) + META_SUFFIX)


def asset_ok(asset: Path | str, min_bytes: int = MIN_ASSET_BYTES) -> bool:
    """产物存在且不是空文件/半截文件（**不**代表命中）。"""
    try:
        p = Path(asset)
        return p.is_file() and p.stat().st_size >= max(0, int(min_bytes))
    except OSError:
        return False


def read_meta(asset: Path | str) -> dict:
    """读产物同名 meta；缺失/损坏返回 {}。"""
    path = meta_path_for(asset)
    try:
        if not path.is_file():
            return {}
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def write_meta(asset: Path | str, sig: str, extra: dict | None = None) -> Path:
    """原子写 ``<asset>.meta.json``：指纹 + 调用方给的证据字段。"""
    path = meta_path_for(asset)
    payload: dict[str, Any] = {"kind": "fingerprint", SIG_KEY: str(sig)}
    payload.update({k: v for k, v in (extra or {}).items() if k != SIG_KEY})
    payload[SIG_KEY] = str(sig)
    try:
        from . import utils  # 局部导入：本模块要能被无 pipeline 依赖的脚本直接 import

        utils.write_json(path, payload)
    except Exception:  # noqa: BLE001 - 指纹写失败不该让渲染整条挂掉
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def meta_matches(asset: Path | str, sig: str, *,
                 min_bytes: int = MIN_ASSET_BYTES) -> bool:
    """命中 = 产物存在 + meta 指纹与本次一致。体积只用于排除空文件。"""
    if not asset_ok(asset, min_bytes):
        return False
    meta = read_meta(asset)
    if not meta:
        return False
    return str(meta.get(SIG_KEY) or "") == str(sig)


def file_signature(path: Path | str | None, *, chunk: int = 1 << 20) -> str:
    """内容签名（图片/音频作参考输入时用）；文件缺失返回 ""。"""
    try:
        p = Path(str(path or ""))
        if not p.is_file():
            return ""
        h = hashlib.sha1()
        with open(p, "rb") as fh:
            while True:
                block = fh.read(chunk)
                if not block:
                    break
                h.update(block)
        return h.hexdigest()[:16]
    except OSError:
        return ""


# ---------------------------------------------------------------------------
# 供其它模块直接复用的键构造器（同一套参数集，避免各自漏项）
# ---------------------------------------------------------------------------
def keyframe_signature(*, template: str, shot_id: str, character: str, prompt: str,
                       negative: str, seed: int, style_key: str, ckpt: str,
                       cfg: float, steps: int, sampler: str, scheduler: str,
                       ip_strength: float, ip_end: float, ip_weight_type: str,
                       candidates: int, chosen: int,
                       width: int = 1024, height: int = 576) -> str:
    """HQ 关键帧（SDXL）指纹：prompt + 全部采样参数 + 抽卡/选卡 + 尺寸。"""
    return signature(
        "keyframe", template=template, shot=shot_id, character=character,
        prompt=prompt, negative=negative, seed=int(seed), style_id=style_key,
        ckpt=ckpt, cfg=float(cfg), steps=int(steps), sampler=sampler,
        scheduler=scheduler, ip_strength=float(ip_strength), ip_end=float(ip_end),
        ip_weight_type=ip_weight_type, candidates=int(candidates), chosen=int(chosen),
        width=int(width), height=int(height))


def scene_plate_signature(*, scene_id: str, prompt: str, negative: str, seed: int,
                          size: tuple[int, int], model: str, steps: int,
                          cfg: float, sampler: str, scheduler: str) -> str:
    """场景 plate（SDXL txt2img）指纹：补上 `_meta_matches` 漏掉的 4 个采样参数。"""
    return signature(
        "scene-plate", scene=scene_id, prompt=prompt, negative=negative, seed=int(seed),
        size=[int(size[0]), int(size[1])], model=model, steps=int(steps),
        cfg=float(cfg), sampler=sampler, scheduler=scheduler)


def layerset_signature(*, camspec: str, style_sig: str, size: tuple[int, int],
                       duration: float, seed: int) -> str:
    """motion2d `_get_layerset` 的缓存键：补上 duration + seed。"""
    return signature(
        "layerset", camspec=camspec, style=style_sig,
        size=[int(size[0]), int(size[1])],
        duration=round(float(duration), 3), seed=int(seed))


__all__ = ["META_SUFFIX", "MIN_ASSET_BYTES", "SIG_KEY", "canonical", "signature",
           "meta_path_for", "asset_ok", "read_meta", "write_meta", "meta_matches",
           "file_signature", "keyframe_signature", "scene_plate_signature",
           "layerset_signature"]