# -*- coding: utf-8 -*-
"""V45 2D 风格全流程自检：健康 → 目录 → 校验负例 → 建模板 → 分派命令 → 真生成 → 媒体播放 → 删除。

用法（先起后端，默认 8090，与 scripts/dev.ps1 一致）：
    python scripts/verify_v45_2d_flow.py
    set ZAOPIANJU_BASE=http://127.0.0.1:8090 && python scripts/verify_v45_2d_flow.py

会真实生成一条 4 幕 2D 样片（约 5–8 分钟，GPU），完成后删除模板与产物；
结果写入 tmp/v45_flow_check.json。
"""
from __future__ import annotations

import json
import pathlib
import shutil
import sys
import time
import urllib.error
import urllib.request

import os

BASE = os.environ.get("ZAOPIANJU_BASE", "http://127.0.0.1:8090").rstrip("/")
REPO = pathlib.Path(__file__).resolve().parents[1]
OUT = REPO / "tmp/v45_flow_check.json"
RESULTS: list[dict] = []
FID = "custom_tpl_v45_flowcheck"


def check(name: str, ok: bool, detail="") -> None:
    RESULTS.append({"check": name, "ok": bool(ok), "detail": detail})
    print(("PASS " if ok else "FAIL ") + name + ("  :: " + str(detail) if detail else ""), flush=True)


def call(method: str, path: str, body=None, timeout: float = 180.0):
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            ctype = resp.headers.get("content-type", "")
            return resp.status, (json.loads(raw.decode("utf-8")) if "json" in ctype else raw)
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        try:
            return exc.code, json.loads(raw.decode("utf-8"))
        except Exception:
            return exc.code, raw[:400]


