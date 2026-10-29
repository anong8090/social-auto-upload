import asyncio
import sqlite3

from playwright.async_api import TimeoutError as PlaywrightTimeoutError, async_playwright

from myUtils.auth import check_cookie
from utils.base_social_media import set_init_script
import uuid
from pathlib import Path
from conf import BASE_DIR, LOCAL_CHROME_HEADLESS, LOCAL_CHROME_PATH
from uploader.bilibili_uploader.runtime import run_biliup_command

# 统一获取浏览器启动配置（防风控+引入本地浏览器）
def get_browser_options():
    options = {
        'headless': LOCAL_CHROME_HEADLESS,
        'args': [
            '--disable-blink-features=AutomationControlled',  # 核心防爬屏蔽：去掉 window.navigator.webdriver 标签
            '--lang=zh-CN',
            '--disable-infobars',
            '--start-maximized'
        ]
    }
    # 如果用户在 conf.py 里配置了本地 Chrome，就用本地的，这样成功率极高
    if LOCAL_CHROME_PATH:
        options['executable_path'] = LOCAL_CHROME_PATH
    else:
        # 未配置路径时使用系统已安装的 Chrome，避免 Playwright Chromium 未安装导致启动失败
        options['channel'] = 'chrome'

    return options

# 等待二维码图片的 src 就绪（页面渲染慢时立即读取会拿到空值，导致前端一直停留在"请求中"）
async def wait_for_qr_src(locator, timeout_s: int = 60):
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout_s
    while True:
        try:
            src = await locator.get_attribute("src", timeout=5000)
        except Exception:
            src = None
        if src:
            return src
        if loop.time() >= deadline:
            return None
        await asyncio.sleep(1)

# 抖音登录
async def douyin_cookie_gen(id,status_queue):
    url_changed_event = asyncio.Event()
    async def on_url_change():
        # 检查是否是主框架的变化
        if page.url != original_url:
            url_changed_event.set()
    async with async_playwright() as playwright:
        options = get_browser_options()
        # Make sure to run headed.
        browser = await playwright.chromium.launch(**options)
        # Setup context however you like.
        context = await browser.new_context()  # Pass any options
        context = await set_init_script(context)
        # Pause the page, and start recording manually.
        page = await context.new_page()
        await page.goto("https://creator.douyin.com/")
        original_url = page.url
        img_locator = page.get_by_role("img", name="二维码")
        src = await wait_for_qr_src(img_locator)
        if not src:
            print("❌ 抖音二维码加载超时")
            await page.close()
            await context.close()
            await browser.close()
            status_queue.put("500")
            return None
        print("✅ 图片地址:", src)
        status_queue.put(src)
        # 监听页面的 'framenavigated' 事件，只关注主框架的变化
        page.on('framenavigated',
                lambda frame: asyncio.create_task(on_url_change()) if frame == page.main_frame else None)
        try:
            # 等待 URL 变化或超时
            await asyncio.wait_for(url_changed_event.wait(), timeout=200)  # 最多等待 200 秒
            print("监听页面跳转成功")
        except asyncio.TimeoutError:
            print("监听页面跳转超时")
            await page.close()
            await context.close()
            await browser.close()
            status_queue.put("500")
            return None
        uuid_v1 = uuid.uuid1()
        print(f"UUID v1: {uuid_v1}")
        # 确保cookiesFile目录存在
        cookies_dir = Path(BASE_DIR / "cookiesFile")
        cookies_dir.mkdir(exist_ok=True)
        await context.storage_state(path=cookies_dir / f"{uuid_v1}.json")
        result = await check_cookie(3, f"{uuid_v1}.json")
        if not result:
            status_queue.put("500")
            await page.close()
            await context.close()
            await browser.close()
            return None
        await page.close()
        await context.close()
        await browser.close()
        with sqlite3.connect(Path(BASE_DIR / "db" / "database.db")) as conn:
            cursor = conn.cursor()
            cursor.execute('''
                                INSERT INTO user_info (type, filePath, userName, status)
                                VALUES (?, ?, ?, ?)
                                ''', (3, f"{uuid_v1}.json", id, 1))
            conn.commit()
            print("✅ 用户状态已记录")
        status_queue.put("200")


