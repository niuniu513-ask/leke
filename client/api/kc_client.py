# -*- coding: utf-8 -*-
"""kc_server 客户端（纯网络层，不依赖 Qt）。

账号体系：登录kc_server 拿 token → 心跳维持在线 → 事件批量上报。
状态持久化在 gui_settings.json（键前缀 kc_），离线宽限 72 小时。

红线：这里只管「乐课账号」本身，绝不读/传任何课程平台（超星等）的凭据。
"""
from __future__ import annotations

import datetime as _dt
import os
import socket
import sys
import threading
import uuid
from collections import deque

import requests

DEFAULT_SERVER = "https://kc-api.lifedays.site"
GRACE_HOURS = 72          # 心跳失败后的离线宽限
TIMEOUT = 8               # 单请求超时（秒）
API = "/api/v1"

_ISODT = "%Y-%m-%dT%H:%M:%SZ"
_buf: deque = deque()
_buf_lock = threading.Lock()


class KcError(Exception):
    """网络/服务端不可达。"""

    code = "NETWORK"


class KcAuthError(KcError):
    """token 无效或过期（需要重新登录）。"""

    code = "AUTH"


class KcBlockedError(KcError):
    """账号被封禁/过期/超设备数（服务端明确拒绝）。"""

    code = "BLOCKED"


# ---------- 机器标识 ----------

def machine_id() -> str:
    """稳定机器标识：Windows 读注册表 MachineGuid，其他平台退回 MAC+主机名。"""
    raw = ""
    if os.name == "nt":
        try:
            import winreg

            with winreg.OpenKey(
                winreg.HKEY_LOCAL_MACHINE,
                r"SOFTWARE\Microsoft\Cryptography",
            ) as k:
                raw = winreg.QueryValueEx(k, "MachineGuid")[0]
        except OSError:
            raw = ""
    if not raw:
        raw = f"{socket.gethostname()}-{uuid.getnode():012x}"
    from hashlib import sha256

    return sha256(raw.encode()).hexdigest()[:32]


def machine_name() -> str:
    return socket.gethostname()[:64]


def _server() -> str:
    from urllib.parse import urlsplit
    value = (DEFAULT_SERVER if getattr(sys, 'frozen', False)
             else os.environ.get("KC_SERVER", DEFAULT_SERVER)).strip().rstrip("/")
    try:
        url = urlsplit(value)
        local = url.hostname in {'localhost', '127.0.0.1', '::1'}
        if (url.scheme not in {'https', 'http'} or not url.hostname or url.username or url.password
                or url.path or url.query or url.fragment or (url.scheme == 'http' and not local)):
            raise ValueError('unsafe server URL')
        _ = url.port
    except ValueError as exc:
        raise KcError('乐课服务器地址须使用 HTTPS，且不能包含账号、路径或查询参数') from exc
    return value


# ---------- 状态持久化（gui_settings.json） ----------

def load_state() -> dict:
    from gui_app.store import ui_load

    d = ui_load()
    return {k: d[k] for k in d if k.startswith("kc_")}


def save_state(patch: dict) -> None:
    from gui_app.store import ui_save

    ui_save(patch)


def _now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime(_ISODT)


def _parse_iso(s: str) -> _dt.datetime | None:
    try:
        return _dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
    except (ValueError, AttributeError, TypeError):
        return None


# ---------- HTTP ----------

# 2026-10-10 牛哥拍板「代理环境全部使用直连」：不再搞「先尊重后绕过」，
# 登录 / 心跳 / 头像 / 设备管理一律直连。统一实现见 api/_net.py。
from api._net import send as _no_proxy_send


def _send(method: str, url: str, **kwargs):
    """发一个 HTTP 请求 —— 一律强制直连，无视一切代理来源（api/_net.py）。"""
    kwargs['allow_redirects'] = False  # Never resend passwords or tokens to a redirect target.
    return _no_proxy_send(method, url, **kwargs)


def _post(path: str, payload: dict, token: str = "") -> dict:
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        r = _send(
            "POST", _server() + API + path,
            json=payload, headers=headers, timeout=TIMEOUT,
        )
    except requests.RequestException as e:
        raise KcError(f"无法连接服务器：{e.__class__.__name__}") from e
    return _handle(r)


def _get(path: str, token: str) -> dict:
    try:
        r = _send(
            "GET", _server() + API + path,
            headers={"Authorization": f"Bearer {token}"}, timeout=TIMEOUT,
        )
    except requests.RequestException as e:
        raise KcError(f"无法连接服务器：{e.__class__.__name__}") from e
    return _handle(r)


