from types import SimpleNamespace

import pytest

from api.yuketang import standard_courses as standard
from api.yuketang.client import YuketangError
from gui_app import store, yuketang_engine


def payload(rows):
    return {'errcode': 0, 'data': {'list': rows}}


STUDENT = {'classroom_id': 11, 'name': '一班', 'course':
           {'id': 7, 'name': '学生课程', 'university_id': 88, 'platform': 3}}
TEACHER = {'id': 8, 'name': '教师课程', 'classrooms': [{'id': 12, 'name': '二班'}]}


def test_course_and_classroom_names_and_official_routes():
    student = standard.course_rows(payload([STUDENT]), 2)[0]
    teacher = standard.course_rows(payload([TEACHER]), 1)[0]
    assert student['course_name'] == '[我学的课] 学生课程 · 一班'
    assert '/v2/web/studentLog/11?' in student['portal_url']
    assert 'university_id=88' in student['portal_url']
    assert teacher['course_name'] == '[我教的课] 教师课程 · 二班'
    assert '/v2/web/teacherLog/12?' in teacher['portal_url']


@pytest.mark.parametrize('data', [None, {'errcode': 403},
    {'errcode': 0, 'data': {}}, payload([{'course': {'name': '缺失ID'}}])])
def test_errors_not_treated_as_empty_course_list(data):
    with pytest.raises(YuketangError):
        standard.course_rows(data, 2)


def test_reads_both_roles_after_confirming_current_account():
    calls = []
    class Driver:
        current_url = 'https://www.yuketang.cn/v2/web/index'
        def set_script_timeout(self, seconds): pass
        def execute_async_script(self, script, path):
            calls.append(path)
            if path.endswith('userinfo'):
                return {'errcode': 0, 'data': [{'user_id': 42}]}
            return payload([STUDENT] if path.endswith('2') else [TEACHER])
    rows = standard.list_courses('sessionid=fixture', '42', Driver())
    assert len(rows) == 2 and [row['identity'] for row in rows] == [2, 1]
    assert len(calls) == 3
    calls.clear()
    with pytest.raises(YuketangError, match='账号与当前档案不一致'):
        standard.list_courses('sessionid=fixture', 'other-user', Driver())
    assert len(calls) == 1


def test_runner_reads_standard_without_professional_client(tmp_path, monkeypatch):
    monkeypatch.setattr(store, 'app_dir', lambda: str(tmp_path))
    monkeypatch.setattr(yuketang_engine, '_STORE', None)
    book = yuketang_engine.account_store()
    book.update(book.active, login_mode='standard', cookie='sessionid=fixture', user_id='42')
    runner = yuketang_engine.YuketangRunner(book.active)
    monkeypatch.setattr(runner, '_spawn', lambda job, kind: job())
    expected = standard.course_rows(payload([STUDENT]), 2)
    monkeypatch.setattr(standard, 'list_courses', lambda *args: expected)
    received = []
    runner.sig_courses.connect(received.append)
    runner.load_courses()
    assert received == [expected] and runner.phase == 'loaded'
    contexts = []
    driver = SimpleNamespace(current_url='https://www.yuketang.cn/v2/web/index',
        get=lambda url: opened.append(url), execute_script=lambda script, value: contexts.append(value))
    opened = []
    runner._browser_driver = driver
    runner.open_course(expected[0])
    assert opened == [expected[0]['portal_url']]
    assert contexts[0] == {'university_id': '88', 'platform_id': '3', 'classroom_id': '11'}
    runner.open_course({'portal_url': 'https://other.example/studentLog/11'})
    assert len(opened) == 1


def test_header_badge_uses_current_platform_not_chaoxing():
    from gui_app.main_window import MainWindow
    states = []
    window = SimpleNamespace(_current_key='yuketang',
        yuketang_page=SimpleNamespace(_store=SimpleNamespace(accounts=[{'cookie': 'fixture'}])),
        badge_login=SimpleNamespace(set_kind=lambda kind, text: states.append((kind, text))))
    MainWindow._refresh_platform_login_badge(window)
    assert states == [('ok', '1/1 个账号已登录')]
