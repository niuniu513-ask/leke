"""保存供应商返回的真实用量，按账号隔离、失败重试且不重复入账。"""
import base64
import json
import threading
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

_flush_lock = threading.Lock()


def account_context(token=None):
    from api import kc_client
    try:
        if token is None:
            token = kc_client.load_state().get('kc_token', '')
        payload = token.split('.')[1]
        user_id = str(json.loads(base64.urlsafe_b64decode(payload + '=' * (-len(payload) % 4)))['sub'])
        return {'user_id': user_id, 'server': kc_client._server()}
    except Exception:
        return None


def pending_dir():
    from gui_app.app import project_root
    return Path(project_root()) / '.token_usage_pending'


def token_count(response):
    usage = response.get('usage') if isinstance(response, dict) else getattr(response, 'usage', None)
    if not usage:
        return 0
    def field(name):
        value = usage.get(name) if isinstance(usage, dict) else getattr(usage, name, None)
        return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None
    total = field('total_tokens')
    if total is None:
        prompt, completion = field('prompt_tokens'), field('completion_tokens')
        if prompt is None or completion is None:
            return 0
        total = prompt + completion
    # reasoning/cached tokens are already included; never add detail fields again.
    return total


def record_response(response, context=None):
    try:
        context = context or account_context()
        tokens = token_count(response)
        if not context or not tokens:
            return
        report = {**context, 'id': str(uuid.uuid4()), 'tokens': tokens,
                  'day': datetime.now(timezone(timedelta(hours=8))).date().isoformat()}
        directory = pending_dir()
        directory.mkdir(parents=True, exist_ok=True)
        target = directory / (report['id'] + '.json')
        temporary = target.with_suffix('.tmp')
        temporary.write_text(json.dumps(report), encoding='utf-8')
        temporary.replace(target)
        threading.Thread(target=flush_usage, daemon=True).start()
    except Exception:
        # Telemetry must never trigger another billable AI retry.
        pass


def tracked_completion(client, **kwargs):
    context = account_context()
    response = client.chat.completions.create(**kwargs)
    if context:
        record_response(response, context)
    return response


def flush_usage():
    from api import kc_client
    if not _flush_lock.acquire(blocking=False):
        return 0
    sent = 0
    try:
        token = kc_client.load_state().get('kc_token', '')
        context = account_context(token)
        if not context or not token:
            return 0
        files = sorted(pending_dir().glob('*.json'))
        for offset in range(0, len(files), 100):
            reports, paths = [], []
            for path in files[offset:offset + 100]:
                try:
                    report = json.loads(path.read_text(encoding='utf-8'))
                    if report.get('user_id') == context['user_id'] and report.get('server') == context['server']:
                        uuid.UUID(report['id'])
                        datetime.strptime(report['day'], '%Y-%m-%d')
                        if type(report['tokens']) is not int or not 0 < report['tokens'] <= 1_000_000_000:
                            continue
                        reports.append({key: report[key] for key in ('id', 'day', 'tokens')})
                        paths.append(path)
                except (ValueError, TypeError, KeyError, AttributeError, OSError):
                    # 保留损坏文件供排查，不能因此堵住其他正常记录。
                    continue
            if not reports:
                continue
            # A response lost after commit is safely replayed with the same IDs.
            result = kc_client._post('/usage/tokens', {'reports': reports}, token=token)
            if not isinstance(result, dict) or result.get('ok') is not True or result.get('accepted') != len(reports):
                return sent
            for path in paths:
                path.unlink(missing_ok=True)
            sent += len(reports)
        return sent
    except Exception:
        return sent
    finally:
        _flush_lock.release()
