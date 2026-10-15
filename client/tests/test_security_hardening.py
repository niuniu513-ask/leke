import json
import os
import sys

import pytest

from api import kc_client
from gui_app import credential_protection as protection, gate, store


@pytest.mark.parametrize('url', ['http://evil.test', 'https://user:pass@example.test',
    'https://example.test/api', 'https://example.test?x=1', 'file:///tmp', 'https://example.test:bad'])
def test_reject_unsafe_account_server(monkeypatch, url):
    monkeypatch.setenv('KC_SERVER', url)
    with pytest.raises(kc_client.KcError):
        kc_client._server()


@pytest.mark.parametrize('url', ['https://example.test/', 'http://127.0.0.1:8100'])
def test_allow_tls_and_loopback_development(monkeypatch, url):
    monkeypatch.setenv('KC_SERVER', url)
    assert kc_client._server() == url.rstrip('/')


def test_credentials_are_not_forwarded_on_redirect(monkeypatch):
    from types import SimpleNamespace
    calls = []
    monkeypatch.setattr(kc_client, '_no_proxy_send', lambda *a, **kw: calls.append(kw) or 'fixture')
    assert kc_client._send('POST', 'https://example.test', allow_redirects=True) == 'fixture'
    assert calls[0]['allow_redirects'] is False
    with pytest.raises(kc_client.KcError):
        kc_client._handle(SimpleNamespace(status_code=307))


def test_frozen_account_server_cannot_be_replaced_by_environment(monkeypatch):
    monkeypatch.setattr(sys, 'frozen', True, raising=False)
    monkeypatch.setenv('KC_SERVER', 'https://fake-service.test')
    assert kc_client._server() == kc_client.DEFAULT_SERVER


def test_frozen_gate_ignores_test_environment(monkeypatch):
    from PySide6.QtWidgets import QDialog
    from gui_app import login_dialog
    monkeypatch.setattr(sys, 'frozen', True, raising=False)
    monkeypatch.setenv('KC_SKIP_GATE', '1')
    monkeypatch.setenv('GUIAUTO_SHOT_DIR', 'unused')
    class Dialog:
        def __init__(self, **kwargs): pass
        def exec(self): return QDialog.DialogCode.Rejected
    monkeypatch.setattr(login_dialog, 'AccessGateDialog', Dialog)
    assert gate.run_access_gate() is None


@pytest.mark.skipif(os.name != 'nt', reason='Windows DPAPI')
def test_dpapi_real_roundtrip_and_settings_migration(monkeypatch, tmp_path):
    monkeypatch.setattr(store, 'app_dir', lambda: str(tmp_path))
    path = tmp_path / store.UI_FILE
    path.write_text(json.dumps({'kc_token': 'fixture-token', 'kc_user': 'fixture'}), encoding='utf-8')
    store.ui_save({'geometry': 'fixture-geometry'})
    raw = json.loads(path.read_text(encoding='utf-8'))
    assert raw['kc_token'].startswith(protection.PREFIX)
    assert 'fixture-token' not in path.read_text(encoding='utf-8')
    assert store.ui_load()['kc_token'] == 'fixture-token'
    store.ui_save({'kc_token': ''})
    assert store.ui_load()['kc_token'] == ''
    assert protection.unprotect(protection.PREFIX + 'broken') == ''


def test_dpapi_failure_does_not_replace_settings_with_cleartext(monkeypatch, tmp_path):
    monkeypatch.setattr(store, 'app_dir', lambda: str(tmp_path))
    path = tmp_path / store.UI_FILE
    path.write_text('{}', encoding='utf-8')
    def fail(value): raise OSError('fixture')
    monkeypatch.setattr(protection, 'protect', fail)
    store.ui_save({'kc_token': 'do-not-write'})
    assert path.read_text(encoding='utf-8') == '{}'


@pytest.mark.parametrize('message,secret', [
    ('Authorization: Bearer fixture-secret', 'fixture-secret'),
    ("headers: {'Cookie': 'fixture-cookie'}", 'fixture-cookie'),
    ('token=fixture-token', 'fixture-token'),
    ('refresh https://user:pass@host.test/private-key/path?q=secret', 'private-key'),
    ('<RequestsCookieJar[<Cookie uid=fixture-id for host.test/>]>', 'fixture-id'),
    ('<form><input name="enc" value="fixture-enc"></form>', 'fixture-enc'),
])
def test_logs_redact_sensitive_context(message, secret):
    from api.log_privacy import redact_message
    assert secret not in redact_message(message)
