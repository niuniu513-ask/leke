# -*- coding: utf-8 -*-
"""乐课品牌图标：应用图标 / 窗口图标 / 界面内 logo。

为什么内置 SVG 字符串（同 `gui_app/icons.py` 的思路）
----------------------------------------------------
1. 打包后不用多带资源文件，少一处漏图风险；
2. 高 DPI 下按目标尺寸原生栅格化，边缘依然干净；
3. 图标要跟着主题换色时，改字符串里的 `currentColor` 即可。

两档图形
--------
* **≥32px**：完整品牌笔触（手写连笔「乐」），细节可辨；
* **16 / 24px**：同源**简化骨架**（笔触右侧那段上扬主弧）。
  细线笔触缩到 16px 会糊成一团，换骨架后浏览器标签页、任务栏小图标依然干净。

⚠ 与网站同源：`kc_server/brand/gen_icons.py` 生成网站的 favicon / PWA 图标与
Windows exe 用的 `leke.ico`；本文件的内嵌 SVG 由它导出。**改图标要两边一起改**，
改完跑 `gen_icons.py` 再跑 `install_icons.py` 同步到各平台。
"""
from __future__ import annotations

from functools import lru_cache

from PySide6.QtCore import QByteArray, Qt
from PySide6.QtGui import QColor, QFont, QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer

#: 完整品牌笔触（512 视窗，青绿渐变圆角底 + 白色连笔「乐」）
ICON_FULL_SVG = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 512 512">'
    '<defs><linearGradient id="bg" x1="0" y1="0" x2="0.8" y2="1">'
    '<stop offset="0" stop-color="#345FE5"/><stop offset="1" stop-color="#294DC2"/>'
    '</linearGradient></defs>'
    '<rect x="0" y="0" width="512" height="512" rx="116.22" ry="116.22" fill="url(#bg)"/>'
    '<rect x="1.5" y="1.5" width="509" height="509" rx="114.72" ry="114.72" fill="none" '
    'stroke="#ffffff" stroke-opacity="0.12" stroke-width="3"/>'
    '<g transform="translate(31.429,54.257) scale(1.42675)">'
    '<path d="M128 42C116 61 108 81 110 99c2 22 15 43 35 56 19 12 41 14 58 3 14-9 26-27 36-46 '
    '10-19 16-37 9-54-3-8-9-14-18-16-10-3-17 1-23 8-13 16-23 40-29 65 -6 23-11 44-24 61'
    '-12 16-31 28-53 30-21 2-45-5-59-18-10-9-12-20-6-28 7-10 20-14 34-11 19 3 40 12 61 21 '
    '26 11 52 19 81 25 24 5 47 8 63 9" fill="none" stroke="#FFFFFF" stroke-width="10" '
    'stroke-linecap="round" stroke-linejoin="round"/></g></svg>'
)

#: 小尺寸简化骨架（100 视窗，同一底 + 上扬主弧），16/24px 用
ICON_COMPACT_SVG = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100">'
    '<defs><linearGradient id="bg" x1="0" y1="0" x2="0.8" y2="1">'
    '<stop offset="0" stop-color="#345FE5"/><stop offset="1" stop-color="#294DC2"/>'
    '</linearGradient></defs>'
    '<rect x="0" y="0" width="100" height="100" rx="22.7" ry="22.7" fill="url(#bg)"/>'
    '<rect x="1.5" y="1.5" width="97" height="97" rx="21.2" ry="21.2" fill="none" '
    'stroke="#ffffff" stroke-opacity="0.12" stroke-width="3"/>'
    '<g transform="translate(-1.478,3.067) scale(1.03597)">'
    '<path d="M 30 82 C 20 54 42 26 64 25 C 78 24 84 34 80 44" fill="none" stroke="#FFFFFF" '
    'stroke-width="16" stroke-linecap="round" stroke-linejoin="round"/></g></svg>'
)

#: 纯笔触（透明底，描边取 currentColor）—— 界面里当 logo 用
MARK_SVG_TMPL = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 316 244">'
    '<path d="M128 42C116 61 108 81 110 99c2 22 15 43 35 56 19 12 41 14 58 3 14-9 26-27 36-46 '
    '10-19 16-37 9-54-3-8-9-14-18-16-10-3-17 1-23 8-13 16-23 40-29 65 -6 23-11 44-24 61'
    '-12 16-31 28-53 30-21 2-45-5-59-18-10-9-12-20-6-28 7-10 20-14 34-11 19 3 40 12 61 21 '
    '26 11 52 19 81 25 24 5 47 8 63 9" fill="none" stroke="{color}" stroke-width="9" '
    'stroke-linecap="round" stroke-linejoin="round"/></svg>'
)

#: 低于此尺寸换成简化骨架
COMPACT_BELOW = 32
#: 打进 QIcon 的档位
ICON_SIZES = (16, 24, 32, 48, 64, 128, 256)


def svg_for(size: int) -> str:
    """按目标尺寸挑图形。"""
    return ICON_COMPACT_SVG if size < COMPACT_BELOW else ICON_FULL_SVG


def render_pixmap(svg: str, width: int, height: int) -> QPixmap:
    """SVG → QPixmap（按需栅格化，支持任意 DPR）。"""
    pm = QPixmap(width, height)
    pm.fill(Qt.transparent)
    r = QSvgRenderer(QByteArray(svg.encode("utf-8")))
    if not r.isValid():
        raise ValueError("品牌图标 SVG 解析失败")
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing, True)
    p.setRenderHint(QPainter.SmoothPixmapTransform, True)
    r.render(p)
    p.end()
    return pm


@lru_cache(maxsize=1)
def app_icon() -> QIcon:
    """应用 / 窗口 / 托盘图标（多档一次栅格化，Qt 按需挑最接近的）。"""
    try:
        icon = QIcon()
        for size in ICON_SIZES:
            icon.addPixmap(render_pixmap(svg_for(size), size, size))
        if not icon.isNull():
            return icon
    except Exception:
        pass
    return _fallback_icon()


@lru_cache(maxsize=1)
def _fallback_icon() -> QIcon:
    """兜底：万一 SVG 渲染不可用，退回「青绿圆角块 + 白色乐字」。"""
    try:
        from gui_app import theme as T
        primary = T.PRIMARY
    except Exception:
        primary = "#294DC2"
    pm = QPixmap(64, 64)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing, True)
    p.setPen(Qt.NoPen)
    p.setBrush(QColor(primary))
    p.drawRoundedRect(2, 2, 60, 60, 16, 16)
    p.setPen(QColor("#FFFFFF"))
    f = QFont()
    f.setPointSize(30)
    f.setBold(True)
    p.setFont(f)
    p.drawText(pm.rect(), Qt.AlignCenter, "乐")
    p.end()
    return QIcon(pm)


def mark_pixmap(height: int, color: str) -> QPixmap:
    """界面内的品牌笔触（透明底）。height 为像素高，宽按 316:244 比例。"""
    width = max(1, round(height * 316 / 244))
    return render_pixmap(MARK_SVG_TMPL.replace("{color}", color), width, height)


def brand_mark_svg(color: str) -> str:
    """笔触 SVG 源码（给需要矢量内联的场景）。"""
    return MARK_SVG_TMPL.replace("{color}", color)
