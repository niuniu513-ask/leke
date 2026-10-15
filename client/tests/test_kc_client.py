# -*- coding: utf-8 -*-
"""kc_client 单测：mock kc_server + 闸门状态机（在线/宽限/锁死/封禁/过期）。

运行：python -m pytest tests/test_kc_client.py -v
"""
import datetime as _dt
import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from api import kc_client


# ---------- mock kc_server ----------

class _Handler(BaseHTTPRequestHandler):
    users = {"real01": "secret66"}
    banned = False
    expired = False
    received_events = []
    profile_calls = []

    def log_message(self, *a):
        pass

    def _json(self, code: int, body: dict):
        raw = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(n) or b"{}")
        if self.path == "/api/v1/auth/register":
            u = body.get("username", "")
            if u in self.users:
                return self._json(409, {"detail": "用户名已存在"})
            self.users[u] = body.get("password", "")
            return self._json(200, {"ok": True, "expires_at": "2026-11-08T00:00:00Z"})
        if self.path == "/api/v1/auth/login":
            u, p = body.get("username", ""), body.get("password", "")
            if self.users.get(u) != p:
                return self._json(400, {"detail": "用户名或密码错误"})
            return self._json(200, {"token": "tok-" + u, "username": u,
                                    "expires_at": "2026-11-08T00:00:00Z", "token_hours": 168})
        if self.path == "/api/v1/heartbeat":
            if self.headers.get("Authorization") != "Bearer tok-real01":
                return self._json(401, {"detail": "invalid token"})
            if self.banned:
                return self._json(403, {"detail": "ACCOUNT_BANNED"})
            if self.expired:
                return self._json(403, {"detail": "ACCOUNT_EXPIRED"})
            return self._json(200, {"server_time": "2026-10-09T00:00:00Z",
                                    "announcements": [], "config": {},
                                    "heartbeat_interval_min": 30})
        if self.path == "/api/v1/events":
            self.received_events.extend(body.get("events", []))
            return self._json(200, {"ok": True})
        if self.path == "/api/v1/me/profile":
            if self.headers.get("Authorization") != "Bearer tok-real01":
                return self._json(401, {"detail": "invalid token"})
            self.profile_calls.append(body)
            if body.get("nickname") == "占用名":
                return self._json(409, {"detail": "该用户名已被使用，换一个试试"})
            nick = body.get("nickname") or "牛哥"
            has_av = bool(body.get("avatar"))
            return self._json(200, {"ok": True, "nickname": nick, "display_name": nick,
                                    "avatar_url": "/api/v1/avatars/1?v=abc" if has_av else "",
                                    "has_avatar": has_av})
        if self.path == "/api/v1/auth/change_password":
            if body.get("old_password") != "secret66":
                # 真实服务端用 400（401 会被客户端当成"登录态失效"）
                return self._json(400, {"detail": "原密码错误"})
            return self._json(200, {"ok": True})
        return self._json(404, {"detail": "Not Found"})

    def do_DELETE(self):
        if self.path.startswith("/api/v1/my/devices/"):
            if self.headers.get("Authorization") != "Bearer tok-real01":
                return self._json(401, {"detail": "invalid token"})
            return self._json(200, {"ok": True})
        return self._json(404, {"detail": "Not Found"})

    def do_GET(self):
        if self.path == "/api/v1/me":
            if self.headers.get("Authorization") != "Bearer tok-real01":
                return self._json(401, {"detail": "invalid token"})
            return self._json(200, {"username": "real01", "nickname": "牛哥",
                                    "display_name": "牛哥", "devices": [],
                                    "avatar_url": "/api/v1/avatars/1?v=abc"})
        if self.path.startswith("/api/v1/avatars/"):
            raw = b"\x89PNG\r\n\x1a\n" + b"0" * 32
            self.send_response(200)
            self.send_header("Content-Type", "image/png")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)
            return
        return self._json(404, {"detail": "Not Found"})


