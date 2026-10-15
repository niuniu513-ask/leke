"""Read ordinary rain-classroom courses using official web API schemas."""
from urllib.parse import quote, urlencode, urlsplit

from .client import YuketangError, parse_cookie_string

ORIGIN = 'https://www.yuketang.cn'


def course_rows(payload, identity):
    if not isinstance(payload, dict) or payload.get('errcode') != 0:
        raise YuketangError('普通版课程接口拒绝请求，请重新扫码登录。')
    data = payload.get('data')
    rows = data.get('list') if isinstance(data, dict) else None
    if not isinstance(rows, list):
        raise YuketangError('普通版课程响应格式变化：缺少 data.list，未将错误当作空课程。')
    result = []
    for row in rows:
        if not isinstance(row, dict):
            raise YuketangError('普通版课程列表存在无法识别的记录。')
        course = row.get('course') if isinstance(row.get('course'), dict) else row
        classrooms = row.get('classrooms') if identity == 1 else [row]
        if classrooms is None:
            classrooms = []
        if not isinstance(classrooms, list):
            raise YuketangError('普通版班级响应格式变化。')
        for classroom in classrooms:
            if not isinstance(classroom, dict):
                raise YuketangError('普通版班级列表存在无法识别的记录。')
            classroom_id = classroom.get('classroom_id') or classroom.get('id')
            if not classroom_id:
                raise YuketangError('普通版课程缺少班级 ID，无法建立课程入口。')
            university_id = course.get('university_id') or ''
            platform = course.get('platform') or 3
            name = course.get('name') or course.get('course_name') or '未命名课程'
            classroom_name = classroom.get('classroom_name') or classroom.get('name') or ''
            role = '我教的课' if identity == 1 else '我学的课'
            route = 'teacherLog' if identity == 1 else 'studentLog'
            query = urlencode({'university_id': university_id, 'platform_id': platform,
                               'classroom_id': classroom_id, 'content_url': ''})
            url = ORIGIN + '/v2/web/' + route + '/' + quote(str(classroom_id), safe='') + '?' + query
            result.append({'course_name': f'[{role}] {name}' + (f' · {classroom_name}' if classroom_name else ''),
                           'classroom_id': str(classroom_id), 'course_id': str(course.get('id') or classroom.get('course_id') or ''),
                           'university_id': str(university_id), 'identity': identity,
                           'platform_id': str(platform),
                           'login_mode': 'standard', 'portal_url': url})
    return result


def list_courses(cookie, expected_user_id, driver=None):
    """Use same-origin browser requests, or an isolated session after app restart.

    Revalidate user identity before reading so an account cannot import another
    user's courses when the browser account has changed.
    """
    session = None
    host = urlsplit(ORIGIN).hostname
    browser = driver is not None and urlsplit(driver.current_url).hostname == host
    try:
        if not browser:
            from api._net import new_session
            session = new_session()
            for name, value in parse_cookie_string(cookie).items():
                session.cookies.set(name, value, domain=host, path='/')
        def fetch(path):
            if browser:
                driver.set_script_timeout(18)
                payload = driver.execute_async_script('''
                    const path = arguments[0], done = arguments[arguments.length - 1];
                    fetch(path, {credentials:'same-origin', signal:AbortSignal.timeout(15000)})
                      .then(r => r.ok ? r.json() : null).then(done).catch(() => done(null));
                ''', path)
            else:
                response = session.get(ORIGIN + path, timeout=(10, 18), allow_redirects=False)
                if response.status_code != 200:
                    raise YuketangError('普通版登录已失效或课程接口不可用，请重新扫码。')
                try:
                    payload = response.json()
                except ValueError as exc:
                    raise YuketangError('普通版接口未返回有效 JSON，请重新扫码。') from exc
            return payload
        identity = fetch('/v2/api/web/userinfo')
        rows = identity.get('data') if isinstance(identity, dict) and identity.get('errcode') == 0 else None
        if not isinstance(rows, list) or not rows or not isinstance(rows[0], dict) or not rows[0].get('user_id'):
            raise YuketangError('普通版登录验证失败，请重新扫码。')
        if not expected_user_id or str(rows[0]['user_id']) != str(expected_user_id):
            raise YuketangError('浏览器账号与当前档案不一致，请切换账号或重新扫码。')
        courses = []
        for role in (2, 1):
            courses.extend(course_rows(fetch('/v2/api/web/courses/list?identity=' + str(role)), role))
        return courses
    finally:
        if session is not None:
            session.close()
