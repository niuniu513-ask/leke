# -*- coding: utf-8 -*-
"""验证启动顺序与日志全链路。

这一步最容易翻车：api/logger.py 在导入时就把当时的 sys.stderr 存成了模块级常量
（tqdm_stream），如果接管输出流的动作晚于这个导入，日志就会全部丢失或直接抛异常。
"""
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # tests/regression -> 项目根
sys.path.insert(0, ROOT)

ok = []
bad = []


def check(name, cond, extra=""):
    (ok if cond else bad).append(name)
    print(f"  [{'PASS' if cond else 'FAIL'}] {name} {extra}")


def main():
    # ---------- 按 app.py 的真实顺序启动 ----------
    from gui_app import stream

    stream.install()                      # ① 先接管输出流

    from gui_app.store import ensure_config
    os.chdir(ROOT)
    ensure_config()

    from PySide6.QtWidgets import QApplication

    from gui_app import theme as T
    from gui_app.main_window import MainWindow   # ② 这里才第一次导入 api.logger

    app = QApplication([])
    app.setStyle("Fusion")
    app.setStyleSheet(T.build_qss(os.path.join(ROOT, "assets")))

    win = MainWindow()
    stream.set_handlers(win.on_stream_line, win.on_stream_activity)

    print("\n" + "=" * 62)
    print("启动顺序 / 日志链路验证")
    print("=" * 62)

    # ---------- 1) api.logger 是否绑定了我们的代理 ----------
    import api.logger as apilog
    check("api.logger.tqdm_stream 已被接管",
          hasattr(apilog.tqdm_stream, "on_line"),
          f"-> {type(apilog.tqdm_stream).__name__}")

    check("sys.stderr 已被接管", hasattr(sys.stderr, "on_line"),
          f"-> {type(sys.stderr).__name__}")
    check("sys.stdout 已被接管", hasattr(sys.stdout, "on_line"),
          f"-> {type(sys.stdout).__name__}")

    # ---------- 2) loguru 各级别能否到达日志区 ----------
    from api.logger import logger

    win.run_page.clear_log()
    win.run_page.cb_verbose.setChecked(True)   # DEBUG 也要显示

    logger.info("链路测试-INFO")
    logger.warning("链路测试-WARNING")
    logger.error("链路测试-ERROR")
    logger.debug("链路测试-DEBUG")

    time.sleep(0.8)          # loguru enqueue 是异步的
    app.processEvents()
    time.sleep(0.4)
    app.processEvents()

    text = win.run_page.log.toPlainText()
    for lvl in ("INFO", "WARNING", "ERROR", "DEBUG"):
        check(f"loguru {lvl} 到达日志区", f"链路测试-{lvl}" in text)

    # ---------- 3) print / tqdm 路径 ----------
    print("链路测试-print语句")
    time.sleep(0.3)
    app.processEvents()
    text = win.run_page.log.toPlainText()
    check("print 输出到达日志区", "链路测试-print语句" in text)

    # tqdm 进度条应该走 on_progress，进状态栏而不是日志区
    from tqdm import tqdm
    for _ in tqdm(range(3), desc="链路测试进度", ncols=40):
        pass
    time.sleep(0.3)
    app.processEvents()

    # ---------- 4) 日志级别过滤 ----------
    win.run_page.cb_verbose.setChecked(False)
    win.run_page.clear_log()
    logger.debug("链路测试-DEBUG-应被过滤")
    logger.info("链路测试-INFO-应显示")
    time.sleep(0.8)
    app.processEvents()
    time.sleep(0.3)
    app.processEvents()
    text = win.run_page.log.toPlainText()
    check("关闭详细日志后 DEBUG 被过滤", "应被过滤" not in text)
    check("关闭详细日志后 INFO 仍显示", "应显示" in text)

    # ---------- 5) 日志颜色 ----------
    from gui_app import theme as TT
    check("ERROR 有独立配色", TT.LOG_COLORS["ERROR"] != TT.LOG_COLORS["INFO"],
          f"{TT.LOG_COLORS['ERROR']} vs {TT.LOG_COLORS['INFO']}")

    # ---------- 6) 还原，避免影响后续输出 ----------
    stream.uninstall()
    sys.stdout = sys.__stdout__
    sys.stderr = sys.__stderr__

    print("\n" + "=" * 62)
    print(f"结果：{len(ok)} 通过 / {len(bad)} 失败")
    if bad:
        print("失败项：", bad)
    print("=" * 62)
    return 1 if bad else 0


if __name__ == "__main__":
    code = main()
    sys.exit(code)
