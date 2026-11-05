#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""账号健康预检独立 CLI 入口（供手动排查与发布前调用）。

定时任务的正式入口是 run_daily_preflight.py；两者都调用 myUtils/preflight.py，
不另写一套账号判定逻辑。本 CLI 的飞书配置与告警函数复用 preflight_notify.py。

用法示例：
    python scripts/preflight_accounts.py                    # 默认检查 DEFAULT_POOL
    python scripts/preflight_accounts.py --platforms 5,7,9  # 指定平台
    python scripts/preflight_accounts.py --verify-cookie    # 开启浏览器在线校验
    python scripts/preflight_accounts.py --json             # 输出结构化 JSON
    python scripts/preflight_accounts.py --save --quiet     # 落地并静默

返回码：0=全部正常，1=有失效/未绑定，2=参数/环境错误，3=运行或告警投递故障。
"""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

OMNIPOST_DIR = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(OMNIPOST_DIR))
sys.path.insert(0, str(SCRIPTS_DIR))

from conf import BASE_DIR  # noqa: E402
from myUtils import preflight  # noqa: E402
import preflight_notify  # noqa: E402

DB_PATH = Path(BASE_DIR) / "db" / "database.db"
LOGS_DIR = OMNIPOST_DIR.parent / "logs" / "preflight"
CONFIG_PATH = preflight_notify.CONFIG_PATH
LOCK_STALE_SECONDS = 300


def _print_table(results: List[Dict[str, Any]]) -> None:
    """美化打印预检结果表格。"""
    if not results:
        print("（无结果）")
        return
    print(f"{'平台':<8} {'状态':<6} {'账号':<16} {'原因'}")
    print("-" * 72)
    for r in results:
        status_icon = "✅" if r["ok"] else "❌"
        account = r.get("account") or "—"
        reason = r.get("reason", "")
        print(f"{r['platform_name']:<8} {status_icon}     {account:<16} {reason}")


def _summarize(results: List[Dict[str, Any]]) -> Dict[str, int]:
    """统计正常/失效/未绑定数量。"""
    ok = sum(1 for r in results if r["ok"])
    failed = sum(1 for r in results if not r["ok"])
    unbound = sum(1 for r in results if not r["ok"] and "未绑定" in r.get("reason", ""))
    return {"ok": ok, "failed": failed, "unbound": unbound}


def _build_lark_message(results: List[Dict[str, Any]], *, warning: str = "") -> str:
    """构建飞书告警消息，只包含失效项与必要恢复建议，不含凭据。"""
    failed = [r for r in results if not r["ok"]]
    if not failed and not warning:
        return ""
    lines: List[str] = ["⚠️ 账号预检发现异常，请及时处理：", ""]
    for r in failed:
        lines.append(
            f"- {r['platform_name']}（{r.get('account') or '未绑定'}）：{r.get('reason', '未知原因')}"
        )
    if warning:
        lines.append(f"- 运行异常：{warning}")
    lines.extend(
        [
            "",
            "建议操作：",
            "1. 打开本地控制台「账号管理」检查登录态，或使用对应 login_*.py 重新登录。",
            "2. Cookie 文件位于 omnipost/cookiesFile/，不要提交或转发。",
            f"3. 查看当日结果：logs/preflight/{datetime.now():%Y-%m-%d}.json。",
        ]
    )
    return "\n".join(lines)


def _config_signature(args: argparse.Namespace, platforms: List[int]) -> str:
    """把任务目标与规则配置纳入去重标识；配置变更后不复用旧结果。"""
    raw = json.dumps(
        {
            "date": datetime.now().strftime("%Y-%m-%d"),
            "module": "preflight-manual-cli",
            "platforms": platforms,
            "verify_cookie": args.verify_cookie,
            "script_digest": hashlib.sha256(Path(__file__).read_bytes()).hexdigest()[:16],
            "shared_digest": hashlib.sha256(
                (OMNIPOST_DIR / "myUtils" / "preflight.py").read_bytes()
            ).hexdigest()[:16],
        },
        sort_keys=True,
        ensure_ascii=False,
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def _save_result(
    results: List[Dict[str, Any]],
    output_dir: Path,
    *,
    task_id: str,
    status: str,
    attempts: int,
    warning: str = "",
    notified: bool = False,
    filename: Optional[str] = None,
) -> Path:
    """原子写入当日结果，含运行状态、去重标识、尝试次数与告警状态。

    手动 CLI 结果以 .manual 后缀区分，避免覆盖定时任务的主结果文件。
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    if filename is None:
        filename = f"{datetime.now():%Y-%m-%d}.manual.json"
    out_path = output_dir / filename
    payload: Dict[str, Any] = {
        "task_id": task_id,
        "category": "account-preflight-manual-cli",
        "window": datetime.now().strftime("%Y-%m-%d"),
        "dedupe_key": task_id,
        "target_version": str(DB_PATH),
        "status": status,
        "attempts": attempts,
        "started_at": datetime.now().isoformat(timespec="seconds"),
        "finished_at": datetime.now().isoformat(timespec="seconds"),
        "checked_at": datetime.now().isoformat(timespec="seconds"),
        "summary": _summarize(results),
        "results": results,
        "artifact": str(out_path),
        "notification_sent": notified,
        "error_reason": warning or None,
    }
    tmp_path = output_dir / f".{filename}.{os.getpid()}.tmp"
    try:
        tmp_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        os.replace(tmp_path, out_path)
    finally:
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass
    return out_path


