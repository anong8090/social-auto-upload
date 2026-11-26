#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""按规格 JSON 生成个人 IP 或主线配图。不要再复制旧的网格脚本。

用法：
  python3 河狸云蓝枢_自媒体物料包/配图工坊/render.py 规格.json

规格：
  line: personal 或 mainline
  out: 输出目录
  images: 每张图一个对象
    file, size(16x9|3x4), layout(cards|split|rows|cover|statement|compare|path)
    kicker, title, footer
    cards: [{label, lines, color}]          layout=cards
    left/center/right: {title, lines}       layout=split
    rows: [{label, text, color}]            layout=rows
    statement: [{title, lines}]             layout=statement
    top/bottom: {title, lines}              layout=compare
    steps: [{label, text, color}]           layout=path
    points: [短句]                           layout=cover
    shot: 产品真实界面截图文件名              仅 mainline
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from craft import (
    BLUE,
    FILLS,
    INK,
    NAVY,
    ORANGE,
    SLATE,
    TEAL,
    TINTS,
    WHITE,
    Board,
    check_palette,
    reject_copy,
)

HERE = Path(__file__).resolve().parent


def collect_text(spec: dict, image: dict) -> str:
    chunks = [image.get("kicker", ""), image.get("title", ""), image.get("footer", "")]
    chunks.extend(image.get("points") or [])
    for card in image.get("cards") or []:
        chunks.append(card.get("label", ""))
        chunks.extend(card.get("lines") or [])
    for row in image.get("rows") or []:
        chunks.extend((row.get("label", ""), row.get("text", "")))
    for block in image.get("statement") or []:
        chunks.extend([block.get("title", ""), *block.get("lines", [])])
    for side in ("top", "bottom"):
        block = image.get(side) or {}
        chunks.append(block.get("title", ""))
        chunks.extend(block.get("lines") or [])
    for step in image.get("steps") or []:
        chunks.extend((step.get("label", ""), step.get("text", "")))
    for side in ("left", "center", "right"):
        block = image.get(side) or {}
        chunks.append(block.get("title", ""))
        chunks.extend(block.get("lines") or [])
    return "\n".join(str(item) for item in chunks if item)


def color_of(name: str):
    if name not in FILLS:
        raise SystemExit(f"色块只允许 navy/blue/teal/orange，收到 {name}")
    return FILLS[name]


def draw_cards(board: Board, image: dict):
    cards = image.get("cards") or []
    if not 2 <= len(cards) <= 3:
        raise SystemExit("cards 布局只放 2 或 3 张卡，一张图只讲一件事")
    board.header(image["kicker"], image["title"])
    if board.size == "16x9":
        gap = 28
        width = (board.w - 160 - gap * (len(cards) - 1)) // len(cards)
        x = 80
        for index, card in enumerate(cards, 1):
            _card_wide(board, card, index, x, 292, x + width, 860)
            x += width + gap
    else:
        y = 250
        height = 280
        for index, card in enumerate(cards, 1):
            _card_flat(board, card, index, 56, y, board.w - 56, y + height)
            y += height + 24
    if image.get("footer"):
        board.footer(image["footer"])


def _card_wide(board: Board, card: dict, index: int, x1, y1, x2, y2):
    fill = color_of(card.get("color", "blue"))
    board.panel((x1, y1, x2, y2), WHITE)
    board.panel((x1, y1, x2, y1 + 216), fill)
    board.draw.rectangle((x1, y1 + 168, x2, y1 + 216), fill=fill)
    board.text((x1 + 48, y1 + 36), f"{index:02d}", 36, WHITE)
    board.text((x1 + 48, y1 + 96), card["label"], 72, WHITE, max_width=x2 - x1 - 96)
    y = y1 + 276
    for line in card.get("lines") or []:
        board.text((x1 + 48, y), line, 52, INK, max_width=x2 - x1 - 96)
        y += 84


def _card_flat(board: Board, card: dict, index: int, x1, y1, x2, y2):
    fill = color_of(card.get("color", "blue"))
    board.panel((x1, y1, x2, y2), WHITE)
    board.panel((x1, y1, x1 + 220, y2), fill)
    board.text((x1 + 110, y1 + 92), f"{index:02d}", 36, WHITE, anchor="mm")
    board.text((x1 + 110, y1 + 168), card["label"], 48, WHITE, anchor="mm", max_width=180)
    y = y1 + 48
    for line in (card.get("lines") or [])[:3]:
        board.text((x1 + 260, y), line, 44, INK, max_width=x2 - x1 - 300)
        y += 72


