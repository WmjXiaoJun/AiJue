# -*- coding: utf-8 -*-
"""AUDIT_render P1-1 / P1-2 / P1-4 自证：缓存判据「体积」→「输入指纹」。

全部在 `tmp/` 下用假文件 + monkeypatch 跑**真实函数**（不联网、不占 GPU、不写 outputs/）：
  A. gen_t2va：同输入 → 命中（不再提交）；改 prompt → **未命中**（重新提交）
     并对照旧判据 `size > 200_000`：同一份假文件在改 prompt 后仍然「命中」= 假命中
  B. 关键帧 _render 短路：`_cache_hit` 比对指纹；旧判据只看体积 → 改 prompt 仍复用旧图
  C. per-shot meta：HQ 写 `shots/<sid>.meta.json` 后，h3_comfy.load_meta/meta_ok 能认
  D. 静态断言：`return key_png` 之前不会写 meta（"只有真渲完才写 prompt_sig"）
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO))

from pipeline import fingerprint, h3_comfy, templates3d_hq as hq, utils  # noqa: E402

TMP = Path(tempfile.mkdtemp(prefix="r4_fp_"))
hq.WORK = TMP / "work"              # 不碰真实 tmp/v16 缓存
FAKE_MP4 = b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 250_000   # >200KB 的假 mp4


class _Renderer:
    """假的 ComfyUI 客户端：只落一个 >200KB 的假 mp4，记录提交次数。"""

    def __init__(self) -> None:
        self.submits: list[dict] = []
        self._pid = 0

    def install(self) -> None:
        hq.local.build_workflow = lambda prompt, w, h, length, seed, prefix: {
            "prompt": prompt, "width": w, "height": h, "length": length,
            "seed": seed, "prefix": prefix}
        hq.local.submit = self._submit
        hq.local.wait_and_fetch = self._fetch

    def _submit(self, wf: dict) -> str:
        self.submits.append(wf)
        self._pid += 1
        return f"pid-{self._pid}"

    def _fetch(self, pid: str, out: Path, timeout: float = 0) -> dict:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(FAKE_MP4)
        return {"file": str(out), "bytes": len(FAKE_MP4), "raw": "scene.mp4"}


SHOT = {"id": "v5_test01", "prompt": "a rainy alley, neon signs",
        "duration": 4.0, "line": "", "semantic_visual": "alley"}


def part_a(renderer: _Renderer) -> dict:
    print("\n=== A. gen_t2va：改 prompt 后必须未命中 ===")
    out = hq.gen_t2va("aa_test", SHOT, 12345, "3d", look="3d_anim")
    n1 = len(renderer.submits)
    print(f"  1) 首次渲：submits={n1} -> {out.name}")
    again = hq.gen_t2va("aa_test", SHOT, 12345, "3d", look="3d_anim")
    n2 = len(renderer.submits)
    print(f"  2) 同输入重跑：submits={n2}（+{n2 - n1}）→ 命中={n2 == n1}")

    changed = dict(SHOT, prompt="a snowy rooftop at dawn")   # 改一句 prompt
    hq.gen_t2va("aa_test", changed, 12345, "3d", look="3d_anim")
    n3 = len(renderer.submits)
    print(f"  3) 改 prompt 后：submits={n3}（+{n3 - n2}）→ 未命中={n3 == n2 + 1}")

    size = out.stat().st_size
    old_hit = out.is_file() and size > 200_000        # 旧判据（P1-1）
    new_meta = fingerprint.read_meta(out)
    print(f"  4) 旧判据 size>200_000 = {old_hit}（改 prompt 也会命中=假命中）")
    print(f"     新判据 meta.fingerprint={new_meta.get('fingerprint')} "
          f"seed={new_meta.get('seed')} prompt_id={new_meta.get('prompt_id')}")
    assert n2 == n1, "同输入应当命中"
    assert n3 == n2 + 1, "改 prompt 必须重新渲染"
    assert new_meta.get("prompt_id"), "clip meta 必须带 prompt_id"
    return {"hit_same": n2 == n1, "miss_after_prompt_change": n3 == n2 + 1,
            "old_predicate_would_hit": old_hit}


def part_b() -> dict:
    print("\n=== B. 关键帧 _render 短路：只看体积 → 改 prompt 仍复用旧图（缓存毒化） ===")
    png = TMP / "keyframes" / "jp_anime" / "v5_test01.png"
    png.parent.mkdir(parents=True, exist_ok=True)
    png.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 250_000)   # >100KB
    sig_old = fingerprint.keyframe_signature(
        template="aa_test", shot_id="v5_test01", character="laozhou",
        prompt="rainy alley", negative="text", seed=7, style_key="jp_anime",
        ckpt="animagine-xl-4.0.safetensors", cfg=5.5, steps=30,
        sampler="euler_ancestral", scheduler="normal", ip_strength=0.5,
        ip_end=0.58, ip_weight_type="style transfer", candidates=1, chosen=0)
    hq._render_meta(png, sig_old, {"kind": "keyframe", "shot": "v5_test01",
                                   "chosen": 0, "prompt_sig": "deadbeef"})
    sig_new = fingerprint.keyframe_signature(
        template="aa_test", shot_id="v5_test01", character="laozhou",
        prompt="SNOWY ROOFTOP", negative="text", seed=7, style_key="jp_anime",
        ckpt="animagine-xl-4.0.safetensors", cfg=5.5, steps=30,
        sampler="euler_ancestral", scheduler="normal", ip_strength=0.5,
        ip_end=0.58, ip_weight_type="style transfer", candidates=1, chosen=0)
    same = hq._cache_hit(png, sig_old)
    changed = hq._cache_hit(png, sig_new)
    old_pred = png.is_file() and png.stat().st_size > 100_000
    print(f"  1) 同指纹命中         : {same}")
    print(f"  2) 改 prompt 后命中   : {changed}（新判据；旧判据 size>100_000 = {old_pred} → 假命中）")
    # 步骤/采样参数变化也必须失效（P2-13 的同类漏项）
    sig_steps = fingerprint.keyframe_signature(
        template="aa_test", shot_id="v5_test01", character="laozhou",
        prompt="rainy alley", negative="text", seed=7, style_key="jp_anime",
        ckpt="animagine-xl-4.0.safetensors", cfg=6.5, steps=24,
        sampler="dpmpp_sde", scheduler="karras", ip_strength=0.5,
        ip_end=0.58, ip_weight_type="style transfer", candidates=1, chosen=0)
    steps_hit = hq._cache_hit(png, sig_steps)
    print(f"  3) 只改 steps/cfg/sampler/scheduler 后命中: {steps_hit}（应 False）")
    # 体积被截断（meta 记录的 bytes 与实际不符）→ 不认这条缓存
    png.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 1000)
    truncated = hq._cache_hit(png, sig_old)
    print(f"  4) 产物被改写/截断后命中: {truncated}（应 False）")
    assert same is True and changed is False and steps_hit is False and truncated is False
    return {"same": same, "changed": changed, "steps_changed": steps_hit,
            "truncated": truncated, "old_predicate_would_hit": old_pred}


def part_c() -> dict:
    print("\n=== C. per-shot meta 与 h3_comfy 证据链同名同位置 ===")
    shots_dir = TMP / "shots"
    shots_dir.mkdir(parents=True, exist_ok=True)
    clip = shots_dir / "v5_test01.mp4"
    clip.write_bytes(FAKE_MP4)
    meta_path = fingerprint.meta_path_for(clip)
    print(f"  meta 路径: {meta_path.relative_to(TMP)}（h3_comfy 期望 "
          f"{h3_comfy.meta_path_for(clip).name}）")
    utils.write_json(meta_path, {
        "h3": True, "ok": True, "provider": "hq-local", "mode": "t2va+ref2va",
        "workflow": "05_ref2va_4step_sla.json", "prompt_id": "pid-r4-proof",
        "seed": 20261102, "duration": 4.0, "width": 1024, "height": 576, "fps": 24,
        "shot_id": "v5_test01", "template": "aa_test", "path": str(clip),
        fingerprint.SIG_KEY: "sig-proof",
    })
    loaded = h3_comfy.load_meta(clip)
    ok = h3_comfy.meta_ok(loaded)
    ids = h3_comfy.assert_unique_prompt_ids([clip])
    print(f"  h3_comfy.load_meta 命中={bool(loaded)} meta_ok={ok} prompt_ids={ids}")
    assert meta_path == h3_comfy.meta_path_for(clip)
    assert loaded and ok and ids == ["pid-r4-proof"]
    return {"load_meta": bool(loaded), "meta_ok": ok, "prompt_ids": ids}


def part_d() -> dict:
    print("\n=== D. 静态断言：命中分支只 return，不写 meta（签名不会落到旧图上） ===")
    src = (REPO / "pipeline" / "templates3d_hq.py").read_text(encoding="utf-8")
    start = src.index("def _generate_keyframe(")
    end = src.index("def log(message")
    body = src[start:end]
    hit_idx = body.index("if (_cache_hit(key_png, cached_sig")
    first_return = body.index("return key_png", hit_idx)
    write_calls = [i for i in (body.find("_render_meta(", 0),
                               body.find("meta_path.write_text", 0)) if i != -1]
    early_writes = [i for i in write_calls if i < first_return]
    print(f"  命中分支前写 meta 的位置: {early_writes}（应为空）")
    print(f"  _render 内是否还有旧的体积短路: "
          f"{'st_size > 100_000' in body}")
    assert not early_writes
    assert "and dest.stat().st_size > 100_000" not in body.replace(
        "min_bytes=100_000", "")
    return {"early_writes": early_writes, "size_shortcut_removed": True}


def main() -> int:
    renderer = _Renderer()
    renderer.install()
    out = {"tmp": str(TMP), "a": part_a(renderer), "b": part_b(),
           "c": part_c(), "d": part_d()}
    print("\n" + json.dumps(out, ensure_ascii=False, indent=1))
    print("\n结论：改前（体积判据）命中、改后（指纹判据）未命中 —— P1-1/P1-2/P1-4 已修")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
