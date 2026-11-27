#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""直接操作你正在用的 Chrome：把当日内容填进小红书 / 今日头条发布页。

为什么是 AppleScript 而不是 Playwright
---------------------------------------
Chrome 136+ 禁止对日常默认 profile 开远程调试，Playwright 接不上你在用的
那个 Chrome。AppleScript 走的是浏览器自己的 Apple Events 通道，不需要调试
端口，页面就是你的登录态、你的窗口。

前提（一次性）
--------------
Chrome 菜单：查看 → 开发者 → 允许 Apple 事件中的 JavaScript。
没开时报错：通过 AppleScript 执行 JavaScript 的功能已关闭。

行为
----
- 新开一个标签页打开发布页，不动你已有的标签；
- 只填标题/正文/配图/声明，**不点发布、不点暂存离开**；
- 结果由页面内异步任务写回 `window.__lxFill`，本脚本轮询打印。

用法：

    # 小红书：先起桥接，再填
    omnipost/.venv/bin/python omnipost/scripts/xhs_fill_bridge.py
    omnipost/.venv/bin/python omnipost/scripts/fill_daily_browser.py \
        --platform xhs --dir 2026-11-25 --kind daily

    # 头条长文
    omnipost/.venv/bin/python omnipost/scripts/fill_daily_browser.py \
        --platform toutiao --dir 长文-只读不是保守 --kind long
