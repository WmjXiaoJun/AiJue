"""造片局 Pipeline CLI 入口。

用法（仓库根目录）：
  python -m pipeline.cli gen-shot --spec <shot_spec.json> --out <clip.mp4> [--mock]
  python -m pipeline.cli tts --text "..." --voice zh-CN-YunxiNeural --out <a.mp3> [--vtt <a.vtt>]
  python -m pipeline.cli render --episode <episode.json> --workdir <dir> --out <final.mp4>

约定：stdout 最后一行是 JSON 结果；日志走 stderr；失败 exit code != 0。
"""
from __future__ import annotations

import argparse
import os
import sys
import traceback
from pathlib import Path

from . import __version__, config, utils

LOG = utils.LOG


def _scratch_for_out(out: Path) -> Path:
    """gen-shot 的临时目录（字体 + textfile）。

    默认放系统临时目录的共享子目录，避免每个输出目录复制字体；
    可用 PIPELINE_SCRATCH_DIR 覆盖。
    """
    override = os.environ.get("PIPELINE_SCRATCH_DIR")
    if override:
        return utils.ensure_dir(override)
    import tempfile
    return utils.ensure_dir(Path(tempfile.gettempdir()) / "zaopianju_pipeline" / "gen_shot")


def cmd_gen_shot(args: argparse.Namespace) -> int:
    spec = utils.read_json(args.spec)
    if spec is None:
        utils.emit_json_line({"ok": False, "error": f"无法读取 spec: {args.spec}"})
        return 1
    out = Path(args.out)
    if not out.is_absolute():
        out = Path.cwd() / out
    mode = "mock" if args.mock else str(spec.get("mode") or "mock").lower()
    duration = float((spec.get("shot") or {}).get("duration") or 5.0)
    try:
        if mode == "comfyui":
            from .comfyui import generate_comfyui_clip
            result = generate_comfyui_clip(spec, out)
        else:
            result = mock_shot_generate(spec, out, duration)
        utils.emit_json_line(result)
        return 0
    except Exception as exc:  # noqa: BLE001
        LOG.error("gen-shot 失败: %s", exc)
        LOG.debug("%s", traceback.format_exc())
        utils.emit_json_line({"ok": False, "error": str(exc), "provider": mode})
        return 1


def mock_shot_generate(spec: dict, out: Path, duration: float) -> dict:
    from . import mock_shot
    scratch = _scratch_for_out(out)
    result = mock_shot.generate_mock_clip(spec, out, scratch)
    result["duration"] = result.get("duration") or duration
    return result


def cmd_tts(args: argparse.Namespace) -> int:
    from . import tts_engine
    out = Path(args.out)
    if not out.is_absolute():
        out = Path.cwd() / out
    try:
        result = tts_engine.synth_with_retry(
            args.text, args.voice, out,
            Path(args.vtt) if args.vtt else None,
            rate=args.rate,
        )
        utils.emit_json_line(result)
        return 0
    except Exception as exc:  # noqa: BLE001
        LOG.error("tts 失败: %s", exc)
        utils.emit_json_line({"ok": False, "error": str(exc), "voice": args.voice})
        return 1


