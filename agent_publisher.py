#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Agent 内容分发统一适配器 (Agent Publisher Adapter)
支持小红书、百家号等多平台的图文与视频自动化分发。
可作为 CLI 命令直接调用，亦可作为 Python 模块被 LLM Agent (LangGraph/Dify/FastAPI等) 直接导入。
"""

import os
import sys
import argparse
import asyncio
import subprocess
from pathlib import Path
from datetime import datetime
from typing import List, Optional, Dict, Any

# 将 omnipost 加入 Python 模块搜索路径
CURRENT_DIR = Path(__file__).resolve().parent
OMNIPOST_DIR = CURRENT_DIR / "omnipost"
if str(OMNIPOST_DIR) not in sys.path:
    sys.path.insert(0, str(OMNIPOST_DIR))

# 默认配置路径
DEFAULT_VENV_PYTHON = OMNIPOST_DIR / ".venv" / "bin" / "python"
DEFAULT_SAU_BIN = OMNIPOST_DIR / ".venv" / "bin" / "sau"


def get_cookie_file(platform: str, account: str = "default") -> Path:
    """获取指定平台的 Cookie 文件路径"""
    # 兼容 omnipost 的 cookiesFile 目录和 cookies 目录
    cookies_dir = OMNIPOST_DIR / "cookiesFile"
    cookies_dir.mkdir(parents=True, exist_ok=True)
    
    # 百家号常用 baijiahao_account.json，小红书常用 xiaohongshu_{account}.json
    if platform == "baijiahao":
        return cookies_dir / f"baijiahao_account.json"
    else:
        return cookies_dir / f"{platform}_{account}.json"


async def check_cookie(platform: str, account: str = "default") -> bool:
    """检查平台 Cookie 是否有效"""
    if platform == "xiaohongshu":
        cmd = [str(DEFAULT_SAU_BIN), "xiaohongshu", "check", "--account", account]
        res = subprocess.run(cmd, cwd=str(OMNIPOST_DIR), capture_output=True, text=True)
        return res.returncode == 0
    elif platform == "baijiahao":
        try:
            from uploader.baijiahao_uploader.main import baijiahao_setup
            account_file = get_cookie_file("baijiahao", account)
            if not account_file.exists():
                return False
            return await baijiahao_setup(account_file, handle=False)
        except Exception as e:
            print(f"[Cookie 检查错误] 百家号: {e}")
            return False
    else:
        print(f"暂未实现该平台的在线检查: {platform}")
        return False


def publish_xiaohongshu(
    content_type: str,
    title: str,
    body: str,
    tags: List[str],
    files: List[str],
    account: str = "default",
    dry_run: bool = False,
    schedule: Optional[str] = None
) -> Dict[str, Any]:
    """发布内容到小红书 (通过 sau CLI)"""
    if not files:
        return {"success": False, "msg": "小红书发布必须提供至少一张图片或视频文件"}

    # 规范化文件路径为绝对路径
    resolved_files = [str(Path(f).resolve()) for f in files]

    if content_type == "video":
        cmd = [
            str(DEFAULT_SAU_BIN), "xiaohongshu", "upload-video",
            "--account", account,
            "--file", resolved_files[0],
            "--title", title,
            "--desc", body,
        ]
    else:  # 图文笔记
        cmd = [
            str(DEFAULT_SAU_BIN), "xiaohongshu", "upload-note",
            "--account", account,
            "--title", title,
            "--note", body,
            "--images"
        ] + resolved_files

    if tags:
        cmd.extend(["--tags", ",".join(tags)])
    if schedule:
        cmd.extend(["--schedule", schedule])

    if dry_run:
        print(f"[小红书 Dry-Run 模式] 待执行命令: {' '.join(cmd)}")
        return {"success": True, "dry_run": True, "command": cmd}

    print(f"[小红书] 正在调用 sau 执行发布: {title}")
    res = subprocess.run(cmd, cwd=str(OMNIPOST_DIR), capture_output=True, text=True)
    if res.returncode == 0:
        return {"success": True, "output": res.stdout}
    else:
        return {"success": False, "error": res.stderr or res.stdout}


async def publish_baijiahao_async(
    content_type: str,
    title: str,
    body: str,
    tags: List[str],
    files: List[str],
    account: str = "default",
    dry_run: bool = False,
    schedule: Optional[str] = None
) -> Dict[str, Any]:
    """发布内容到百家号 (通过 BaiJiaHaoArticle/BaiJiaHaoVideo)"""
    account_file = get_cookie_file("baijiahao", account)
    if not account_file.exists():
        return {
            "success": False,
            "msg": f"百家号 Cookie 文件不存在: {account_file}。请先运行登录获取 Cookie。"
        }

    cover_path = str(Path(files[0]).resolve()) if files else None

    if content_type == "video":
        from uploader.baijiahao_uploader.main import BaiJiaHaoVideo
        video_uploader = BaiJiaHaoVideo(
            title=title,
            file_path=files[0],
            tags=tags,
            publish_date=0,  # 立即发布
            account_file=account_file
        )
        await video_uploader.main()
        return {"success": True, "msg": "百家号视频任务已执行"}
    else:  # 图文文章
        from uploader.baijiahao_uploader.main import BaiJiaHaoArticle
        if not cover_path:
            return {"success": False, "msg": "百家号图文文章发布必须包含至少一张封面图片"}

        article = BaiJiaHaoArticle(
            title=title,
            body=body,
            tags=tags,
            publish_date=0,  # 0 为立即发布，亦可支持 datetime
            account_file=account_file,
            dry_run=dry_run,
            cover_path=cover_path,
            ai_generated=True
        )
        print(f"[百家号] 正在执行文章发布: {title} (dry_run={dry_run})")
        await article.main()
        return {"success": True, "msg": "百家号文章任务已执行", "dry_run": dry_run}


def publish_baijiahao(*args, **kwargs) -> Dict[str, Any]:
    """同步包装的百家号发布方法"""
    return asyncio.run(publish_baijiahao_async(*args, **kwargs))


def dispatch_content(
    platforms: List[str],
    content_type: str,
    title: str,
    body: str,
    tags: List[str],
    files: List[str],
    dry_run: bool = False
) -> Dict[str, Any]:
    """
    通用内容分发入口，供 Agent 直接调用。
    """
    results = {}
    db_file = OMNIPOST_DIR / "db" / "database.db"

    for p in platforms:
        p_clean = p.strip().lower()
        print(f"\n====== 正在分发到平台: {p_clean} ======")
        platform_type = 1 if p_clean in ["xhs", "xiaohongshu"] else (5 if p_clean in ["bjh", "baijiahao"] else 0)
        platform_name = "小红书" if platform_type == 1 else ("百家号" if platform_type == 5 else p_clean)

        res = None
        if p_clean in ["xhs", "xiaohongshu"]:
            res = publish_xiaohongshu(
                content_type=content_type,
                title=title,
                body=body,
                tags=tags,
                files=files,
                dry_run=dry_run
            )
            results["xiaohongshu"] = res
        elif p_clean in ["bjh", "baijiahao"]:
            res = publish_baijiahao(
                content_type=content_type,
                title=title,
                body=body,
                tags=tags,
                files=files,
                dry_run=dry_run
            )
            results["baijiahao"] = res
        else:
            res = {"success": False, "msg": f"暂不支持的平台: {p_clean}"}
            results[p_clean] = res

        # 写入数据库发布历史
        try:
            import json, sqlite3
            if db_file.exists():
                status = "preview" if dry_run else ("success" if res.get("success") else "failed")
                err_msg = res.get("error") or res.get("msg") if not res.get("success") else None
                with sqlite3.connect(db_file) as conn:
                    cur = conn.cursor()
                    cur.execute('''
                        INSERT INTO publish_history 
                        (platform_type, platform_name, account_name, content_type, title, content, tags, files, status, error_msg, created_at, published_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now', 'localtime'), datetime('now', 'localtime'))
                    ''', (
                        platform_type,
                        platform_name,
                        "智能体/Agent",
                        content_type,
                        title,
                        body,
                        ','.join(tags) if tags else '',
                        json.dumps(files, ensure_ascii=False) if files else '[]',
                        status,
                        str(err_msg) if err_msg else None
                    ))
                    conn.commit()
        except Exception as e:
            print(f"[提示] 记录发布历史异常: {e}")

    return results


def main():
    parser = argparse.ArgumentParser(description="Agent 多平台内容分发统一命令行")
    parser.add_argument("--action", choices=["publish", "check"], default="publish", help="执行动作")
    parser.add_argument("--platforms", default="xiaohongshu,baijiahao", help="发布平台，逗号隔开 (如: xiaohongshu,baijiahao)")
    parser.add_argument("--type", choices=["note", "video"], default="note", help="内容形式: note(图文) 或 video(视频)")
    parser.add_argument("--title", help="标题")
    parser.add_argument("--body", help="正文内容")
    parser.add_argument("--tags", default="", help="标签，逗号隔开")
    parser.add_argument("--files", nargs="*", default=[], help="图片路径列表或视频文件路径")
    parser.add_argument("--dry-run", action="store_true", help="是否以预览/Dry-run 模式运行，不真正触发发布")

    args = parser.parse_args()
    platform_list = [p.strip() for p in args.platforms.split(",") if p.strip()]

    if args.action == "check":
        print("====== 正在检查平台登录 Cookie ======")
        for p in platform_list:
            is_valid = asyncio.run(check_cookie(p))
            status = "✅ 有效 (已登录)" if is_valid else "❌ 未登录或已失效"
            print(f"- {p}: {status}")
        return

    if not args.title or not args.body:
        print("错误: 发布操作必须提供 --title 和 --body 参数")
        sys.exit(1)

    tag_list = [t.strip() for t in args.tags.split(",") if t.strip()]
    res = dispatch_content(
        platforms=platform_list,
        content_type=args.type,
        title=args.title,
        body=args.body,
        tags=tag_list,
        files=args.files,
        dry_run=args.dry_run
    )
    print("\n分发执行结果:")
    print(res)


if __name__ == "__main__":
    main()
