# -*- coding: utf-8 -*-
"""统一网络出口：全部强制直连，完全无视代理环境。

2026-10-10 牛哥拍板「代理环境全部使用直连」。背景：用户机器上的代理
环境变量常指向临时端口（本机先后踩过 7890 / 63710 / 60152），代理一关
就变死端口；requests / urllib 默认吃这些变量（Windows 上还会读注册表
代理），表现为登录 / 题库 / 通知 / 音频下载全挂，报错完全不指向代理。

所有出网请求一律走这里，别直接用 requests.get/post：
- send()        —— 单次请求（替代 requests.get / requests.post / requests.request）
- new_session() —— 造 Session（替代 requests.Session()）
- urllib_opener() —— urllib 的直连 opener（替代 urllib.request.urlopen）

⚠ Telegram 等必须走代理才能到达的出口，直连后天然不可达 —— 有意为之。
"""
from __future__ import annotations

import os
from contextlib import contextmanager

import requests

NO_PROXY = {"http": None, "https": None}
_PROXY_ENV_KEYS = ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY",
                   "http_proxy", "https_proxy", "all_proxy")


@contextmanager
def no_proxy_env():
    """临时清空代理环境变量，退出时精确还原。"""
    saved: dict = {}
    for k in _PROXY_ENV_KEYS:
        if k in os.environ:
            saved[k] = os.environ.pop(k)
    try:
        yield
    finally:
        os.environ.update(saved)


def send(method: str, url: str, **kwargs):
    """强制直连发一个 HTTP 请求。

    每次请求使用独立的 trust_env=False 会话，屏蔽环境与注册表代理。
    不修改进程环境，避免多账号并发请求互相恢复代理变量。
    """
    kwargs["proxies"] = NO_PROXY
    with new_session() as session:
        return session.request(method, url, **kwargs)


def new_session() -> requests.Session:
    """造一个不吃任何代理的 Session（trust_env=False 同时屏蔽
    环境变量与 Windows 注册表代理）。"""
    s = requests.Session()
    s.trust_env = False
    return s


def urllib_opener():
    """urllib 用：返回强制直连的 opener（不吃环境代理）。"""
    import urllib.request

    return urllib.request.build_opener(urllib.request.ProxyHandler({}))
