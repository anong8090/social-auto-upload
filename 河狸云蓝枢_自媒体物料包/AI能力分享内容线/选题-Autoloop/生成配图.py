#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""IP-03 Autoloop 选题包配图：长文 3 张 16:9，短稿各 1 张 3:4。"""
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

OUT = Path(__file__).resolve().parent
LONG_DIR = OUT / "01_公开稿"
CONTENT_ROOT = OUT.parent
SHORT_DIRS = {
    "2026-11-25": CONTENT_ROOT / "2026-11-25",
    "2026-11-26": CONTENT_ROOT / "2026-11-26",
    "2026-11-27": CONTENT_ROOT / "2026-11-27",
}

FONT_BOLD = "/System/Library/Fonts/STHeiti Medium.ttc"
FONT_LIGHT = "/System/Library/Fonts/STHeiti Light.ttc"

NAVY = (18, 31, 53)
SLATE = (83, 101, 126)
MUTED = (148, 163, 184)
BLUE = (37, 99, 235)
CYAN = (14, 165, 233)
ORANGE = (234, 88, 12)
GREEN = (22, 163, 74)
BG_TOP = (246, 250, 255)
BG_BOTTOM = (255, 255, 255)
CARD = (255, 255, 255)
LINE = (214, 229, 247)
GRID = (237, 244, 252)
SHADOW = (232, 241, 252)


def f(size, bold=False):
    return ImageFont.truetype(FONT_BOLD if bold else FONT_LIGHT, size)


def base(w, h):
    im = Image.new("RGB", (w, h), BG_BOTTOM)
    d = ImageDraw.Draw(im)
    for y in range(h):
        t = y / max(1, h - 1)
        color = tuple(int(BG_TOP[i] * (1 - t) + BG_BOTTOM[i] * t) for i in range(3))
        d.line((0, y, w, y), fill=color)
    for x in range(0, w, 64):
        d.line((x, 0, x, h), fill=GRID)
    for y in range(0, h, 64):
        d.line((0, y, w, y), fill=GRID)
    return im, d


def rr(d, box, radius, fill=CARD, outline=LINE, width=2):
    d.rounded_rectangle(box, radius=radius, fill=fill, outline=outline, width=width)


def card(d, box, title, desc, color=(37, 99, 235), fill=CARD):
    rr(d, box, 20, fill, color, 3)
    x1, y1 = box[0], box[1]
    d.rounded_rectangle((x1, y1, x1 + 10, box[3]), radius=5, fill=color)
    d.text((x1 + 34, y1 + 28), title, font=f(40, True), fill=color)
    wrap(d, (x1 + 34, y1 + 92), desc, f(24), SLATE, box[2] - box[0] - 62, 10)


def wrap(d, xy, text, font, color, max_width, gap=10):
    x, y = xy
    line = ""
    for char in text:
        trial = line + char
        if line and d.textlength(trial, font=font) > max_width:
            d.text((x, y), line, font=font, fill=color)
            box = d.textbbox((x, y), line, font=font)
            y += box[3] - box[1] + gap
            line = char
        else:
            line = trial
    if line:
        d.text((x, y), line, font=font, fill=color)
    return y


def header(d, eyebrow, lines, sub, title_size=84, eyebrow_size=26, sub_size=36, top=64):
    d.text((92, top), eyebrow, font=f(eyebrow_size, True), fill=BLUE)
    y = top + 60
    for line in lines:
        d.text((88, y), line, font=f(title_size, True), fill=NAVY)
        y += title_size + 20
    d.text((92, y + 8), sub, font=f(sub_size, True), fill=ORANGE)
    return y + sub_size + 50


