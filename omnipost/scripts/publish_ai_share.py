# -*- coding: utf-8 -*-
"""「河狸云 AI 能力分享」独立内容线：小红书 + 微信公众号，同内容、只存草稿。

与主线 `publish_episode.py` 完全隔离：

- 主线是「蓝枢」一稿五投，随机抽 5 台；小红书在主线里被 `preflight.BANNED_PLATFORMS`
  硬拦截（AGENTS.md 记其为封禁账号）。本脚本不动主线的封禁表与候选池。
- 本内容线**不指定（不提及）蓝枢**，只讲河狸云的 AI 能力分享，每日单独指定内容。
- 两个平台发**同一份内容**，都**只存草稿**，绝不点击「发布 / 发表 / 发送」。
- 全程无头浏览器，不干扰用户工作。

素材约定（每天一个目录，由人工/上游生成）：

    河狸云蓝枢_自媒体物料包/AI能力分享内容线/<YYYY-MM-DD>/
        内容.md            # 首行 # 标题，随后是正文；不得出现「蓝枢」
        *.png / *.jpg     # 配图（小红书图集 / 公众号正文图与封面）

用法：

    # 预检（离线，不开浏览器）：只校验素材与合规
    .venv/bin/python scripts/publish_ai_share.py --date 2026-11-09 --check-only

    # 存草稿（默认，两个平台都存）
    .venv/bin/python scripts/publish_ai_share.py --date 2026-11-09

    # 只存其中一个平台
    .venv/bin/python scripts/publish_ai_share.py --date 2026-11-09 --platforms 1

⚠️ 本脚本**不提供** `--publish` 参数；传入任何发布意图参数都会直接报错退出。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, List

OMNIPOST_DIR = Path(__file__).resolve().parent.parent
PROJECT_DIR = OMNIPOST_DIR.parent
sys.path.insert(0, str(OMNIPOST_DIR))

from myUtils import postVideo  # noqa: E402
from myUtils import preflight  # noqa: E402
from scripts import compliance_check as cc  # noqa: E402

MATERIAL_ROOT = PROJECT_DIR / "河狸云蓝枢_自媒体物料包" / "AI能力分享内容线"
CONTENT_LINE = "河狸云AI能力分享"
ALLOWED_PLATFORMS = (1, 12)  # 小红书 / 微信公众号
PLATFORM_LABELS = {1: "小红书", 12: "微信公众号"}

# 独立内容线独有的红线：正文/标题不得出现「蓝枢」及其产品名。
# 用户明确要求「不指定蓝枢」，这里在零容忍词之外单独加一道闸门。
BRAND_EXCLUSIONS = ("蓝枢", "Planora", "河狸云·蓝枢")

# 小红书标题 20 字、公众号 64 字（与 preflight.TITLE_LIMITS 中 12 一致）
TITLE_LIMITS = {1: (1, 20), 12: (1, 64)}


def _parse_content(md_path: Path) -> Dict[str, str]:
    raw = md_path.read_text(encoding="utf-8")
    lines = raw.splitlines()
    title = ""
    for line in lines:
        s = line.strip()
        if s.startswith("# "):
            title = s[2:].strip()
            break
    if not title:
        raise ValueError(f"{md_path} 缺少一级标题（# 标题）")
    body_start = next(
        (i for i, l in enumerate(lines) if l.strip().startswith("# ")), 0
    ) + 1
    body = "\n".join(lines[body_start:]).strip()
    if len(body) < 50:
        raise ValueError(f"{md_path} 正文过短（{len(body)} 字），拒绝存草稿")
    return {"title": title, "body": body, "raw": raw}


def _collect_images(day_dir: Path) -> List[str]:
    files = sorted(
        p for p in day_dir.iterdir()
        if p.is_file() and p.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp")
    )
    if not files:
        raise ValueError(f"{day_dir} 缺少配图（至少需要 1 张）")
    return [str(p.resolve()) for p in files]


def precheck(day_dir: Path, platforms: List[int]) -> Dict[str, Any]:
    """离线合规自检：任一平台不通过即抛错，绝不进入浏览器环节。"""
    content = _parse_content(day_dir / "内容.md")
    images = _collect_images(day_dir)
    text = content["raw"]

    problems: List[str] = []
    for word in BRAND_EXCLUSIONS:
        if word in text:
            problems.append(f"独立内容线禁止出现「{word}」（用户要求不指定蓝枢）")
    for word in cc.BANNED_WORDS:
        if word in text:
            problems.append(f"命中零容忍词：{word}")

    for ptype in platforms:
        low, high = TITLE_LIMITS[ptype]
        n = len(content["title"])
        if not (low <= n <= high):
            problems.append(
                f"{PLATFORM_LABELS[ptype]}标题 {n} 字，超出 {low}~{high} 字红线"
            )
    if problems:
        raise ValueError("合规自检未通过：\n  - " + "\n  - ".join(problems))

    return {
        "date": day_dir.name,
        "title": content["title"],
        "body": content["body"],
        "images": images,
        "platforms": platforms,
        "body_chars": len(content["body"]),
    }


def _account_files(ptype: int) -> List[str]:
    """从 user_info 读取账号 cookie 文件名（不改数据库）。"""
    import sqlite3
    db = OMNIPOST_DIR / "db" / "database.db"
    with sqlite3.connect(str(db)) as conn:
        rows = conn.execute(
            "select filePath, status from user_info where type = ?", (ptype,)
        ).fetchall()
    files = [r[0] for r in rows if r[0]]
    if not files:
        raise RuntimeError(f"{PLATFORM_LABELS[ptype]}（type={ptype}）未绑定账号")
    return files


def _readback_drafts(ptype: int, title: str) -> bool:
    """存完草稿后回平台草稿箱按本期标题回读核验（无头）。

    返回 True 表示命中；抛错表示核验不可用，由调用方如实报告，不得宣称成功。
    """
    import asyncio
    from playwright.async_api import async_playwright
    from conf import LOCAL_CHROME_PATH
    from utils.base_social_media import set_init_script

    cookie = OMNIPOST_DIR / "cookiesFile" / _account_files(ptype)[0]
    if ptype == 12:
        # 微信公众平台在 bundled Chromium 下必崩，必须走系统 Chrome
        launch = {"headless": True, "channel": "chrome"}
        url = "https://mp.weixin.qq.com/cgi-bin/appmsg?type=9&action=list_card&lang=zh_CN"
    else:
        launch = {"headless": True}
        url = "https://creator.xiaohongshu.com/publish/publish?target=image"
    if LOCAL_CHROME_PATH and ptype == 1:
        launch["executable_path"] = LOCAL_CHROME_PATH

    async def _run() -> bool:
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(**launch)
            ctx = await browser.new_context(
                storage_state=str(cookie), viewport={"width": 1440, "height": 900}
            )
            ctx = await set_init_script(ctx)
            page = await ctx.new_page()
            try:
                await page.goto(url, timeout=60000, wait_until="domcontentloaded")
                await page.wait_for_timeout(6000)
                text = await page.evaluate("() => document.body.innerText")
                return title in text
            finally:
                await browser.close()

    return asyncio.run(_run())


def main(argv: List[str] | None = None) -> int:
    # 硬拦截任何「发布」意图：本内容线只能存草稿。
    joined = " ".join(argv if argv is not None else sys.argv[1:])
    for forbidden in ("--publish", "--no-draft", "--direct"):
        if forbidden in joined:
            print(f"❌ 独立内容线只能存草稿，禁止使用 {forbidden}", file=sys.stderr)
            return 2

    parser = argparse.ArgumentParser(description="河狸云 AI 能力分享独立内容线（只存草稿）")
    parser.add_argument("--date", default=date.today().isoformat(), help="内容日期 YYYY-MM-DD")
    parser.add_argument("--dir", default="", help="直接指定内容目录（覆盖 --date）")
    parser.add_argument("--platforms", default="1,12", help="平台类型，仅允许 1,12")
    parser.add_argument("--check-only", action="store_true", help="只做离线合规自检")
    parser.add_argument("--tags", default="", help="逗号分隔话题标签")
    args = parser.parse_args(argv)

    platforms = [int(x) for x in args.platforms.split(",") if x.strip()]
    illegal = [p for p in platforms if p not in ALLOWED_PLATFORMS]
    if illegal:
        print(f"❌ 独立内容线只允许 {ALLOWED_PLATFORMS}，收到非法平台 {illegal}", file=sys.stderr)
        return 2

    day_dir = Path(args.dir).resolve() if args.dir else MATERIAL_ROOT / args.date
    if not day_dir.is_dir():
        print(f"❌ 当日内容目录不存在：{day_dir}\n请先创建 内容.md 与配图", file=sys.stderr)
        return 2

    try:
        assets = precheck(day_dir, platforms)
    except Exception as exc:
        print(f"❌ 合规自检未通过：{exc}", file=sys.stderr)
        return 2

    print(f"📦 {CONTENT_LINE} | {assets['date']} | 标题：{assets['title']}")
    print(f"🖼  配图 {len(assets['images'])} 张 | 正文 {assets['body_chars']} 字")
    print(f"🎯 平台：{[PLATFORM_LABELS[p] for p in platforms]}（全部只存草稿）")
    if args.check_only:
        print("🧪 离线自检通过：未启动浏览器、未写数据库、未发布")
        return 0

    tags = [t.strip() for t in args.tags.split(",") if t.strip()]
    results = []
    for ptype in platforms:
        label = PLATFORM_LABELS[ptype]
        accounts = _account_files(ptype)
        try:
            if ptype == 1:
                postVideo.post_note_xhs(
                    title=assets["title"],
                    files=assets["images"][:9],
                    note=assets["body"],
                    tags=tags,
                    account_file=accounts,
                    is_draft=True,      # 强制存草稿
                    dry_run=False,
                )
            else:
                postVideo.post_article_wechat_mp(
                    title=assets["title"],
                    body=assets["body"],
                    tags=tags,
                    account_file=accounts,
                    cover_path=assets["images"][0],
                    file_list=assets["images"],
                    is_draft=True,      # 强制存草稿
                    dry_run=False,
                )
            # 草稿箱回读核验：点了存草稿不等于落库
            hit = _readback_drafts(ptype, assets["title"])
            status = "已存草稿（回读核验通过）" if hit else "存草稿未通过回读核验"
            if not hit:
                raise RuntimeError(f"{label}草稿箱未命中本期标题，不得误判为已存草稿")
        except Exception as exc:
            status = f"失败：{exc}"
        results.append({"platform": label, "type": ptype, "status": status})
        print(f"  {'✅' if '通过' in status else '❌'} {label}: {status}")

    out = PROJECT_DIR / "logs" / f"ai_share_{assets['date']}_{datetime.now():%H%M%S}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps({"content_line": CONTENT_LINE, "title": assets["title"],
                    "date": assets["date"], "results": results},
                   ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"📊 明细：{out}")
    return 0 if all("通过" in r["status"] for r in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
