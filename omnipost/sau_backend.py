import asyncio
import json
import os
import requests
import sqlite3
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path
from queue import Queue
from flask_cors import CORS
from myUtils.auth import check_cookie
from flask import Flask, request, jsonify, Response, render_template, send_from_directory
from myUtils.login import get_tencent_cookie, douyin_cookie_gen, get_ks_cookie, xiaohongshu_cookie_gen, baijiahao_cookie_gen, bilibili_cookie_gen, toutiao_cookie_gen, sohu_cookie_gen, zhihu_cookie_gen, tieba_cookie_gen
from myUtils.postVideo import post_video_tencent, post_article_tencent, post_video_DouYin, post_video_ks, post_video_xhs, post_note_xhs, post_video_baijiahao, post_video_bilibili, post_video_toutiao, post_article_toutiao, post_article_baijiahao, post_article_sohu, post_article_zhihu, post_article_tieba
from uploader.douyin_uploader.content_stats import (
    DouyinStatsSyncError,
    ensure_content_stats_table,
    replace_account_stats,
    row_to_stat_item,
    sync_recent_items_sync as sync_douyin_content_stats,
)
from uploader.ks_uploader.content_stats import (
    KuaishouStatsSyncError,
    sync_recent_items_sync as sync_kuaishou_content_stats,
)
from uploader.xiaohongshu_uploader.content_stats import (
    XiaohongshuStatsSyncError,
    sync_recent_items_sync as sync_xiaohongshu_content_stats,
)
from uploader.bilibili_uploader.content_stats import (
    BilibiliStatsSyncError,
    sync_recent_items_sync as sync_bilibili_content_stats,
)

SUPPORTED_STATS_PLATFORMS = {
    "douyin": {"type": 3, "label": "抖音"},
    "kuaishou": {"type": 4, "label": "快手"},
    "xiaohongshu": {"type": 1, "label": "小红书"},
    "bilibili": {"type": 6, "label": "B站"},
}

import conf as app_conf
from conf import BASE_DIR
from myUtils.dingtalk import (
    DingTalkPushError,
    format_multi_account_stats_markdown,
    send_markdown,
)


def _dingtalk_webhook_url() -> str:
    return (getattr(app_conf, "DINGTALK_WEBHOOK_URL", None) or "").strip()


def _dingtalk_secret() -> str:
    return (getattr(app_conf, "DINGTALK_SECRET", None) or "").strip()


def _dingtalk_rehost_covers() -> bool:
    return bool(getattr(app_conf, "DINGTALK_REHOST_COVERS", True))


def _dingtalk_push_covers() -> bool:
    return bool(getattr(app_conf, "DINGTALK_PUSH_COVERS", True))


active_queues = {}
app = Flask(__name__)


def _db_path():
    return Path(BASE_DIR / "db" / "database.db")


PLATFORM_NAME_MAP = {
    1: '小红书',
    2: '视频号',
    3: '抖音',
    4: '快手',
    5: '百家号',
    6: 'B站',
    7: '今日头条',
    8: '搜狐',
    9: '知乎',
    10: '百度贴吧',
}

def add_publish_history(platform_type, account_name, content_type, title, content, tags, files, status='publishing', tieba_fid=None, tieba_kw=None):
    try:
        platform_name = PLATFORM_NAME_MAP.get(platform_type, f'平台{platform_type}')
        tags_str = ','.join(tags) if isinstance(tags, list) else str(tags or '')
        files_str = json.dumps(files, ensure_ascii=False) if isinstance(files, (list, tuple)) else str(files or '')
        with sqlite3.connect(_db_path()) as conn:
            cur = conn.cursor()
            cur.execute('''
                INSERT INTO publish_history
                (platform_type, platform_name, account_name, content_type, title, content, tags, files, tieba_fid, tieba_kw, status, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now', 'localtime'))
            ''', (platform_type, platform_name, str(account_name), content_type, title, content, tags_str, files_str, tieba_fid or None, tieba_kw or None, status))
            conn.commit()
            return cur.lastrowid
    except Exception as e:
        print(f"写入发布历史记录失败: {e}", flush=True)
        return None

def update_publish_history(history_id, status, error_msg=None):
    if not history_id:
        return
    try:
        with sqlite3.connect(_db_path()) as conn:
            cur = conn.cursor()
            cur.execute('''
                UPDATE publish_history 
                SET status = ?, error_msg = ?, published_at = datetime('now', 'localtime')
                WHERE id = ?
            ''', (status, str(error_msg or '') if error_msg else None, history_id))
            conn.commit()
    except Exception as e:
        print(f"更新发布历史记录失败: {e}", flush=True)

@app.route('/getPublishHistory', methods=['GET'])
def get_publish_history():
    try:
        page = int(request.args.get('page', 1))
        page_size = int(request.args.get('pageSize', 20))
        platform = request.args.get('platform')
        status = request.args.get('status')
        keyword = request.args.get('keyword', '').strip()

        offset = (page - 1) * page_size
        conditions = []
        params = []

        if platform:
            conditions.append("platform_type = ?")
            params.append(int(platform))
        if status:
            conditions.append("status = ?")
            params.append(status)
        if keyword:
            conditions.append("(title LIKE ? OR content LIKE ?)")
            params.extend([f"%{keyword}%", f"%{keyword}%"])

        where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""

        with sqlite3.connect(_db_path()) as conn:
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()
            
            cur.execute(f"SELECT COUNT(*) as total FROM publish_history {where_clause}", params)
            total = cur.fetchone()['total']

            query_sql = f"""
                SELECT id, platform_type, platform_name, account_name, content_type, 
                       title, content, tags, files, status, error_msg, created_at, published_at
                FROM publish_history
                {where_clause}
                ORDER BY id DESC
                LIMIT ? OFFSET ?
            """
            cur.execute(query_sql, params + [page_size, offset])
            rows = [dict(row) for row in cur.fetchall()]

            for item in rows:
                try:
                    item['files'] = json.loads(item['files']) if item['files'] else []
                except Exception:
                    item['files'] = [item['files']] if item['files'] else []
                item['tags'] = [t for t in (item['tags'] or '').split(',') if t]

            return jsonify({"code": 200, "data": {"list": rows, "total": total}, "msg": None}), 200
    except Exception as e:
        return jsonify({"code": 500, "msg": f"获取发布历史失败: {e}", "data": None}), 500

@app.route('/deletePublishHistory', methods=['POST'])
def delete_publish_history():
    try:
        data = request.get_json() or {}
        history_id = data.get('id')
        if not history_id:
            return jsonify({"code": 400, "msg": "缺少历史记录 ID", "data": None}), 400
        with sqlite3.connect(_db_path()) as conn:
            cur = conn.cursor()
            cur.execute("DELETE FROM publish_history WHERE id = ?", (history_id,))
            conn.commit()
        return jsonify({"code": 200, "msg": "删除成功", "data": None}), 200
    except Exception as e:
        return jsonify({"code": 500, "msg": f"删除失败: {e}", "data": None}), 500

@app.route('/retryPublish', methods=['POST'])
def retry_publish():
    try:
        data = request.get_json() or {}
        history_id = data.get('id')
        if not history_id:
            return jsonify({"code": 400, "msg": "缺少历史记录 ID", "data": None}), 400

        with sqlite3.connect(_db_path()) as conn:
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()
            cur.execute("SELECT * FROM publish_history WHERE id = ?", (history_id,))
            record = cur.fetchone()
            if not record:
                return jsonify({"code": 404, "msg": "未找到对应的发布记录", "data": None}), 404
            record = dict(record)

            platform_type = record.get('platform_type')
            account_name = record.get('account_name')

            cur.execute("SELECT filePath FROM user_info WHERE userName = ? AND type = ?", (account_name, platform_type))
            acc = cur.fetchone()
            if not acc:
                cur.execute("SELECT filePath FROM user_info WHERE type = ?", (platform_type,))
                acc = cur.fetchone()
            if not acc:
                return jsonify({"code": 400, "msg": "未找到该平台绑定的有效账号，请先在账号管理中添加", "data": None}), 400
            account_file = acc['filePath']

            cur.execute('''
                UPDATE publish_history 
                SET status = 'publishing', error_msg = NULL, published_at = NULL 
                WHERE id = ?
            ''', (history_id,))
            conn.commit()

        content_type = record.get('content_type') or 'video'
        title = record.get('title') or ''
        article_body = record.get('content') or ''
        tags = [t for t in (record.get('tags') or '').split(',') if t]

        file_list = []
        if record.get('files'):
            try:
                parsed_files = json.loads(record['files'])
                if isinstance(parsed_files, list):
                    file_list = [f if isinstance(f, str) else (f.get('path') or f.get('filename') or '') for f in parsed_files]
            except Exception:
                file_list = [f for f in record['files'].split(',') if f]

        thumbnail_path = file_list[0] if file_list else ''
        account_list = [account_file]

        def do_retry():
            try:
                print(f"🔄 正在重试发布任务 #{history_id}: type={platform_type}, title={title}", flush=True)
                match platform_type:
                    case 1:
                        if content_type == 'article':
                            post_note_xhs(title, file_list, article_body, tags, account_list, False, 1, ['10:00'], 0, False, False)
                        else:
                            post_video_xhs(title, file_list, tags, account_list, 0, 0, 1, ['10:00'], 0, False, False)
                    case 2:
                        post_video_tencent(title, file_list, tags, account_list, 0, 0, 1, ['10:00'], 0, False, False, False)
                    case 3:
                        post_video_DouYin(title, file_list, tags, account_list, 0, 0, 1, ['10:00'], 0, '', '', '', False, False)
                    case 4:
                        post_video_ks(title, file_list, tags, account_list, 0, 0, 1, ['10:00'], 0, False, False)
                    case 5:
                        if content_type == 'article':
                            post_article_baijiahao(title, article_body, tags, account_list, 0, 1, ['10:00'], 0, False, thumbnail_path)
                        else:
                            post_video_baijiahao(title, file_list, tags, account_list, 0, 0, 1, ['10:00'], 0, False)
                    case 6:
                        post_video_bilibili(title, file_list, tags, account_list, 0, 0, 1, ['10:00'], 0, False, None)
                    case 7:
                        if content_type == 'article':
                            post_article_toutiao(title, article_body, tags, account_list, 0, 1, ['10:00'], 0, False, thumbnail_path, [])
                        else:
                            post_video_toutiao(title, file_list, tags, account_list, 0, 0, 1, ['10:00'], 0, False)
                    case 8:
                        post_article_sohu(title, article_body, tags, account_list, 0, 1, ['10:00'], 0, False, thumbnail_path, None, file_list)
                    case 9:
                        post_article_zhihu(title, article_body, tags, account_list, 0, 1, ['10:00'], 0, False, thumbnail_path, None)
                    case 10:
                        tieba_fid_h = record.get('tieba_fid') or None
                        tieba_kw_h = record.get('tieba_kw') or None
                        post_article_tieba(title, article_body, tags, account_list, fid=tieba_fid_h, kw=tieba_kw_h, enableTimer=False,
                                           videos_per_day=1, daily_times=['10:00'], start_days=0, dry_run=False,
                                           cover_path=thumbnail_path, is_draft=True)
                print(f"✅ 重试发布任务完成: #{history_id}", flush=True)
                update_publish_history(history_id, 'success')
            except Exception as e:
                print(f"❌ 重试发布任务出错: {e}", flush=True)
                update_publish_history(history_id, 'failed', error_msg=str(e))

        t = threading.Thread(target=do_retry, daemon=True)
        t.start()

        return jsonify({"code": 200, "msg": "已重新提交发布任务", "data": {"id": history_id}}), 200
    except Exception as e:
        print(f"重试发布失败: {e}")
        return jsonify({"code": 500, "msg": f"重试失败: {e}", "data": None}), 500