# 视频号登录：跳出独立 PC 浏览器窗口手动授权
async def get_tencent_cookie(id, status_queue):
    browser = None
    context = None
    page = None
    try:
        # 通知前端进入手动登录模式，弹窗提示在浏览器中完成授权
        status_queue.put("MANUAL_LOGIN")
        print(f"🟢 开始视频号登录（跳出独立 PC 浏览器窗口）: id={id}", flush=True)

        async with async_playwright() as playwright:
            options = get_browser_options()
            options['headless'] = False
            print(f"🟢 启动浏览器 options={ {k: v for k, v in options.items() if k != 'args'} }", flush=True)

            browser = await playwright.chromium.launch(**options)
            context = await browser.new_context(
                viewport={"width": 1280, "height": 850},
                locale="zh-CN"
            )
            context = await set_init_script(context)
            page = await context.new_page()

            print("🟢 打开微信视频号助手登录页...", flush=True)
            await page.goto("https://channels.weixin.qq.com/login.html", wait_until="domcontentloaded", timeout=60000)
            print(f"🟢 登录页已打开: {page.url}，请在浏览器中完成微信授权...", flush=True)

            # 轮询等待用户在浏览器中完成授权并进入后台平台页（最多等 200 秒）
            logged_in = False
            for _ in range(100):
                await asyncio.sleep(2)
                cur_url = page.url or ""
                if "/platform/" in cur_url or "post/create" in cur_url:
                    print("✅ 检测到已成功进入创作者平台！会话已建立", flush=True)
                    logged_in = True
                    await page.wait_for_timeout(3000)  # 留出时间让 session cookie 充分落定
                    break

            if not logged_in:
                print("❌ 视频号登录超时：未在规定时间内进入平台后台", flush=True)
                status_queue.put("500")
                return None

            uuid_v1 = uuid.uuid1()
            print(f"UUID v1: {uuid_v1}", flush=True)
            cookies_dir = Path(BASE_DIR / "cookiesFile")
            cookies_dir.mkdir(exist_ok=True)
            saved_cookie_file = cookies_dir / f"{uuid_v1}.json"
            await context.storage_state(path=saved_cookie_file)

            # 校验 cookie 是否有效
            result = await check_cookie(2, f"{uuid_v1}.json")
            if not result:
                print("❌ 视频号登录保存的 cookie 校验未通过", flush=True)
                status_queue.put("500")
                return None

            with sqlite3.connect(Path(BASE_DIR / "db" / "database.db")) as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT id FROM user_info WHERE userName = ? AND type = 2", (id,))
                existing = cursor.fetchone()
                if existing:
                    cursor.execute("UPDATE user_info SET filePath = ?, status = 1 WHERE id = ?", (f"{uuid_v1}.json", existing[0]))
                    print(f"✅ 已更新已有视频号用户: id={existing[0]}, userName={id}", flush=True)
                else:
                    cursor.execute(
                        "INSERT INTO user_info (type, filePath, userName, status) VALUES (?, ?, ?, 1)",
                        (2, f"{uuid_v1}.json", id)
                    )
                    print(f"✅ 已新增视频号用户: userName={id}", flush=True)
                conn.commit()

            status_queue.put("200")
            print("✅ 视频号登录流程全部完成！", flush=True)
            return True

    except Exception as e:
        print(f"❌ 视频号登录异常: {e}", flush=True)
        import traceback
        traceback.print_exc()
        try:
            status_queue.put("500")
        except Exception:
            pass
        return None
    finally:
        try:
            if page:
                await page.close()
        except Exception:
            pass
        try:
            if context:
                await context.close()
        except Exception:
            pass
        try:
            if browser:
                await browser.close()
        except Exception:
            pass