"""
from __future__ import annotations

import argparse
import base64
import json
import subprocess
import sys
import time
from urllib.parse import quote

BRIDGE = "http://127.0.0.1:5419"
CHROME = "Google Chrome"

TARGETS = {
    "xhs": "https://creator.xiaohongshu.com/publish/publish?from=homepage&target=image",
    "toutiao": "https://mp.toutiao.com/profile_v4/graphic/publish",
}


class ChromeScriptError(RuntimeError):
    pass


JS_DISABLED_HINT = (
    "Chrome 还没开「允许 Apple 事件中的 JavaScript」。\n"
    "   菜单：查看 → 开发者 → 允许 Apple 事件中的 JavaScript，打开后重跑。"
)


def _is_js_disabled(message: str) -> bool:
    return "执行 JavaScript 的功能已关闭" in message or "Apple 事件中的 JavaScript" in message


def assert_js_enabled() -> None:
    """先确认能执行 JS，否则后面所有等待都会误报成「页面元素未出现」。"""
    try:
        js_eval(1, "'ok'")
    except ChromeScriptError as exc:
        if _is_js_disabled(str(exc)):
            raise ChromeScriptError(JS_DISABLED_HINT) from exc
        raise


def osascript(script: str) -> str:
    proc = subprocess.run(
        ["osascript", "-e", script], capture_output=True, text=True, timeout=120
    )
    if proc.returncode != 0:
        raise ChromeScriptError((proc.stderr or proc.stdout).strip())
    return proc.stdout.strip()


def js_eval(tab_index: int, js: str) -> str:
    """在指定标签页里执行 JS；用 base64 + eval 规避引号转义。"""
    encoded = base64.b64encode(js.encode("utf-8")).decode("ascii")
    script = (
        f'tell application "{CHROME}"\n'
        f'  tell tab {tab_index} of window 1\n'
        f'    execute javascript "eval(atob(\'{encoded}\'))"\n'
        f'  end tell\n'
        f'end tell'
    )
    return osascript(script)


def open_tab(url: str) -> int:
    script = (
        f'tell application "{CHROME}"\n'
        f'  activate\n'
        f'  tell window 1\n'
        f'    set newTab to make new tab with properties {{URL:"{url}"}}\n'
        f'    set active tab index to (count of tabs)\n'
        f'    return (count of tabs)\n'
        f'  end tell\n'
        f'end tell'
    )
    return int(osascript(script))


def wait_ready(tab_index: int, selector: str, timeout: int = 45) -> None:
    deadline = time.time() + timeout
    last_url = ""
    while time.time() < deadline:
        try:
            ready = js_eval(
                tab_index,
                "JSON.stringify({href: location.href, state: document.readyState, "
                f"hit: !!document.querySelector({json.dumps(selector)})}})",
            )
            data = json.loads(ready or "{}")
            last_url = data.get("href") or last_url
            if data.get("hit"):
                return
        except ChromeScriptError as exc:
            # 开关没开时必须立刻失败，不能被当成「页面还没加载好」继续等
            if _is_js_disabled(str(exc)):
                raise ChromeScriptError(JS_DISABLED_HINT) from exc
        time.sleep(1.5)
    raise ChromeScriptError(
        f"页面元素未出现：{selector}\n   当前地址：{last_url or '(空)'}\n"
        "   如果是登录页，请先在这个标签页里登录；若是发布页，可能是页面改版。"
    )


XHS_JS = r"""
(async () => {
  const R = {steps: [], error: ''};
  const sleep = (ms) => new Promise(r => setTimeout(r, ms));
  const waitFor = async (fn, ms) => {
    const start = Date.now();
    while (Date.now() - start < ms) { const v = fn(); if (v) return v; await sleep(250); }
    return null;
  };
  const setInput = (el, v) => {
    const proto = el instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
    Object.getOwnPropertyDescriptor(proto, 'value').set.call(el, v);
    el.dispatchEvent(new Event('input', {bubbles: true}));
    el.dispatchEvent(new Event('change', {bubbles: true}));
  };
  try {
    const payload = window.__lxPayload;
    const imgs = (payload.images || []).slice(0, 9);
    if (imgs.length) {
      const input = await waitFor(() => document.querySelector('input[type="file"][accept*="image"]'), 20000);
      if (!input) throw new Error('没找到图片上传框，请确认是「上传图文」页');
      const dt = new DataTransfer();
      for (const im of imgs) {
        const res = await fetch('http://127.0.0.1:5419' + im.url);
        if (!res.ok) throw new Error('图片下载失败 ' + im.name);
        const blob = await res.blob();
        dt.items.add(new File([blob], im.name, {type: blob.type || 'image/png'}));
      }
      input.files = dt.files;
      input.dispatchEvent(new Event('change', {bubbles: true}));
      R.steps.push('已上传 ' + imgs.length + ' 张配图');
    }
    const titleEl = await waitFor(() => document.querySelector('input[placeholder*="填写标题"], input[placeholder*="标题"]'), 20000);
    if (!titleEl) throw new Error('没找到标题输入框');
    titleEl.focus(); setInput(titleEl, payload.title.slice(0, 20)); titleEl.blur();
    R.steps.push('已填标题');
    await sleep(600);
    const marker = await waitFor(() => document.querySelector('[data-placeholder*="输入正文描述"]'), 20000);
    const editor = marker && (marker.closest('[contenteditable="true"]') || marker);
    if (!editor) throw new Error('没找到正文编辑器');
    editor.focus();
    const sel = window.getSelection(); const range = document.createRange();
    range.selectNodeContents(editor); sel.removeAllRanges(); sel.addRange(range);
    document.execCommand('delete', false, null);
    const paras = String(payload.body || '').split(/\n{2,}/).map(s => s.trim()).filter(Boolean);
    for (let i = 0; i < paras.length; i++) {
      document.execCommand('insertText', false, paras[i]);
      if (i < paras.length - 1) document.execCommand('insertParagraph', false, null);
    }
    if ((payload.tags || []).length) {
      document.execCommand('insertParagraph', false, null);
      for (const t of payload.tags) { document.execCommand('insertText', false, '#' + t + ' '); await sleep(150); }
      R.steps.push('话题已写成 #文本，可在下拉里点选成标签');
    }
    editor.dispatchEvent(new Event('input', {bubbles: true}));
    R.steps.push('已填正文');
    const findText = (t) => [...document.querySelectorAll('div,span,button,label')]
      .find(el => el.childElementCount === 0 && (el.textContent || '').trim() === t);
    const trigger = await waitFor(() => findText('添加内容类型声明'), 5000);
    if (trigger) {
      trigger.click();
      const opt = await waitFor(() => findText('笔记含AI合成内容'), 5000);
      if (opt) { opt.click(); R.steps.push('已勾选「笔记含AI合成内容」'); }
      else R.steps.push('未找到 AI 声明选项');
    } else R.steps.push('未找到「添加内容类型声明」入口');
    R.steps.push('未点击发布，请复核后自行操作');
  } catch (e) {
    R.error = String(e && e.message || e);
  }
  R.done = true;
  window.__lxFill = R;
})();
'started';
"""

TOUTIAO_JS = r"""
(async () => {
  const R = {steps: [], error: ''};
  const sleep = (ms) => new Promise(r => setTimeout(r, ms));
  const waitFor = async (fn, ms) => {
    const start = Date.now();
    while (Date.now() - start < ms) { const v = fn(); if (v) return v; await sleep(250); }
    return null;
  };
  const setInput = (el, v) => {
    const proto = el instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
    Object.getOwnPropertyDescriptor(proto, 'value').set.call(el, v);
    el.dispatchEvent(new Event('input', {bubbles: true}));
    el.dispatchEvent(new Event('change', {bubbles: true}));
  };
  try {
    const payload = window.__lxPayload;
    const titleEl = await waitFor(() => document.querySelector('input[placeholder*="标题"], textarea[placeholder*="标题"]'), 20000);
    if (!titleEl) throw new Error('没找到标题输入框');
    titleEl.focus(); setInput(titleEl, payload.title.slice(0, 30)); titleEl.blur();
    R.steps.push('已填标题');
    await sleep(500);
    const editor = await waitFor(() => document.querySelector('div[contenteditable="true"]'), 20000);
    if (!editor) throw new Error('没找到正文编辑器');
    editor.focus();
    const sel = window.getSelection(); const range = document.createRange();
    range.selectNodeContents(editor); sel.removeAllRanges(); sel.addRange(range);
    document.execCommand('delete', false, null);
    const paras = String(payload.body || '').split(/\n{2,}/).map(s => s.trim()).filter(Boolean);
    for (let i = 0; i < paras.length; i++) {
      document.execCommand('insertText', false, paras[i]);
      if (i < paras.length - 1) document.execCommand('insertParagraph', false, null);
    }
    editor.dispatchEvent(new Event('input', {bubbles: true}));
    R.steps.push('已填正文');
    const cover = (payload.images || [])[0];
    if (cover) {
      const isImg = (el) => (el.getAttribute('accept') || '').includes('image');
      let input = [...document.querySelectorAll('input[type="file"]')].find(isImg);
      if (!input) {
        const add = [...document.querySelectorAll('div,span,button,label')]
          .find(el => /article-cover-add|article-cover-img-wrap|article-cover-images/.test(String(el.className || '')));
        if (add) { add.click(); await sleep(1200); }
        input = [...document.querySelectorAll('input[type="file"]')].find(isImg);
      }
      if (input) {
        const res = await fetch('http://127.0.0.1:5419' + cover.url);
        const blob = await res.blob();
        const dt = new DataTransfer();
        dt.items.add(new File([blob], cover.name, {type: blob.type || 'image/png'}));
        input.files = dt.files;
        input.dispatchEvent(new Event('change', {bubbles: true}));
        R.steps.push('已上传封面 ' + cover.name);
      } else R.steps.push('未找到封面上传入口，请手动设置');
    }
    const label = [...document.querySelectorAll('label')].find(el => (el.innerText || '').includes('引用AI'));
    if (label) {
      const cb = label.querySelector('input[type="checkbox"]');
      if (!cb || !cb.checked) label.click();
      await sleep(300);
      R.steps.push('已处理作品声明「引用AI」');
    } else R.steps.push('未找到作品声明「引用AI」');
    R.steps.push('未点击发布：请核对后自行点「预览并发布」');
  } catch (e) {
    R.error = String(e && e.message || e);
  }
  R.done = true;
  window.__lxFill = R;
})();
'started';
"""


def main() -> int:
    parser = argparse.ArgumentParser(description="用 AppleScript 直接操作日常 Chrome 填入内容")
    parser.add_argument("--platform", choices=sorted(TARGETS), required=True)
    parser.add_argument("--dir", required=True, help="内容目录名，如 2026-11-25 或 长文-xxx")
    parser.add_argument("--kind", choices=("daily", "long"), default="daily")
    parser.add_argument("--timeout", type=int, default=180, help="等待填入完成秒数")
    args = parser.parse_args()

    payload_url = f"{BRIDGE}/payload?dir={quote(args.dir)}&kind={args.kind}"
    try:
        osascript(f'tell application "{CHROME}" to get URL of active tab of front window')
        # 先确认 JS 通道，否则会在后面误报成「页面元素未出现」
        assert_js_enabled()
    except ChromeScriptError as exc:
        print(f"❌ {exc}", file=sys.stderr)
        return 2

    print(f"🌐 打开 {args.platform} 发布页…")
    try:
        tab = open_tab(TARGETS[args.platform])
    except ChromeScriptError as exc:
        print(f"❌ 打开标签页失败：{exc}", file=sys.stderr)
        return 2

    selector = (
        'input[placeholder*="填写标题"], input[placeholder*="标题"]'
        if args.platform == "xhs"
        else 'input[placeholder*="标题"], textarea[placeholder*="标题"]'
    )
    try:
        wait_ready(tab, selector)
    except ChromeScriptError as exc:
        print(f"❌ {exc}", file=sys.stderr)
        return 2

    print("📦 读取内容并开始填入…")
    preload = (
        f"fetch({json.dumps(payload_url)}).then(r => r.json())"
        f".then(d => {{ window.__lxPayload = d; window.__lxFill = {{state:'loading'}}; return d.ok ? 'ok' : (d.error || 'bad'); }})"
        f".catch(e => 'ERR:' + e);"
        f"'loading';"
    )
    try:
        js_eval(tab, preload)
    except ChromeScriptError as exc:
        msg = str(exc)
        if "Apple 事件中的 JavaScript" in msg or "JavaScript" in msg:
            print(
                "❌ Chrome 还没开「允许 Apple 事件中的 JavaScript」。\n"
                "   菜单：查看 → 开发者 → 允许 Apple 事件中的 JavaScript，打开后重跑。",
                file=sys.stderr,
            )
        else:
            print(f"❌ 执行脚本失败：{msg}", file=sys.stderr)
        return 2

    deadline = time.time() + args.timeout
    while time.time() < deadline:
        time.sleep(2)
        ready = js_eval(tab, "window.__lxPayload ? JSON.stringify({ok: window.__lxPayload.ok, title: window.__lxPayload.title, error: window.__lxPayload.error || ''}) : ''")
        try:
            data = json.loads(ready) if ready else {}
        except json.JSONDecodeError:
            data = {}
        if data.get("ok") is False:
            print(f"❌ 桥接拒绝：{data.get('error')}", file=sys.stderr)
            return 2
        if data.get("title"):
            print(f"📄 标题：{data['title']}")
            break
    else:
        print("❌ 内容没读到，请确认桥接服务在 127.0.0.1:5419 运行", file=sys.stderr)
        return 2

    js_eval(tab, TOUTIAO_JS if args.platform == "toutiao" else XHS_JS)

    while time.time() < deadline:
        time.sleep(2)
        raw = js_eval(tab, "window.__lxFill ? JSON.stringify(window.__lxFill) : ''")
        try:
            state = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            continue
        if state.get("done"):
            for step in state.get("steps", []):
                print(f"  ✅ {step}")
            if state.get("error"):
                print(f"❌ {state['error']}", file=sys.stderr)
                return 2
            print("完成：已填入，未发布。请在该标签页复核后自己点平台按钮。")
            return 0
    print("⚠️ 等待超时，页面可能仍在填入，请直接看那个标签页。", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