def _ensure_publish_drafts_columns(conn):
    try:
        cur = conn.cursor()
        cur.execute("PRAGMA table_info(publish_drafts)")
        existing_cols = [c[1] for c in cur.fetchall()]
        if 'status' not in existing_cols:
            cur.execute("ALTER TABLE publish_drafts ADD COLUMN status TEXT DEFAULT 'draft'")
        if 'completion_note' not in existing_cols:
            cur.execute("ALTER TABLE publish_drafts ADD COLUMN completion_note TEXT")
        if 'synced_platforms' not in existing_cols:
            cur.execute("ALTER TABLE publish_drafts ADD COLUMN synced_platforms TEXT")
        conn.commit()
    except Exception as e:
        print(f"检查 publish_drafts 列结构时出错: {e}")

@app.route('/saveDraft', methods=['POST'])
def save_draft():
    try:
        data = request.get_json() or {}
        draft_id = data.get('id')
        title = data.get('title', '')
        platform_type = data.get('platform_type') or data.get('type') or 1
        platform_name = PLATFORM_NAME_MAP.get(int(platform_type), f'平台{platform_type}')
        content_type = data.get('content_type') or data.get('contentType') or 'note'
        body = data.get('body') or data.get('articleBody') or ''
        tags = data.get('tags', [])
        tags_str = ','.join(tags) if isinstance(tags, list) else str(tags or '')
        files = data.get('files') or data.get('fileList') or []
        files_json = json.dumps(files, ensure_ascii=False) if isinstance(files, (list, tuple)) else str(files or '')
        account_ids = data.get('account_ids') or data.get('selectedAccounts') or []
        account_ids_json = json.dumps(account_ids, ensure_ascii=False) if isinstance(account_ids, list) else str(account_ids or '')
        ai_generated = 1 if data.get('aiGenerated') else 0
        dry_run = 1 if data.get('dryRun') else 0
        status = data.get('status', 'draft')
        completion_note = data.get('completion_note')
        synced_platforms = data.get('synced_platforms')

        with sqlite3.connect(_db_path()) as conn:
            _ensure_publish_drafts_columns(conn)
            cur = conn.cursor()
            if draft_id:
                cur.execute('''
                    UPDATE publish_drafts
                    SET title = ?, platform_type = ?, platform_name = ?, content_type = ?,
                        body = ?, tags = ?, files_json = ?, account_ids_json = ?,
                        ai_generated = ?, dry_run = ?, 
                        status = COALESCE(?, status),
                        completion_note = CASE WHEN ? IS NOT NULL THEN ? ELSE completion_note END,
                        synced_platforms = CASE WHEN ? IS NOT NULL THEN ? ELSE synced_platforms END,
                        updated_at = datetime('now', 'localtime')
                    WHERE id = ?
                ''', (title, platform_type, platform_name, content_type, body, tags_str, files_json, account_ids_json, ai_generated, dry_run, status, completion_note, completion_note, synced_platforms, synced_platforms, draft_id))
            else:
                cur.execute('''
                    INSERT INTO publish_drafts
                    (title, platform_type, platform_name, content_type, body, tags, files_json, account_ids_json, ai_generated, dry_run, status, completion_note, synced_platforms, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now', 'localtime'), datetime('now', 'localtime'))
                ''', (title, platform_type, platform_name, content_type, body, tags_str, files_json, account_ids_json, ai_generated, dry_run, status, completion_note, synced_platforms))
                draft_id = cur.lastrowid
            conn.commit()

        return jsonify({"code": 200, "msg": "草稿保存成功", "data": {"id": draft_id}}), 200
    except Exception as e:
        return jsonify({"code": 500, "msg": f"保存草稿失败: {e}", "data": None}), 500

@app.route('/updateDraftStatus', methods=['POST'])
def update_draft_status():
    try:
        data = request.get_json() or {}
        draft_id = data.get('id')
        if not draft_id:
            return jsonify({"code": 400, "msg": "缺少草稿 ID", "data": None}), 400
        status = data.get('status', 'draft')
        completion_note = data.get('completion_note')
        synced_platforms = data.get('synced_platforms')

        with sqlite3.connect(_db_path()) as conn:
            _ensure_publish_drafts_columns(conn)
            cur = conn.cursor()
            cur.execute('''
                UPDATE publish_drafts
                SET status = ?,
                    completion_note = CASE WHEN ? IS NOT NULL THEN ? ELSE completion_note END,
                    synced_platforms = CASE WHEN ? IS NOT NULL THEN ? ELSE synced_platforms END,
                    updated_at = datetime('now', 'localtime')
                WHERE id = ?
            ''', (status, completion_note, completion_note, synced_platforms, synced_platforms, draft_id))
            conn.commit()
        return jsonify({"code": 200, "msg": "草稿状态更新成功", "data": {"id": draft_id, "status": status}}), 200
    except Exception as e:
        return jsonify({"code": 500, "msg": f"更新草稿状态失败: {e}", "data": None}), 500

@app.route('/getDrafts', methods=['GET'])
def get_drafts():
    try:
        filter_status = request.args.get('status')
        with sqlite3.connect(_db_path()) as conn:
            _ensure_publish_drafts_columns(conn)
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()
            if filter_status:
                cur.execute("SELECT * FROM publish_drafts WHERE status = ? ORDER BY updated_at DESC, id DESC", (filter_status,))
            else:
                cur.execute("SELECT * FROM publish_drafts ORDER BY updated_at DESC, id DESC")
            rows = [dict(r) for r in cur.fetchall()]
            for item in rows:
                try:
                    item['files'] = json.loads(item['files_json']) if item.get('files_json') else []
                except Exception:
                    item['files'] = []
                try:
                    item['account_ids'] = json.loads(item['account_ids_json']) if item.get('account_ids_json') else []
                except Exception:
                    item['account_ids'] = []
                item['tags'] = [t for t in (item.get('tags') or '').split(',') if t]
                if not item.get('status'):
                    item['status'] = 'draft'
        return jsonify({"code": 200, "data": rows, "msg": None}), 200
    except Exception as e:
        return jsonify({"code": 500, "msg": f"获取草稿列表失败: {e}", "data": None}), 500

@app.route('/deleteDraft', methods=['POST'])
def delete_draft():
    try:
        data = request.get_json() or {}
        draft_ids = data.get('ids')
        draft_id = data.get('id')
        if draft_ids and isinstance(draft_ids, list):
            target_ids = [int(x) for x in draft_ids if x is not None]
        elif draft_id is not None:
            target_ids = [int(draft_id)]
        else:
            return jsonify({"code": 400, "msg": "缺少草稿 ID", "data": None}), 400

        if not target_ids:
            return jsonify({"code": 400, "msg": "未提供有效的草稿 ID", "data": None}), 400

        placeholders = ','.join(['?'] * len(target_ids))
        with sqlite3.connect(_db_path()) as conn:
            cur = conn.cursor()
            cur.execute(f"DELETE FROM publish_drafts WHERE id IN ({placeholders})", target_ids)
            conn.commit()
        return jsonify({"code": 200, "msg": f"已删除 {len(target_ids)} 篇草稿", "data": {"deleted_count": len(target_ids)}}), 200
    except Exception as e:
        return jsonify({"code": 500, "msg": f"删除草稿失败: {e}", "data": None}), 500


@app.route('/confirmDrafts', methods=['POST'])
def confirm_drafts():
    """将选中的草稿从 publish_drafts 移除，并作为已发布完结（status='success'）写入 publish_history"""
    try:
        data = request.get_json() or {}
        draft_ids = data.get('ids')
        draft_id = data.get('id')
        if draft_ids and isinstance(draft_ids, list):
            target_ids = [int(x) for x in draft_ids if x is not None]
        elif draft_id is not None:
            target_ids = [int(draft_id)]
        else:
            return jsonify({"code": 400, "msg": "缺少草稿 ID", "data": None}), 400

        if not target_ids:
            return jsonify({"code": 400, "msg": "未选择任何草稿", "data": None}), 400

        placeholders = ','.join(['?'] * len(target_ids))
        with sqlite3.connect(_db_path()) as conn:
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()

            cur.execute(f"SELECT * FROM publish_drafts WHERE id IN ({placeholders}) ORDER BY id ASC", target_ids)
            draft_rows = [dict(r) for r in cur.fetchall()]
            if not draft_rows:
                return jsonify({"code": 404, "msg": "未找到对应的草稿记录", "data": None}), 404

            # 预加载各平台默认账号名称映射
            cur.execute("SELECT id, type, userName, filePath FROM user_info")
            user_rows = [dict(u) for u in cur.fetchall()]
            type_to_username = {}
            id_to_username = {}
            file_to_username = {}
            for u in user_rows:
                id_to_username[u['id']] = u['userName']
                file_to_username[u['filePath']] = u['userName']
                if u['type'] not in type_to_username:
                    type_to_username[u['type']] = u['userName']

            inserted_ids = []
            for d in draft_rows:
                p_type = int(d.get('platform_type') or 1)
                p_name = d.get('platform_name') or PLATFORM_NAME_MAP.get(p_type, f'平台{p_type}')
                c_type = d.get('content_type') or 'article'
                title = d.get('title') or ''
                content = d.get('body') or ''
                tags_str = d.get('tags') or ''

                # 归一化 files 为文件名字符串数组
                norm_files = []
                raw_files_json = d.get('files_json')
                if raw_files_json:
                    try:
                        parsed = json.loads(raw_files_json)
                        if isinstance(parsed, list):
                            for f in parsed:
                                if isinstance(f, dict):
                                    fname = f.get('filename') or f.get('path') or f.get('name') or ''
                                    if fname:
                                        norm_files.append(fname)
                                elif isinstance(f, str) and f:
                                    norm_files.append(f)
                    except Exception:
                        pass
                files_str = json.dumps(norm_files, ensure_ascii=False)

                # 解析账号名称
                acc_name = type_to_username.get(p_type, '默认账号')
                raw_acc = d.get('account_ids_json')
                if raw_acc:
                    try:
                        acc_list = json.loads(raw_acc)
                        if isinstance(acc_list, list) and acc_list:
                            first_acc = acc_list[0]
                            if isinstance(first_acc, int) and first_acc in id_to_username:
                                acc_name = id_to_username[first_acc]
                            elif isinstance(first_acc, str) and first_acc in file_to_username:
                                acc_name = file_to_username[first_acc]
                            elif isinstance(first_acc, str) and not first_acc.endswith('.json'):
                                acc_name = first_acc
                    except Exception:
                        pass

                cur.execute('''
                    INSERT INTO publish_history
                    (platform_type, platform_name, account_name, content_type, title, content, tags, files, status, created_at, published_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'success', COALESCE(?, datetime('now', 'localtime')), datetime('now', 'localtime'))
                ''', (p_type, p_name, acc_name, c_type, title, content, tags_str, files_str, d.get('updated_at') or d.get('created_at')))
                inserted_ids.append(cur.lastrowid)

            cur.execute(f"DELETE FROM publish_drafts WHERE id IN ({placeholders})", target_ids)
            conn.commit()

        return jsonify({
            "code": 200,
            "msg": f"已确认完结 {len(inserted_ids)} 篇草稿并转入发布记录",
            "data": {"confirmed_count": len(inserted_ids), "history_ids": inserted_ids}
        }), 200
    except Exception as e:
        return jsonify({"code": 500, "msg": f"确认完结草稿失败: {e}", "data": None}), 500


def _row_to_stat_item(row):
    return row_to_stat_item(row)


def _load_stats_payload(conn, platform: str, account_id: int):
    ensure_content_stats_table(conn)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute(
        """
        SELECT * FROM content_stats
        WHERE platform = ? AND account_id = ?
        ORDER BY published_at DESC, id DESC
        """,
        (platform, account_id),
    )
    rows = cur.fetchall()
    last = rows[0]["synced_at"] if rows else None
    return {"items": [_row_to_stat_item(r) for r in rows], "lastSyncedAt": last}

