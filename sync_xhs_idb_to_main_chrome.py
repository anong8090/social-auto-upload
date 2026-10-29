#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
将持久化自动化浏览器目录 (omnipost/browser_profiles/xiaohongshu) 中的小红书图文草稿
(IndexedDB: draft-database-v1 -> image-draft) 完整桥接注入到用户日常使用的主 Google Chrome 窗口中，
并自动将主 Chrome 的小红书标签页刷新至「上传图文 (target=image)」并展开「草稿箱 -> 图文笔记」。
"""
import asyncio
import base64
import json
import subprocess
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE_DIR / 'omnipost'))
from patchright.async_api import async_playwright

PROFILE_DIR = BASE_DIR / 'omnipost' / 'browser_profiles' / 'xiaohongshu'
IMG_SEARCH_ROOT = BASE_DIR / '河狸云蓝枢_自媒体物料包' / '图片素材'
TARGET_IMAGE_URL = "https://creator.xiaohongshu.com/publish/publish?source=official&from=tab_switch&target=image"


def find_all_local_pngs() -> dict[str, Path]:
    """扫描物料包下所有 PNG 图片文件名到路径的映射，用于重建 IndexedDB 中的 File 对象。"""
    mapping: dict[str, Path] = {}
    for p in IMG_SEARCH_ROOT.rglob("*.png"):
        mapping[p.name] = p
    return mapping


async def dump_profile_image_drafts() -> list[dict]:
    """从 omnipost/browser_profiles/xiaohongshu 导出 draft-database-v1 -> image-draft 全部记录。"""
    async with async_playwright() as p:
        ctx = await p.chromium.launch_persistent_context(
            user_data_dir=str(PROFILE_DIR),
            headless=True,
            channel='chrome',
        )
        page = ctx.pages[0] if ctx.pages else await ctx.new_page()
        await page.goto(TARGET_IMAGE_URL, timeout=60000)
        await page.wait_for_timeout(2500)
        records = await page.evaluate('''async () => {
            const db = await new Promise((res, rej) => {
                const req = indexedDB.open('draft-database-v1', 10);
                req.onupgradeneeded = (e) => {
                    const d = e.target.result;
                    for (const s of ['article-draft', 'audio-draft', 'image-draft', 'video-draft']) {
                        if (!d.objectStoreNames.contains(s)) {
                            d.createObjectStore(s, { keyPath: 'draftId' });
                        }
                    }
                };
                req.onsuccess = () => res(req.result);
                req.onerror = () => rej(req.error);
            });
            const tx = db.transaction('image-draft', 'readonly');
            const store = tx.objectStore('image-draft');
            const vals = await new Promise((res) => {
                const r = store.getAll();
                r.onsuccess = () => res(r.result || []);
            });
            db.close();
            return vals;
        }''')
        await ctx.close()
        return records


def ensure_main_chrome_xhs_tab() -> None:
    """确保用户主 Google Chrome 存在 creator.xiaohongshu.com 标签页并导航到上传图文页。"""
    apple_script = f'''
    tell application "Google Chrome"
        set foundTab to false
        repeat with w in windows
            repeat with t in tabs of w
                if URL of t contains "creator.xiaohongshu.com" then
                    set foundTab to true
                    if URL of t does not contain "target=image" then
                        set URL of t to "{TARGET_IMAGE_URL}"
                    end if
                end if
            end repeat
        end repeat
        if not foundTab then
            if (count of windows) = 0 then
                make new window
            end if
            tell front window to make new tab with properties {{URL:"{TARGET_IMAGE_URL}"}}
        end if
    end tell
    '''
    subprocess.run(["osascript", "-e", apple_script], check=False)


async def sync_to_main_chrome() -> int:
    if sys.platform != 'darwin':
        print("ℹ️ 当前为 Windows/Linux 系统，跳过 macOS 专属 AppleScript 注入。")
        print("👉 小红书草稿已安全保存在本地持久化目录，运行 python open_xhs_draft_box.py 即可直接打开草稿箱查看与发布。")
        records = await dump_profile_image_drafts()
        return len(records)

    records = await dump_profile_image_drafts()
    if not records:
        print("⚠️ 持久化目录中未找到任何小红书图文草稿记录。")
        return 0

    local_pngs = find_all_local_pngs()
    needed_files_b64: dict[str, str] = {}

    for rec in records:
        img_list = rec.get('content', {}).get('draftStore', {}).get('imgList', [])
        for img in img_list:
            key = img.get('key', '')
            matched_name = None
            for fname, fpath in local_pngs.items():
                if key.startswith(fname):
                    matched_name = fname
                    if fname not in needed_files_b64:
                        needed_files_b64[fname] = base64.b64encode(fpath.read_bytes()).decode('ascii')
                    break
            img['_fileName'] = matched_name

    ensure_main_chrome_xhs_tab()
    await asyncio.sleep(2.5)

    payload = {
        'records': records,
        'files': needed_files_b64,
    }

    js_code = (
        "(async () => {\n"
        f"const payload = {json.dumps(payload, ensure_ascii=False)};\n"
        """
        function b64ToFile(b64, filename) {
            const bin = atob(b64);
            const len = bin.length;
            const arr = new Uint8Array(len);
            for (let i = 0; i < len; i++) arr[i] = bin.charCodeAt(i);
            return new File([arr], filename, { type: 'image/png', lastModified: Date.now() });
        }
        const db = await new Promise((resolve, reject) => {
            const req = indexedDB.open('draft-database-v1', 10);
            req.onupgradeneeded = (e) => {
                const d = e.target.result;
                for (const s of ['article-draft', 'audio-draft', 'image-draft', 'video-draft']) {
                    if (!d.objectStoreNames.contains(s)) {
                        d.createObjectStore(s, { keyPath: 'draftId' });
                    }
                }
            };
            req.onsuccess = () => resolve(req.result);
            req.onerror = () => reject(req.error);
        });
        const tx = db.transaction('image-draft', 'readwrite');
        const store = tx.objectStore('image-draft');
        store.clear();
        for (const rec of payload.records) {
            for (const img of (rec.content?.draftStore?.imgList || [])) {
                const fname = img._fileName;
                delete img._fileName;
                if (fname && payload.files[fname]) {
                    const f = b64ToFile(payload.files[fname], fname);
                    img.file = f;
                    img.url = URL.createObjectURL(f);
                }
            }
            store.put(rec);
        }
        await new Promise((resolve, reject) => {
            tx.oncomplete = () => resolve(true);
            tx.onerror = () => reject(tx.error);
        });
        db.close();
        window.__XHS_INJECT_DONE__ = payload.records.length;
        })();
        """
    )

    tmp_js = Path('/tmp/sync_xhs_idb_payload.js')
    tmp_js.write_text(js_code, encoding='utf-8')

    try:
        inject_script = '''
        set jsCode to read POSIX file "/tmp/sync_xhs_idb_payload.js" as «class utf8»
        tell application "Google Chrome"
            repeat with w in windows
                repeat with t in tabs of w
                    if URL of t contains "creator.xiaohongshu.com" then
                        execute t javascript jsCode
                    end if
                end repeat
            end repeat
        end tell
        '''
        subprocess.run(["osascript", "-e", inject_script], check=True)
        await asyncio.sleep(2.5)

        reload_and_open_script = f'''
        tell application "Google Chrome"
            repeat with w in windows
                repeat with t in tabs of w
                    if URL of t contains "creator.xiaohongshu.com" then
                        set URL of t to "{TARGET_IMAGE_URL}"
                    end if
                end repeat
            end repeat
        end tell
        '''
        subprocess.run(["osascript", "-e", reload_and_open_script], check=True)
        await asyncio.sleep(3.5)

        click_modal_script = '''
        tell application "Google Chrome"
            repeat with w in windows
                repeat with t in tabs of w
                    if URL of t contains "creator.xiaohongshu.com" then
                        return (execute t javascript "(() => {
                            const els = Array.from(document.querySelectorAll('*')).filter(el => el.children.length === 0);
                            const btn = els.find(el => (el.textContent || '').trim().startsWith('草稿箱'));
                            if (btn) btn.click();
                            setTimeout(() => {
                                const els2 = Array.from(document.querySelectorAll('*')).filter(el => el.children.length === 0);
                                const tab = els2.find(el => (el.textContent || '').trim().startsWith('图文笔记'));
                                if (tab) tab.click();
                            }, 600);
                            return btn ? btn.textContent.trim() : 'NO_BTN';
                        })()")
                    end if
                end repeat
            end repeat
        end tell
        '''
        res = subprocess.run(["osascript", "-e", click_modal_script], capture_output=True, text=True)
        print(f"✅ 已同步 {len(records)} 篇草稿至主 Google Chrome！草稿箱按钮状态: {res.stdout.strip()}")
    finally:
        if tmp_js.exists():
            tmp_js.unlink()

    return len(records)


if __name__ == '__main__':
    asyncio.run(sync_to_main_chrome())
