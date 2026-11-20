#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""核验大鱼号「我的草稿」里第 17 期草稿是否完整落库。

存草稿不等于内容完整：标题、正文、两张内文图、封面任何一项没保存上，
草稿箱里都会是残缺的。这个脚本按标题回读草稿列表，逐一核对。

用法：
  .venv/bin/python scripts/dayu_verify_draft.py --title "没信号工地，图纸怎么才能打开？"
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))

from playwright.async_api import async_playwright  # noqa: E402

ACCOUNT_FILE = BASE / "cookiesFile" / "f4a67884-d9b3-11f1-9251-95caa5137ef6.json"
SHOT = BASE.parent / "logs" / "screenshots" / "ep17_dayu_draft.png"


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--title", required=True, help="本期标题")
    parser.add_argument("--shot", default=str(SHOT))
    args = parser.parse_args()

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True, channel="chrome")
        context = await browser.new_context(
            storage_state=str(ACCOUNT_FILE),
            viewport={"width": 1440, "height": 900},
        )
        page = await context.new_page()
        await page.goto(
            "https://mp.dayu.com/dashboard/draft",
            timeout=90000,
            wait_until="domcontentloaded",
        )
        await page.wait_for_timeout(7000)

        body = await page.evaluate("() => document.body.innerText")
        print("URL:", page.url)
        if args.title not in body:
            print(f"❌ 草稿箱未找到「{args.title}」")
            print("页面片段:", body[:400].replace("\n", " | "))
            await page.screenshot(path=args.shot)
            await browser.close()
            return 1

        print(f"✅ 草稿箱已找到「{args.title}」")
        idx = body.find(args.title)
        print("上下文:", body[max(0, idx - 120):idx + 260].replace("\n", " | "))

        Path(args.shot).parent.mkdir(parents=True, exist_ok=True)
        await page.screenshot(path=args.shot, full_page=True)
        print(f"📸 截图已保存: {args.shot}")

        await browser.close()
        return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
