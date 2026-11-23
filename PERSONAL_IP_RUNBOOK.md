# 个人 IP 发布 Runbook

> 适用：`market_for_team` 仓库内、个人 IP 独立线（微信公众号 + 小红书）。
>
> 两条铁律：**只存草稿，不点发布**；**存完必须回读核验**。
>
> 与 `~/.agents/skills/personal-ip-ai-content/references/publishing.md` 配套使用。

## 0. 每次开工前

```bash
cd /Users/anong/Developer/market_for_team

# 1) 看最近选题与判断，避免重复
omnipost/.venv/bin/python omnipost/scripts/ip_topic_registry.py --list --limit 10

# 2) 看发布序列与已写状态
omnipost/.venv/bin/python omnipost/scripts/ip_sequence_status.py --status

# 3) 确认账号状态（只看状态与文件是否存在，不打印 Cookie）
omnipost/.venv/bin/python - <<'PY'
import sqlite3
from pathlib import Path
import sys
sys.path.insert(0, 'omnipost')
from conf import BASE_DIR
with sqlite3.connect('omnipost/db/database.db') as conn:
    for ptype, status, file_path in conn.execute(
        "select type, status, filePath from user_info where type in (1,12)"
    ):
        path = Path(BASE_DIR) / 'cookiesFile' / (file_path or '')
        print(f"type={ptype} status={status} cookie_exists={path.exists()}")
PY
```

- `type=12` 微信公众号，`type=1` 小红书。
- Cookie 文件不存在或登录失效：先处理登录，不要把未登录当成成功。

## 1. 每日精炼稿（公众号）

```bash
DIR="河狸云蓝枢_自媒体物料包/AI能力分享内容线/<YYYY-MM-DD>"

# 1) 离线预检
omnipost/.venv/bin/python omnipost/scripts/publish_ai_share.py \
  --dir "$DIR" --platforms 12 --check-only

# 2) 存公众号草稿
omnipost/.venv/bin/python omnipost/scripts/publish_ai_share.py \
  --dir "$DIR" --platforms 12
```

预期：标题、正文 180~320 字、3 段、配图与公开分级均通过；末尾出现「已存草稿（标题与配图回读核验通过）」。

失败处理：

- `正文 X 个中文字符`：补/删到 180~320，再预检。
- `配图比例...`：按 `PERSONAL_IP_IMAGE_GUIDE.md` 换成 3:4 竖图或 16:9 横图。
- `公开分级硬拦`：改掉「首创/原创概念」等表述；提醒项逐条人工确认。
- `存草稿未通过回读核验`：不要重试发布；先查标题空白归一化、草稿箱入口和配图，再决定是否修补。

## 2. 每日精炼稿（小红书可见窗口）

```bash
DIR="河狸云蓝枢_自媒体物料包/AI能力分享内容线/<YYYY-MM-DD>"

omnipost/.venv/bin/python omnipost/scripts/publish_ai_share.py \
  --dir "$DIR" --platforms 1 --visible-xhs
```

- 会拉起一个独立 Chrome 窗口，填完草稿后**只断开 CDP，不关闭窗口**。
- 草稿只存在这个窗口的 profile；请人工复核后自行发布。
- 不要换 profile 回读，也不要把「填好了」当成「草稿已落库」。
- 窗口已打开时会复用；不要手动关掉后再跑。

## 3. 模式 B 长文（公众号）

```bash
LONG="河狸云蓝枢_自媒体物料包/AI能力分享内容线/选题-Autoloop/01_公开稿"

# 1) 离线预检
omnipost/.venv/bin/python omnipost/scripts/publish_ai_share_longform.py \
  --dir "$LONG" --check-only

# 2) 存公众号草稿
omnipost/.venv/bin/python omnipost/scripts/publish_ai_share_longform.py \
  --dir "$LONG"
```

预期：正文 ≥500 中文字符（规划建议 800~1100）、配图与 `配图位置.json` 数量一致、回读 `"readback": true`。

## 4. 修补既有草稿（不新建重复草稿）

**短稿**：先确认草稿箱中的旧标题，再执行：

```bash
omnipost/.venv/bin/python omnipost/scripts/patch_ai_share_draft.py \
  <YYYY-MM-DD> "<草稿箱中的旧标题>"
```

**长文**：

```bash
omnipost/.venv/bin/python omnipost/scripts/publish_ai_share_longform.py \
  --dir "<长文目录>" --patch-existing
```

- 旧标题必填，避免按新标题找不到草稿而新建一份。
- 修补后仍要回读标题与配图；未命中按失败处理。

## 5. 长文拆精炼稿

```bash
omnipost/.venv/bin/python omnipost/scripts/split_ip_longform.py \
  --dir "<长文目录>" --out "<输出目录>" --count 3
```

- 只生成骨架与 `拆分建议.md`，不生成成稿。
- 人工补写判断与正文后，逐篇跑 `publish_ai_share.py --check-only`，未通过不进草稿。

## 6. 公众号 Cookie 失效

1. 运行公众号存草稿命令；若脚本等待扫码，保持进程运行，扫码后不要中途结束。
2. 扫码成功后确认 Cookie 文件更新时间变化。
3. 重新执行 `--check-only`，再存草稿。
4. 不要把未登录、空白页或二维码截图当成成功。

## 7. 小红书登录失效

- 用 `--visible-xhs` 打开专用窗口，在窗口内手动扫码登录。
- 登录后再跑一次填草稿命令；草稿保存成功会提示「cookie 更新完毕」。
- 不要日常 Chrome 与专用 profile 混用。

## 8. 证据与记录

| 内容 | 路径 |
| --- | --- |
| 短稿明细 | `logs/ai_share_<YYYY-MM-DD>_<HHMMSS>.json` |
| 长文明细 | `logs/ai_share_longform_<YYYYMMDD_HHMMSS>.json` |
| 长文截图 | `logs/wechat_mp_draft_longform_<stamp>.png` |
| 公众号截图 | `omnipost/cookiesFile/wechat_mp_draft_saved.png` |
| 选题登记 | `PERSONAL_IP_TOPICS.md` |
| 发布序列 | `PERSONAL_IP_SEQUENCE.md` |

- 个人 IP 独立线不写 `publish_history`；不要用主线台账判断个人 IP 是否成功。
- 每期结束后更新 `PERSONAL_IP_TOPICS.md` 的状态与链接，并视情况更新 `PERSONAL_IP_SEQUENCE.md`。

## 9. 失败处理速查

| 现象 | 立即动作 | 不要做 |
| --- | --- | --- |
| 预检不过 | 按报错修文案/配图/公开分级 | 不要跳过预检直接存草稿 |
| 回读未命中 | 查标题空白、草稿箱入口、配图证据 | 不要重复点保存或改成发布 |
| Cookie 失效 | 重新扫码登录 | 不要用未登录状态硬跑 |
| 平台风控/审核 | 停止该平台，记录截图与日志 | 不要反复重试触发更大风控 |
| 配图重复 | 换图或改版式 | 不要复用往期图 |
| 长文拆分同质 | 换判断、重写标题 | 不要把长文段落压缩后复用 |

## 10. 验收

- 按本 Runbook 从零跑一次 `--check-only`，命令可直接复制执行。
- 每个失败场景都有明确下一步，不需要临场猜。
- 全程只存草稿；任何「发布」按钮都不点。
