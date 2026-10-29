import sqlite3
import json
import os

# 数据库文件路径（如果不存在会自动创建）
db_file = './database.db'

# 如果数据库已存在，则删除旧的表（可选）
# if os.path.exists(db_file):
#     os.remove(db_file)

# 连接到SQLite数据库（如果文件不存在则会自动创建）
conn = sqlite3.connect(db_file)
cursor = conn.cursor()

# 创建账号记录表
cursor.execute('''
CREATE TABLE IF NOT EXISTS user_info (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    type INTEGER NOT NULL,
    filePath TEXT NOT NULL,  -- 存储文件路径
    userName TEXT NOT NULL,
    status INTEGER DEFAULT 0
)
''')

# 创建文件记录表
cursor.execute('''CREATE TABLE IF NOT EXISTS file_records (
    id INTEGER PRIMARY KEY AUTOINCREMENT, -- 唯一标识每条记录
    filename TEXT NOT NULL,               -- 文件名
    filesize REAL,                     -- 文件大小（单位：MB）
    upload_time DATETIME DEFAULT CURRENT_TIMESTAMP, -- 上传时间，默认当前时间
    file_path TEXT                        -- 文件路径
)
''')

cursor.execute('''
CREATE TABLE IF NOT EXISTS content_stats (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    platform TEXT NOT NULL,
    account_id INTEGER NOT NULL,
    item_id TEXT NOT NULL,
    title TEXT,
    cover_url TEXT,
    status TEXT,
    published_at TEXT,
    play_count INTEGER DEFAULT 0,
    like_count INTEGER DEFAULT 0,
    comment_count INTEGER DEFAULT 0,
    share_count INTEGER DEFAULT 0,
    collect_count INTEGER DEFAULT 0,
    completion_rate REAL,
    avg_play_duration_sec REAL,
    bounce_rate_2s REAL,
    completion_rate_5s REAL,
    avg_play_percent REAL,
    traffic_sources_json TEXT,
    extra_json TEXT,
    boost_play_count INTEGER,
    boost_reason TEXT,
    synced_at TEXT NOT NULL,
    raw_json TEXT,
    UNIQUE(platform, account_id, item_id)
)
''')


cursor.execute('''
CREATE TABLE IF NOT EXISTS publish_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    platform_type INTEGER NOT NULL,
    platform_name TEXT NOT NULL,
    account_name TEXT NOT NULL,
    content_type TEXT NOT NULL,
    title TEXT,
    content TEXT,
    tags TEXT,
    files TEXT,
    status TEXT DEFAULT 'pending',
    error_msg TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    published_at DATETIME
)''')
# 为已有库的 publish_history 补充贴吧 fid/kw 字段（兼容旧库，不重建表）
_pub_hist_cols = [row[1] for row in cursor.execute("PRAGMA table_info(publish_history)").fetchall()]
if 'tieba_fid' not in _pub_hist_cols:
    cursor.execute("ALTER TABLE publish_history ADD COLUMN tieba_fid TEXT")
if 'tieba_kw' not in _pub_hist_cols:
    cursor.execute("ALTER TABLE publish_history ADD COLUMN tieba_kw TEXT")
cursor.execute('''
CREATE TABLE IF NOT EXISTS publish_drafts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT,
    platform_type INTEGER,
    platform_name TEXT,
    content_type TEXT,
    body TEXT,
    tags TEXT,
    files_json TEXT,
    account_ids_json TEXT,
    ai_generated INTEGER DEFAULT 0,
    dry_run INTEGER DEFAULT 0,
    status TEXT DEFAULT 'draft',
    completion_note TEXT,
    synced_platforms TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
)
''')

# 产品主表（每个产品一行，后续可接入品牌色、Logo 等）
cursor.execute('''
CREATE TABLE IF NOT EXISTS products (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    description TEXT,
    logo_path TEXT,
    brand_color TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
)
''')

# 知识库条目表（属于某个产品，用于生成该产品的发布文案与元数据）
cursor.execute('''
CREATE TABLE IF NOT EXISTS knowledge_base (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id INTEGER NOT NULL,
    title TEXT NOT NULL,
    content TEXT,
    pain_points TEXT,
    solutions TEXT,
    tags TEXT,
    file_paths TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (product_id) REFERENCES products(id)
)
''')

# 为 publish_history 补充 product_id（可选，用于发布记录溯源）
_pub_hist_cols = [row[1] for row in cursor.execute("PRAGMA table_info(publish_history)").fetchall()]
if 'product_id' not in _pub_hist_cols:
    cursor.execute("ALTER TABLE publish_history ADD COLUMN product_id INTEGER")

# 提交更改
conn.commit()
print("✅ 表创建成功")
# 关闭连接
conn.close()
