# -*- coding: utf-8 -*-
"""「河狸云 AI 能力分享」独立个人 IP 内容线：小红书 + 微信公众号，只存草稿。

与主线 `publish_episode.py` 完全隔离：

- 微信公众号、小红书用于打造个人 IP，必须精细化：每天只发一条
  （同一份内容分别存两个平台的草稿），比蓝枢主线更精炼，不重复发布蓝枢内容。
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

    # 小红书用专用可见窗口填草稿并保留窗口（方案 A，人工复核后自行发布）
    .venv/bin/python scripts/publish_ai_share.py --date 2026-11-09 --platforms 1 --visible-xhs

⚠️ 本脚本**不提供** `--publish` 参数；传入任何发布意图参数都会直接报错退出。

⚠️ 小红书草稿只存在 `browser_profiles/xiaohongshu` 的 IndexedDB 里。
   `--visible-xhs` 会用独立进程拉起可见 Chrome（非默认 user data dir，
   因此 Chrome 136+ 允许开远程调试），Playwright 通过 CDP attach 填草稿，
   结束后只断开 CDP、**不关闭窗口**，草稿就留在你能直接看到的那个窗口里。
   不带该参数时小红书仍走无头（默认），草稿存在同一 profile 但你看不到。
"""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
from datetime import date, datetime
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Dict, List, Tuple

OMNIPOST_DIR = Path(__file__).resolve().parent.parent
PROJECT_DIR = OMNIPOST_DIR.parent
sys.path.insert(0, str(OMNIPOST_DIR))

from myUtils import postVideo  # noqa: E402
from myUtils import preflight  # noqa: E402
from scripts import compliance_check as cc  # noqa: E402

MATERIAL_ROOT = PROJECT_DIR / "河狸云蓝枢_自媒体物料包" / "AI能力分享内容线"
CONTENT_LINE = "河狸云AI能力分享（个人IP）"
ALLOWED_PLATFORMS = (1, 12)  # 小红书 / 微信公众号
PLATFORM_LABELS = {1: "小红书", 12: "微信公众号"}

# 独立内容线独有的红线：正文/标题不得出现「蓝枢」及其产品名。
# 用户明确要求「不指定蓝枢」，这里在零容忍词之外单独加一道闸门。
BRAND_EXCLUSIONS = ("蓝枢", "Planora", "河狸云·蓝枢")

# 小红书标题 20 字、公众号 64 字（与 preflight.TITLE_LIMITS 中 12 一致）
TITLE_LIMITS = {1: (1, 20), 12: (1, 64)}

# 个人 IP 线比蓝枢母本更精炼：去掉 Markdown 标记与话题标签后，
# 中文正文 180~320 字、最多 3 个自然段、最多 1 个小标题。
BODY_CHAR_RANGE = (180, 320)
MAX_PARAGRAPHS = 3
MAX_SUBHEADINGS = 1

# 与历史内容重复时的拦截阈值：标题/段落/长句任一相似即拒绝。
TITLE_SIMILARITY_LIMIT = 0.82
PARAGRAPH_SIMILARITY_LIMIT = 0.72
SENTENCE_SIMILARITY_LIMIT = 0.84
MIN_COMPARE_SENTENCE_CHARS = 14

# 个人 IP 不得复用蓝枢主线物料；目录名是历史约定，不靠文件名猜。
MAINLINE_MATERIAL_MARKERS = ("图片素材", "文案合集", "品牌标准资产")
MAINLINE_TEXT_ROOT = PROJECT_DIR / "河狸云蓝枢_自媒体物料包" / "文案合集"
CONTENT_LINE_ROOT_NAME = "AI能力分享内容线"
PUBLISHED_LOG = PROJECT_DIR / "logs" / "ai_share_published.json"


def _normalize_text(raw: str) -> str:
    """归一化为可比对的纯文本：去掉 Markdown 标记、话题标签与空白。"""
    text = re.sub(r"#[^\s#]+", "", raw)  # 先去话题标签，再去标题井号
    text = re.sub(r"(?m)^#+\s*", "", text)
    text = re.sub(r"\*\*|`|>|\||[-*_]{2,}", "", text)
    text = re.sub(r"\s+", "", text)
    return text