def long_images():
    LONG_DIR.mkdir(parents=True, exist_ok=True)

    w, h = 1920, 1080
    im, d = base(w, h)
    y = header(d, "自动化治理", ["规则也要有", "到期日"], "临时判断 · 词表 · 白名单")
    boxes = [(92, y, 650, y + 250), (681, y, 1239, y + 250), (1270, y, 1828, y + 250)]
    card(d, boxes[0], "写清起因", "当初防的是哪个具体问题", BLUE, (244, 249, 255))
    card(d, boxes[1], "准备替代", "有没有更轻的保护办法", CYAN, (240, 250, 255))
    card(d, boxes[2], "定复核日", "到期做换掉、撤掉或延长", ORANGE, (255, 247, 242))
    rr(d, (92, 930, 1828, 1000), 18, (255, 250, 244), ORANGE, 2)
    d.text((124, 946), "到期只做三件事：换掉 / 撤掉 / 明确延长", font=f(30, True), fill=ORANGE)
    im.save(LONG_DIR / "01_16x9_规则到期日.png")

    im, d = base(w, h)
    y = header(d, "规则为什么越积越多", ["每条都曾有用，", "后来没人敢删"], "只加不减，系统会越来越难改")
    boxes = [(92, y, 650, y + 250), (681, y, 1239, y + 250), (1270, y, 1828, y + 250)]
    card(d, boxes[0], "当时有效", "针对一个真实出现的问题", GREEN, (242, 252, 246))
    card(d, boxes[1], "场景变了", "原来的问题可能已经消失", BLUE, (244, 249, 255))
    card(d, boxes[2], "没人敢删", "不知道它当初防的是什么", ORANGE, (255, 247, 242))
    im.save(LONG_DIR / "02_16x9_规则越积越多.png")

    im, d = base(w, h)
    y = header(d, "到期复核", ["只看四件事"], "删规则和加规则，都要看后果")
    boxes = [
        (92, y, 880, y + 230),
        (940, y, 1728, y + 230),
        (92, y + 260, 880, y + 490),
        (940, y + 260, 1728, y + 490),
    ]
    card(d, boxes[0], "原来防什么", "把当初的场景找回来", BLUE, (244, 249, 255))
    card(d, boxes[1], "问题还在吗", "现在是否仍会发生", CYAN, (240, 250, 255))
    card(d, boxes[2], "有无替代", "有没有更轻的保护", GREEN, (242, 252, 246))
    card(d, boxes[3], "最坏会怎样", "撤掉后错误能不能收回", ORANGE, (255, 247, 242))
    im.save(LONG_DIR / "03_16x9_到期复核.png")


def short_image(day, title_lines, sub, rows, filename, accent=BLUE, layout="rows"):
    w, h = 1080, 1440
    im, d = base(w, h)
    y = header(d, "Autoloop 判断", title_lines, sub, title_size=66, eyebrow_size=24, sub_size=30, top=72)
    if layout == "rows":
        for index, (title, desc) in enumerate(rows):
            top = y + index * 220
            card(d, (72, top, 1008, top + 190), title, desc, accent if index == 0 else BLUE, CARD)
    elif layout == "focus":
        card(d, (72, y, 1008, y + 190), *rows[0], accent, CARD)
        card(d, (72, y + 230, 520, y + 420), *rows[1], BLUE, CARD)
        card(d, (560, y + 230, 1008, y + 420), *rows[2], BLUE, CARD)
    else:
        d.line((130, y + 30, 130, y + 620), fill=LINE, width=4)
        for index, (title, desc) in enumerate(rows):
            top = y + index * 220
            cx, cy = 130, top + 54
            d.ellipse((cx - 26, cy - 26, cx + 26, cy + 26), fill=accent if index == 0 else BLUE)
            d.text((cx - 8, cy - 20), str(index + 1), font=f(24, True), fill=(255, 255, 255))
            card(d, (190, top, 1008, top + 180), title, desc, accent if index == 0 else BLUE, CARD)
    rr(d, (72, 1260, 1008, 1350), 18, (255, 250, 244), ORANGE, 2)
    d.text((104, 1282), "先定义边界，再谈自动化", font=f(30, True), fill=ORANGE)
    SHORT_DIRS[day].mkdir(parents=True, exist_ok=True)
    im.save(SHORT_DIRS[day] / filename)


def short_images():
    short_image(
        "2026-11-25",
        ["规则也要", "有保质期"],
        "临时规则 · 到期复核",
        [("写复核日", "到期不是忘了，而是主动判断"), ("看问题是否还在", "原来的场景可能已经消失"), ("能换能撤", "保留也要写下次复核时间")],
        "01_3x4_规则保质期.png",
        ORANGE,
        "rows",
    )
    short_image(
        "2026-11-26",
        ["只读不是", "保守"],
        "先买证据 · 再谈权限",
        [("先看先算", "不直接改业务数据"), ("错了代价低", "最多多一次人工确认"), ("证据够了再放权", "权限跟着后果走")],
        "01_3x4_只读不是保守.png",
        CYAN,
        "focus",
    )
    short_image(
        "2026-11-27",
        ["人工门", "不是拖延"],
        "证据不足 · 默认交人",
        [("证据不完整", "机器给结果，不等于能验证"), ("默认交人", "让人看一眼更省事"), ("补齐再放行", "门要有退出条件")],
        "01_3x4_人工门不是拖延.png",
        GREEN,
        "timeline",
    )


if __name__ == "__main__":
    long_images()
    short_images()
    for path in sorted(OUT.rglob("*.png")):
        print(path)
