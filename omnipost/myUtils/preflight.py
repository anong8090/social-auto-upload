# -*- coding: utf-8 -*-
"""发布前预检：平台封禁拦截、账号可用性、随机选台。

设计原则：
- 预检只做「能否开跑」的判断，不替用户改数据；
- 账号有效性优先看 cookie 文件是否存在且非空，真正的 cookie 在线校验是可选慢路径
  （`verify_cookie=True`），因为每个平台要开一次浏览器。
"""
from __future__ import annotations

import random
import sqlite3
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence

# 永久封禁平台：代码层硬拦截，任何入口都不得放行
BANNED_PLATFORMS: Dict[int, str] = {
    1: "小红书账号已封禁，禁止发布（AGENTS.md 铁律）",
}

PLATFORM_NAMES = {
    1: "小红书",
    2: "视频号",
    3: "抖音",
    4: "快手",
    5: "百家号",
    6: "B站",
    7: "今日头条",
    8: "搜狐号",
    9: "知乎",
    10: "百度贴吧",
    11: "微博",
    12: "微信公众号",
    13: "网易号",
    14: "大鱼号",
    15: "企鹅号",
}

# 「一稿五投」默认候选池：图文/动态可投的平台，不含视频平台与封禁平台
DEFAULT_POOL: tuple[int, ...] = (5, 7, 8, 9, 10, 11, 12, 13, 14, 15)

# 各平台标题字数红线（用于合规闸门）
TITLE_LIMITS: Dict[int, tuple[int, int]] = {
    5: (8, 30),
    7: (1, 30),
    8: (1, 30),
    9: (1, 35),
    10: (1, 100),
    11: (1, 2000),
    12: (1, 64),
    13: (5, 30),
    14: (5, 30),
    15: (5, 30),
}

# 需要上传 16:9 封面 + 正文内文图的长文平台
COVER_REQUIRED_PLATFORMS = (5, 7, 8, 9, 13, 14, 15)

# 零容忍词表唯一来源（AGENTS.md 第二节第 4 条）。
# compliance_check.py / violation_feedback.py 一律从此处引用，禁止各自复制一份。
STATIC_BANNED_WORDS: tuple[str, ...] = (
    "15天免费试点", "免费试跑", "免费领", "私信领资料", "加微信", "扫码体验", "点击链接",
    "www.ricent.com", "国家级", "第一", "唯一", "100%零失误", "全网最强", "二维码",
)

# 允许从 verified 平台判词里提取、并考虑升级为拦截词的「词法候选」。
# 平台判词整句（如「内容因违规被下架」）不在其中，绝不会被当成正文违禁词。
PROMOTABLE_RISK_TERMS: tuple[str, ...] = (
    "标题党", "标题夸大", "低质量", "营销推广", "广告嫌疑", "夸大宣传", "诱导点击",
)


class PlatformBlockedError(RuntimeError):
    """平台被永久封禁时抛出，调用方不应捕获后继续发布。"""


def assert_platform_allowed(platform_type: int) -> None:
    """封禁平台直接抛错，任何发布入口都必须先调用。"""
    reason = BANNED_PLATFORMS.get(int(platform_type))
    if reason:
        raise PlatformBlockedError(f"[type={platform_type}] {reason}")


def _connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    return conn


def get_account(
    db_path: Path, platform_type: int, *, only_enabled: bool = True
) -> Optional[Dict[str, Any]]:
    """取该平台优先级最高的账号记录（status 降序、id 降序）。"""
    db_path = Path(db_path)
    if not db_path.exists():
        return None
    sql = "SELECT * FROM user_info WHERE type = ?"
    if only_enabled:
        sql += " AND status = 1"
    sql += " ORDER BY status DESC, id DESC LIMIT 1"
    try:
        with _connect(db_path) as conn:
            row = conn.execute(sql, (int(platform_type),)).fetchone()
            return dict(row) if row else None
    except Exception:
        return None


def resolve_cookie_path(base_dir: Path, file_path: str) -> Path:
    """把 user_info.filePath 解析成绝对路径，兼容相对名与绝对路径。"""
    candidate = Path(file_path)
    if candidate.is_absolute():
        return candidate
    return Path(base_dir) / "cookiesFile" / candidate


