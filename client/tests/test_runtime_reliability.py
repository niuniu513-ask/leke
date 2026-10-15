import os
import threading
import time
from types import SimpleNamespace

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication, QWidget
from api import kc_client, diagnostics
from gui_app import gate, study_engine
from main import ChapterResult, ChapterTask


def test_scheduler_waits_before_retry_and_records_unopened_failure(monkeypatch):
    times = []
    def process(*args, **kwargs):
        times.append(time.monotonic())
        return ChapterResult.NOT_OPEN
    monkeypatch.setattr(study_engine, '_load_engine', lambda: (process, ChapterResult))
    task = ChapterTask(index=0, course={'title': '课'}, point={'title': '章节'})
    scheduler = study_engine.StudyScheduler(SimpleNamespace(), [task], {'jobs': 1, 'retry_interval': 0.12})
    scheduler.max_tries = 3
    assert scheduler.run() == 1
    assert len(times) == 3 and all(b - a >= 0.11 for a, b in zip(times, times[1:]))
    assert scheduler.failed == [task] and scheduler.success_count == 0


def test_heartbeat_result_reaches_gui_thread_and_no_overlapping_calls(monkeypatch):
    app = QApplication.instance() or QApplication([])
    window = QWidget()
    delivered = []
    calls = []
    entered, release = threading.Event(), threading.Event()
    def request(**kwargs):
        calls.append(threading.get_ident())
        entered.set()
        release.wait(2)
        return {'mode': 'online', 'hb': {}}
    monkeypatch.setattr(kc_client, 'check_access', request)
    monkeypatch.setattr(kc_client, 'flush_events', lambda: 0)
    monkeypatch.setattr(gate, 'update_remaining_title', lambda win, hb: delivered.append(threading.get_ident()))
    gate.install_heartbeat(window, 5)
    timer = window.findChild(QTimer, 'authorizationHeartbeat')
    try:
        timer.timeout.emit()
        assert entered.wait(1)
        timer.timeout.emit()
        release.set()
        deadline = time.monotonic() + 2
        while not delivered and time.monotonic() < deadline:
            app.processEvents()
            time.sleep(0.005)
        assert delivered == [threading.get_ident()]
        assert len(calls) == 1 and calls[0] != delivered[0]
    finally:
        release.set()
        window.close()


def test_diagnostics_classifies_auth_and_network_without_exposing_secrets(monkeypatch):
    class Response:
        status_code = 200
        def raise_for_status(self): pass
        def json(self): return {'status': 'ok'}
    monkeypatch.setattr(diagnostics._net, 'send', lambda *a, **kw: Response())
    monkeypatch.setattr(kc_client, 'load_state', lambda: {})
    def login_required(): raise kc_client.KcAuthError('secret-token')
    monkeypatch.setattr(kc_client, 'knowledge_stats', login_required)
    rows = diagnostics.run_diagnostics()
    assert len(rows) == 7
    assert rows[0]['status'] == '通过'
    assert rows[1]['status'] == rows[2]['status'] == '待登录'
    assert 'secret-token' not in str(rows)


def test_cli_retry_waits_and_shuts_down_all_queues(monkeypatch):
    import main
    times = []
    def process(*args, **kwargs):
        times.append(time.monotonic())
        return ChapterResult.NOT_OPEN
    monkeypatch.setattr(main, 'process_chapter', process)
    task = ChapterTask(index=0, course={'title': '课'}, point={'title': '章节'})
    runner = main.JobProcessor(None, [task], {'jobs': 1, 'speed': 1,
                                            'notopen_action': 'retry', 'retry_interval': 0.12})
    runner.max_tries = 3
    runner.run()
    assert len(times) == 3 and all(b - a >= 0.11 for a, b in zip(times, times[1:]))
    assert runner.failed_tasks == [task]
    assert runner.task_queue.unfinished_tasks == runner.retry_queue.unfinished_tasks == 0
    assert all(not thread.is_alive() for thread in runner.threads)
