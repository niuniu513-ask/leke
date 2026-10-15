"""Read-only progress checks; no live account or learning operations."""
from types import SimpleNamespace as NS
import threading
import time
import pytest
from PySide6.QtWidgets import QApplication
from api.decode import decode_course_point
from gui_app.worker import Runner
from gui_app.page_courses import CoursesPage


def test_unrecognized_page_is_not_completed_course():
    for html in ('<html>请登录</html>', '<html>系统繁忙</html>', ''):
        with pytest.raises(ValueError):
            decode_course_point(html)


def test_chapter_parser_handles_auxiliary_list_items():
    data = decode_course_point('''<div class="chapter_unit"><ul>
        <li>章节标题</li><li><div id="other">说明</div></li>
        <li><div id="cur123"><a class="clicktitle">视频章节</a>
        <input class="knowledgeJobCount" value="2"><span class="bntHoverTips">已完成</span>
        </div></li></ul></div>''')
    assert len(data['points']) == 1
    assert data['points'][0]['has_finished'] is True
    assert data['points'][0]['jobCount'] == '2'


def test_final_check_counts_server_status_and_selected_scope():
    points = [dict(id='1', has_finished=True), dict(id='2', has_finished=False)]
    st = NS(chaoxing=NS(get_course_point=lambda *args: {'points': points}))
    course = dict(courseId='c', clazzId='b')
    result = Runner().verify_course_completion(st, [course])
    assert result == dict(done=1, total=2, remaining=1, errors=0)
    assert Runner().verify_course_completion(st, [dict(course, selected_points=['1'])])['remaining'] == 0
    assert Runner().verify_course_completion(st, [dict(course, selected_points=['missing'])])['errors'] == 1
    st.chaoxing.get_course_point = lambda *args: None
    assert Runner().verify_course_completion(st, [course])['errors'] == 1


def test_refresh_invalidates_old_dialog_and_restores_cached_progress(monkeypatch):
    from gui_app import store
    monkeypatch.setattr(store, 'ui_load', lambda: {})
    app = QApplication.instance() or QApplication([])
    page = CoursesPage()
    course = dict(courseId='c', clazzId='b', title='课程',
                  _progress=dict(done=1, total=2, error=None))
    page._chapter_cache[('a', 'c')] = [{'id': 'stale'}]
    page._pending_dialog = ('a', course)
    page.fill([course])
    assert not page._chapter_cache and page._pending_dialog is None
    assert page.progress[0]['done'] == 1
    page.on_chapters('a', 'c', [{'id': 'late'}], '')
    assert not page._chapter_cache
    page._pending_dialog = ('a', course)
    page._chapter_cache[('a', 'c')] = [{'id': 'stale'}]
    page.on_chapters('a', 'c', [], 'network error')
    assert ('a', 'c') not in page._chapter_cache
    page.fill([dict(course, _progress=dict(done=0, total=0, error='offline'))])
    assert '进度未确认' in page.lb_sel.text()
    page.deleteLater()
    app.processEvents()


def test_duplicate_course_reads_do_not_race_and_progress_has_identity():
    app = QApplication.instance() or QApplication([])
    entered, release = threading.Event(), threading.Event()
    calls, progress = [], []
    course = dict(courseId='c', clazzId='b', title='课程')
    def read_courses():
        calls.append(1)
        entered.set()
        assert release.wait(2)
        return [course]
    runner = Runner()
    st = runner.session('test-only')
    st.chaoxing = NS(get_course_list=read_courses,
                    get_course_point=lambda *args: {'points': [{'has_finished': True}]})
    runner.sig_course_progress.connect(lambda *args: progress.append(args))
    try:
        runner.load_courses('test-only')
        assert entered.wait(1)
        runner.load_courses('test-only')
        release.set()
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            app.processEvents()
            if progress and not runner._course_reads:
                break
            time.sleep(.005)
        assert len(calls) == len(progress) == 1
        assert progress[0][2]['course_id'] == 'c'
        assert progress[0][2]['clazz_id'] == 'b'
    finally:
        release.set()


def test_progress_is_bound_to_course_identity_and_account():
    from gui_app.main_window import MainWindow
    painted = []
    window = NS(courses_cache={'a': [dict(courseId='c', clazzId='b')]},
                courses_page=NS(current_account=lambda: 'other',
                                update_progress=lambda *args: painted.append(args)))
    MainWindow._on_course_progress(window, 'a', 0,
                                  dict(course_id='old', clazz_id='b', done=9, total=9))
    assert '_progress' not in window.courses_cache['a'][0]
    MainWindow._on_course_progress(window, 'a', 0,
                                  dict(course_id='c', clazz_id='b', done=1, total=2))
    assert window.courses_cache['a'][0]['_progress']['done'] == 1
    assert not painted


def test_background_account_does_not_reset_active_loading_state():
    from gui_app.main_window import MainWindow
    calls = []
    window = NS(courses_page=NS(current_account=lambda: 'active',
                               set_loading=lambda value: calls.append(value)),
                lb_activity=NS(setText=lambda value: calls.append(value)))
    MainWindow._on_finished(window, 'background', 'courses', 'ok', '')
    assert not calls


def test_same_course_old_reply_cannot_replace_new_request(monkeypatch):
    from gui_app import store
    monkeypatch.setattr(store, 'ui_load', lambda: {})
    app = QApplication.instance() or QApplication([])
    page = CoursesPage()
    monkeypatch.setattr(page, 'current_account', lambda: 'a')
    shown = []
    monkeypatch.setattr(page, '_show_chapter_dialog', lambda course, points: shown.append(points))
    course = dict(courseId='c', clazzId='b', title='课程')
    page.fill([course])
    page._open_chapters(course)
    old = page._pending_dialog[1]['_chapter_request']
    page.fill([course])
    page._open_chapters(course)
    new = page._pending_dialog[1]['_chapter_request']
    assert old != new
    page.on_chapters_versioned('a', 'c', [{'id': 'old'}], '', old)
    assert not shown and not page._chapter_cache
    page.on_chapters_versioned('a', 'c', [{'id': 'new'}], '', new)
    assert shown == [[{'id': 'new'}]]
    page.deleteLater()
    app.processEvents()


def test_post_run_refresh_is_not_lost_when_old_read_is_running():
    app = QApplication.instance() or QApplication([])
    entered, release = threading.Event(), threading.Event()
    calls, progress = [], []
    course = dict(courseId='c', clazzId='b')
    def read():
        calls.append(1)
        entered.set()
        assert release.wait(2)
        return [course]
    runner = Runner()
    runner.session('a').chaoxing = NS(get_course_list=read,
        get_course_point=lambda *args: {'points': [{'has_finished': len(calls) > 1}]})
    runner.sig_course_progress.connect(lambda *args: progress.append(args[2]['done']))
    try:
        runner.load_courses('a')
        assert entered.wait(1)
        runner.load_courses('a', force_refresh=True)
        release.set()
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            app.processEvents()
            if len(progress) == 2 and not runner._course_reads:
                break
            time.sleep(.005)
        assert len(calls) == 2 and progress == [0, 1]
    finally:
        release.set()
