"""云端存储默认值、批量查询、身份切换和可恢复迁移。"""
import json
from unittest.mock import Mock

import pytest
from api import answer as A, kc_client as K


@pytest.fixture
def state(monkeypatch):
    values = {'kc_token': 'test-token'}
    monkeypatch.setattr(K, 'load_state', lambda: dict(values))
    monkeypatch.setattr(K, 'save_state', values.update)
    return values


def test_production_defaults_to_cloud_without_creating_local_files(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(A, '_cache_dao_singleton', None)
    assert isinstance(A.get_cache_dao(), A.CloudCacheDAO)
    assert list(tmp_path.iterdir()) == []


def test_bulk_query_preserves_order_and_chunks(state, monkeypatch):
    calls = []
    def post(path, payload, token):
        calls.append(payload['titles'])
        assert token == 'test-token' and path == '/knowledge/query'
        return {'answers': [str(t) for t in payload['titles']]}
    monkeypatch.setattr(K, '_post', post)
    titles = [f'题{i}' for i in range(205)]
    assert K.knowledge_query(titles) == titles
    assert list(map(len, calls)) == [100, 100, 5]


def test_no_credentials_prevents_network(monkeypatch):
    monkeypatch.setattr(K, 'load_state', lambda: {})
    post = Mock()
    monkeypatch.setattr(K, '_post', post)
    with pytest.raises(K.KcAuthError):
        K.knowledge_query(['题'])
    with pytest.raises(K.KcAuthError):
        K.knowledge_write([{'question': '题', 'answer': '答案'}])
    post.assert_not_called()


def test_identity_switch_recovers_from_auth_failure(state, monkeypatch):
    query = Mock(side_effect=[K.KcAuthError('expired'), ['正确']])
    monkeypatch.setattr(K, 'knowledge_query', query)
    dao = A.CloudCacheDAO()
    assert dao.get_cache('题') is None
    assert dao.get_cache('题') is None
    assert query.call_count == 1
    state['kc_token'] = 'fresh-token'
    assert dao.get_cache('题') == '正确'


def test_query_all_uses_one_bulk_cloud_lookup(state, monkeypatch):
    query = Mock(return_value=['正确', '错误'])
    monkeypatch.setattr(K, 'knowledge_query', query)
    monkeypatch.setattr(A, '_cache_dao_singleton', A.CloudCacheDAO())
    bank = A.TikuLocal()
    assert bank.query_all([{'title': '题1', 'type': 'judgement'},
                           {'title': '题2', 'type': 'judgement'}]) == ['正确', '错误']
    query.assert_called_once_with(['题1', '题2'])


def test_cloud_writes_verified_answers_without_local_file(state, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    write = Mock(return_value=1)
    monkeypatch.setattr(K, 'knowledge_write', write)
    dao = A.CloudCacheDAO()
    assert dao.add_cache('1.【判断题】题（2分）', '正确', verified=True)
    write.assert_called_once_with([{'question': '题', 'answer': '正确', 'verified': True}])
    assert list(tmp_path.iterdir()) == []


def test_harvest_promotes_matching_prediction_to_verified(state, monkeypatch):
    monkeypatch.setattr(A, '_cache_dao_singleton', A.CloudCacheDAO())
    monkeypatch.setattr(K, 'knowledge_query', lambda titles: ['正确'] * len(titles))
    write = Mock(return_value=1)
    monkeypatch.setattr(K, 'knowledge_write', write)
    assert A.harvest_correct_answers([{
        'title': '题', 'type_label': '判断', 'correct_answer': '正确',
    }]) == 1
    write.assert_called_once_with([{'question': '题', 'answer': '正确', 'verified': True}])


def test_migration_is_idempotent_and_retains_source(state, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    source = tmp_path / '知识库' / 'cache.json'
    source.parent.mkdir()
    source.write_text(json.dumps({'1.【单选题】题（2分）': '北京'}), encoding='utf-8')
    original = source.read_bytes()
    monkeypatch.setattr(K, 'knowledge_stats', lambda: {'count': 0, 'scope': 'shared', 'user_id': 1})
    write = Mock(return_value=1)
    monkeypatch.setattr(K, 'knowledge_write', write)
    dao = A.CloudCacheDAO()
    assert dao.migrate_legacy() == 1
    assert dao.migrate_legacy() == 0
    write.assert_called_once_with([{'question': '题', 'answer': '北京', 'verified': False}], overwrite=False)
    assert source.read_bytes() == original


def test_failed_migration_can_retry_without_completion_marker(state, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / 'cache.json').write_text('{"题":"答案"}', encoding='utf-8')
    monkeypatch.setattr(K, 'knowledge_stats', lambda: {'count': 0, 'scope': 'shared', 'user_id': 1})
    monkeypatch.setattr(K, 'knowledge_write', Mock(side_effect=K.KcError('unavailable')))
    dao = A.CloudCacheDAO()
    with pytest.raises(K.KcError):
        dao.migrate_legacy()
    assert 'kc_knowledge_migrated' not in state
    monkeypatch.setattr(K, 'knowledge_write', Mock(return_value=1))
    assert dao.migrate_legacy() == 1


def test_mismatched_cloud_response_rejected(state, monkeypatch):
    monkeypatch.setattr(K, '_post', lambda *a, **kw: {'answers': ['错位答案']})
    with pytest.raises(K.KcError):
        K.knowledge_query(['甲', '乙'])
