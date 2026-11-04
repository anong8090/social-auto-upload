# -*- coding: utf-8 -*-
import shutil
from pathlib import Path

BASE = Path('/Users/anong/Developer/market_for_team')
IMG = BASE / '河狸云蓝枢_自媒体物料包' / '图片素材' / '12_工地图纸多找详图难'
VID = BASE / 'omnipost' / 'videoFile'
VID.mkdir(parents=True, exist_ok=True)

files = [
    IMG / '01_16x9_头图封面_工地图纸多找详图难.png',
    IMG / '02_16x9_内文图1_工地图纸多找详图难.png',
    IMG / '03_16x9_内文图2_工地图纸多找详图难.png',
    IMG / '04_3x4_小红书封面_工地图纸多找详图难.png',
    IMG / '05_3x4_小红书内页1_工地图纸多找详图难.png',
    IMG / '06_3x4_小红书内页2_工地图纸多找详图难.png',
]
for p in files:
    shutil.copy2(p, VID / p.name)
    print('覆盖:', p.name)
print('配图已全部覆盖到 omnipost/videoFile/')