def main() -> int:
    st, health = call("GET", "/api/health", timeout=20)
    check("health 200", st == 200 and health.get("status") == "ok", health)

    st, cat = call("GET", "/api/v5/catalog", timeout=60)
    tpls = {t.get("id"): t for t in ((cat or {}).get("templates") or []) if isinstance(t, dict)}
    want = {
        "custom_tpl_v45_jp_anime": "jp_anime",
        "custom_tpl_v45_guoman": "guoman",
        "custom_tpl_v45_retro_cel": "retro_cel",
        "custom_tpl_v45_soft_watercolor": "soft_watercolor",
    }
    check("catalog has 4 v45 templates", st == 200 and all(t in tpls for t in want),
          f"templates={len(tpls)}")
    for tid, style in want.items():
        item = tpls.get(tid) or {}
        label = item.get("render_style_label")
        ok = item.get("render_style") == style and bool(label)
        if style == "soft_watercolor":
            ok = ok and label == "吉卜力治愈系"
        check(f"{tid} render_style={style}", ok, f"label_ok={bool(label)}")

    st, body = call("POST", "/api/v5/templates", {
        "label": "V45 非法风格", "style": "neon_toon", "render_type": "2d",
        "render_style": "ghibli_wrong", "cast": [{"role": "host", "character": "laozhou"}],
        "acts": [{"scene": "neon_skyline", "camera": "dolly", "text": ""}],
    }, timeout=60)
    detail = json.dumps(body, ensure_ascii=False) if isinstance(body, dict) else str(body)[:300]
    check("invalid render_style -> 400", st == 400 and "render_style" in detail, f"{st}")

    payload = {
        "id": FID, "label": "V45 全流程验证·治愈", "style": "neon_toon",
        "render_type": "2d", "render_style": "soft_watercolor",
        "auto_generate": False, "dry_run": True,
        "cast": [
            {"role": "host", "character": "laozhou", "name": "老周"},
            {"role": "figure_a", "character": "ali", "name": "阿栗"},
        ],
        "acts": [
            {"scene": "neon_skyline", "camera": "dolly", "duration": 3.0, "text": ""},
            {"scene": "late_night_studio", "camera": "push_in", "speaker": "host",
             "text": "夜色入画，欢迎来到城市来信。"},
            {"scene": "cafe_window", "camera": "static", "speaker": "figure_a",
             "text": "窗外的灯，像一笔未干的水墨。"},
            {"scene": "rainy_street", "camera": "crane_up", "duration": 3.0, "text": ""},
        ],
    }
    st, created = call("POST", "/api/v5/templates", payload, timeout=600)
    created_ok = bool(isinstance(created, dict) and st in (200, 201)
                      and created.get("id") == FID
                      and created.get("render_style") == "soft_watercolor")
    check("create 2D soft_watercolor template", created_ok,
          f"{st} shots={created.get('shots') if isinstance(created, dict) else '?'} "
          f"audio={created.get('audio') if isinstance(created, dict) else '?'} "
          f"brief={created.get('brief') if isinstance(created, dict) else '?'}")
    contract = REPO / f"contract/v5/custom/templates/{FID}.json"
    episode = REPO / f"outputs/templates_v5/{FID}/work/episode.json"
    semantic = REPO / f"contract/v5/cinema3d/templates3d/{FID}_semantic.json"
    if created_ok:
        data = json.loads(contract.read_text(encoding="utf-8"))
        check("new template contract render_style", data.get("render_style") == "soft_watercolor",
              data.get("render_style"))
        check("episode.json generated", episode.is_file(), "ok" if episode.is_file() else "missing")
        check("semantic brief generated", semantic.is_file(), "ok" if semantic.is_file() else "missing")
        brief = json.loads(semantic.read_text(encoding="utf-8")) if semantic.is_file() else {}
        check("semantic brief 4 shots", len(brief.get("shots") or []) == 4,
              f"shots={len(brief.get('shots') or [])}")

    for tid, style in want.items():
        st, dry = call("POST", "/api/pipeline/run",
                       {"template_id": tid, "reuse_shots": True, "dry_run": True}, timeout=60)
        cmd = " ".join((dry or {}).get("command") or []) if isinstance(dry, dict) else ""
        check(f"pipeline dry-run {style}",
              st in (200, 202) and "--style-mode 2d" in cmd and f"--2d-style {style}" in cmd,
              f"http={st}")

    st, dry3d = call("POST", "/api/pipeline/run",
                     {"template_id": "tonight_show", "dry_run": True}, timeout=60)
    cmd3d = " ".join((dry3d or {}).get("command") or []) if isinstance(dry3d, dict) else ""
    check("3D template command no --2d-style",
          st in (200, 202) and "--style-mode 3d" in cmd3d and "--2d-style" not in cmd3d,
          f"http={st}")

    # 新模板真生成（不复用镜头）：完整 4 镜 T2VA/ref2va → 剪辑 → 同步 2D
    st, run = call("POST", "/api/pipeline/run", {"template_id": FID}, timeout=120)
    job_id = (run or {}).get("job_id")
    check("start real run on new template", st == 202 and bool(job_id), f"{st} job={job_id}")
    final_state = {}
    if job_id:
        deadline = time.time() + 1200
        last = None
        while time.time() < deadline:
            st, view = call("GET", f"/api/pipeline/run/{job_id}", timeout=30)
            status = (view or {}).get("status")
            if status != last:
                print("  .. job", status, (view or {}).get("stage"), (view or {}).get("percent"), flush=True)
                last = status
            if status in ("done", "failed", "cancelled"):
                final_state = view
                break
            time.sleep(8)
    check("new template run done", final_state.get("status") == "done",
          f"status={final_state.get('status')} err={final_state.get('error')}")
    check("run echoes soft_watercolor", final_state.get("render_style") == "soft_watercolor",
          final_state.get("render_style"))
    prog = final_state.get("progress") or {}
    check("run 4/4 shots", int(prog.get("done") or 0) == 4 and int(prog.get("total") or 0) == 4,
          json.dumps(prog, ensure_ascii=False))
    check("run final_url", bool(final_state.get("final_url")), final_state.get("final_url"))
    f3d = REPO / f"outputs/templates_3d/{FID}/final.mp4"
    f2d = REPO / f"outputs/templates_2d/{FID}/final.mp4"
    check("new final in templates_3d", f3d.is_file() and f3d.stat().st_size > 500_000,
          f"{f3d.stat().st_size if f3d.is_file() else 0} B")
    check("new final synced to templates_2d", f2d.is_file() and f2d.stat().st_size > 500_000,
          f"{f2d.stat().st_size if f2d.is_file() else 0} B")
    st, media = call("GET", f"/media/templates_2d/{FID}/final.mp4", timeout=120)
    check("new media playable", st == 200 and isinstance(media, bytes) and len(media) > 500_000,
          f"{st} {len(media) if isinstance(media, bytes) else '?'}")

    # 有关键帧/成片证据（V45 生成参数）
    kf = list((REPO / f"tmp/v16/keyframes/{FID}").rglob("v5_002.meta.json")) if (REPO / f"tmp/v16/keyframes/{FID}").is_dir() else []
    if kf:
        meta = json.loads(kf[0].read_text(encoding="utf-8"))
        check("keyframe meta records ckpt/ip/age",
              bool(meta.get("ckpt")) and "ip_strength" in meta and bool(meta.get("age_identity")),
              f"ckpt={meta.get('ckpt')} ip={meta.get('ip_strength')}->{meta.get('ip_end')} "
              f"age={meta.get('age')}")
        check("keyframe uses soft_watercolor ckpt",
              str(meta.get("ckpt") or "").startswith("AAM_XL_Anime_Mix"),
              meta.get("ckpt"))
    else:
        check("keyframe meta records ckpt/ip/age", False, "no keyframe meta (fallback path?)")

    st, html = call("GET", "/", timeout=30)
    text = html.decode("utf-8", "replace") if isinstance(html, bytes) else str(html)
    check("frontend index served", st == 200 and ("root" in text or "assets/" in text),
          f"{st} len={len(text)}")
    assets = sorted((REPO / "frontend/dist/assets").glob("*.js"))
    label_hit = any("吉卜力治愈系" in a.read_text(encoding="utf-8", errors="ignore") for a in assets)
    check("built assets contain ghibli label", label_hit, f"assets={len(assets)}")

    # 有终稿时不可删；移除产物后删除并核对清理
    st, blocked = call("DELETE", f"/api/v5/templates/{FID}", timeout=60)
    check("delete blocked when final exists", st == 400 and contract.is_file(), f"{st}")
    shutil.rmtree(REPO / f"outputs/templates_3d/{FID}", ignore_errors=True)
    shutil.rmtree(REPO / f"outputs/templates_2d/{FID}", ignore_errors=True)
    shutil.rmtree(REPO / f"outputs/templates_v5/{FID}", ignore_errors=True)
    shutil.rmtree(REPO / f"tmp/v16/keyframes/{FID}", ignore_errors=True)
    st, deleted = call("DELETE", f"/api/v5/templates/{FID}", timeout=60)
    check("delete template after artifacts removed",
          st in (200, 204) and isinstance(deleted, dict) and deleted.get("deleted"), f"{st}")
    check("contract removed", not contract.is_file())
    check("semantic removed", not semantic.is_file())

    total = len(RESULTS)
    passed = sum(1 for r in RESULTS if r["ok"])
    print(f"\nSUMMARY {passed}/{total} passed", flush=True)
    OUT.write_text(json.dumps({"passed": passed, "total": total, "results": RESULTS},
                              ensure_ascii=False, indent=2), encoding="utf-8")
    print("written:", OUT, flush=True)
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())