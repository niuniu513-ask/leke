import os
import threading

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from gui_app import store, yuketang_engine as engine
from api.yuketang import standard_courses


def make_runner(tmp_path, monkeypatch):
    monkeypatch.setattr(store, 'app_dir', lambda: str(tmp_path))
    monkeypatch.setattr(engine, '_STORE', None)
    monkeypatch.setattr(engine, '_runners', {})
    book = engine.account_store()
    book.update(book.active, login_mode='standard', cookie='fixture', user_id='42')
    return engine.YuketangRunner(book.active)


def test_browser_queue_next_stop_and_no_false_completion(tmp_path, monkeypatch):
    runner = make_runner(tmp_path, monkeypatch)
    runner._browser_driver = object()
    courses = [dict(identity=2, classroom_id=str(i), course_name='课程' + str(i)) for i in range(2)]
    monkeypatch.setattr(standard_courses, 'list_courses', lambda *a: courses)
    entered = [threading.Event(), threading.Event()]
    opened = []
    def open_course(c):
        opened.append(c)
        entered[len(opened) - 1].set()
    monkeypatch.setattr(runner, '_open_course_browser', open_course)
    runner.start(courses)
    assert entered[0].wait(2)
    assert runner.phase == 'running' and len(opened) == 1
    runner.next_course()
    assert entered[1].wait(2)
    runner.stop()
    runner._thread.join(2)
    assert not runner.busy() and runner.phase == 'ready'
    assert opened == courses


def test_standard_answer_never_calls_professional_client(tmp_path, monkeypatch):
    runner = make_runner(tmp_path, monkeypatch)
    monkeypatch.setattr(runner, '_ensure_client', lambda: (_ for _ in ()).throw(AssertionError('wrong client')))
    assert runner.answer([{'identity': 2}]) is False


def test_narrow_course_names_selection_and_start(tmp_path, monkeypatch):
    from PySide6.QtWidgets import QApplication, QLabel
    from gui_app.page_yuketang import YuketangPage
    app = QApplication.instance() or QApplication([])
    runner = make_runner(tmp_path, monkeypatch)
    page = YuketangPage()
    courses = [dict(identity=2, classroom_id='1', course_name='机器学习及嵌入式系统' * 12),
               dict(identity=2, classroom_id='2', course_name='信号处理')]
    page.fill_courses(courses, page._current)
    page.set_state('loaded', page._current)
    page.resize(720, 800)
    page.show()
    app.processEvents()
    assert page.btn_start.isEnabled() and page.browser.isHidden()
    page._boxes[1].setChecked(False)
    page._rebuild_courses()
    assert page._selected() == courses[:1]
    host = page.courses_box.itemAt(0).widget()
    label = host.findChild(QLabel)
    assert label.wordWrap() and label.width() <= page.width()
    page.ed_search.setText('信号处理')
    assert host.isHidden() and not page.courses_box.itemAt(1).widget().isHidden()
    page.cb_task.setCurrentIndex(1)
    assert not page.btn_start.isEnabled()
    page.close()