def draw_split(board: Board, image: dict):
    board.header(image["kicker"], image["title"])
    if board.size != "16x9":
        raise SystemExit("split 布局只用于 16x9，竖版改用 rows")
    left, center, right = image["left"], image["center"], image["right"]
    top, bottom = 292, 860
    board.panel((72, top, 848, bottom), WHITE)
    board.panel((876, top, 1044, bottom), ORANGE, radius=36)
    board.panel((1072, top, 1848, bottom), NAVY)
    board.text((120, 340), left["title"], 64, INK, max_width=680)
    y = 470
    for index, line in enumerate(left.get("lines") or [], 1):
        board.panel((120, y, 196, y + 72), BLUE, radius=16)
        board.text((158, y + 36), f"{index:02d}", 32, WHITE, anchor="mm")
        board.text((224, y + 36), line, 48, INK, anchor="lm", max_width=560)
        y += 96
    for index, line in enumerate(center.get("lines") or []):
        board.text((960, 500 + index * 90), line, 44, WHITE, anchor="mm", max_width=150)
    board.text((1120, 340), right["title"], 64, WHITE, max_width=680)
    y = 500
    for line in right.get("lines") or []:
        board.text((1120, y), line, 52, WHITE, max_width=680)
        y += 100
    if image.get("footer"):
        board.footer(image["footer"])


def draw_rows(board: Board, image: dict):
    rows = image.get("rows") or []
    if not 2 <= len(rows) <= 3:
        raise SystemExit("rows 布局只放 2 或 3 行")
    board.header(image["kicker"], image["title"])
    y = 300 if board.size == "16x9" else 260
    height = 176 if board.size == "16x9" else 240
    step = 196 if board.size == "16x9" else 270
    for row in rows:
        fill = color_of(row.get("color", "blue"))
        tint = TINTS[row.get("color", "blue")]
        board.panel((80, y, board.w - 80, y + height), tint, radius=24)
        board.panel((112, y + 36, 392, y + height - 36), fill, radius=20)
        board.text((252, y + height / 2), row["label"], 56, WHITE, anchor="mm", max_width=240)
        board.text((440, y + height / 2), row["text"], 48 if board.size == "3x4" else 52, INK, anchor="lm", max_width=board.w - 560)
        y += step


def draw_cover(board: Board, image: dict, line: str):
    if line != "mainline":
        raise SystemExit("cover 带界面截图只给主线；个人 IP 用 cards、split 或 rows")
    left = board.logo() if image.get("logo", True) else 80
    board.header(image["kicker"], image["title"], left=left)
    points = image.get("points") or []
    if not 2 <= len(points) <= 3:
        raise SystemExit("cover 只放 2 或 3 个短句")
    if board.size == "16x9" and image.get("shot"):
        board.shot(image["shot"], (1120, 300, 1840, 860))
        max_width = 980
    else:
        max_width = board.w - 160
    y = 340 if board.size == "16x9" else 280
    for index, point in enumerate(points, 1):
        board.panel((80, y, 168, y + 84), TEAL, radius=18)
        board.text((124, y + 42), f"{index:02d}", 32, WHITE, anchor="mm")
        board.text((196, y + 42), point, 48, INK, anchor="lm", max_width=max_width - 140)
        y += 120
    if image.get("footer"):
        board.footer(image["footer"])


def draw_statement(board: Board, image: dict):
    """左侧主张、右侧纵向清单：用于一个判断配若干边界。"""
    if board.size != "16x9":
        raise SystemExit("statement 布局只用于 16x9")
    blocks = image.get("statement") or []
    if not 2 <= len(blocks) <= 3:
        raise SystemExit("statement 布局放 2 或 3 组内容")
    board.header(image["kicker"], image["title"])
    board.panel((80, 292, 760, 860), NAVY, radius=36)
    board.text((128, 360), blocks[0]["title"], 72, WHITE, max_width=584)
    y = 500
    for line in blocks[0].get("lines") or []:
        board.text((128, y), line, 52, WHITE, max_width=584)
        y += 96
    x = 808
    width = (1840 - x - 28 * (len(blocks) - 1)) // (len(blocks) - 1)
    for index, block in enumerate(blocks[1:], 1):
        fill = color_of(block.get("color", "blue"))
        board.panel((x, 292, x + width, 860), WHITE)
        board.panel((x, 292, x + width, 388), fill)
        board.text((x + 36, 340), f"{index:02d}", 36, WHITE, anchor="lm")
        board.text((x + 36, 458), block["title"], 56, INK, max_width=width - 72)
        y = 560
        for line in block.get("lines") or []:
            board.text((x + 36, y), line, 44, INK, max_width=width - 72)
            y += 78
        x += width + 28
    if image.get("footer"):
        board.footer(image["footer"])