@pytest.fixture()
def server(monkeypatch, tmp_path):
    _Handler.banned = False
    _Handler.expired = False
    _Handler.received_events = []
    _Handler.profile_calls = []
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    monkeypatch.setenv("KC_SERVER", f"http://127.0.0.1:{httpd.server_port}")
    # 状态文件隔离到临时目录
    monkeypatch.setattr(kc_client, "_state_path", None, raising=False)
    import gui_app.store as store

    monkeypatch.setattr(store, "ui_path", lambda: str(tmp_path / "gui_settings.json"))
    yield httpd
    httpd.shutdown()


def _set_state(**kw):
    kc_client.save_state(kw)


# ---------- 登录 / 注册 ----------

def test_login_saves_state(server):
    data = kc_client.login("real01", "secret66", version="1.5.8")
    assert data["token"] == "tok-real01"
    st = kc_client.load_state()
    assert st["kc_token"] == "tok-real01" and st["kc_user"] == "real01"
    assert st["kc_status"] == "active" and st["kc_last_ok"]


def test_login_wrong_password(server):
    with pytest.raises(kc_client.KcError, match="用户名或密码错误"):
        kc_client.login("real01", "wrong!")


# ---------- 环境代理化石防护 ----------

def test_dead_proxy_env_still_logs_in(server, monkeypatch):
    """环境变量里挂着死代理时登录/心跳仍要成功（自动绕过直连重试）。

    本机实证：用户级 HTTP_PROXY/HTTPS_PROXY 残留 127.0.0.1:7890，FlClash 一关
    该端口就拒绝连接，requests 默认吃这个变量 ⇒ 登录全挂。修复后首次尊重环境
    代理、网络类失败自动绕过直连重试一次 —— 这里用必拒绝的 127.0.0.1:9 当死代理。
    """
    dead = "http://127.0.0.1:9"
    monkeypatch.setenv("HTTP_PROXY", dead)
    monkeypatch.setenv("HTTPS_PROXY", dead)

    data = kc_client.login("real01", "secret66", version="1.6.6")
    assert data["token"] == "tok-real01"
    # GET 路径（心跳/me）同样要走通
    me = kc_client.me()
    assert me.get("username") == "real01" or me.get("username") is None  # 服务端返回原样


def test_no_proxy_env_restores_vars(monkeypatch):
    """no_proxy_env 退出时必须把代理环境变量原样还原，不能污染进程环境。"""
    from api._net import no_proxy_env

    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:7890")
    monkeypatch.delenv("HTTP_PROXY", raising=False)
    with no_proxy_env():
        assert "HTTPS_PROXY" not in os.environ
        assert "HTTP_PROXY" not in os.environ
    assert os.environ.get("HTTPS_PROXY") == "http://127.0.0.1:7890"
    assert "HTTP_PROXY" not in os.environ


def test_register_then_login(server):
    kc_client.register("new01", "secret66")
    kc_client.login("new01", "secret66")
    assert kc_client.load_state()["kc_user"] == "new01"


def test_register_duplicate(server):
    with pytest.raises(kc_client.KcError, match="用户名已存在"):
        kc_client.register("real01", "secret66")


# ---------- 闸门判定 ----------

def test_access_requires_login_when_no_token(server):
    r = kc_client.check_access(version="1.5.8")
    assert r["mode"] == "login"


def test_access_online(server):
    kc_client.login("real01", "secret66")
    r = kc_client.check_access(version="1.5.8")
    assert r["mode"] == "online" and r["hb"]["heartbeat_interval_min"] == 30


def test_access_banned_blocks_even_in_grace(server):
    kc_client.login("real01", "secret66")
    _Handler.banned = True
    r = kc_client.check_access(version="1.5.8")
    assert r["mode"] == "login" and "封禁" in r["reason"]
    assert kc_client.load_state()["kc_status"] == "banned"
    # 即使断网（宽限内），封禁状态也不放行
    monkey_offline(server)
    r2 = kc_client.check_access(version="1.5.8")
    assert r2["mode"] == "login"


def monkey_offline(server):
    server.shutdown()  # 关掉 mock 服务器 → 连接拒绝


def test_access_expired(server):
    kc_client.login("real01", "secret66")
    _Handler.expired = True
    r = kc_client.check_access(version="1.5.8")
    assert r["mode"] == "login" and "过期" in r["reason"]
    assert kc_client.load_state()["kc_status"] == "expired"


