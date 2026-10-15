# -*- coding: utf-8 -*-
"""内置矢量图标：SVG 字符串 → QPixmap / QIcon。

为什么不用图片文件
------------------
1. 打包后不用多带一个资源目录，少一处漏图风险；
2. 图标要跟着状态换色（灰 → 蓝），矢量按需渲染最省事；
3. 高 DPI 下按 2 倍栅格化，边缘依然干净。

所有图标统一 24×24 viewBox、线性描边（stroke-width 1.7），
形状一律不依赖第三方品牌标识。
"""
from __future__ import annotations

from PySide6.QtCore import QByteArray, Qt
from PySide6.QtGui import QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer

C = "__C__"   # 颜色占位符（用 replace 注入，避免 f-string 与 format 打架）

_SVG = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" '
    'stroke="__C__" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round">'
    "{body}</svg>"
)

ICONS: dict[str, str] = {
    "user": '<circle cx="12" cy="8.2" r="3.6"/>'
            '<path d="M4.8 20.2c0-3.7 3.2-5.8 7.2-5.8s7.2 2.1 7.2 5.8"/>',
    # 证件卡式「用户中心」：和「账号登录」的纯头像图标区分开
    "profile": '<rect x="3.4" y="5.4" width="17.2" height="13.2" rx="2.4"/>'
               '<circle cx="9.4" cy="11" r="2.1"/>'
               '<path d="M6.4 16.2c.5-1.5 1.7-2.3 3-2.3s2.5.8 3 2.3"/>'
               '<path d="M15.6 10.4h2.6M15.6 13.6h2.6"/>',
    "book": '<path d="M4 6.2c2.6-1.3 5.4-1.3 8 0 2.6-1.3 5.4-1.3 8 0v12'
            'c-2.6-1.3-5.4-1.3-8 0-2.6-1.3-5.4-1.3-8 0z"/>'
            '<path d="M12 6.2v12"/>',
    "sliders": '<path d="M4.5 7.5h9M17.5 7.5h2M4.5 16.5h3M11.5 16.5h8"/>'
               '<circle cx="15.5" cy="7.5" r="2.1"/><circle cx="9.5" cy="16.5" r="2.1"/>',
    "play": '<circle cx="12" cy="12" r="8.4"/>'
            '<path d="M10.3 8.9l5 3.1-5 3.1z" fill="__C__" stroke="none"/>',
    "check": '<path d="M5.5 12.6l4.2 4.2 8.8-9.4"/>',
    "check-circle": '<circle cx="12" cy="12" r="8.4"/><path d="M8.2 12.3l2.6 2.6 5-5.2"/>',
    "alert": '<path d="M12 4.6l8.2 14.2H3.8z"/><path d="M12 10v3.6M12 16.6v.1"/>',
    "info": '<circle cx="12" cy="12" r="8.4"/><path d="M12 11v5.4M12 7.9v.1"/>',
    "clock": '<circle cx="12" cy="12" r="8.4"/><path d="M12 7.4V12l3.2 1.9"/>',
    "list": '<path d="M8.6 6.8h10.8M8.6 12h10.8M8.6 17.2h10.8"/>'
            '<circle cx="5" cy="6.8" r="1.1" fill="__C__" stroke="none"/>'
            '<circle cx="5" cy="12" r="1.1" fill="__C__" stroke="none"/>'
            '<circle cx="5" cy="17.2" r="1.1" fill="__C__" stroke="none"/>',
    "layers": '<path d="M12 3.8l8.2 4.3-8.2 4.3-8.2-4.3z"/>'
              '<path d="M4.6 12.2l7.4 3.9 7.4-3.9"/>'
              '<path d="M4.6 16.1l7.4 3.9 7.4-3.9"/>',
    "chart": '<path d="M4.5 19.4h15"/><path d="M7.6 19.4v-5.8M12 19.4v-9.8M16.4 19.4v-3.6"/>',
    "shield": '<path d="M12 3.6l7 2.6v5.4c0 4.2-2.9 7.3-7 8.8'
              '-4.1-1.5-7-4.6-7-8.8V6.2z"/>',
    "zap": '<path d="M13.4 3.6L6.2 13.4h4.6l-1.2 7 7.2-9.8h-4.6z"/>',
    "refresh": '<path d="M20 11.4a8 8 0 1 0-1.4 5.6"/><path d="M20 4.4v5.2h-5.2"/>',
    "folder": '<path d="M3.8 7.4a2 2 0 0 1 2-2h3l1.8 2.2h7.6a2 2 0 0 1 2 2v7.6'
              'a2 2 0 0 1-2 2H5.8a2 2 0 0 1-2-2z"/>',
    "file": '<path d="M13.6 3.6H7.4a2 2 0 0 0-2 2v12.8a2 2 0 0 0 2 2h9.2'
            'a2 2 0 0 0 2-2V8.6z"/><path d="M13.6 3.6V8.6h5"/>',
    "key": '<circle cx="8.6" cy="15.4" r="3.4"/>'
           '<path d="M11 13l7.4-7.4M16.4 7.6l2 2M14.2 9.8l2 2"/>',
    "globe": '<circle cx="12" cy="12" r="8.4"/>'
             '<path d="M3.8 12h16.4M12 3.8c2.2 2.4 3.4 5.2 3.4 8.2s-1.2 5.8-3.4 8.2'
             'c-2.2-2.4-3.4-5.2-3.4-8.2s1.2-5.8 3.4-8.2z"/>',
    "bell": '<path d="M18 16.4V11a6 6 0 1 0-12 0v5.4L4.4 18.4h15.2z"/>'
            '<path d="M10.2 21.4h3.6"/>',
    "inbox": '<path d="M6.4 5.4h11.2l2.6 8.2v5a2 2 0 0 1-2 2H5.8a2 2 0 0 1-2-2v-5z"/>'
             '<path d="M3.8 13.6h4l1.2 2.2h6l1.2-2.2h4"/>',
    "search": '<circle cx="11" cy="11" r="6.4"/><path d="M15.8 15.8l4 4"/>',
    "spark": '<path d="M12 3.8l1.9 5.1 5.1 1.9-5.1 1.9L12 17.8l-1.9-5.1L5 10.8l5.1-1.9z"/>',
    "download": '<path d="M12 4.4v10.2"/><path d="M8.2 11.2L12 15l3.8-3.8"/>'
                '<path d="M4.8 18.6h14.4"/>',
    "link": '<path d="M10 13.6a3.4 3.4 0 0 0 4.8 0l3-3a3.4 3.4 0 0 0-4.8-4.8l-1 1"/>'
            '<path d="M14 10.4a3.4 3.4 0 0 0-4.8 0l-3 3a3.4 3.4 0 0 0 4.8 4.8l1-1"/>',
    "tune": '<path d="M4.5 6.5h6M14.5 6.5h5M4.5 12h2M10.5 12h9M4.5 17.5h9M17.5 17.5h2"/>'
            '<circle cx="12.5" cy="6.5" r="2.1"/><circle cx="8.5" cy="12" r="2.1"/>'
            '<circle cx="15.5" cy="17.5" r="2.1"/>',
    "power": '<path d="M12 3.8v7.4"/><path d="M17.2 6.8a7.4 7.4 0 1 1-10.4 0"/>',
    "clipboard": '<path d="M9 4.6h6a1.6 1.6 0 0 1 1.6 1.6v12.4a1.6 1.6 0 0 1-1.6 1.6H9'
                 'a1.6 1.6 0 0 1-1.6-1.6V6.2A1.6 1.6 0 0 1 9 4.6z"/>'
                 '<path d="M9.6 4.6V3.4h4.8v1.2"/>',
    "x": '<path d="M6.6 6.6l10.8 10.8M17.4 6.6L6.6 17.4"/>',
    # U校园（v1.5.2b）：U 形通道 + 短柄 + 底部「校园定位点」，
    # 与学习通的 book/play 图标语言区分开（品牌独立感）
    "unipus": '<path d="M7 3.4v7.4a5 5 0 0 0 10 0V3.4"/>'
              '<path d="M12 16.2v2"/>'
              '<circle cx="12" cy="20.6" r="1.35" fill="__C__" stroke="none"/>',
    # 雨课堂（v1.5.4）：水滴外形 + 内部播放三角。
    # 「会下雨的课堂」+「视频课」两个语义各占一半，形状和 play/book/unipus 都不撞。
    "yuketang": '<path d="M12 3.4c3.1 3.4 5.6 6.2 5.6 9.1a5.6 5.6 0 0 1-11.2 0'
                'c0-2.9 2.5-5.7 5.6-9.1z"/>'
                '<path d="M10.4 11.7l3.7 2.2-3.7 2.2z" fill="__C__" stroke="none"/>',
    # 导航分组标题左边的小圆点：按平台染成品牌色，让侧栏读起来是"彩色分区"
    # 智慧树（知到）：叠层树冠 + 树干
    "zhihuishu": '<path d="M12 3.5l5 6.8h-2.7l3.4 4.6H6.3l3.4-4.6H7z"/>'
                 '<path d="M12 14.9v5.6"/>',
    "dot": '<circle cx="12" cy="12" r="5.4" fill="__C__" stroke="none"/>',
}