#允许所有来源跨域访问
CORS(app)

# 限制上传文件大小为160MB
app.config['MAX_CONTENT_LENGTH'] = 160 * 1024 * 1024

# 获取当前目录（假设 index.html 和 assets 在这里）
current_dir = os.path.dirname(os.path.abspath(__file__))

# 处理所有静态资源请求（未来打包用）
@app.route('/assets/<filename>')
def custom_static(filename):
    return send_from_directory(os.path.join(current_dir, 'assets'), filename)

# 处理 favicon.ico 静态资源（未来打包用）
@app.route('/favicon.ico')
def favicon():
    return send_from_directory(os.path.join(current_dir, 'assets'), 'vite.svg')

@app.route('/vite.svg')
def vite_svg():
    return send_from_directory(os.path.join(current_dir, 'assets'), 'vite.svg')

# （未来打包用）
@app.route('/')
def index():  # put application's code here
    return send_from_directory(current_dir, 'index.html')

@app.route('/upload', methods=['POST'])
def upload_file():
    if 'file' not in request.files:
        return jsonify({
            "code": 400,
            "data": None,
            "msg": "No file part in the request"
        }), 400
    file = request.files['file']
    if file.filename == '':
        return jsonify({
            "code": 400,
            "data": None,
            "msg": "No selected file"
        }), 400
    try:
        # 保存文件到指定位置
        uuid_v1 = uuid.uuid1()
        print(f"UUID v1: {uuid_v1}")
        save_dir = Path(BASE_DIR / "videoFile")
        os.makedirs(save_dir, exist_ok=True)
        filepath = Path(save_dir / f"{uuid_v1}_{file.filename}")
        file.save(filepath)
        return jsonify({"code":200,"msg": "File uploaded successfully", "data": f"{uuid_v1}_{file.filename}"}), 200
    except Exception as e:
        return jsonify({"code":500,"msg": str(e),"data":None}), 500

@app.route('/downloadRemoteImage', methods=['POST'])
def download_remote_image():
    try:
        data = request.get_json() or {}
        img_url = data.get('url', '').strip()
        if not img_url:
            return jsonify({"code": 400, "msg": "未提供图片URL", "data": None}), 400

        save_dir = Path(BASE_DIR / "videoFile")
        os.makedirs(save_dir, exist_ok=True)

        # 支持 base64 data url
        if img_url.startswith('data:image/'):
            import base64
            header, encoded = img_url.split(',', 1)
            ext = 'png'
            if 'jpeg' in header or 'jpg' in header:
                ext = 'jpg'
            elif 'webp' in header:
                ext = 'webp'
            elif 'gif' in header:
                ext = 'gif'
            data_bytes = base64.b64decode(encoded)
            filename = f"{uuid.uuid1()}_remote.{ext}"
            filepath = Path(BASE_DIR / "videoFile" / filename)
            with open(filepath, 'wb') as f:
                f.write(data_bytes)
            return jsonify({
                "code": 200,
                "msg": "图片转存成功",
                "data": {
                    "filename": filename,
                    "name": filename,
                    "url": f"http://localhost:5409/getFile?filename={filename}",
                    "path": str(filepath),
                    "size": len(data_bytes)
                }
            }), 200

        # 普通 http/https 图片下载
        headers = {
            'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36',
            'Referer': img_url
        }
        res = requests.get(img_url, headers=headers, timeout=15)
        if res.status_code != 200 or not res.content:
            return jsonify({"code": 400, "msg": f"远程图片下载失败: HTTP {res.status_code}", "data": None}), 400

        # 判断后缀
        ext = 'jpg'
        content_type = res.headers.get('Content-Type', '')
        if 'png' in content_type:
            ext = 'png'
        elif 'webp' in content_type:
            ext = 'webp'
        elif 'gif' in content_type:
            ext = 'gif'
        elif '.' in img_url.split('?')[0]:
            possible_ext = img_url.split('?')[0].split('.')[-1].lower()
            if possible_ext in ['jpg', 'jpeg', 'png', 'webp', 'gif']:
                ext = possible_ext

        filename = f"{uuid.uuid1()}_remote.{ext}"
        filepath = Path(BASE_DIR / "videoFile" / filename)
        with open(filepath, 'wb') as f:
            f.write(res.content)

        return jsonify({
            "code": 200,
            "msg": "远程图片抓取成功",
            "data": {
                "filename": filename,
                "name": filename,
                "url": f"http://localhost:5409/getFile?filename={filename}",
                "path": str(filepath),
                "size": len(res.content)
            }
        }), 200
    except Exception as e:
        print(f"下载远程图片出错: {e}")
        return jsonify({"code": 500, "msg": f"下载失败: {e}", "data": None}), 500

@app.route('/getFile', methods=['GET'])
def get_file():
    # 获取 filename 参数
    filename = request.args.get('filename')

    if not filename:
        return jsonify({"code": 400, "msg": "filename is required", "data": None}), 400

    # 防止路径穿越攻击
    if '..' in filename or filename.startswith('/'):
        return jsonify({"code": 400, "msg": "Invalid filename", "data": None}), 400

    # 拼接完整路径
    file_path = str(Path(BASE_DIR / "videoFile"))

    # 返回文件
    return send_from_directory(file_path,filename)


@app.route('/uploadSave', methods=['POST'])
def upload_save():
    if 'file' not in request.files:
        return jsonify({
            "code": 400,
            "data": None,
            "msg": "No file part in the request"
        }), 400

    file = request.files['file']
    if file.filename == '':
        return jsonify({
            "code": 400,
            "data": None,
            "msg": "No selected file"
        }), 400

    # 获取表单中的自定义文件名（可选）
    custom_filename = request.form.get('filename', None)
    if custom_filename:
        filename = custom_filename + "." + file.filename.split('.')[-1]
    else:
        filename = file.filename

    try:
        # 生成 UUID v1
        uuid_v1 = uuid.uuid1()
        print(f"UUID v1: {uuid_v1}")

        # 构造文件名和路径
        final_filename = f"{uuid_v1}_{filename}"
        filepath = Path(BASE_DIR / "videoFile" / f"{uuid_v1}_{filename}")

        # 保存文件
        file.save(filepath)

        with sqlite3.connect(Path(BASE_DIR / "db" / "database.db")) as conn:
            cursor = conn.cursor()
            cursor.execute('''
                                INSERT INTO file_records (filename, filesize, file_path)
            VALUES (?, ?, ?)
                                ''', (filename, round(float(os.path.getsize(filepath)) / (1024 * 1024),2), final_filename))
            conn.commit()
            print("✅ 上传文件已记录")

        return jsonify({
            "code": 200,
            "msg": "File uploaded and saved successfully",
            "data": {
                "filename": filename,
                "filepath": final_filename
            }
        }), 200

    except Exception as e:
        print(f"Upload failed: {e}")
        return jsonify({
            "code": 500,
            "msg": f"upload failed: {e}",
            "data": None
        }), 500

@app.route('/getFiles', methods=['GET'])
def get_all_files():
    try:
        # 使用 with 自动管理数据库连接
        with sqlite3.connect(Path(BASE_DIR / "db" / "database.db")) as conn:
            conn.row_factory = sqlite3.Row  # 允许通过列名访问结果
            cursor = conn.cursor()

            # 查询所有记录
            cursor.execute("SELECT * FROM file_records")
            rows = cursor.fetchall()
            
            # 将结果转为字典列表，并提取UUID
            data = []
            for row in rows:
                row_dict = dict(row)
                # 从 file_path 中提取 UUID (文件名的第一部分，下划线前)
                if row_dict.get('file_path'):
                    file_path_parts = row_dict['file_path'].split('_', 1)  # 只分割第一个下划线
                    if len(file_path_parts) > 0:
                        row_dict['uuid'] = file_path_parts[0]  # UUID 部分
                    else:
                        row_dict['uuid'] = ''
                else:
                    row_dict['uuid'] = ''
                data.append(row_dict)

            return jsonify({
                "code": 200,
                "msg": "success",
                "data": data
            }), 200
    except Exception as e:
        return jsonify({
            "code": 500,
            "msg": str("get file failed!"),
            "data": None
        }), 500


@app.route("/getAccounts", methods=['GET'])
def getAccounts():
    """快速获取所有账号信息，不进行cookie验证"""
    try:
        with sqlite3.connect(Path(BASE_DIR / "db" / "database.db")) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute('''
            SELECT * FROM user_info''')
            rows = cursor.fetchall()
            rows_list = [list(row) for row in rows]

            return jsonify(
                {
                    "code": 200,
                    "msg": None,
                    "data": rows_list
                }), 200
    except Exception as e:
        return jsonify({
            "code": 500,
            "msg": f"获取账号列表失败: {str(e)}",
            "data": None
        }), 500


@app.route("/getValidAccounts",methods=['GET'])
async def getValidAccounts():
    platform_type = request.args.get('type')
    if platform_type is not None:
        try:
            platform_type = int(platform_type)
        except ValueError:
            return jsonify({
                "code": 400,
                "msg": "Invalid platform type",
                "data": None
            }), 400

    with sqlite3.connect(Path(BASE_DIR / "db" / "database.db")) as conn:
        cursor = conn.cursor()
        if platform_type is not None:
            cursor.execute('SELECT * FROM user_info WHERE type = ?', (platform_type,))
        else:
            cursor.execute('SELECT * FROM user_info')
        rows = cursor.fetchall()
        rows_list = [list(row) for row in rows]
        for row in rows_list:
            flag = await check_cookie(row[1],row[2])
            new_status = 1 if flag else 0
            row[4] = new_status
            cursor.execute('''
            UPDATE user_info 
            SET status = ? 
            WHERE id = ?
            ''', (new_status, row[0]))
            conn.commit()
        return jsonify(
                        {
                            "code": 200,
                            "msg": None,
                            "data": rows_list
                        }),200

@app.route('/deleteFile', methods=['GET'])
def delete_file():
    file_id = request.args.get('id')

    if not file_id or not file_id.isdigit():
        return jsonify({
            "code": 400,
            "msg": "Invalid or missing file ID",
            "data": None
        }), 400

    try:
        # 获取数据库连接
        with sqlite3.connect(Path(BASE_DIR / "db" / "database.db")) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()

            # 查询要删除的记录
            cursor.execute("SELECT * FROM file_records WHERE id = ?", (file_id,))
            record = cursor.fetchone()

            if not record:
                return jsonify({
                    "code": 404,
                    "msg": "File not found",
                    "data": None
                }), 404

            record = dict(record)

            # 获取文件路径并删除实际文件
            file_path = Path(BASE_DIR / "videoFile" / record['file_path'])
            if file_path.exists():
                try:
                    file_path.unlink()  # 删除文件
                    print(f"✅ 实际文件已删除: {file_path}")
                except Exception as e:
                    print(f"⚠️ 删除实际文件失败: {e}")
                    # 即使删除文件失败，也要继续删除数据库记录，避免数据不一致
            else:
                print(f"⚠️ 实际文件不存在: {file_path}")

            # 删除数据库记录
            cursor.execute("DELETE FROM file_records WHERE id = ?", (file_id,))
            conn.commit()

        return jsonify({
            "code": 200,
            "msg": "File deleted successfully",
            "data": {
                "id": record['id'],
                "filename": record['filename']
            }
        }), 200

    except Exception as e:
        return jsonify({
            "code": 500,
            "msg": str("delete failed!"),
            "data": None
        }), 500