# 快手登录
async def get_ks_cookie(id,status_queue):
    url_changed_event = asyncio.Event()
    async def on_url_change():
        # 检查是否是主框架的变化
        if page.url != original_url:
            url_changed_event.set()
    async with async_playwright() as playwright:
        options = {
            'args': [
                '--lang en-GB'
            ],
            'headless': LOCAL_CHROME_HEADLESS,  # Set headless option here
        }
        # Make sure to run headed.
        browser = await playwright.chromium.launch(**options)
        # Setup context however you like.
        context = await browser.new_context()  # Pass any options
        context = await set_init_script(context)
        # Pause the page, and start recording manually.
        page = await context.new_page()
        await page.goto("https://cp.kuaishou.com")

        # 定位并点击“立即登录”按钮（类型为 link）
        await page.get_by_role("link", name="立即登录").click()
        await page.get_by_text("扫码登录").click()
        img_locator = page.get_by_role("img", name="qrcode")
        src = await wait_for_qr_src(img_locator)
        if not src:
            print("❌ 快手二维码加载超时")
            await page.close()
            await context.close()
            await browser.close()
            status_queue.put("500")
            return None
        original_url = page.url
        print("✅ 图片地址:", src)
        status_queue.put(src)
        # 监听页面的 'framenavigated' 事件，只关注主框架的变化
        page.on('framenavigated',
                lambda frame: asyncio.create_task(on_url_change()) if frame == page.main_frame else None)

        try:
            # 等待 URL 变化或超时
            await asyncio.wait_for(url_changed_event.wait(), timeout=200)  # 最多等待 200 秒
            print("监听页面跳转成功")
        except asyncio.TimeoutError:
            status_queue.put("500")
            print("监听页面跳转超时")
            await page.close()
            await context.close()
            await browser.close()
            return None
        uuid_v1 = uuid.uuid1()
        print(f"UUID v1: {uuid_v1}")
        # 确保cookiesFile目录存在
        cookies_dir = Path(BASE_DIR / "cookiesFile")
        cookies_dir.mkdir(exist_ok=True)
        await context.storage_state(path=cookies_dir / f"{uuid_v1}.json")
        result = await check_cookie(4, f"{uuid_v1}.json")
        if not result:
            status_queue.put("500")
            await page.close()
            await context.close()
            await browser.close()
            return None
        await page.close()
        await context.close()
        await browser.close()

        with sqlite3.connect(Path(BASE_DIR / "db" / "database.db")) as conn:
            cursor = conn.cursor()
            cursor.execute('''
                                        INSERT INTO user_info (type, filePath, userName, status)
                                        VALUES (?, ?, ?, ?)
                                        ''', (4, f"{uuid_v1}.json", id, 1))
            conn.commit()
            print("✅ 用户状态已记录")
        status_queue.put("200")

# 小红书登录
async def xiaohongshu_cookie_gen(id,status_queue):
    url_changed_event = asyncio.Event()

    async def on_url_change():
        # 检查是否是主框架的变化
        if page.url != original_url:
            url_changed_event.set()

    async with async_playwright() as playwright:
        options = {
            'args': [
                '--lang en-GB'
            ],
            'headless': LOCAL_CHROME_HEADLESS,  # Set headless option here
        }
        # Make sure to run headed.
        browser = await playwright.chromium.launch(**options)
        # Setup context however you like.
        context = await browser.new_context()  # Pass any options
        context = await set_init_script(context)
        # Pause the page, and start recording manually.
        page = await context.new_page()
        await page.goto("https://creator.xiaohongshu.com/")
        await page.locator('img.css-wemwzq').click()

        img_locator = page.get_by_role("img").nth(2)
        src = await wait_for_qr_src(img_locator)
        if not src:
            print("❌ 小红书二维码加载超时")
            await page.close()
            await context.close()
            await browser.close()
            status_queue.put("500")
            return None
        original_url = page.url
        print("✅ 图片地址:", src)
        status_queue.put(src)
        # 监听页面的 'framenavigated' 事件，只关注主框架的变化
        page.on('framenavigated',
                lambda frame: asyncio.create_task(on_url_change()) if frame == page.main_frame else None)

        try:
            # 等待 URL 变化或超时
            await asyncio.wait_for(url_changed_event.wait(), timeout=200)  # 最多等待 200 秒
            print("监听页面跳转成功")
        except asyncio.TimeoutError:
            status_queue.put("500")
            print("监听页面跳转超时")
            await page.close()
            await context.close()
            await browser.close()
            return None
        uuid_v1 = uuid.uuid1()
        print(f"UUID v1: {uuid_v1}")
        # 确保cookiesFile目录存在
        cookies_dir = Path(BASE_DIR / "cookiesFile")
        cookies_dir.mkdir(exist_ok=True)
        await context.storage_state(path=cookies_dir / f"{uuid_v1}.json")
        result = await check_cookie(1, f"{uuid_v1}.json")
        if not result:
            status_queue.put("500")
            await page.close()
            await context.close()
            await browser.close()
            return None
        await page.close()
        await context.close()
        await browser.close()

        with sqlite3.connect(Path(BASE_DIR / "db" / "database.db")) as conn:
            cursor = conn.cursor()
            cursor.execute('''
                           INSERT INTO user_info (type, filePath, userName, status)
                           VALUES (?, ?, ?, ?)
                           ''', (1, f"{uuid_v1}.json", id, 1))
            conn.commit()
            print("✅ 用户状态已记录")
        status_queue.put("200")