def _handle(r: requests.Response) -> dict:
    if 300 <= r.status_code < 400:
        raise KcError('乐课服务器意外重定向，已停止发送凭据')
    if r.status_code == 401:
        raise KcAuthError("登录状态已失效，请重新登录")
    if r.status_code == 403:
        detail = ""
        try:
            detail = r.json().get("detail", "")
        except ValueError:
            pass
        _msg = {"ACCOUNT_BANNED": "账号已被封禁", "ACCOUNT_EXPIRED": "账号已过期"}.get(detail)
        e = KcBlockedError(_msg or detail or "账号已被限制")
        e.code = detail or "BLOCKED"  # 结构化错误码，供状态机判定（别靠中文串）
        raise e
    if r.status_code == 429:
        raise KcError("请求过于频繁，请稍后再试")
    if r.status_code >= 400:
        detail = ""
        try:
            j = r.json()
            detail = j.get("detail", "") if isinstance(j, dict) else ""
            if isinstance(detail, list):  # pydantic 422
                detail = detail[0].get("msg", "") if detail else ""
        except ValueError:
            pass
        raise KcError(detail or f"服务器错误 ({r.status_code})")
    try:
        return r.json()
    except ValueError as e:
        raise KcError("服务器响应格式异常") from e


# ---------- 业务接口 ----------

def knowledge_query(titles: list[str]) -> list:
    token = load_state().get('kc_token', '')
    if not token:
        raise KcAuthError('请先登录乐课账号后使用云端题库')
    results = []
    for start in range(0, len(titles), 100):
        batch = titles[start:start + 100]
        data = _post('/knowledge/query', {'titles': batch}, token=token)
        answers = data.get('answers') if isinstance(data, dict) else None
        if not isinstance(answers, list) or len(answers) != len(batch):
            raise KcError('云端题库返回的答案数量异常')
        results.extend(answers)
    return results


def knowledge_write(entries: list[dict], *, overwrite: bool = True) -> int:
    token = load_state().get('kc_token', '')
    if not token:
        raise KcAuthError('请先登录乐课账号后使用云端题库')
    accepted = 0
    for start in range(0, len(entries), 100):
        batch = entries[start:start + 100]
        data = _post('/knowledge/entries', {'entries': batch, 'overwrite': overwrite}, token=token)
        if not isinstance(data, dict) or data.get('ok') is not True or data.get('accepted') != len(batch):
            raise KcError('云端题库未确认完整写入，稍后可安全重试')
        accepted += data['accepted']
    return accepted


def knowledge_stats() -> dict:
    token = load_state().get('kc_token', '')
    if not token:
        raise KcAuthError('请先登录乐课账号后使用云端题库')
    data = _get('/knowledge/stats', token)
    if not isinstance(data, dict) or not isinstance(data.get('count'), int):
        raise KcError('云端题库统计响应异常，请确认服务端已升级')
    return data

def register(username: str, password: str) -> dict:
    return _post("/auth/register", {"username": username, "password": password})


def login(username: str, password: str, version: str = "") -> dict:
    data = _post(
        "/auth/login",
        {
            "username": username,
            "password": password,
            "machine_id": machine_id(),
            "machine_name": machine_name(),
            "version": version,
        },
    )
    save_state(
        {
            "kc_token": data.get("token", ""),
            "kc_user": data.get("username", username),
            "kc_nickname": data.get("nickname", "") or data.get("username", username),
            "kc_avatar": data.get("avatar_url", "") or "",
            "kc_expires": data.get("expires_at") or "",
            "kc_status": "active",
            "kc_last_ok": _now(),
        }
    )
    return data


def heartbeat(version: str = "") -> dict:
    """心跳。成功/失败都会更新 kc_last_ok / kc_status。"""
    state = load_state()
    token = state.get("kc_token", "")
    if not token:
        raise KcAuthError("尚未登录")
    try:
        data = _post(
            "/heartbeat",
            {"machine_id": machine_id(), "version": version, "stats": {}},
            token=token,
        )
    except KcBlockedError:
        # 封禁/过期：服务器明确拒绝 ≠ 验证通过，不刷新 kc_last_ok（不续宽限期）
        raise
    # 心跳顺带把用户名/头像带回来：网站那边改了名，客户端下次心跳就同步到
    patch = {"kc_last_ok": _now()}
    if data.get("nickname"):
        patch["kc_nickname"] = data["nickname"]
    if "avatar_url" in data:
        patch["kc_avatar"] = data.get("avatar_url") or ""
    save_state(patch)
    from api.token_usage import flush_usage
    flush_usage()
    return data


def send_events(events: list[dict]) -> None:
    state = load_state()
    token = state.get("kc_token", "")
    if not token or not events:
        return
    _post("/events", {"events": events}, token=token)


def me() -> dict:
    state = load_state()
    token = state.get("kc_token", "")
    if not token:
        raise KcAuthError("尚未登录")
    data = _get("/me", token)
    save_state({"kc_nickname": data.get("display_name") or state.get("kc_user", ""),
                "kc_avatar": data.get("avatar_url") or ""})
    return data


# ---------- 资料（用户名 / 头像）：网站与客户端共用同一份 ----------

def update_profile(nickname: str | None = None, avatar: str | None = None) -> dict:
    """修改用户名 / 头像。

    nickname：None = 不改，字符串 = 改成它（平台内唯一，重名会报错）。
    avatar  ：None = 不改，data URL = 换头像，空串 = 删除头像。
    """
    token = load_state().get("kc_token", "")
    if not token:
        raise KcAuthError("尚未登录")
    payload: dict = {}
    if nickname is not None:
        payload["nickname"] = nickname
    if avatar is not None:
        payload["avatar"] = avatar
    data = _post("/me/profile", payload, token=token)
    save_state({"kc_nickname": data.get("nickname", "") or "",
                "kc_avatar": data.get("avatar_url", "") or ""})
    return data