@app.route('/deleteAccount', methods=['GET'])
def delete_account():
    account_id = request.args.get('id')

    if not account_id or not account_id.isdigit():
        return jsonify({
            "code": 400,
            "msg": "Invalid or missing account ID",
            "data": None
        }), 400

    account_id = int(account_id)

    try:
        # 获取数据库连接
        with sqlite3.connect(Path(BASE_DIR / "db" / "database.db")) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()

            # 查询要删除的记录
            cursor.execute("SELECT * FROM user_info WHERE id = ?", (account_id,))
            record = cursor.fetchone()

            if not record:
                return jsonify({
                    "code": 404,
                    "msg": "account not found",
                    "data": None
                }), 404

            record = dict(record)

            # 删除关联的cookie文件
            if record.get('filePath'):
                cookie_file_path = Path(BASE_DIR / "cookiesFile" / record['filePath'])
                if cookie_file_path.exists():
                    try:
                        cookie_file_path.unlink()
                        print(f"✅ Cookie文件已删除: {cookie_file_path}")
                    except Exception as e:
                        print(f"⚠️ 删除Cookie文件失败: {e}")

            # 删除数据库记录
            cursor.execute("DELETE FROM user_info WHERE id = ?", (account_id,))
            conn.commit()

        return jsonify({
            "code": 200,
            "msg": "account deleted successfully",
            "data": None
        }), 200

    except Exception as e:
        return jsonify({
            "code": 500,
            "msg": f"delete failed: {str(e)}",
            "data": None
        }), 500


# SSE 登录接口
@app.route('/login')
def login():
    # 1 小红书 2 视频号 3 抖音 4 快手 5 百家号 6 B站 7 今日头条 8 搜狐 9 知乎 10 百度贴吧
    type = request.args.get('type')
    # 账号名
    id = request.args.get('id')
    print(f"登录请求: type={type}, id={id}")

    # 模拟一个用于异步通信的队列
    status_queue = Queue()
    active_queues[id] = status_queue

    def on_close():
        print(f"清理队列: {id}")
        del active_queues[id]
    # 启动异步任务线程
    thread = threading.Thread(target=run_async_function, args=(type,id,status_queue), daemon=True)
    thread.start()
    response = Response(sse_stream(status_queue,), mimetype='text/event-stream')
    response.headers['Cache-Control'] = 'no-cache'
    response.headers['X-Accel-Buffering'] = 'no'  # 关键：禁用 Nginx 缓冲
    response.headers['Content-Type'] = 'text/event-stream'
    response.headers['Connection'] = 'keep-alive'
    return response

@app.route('/postVideo', methods=['POST'])
def postVideo():
    # 获取JSON数据
    data = request.get_json()

    if not data:
        return jsonify({"code": 400, "msg": "请求数据不能为空", "data": None}), 400

    # 从JSON数据中提取fileList和accountList
    file_list = data.get('fileList', [])
    account_list = data.get('accountList', [])
    type = data.get('type')
    title = data.get('title')
    tags = data.get('tags')
    category = data.get('category')
    enableTimer = data.get('enableTimer')
    if category == 0:
        category = None
    productLink = data.get('productLink', '')
    productTitle = data.get('productTitle', '')
    thumbnail_path = data.get('thumbnail', '') or data.get('coverImage', '')
    cover_images = data.get('coverImages') or data.get('cover_images') or []
    if isinstance(cover_images, str):
        cover_images = [x.strip() for x in cover_images.split(',') if x.strip()]
    elif not isinstance(cover_images, list):
        cover_images = []
    else:
        cover_images = [str(x).strip() for x in cover_images if str(x).strip()]
    # 兼容：仅传单图时并入 coverImages
    if thumbnail_path and str(thumbnail_path).strip() and str(thumbnail_path).strip() not in cover_images:
        if not cover_images:
            cover_images = [str(thumbnail_path).strip()]
    is_draft = data.get('isDraft', True)  # 默认统一保存为草稿
    ai_generated = data.get('aiGenerated', False)  # 抖音/快手/小红书/视频号 AI 内容声明
    dry_run = data.get('dryRun', False)  # 仅预览不发布：填完表后不点发布
    # B站创作声明 id：-1/1/2/3/4；None 表示不传
    bilibili_creation_statement = data.get('bilibiliCreationStatement', None)
    # 今日头条内容类型：video（默认）| article
    content_type = (data.get('contentType') or 'video').strip().lower()
    article_body = data.get('articleBody') or data.get('body') or ''
    # 今日头条图文作品声明（单选）
    work_statement = data.get('workStatement') or data.get('work_statement') or ''
    work_statements = data.get('workStatements') or data.get('work_statements') or []
    if work_statement and str(work_statement).strip():
        work_statements = [str(work_statement).strip()]
    elif isinstance(work_statements, str):
        work_statements = [work_statements] if work_statements.strip() else []
    elif not isinstance(work_statements, list):
        work_statements = []
    else:
        work_statements = [str(x).strip() for x in work_statements if str(x).strip()]
    # 单选：最多保留第一项
    work_statements = work_statements[:1]

    # 百度贴吧目标贴吧：优先 fid，其次 kw（前后端字段名兼容）
    tieba_fid = data.get('tiebaFid') or data.get('fid')
    tieba_kw = data.get('tiebaKw') or data.get('kw')

    videos_per_day = data.get('videosPerDay')
    daily_times = data.get('dailyTimes')
    start_days = data.get('startDays')

    if type is None or type == "":
        return jsonify({"code": 400, "msg": "平台类型不能为空", "data": None}), 400
    try:
        type = int(type)
    except (TypeError, ValueError):
        return jsonify({"code": 400, "msg": f"不支持的平台类型: {type}", "data": None}), 400

    # 支持图文文章的平台：1=小红书，2=视频号，5=百家号，7=今日头条，8=搜狐号，9=知乎，10=百度贴吧
    ARTICLE_CAPABLE_PLATFORMS = (1, 2, 5, 7, 8, 9, 10)
    # 搜狐号/知乎第一期仅支持图文
    if type == 8:
        content_type = 'article'
    if type == 9:
        content_type = 'article'
    is_article = (type in ARTICLE_CAPABLE_PLATFORMS and content_type == 'article')
    is_xhs_article = (type == 1 and content_type == 'article')
    is_tencent_article = (type == 2 and content_type == 'article')
    is_toutiao_article = (type == 7 and content_type == 'article')
    is_baijiahao_article = (type == 5 and content_type == 'article')
    is_sohu_article = (type == 8)
    is_zhihu_article = (type == 9)

    # 参数校验
    if is_xhs_article:
        if not file_list:
            return jsonify({"code": 400, "msg": "小红书图文模式必须上传至少1张图片", "data": None}), 400
        if len(file_list) > 9:
            return jsonify({"code": 400, "msg": "小红书图文最多支持9张图片", "data": None}), 400
    elif is_tencent_article:
        if not file_list:
            return jsonify({"code": 400, "msg": "视频号图文模式必须上传至少1张图片", "data": None}), 400
        if len(file_list) > 18:
            return jsonify({"code": 400, "msg": "视频号图文最多支持18张图片", "data": None}), 400
    elif not is_article and not file_list:
        return jsonify({"code": 400, "msg": "文件列表不能为空", "data": None}), 400
    if not account_list:
        return jsonify({"code": 400, "msg": "账号列表不能为空", "data": None}), 400
    if type == 10 and not tieba_fid and not tieba_kw:
        return jsonify({"code": 400, "msg": "百度贴吧发帖必须指定 fid（贴吧 ID）或 kw（贴吧名）", "data": None}), 400
    if is_article and not is_xhs_article and not is_tencent_article:
        if not title or not str(title).strip():
            return jsonify({"code": 400, "msg": "文章标题不能为空", "data": None}), 400
        if is_sohu_article:
            title_len = len(str(title).strip())
            if title_len < 5 or title_len > 72:
                return jsonify({"code": 400, "msg": "搜狐号文章标题需 5-72 个字", "data": None}), 400
        if is_zhihu_article:
            title_len = len(str(title).strip())
            if title_len > 100:
                return jsonify({"code": 400, "msg": "知乎文章标题最多 100 个字", "data": None}), 400
        if not article_body or not str(article_body).strip():
            return jsonify({"code": 400, "msg": "文章正文不能为空", "data": None}), 400
        # 百家号图文标题下限 2 字
        if is_baijiahao_article and len(str(title).strip()) < 2:
            return jsonify({"code": 400, "msg": "百家号图文标题至少 2 个字", "data": None}), 400
        if is_baijiahao_article and not str(thumbnail_path or "").strip():
            return jsonify({"code": 400, "msg": "百家号图文展示封面不能为空", "data": None}), 400
    # 小红书（1）、视频号（2）、抖音（3）、快手（4）标题非必填，其它平台仍必填
    elif type not in (1, 2, 3, 4) and not title:
        return jsonify({"code": 400, "msg": "标题不能为空", "data": None}), 400
    if title is None:
        title = ""
    if is_tencent_article and title:
        title = str(title).strip()[:22]
    if is_sohu_article:
        title = str(title).strip()[:72]
    if is_zhihu_article:
        title = str(title).strip()[:100]
    if bilibili_creation_statement is not None and bilibili_creation_statement != "":
        try:
            bilibili_creation_statement = int(bilibili_creation_statement)
        except (TypeError, ValueError):
            return jsonify({"code": 400, "msg": f"非法的创作声明: {bilibili_creation_statement}", "data": None}), 400
    else:
        bilibili_creation_statement = None

    # 打印获取到的数据（仅作为示例）
    print("File List:", file_list)
    print("Account List:", account_list)
    print(
        f"enableTimer={enableTimer}, dryRun={dry_run}, aiGenerated={ai_generated}, "
        f"bilibiliCreationStatement={bilibili_creation_statement}, "
        f"contentType={content_type}, cover={thumbnail_path}, workStatements={work_statements}, isDraft={is_draft}",
        flush=True,
    )

    # 参数校验通过后，在后台线程中执行发布任务，避免阻塞Flask主线程。
    # 前端拿到的 200 只表示“任务已提交”，不代表平台已经发布成功。
    def run_publish_task():
        try:
            print(
                f"🧵 后台发布线程启动: type={type}, title={title}, dryRun={dry_run}, isDraft={is_draft}, contentType={content_type}",
                flush=True,
            )
            match type:
                case 1:
                    if is_xhs_article:
                        post_note_xhs(title, file_list, article_body, tags, account_list, enableTimer, videos_per_day, daily_times,
                                      start_days, dry_run, ai_generated, is_draft)
                    else:
                        post_video_xhs(title, file_list, tags, account_list, category, enableTimer, videos_per_day, daily_times,
                                       start_days, dry_run, ai_generated, is_draft)
                case 2:
                    if is_tencent_article:
                        post_article_tencent(
                            title=title,
                            files=file_list,
                            note=article_body,
                            tags=tags,
                            account_file=account_list,
                            enableTimer=enableTimer,
                            videos_per_day=videos_per_day,
                            daily_times=daily_times,
                            start_days=start_days,
                            dry_run=dry_run,
                            ai_generated=ai_generated,
                            is_draft=is_draft,
                        )
                    else:
                        post_video_tencent(title, file_list, tags, account_list, category, enableTimer, videos_per_day, daily_times,
                                           start_days, is_draft, dry_run, ai_generated)
                case 3:
                    post_video_DouYin(title, file_list, tags, account_list, category, enableTimer, videos_per_day, daily_times,
                              start_days, thumbnail_path, productLink, productTitle, ai_generated, dry_run)
                case 4:
                    post_video_ks(title, file_list, tags, account_list, category, enableTimer, videos_per_day, daily_times,
                              start_days, dry_run, ai_generated)
                case 5:
                    if content_type == 'article':
                        post_article_baijiahao(
                            title,
                            article_body,
                            tags,
                            account_list,
                            enableTimer,
                            videos_per_day,
                            daily_times,
                            start_days,
                            dry_run,
                            thumbnail_path,
                        )
                    else:
                        post_video_baijiahao(title, file_list, tags, account_list, category, enableTimer, videos_per_day, daily_times,
                                  start_days, dry_run)
                case 6:
                    post_video_bilibili(title, file_list, tags, account_list, category, enableTimer, videos_per_day, daily_times,
                              start_days, dry_run, bilibili_creation_statement)
                case 7:
                    if content_type == 'article':
                        post_article_toutiao(
                            title,
                            article_body,
                            tags,
                            account_list,
                            enableTimer,
                            videos_per_day,
                            daily_times,
                            start_days,
                            dry_run,
                            thumbnail_path,
                            work_statements,
                        )
                    else:
                        post_video_toutiao(title, file_list, tags, account_list, category, enableTimer, videos_per_day, daily_times,
                                  start_days, dry_run)
                case 8:
                    post_article_sohu(
                        title,
                        article_body,
                        tags,
                        account_list,
                        enableTimer,
                        videos_per_day,
                        daily_times,
                        start_days,
                        dry_run,
                        thumbnail_path,
                        work_statements[0] if work_statements else (work_statement or None),
                        cover_images,
                    )
                case 9:
                    post_article_zhihu(
                        title,
                        article_body,
                        tags,
                        account_list,
                        enableTimer,
                        videos_per_day,
                        daily_times,
                        start_days,
                        dry_run,
                        thumbnail_path,
                        work_statements[0] if work_statements else (work_statement or None),
                        is_draft=is_draft,
                    )
                case 10:
                    post_article_tieba(
                        title,
                        article_body,
                        tags,
                        account_list,
                        fid=tieba_fid,
                        kw=tieba_kw,
                        enableTimer=enableTimer,
                        videos_per_day=videos_per_day,
                        daily_times=daily_times,
                        start_days=start_days,
                        dry_run=dry_run,
                        cover_path=thumbnail_path,
                        is_draft=is_draft,
                    )
            print(f"✅ 发布任务完成: type={type}, title={title}", flush=True)
            status_result = 'preview' if dry_run else ('draft' if is_draft else 'success')
            update_publish_history(history_id, status_result)
        except Exception as e:
            print(f"❌ 发布任务出错: {str(e)}", flush=True)
            import traceback
            traceback.print_exc()
            update_publish_history(history_id, 'failed', error_msg=str(e))

    # 查出账号用户名并写入发布历史表
    account_names = []
    try:
        with sqlite3.connect(_db_path()) as conn:
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()
            for acc in account_list:
                cur.execute("SELECT userName FROM user_info WHERE filePath = ? OR id = ?", (str(acc), str(acc)))
                r = cur.fetchone()
                account_names.append(r['userName'] if r else str(acc))
    except Exception:
        account_names = [str(x) for x in account_list]

    history_id = add_publish_history(
        platform_type=type,
        account_name=', '.join(account_names) if account_names else '未知账号',
        content_type=content_type,
        title=title,
        content=article_body if is_article else (tags if isinstance(tags, str) else ''),
        tags=tags,
        files=file_list if (file_list and len(file_list) > 0) else (cover_images if (cover_images and len(cover_images) > 0) else ([thumbnail_path] if thumbnail_path else [])),
        status='preview' if dry_run else 'publishing',
        tieba_fid=tieba_fid if type == 10 else None,
        tieba_kw=tieba_kw if type == 10 else None,
    )

    thread = threading.Thread(target=run_publish_task, daemon=True)
    thread.start()

    if type == 6 and dry_run:
        submit_msg = "B站仅预览模式不会实际上传（无浏览器可预览）"
    elif type == 6:
        submit_msg = "B站无官方草稿箱机制，已直接提交上传并在后台执行"
    elif type == 4 and dry_run:
        submit_msg = "快手仅预览模式已提交：仅预览不发布"
    elif type == 4:
        submit_msg = "快手无官方草稿箱机制，已直接提交发布并在后台执行"
    elif dry_run:
        submit_msg = "发布任务已提交：仅预览不发布模式"
    elif is_draft:
        submit_msg = "发布任务已提交：已保存至官方草稿箱，请前往对应平台或手机 APP 确认后发布"
    else:
        submit_msg = "发布任务已提交，正在后台执行"

    return jsonify(
        {
            "code": 200,
            "msg": submit_msg,
            "data": None
        }), 200