def check_account_file(
    db_path: Path, base_dir: Path, platform_type: int
) -> tuple[bool, str, Optional[Dict[str, Any]]]:
    """快速预检（不打开浏览器）：封禁 → 账号记录 → cookie 文件存在且非空。"""
    try:
        assert_platform_allowed(platform_type)
    except PlatformBlockedError as exc:
        return False, str(exc), None

    account = get_account(db_path, platform_type)
    if not account:
        return False, f"{PLATFORM_NAMES.get(platform_type, platform_type)} 未绑定有效账号", None

    cookie_path = resolve_cookie_path(base_dir, str(account.get("filePath") or ""))
    if not cookie_path.exists():
        return False, f"cookie 文件不存在: {cookie_path.name}", account
    try:
        if cookie_path.stat().st_size < 16:
            return False, f"cookie 文件内容为空: {cookie_path.name}", account
    except OSError as exc:
        return False, f"cookie 文件不可读: {exc}", account

    return True, "ok", account


async def verify_cookie_online(
    platform_type: int, cookie_path: Path
) -> tuple[bool, str]:
    """慢路径：真正打开各平台后台校验 cookie 是否仍登录。"""
    try:
        from myUtils.auth import check_cookie

        ok = await check_cookie(int(platform_type), cookie_path.name)
        return (bool(ok), "ok" if ok else "cookie 已失效，请重新登录")
    except Exception as exc:  # noqa: BLE001 - 校验异常按不可用处理
        return False, f"cookie 校验异常: {exc}"


async def preflight(
    platform_types: Iterable[int],
    db_path: Path,
    base_dir: Path,
    *,
    verify_cookie: bool = False,
) -> List[Dict[str, Any]]:
    """对一批平台做预检，返回逐平台结果，不中断流程。

    返回项：{platform_type, platform_name, ok, reason, account, cookie_path}
    """
    results: List[Dict[str, Any]] = []
    for platform_type in platform_types:
        platform_type = int(platform_type)
        ok, reason, account = check_account_file(db_path, base_dir, platform_type)
        cookie_path = None
        if ok and account:
            cookie_path = resolve_cookie_path(base_dir, str(account.get("filePath") or ""))
            if verify_cookie:
                ok, reason = await verify_cookie_online(platform_type, cookie_path)

        results.append(
            {
                "platform_type": platform_type,
                "platform_name": PLATFORM_NAMES.get(platform_type, f"平台{platform_type}"),
                "ok": ok,
                "reason": reason,
                "account": (account or {}).get("userName"),
                "cookie_path": str(cookie_path) if cookie_path else None,
            }
        )
    return results


def pick_random_platforms(
    db_path: Path,
    base_dir: Path,
    *,
    count: int = 5,
    pool: Sequence[int] = DEFAULT_POOL,
    exclude: Iterable[int] = (),
    seed: Optional[int] = None,
) -> List[int]:
    """从候选池中随机抽取 N 个「账号文件就绪」的平台，自动剔除封禁与缺号平台。

    可复现：传 seed 时使用独立的 Random 实例，不影响全局随机状态。
    """
    excluded = {int(x) for x in exclude} | set(BANNED_PLATFORMS)
    candidates = [
        int(p)
        for p in pool
        if int(p) not in excluded
        and check_account_file(db_path, base_dir, int(p))[0]
    ]
    rng = random.Random(seed) if seed is not None else random
    if len(candidates) <= count:
        rng.shuffle(candidates)
        return candidates
    return rng.sample(candidates, count)


def describe_pool(db_path: Path, base_dir: Path) -> List[Dict[str, Any]]:
    """列出候选池每个平台的就绪状态，供 CLI 打印与排查。"""
    out = []
    for p in DEFAULT_POOL:
        ok, reason, account = check_account_file(db_path, base_dir, p)
        out.append(
            {
                "platform_type": p,
                "platform_name": PLATFORM_NAMES.get(p, str(p)),
                "ok": ok,
                "reason": reason,
                "account": (account or {}).get("userName"),
            }
        )
    return out