def fetch_avatar(path: str) -> bytes:
    """按 /me 返回的相对地址下载头像字节（失败抛 KcError）。"""
    if not path:
        raise KcError("没有设置头像")
    url = path if path.startswith("http") else _server() + path
    try:
        r = _send("GET", url, timeout=TIMEOUT)
    except requests.RequestException as e:
        raise KcError(f"无法下载头像：{e.__class__.__name__}") from e
    if r.status_code != 200:
        raise KcError(f"头像下载失败（{r.status_code}）")
    return r.content


def change_password(old_password: str, new_password: str) -> dict:
    token = load_state().get("kc_token", "")
    if not token:
        raise KcAuthError("尚未登录")
    return _post(
        "/auth/change_password",
        {"old_password": old_password, "new_password": new_password},
        token=token,
    )


def unbind_device(device_id: int) -> dict:
    token = load_state().get("kc_token", "")
    if not token:
        raise KcAuthError("尚未登录")
    try:
        r = _send(
            "DELETE", _server() + API + f"/my/devices/{int(device_id)}",
            headers={"Authorization": f"Bearer {token}"}, timeout=TIMEOUT,
        )
    except requests.RequestException as e:
        raise KcError(f"无法连接服务器：{e.__class__.__name__}") from e
    return _handle(r)


def logout() -> None:
    """本地登出：清掉 token 与资料缓存（下次启动回到登录闸门）。"""
    save_state({
        "kc_token": "", "kc_user": "", "kc_nickname": "", "kc_avatar": "",
        "kc_expires": "", "kc_status": "", "kc_last_ok": "",
    })


# ---------- 事件缓冲 ----------

def track(page: str, action: str, platform: str = "", extra: dict | None = None) -> None:
    """缓冲一条使用事件（失败静默，绝不影响主流程）。"""
    with _buf_lock:
        _buf.append(
            {
                "ts": _now(),
                "page": page[:64],
                "action": action[:64],
                "platform": platform[:32],
                "extra": extra or {},
            }
        )
        if len(_buf) > 200:
            _buf.popleft()


def flush_events() -> int:
    """把缓冲的事件发出去，返回发送条数（失败静默清空）。"""
    with _buf_lock:
        if not _buf:
            return 0
        if not load_state().get("kc_token", ""):
            return 0  # 未登录：留在缓冲，登录后再发（不能清空丢事件）
        batch = list(_buf)
        _buf.clear()
    try:
        send_events(batch)
        return len(batch)
    except KcError:
        return 0


# ---------- 闸门判定 ----------

def check_access(version: str = "") -> dict:
    """判定当前能否进入主界面。

    返回 {"mode": "online"|"grace"|"login", "reason": str, "hb": dict}
    - online：心跳成功
    - grace ：服务器不可达，但在 72h 离线宽限内且账号状态正常
    - login ：需要登录/重新登录
    封禁/过期一律 mode=login 且 reason 带明文（宽限期内也不放行）。
    """
    state = load_state()
    if not state.get("kc_token"):
        return {"mode": "login", "reason": "", "hb": None}
    try:
        hb = heartbeat(version=version)
        # 版本要求提示（不阻断，只留日志给主流程弹）
        min_v = (hb.get("config") or {}).get("min_version")
        if min_v and _lt_version(version or "", str(min_v)):
            hb["_version_warning"] = f"当前版本 {version} 低于要求的 {min_v}，请尽快更新"
        return {"mode": "online", "reason": "", "hb": hb}
    except KcBlockedError as e:
        code = getattr(e, "code", "")
        status = "banned" if "BANNED" in code else "expired"
        save_state({"kc_status": status})
        return {"mode": "login", "reason": str(e), "hb": None}
    except KcAuthError:
        return {"mode": "login", "reason": "登录状态已失效，请重新登录", "hb": None}
    except KcError:
        # 网络不通：宽限判定
        if state.get("kc_status") in ("banned", "expired"):
            reason = "账号已被封禁" if state["kc_status"] == "banned" else "账号已过期"
            return {"mode": "login", "reason": reason, "hb": None}
        last = _parse_iso(state.get("kc_last_ok", ""))
        if last is not None:
            age = _dt.datetime.now(_dt.timezone.utc) - last
            if age <= _dt.timedelta(hours=GRACE_HOURS):
                return {"mode": "grace", "reason": "", "hb": None}
        return {
            "mode": "login",
            "reason": f"无法连接服务器，且已超出 {GRACE_HOURS} 小时离线宽限期",
            "hb": None,
        }


def _lt_version(a: str, b: str) -> bool:
    """粗略 semver 比较：a < b 返回 True。"""

    def key(v: str) -> list[int]:
        parts = []
        for p in "".join(c if c.isdigit() else "." for c in str(v)).split("."):
            if p:
                parts.append(int(p))
        return parts

    try:
        return key(a) < key(b)
    except ValueError:
        return False
