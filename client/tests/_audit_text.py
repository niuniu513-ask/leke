# -*- coding: utf-8 -*-
"""客户端文字审计：逐页找「文字溢出 / 裁切」与「低对比（色混）」。

为什么不用 offscreen：offscreen 下字体 fallback 成 Sans Serif，英文按 1em 计
（'ABCD' 11px→44px，真实约 30px），会把「正常」误报成溢出。
这里用真实 windows 平台 + WA_DontShowOnScreen（不弹窗但走真实布局与渲染）。

判据
----
1. 横向溢出：`sizeHint().width() > width()`（sizeHint 含 QSS padding，比手算 text_w 准）
2. 纵向裁切：wordWrap 控件 `heightForWidth(w) > height()`
3. 低对比：grab() 后对控件区域做像素统计，取众数色为底、差异色为字，
   算 WCAG 对比度；正文 <4.5 / 大字(<18px 且非 bold) <3.0 报「色混」

跑法：cd chaoxing-main; .venv/Scripts/python.exe tests/_audit_text.py
"""
from __future__ import annotations

import os
import sys
from collections import Counter

os.environ.setdefault("QT_QPA_PLATFORM", "windows")

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtGui import QFontMetrics  # noqa: E402
from PySide6.QtWidgets import QApplication, QLabel, QWidget  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

app = QApplication(sys.argv)
from gui_app import theme as T                              # noqa: E402
from gui_app.main_window import MainWindow, PAGE_KEYS       # noqa: E402

app.setStyleSheet(T.QSS)

TOL = 2          # 允许 2px 误差（抗锯齿 / 取整）
OVERFLOW: list = []
CONTRAST: list = []
KEEP: dict = {}  # 持有窗口引用，避免提前析构


# ---------------- 颜色工具 ----------------
def _srgb(c: float) -> float:
    c = c / 255.0
    return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4


def lum(rgb) -> float:
    r, g, b = (_srgb(v) for v in rgb[:3])
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def ratio(c1, c2) -> float:
    a, b = lum(c1), lum(c2)
    hi, lo = max(a, b), min(a, b)
    return (hi + 0.05) / (lo + 0.05)


def dist(c1, c2) -> float:
    return sum(abs(int(a) - int(b)) for a, b in zip(c1[:3], c2[:3]))


def widget_text(w: QWidget) -> str:
    t = getattr(w, "text", None)
    if callable(t):
        try:
            return (w.text() or "").strip()
        except Exception:
            return ""
    return ""


def content_avail(w: QWidget, text_w: int) -> int:
    """控件里真正能放文字的宽度 —— 不猜 padding，让 Qt 自己算。

    QLabel 没有内容区 API，但 sizeHint = 文字宽 + QSS padding，可以反推。
    第一版用"猜的 pad_est"报出 286 处，全部是误报（QLabel 压根没 padding）。
    """
    from PySide6.QtWidgets import (QAbstractSpinBox, QCheckBox, QComboBox, QLineEdit,
                                   QPushButton, QRadioButton, QStyle, QStyleOptionButton,
                                   QStyleOptionComboBox, QStyleOptionFrame,
                                   QStyleOptionSpinBox, QToolButton)
    st = w.style()
    try:
        if isinstance(w, (QPushButton, QToolButton)):
            o = QStyleOptionButton()
            o.initFrom(w)
            r = st.subElementRect(QStyle.SE_PushButtonContents, o, w)
            ic = w.icon()
            if ic is not None and not ic.isNull():
                r = r.adjusted(w.iconSize().width() + 4, 0, 0, 0)
            return r.width()
        if isinstance(w, (QCheckBox, QRadioButton)):
            o = QStyleOptionButton()
            o.initFrom(w)
            return st.subElementRect(QStyle.SE_CheckBoxContents, o, w).width()
        if isinstance(w, QComboBox):
            o = QStyleOptionComboBox()
            o.initFrom(w)
            return st.subControlRect(QStyle.CC_ComboBox, o, QStyle.SC_ComboBoxEditField, w).width()
        if isinstance(w, QAbstractSpinBox):
            o = QStyleOptionSpinBox()
            o.initFrom(w)
            return st.subControlRect(QStyle.CC_SpinBox, o, QStyle.SC_SpinBoxEditField, w).width()
        if isinstance(w, QLineEdit):
            o = QStyleOptionFrame()
            o.initFrom(w)
            return st.subElementRect(QStyle.SE_LineEditContents, o, w).width()
    except Exception:
        pass
    hint = w.sizeHint().width()
    padding = max(0, hint - text_w)
    return w.width() - padding


