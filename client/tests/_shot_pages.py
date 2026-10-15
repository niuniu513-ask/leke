# -*- coding: utf-8 -*-
"""按页截图（真实字体、不弹窗），用于肉眼核对文字排版与配色。
跑法：cd chaoxing-main; .venv/Scripts/python.exe tests/_shot_pages.py [outdir] [keys...]
"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "windows")

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

app = QApplication(sys.argv)
from gui_app import theme as T               # noqa: E402
from gui_app.main_window import (MainWindow,  # noqa: E402
                                 PAGE_KEYS)

app.setStyleSheet(T.QSS)

outdir = sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, "..", "kc_server",
                                                            "preview", "text_audit")
os.makedirs(outdir, exist_ok=True)
keys = sys.argv[2:] or list(PAGE_KEYS)

win = MainWindow()
win.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
win.resize(1280, 840)
win.show()
app.processEvents()

for k in keys:
    win.goto(k)
    for _ in range(4):
        app.processEvents()
    p = os.path.join(outdir, f"{k}.png")
    ok = win.grab().save(p)
    print(f"  {k:<12} -> {p}  {'OK' if ok else 'FAIL'}")

sys.stdout.flush()
os._exit(0)
