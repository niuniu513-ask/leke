"""Submission and draft states, with simulated HTTP only."""
import copy
from types import SimpleNamespace as NS
import pytest
import requests
from api.base import Chaoxing, StudyResult
from api.work_answer_policy import classify_work_receipt


@pytest.mark.parametrize('status,expected', [(True, 'accepted'), ('true', 'accepted'),
    (1, 'accepted'), (False, 'rejected'), ('false', 'rejected'), (0, 'rejected'),
    (None, 'unknown'), ('ok', 'unknown'), ({}, 'unknown')])
def test_receipt_requires_explicit_status(status, expected):
    assert classify_work_receipt(NS(status_code=200, json=lambda: {'status': status})) == expected


def test_non_json_and_http_errors_are_not_success():
    def invalid():
        raise ValueError('invalid JSON')
    assert classify_work_receipt(NS(status_code=200, json=invalid)) == 'unknown'
    assert classify_work_receipt(NS(status_code=502)) == 'unknown'
    assert classify_work_receipt(NS(status_code=403)) == 'rejected'


@pytest.mark.parametrize('answer,submit,receipt,verdict,expected,flag', [
    ('A', '', True, {'all_correct': True}, StudyResult.SUCCESS, ''),
    ('A', '1', True, None, StudyResult.DEFERRED, '1'),
    ('', '', True, None, StudyResult.DEFERRED, '1'),
    ('A', '', 'false', None, StudyResult.ERROR, ''),
    ('A', '', None, None, StudyResult.DEFERRED, ''),
    ('A', '', True, None, StudyResult.DEFERRED, ''),
    ('A', '', 'timeout', None, StudyResult.DEFERRED, ''),
    ('mismatched_batch', '', True, None, StudyResult.DEFERRED, '1'),
    ('A', '', True, {'wrong': ['fixture'], 'score': 0}, StudyResult.DEFERRED, ''),
    ('A', '', True, {'unknown': 1, 'score': 0}, StudyResult.DEFERRED, ''),
    ('A', '', True, {'wrong': ['fixture'], 'feedback': ['fixture'], 'score': 0}, StudyResult.DEFERRED, ''),
    ('ai_recovery', '', True, {'all_correct': True}, StudyResult.SUCCESS, ''),
])
def test_work_submission_flow(monkeypatch, answer, submit, receipt, verdict, expected, flag):
    import api.base as base
    fixture = {'pyFlag': '1', 'questions': [{'id': '1', 'type': 'single',
        'title': 'fixture', 'options': 'A. first\nB. second',
        'answerField': {'answertype1': '0'}}]}
    monkeypatch.setattr(base, 'decode_questions_info', lambda html: copy.deepcopy(fixture))
    forms = []
    def post(url, data, headers):
        forms.append(copy.deepcopy(data))
        if receipt == 'timeout':
            raise requests.Timeout('fixture')
        return NS(status_code=200, json=lambda: {'status': receipt})
    cx = NS()
    cx._session = NS(get=lambda *args, **kwargs: NS(status_code=200, text='fixture'), post=post)
    cx.tiku = NS(DISABLE=False, query_all=lambda *args, **kwargs:
        ['A', 'B'] if answer == 'mismatched_batch' else [None if answer == 'ai_recovery' else answer], get_submit_params=lambda: submit)
    if answer == 'ai_recovery':
        calls = []
        provider = type('AI', (), {'DISABLE': False, '_query': lambda self, q: calls.append(q['id']) or 'B'})()
        cx.tiku.providers = [provider]
    cx.kwargs = {}
    cx.rollback_times = 0
    checks = []
    cx._check_work_result = lambda *args: checks.append(True) or verdict
    result = Chaoxing.study_work(cx, {'courseId': 'c', 'clazzId': 'b'},
                           {'jobid': 'work-1', 'enc': 'fixture'},
                           {'knowledgeid': 'k', 'ktoken': 'fixture', 'cpi': 'p'})
    assert result == expected
    assert len(forms) == 1 and forms[0]['pyFlag'] == flag
    if answer == 'ai_recovery':
        assert calls == ['1'] and forms[0]['answer1'] == 'B'
    if flag == '1':
        assert not checks
    if answer == 'mismatched_batch':
        assert forms[0]['answer1'] == ''


@pytest.mark.parametrize('payload,expected', [
    ({'Answer': 'AB'}, 'AB'), ({'Answer': [' A ', 'B']}, 'A\nB'),
    ({'Answer': []}, ''), ({'Answer': [True]}, ''), ({'Answer': ['A', None]}, ''),
    (None, ''), ({'Answer': {'value': 'A'}}, ''),
])
def test_model_answer_shape(payload, expected):
    from api.work_answer_policy import parse_model_answer
    assert parse_model_answer(payload) == expected


def test_unknown_question_cannot_be_mapped_to_submission():
    from api.work_answer_policy import map_answer
    assert map_answer({'type': 'unknown'}, 'A', NS()) == ''


def test_deferred_work_is_not_counted_as_completed_or_retried(monkeypatch):
    import gui_app.study_engine as engine
    from main import ChapterResult, ChapterTask
    calls = []
    monkeypatch.setattr(engine, '_load_engine', lambda:
        (lambda *args, **kwargs: calls.append(1) or ChapterResult.DEFERRED, ChapterResult))
    task = ChapterTask(0, {'id': 'p', 'title': '章节'}, {'courseId': 'c', 'title': '课'})
    scheduler = engine.StudyScheduler(NS(), [task], {'jobs': 1, 'retry_interval': .01})
    scheduler.run()
    assert calls == [1]
    assert scheduler.deferred == [task] and scheduler.success_count == 0
    assert not scheduler.failed


def test_chapter_with_saved_work_remains_deferred(monkeypatch):
    import main
    monkeypatch.setattr(main, 'process_job', lambda *args, **kwargs: StudyResult.DEFERRED)
    cx = NS(rate_limiter=NS(limit_rate=lambda **kwargs: None),
            get_job_list=lambda *args, **kwargs: ([{'type': 'workid'}], {}))
    assert main.process_chapter(cx, {'title': '课程'},
                                {'title': '章节', 'has_finished': False}, 1) == main.ChapterResult.DEFERRED
