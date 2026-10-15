# -*- coding: utf-8 -*-
"""启动闸门 + 心跳定时器的编排层（可被测试跳过）。"""
from __future__ import annotations

import datetime as _dt
import threading
import sys

from PySide6.QtWidgets import QDialog


def update_remaining_title(win, hb: dict | None) -> None:
    """按心跳里的 expires_at 把主窗口标题升级为「乐课 · 剩余 N 天」。"""
    try:
        exp = (hb or {}).get("expires_at")
        if not exp:
            return
        from api import kc_client

        dt = kc_client._parse_iso(exp)
        if dt is None:
            return
        left = max(0, -(- (dt - _dt.datetime.now(_dt.timezone.utc)).total_seconds() // 86400))
        win.setWindowTitle(f"乐课 · 剩余 {int(left)} 天")
    except Exception:
        pass


def run_access_gate(version: str = "") -> dict | None:
    """阻塞式启动闸门。

    返回 access 信息 dict（含 mode / hb）；用户关闭对话框 → None（调用方退出程序）。
    测试自检模式（GUIAUTO_SHOT_DIR / KC_SKIP_GATE=1）直接放行，不弹窗。
    """
    import os

    if not getattr(sys, 'frozen', False) and (
            os.environ.get("GUIAUTO_SHOT_DIR") or os.environ.get("KC_SKIP_GATE") == "1"):
        return {"mode": "skipped", "hb": None}

    from PySide6.QtWidgets import QMessageBox

    from api import kc_client
    from gui_app.login_dialog import AccessGateDialog

    dlg = AccessGateDialog(version=version)
    if dlg.exec() != QDialog.DialogCode.Accepted or not dlg.access_info:
        return None

    hb = dlg.access_info.get("hb") or {}
    _notify_announcements(hb)
    _warn_version(hb, version)
    return dlg.access_info


def _notify_announcements(hb: dict) -> None:
    """有生效公告就记日志；critical 级别弹一次窗。"""
    anns = hb.get("announcements") or []
    if not anns:
        return
    try:
        from api.logger import logger

        for a in anns:
            logger.info("[公告·{}] {}", a.get("level", "info"), a.get("title", ""))
    except Exception:
        pass
    critical = [a for a in anns if a.get("level") == "critical"]
    if critical:
        try:
            from PySide6.QtWidgets import QMessageBox

            a = critical[0]
            QMessageBox.warning(
                None, "重要公告", f"{a.get('title', '')}\n\n{a.get('body', '')}"
            )
        except Exception:
            pass


def _warn_version(hb: dict, version: str) -> None:
    warning = hb.get("_version_warning")
    if warning:
        try:
            from api.logger import logger

            logger.warning(warning)
        except Exception:
            pass


def install_heartbeat(win, interval_min: int, version: str = "") -> None:
    """后台请求通过 Qt 信号回到主线程，避免无事件循环的工作线程吞掉回调。"""
    from PySide6.QtCore import QObject, QTimer, Signal, Slot, Qt
    from api import kc_client

    closing = threading.Event()
    in_flight = threading.Event()

    def on_result(result):
        if closing.is_set():
            return
        if result.get("mode") in ("online", "grace"):
            _notify_announcements(result.get("hb") or {})
            update_remaining_title(win, result.get("hb"))
            _warn_version(result.get("hb") or {}, version)
            return
        timer.stop()
        from gui_app.login_dialog import AccessGateDialog
        dlg = AccessGateDialog(version=version, parent=win)
        if dlg.exec() == QDialog.DialogCode.Accepted and dlg.access_info:
            hb = dlg.access_info.get("hb") or {}
            _notify_announcements(hb)
            update_remaining_title(win, hb)
            timer.start()
        else:
            win.close()

    class HeartbeatRelay(QObject):
        finished = Signal(dict)

        @Slot(dict)
        def deliver(self, result):
            in_flight.clear()
            on_result(result)

    relay = HeartbeatRelay(win)
    relay.finished.connect(relay.deliver, Qt.ConnectionType.QueuedConnection)

    def tick():
        if closing.is_set() or in_flight.is_set():
            return
        in_flight.set()
        def worker():
            try:
                result = kc_client.check_access(version=version)
                kc_client.flush_events()
                if not closing.is_set():
                    relay.finished.emit(result)
            except Exception:
                in_flight.clear()
        threading.Thread(target=worker, daemon=True).start()

    timer = QTimer(win)
    timer.setObjectName("authorizationHeartbeat")
    timer.setInterval(max(5, interval_min) * 60 * 1000)
    timer.timeout.connect(tick)
    timer.start()

    usage_timer = QTimer(win)
    usage_timer.setInterval(60_000)
    def flush_usage_async():
        from api.token_usage import flush_usage
        threading.Thread(target=flush_usage, daemon=True).start()
    usage_timer.timeout.connect(flush_usage_async)
    usage_timer.start()
    orig_close = win.closeEvent

    def close_event(event):
        orig_close(event)
        if not event.isAccepted():
            return
        closing.set()
        timer.stop()
        usage_timer.stop()
        def final_flush():
            from api.token_usage import flush_usage
            flush_usage()
            kc_client.flush_events()
        threading.Thread(target=final_flush, daemon=True).start()

    win.closeEvent = close_event