# ---------------------------------------------------------------- U校园品牌标志
# 彩色版 logo（不走 __C__ 单色染色）：主色青绿渐变圆角方块 + 白色 U + 定位点。
# v1.6.8：由橙红改为主色青绿 —— 四平台只用一套强调色，不再各自一个色相。
UNIPUS_BRAND_SVG = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 48 48">'
    '<defs><linearGradient id="g" x1="0" y1="0" x2="1" y2="1">'
    '<stop offset="0" stop-color="#345FE5"/>'
    '<stop offset="1" stop-color="#294DC2"/>'
    '</linearGradient></defs>'
    '<rect x="1.5" y="1.5" width="45" height="45" rx="12.5" fill="url(#g)"/>'
    '<path d="M16.5 14v8.8a7.5 7.5 0 0 0 15 0V14" fill="none" stroke="#FFFFFF"'
    ' stroke-width="4.2" stroke-linecap="round" stroke-linejoin="round"/>'
    '<circle cx="24" cy="35.6" r="2.5" fill="#FFFFFF"/>'
    '<circle cx="35.2" cy="12.8" r="1.9" fill="#FFFFFF" opacity="0.85"/>'
    '</svg>'
)


def brand_pixmap(size: int, dpr: int = 2) -> QPixmap:
    """U校园彩色品牌标志（Hero 条 / 空态用，随 size 栅格化）。"""
    renderer = QSvgRenderer(QByteArray(UNIPUS_BRAND_SVG.encode("utf-8")))
    pm = QPixmap(size * dpr, size * dpr)
    pm.fill(Qt.transparent)
    if renderer.isValid():
        p = QPainter(pm)
        p.setRenderHint(QPainter.Antialiasing, True)
        p.setRenderHint(QPainter.SmoothPixmapTransform, True)
        renderer.render(p)
        p.end()
    pm.setDevicePixelRatio(dpr)
    return pm


def svg_body(name: str) -> str:
    return ICONS.get(name, ICONS["info"])


def pixmap(name: str, color: str, size: int, dpr: int = 2) -> QPixmap:
    """把图标栅格化成 QPixmap（dpr 倍超采样，逻辑尺寸 = size）。"""
    src = _SVG.format(body=svg_body(name)).replace(C, color)
    renderer = QSvgRenderer(QByteArray(src.encode("utf-8")))
    pm = QPixmap(size * dpr, size * dpr)
    pm.fill(Qt.transparent)
    if renderer.isValid():
        p = QPainter(pm)
        p.setRenderHint(QPainter.Antialiasing, True)
        p.setRenderHint(QPainter.SmoothPixmapTransform, True)
        renderer.render(p)
        p.end()
    pm.setDevicePixelRatio(dpr)
    return pm


def icon(name: str, color: str, size: int = 18) -> QIcon:
    return QIcon(pixmap(name, color, size))