# 百家号登录（手动登录，非二维码）
async def baijiahao_cookie_gen(id, status_queue):
    browser = None
    context = None
    page = None
    try:
        status_queue.put("MANUAL_LOGIN")
        print(f"🟢 开始百家号登录: id={id}")
        async with async_playwright() as playwright:
            options = get_browser_options()
            options['headless'] = False
            browser = await playwright.chromium.launch(**options)
            context = await browser.new_context()
            context = await set_init_script(context)
            page = await context.new_page()
            await page.goto(
                "https://baijiahao.baidu.com/builder/theme/bjh/login",
                timeout=60000,
                wait_until="domcontentloaded",
            )
            print(f"🟢 百家号登录页已打开: {page.url}")
            try:
                await page.wait_for_url(
                    "https://baijiahao.baidu.com/builder/rc/**",
                    timeout=200_000,
                )
                print("✅ 百家号登录成功，检测到页面跳转")
            except asyncio.TimeoutError:
                print("❌ 百家号登录超时")
                status_queue.put("500")
                return None

            uuid_v1 = uuid.uuid1()
            print(f"UUID v1: {uuid_v1}")
            cookies_dir = Path(BASE_DIR / "cookiesFile")
            cookies_dir.mkdir(exist_ok=True)
            await context.storage_state(path=cookies_dir / f"{uuid_v1}.json")

            result = await check_cookie(5, f"{uuid_v1}.json")
            if not result:
                print("⚠️ cookie 即时校验未通过，仍保存账号，请稍后刷新验证")

            with sqlite3.connect(Path(BASE_DIR / "db" / "database.db")) as conn:
                cursor = conn.cursor()
                cursor.execute('''
                    INSERT INTO user_info (type, filePath, userName, status)
                    VALUES (?, ?, ?, ?)
                ''', (5, f"{uuid_v1}.json", id, 1 if result else 0))
                conn.commit()
                print("✅ 百家号用户状态已记录")
            status_queue.put("200")
            return True
    except Exception as e:
        print(f"❌ 百家号登录异常: {e}")
        import traceback
        traceback.print_exc()
        try:
            status_queue.put("500")
        except Exception:
            pass
        return None
    finally:
        try:
            if page:
                await page.close()
        except Exception:
            pass
        try:
            if context:
                await context.close()
        except Exception:
            pass
        try:
            if browser:
                await browser.close()
        except Exception:
            pass


