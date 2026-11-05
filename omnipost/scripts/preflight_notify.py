# -*- coding: utf-8 -*-
"""定时预检共用飞书告警：受控配置、签名、单次告警去重。

模块只负责通知，不读取账号数据、不执行发布；Webhook 与密钥不写入项目日志。
"""

from __future__ import annotations

import base64
import fcntl
import hashlib
import hmac
import json
import os
import stat
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional, Tuple
from urllib.parse import urlsplit

CONFIG_PATH = Path.home() / ".config" / "omnipost" / "preflight.env"
LARK_HOSTS = frozenset({"open.feishu.cn", "open.larksuite.com"})


def _allowed_webhook_parts(parsed: Any) -> bool:
    """严格限定飞书机器人地址：无端口、userinfo、query 或 fragment，且路径含 token。"""
    if (
        parsed.scheme != "https"
        or parsed.netloc.lower() not in LARK_HOSTS
        or not parsed.path.startswith("/open-apis/bot/v2/hook/")
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        return False
    return bool(parsed.path.rstrip("/").rsplit("/", 1)[-1])


class _SafeRedirectHandler(urllib.request.HTTPRedirectHandler):
    """阻止 Webhook 被重定向到白名单之外的地址。"""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        if not _allowed_webhook_parts(urlsplit(newurl)):
            raise RuntimeError("重定向目标不在飞书白名单，已阻止")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def env_file_values(path: Path = CONFIG_PATH) -> Dict[str, str]:
    """读取用户级 KEY=VALUE 配置；忽略注释与 export 前缀，不解析行内注释。"""
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return {}
    except OSError as exc:
        raise ValueError(f"配置文件不可读: {path}: {exc}") from exc

    values: Dict[str, str] = {}
    for lineno, raw in enumerate(lines, 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].strip()
        if "=" not in line:
            raise ValueError(f"配置文件第 {lineno} 行缺少 '=': {path}")
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        if key:
            values[key] = value
    return values


def validate_config_permissions(path: Path = CONFIG_PATH) -> None:
    """真实凭据文件只允许本人读写；进程环境变量方式不触发此检查。"""
    try:
        mode = stat.S_IMODE(path.stat().st_mode)
    except FileNotFoundError:
        return
    except OSError as exc:
        raise ValueError(f"配置文件权限无法检查: {path}: {exc}") from exc
    if mode & 0o077:
        raise ValueError(f"配置文件权限过宽（{oct(mode)}）：请执行 chmod 600 {path}")


def resolve_lark_settings(
    explicit_webhook: Optional[str] = None, env_file: Path = CONFIG_PATH
) -> Tuple[Optional[str], Optional[str]]:
    """显式参数 > 进程环境 > 用户级配置文件；返回空值表示未配置。"""
    validate_config_permissions(env_file)
    file_values = env_file_values(env_file)
    webhook = (
        explicit_webhook
        or os.environ.get("LARK_WEBHOOK_URL")
        or file_values.get("LARK_WEBHOOK_URL")
        or ""
    ).strip()
    secret = (
        os.environ.get("LARK_WEBHOOK_SECRET")
        or file_values.get("LARK_WEBHOOK_SECRET")
        or ""
    ).strip()
    return (webhook or None, secret or None)


def validate_webhook(webhook: str) -> str:
    """仅允许飞书机器人 HTTPS Webhook，避免环境变量把结果投递到任意地址。"""
    if not _allowed_webhook_parts(urlsplit(webhook)):
        raise ValueError("LARK_WEBHOOK_URL 必须是飞书机器人 HTTPS Webhook 地址")
    return webhook


def lark_sign(timestamp: str, secret: str) -> str:
    """按飞书自定义机器人规则：timestamp + '\\n' + secret 作为 HMAC key，空消息 SHA256 后 Base64。"""
    string_to_sign = f"{timestamp}\n{secret}".encode("utf-8")
    hmac_code = hmac.new(string_to_sign, b"", digestmod=hashlib.sha256).digest()
    return base64.b64encode(hmac_code).decode("utf-8")


def send_lark(webhook: str, secret: Optional[str], text: str) -> None:
    """投递文本告警；非 2xx、飞书业务错误码与网络异常都视作投递结果未知。"""
    payload: Dict[str, Any] = {"msg_type": "text", "content": {"text": text}}
    if secret:
        timestamp = str(int(time.time()))
        payload["timestamp"] = timestamp
        payload["sign"] = lark_sign(timestamp, secret)
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        webhook,
        data=data,
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    opener = urllib.request.build_opener(_SafeRedirectHandler())
    with opener.open(request, timeout=10) as response:
        status = getattr(response, "status", None) or response.getcode()
        body = response.read(4096).decode("utf-8", errors="replace")
    if not 200 <= status < 300:
        raise RuntimeError(f"HTTP {status}: {body[:200]}")
    try:
        parsed_body = json.loads(body or "{}")
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"飞书响应不是 JSON: {body[:200]}") from exc
    if isinstance(parsed_body, dict):
        code = parsed_body.get("code")
        status_code = parsed_body.get("StatusCode")
        if code not in (None, 0) or status_code not in (None, 0):
            raise RuntimeError(
                f"飞书返回异常: code={code}, StatusCode={status_code}, msg={parsed_body.get('msg')}"
            )


