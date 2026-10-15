# -*- coding: utf-8 -*-
"""cookies.txt 的读写。

多账号扩展：两个函数都接受可选的 path 参数。
不传时仍然落到 GlobalConst.COOKIES_PATH（"cookies.txt"），
命令行版本与单账号用法完全不受影响。
"""
import os.path
import threading

import requests

from api.config import GlobalConst as gc

# 定义全局 Cookie 文件锁，保证读写绝对安全
cookie_lock = threading.RLock()


def save_cookies(session: requests.Session, path: str | None = None):
    """把 session 里的 cookie 写成一行 "k=v;k=v"。

    path 为空时写默认的 cookies.txt（原行为）；
    多账号场景由调用方传入各账号自己的 cookie 文件路径。
    """
    path = path or gc.COOKIES_PATH
    with cookie_lock:
        buffer = ""
        for k, v in session.cookies.items():
            buffer += f"{k}={v};"
        buffer = buffer.removesuffix(";")
        with open(path, "w") as f:
            f.write(buffer)


def use_cookies(path: str | None = None) -> dict:
    """读取 cookie 文件并解析成 dict；文件不存在或为空时返回 {}。"""
    path = path or gc.COOKIES_PATH
    with cookie_lock:
        if not os.path.exists(path):
            return {}

        cookies = {}
        try:
            with open(path, "r") as f:
                buffer = f.read().strip()
                if not buffer:
                    return {}
                for item in buffer.split(";"):
                    item = item.strip()
                    if not item:
                        continue
                    parts = item.split("=", 1)
                    if len(parts) == 2:
                        cookies[parts[0]] = parts[1]
        except Exception:
            return {}

        return cookies