def _preflight_lock(output_dir: Path) -> Tuple[Any, Path]:
    """获取互斥文件锁；崩溃留下的过期锁会自动回收。"""
    output_dir.mkdir(parents=True, exist_ok=True)
    lock_path = output_dir / ".preflight-manual.lock"
    now = time.time()
    try:
        stat = lock_path.stat()
        if now - stat.st_mtime > LOCK_STALE_SECONDS:
            lock_path.unlink()
    except FileNotFoundError:
        pass
    handle = open(lock_path, "a+", encoding="utf-8")
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as exc:
        handle.close()
        raise RuntimeError("已有同一目标预检正在运行") from exc
    handle.seek(0)
    handle.truncate()
    handle.write(f"pid={os.getpid()} started_at={datetime.now().isoformat(timespec='seconds')}\n")
    handle.flush()
    os.utime(lock_path, (now, now))
    return handle, lock_path


def _quiet_output(args: argparse.Namespace, results: List[Dict[str, Any]], saved: Optional[Path]) -> None:
    """正常静默；仅 JSON 模式输出结构化结果。"""
    if args.json:
        payload: Dict[str, Any] = {
            "checked_at": datetime.now().isoformat(timespec="seconds"),
            "summary": _summarize(results),
            "results": results,
        }
        if saved:
            payload["artifact"] = str(saved)
        print(json.dumps(payload, ensure_ascii=False))
    elif saved and not args.quiet:
        print(f"💾 结果已保存: {saved}")


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="账号健康预检 - 独立可调用 CLI",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例：
  python scripts/preflight_accounts.py                         # 检查默认候选池
  python scripts/preflight_accounts.py --platforms 5,7,9       # 指定平台类型
  python scripts/preflight_accounts.py --verify-cookie         # 开启浏览器在线校验
  python scripts/preflight_accounts.py --json                  # 输出 JSON
  python scripts/preflight_accounts.py --save --quiet          # 静默按日留档
  python scripts/preflight_accounts.py --config-check          # 检查告警配置
        """,
    )
    parser.add_argument("--platforms", help="逗号分隔的平台类型，如 5,7,9；默认检查 DEFAULT_POOL")
    parser.add_argument(
        "--verify-cookie", action="store_true", help="开启浏览器在线校验 Cookie（较慢，逐平台打开页面）"
    )
    parser.add_argument("--json", action="store_true", help="输出结构化 JSON 到 stdout")
    parser.add_argument("--save", action="store_true", help="将结果保存到 logs/preflight/YYYY-MM-DD.json")
    parser.add_argument("--output-dir", type=Path, default=LOGS_DIR, help=f"保存目录（默认 {LOGS_DIR}）")
    parser.add_argument("--lark-webhook", help="飞书群机器人 Webhook URL；建议使用用户级配置文件")
    parser.add_argument(
        "--config-check", action="store_true", help="检查 Webhook 配置是否存在且格式合法，不读取数据库"
    )
    parser.add_argument("--quiet", "-q", action="store_true", help="正常时不打印表格或提示")
    args = parser.parse_args(argv)

    webhook: Optional[str] = None
    secret: Optional[str] = None
    try:
        webhook, secret = preflight_notify.resolve_lark_settings(args.lark_webhook, CONFIG_PATH)
        if webhook:
            webhook = preflight_notify.validate_webhook(webhook)
    except (ValueError, OSError) as exc:
        print(f"❌ Webhook 配置错误: {preflight_notify.redact_error(exc, None, None)}", file=sys.stderr)
        return 2

    if args.config_check:
        if not webhook:
            print(f"❌ 未配置 LARK_WEBHOOK_URL（配置文件 {CONFIG_PATH}）", file=sys.stderr)
            return 2
        if not args.quiet:
            print(f"✅ Webhook 配置可用（{'已配置签名密钥' if secret else '未配置签名密钥'}，URL 已隐藏）")
        return 0

    if not DB_PATH.exists():
        print(f"❌ 数据库不存在: {DB_PATH}", file=sys.stderr)
        return 2
    if args.platforms:
        try:
            platforms = [int(x.strip()) for x in args.platforms.split(",") if x.strip()]
        except ValueError:
            print("❌ --platforms 格式错误，应为逗号分隔的数字，如 5,7,9", file=sys.stderr)
            return 2
    else:
        platforms = list(preflight.DEFAULT_POOL)

    task_id = _config_signature(args, platforms)
    lock: Optional[Any] = None
    lock_path: Optional[Path] = None
    acquired = False
    try:
        lock, lock_path = _preflight_lock(args.output_dir)
        acquired = True
        import asyncio

        results = asyncio.run(
            preflight.preflight(
                platforms, DB_PATH, Path(BASE_DIR), verify_cookie=args.verify_cookie
            )
        )
        failed = [r for r in results if not r["ok"]]
        warning = ""
        notified = False
        if failed and webhook:
            try:
                preflight_notify.send_lark(webhook, secret, _build_lark_message(results))
                notified = True
            except Exception as exc:  # noqa: BLE001 - 通知结果未知，不自动重试
                warning = preflight_notify.redact_error(exc, webhook, secret)
                print(f"⚠️ 飞书告警投递失败或结果未知: {warning}", file=sys.stderr)
        elif failed and not args.quiet:
            print("⚠️ 发现异常账号；未配置飞书 Webhook，仅写入本地结果", file=sys.stderr)

        status = "failed" if warning else ("attention_needed" if failed else "success")
        saved: Optional[Path] = None
        if args.save:
            saved = _save_result(
                results,
                args.output_dir,
                task_id=task_id,
                status=status,
                attempts=1,
                warning=warning,
                notified=notified,
            )
        if not args.quiet and not args.json:
            _print_table(results)
            summary = _summarize(results)
            print(f"\n📊 汇总：正常 {summary['ok']} | 失效 {summary['failed']} | 未绑定 {summary['unbound']}")
        _quiet_output(args, results, saved)
        return 3 if warning else (1 if failed else 0)
    except Exception as exc:  # noqa: BLE001 - 运行异常必须留痕
        reason = preflight_notify.redact_error(exc, webhook, secret)
        print(f"❌ 预检运行失败: {reason}", file=sys.stderr)
        if acquired:
            try:
                _save_result(
                    [],
                    args.output_dir,
                    task_id=task_id,
                    status="failed",
                    attempts=1,
                    warning=reason,
                    notified=False,
                )
            except Exception as save_exc:  # noqa: BLE001
                print(f"⚠️ 失败留痕未写入: {save_exc}", file=sys.stderr)
        return 3
    finally:
        if lock is not None:
            try:
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
            finally:
                lock.close()
        if acquired and lock_path is not None:
            try:
                lock_path.unlink(missing_ok=True)
            except OSError:
                pass


if __name__ == "__main__":
    raise SystemExit(main())