def draw_compare(board: Board, image: dict):
    """上下分区的对话式对照：用于「不是 A，而是 B」的转折。"""
    if board.size != "16x9":
        raise SystemExit("compare 布局只用于 16x9")
    top, bottom = image["top"], image["bottom"]
    board.header(image["kicker"], image["title"])
    board.panel((80, 292, 1840, 548), WHITE)
    board.panel((80, 580, 1840, 860), NAVY)
    board.panel((80, 292, 168, 548), ORANGE)
    board.text((124, 420), "旧", 52, WHITE, anchor="mm")
    board.text((220, 356), top["title"], 60, INK, max_width=1520)
    board.text((220, 454), "；".join(top.get("lines") or []), 48, SLATE, max_width=1520)
    board.panel((80, 580, 168, 860), TEAL)
    board.text((124, 720), "新", 52, WHITE, anchor="mm")
    board.text((220, 648), bottom["title"], 60, WHITE, max_width=1520)
    board.text((220, 760), "；".join(bottom.get("lines") or []), 48, WHITE, max_width=1520)
    if image.get("footer"):
        board.footer(image["footer"])


def draw_path(board: Board, image: dict):
    """从左到右的短路径：用于「选路—计算—回核」这类动作顺序。"""
    if board.size != "16x9":
        raise SystemExit("path 布局只用于 16x9")
    steps = image.get("steps") or []
    if not 2 <= len(steps) <= 4:
        raise SystemExit("path 布局放 2 到 4 步")
    board.header(image["kicker"], image["title"])
    gap = 32
    width = (board.w - 160 - gap * (len(steps) - 1)) // len(steps)
    x = 80
    for index, step in enumerate(steps, 1):
        fill = color_of(step.get("color", "blue"))
        board.panel((x, 320, x + width, 820), WHITE)
        board.panel((x, 320, x + width, 420), fill)
        board.text((x + 40, 370), f"{index:02d}", 36, WHITE, anchor="lm")
        board.text((x + 40, 500), step["label"], 60, INK, max_width=width - 80)
        board.text((x + 40, 620), step["text"], 44, SLATE, max_width=width - 80)
        if index < len(steps):
            board.draw.line((x + width + 6, 570, x + width + gap - 6, 570), fill=ORANGE, width=8)
        x += width + gap
    if image.get("footer"):
        board.footer(image["footer"])


LAYOUTS = {
    "cards": draw_cards,
    "split": draw_split,
    "rows": draw_rows,
    "cover": draw_cover,
    "statement": draw_statement,
    "compare": draw_compare,
    "path": draw_path,
}


def render_spec(path: Path) -> list[Path]:
    spec = json.loads(path.read_text(encoding="utf-8"))
    line = spec.get("line")
    if line not in {"personal", "mainline"}:
        raise SystemExit("line 只能是 personal 或 mainline")
    check_palette()
    out = Path(spec["out"])
    if not out.is_absolute():
        out = path.parent / out
    written = []
    for image in spec["images"]:
        reject_copy(collect_text(spec, image), line)
        layout = image.get("layout")
        if layout not in LAYOUTS:
            raise SystemExit(f"未知布局 {layout}")
        if layout == "cover":
            board = Board(image.get("size", "16x9"))
            draw_cover(board, image, line)
        else:
            board = Board(image.get("size", "16x9"))
            LAYOUTS[layout](board, image)
        target = out / image["file"]
        board.save(target)
        written.append(target)
    return written


def main() -> int:
    if len(sys.argv) != 2:
        raise SystemExit("用法：python3 render.py 规格.json")
    for path in render_spec(Path(sys.argv[1])):
        print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
