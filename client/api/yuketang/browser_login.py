"""Official QR login in Edge; import only a verified school portal session."""
import tempfile
import time
from contextlib import nullcontext
from urllib.parse import urlsplit
from selenium.common.exceptions import (
    InvalidSessionIdException, NoSuchWindowException, WebDriverException,
)

from .client import YuketangClient, YuketangError, _cookie_header, _normalize_domain

OFFICIAL_LOGIN = 'https://www.yuketang.cn/web'
CENTRAL_HOSTS = {'www.yuketang.cn', 'yuketang.cn', 'v.yuketang.cn', 'b.yuketang.cn'}


def standard_session(driver):
    """Verify ordinary rain-classroom login using the site's own identity API.

    Public web bundle 1.2.324 declares GET_USER_INFO=/v2/api/web/userinfo,
    with errcode=0 and data[0].user_id. Cookies alone are not login evidence.
    """
    if urlsplit(driver.current_url).hostname != 'www.yuketang.cn':
        return None
    if not hasattr(driver, 'execute_async_script'):
        return None
    driver.set_script_timeout(10)
    payload = driver.execute_async_script('''
        const done = arguments[arguments.length - 1];
        fetch('/v2/api/web/userinfo', {credentials:'same-origin',
            signal:AbortSignal.timeout(8000)})
          .then(r => r.ok ? r.json() : null).then(done).catch(() => done(null));
    ''')
    if not isinstance(payload, dict) or payload.get('errcode') != 0:
        return None
    rows = payload.get('data')
    if not isinstance(rows, list) or not rows or not isinstance(rows[0], dict):
        return None
    identity = rows[0]
    if not identity.get('user_id'):
        return None
    cookies = {str(c['name']): str(c['value']) for c in driver.get_cookies()}
    if not cookies:
        return None
    return {'domain': 'www.yuketang.cn', 'login_mode': 'standard',
            'user_id': str(identity['user_id']), 'realname': str(identity.get('name') or ''),
            'university_id': '', 'cookie': _cookie_header(cookies)}


def school_choices(payload):
    """Use explicit portal addresses returned by the official bound-school API only."""
    found = {}
    def visit(value):
        if isinstance(value, list):
            for row in value:
                visit(row)
        elif isinstance(value, dict):
            school_id = value.get('university_id') or value.get('school_id') or value.get('id')
            portal = value.get('domain') or value.get('host_name') or value.get('university_domain')
            if school_id and portal:
                try:
                    domain = _normalize_domain(portal)
                    if domain not in CENTRAL_HOSTS:
                        found[(domain, str(school_id))] = {
                            'domain': domain, 'university_id': str(school_id),
                            'name': str(value.get('university_name') or value.get('name') or domain)}
                except YuketangError:
                    pass
            for child in value.values():
                if isinstance(child, (list, dict)):
                    visit(child)
    if isinstance(payload, dict) and payload.get('success') is True:
        visit(payload.get('data'))
    return list(found.values())


def unified_session(driver):
    """If the official response provides one bound school, exchange and verify its session."""
    if urlsplit(driver.current_url).hostname != 'www.yuketang.cn':
        return None
    if not any(c.get('name') == 'sessionid' for c in driver.get_cookies()):
        return None
    driver.set_script_timeout(10)
    response = driver.execute_async_script('''
        const done = arguments[arguments.length - 1];
        fetch('/edu_admin/get_university_info/', {credentials:'same-origin',
            signal:AbortSignal.timeout(8000)})
          .then(r => r.ok ? r.json() : null)
          .then(data => done({schools:data, auth:localStorage.getItem('Auth'),
                             user:localStorage.getItem('UserID')}))
          .catch(() => done(null));
    ''')
    if not isinstance(response, dict):
        return None
    schools = school_choices(response.get('schools'))
    # Never silently choose among multiple schools or derive their domains from names.
    if len(schools) != 1 or not response.get('auth') or not response.get('user'):
        return None
    selected = schools[0]
    session = YuketangClient._verify_login(selected['domain'], selected['university_id'],
                                           response['auth'], response['user'])
    client = YuketangClient(selected['domain'], cookie=session['cookie'],
                            university_id=selected['university_id'])
    try:
        identity = client.fetch_identity()
        if not identity.get('user_id'):
            return None
        return {**identity, **selected, 'cookie': client.cookie}
    finally:
        client.close()


def school_session(driver):
    """Read the user's selected school tab, never guess a domain from a school name."""
    host = (urlsplit(driver.current_url).hostname or '').lower()
    path = urlsplit(driver.current_url).path
    if host in CENTRAL_HOSTS or '/pro/' not in path:
        return None
    domain = _normalize_domain(host)
    cookies = {str(c['name']): str(c['value']) for c in driver.get_cookies()}
    if not cookies.get('sessionid'):
        return None
    # Check the portal's own school identity, rather than trust a central cookie's uv_id.
    school_id = YuketangClient.get_university_id(domain)
    client = YuketangClient(domain, cookie=_cookie_header(cookies), university_id=school_id)
    try:
        identity = client.fetch_identity()
        if not identity.get('user_id'):
            raise YuketangError('学校网页尚未完成登录')
        return {**identity, 'domain': domain, 'cookie': client.cookie,
                'university_id': str(school_id)}
    finally:
        client.close()


