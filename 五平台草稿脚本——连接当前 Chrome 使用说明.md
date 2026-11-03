# 五平台草稿脚本——连接当前 Chrome 使用说明

## 为什么要改

之前的脚本会**另开一个独立浏览器**，不和你日常用的 Chrome 共用缓存/登录态/IndexedDB，导致：
- 脚本里存的草稿，你自己打开 Chrome 看不到；
- OmniPost 草稿箱也看不到第 11 期的记录。

改后：脚本**连接你当前已打开的 Chrome**，填内容、存草稿、写入 OmniPost，草稿就存在你自己的浏览器里，自己打开 Chrome 就能看到并手动发布。

---

## 操作步骤（每次运行脚本前都要做）

### 第 1 步：启动带远程调试端口的 Chrome

在终端执行：

```bash
/Applications/Google\ Chrome.app/Contents/MacOS/Google Chrome --remote-debugging-port=9222
```

- 这会打开一个**新的 Chrome 窗口**（带 `--remote-debugging-port=9222`）。
- 你在这个窗口里**手动登录好** 小红书 / 知乎 / 搜狐号 / 百家号 / 今日头条 的创作者平台（只要 cookie 没过期，脚本复用即可，不需要每次都扫码）。
- **不要关闭这个 Chrome 窗口**，脚本运行期间要保持打开。

> 如果你已经有日常 Chrome 在跑，建议专门开一个带调试端口的窗口专门用于运行脚本，避免和日常浏览混在一起。

### 第 2 步：运行对应平台的脚本

在项目根目录 `/Users/anong/Developer/market_for_team` 下，用项目虚拟环境 Python 运行：

| 平台 | 脚本文件 | 平台类型 |
| :--- | :--- | :--- |
| 小红书 | `save_all_5_xhs_drafts.py` | type=1 |
| 知乎 | `save_ep10_zhihu_draft.py` | type=9 |
| 搜狐号 | `save_all_5_sohu_drafts.py` | type=8 |
| 百家号 | `save_all_5_bjh_drafts.py` | type=5 |
| 今日头条 | `save_all_5_toutiao_drafts.py` | type=7 |

示例：

```bash
omnipost/.venv/bin/python save_all_5_xhs_drafts.py
```

脚本会：
1. 连接你当前 Chrome（端口 9222），复用已登录的 cookie；
2. 打开对应平台发布页，填标题、正文、配图、封面；
3. 勾选 AI 创作声明；
4. **点击「存草稿 / 暂存离开」**（绝不点击「发布」）；
5. 截图保存到 `logs/screenshots/`；
6. 把本期配图复制到 `omnipost/videoFile/`；
7. 向 `omnipost/db/database.db` 的 `publish_drafts` 表插入一条记录（`status='draft'`）。

脚本运行完会**断开与 Chrome 的连接，但不会关闭你的 Chrome 窗口**。

### 第 3 步：你在自己 Chrome 里核验并发布

打开你当前的 Chrome，进入对应平台的创作者平台 → 草稿箱：
- 小红书：草稿箱 → 图文笔记
- 知乎：专栏 → 草稿箱
- 搜狐号：草稿箱
- 百家号：草稿箱
- 今日头条：草稿箱

确认内容无误后，**由你手动点击「发布」**（脚本绝不自动发布）。

### 第 4 步：OmniPost 草稿箱确认完结

发布完成后，打开 OmniPost 面板：

```
http://localhost:5173/#/publish-history
```

进入「草稿箱」标签页，能看到本期 5 个平台的待发草稿卡片及缩略图。人工发布后，点击「确认完结」归档。

---

## 脚本文件清单

```
save_all_5_xhs_drafts.py        # 小红草稿
save_ep10_zhihu_draft.py        # 知乎草稿
save_all_5_sohu_drafts.py       # 搜狐号草稿
save_all_5_bjh_drafts.py        # 百家号草稿
save_all_5_toutiao_drafts.py    # 今日头条草稿
omnipost/utils/connect_current_chrome.py  # 连接当前 Chrome 的辅助模块（新建）
```

---

## 注意事项

1. **必须先启动带 `--remote-debugging-port=9222` 的 Chrome**，否则脚本连接不上会报错。
2. **脚本不自动发布**，只负责填内容 + 存草稿 + 写入 OmniPost 记录，发布由你在自己 Chrome 里手动完成。
3. 若某平台 cookie 已过期，脚本会提示登录失败，请先在当前 Chrome 里手动登录一次该平台，再重新运行脚本。
4. 每个平台脚本默认只处理第 11 期（`11_dwg_vs_pdf_现场看图选型`），如需改期，修改脚本顶部的 `DOCS_DIR` / `IMG_BASE` 路径即可。
