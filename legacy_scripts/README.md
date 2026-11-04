# legacy_scripts —— 历史一次性脚本归档

> 2026-11-04 归档。这里的脚本**不再作为日常发布方式**，仅作历史参考与应急排查。

## 为什么归档

原先每期发布都要复制上一期的脚本、改期数、改路径，根目录累计堆了 39 个 `_publish_*_epNN.py` / `_verify_*` / `save_all_5_*`。
它们的共同问题：

- 硬编码期数与素材路径，换一期就要改多处；
- 不复用平台登录态，重复写 Playwright 逻辑；
- **不写 `publish_history`**，发布结果无法核对；
- 小红书脚本仍存在，账号封禁后属于误发风险。

## 现在怎么发

统一入口在 `omnipost/scripts/publish_episode.py`：

```bash
omnipost/.venv/bin/python omnipost/scripts/publish_episode.py --episode 15 --seed 20261105
```

它会自动完成素材发现、预检、随机抽 5 台、逐平台发布、写库（含公开链接）与汇总输出。
详见项目根目录 `AGENTS.md` 第四节。

## 归档清单

| 类别 | 文件 | 说明 |
| :--- | :--- | :--- |
| 单平台发布 | `_publish_*_ep13.py`、`_publish_*_ep14.py` | 知乎/百家号/搜狐/头条/小红书逐平台硬编码发布 |
| 重发修复 | `_republish_xhs_ep14*.py` | 小红书第 14 期反复重发，共 4 个版本 |
| 链接/校验 | `_get_*_links*.py`、`_verify_*.py` | 事后反爬补链接、发布结果校验 |
| 草稿批处理 | `save_all_5_*.py`、`save_ep10_zhihu_draft.py` | 批量存草稿 |
| 小红书专用 | `open_xhs_draft_box.py`、`sync_xhs_idb_to_main_chrome.py`、`fix_draft_covers.py` | 已随账号封禁失效 |
| 工具模块 | `account_lookup.py`、`draft_content_parser.py` | 被上述脚本引用，一并移入以保持自洽 |
| 早期适配器 | `agent_publisher.py` | 已被 `publish_episode.py` 取代 |

## 重要说明

1. **路径已重写**：归档时把所有 `Path(__file__).resolve().parent` 改为 `.parent.parent`，因此这些脚本仍能定位到项目根、`omnipost/` 与物料包，可直接运行。
2. **不受 git 跟踪**：`.gitignore` 中的 `_*.py`、`login_sohu_v*.py` 等规则使其中的部分文件本就不进版本库，归档不改变这一点。
3. **保留在根目录的 `login_*.py`**：各平台扫码登录脚本仍是新平台接入选台的唯一途径，未归档。
4. **不要在这里新增脚本**：需要新能力请改 `omnipost/scripts/publish_episode.py` 或 `omnipost/myUtils/`。
