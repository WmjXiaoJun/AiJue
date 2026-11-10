# -*- coding: utf-8 -*-
"""R5 回归：视频模型优先级（Muse → 本地 ComfyUI → 其它）+ LLM 剧本读取路径。

背景：
- 用户口径「视频模型第一优先 Muse，其次本地 ComfyUI，最后其它」——以前没有统一策略，
  `muse_video.available()` 还会把 metaso 这类"其它云端"算进 Muse；
- `v5_story.load_script` 旧实现只读 `<repo>/llm_scripts/`（不存在），DeepSeek 生成的
  `outputs/llm_scripts/*.json` **永远进不了 V5 渲染**。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from pipeline import config, v5_story, video_provider  # noqa: E402


# ---------------- 视频模型优先级 ----------------

def test_default_order_is_muse_first(monkeypatch):
    monkeypatch.delenv("ZAOPIANJU_VIDEO_PROVIDER_ORDER", raising=False)
    assert video_provider.provider_order() == ["muse", "comfyui_local", "other"]


def test_env_can_reorder_and_unknown_is_ignored(monkeypatch):
    monkeypatch.setenv("ZAOPIANJU_VIDEO_PROVIDER_ORDER", "comfyui_local, muse ,aliens")
    assert video_provider.provider_order() == ["comfyui_local", "muse", "other"]
    monkeypatch.setenv("ZAOPIANJU_VIDEO_PROVIDER_ORDER", "other")
    # 没点到的类别排在后面，保证 resolve 仍有兜底
    assert video_provider.provider_order() == ["other", "muse", "comfyui_local"]


def test_status_shape_and_label(monkeypatch):
    monkeypatch.delenv("ZAOPIANJU_VIDEO_PROVIDER_ORDER", raising=False)
    st = video_provider.status()
    assert set(("ok", "order", "chosen", "chosen_label", "reason", "muse",
                "comfyui_local", "other")) <= set(st)
    assert st["order"] == ["muse", "comfyui_local", "other"]
    # chosen 必须是 order 里第一个 available 的类别（没配置就是不选）
    first_available = next((n for n in st["order"]
                            if ({"muse": st["muse"]["ok"],
                                 "comfyui_local": st["comfyui_local"]["available"],
                                 "other": st["other"]["available"]})[n]), "")
    assert st["chosen"] == first_available
    assert st["chosen_label"] in ("Muse", "本地 ComfyUI", "第三方云端", "不可用")


def test_muse_bucket_excludes_other_clouds():
    """metaso/minimax 这类"其它云端"不能把 Muse 顶成可用（否则优先级撒谎）。"""
    st = video_provider.status()
    configured = st["muse"]["video"].get("configured") or []
    assert all(name in video_provider.MUSE_FAMILY for name in configured), configured


# ---------------- LLM 剧本路径 ----------------

@pytest.fixture()
def fake_roots(tmp_path, monkeypatch):
    outputs = tmp_path / "outputs"
    repo = tmp_path / "repo"
    (outputs / "llm_scripts").mkdir(parents=True)
    repo.mkdir()
    monkeypatch.setattr(config, "OUTPUTS_ROOT", outputs)
    monkeypatch.setattr(config, "REPO_ROOT", repo)
    return {"outputs": outputs, "repo": repo, "scripts": outputs / "llm_scripts"}


def _write(path: Path, text: str, mtime: float) -> None:
    path.write_text(json.dumps({"title": text, "lines": [{"speaker": "host", "text": text}]},
                               ensure_ascii=False), encoding="utf-8")
    import os
    os.utime(path, (mtime, mtime))


def test_load_script_reads_outputs_dir(fake_roots):
    _write(fake_roots["scripts"] / "tonight_show.json", "来自 DeepSeek 的台本", 1_700_000_000)
    data = v5_story.load_script("tonight_show")
    assert data.get("title") == "来自 DeepSeek 的台本", data


def test_load_script_prefers_newest_and_supports_suffix(fake_roots):
    _write(fake_roots["scripts"] / "tonight_show.json", "旧", 1_700_000_000)
    _write(fake_roots["scripts"] / "tonight_show_5min.json", "新", 1_800_000_000)
    assert v5_story.load_script("tonight_show_5min").get("title") == "新"
    # 查 tonight_show 时也应命中更新的 _5min（同一个模板的不同版本）
    assert v5_story.load_script("tonight_show").get("title") == "新"


def test_load_script_repo_dir_still_works(fake_roots):
    legacy = fake_roots["repo"] / "llm_scripts"
    legacy.mkdir()
    _write(legacy / "classics.json", "旧目录也能读", 1_700_000_000)
    assert v5_story.load_script("classics").get("title") == "旧目录也能读"


def test_explicit_script_wins(fake_roots):
    _write(fake_roots["scripts"] / "tonight_show.json", "文件里的", 1_700_000_000)
    data = v5_story.load_script("tonight_show", script={"title": "显式传入"})
    assert data.get("title") == "显式传入"
