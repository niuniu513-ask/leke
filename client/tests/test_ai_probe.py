"""Connection checks use fake clients: no paid API or account traffic."""
from types import SimpleNamespace
import pytest

from gui_app.ai_panel import TestWorker as ProbeWorker, normalize_probe_url


def test_pasted_chat_endpoint():
    assert normalize_probe_url(' https://example.com/v1/chat/completions/ ') == 'https://example.com/v1'
    assert normalize_probe_url('http://localhost:8080/api') == 'http://localhost:8080/api'


def test_saved_address_matches_probe():
    from PySide6.QtWidgets import QApplication
    from gui_app.ai_panel import AiConfigPanel
    app = QApplication.instance() or QApplication([])
    panel = AiConfigPanel()
    panel.ed_ep.setText('https://example.com/v1/chat/completions/')
    panel.ed_key.setText('test-key')
    saved = []
    panel.request_save.connect(lambda: saved.append(panel.values()))
    panel.save_config()
    assert saved[0][('tiku', 'endpoint')] == 'https://example.com/v1'
    panel.ed_ep.setText('https://example.com?key=secret')
    panel.save_config()
    assert len(saved) == 1
    panel.deleteLater()
    app.processEvents()


@pytest.mark.parametrize('url', ['file:///tmp/key', 'https://user:secret@example.com/v1',
                               'https://example.com/v1?key=secret', 'https://example.com/#secret'])
def test_invalid_probe_address(url):
    with pytest.raises(ValueError):
        normalize_probe_url(url)


@pytest.mark.parametrize('mode,model,ok', [('list_fail', '', False), ('success', '', True),
                                         ('success', 'model', True), ('init_fail', 'model', False),
                                         ('reply_fail', 'model', False)])
def test_probe_result_and_resource_cleanup(monkeypatch, mode, model, ok):
    import openai
    import httpx
    import gui_app.ai_panel as panel
    closed = []
    def list_models():
        if mode == 'list_fail':
            raise RuntimeError('unknown secret-key https://private.example')
        return SimpleNamespace(data=[SimpleNamespace(id='model')])
    class HTTP:
        def __init__(self, **kwargs):
            assert kwargs['trust_env'] is False
        def close(self):
            closed.append('http')
    class Client:
        def __init__(self, **kwargs):
            assert kwargs['max_retries'] == 0
            assert kwargs['base_url'] == 'https://example.com/v1'
            if mode == 'init_fail':
                raise RuntimeError('secret-key')
            self.models = SimpleNamespace(list=list_models)
        def close(self):
            closed.append('client')
    def completion(*args, **kwargs):
        if mode == 'reply_fail':
            raise RuntimeError('secret-key')
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content='2'))])
    monkeypatch.setattr(httpx, 'Client', HTTP)
    monkeypatch.setattr(openai, 'OpenAI', Client)
    monkeypatch.setattr(panel, 'tracked_completion', completion)
    worker = ProbeWorker('https://example.com/v1/chat/completions', 'secret-key', model)
    result = []
    worker.done.connect(lambda *args: result.append(args))
    worker.run()
    assert len(result) == 1 and result[0][0] is ok
    assert 'secret-key' not in result[0][1] and 'private.example' not in result[0][1]
    assert 'http' in closed
    assert ('client' in closed) == (mode != 'init_fail')