def cmd_render(args: argparse.Namespace) -> int:
    from . import render as render_mod
    return render_mod.main(args)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m pipeline.cli",
        description="造片局 · 视频流水线 CLI（mock 画面 / edge-tts / ffmpeg 合成）",
    )
    parser.add_argument("--version", action="version", version=__version__)
    parser.add_argument("-v", "--verbose", action="store_true", help="调试日志")
    sub = parser.add_subparsers(dest="command", required=True)

    p_shot = sub.add_parser("gen-shot", help="生成单镜头片段（mock/comfyui）")
    p_shot.add_argument("--spec", required=True, help="shot_spec.json 路径")
    p_shot.add_argument("--out", required=True, help="输出 mp4 路径")
    p_shot.add_argument("--mock", action="store_true", help="强制 mock 模式")
    p_shot.set_defaults(func=cmd_gen_shot)

    p_tts = sub.add_parser("tts", help="edge-tts 合成单条台词")
    p_tts.add_argument("--text", required=True)
    p_tts.add_argument("--voice", default=config.DEFAULT_VOICE)
    p_tts.add_argument("--out", required=True, help="输出 mp3")
    p_tts.add_argument("--vtt", default=None, help="输出词级 vtt")
    p_tts.add_argument("--rate", default=None, help="语速，如 +6%%")
    p_tts.set_defaults(func=cmd_tts)

    p_render = sub.add_parser("render", help="把 episode 渲染为成片 mp4")
    p_render.add_argument("--episode", required=True, help="episode.json")
    p_render.add_argument("--workdir", required=True, help="中间产物目录")
    p_render.add_argument("--out", required=True, help="输出成片路径")
    p_render.add_argument("--resolution", default=None, help="默认取 episode，如 1280x720")
    p_render.add_argument("--fps", type=int, default=None)
    p_render.add_argument("--no-bgm", action="store_true", help="不加背景音乐")
    p_render.add_argument("--limit", type=int, default=None,
                          help="只渲染前 N 个镜头（调试用）")
    p_render.add_argument("--force", action="store_true", help="忽略缓存重新生成")
    p_render.add_argument("--use-h3", action="store_true", default=False,
                          help="尝试用 outputs/h3/<sid>.mp4 的真实 H3 镜头替换对应镜头（默认关）")
    p_render.add_argument("--dynamic", action="store_true", default=False,
                          help="动态镜头模式：每镜调 H3 ref CLI 生成音驱动/首尾帧动态 clip")
    p_render.add_argument("--dynamic-fallback-static", action="store_true", default=False,
                          help="H3 CLI 不可用时回退静态 clips（调试用）")
    p_render.add_argument("--h3-ref-cli", default=None,
                          help="H3 动态 CLI 路径（默认 integrations/comfyui/run_h3_ref.py）")
    p_render.add_argument("--transitions", default="cut", choices=["cut", "xfade"],
                          help="镜头拼接方式：cut 硬切（默认）/ xfade 转场")
    p_render.add_argument("--transition-style", default="fade",
                          choices=sorted(__import__("pipeline.render", fromlist=["TRANSITION_PRESETS"]).TRANSITION_PRESETS),
                          help="xfade 转场预设")
    p_render.add_argument("--transition-duration", type=float, default=0.30,
                          help="xfade 转场时长（秒，建议 0.2-0.4）")
    p_render.add_argument("--scenes", action="store_true", default=False,
                          help="场景模式：先用 contract/scene_map.json 合成 2D 绘景+角色分层镜头再渲染")
    p_render.add_argument("--scene-template", default=None,
                          help="scene_map 模板 id（默认取 episode.format_id/id）")
    p_render.add_argument("--style", default=None, choices=["3d_anime", "2d_anime"],
                          help="覆盖风格（默认按 contract/style_map.json 的模板映射）")
    p_render.add_argument("--style-profile", default=None,
                          help="style_v2 风格 profile：auto 按模板映射，或 preset 名/模板 id（contract/style_presets.json）")
    p_render.set_defaults(func=cmd_render)
    return parser


def _force_utf8_stdio() -> None:
    """子进程被后端捕获时统一 UTF-8，避免 Windows 代码页把中文 JSON 写坏。"""
    for stream_name in ("stdout", "stderr"):
        stream = getattr(sys, stream_name, None)
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass


def main(argv: list[str] | None = None) -> int:
    _force_utf8_stdio()
    parser = build_parser()
    args = parser.parse_args(argv)
    utils.setup_logging(bool(getattr(args, "verbose", False)))
    try:
        return int(args.func(args))
    except KeyboardInterrupt:
        LOG.error("已中断")
        return 130
    except Exception as exc:  # noqa: BLE001
        LOG.error("执行失败: %s", exc)
        LOG.debug("%s", traceback.format_exc())
        utils.emit_json_line({"ok": False, "error": str(exc)})
        return 1


if __name__ == "__main__":
    sys.exit(main())
