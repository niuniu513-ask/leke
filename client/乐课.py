# -*- coding: utf-8 -*-
"""乐课 · 在线课程学习助手 —— 源码运行入口。

双击本文件、或运行「启动乐课.bat」即可。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from gui_app.app import main  # noqa: E402

if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        # GUI 输出流尚未接入时也要保留启动失败原因。
        import traceback
        from pathlib import Path
        root = Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).parent
        (root / "startup_error.log").write_text(traceback.format_exc(), encoding="utf-8")
        raise
