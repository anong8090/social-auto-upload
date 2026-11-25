#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""个人 IP 与蓝枢主线共用的配图画布。

按手机阅读宽度画：1920 画布缩到约 390px 后，说明文字仍要能读。
说明文字不小于 40px。不铺工程网格，不改用细体小字。
"""

from __future__ import annotations

import re
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[2]
MATERIAL = ROOT / "河狸云蓝枢_自媒体物料包"
LOGO = MATERIAL / "品牌标准资产" / "official_logo_transparent.png"
SHOTS = MATERIAL / "图片素材" / "产品真实界面截图"

SIZES = {"16x9": (1920, 1080), "3x4": (1080, 1440)}
NAVY = (11, 19, 43)
INK = (16, 42, 67)
SLATE = (71, 85, 105)
BLUE = (29, 78, 216)
ORANGE = (194, 65, 12)
TEAL = (15, 118, 110)
WHITE = (255, 255, 255)
MIST = (244, 248, 252)
LINE = (214, 226, 240)
TINTS = {
    "blue": (239, 246, 255),
    "teal": (236, 253, 245),
    "orange": (255, 247, 237),
    "navy": (241, 245, 249),
}
FILLS = {"navy": NAVY, "blue": BLUE, "teal": TEAL, "orange": ORANGE, "ink": INK, "white": WHITE}
WEIGHT = {"medium": 7, "semibold": 11}
BANNED = ("个人IP", "个人 IP", "AI 应用思考", "AI应用思考")


def find_pingfang() -> Path:
    direct = Path("/System/Library/Fonts/PingFang.ttc")
    if direct.exists():
        return direct
    root = Path("/System/Library/AssetsV2/com_apple_MobileAsset_Font8")
    found = sorted(root.glob("*/AssetData/PingFang.ttc")) if root.exists() else []
    if found:
        return found[0]
    raise SystemExit("找不到苹方 PingFang.ttc，配图不改用细体代替")


PINGFANG = find_pingfang()


def rel_luminance(rgb) -> float:
    def channel(value: int) -> float:
        c = value / 255
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4

    r, g, b = rgb
    return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b)


def contrast(fg, bg) -> float:
    lighter = max(rel_luminance(fg), rel_luminance(bg))
    darker = min(rel_luminance(fg), rel_luminance(bg))
    return (lighter + 0.05) / (darker + 0.05)


def require_contrast(fg, bg, minimum: float, label: str) -> None:
    ratio = contrast(fg, bg)
    if ratio < minimum:
        raise SystemExit(f"{label} 对比度 {ratio:.2f} 低于 {minimum}")


def check_palette() -> None:
    for fill, label in ((BLUE, "蓝"), (TEAL, "青"), (ORANGE, "橙"), (NAVY, "深蓝")):
        require_contrast(WHITE, fill, 4.5, f"白字在{label}底")
    require_contrast(ORANGE, MIST, 3, "橙色短句在浅底")


def font(size: int, weight: str = "semibold") -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(PINGFANG), size, index=WEIGHT[weight])


def reject_copy(text: str, line: str) -> None:
    if any(word in text for word in BANNED) or re.search(r"第\s*\d+\s*期", text):
        raise SystemExit(f"配图文字含内部标签：{text}")
    if line == "personal" and any(word in text for word in ("蓝枢", "Planora")):
        raise SystemExit(f"个人 IP 配图不能出现品牌专名：{text}")


class Board:
    def __init__(self, size: str):
        if size not in SIZES:
            raise SystemExit(f"只支持 16x9 或 3x4，收到 {size}")
        self.size = size
        self.w, self.h = SIZES[size]
        self.image = Image.new("RGB", (self.w, self.h), MIST)
        self.draw = ImageDraw.Draw(self.image)

    def text(self, xy, content, size, color, weight="semibold", anchor=None, max_width=None):
        content = str(content)
        if size < 32 or (size < 40 and not re.fullmatch(r"\d{1,2}", content)):
            raise SystemExit(f"说明文字不小于 40px：{content} {size}px")
        if max_width is not None:
            box = self.draw.textbbox((0, 0), content, font=font(size, weight))
            width = box[2] - box[0]
            if width > max_width:
                raise SystemExit(f"文字超出宽度：{content} {width}px > {max_width}px")
        self.draw.text(xy, content, font=font(size, weight), fill=color, anchor=anchor)

    def panel(self, box, fill, radius=28):
        self.draw.rounded_rectangle(box, radius=radius, fill=fill)

    def header(self, kicker: str, title: str, left: int = 80):
        title_size = 92 if self.size == "16x9" else 72
        self.text((left, 64), kicker, 48, ORANGE, max_width=self.w - left - 80)
        self.text((left, 132 if self.size == "16x9" else 128), title, title_size, NAVY, max_width=self.w - left - 80)

    def footer(self, content: str):
        top = self.h - 192 if self.size == "3x4" else self.h - 192
        self.panel((80, top, self.w - 80, top + 112), NAVY, radius=22)
        self.text((self.w / 2, top + 56), content, 46, WHITE, anchor="mm", max_width=self.w - 240)

    def logo(self):
        if not LOGO.exists():
            raise SystemExit(f"找不到正版 Logo：{LOGO}")
        mark = Image.open(LOGO).convert("RGBA")
        height = 72
        width = int(mark.width * height / mark.height)
        mark = mark.resize((width, height), Image.Resampling.LANCZOS)
        self.image.paste(mark, (80, 36), mark)
        return 80 + width + 28

    def shot(self, name: str, box):
        path = SHOTS / name
        if not path.exists():
            raise SystemExit(f"找不到已授权界面截图：{path}")
        x1, y1, x2, y2 = box
        width, height = x2 - x1, y2 - y1
        src = Image.open(path).convert("RGB")
        scale = max(width / src.width, height / src.height)
        resized = src.resize((int(src.width * scale), int(src.height * scale)), Image.Resampling.LANCZOS)
        left = (resized.width - width) // 2
        top = (resized.height - height) // 2
        crop = resized.crop((left, top, left + width, top + height))
        mask = Image.new("L", (width, height), 0)
        ImageDraw.Draw(mask).rounded_rectangle((0, 0, width, height), radius=28, fill=255)
        self.image.paste(crop, (x1, y1), mask)

    def save(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.image.save(path, "PNG", optimize=True)