@app.route('/updateUserinfo', methods=['POST'])
def updateUserinfo():
    # 获取JSON数据
    data = request.get_json()

    # 从JSON数据中提取 type 和 userName
    user_id = data.get('id')
    type = data.get('type')
    userName = data.get('userName')
    try:
        # 获取数据库连接
        with sqlite3.connect(Path(BASE_DIR / "db" / "database.db")) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()

            # 更新数据库记录
            cursor.execute('''
                           UPDATE user_info
                           SET type     = ?,
                               userName = ?
                           WHERE id = ?;
                           ''', (type, userName, user_id))
            conn.commit()

        return jsonify({
            "code": 200,
            "msg": "account update successfully",
            "data": None
        }), 200

    except Exception as e:
        return jsonify({
            "code": 500,
            "msg": str("update failed!"),
            "data": None
        }), 500

@app.route('/postVideoBatch', methods=['POST'])
def postVideoBatch():
    data_list = request.get_json()

    if not isinstance(data_list, list):
        return jsonify({"code": 400, "msg": "Expected a JSON array", "data": None}), 400
    for data in data_list:
        # 从JSON数据中提取fileList和accountList
        file_list = data.get('fileList', [])
        account_list = data.get('accountList', [])
        type = data.get('type')
        title = data.get('title')
        tags = data.get('tags')
        category = data.get('category')
        enableTimer = data.get('enableTimer')
        if category == 0:
            category = None
        productLink = data.get('productLink', '')
        productTitle = data.get('productTitle', '')
        is_draft = data.get('isDraft', False)
        ai_generated = data.get('aiGenerated', False)
        dry_run = data.get('dryRun', False)
        thumbnail_path = data.get('thumbnail', '') or data.get('coverImage', '')
        cover_images = data.get('coverImages') or data.get('cover_images') or []
        if isinstance(cover_images, str):
            cover_images = [x.strip() for x in cover_images.split(',') if x.strip()]
        elif not isinstance(cover_images, list):
            cover_images = []
        else:
            cover_images = [str(x).strip() for x in cover_images if str(x).strip()]
        if thumbnail_path and str(thumbnail_path).strip() and str(thumbnail_path).strip() not in cover_images:
            if not cover_images:
                cover_images = [str(thumbnail_path).strip()]
        content_type = (data.get('contentType') or 'video').strip().lower()
        article_body = data.get('articleBody') or data.get('body') or ''
        work_statement = data.get('workStatement') or data.get('work_statement') or ''
        work_statements = data.get('workStatements') or data.get('work_statements') or []
        if work_statement and str(work_statement).strip():
            work_statements = [str(work_statement).strip()]
        elif isinstance(work_statements, str):
            work_statements = [work_statements] if work_statements.strip() else []
        elif not isinstance(work_statements, list):
            work_statements = []
        else:
            work_statements = [str(x).strip() for x in work_statements if str(x).strip()]
        work_statements = work_statements[:1]
        bilibili_creation_statement = data.get('bilibiliCreationStatement', None)
        if bilibili_creation_statement is not None and bilibili_creation_statement != "":
            try:
                bilibili_creation_statement = int(bilibili_creation_statement)
            except (TypeError, ValueError):
                bilibili_creation_statement = None
        else:
            bilibili_creation_statement = None

        videos_per_day = data.get('videosPerDay')
        daily_times = data.get('dailyTimes')
        start_days = data.get('startDays')
        # 搜狐号/知乎第一期仅支持图文
        if type == 8:
            content_type = 'article'
            title_text = str(title or "").strip()
            if len(title_text) < 5 or len(title_text) > 72:
                return jsonify({"code": 400, "msg": "搜狐号文章标题需 5-72 个字", "data": None}), 400
            title = title_text[:72]
        if type == 9:
            content_type = 'article'
            title_text = str(title or "").strip()
            if len(title_text) > 100:
                return jsonify({"code": 400, "msg": "知乎文章标题最多 100 个字", "data": None}), 400
            title = title_text[:100]
        is_baijiahao_article = (type == 5 and content_type == 'article')
        if is_baijiahao_article and not str(thumbnail_path or "").strip():
            return jsonify({"code": 400, "msg": "百家号图文展示封面不能为空", "data": None}), 400
        # 打印获取到的数据（仅作为示例）
        print("File List:", file_list)
        print("Account List:", account_list)
        match type:
            case 1:
                if content_type == 'article':
                    post_note_xhs(title, file_list, article_body, tags, account_list, enableTimer, videos_per_day, daily_times,
                                  start_days, dry_run, ai_generated)
                else:
                    post_video_xhs(title, file_list, tags, account_list, category, enableTimer, videos_per_day, daily_times,
                                   start_days, dry_run, ai_generated)
            case 2:
                post_video_tencent(title, file_list, tags, account_list, category, enableTimer, videos_per_day, daily_times,
                                   start_days, is_draft, dry_run, ai_generated)
            case 3:
                post_video_DouYin(title, file_list, tags, account_list, category, enableTimer, videos_per_day, daily_times,
                          start_days, thumbnail_path, productLink, productTitle, ai_generated, dry_run)
            case 4:
                post_video_ks(title, file_list, tags, account_list, category, enableTimer, videos_per_day, daily_times,
                          start_days, dry_run)
            case 5:
                if content_type == 'article':
                    post_article_baijiahao(
                        title,
                        article_body,
                        tags,
                        account_list,
                        enableTimer,
                        videos_per_day,
                        daily_times,
                        start_days,
                        dry_run,
                        thumbnail_path,
                    )
                else:
                    post_video_baijiahao(title, file_list, tags, account_list, category, enableTimer, videos_per_day, daily_times,
                              start_days, dry_run)
            case 6:
                post_video_bilibili(title, file_list, tags, account_list, category, enableTimer, videos_per_day, daily_times,
                          start_days, dry_run, bilibili_creation_statement)
            case 7:
                if content_type == 'article':
                    post_article_toutiao(
                        title,
                        article_body,
                        tags,
                        account_list,
                        enableTimer,
                        videos_per_day,
                        daily_times,
                        start_days,
                        dry_run,
                        thumbnail_path,
                        work_statements,
                    )
                else:
                    post_video_toutiao(title, file_list, tags, account_list, category, enableTimer, videos_per_day, daily_times,
                              start_days, dry_run)
            case 8:
                post_article_sohu(
                    title,
                    article_body,
                    tags,
                    account_list,
                    enableTimer,
                    videos_per_day,
                    daily_times,
                    start_days,
                    dry_run,
                    thumbnail_path,
                    work_statements[0] if work_statements else (work_statement or None),
                    cover_images,
                )
            case 9:
                post_article_zhihu(
                    title,
                    article_body,
                    tags,
                    account_list,
                    enableTimer,
                    videos_per_day,
                    daily_times,
                    start_days,
                    dry_run,
                    thumbnail_path,
                    work_statements[0] if work_statements else (work_statement or None),
                    is_draft=is_draft,
                )
            case 10:
                tieba_fid = data.get('tiebaFid') or data.get('fid')
                tieba_kw = data.get('tiebaKw') or data.get('kw')
                post_article_tieba(
                    title,
                    article_body,
                    tags,
                    account_list,
                    fid=tieba_fid,
                    kw=tieba_kw,
                    enableTimer=enableTimer,
                    videos_per_day=videos_per_day,
                    daily_times=daily_times,
                    start_days=start_days,
                    dry_run=dry_run,
                    cover_path=thumbnail_path,
                    is_draft=is_draft,
                )
    # 返回响应给客户端
    return jsonify(
        {
            "code": 200,
            "msg": None,
            "data": None
        }), 200

