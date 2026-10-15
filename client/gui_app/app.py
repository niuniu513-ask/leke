# -*- coding: utf-8 -*-
"""桌面版入口。

启动顺序很讲究：
1. 先接管 stdout/stderr —— api/logger.py 在导入时就把当时的 sys.stderr 存成了模块常量，
   一旦那个值是 None（pythonw / 打包后），所有日志写入都会抛异常；
2. 再切工作目录到程序目录 —— api/base.py 用相对路径读写 cookies.txt / 日志文件；
3. 最后才 import 界面与业务模块。
"""
from __future__ import annotations

import os
import sys


def project_root() -> str:
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def main() -> int:
    root = project_root()
    if root not in sys.path:
        sys.path.insert(0, root)

    # 1) 接管输出流 —— 必须早于任何 api.* 的导入
    from gui_app import stream
    stream.install()

    # 2) 工作目录与配置文件
    from gui_app.store import ensure_config
    os.chdir(root)
    ensure_config()

    # 3) 界面
    from PySide6.QtWidgets import QApplication

    from gui_app import APP_NAME
    from gui_app import __version__ as APP_VERSION
    from gui_app import theme as T
    from gui_app.main_window import MainWindow

    # 启动留痕（v1.5.6）：以前一次"只是点点页面"的会话在 leke.log 里**一行都没有**，
    # 出了问题无法判断"用户到底跑的是哪个版本、有没有真的点过那个按钮"。
    # 这行让每次启动都可核对（版本 + 数据目录 + 时间）。
    try:
        from api.logger import logger
        logger.info("{} v{} 启动　·　数据目录 {}", APP_NAME, APP_VERSION, root)
    except Exception:
        pass

    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setApplicationDisplayName(APP_NAME)
    app.setStyle("Fusion")
    # 全局窗口图标：所有窗口 / 消息框 / 任务栏都用品牌图标
    # （打包后靠 PyInstaller 写入 exe 资源，这里保证运行期一致，独立源码运行也有图标）
    try:
        from gui_app.brand import app_icon
        app.setWindowIcon(app_icon())
    except Exception:
        pass
    # 勾选图标是生成出来的，必须等 QApplication 存在才能画
    app.setStyleSheet(T.build_qss(os.path.join(root, "assets")))

    def _hook(exc_type, exc, tb):
        import traceback
        detail = "".join(traceback.format_exception(exc_type, exc, tb))
        try:
            stream._dispatch("line", f"ERROR | 未捕获异常: {exc}\n{detail}")
        except Exception:
            pass

    sys.excepthook = _hook

    # 4) 授权闸门：未登录 / 被封禁 / 宽限期已过 → 完全锁死，不进主界面。
    #    测试自检（GUIAUTO_SHOT_DIR）与 KC_SKIP_GATE=1 直接放行。
    from gui_app.gate import install_heartbeat, run_access_gate, update_remaining_title

    access = run_access_gate(version=APP_VERSION)
    if access is None:
        return 0
    try:
        from api import kc_client
        from api.logger import logger

        kc_client.track("app", "launch", extra={"mode": access.get("mode", "")})
        logger.info("授权闸门通过（mode={})", access.get("mode", ""))
    except Exception:
        pass

    win = MainWindow()
    update_remaining_title(win, access.get("hb"))  # 标题栏显示剩余天数
    stream.set_handlers(win.on_stream_line, win.on_stream_activity)
    win.show()
    if access.get('mode') != 'skipped':
        # 首次登录自动导入旧本地题库；失败不会记迁移完成标记，可在设置页重试。
        import threading
        def migrate_knowledge():
            try:
                from api.answer import get_cache_dao
                count = get_cache_dao().migrate_legacy()
                if count:
                    logger.info(f'旧本地题库已导入云端：{count} 条')
            except Exception as exc:
                logger.warning(f'旧题库导入未完成，可在参数设置重试：{exc}')
        threading.Thread(target=migrate_knowledge, daemon=True).start()

    # 心跳定时器：服务端建议间隔，掉线自动弹回登录框
    try:
        hb_min = int((access.get("hb") or {}).get("heartbeat_interval_min", 30))
    except (TypeError, ValueError):
        hb_min = 30
    install_heartbeat(win, hb_min, version=APP_VERSION)

    # 自检钩子：设置 GUIAUTO_SHOT_DIR 后，启动完自动逐页截图并退出。
    # 用于在没有人工干预的情况下验证「真实启动路径」是否可用。
    shot_dir = os.environ.get("GUIAUTO_SHOT_DIR") if not getattr(sys, 'frozen', False) else None
    if shot_dir:
        from PySide6.QtCore import QTimer

        def _auto_shot():
            os.makedirs(shot_dir, exist_ok=True)
            # 页面清单从 NAV 单一数据源派生，插页/换序都不会再指错
            from gui_app.main_window import PAGE_KEYS

            for i, key in enumerate(PAGE_KEYS):
                win.goto(key)
                app.processEvents()
                win.grab().save(os.path.join(shot_dir, f"launch_{i}_{key}.png"))
            win.goto('unipus')
            for index, name in ((1, 'account'), (2, 'log')):
                win.unipus_page.inspector.setCurrentIndex(index)
                app.processEvents()
                win.grab().save(os.path.join(shot_dir, 'unipus_' + name + '.png'))
            win.unipus_page.inspector.setCurrentIndex(0)
            before = win.size()
            win.resize(1080, 720)
            app.processEvents()
            win.grab().save(os.path.join(shot_dir, 'unipus_compact.png'))
            win.resize(before)
            # 自检还覆盖设置页的子页与异步连接检查，不接触真实课程。
            settings = win.findChild(__import__('gui_app.page_settings', fromlist=['SettingsPage']).SettingsPage)
            if settings is None:
                app.exit(2)
                return
            win.goto('settings')
            settings.seg.set_index(1)
            app.processEvents()
            win.grab().save(os.path.join(shot_dir, 'settings_knowledge.png'))
            settings.seg.set_index(2)
            settings.btn_diagnostics.click()
            from time import monotonic
            deadline = monotonic() + 35
            def finish_diagnostics():
                if not settings.btn_diagnostics.isEnabled() and monotonic() < deadline:
                    QTimer.singleShot(100, finish_diagnostics)
                    return
                import json
                app.processEvents()
                win.grab().save(os.path.join(shot_dir, 'settings_diagnostics.png'))
                with open(os.path.join(shot_dir, 'diagnostics.json'), 'w', encoding='utf-8') as fp:
                    json.dump({'completed': settings.btn_diagnostics.isEnabled(),
                               'text': settings.lb_diagnostics.text()}, fp, ensure_ascii=False, indent=2)
                app.exit(0 if settings.btn_diagnostics.isEnabled() else 3)
            QTimer.singleShot(100, finish_diagnostics)

        QTimer.singleShot(1500, _auto_shot)

    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
