import os
from types import SimpleNamespace

import pytest

from api.yuketang import browser_login as rain
from gui_app import store, yuketang_engine


@pytest.mark.parametrize('payload,valid', [
    ({'errcode': 0, 'data': [{'user_id': 42, 'name': 'Fixture'}]}, True),
    ({'errcode': 1, 'data': [{'user_id': 42}]}, False),
    ({'errcode': 0, 'data': []}, False),
    ({'errcode': 0, 'data': [{'name': 'Guest'}]}, False),
    ({'errcode': 0, 'data': {'user_id': 42}}, False),
])
def test_standard_identity_requires_server_confirmation(payload, valid):
    class Driver:
        current_url = 'https://www.yuketang.cn/v2/web/index'
        def set_script_timeout(self, seconds): pass
        def execute_async_script(self, script):
            assert '/v2/api/web/userinfo' in script
            return payload
        def get_cookies(self): return [{'name': 'sessionid', 'value': 'fixture'}]
    result = rain.standard_session(Driver())
    assert bool(result) == valid
    if valid:
        assert result['login_mode'] == 'standard'
        assert result['user_id'] == '42'


@pytest.mark.parametrize('school_rejected', [False, True])
def test_owned_browser_retained_after_verified_login(tmp_path, monkeypatch, school_rejected):
    class Driver:
        current_url = 'https://www.yuketang.cn/v2/web/index'
        window_handles = ['live']
        closed = False
        service = SimpleNamespace(process=SimpleNamespace(pid=123))
        def __init__(self): self.switch_to = self
        def window(self, handle): pass
        def set_page_load_timeout(self, timeout): pass
        def get(self, url): pass
        def quit(self): self.closed = True
    driver = Driver()
    monkeypatch.setattr(rain, 'school_session', lambda d: None)
    def unified(driver):
        if school_rejected:
            raise rain.YuketangError('学校未绑定')
        return None
    monkeypatch.setattr(rain, 'unified_session', unified)
    monkeypatch.setattr(rain, 'standard_session', lambda d: {'domain': 'www.yuketang.cn'})
    owned, ready = [], []
    rain.login_by_browser(lambda: False, lambda text: None,
        driver_factory=lambda profile: driver, profile_dir=str(tmp_path),
        on_browser=owned.append, on_ready=ready.append)
    assert owned == [123] and ready == [driver]
    assert not driver.closed


def test_runner_marks_standard_login_and_releases_browser(tmp_path, monkeypatch):
    monkeypatch.setattr(store, 'app_dir', lambda: str(tmp_path))
    monkeypatch.setattr(yuketang_engine, '_STORE', None)
    book = yuketang_engine.account_store()
    runner = yuketang_engine.YuketangRunner(book.active)
    monkeypatch.setattr(runner, '_spawn', lambda job, kind: job())
    driver = SimpleNamespace(quit=lambda: closed.append(True))
    closed = []
    def login(stop, step, **kwargs):
        kwargs['on_ready'](driver)
        return {'login_mode': 'standard', 'domain': 'www.yuketang.cn',
                'cookie': 'sessionid=fixture', 'user_id': '42', 'realname': 'Fixture'}
    monkeypatch.setattr(rain, 'login_by_browser', login)
    runner.login_auto()
    assert runner.phase == 'ready'
    assert runner.account_info()['logged_in']
    assert runner.account_info()['login_mode'] == 'standard'
    with pytest.raises(RuntimeError, match='普通版官网已登录'):
        runner._make_client()
    runner.shutdown()
    assert closed == [True]


def test_both_pages_can_hide_browser_without_disconnect(tmp_path, monkeypatch):
    os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
    from PySide6.QtWidgets import QApplication
    from gui_app import unipus_engine
    from gui_app.page_yuketang import YuketangPage
    from gui_app.page_unipus import UnipusPage
    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(store, 'app_dir', lambda: str(tmp_path))
    monkeypatch.setattr(yuketang_engine, '_STORE', None)
    monkeypatch.setattr(yuketang_engine, '_runners', {})
    monkeypatch.setattr(unipus_engine, '_store', None)
    pages = [YuketangPage(), UnipusPage()]
    for page in pages:
        page.browser.set_browser_visible(True)
        page.show()
        app.processEvents()
        session = page.browser.currentWidget()
        session.root_pid = 123
        page._toggle_browser_visible()
        assert page.browser.isHidden()
        assert session.root_pid == 123
        assert page.btn_browser_visible.text() == '显示浏览器'
        page._toggle_browser_visible()
        assert not page.browser.isHidden()
        session.root_pid = 0
        page.shutdown()
        page.close()
