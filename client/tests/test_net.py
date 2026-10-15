"""直连网络出口不能修改共享环境变量或复用账号 Cookie。"""
import os
from unittest.mock import Mock

from api import _net


def test_send_ignores_proxy_without_mutating_environment(monkeypatch):
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:1")
    session = _net.new_session()
    assert session.trust_env is False
    observed = []

    def request(method, url, **kwargs):
        observed.append(os.environ.get("HTTPS_PROXY"))
        assert kwargs["proxies"] == _net.NO_PROXY
        return "response"

    session.request = request
    session.close = Mock()
    monkeypatch.setattr(_net, "new_session", lambda: session)
    assert _net.send("GET", "https://example.test", proxies={"https": "bad"}) == "response"
    assert observed == ["http://127.0.0.1:1"]
    assert os.environ["HTTPS_PROXY"] == "http://127.0.0.1:1"
    session.close.assert_called_once()
