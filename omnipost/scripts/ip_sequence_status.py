#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""个人 IP 发布序列台账状态（只读，不回写台账）。

--status  列出日期目录、标题与登记/草稿状态
--next    读取序列表，找下一条「未写」；序列未填写时如实说明
--verify  检查日期目录与选题登记、序列已写行是否自洽
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Dict, List, Optional

PROJECT_DIR = Path(__file__).resolve().parents[2]
MATERIAL_ROOT = PROJECT_DIR / "河狸云蓝枢_自媒体物料包" / "AI能力分享内容线"
DEFAULT_REGISTRY = PROJECT_DIR / "PERSONAL_IP_TOPICS.md"
DEFAULT_SEQUENCE = PROJECT_DIR / "PERSONAL_IP_SEQUENCE.md"
LOGS_DIR = PROJECT_DIR / "logs"

DATE_DIR_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})(?:[_-].*)?$")
REGISTRY_HEADER = ["日期", "标题", "一句话判断", "支柱", "层级", "状态", "目录", "平台链接"]
SEQUENCE_HEADER = ["序号", "层级", "支柱", "选题", "状态", "日期", "平台", "链接", "备注"]


def _clean_cell(value: str) -> str:
    return re.sub(r"\s+", " ", value.replace("|", "／").replace("\n", " ")).strip()


def _split_row(line: str) -> List[str]:
    return [_clean_cell(cell) for cell in line.strip().strip("|").split("|")]


def _parse_table(path: Path, header: List[str]) -> List[Dict[str, str]]:
    if not path.exists():
        return []
    rows: List[Dict[str, str]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip().startswith("|"):
            continue
        cells = _split_row(line)
        if not cells or cells[0] == header[0]:
            continue
        if all(set(cell) <= {"-", ":"} for cell in cells if cell):
            continue
        if len(cells) < len(header):
            cells += [""] * (len(header) - len(cells))
        rows.append(dict(zip(header, cells[: len(header)])))
    return rows


def _iter_day_dirs(root: Path) -> List[Path]:
    if not root.exists():
        return []
    result: List[Path] = []
    for path in sorted(root.iterdir()):
        if not path.is_dir() or path.name.startswith("_"):
            continue
        if not DATE_DIR_RE.match(path.name):
            continue
        if (path / "内容.md").is_file():
            result.append(path)
    return result


def _read_title(day_dir: Path) -> str:
    path = day_dir / "内容.md"
    if not path.is_file():
        return ""
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip().startswith("# "):
            return line.strip()[2:].strip()
    return ""


def _has_draft_log(date: str) -> bool:
    for path in sorted(LOGS_DIR.glob(f"ai_share_{date}_*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        for item in data.get("results", []) if isinstance(data, dict) else []:
            if "已存草稿" in str(item.get("status", "")):
                return True
    return False


def _status_for(day_dir: Path, registry: Dict[str, Dict[str, str]]) -> str:
    row = registry.get(day_dir.name)
    if row and row.get("状态") in ("已存草稿", "已发布", "作废"):
        return row["状态"]
    if _has_draft_log(day_dir.name):
        return "已存草稿"
    return "已写"


def status(root: Path, registry_path: Path) -> int:
    if not root.exists():
        print(f"❌ 内容线根目录不存在：{root}", file=sys.stderr)
        return 2
    registry = {
        row.get("目录", ""): row
        for row in _parse_table(registry_path, REGISTRY_HEADER)
        if row.get("目录")
    }
    days = _iter_day_dirs(root)
    if not days:
        print("没有找到日期目录。")
        return 1
    print("日期 | 标题 | 状态 | 目录")
    print("--- | --- | --- | ---")
    counts: Counter[str] = Counter()
    for day_dir in days:
        state = _status_for(day_dir, registry)
        counts[state] += 1
        print(f"{day_dir.name} | {_read_title(day_dir)} | {state} | {day_dir.name}")
    print()
    print("汇总：" + " / ".join(f"{key} {value}" for key, value in sorted(counts.items())))
    return 0


def next_item(sequence_path: Path) -> int:
    rows = _parse_table(sequence_path, SEQUENCE_HEADER)
    if not rows:
        print(f"❌ 序列台账为空或不存在：{sequence_path}", file=sys.stderr)
        return 2
    unwritten = [row for row in rows if row.get("状态") == "未写"]
    if unwritten:
        row = unwritten[0]
        print(
            f"下一条未写：#{row.get('序号')} | {row.get('层级')} | "
            f"{row.get('选题')} | 支柱：{row.get('支柱')}"
        )
        return 0
    pending = [
        row
        for row in rows
        if row.get("状态") in ("待补", "") or row.get("选题", "").startswith("待补")
    ]
    if pending:
        print(
            f"序列中有 {len(pending)} 条仍为「待补」：完整选题在 Muse「个人 IP 打造」空间。"
            "先补全台账，才能判断下一篇；脚本不猜测选题。"
        )
        return 0
    print("没有「未写」的序列项。")
    return 0


def verify(root: Path, registry_path: Path, sequence_path: Path) -> int:
    if not root.exists():
        print(f"❌ 内容线根目录不存在：{root}", file=sys.stderr)
        return 2
    registry_rows = _parse_table(registry_path, REGISTRY_HEADER)
    registered = {
        row.get("目录", "")
        for row in registry_rows
        if row.get("目录") and DATE_DIR_RE.match(Path(row["目录"]).name)
    }
    actual = {day_dir.name for day_dir in _iter_day_dirs(root)}
    missing_rows = sorted(actual - registered)
    missing_dirs = sorted(registered - actual)

    issues: List[str] = []
    if missing_rows:
        issues.extend(f"有目录无登记：{item}" for item in missing_rows)
    if missing_dirs:
        issues.extend(f"有登记无目录：{item}" for item in missing_dirs)

    sequence_rows = _parse_table(sequence_path, SEQUENCE_HEADER)
    claimed = [
        row
        for row in sequence_rows
        if row.get("状态") in ("已写", "已存草稿", "已发布")
    ]
    for row in claimed:
        topic = row.get("选题", "")
        if not topic or topic.startswith("待补"):
            issues.append(f"序列 #{row.get('序号')} 标记为已写，但选题仍为空/待补")
        if not row.get("日期"):
            issues.append(f"序列 #{row.get('序号')} 标记为已写，但日期为空")

    if issues:
        for item in issues:
            print(f"[不一致] {item}")
        return 1
    pending = sum(1 for row in sequence_rows if row.get("状态") == "待补")
    print(f"✅ 目录与选题登记一致：{len(actual)} 条")
    print(f"✅ 序列已写行自洽；仍有 {pending} 条待补（不计为不一致）")
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="个人 IP 发布序列台账状态（只读）")
    parser.add_argument("--root", type=Path, default=MATERIAL_ROOT)
    parser.add_argument("--registry", type=Path, default=DEFAULT_REGISTRY)
    parser.add_argument("--sequence", type=Path, default=DEFAULT_SEQUENCE)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--status", action="store_true", help="列出日期目录与草稿状态")
    group.add_argument("--next", action="store_true", help="找下一条未写选题")
    group.add_argument("--verify", action="store_true", help="检查登记与序列是否自洽")
    args = parser.parse_args(argv)

    root = args.root.expanduser().resolve()
    registry = args.registry.expanduser().resolve()
    sequence = args.sequence.expanduser().resolve()
    if args.status:
        return status(root, registry)
    if args.next:
        return next_item(sequence)
    if args.verify:
        return verify(root, registry, sequence)
    return 2


if __name__ == "__main__":
    sys.exit(main())
