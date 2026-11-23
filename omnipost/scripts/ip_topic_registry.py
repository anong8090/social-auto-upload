#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""个人 IP 选题登记与论点去重。

只做词面检查，不做语义判断：标题相似度与判断 2-gram Jaccard 只用于
提示人工复核，最终是否换题由人决定。脚本只追加/补全登记表，不删除历史行。
"""
from __future__ import annotations

import argparse
import re
import sys
from difflib import SequenceMatcher
from pathlib import Path
from typing import Dict, List, Optional, Tuple

PROJECT_DIR = Path(__file__).resolve().parents[2]
MATERIAL_ROOT = PROJECT_DIR / "河狸云蓝枢_自媒体物料包" / "AI能力分享内容线"
DEFAULT_REGISTRY = PROJECT_DIR / "PERSONAL_IP_TOPICS.md"

HEADER = ["日期", "标题", "一句话判断", "支柱", "层级", "状态", "目录", "平台链接"]
STATUSES = ("未写", "已写", "已存草稿", "已发布", "作废")
TITLE_HARD_LIMIT = 0.82
TITLE_WARN_LIMIT = 0.70
JUDGMENT_WARN_LIMIT = 0.60
DATE_DIR_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})(?:[_-].*)?$")
PENDING_JUDGMENTS = {"", "待补", "待填写", "TBD"}


def _clean_cell(value: str) -> str:
    return re.sub(r"\s+", " ", value.replace("|", "／").replace("\n", " ")).strip()


def _split_row(line: str) -> List[str]:
    return [_clean_cell(cell) for cell in line.strip().strip("|").split("|")]


def _load_rows(path: Path) -> List[Dict[str, str]]:
    if not path.exists():
        return []
    rows: List[Dict[str, str]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip().startswith("|"):
            continue
        cells = _split_row(line)
        if not cells or cells[0] == "日期":
            continue
        if all(set(cell) <= {"-", ":"} for cell in cells if cell):
            continue
        if len(cells) < len(HEADER):
            cells += [""] * (len(HEADER) - len(cells))
        rows.append(dict(zip(HEADER, cells[: len(HEADER)])))
    return rows


def _write_rows(path: Path, rows: List[Dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# 个人 IP 选题登记",
        "",
        "> 一句话判断由人填写；脚本只做词面查重，最终是否换题由人决定。",
        "",
        "| " + " | ".join(HEADER) + " |",
        "| " + " | ".join(["---"] * len(HEADER)) + " |",
    ]
    for row in rows:
        lines.append("| " + " | ".join(_clean_cell(row.get(key, "")) for key in HEADER) + " |")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


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


def _read_content(day_dir: Path) -> Tuple[str, str]:
    path = day_dir / "内容.md"
    if not path.is_file():
        return "", ""
    lines = path.read_text(encoding="utf-8").splitlines()
    title = ""
    title_index = -1
    for index, line in enumerate(lines):
        if line.strip().startswith("# "):
            title = line.strip()[2:].strip()
            title_index = index
            break
    body = "\n".join(lines[title_index + 1 :]).strip() if title_index >= 0 else ""
    return title, body


def _normalize(text: str) -> str:
    return re.sub(r"[\s\W_]+", "", text, flags=re.UNICODE).lower()


def _similarity(left: str, right: str) -> float:
    a, b = _normalize(left), _normalize(right)
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a, b).ratio()


def _grams(text: str) -> set[str]:
    normalized = _normalize(text)
    if len(normalized) < 2:
        return {normalized} if normalized else set()
    return {normalized[i : i + 2] for i in range(len(normalized) - 1)}


def _jaccard(left: str, right: str) -> float:
    a, b = _grams(left), _grams(right)
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def _find_row(rows: List[Dict[str, str]], dir_name: str) -> Optional[Dict[str, str]]:
    for row in rows:
        if row.get("目录") == dir_name or Path(row.get("目录", "")).name == dir_name:
            return row
    return None


def scan(root: Path, registry: Path) -> int:
    if not root.exists():
        print(f"❌ 内容线根目录不存在：{root}", file=sys.stderr)
        return 2
    rows = _load_rows(registry)
    added = 0
    for day_dir in _iter_day_dirs(root):
        relative = day_dir.relative_to(root).as_posix()
        title, _ = _read_content(day_dir)
        row = _find_row(rows, relative)
        if row is not None:
            if not row.get("标题") and title:
                row["标题"] = title
            continue
        rows.append(
            {
                "日期": day_dir.name[:10],
                "标题": title,
                "一句话判断": "待补",
                "支柱": "",
                "层级": "",
                "状态": "已写",
                "目录": relative,
                "平台链接": "",
            }
        )
        added += 1
    _write_rows(registry, rows)
    print(f"✅ 选题登记已更新：共 {len(rows)} 条，本次新增 {added} 条")
    print(f"   登记表：{registry}")
    return 0


def list_rows(registry: Path, limit: int) -> int:
    rows = _load_rows(registry)
    if not rows:
        print(f"登记表为空或不存在：{registry}")
        print("先运行 --scan 建立登记。")
        return 1
    ordered = sorted(rows, key=lambda row: row.get("日期", ""), reverse=True)[:limit]
    print(f"最近 {len(ordered)} 条选题：")
    for row in ordered:
        print(
            f"- {row.get('日期', '')} | {row.get('标题', '')} | "
            f"{row.get('一句话判断', '')} | {row.get('状态', '')}"
        )
    return 0


def check(day_dir: Path, registry: Path) -> int:
    if not day_dir.is_dir():
        print(f"❌ 内容目录不存在：{day_dir}", file=sys.stderr)
        return 2
    rows = _load_rows(registry)
    if not rows:
        print(f"❌ 登记表为空或不存在：{registry}\n先运行 --scan。", file=sys.stderr)
        return 1

    row = _find_row(rows, day_dir.name)
    if row is None:
        print(f"❌ 当前目录未登记：{day_dir.name}\n先运行 --scan。", file=sys.stderr)
        return 1

    title, _ = _read_content(day_dir)
    if not title:
        print(f"❌ 内容目录缺少一级标题：{day_dir / '内容.md'}", file=sys.stderr)
        return 1

    judgment = row.get("一句话判断", "").strip()
    if judgment in PENDING_JUDGMENTS:
        print(
            f"❌ 本期判断仍为「{judgment or '空'}」：请先在 {registry.name} 写清「判断 + 边界/代价」。",
            file=sys.stderr,
        )
        return 1

    errors: List[str] = []
    warnings: List[str] = []
    for other in rows:
        if other is row or other.get("目录") == row.get("目录"):
            continue
        other_title = other.get("标题", "").strip()
        if other_title:
            ratio = _similarity(title, other_title)
            if ratio >= TITLE_HARD_LIMIT:
                errors.append(
                    f"标题与 {other.get('目录', '')} 高度相似（{ratio:.2f}）：{other_title}"
                )
            elif ratio >= TITLE_WARN_LIMIT:
                warnings.append(
                    f"标题与 {other.get('目录', '')} 偏近（{ratio:.2f}），确认不是同题换皮"
                )
        other_judgment = other.get("一句话判断", "").strip()
        if other_judgment not in PENDING_JUDGMENTS:
            ratio = _jaccard(judgment, other_judgment)
            if ratio >= JUDGMENT_WARN_LIMIT:
                warnings.append(
                    f"判断与 {other.get('目录', '')} 过近（{ratio:.2f}），确认不是旧论点换皮"
                )

    if errors:
        for item in errors:
            print(f"[错误] {item}")
    for item in warnings:
        print(f"[提醒] {item}")
    if errors:
        print("[未通过] 先换题或重写判断，再进入写稿。")
        return 1
    if warnings:
        print("[通过] 无硬冲突，但有过近提醒，需人工确认。")
    else:
        print("[通过] 标题与判断均未发现明显重复。")
    return 0


def verify(root: Path, registry: Path) -> int:
    if not root.exists():
        print(f"❌ 内容线根目录不存在：{root}", file=sys.stderr)
        return 2
    rows = _load_rows(registry)
    registered = {
        row.get("目录", "")
        for row in rows
        if row.get("目录") and DATE_DIR_RE.match(Path(row["目录"]).name)
    }
    actual = {day_dir.relative_to(root).as_posix() for day_dir in _iter_day_dirs(root)}
    missing_rows = sorted(actual - registered)
    missing_dirs = sorted(registered - actual)

    if missing_rows:
        print("有目录无登记：")
        for item in missing_rows:
            print(f"  - {item}")
    if missing_dirs:
        print("有登记无目录：")
        for item in missing_dirs:
            print(f"  - {item}")
    if missing_rows or missing_dirs:
        print(f"\n共 {len(missing_rows) + len(missing_dirs)} 处不一致，请运行 --scan 或手工修正。")
        return 1
    print(f"✅ 目录与登记表一致：{len(actual)} 条")
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="个人 IP 选题登记与论点去重（只做词面检查，不判断语义）"
    )
    parser.add_argument("--registry", type=Path, default=DEFAULT_REGISTRY, help=f"登记表路径（默认 {DEFAULT_REGISTRY}）")
    parser.add_argument("--root", type=Path, default=MATERIAL_ROOT, help=f"内容线根目录（默认 {MATERIAL_ROOT}）")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--scan", action="store_true", help="扫描日期目录，为未登记目录补行")
    group.add_argument("--list", action="store_true", help="列出最近 N 条标题与判断")
    group.add_argument(
        "--check",
        nargs="?",
        const=True,
        type=Path,
        metavar="DIR",
        help="检查指定内容目录；可写 --check <DIR> 或 --check --dir <DIR>",
    )
    group.add_argument("--verify", action="store_true", help="检查目录与登记表是否一一对应")
    parser.add_argument("--dir", type=Path, help="--check 的内容目录（与 --check DIR 二选一）")
    parser.add_argument("--limit", type=int, default=10, help="--list 返回条数（默认 10）")
    args = parser.parse_args(argv)

    registry = args.registry.expanduser().resolve()
    root = args.root.expanduser().resolve()
    if args.dir is not None and args.check is None:
        parser.error("--dir 只能与 --check 一起使用")
    if args.scan:
        return scan(root, registry)
    if args.list:
        return list_rows(registry, max(1, args.limit))
    if args.check is not None:
        if args.dir is not None:
            day_dir = args.dir
        elif isinstance(args.check, Path):
            day_dir = args.check
        else:
            parser.error("--check 需要目录：--check <DIR> 或 --check --dir <DIR>")
        return check(day_dir.expanduser().resolve(), registry)
    if args.verify:
        return verify(root, registry)
    return 2


if __name__ == "__main__":
    sys.exit(main())
