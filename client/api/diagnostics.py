"""只读连接检查：不提交课程、不发送题目、不调用收费模型。"""
from concurrent.futures import ThreadPoolExecutor
from time import monotonic

from api import kc_client, _net


def run_diagnostics():
    def website(url):
        response = _net.send('GET', url, timeout=(3, 5))
        if not 200 <= response.status_code < 400:
            raise RuntimeError('站点暂不可用，请稍后重试')
        return '连接正常'

    def server():
        response = _net.send('GET', kc_client._server() + '/api/v1/health', timeout=(3, 5))
        response.raise_for_status()
        if response.json().get('status') != 'ok':
            raise RuntimeError('服务暂不可用，请稍后重试')
        return '服务正常'

    def knowledge():
        result = kc_client.knowledge_stats()
        scope = '共享题库' if result.get('scope') == 'shared' else '个人题库'
        return f"{scope} · {result['count']} 题"

    def usage():
        state = kc_client.load_state()
        token = state.get('kc_token', '')
        if not token:
            raise kc_client.KcAuthError('请先登录乐课账号')
        data = kc_client._get('/me', token)['usage']
        return f"今日 {data['today_tokens']:g} tokens · 累计 {data['total_tokens']:g} tokens"

    checks = [
        ('乐课服务', server), ('云端题库', knowledge), ('用量统计', usage),
        ('学习通登录', lambda: website('https://passport2.chaoxing.com/login')),
        ('雨课堂网站', lambda: website('https://www.yuketang.cn/')),
        ('智慧树网站', lambda: website('https://www.zhihuishu.com/')),
        ('U校园网站', lambda: website('https://ucloud.unipus.cn/home')),
    ]

    def check(item):
        name, operation = item
        start = monotonic()
        try:
            detail = operation()
            status = '通过'
        except kc_client.KcAuthError:
            status, detail = '待登录', '请先登录乐课账号，再检查'
        except kc_client.KcBlockedError:
            status, detail = '需处理', '账号被限制或已到期，请在用户网站检查'
        except Exception:
            status, detail = '未通过', '检查网络连接；若其他项目正常，请稍后重试此服务'
        return {'name': name, 'status': status, 'detail': detail,
                'elapsed_ms': round((monotonic() - start) * 1000)}

    with ThreadPoolExecutor(max_workers=4) as pool:
        return list(pool.map(check, checks))
