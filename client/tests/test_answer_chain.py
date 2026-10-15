"""题库查询、重做和平台批改回流的隔离回归。"""
from unittest.mock import Mock

import pytest

from api import answer as A


class Provider(A.Tiku):
    def __init__(self, answer="正确"):
        super().__init__()
        self.name = "test"
        self._query = Mock(return_value=answer)

    def _query(self, q_info):
        raise NotImplementedError


@pytest.fixture
def dao(tmp_path, monkeypatch):
    cache = A.CacheDAO(str(tmp_path / "cache.json"))
    monkeypatch.setattr(A, "get_cache_dao", lambda: cache)
    return cache


def question(title="测试题", kind="judgement"):
    return {"title": title, "type": kind, "options": "A.北京\nB.上海"}


def test_single_query_returns_and_caches_remote_answer(dao):
    provider = Provider()
    assert provider.query(question()) == "正确"
    assert dao.get_cache("测试题") == "正确"
    assert provider.query(question()) == "正确"
    assert provider._query.call_count == 1


@pytest.mark.parametrize("batch", [False, True])
def test_feedback_bypasses_cache_in_fallback(dao, batch):
    dao.add_cache("测试题", "错误")
    provider = Provider()
    chain = A.TikuFallback([provider])
    chain.set_work_feedback(["测试题上次答错"])
    result = chain.query_all([question()]) if batch else chain.query(question())
    assert result == (["正确"] if batch else "正确")
    assert dao.get_cache("测试题") == "正确"
    assert provider.work_feedback == ["测试题上次答错"]
    chain.set_work_feedback(None)
    assert chain.work_feedback is None
    assert provider.work_feedback is None


@pytest.mark.parametrize("batch", [False, True])
def test_invalid_cached_answer_falls_through(dao, batch):
    dao.add_cache("测试题", "北京")
    provider = Provider()
    result = provider.query_all([question()]) if batch else provider.query(question())
    assert result == (["正确"] if batch else "正确")
    assert provider._query.call_count == 1


def test_fallback_validates_cached_answer(dao):
    dao.add_cache("测试题", "北京")
    assert A.TikuFallback([Provider()]).query_all([question()]) == ["正确"]


def test_harvest_keeps_english_option_text(dao):
    assert A.harvest_correct_answers([{
        "title": "Excel求和函数", "type_label": "单选题", "correct_answer": "SUM",
        "options": [("A", "SUM"), ("B", "MAX")],
    }]) == 1
    assert dao.get_cache("Excel求和函数") == "SUM"


def test_truncated_batch_is_discarded_before_fallback(dao):
    broken = Provider()
    broken._query_all = Mock(return_value=["北京"])
    healthy = Provider("上海")
    chain = A.TikuFallback([broken, healthy])
    assert chain.query_all([question("甲", "single"), question("乙", "single")]) == ["上海", "上海"]


@pytest.mark.parametrize("correct", ["A", "A. 北京"])
def test_harvest_stores_option_text_not_position(dao, correct):
    assert A.harvest_correct_answers([{
        "title": "测试题", "type_label": "单选题", "correct_answer": correct,
        "options": [("A", "北京"), ("B", "上海")],
    }]) == 1
    assert dao.get_cache("测试题") == "北京"


def test_verified_harvest_replaces_wrong_prediction(dao):
    dao.add_cache("测试题", "错误")
    assert A.harvest_correct_answers([{
        "title": "测试题", "type_label": "判断题", "correct_answer": "正确",
    }]) == 1
    assert dao.get_cache("测试题") == "正确"


@pytest.mark.parametrize("title, expected", [
    ("3.14是圆周率的近似值", "3.14是圆周率的近似值"),
    ("1.【单选题】测试题（2分）", "测试题"),
    ("2、测试题 (1.5分)", "测试题"),
])
def test_title_normalization_preserves_decimal(title, expected):
    assert A.cache_key(title) == expected


def test_adapter_accepts_string_answer_without_splitting_characters(dao, monkeypatch):
    response = Mock(status_code=200)
    response.json.return_value = {"answer": {"bestAnswer": "北京"}}
    monkeypatch.setattr(A, "_net_send", Mock(return_value=response))
    assert A.TikuAdapter().query(question(kind="single")) == "北京"