def test_access_grace_within_72h(server):
    kc_client.login("real01", "secret66")
    monkey_offline(server)  # 断网
    r = kc_client.check_access(version="1.5.8")
    assert r["mode"] == "grace"


def test_access_blocked_after_grace(server):
    kc_client.login("real01", "secret66")
    old = (_dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(hours=73)).strftime("%Y-%m-%dT%H:%M:%SZ")
    _set_state(kc_last_ok=old)
    monkey_offline(server)
    r = kc_client.check_access(version="1.5.8")
    assert r["mode"] == "login" and "宽限期" in r["reason"]


# ---------- 事件 ----------

def test_track_and_flush(server):
    kc_client.login("real01", "secret66")
    kc_client.track("nav", "goto_page", extra={"page": "run"})
    kc_client.track("app", "launch")
    n = kc_client.flush_events()
    assert n == 2
    assert any(e["action"] == "goto_page" for e in _Handler.received_events)
    # 缓冲已清空
    assert kc_client.flush_events() == 0


def test_flush_silent_on_network_error(server):
    kc_client.track("app", "launch")
    monkey_offline(server)
    assert kc_client.flush_events() == 0  # 失败静默，不抛


# ---------- 机器标识 ----------

def test_machine_id_stable():
    a, b = kc_client.machine_id(), kc_client.machine_id()
    assert a == b and len(a) == 32


# ---------- 用户资料：用户名 / 头像（v1.6.3） ----------

def test_me_caches_nickname_and_avatar(server):
    kc_client.login("real01", "secret66")
    data = kc_client.me()
    assert data["display_name"] == "牛哥"
    st = kc_client.load_state()
    assert st["kc_nickname"] == "牛哥"          # 离线也能显示用户名
    assert st["kc_avatar"] == "/api/v1/avatars/1?v=abc"


def test_update_profile_nickname(server):
    kc_client.login("real01", "secret66")
    r = kc_client.update_profile(nickname="新名字")
    assert r["nickname"] == "新名字"
    assert kc_client.load_state()["kc_nickname"] == "新名字"
    assert _Handler.profile_calls[-1] == {"nickname": "新名字"}


def test_update_profile_nickname_conflict(server):
    kc_client.login("real01", "secret66")
    with pytest.raises(kc_client.KcError, match="已被使用"):
        kc_client.update_profile(nickname="占用名")


def test_update_profile_avatar_and_clear(server):
    kc_client.login("real01", "secret66")
    data_url = "data:image/png;base64," + "A" * 40
    r = kc_client.update_profile(avatar=data_url)
    assert r["has_avatar"] is True and r["avatar_url"].startswith("/api/v1/avatars/")
    assert _Handler.profile_calls[-1] == {"avatar": data_url}
    assert kc_client.load_state()["kc_avatar"] == r["avatar_url"]

    # 传空串 = 删头像（键要真的发出去，不能因为 falsy 被丢掉）
    r = kc_client.update_profile(avatar="")
    assert r["has_avatar"] is False
    assert _Handler.profile_calls[-1] == {"avatar": ""}
    assert kc_client.load_state()["kc_avatar"] == ""


def test_fetch_avatar_bytes(server):
    kc_client.login("real01", "secret66")
    raw = kc_client.fetch_avatar("/api/v1/avatars/1?v=abc")
    assert raw.startswith(b"\x89PNG")
    with pytest.raises(kc_client.KcError):
        kc_client.fetch_avatar("")


def test_change_password_and_unbind(server):
    kc_client.login("real01", "secret66")
    assert kc_client.change_password("secret66", "newpass66")["ok"] is True
    with pytest.raises(kc_client.KcError, match="原密码错误"):
        kc_client.change_password("wrong", "newpass66")
    assert kc_client.unbind_device(1)["ok"] is True


def test_logout_clears_local_state(server):
    kc_client.login("real01", "secret66")
    assert kc_client.load_state()["kc_token"]
    kc_client.logout()
    st = kc_client.load_state()
    assert not st["kc_token"] and not st["kc_nickname"] and not st["kc_avatar"]
    # 登出后闸门要求重新登录
    assert kc_client.check_access(version="1.6.3")["mode"] == "login"
