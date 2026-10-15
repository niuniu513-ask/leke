# -*- coding: utf-8 -*-
"""登录健壮性回归测试。

背景：2026-10-07 23:00 用户登录失败，日志里是
    NameResolutionError: Failed to resolve 'passport2.chaoxing.com' (getaddrinfo failed)
—— 网络/DNS 中断。但当时的表现很糟：界面只给一长串英文 ConnectionError，
而且 cookie 校验把「网络断了」也当成「cookie 失效」，会误导用户去重新抓 cookie。

本测试覆盖：
  1. describe_network_error 的分类翻译
  2. cookie 校验遇网络故障 → 不误报 cookie 失效、不回退账号登录、不进网络请求
  3. cookie 真失效 → 仍然正常回退账号密码登录
  4. 账号登录：网络异常 / 返回 HTML 验证码 / 返回非 JSON / 缺 msg2 / 成功
  5. 登录请求确实带上了 timeout
  6. worker._friendly 的界面文案
  7. 真实网络下用户 cookie 会话仍然有效（只读，不写任何文件）
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # tests/regression -> 项目根
sys.path.insert(0, ROOT)

from unittest import mock

import requests

import api.base as B
from api.base import Account, Chaoxing, describe_network_error, friendly_error
from gui_app.worker import _friendly

PASS = 0
FAIL = 0


def check(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  [OK] {name}")
    else:
        FAIL += 1
        print(f"  [XX] {name}  {extra}")


class FakeResp:
    def __init__(self, payload=None, text="", status=200):
        self._p = payload
        self.text = text
        self.status_code = status

    def json(self):
        if self._p is None:
            raise ValueError("Expecting value: line 1 column 1 (char 0)")
        return self._p


def mk_cx():
    return Chaoxing(account=Account("18000000000", "pw"), tiku=None)


DNS_ERR = (
    "HTTPSConnectionPool(host='passport2.chaoxing.com', port=443): Max retries exceeded "
    "with url: /fanyalogin (Caused by NameResolutionError(\"HTTPSConnection(host="
    "'passport2.chaoxing.com', port=443): Failed to resolve 'passport2.chaoxing.com' "
    "([Errno 11001] getaddrinfo failed)\"))"
)

print("=== 1. describe_network_error 分类 ===")
check("DNS 解析失败", "DNS" in describe_network_error(requests.ConnectionError(DNS_ERR)))
check(
    "连接被拒绝（代理未监听）",
    "代理" in describe_network_error(requests.ConnectionError("Connection refused")),
)
check("超时", "超时" in describe_network_error(requests.ConnectTimeout("connect timed out")))
check(
    "证书错误",
    "证书" in describe_network_error(requests.exceptions.SSLError("certificate verify failed")),
)
check(
    "代理错误",
    "代理" in describe_network_error(requests.exceptions.ProxyError("cannot connect to proxy")),
)
check("兜底不空", describe_network_error(requests.RequestException("something odd")) != "")

print("=== 2. friendly_error ===")
check(
    "网络异常 → 中文（不含类名）",
    "ConnectionError" not in friendly_error(requests.ConnectionError("getaddrinfo failed")),
)
check("普通异常 → 保留类型信息", friendly_error(ValueError("boom")) == "ValueError: boom")

print("=== 3. cookie 校验遇网络故障 ===")
with mock.patch.object(B.SessionManager, "update_cookies", classmethod(lambda cls: None)):
    cx = mk_cx()
    net_exc = requests.ConnectionError(DNS_ERR)

    def fake_validate_net(self):
        self._cookie_net_error = net_exc
        return False

    with mock.patch.object(Chaoxing, "_validate_cookie_session", fake_validate_net):
        with mock.patch.object(
            requests.Session,
            "post",
            side_effect=AssertionError("不该发起账号密码登录请求"),
        ):
            res = cx.login(login_with_cookies=True)
    check("返回 net_error 标记", res.get("net_error") is True, str(res))
    check("给的是网络故障提示", "DNS" in res.get("msg", ""), res.get("msg"))
    check("没有误报 cookie 失效", "cookie" not in res.get("msg", "").lower(), res.get("msg"))

print("=== 4. cookie 真失效 → 正常回退账号登录 ===")
cx = mk_cx()
with mock.patch.object(Chaoxing, "_validate_cookie_session", lambda self: False):
    with mock.patch.object(
        requests.Session,
        "post",
        return_value=FakeResp({"status": False, "msg2": "账号或密码不正确"}),
    ):
        res = cx.login(login_with_cookies=True)
check(
    "回退后原样返回超星提示",
    res.get("msg") == "账号或密码不正确" and not res.get("net_error"),
    str(res),
)

print("=== 5. 账号密码登录分支 ===")
cx = mk_cx()
with mock.patch.object(
    requests.Session, "post", side_effect=requests.ConnectionError("Connection refused")
):
    res = cx.login(login_with_cookies=False)
check("网络异常 → 中文 + net_error", res.get("net_error") is True and "代理" in res["msg"], str(res))

cx = mk_cx()
with mock.patch.object(
    requests.Session,
    "post",
    return_value=FakeResp(None, "<html>请输入验证码 captcha</html>", 200),
):
    res = cx.login(login_with_cookies=False)
check("返回 HTML → 提示验证码", "验证码" in res["msg"], str(res))

cx = mk_cx()
with mock.patch.object(
    requests.Session, "post", return_value=FakeResp(None, "<html>502 Bad Gateway</html>", 502)
):
    res = cx.login(login_with_cookies=False)
check("非 JSON → 提示非预期内容且带状态码", "非预期" in res["msg"] and "502" in res["msg"], str(res))

cx = mk_cx()
with mock.patch.object(requests.Session, "post", return_value=FakeResp({"status": False})):
    res = cx.login(login_with_cookies=False)
check("缺 msg2 字段 → 默认文案", res["msg"] == "账号或密码不正确", str(res))

cx = mk_cx()
with mock.patch.object(requests.Session, "post", return_value=FakeResp({"status": True})), \
        mock.patch.object(B, "save_cookies") as sc, \
        mock.patch.object(B.SessionManager, "update_cookies", classmethod(lambda cls: None)), \
        mock.patch.object(Chaoxing, "get_name", staticmethod(lambda: "牛畅乐")):
    res = cx.login(login_with_cookies=False)
check("成功分支", res["status"] is True and res["msg"] == "登录成功", str(res))
check("成功时保存 cookie", sc.called)

print("=== 6. 登录请求带超时 ===")
captured = {}


def spy_post(self, url, **kw):
    captured.update(kw)
    return FakeResp({"status": False, "msg2": "x"})


cx = mk_cx()
with mock.patch.object(requests.Session, "post", spy_post):
    cx.login(login_with_cookies=False)
check("timeout == gc.HTTP_TIMEOUT", captured.get("timeout") == B.gc.HTTP_TIMEOUT, str(captured))
check("HTTP_TIMEOUT 合理（5~60s）", 5 <= B.gc.HTTP_TIMEOUT <= 60, str(B.gc.HTTP_TIMEOUT))

print("=== 7. worker._friendly ===")
w = _friendly(requests.ConnectionError(DNS_ERR))
check("网络异常 → 中文", "ConnectionError" not in w and "DNS" in w, w)
check("普通异常 → 保留类型", _friendly(ValueError("boom")) == "ValueError: boom")

print("=== 8. 真实网络下用户 cookie 仍然有效（只读）===")
CK = r"D:/学习通/学习通助手/cookies.txt"
if os.path.exists(CK):
    raw = open(CK, encoding="utf-8").read().strip()
    ck = {}
    for part in raw.split(";"):
        if "=" in part:
            k, v = part.split("=", 1)
            ck[k.strip()] = v.strip()
    B.SessionManager.get_session().cookies.update(ck)
    cx = mk_cx()
    try:
        ok = cx._validate_cookie_session()
        check("cookie 会话校验通过", ok is True, f"ok={ok} net_error={cx._cookie_net_error}")
        if ok:
            check("能读到真实姓名", Chaoxing.get_name() == "牛畅乐", repr(Chaoxing.get_name()))
    except requests.RequestException as e:
        print("  [--] 离线或网络异常，跳过真实校验:", type(e).__name__)
else:
    print("  [--] 找不到 cookies.txt，跳过")

print()
print(f"通过 {PASS} 项，失败 {FAIL} 项")
sys.exit(1 if FAIL else 0)