def _chinese_char_count(raw: str) -> int:
    return len(re.findall(r"[一-鿿]", _normalize_text(raw)))


def _paragraphs(raw: str) -> List[str]:
    lines = [line.strip() for line in raw.splitlines()]
    return [
        p for p in "\n".join(lines).split("\n\n")
        if p.strip() and not p.strip().startswith("# ")
    ]


def _sentences(text: str) -> List[str]:
    parts = re.split(r"[。！？!?；;\n]", text)
    return [p.strip() for p in parts if len(p.strip()) >= MIN_COMPARE_SENTENCE_CHARS]


def _similarity(left: str, right: str) -> float:
    left, right = _normalize_text(left), _normalize_text(right)
    if not left or not right:
        return 0.0
    return SequenceMatcher(None, left, right).ratio()


def _history_materials(current_dir: Path) -> List[Tuple[str, str]]:
    """收集主线发布历史、个人 IP 历史与文案合集母本，供去重比对。"""
    materials: List[Tuple[str, str]] = []

    db = OMNIPOST_DIR / "db" / "database.db"
    if db.exists():
        with sqlite3.connect(str(db)) as conn:
            rows = conn.execute(
                "select title, content from publish_history where title is not null"
            ).fetchall()
        for title, content in rows:
            materials.append((f"publish_history: {title or ''}", f"{title or ''}\n{content or ''}"))

    if MAINLINE_TEXT_ROOT.exists():
        for path in MAINLINE_TEXT_ROOT.rglob("*.md"):
            if current_dir in path.parents:
                continue
            materials.append((f"文案合集: {path.name}", path.read_text(encoding="utf-8", errors="ignore")))

    if MATERIAL_ROOT.exists():
        for path in MATERIAL_ROOT.rglob("内容.md"):
            if path.parent == current_dir:
                continue
            materials.append((f"个人IP历史: {path.parent.name}", path.read_text(encoding="utf-8", errors="ignore")))

    if PUBLISHED_LOG.exists():
        try:
            records = json.loads(PUBLISHED_LOG.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            records = []
        for item in records if isinstance(records, list) else []:
            materials.append(
                (f"已发记录: {item.get('date', '')}", f"{item.get('title', '')}\n{item.get('body', '')}")
            )
    return materials


def _historical_title(label: str, raw: str) -> str:
    for line in raw.splitlines():
        if line.strip().startswith("# "):
            return line.strip()[2:].strip()
    if label.startswith("publish_history: "):
        return label.split(": ", 1)[1]
    return ""


def _find_repetition(title: str, body: str, current_dir: Path) -> List[str]:
    """检查是否复用主线母本/历史发布的标题、段落或长句。

    历史正文通常是一整段长文，不能只按句号切分：再用滑动窗口覆盖
    与当期句子等长的片段，拦住「从主线长段里截一句」或轻微改写。
    """
    current_paragraphs = [p for p in _paragraphs(body) if not p.lstrip().startswith("#")]
    current_sentences = _sentences(_normalize_text(body))
    current_title = _normalize_text(title)

    for label, raw in _history_materials(current_dir):
        normalized = _normalize_text(raw)
        history_title = _historical_title(label, raw)
        if history_title and _similarity(title, history_title) >= TITLE_SIMILARITY_LIMIT:
            return [f"标题与{label}高度相似，个人 IP 不得重复发布蓝枢/历史选题"]
        if len(current_title) >= 8 and current_title in normalized:
            return [f"本期标题出现在{label}正文中，疑似复用主线内容"]

        history_sentences = _sentences(normalized)
        for sentence in current_sentences:
            if any(_similarity(sentence, old) >= SENTENCE_SIMILARITY_LIMIT for old in history_sentences):
                return [f"正文长句与{label}重复，个人 IP 必须单独撰写"]
            n = len(sentence)
            for i in range(0, max(0, len(normalized) - n + 1), 4):
                if _similarity(sentence, normalized[i:i + n]) >= SENTENCE_SIMILARITY_LIMIT:
                    return [f"正文片段与{label}重复，个人 IP 必须单独撰写"]

        history_paragraphs = _paragraphs(raw)
        for paragraph in current_paragraphs:
            if any(_similarity(paragraph, old) >= PARAGRAPH_SIMILARITY_LIMIT for old in history_paragraphs):
                return [f"正文段落与{label}高度相似，疑似压缩改写蓝枢母本"]
    return []


def _assert_one_daily_content(day_dir: Path) -> None:
    """目录层落实「每天一条」：一个日期目录只能有一份 内容.md。"""
    md_files = [
        p for p in day_dir.iterdir()
        if p.is_file() and p.name.startswith("内容") and p.suffix.lower() == ".md"
    ]
    if len(md_files) != 1 or md_files[0].name != "内容.md":
        raise ValueError("每日目录必须且只能有 1 份 内容.md（每天一条）")


def _assert_distinct_images(images: List[str], day_dir: Path) -> None:
    """个人 IP 配图必须是当日专属，不能复用主线图片素材或往期图片。"""
    day_root = day_dir.resolve()
    for image in images:
        path = Path(image).resolve()
        rel = path.relative_to(PROJECT_DIR).as_posix() if path.is_relative_to(PROJECT_DIR) else path.as_posix()
        if CONTENT_LINE_ROOT_NAME not in rel:
            raise ValueError(f"配图不属于个人 IP 内容线，禁止复用主线物料：{rel}")
        if day_root.name not in rel or path.parent != day_root:
            raise ValueError(f"配图必须放在当日专属目录，禁止复用往期图片：{rel}")
        if any(marker in rel for marker in MAINLINE_MATERIAL_MARKERS):
            raise ValueError(f"配图路径命中主线物料目录：{rel}")


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
    """离线合规自检：精炼、去重、每日一条、品牌与合规任一不过即中止。"""
    _assert_one_daily_content(day_dir)
    content = _parse_content(day_dir / "内容.md")
    images = _collect_images(day_dir)
    _assert_distinct_images(images, day_dir)
    text = content["raw"]

    problems: List[str] = []
    for word in BRAND_EXCLUSIONS:
        if word in text:
            problems.append(f"个人 IP 内容线禁止出现「{word}」（不指定蓝枢）")
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

    body_chars = _chinese_char_count(content["body"])
    low, high = BODY_CHAR_RANGE
    if not (low <= body_chars <= high):
        problems.append(
            f"正文 {body_chars} 个中文字符，个人 IP 要求 {low}~{high}，"
            "不得把蓝枢长文压缩后重发"
        )
    paragraphs = _paragraphs(content["body"])
    if len(paragraphs) > MAX_PARAGRAPHS:
        problems.append(f"正文 {len(paragraphs)} 个自然段，最多 {MAX_PARAGRAPHS} 个")
    subheadings = [
        line for line in content["body"].splitlines()
        if re.match(r"^#{2,6}\s+", line.strip())
    ]
    if len(subheadings) > MAX_SUBHEADINGS:
        problems.append(f"正文含 {len(subheadings)} 个小标题，最多 {MAX_SUBHEADINGS} 个")

    if not problems:
        problems = _find_repetition(content["title"], content["body"], day_dir)
    if problems:
        raise ValueError("合规自检未通过：\n  - " + "\n  - ".join(problems))

    return {
        "date": day_dir.name,
        "title": content["title"],
        "body": content["body"],
        "images": images,
        "platforms": platforms,
        "body_chars": body_chars,
        "paragraphs": len(paragraphs),
        "subheadings": len(subheadings),
    }


def _account_files(ptype: int) -> List[str]:
    """从 user_info 读取账号 cookie 文件名（不改数据库）。"""
    db = OMNIPOST_DIR / "db" / "database.db"
    with sqlite3.connect(str(db)) as conn:
        rows = conn.execute(
            "select filePath, status from user_info where type = ?", (ptype,)
        ).fetchall()
    files = [r[0] for r in rows if r[0]]
    if not files:
        raise RuntimeError(f"{PLATFORM_LABELS[ptype]}（type={ptype}）未绑定账号")
    return files


XHS_PROFILE = OMNIPOST_DIR / "browser_profiles" / "xiaohongshu"
XHS_CDP_PORT = 9333
XHS_CDP_URL = f"http://127.0.0.1:{XHS_CDP_PORT}"


def _cdp_port_ready(port: int) -> bool:
    import socket
    s = socket.socket()
    s.settimeout(0.5)
    try:
        s.connect(("127.0.0.1", port))
        return True
    except OSError:
        return False
    finally:
        s.close()


def _start_visible_xhs_window() -> str:
    """用独立进程拉起可见 Chrome（专用 profile），返回 CDP 地址。

    Chrome 136+ 禁止对默认 user data dir 开远程调试，但我们用的是非标准
    目录 `browser_profiles/xiaohongshu`，合法且草稿与无头模式共用一份
    IndexedDB。独立进程启动是留窗的关键：Playwright 自己 launch 的 Chrome
    会在 driver 退出时被一并杀掉，attach 模式则不会。
    """
    import shutil
    import subprocess
    import time

    if _cdp_port_ready(XHS_CDP_PORT):
        print(f"♻️  复用已打开的小红书可见窗口（{XHS_CDP_URL}）")
        return XHS_CDP_URL

    chrome = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
    if not Path(chrome).exists():
        raise RuntimeError(f"未找到系统 Chrome：{chrome}")
    XHS_PROFILE.mkdir(parents=True, exist_ok=True)
    proc = subprocess.Popen(
        [
            chrome,
            f"--remote-debugging-port={XHS_CDP_PORT}",
            f"--user-data-dir={XHS_PROFILE}",
            "--no-first-run",
            "--no-default-browser-check",
            "--new-window",
            "https://creator.xiaohongshu.com/publish/publish?target=image",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    for _ in range(60):
        if _cdp_port_ready(XHS_CDP_PORT):
            break
        time.sleep(0.5)
    else:
        raise RuntimeError(f"小红书可见窗口启动失败（{XHS_CDP_PORT} 端口未就绪）")
    print(f"🪟 小红书可见窗口已打开（pid={proc.pid}），本脚本不会关闭它")
    return XHS_CDP_URL


def _wechat_token(href: str) -> str:
    """从公众号后台链接里提取当前有效的 token 参数。"""
    match = re.search(r"[?&]token=(\d+)", href or "")
    return match.group(1) if match else ""


def _readback_drafts(ptype: int, title: str, cdp_endpoint: str = "", require_image: bool = False) -> bool:
    """存完草稿后回平台草稿箱按本期标题回读核验。

    ⚠️ 小红书草稿存在**固定持久化 profile 的 IndexedDB**，用临时
    `new_context(storage_state=...)` 读不到（曾因此误判）。因此：
      - 留窗模式：直接 attach 到可见窗口，读它自己的页面；
      - 无头模式：用 `launch_persistent_context` 打开同一个 profile。
    返回 True 表示命中；抛错表示核验不可用，由调用方如实报告。
    """
    import asyncio
    from playwright.async_api import async_playwright
    from conf import LOCAL_CHROME_PATH
    from utils.base_social_media import set_init_script

    cookie = OMNIPOST_DIR / "cookiesFile" / _account_files(ptype)[0]

    async def _run() -> bool:
        async with async_playwright() as pw:
            if ptype == 1:
                if cdp_endpoint:
                    browser = await pw.chromium.connect_over_cdp(cdp_endpoint)
                    ctx = browser.contexts[0]
                else:
                    ctx = await pw.chromium.launch_persistent_context(
                        user_data_dir=str(XHS_PROFILE),
                        headless=True,
                        channel="chrome",
                    )
                url = "https://creator.xiaohongshu.com/publish/publish?target=image"
            else:
                # 微信公众平台在 bundled Chromium 下必崩，必须走系统 Chrome
                browser = await pw.chromium.launch(
                    headless=True, channel="chrome",
                    **({"executable_path": LOCAL_CHROME_PATH} if LOCAL_CHROME_PATH else {}),
                )
                ctx = await browser.new_context(
                    storage_state=str(cookie), viewport={"width": 1440, "height": 900}
                )
                url = "https://mp.weixin.qq.com/"
            ctx = await set_init_script(ctx)
            try:
                page = ctx.pages[0] if ctx.pages else await ctx.new_page()
                await page.goto(url, timeout=60000, wait_until="domcontentloaded")
                await page.wait_for_timeout(7000)
                # 公众号草稿箱 URL 必须带当前后台 token 与正确 type。
                # 固定写 type=9 会打开「发表记录」而非草稿箱（新版为 type=77），
                # 因此优先使用后台「草稿箱」链接自带的完整 href。
                if ptype != 1:
                    draft_link = page.locator(
                        'a[href*="action=list_card"][href*="type=77"], '
                        'a[href*="action=list_card"]'
                    ).first
                    draft_url = ""
                    if await draft_link.count():
                        href = await draft_link.get_attribute("href") or ""
                        token = _wechat_token(href)
                        if href.startswith("http"):
                            draft_url = href
                        elif href.startswith("/"):
                            draft_url = f"https://mp.weixin.qq.com{href}"
                        elif token:
                            draft_url = (
                                "https://mp.weixin.qq.com/cgi-bin/appmsg?"
                                f"type=77&action=list_card&lang=zh_CN&token={token}"
                            )
                    if not draft_url:
                        token_link = page.locator('a[href*="token="]').first
                        token = ""
                        if await token_link.count():
                            token = _wechat_token(
                                await token_link.get_attribute("href") or ""
                            )
                        if token:
                            draft_url = (
                                "https://mp.weixin.qq.com/cgi-bin/appmsg?"
                                f"type=77&action=list_card&lang=zh_CN&token={token}"
                            )
                    if draft_url:
                        await page.goto(draft_url, timeout=60000, wait_until="domcontentloaded")
                        await page.wait_for_timeout(6000)
                text = await page.evaluate("() => document.body.innerText")
                # 草稿箱把普通空格渲染成 NBSP（\xa0），直接按原字符串匹配会误判未命中。
                normalized_text = re.sub(r"\s+", "", text.replace("\xa0", " "))
                normalized_title = re.sub(r"\s+", "", title.replace("\xa0", " "))
                card = page.locator(".publish_card_container").filter(has_text=title)
                card_count = await card.count()
                if not card_count and ptype != 1:
                    # NBSP 会让 filter(has_text=title) 落空；按归一化文本定位本期卡片。
                    matched_index = await page.evaluate(
                        """(t) => [...document.querySelectorAll('.publish_card_container')]
                            .findIndex(el => (el.innerText || '')
                                .replace(/[\\s\\u00a0]/g, '').includes(t))""",
                        normalized_title,
                    )
                    if matched_index >= 0:
                        card = page.locator(".publish_card_container").nth(matched_index)
                        card_count = await card.count()
                hit = normalized_title in normalized_text or card_count > 0
                if hit and require_image and ptype != 1:
                    # 配图回读不能只凭全页文本命中：必须精确找到本期草稿卡片，
                    # 且卡片封面或卡片内的公众号素材图真实存在。草稿箱曾出现
                    # 「内容存进去了但封面是 url("")」的假成功，不得只截图宣称成功。
                    if not card_count:
                        hit = False
                    else:
                        hit = await card.first.evaluate(
                            r"""el => {
                                const coverOk = [...el.querySelectorAll(
                                    '.weui-desktop-publish__cover__thumb,' +
                                    '.card_appmsg_thumb,[class*="cover__thumb"]'
                                )].some(node => {
                                    const bg = getComputedStyle(node).backgroundImage || '';
                                    if (!bg || bg === 'none') return false;
                                    if (/url\(\s*["']?\s*["']?\s*\)/.test(bg)) return false;
                                    if (bg.includes('url("")') || bg.includes("url('')")) return false;
                                    return true;
                                });
                                if (coverOk) return true;
                                // 新版草稿箱可能把封面渲染成内联 style / 懒加载 img，
                                // 卡片里出现公众号素材域名的真实图片也算配图已落库。
                                const html = el.innerHTML || '';
                                if (/mmbiz\.(qpic|qlogo)\.cn/i.test(html)) return true;
                                return [...el.querySelectorAll('img')].some(img => {
                                    const s = img.getAttribute('src') ||
                                              img.getAttribute('data-src') || '';
                                    return !!s && /mmbiz\.(qpic|qlogo)\.cn/i.test(s);
                                });
                            }"""
                        )
                # 小红书发布页只显示草稿箱计数，标题在列表弹层里；
                # 若正文没命中，再尝试展开草稿箱列表回读。
                if not hit and ptype == 1:
                    try:
                        await page.get_by_text(re.compile(r"草稿箱\(\d+\)")).first.click()
                        await page.wait_for_timeout(2500)
                        # 草稿箱抽屉默认停在「视频笔记(0)」；图文稿要切到
                        # 「图文笔记(N)」标签才能按标题回读，否则会误判草稿未落库。
                        image_tab = page.get_by_text(re.compile(r"图文笔记\(\d+\)"))
                        if await image_tab.count():
                            await image_tab.first.click()
                            await page.wait_for_timeout(4000)
                        text = await page.evaluate("() => document.body.innerText")
                        hit = title in text
                    except Exception:
                        pass
                return hit
            finally:
                if ptype == 1 and cdp_endpoint:
                    await browser.close()  # 只断 CDP，窗口保留
                elif ptype == 1:
                    await ctx.close()
                else:
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
    parser.add_argument(
        "--visible-xhs", action="store_true",
        help="小红书用专用可见窗口填草稿并保留窗口供人工复核（其余平台仍无头）",
    )
    parser.add_argument(
        "--patch-existing-wechat-draft", action="store_true",
        help="只修补公众号当期既有草稿的正文图与封面，不新建草稿（仅 --platforms 12）",
    )
    args = parser.parse_args(argv)

    platforms = [int(x) for x in args.platforms.split(",") if x.strip()]
    illegal = [p for p in platforms if p not in ALLOWED_PLATFORMS]
    if illegal:
        print(f"❌ 独立内容线只允许 {ALLOWED_PLATFORMS}，收到非法平台 {illegal}", file=sys.stderr)
        return 2
    if args.patch_existing_wechat_draft and platforms != [12]:
        print("❌ --patch-existing-wechat-draft 只允许与 --platforms 12 一起使用", file=sys.stderr)
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
    print(
        f"🖼  配图 {len(assets['images'])} 张 | 正文 {assets['body_chars']} 个中文字符"
        f" | {assets['paragraphs']} 段 | 每日 1 条"
    )
    print(f"🎯 平台：{[PLATFORM_LABELS[p] for p in platforms]}（同一份内容、全部只存草稿）")
    if args.check_only:
        print("🧪 离线自检通过：精炼/去重/每日单条，未启动浏览器、未写数据库、未发布")
        return 0

    tags = [t.strip() for t in args.tags.split(",") if t.strip()]
    cdp_endpoint = ""
    if args.visible_xhs and 1 in platforms:
        # 用户明确选择方案 A：小红书用专用可见窗口，存完草稿保留窗口。
        # 其余平台（公众号）继续走无头，不受影响。
        try:
            cdp_endpoint = _start_visible_xhs_window()
        except Exception as exc:
            print(f"❌ 可见窗口启动失败：{exc}", file=sys.stderr)
            return 2
        print("ℹ️  小红书草稿只在该窗口的 profile 里；本脚本结束不会关闭它")

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
                    cdp_endpoint=cdp_endpoint,
                    keep_window_open=bool(cdp_endpoint),
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
                    existing_draft_title=(
                        assets["title"] if args.patch_existing_wechat_draft else ""
                    ),
                    replacement_title=(
                        assets["title"] if args.patch_existing_wechat_draft else ""
                    ),
                )
            # 草稿箱回读核验：点了存草稿不等于落库。小红书必须读同一个
            # 持久化 profile（留窗模式下直接 attach 到那个可见窗口）。
            hit = _readback_drafts(
                ptype,
                assets["title"],
                cdp_endpoint if ptype == 1 else "",
                require_image=(ptype == 12),
            )
            status = "已存草稿（标题与配图回读核验通过）" if hit else "存草稿未通过回读核验"
            if not hit:
                raise RuntimeError(
                    f"{label}草稿箱未命中本期标题或封面仍为空，不得误判为已存草稿"
                    + ("（可见窗口仍保留，可人工复核）" if cdp_endpoint and ptype == 1 else "")
                )
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
