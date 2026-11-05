---
title: "account-preflight-readonly"
project: "market_for_team"
type: "task-category"
status: "level-2-observe"
date: "2026-11-05"
author: "Agent"
---

# 任务类别：账号健康预检只读巡检

## 级别：第 2 级（自动触发，只产出提案/告警）

### 目标
每日 08:30 自动检查所有可用平台的账号与 Cookie 状态，正常静默，异常发飞书告警，结果按日落地 logs/preflight/。

### 边界
- 只读：读取 user_info 表、cookie 文件、共享预检逻辑（myUtils/preflight.py）
- 写入：logs/preflight/ 目录下的结果 JSON、runs.jsonl、通知去重标记
- 禁止：发布内容、修改业务表、重新绑定账号、触发发布流程

### 验收清单
- [x] 共享预检逻辑：publish_episode.py 与 run_daily_preflight.py 均调用 myUtils/preflight.py
- [x] 每日 08:30 LaunchAgent（com.helicloud.preflight.plist）已安装并加载
- [x] 结果按日留档：logs/preflight/YYYY-MM-DD.json
- [x] 运行日志追加：logs/preflight/runs.jsonl（含状态迁移）
- [x] 飞书告警配置：用户级 ~/.config/omnipost/preflight.env（chmod 600）
- [x] 告警去重：同一问题跨天只提醒一次，问题消失后解除标记
- [x] 升级提醒：连续 2 天同一异常触发超期升级措辞
- [x] 存活检测：08:45 截止判断漏跑/卡死，独立触发告警
- [x] 退出码规范：0=完成/已完成，1=发现异常，2=配置错误，3=运行故障/告警结果未知
- [ ] 连续 7 天自动运行无遗漏（需时间验证）
- [ ] 人为让账号失效后，次日发布前能收到提醒（需人工验证）

### 权限与配置
- Python: omnipost/.venv/bin/python（3.12）
- plist: ~/Library/LaunchAgents/com.helicloud.preflight.plist
- 配置文件: ~/.config/omnipost/preflight.env（chmod 600）
- 白名单域名: open.feishu.cn, open.larksuite.com
- 路径前缀: /open-apis/bot/v2/hook/

### 依据与引用
- AUTOMATION_ROADMAP.md 第 1 件
- AGENTS.md 第四节（统一发布入口、预检复用）
- REVIEW.md A1（选台预检项）

### 负责人与复盘
- 结果负责人：用户
- 观察窗口：2 周起，至少 20 个真实任务
- 复盘入口：logs/preflight/runs.jsonl、logs/preflight/.state/notify-*.json