def plain(text: str) -> str:
    """富文本去掉标签后再量宽（否则 <b>xxx</b> 会把标签也算进去）。"""
    if "<" not in text:
        return text
    import re
    return re.sub(r"<[^>]+>", "", text)


def audit_overflow(w: QWidget, page: str) -> None:
    text = widget_text(w)
    if not text or not w.isVisible():
        return
    fm = QFontMetrics(w.font())
    t = plain(text)
    if not t:
        return
    if getattr(w, "wordWrap", lambda: False)():
        need_h = w.heightForWidth(max(w.width(), 1))
        if need_h > w.height() + TOL:
            OVERFLOW.append((page, w, "纵向裁切", f"需 {need_h}px / 实高 {w.height()}px", t, True))
        return
    text_w = max(fm.horizontalAdvance(x) for x in t.split("\n"))
    avail = content_avail(w, text_w)
    if text_w > avail + TOL:
        OVERFLOW.append((page, w, "横向溢出",
                         f"文字 {text_w}px > 可用 {avail}px（控件 {w.width()}px）", t, True))
    elif avail - text_w < 6:
        # 只有「可能被固定宽度卡住」的控件才关心余量；QLabel / Badge 的宽度天然
        # 贴合文字（余量 0 是正常设计），报出来全是噪音（实测 559 条里 0 条有用）。
        from PySide6.QtWidgets import QAbstractSpinBox, QComboBox, QPushButton, QToolButton
        if not isinstance(w, (QPushButton, QToolButton, QComboBox, QAbstractSpinBox)):
            return
        OVERFLOW.append((page, w, "余量过紧",
                         f"文字 {text_w}px / 可用 {avail}px（仅剩 {avail - text_w}px）", t, False))


def text_color(w: QWidget) -> tuple:
    """控件真实文字色。

    QSS 的 `color:` 会写进 palette（QStyleSheetStyle 会同步），比像素采样稳：
    早先版本按"与底色差异最大的 25% 像素"估前景，会把抗锯齿过渡色
    （#E6E7F2，dist 仅 62）误当成文字，报出一堆 1.23:1 的假问题。
    """
    from PySide6.QtGui import QPalette
    pal = w.palette()
    role = (QPalette.ColorRole.ButtonText
            if hasattr(w, "isCheckable") or type(w).__name__ in
            ("QPushButton", "QToolButton", "QCheckBox", "QRadioButton")
            else QPalette.ColorRole.WindowText)
    c = pal.color(role)
    if c.alpha() == 0:
        c = pal.color(QPalette.ColorRole.WindowText)
    return (c.red(), c.green(), c.blue())


def audit_contrast(w: QWidget, page: str, win: QWidget, img, dpr: float) -> None:
    text = widget_text(w)
    if not text or not w.isVisible() or w.width() < 8 or w.height() < 6:
        return
    fg = text_color(w)
    tl = w.mapTo(win, w.rect().topLeft())
    x0, y0 = int(tl.x() * dpr), int(tl.y() * dpr)
    x1, y1 = int((tl.x() + w.width()) * dpr), int((tl.y() + w.height()) * dpr)
    x0, y0 = max(x0, 0), max(y0, 0)
    x1, y1 = min(x1, img.width()), min(y1, img.height())
    if x1 - x0 < 4 or y1 - y0 < 4:
        return
    cnt: Counter = Counter()
    for y in range(y0, y1):
        for x in range(x0, x1):
            c = img.pixelColor(x, y)
            rgb = (c.red(), c.green(), c.blue())
            if dist(rgb, fg) > 40:          # 排除文字本身，剩下的众数就是底
                cnt[rgb] += 1
    if not cnt:
        return
    bg = cnt.most_common(1)[0][0]
    r = ratio(fg, bg)
    bold = w.font().bold()
    big = w.font().pixelSize() >= 18 or (bold and w.font().pixelSize() >= 14)
    need = 3.0 if big else 4.5
    if r < need:
        CONTRAST.append((page, w, r, need, bg, fg, text, w.isEnabled()))