# Cookie文件上传API
@app.route('/uploadCookie', methods=['POST'])
def upload_cookie():
    try:
        if 'file' not in request.files:
            return jsonify({
                "code": 400,
                "msg": "没有找到Cookie文件",
                "data": None
            }), 400

        file = request.files['file']
        if file.filename == '':
            return jsonify({
                "code": 400,
                "msg": "Cookie文件名不能为空",
                "data": None
            }), 400

        if not file.filename.endswith('.json'):
            return jsonify({
                "code": 400,
                "msg": "Cookie文件必须是JSON格式",
                "data": None
            }), 400

        # 获取账号信息
        account_id = request.form.get('id')
        platform = request.form.get('platform')

        if not account_id or not platform:
            return jsonify({
                "code": 400,
                "msg": "缺少账号ID或平台信息",
                "data": None
            }), 400

        # 从数据库获取账号的文件路径
        with sqlite3.connect(Path(BASE_DIR / "db" / "database.db")) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute('SELECT filePath FROM user_info WHERE id = ?', (account_id,))
            result = cursor.fetchone()

        if not result:
            return jsonify({
                "code": 500,
                "msg": "账号不存在",
                "data": None
            }), 404

        # 保存上传的Cookie文件到对应路径
        cookie_file_path = Path(BASE_DIR / "cookiesFile" / result['filePath'])
        cookie_file_path.parent.mkdir(parents=True, exist_ok=True)

        file.save(str(cookie_file_path))

        # 更新数据库中的账号信息（可选，比如更新更新时间）
        # 这里可以根据需要添加额外的处理逻辑

        return jsonify({
            "code": 200,
            "msg": "Cookie文件上传成功",
            "data": None
        }), 200

    except Exception as e:
        print(f"上传Cookie文件时出错: {str(e)}")
        return jsonify({
            "code": 500,
            "msg": f"上传Cookie文件失败: {str(e)}",
            "data": None
        }), 500


# Cookie文件下载API
@app.route('/downloadCookie', methods=['GET'])
def download_cookie():
    try:
        file_path = request.args.get('filePath')
        if not file_path:
            return jsonify({
                "code": 500,
                "msg": "缺少文件路径参数",
                "data": None
            }), 400

        # 验证文件路径的安全性，防止路径遍历攻击
        cookie_file_path = Path(BASE_DIR / "cookiesFile" / file_path).resolve()
        base_path = Path(BASE_DIR / "cookiesFile").resolve()

        if not cookie_file_path.is_relative_to(base_path):
            return jsonify({
                "code": 500,
                "msg": "非法文件路径",
                "data": None
            }), 400

        if not cookie_file_path.exists():
            return jsonify({
                "code": 500,
                "msg": "Cookie文件不存在",
                "data": None
            }), 404

        # 返回文件
        return send_from_directory(
            directory=str(cookie_file_path.parent),
            path=cookie_file_path.name,
            as_attachment=True
        )

    except Exception as e:
        print(f"下载Cookie文件时出错: {str(e)}")
        return jsonify({
            "code": 500,
            "msg": f"下载Cookie文件失败: {str(e)}",
            "data": None
        }), 500


@app.route("/proxyImage", methods=["GET"])
def proxy_image():
    """代理外链封面图，规避 B 站等 CDN 防盗链。"""
    from urllib.parse import urlparse

    import requests as http_requests

    raw_url = (request.args.get("url") or "").strip()
    if not raw_url:
        return jsonify({"code": 400, "msg": "url 不能为空", "data": None}), 400
    try:
        parsed = urlparse(raw_url)
    except Exception:
        return jsonify({"code": 400, "msg": "url 无效", "data": None}), 400
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        return jsonify({"code": 400, "msg": "仅支持 http/https 图片地址", "data": None}), 400
    host = parsed.hostname
    if not host:
        return jsonify({"code": 400, "msg": "url 无效", "data": None}), 400
    host = host.lower()
    allowed_suffixes = (".hdslb.com",)
    allowed_exact = {"hdslb.com"}
    if not (host in allowed_exact or any(host.endswith(suf) for suf in allowed_suffixes)):
        return jsonify({"code": 403, "msg": "该图片域名不允许代理", "data": None}), 403

    try:
        upstream = http_requests.get(
            raw_url,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/122.0.0.0 Safari/537.36"
                ),
                "Referer": "https://www.bilibili.com/",
            },
            timeout=20,
            stream=True,
        )
    except Exception as e:
        return jsonify({"code": 502, "msg": f"拉取图片失败: {e}", "data": None}), 502
    if upstream.status_code != 200:
        return jsonify({"code": 502, "msg": f"上游返回 {upstream.status_code}", "data": None}), 502

    content_type = (upstream.headers.get("Content-Type") or "image/jpeg").split(";")[0].strip()
    if not content_type.startswith("image/"):
        return jsonify({"code": 502, "msg": "上游不是图片", "data": None}), 502

    # cap ~5MB
    chunks = []
    total = 0
    for chunk in upstream.iter_content(chunk_size=64 * 1024):
        if not chunk:
            continue
        total += len(chunk)
        if total > 5 * 1024 * 1024:
            return jsonify({"code": 502, "msg": "图片过大", "data": None}), 502
        chunks.append(chunk)
    body = b"".join(chunks)
    return Response(body, status=200, mimetype=content_type, headers={
        "Cache-Control": "public, max-age=86400",
    })


@app.route("/getContentStats", methods=["GET"])
def get_content_stats():
    account_id = request.args.get("accountId", type=int)
    platform = (request.args.get("platform") or "douyin").strip().lower()
    if not account_id:
        return jsonify({"code": 400, "msg": "accountId 不能为空", "data": None}), 400
    if platform not in SUPPORTED_STATS_PLATFORMS:
        return jsonify({"code": 400, "msg": "仅支持 platform=douyin|kuaishou|xiaohongshu|bilibili", "data": None}), 400
    try:
        with sqlite3.connect(_db_path()) as conn:
            payload = _load_stats_payload(conn, platform, account_id)
        return jsonify({"code": 200, "msg": None, "data": payload}), 200
    except Exception as e:
        return jsonify({"code": 500, "msg": f"读取失败: {e}", "data": None}), 500


@app.route("/syncContentStats", methods=["POST"])
def sync_content_stats():
    body = request.get_json(silent=True) or {}
    account_id = body.get("accountId")
    platform = str(body.get("platform") or "douyin").strip().lower()
    try:
        limit = int(body.get("limit") or 5)
    except (TypeError, ValueError):
        return jsonify({"code": 400, "msg": "limit 无效", "data": None}), 400
    try:
        account_id = int(account_id)
    except (TypeError, ValueError):
        return jsonify({"code": 400, "msg": "accountId 无效", "data": None}), 400
    meta = SUPPORTED_STATS_PLATFORMS.get(platform)
    if not meta:
        return jsonify({"code": 400, "msg": "仅支持 platform=douyin|kuaishou|xiaohongshu|bilibili", "data": None}), 400
    if limit < 1 or limit > 20:
        return jsonify({"code": 400, "msg": "limit 需在 1-20", "data": None}), 400

    try:
        with sqlite3.connect(_db_path()) as conn:
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()
            cur.execute("SELECT id, type, filePath FROM user_info WHERE id = ?", (account_id,))
            row = cur.fetchone()
            if not row:
                return jsonify({"code": 404, "msg": "账号不存在", "data": None}), 404
            if int(row["type"]) != meta["type"]:
                return jsonify({"code": 400, "msg": f"该账号不是{meta['label']}账号", "data": None}), 400
            cookie_path = Path(BASE_DIR / "cookiesFile" / row["filePath"])
    except Exception as e:
        return jsonify({"code": 500, "msg": f"查询账号失败: {e}", "data": None}), 500

    try:
        if platform == "douyin":
            items = sync_douyin_content_stats(cookie_path, limit=limit)
        elif platform == "kuaishou":
            items = sync_kuaishou_content_stats(cookie_path, limit=limit)
        elif platform == "xiaohongshu":
            items = sync_xiaohongshu_content_stats(cookie_path, limit=limit)
        else:
            items = sync_bilibili_content_stats(cookie_path, limit=limit)
    except (
        DouyinStatsSyncError,
        KuaishouStatsSyncError,
        XiaohongshuStatsSyncError,
        BilibiliStatsSyncError,
    ) as e:
        return jsonify({"code": e.code, "msg": e.message, "data": None}), e.code
    except Exception as e:
        return jsonify({"code": 500, "msg": f"同步失败: {e}", "data": None}), 500

    synced_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    try:
        with sqlite3.connect(_db_path()) as conn:
            replace_account_stats(
                conn,
                platform=platform,
                account_id=account_id,
                items=items,
                synced_at=synced_at,
            )
            payload = _load_stats_payload(conn, platform, account_id)
        return jsonify({"code": 200, "msg": "同步成功", "data": payload}), 200
    except Exception as e:
        return jsonify({"code": 500, "msg": f"写入失败: {e}", "data": None}), 500


@app.route("/pushContentStatsToDingTalk", methods=["POST"])
def push_content_stats_to_dingtalk():
    body = request.get_json(silent=True) or {}
    webhook = _dingtalk_webhook_url()
    if not webhook:
        return jsonify({"code": 400, "msg": "未配置 DINGTALK_WEBHOOK_URL", "data": None}), 400

    raw_accounts = body.get("accounts")
    targets = []
    if isinstance(raw_accounts, list) and raw_accounts:
        for entry in raw_accounts:
            if not isinstance(entry, dict):
                return jsonify({"code": 400, "msg": "accounts 项无效", "data": None}), 400
            platform = str(entry.get("platform") or "").strip().lower()
            try:
                account_id = int(entry.get("accountId"))
            except (TypeError, ValueError):
                return jsonify({"code": 400, "msg": "accountId 无效", "data": None}), 400
            if platform not in SUPPORTED_STATS_PLATFORMS:
                return jsonify(
                    {
                        "code": 400,
                        "msg": "仅支持 platform=douyin|kuaishou|xiaohongshu|bilibili",
                        "data": None,
                    }
                ), 400
            targets.append({"accountId": account_id, "platform": platform})
    else:
        platform = str(body.get("platform") or "douyin").strip().lower()
        try:
            account_id = int(body.get("accountId"))
        except (TypeError, ValueError):
            return jsonify({"code": 400, "msg": "accountId 无效", "data": None}), 400
        if platform not in SUPPORTED_STATS_PLATFORMS:
            return jsonify(
                {
                    "code": 400,
                    "msg": "仅支持 platform=douyin|kuaishou|xiaohongshu|bilibili",
                    "data": None,
                }
            ), 400
        targets.append({"accountId": account_id, "platform": platform})

    # de-dupe while preserving order
    seen = set()
    unique_targets = []
    for t in targets:
        key = (t["platform"], t["accountId"])
        if key in seen:
            continue
        seen.add(key)
        unique_targets.append(t)
    targets = unique_targets

    if not targets:
        return jsonify({"code": 400, "msg": "请选择账号", "data": None}), 400
    if len(targets) > 20:
        return jsonify({"code": 400, "msg": "一次最多推送 20 个账号", "data": None}), 400

    sections = []
    skipped = []
    try:
        with sqlite3.connect(_db_path()) as conn:
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()
            for t in targets:
                platform = t["platform"]
                account_id = t["accountId"]
                meta = SUPPORTED_STATS_PLATFORMS[platform]
                cur.execute(
                    "SELECT id, type, userName FROM user_info WHERE id = ?",
                    (account_id,),
                )
                row = cur.fetchone()
                if not row:
                    skipped.append({"accountId": account_id, "platform": platform, "reason": "账号不存在"})
                    continue
                if int(row["type"]) != meta["type"]:
                    skipped.append(
                        {
                            "accountId": account_id,
                            "platform": platform,
                            "reason": f"不是{meta['label']}账号",
                        }
                    )
                    continue
                payload = _load_stats_payload(conn, platform, account_id)
                items = payload.get("items") or []
                if not items:
                    skipped.append(
                        {
                            "accountId": account_id,
                            "platform": platform,
                            "accountName": row["userName"] or str(account_id),
                            "reason": "尚未同步",
                        }
                    )
                    continue
                sections.append(
                    {
                        "platform_label": meta["label"],
                        "account_name": row["userName"] or str(account_id),
                        "last_synced_at": payload.get("lastSyncedAt"),
                        "items": items,
                        "accountId": account_id,
                        "platform": platform,
                    }
                )
    except Exception as e:
        return jsonify({"code": 500, "msg": f"读取失败: {e}", "data": None}), 500

    if not sections:
        return jsonify(
            {
                "code": 400,
                "msg": "所选账号均无已同步数据，请先同步",
                "data": {"skipped": skipped},
            }
        ), 400

    try:
        title, text = format_multi_account_stats_markdown(
            sections=sections,
            rehost_covers=_dingtalk_rehost_covers(),
            include_covers=_dingtalk_push_covers(),
        )
        send_markdown(
            webhook_url=webhook,
            secret=_dingtalk_secret(),
            title=title,
            text=text,
        )
    except DingTalkPushError as e:
        return jsonify({"code": e.code, "msg": e.message, "data": None}), e.code
    except Exception as e:
        return jsonify({"code": 502, "msg": f"推送失败: {e}", "data": None}), 502

    item_count = sum(len(s["items"]) for s in sections)
    return jsonify(
        {
            "code": 200,
            "msg": "推送成功",
            "data": {
                "accountCount": len(sections),
                "itemCount": item_count,
                "skipped": skipped,
            },
        }
    ), 200


