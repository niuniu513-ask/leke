from types import SimpleNamespace
import threading

import pytest

from api.answer import Tiku
from api.work_answer_policy import map_answer
from api.unipus import answer_policy, unipus_engine as vendor
from gui_app.unipus_engine import UnipusRunner


@pytest.mark.parametrize('kind,result,expected', [
    ('single', 'A', 'A'), ('single', 'apple', 'A'),
    ('single', 'app', ''), ('single', 'maybe B', ''),
    ('single', 'AB', ''), ('single', 'Z', ''),
    ('multiple', 'B,A', 'AB'), ('multiple', 'apple|pear', 'AB'),
])
def test_work_requires_explicit_matching_answer(kind, result, expected):
    q = {'type': kind, 'options': 'A. apple\nB. pear'}
    assert map_answer(q, result, None) == expected


def test_unknown_judgement_stays_unanswered():
    t = Tiku.get_tiku_from_config({'provider': 'AI'})
    t.true_list, t.false_list = [], []
    assert t.judgement_select('not sure') is None
    assert map_answer({'type': 'judgement'}, 'not sure', t) == ''


def test_model_is_last_even_when_old_config_has_no_ai():
    t = Tiku.get_tiku_from_config({'provider': 'AI,TikuLocal,TikuGo'})
    assert [p.__class__.__name__ for p in t.providers] == ['TikuLocal', 'TikuGo', 'AI']
    t = Tiku.get_tiku_from_config({'provider': 'TikuLocal'})
    assert [p.__class__.__name__ for p in t.providers] == ['TikuLocal', 'AI']


def make_solver(monkeypatch, model_answer='2. B', submit=True):
    questions = [vendor.Question(i, f'question {i}', vendor.QuestionType.SINGLE_CHOICE, None,
        options=[vendor.Option('A', 'first', None), vendor.Option('B', 'second', None)]) for i in [1, 2]]
    solver = vendor.AISolver.__new__(vendor.AISolver)
    solver.stop_requested = threading.Event()
    solver.processed_hashes = set()
    solver.content_handlers = []
    solver.parser = SimpleNamespace(parse_all=lambda: (questions, 'directions'))
    prompts, writes, saved = [], [], []
    solver.ai_client = SimpleNamespace(accumulated_passages=set(), ask=lambda prompt, **kw: model_answer)
    solver.prompt_builder = SimpleNamespace(build=lambda qs, d: prompts.append([q.number for q in qs]) or 'prompt')
    solver.executor = SimpleNamespace(execute=lambda q, ans: writes.append((q.number, ans)) or True,
                                      submit=lambda: submit)
    solver._generate_content_hash_from_direction = lambda: 'direction'
    solver._preprocess_video_if_needed = solver._preprocess_audio_if_needed = lambda *args: None
    solver._extract_passage = lambda: ''
    solver._find_next_question_button = lambda: None
    solver._wait_for_submit_complete = lambda: True
    solver._handle_confirm_dialog = lambda: False
    keys = {answer_policy.question_key(q, '["directions", []]'): q.number for q in questions}
    monkeypatch.setattr(answer_policy, 'cached_answer', lambda key: 'A' if keys[key] == 1 else '')
    monkeypatch.setattr(answer_policy, 'remember_answer', lambda key, ans: saved.append((key, ans)))
    return solver, prompts, writes


def test_unipus_bank_hit_avoids_model_and_only_miss_is_sent(monkeypatch):
    solver, prompts, writes = make_solver(monkeypatch)
    assert solver._process_current_tab_content('chapter', 'tab', 0, 0)
    assert prompts == [[2]]
    assert writes == [(1, 'A'), (2, 'B')]


@pytest.mark.parametrize('answer', ['', '2. Z', '2. maybe B'])
def test_unipus_model_failure_does_not_guess_or_submit(monkeypatch, answer):
    solver, prompts, writes = make_solver(monkeypatch, answer)
    solver.executor.submit = lambda: pytest.fail('incomplete page must not submit')
    assert not solver._process_current_tab_content('chapter', 'tab', 0, 0)
    assert writes == [(1, 'A')]


def test_unipus_submit_failure_is_not_success(monkeypatch):
    solver, _, _ = make_solver(monkeypatch, submit=False)
    assert not solver._process_current_tab_content('chapter', 'tab', 0, 0)


def test_popup_uses_configured_model_and_failure_has_no_heuristic(monkeypatch):
    handler = vendor.VideoHandler.__new__(vendor.VideoHandler)
    handler.config = SimpleNamespace(model='configured-model')
    handler.stop_requested = threading.Event()
    handler.video_transcript = 'fixture transcript'
    handler.current_video_url = 'fixture-video'
    handler.analyzer_client = object()
    monkeypatch.setattr(answer_policy, 'cached_answer', lambda key: '')
    calls = []
    def fail(client, **kwargs):
        calls.append(kwargs['model'])
        raise RuntimeError('model failed')
    monkeypatch.setattr(vendor, 'tracked_completion', fail)
    assert handler._intelligent_select_answer({'question': 'q', 'options': [{'letter': 'A', 'text': 'x'}]}) == ''
    assert calls == ['configured-model']


def test_partial_batch_and_unknown_return_are_failures():
    for result in [(1, ['missing']), None]:
        runner = UnipusRunner('fixture')
        runner._solver = SimpleNamespace(process_selected_tabs=lambda *a: result, _should_stop=lambda: False)
        runner._log = lambda text: None
        events = []
        runner.sig_done.connect(lambda kind, status: events.append(status))
        runner._process_thread([{'display': 'one'}, {'display': 'two'}])
        assert events == ['error']


def test_disconnect_during_login_cannot_restore_ready(tmp_path, monkeypatch):
    runner = UnipusRunner('fixture')
    runner._phase = 'connecting'
    cfg = tmp_path / 'config.json'
    cfg.write_text('{}')
    runner._write_engine_config = lambda u: str(cfg)
    runner._log = lambda text: None
    closed = []
    class Bot:
        def __init__(self, *a, **kw):
            self.driver = SimpleNamespace(quit=lambda: closed.append(True))
            self.popup_watcher = SimpleNamespace(run=lambda: None)
        def _login(self):
            runner.shutdown()
            return True
    monkeypatch.setattr(vendor, 'UCampusBot', Bot)
    monkeypatch.setattr(vendor, 'AISolver', lambda *a: pytest.fail('cancelled connection must not create solver'))
    runner._connect_thread({}, runner._generation)
    assert runner.phase == 'idle' and not runner.connected and closed == [True]
