# 河狸云·蓝枢 Planora —— 「一稿五投」AI 自媒体全自动流水线

本项目面向团队协作设计，集成了开源自媒体分发引擎 **OmniPost** 与面向 AI Agent（如 Antigravity、Cursor、Windsurf、Claude Code 等）的自动化流水线规范（[AGENTS.md](AGENTS.md)），支持**1 个选题全自动生成双版本文案 + 6 张专版配图，并一次性推入小红书、百家号、今日头条、知乎、搜狐号五大平台官方草稿箱**。

---

## 目录结构

```
market/
├── AGENTS.md                  # 🌟 最高执行准则：AI Agent 每次必读的核心生产与合规规范
├── README.md                  # 📖 新人同事上手指引（本文件）
├── setup.command              # 🛠️ macOS 双击一键初始化运行环境 (创建 Python venv 并装内核)
├── start.command              # 🚀 macOS 双击一键启动项目 (自动打开控制台面板)
├── stop.command               # 🛑 macOS 双击一键关闭项目
├── account_lookup.py          # 🔍 多平台账号 Cookie 动态探测工具
├── agent_publisher.py         # 🤖 统一内容分发适配器 (CLI / SDK)
├── sync_xhs_idb_to_main_chrome.py # 🌉 小红书草稿与日常主 Chrome 浏览器 IndexedDB 同步桥接
├── open_xhs_draft_box.py      # 📕 一键拉起持久化浏览器并直达小红书图文草稿箱
├── save_all_5_xhs_drafts.py   # 自动化小红书存草稿脚本
├── save_all_5_bjh_drafts.py   # 自动化百家号存草稿脚本
├── save_all_5_toutiao_drafts.py # 自动化今日头条存草稿脚本
├── save_all_5_sohu_drafts.py  # 自动化搜狐号存草稿脚本
├── 河狸云蓝枢_自媒体物料包/    # 🎨 品牌标准资产、正版 Logo、实机截图与往期参考
└── omnipost/                  # ⚙️ 分发引擎核心 (含后台 API、数据库与前端构建产物)
```

---

## 一、 新人同事极速上手（3 步走）

### 步骤 1：一键初始化运行环境
在 macOS 访达（Finder）中直接**双击运行 `setup.command`**（或在终端执行）：
```bash
./setup.command
```
> 该脚本会自动创建 `omnipost/.venv` 虚拟环境、安装 Python 依赖库并下载 Chromium 浏览器内核。

### 步骤 2：启动控制台并扫码绑定账号
双击运行 **`start.command`**，系统将自动拉起服务并在浏览器中打开控制台：
- 控制台地址：`http://localhost:5173`
- 进到左侧 **「账号管理」** 页面，分别为您要分发的平台点击扫码登录：
  - **小红书** (`type=1`)
  - **百家号** (`type=5`)
  - **今日头条** (`type=7`)
  - **搜狐号** (`type=8`)
  - **知乎** (`type=9`)
- 扫码成功后，Cookie 会自动保存在本地数据库中。随时可在终端运行以下命令检查当前绑定状态：
```bash
omnipost/.venv/bin/python account_lookup.py
```

### 步骤 3：让您的 AI Agent 接管内容生产
您只需将下方的话术复制给您的 AI Agent（推荐 Antigravity / Cursor / Windsurf），输入选题即可享受全自动一步到位生产！

---

## 二、 复制给 AI Agent 的执行话术

请直接复制以下内容发送给您的 AI Agent：

```markdown
请首先读取当前项目根目录下的 `AGENTS.md` 文件。本项目是「河狸云·蓝枢 Planora」的一稿五投自媒体内容生产流水线。

我的目标是针对我给出的选题，采用「全自动一步到位」模式：
1. 严格按照 AGENTS.md 规范撰写双版本正文（小红书短图文 300~500字 + 四长文平台通用精炼文 800~1100字），并为小红书、百家号、今日头条、知乎、搜狐号定制专属合规标题与标签（注意各平台标题字数红线，杜绝任何免费试点/外链等违规词，勾选对应 AI 声明）；
2. 生成 6 张独立专版配图（3张 16:9 横图供长文平台 + 3张 3:4 竖图供小红书），必须使用品牌标准资产目录下的正版河狸云 Logo 与真实产品截图，严禁 AI 假 Logo 与文字乱码框；
3. 将内容安全存入五大平台官方草稿箱（严禁点击正式发布），小红书自动执行主 Chrome 浏览器的 IndexedDB 桥接；
4. 将草稿数据同步写入本地 OmniPost 数据库 `publish_drafts` 表，以便我在前端草稿箱中审核与完结；
5. 最终输出可点击的文件路径、配图预览与草稿箱核验截图。

请先运行 `omnipost/.venv/bin/python account_lookup.py` 检查各平台账号登录状态并向我汇报，确认无误后我将提供接下来的核心选题。
```

---

## 三、 日常发布与运营节奏

1. **每天 1 篇**：在各平台官方后台（或通过 OmniPost 唤起的草稿箱）快速人工复核，点击发布。
2. **本地完结归档**：在本地面板 `http://localhost:5173/#/publish-history` 的 **「草稿箱」** 标签页，点击对应记录的 **「确认完结」**，即可将草稿状态标记为已发布并归档至发布历史。
3. **关闭服务**：不再使用时，双击运行 **`stop.command`** 即可安全退出所有后台进程。