def login_by_browser(should_stop, on_step, timeout=300, driver_factory=None, domain='',
                     on_browser=None, on_ready=None, profile_dir=None):
    """No credentials are entered by the app; the user scans the official page."""
    driver = None
    retained = False
    profile_context = nullcontext(profile_dir) if profile_dir else tempfile.TemporaryDirectory(
        prefix='leke_rain_login_', ignore_cleanup_errors=True)
    with profile_context as profile:
        try:
            if should_stop():
                raise YuketangError('已取消')
            if driver_factory is None:
                from selenium import webdriver
                options = webdriver.EdgeOptions()
                options.add_argument('--user-data-dir=' + profile)
                options.add_argument('--disable-notifications')
                driver = webdriver.Edge(options=options)
            else:
                driver = driver_factory(profile)
            if on_browser:
                process = getattr(getattr(driver, 'service', None), 'process', None)
                if process:
                    on_browser(int(process.pid))
            driver.set_page_load_timeout(35)
            entry = 'https://' + _normalize_domain(domain) + '/pro/portal/home/' if domain else OFFICIAL_LOGIN
            driver.get(entry)
            on_step('请在 Edge 官方页面扫码；如有多所学校，请打开目标学校的专业版课程入口。')
            deadline = time.monotonic() + timeout
            attempted = {}
            last_error = ''
            prompted = False
            while time.monotonic() < deadline:
                if should_stop():
                    raise YuketangError('已取消')
                handles = list(driver.window_handles)
                if not handles:
                    raise YuketangError('登录窗口已关闭')
                for handle in handles:
                    try:
                        driver.switch_to.window(handle)
                        url = driver.current_url
                    except NoSuchWindowException:
                        # A tab may close between listing handles and selecting it.
                        continue
                    if time.monotonic() - attempted.get(url, -100) < 5:
                        continue
                    attempted[url] = time.monotonic()
                    try:
                        result = school_session(driver) or unified_session(driver) or standard_session(driver)
                    except NoSuchWindowException:
                        continue
                    except YuketangError as exc:
                        result = standard_session(driver)
                        if not result:
                            message = str(exc)
                            if message != last_error:
                                last_error = message
                                on_step('学校登录尚未通过验证：' + message)
                            continue
                    if result:
                        if should_stop():
                            raise YuketangError('已取消')
                        if on_ready:
                            if not profile_dir:
                                raise YuketangError('保留浏览器需要独立账号档案目录')
                            on_ready(driver)
                            retained = True
                        on_step('已验证网页登录：' + result['domain'])
                        return result
                    if urlsplit(url).hostname in CENTRAL_HOSTS:
                        links = driver.execute_script('''
                            return Array.from(document.querySelectorAll('a[href]'))
                              .map(a => a.href).filter(u => {
                                try { const x = new URL(u); return x.protocol === 'https:' && x.pathname.includes('/pro/'); }
                                catch (_) { return false; }
                              });
                        ''') or []
                        portals = {}
                        for link in links:
                            try:
                                host = _normalize_domain(link)
                                if host not in CENTRAL_HOSTS:
                                    portals[host] = 'https://' + host + '/pro/portal/home/'
                            except YuketangError:
                                continue
                        if len(portals) == 1:
                            on_step('正在打开官网提供的学校专业版入口')
                            driver.get(next(iter(portals.values())))
                        elif not prompted and any(c.get('name') == 'sessionid' for c in driver.get_cookies()):
                            on_step('官网已登录，请在此 Edge 窗口选择学校专业版课程入口；普通版登录不能直接读取学校专业版课程。')
                            prompted = True
                time.sleep(0.25)
            if last_error:
                raise YuketangError('学校登录验证未完成：' + last_error)
            raise YuketangError('尚未进入学校专业版课程入口。官网扫码成功不等于学校登录成功；请在 Edge 中打开学校的 /pro/ 课程页面，或填写学校网址后使用“学校扫码登录”。普通雨课堂课程暂不支持此登录链路。')
        except InvalidSessionIdException as exc:
            message = '已取消' if should_stop() else '登录浏览器已关闭或连接已断开，请重新点击“扫码识别学校”，并保持 Edge 窗口打开直到识别完成。'
            raise YuketangError(message) from exc
        except WebDriverException as exc:
            message = '已取消' if should_stop() else '无法连接登录浏览器，请关闭本次登录窗口后重试；若仍失败，请检查 Edge 是否可正常启动并更新浏览器。'
            raise YuketangError(message) from exc
        finally:
            if driver is not None and not retained:
                try:
                    driver.quit()
                except Exception:
                    pass