# 包装函数：在线程中运行异步函数
def run_async_function(type,id,status_queue):
    print(f"🧵 登录线程启动: type={type}, id={id}", flush=True)
    try:
        match str(type):
            case '1':
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                loop.run_until_complete(xiaohongshu_cookie_gen(id, status_queue))
                loop.close()
            case '2':
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                loop.run_until_complete(get_tencent_cookie(id,status_queue))
                loop.close()
            case '3':
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                loop.run_until_complete(douyin_cookie_gen(id,status_queue))
                loop.close()
            case '4':
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                loop.run_until_complete(get_ks_cookie(id,status_queue))
                loop.close()
            case '5':
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                loop.run_until_complete(baijiahao_cookie_gen(id, status_queue))
                loop.close()
            case '6':
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                loop.run_until_complete(bilibili_cookie_gen(id, status_queue))
                loop.close()
            case '7':
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                loop.run_until_complete(toutiao_cookie_gen(id, status_queue))
                loop.close()
            case '8':
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                loop.run_until_complete(sohu_cookie_gen(id, status_queue))
                loop.close()
            case '9':
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                loop.run_until_complete(zhihu_cookie_gen(id, status_queue))
                loop.close()
            case '10':
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                loop.run_until_complete(tieba_cookie_gen(id, status_queue))
                loop.close()
            case _:
                print(f"❌ 不支持的登录平台类型: {type}", flush=True)
                status_queue.put("500")
    except Exception as e:
        print(f"❌ 登录线程异常 type={type}, id={id}: {e}", flush=True)
        import traceback
        traceback.print_exc()
        try:
            status_queue.put("500")
        except Exception:
            pass

# SSE 流生成器函数
def sse_stream(status_queue):
    while True:
        if not status_queue.empty():
            msg = status_queue.get()
            yield f"data: {msg}\n\n"
        else:
            # 避免 CPU 占满
            time.sleep(0.1)


# =====================================================================
# 多产品知识库泛化 API（Step 1 & Step 2：产品与知识库 CRUD）
# =====================================================================

def _dict_from_row(row, cursor):
    """将 sqlite Row 转成 dict。"""
    if row is None:
        return None
    return {col[0]: row[idx] for idx, col in enumerate(cursor.description)}


@app.route('/api/products', methods=['GET'])
def list_products():
    """列出所有产品。"""
    try:
        with sqlite3.connect(_db_path()) as conn:
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()
            cur.execute("SELECT * FROM products ORDER BY id DESC")
            rows = cur.fetchall()
            data = [dict(r) for r in rows]
        return jsonify({"code": 200, "msg": "success", "data": data})
    except Exception as e:
        print(f"列出产品失败: {e}", flush=True)
        return jsonify({"code": 500, "msg": f"列出产品失败: {e}", "data": None}), 500


@app.route('/api/products', methods=['POST'])
def create_product():
    """创建产品。"""
    try:
        data = request.get_json() or {}
        name = (data.get('name') or '').strip()
        if not name:
            return jsonify({"code": 400, "msg": "产品名称不能为空", "data": None}), 400
        description = data.get('description') or ''
        logo_path = data.get('logo_path') or ''
        brand_color = data.get('brand_color') or ''
        with sqlite3.connect(_db_path()) as conn:
            cur = conn.cursor()
            cur.execute(
                "INSERT INTO products (name, description, logo_path, brand_color, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, datetime('now', 'localtime'), datetime('now', 'localtime'))",
                (name, description, logo_path, brand_color),
            )
            conn.commit()
            pid = cur.lastrowid
        return jsonify({"code": 200, "msg": "产品创建成功", "data": {"id": pid}})
    except Exception as e:
        print(f"创建产品失败: {e}", flush=True)
        return jsonify({"code": 500, "msg": f"创建产品失败: {e}", "data": None}), 500


@app.route('/api/products/<int:product_id>', methods=['GET'])
def get_product(product_id):
    """获取产品详情。"""
    try:
        with sqlite3.connect(_db_path()) as conn:
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()
            cur.execute("SELECT * FROM products WHERE id = ?", (product_id,))
            row = cur.fetchone()
        if not row:
            return jsonify({"code": 404, "msg": "产品不存在", "data": None}), 404
        return jsonify({"code": 200, "msg": "success", "data": dict(row)})
    except Exception as e:
        print(f"获取产品失败: {e}", flush=True)
        return jsonify({"code": 500, "msg": f"获取产品失败: {e}", "data": None}), 500


@app.route('/api/products/<int:product_id>', methods=['PUT'])
def update_product(product_id):
    """更新产品。"""
    try:
        data = request.get_json() or {}
        fields = []
        values = []
        for key in ('name', 'description', 'logo_path', 'brand_color'):
            if key in data:
                fields.append(f"{key} = ?")
                values.append(data.get(key) or '')
        if not fields:
            return jsonify({"code": 400, "msg": "没有可更新的字段", "data": None}), 400
        fields.append("updated_at = datetime('now', 'localtime')")
        values.append(product_id)
        with sqlite3.connect(_db_path()) as conn:
            cur = conn.cursor()
            cur.execute(f"UPDATE products SET {', '.join(fields)} WHERE id = ?", values)
            if cur.rowcount == 0:
                return jsonify({"code": 404, "msg": "产品不存在", "data": None}), 404
            conn.commit()
        return jsonify({"code": 200, "msg": "产品更新成功", "data": None})
    except Exception as e:
        print(f"更新产品失败: {e}", flush=True)
        return jsonify({"code": 500, "msg": f"更新产品失败: {e}", "data": None}), 500


@app.route('/api/products/<int:product_id>', methods=['DELETE'])
def delete_product(product_id):
    """删除产品及其下的知识库条目。"""
    try:
        with sqlite3.connect(_db_path()) as conn:
            cur = conn.cursor()
            cur.execute("DELETE FROM knowledge_base WHERE product_id = ?", (product_id,))
            cur.execute("DELETE FROM products WHERE id = ?", (product_id,))
            if cur.rowcount == 0:
                return jsonify({"code": 404, "msg": "产品不存在", "data": None}), 404
            conn.commit()
        return jsonify({"code": 200, "msg": "产品已删除", "data": None})
    except Exception as e:
        print(f"删除产品失败: {e}", flush=True)
        return jsonify({"code": 500, "msg": f"删除产品失败: {e}", "data": None}), 500


@app.route('/api/knowledge-base', methods=['GET'])
def list_knowledge_base():
    """列出知识库条目，支持 ?product_id= 过滤。"""
    try:
        product_id = request.args.get('product_id')
        with sqlite3.connect(_db_path()) as conn:
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()
            if product_id and str(product_id).isdigit():
                cur.execute(
                    "SELECT * FROM knowledge_base WHERE product_id = ? ORDER BY id DESC",
                    (int(product_id),),
                )
            else:
                cur.execute("SELECT * FROM knowledge_base ORDER BY id DESC")
            rows = cur.fetchall()
            data = [dict(r) for r in rows]
        return jsonify({"code": 200, "msg": "success", "data": data})
    except Exception as e:
        print(f"列出知识库失败: {e}", flush=True)
        return jsonify({"code": 500, "msg": f"列出知识库失败: {e}", "data": None}), 500


@app.route('/api/knowledge-base', methods=['POST'])
def create_knowledge_base():
    """创建知识库条目。"""
    try:
        data = request.get_json() or {}
        product_id = data.get('product_id')
        if not product_id or not str(product_id).isdigit():
            return jsonify({"code": 400, "msg": "product_id 不能为空", "data": None}), 400
        title = (data.get('title') or '').strip()
        if not title:
            return jsonify({"code": 400, "msg": "条目标题不能为空", "data": None}), 400
        content = data.get('content') or ''
        pain_points = data.get('pain_points') or ''
        solutions = data.get('solutions') or ''
        tags = data.get('tags') or ''
        file_paths = data.get('file_paths') or ''
        with sqlite3.connect(_db_path()) as conn:
            cur = conn.cursor()
            cur.execute(
                "INSERT INTO knowledge_base "
                "(product_id, title, content, pain_points, solutions, tags, file_paths, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now', 'localtime'), datetime('now', 'localtime'))",
                (int(product_id), title, content, pain_points, solutions, tags, file_paths),
            )
            conn.commit()
            kb_id = cur.lastrowid
        return jsonify({"code": 200, "msg": "知识库条目创建成功", "data": {"id": kb_id}})
    except Exception as e:
        print(f"创建知识库条目失败: {e}", flush=True)
        return jsonify({"code": 500, "msg": f"创建知识库条目失败: {e}", "data": None}), 500


@app.route('/api/knowledge-base/<int:kb_id>', methods=['GET'])
def get_knowledge_base(kb_id):
    """获取知识库条目详情。"""
    try:
        with sqlite3.connect(_db_path()) as conn:
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()
            cur.execute("SELECT * FROM knowledge_base WHERE id = ?", (kb_id,))
            row = cur.fetchone()
        if not row:
            return jsonify({"code": 404, "msg": "知识库条目不存在", "data": None}), 404
        return jsonify({"code": 200, "msg": "success", "data": dict(row)})
    except Exception as e:
        print(f"获取知识库条目失败: {e}", flush=True)
        return jsonify({"code": 500, "msg": f"获取知识库条目失败: {e}", "data": None}), 500


@app.route('/api/knowledge-base/<int:kb_id>', methods=['PUT'])
def update_knowledge_base(kb_id):
    """更新知识库条目。"""
    try:
        data = request.get_json() or {}
        fields = []
        values = []
        for key in ('title', 'content', 'pain_points', 'solutions', 'tags', 'file_paths'):
            if key in data:
                fields.append(f"{key} = ?")
                values.append(data.get(key) or '')
        if 'product_id' in data:
            fields.append("product_id = ?")
            values.append(int(data.get('product_id')))
        if not fields:
            return jsonify({"code": 400, "msg": "没有可更新的字段", "data": None}), 400
        fields.append("updated_at = datetime('now', 'localtime')")
        values.append(kb_id)
        with sqlite3.connect(_db_path()) as conn:
            cur = conn.cursor()
            cur.execute(f"UPDATE knowledge_base SET {', '.join(fields)} WHERE id = ?", values)
            if cur.rowcount == 0:
                return jsonify({"code": 404, "msg": "知识库条目不存在", "data": None}), 404
            conn.commit()
        return jsonify({"code": 200, "msg": "知识库条目更新成功", "data": None})
    except Exception as e:
        print(f"更新知识库条目失败: {e}", flush=True)
        return jsonify({"code": 500, "msg": f"更新知识库条目失败: {e}", "data": None}), 500


