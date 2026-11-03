# -*- coding: utf-8 -*-
"""第 13 期五平台发布完成后，写入本地 OmniPost publish_drafts 记录。"""
import sqlite3, json, shutil
from datetime import datetime
from pathlib import Path

BASE = Path('/Users/anong/Developer/market_for_team')
IMG = BASE / '河狸云蓝枢_自媒体物料包' / '图片素材' / '13_设计变更后图纸怎么跟'
DOCS = BASE / '河狸云蓝枢_自媒体物料包' / '文案合集' / '13_设计变更后图纸怎么跟_一稿五投'
VID = BASE / 'omnipost' / 'videoFile'
VID.mkdir(parents=True, exist_ok=True)
DB = BASE / 'omnipost' / 'db' / 'database.db'

xhs_imgs = [
    IMG / '04_3x4_小红书封面_设计变更后图纸怎么跟.png',
    IMG / '05_3x4_小红书内页1_设计变更后图纸怎么跟.png',
    IMG / '06_3x4_小红书内页2_设计变更后图纸怎么跟.png',
]
long_imgs = [
    IMG / '01_16x9_头图封面_设计变更后图纸怎么跟.png',
    IMG / '02_16x9_内文图1_设计变更后图纸怎么跟.png',
    IMG / '03_16x9_内文图2_设计变更后图纸怎么跟.png',
]


def copy_imgs(imgs):
    rels = []
    for p in imgs:
        dst = VID / p.name
        shutil.copy2(p, dst)
        rels.append(f'omnipost/videoFile/{p.name}')
    return rels


xhs_body = (DOCS / '母本A_小红书短图文_设计变更后图纸怎么跟.md').read_text(encoding='utf-8')
long_body = (DOCS / '母本B_四平台精炼长文_设计变更后图纸怎么跟.md').read_text(encoding='utf-8')

tags = '设计变更,施工图纸,工程人,工地日常,图纸管理'
now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
LONG_TITLE = '设计变更一来，手上那叠图该换哪几张？'

records = [
    ('设计变更来了，手上这叠图怎么跟', 1, '小红书', 'note', xhs_body,
     copy_imgs(xhs_imgs), '已发布：图文笔记×3，含AI合成内容声明', '小红书'),
    (LONG_TITLE, 5, '百家号', 'article', long_body,
     copy_imgs(long_imgs), '已发布：单图封面+2张内文图，采用AI生成内容', '百家号'),
    (LONG_TITLE, 7, '今日头条', 'article', long_body,
     copy_imgs(long_imgs), '已发布：单图封面+2张内文图，声明引用AI', '今日头条'),
    (LONG_TITLE, 8, '搜狐号', 'article', long_body,
     copy_imgs(long_imgs), '已发布：单图封面+2张内文图，含有AI生成内容', '搜狐号'),
    (LONG_TITLE, 9, '知乎', 'article', long_body,
     copy_imgs(long_imgs), '已发布：专栏文章，包含 AI 辅助创作声明', '知乎'),
]

con = sqlite3.connect(str(DB))
cur = con.cursor()
for title, pt, pn, ctype, body, files, note, pname in records:
    cur.execute('''INSERT INTO publish_drafts
    (title,platform_type,platform_name,content_type,body,tags,files_json,account_ids_json,
     ai_generated,dry_run,created_at,updated_at,status,completion_note,synced_platforms)
    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
    (title, pt, pn, ctype, body, tags, json.dumps(files, ensure_ascii=False), '[]',
     1, 0, now, now, 'published', note, pname))
    print(f'  ✅ {pn}: {title} (id={cur.lastrowid})')
con.commit()
con.close()
print('第 13 期 5 条发布记录已写入 OmniPost')