# 今日头条登录（扫码/手动，非二维码 SSE 推送，与百家号类似）
async def toutiao_cookie_gen(id, status_queue):
    browser = None
    context = None
    page = None
    try:
        # 尽早通知前端进入「手动登录」态，避免浏览器启动慢时一直转圈
        status_queue.put("MANUAL_LOGIN")
        print(f"🟢 开始今日头条登录: id={id}")
        async with async_playwright() as playwright:
            # 登录必须有头模式，方便用户扫码
            options = get_browser_options()
            options['headless'] = False
            print(f"🟢 启动浏览器 options={ {k: v for k, v in options.items() if k != 'args'} }")
            browser = await playwright.chromium.launch(**options)
            context = await browser.new_context()
            context = await set_init_script(context)
            page = await context.new_page()
            print("🟢 打开今日头条登录页...")
            await page.goto("https://mp.toutiao.com/auth/page/login", timeout=60000, wait_until="domcontentloaded")
            print(f"🟢 登录页已打开: {page.url}")
            try:
                # Playwright 的 callable 参数可能是 URL 对象，统一转 str
                await page.wait_for_url(
                    lambda url: ("/profile_v4" in str(url)) and ("/auth/page/login" not in str(url)),
                    timeout=200_000,
                )
                print("✅ 今日头条登录成功，检测到进入创作者后台")
            except asyncio.TimeoutError:
                print("❌ 今日头条登录超时")
                status_queue.put("500")
                return None

            uuid_v1 = uuid.uuid1()
            print(f"UUID v1: {uuid_v1}")
            cookies_dir = Path(BASE_DIR / "cookiesFile")
            cookies_dir.mkdir(exist_ok=True)
            await context.storage_state(path=cookies_dir / f"{uuid_v1}.json")

            # cookie 校验失败也不要卡死前端；仍写入账号，状态后续可再验证
            result = await check_cookie(7, f"{uuid_v1}.json")
            if not result:
                print("⚠️ cookie 即时校验未通过，仍保存账号，请稍后刷新验证")

            with sqlite3.connect(Path(BASE_DIR / "db" / "database.db")) as conn:
                cursor = conn.cursor()
                cursor.execute('''
                    INSERT INTO user_info (type, filePath, userName, status)
                    VALUES (?, ?, ?, ?)
                ''', (7, f"{uuid_v1}.json", id, 1 if result else 0))
                conn.commit()
                print("✅ 今日头条用户状态已记录")
            status_queue.put("200")
            return True
    except Exception as e:
        print(f"❌ 今日头条登录异常: {e}")
        import traceback
        traceback.print_exc()
        try:
            status_queue.put("500")
        except Exception:
            pass
            return None
    finally:
        try:
            if page:
                await page.close()
        except Exception:
            pass
        try:
            if context:
                await context.close()
        except Exception:
            pass
        try:
            if browser:
                await browser.close()
        except Exception:
            pass


async def sohu_cookie_gen(id, status_queue):
    browser = None
    context = None
    page = None
    try:
        status_queue.put("MANUAL_LOGIN")
        print(f"🟢 开始搜狐号登录: id={id}")
        async with async_playwright() as playwright:
            options = get_browser_options()
            options['headless'] = False
            print(f"🟢 启动浏览器 options={ {k: v for k, v in options.items() if k != 'args'} }")
            browser = await playwright.chromium.launch(**options)
            # 搜狐后台为 qiankun 微前端，禁止注入 stealth（会破坏 createElement）
            context = await browser.new_context()
            page = await context.new_page()
            print("🟢 打开搜狐号登录页...")
            await page.goto("https://mp.sohu.com/mpfe/v4/login", timeout=60000, wait_until="domcontentloaded")
            print(f"🟢 登录页已打开: {page.url}")
            try:
                await page.wait_for_url(
                    lambda url: (
                        "mp.sohu.com" in str(url)
                        and "/login" not in str(url).lower()
                        and "passport.sohu.com" not in str(url).lower()
                    ),
                    timeout=200_000,
                )
                print("✅ 搜狐号登录成功，检测到进入创作者后台")
            except asyncio.TimeoutError:
                print("❌ 搜狐号登录超时")
                status_queue.put("500")
                return None

            uuid_v1 = uuid.uuid1()
            print(f"UUID v1: {uuid_v1}")
            cookies_dir = Path(BASE_DIR / "cookiesFile")
            cookies_dir.mkdir(exist_ok=True)
            await context.storage_state(path=cookies_dir / f"{uuid_v1}.json")

            result = await check_cookie(8, f"{uuid_v1}.json")
            if not result:
                print("⚠️ cookie 即时校验未通过，仍保存账号，请稍后刷新验证")

            with sqlite3.connect(Path(BASE_DIR / "db" / "database.db")) as conn:
                cursor = conn.cursor()
                cursor.execute('''
                    INSERT INTO user_info (type, filePath, userName, status)
                    VALUES (?, ?, ?, ?)
                ''', (8, f"{uuid_v1}.json", id, 1 if result else 0))
                conn.commit()
                print("✅ 搜狐号用户状态已记录")
            status_queue.put("200")
            return True
    except Exception as e:
        print(f"❌ 搜狐号登录异常: {e}")
        import traceback
        traceback.print_exc()
        try:
            status_queue.put("500")
        except Exception:
            pass
        return None
    finally:
        try:
            if page:
                await page.close()
        except Exception:
            pass
        try:
            if context:
                await context.close()
        except Exception:
            pass
        try:
            if browser:
                await browser.close()
        except Exception:
            pass