def audit_page(win: MainWindow, key: str, size: tuple) -> None:
    win.resize(*size)
    win.goto(key)
    app.processEvents()
    app.processEvents()
    img = win.grab().toImage()
    dpr = img.devicePixelRatio() or 1.0
    for w in win.findChildren(QWidget):
        if not w.isVisible():
            continue
        if isinstance(w, QLabel) or hasattr(w, "text"):
            audit_overflow(w, key)
            audit_contrast(w, key, win, img, dpr)


def main() -> int:
    global KEEP
    win = MainWindow()
    KEEP["win"] = win          # 模块级持有：main() 返回时若引用归零会触发析构 SIGSEGV
    win.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
    win.show()
    app.processEvents()

    print("=" * 78)
    print("客户端文字审计（真实字体 / windows 平台）")
    print("=" * 78)
    # 1080x720 是 main_window.setMinimumSize() —— 用户能缩到的最小尺寸，
    # 窄窗口才是文字溢出的高发区，必须测进来。
    for size in ((1080, 720), (1280, 840), (1500, 950)):
        print(f"\n### 视口 {size[0]}x{size[1]}")
        for key in PAGE_KEYS:
            n0, n1 = len(OVERFLOW), len(CONTRAST)
            audit_page(win, key, size)
            added = (len(OVERFLOW) - n0, len(CONTRAST) - n1)
            flag = "" if added == (0, 0) else f"  ← 溢出 {added[0]} / 低对比 {added[1]}"
            print(f"  {key:<14}{flag}")

    hard = [x for x in OVERFLOW if x[5]]
    tight = [x for x in OVERFLOW if not x[5]]
    print("\n" + "=" * 78)
    print(f"【硬溢出 / 裁切】共 {len(hard)} 处")
    print("=" * 78)
    seen = set()
    for page, w, kind, detail, text, _ in hard:
        k = (page, text, kind)
        if k in seen:
            continue
        seen.add(k)
        print(f"  [{page}] {kind}\n      text = {text[:70]!r}\n      {detail}")
    print("\n" + "=" * 78)
    print(f"【余量过紧 <6px】共 {len(tight)} 处（去重）")
    print("=" * 78)
    seen = set()
    for page, w, kind, detail, text, _ in tight:
        k = (page, text)
        if k in seen:
            continue
        seen.add(k)
        print(f"  [{page}] {text[:44]!r}  {detail}")

    print("\n" + "=" * 78)
    print(f"【低对比 / 色混】共 {len(CONTRAST)} 处")
    print("=" * 78)
    seen = set()
    for page, w, r, need, bg, fg, text, en in sorted(CONTRAST, key=lambda x: x[2]):
        k = (page, text[:20], bg, fg)
        if k in seen:
            continue
        seen.add(k)
        tag = "正常" if en else "禁用"
        print(f"  [{page}|{tag}] {r:.2f}:1 (需{need})  底 #{bg[0]:02X}{bg[1]:02X}{bg[2]:02X} "
              f"字 #{fg[0]:02X}{fg[1]:02X}{fg[2]:02X}  {text[:38]!r}")

    # 不要关闭/释放主窗口 —— windows 平台下 MainWindow 析构会偶发 SIGSEGV
    # （exit 139），而 stdout 一旦被重定向到文件就是块缓冲，崩溃会把整份报告丢掉。
    # 直接 flush + os._exit 结束进程。
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(0)


if __name__ == "__main__":
    main()
