"""Closing a window must keep its running Qt workers alive until they finish."""
from types import SimpleNamespace
from PySide6.QtCore import QThread, QTimer
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import QApplication, QWidget
from gui_app.main_window import MainWindow


def test_close_waits_for_live_thread(monkeypatch):
    import gui_app.main_window as main
    monkeypatch.setattr(main.store, 'ui_save', lambda value: None)
    app = QApplication.instance() or QApplication([])
    class Window(QWidget):
        closeEvent = MainWindow.closeEvent
        def status(self, text):
            self.message = text
    win = Window()
    win.live_page = QWidget(win)
    win.live_page._watch = QTimer(win.live_page)
    win.live_page._watch.start(1000)
    win.runner = SimpleNamespace(studying=False)
    for name in ('unipus_page', 'yuketang_page', 'zhihuishu_page'):
        setattr(win, name, SimpleNamespace(shutdown=lambda: None))
    # Do not touch process-global platform runners.
    for module_name in ('unipus_engine', 'yuketang_engine', 'zhihuishu_engine'):
        module = __import__('gui_app.' + module_name, fromlist=['shutdown_all'])
        monkeypatch.setattr(module, 'shutdown_all', lambda: None)
    class Worker(QThread):
        def run(self):
            self.msleep(100)
    task = Worker(win.live_page)
    task.start()
    event = QCloseEvent()
    win.closeEvent(event)
    assert not event.isAccepted()
    assert not win.live_page._watch.isActive()
    assert win._close_retry.isActive()
    assert task.isInterruptionRequested()
    assert task.wait(1000)
    win._close_retry.stop()
    event = QCloseEvent()
    win.closeEvent(event)
    assert event.isAccepted()
    win.deleteLater()
    app.processEvents()