def redact_error(exc: BaseException, webhook: Optional[str], secret: Optional[str]) -> str:
    """错误留痕前移除 URL、token 与签名密钥，并限制长度。"""
    text = str(exc)
    redactions = [secret or ""]
    if webhook:
        redactions.append(webhook)
        token = urlsplit(webhook).path.rstrip("/").rsplit("/", 1)[-1]
        if token:
            redactions.append(token)
    for value in redactions:
        if value:
            text = text.replace(value, "[REDACTED]")
    return text[:500]


def _atomic_write_json(path: Path, payload: Dict[str, Any]) -> None:
    """以 0600 原子写入状态文件，避免日志与去重标记被并发写坏。"""
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


def _marker_path(state_dir: Path, key: str) -> Path:
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()[:24]
    return state_dir / f".notify-{digest}.json"


def _read_marker(path: Path) -> Optional[Dict[str, Any]]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, json.JSONDecodeError):
        return {"state": "needs_human_check", "reason": "通知标记不可读"}
    if not isinstance(payload, dict):
        return {"state": "needs_human_check", "reason": "通知标记格式异常"}
    if payload.get("state") == "resolved":
        return None
    return payload


def mark_issue_resolved(state_dir: Path, key: str, *, now: Optional[datetime] = None) -> None:
    """问题消失后解除已发送标记，使同一问题再次出现时能重新提醒。

    pending / needs_human_check 标记属于结果未知，不允许自动清除。
    """
    now = now or datetime.now()
    state_dir.mkdir(parents=True, exist_ok=True)
    lock_path = state_dir / ".notify.lock"
    marker_path = _marker_path(state_dir, key)
    with open(lock_path, "a+", encoding="utf-8") as lock_handle:
        fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX)
        marker = _read_marker(marker_path)
        if marker and marker.get("state") == "sent":
            _atomic_write_json(
                marker_path,
                {
                    "key_digest": marker_path.stem,
                    "state": "resolved",
                    "resolved_at": now.isoformat(timespec="seconds"),
                },
            )


def notify_once(
    state_dir: Path,
    key: str,
    webhook: Optional[str],
    secret: Optional[str],
    text: str,
    *,
    now: Optional[datetime] = None,
) -> Tuple[str, str]:
    """同一 key 最多确认投递一次；结果未知时转人工核查，禁止盲重试。

    返回：(sent | skipped | unknown | not_configured, 原因)。标记只保存状态与脱敏原因。
    """
    if not text:
        return ("skipped", "")
    if not webhook:
        return ("not_configured", "未配置飞书 Webhook")
    now = now or datetime.now()
    timestamp = now.isoformat(timespec="seconds")
    state_dir.mkdir(parents=True, exist_ok=True)
    lock_path = state_dir / ".notify.lock"
    marker_path = _marker_path(state_dir, key)

    with open(lock_path, "a+", encoding="utf-8") as lock_handle:
        try:
            os.chmod(lock_path, 0o600)
        except OSError:
            pass
        fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX)
        marker = _read_marker(marker_path)
        if marker:
            state = marker.get("state")
            if state == "sent":
                return ("skipped", "同一问题已提醒过，不重复发送")
            if state == "pending":
                _atomic_write_json(
                    marker_path,
                    {
                        "key_digest": marker_path.stem,
                        "state": "needs_human_check",
                        "reason": "上次告警投递结果未知，转人工核查",
                        "updated_at": timestamp,
                    },
                )
                return ("unknown", "上次告警投递结果未知，转人工核查")
            return ("unknown", f"通知标记状态 {state}，不重复发送")

        try:
            _atomic_write_json(
                marker_path,
                {"key_digest": marker_path.stem, "state": "pending", "created_at": timestamp},
            )
        except OSError as exc:
            return ("unknown", f"通知去重标记写入失败，未发送: {redact_error(exc, webhook, secret)}")
        try:
            send_lark(webhook, secret, text)
        except Exception as exc:  # noqa: BLE001 - 投递可能已发生，结果未知
            reason = redact_error(exc, webhook, secret)
            _atomic_write_json(
                marker_path,
                {
                    "key_digest": marker_path.stem,
                    "state": "needs_human_check",
                    "reason": reason,
                    "updated_at": datetime.now().isoformat(timespec="seconds"),
                },
            )
            return ("unknown", reason)
        _atomic_write_json(
            marker_path,
            {
                "key_digest": marker_path.stem,
                "state": "sent",
                "sent_at": datetime.now().isoformat(timespec="seconds"),
            },
        )
        return ("sent", "")
