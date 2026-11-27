# 个人 IP「日常浏览器填入」扩展（小红书 / 今日头条）

> 首选方案不是扩展，而是 `omnipost/scripts/fill_daily_browser.py`：
> Chrome 打开「查看 → 开发者 → 允许 Apple 事件中的 JavaScript」后，
> 该脚本用 AppleScript 直接在你正在用的 Chrome 标签页里填入，无需装任何扩展。
> 扩展作为不想动这个开关时的备选。

## 为什么用扩展

- 小红书草稿只存在**当前浏览器 profile 的 IndexedDB**，无头浏览器填的草稿你看不见；
- Chrome 136+ 禁止对日常默认 profile 开远程调试，外部 Playwright 接不上你在用的 Chrome；
- 今日头条该账号的自动保存被服务端 `code=7050` 拒绝，存不进草稿箱。

所以改成：**内容由本机桥接服务提供，扩展在你自己打开的页面里填入**。
页面是你的日常浏览器、你的登录态，扩展只写 DOM，不点发布。

## 一次性安装

1. 启动桥接服务（只读，默认 `127.0.0.1:5419`）：

   ```bash
   omnipost/.venv/bin/python omnipost/scripts/xhs_fill_bridge.py
   ```

2. Chrome 打开 `chrome://extensions`，右上角开启「开发者模式」。
3. 点「加载已解压的扩展程序」，选择本目录 `omnipost/chrome_extension_xhs`。

## 每天的使用

### 小红书短稿

1. 桥接服务保持运行。
2. 日常 Chrome 打开 <https://creator.xiaohongshu.com/publish/publish?target=image>。
3. 点扩展图标 → 选日期 →「读取内容」→「填入当前页」。
4. 自己核对配图、话题和 AI 声明，再点「暂存离开」存草稿，或直接发布。

### 今日头条长文

1. 日常 Chrome 打开 <https://mp.toutiao.com/profile_v4/graphic/publish>。
2. 扩展里选「长文-…」→「读取内容」→「填入当前页」。
3. 核对封面与「引用AI」声明后，自己点「预览并发布」。

## 边界

- 扩展**不会**点平台的发布按钮，也不会点「暂存离开」，最终动作永远由人完成。
- 合规闸门：短稿走 `publish_ai_share.precheck`（标题 20 字、正文 180~320 字）；
  长文走 `publish_ai_share_longform.check_content`（≥500 字、头条标题 ≤30 字）。
  零容忍词、品牌排除词、历史重复度任一不过，桥接返回 422，扩展不填。
- 头条正文会先经 `publish_ai_share.to_plain_text` 清理 Markdown 标记，
  避免 `## 小标题` 原样出现在编辑器里。
- 配图必须放在内容目录下，桥接只读 `AI能力分享内容线/` 内的目录。
- 小红书话题以 `#文本` 写入正文；话题弹层需要你手动点选才会变成标签。
- 桥接服务只绑定 `127.0.0.1`，不对外网暴露。
