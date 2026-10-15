"""School selection must not reuse another university's identity or courses."""
import shutil
from pathlib import Path

import pytest

from api.yuketang.client import _normalize_domain, YuketangClient, YuketangError
from gui_app import store, yuketang_engine
from api.yuketang import browser_login


@pytest.mark.parametrize('value,host', [
    ('https://USTC.yuketang.cn/pro/portal/?x=1', 'ustc.yuketang.cn'),
    ('campus.example.edu.cn', 'campus.example.edu.cn'),
    ('https://school-b.yuketang.cn/', 'school-b.yuketang.cn'),
])
def test_school_url_normalized(value, host):
    client = YuketangClient(value)
    assert client.domain == host
    assert client._url('/test') == 'https://' + host + '/test'


@pytest.mark.parametrize('value', ['', 'www.yuketang.cn', 'https://user:pass@school.example.cn', 'invalid domain'])
def test_invalid_school_never_falls_back(value):
    with pytest.raises(YuketangError):
        _normalize_domain(value)


def test_switch_school_isolates_session(tmp_path, monkeypatch):
    monkeypatch.setattr(store, 'app_dir', lambda: str(tmp_path))
    shutil.copyfile(Path(__file__).parents[1] / 'config_template.ini', tmp_path / 'config.ini')
    monkeypatch.setattr(yuketang_engine, '_STORE', None)
    book = yuketang_engine.account_store()
    first = book.active
    book.update(first, domain='school-a.yuketang.cn', cookie='old-cookie', user_id='old-user',
                university_id='old-school', realname='old-name')
    second = book.add()
    before = dict(second)
    runner = yuketang_engine.YuketangRunner(first)
    assert runner.configure_school('https://school-a.yuketang.cn/path') == 'school-a.yuketang.cn'
    assert runner.account['cookie'] == 'old-cookie'
    assert runner.configure_school('school-b.example.edu.cn') == 'school-b.example.edu.cn'
    assert all(runner.account[key] == '' for key in ('cookie', 'user_id', 'university_id', 'realname'))
    assert book.get(second['id']) == before
    assert runner.phase == 'idle'


def test_legacy_example_school_removed_only_without_identity(tmp_path, monkeypatch):
    monkeypatch.setattr(store, 'app_dir', lambda: str(tmp_path))
    monkeypatch.setattr(yuketang_engine, '_STORE', None)
    book = yuketang_engine.account_store()
    seed = book.active
    book.update(seed, domain='buu.yuketang.cn')
    logged = book.add()
    book.update(logged['id'], domain='buu.yuketang.cn', cookie='fixture-cookie')
    other = book.add()
    book.update(other['id'], domain='school.example.cn')
    monkeypatch.setattr(yuketang_engine, '_STORE', None)
    reloaded = yuketang_engine.account_store()
    assert reloaded.get(seed)['domain'] == ''
    assert reloaded.get(logged['id'])['domain'] == 'buu.yuketang.cn'
    assert reloaded.get(logged['id'])['cookie'] == 'fixture-cookie'
    assert reloaded.get(other['id'])['domain'] == 'school.example.cn'


def test_auto_qr_does_not_reuse_previous_school(tmp_path, monkeypatch):
    monkeypatch.setattr(store, 'app_dir', lambda: str(tmp_path))
    monkeypatch.setattr(yuketang_engine, '_STORE', None)
    book = yuketang_engine.account_store()
    book.update(book.active, domain='previous.example.cn', cookie='fixture-old')
    runner = yuketang_engine.YuketangRunner(book.active)
    monkeypatch.setattr(runner, '_spawn', lambda job, kind: job())
    calls = []
    def login(stop, step, **kwargs):
        calls.append(kwargs)
        return {'domain': 'selected.example.cn', 'cookie': 'fixture-new',
                'user_id': 'new-user', 'university_id': 'new-school'}
    monkeypatch.setattr(browser_login, 'login_by_browser', login)
    runner.login_auto()
    assert len(calls) == 1 and 'domain' not in calls[0]
    assert runner.account['domain'] == 'selected.example.cn'
    assert runner.account['cookie'] == 'fixture-new'


def test_browser_import_verifies_selected_school(monkeypatch):
    closed = []
    class Driver:
        current_url = 'https://school-b.example.edu.cn/pro/portal/home/'
        def get_cookies(self):
            return [{'name': 'sessionid', 'value': 'fixture-session'}]
    class Client:
        def __init__(self, domain, cookie, university_id):
            assert domain == 'school-b.example.edu.cn'
            assert university_id == 'school-b-id'
            self.cookie = cookie
        def fetch_identity(self):
            return {'user_id': 'fixture-user', 'realname': 'fixture-name'}
        def close(self):
            closed.append(True)
        @staticmethod
        def get_university_id(domain):
            return 'school-b-id'
    monkeypatch.setattr(browser_login, 'YuketangClient', Client)
    result = browser_login.school_session(Driver())
    assert result['domain'] == 'school-b.example.edu.cn'
    assert result['university_id'] == 'school-b-id'
    assert result['user_id'] == 'fixture-user'
    assert closed == [True]


@pytest.mark.parametrize('url', ['https://www.yuketang.cn/v2/web', 'https://school.example.cn/login'])
def test_browser_central_or_login_page_is_not_school_session(url):
    class Driver:
        current_url = url
        def get_cookies(self):
            raise AssertionError('must not read cookies before selecting a school portal')
    assert browser_login.school_session(Driver()) is None


def test_browser_cancellation_closes_window(monkeypatch):
    flags = {'cancel': False, 'closed': False}
    class Driver:
        def set_page_load_timeout(self, timeout):
            pass
        def get(self, url):
            assert url == 'https://www.yuketang.cn/web'
            flags['cancel'] = True
        def quit(self):
            flags['closed'] = True
    with pytest.raises(YuketangError, match='已取消'):
        browser_login.login_by_browser(lambda: flags['cancel'], lambda text: None,
                                       driver_factory=lambda profile: Driver())
    assert flags['closed']


def test_school_discovery_never_guesses_or_collapses_multischool():
    payload = {'success': True, 'data': {'universities': [
        {'id': 1, 'domain': 'school-a.example.cn', 'name': '学校 A'},
        {'id': 2, 'domain': 'school-b.example.cn', 'name': '学校 B'},
        {'id': 3, 'name': '只有校名，不猜域名'},
        {'id': 4, 'domain': 'www.yuketang.cn'},
    ]}}
    assert [x['domain'] for x in browser_login.school_choices(payload)] == [
        'school-a.example.cn', 'school-b.example.cn']
    assert browser_login.school_choices({'success': False, 'data': payload['data']}) == []
