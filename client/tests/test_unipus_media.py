import threading
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from api.unipus import audio_recognizer as audio
from api.unipus import reference_knowledge as kb
from api.unipus import unipus_engine as vendor


def test_download_uses_scoped_cookies_referer_and_cleans_temp(monkeypatch):
    response = Mock()
    response.headers = {'Content-Type': 'audio/mpeg'}
    response.iter_content.return_value = [b'audio']
    response.__enter__ = Mock(return_value=response)
    response.__exit__ = Mock(return_value=False)
    session = Mock()
    session.headers = {}
    session.__enter__ = Mock(return_value=session)
    session.__exit__ = Mock(return_value=False)
    session.get.return_value = response
    monkeypatch.setattr('api._net.new_session', lambda: session)
    driver = SimpleNamespace(current_url='https://course.example.test/page', get_cookies=lambda: [
        {'name': 'session', 'value': 'fixture', 'domain': 'course.example.test', 'path': '/course', 'secure': True}])
    t = audio.AudioTranscriber(driver)
    paths = []
    def recognize(path, language):
        from pathlib import Path
        paths.append(Path(path))
        assert Path(path).read_bytes() == b'audio'
        return 'short transcript'
    t._transcribe_local = recognize
    assert t.transcribe('https://course.example.test/course/audio.mp3') == 'short transcript'
    assert t.transcribe('https://course.example.test/course/audio.mp3') == 'short transcript'
    session.cookies.set.assert_called_once_with('session', 'fixture', domain='course.example.test', path='/course', secure=True)
    assert session.headers['Referer'] == driver.current_url
    assert session.get.call_count == 1 and not paths[0].exists()


@pytest.mark.parametrize('failure', ['html', 'size', 'cancel'])
def test_failed_media_is_not_cached(monkeypatch, tmp_path, failure):
    t = audio.AudioTranscriber()
    t._transcribe_local = lambda *a: pytest.fail('must not recognize rejected media')
    response = Mock()
    response.headers = {'Content-Type': 'text/html' if failure == 'html' else 'audio/mpeg',
                        'Content-Length': str(audio.MAX_MEDIA_BYTES + 1) if failure == 'size' else '1'}
    def chunks(*args):
        t.stop_requested.set()
        yield b'x'
    response.iter_content.side_effect = chunks
    response.__enter__ = Mock(return_value=response)
    response.__exit__ = Mock(return_value=False)
    session = Mock(); session.headers = {}
    session.__enter__ = Mock(return_value=session); session.__exit__ = Mock(return_value=False)
    session.get.return_value = response
    monkeypatch.setattr('api._net.new_session', lambda: session)
    assert t.transcribe('https://course.example.test/media.mp4') == ''
    assert not t._transcript_cache


def test_short_transcript_becomes_context_and_readds_after_reset():
    client = vendor.OpenAICompatibleClient.__new__(vendor.OpenAICompatibleClient)
    client.accumulated_passages = set(); client.conversation_history = []
    assert client.add_audio_transcript_if_new('The answer is nine.')
    assert not client.add_audio_transcript_if_new('The answer is nine.')
    client.force_reset('next')
    assert client.add_audio_transcript_if_new('The answer is nine.')


def test_all_audio_sources_success_only_and_retry_failures():
    solver = vendor.AISolver.__new__(vendor.AISolver)
    solver.stop_requested = threading.Event(); solver._processed_audio_tabs = set()
    solver._extract_audio_urls_from_page = lambda: ['https://example.test/one', 'https://example.test/two']
    transcriber = Mock(); transcriber.transcribe.side_effect = ['one transcript', '', 'one transcript', 'two transcript']
    solver.video_handler = SimpleNamespace(transcriber=transcriber)
    solver.ai_client = Mock()
    solver._preprocess_audio_if_needed('tab', 0, 0)
    assert solver._processed_audio_tabs == {'https://example.test/one'}
    solver._preprocess_audio_if_needed('tab', 0, 0)
    assert len(solver._processed_audio_tabs) == 2
    assert solver.ai_client.add_audio_transcript_if_new.call_count == 3


def test_reference_requires_book_edition_unit_and_question(monkeypatch):
    phrase = 'Why do people go to festivals around the world?'
    monkeypatch.setattr(kb, 'documents', lambda: [
        ('unused', '新视野大学英语（第四版）', '新视野大学英语 视听说教程1.md', '1',
         '## While you view', phrase + '\nReference answers: to escape their normal jobs.')])
    questions = [SimpleNamespace(text=phrase)]
    page = '新视野大学英语（第四版）视听说教程1 Unit 1'
    assert 'Reference answers' in kb.retrieve(page, questions)
    assert not kb.retrieve(page.replace('第四版', '第三版'), questions)
    assert not kb.retrieve(page.replace('Unit 1', 'Unit 2'), questions)
    assert not kb.retrieve('Unit 1', questions)
    assert not kb.retrieve(page, [SimpleNamespace(text='different question with enough length')])


def test_popup_missing_transcript_does_not_guess(monkeypatch):
    handler = vendor.VideoHandler.__new__(vendor.VideoHandler)
    handler.stop_requested = threading.Event(); handler.video_transcript = ''
    handler.current_video_url = 'fixture-video'
    monkeypatch.setattr('api.unipus.answer_policy.cached_answer', lambda key: '')
    monkeypatch.setattr(vendor, 'tracked_completion', lambda *a, **kw: pytest.fail('no evidence for model'))
    assert handler._intelligent_select_answer({'question': 'q', 'options': [{'letter': 'A', 'text': 'x'}]}) == ''
