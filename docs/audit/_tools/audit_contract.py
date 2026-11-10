# -*- coding: utf-8 -*-
"""体检脚本 A：模板清单三处一致性 + 资源引用 + JSON 合法性 + 交叉引用（只读）。

用法： python docs/audit/_tools/audit_contract.py
输出： docs/audit/_tools/contract_audit.json（+ 控制台摘要）

口径：
- templates_v3      = contract/templates_v3/*.json（v3 历史层）
- templates3d       = contract/v5/cinema3d/templates3d/*.json，去掉 *_semantic 后缀与 .bak
- workflows         = contract/v6/workflows/*.json（V6 编排）
- runtime_catalog   = contract/v5/templates + contract/v5/custom/templates（v5_catalog.load_catalog 的真实注册表）
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve()
REPO = next(p for p in HERE.parents if (p / "contract").is_dir() and (p / "pipeline").is_dir())
OUT = HERE.parent / "contract_audit.json"

sys.path.insert(0, str(REPO))


def read_json(p: Path):
    try:
        return json.loads(p.read_text(encoding="utf-8-sig")), None
    except Exception as exc:  # noqa: BLE001
        return None, f"{type(exc).__name__}: {exc}"


def stems(d: Path, *, strip_semantic: bool = False) -> dict[str, Path]:
    out: dict[str, Path] = {}
    if not d.is_dir():
        return out
    for p in sorted(d.glob("*.json")):
        stem = p.stem
        if strip_semantic and stem.endswith("_semantic"):
            stem = stem[: -len("_semantic")]
        out[stem] = p
    return out


S1 = stems(REPO / "contract" / "templates_v3")
S2 = stems(REPO / "contract" / "v5" / "cinema3d" / "templates3d", strip_semantic=True)
S2_RAW = stems(REPO / "contract" / "v5" / "cinema3d" / "templates3d")
S3 = stems(REPO / "contract" / "v6" / "workflows")
S4: dict[str, Path] = {}
S4.update(stems(REPO / "contract" / "v5" / "templates"))
S4.update(stems(REPO / "contract" / "v5" / "custom" / "templates"))
S2_HAS_SEMANTIC = {k for k in S2 if f"{k}_semantic" in S2_RAW}

RESULT: dict = {
    "counts": {"templates_v3": len(S1), "templates3d_ids": len(S2),
               "templates3d_files": len(S2_RAW), "workflows": len(S3),
               "runtime_catalog_templates": len(S4)},
    "inventory": {}, "resources": [], "json_files": {}, "crossref": {},
}

inv = {k: sorted(v) for k, v in (("templates_v3", S1), ("templates3d", S2),
                                 ("workflows", S3), ("runtime_catalog", S4))}
RESULT["inventory"] = {
    "sets": inv,
    "only_in_templates_v3_vs_workflows": sorted(set(S1) - set(S3)),
    "only_in_templates3d_vs_workflows": sorted(set(S2) - set(S3)),
    "only_in_workflows_vs_templates3d": sorted(set(S3) - set(S2)),
    "workflows_without_runtime_template": sorted(set(S3) - set(S4)),
    "runtime_templates_without_workflow": sorted(set(S4) - set(S3)),
    "runtime_templates_without_semantic_brief": sorted(
        t for t in S4 if str((read_json(S4[t])[0] or {}).get("render_style") or "").strip()
        and not (REPO / "contract/v5/cinema3d/templates3d" / f"{t}_semantic.json").is_file()),
}

try:
    from pipeline import templates3d_hq as hq  # noqa: PLC0415

    RENDER_STYLES = set(hq.STYLE_2D_STYLES)
except Exception as exc:  # noqa: BLE001
    RENDER_STYLES, RESULT["render_style_import_error"] = set(), f"{type(exc).__name__}: {exc}"

try:
    from pipeline.agents.registry import TOOLS  # noqa: PLC0415
    from pipeline.agents.workflow import is_shorts  # noqa: PLC0415

    TOOL_NAMES = set(TOOLS)
except Exception as exc:  # noqa: BLE001
    TOOL_NAMES, RESULT["tools_import_error"] = set(), f"{type(exc).__name__}: {exc}"


def _dir_ids(*paths: str) -> set[str]:
    out: set[str] = set()
    for rel in paths:
        out |= {p.stem for p in (REPO / rel).glob("*.json")}
    return out


styles = _dir_ids("contract/v5/styles", "contract/v5/custom/styles")
scenes = _dir_ids("contract/v5/scenes", "contract/v5/custom/scenes")
chars = _dir_ids("contract/v5/characters", "contract/v5/custom/characters")
voices_data, _ = read_json(REPO / "contract/v5/voices.json")
VOICES = set((voices_data or {}).get("presets") or {})
cv, _ = read_json(REPO / "contract/v5/custom/voices.json")
VOICES |= set((cv or {}).get("presets") or {})
SCENE_MAP = read_json(REPO / "contract/scene_map.json")[0] or {}
SCENE_DESC = read_json(REPO / "contract/scene_descriptions.json")[0] or {}

RESULT["registries"] = {
    "styles": len(styles), "scenes": len(scenes), "characters": len(chars),
    "voices": len(VOICES), "render_styles": sorted(RENDER_STYLES),
    "scene_map_keys": len(SCENE_MAP), "scene_desc_keys": len(SCENE_DESC),
}

missing: list[dict] = []
for tid, path in sorted(S4.items()):
    data, err = read_json(path)
    if err:
        missing.append({"scope": "template", "id": tid, "what": "JSON 解析", "detail": err})
        continue
    tpl = data or {}
    style = tpl.get("style")
    if style and style not in styles:
        missing.append({"scope": "template", "id": tid, "what": "style 未注册", "detail": style})
    rs = str(tpl.get("render_style") or "").strip()
    if rs and rs not in RENDER_STYLES:
        missing.append({"scope": "template", "id": tid, "what": "render_style 未注册",
                        "detail": f"{rs}（注册表 {sorted(RENDER_STYLES)}）"})
    for c in tpl.get("cast") or []:
        if not isinstance(c, dict):
            continue
        cid = str(c.get("character") or "")
        if not cid:
            missing.append({"scope": "template", "id": tid, "what": "cast 空 character",
                            "detail": c.get("role")})
            continue
        if cid not in chars:
            missing.append({"scope": "template", "id": tid, "what": "cast 角色合同缺失",
                            "detail": cid})
        adir = REPO / "assets" / "v5" / "characters" / cid
        for need in ("meta.json", "portrait.png"):
            if not (adir / need).is_file():
                missing.append({"scope": "template", "id": tid,
                                "what": f"角色资产 {need} 缺失",
                                "detail": f"assets/v5/characters/{cid}/{need}"})
        cdata = read_json(REPO / "contract/v5/characters" / f"{cid}.json")[0]
        if isinstance(cdata, dict):
            vid = (cdata.get("voice") or {}).get("voice_id")
            if vid and vid not in VOICES:
                missing.append({"scope": "character", "id": cid, "what": "voice_id 未注册",
                                "detail": vid})
    for act in tpl.get("acts") or []:
        if not isinstance(act, dict):
            continue
        sc = str(act.get("scene") or "")
        if not sc:
            missing.append({"scope": "template", "id": tid, "what": "act 缺 scene",
                            "detail": act.get("id")})
            continue
        if sc not in scenes:
            missing.append({"scope": "template", "id": tid, "what": "act.scene 场景合同缺失",
                            "detail": sc})
        if not (REPO / "assets" / "v5" / "scenes" / f"{sc}.png").is_file():
            missing.append({"scope": "template", "id": tid, "what": "场景 plate 缺失",
                            "detail": f"assets/v5/scenes/{sc}.png"})
        if sc not in SCENE_MAP:
            missing.append({"scope": "template", "id": tid, "what": "scene 不在 scene_map",
                            "detail": sc})
        if sc not in SCENE_DESC:
            missing.append({"scope": "template", "id": tid,
                            "what": "scene 不在 scene_descriptions", "detail": sc})

_uniq = {(i["scope"], i["id"], i["what"], str(i["detail"])): i for i in missing}
RESULT["resources"] = sorted(_uniq.values(), key=lambda x: (x["what"], x["id"], str(x["detail"])))
_sum: dict[str, list] = {}
for item in RESULT["resources"]:
    _sum.setdefault(item["what"], []).append(item["id"])
RESULT["resource_summary"] = {k: {"count": len(set(v)), "ids": sorted(set(v))}
                             for k, v in _sum.items()}

v3_missing = []
for tid, path in sorted(S1.items()):
    data, err = read_json(path)
    if err:
        v3_missing.append({"id": tid, "what": "JSON 解析", "detail": err})
        continue
    plate = ((data or {}).get("stage") or {}).get("plate") or ""
    if plate and not (REPO / "outputs" / "scene_plates" / f"{plate}.png").is_file():
        v3_missing.append({"id": tid, "what": "stage.plate 图片缺失",
                           "detail": f"outputs/scene_plates/{plate}.png"})
RESULT["templates_v3_resources"] = v3_missing

REQUIRED = {"templates": ("id", "label"), "styles": ("id", "label"),
            "scenes": ("id",), "characters": ("id",), "series": ("id", "members")}
bad_json, empty_files, missing_fields = [], [], []
all_json = sorted((REPO / "contract").rglob("*.json"))
for p in all_json:
    rel = p.relative_to(REPO).as_posix()
    raw = p.read_text(encoding="utf-8-sig", errors="replace")
    if not raw.strip():
        empty_files.append({"path": rel, "why": "空白文件"})
        continue
    data, err = read_json(p)
    if err:
        bad_json.append({"path": rel, "error": err})
        continue
    if data in ({}, []):
        empty_files.append({"path": rel, "why": "内容为 {} / []"})
        continue
    if "workflows" in p.parts:
        for f in ("id", "version", "stages", "stack"):
            if not (data or {}).get(f):
                missing_fields.append({"path": rel, "missing": f})
        stages = [s for s in (data or {}).get("stages") or [] if isinstance(s, dict)]
        sids = [str(s.get("id")) for s in stages]
        if len(set(sids)) != len(sids):
            missing_fields.append({"path": rel, "missing": "stage id 重复"})
        for s in stages:
            for dep in s.get("depends") or []:
                if dep not in sids:
                    missing_fields.append({"path": rel,
                                           "missing": f"{s.get('id')} 依赖不存在 {dep}"})
            tool = s.get("tool")
            if tool and tool not in TOOL_NAMES:
                missing_fields.append({"path": rel, "missing": f"未知 tool {tool}"})
    elif p.parent.name in REQUIRED:
        for f in REQUIRED[p.parent.name]:
            if not (data or {}).get(f):
                missing_fields.append({"path": rel, "missing": f})

RESULT["json_files"] = {"total": len(all_json), "bad_json": bad_json,
                        "empty": empty_files, "missing_fields": missing_fields}

cross: dict = {"series": [], "stack_mismatch": [], "brief_duration": []}
for p in sorted((REPO / "contract" / "v5" / "series").glob("*.json")):
    data = read_json(p)[0] or {}
    for m in data.get("members") or []:
        mid = m.get("template") if isinstance(m, dict) else m
        if mid not in S4:
            cross["series"].append({"series": p.stem, "missing_template": mid})
    if data.get("cover") and data["cover"] not in S4:
        cross["series"].append({"series": p.stem, "missing_cover": data["cover"]})

for tid, path in sorted(S3.items()):
    wf = read_json(path)[0] or {}
    tpl = read_json(S4[tid])[0] if tid in S4 else {}
    want = "hq_shorts" if is_shorts(tid, tpl or {}) else "v5_render"
    if wf.get("stack") != want:
        cross["stack_mismatch"].append({"template": tid, "workflow_stack": wf.get("stack"),
                                        "expected": want})

for tid in sorted(S4):
    tpl = read_json(S4[tid])[0] or {}
    bp = REPO / "contract/v5/cinema3d/templates3d" / f"{tid}_semantic.json"
    if not bp.is_file():
        cross["brief_duration"].append({"template": tid, "why": "缺语义 brief"})
        continue
    brief = read_json(bp)[0] or {}
    shots = [s for s in brief.get("shots") or [] if isinstance(s, dict)]
    bdur = round(sum(float(s.get("duration") or 0) for s in shots), 2)
    tdur = float(tpl.get("target_duration") or 0)
    cross["brief_duration"].append({
        "template": tid, "render_style": str(tpl.get("render_style") or ""),
        "acts": len(tpl.get("acts") or []), "brief_shots": len(shots),
        "brief_duration": bdur, "target_duration": tdur,
        "ratio": round(bdur / tdur, 2) if tdur else None,
    })
RESULT["crossref"] = cross

OUT.write_text(json.dumps(RESULT, ensure_ascii=False, indent=2), encoding="utf-8")

print("== counts ==", json.dumps(RESULT["counts"], ensure_ascii=False))
print("== registries ==", json.dumps(RESULT["registries"], ensure_ascii=False))
print("\n== 清单差异 ==")
for k in ("only_in_templates_v3_vs_workflows", "only_in_templates3d_vs_workflows",
          "only_in_workflows_vs_templates3d", "workflows_without_runtime_template",
          "runtime_templates_without_workflow", "runtime_templates_without_semantic_brief"):
    print(f"  {k}: {RESULT['inventory'][k]}")
print("\n== 资源引用汇总（去重后 %d 条）==" % len(RESULT["resources"]))
for what, info in sorted(RESULT["resource_summary"].items()):
    print(f"  {what}: {info['count']} 个模板 → {info['ids']}")
print("\n== templates_v3 stage.plate ==", len(v3_missing), "条缺失")
print("\n== JSON 体检 == total", RESULT["json_files"]["total"],
      "bad", len(bad_json), "empty", len(empty_files), "missing", len(missing_fields))
for item in missing_fields[:10]:
    print("  ", item)
print("\n== 交叉引用 ==")
print("  series:", cross["series"])
print("  stack_mismatch:", cross["stack_mismatch"])
print("  brief 时长比（>3 视为与模板 target_duration 严重不符）:")
for item in cross["brief_duration"]:
    if item.get("ratio") and item["ratio"] > 3:
        print("   ", json.dumps(item, ensure_ascii=False))
print("\nwrote", OUT)
