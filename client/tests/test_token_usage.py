import base64
import json
from types import SimpleNamespace

import pytest
from api import kc_client, token_usage as usage


@pytest.fixture
def outbox(tmp_path, monkeypatch):
    payload = base64.urlsafe_b64encode(json.dumps({'sub': '42'}).encode()).decode().rstrip('=')
    state = {'kc_token': 'x.' + payload + '.x'}
    monkeypatch.setattr(kc_client, 'load_state', lambda: state)
    monkeypatch.setattr(usage, 'pending_dir', lambda: tmp_path)
    monkeypatch.setattr(usage.threading, 'Thread', lambda **kwargs: SimpleNamespace(start=lambda: None))
    return tmp_path, state


def test_actual_usage_no_double_count_and_no_estimate():
    assert usage.token_count({'usage': {'total_tokens': 100, 'prompt_tokens': 80, 'completion_tokens': 20,
                                       'completion_tokens_details': {'reasoning_tokens': 10}}}) == 100
    assert usage.token_count(SimpleNamespace(usage=SimpleNamespace(prompt_tokens=80, completion_tokens=20))) == 100
    assert usage.token_count({'usage': {'prompt_tokens': 80}}) == 0
    assert usage.token_count({'usage': {'total_tokens': True}}) == 0


def test_retry_keeps_id_and_other_accounts_are_not_sent(outbox, monkeypatch):
    directory, state = outbox
    usage.record_response({'usage': {'total_tokens': 150}})
    reports = list(directory.glob('*.json'))
    assert len(reports) == 1
    report = json.loads(reports[0].read_text())
    attempts = []
    def fail(path, body, token):
        attempts.append(body)
        raise kc_client.KcError('response lost')
    monkeypatch.setattr(kc_client, '_post', fail)
    assert usage.flush_usage() == 0 and reports[0].exists()
    original = state['kc_token']
    state['kc_token'] = 'x.' + base64.urlsafe_b64encode(b'{"sub":"43"}').decode().rstrip('=') + '.x'
    assert usage.flush_usage() == 0 and len(attempts) == 1
    state['kc_token'] = original
    def success(path, body, token):
        attempts.append(body)
        return {'ok': True, 'accepted': len(body['reports'])}
    monkeypatch.setattr(kc_client, '_post', success)
    assert usage.flush_usage() == 1 and not reports[0].exists()
    assert attempts[0] == attempts[1]
    assert attempts[0]['reports'][0]['id'] == report['id']


def test_completion_recorded_before_answer_parsing_and_account_switch(outbox):
    directory, state = outbox
    def create(**kwargs):
        state['kc_token'] = ''
        return SimpleNamespace(usage=SimpleNamespace(total_tokens=25), choices=[])
    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    response = usage.tracked_completion(client, model='test')
    assert response.choices == []
    report = json.loads(next(directory.glob('*.json')).read_text())
    assert report['user_id'] == '42' and report['tokens'] == 25


def test_corrupt_pending_file_does_not_block_valid_usage(outbox, monkeypatch):
    directory, _ = outbox
    (directory / '000-corrupt.json').write_text('{broken', encoding='utf-8')
    usage.record_response({'usage': {'total_tokens': 20}})
    monkeypatch.setattr(kc_client, '_post', lambda path, body, token: {'ok': True, 'accepted': len(body['reports'])})
    assert usage.flush_usage() == 1
    assert [p.name for p in directory.glob('*.json')] == ['000-corrupt.json']


def test_unbound_yuketang_is_actionable_not_missing_cookie(monkeypatch):
    from api.yuketang import client
    response = SimpleNamespace(status_code=200, headers={}, cookies={},
                               json=lambda: {'success': True, 'data': {'bind_status': False}})
    monkeypatch.setattr(client, '_send', lambda *a, **kw: response)
    with pytest.raises(client.YuketangError, match='尚未绑定当前学校') as error:
        client.YuketangClient._verify_login('example.edu', '1', 'private-auth', '2')
    assert 'private-auth' not in str(error.value) and '学号或工号' in str(error.value)