async def zhihu_cookie_gen(id, status_queue):
    browser = None
    context = None
    page = None
    try:
        status_queue.put("MANUAL_LOGIN")
        print(f"🟢 开始知乎登录: id={id}")
        async with async_playwright() as playwright:
            options = get_browser_options()
            options['headless'] = False
            print(f"🟢 启动浏览器 options={ {k: v for k, v in options.items() if k != 'args'} }")
            browser = await playwright.chromium.launch(**options)
            context = await browser.new_context()
            context = await set_init_script(context)
            page = await context.new_page()
            print("🟢 打开知乎登录页...")
            await page.goto("https://www.zhihu.com/signin", timeout=60000, wait_until="domcontentloaded")
            print(f"🟢 登录页已打开: {page.url}")
            print("🟢 请在浏览器完成登录，等待登录凭证 z_c0…")
            from uploader.zhihu_uploader.main import _wait_until_logged_in
            ok = await _wait_until_logged_in(page, context, timeout_ms=200_000)
            if not ok:
                print("❌ 知乎登录超时：未检测到 z_c0")
                status_queue.put("500")
                return None
            print("✅ 知乎登录成功，已检测到 z_c0")

            uuid_v1 = uuid.uuid1()
            print(f"UUID v1: {uuid_v1}")
            cookies_dir = Path(BASE_DIR / "cookiesFile")
            cookies_dir.mkdir(exist_ok=True)
            await context.storage_state(path=cookies_dir / f"{uuid_v1}.json")

            result = await check_cookie(9, f"{uuid_v1}.json")
            if not result:
                print("⚠️ cookie 即时校验未通过，仍保存账号，请稍后刷新验证")

            with sqlite3.connect(Path(BASE_DIR / "db" / "database.db")) as conn:
                cursor = conn.cursor()
                cursor.execute('''
                    INSERT INTO user_info (type, filePath, userName, status)
                    VALUES (?, ?, ?, ?)
                ''', (9, f"{uuid_v1}.json", id, 1 if result else 0))
                conn.commit()
                print("✅ 知乎用户状态已记录")
            status_queue.put("200")
            return True
    except Exception as e:
        print(f"❌ 知乎登录异常: {e}")
        import traceback
        traceback.print_exc()
        try:
            status_queue.put("500")
        except Exception:
            pass
        return None
    finally:
        try:
            if page:
                await page.close()
        except Exception:
            pass
        try:
            if context:
                await context.close()
        except Exception:
            pass
        try:
            if browser:
                await browser.close()
        except Exception:
            pass


# Bilibili登录（通过biliup CLI，在新终端窗口中扫码）
async def bilibili_cookie_gen(id, status_queue):
    import subprocess
    import sys
    from uploader.bilibili_uploader.runtime import ensure_biliup_binary

    account_dir = Path(BASE_DIR / "cookiesFile")
    account_dir.mkdir(exist_ok=True)
    account_file = account_dir / f"bilibili_{id}.json"

    # 获取正确的biliup二进制路径（不是pip安装的Python版）
    biliup_path = str(ensure_biliup_binary(force_check=False))

    # 通知前端已打开终端，请手动登录
    status_queue.put("MANUAL_LOGIN")

    try:
        # 在新终端窗口中运行 biliup login
        if sys.platform == 'win32':
            subprocess.Popen(
                [biliup_path, '-u', str(account_file), 'login'],
                creationflags=subprocess.CREATE_NEW_CONSOLE
            )
        else:
            subprocess.Popen(
                ['x-terminal-emulator', '-e', biliup_path, '-u', str(account_file), 'login']
            )
        print(f"✅ 已打开终端窗口，请在终端中扫码登录Bilibili")
    except Exception as e:
        print(f"❌ 打开终端失败: {e}")
        status_queue.put("500")
        return

    # 轮询等待cookie文件生成（用户在终端完成扫码后biliup会创建文件）
    for _ in range(100):  # 最多等200秒
        await asyncio.sleep(2)
        if account_file.exists():
            result = run_biliup_command(["-u", str(account_file), "renew"])
            if result.returncode == 0:
                print("✅ Bilibili登录成功")
                with sqlite3.connect(Path(BASE_DIR / "db" / "database.db")) as conn:
                    conn.cursor().execute(
                        'INSERT INTO user_info (type, filePath, userName, status) VALUES (?, ?, ?, ?)',
                        (6, f"bilibili_{id}.json", id, 1))
                    conn.commit()
                status_queue.put("200")
                return

    print("❌ Bilibili登录超时")
    status_queue.put("500")


