"""chrome 噪音过滤（第四阶段 Task 4，通用能力）。

SpiderMonkey script enter/exit 事件会记录浏览器自身模块的脚本执行
（`resource://gre/modules/*.sys.mjs`、`chrome://`、`about:`），
对目标站点逆向是纯噪音。raw trace 只追加不修改，本模块产出 derived
过滤视图，并返回自描述的统计（总量 / 保留 / 按前缀过滤数）。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable

NOISE_PREFIXES: tuple[str, ...] = ("resource://", "chrome://", "about:")


def is_noise(event: dict[str, Any]) -> bool:
    """事件是否属于浏览器内部噪音（按 p 字段前缀判定）。"""
    target = event.get("p")
    if not isinstance(target, str):
        return False
    return target.startswith(NOISE_PREFIXES)


def filter_lines(lines: Iterable[str]) -> tuple[list[str], dict[str, Any]]:
    """过滤 jsonl 行序列，返回 (保留行, 统计)。

    无法解析的行原样保留并计入 ``unparseable``——宁可多留也不错删。
    """
    kept: list[str] = []
    stats: dict[str, Any] = {
        "total": 0,
        "kept": 0,
        "filtered": 0,
        "unparseable": 0,
        "by_prefix": {},
    }
    for line in lines:
        stats["total"] += 1
        try:
            event = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            stats["unparseable"] += 1
            stats["kept"] += 1
            kept.append(line)
            continue
        if isinstance(event, dict) and is_noise(event):
            stats["filtered"] += 1
            for prefix in NOISE_PREFIXES:
                if event["p"].startswith(prefix):
                    stats["by_prefix"][prefix] = (
                        stats["by_prefix"].get(prefix, 0) + 1)
                    break
            continue
        stats["kept"] += 1
        kept.append(line)
    return kept, stats


def filter_trace(source: str | Path, destination: str | Path) -> dict[str, Any]:
    """把 source jsonl 过滤后写入 destination（derived 视图），返回统计。

    source 只读不改；destination 会被整体覆盖。
    """
    source_path = Path(source)
    destination_path = Path(destination)
    lines = source_path.read_text(encoding="utf-8").splitlines()
    kept, stats = filter_lines(lines)
    destination_path.parent.mkdir(parents=True, exist_ok=True)
    text = "\n".join(kept)
    if kept:
        text += "\n"
    destination_path.write_text(text, encoding="utf-8")
    stats["source"] = str(source_path)
    stats["destination"] = str(destination_path)
    return stats
