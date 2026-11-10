# -*- coding: utf-8 -*-
"""统一 id 校验 + 路径 containment —— 全仓唯一来源。

对应审查条目：`docs/audit/REVIEW_ROUND3.md` P1-1（三处路径穿越）、P1-3（`/media` 与 `ID_RE`
边界不一致）、P2-2（绝对路径外泄）。

为什么要集中（两条都是实测缺陷，不是理论风险）：

1) **反斜杠穿越**：三处 `base / f"{ident}.json"` 只挡了 `/`，没挡 `\\`。Starlette 路由参数
   正则是 `[^/]+`，`%5C` 解码后就是 `\\`，而 `\\` 在 Windows 上是合法分隔符：

       GET /api/v6/agent/season/..%5C..%5C..%5Ccontract%5Cdemo_episode/status   -> 200 + 文件内容

2) **两套 id 边界**：`/media/agent_runs/{run_id}/{file}` 当初写成 `^[a-z0-9_]{8,40}$`，
   而合同 id 用 `ID_RE ^[a-z][a-z0-9_]{1,60}$`。46 字符的 run_id
   （`20261106_233032_custom_tpl_v45_soft_watercolor`）证据 **永久 404**。

因此：id 规则在这里只定义一次。入口先 :func:`check_id` / :func:`check_run_id`（白名单），
再用 :func:`resolve_under` 做 containment 兜底（防 Windows 反斜杠、大小写、短文件名等边角）。
**不要**在别处再写一套正则：两套边界就是 P1-3 的根因。
"""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, Union

from fastapi import HTTPException

from . import config

# 合同 id（模板 / 系列 / 节目格式 / 角色）：小写字母开头，2–61 位小写字母/数字/下划线。
SAFE_ID = re.compile(r"^[a-z][a-z0-9_]{1,60}$")

# 运行/证据目录名（run_id、`season_<sid>_<ts>`）：**必须与 SAFE_ID 同源**。
# 同一模块、同一字符集 {a-z0-9_}，只放宽两处边界：
#   * 允许前导数字 —— run_id = `%Y%m%d_%H%M%S` + "_" + 模板 id；
#   * 长度 1..80 —— SAFE_ID 是 2..61，46 字符 run_id 的 run.json/summary.md 必须能取到。
# 刻意**不允许 `.`**：否则 `..` 又能当穿越；也不允许 `-`/空格，避免与 URL 编码纠缠。
# 改动任何一个都要同时看另一个（P1-3 的根因就是这里分裂成了两套边界）。
RUN_ID_RE = re.compile(r"^[a-z0-9_]{1,80}$")


def _reject(what: str, value: Any, hint: str) -> HTTPException:
    return HTTPException(status_code=400, detail=f"非法 {what}: {value!r}（{hint}）")


def check_id(value: Any, what: str = "id") -> str:
    """严格 id 校验；不匹配 :data:`SAFE_ID` 抛 ``HTTPException(400)``，返回 strip 后的字符串。"""
    text = str(value or "").strip()
    if not SAFE_ID.match(text):
        raise _reject(what, value, "只允许小写字母开头、小写字母/数字/下划线、2-61 位")
    return text


def check_run_id(value: Any, what: str = "run_id") -> str:
    """运行/证据目录名校验；不匹配 :data:`RUN_ID_RE` 抛 ``HTTPException(400)``。"""
    text = str(value or "").strip()
    if not RUN_ID_RE.match(text):
        raise _reject(what, value, "只允许小写字母/数字/下划线、1-80 位（不允许点号）")
    return text


def is_inside(root: Union[Path, str], target: Union[Path, str]) -> bool:
    """target 是否落在 root 之内（按 `os.path.commonpath` 判定，Windows 上大小写不敏感）。"""
    base = os.path.normcase(os.path.abspath(str(root)))
    candidate = os.path.normcase(os.path.abspath(str(target)))
    try:
        return os.path.commonpath([base, candidate]) == base
    except ValueError:  # 不同盘符 -> commonpath 直接抛错，就是越界
        return False


def resolve_under(root: Union[Path, str], relative: Any, what: str = "路径") -> Path:
    """把相对路径钉在 ``root`` 之内，返回 ``resolve()`` 后的绝对路径。

    - ``relative`` 既可以是 `/media/...` 之外的相对片段，也可以是仓库相对/绝对路径（绝对路径
      直接参与 containment 判定，越界同样拒绝）；
    - 越界、非法的空路径、解析失败统一抛 ``HTTPException(400)``（不是 404：这是调用方传错参数，
      与"文件不存在"必须区分，前端据此提示而不会静默 404）。

    先白名单（``check_id``）再 containment 是刻意的双保险：白名单挡住 `..`/`\\`/`:` 这类
    构造，containment 兜住大小写、短文件名、`\\\\?\\` 前缀等 Windows 边角。
    """
    text = str(relative or "").strip()
    if not text:
        raise _reject(what, relative, "路径不能为空")
    base = Path(root).resolve()
    candidate = Path(text)
    target = candidate if candidate.is_absolute() else base / text
    try:
        resolved = target.resolve()
    except (OSError, RuntimeError, ValueError) as exc:  # 非法盘符 / 循环链接 / 过长路径
        raise _reject(what, relative, f"路径无法解析: {exc}") from exc
    if not is_inside(base, resolved):
        raise _reject(what, relative, f"越出允许目录 {base.name}")
    return resolved


def rel_to_repo(path: Any) -> str:
    """仓库内绝对路径 -> 仓库相对 posix 路径（对外只暴露这个，绝不回吐盘符/用户名）。

    非绝对路径或不在仓库内时原样返回（斜杠统一成 `/`，前端当 URL 片段用也安全）。
    """
    text = str(path or "").strip()
    if not text:
        return ""
    candidate = Path(text)
    if not candidate.is_absolute():
        return text.replace("\\", "/")
    root = config.REPO_ROOT.resolve()
    try:
        return candidate.resolve().relative_to(root).as_posix()
    except (ValueError, OSError):  # 不在仓库内（如临时目录）：退回文件名，不泄露绝对路径
        try:
            return candidate.name
        except OSError:
            return ""


def as_bool(value: Any, default: bool = False) -> bool:
    """严格布尔：`"false" / "0" / "no" / "off" / ""` -> False（避免 ``bool("false") == True``）。"""
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    if isinstance(value, (int, float)):
        return bool(value)
    text = str(value).strip().lower()
    if not text:
        return False
    return text in ("1", "true", "yes", "y", "on")


def env_flag(name: str, default: bool = True) -> bool:
    """读环境变量开关：`0/false/no/off/空` -> False，未设置 -> default。"""
    raw = os.environ.get(name)
    if raw is None or not str(raw).strip():
        return default
    return as_bool(raw, default)
