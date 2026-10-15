import pytest
from selenium.common.exceptions import InvalidSessionIdException, NoSuchWindowException
from api.yuketang import browser_login as rain
from api.yuketang.client import YuketangError
from api.unipus.login_input import replace_login_value


@pytest.mark.parametrize('stage', ['get', 'handles', 'cookies'])
def test_rain_disconnect_is_readable_and_quits(monkeypatch, stage):
    class Driver:
        closed = False
        current_url = 'https://school.example.cn/pro/portal/'
        switch_to = None
        def __init__(self): self.switch_to = self
        def set_page_load_timeout(self, t): pass
        def get(self, url):
            if stage == 'get': raise InvalidSessionIdException('secret stacktrace')
        @property
        def window_handles(self):
            if stage == 'handles': raise InvalidSessionIdException('secret stacktrace')
            return ['tab']
        def window(self, h): pass
        def get_cookies(self): raise InvalidSessionIdException('secret stacktrace')
        def quit(self): self.closed = True
    d = Driver()
    with pytest.raises(YuketangError, match='连接已断开') as error:
        rain.login_by_browser(lambda: False, lambda t: None, driver_factory=lambda p: d)
    assert 'stacktrace' not in str(error.value)
    assert d.closed


def test_rain_closed_tab_does_not_lose_other_tab(monkeypatch):
    class Driver:
        current_url = 'https://school.example.cn/pro/portal/'
        window_handles = ['closed', 'live']
        def __init__(self): self.switch_to = self; self.closed = False
        def set_page_load_timeout(self, t): pass
        def get(self, url): self.entry = url
        def window(self, h):
            if h == 'closed': raise NoSuchWindowException()
        def quit(self): self.closed = True
    d = Driver()
    monkeypatch.setattr(rain, 'school_session', lambda driver: {'domain': 'school.example.cn'})
    result = rain.login_by_browser(lambda: False, lambda t: None,
        driver_factory=lambda p: d, domain='school.example.cn')
    assert result['domain'] == 'school.example.cn'
    assert d.entry == 'https://school.example.cn/pro/portal/home/'
    assert d.closed


@pytest.mark.parametrize('value', ['account', 'password'])
def test_unipus_replaces_existing_and_repeated_values(value):
    class Element:
        current = value * 2
        def click(self): pass
        def get_property(self, key): return self.current
        def send_keys(self, *args): raise AssertionError('must not append after exact replace')
    class Driver:
        def execute_script(self, script, element, replacement): element.current = replacement
    el = Element()
    for _ in range(2): replace_login_value(Driver(), el, value)
    assert el.current == value


def test_unipus_refuses_submit_if_input_does_not_update():
    class Element:
        def click(self): pass
        def get_property(self, key): return 'wrong'
        def send_keys(self, *args): pass
    class Driver:
        def execute_script(self, *args): pass
    with pytest.raises(RuntimeError, match='未正确更新'):
        replace_login_value(Driver(), Element(), 'fixture')


def test_rain_follows_single_official_school_link(monkeypatch):
    class Driver:
        current_url = rain.OFFICIAL_LOGIN
        window_handles = ['live']
        def __init__(self): self.switch_to = self; self.closed = False
        def set_page_load_timeout(self, t): pass
        def get(self, url): self.current_url = url
        def window(self, h): pass
        def execute_script(self, script): return ['https://school.example.cn/pro/portal/home/']
        def quit(self): self.closed = True
    d = Driver()
    monkeypatch.setattr(rain.time, 'sleep', lambda t: None)
    monkeypatch.setattr(rain, 'school_session', lambda driver:
        {'domain': 'school.example.cn'} if '/pro/' in driver.current_url else None)
    monkeypatch.setattr(rain, 'unified_session', lambda driver: None)
    result = rain.login_by_browser(lambda: False, lambda t: None, driver_factory=lambda p: d)
    assert result['domain'] == 'school.example.cn'
    assert d.closed


def test_rain_timeout_preserves_school_validation_reason(monkeypatch):
    class Driver:
        current_url = 'https://school.example.cn/pro/portal/'
        window_handles = ['live']
        def __init__(self): self.switch_to = self
        def set_page_load_timeout(self, t): pass
        def get(self, url): pass
        def window(self, h): pass
        def quit(self): pass
    ticks = iter([0, 0, 0, 0, 10])
    monkeypatch.setattr(rain.time, 'monotonic', lambda: next(ticks))
    monkeypatch.setattr(rain.time, 'sleep', lambda t: None)
    def unbound(driver): raise YuketangError('需要绑定学校学号')
    monkeypatch.setattr(rain, 'school_session', unbound)
    steps = []
    with pytest.raises(YuketangError, match='需要绑定学校学号'):
        rain.login_by_browser(lambda: False, steps.append, timeout=5, driver_factory=lambda p: Driver())
    assert any('需要绑定学校学号' in step for step in steps)
