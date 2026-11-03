"""连接用户当前已打开的 Chrome（需先启动带 --remote-debugging-port 的 Chrome）。

使用 Playwright 的 connect_over_cdp 接管当前浏览器会话，这样脚本填写的内容
和存的草稿都会写入用户当前 Chrome 的 IndexedDB / 本地存储中，用户自己打开
Chrome 就能看到，避免脚本另开浏览器导致草稿“不共享缓存”。

macOS 启动示例：
    /Applications/Google Chrome.app/Contents/MacOS/Google Chrome \
        --remote-debugging-port=9222
"""

from __future__ import annotations

from playwright.async_api import async_playwright

DEFAULT_CDP_PORT = 9222


async def connect_current_chrome(port: int = DEFAULT_CDP_PORT):
    """连接当前 Chrome，返回 (playwright, browser, context, page)。

    - 复用用户当前已有的第一个浏览器上下文（保持登录态与缓存共享）
    - 在该上下文中新建一个页面用于填内容/存草稿
    - 不关闭用户的浏览器，脚本结束后仅断开 Playwright 连接
    """
    playwright = await async_playwright().start()
    browser = await playwright.chromium.connect_over_cdp(f"http://localhost:{port}")

    contexts = browser.contexts
    if contexts:
        context = contexts[0]
    else:
        context = await browser.new_context()

    pages = context.pages
    if pages:
        page = pages[0]
    else:
        page = await context.new_page()

    return playwright, browser, context, page