# a = asyncio.run(xiaohongshu_cookie_gen(4,None))
# print(a)


# 百度贴吧登录（手动登录，非二维码；检测 BDUSS/STOKEN/BAIDUID 登录凭证）
async def tieba_cookie_gen(id, status_queue):
    browser = None
    context = None
    page = None
    try:
        status_queue.put("MANUAL_LOGIN")
        print(f"🟢 开始百度贴吧登录: id={id}")
        async with async_playwright() as playwright:
            options = get_browser_options()
            options['headless'] = False
            print(f"🟢 启动浏览器 options={ {k: v for k, v in options.items() if k != 'args'} }")
            browser = await playwright.chromium.launch(**options)
            context = await browser.new_context()
            context = await set_init_script(context)
            page = await context.new_page()
            print("🟢 打开百度贴吧登录页...")
            await page.goto("https://passport.baidu.com/v2/?login", timeout=60000, wait_until="domcontentloaded")
            print(f"🟢 登录页已打开: {page.url}")
            print("🟢 请在浏览器完成百度贴吧登录，等待登录凭证 BDUSS…")

            async def _has_auth_cookie(ctx) -> bool:
                """贴吧发表页需要 BDUSS，BAIDUID 单一不足以发表。"""
                try:
                    cookies = await ctx.cookies()
                except Exception:
                    return False
                names = {c.get("name") for c in cookies}
                return bool(names & {"BDUSS"})

            elapsed = 0
            step = 1000
            ok = False
            while elapsed < 200_000:
                if await _has_auth_cookie(context):
                    ok = True
                    break
                cur_url = (page.url or "").lower()
                if "passport.baidu.com" not in cur_url and "/login" not in cur_url and "tieba.baidu.com" in cur_url:
                    await page.wait_for_timeout(500)
                    if await _has_auth_cookie(context):
                        ok = True
                        break
                await page.wait_for_timeout(step)
                elapsed += step
            if not ok:
                ok = await _has_auth_cookie(context)
            if not ok:
                print("❌ 百度贴吧登录超时：未检测到登录凭证 BDUSS")
                status_queue.put("500")
                return None
            print("✅ 百度贴吧登录成功，已检测到 BDUSS 等登录凭证")

            uuid_v1 = uuid.uuid1()
            print(f"UUID v1: {uuid_v1}")
            cookies_dir = Path(BASE_DIR / "cookiesFile")
            cookies_dir.mkdir(exist_ok=True)
            await context.storage_state(path=cookies_dir / f"{uuid_v1}.json")

            result = await check_cookie(10, f"{uuid_v1}.json")
            if not result:
                print("⚠️ cookie 即时校验未通过，仍保存账号，请稍后刷新验证")

            with sqlite3.connect(Path(BASE_DIR / "db" / "database.db")) as conn:
                cursor = conn.cursor()
                cursor.execute('''
                    INSERT INTO user_info (type, filePath, userName, status)
                    VALUES (?, ?, ?, ?)
                ''', (10, f"{uuid_v1}.json", id, 1 if result else 0))
                conn.commit()
                print("✅ 百度贴吧用户状态已记录")
            status_queue.put("200")
            return True
    except Exception as e:
        print(f"❌ 百度贴吧登录异常: {e}")
        import traceback
        traceback.print_exc()
        try:
            status_queue.put("500")
        except Exception:
            pass
        return None
    finally:
        try:
            if page:
                await page.close()
        except Exception:
            pass
        try:
            if context:
                await context.close()
        except Exception:
            pass
        try:
            if browser:
                await browser.close()
        except Exception:
            pass