@app.route('/api/knowledge-base/<int:kb_id>', methods=['DELETE'])
def delete_knowledge_base(kb_id):
    """删除知识库条目。"""
    try:
        with sqlite3.connect(_db_path()) as conn:
            cur = conn.cursor()
            cur.execute("DELETE FROM knowledge_base WHERE id = ?", (kb_id,))
            if cur.rowcount == 0:
                return jsonify({"code": 404, "msg": "知识库条目不存在", "data": None}), 404
            conn.commit()
        return jsonify({"code": 200, "msg": "知识库条目已删除", "data": None})
    except Exception as e:
        print(f"删除知识库条目失败: {e}", flush=True)
        return jsonify({"code": 500, "msg": f"删除知识库条目失败: {e}", "data": None}), 500


# =====================================================================
# Step 3：知识库内容生成 + 合规自检（模板优先，可选 LLM 增强）
# =====================================================================

# 合规红线词（与 AGENTS.md 一致，零容忍）
_FORBIDDEN_WORDS = [
    "15天免费试点", "免费试跑", "免费领", "私信领资料", "加微信",
    "扫码体验", "点击链接", "www.ricent.com",
    "国家级", "第一", "唯一", "100%零失误", "全网最强",
]


def _compliance_check(text: str) -> dict:
    """检查文本是否包含违规词，返回 {passed: bool, hits: [words]}。"""
    hits = [w for w in _FORBIDDEN_WORDS if w in text]
    return {"passed": len(hits) == 0, "hits": hits}


def _build_kb_prompt(kb: dict) -> dict:
    """从知识库条目提取用于生成文案的关键字段。"""
    return {
        "title": kb.get("title") or "",
        "content": kb.get("content") or "",
        "pain_points": kb.get("pain_points") or "",
        "solutions": kb.get("solutions") or "",
        "tags": kb.get("tags") or "",
    }


def _gen_xhs_motherboard(kb: dict) -> dict:
    """母本 A：小红书短图文（300~500 字，标题 ≤20 字，5 个话题标签）。"""
    product_hint = kb.get("title") or "该产品"
    title = f"{product_hint}现场避坑指南"
    title = title[:20]
    body = (
        f"🔍 为什么会翻车\n"
        f"1. {kb.get('pain_points') or '现场问题多，找不到根因'}。\n"
        f"2. 传统方式对比低效，耽误施工进度。\n"
        f"3. 资料交接不清晰，返工率高。\n\n"
        f"💡 {product_hint} 怎么破局\n"
        f"✅ {kb.get('solutions') or '云端协同，版本自动对比'}。\n"
        f"✅ 实时同步，现场即看即用。\n"
        f"✅ 一键生成报告，交接更清晰。\n\n"
        f"📌 现场避坑建议\n"
        f"入场前先同步最新图纸版本，重点节点拍照存档，问题现场记录。\n\n"
        f"#{kb.get('tags') or '工地' } #看图 #效率 #避坑 #蓝枢"
    )
    return {"title": title, "body": body, "hashtags": 5}


def _gen_long_motherboard(kb: dict) -> dict:
    """母本 B：四平台精炼长文（800~1100 字，含 2 个 H2 小标题，2 张 16:9 内文图占位）。"""
    product_hint = kb.get("title") or "该产品"
    title = f"{product_hint} 现场痛点与解决方案"
    title = title[:35]
    body = (
        f"## 一、一线痛点场景\n\n"
        f"工程现场最头疼的莫过于图纸版本混乱。{kb.get('pain_points') or '资料员每天花大量时间手工对比图纸，却仍容易漏掉关键变更。'}\n\n"
        f"## 二、根因拆解\n\n"
        f"1. 图纸分散在多个渠道，没有统一版本入口。\n"
        f"2. 变更通知不及时，现场按旧图施工返工。\n"
        f"3. 交接记录不完整，责任界定困难。\n\n"
        f"[16:9 内文图 1：原理或痛点对比]\n\n"
        f"## 三、{product_hint} 解决机制与实操建议\n\n"
        f"{kb.get('solutions') or '蓝枢提供云端看图与自动版本对比，现场扫码即看最新图纸。'}\n\n"
        f"实操建议：\n"
        f"1. 入场前统一同步最新图纸版本。\n"
        f"2. 重点节点现场拍照存档，问题即时记录。\n"
        f"3. 一键生成交接报告，资料更清晰。\n\n"
        f"[16:9 内文图 2：实机视窗解析]\n\n"
        f"结语：让看图回归本分，把时间留给施工本身。"
    )
    return {"title": title, "body": body}


def _gen_platform_metadata(kb: dict, xhs: dict, long: dict) -> list:
    """生成五平台专属元数据。"""
    product_hint = kb.get("title") or "产品"
    tags_raw = kb.get("tags") or ""
    tag_list = [t.strip() for t in tags_raw.replace("，", ",").split(",") if t.strip()]

    return [
        {
            "type": 1,
            "platform": "小红书",
            "title": xhs["title"],
            "body": xhs["body"],
            "tags": ["#" + t for t in tag_list[:5]] or ["#看图", "#工地", "#效率", "#避坑", "#蓝枢"],
            "cover_index": 0,
            "image_count": 3,
            "note": "首图 3:4 竖版，共传 3 张 3:4 图；必勾 AI 合成内容声明",
        },
        {
            "type": 5,
            "platform": "百家号",
            "title": long["title"][:30],
            "body": long["body"],
            "tags": tag_list[:5],
            "cover_index": 0,
            "image_count": 2,
            "note": "单图 16:9 封面，正文穿插 2 张 16:9 内文图；必勾 AI 生成内容",
        },
        {
            "type": 7,
            "platform": "今日头条",
            "title": long["title"][:30],
            "body": long["body"],
            "tags": tag_list[:5],
            "cover_index": 0,
            "image_count": 2,
            "note": "单图 16:9 封面，正文穿插 2 张 16:9 内文图；必勾 内容由 AI 生成",
        },
        {
            "type": 8,
            "platform": "搜狐号",
            "title": long["title"][:30],
            "body": long["body"],
            "tags": tag_list[:5],
            "cover_index": 0,
            "image_count": 2,
            "note": "标题 ≤30 字，单图 16:9 封面，正文穿插 2 张 16:9 内文图",
        },
        {
            "type": 9,
            "platform": "知乎",
            "title": long["title"][:35],
            "body": long["body"],
            "tags": tag_list[:5],
            "cover_index": 0,
            "image_count": 2,
            "note": "专栏 16:9 题图，正文穿插 2 张 16:9 内文图；按需开启 AI 声明",
        },
    ]


@app.route('/api/generate-content', methods=['POST'])
def generate_content():
    """基于知识库条目生成双版本文案 + 五平台元数据，并做合规自检。"""
    try:
        data = request.get_json() or {}
        kb_id = data.get('kb_id')
        product_id = data.get('product_id')

        if not kb_id or not str(kb_id).isdigit():
            return jsonify({"code": 400, "msg": "kb_id 不能为空", "data": None}), 400

        with sqlite3.connect(_db_path()) as conn:
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()
            cur.execute("SELECT * FROM knowledge_base WHERE id = ?", (int(kb_id),))
            row = cur.fetchone()
            if not row:
                return jsonify({"code": 404, "msg": "知识库条目不存在", "data": None}), 404
            kb = dict(row)

        kb_fields = _build_kb_prompt(kb)
        xhs = _gen_xhs_motherboard(kb_fields)
        long = _gen_long_motherboard(kb_fields)
        platforms = _gen_platform_metadata(kb_fields, xhs, long)

        # 合规自检
        all_texts = [xhs["title"], xhs["body"], long["title"], long["body"]]
        all_texts += [p["title"] for p in platforms] + [p["body"] for p in platforms]
        compliance = {"passed": True, "details": []}
        for text in all_texts:
            r = _compliance_check(text)
            if not r["passed"]:
                compliance["passed"] = False
                compliance["details"].append({"text": text[:60], "hits": r["hits"]})

        result = {
            "kb_id": int(kb_id),
            "product_id": kb.get("product_id"),
            "motherboards": {
                "xhs": xhs,
                "long": long,
            },
            "platforms": platforms,
            "compliance": compliance,
        }
        return jsonify({"code": 200, "msg": "内容生成成功", "data": result})
    except Exception as e:
        print(f"内容生成失败: {e}", flush=True)
        import traceback
        traceback.print_exc()
        return jsonify({"code": 500, "msg": f"内容生成失败: {e}", "data": None}), 500


# =====================================================================
# Step 4：一键发布（复用现有 /postVideo 发布通道）
# =====================================================================


@app.route('/api/publish-from-kb', methods=['POST'])
def publish_from_kb():
    """基于知识库条目生成内容后，直接发布到指定平台（复用 /postVideo）。

    请求体：{ kb_id, type, account_list, file_list?, ... }，其中 type 为平台类型，
    account_list 为账号文件路径列表。生成的文案会自动填入 title/articleBody。
    """
    try:
        data = request.get_json() or {}
        kb_id = data.get('kb_id')
        if not kb_id or not str(kb_id).isdigit():
            return jsonify({"code": 400, "msg": "kb_id 不能为空", "data": None}), 400

        # 先生成内容
        gen_resp = generate_content()
        gen_json = gen_resp.get_json() if hasattr(gen_resp, 'get_json') else None
        if not gen_json or gen_json.get('code') != 200:
            return jsonify({"code": 400, "msg": "内容生成失败，无法发布", "data": None}), 400
        content_data = gen_json['data']

        platform_type = int(data.get('type') or 1)
        # 支持图文文章的平台：1 小红书 2 视频号 5 百家号 7 今日头条 8 搜狐 9 知乎 10 贴吧
        ARTICLE_CAPABLE = {1, 2, 5, 7, 8, 9, 10}

        # 按平台选对应的文案母本
        if platform_type == 1:
            title = content_data['motherboards']['xhs']['title']
            article_body = content_data['motherboards']['xhs']['body']
            tags = content_data['platforms'][0]['tags']
        elif platform_type in ARTICLE_CAPABLE:
            title = content_data['motherboards']['long']['title']
            article_body = content_data['motherboards']['long']['body']
            idx = next((i for i, p in enumerate(content_data['platforms']) if p['type'] == platform_type), 0)
            tags = content_data['platforms'][idx]['tags']
        else:
            title = content_data['motherboards']['long']['title']
            article_body = content_data['motherboards']['long']['body']
            tags = content_data['platforms'][0]['tags']

        # 合成转发给 /postVideo 的请求体
        post_body = dict(data)
        post_body['title'] = title
        post_body['articleBody'] = article_body
        post_body['body'] = article_body
        post_body['tags'] = tags
        post_body['contentType'] = 'article'
        post_body['isDraft'] = True  # 默认存草稿，不自动发布

        # 构造伪请求调用 postVideo 逻辑（直接复用路由函数）
        with app.test_request_context(
            '/postVideo',
            method='POST',
            json=post_body,
            content_type='application/json',
        ):
            return postVideo()
    except Exception as e:
        print(f"一键发布失败: {e}", flush=True)
        import traceback
        traceback.print_exc()
        return jsonify({"code": 500, "msg": f"一键发布失败: {e}", "data": None}), 500


if __name__ == '__main__':
    app.run(host='0.0.0.0' ,port=5409)
