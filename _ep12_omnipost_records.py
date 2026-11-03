# -*- coding: utf-8 -*-
import sqlite3, json, shutil
from datetime import datetime
from pathlib import Path

BASE = Path('/Users/anong/Developer/market_for_team')
IMG = BASE / '河狸云蓝枢_自媒体物料包' / '图片素材' / '12_工地图纸多找详图难'
VID = BASE / 'omnipost' / 'videoFile'
VID.mkdir(parents=True, exist_ok=True)
DB = BASE / 'omnipost' / 'db' / 'database.db'

xhs_imgs = [
    IMG / '04_3x4_小红书封面_工地图纸多找详图难.png',
    IMG / '05_3x4_小红书内页1_工地图纸多找详图难.png',
    IMG / '06_3x4_小红书内页2_工地图纸多找详图难.png',
]
long_imgs = [
    IMG / '01_16x9_头图封面_工地图纸多找详图难.png',
    IMG / '02_16x9_内文图1_工地图纸多找详图难.png',
    IMG / '03_16x9_内文图2_工地图纸多找详图难.png',
]

def copy_imgs(imgs):
    rels = []
    for p in imgs:
        dst = VID / p.name
        shutil.copy2(p, dst)
        rels.append(f'omnipost/videoFile/{p.name}')
        print('  复制:', p.name)
    return rels

xhs_body = (BASE / '河狸云蓝枢_自媒体物料包/文案合集/12_工地图纸多找详图难_一稿五投/母本A_小红书短图文_工地图纸多找详图难.md').read_text(encoding='utf-8')
long_body = (BASE / '河狸云蓝枢_自媒体物料包/文案合集/12_工地图纸多找详图难_一稿五投/母本B_四平台精炼长文_工地图纸多找详图难.md').read_text(encoding='utf-8')

tags = '施工图纸,看图找详图,工程人,工地日常,图纸管理'
now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

records = [
    (1, '工地图纸找详图翻半天', 1, '小红书', 'note', xhs_body, tags, copy_imgs(xhs_imgs), '暂存离开，图文笔记×3', '小红书'),
    (2, '工地图纸几十张，找详图翻半天怎么办？', 5, '百家号', 'article', long_body, tags, copy_imgs(long_imgs), '存草稿，不自动发布', '百家号'),
    (3, '工地图纸几十张，找详图翻半天怎么办？', 7, '今日头条', 'article', long_body, tags, copy_imgs(long_imgs), '存草稿，不自动发布', '今日头条'),
    (4, '工地图纸几十张，找详图翻半天怎么办？', 8, '搜狐号', 'article', long_body, tags, copy_imgs(long_imgs), '存草稿，不自动发布', '搜狐号'),
    (5, '工地图纸几十张，找详图翻半天怎么办？', 9, '知乎', 'article', long_body, tags, copy_imgs(long_imgs), '存草稿，不自动发布', '知乎'),
]

con = sqlite3.connect(str(DB))
cur = con.cursor()
for id_, title, pt, pn, ctype, body, tgs, files, note, pname in records:
    cur.execute('''INSERT INTO publish_drafts
    (title,platform_type,platform_name,content_type,body,tags,files_json,account_ids_json,ai_generated,dry_run,created_at,updated_at,status,completion_note,synced_platforms)
    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
    (title, pt, pn, ctype, body, tgs, json.dumps(files, ensure_ascii=False), '[]', 1, 0, now, now, 'draft', note, pname))
    print(f'  OmniPost记录: {title} ({pname}) id={cur.lastrowid}')
con.commit()
con.close()
print('全部完成：配图已复制，5条OmniPost记录已写入')
