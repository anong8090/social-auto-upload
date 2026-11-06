#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""每日账号健康预检 + 链路巡检 + 违规聚合调度入口（第 2+3+5 级巡检）。

职责边界：
- 只读取账号与 Cookie 文件状态、写 logs/preflight/ logs/link_health/ logs/violations/ logs/review/
- 写 platform_feedback 的巡检健康字段与人工复核结论
- 绝不发布内容、改账号业务表、重新绑定账号或自动生效合规规则

退出码：
  0 = 成功/当日已完成
  1 = 发现异常（已按状态处理并告警）
  2 = 配置错误
  3 = 运行故障或告警结果未知
"""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import signal
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

OMNIPOST_DIR = Path(__file__).resolve().parent.parent
PROJECT_DIR = OMNIPOST_DIR.parent
sys.path.insert(0, str(OMNIPOST_DIR))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from conf import BASE_DIR  # noqa: E402
from myUtils import preflight  # noqa: E402
import preflight_notify  # noqa: E402
import failure_classify  # noqa: E402

DB_PATH = Path(BASE_DIR) / "db" / "database.db"
LOGS_DIR = PROJECT_DIR / "logs" / "preflight"
LINK_HEALTH_DIR = PROJECT_DIR / "logs" / "link_health"
VIOLATIONS_DIR = PROJECT_DIR / "logs" / "violations"
REVIEW_DIR = PROJECT_DIR / "logs" / "review"
RUN_LOG_PATH = LOGS_DIR / "runs.jsonl"
STATE_DIR = LOGS_DIR / ".state"
CONFIG_PATH = preflight_notify.CONFIG_PATH
WINDOW_TIME = "08:30"
DEADLINE_TIME = "08:45"
MONITOR_GRACE_SECONDS = 600
MAX_RUNTIME_SECONDS = 300  # 增加到 5 分钟以容纳链路巡检
MAX_ATTEMPTS = 3
RETRY_BACKOFF_SECONDS = (0, 2, 5)
LOCK_STALE_SECONDS = MAX_RUNTIME_SECONDS + 60
ESCALATION_AFTER_DAYS = 2
SUCCEEDED_STATES = frozenset({"success", "attention_needed"})


def _now() -> datetime:
    return datetime.now()


def _atomic_write_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        os.replace(tmp, path)
    finally:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass


def _read_json(path: Path) -> Optional[Dict[str, Any]]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, json.JSONDecodeError):
        return {"state": "needs_human_check", "reason": "状态文件不可读或格式异常"}
    return payload if isinstance(payload, dict) else {"state": "needs_human_check", "reason": "状态文件格式异常"}


def _append_run_log(record: Dict[str, Any]) -> None:
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    line = json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n"
    with open(RUN_LOG_PATH, "a", encoding="utf-8") as handle:
        handle.write(line)


def _rule_version() -> str:
    """所有会影响结果/告警/规则产物的文件都进版本，避免改了规则却仍判定「结果匹配」。"""
    parts = [
        Path(__file__).resolve(),
        OMNIPOST_DIR / "myUtils" / "preflight.py",
        Path(preflight_notify.__file__).resolve(),
        Path(__file__).resolve().parent / "link_health_check.py",
        Path(__file__).resolve().parent / "violation_feedback.py",
        Path(__file__).resolve().parent / "compliance_check.py",
        Path(__file__).resolve().parent / "failure_classify.py",
        Path(__file__).resolve().parent / "review_sync.py",
        Path(__file__).resolve().parent / "feedback_schema.py",
    ]
    digest = hashlib.sha256()
    for path in parts:
        digest.update(str(path).encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()[:16]


def _dedupe_key(now: datetime, rule_version: str) -> str:
    raw = json.dumps(
        {
            "category": "account-preflight-readonly",
            "window": now.strftime("%Y-%m-%d 08:30"),
            "target": str(DB_PATH),
            "platforms": list(preflight.DEFAULT_POOL),
            "rule_version": rule_version,
        },
        sort_keys=True,
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def _load_config() -> Tuple[Optional[str], Optional[str]]:
    webhook, secret = preflight_notify.resolve_lark_settings(None, CONFIG_PATH)
    if webhook:
        webhook = preflight_notify.validate_webhook(webhook)
    return webhook, secret


def _consecutive_failure_days(now: datetime, issue_signature: str) -> int:
    """统计此前连续出现同一异常签名的天数，最多回看 7 天。"""
    days = 0
    for offset in range(1, 8):
        data = _read_json(LOGS_DIR / f"{(now - timedelta(days=offset)):%Y-%m-%d}.json")
        results = data.get("results") if data and isinstance(data.get("results"), list) else []
        prior = [r for r in results if isinstance(r, dict) and not r.get("ok")]
        prior_signature = ",".join(str(r.get("platform_type")) for r in prior)
        if prior and prior_signature == issue_signature:
            days += 1
        else:
            break
    return days


def _build_message(
    results: List[Dict[str, Any]],
    *,
    record: Dict[str, Any],
    error: str = "",
    streak: int = 1,
) -> str:
    lines = ["⚠️ 账号预检发现异常，请在发布前处理：", ""]
    for item in results:
        if not item.get("ok"):
            lines.append(
                f"- {item['platform_name']}（{item.get('account') or '未绑定'}）：{item.get('reason', '未知原因')}"
            )
    if error:
        lines.append(f"- 运行异常：{error}")
    if streak >= ESCALATION_AFTER_DAYS:
        lines.append(f"- 同一问题已连续 {streak} 天，请优先处理；本任务不自动修复账号。")
    lines.extend(
        [
            "",
            "建议：打开本地控制台「账号管理」或使用对应 login_*.py 重新登录；",
            "Cookie 文件位于 omnipost/cookiesFile/，不要提交或转发。",
            f"证据：logs/preflight/{record['date']}.json、logs/preflight/runs.jsonl。",
            f"任务 ID：{record['task_id']}；影响：仅提醒，不自动修改账号或发布内容。",
        ]
    )
    return "\n".join(lines)


def _run_check() -> List[Dict[str, Any]]:
    """执行共享预检；单次运行受 MAX_RUNTIME_SECONDS 硬预算约束。"""
    import asyncio

    started = time.monotonic()

    def _timeout(_signum: int, _frame: Any) -> None:
        raise TimeoutError(f"预检超过 {MAX_RUNTIME_SECONDS}s 预算")

    alarm_armed = False
    previous_handler: Any = None
    try:
        previous_handler = signal.signal(signal.SIGALRM, _timeout)
        signal.setitimer(signal.ITIMER_REAL, MAX_RUNTIME_SECONDS)
        alarm_armed = True
    except (ValueError, AttributeError, OSError):
        alarm_armed = False
    try:
        results = asyncio.run(
            preflight.preflight(
                list(preflight.DEFAULT_POOL), DB_PATH, Path(BASE_DIR), verify_cookie=False
            )
        )
    finally:
        if alarm_armed:
            signal.setitimer(signal.ITIMER_REAL, 0)
            signal.signal(signal.SIGALRM, previous_handler)
    elapsed = time.monotonic() - started
    if elapsed > MAX_RUNTIME_SECONDS:
        raise TimeoutError(f"预检耗时 {elapsed:.1f}s，超过 {MAX_RUNTIME_SECONDS}s 预算")
    return results


def _run_link_health_check() -> List[Dict[str, Any]]:
    """执行链路存活巡检；告警由统一汇总逻辑发送，这里显式关闭逐条告警。"""
    import asyncio
    import link_health_check  # scripts 目录已在 sys.path 中

    return asyncio.run(
        link_health_check.run_health_check(
            DB_PATH, lookback_days=30, concurrency=2, send_alerts=False,
        )
    )


def _sync_human_reviews() -> Dict[str, Any]:
    """同步飞书复核台：先回写人工结论，再把新待复核证据上行。"""
    import review_sync  # scripts 目录已在 sys.path 中

    result = review_sync.sync_reviews(DB_PATH, days=30, dry_run=False)
    result["ok"] = bool(result.get("ok"))
    return result


def _run_violation_feedback() -> Dict[str, Any]:
    """执行违规聚合，只写观察提案；无记录时也要自愈回静态基线并清理旧规则。"""
    import violation_feedback  # scripts 目录已在 sys.path 中

    records = violation_feedback.load_feedback(DB_PATH, days=30)
    signals = violation_feedback.extract_violation_signals(records)
    dynamic_words = violation_feedback.build_dynamic_banned_words(signals)
    guide_md = violation_feedback.generate_avoid_guide(signals, dynamic_words)
    platform_rules = violation_feedback.generate_platform_rules(signals)
    result = violation_feedback.write_outputs(dynamic_words, guide_md, platform_rules)
    result["unverified_records_ignored"] = signals.get("unverified_count", 0)
    result["human_reviewed_count"] = signals.get("human_reviewed_count", 0)
    return result


def _mark_resolved_issues(now: datetime) -> None:
    """正常结果解除此前 7 天内已确认投递的告警标记，使复发时可重新提醒。"""
    for offset in range(1, 8):
        data = _read_json(LOGS_DIR / f"{(now - timedelta(days=offset)):%Y-%m-%d}.json")
        results = data.get("results") if data and isinstance(data.get("results"), list) else []
        prior = [r for r in results if isinstance(r, dict) and not r.get("ok")]
        if not prior:
            continue
        signature = ",".join(str(r.get("platform_type")) for r in prior)
        preflight_notify.mark_issue_resolved(STATE_DIR, f"account-issue|{signature}")
        preflight_notify.mark_issue_resolved(STATE_DIR, f"escalation|{signature}")


def _build_link_health_alert(health_results: List[Dict[str, Any]]) -> Optional[str]:
    """构建链路巡检告警；verified 异常与 unverified 待复核分开呈现。"""
    verified = [
        r for r in health_results
        if r.get("evidence_quality") == "verified" and r.get("check_status") != "alive"
    ]
    unverified = [r for r in health_results if r.get("evidence_quality") != "verified"]
    if not verified and not unverified:
        return None

    lines = ["🔗 链路存活巡检结果：", ""]
    if verified:
        lines.append("已验证异常（可进入违规复盘）：")
        for r in verified[:12]:
            lines.append(
                f"- {r['platform_name']}（期数 {r.get('episode', '?')}，{r['check_status']}）：{r.get('published_url', '')}"
            )
            if r.get("evidence_text"):
                lines.append(f"  判词：{r['evidence_text']}")
    if unverified:
        lines.append("")
        lines.append(f"待人工复核：{len(unverified)} 条（登录墙/反爬/容器未命中，不进入规则聚合）")
        reasons = [
            str(r.get("evidence_reason") or "证据不足")
            for r in unverified
        ]
        for reason, count in sorted(
            {reason: reasons.count(reason) for reason in reasons}.items(),
            key=lambda item: -item[1],
        )[:4]:
            lines.append(f"  - {reason} × {count}")
    lines.extend(
        [
            "",
            f"证据：{LINK_HEALTH_DIR}/health_check_*.json",
            "影响：仅提醒与记录，不自动修改发布内容、不自动改合规规则。",
        ]
    )
    return "\n".join(lines)


def _build_review_alert(
    review_result: Dict[str, Any],
    review_error: str = "",
) -> Optional[str]:
    """构建人工复核台汇总；同步失败必须显式呈现，不得静默吞掉。"""
    if review_error:
        return "\n".join([
            "🚨 飞书人工复核同步失败：",
            "",
            f"- 原因：{review_error}",
            f"- 本地证据：logs/review/、logs/preflight/runs.jsonl",
            "- 影响：本轮新增待复核项可能未写入复核台；人工结论也未保证回写。",
            "- 处理：检查 lark-cli user 身份与复核台配置后人工重跑 review_sync.py。",
        ])
    if not review_result:
        return None
    base_url = str(review_result.get("base_url") or "")
    pending = int(review_result.get("pending_in_base") or 0)
    created = int(review_result.get("created") or 0)
    reviewed = int(review_result.get("reviewed") or 0)
    upgraded = int(review_result.get("verified_upgraded") or 0)
    conflicts = int(review_result.get("conflicts") or 0)
    if not (pending or created or reviewed or conflicts or review_result.get("unprocessed_reviews")):
        return None
    lines = [
        "🧾 飞书人工复核台同步完成：",
        "",
        f"- 复核台：{base_url}" if base_url else "- 复核台：未配置 URL",
        f"- 待复核：{pending} 条；本轮新增：{created} 条",
        f"- 已回写人工结论：{reviewed} 条；其中确认异常并升级证据：{upgraded} 条",
        f"- 需人工检查的冲突：{conflicts} 条；复核台未处理结论：{review_result.get('unprocessed_reviews', 0)} 条",
        "",
        "操作：打开复核台，在「复核结论」选择确认正常 / 确认异常 / 忽略；",
        "确认异常还需选择「人工判定状态」并写明创作者后台核实依据。",
        "规则变更仍只产出观察提案，不自动改发布拦截词。",
    ]
    return "\n".join(lines)


def _build_violation_feedback_alert(feedback_result: Dict[str, Any]) -> Optional[str]:
    """构建违规聚合告警；明确标注为观察提案，不再宣称自动生效。"""
    if not feedback_result.get("updated"):
        return None

    lines = [
        "📊 违规反馈聚合完成（观察提案，未自动改规则）：",
        "",
        f"- 静态零容忍词：{feedback_result['high_risk_count']} 个（唯一来源 preflight.py）",
        f"- verified 信号桶：{feedback_result.get('verified_signal_buckets', 0)} 个",
        f"- 观察词候选：{feedback_result.get('observed_term_count', 0)} 个，均待人工确认",
        f"- unverified 待复核：{feedback_result.get('unverified_records_ignored', 0)} 条；"
        f"人工已复核：{feedback_result.get('human_reviewed_count', 0)} 条",
        f"- 平台建议文件：{feedback_result['platform_rules_count']} 个（proposal_only）",
        "",
        f"提案：{feedback_result['dynamic_words']}",
        f"指南：{feedback_result['avoid_guide']}",
        "",
        "规则变更仍需人工确认并验证效果；发布闸门只加载静态词表与白名单内候选。",
    ]
    return "\n".join(lines)


def _run_once(args: argparse.Namespace, task_id: str, window: str, now: datetime) -> int:
    """带互斥锁运行单次预检 + 链路巡检 + 违规聚合；仅临时环境/超时类错误限次重试。"""
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    LINK_HEALTH_DIR.mkdir(parents=True, exist_ok=True)
    VIOLATIONS_DIR.mkdir(parents=True, exist_ok=True)
    REVIEW_DIR.mkdir(parents=True, exist_ok=True)
    lock_path = STATE_DIR / "daily-preflight.lock"
    try:
        stat = lock_path.stat()
        if time.time() - stat.st_mtime > LOCK_STALE_SECONDS:
            lock_path.unlink()
    except FileNotFoundError:
        pass

    started_at = now.isoformat(timespec="seconds")
    config_error = ""
    webhook: Optional[str] = None
    secret: Optional[str] = None
    try:
        webhook, secret = _load_config()
    except (ValueError, OSError) as exc:
        config_error = preflight_notify.redact_error(exc, None, None)
        print(f"❌ 配置错误: {config_error}", file=sys.stderr)

    record_base = {
        "task_id": task_id,
        "dedupe_key": task_id,
        "category": "account-preflight-readonly",
        "window": window,
        "date": now.strftime("%Y-%m-%d"),
        "target": str(DB_PATH),
        "rule_version": _rule_version(),
        "evaluation_version": "preflight-static-v1",
        "pid": os.getpid(),
        "config_source": str(CONFIG_PATH),
    }
    result_path = LOGS_DIR / f"{record_base['date']}.json"

    with open(lock_path, "a+", encoding="utf-8") as lock_handle:
        acquired = False
        try:
            fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            acquired = True
        except BlockingIOError:
            if not args.quiet:
                print("已有同一目标预检正在运行，本次退出")
            return 0
        try:
            os.chmod(lock_path, 0o600)
        except OSError:
            pass
        lock_handle.seek(0)
        lock_handle.truncate()
        lock_handle.write(f"pid={os.getpid()} task_id={task_id} started_at={started_at}\n")
        lock_handle.flush()
        os.utime(lock_path, (time.time(), time.time()))
        _atomic_write_json(
            result_path,
            {
                **record_base,
                "state": "running",
                "attempts": 0,
                "started_at": started_at,
                "finished_at": None,
                "error_reason": None,
            },
        )
        _append_run_log(
            {
                **record_base,
                "state": "running",
                "attempts": 0,
                "started_at": started_at,
                "deadline": f"{record_base['date']} {DEADLINE_TIME}",
                "budget_seconds": MAX_RUNTIME_SECONDS,
                "max_attempts": MAX_ATTEMPTS,
            }
        )

        attempts = 0
        results: List[Dict[str, Any]] = []
        health_results: List[Dict[str, Any]] = []
        feedback_result: Dict[str, Any] = {}
        error_reason = config_error

        # 步骤 1：账号健康预检
        if not config_error:
            for attempt in range(1, MAX_ATTEMPTS + 1):
                attempts = attempt
                try:
                    results = _run_check()
                    error_reason = ""
                    break
                except (TimeoutError, OSError) as exc:
                    error_reason = preflight_notify.redact_error(exc, webhook, secret)
                    if attempt < MAX_ATTEMPTS:
                        time.sleep(RETRY_BACKOFF_SECONDS[attempt])
                except Exception as exc:  # noqa: BLE001
                    error_reason = preflight_notify.redact_error(exc, webhook, secret)
                    break

        # 步骤 2：链路存活巡检（仅在预检无致命错误时执行）
        if not error_reason:
            try:
                health_results = _run_link_health_check()
            except Exception as exc:  # noqa: BLE001
                print(f"⚠️ 链路巡检异常: {preflight_notify.redact_error(exc, webhook, secret)}", file=sys.stderr)

        # 步骤 3：人工复核结论回写 + 新待复核证据上行
        # 前置预检失败时本步骤按设计跳过，不伪装成「复核同步失败」。
        review_result: Dict[str, Any] = {}
        review_error = ""
        review_skipped = bool(error_reason)
        if not error_reason:
            try:
                review_result = _sync_human_reviews()
                if not review_result.get("ok"):
                    review_error = "复核台读回校验未通过"
            except Exception as exc:  # noqa: BLE001
                review_error = preflight_notify.redact_error(exc, webhook, secret)
                print(f"⚠️ 飞书人工复核同步异常: {review_error}", file=sys.stderr)

        # 步骤 4：违规聚合分析（只采信机器 verified 或人工确认异常后的证据）
        if not error_reason:
            try:
                feedback_result = _run_violation_feedback()
            except Exception as exc:  # noqa: BLE001
                print(f"⚠️ 违规聚合异常: {preflight_notify.redact_error(exc, webhook, secret)}", file=sys.stderr)

        failed = [r for r in results if not r.get("ok")]
        issue_signature = error_reason or ",".join(str(r["platform_type"]) for r in failed)
        streak = 1 + _consecutive_failure_days(now, issue_signature) if failed else 1
        status = "failed" if error_reason else ("attention_needed" if failed else "success")
        notify_state = "skipped"
        notify_reason = ""

        # ========== 汇总所有告警消息，统一发送（去重按 key） ==========
        alert_messages: List[str] = []
        review_fingerprint = ""

        # 账号预检异常
        if error_reason or failed:
            alert_messages.append(_build_message(results, record=record_base, error=error_reason, streak=streak))

        # 前置预检失败时复核同步按设计跳过；汇总里显式说明，避免误以为复核台已同步。
        if review_skipped:
            alert_messages.append(
                "⏸️ 飞书人工复核同步已跳过：账号预检前置步骤失败；"
                "本轮不写复核台、不回写人工结论，待预检恢复后由下次任务重试。"
            )

        # 链路巡检：有 verified 异常或待复核记录时汇总一次
        health_alert = _build_link_health_alert(health_results)
        if health_alert:
            alert_messages.append(health_alert)

        # 人工复核台：待复核数量、复核链接、回写结果与同步失败
        review_alert = _build_review_alert(review_result, review_error)
        if review_alert:
            review_fingerprint = hashlib.sha256(
                json.dumps(
                    {
                        "pending": review_result.get("pending_in_base", 0),
                        "created": review_result.get("created", 0),
                        "reviewed": review_result.get("reviewed", 0),
                        "upgraded": review_result.get("verified_upgraded", 0),
                        "conflicts": review_result.get("conflicts", 0),
                        "unprocessed": review_result.get("unprocessed_reviews", 0),
                        "error": review_error,
                    },
                    sort_keys=True,
                ).encode()
            ).hexdigest()[:8]
            alert_messages.append(f"{review_alert}\n\n复核同步签名：{review_fingerprint}")

        # 违规聚合：每天重写观察提案，内容未变时不重复推送
        feedback_alert = _build_violation_feedback_alert(feedback_result)
        if feedback_alert:
            fingerprint = hashlib.sha256(
                json.dumps(
                    {
                        "verified": feedback_result.get("verified_signal_buckets", 0),
                        "observed": feedback_result.get("observed_term_count", 0),
                        "unverified": feedback_result.get("unverified_records_ignored", 0),
                        "rules": feedback_result.get("platform_rules_count", 0),
                    },
                    sort_keys=True,
                ).encode()
            ).hexdigest()[:8]
            alert_messages.append(f"{feedback_alert}\n\n聚合签名：{fingerprint}")

        # 昨日失败汇总（第 4 件）
        yesterday = now - timedelta(days=1)
        yesterday_failures = failure_classify.get_yesterday_network_failures(DB_PATH)
        login_expired_today = [r for r in failed if "cookie" in r.get("reason", "").lower() or "登录" in r.get("reason", "") or "失效" in r.get("reason", "")]
        if yesterday_failures or login_expired_today:
            lines = ["📋 昨日发布失败汇总：", ""]
            if login_expired_today:
                lines.append("⚠️ 今日预检发现登录失效（需人工重新绑定，不自动补发）：")
                for r in login_expired_today:
                    lines.append(f"  - {r['platform_name']}（{r.get('account') or '未绑定'}）：{r.get('reason', '未知原因')}")
                lines.append("")
            if yesterday_failures:
                lines.append("🔄 昨日 NETWORK 类失败，符合自动补发条件（下次发布时自动重试）：")
                for r in yesterday_failures:
                    lines.append(f"  - {r['platform_name']}（{r.get('account_name') or '未知账号'}）：期数 {r.get('episode')}")
                lines.append("")
            alert_messages.append("\n".join(lines))

        # 发送合并告警
        if alert_messages:
            combined_message = "\n\n---\n\n".join(alert_messages)
            notify_state, notify_reason = preflight_notify.notify_once(
                STATE_DIR,
                f"daily-summary|{now:%Y-%m-%d}|{issue_signature}|review:{review_fingerprint}",
                webhook,
                secret,
                combined_message,
            )
            if streak >= ESCALATION_AFTER_DAYS and notify_state != "unknown":
                escalation_text = (
                    "🚨 每日巡检异常超期\n"
                    f"- 同一问题已连续 {streak} 天未解决：{issue_signature[:200]}\n"
                    f"- 当日证据：logs/preflight/{record_base['date']}.json、logs/link_health/、logs/violations/\n"
                    "- 请负责人确认处理或人工升级；任务不会自动修改账号或发布内容。"
                )
                escalation_state, escalation_reason = preflight_notify.notify_once(
                    STATE_DIR,
                    f"escalation|{issue_signature}",
                    webhook,
                    secret,
                    escalation_text,
                )
                if escalation_state == "unknown":
                    notify_state = "unknown"
                    notify_reason = escalation_reason
                elif escalation_state == "sent":
                    notify_state = "sent"
        else:
            _mark_resolved_issues(now)

        if notify_state == "unknown":
            status = "needs_human_check"
        if notify_state == "not_configured" and alert_messages:
            print("⚠️ 发现异常/故障，但未配置飞书 Webhook；仅写本地结果，需人工配置告警", file=sys.stderr)

        summary = {
            "ok": sum(1 for r in results if r.get("ok")),
            "failed": len(failed),
            "unbound": sum(1 for r in failed if "未绑定" in r.get("reason", "")),
            "link_health_checked": len(health_results),
            "link_health_verified_abnormal": sum(
                1 for r in health_results
                if r.get("evidence_quality") == "verified" and r.get("check_status") != "alive"
            ),
            "link_health_unverified": sum(
                1 for r in health_results if r.get("evidence_quality") != "verified"
            ),
            "violation_feedback_updated": feedback_result.get("updated", False),
            "violation_observed_terms": feedback_result.get("observed_term_count", 0),
            "review_pending": review_result.get("pending_in_base", 0),
            "review_created": review_result.get("created", 0),
            "review_writebacks": review_result.get("reviewed", 0),
            "review_conflicts": review_result.get("conflicts", 0),
            "review_sync_skipped": review_skipped,
            "review_sync_failed": bool(
                review_error or (not review_skipped and not review_result.get("ok"))
            ),
        }
        finished_at = _now().isoformat(timespec="seconds")
        result_payload = {
            **record_base,
            "state": status,
            "attempts": attempts,
            "started_at": started_at,
            "finished_at": finished_at,
            "checked_at": finished_at,
            "summary": summary,
            "results": results,
            "artifact": str(result_path),
            "notification_state": notify_state,
            "notification_sent": notify_state == "sent",
            "notification_reason": notify_reason or None,
            "error_reason": error_reason or None,
            "access": "local-only",
            "allowed_writes": [
                "logs/preflight/", "logs/link_health/", "logs/violations/", "logs/review/",
                "omnipost/db/database.db（仅 publish_history 健康字段、platform_feedback、幂等 schema 补齐）",
                "飞书多维表格「蓝枢发布链路人工复核台」（待复核记录与回写状态）",
            ],
            "forbidden_actions": [
                "publish", "account-rebind",
                "publish_drafts 写入", "user_info 写入", "台账写入", "合规规则自动生效",
            ],
        }
        current = _read_json(result_path)
        if not error_reason and results:
            _atomic_write_json(result_path, result_payload)
        elif current and current.get("state") in SUCCEEDED_STATES:
            result_payload["artifact"] = str(STATE_DIR / f"failed-{task_id}.json")
            _atomic_write_json(STATE_DIR / f"failed-{task_id}.json", result_payload)
        else:
            _atomic_write_json(result_path, result_payload)

        _append_run_log(
            {
                **record_base,
                "state": status,
                "attempts": attempts,
                "started_at": started_at,
                "finished_at": finished_at,
                "deadline": f"{record_base['date']} {DEADLINE_TIME}",
                "budget_seconds": MAX_RUNTIME_SECONDS,
                "max_attempts": MAX_ATTEMPTS,
                "summary": summary,
                "notification_state": notify_state,
                "notification_sent": notify_state == "sent",
                "notification_reason": notify_reason or None,
                "error_reason": error_reason or None,
                "result_artifact": result_payload["artifact"],
            }
        )
        if status in SUCCEEDED_STATES or status == "failed":
            _atomic_write_json(
                STATE_DIR / f"last-completed-{record_base['date']}.json",
                {
                    "window": window,
                    "task_id": task_id,
                    "state": status,
                    "finished_at": finished_at,
                    "result_artifact": result_payload["artifact"],
                },
            )

        if config_error:
            return 2
        if error_reason:
            print(f"❌ 预检运行失败: {error_reason}", file=sys.stderr)
            return 3
        if notify_state == "unknown":
            print(f"⚠️ 告警投递结果未知，需人工核查: {notify_reason}", file=sys.stderr)
            return 3
        if not args.quiet:
            print(
                f"✅ 当日巡检完成：账号正常 {summary['ok']}、异常 {summary['failed']}；"
                f"链路巡检 {summary['link_health_checked']} 条"
                f"（verified 异常 {summary['link_health_verified_abnormal']}、"
                f"待复核 {summary['link_health_unverified']}）；"
                f"复核台待复核 {summary['review_pending']}、回写 {summary['review_writebacks']}；"
                f"观察词候选 {summary['violation_observed_terms']} 个"
                f"（任务 {task_id}）"
            )
        # 待人工复核不单独制造「异常退出码」；复核同步故障与已验证异常需要显露
        return 1 if (
            failed
            or summary["link_health_verified_abnormal"] > 0
            or summary["review_sync_failed"]
            or summary["review_conflicts"] > 0
        ) else 0


def _status_check(args: argparse.Namespace, task_id: str, window: str, today: datetime) -> int:
    """独立存活检测：按 08:45 截止判断漏跑、卡死或结果待核查。"""
    deadline = datetime.strptime(f"{today:%Y-%m-%d} {DEADLINE_TIME}", "%Y-%m-%d %H:%M")
    now = _now()
    monitor_state_path = STATE_DIR / "monitor-state.json"
    monitor_state = _read_json(monitor_state_path) or {}
    if monitor_state.get("date") != today.strftime("%Y-%m-%d"):
        monitor_state = {"date": today.strftime("%Y-%m-%d"), "state": "waiting"}
    monitor_started = monitor_state.get("started_at")
    if not monitor_started:
        monitor_started = now.isoformat(timespec="seconds")
    monitor_state["started_at"] = monitor_started
    started_dt = datetime.fromisoformat(monitor_started)
    if now < deadline and (now - started_dt).total_seconds() < MONITOR_GRACE_SECONDS and not args.force:
        monitor_state["state"] = "waiting"
        _atomic_write_json(monitor_state_path, monitor_state)
        return 0
    if monitor_state.get("state") == "completed" and not args.force:
        return 0

    config_problem = ""
    webhook: Optional[str] = None
    secret: Optional[str] = None
    try:
        webhook, secret = _load_config()
    except (ValueError, OSError) as exc:
        config_problem = f"告警配置不可用: {preflight_notify.redact_error(exc, None, None)}"
    try:
        rule_version = _rule_version()
    except Exception as exc:  # noqa: BLE001 - 规则文件缺失也要触发存活告警
        rule_version = None
        config_problem = config_problem or f"规则版本无法计算: {preflight_notify.redact_error(exc, None, None)}"

    result_path = LOGS_DIR / f"{today:%Y-%m-%d}.json"
    result = _read_json(result_path)
    problem = config_problem
    if not problem:
        if result is None:
            problem = "未发现当日预检结果，疑似漏跑或任务卡死"
        elif result.get("window") != window or result.get("rule_version") != rule_version:
            problem = "当日结果的任务窗口或规则版本不匹配"
        elif result.get("state") == "running":
            problem = "当日预检仍为 running，疑似任务卡死或崩溃"
        elif result.get("state") in {"failed", "needs_human_check"}:
            problem = f"当日预检状态为 {result.get('state')}：{result.get('error_reason') or '需人工核查'}"
        elif not result.get("finished_at"):
            problem = "当日预检结果缺少完成时间，疑似卡死"
    if not problem:
        monitor_state["state"] = "completed"
        monitor_state["completed_at"] = now.isoformat(timespec="seconds")
        _atomic_write_json(monitor_state_path, monitor_state)
        if not args.quiet:
            print(f"✅ {today:%Y-%m-%d} {WINDOW_TIME} 预检已在截止前完成")
        return 0

    state, reason = preflight_notify.notify_once(
        STATE_DIR,
        f"watchdog|{window}|{problem}",
        webhook,
        secret,
        (
            "🚨 定时预检存活检测异常\n"
            f"- 问题：{problem}\n"
            f"- 检查窗口：{window}，截止：{deadline:%Y-%m-%d %H:%M}\n"
            f"- 证据：logs/preflight/runs.jsonl、logs/preflight/{today:%Y-%m-%d}.json\n"
            "- 建议：检查 launchctl print gui/$(id -u)/com.helicloud.preflight 与 launchd 日志后人工补跑。\n"
            f"- 任务 ID：{task_id}；影响：账号异常可能未被提醒；恢复办法：人工运行 run_daily_preflight.py。"
        ),
    )
    _append_run_log(
        {
            "task_id": f"watchdog-{task_id}",
            "category": "preflight-watchdog",
            "window": window,
            "state": "attention_needed" if state == "sent" else "needs_human_check",
            "checked_at": now.isoformat(timespec="seconds"),
            "problem": problem,
            "notification_state": state,
            "notification_reason": reason or None,
        }
    )
    monitor_state["state"] = "attention_needed" if state == "sent" else "needs_human_check"
    monitor_state["problem"] = problem
    monitor_state["updated_at"] = now.isoformat(timespec="seconds")
    _atomic_write_json(monitor_state_path, monitor_state)
    print(f"⚠️ {problem}；存活检测告警状态：{state}", file=sys.stderr)
    return 3


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="每日 08:30 账号健康预检与独立存活检测")
    parser.add_argument("--config-check", action="store_true", help="检查告警配置，不执行预检")
    parser.add_argument("--status-check", action="store_true", help="检查当日窗口是否按时完成")
    parser.add_argument("--force", action="store_true", help="存活检测忽略 08:45 截止时间（仅用于演练）")
    parser.add_argument("--quiet", "-q", action="store_true", help="正常时静默")
    args = parser.parse_args(argv)

    if args.config_check:
        try:
            webhook, secret = _load_config()
            if not webhook:
                print(f"❌ 未配置 LARK_WEBHOOK_URL（配置文件 {CONFIG_PATH}）", file=sys.stderr)
                return 2
        except (ValueError, OSError) as exc:
            print(f"❌ 配置错误: {preflight_notify.redact_error(exc, None, None)}", file=sys.stderr)
            return 2
        if not args.quiet:
            print(f"✅ Webhook 配置可用（{'已配置签名密钥' if secret else '未配置签名密钥'}，URL 已隐藏）")
        return 0

    if not DB_PATH.exists():
        print(f"❌ 数据库不存在: {DB_PATH}", file=sys.stderr)
        return 2
    try:
        rule_version = _rule_version()
    except Exception as exc:  # noqa: BLE001
        print(f"❌ 规则版本无法计算: {preflight_notify.redact_error(exc, None, None)}", file=sys.stderr)
        return 2

    today = _now()
    window = f"{today:%Y-%m-%d} {WINDOW_TIME}"
    task_id = _dedupe_key(today, rule_version)
    if args.status_check:
        return _status_check(args, task_id, window, today)

    # 只认定时任务写出的主结果文件；手动 CLI 结果以 .manual.json 结尾，不影响去重判断
    existing = _read_json(LOGS_DIR / f"{today:%Y-%m-%d}.json")
    if (
        existing
        and existing.get("state") in SUCCEEDED_STATES
        and existing.get("window") == window
        and existing.get("rule_version") == rule_version
    ):
        if not args.quiet:
            print(f"当日窗口已完成预检（任务 {task_id}），跳过重复执行")
        return 0
    return _run_once(args, task_id, window, today)


if __name__ == "__main__":
    raise SystemExit(main())
